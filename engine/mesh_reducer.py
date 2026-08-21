import trimesh

try:
    import fast_simplification as fs
    HAS_FAST_SIMPLIFY = True
except ImportError:
    HAS_FAST_SIMPLIFY = False

try:
    import pymeshlab as ml
    HAS_PYMESHLAB = True
except ImportError:
    HAS_PYMESHLAB = False


def reduce_mesh(mesh: trimesh.Trimesh, target_factor: float = 0.5, target_faces: int = 0) -> tuple[trimesh.Trimesh, dict]:
    """
    Reduces the polygon count of the mesh using Quadric Edge Collapse decimation.
    - target_factor: float between 0.05 and 0.95 (e.g. 0.5 = keep 50% faces, remove 50%).
    - target_faces: int, if > 0 explicitly targets that face count.
    """
    initial_faces = len(mesh.faces)
    if initial_faces < 10:
        return mesh, {"reduced": False, "reason": "Mesh face count too low to reduce."}

    if target_faces > 0:
        target_count = max(10, min(initial_faces, target_faces))
        target_ratio = target_count / float(initial_faces)
    else:
        target_ratio = max(0.01, min(0.99, target_factor))
        target_count = max(10, int(initial_faces * target_ratio))

    reduced_mesh = None
    method_used = "none"

    def _nonmanifold(m):
        groups = trimesh.grouping.group_rows(m.edges_sorted)
        return int(sum(1 for g in groups if len(g) > 2))

    nm_before = _nonmanifold(mesh)

    # Strategy 1: fast_simplification (ultra-fast C++ library)
    if HAS_FAST_SIMPLIFY:
        try:
            # fast_simplification.simplify accepts target_reduction fraction (e.g. 0.5 = remove 50%)
            reduction_fraction = 1.0 - target_ratio
            sim_v, sim_f = fs.simplify(mesh.vertices, mesh.faces, target_reduction=reduction_fraction)
            if len(sim_f) > 0:
                cand = trimesh.Trimesh(vertices=sim_v, faces=sim_f, process=True)
                # fast_simplification can pinch the surface into non-manifold edges; if it does,
                # fall through to MeshLab's topology-preserving collapse instead.
                if _nonmanifold(cand) <= nm_before or not HAS_PYMESHLAB:
                    reduced_mesh = cand
                    method_used = "fast_simplification"
        except Exception:
            reduced_mesh = None

    # Strategy 2: PyMeshLab Quadric Edge Collapse (topology preserving)
    if reduced_mesh is None and HAS_PYMESHLAB:
        try:
            ms = ml.MeshSet()
            m_pml = ml.Mesh(vertex_matrix=mesh.vertices, face_matrix=mesh.faces)
            ms.add_mesh(m_pml)
            ms.meshing_decimation_quadric_edge_collapse(targetfacenum=target_count, preservenormal=True,
                                                        preservetopology=True, preserveboundary=True,
                                                        planarquadric=True)
            res_m = ms.current_mesh()
            reduced_mesh = trimesh.Trimesh(vertices=res_m.vertex_matrix(), faces=res_m.face_matrix(), process=True)
            method_used = "pymeshlab"
        except Exception:
            reduced_mesh = None

    # Strategy 3: Trimesh simplify_quadric_decimation fallback
    if reduced_mesh is None:
        try:
            reduced_mesh = mesh.simplify_quadric_decimation(target_count)
            method_used = "trimesh_quadric"
        except Exception:
            reduced_mesh = mesh.copy()
            method_used = "failed_fallback"

    reduced_mesh.fix_normals()

    info = {
        "initial_faces": initial_faces,
        "final_faces": len(reduced_mesh.faces),
        "initial_vertices": len(mesh.vertices),
        "final_vertices": len(reduced_mesh.vertices),
        "reduction_percentage": round((1.0 - (len(reduced_mesh.faces) / float(initial_faces))) * 100.0, 1),
        "method_used": method_used
    }

    return reduced_mesh, info
