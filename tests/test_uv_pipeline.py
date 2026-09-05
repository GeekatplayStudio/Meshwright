"""
The UV channel end to end.

These tests pin the behaviour that was wrong before: a textured model must survive
the whole pipeline as a welded solid, its texture must not slide, and everything the
texture layer touches must stay honest about what it did.
"""
import base64
import os
import sys

import numpy as np
import pytest
import trimesh
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.preview import build_mesh_preview
from engine.service import MeshService
from engine.texture import uv_channel as UV
from engine.texture.companion_detector import (
    find_and_load_companion_textures,
    find_companion_texture_files,
)
from engine.texture.uv_unwrapper import unwrap_corner_uv


# ------------------------------------------------------------------ fixtures
def _checker(size=32, colour=(255, 255, 255)):
    arr = (np.indices((size, size)).sum(0) % 2 * 255).astype(np.uint8)
    img = Image.fromarray(arr).convert("RGB")
    return Image.merge("RGB", [c.point(lambda v, k=k: v * k // 255) for c, k in zip(img.split(), colour)])


@pytest.fixture
def textured_glb(tmp_path):
    """A watertight sphere with UVs and an embedded base-colour texture."""
    sphere = trimesh.creation.icosphere(subdivisions=3, radius=40.0)
    corner_uv, _ = unwrap_corner_uv(sphere)
    split = UV.to_textured_mesh(
        sphere, corner_uv,
        material=trimesh.visual.material.PBRMaterial(baseColorTexture=_checker()))
    path = tmp_path / "sphere_tex.glb"
    split.export(path)
    return str(path)


@pytest.fixture
def service():
    svc = MeshService(log=lambda m, level="info": None, autosave=False)
    yield svc
    svc.close()


# ------------------------------------------------------------------ the headline
def test_textured_model_survives_the_whole_pipeline(service, textured_glb):
    """
    Load, decimate, repair. Every step must keep the model a watertight single shell.

    Before the UV channel existed this sequence read as 9 shells on load, 201 after
    decimation, and repair inflated 1,280 faces into six figures.
    """
    loaded = service.load(textured_glb)
    assert loaded["stats"]["is_watertight"] is True
    assert loaded["stats"]["body_count"] == 1
    assert loaded.get("shells", []) == [], "a single solid was listed as separate pieces"
    assert loaded["has_uv"] is True
    assert loaded["textures"]["has_textures"] is True
    faces_in = loaded["stats"]["face_count"]

    reduced = service.simplify(0.25)
    assert reduced["stats"]["is_watertight"] is True
    assert reduced["stats"]["body_count"] == 1
    assert reduced["has_uv"] is True
    # A welded triangle mesh has about half as many vertices as faces.
    assert reduced["stats"]["vertex_count"] < reduced["stats"]["face_count"]

    repaired = service.repair()
    assert repaired["success"] is True
    assert repaired["stats"]["is_watertight"] is True
    # Repair must find nothing to do, not rebuild the model.
    assert repaired["stats"]["face_count"] <= faces_in
    assert repaired["has_uv"] is True


def test_undo_restores_the_uvs_with_the_geometry(service, textured_glb):
    service.load(textured_glb)
    before = service.corner_uv.copy()
    service.simplify(0.5)
    assert len(service.corner_uv) != len(before)

    service.undo()
    assert np.array_equal(service.corner_uv, before)


def test_rotate_carries_uvs_untouched(service, textured_glb):
    service.load(textured_glb)
    before = service.corner_uv.copy()
    service.rotate([[1, 0, 0], [0, 0, -1], [0, 1, 0]])
    # A rigid transform changes no topology, so the UVs must be identical, not
    # re-projected onto rotated geometry.
    assert np.array_equal(service.corner_uv, before)


def test_revert_restores_the_files_own_uvs(service, textured_glb):
    service.load(textured_glb)
    original = service.corner_uv.copy()
    service.simplify(0.25)
    service.revert()
    assert np.array_equal(service.corner_uv, original)


# ------------------------------------------------------------------ unwrap
def test_unwrap_keeps_a_print_ready_model_print_ready(service):
    service.file_path = "sphere.stl"
    before = service._commit(trimesh.creation.icosphere(subdivisions=3, radius=40.0),
                             "load", guard=False)
    assert before["analysis"]["verdict"] == "Print ready"
    assert before["analysis"]["score"] == 100

    after = service.unwrap_uvs()
    assert after["analysis"]["verdict"] == "Print ready"
    assert after["analysis"]["score"] == 100
    assert after["stats"]["is_watertight"] is True
    assert after["stats"]["face_count"] == before["stats"]["face_count"]
    assert after["stats"]["vertex_count"] == before["stats"]["vertex_count"]
    assert after["uv_stats"]["islands"] > 1          # the real chart count, not body count


def test_re_unwrap_asks_before_replacing_a_layout(service, textured_glb):
    service.load(textured_glb)
    assert service.corner_uv is not None

    blocked = service.unwrap_uvs()
    assert blocked["success"] is False
    assert blocked["needs_confirm"] is True
    assert blocked["has_textures"] is True
    assert service.materials.has_textures() is True      # nothing thrown away yet

    forced = service.unwrap_uvs(force=True)
    assert forced["success"] is True
    # Loaded maps are preserved as reference for the new layout
    assert service.materials.has_textures() is True


def test_wireframe_lines_continuous_contours(service, textured_glb):
    service.load(textured_glb)
    layout = service.get_uv_layout(max_edges=50)
    assert layout["has_uv"] is True
    assert layout["edge_count"] > 0
    assert len(layout["lines"]) == layout["edge_count"] * 4


# ------------------------------------------------------------------ texture discovery
def _glb_with_material(tmp_path, colour, size=(64, 64), name="thing.glb"):
    box = trimesh.creation.box(extents=[5, 5, 5])
    box.visual = trimesh.visual.TextureVisuals(
        uv=np.zeros((len(box.vertices), 2)),
        material=trimesh.visual.material.PBRMaterial(
            baseColorTexture=Image.new("RGB", size, colour)))
    path = tmp_path / name
    box.export(path)
    return path


def test_the_models_own_material_outranks_the_folder(tmp_path):
    """A stray *_diffuse.png must not beat the texture the file actually declares."""
    Image.new("RGB", (64, 64), (255, 0, 0)).save(tmp_path / "leftover_diffuse.png")
    path = _glb_with_material(tmp_path, (0, 255, 0))

    loaded = trimesh.load(path, force="mesh", process=False)
    maps = find_and_load_companion_textures(str(path), loaded)
    assert maps["albedo"].convert("RGB").getpixel((0, 0))[1] > 200, "the folder decoy won"


def test_a_placeholder_in_the_material_does_not_beat_the_real_file(tmp_path):
    """
    Meshy, Hi3D and others embed a 2x2 stub in the FBX and ship the real 2048px maps
    beside it. Treating the stub as authoritative made textured models load flat.
    """
    Image.new("RGB", (256, 256), (0, 0, 255)).save(tmp_path / "thing.png")
    path = _glb_with_material(tmp_path, (255, 255, 255), size=(2, 2))

    loaded = trimesh.load(path, force="mesh", process=False)
    maps = find_and_load_companion_textures(str(path), loaded)

    assert maps["albedo"].size == (256, 256), "the 2x2 stub was used instead of the real map"
    assert maps["albedo"].convert("RGB").getpixel((0, 0))[2] > 200


def test_gltf_style_packed_map_names_are_recognised(tmp_path):
    """chair_metallicRoughness.png is what most glTF exporters write."""
    textures = tmp_path / "textures"
    textures.mkdir()
    for name in ("chair_baseColor.png", "chair_normal.png", "chair_metallicRoughness.png"):
        Image.new("RGB", (8, 8), (128, 128, 128)).save(textures / name)
    model = tmp_path / "chair.obj"
    model.write_bytes(b"# placeholder")

    found = find_companion_texture_files(str(model))
    assert found.keys() >= {"albedo", "normal", "metallic_roughness"}

    maps = find_and_load_companion_textures(str(model), None)
    # The packed map must arrive unpacked, not as one opaque channel.
    assert "metallic_roughness" not in maps
    assert maps.keys() >= {"albedo", "normal", "roughness", "metallic"}


# ------------------------------------------------------------------ boundaries
def test_preview_splits_seams_and_keeps_open_edges_valid():
    """
    The GPU payload duplicates seam vertices; the open-edge overlay is found on the
    welded mesh, so its indices have to be re-pointed at the split buffer.
    """
    # A plane: 4 open boundary edges that the overlay must still be able to draw.
    plane = trimesh.Trimesh(
        vertices=[[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0]],
        faces=[[0, 1, 2], [0, 2, 3]], process=False)
    corner_uv = np.zeros((2, 3, 2), dtype=np.float32)
    corner_uv[0] = [[0, 0], [1, 0], [1, 1]]
    corner_uv[1] = [[0, 0], [1, 1], [0, 1]]

    payload = build_mesh_preview(plane, corner_uv)
    verts = np.frombuffer(base64.b64decode(payload["vertices"]), dtype=np.float32).reshape(-1, 3)
    faces = np.frombuffer(base64.b64decode(payload["faces"]), dtype=np.uint32).reshape(-1, 3)
    uvs = np.frombuffer(base64.b64decode(payload["uvs"]), dtype=np.float32).reshape(-1, 2)
    edges = np.frombuffer(base64.b64decode(payload["boundary_edges"]), dtype=np.uint32)

    assert len(uvs) == len(verts)
    assert len(faces) == len(plane.faces)
    assert edges.max() < len(verts), "an open-edge index points past the split buffer"
    assert len(edges) == 8, "all four boundary edges should be reported"


def test_preview_without_uvs_is_the_welded_mesh():
    sphere = trimesh.creation.icosphere(subdivisions=2, radius=10.0)
    payload = build_mesh_preview(sphere, None)
    verts = np.frombuffer(base64.b64decode(payload["vertices"]), dtype=np.float32).reshape(-1, 3)
    assert "uvs" not in payload
    assert len(verts) == len(sphere.vertices)


@pytest.mark.parametrize("fmt,expect_uv", [("glb", True), ("obj", True), ("stl", False), ("3mf", False)])
def test_export_carries_uvs_only_where_the_format_can(service, textured_glb, tmp_path, fmt, expect_uv):
    service.load(textured_glb)
    out = tmp_path / f"out.{fmt}"
    res = service.export_model(str(out), fmt)["result"]

    assert res["has_uv"] is expect_uv
    assert os.path.getsize(out) > 0
    if expect_uv:
        back = trimesh.load(out, force="mesh", process=False)
        assert back.visual.uv is not None
        assert len(back.visual.uv) == len(back.vertices)


def test_baked_glb_round_trips_the_maps(service, textured_glb, tmp_path):
    service.load(textured_glb)
    out = tmp_path / "baked.glb"
    res = service.bake_and_export_glb(str(out))

    assert res["success"] is True and res["has_pbr"] is True
    back = trimesh.load(out, force="mesh", process=False)
    assert len(back.faces) == len(service.mesh.faces)
    assert isinstance(back.visual.material, trimesh.visual.material.PBRMaterial)
    assert back.visual.material.baseColorTexture is not None
