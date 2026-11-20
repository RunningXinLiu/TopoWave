"""Research conforming Q4 meshes for multiple rectangular/circular regions.

Circle boundaries are inscribed polygons with exact circular vertices, never
staircase cell removal. Optional surface deformation is confined above all
region patches, preserving their requested physical coordinates. This module
does not grant a production capability or numerical accuracy certificate.
"""
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import numpy as np
from model import validate as validate_model,write_case,replace_values,elevation,stations
from materials import kind,material_rows,attenuating


def surface_height(case,x):
    m=case['model']
    if case.get('surface_interpolation','spline')=='linear' and m['surface']:
        p=np.array(m['surface']);return np.interp(x,p[:,0],p[:,1])
    return elevation(m,x)


def bounds(region,patch=False):
    s=region['shape']
    if s['kind']=='rectangle':return np.array(s['bounds_m'],dtype=float)
    x,z=s['center_m'];r=s['patch_halfwidth_m'] if patch else s['radius_m']
    return np.array([x-r,x+r,z-r,z+r],dtype=float)


def inside(points,region):
    s=region['shape']
    if s['kind']=='circle':return np.sum((points-np.array(s['center_m']))**2,axis=1)<=s['radius_m']**2
    x0,x1,z0,z1=bounds(region)
    return (points[:,0]>=x0)&(points[:,0]<=x1)&(points[:,1]>=z0)&(points[:,1]<=z1)


def circle_blocks(region,h,points,cells,mats,fluid_id,host_id=1):
    """O-grid with a central square for fluids; no collapsed disk corners."""
    s=region['shape'];cx,cz=s['center_m'];R=s['radius_m'];L=s['patch_halfwidth_m'];n=round(2*L/h)
    lookup={tuple(np.round(p,12)):i for i,p in enumerate(points)}
    def node(xy):
        key=tuple(np.round(xy,12))
        if key not in lookup:lookup[key]=len(points);points.append(list(xy))
        else:
            if np.linalg.norm(np.array(points[lookup[key]])-xy)>1e-10:raise ValueError('网格拼接坐标不一致。')
        return lookup[key]
    def square(k,a):
        side,j=divmod(k,n);t=-a+2*a*j/n
        return np.array([(t,-a),(a,t),(-t,a),(-a,-t)][side])+[cx,cz]
    circular=np.array([[cx+R*np.cos(-3*np.pi/4+2*np.pi*k/(4*n)),cz+R*np.sin(-3*np.pi/4+2*np.pi*k/(4*n))] for k in range(4*n)])
    def ring(inner,outer,count,material):
        nodes=[[node((1-j/count)*inner[k]+(j/count)*outer[k]) for k in range(4*n)] for j in range(count+1)]
        for j in range(count):
            for k in range(4*n):
                other=(k+1)%(4*n);cells.append([nodes[j][k],nodes[j+1][k],nodes[j+1][other],nodes[j][other]]);mats.append(material)
    ring(circular,np.array([square(k,L) for k in range(4*n)]),max(1,int(np.ceil((np.sqrt(2)*L-R)/h))),host_id)
    if fluid_id is not None:
        a=R/2
        ring(np.array([square(k,a) for k in range(4*n)]),circular,max(1,int(np.ceil((R-a)/h))),fluid_id)
        nodes=[[node(np.array([cx-a+2*a*i/n,cz-a+2*a*j/n])) for i in range(n+1)] for j in range(n+1)]
        for j in range(n):
            for i in range(n):cells.append([nodes[j][i],nodes[j][i+1],nodes[j+1][i+1],nodes[j+1][i]]);mats.append(fluid_id)


def boundary_count(region,h):
    if region['shape']['kind']=='circle':return round(8*region['shape']['patch_halfwidth_m']/h)
    b=bounds(region);return round(2*((b[1]-b[0])+(b[3]-b[2]))/h)


