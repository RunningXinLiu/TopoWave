"""Validated mixed-region preview bundle. Full receiver records, fluid-only fields."""
import hashlib,json,re,zipfile
from pathlib import Path
import numpy as np
from native_mesh import ElementMesh,read_material_vtk

SCHEMA='region_result_v1'
DUAL_SCHEMA='region_result_v2'
def case_hash(case):
    return hashlib.sha256(json.dumps(case,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1048576),b''):h.update(b)
    return h.hexdigest()

def validate_bundle(a):
    if json.loads(str(a['metadata'])).get('schema')=='region_geometry_result_v1':
        from region_geometry_result import validate_bundle as validate_geometry
        return validate_geometry(a)
    if json.loads(str(a['metadata'])).get('schema')=='region_dry_result_v1':
        from region_dry_result import validate_bundle as validate_dry
        return validate_dry(a)
    required={'metadata','t','x','z','vx','vz','pressure_t','pressure','pressure_xy','field_points','field_pressure','field_t','field_step','mesh_points','mesh_cells','mesh_materials','mesh_vtk_sha256'}
    meta=json.loads(str(a['metadata']))
    dual=meta.get('schema')==DUAL_SCHEMA
    if dual:required|={'solid_points','solid_velocity'}
    if set(a)!=required:raise ValueError('内部区域结果字段不完整或版本不兼容。')
    if meta.get('schema') not in (SCHEMA,DUAL_SCHEMA) or meta.get('field_quantity')!='fluid_pressure_Pa':raise ValueError('未知结果类型或单位。')
    if dual and meta.get('solid_quantity')!='solid_velocity_m_s':raise ValueError('固体速度单位不符。')
    case=meta['case'];c=case['model']
    if case.get('schema')!='rect_region_v1' or case['interior']['kind']!='fluid' or meta['case_sha256']!=case_hash(case):raise ValueError('输入类型或哈希不匹配。')
    from materials import attenuating,kind,validate_materials
    validate_materials(c)
    if kind(c)!='isotropic':raise ValueError('TI背景与内部流体区域不在支持范围。')
    if attenuating(c) and not dual:raise ValueError('Q声固结果必须包含同步固体速度与流体压力。')
    for k in required-{'metadata','mesh_vtk_sha256'}:
        if not np.issubdtype(a[k].dtype,np.number) or not np.isfinite(a[k]).all():raise ValueError('结果含非有限数或非法数组。')
    n=c['receivers'];nt=(c['nstep']+c['sample_stride']-1)//c['sample_stride']
    if a['t'].shape!=(nt,) or a['x'].shape!=(n,) or a['z'].shape!=(n,) or any(a[k].shape!=(nt,n) for k in ['vx','vz']):raise ValueError('完整道集形状不符。')
    if not np.allclose(a['x'],c['first_x_m']+np.arange(n)*c['spacing_m'],rtol=0,atol=1e-9) or not np.all(a['z']==-c['receiver_depth_m']):raise ValueError('炮检排列不符。')
    if not np.allclose(np.diff(a['t']),c['dt_s']*c['sample_stride'],rtol=1e-8,atol=1e-10):raise ValueError('记录时轴不符。')
    if a['pressure'].shape!=(nt,) or not np.array_equal(a['pressure_t'],a['t']):raise ValueError('洞中心声压记录不完整。')
    points=a['field_points'];steps=a['field_step'];nf=len(steps)
    if points.ndim!=2 or points.shape[1]!=2 or not len(points) or a['field_pressure'].shape!=(nf,len(points)) or a['field_t'].shape!=(nf,) or nf<2:raise ValueError('压力快照形状不符。')
    actual=nt*c['sample_stride'];stride=c.get('snapshot_stride') or int(np.ceil(c['nstep']/60))
    expected=sorted(set(range(stride,actual+1,stride))|{actual}|({5} if actual>=5 else set()))
    if not np.array_equal(steps,expected) or not np.allclose(a['field_t'],a['t'][0]+(steps-1)*c['dt_s'],rtol=0,atol=1e-9):raise ValueError('压力快照时轴或帧数不符。')
    x0,x1,z0,z1=case['rectangle_m']
    if not np.all((points[:,0]>x0)&(points[:,0]<x1)&(points[:,1]>z0)&(points[:,1]<z1)):raise ValueError('压力快照混入界面或固体节点。')
    if dual:
        p=a['solid_points'];v=a['solid_velocity']
        if p.ndim!=2 or p.shape[1]!=2 or not len(p) or v.shape!=(nf,len(p),2):raise ValueError('同步固体速度帧形状不符。')
        if not np.all((p[:,0]<x0)|(p[:,0]>x1)|(p[:,1]<z0)|(p[:,1]>z1)):raise ValueError('固体速度快照混入流体或界面节点。')
        if not np.all((p[:,0]>=0)&(p[:,0]<=c['width_m'])&(p[:,1]<=0)&(p[:,1]>=-c['layers'][0][0])):raise ValueError('固体节点超出计算域。')
    if a['pressure_xy'].shape!=(2,) or not np.allclose(a['pressure_xy'],[(x0+x1)/2,(z0+z1)/2]):raise ValueError('声压接收位置不符。')
    mesh=ElementMesh.read(a)
    if mesh is None or not set(np.unique(mesh.materials))=={1,2}:raise ValueError('实际混合材料网格缺失。')
    centers=mesh.points[mesh.cells[:,:4]].mean(axis=1)
    inside=(centers[:,0]>x0)&(centers[:,0]<x1)&(centers[:,1]>z0)&(centers[:,1]<z1)
    if not np.array_equal(mesh.materials,np.where(inside,2,1)):raise ValueError('网格材料与保存的内部区域不一致。')
    return meta,mesh

