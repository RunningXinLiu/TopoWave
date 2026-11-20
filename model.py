"""Model coordinates are x (right) and z (up). Depths are measured vertically."""
import json
import math
import re
from pathlib import Path

import numpy as np
from scipy.interpolate import CubicSpline
from materials import validate_materials, material_rows, attenuating, kind

ROOT = Path(__file__).resolve().parent
COLUMNS = ("厚度 (m)", "Vp (m/s)", "Vs (m/s)", "密度 (kg/m³)")
SOLVER_DEFAULTS = dict(source_mode="legacy", boundary="stacey", pml_elements=12, source_t0_s=0., damping_change_factor_elastic=1., cpml_alpha_factor=1.)


def snapshot_interval(c):
    return c.get("snapshot_stride", 0) or max(1, math.ceil(c["nstep"]/60))


def read_model(path):
    if Path(path).suffix.lower()=='.bgpscene':
        from custom_scene import read_document
        config=read_document(path)['model']
        if config is None:raise ValueError('场景仍是未完成草稿，不能生成求解器输入。')
        return config
    config = json.loads(Path(path).read_text())
    if config.get('schema')=='custom_scene_v1':
        from custom_scene import read_document
        config=read_document(path)['model']
        if config is None:raise ValueError('场景仍是未完成草稿，不能生成求解器输入。')
        return config
    if config.get("schema")=="active_shot_template_v1":
        from shot_templates import read_template
        return read_template(path)[0]
    validate(config)
    return config


def stations(c):
    return c["first_x_m"] + np.arange(c["receivers"]) * c["spacing_m"]


def interface_spline(x,z):
    slopes = np.diff(z)/np.diff(x)
    return CubicSpline(x,z,bc_type=((1,slopes[0]),(1,slopes[-1])))


def elevation(c, x, boundary=0):
    depth = sum(row[0] for row in c["layers"][:boundary])
    if not c["surface"]:
        return np.zeros_like(x, dtype=float) - depth
    xp, zp = np.array(c["surface"]).T
    if boundary and c.get("interface_relief"):
        zp = zp + c["interface_relief"][boundary-1]
    return interface_spline(xp,zp-depth)(x)


