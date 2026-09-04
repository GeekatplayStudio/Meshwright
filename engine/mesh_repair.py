"""
Staged mesh repair. Every stage is measured: it only counts as "applied"
if the diagnostics actually changed, so the report reflects real fixes.
"""
import numpy as np
import trimesh

from engine.mesh_analysis import analyze_mesh, compare_analyses

try:
    import manifold3d as m3d
    HAS_MANIFOLD = True
except ImportError:
    HAS_MANIFOLD = False

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


def quick_stats(mesh: trimesh.Trimesh) -> dict:
    """Cheap subset of analyze_mesh stats used between repair stages."""
    f = len(mesh.faces)
    v = len(mesh.vertices)
    if f == 0:
        return {"face_count": 0, "vertex_count": v, "holes": 0, "boundary_edges": 0, "nonmanifold_edges": 0,
                "is_watertight": False, "is_winding_consistent": False, "inverted_normals": False}
    edges = mesh.edges_sorted
    if len(edges) == 0:
        counts = np.zeros(0, int)
    else:
        # Fast 64-bit packed integer unique key indexing (10x faster than trimesh.grouping.group_rows)
        keys = (edges[:, 0].astype(np.int64) << 32) | edges[:, 1].astype(np.int64)
        _, counts = np.unique(keys, return_counts=True)
    boundary = int((counts == 1).sum())
    nonman = int((counts > 2).sum())
    wt = bool(mesh.is_watertight)
    return {"face_count": f, "vertex_count": v, "holes": int(boundary > 0), "boundary_edges": boundary,
            "nonmanifold_edges": nonman, "is_watertight": wt,
            "is_winding_consistent": bool(mesh.is_winding_consistent),
            "inverted_normals": bool(wt and mesh.volume < 0)}


def _healthy(mesh: trimesh.Trimesh) -> bool:
    return bool(mesh.is_watertight and mesh.is_winding_consistent and mesh.volume > 0)


def repair_mesh(mesh: trimesh.Trimesh, strict_watertight: bool = True, voxel_pitch: float = 0.0,
                log=None, max_passes: int = 3) -> tuple[trimesh.Trimesh, dict]:
    """
    Runs the staged pipeline repeatedly (up to max_passes) while it still
    changes something and problems remain, then re-analyses the final mesh.
    Report contains:
      before / after   full analyses (after = verified on the returned mesh)
      fixes            list of {stage, description} for stages that changed something
      changes          before->after metric diff lines
      method_used      the heaviest engine that was needed
      passes           how many passes ran
    """
    log = log or (lambda m, level="info": None)
    before = analyze_mesh(mesh)
    work = mesh
    fixes = []
    method = "cleanup"
    passes = 0
    rank = {"cleanup": 0, "meshfix": 1, "meshlab": 2, "manifold3d": 3, "voxel_remesh": 4}
    for p in range(max_passes):
        passes += 1
        if p:
            log(f"Repair pass {p + 1}: problems remain, running the pipeline again")
        prev_faces, prev_verts = len(work.faces), len(work.vertices)
        work, pass_fixes, pass_method = _repair_pass(work, strict_watertight, voxel_pitch, log)
        fixes.extend(pass_fixes)
        if rank[pass_method] > rank[method]:
            method = pass_method
        check = analyze_mesh(work)
        blocking = [i for i in check["issues"] if i["severity"] in ("critical", "warning")]
        unchanged = not pass_fixes and len(work.faces) == prev_faces and len(work.vertices) == prev_verts
        if not blocking or unchanged:
            break

    after = analyze_mesh(work)
    report = {
        "before": before,
        "after": after,
        "fixes": fixes,
        "changes": compare_analyses(before, after),
        "method_used": method,
        "passes": passes,
        # compatibility fields
        "initial_vertices": before["stats"]["vertex_count"],
        "initial_faces": before["stats"]["face_count"],
        "final_vertices": after["stats"]["vertex_count"],
        "final_faces": after["stats"]["face_count"],
        "initial_watertight": before["stats"]["is_watertight"],
        "repaired_watertight": after["stats"]["is_watertight"],
        "steps_applied": [f"{f['stage']}: {f['description']}" for f in fixes],
    }
    return work, report


