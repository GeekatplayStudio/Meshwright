"""
Sliver-triangle removal.

Two kinds of slivers exist:
  * needles — one very short edge. Removed by collapsing that edge: the vertex
    with fewer incident faces is merged into the better-connected one, so the
    larger surrounding geometry stays where it is.
  * caps — one angle close to 180°, no short edge. Removed by flipping the long
    edge shared with the neighbouring triangle.

Both operations can tear a mesh if applied blindly, so every candidate is
checked first:

  * an edge collapse is only performed when it satisfies the **link condition**
    — the two endpoints must share exactly the two vertices opposite the edge.
    Collapsing an edge whose endpoints share a third neighbour folds the surface
    onto itself and creates non-manifold edges or holes.
  * an edge flip is only performed when the replacement edge does not already
    exist, and both triangles are interior (their shared edge has exactly two
    faces).

Passes repeat until no slivers remain or nothing changes. If a pass still
manages to make the topology worse, it is discarded and the previous mesh kept.
"""
import numpy as np
import trimesh


def _sliver_mask(mesh: trimesh.Trimesh, min_angle_deg: float) -> np.ndarray:
    if len(mesh.faces) == 0:
        return np.zeros(0, dtype=bool)
    return mesh.face_angles.min(axis=1) < np.radians(min_angle_deg)


def _topology(mesh: trimesh.Trimesh) -> tuple[int, int]:
    """(boundary edges, non-manifold edges) — the two things we must not make worse."""
    edges = mesh.edges_sorted
    if len(edges) == 0:
        return 0, 0
    _, counts = np.unique(edges, axis=0, return_counts=True)
    return int((counts == 1).sum()), int((counts > 2).sum())


def _neighbours(mesh: trimesh.Trimesh, verts: np.ndarray) -> dict:
    """Adjacency sets, built only for the vertices we care about."""
    e = mesh.edges_unique
    wanted = np.zeros(len(mesh.vertices), dtype=bool)
    wanted[verts] = True
    keep = wanted[e[:, 0]] | wanted[e[:, 1]]
    adj = {int(v): set() for v in verts}
    for a, b in e[keep]:
        a, b = int(a), int(b)
        if a in adj:
            adj[a].add(b)
        if b in adj:
            adj[b].add(a)
    return adj


def _edge_faces(faces: np.ndarray) -> dict:
    """sorted edge -> list of face indices."""
    out = {}
    for fi, f in enumerate(faces):
        a, b, c = int(f[0]), int(f[1]), int(f[2])
        for u, v in ((a, b), (b, c), (c, a)):
            out.setdefault((u, v) if u < v else (v, u), []).append(fi)
    return out


