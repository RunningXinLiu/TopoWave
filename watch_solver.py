"""Single-rank solver wrapper: inspect complete native frames, never alter signals."""
import json
import math
import re
import subprocess
import sys
from pathlib import Path
import numpy as np


def growth_test(rows, duration):
    late=[r for r in rows if r['elapsed_s']>=.75*duration]
    if len(late)<5:
        return {'state':'insufficient_frames'}
    result={}
    for region in late[0]['regions']:
        t=np.array([r['elapsed_s'] for r in late]);y=np.array([r['regions'][region] for r in late])
        if np.any(y<=0):continue
        slope=float(np.polyfit(t,np.log(y),1)[0]);ratio=float(y[-1]/y[0])
        result[region]={'log_slope_s_inv':slope,'end_start_ratio':ratio,'alert':slope>1 and ratio>2}
    return {'state':'growth_alert' if any(r['alert'] for r in result.values()) else 'no_alert_not_validation',
            'regions':result,'scope':'last quarter; nodal velocity L2, not energy; arrivals can trigger alerts'}


class Monitor:
    def __init__(self,root):
        self.root=Path(root);self.c=json.loads((self.root/'model.json').read_text());self.rows=[];self.seen=set()
        self.enabled=self.c.get('boundary')=='cpml' and self.c.get('physics','elastic')=='elastic'
        self.grid=None;self.masks=None

    def inspect(self):
        if not self.enabled:return {'state':'not_applicable'}
        out=self.root/'OUTPUT_FILES'
        if self.grid is None:
            log=(self.root/'solver.log').read_text(errors='replace')
            n=re.findall(r'number of global nodes in (?:outer|inner) elements\s*=\s*(\d+)',log)
            grid=out/'wavefield_grid_for_dumps.bin'
            if len(n)!=2 or not grid.exists():return {'state':'initializing'}
            self.n=sum(map(int,n));self.bytes=grid.stat().st_size
            if self.bytes<self.n*8 or self.bytes%8:return {'state':'initializing'}
            self.grid=np.fromfile(grid,dtype='<f4',count=self.n*2).reshape(-1,2)
            x,z=self.grid.T;w=self.c['width_m'];nx=math.ceil(w/self.c['element_m']);pad=self.c['pml_elements']*w/nx
            # Native vertical columns give the actual surface, including topography.
            ux,inv=np.unique(x,return_inverse=True);top=np.full(len(ux),-np.inf);np.maximum.at(top,inv,z)
            shallow=z>=top[inv]-max(2*self.c['layers'][0][0],4*self.c['element_m'])
            self.masks={'left_shallow':(x>0)&(x<=pad/2)&shallow,
                        'right_shallow':(x<w)&(x>=w-pad/2)&shallow,'full_domain':np.ones(len(x),bool)}
        files=sorted(out.glob('wavefield0*_01.bin'))
        for p in files:
            if p.name in self.seen or p.stat().st_size!=self.bytes:continue
            step=int(p.name[9:16])
            # Writer emits this message after closing the file; skip in-progress frames.
            log=(self.root/'solver.log').read_text(errors='replace')
            marker=f'Dumping the wave field to a file for time step'
            matches=list(re.finditer(marker+r'\s+'+str(step)+r'\s*\n',log))
            if not matches or 'Wave field dumped' not in log[matches[-1].end():]:continue
            v=np.fromfile(p,dtype='<f4',count=self.n*2).reshape(-1,2).astype(float)
            if not np.isfinite(v).all():return {'state':'nonfinite_alert','file':p.name}
            self.rows.append({'step':step,'elapsed_s':(step-1)*self.c['dt_s'],
                              'regions':{k:float(np.linalg.norm(v[m])) for k,m in self.masks.items() if m.any()}})
            self.seen.add(p.name)
        r=growth_test(self.rows,(self.c['nstep']-1)*self.c['dt_s']);r['frames']=self.rows
        return r


def main():
    m=Monitor(Path.cwd());last=None
    with open('solver.log','w') as log:
        p=subprocess.Popen([sys.argv[1]],stdout=log,stderr=subprocess.STDOUT)
        while True:
            try:rc=p.wait(timeout=10)
            except subprocess.TimeoutExpired:rc=None
            try:r=m.inspect()
            except Exception as exc:r={'state':'monitor_error','error':str(exc)}
            r['solver_running']=rc is None
            tmp=Path('boundary_monitor.tmp');tmp.write_text(json.dumps(r,indent=2));tmp.replace('boundary_monitor.json')
            if r['state']!=last and r['state'] in ('growth_alert','nonfinite_alert','monitor_error'):
                print('边界监测提示：'+r['state']+'；保留完整结果，需要检查原始波场。',flush=True)
            last=r['state']
            if rc is not None:
                print('边界监测：'+r['state']+'（不代表科学验收通过）',flush=True)
                return rc


if __name__=='__main__':sys.exit(main())
