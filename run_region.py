"""Run an explicit rectangular-region case; retain raw results in an isolated local directory."""
import argparse,hashlib,json,re,subprocess,os
from pathlib import Path
from tempfile import TemporaryDirectory
from region_case import write,validate
from runtime_config import read_compute_runtime
from model import ROOT
from materials import attenuating

def verify_serial_input(par_file):
    """The released region runner admits one process only; never rewrite input."""
    values=re.findall(r'(?m)^\s*NPROC\s*=\s*([^#\n]+)',Path(par_file).read_text())
    if len(values)!=1 or values[0].strip()!='1':
        raise ValueError('当前区域正演仅验收串行 NPROC=1；导出输入请求了未准入的并行配置，未提交计算。')

def select_backend(case,runtime):
    if case.get('schema')=='region_geometry_v1':
        if attenuating(case['model']):raise ValueError('几何扩展当前不接入Q；不得继承单矩形Q证据。')
        backend=runtime.get('geometry_cavity_backend',{})
        if backend.get('capability')!='iso_geometry_double_dual_gll5_v1':raise ValueError('多区域几何要求单独登记的5点GLL双精度双场后端。')
        return dict(runtime,bin_dir=backend['bin_dir'],binary_sha256=backend['binary_sha256'],region_capability=backend['capability'])
    if attenuating(case['model']):
        if case['interior']['kind']=='fluid':
            backend=runtime.get('q_fluid_cavity_backend',{})
            if backend.get('capability')!='iso_q_fluid_double_dual_v1':raise ValueError('Q声固区域要求单独登记的双精度双场后端；不继承无损流体或干洞后端配置。')
            return dict(runtime,bin_dir=backend['bin_dir'],binary_sha256=backend['binary_sha256'],region_capability=backend['capability'])
        backend=runtime.get('q_cavity_backend',{})
        if backend.get('capability')!='iso_q_dry_double_v1':raise ValueError('Q空洞试验要求显式登记的双精度后端；不自动选择其他后端。')
        return dict(runtime,bin_dir=backend['bin_dir'],binary_sha256=backend['binary_sha256'],region_capability=backend['capability'])
    if case['interior']['kind']=='fluid':
        backend=runtime.get('fluid_cavity_backend',{})
        if backend.get('capability') not in ('acoustic_elastic_double_v1','acoustic_elastic_double_dual_v1'):
            raise ValueError('流体试验要求已登记的双精度声固后端；不回退到原单精度后端。')
        return dict(runtime,bin_dir=backend['bin_dir'],binary_sha256=backend['binary_sha256'],region_capability=backend['capability'])
    backend=runtime.get('dry_cavity_backend',{})
    if backend.get('capability')!='iso_dry_double_v1':raise ValueError('无牵引空洞运行要求显式登记的双精度后端；不自动回退到主场景后端。')
    return dict(runtime,bin_dir=backend['bin_dir'],binary_sha256=backend['binary_sha256'],region_capability=backend['capability'])


def run(case,reference,runtime,mesh_only=False):
    validate(case)
    runtime=select_backend(case,runtime)
    dest=Path(reference)
    if dest.exists():raise ValueError('运行引用文件已存在，不能覆盖。')
    from local_compute import prepare,execute,script
    from custom_scene import atomic_json,digest
    work,binary=prepare(runtime,'region_')
    qc=write(case,work)
    verify_serial_input(work/'DATA/Par_file')
    record=dict(schema='region_run_v1',case=case,local_run=str(work),binary_sha256=runtime['binary_sha256'],
        input_sha256=qc['input_sha256'],backend_capability=runtime['region_capability'],
        execution=dict(target='local',mode='serial',nproc=1,omp_num_threads=1),status='running')
    atomic_json(dest,record)
    try:
        (work/'OUTPUT_FILES').mkdir()
        execute(work,[binary/'xmeshfem2D'],'mesh.log')
        if mesh_only:
            preview=work/'mesh_preview.npz'
            script(work,'region_mesh_result.py','.',preview)
        else:
            execute(work,[binary/'xspecfem2D'],'solver.log')
            preview=work/'region_preview.npz'
            dual=runtime['region_capability'] in ('acoustic_elastic_double_dual_v1','iso_q_fluid_double_dual_v1')
            script(work,'region_result.py',*(('--require-dual',) if dual else ()),'.',preview)
        record.update(status='mesh_completed_pending_qc' if mesh_only else 'solver_completed_pending_qc',
                      local_preview=str(preview),preview_sha256=digest(preview))
    except Exception:
        record['status']='failed'
        raise
    finally:
        atomic_json(dest,record)
    print('本机完成 '+str(preview),flush=True)