def _one_pass(mesh: trimesh.Trimesh, min_angle_deg: float, log) -> tuple[trimesh.Trimesh, int, int]:
    """Returns (mesh, collapsed, flipped)."""
    mask = _sliver_mask(mesh, min_angle_deg)
    if not mask.any():
        return mesh, 0, 0

    faces = np.asarray(mesh.faces)
    verts = np.asarray(mesh.vertices)
    sliver_idx = np.nonzero(mask)[0]
    log(f"Sliver pass: {len(sliver_idx)} thin triangles")

    # Edge lengths per sliver face: edge k is opposite vertex k -> (v[k+1], v[k+2])
    tri = verts[faces[sliver_idx]]
    lengths = np.stack([
        np.linalg.norm(tri[:, 1] - tri[:, 2], axis=1),
        np.linalg.norm(tri[:, 2] - tri[:, 0], axis=1),
        np.linalg.norm(tri[:, 0] - tri[:, 1], axis=1),
    ], axis=1)
    shortest = lengths.argmin(axis=1)
    longest = lengths.argmax(axis=1)
    is_needle = lengths.min(axis=1) < 0.25 * lengths.max(axis=1)

    involved = np.unique(faces[sliver_idx])
    adj = _neighbours(mesh, involved)
    ef = _edge_faces(faces)
    edge_set = set(ef.keys())
    valence = np.bincount(faces.ravel(), minlength=len(verts))

    remap = np.arange(len(verts))
    touched = np.zeros(len(verts), dtype=bool)
    collapsed = 0

    # ---- needles: collapse the short edge, only when the link condition holds
    for fi, k, needle in zip(sliver_idx, shortest, is_needle):
        if not needle:
            continue
        f = faces[fi]
        u, v = int(f[(k + 1) % 3]), int(f[(k + 2) % 3])
        if u == v or touched[u] or touched[v]:
            continue
        key = (u, v) if u < v else (v, u)
        incident = ef.get(key, [])
        if len(incident) != 2:                     # boundary or non-manifold edge
            continue
        opposite = set()
        for g in incident:
            opposite |= {int(x) for x in faces[g] if x != u and x != v}
        shared = adj.get(u, set()) & adj.get(v, set())
        if shared != opposite or len(opposite) != 2:
            continue                               # link condition fails: would tear the surface
        keep, drop = (u, v) if valence[u] >= valence[v] else (v, u)
        remap[drop] = keep
        touched[keep] = touched[drop] = True
        collapsed += 1

    new_faces = remap[faces]

    # ---- caps: flip the long edge with the neighbouring triangle
    flipped = 0
    used_faces = set()
    for fi, k, needle in zip(sliver_idx, longest, is_needle):
        if needle or fi in used_faces:
            continue
        f = new_faces[fi]
        if len(set(int(x) for x in f)) < 3:
            continue
        apex = int(f[k])
        u, v = int(f[(k + 1) % 3]), int(f[(k + 2) % 3])
        key = (u, v) if u < v else (v, u)
        incident = [g for g in ef.get(key, []) if g != fi]
        if len(ef.get(key, [])) != 2 or not incident:
            continue
        fj = incident[0]
        if fj in used_faces:
            continue
        g = new_faces[fj]
        other = [int(x) for x in g if x != u and x != v]
        if len(other) != 1 or other[0] == apex:
            continue
        w = other[0]
        new_key = (apex, w) if apex < w else (w, apex)
        if new_key in edge_set:                    # that edge already exists -> non-manifold
            continue
        # replace (apex,u,v) + (u,v,w) with (apex,u,w) + (apex,w,v), preserving orientation
        new_faces[fi] = [apex, u, w]
        new_faces[fj] = [apex, w, v]
        edge_set.add(new_key)
        used_faces.update((fi, fj))
        flipped += 1

    out = trimesh.Trimesh(vertices=verts, faces=new_faces, process=False)
    out.update_faces(out.nondegenerate_faces())
    out.update_faces(out.unique_faces())
    out.remove_unreferenced_vertices()
    return out, collapsed, flipped


def fix_slivers(mesh: trimesh.Trimesh, min_angle_deg: float = 1.0, max_passes: int = 6, log=None):
    """
    Returns (cleaned_mesh, info) where info has collapsed / flipped / before / after counts.
    The result is never topologically worse than the input.
    """
    inner = log or (lambda m, level="info": None)

    def log_safe(message, level="info"):
        try:
            inner(message, level)
        except Exception:
            pass

    work = mesh.copy()
    before = int(_sliver_mask(work, min_angle_deg).sum())
    base_boundary, base_nm = _topology(work)
    collapsed_total = 0
    flipped_total = 0
    skipped = 0

    for _ in range(max_passes):
        if not _sliver_mask(work, min_angle_deg).any():
            break
        candidate, collapsed, flipped = _one_pass(work, min_angle_deg, log_safe)
        if collapsed == 0 and flipped == 0:
            skipped = int(_sliver_mask(work, min_angle_deg).sum())
            break
        b, nm = _topology(candidate)
        if b > base_boundary or nm > base_nm:
            # Should not happen now that both operations are validated, but never
            # hand back a torn mesh: drop this pass and stop.
            log_safe("A sliver pass would have opened the mesh; it was discarded", "warn")
            skipped = int(_sliver_mask(work, min_angle_deg).sum())
            break
        work = candidate
        collapsed_total += collapsed
        flipped_total += flipped

    trimesh.repair.fix_winding(work)
    work.fix_normals()
    after = int(_sliver_mask(work, min_angle_deg).sum())
    if skipped:
        log_safe(f"{skipped} sliver(s) left in place — removing them would tear the surface", "warn")

    info = {
        "before": before,
        "after": after,
        "collapsed": collapsed_total,
        "flipped": flipped_total,
        "skipped": skipped,
        "faces_before": len(mesh.faces),
        "faces_after": len(work.faces),
    }
    return work, info
