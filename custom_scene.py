"""Portable scene inputs and references; result arrays are optional, never executable."""
import copy
import hashlib
import json
import math
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import uuid
import zipfile

from analysis_presets import checked_options, model_hash
from model import SOLVER_DEFAULTS, validate

SCHEMA = 'custom_scene_v1'


def blank_model(layers=2, material='isotropic', attenuation=False):
    c = dict(schema=1, physics='elastic', material_model=material, layers=[[0., 0., 0., 0.] for _ in range(layers)],
             surface=[], interface_relief=[], source_mode='surface_force', boundary='stacey',
             width_m=0., element_m=0., dt_s=0., nstep=0, receivers=0, sample_stride=1, snapshot_stride=0,
             shot_x_m=0., shot_depth_m=0., f0_hz=0., first_x_m=0., spacing_m=0., receiver_depth_m=0.,
             source_t0_s=0., pml_elements=12, cpml_alpha_factor=1., damping_change_factor_elastic=1.)
    if material != 'isotropic': c['material_properties'] = [dict(epsilon=0., delta=0., gamma=0.) for _ in range(layers)]
    if attenuation:
        if material != 'isotropic': raise ValueError('TI＋衰减尚未支持。')
        c['attenuation'] = dict(enabled=True, n_sls=3, velocity_reference='source_f0')
        c['material_properties'] = [dict(qkappa=0., qmu=0.) for _ in range(layers)]
    return c


def identity(name='未命名场景', source=None):
    return dict(id=uuid.uuid4().hex, name=name, source=source)


def physical_hash(model):
    defaults = dict(physics='elastic', snapshot_stride=0, interface_relief=[], elements_per_layer=None, **SOLVER_DEFAULTS)
    return model_hash(dict(defaults, **model))


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024*1024), b''): h.update(block)
    return h.hexdigest()


def check_settings(s):
    if not isinstance(s, dict): raise ValueError('场景分析设置必须为对象。')
    s = copy.deepcopy(s)
    s['options'] = checked_options(s.get('options'))
    if s.setdefault('power_scale', 'db') not in ('db', 'percent'):
        raise ValueError('未知频散图色标。')
    if type(s.get('observable')) is not int or s['observable'] not in (0, 1, 2): raise ValueError('未知观测分量。')
    g = s.get('gauge_m')
    if type(g) not in (int, float) or not math.isfinite(g) or not .01 <= g <= 10000: raise ValueError('DAS标距无效。')
    if type(s.get('theory_enabled')) is not bool: raise ValueError('理论开关无效。')
    s.setdefault('plate_theory_enabled',False)
    if type(s['plate_theory_enabled']) is not bool:raise ValueError('自由板参考开关无效。')
    if type(s.get('theory_max_mode')) is not int or not 0 <= s['theory_max_mode'] <= 8: raise ValueError('理论最高阶无效。')
    for key,default,choices in [('theory_selection','actual',('actual','elastic','both')),('theory_backend','disba',('disba','qedisp'))]:
        s.setdefault(key,default)
        if s[key] not in choices:raise ValueError('未知理论选项：'+key)
    return s


def check_document(doc):
    if not isinstance(doc, dict) or doc.get('schema') != SCHEMA: raise ValueError('未知场景格式。')
    ident = doc.get('identity', {})
    if not isinstance(ident, dict) or not isinstance(ident.get('name'), str) or not isinstance(ident.get('id'), str): raise ValueError('缺少场景名称或ID。')
    if len(ident['id']) != 32 or any(c not in '0123456789abcdef' for c in ident['id']): raise ValueError('场景ID无效。')
    if doc.get('model') is not None: validate(doc['model'])
    elif not isinstance(doc.get('draft'), dict): raise ValueError('草稿缺少编辑输入。')
    check_settings(doc.get('settings'))
    from region_scene_data import check_regions, check_region_run
    check_regions(doc.get('internal_regions', []))
    active=doc.get('active_region_id')
    if active is not None:
        entry=next((e for e in doc.get('internal_regions',[]) if e['id']==active),None)
        if entry is None:raise ValueError('当前空洞场景引用不存在。')
        if doc.get('model') is not None and physical_hash(entry['case']['model'])!=physical_hash(doc['model']):
            raise ValueError('当前背景与空洞方案输入不一致，不能保存成同一场景。')
    if not isinstance(doc.get('runs'), list): raise ValueError('运行引用必须为列表。')
    for run in doc['runs']:
        if not isinstance(run, dict) or not isinstance(run.get('id'), str): raise ValueError('运行引用无效。')
        check_region_run(run)
    return doc


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path.parent) as temp:
        stage = Path(temp)/'stage.json'
        stage.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
        os.replace(stage, path)


