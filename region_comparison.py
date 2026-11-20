"""Optional matched waveform comparison; never synthesizes a no-cavity baseline."""
import copy
import numpy as np
from PySide6 import QtWidgets as W
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from plot_toolbar import PlotToolbar
from custom_scene import physical_hash
from pathlib import Path
import json
from imaging import Gather,spectrum


def check_pair(cavity,reference):
    if not cavity.meta.get('region_case') or cavity.meta['region_case'].get('regions')==[]:raise ValueError('第一组必须为含洞体的原始结果。')
    reference_case=reference.meta.get('region_case')
    if reference_case and reference_case.get('regions')==[]:
        expected=dict(cavity.meta['region_case'],regions=[])
        if expected!=reference_case:raise ValueError('两份贴体网格的背景或地表变形设置不同。')
    elif reference_case or reference.meta.get('model',{}).get('external_geometry'):
        raise ValueError('对照必须是明确无洞的实际波形结果。')
    if physical_hash(cavity.meta['model'])!=physical_hash(reference.meta.get('model',{})):
        raise ValueError('无洞对照的背景、源、阵列或数值参数不同；不能作为只改变洞体的配对。')
    if not np.array_equal(cavity.t,reference.t) or not np.array_equal(cavity.x,reference.x):
        raise ValueError('两组完整时轴或接收坐标不一致，不进行插值、时移或增益拟合。')
    if cavity.meta['component']!='Vz' or reference.meta['component']!='Vz':raise ValueError('请使用保留原始Vz/Vx的结果。')


def observable(g,component,gauge):
    if component=='Vz':return g
    if g.vx is None:raise ValueError('该结果缺少原始Vx。')
    if component=='Vx':return Gather(g.t,g.x,g.vx,dict(g.meta,component='Vx',unit='m/s'))
    from das_observation import horizontal_strain_rate
    from model import elevation
    if np.ptp(elevation(g.meta['model'],g.x))>1e-7:raise ValueError('DAS对比当前仅支持水平直光纤。')
    x=g.x[(g.x-gauge/2>=g.x[0])&(g.x+gauge/2<=g.x[-1])]
    d=horizontal_strain_rate(g.t,g.x,g.vx,x,gauge)
    return Gather(g.t,x,d['strain_rate'],dict(g.meta,component='DAS_strain_rate',unit='s^-1',gauge_length_m=gauge))


