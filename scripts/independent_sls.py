"""Independent dense least-squares SLS material specification, plane strain.

Matches the declared log-spaced pole / frequency grid with USE_SOLVOPT=false.
No native relaxation fitter or stress/time-stepping implementation is imported.
Qkappa refers to the 2D plane-strain dilatational modulus lambda+mu here.
"""
import numpy as np


def fit(q,f0,n):
    if not (np.isfinite(q) and 0<q<=9999 and f0>0 and np.isfinite(f0) and type(n) is int and 3<=n<=8):
        raise ValueError('Unsupported explicit SLS input')
    # The fixed native build forms normalized log-grid exponents in default
    # REAL before raising a double base. Preserve this declared grid rounding
    # (not a fitted correction); n=3 is exactly representable either way.
    exponent=np.arange(n,dtype=np.float32)/np.float32(n-1)
    poles=2*np.pi*(f0/10)*100.**exponent.astype(float)
    if q==9999:return poles,np.zeros(n)
    exponent=np.arange(2*n-1,dtype=np.float32)/np.float32(2*n-2)
    w=2*np.pi*(f0/10)*100.**exponent.astype(float)
    A=w[:,None]*(poles[None,:]-w[:,None]/q)/(poles[None,:]**2+w[:,None]**2)
    weights=np.linalg.lstsq(A,np.full(len(w),1/q),rcond=None)[0]
    if np.any(weights<0):raise ValueError('Non-passive SLS fit; do not silently clip weights')
    return poles,weights


def material(rho,vp,vs,qkappa,qmu,f0,n,velocity_reference):
    if velocity_reference not in ('source_f0','unrelaxed'):raise ValueError('Explicit velocity reference required')
    mu=rho*vs**2;kappa=rho*(vp**2-vs**2)
    if min(rho,mu,kappa)<=0:raise ValueError('Invalid plane-strain material')
    result={}
    for name,value,q in [('kappa',kappa,qkappa),('mu',mu,qmu)]:
        poles,weights=fit(q,f0,n);fractions=weights/(1+weights.sum())
        ratio=1-np.sum(fractions*poles**2/(poles**2+(2*np.pi*f0)**2))
        unrelaxed=value/ratio if velocity_reference=='source_f0' else value
        result[name]=dict(poles_s_inv=poles.tolist(),weights=weights.tolist(),relaxation_fractions=fractions.tolist(),
            unrelaxed_pa=unrelaxed,relaxed_pa=unrelaxed*(1-fractions.sum()),tau_epsilon_s=((1+n*weights)/poles).tolist())
    result['vp_unrelaxed']=float(np.sqrt((result['kappa']['unrelaxed_pa']+result['mu']['unrelaxed_pa'])/rho))
    result['vs_unrelaxed']=float(np.sqrt(result['mu']['unrelaxed_pa']/rho))
    return result


def complex_modulus(spec,frequency):
    pole=np.array(spec['poles_s_inv']);b=np.array(spec['relaxation_fractions']);w=2*np.pi*np.asarray(frequency)
    return spec['unrelaxed_pa']*(1-np.sum(b*pole/(pole+1j*w[...,None]),axis=-1))
