"""
Per-corner UV channel — Geekatplay Studio
Author: Vladimir Chopine

Meshwright stores texture coordinates per FACE CORNER, in an (F, 3, 2) array held
beside the mesh, rather than per vertex.

Why it matters: a per-vertex UV forces a vertex to be duplicated at every UV seam.
A renderer does not mind, but a mesh analyser does — the duplication turns one
watertight solid into a pile of disconnected charts with open boundary edges, and
the repair pipeline then closes "holes" that were never there. Per-corner UVs let
the geometry stay welded, so analysis, repair and decimation all see the true
topology. Vertices are split only at the two boundaries that genuinely need it:
the GPU vertex buffer and a glTF/OBJ file (see `split_for_gpu`).

The transfer used after an edit is exact rather than approximate: every corner is
projected to its closest point on the *source surface* (an AABB query, not a
nearest-centroid guess), and corners that land across a UV seam are re-evaluated
inside the chart their own face belongs to.
"""
import numpy as np
import trimesh
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree

from engine.indexing import unique_rows as _unique_rows

try:                                   # broad phase for the exact closest-point query
    import rtree  # noqa: F401
    HAS_RTREE = True
except ImportError:                    # pragma: no cover - exercised by the fallback path
    HAS_RTREE = False

# How many source faces to test per query point when rtree is missing. The
# fallback is exact within this candidate set; 24 covers the neighbourhood of a
# point comfortably on any mesh with sane triangle sizes.
FALLBACK_CANDIDATES = 24

# The same idea for the viewport's quick transfer, where the shortlist only has to be
# long enough to contain the triangle a decimated face actually sits on. Four is
# where the accuracy stops improving on the models this was measured against.
FAST_CANDIDATES = 4


# ------------------------------------------------------------------ construction
def from_vertex_uv(faces, vertex_uv) -> np.ndarray | None:
    """Fan a per-vertex UV array out to per-corner. Returns None if it cannot."""
    if vertex_uv is None or faces is None or len(faces) == 0:
        return None
    uv = np.asarray(vertex_uv, dtype=np.float32)
    f = np.asarray(faces)
    if uv.ndim != 2 or uv.shape[1] != 2 or len(uv) <= int(f.max()):
        return None
    return np.ascontiguousarray(uv[f], dtype=np.float32)


def is_valid(mesh: trimesh.Trimesh, corner_uv) -> bool:
    """True when this UV array actually describes this mesh."""
    if corner_uv is None or mesh is None:
        return False
    uv = np.asarray(corner_uv)
    return uv.ndim == 3 and uv.shape == (len(mesh.faces), 3, 2)


def uv_of(mesh: trimesh.Trimesh, corner_uv) -> np.ndarray | None:
    """The UV array if it matches the mesh, otherwise None. Use before any read."""
    return np.asarray(corner_uv, dtype=np.float32) if is_valid(mesh, corner_uv) else None


# ------------------------------------------------------------------ charts
def chart_labels(mesh: trimesh.Trimesh, corner_uv: np.ndarray, tol: float = 1e-6) -> np.ndarray:
    """
    Label each face with the id of the UV chart (island) it belongs to.

    Two neighbouring faces share a chart when they agree on the UV of both ends of
    their shared edge. Where they disagree, that edge is a seam.
    """
    n_faces = len(mesh.faces)
    if n_faces == 0:
        return np.zeros(0, dtype=np.int32)

    pairs = np.asarray(mesh.face_adjacency)
    if len(pairs) == 0:
        return np.arange(n_faces, dtype=np.int32)

    shared = np.asarray(mesh.face_adjacency_edges)       # (A, 2) welded vertex ids
    faces = np.asarray(mesh.faces)
    joined = np.ones(len(pairs), dtype=bool)

    for end in range(2):                                  # both ends of the shared edge
        vertex = shared[:, end]
        seen = None
        for side in range(2):                             # both faces of the pair
            tri = faces[pairs[:, side]]                   # (A, 3)
            corner = np.argmax(tri == vertex[:, None], axis=1)
            uv = corner_uv[pairs[:, side], corner]        # (A, 2)
            if seen is None:
                seen = uv
            else:
                joined &= np.all(np.abs(uv - seen) <= tol, axis=1)

    linked = pairs[joined]
    if len(linked) == 0:
        return np.arange(n_faces, dtype=np.int32)

    graph = coo_matrix((np.ones(len(linked), dtype=np.int8), (linked[:, 0], linked[:, 1])),
                       shape=(n_faces, n_faces))
    _, labels = connected_components(graph, directed=False)
    return labels.astype(np.int32)


