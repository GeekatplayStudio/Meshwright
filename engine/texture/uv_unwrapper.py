"""
UV Unwrapping & Parameterization via xatlas — Geekatplay Studio
Computes chart segmentation, 2D flattening, and atlas packing.

Two things here are not the obvious implementation, and both are measured rather
than defensive:

The mesh is split into open, size-capped patches before xatlas sees it
(uv_patches.py explains why: xatlas crashes on closed surfaces and silently returns
overlapping charts on large ones). Patches go into a single atlas, so they still
pack into one texture.

xatlas then runs in a child process. The known failure modes are designed around
now, but it is native code, and a crash in it would close the Meshwright window
without a word. One process spawn is cheap insurance.

Every result is checked before it is handed back. If xatlas produces an overlapping
layout anyway, the patches are halved and it is tried again rather than shipping
UVs that would smear a texture.
"""
import os
import subprocess
import sys
import tempfile

import numpy as np
import trimesh
from PIL import Image, ImageDraw

from . import uv_patches as PATCH
from .uv_channel import seam_edge_count

try:
    import xatlas
    HAS_XATLAS = True
except ImportError:
    HAS_XATLAS = False

_WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_xatlas_worker.py")

# With patching, a 327,680-face model unwraps in under two seconds. This only has to
# be long enough that a genuinely enormous model is not cut off prematurely.
UNWRAP_TIMEOUT_S = 300

# How many times the patch cap may be halved when a layout comes back overlapping
# or badly stretched. Each attempt costs well under a second on a 300,000-face model.
MAX_RETRIES = 3


class UnwrapError(RuntimeError):
    """Unwrapping could not be completed, with a reason worth showing the user."""


# ------------------------------------------------------------------ the worker
def _pack_input(vertices, faces, groups, path):
    """Lay the patches out as flat arrays the worker can slice."""
    verts, tris, v_off, f_off = [], [], [0], [0]
    for group in groups:
        pv, pf = PATCH.localise(vertices, faces, group)
        verts.append(pv)
        tris.append(pf)
        v_off.append(v_off[-1] + len(pv))
        f_off.append(f_off[-1] + len(pf))
    np.savez(path,
             vertices=np.concatenate(verts).astype(np.float64),
             faces=np.concatenate(tris).astype(np.int32),
             vertex_offsets=np.asarray(v_off, dtype=np.int64),
             face_offsets=np.asarray(f_off, dtype=np.int64))


def _run_worker(vertices, faces, groups):
    """Unwrap the patches out of process. Raises UnwrapError with something actionable."""
    work = tempfile.mkdtemp(prefix="meshwright-uv-")
    in_path = os.path.join(work, "in.npz")
    out_path = os.path.join(work, "out.npz")
    try:
        _pack_input(vertices, faces, groups, in_path)
        try:
            proc = subprocess.run([sys.executable, _WORKER, in_path, out_path],
                                  capture_output=True, timeout=UNWRAP_TIMEOUT_S, check=False)
        except subprocess.TimeoutExpired:
            raise UnwrapError(
                f"Unwrapping this model ({len(faces):,} faces) took longer than "
                f"{UNWRAP_TIMEOUT_S // 60} minutes and was stopped. Reduce the model first, "
                "then unwrap — the layout will be the same shape and far quicker.") from None
        except (OSError, ValueError) as exc:
            return _run_in_process(vertices, faces, groups, note=str(exc))

        if proc.returncode != 0 or not os.path.exists(out_path):
            detail = (proc.stderr or b"").decode("utf-8", "replace").strip().splitlines()
            if detail:
                raise UnwrapError(f"Unwrapping failed: {detail[-1]}")
            raise UnwrapError(
                f"The UV unwrapper stopped unexpectedly on this model ({len(faces):,} faces). "
                "Reducing the model first usually gets it through.")

        data = np.load(out_path)
        return (data["uvs"], data["indices"], data["uv_offsets"],
                data["charts"], tuple(int(x) for x in data["atlas_size"]))
    finally:
        for path in (in_path, out_path):
            try:
                os.remove(path)
            except OSError:
                pass
        try:
            os.rmdir(work)
        except OSError:
            pass


def _run_in_process(vertices, faces, groups, note=""):
    """Last resort when no child process can be started."""
    atlas = xatlas.Atlas()
    for group in groups:
        pv, pf = PATCH.localise(vertices, faces, group)
        atlas.add_mesh(pv, pf)
    atlas.generate()

    uv_parts, index_parts, offsets, charts = [], [], [0], []
    for i in range(len(groups)):
        _, indices, uvs = atlas[i]
        uv_parts.append(np.asarray(uvs, dtype=np.float32))
        index_parts.append(np.asarray(indices, dtype=np.int64))
        offsets.append(offsets[-1] + len(uvs))
        try:
            charts.append(int(atlas.get_mesh_chart_count(i)))
        except Exception:
            charts.append(1)
    return (np.concatenate(uv_parts), np.concatenate(index_parts),
            np.asarray(offsets), np.asarray(charts), (atlas.width, atlas.height))


