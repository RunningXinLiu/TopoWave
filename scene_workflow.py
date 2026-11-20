"""Scene lifecycle and explicit result reuse for the native workbench."""
import copy
import json
import re
from pathlib import Path
from local_template_results import result_for_ref
import uuid
import zipfile
from PySide6 import QtCore as C, QtWidgets as W

from analysis_presets import model_hash
from custom_scene import (SCHEMA, identity, blank_model, check_document, atomic_json, digest, physical_hash,
                          read_document, save_document, unpack_result, state_root, save_runs, merge_runs)
from imaging import Gather
from model import ROOT, SOLVER_DEFAULTS, validate
from shot_templates import read_template
from runtime_config import read_runtime,read_compute_runtime
from scene_regions import SceneRegions
from region_scene_data import check_region_run, region_match


class SceneWorkflow(SceneRegions):
    def init_scene_workflow(self, layout):
        self.scene_identity=identity(); self.scene_path=None; self.run_refs=[]; self.saved_signature=None
        self.region_cases=[]
        self.active_region_id=None;self._region_job=None
        self.active_run=None; self.fetch_proc=None
        bar=W.QWidget(); row=W.QHBoxLayout(bar); row.setContentsMargins(0,0,0,0)
        row.addWidget(W.QLabel('场景'))
        self.scene_name=W.QLineEdit('未命名场景'); self.scene_name.setMaximumWidth(220); row.addWidget(self.scene_name)
        self.scene_status=W.QLabel('草稿 · 尚未保存'); row.addWidget(self.scene_status,1)
        def action(text, slot):
            b=W.QPushButton(text);b.clicked.connect(slot);row.addWidget(b);return b
        action('模板库…',self.show_template_library)
        action('保存场景',self.save_template)
        more=W.QToolButton();more.setText('文件…');more.setPopupMode(W.QToolButton.ToolButtonPopupMode.InstantPopup)
        menu=W.QMenu(more);more.setMenu(menu);row.addWidget(more)
        menu.addAction('另存为新场景…',lambda:self.save_template(save_as=True))
        menu.addAction('导出可选附带结果的模板包…',self.export_scene_package)
        menu.addAction('恢复上次会话草稿',self.restore_recovery)
        menu.addAction('模板验证总览与已算理论对照…',self.show_validation_evidence)
        menu.addAction('有洞／无洞对比（可选）…',self.show_region_comparison)
        action('分析设置…',self.edit_scene_analysis)
        action('查看已有结果…',self.show_scene_runs)

        action('洞体与内部区域…',self.edit_active_region)
        layout.insertWidget(1,bar)
        self.design.custom_requested.connect(self.new_custom_scene)
        self.design.changed.connect(self.scene_edited)
        self.scene_name.textChanged.connect(self.scene_edited)
        for widget in list(self.results.inputs.values())+[self.results.gauge,self.results.theory_order,self.results.db_floor]:
            widget.valueChanged.connect(self.scene_edited)
        for widget in (self.results.observable,self.results.method_choice,self.results.side_choice,self.results.spatial_choice,self.results.power_scale,self.results.theory_selection,self.results.theory_backend):
            widget.currentIndexChanged.connect(self.scene_edited)
        self.results.theory.toggled.connect(self.scene_edited)
        self.results.plate_theory.toggled.connect(self.scene_edited)
        self.scene_note=W.QLabel('打开模板不启动计算。可直接查看已有结果；修改后新算一炮会保留旧记录。')
        self.scene_note.setWordWrap(True);layout.insertWidget(2,self.scene_note)
        self.design.run.setToolTip('明确启动一次新计算，即使已有相同参数的结果也不会覆盖。查看已有结果无需计算。')
        self.initial_signature=self.scene_signature()

    def show_template_library(self):
        from template_library import TemplateLibrary
        TemplateLibrary(self).exec()

    def show_validation_evidence(self):
        from validation_evidence import dialog
        dialog(self).exec()

    def scene_signature(self):
        return model_hash(dict(draft=self.draft_state(),settings=self.results.scene_settings(),name=self.scene_name.text(),internal_regions=self.region_cases,active_region_id=self.active_region_id))

    def draft_state(self):
        d=self.design
        return dict(base=copy.deepcopy(d.base),fields={k:w.value() for k,w in d.fields.items()},
                    layers=[[d.table.item(i,j).text() if d.table.item(i,j) else '' for j in range(4)] for i in range(d.table.rowCount())],
                    nz=d.nz_edit.text(),source_mode=d.source_mode.currentData(),boundary=d.boundary.currentData(),domain_kind=d.domain_kind.currentData())

    def document(self):
        self.sync_active_region()
        ident=dict(self.scene_identity,name=self.scene_name.text().strip() or '未命名场景')
        return dict(schema=SCHEMA,identity=ident,model=copy.deepcopy(self.design.config),draft=self.draft_state(),
                    settings=self.results.scene_settings(),runs=copy.deepcopy(self.run_refs),
                    internal_regions=copy.deepcopy(self.region_cases),
                    active_region_id=self.active_region_id,
                    last_saved_path=str(self.scene_path) if self.scene_path else None)

    def scene_edited(self,*_):
        if not hasattr(self,'scene_name'):return
        sig=self.scene_signature()
        self.scene_status.setText(('已保存' if sig==self.saved_signature else '未保存修改')+(' · 草稿，不能运行' if self.design.config is None else ' · 输入可编辑'))

    def mark_saved(self):
        self.saved_signature=self.scene_signature()
        self.scene_edited()

    def keep_recovery(self):
        if not hasattr(self,'scene_name'):return
        sig=self.scene_signature()
        if not self.run_refs and self.scene_path is None and sig==self.initial_signature:return
        try:
            atomic_json(state_root()/'recovery.json',self.document())
            save_runs(self.scene_identity['id'],self.run_refs)
        except (OSError,ValueError) as exc:self.statusBar().showMessage('草稿暂存失败：'+str(exc))

    def restore_recovery(self):
        p=state_root()/'recovery.json'
        if not p.exists():
            W.QMessageBox.information(self,'恢复草稿','尚无上次会话草稿。');return
        self.load_template(p, recovering=True)

    def _check_model_ui(self,c):
        for key,w in self.design.fields.items():
            v=c.get(key,SOLVER_DEFAULTS.get(key,0))
            if not isinstance(v,(int,float)) or not w.minimum()<=v<=w.maximum():raise ValueError(f'{key} 超出界面范围。')
            if isinstance(w,W.QSpinBox) and type(v) is not int:raise ValueError(f'{key} 必须为整数。')

    def _check_draft(self,d):
        if not isinstance(d,dict) or not isinstance(d.get('base'),dict):raise ValueError('草稿结构无效。')
        if not {'layers','width_m','surface','dt_s','nstep'}<=set(d['base']):raise ValueError('草稿基础字段不完整。')
        rows=d['base']['layers']
        if not isinstance(rows,list) or not 1<=len(rows)<=40 or any(not isinstance(r,list) or len(r)!=4 or any(type(x) not in (int,float) for x in r) for r in rows):raise ValueError('草稿基础层表无效。')
        self._check_model_ui(d['base'])
        if set(d.get('fields',{}))!=set(self.design.fields):raise ValueError('草稿数值字段不完整。')
        self._check_model_ui(d['fields'])
        if not isinstance(d.get('layers'),list) or not 1<=len(d['layers'])<=40 or any(len(r)!=4 or any(not isinstance(x,str) for x in r) for r in d['layers']):raise ValueError('草稿层表无效。')
        if d.get('domain_kind',d['base'].get('domain_kind','halfspace')) not in ('halfspace','free_plate','solid_fluid_layers'):raise ValueError('草稿域类型无效。')
        if d.get('source_mode') not in ('legacy','surface_force') or d.get('boundary') not in ('cpml','stacey') or not isinstance(d.get('nz'),str):raise ValueError('草稿选项无效。')

    def _apply_draft(self,d):
        self.design.apply(d['base']);w=self.design;w.loading=True
        for key,value in d['fields'].items():w.fields[key].setValue(value)
        w.table.setRowCount(len(d['layers']))
        for i,r in enumerate(d['layers']):
            for j,v in enumerate(r):w.table.setItem(i,j,W.QTableWidgetItem(v))
        w.nz_edit.setText(d['nz']);w.source_mode.setCurrentIndex(w.source_mode.findData(d['source_mode']))
        w.boundary.setCurrentIndex(w.boundary.findData(d['boundary']))
        w.domain_kind.setCurrentIndex(w.domain_kind.findData(d.get('domain_kind',d['base'].get('domain_kind','halfspace'))))
        w.loading=False;w.refresh();w.update_duration()

    def load_template(self,path,recovering=False):
        path=Path(path)
        if hasattr(self,'proc') and self.proc.state()!=C.QProcess.ProcessState.NotRunning:
            self.statusBar().showMessage('请等待当前计算完成后切换场景。');return
        if self.fetch_proc is not None:
            self.statusBar().showMessage('请等待结果取回后切换场景。');return
        try:
            gather=None;doc=None;analysis=None
            if path.suffix.lower()=='.bgpscene':doc=read_document(path)
            elif path.suffix.lower()=='.json':
                raw=json.loads(path.read_text())
                if raw.get('schema') in ('rect_region_v1','region_geometry_v1'):
                    self.load_region_case(raw,path.stem);return True
                if raw.get('schema')=='region_run_v1':
                    self.load_region_case(raw['case'],path.stem);self.import_region_file(path);self.open_region_run(self.run_refs[-1]);return
                if isinstance(raw,dict) and raw.get('schema')==SCHEMA:doc=read_document(path)
                else:config,analysis=read_template(path)
            else:
                import numpy as np
                with np.load(path,allow_pickle=False) as a:meta=json.loads(str(a['metadata']))
                if meta.get('case'):
                    from region_result import load as load_region_result
                    load_region_result(path)  # Reject damaged arrays before replacing the live scene.
                    self.load_region_case(meta['case'],path.stem);self.import_region_file(path);self.open_region_run(self.run_refs[-1]);return
                gather=Gather.open(path);config=gather.meta.get('model')
                if not config:raise ValueError('此结果缺少可复算模型。')
                validate(config);analysis=gather.meta.get('analysis_preset')
            settings=copy.deepcopy(self.results.default_settings)
            if doc:
                config=doc['model'];settings=self.results.check_scene_settings(doc['settings'])
                if config is None:self._check_draft(doc['draft'])
            elif analysis is not None:
                settings['options']=self.results.check_analysis_preset(analysis)
                settings['origin_model_sha256']=analysis.get('origin_model_sha256')
            if config is not None:self._check_model_ui(config)
            # Read references before touching the live scene.
            runs=merge_runs(doc['identity']['id'],doc['runs']) if doc else []
            for r in runs:
                check_region_run(r)
                if r.get('status')=='running':r['status']='unknown_after_restart'
            source=dict(path=str(path.resolve()),sha256=digest(path))
            if doc is None and path.suffix.lower()=='.json':
                from template_catalog import matching_results
                locations=read_runtime(ROOT).get('template_result_locations',{})
                for entry in matching_results(path,config):
                    location=locations.get(entry['locator'])
                    # Scientific hashes/labels come from the catalog, never runtime.
                    from local_template_results import local_result
                    ref=dict(location or {},**entry)
                    local=local_result(entry['result_sha256'])
                    if local:ref['local_path']=str(local)
                    runs.append(ref)
            if not recovering:self.keep_recovery()
            self.scene_identity=copy.deepcopy(doc['identity']) if doc else identity(path.stem,source)
            self.scene_path=path if doc and path.suffix=='.json' and not recovering else None
            if recovering and doc.get('last_saved_path'):self.scene_path=Path(doc['last_saved_path'])
            self.run_refs=runs;self.saved_signature=None
            self.region_cases=copy.deepcopy(doc.get('internal_regions',[])) if doc else []
            self.active_region_id=doc.get('active_region_id') if doc else None
            self.design.region_case=copy.deepcopy(next(e['case'] for e in self.region_cases if e['id']==self.active_region_id)) if self.active_region_id else None
            self.scene_name.setText(self.scene_identity['name'])
            if config is not None:self.design.apply(config)
            else:self._apply_draft(doc['draft'])
            self.results.set_gather(gather,path if gather is not None else None)
            self.results.apply_scene_settings(settings)
            if gather is not None:self.remember_result(path,gather)
            if doc and doc.get('bundled_result'):
                b=doc['bundled_result']
                ref=dict(id='package-'+b['sha256'],kind=b.get('kind','result'),status='available',package=str(path.resolve()),
                         result_sha256=b['sha256'],model_sha256=b['model_sha256'],label=path.name+' · 包内结果')
                if b.get('kind')=='region':ref.update(region_id=self.active_region_id,case=copy.deepcopy(self.design.region_case),case_sha256=b['case_sha256'])
                self.run_refs=[x for x in self.run_refs if x['id']!=ref['id']]+[ref]
            if doc:self.mark_saved()
            self.scene_note.setText('已打开；已有结果可直接查看，不会自动重算。参数修改后旧结果仍属于原输入。'+(' 此文件没有分析预设，已恢复初始候选，需自行设置面波窗。' if not doc and analysis is None else ''))
            self.tabs.setCurrentWidget(self.design);self.update_shot_state(self.design.config)
            self.statusBar().showMessage('已打开 '+path.name)
            return True
        except (ValueError,KeyError,TypeError,OSError,zipfile.BadZipFile) as exc:
            W.QMessageBox.warning(self,'打开场景失败',str(exc))

    def new_custom_scene(self):
        if self.proc.state()!=C.QProcess.ProcessState.NotRunning or self.fetch_proc is not None:return
        d=W.QDialog(self);d.setWindowTitle('新建自定义场景');f=W.QFormLayout(d)
        name=W.QLineEdit('我的场景');layers=W.QSpinBox();layers.setRange(1,40);layers.setValue(2)
        material=W.QComboBox()
        for text,key in [('各向同性','isotropic'),('VTI（试验）','vti'),('HTI（试验）','hti')]:material.addItem(text,key)
        att=W.QCheckBox('Q衰减（仅各向同性）')
        for label,w in [('名称',name),('层数',layers),('背景',material),('材料损耗',att)]:f.addRow(label,w)
        f.addRow(W.QLabel('创建待填写草稿：零值表示尚未输入，不能运行。\n随后填写层参数、源与排列、计算设置；可先保存草稿。'))
        box=W.QDialogButtonBox(W.QDialogButtonBox.StandardButton.Ok|W.QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(d.accept);box.rejected.connect(d.reject);f.addRow(box)
        if d.exec()!=W.QDialog.DialogCode.Accepted:return
        try:c=blank_model(layers.value(),material.currentData(),att.isChecked())
        except ValueError as exc:W.QMessageBox.warning(self,'组合不支持',str(exc));return
        self.keep_recovery();self.scene_identity=identity(name.text().strip() or '我的场景')
        self.active_region_id=None;self.design.region_case=None
        self.scene_path=None;self.run_refs=[];self.saved_signature=None
        self.region_cases=[]
        self.scene_name.setText(self.scene_identity['name']);self.design.apply(c)
        self.results.set_gather(None);self.results.apply_scene_settings(copy.deepcopy(self.results.default_settings))
        self.scene_note.setText('自定义草稿：请填写所有物理输入；可保存未完成层表。支持的参数由界面校验，不会自动启动计算。')
        self.tabs.setCurrentWidget(self.design);self.scene_edited()

    def edit_scene_analysis(self):
        """Editable before any result exists; application is explicit, not an estimate."""
        s=self.results.scene_settings();d=W.QDialog(self);d.setWindowTitle('场景观测与分析设置 · 候选输入');d.resize(630,700)
        layout=W.QVBoxLayout(d);scroll=W.QScrollArea();scroll.setWidgetResizable(True);body=W.QWidget();form=W.QFormLayout(body)
        scroll.setWidget(body);layout.addWidget(scroll)
        info=W.QLabel('可在正演前保存。分析设置不会修改波场计算；运行后须结合完整单炮检查面波窗。\nDAS目前仅支持水平直光纤；VTI及沿轴HTI使用SASEM；均匀或两层ISO衰减柱使用SLS复波数参考。可显式选择弹性对照。');info.setWordWrap(True);form.addRow(info)
        obs=W.QComboBox();obs.addItems(['节点 Vz','节点 Vx','DAS 应变率（水平直光纤）']);obs.setCurrentIndex(s['observable']);form.addRow('观测量',obs)
        gauge=W.QDoubleSpinBox();gauge.setRange(.01,10000);gauge.setDecimals(2);gauge.setValue(s['gauge_m']);gauge.setToolTip(self.results.gauge.toolTip());form.addRow('DAS 标距 (m)',gauge)
        gauge.setKeyboardTracking(False);gauge.setSingleStep(self.results.gauge.singleStep())
        method=W.QComboBox();method.addItems(['PS','FH']);method.setCurrentText(s['options'].get('method','PS'));form.addRow('频散算子',method)
        side=W.QComboBox();side.addItems(['炮点右侧','炮点左侧']);side.setCurrentIndex(0 if s['options']['right'] else 1);form.addRow('选道方向',side)
        weight=W.QComboBox();weight.addItems(['equal','hann']);weight.setCurrentText(s['options']['spatial_weighting']);form.addRow('空间权重',weight)
        labels=dict(min_offset='最近偏移 (m)',max_offset='最远偏移 (m)，0全排列',vslow='慢端视群速 (m/s)',vfast='快端视群速 (m/s)',pad='窗口余量 (s)',fmin='最低频率 (Hz)',fmax='最高频率 (Hz)',cmin='最低相速度 (m/s)',cmax='最高相速度 (m/s)',dc='速度步长 (m/s)')
        fields={}
        for k,source in self.results.inputs.items():
            w=W.QDoubleSpinBox();w.setDecimals(source.decimals());w.setRange(source.minimum(),source.maximum());w.setValue(s['options'][k]);w.setToolTip(source.toolTip());form.addRow(labels[k],w);fields[k]=w
        theory=W.QCheckBox('显示台阵中心理论曲线');theory.setChecked(s['theory_enabled']);form.addRow(theory)
        order=W.QSpinBox();order.setRange(0,8);order.setValue(s['theory_max_mode']);order.setToolTip(self.results.theory_order.toolTip());form.addRow('最高阶（含R0）',order)
        choices={}
        for key,source,label in [('theory_selection',self.results.theory_selection,'参考类型'),('theory_backend',self.results.theory_backend,'弹性参考后端')]:
            combo=W.QComboBox()
            for i in range(source.count()):combo.addItem(source.itemText(i),source.itemData(i))
            combo.setCurrentIndex(combo.findData(s[key]));combo.setToolTip(source.toolTip());form.addRow(label,combo);choices[key]=combo
        error=W.QLabel();error.setWordWrap(True);layout.addWidget(error)
        for target,source in [(obs,self.results.observable),(method,self.results.method_choice),(side,self.results.side_choice),(weight,self.results.spatial_choice),(theory,self.results.theory)]:
            target.setToolTip(source.toolTip())
        from ui_help import expose_parameter_tips
        expose_parameter_tips(d)
        buttons=W.QDialogButtonBox(W.QDialogButtonBox.StandardButton.Ok|W.QDialogButtonBox.StandardButton.Cancel);layout.addWidget(buttons);buttons.rejected.connect(d.reject)
        def apply():
            candidate=copy.deepcopy(s)
            candidate['options'].update({k:w.value() for k,w in fields.items()})
            candidate['options'].update(method=method.currentText(),right=side.currentIndex()==0,spatial_weighting=weight.currentText())
            candidate.update({k:w.currentData() for k,w in choices.items()})
            candidate.update(observable=obs.currentIndex(),gauge_m=gauge.value(),theory_enabled=theory.isChecked(),theory_max_mode=order.value(),origin_model_sha256=model_hash(self.design.config))
            try:self.results.check_scene_settings(candidate)
            except (ValueError,TypeError) as exc:error.setText(str(exc));return
            self.results.apply_scene_settings(candidate);self.scene_edited();d.accept()
        buttons.accepted.connect(apply);d.exec()

    def save_template(self,*_,save_as=False):
        if save_as and self.proc.state()!=C.QProcess.ProcessState.NotRunning:
            self.statusBar().showMessage('计算结束后可另存为新场景；当前场景仍可保存。');return
        try:
            doc=self.document();check_document(doc)
            dest=None if save_as else self.scene_path
            if dest is None:
                p,_=W.QFileDialog.getSaveFileName(self,'保存自定义场景（可含未完成草稿）',self.scene_name.text()+'.json','场景 (*.json)')
                if not p:return
                dest=Path(p)
                if dest.suffix.lower()!='.json':dest=Path(str(dest)+'.json')
            if any(dest.resolve().is_relative_to(ROOT/x) for x in ('examples','templates')):raise ValueError('内置模板保留原件，请另存到自己的场景目录。')
            if save_as:
                doc['identity']=identity(self.scene_name.text(),dict(parent_scene_id=self.scene_identity['id']))
            doc['last_saved_path']=str(dest.resolve());save_document(dest,doc)
            self.scene_identity=doc['identity'];self.scene_path=dest;save_runs(self.scene_identity['id'],self.run_refs)
            self.mark_saved();self.statusBar().showMessage('已保存场景 '+str(dest))
        except (ValueError,TypeError,KeyError,OSError) as exc:W.QMessageBox.warning(self,'保存场景失败',str(exc))

    def export_scene_package(self):
        try:
            doc=self.document();check_document(doc)
            if doc['model'] is None:raise ValueError('模板包要求完整有效模型；未完成输入请保存场景草稿。')
            g=self.results.raw_gather;result=self.results.path
            matched=g is not None and result is not None and physical_hash(g.meta['model'])==physical_hash(doc['model'])
            if matched and self.active_region_id:
                from region_result import case_hash
                matched=g.meta.get('region_case_sha256')==case_hash(self.current_region_case())
            elif matched and g.meta.get('region_case'):matched=False
            d=W.QDialog(self);d.setWindowTitle('导出模板包');layout=W.QVBoxLayout(d)
            info=W.QLabel('参数包可独立编辑和重跑。附带现有结果可离线查看，不会触发计算。\n完整原生波场保存在原始运行目录；包内仅含已有桌面波场预览、道集及可用网格。');info.setWordWrap(True);layout.addWidget(info)
            include=W.QCheckBox('附带当前匹配的原始结果');include.setEnabled(matched);include.setChecked(matched);layout.addWidget(include)
            if not matched:layout.addWidget(W.QLabel('当前无匹配原始结果，将导出参数包。'))
            box=W.QDialogButtonBox(W.QDialogButtonBox.StandardButton.Ok|W.QDialogButtonBox.StandardButton.Cancel);box.accepted.connect(d.accept);box.rejected.connect(d.reject);layout.addWidget(box)
            if d.exec()!=W.QDialog.DialogCode.Accepted:return
            p,_=W.QFileDialog.getSaveFileName(self,'保存模板包',self.scene_name.text()+'.bgpscene','模板包 (*.bgpscene)')
            if not p:return
            p=Path(p)
            if p.suffix!='.bgpscene':p=Path(str(p)+'.bgpscene')
            save_document(p,doc,result if include.isChecked() else None)
            self.statusBar().showMessage('模板包已导出；原结果文件保留。')
        except (ValueError,KeyError,TypeError,OSError) as exc:W.QMessageBox.warning(self,'导出失败',str(exc))

    def remember_result(self,path,g,ref=None):
        ref=ref or dict(id=uuid.uuid4().hex,kind='result')
        ref.update(status='available',local_path=str(Path(path).resolve()),model_sha256=physical_hash(g.meta['model']),
                   result_sha256=digest(path),remote_run=g.meta.get('run'),label=Path(path).name)
        self.run_refs=[r for r in self.run_refs if r['id']!=ref['id']]+[ref]
        save_runs(self.scene_identity['id'],self.run_refs)

    def show_scene_runs(self):
        self.sync_active_region()
        d=W.QDialog(self);d.setWindowTitle('本场景已有结果 · 查看不会重算');d.resize(850,450)
        layout=W.QVBoxLayout(d);listing=W.QListWidget();layout.addWidget(listing)
        current=physical_hash(self.design.config) if self.design.config else None
        for r in self.run_refs:
            match='匹配当前参数' if r.get('model_sha256')==current else '历史输入 / 不匹配当前参数'
            if r.get('view_only'):match+=' · 仅查看，不能据此直接重算'
            if r.get('kind')=='region':match='匹配当前区域方案' if region_match(r,self.region_cases) else '历史区域输入 / 不匹配当前方案'
            loc='包内结果' if r.get('package') else '本地可用' if result_for_ref(r) else '服务器引用' if r.get('remote_run') else '此机器未配置结果位置'
            labels=dict(available='文件可用',completed='计算结束，非精度验收',failed='计算失败',running='运行中',unknown_after_restart='重启后状态待核查，不自动重跑')
            kind='内部区域单炮' if r.get('kind')=='region' else '网格' if r.get('kind')=='mesh' else '单炮'
            if r.get('region_run',{}).get('status')=='mesh_completed_pending_qc':kind='内部区域网格'
            item=W.QListWidgetItem(f"{r.get('label',r['id'])} · {kind} · {labels.get(r.get('status'),r.get('status','未知'))}\n{match} · {loc}"+('\n'+r['validation_label'] if r.get('validation_label') else ''))
            item.setToolTip(json.dumps(r,ensure_ascii=False,indent=2));listing.addItem(item)
        if not self.run_refs:layout.addWidget(W.QLabel('此场景暂无结果。可读入带结果模板，或明确点击“按当前参数新算一炮”。'))
        hint=W.QLabel('旧结果始终按其自身模型显示。服务器结果仅在点击查看时取回桌面预览，不重算。');hint.setWordWrap(True);layout.addWidget(hint)
        b=W.QPushButton('查看选中结果');layout.addWidget(b)
        def open_selected():
            i=listing.currentRow()
            if i>=0:d.accept();self.open_run_result(self.run_refs[i])
        b.clicked.connect(open_selected);listing.itemDoubleClicked.connect(lambda *_:open_selected())
        if self.run_refs:listing.setCurrentRow(len(self.run_refs)-1)
        d.exec()

    def open_run_result(self,ref):
        if self.proc.state()!=C.QProcess.ProcessState.NotRunning:
            self.statusBar().showMessage('请在当前计算完成后查看历史结果。');return
        try:
            if ref.get('kind')=='region':self.open_region_run(ref);return
            local=result_for_ref(ref)
            if local:self.accept_run_result(local,ref);return
            folder=Path(self.result_dir.name)/('view-'+uuid.uuid4().hex);folder.mkdir()
            dest=folder/('mesh.npz' if ref.get('kind')=='mesh' else 'gather.npz')
            if ref.get('package'):
                unpack_result(ref['package'],dest,ref['result_sha256']);self.accept_run_result(dest,ref);return
            runtime=read_runtime(ROOT,required=True);host=runtime['ssh_host'];remote=ref.get('remote_run','')
            if ref.get('ssh_host',host)!=host or not re.fullmatch(r'/[a-zA-Z0-9_./-]+',remote):raise ValueError('缺少可用结果位置；可手动打开原始NPZ。')
            if self.fetch_proc is not None:raise ValueError('已有结果正在取回，请稍候。')
            proc=C.QProcess(self);self.fetch_proc=proc
            def done(code,status):
                error=bytes(proc.readAllStandardError()).decode(errors='replace');self.fetch_proc=None
                if code!=0:W.QMessageBox.warning(self,'结果取回失败','未启动新正演。请检查网络/SSH登录后重试。\n'+error);return
                try:self.accept_run_result(dest,ref)
                except (ValueError,KeyError,OSError,TypeError) as exc:W.QMessageBox.warning(self,'结果核验失败',str(exc))
            proc.finished.connect(done)
            def failed(error):
                if error==C.QProcess.ProcessError.FailedToStart:
                    self.fetch_proc=None
                    W.QMessageBox.warning(self,'结果传输未启动',proc.errorString())
            proc.errorOccurred.connect(failed)
            self.statusBar().showMessage('正在取回已有桌面预览；没有启动正演…')
            proc.start('scp',['-q','-o','BatchMode=yes','-o','ConnectTimeout=8',f'{host}:{remote}/{dest.name}',str(dest)])
        except (ValueError,KeyError,OSError,TypeError,zipfile.BadZipFile) as exc:W.QMessageBox.warning(self,'查看结果失败',str(exc))

    def accept_run_result(self,path,ref):
        if ref.get('result_sha256') and digest(path)!=ref['result_sha256']:raise ValueError('结果文件散列与登记不一致。')
        if ref.get('kind')=='mesh':
            import numpy as np
            from native_mesh import ElementMesh
            with np.load(path,allow_pickle=False) as z:
                c=json.loads(str(z['model']));mesh=ElementMesh.read(z)
                if physical_hash(c)!=ref['model_sha256']:raise ValueError('网格输入与引用不匹配。')
                self.design.set_actual_mesh(mesh,c,str(z['run']))
            self.tabs.setCurrentWidget(self.design)
        else:
            g=Gather.open(path)
            if physical_hash(g.meta['model'])!=ref['model_sha256']:raise ValueError('结果模型与引用不匹配。')
            settings=self.results.scene_settings();self.results.set_gather(g,path);self.results.apply_scene_settings(settings)
            self.tabs.setCurrentWidget(self.results);self.update_shot_state(self.design.config)
        ref['local_path']=str(path);self.statusBar().showMessage('已打开既有结果；没有重新计算。')
        if ref.get('validation_label'):self.scene_note.setText('当前查看结果的验证范围：'+ref['validation_label'])
        if ref.get('view_only'):
            self.scene_note.setText(self.scene_note.text()+' 当前为已算研究结果，只读其原始输入；模型页仍是另一个可编辑方案，新算一炮不会复现此结果。')

    def register_run(self,mesh_only):
        from run_forward import select_backend
        runtime=select_backend(self.design.config,read_compute_runtime(ROOT))
        ref=dict(id=uuid.uuid4().hex,kind='mesh' if mesh_only else 'result',status='running',
                 model_sha256=physical_hash(self.design.config),model=copy.deepcopy(self.design.config),
                 execution_target='local',binary_sha256=runtime.get('binary_sha256'),
                 code_sha256={name:digest(ROOT/name) for name in ('model.py','materials.py','run_forward.py','pack_result.py','templates/Par_file','templates/SOURCE')},
                 label=('网格 ' if mesh_only else '单炮 ')+C.QDateTime.currentDateTime().toString('yyyy-MM-dd HH:mm:ss'))
        self.active_log_start=len(self.log.toPlainText())
        self.active_run=ref;self.run_refs.append(ref);save_runs(self.scene_identity['id'],self.run_refs)
        self.keep_recovery()

    def capture_run_location(self):
        if not self.active_run:return
        log=self.log.toPlainText()[getattr(self,'active_log_start',0):]
        hits=re.findall(r'算例[ \t]+([\w.-]+):(/[^\s]+)[\r\n]',log)
        local=re.findall(r"^本机算例 (.+)$",log,re.MULTILINE)
        if local:self.active_run["local_run"]=local[-1].strip()
        if hits:
            self.active_run.update(ssh_host=hits[-1][0],remote_run=hits[-1][1])
            save_runs(self.scene_identity['id'],self.run_refs)
        atomic_json(state_root()/'logs'/f"{self.active_run['id']}.json",dict(log=log))

    def finish_run_record(self,code):
        if not self.active_run:return
        ref=self.active_run;ref['status']='completed' if code==0 else 'failed'
        if code==0 and self.pending.is_file():
            saved=Path(ref['local_run'])/self.pending.name if ref.get('local_run') else self.pending
            ref.update(local_path=str(saved),result_sha256=digest(self.pending))
        save_runs(self.scene_identity['id'],self.run_refs);self.active_run=None;self.keep_recovery()
