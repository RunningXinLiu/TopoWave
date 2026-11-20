"""Active internal-region scenes use the same workbench lifecycle as layered shots."""
import copy
import json
import sys
from pathlib import Path
from PySide6 import QtCore as C, QtWidgets as W
from custom_scene import identity, physical_hash, state_root, atomic_json
from region_result import case_hash
from region_case import validate


def case_with_model(case, model):
    from model import SOLVER_DEFAULTS
    value=copy.deepcopy(case);candidate=copy.deepcopy(model)
    defaults=dict(physics='elastic',snapshot_stride=0,interface_relief=[],elements_per_layer=None,**SOLVER_DEFAULTS)
    for key in set(candidate)-set(case['model']):
        if key in defaults and candidate[key]==defaults[key]:candidate.pop(key)
    value['model']=candidate
    return value


def draw_regions(ax, case):
    from matplotlib.patches import Rectangle, Circle
    regions=case['regions'] if 'regions' in case else [dict(shape=dict(kind='rectangle',bounds_m=case['rectangle_m']),interior=case['interior'])]
    for r in regions:
        s=r['shape'];fluid=r['interior']['kind']=='fluid'
        kw=dict(facecolor='#add9ec' if fluid else 'white',edgecolor='#8b4633',linewidth=1.3,zorder=3)
        if s['kind']=='rectangle':
            x0,x1,z0,z1=s['bounds_m'];patch=Rectangle((x0,z0),x1-x0,z1-z0,**kw)
        else:patch=Circle(s['center_m'],s['radius_m'],**kw)
        ax.add_patch(patch)


