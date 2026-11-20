"""Experimental flat ISO rectangular dry cavity; no desktop capability claim.

Q4 geometry, GLL order remains the solver's compiled order. Elastic unassigned
boundary edges are natural traction-free boundaries, not acoustic free surfaces.
"""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path

import numpy as np
from materials import kind
from model import validate, write_case, replace_values, stations


def build(case):
    if case.get('schema') != 'dry_rect_cavity_v1' or set(case) != {'schema', 'model', 'rectangle_m'}:
        raise ValueError('需要 dry_rect_cavity_v1，且只允许 model、rectangle_m。')
    c = case['model']
    validate(c)
    if c.get('domain_kind')=='free_plate':raise ValueError('自由板暂不支持内部洞体；不可静默改用地基底部吸收边界。')
    if (c.get('physics', 'elastic') != 'elastic' or kind(c) != 'isotropic'
            or c['surface'] or c.get('interface_relief') or c.get('boundary') != 'stacey'
            or len(c['layers']) != 1 or c.get('elements_per_layer')):
        raise ValueError('试验入口仅允许平地、单层ISO、Stacey、统一单元尺寸。')
    w, d, h = c['width_m'], c['layers'][0][0], c['element_m']
    rect = case['rectangle_m']
    if rect is not None:
        r = np.asarray(rect, dtype=float)
        if r.shape != (4,) or not np.isfinite(r).all():
            raise ValueError('rectangle_m 为 [xmin,xmax,zmin,zmax]。')
        x0,x1,z0,z1 = r
        if not (0 < x0 < x1 < w and -d < z0 < z1 < 0):
            raise ValueError('有限洞体必须严格位于域内。')
        for x,z in [(c['shot_x_m'],-c['shot_depth_m']), *[(x,-c['receiver_depth_m']) for x in stations(c)]]:
            if x0 <= x <= x1 and z0 <= z <= z1:
                raise ValueError('炮检点不能位于洞内或洞壁。')
    values = [w,d] + ([] if rect is None else list(rect))
    if any(not np.isclose(v/h, round(v/h), rtol=0, atol=1e-9) for v in values):
        raise ValueError('域及洞边界必须精确落在网格线上；不自动移动几何。')
    nx,nz = round(w/h),round(d/h)
    if nx*nz > 200000:
        raise ValueError('试验网格最多20万个单元。')
    xx,zz=np.meshgrid(np.arange(nx+1)*h, -d+np.arange(nz+1)*h)
    points=np.c_[xx.ravel(),zz.ravel()]
    cells=[]
    for j in range(nz):
        for i in range(nx):
            if rect is not None and x0 < (i+.5)*h < x1 and z0 < -d+(j+.5)*h < z1:
                continue
            a=j*(nx+1)+i
            cells.append([a,a+1,a+nx+2,a+nx+1])
    cells=np.array(cells)
    used,inverse=np.unique(cells,return_inverse=True)
    points,cells=points[used],inverse.reshape(-1,4)
    edges=defaultdict(list)
    for e,cell in enumerate(cells,1):
        for a,b in zip(cell,np.roll(cell,-1)):
            edges[tuple(sorted((a,b)))].append((e,int(a)+1,int(b)+1))
    absorbing=[]; free=[]; wall=[]
    for key,owners in edges.items():
        if len(owners)>2: raise ValueError('非流形边。')
        if len(owners)==2: continue
        e,a,b=owners[0]; xy=points[list(key)]
        side = (1 if np.all(xy[:,1]==-d) else 2 if np.all(xy[:,0]==w)
                else 4 if np.all(xy[:,0]==0) else 0)
        if side: absorbing.append([e,2,a,b,side])
        else:
            free.append([e,2,a,b])
            if not np.all(xy[:,1]==0): wall.append([e,2,a,b])
    expected=0 if rect is None else round(2*((x1-x0)+(z1-z0))/h)
    if len(wall)!=expected: raise ValueError('洞壁边计数不符。')
    # For these affine squares J=h²/4 everywhere, including all GLL nodes.
    qc=dict(elements=len(cells),nodes=len(points),jacobian_min_m2=h*h/4,
            cavity_edges=len(wall),natural_free_edges=len(free),absorbing_edges=len(absorbing),
            removed_elements=nx*nz-len(cells),geometry='Q4 affine squares',
            physical_boundary='dry traction-free cavity; no air degrees of freedom',
            status='topology_checked_not_physics_validated')
    return points,cells,absorbing,free,qc


def write(case, destination):
    points,cells,absorbing,free,qc=build(case)
    root=Path(destination)
    if root.exists(): raise ValueError('目标目录已存在；保留旧实验。')
    write_case(case['model'],root)
    data=root/'DATA'
    def rows(name,values,header=True):
        lines=[' '.join(map(str,row)) for row in values]
        (data/name).write_text(('\n'.join(([str(len(lines))] if header else [])+lines))+'\n')
    rows('nodes_coords_file',points)
    rows('mesh_file',cells+1)
    rows('materials_file',[[1]]*len(cells),False)
    rows('absorbing_surface_file',absorbing)
    # Acoustic list deliberately empty: elastic free edges are natural BCs.
    rows('free_surface_file',[])
    (data/'Par_file').write_text(replace_values((data/'Par_file').read_text(),
        dict(read_external_mesh='.true.',NGNOD=4)))
    export_model=dict(case['model'], external_geometry=dict(schema='dry_rect_cavity_v1', rectangle_m=case['rectangle_m'], authoritative_input='case.json'))
    (root/'model.json').write_text(json.dumps(export_model,indent=2)+'\n')
    (root/'case.json').write_text(json.dumps(case,indent=2)+'\n')
    (root/'natural_free_edges.json').write_text(json.dumps(free)+'\n')
    qc['input_sha256']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(data.iterdir())}
    qc['adapter_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    (root/'topology_qc.json').write_text(json.dumps(qc,indent=2)+'\n')
    return qc

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('case');p.add_argument('destination')
    a=p.parse_args();write(json.loads(Path(a.case).read_text()),a.destination)
