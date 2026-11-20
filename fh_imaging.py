"""Directed FH0: exp(-i wt) FFT, H0(1) steering, normalized r*dr weights."""
import numpy as np
from scipy.special import hankel1
from imaging_validation import inputs

def fh_power(fft,freq,vel,distance,spatial_weights):
    fft,freq,vel,r,spatial_weights=inputs(fft,freq,vel,distance,spatial_weights)
    if spatial_weights is None:raise ValueError("FH需要显式空间权重。")
    if len(r)<2 or np.any(r<=0):raise ValueError('FH要求正偏移距，不能包含炮点。')
    order=np.argsort(r);r=r[order];u=fft[:,order];w=np.asarray(spatial_weights)[order]
    q=radial_weights(r,w)
    result=np.empty((len(vel),len(freq)))
    for j,f in enumerate(freq):
        omega=2*np.pi*f;k=omega/vel
        a=omega*k*np.sum(u[j][None,:]*hankel1(0,k[:,None]*r)*q,axis=1)
        result[:,j]=abs(a)**2
    return result


def radial_weights(r,w):
    """v1 full endpoint gaps, interior half-neighbour spans; not trapezoids.

    Caller supplies sorted, distinct positive radii. Preserve the declared
    finite-array convention rather than silently changing endpoint weights.
    """
    q=r*np.gradient(r)*w
    if not np.isfinite(q).all() or not np.isfinite(q.sum()) or q.sum()<=0:
        raise ValueError('FH径向权重无效或溢出。')
    return q/q.sum()
