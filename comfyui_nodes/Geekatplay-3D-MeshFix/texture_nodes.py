"""
Meshwright ComfyUI Texture & PBR Nodes — Geekatplay Studio
Author: Vladimir Chopine

Provides UV Unwrapping, Image-to-PBR map derivation, gutter dilation,
and GLB material baking as native ComfyUI nodes.
"""
import os
import time

import numpy as np
from PIL import Image

try:
    import torch
    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False

from engine.texture import (
    MaterialManager,
    dilate_map_set,
    from_vertex_uv,
    generate_pbr_material,
    unwrap_mesh,
)

from .utils import to_trimesh


def _pil_to_tensor(img: Image.Image):
    """Convert PIL Image to ComfyUI IMAGE tensor [1, H, W, 3] (float32, 0..1)."""
    rgb = img.convert("RGB")
    arr = np.array(rgb).astype(np.float32) / 255.0
    arr = np.expand_dims(arr, axis=0)
    if HAS_TORCH:
        return torch.from_numpy(arr)
    return arr


def _tensor_to_pil(tensor) -> Image.Image:
    """Convert ComfyUI IMAGE tensor [B, H, W, C] to PIL Image."""
    if tensor is None:
        return None
    if isinstance(tensor, Image.Image):
        return tensor
    if hasattr(tensor, "cpu"):
        arr = tensor.cpu().numpy()
    else:
        arr = np.array(tensor)
    if len(arr.shape) == 4:
        arr = arr[0]
    arr = (np.clip(arr, 0.0, 1.0) * 255.0).astype(np.uint8)
    return Image.fromarray(arr)


class MeshwrightUVUnwrap:
    """Unwraps a 3D mesh into non-overlapping 2D UV charts using xatlas."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "mesh": ("MESH",),
            }
        }

    RETURN_TYPES = ("MESH", "INT", "INT", "STRING")
    RETURN_NAMES = ("mesh", "island_count", "seam_edges", "uv_info")
    FUNCTION = "unwrap"
    CATEGORY = "Geekatplay/3D/Texture"

    def unwrap(self, mesh):
        tm = to_trimesh(mesh)
        unwrapped, stats = unwrap_mesh(tm)
        info = f"Unwrapped: {stats['islands']} charts/islands, {stats['seam_edges']} seam boundary edges"
        return (unwrapped, stats["islands"], stats["seam_edges"], info)


class MeshwrightImageToPBR:
    """Derives complete PBR material maps (Normal, Roughness, Metalness, AO) from a single color image."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "normal_strength": ("FLOAT", {"default": 2.0, "min": 0.1, "max": 10.0, "step": 0.1}),
                "opengl_normal": ("BOOLEAN", {"default": True}),
                "roughness_min": ("FLOAT", {"default": 0.2, "min": 0.0, "max": 1.0, "step": 0.05}),
                "roughness_max": ("FLOAT", {"default": 0.85, "min": 0.0, "max": 1.0, "step": 0.05}),
                "metallic": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1.0, "step": 0.05}),
                "ao_intensity": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 2.0, "step": 0.1}),
            }
        }

    RETURN_TYPES = ("PBR_MATERIAL", "IMAGE", "IMAGE", "IMAGE", "IMAGE", "IMAGE")
    RETURN_NAMES = ("pbr_material", "albedo", "normal", "roughness", "metallic", "ao")
    FUNCTION = "generate_pbr"
    CATEGORY = "Geekatplay/3D/Texture"

    def generate_pbr(self, image, normal_strength, opengl_normal, roughness_min, roughness_max, metallic, ao_intensity):
        pil_img = _tensor_to_pil(image)
        maps = generate_pbr_material(
            pil_img,
            normal_strength=normal_strength,
            opengl_normal=opengl_normal,
            min_roughness=roughness_min,
            max_roughness=roughness_max,
            metallic_amount=metallic,
            ao_intensity=ao_intensity
        )

        pbr_dict = {
            "albedo": maps["albedo"],
            "normal": maps["normal"],
            "roughness": maps["roughness"],
            "metallic": maps["metallic"],
            "ao": maps["ao"],
        }

        albedo_t = _pil_to_tensor(maps["albedo"])
        normal_t = _pil_to_tensor(maps["normal"])
        rough_t = _pil_to_tensor(maps["roughness"])
        metal_t = _pil_to_tensor(maps["metallic"])
        ao_t = _pil_to_tensor(maps["ao"])

        return (pbr_dict, albedo_t, normal_t, rough_t, metal_t, ao_t)


