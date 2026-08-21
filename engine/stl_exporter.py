import os
import trimesh


def export_to_stl(mesh: trimesh.Trimesh, output_path: str, scale_unit: str = "mm", align_origin: bool = True) -> dict:
    """
    Prepares and exports the mesh as a print-ready binary STL file.
    - scale_unit: 'mm', 'cm' (multiplies by 10), or 'in' (multiplies by 25.4)
    - align_origin: centers X and Y at 0 and sets lowest Z bound at Z=0 (resting flat on build plate).
    """
    export_mesh = mesh.copy()

    # Apply scaling if needed
    scale_factor = 1.0
    if scale_unit == "cm":
        scale_factor = 10.0
    elif scale_unit == "in":
        scale_factor = 25.4

    if scale_factor != 1.0:
        export_mesh.apply_scale(scale_factor)

    # Align to build plate: center X and Y, set Z_min = 0
    if align_origin and export_mesh.bounds is not None:
        bounds = export_mesh.bounds
        center_x = (bounds[0][0] + bounds[1][0]) / 2.0
        center_y = (bounds[0][1] + bounds[1][1]) / 2.0
        min_z = bounds[0][2]

        translation = [-center_x, -center_y, -min_z]
        export_mesh.apply_translation(translation)

    # Ensure output folder exists
    out_dir = os.path.dirname(output_path)
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    # Export to binary STL
    export_mesh.export(output_path, file_type='stl_ascii' if output_path.endswith('.stl_ascii') else 'stl')

    file_size_bytes = os.path.getsize(output_path)
    file_size_mb = file_size_bytes / (1024.0 * 1024.0)

    bounds = export_mesh.bounds.tolist() if export_mesh.bounds is not None else [[0, 0, 0], [0, 0, 0]]
    extents = export_mesh.extents.tolist() if export_mesh.extents is not None else [0, 0, 0]

    return {
        "output_path": output_path,
        "filename": os.path.basename(output_path),
        "file_size_mb": round(file_size_mb, 2),
        "file_size_bytes": file_size_bytes,
        "vertex_count": int(len(export_mesh.vertices)),
        "face_count": int(len(export_mesh.faces)),
        "is_watertight": bool(export_mesh.is_watertight),
        "dimensions_mm": [round(x, 2) for x in extents],
        "bounds_min": [round(x, 2) for x in bounds[0]],
        "bounds_max": [round(x, 2) for x in bounds[1]]
    }
