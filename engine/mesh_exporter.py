"""Shared mesh export helpers for every file format offered by the desktop UI."""
import os

import numpy as np
import trimesh
from trimesh.grouping import group_rows

from engine.texture import uv_channel as UV

# Formats that can actually carry texture coordinates. STL, OFF and 3MF cannot, so
# a textured model exported to those is silently geometry-only — which is correct,
# and is what a slicer wants anyway.
UV_CAPABLE = ("obj", "glb", "gltf")


# File types are the names understood by trimesh's exporter.  FBX is
# intentionally absent: the project can import it through ufbx, but neither
# trimesh nor the bundled MeshLab build can create a valid FBX file.
EXPORT_FORMATS = {
    "stl": ("STL", "stl"),
    "obj": ("OBJ", "obj"),
    "ply": ("PLY", "ply"),
    "off": ("OFF", "off"),
    "glb": ("GLB", "glb"),
    "gltf": ("glTF", "gltf"),
    "3mf": ("3MF", "3mf"),
}

# A closed shell of wall thickness t has volume ~= area/2 * t, so 2V/A is a fair
# estimate of the average wall.  Below this, a slicer produces walls only and no
# infill — which is exactly what "my print came out as a thin shell" looks like.
THIN_WALL_MM = 1.2
# Small parts are legitimately thin, so only judge the wall of something big enough.
WALL_CHECK_MIN_SIZE_MM = 10.0


def solidity_report(mesh: trimesh.Trimesh) -> dict:
    """
    Does this mesh describe a printable solid?

    Slicers need a closed volume. An open surface is sliced as a single-wall
    shell — one perimeter thick, no infill, no matter what the slicer settings
    say — so the answer belongs on screen at export time, not after a 6-hour print.
    """
    watertight = bool(mesh.is_watertight)
    volume_mm3 = float(mesh.volume) if watertight else 0.0
    area_mm2 = float(mesh.area)
    extents = mesh.extents if mesh.extents is not None else np.zeros(3)
    max_dim = float(np.max(extents)) if len(extents) else 0.0

    boundary_edges = 0
    if not watertight and len(mesh.faces):
        try:
            boundary_edges = len(group_rows(mesh.edges_sorted, require_count=1))
        except Exception:
            boundary_edges = 0

    wall_mm = None
    if watertight and area_mm2 > 0:
        wall_mm = 2.0 * abs(volume_mm3) / area_mm2

    warnings = []
    if not watertight:
        warnings.append(
            f"This mesh is not a closed solid — {boundary_edges:,} edges are open. "
            "A slicer treats an open surface as a thin shell: one perimeter thick, "
            "no infill. Run Repair before exporting."
        )
    elif abs(volume_mm3) < 1e-6:
        warnings.append(
            "This mesh encloses no volume — the surfaces sit on top of each other. "
            "A slicer will produce a shell with nothing inside it."
        )
    elif wall_mm is not None and wall_mm < THIN_WALL_MM and max_dim >= WALL_CHECK_MIN_SIZE_MM:
        warnings.append(
            f"The model is hollow with walls averaging {wall_mm:.2f} mm. That is thinner "
            "than most nozzles can fill, so the slicer will print walls only and no infill. "
            "This is correct if the model really is a hollow shell."
        )

    return {
        "is_solid": bool(watertight and abs(volume_mm3) > 1e-6),
        "is_watertight": watertight,
        "boundary_edges": boundary_edges,
        "volume_cm3": round(abs(volume_mm3) / 1000.0, 3),
        "avg_wall_mm": round(wall_mm, 3) if wall_mm is not None else None,
        "warnings": warnings,
    }


def prepare_for_export(mesh: trimesh.Trimesh, scale_unit: str = "mm", align_origin: bool = True) -> trimesh.Trimesh:
    """Copy a mesh, convert its source units to mm, straighten normals, ground it."""
    export_mesh = mesh.copy()
    scale_factor = {"mm": 1.0, "cm": 10.0, "in": 25.4}[scale_unit]
    if scale_factor != 1.0:
        export_mesh.apply_scale(scale_factor)

    # An inside-out or inconsistently wound mesh slices as a cavity, so fix the
    # winding on the way out. Geometry is untouched; only face order changes.
    try:
        if not export_mesh.is_winding_consistent or (export_mesh.is_watertight and export_mesh.volume < 0):
            export_mesh.fix_normals()
    except Exception:
        pass

    if align_origin and export_mesh.bounds is not None:
        bounds = export_mesh.bounds
        export_mesh.apply_translation([
            -(bounds[0][0] + bounds[1][0]) / 2.0,
            -(bounds[0][1] + bounds[1][1]) / 2.0,
            -bounds[0][2],
        ])
    return export_mesh


def export_to_format(mesh: trimesh.Trimesh, output_path: str, export_format: str = "stl",
                     scale_unit: str = "mm", align_origin: bool = True,
                     corner_uv=None, material=None) -> dict:
    """
    Export a prepared mesh in one of the formats supported by trimesh.

    For OBJ and glTF the per-corner UV channel is expanded to per-vertex first, so
    the vertex split that a texture needs happens in the file and never in the mesh
    Meshwright is holding.
    """
    label, file_type = EXPORT_FORMATS[export_format]

    textured = export_format in UV_CAPABLE and UV.is_valid(mesh, corner_uv)
    source = UV.to_textured_mesh(mesh, corner_uv, material=material) if textured else mesh
    export_mesh = prepare_for_export(source, scale_unit, align_origin)
    if export_format == "stl" and output_path.lower().endswith(".stl_ascii"):
        file_type = "stl_ascii"

    out_dir = os.path.dirname(output_path)
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir, exist_ok=True)
    export_mesh.export(output_path, file_type=file_type)

    solidity = solidity_report(export_mesh)
    file_size_bytes = os.path.getsize(output_path)
    bounds = export_mesh.bounds.tolist() if export_mesh.bounds is not None else [[0, 0, 0], [0, 0, 0]]
    extents = export_mesh.extents.tolist() if export_mesh.extents is not None else [0, 0, 0]
    return {
        "output_path": output_path,
        "filename": os.path.basename(output_path),
        "format": label,
        "file_size_mb": round(file_size_bytes / (1024.0 * 1024.0), 2),
        "file_size_bytes": file_size_bytes,
        "vertex_count": len(export_mesh.vertices),
        "face_count": len(export_mesh.faces),
        "has_uv": bool(textured),
        "texture_channels": int(sum(
            getattr(material, name, None) is not None
            for name in ("baseColorTexture", "normalTexture", "occlusionTexture", "metallicRoughnessTexture")
        )) if textured and material is not None else 0,
        "is_watertight": solidity["is_watertight"],
        "is_solid": solidity["is_solid"],
        "avg_wall_mm": solidity["avg_wall_mm"],
        "volume_cm3": solidity["volume_cm3"],
        "warnings": solidity["warnings"],
        "dimensions_mm": [round(x, 2) for x in extents],
        "bounds_min": [round(x, 2) for x in bounds[0]],
        "bounds_max": [round(x, 2) for x in bounds[1]],
    }
