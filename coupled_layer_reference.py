"""Research-only exact homogeneous solid/fluid/solid secular matrix (SI).

exp(i*k*x-i*omega*t), depth positive down. Solid state [ux/i,uz,txz/i,tzz].
Normal displacement/traction continuous; zero shear on BOTH fluid interfaces.
Lower solid is an outgoing/decaying halfspace, never a free plate boundary.
Finite search; complex-k roots require outgoing-sheet and residual checks.
Not a production-certified general multilayer backend.
"""
import numpy as np
from scipy.linalg import expm
from scipy.optimize import root

def solid_generator(w,k,vp,vs,rho,scale):
    mu=rho*vs**2;la=rho*vp**2-2*mu;long=la+2*mu
    return np.array([[0,-k,scale/mu,0],[la*k/long,0,0,scale/long],
      [(4*mu*(la+mu)/long*k*k-rho*w*w)/scale,0,0,-la*k/long],
      [0,-rho*w*w/scale,k,0]],complex)

def vertical_q(w,k,v):
    q=np.sqrt(complex((w/v)**2-k*k))
    # Continue outgoing roots separately on each side of the bulk branch point.
    if k.real>w/v:
        if q.imag<0:q=-q
    elif q.real<0:q=-q
    return q

def halfspace(w,k,vp,vs,rho,scale):
    mu=rho*vs**2;la=rho*vp**2-2*mu
    p=vertical_q(w,k,vp);s=vertical_q(w,k,vs)
    return np.array([[k,-s],[1j*p,1j*k],[2j*mu*k*p/scale,1j*mu*(k*k-s*s)/scale],
      [-(la*k*k+(la+2*mu)*p*p)/scale,-2*mu*k*s/scale]],complex)

def operators(layers,f,k,propagator=expm):
    a,b,c=np.asarray(layers,float);w=2*np.pi*f;scale=a[3]*a[2]*w
    es=propagator(solid_generator(w,k,*a[1:],scale)*a[0])
    ef=propagator(np.array([[0,(1/(b[3]*b[1]**2)-k*k/(b[3]*w*w))*scale],[-b[3]*w*w/scale,0]],complex)*b[0])
    hs=halfspace(w,k,*c[1:],scale)
    return es,ef,hs

def matrix(layers,f,k,propagator=expm):
    es,ef,hs=operators(layers,f,k,propagator)
    top=es[:,:2];bottom=ef@top[[1,3],:]
    m=np.zeros((4,4),complex)
    m[0,:2]=top[2];m[1,2:]=hs[2]
    m[2:,:2]=bottom;m[2:,2:]=-hs[[1,3],:]
    # Nonzero row/column scalings preserve zeros, improve air/solid contrast.
    col=np.maximum(np.linalg.norm(m,axis=0),1e-100);m=m/col
    row=np.maximum(np.linalg.norm(m,axis=1),1e-100)
    return m/row[:,None]

def residual(layers,f,k,propagator=expm):
    s=np.linalg.svd(matrix(layers,f,k,propagator),compute_uv=False)
    return float(s[-1]/s[0])

def find_root(layers,f,c,attenuation=0.,propagator=expm):
    w=2*np.pi*f;k0=w/c
    def fun(z):
        if not np.isfinite(z).all() or not .1<z[0]<5 or abs(z[1])>1:return [1e3+abs(z[0]),1e3+abs(z[1])]
        with np.errstate(over='raise',invalid='raise',divide='raise'):
            try:val=np.linalg.det(matrix(layers,f,k0*complex(*z),propagator))
            except (FloatingPointError,ValueError):return [1e3,1e3]
        return [val.real,val.imag]
    r=root(fun,[1.,attenuation/k0],tol=1e-10)
    k=k0*complex(*r.x)
    if not np.isfinite(k) or k.real<=0 or k.imag< -1e-8 or residual(layers,f,k,propagator)>1e-10:return None
    return dict(f_hz=float(f),c_m_s=float(w/k.real),k_real=float(k.real),k_imag=float(k.imag),
      residual=residual(layers,f,k,propagator),optimizer_success=bool(r.success),kind='radiating' if w/k.real>layers[-1][2] else 'trapped',
      attenuation_length_m=float(1/k.imag) if k.imag>1e-12 else None)

def scan(layers,f,cmin=250.,cmax=1200.,samples=1001):
    cs=np.linspace(cmin,cmax,samples)
    vals=np.array([residual(layers,f,2*np.pi*f/c) for c in cs])
    seeds=cs[1:-1][(vals[1:-1]<vals[:-2])&(vals[1:-1]<vals[2:])]
    found=[]
    for c in seeds:
      for attenuation in (0.,1e-5,1e-3):
        r=find_root(layers,f,c,attenuation)
        if r and cmin<=r['c_m_s']<=cmax and not any(abs(r['k_real']-p['k_real'])<1e-6 and abs(r['k_imag']-p['k_imag'])<1e-6 for p in found):found.append(r)
    return sorted(found,key=lambda r:r['c_m_s'])


def solve_research(c,frequencies,cmin=250.,cmax=1200.,samples=1001):
    """Explicit experimental entry; returns root points, never automatic R0/R1 labels."""
    from model import validate
    validate(c)
    if c.get('physics')!='coupled' or c.get('domain_kind')!='solid_fluid_layers':
        raise ValueError('需要显式固—流体—固水平三层模型。')
    fs=np.asarray(frequencies,float)
    if fs.ndim!=1 or not len(fs) or not np.isfinite(fs).all() or np.any(fs<=0) or np.any(np.diff(fs)<=0):
        raise ValueError('频率须为正且递增。')
    if not 0<cmin<cmax or type(samples) is not int or not 101<=samples<=10001:
        raise ValueError('无效的有限速度搜索范围/采样数。')
    # Only tested frequency-thickness/contrast envelope: do not expose unbounded expm use.
    if fs.max()*c['layers'][0][0]/c['layers'][0][2]>1.2 or fs.max()*c['layers'][1][0]/c['layers'][1][1]>3:
        raise ValueError('超出本研究实现已检查的频率×厚度范围；请使用经验证的通用后端。')
    return dict(status='experimental_finite_search',x_m=c['first_x_m']+(c['receivers']-1)*c['spacing_m']/2,
        layers=c['layers'],halfspace='last material continued infinitely',
        roots=[r for f in fs for r in scan(c['layers'],f,cmin,cmax,samples)],
        scope='No global completeness, modal order or full-waveform accuracy certificate. Not a production default.')
