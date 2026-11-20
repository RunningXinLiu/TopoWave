"""Explicit layer material adapters for the fixed 2-D P-SV solver (SI units)."""
import math
import numpy as np


def kind(c):
    return c.get('material_model', 'isotropic')


def attenuating(c):
    return c.get('attenuation', {}).get('enabled', False)


def sls_weights(q,n):
    """Fixed native USE_SOLVOPT=false grid; reject negative SLS branches."""
    if q==9999:return np.zeros(n)
    p=100.**(np.arange(n,dtype=np.float32)/np.float32(n-1)).astype(float)
    w=100.**(np.arange(2*n-1,dtype=np.float32)/np.float32(2*n-2)).astype(float)
    A=w[:,None]*(p[None,:]-w[:,None]/q)/(p[None,:]**2+w[:,None]**2)
    return np.linalg.lstsq(A,np.full(len(w),1/q),rcond=None)[0]


def ti_stiffness(layer, p, orientation='vti'):
    """3-D engineering-Voigt C in x,y,z coordinates; HTI axis is global x."""
    _, vp, vs, rho = layer
    eps, delta, gamma = (float(p[k]) for k in ('epsilon', 'delta', 'gamma'))
    c33, c44 = rho*vp*vp, rho*vs*vs
    c11, c66 = c33*(1+2*eps), c44*(1+2*gamma)
    rad = (c33-c44)**2 + 2*delta*c33*(c33-c44)
    if not math.isfinite(rad) or rad < 0:
        raise ValueError('TI 的 delta 使 C13 根号内为负或非有限。')
    c13 = math.sqrt(rad)-c44
    c12 = c11-2*c66
    C = np.array([[c11,c12,c13,0,0,0],[c12,c11,c13,0,0,0],
                  [c13,c13,c33,0,0,0],[0,0,0,c44,0,0],
                  [0,0,0,0,c44,0],[0,0,0,0,0,c66]],float)
    if not np.isfinite(C).all() or np.linalg.eigvalsh(C).min() <= 0:
        raise ValueError('TI 三维刚度矩阵非正定，请检查 Vp0、Vs0、密度及 epsilon/delta/gamma。')
    if orientation == 'hti':
        # Permutation x<->z; no arbitrary azimuth and no P-SV/SH coupling.
        order = [2,1,0,5,4,3]
        C = C[np.ix_(order,order)]
    elif orientation != 'vti':
        raise ValueError('只支持垂直轴 VTI 或沿剖面 x 轴的 HTI。')
    return C


