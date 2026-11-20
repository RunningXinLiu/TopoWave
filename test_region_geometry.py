import copy
import json
from pathlib import Path
import unittest
import numpy as np
from region_geometry import build
from fluid_cavity import PRESETS

def base():
    m=json.loads(Path('examples/cavity/elastic_dry_boundary480.json').read_text())['model']
    return dict(schema='region_geometry_v1',model=m,regions=[dict(id='c1',shape=dict(kind='circle',center_m=[240.,-40.],radius_m=8.,patch_halfwidth_m=16.),interior=dict(kind='traction_free'))],surface_warp_depth_m=None)

class GeometryChecks(unittest.TestCase):
    def test_circle_wall_exact_vertices_and_area_converges(self):
        errors=[]
        for h in (2.,1.):
            c=base();c['model']['element_m']=h;p,cells,mats,absorb,free,qc=build(c)
            wall=[r for r in free if not np.all(p[np.array(r[2:])-1,1]==0)]
            ids=np.unique(np.array([r[2:] for r in wall])-1);xy=p[ids]-[240,-40]
            np.testing.assert_allclose(np.linalg.norm(xy,axis=1),8.,atol=1e-12)
            angles=np.arctan2(xy[:,1],xy[:,0]);xy=xy[np.argsort(angles)]
            area=.5*abs(np.sum(xy[:,0]*np.roll(xy[:,1],-1)-xy[:,1]*np.roll(xy[:,0],-1)))
            errors.append(abs(area-np.pi*64));self.assertGreater(qc['jacobian_min_m2'],0);self.assertEqual(qc['cavity_edges'],round(128/h))
        self.assertLess(errors[1],errors[0]/3)
    def test_mixed_multiple_regions_and_terrain_preserve_circle(self):
        c=base();c['regions'][0]['interior']=copy.deepcopy(PRESETS['water'])
        c['regions'].append(dict(id='r2',shape=dict(kind='rectangle',bounds_m=[280,296,-48,-32]),interior=dict(kind='traction_free')))
        plain=build(c);before=copy.deepcopy(c)
        c['model']['surface']=[[0,0],[120,0],[240,6],[360,0],[480,0]];c['surface_warp_depth_m']=20.
        terrain=build(c)
        self.assertGreater(terrain[-1]['jacobian_min_m2'],0);self.assertEqual(terrain[-1]['coupling_edges'],64)
        self.assertEqual(terrain[-1]['cavity_edges'],32)
        np.testing.assert_array_equal(plain[1],terrain[1]);np.testing.assert_array_equal(plain[2],terrain[2])
        mask=plain[0][:,1]<=-20;np.testing.assert_array_equal(plain[0][mask],terrain[0][mask])
        self.assertEqual(c['regions'],before['regions'])
    def test_overlap_ti_and_deformation_touching_cavity_rejected(self):
        c=base();c['regions'].append(copy.deepcopy(c['regions'][0]));c['regions'][1]['id']='c2'
        with self.assertRaises(ValueError):build(c)
        c=base();c['model']['material_model']='vti';c['model']['material_properties']=[dict(epsilon=.1,delta=.05,gamma=.1)]
        with self.assertRaisesRegex(ValueError,'TI空洞'):build(c)
        c=base();c['model']['surface']=[[0,0],[240,6],[480,0]];c['surface_warp_depth_m']=30
        with self.assertRaises(ValueError):build(c)
    def test_circle_radius_is_not_snapped_to_cartesian_grid(self):
        c=base();c['regions'][0]['shape']['radius_m']=8.3;p,cells,mats,absorb,free,qc=build(c)
        wall=[r for r in free if not np.all(p[np.array(r[2:])-1,1]==0)]
        ids=np.unique(np.array([r[2:] for r in wall])-1)
        np.testing.assert_allclose(np.linalg.norm(p[ids]-[240,-40],axis=1),8.3,atol=1e-12)

    def test_layered_valley_with_matched_water_and_solid_grids(self):
        root=Path('examples/layered_terrain')
        wet=json.loads((root/'valley_water.json').read_text())
        control=json.loads((root/'valley_solid.json').read_text())
        self.assertEqual(dict(wet,regions=[]),control)
        p,c,m,_,_,qc=build(wet);p0,c0,m0,_,_,qc0=build(control)
        np.testing.assert_array_equal(p,p0);np.testing.assert_array_equal(c,c0)
        center=p[c].mean(axis=1)
        expected=np.where(center[:,1]>-33,1,np.where(center[:,1]>-63,2,3))
        np.testing.assert_array_equal(m0,expected)
        x,z=center.T
        inside=(x>303)&(x<339)&(z>-27)&(z<-9)
        np.testing.assert_array_equal(m,np.where(inside,4,expected))
        self.assertEqual((qc['coupling_edges'],qc0['coupling_edges']),(36,0))
        wet['model']['interface_relief']=[]
        with self.assertRaisesRegex(ValueError,'水平'):build(wet)
