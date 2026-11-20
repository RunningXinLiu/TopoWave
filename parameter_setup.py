"""Trial settings for this workbench's 5-GLL-point, 2-D isotropic internal mesh."""
import math

import numpy as np

from imaging import reference_column
from model import elevation, stations, validate
from materials import speed_bounds, kind, attenuating

SOURCE_BAND = 2.5
GLL_MIN_FRACTION = .17267316464601141


def steps_for_duration(span,dt):
    if dt<=0 or span<=0:
        raise ValueError("传播时长和 dt 均须大于零。")
    # span is from the first sample, not relative to the wavelet peak.
    return int(math.ceil(np.nextafter(span/dt,-np.inf)))+1


def _geometry(c):
    x=np.unique(np.r_[np.linspace(0,c["width_m"],513),[p[0] for p in c["surface"]]])
    edges=np.stack([elevation(c,x,i) for i in range(len(c["layers"])+1)])
    h=-np.diff(edges,axis=0)
    if np.min(h)<=0:
        raise ValueError("顶底界面相交，请先修正模型。")
    stretch=np.hypot(1,np.max(np.abs(np.diff(edges)/np.diff(x)),axis=1))
    return h.min(axis=1),h.max(axis=1),np.maximum(stretch[:-1],stretch[1:])


def mesh_estimates(c):
    hmin,hmax,stretch=_geometry(c)
    layers=np.asarray(c["layers"])
    nz=c.get("elements_per_layer") or np.ceil(layers[:,0]/c["element_m"]).astype(int)
    dx=c["width_m"]/math.ceil(c["width_m"]/c["element_m"])
    shortest=np.minimum(dx,hmin/nz)
    longest=np.maximum(dx*stretch,hmax/nz)
    fast,speed=speed_bounds(c)
    limit=np.min(GLL_MIN_FRACTION*shortest/fast)
    return float(c["dt_s"]/limit),float(np.min(4*speed/(SOURCE_BAND*c["f0_hz"]*longest)))


def modal_bounds(c,positions):
    from disba import GroupDispersion, PhaseDispersion
    periods=np.geomspace(1/(2*c["f0_hz"]),1/(.5*c["f0_hz"]),32)
    profiles=np.unique([reference_column(c,x).ravel() for x in positions],axis=0)
    group,phase=[],[]
    for profile in profiles:
        h,vp,vs,rho=profile.reshape(-1,4).T/1000
        for solver,values in ((GroupDispersion,group),(PhaseDispersion,phase)):
            try:
                curve=solver(h,vp,vs,rho)(periods,mode=0,wave="rayleigh")
            except Exception as exc:
                raise ValueError(f"局部 R0 求解失败，请手动设置时间窗：{exc}") from exc
            v=curve.velocity*1000
            if len(v)!=len(periods) or not np.isfinite(v).all() or min(v)<=0:
                raise ValueError("局部 R0 未覆盖目标频带，请手动设置或检查材料。")
            values.extend(v)
    return .8*min(group),1.2*max(group),.8*min(phase),1.2*max(phase)