def validate(c):
    if c.get("external_geometry") is not None:
        raise ValueError("此模型依赖外部空洞网格；当前桌面入口不能重建，必须使用独立空洞入口及完整case.json。")
    if c.get("schema") != 1:
        raise ValueError("需要版本 1 的模型 JSON。")
    for key in ("width_m", "shot_x_m", "shot_depth_m", "f0_hz", "first_x_m", "spacing_m",
                "receiver_depth_m", "element_m", "dt_s"):
        if not isinstance(c[key], (int, float)) or not math.isfinite(c[key]):
            raise ValueError(f"{key} 必须为有限数。")
    for key in ("receivers", "nstep", "sample_stride"):
        if type(c[key]) is not int:
            raise ValueError(f"{key} 必须为整数。")
    stride = c.get("snapshot_stride", 0)
    if type(stride) is not int or stride < 0:
        raise ValueError("快照间隔须为非负整数，0 表示自动。")
    if not 3 <= c["receivers"] <= 2000 or not 2 <= c["nstep"] <= 200000 or not 1 <= c["sample_stride"] < c["nstep"]:
        raise ValueError("检查道数 (3–2000)、计算步数 (2–200000) 和输出步长。")
    layers = np.asarray(c["layers"], dtype=float)
    if layers.ndim != 2 or layers.shape[1] != 4 or not 1 <= len(layers) <= 40:
        raise ValueError("请填写 1–40 层，每层四个参数。")
    counts = c.get("elements_per_layer")
    if counts is not None and (not isinstance(counts,list) or len(counts)!=len(layers) or
                               any(type(n) is not int or n<1 for n in counts)):
        raise ValueError("垂向单元数需逐层填写正整数，或留空使用自动划分。")
    acoustic = c.get("physics", "elastic") == "acoustic"
    coupled = c.get('physics') == 'coupled'
    if c.get("source_mode", "legacy") not in ("legacy", "surface_force"):
        raise ValueError("未知震源施加方式。")
    if c.get("source_mode") == "surface_force" and (acoustic or c["shot_depth_m"] != 0):
        raise ValueError("地表竖向力要求弹性介质和零埋深。")
    if c.get("boundary", "stacey") not in ("stacey", "cpml"):
        raise ValueError("未知吸收边界。")
    t0=c.get("source_t0_s",0.)
    if not isinstance(t0,(int,float)) or not math.isfinite(t0) or t0<0:
        raise ValueError("源预移须为非负有限秒数，0表示求解器自动。")
    damping=c.get("damping_change_factor_elastic",1.)
    if type(damping) not in (int,float) or not math.isfinite(damping) or damping<=0:
        raise ValueError("弹性PML阻尼因子须为正有限数。")
    alpha=c.get("cpml_alpha_factor",1.)
    if type(alpha) not in (int,float) or not math.isfinite(alpha) or alpha<=0 or not math.isfinite(alpha*math.pi*c["f0_hz"]):
        raise ValueError("CPML频移系数须为正有限数。")
    if c.get("boundary")=="cpml" and alpha!=1 and acoustic:
        raise ValueError("显式CPML频移目前仅支持弹性单炮候选后端。")
    pml=c.get("pml_elements",12)
    if type(pml) is not int or pml<1:
        raise ValueError("PML厚度须为正整数个谱元。")
    if c.get("boundary") == "cpml":
        nx=math.ceil(c["width_m"]/c["element_m"]) if c["element_m"]>0 else 0
        nz=counts or np.ceil(layers[:,0]/max(c["element_m"],1e-30)).astype(int)
        if nx<=2*pml or nz[-1]<=pml:
            raise ValueError("PML厚度超过有效网格范围，底层及两侧须保留内部区域。")
        pad=pml*c["width_m"]/nx
        if np.any((np.r_[c["shot_x_m"],stations(c)]<=pad)|(np.r_[c["shot_x_m"],stations(c)]>=c["width_m"]-pad)):
            raise ValueError("炮点或接收点进入侧面PML，请扩大模型或调整PML厚度。")
    if c.get("physics", "elastic") not in ("acoustic","elastic","coupled"):
        raise ValueError("未知的介质类型。")
    if not np.isfinite(layers).all() or (layers[:,[0,1,3]] <= 0).any():
        raise ValueError("层厚、Vp 和密度须为正值。")
    if acoustic and np.any(layers[:,2] != 0):
        raise ValueError("声学介质的 Vs 应为 0。")
    if not acoustic and not coupled and ((layers[:,2] <= 0).any() or (kind(c)=="isotropic" and (layers[:,1] <= np.sqrt(4/3)*layers[:,2]).any())):
        raise ValueError("弹性模型要求 Vs > 0 且 Vp² > 4 Vs² / 3。")
    if coupled:
        if (c.get('domain_kind')!='solid_fluid_layers' or len(layers)!=3 or
                not (layers[0,2]>0 and layers[1,2]==0 and layers[2,2]>0) or
                kind(c)!='isotropic' or attenuating(c) or c['surface'] or c.get('interface_relief') or
                c.get('boundary')!='stacey'):
            raise ValueError('固—流体—固入口要求三个水平无损ISO层：固体/流体(Vs=0)/固体，左右及底部Stacey；不替换材料或边界。')
        if np.any(layers[[0,2],1]**2 <= 4*layers[[0,2],2]**2/3):
            raise ValueError('固体层要求 Vp² > 4 Vs²/3。')
        if max(c['shot_depth_m'],c['receiver_depth_m'])>=layers[0,0]:
            raise ValueError('此入口的震源及接收点必须位于上覆固体中。')
    if min(c[k] for k in ("width_m", "spacing_m", "element_m", "dt_s", "f0_hz")) <= 0:
        raise ValueError("宽度、道距、单元尺寸、时间步长和主频须大于零。")
    first, last = stations(c)[[0, -1]]
    if not (0 < c["shot_x_m"] < c["width_m"] and 0 < first < last < c["width_m"]):
        raise ValueError(f"炮点和检波点应在模型侧边界以内，当前末道 x = {last:g} m。")
    if min(c["shot_depth_m"], c["receiver_depth_m"]) < 0:
        raise ValueError("炮点和检波器埋深必须非负。")
    if c["surface"]:
        pts = np.asarray(c["surface"], dtype=float)
        if pts.ndim != 2 or pts.shape[1] != 2 or len(pts)<2 or not np.isfinite(pts).all():
            raise ValueError("地形需要至少两个有限的 x,z 坐标。")
        if pts[0,0] != 0 or pts[-1,0] != c["width_m"] or (np.diff(pts[:,0]) <= 0).any():
            raise ValueError("地形 x 必须严格递增，从 0 到模型宽度。")
    if c.get("interface_relief"):
        relief = np.array(c["interface_relief"])
        if not c["surface"] or relief.shape != (len(layers), len(c["surface"])) or not np.isfinite(relief).all():
            raise ValueError("界面起伏和层数或地形控制点不一致。")
        offsets = np.vstack((np.zeros(len(pts)),relief))
        for i,thickness in enumerate(layers[:,0]):
            # Differences of the two clamped splines use the same knots and edge slopes.
            local = thickness + offsets[i] - offsets[i+1]
            profile = interface_spline(pts[:,0],local)
            extrema = profile.derivative().roots(extrapolate=False)
            if min(local)<=0 or np.any(profile(extrema[np.isfinite(extrema)])<=0):
                raise ValueError(f"第 {i+1} 层顶底相交，请检查控制点之间的层厚。")
    validate_materials(c)
    domain=c.get('domain_kind','halfspace')
    if domain not in ('halfspace','free_plate','solid_fluid_layers'):
        raise ValueError('未知模型类型；请选择分层半空间或水平自由板。')
    if domain=='free_plate' and (acoustic or kind(c)!='isotropic' or attenuating(c) or len(layers)!=1
            or c['surface'] or c.get('interface_relief') or c.get('boundary')!='stacey'):
        raise ValueError('水平自由板当前仅支持平地、单层均匀无损ISO；上下无牵引，左右Stacey。')
    if domain=='solid_fluid_layers' and not coupled:raise ValueError('固—流体—固模型必须使用 coupled 声弹耦合方程。')
    for xs, burial in [(c["shot_x_m"], c["shot_depth_m"]), (stations(c),c["receiver_depth_m"])]:
        if np.any(elevation(c,xs)-burial <= elevation(c,xs,len(layers))):
            raise ValueError("炮点或接收点超过当地模型底界。")
        if c.get('boundary')=='cpml':
            bottom=elevation(c,xs,len(layers))
            inner=bottom+(elevation(c,xs,len(layers)-1)-bottom)*pml/nz[-1]
            if np.any(elevation(c,xs)-burial<=inner):
                raise ValueError("炮点或接收点进入底部PML，请调整埋深、模型深度或PML厚度。")


