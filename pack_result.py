"""Run on the solver host. Retain full native files on that host."""
import hashlib
import json
import re
import sys
from pathlib import Path

import numpy as np
from native_mesh import read_material_vtk


def preview_columns(xs, config):
    """Preserve native positions; avoid the fixed 601-column limit for thin plates."""
    if config.get('domain_kind')!='free_plate' and config.get('physics')!='coupled':
        return np.linspace(0,len(xs)-1,min(601,len(xs))).round().astype(int)
    # Display candidate: 8 intervals per shortest S wavelength at 2.5*f0.
    # If even the native grid is coarser, keep all its points, never interpolate.
    target=min(row[2] if row[2]>0 else row[1] for row in config['layers'])/(20*config['f0_hz'])
    indices=[0]
    while indices[-1]<len(xs)-1:
        end=int(np.searchsorted(xs,float(xs[indices[-1]])+target,side='right'))-1
        indices.append(min(len(xs)-1,max(indices[-1]+1,end)))
    return np.asarray(indices,dtype=int)


def snapshots(root, config, t0, acoustic):
    output = root / "OUTPUT_FILES"
    # This single-rank build can append unused buffer space after the true GLL nodes.
    log = (root/"solver.log").read_text()
    counts = re.findall(r"number of global nodes in (?:outer|inner) elements\s*=\s*(\d+)",log)
    if len(counts) != 2:
        raise ValueError("Cannot determine the single-rank mesh node count")
    n = sum(map(int,counts))
    grid = np.fromfile(str(output/"wavefield_grid_for_dumps.bin"), dtype="<f4").reshape(-1,2)
    nodes = grid[:n]
    if len(nodes)!=n or not np.isfinite(nodes).all():
        raise ValueError("Incomplete coordinate dump")
    order = np.lexsort((nodes[:,1],nodes[:,0]))
    xs, sizes = np.unique(nodes[:,0],return_counts=True)
    if len(xs)<2 or np.any(sizes!=sizes[0]) or sizes[0]<2:
        raise ValueError("Snapshot preview requires a structured internal mesh")
    ids = order.reshape(len(xs),int(sizes[0])).T
    if np.any(np.diff(nodes[ids,1],axis=0)<=0):
        raise ValueError("Wavefield grid contains duplicate or inverted nodes")
    iz = np.linspace(0,ids.shape[0]-1,min(241,ids.shape[0])).round().astype(int)
    if config.get('physics')=='coupled':
        # Keep every native vertical sample in the thin upper solid and fluid;
        # sample only the deeper solid for this display product.
        dense=np.flatnonzero(nodes[ids[:,0],1]>=-sum(row[0] for row in config['layers'][:2])-4*config['element_m'])
        lower=np.linspace(0,max(0,dense[0]-1),min(dense[0],max(2,241-len(dense)))).round().astype(int)
        iz=np.unique(np.r_[lower,dense])
    ix = preview_columns(xs,config)
    view = ids[np.ix_(iz,ix)]
    # The solver rounds NSTEP up to a multiple of the receiver output stride.
    actual = re.search(r"Number of time iterations[^\n]*\(NSTEP\)\s*=\s*(\d+)",log)
    if not actual:
        raise ValueError("Cannot determine the final solver step")
    last = int(actual[1])
    stride = config.get("snapshot_stride") or int(np.ceil(config["nstep"]/60))
    steps = sorted(set(range(stride,last+1,stride)) | {last} | ({5} if last>=5 else set()))
    if config.get('domain_kind')=='free_plate' and len(steps)*len(iz)*len(ix)>20_000_000:
        raise ValueError('Wavefield preview exceeds 20 million values; increase snapshot stride explicitly. No silent spatial undersampling.')
    frames = []
    for step in steps:
        prefix='solid_velocity' if config.get('physics')=='coupled' and not acoustic else 'wavefield'
        path = output/f"{prefix}{step:07d}_01.bin"
        channels = 1 if acoustic else 2
        if path.stat().st_size != grid.shape[0]*channels*4:
            raise ValueError(f"Incomplete wavefield: {path.name}")
        native = np.memmap(str(path),dtype="<f4",mode="r",shape=(len(grid),channels))
        frame = native[view,channels-1]
        if not np.isfinite(frame).all():
            raise ValueError(f"Nonfinite wavefield: {path.name}")
        frames.append(frame)
    return dict(field_x=xs[ix], field_z=nodes[view,1], field_values=np.stack(frames),
                field_step=steps, field_t=t0+(np.asarray(steps)-1)*config["dt_s"],
                field_sampling=json.dumps(dict(native_columns=len(xs),preview_columns=len(ix),
                    max_horizontal_gap_m=float(np.max(np.diff(xs[ix]))),
                    vertical_policy='all native upper-solid/fluid rows, sampled lower solid' if config.get('physics')=='coupled' else 'up to241 native rows',
                    policy='native points, minimum propagating wavelength/8 at 2.5*f0' if config.get('domain_kind')=='free_plate' or config.get('physics')=='coupled' else 'legacy up to601 columns',
                    interpolation='none during packing',scope='display sampling candidate, not solver validation')))


