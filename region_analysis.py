"""Lossless bridge from validated region bundles to existing surface-wave imaging."""
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import numpy as np
from PySide6 import QtWidgets as W
from imaging import Gather, array_center
from region_result import validate_bundle

REFERENCE_NOTE = '无洞背景的一维参考；忽略洞体及流体，不是空洞模型的精确理论曲线。'


def surface_gather(data):
    meta, mesh = validate_bundle(data)
    case = copy.deepcopy(meta['case'])
    model = case['model']
    return Gather(data['t'].copy(), data['x'].copy(), data['vz'].copy(),
                  dict(component='Vz', unit='m/s', source_x=model['shot_x_m'],
                       run=meta['remote_run'], model=model,
                       receiver_z_m=data['z'].tolist(),
                       reference_center_x_m=array_center(data['x']),
                       theory_reference_scope=REFERENCE_NOTE,
                       region_case=case, region_case_sha256=meta['case_sha256'],
                       region_provenance=copy.deepcopy(meta)),
                  mesh=mesh,vx=data['vx'].copy(),region_data=data)


class RegionAnalysis(W.QDialog):
    def __init__(self, data, parent=None):
        super().__init__(parent)
        from workbench import Results
        self.setWindowTitle('内部区域 · 地表单炮与面波分析')
        self.resize(1230, 900)
        layout = W.QVBoxLayout(self)
        note = W.QLabel('使用原始地表 Vz/Vx 全记录；洞内压力不参与面波成像。\n'
                       + REFERENCE_NOTE + ' 窗口不能保证分离洞体散射与面波。')
        note.setWordWrap(True); layout.addWidget(note)
        self.results = Results(); layout.addWidget(self.results)
        self.temp = TemporaryDirectory(prefix='topowave-region-analysis-')
        g = surface_gather(data)
        path = Path(self.temp.name)/'surface_shot.npz'
        np.savez_compressed(path, t=g.t, x=g.x, traces=g.traces, vx=g.vx,
                            metadata=json.dumps(g.meta, ensure_ascii=False))
        self.results.set_gather(g, path)
        # A user explicitly opens this analysis; no solver or spectrum runs here.
        self.results.analysis_mode.setChecked(True)

    def done(self, result):
        self.results.stop_theory()
        self.temp.cleanup()
        super().done(result)

    def closeEvent(self, event):
        self.results.stop_theory()
        self.temp.cleanup()
        super().closeEvent(event)
