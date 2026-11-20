"""Native Q4/Q9 geometry screening; no automatic change of physical inputs."""
import json
from pathlib import Path
import numpy as np
from native_mesh import read_material_vtk
from materials import speed_bounds, attenuating

GLL=np.array([-1.,-np.sqrt(3/7),0.,np.sqrt(3/7),1.])


def shape_derivatives(width, r, s):
    if width==4:
        return (np.array([-(1-s),1-s,1+s,-(1+s)])/4,
                np.array([-(1-r),-(1+r),1+r,1-r])/4)
    # VTK biquadratic quad: four corners, four edge middles, center.
    pairs=[(0,0),(2,0),(2,2),(0,2),(1,0),(2,1),(1,2),(0,1),(1,1)]
    a=np.array([r*(r-1)/2,1-r*r,r*(r+1)/2]);da=np.array([r-.5,-2*r,r+.5])
    b=np.array([s*(s-1)/2,1-s*s,s*(s+1)/2]);db=np.array([s-.5,-2*s,s+.5])
    return np.array([da[i]*b[j] for i,j in pairs]),np.array([a[i]*db[j] for i,j in pairs])


def inspect_mesh(mesh, config=None):
    """Check all elements at GLL plus interleaved reference positions (9x9).

    Positive sampled Jacobians do not certify unsampled points. CFL/resolution
    are estimates from mapping singular values, not solver stability guarantees.
    """
    n=len(mesh.cells);lo=np.full(n,np.inf);sj=lo.copy();cond=np.zeros(n)
    hmin=lo.copy();hmax=np.zeros(n)
    locations=np.unique(np.r_[GLL,(GLL[:-1]+GLL[1:])/2])
    for start in range(0,n,4096):
        stop=min(start+4096,n);p=mesh.points[mesh.cells[start:stop]]
        for r in locations:
            for s in locations:
                dr,ds=shape_derivatives(p.shape[1],r,s)
                u=np.einsum('eni,n->ei',p,dr);v=np.einsum('eni,n->ei',p,ds)
                det=u[:,0]*v[:,1]-u[:,1]*v[:,0]
                norm=np.linalg.norm(u,axis=1)*np.linalg.norm(v,axis=1)
                scaled=np.divide(det,norm,out=np.zeros_like(det),where=norm>0)
                # Squared singular values of [u v], 2x2 symmetric Gram matrix.
                aa=(u*u).sum(axis=1);bb=(v*v).sum(axis=1);ab=(u*v).sum(axis=1)
                disc=np.sqrt(np.maximum(0,(aa-bb)**2+4*ab*ab))
                hi=np.sqrt(np.maximum(0,(aa+bb+disc)/2))
                small=np.divide(abs(det),hi,out=np.zeros_like(det),where=hi>0)
                ratio=np.divide(hi,small,out=np.full_like(hi,np.inf),where=small>0)
                sl=slice(start,stop)
                lo[sl]=np.minimum(lo[sl],det);sj[sl]=np.minimum(sj[sl],scaled)
                cond[sl]=np.maximum(cond[sl],ratio)
                hmin[sl]=np.minimum(hmin[sl],2*small);hmax[sl]=np.maximum(hmax[sl],2*hi)
    bad=(lo<=0)|~np.isfinite(lo)|(hmin<=0)
    warn=(sj<.3)|(cond>10)
    result=dict(schema='native_mesh_qc_v1',vtk_sha256=mesh.vtk_sha256,elements=n,
        status='invalid_geometry' if bad.any() else 'geometry_warning' if warn.any() else 'sampled_geometry_ok',
        sampling='Q4/Q9 mapping, 9x9 reference points including all 5x5 GLL points',
        min_jacobian_m2=float(lo.min()),min_scaled_jacobian=float(sj.min()),
        max_mapping_condition=float(cond.max()) if np.isfinite(cond).all() else None,
        invalid_count=int(bad.sum()),warning_count=int(warn.sum()),
        invalid_element_ids_1based=(np.flatnonzero(bad)+1).tolist(),
        warning_element_ids_1based=(np.flatnonzero(warn & ~bad)+1).tolist(),
        scope='Geometry sampling only; no stability, absorption or waveform-accuracy certificate.',warnings=[])
    if config and not bad.any():
        fast,slow=speed_bounds(config);ids=mesh.materials-1
        if (ids<0).any() or (ids>=len(fast)).any():raise ValueError('网格材料编号与当前模型不一致。')
        cfl=config['dt_s']*fast[ids]/(.1726731646460114*hmin)
        ppw=4*slow[ids]/(2.5*config['f0_hz']*hmax)
        result.update(estimated_max_cfl=float(cfl.max()),min_average_gll_intervals_per_wavelength=float(ppw.min()),design_fmax_hz=2.5*config['f0_hz'])
        if cfl.max()>=.5:result['warnings'].append('预计 CFL≥0.5；检查实际求解器时间步限制，考虑减小dt。')
        if ppw.min()<5:result['warnings'].append('设计频带最短波长不足5个平均GLL间距，考虑细化。')
        if attenuating(config):result['warnings'].append('衰减材料速度会频散，此处速度估计不含SLS的未松弛速度修正；以求解器实测限制为准。')
        if config.get('boundary')=='cpml':result['warnings'].append('此检查未认证PML材料延拓、吸收和稳定性；需结合运行监测。')
    return result


def draw_quality(ax, mesh, report):
    from matplotlib.collections import PolyCollection
    colors=np.full(len(mesh.cells),'#b8d7cb',dtype=object)
    colors[np.array(report['warning_element_ids_1based'],int)-1]='#f1b44c'
    colors[np.array(report['invalid_element_ids_1based'],int)-1]='#cc364b'
    ax.add_collection(PolyCollection(mesh.points[mesh.cells[:,:4]],facecolors=colors,edgecolors='#35584d',linewidths=.3))
    from native_mesh import draw_mesh
    draw_mesh(ax,mesh)
    ax.autoscale();ax.set_aspect('equal',adjustable='box')
    ax.set(xlabel='x (m)',ylabel='z (m)',title='Native mesh QC: green sampled OK / orange distorted / red invalid')


def main():
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--check',action='store_true');args=parser.parse_args()
    mesh=read_material_vtk(Path('OUTPUT_FILES/mesh_materials.vtk'))
    c=json.loads(Path('model.json').read_text());report=inspect_mesh(mesh,c)
    Path('mesh_qc.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False))
    print('实际网格 QC:',report['status'],'最小scaled Jacobian=',report['min_scaled_jacobian'],flush=True)
    if args.check and report['invalid_count']:raise SystemExit('无效实际网格：停止进入求解器；请修改地形/层界或网格后重新生成。')

if __name__=='__main__':main()
