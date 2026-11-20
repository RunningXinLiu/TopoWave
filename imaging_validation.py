"""Validation shared by the two coherent imaging kernels; no signal processing."""
import numpy as np


def inputs(fft, freq, vel, distance, spatial_weights=None):
    u = np.asarray(fft, dtype=complex)
    f, c, r = (np.asarray(a, dtype=float) for a in (freq, vel, distance))
    if any(a.ndim != 1 or not len(a) or not np.isfinite(a).all() for a in (f,c,r)):
        raise ValueError('频率、速度和偏移坐标必须为非空有限一维数组。')
    if np.any(f <= 0) or np.any(c <= 0) or len(np.unique(r)) != len(r):
        raise ValueError('频率和速度必须为正，接收坐标不能重复。')
    if u.shape != (len(f),len(r)) or not np.isfinite(u).all():
        raise ValueError('复频谱必须为频率 × 接收道，且数值有限。')
    w = None if spatial_weights is None else np.asarray(spatial_weights,dtype=float)
    if w is not None and (w.shape != r.shape or not np.isfinite(w).all() or
                          np.any(w<0) or not np.isfinite(w.sum()) or w.sum()<=0):
        raise ValueError('空间权重需要逐道、有限、非负且总和为正。')
    return u,f,c,r,w
