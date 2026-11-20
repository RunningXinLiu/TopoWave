"""Editable ISO geometry experiments sharing the existing run/history lifecycle."""
import copy
import json
from pathlib import Path
import numpy as np
from PySide6 import QtCore as C,QtWidgets as W
from region_editor import RegionEditor,ROOT
from region_case import validate,write
from fluid_cavity import PRESETS


class GeometryEditor(RegionEditor):
    columns=('id','shape','fill','cx','cz','width','height','radius','patch','name','rho','c','reference')
    def __init__(self,parent=None,scene_owner=None,entry=None):
        W.QDialog.__init__(self,parent);self.setWindowTitle('圆洞、多洞与地形 · ISO单炮试验');self.resize(1150,850)
        self.scene_owner=scene_owner;self.region_id=entry['id'] if entry else None;self.scene_run_id=None
        layout=W.QVBoxLayout(self);intro=W.QLabel('无损 ISO、Stacey；多层围岩使用水平地下界面。可设置多个矩形/圆形无牵引区或无黏流体区；洞体与网格过渡区须避开地表变形。\n每个模板的数值证据单独登记；任意编辑不继承通过结论。Q请使用单矩形入口，TI空洞不支持。');intro.setWordWrap(True);layout.addWidget(intro)
        chooser=W.QHBoxLayout();layout.addLayout(chooser);self.template=W.QComboBox()
        self.template.setPlaceholderText('自定义输入')
        for key,label in [('circle_dry','圆形干洞'),('circle_water','圆形水区'),('multi_dry','两个干洞'),('multi_fluid','空气区＋水区'),('hill_dry','地形＋干洞'),('hill_water','地形＋水区')]:self.template.addItem(label,key)
        chooser.addWidget(self.template);b=W.QPushButton('载入所选模板');b.clicked.connect(self.load_template);chooser.addWidget(b)
        tabs=W.QTabWidget();layout.addWidget(tabs,1);self.fields={}
        items=[('width_m','模型宽度 (m)'),('depth','第一层厚度 (m)'),('vp','Vp (m/s)'),('vs','Vs (m/s)'),('rho','密度 (kg/m³)'),('shot_x_m','炮点x (m)'),('shot_depth_m','炮点埋深 (m)'),('f0_hz','主频 (Hz)'),('source_t0_s','源预移 (s)'),('first_x_m','首道x (m)'),('spacing_m','道距 (m)'),('receivers','道数'),('receiver_depth_m','接收器埋深 (m)'),('element_m','背景单元边长 (m)'),('dt_s','dt (s)'),('nstep','步数'),('sample_stride','记录间隔 (步)'),('snapshot_stride','快照间隔 (步)')]
        for title,part in [('模型、源与接收',items[:13]),('数值设置',items[13:])]:
            page=W.QWidget();form=W.QFormLayout(page);tabs.addTab(page,title)
            for k,label in part:self.fields[k]=W.QLineEdit();form.addRow(label,self.fields[k])
        page=W.QWidget();form=W.QFormLayout(page);tabs.addTab(page,'深部地层')
        self.deep_layers=W.QPlainTextEdit();form.addRow('自第二层起每行填写厚度(m)、Vp、Vs(m/s)、密度(kg/m³)',self.deep_layers)
        page=W.QWidget();box=W.QVBoxLayout(page);tabs.addTab(page,'内部区域')
        note=W.QLabel('形状分别使用：矩形＝中心/宽/高；圆形＝中心/半径/过渡半宽。后者控制贴体网格，须大于半径。\n流体需填写名称、正密度、声速及参考条件；无牵引区域不使用流体参数。边界/过渡区须对齐背景网格。');note.setWordWrap(True);box.addWidget(note)
        self.regions=W.QTableWidget(0,len(self.columns));self.regions.setHorizontalHeaderLabels(['ID','形状','内部','中心x m','中心z m','宽 m','高 m','半径 m','过渡半宽 m','流体名称','ρ kg/m³','c m/s','参考条件']);self.regions.setAlternatingRowColors(True);box.addWidget(self.regions)
        buttons=W.QHBoxLayout();box.addLayout(buttons)
        for label,fn in [('添加矩形',lambda:self.add_region('rectangle')),('添加圆形',lambda:self.add_region('circle')),('删除选中区域',self.remove_region),('选中区填入空气',lambda:self.fill_preset('air')),('选中区填入水',lambda:self.fill_preset('water'))]:
            b=W.QPushButton(label);b.clicked.connect(fn);buttons.addWidget(b)
        page=W.QWidget();form=W.QFormLayout(page);tabs.addTab(page,'地表')
        self.surface=W.QPlainTextEdit();self.surface.setPlaceholderText('平地留空。起伏地表每行填写 x 高程，单位m；覆盖整个模型宽度。');form.addRow('地表控制点',self.surface)
        self.interpolation=W.QComboBox();self.interpolation.addItem('分段直线','linear');self.interpolation.addItem('夹持三次样条','spline');form.addRow('地表插值',self.interpolation)
        self.warp=W.QLineEdit();form.addRow('网格变形层深度 (m；平地留空)',self.warp)
        self.message=W.QLabel();self.message.setWordWrap(True);layout.addWidget(self.message)
        row=W.QHBoxLayout();layout.addLayout(row)
        for label,fn in [('打开输入',self.open_input),('检查输入',self.check),('预览待提交网格',self.preview_mesh),('查看验证证据',self.show_evidence),('保存输入',self.save),('导出算例',self.export),('查看结果',self.open_result)]:
            b=W.QPushButton(label);b.clicked.connect(fn);row.addWidget(b)
        if scene_owner is not None:
            b=W.QPushButton('保存当前区域方案到场景');b.clicked.connect(self.attach_to_scene);layout.addWidget(b)
        self.run_button=W.QPushButton('保存并在本机运行');self.run_button.clicked.connect(self.start_run);layout.addWidget(self.run_button)
        self.process=C.QProcess(self);self.process.setProcessChannelMode(C.QProcess.ProcessChannelMode.MergedChannels);self.process.readyReadStandardOutput.connect(self.run_output);self.process.finished.connect(self.run_finished);self.process.errorOccurred.connect(self.run_error)
        self.run_log='';self.run_reference=None
        self.apply(entry['case'] if entry else json.loads((ROOT/'examples/cavity/geometry/circle_dry.json').read_text()))

    def set_row(self,i,r):
        self.regions.insertRow(i);s=r['shape'];f=r['interior'];values=dict(id=r['id'])
        if s['kind']=='rectangle':
            x0,x1,z0,z1=s['bounds_m'];values.update(cx=(x0+x1)/2,cz=(z0+z1)/2,width=x1-x0,height=z1-z0)
        else:values.update(cx=s['center_m'][0],cz=s['center_m'][1],radius=s['radius_m'],patch=s['patch_halfwidth_m'])
        if f['kind']=='fluid':values.update(name=f['name'],rho=f['rho_kg_m3'],c=f['sound_speed_m_s'],reference=f['reference'])
        for j,k in enumerate(self.columns):
            if k in ('shape','fill'):
                w=W.QComboBox()
                for label,value in ([('矩形','rectangle'),('圆形','circle')] if k=='shape' else [('无牵引','traction_free'),('流体','fluid')]):w.addItem(label,value)
                w.setCurrentIndex(w.findData(s['kind'] if k=='shape' else f['kind']));self.regions.setCellWidget(i,j,w)
            else:self.regions.setItem(i,j,W.QTableWidgetItem(str(values.get(k,''))))
        self.regions.resizeColumnsToContents()

    def apply(self,case):
        if case.get('schema')!='region_geometry_v1':raise ValueError('此窗口只接收圆洞/多区域/地形输入；矩形旧格式请用原入口。')
        validate(case);self.base=copy.deepcopy(case);m=case['model'];values=dict(m,**dict(zip(('depth','vp','vs','rho'),m['layers'][0])))
        for k,w in self.fields.items():w.setText(str(values[k]))
        self.deep_layers.setPlainText('\n'.join(' '.join(map(str,row)) for row in m['layers'][1:]))
        self.regions.setRowCount(0)
        for i,r in enumerate(case['regions']):self.set_row(i,r)
        self.surface.setPlainText('\n'.join(f'{x} {z}' for x,z in m['surface']));self.warp.setText('' if case['surface_warp_depth_m'] is None else str(case['surface_warp_depth_m']));self.interpolation.setCurrentIndex(self.interpolation.findData(case.get('surface_interpolation','spline')))
        self.message.setText('已载入完整实验输入；编辑不会改变历史记录，也不会自动启动计算。')
        self.template.setCurrentIndex(-1)
        for i in range(self.template.count()):
            if json.loads((ROOT/'examples/cavity/geometry'/(self.template.itemData(i)+'.json')).read_text())==case:self.template.setCurrentIndex(i);break

    def collect(self):
        c=copy.deepcopy(self.base);m=c['model'];integers={'receivers','nstep','sample_stride','snapshot_stride'}
        v={k:(int(w.text()) if k in integers else float(w.text())) for k,w in self.fields.items()}
        for k in m.keys()&v.keys():
            if m[k]!=v[k]:m[k]=v[k]
        layer=[[v[k] for k in ('depth','vp','vs','rho')]]
        layer.extend([float(x) for x in line.split()] for line in self.deep_layers.toPlainText().splitlines() if line.strip())
        if m['layers']!=layer:m['layers']=layer
        regions=[]
        for i in range(self.regions.rowCount()):
            row={k:self.regions.cellWidget(i,j).currentData() if k in ('shape','fill') else self.regions.item(i,j).text().strip() for j,k in enumerate(self.columns)}
            x,z=float(row['cx']),float(row['cz'])
            if row['shape']=='rectangle':
                w,h=float(row['width']),float(row['height']);s=dict(kind='rectangle',bounds_m=[x-w/2,x+w/2,z-h/2,z+h/2])
            else:s=dict(kind='circle',center_m=[x,z],radius_m=float(row['radius']),patch_halfwidth_m=float(row['patch']))
            f=dict(kind=row['fill'])
            if f['kind']=='fluid':f.update(name=row['name'],rho_kg_m3=float(row['rho']),sound_speed_m_s=float(row['c']),reference=row['reference'])
            regions.append(dict(id=row['id'],shape=s,interior=f))
        if regions!=c['regions']:c['regions']=regions
        surface=[list(map(float,line.replace(',',' ').split())) for line in self.surface.toPlainText().splitlines() if line.strip()]
        if surface!=m['surface']:m['surface']=surface
        if (surface!=self.base['model']['surface'] or len(layer)!=len(self.base['model']['layers'])):
            m['interface_relief']=[[-p[1] for p in surface] for _ in layer] if surface and len(layer)>1 else []
        depth=float(self.warp.text()) if self.warp.text().strip() else None
        if depth!=c['surface_warp_depth_m']:c['surface_warp_depth_m']=depth
        interpolation=self.interpolation.currentData()
        if 'surface_interpolation' in c or interpolation!='spline':c['surface_interpolation']=interpolation
        validate(c);return copy.deepcopy(self.base) if c==self.base else c

    def add_region(self,shape):
        ids={self.regions.item(i,0).text() for i in range(self.regions.rowCount())};n=1
        while 'r'+str(n) in ids:n+=1
        s=dict(kind='rectangle',bounds_m=[280,296,-48,-32]) if shape=='rectangle' else dict(kind='circle',center_m=[288,-40],radius_m=8,patch_halfwidth_m=10)
        self.set_row(self.regions.rowCount(),dict(id='r'+str(n),shape=s,interior=dict(kind='traction_free')));self.regions.selectRow(self.regions.rowCount()-1);self.message.setText('已添加可编辑候选；请检查位置、间距及网格对齐。')

    def remove_region(self):
        i=self.regions.currentRow()
        if i>=0:self.regions.removeRow(i)

    def fill_preset(self,name):
        i=self.regions.currentRow()
        if i<0:self.message.setText('请先选中一个区域。');return
        f=PRESETS[name];self.regions.cellWidget(i,2).setCurrentIndex(1)
        for col,key in [(9,'name'),(10,'rho_kg_m3'),(11,'sound_speed_m_s'),(12,'reference')]:self.regions.item(i,col).setText(str(f[key]))

    def load_template(self):
        try:self.apply(json.loads((ROOT/'examples/cavity/geometry'/(self.template.currentData()+'.json')).read_text()))
        except (ValueError,KeyError,TypeError,OSError) as exc:self.message.setText(str(exc))

    def export(self):
        try:
            case=self.collect();parent=W.QFileDialog.getExistingDirectory(self,'选择父目录（在其下新建region_case）')
            if parent:write(case,Path(parent)/'region_case');self.message.setText('已导出完整输入；原目录不覆盖，未启动计算。')
        except (ValueError,KeyError,TypeError,OSError) as exc:self.message.setText(str(exc))

    def preview_mesh(self):
        try:
            from region_geometry import build
            from matplotlib.figure import Figure
            from matplotlib.collections import PolyCollection
            from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
            from plot_toolbar import PlotToolbar
            case=self.collect();points,cells,mats,_,_,qc=build(case);d=W.QDialog(self);d.setWindowTitle('当前输入 · 实际待提交几何');d.resize(1100,650);layout=W.QVBoxLayout(d)
            note=W.QLabel(f"{len(cells)}单元；最小Jacobian={qc['jacobian_min_m2']:.6g} m²，最小scaled Jacobian={qc['scaled_jacobian_min']:.4g}。拓扑通过不等于波形精度通过。");note.setWordWrap(True);layout.addWidget(note)
            fig=Figure(figsize=(11,5),layout='constrained');canvas=FigureCanvasQTAgg(fig);layout.addWidget(PlotToolbar(canvas,d));layout.addWidget(canvas);axs=fig.subplots(1,2)
            from region_geometry import bounds
            boxes=np.array([bounds(r,True) for r in case['regions']]);coords=np.array(qc['mesh_acquisition_xz_m'])
            for ax in axs:
                ax.add_collection(PolyCollection(points[cells],array=mats,cmap='viridis',edgecolors='#6f7f7f',linewidths=.2));ax.autoscale();ax.set(aspect='equal',xlabel='x (m)',ylabel='z (m)');ax.scatter(*coords[0],c='red',marker='*',s=70);ax.scatter(coords[1:,0],coords[1:,1],s=5,c='orange')
            if len(boxes):axs[1].set(xlim=(boxes[:,0].min()-10,boxes[:,1].max()+10),ylim=(boxes[:,2].min()-10,max(points[:,1].max()+3,boxes[:,3].max()+10)))
            canvas.draw();d.exec()
        except (ValueError,KeyError,TypeError,OSError) as exc:self.message.setText(str(exc))
