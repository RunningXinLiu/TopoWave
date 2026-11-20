"""Pack xmeshfem2D output without starting the wave solver."""
import json,hashlib
from pathlib import Path
import numpy as np
from native_mesh import read_material_vtk,ElementMesh
from mesh_quality import inspect_mesh
from region_result import case_hash


def pack(root,destination):
    root=Path(root);case=json.loads((root/'case.json').read_text())
    mesh=read_material_vtk(root/'OUTPUT_FILES/mesh_materials.vtk')
    qc=inspect_mesh(mesh)
    if qc['invalid_count']:raise ValueError('区域实际网格存在无效单元。')
    hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((root/'DATA').iterdir()) if p.is_file()}
    np.savez_compressed(destination,case=json.dumps(case),case_sha256=case_hash(case),
                        input_sha256=json.dumps(hashes),mesh_qc=json.dumps(qc),**mesh.arrays())


def load(path,record):
    with np.load(path,allow_pickle=False) as a:
        case=json.loads(str(a['case']));mesh=ElementMesh.read(a)
        if str(a['case_sha256'])!=case_hash(record['case']) or case_hash(case)!=case_hash(record['case']):raise ValueError('区域网格输入不匹配。')
        if json.loads(str(a['input_sha256']))!=record['input_sha256']:raise ValueError('区域网格DATA散列不匹配。')
        if mesh is None:raise ValueError('实际网格缺失。')
    return case,mesh


if __name__=='__main__':
    import sys
    pack(sys.argv[1],sys.argv[2])