def build(case):
    required={'schema','model','regions','surface_warp_depth_m'}
    if not required<=set(case) or set(case)-required-{'surface_interpolation'} or case['schema']!='region_geometry_v1':raise ValueError('需要完整 region_geometry_v1 输入。')
    if case.get('surface_interpolation','spline') not in ('linear','spline'):raise ValueError('地表插值必须明确为linear或spline。')
    m=case['model'];validate_model(m)
    if m.get('domain_kind')=='free_plate':raise ValueError('自由板暂不支持内部区域及其网格；请使用无内部区域的自由板入口。')
    if attenuating(m):raise ValueError('圆洞、多洞和地形洞体当前限无损ISO；Q组合请使用单矩形入口。')
    if kind(m)!='isotropic' or m.get('physics','elastic')!='elastic' or m['boundary']!='stacey' or m.get('elements_per_layer'):
        raise ValueError('贴体网格要求无损ISO、统一背景单元尺寸及Stacey；TI空洞尚未支持。')
    regions=case['regions'];h=m['element_m'];w=m['width_m']
    depths=np.cumsum([row[0] for row in m['layers']])
    d=depths[-1];nsolid=len(depths)
    if nsolid>1 and m['surface']:
        horizontal=-np.asarray(m['surface'])[:,1]
        if not np.array_equal(m.get('interface_relief'),np.tile(horizontal,(nsolid,1))):
            raise ValueError('多层区域网格要求地下界面和底界水平，请显式指定抵消地表的界面起伏。')
    elif m.get('interface_relief'):
        raise ValueError('此区域网格不支持自定义界面起伏。')
    if not isinstance(regions,list) or len(regions)>8:raise ValueError('可设置0–8个内部区域，空列表为无洞对照。')
    ids=[];boxes=[];values=[w,*depths]
    for r in regions:
        if set(r)!={'id','shape','interior'} or not isinstance(r['id'],str) or not r['id'].strip():raise ValueError('区域需唯一id、shape、interior。')
        if r['id'] in ids:raise ValueError('区域id重复。')
        ids.append(r['id']);s=r['shape'];f=r['interior']
        if s.get('kind')=='rectangle':
            if set(s)!={'kind','bounds_m'} or np.asarray(s['bounds_m']).shape!=(4,):raise ValueError('矩形需bounds_m。')
        elif s.get('kind')=='circle':
            if set(s)!={'kind','center_m','radius_m','patch_halfwidth_m'} or np.asarray(s['center_m']).shape!=(2,):raise ValueError('圆洞需圆心、半径及显式网格过渡半宽。')
            if not 0<s['radius_m']<s['patch_halfwidth_m']:raise ValueError('网格过渡半宽必须大于正半径。')
        else:raise ValueError('只支持rectangle或circle。')
        b=bounds(r,True);physical=bounds(r)
        if not np.isfinite(b).all() or not (0<b[0]<b[1]<w and -d<b[2]<b[3]<0):raise ValueError('区域及其网格过渡区必须严格位于平地参考域内。')
        for other in boxes:
            if not (b[1]+h<=other[0] or other[1]+h<=b[0] or b[3]+h<=other[2] or other[3]+h<=b[2]):raise ValueError('区域网格过渡区需至少间隔一个原始网格单元。')
        boxes.append(b);values.extend(b)
        # Only the outer patch must match the Cartesian grid. A circle's
        # physical radius need not be an integer multiple of the mesh size.
        if s['kind']=='rectangle':values.extend(physical)
        elif np.any((b[2]<-depths[:-1]) & (-depths[:-1]<b[3])):
            raise ValueError('圆洞过渡网格不能跨水平材料界面。')
        if f.get('kind')=='traction_free':
            if set(f)!={'kind'}:raise ValueError('无牵引区域不接受材料值。')
        elif f.get('kind')=='fluid':
            if set(f)!={'kind','name','rho_kg_m3','sound_speed_m_s','reference'}:raise ValueError('流体需完整参数及参考条件。')
            if any(type(f[k]) not in (int,float) or not np.isfinite(f[k]) or f[k]<=0 for k in ('rho_kg_m3','sound_speed_m_s')):raise ValueError('流体密度、声速必须正且有限。')
            if not all(isinstance(f[k],str) and f[k].strip() for k in ('name','reference')):raise ValueError('流体名称、参考条件不可空。')
            if not np.isfinite(f['rho_kg_m3']*f['sound_speed_m_s']**2):raise ValueError('流体体积模量溢出。')
        else:raise ValueError('需明确无牵引或流体。')
    if any(not np.isclose(v/h,round(v/h),rtol=0,atol=1e-9) for v in values):raise ValueError('边界及过渡区必须落在原始网格线上，不自动移动几何。')
    depth=case['surface_warp_depth_m']
    if m['surface']:
        if type(depth) not in (int,float) or not np.isfinite(depth) or not 0<depth<d or (nsolid>1 and depth>depths[0]):raise ValueError('变形深度须在计算域内，多层网格中不得超过第一层厚度。')
        for b in boxes:
            if b[3]>-depth:
                columns=np.linspace(b[0],b[1],1+round((b[1]-b[0])/h))
                if np.any(surface_height(case,columns)!=0):raise ValueError('洞体过渡区与地表变形层重叠。')
    elif depth is not None:raise ValueError('平地 surface_warp_depth_m 应显式为null。')
    nx,nz=round(w/h),round(d/h)
    if nx*nz>200000:raise ValueError('研究网格最多20万个单元。')
    xx,zz=np.meshgrid(np.arange(nx+1,dtype=float)*h,-d+np.arange(nz+1,dtype=float)*h);original=np.c_[xx.ravel(),zz.ravel()];points=original.copy()
    if m['surface']:
        weight=np.maximum(0,(original[:,1]+depth)/depth)
        points[:,1]+=surface_height(case,original[:,0])*weight
    zsrc=float(surface_height(case,m['shot_x_m']))-m['shot_depth_m']
    rx=stations(m);rz=surface_height(case,rx)-m['receiver_depth_m'];acquisition=np.vstack(([m['shot_x_m'],zsrc],np.c_[rx,rz]))
    if any(inside(acquisition,r).any() for r in regions):raise ValueError('炮检点不得位于任一内部区域或边界。')
    top=points[original[:,1]==0]
    actual=acquisition.copy();actual[:,1]=np.interp(acquisition[:,0],top[:,0],top[:,1])-np.r_[m['shot_depth_m'],np.full(len(rx),m['receiver_depth_m'])]
    if any(inside(actual,r).any() for r in regions):raise ValueError('离散地表下的炮检点位于内部区域。')
    cells=[];mats=[];fluid_ids={};next_id=nsolid+1
    for r in regions:
        if r['interior']['kind']=='fluid':fluid_ids[r['id']]=next_id;next_id+=1
    for j in range(nz):
        host_id=1+int(np.searchsorted(depths,d-(j+.5)*h))
        for i in range(nx):
            x=(i+.5)*h;z=-d+(j+.5)*h;material=host_id;remove=False
            for r in regions:
                x0,x1,z0,z1=bounds(r,r['shape']['kind']=='circle')
                if x0<x<x1 and z0<z<z1:
                    if r['shape']['kind']=='circle' or r['interior']['kind']=='traction_free':remove=True
                    else:material=fluid_ids[r['id']]
                    break
            if remove:continue
            a=j*(nx+1)+i;cells.append([a,a+1,a+nx+2,a+nx+1]);mats.append(material)
    n_background=len(points);points=points.tolist()
    for r in regions:
        if r['shape']['kind']=='circle':circle_blocks(r,h,points,cells,mats,fluid_ids.get(r['id']),1+int(np.searchsorted(depths,-r['shape']['center_m'][1])))
    points=np.array(points);original=np.vstack((original,points[n_background:]))
    if len(cells)>200000:raise ValueError('O-grid加密后超过20万个单元。')
    cells=np.array(cells);used,inverse=np.unique(cells,return_inverse=True);points=points[used];original=original[used];cells=inverse.reshape(-1,4);mats=np.array(mats)
    p=points[cells];minimum=float('inf');minimum_scaled=float('inf')
    # det(J) is affine on a bilinear Q4 map; checking all corners bounds it.
    for xi,eta in [(-1,-1),(-1,1),(1,-1),(1,1),(0,0)]:
        dx=np.array([-(1-eta),1-eta,1+eta,-(1+eta)])/4;de=np.array([-(1-xi),-(1+xi),1+xi,1-xi])/4
        a=p.transpose(0,2,1)@dx;b=p.transpose(0,2,1)@de;det=a[:,0]*b[:,1]-a[:,1]*b[:,0]
        minimum=min(minimum,float(det.min()));minimum_scaled=min(minimum_scaled,float((det/(np.linalg.norm(a,axis=1)*np.linalg.norm(b,axis=1))).min()))
    if minimum<=0 or not np.isfinite(minimum):raise ValueError('生成网格含非正Jacobian，拒绝输出。')
    edges=defaultdict(list)
    for e,cell in enumerate(cells,1):
        for a,b in zip(cell,np.roll(cell,-1)):edges[tuple(sorted((a,b)))].append((e,int(a)+1,int(b)+1))
    absorb=[];free=[];wall=[];coupling=0
    for key,owners in edges.items():
        if len(owners)>2:raise ValueError('非流形边。')
        if len(owners)==2:
            if (mats[owners[0][0]-1]>nsolid)!=(mats[owners[1][0]-1]>nsolid):coupling+=1
            continue
        e,a,b=owners[0];xy=original[list(key)]
        side=1 if np.all(xy[:,1]==-d) else 2 if np.all(xy[:,0]==w) else 4 if np.all(xy[:,0]==0) else 0
        if side:absorb.append([e,2,a,b,side])
        else:
            free.append([e,2,a,b])
            if not np.all(xy[:,1]==0):wall.append([e,2,a,b])
    expected_dry=sum(boundary_count(r,h) for r in regions if r['interior']['kind']=='traction_free')
    expected_fluid=sum(boundary_count(r,h) for r in regions if r['interior']['kind']=='fluid')
    if len(wall)!=expected_dry or coupling!=expected_fluid:raise ValueError('内部边界拓扑计数不符。')
    qc=dict(elements=len(cells),nodes=len(points),jacobian_min_m2=minimum,scaled_jacobian_min=minimum_scaled,cavity_edges=len(wall),coupling_edges=coupling,
        background_elements=nx*nz,fluid_material_ids=fluid_ids,circle_geometry='Conforming O-grid and central square; exact circular vertices with inscribed Q4 polygon edges; requires geometry refinement',
        surface_geometry='Piecewise-linear Q4 top edges sample declared '+case.get('surface_interpolation','spline')+' profile; region patches remain fixed',status='candidate_topology_only_not_wave_accuracy')
    qc['requested_acquisition_xz_m']=acquisition.tolist();qc['mesh_acquisition_xz_m']=actual.tolist()
    return points,cells,mats,absorb,free,qc


