"""One-sided finite-array coherent waveform power on an f-c grid."""
import csv
import gzip
import hashlib
import json
from dataclasses import dataclass

import numpy as np
from scipy.signal.windows import tukey

from model import ROOT, elevation
from native_mesh import ElementMesh
from imaging_validation import inputs as imaging_inputs


@dataclass
class Wavefield:
    x: np.ndarray
    z: np.ndarray
    values: np.ndarray
    steps: np.ndarray
    times: np.ndarray

    @classmethod
    def read(cls,data):
        names = ["field_x","field_z","field_values","field_step","field_t"]
        if not any(name in data for name in names):
            return None  # Legacy receiver-only file.
        if not all(name in data for name in names):
            raise ValueError("快照数组不完整。")
        w = cls(*(data[name] for name in names))
        if (w.x.ndim!=1 or w.z.ndim!=2 or w.z.shape[1]!=len(w.x) or min(w.z.shape)<2 or
                w.times.ndim!=1 or not len(w.times) or w.steps.shape!=w.times.shape or
                w.values.shape!=(len(w.times),*w.z.shape) or
                not all(np.isfinite(a).all() for a in (w.x,w.z,w.values,w.times,w.steps)) or
                np.any(np.diff(w.times)<=0) or np.any(np.diff(w.x)<=0) or np.any(np.diff(w.z,axis=0)<=0)):
            raise ValueError("快照网格、帧时间或振幅无效。")
        return w


@dataclass
class Gather:
    t: np.ndarray
    x: np.ndarray
    traces: np.ndarray
    meta: dict
    field: Wavefield | None = None
    mesh: ElementMesh | None = None
    vx: np.ndarray | None = None
    pressure_field: Wavefield | None = None
    region_data: dict | None = None

    def __post_init__(self):
        if self.pressure_field is not None:
            if self.meta.get('model',{}).get('physics')!='coupled' or self.field is None:
                raise ValueError('双场结果必须明确固—流体—固模型及固体速度场。')
            for name in ('x','z','steps','times'):
                if not np.array_equal(getattr(self.field,name),getattr(self.pressure_field,name)):raise ValueError('声压与固体速度的节点/快照时轴不一致。')
        if self.vx is not None and (self.vx.shape!=self.traces.shape or not np.isfinite(self.vx).all()):
            raise ValueError("无效Vx记录。")
        if self.t.ndim != 1 or self.x.ndim != 1 or self.traces.shape != (len(self.t),len(self.x)):
            raise ValueError("波形矩阵必须是时间 × 接收道。")
        if len(self.t)<8 or len(self.x)<3 or not all(np.isfinite(a).all() for a in (self.t,self.x,self.traces)):
            raise ValueError("记录长度不足或包含非有限数。")
        if np.any(np.diff(self.x)<=0) or self.t[1]<=self.t[0] or not np.allclose(np.diff(self.t),np.median(np.diff(self.t)),rtol=1e-4):
            raise ValueError("要求递增的接收坐标和均匀递增的时间。")
        if self.meta.get("component") not in ("Vz","Vx","DAS_strain_rate","pressure") or not np.isfinite(self.meta["source_x"]):
            raise ValueError("记录需要有效的炮点和分量元数据。")

    @classmethod
    def open(cls, path):
        with np.load(path,allow_pickle=False) as data:
            if json.loads(str(data['metadata'])).get('case'):
                from region_analysis import surface_gather
                return surface_gather({k:data[k] for k in data.files})
            pressure=None
            if 'pressure_field_values' in data:
                pressure=Wavefield.read({key:(data['pressure_field_values'] if key=='field_values' else data[key]) for key in ('field_x','field_z','field_values','field_step','field_t')})
            return cls(data["t"],data["x"],data["traces"],json.loads(str(data["metadata"])),
                       Wavefield.read(data),ElementMesh.read(data),data["vx"] if "vx" in data else None,pressure)

    @classmethod
    def historical(cls):
        root = ROOT/"preview"
        with (root/"shot-S01-stations.csv").open() as f:
            rows = list(csv.DictReader(f))
        with gzip.open(root/"shot-S01-pressure.csv.gz","rt") as f:
            header = f.readline().strip().split(",")
            if header[1:] != [r["station"] for r in rows]:
                raise ValueError("历史道表和压力记录的台站名不匹配。")
            data = np.loadtxt(f,delimiter=",")
        with np.load(ROOT/"examples/historical-field.npz",allow_pickle=False) as archive:
            if str(archive["source_sha256"])!=hashlib.sha256((root/"shot-S01-pressure.csv.gz").read_bytes()).hexdigest():
                raise ValueError("历史快照与本地单炮记录不匹配。")
            field = Wavefield.read(archive)
            mesh = ElementMesh.read(archive)
        return cls(data[:,0],np.array([float(r["x_m"]) for r in rows]),data[:,1:],
                   dict(source_x=225,component="pressure",run="acoustic_dense_20261104_DoPCFt",
                        geometry_model=json.loads((ROOT/"examples/acoustic.json").read_text())),field,mesh)


