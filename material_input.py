"""Layer-table/CSV contract shared by the Qt editor and tests; no estimated values."""
import copy
import csv
import io
import math
from materials import kind, attenuating, ti_stiffness, validate_materials
from model import validate

KEYS=('h_m','vp_m_s','vs_m_s','rho_kg_m3','qkappa','qmu','epsilon','delta','gamma','nz')
LABELS=('厚度 (m)','Vp / Vp0 (m/s)','Vs / Vs0 (m/s)','密度 (kg/m³)','Qκ','Qμ (=Qs)','ε','δ','γ','垂向单元数')
TIPS=(
    '从浅到深的层厚，单位m。独立起伏界面时是x=0处厚度；末层厚度为实际计算域，不是无穷大。',
    '各向同性为Vp；TI为对称轴方向Vp0，单位m/s。衰减时按所选参考频率解释。',
    '各向同性为Vs；TI为对称轴方向Vs0，单位m/s。固体大于零；纯声学或固—流体—固的中间流体层为零，流体声速和密度仍为正。',
    '密度，单位kg/m³，必须大于零；不是g/cm³。',
    '原生体积损耗Qκ，无量纲，不等于Qp。正数或9999无损；当前CPML要求Qκ/Qμ同时有损或同时无损。',
    '剪切损耗Qμ=Qs，无量纲。正数或9999无损；不可用0表示无损。',
    'Thomsen ε=(C11−C33)/(2C33)，在材料轴坐标下定义，无量纲，0.1表示10%。',
    'Thomsen δ，无量纲；经C13根号和完整刚度正定性检查，不能仅凭单一取值范围判定材料有效。',
    'Thomsen γ=(C66−C44)/(2C44)，无量纲。当前TI对称面P-SV对γ无独立响应，但完整材料正定检查仍需要γ。',
    '本层垂向谱元数，正整数。全部留空按目标单元边长自动划分；不可一部分留空一部分指定。')

class InputError(ValueError):
    def __init__(self,message,row=None,column=None):
        super().__init__(message);self.row=row;self.column=column


def table_rows(c):
    props=c.get('material_properties',[]);counts=c.get('elements_per_layer')
    return [[str(x) for x in layer]+[str(props[i].get(k,'')) if i<len(props) else '' for k in KEYS[4:9]]+
            [str(counts[i]) if counts is not None else ''] for i,layer in enumerate(c['layers'])]


def build_model(original,rows,mode,attenuation,boundary,check_design=True):
    c=copy.deepcopy(original)
    if mode!='isotropic' and attenuation['enabled']:
        raise InputError('当前不支持TI+衰减；请显式选择支持的材料组合。')
    if not 1<=len(rows)<=40:raise InputError('需要1–40层。')
    if c.get('interface_relief') and len(rows)!=len(c['layers']):
        raise InputError('独立界面与材料层一一对应，不能在材料表改变层数；请先编辑几何。')
    active=KEYS[4:6] if attenuation['enabled'] else KEYS[6:9] if mode!='isotropic' else ()
    layers=[];props=[];counts=[]
    for i,row in enumerate(rows):
        if len(row)!=len(KEYS):raise InputError('材料表列数不完整。',i)
        values={}
        for key in KEYS[:4]+active:
            j=KEYS.index(key)
            try:v=float(row[j])
            except (TypeError,ValueError):raise InputError(f'第{i+1}层：{LABELS[j]}需要填写数字。',i,j)
            if not math.isfinite(v):raise InputError(f'第{i+1}层：{LABELS[j]}需要有限数。',i,j)
            if j in (0,1,3) and v<=0:raise InputError(f'第{i+1}层：{LABELS[j]}须大于零。',i,j)
            if j==2 and (v<0 or (c.get('physics','elastic')=='elastic' and v==0)):
                raise InputError(f'第{i+1}层：弹性Vs须大于零，声学Vs为零。',i,j)
            if j in (4,5) and not (0<v<9998.999 or v==9999):raise InputError(f'第{i+1}层：{LABELS[j]}须为正数(<9998.999)或9999无损。',i,j)
            values[key]=v
        layers.append([values[k] for k in KEYS[:4]]);props.append({k:values[k] for k in active})
        if mode!='isotropic':
            try:ti_stiffness(layers[-1],props[-1],mode)
            except ValueError as exc:raise InputError(f'第{i+1}层：{exc}',i) from exc
        raw=str(row[9]).strip()
        if not raw:counts.append(None)
        else:
            try:n=int(raw)
            except ValueError:raise InputError(f'第{i+1}层：垂向单元数需要正整数。',i,9)
            if n<1:raise InputError(f'第{i+1}层：垂向单元数须大于零。',i,9)
            counts.append(n)
    if any(n is None for n in counts) and not all(n is None for n in counts):
        raise InputError('垂向单元数需全部填写或全部留空。',counts.index(None),9)
    c['layers']=layers;c['boundary']=boundary
    if mode!='isotropic' or 'material_model' in c:c['material_model']=mode
    if attenuation['enabled'] or 'attenuation' in c:c['attenuation']=copy.deepcopy(attenuation)
    if active:c['material_properties']=props
    else:c.pop('material_properties',None)
    if counts[0] is None:c.pop('elements_per_layer',None)
    else:c['elements_per_layer']=counts
    # Whole-design validation catches source/receiver/PML extents after depth edits.
    if check_design:validate(c)
    else:
        # A draft can accept a valid layer table before the survey/time settings exist.
        # It still cannot enter write_case/run without full model.validate.
        validate_materials(c)
        for i,(_,vp,vs,_) in enumerate(layers):
            if c.get('physics')=='acoustic' and vs!=0:raise InputError('声学Vs必须为零。',i,2)
            if c.get('physics','elastic')=='elastic' and mode=='isotropic' and vp<=math.sqrt(4/3)*vs:
                raise InputError('各向同性弹性要求Vp² > 4 Vs²/3。',i,1)
    return c


def read_csv(text,current_rows,mode,attenuation_enabled):
    reader=csv.DictReader(io.StringIO(text.lstrip('\ufeff')))
    fields=reader.fieldnames or []
    required=set(KEYS[:4]+(KEYS[4:6] if attenuation_enabled else KEYS[6:9] if mode!='isotropic' else ()))
    if len(fields)!=len(set(fields)) or set(fields)-set(KEYS) or not required<=set(fields):
        raise InputError('CSV表头不匹配。必需：'+','.join(sorted(required))+'；可选列限于：'+','.join(KEYS))
    parsed=list(reader)
    if not 1<=len(parsed)<=40:raise InputError('CSV需要1–40层。')
    rows=[]
    for i,row in enumerate(parsed):
        if None in row or any(v is None for v in row.values()):raise InputError(f'CSV第{i+2}行字段数量不匹配。')
        counts=current_rows[i][9] if len(parsed)==len(current_rows) else ''
        rows.append([row.get(k,counts if k=='nz' else '').strip() for k in KEYS])
    return rows


def write_csv(rows):
    out=io.StringIO(newline='');writer=csv.writer(out);writer.writerow(KEYS);writer.writerows(rows);return out.getvalue()
