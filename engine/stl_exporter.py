import trimesh

from engine.mesh_exporter import export_to_format


def export_to_stl(mesh: trimesh.Trimesh, output_path: str, scale_unit: str = "mm", align_origin: bool = True) -> dict:
    """
    Prepares and exports the mesh as a print-ready binary STL file.
    - scale_unit: 'mm', 'cm' (multiplies by 10), or 'in' (multiplies by 25.4)
    - align_origin: centers X and Y at 0 and sets lowest Z bound at Z=0 (resting flat on build plate).
    """
    return export_to_format(mesh, output_path, "stl", scale_unit, align_origin)
