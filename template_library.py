"""One template picker for editable designs and previously computed shots."""
import json
from dataclasses import dataclass, field
from pathlib import Path

from PySide6 import QtCore as C, QtWidgets as W

from computed_examples import checked_input, open_entry
from local_template_results import local_result
from model import ROOT
from region_result import case_hash
from region_templates import TUNNELS, REFERENCE_CASES
from runtime_config import read_runtime
from template_catalog import ACTIVE_TEMPLATES, available_recommended, matching_results


# Each path names a frozen acquisition. Labels identify the intended comparison.
FEATURED = {
    **{f'layered_terrain/{key}_{fill}.json': f'三层{terrain} · {label}'
       for key,terrain in [('flat','平地'),('hill','山丘 +18 m'),('valley','谷地 −18 m')]
       for fill,label in [('solid','无洞'),('water','36 × 18 m 水洞')]},
    'computed600/iso_flat.json': '弹性平地 · 两层 · 600 m',
    'computed600/iso_hill.json': '弹性山丘 · 两层 · 600 m',
    'computed600/q_flat.json': 'Q 衰减平地 · 两层 · 600 m',
    'computed600/q_hill.json': 'Q 衰减山丘 · 两层 · 600 m',
    'computed600/vti_hill.json': 'VTI 山丘 · 两层 · 600 m',
    'computed600/hti_flat.json': 'HTI 平地 · 两层 · 600 m',
    'computed600/hti_hill.json': 'HTI 山丘 · 两层 · 600 m',
    'computed600/vti_flat_checked.json': 'VTI 平地 · 两层 · 600 m',
    'cavity/tunnels/circle_water.json': '均匀围岩 · 16 m 圆隧道 · 满水',
    'cavity/tunnels/circle_dry.json': '均匀围岩 · 16 m 圆隧道 · 无牵引',
    'cavity/tunnels/circle_air.json': '均匀围岩 · 16 m 圆隧道 · 空气填充',
}


@dataclass
class Template:
    label: str
    path: Path
    groups: list = field(default_factory=list)
    status: str = '仅输入 · 未关联结果'
    detail: str = ''
    viewable: bool = False
    frozen: dict | None = None


def catalog():
    examples = ROOT / 'examples'
    runtime = read_runtime(ROOT)
    locations = runtime.get('template_result_locations', {})
    regions = runtime.get('computed_result_locations', {})
    entries = {}
    region_inputs = {}

    def add(label, path, group):
        row = entries.setdefault(path, Template(label, path))
        if group not in row.groups:
            row.groups.append(group)
        return row

    def registered(row, available, checksums):
        if any(local_result(h) for h in checksums):
            row.viewable=True
            row.status="已算 · 本地结果"
            return
        row.viewable = bool(available)
        row.status = '已算 · 服务器结果' if available else '已算 · 需配置结果位置'

    for entry in json.loads((examples/'computed600/index.json').read_text())['entries']:
        path = examples/'computed600'/entry['template']
        row = add(entry['label'], path, '600 m 等权')
        row.frozen = entry
        row.detail = entry['scope'] + '\n理论参考：' + entry['theory']
        try:
            _, value = checked_input(entry)
        except (ValueError, OSError, KeyError) as exc:
            row.status = '输入校验失败'
            row.detail = str(exc)
            continue
        from theory_reference import actual_backend
        if value['model'].get('attenuation',{}).get('enabled') or value['model'].get('material_model')=='hti':
            background = '无洞背景' if entry['kind']!='layered' else '台阵中心一维柱'
            row.detail = entry['scope'] + '\n理论参考（'+background+'）：' + actual_backend(value['model'])[1]
        locator = entry['locator']
        if entry['kind'] == 'layered':
            registered(row, locations.get(locator, {}).get('remote_run'), [entry['result_sha256']])
        else:
            region_inputs[case_hash(value)] = row
            record = regions.get(locator, {}).get('region_run', {})
            registered(row, record.get('case') == value and record.get('preview_sha256') == entry['result_sha256'], [entry['result_sha256']])

    tunnels = json.loads((examples/'cavity/tunnels/results.json').read_text())['entries']
    for group, items in [('浅埋隧道', TUNNELS), ('空洞研究', REFERENCE_CASES)]:
        for key, label in items:
            path = examples/'cavity'/key
            case = json.loads(path.read_text())
            fingerprint = case_hash(case)
            if fingerprint in region_inputs:
                row = region_inputs[fingerprint]
                add(row.label, row.path, group)
                continue
            row = add(label, path, group)
            if fingerprint in tunnels:
                entry = tunnels[fingerprint]
                record = regions.get(entry['locator'], {}).get('region_run', {})
                registered(row, record.get('case') == case and record.get('preview_sha256') == entry['result_sha256'], [entry['result_sha256']])

    for group, items in [('推荐演示', available_recommended()), ('历史与研究', ACTIVE_TEMPLATES)]:
        for label, key in items:
            path = examples/key
            exists = path in entries
            row = add(label, path, group)
            if exists:
                continue
            if path.suffix == '.npz':
                row.status = '已算 · 本地结果'
                row.viewable = True
                continue
            value = json.loads(path.read_text())
            refs = matching_results(path, value.get('model', value))
            if refs:
                registered(row, any(locations.get(r['locator'], {}).get('remote_run') for r in refs), [r['result_sha256'] for r in refs])
                row.detail = '\n'.join(r['validation_label'] for r in refs if r.get('validation_label'))
    terrain_dir=examples/'layered_terrain'
    for item in json.loads((terrain_dir/'index.json').read_text())['entries']:
        path=terrain_dir/(item['key']+'.json')
        row=add(item['label'],path,'三层地形与水洞')
        if case_hash(json.loads(path.read_text()))!=item['case_sha256']:
            row.status='输入校验失败'
        elif path.with_suffix('.npz').is_file():
            row.status='已算 · 本地结果';row.viewable=True
        row.detail='水平层界 −33、−63 m；93 道 × 3 m。水洞位于起伏段右侧，洞顶埋深 9 m。各地形有配对无洞记录。'
    return list(entries.values())


