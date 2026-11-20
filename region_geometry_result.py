"""Native multi-region preview; all traces, sampled fields, no interface interpolation."""
import json
import re
from pathlib import Path
import numpy as np
from native_mesh import ElementMesh,read_material_vtk
from region_result import case_hash,digest

SCHEMA='region_geometry_result_v1'


def signed_distance(points,region,h):
    """Signed half-space distance to the actual rectangle/regular polygon."""
    s=region['shape'];p=np.asarray(points)
    if s['kind']=='rectangle':
        x0,x1,z0,z1=s['bounds_m'];return np.maximum.reduce((x0-p[:,0],p[:,0]-x1,z0-p[:,1],p[:,1]-z1))
    v=p-np.asarray(s['center_m']);n=round(8*s['patch_halfwidth_m']/h);delta=2*np.pi/n
    angle=np.arctan2(v[:,1],v[:,0]);offset=np.remainder(angle+3*np.pi/4,delta)-delta/2
    return np.linalg.norm(v,axis=1)*np.cos(offset)-s['radius_m']*np.cos(np.pi/n)


def masks(points,case):
    # Native coordinates are float32; exclude the uncertain interface strip.
    tol=max(1.,float(abs(points).max(initial=0)))*np.finfo(np.float32).eps*4
    solid=np.ones(len(points),bool);fluid=np.zeros(len(points),np.int32);material=len(case['model']['layers'])+1
    for region in case['regions']:
        distance=signed_distance(points,region,case['model']['element_m']);solid&=distance>tol
        if region['interior']['kind']=='fluid':fluid[distance < -tol]=material;material+=1
    return solid,fluid


