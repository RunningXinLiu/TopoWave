"""Uniform-gauge axial strain rate for a straight horizontal fiber, SI units."""
import numpy as np

def horizontal_strain_rate(t,x,vx,centers,gauge_length):
    t,x,vx,centers=map(np.asarray,(t,x,vx,centers))
    if (t.ndim!=1 or x.ndim!=1 or centers.ndim!=1 or len(t)<2 or len(x)<2 or not len(centers)
        or vx.shape!=(len(t),len(x)) or any(not np.isfinite(a).all() for a in (t,x,vx,centers))
        or np.any(np.diff(t)<=0) or np.any(np.diff(x)<=0) or np.any(np.diff(centers)<=0)):
        raise ValueError('Invalid receiver arrays')
    if type(gauge_length) not in (int,float) or not np.isfinite(gauge_length) or gauge_length<=0:
        raise ValueError('Gauge length must be positive metres')
    def indices(points):
        result=[]
        for p in points:
            i=int(np.argmin(abs(x-p)))
            if abs(x[i]-p)>1e-7:raise ValueError('Gauge endpoints require native receivers; interpolation is not enabled')
            result.append(i)
        return np.array(result)
    left=indices(centers-gauge_length/2);right=indices(centers+gauge_length/2)
    return dict(strain_rate=(vx[:,right]-vx[:,left])/gauge_length,centers=centers.copy(),
                left_x=x[left],right_x=x[right],gauge_length_m=gauge_length,unit='s^-1',
                operator='horizontal_uniform_gauge_velocity_difference_v1')