def suggest_forward(c,quality="quick"):
    if quality not in ("quick","weak"):
        raise ValueError("未知参数估算档位。")
    original=dict(c)
    # Validate physics independently of numerical values that this button replaces.
    c=dict(c,dt_s=.0001,nstep=100,element_m=1.,sample_stride=1,snapshot_stride=0)
    c.pop("elements_per_layer",None)
    validate(dict(c,boundary="stacey"))  # Numerical PML extent is checked after recomputing the mesh.
    hmin,hmax,stretch=_geometry(c)
    layers=np.asarray(c["layers"])
    acoustic=c.get("physics")=="acoustic"
    fast,speed=speed_bounds(c)
    f0=c["f0_hz"]
    # Six average GLL intervals per shortest design wavelength.
    points=6 if quality=="quick" else 14
    target_cfl=.32 if quality=="quick" else .24
    hlimit=4*speed/(points*SOURCE_BAND*f0)
    elem=math.floor(min(hlimit/stretch)*10000)/10000
    if elem<=0:
        raise ValueError("所需单元尺寸小于桌面输入精度。")
    nz=np.maximum(1,np.ceil(hmax/hlimit)).astype(int)
    dx=c["width_m"]/math.ceil(c["width_m"]/elem)
    dt=math.floor(target_cfl*GLL_MIN_FRACTION*np.min(np.minimum(dx,hmin/nz)/(fast*stretch))*1e7)/1e7
    if dt<=0:
        raise ValueError("所需 dt 小于桌面输入精度。")
    xs=stations(c)
    positions=np.linspace(min(xs[0],c["shot_x_m"]),max(xs[-1],c["shot_x_m"]),5)
    r=max(abs(xs-c["shot_x_m"]))
    if acoustic:
        slow=float(min(speed))
        origin="最慢层 Vp"
    elif kind(c)!='isotropic' or attenuating(c):
        slow=.7*float(min(speed))
        origin='0.7倍最慢体波速度的试算预算（无该材料的群速理论，不保证慢模态覆盖）'
    else:
        slow=modal_bounds(c,positions)[0]
        origin="沿炮检路径五根局部一维柱的基阶群速慢端（含余量）"
    # Also budget one bottom round-trip at the slowest bulk speed as a time scale.
    bulk=np.hypot(r,2*sum(hmax))/min(speed)
    span=max(1.2/f0,c.get("source_t0_s",0.))+max(r/slow,bulk)+3/f0
    n=steps_for_duration(span,dt)
    stride=max(1,int(1/(12*SOURCE_BAND*f0*dt)))
    stride=min(stride,max(1,(n-1)//16))
    # Include the requested end in the recording despite receiver decimation.
    n=math.ceil((n-1)/stride)*stride+1
    values=dict(element_m=elem,elements_per_layer=nz.tolist(),dt_s=dt,nstep=n,sample_stride=stride,
                snapshot_stride=max(1,min(math.ceil(n/80),int(1/(6*f0*dt)))))
    if c.get("boundary")=="cpml":
        if not math.isfinite(original['element_m']) or original['element_m']<=0:
            raise ValueError("请先填写正的原网格尺寸，以便估算时保留侧面PML物理厚度。")
        old_dx=original["width_m"]/math.ceil(original["width_m"]/original["element_m"])
        values['pml_elements']=max(1,math.ceil(original.get('pml_elements',12)*old_dx/dx))
    validate(dict(c,**values))
    return values,(f"按 2.5 f₀ = {SOURCE_BAND*f0:g} Hz 设计，最短波长目标 {points} 个平均 GLL 间距；"
                   f"预计 CFL ≤ {target_cfl:g}。时长取最远道（{r:g} m）与一次底部往返的较长预算，"
                   f"含起始预移与 3 个主周期尾段。到时依据：{origin}。这是试算值，需查看波包与末段。")


def suggest_gate(g,right,min_offset,max_offset):
    c=g.meta.get("model")
    if not c or g.meta["component"]!="Vz":
        raise ValueError("需要随弹性 Vz 单炮保存的模型，才能估算面波窗。")
    r=(g.x-g.meta["source_x"])*(1 if right else -1)
    ids=(r>0)&(r>=min_offset)&((max_offset==0)|(r<=max_offset))
    if ids.sum()<3:
        raise ValueError("当前偏移距与方向至少需包含三道。")
    extent=np.r_[g.meta["source_x"],g.x[ids]]
    lo,hi,cmin,cmax=modal_bounds(c,np.linspace(min(extent),max(extent),5))
    dt=float(np.median(np.diff(g.t)))
    fmin=max(.5*c["f0_hz"],1/(len(g.t)*dt))
    fmax=min(2*c["f0_hz"],.49/dt)
    if fmax-fmin<2/(len(g.t)*dt):
        raise ValueError("现有记录的频带采样不足，请延长记录或减小输出间隔。")
    vals=dict(vslow=lo,vfast=hi,pad=1/c["f0_hz"],fmin=fmin,fmax=fmax,cmin=cmin,cmax=cmax,
              dc=max(.1,(cmax-cmin)/200))
    return vals


def assess_acquisition(c,fmin,fmax,relative_width=.2,spatial_weighting="equal"):
    """R0-based design candidates; not a forward or field-survey certificate."""
    from disba import PhaseDispersion,GroupDispersion
    if c.get("physics","elastic")!="elastic":
        raise ValueError("面波观测设计需要弹性模型。")
    if not (np.isfinite([fmin,fmax,relative_width]).all() and 0<fmin<fmax and 0<relative_width<1):
        raise ValueError("频带或目标相对主峰宽度无效。")
    if spatial_weighting not in ("equal","hann"):
        raise ValueError("未知空间权重。")
    xs=stations(c);positions=np.linspace(min(xs[0],c["shot_x_m"]),max(xs[-1],c["shot_x_m"]),5)
    if min(positions)<0 or max(positions)>c["width_m"]:
        raise ValueError("当前炮检点超出模型，请先修正。")
    f=np.geomspace(fmin,fmax,64);periods=1/f[::-1];speeds=[];groups=[]
    for x in positions:
        h,vp,vs,rho=reference_column(c,x).T/1000
        for solver,dest in [(PhaseDispersion,speeds),(GroupDispersion,groups)]:
            result=solver(h,vp,vs,rho)(periods,mode=0,wave="rayleigh")
            v=result.velocity[::-1]*1000
            if len(v)!=len(f) or not np.isfinite(v).all() or np.any(v<=0):
                raise ValueError("局部R0未完整覆盖目标频带，不能自动推算。")
            dest.append(v)
    speeds=np.array(speeds);groups=np.array(groups);lam=speeds/f
    # Ideal uniform-aperture small-slowness approximation, not inversion uncertainty.
    factor=.886 if spatial_weighting=="equal" else 1.44
    length=float(factor*np.max(lam)/relative_width)
    spacing=math.floor(float(np.min(lam)/4)*10)/10
    if spacing<=0:raise ValueError("候选道间距小于0.1m，请手动设计。")
    count=math.ceil(length/spacing)+1
    off=xs-c["shot_x_m"];one_side=bool(np.all(off>0) or np.all(off<0))
    aperture=float(np.ptp(xs));near=float(np.min(abs(off)))
    source_ratio=(f/c["f0_hz"])**2*np.exp(1-(f/c["f0_hz"])**2)
    return dict(status="candidate_not_validated",frequency_hz=f.tolist(),positions_m=positions.tolist(),
                phase_m_s=speeds.tolist(),group_m_s=groups.tolist(),
                wavelength_min_m=float(lam.min()),wavelength_max_m=float(lam.max()),
                nyquist_spacing_limit_m=float(lam.min()/2),candidate_spacing_m=spacing,
                candidate_aperture_m=(count-1)*spacing,candidate_receivers=count,
                candidate_near_offset_m=float(lam.max()/2),
                current_aperture_m=aperture,current_spacing_m=c["spacing_m"],current_near_offset_m=near,
                one_sided=one_side,current_nyquist_ok=bool(c["spacing_m"]<lam.min()/2),
                current_four_samples_ok=bool(c["spacing_m"]<=lam.min()/4),
                current_width_fraction_estimate=float(factor*lam.max()/aperture) if aperture>0 and one_side else None,
                target_width_fraction=relative_width,spatial_weighting=spatial_weighting,
                source_band_min_relative_amplitude=float(source_ratio.min()),
                current_receiver_duration_candidate_s=float(c.get("source_t0_s",0)+max(abs(off))/(.8*groups.min())+3/fmin),
                explanation="五根局部一维柱R0；四点/最短波长为候选裕量。孔径由理想主峰半功率全宽估计，等权0.886λ/ε、Hann1.44λ/ε，非误差条。近偏移0.5最长波长仅起始试算值，须做偏移对照；更长排列会混合不同地形。分裂排列应分别评估。未验证高阶模态、真实源支持及边界余量；不自动移动炮检点或扩大模型。")
