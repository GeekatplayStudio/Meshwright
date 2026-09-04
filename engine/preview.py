"""
Mesh preview generation for viewport serialization.

The viewport gets its own copy of the geometry, and for a dense model that copy is
the expensive part of loading: a five-million-triangle mesh is 147 MB of base64 by the
time it has crossed the bridge into the page. So above a threshold the preview is
decimated — the *display* is simplified, never the model. Diagnostics, repair,
reduction and export always work on the mesh Meshwright is holding.
"""
import base64

import numpy as np
import trimesh
from scipy.spatial import cKDTree
from trimesh.grouping import group_rows

from engine.texture.uv_channel import split_for_gpu, uv_of

try:
    import fast_simplification as fs
    HAS_FAST_SIMPLIFY = True
except ImportError:
    HAS_FAST_SIMPLIFY = False

# Roughly 25 MB of payload, which crosses the bridge and uploads to the GPU without a
# stall. Past this the wait is long enough that people assume the app has hung.
DEFAULT_PREVIEW_FACES = 900_000

# Below this a shell is left alone: decimating a handful of triangles saves nothing
# and can collapse a small part away entirely.
MIN_SHELL_FACES = 64


def _b64(array, dtype) -> str:
    return base64.b64encode(np.ascontiguousarray(array, dtype=dtype).tobytes()).decode("ascii")


def _decimate_block(vertices, faces, uvs, keep):
    """
    One connected block of welded geometry at reduced detail, carrying UVs across.
    """
    reduction = float(np.clip(1.0 - keep, 0.0, 0.99))
    verts32 = np.ascontiguousarray(vertices, dtype=np.float32)
    faces32 = np.ascontiguousarray(faces, dtype=np.int32)

    new_v, new_f = fs.simplify(verts32, faces32, target_reduction=reduction)
    new_v = np.asarray(new_v, dtype=np.float32)
    new_f = np.asarray(new_f, dtype=np.int64)

    new_uv = None
    if uvs is not None and len(new_v):
        _, nearest = cKDTree(verts32).query(new_v, workers=-1)
        new_uv = np.ascontiguousarray(uvs[nearest], dtype=np.float32)
    return new_v, new_f, new_uv


def _reduce_for_display(vertices, faces, uvs, shell_face_counts, keep):
    """
    Decimate for the viewport, one shell at a time, on continuous welded geometry.
    """
    blocks = [int(c) for c in shell_face_counts] if shell_face_counts else [len(faces)]

    out_v, out_f, out_uv, out_counts, offset, start = [], [], [], [], 0, 0
    for count in blocks:
        block = faces[start:start + count]
        start += count
        if len(block) == 0:
            out_counts.append(0)
            continue

        used = np.unique(block)
        remap = np.zeros(len(vertices), dtype=np.int64)
        remap[used] = np.arange(len(used))
        local_v = vertices[used]
        local_f = remap[block]
        local_uv = uvs[used] if uvs is not None else None

        if len(block) > MIN_SHELL_FACES:
            local_v, local_f, local_uv = _decimate_block(local_v, local_f, local_uv, keep)

        out_v.append(np.asarray(local_v))
        out_f.append(np.asarray(local_f) + offset)
        if local_uv is not None:
            out_uv.append(local_uv)
        out_counts.append(len(local_f))
        offset += len(local_v)

    vertices = np.vstack(out_v) if out_v else np.zeros((0, 3), dtype=np.float32)
    faces = np.vstack(out_f) if out_f else np.zeros((0, 3), dtype=np.int64)
    uvs = np.vstack(out_uv) if out_uv and len(out_uv) == len(out_v) else None
    return vertices, faces, uvs, out_counts


def build_mesh_preview(mesh: trimesh.Trimesh, corner_uv=None, max_faces: int | None = None,
                       shell_face_counts=None, detail: float | None = None) -> dict:
    """
    Serialize mesh geometry to base64 typed arrays for the Three.js client.

    The mesh Meshwright holds is welded. A GPU vertex buffer cannot be: it needs one
    UV per vertex. Decimation is performed on the welded geometry FIRST to keep topology
    closed and eliminate UV seam tearing artifacts.

    `detail` is the fraction of the model to draw, or None to derive one from
    `max_faces`. What actually happened is reported back under "detail", so the
    interface can say so rather than quietly showing something else.
    """
    uv = uv_of(mesh, corner_uv)

    # Open edges are a property of the real topology, so they are found on the welded mesh.
    edges = mesh.edges_sorted
    groups = group_rows(edges, require_count=1) if len(edges) else []
    boundary = np.asarray(edges[groups], dtype=np.int64) if len(groups) else np.zeros((0, 2), np.int64)

    total_faces = len(mesh.faces)
    keep = 1.0
    if detail is not None:
        keep = float(np.clip(detail, 0.01, 1.0))
    elif max_faces and total_faces > max_faces:
        keep = max_faces / total_faces

    reduce_now = keep < 1.0 and HAS_FAST_SIMPLIFY and total_faces > MIN_SHELL_FACES
    if reduce_now:
        # Decimate the WELDED, CONTINUOUS geometry first — keeps topology closed with 0 seam tearing
        raw_v = np.asarray(mesh.vertices, dtype=np.float32)
        raw_f = np.asarray(mesh.faces, dtype=np.int64)

        vert_uv = None
        if uv is not None and len(mesh.vertices) > 0:
            flat_v = mesh.faces.reshape(-1)
            flat_uv = uv.reshape(-1, 2)
            vert_uv = np.zeros((len(mesh.vertices), 2), dtype=np.float32)
            _, first_idx = np.unique(flat_v, return_index=True)
            vert_uv[flat_v[first_idx]] = flat_uv[first_idx]

        vertices, faces, split_uv, shell_face_counts = _reduce_for_display(
            raw_v, raw_f, vert_uv, shell_face_counts, keep)
        # The overlay would describe a mesh that is no longer the one on screen.
        boundary = np.zeros((0, 2), np.int64)
    else:
        if uv is None:
            vertices = np.asarray(mesh.vertices)
            faces = np.asarray(mesh.faces)
            split_uv = None
        else:
            vertices, faces, split_uv, vertex_map = split_for_gpu(mesh, uv)
            boundary = vertex_map[boundary] if len(boundary) else boundary

    out = {
        "vertices": _b64(vertices, np.float32),
        "faces": _b64(faces, np.uint32),
        "boundary_edges": _b64(boundary, np.uint32),
        "face_count": len(faces),
        "detail": {
            "reduced": bool(reduce_now),
            "faces_shown": len(faces),
            "faces_total": total_faces,
            "fraction": round(len(faces) / total_faces, 4) if total_faces else 1.0,
            "open_edges_hidden": bool(reduce_now and len(groups) > 0),
        },
    }
    if reduce_now and shell_face_counts:
        out["shell_face_counts"] = [int(c) for c in shell_face_counts]
    if split_uv is not None:
        out["uvs"] = _b64(split_uv, np.float32)
    return out
