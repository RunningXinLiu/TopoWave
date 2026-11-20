"""Shared native-widget explanations, including labels and spin-box editors."""
from PySide6 import QtWidgets as W

DAS_TIP = ('节点Vz/Vx为SPECFEM2D粒子速度，单位m/s。DAS为水平直光纤的有限标距轴向应变率：'
           '[Vx(x+L/2,t)−Vx(x−L/2,t)]/L，单位s⁻¹。使用已有Vx，不对Vz求导、不再次求时间导数，也不重跑正演。'
           '当前不支持沿起伏地表或弯曲光纤；请用平地模板测试。')
GAUGE_TIP = ('L为标距（米），是轴向应变率的空间平均长度，与通道间距不同。'
             '中心取原接收点，两端必须精确落在已保存的Vx接收点；不插值。'
             '均匀道距Δx时，当前可用L=2nΔx。2m道距可选4、8、12m；L=8m时边缘各去掉2个中心。'
             '较长标距会平均短波长信号，可能造成衰减或零点，不是越长越好。只改变DAS观测，不改变原始波场。')


def expose_parameter_tips(widget):
    for form in widget.findChildren(W.QFormLayout):
        for i in range(form.rowCount()):
            item = form.itemAt(i, W.QFormLayout.ItemRole.FieldRole)
            field = item.widget() if item else None
            label = form.labelForField(field) if field else None
            if field and label and field.toolTip():
                label.setToolTip(field.toolTip())
    for box in widget.findChildren(W.QAbstractSpinBox):
        if box.toolTip():
            box.lineEdit().setToolTip(box.toolTip())
