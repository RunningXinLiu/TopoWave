import unittest,numpy as np
from scipy.special import hankel2
from fh_imaging import fh_power
class FH(unittest.TestCase):
 def test_outgoing_hankel_direction_and_velocity(self):
  r=np.arange(200.,1001.,2.);f=np.array([15.]);c=np.arange(270.,331.);u=hankel2(0,2*np.pi*15*r/300)[None,:]
  p=fh_power(u,f,c,r,np.ones(len(r))/len(r));reverse=fh_power(u.conj(),f,c,r,np.ones(len(r))/len(r))
  self.assertLessEqual(abs(c[p[:,0].argmax()]-300),2)
  self.assertGreater(p.max(),100*reverse.max())
if __name__=='__main__':unittest.main()
