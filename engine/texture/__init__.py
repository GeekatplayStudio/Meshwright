"""
Texture and PBR Engine — Geekatplay Studio
"""
from .companion_detector import (
    find_and_load_companion_textures,
    find_companion_texture_files,
)
from .material_manager import MaterialManager
from .pbr_generator import (
    generate_normal_map,
    generate_pbr_material,
    generate_roughness_map,
)
from .seam_fixer import dilate_map_set, dilate_texture_gutters, make_tileable_seamless
from .uv_channel import (
    chart_labels,
    from_vertex_uv,
    is_valid,
    seam_edge_count,
    split_for_gpu,
    to_textured_mesh,
    transfer,
    uv_of,
    wireframe_lines,
)
from .uv_unwrapper import (
    export_uv_wireframe_image,
    unwrap_corner_uv,
    unwrap_mesh,
)

__all__ = [
    "MaterialManager",
    "chart_labels",
    "dilate_map_set",
    "dilate_texture_gutters",
    "export_uv_wireframe_image",
    "find_and_load_companion_textures",
    "find_companion_texture_files",
    "from_vertex_uv",
    "generate_normal_map",
    "generate_pbr_material",
    "generate_roughness_map",
    "is_valid",
    "make_tileable_seamless",
    "seam_edge_count",
    "split_for_gpu",
    "to_textured_mesh",
    "transfer",
    "unwrap_corner_uv",
    "unwrap_mesh",
    "uv_of",
    "wireframe_lines",
]
