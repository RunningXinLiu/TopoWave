"""Explicit acquisition budgets, independent of solver and imaging cosmetics."""
import math
import numpy as np
from model import stations

def assess_bounds(c,fmin,fmax,phase_min,phase_max,group_min,relative_width=.2,spatial_weighting='equal'):
    values=[fmin,fmax,phase_min,phase_max,group_min,relative_width]
    if not np.isfinite(values).all() or not (0<fmin<fmax and 0<phase_min<=phase_max and group_min>0 and 0<relative_width<1):
        raise ValueError('频率、相速度、群速度和分辨率目标须为有效正值。')
    if spatial_weighting not in ('equal','hann'):raise ValueError('未知空间权重。')
    wavelength_min=phase_min/fmax;wavelength_max=phase_max/fmin
    spacing=math.floor(wavelength_min/4*10)/10
    if spacing<.1:raise ValueError('候选道距小于0.1 m，请手动设计。')
    factor=.886 if spatial_weighting=='equal' else 1.44
    n=math.ceil(factor*wavelength_max/relative_width/spacing)+1
    x=stations(c);off=x-c['shot_x_m'];length=float(np.ptp(x))
    target_end=c['first_x_m']+(n-1)*spacing
    pml=(c.get('pml_elements',0)*c['width_m']/math.ceil(c['width_m']/c['element_m'])) if c.get('boundary')=='cpml' else 0
    r=max(abs(off));duration=c.get('source_t0_s',0)+r/group_min+3/fmin
    f=np.geomspace(fmin,fmax,64);ratio=(f/c['f0_hz'])**2*np.exp(1-(f/c['f0_hz'])**2)
    return dict(status='candidate_not_validated',basis='manual_phase_and_group_bounds',
        wavelength_min_m=wavelength_min,wavelength_max_m=wavelength_max,
        candidate_spacing_m=spacing,candidate_receivers=n,candidate_aperture_m=(n-1)*spacing,
        candidate_last_x_m=target_end,candidate_near_offset_m=wavelength_max/2,
        fits_current_domain=bool(n<=2000 and c['first_x_m']>pml and target_end<c['width_m']-pml),
        current_spacing_m=c['spacing_m'],current_aperture_m=length,
        current_nyquist_ok=bool(c['spacing_m']<wavelength_min/2),
        current_four_samples_ok=bool(c['spacing_m']<=wavelength_min/4),
        current_width_fraction_estimate=factor*wavelength_max/length if length else None,
        current_receiver_duration_candidate_s=duration,
        candidate_receiver_duration_candidate_s=c.get('source_t0_s',0)+max(abs(c['first_x_m']-c['shot_x_m']),abs(target_end-c['shot_x_m']))/group_min+3/fmin,
        current_duration_s=(c['nstep']-1)*c['dt_s'],
        source_band_min_relative_amplitude=float(ratio.min()),
        candidate_balanced_ricker_f0_hz=math.sqrt((fmax*fmax-fmin*fmin)/(2*math.log(fmax/fmin))),
        target_width_fraction=relative_width,spatial_weighting=spatial_weighting,
        candidate_values=dict(spacing_m=spacing,receivers=n),
        explanation='手动相速度范围估算波长，手动最慢群速度估算到时；二者不能互换。候选保留首道/炮点，不改模型、源、dt或边界；无损/高阶/散射覆盖仍须实测。Hann降低旁瓣同时加宽主峰。')
