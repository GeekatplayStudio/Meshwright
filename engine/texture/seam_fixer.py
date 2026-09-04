"""
Texture Seam & Gutter Dilation Fixer — Geekatplay Studio

A texture only covers the triangles of the UV layout. Everything between the charts
is empty, and when the GPU filters a texel near a chart edge — or builds a mipmap —
it blends that emptiness in, which shows up as dark fringes along every seam.

The fix is to bleed the surface colours outward into the gutters so there is
something sensible to blend with. Meshwright's unwrapper splits models into patches,
which means more seams than a single-chart layout would have, so this matters here
more than it might elsewhere.

The UV mask is rasterised once per texture size and reused across all six channels;
it used to be rebuilt per channel, which cost two seconds each on a dense model.
"""
import numpy as np
from PIL import Image, ImageDraw

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

# Enough to survive several mipmap levels without eating into neighbouring charts.
DEFAULT_PADDING_PX = 16


def rasterize_uv_mask(uv_coords: np.ndarray, faces: np.ndarray, width: int, height: int) -> np.ndarray:
    """
    Renders a binary uint8 mask (H, W) where 255 = inside a UV triangle, 0 = gutter.

    With OpenCV present every triangle goes in one `fillPoly` call; the PIL fallback
    draws them one at a time, which is fine for the sizes it will see.
    """
    px = np.clip(uv_coords[:, 0] * (width - 1), 0, width - 1)
    py = np.clip((1.0 - uv_coords[:, 1]) * (height - 1), 0, height - 1)

    if HAS_CV2:
        pts = np.stack([px[faces], py[faces]], axis=-1)
        mask = np.zeros((height, width), dtype=np.uint8)
        cv2.fillPoly(mask, np.round(pts).astype(np.int32), 255)
        return mask

    mask_img = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(mask_img)
    for face in faces:
        draw.polygon([(float(px[i]), float(py[i])) for i in face], fill=255)
    return np.array(mask_img, dtype=np.uint8)


def uv_mask_from_corner_uv(corner_uv: np.ndarray, width: int, height: int) -> np.ndarray:
    """The same mask, straight from a per-face-corner UV array."""
    flat = np.asarray(corner_uv, dtype=np.float64).reshape(-1, 2)
    faces = np.arange(len(flat), dtype=np.int64).reshape(-1, 3)
    return rasterize_uv_mask(flat, faces, width, height)


def dilate_texture_gutters(image_input, uv_coords: np.ndarray = None, faces: np.ndarray = None,
                           padding_px: int = DEFAULT_PADDING_PX, mask: np.ndarray = None,
                           **kwargs) -> Image.Image:
    """
    Bleed the surface colours outward into the empty UV gutters.

    Pass `mask` to reuse a rasterisation across several channels — that is what the
    texture pack export and the GLB bake do, since all six maps share one layout.
    """
    pad = int(kwargs.get("dilation_pixels", padding_px))

    if isinstance(image_input, Image.Image):
        mode = image_input.mode
        rgb_np = np.array(image_input.convert("RGB"), dtype=np.uint8)
    elif isinstance(image_input, np.ndarray):
        mode = "RGB" if len(image_input.shape) == 3 else "L"
        rgb_np = image_input.copy()
    else:
        raise TypeError("image_input must be PIL Image or NumPy array.")

    h, w = rgb_np.shape[:2]

    if mask is not None:
        uv_mask = np.asarray(mask, dtype=np.uint8)
        if uv_mask.shape != (h, w):
            uv_mask = np.array(Image.fromarray(uv_mask).resize((w, h), Image.Resampling.NEAREST))
    else:
        if faces is None and uv_coords is not None and len(uv_coords) >= 3:
            try:
                from scipy.spatial import Delaunay
                faces = Delaunay(uv_coords).simplices
            except Exception:
                faces = None
        if uv_coords is not None and faces is not None and len(faces) > 0:
            uv_mask = rasterize_uv_mask(np.asarray(uv_coords, dtype=np.float64), np.asarray(faces), w, h)
        else:
            # Fallback: treat non-black pixels as surface.
            lum = np.mean(rgb_np, axis=2) if rgb_np.ndim == 3 else rgb_np
            uv_mask = (lum > 5).astype(np.uint8) * 255

    filled = rgb_np.copy()
    current_mask = uv_mask.copy()

    if HAS_CV2:
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        for _ in range(max(1, pad)):
            dilated_img = cv2.dilate(filled, kernel)
            dilated_mask = cv2.dilate(current_mask, kernel)

            fill_region = (dilated_mask > 0) & (current_mask == 0)
            if not np.any(fill_region):
                break

            filled[fill_region] = dilated_img[fill_region]
            current_mask = dilated_mask
    else:
        from scipy import ndimage
        struct = ndimage.generate_binary_structure(2, 2)
        for _ in range(max(1, pad)):
            dilated_mask = ndimage.binary_dilation(current_mask > 0, structure=struct)
            fill_region = dilated_mask & (current_mask == 0)
            if not np.any(fill_region):
                break
            for c in range(filled.shape[2] if len(filled.shape) == 3 else 1):
                plane = filled[:, :, c] if len(filled.shape) == 3 else filled
                dil_plane = ndimage.maximum_filter(plane, size=3)
                if len(filled.shape) == 3:
                    filled[fill_region, c] = dil_plane[fill_region]
                else:
                    filled[fill_region] = dil_plane[fill_region]
            current_mask = dilated_mask.astype(np.uint8) * 255

    res_img = Image.fromarray(filled)
    if mode == "L" and res_img.mode != "L":
        res_img = res_img.convert("L")
    return res_img


def dilate_map_set(maps: dict, corner_uv, padding_px: int = DEFAULT_PADDING_PX) -> dict:
    """
    Pad the gutters on a whole PBR set, rasterising the UV layout once per size.

    Returns a new dict; the originals are left alone so the user's loaded maps are
    never modified in place.
    """
    if corner_uv is None:
        return dict(maps)

    padded, masks = {}, {}
    for name, img in maps.items():
        if img is None:
            padded[name] = None
            continue
        size = img.size
        if size not in masks:
            masks[size] = uv_mask_from_corner_uv(corner_uv, size[0], size[1])
        padded[name] = dilate_texture_gutters(img, padding_px=padding_px, mask=masks[size])
    return padded


def make_tileable_seamless(image: Image.Image, blend_width: int = 32) -> Image.Image:
    """
    Feather-blends opposite image edges for tileable planar textures.
    """
    rgb = np.array(image.convert("RGB"), dtype=np.float32)
    h, w = rgb.shape[:2]
    blend_w = min(blend_width, w // 4, h // 4)

    out = rgb.copy()
    ramp = np.linspace(0.0, 1.0, blend_w, dtype=np.float32)

    # Horizontal seam blend (left & right)
    for i in range(blend_w):
        alpha = ramp[i]
        out[:, i] = (1.0 - alpha) * rgb[:, w - blend_w + i] + alpha * rgb[:, i]
        out[:, w - blend_w + i] = out[:, i]

    # Vertical seam blend (top & bottom)
    for j in range(blend_w):
        alpha = ramp[j]
        out[j, :] = (1.0 - alpha) * rgb[h - blend_w + j, :] + alpha * rgb[j, :]
        out[h - blend_w + j, :] = out[j, :]

    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))