# ------------------------------------------------------------------ public API
def _attempt(vertices, faces, cap):
    """One unwrap at a given patch cap. Returns (corner_uv, stats)."""
    groups = PATCH.split_into_patches(vertices, faces, max_faces=cap)
    if not groups:
        raise UnwrapError("Cannot unwrap a mesh with no faces.")

    uvs, indices, offsets, charts, atlas_size = _run_worker(vertices, faces, groups)

    corner_uv = np.zeros((len(faces), 3, 2), dtype=np.float32)
    cursor = 0
    for i, group in enumerate(groups):
        patch_uv = uvs[offsets[i]:offsets[i + 1]]
        patch_idx = indices[cursor:cursor + len(group)]
        cursor += len(group)
        if len(patch_idx) != len(group):
            raise UnwrapError("The unwrapper returned a different face count; cannot map UVs back.")
        corner_uv[group] = patch_uv[patch_idx]

    stats = {
        "patches": len(groups),
        "islands": int(np.sum(charts)),
        "atlas_size": [int(atlas_size[0]), int(atlas_size[1])],
        "patch_cap": int(cap),
        "uv_coverage": round(PATCH.uv_area(corner_uv), 3),
        "texel_spread": round(PATCH.texel_spread(vertices, faces, corner_uv), 2),
    }
    return corner_uv, stats


def unwrap_corner_uv(mesh: trimesh.Trimesh, max_patch_faces: int = PATCH.DEFAULT_PATCH_FACES
                     ) -> tuple[np.ndarray, dict]:
    """
    Unwrap without touching the geometry.

    The caller keeps its welded, watertight mesh and gains a per-corner UV array —
    xatlas's vertex split is a rendering detail and is unpacked straight back out.

    Returns (corner_uv of shape (F, 3, 2), stats).
    """
    if not HAS_XATLAS:
        raise UnwrapError("The UV unwrapper (xatlas) is not installed. "
                          "Run install.bat again, or: .venv\\Scripts\\python -m pip install xatlas")
    if len(mesh.faces) == 0:
        raise UnwrapError("Cannot unwrap a mesh with no faces.")

    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(mesh.faces, dtype=np.int32)

    cap = int(max_patch_faces)
    best = None                       # (corner_uv, stats) — the least-stretched valid try
    problem = "no attempt completed"

    for attempt in range(MAX_RETRIES + 1):
        corner_uv, stats = _attempt(vertices, faces, cap)
        stats["retries"] = attempt
        problem = PATCH.validate(corner_uv)

        if problem is None:
            if best is None or stats["texel_spread"] < best[1]["texel_spread"]:
                best = (corner_uv, stats)
            # An even layout is as good as this is going to get; stop paying for more.
            if stats["texel_spread"] <= PATCH.GOOD_TEXEL_SPREAD:
                break

        if cap <= PATCH.MIN_PATCH_FACES:
            break
        cap = max(PATCH.MIN_PATCH_FACES, cap // 2)

    if best is None:
        raise UnwrapError(
            f"The UV layout could not be made usable — {problem}. This is a limit of the "
            "unwrapper on this shape; reducing the model first usually resolves it.")

    corner_uv, stats = best
    stats["faces"] = len(faces)
    stats["vertices"] = len(mesh.vertices)
    stats["seam_edges"] = seam_edge_count(mesh, corner_uv)
    stats["has_uv"] = True
    stats["even"] = bool(stats["texel_spread"] <= PATCH.GOOD_TEXEL_SPREAD)
    return corner_uv, stats


def unwrap_mesh(mesh: trimesh.Trimesh) -> tuple[trimesh.Trimesh, dict]:
    """
    Unwrap and return a vertex-split mesh carrying per-vertex UVs.

    This is the form a GPU or a glTF file wants, and it is what the ComfyUI nodes
    consume. Inside Meshwright use `unwrap_corner_uv`, which leaves geometry welded.
    """
    from .uv_channel import to_textured_mesh

    corner_uv, stats = unwrap_corner_uv(mesh)
    unwrapped = to_textured_mesh(mesh, corner_uv)
    stats = dict(stats)
    stats["original_vertices"] = len(mesh.vertices)
    stats["unwrapped_vertices"] = len(unwrapped.vertices)
    return unwrapped, stats


def export_uv_wireframe_image(mesh: trimesh.Trimesh, width: int = 1024, height: int = 1024,
                              line_color: tuple = (0, 230, 180, 220),
                              bg_color: tuple = (0, 0, 0, 0),
                              size: tuple | None = None) -> Image.Image:
    """
    Renders a crisp 2D transparent wireframe of the UV islands.
    Used as an overlay guide layer in Photoshop / GIMP.
    """
    if size is not None:
        width, height = size

    if not hasattr(mesh, "visual") or not hasattr(mesh.visual, "uv") or mesh.visual.uv is None:
        mesh, _ = unwrap_mesh(mesh)

    uvs = np.asarray(mesh.visual.uv, dtype=np.float64)
    faces = np.asarray(mesh.faces, dtype=np.int32)

    img = Image.new("RGBA", (width, height), bg_color)
    draw = ImageDraw.Draw(img)

    # Map UV [0, 1] -> Canvas pixels [0, width], [height, 0] (V inverted for image space)
    px = np.clip(uvs[:, 0] * (width - 1), 0, width - 1)
    py = np.clip((1.0 - uvs[:, 1]) * (height - 1), 0, height - 1)

    for face in faces:
        p0 = (float(px[face[0]]), float(py[face[0]]))
        p1 = (float(px[face[1]]), float(py[face[1]]))
        p2 = (float(px[face[2]]), float(py[face[2]]))

        draw.line([p0, p1], fill=line_color, width=1)
        draw.line([p1, p2], fill=line_color, width=1)
        draw.line([p2, p0], fill=line_color, width=1)

    return img
