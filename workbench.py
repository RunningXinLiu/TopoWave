"""Native PySide6 controls for single-shot SPECFEM2D modelling."""
import json
import hashlib
import os
import shutil
import sys
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import PySide6
from PySide6 import QtCore as C, QtGui as G, QtWidgets as W
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from plot_toolbar import PlotToolbar
from matplotlib.figure import Figure

from shot_templates import read_template, make_template
from analysis_presets import make_preset, read_preset, model_hash
from imaging import Gather, frequency_band_indices, array_center, fundamental, gather_window, reference_column, spectrum, theoretical_modes, windowed_traces
from geometry_editor import GeometryEditor
from wavefield_view import WavefieldView
from native_mesh import ElementMesh, draw_mesh
from mesh_quality import inspect_mesh, draw_quality
from material_editor import MaterialEditor
from scene_dialog import SceneDialog
from template_catalog import ACTIVE_TEMPLATES
from scene_workflow import SceneWorkflow
from custom_scene import blank_model, check_settings
from theory_controls import TheoryControls
from parameter_report import design_report
from materials import kind, attenuating
from parameter_setup import mesh_estimates, steps_for_duration, suggest_forward, suggest_gate, assess_acquisition
from run_forward import check_desktop_size
from boundary_domain import extend_stacey, current_domain
from model import COLUMNS, ROOT, SOLVER_DEFAULTS, elevation, read_model, snapshot_interval, stations, validate, write_case

GREEN = "#146653"
ORANGE = "#b45621"

TIPS = {
    "cpml_alpha_factor":"CPML频移 αmax=aπf0。1沿用官方值，2仅在三层平地4.5秒案例中抑制增长，完整Vz精度仍未通过。不是材料Q；增大不保证吸收更好。非1需支持该参数的候选后端。",
    "source_t0_s":"源主峰前的起始预移秒数，0表示求解器自动。修改会改变记录起点；传播总时长仍从首个计算步计时。",
    "pml_elements":"侧面和底面C-PML的谱元层数。实际厚度取决于网格；炮检点必须在侧面PML内边缘以内。网格加密时须复核物理厚度。",
    "width_m":"x 方向模型总宽。炮点和全部检波点须位于两侧边界以内，地形控制点应覆盖全宽。",
    "shot_x_m":"主动源水平位置（m），坐标向右增加。",
    "shot_depth_m":"从炮点当地地表向下的垂直埋深（m）。",
    "f0_hz":"震源主频（Hz）。弹性炮使用 Ricker 竖向力，声学炮使用高斯势源、接收压力。",
    "first_x_m":"首道的绝对水平坐标（m）。",
    "spacing_m":"相邻接收点水平间距（m）。会影响可分辨的最短水平波长。",
    "receivers":"道数。末道 x = 首道 x + (道数−1) × 道距。",
    "receiver_depth_m":"各道相对当地地表的垂直埋深（m）。声学自由表面压力趋零，压力检波器宜埋入介质。",
    "element_m":"水平方向目标谱元尺寸（m）。垂向留空时用同一尺寸自动分层。",
    "dt_s":"每个求解步的秒数。减小可改善稳定性；手动修改 dt 时优先保持填写的传播时长。",
    "nstep":"输入求解步数，可直接编辑。首末步跨度 T=(NSTEP−1)×dt；求解器可能为输出采样补齐末步。",
    "sample_stride":"每隔多少个计算步保存一次接收样点。输出间隔 = dt × 此数。",
    "damping_change_factor_elastic":"原生弹性C-PML参数：1为历史设置，0.5对应本轮2倍阻尼。仅影响弹性C-PML；不是材料衰减Q，也不保证弱尾波准确。",
    "snapshot_stride":"每隔多少步保存波场；0 表示自动约 60 帧。减小会增加本机磁盘占用。",
    "min_offset":"参与面波成像的最近水平偏移距（m）。整张单炮图始终显示所有接收道。",
    "max_offset":"最远水平偏移距（m）。0 使用该侧全部可用道，须至少三道。",
    "vslow":"慢端视群速度决定每道窗尾，t 尾 = 偏移距 / 慢端速度 + 边缘余量。",
    "vfast":"快端视群速度决定每道窗首，t 首 = 偏移距 / 快端速度 − 边缘余量。快端必须大于慢端。",
    "pad":"窗口两侧余量（s）。橙色边缘采用余弦渐变，窗外权重为零。",
    "fmin":"频散成像频带下限（Hz）。记录长度决定实际 FFT 频点。",
    "fmax":"频带上限（Hz），不得超过本炮采样 Nyquist 频率。",
    "cmin":"扫描的最低相速度（m/s），与用于取窗的群速度分开设置。",
    "cmax":"扫描的最高相速度（m/s）。应高于下限，包含待比较的理论曲线。",
    "dc":"相速度网格间隔（m/s），界面最多支持 2501 个速度采样点。",
}


def field_tip(widget,base,problem=""):
    widget.setToolTip(base+("\n当前提示："+problem if problem else ""))
    widget.setStyleSheet("border:1px solid #ba671b;" if problem else "")





def same_design(a,b):
    if a is None or b is None:
        return False
    defaults = dict(physics="elastic",snapshot_stride=0,interface_relief=[],elements_per_layer=None,**SOLVER_DEFAULTS)
    return {**defaults,**a} == {**defaults,**b}


def matched_material_evidence(c):
    """Return evidence only for a hash-verified, unchanged material template."""
    from template_catalog import evidence_entries,checked_evidence
    for path,entry in evidence_entries():
        if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()==entry['template_sha256'] and same_design(c,read_model(path)):
            return checked_evidence(entry)
    return None


def validated_label(c):
    # This is a scoped evidence label, never a universal stability certificate.
    entry=matched_material_evidence(c)
    if entry:return entry['label']
    candidate=ROOT/'examples/cpml_alpha/flat.json'
    ev=json.loads((ROOT/'examples/cpml_alpha/evidence.json').read_text())
    if hashlib.sha256(candidate.read_bytes()).hexdigest()==ev['template_sha256'] and same_design(c,read_model(candidate)):
        return ev['label']
    stacey_path=ROOT/'examples/stacey/flat780.json'
    stacey_evidence=json.loads((ROOT/'examples/stacey/evidence.json').read_text())
    if hashlib.sha256(stacey_path.read_bytes()).hexdigest()==stacey_evidence['template_sha256'] and same_design(c,read_model(stacey_path)):
        return 'Stacey平地基线：完整Vz误差0.83%、窗内0.51%；晚时弱段5.20%，其他模型/边界距离待验。'
    domain_evidence=json.loads((ROOT/'examples/stacey/domain_evidence.json').read_text())
    for filename,entry in domain_evidence.items():
        p=ROOT/'examples/stacey'/filename
        if hashlib.sha256(p.read_bytes()).hexdigest()==entry['sha256'] and same_design(c,read_model(p)):
            return entry['label']
    evidence=json.loads((ROOT/'examples/validated/evidence.json').read_text())
    for label,file in ((label,file) for label,file in ACTIVE_TEMPLATES if file.startswith('validated/')):
        p=ROOT/'examples'/file
        if (hashlib.sha256(p.read_bytes()).hexdigest()==evidence[p.name]['sha256']
                and same_design(c,read_model(p))):
            return label+'：仅该平地配置有波形核验证据。'+('最弱尾段约1.3%误差。' if '80 m' in label else '')
    for label,file in ((label,file) for label,file in ACTIVE_TEMPLATES if file in ('matched/flat18.json','matched/hill18.json')):
        p=ROOT/'examples'/file
        entry=evidence.get(file)
        if entry and hashlib.sha256(p.read_bytes()).hexdigest()==entry['sha256'] and same_design(c,read_template(p)[0]):
            return label+'：仅12–30 Hz固定面波窗的网格/地形增量筛查通过；晚段未全面通过。'
    return None


def button(label, callback, primary=False):
    item = W.QPushButton(label)
    item.setProperty("primary", primary)
    item.clicked.connect(callback)
    return item


def heading(label):
    w = W.QLabel(label)
    w.setProperty("heading",True)
    return w


def note(text=""):
    w = W.QLabel(text)
    w.setWordWrap(True)
    w.setProperty("note",True)
    return w


def row(*widgets):
    layout = W.QHBoxLayout()
    for w in widgets:
        layout.addWidget(w)
    return layout


def spin(value, low, high, decimals=2, integer=False):
    if integer:
        w = W.QSpinBox()
    else:
        w = W.QDoubleSpinBox()
        w.setDecimals(decimals)
    w.setRange(low,high)
    w.setValue(value)
    w.setKeyboardTracking(False)
    w.setMinimumWidth(84)
    return w


def panel():
    w = W.QWidget()
    layout = W.QVBoxLayout(w)
    layout.setContentsMargins(14,12,14,10)
    layout.setSpacing(9)
    return w,layout


def canvas():
    fig = Figure(figsize=(7,4), dpi=100, layout="constrained",facecolor="#fbfcfa")
    cv = FigureCanvasQTAgg(fig)
    cv.setMinimumSize(230,180)
    cv.setSizePolicy(W.QSizePolicy.Policy.Expanding,W.QSizePolicy.Policy.Expanding)
    return cv


def style_axis(ax):
    ax.tick_params(labelsize=9,colors="#334840")
    for edge in ax.spines.values():
        edge.set_color("#a6b4ab")


def mesh_geometry(c):
    return (c["width_m"],c["element_m"],c.get("elements_per_layer"),[r[0] for r in c["layers"]],
            c["surface"],c.get("interface_relief") or [])


