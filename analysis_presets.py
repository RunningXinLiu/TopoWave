"""Portable analysis settings, never a forward-validation certificate."""
import hashlib
import json
import math
from pathlib import Path

KEYS={"right","gated","vslow","vfast","pad","min_offset","max_offset","fmin","fmax","cmin","cmax","dc","spatial_weighting"}

def checked_options(options):
    if not isinstance(options,dict) or set(options)-{"method"}!=KEYS:
        raise ValueError("分析预设字段缺失或含未知字段。")
    o=dict(options)
    if o.get("method","PS") not in ("PS","FH"):raise ValueError("未知成像方法。")
    if type(o["right"]) is not bool or o["gated"] is not True:
        raise ValueError("分析预设必须使用明确方向和面波时间窗。")
    if o["spatial_weighting"] not in ("equal","hann"):
        raise ValueError("未知空间权重。")
    for k in KEYS-{"right","gated","spatial_weighting"}:
        if type(o[k]) not in (int,float) or not math.isfinite(o[k]):
            raise ValueError("预设数值必须有限："+k)
    if not (0<o["vslow"]<o["vfast"] and o["pad"]>0 and 0<o["fmin"]<o["fmax"] and 0<o["cmin"]<o["cmax"] and o["dc"]>0):
        raise ValueError("预设频带、速度或时间窗范围无效。")
    if o["min_offset"]<0 or o["max_offset"]<0 or (o["max_offset"] and o["max_offset"]<=o["min_offset"]):
        raise ValueError("预设偏移距范围无效。")
    if not 1<=(o["cmax"]-o["cmin"])/o["dc"]<=2500:
        raise ValueError("预设速度扫描需要2–2501个点。")
    return o

def model_hash(model):
    return hashlib.sha256(json.dumps(model,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest() if model else None

def record_hash(g):
    import numpy as np
    h=hashlib.sha256()
    for a in (g.t,g.x,g.traces):
        a=np.asarray(a,dtype='<f8')
        h.update(str(a.shape).encode());h.update(a.tobytes())
    return h.hexdigest()

def matched_showcase_preset(g,root=None):
    from model import ROOT
    root=Path(root) if root is not None else ROOT/'examples/analysis_presets'
    index=root/'showcase_index.json'
    if g is None or g.meta.get('component')!='Vz' or not index.is_file():
        raise ValueError('当前记录没有匹配的演示窗口；可手动选窗或读入分析预设。')
    digest=record_hash(g)
    for item in json.loads(index.read_text())['entries']:
        if item['record_sha256']==digest and item['model_sha256']==model_hash(g.meta.get('model')):
            path=root/item['file']
            if hashlib.sha256(path.read_bytes()).hexdigest()!=item['preset_sha256']:
                raise ValueError('演示窗口文件已改变，不能继承匹配状态。')
            return read_preset(path)
    raise ValueError('当前波形或模型与已检查记录不匹配；未套用其他案例窗口。')

def make_preset(options,model=None):
    return dict(schema="surface_wave_analysis_v1",options=checked_options(options),
                origin_model_sha256=model_hash(model),
                note="仅保存分析参数；模型匹配也不代表正演或频散已通过验收。")

def read_preset(path):
    p=json.loads(Path(path).read_text())
    if not isinstance(p,dict) or p.get("schema")!="surface_wave_analysis_v1":
        raise ValueError("不支持的分析预设版本。")
    p["options"]=checked_options(p.get("options"))
    return p
