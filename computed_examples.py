"""Browse frozen computed examples without treating completion as acceptance."""
import json
from PySide6 import QtCore as C
from custom_scene import digest, physical_hash
from model import ROOT


def checked_input(entry):
    path=ROOT/'examples/computed600'/entry['template']
    if digest(path)!=entry['template_sha256']:
        raise ValueError('模板已改变；不能继承已算结果。请作为自定义输入打开。')
    value=json.loads(path.read_text())
    if physical_hash(value['model'])!=entry['model_sha256']:raise ValueError('模板模型散列不匹配。')
    return path,value


def open_entry(owner,entry,view=False):
    if owner.proc.state()!=C.QProcess.ProcessState.NotRunning or owner.fetch_proc is not None:
        raise ValueError('请等待当前计算或结果取回完成。')
    path,value=checked_input(entry)
    if entry['kind']=='layered':
        if not owner.load_template(path):return
        if view:
            refs=[r for r in owner.run_refs if r.get('locator')==entry['locator'] and r.get('result_sha256')==entry['result_sha256']]
            if not refs:raise ValueError('此机器尚未配置结果位置。可编辑输入，或配置runtime中的结果位置后查看。')
            owner.open_run_result(refs[-1])
    else:
        analysis=json.loads((ROOT/'examples/computed600'/entry['analysis']).read_text())
        owner.load_region_case(value,entry['label'],analysis)
        if not view:return
        refs=[r for r in owner.run_refs if r.get('locator')==entry['locator'] and r.get('result_sha256')==entry['result_sha256']]
        if not refs:raise ValueError('匹配结果引用未载入，请检查本机结果位置配置。')
        owner.open_run_result(refs[-1])
