import unittest,numpy as np
from das_observation import horizontal_strain_rate as calc
class DAS(unittest.TestCase):
 def test_plane_wave_and_rigid_translation(self):
  x=np.arange(0.,41.);t=np.arange(0.,1.,.01);k=.3;omega=12.;v=np.sin(k*x[None,:]-omega*t[:,None]);centers=x[8:-8]
  for L in [4.,8.,16.]:
   got=calc(t,x,v,centers,L)['strain_rate'];expected=2*np.sin(k*L/2)/L*np.cos(k*centers[None,:]-omega*t[:,None]);np.testing.assert_allclose(got,expected,atol=1e-14)
  np.testing.assert_array_equal(calc(t,x,np.ones_like(v),centers,8.)['strain_rate'],0)
 def test_missing_endpoint_rejected(self):
  with self.assertRaises(ValueError):calc(np.arange(3.),np.arange(5.),np.zeros((3,5)),np.array([2.]),5.)
if __name__=='__main__':unittest.main()
