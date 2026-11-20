"""Read-only constitutive inspection; never selects or changes solver settings."""
import copy
import hashlib
import json
import numpy as np
from materials import kind,attenuating,ti_stiffness,material_rows
from model import validate

VOIGT=['xx','yy','zz','yz','xz','xy']
PSV=[0,2,4]

# This is the workbench's fixed-backend scope, not SPECFEM's total capabilities.
CAPABILITIES=[
 dict(scene='固—流体—固水平层',operator='固体P-SV + 流体声学势/压力 + 两条声弹耦合界面',inputs='逐层厚度/Vp/Vs/ρ；中间流体Vs=0',boundary='地表无牵引，外侧/底部Stacey，内部界面不是自由边界',scope='三层输入与双场已接入；实际基准计算及波形验收中'),
 dict(scene='各向同性弹性',operator='弹性平面应变 P-SV',inputs='Vp、Vs、ρ',boundary='Stacey / CPML',scope='已接入；数值与波形证据按具体模板匹配'),
 dict(scene='各向同性衰减',operator='P-SV + SLS记忆变量',inputs='Vp、Vs、ρ、Qκ/Qμ、速度参考、N_SLS',boundary='Stacey / 受限CPML',scope='已接入；CPML需两种Q同时有损或同时无损'),
 dict(scene='VTI / 剖面内轴HTI',operator='TI刚度的弹性 P-SV',inputs='Vp0、Vs0、ρ、ε/δ/γ + 对称轴',boundary='当前仅Stacey；TI+CPML拒绝',scope='已接入；平地完整单炮独立比较未通过，主要为边界返回'),
 dict(scene='声学流体',operator='声学势/压力方程',inputs='Vp、ρ；Vs=0',boundary='自由表面压力为零；Stacey / CPML',scope='已有纯声学入口；不能靠弹性6×6正定检查判断流体'),
 dict(scene='空洞',operator='固体域内弹性方程 + 内壁无牵引',inputs='孔洞几何、外部贴体网格',boundary='内壁 σ·n=0；外边界另设',scope='未接入；不是把Vp、Vs、ρ或C设为零'),
 dict(scene='任意方位TI / TI+衰减',operator='可能需三分量耦合 / 各向异性记忆变量',inputs='完整材料、坐标轴及相应损耗模型',boundary='须与相应方程实现配套',scope='当前工作台未接入；完整6×6预览不代表支持此求解路径'),
]


def inspect_material(c):
    validate(c)
    acoustic=c.get('physics','elastic')=='acoustic';mode=kind(c);att=attenuating(c)
    rows=material_rows(c);layers=[]
    for i,layer in enumerate(c['layers']):
        h,vp,vs,rho=layer
        item=dict(layer=i+1,thickness_m=h,density_kg_m3=rho,native_material_row=rows[i])
        if acoustic or (c.get('physics')=='coupled' and vs==0):
            item.update(matrix_pa=None,psv_matrix_pa=None,bulk_modulus_pa=rho*vp**2,
                interpretation='声学体积模量 K=ρVp²；流体剪切模量为零，不按固体刚度正定性验收。')
        else:
            if mode=='isotropic':
                mu=rho*vs**2;lam=rho*vp**2-2*mu
                C=np.zeros((6,6));C[:3,:3]=lam
                for j in range(3):C[j,j]+=2*mu
                for j in range(3,6):C[j,j]=mu
            else:C=ti_stiffness(layer,c['material_properties'][i],mode)
            interp='无衰减弹性刚度；与当前材料参数等价。'
            if att:
                ref=c['attenuation']['velocity_reference']
                interp=('由输入速度构造的参考实矩阵；速度参考为源f0。它不是拟合SLS后的复刚度，也不是已计算的未松弛刚度。' if ref=='source_f0' else
                    '由输入的无限频率速度构造的未松弛实矩阵；有限频率复刚度还依赖Q及SLS记忆变量。')
                p=c['material_properties'][i]
                interp+=f" f0={c['f0_hz']:g}Hz，Qκ={p['qkappa']:g}，Qμ={p['qmu']:g}，N_SLS={c['attenuation']['n_sls']}。"
            item.update(matrix_pa=C.tolist(),psv_matrix_pa=C[np.ix_(PSV,PSV)].tolist(),
                minimum_eigenvalue_pa=float(np.linalg.eigvalsh(C).min()),interpretation=interp)
        layers.append(item)
    mechanism=('声学势/压力方程' if acoustic else '弹性P-SV + SLS记忆变量' if att else '弹性平面应变P-SV')
    if c.get('physics')=='coupled':mechanism='固体弹性P-SV + 流体声学势/压力；两界面法向运动与牵引耦合，固体切向牵引为零'
    return dict(schema='material_inspection_v1',read_only=True,
        input_sha256=hashlib.sha256(json.dumps(c,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest(),
        coordinates='全局 x水平、y出剖面、z向上；当前TI轴不随起伏地表旋转',
        voigt_order=VOIGT,strain_convention='工程剪切应变：[εxx, εyy, εzz, 2εyz, 2εxz, 2εxy]',
        psv_stress_order=['σxx','σzz','σxz'],psv_strain_order=['εxx','εzz','2εxz'],
        psv_active_components=['C11','C13','C15','C33','C35','C55'],
        psv_notes='当前VTI/HTI对称面C15=C35=0。C22不直接进入该P-SV更新；C12/C23/C25用于σyy等压力后处理。6×6预览不表示三维传播。',
        matrix_unit='Pa',display_unit='GPa',medium=mode,attenuation=att,operator=mechanism,
        surface_condition='p=0（声学自由表面）' if acoustic else 'σ·n=0（自由表面，无源处；地表力在源位置施加载荷）',
        selected_outer_boundary=c.get('boundary','stacey'),
        boundary_note='当前选择仅说明输入路径可用；吸收效果、稳定性与完整波形仍需模型级验证。',
        capabilities=copy.deepcopy(CAPABILITIES),layers=layers)