def validate_bundle(a):
    required={'metadata','t','x','z','vx','vz','pressure','pressure_xy','fluid_points','fluid_material','fluid_pressure','solid_points','solid_velocity','field_step','field_t','mesh_points','mesh_cells','mesh_materials','mesh_vtk_sha256'}
    if set(a)!=required:raise ValueError('多区域结果字段不完整。')
    meta=json.loads(str(a['metadata']));case=meta['case'];c=case['model']
    if meta.get('schema')!=SCHEMA or case.get('schema')!='region_geometry_v1' or meta.get('case_sha256')!=case_hash(case):raise ValueError('多区域输入/哈希不匹配。')
    from materials import kind,attenuating
    if kind(c)!='isotropic' or attenuating(c):raise ValueError('多区域结果当前限无损ISO。')
    if meta.get('units')!=dict(velocity='m/s',pressure='Pa',coordinates='m',time='s'):raise ValueError('多区域结果单位不明。')
    for k in required-{'metadata','mesh_vtk_sha256'}:
        if not np.issubdtype(a[k].dtype,np.number) or not np.isfinite(a[k]).all():raise ValueError('多区域数组非法。')
    nt=(c['nstep']-1)//c['sample_stride']+1;n=c['receivers'];nf=len(a['field_step'])
    fluids=[r for r in case['regions'] if r['interior']['kind']=='fluid'];npres=len(fluids)
    if a['t'].shape!=(nt,) or a['x'].shape!=(n,) or a['z'].shape!=(n,) or any(a[k].shape!=(nt,n) for k in ('vx','vz')):raise ValueError('完整道集形状错误。')
    if not np.allclose(a['t'],-c['source_t0_s']+np.arange(nt)*c['dt_s']*c['sample_stride'],atol=1e-10,rtol=0):raise ValueError('记录时轴不符。')
    xy=np.array(meta['mesh_acquisition_xz_m'])
    if xy.shape!=(n+1,2) or not np.allclose(np.c_[a['x'],a['z']],xy[1:],rtol=0,atol=1e-9):raise ValueError('实际炮检坐标不符。')
    if not np.isfinite(xy).all() or xy[0,0]!=c['shot_x_m']:raise ValueError('实际震源坐标不符。')
    if not np.allclose(a['x'],c['first_x_m']+np.arange(n)*c['spacing_m'],rtol=0,atol=1e-9):raise ValueError('接收排列不符。')
    expected=sorted(set([5,c['nstep']]+list(range(c['snapshot_stride'],c['nstep']+1,c['snapshot_stride']))))
    if not np.array_equal(a['field_step'],expected) or not np.allclose(a['field_t'],-c['source_t0_s']+(a['field_step']-1)*c['dt_s'],atol=1e-10,rtol=0):raise ValueError('完整帧时轴不符。')
    if a['pressure'].shape!=(nt,npres) or a['pressure_xy'].shape!=(npres,2):raise ValueError('各流体中心记录缺失。')
    centers=[]
    for r in fluids:
        s=r['shape'];centers.append(s['center_m'] if s['kind']=='circle' else [(s['bounds_m'][0]+s['bounds_m'][1])/2,(s['bounds_m'][2]+s['bounds_m'][3])/2])
    if npres and not np.allclose(a['pressure_xy'],centers,rtol=0,atol=1e-9):raise ValueError('流体压力位置错误。')
    nsolid=len(c['layers'])
    for kind in ('solid','fluid'):
        p=a[kind+'_points']
        if p.ndim!=2 or p.shape[1]!=2:raise ValueError('原生节点坐标错误。')
        solid,fluid=masks(p,case)
        if kind=='solid':
            if not len(p) or not solid.all() or a['solid_velocity'].shape!=(nf,len(p),2):raise ValueError('固体速度混入区域/界面或帧数错误。')
        elif a['fluid_pressure'].shape!=(nf,len(p)) or not np.array_equal(a['fluid_material'],fluid) or (len(p) and (fluid==0).any()) or set(np.unique(fluid))!=set(range(nsolid+1,nsolid+1+npres)):raise ValueError('流体压力材料/帧不符。')
    mesh=ElementMesh.read(a)
    if mesh is None or set(np.unique(mesh.materials))!=set(range(1,nsolid+1+npres)):raise ValueError('实际多材料网格缺失。')
    center=mesh.points[mesh.cells[:,:4]].mean(axis=1);expected_material=1+np.searchsorted(np.cumsum([row[0] for row in c['layers']])[:-1],-center[:,1]);material=len(case['model']['layers'])+1
    for r in case['regions']:
        inside=signed_distance(center,r,c['element_m'])<0
        if r['interior']['kind']=='traction_free':
            if inside.any():raise ValueError('无牵引洞内出现单元。')
        else:expected_material[inside]=material;material+=1
    if not np.array_equal(mesh.materials,expected_material):raise ValueError('实际网格材料与几何不一致。')
    return meta,mesh