def gather_window(g, *, right, vslow, vfast, pad, min_offset=0, max_offset=0, gated=True):
    off = (g.x - g.meta["source_x"]) * (1 if right else -1)
    if min_offset < 0 or max_offset < 0 or (max_offset and max_offset <= min_offset):
        raise ValueError("最大偏移距应大于最小偏移距，或设为 0 使用该侧全排列。")
    keep = np.flatnonzero((off>0) & (off>=min_offset) & ((max_offset==0) | (off<=max_offset)))
    keep = keep[np.argsort(off[keep])]
    if len(keep)<3:
        raise ValueError("所选侧和偏移距范围内至少需要三道。")
    offset = off[keep]
    if gated:
        if not (0 < vslow < vfast and pad > 0):
            raise ValueError("时间窗需要正慢、快视群速，快视群速更大，边缘余量须为正。")
        begin, end = offset/vfast-pad, offset/vslow+pad
        ramp = np.minimum(g.t[:,None]-begin, end-g.t[:,None])
        weights = np.sin(np.pi/2*np.clip(ramp/pad,0,1))**2
    else:
        begin = np.full_like(offset,g.t[0])
        end = np.full_like(offset,g.t[-1])
        weights = tukey(len(g.t),0.1)[:,None]
    if np.any(np.sum(weights,axis=0)<2):
        raise ValueError("部分道的时间窗与记录没有有效交集。")
    return dict(indices=keep,offset=offset,window_start=begin,window_end=end,weights=weights)


def windowed_traces(g,window):
    selected = g.traces[:,window["indices"]]
    weights = window["weights"]
    return (selected - np.sum(selected*weights,axis=0)/weights.sum(axis=0))*weights


def frequency_band_indices(freq, fmin, fmax):
    """Inclusive requested bounds, tolerant only to floating-point endpoint noise.

    Preserve the actual FFT frequencies; no rounding, interpolation or extra bins.
    """
    freq=np.asarray(freq)
    lower=(freq>=fmin)|np.isclose(freq,fmin,rtol=1e-12,atol=0)
    upper=(freq<=fmax)|np.isclose(freq,fmax,rtol=1e-12,atol=0)
    return np.flatnonzero(lower & upper)


def spectrum(g, *, right, fmin, fmax, cmin, cmax, dc, gated=True, vslow, vfast, pad,
             min_offset=0, max_offset=0, spatial_weighting="equal",method="PS"):
    if g.meta["component"] not in ("Vz","Vx","DAS_strain_rate"):
        raise ValueError("主动源面波分析需要弹性竖向速度记录。")
    dt = np.median(np.diff(g.t))
    if not (0 < fmin < fmax <= 0.5/dt and 0 < cmin < cmax and dc > 0 and (cmax-cmin)/dc <= 2500):
        raise ValueError("请检查频率范围、速度范围和速度步长（最多 2501 个速度）。")
    window = gather_window(g,right=right,gated=gated,vslow=vslow,vfast=vfast,pad=pad,
                           min_offset=min_offset,max_offset=max_offset)
    tapered = windowed_traces(g,window)
    offset = window["offset"]
    all_freq = np.fft.rfftfreq(len(g.t),dt)
    indices = frequency_band_indices(all_freq,fmin,fmax)
    if len(indices)<2:
        raise ValueError("该频带内不足两个真实 FFT 频点，请扩大频带或延长记录。")
    freq = all_freq[indices]
    vel = cmin+dc*np.arange(1+int(np.floor((cmax-cmin)/dc)))
    if len(vel)<2:
        raise ValueError("速度步长过大。")
    fft = np.fft.rfft(tapered,axis=0)[indices]*dt
    if spatial_weighting not in ("equal","hann"):
        raise ValueError("空间权重仅支持 equal 或 hann。")
    spatial_weights = np.ones(len(offset)) if spatial_weighting=="equal" else np.hanning(len(offset))
    spatial_weights /= spatial_weights.sum()
    if method=="PS":
        power = beam_power(fft,freq,vel,offset,None if spatial_weighting=="equal" else spatial_weights)
    elif method=="FH":
        from fh_imaging import fh_power, radial_weights
        power=fh_power(fft,freq,vel,offset,spatial_weights)
    else:raise ValueError("成像方法只支持PS或FH。")
    fh_meta={}
    if method=="FH":
        q=radial_weights(offset,spatial_weights)
        fh_meta=dict(fh_radial_weights=q,fh_kernel="H0(1)",fh_prefactor="omega*k",fh_quadrature="gradient_cell_full_endpoints_v1")
    # Conservative local two-samples-per-wavelength limit; leave these pixels blank.
    alias = vel[:,None] < 2*np.max(np.diff(offset))*freq
    if np.all(alias) or not np.any(power[~alias]>0):
        raise ValueError("所选网格没有可显示的非混叠信号。")
    return dict(f=freq,c=vel,power=power,alias=alias,spatial_weights=spatial_weights,
                spatial_weighting=spatial_weighting,operator_version="coherent_spatial_v3" if method=="PS" else "fh_hankel1_rdr_omegak_v2",method=method,fft_sign="exp(-i*omega*t)",fft_scale="dt; no one-sided doubling",
                fft_time_origin_s=float(g.t[0]),fft_phase_reference="t-t[0]",
                trace_normalization="none; preserve relative amplitudes",demean="weighted mean before taper",
                frequency_selection="inclusive_fft_bounds_rtol1e-12_v1",requested_frequency_bounds_hz=[float(fmin),float(fmax)],
                power_definition="abs(coherent_sum)**2",offset_geometry="horizontal source distance",
                **fh_meta,**window)