def fetch(reference,destination,mesh_only=False):
    from region_result import load,digest,case_hash
    record=json.loads(Path(reference).read_text());dest=Path(destination)
    if record.get('schema')!='region_run_v1' or dest.exists():raise ValueError('无效运行引用或目标文件已存在。')
    if record.get('execution',{}).get('target')=='local':
        from local_compute import deliver
        expected='mesh_preview.npz' if mesh_only else 'region_preview.npz'
        path=Path(record['local_run'])/expected
        if str(path)!=record.get('local_preview') or digest(path)!=record.get('preview_sha256'):
            raise ValueError('本机运行结果路径或 SHA256 不匹配。')
        deliver(path,dest)
        return dest
    host=record['ssh_host'];remote=record['remote_run'];preview=record.get('server_preview')
    filename='/mesh_preview.npz' if mesh_only else '/region_preview.npz'
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*',host) or not re.fullmatch(r'/[a-zA-Z0-9_./-]+',remote) or preview!=remote+filename:raise ValueError('无效预览位置；请先在服务器生成预览包。')
    ssh=['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10',host]
    size=int(subprocess.check_output(ssh+[f'stat -c %s {preview}'],text=True))
    if not 0<size<=64_000_000:raise ValueError('只允许取回64MB以内的预览包。')
    expected=subprocess.check_output(ssh+[f'sha256sum {preview}'],text=True).split()[0]
    dest.parent.mkdir(parents=True,exist_ok=True)
    with TemporaryDirectory(dir=dest.parent) as temp:
        got=Path(temp)/'preview.npz'
        subprocess.run(['scp','-q','-o','BatchMode=yes','-o','ConnectTimeout=10',f'{host}:{preview}',str(got)],check=True)
        if digest(got)!=expected:raise ValueError('下载预览哈希不匹配。')
        if mesh_only:
            from region_mesh_result import load as load_mesh
            load_mesh(got,record)
        else:
            a=load(got);meta=json.loads(str(a['metadata']))
            if meta['case_sha256']!=case_hash(record['case']) or meta['input_sha256']!=record['input_sha256']:raise ValueError('预览输入与运行引用不符。')
            if record.get('backend_capability') in ('acoustic_elastic_double_dual_v1','iso_q_fluid_double_dual_v1') and meta['schema']!='region_result_v2':raise ValueError('双场运行引用缺少同步固体速度。')
            if record.get('backend_capability')=='iso_geometry_double_dual_gll5_v1' and meta['schema']!='region_geometry_result_v1':raise ValueError('几何运行引用缺少对应版本结果。')
        # Exclusive destination creation prevents overwriting an existing preview.
        with dest.open('xb') as output,got.open('rb') as source:
            import shutil
            shutil.copyfileobj(source,output)
    return dest

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--fetch',action='store_true',help='取回已有运行的小型预览；两个参数为运行引用、目标NPZ');p.add_argument('--fetch-mesh',action='store_true');p.add_argument('--mesh-only',action='store_true');p.add_argument('--preview');p.add_argument('case');p.add_argument('run_reference');a=p.parse_args()
    if a.fetch or a.fetch_mesh:fetch(a.case,a.run_reference,a.fetch_mesh)
    else:
        run(json.loads(Path(a.case).read_text()),a.run_reference,read_compute_runtime(ROOT),a.mesh_only)
        if a.preview:fetch(a.run_reference,a.preview,a.mesh_only)
