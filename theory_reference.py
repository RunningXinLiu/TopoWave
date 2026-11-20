"""Explicit local 1-D references. No reference is used to form imaging power.

SASEM is a bounded real-root VTI reference, not an attenuating/leaky-mode solver.
Platform paths and pinned hashes belong to runtime.json, never to scene physics.
"""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import numpy as np
from imaging import reference_column, theoretical_modes
from materials import kind, attenuating, ti_stiffness
from model import elevation

SELECTIONS = [('actual','实际材料参考'),('elastic','弹性固体对照'),('both','两者叠加比较')]
BACKENDS = [('disba','disba / CPS（弹性）'),('qedisp','QEDisp（弹性）')]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_hash(root):
    root=Path(root);h=hashlib.sha256()
    for p in sorted(root.rglob('*.py')):
        h.update(str(p.relative_to(root)).encode());h.update(p.read_bytes())
    return h.hexdigest()


def column(c,x):
    # Explicit comparison material; never changes the forward model.
    elastic=copy.deepcopy(c);elastic['material_model']='isotropic'
    elastic['attenuation']={'enabled':False};elastic.pop('material_properties',None)
    layers=reference_column(elastic,x)
    local=any(np.ptp(elevation(c,([p[0] for p in c['surface']] or [0,c['width_m']]),i))>1e-8 for i in range(len(layers)))
    return layers,dict(x_m=float(x),selection='full_array_midpoint',local_approximation=bool(local),
        layers_h_m_vp_ms_vs_ms_rho_kgm3=layers.tolist(),original_material=kind(c),
        material_properties=copy.deepcopy(c.get('material_properties',[])),attenuation=copy.deepcopy(c.get('attenuation',{})),
        source_f0_hz=c.get('f0_hz'),model_sha256=hashlib.sha256(json.dumps(c,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
        wave='Rayleigh / P-SV',halfspace='last material continued to infinity')


def actual_backend(c):
    if c.get('physics')=='coupled':return None,'固—流体—固的实际声弹耦合频散后端尚未验收；不以自由板或删去空气层的固体曲线替代。'
    if c.get('domain_kind')=='free_plate':return 'plate','均匀水平自由板：Rayleigh–Lamb A/S模态；上下无牵引，不延伸底层为半空间。'
    if c.get('physics','elastic')!='elastic':return None,'当前入口仅支持固体 Rayleigh / P-SV 参考，不支持声学模型。'
    if attenuating(c):
        if kind(c)=='isotropic' and len(c['layers']) in (1,2) and all({'qkappa','qmu'} <= set(p) for p in c.get('material_properties',[])) and len(c.get('material_properties',[]))==len(c['layers']):
            return 'sls','各向同性 SLS：逐层 Qκ/Qμ、速度参考和松弛机制进入复波数求根，返回束缚分支的相速度。'
        return None,'当前实际 Q 参考仅支持各向同性均匀半空间或一层覆盖层加半空间，需逐层 Qκ/Qμ。'
    if kind(c)=='isotropic':return 'elastic','各向同性无损材料；与弹性对照是同一模型。'
    if kind(c)=='vti':return 'sasem','SASEM：垂直轴 VTI 实束缚模态，有限搜索；非泄漏波／复波数完备求解。'
    if kind(c)=='hti':return 'sasem','SASEM：沿水平对称轴 x 传播，使用旋转后 x–z 面内刚度求 P-SV 束缚模态。'
    return None,'未支持当前材料的一维频散参考。'


def route_summary(c,selection='actual',elastic_backend='disba'):
    """Describe capability before execution; does not certify runtime availability."""
    if not c:return '载入带材料模型的单炮记录后显示理论后端。'
    if c.get('physics')=='coupled':return actual_backend(c)[1]
    if c.get('domain_kind')=='free_plate':return actual_backend(c)[1]+' 最高阶N分别包含A0–AN、S0–SN；有限搜索保留缺根。'
    if c.get('physics','elastic')!='elastic':return actual_backend(c)[1]
    route,reason=actual_backend(c);label=dict(BACKENDS)[elastic_backend];parts=[]
    if selection in ('elastic','both'):
        parts.append('弹性对照 → '+label+'；保留 Vp0/Vs0/rho，忽略 TI 与 Q。')
    if selection in ('actual','both'):
        parts.append('实际材料 → '+({'sasem':reason,'sls':'SLS 复波数'}.get(route,label)) + '。' if route else reason)
    parts.append('最高阶 N 包含 R0–RN；缺根保留缺口。后端可用性在计算时核验。')
    return '\n'.join(parts)


def mode_status(curves,f,max_mode):
    return {str(m):dict(returned_points=len(curves.get(m,([],[]))[0]),requested_points=len(f),
        state='complete' if len(curves.get(m,([],[]))[0])==len(f) else 'partial' if m in curves else 'unavailable') for m in range(max_mode+1)}


def qedisp(layers,f,max_mode,runtime):
    spec=runtime.get('theory_backends',{}).get('qedisp',{})
    exe=Path(spec.get('executable',''))
    if not exe.is_file() or sha(exe)!=spec.get('sha256'):raise ValueError('QEDisp 本地程序缺失或散列不符；请配置 runtime.json，不自动换后端。')
    # Native depth-node input needs two rows. A same-material split has no
    # interface contrast and exactly represents the original half-space.
    split_m=None
    if len(layers)==1:
        layers=np.repeat(layers,2,axis=0)
        split_m=float(layers[0,2]/np.sqrt(f[0]*f[-1]))
        layers[0,0]=split_m
    with TemporaryDirectory() as td:
        root=Path(td);depth=np.r_[0,np.cumsum(layers[:-1,0])]/1000
        np.savetxt(root/'model.txt',np.column_stack((np.arange(len(layers)),depth,layers[:,3]/1000,layers[:,2]/1000,layers[:,1]/1000)),fmt='%.17g')
        np.savetxt(root/'f.txt',f,fmt='%.17g')
        subprocess.run([str(exe),'--model',str(root/'model.txt'),'--frequencies',str(root/'f.txt'),
            '--num-modes',str(max_mode+1),'--iwave','2','--output',str(root/'roots.txt')],check=True,capture_output=True,text=True,timeout=120)
        rows=[line.split() for line in (root/'roots.txt').read_text().splitlines() if line and not line.startswith('#')]
        if len(rows)!=len(f):raise ValueError('QEDisp 输出频点数量不匹配。')
        curves={}
        for fi,row in zip(f,rows):
            if not np.isclose(float(row[0]),fi,rtol=1e-12) or int(row[1])!=len(row)-2:raise ValueError('QEDisp 输出轴或根数量无效。')
            vals=np.array(row[2:],float)*1000
            if not np.isfinite(vals).all() or np.any(vals<=0) or np.any(np.diff(vals)<=0):raise ValueError('QEDisp 返回无效／非递增根。')
            for m,v in enumerate(vals[:max_mode+1]):
                ff,cc=curves.setdefault(m,([],[]));ff.append(float(fi));cc.append(float(v))
    return curves,mode_status(curves,f,max_mode),dict(backend='QEDisp root spectrum',sha256=spec['sha256'],homogeneous_same_material_split_m=split_m,source_checkout_commit=spec.get('source_checkout_commit'),interface_source_sha256=spec.get('interface_source_sha256'),build_provenance=spec.get('build_provenance'),units='input km, km/s, g/cm3; output converted to m/s',mode_definition='ascending real-root ordinal; no cross-model identity guarantee')


def sasem(c,layers,f,max_mode,runtime,progress=None):
    spec=runtime.get('theory_backends',{}).get('sasem',{});vendor=Path(spec.get('vendor_dir',''))
    root=vendor/'python_sasem'
    if not root.is_dir() or source_hash(root)!=spec.get('source_sha256'):raise ValueError('SASEM 本地源码缺失或散列不符；请配置 runtime.json，不自动换后端。')
    sys.path.insert(0,str(vendor))
    from python_sasem.anisotropy_vti import VTIPSVLayer
    from python_sasem.vti_frequency_inertia import FrequencyOptions,solve_normal_modes_frequency
    from python_sasem.vti_halfspace_inertia import InertiaOptions
    media=[]
    for i,(l,p) in enumerate(zip(layers,c['material_properties'])):
        stiffness=ti_stiffness(l,p,kind(c))
        media.append(VTIPSVLayer(float(l[0]) if i<len(layers)-1 else None,stiffness[0,0],stiffness[0,2],stiffness[2,2],stiffness[4,4],float(l[3])))
    # Explicit bounded search, recorded below; unsupported roots remain absent.
    lower=float(.5*np.min(layers[:,2]));curves={};failures=[]
    degree=int(spec.get('degree',8))
    for index,fi in enumerate(f, start=1):
        r=solve_normal_modes_frequency(tuple(media),float(fi),'rayleigh',FrequencyOptions(inertia=InertiaOptions(degree=degree),phase_velocity_min=lower,count_backend='eigen'),modes=tuple(range(max_mode+1)))
        for p in r.predictions:
            if r.search_converged and p.status=='ok' and p.phase_velocity is not None and np.isfinite(p.phase_velocity):
                ff,cc=curves.setdefault(p.mode,([],[]));ff.append(float(fi));cc.append(float(p.phase_velocity))
            else:failures.append(dict(f_hz=float(fi),mode=p.mode,status=p.status,search_converged=bool(r.search_converged)))
        if progress is not None:progress(index,len(f))
    return curves,mode_status(curves,f,max_mode),dict(backend='SASEM '+kind(c).upper()+' P-SV frequency inertia',orientation=kind(c),source_sha256=spec['source_sha256'],degree=degree,
        phase_velocity_min_m_s=lower,count_backend='eigen',scan_intervals=16,scan_refinements=2,failures=failures,
        scope='bounded real trapped-mode intersections; not global completeness, leaky or attenuation reference')


def solve(c,x,f,max_mode=0,selection='actual',elastic_backend='disba',runtime=None,region_case=None,plate=False,progress=None):
    if c.get('physics','elastic')!='elastic':raise ValueError(actual_backend(c)[1])
    if selection not in dict(SELECTIONS) or elastic_backend not in dict(BACKENDS):raise ValueError('未知理论参考选项。')
    if type(max_mode) is not int or not 0<=max_mode<=8:raise ValueError('理论阶数必须为0–8。')
    f=np.asarray(f,float)
    if f.ndim!=1 or len(f)<2 or not np.isfinite(f).all() or np.any(f<=0) or np.any(np.diff(f)<=0):raise ValueError('理论频率必须递增且为正数。')
    if c.get('domain_kind')=='free_plate':
        from plate_reference import solve_free_plate
        group=solve_free_plate(c,x,f,max_mode)
        groups={purpose:copy.deepcopy(group) for purpose in (['elastic','actual'] if selection=='both' else [selection])}
        ref=dict(x_m=float(x),selection='full_array_midpoint',local_approximation=False,
            layers_h_m_vp_ms_vs_ms_rho_kgm3=copy.deepcopy(c['layers']),original_material='isotropic',
            halfspace='none; lower face traction-free',wave='Lamb A/S',
            model_sha256=hashlib.sha256(json.dumps(c,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
            groups=groups,requested_f_hz=f.tolist(),modes_requested=list(range(max_mode+1)),
            theory_selection=selection,elastic_backend='Rayleigh–Lamb free plate')
        return dict(reference=ref,notes=[f'完整台阵中心 x={x:g} m。'+group['definition'],
            '本例实际材料与弹性对照均为同一自由板；不用半空间后端，曲线不参与成像。'])
    layers,ref=column(c,x);groups={};notes=[];route,reason=actual_backend(c)
    requested=['elastic','actual'] if selection=='both' else [selection]
    for purpose in requested:
        if purpose=='actual' and route is None:
            groups[purpose]=dict(curves={},status={},label=dict(SELECTIONS)[purpose],error=reason)
            notes.append(reason);continue
        backend=route if purpose=='actual' and route in ('sasem','sls') else elastic_backend
        try:
            if backend=='disba':
                import importlib.metadata
                curves,status=theoretical_modes(dict(layers=layers),f,max_mode)
                provenance=dict(backend='disba / CPS',version=importlib.metadata.version('disba'))
            elif backend=='qedisp':curves,status,provenance=qedisp(layers,f,max_mode,runtime or {})
            elif backend=='sls':
                from sls_reference import sls_modes
                curves,provenance=sls_modes(c,layers,f,max_mode)
                status=mode_status(curves,f,max_mode)
            else:curves,status,provenance=sasem(c,layers,f,max_mode,runtime or {},progress)
            if purpose=='elastic':
                definition='保留输入 Vp0/Vs0/rho（ISO为Vp/Vs/rho），忽略各向异性和损耗；不是唯一等效介质。'
            else:definition=reason
            if provenance.get('homogeneous_same_material_split_m') is not None:
                definition+=' QEDisp 两个深度节点使用完全相同的半空间材料。'
            groups[purpose]=dict(curves={str(m):[np.asarray(a).tolist() for a in curve] for m,curve in curves.items()},status=status,
                label=dict(SELECTIONS)[purpose],definition=definition,provenance=provenance)
            notes.append(dict(SELECTIONS)[purpose]+'：'+provenance['backend']+'。'+definition)
            if any(v['state']!='complete' for v in status.values()):notes.append('部分阶次／频点无有效根；保留缺口，不外推。')
        except (ValueError,RuntimeError,ImportError,ArithmeticError,OSError,subprocess.SubprocessError) as exc:
            groups[purpose]=dict(curves={},status={},label=dict(SELECTIONS)[purpose],error=str(exc))
            notes.append(dict(SELECTIONS)[purpose]+'失败：'+str(exc))
    if plate:
        from plate_reference import solve_plate
        if not region_case or region_case.get('model')!=c:raise ValueError('板波参考的区域与单炮模型不一致。')
        groups['plate']=solve_plate(region_case,x,f,max_mode)
        notes.append(groups['plate']['definition'])
    ref.update(groups=groups,requested_f_hz=f.tolist(),modes_requested=list(range(max_mode+1)),theory_selection=selection,elastic_backend=elastic_backend)
    notes.insert(0,f'完整台阵中心 x={x:g} m，底层作半空间。')
    if ref['local_approximation']:notes.append('起伏界面在该柱展平；不是二维地形的精确频散曲线。')
    return dict(reference=ref,notes=notes)


if __name__=='__main__':
    request=json.loads(Path(sys.argv[1]).read_text())
    try:result=solve(**request,progress=lambda done,total:print(f"progress {done} {total}",flush=True))
    except Exception as exc:result=dict(error=str(exc))
    Path(sys.argv[2]).write_text(json.dumps(result,ensure_ascii=False,allow_nan=False))
