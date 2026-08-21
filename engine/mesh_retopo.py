"""
Smart retopology and aggressive reduction.

Three strategies, all targeting an absolute face count:

  quadriflow  — QuadriFlow (BSD-3; the remesher inside Blender) rebuilds the
                surface as a clean, evenly sized all-quad mesh aligned to the
                shape's curvature. Output is triangulated for STL. Best for
                organic / scanned / sculpted models and true "low-poly" looks.
  isotropic   — MeshLab isotropic explicit remeshing to uniform triangles of a
                computed edge length, then quadric collapse to the exact target.
                Best when QuadriFlow is unavailable or the mesh is very noisy.
  quadric     — MeshLab topology-preserving quadric edge collapse with quality
                and boundary constraints. Sharpest, keeps hard edges best.

`deviation()` measures how far the result strays from the source so the user
(and the safety log) can judge the trade-off.
"""
import numpy as np
import trimesh
from scipy.spatial import cKDTree

try:
    from pyQuadriFlow.pyQuadriFlow import pyquadriflow
    HAS_QUADRIFLOW = True
except Exception:
    HAS_QUADRIFLOW = False

try:
    import pymeshlab as ml
    HAS_PYMESHLAB = True
except ImportError:
    HAS_PYMESHLAB = False

try:
    import pymeshfix
    HAS_MESHFIX = True
except ImportError:
    HAS_MESHFIX = False


def _safe_log(log):
    """A failing logger must never abort a mesh operation."""
    inner = log or (lambda m, level="info": None)

    def safe(message, level="info"):
        try:
            inner(message, level)
        except Exception:
            pass
    return safe


def deviation(source: trimesh.Trimesh, result: trimesh.Trimesh, samples: int = 40000) -> dict:
    """Surface distance result→source (mm) from surface samples. Cheap and symmetric enough for reporting."""
    if len(source.faces) == 0 or len(result.faces) == 0:
        return {"max_mm": 0.0, "mean_mm": 0.0, "relative_pct": 0.0}
    src = source.sample(samples)
    res = result.sample(min(samples, max(2000, len(result.faces) * 4)))
    d, _ = cKDTree(src).query(res)
    size = float(max(source.extents)) or 1.0
    return {"max_mm": round(float(d.max()), 4), "mean_mm": round(float(d.mean()), 4),
            "relative_pct": round(100.0 * float(d.max()) / size, 3)}


def _triangulate_quads(v: np.ndarray, quads: np.ndarray) -> trimesh.Trimesh:
    """Split each quad along its shorter diagonal."""
    q = np.asarray(quads)
    d02 = np.linalg.norm(v[q[:, 0]] - v[q[:, 2]], axis=1)
    d13 = np.linalg.norm(v[q[:, 1]] - v[q[:, 3]], axis=1)
    use02 = d02 <= d13
    t1 = np.where(use02[:, None], q[:, [0, 1, 2]], q[:, [0, 1, 3]])
    t2 = np.where(use02[:, None], q[:, [0, 2, 3]], q[:, [1, 2, 3]])
    return trimesh.Trimesh(vertices=v, faces=np.vstack([t1, t2]), process=True)


