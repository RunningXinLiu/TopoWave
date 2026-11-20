"""Editable shot templates: physics and analysis remain separate."""
import json
from pathlib import Path
from model import validate
from analysis_presets import make_preset, checked_options

SCHEMA = 'active_shot_template_v1'

def make_template(model, options):
    validate(model)
    return dict(schema=SCHEMA, model=model, analysis=make_preset(options,model))

def read_template(path):
    value=json.loads(Path(path).read_text())
    if not isinstance(value,dict):
        raise ValueError("模板必须为JSON对象。")
    if value.get('schema') == 1:
        validate(value)
        return value,None
    if value.get('schema') != SCHEMA:
        raise ValueError('未知单炮模板版本。')
    model=value.get('model')
    if not isinstance(model,dict):raise ValueError('模板缺少模型对象。')
    validate(model)
    analysis=value.get('analysis')
    if not isinstance(analysis,dict):raise ValueError('模板缺少分析对象。')
    if analysis.get('schema')!='surface_wave_analysis_v1':
        raise ValueError('未知分析预设版本。')
    analysis=dict(analysis,options=checked_options(analysis.get('options')))
    return model,analysis
