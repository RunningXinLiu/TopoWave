"""Explicit starter combinations; templates remain the sole input authority."""
import hashlib
import json
from model import ROOT, read_model

SCENE_ROOT = ROOT / 'examples/material_scenes'


def load_scene(background='isotropic', attenuation=False, terrain='flat', inclusion='none', root=SCENE_ROOT, recommended=False):
    if background not in ('isotropic', 'vti', 'hti') or terrain not in ('flat', 'hill'):
        raise ValueError('未知背景或地形；请从已有模板开始，再编辑自定义地表和层界。')
    if inclusion != 'none':
        raise ValueError('空洞及流体方案请使用上方“空洞与流体模板”入口；此分层模板入口不混合区域输入，也不会用零速度区域代替空洞。')
    if attenuation and background != 'isotropic':
        raise ValueError('TI＋衰减尚未接入当前求解路径；请修改所选组合，不会自动替换背景或关闭衰减。')
    stem = 'attenuation' if attenuation else background
    path = root / f'{stem}_{terrain}.json'
    if root.resolve() == SCENE_ROOT.resolve() and not recommended:
        if attenuation:
            path = root / ('attenuation_array300.json' if terrain == 'flat' else 'attenuation_hill_array300.json')
        elif not attenuation and background == 'isotropic':
            path = root / ('iso_array300.json' if terrain == 'flat' else 'iso_hill_array300.json')
        elif background in ('vti','hti') and terrain == 'hill':
            path = root / f'{background}_hill_array300.json'
        elif not attenuation and terrain == 'flat':
            path = root.parent / 'recommended' / f'{background}_array300.json'
    if recommended:
        if attenuation or (background != 'isotropic' and terrain != 'flat'):
            raise ValueError('该组合目前只有研究模板。取消“仅使用推荐演示”可查看其实际验证范围；不会自动更换材料或地形。')
        path = (root / ('iso_array300.json' if terrain=='flat' else 'iso_hill_array300.json')) if background=='isotropic' else root.parent/'recommended'/f'{background}_array300.json'
    config = read_model(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    entries = json.loads((path.parent / 'evidence.json').read_text())['entries']
    evidence = next((x for x in entries if x['file'] == path.name and x['template_sha256'] == digest), None)
    from template_catalog import checked_evidence
    evidence=checked_evidence(evidence)
    return dict(path=path, model=config, evidence=evidence, sha256=digest)
