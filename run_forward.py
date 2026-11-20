"""Run an isolated single-rank SPECFEM2D shot and return its receiver gather."""
import argparse
import json
import math
import sys

from model import ROOT, read_model, snapshot_interval, write_case, validate
from runtime_config import read_compute_runtime


def check_desktop_size(config,mesh_only=False):
    stride=config["sample_stride"]
    actual=math.ceil(config["nstep"]/stride)*stride
    frames = actual//snapshot_interval(config)+2
    nx = math.ceil(config["width_m"]/config["element_m"])
    nz = sum(config.get("elements_per_layer") or [math.ceil(row[0]/config["element_m"]) for row in config["layers"]])
    components = {"acoustic": 1, "coupled": 3}.get(config.get("physics"), 2)
    if mesh_only and nx*nz > 350_000:
        raise ValueError("桌面分层网格预览上限为 35 万个单元，可导出 DATA 在服务器划分更大的网格。")
    if not mesh_only and (frames > 245 or (frames*components+2)*25*nx*nz*4 > 2_000_000_000):
        raise ValueError("本桌面任务的原始快照过多，请增大快照间隔，或导出 DATA 在服务器运行。")
    samples = (1+(config["nstep"]-1)//config["sample_stride"])*config["receivers"]
    if not mesh_only and actual//stride<8:
        raise ValueError("单炮不足 8 个接收采样点，请延长传播时间或减小输出步长。")
    if not mesh_only and samples>3_000_000:
        raise ValueError("本桌面预览最多传回 300 万个振幅值。请调整输出采样或道数，较大算例可导出 DATA 在服务器运行。")


def select_backend(config, runtime):
    if config.get('physics')=='coupled':
        candidate=runtime.get('layered_fluid_backend',{})
        if candidate.get('capability')!='solid_fluid_solid_double_dual_gll5_v1' or not candidate.get('binary_sha256'):
            raise ValueError('固—流体—固模型需要显式 layered_fluid_backend 双精度声弹耦合双场后端。')
        return dict(runtime,bin_dir=candidate['bin_dir'],binary_sha256=candidate['binary_sha256'])
    if config.get('domain_kind')=='free_plate':
        candidate=runtime.get('free_plate_backend',{})
        if candidate.get('capability')!='iso_free_plate_double_gll5_v1' or not candidate.get('binary_sha256'):
            raise ValueError('自由板需要显式登记的双精度5点GLL后端 free_plate_backend，不回退普通半空间配置。')
        return dict(runtime,bin_dir=candidate['bin_dir'],binary_sha256=candidate['binary_sha256'])
    if config.get('material_model') in ('vti','hti'):
        candidate=runtime.get('ti_layered_backend')
        if not candidate or candidate.get('capability')!='ti_layered_double_v1' or not candidate.get('binary_sha256'):
            raise ValueError('TI分层正演需要已登记的双精度后端 ti_layered_backend；不自动回退旧构建。')
        return dict(runtime,bin_dir=candidate['bin_dir'],binary_sha256=candidate['binary_sha256'])
    if config.get("boundary")=="cpml" and config.get("cpml_alpha_factor",1.)!=1:
        candidate=runtime.get("cpml_alpha_backend")
        if not candidate or candidate.get("capability")!="cpml_alpha_max_v1" or not candidate.get("binary_sha256"):
            raise ValueError("当前后端不支持显式CPML频移；请配置已登记的候选后端，设置不会被忽略。")
        return dict(runtime,bin_dir=candidate["bin_dir"],binary_sha256=candidate["binary_sha256"])
    return runtime


def run(config, destination, runtime, mesh_only=False):
    validate(config)
    runtime=select_backend(config,runtime)
    check_desktop_size(config,mesh_only)
    from local_compute import prepare,execute,script,deliver
    from time import monotonic
    work,binary=prepare(runtime,'shot_')
    write_case(config,work)
    (work/'OUTPUT_FILES').mkdir()
    execute(work,[binary/'xmeshfem2D'],'mesh.log')
    script(work,'mesh_quality.py',*(() if mesh_only else ('--check',)))
    if mesh_only:
        script(work,'pack_result.py','--mesh-only')
        result=work/'mesh.npz'
    else:
        start=monotonic()
        script(work,'watch_solver.py',binary/'xspecfem2D')
        (work/'elapsed.txt').write_text(str(monotonic()-start))
        script(work,'pack_result.py',binary)
        result=work/'gather.npz'
    deliver(result,destination)
    print('本机完成 '+str(result),flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("model")
    parser.add_argument("gather")
    parser.add_argument("--mesh-only",action="store_true")
    args = parser.parse_args()
    try:
        run(read_model(args.model),args.gather,read_compute_runtime(ROOT),args.mesh_only)
    except (OSError, ValueError, RuntimeError) as exc:
        sys.exit(str(exc))
