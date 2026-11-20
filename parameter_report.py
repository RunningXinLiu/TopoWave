"""Read-only design summary. Reports estimates without changing any input."""
import hashlib,json,math
import numpy as np
from model import validate,elevation,stations,snapshot_interval
from materials import kind,attenuating
from parameter_setup import mesh_estimates


def design_report(c):
    validate(c)
    nx=math.ceil(c['width_m']/c['element_m'])
    nz=c.get('elements_per_layer') or [math.ceil(r[0]/c['element_m']) for r in c['layers']]
    cfl,ppw=mesh_estimates(c)
    stride=c['sample_stride'];steps=math.ceil(c['nstep']/stride)*stride;samples=math.ceil(c['nstep']/stride)
    first=-c['source_t0_s'] if c.get('source_t0_s',0)>0 else None
    last=first+(samples-1)*stride*c['dt_s'] if first is not None else None
    x=stations(c);warnings=[]
    if cfl>=.5:warnings.append('预计CFL≥0.5，请检查dt及实际求解器限制。')
    if ppw<5:warnings.append('设计频带最短波长不足5个平均GLL间距，请检查网格分辨率。')
    if .5/(stride*c['dt_s'])<2.5*c['f0_hz']:warnings.append('接收输出Nyquist低于设计频带上限。')
    if attenuating(c):warnings.append('衰减速度频散会改变未松弛速度；几何CFL估计不能代替求解器检查。')
    report=dict(schema='design_report_v1',model_sha256=hashlib.sha256(json.dumps(c,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest(),
        medium=kind(c),attenuation_enabled=attenuating(c),
        nx=nx,nz_per_layer=nz,element_count=nx*sum(nz),horizontal_element_m=c['width_m']/nx,
        estimated_cfl=cfl,minimum_average_gll_intervals_per_wavelength=ppw,design_fmax_hz=2.5*c['f0_hz'],
        requested_steps=c['nstep'],solver_rounded_steps=steps,requested_span_s=(c['nstep']-1)*c['dt_s'],
        solver_span_s=(steps-1)*c['dt_s'],receiver_samples=samples,receiver_dt_s=stride*c['dt_s'],
        receiver_first_s=first,receiver_last_s=last,snapshot_stride=snapshot_interval(c),
        receiver_first_x_m=float(x[0]),receiver_last_x_m=float(x[-1]),aperture_m=float(np.ptp(x)),
        warnings=warnings,scope='Input/estimated numerical checks; no waveform accuracy or boundary validation.')
    if c.get('boundary')=='cpml':
        xx=np.linspace(0,c['width_m'],513);h=elevation(c,xx,len(nz)-1)-elevation(c,xx,len(nz))
        bottom=h*c['pml_elements']/nz[-1]
        report.update(pml_side_m=c['pml_elements']*c['width_m']/nx,pml_bottom_range_m=[float(bottom.min()),float(bottom.max())],alpha_max_s_inv=c.get('cpml_alpha_factor',1)*math.pi*c['f0_hz'])
    return report
