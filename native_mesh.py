"""Geometry written by SPECFEM2D's xmeshfem2D in mesh_materials.vtk."""
from dataclasses import dataclass
import hashlib
import re

import numpy as np


@dataclass
class ElementMesh:
    points: np.ndarray
    cells: np.ndarray
    materials: np.ndarray
    vtk_sha256: str

    def __post_init__(self):
        if (self.points.ndim != 2 or self.points.shape[1] != 2 or
                not np.isfinite(self.points).all() or self.cells.ndim != 2 or
                self.cells.shape[1] not in (4, 9) or not len(self.cells) or
                not np.issubdtype(self.cells.dtype, np.integer) or
                self.cells.min() < 0 or self.cells.max() >= len(self.points) or
                self.materials.shape != (len(self.cells),) or
                not re.fullmatch(r"[0-9a-f]{64}", self.vtk_sha256)):
            raise ValueError("无效的 SPECFEM2D 网格坐标、单元连接或来源摘要。")

    @classmethod
    def read(cls, data):
        keys = ("mesh_points", "mesh_cells", "mesh_materials", "mesh_vtk_sha256")
        if not any(k in data for k in keys):
            return None
        if not all(k in data for k in keys):
            raise ValueError("记录中的谱元网格不完整。")
        return cls(data[keys[0]], data[keys[1]], data[keys[2]], str(data[keys[3]]))

    def arrays(self):
        return dict(mesh_points=self.points, mesh_cells=self.cells, mesh_materials=self.materials,
                    mesh_vtk_sha256=self.vtk_sha256)

    def edge_paths(self):
        # Canonicalize shared edges, retaining the native quadratic mid-edge node.
        corners = self.cells[:, :4]
        ends = np.sort(np.stack((corners, np.roll(corners,-1,axis=1)),axis=-1),axis=-1)
        if self.cells.shape[1] == 4:
            return self.points[np.unique(ends.reshape(-1,2),axis=0)]
        ids = np.concatenate((ends, self.cells[:,4:8,None]),axis=-1)
        a,b,m = self.points[np.unique(ids.reshape(-1,3),axis=0)].transpose(1,0,2)
        t = np.linspace(-1,1,5)[:,None]
        return a[:,None]*(t*(t-1)/2) + m[:,None]*(1-t*t) + b[:,None]*(t*(t+1)/2)


def read_material_vtk(path):
    raw = path.read_bytes()
    text = raw.decode("ascii")
    if not re.search(r"(?m)^ASCII\s*\nDATASET UNSTRUCTURED_GRID\s*$",text):
        raise ValueError("需要 SPECFEM2D 的 ASCII 非结构网格 VTK。")
    # Fortran wraps a single Q9 connectivity record across two physical lines.
    patterns = [r"^POINTS\s+(\d+)\s+float[ \t]*$", r"^CELLS\s+(\d+)\s+(\d+)[ \t]*$",
                r"^CELL_TYPES\s+(\d+)[ \t]*$", r"^CELL_DATA\s+(\d+)[ \t]*$",
                r"^LOOKUP_TABLE default[ \t]*$"]
    headers = [re.search(p,text,re.M) for p in patterns]
    if not all(headers) or [h.start() for h in headers] != sorted(h.start() for h in headers):
        raise ValueError("缺少 VTK 的坐标、单元或材料段。")
    pts, con, typ, dat, lut = headers
    n = int(con[1])
    if not n or n != int(typ[1]) or n != int(dat[1]) or int(con[2]) not in (5*n,10*n):
        raise ValueError("不支持的 VTK 单元数量或连接格式。")
    points = np.fromstring(text[pts.end():con.start()],sep=" ").reshape(int(pts[1]),3)
    cells = np.fromstring(text[con.end():typ.start()],dtype=np.int32,sep=" ").reshape(n,int(con[2])//n)
    types = np.fromstring(text[typ.end():dat.start()],dtype=np.int32,sep=" ")
    width = cells.shape[1]-1
    if (not np.all(cells[:,0]==width) or len(types)!=n or
            not np.all(types==28 if width==9 else np.isin(types,[8,9])) or not np.all(points[:,1]==0)):
        raise ValueError("VTK 需要 x-z 平面上的四节点或九节点四边形。")
    # SPECFEM NGNOD=4 writes VTK_PIXEL (LL, LR, UL, UR), not VTK_QUAD.
    # Normalize to perimeter order before any mesh edge/geometry operations.
    if width==4:
        pixel=types==8
        cells[pixel,1:]=cells[pixel,1:][:,[0,1,3,2]]
    materials = np.fromstring(text[lut.end():],dtype=np.int32,sep=" ")
    return ElementMesh(points[:,[0,2]], cells[:,1:], materials, hashlib.sha256(raw).hexdigest())


def draw_mesh(ax, mesh):
    from matplotlib.collections import LineCollection
    lines = LineCollection(mesh.edge_paths(),colors="#253944",linewidths=.4,alpha=.52,zorder=2)
    ax.add_collection(lines,autolim=False)
    return lines
