"""Explicit rectangular internal-region inputs; experimental ISO/Stacey route."""
import copy
import hashlib
import json
from pathlib import Path
import re
import numpy as np
from cavity_mesh import build as dry_build, write as dry_write
from model import replace_values
from materials import attenuating

PRESETS={
    'air':dict(kind='fluid',name='空气候选',rho_kg_m3=1.2,sound_speed_m_s=343.,reference='约20°C常压；用户应按实际条件编辑'),
    'water':dict(kind='fluid',name='清水候选',rho_kg_m3=998.,sound_speed_m_s=1482.,reference='约20°C；用户应按实际条件编辑'),
    'traction_free':dict(kind='traction_free'),
}

def validate(case):
    if not isinstance(case,dict) or set(case)!={'schema','model','rectangle_m','interior'} or case['schema']!='rect_region_v1':
        raise ValueError('需要 rect_region_v1 完整输入。')
    interior=case['interior']
    if not isinstance(interior,dict) or interior.get('kind') not in ('traction_free','fluid'):
        raise ValueError('请明确选择无牵引或流体填充。')
    if interior['kind']=='traction_free':
        if set(interior)!={'kind'}:raise ValueError('无牵引空洞不接受材料参数。')
    else:
        allowed={'kind','name','rho_kg_m3','sound_speed_m_s','reference'}
        if set(interior)!=allowed:raise ValueError('流体需显式名称、密度、声速和参考条件；不接受隐藏的Vs/Q。')
        for k in ('rho_kg_m3','sound_speed_m_s'):
            v=interior[k]
            if type(v) not in (float,int) or not np.isfinite(v) or v<=0:raise ValueError('流体密度和声速必须是正有限数。')
        if not np.isfinite(interior['rho_kg_m3']*interior['sound_speed_m_s']*interior['sound_speed_m_s']):
            raise ValueError('流体体积模量超出有限数范围。')
        for k in ('name','reference'):
            if not isinstance(interior[k],str) or not interior[k].strip():raise ValueError('需填写流体名称及参数来源/参考条件。')
    dry=dict(schema='dry_rect_cavity_v1',model=case['model'],rectangle_m=case['rectangle_m'])
    if case['rectangle_m'] is None:raise ValueError('必须提供有限矩形内部区域。')
    dry_build(dry)
    return dry


def write(case,destination):
    dry=validate(case); fluid=case['interior']['kind']=='fluid'
    full=copy.deepcopy(dry)
    if fluid:full['rectangle_m']=None
    qc=dry_write(full,destination);root=Path(destination);data=root/'DATA'
    if fluid:
        points,cells,_,_,_=dry_build(full)
        centers=points[cells].mean(axis=1);x0,x1,z0,z1=case['rectangle_m']
        inside=(centers[:,0]>x0)&(centers[:,0]<x1)&(centers[:,1]>z0)&(centers[:,1]<z1)
        mats=np.where(inside,2,1)
        (data/'materials_file').write_text('\n'.join(map(str,mats))+'\n')
        f=case['interior']
        par=replace_values((data/'Par_file').read_text(),dict(nbmodels=2,seismotype='2,4',imagetype_wavefield_dumps=4,ATTENUATION_VISCOACOUSTIC='.false.'))
        blocks=list(re.finditer(r'(?m)^[0-9][^\n]*(?:\n[0-9][^\n]*)*',par))
        b=blocks[0]
        material=f"2 1 {f['rho_kg_m3']:.17g} {f['sound_speed_m_s']:.17g} 0 0 0 9999 9999 0 0 0 0 0 0"
        par=par[:b.end()]+'\n'+material+par[b.end():]
        (data/'Par_file').write_text(par)
        # One interior pressure sensor, plus all original surface stations.
        with (data/'STATIONS').open('a') as stream:
            stream.write(f"F0001 FL {(x0+x1)/2:.17g} {(z0+z1)/2:.17g} 0 0\n")
        qc.update(fluid_elements=int(inside.sum()),coupling_edges_expected=round(2*(x1-x0+z1-z0)/case['model']['element_m']),
                  physical_boundary='inviscid acoustic-elastic coupling; no free boundary at cavity wall',
                  field_output='pressure Pa; solid pressure must be masked for fluid-only display')
    (root/'case.json').write_text(json.dumps(case,ensure_ascii=False,indent=2)+'\n')
    model=dict(case['model'],external_geometry=dict(schema=case['schema'],rectangle_m=case['rectangle_m'],interior=case['interior'],authoritative_input='case.json'))
    (root/'model.json').write_text(json.dumps(model,ensure_ascii=False,indent=2)+'\n')
    qc['input_sha256']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(data.iterdir())}
    qc['fluid_adapter_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    (root/'topology_qc.json').write_text(json.dumps(qc,indent=2)+'\n')
    return qc

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('case');p.add_argument('destination');a=p.parse_args()
    write(json.loads(Path(a.case).read_text()),a.destination)