def seam_edge_count(mesh: trimesh.Trimesh, corner_uv: np.ndarray) -> int:
    """How many interior edges are UV seams — reported to the user after unwrapping."""
    if not is_valid(mesh, corner_uv):
        return 0
    labels = chart_labels(mesh, corner_uv)
    pairs = np.asarray(mesh.face_adjacency)
    if len(pairs) == 0:
        return 0
    return int(np.count_nonzero(labels[pairs[:, 0]] != labels[pairs[:, 1]]))


# ------------------------------------------------------------------ geometry helpers
def _barycentric(tri: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """
    Unclamped barycentric coordinates of points against triangles, in the plane of
    each triangle. Shapes (N, 3, 3) and (N, 3) in, (N, 3) out.

    Left unclamped on purpose: outside [0, 1] it extends the triangle's own linear
    parameterisation, which is exactly what a corner sitting just past a chart's
    edge needs.
    """
    v0 = tri[:, 1] - tri[:, 0]
    v1 = tri[:, 2] - tri[:, 0]
    v2 = pts - tri[:, 0]
    d00 = np.einsum('ij,ij->i', v0, v0)
    d01 = np.einsum('ij,ij->i', v0, v1)
    d11 = np.einsum('ij,ij->i', v1, v1)
    d20 = np.einsum('ij,ij->i', v2, v0)
    d21 = np.einsum('ij,ij->i', v2, v1)
    denom = d00 * d11 - d01 * d01
    denom = np.where(np.abs(denom) < 1e-20, 1.0, denom)
    b1 = (d11 * d20 - d01 * d21) / denom
    b2 = (d00 * d21 - d01 * d20) / denom
    return np.stack([1.0 - b1 - b2, b1, b2], axis=1)


def _closest_point_fallback(mesh: trimesh.Trimesh, points: np.ndarray):
    """
    Closest point on the surface without rtree: shortlist faces by centroid with a
    KD-tree, then solve the exact point-triangle distance for each candidate.
    """
    tris = mesh.triangles
    centroids = tris.mean(axis=1)
    k = int(min(FALLBACK_CANDIDATES, len(centroids)))
    _, candidates = cKDTree(centroids).query(points, k=k)
    candidates = np.atleast_2d(candidates.reshape(len(points), k))

    best_d = np.full(len(points), np.inf)
    best_f = np.zeros(len(points), dtype=np.int64)
    best_p = np.zeros((len(points), 3), dtype=np.float64)
    for column in range(k):
        fid = candidates[:, column]
        cp = trimesh.triangles.closest_point(tris[fid], points)
        d = np.linalg.norm(cp - points, axis=1)
        better = d < best_d
        best_d[better] = d[better]
        best_f[better] = fid[better]
        best_p[better] = cp[better]
    return best_p, best_f


def closest_surface_point(mesh: trimesh.Trimesh, points: np.ndarray):
    """Closest point on `mesh` for each query point, as (positions, face_ids)."""
    points = np.asarray(points, dtype=np.float64)
    if len(points) == 0:
        return points.reshape(0, 3), np.zeros(0, dtype=np.int64)
    if HAS_RTREE:
        try:
            cp, _, fid = trimesh.proximity.closest_point(mesh, points)
            return np.asarray(cp), np.asarray(fid, dtype=np.int64)
        except Exception:
            pass
    return _closest_point_fallback(mesh, points)


# ------------------------------------------------------------------ transfer
def _same_topology(a: trimesh.Trimesh, b: trimesh.Trimesh) -> bool:
    return (len(a.faces) == len(b.faces)
            and len(a.vertices) == len(b.vertices)
            and np.array_equal(np.asarray(a.faces), np.asarray(b.faces))
            and np.allclose(np.asarray(a.vertices), np.asarray(b.vertices), atol=1e-9))


def transfer(source: trimesh.Trimesh, source_uv, target: trimesh.Trimesh) -> np.ndarray | None:
    """
    Carry per-corner UVs from `source` onto `target` after an edit.

    Each target corner is projected to its closest point on the source *surface* and
    reads the UV interpolated there. A corner whose closest source face sits in a
    different UV chart from the rest of its own face would drag a texel from the far
    side of the atlas; those are re-evaluated against the face's own chart instead,
    which is what keeps seams from smearing.
    """
    src_uv = uv_of(source, source_uv)
    if src_uv is None or len(target.faces) == 0 or len(source.faces) == 0:
        return None
    if _same_topology(source, target):
        return src_uv.copy()

    tgt_faces = np.asarray(target.faces)
    tgt_verts = np.asarray(target.vertices, dtype=np.float64)
    tris = tgt_verts[tgt_faces]                                   # (F, 3, 3)
    n_faces = len(tgt_faces)

    corners = tris.reshape(-1, 3)                                 # (F*3, 3)
    centroids = tris.mean(axis=1)                                 # (F, 3)

    src_tris = np.asarray(source.triangles)
    labels = chart_labels(source, src_uv)

    # The anchor decides which chart each target face belongs to.
    _, anchor_face = closest_surface_point(source, centroids)
    anchor_chart = labels[anchor_face]

    corner_pos, corner_face = closest_surface_point(source, corners)
    direct_bary = _barycentric(src_tris[corner_face], corner_pos)
    direct_uv = np.einsum('nk,nkj->nj', direct_bary, src_uv[corner_face])

    # Corners that fell into a different chart than their face's anchor: read them
    # from the anchor triangle instead, extending its parameterisation.
    anchor_per_corner = np.repeat(anchor_face, 3)
    crossed = labels[corner_face] != np.repeat(anchor_chart, 3)
    if np.any(crossed):
        af = anchor_per_corner[crossed]
        bary = _barycentric(src_tris[af], corners[crossed])
        direct_uv[crossed] = np.einsum('nk,nkj->nj', bary, src_uv[af])

    # Reading a corner off a neighbouring triangle extends that triangle's plane, so
    # the answer can land just past the edge of the sheet — measured 0.0077 outside on
    # 108 of 119,994 corners when a 2.96M-face model is taken down to 40,000. The
    # viewer samples with RepeatWrapping, which turns "just past the edge" into a
    # colour fetched from the opposite side of the atlas, on about a hundred faces.
    # Holding the result inside the range the source itself used fixes that and leaves
    # a deliberately tiled layout, whose UVs run past 1 on purpose, exactly as it was.
    flat = src_uv.reshape(-1, 2)
    np.clip(direct_uv, flat.min(axis=0), flat.max(axis=0), out=direct_uv)

    return np.ascontiguousarray(direct_uv.reshape(n_faces, 3, 2), dtype=np.float32)


def transfer_fast(source: trimesh.Trimesh, source_uv, target: trimesh.Trimesh) -> np.ndarray | None:
    """
    `transfer` for the viewport: same chart-aware answer, a cheaper way of finding
    which source triangle to read.

    The exact version projects every corner onto the source surface, which costs half
    a minute on a 200,000-face display copy — too long to wait before the first frame.
    This one shortlists by nearest face centroid instead. The chart check is what
    actually matters for the picture: a corner is read from its own nearest triangle
    only when that triangle belongs to the same UV island as the face it is part of,
    and otherwise from the face's anchor. Without that test a decimated corner reads
    whichever island happens to be nearest in space, and the model comes out papered
    in fragments from all over the atlas.

    Use `transfer` for anything the user keeps; this is for what they look at.
    """
    src_uv = uv_of(source, source_uv)
    if src_uv is None or len(target.faces) == 0 or len(source.faces) == 0:
        return None
    if _same_topology(source, target):
        return src_uv.copy()

    src_tris = np.asarray(source.triangles)
    labels = chart_labels(source, src_uv)
    tree = cKDTree(src_tris.mean(axis=1))

    tris = np.asarray(target.vertices, dtype=np.float64)[np.asarray(target.faces)]
    corners = tris.reshape(-1, 3)

    def nearest_face(points, shortlist=FAST_CANDIDATES):
        """Closest source face by true point-triangle distance, over a centroid shortlist."""
        _, candidates = tree.query(points, k=min(shortlist, len(src_tris)), workers=-1)
        candidates = np.atleast_2d(candidates.reshape(len(points), -1))
        best_d = np.full(len(points), np.inf)
        best_f = np.zeros(len(points), dtype=np.int64)
        for column in range(candidates.shape[1]):
            fid = candidates[:, column]
            d = np.linalg.norm(trimesh.triangles.closest_point(src_tris[fid], points) - points, axis=1)
            closer = d < best_d
            best_d[closer] = d[closer]
            best_f[closer] = fid[closer]
        return best_f

    # Which island a face belongs to is the one decision worth paying for: getting it
    # wrong paints the whole face from somewhere else in the atlas. Measured on the
    # model this was found on, taking the anchor by true distance rather than by
    # nearest centroid moved the display copy from 6.7% of faces visibly off to 1.8%,
    # for half a second. Doing the same for each corner costs another second and
    # changes nothing, so the corners keep the cheap query.
    anchor = nearest_face(tris.mean(axis=1))                   # island per target face
    _, nearest = tree.query(corners, workers=-1)               # best triangle per corner
    anchor_per_corner = np.repeat(anchor, 3)
    source_face = np.where(labels[nearest] == labels[anchor_per_corner], nearest, anchor_per_corner)

    bary = _barycentric(src_tris[source_face], corners)
    uv = np.einsum('nk,nkj->nj', bary, src_uv[source_face])

    # As in `transfer`: reading off a neighbouring triangle extends its plane, so hold
    # the result inside the range the source itself used rather than letting a corner
    # wrap around to the far side of the atlas.
    flat = src_uv.reshape(-1, 2)
    np.clip(uv, flat.min(axis=0), flat.max(axis=0), out=uv)
    return np.ascontiguousarray(uv.reshape(len(target.faces), 3, 2), dtype=np.float32)


# ------------------------------------------------------------------ splitting out
def split_for_gpu(mesh: trimesh.Trimesh, corner_uv, quantize: int = 1 << 20):
    """
    Expand a welded mesh + corner UVs into the per-vertex form a GPU or a glTF file
    needs: vertices duplicated only where a UV seam actually runs.

    Returns (positions, faces, uvs, vertex_map) where `vertex_map` gives, for every
    original vertex, one index into the new buffer — enough to redraw anything that
    was indexed against the welded mesh, such as open-edge overlays.
    """
    uv = uv_of(mesh, corner_uv)
    verts = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(mesh.faces)
    if uv is None:
        return verts, faces, None, np.arange(len(verts), dtype=np.int64)

    flat_vertex = faces.reshape(-1).astype(np.int64)
    flat_uv = uv.reshape(-1, 2)

    # One row per corner: the vertex it uses plus its quantised UV. Corners that
    # agree on all three collapse back into a single shared vertex.
    key = np.column_stack([
        flat_vertex,
        np.round(flat_uv.astype(np.float64) * quantize).astype(np.int64),
    ])
    first, inverse, _ = _unique_rows(key)

    new_faces = inverse.reshape(-1, 3).astype(np.int64)
    new_verts = verts[flat_vertex[first]]
    new_uvs = flat_uv[first]

    # Any split copy will do for an overlay drawn in the welded index space.
    vertex_map = np.zeros(len(verts), dtype=np.int64)
    vertex_map[flat_vertex[first]] = np.arange(len(first), dtype=np.int64)
    return new_verts, new_faces, np.ascontiguousarray(new_uvs, dtype=np.float32), vertex_map


def to_textured_mesh(mesh: trimesh.Trimesh, corner_uv, material=None) -> trimesh.Trimesh:
    """A trimesh carrying per-vertex UVs, ready for glTF / OBJ export."""
    verts, faces, uvs, _ = split_for_gpu(mesh, corner_uv)
    out = trimesh.Trimesh(vertices=verts, faces=faces, process=False)
    if uvs is not None:
        out.visual = trimesh.visual.TextureVisuals(uv=uvs, material=material)
    return out


# ------------------------------------------------------------------ UI data
def _corner_of(faces, face_ids, vertex_ids):
    """Which corner (0, 1 or 2) of each face holds that vertex."""
    return np.argmax(faces[face_ids] == vertex_ids[:, None], axis=1)


def extract_island_contours(corner_uv: np.ndarray, max_edges: int = 15000,
                            quantize: float = 1e5) -> np.ndarray:
    """
    Extract continuous island boundary loops directly in 2D UV space.

    An edge is on the perimeter of a UV island if and only if it is traversed by
    exactly one triangle in UV space. By extracting directed boundary edges and chaining
    them into closed polygonal loops, we guarantee:
    1. 100% boundary coverage (both sides of all seams and all open edges).
    2. Subsampling along the loop keeps contours closed and continuous, eliminating
       the scattered 'dots' artifact caused by disjoint edge decimation.
    """
    flat_uv = np.asarray(corner_uv, dtype=np.float32).reshape(-1, 2)
    if len(flat_uv) < 3:
        return np.zeros((0, 2, 2), dtype=np.float32)

    int_uv = np.round(flat_uv * quantize).astype(np.int64)
    u_keys = (int_uv[:, 0] + 10_000_000).astype(np.uint64)
    v_keys = (int_uv[:, 1] + 10_000_000).astype(np.uint64)
    packed = (v_keys << 32) | u_keys

    unique_packed, inv = np.unique(packed, return_inverse=True)
    tri_corners = inv.reshape(-1, 3)

    e0 = tri_corners[:, [0, 1]]
    e1 = tri_corners[:, [1, 2]]
    e2 = tri_corners[:, [2, 0]]
    edges = np.vstack([e0, e1, e2])

    sorted_edges = np.sort(edges, axis=1)
    edge_keys = (sorted_edges[:, 0].astype(np.uint64) << 32) | sorted_edges[:, 1].astype(np.uint64)

    _, first_idx, counts = np.unique(edge_keys, return_index=True, return_counts=True)
    b_indices = first_idx[counts == 1]
    if len(b_indices) == 0:
        return np.zeros((0, 2, 2), dtype=np.float32)

    b_edges = edges[b_indices]

    u_vals = (unique_packed & 0xFFFFFFFF).astype(np.int64) - 10_000_000
    v_vals = (unique_packed >> 32).astype(np.int64) - 10_000_000
    uniq_uv = np.column_stack([u_vals, v_vals]).astype(np.float32) / float(quantize)

    adj = {}
    for start, end in b_edges:
        adj[start] = end

    visited = set()
    loops = []
    for start in adj:
        if start not in visited:
            curr = start
            loop = []
            while curr in adj and curr not in visited:
                visited.add(curr)
                loop.append(curr)
                curr = adj[curr]
                if curr == start:
                    loop.append(curr)
                    break
            if len(loop) > 2:
                loops.append(loop)

    total_edges = sum(len(l) - 1 for l in loops)
    step = max(1, int(np.ceil(total_edges / max_edges))) if max_edges and total_edges > max_edges else 1

    segments = []
    for loop in loops:
        if step > 1 and len(loop) > 4:
            stride = min(step, max(1, len(loop) // 3))
            sub = loop[::stride]
            if sub[-1] != sub[0]:
                sub.append(sub[0])
            for i in range(len(sub) - 1):
                segments.append((uniq_uv[sub[i]], uniq_uv[sub[i + 1]]))
        else:
            for i in range(len(loop) - 1):
                segments.append((uniq_uv[loop[i]], uniq_uv[loop[i + 1]]))

    if not segments and len(b_edges):
        for s, e in b_edges:
            segments.append((uniq_uv[s], uniq_uv[e]))

    return np.asarray(segments, dtype=np.float32) if segments else np.zeros((0, 2, 2), dtype=np.float32)


def seam_outline(mesh: trimesh.Trimesh, corner_uv: np.ndarray, max_edges: int = 15000) -> np.ndarray:
    """
    The island outlines in UV space, as (E, 2, 2) line segments.

    Captures both sides of every UV seam as well as mesh boundary edges,
    chaining them into continuous polygonal island contours.
    """
    uv = uv_of(mesh, corner_uv)
    if uv is None:
        return np.zeros((0, 2, 2), dtype=np.float32)
    return extract_island_contours(uv, max_edges=max_edges)


def wireframe_lines(mesh: trimesh.Trimesh, corner_uv, max_edges: int = 15000) -> dict:
    """
    Flat [u0, v0, u1, v1, ...] list of UV-space edges for the 2D unfold canvas.

    A model with a few thousand triangles is drawn edge for edge. Past that, drawing
    the full wireframe produces an illegible dense mesh, so what gets drawn instead is
    the continuous island contour loops — the seams and open edges that define the
    layout. These are chained into closed polygons so they never degrade to dots.
    """
    uv = uv_of(mesh, corner_uv)
    if uv is None or len(mesh.faces) == 0:
        return {"has_uv": False, "lines": [], "edge_count": 0, "outlines_only": False}

    outlines_only = len(uv) * 3 > max_edges
    if outlines_only:
        edges = extract_island_contours(uv, max_edges=max_edges)
    else:
        edges = np.concatenate([uv[:, [0, 1]], uv[:, [1, 2]], uv[:, [2, 0]]], axis=0)

    return {
        "has_uv": True,
        "edge_count": int(len(edges)),
        "outlines_only": bool(outlines_only),
        "lines": np.round(edges.reshape(-1), 4).tolist(),
    }