class Design(W.QWidget):
    load_requested = C.Signal(object)
    save_requested = C.Signal()
    custom_requested = C.Signal()
    changed = C.Signal(object)
    def __init__(self, start_run, start_mesh):
        super().__init__()
        self.loading = True
        self.fields = {}
        self.base = {}
        self.region_case = None
        self.mesh_region_key = None
        self.native_mesh = None
        self.mesh_key = None
        self.mesh_source = ""
        self.mesh_artist = None
        main = W.QVBoxLayout(self)
        strip = W.QHBoxLayout()
        strip.addWidget(heading("主动源单炮设计"))
        strip.addStretch()
        strip.addWidget(button("自定义新建…",lambda:self.custom_requested.emit()))
        strip.addWidget(button("打开场景…",self.open_config))
        main.addLayout(strip)
        splitter = W.QSplitter()
        main.addWidget(splitter,1)
        self.pages = W.QTabWidget()
        splitter.addWidget(self.pages)
        model, left = panel()
        self.pages.addTab(model,"速度模型")
        self.domain_kind=W.QComboBox()
        self.domain_kind.addItem('分层地基 · 底层半空间参考','halfspace')
        self.domain_kind.addItem('水平自由板 · 上下无牵引','free_plate')
        self.domain_kind.addItem('固—流体—固水平层 · 声弹耦合','solid_fluid_layers')
        self.domain_kind.setToolTip('分层地基、自由板与固—流体—固是不同物理模型。流体层必须保留正声速和密度、Vs=0，上下界面为声弹耦合；自由板没有空气和下伏固体。切换不会补造或删除层，不支持的组合会报错。')
        self.domain_kind.currentIndexChanged.connect(self.refresh)
        left.addWidget(self.domain_kind)
        self.medium = note()
        left.addWidget(self.medium)
        left.addWidget(button("编辑全部层参数：速度 / 衰减 / TI…",self.edit_material))
        self.table = W.QTableWidget(0,4)
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(W.QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.setMinimumHeight(100)
        self.table.setMaximumHeight(240)
        self.table.itemChanged.connect(self.refresh)
        self.table.setToolTip("从上至下逐层填写厚度、Vp、Vs、密度。弹性层 Vs > 0，声学层 Vs = 0。")
        left.addWidget(self.table)
        left.addLayout(row(button("添加一层",self.add_layer),button("删除选中层",self.remove_layer)))
        left.addWidget(heading("地表与地下界面"))
        self.shape = note()
        left.addWidget(self.shape)
        left.addLayout(row(button("自定义地表与层界…",self.edit_geometry,True),button("改为平层",self.flatten)))
        left.addStretch()
        survey, sl = panel()
        self.pages.addTab(survey,"观测系统")
        sl.addWidget(heading("震源"))
        self.source_fields = W.QFormLayout()
        sl.addLayout(self.source_fields)
        self.add_fields(self.source_fields,[
            ("shot_x_m","炮点 x (m)"),("shot_depth_m","地下埋深 (m)"),("f0_hz","主频 (Hz)")])
        self.source_mode=W.QComboBox()
        self.source_mode.addItem("地表竖向力 · 向下主瓣","surface_force")
        self.source_mode.addItem("历史源设置 · 保留埋深与符号","legacy")
        self.source_mode.setToolTip("地表力作用于实际地形，方向为全局竖直，并非坡面法向。二维线力、理想Ricker子波。历史项保留旧弹性力或声学压力设置。")
        self.source_fields.insertRow(0,"震源施加",self.source_mode)
        self.source_mode.currentIndexChanged.connect(self.change_source_mode)
        sl.addWidget(heading("直线接收排列"))
        form = W.QFormLayout()
        sl.addLayout(form)
        self.add_fields(form,[("first_x_m","首道 x (m)"),("spacing_m","道间距 (m)"),
                              ("receivers","道数"),("receiver_depth_m","地下埋深 (m)")])
        self.aperture = note()
        sl.addWidget(self.aperture)
        self.acq_low=spin(9,.1,1000,1)
        self.acq_high=spin(36,.1,1000,1)
        self.acq_low.setToolTip("观测设计目标频带下限；不会修改震源或频散分析参数。")
        self.acq_high.setToolTip("观测设计目标频带上限；须与实际源谱、目标模态共同检查。")
        form.addRow("设计频率下限 (Hz)",self.acq_low)
        form.addRow("设计频率上限 (Hz)",self.acq_high)
        assess=button("设计观测系统…",self.assess_survey)
        assess.setToolTip("打开设计窗口，默认只评估；点击“仅填入候选道距与道数”才会修改。支持ISO基阶理论及手动相/群速度范围，TI/Q不冒用ISO理论。")
        sl.addWidget(assess)
        sl.addStretch()
        solver, nl = panel()
        solver_scroll = W.QScrollArea()
        solver_scroll.setWidgetResizable(True)
        solver_scroll.setWidget(solver)
        self.pages.addTab(solver_scroll,"计算设置")
        self.auto_forward = button("一键估算并填入正演参数",self.fill_forward,True)
        self.auto_forward.setToolTip("按本模型重估网格、逐层单元数、dt、传播时长、接收采样与快照间隔。只在点击时写入。")
        nl.addWidget(button("检查当前参数与采样…",self.inspect_parameters))
        nl.addWidget(button("内部区域与空洞模板…",self.edit_internal_region))
        nl.addWidget(self.auto_forward)
        self.quality=W.QComboBox()
        self.quality.addItem("快速试算 · 先看传播与记录覆盖","quick")
        self.quality.addItem("弱信号检查 · 更细网格与更小dt","weak")
        self.quality.setToolTip("仅选择档位不会修改参数，需点击上方按钮。两档都是候选值；弱信号仍需网格、dt和边界控制。")
        nl.addWidget(self.quality)
        nf = W.QFormLayout()
        nl.addLayout(nf)
        self.add_fields(nf,[("width_m","模型宽度 (m)"),("element_m","目标单元边长 (m)"),
                            ("dt_s","计算步长 (s)"),("nstep","计算步数"),
                            ("sample_stride","单炮采样间隔 (步)"),("snapshot_stride","波场快照间隔 (步)")])
        self.duration=spin(1,0,1e12,7)
        self.duration.setToolTip("从第一个计算步开始计时（秒）。按 dt 向上取整为整步，记录通常从负时间开始。")
        nf.insertRow(3,"传播总时长 (s)",self.duration)
        self.duration.valueChanged.connect(self.update_time_steps)
        self.fields["snapshot_stride"].setSpecialValueText("自动（约 60 帧）")
        self.nz_edit = W.QLineEdit()
        self.nz_edit.setToolTip("从上到下每层一个正整数，以逗号分隔。留空按单元尺寸自动划分。")
        self.nz_edit.setPlaceholderText("自动；也可逐层输入，如 8,42")
        self.nz_edit.editingFinished.connect(self.refresh)
        nf.addRow("各层垂向单元数",self.nz_edit)
        self.boundary=W.QComboBox()
        self.boundary.addItem("Stacey＋外围距离检查","stacey");self.boundary.addItem("C-PML（可设置频移）","cpml")
        self.boundary.setToolTip("顶面保持自由。Stacey需足够外围距离并检查返回波；C-PML原频移曾失稳；增强频移候选抑制了该例增长，但完整波形精度尚未通过。历史短时平层证据不能覆盖新模型。")
        self.boundary.currentIndexChanged.connect(self.refresh)
        nf.addRow("侧面与底部边界",self.boundary)
        nl.addWidget(button("设计外围扩域…",self.extend_boundary))
        self.add_fields(nf,[("cpml_alpha_factor","CPML频移系数 a"),("pml_elements","PML厚度 (谱元层数)"),("damping_change_factor_elastic","弹性PML阻尼因子"),("source_t0_s","源起始预移 (s)")])
        self.alpha_fill=button("填入频移候选 a=2（仅改此项）",self.fill_alpha)
        nl.addWidget(self.alpha_fill)
        self.cpml_info=note()
        nl.addWidget(self.cpml_info)
        self.material_info=note()
        nl.addWidget(self.material_info)
        self.fields['source_t0_s'].setSpecialValueText("自动")
        self.time_info = note()
        nl.addWidget(self.time_info)
        self.evidence_note=note()
        nl.addWidget(self.evidence_note)
        nl.addWidget(note("单炮单进程计算，原始结果保存在本机独立目录。参数模板仅载入输入，不代表已载入同炮波场与记录。"))
        nl.addStretch()
        right, rl = panel()
        plot_header = W.QHBoxLayout()
        plot_header.addWidget(heading("模型与排列"),1)
        self.mesh_toggle = W.QCheckBox("实际谱元网格")
        self.mesh_toggle.setChecked(True)
        self.mesh_toggle.toggled.connect(self.toggle_mesh)
        plot_header.addWidget(self.mesh_toggle)
        rl.addLayout(plot_header)
        self.cv = canvas()
        rl.addWidget(self.cv,1)
        self.summary = note()
        rl.addWidget(self.summary)
        splitter.addWidget(right)
        splitter.setSizes([440,670])
        bottom = W.QHBoxLayout()
        self.status = note()
        bottom.addWidget(self.status,1)
        self.export = button("导出 DATA.zip",self.export_data)
        self.run = button("按当前参数在本机新算一炮",start_run,True)
        self.mesh_run = button("生成 / 重新生成网格",start_mesh)
        self.mesh_run.setToolTip("使用当前设计创建独立网格，保留旧结果；不自动改变地形、材料或网格参数。")
        bottom.addWidget(button("实际网格 QC…",self.show_mesh_qc))
        bottom.addWidget(self.export)
        bottom.addWidget(self.mesh_run)
        bottom.addWidget(self.run)
        main.addLayout(bottom)
        self.apply(blank_model())

    def add_fields(self, form, entries):
        for key,label in entries:
            integer = key in ("nstep","receivers","sample_stride","snapshot_stride","pml_elements")
            w = spin(0,0,1000000,7 if key=="dt_s" else 4,integer)
            self.fields[key] = w
            w.setToolTip(TIPS[key])
            if key=="dt_s":
                w.valueChanged.connect(self.update_time_steps)
            elif key=="nstep":
                w.valueChanged.connect(self.update_duration)
            else:
                w.valueChanged.connect(self.refresh)
            form.addRow(label,w)
            form.labelForField(w).setToolTip(TIPS[key])

    def apply(self,c):
        self.loading = True
        self.base = dict(c)
        self.domain_kind.setCurrentIndex(self.domain_kind.findData(c.get("domain_kind","halfspace")))
        self.source_mode.setCurrentIndex(self.source_mode.findData(c.get('source_mode','legacy')))
        self.boundary.setCurrentIndex(self.boundary.findData(c.get('boundary','stacey')))
        self.nz_edit.setText(",".join(str(n) for n in (c.get("elements_per_layer") or [])))
        for key,w in self.fields.items():
            w.setValue(c.get(key,0) if key=="snapshot_stride" else c.get(key,SOLVER_DEFAULTS.get(key,0)))
        self.duration.setValue((c["nstep"]-1)*c["dt_s"])
        self.table.setRowCount(0)
        for layer in c["layers"]:
            self.append_row(layer)
        self.loading = False
        self.refresh()

    def extend_boundary(self):
        dialog=W.QDialog(self);dialog.setWindowTitle("外围扩域 · Stacey")
        layout=W.QVBoxLayout(dialog)
        layout.addWidget(note("明确追加计算区域，距离按现有谱元大小向上取整。原区域水平坐标整体平移左侧追加距离；源台相对位置不变。仅支持各界面端部水平的常值延拓，底部延伸末层材料。"))
        form=W.QFormLayout();layout.addLayout(form);widgets=[]
        for label in ["左侧追加 (m)","右侧追加 (m)","底部追加 (m)"]:
            w=spin(0,0,100000,3);form.addRow(label,w);widgets.append(w)
        layout.addWidget(note("距离是待验证输入，不自动保证无反射。应结合完整记录时长、P/S波及面波路径设计，并与更远边界对照。dt、记录时长、源和采样不变。"))
        buttons=W.QDialogButtonBox(W.QDialogButtonBox.StandardButton.Ok|W.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons)
        if dialog.exec()==W.QDialog.DialogCode.Accepted:
            try:self.apply(extend_stacey(self.collect(),*[w.value() for w in widgets]))
            except (ValueError,TypeError,KeyError) as exc:W.QMessageBox.warning(self,"扩域未应用",str(exc))

    def change_source_mode(self,*_):
        if self.loading:return
        if self.source_mode.currentData()=="surface_force":
            self.fields['shot_depth_m'].setValue(0)
        self.refresh()

    def update_time_steps(self,*_):
        if self.loading:
            return
        dt=self.fields["dt_s"].value()
        n=steps_for_duration(self.duration.value(),dt) if dt>0 and self.duration.value()>0 else 0
        with C.QSignalBlocker(self.fields["nstep"]):
            self.fields["nstep"].setValue(min(n,200001))
        self.refresh()

    def update_duration(self,*_):
        if self.loading:
            return
        with C.QSignalBlocker(self.duration):
            self.duration.setValue(max(0,self.fields["nstep"].value()-1)*self.fields["dt_s"].value())
        self.refresh()

    def assess_survey(self):
        from acquisition_dialog import AcquisitionDialog
        try:
            dialog=AcquisitionDialog(self.collect(numerical=False),self.acq_low.value(),self.acq_high.value(),self)
            if dialog.exec()==W.QDialog.DialogCode.Accepted and dialog.values:
                self.loading=True
                for key,value in dialog.values.items():self.fields[key].setValue(value)
                self.loading=False;self.refresh()
                self.status.setText('已显式填入候选道距和道数；请重新检查传播时长、计算域与边界。尚未启动正演。')
        except (ValueError,RuntimeError,ImportError,ArithmeticError) as exc:
            self.status.setText('观测系统评估失败：'+str(exc))

    def fill_alpha(self):
        self.fields['cpml_alpha_factor'].setValue(2.)
        self.alpha_fill.setToolTip("仅填入频移系数；来源为三层平地4.5秒对照。改变材料、源、网格或时长后，不继承该证据。")

    def fill_forward(self):
        try:
            c=self.collect(numerical=False)
            values,detail=suggest_forward(c,self.quality.currentData())
            config=dict(c,**values)
            check_desktop_size(config)
            for key,value in values.items():
                if key in self.fields and not self.fields[key].minimum()<=value<=self.fields[key].maximum():
                    raise ValueError(f"{key} 的候选值超出当前桌面范围，请手动指定。")
            self.apply(config)
            self.time_info.setToolTip(detail)
            self.auto_forward.setToolTip(detail)
            self.time_info.setText(self.time_info.text()+"  ·  已填入试算值，悬停查看依据。")
        except (ValueError,TypeError,AttributeError,OverflowError,ImportError) as exc:
            self.status.setText("未能估算："+str(exc))
            self.auto_forward.setToolTip(str(exc))

    def append_row(self, values):
        i = self.table.rowCount()
        self.table.insertRow(i)
        for j,v in enumerate(values):
            self.table.setItem(i,j,W.QTableWidgetItem(str(v)))

    def add_layer(self):
        if self.base.get("material_properties"):
            self.edit_material("add")
            return
        if self.base.get("interface_relief"):
            W.QMessageBox.information(self,"界面","此模型含独立界面。请先选择“改为平层”，或导入地形后按等厚层编辑。")
            return
        self.loading = True
        r = self.table.rowCount()-1
        self.append_row([float(self.table.item(r,i).text()) for i in range(4)] if self.config else [20,900,300,1800])
        self.loading = False
        self.refresh()

    def remove_layer(self):
        if self.base.get("material_properties"):
            self.edit_material("remove")
            return
        if self.base.get("interface_relief"):
            W.QMessageBox.information(self,"界面","保留独立界面时层数固定。可先改为平层。")
            return
        if self.table.rowCount()>1 and self.table.currentRow()>=0:
            self.table.removeRow(self.table.currentRow())
            self.refresh()

    def flatten(self):
        self.base.update(surface=[],interface_relief=[])
        self.refresh()

    def edit_material(self,action=None):
        try:
            editor=MaterialEditor(self.collect(),self,allow_draft=self.config is None)
            if action=="add":editor.add_layer()
            elif action=="remove":
                editor.table.setCurrentCell(max(0,self.table.currentRow()),0);editor.remove_layer()
            if editor.exec()==W.QDialog.DialogCode.Accepted:self.apply(editor.result_model)
        except (ValueError,KeyError,TypeError,AttributeError) as exc:
            W.QMessageBox.warning(self,'材料参数',str(exc))

    def inspect_parameters(self):
        try:
            c=self.collect();r=design_report(c)
            popup=W.QDialog(self);popup.setWindowTitle('当前参数检查 · 不改输入');popup.resize(820,650)
            layout=W.QVBoxLayout(popup);tree=W.QTreeWidget();tree.setHeaderLabels(['参数','当前值与解释']);layout.addWidget(tree)
            groups={
                '模型与材料':[('介质',kind(c)+('，材料衰减开启' if attenuating(c) else '，无材料衰减')),('模型宽度',f"{c['width_m']:g} m"),('层厚/Vp/Vs/密度（SI）',str(c['layers'])),('材料附加参数',str(c.get('material_properties',[])))],
                '源与观测':[('源类型',c.get('source_mode','legacy')),('主频',f"{c['f0_hz']:g} Hz"),('炮点/埋深',f"{c['shot_x_m']:g} / {c['shot_depth_m']:g} m"),('首末道 / 道距',f"{r['receiver_first_x_m']:g}–{r['receiver_last_x_m']:g} / {c['spacing_m']:g} m"),('道数 / 孔径',f"{c['receivers']} / {r['aperture_m']:g} m")],
                '网格候选':[('水平/逐层垂向单元数',f"{r['nx']} / {r['nz_per_layer']}"),('总谱元数',str(r['element_count'])),('实际水平边长',f"{r['horizontal_element_m']:.6g} m"),('估计CFL',f"{r['estimated_cfl']:.4g}（不是稳定性证书）"),('最短波长平均GLL间距数',f"{r['minimum_average_gll_intervals_per_wavelength']:.4g}，按{r['design_fmax_hz']:g} Hz设计")],
                '时间与输出':[('dt',f"{c['dt_s']:.9g} s"),('请求/求解器补齐步数',f"{r['requested_steps']} / {r['solver_rounded_steps']}"),('请求/补齐时长',f"{r['requested_span_s']:.9g} / {r['solver_span_s']:.9g} s"),('接收样点数/间隔',f"{r['receiver_samples']} / {r['receiver_dt_s']:.9g} s"),('预计接收首末时刻','源预移自动，待求解器确定' if r['receiver_first_s'] is None else f"{r['receiver_first_s']:.9g}–{r['receiver_last_s']:.9g} s"),('快照间隔',f"{r['snapshot_stride']}步")],
                '边界与证据':[('侧/底边界',c.get('boundary','stacey')),('源预移','自动' if not c.get('source_t0_s') else str(c['source_t0_s'])+' s'),('匹配证据',validated_label(c) or '无完全匹配的验证记录')],
            }
            evidence=matched_material_evidence(c)
            checks=evidence.get('checks',[]) if evidence else []
            if checks:
                groups={'逐项验证（仅匹配当前模板）':[(x['name'],x['summary']) for x in checks],**groups}
            if c.get('boundary')=='cpml':groups['边界与证据'] += [('CPML层数/侧厚',f"{c['pml_elements']} / {r['pml_side_m']:g} m"),('底部厚度范围',str(r['pml_bottom_range_m'])+' m'),('alpha_max',f"{r['alpha_max_s_inv']:.7g} s⁻¹"),('弹性阻尼因子',str(c.get('damping_change_factor_elastic',1.)))]
            for group,rows in groups.items():
                parent=W.QTreeWidgetItem([group,'']);tree.addTopLevelItem(parent)
                for name,value in rows:
                    item=W.QTreeWidgetItem([name,value]);item.setToolTip(1,value);parent.addChild(item)
                    if group=='逐项验证（仅匹配当前模板）':
                        entry=next(x for x in checks if x['name']==name)
                        item.setToolTip(1,value+'\n证据：'+str(ROOT/entry['report']))
                        if entry['state']=='failed':item.setForeground(1,G.QColor('#a52d36'))
            tree.expandAll();tree.resizeColumnToContents(0)
            layout.addWidget(note('；'.join(r['warnings']) or '输入检查未发现上述采样警告；仍需实际网格QC及波形验证。'))
            def save():
                target,_=W.QFileDialog.getSaveFileName(popup,'保存完整参数与检查','parameter_check.json','JSON (*.json)')
                if target:Path(target).write_text(json.dumps(dict(model=c,check=r,matched_evidence=evidence),ensure_ascii=False,indent=2))
            layout.addLayout(row(button('保存完整参数与检查',save),button('关闭',popup.accept)));popup.exec()
        except (ValueError,TypeError,KeyError,AttributeError) as exc:W.QMessageBox.information(self,'参数检查',str(exc))

    def show_mesh_qc(self):
        try:
            c=self.collect();validate(c)
            if self.native_mesh is None or self.mesh_key!=mesh_geometry(c):
                raise ValueError('当前设计尚无匹配实际网格，请先生成/重新生成网格。')
            report=inspect_mesh(self.native_mesh,c)
            popup=W.QDialog(self);popup.setWindowTitle('实际谱元网格 QC');popup.resize(1000,750)
            layout=W.QVBoxLayout(popup);cv=canvas();layout.addWidget(PlotToolbar(cv,popup));layout.addWidget(cv,2)
            draw_quality(cv.figure.subplots(),self.native_mesh,report);cv.draw()
            details=W.QPlainTextEdit();details.setReadOnly(True)
            details.setPlainText(f"单元 {report['elements']}；无效 {report['invalid_count']}；畸变提示 {report['warning_count']}；最小scaled Jacobian {report['min_scaled_jacobian']:.4g}。\n"+
                '红：翻转/退化，禁止正演；橙：畸变提示；绿：所采样位置未发现几何问题。\n'
                '不是波形精度或PML稳定性认证。调整地形/层界、水平边长或各层单元数后重新生成。仅重复生成不会改善同一组输入。\n'+json.dumps(report,ensure_ascii=False,indent=2))
            layout.addWidget(details,1)
            def save():
                target,_=W.QFileDialog.getSaveFileName(popup,'保存网格QC','mesh_qc.json','JSON (*.json)')
                if target:
                    Path(target).write_text(json.dumps(report,ensure_ascii=False,indent=2));cv.figure.savefig(str(Path(target).with_suffix('.png')),dpi=150)
            layout.addLayout(row(button('保存指标和图',save),button('关闭',popup.accept)));popup.exec()
        except (ValueError,KeyError,TypeError) as exc:W.QMessageBox.information(self,'网格QC',str(exc))

    def edit_geometry(self):
        try:
            c = self.collect()
            editor = GeometryEditor(c,self)
            if editor.exec()==W.QDialog.DialogCode.Accepted:
                self.apply(editor.result_model)
        except (ValueError,KeyError,AttributeError,TypeError) as exc:
            W.QMessageBox.warning(self,"检查层参数",str(exc))

    def collect(self,numerical=True):
        layers = [[float(self.table.item(i,j).text()) for j in range(4)] for i in range(self.table.rowCount())]
        # Preserve unchanged numeric representations, as for the scalar fields below.
        # Merely opening an integer-valued model must not invalidate its result hash.
        for i,row in enumerate(layers):
            if i<len(self.base.get('layers',[])):
                for j,value in enumerate(row):
                    if value==self.base['layers'][i][j]:row[j]=self.base['layers'][i][j]
        values={}
        for k,w in self.fields.items():
            value=w.value()
            original=self.base.get(k,value)
            if isinstance(w,W.QDoubleSpinBox) and value==round(original,w.decimals()):
                value=original
            values[k]=value
        config=dict(self.base,layers=layers,**values)
        config.update(source_mode=self.source_mode.currentData(),boundary=self.boundary.currentData())
        if self.domain_kind.currentData() in ('free_plate','solid_fluid_layers'):config['domain_kind']=self.domain_kind.currentData()
        else:config.pop('domain_kind',None)
        if self.domain_kind.currentData()=='solid_fluid_layers':config['physics']='coupled'
        elif config.get('physics')=='coupled':config['physics']='elastic'
        spec=self.nz_edit.text().strip().replace("，",",")
        if numerical and spec:
            config["elements_per_layer"]=[int(item.strip()) for item in spec.split(",")]
        else:
            config.pop("elements_per_layer",None)
        return config

    def region_key(self,c):
        if self.region_case is None:return None
        from region_workflow import case_with_model
        from region_result import case_hash
        return case_hash(case_with_model(self.region_case,c))

    def set_actual_mesh(self,mesh,config,source,region_case=None):
        self.native_mesh = mesh
        self.mesh_key = mesh_geometry(config) if mesh is not None and config is not None else None
        self.mesh_source = Path(source).name
        from region_result import case_hash
        self.mesh_region_key=case_hash(region_case) if region_case is not None else None
        self.mesh_report = inspect_mesh(mesh) if mesh is not None else None
        self.refresh()

    def mesh_from_gather(self,g):
        if g is None:
            self.set_actual_mesh(None,None,"")
        else:
            self.set_actual_mesh(g.mesh,g.meta.get("model"),g.meta["run"],g.meta.get('region_case'))

    def toggle_mesh(self,visible):
        if self.mesh_artist is not None:
            self.mesh_artist.set_visible(visible)
            self.cv.draw_idle()

    def refresh(self,*_):
        if self.loading:
            return
        self.config = None
        self.cpml_info.setText("")
        self.material_info.setText("")
        free_plate=self.domain_kind.currentData()=='free_plate'
        coupled=self.domain_kind.currentData()=='solid_fluid_layers'
        self.auto_forward.setEnabled(not (free_plate or coupled))
        self.auto_forward.setToolTip('自由板使用A/S群速度；当前R0一键估算不适用，请载入自由板候选模板并手动调整。' if free_plate else '按本模型估算正演候选；仅点击时填入，需核验实际波形。')
        if coupled:self.auto_forward.setToolTip('固—流体—固不能使用纯固体R0估算。可读入候选模板，再手动调整dt、网格和时长；仍需数值验收。')
        cpml=self.boundary.currentData()=='cpml'
        self.fields['cpml_alpha_factor'].setEnabled(cpml)
        self.alpha_fill.setEnabled(cpml and self.base.get('physics','elastic')=='elastic')
        self.evidence_note.setText("输入未通过检查，当前配置没有可用的核验结论。")
        self.fields['shot_depth_m'].setEnabled(self.source_mode.currentData()!='surface_force')
        self.fields['pml_elements'].setEnabled(self.boundary.currentData()=='cpml')
        acoustic = self.base.get("physics")=="acoustic"
        self.medium.setText(("声学介质 · 压力源与压力接收，Vs 填 0。" if acoustic else "弹性 P–SV · 竖向力源与竖向速度接收。")+" 双击表格，从地表向下逐层填入厚度、纵波速度、横波速度和密度。每层内的材料参数均一。")
        ti=kind(self.base)!='isotropic'
        self.table.setHorizontalHeaderLabels(("厚度 (m)","Vp0 (m/s)","Vs0 (m/s)","密度 (kg/m³)") if ti else COLUMNS)
        if ti:self.medium.setText(kind(self.base).upper()+" · 对称轴为全局"+("竖直 z" if kind(self.base)=='vti' else "水平 x（剖面内）")+"。Vp0/Vs0沿对称轴定义，材料轴不随地形旋转。")
        elif attenuating(self.base):self.medium.setText("黏弹性各向同性 · Qκ/Qμ 与速度参考在‘材料属性’内设置。")
        self.shape.setText("几何：独立起伏界面；厚度取 x = 0 处。" if self.base.get("interface_relief")
                           else "几何：各界面随导入地形等厚起伏。" if self.base.get("surface") else "几何：水平分层；最后一层延伸到模型底界。")
        if free_plate:self.shape.setText('几何：单层水平自由板；厚度为实际板厚，底面无牵引，不延伸成半空间。左右边界仍需数值核验。')
        if coupled:
            self.medium.setText('实际三个材料层：上覆固体／流体／下伏固体。流体层Vp为声速、Vs=0、密度为正；不是删除单元。地表接收仍为固体Vx/Vz。')
            self.shape.setText('几何：流体层横贯整个计算域；上下为声弹耦合，地表自由，外侧与底部Stacey。下伏固体保留；有限数值域不自动等于无限域。')
        for key,w in self.fields.items():
            field_tip(w,TIPS[key])
        table_help="各行自浅到深。厚度、密度和 Vp 须为正值，弹性层要求 Vs > 0 且 Vp² > 4 Vs²/3。"
        if coupled:table_help='必须是固体/流体/固体三行。中间层Vs=0，但声速、密度和厚度为正；上下固体Vs>0。每层实际存在，不是自由板。底层厚度是数值截断深度。'
        nz_help="每层一个正整数，以逗号分隔。留空表示按目标单元边长自动划分。"
        if ti:table_help="Vp0/Vs0 为对称轴速度；需要完整 TI 刚度正定。ε/δ/γ 在材料属性中逐层设置。"
        field_tip(self.table,table_help)
        field_tip(self.nz_edit,nz_help)
        self.cv.figure.clear()
        self.mesh_artist = None
        self.mesh_toggle.setEnabled(False)
        try:
            c = self.collect()
            validate(c)
            if self.region_case is not None:
                from region_case import validate as validate_region
                from region_workflow import case_with_model
                validate_region(case_with_model(self.region_case,c))
            self.config = c
            if not acoustic and kind(c)=="isotropic":
                material=[]
                for i,(_,vp,vs,_) in enumerate(c['layers'],1):
                    if vs==0:
                        material.append(f'L{i}: 流体声速={vp:g} m/s；不使用固体Vp/Vs与泊松比')
                        continue
                    ratio=vp/vs;nu=(ratio*ratio-2)/(2*(ratio*ratio-1))
                    material.append(f"L{i}: Vp/Vs={ratio:.3f}，ν={nu:.4f}")
                self.material_info.setText("材料检查："+"；".join(material)+"。高泊松比不等于材料非法，也没有通用稳定阈值。")
            if kind(c)!='isotropic':
                self.material_info.setText(kind(c).upper()+'：Vp0/Vs0沿对称轴定义；三维刚度正定检查通过。无统一泊松比。')
            if attenuating(c):
                self.material_info.setText(self.material_info.text()+f" 材料衰减已启用：Qκ/Qμ，N_SLS={c['attenuation']['n_sls']}，参考频率f0={c['f0_hz']:g} Hz。")
            self.fields['shot_depth_m'].setEnabled(c.get('source_mode')!='surface_force')
            self.fields['pml_elements'].setEnabled(c.get('boundary')=='cpml')
            self.fields["damping_change_factor_elastic"].setEnabled(c.get("boundary")=="cpml" and c.get("physics","elastic")=="elastic")
            matched=validated_label(c) if self.region_case is None else '当前含洞体/内部区域；背景模型的验证结论不自动覆盖洞体散射。'
            self.evidence_note.setText(matched or "当前自定义配置：尚无完全匹配的波形核验记录，需检查网格、dt、边界和完整波形。")
            xs = stations(c)
            self.aperture.setText(f"末道 x = {xs[-1]:g} m  ·  孔径 {np.ptp(xs):g} m")
            self.time_info.setText(f"单炮输出间隔 {c['sample_stride']*c['dt_s']*1000:.4g} ms  ·  波场每 {snapshot_interval(c)} 步保存（{snapshot_interval(c)*c['dt_s']*1000:.4g} ms）。")
            span=(c["nstep"]-1)*c["dt_s"]
            self.time_info.setText(f"请求跨度 {span:.6g} s · "+self.time_info.text())
            cfl,points=mesh_estimates(c)
            warnings=[]
            if cfl>=.5:
                warnings.append("预计 CFL ≥ 0.5，建议减小 dt。")
                field_tip(self.fields["dt_s"],TIPS["dt_s"],warnings[-1])
            if points<5:
                warnings.append("目标频带网格偏粗，建议细化谱元。")
                field_tip(self.fields["element_m"],TIPS["element_m"],warnings[-1])
            if c["sample_stride"]*c["dt_s"]*2*2.5*c["f0_hz"]>=1:
                warnings.append("输出采样不足，可能混叠。")
                field_tip(self.fields["sample_stride"],TIPS["sample_stride"],warnings[-1])
            self.time_info.setToolTip(f"设计预检：预计 CFL {cfl:.3g}，按 2.5 倍主频估计最少 {points:.2f} 个平均 GLL 间距/波长。以实际网格日志和波形复核。")
            if c.get('boundary')=='cpml':
                nx=int(np.ceil(c['width_m']/c['element_m']))
                nz=c.get('elements_per_layer') or [int(np.ceil(r[0]/c['element_m'])) for r in c['layers']]
                xx=np.linspace(0,c['width_m'],513)
                bottom=(elevation(c,xx,len(c['layers'])-1)-elevation(c,xx,len(c['layers'])))*c['pml_elements']/nz[-1]
                self.cpml_info.setText(f"实际侧厚约 {c['pml_elements']*c['width_m']/nx:.3g} m；底厚约 {bottom.min():.3g}–{bottom.max():.3g} m。\nαmax={c.get('cpml_alpha_factor',1)*np.pi*c['f0_hz']:.6g} s⁻¹；"+("官方频移公式。" if c.get('cpml_alpha_factor',1)==1 else "显式频移：使用候选后端，尚非通用验收配置。"))
                field_tip(self.fields['pml_elements'],TIPS['pml_elements']+f"\n当前侧厚约{c['pml_elements']*c['width_m']/nx:.3g} m，底厚约{bottom.min():.3g}–{bottom.max():.3g} m。")
            ax = self.cv.figure.subplots()
            x = np.linspace(0,c["width_m"],500)
            palette = ["#b6d9c9","#81b7a4","#4c8c80","#336960"]
            for i,layer in enumerate(c["layers"]):
                upper,lower = elevation(c,x,i),elevation(c,x,i+1)
                if self.region_case is not None and self.region_case.get('schema')=='region_geometry_v1' and i==0:
                    from region_geometry import surface_height
                    upper=surface_height(dict(self.region_case,model=c),x)
                ax.fill_between(x,lower,upper,color=palette[min(i,3)],ec="#498071",lw=.65)
                if len(c["layers"])<=6:
                    ax.text(x[-1]*.53,(upper[260]+lower[260])/2,f"L{i+1}    {'Vp0' if ti else 'Vp'} {layer[1]:g}    {'Vs0' if ti else 'Vs'} {layer[2]:g}",ha="center",va="center",color="#152c25" if i<2 else "white",fontsize=9)
            surface=lambda values:elevation(c,values)
            if self.region_case is not None and self.region_case.get('schema')=='region_geometry_v1':
                from region_geometry import surface_height
                surface=lambda values:surface_height(dict(self.region_case,model=c),values)
            ax.scatter(xs,surface(xs)-c["receiver_depth_m"],s=12,c=GREEN,marker="v",label=f"Receivers ({len(xs)})",zorder=4)
            if self.region_case is not None:
                from region_workflow import draw_regions
                draw_regions(ax,self.region_case)
            sx = c["shot_x_m"]
            ax.scatter([sx],[surface(sx)-c["shot_depth_m"]],s=130,color=ORANGE,marker="*",label="Shot",zorder=5)
            domain=current_domain(c)
            if domain:
                left=domain['left_m'];right=left+domain['original_width_m']
                ax.axvspan(0,left,color='#909090',alpha=.22,label='Added computational buffer')
                ax.axvspan(right,c['width_m'],color='#909090',alpha=.22)
                ax.axvline(left,color='#555555',ls='--',lw=.8);ax.axvline(right,color='#555555',ls='--',lw=.8)
                if domain['bottom_m']:
                    bottom=elevation(c,x,len(c['layers']))
                    ax.fill_between(x,bottom,bottom+domain['bottom_m'],color='#909090',alpha=.22)
            ax.margins(x=.02,y=.15)
            ax.set(xlabel="Horizontal position x (m)",ylabel="Elevation z (m)")
            ax.legend(loc="lower left",framealpha=.95,fontsize=8,ncol=2)
            style_axis(ax)
            self.summary.setText(f"炮点 {sx:g} m    ·    接收 {xs[0]:g}–{xs[-1]:g} m    ·    {len(xs)} 道 × 道距 {c['spacing_m']:g} m")
            if domain:
                self.summary.setText(self.summary.text()+f"\n外围追加：左{domain['left_m']:g} / 右{domain['right_m']:g} / 底{domain['bottom_m']:g} m；原坐标 +{domain['coordinate_shift_x_m']:g} m。灰色为追加计算区。")
            self.status.setText("参数已就绪。修改后需重新运行，单炮记录随算例保存。")
            if self.native_mesh is not None and self.mesh_key == mesh_geometry(c) and self.mesh_region_key==self.region_key(c):
                self.mesh_artist = draw_mesh(ax,self.native_mesh)
                self.mesh_artist.set_visible(self.mesh_toggle.isChecked())
                self.mesh_toggle.setEnabled(True)
                self.status.setText(f"实际网格 · {len(self.native_mesh.cells):,} 个谱元 · {self.mesh_source} · QC: { {'sampled_geometry_ok':'几何采样未见异常','geometry_warning':'有畸变提示','invalid_geometry':'存在无效单元'}[self.mesh_report['status']] }")
            else:
                self.status.setText("当前为设计剖面。点击「生成 / 重新生成网格」查看实际谱元。")
            if warnings:
                self.status.setText("参数提示 · "+" ".join(warnings))
            self.status.setToolTip(self.time_info.toolTip())
        except (KeyError,TypeError,ValueError,AttributeError) as exc:
            self.config=None
            problem=str(exc)
            if "单元数" in problem or "invalid literal for int" in problem:
                problem="垂向单元数请从上到下每层填一个正整数，以逗号分隔。"
                field_tip(self.nz_edit,nz_help,problem)
            if "层" in problem or "Vs" in problem or "float" in problem:
                field_tip(self.table,table_help,problem)
            self.status.setToolTip(problem)
            keys = ["nstep","sample_stride","receivers"] if "计算步数" in problem else []
            if "末道" in problem:
                keys=["shot_x_m","first_x_m","spacing_m","receivers","width_m"]
            if "埋深" in problem:
                keys=["shot_depth_m","receiver_depth_m"]
            if "步长和主频" in problem:
                keys=["element_m","dt_s","f0_hz","width_m","spacing_m"]
            for key in keys:
                field_tip(self.fields[key],TIPS[key],problem)
            self.status.setText("输入待修正  ·  "+problem)
            self.aperture.clear()
            self.summary.clear()
            self.time_info.clear()
        self.export.setEnabled(self.config is not None)
        runnable=self.config is not None
        self.run.setToolTip("使用当前源、模型、网格和采集提交 SPECFEM2D 单炮。")
        if runnable:
            try:
                check_desktop_size(self.config)
            except ValueError as exc:
                self.run.setToolTip(str(exc))
                self.status.setText(str(exc))
                self.status.setToolTip(str(exc))
                runnable=False
        if runnable and self.native_mesh is not None and self.mesh_key==mesh_geometry(self.config) and self.mesh_region_key==self.region_key(self.config) and self.mesh_report['invalid_count']:
            runnable=False
            self.status.setText('实际网格存在翻转/退化单元；请查看网格QC，修改设计后重新生成。')
        self.run.setEnabled(runnable)
        self.mesh_run.setEnabled(self.config is not None)
        self.cv.draw_idle()
        self.changed.emit(self.config)

    def edit_internal_region(self):
        owner=self.window()
        if hasattr(owner,'show_scene_regions'):
            owner.edit_active_region();return
        from region_editor import RegionEditor
        RegionEditor(self).exec()

    def new_scene(self):
        dialog = SceneDialog(self)
        if dialog.exec() == W.QDialog.DialogCode.Accepted:
            if dialog.selected_region_case is not None:
                owner = self.window()
                if hasattr(owner, 'open_region_template'):
                    owner.open_region_template(dialog.selected_region_case)
                else:
                    from region_templates import make_editor
                    make_editor(dialog.selected_region_case, self).exec()
            elif dialog.selected_path:
                self.load_requested.emit(dialog.selected_path)

    def open_config(self):
        path,_ = W.QFileDialog.getOpenFileName(self,"打开场景或模板","","场景 / 模板 / 结果 (*.json *.bgpscene *.npz)")
        if path:
            self.load_requested.emit(Path(path))

    def export_data(self):
        path,_ = W.QFileDialog.getSaveFileName(self,"导出求解器输入","DATA.zip","压缩包 (*.zip)")
        if path:
            with TemporaryDirectory() as temp, zipfile.ZipFile(path,"w",zipfile.ZIP_DEFLATED) as dest:
                if self.region_case is not None:
                    from region_case import write
                    from region_workflow import case_with_model
                    write(case_with_model(self.region_case,self.config),temp)
                else:write_case(self.config,temp)
                for p in Path(temp).rglob("*"):
                    if p.is_file():
                        dest.write(p,p.relative_to(temp))


class Results(TheoryControls,W.QWidget):
    shot_changed = C.Signal(object)
    comparison_requested = C.Signal()
    def __init__(self):
        super().__init__()
        self.gather = self.result = self.path = None
        self.curve = None
        outer = W.QVBoxLayout(self)
        top = W.QHBoxLayout()
        top.addWidget(heading("单炮记录"))
        top.addStretch()
        self.analysis_mode = W.QPushButton("面波窗与频散…")
        self.analysis_mode.setCheckable(True)
        top.addWidget(self.analysis_mode)
        self.qc = button("检查接收道",self.inspect_traces)
        top.addWidget(self.qc)
        top.addWidget(button("打开记录",self.choose))
        self.save_btn = button("另存记录",self.save_gather)
        top.addWidget(self.save_btn)
        outer.addLayout(top)
        self.info = note()
        outer.addWidget(self.info)
        observ=W.QHBoxLayout()
        self.observable=W.QComboBox();self.observable.addItems(["节点 Vz","节点 Vx","DAS 应变率（水平直光纤）"])
        from ui_help import DAS_TIP, GAUGE_TIP
        self.observable.setToolTip(DAS_TIP)
        self.gauge=spin(8,0.01,10000,2)
        self.gauge.setKeyboardTracking(False)
        self.gauge.setToolTip(GAUGE_TIP)
        gauge_label=W.QLabel("标距 (m)");gauge_label.setToolTip(GAUGE_TIP)
        observ.addWidget(self.observable);observ.addWidget(gauge_label);observ.addWidget(self.gauge)
        self.das_help=button("DAS说明",lambda:W.QMessageBox.information(self,"DAS观测与标距",DAS_TIP+'\n\n'+GAUGE_TIP))
        self.das_help.setToolTip("查看计算公式、分量、单位、标距选取和支持范围")
        observ.addWidget(self.das_help)
        self.compare_region=button('有洞／无洞对比…',self.comparison_requested.emit)
        self.compare_region.setToolTip('可选：载入匹配的无洞实际结果，共用分量、面波窗、PS/FH和权重进行对比。不会自动启动正演。')
        self.compare_region.hide();observ.addWidget(self.compare_region)
        self.observation_note=note();observ.addWidget(self.observation_note,1);outer.addLayout(observ)
        self.observable.currentIndexChanged.connect(self.change_observable)
        self.gauge.valueChanged.connect(self.change_observable)
        split = W.QSplitter()
        outer.addWidget(split,1)
        controls, side = panel()
        controls.setMinimumWidth(230)
        controls.setMaximumWidth(315)
        scroll = W.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(controls)
        self.analysis_scroll=scroll
        scroll.setMinimumWidth(245)
        self.tool_panel = tool_panel = W.QWidget()
        tool_layout = W.QVBoxLayout(tool_panel)
        tool_layout.setContentsMargins(0,0,0,0)
        tool_layout.addWidget(scroll,1)
        split.addWidget(tool_panel)
        side.addWidget(heading("面波时间窗"))
        self.side_choice = W.QComboBox()
        self.side_choice.addItems(["炮点右侧 →","炮点左侧 ←"])
        self.side_choice.setToolTip("选择炮点一侧用于频散成像；原始单炮仍显示全部道，台阵中心理论参考柱不移动。")
        side.addWidget(self.side_choice)
        self.inputs = {}
        form=W.QFormLayout()
        side.addLayout(form)
        for label,key,default,minimum,decimals in [
                ("最近偏移 (m)","min_offset",0,0,1),("最远偏移 (m)","max_offset",0,0,1),
                ("慢端视群速 (m/s)","vslow",180,.001,2),("快端视群速 (m/s)","vfast",1100,.001,2),
                ("边缘余量 (s)","pad",.08,.001,3)]:
            self.inputs[key]=spin(default,minimum,20000,decimals)
            form.addRow(label,self.inputs[key])
        self.inputs["max_offset"].setSpecialValueText("全排列")
        self.auto_gate=button("一键估算面波窗与扫描范围",self.fill_gate)
        self.auto_gate.setToolTip("依据本炮局部 R0 群速度估算窗，按相速度估算扫描范围。保留已选的方向和偏移距。")
        side.addWidget(self.auto_gate)
        example_analysis=button("填入匹配模板的演示窗口",self.fill_showcase_analysis)
        example_analysis.setToolTip("核对当前完整Vz记录、时间轴、接收坐标及模型散列，只填入匹配案例的显式窗口。不自动成像，不修改原始波形；无法分离的体波与散射仍保留。")
        side.addWidget(example_analysis)
        side.addLayout(row(button("读入分析预设…",self.load_analysis_preset),
                           button("保存分析预设…",self.save_analysis_preset)))
        self.pick_gate=button("在单炮图上点选窗",self.start_gate_pick)
        self.pick_gate.setToolTip("在所选侧的接收道上先点快到波前，再点慢到波尾。由水平偏移距 / 到时换算两条视群速度，余量另加。")
        side.addWidget(self.pick_gate)
        self.pick_fast=None
        self.theory = W.QCheckBox("显示台阵中心理论曲线")
        self.theory.setChecked(True)
        side.addWidget(self.theory)
        self.theory_order=W.QSpinBox();self.theory_order.setRange(0,8);self.theory_order.setValue(0)
        self.theory_order.setToolTip("0为基阶R0；选择N会计算R0至RN。始终取完整原始台阵两端水平中点的一维柱，底层延伸半空间；选道、窗口、PS/FH或DAS标距不改变参考位置。高阶可能有截止、无根或求解失败，不补造曲线；起伏地表下只作局部一维参考。")
        side.addLayout(row(W.QLabel("最高阶（R或A/S）"),self.theory_order))
        self.init_theory_controls(side)
        side.addWidget(heading("频率与相速度扫描"))
        scan=W.QFormLayout()
        side.addLayout(scan)
        for label,key,default in [("起始频率 (Hz)","fmin",5),("终止频率 (Hz)","fmax",30),
                ("最低相速度 (m/s)","cmin",160),("最高相速度 (m/s)","cmax",1000),("速度步长 (m/s)","dc",5)]:
            self.inputs[key]=spin(default,.1,20000,1)
            scan.addRow(label,self.inputs[key])
        self.spatial_choice = W.QComboBox()
        self.spatial_choice.addItems(["等权（默认）","Hann（可选空间加权）"])
        self.spatial_choice.setToolTip("默认等权。Hann仅按接收道序号给阵列两端降权，不使用理论曲线；可降低旁瓣，但会展宽主峰、改变弱能量与峰位。理论曲线不参与成像。FH另保留r·dr积分权重。")
        observ.insertWidget(3,W.QLabel("空间权重"));observ.insertWidget(4,self.spatial_choice)
        self.method_choice=W.QComboBox();self.method_choice.addItems(["PS","FH"])
        self.method_choice.setToolTip("PS：保留道间振幅的定向相移叠加，不是逐道U/|U|的phase-only方法。FH：本项目H0(1)核、归一化r·dr权重及ωk因子；dr端点用整间距，非梯形半权。两者功率尺度不同。二维线源、起伏地表及有限标距DAS上的FH仅作对照，不保证峰值无偏；理论曲线不随方法改变。")
        observ.insertWidget(3,W.QLabel("成像方法"));observ.insertWidget(4,self.method_choice)
        self.method_choice.currentIndexChanged.connect(self.invalidate)
        self.compute = button("由面波窗计算频散谱",self.analyze,True)
        self.export = button("导出谱与窗 (.npz)",self.export_result)
        self.power_scale=W.QComboBox()
        self.power_scale.addItem("功率（dB）","db")
        self.power_scale.addItem("线性功率（%）","percent")
        self.power_scale.setToolTip("两种色标共用本图非混叠区的最大功率。线性百分比显示各点功率相对峰值的百分比，dB 展开弱信号。")
        self.db_floor=spin(-40,-100,-10,0)
        self.db_floor.setMaximumWidth(90)
        self.db_floor.setToolTip("相对于本图非混叠区全局功率峰值。低于此值的功率使用底色显示，原始数值仍保留。设为−60或−80dB可查看更弱条纹；不会重新计算、滤波或删除能量。")
        self.processing_button=button("查看本次处理明细…",self.show_processing)
        self.processing_button.setEnabled(False)
        tool_layout.addWidget(self.compute)
        tool_layout.addWidget(self.export)
        side.addStretch()
        plot, layout = panel()
        self.display_controls=W.QWidget()
        display_row=W.QHBoxLayout(self.display_controls);display_row.setContentsMargins(0,0,0,0)
        display_row.addWidget(self.power_scale)
        self.db_floor_label=W.QLabel("下限 (dB)")
        display_row.addWidget(self.db_floor_label);display_row.addWidget(self.db_floor)
        display_row.addWidget(self.processing_button)
        display_row.addWidget(button("理论参考设置…",lambda:self.analysis_scroll.ensureWidgetVisible(self.theory_selection,0,120)))
        display_row.addStretch()
        layout.addWidget(self.display_controls)
        self.processing_summary=note()
        self.processing_summary.setTextInteractionFlags(C.Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.processing_summary)
        self.cv = canvas()
        layout.addWidget(self.cv,1)
        self.cv.mpl_connect("button_press_event",self.pick_arrival)
        self.explain = note()
        self.explain.setTextInteractionFlags(C.Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.explain)
        split.addWidget(plot)
        split.setSizes([258,835])
        for key,w in self.inputs.items():
            w.setToolTip(TIPS[key])
            w.valueChanged.connect(self.invalidate)
        self.side_choice.currentIndexChanged.connect(self.invalidate)
        self.spatial_choice.currentIndexChanged.connect(self.invalidate)
        self.theory.toggled.connect(self.refresh_theory)
        self.theory_order.valueChanged.connect(self.refresh_theory)
        self.analysis_mode.toggled.connect(self.invalidate)
        self.power_scale.currentIndexChanged.connect(self.draw)
        self.db_floor.valueChanged.connect(self.draw)
        self.export.setEnabled(False)

        self.analysis_origin = None
        self.default_settings = self.scene_settings()

    def scene_settings(self):
        return dict(options=self.options(), observable=getattr(self,'scene_observable',self.observable.currentIndex()), gauge_m=self.gauge.value(),
                    theory_enabled=self.theory.isChecked(), theory_max_mode=self.theory_order.value(),
                    plate_theory_enabled=self.plate_theory.isChecked(),
                    theory_selection=self.theory_selection.currentData(),theory_backend=self.theory_backend.currentData(),
                    power_scale=self.power_scale.currentData(),db_floor=self.db_floor.value(), origin_model_sha256=getattr(self,'analysis_origin',None))

    def check_scene_settings(self,s):
        s=check_settings(s)
        self.check_analysis_preset(dict(options=s['options']))
        if round(s['gauge_m'],self.gauge.decimals())!=s['gauge_m']:raise ValueError('DAS标距超出界面精度。')
        floor=s.get('db_floor',-40)
        if not isinstance(floor,(int,float)) or not np.isfinite(floor) or not -100<=floor<=-10:raise ValueError('色标下限无效。')
        return s

    def apply_scene_settings(self,s):
        s=self.check_scene_settings(s)
        self.apply_analysis_preset(dict(options=s['options'],origin_model_sha256=s.get('origin_model_sha256')),validate_gather=False)
        widgets=[self.gauge,self.observable,self.theory,self.theory_order,self.db_floor,self.power_scale,self.theory_selection,self.theory_backend,self.plate_theory]
        blockers=[C.QSignalBlocker(w) for w in widgets]
        self.gauge.setValue(s['gauge_m']);self.observable.setCurrentIndex(s['observable'])
        self.theory.setChecked(s['theory_enabled']);self.theory_order.setValue(s['theory_max_mode'])
        self.theory_selection.setCurrentIndex(self.theory_selection.findData(s['theory_selection']))
        self.theory_backend.setCurrentIndex(self.theory_backend.findData(s['theory_backend']))
        self.plate_theory.setChecked(s['plate_theory_enabled'])
        self.power_scale.setCurrentIndex(self.power_scale.findData(s['power_scale']))
        self.db_floor.setValue(s.get('db_floor',-40))
        del blockers
        self.scene_observable=s['observable']
        if self.raw_gather is not None:
            self.change_observable()
            if self.observable.currentIndex()!=s['observable']:
                # Imported/pre-run settings are candidates for the scene, not a
                # promise that the currently displayed historical shot supports them.
                self.scene_observable=s['observable']
                self.observation_note.setText(self.observation_note.text()+"；场景仍保存所选观测候选，尚未应用于当前记录。")
        self.theory_order.setEnabled(self.theory.isEnabled() and self.theory.isChecked())
        self.invalidate()
        self.update_theory_route()

    def set_gather(self,g,path=None):
        self.raw_gather=g
        with C.QSignalBlocker(self.observable):self.observable.setCurrentIndex(0)
        self.observable.setEnabled(g is not None and g.vx is not None)
        self.gauge.setEnabled(g is not None and g.vx is not None)
        dx=np.diff(g.x) if g is not None else np.array([])
        self.gauge.setSingleStep(float(2*dx[0]) if len(dx) and np.allclose(dx,dx[0],rtol=1e-10,atol=1e-7) else 1.)
        self.observation_note.setText("" if g is not None and g.vx is not None else "旧记录未保存Vx；新弹性正演将同时保存。")
        self.gather,self.path = g,path
        background = bool(g is not None and g.meta.get('region_case'))
        self.compare_region.setVisible(background)
        self.theory.setText('显示无洞背景一维参考曲线' if background else '显示台阵中心理论曲线')
        self.auto_gate.setText('按无洞背景估算窗与扫描范围' if background else '一键估算面波窗与扫描范围')
        from theory_reference import SELECTIONS
        for i, (_, label) in enumerate(SELECTIONS):
            self.theory_selection.setItemText(i, ('无洞背景 · ' if background else '') + label)
        material=g.meta.get('model',{}) if g is not None else {}
        theory_supported=bool(material) and material.get('physics','elastic')=='elastic'
        self.theory.setEnabled(theory_supported)
        self.update_theory_route()
        self.theory.setToolTip('完整台阵中心：弹性对照可选disba/QEDisp；VTI及沿轴HTI用SASEM，均匀或两层ISO衰减背景用SLS复波数。起伏地形只作局部一维参考。')
        self.theory_order.setEnabled(theory_supported and self.theory.isChecked())
        gate_supported=theory_supported and kind(material)=='isotropic' and not attenuating(material) and material.get('domain_kind')!='free_plate'
        self.theory_order.setToolTip('自由板：N包含A0–AN及S0–SN；普通地基：R0–RN。完整台阵中心参考，不随选道改变；有限搜索保留缺根。')
        if material.get('domain_kind')=='free_plate':self.theory.setToolTip('水平无限自由板Rayleigh–Lamb A/S模态；保留有限板厚，上下无牵引；不使用半空间Rayleigh曲线。')
        if material.get('physics')=='coupled':self.theory.setToolTip('实际固—流体—固通用理论入口尚未验收；不能套用自由板或无洞固体参考。已算SASEM研究对照在“文件…→模板验证总览与已算理论对照”，只适用于其中冻结模型。')
        self.auto_gate.setEnabled(gate_supported)
        self.auto_gate.setToolTip('由局部R0群速度估算，须检查实际波包。' if gate_supported else 'R0自动窗不适用于固—流体—固、板波、TI和Q；请手动设窗或拾取，不套用纯固体半空间群速度。')
        self.analysis_mode.setChecked(False)
        self.save_btn.setEnabled(path is not None)
        self.qc.setEnabled(g is not None)
        self.analysis_mode.setEnabled(g is not None and g.meta["component"]!="pressure")
        if g is None:
            self.info.setText("完成主动源正演，或从模型页读入带结果的模板，即可查看本炮记录。")
            self.invalidate()
            self.shot_changed.emit(None)
            return
        self.side_choice.setCurrentIndex(0 if np.count_nonzero(g.x>g.meta["source_x"])>=3 else 1)
        is_vz = g.meta["component"]!="pressure"
        self.compute.setEnabled(is_vz)
        label = ("声弹耦合 · " if material.get('physics')=='coupled' else "弹性 · ")+g.meta["component"] if is_vz else "声学 · 压力"
        sec = g.meta.get("solver_seconds")
        elapsed = f"  ·  求解 {sec:.1f} s" if sec is not None else ""
        self.info.setText(f"{label}    |    炮点 {g.meta['source_x']:g} m    ·    {len(g.x)} 道 × {len(g.t)} 点    ·    {np.median(np.diff(g.t))*1000:.3g} ms    ·    {Path(g.meta['run']).name}{elapsed}")
        if g.meta.get("preview_note"):
            self.info.setText(self.info.text()+"  ·  附波场预览说明")
        check=g.meta.get("solver_checks",{})
        detail=f"实际记录从 {g.t[0]:.6g} 到 {g.t[-1]:.6g} s。原始运行目录：{g.meta['run']}"
        if "max_cfl" in check:
            self.info.setText(self.info.text()+f"  ·  实算 CFL {check['max_cfl']:.3f}")
            detail+=f"\n实际网格 CFL {check['max_cfl']:.4g}，求解器建议最大 dt {check.get('dt_limit_s','—')} s；解析频率上限 {check.get('resolved_hz','—')} Hz。"
        monitor=g.meta.get("boundary_monitor",{})
        if monitor:
            names={'growth_alert':'边界增长提示：需查看原始波场','nonfinite_alert':'波场含非有限数',
                   'no_alert_not_validation':'未触发边界增长提示（不代表验收通过）',
                   'insufficient_frames':'边界监测帧数不足','monitor_error':'边界监测失败','not_applicable':'当前边界不适用CPML增长监测'}
            self.info.setText(self.info.text()+" · "+names.get(monitor.get('state'),"边界监测未完成"))
            detail+="\n"+json.dumps({k:v for k,v in monitor.items() if k!='frames'},ensure_ascii=False)
        if g.meta.get("preview_note"):detail+="\n"+g.meta["preview_note"]
        if g.meta.get('theory_reference_scope'):
            detail+='\n'+g.meta['theory_reference_scope']
            self.info.setText(self.info.text()+' · 无洞背景参考；含洞体散射')
        self.info.setToolTip(detail)
        self.invalidate()
        self.shot_changed.emit(g)

    def change_observable(self,*_):
        self.scene_observable=self.observable.currentIndex()
        g=getattr(self,"raw_gather",None)
        if g is None:return
        try:
            mode=self.observable.currentIndex()
            if mode and g.vx is None:
                raise ValueError("当前记录未保存Vx，不能应用水平速度或DAS观测；原始Vz仍可查看。")
            if mode==0:new=g
            elif mode==1:
                new=Gather(g.t,g.x,g.vx,dict(g.meta,component="Vx",unit="m/s"))
            else:
                from das_observation import horizontal_strain_rate
                c=g.meta.get("model")
                if not c or np.ptp(elevation(c,g.x))>1e-7:raise ValueError("当前只支持水平平地直光纤；起伏光纤尚未验证。")
                L=self.gauge.value();centers=g.x[(g.x-L/2>=g.x[0])&(g.x+L/2<=g.x[-1])]
                d=horizontal_strain_rate(g.t,g.x,g.vx,centers,L)
                new=Gather(g.t,centers,d["strain_rate"],dict(g.meta,component="DAS_strain_rate",unit="s^-1",gauge_length_m=L,
                    observation_operator=d["operator"],reference_center_x_m=array_center(g.x)))
            self.gather=new
            physics_label='声弹耦合 · ' if g.meta.get('model',{}).get('physics')=='coupled' else ''
            self.info.setText(physics_label+f"当前 {new.meta['component']} · {len(new.x)}道 × {len(new.t)}点 · 原始节点{len(g.x)}道 · {Path(g.meta['run']).name}")
            self.observation_note.setText(f"{new.meta['component']} · {len(new.x)}道 · "+("s⁻¹；边缘缺完整标距的中心不输出" if mode==2 else "m/s"))
            self.invalidate()
        except (ValueError,TypeError) as exc:
            with C.QSignalBlocker(self.observable):self.observable.setCurrentIndex(0)
            self.change_observable()
            self.observation_note.setText("未应用，已返回节点Vz："+str(exc))

    def open(self,path):
        try:
            self.set_gather(Gather.open(path),path)
            return True
        except (OSError,KeyError,ValueError,TypeError) as exc:
            W.QMessageBox.warning(self,"读取记录失败",str(exc))
            return False

    def choose(self):
        p,_ = W.QFileDialog.getOpenFileName(self,"打开工作台单炮记录","","单炮 (*.npz)")
        if p:
            owner=W.QWidget.window(self)
            if hasattr(owner,'load_template'):
                try:
                    with np.load(p,allow_pickle=False) as a:
                        regional='case' in json.loads(str(a['metadata']))
                    if regional:
                        owner.load_template(Path(p));return
                except (OSError,KeyError,ValueError,TypeError):pass
            self.open(Path(p))

    def save_gather(self):
        p,_ = W.QFileDialog.getSaveFileName(self,"保存当前单炮","shot.npz","单炮 (*.npz)")
        if p and Path(p).resolve()!=self.path.resolve():
            if self.gather is not self.raw_gather:
                np.savez_compressed(p,t=self.gather.t,x=self.gather.x,traces=self.gather.traces,
                                    metadata=json.dumps(self.gather.meta))
            else:shutil.copyfile(self.path,p)

    def start_gate_pick(self):
        self.pick_fast=0.
        self.explain.setText("在完整单炮图上点击面波包快端，再点慢端。点在已选偏移距内、源峰零时刻之后。")

    def pick_arrival(self,event):
        if (self.pick_fast is None or event.button!=1 or not self.cv.figure.axes or
                event.inaxes is not self.cv.figure.axes[0] or event.ydata is None):
            return
        g=self.gather
        x=float(g.x[np.argmin(np.abs(g.x-event.xdata))])
        offset=(x-g.meta["source_x"])*(1 if self.side_choice.currentIndex()==0 else -1)
        limit=self.inputs["max_offset"].value()
        if (offset<=0 or offset<self.inputs["min_offset"].value() or (limit and offset>limit) or event.ydata<=0):
            self.explain.setText("请点击所选一侧与偏移距范围内的道，到时必须大于零。")
            return
        speed=round(offset/event.ydata,2)
        if not self.inputs["vfast"].minimum()<=speed<=self.inputs["vfast"].maximum():
            self.explain.setText("该点换算的速度超出范围，请重新选点。")
            return
        if self.pick_fast==0.:
            self.pick_fast=speed
            event.inaxes.plot(x,event.ydata,"o",color=ORANGE,ms=6)
            self.cv.draw_idle()
            self.explain.setText(f"快端 {speed:g} m/s。请点击波包慢端。")
            return
        if speed>=self.pick_fast:
            self.explain.setText("第二点的视速度需小于快端，请重新点选慢端。")
            return
        with C.QSignalBlocker(self.inputs["vfast"]), C.QSignalBlocker(self.inputs["vslow"]):
            self.inputs["vfast"].setValue(self.pick_fast)
            self.inputs["vslow"].setValue(speed)
        self.invalidate()

    def fill_gate(self):
        try:
            values=suggest_gate(self.gather,self.side_choice.currentIndex()==0,
                                self.inputs["min_offset"].value(),self.inputs["max_offset"].value())
            for key,value in values.items():
                w=self.inputs[key]
                if not w.minimum()<=value<=w.maximum():
                    raise ValueError(f"{key} 的建议值超出界面支持范围，请手动设置。")
            for key,value in values.items():
                with C.QSignalBlocker(self.inputs[key]):
                    self.inputs[key].setValue(value)
            self.invalidate()
            if self.window is not None:
                self.explain.setText(self.explain.text()+" 已按局部 R0 群速填入候选窗，请检查首末道波包。")
        except (ValueError,ImportError,ArithmeticError) as exc:
            self.explain.setText("未能估算："+str(exc))
            self.auto_gate.setToolTip(str(exc))

    def check_analysis_preset(self,preset,gather=None):
        from analysis_presets import checked_options
        o=checked_options(preset["options"])
        # Validate all values and this gather before changing any control.
        for k,w in self.inputs.items():
            v=o[k]
            if not w.minimum()<=v<=w.maximum() or abs(round(v,w.decimals())-v)>1e-9:
                raise ValueError(f"{k} 超出界面范围或精度。")
        if gather is not None:
            spectrum(gather,**o)
        return o

    def apply_analysis_preset(self,preset,validate_gather=True):
        o=self.check_analysis_preset(preset,self.gather if validate_gather else None)
        self.analysis_origin=preset.get('origin_model_sha256')
        for k,w in self.inputs.items():
            with C.QSignalBlocker(w):w.setValue(o[k])
        with C.QSignalBlocker(self.side_choice):self.side_choice.setCurrentIndex(0 if o["right"] else 1)
        with C.QSignalBlocker(self.method_choice):self.method_choice.setCurrentText(o.get("method","PS"))
        with C.QSignalBlocker(self.spatial_choice):self.spatial_choice.setCurrentIndex(0 if o['spatial_weighting']=='equal' else 1)
        self.invalidate()
        same=(self.gather is not None and preset.get("origin_model_sha256") is not None
              and preset["origin_model_sha256"]==model_hash(self.gather.meta.get("model")))
        self.explain.setText(self.explain.text()+(" 预设来源模型匹配，但不构成验收。" if same else " 已填入分析候选，未认证当前模型适用性。")+" 请检查窗口后计算。")
        if preset.get('note'):self.explain.setText(self.explain.text()+' '+preset['note'])

    def fill_example_analysis(self):
        try:self.apply_analysis_preset(read_preset(ROOT/"examples/analysis_presets/hill18_main.json"))
        except (ValueError,TypeError,OSError) as exc:self.explain.setText("未能填入："+str(exc))

    def fill_showcase_analysis(self,hann=False):
        from analysis_presets import matched_showcase_preset
        try:
            preset=matched_showcase_preset(self.gather)
            if hann:preset['options']['spatial_weighting']='hann'
            self.apply_analysis_preset(preset)
        except (ValueError,OSError,KeyError,TypeError) as exc:self.explain.setText(str(exc))

    def load_analysis_preset(self):
        p,_=W.QFileDialog.getOpenFileName(self,"读入分析预设","","分析预设 (*.json)")
        if p:
            try:self.apply_analysis_preset(read_preset(p))
            except (ValueError,TypeError,OSError) as exc:self.explain.setText("未能读入："+str(exc))

    def save_analysis_preset(self):
        p,_=W.QFileDialog.getSaveFileName(self,"保存分析预设","analysis.json","分析预设 (*.json)")
        if p:
            try:
                preset=make_preset(self.options(),self.gather.meta.get("model") if self.gather else None)
                Path(p).write_text(json.dumps(preset,ensure_ascii=False,indent=2))
            except (ValueError,TypeError,OSError) as exc:self.explain.setText("未能保存："+str(exc))

    def options(self):
        return dict(right=self.side_choice.currentIndex()==0,gated=True,
                    spatial_weighting="equal" if self.spatial_choice.currentIndex()==0 else "hann",
                    **({"method":"FH"} if self.method_choice.currentText()=="FH" else {}),
                    **{k:w.value() for k,w in self.inputs.items()})

    def invalidate(self,*_):
        self.stop_theory()
        self.theory_status.clear()
        self.pick_fast=None
        self.result = self.curve = None
        self.reference = None
        self.curves = {}
        self.window = None
        analysis = self.analysis_mode.isChecked()
        self.tool_panel.setVisible(analysis)
        self.analysis_mode.setText("收起频散分析" if analysis else "面波窗与频散…")
        self.export.setEnabled(False)
        self.processing_button.setEnabled(False)
        self.compute.setEnabled(False)
        for key,w in self.inputs.items():
            field_tip(w,TIPS[key])
        if self.gather is None:
            self.explain.clear()
            self.draw()
            return
        msg = "原始单炮 · 共用振幅色标（第 99 百分位显示裁剪）。"
        if analysis:
            try:
                keys=("right","vslow","vfast","pad","min_offset","max_offset")
                opt=self.options()
                self.window=gather_window(self.gather,**{k:opt[k] for k in keys})
                opt=self.options()
                dt=float(np.median(np.diff(self.gather.t)))
                f=np.fft.rfftfreq(len(self.gather.t),dt)
                if not (0<opt["fmin"]<opt["fmax"]<=.5/dt) or len(frequency_band_indices(f,opt["fmin"],opt["fmax"]))<2:
                    raise ValueError("频带须在 Nyquist 以内且至少包含两个真实 FFT 频点。")
                if not 1 <= (opt["cmax"]-opt["cmin"])/opt["dc"] <= 2500:
                    raise ValueError("相速度范围与步长需产生 2–2501 个速度点。")
                self.compute.setEnabled(True)
                msg=f"橙线标出面波窗，检查波包后再计算。参考柱固定于完整台阵中心 x = {array_center(self.gather.x):g} m。"
                if np.max(self.window["window_end"])>self.gather.t[-1]:
                    msg+=" 窗尾超出记录终点，检查传播时长。"
                    field_tip(self.inputs["vslow"],TIPS["vslow"],msg)
            except ValueError as exc:
                msg=str(exc)
                if "频" in msg:
                    keys=("fmin","fmax")
                elif "相速度" in msg:
                    keys=("cmin","cmax","dc")
                elif "偏移" in msg or "三道" in msg:
                    keys=("min_offset","max_offset")
                else:
                    keys=("vslow","vfast","pad")
                for key in keys:
                    field_tip(self.inputs[key],TIPS[key],msg)
        self.explain.setToolTip(msg)
        self.explain.setText(msg)
        self.draw()

    def analyze(self):
        if self.window is None or not self.analysis_mode.isChecked():
            return
        g = self.gather
        self.result = self.curve = None
        self.reference = None
        self.curves = {}
        self.export.setEnabled(False)
        self.processing_button.setEnabled(False)
        try:
            s = spectrum(g,**self.options())
        except ValueError as exc:
            self.explain.setText(str(exc))
            self.draw()
            return
        self.result = s
        theory_notes = self.calculate_theory()
        notes = []
        if np.any(s["window_end"]>g.t[-1]):
            notes.append("部分道的窗尾超出记录终点。")
        notes.append(f"参与 {len(s['offset'])} 道，偏移距 {s['offset'][0]:g}–{s['offset'][-1]:g} m；Δf = {np.diff(s['f'])[0]:.2f} Hz。")
        notes.append("空间权重："+("等权。" if s["spatial_weighting"]=="equal" else "Hann；降低旁瓣，主峰变宽，峰位可能变化。"))
        notes.append("全谱共用功率标尺，白区超出空间采样限；亮带可能包含体波或反射。")
        if s["method"]=="FH":notes.append("FH为本项目加权Hankel对照，保留r·dr积分权重；短孔径低频峰值可能偏移，不代表更高精度。")
        self.analysis_notes=notes
        self.explain.setText(" ".join(theory_notes+notes))
        self.export.setEnabled(True)
        self.processing_button.setEnabled(True)
        self.draw()

    def display_settings(self):
        s=self.result
        peak=float(np.max(s["power"][~s["alias"]])) if s is not None else None
        linear=self.power_scale.currentData()=="percent"
        return dict(normalization="global_peak_nonaliased",reference_power=peak,scale=self.power_scale.currentData(),
                    units="%" if linear else "dB",limits=[0.,100.] if linear else [self.db_floor.value(),0.],
                    formula="100*power/reference_power" if linear else "10*log10(power/reference_power)",
                    db_min=None if linear else float(self.db_floor.value()),db_max=None if linear else 0.,
                    interpolation="nearest",alias_pixels="masked for display only",raw_power_modified=False,
                    gather_display="all channels and full time; shared amplitude scale clipped at absolute-amplitude 99th percentile; no per-trace normalization")

    def processing_settings(self):
        s=self.result;g=self.gather
        if s is None or g is None:return None
        hashes=g.meta.get("hashes")
        return dict(data_source="SPECFEM2D" if isinstance(hashes,dict) and hashes.get("xspecfem2D") else "imported_record",
                    run=g.meta.get("run"),component=g.meta["component"],method=s["method"],
                    operator_version=s["operator_version"],spatial_weighting=s["spatial_weighting"],
                    trace_normalization=s["trace_normalization"],demean=s["demean"],
                    time_window="source-offset group-velocity bounds; sin-squared edge taper",
                    band_hz=[float(s["f"][0]),float(s["f"][-1])],
                    frequency_filter="none after window FFT; only requested FFT bins are imaged",
                    theory_used_in_imaging=False,spectrum_smoothing=False)

    def show_processing(self):
        p=self.processing_settings()
        if p is None:return
        s=self.result;d=self.display_settings()
        method="保幅定向相移叠加；没有逐道U/|U|相位归一化" if p["method"]=="PS" else "H0(1)核，归一化r·dr权重和ωk因子；端点dr使用整间距"
        weight="等权基线" if p["spatial_weighting"]=="equal" else "Hann空间加权：改变相干叠加，降低旁瓣但可展宽主峰"
        source="SPECFEM2D（记录附有求解器散列）" if p["data_source"]=="SPECFEM2D" else "导入记录（未提供求解器散列）"
        text=(f"数据来源：{source}；分量：{p['component']}\n记录：{p['run'] or '未提供运行路径'}\n\n"
              f"完整单炮始终保留，频散仅用当前所选{len(s['indices'])}道的面波窗。\n"
              "单炮显示：全道、全时段共用振幅色标，按绝对振幅99百分位裁色；非逐道归一化，原始波形未改。\n"
              f"实际偏移：{s['offset'][0]:g}–{s['offset'][-1]:g} m\n"
              "时间处理：窗内加权去均值，然后乘带sin²渐变边缘的面波窗。\n"
              f"成像：{p['method']}；{method}。\n空间权重：{weight}。\n"
              f"实际FFT频点：{p['band_hz'][0]:.4g}–{p['band_hz'][1]:.4g} Hz；保留道间振幅，不做AGC或白化。\n"
              "未使用理论线掩膜、图像平滑或旁瓣消除；理论线只作覆盖。\n\n"
              f"显示：{d['formula']}，参考功率取本图非混叠区全局峰值。\n"
              f"色标：{d['limits'][0]:g} 至 {d['limits'][1]:g} {d['units']}。空间混叠区留白，导出保留原始功率。\n"
              "不同图各自归一化，颜色不能直接比较绝对功率。\n"
              "导出包含原始线性功率、实际时间/空间权重、加窗波形、理论参考柱及本页处理/显示说明。")
        popup=W.QDialog(self);popup.setWindowTitle("本次频散处理明细");popup.resize(760,570)
        layout=W.QVBoxLayout(popup);body=W.QPlainTextEdit();body.setReadOnly(True);body.setPlainText(text)
        layout.addWidget(body);layout.addWidget(button("关闭",popup.accept));popup.exec()

    def draw(self,*_):
        g,s = self.gather,self.result
        linear=self.power_scale.currentData()=="percent"
        self.db_floor.setVisible(not linear)
        self.db_floor_label.setVisible(not linear)
        self.display_controls.setVisible(s is not None)
        if s is None:
            self.processing_summary.setText("" if g is None else "完整原始单炮；默认等权，Hann仅为可选空间加权。理论曲线只作叠加参考，不参与成像或筛选能量。")
        else:
            p=self.processing_settings();weight="等权基线" if p["spatial_weighting"]=="equal" else "Hann加权（降低旁瓣，可能展宽主峰）"
            method="PS保幅相移" if p["method"]=="PS" else "FH：H0(1)＋r·dr＋ωk"
            source="SPECFEM2D记录" if p["data_source"]=="SPECFEM2D" else "导入记录（来源待核）"
            scale_text="0–100% 线性功率" if linear else f"{self.db_floor.value():g} 至 0 dB"
            self.processing_summary.setText(f"{source} · {p['component']} · 面波窗＋加权去均值 · {method} · {weight}\n"
                f"显示：全图非混叠峰值归一化，{scale_text}。理论线不参与成像。")
        fig = self.cv.figure
        fig.clear()
        if g is None:
            self.cv.draw_idle()
            return
        if s is None:
            ax = fig.subplots()
        else:
            ax,fc = fig.subplots(1,2,gridspec_kw={"width_ratios":[1,1.12]})
        step_x = np.median(np.diff(g.x))
        step_t = np.median(np.diff(g.t))
        clip = max(np.percentile(np.abs(g.traces),99),np.finfo(float).tiny)
        window=self.window
        ax.pcolormesh(g.x,g.t,g.traces,cmap="RdBu_r",vmin=-clip,vmax=clip,shading="nearest",rasterized=True)
        ax.set(title=f"Full shot gather  /  {g.meta['component']}",xlabel="Receiver x (m)",ylabel="Time (s)")
        ax.set_ylim(g.t[-1]+step_t/2,g.t[0]-step_t/2)
        ax.set_xlim(g.x[0]-step_x/2,g.x[-1]+step_x/2)
        style_axis(ax)
        if window is not None:
            x = g.x[window["indices"]]
            begin,end=window["window_start"],window["window_end"]
            ax.plot(x,begin,color="#b25e05",ls="--",lw=1)
            ax.plot(x,end,color="#b25e05",ls="--",lw=1)
            pad = self.inputs["pad"].value()
            for edge,inner in [(begin,begin+pad),(end,end-pad)]:
                ax.fill_between(x,edge,inner,color="#ffa92b",alpha=.2,linewidth=0)
        if s is not None:
            power = np.where(s["alias"],np.nan,s["power"])
            relative = power/np.nanmax(power)
            values = 100*relative if linear else 10*np.log10(np.maximum(relative,1e-12))
            lower,upper = (0,100) if linear else (self.db_floor.value(),0)
            art = fc.pcolormesh(s["f"],s["c"],values,cmap="magma",vmin=lower,vmax=upper,shading="nearest")
            if self.reference and self.theory.isChecked():
                colors=["#70ebee","#ffffff","#64ef91","#ffd166","#b6a3ff","#ff90c3","#4f9fff","#00c9a7","#e8d5b7"]
                lines=0
                for purpose,group in self.reference.get('groups',{}).items():
                    backend=group.get('provenance',{}).get('backend','')
                    for mode,curve in group.get('curves',{}).items():
                        # NaN at missing requested frequencies prevents joining across cutoffs/failures.
                        y=np.full(len(s['f']),np.nan)
                        for f,v in zip(*curve):y[np.isclose(s['f'],f,rtol=1e-10)]=v
                        short_backend="QED" if backend.startswith("QED") else "SASEM" if backend.startswith("SASEM") else "SLS" if backend.startswith("SLS") else "disba"
                        label=f"{'Actual' if purpose=='actual' else 'Elastic'} {short_backend} R{mode}"
                        if g.meta.get('region_case'):label='Background '+label
                        if backend.startswith('Rayleigh'):label=('Actual ' if purpose=='actual' else 'Elastic ')+group['curve_labels'][mode]
                        if purpose=='plate':label='Free-plate approx. '+group['curve_labels'][mode]
                        fc.plot(s['f'],y,marker='.' if np.isfinite(y).sum()==1 else None,color=colors[int(mode)%len(colors)],ls='-' if purpose=='actual' else '--',lw=1.3,label=label);lines+=1
                if lines:fc.legend(loc="lower center",bbox_to_anchor=(.5,1.02),ncol=3,fontsize=7,frameon=True,facecolor="#d4dbd5",edgecolor="none")
            fc.set(xlabel="Frequency (Hz)",ylabel="Phase velocity (m/s)",title=f"{s.get('method','PS')} / {s['spatial_weighting']} / {g.meta['component']}")
            if self.reference and any(group.get("curves") for group in self.reference.get("groups",{}).values()):fc.set_title(fc.get_title(),pad=32)
            fc.set_ylim(s["c"][0],s["c"][-1])
            style_axis(fc)
            fig.colorbar(art,ax=fc,location="bottom",shrink=.82,pad=.1,label="Power relative to global peak (%)" if linear else "Power relative to global peak (dB)")
        self.cv.draw_idle()

    def reference_mode_label(self,mode):
        ref=self.reference or {}
        group=ref.get('groups',{}).get(ref.get('diagnostic_reference','actual'),{})
        return group.get('curve_labels',{}).get(str(mode),f'R{mode}')

    def inspect_traces(self):
        g,s = self.gather,self.result
        popup = W.QDialog(self)
        popup.setWindowTitle("单炮质检")
        popup.resize(880,540)
        layout = W.QVBoxLayout(popup)
        cv = canvas()
        layout.addWidget(cv,1)
        with_theory = s is not None and self.curve is not None
        axes = cv.figure.subplots(1,2 if with_theory else 1,squeeze=False)[0]
        ids = np.argsort(np.abs(g.x-g.meta["source_x"])) if s is None else s["indices"]
        for row,index in enumerate([0,len(ids)//2,len(ids)-1]):
            trace = g.traces[:,ids[index]]
            norm = np.max(np.abs(trace))
            trace = trace/norm if norm else trace
            axes[0].plot(g.t,2.5*row+trace,lw=1,color=GREEN)
            if s is not None:
                axes[0].fill_between(g.t,2.5*row-1,2.5*row-1+s["weights"][:,index if s["weights"].shape[1]>1 else 0]*.24,
                                     color="#edab66",lw=0)
        chosen = ids[[0,len(ids)//2,-1]]
        axes[0].set_yticks([0,2.5,5],labels=[f"x = {g.x[i]:g} m" for i in chosen])
        axes[0].set(title="Near / middle / far traces",xlabel="Time (s)")
        style_axis(axes[0])
        if with_theory:
            f,v = self.curve
            power = np.where(s["alias"],-np.inf,s["power"])
            valid = np.isfinite(np.max(power,axis=0)) & (np.max(power,axis=0)>0)
            valid &= np.any(np.isclose(s["f"][:,None],f[None,:],rtol=1e-10),axis=1)
            ridge = s["c"][np.argmax(power[:,valid],axis=0)]
            freq = s["f"][valid]
            axes[1].axhline(0,color="#929f99",lw=1)
            axes[1].plot(freq,ridge-np.interp(freq,f,v),"o-",ms=3,color=ORANGE)
            target = self.reference.get("diagnostic_reference","actual")+" local 1-D "+self.reference_mode_label(0)
            axes[1].set(title=f"Peak minus {target}",xlabel="Frequency (Hz)",ylabel="Velocity difference (m/s)")
            style_axis(axes[1])
        layout.addWidget(note("波形按道缩放。橙条表示实际窗权重。谱峰偏差取各频率非混叠区内最大功率的位置，可能选中体波、高阶模态或弱信号。"))
        popup.exec()

    def export_result(self):
        p,_ = W.QFileDialog.getSaveFileName(self,"导出原始相干功率及计算窗","spectrum.npz","频散谱 (*.npz)")
        if p:
            ref = self.curve if self.curve else ([],[])
            np.savez_compressed(p,**self.result,t=self.gather.t,source_metadata=json.dumps(self.gather.meta),
                                windowed_traces=windowed_traces(self.gather,self.result),
                                **({"windowed_vz":windowed_traces(self.gather,self.result)} if self.gather.meta["component"]=="Vz" else {}),
                                imaging=json.dumps(self.options()),reference_f=ref[0],reference_c=ref[1],reference_label=self.reference_mode_label(0) if self.curve else "",
                                reference_model=json.dumps(self.reference),
                                display=json.dumps(self.display_settings()),processing_report=json.dumps(self.processing_settings()),
                                theory_enabled=self.theory.isChecked(),theory_max_mode=self.theory_order.value(),
                                **{f"reference_{self.reference_mode_label(m)}_{axis}":curve[i] for m,curve in self.curves.items() for i,axis in enumerate(["f","c"])})


class Window(SceneWorkflow,W.QMainWindow):
    def __init__(self):
        super().__init__()
        from app_info import APP_NAME, AUTHOR_NAME, AUTHOR_WEBSITE
        self.setWindowTitle(APP_NAME)
        self.resize(1230,900)
        self.setMinimumSize(910,760)
        root,layout = panel()
        self.setCentralWidget(root)
        title = W.QLabel(APP_NAME)
        title.setObjectName("title")
        header = W.QHBoxLayout()
        header.addWidget(title)
        self.author_link = W.QPushButton(f"{AUTHOR_NAME} · runningxinliu.github.io")
        self.author_link.setObjectName("workbenchAuthorLink")
        self.author_link.setFlat(True)
        self.author_link.setCursor(C.Qt.CursorShape.PointingHandCursor)
        self.author_link.setToolTip("作者个人主页；点击后在系统浏览器打开")
        self.author_link.clicked.connect(lambda: G.QDesktopServices.openUrl(C.QUrl(AUTHOR_WEBSITE)))
        header.addWidget(self.author_link)
        header.addStretch()
        self.shot_state = W.QLabel()
        header.addWidget(self.shot_state)
        self.about_button = button("关于", self.show_about)
        self.about_button.setObjectName("workbenchAboutButton")
        self.about_button.setToolTip("开发者、主页、邮箱、软件定位及上游致谢；打开不联网")
        header.addWidget(self.about_button)
        layout.addLayout(header)
        self.tabs = W.QTabWidget()
        layout.addWidget(self.tabs,1)
        self.design = Design(lambda:self.start_run(),lambda:self.start_run(mesh_only=True))
        self.results = Results()
        self.results.comparison_requested.connect(self.show_region_comparison)
        self.design.load_requested.connect(self.load_template)
        self.design.save_requested.connect(self.save_template)
        self.design.changed.connect(self.update_shot_state)
        self.wavefields = WavefieldView()
        self.results.shot_changed.connect(self.wavefields.load)
        self.results.shot_changed.connect(self.design.mesh_from_gather)
        self.tabs.addTab(self.design,"  01   模型与观测  ")
        self.tabs.addTab(self.wavefields,"  02   波场快照  ")
        self.tabs.addTab(self.results,"  03   单炮记录  ")
        run,rl = panel()
        rl.addWidget(heading("计算记录"))
        self.log = W.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("运行时在此显示算例位置、计算阶段和错误信息。")
        rl.addWidget(self.log,1)
        rl.addWidget(button("播放历史声学炮的波场录像",self.video))
        self.run_page = run
        self.tabs.addTab(run,"  04   运行记录  ")
        self.result_dir = TemporaryDirectory(prefix="bgp_shots_")
        self.jobs = 0
        self.proc = C.QProcess(self)
        self.proc.setProcessChannelMode(C.QProcess.ProcessChannelMode.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self.read_logs)
        self.proc.finished.connect(self.finished)
        self.proc.errorOccurred.connect(self.process_error)
        self.results.set_gather(None)
        self.init_scene_workflow(layout)
        from ui_help import expose_parameter_tips
        expose_parameter_tips(self)

    def show_about(self):
        from app_info import AboutDialog
        if not hasattr(self, "about_dialog"):
            self.about_dialog = AboutDialog(self)
        self.about_dialog.show()
        self.about_dialog.raise_()
        self.about_dialog.activateWindow()

    def update_shot_state(self,config):
        g=self.results.gather
        if getattr(self,'active_region_id',None):
            from region_result import case_hash
            try:matches=g is not None and g.meta.get('region_case_sha256')==case_hash(self.current_region_case())
            except (ValueError,KeyError,TypeError):matches=False
            self.shot_state.setText('当前空洞场景与已载入结果一致' if matches else '当前空洞场景 · '+('输入与历史结果不同' if g is not None else '待运行/载入结果'))
            return
        if g is None:
            self.shot_state.setText("当前设计 · 待运行")
        elif same_design(config,g.meta.get("model")):
            self.shot_state.setText("当前模型与已载入的单炮一致")
        else:
            self.shot_state.setText("当前设计与已载入结果不同 · 运行后更新")

    def video(self):
        path = ROOT/"preview/wavefield-playback.mp4"
        if not path.is_file() or not G.QDesktopServices.openUrl(C.QUrl.fromLocalFile(str(path))):
            W.QMessageBox.warning(self,"录像","未能打开历史波场录像。")

    def start_run(self,mesh_only=False):
        if getattr(self,'active_region_id',None):
            self.start_active_region_run(mesh_only);return
        if not self.design.config or self.proc.state()!=C.QProcess.ProcessState.NotRunning or self.fetch_proc is not None:
            return
        self.jobs += 1
        try:self.register_run(mesh_only)
        except (OSError,ValueError,KeyError) as exc:
            W.QMessageBox.warning(self,'未启动计算',str(exc));return
        folder = Path(self.result_dir.name)/f"shot-{self.jobs}"
        folder.mkdir()
        draft = folder/"model.json"
        draft.write_text(json.dumps(self.design.config))
        self.mesh_only = mesh_only
        self.pending = folder/("mesh.npz" if mesh_only else "gather.npz")
        self.design.setEnabled(False)
        self.tabs.setCurrentWidget(self.run_page)
        kind = "仅划网格" if mesh_only else "单炮正演"
        self.log.appendPlainText(f"\n—— 任务 {self.jobs} · {kind} ——")
        self.statusBar().showMessage("正在运行，请查看计算记录…")
        args = ["-u",str(ROOT/"run_forward.py"),str(draft),str(self.pending)]
        if mesh_only:
            args.append("--mesh-only")
        self.proc.start(sys.executable,args)

    def read_logs(self):
        data = bytes(self.proc.readAllStandardOutput()).decode(errors="replace")
        if data:
            self.log.moveCursor(G.QTextCursor.MoveOperation.End)
            self.log.insertPlainText(data)
            self.capture_run_location()

    def process_error(self,err):
        self.log.appendPlainText(self.proc.errorString())
        if err==C.QProcess.ProcessError.FailedToStart:
            self.design.setEnabled(True)
            if getattr(self,'_region_job',None):self.finish_active_region_run(-1);return
            self.finish_run_record(-1)
            self.statusBar().showMessage("启动失败。")

    def finished(self,code,status):
        self.read_logs()
        if getattr(self,'_region_job',None):self.finish_active_region_run(code);return
        settings=self.results.scene_settings()
        self.finish_run_record(code)
        self.design.setEnabled(True)
        if self.mesh_only and code==0:
            try:
                with np.load(self.pending,allow_pickle=False) as result:
                    mesh = ElementMesh.read(result)
                    if mesh is None:
                        raise ValueError("结果缺少实际网格。")
                    self.design.set_actual_mesh(mesh,json.loads(str(result["model"])),str(result["run"]))
                self.design.mesh_toggle.setChecked(True)
                self.tabs.setCurrentWidget(self.design)
                self.statusBar().showMessage("划分完成，当前剖面已叠加实际谱元边界。")
            except (KeyError,ValueError,OSError) as exc:
                self.log.appendPlainText(str(exc))
                self.statusBar().showMessage("网格读取失败，详见运行记录。")
            return
        if code==0 and self.pending.is_file() and self.results.open(self.pending):
            self.results.apply_scene_settings(settings)
            self.update_shot_state(self.design.config)
            self.tabs.setCurrentWidget(self.wavefields)
            self.statusBar().showMessage("正演完成，当前显示本炮波场。单炮与频散页显示同一炮的接收记录。")
        else:
            self.statusBar().showMessage("计算未完成，详见运行记录。")

    def closeEvent(self,event):
        if self.proc.state()!=C.QProcess.ProcessState.NotRunning or self.fetch_proc is not None:
            self.statusBar().showMessage("计算仍在进行。请等本炮结束后关闭。")
            event.ignore()
        else:
            self.results.stop_theory()
            self.keep_recovery()
            self.result_dir.cleanup()
            super().closeEvent(event)


def create_app():
    # Select plugins belonging to the loaded Qt runtime. A mixed Conda/wheel
    # environment may also contain an incompatible PySide6/Qt/plugins tree.
    plugins = Path(C.QLibraryInfo.path(C.QLibraryInfo.LibraryPath.PluginsPath))
    if not (plugins/"platforms").is_dir():
        plugins = Path(PySide6.__file__).parent/"Qt/plugins"
    if not W.QApplication.instance() and (plugins/"platforms").is_dir():
        os.environ["QT_PLUGIN_PATH"] = str(plugins)
        os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = str(plugins/"platforms")
        C.QCoreApplication.setLibraryPaths([str(plugins)])
    app = W.QApplication.instance() or W.QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet("""
    QWidget {background:#f5f7f4;color:#203c32;font-size:13px}
    QLabel#title {font-size:22px;font-weight:bold;padding:0 4px 5px}
    QLabel[heading="true"] {font-size:17px;font-weight:bold}
    QLabel[note="true"] {color:#405d51;padding:3px 0}
    QPushButton,QComboBox,QSpinBox,QDoubleSpinBox {background:white;border:1px solid #acbeb4;border-radius:5px;padding:5px 7px}
    QPushButton:hover {background:#e0ede5}
    QPushButton[primary="true"] {background:#176553;color:white;font-weight:bold;padding:7px 15px}
    QPushButton:disabled {color:#8e9994;background:#e1e7e2}
    QTableWidget,QPlainTextEdit {background:white;alternate-background-color:#f0f5ef;gridline-color:#dbe4dc}
    QHeaderView::section {padding:4px;background:#e5eee7;color:#254737;border:0}
    QTabBar::tab {background:#e3ebe5;padding:7px 12px;border-bottom:2px solid transparent}
    QTabBar::tab:selected {background:#fbfcfa;color:#115844;border-bottom:2px solid #22795c}
    QTabWidget::pane {border:1px solid #ccd8cf}
    """)
    return app


if __name__=="__main__":
    app = create_app()
    window = Window()
    window.show()
    sys.exit(app.exec())
