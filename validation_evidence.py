"""Read-only, hash-checked evidence viewer. Never modifies analysis settings."""
import json
from pathlib import Path
from PySide6 import QtCore as C, QtGui as G, QtWidgets as W
from model import ROOT
from custom_scene import digest


def evidence_path(entry, root=ROOT):
    path=(root/entry['path']).resolve()
    if not path.is_relative_to(root.resolve()):raise ValueError('证据路径超出项目。')
    if not path.is_file():raise ValueError('此安装未附带这份证据文件。')
    if digest(path)!=entry['sha256']:raise ValueError('证据文件已改变；与登记散列不符。')
    return path


def dialog(owner):
    entries=json.loads((ROOT/'examples/validation_evidence.json').read_text())['entries']
    d=W.QDialog(owner);d.setWindowTitle('模板验证总览与已算理论对照');d.resize(1150,820)
    layout=W.QVBoxLayout(d)
    note=W.QLabel('这里显示冻结算例的报告和原图，不是当前自定义模型的计算结果。\n'
                  '不会修改波形、面波窗、等权设置或频散能量；空气层理论根仍属研究对照。')
    note.setWordWrap(True);layout.addWidget(note)
    choice=W.QComboBox();choice.addItems([e['label'] for e in entries]);layout.addWidget(choice)
    scope=W.QLabel();scope.setWordWrap(True);layout.addWidget(scope)
    pages=W.QStackedWidget();layout.addWidget(pages,1)
    text=W.QTextBrowser();text.setOpenLinks(False);pages.addWidget(text)
    scroll=W.QScrollArea();picture=W.QLabel();picture.setAlignment(C.Qt.AlignmentFlag.AlignTop|C.Qt.AlignmentFlag.AlignLeft)
    scroll.setWidget(picture);pages.addWidget(scroll)
    def show(i):
        entry=entries[i];scope.setText(entry['scope'])
        try:
            path=evidence_path(entry)
            if path.suffix=='.png':
                pix=G.QPixmap(str(path))
                if pix.isNull():raise ValueError('图片解码失败。')
                picture.setPixmap(pix);picture.resize(pix.size());pages.setCurrentWidget(scroll)
            else:
                text.setMarkdown(path.read_text());pages.setCurrentWidget(text)
        except (ValueError,OSError,KeyError) as exc:
            text.setPlainText(str(exc));pages.setCurrentWidget(text)
    choice.currentIndexChanged.connect(show)
    row=W.QHBoxLayout();layout.addLayout(row)
    open_button=W.QPushButton('在系统查看器打开原文件')
    def open_file():
        try:G.QDesktopServices.openUrl(C.QUrl.fromLocalFile(str(evidence_path(entries[choice.currentIndex()]))))
        except (ValueError,OSError) as exc:W.QMessageBox.warning(d,'证据不可用',str(exc))
    open_button.clicked.connect(open_file);row.addWidget(open_button)
    close=W.QPushButton('关闭');close.clicked.connect(d.accept);row.addWidget(close)
    show(0)
    return d
