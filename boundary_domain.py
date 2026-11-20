"""Explicit Stacey domain extension; no automatic changes during running.
Padding is rounded to existing element widths. Only constant continuation at
flat interface ends is supported; the original cubic geometry is checked.
"""
from copy import deepcopy
import hashlib
import json
import math
import numpy as np
from model import elevation, interface_spline, validate


def signature(c):
    def canonical(v):
        if isinstance(v,dict):return {k:canonical(x) for k,x in v.items()}
        if isinstance(v,list):return [canonical(x) for x in v]
        if type(v) in (int,float):return float(v)
        return v
    return hashlib.sha256(json.dumps(canonical({k:v for k,v in c.items() if k!='boundary_domain'}),sort_keys=True).encode()).hexdigest()


def current_domain(c):
    d=c.get('boundary_domain')
    return d if d and d.get('configuration_sha256')==signature(c) else None


def extend_stacey(c,left=0.,right=0.,bottom=0.):
    validate(c)
    if c.get("domain_kind")=="free_plate" and bottom:
        raise ValueError("自由板不能向底部扩域：这会改变物理板厚，请只扩左右边界。")
    if current_domain(c):
        raise ValueError('当前模型已带扩域记录，请从原始模型重新设计扩域，避免重复追加。')
    if any(not math.isfinite(v) or v<0 for v in (left,right,bottom)) or not any((left,right,bottom)):
        raise ValueError('扩域距离须非负，且至少一侧大于零。')
    width=c['width_m']; nx=math.ceil(width/c['element_m']); dx=width/nx
    nz=c.get('elements_per_layer') or [math.ceil(r[0]/c['element_m']) for r in c['layers']]
    dz=c['layers'][-1][0]/nz[-1]
    left=math.ceil(left/dx-1e-10)*dx;right=math.ceil(right/dx-1e-10)*dx
    nb=max(0,math.ceil(bottom/dz-1e-10));bottom=nb*dz
    q=deepcopy(c);q.pop('boundary_domain',None)
    if bottom:
        probe=np.array(c['surface'] or [[0.,0.],[width,0.]])[:,0]
        thickness=elevation(c,probe,len(c['layers'])-1)-elevation(c,probe,len(c['layers']))
        if np.ptp(thickness)>1e-9:
            raise ValueError('底层横向厚度变化时不能自动加深并保持原网格，请显式设计底部过渡。')
    pts=np.array(c['surface'] or [[0.,0.],[width,0.]])
    xp=pts[:,0]; nleft=int(left>0)
    xx=np.r_[([0.] if left else []),xp+left,([width+left+right] if right else [])]
    boundaries=[];max_error=0.
    for k in range(len(c['layers'])+1):
        yy=elevation(c,xp,k)
        if (left and abs(yy[1]-yy[0])>1e-10) or (right and abs(yy[-1]-yy[-2])>1e-10):
            raise ValueError('自动扩域要求各地表/层界端部水平；请先显式设计外围过渡，不能外推斜坡。')
        zz=np.r_[([yy[0]] if left else []),yy,([yy[-1]] if right else [])]
        old=interface_spline(xp,yy);new=interface_spline(xx,zz)
        diff=new.c[:,nleft:nleft+len(xp)-1]-old.c
        # Difference is a cubic on each unchanged core interval: inspect extrema.
        for coeff,h in zip(diff.T,np.diff(xp)):
            roots=np.roots(np.polyder(coeff))
            ts=[0.,h]+[float(r.real) for r in roots if abs(r.imag)<1e-10 and 0<r.real<h]
            max_error=max(max_error,float(np.max(abs(np.polyval(coeff,ts)))))
        boundaries.append(zz)
    if max_error>1e-9:
        raise ValueError(f'扩域将改变原区域样条（最大{max_error:.3g} m），已拒绝；请显式设计更长水平过渡。')
    q.update(width_m=width+left+right,shot_x_m=c['shot_x_m']+left,
             first_x_m=c['first_x_m']+left,boundary='stacey',element_m=dx,
             elements_per_layer=list(nz))
    q['layers'][-1][0]+=bottom;q['elements_per_layer'][-1]+=nb
    q['surface']=np.column_stack((xx,boundaries[0])).tolist()
    depths=np.cumsum([r[0] for r in q['layers']])
    boundaries[-1]=boundaries[-1]-bottom
    q['interface_relief']=[(z-boundaries[0]+d).tolist() for z,d in zip(boundaries[1:],depths)]
    if c.get("domain_kind")=="free_plate":q.update(surface=[],interface_relief=[])
    validate(q)
    q['boundary_domain']=dict(schema=1,left_m=left,right_m=right,bottom_m=bottom,
        original_width_m=width,original_depth_m=sum(r[0] for r in c['layers']),
        coordinate_shift_x_m=left,original_configuration_sha256=signature(c),
        continuation='constant interface elevations and edge material; bottom extends last material',
        core_spline_max_error_m=max_error,validation='candidate; boundary-distance comparison required',
        configuration_sha256=signature(q))
    return q
