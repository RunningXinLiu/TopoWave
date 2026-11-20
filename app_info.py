"""Public workbench identity; no runtime paths or automatic network access."""
from PySide6 import QtCore as C, QtGui as G, QtWidgets as W

APP_NAME = "TopoWave · 地形波场工作台"
AUTHOR_NAME = "Xin Liu"
AUTHOR_WEBSITE = "https://runningxinliu.github.io/"
AUTHOR_EMAIL = "xinliu_geo@outlook.com"
RELEASE_STATUS = "私有源码试用版 · 尚未公开发布"

ABOUT_TEXT = f"""{APP_NAME}
{RELEASE_STATUS}

开发与维护：{AUTHOR_NAME}
个人主页：{AUTHOR_WEBSITE}
联系邮箱：{AUTHOR_EMAIL}

软件定位
面向平地与起伏地表的主动源单炮实验。自定义地表、层界、材料、震源和接收排列，查看实际谱元网格、网格QC、波场与完整单炮记录；从面波时间窗计算PS/FH频散能量，叠加完整台阵中心的一维理论参考。

来源与致谢
正演使用SPECFEM2D及本项目明确登记的适配/候选构建；本工作台并非SPECFEM2D官方产品。感谢SPECFEM2D开发团队。
理论参考使用disba/CPS、QEDisp及限定VTI范围的SASEM接口。感谢Robert B. Herrmann、disba维护者、Pan Lei的QEDispInv，以及Caiwang Shi与合作者的DispersionCurves / SASEM。
界面和运行环境使用PySide6 / Qt、NumPy、SciPy、Matplotlib等组件。各上游项目保留原署名和许可，致谢不代替再分发授权。

当前范围
已核验的ISO平地/山丘与12个空洞/流体固定案例分别标注证据。空洞结果支持完整地表记录、面波窗及PS/FH；一维理论只作无洞背景参考。TI空洞、Q与圆洞/多洞/地形组合及实际衰减理论未支持。TI固定大域证据不覆盖旧小域失败。模板可载入不代表任意修改后仍可靠。桌面波场是抽样预览，完整原生结果保留在计算端。

反馈建议
请提供操作步骤、场景参数及报错；如涉及波形，请说明模板、分量、完整记录或面波窗。分享前检查运行路径等个人信息。
打开本窗口不会联网；仅点击主页或邮箱按钮时打开系统应用，不自动发送邮件。
"""


class AboutDialog(W.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("workbenchAboutDialog")
        self.setWindowTitle(f"关于 · {APP_NAME}")
        self.resize(700, 610)
        layout = W.QVBoxLayout(self)
        self.content = W.QPlainTextEdit()
        self.content.setObjectName("workbenchAboutContent")
        self.content.setReadOnly(True)
        self.content.setPlainText(ABOUT_TEXT)
        layout.addWidget(self.content)
        links = W.QHBoxLayout()
        for label, url in [("作者主页", AUTHOR_WEBSITE), ("联系邮箱", f"mailto:{AUTHOR_EMAIL}")]:
            b = W.QPushButton(label)
            b.setToolTip(url)
            b.clicked.connect(lambda checked=False, u=url: G.QDesktopServices.openUrl(C.QUrl(u)))
            links.addWidget(b)
        links.addStretch()
        close = W.QPushButton("关闭")
        close.clicked.connect(self.reject)
        links.addWidget(close)
        layout.addLayout(links)