def save_document(path, doc, result=None):
    """A .bgpscene package contains scene.json and optionally one raw preview NPZ."""
    doc = copy.deepcopy(check_document(doc)); path = Path(path)
    if path.suffix.lower() != '.bgpscene':
        atomic_json(path, doc)
        return
    doc.pop('bundled_result', None)
    if result is not None:
        import numpy as np
        with np.load(result, allow_pickle=False) as data:
            meta = json.loads(str(data['metadata']))
        regional=meta.get('case') is not None
        result_model=meta['case']['model'] if regional else meta['model']
        if doc['model'] is None or physical_hash(result_model) != physical_hash(doc['model']):
            raise ValueError('仅能附带与当前场景匹配的原始结果。')
        active=doc.get('active_region_id')
        if regional:
            from region_result import case_hash,load
            load(result)
            case=next((e['case'] for e in doc['internal_regions'] if e['id']==active),None)
            if case is None or case_hash(case)!=meta['case_sha256']:raise ValueError('附带结果的洞体与当前场景不一致。')
            doc['bundled_result']=dict(kind='region',sha256=digest(result),model_sha256=physical_hash(result_model),case_sha256=meta['case_sha256'])
        else:
            if active or meta.get('region_case'):raise ValueError('空洞场景须附带完整原始区域结果，不能用无洞或仅地表派生文件代替。')
            if meta.get('component') not in ('Vz', 'pressure'): raise ValueError('请附带原始炮记录，不能用DAS/Vx派生文件替代。')
            doc['bundled_result'] = dict(sha256=digest(result), model_sha256=physical_hash(meta['model']))
    with TemporaryDirectory(dir=path.parent) as temp:
        stage = Path(temp)/'scene.zip'
        with zipfile.ZipFile(stage, 'w', compression=zipfile.ZIP_STORED) as z:
            z.writestr('scene.json', json.dumps(doc, ensure_ascii=False, allow_nan=False))
            if result is not None: z.write(result, 'result.npz')
        os.replace(stage, path)


def read_document(path):
    path = Path(path)
    if path.suffix.lower() == '.bgpscene':
        with zipfile.ZipFile(path) as z:
            if z.namelist().count('scene.json') != 1 or z.namelist().count('result.npz') > 1: raise ValueError('场景包成员重复或缺失。')
            if z.getinfo('scene.json').file_size > 10_000_000: raise ValueError('场景描述过大。')
            doc = json.loads(z.read('scene.json'))
            if doc.get('bundled_result') and 'result.npz' not in z.namelist(): raise ValueError('场景包缺少声明的结果。')
    else:
        doc = json.loads(path.read_text())
    return check_document(doc)


def unpack_result(package, destination, expected):
    # Never extract paths provided by the archive.
    import shutil
    destination = Path(destination)
    with TemporaryDirectory(dir=destination.parent) as temp:
        p = Path(temp)/'result.npz'
        with zipfile.ZipFile(package) as z, z.open('result.npz') as source, p.open('wb') as target:
            shutil.copyfileobj(source, target)
        if digest(p) != expected: raise ValueError('附带结果散列不匹配，未载入。')
        os.replace(p, destination)


def state_root():
    return Path(os.environ.get('BGP_STATE_DIR', str(Path.home()/'.bgp_workbench')))


def save_runs(scene_id, runs):
    # IDs have already passed document validation or were generated with uuid4.
    if len(scene_id) != 32 or any(c not in '0123456789abcdef' for c in scene_id): raise ValueError('场景ID无效。')
    atomic_json(state_root()/'runs'/f'{scene_id}.json', runs)


def merge_runs(scene_id, runs):
    p = state_root()/'runs'/f'{scene_id}.json'
    stored = json.loads(p.read_text()) if p.exists() else []
    merged = {x['id']: x for x in runs}
    merged.update({x['id']: x for x in stored})
    return list(merged.values())
