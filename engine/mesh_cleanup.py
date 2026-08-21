"""
Sliver-triangle removal.

Two kinds of slivers exist:
  * needles — one very short edge. Fixed by collapsing that edge: the vertex
    with fewer incident faces is merged into the better-connected one, so the
    larger surrounding geometry stays where it is.
  * caps — one angle close to 180°, no short edge. Fixed by flipping the long
    edge shared with the neighbouring triangle.

Passes repeat until no slivers remain or nothing changes.
"""
import numpy as np
import trimesh


def _sliver_mask(mesh: trimesh.Trimesh, min_angle_deg: float) -> np.ndarray:
    if len(mesh.faces) == 0:
        return np.zeros(0, dtype=bool)
    return mesh.face_angles.min(axis=1) < np.radians(min_angle_deg)


def fix_slivers(mesh: trimesh.Trimesh, min_angle_deg: float = 1.0, max_passes: int = 6, log=None):
    """
    Returns (cleaned_mesh, info) where info has collapsed / flipped / before / after counts.
    """
    log = log or (lambda m, level="info": None)
    work = mesh.copy()
    before = int(_sliver_mask(work, min_angle_deg).sum())
    collapsed_total = 0
    flipped_total = 0

    for p in range(max_passes):
        mask = _sliver_mask(work, min_angle_deg)
        if not mask.any():
            break
        faces = work.faces
        verts = work.vertices.copy()
        sliver_idx = np.nonzero(mask)[0]
        log(f"Sliver pass {p + 1}: {len(sliver_idx)} thin triangles")

        # edge lengths per face: edge k is opposite vertex k -> (v[k+1], v[k+2])
        tri = verts[faces[sliver_idx]]
        e0 = np.linalg.norm(tri[:, 1] - tri[:, 2], axis=1)
        e1 = np.linalg.norm(tri[:, 2] - tri[:, 0], axis=1)
        e2 = np.linalg.norm(tri[:, 0] - tri[:, 1], axis=1)
        lengths = np.stack([e0, e1, e2], axis=1)
        shortest = lengths.argmin(axis=1)
        longest = lengths.argmax(axis=1)
        is_needle = lengths.min(axis=1) < 0.25 * lengths.max(axis=1)

        valence = np.bincount(faces.ravel(), minlength=len(verts))
        remap = np.arange(len(verts))
        touched = np.zeros(len(verts), dtype=bool)
        collapsed = 0

        # --- needles: collapse the short edge onto the better-connected vertex
        for fi, k, needle in zip(sliver_idx, shortest, is_needle):
            if not needle:
                continue
            a, b = faces[fi][(k + 1) % 3], faces[fi][(k + 2) % 3]
            a, b = remap[a], remap[b]
            if a == b or touched[a] or touched[b]:
                continue
            keep, drop = (a, b) if valence[a] >= valence[b] else (b, a)
            remap[remap == drop] = keep
            touched[keep] = touched[drop] = True
            collapsed += 1

        new_faces = remap[faces]

        # --- caps: flip the long edge with the neighbouring triangle
        flipped = 0
        if (~is_needle).any():
            tm = trimesh.Trimesh(vertices=verts, faces=new_faces, process=False)
            adj = tm.face_adjacency
            adj_edges = tm.face_adjacency_edges
            # lookup: (face, sorted edge) -> neighbour face
            lookup = {}
            for (f1, f2), (u, v) in zip(adj, adj_edges):
                key = (min(u, v), max(u, v))
                lookup[(f1, key)] = f2
                lookup[(f2, key)] = f1
            used_faces = set()
            nf = new_faces.copy()
            for fi, k, needle in zip(sliver_idx, longest, is_needle):
                if needle or fi in used_faces:
                    continue
                f = nf[fi]
                if len(set(f)) < 3:
                    continue
                apex = f[k]
                u, v = f[(k + 1) % 3], f[(k + 2) % 3]
                key = (min(u, v), max(u, v))
                fj = lookup.get((fi, key))
                if fj is None or fj in used_faces:
                    continue
                g = nf[fj]
                other = [x for x in g if x != u and x != v]
                if len(other) != 1 or other[0] == apex:
                    continue
                w = other[0]
                # replace (apex,u,v)+(u,v,w) with (apex,u,w)+(apex,w,v) preserving orientation of fi
                nf[fi] = [apex, u, w]
                nf[fj] = [apex, w, v]
                used_faces.update((fi, fj))
                flipped += 1
            new_faces = nf

        work = trimesh.Trimesh(vertices=verts, faces=new_faces, process=False)
        work.update_faces(work.nondegenerate_faces())
        work.update_faces(work.unique_faces())
        work.remove_unreferenced_vertices()
        collapsed_total += collapsed
        flipped_total += flipped
        if collapsed == 0 and flipped == 0:
            break

    work.fix_normals()
    after = int(_sliver_mask(work, min_angle_deg).sum())
    info = {
        "before": before,
        "after": after,
        "collapsed": collapsed_total,
        "flipped": flipped_total,
        "faces_before": int(len(mesh.faces)),
        "faces_after": int(len(work.faces)),
    }
    return work, info
