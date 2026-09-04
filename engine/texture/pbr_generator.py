"""
Image-to-PBR Map Generator — Geekatplay Studio
Derives physically-based rendering maps (Normal, Roughness, Metallic, AO, Height)
from any 2D color texture or photograph.
"""
import os

import numpy as np
from PIL import Image
from scipy import ndimage


def _load_as_rgb_np(image_input) -> np.ndarray:
    """Converts PIL.Image, file path, or array into uint8 RGB numpy array (H, W, 3)."""
    if isinstance(image_input, str):
        if not os.path.exists(image_input):
            raise FileNotFoundError(f"Image file not found: {image_input}")
        with Image.open(image_input) as img:
            rgb = img.convert("RGB")
            return np.array(rgb, dtype=np.uint8)

    if isinstance(image_input, Image.Image):
        return np.array(image_input.convert("RGB"), dtype=np.uint8)

    if isinstance(image_input, np.ndarray):
        arr = image_input.copy()
        if arr.dtype != np.uint8:
            arr = np.clip(arr * 255.0 if arr.max() <= 1.0 else arr, 0, 255).astype(np.uint8)
        if len(arr.shape) == 2:
            arr = np.stack([arr, arr, arr], axis=-1)
        elif arr.shape[2] == 4:
            arr = arr[:, :, :3]
        return arr

    raise TypeError(f"Unsupported image input type: {type(image_input)}")


def generate_height_map(rgb: np.ndarray, blur_radius: float = 1.0) -> np.ndarray:
    """
    Computes a 2D float32 height map [0.0, 1.0] from RGB luminance.
    """
    luminance = 0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]
    norm_lum = luminance / 255.0

    if blur_radius > 0:
        norm_lum = ndimage.gaussian_filter(norm_lum, sigma=blur_radius)

    # Normalize range
    min_val, max_val = norm_lum.min(), norm_lum.max()
    if max_val - min_val > 1e-4:
        norm_lum = (norm_lum - min_val) / (max_val - min_val)

    return norm_lum.astype(np.float32)


def generate_normal_map(input_data, strength: float = 2.5, opengl: bool = True,
                        tileable: bool = False):
    """
    Generates a tangent-space RGB normal map (uint8) using Sobel gradients.
    Accepts 2D float height map, 3D RGB array, or PIL Image.
    opengl=True uses +Y (green points up), opengl=False uses DirectX -Y (green inverted).

    `tileable` says whether the source image wraps at its edges. It does for a
    repeating material; it does not for a UV atlas, and treating an atlas as tileable
    puts a hard fake ridge down all four borders of the normal map, because the Sobel
    kernel reads the opposite edge as if it were adjacent.
    """
    is_pil = isinstance(input_data, Image.Image)
    if is_pil:
        height_map = generate_height_map(np.array(input_data.convert("RGB")))
    elif isinstance(input_data, np.ndarray) and input_data.ndim == 3:
        height_map = generate_height_map(input_data)
    else:
        height_map = input_data

    # Sobel filter convolution
    sobel_x = np.array([[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]], dtype=np.float32)
    sobel_y = np.array([[-1, -2, -1], [0, 0, 0], [1, 2, 1]], dtype=np.float32)

    edge_mode = "wrap" if tileable else "nearest"
    gx = ndimage.convolve(height_map, sobel_x, mode=edge_mode)
    gy = ndimage.convolve(height_map, sobel_y, mode=edge_mode)

    # Scale gradients by strength
    nx = -gx * strength
    ny = gy * strength if opengl else -gy * strength
    nz = np.ones_like(height_map, dtype=np.float32)

    norm = np.sqrt(nx ** 2 + ny ** 2 + nz ** 2)
    norm[norm == 0] = 1.0

    nx /= norm
    ny /= norm
    nz /= norm

    # Map [-1.0, 1.0] to [0, 255]
    r = np.clip((nx + 1.0) * 127.5, 0, 255).astype(np.uint8)
    g = np.clip((ny + 1.0) * 127.5, 0, 255).astype(np.uint8)
    b = np.clip((nz + 1.0) * 127.5, 0, 255).astype(np.uint8)

    normal_arr = np.stack([r, g, b], axis=-1)
    if is_pil:
        return Image.fromarray(normal_arr)
    return normal_arr


