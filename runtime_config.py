"""Optional local platform configuration; offline viewing needs no SSH setup."""
import json
from pathlib import Path


def read_runtime(root, required=False):
    path = Path(root) / 'runtime.json'
    if not path.exists():
        if required:
            raise ValueError('尚未配置正演服务器。请按 docs/release/INSTALL_zh.md 将 runtime.example.json 复制为 runtime.json 并填写平台信息；查看离线示例不需要此配置。')
        return {}
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError('runtime.json 必须是配置对象。')
    if required and any(not data.get(k) for k in ('ssh_host', 'run_parent', 'bin_dir')):
        raise ValueError('runtime.json 缺少 ssh_host、run_parent 或 bin_dir。')
    return data


def read_compute_runtime(root, required=True):
    path=Path(root)/'local_compute.json'
    if not path.is_file():
        if not required:return {}
        raise ValueError('未配置本机求解器。请按 docs/release/INSTALL_zh.md 设置 local_compute.json。')
    runtime=json.loads(path.read_text())
    if not isinstance(runtime,dict) or runtime.get('execution_target')!='local' or not runtime.get('run_parent') or not runtime.get('bin_dir'):
        raise ValueError('本机配置缺少 execution_target=local、run_parent 或 bin_dir。')
    return runtime