class TemplateLibrary(W.QDialog):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.setWindowTitle('模板库')
        self.resize(960, 650)
        layout = W.QVBoxLayout(self)
        self.rows = catalog()
        self.scope = W.QComboBox()
        self.scope.addItems(['精选对比', '本地已有结果', '全部模板', '三层地形与水洞', '推荐演示', '600 m 等权', '浅埋隧道', '空洞研究', '历史与研究'])
        layout.addWidget(self.scope)
        self.table = W.QTreeWidget()
        self.table.setRootIsDecorated(False)
        self.table.setHeaderLabels(['模板', '结果状态', '分类'])
        self.table.header().setSectionResizeMode(0, W.QHeaderView.ResizeMode.Stretch)
        for column in (1, 2):
            self.table.header().setSectionResizeMode(column, W.QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table)
        self.detail = W.QLabel()
        self.detail.setWordWrap(True)
        self.detail.setMinimumHeight(60)
        layout.addWidget(self.detail)
        buttons = W.QHBoxLayout()
        layout.addLayout(buttons)
        params = W.QPushButton('按材料与地形预览参数…')
        params.clicked.connect(self.preview_parameters)
        buttons.addWidget(params)
        buttons.addStretch()
        self.edit = W.QPushButton('载入并编辑输入')
        self.view = W.QPushButton('查看已算结果')
        self.view.setToolTip('载入当前行的单炮和波场；服务器结果按需取回。')
        for button, view in [(self.edit, False), (self.view, True)]:
            buttons.addWidget(button)
            button.clicked.connect(lambda _, view=view: self.open_selected(view))
        self.scope.currentTextChanged.connect(self.populate)
        self.table.currentItemChanged.connect(self.update_actions)
        self.table.itemDoubleClicked.connect(lambda *_: self.open_selected(self.view.isEnabled()))
        self.populate(self.scope.currentText())

    def preview_parameters(self):
        self.accept()
        self.owner.design.new_scene()

    def populate(self, group):
        self.table.clear()
        rows = list(enumerate(self.rows))
        if group == '精选对比':
            by_path = {r.path.relative_to(ROOT/'examples').as_posix(): (i,r) for i,r in rows}
            rows = [by_path[key] for key in FEATURED if key in by_path]
        if group == '本地已有结果':
            rows = [(i,r) for i,r in rows if r.status == '已算 · 本地结果']
        for index, row in rows:
            if group not in ('全部模板', '精选对比', '本地已有结果') and group not in row.groups:
                continue
            label = FEATURED[row.path.relative_to(ROOT/'examples').as_posix()] if group=='精选对比' else row.label
            item = W.QTreeWidgetItem([label, row.status, ' / '.join(row.groups)])
            item.setData(0, C.Qt.ItemDataRole.UserRole, index)
            self.table.addTopLevelItem(item)
        self.table.setCurrentItem(self.table.topLevelItem(0))
        self.update_actions()

    def selected(self):
        item = self.table.currentItem()
        if item is not None:
            return self.rows[item.data(0, C.Qt.ItemDataRole.UserRole)]

    def update_actions(self, *_):
        row = self.selected()
        self.edit.setEnabled(row is not None and row.status != '输入校验失败')
        self.view.setEnabled(row is not None and row.viewable)
        self.detail.setText((row.detail or row.status) if row else '')

    def open_selected(self, view):
        row = self.selected()
        if not row or (view and not row.viewable):
            return
        try:
            if row.frozen:
                open_entry(self.owner, row.frozen, view)
            else:
                if not self.owner.load_template(row.path):
                    return
                if view:
                    if row.path.suffix == '.npz':
                        self.owner.tabs.setCurrentWidget(self.owner.results)
                    elif len(self.owner.run_refs) == 1:
                        self.owner.open_run_result(self.owner.run_refs[0])
                    elif self.owner.run_refs:
                        self.owner.show_scene_runs()
                    else:
                        raise ValueError('当前输入没有匹配的结果引用。')
            self.accept()
        except (OSError, ValueError, KeyError, TypeError) as exc:
            W.QMessageBox.warning(self, '打开模板', str(exc))
