"""
PBR Material Manager & Export/Bake System — Geekatplay Studio
Manages texture sets, Photoshop round-trip export/re-import,
and binary glTF (.glb) PBR material baking.
"""
import base64
import io
import os

import numpy as np
import trimesh
from PIL import Image

from . import uv_channel as UV
from .seam_fixer import DEFAULT_PADDING_PX, dilate_map_set
from .uv_unwrapper import export_uv_wireframe_image


class MaterialManager:
    """
    Holds the active PBR material set for a model and handles external editing round-trips.
    """
    def __init__(self):
        self.albedo: Image.Image | None = None
        self.normal: Image.Image | None = None
        self.roughness: Image.Image | None = None
        self.metallic: Image.Image | None = None
        self.ao: Image.Image | None = None
        self.height: Image.Image | None = None
        self.last_export_dir: str | None = None
        # Bumped whenever a channel changes. Encoding six maps to base64 costs a
        # third of a second and a dozen megabytes across the UI bridge, so the
        # result of every operation carries this number instead of the images, and
        # the UI re-fetches only when it moves.
        self.version: int = 0

    def _touch(self):
        self.version += 1

    def clear(self):
        self.albedo = None
        self.normal = None
        self.roughness = None
        self.metallic = None
        self.ao = None
        self.height = None
        self.last_export_dir = None
        self._touch()

    def has_textures(self) -> bool:
        return self.albedo is not None

    @property
    def maps(self) -> dict:
        return {
            "albedo": self.albedo,
            "normal": self.normal,
            "roughness": self.roughness,
            "metallic": self.metallic,
            "ao": self.ao,
            "height": self.height,
        }

    def channels(self) -> list:
        """Names of the channels that actually hold an image."""
        return [name for name, img in self.maps.items() if img is not None]

    def summary(self) -> dict:
        """What the UI needs on every operation: no pixels, just what changed."""
        return {
            "has_textures": self.has_textures(),
            "version": self.version,
            "channels": self.channels(),
        }

    def set_material_maps(self, maps: dict):
        """Sets texture channels from a dictionary of PIL Images."""
        if maps.get("albedo"):
            self.albedo = maps["albedo"].convert("RGB")
        if maps.get("normal"):
            self.normal = maps["normal"].convert("RGB")
        if maps.get("roughness"):
            self.roughness = maps["roughness"].convert("L")
        if maps.get("metallic"):
            self.metallic = maps["metallic"].convert("L")
        if maps.get("ao"):
            self.ao = maps["ao"].convert("L")
        if maps.get("height"):
            self.height = maps["height"].convert("L")
        self._touch()

    def export_texture_pack(self, output_dir: str, mesh: trimesh.Trimesh | None = None,
                            corner_uv=None, padding_px: int = DEFAULT_PADDING_PX) -> dict:
        """
        Exports all texture channels as separate PNG files, plus a transparent UV guide for Photoshop.

        The gutters between charts are padded on the way out so the maps survive
        mipmapping and bilinear filtering without dark fringes along the seams —
        see seam_fixer.py. The maps held in memory are not touched.
        """
        os.makedirs(output_dir, exist_ok=True)
        self.last_export_dir = output_dir

        written = dilate_map_set(self.maps, corner_uv, padding_px=padding_px) \
            if UV.is_valid(mesh, corner_uv) else self.maps

        exported = {}
        mapping = {f"{name}.png": img for name, img in written.items()}

        for filename, img in mapping.items():
            if img is not None:
                path = os.path.join(output_dir, filename)
                img.save(path, format="PNG")
                exported[filename] = path

        # Transparent UV guide to open as a layer in Photoshop / GIMP.
        if mesh is not None and UV.is_valid(mesh, corner_uv):
            guide = UV.to_textured_mesh(mesh, corner_uv)
            uv_img = export_uv_wireframe_image(guide, width=2048, height=2048)
            wire_path = os.path.join(output_dir, "uv_wireframe.png")
            uv_img.save(wire_path, format="PNG")
            exported["uv_wireframe.png"] = wire_path

        return {
            "directory": output_dir,
            "files": exported,
            "count": len(exported)
        }

    def reload_texture_pack(self, input_dir: str | None = None) -> dict:
        """
        Reloads modified textures from disk (e.g. after editing in Photoshop).
        """
        scan_dir = input_dir or self.last_export_dir
        if not scan_dir or not os.path.isdir(scan_dir):
            raise FileNotFoundError(f"Texture directory not found: {scan_dir}")

        reloaded = []
        for file in os.listdir(scan_dir):
            lower = file.lower()
            full_path = os.path.join(scan_dir, file)
            if not os.path.isfile(full_path):
                continue

            try:
                if lower.startswith("albedo") or lower.startswith("diffuse") or lower.startswith("color"):
                    self.albedo = Image.open(full_path).convert("RGB")
                    reloaded.append("albedo")
                elif lower.startswith("normal"):
                    self.normal = Image.open(full_path).convert("RGB")
                    reloaded.append("normal")
                elif lower.startswith("roughness"):
                    self.roughness = Image.open(full_path).convert("L")
                    reloaded.append("roughness")
                elif lower.startswith("metallic") or lower.startswith("metalness"):
                    self.metallic = Image.open(full_path).convert("L")
                    reloaded.append("metallic")
                elif lower.startswith("ao") or lower.startswith("occlusion"):
                    self.ao = Image.open(full_path).convert("L")
                    reloaded.append("ao")
                elif lower.startswith("height") or lower.startswith("displacement"):
                    self.height = Image.open(full_path).convert("L")
                    reloaded.append("height")
            except Exception as e:
                print(f"[MaterialManager] Warning loading {file}: {e}")

        self._touch()
        return {
            "success": True,
            "reloaded_channels": sorted(set(reloaded)),
            "directory": scan_dir
        }

    def _pack_metallic_roughness(self, maps: dict | None = None) -> Image.Image:
        """
        glTF 2.0 PBR standard:
          Green channel = Roughness
          Blue channel  = Metallic
        """
        maps = maps if maps is not None else self.maps
        roughness, metallic, albedo = maps["roughness"], maps["metallic"], maps["albedo"]

        w, h = 512, 512
        if roughness:
            w, h = roughness.size
        elif albedo:
            w, h = albedo.size

        r_plane = np.zeros((h, w), dtype=np.uint8)  # Red unused
        g_plane = np.array(roughness.resize((w, h)), dtype=np.uint8) if roughness else np.full((h, w), 128, dtype=np.uint8)
        b_plane = np.array(metallic.resize((w, h)), dtype=np.uint8) if metallic else np.zeros((h, w), dtype=np.uint8)

        mr_np = np.stack([r_plane, g_plane, b_plane], axis=-1)
        return Image.fromarray(mr_np, mode="RGB")

    def as_trimesh_material(self, corner_uv=None, padding_px: int = DEFAULT_PADDING_PX):
        """
        The active maps as a glTF 2.0 PBR material, or None when there is nothing to
        write. Only the channels that actually exist are attached, so an albedo-only
        model is not given an invented metallic-roughness map.

        Pass `corner_uv` to pad the chart gutters first, which is what an exported
        file wants; leave it out for a quick in-memory material.
        """
        if not any(self.maps.values()):
            return None

        maps = dilate_map_set(self.maps, corner_uv, padding_px=padding_px) if corner_uv is not None else self.maps

        kwargs = {}
        if maps["albedo"] is not None:
            kwargs["baseColorTexture"] = maps["albedo"]
        if maps["normal"] is not None:
            kwargs["normalTexture"] = maps["normal"]
        if maps["ao"] is not None:
            kwargs["occlusionTexture"] = maps["ao"]
        if maps["roughness"] is not None or maps["metallic"] is not None:
            kwargs["metallicRoughnessTexture"] = self._pack_metallic_roughness(maps)
        if not kwargs:
            return None
        return trimesh.visual.material.PBRMaterial(**kwargs)

    def bake_and_export_glb(self, mesh: trimesh.Trimesh, output_path: str, corner_uv=None) -> dict:
        """
        Bakes active PBR materials into a single, portable binary glTF (.glb) file.

        The welded mesh is split into per-vertex UVs here, at the file boundary,
        which is the only place a glTF needs that form.
        """
        if not UV.is_valid(mesh, corner_uv):
            raise ValueError("This model has no UV coordinates yet. Run Unwrap UVs first.")

        # Pad the gutters before embedding: a baked GLB is what other people open,
        # and seam fringes are far harder to explain than to prevent.
        pbr = self.as_trimesh_material(corner_uv=corner_uv)
        out_mesh = UV.to_textured_mesh(mesh, corner_uv, material=pbr)

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        glb_data = trimesh.exchange.gltf.export_glb(out_mesh)

        with open(output_path, "wb") as f:
            f.write(glb_data)

        size_mb = round(os.path.getsize(output_path) / 1e6, 2)
        return {
            "success": True,
            "path": output_path,
            "size_mb": size_mb,
            "format": "glb",
            "has_pbr": pbr is not None,
            "vertices": len(out_mesh.vertices),
            "faces": len(out_mesh.faces),
        }

    def get_ui_payload(self) -> dict:
        """
        Encode the maps as base64 data URLs for the Three.js viewport and 2D canvas.

        This is the expensive one — a couple of seconds and a dozen megabytes for a
        full 2048px set — so it is fetched on demand against `version`, never
        included in the result of an ordinary mesh operation.
        """
        def _to_b64(img: Image.Image | None, max_size: int = 1024) -> str | None:
            if img is None:
                return None
            copy_img = img.copy()
            if max(copy_img.size) > max_size:
                copy_img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            copy_img.save(buf, format="PNG", optimize=True)
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            return f"data:image/png;base64,{b64}"

        return {
            "has_textures": self.has_textures(),
            "version": self.version,
            "albedo": _to_b64(self.albedo),
            "normal": _to_b64(self.normal),
            "roughness": _to_b64(self.roughness),
            "metallic": _to_b64(self.metallic),
            "ao": _to_b64(self.ao),
            "height": _to_b64(self.height),
        }