class ActiveRegionWorkflow:
    def current_region_case(self):
        if not getattr(self,'active_region_id',None):return None
        if self.design.config is None:raise ValueError('请先修正当前模型输入。')
        case=case_with_model(self.design.region_case,self.design.config)
        validate(case)
        return case

    def load_region_case(self,case,name='空洞场景',analysis=None):
        validate(case);self._check_model_ui(case['model'])
        if self.proc.state()!=C.QProcess.ProcessState.NotRunning or self.fetch_proc is not None:
            raise ValueError('请等待当前运行或取回完成。')
        self.keep_recovery()
        self.active_region_id=None;self.design.region_case=None
        from region_templates import tunnel_result
        from analysis_presets import make_preset
        tunnel = tunnel_result(case)
        if tunnel:
            if name in ('空洞场景', Path(tunnel['locator']).name):
                name = tunnel['label']
            if analysis is None:
                analysis = make_preset(tunnel['options'], case['model'])
        self.scene_identity=identity(name);self.scene_path=None;self.saved_signature=None
        self.run_refs=[];self.region_cases=[]
        self.scene_name.setText(name)
        rid=self.store_region_case(case)
        self.activate_region(rid,clear_result=True)
        self.results.apply_scene_settings(copy.deepcopy(self.results.default_settings))
        if analysis is not None:self.results.apply_analysis_preset(analysis,validate_gather=False)
        self.attach_available_region_results(tunnel, apply_template_window=analysis is None)
        if self.run_refs:
            note = '已有匹配结果，点击“查看已有结果”载入。'
        elif tunnel:
            note = '此模板已有计算记录。本机需配置该结果位置，或打开对应结果包。'
        else:
            note = '此输入未登记匹配结果。可新算一炮，或从已算模板选择已有结果。'
        self.scene_note.setText('当前场景包含背景、洞体、震源和接收排列。' + note)

    def attach_available_region_results(self, tunnel=None, apply_template_window=False):
        from model import ROOT
        from runtime_config import read_runtime
        from computed_examples import checked_input
        from region_scene_data import check_region_run
        locations=read_runtime(ROOT).get('computed_result_locations',{})
        index=ROOT/'examples/computed600/index.json'
        case=self.current_region_case()
        terrain_dir=ROOT/'examples/layered_terrain'
        index_path=terrain_dir/'index.json'
        if index_path.is_file():
            index_data=json.loads(index_path.read_text())
            for item in index_data['entries']:
                if item['case_sha256']!=case_hash(case):continue
                result=terrain_dir/(item['key']+'.npz')
                if result.is_file():
                    ref=dict(id=C.QUuid.createUuid().toString(C.QUuid.StringFormat.Id128),kind='region',region_id=self.active_region_id,
                             case=copy.deepcopy(case),case_sha256=item['case_sha256'],label=item['label'],status='available',
                             local_path=str(result),result_sha256=item['result_sha256'])
                    check_region_run(ref);self.run_refs.append(ref)
                    if apply_template_window:
                        from analysis_presets import make_preset
                        self.results.apply_analysis_preset(make_preset(index_data['options'],case['model']),validate_gather=False)
                return
        entries = [tunnel] if tunnel else []
        if not tunnel and index.is_file():
            for entry in json.loads(index.read_text())['entries']:
                if entry['kind']=='layered':continue
                try:_,candidate=checked_input(entry)
                except (ValueError,OSError,KeyError):continue
                if candidate==case:entries.append(entry)
        for entry in entries:
            record=locations.get(entry['locator'],{}).get('region_run')
            from local_template_results import local_result
            local=local_result(entry['result_sha256'])
            if record and (record['case']!=case or record.get('preview_sha256')!=entry['result_sha256']):
                record=None
            if not record and not local:continue
            ref=dict(id=C.QUuid.createUuid().toString(C.QUuid.StringFormat.Id128),kind='region',region_id=self.active_region_id,
                     case=copy.deepcopy(case),case_sha256=case_hash(case),label=entry['label'],locator=entry['locator'],
                     status='available',result_sha256=entry['result_sha256'])
            if record:ref.update(region_run=copy.deepcopy(record),remote_run=record['remote_run'])
            if local:ref['local_path']=str(local)
            check_region_run(ref);self.run_refs.append(ref)

    def activate_region(self,region_id,clear_result=False):
        entry=next(e for e in self.region_cases if e['id']==region_id)
        self.active_region_id=region_id
        self.design.region_case=copy.deepcopy(entry['case'])
        self.design.apply(entry['case']['model'])
        if clear_result:self.results.set_gather(None)
        self.tabs.setCurrentWidget(self.design)
        self.scene_edited();self.update_shot_state(self.design.config)

    def sync_active_region(self):
        if not getattr(self,'active_region_id',None):return
        if self.design.config is not None:
            case=self.current_region_case()
            entry=next(e for e in self.region_cases if e['id']==self.active_region_id)
            entry['case']=copy.deepcopy(case)
            self.design.region_case=copy.deepcopy(case)

    def edit_active_region(self):
        if getattr(self,'active_region_id',None):
            self.sync_active_region();self.edit_scene_region(self.active_region_id)
        else:self.show_scene_regions()

    def start_active_region_run(self,mesh_only=False):
        from model import ROOT
        from run_region import select_backend
        from runtime_config import read_runtime,read_compute_runtime
        if self.proc.state()!=C.QProcess.ProcessState.NotRunning or self.fetch_proc is not None:return
        try:
            self.sync_active_region();case=self.current_region_case()
            select_backend(case,read_compute_runtime(ROOT))
            self.jobs+=1
            folder=state_root()/'region_jobs'/self.scene_identity['id']/C.QUuid.createUuid().toString(C.QUuid.StringFormat.Id128)
            folder.mkdir(parents=True)
            draft=folder/'case.json';atomic_json(draft,case)
            reference=folder/'run.json'
            run_id=self.register_region_run(case,self.active_region_id,reference)
            self._region_job=dict(id=run_id,reference=reference,mesh_only=mesh_only)
            self.pending=folder/('mesh.npz' if mesh_only else 'result.npz')
            self.active_run=None;self.design.setEnabled(False);self.tabs.setCurrentWidget(self.run_page)
            self.log.appendPlainText('\n—— 当前内部区域场景：'+('仅生成实际网格' if mesh_only else '新算一炮')+' ——')
            args=['-u',str(ROOT/'run_region.py'),str(draft),str(reference),'--preview',str(self.pending)]
            if mesh_only:args.append('--mesh-only')
            self.proc.start(sys.executable,args)
        except (ValueError,KeyError,TypeError,OSError) as exc:
            W.QMessageBox.warning(self,'未启动区域计算',str(exc))

    def finish_active_region_run(self,code):
        job=self._region_job;self._region_job=None;self.design.setEnabled(True)
        try:
            self.finish_region_run(job['id'],job['reference'],code==0)
            ref=next(r for r in self.run_refs if r['id']==job['id'])
            if code!=0:raise ValueError('运行未完成，请查看日志；不会自动重新启动。')
            self.accept_region_result(self.pending,ref)
        except (ValueError,KeyError,TypeError,OSError) as exc:
            self.log.appendPlainText(str(exc));self.statusBar().showMessage(str(exc))

    def show_region_comparison(self):
        from region_comparison import ComparisonDialog
        g=self.results.raw_gather
        if g is None or not g.meta.get('region_case'):
            W.QMessageBox.information(self,'有洞／无洞对比','先在主工作台载入一份有洞结果。正常正演和频散分析不需要无洞对照。');return
        ComparisonDialog(g,self.results.scene_settings(),self).exec()
