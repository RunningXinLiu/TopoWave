"""Nonblocking, cancellable local reference computation for the Qt workbench."""
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import numpy as np
from PySide6 import QtCore as C, QtWidgets as W
from imaging import array_center
from model import ROOT
from theory_reference import SELECTIONS, BACKENDS, actual_backend, route_summary


class TheoryControls:
    def init_theory_controls(self,layout):
        self.theory_proc=None;self.theory_temp=None
        self.theory_selection=W.QComboBox()
        for key,label in SELECTIONS:self.theory_selection.addItem(label,key)
        self.theory_selection.setToolTip('弹性对照保留输入Vp0/Vs0/rho，忽略TI及Q；并非唯一等效材料。实际参考按材料选择后端，不支持时明确报告，不替换模型。两者均取完整原始台阵中心。')
        self.theory_backend=W.QComboBox()
        for key,label in BACKENDS:self.theory_backend.addItem(label,key)
        self.theory_backend.setToolTip('仅选择各向同性弹性曲线的后端；VTI和沿轴HTI的实际参考使用SASEM，ISO衰减参考使用SLS复波数。外部后端路径与散列在runtime.json，未配置时不自动回退。')
        layout.addWidget(self.theory_selection);layout.addWidget(self.theory_backend)
        self.plate_theory=W.QCheckBox('叠加覆盖层自由板近似（A/S模态）')
        self.plate_theory.setChecked(False);self.plate_theory.setEnabled(False)
        self.plate_theory.setToolTip('仅当完整台阵中心位于平地均匀ISO矩形空洞上方时可选。取洞顶以上厚度计算两面无牵引自由板；忽略有限洞端。空气/水洞额外忽略流体加载，不是实际声固耦合理论。最高阶N分别包含A0–AN及S0–SN；有限搜索可能缺根。只叠加曲线，不改窗口或频散功率。')
        layout.addWidget(self.plate_theory)
        self.plate_theory.toggled.connect(self.refresh_theory)
        self.theory_route_note=W.QLabel(route_summary(None));self.theory_route_note.setWordWrap(True)
        self.theory_route_note.setToolTip('曲线只叠加显示，绝不参与时间窗、功率计算或谱峰筛选。后端设置对应当前已载入记录的材料；编辑但尚未重新正演的模型不改变历史记录。')
        layout.addWidget(self.theory_route_note)
        self.theory_run=W.QPushButton('计算／更新理论覆盖')
        self.theory_run.clicked.connect(self.start_theory);layout.addWidget(self.theory_run)
        self.theory_status=W.QLabel();self.theory_status.setWordWrap(True)
        layout.addWidget(self.theory_status)
        self.theory_cancel=W.QPushButton('取消理论计算');self.theory_cancel.setEnabled(False)
        self.theory_cancel.clicked.connect(self.cancel_theory_clicked);layout.addWidget(self.theory_cancel)
        details=W.QPushButton('查看参考柱与后端记录…');details.clicked.connect(self.show_theory_reference);layout.addWidget(details)
        self.theory_selection.currentIndexChanged.connect(self.refresh_theory)
        self.theory_backend.currentIndexChanged.connect(self.refresh_theory)

    def update_theory_route(self):
        gather=getattr(self,'raw_gather',None)
        c=gather.meta.get('model',{}) if gather is not None else {}
        selection=self.theory_selection.currentData();backend=self.theory_backend.currentData()
        note=route_summary(c,selection,backend)
        try:
            from plate_reference import cover
            cover(gather.meta.get('region_case'),gather.meta.get('reference_center_x_m',array_center(gather.x)))
            plate_available=True
        except (ValueError,AttributeError,KeyError,TypeError):plate_available=False
        self.plate_theory.setEnabled(plate_available)
        if not plate_available:
            with C.QSignalBlocker(self.plate_theory):self.plate_theory.setChecked(False)
        if self.plate_theory.isChecked():note+='\n另叠加局部自由板A/S近似；不代表有限空洞或流体加载的精确模态。'
        if gather is not None and gather.meta.get('theory_reference_scope'):
            note=gather.meta['theory_reference_scope']+'\n'+note
        self.theory_route_note.setText(note)
        uses_elastic=selection in ('elastic','both') or bool(c and actual_backend(c)[0]=='elastic')
        self.theory_backend.setEnabled(bool(c) and c.get('physics','elastic')=='elastic' and uses_elastic and c.get('domain_kind')!='free_plate')

    def show_theory_reference(self):
        if self.reference is None:
            W.QMessageBox.information(self,'理论参考','尚无本次理论结果；请先计算频散谱及理论覆盖。');return
        d=W.QDialog(self);d.setWindowTitle('实际参考柱、材料与后端记录');d.resize(760,620)
        layout=W.QVBoxLayout(d);text=W.QPlainTextEdit();text.setReadOnly(True)
        text.setPlainText(json.dumps(self.reference,ensure_ascii=False,indent=2));layout.addWidget(text)
        b=W.QPushButton('关闭');b.clicked.connect(d.accept);layout.addWidget(b);d.exec()

    def stop_theory(self):
        p=getattr(self,'theory_proc',None);self.theory_proc=None
        if p is not None:
            p.kill();p.waitForFinished(1000);p.deleteLater()
        if getattr(self,'theory_temp',None) is not None:
            self.theory_temp.cleanup();self.theory_temp=None
        if hasattr(self,'theory_cancel'):self.theory_cancel.setEnabled(False)

    def cancel_theory_clicked(self):
        self.stop_theory();self.theory_status.setText('已取消理论计算');self.explain.setText('已取消理论计算；频散功率和原始记录未变。')

    def calculate_theory(self):
        self.theory_status.clear()
        self.stop_theory();self.curve=None;self.curves={};self.reference=None
        self.theory_order.setEnabled(self.theory.isEnabled() and self.theory.isChecked())
        if not self.theory.isChecked():return []
        if not self.theory.isEnabled():return ['当前记录不支持通用理论求解；保留实际材料，不替换成纯固体模型。已算研究对照见“文件…→模板验证总览”。']
        if self.result is None:return ['先计算面波窗内的频散能量，再计算理论覆盖。']
        c=self.gather.meta.get('model')
        if not c:return ['记录缺少分层模型。']
        # Original raw array, never selected or DAS endpoint-trimmed channels.
        x=self.raw_gather.meta.get('reference_center_x_m',array_center(self.raw_gather.x))
        from runtime_config import read_runtime
        try:runtime=read_runtime(ROOT)
        except (OSError,ValueError) as exc:return ['理论运行配置读取失败：'+str(exc)]
        request=dict(c=c,x=x,f=self.result['f'].tolist(),max_mode=self.theory_order.value(),
            selection=self.theory_selection.currentData(),elastic_backend=self.theory_backend.currentData(),runtime=runtime,
            region_case=self.raw_gather.meta.get('region_case'),plate=self.plate_theory.isChecked())
        self.theory_temp=TemporaryDirectory();root=Path(self.theory_temp.name)
        (root/'request.json').write_text(json.dumps(request,ensure_ascii=False))
        p=C.QProcess(self);self.theory_proc=p
        env=C.QProcessEnvironment.systemEnvironment()
        for key in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS'):env.insert(key,'1')
        env.insert('PYTHONDONTWRITEBYTECODE','1');p.setProcessEnvironment(env)
        watchdog=C.QTimer(p);watchdog.setSingleShot(True);watchdog.setInterval(180000)
        watchdog.timeout.connect(lambda:self.theory_timeout(p))
        def on_progress():
            while p.canReadLine():
                fields=bytes(p.readLine()).decode(errors='replace').split()
                if len(fields)==3 and fields[0]=='progress' and self.theory_proc is p:
                    watchdog.start()
                    self.theory_status.setText(f'SASEM 已计算 {fields[1]} / {fields[2]} 个理论频点')
        p.readyReadStandardOutput.connect(on_progress)
        p.finished.connect(lambda code,status:self.theory_finished(p,root,code))
        p.errorOccurred.connect(lambda error:self.theory_failed(p,str(error)))
        p.start(sys.executable,[str(ROOT/'theory_reference.py'),str(root/'request.json'),str(root/'result.json')])
        self.theory_cancel.setEnabled(True)
        watchdog.start()
        return ['正在独立进程计算台阵中心理论参考；可取消，不改变原始波形或频散功率。']

    def start_theory(self):
        self.explain.setText(' '.join(self.calculate_theory()+getattr(self,'analysis_notes',[])));self.draw()

    def theory_failed(self,p,error):
        if self.theory_proc is not p:return
        self.stop_theory();self.explain.setText('理论进程失败：'+error+'；频散功率保留。')

    def theory_timeout(self,p):
        if self.theory_proc is p:
            self.stop_theory();self.theory_status.clear();self.explain.setText('理论后端连续180秒未返回进展，已停止；可检查后端或缩小所选范围。')

    def theory_finished(self,p,root,code):
        if self.theory_proc is not p:return
        try:
            if code!=0:raise ValueError(bytes(p.readAllStandardError()).decode(errors='replace')[-2000:])
            result=json.loads((root/'result.json').read_text())
            if result.get('error'):raise ValueError(result['error'])
            self.reference=result['reference'];groups=self.reference['groups']
            if self.raw_gather.meta.get('theory_reference_scope'):
                self.reference['scope_note']=self.raw_gather.meta['theory_reference_scope']
                result['notes'].append(self.reference['scope_note'])
            chosen='actual' if groups.get('actual',{}).get('curves') else 'elastic'
            primary=groups.get(chosen,{})
            self.curves={int(m):tuple(np.array(a,float) for a in curve) for m,curve in primary.get('curves',{}).items()}
            self.curve=self.curves.get(0)
            self.reference['diagnostic_reference']=chosen if self.curves else None
            messages=[]
            for group in groups.values():
                name=group.get('provenance',{}).get('backend',group.get('label','理论'))
                if group.get('error'):
                    messages.append(name+'：'+group['error'])
                    continue
                summary=[]
                for mode,status in group.get('status',{}).items():
                    count=status['returned_points']
                    label=group.get('curve_labels',{}).get(mode,'R'+mode)
                    summary.append(f'{label} {count}/{len(self.result["f"])}')
                if not summary:
                    summary=[group.get('curve_labels',{}).get(mode,mode)+f' {len(ff)}/{len(self.result["f"])}' for mode,(ff,_) in group.get('curves',{}).items()]
                messages.append(name+' 有效频点：'+('，'.join(summary) or '0'))
                velocities=[v for _,vv in group.get('curves',{}).values() for v in vv]
                if velocities and not any(self.result['c'][0]<=v<=self.result['c'][-1] for v in velocities):
                    messages.append('全部理论根位于当前显示速度范围外。')
            self.theory_status.setText('\n'.join(messages))
            self.explain.setText(' '.join(result['notes']+getattr(self,'analysis_notes',[])))
        except (ValueError,OSError,KeyError) as exc:self.explain.setText('理论求解失败：'+str(exc))
        self.stop_theory();self.draw()

    def refresh_theory(self,*_):
        self.theory_status.clear()
        self.stop_theory();self.curve=None;self.curves={};self.reference=None
        self.update_theory_route()
        self.theory_order.setEnabled(self.theory.isEnabled() and self.theory.isChecked())
        if self.result is not None:
            self.explain.setText('理论选项已修改，点击“计算／更新理论覆盖”。频散功率与时间窗未变。')
            self.draw()
