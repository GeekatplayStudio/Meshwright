"""
Mesh diagnostics: measures every print-relevant property of a mesh and
turns it into a list of concrete issues plus a readiness score.
"""
import os

import numpy as np
import trimesh
from trimesh.grouping import group_rows

from engine.indexing import duplicate_mask, quantise, unique_rows

MAX_LOCATION_POINTS = 1500


def _boundary_loops(mesh: trimesh.Trimesh, boundary_idx=None) -> int:
    """
    Count closed loops of boundary (open) edges = number of holes.

    `boundary_idx` is the caller's own list of open-edge rows. Passing it in avoids
    a second grouping pass over every edge in the mesh, which on a five-million-face
    model is a second or so of work for an answer already in hand.
    """
    edges = mesh.edges_sorted
    if len(edges) == 0:
        return 0
    groups = group_rows(edges, require_count=1) if boundary_idx is None else boundary_idx
    if len(groups) == 0:
        return 0
    boundary = edges[groups]
    # Build adjacency among boundary vertices and count connected components
    import scipy.sparse as sp
    from scipy.sparse.csgraph import connected_components
    n = len(mesh.vertices)
    data = np.ones(len(boundary))
    adj = sp.coo_matrix((data, (boundary[:, 0], boundary[:, 1])), shape=(n, n))
    used = np.unique(boundary)
    comps, labels = connected_components(adj, directed=False)
    return len(np.unique(labels[used]))


