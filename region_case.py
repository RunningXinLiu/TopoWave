"""Dispatch explicit region schemas without silently changing the experiment."""
def validate(case):
    if isinstance(case,dict) and case.get('schema')=='region_geometry_v1':
        from materials import attenuating
        if attenuating(case['model']):raise ValueError('圆洞、多洞和地形洞体当前限无损ISO；Q组合请使用单矩形入口。')
        from region_geometry import build
        result=build(case);limit=stability_screen(case,result)
        if case['model']['dt_s']>limit:
            raise ValueError(f'当前贴体网格的5点GLL稳定性筛查建议dt≤{limit:.8g}s；输入dt超过该值。请显式修改时间步及相应步数/采样，不会自动改变输入。')
        return case
    from fluid_cavity import validate as legacy
    return legacy(case)


def write(case,destination):
    validate(case)
    if case.get('schema')=='region_geometry_v1':
        from region_geometry import write as exporter
    else:
        from fluid_cavity import write as exporter
    return exporter(case,destination)


def description(case):
    if case.get('schema')=='region_geometry_v1':
        return '；'.join(r['id']+' '+('圆形' if r['shape']['kind']=='circle' else '矩形')+' '+r['interior'].get('name','无牵引') for r in case['regions']) or '无洞背景对照'
    return case['interior'].get('name','无牵引空洞')+' · '+str(case['rectangle_m'])+' m'


def stability_screen(case,built):
    """Native check_grid.F90 estimate for audited NGLLX=5 ISO geometry backend.

    This necessary input screen is empirical, not a stability/accuracy proof.
    It reproduces 0.5*minimum_edge*minimum_GLL_fraction / local_max_velocity.
    """
    import numpy as np
    points,cells,mats,*_=built;p=points[cells]
    edge=np.linalg.norm(p-np.roll(p,-1,axis=1),axis=2).min(axis=1)
    layers=case['model']['layers'];speeds=np.empty(len(cells));material=len(layers)+1
    for i,layer in enumerate(layers,1):speeds[mats==i]=max(layer[1:3])
    for r in case['regions']:
        if r['interior']['kind']=='fluid':speeds[mats==material]=r['interior']['sound_speed_m_s'];material+=1
    return float(np.min(.5*edge*((1-np.sqrt(3/7))/2)/speeds))
