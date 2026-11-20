"""Edit physical elevations at shared control points before changing the live model."""
import csv
from copy import deepcopy

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6 import QtCore as C, QtWidgets as W

from model import elevation, interface_spline, validate


class GeometryEditor(W.QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.original = config
        self.result_model = None
        self.setWindowTitle("设计地表与地下界面")
        self.resize(980,560)
        layout = W.QVBoxLayout(self)
        self.mode = W.QComboBox()
        self.mode.addItems(["各层随地表等厚起伏","分别输入各层底的高程"])
        self.mode.setCurrentIndex(int(bool(config.get("interface_relief"))))
        layout.addWidget(self.mode)
        self.hint = W.QLabel()
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self.table = W.QTableWidget()
        self.table.setColumnCount(2+len(config["layers"]))
        self.table.setHorizontalHeaderLabels(["水平 x (m)","地表 z (m)"]+
                                              [f"层 {i+1} 底 z (m)" for i in range(len(config["layers"]))])
        self.table.horizontalHeader().setSectionResizeMode(W.QHeaderView.ResizeMode.ResizeToContents)
        split=W.QSplitter()
        split.addWidget(self.table)
        self.figure=Figure(layout="constrained",facecolor="#fbfcfa")
        self.canvas=FigureCanvasQTAgg(self.figure)
        split.addWidget(self.canvas)
        split.setSizes([430,530])
        layout.addWidget(split,1)
        self.redraw=C.QTimer(self)
        self.redraw.setSingleShot(True)
        self.redraw.timeout.connect(self.draw_surface)
        self.table.itemChanged.connect(lambda *_: self.redraw.start(0))
        self.table.model().rowsRemoved.connect(lambda *_: self.redraw.start(0))
        self.canvas.mpl_connect("button_press_event",self.place_surface_point)
        xs = [p[0] for p in config["surface"]] or [0,config["width_m"]]
        self.fill(np.column_stack([xs]+[elevation(config,xs,i) for i in range(len(config["layers"])+1)]))
        bar = W.QHBoxLayout()
        for label,slot in [("插入中间点",self.insert_point),("删除选中点",self.remove_point),
                           ("填入山丘示例",self.hill),("读入 CSV…",self.read_csv),("导出 CSV…",self.write_csv)]:
            btn = W.QPushButton(label)
            btn.clicked.connect(slot)
            bar.addWidget(btn)
        layout.addLayout(bar)
        self.message = W.QLabel()
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        buttons = W.QDialogButtonBox()
        ok = buttons.addButton("应用到模型",W.QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("取消",W.QDialogButtonBox.ButtonRole.RejectRole)
        ok.clicked.connect(self.apply_points)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.mode.currentIndexChanged.connect(self.change_mode)
        self.change_mode()

    def change_mode(self):
        independent = self.mode.currentIndex()==1
        for col in range(2,self.table.columnCount()):
            self.table.setColumnHidden(col,not independent)
        extra = ("每列给出同一 x 处各层底的绝对高程，表内厚度将按左端点更新。"
                 if independent else "只需输入地表，后续层底等于地表高程减去累计层厚。")
        self.hint.setText(f"在右图点击绘制地表，也可在表格里输入坐标或导入 CSV。x 从 0 到 {self.original['width_m']:g} m，z 为高程，向上为正。"+extra)

    def draw_surface(self):
        self.figure.clear()
        self.ax=self.figure.subplots()
        width=self.original["width_m"]
        self.ax.set(xlabel="Horizontal position x (m)",ylabel="Surface elevation z (m)",
                    title="Edit surface control points",xlim=(-.015*width,1.015*width))
        try:
            pts=self.numbers()
            x,z=pts[:,0],pts[:,1]
            path=np.linspace(x[0],x[-1],500)
            height=interface_spline(x,z)(path)
            pad=max(width*.035,np.ptp(height)*.25,1.)
            self.ax.set_ylim(height.min()-pad,height.max()+pad)
            self.ax.plot(path,height,color="#19644e")
            self.ax.plot(x,z,"o",ms=5,color="#b95e26")
            self.ax.grid(alpha=.2)
        except (IndexError,ValueError,AttributeError):
            pass
        self.canvas.draw_idle()

    def place_surface_point(self,event):
        if event.button!=1 or event.inaxes is not getattr(self,"ax",None) or event.xdata is None:
            return
        try:
            pts=self.numbers()
            xs=pts[:,0]
            if np.any(np.diff(xs)<=0):
                raise ValueError("请先按 x 递增排列控制点。")
            x=float(np.clip(event.xdata,xs[0],xs[-1]))
            z=round(float(event.ydata),4)
            i=int(np.argmin(abs(xs-x)))
            # Use screen-space proximity so end points can also be repositioned vertically.
            if abs(self.ax.transData.transform((xs[i],z))[0]-event.x)<7:
                self.table.item(i,1).setText(str(z))
            else:
                i=int(np.searchsorted(xs,x))
                other=[float(interface_spline(xs,col)(x)) for col in pts[:,2:].T]
                self.put(i,[x,z,*other])
            self.table.setCurrentCell(i,1)
        except (ValueError,AttributeError) as exc:
            self.message.setText("请先修正控制点。 "+str(exc))

    def fill(self,rows):
        self.table.setRowCount(0)
        for row in rows:
            self.put(self.table.rowCount(),row)

    def put(self,i,values):
        self.table.insertRow(i)
        for j,v in enumerate(values):
            self.table.setItem(i,j,W.QTableWidgetItem(format(float(v),".15g")))

    def numbers(self):
        return np.array([[float(self.table.item(i,j).text()) for j in range(self.table.columnCount())]
                         for i in range(self.table.rowCount())])

    def insert_point(self):
        try:
            points = self.numbers()
            i = min(max(0,self.table.currentRow()),len(points)-2)
            self.put(i+1,(points[i]+points[i+1])/2)
            self.table.setCurrentCell(i+1,1)
        except (ValueError,AttributeError):
            self.message.setText("请先把控制点补全为数值。")

    def remove_point(self):
        i = self.table.currentRow()
        if 0<i<self.table.rowCount()-1:
            self.table.removeRow(i)
        else:
            self.message.setText("首末点用于确定模型边界，请选择中间点。")

    def from_surface(self,x,z):
        depths = np.cumsum([r[0] for r in self.original["layers"]])
        self.fill(np.column_stack([x,z]+[z-d for d in depths]))
        self.mode.setCurrentIndex(0)

    def hill(self):
        x = self.original["width_m"]*np.array([0,.25,.5,.75,1])
        height = min(self.original["width_m"]*.04,self.original["layers"][0][0]*.5)
        self.from_surface(x,height*np.array([0,.5,1,.5,0]))
        self.message.setText("已填入五个示例点，可双击修改高程后应用。")

    def read_csv(self):
        path,_ = W.QFileDialog.getOpenFileName(self,"读取地表与层界 CSV","","CSV (*.csv)")
        if not path:
            return
        try:
            with open(path,encoding="utf-8-sig",newline="") as f:
                reader=csv.DictReader(f);rows=list(reader);names=reader.fieldnames
            if len(rows)<2:
                raise ValueError("至少需要两个地表点。")
            basic=['x_m','z_m'];full=basic+[f'bottom_{i+1}_z_m' for i in range(len(self.original['layers']))]
            if names not in (basic,full):raise ValueError('表头必须为 '+','.join(basic)+' 或 '+','.join(full))
            if any(None in r or any(v is None for v in r.values()) for r in rows):raise ValueError('CSV列数不一致。')
            points=np.array([[float(r[k]) for k in names] for r in rows])
            independent=names==full
            self.model_from_points(points,independent)  # Validate before changing the editor.
            if independent:self.fill(points);self.mode.setCurrentIndex(1)
            else:self.from_surface(points[:,0],points[:,1])
            self.message.setText('已读入地表及全部层底。' if independent else '已读入地表，层界按等厚方式生成。')
        except (OSError,ValueError,KeyError,TypeError) as exc:
            self.message.setText("读取失败，请检查 x_m,z_m 表头和数值。 "+str(exc))

    def model_from_points(self,points,independent):
        if not np.isfinite(points).all():raise ValueError('坐标必须为有限数。')
        c=deepcopy(self.original);c['surface']=points[:,:2].tolist();c['interface_relief']=[]
        if independent:
            thickness=-np.diff(points[0,1:])
            for layer,h in zip(c['layers'],thickness):layer[0]=float(h)
            c['interface_relief']=(points[:,2:]-points[:,1,None]+np.cumsum(thickness)).T.tolist()
        validate(c)
        return c

    def write_csv(self):
        try:
            independent=self.mode.currentIndex()==1;points=self.numbers()
            if not independent:points=points[:,:2]
            self.model_from_points(points,independent)
            path,_=W.QFileDialog.getSaveFileName(self,'导出地表与层界','geometry.csv','CSV (*.csv)')
            if not path:return
            names=['x_m','z_m']+([f'bottom_{i+1}_z_m' for i in range(len(self.original['layers']))] if independent else [])
            with open(path,'w',encoding='utf-8',newline='') as f:
                writer=csv.writer(f);writer.writerow(names)
                writer.writerows([[format(float(v),'.17g') for v in row] for row in points])
            self.message.setText('已导出当前几何，可重新导入。')
        except (OSError,ValueError,TypeError,AttributeError) as exc:self.message.setText('未导出：'+str(exc))

    def apply_points(self):
        try:
            points = self.numbers()
            c=self.model_from_points(points,self.mode.currentIndex()==1)
        except (ValueError,TypeError,AttributeError) as exc:
            self.message.setText("请修正后应用。 "+str(exc))
            return
        self.result_model = c
        self.accept()