def analyze_mesh(mesh: trimesh.Trimesh, file_path: str = "") -> dict:
    """
    Returns a full diagnostic report:
      stats   - numeric properties
      issues  - list of {id, severity, title, detail, count}
      score   - 0..100 print readiness
      verdict - short human summary
    """
    v_count = len(mesh.vertices)
    f_count = len(mesh.faces)

    if f_count == 0:
        return {
            "stats": {"filename": os.path.basename(file_path), "vertex_count": v_count, "face_count": 0},
            "issues": [{"id": "empty", "severity": "critical", "title": "No geometry",
                        "detail": "The file contains no triangles.", "count": 0}],
            "score": 0,
            "verdict": "Nothing to print",
        }

    is_watertight = bool(mesh.is_watertight)
    is_winding = bool(mesh.is_winding_consistent)
    is_volume = bool(mesh.is_volume)

    # Edge topology. One pass over the edge rows answers all of it: an edge shared
    # by one face is a boundary, by more than two a non-manifold junction.
    edges = mesh.edges_sorted
    first_edge, edge_of_row, edge_counts = unique_rows(edges)
    boundary_idx = (np.flatnonzero(edge_counts[edge_of_row] == 1)
                    if len(edges) else np.array([], dtype=int))
    boundary_edges = len(boundary_idx)
    nonmanifold_rows = first_edge[edge_counts > 2] if len(edges) else np.array([], dtype=int)
    nonmanifold_edges = len(nonmanifold_rows)
    nm_verts = np.unique(edges[nonmanifold_rows]) if nonmanifold_edges else np.array([], dtype=int)
    boundary_verts = np.unique(edges[boundary_idx]) if boundary_edges else np.array([], dtype=int)
    holes = _boundary_loops(mesh, boundary_idx) if boundary_edges else 0

    # Face quality
    degen_mask = ~mesh.nondegenerate_faces()
    degenerate = int(degen_mask.sum())
    # The masks are kept, not just the totals: the issue list points at where the
    # duplicates actually are so the user can find them in the viewport.
    dup_face_mask = duplicate_mask(np.sort(mesh.faces, axis=1))
    duplicate_faces = int(dup_face_mask.sum())

    # Vertices are compared to six decimals, so quantise and compare exactly.
    rounded = quantise(mesh.vertices, 6)
    if rounded is None:                       # coordinates too large to quantise
        _, first_v = np.unique(np.round(mesh.vertices, 6), axis=0, return_index=True)
        dup_vert_mask = np.ones(v_count, dtype=bool)
        dup_vert_mask[first_v] = False
    else:
        dup_vert_mask = duplicate_mask(rounded)
    dup_verts = int(dup_vert_mask.sum())
    referenced = np.unique(mesh.faces)
    unref_mask = np.ones(v_count, dtype=bool)
    unref_mask[referenced] = False
    unreferenced = int(unref_mask.sum())

    # Bodies / orientation
    try:
        bodies = int(mesh.body_count)
    except Exception:
        bodies = 1
    inverted = bool(is_watertight and mesh.volume < 0)

    # Thin triangles (sliver) — aspect check by min-angle
    try:
        angles = mesh.face_angles
        sliver_mask = angles.min(axis=1) < np.radians(1.0)
        slivers = int(sliver_mask.sum())
    except Exception:
        sliver_mask = np.zeros(f_count, dtype=bool)
        slivers = 0

    extents = mesh.extents.tolist() if mesh.extents is not None else [0, 0, 0]
    bounds = mesh.bounds.tolist() if mesh.bounds is not None else [[0, 0, 0], [0, 0, 0]]
    volume_mm3 = abs(float(mesh.volume)) if is_watertight else 0.0
    area_mm2 = float(mesh.area)
    max_dim = max(extents) if extents else 0.0
    euler = int(mesh.euler_number) if is_watertight else None
    genus = int((2 - euler) / 2) if euler is not None and bodies == 1 else None

    stats = {
        "filename": os.path.basename(file_path) if file_path else "model",
        "file_path": file_path,
        "vertex_count": v_count,
        "face_count": f_count,
        "edge_count": len(mesh.edges_unique),
        "body_count": bodies,
        "is_watertight": is_watertight,
        "is_winding_consistent": is_winding,
        "is_manifold": is_volume,
        "boundary_edges": boundary_edges,
        "holes": holes,
        "nonmanifold_edges": nonmanifold_edges,
        "degenerate_faces": degenerate,
        "duplicate_faces": duplicate_faces,
        "duplicate_vertices": dup_verts,
        "unreferenced_vertices": unreferenced,
        "sliver_faces": slivers,
        "inverted_normals": inverted,
        "euler_number": euler,
        "genus": genus,
        "dimensions_mm": [round(x, 2) for x in extents],
        "bounds_min": [round(x, 2) for x in bounds[0]],
        "bounds_max": [round(x, 2) for x in bounds[1]],
        "volume_cm3": round(volume_mm3 / 1000.0, 3),
        "surface_area_cm2": round(area_mm2 / 100.0, 2),
        "max_dimension_mm": round(max_dim, 2),
    }

    issues = []
    face_centers = mesh.triangles_center

    def locate(points):
        """Compact location payload: sampled points + centre + extent."""
        pts = np.asarray(points, dtype=float).reshape(-1, 3)
        if len(pts) == 0:
            return None
        total = len(pts)
        if total > MAX_LOCATION_POINTS:
            pts = pts[np.linspace(0, total - 1, MAX_LOCATION_POINTS).astype(int)]
        lo, hi = pts.min(axis=0), pts.max(axis=0)
        return {"points": np.round(pts, 3).tolist(), "total": int(total),
                "center": np.round((lo + hi) / 2, 3).tolist(), "extent": round(float(np.linalg.norm(hi - lo)), 3)}

    def add(iid, severity, title, detail, count=0, location=None):
        issues.append({"id": iid, "severity": severity, "title": title, "detail": detail,
                       "count": int(count), "location": location})

    if holes or boundary_edges:
        add("holes", "critical", f"{holes} open hole{'s' if holes != 1 else ''}",
            f"{boundary_edges} boundary edges are not shared by two faces. Slicers cannot tell inside from outside.", holes,
            locate(mesh.vertices[boundary_verts]))
    if nonmanifold_edges:
        add("nonmanifold", "critical", "Non-manifold edges",
            f"{nonmanifold_edges} edges are shared by three or more faces. These create ambiguous solids.", nonmanifold_edges,
            locate(mesh.vertices[nm_verts]))
    if not is_winding and f_count > 0:
        add("winding", "warning", "Inconsistent face winding",
            "Neighbouring triangles point in opposite directions; normals will flip across the surface.")
    if inverted:
        add("inverted", "warning", "Inside-out mesh",
            "All normals point inward. The slicer would treat the model as a cavity.")
    if degenerate:
        add("degenerate", "warning", "Degenerate triangles",
            f"{degenerate} zero-area triangles (collapsed or collinear points).", degenerate,
            locate(face_centers[degen_mask]))
    if duplicate_faces:
        add("dupfaces", "warning", "Duplicate faces",
            f"{duplicate_faces} triangles are exact copies of another triangle.", duplicate_faces,
            locate(face_centers[dup_face_mask]))
    if dup_verts:
        add("dupverts", "info", "Duplicate vertices",
            f"{dup_verts} vertices share a position with another vertex; edges may not be connected.", dup_verts,
            locate(mesh.vertices[dup_vert_mask]))
    if unreferenced:
        add("unref", "info", "Unused vertices",
            f"{unreferenced} vertices are not used by any face.", unreferenced,
            locate(mesh.vertices[unref_mask]))
    if slivers:
        add("slivers", "info", "Sliver triangles",
            f"{slivers} very thin triangles (min angle < 1°) which can produce slicing artifacts. Use “Fix slivers” to merge them into their larger neighbours.", slivers,
            locate(face_centers[sliver_mask]))
    if bodies > 1:
        add("bodies", "info", f"{bodies} separate shells",
            "The model consists of multiple disconnected pieces. Verify this is intended.", bodies)
    if max_dim > 0 and (max_dim < 1.0 or max_dim > 1000.0):
        add("scale", "info", "Unusual scale",
            f"Largest dimension is {round(max_dim, 2)} mm. The source file may use different units.")

    # Score
    score = 100
    for it in issues:
        score -= {"critical": 35, "warning": 12, "info": 3}[it["severity"]]
    score = max(0, min(100, score))
    if is_watertight and is_winding and not inverted:
        score = max(score, 80)

    if not issues:
        verdict = "Print ready"
    elif any(i["severity"] == "critical" for i in issues):
        verdict = "Repair required"
    elif any(i["severity"] == "warning" for i in issues):
        verdict = "Repair recommended"
    else:
        verdict = "Printable, minor cleanup available"

    return {"stats": stats, "issues": issues, "score": score, "verdict": verdict}