def pack(root,destination):
    root=Path(root);destination=Path(destination)
    if destination.exists():raise ValueError('结果包已存在。')
    case=json.loads((root/'case.json').read_text());c=case['model'];topo=json.loads((root/'topology_qc.json').read_text());o=root/'OUTPUT_FILES'
    if case.get('schema')!='region_geometry_v1':raise ValueError('需要多区域输入。')
    if any(digest(root/'DATA'/k)!=h for k,h in topo['input_sha256'].items()):raise ValueError('输入文件已改变。')
    log=(root/'solver.log').read_text()
    if not re.search(r'Total number of time steps done:\s*'+str(c['nstep'])+r'\b',log) or 'using double precision' not in log:raise ValueError('正演未完整结束。')
    t=None;v=[];xy=[];p=[];pxy=[];sources=[]
    def trace(path):
        nonlocal t
        a=np.loadtxt(path);sources.append(path)
        if a.ndim!=2 or a.shape[1]!=2 or not np.isfinite(a).all() or (t is not None and not np.array_equal(t,a[:,0])):raise ValueError('接收记录损坏。')
        t=a[:,0];return a[:,1]
    for row in (root/'DATA/STATIONS').read_text().splitlines():
        station,net,x,z,*_=row.split()
        if net=='AA':v.append([trace(o/f'{net}.{station}.BXX.semv'),trace(o/f'{net}.{station}.BXZ.semv')]);xy.append([float(x),float(z)])
        elif net=='FL':p.append(trace(o/f'{net}.{station}.PRE.semp'));pxy.append([float(x),float(z)])
        else:raise ValueError('未知台站网络。')
    counts=re.findall(r'number of global nodes in (?:outer|inner) elements\s*=\s*(\d+)',log)
    if len(counts)!=2:raise ValueError('缺少单进程节点计数。')
    n=sum(map(int,counts));gp=o/'wavefield_grid_for_dumps.bin';grid=np.fromfile(gp,dtype='<f4').reshape(-1,2);nodes=grid[:n]
    if len(nodes)!=n or not np.isfinite(nodes).all():raise ValueError('原生节点损坏。')
    solid,fluid=masks(nodes,case)
    def sample(ids,maximum):return ids if len(ids)<=maximum else ids[np.linspace(0,len(ids)-1,maximum).round().astype(int)]
    si=sample(np.flatnonzero(solid),20000);fi=np.concatenate([sample(np.flatnonzero(fluid==i),max(1,10000//len(p))) for i in range(len(c['layers'])+1,len(c['layers'])+1+len(p))]) if p else np.array([],dtype=int)
    paths=sorted(o.glob('wavefield*_01.bin'));steps=[int(f.name[9:16]) for f in paths];pressure=[];velocity=[]
    for f in paths:
        a=np.fromfile(f,dtype='<f4').reshape(-1,1 if p else 2)
        if len(a)!=len(grid) or not np.isfinite(a[:n]).all():raise ValueError('完整原生帧非法。')
        sources.append(f)
        if p:pressure.append(a[fi,0])
        else:velocity.append(a[si])
    if p:
        paths=sorted(o.glob('solid_velocity*_01.bin'))
        if [int(f.name[14:21]) for f in paths]!=steps:raise ValueError('固体与流体帧不同步。')
        for f in paths:
            a=np.fromfile(f,dtype='<f4').reshape(-1,2)
            if len(a)!=len(grid) or not np.isfinite(a[:n]).all():raise ValueError('完整原生速度帧非法。')
            velocity.append(a[si]);sources.append(f)
    sources+=[gp,o/'mesh_materials.vtk',root/'case.json',root/'topology_qc.json',root/'solver.log']+list((root/'DATA').iterdir())
    meta=dict(schema=SCHEMA,case=case,case_sha256=case_hash(case),remote_run=str(root.resolve()),
        units=dict(velocity='m/s',pressure='Pa',coordinates='m',time='s'),input_sha256=topo['input_sha256'],
        mesh_acquisition_xz_m=topo['mesh_acquisition_xz_m'],source_sha256={str(f.relative_to(root)):digest(f) for f in sources},packer_sha256=digest(__file__),
        scientific_status='experimental_exact_input_evidence_required',preview_note='完整各道记录；原生节点分介质抽样，排除float32坐标下不确定的界面窄带，不跨界面插值。')
    v=np.asarray(v);xy=np.asarray(xy);steps=np.array(steps,dtype=int)
    a=dict(metadata=np.array(json.dumps(meta,ensure_ascii=False)),t=t,x=xy[:,0],z=xy[:,1],vx=v[:,0].T,vz=v[:,1].T,
        pressure=np.array(p).T if p else np.empty((len(t),0)),pressure_xy=np.array(pxy).reshape(-1,2),
        fluid_points=nodes[fi],fluid_material=fluid[fi],fluid_pressure=np.array(pressure) if p else np.empty((len(steps),0)),
        solid_points=nodes[si],solid_velocity=np.array(velocity),field_step=steps,field_t=-c['source_t0_s']+(steps-1)*c['dt_s'],**read_material_vtk(o/'mesh_materials.vtk').arrays())
    validate_bundle(a)
    with destination.open('xb') as f:np.savez_compressed(f,**a)
    with np.load(destination,allow_pickle=False) as restored:
        validate_bundle(dict(restored))
        if any(not np.array_equal(a[k],restored[k]) for k in a):raise ValueError('结果包往返不一致。')
    return meta


if __name__=='__main__':
    import sys
    pack(sys.argv[1],sys.argv[2])
