"""Independent mixed-region result player; never interprets pressure as velocity."""
from pathlib import Path
import subprocess
import numpy as np
from PySide6 import QtCore as C,QtWidgets as W
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from plot_toolbar import PlotToolbar
from matplotlib.collections import PolyCollection
from region_result import load,validate_bundle

class RegionResultView(W.QDialog):
    def __init__(self,parent=None,embedded=False):
        super().__init__(parent);self.setWindowTitle('内部区域结果 · 完整单炮与波场');self.resize(1250,900);self.data=None
        if embedded:self.setWindowFlags(C.Qt.WindowType.Widget)
        layout=W.QVBoxLayout(self);self.summary=W.QLabel();self.summary.setWordWrap(True);layout.addWidget(self.summary)
        self.figure=Figure(figsize=(12,8),layout='constrained');self.canvas=FigureCanvasQTAgg(self.figure);layout.addWidget(PlotToolbar(self.canvas,self));layout.addWidget(self.canvas,1)
        row=W.QHBoxLayout();layout.addLayout(row);self.component=W.QComboBox();self.component.addItems(['Vz','Vx']);row.addWidget(self.component)
        self.near_region = W.QCheckBox('洞周近景')
        self.near_region.setToolTip('放大洞体及其周围的固体波场；下方单炮保留全道和全时长。')
        self.near_region.toggled.connect(self.update_extent)
        row.addWidget(self.near_region)
        self.play=W.QPushButton('播放');row.addWidget(self.play);self.slider=W.QSlider(C.Qt.Orientation.Horizontal);row.addWidget(self.slider,1);self.position=W.QLabel();row.addWidget(self.position)
        self.open=W.QPushButton('打开结果包');self.export=W.QPushButton('导出当前视图');row.addWidget(self.open);row.addWidget(self.export)
        self.analysis=W.QPushButton('地表面波窗与频散…');row.addWidget(self.analysis)
        self.analysis.setToolTip('使用完整地表速度记录打开PS/FH分析；压力不参与。理论仅为忽略洞体的背景参考。')
        self.analysis.clicked.connect(self.open_analysis)
        self.timer=C.QTimer(self);self.timer.setInterval(140);self.timer.timeout.connect(self.next_frame)
        self.play.clicked.connect(self.toggle);self.slider.valueChanged.connect(self.frame);self.component.currentIndexChanged.connect(self.draw)
        self.open.clicked.connect(self.open_file);self.export.clicked.connect(self.save_figure)
        for w in [self.play,self.slider,self.export,self.component,self.analysis]:w.setEnabled(False)
        if embedded:
            self.open.hide();self.analysis.hide()
    def open_analysis(self):
        if self.data is None:return
        from region_analysis import RegionAnalysis
        self.timer.stop();self.play.setText('播放')
        dialog=RegionAnalysis(self.data,self)
        dialog.exec()
        dialog.deleteLater()
    def load_path(self,path):
        self.load_data(load(path))
    def load_data(self,candidate):
        meta,mesh=validate_bundle(candidate)
        if meta['schema']=='region_geometry_result_v1':
            from region_geometry_view import GeometryResultView
            GeometryResultView.attach(self,candidate,meta,mesh)
            self.describe_cadence()
            return
        self.general=False
        if hasattr(self,'fluid_selector'):self.fluid_selector.hide()
        self.timer.stop();self.play.setText('播放');self.data=candidate;self.meta=meta;self.actual_mesh=mesh
        self.dry=meta['case']['interior']['kind']=='traction_free'
        for w in [self.play,self.slider,self.export,self.component,self.analysis]:w.setEnabled(True)
        fields='同一步固体速度与流体压力快照，分别取原生节点，排除共享界面。' if 'solid_velocity' in candidate else '仅洞内压力快照；没有固体速度快照。'
        if self.dry:fields='完整地表双分量和实体速度快照(m/s)；无牵引洞内无单元，无声压。'
        self.summary.setText(f"{meta['case']['interior'].get('name','无牵引空洞')} · {Path(meta['remote_run']).name} · 输入摘要 {meta['case_sha256'][:12]}\n{fields}此包不是新输入的验证证书。")
        c=meta['case']['model'];r=meta['case']['rectangle_m'];f=meta['case']['interior']
        fluid='' if self.dry else f"流体 c={f['sound_speed_m_s']:g} m/s, ρ={f['rho_kg_m3']:g} kg/m³; "
        self.summary.setText(self.summary.text()+f"\n固体 Vp/Vs/ρ={c['layers'][0][1:]}; {fluid}区域{r} m; 源 {c['f0_hz']:g} Hz; h={c['element_m']:g} m, dt={c['dt_s']:g} s, Stacey。")
        if c.get('attenuation',{}).get('enabled'):
            q=c['material_properties'][0];att=c['attenuation']
            reference=f"源主频{c['f0_hz']:g} Hz处" if att['velocity_reference']=='source_f0' else '无限频率未松弛'
            self.summary.setText(self.summary.text()+f"\n固体Qκ={q['qkappa']:g}，Qμ={q['qmu']:g}；SLS={att['n_sls']}；输入速度为{reference}值。Q空洞为试验扩展。")
        with C.QSignalBlocker(self.slider):self.slider.setRange(0,len(candidate['field_t'])-1);self.slider.setValue(len(candidate['field_t'])//3)
        self.draw()
        self.describe_cadence()

    def describe_cadence(self):
        times = self.data['field_t']
        if len(times) < 2:
            return
        gap = float(np.max(np.diff(times)))
        frequency = self.meta['case']['model']['f0_hz']
        message = f'最大帧间隔 {1000*gap:g} ms，震源主周期 {1000/frequency:g} ms。'
        if gap * frequency >= 0.5:
            message += '快照过疏，播放可能出现波峰跳动或表观倒退。'
        self.summary.setText(self.summary.text() + '\n' + message)

    def update_extent(self):
        if self.data is None:
            return
        from region_geometry import bounds
        case = self.meta['case']
        boxes = [bounds(r) for r in case['regions']] if 'regions' in case else [case['rectangle_m']]
        points = self.actual_mesh.points
        xmin, zmin = points.min(axis=0)
        xmax, zmax = points.max(axis=0)
        self.near_region.setEnabled(bool(boxes))
        if boxes and self.near_region.isChecked():
            limits = np.asarray(boxes)
            x0, z0 = limits.min(axis=0)[[0, 2]]
            x1, z1 = limits.max(axis=0)[[1, 3]]
            pad = max(x1-x0, z1-z0)
            xmin, xmax = max(xmin,x0-pad), min(xmax,x1+pad)
            zmin = max(zmin,z0-pad)
        ax = self.axes[0,0]
        ax.set(xlim=(xmin,xmax), ylim=(zmin,zmax+3))
        if self.dry:
            self.axes[0,1].set(xlim=ax.get_xlim(), ylim=ax.get_ylim())
        self.canvas.draw_idle()

    def draw(self,*_):
        if self.data is None:return
        if getattr(self,'general',False):
            from region_geometry_view import GeometryResultView
            return GeometryResultView.draw(self,*_)
        a=self.data;case=self.meta['case'];c=case['model'];mesh=self.actual_mesh;self.figure.clear()
        self.axes=self.figure.subplots(2,2);whole,zoom,gather,pressure=self.axes.ravel()
        colors=np.where(mesh.materials[:,None]==2,np.array([[.65,.85,1.]]),np.array([[.93,.9,.83]]))
        whole.add_collection(PolyCollection(mesh.points[mesh.cells[:,:4]],facecolors=colors,edgecolors='#728080',linewidths=.15))
        whole.scatter([c['shot_x_m']],[-c['shot_depth_m']],marker='*',c='red',s=60)
        whole.scatter(a['x'],a['z'],s=4,c='orange')
        if not self.dry:whole.scatter(*a['pressure_xy'],marker='x',c='blue',s=35)
        whole.set(xlim=(0,c['width_m']),ylim=(-c['layers'][0][0],3),xlabel='x (m)',ylabel='z (m)',title='Actual mesh / source / receivers',aspect='equal')
        self.solid_artist=None
        if 'solid_velocity' in a:
            component=1 if self.component.currentText()=='Vz' else 0
            p=a['solid_points'];v=a['solid_velocity'][:,:,component];scale=max(float(np.max(abs(v))),np.finfo(float).tiny)
            self.solid_artist=whole.scatter(p[:,0],p[:,1],c=v[0],s=2,cmap='RdBu_r',vmin=-scale,vmax=scale,zorder=1.5)
            self.figure.colorbar(self.solid_artist,ax=whole,label=f'Solid {self.component.currentText()} (m/s); fixed scale')
        p=a['field_points'];self.field_values=a['field_velocity'][:,:,1 if self.component.currentText()=='Vz' else 0] if self.dry else a['field_pressure']
        scale=max(float(np.max(abs(self.field_values))),np.finfo(float).tiny)
        self.artist=zoom.scatter(p[:,0],p[:,1],c=self.field_values[0],s=2 if self.dry else 8,cmap='RdBu_r',vmin=-scale,vmax=scale)
        x0,x1,z0,z1=case['rectangle_m']
        zoom.set(xlim=(0,c['width_m']) if self.dry else (x0,x1),ylim=(-c['layers'][0][0],3) if self.dry else (z0,z1),aspect='equal',xlabel='x (m)',ylabel='z (m)')
        self.figure.colorbar(self.artist,ax=zoom,label=f'Solid {self.component.currentText()} (m/s); fixed scale' if self.dry else 'Fluid pressure (Pa); fixed scale')
        values=a[self.component.currentText().lower()];clip=max(float(np.percentile(abs(values),99)),np.finfo(float).tiny)
        gather.pcolormesh(a['x'],a['t'],values,shading='nearest',cmap='RdBu_r',vmin=-clip,vmax=clip)
        gather.set(xlabel='Receiver x (m)',ylabel='Time (s)',title=f'Full {self.component.currentText()} (m/s); display clip: 99th percentile',ylim=(a['t'][-1],a['t'][0]))
        if self.dry:
            other='vx' if self.component.currentText()=='Vz' else 'vz';v=a[other];limit=max(float(np.percentile(abs(v),99)),np.finfo(float).tiny)
            pressure.pcolormesh(a['x'],a['t'],v,shading='nearest',cmap='RdBu_r',vmin=-limit,vmax=limit)
            pressure.set(xlabel='Receiver x (m)',ylabel='Time (s)',title=f'Full {other} (m/s); display clip: 99th percentile',ylim=(a['t'][-1],a['t'][0]))
            self.pressure_line=pressure.axhline(a['field_t'][0],color='orange')
        else:
            pressure.plot(a['pressure_t'],a['pressure'],lw=1);pressure.set(xlabel='Time (s)',ylabel='Pressure (Pa)',title='Full cavity-center pressure')
            self.pressure_line=pressure.axvline(a['field_t'][0],color='orange')
        self.gather_line=gather.axhline(a['field_t'][0],color='orange')
        self.update_extent()
        self.frame(self.slider.value())
    def frame(self,i):
        if self.data is None:return
        a=self.data;t=a['field_t'][i];self.artist.set_array(self.field_values[i]);self.axes[0,1].set_title(f'Native {"solid" if self.dry else "fluid"} nodes / t={t:.5f} s')
        if self.solid_artist is not None:
            self.solid_artist.set_array(a['solid_velocity'][i,:,1 if self.component.currentText()=='Vz' else 0])
            self.axes[0,0].set_title(f'Actual mesh + solid {self.component.currentText()} / t={t:.5f} s')
        self.gather_line.set_ydata([t,t])
        if self.dry:self.pressure_line.set_ydata([t,t])
        else:self.pressure_line.set_xdata([t,t])
        self.position.setText(f"{i+1}/{len(a['field_t'])} · {t:.5f} s");self.canvas.draw_idle()
    def toggle(self):
        if self.timer.isActive():self.timer.stop();self.play.setText('播放')
        else:
            if self.slider.value()==self.slider.maximum():self.slider.setValue(0)
            self.timer.start();self.play.setText('暂停')
    def next_frame(self):
        if self.slider.value()==self.slider.maximum():self.timer.stop();self.play.setText('播放')
        else:self.slider.setValue(self.slider.value()+1)
    def open_file(self):
        p,_=W.QFileDialog.getOpenFileName(self,'打开内部区域结果或运行引用','','内部区域结果 (*.npz *.json)')
        if p:
            try:
                if Path(p).suffix.lower()=='.json':
                    from run_region import fetch
                    target,_=W.QFileDialog.getSaveFileName(self,'保存取回的小型预览',Path(p).stem+'.npz','NPZ (*.npz)')
                    if not target:return
                    p=str(fetch(p,target))
                self.load_path(p)
            except (ValueError,KeyError,TypeError,OSError,subprocess.SubprocessError) as e:W.QMessageBox.warning(self,'结果未载入',str(e))
    def save_figure(self):
        p,_=W.QFileDialog.getSaveFileName(self,'保存当前视图','region_result.png','PNG (*.png)')
        if p:self.figure.savefig(p,dpi=145)
    def closeEvent(self,event):self.timer.stop();super().closeEvent(event)
    def reject(self):self.timer.stop();super().reject()
