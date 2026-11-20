"""Complete native layer form with explicit CSV input, validation and apply."""
import copy
from pathlib import Path
from PySide6 import QtCore as C, QtGui as G, QtWidgets as W
from materials import kind, attenuating, ti_stiffness
from material_input import KEYS, LABELS, TIPS, InputError, table_rows, build_model, read_csv, write_csv
from material_inspection_view import MaterialInspection
from model import validate

class MaterialEditor(W.QDialog):
    def __init__(self,config,parent=None,allow_draft=False):
        super().__init__(parent)
        self.allow_draft=allow_draft
        self.config=copy.deepcopy(config);self.result_model=None;self.loading=True
        self.setWindowTitle('层参数 · 速度 / 密度 / 衰减 / 各向异性');self.resize(1120,650)
        layout=W.QVBoxLayout(self);form=W.QFormLayout();layout.addLayout(form)
        self.mode=W.QComboBox()
        for label,value in [('各向同性','isotropic'),('VTI · 全局竖直 z 轴','vti'),('HTI · 剖面内全局水平 x 轴','hti')]:self.mode.addItem(label,value)
        self.mode.setCurrentIndex(self.mode.findData(kind(config)));form.addRow('材料类型',self.mode)
        self.mode.setToolTip('所有层使用此类型；TI轴不随坡面旋转。HTI仅支持剖面内水平轴的P-SV，不是任意方位三维传播。')
        self.att=W.QCheckBox('启用各向同性黏弹性衰减');self.att.setChecked(attenuating(config));form.addRow(self.att)
        self.att.setToolTip('启用真实材料Q，独立于CPML吸收和频域数值权重。当前TI+衰减未实现。')
        self.ref=W.QComboBox();self.ref.addItem('源主频 f0 处速度','source_f0');self.ref.addItem('无限频率未松弛速度','unrelaxed')
        self.ref.setCurrentIndex(self.ref.findData(config.get('attenuation',{}).get('velocity_reference','source_f0')));form.addRow('Vp/Vs参考',self.ref)
        self.ref.setToolTip('Ricker源下SLS参考频率由原生求解器固定为源f0。source_f0会换算未松弛模量；unrelaxed直接按无限频率输入解释。')
        self.sls=W.QSpinBox();self.sls.setRange(3,8);self.sls.setValue(config.get('attenuation',{}).get('n_sls',3));form.addRow('SLS 个数',self.sls)
        self.sls.setToolTip('用于近似材料损耗的标准线性固体数。更多SLS增加开销，不替代网格或时间步收敛验证。')
        self.boundary=W.QComboBox();self.boundary.addItem('Stacey','stacey');self.boundary.addItem('C-PML','cpml')
        self.boundary.setCurrentIndex(self.boundary.findData(config.get('boundary','stacey')));form.addRow('侧面/底部边界',self.boundary)
        self.boundary.setToolTip('与计算设置同步，仅点击应用后修改。TI+CPML被拒绝，不自动换边界。地表保持自由。')
        self.hint=W.QLabel();self.hint.setWordWrap(True);layout.addWidget(self.hint)
        self.table=W.QTableWidget(0,len(KEYS));self.table.setHorizontalHeaderLabels(LABELS)
        self.table.setSelectionBehavior(W.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(W.QHeaderView.ResizeMode.Stretch)
        self.table.setAlternatingRowColors(True)
        for j,tip in enumerate(TIPS):self.table.horizontalHeaderItem(j).setToolTip(tip)
        layout.addWidget(self.table,1)
        buttons=W.QHBoxLayout();layout.addLayout(buttons)
        def button(text,callback):
            b=W.QPushButton(text);b.clicked.connect(callback);buttons.addWidget(b);return b
        self.add_button=button('添加空白层',self.add_layer)
        self.copy_button=button('复制选中层',self.duplicate_layer)
        self.remove_button=button('删除选中层',self.remove_layer)
        button('导入层参数 CSV…',self.import_csv);button('导出层参数 CSV…',self.export_csv)
        self.geometry_note=W.QLabel('独立起伏界面下层数固定；请先在几何编辑器中设计界面。' if config.get('interface_relief') else '层厚改变会改变计算域深度。新增层请填写全部参数；nz全空为自动，全部填写为逐层指定。')
        self.geometry_note.setWordWrap(True);layout.addWidget(self.geometry_note)
        fixed=bool(config.get('interface_relief'))
        for b in (self.add_button,self.copy_button,self.remove_button):b.setEnabled(not fixed)
        self.error=W.QLabel();self.error.setWordWrap(True);layout.addWidget(self.error)
        self.summary=W.QLabel();self.summary.setWordWrap(True);layout.addWidget(self.summary)
        self.inspect_button=W.QPushButton('查看刚度矩阵与方程 / 边界兼容范围…')
        self.inspect_button.setToolTip('只读查看当前表中的每层完整刚度、实际P-SV分量、单位和支持范围。不会改变输入或切换边界。')
        self.inspect_button.clicked.connect(self.inspect);layout.addWidget(self.inspect_button)
        self.box=W.QDialogButtonBox(W.QDialogButtonBox.StandardButton.Apply|W.QDialogButtonBox.StandardButton.Cancel)
        self.apply_button=self.box.button(W.QDialogButtonBox.StandardButton.Apply);self.apply_button.setText('应用到模型')
        self.box.button(W.QDialogButtonBox.StandardButton.Cancel).setText('取消')
        self.apply_button.clicked.connect(self.commit);self.box.rejected.connect(self.reject);layout.addWidget(self.box)
        self.replace_rows(table_rows(config))
        self.loading=False
        self.table.itemChanged.connect(self.check)
        self.mode.currentIndexChanged.connect(self.update_fields);self.att.toggled.connect(self.update_fields)
        self.ref.currentIndexChanged.connect(self.check);self.sls.valueChanged.connect(self.check);self.boundary.currentIndexChanged.connect(self.check)
        self.update_fields()

    def rows(self):
        return [[self.table.item(i,j).text() if self.table.item(i,j) else '' for j in range(len(KEYS))] for i in range(self.table.rowCount())]

    def replace_rows(self,rows):
        with C.QSignalBlocker(self.table):
            self.table.setRowCount(len(rows))
            for i,row in enumerate(rows):
                for j,value in enumerate(row):
                    item=W.QTableWidgetItem(str(value));item.setToolTip(TIPS[j]);self.table.setItem(i,j,item)
        if not self.loading:self.check()

    def candidate(self,rows=None):
        return build_model(self.config,self.rows() if rows is None else rows,self.mode.currentData(),
            dict(enabled=self.att.isChecked(),n_sls=self.sls.value(),velocity_reference=self.ref.currentData()),self.boundary.currentData(),check_design=not self.allow_draft)

    def update_fields(self):
        iso=self.mode.currentData()=='isotropic'
        self.att.setEnabled(iso and self.config.get('physics','elastic')=='elastic')
        if not iso and self.att.isChecked():
            with C.QSignalBlocker(self.att):self.att.setChecked(False)
        active=iso and self.att.isChecked()
        self.ref.setEnabled(active);self.sls.setEnabled(active)
        for j in (4,5):self.table.setColumnHidden(j,not active)
        for j in (6,7,8):self.table.setColumnHidden(j,iso)
        self.table.horizontalHeaderItem(1).setText('Vp (m/s)' if iso else 'Vp0 (m/s)')
        self.table.horizontalHeaderItem(2).setText('Vs (m/s)' if iso else 'Vs0 (m/s)')
        self.hint.setText(f"衰减参考频率 f0={self.config['f0_hz']:g} Hz（在观测系统修改源主频）。Qκ≠Qp，Qμ=Qs，9999为无损。" if active else
            'Vp0/Vs0沿材料对称轴定义；ε、δ、γ无量纲。γ不影响当前对称面P-SV传播，但参与完整材料正定检查。' if not iso else
            '自上而下输入层厚、速度和密度。悬停列名或单元格查看单位和含义。')
        self.check()

    def check(self,*_):
        if self.loading:return
        with C.QSignalBlocker(self.table):
            for i in range(self.table.rowCount()):
                for j in range(len(KEYS)):self.table.item(i,j).setBackground(G.QBrush())
        try:
            c=self.candidate();self.apply_button.setEnabled(True);self.inspect_button.setEnabled(True);self.error.setText('输入检查通过；尚不代表波形或边界已验收。');self.error.setStyleSheet('color:#146653')
            if self.allow_draft:
                try:validate(c)
                except (ValueError,TypeError,KeyError):
                    self.inspect_button.setEnabled(False)
                    self.error.setText('层表检查通过，可以应用到草稿；主界面其余参数未完成，尚不能运行。')
            if kind(c)!='isotropic':
                terms=[]
                for i,(layer,p) in enumerate(zip(c['layers'],c['material_properties']),1):
                    M=ti_stiffness(layer,p,kind(c));terms.append(f'L{i}: C11/C33/C55={M[0,0]/1e9:.4g}/{M[2,2]/1e9:.4g}/{M[4,4]/1e9:.4g} GPa')
                self.summary.setText('全局坐标刚度已通过正定检查；'+ '；'.join(terms))
            else:self.summary.setText(f"{len(c['layers'])}层，总计算深度（x=0厚度和）{sum(r[0] for r in c['layers']):g} m；应用后保留手动dt和记录时长。")
        except (ValueError,TypeError,KeyError) as exc:
            self.apply_button.setEnabled(False);self.inspect_button.setEnabled(False);self.summary.clear();self.error.setText(str(exc));self.error.setStyleSheet('color:#a52d36')
            if isinstance(exc,InputError) and exc.row is not None:
                with C.QSignalBlocker(self.table):
                    for j in (range(len(KEYS)) if exc.column is None else [exc.column]):self.table.item(exc.row,j).setBackground(G.QColor('#f9d8d9'))

    def inspect(self):
        try:MaterialInspection(self.candidate(),self,self.table.currentRow()).exec()
        except (ValueError,KeyError,TypeError) as exc:self.error.setText(str(exc))

    def add_layer(self):
        if self.config.get('interface_relief'):return
        self.replace_rows(self.rows()+[['']*len(KEYS)]);self.table.setCurrentCell(self.table.rowCount()-1,0)

    def duplicate_layer(self):
        if self.config.get('interface_relief'):return
        rows=self.rows();i=self.table.currentRow()
        if i<0:i=len(rows)-1
        rows.insert(i+1,list(rows[i]));self.replace_rows(rows);self.table.setCurrentCell(i+1,0)

    def remove_layer(self):
        if self.config.get('interface_relief'):return
        rows=self.rows();i=self.table.currentRow()
        if len(rows)>1 and i>=0:rows.pop(i);self.replace_rows(rows)

    def import_csv(self):
        path,_=W.QFileDialog.getOpenFileName(self,'读入层参数 CSV','','CSV (*.csv)')
        if not path:return
        try:
            rows=read_csv(Path(path).read_text(encoding='utf-8-sig'),self.rows(),self.mode.currentData(),self.att.isChecked())
            self.candidate(rows) # Atomic: failed imports do not overwrite current edits.
            self.replace_rows(rows)
        except (OSError,ValueError,KeyError) as exc:self.error.setText('未导入：'+str(exc))

    def export_csv(self):
        try:self.candidate()
        except ValueError as exc:self.error.setText('请先修正输入再导出：'+str(exc));return
        path,_=W.QFileDialog.getSaveFileName(self,'保存层参数 CSV','layers.csv','CSV (*.csv)')
        if path:
            try:Path(path).write_text(write_csv(self.rows()),encoding='utf-8-sig')
            except OSError as exc:self.error.setText(str(exc))

    def commit(self):
        try:self.result_model=self.candidate();self.accept()
        except (ValueError,TypeError,KeyError) as exc:self.error.setText(str(exc))
