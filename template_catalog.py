"""Curated entry points; history remains available without changing its inputs."""
from model import ROOT

RECOMMENDED = [
    ('弹性平地 · 300 m阵列 · 波形与PS/FH核验', 'material_scenes/iso_array300.json'),
    ('弹性山丘 · 300 m阵列 · 地形增量控制', 'material_scenes/iso_hill_array300.json'),
    ('浅埋隧道 · 洞顶10 m · 直径16 m空气圆洞', 'cavity/tunnels/circle_air.json'),
    ('VTI平地 · 300 m阵列 · 完整波形与PS/FH核验', 'recommended/vti_array300.json'),
    ('HTI平地 · 300 m阵列 · 扩域波形与PS/FH核验', 'recommended/hti_array300.json'),
]

def available_recommended():
    return [(label, path) for label, path in RECOMMENDED if (ROOT/'examples'/path).is_file()]

def evidence_entries():
    import json
    for folder in ('material_scenes', 'recommended', 'computed600', 'free_plate', 'solid_air_solid'):
        path = ROOT/'examples'/folder/'evidence.json'
        if path.exists():
            for entry in json.loads(path.read_text())['entries']:
                yield path.parent/entry['file'], entry

def matching_results(path, model, root=ROOT):
    """Bind catalog results to an unchanged template, retaining each result's input.

    template_model_sha256 is only for explicitly labelled related controls (e.g.
    a CPML research result beside the editable Stacey template). model_sha256
    always identifies the actual result and is still checked on opening.
    """
    import json
    from custom_scene import digest, physical_hash
    catalog = root/'examples/result_catalog.json'
    if not catalog.exists():return []
    source_hash, input_hash = digest(path), physical_hash(model)
    return [dict(e) for e in json.loads(catalog.read_text())['entries']
            if (root/'examples'/e['template']).resolve() == path.resolve()
            and e['template_sha256'] == source_hash
            and e.get('template_model_sha256', e['model_sha256']) == input_hash]

def checked_evidence(entry):
    """New recommendations bind their source report and distinguish backend reuse."""
    import hashlib
    if not entry or 'source_report_sha256' not in entry:return entry
    path=ROOT/entry['source_report']
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=entry['source_report_sha256']:return None
    from runtime_config import read_compute_runtime
    configured=read_compute_runtime(ROOT,required=False).get(entry.get('backend_config_key','ti_layered_backend'),{}).get('binary_sha256')
    if configured!=entry.get('backend_sha256'):
        entry=dict(entry,label=entry['label']+' 当前所需后端未配置或与证据不同：这里只是历史结果证据，不能继承到新运行。',scientific_status='historical_backend_unmatched')
    return entry


ACTIVE_TEMPLATES = [
    ("固—空气—固水平层 · 600 m阵列（已算研究结果）", "solid_air_solid/flat12_air16_shot.json"),
    ("独立自由板研究 · 非空气夹层（指定配置数值控制通过）", "free_plate/flat12_shot.json"),
    ("已核验平地参数 · 均匀介质12 Hz", "validated/homogeneous.json"),
    ("已核验平地参数 · 20 m层18 Hz", "validated/flat20.json"),
    ("已核验平地参数 · 80 m层18 Hz", "validated/flat80.json"),
    ("18 Hz平地 · 主面波窗筛查", "matched/flat18.json"),
    ("18 Hz山丘 · 主面波窗筛查", "matched/hill18.json"),
    ("18 Hz平地 · 节点与DAS记录", "matched/flat18-das.npz"),
    ("18 Hz山丘 · 波场预览（短记录）", "matched/hill18-preview.npz"),
    ("水平两层 · 弹性", "elastic-vz.npz"),
    ("起伏地表 · 声学", "active_templates/acoustic-pressure.npz"),
    ("山丘与水平层界 · 弹性", "elastic-hill-vz.npz"),
    ("A  平地 · 等厚覆盖层", "active_templates/A.npz"),
    ("B  山丘 · 等厚覆盖层", "active_templates/B.npz"),
    ("C  平地 · 变厚覆盖层", "active_templates/C.npz"),
    ("D  山丘 · 固定地下层界", "active_templates/D.npz"),
    ("E  平地 · 固定地下层界", "active_templates/E.npz"),
    ("三层平地 · 380 m孔径参数", "long_array/flat.json"),
    ("三层山丘 · 380 m孔径参数", "long_array/hill.json"),
    ("三层平地 · 长记录与波场", "long_array/flat.npz"),
    ("三层山丘 · 长记录与波场", "long_array/hill.npz"),
    ("780 m平地 · Stacey扩域波形基线", "stacey/flat780.json"),
    ("780 m平地 · Stacey更远边界参数", "stacey/flat_far.json"),
    ("780 m山丘 · Stacey更远边界参数", "stacey/hill_far.json"),
    ("780 m平地 · 等权参数（待正演验证）", "demo_780m/flat.json"),
    ("780 m山丘 · 等权参数（待正演验证）", "demo_780m/hill.json"),
    ("三层平地 · CPML频移候选（精度待改善）", "cpml_alpha/flat.json"),
    ("两层ISO平地 · 完整记录核验（0.8s）", "material_scenes/isotropic_flat.json"),
    ("两层ISO平地 · 300m阵列（3.2s已核验）", "material_scenes/iso_array300.json"),
    ("固定层界4m山丘 · 300m阵列（总体控制通过）", "material_scenes/iso_hill_array300.json"),
    ("各向同性对照 · 山丘（材料接口试验）", "material_scenes/isotropic_hill.json"),
    ("材料衰减 Qκ/Qμ · 平地（材料接口试验）", "material_scenes/attenuation_flat.json"),
    ("材料衰减 Qκ/Qμ · 平地300 m（网格/dt控制通过）", "material_scenes/attenuation_array300.json"),
    ("材料衰减 Qκ/Qμ · 山丘（材料接口试验）", "material_scenes/attenuation_hill.json"),
    ("材料衰减 Qκ/Qμ · 山丘300 m（网格控制通过）", "material_scenes/attenuation_hill_array300.json"),
    ("VTI · 平地（材料接口试验）", "material_scenes/vti_flat.json"),
    ("VTI · 山丘（材料接口试验）", "material_scenes/vti_hill.json"),
    ("VTI · 山丘300 m（时间步控制通过）", "material_scenes/vti_hill_array300.json"),
    ("HTI 剖面水平轴 · 平地（材料接口试验）", "material_scenes/hti_flat.json"),
    ("HTI 剖面水平轴 · 山丘（材料接口试验）", "material_scenes/hti_hill.json"),
    ("HTI · 山丘300 m（时间步控制通过）", "material_scenes/hti_hill_array300.json"),
    ("VTI大域 · 旧100 m阵列（完整波形核验）", "recommended/vti_flat.json"),
    ("HTI大域 · 旧100 m阵列（完整波形核验）", "recommended/hti_flat.json"),
]


# Optional historical result files are not part of every source distribution.
ACTIVE_TEMPLATES = [(name, path) for name, path in ACTIVE_TEMPLATES if (ROOT/'examples'/path).is_file()]
