import trimesh


def extract_color_information(mesh: trimesh.Trimesh) -> dict:
    """
    Extracts vertex colors, face colors, or texture maps if present in the model.
    Prepares data for multi-color 3D printing export (e.g. 3MF format).
    """
    has_vertex_colors = hasattr(mesh.visual, 'vertex_colors') and mesh.visual.vertex_colors is not None and len(mesh.visual.vertex_colors) > 0
    has_face_colors = hasattr(mesh.visual, 'face_colors') and mesh.visual.face_colors is not None and len(mesh.visual.face_colors) > 0
    has_texture = hasattr(mesh.visual, 'uv') and mesh.visual.uv is not None and len(mesh.visual.uv) > 0

    return {
        "has_vertex_colors": has_vertex_colors,
        "has_face_colors": has_face_colors,
        "has_texture": has_texture,
        "color_mode": "vertex" if has_vertex_colors else ("face" if has_face_colors else ("texture" if has_texture else "none"))
    }


def export_to_3mf(mesh: trimesh.Trimesh, output_path: str) -> dict:
    """
    Exports the model with color metadata to 3MF format for multi-material printers (Bambu AMS, Prusa MMU).
    """
    mesh.export(output_path, file_type='3mf')
    return {
        "output_path": output_path,
        "format": "3MF",
        "color_supported": True
    }
