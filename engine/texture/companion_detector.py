"""
Smart Companion & Embedded Texture Detector — Geekatplay Studio
Author: Vladimir Chopine

Automatically discovers and maps companion PBR texture files (Meshy AI,
Tripo, Sketchfab, Blender, etc.) located alongside 3D models or embedded within them.
"""
import os
import re

import trimesh
from PIL import Image

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".tga", ".bmp"}

# Meshy, Hi3D and several other generators write a 2x2 stub into the FBX material and
# ship the real 2048px maps as files beside it. A stub is not a texture, and it must
# not be allowed to outrank the artwork it stands in for.
MIN_REAL_TEXTURE_PX = 16

CHANNEL_PATTERNS = {
    "normal": re.compile(r"(_|\b|-)(normal|norm|nor|nrm|n)(\.|$|_)", re.IGNORECASE),
    "roughness": re.compile(r"(_|\b|-)(roughness|rough|rgh|r)(\.|$|_)", re.IGNORECASE),
    "metallic": re.compile(r"(_|\b|-)(metallic|metalness|metal|met|m)(\.|$|_)", re.IGNORECASE),
    "ao": re.compile(r"(_|\b|-)(ao|occlusion|ambient|ambient_occlusion)(\.|$|_)", re.IGNORECASE),
    "height": re.compile(r"(_|\b|-)(height|disp|displacement|bump)(\.|$|_)", re.IGNORECASE),
    "orm": re.compile(r"(_|\b|-)(orm|occlusionroughnessmetallic)(\.|$|_)", re.IGNORECASE),
    # What Sketchfab, Blender and most glTF exporters actually write. It has to be
    # tried before "metallic" and "roughness" or it matches neither of them.
    "metallic_roughness": re.compile(
        r"(_|\b|-)(metallicroughness|metallic_roughness|metalroughness|metalrough|rma|mr)(\.|$|_)",
        re.IGNORECASE),
    "albedo": re.compile(r"(_|\b|-)(albedo|diffuse|basecolor|base_color|color|col|d)(\.|$|_)", re.IGNORECASE),
}


def find_companion_texture_files(model_path: str) -> dict[str, str]:
    """
    Scans the directory of a model file and standard subfolders to locate
    companion PBR texture map image files.
    """
    if not model_path or not os.path.exists(model_path):
        return {}

    model_dir = os.path.dirname(os.path.abspath(model_path))
    model_base = os.path.splitext(os.path.basename(model_path))[0].lower()

    # Search folders
    search_dirs = [
        model_dir,
        os.path.join(model_dir, "textures"),
        os.path.join(model_dir, "texture"),
        os.path.join(model_dir, "images"),
        os.path.join(model_dir, "materials"),
        os.path.join(model_dir, f"{model_base}.fbm"),
        os.path.join(model_dir, "model.fbm"),
    ]

    all_files = []
    for s_dir in search_dirs:
        if os.path.isdir(s_dir):
            try:
                for entry in os.listdir(s_dir):
                    ext = os.path.splitext(entry)[1].lower()
                    if ext in IMAGE_EXTS:
                        all_files.append((entry, os.path.join(s_dir, entry)))
            except Exception:
                pass

    found = {}
    remaining_files = []

    # Pass 1: Identify explicit channels (normal, roughness, metallic, ao, height, orm)
    for fname, fpath in all_files:
        name_no_ext = os.path.splitext(fname)[0].lower()
        matched = False
        for channel in ("normal", "orm", "metallic_roughness", "roughness", "metallic", "ao", "height"):
            if channel not in found and CHANNEL_PATTERNS[channel].search(name_no_ext):
                found[channel] = fpath
                matched = True
                break
        if not matched:
            remaining_files.append((fname, fpath))

    # Pass 2: Identify albedo / base color
    for fname, fpath in remaining_files:
        name_no_ext = os.path.splitext(fname)[0].lower()
        # Direct pattern match (e.g. *_albedo*, *_diffuse*, *_baseColor*)
        if "albedo" not in found and CHANNEL_PATTERNS["albedo"].search(name_no_ext):
            found["albedo"] = fpath
            break
        # Exact model name match (e.g. model.png next to model.fbx, as in Meshy AI)
        if "albedo" not in found and name_no_ext == model_base:
            found["albedo"] = fpath
            break
        # Common default naming like texture.png, texture_0.png, or main.png
        if "albedo" not in found and name_no_ext in ("texture", "texture_0", "main", "base_color"):
            found["albedo"] = fpath

    # Pass 3: If still no albedo, but there's a unique non-matched image with model_base in its name
    if "albedo" not in found:
        for fname, fpath in remaining_files:
            name_no_ext = os.path.splitext(fname)[0].lower()
            if model_base in name_no_ext:
                found["albedo"] = fpath
                break

    return found