def replace_values(text, fields):
    for key, value in fields.items():
        text, count = re.subn(r"(?m)^(" + re.escape(key) + r"[ \t]*=[ \t]*)[^#\n]*",
                              lambda m: m[1] + str(value) + " ", text)
        if not count:
            raise ValueError(f"模板缺少 {key}")
    return text


def write_case(c, directory):
    validate(c)
    directory = Path(directory)
    data = directory / "DATA"
    data.mkdir(parents=True)
    (directory/"model.json").write_text(json.dumps(c, ensure_ascii=False, indent=2))
    layers = np.array(c["layers"])
    width = c["width_m"]
    nx = max(1, math.ceil(width / c["element_m"]))
    nz = np.array(c["elements_per_layer"],dtype=int) if c.get("elements_per_layer") else np.ceil(layers[:,0]/c["element_m"]).astype(int)
    acoustic = c.get("physics") == "acoustic"
    par = replace_values((ROOT/"templates/Par_file").read_text(), {
        "title":"BGP acoustic pressure" if acoustic else "BGP elastic P-SV vertical force",
        "seismotype":4 if acoustic else 2,
        "nx":nx, "xmax":width, "DT":c["dt_s"], "NSTEP":c["nstep"],
        "nbmodels":len(layers), "nbregions":len(layers), "nrec":c["receivers"],
        "NTSTEP_BETWEEN_OUTPUT_SAMPLE":c["sample_stride"],
        "NTSTEP_BETWEEN_OUTPUT_SEISMOS":math.ceil(c["nstep"]/c["sample_stride"])*c["sample_stride"],
        "output_wavefield_dumps":".true.", "use_binary_for_wavefield_dumps":".true.",
        "imagetype_wavefield_dumps":4 if acoustic else 2,
        "NTSTEP_BETWEEN_OUTPUT_IMAGES":snapshot_interval(c)})
    par=replace_values(par,{
        "PML_BOUNDARY_CONDITIONS":".true." if c.get("boundary")=="cpml" else ".false.",
        "STACEY_ABSORBING_CONDITIONS":".false." if c.get("boundary")=="cpml" else ".true.",
        "NELEM_PML_THICKNESS":c.get("pml_elements",12), "USER_T0":c.get("source_t0_s",0.)})
    if "damping_change_factor_elastic" in c:
        par=replace_values(par,{"damping_change_factor_elastic":c["damping_change_factor_elastic"]})
    if c.get('domain_kind')=='free_plate':
        par=replace_values(par,dict(absorbbottom='.false.',absorbtop='.false.',
                                   absorbleft='.true.',absorbright='.true.'))
    if c.get('physics')=='coupled':
        par=replace_values(par,dict(title='TopoWave solid-fluid-solid layered medium',seismotype='2,4',
            imagetype_wavefield_dumps=4,ATTENUATION_VISCOACOUSTIC='.false.',
            absorbbottom='.true.',absorbtop='.false.',absorbleft='.true.',absorbright='.true.'))
    if attenuating(c):
        att=c['attenuation']
        par=replace_values(par,dict(ATTENUATION_VISCOELASTIC='.true.', N_SLS=att['n_sls'],
            ATTENUATION_f0_REFERENCE=c['f0_hz'],
            READ_VELOCITIES_AT_f0='.true.' if att['velocity_reference']=='source_f0' else '.false.'))
    materials, regions = material_rows(c), []
    bottom = 1
    for i in reversed(range(len(layers))):
        regions.append(f"1 {nx} {bottom} {bottom+nz[i]-1} {i+1}")
        bottom += nz[i]
    blocks = list(re.finditer(r"(?m)^[0-9][^\n]*(?:\n[0-9][^\n]*)*", par))
    if len(blocks) != 2:
        raise ValueError("材料和网格区域模板结构有变。")
    for block, rows in zip(reversed(blocks), (regions, materials)):
        par = par[:block.start()] + "\n".join(rows) + par[block.end():]
    (data/"Par_file").write_text(par)
    if c.get("boundary")=="cpml" and c.get("cpml_alpha_factor",1.)!=1:
        (data/"CPML_ALPHA_MAX").write_text(format(c["cpml_alpha_factor"]*math.pi*c["f0_hz"],".17g")+"\n")
        (directory/"CPML_BACKEND_REQUIRED.txt").write_text("此输入含显式CPML频移，必须使用支持DATA/CPML_ALPHA_MAX的后端。原版SPECFEM2D不会读取此覆盖文件；请核对求解器日志中的BGP explicit CPML alpha_max。该配置是候选，不是通用稳定性认证。\n")
    surface = np.array(c["surface"] or [[0,0], [width,0]])
    depths = np.r_[0, np.cumsum(layers[:,0])]
    lines = [str(len(depths))]
    for index in reversed(range(len(depths))):
        lines.append(str(len(surface)))
        lines.extend(f"{x:.12g} {z:.12g}" for x,z in zip(surface[:,0],elevation(c,surface[:,0],index)))
    (data/"topography.dat").write_text("\n".join(lines+[str(n) for n in nz[::-1]])+"\n")
    sx = c["shot_x_m"]
    src = replace_values((ROOT/"templates/SOURCE").read_text(), {
        "xs":sx, "zs":float(elevation(c,sx))-c["shot_depth_m"], "f0":c["f0_hz"],
        "time_function_type":3 if acoustic else 1, "source_type":1, "anglesource":0,
        "source_surf":".true." if c.get("source_mode")=="surface_force" else ".false.",
        "factor":-1 if c.get("source_mode")=="surface_force" else 1})
    (data/"SOURCE").write_text(src)
    xs = stations(c)
    zs = elevation(c,xs) - c["receiver_depth_m"]
    (data/"STATIONS").write_text("".join(f"R{i:04d} AA {x:.12g} {z:.12g} 0 0\n"
                                           for i,(x,z) in enumerate(zip(xs,zs),1)))
    if c.get('physics')=='coupled':
        # Air is retained across the full width; this is a pressure receiver, not a void.
        z=-(c['layers'][0][0]+c['layers'][1][0]/2)
        with (data/'STATIONS').open('a') as f:f.write(f'F0001 FL {(xs[0]+xs[-1])/2:.12g} {z:.12g} 0 0\n')
