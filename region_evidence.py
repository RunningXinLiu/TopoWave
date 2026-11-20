"""Exact-input, report-hash-bound evidence for optional region experiments."""
import hashlib
import json
from pathlib import Path
from region_result import case_hash

ROOT = Path(__file__).resolve().parent


def backend_scope(case, found, root):
    """Compare registered hashes only; this read-only view does not contact SSH."""
    from runtime_config import read_compute_runtime
    from run_region import select_backend
    source = found.get('backend_evidence')
    historical = None
    if source:
        path = (root/source['report']).resolve()
        if not path.is_relative_to(root):
            raise ValueError('后端证据路径超出项目。')
        if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()==source['report_sha256']:
            historical = json.loads(path.read_text())[source['key']]['binary_sha256']
    try:
        configured = select_backend(case, read_compute_runtime(root))['binary_sha256']
    except (ValueError, KeyError, TypeError):
        configured = None
    required = {'xmeshfem2D', 'xspecfem2D'}
    known = historical and configured and required <= historical.keys() and required <= configured.keys()
    status = ('matched' if all(historical[k]==configured[k] for k in required) else 'different') if known else 'unknown'
    notes = {
        'matched': '当前配置登记的后端哈希与历史证据一致；实际服务器二进制仍须在运行时核验。',
        'different': '当前配置后端与历史证据不同，不能直接继承验收；下表仅展示相同输入的历史结果。',
        'unknown': '无法确认当前配置与历史后端一致；下表仅展示相同输入的历史结果。',
    }
    return dict(status=status, historical_sha256=historical, configured_sha256=configured, note=notes[status])


def evidence_for(case, root=ROOT):
    root = Path(root).resolve()
    catalog = root/'examples/cavity/evidence.json'
    if not catalog.is_file():
        return dict(matched=False, rows=[], note='本安装没有区域验证清单；输入可运行不等于精度已验证。')
    document = json.loads(catalog.read_text())
    if document.get('schema') != 'region_evidence_v1':
        raise ValueError('区域证据清单版本不支持。')
    found = next((e for e in document['entries'] if e['case_sha256']==case_hash(case)),None)
    if found is None:
        return dict(matched=False, rows=[], note='当前完整输入没有精确匹配的验证记录。修改后的参数不继承模板结论。')
    rows = []
    for row in found['checks']:
        value = dict(row)
        report = (root/row['report']).resolve()
        if not report.is_relative_to(root):
            raise ValueError('证据路径超出项目。')
        verified = report.is_file() and hashlib.sha256(report.read_bytes()).hexdigest()==row['report_sha256']
        value['verified'] = verified
        value['report_path'] = str(report)
        if not verified:
            value.update(status='证据不可用',detail='报告缺失或SHA256不一致；不显示原通过结论。')
        rows.append(value)
    backend = backend_scope(case, found, root)
    return dict(matched=True,rows=rows,backend=backend,note=backend['note']+'\n'+found['limitation'])