def write(case,destination):
    points,cells,mats,absorb,free,qc=build(case);root=Path(destination)
    if root.exists():raise ValueError('目标目录已存在。')
    write_case(case['model'],root);data=root/'DATA'
    acquisition=qc['mesh_acquisition_xz_m']
    (data/'SOURCE').write_text(replace_values((data/'SOURCE').read_text(),dict(zs=acquisition[0][1])))
    (data/'STATIONS').write_text(''.join(f'R{i:04d} AA {x:.17g} {z:.17g} 0 0\n' for i,(x,z) in enumerate(acquisition[1:],1)))
    def rows(name,values,header=True):
        lines=[' '.join(map(str,row)) for row in values];(data/name).write_text('\n'.join(([str(len(lines))] if header else [])+lines)+'\n')
    rows('nodes_coords_file',points);rows('mesh_file',cells+1);rows('materials_file',mats[:,None],False);rows('absorbing_surface_file',absorb);rows('free_surface_file',[])
    materials=material_rows(case['model']);fluid_count=0
    for r in case['regions']:
        f=r['interior']
        if f['kind']!='fluid':continue
        i=qc['fluid_material_ids'][r['id']];fluid_count+=1
        materials.append(f"{i} 1 {f['rho_kg_m3']:.17g} {f['sound_speed_m_s']:.17g} 0 0 0 9999 9999 0 0 0 0 0 0")
        b=bounds(r)
        with (data/'STATIONS').open('a') as stream:stream.write(f'F{fluid_count:04d} FL {(b[0]+b[1])/2:.17g} {(b[2]+b[3])/2:.17g} 0 0\n')
    par=replace_values((data/'Par_file').read_text(),dict(read_external_mesh='.true.',NGNOD=4,nbmodels=len(materials),
        ATTENUATION_VISCOACOUSTIC='.false.',seismotype='2,4' if fluid_count else '2',imagetype_wavefield_dumps=4 if fluid_count else 2))
    block=list(re.finditer(r'(?m)^[0-9][^\n]*(?:\n[0-9][^\n]*)*',par))[0];par=par[:block.start()]+'\n'.join(materials)+par[block.end():]
    (data/'Par_file').write_text(par);(root/'case.json').write_text(json.dumps(case,ensure_ascii=False,indent=2)+'\n')
    (root/'model.json').write_text(json.dumps(dict(case['model'],external_geometry=dict(schema=case['schema'],regions=case['regions'],authoritative_input='case.json')),ensure_ascii=False,indent=2)+'\n')
    (root/'natural_free_edges.json').write_text(json.dumps(free)+'\n');qc['input_sha256']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(data.iterdir())};qc['adapter_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    (root/'topology_qc.json').write_text(json.dumps(qc,indent=2)+'\n');return qc
