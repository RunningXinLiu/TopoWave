"""Native scenario starter. Selection does not mutate the current design."""
from PySide6 import QtCore as C, QtWidgets as W
from scene_catalog import load_scene


class SceneDialog(W.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('新建场景 · 选择模板后自定义')
        self.resize(920, 740)
        self.selected_path = None
        self.selected_region_case = None
        self.scene = None
        layout = W.QVBoxLayout(self)
        intro = W.QLabel('按层输入材料，程序生成各网格点的本构参数。载入后可编辑地表、层界、材料、炮检排列和计算参数。')
        intro.setWordWrap(True)
        layout.addWidget(intro)
        regions = W.QPushButton('浅埋隧道与空洞模板…（圆形、长方形、无牵引、空气、满水）')
        regions.clicked.connect(self.choose_region)
        layout.addWidget(regions)
        form = W.QFormLayout()
        layout.addLayout(form)
        self.recommended = W.QCheckBox('仅使用推荐演示（其他组合可切换为研究模板）')
        self.recommended.setChecked(True)
        form.addRow('模板范围', self.recommended)
        self.recommended.toggled.connect(self.refresh)
        self.background = W.QComboBox()
        for label, key in [('各向同性', 'isotropic'), ('VTI · 全局竖直轴（试验）', 'vti'), ('HTI · 剖面内水平轴（试验）', 'hti')]:
            self.background.addItem(label, key)
        self.attenuation = W.QCheckBox('启用材料衰减 Qκ / Qμ')
        self.attenuation.setToolTip('当前支持各向同性衰减。Qκ为体积模量品质因子，不是Qp；载入后在层参数窗口逐层修改。')
        self.terrain = W.QComboBox()
        self.terrain.addItem('平地起步 · 可编辑地表与层界', 'flat')
        self.terrain.addItem('山丘起步 · 固定地下层界，可自定义', 'hill')
        self.terrain.setToolTip('这是初始几何模板。载入后通过地表 / 层界编辑器修改控制点；自定义模型需重新生成网格并检查。')
        self.inclusion = W.QComboBox()
        self.inclusion.addItem('无内含体 · 分层背景', 'none')
        self.inclusion.addItem('空洞 / 流体（请用上方专用模板入口）', 'cavity')
        form.addRow('背景材料', self.background)
        form.addRow('材料损耗', self.attenuation)
        form.addRow('地表与层界', self.terrain)
        form.addRow('内部几何', self.inclusion)
        self.summary = W.QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.layers = W.QTableWidget(0, 5)
        self.layers.setHorizontalHeaderLabels(['厚度 (m)', 'Vp / Vp0 (m/s)', 'Vs / Vs0 (m/s)', 'ρ (kg/m³)', '附加材料参数'])
        self.layers.horizontalHeader().setSectionResizeMode(W.QHeaderView.ResizeMode.Stretch)
        self.layers.setEditTriggers(W.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.layers.setMaximumHeight(135)
        layout.addWidget(self.layers)
        self.details = W.QPlainTextEdit()
        self.details.setReadOnly(True)
        layout.addWidget(self.details, 1)
        self.evidence = W.QLabel()
        self.evidence.setWordWrap(True)
        self.evidence.setTextFormat(C.Qt.TextFormat.PlainText)
        layout.addWidget(self.evidence)
        footer = W.QLabel('载入会替换当前设计，并清除当前显示的旧炮结果；磁盘文件保留。\n随后：编辑参数 → 生成实际网格并查看 QC → 正演 → 查看完整单炮与波场 → 可选面波窗 PS/FH。')
        footer.setWordWrap(True)
        layout.addWidget(footer)
        buttons = W.QDialogButtonBox(W.QDialogButtonBox.StandardButton.Cancel)
        buttons.button(W.QDialogButtonBox.StandardButton.Cancel).setText('取消')
        self.load_button = buttons.addButton('载入参数模板', W.QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.choose)
        layout.addWidget(buttons)
        for combo in (self.background, self.terrain, self.inclusion):
            combo.currentIndexChanged.connect(self.refresh)
        self.attenuation.toggled.connect(self.refresh)
        self.refresh()

    def choose_region(self):
        from region_templates import template_dialog
        dialog = template_dialog(self)
        if dialog.exec() == W.QDialog.DialogCode.Accepted:
            self.selected_region_case = dialog.selected_case
            self.selected_path = None
            self.accept()

    def refresh(self):
        self.scene = None
        self.selected_path = None
        self.load_button.setEnabled(False)
        self.layers.setRowCount(0)
        self.details.clear()
        self.evidence.clear()
        try:
            scene = load_scene(self.background.currentData(), self.attenuation.isChecked(), self.terrain.currentData(), self.inclusion.currentData(), recommended=self.recommended.isChecked())
        except (ValueError, OSError, KeyError, TypeError) as exc:
            self.summary.setText(str(exc))
            self.summary.setStyleSheet('color:#a52d36')
            return
        self.scene = scene
        c = scene['model']
        self.summary.setStyleSheet('')
        aperture = (c['receivers']-1)*c['spacing_m']
        self.summary.setText(f"{'推荐演示' if self.recommended.isChecked() else '历史与研究模板'} · 当前孔径 {aperture:g} m，道距 {c['spacing_m']:g} m，{c['receivers']} 道。验证范围见下方；孔径不保证所有高阶可分辨。")
        self.layers.setRowCount(len(c['layers']))
        for i, row in enumerate(c['layers']):
            props = c.get('material_properties', [{} for _ in c['layers']])[i]
            labels = dict(qkappa='Qκ', qmu='Qμ', epsilon='ε', delta='δ', gamma='γ')
            extra = ', '.join(f'{labels.get(k, k)}={v:g}' for k, v in props.items()) or '无'
            for j, value in enumerate([*(f'{v:g}' for v in row), extra]):
                self.layers.setItem(i, j, W.QTableWidgetItem(value))
        self.layers.resizeRowsToContents()
        boundary = 'Stacey；大域证据仅适用于匹配输入和记录时长，不代表已解决掠入射吸收' if c['boundary'] == 'stacey' else f"CPML {c['pml_elements']} 层；α 系数 {c['cpml_alpha_factor']:g}（候选后端）"
        from theory_reference import actual_backend
        theory = actual_backend(c)[1] + ' 可另选弹性固体对照；参考位置固定为完整台阵中心。'
        lines = [
            f"范围：宽 {c['width_m']:g} m，计算深度 {sum(r[0] for r in c['layers']):g} m；最后一层在有限计算域底部接吸收边界。",
            f"源：地表竖向 Ricker 力，x={c['shot_x_m']:g} m，主频 {c['f0_hz']:g} Hz，预移 {c['source_t0_s']:g} s。",
            f"接收：{c['receivers']} 道，起点 {c['first_x_m']:g} m，间隔 {c['spacing_m']:g} m，孔径 {(c['receivers']-1)*c['spacing_m']:g} m。",
            f"网格：水平目标谱元 {c['element_m']:g} m，逐层单元数 {c['elements_per_layer']}；此处为参数预览，实际网格需生成后 QC。",
            f"时间：dt={c['dt_s']:g} s，{c['nstep']} 步，首末步跨度 {(c['nstep']-1)*c['dt_s']:g} s；记录每 {c['sample_stride']} 步、波场每 {c['snapshot_stride']} 步保存。",
            f'边界：{boundary}。', f'派生产物：{theory}。',
            f"模板：{scene['path'].name}",
        ]
        ev = scene['evidence']
        if c.get('attenuation', {}).get('enabled'):
            a = c['attenuation']
            reference = '源主频处' if a['velocity_reference'] == 'source_f0' else '无限频率（未松弛）'
            lines.insert(1, f"衰减：{a['n_sls']} 个 SLS；输入速度参考为{reference}。")
        if ev:
            lines += ['', '匹配模板的检查：'] + [f"• {x['name']}：{x['summary']}" for x in ev.get('checks', [])]
        self.details.setPlainText('\n'.join(lines))
        self.evidence.setText((ev['label'] if ev else '模板已改变或缺少匹配证据：不继承原验证状态。') + '\n自定义修改后按新配置重新检查；其他材料或地形不继承本模板结论。')
        self.evidence.setStyleSheet('color:#a52d36' if ev and ev.get('scientific_status') == 'failed_full_record_reference' else 'color:#80501d')
        self.load_button.setEnabled(True)

    def choose(self):
        # Re-read at acceptance, so missing/invalid files cannot accept stale selection.
        self.refresh()
        if self.scene is not None:
            self.selected_path = self.scene['path']
            self.accept()