def _closed_copy(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """QuadriFlow needs a manifold surface; close holes first if we can."""
    if mesh.is_watertight or not HAS_MESHFIX:
        return mesh
    try:
        mf = pymeshfix.MeshFix(np.asarray(mesh.vertices, dtype=np.float64), np.asarray(mesh.faces, dtype=np.int32))
        mf.repair(joincomp=False, remove_smallest_components=False)
        fixed = trimesh.Trimesh(mf.points, mf.faces, process=True)
        return fixed if len(fixed.faces) else mesh
    except Exception:
        return mesh


def _quadriflow_one(mesh: trimesh.Trimesh, target_faces: int, preserve_sharp: bool, adaptive: bool, seed: int,
                    log=None) -> trimesh.Trimesh | None:
    log = log or (lambda m, level="info": None)
    src = _closed_copy(mesh)
    if len(src.faces) > PRE_DECIMATE_ABOVE:
        pre = _quadric(src, max(PRE_DECIMATE_TO, target_faces * 4))
        if pre is not None and len(pre.faces) > target_faces:
            log(f"Pre-decimated {len(src.faces):,} → {len(pre.faces):,} faces for the quad field")
            src = pre
    quads = max(20, int(target_faces // 2))     # each quad becomes 2 triangles
    res = pyquadriflow(quads, int(seed), np.asarray(src.vertices, dtype=float).tolist(),
                       np.asarray(src.faces, dtype=int).tolist(),
                       bool(preserve_sharp), False, bool(adaptive), False, False)
    v = np.asarray(res.get("vertices", []), dtype=float)
    f = res.get("faces", [])
    if len(v) == 0 or len(f) == 0:
        return None
    f = np.asarray(f)
    if f.ndim != 2 or f.shape[1] != 4:
        return None
    out = _triangulate_quads(v, f)
    out.fix_normals()
    return out


def _isotropic_then_collapse(mesh: trimesh.Trimesh, target_faces: int) -> trimesh.Trimesh | None:
    if not HAS_PYMESHLAB:
        return None
    ms = ml.MeshSet()
    ms.add_mesh(ml.Mesh(vertex_matrix=np.asarray(mesh.vertices, dtype=np.float64),
                        face_matrix=np.asarray(mesh.faces, dtype=np.int32)))
    # uniform triangles: area ≈ (√3/4)·L²  →  L = sqrt(4A / (√3 · n))
    area = float(mesh.area)
    edge = float(np.sqrt(4.0 * area / (np.sqrt(3.0) * max(target_faces, 4))))
    ms.meshing_isotropic_explicit_remeshing(targetlen=ml.PureValue(edge), iterations=6, adaptive=False)
    if ms.current_mesh().face_number() > target_faces * 1.05:
        ms.meshing_decimation_quadric_edge_collapse(targetfacenum=int(target_faces), preservenormal=True,
                                                    preservetopology=True, preserveboundary=True, planarquadric=True)
    cur = ms.current_mesh()
    out = trimesh.Trimesh(cur.vertex_matrix(), cur.face_matrix(), process=True)
    out.fix_normals()
    return out if len(out.faces) else None


def _quadric(mesh: trimesh.Trimesh, target_faces: int) -> trimesh.Trimesh | None:
    if not HAS_PYMESHLAB:
        return None
    ms = ml.MeshSet()
    ms.add_mesh(ml.Mesh(vertex_matrix=np.asarray(mesh.vertices, dtype=np.float64),
                        face_matrix=np.asarray(mesh.faces, dtype=np.int32)))
    ms.meshing_decimation_quadric_edge_collapse(targetfacenum=int(target_faces), preservenormal=True,
                                                preservetopology=True, preserveboundary=True,
                                                planarquadric=True, qualitythr=0.5, optimalplacement=True)
    cur = ms.current_mesh()
    out = trimesh.Trimesh(cur.vertex_matrix(), cur.face_matrix(), process=True)
    out.fix_normals()
    return out if len(out.faces) else None


# QuadriFlow needs a reasonably dense, closed piece to produce a clean field.
# Small pieces are decimated instead — retopology of a 200-triangle gem only makes holes.
MIN_QUADRIFLOW_INPUT = 4000
MIN_QUADRIFLOW_TARGET = 400
# QuadriFlow does not need millions of input faces to build its field; feeding it a
# pre-decimated copy is much faster and no less accurate.
PRE_DECIMATE_ABOVE = 250000
PRE_DECIMATE_TO = 200000


def _meshfix(mesh: trimesh.Trimesh) -> trimesh.Trimesh | None:
    if not HAS_MESHFIX:
        return None
    try:
        mf = pymeshfix.MeshFix(np.asarray(mesh.vertices, dtype=np.float64), np.asarray(mesh.faces, dtype=np.int32))
        mf.repair(joincomp=False, remove_smallest_components=False)
        out = trimesh.Trimesh(mf.points, mf.faces, process=True)
        return out if len(out.faces) else None
    except Exception:
        return None


def _finalize(source: trimesh.Trimesh, cand: trimesh.Trimesh, log) -> trimesh.Trimesh | None:
    """
    Clean a retopology result and make sure it is no worse than the source.
    Remeshers can leave holes and non-manifold edges on topologically complex
    shapes; MeshFix closes those. A candidate that still cannot match a
    watertight source is rejected so the caller can try another engine.
    """
    out = _cleanup(cand)
    if not source.is_watertight:
        return out
    if out.is_watertight and out.body_count == 1:
        return out
    fixed = _meshfix(out)
    if fixed is not None:
        fixed = _cleanup(fixed)
        if fixed.is_watertight:
            log("Retopology left open edges; repaired with MeshFix")
            return fixed
    return None


def _cleanup(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Remove the small defects retopology can leave behind, without changing the shape."""
    m = mesh.copy()
    m.merge_vertices()
    m.update_faces(m.nondegenerate_faces())
    m.update_faces(m.unique_faces())
    m.remove_unreferenced_vertices()
    try:
        trimesh.repair.fill_holes(m)
    except Exception:
        pass
    trimesh.repair.fix_winding(m)
    m.fix_normals()
    return m


def retopologize(mesh: trimesh.Trimesh, target_faces: int, method: str = "quadriflow",
                 preserve_sharp: bool = True, adaptive: bool = True, seed: int = 0, log=None):
    """
    Returns (mesh, info). Works per connected shell so multi-part models keep their parts.
    Falls back quadriflow → isotropic → quadric when an engine is unavailable or fails.
    """
    log = _safe_log(log)
    target_faces = int(max(20, target_faces))
    initial = len(mesh.faces)
    if initial <= target_faces:
        return mesh.copy(), {"method_used": "none", "initial_faces": initial, "final_faces": initial,
                             "reason": "Already at or below the target face count."}

    parts = mesh.split(only_watertight=False) if mesh.body_count > 1 else [mesh]
    parts = [p for p in parts if len(p.faces) > 0]
    total = sum(len(p.faces) for p in parts)
    results, used = [], set()
    small = 0

    for i, part in enumerate(parts):
        share = max(20, int(round(target_faces * len(part.faces) / total)))
        if len(part.faces) <= share:
            results.append(part.copy())
            continue
        out = None
        order = {"quadriflow": ["quadriflow", "isotropic", "quadric"],
                 "isotropic": ["isotropic", "quadric"],
                 "quadric": ["quadric"]}.get(method, ["quadriflow", "isotropic", "quadric"])
        # Small pieces: skip QuadriFlow, it cannot build a clean field on them.
        if order[0] == "quadriflow" and (len(part.faces) < MIN_QUADRIFLOW_INPUT or share < MIN_QUADRIFLOW_TARGET):
            order = ["quadric", "isotropic"]
            small += 1
        for m in order:
            try:
                if m == "quadriflow" and HAS_QUADRIFLOW:
                    out = _quadriflow_one(part, share, preserve_sharp, adaptive, seed + i, log)
                elif m == "isotropic":
                    out = _isotropic_then_collapse(part, share)
                elif m == "quadric":
                    out = _quadric(part, share)
                if out is not None and len(out.faces) > 0:
                    out = _finalize(part, out, log)
                if out is not None and len(out.faces) > 0:
                    used.add(m)
                    break
                if m != order[-1]:
                    log(f"{m} could not match the source quality on piece {i + 1}; trying {order[order.index(m) + 1]}", "warn")
            except Exception as e:
                log(f"{m} failed on piece {i + 1}: {e}", "warn")
                out = None
        if out is None:
            log(f"Piece {i + 1} left unchanged (no engine succeeded)", "warn")
            out = part.copy()
        results.append(out)

    result = trimesh.util.concatenate(results) if len(results) > 1 else results[0]
    if small:
        log(f"{small} small piece(s) decimated instead of retopologised (too few triangles for a clean quad field)")
    result = _cleanup(result)
    dev = deviation(mesh, result)
    info = {
        "method_used": "+".join(sorted(used)) or "none",
        "initial_faces": initial,
        "final_faces": int(len(result.faces)),
        "target_faces": target_faces,
        "reduction_percentage": round((1.0 - len(result.faces) / float(initial)) * 100.0, 1),
        "deviation": dev,
        "pieces": len(parts),
        "small_pieces_decimated": small,
    }
    return result, info
