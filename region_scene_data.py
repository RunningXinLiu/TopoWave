"""Validated internal-region variants and immutable run snapshots in a scene."""
import copy
import re
import uuid

from region_case import validate
from region_result import case_hash


def check_regions(regions):
    if not isinstance(regions, list): raise ValueError('内部区域列表无效。')
    seen = set()
    for entry in regions:
        if not isinstance(entry, dict) or not re.fullmatch('[0-9a-f]{32}', entry.get('id', '')):
            raise ValueError('内部区域ID无效。')
        if entry['id'] in seen or not isinstance(entry.get('name'), str) or not entry['name'].strip():
            raise ValueError('内部区域ID重复或名称缺失。')
        seen.add(entry['id']); validate(entry['case'])
    return regions


def check_region_run(ref):
    if ref.get('kind') != 'region': return
    validate(ref['case'])
    if ref.get('case_sha256') != case_hash(ref['case']): raise ValueError('区域运行输入摘要不匹配。')
    if not re.fullmatch('[0-9a-f]{32}', ref.get('region_id', '')): raise ValueError('区域运行缺少有效归属。')
    record = ref.get('region_run')
    if record is not None and (record.get('schema') != 'region_run_v1' or case_hash(record['case']) != ref['case_sha256']):
        raise ValueError('服务器运行引用与区域快照不匹配。')


def update_region(regions, case, region_id=None):
    validate(case); result = copy.deepcopy(check_regions(regions))
    if region_id is not None:
        entry = next((e for e in result if e['id'] == region_id), None)
        if entry is None: raise ValueError('此内部区域已不属于当前场景。')
        entry['case'] = copy.deepcopy(case)
    else:
        entry = dict(id=uuid.uuid4().hex, name=f'内部区域 {len(result)+1}', case=copy.deepcopy(case))
        result.append(entry)
    return result, entry['id']


def region_match(ref, regions):
    entry = next((e for e in regions if e['id'] == ref.get('region_id')), None)
    return entry is not None and ref.get('case_sha256') == case_hash(entry['case'])