class ComparisonDialog(W.QDialog):
    def __init__(self,gather,settings,parent=None):
        super().__init__(parent);self.setWindowTitle('有洞／无洞对比 · 可选分析');self.resize(1420,940)
        self.cavity=gather;self.reference=None;self.settings=copy.deepcopy(settings)
        layout=W.QVBoxLayout(self)
        self.note=W.QLabel('尚未载入匹配的无洞结果。正常正演、波场和频散不需要此步骤。\n对照必须是真实计算结果；核对背景、源、阵列、数值参数与完整时轴，仅洞体不同。');self.note.setWordWrap(True);layout.addWidget(self.note)
        row=W.QHBoxLayout();layout.addLayout(row)
        load=W.QPushButton('打开无洞结果…');load.clicked.connect(self.choose);row.addWidget(load)
        self.component=W.QComboBox();self.component.addItems(['Vz','Vx','DAS应变率']);row.addWidget(self.component)
        self.gauge=W.QDoubleSpinBox();self.gauge.setRange(.01,10000);self.gauge.setValue(settings['gauge_m']);self.gauge.setSuffix(' m 标距');row.addWidget(self.gauge)
        self.gauge.setToolTip('DAS为原始Vx沿水平光纤的有限标距差分，单位s⁻¹；两组使用相同标距。')
        self.method=W.QComboBox();self.method.addItems(['PS','FH']);self.method.setCurrentText(settings['options'].get('method','PS'));row.addWidget(self.method)
        self.update=W.QPushButton('更新对比');self.update.clicked.connect(self.draw);row.addWidget(self.update)
        self.figure=Figure(layout='constrained');self.canvas=FigureCanvasQTAgg(self.figure)
        layout.addWidget(PlotToolbar(self.canvas,self));layout.addWidget(self.canvas,1)
        self.details=W.QLabel('窗口和空间权重沿用打开此页时主工作台的设置；两组始终相同，修改主窗后请重新打开此页。流体声压在波场页单独查看。');self.details.setWordWrap(True);layout.addWidget(self.details)

        from model import ROOT
        from region_result import case_hash, digest, load
        from region_analysis import surface_gather
        case=self.cavity.meta.get('region_case',{})
        if case.get('regions'):
            folder=ROOT/'examples/layered_terrain'
            baseline=case_hash(dict(case,regions=[]))
            for entry in json.loads((folder/'index.json').read_text())['entries']:
                if entry['case_sha256']!=baseline:continue
                path=folder/(entry['key']+'.npz')
                if path.is_file() and digest(path)==entry['result_sha256']:
                    candidate=surface_gather(load(path))
                    check_pair(self.cavity,candidate)
                    self.reference=candidate;self.draw()
                break

    def choose(self):
        path,_=W.QFileDialog.getOpenFileName(self,'打开匹配无洞单炮','','单炮结果 (*.npz)')
        if not path:return
        try:
            with np.load(path,allow_pickle=False) as archive:
                regional='fluid_material' in archive and 'solid_velocity' in archive
            if regional:
                from region_analysis import surface_gather
                from region_result import load
                candidate=surface_gather(load(path))
            else:candidate=Gather.open(path)
            check_pair(self.cavity,candidate)
            self.reference=candidate;self.draw()
        except (ValueError,OSError,KeyError,TypeError) as exc:W.QMessageBox.warning(self,'对照未载入',str(exc))

    def draw(self):
        if self.reference is None:
            self.note.setText('请先打开匹配的无洞结果；不会自动启动计算。');return
        try:
            check_pair(self.cavity,self.reference)
            a=observable(self.cavity,self.component.currentText(),self.gauge.value())
            b=observable(self.reference,self.component.currentText(),self.gauge.value())
            options=dict(self.settings['options'],method=self.method.currentText())
            sa,sb=spectrum(a,**options),spectrum(b,**options)
            for key in ('f','c'):np.testing.assert_array_equal(sa[key],sb[key])
            self.figure.clear();axes=self.figure.subplots(2,3)
            clip=max(float(np.percentile(np.abs(a.traces),99)),float(np.percentile(np.abs(b.traces),99)),1e-30)
            unit='s^-1' if a.meta['component']=='DAS_strain_rate' else 'm/s'
            for ax,data,title in zip(axes[0],[a.traces,b.traces,a.traces-b.traces],['Cavity','No cavity','Cavity - no cavity']):
                im=ax.pcolormesh(a.x,a.t,data,cmap='RdBu_r',shading='nearest',vmin=-clip,vmax=clip)
                for key in ('window_start','window_end'):ax.plot(a.x[sa['indices']],sa[key],color='#c27800',lw=.8)
                ax.set(title=title,xlabel='Receiver x (m)',ylabel='Time (s)',ylim=(a.t[-1],a.t[0]))
            self.figure.colorbar(im,ax=list(axes[0]),label=unit+' / common clip')
            pa,pb=sa['power'],sb['power'];mask=sa['alias']|sb['alias']
            peak=max(float(np.max(pa[~mask])),float(np.max(pb[~mask])),1e-300)
            linear=self.settings.get('power_scale', 'db')=='percent'
            lower,upper=(0.,100.) if linear else (self.settings.get('db_floor',-40.),0.)
            for ax,power,title in zip(axes[1,:2],[pa,pb],['Cavity','No cavity']):
                relative=np.where(mask,np.nan,power/peak)
                values=100*relative if linear else 10*np.log10(np.maximum(relative,1e-30))
                im=ax.pcolormesh(sa['f'],sa['c'],values,shading='nearest',cmap='magma',vmin=lower,vmax=upper)
                ax.set(title=title+' / '+sa['method'],xlabel='Frequency (Hz)',ylabel='Phase velocity (m/s)')
            self.figure.colorbar(im,ax=list(axes[1,:2]),label='Power (% of common peak)' if linear else 'dB / common peak')
            difference=np.where(mask,np.nan,(pa-pb)/peak*100)
            scale=max(float(np.nanmax(np.abs(difference))),1e-12)
            im=axes[1,2].pcolormesh(sa['f'],sa['c'],difference,shading='nearest',cmap='RdBu_r',vmin=-scale,vmax=scale)
            axes[1,2].set(title='Linear power difference',xlabel='Frequency (Hz)',ylabel='Phase velocity (m/s)')
            self.figure.colorbar(im,ax=axes[1,2],label='% of common peak')
            self.note.setText('输入与完整时轴匹配；完整波形和共同窗频散对比。没有拟合时移/增益，差值不等于散射能流或独立精度验收。\n有洞：'+str(a.meta['run'])+'；无洞：'+str(b.meta['run']))
            self.canvas.draw()
        except (ValueError,KeyError,TypeError,AssertionError) as exc:self.note.setText('未生成对比：'+str(exc))