def beam_power(fft, freq, vel, distance, spatial_weights=None):
    """Square after coherent station stacking, for one time window and direction."""
    fft,freq,vel,distance,spatial_weights = imaging_inputs(fft,freq,vel,distance,spatial_weights)
    if spatial_weights is not None:
        spatial_weights = spatial_weights/spatial_weights.sum()
    power = np.empty((len(vel),len(freq)))
    travel = 2j*np.pi*distance[None,:]/vel[:,None]
    for i,f in enumerate(freq):
        phased = np.exp(f*travel)*fft[i]
        stack = (np.sum(phased,axis=1)/len(distance) if spatial_weights is None
                 else np.sum(phased*spatial_weights,axis=1))
        power[:,i] = np.abs(stack)**2
    return power


def reference_column(c,x):
    """Flatten a vertical column and continue its bottom material into a half-space."""
    if c.get('domain_kind')=='free_plate':
        raise ValueError('自由板底面是无牵引界面，不能使用半空间Rayleigh参考或R0自动参数；请用A/S板波参考。')
    if not 0 <= x <= c["width_m"] or c.get("physics","elastic")!="elastic":
        raise ValueError("参考剖面应位于弹性模型内。")
    from materials import require_isotropic_theory
    require_isotropic_theory(c)
    layers = np.array(c["layers"],dtype=float)
    interfaces = [float(elevation(c,x,i)) for i in range(len(layers)+1)]
    layers[:,0] = -np.diff(interfaces)
    layers[-1,0] = 0
    return layers


def array_center(x):
    """Physical midpoint of the complete receiver aperture, including muted traces."""
    return float((np.min(x)+np.max(x))/2)


def phase_curve(c, freq, mode=0):
    from materials import require_isotropic_theory
    require_isotropic_theory(c)
    if type(mode) is not int or not 0 <= mode <= 8:
        raise ValueError("理论模态阶数必须为0–8的整数。")
    freq = np.asarray(freq,dtype=float)
    if freq.ndim!=1 or len(freq)<2 or not np.isfinite(freq).all() or np.any(freq<=0) or np.any(np.diff(freq)<=0):
        raise ValueError("理论频率必须为递增的正数。")
    from disba import PhaseDispersion
    h,vp,vs,rho = np.array(c["layers"],dtype=float).T / 1000.
    h[-1] = 0  # Continue the bottom material as a half-space for the reference.
    ref = PhaseDispersion(h,vp,vs,rho)(1/freq[::-1],mode=mode,wave="rayleigh")
    return 1/ref.period[::-1], 1000*ref.velocity[::-1]


def fundamental(c, freq):
    return phase_curve(c,freq,0)


def theoretical_modes(c, freq, max_mode=0):
    """Independent branches; a missing higher mode must not erase valid lower modes."""
    from disba import DispersionError
    if type(max_mode) is not int or not 0 <= max_mode <= 8:
        raise ValueError("最高理论阶数必须为0–8的整数。")
    curves,status = {},{}
    for mode in range(max_mode+1):
        try:
            f,v=phase_curve(c,freq,mode)
            keep=np.isfinite(f)&np.isfinite(v)&(v>0)
            f,v=f[keep],v[keep]
            if len(f):curves[mode]=(f,v)
            status[str(mode)]=dict(returned_points=len(f),requested_points=len(freq),
                state="complete" if len(f)==len(freq) else "partial" if len(f) else "unavailable")
        except (DispersionError,RuntimeError,ArithmeticError) as exc:
            status[str(mode)]=dict(returned_points=0,requested_points=len(freq),state="failed",error=str(exc))
    return curves,status
