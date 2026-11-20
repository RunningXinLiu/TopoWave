"""Review a survey candidate before explicitly applying receiver settings."""
from PySide6 import QtWidgets as W
from acquisition_design import assess_bounds
from parameter_setup import assess_acquisition
from materials import kind,attenuating

class AcquisitionDialog(W.QDialog):
    def __init__(self,c,fmin,fmax,parent=None):
        super().__init__(parent);self.setWindowTitle('观测设计 · 频带、孔径与到时预算');self.resize(760,670)
        self.config=c;self.values=None;self.report=None
        layout=W.QVBoxLayout(self)
        note=W.QLabel('先选研究频带，再检查源谱、波长、孔径和记录长度。延长孔径主要提高分辨率，不能保证消除等权阵列的旁瓣；减小道距主要控制空间混叠。长孔径也会混合更多横向结构，所有建议均为候选。')
        note.setWordWrap(True);layout.addWidget(note)
        form=W.QFormLayout();layout.addLayout(form);self.fields={}
        self.basis=W.QComboBox();self.basis.addItem('局部ISO基阶理论（只覆盖R0）','r0');self.basis.addItem('手动相/群速度范围（高阶、板波、TI、Q）','manual')
        iso=kind(c)=='isotropic' and not attenuating(c) and c.get('physics','elastic')=='elastic' and c.get('domain_kind')!='free_plate'
        if not iso:self.basis.setCurrentIndex(1);self.basis.model().item(0).setEnabled(False)
        form.addRow('估计依据',self.basis)
        for key,label,value,hi in [('fmin','目标频率下限 (Hz)',fmin,1000),('fmax','目标频率上限 (Hz)',fmax,1000),('phase_min','最小相速度 (m/s)',0,20000),('phase_max','最大相速度 (m/s)',0,20000),('group_min','最慢群速度 (m/s)',0,20000),('width','目标相对主峰宽度',.2,.9)]:
            w=W.QDoubleSpinBox();w.setRange(0,hi);w.setDecimals(3);w.setValue(value);self.fields[key]=w;form.addRow(label,w)
            w.setToolTip('手动范围需来自目标模态理论或实际波包；0表示尚未填写，不自动用Vs或相速度代替群速度。' if key.startswith(('phase','group')) else '仅用于设计评估，不自动修改源、模型或频散计算设置。')
        self.weight=W.QComboBox();self.weight.addItem('等权（默认）','equal');self.weight.addItem('Hann（可选）','hann');form.addRow('空间权重',self.weight)
        self.weight.setToolTip('默认等权；可评估Hann对所需孔径的影响。应用阵列不会自动更改频散分析权重。')
        calc=W.QPushButton('计算并检查候选');calc.clicked.connect(self.assess);layout.addWidget(calc)
        self.text=W.QPlainTextEdit();self.text.setReadOnly(True);layout.addWidget(self.text)
        self.apply_button=W.QPushButton('仅填入候选道距与道数');self.apply_button.setEnabled(False);self.apply_button.clicked.connect(self.apply);layout.addWidget(self.apply_button)
        close=W.QPushButton('关闭，不改参数');close.clicked.connect(self.reject);layout.addWidget(close)
        for w in self.fields.values():w.valueChanged.connect(self.invalidate)
        self.basis.currentIndexChanged.connect(self.invalidate);self.weight.currentIndexChanged.connect(self.invalidate)
        self.invalidate()
        if iso:self.assess()

    def invalidate(self,*_):
        self.report=None;self.apply_button.setEnabled(False)
        for k in ('phase_min','phase_max','group_min'):self.fields[k].setEnabled(self.basis.currentData()=='manual')
        self.text.setPlainText('修改后请重新计算候选。手动模式必须填写相速度与群速度范围。')

    def assess(self):
        self.report=None;self.apply_button.setEnabled(False)
        try:
            v={k:w.value() for k,w in self.fields.items()};weight=self.weight.currentData()
            if self.basis.currentData()=='r0':
                r=assess_acquisition(self.config,v['fmin'],v['fmax'],v['width'],weight)
                # Preserve the R0 wavelength calculation, not independent worst-case bounds.
                import math
                c=self.config;pml=c.get('pml_elements',0)*c['width_m']/math.ceil(c['width_m']/c['element_m']) if c.get('boundary')=='cpml' else 0
                r['fits_current_domain']=r['candidate_receivers']<=2000 and c['first_x_m']>pml and c['first_x_m']+r['candidate_aperture_m']<c['width_m']-pml
                r['candidate_values']=dict(spacing_m=r['candidate_spacing_m'],receivers=r['candidate_receivers'])
                slow=.8*min(min(row) for row in r['group_m_s'])
                r['candidate_receiver_duration_candidate_s']=c.get('source_t0_s',0)+max(abs(c['first_x_m']-c['shot_x_m']),abs(c['first_x_m']+r['candidate_aperture_m']-c['shot_x_m']))/slow+3/v['fmin']
            else:r=assess_bounds(self.config,v['fmin'],v['fmax'],v['phase_min'],v['phase_max'],v['group_min'],v['width'],weight)
            span=(self.config['nstep']-1)*self.config['dt_s']
            sufficient=r['current_four_samples_ok'] and r.get('current_width_fraction_estimate') is not None and r['current_width_fraction_estimate']<=v['width']
            self.text.setPlainText(f"候选，非精度认证。波长 {r['wavelength_min_m']:.2f}–{r['wavelength_max_m']:.2f} m\n"
                f"当前孔径 {r['current_aperture_m']:g} m，道距 {r['current_spacing_m']:g} m；四点/最短波长：{'满足' if r['current_four_samples_ok'] else '不足'}\n"
                +('当前采样和理想孔径估计已达到该目标，建议保留；不必为了符合最小候选而缩短现有阵列。\n' if sufficient else '')+
                f"最小设计候选：道距 {r['candidate_spacing_m']:g} m，{r['candidate_receivers']} 道，孔径 {r['candidate_aperture_m']:g} m\n"
                f"近偏移起始试算约 {r['candidate_near_offset_m']:.1f} m（不自动移动炮点或首道）\n"
                f"当前记录跨度 {span:.3f} s；当前排列到时预算 {r['current_receiver_duration_candidate_s']:.3f} s\n"
                f"候选排列到时预算 {r['candidate_receiver_duration_candidate_s']:.3f} s（非自动延时；高阶/散射须另检查）\n"
                f"理想Ricker目标频带最小相对幅度 {r['source_band_min_relative_amplitude']:.4f}\n"
                +('候选阵列可放入当前非PML域；应用后须重新检查传播时长和边界。' if r['fits_current_domain'] else '候选阵列无法放入当前非PML域或道数过多；请先设计计算域/首道，未自动扩域。')+'\n'+r['explanation'])
            self.report=r;self.apply_button.setEnabled(r['fits_current_domain'])
        except (ValueError,RuntimeError,ImportError,ArithmeticError) as exc:self.text.setPlainText(str(exc))

    def apply(self):
        if self.report and self.report['fits_current_domain']:
            self.values=self.report['candidate_values'];self.accept()
