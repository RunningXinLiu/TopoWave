"""Rebuild the local six-shot comparison from distributed native results."""
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.collections import PolyCollection
import numpy as np

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
sys.path.insert(0, str(ROOT))
from imaging import spectrum, array_center
from region_result import load, digest
from region_analysis import surface_gather
from region_comparison import check_pair
from region_geometry import build
from theory_reference import solve

folder = ROOT / 'examples/layered_terrain'
index = json.loads((folder / 'index.json').read_text())
rows = ['flat', 'hill', 'valley']
keys = [f'{terrain}_{fill}' for terrain in rows for fill in ('solid','water')]
data, gathers, spectra, theory, metrics = {}, {}, {}, {}, {}
for key in keys:
    path = folder / (key+'.npz')
    data[key] = a = load(path)
    g = gathers[key] = surface_gather(a)
    case = g.meta['region_case']
    qc = build(case)[-1]
    m = g.meta['model']
    for method in ('PS','FH'):
        spectra[key,method] = spectrum(g, **dict(index['options'],method=method))
    ss = spectra[key,'FH']
    ref = solve(m,array_center(g.x),ss['f'],max_mode=2)['reference']
    curves = ref['groups']['actual']['curves']
    assert curves and '0' in curves
    theory[key] = curves
    metrics[key] = dict(bundle_sha256=digest(path),case_sha256=g.meta['region_case_sha256'],
        waveform_shape=list(g.traces.shape),frame_count=len(a['field_t']),
        mesh_material_counts=dict(zip(map(str,np.unique(g.mesh.materials)),np.unique(g.mesh.materials,return_counts=True)[1].tolist())),
        scaled_jacobian_min=qc['scaled_jacobian_min'],coupling_edges=qc['coupling_edges'],
        pressure_peak_Pa=float(abs(a['pressure']).max()) if a['pressure'].size else None,
        mode_frequency_counts={i:len(p[0]) for i,p in curves.items()},
        window_covers_all_times=bool(np.min(ss['window_start'])>=g.t[0] and np.max(ss['window_end'])<=g.t[-1]))
    assert metrics[key]['window_covers_all_times']
    if key.endswith('water'):
        base = gathers[key.replace('water','solid')]
        check_pair(g,base)
        assert np.array_equal(g.mesh.points,base.mesh.points) and np.array_equal(g.mesh.cells,base.mesh.cells)
        metrics[key]['relative_full_vz_l2'] = float(np.linalg.norm(g.traces-base.traces)/np.linalg.norm(base.traces))

fig, axes = plt.subplots(3,2,figsize=(11,6.5),sharex=True,sharey=True,layout='constrained')
for ax,key in zip(axes.flat,keys):
    g=gathers[key];mesh=g.mesh
    palette=np.array(['#d8dbad','#9cb8a3','#547e79','#318dc1'])
    ax.add_collection(PolyCollection(mesh.points[mesh.cells[:,:4]],facecolors=palette[mesh.materials-1],edgecolors='none'))
    ax.plot(g.x,data[key]['z'],'.',color='#255b48',ms=2)
    ax.plot(99,0,'*',color='#b5481b',ms=9)
    ax.set(xlim=(0,480),ylim=(-138,25),aspect='equal',title=key.replace('_',' / '))
fig.supxlabel('Horizontal position (m)');fig.supylabel('Elevation (m)')
fig.savefig(OUT/'geometry.png',dpi=150);plt.close(fig)

for kind in ('record','PS','FH'):
    fig,axs=plt.subplots(3,2,figsize=(11,9),sharex=True,sharey=True,layout='constrained')
    if kind=='record':
        clip=max(np.percentile(abs(g.traces),99) for g in gathers.values())
    else:
        peak=max(spectra[k,kind]['power'][~spectra[k,kind]['alias']].max() for k in keys)
    for ax,key in zip(axs.flat,keys):
        g=gathers[key];s=spectra[key,'FH' if kind=='record' else kind]
        if kind=='record':
            im=ax.pcolormesh(g.x,g.t,g.traces,cmap='RdBu_r',vmin=-clip,vmax=clip,shading='nearest')
            ax.plot(g.x,s['window_start'],color='0.4',ls='--',lw=.7);ax.plot(g.x,s['window_end'],color='0.4',ls='--',lw=.7)
            ax.set_ylim(g.t[-1],g.t[0])
        else:
            values=np.ma.masked_array(10*np.log10(np.maximum(s['power']/peak,1e-30)),mask=s['alias'])
            im=ax.pcolormesh(s['f'],s['c'],values,cmap='magma',vmin=-40,vmax=0,shading='nearest')
        ax.set_title(key.replace('_',' / '))
    fig.supxlabel('Receiver x (m)' if kind=='record' else 'Frequency (Hz)')
    fig.supylabel('Time from source peak (s)' if kind=='record' else 'Phase velocity (m/s)')
    fig.colorbar(im,ax=list(axs.flat),label='Vz (m/s), common display clip' if kind=='record' else 'dB / common peak')
    fig.savefig(OUT/(kind+'.png'),dpi=125)
    if kind!='record':
        for key,ax in zip(keys,axs.flat):
            for f,c in theory[key].values():ax.plot(f,c,ls='--',color='cyan',lw=.8)
        fig.savefig(OUT/(kind+'_theory.png'),dpi=125)
    plt.close(fig)
metrics['code_sha256']={f:digest(ROOT/f) for f in ('region_geometry.py','region_geometry_result.py','region_case.py','imaging.py','fh_imaging.py','theory_reference.py')}
(OUT/'metrics.json').write_text(json.dumps(metrics,indent=2)+'\n')

from matplotlib.colors import Normalize
from matplotlib.patches import Rectangle
from region_geometry import surface_height
fig,axs=plt.subplots(3,2,figsize=(11,6),sharex=True,sharey=True,layout='constrained')
frame=np.argmin(abs(data[keys[0]]['field_t']-.8))
clip=max(np.percentile(abs(data[k]['solid_velocity'][frame,:,1]),99.2) for k in keys)
for key,ax in zip(keys,axs.flat):
    a=data[key];points=a['solid_points'];case=gathers[key].meta['region_case']
    im=ax.scatter(*points.T,c=a['solid_velocity'][frame,:,1],s=2,cmap='RdBu_r',norm=Normalize(-clip,clip),linewidths=0,rasterized=True)
    x=np.arange(0,481,3);ax.plot(x,surface_height(case,x),color='.3',lw=.6)
    for z in [-33,-63]:ax.axhline(z,color='.45',ls=':',lw=.6)
    if case['regions']:
        ax.add_patch(Rectangle((303,-27),36,18,facecolor='none',edgecolor='#1c6952',lw=1.4))
    ax.set(xlim=(45,435),ylim=(-105,22),aspect='equal',title=key.replace('_',' / '))
fig.supxlabel('Horizontal position (m)');fig.supylabel('Elevation (m)')
fig.colorbar(im,ax=list(axs.flat),label='Solid Vz (m/s), common display clip')
fig.suptitle(f"Wavefield at {a['field_t'][frame]:.3f} s from source peak")
fig.savefig(OUT/'wavefield.png',dpi=145);plt.close(fig)