class MeshwrightApplyTextures:
    """Binds PBR material maps and UVs to a mesh, optionally dilating gutters to eliminate seam artifacts."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "mesh": ("MESH",),
            },
            "optional": {
                "pbr_material": ("PBR_MATERIAL",),
                "albedo": ("IMAGE",),
                "normal": ("IMAGE",),
                "roughness": ("IMAGE",),
                "metallic": ("IMAGE",),
                "dilate_gutters": ("BOOLEAN", {"default": True}),
            }
        }

    RETURN_TYPES = ("MESH", "PBR_MATERIAL", "STRING")
    RETURN_NAMES = ("mesh", "pbr_material", "summary")
    FUNCTION = "apply"
    CATEGORY = "Geekatplay/3D/Texture"

    def apply(self, mesh, pbr_material=None, albedo=None, normal=None, roughness=None, metallic=None, dilate_gutters=True):
        tm = to_trimesh(mesh)

        # Ensure mesh has UVs
        if not hasattr(tm, "visual") or not hasattr(tm.visual, "uv") or tm.visual.uv is None:
            tm, _ = unwrap_mesh(tm)

        mat = dict(pbr_material) if pbr_material else {}
        if albedo is not None: mat["albedo"] = _tensor_to_pil(albedo)
        if normal is not None: mat["normal"] = _tensor_to_pil(normal)
        if roughness is not None: mat["roughness"] = _tensor_to_pil(roughness)
        if metallic is not None: mat["metallic"] = _tensor_to_pil(metallic)

        if dilate_gutters and getattr(getattr(tm, "visual", None), "uv", None) is not None:
            # One UV rasterisation shared across every channel.
            mat = dilate_map_set(mat, from_vertex_uv(tm.faces, tm.visual.uv))

        summary = f"Applied {len(mat)} material channels to mesh ({len(tm.faces):,} faces)"
        return (tm, mat, summary)


class MeshwrightSaveGLB:
    """Exports a textured 3D mesh as a self-contained glTF 2.0 Binary (.glb) with embedded PBR maps."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "mesh": ("MESH",),
                "filename_prefix": ("STRING", {"default": "Meshwright_PBR"}),
            },
            "optional": {
                "pbr_material": ("PBR_MATERIAL",),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("glb_path",)
    OUTPUT_NODE = True
    FUNCTION = "save"
    CATEGORY = "Geekatplay/3D/Texture"

    def save(self, mesh, filename_prefix="Meshwright_PBR", pbr_material=None):
        tm = to_trimesh(mesh)

        # Locate output directory
        out_dir = os.path.join(os.getcwd(), "output")
        try:
            import folder_paths
            out_dir = folder_paths.get_output_directory()
        except Exception:
            pass
        os.makedirs(out_dir, exist_ok=True)

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        out_filename = f"{filename_prefix}_{timestamp}.glb"
        out_path = os.path.join(out_dir, out_filename)

        manager = MaterialManager()
        if pbr_material:
            manager.set_material_maps(pbr_material)

        # ComfyUI hands meshes around in the vertex-split form; the material engine
        # works in per-corner UVs, so convert (unwrapping first if there is nothing).
        corner_uv = from_vertex_uv(tm.faces, getattr(getattr(tm, "visual", None), "uv", None))
        if corner_uv is None:
            tm, _ = unwrap_mesh(tm)
            corner_uv = from_vertex_uv(tm.faces, tm.visual.uv)

        res = manager.bake_and_export_glb(tm, out_path, corner_uv)
        return (out_path,)


TEXTURE_NODE_CLASS_MAPPINGS = {
    "MeshwrightUVUnwrap": MeshwrightUVUnwrap,
    "MeshwrightImageToPBR": MeshwrightImageToPBR,
    "MeshwrightApplyTextures": MeshwrightApplyTextures,
    "MeshwrightSaveGLB": MeshwrightSaveGLB,
}

TEXTURE_NODE_DISPLAY_NAME_MAPPINGS = {
    "MeshwrightUVUnwrap": "Meshwright 3D UV Unwrap",
    "MeshwrightImageToPBR": "Meshwright Image to PBR Textures",
    "MeshwrightApplyTextures": "Meshwright Apply PBR Textures",
    "MeshwrightSaveGLB": "Meshwright Save Baked GLB",
}