def generate_roughness_map(height_map: np.ndarray, min_roughness: float = 0.2, max_roughness: float = 0.85,
                           invert: bool = True) -> np.ndarray:
    """
    Generates a grayscale uint8 roughness map.
    Specular highlights invert to low roughness (smooth/shiny).
    """
    base = (1.0 - height_map) if invert else height_map

    # Frequency split: add high-pass micro-surface roughness
    blurred = ndimage.gaussian_filter(base, sigma=3.0)
    high_freq = np.clip(base - blurred + 0.5, 0.0, 1.0)

    combined = 0.6 * base + 0.4 * high_freq
    remapped = min_roughness + combined * (max_roughness - min_roughness)
    res = np.clip(remapped * 255.0, 0, 255).astype(np.uint8)

    return res


def generate_metallic_map(rgb: np.ndarray, metallic_amount: float = 0.0, auto_detect: bool = False) -> np.ndarray:
    """
    Generates a grayscale uint8 metallic mask.
    Non-metals (dielectrics) are black (0), metals are white (255).
    """
    h, w = rgb.shape[:2]
    if not auto_detect or metallic_amount > 0:
        # Uniform metallic level
        val = int(np.clip(metallic_amount * 255.0, 0, 255))
        return np.full((h, w), val, dtype=np.uint8)

    # Auto-detect: high-luminance, low-saturation areas often represent metals/steels
    r, g, b = rgb[:, :, 0].astype(float), rgb[:, :, 1].astype(float), rgb[:, :, 2].astype(float)
    max_c = np.maximum(np.maximum(r, g), b)
    min_c = np.minimum(np.minimum(r, g), b)
    sat = (max_c - min_c) / (max_c + 1e-5)
    lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255.0

    # High lum + low sat -> metallic mask candidate
    metal_prob = np.clip((lum - 0.5) * 2.0 * (1.0 - sat), 0.0, 1.0)
    return (metal_prob * 255.0).astype(np.uint8)


def generate_ambient_occlusion_map(height_map: np.ndarray, intensity: float = 1.0) -> np.ndarray:
    """
    Generates a grayscale uint8 Ambient Occlusion (AO) map.
    Depressions and crevices are shaded darker.
    """
    large_blur = ndimage.gaussian_filter(height_map, sigma=6.0)
    small_blur = ndimage.gaussian_filter(height_map, sigma=1.5)

    # Cavity difference
    cavity = np.clip(small_blur - large_blur + 0.5, 0.0, 1.0)
    ao = np.clip(height_map * 0.4 + cavity * 0.6, 0.0, 1.0)

    # Apply intensity curve
    ao = ao ** max(0.2, intensity)
    return (ao * 255.0).astype(np.uint8)


def generate_pbr_material(image_input, normal_strength: float = 2.5, opengl_normal: bool = True,
                          min_roughness: float = 0.2, max_roughness: float = 0.85,
                          metallic_amount: float = 0.0, ao_intensity: float = 1.0,
                          tileable: bool = False) -> dict:
    """
    Primary API: Takes an input image and derives a complete PBR material set.
    Returns dict of PIL Images:
      'albedo', 'normal', 'roughness', 'metallic', 'ao', 'height'

    Set `tileable` for a repeating material — the edges are then blended so the
    albedo tiles without a visible join, and the gradients wrap. Leave it off for a
    photograph or a texture painted for one specific UV layout.
    """
    rgb_np = _load_as_rgb_np(image_input)
    if tileable:
        from .seam_fixer import make_tileable_seamless
        rgb_np = np.array(make_tileable_seamless(Image.fromarray(rgb_np)), dtype=np.uint8)
    height_np = generate_height_map(rgb_np)

    normal_np = generate_normal_map(height_np, strength=normal_strength,
                                    opengl=opengl_normal, tileable=tileable)
    roughness_np = generate_roughness_map(height_np, min_roughness=min_roughness, max_roughness=max_roughness)
    metallic_np = generate_metallic_map(rgb_np, metallic_amount=metallic_amount)
    ao_np = generate_ambient_occlusion_map(height_np, intensity=ao_intensity)

    return {
        "albedo": Image.fromarray(rgb_np),
        "normal": Image.fromarray(normal_np),
        "roughness": Image.fromarray(roughness_np, mode="L"),
        "metallic": Image.fromarray(metallic_np, mode="L"),
        "ao": Image.fromarray(ao_np, mode="L"),
        "height": Image.fromarray((height_np * 255.0).astype(np.uint8), mode="L")
    }