def _is_placeholder(img) -> bool:
    """A stand-in the exporter wrote, not a texture anybody painted."""
    try:
        return max(img.size) < MIN_REAL_TEXTURE_PX
    except Exception:
        return False


def extract_embedded_textures(mesh: trimesh.Trimesh) -> dict[str, Image.Image]:
    """Extracts textures already loaded into trimesh visual / materials."""
    embedded = {}
    if mesh is None:
        return embedded

    # load_model() lifts the material off the mesh before welding and leaves the
    # images here, so this keeps working long after mesh.visual has been replaced.
    cached = (getattr(mesh, "metadata", None) or {}).get("embedded_textures")
    if cached:
        return dict(cached)

    if not hasattr(mesh, "visual") or mesh.visual is None:
        return embedded

    vis = mesh.visual
    mat = getattr(vis, "material", None)
    if mat is None:
        return embedded

    # Simple texture visual
    if hasattr(vis, "image") and isinstance(vis.image, Image.Image):
        embedded["albedo"] = vis.image

    # PBR Material standard attributes (e.g. from glTF / GLB)
    for attr, ch in (
        ("baseColorTexture", "albedo"),
        ("image", "albedo"),
        ("normalTexture", "normal"),
        ("metallicRoughnessTexture", "metallic_roughness"),
        ("occlusionTexture", "ao")
    ):
        img = getattr(mat, attr, None)
        if isinstance(img, Image.Image):
            embedded[ch] = img

    return embedded


def find_and_load_companion_textures(model_path: str, mesh: trimesh.Trimesh | None = None) -> dict[str, Image.Image]:
    """
    Collect every texture channel for a model, as PIL Images.

    Order of authority: the textures the file's own material declares, then
    companion image files found beside it. Packed maps (ORM, metallicRoughness) are
    unpacked into their separate channels.
    """
    maps: dict[str, Image.Image] = {}

    def _unpack(name: str, channels: tuple):
        """Split a packed map (ORM, metallicRoughness) into the channels it carries."""
        if name not in maps:
            return
        planes = maps.pop(name).convert("RGB").split()
        for plane, channel in zip(planes, channels):
            if channel and channel not in maps:
                maps[channel] = plane

    # The material the file itself declares is the authority. A folder scan is a
    # guess, and a stale *_diffuse.png sitting beside the model must never beat the
    # texture the model actually points at — unless what it points at is a stub.
    for channel, img in extract_embedded_textures(mesh).items():
        if _is_placeholder(img):
            continue
        maps[channel] = img
    _unpack("metallic_roughness", (None, "roughness", "metallic"))

    # Companion files then fill in whatever the material did not supply.
    packed = ("albedo", "normal", "orm", "metallic_roughness")
    for channel, path in find_companion_texture_files(model_path).items():
        if channel in maps:
            continue
        try:
            img = Image.open(path)
            img.load()
            maps[channel] = img.convert("RGB" if channel in packed else "L")
        except Exception:
            pass

    _unpack("orm", ("ao", "roughness", "metallic"))
    _unpack("metallic_roughness", (None, "roughness", "metallic"))
    return maps