def validate_materials(c):
    mode = kind(c)
    if mode not in ('isotropic','vti','hti'):
        raise ValueError('当前材料入口支持 isotropic、vti、hti；空洞需要外部带孔网格，不能用零速度代替。')
    att = c.get('attenuation', {})
    if not isinstance(att,dict) or type(att.get('enabled',False)) is not bool:
        raise ValueError('attenuation.enabled 必须为布尔值。')
    props = c.get('material_properties', [])
    active = mode != 'isotropic' or attenuating(c)
    if active and (not isinstance(props,list) or len(props)!=len(c['layers'])):
        raise ValueError('每层需要一组对应的材料附加参数。')
    if c.get('physics','elastic') != 'elastic' and active:
        raise ValueError('新增衰减/TI 材料目前只接入弹性 P-SV，不支持声学混合。')
    if mode != 'isotropic' and c.get('boundary')=='cpml':
        raise ValueError('当前固定后端的 P-SV CPML 应力更新使用各向同性 Lamé 参数，TI+CPML 未实现；请显式选择 Stacey 做有限范围试验。不会自动替换边界。')
    if mode != 'isotropic' and attenuating(c):
        raise ValueError('当前后端不支持各向异性与材料衰减组合。')
    if attenuating(c):
        if type(att.get('n_sls')) is not int or not 3<=att['n_sls']<=8:
            raise ValueError('N_SLS 需为 3–8 的整数。')
        if att.get('velocity_reference') not in ('source_f0','unrelaxed'):
            raise ValueError('需明确速度是源主频处值 source_f0，还是无限频率值 unrelaxed。')
        for p in props:
            for k in ('qkappa','qmu'):
                v=p.get(k) if isinstance(p,dict) else None
                if type(v) not in (int,float) or not math.isfinite(v) or not (0<v<9998.999 or v==9999):
                    raise ValueError('每层 Qκ、Qμ 必须在 (0,9999]；9999 是后端无衰减哨兵值，Qκ 不等于 Qp。')
                if np.any(sls_weights(v,att['n_sls'])<0):
                    raise ValueError(f"{k}={v:g}、N_SLS={att['n_sls']} 在当前后端产生负SLS支路；该组合不受支持，请显式修改参数。不会自动改Q或SLS数。")
    if attenuating(c) and c.get('boundary')=='cpml':
        for p in props:
            if (p['qkappa']==9999)!=(p['qmu']==9999):
                raise ValueError('当前 CPML 黏弹性分支要求 Qκ/Qμ 同时有损或同时为9999；单独关闭一种损耗尚未接入。')
    if mode != 'isotropic':
        for layer,p in zip(c['layers'],props):
            if not isinstance(p,dict) or any(type(p.get(k)) not in (int,float) or not math.isfinite(p[k]) for k in ('epsilon','delta','gamma')):
                raise ValueError('每层 TI 需有限的 epsilon、delta、gamma。')
            ti_stiffness(layer,p,mode)


def material_rows(c):
    rows=[]
    for i, layer in enumerate(np.asarray(c['layers']),1):
        _,vp,vs,rho=layer
        if kind(c)=='isotropic':
            q=c['material_properties'][i-1] if attenuating(c) else dict(qkappa=9999,qmu=9999)
            rows.append(f"{i} 1 {rho} {vp} {vs} 0 0 {q['qkappa']} {q['qmu']} 0 0 0 0 0 0")
        else:
            C=ti_stiffness(layer,c['material_properties'][i-1],kind(c))
            vals=[rho,C[0,0],C[0,2],0,C[2,2],0,C[4,4],C[0,1],C[1,2],0,0,9999,9999]
            rows.append(f'{i} 2 '+ ' '.join(format(float(v),'.17g') for v in vals))
    return rows


def speed_bounds(c):
    """Directional sampled qP/qSV bounds, not an analytic global stability bound."""
    layers=np.asarray(c['layers'])
    if kind(c)=='isotropic':
        return layers[:,1], np.where(layers[:,2]==0,layers[:,1],layers[:,2]) if c.get('physics') in ('acoustic','coupled') else layers[:,2]
    fast=[];slow=[]
    a=np.linspace(0,np.pi,721);nx,nz=np.sin(a),np.cos(a)
    for layer,p in zip(layers,c['material_properties']):
        C=ti_stiffness(layer,p,kind(c));rho=layer[3]
        G=np.zeros((len(a),2,2))
        G[:,0,0]=(C[0,0]*nx*nx+C[4,4]*nz*nz)/rho
        G[:,1,1]=(C[4,4]*nx*nx+C[2,2]*nz*nz)/rho
        G[:,0,1]=G[:,1,0]=(C[0,2]+C[4,4])*nx*nz/rho
        v=np.sqrt(np.linalg.eigvalsh(G));fast.append(v.max());slow.append(v.min())
    return np.asarray(fast),np.asarray(slow)


def require_isotropic_theory(c):
    if c.get('physics')=='coupled':raise ValueError('埋藏流体层需要实际声弹耦合理论；不调用纯固体Rayleigh或自由板替代。')
    if kind(c)!='isotropic' or attenuating(c):
        raise ValueError('当前一维理论/群速估算只支持无衰减各向同性；此材料不绘制替代曲线。PS/FH仍可使用手选面波窗。')