def collect(root, bindir, destination=None):
    output = root / "OUTPUT_FILES"
    c = json.loads((root/"model.json").read_text())
    acoustic = c.get("physics") == "acoustic"
    coords = []
    traces = []
    horizontal = []
    times = None
    pressure_record=None
    for row in (root/"DATA/STATIONS").read_text().splitlines():
        station, net, x, z, *_ = row.split()
        if net=='FL' and c.get('physics')=='coupled':
            if pressure_record is not None:raise ValueError('Multiple fluid probe records are not supported by this layered preview.')
            pt,pv=np.loadtxt(str(output/f'{net}.{station}.PRE.semp'),unpack=True)
            pressure_record=dict(pressure_t=pt,pressure_traces=pv,pressure_xy=np.array([float(x),float(z)]))
            continue
        suffix = "PRE.semp" if acoustic else "*Z.semv"
        files = list(output.glob(f"{net}.{station}.{suffix}"))
        if len(files) != 1:
            raise ValueError(f"{station}: expected one {suffix} trace, found {files}")
        t, v = np.loadtxt(str(files[0]), unpack=True)
        if len(t) != 1 + (c["nstep"]-1)//c["sample_stride"] or not np.all(np.isfinite(v)):
            raise ValueError("Incomplete or nonfinite trace: "+station)
        if times is not None and not np.array_equal(times, t):
            raise ValueError("Time samples differ: "+station)
        times = t
        traces.append(v)
        if not acoustic:
            xf=list(output.glob(f"{net}.{station}.*X.semv"))
            if len(xf)!=1:raise ValueError("缺少原生Vx记录")
            tx,vx=np.loadtxt(str(xf[0]),unpack=True)
            if not np.array_equal(tx,t) or not np.isfinite(vx).all():raise ValueError("Vx时轴或数值无效")
            horizontal.append(vx)
        coords.append((float(x),float(z)))
    if not np.allclose(np.diff(times), c["dt_s"]*c["sample_stride"]) or not np.any(traces):
        raise ValueError("Invalid sampling or silent gather")
    files = list((root/"DATA").iterdir()) + [bindir/"xmeshfem2D",bindir/"xspecfem2D"]
    hashes = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    meta = dict(model=c, component="pressure" if acoustic else "Vz", source_x=c["shot_x_m"], run=str(root),
                hashes=hashes, solver_seconds=float((root/"elapsed.txt").read_text()))
    log=(root/"solver.log").read_text()
    limits={}
    for key,pattern in [("max_cfl",r"Max CFL[^\n]*?=\s*"),("dt_limit_s",r"Maximum suggested time step\s*=\s*"),
                        ("resolved_hz",r"Maximum frequency resolved\s*=\s*")]:
        match=re.search(pattern+r"([0-9.Ee+\-]+)",log)
        if match:
            limits[key]=float(match[1])
    meta["solver_checks"]=limits
    mesh_qc=root/"mesh_qc.json"
    if mesh_qc.exists(): meta["mesh_qc"]=json.loads(mesh_qc.read_text())
    monitor=root/"boundary_monitor.json"
    if monitor.exists():
        meta["boundary_monitor"]=json.loads(monitor.read_text())
    print("实际网格检查",limits,"；单炮首末时刻 (s)",times[0],times[-1],flush=True)
    fields=snapshots(root,c,times[0],acoustic)
    meta['wavefield_sampling']=json.loads(fields.pop('field_sampling'))
    if c.get('physics')=='coupled':
        if pressure_record is None or not np.array_equal(pressure_record['pressure_t'],times) or not np.isfinite(pressure_record['pressure_traces']).all():
            raise ValueError('Missing/invalid actual fluid pressure record.')
        pressure=snapshots(root,c,times[0],True)
        for key in ('field_x','field_z','field_step','field_t'):np.testing.assert_array_equal(fields[key],pressure[key])
        fields['pressure_field_values']=pressure['field_values']
        fields.update(pressure_record)
        meta.update(coupled_field_units=dict(solid_velocity='m/s',fluid_pressure='Pa'),
            preview_note='实际固—流体—固三层。同次计算同步固体速度与流体压力，显示时按材料遮罩排除另一介质及共享界面；完整地表Vx/Vz保留。')
    np.savez_compressed(str(destination if destination is not None else root/"gather.npz"), t=times, x=np.array(coords)[:,0],
                        z=np.array(coords)[:,1], traces=np.asarray(traces).T,
                        metadata=json.dumps(meta), **({"vx":np.asarray(horizontal).T} if not acoustic else {}), **fields,
                        **read_material_vtk(output/"mesh_materials.vtk").arrays())


if __name__ == "__main__":
    if sys.argv[1] == "--mesh-only":
        mesh = read_material_vtk(Path("OUTPUT_FILES/mesh_materials.vtk"))
        np.savez_compressed("mesh.npz", **mesh.arrays(), model=Path("model.json").read_text(),
                            run=str(Path.cwd()), mesh_qc=Path("mesh_qc.json").read_text() if Path("mesh_qc.json").exists() else "{}")
    else:
        collect(Path.cwd(), Path(sys.argv[1]))
