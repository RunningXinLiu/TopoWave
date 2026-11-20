"""Traction-free cavity results: solid velocity only, no fictitious cavity field."""
import json,re
from pathlib import Path
import numpy as np
from native_mesh import ElementMesh,read_material_vtk

SCHEMA='region_dry_result_v1'
def validate_bundle(a):
    from region_result import case_hash
    keys={'metadata','t','x','z','vx','vz','field_points','field_velocity','field_t','field_step','mesh_points','mesh_cells','mesh_materials','mesh_vtk_sha256'}
    if set(a)!=keys:raise ValueError('无牵引结果字段不完整。')
    m=json.loads(str(a['metadata']));case=m['case'];c=case['model']
    if m.get('schema')!=SCHEMA or m.get('field_quantity')!='solid_velocity_m_s' or case.get('schema')!='rect_region_v1' or case['interior']!={'kind':'traction_free'} or m['case_sha256']!=case_hash(case):raise ValueError('无牵引结果物理类型或哈希不符。')
    from materials import validate_materials,kind
    validate_materials(c)
    if kind(c)!='isotropic':raise ValueError('无牵引结果暂不支持TI材料。')
    for k in keys-{'metadata','mesh_vtk_sha256'}:
        if not np.issubdtype(a[k].dtype,np.number) or not np.isfinite(a[k]).all():raise ValueError('无效无牵引结果数组。')
    nt=(c['nstep']+c['sample_stride']-1)//c['sample_stride'];nr=c['receivers']
    if a['t'].shape!=(nt,) or a['x'].shape!=(nr,) or a['z'].shape!=(nr,) or any(a[k].shape!=(nt,nr) for k in ('vx','vz')):raise ValueError('完整记录形状不符。')
    if not np.allclose(a['x'],c['first_x_m']+np.arange(nr)*c['spacing_m'],rtol=0,atol=1e-9) or not np.all(a['z']==-c['receiver_depth_m']):raise ValueError('接收位置不符。')
    if not np.allclose(np.diff(a['t']),c['dt_s']*c['sample_stride'],rtol=1e-8,atol=1e-10):raise ValueError('记录时轴不符。')
    actual=nt*c['sample_stride'];stride=c.get('snapshot_stride') or int(np.ceil(c['nstep']/60))
    steps=sorted(set(range(stride,actual+1,stride))|{actual}|({5} if actual>=5 else set()))
    if not np.array_equal(a['field_step'],steps) or a['field_t'].shape!=(len(steps),) or not np.allclose(a['field_t'],a['t'][0]+(a['field_step']-1)*c['dt_s'],rtol=0,atol=1e-9):raise ValueError('快照步数或时轴不符。')
    p=a['field_points'];x0,x1,z0,z1=case['rectangle_m']
    if p.ndim!=2 or p.shape[1]!=2 or not len(p) or a['field_velocity'].shape!=(len(steps),len(p),2):raise ValueError('速度快照形状不符。')
    if np.any((p[:,0]>x0)&(p[:,0]<x1)&(p[:,1]>z0)&(p[:,1]<z1)):raise ValueError('无牵引空洞内不应存在波场节点。')
    if not np.all((p[:,0]>=0)&(p[:,0]<=c['width_m'])&(p[:,1]<=0)&(p[:,1]>=-c['layers'][0][0])):raise ValueError('节点超出模型。')
    mesh=ElementMesh.read(a)
    if mesh is None or not np.all(mesh.materials==1):raise ValueError('无牵引实际网格无效。')
    centers=mesh.points[mesh.cells[:,:4]].mean(axis=1)
    if np.any((centers[:,0]>x0)&(centers[:,0]<x1)&(centers[:,1]>z0)&(centers[:,1]<z1)):raise ValueError('洞内仍有实体单元。')
    return m,mesh

def pack(root,destination):
    from region_result import case_hash,digest,load
    root=Path(root).resolve();dest=Path(destination);o=root/'OUTPUT_FILES';case=json.loads((root/'case.json').read_text());c=case['model']
    if dest.exists():raise ValueError('结果包已存在。')
    channels=[];coords=[];files=[];times=None
    for row in (root/'DATA/STATIONS').read_text().splitlines():
        station,net,x,z,*_=row.split()
        if net!='AA':raise ValueError('无牵引案例出现未知接收器网络。')
        values=[]
        for suffix in ('BXX.semv','BXZ.semv'):
            p=o/f'{net}.{station}.{suffix}';t,v=np.loadtxt(p,unpack=True)
            if times is not None and not np.array_equal(t,times):raise ValueError('接收记录时轴不一致。')
            times=t;values.append(v);files.append(p)
        channels.append(values);coords.append([float(x),float(z)])
    counts=re.findall(r'number of global nodes in (?:outer|inner) elements\s*=\s*(\d+)',(root/'solver.log').read_text())
    if len(counts)!=2:raise ValueError('缺少单进程节点计数。')
    n=sum(map(int,counts));gp=o/'wavefield_grid_for_dumps.bin';grid=np.fromfile(gp,dtype='<f4').reshape(-1,2)
    if len(grid)<n or not np.isfinite(grid[:n]).all():raise ValueError('原生网格损坏。')
    ids=np.arange(n) if n<=20000 else np.linspace(0,n-1,20000).round().astype(int)
    frames=[];steps=[]
    for p in sorted(o.glob('wavefield*_01.bin')):
        v=np.fromfile(p,dtype='<f4').reshape(-1,2)
        if len(v)!=len(grid) or not np.isfinite(v[:n]).all():raise ValueError('原生速度帧损坏。')
        frames.append(v[ids]);steps.append(int(p.name[9:16]));files.append(p)
    files += [gp,o/'mesh_materials.vtk',root/'case.json',root/'model.json',root/'solver.log']+list((root/'DATA').iterdir())
    meta=dict(schema=SCHEMA,case=case,case_sha256=case_hash(case),remote_run=str(root),field_quantity='solid_velocity_m_s',
        preview_note='无牵引洞内没有单元或声场；速度为实体原生节点；完整接收记录。',
        input_sha256={p.name:digest(p) for p in (root/'DATA').iterdir()},source_sha256={str(p.relative_to(root)):digest(p) for p in files},packer_sha256=digest(__file__),scientific_status='experimental_see_phase1_and_phase4_evidence')
    if c.get('attenuation',{}).get('enabled'):meta['scientific_status']='experimental_Q_integration_only_see_phase5_evidence'
    v=np.array(channels);coords=np.array(coords);steps=np.array(steps)
    a=dict(metadata=np.array(json.dumps(meta,ensure_ascii=False)),t=times,x=coords[:,0],z=coords[:,1],vx=v[:,0].T,vz=v[:,1].T,
        field_points=grid[ids],field_velocity=np.array(frames),field_step=steps,field_t=times[0]+(steps-1)*c['dt_s'],**read_material_vtk(o/'mesh_materials.vtk').arrays())
    validate_bundle(a)
    with dest.open('xb') as f:np.savez_compressed(f,**a)
    restored=load(dest)
    if any(not np.array_equal(a[k],restored[k]) for k in a):raise ValueError('结果打包往返不一致。')
    return meta
