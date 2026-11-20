"""Native Qt view of material tensors and supported equation/boundary combinations."""
import copy,json
from pathlib import Path
from PySide6 import QtCore as C,QtGui as G,QtWidgets as W
from material_inspection import inspect_material,VOIGT,PSV

class MaterialInspection(W.QDialog):
    def __init__(self,config,parent=None,selected_layer=0):
        super().__init__(parent);self.config=copy.deepcopy(config);self.report=inspect_material(config)
        self.setWindowTitle('刚度与方程 / 边界 · 只读');self.resize(1080,800)
        layout=W.QVBoxLayout(self);self.tabs=W.QTabWidget();layout.addWidget(self.tabs)
        page=W.QWidget();body=W.QVBoxLayout(page);self.tabs.addTab(page,'逐层刚度')
        self.layers=W.QComboBox()
        for x in self.report['layers']:self.layers.addItem(f"第{x['layer']}层 · 厚{x['thickness_m']:g}m · ρ={x['density_kg_m3']:g}kg/m³")
        body.addWidget(self.layers)
        self.note=W.QLabel();self.note.setWordWrap(True);body.addWidget(self.note)
        convention=W.QLabel(self.report['coordinates']+'\n'+self.report['strain_convention']+'；界面显示 GPa，导出 Pa。')
        convention.setWordWrap(True);body.addWidget(convention)
        tables=W.QHBoxLayout();body.addLayout(tables,1)
        labels=[f'{i+1}·{name}' for i,name in enumerate(VOIGT)]
        self.full=self.matrix_box(tables,'完整材料 C（GPa）',labels,labels)
        self.psv=self.matrix_box(tables,'当前 P-SV 子矩阵（GPa）',['σxx','σzz','σxz'],['εxx','εzz','2εxz'])
        self.explain=W.QLabel(self.report['psv_notes']+'\n绿色单元为P-SV使用的刚度分量。正定性是材料检查，不是时间推进或PML稳定性证明。')
        self.explain.setWordWrap(True);body.addWidget(self.explain)
        body.addWidget(W.QLabel('实际导出的材料行（数值保持原精度；TI刚度单位Pa，密度kg/m³）：'))
        self.native=W.QPlainTextEdit();self.native.setReadOnly(True);self.native.setMaximumHeight(75);body.addWidget(self.native)
        self.native.setToolTip('原生材料行遵循固定SPECFEM后端格式；不是通用6×6矩阵交换文件。')
        scope=W.QWidget();sbody=W.QVBoxLayout(scope);self.tabs.addTab(scope,'方程与边界兼容范围')
        headline=W.QLabel('当前：'+self.report['operator']+'；外边界 '+self.report['selected_outer_boundary']+'\n自由表面：'+self.report['surface_condition']+'\n'+self.report['boundary_note'])
        headline.setWordWrap(True);sbody.addWidget(headline)
        caps=self.report['capabilities'];self.compatibility=W.QTableWidget(len(caps),5)
        self.compatibility.setHorizontalHeaderLabels(['介质场景','所解方程','物理输入','边界实现','当前工作台范围'])
        self.compatibility.setEditTriggers(W.QAbstractItemView.EditTrigger.NoEditTriggers);self.compatibility.setWordWrap(True)
        self.compatibility.horizontalHeader().setSectionResizeMode(W.QHeaderView.ResizeMode.Stretch)
        for i,x in enumerate(caps):
            for j,k in enumerate(('scene','operator','inputs','boundary','scope')):
                item=W.QTableWidgetItem(x[k]);item.setToolTip(x[k]);self.compatibility.setItem(i,j,item)
        sbody.addWidget(self.compatibility)
        note=W.QLabel('材料决定本构关系；空间离散、时间推进和吸收边界需与方程相容。这里说明接口能力，逐个模板的通过/失败仍在“检查当前参数与采样”中查看。')
        note.setWordWrap(True);sbody.addWidget(note)
        buttons=W.QHBoxLayout();layout.addLayout(buttons)
        save=W.QPushButton('导出刚度、输入与适用范围 JSON…');save.clicked.connect(self.save);buttons.addWidget(save)
        close=W.QPushButton('关闭');close.clicked.connect(self.accept);buttons.addWidget(close)
        self.layers.currentIndexChanged.connect(self.show_layer);self.layers.setCurrentIndex(max(0,min(selected_layer,len(self.report['layers'])-1)));self.show_layer()

    def matrix_box(self,layout,title,rowlabels,collabels):
        box=W.QGroupBox(title);layout.addWidget(box);inside=W.QVBoxLayout(box)
        table=W.QTableWidget(len(rowlabels),len(collabels));table.setVerticalHeaderLabels(rowlabels);table.setHorizontalHeaderLabels(collabels)
        table.setEditTriggers(W.QAbstractItemView.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(W.QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().setSectionResizeMode(W.QHeaderView.ResizeMode.Stretch);inside.addWidget(table)
        return table

    def show_layer(self,*_):
        x=self.report['layers'][self.layers.currentIndex()];matrix=x['matrix_pa']
        self.note.setText(x['interpretation']+(f" 最小刚度特征值 {x['minimum_eigenvalue_pa']/1e9:.6g} GPa。" if matrix else f" K={x['bulk_modulus_pa']/1e9:.6g} GPa。"))
        self.full.setEnabled(matrix is not None);self.psv.setEnabled(matrix is not None)
        self.explain.setVisible(matrix is not None)
        for table,a in [(self.full,matrix),(self.psv,x['psv_matrix_pa'])]:
            for i in range(table.rowCount()):
                for j in range(table.columnCount()):
                    item=W.QTableWidgetItem('—' if a is None else f'{a[i][j]/1e9:.6g}')
                    item.setTextAlignment(C.Qt.AlignmentFlag.AlignCenter)
                    if a is not None:
                        ci,cj=(i+1,j+1) if table is self.full else (PSV[i]+1,PSV[j]+1)
                        item.setToolTip(f'C{ci}{cj} = {a[i][j]:.17g} Pa; {a[i][j]/1e9:.17g} GPa')
                        if table is self.psv or (i in PSV and j in PSV):item.setBackground(G.QColor('#e0efe6'))
                    table.setItem(i,j,item)
        self.native.setPlainText(x['native_material_row'])

    def showEvent(self,event):
        super().showEvent(event);self.compatibility.resizeRowsToContents()

    def save(self):
        path,_=W.QFileDialog.getSaveFileName(self,'保存材料检查','material_inspection.json','JSON (*.json)')
        if path:
            try:Path(path).write_text(json.dumps(dict(input=self.config,inspection=self.report),ensure_ascii=False,indent=2,allow_nan=False))
            except OSError as exc:W.QMessageBox.warning(self,'保存失败',str(exc))
