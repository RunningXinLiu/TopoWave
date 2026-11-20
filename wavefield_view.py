"""Native snapshot player linked to the same shot's receiver time axis."""
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from plot_toolbar import PlotToolbar
from matplotlib.figure import Figure
from matplotlib import colormaps
from matplotlib.colors import Normalize, ListedColormap
from matplotlib.cm import ScalarMappable
from PySide6 import QtCore as C, QtWidgets as W

from model import elevation
from materials import kind as material_kind, attenuating
from native_mesh import draw_mesh


class WavefieldView(W.QWidget):
    def __init__(self):
        super().__init__()
        self.gather = None
        self.figure = Figure(layout="constrained",facecolor="#fbfcfa")
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.toolbar = PlotToolbar(self.canvas,self)
        self.toolbar.setToolTip("框选放大或平移查看真实网格；Home恢复全域。导航模式下点击道集不会改变帧时间。")
        self.summary = W.QLabel()
        self.quantity=W.QComboBox()
        self.quantity.setToolTip('声弹耦合时可切换固体Vz(m/s)或流体声压(Pa)，各用独立全时段色标；排除另一介质与共享界面，不跨界面插值。右侧始终为完整固体单炮。')
        self.quantity.currentIndexChanged.connect(self.update_quantity)
        self.aspect_mode=W.QComboBox()
        self.aspect_mode.addItem('真实比例','equal')
        self.aspect_mode.addItem('纵向拉伸显示','auto')
        self.aspect_mode.setToolTip('真实比例保持x、z单位长度相同。纵向拉伸仅适配画布，坐标仍为m，不改变网格、波形或振幅；标题会标明拉伸。薄自由板默认采用拉伸，普通地基默认真实比例。')
        self.aspect_mode.currentIndexChanged.connect(self.update_aspect)
        self.grid_toggle = W.QCheckBox("实际谱元网格")
        self.grid_toggle.setChecked(True)
        self.grid_toggle.toggled.connect(self.toggle_grid)
        self.grid_artist = None
        self.structure_artists = []
        self.structure_bar = None
        self.structure = W.QComboBox()
        self.structure.addItem("不叠加速度",None)
        self.structure.addItem("Vs 速度结构","Vs")
        self.structure.addItem("Vp 速度结构","Vp")
        self.structure.setToolTip("使用当前已载入单炮保存的速度模型与起伏层界，单位m/s；不是尚未运行的新设计。灰度表示材料速度，红蓝表示波场。")
        self.structure.currentIndexChanged.connect(self.update_structure)
        self.opacity = W.QSpinBox()
        self.opacity.setRange(0,100);self.opacity.setValue(85);self.opacity.setSuffix(" %")
        self.opacity.setToolTip("仅改变显示。不叠加结构时为统一不透明度；叠加时零振幅透明，随绝对振幅增强至此上限，以露出背景。全程采用同一振幅尺度；弱信号可能不显眼。")
        self.opacity.valueChanged.connect(self.update_wave_alpha)
        self.summary.setWordWrap(True)
        self.slider = W.QSlider(C.Qt.Orientation.Horizontal)
        self.position = W.QLabel()
        self.play = W.QPushButton("播放")
        self.save = W.QPushButton("导出当前帧…")
        self.timer = C.QTimer(self)
        self.timer.setInterval(130)
        self.timer.timeout.connect(self.next_frame)
        self.play.clicked.connect(self.toggle)
        self.slider.valueChanged.connect(self.show_frame)
        self.save.clicked.connect(self.export)
        self.canvas.mpl_connect("button_press_event",self.pick_time)
        layout = W.QVBoxLayout(self)
        header = W.QHBoxLayout()
        header.addWidget(self.summary,1)
        header.addWidget(self.grid_toggle)
        layout.addLayout(header)
        layers = W.QHBoxLayout()
        layers.addWidget(W.QLabel('场量'));layers.addWidget(self.quantity)
        layers.addWidget(W.QLabel("速度背景"));layers.addWidget(self.structure)
        layers.addWidget(W.QLabel("波场不透明度"));layers.addWidget(self.opacity)
        layers.addWidget(W.QLabel("显示比例"));layers.addWidget(self.aspect_mode);layers.addStretch()
        layout.addLayout(layers)
        layout.addWidget(self.toolbar)
        layout.addWidget(self.canvas,1)
        controls = W.QHBoxLayout()
        for widget in (self.play,self.slider,self.position,self.save):
            controls.addWidget(widget)
        layout.addLayout(controls)
        self.status = W.QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.ax_gather = None

    def load(self,g):
        self.stop()
        regional=g is not None and g.region_data is not None
        if not hasattr(self,'region_view'):self.region_view=None
        if regional and self.region_view is None:
            from region_result_view import RegionResultView
            self.region_view=RegionResultView(self,embedded=True)
            self.layout().addWidget(self.region_view,1)
        def visible_widgets(layout,visible):
            for i in range(layout.count()):
                item=layout.itemAt(i)
                if item.widget() is not None and item.widget() is not self.region_view:item.widget().setVisible(visible)
                elif item.layout() is not None:visible_widgets(item.layout(),visible)
        visible_widgets(self.layout(),not regional)
        if self.region_view is not None:
            self.region_view.timer.stop();self.region_view.setVisible(regional)
        if regional:
            self.gather=g;self.region_view.load_data(g.region_data)
            return
        self.gather = g
        f = g.field if g is not None else None
        self.figure.clear()
        self.toolbar.update()
        self.ax_gather = None
        self.grid_artist = None
        self.structure_artists = []
        self.structure_bar = None
        self.structure_model = (g.meta.get("model") or g.meta.get("geometry_model")) if g is not None else None
        with C.QSignalBlocker(self.quantity):
            self.quantity.clear();self.quantity.addItem('固体 Vz' if (self.structure_model or {}).get('physics')=='coupled' else '原始场量','primary')
            if g is not None and g.pressure_field is not None:self.quantity.addItem('流体声压 p','fluid_pressure')
        self.quantity.setEnabled(self.quantity.count()>1)
        ti=bool(self.structure_model and material_kind(self.structure_model)!='isotropic')
        self.structure.setItemText(1,'Vs0 对称轴速度结构' if ti else 'Vs 速度结构')
        self.structure.setItemText(2,'Vp0 对称轴速度结构' if ti else 'Vp 速度结构')
        self.structure.setToolTip('TI显示沿材料对称轴的Vp0/Vs0，不是所有传播方向的相速度；衰减材料按保存的速度参考定义。')
        has_structure = f is not None and bool(self.structure_model and self.structure_model.get("layers"))
        self.structure.setEnabled(has_structure)
        self.opacity.setEnabled(f is not None)
        self.aspect_mode.setEnabled(f is not None)
        with C.QSignalBlocker(self.aspect_mode):
            self.aspect_mode.setCurrentIndex(1 if (self.structure_model or {}).get('domain_kind')=='free_plate' else 0)
        with C.QSignalBlocker(self.structure):
            self.structure.setCurrentIndex((2 if g.meta["component"]=="pressure" else 1) if has_structure else 0)
        with C.QSignalBlocker(self.grid_toggle):
            self.grid_toggle.setChecked((self.structure_model or {}).get('domain_kind')!='free_plate')
        self.grid_toggle.setEnabled(f is not None and g.mesh is not None)
        self.grid_toggle.setToolTip("来自本炮 mesh_materials.vtk 的单元边界" if g is not None and g.mesh is not None
                                    else "这份旧记录未保存实际网格")
        for widget in (self.play,self.save,self.slider):
            widget.setEnabled(f is not None)
        self.position.clear()
        if g is None:
            self.summary.setText("波场快照")
            self.status.setText("在模型页运行主动源单炮，或读入带波场快照的模板。")
            self.canvas.draw_idle()
            return
        component = "竖向速度 Vz" if g.meta["component"]=="Vz" else "压力 p"
        if (self.structure_model or {}).get('physics')=='coupled':component='固体 Vz / 流体声压 · 同次声弹耦合计算'
        self.summary.setText(f"{component}   ·   炮点 x = {g.meta['source_x']:g} m   ·   {Path(g.meta['run']).name}")
        if g.mesh is not None:
            self.summary.setText(self.summary.text()+f"   ·   {len(g.mesh.cells):,} 个谱元")
        if f is None:
            self.status.setText("该旧记录只保存了接收波形。可打开自带弹性示例查看快照，也可用原方案重新运行。")
            self.canvas.draw_idle()
            return
        self.ax,self.ax_gather = self.figure.subplots(1,2,gridspec_kw={"width_ratios":[1.6,1]})
        x = np.broadcast_to(f.x,f.z.shape)
        scale = float(np.percentile(np.abs(f.values),99.5)) or float(np.max(np.abs(f.values))) or 1
        self.mesh = self.ax.pcolormesh(x,f.z,f.values[0],shading="gouraud",cmap="RdBu_r",
                                       vmin=-scale,vmax=scale,zorder=1)
        if g.mesh is not None:
            self.grid_artist = draw_mesh(self.ax,g.mesh)
            self.grid_artist.set_visible(self.grid_toggle.isChecked())
        self.ax.plot(f.x,f.z[-1],lw=1,color="#2b5948")
        c = g.meta.get("model") or g.meta.get("geometry_model")
        if c:
            for j in range(1,len(c["layers"])):
                self.ax.plot(f.x,elevation(c,f.x,j),color="#56685e",alpha=.65,lw=.65,ls="--")
            sx = g.meta["source_x"]
            self.ax.scatter(g.x,elevation(c,g.x)-c["receiver_depth_m"],s=8,marker="v",color="#11644d",zorder=4)
            self.ax.scatter([sx],[float(elevation(c,sx))-c["shot_depth_m"]],s=68,marker="*",color="#bf651c",zorder=5)
        self.ax.set(xlabel="Horizontal position x (m)",ylabel="Elevation z (m)",aspect=self.aspect_mode.currentData())
        unit = "Vz (m/s)" if g.meta["component"]=="Vz" else "Pressure (solver units)"
        self.field_bar=self.figure.colorbar(self.mesh,ax=self.ax,location="bottom",shrink=.72,pad=.12,label=unit)
        clip = max(float(np.percentile(np.abs(g.traces),99)),np.finfo(float).tiny)
        self.ax_gather.pcolormesh(g.x,g.t,g.traces,shading="nearest",cmap="RdBu_r",vmin=-clip,vmax=clip)
        self.ax_gather.set(xlabel="Receiver x (m)",ylabel="Time (s)",title="Shot gather")
        self.ax_gather.set_ylim(max(g.t[-1],f.times[-1]),min(g.t[0],f.times[0]))
        self.time_line = self.ax_gather.axhline(f.times[0],color="#d17a00",lw=1.7)
        # Start on a developed wavefront. Preserve a common scale throughout playback.
        self.slider.blockSignals(True)
        self.slider.setRange(0,len(f.times)-1)
        self.slider.setValue(len(f.times)//3)
        self.slider.blockSignals(False)
        self.status.setText(f"{len(f.times)} 帧  ·  拖动时间条或点击右图选时刻  ·  虚线为层界，三角为检波点，星号为炮点。仅显示抽取的 GLL 节点，全时段共用 |振幅| 第 99.5 百分位色标。")
        sampling=g.meta.get('wavefield_sampling',{})
        if sampling:self.status.setText(self.status.text()+f"\n波场预览{sampling['preview_columns']}列；最大水平间隔{sampling['max_horizontal_gap_m']:g} m。薄板全域网格可能遮住波场，可放大后勾选网格。")
        if g.meta.get("preview_note"):
            self.status.setText(self.status.text()+"\n"+g.meta["preview_note"])
        self.update_structure()
        self.update_quantity()
        self.show_frame(self.slider.value())

    def selected_field(self):
        f=self.gather.pressure_field if self.quantity.currentData()=='fluid_pressure' else self.gather.field
        c=self.structure_model or {};mask=np.zeros(f.z.shape,dtype=bool)
        if c.get('physics')=='coupled':
            top=-c['layers'][0][0];bottom=top-c['layers'][1][0]
            tol=max(1.,float(abs(f.z).max()))*np.finfo(np.float32).eps*4
            keep=(f.z>bottom+tol)&(f.z<top-tol) if self.quantity.currentData()=='fluid_pressure' else (f.z>top+tol)|(f.z<bottom-tol)
            mask=~keep
        return f,mask

    def update_quantity(self,*_):
        if self.gather is None or self.gather.field is None or self.ax_gather is None:return
        f,mask=self.selected_field();v=abs(f.values[:,~mask])
        scale=float(np.percentile(v,99.5)) or float(v.max()) or 1.
        self.mesh.set_clim(-scale,scale)
        self.field_bar.set_label('Fluid pressure (Pa)' if self.quantity.currentData()=='fluid_pressure' else ('Solid Vz (m/s)' if (self.structure_model or {}).get('physics')=='coupled' else ('Pressure (solver units)' if self.gather.meta['component']=='pressure' else 'Vz (m/s)')))
        self.show_frame(self.slider.value())

    def update_structure(self,*_):
        if self.gather is None or self.gather.field is None or self.ax_gather is None:
            return
        limits=(self.ax.get_xlim(),self.ax.get_ylim())
        for artist in self.structure_artists:artist.remove()
        self.structure_artists=[]
        if self.structure_bar is not None:
            self.structure_bar.remove();self.structure_bar=None
        kind=self.structure.currentData()
        c=self.structure_model
        if kind and c:
            column=2 if kind=="Vs" else 1
            speeds=np.asarray(c["layers"],dtype=float)[:,column]
            low,high=float(speeds.min()),float(speeds.max())
            pad=max(1.,abs(low)*.01) if low==high else 0
            norm=Normalize(low-pad,high+pad)
            cmap=ListedColormap(colormaps["Greys"](np.linspace(.12,.48,256)))
            x=np.unique(np.r_[self.gather.field.x,np.linspace(0,c["width_m"],1001)])
            for i,speed in enumerate(speeds):
                artist=self.ax.fill_between(x,elevation(c,x,i+1),elevation(c,x,i),
                                            color=cmap(norm(speed)),zorder=0)
                self.structure_artists.append(artist)
            self.structure_bar=self.figure.colorbar(ScalarMappable(norm=norm,cmap=cmap),ax=self.ax,
                                                   location="right",shrink=.65,pad=.02,label=f"{kind}{'0 (axis)' if material_kind(c)!='isotropic' else ''} (m/s)")
            if low==high:self.structure_bar.set_ticks([low])
        self.ax.set_xlim(limits[0]);self.ax.set_ylim(limits[1])
        self.update_wave_alpha()

    def update_wave_alpha(self,*_):
        if self.gather is None or self.gather.field is None or self.ax_gather is None:
            return
        rgba=colormaps["RdBu_r"](np.linspace(0,1,257))
        alpha=self.opacity.value()/100
        rgba[:,3]=alpha*(np.abs(np.linspace(-1,1,257)) if self.structure_artists else 1)
        self.mesh.set_cmap(ListedColormap(rgba))
        self.canvas.draw_idle()

    def toggle_grid(self,visible):
        if self.grid_artist is not None:
            self.grid_artist.set_visible(visible)
            self.canvas.draw_idle()

    def update_aspect(self,*_):
        if self.gather is None or self.gather.field is None or self.ax_gather is None:return
        self.ax.set_aspect(self.aspect_mode.currentData())
        self.show_frame(self.slider.value())

    def show_frame(self,i):
        if self.gather is None or self.gather.field is None:
            return
        f,mask = self.selected_field()
        # Gouraud rendering needs the mask in the same 2-D shape as its vertex
        # colors. Flattening can fail at draw time in recent Matplotlib builds.
        self.mesh.set_array(np.ma.array(f.values[i],mask=mask))
        quantity='Fluid pressure (Pa)' if self.quantity.currentData()=='fluid_pressure' else self.gather.meta['component']
        self.ax.set_title(f"{quantity}   /   t = {f.times[i]:.4f} s"+("\nVertical display stretched" if self.aspect_mode.currentData()=="auto" else ""))
        self.time_line.set_ydata([f.times[i]]*2)
        self.position.setText(f"{i+1} / {len(f.times)} 帧   ·   步 {f.steps[i]}   ·   {f.times[i]:.4f} s")
        self.canvas.draw_idle()

    def pick_time(self,event):
        if self.toolbar.mode:
            return
        if self.ax_gather is not None and event.inaxes is self.ax_gather and event.ydata is not None:
            self.stop()
            self.slider.setValue(int(np.argmin(abs(self.gather.field.times-event.ydata))))

    def next_frame(self):
        if self.slider.value()>=self.slider.maximum():
            self.stop()
        else:
            self.slider.setValue(self.slider.value()+1)

    def toggle(self):
        if self.timer.isActive():
            self.stop()
        else:
            if self.slider.value()==self.slider.maximum():
                self.slider.setValue(0)
            self.timer.start()
            self.play.setText("暂停")

    def stop(self):
        self.timer.stop()
        self.play.setText("播放")
        if getattr(self,'region_view',None) is not None:
            self.region_view.timer.stop();self.region_view.play.setText('播放')

    def hideEvent(self,event):
        self.stop()
        super().hideEvent(event)

    def export(self):
        self.stop()
        step = self.gather.field.steps[self.slider.value()]
        run = Path(self.gather.meta["run"]).name
        path,_ = W.QFileDialog.getSaveFileName(self,"导出波场与同炮道集",f"{run}-step{step}.png","PNG (*.png)")
        if path:
            self.figure.savefig(path,dpi=160)
