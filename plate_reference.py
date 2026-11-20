"""Rayleigh–Lamb roots for a traction-free isotropic plate (SI units).

Gresil & Giurgiutiu (2013), doi:10.1177/1045389X13486712, eqs. 1–2.
Pole-free determinants, with removable p=0/q=0 factors divided analytically.
This is not a finite cavity or fluid-loaded plate solver.
"""
import numpy as np
import hashlib
from pathlib import Path
from scipy.optimize import brentq
from materials import kind,attenuating


def cover(case,x):
    if not case or case.get('schema')!='rect_region_v1':
        raise ValueError('自由板近似仅支持单矩形内部区域。')
    m=case['model']
    if kind(m)!='isotropic' or attenuating(m) or m.get('surface') or len(m['layers'])!=1:
        raise ValueError('自由板近似需要平地、均匀无损ISO覆盖层。')
    x0,x1,z0,z1=case['rectangle_m']
    if not x0<x<x1 or z1>=0:raise ValueError('完整台阵中心不在空洞覆盖层内，不能用该处自由板替换背景参考。')
    if case['interior']['kind'] not in ('traction_free','fluid'):raise ValueError('未知洞内材料。')
    return dict(thickness_m=-float(z1),vp_m_s=float(m['layers'][0][1]),vs_m_s=float(m['layers'][0][2]),
                rho_kg_m3=float(m['layers'][0][3]),x_m=float(x),interior=case['interior'],
                scope='完整台阵中心的均匀自由板近似；两面无牵引、水平无限。忽略有限洞端及下伏固体绕射；流体填充时额外忽略流体加载，不能称实际声固耦合模态。')


def determinant(f,c,d,vp,vs,family):
    w=2*np.pi*f;k=w/np.asarray(c);p2=(w/vp)**2-k*k;q2=(w/vs)**2-k*k
    p=np.sqrt(p2+0j);q=np.sqrt(q2+0j);h=d/2
    cp=np.cos(p*h);cq=np.cos(q*h);sp=np.sinc(p*h/np.pi);sq=np.sinc(q*h/np.pi)
    if family=='S':a=(q2-k*k)**2*cp*sq;b=4*k*k*p2*sp*cq
    elif family=='A':a=(q2-k*k)**2*sp*cq;b=4*k*k*q2*cp*sq
    else:raise ValueError('family must be A or S')
    return np.real((a+b)/np.maximum(abs(a)+abs(b),np.finfo(float).tiny))


def roots(f,d,vp,vs,family,cmin=100.,cmax=5000.,samples=8193):
    if not (f>0 and d>0 and vp*vp>4*vs*vs/3 and vs>0 and 0<cmin<cmax):raise ValueError('无效自由板输入或搜索区间。')
    grid=np.geomspace(cmin,cmax,samples);v=determinant(f,grid,d,vp,vs,family);result=[]
    for i in np.flatnonzero(v[:-1]*v[1:]<0):
        c=brentq(lambda c:determinant(f,c,d,vp,vs,family),grid[i],grid[i+1],xtol=1e-9)
        if abs(determinant(f,c,d,vp,vs,family))<1e-7 and (not result or abs(c-result[-1])>1e-5):result.append(float(c))
    return result


def solve_plate(case,x,f,max_mode=0):
    return solve_parameters(cover(case,x),f,max_mode)


def solve_free_plate(c,x,f,max_mode=0):
    from model import validate
    validate(c)
    if c.get('domain_kind')!='free_plate' or not 0<=x<=c['width_m']:
        raise ValueError('需要明确的水平自由板输入及域内参考位置。')
    h,vp,vs,rho=c['layers'][0]
    spec=dict(thickness_m=h,vp_m_s=vp,vs_m_s=vs,rho_kg_m3=rho,x_m=float(x),
              scope='均匀无损ISO水平无限自由板，上下无牵引；A/S板波理论。有限数值域左右吸收效果仍须验收。')
    result=solve_parameters(spec,f,max_mode);result['label']='水平自由板实际参考'
    return result


def solve_parameters(spec,f,max_mode):
    curves={};labels={};bounds=(100.,5000.)
    for fi in f:
        values_by_family={family:roots(float(fi),spec['thickness_m'],spec['vp_m_s'],spec['vs_m_s'],family,*bounds)
                          for family in ('A','S')}
        # Never relabel an isolated higher root as A0 when the finite search
        # excludes the slow flexural fundamental. S0 lies above A0 and below
        # the long-wave extensional limit for this homogeneous free plate.
        extensional=np.sqrt(4*spec['vs_m_s']**2*(1-spec['vs_m_s']**2/spec['vp_m_s']**2))
        if (not values_by_family['A'] or values_by_family['A'][0]>=spec['vs_m_s'] or
                not values_by_family['S'] or values_by_family['S'][0]>extensional*(1+1e-8)):
            raise ValueError(f'{fi:g} Hz的有限搜索未覆盖自由板基阶；不能按高阶根冒充A0/S0。')
        for parity,family in enumerate(('A','S')):
            values=values_by_family[family]
            for n,c in enumerate(values[:max_mode+1]):
                key=str(2*n+parity);ff,cc=curves.setdefault(key,([],[]));ff.append(float(fi));cc.append(c);labels[key]=family+str(n)
    return dict(label='覆盖层自由板近似',curves=curves,curve_labels=labels,definition=spec['scope'],plate=spec,
                provenance=dict(backend='Rayleigh–Lamb free plate',source='https://doi.org/10.1177/1045389X13486712',
                implementation_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                phase_velocity_search_m_s=list(bounds),samples=8193,root_identity='ascending roots per parity; finite search, no global completeness claim'))
