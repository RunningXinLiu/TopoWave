"""Scene-owned region variants with shared history and the existing region viewer."""
import copy
import json
import sys
import uuid
from pathlib import Path
from local_template_results import result_for_ref

from PySide6 import QtCore as C, QtWidgets as W
from custom_scene import atomic_json, digest, save_runs, state_root
from region_result import case_hash
from region_scene_data import check_region_run, region_match, update_region
from region_workflow import ActiveRegionWorkflow


class SceneRegions(ActiveRegionWorkflow):
    def open_region_template(self, case=None):
        if self.proc.state() != C.QProcess.ProcessState.NotRunning or self.fetch_proc is not None:
            self.statusBar().showMessage('请等待当前计算或结果取回完成后编辑内部区域。'); return
        from region_templates import template_dialog, make_editor
        if case is None:
            dialog = template_dialog(self)
            if dialog.exec() != W.QDialog.DialogCode.Accepted:return
            case = dialog.selected_case
        self.load_region_case(case)

    def store_region_case(self, case, region_id=None):
        self.region_cases, region_id = update_region(self.region_cases, case, region_id)
        if getattr(self,'active_region_id',None)==region_id:
            self.design.region_case=copy.deepcopy(case)
            self.design.apply(case['model'])
        self.scene_edited(); self.keep_recovery()
        return region_id

    def edit_scene_region(self, region_id=None):
        if self.proc.state() != C.QProcess.ProcessState.NotRunning or self.fetch_proc is not None:
            self.statusBar().showMessage('请等待当前计算或结果取回完成后编辑内部区域。'); return
        from region_editor import RegionEditor
        entry = next((r for r in self.region_cases if r['id'] == region_id), None)
        if entry and entry['case'].get('schema')=='region_geometry_v1':
            from region_geometry_editor import GeometryEditor
            GeometryEditor(self,scene_owner=self,entry=entry).exec()
        else:RegionEditor(self, scene_owner=self, entry=entry).exec()

    def show_scene_regions(self):
        d=W.QDialog(self); d.setWindowTitle('本场景内部区域'); d.resize(600,350)
        layout=W.QVBoxLayout(d)
        note=W.QLabel('每个方案保存背景、洞体、源和接收排列。\n设为当前场景后，在主页面编辑、运行和分析；旧运行始终保留其原始输入。')
        note.setWordWrap(True); layout.addWidget(note); listing=W.QListWidget(); layout.addWidget(listing)
        for entry in self.region_cases:
            from region_case import description
            listing.addItem(entry['name']+' · '+description(entry['case']))
        activate=W.QPushButton('将选中方案设为当前场景');layout.addWidget(activate)
        def activate_selected():
            i=listing.currentRow()
            if i>=0:self.activate_region(self.region_cases[i]['id'],clear_result=True);d.accept()
        activate.clicked.connect(activate_selected)
        row=W.QHBoxLayout(); layout.addLayout(row)
        templates=W.QPushButton('从空洞与流体模板创建…');layout.addWidget(templates)
        def from_template():
            d.accept();self.open_region_template()
        templates.clicked.connect(from_template)
        for label, new in [('编辑选中方案',False),('新建弹性空洞方案',True)]:
            b=W.QPushButton(label); row.addWidget(b)
            def open_editor(checked=False, new=new):
                i=listing.currentRow()
                if not new and i<0:return
                rid=None if new else self.region_cases[i]['id'];d.accept();self.edit_scene_region(rid)
            b.clicked.connect(open_editor)
        b=W.QPushButton('导入已有输入/结果');row.addWidget(b)
        def import_existing():
            p,_=W.QFileDialog.getOpenFileName(d,'导入区域输入、运行引用或结果','','区域文件 (*.json *.npz)')
            if not p:return
            try:self.import_region_file(p);d.accept()
            except (ValueError,KeyError,TypeError,OSError) as exc:W.QMessageBox.warning(d,'导入失败',str(exc))
        b.clicked.connect(import_existing)
        if self.region_cases:listing.setCurrentRow(0)
        d.exec()

    def import_region_file(self,path):
        from region_case import validate
        from region_result import load
        path=Path(path);record=None;meta=None
        if path.suffix.lower()=='.npz':
            data=load(path);meta=json.loads(str(data['metadata']));case=meta['case']
        else:
            value=json.loads(path.read_text())
            if value.get('schema')=='region_run_v1':record=value;case=value['case']
            else:case=value
        validate(case)
        previous=next((e for e in self.region_cases if case_hash(e['case'])==case_hash(case)),None)
        region_id=previous['id'] if previous else uuid.uuid4().hex
        ref=None
        if meta is not None or record is not None:
            ref=dict(id=uuid.uuid4().hex,kind='region',region_id=region_id,case=copy.deepcopy(case),case_sha256=case_hash(case),label=path.name,
                     status='available' if meta else 'completed' if record.get('status')=='solver_completed_pending_qc' else 'unknown_after_restart')
            if meta:ref.update(local_path=str(path.resolve()),result_sha256=digest(path),remote_run=meta.get('remote_run'))
            else:ref.update(region_run=record,reference_path=str(path.resolve()),remote_run=record.get('remote_run'))
            check_region_run(ref)
        if previous is None:self.region_cases.append(dict(id=region_id,name=path.stem,case=copy.deepcopy(case)))
        if ref:self.run_refs.append(ref);save_runs(self.scene_identity['id'],self.run_refs)
        self.scene_edited();self.keep_recovery();self.statusBar().showMessage('区域输入/结果引用已加入场景草稿；没有启动计算。')
        return region_id

    def register_region_run(self, case, region_id, reference_path):
        ref=dict(id=uuid.uuid4().hex,kind='region',region_id=region_id,case=copy.deepcopy(case),
                 case_sha256=case_hash(case),reference_path=str(Path(reference_path).resolve()),status='running',
                 label='内部区域 '+C.QDateTime.currentDateTime().toString('yyyy-MM-dd HH:mm:ss'))
        check_region_run(ref);self.run_refs.append(ref);save_runs(self.scene_identity['id'],self.run_refs);self.keep_recovery()
        return ref['id']

    def finish_region_run(self, run_id, reference_path, success):
        ref=next(r for r in self.run_refs if r['id']==run_id)
        candidate=copy.deepcopy(ref)
        if success and not Path(reference_path).is_file():raise ValueError('进程成功但缺少区域运行引用，需核查。')
        if Path(reference_path).is_file():
            candidate['region_run']=json.loads(Path(reference_path).read_text())
            check_region_run(candidate)
            candidate['remote_run']=candidate['region_run'].get('remote_run')
            if candidate['region_run'].get('local_preview'):candidate['local_path']=candidate['region_run']['local_preview']
            if success and candidate['region_run'].get('status') not in ('solver_completed_pending_qc','mesh_completed_pending_qc'):raise ValueError('服务器引用未确认计算结束。')
        candidate['status']='completed' if success else 'failed'
        ref.clear();ref.update(candidate);save_runs(self.scene_identity['id'],self.run_refs);self.keep_recovery()

    def open_region_run(self, ref):
        check_region_run(ref)
        local=result_for_ref(ref)
        if local: self.accept_region_result(local,ref);return
        if ref.get('package'):
            from custom_scene import unpack_result
            from tempfile import mkdtemp
            dest=Path(mkdtemp(dir=self.result_dir.name))/'region.npz'
            unpack_result(ref['package'],dest,ref['result_sha256']);self.accept_region_result(dest,ref);return
        record=ref.get('region_run')
        if record is None and ref.get('reference_path') and Path(ref['reference_path']).is_file():
            candidate=dict(ref,region_run=json.loads(Path(ref['reference_path']).read_text()));check_region_run(candidate);record=candidate['region_run']
        if record is None:raise ValueError('此区域运行尚无可用服务器引用；不会自动重算。')
        if self.fetch_proc is not None:raise ValueError('已有结果正在取回。')
        folder=state_root()/'region_previews'/uuid.uuid4().hex;folder.mkdir(parents=True)
        reference=folder/'run.json';atomic_json(reference,record);dest=folder/'result.npz'
        proc=C.QProcess(self);self.fetch_proc=proc
        def done(code,status):
            self.fetch_proc=None
            if code!=0 or status!=C.QProcess.ExitStatus.NormalExit:
                W.QMessageBox.warning(self,'区域结果取回失败',bytes(proc.readAllStandardError()).decode(errors='replace'));return
            try:self.accept_region_result(dest,ref)
            except (ValueError,KeyError,TypeError,OSError) as exc:W.QMessageBox.warning(self,'区域结果核验失败',str(exc))
        def failed(error):
            if error==C.QProcess.ProcessError.FailedToStart:
                self.fetch_proc=None;W.QMessageBox.warning(self,'取回未启动',proc.errorString())
        proc.finished.connect(done);proc.errorOccurred.connect(failed)
        self.statusBar().showMessage('正在取回区域预览，不启动正演…')
        mode='--fetch-mesh' if record.get('status')=='mesh_completed_pending_qc' else '--fetch'
        proc.start(sys.executable,[str(Path(__file__).with_name('run_region.py')),mode,str(reference),str(dest)])

    def accept_region_result(self, path, ref):
        from region_result import load,validate_bundle
        from region_analysis import surface_gather
        import numpy as np
        check_region_run(ref)
        if ref.get('result_sha256') and digest(path)!=ref['result_sha256']:raise ValueError('区域结果文件散列不匹配。')
        record=ref.get('region_run',{})
        if record.get('status')=='mesh_completed_pending_qc':
            from region_mesh_result import load as load_mesh
            case,mesh=load_mesh(path,record)
            if getattr(self,'active_region_id',None)!=ref['region_id']:self.activate_region(ref['region_id'])
            self.design.set_actual_mesh(mesh,case['model'],(record.get('local_run') or record.get('remote_run')),case)
            self.tabs.setCurrentWidget(self.design)
        else:
            data=load(path);meta,_=validate_bundle(data)
            if meta['case_sha256']!=ref['case_sha256']:raise ValueError('区域结果与运行输入快照不匹配。')
            if record and meta.get('input_sha256')!=record.get('input_sha256'):raise ValueError('区域结果DATA散列不匹配。')
            if getattr(self,'active_region_id',None)!=ref['region_id']:
                self.activate_region(ref['region_id'])
            settings=self.results.scene_settings()
            self.results.set_gather(surface_gather(data),path);self.results.apply_scene_settings(settings)
            self.tabs.setCurrentWidget(self.results)
        scope = ref.get('region_run', {}).get('scientific_scope')
        mesh_only=record.get('status')=='mesh_completed_pending_qc'
        message='已载入内部区域实际网格及几何QC；此运行没有生成波场或单炮。' if mesh_only else '已载入内部区域结果：模型、波场和单炮共用当前场景；没有重新计算。'
        self.scene_note.setText(message+(' 验证范围：'+str(scope) if scope else ''))
        if not region_match(ref,self.region_cases):self.scene_note.setText('历史区域输入，与当前保存方案不匹配；显示原运行结果。 '+self.scene_note.text())
        ref.update(local_path=str(Path(path).resolve()),result_sha256=digest(path))
        save_runs(self.scene_identity['id'],self.run_refs)
        self.update_shot_state(self.design.config)
        self.statusBar().showMessage('实际网格载入完成；在模型页查看网格与QC，未运行波动求解器。' if mesh_only else '已有结果载入完成；在波场快照、单炮记录页查看，不会自动重算。')