def compare_analyses(before: dict, after: dict) -> list:
    """Produce a list of human-readable change lines between two analyses."""
    b, a = before["stats"], after["stats"]
    lines = []

    def diff(key, label, good_when_zero=True):
        bv, av = b.get(key, 0) or 0, a.get(key, 0) or 0
        if bv != av:
            lines.append({"label": label, "before": bv, "after": av,
                          "improved": (av < bv) if good_when_zero else (av > bv)})

    diff("holes", "Open holes")
    diff("boundary_edges", "Boundary edges")
    diff("nonmanifold_edges", "Non-manifold edges")
    diff("degenerate_faces", "Degenerate faces")
    diff("duplicate_faces", "Duplicate faces")
    diff("duplicate_vertices", "Duplicate vertices")
    diff("unreferenced_vertices", "Unused vertices")
    diff("sliver_faces", "Sliver faces")
    if b.get("is_watertight") != a.get("is_watertight"):
        lines.append({"label": "Watertight", "before": b.get("is_watertight"), "after": a.get("is_watertight"),
                      "improved": bool(a.get("is_watertight"))})
    if b.get("is_winding_consistent") != a.get("is_winding_consistent"):
        lines.append({"label": "Consistent winding", "before": b.get("is_winding_consistent"),
                      "after": a.get("is_winding_consistent"), "improved": bool(a.get("is_winding_consistent"))})
    if b.get("inverted_normals") != a.get("inverted_normals"):
        lines.append({"label": "Inside-out", "before": b.get("inverted_normals"), "after": a.get("inverted_normals"),
                      "improved": not a.get("inverted_normals")})
    diff("face_count", "Faces", good_when_zero=False)
    diff("vertex_count", "Vertices", good_when_zero=False)
    return lines
