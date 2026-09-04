"""
Meshwright ComfyUI Utilities — Geekatplay Studio
Provides mesh conversion, diagnostic reporting, and software 3D preview rendering.
"""
import math
import os

import numpy as np
import trimesh

try:
    import torch
    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False


def to_trimesh(data) -> trimesh.Trimesh:
    """
    Normalizes any common ComfyUI 3D representation to a trimesh.Trimesh object.
    Supports:
      - trimesh.Trimesh
      - dict with 'mesh': trimesh.Trimesh
      - dict with 'vertices' and 'faces'
      - string path to a 3D file (.obj, .stl, .glb, .gltf, .ply, .fbx, etc.)
    """
    if data is None:
        raise ValueError("Input mesh is None.")

    if isinstance(data, trimesh.Trimesh):
        return data

    if isinstance(data, trimesh.Scene):
        geoms = list(data.geometry.values())
        if len(geoms) == 0:
            raise ValueError("Scene contains no 3D geometry.")
        return trimesh.util.concatenate(geoms)

    if isinstance(data, dict):
        if "mesh" in data and isinstance(data["mesh"], (trimesh.Trimesh, trimesh.Scene)):
            return to_trimesh(data["mesh"])
        if "vertices" in data and "faces" in data:
            verts = np.asarray(data["vertices"], dtype=np.float64)
            faces = np.asarray(data["faces"], dtype=np.int32)
            return trimesh.Trimesh(vertices=verts, faces=faces, process=False)

    if isinstance(data, str):
        path = data.strip().strip('"').strip("'")
        if not os.path.exists(path):
            raise FileNotFoundError(f"3D model file not found: {path}")
        from engine.model_loader import load_model
        mesh, _ = load_model(path)
        return mesh

    raise TypeError(f"Unsupported 3D mesh data type: {type(data)}")


def format_fix_report(before_analysis: dict, after_analysis: dict, fixes: list, method: str, passes: int) -> str:
    """Creates a comprehensive human-readable report of what was wrong before and how it was fixed."""
    b_stats = before_analysis.get("stats", {})
    a_stats = after_analysis.get("stats", {})
    b_issues = before_analysis.get("issues", [])
    a_issues = after_analysis.get("issues", [])

    lines = []
    lines.append("=" * 60)
    lines.append("  GEEKATPLAY MESHWRIGHT - REPAIR REPORT")
    lines.append("=" * 60)
    lines.append(f"Passes performed: {passes} | Heaviest engine used: {method.upper()}")
    lines.append(f"Readiness Score:  {before_analysis.get('score', 0)}/100  -->  {after_analysis.get('score', 0)}/100")
    lines.append(f"Verdict:          {before_analysis.get('verdict', 'Unknown')}  -->  {after_analysis.get('verdict', 'Unknown')}")
    lines.append("")

    lines.append("--- [1] WHAT WAS WRONG BEFORE ---")
    if not b_issues:
        lines.append("  No critical issues detected in initial mesh.")
    else:
        for idx, iss in enumerate(b_issues, 1):
            sev = iss.get("severity", "info").upper()
            title = iss.get("title", "")
            detail = iss.get("detail", "")
            cnt = iss.get("count", 0)
            cnt_str = f" ({cnt} detected)" if cnt else ""
            lines.append(f"  {idx}. [{sev}] {title}{cnt_str}: {detail}")
    lines.append("")

    lines.append("--- [2] ACTIONS & FIXES APPLIED ---")
    if not fixes:
        lines.append("  No geometry modifications were needed.")
    else:
        for idx, f in enumerate(fixes, 1):
            stage = f.get("stage", "Repair")
            desc = f.get("description", "")
            lines.append(f"  * [{stage}] {desc}")
    lines.append("")

    lines.append("--- [3] GEOMETRY BEFORE vs AFTER ---")
    lines.append(f"  Faces:          {b_stats.get('face_count', 0):,}  -->  {a_stats.get('face_count', 0):,}")
    lines.append(f"  Vertices:       {b_stats.get('vertex_count', 0):,}  -->  {a_stats.get('vertex_count', 0):,}")
    lines.append(f"  Watertight:     {b_stats.get('is_watertight', False)}  -->  {a_stats.get('is_watertight', False)}")
    lines.append(f"  Open holes:     {b_stats.get('holes', 0)}  -->  {a_stats.get('holes', 0)}")
    lines.append(f"  Open edges:     {b_stats.get('boundary_edges', 0)}  -->  {a_stats.get('boundary_edges', 0)}")
    lines.append(f"  Non-manifold:   {b_stats.get('nonmanifold_edges', 0)}  -->  {a_stats.get('nonmanifold_edges', 0)}")
    lines.append(f"  Degenerate:     {b_stats.get('degenerate_faces', 0)}  -->  {a_stats.get('degenerate_faces', 0)}")
    lines.append(f"  Duplicate:      {b_stats.get('duplicate_faces', 0)}  -->  {a_stats.get('duplicate_faces', 0)}")
    lines.append("=" * 60)

    return "\n".join(lines)


