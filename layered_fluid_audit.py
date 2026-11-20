"""Audit actual three-material solid-fluid-solid mesh, not just its label."""
import numpy as np


def inspect(mesh, c):
    if c.get('physics')!='coupled' or c.get('domain_kind')!='solid_fluid_layers':
        raise ValueError('Requires the actual solid-fluid-solid model.')
    p=mesh.points; cells=mesh.cells[:,:4]; center=p[cells].mean(axis=1)
    top=-c['layers'][0][0]; bottom=top-c['layers'][1][0]
    expected=np.where(center[:,1]>top,1,np.where(center[:,1]>bottom,2,3))
    if not np.array_equal(mesh.materials,expected):raise ValueError('Native materials do not match all three actual layers.')
    edges=np.concatenate([cells[:,[i,(i+1)%4]] for i in range(4)])
    owners=np.tile(np.arange(len(cells)),4)
    edges.sort(axis=1); unique,inverse,counts=np.unique(edges,axis=0,return_inverse=True,return_counts=True)
    if np.any(counts>2):raise ValueError('Nonmanifold mesh.')
    interfaces={}
    for name,z,media in [('upper',top,{1,2}),('lower',bottom,{2,3})]:
        keep=np.all(np.isclose(p[unique,1],z,rtol=0,atol=1e-9),axis=1)
        ids=np.flatnonzero(keep)
        if not len(ids) or not np.all(counts[ids]==2):raise ValueError('Fluid interface is an exterior/free edge or is missing.')
        selected=np.isin(inverse,ids)
        # Every shared edge must have exactly the two required media.
        labels=np.bincount(inverse,weights=mesh.materials[owners],minlength=len(unique))
        squares=np.bincount(inverse,weights=mesh.materials[owners]**2,minlength=len(unique))
        if not (np.all(labels[ids]==sum(media)) and np.all(squares[ids]==sum(v*v for v in media))):raise ValueError('Wrong interface material pair.')
        xy=p[unique[ids]]
        if not np.isclose(xy[:,:,0].min(),0) or not np.isclose(xy[:,:,0].max(),c['width_m']):raise ValueError('Air layer does not span the whole domain.')
        interfaces[name]=dict(z_m=z,edges=len(ids),material_pair=sorted(media),internal_shared_edges=True)
    air=mesh.materials==2
    if not air.any() or not (mesh.materials==3).any():raise ValueError('Air or lower solid is missing.')
    return dict(state='native_three_material_topology_verified_not_accuracy',vtk_sha256=mesh.vtk_sha256,
        elements=len(cells),material_elements={str(i):int(np.sum(mesh.materials==i)) for i in (1,2,3)},
        interfaces=interfaces,air_spans_full_width=True,lower_solid_present=True,
        physical_scope='solid/fluid/solid, internal acoustic-elastic interfaces; not a free plate')
