"""Single-rank native execution in isolated, persistent local run directories."""
from contextlib import nullcontext
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from custom_scene import digest
from model import ROOT


def prepare(runtime, prefix):
    if runtime.get('execution_target') != 'local':
        raise ValueError('新计算仅在本机运行，请配置 local_compute.json。')
    binary=Path(runtime['bin_dir']).expanduser().resolve()
    for name in ('xmeshfem2D','xspecfem2D'):
        exe=binary/name
        if not exe.is_file() or not os.access(exe,os.X_OK) or digest(exe)!=runtime.get('binary_sha256',{}).get(name):
            raise ValueError(f'本机 {name} 不可执行或与登记的 SHA256 不符：{exe}')
    parent=Path(runtime['run_parent']).expanduser().resolve()
    parent.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix=prefix,dir=parent))/'case'
    print('本机算例 '+str(work),flush=True)
    return work,binary


def execute(work, argv, log=None):
    env=dict(os.environ,OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1')
    with open(work/log,'w') if log else nullcontext() as stream:
        result=subprocess.run(argv,cwd=work,env=env,stdout=stream,stderr=subprocess.STDOUT)
    if result.returncode:
        detail=(work/log).read_text(errors='replace')[-1800:] if log else ''
        raise RuntimeError(f'{Path(argv[0]).name} 退出码 {result.returncode}；运行目录 {work}\n{detail}')


def script(work,name,*args):
    execute(work,[sys.executable,str(ROOT/name),*map(str,args)])


def deliver(source,destination):
    dest=Path(destination)
    dest.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=dest.parent) as tmp:
        staged=Path(tmp)/dest.name
        try:os.link(source,staged)
        except OSError:shutil.copyfile(source,staged)
        os.replace(staged,dest)