def format_reduction_report(mesh_before: trimesh.Trimesh, mesh_after: trimesh.Trimesh, info: dict) -> str:
    """Creates a report detailing mesh simplification/decimation."""
    init_faces = info.get("initial_faces", len(mesh_before.faces))
    final_faces = info.get("final_faces", len(mesh_after.faces))
    pct = info.get("reduction_percentage", 0.0)
    method = info.get("method_used", "none")

    lines = [
        "=" * 50,
        "  GEEKATPLAY MESHWRIGHT - REDUCTION REPORT",
        "=" * 50,
        f"Method:             {method}",
        f"Initial Triangles:  {init_faces:,}",
        f"Reduced Triangles:  {final_faces:,}",
        f"Triangles Removed:  {(init_faces - final_faces):,}",
        f"Reduction Ratio:    {pct:.1f}% reduced",
        f"Watertight Solid:   {bool(mesh_after.is_watertight)}",
        "=" * 50
    ]
    return "\n".join(lines)


def render_mesh_to_np(mesh: trimesh.Trimesh, width: int = 512, height: int = 512,
                      tint_rgb: tuple = (0.22, 0.55, 0.75)) -> np.ndarray:
    """
    Pure CPU/NumPy software rasterizer that produces a clean shaded 3D preview of any mesh.
    Requires no OpenGL or GPU drivers, guaranteeing 100% reliability in headless / worker environments.
    Returns float32 NumPy array with shape (height, width, 3) in [0.0, 1.0].
    """
    img = np.full((height, width, 3), 0.12, dtype=np.float32)  # Dark slate background
    # Add subtle vertical vignette
    vignette = np.linspace(0.14, 0.08, height)[:, None, None]
    img = img * (vignette / 0.12)

    if len(mesh.faces) == 0 or len(mesh.vertices) == 0:
        return img

    verts = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(mesh.faces, dtype=np.int32)

    # Center and normalize bounds
    min_pt = verts.min(axis=0)
    max_pt = verts.max(axis=0)
    center = (min_pt + max_pt) / 2.0
    scale = np.max(max_pt - min_pt)
    if scale <= 1e-6:
        scale = 1.0

    centered = (verts - center) / (scale * 1.3)

    # Isometric rotation matrix (Yaw ~35 deg, Pitch ~25 deg)
    yaw = math.radians(35.0)
    pitch = math.radians(25.0)
    cy, sy = math.cos(yaw), math.sin(yaw)
    cp, sp = math.cos(pitch), math.sin(pitch)

    rot_y = np.array([
        [cy, 0, sy],
        [0, 1, 0],
        [-sy, 0, cy]
    ])
    rot_x = np.array([
        [1, 0, 0],
        [0, cp, -sp],
        [0, sp, cp]
    ])
    rot = rot_x @ rot_y
    rot_verts = centered @ rot.T

    # Map to screen space [0, width], [0, height]
    sx = ((rot_verts[:, 0] + 0.5) * (width - 1)).clip(0, width - 1)
    sy = ((-rot_verts[:, 1] + 0.5) * (height - 1)).clip(0, height - 1)
    sz = rot_verts[:, 2]

    # Compute face normals and simple directional lighting
    tri_verts = rot_verts[faces]  # (F, 3, 3)
    v0, v1, v2 = tri_verts[:, 0], tri_verts[:, 1], tri_verts[:, 2]
    cross = np.cross(v1 - v0, v2 - v0)
    norm = np.linalg.norm(cross, axis=1, keepdims=True)
    norm[norm == 0] = 1e-6
    fnormals = cross / norm  # (F, 3)

    # Light direction pointing from top-front-right
    light_dir = np.array([0.4, 0.6, 0.7])
    light_dir /= np.linalg.norm(light_dir)

    # Shading: ambient + diffuse + rim
    diffuse = np.clip(fnormals @ light_dir, 0.0, 1.0)
    ambient = 0.25
    rim = np.clip(1.0 - np.abs(fnormals[:, 2]), 0.0, 1.0) ** 3 * 0.25
    intensity = np.clip(ambient + 0.65 * diffuse + rim, 0.0, 1.0)

    # Sort faces from back to front
    face_z = (sz[faces[:, 0]] + sz[faces[:, 1]] + sz[faces[:, 2]]) / 3.0
    order = np.argsort(face_z)

    z_buffer = np.full((height, width), -1e9, dtype=np.float32)

    # Subsample if face count is large for rasterization responsiveness
    if len(order) > 40000:
        step = int(math.ceil(len(order) / 40000))
        order = order[::step]

    base_r, base_g, base_b = tint_rgb

    for f_idx in order:
        p0 = (int(sx[faces[f_idx, 0]]), int(sy[faces[f_idx, 0]]))
        p1 = (int(sx[faces[f_idx, 1]]), int(sy[faces[f_idx, 1]]))
        p2 = (int(sx[faces[f_idx, 2]]), int(sy[faces[f_idx, 2]]))

        min_x = max(0, min(p0[0], p1[0], p2[0]))
        max_x = min(width - 1, max(p0[0], p1[0], p2[0]))
        min_y = max(0, min(p0[1], p1[1], p2[1]))
        max_y = min(height - 1, max(p0[1], p1[1], p2[1]))
        if min_x > max_x or min_y > max_y:
            continue

        fz = face_z[f_idx]
        ill = intensity[f_idx]
        col = (base_r * ill, base_g * ill, base_b * ill)

        x_coords, y_coords = np.meshgrid(np.arange(min_x, max_x + 1), np.arange(min_y, max_y + 1))
        x_flat = x_coords.ravel()
        y_flat = y_coords.ravel()

        x0, y0 = p0
        x1, y1 = p1
        x2, y2 = p2

        denom = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
        if denom == 0:
            continue

        w0 = ((y1 - y2) * (x_flat - x2) + (x2 - x1) * (y_flat - y2)) / denom
        w1 = ((y2 - y0) * (x_flat - x2) + (x0 - x2) * (y_flat - y2)) / denom
        w2 = 1.0 - w0 - w1

        inside = (w0 >= 0.0) & (w1 >= 0.0) & (w2 >= 0.0)
        if not np.any(inside):
            continue

        ix = x_flat[inside]
        iy = y_flat[inside]

        closer = fz > z_buffer[iy, ix]
        if np.any(closer):
            cx = ix[closer]
            cy_idx = iy[closer]
            z_buffer[cy_idx, cx] = fz
            img[cy_idx, cx] = col

    return np.clip(img, 0.0, 1.0).astype(np.float32)


def render_comparison_preview(mesh_before: trimesh.Trimesh, mesh_after: trimesh.Trimesh) -> np.ndarray:
    """Renders side-by-side comparison image: Before (amber/coral tint) vs After (cyan/emerald tint)."""
    h, w = 512, 512
    img_before = render_mesh_to_np(mesh_before, width=w, height=h, tint_rgb=(0.85, 0.35, 0.25))
    img_after = render_mesh_to_np(mesh_after, width=w, height=h, tint_rgb=(0.20, 0.72, 0.60))

    sep = np.full((h, 4, 3), 0.35, dtype=np.float32)
    combined = np.concatenate([img_before, sep, img_after], axis=1)

    return combined


def np_to_comfy_tensor(img_np: np.ndarray):
    """Converts a (H, W, 3) float32 numpy image into a ComfyUI (1, H, W, 3) torch.Tensor."""
    if not HAS_TORCH:
        return img_np
    tensor = torch.from_numpy(img_np).unsqueeze(0).float()
    return tensor
