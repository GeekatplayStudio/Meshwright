"""
How texture data reaches the outside: what the UI and the MCP server are sent, what
lands in an exported file, and what the PBR generator does at an image's edges.
"""
import json
import os

import numpy as np
import pytest
import trimesh
from PIL import Image

from engine.service import MeshService
from engine.texture.pbr_generator import generate_normal_map, generate_pbr_material
from engine.texture.seam_fixer import dilate_map_set, uv_mask_from_corner_uv
from engine.texture.uv_unwrapper import unwrap_corner_uv


@pytest.fixture
def textured_service():
    """A sphere with UVs and a full generated PBR set."""
    svc = MeshService(log=lambda m, level="info": None, autosave=False)
    mesh = trimesh.creation.icosphere(subdivisions=3, radius=40.0)
    corner_uv, _ = unwrap_corner_uv(mesh)
    svc.file_path = "sphere.stl"
    svc._commit(mesh, "load", guard=False, uv=corner_uv)
    svc.generate_pbr_textures(
        Image.fromarray((np.indices((256, 256)).sum(0) % 64 * 4).astype(np.uint8)).convert("RGB"))
    yield svc
    svc.close()


# ------------------------------------------------------------------ 1. the payload
def test_operations_carry_a_summary_not_the_pixels(textured_service):
    """
    Encoding six maps costs a third of a second and a dozen megabytes. Doing it on
    every repair, undo and rotate is what this guards against.
    """
    result = textured_service.repair()

    textures = result["textures"]
    assert textures["has_textures"] is True
    assert "albedo" in textures["channels"]
    assert "maps" not in textures, "the base64 maps must not ride along with an operation"
    assert "uv_wireframe" not in textures

    encoded = json.dumps(result["textures"])
    assert len(encoded) < 2000, f"the texture summary grew to {len(encoded)} bytes"


def test_the_version_moves_only_when_the_maps_do(textured_service):
    first = textured_service.get_texture_state()["texture_version"]

    # A mesh operation changes geometry, not textures.
    textured_service.simplify(0.5)
    assert textured_service.get_texture_state()["texture_version"] == first

    # Generating a new set does change them.
    textured_service.generate_pbr_textures(Image.new("RGB", (64, 64), (10, 20, 30)))
    assert textured_service.get_texture_state()["texture_version"] > first


def test_maps_are_fetchable_on_demand(textured_service):
    summary = textured_service.get_texture_state()
    maps = textured_service.get_texture_maps()

    assert maps["texture_version"] == summary["texture_version"]
    for channel in summary["channels"]:
        assert maps["maps"][channel].startswith("data:image/png;base64,")


def test_uv_layout_is_fetched_separately(textured_service):
    layout = textured_service.get_uv_layout()
    assert layout["has_uv"] is True
    assert len(layout["lines"]) % 4 == 0
    assert layout["state_id"] == textured_service.current.id


def test_mcp_results_stay_small(textured_service):
    """A tool result is read by an assistant through a context window."""
    from mcp_server import _strip

    stripped = _strip(textured_service.repair())
    assert "preview" not in stripped
    assert "maps" not in stripped.get("textures", {})
    assert len(json.dumps(stripped)) < 200_000


# ------------------------------------------------------------------ 2. gutters
def _painted_only_inside(corner_uv, size=256):
    """A map with colour inside the charts and nothing at all in the gutters."""
    mask = uv_mask_from_corner_uv(corner_uv, size, size)
    rgb = np.zeros((size, size, 3), np.uint8)
    rgb[mask > 0] = (200, 120, 60)
    return Image.fromarray(rgb), mask


def test_gutters_are_padded_so_seams_do_not_fringe():
    mesh = trimesh.creation.icosphere(subdivisions=3, radius=40.0)
    corner_uv, _ = unwrap_corner_uv(mesh)
    painted, mask = _painted_only_inside(corner_uv)

    gutter = mask == 0
    assert np.array(painted)[gutter].sum() == 0, "the fixture should start with empty gutters"

    padded = dilate_map_set({"albedo": painted}, corner_uv, padding_px=8)["albedo"]
    filled = (np.array(padded)[gutter].sum(axis=1) > 0).mean()

    assert filled > 0.25, f"only {filled:.0%} of the gutter was padded"
    # Inside the charts nothing may change.
    assert np.array_equal(np.array(padded)[mask > 0], np.array(painted)[mask > 0])


def test_dilation_leaves_the_loaded_maps_alone(textured_service):
    before = np.array(textured_service.materials.albedo.convert("RGB"))
    textured_service.materials.as_trimesh_material(corner_uv=textured_service.corner_uv)
    after = np.array(textured_service.materials.albedo.convert("RGB"))
    assert np.array_equal(before, after), "padding must not modify what the user loaded"


def test_exported_pack_and_baked_glb_are_padded(textured_service, tmp_path):
    textured_service.export_texture_pack(str(tmp_path))
    assert (tmp_path / "albedo.png").exists()
    assert (tmp_path / "uv_wireframe.png").exists()
    assert not (tmp_path / "uv_layout_guide.png").exists(), "the guide was written twice before"

    out = tmp_path / "baked.glb"
    res = textured_service.bake_and_export_glb(str(out))
    assert res["success"] and res["has_pbr"]
    assert os.path.getsize(out) > 1000


# ------------------------------------------------------------------ 3. normal borders
def _edge_column(normal_map):
    return int(np.asarray(normal_map)[normal_map.shape[0] // 2, 0, 0])


def test_atlas_normal_maps_have_flat_borders():
    """
    Wrap-mode gradients treat the far edge as adjacent, which stamps a hard fake
    ridge down all four borders of a texture that does not tile.
    """
    flat = np.full((64, 64, 3), 128, np.uint8)
    flat[:, :32] = 40                      # one genuine edge in the middle

    atlas = generate_normal_map(flat, strength=2.5)
    assert abs(_edge_column(atlas) - 128) <= 4, "a false edge appeared at the texture border"
    # The real edge must still be detected.
    assert abs(int(atlas[32, 31, 0]) - 128) > 60


def test_tileable_normal_maps_still_wrap():
    flat = np.full((64, 64, 3), 128, np.uint8)
    flat[:, :32] = 40

    tiling = generate_normal_map(flat, strength=2.5, tileable=True)
    assert abs(_edge_column(tiling) - 128) > 60, "a tiling texture should see across its edge"


def test_tileable_generation_blends_the_albedo_edges():
    rng = np.random.default_rng(0)
    noisy = Image.fromarray(rng.integers(0, 255, (128, 128, 3), dtype=np.uint8))

    plain = np.asarray(generate_pbr_material(noisy)["albedo"], dtype=float)
    tiled = np.asarray(generate_pbr_material(noisy, tileable=True)["albedo"], dtype=float)

    seam = lambda a: np.abs(a[:, 0] - a[:, -1]).mean()
    assert seam(tiled) < seam(plain), "tileable output should join at the edges"


# ------------------------------------------------------------------ 5. height map
def test_height_is_generated_and_delivered(textured_service):
    assert textured_service.materials.height is not None
    assert "height" in textured_service.get_texture_state()["channels"]
    assert textured_service.get_texture_maps()["maps"]["height"].startswith("data:image/png;base64,")


def test_height_is_written_into_the_texture_pack(textured_service, tmp_path):
    res = textured_service.export_texture_pack(str(tmp_path))
    assert "height.png" in res["result"]["files"]
    assert Image.open(tmp_path / "height.png").mode == "L"
