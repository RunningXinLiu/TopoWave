"""Curated editable inputs; validation reports remain optional read-only evidence."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TUNNELS = (
    ('tunnels/circle_air.json', '圆形 · 空气 · 直径 16 m · 孔径 160 m'),
    ('tunnels/circle_air320.json', '圆形 · 空气 · 直径 16 m · 孔径 320 m'),
    ('tunnels/circle_dry.json', '圆形 · 无牵引干洞 · 直径 16 m'),
    ('tunnels/circle_water.json', '圆形 · 满水 · 直径 16 m'),
    ('tunnels/rect_air.json', '长方形 · 空气 · 20 × 10 m'),
    ('tunnels/rect_dry.json', '长方形 · 无牵引干洞 · 20 × 10 m'),
    ('tunnels/rect_water.json', '长方形 · 满水 · 20 × 10 m'),
    ('tunnels/circle_air8.json', '尺寸对照 · 空气圆洞 · 直径 8 m'),
)

REFERENCE_CASES = (
    ('elastic_dry_boundary480.json', '弹性＋无牵引矩形洞（数值验证基准）'),
    ('air_boundary480.json', '弹性＋空气填充矩形洞'),
    ('air_depth12_candidate.json', '空气矩形洞 · 洞顶12 m · 数值控制已核查'),
    ('air_demo300.json', '空气矩形洞 · 300 m · 完整记录对照通过'),
    ('large/air96.json', '大空气洞 · 宽96 m · 600 m阵列 · 待验证'),
    ('large/air240.json', '大空气洞 · 宽240 m · 600 m阵列 · 待验证'),
    ('large/air_layer720.json', '有限空气空层 · 宽720 m覆盖全阵列 · 待验证'),
    ('large/dry_layer720.json', '有限无牵引空层 · 宽720 m覆盖全阵列 · 待验证'),
    ('water_boundary480.json', '弹性＋水填充矩形洞'),
    ('q_dry_trial.json', 'Q固体＋无牵引矩形洞'),
    ('q_air_boundary480.json', 'Q固体＋空气填充矩形洞'),
    ('q_water_boundary480.json', 'Q固体＋水填充矩形洞'),
    ('geometry/circle_dry.json', '弹性＋圆形无牵引洞'),
    ('geometry/circle_water.json', '弹性＋圆形水区'),
    ('geometry/multi_dry.json', '弹性＋两个无牵引洞'),
    ('geometry/multi_fluid.json', '弹性＋空气区和水区'),
    ('geometry/hill_dry.json', '起伏地表＋无牵引矩形洞'),
    ('geometry/hill_water.json', '起伏地表＋水填充矩形洞'),
)

TEMPLATES = TUNNELS + REFERENCE_CASES


def tunnel_result(case):
    from region_result import case_hash
    path = ROOT / 'examples/cavity/tunnels/results.json'
    if not path.is_file():
        return None
    index = json.loads(path.read_text())
    entry = index['entries'].get(case_hash(case))
    if entry is not None:
        return dict(entry, options=index['analysis_options'])
    return None


def load_template(key):
    if key not in dict(TEMPLATES):
        raise ValueError('未知区域模板。')
    from region_case import validate
    case = json.loads((ROOT / 'examples/cavity' / key).read_text())
    validate(case)
    return case


def make_editor(case, parent=None, scene_owner=None):
    from region_editor import RegionEditor
    if case.get('schema') == 'region_geometry_v1':
        from region_geometry_editor import GeometryEditor
        editor = GeometryEditor
    else:
        editor = RegionEditor
    return editor(parent, scene_owner=scene_owner, entry=dict(id=None, case=case))


def template_dialog(parent=None):
    from PySide6 import QtWidgets as W
    from region_evidence import evidence_for

    class TemplateDialog(W.QDialog):
        def __init__(self):
            super().__init__(parent)
            self.setWindowTitle('空洞与流体 · 可编辑单炮模板')
            self.resize(980, 690)
            self.selected_case = None
            layout = W.QVBoxLayout(self)
            intro = W.QLabel('载入后成为主工作台的当前场景：编辑背景/洞体 → 网格QC → 正演 → 波场、完整单炮与频散。\n有洞／无洞对比为可选入口。模板载入输入；已有结果从运行历史打开，修改参数不继承模板验证。')
            intro.setWordWrap(True)
            layout.addWidget(intro)
            split = W.QSplitter(); layout.addWidget(split, 1)
            left = W.QWidget()
            controls = W.QVBoxLayout(left)
            self.group = W.QComboBox()
            self.group.addItems(['浅埋隧道 · 洞顶 10 m', '研究对照 · 小洞、多洞、地形及其他'])
            controls.addWidget(self.group)
            self.listing = W.QListWidget()
            controls.addWidget(self.listing)
            split.addWidget(left)
            self.details = W.QPlainTextEdit(); self.details.setReadOnly(True); split.addWidget(self.details)
            split.setSizes([340, 620])
            buttons = W.QDialogButtonBox(W.QDialogButtonBox.StandardButton.Cancel)
            buttons.button(W.QDialogButtonBox.StandardButton.Cancel).setText('取消')
            self.open_button = buttons.addButton('载入主工作台', W.QDialogButtonBox.ButtonRole.AcceptRole)
            buttons.accepted.connect(self.choose); buttons.rejected.connect(self.reject)
            layout.addWidget(buttons)
            self.listing.currentRowChanged.connect(self.refresh)
            self.group.currentIndexChanged.connect(self.show_group)
            self.show_group(0)

        def show_group(self, index):
            self.visible = (TUNNELS, REFERENCE_CASES)[index]
            self.listing.clear()
            self.listing.addItems([label for _, label in self.visible])
            self.listing.setCurrentRow(0)

        def refresh(self):
            self.selected_case = None
            self.open_button.setEnabled(False)
            if self.listing.currentRow() < 0:
                return
            try:
                key, label = self.visible[self.listing.currentRow()]
                case = load_template(key); m = case['model']
                lines = [label, '', f"模型：{m['width_m']:g} × {m['layers'][0][0]:g} m；单层ISO。",
                         f"Vp / Vs / ρ：{m['layers'][0][1:]}",
                         f"源主频：{m['f0_hz']:g} Hz。接收排列 {m['receivers']} 道，道距 {m['spacing_m']:g} m，水平孔径 {(m['receivers']-1)*m['spacing_m']:g} m。",
                         f"网格：{m['element_m']:g} m；dt：{m['dt_s']:g} s；{m['nstep']} 步。",
                         '边界：Stacey；主产品为实际网格、波场及完整单炮记录。', '']
                if key.startswith('tunnels/'):
                    region = case['regions'][0]
                    fill = region['interior']
                    if fill['kind'] == 'traction_free':
                        wall = '洞内移除单元，洞壁法向和切向牵引均为零。可查看围岩速度和地表双分量。'
                    else:
                        wall = f"洞内声速 {fill['sound_speed_m_s']:g} m/s、密度 {fill['rho_kg_m3']:g} kg/m³。洞壁传递法向压力，切向牵引为零。可查看围岩速度和洞内声压。"
                    lines[1:1] = ['洞顶埋深 10 m，均匀围岩，无衬砌。', wall,
                                   '比较同一断面的填充效应，或比较面积相近的 16 m 圆洞和 20 × 10 m 长方形。8 m 空气圆洞用于直径对照。']
                try:
                    ev = evidence_for(case)
                    lines += [ev['note'], '']
                    lines += [f"{r['label']} — {r['status']}\n{r['detail']}\n" for r in ev['rows']]
                except (ValueError, KeyError, TypeError, OSError) as exc:
                    lines += ['验证证据暂不可读取：' + str(exc)]
                lines += ['模板仅作为新方案起点；TI空洞及Q与圆洞/多洞/地形组合不支持。']
                self.preview_case = case
                self.details.setPlainText('\n'.join(lines))
                self.open_button.setEnabled(True)
            except (ValueError, KeyError, TypeError, OSError) as exc:
                self.details.setPlainText('模板不能载入：' + str(exc))

        def choose(self):
            if self.open_button.isEnabled():
                self.selected_case = self.preview_case
                self.accept()

    return TemplateDialog()