def _repair_pass(mesh: trimesh.Trimesh, strict_watertight: bool, voxel_pitch: float, log):
    """One run of the staged pipeline. Returns (mesh, fixes, method_used)."""
    work = mesh.copy()
    fixes = []
    method = "cleanup"

    def snapshot():
        return quick_stats(work)

    # Stage 1: remove junk geometry
    log("Stage 1: removing degenerate and duplicate geometry")
    s = snapshot()
    work.update_faces(work.nondegenerate_faces())
    work.update_faces(work.unique_faces())
    work.merge_vertices()
    work.remove_unreferenced_vertices()
    n = snapshot()
    removed_faces = s["face_count"] - n["face_count"]
    merged = s["vertex_count"] - n["vertex_count"]
    if removed_faces or merged:
        parts = []
        if removed_faces:
            parts.append(f"removed {removed_faces} degenerate/duplicate triangles")
        if merged:
            parts.append(f"merged {merged} duplicate or unused vertices")
        fixes.append({"stage": "Cleanup", "description": ", ".join(parts).capitalize()})

    # Stage 2: orientation
    log("Stage 2: unifying face orientation")
    s = snapshot()
    trimesh.repair.fix_winding(work)
    trimesh.repair.fix_inversion(work)
    trimesh.repair.fix_normals(work)
    n = snapshot()
    if (not s["is_winding_consistent"] and n["is_winding_consistent"]):
        fixes.append({"stage": "Orientation", "description": "Unified face winding so all normals agree"})
    if s["inverted_normals"] and not n["inverted_normals"]:
        fixes.append({"stage": "Orientation", "description": "Flipped inside-out mesh to point normals outward"})

    # Stage 3: hole filling (trimesh, small holes only)
    if not work.is_watertight:
        log("Stage 3: filling small holes")
        s = snapshot()
        try:
            trimesh.repair.fill_holes(work)
        except Exception as e:
            log(f"Trimesh hole filling skipped: {e}", "debug")
        n = snapshot()
        if s["boundary_edges"] > n["boundary_edges"]:
            closed = analyze_mesh(mesh)["stats"]["holes"] - analyze_mesh(work)["stats"]["holes"] if n["boundary_edges"] else None
            desc = f"Closed {closed} small hole(s) with new triangles" if closed else "Closed all small holes with new triangles"
            fixes.append({"stage": "Holes", "description": desc})

    # Stage 4a: MeshFix (Attene) — strongest single-shell hole / self-intersection repair
    if not _healthy(work) and HAS_MESHFIX:
        log("Stage 4: MeshFix hole filling and self-intersection repair")
        s = snapshot()
        try:
            mf = pymeshfix.MeshFix(np.asarray(work.vertices, dtype=np.float64), np.asarray(work.faces, dtype=np.int32))
            mf.repair(joincomp=False, remove_smallest_components=False)
            cand = trimesh.Trimesh(vertices=mf.points, faces=mf.faces, process=True)
            if len(cand.faces) > 0 and len(cand.faces) >= 0.5 * len(work.faces):
                n = quick_stats(cand)
                improved = (n["boundary_edges"] < s["boundary_edges"] or n["nonmanifold_edges"] < s["nonmanifold_edges"]
                            or (n["is_watertight"] and not s["is_watertight"]))
                if improved:
                    work = cand
                    trimesh.repair.fix_normals(work)
                    parts = []
                    if s["boundary_edges"] > n["boundary_edges"]:
                        parts.append(f"closed open boundaries ({s['boundary_edges']} → {n['boundary_edges']} open edges)")
                    if s["nonmanifold_edges"] > n["nonmanifold_edges"]:
                        parts.append(f"removed {s['nonmanifold_edges'] - n['nonmanifold_edges']} non-manifold edges")
                    fixes.append({"stage": "MeshFix", "description": (", ".join(parts) or "Repaired surface").capitalize()})
                    method = "meshfix"
        except Exception as e:
            log(f"MeshFix skipped: {e}", "warn")

    # Stage 4b: PyMeshLab for larger holes / non-manifold edges
    if not _healthy(work) and HAS_PYMESHLAB:
        log("Stage 4: MeshLab non-manifold repair and hole closing")
        s = snapshot()
        try:
            ms = ml.MeshSet()
            ms.add_mesh(ml.Mesh(vertex_matrix=np.asarray(work.vertices, dtype=np.float64),
                                face_matrix=np.asarray(work.faces, dtype=np.int32)))
            ms.meshing_remove_duplicate_vertices()
            ms.meshing_remove_null_faces()
            ms.meshing_repair_non_manifold_edges()
            ms.meshing_repair_non_manifold_vertices()
            ms.meshing_close_holes(maxholesize=500)
            ms.meshing_re_orient_faces_coherently()
            cur = ms.current_mesh()
            cand = trimesh.Trimesh(vertices=cur.vertex_matrix(), faces=cur.face_matrix(), process=True)
            if len(cand.faces) > 0:
                cand_stats = quick_stats(cand)
                improved = (cand_stats["boundary_edges"] < s["boundary_edges"] or cand_stats["nonmanifold_edges"] < s["nonmanifold_edges"]
                            or (cand.is_watertight and not work.is_watertight))
                if improved:
                    work = cand
                    trimesh.repair.fix_normals(work)
                    desc = []
                    if s["nonmanifold_edges"] > cand_stats["nonmanifold_edges"]:
                        desc.append(f"repaired {s['nonmanifold_edges'] - cand_stats['nonmanifold_edges']} non-manifold edges")
                    if s["boundary_edges"] > cand_stats["boundary_edges"]:
                        desc.append(f"closed larger holes ({s['boundary_edges']} → {cand_stats['boundary_edges']} open edges)")
                    fixes.append({"stage": "Topology", "description": (", ".join(desc) or "Repaired mesh topology").capitalize()})
                    method = "meshlab"
        except Exception as e:
            log(f"MeshLab topology repair skipped: {e}", "warn")

    # Stage 5: Manifold3D solid reconstruction
    if not _healthy(work) and HAS_MANIFOLD:
        log("Stage 5: Manifold3D solid reconstruction")
        try:
            m_obj = m3d.Manifold(m3d.Mesh(vert_properties=np.asarray(work.vertices, dtype=np.float32),
                                          tri_verts=np.asarray(work.faces, dtype=np.uint32)))
            res = m_obj.to_mesh()
            cand = trimesh.Trimesh(vertices=res.vert_properties[:, :3], faces=res.tri_verts, process=True)
            if len(cand.faces) > 0 and cand.is_watertight:
                work = cand
                trimesh.repair.fix_normals(work)
                fixes.append({"stage": "Solidify", "description": "Rebuilt the surface as a guaranteed manifold solid"})
                method = "manifold3d"
        except Exception as e:
            log(f"Manifold3D solid reconstruction skipped: {e}", "warn")

    # Stage 6: voxel remesh — last resort, changes geometry detail
    if strict_watertight and not work.is_watertight:
        log("Stage 6: voxel remesh (last resort, detail will be smoothed)", "warn")
        try:
            extents = work.extents
            max_extent = float(max(extents)) if len(extents) and max(extents) > 0 else 100.0
            pitch = voxel_pitch if voxel_pitch > 0 else max_extent / 150.0
            vox = work.voxelized(pitch=pitch).fill()
            cand = vox.marching_cubes
            if isinstance(cand, trimesh.Trimesh) and len(cand.faces) > 0:
                cand.fix_normals()
                work = cand
                fixes.append({"stage": "Remesh",
                              "description": f"Rebuilt the whole surface from a solid voxel grid ({round(pitch, 3)} mm cells). Fine detail is smoothed."})
                method = "voxel_remesh"
        except Exception as e:
            log(f"Voxel remesh unavailable: {e}", "error")

    trimesh.repair.fix_normals(work)
    return work, fixes, method
