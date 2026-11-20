"""Independent experimental region editor. Never replaces the active scene."""
import copy,json,sys
from pathlib import Path
from PySide6 import QtCore as C,QtWidgets as W
from fluid_cavity import PRESETS,validate,write
from materials import attenuating

ROOT=Path(__file__).resolve().parent
class RegionEditor(W.QDialog):
    def __init__(self,parent=None,scene_owner=None,entry=None):
        super().__init__(parent);self.setWindowTitle('矩形内部区域 · 单炮试验');self.resize(750,760)
        self.scene_owner=scene_owner;self.region_id=entry['id'] if entry else None;self.scene_run_id=None
        layout=W.QVBoxLayout(self)
        intro=W.QLabel('区域方案：平地、单层ISO、矩形、Stacey；当前按串行运行。默认弹性＋无牵引空洞，Q为可选扩展。\n固定模板已有波形参考；尖角应力不在验收范围，空气壁面应力另有未通过项。请用“查看验证证据”核对完整输入。')
        intro.setWordWrap(True);layout.addWidget(intro)
        tabs=W.QTabWidget();layout.addWidget(tabs);self.fields={}
        for title,items in [('模型与区域',[('width_m','模型宽度 (m)'),('depth','模型深度 (m)'),('vp','固体Vp (m/s)'),('vs','固体Vs (m/s)'),('rho','固体密度 (kg/m³)'),('xmin','区域左边 (m)'),('xmax','区域右边 (m)'),('zmin','区域底高程 (m)'),('zmax','区域顶高程 (m)')]),('源、接收与数值',[('shot_x_m','炮点x (m)'),('f0_hz','主频 (Hz)'),('source_t0_s','源预移 (s)'),('first_x_m','接收起点 (m)'),('spacing_m','道距 (m)'),('receivers','道数'),('element_m','单元边长 (m)'),('dt_s','dt (s)'),('nstep','步数'),('sample_stride','记录间隔 (步)'),('snapshot_stride','快照间隔 (步)')])]:
            page=W.QWidget();form=W.QFormLayout(page);tabs.addTab(page,title)
            for key,label in items:
                w=W.QLineEdit();self.fields[key]=w;form.addRow(label,w)
        qpage=W.QWidget();qform=W.QFormLayout(qpage);tabs.addTab(qpage,'可选固体衰减')
        self.q_enabled=W.QCheckBox('启用固体Q衰减（流体内部仍为无黏声学介质）');qform.addRow(self.q_enabled)
        self.q_fields={}
        for key,label in [('qkappa','Qκ（体积损耗）'),('qmu','Qμ（剪切损耗）'),('n_sls','SLS数（3–8）')]:
            self.q_fields[key]=W.QLineEdit();qform.addRow(label,self.q_fields[key])
        self.q_reference=W.QComboBox();self.q_reference.addItem('源主频处速度','source_f0');self.q_reference.addItem('无限频率未松弛速度','unrelaxed');self.q_reference.setCurrentIndex(-1)
        qform.addRow('Vp/Vs参考',self.q_reference)
        qnote=W.QLabel('Qκ不等于Qp；9999是后端关闭该项损耗的标记。\n固体Q不自动施加到流体；流体保持有限密度、声速及零剪切模量。\nQ声固运行需单独登记双场后端；TI空洞、CPML仍拒绝。参数改变不继承模板证据。');qnote.setWordWrap(True);qform.addRow(qnote)
        self.q_enabled.toggled.connect(self.update_q)
        form=W.QFormLayout();layout.addLayout(form)
        self.kind=W.QComboBox();self.kind.addItem('无牵引空洞 · 忽略内部介质','traction_free');self.kind.addItem('流体填充 · 声固耦合','fluid');form.addRow('内部类型',self.kind)
        self.kind.setToolTip('无牵引：孔内无计算单元，固体壁面满足 σ·n=0。\n流体：保留有限密度和声速，Vs=0；界面法向速度连续，固体牵引 σ·n=−p n。\n无黏流体不传递切向牵引，不要求两侧切向速度相等；不能将密度和所有速度设为零来替代这两种模型。')
        self.fluid={}
        for key,label in [('name','流体名称'),('rho_kg_m3','流体密度 (kg/m³)'),('sound_speed_m_s','流体声速 (m/s)'),('reference','参数来源 / 参考条件')]:
            w=W.QLineEdit();self.fluid[key]=w;form.addRow(label,w)
        self.fluid['rho_kg_m3'].setToolTip('正有限密度；与声速共同决定声阻抗 ρc 和体积模量 K=ρc²。')
        self.fluid['sound_speed_m_s'].setToolTip('流体声速 c=Vp，后端明确使用声学方程（Vs=0）；需按最短相关波长检查网格。')
        buttons=W.QHBoxLayout();layout.addLayout(buttons)
        for name,label in [('air','填入空气候选'),('water','填入清水候选')]:
            b=W.QPushButton(label);b.clicked.connect(lambda checked=False,n=name:self.preset(n));buttons.addWidget(b)
        self.message=W.QLabel('主要入口：弹性无牵引空洞。默认480×240m案例已有本组网格/dt/边界检查及独立有限元对照。\nQ保持关闭，另列可选试验页；修改输入不继承验证，原320×160m案例保留边界未通过记录。');self.message.setWordWrap(True);layout.addWidget(self.message)
        actions=W.QHBoxLayout();layout.addLayout(actions)
        for label,fn in [('打开输入',self.open_input),('检查输入',self.check),('保存输入',self.save),('导出新算例目录',self.export),('查看结果包',self.open_result),('关闭',self.reject)]:
            b=W.QPushButton(label);b.clicked.connect(fn);actions.addWidget(b)
        if scene_owner is not None:
            b=W.QPushButton('应用洞体修改到场景');layout.addWidget(b);b.clicked.connect(self.attach_to_scene)
        inspect_row=W.QHBoxLayout();layout.addLayout(inspect_row)
        for label,fn in [('预览待提交网格',self.preview_mesh),('查看验证证据',self.show_evidence)]:
            b=W.QPushButton(label);inspect_row.addWidget(b);b.clicked.connect(fn)
        self.run_button=W.QPushButton('保存并在本机运行');layout.addWidget(self.run_button);self.run_button.clicked.connect(self.start_run)
        advanced=W.QPushButton('圆洞、多洞与地形方案…');layout.addWidget(advanced)
        def open_geometry():
            from region_geometry_editor import GeometryEditor
            GeometryEditor(self,scene_owner=self.scene_owner).exec()
        advanced.clicked.connect(open_geometry)
        self.process=C.QProcess(self);self.process.setProcessChannelMode(C.QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self.run_output);self.process.finished.connect(self.run_finished)
        self.process.errorOccurred.connect(self.run_error);self.run_log='';self.run_reference=None
        self.kind.currentIndexChanged.connect(self.update_kind)
        self.apply(json.loads((ROOT/'examples/cavity/elastic_dry_boundary480.json').read_text()))
        self.message.setText('已载入弹性无牵引默认案例；本组0.6s完整记录已有网格/dt/边界检查及独立有限元对照。Q默认关闭，修改输入不继承验证。')
        if entry is not None:self.apply(entry['case'])

    def show_evidence(self):
        try:
            from region_evidence import evidence_for
            evidence=evidence_for(self.collect())
            d=W.QDialog(self);d.setWindowTitle('当前区域输入 · 验证证据');d.resize(1000,550)
            layout=W.QVBoxLayout(d)
            note=W.QLabel(('完整输入精确匹配。\n' if evidence['matched'] else '')+evidence['note'])
            note.setWordWrap(True);layout.addWidget(note)
            table=W.QTableWidget(len(evidence['rows']),3)
            table.setHorizontalHeaderLabels(['历史检查','历史状态','数值与适用范围'])
            table.setEditTriggers(W.QAbstractItemView.EditTrigger.NoEditTriggers)
            table.setSelectionBehavior(W.QAbstractItemView.SelectionBehavior.SelectRows)
            for i,row in enumerate(evidence['rows']):
                for j,key in enumerate(('label','status','detail')):
                    item=W.QTableWidgetItem(row[key]);item.setToolTip(row['report_path']);table.setItem(i,j,item)
            table.horizontalHeader().setSectionResizeMode(0,W.QHeaderView.ResizeMode.ResizeToContents)
            table.horizontalHeader().setSectionResizeMode(1,W.QHeaderView.ResizeMode.ResizeToContents)
            table.horizontalHeader().setSectionResizeMode(2,W.QHeaderView.ResizeMode.Stretch)
            table.resizeRowsToContents();layout.addWidget(table)
            buttons=W.QDialogButtonBox(W.QDialogButtonBox.StandardButton.Close);layout.addWidget(buttons);buttons.rejected.connect(d.reject)
            buttons.button(W.QDialogButtonBox.StandardButton.Close).setText('关闭')
            open_report=buttons.addButton('打开选中证据报告',W.QDialogButtonBox.ButtonRole.ActionRole)
            def open_selected():
                from PySide6 import QtGui
                i=table.currentRow()
                if i>=0 and evidence['rows'][i]['verified']:
                    QtGui.QDesktopServices.openUrl(C.QUrl.fromLocalFile(evidence['rows'][i]['report_path']))
            open_report.clicked.connect(open_selected)
            open_report.setEnabled(False)
            table.itemSelectionChanged.connect(lambda:open_report.setEnabled(table.currentRow()>=0 and evidence['rows'][table.currentRow()]['verified']))
            if evidence['rows']:table.selectRow(0)
            d.exec()
        except (ValueError,KeyError,TypeError,OSError) as exc:self.message.setText('无法检查验证证据：'+str(exc))

    def attach_to_scene(self):
        try:
            self.region_id=self.scene_owner.store_region_case(self.collect(),self.region_id)
            self.message.setText('当前区域方案已写入场景草稿；请用主窗口“保存场景”持久保存。已有运行仍保留原输入。')
        except (ValueError,KeyError,TypeError,OSError) as exc:self.message.setText(str(exc))

    def preview_mesh(self):
        try:
            import numpy as np
            from cavity_mesh import build
            from matplotlib.figure import Figure
            from matplotlib.collections import PolyCollection
            from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
            from plot_toolbar import PlotToolbar
            case=self.collect();c=case['model'];fluid=case['interior']['kind']=='fluid'
            points,cells,_,_,qc=build(dict(schema='dry_rect_cavity_v1',model=c,rectangle_m=None if fluid else case['rectangle_m']))
            x0,x1,z0,z1=case['rectangle_m'];centers=points[cells].mean(axis=1)
            inside=(centers[:,0]>x0)&(centers[:,0]<x1)&(centers[:,1]>z0)&(centers[:,1]<z1)
            colors=np.where(inside[:,None],np.array([[.6,.83,1.]]),np.array([[.93,.9,.83]]))
            d=W.QDialog(self);d.setWindowTitle('当前区域输入 · 待提交网格');d.resize(1050,650);layout=W.QVBoxLayout(d)
            note=W.QLabel(f"{len(cells)} 单元 · {len(points)} 几何节点 · 最小Jacobian={qc['jacobian_min_m2']:g} m²。\n与导出使用同一网格生成器；拓扑检查不代表求解精度验收。当前输入未修改，也未启动计算。")
            note.setWordWrap(True);layout.addWidget(note);fig=Figure(figsize=(10,5),layout='constrained');canvas=FigureCanvasQTAgg(fig)
            layout.addWidget(PlotToolbar(canvas,d));layout.addWidget(canvas)
            whole,zoom=fig.subplots(1,2)
            for ax in (whole,zoom):
                ax.add_collection(PolyCollection(points[cells],facecolors=colors,edgecolors='#657580',linewidths=.25))
                ax.scatter([c['shot_x_m']],[-c['shot_depth_m']],c='red',marker='*',s=65,label='Source')
                ax.scatter(c['first_x_m']+np.arange(c['receivers'])*c['spacing_m'],np.full(c['receivers'],-c['receiver_depth_m']),c='orange',s=7,label='Receivers')
                ax.set(xlabel='x (m)',ylabel='z (m)',aspect='equal')
            whole.set(xlim=(0,c['width_m']),ylim=(-c['layers'][0][0],3),title='Current exported element geometry');whole.legend()
            zoom.set(xlim=(max(0,x0-16),min(c['width_m'],x1+16)),ylim=(max(-c['layers'][0][0],z0-16),min(3,z1+16)),title='Fluid region' if fluid else 'Traction-free hole: no interior cells')
            canvas.draw();d.exec()
        except (ValueError,KeyError,TypeError,OSError) as exc:self.message.setText(str(exc))
    def open_result(self):
        from region_result_view import RegionResultView
        view=RegionResultView(self);view.open_file()
        if view.data is not None:
            from region_result import case_hash
            try:matches=case_hash(self.collect())==view.meta['case_sha256']
            except (ValueError,KeyError,TypeError):matches=False
            if not matches:view.summary.setText('当前编辑输入与此历史结果不匹配；以下显示结果包保存的输入。\n'+view.summary.text())
            view.exec()

    def update_kind(self):
        for w in self.fluid.values():w.setEnabled(self.kind.currentData()=='fluid')
        self.update_q()
    def update_q(self):
        active=self.q_enabled.isChecked()
        for w in [*self.q_fields.values(),self.q_reference]:w.setEnabled(active)
    def preset(self,name):
        self.kind.setCurrentIndex(1)
        for k,w in self.fluid.items():w.setText(str(PRESETS[name][k]))
    def apply(self,case):
        validate(case) # reject before touching widgets
        self.base=copy.deepcopy(case)
        m=case['model'];vals=dict(m,**dict(zip(['depth','vp','vs','rho'],m['layers'][0])),**dict(zip(['xmin','xmax','zmin','zmax'],case['rectangle_m'])))
        for k,w in self.fields.items():w.setText(str(vals[k]))
        self.kind.setCurrentIndex(0 if case['interior']['kind']=='traction_free' else 1)
        for k,w in self.fluid.items():w.setText(str(case['interior'].get(k,'')))
        att=m.get('attenuation',{});props=m.get('material_properties')
        props=props[0] if isinstance(props,list) and props and isinstance(props[0],dict) else {}
        self.q_enabled.setChecked(attenuating(m))
        for k,w in self.q_fields.items():w.setText(str(att.get(k,'') if k=='n_sls' else props.get(k,'')))
        self.q_reference.setCurrentIndex(self.q_reference.findData(att.get('velocity_reference')))
        self.update_kind()
        self.message.setText('已载入完整区域输入；可编辑、预览网格并保存到场景。使用“查看验证证据”核对本次输入，参数修改不继承模板结论。')
    def collect(self):
        c=copy.deepcopy(self.base);m=c['model'];ints={'receivers','nstep','sample_stride','snapshot_stride'}
        values={k:(int(w.text()) if k in ints else float(w.text())) for k,w in self.fields.items()}
        # Preserve numeric representation when unchanged: historical case hashes
        # are byte-canonical JSON, and 2 versus 2.0 must not create a fake edit.
        for k in m.keys()&values.keys():
            if m[k]!=values[k]:m[k]=values[k]
        layers=[[values[k] for k in ['depth','vp','vs','rho']]]
        if layers!=m['layers']:m['layers']=layers
        rectangle=[values[k] for k in ['xmin','xmax','zmin','zmax']]
        if rectangle!=c['rectangle_m']:c['rectangle_m']=rectangle
        c['interior']=dict(kind=self.kind.currentData())
        if self.kind.currentData()=='fluid':
            c['interior'].update({k:float(w.text()) if k in ('rho_kg_m3','sound_speed_m_s') else w.text() for k,w in self.fluid.items()})
        if self.q_enabled.isChecked():
            m['attenuation']=dict(enabled=True,n_sls=int(self.q_fields['n_sls'].text()),velocity_reference=self.q_reference.currentData())
            m['material_properties']=[{k:float(self.q_fields[k].text()) for k in ('qkappa','qmu')}]
        elif 'attenuation' in m:m['attenuation']['enabled']=False
        validate(c);return copy.deepcopy(self.base) if c==self.base else c
    def check(self):
        try:self.collect();self.message.setText('输入检查通过；需重新生成实际网格及验证。未更改dt、时长或材料。')
        except (ValueError,KeyError,TypeError) as e:self.message.setText(str(e))
    def open_input(self):
        p,_=W.QFileDialog.getOpenFileName(self,'打开输入、历史运行或结果包','','内部区域 (*.json *.npz)')
        if p:
            try:
                if Path(p).suffix.lower()=='.npz':
                    from region_result import load
                    value=json.loads(str(load(p)['metadata']))['case']
                else:
                    value=json.loads(Path(p).read_text())
                    if value.get('schema')=='region_run_v1':value=value['case']
                self.apply(value);self.message.setText('已读取保存的输入，可编辑并另存重算；历史结果不变。')
            except (ValueError,KeyError,TypeError,OSError) as e:self.message.setText(str(e))
    def start_run(self):
        if self.process.state()!=C.QProcess.ProcessState.NotRunning:return
        try:
            case=self.collect()
            from run_region import select_backend
            from runtime_config import read_runtime,read_compute_runtime
            select_backend(case,read_compute_runtime(ROOT))
            p,_=W.QFileDialog.getSaveFileName(self,'保存新的运行引用（不得覆盖）','region_run.json','JSON (*.json)')
            if not p:return
            reference=Path(p);input_path=reference.with_name(reference.stem+'.case.json')
            if reference.exists() or input_path.exists():raise ValueError('请为本次运行选用新名称；历史运行及其输入不覆盖。')
            with input_path.open('x') as f:f.write(json.dumps(case,ensure_ascii=False,indent=2)+'\n')
            self.run_reference=reference;self.run_log='';self.run_button.setEnabled(False)
            if self.scene_owner is not None:
                self.region_id=self.scene_owner.store_region_case(case,self.region_id)
                self.scene_run_id=self.scene_owner.register_region_run(case,self.region_id,reference)
            self.message.setText('正在服务器运行；原始输出留在服务器，运行引用保存到 '+str(reference))
            self.process.setWorkingDirectory(str(ROOT));self.process.start(sys.executable,[str(ROOT/'run_region.py'),str(input_path),str(reference)])
        except (ValueError,KeyError,TypeError,OSError) as e:self.message.setText(str(e))
    def run_output(self):
        self.run_log+=bytes(self.process.readAllStandardOutput()).decode('utf-8',errors='replace')
        self.message.setText(self.run_log[-1600:])
    def run_finished(self,code,status):
        self.run_output();self.run_button.setEnabled(True)
        if self.scene_owner is not None and self.scene_run_id:
            try:self.scene_owner.finish_region_run(self.scene_run_id,self.run_reference,code==0 and status==C.QProcess.ExitStatus.NormalExit)
            except (ValueError,KeyError,TypeError,OSError) as exc:
                self.message.setText('运行索引核验失败：'+str(exc));return
        if code==0 and status==C.QProcess.ExitStatus.NormalExit:
            self.message.setText('运行结束，尚需检查结果；用“查看结果包”打开本次运行引用取回预览：\n'+str(self.run_reference))
        else:self.message.setText('运行未成功完成；保留输入、引用和本机日志供检查。\n'+self.run_log[-1200:])
    def run_error(self,error):
        self.run_button.setEnabled(True);self.message.setText('运行进程错误：'+self.process.errorString()+'；请检查已保存的运行引用和本机日志。')
        if error==C.QProcess.ProcessError.FailedToStart and self.scene_owner is not None and self.scene_run_id:
            try:self.scene_owner.finish_region_run(self.scene_run_id,self.run_reference,False)
            except (ValueError,KeyError,TypeError,OSError) as exc:self.message.setText(self.message.text()+'\n索引保存失败：'+str(exc))
    def reject(self):
        if self.process.state()!=C.QProcess.ProcessState.NotRunning:
            self.message.setText('本机计算正在运行，请等待当前任务结束；此窗口保持运行连接。');return
        super().reject()
    def closeEvent(self,event):
        if self.process.state()!=C.QProcess.ProcessState.NotRunning:event.ignore();self.reject()
        else:super().closeEvent(event)
    def save(self):
        try:
            c=self.collect();p,_=W.QFileDialog.getSaveFileName(self,'保存内部区域输入','region.json','JSON (*.json)')
            if p:Path(p).write_text(json.dumps(c,ensure_ascii=False,indent=2)+'\n');self.message.setText('输入已保存。')
        except (ValueError,KeyError,TypeError,OSError) as e:self.message.setText(str(e))
    def export(self):
        try:
            c=self.collect();parent=W.QFileDialog.getExistingDirectory(self,'选择新算例的父目录')
            if not parent:return
            name,ok=W.QInputDialog.getText(self,'新目录名','名称（必须尚不存在）：')
            if not ok:return
            if not name or Path(name).name!=name or name in ('.','..'):raise ValueError('请输入单个目录名称。')
            write(c,Path(parent)/name);self.message.setText('已导出完整DATA和权威case.json；尚未启动计算。')
        except (ValueError,KeyError,TypeError,OSError) as e:self.message.setText(str(e))