def load(path):
    try:
        with zipfile.ZipFile(path) as z:
            if sum(i.file_size for i in z.infolist())>128_000_000:raise ValueError('预览包超过128MB限制。')
    except zipfile.BadZipFile as exc:raise ValueError('文件不是有效NPZ结果包。') from exc
    with np.load(path,allow_pickle=False) as data:a={k:data[k] for k in data.files}
    validate_bundle(a);return a

def pack(root,destination,require_dual=False):
    root=Path(root).resolve();destination=Path(destination)
    if destination.exists():raise ValueError('预览包已存在，拒绝覆盖。')
    case=json.loads((root/'case.json').read_text());c=case['model'];o=root/'OUTPUT_FILES'
    if case.get('schema')=='region_geometry_v1':
        from region_geometry_result import pack as pack_geometry
        return pack_geometry(root,destination)
    if case.get('schema')=='rect_region_v1' and case['interior']['kind']=='traction_free':
        if require_dual:raise ValueError('无牵引洞不具有双场输出。')
        from region_dry_result import pack as pack_dry
        return pack_dry(root,destination)
    if case.get('schema')!='rect_region_v1' or case['interior']['kind']!='fluid':raise ValueError('本打包器仅支持显式流体区域。')
    traces=[];coords=[];pressure=None;times=None;source_files=[]
    rows=(root/'DATA/STATIONS').read_text().splitlines()
    for row in rows:
        station,net,x,z,*_=row.split();x,z=float(x),float(z)
        if net=='AA':
            channels=[]
            for suffix in ['BXX.semv','BXZ.semv']:
                p=o/f'{net}.{station}.{suffix}';t,v=np.loadtxt(p,unpack=True);source_files.append(p)
                if times is not None and not np.array_equal(times,t):raise ValueError('时轴不一致。')
                times=t;channels.append(v)
            traces.append(channels);coords.append([x,z])
        elif net=='FL' and pressure is None:
            p=o/f'{net}.{station}.PRE.semp';pt,pressure=np.loadtxt(p,unpack=True);source_files.append(p);pxy=[x,z]
        else:raise ValueError('未知接收器网络或重复流体接收器。')
    if pressure is None:raise ValueError('缺少流体压力接收点。')
    log=(root/'solver.log').read_text();counts=re.findall(r'number of global nodes in (?:outer|inner) elements\s*=\s*(\d+)',log)
    if len(counts)!=2:raise ValueError('缺少单进程节点计数。')
    n=sum(map(int,counts));gp=o/'wavefield_grid_for_dumps.bin';grid=np.fromfile(gp,dtype='<f4').reshape(-1,2);nodes=grid[:n]
    if len(nodes)!=n or not np.isfinite(nodes).all():raise ValueError('网格节点损坏。')
    x0,x1,z0,z1=case['rectangle_m'];ids=np.flatnonzero((nodes[:,0]>x0)&(nodes[:,0]<x1)&(nodes[:,1]>z0)&(nodes[:,1]<z1))
    if len(ids)>10000:ids=ids[np.linspace(0,len(ids)-1,10000).round().astype(int)]
    frames=[];steps=[]
    for p in sorted(o.glob('wavefield*_01.bin')):
        v=np.fromfile(p,dtype='<f4')
        if len(v)!=len(grid) or not np.isfinite(v[:n]).all():raise ValueError('压力帧尺寸或数值不符。')
        frames.append(v[ids]);steps.append(int(p.name[9:16]));source_files.append(p)
    velocity_files=sorted(o.glob('solid_velocity*_01.bin'));solid_arrays={}
    if require_dual and not velocity_files:raise ValueError('双场后端没有输出同步速度帧。')
    if velocity_files:
        velocity_steps=[int(p.name[len('solid_velocity'):len('solid_velocity')+7]) for p in velocity_files]
        if velocity_steps!=steps:raise ValueError('速度和压力必须来自同一次计算的相同步。')
        solid_ids=np.flatnonzero((nodes[:,0]<x0)|(nodes[:,0]>x1)|(nodes[:,1]<z0)|(nodes[:,1]>z1))
        if len(solid_ids)>20000:solid_ids=solid_ids[np.linspace(0,len(solid_ids)-1,20000).round().astype(int)]
        velocity=[]
        for p in velocity_files:
            v=np.fromfile(p,dtype='<f4').reshape(-1,2)
            if len(v)!=len(grid) or not np.isfinite(v[:n]).all():raise ValueError('速度帧尺寸或数值不符。')
            velocity.append(v[solid_ids]);source_files.append(p)
        solid_arrays=dict(solid_points=nodes[solid_ids],solid_velocity=np.array(velocity))
    source_files += [gp,o/'mesh_materials.vtk',root/'case.json',root/'model.json',root/'solver.log']+list((root/'DATA').iterdir())
    meta=dict(schema=SCHEMA,case=case,case_sha256=case_hash(case),remote_run=str(root),field_quantity='fluid_pressure_Pa',
        preview_note='仅严格流体内部原生节点；排除共享界面。完整地表记录；不包含固体速度快照。',
        input_sha256={p.name:digest(p) for p in (root/'DATA').iterdir()},source_sha256={str(p.relative_to(root)):digest(p) for p in source_files},
        packer_sha256=digest(__file__),scientific_status='experimental_see_phase3_evidence')
    if solid_arrays:
        meta.update(schema=DUAL_SCHEMA,solid_quantity='solid_velocity_m_s',
            preview_note='同一步原生压力和速度；按介质分别取样，排除共享界面节点；完整接收记录。',
            scientific_status='experimental_see_phase4_evidence')
    traces=np.asarray(traces);coords=np.asarray(coords);steps=np.array(steps,dtype=int)
    a=dict(metadata=np.array(json.dumps(meta,ensure_ascii=False)),t=times,x=coords[:,0],z=coords[:,1],vx=traces[:,0].T,vz=traces[:,1].T,
        pressure_t=pt,pressure=pressure,pressure_xy=np.array(pxy),field_points=nodes[ids],field_pressure=np.array(frames),field_step=steps,field_t=times[0]+(steps-1)*c['dt_s'],**solid_arrays,**read_material_vtk(o/'mesh_materials.vtk').arrays())
    validate_bundle(a)
    with destination.open('xb') as f:np.savez_compressed(f,**a)
    # Validate the serialized artifact, not just the in-memory arrays.
    restored=load(destination)
    if any(not np.array_equal(a[k],restored[k]) for k in a):raise ValueError('打包往返不一致。')
    return meta

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--require-dual',action='store_true');p.add_argument('run');p.add_argument('destination');a=p.parse_args();pack(a.run,a.destination,a.require_dual)
