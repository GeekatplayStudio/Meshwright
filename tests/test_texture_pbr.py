"""
Unit tests for Meshwright Texture & PBR Engine, Photoshop Round-Trip, and ComfyUI Texture Nodes.
"""
import importlib
import os
import tempfile

import numpy as np
import pytest
import trimesh
from PIL import Image

from engine.service import MeshService
from engine.texture import uv_channel as UV
from engine.texture.material_manager import MaterialManager
from engine.texture.pbr_generator import generate_normal_map, generate_pbr_material
from engine.texture.seam_fixer import dilate_texture_gutters
from engine.texture.uv_unwrapper import (
    export_uv_wireframe_image,
    unwrap_corner_uv,
    unwrap_mesh,
)

tex_nodes_mod = importlib.import_module("comfyui_nodes.Geekatplay-3D-MeshFix.texture_nodes")

MeshwrightUVUnwrap = tex_nodes_mod.MeshwrightUVUnwrap
MeshwrightImageToPBR = tex_nodes_mod.MeshwrightImageToPBR
MeshwrightApplyTextures = tex_nodes_mod.MeshwrightApplyTextures
MeshwrightSaveGLB = tex_nodes_mod.MeshwrightSaveGLB


@pytest.fixture
def sample_mesh():
    """Creates a clean icosphere test mesh."""
    return trimesh.creation.icosphere(subdivisions=2, radius=10.0)


@pytest.fixture
def sample_image():
    """Creates a synthetic gradient test image."""
    img = Image.new("RGB", (128, 128), color=(140, 120, 100))
    # Add a bright diagonal stripe
    for i in range(128):
        img.putpixel((i, i), (255, 200, 50))
    return img


def test_uv_unwrapper(sample_mesh):
    """Verifies xatlas chart parameterization and line segment extraction."""
    unwrapped, stats = unwrap_mesh(sample_mesh)
    assert hasattr(unwrapped.visual, "uv")
    assert unwrapped.visual.uv is not None
    assert len(unwrapped.visual.uv) == len(unwrapped.vertices)
    assert stats["islands"] >= 1
    assert stats["seam_edges"] >= 0

    # UV wireframe image
    wire_img = export_uv_wireframe_image(unwrapped, size=(256, 256))
    assert wire_img.size == (256, 256)
    assert wire_img.mode == "RGBA"

    # Line segment data for the 2D canvas, from the per-corner channel
    corner_uv, _ = unwrap_corner_uv(sample_mesh)
    data = UV.wireframe_lines(sample_mesh, corner_uv)
    assert data["has_uv"] is True
    assert len(data["lines"]) > 0
    assert len(data["lines"]) % 4 == 0  # (x1, y1, x2, y2) tuples


def test_pbr_generator(sample_image):
    """Verifies image-to-PBR map derivation with Sobel normals and roughness separation."""
    maps = generate_pbr_material(
        sample_image,
        normal_strength=2.5,
        opengl_normal=True,
        min_roughness=0.1,
        max_roughness=0.9,
        metallic_amount=0.5,
        ao_intensity=1.2
    )

    for ch in ("albedo", "normal", "roughness", "metallic", "ao"):
        assert ch in maps
        assert maps[ch].size == sample_image.size

    # Verify normal format
    normal_gl = generate_normal_map(sample_image, strength=2.0, opengl=True)
    normal_dx = generate_normal_map(sample_image, strength=2.0, opengl=False)
    arr_gl = np.array(normal_gl)
    arr_dx = np.array(normal_dx)
    # Green channel (Y) should be inverted between GL and DX
    assert not np.array_equal(arr_gl[:, :, 1], arr_dx[:, :, 1])


def test_seam_fixer(sample_mesh, sample_image):
    """Verifies morphological gutter dilation outside UV boundaries."""
    unwrapped, _ = unwrap_mesh(sample_mesh)
    dilated = dilate_texture_gutters(sample_image, unwrapped.visual.uv, dilation_pixels=12)
    assert dilated.size == sample_image.size
    assert dilated.mode == "RGB"


def test_material_manager_and_roundtrip(sample_mesh, sample_image):
    """Tests Photoshop export pack, image reload, and GLB baking."""
    corner_uv, _ = unwrap_corner_uv(sample_mesh)
    mgr = MaterialManager()

    maps = generate_pbr_material(sample_image)
    mgr.set_material_maps(maps)

    with tempfile.TemporaryDirectory() as tmpdir:
        # Export texture pack
        exp_res = mgr.export_texture_pack(tmpdir, sample_mesh, corner_uv)
        assert exp_res["count"] >= 5
        assert os.path.exists(os.path.join(tmpdir, "albedo.png"))
        assert os.path.exists(os.path.join(tmpdir, "normal.png"))
        assert os.path.exists(os.path.join(tmpdir, "uv_wireframe.png"))

        # Modify an exported texture file (simulating Photoshop edit)
        albedo_path = os.path.join(tmpdir, "albedo.png")
        edited = Image.new("RGB", (128, 128), color=(20, 180, 220))
        edited.save(albedo_path)

        # Reload texture pack
        reload_res = mgr.reload_texture_pack(tmpdir)
        assert "albedo" in reload_res["reloaded_channels"]
        loaded_albedo = mgr.maps["albedo"]
        assert loaded_albedo.getpixel((0, 0)) == (20, 180, 220)

        # Bake to GLB
        glb_path = os.path.join(tmpdir, "baked_model.glb")
        bake_res = mgr.bake_and_export_glb(sample_mesh, glb_path, corner_uv)
        assert bake_res["success"] is True
        assert bake_res["has_pbr"] is True
        assert os.path.exists(glb_path)
        assert os.path.getsize(glb_path) > 1000

        # The GLB must come back with the same faces and a usable UV per vertex.
        reloaded = trimesh.load(glb_path, force="mesh", process=False)
        assert len(reloaded.faces) == len(sample_mesh.faces)
        assert reloaded.visual.uv is not None
        assert len(reloaded.visual.uv) == len(reloaded.vertices)

        # Baking without UVs is refused rather than silently unwrapping behind the
        # user's back onto a layout their artwork was never painted for.
        with pytest.raises(ValueError, match="no UV coordinates"):
            mgr.bake_and_export_glb(sample_mesh, glb_path, None)


def test_service_texture_integration(sample_mesh, sample_image):
    """Verifies MeshService orchestration of UV unwrapping, PBR gen, and state."""
    svc = MeshService(autosave=False)
    svc._commit(sample_mesh, "init", guard=False)

    # Unwrap UVs — the layout comes back with the result that produced it.
    res_uv = svc.unwrap_uvs()
    assert res_uv["success"] is True
    assert "uv_stats" in res_uv
    assert res_uv["uv_layout"]["has_uv"] is True
    assert len(res_uv["uv_layout"]["lines"]) > 0

    # Generate PBR maps. The result is the light summary; the pixels are fetched
    # separately so an ordinary mesh operation does not carry megabytes of base64.
    res_pbr = svc.generate_pbr_textures(sample_image, normal_strength=2.0)
    assert res_pbr["success"] is True
    assert res_pbr["has_textures"] is True
    assert "albedo" in res_pbr["channels"]
    assert "maps" not in res_pbr

    maps = svc.get_texture_maps()
    assert maps["texture_version"] == res_pbr["texture_version"]
    assert maps["maps"]["albedo"].startswith("data:image/png;base64,")

    # Round-trip pack export and reload
    with tempfile.TemporaryDirectory() as tmpdir:
        exp = svc.export_texture_pack(tmpdir)
        assert exp["success"] is True
        rel = svc.reload_texture_pack(tmpdir)
        assert rel["success"] is True

        glb_out = os.path.join(tmpdir, "test.glb")
        bake = svc.bake_and_export_glb(glb_out)
        assert bake["success"] is True
        assert os.path.exists(glb_out)


def test_comfyui_texture_nodes(sample_mesh, sample_image):
    """Verifies all 4 ComfyUI texture custom nodes."""
    # 1. MeshwrightUVUnwrap
    node_unwrap = MeshwrightUVUnwrap()
    out_mesh, islands, seams, info = node_unwrap.unwrap(sample_mesh)
    assert hasattr(out_mesh.visual, "uv")
    assert islands >= 1
    assert "Unwrapped:" in info

    # 2. MeshwrightImageToPBR
    node_pbr = MeshwrightImageToPBR()
    pbr_dict, albedo_t, normal_t, rough_t, metal_t, ao_t = node_pbr.generate_pbr(
        sample_image,
        normal_strength=2.0,
        opengl_normal=True,
        roughness_min=0.2,
        roughness_max=0.8,
        metallic=0.1,
        ao_intensity=1.0
    )
    assert "albedo" in pbr_dict
    assert albedo_t.shape[-1] == 3  # RGB channels

    # 3. MeshwrightApplyTextures
    node_apply = MeshwrightApplyTextures()
    applied_mesh, mat, summary = node_apply.apply(
        out_mesh,
        pbr_material=pbr_dict,
        dilate_gutters=True
    )
    assert "Applied" in summary
    assert "normal" in mat

    # 4. MeshwrightSaveGLB
    node_save = MeshwrightSaveGLB()
    with tempfile.TemporaryDirectory() as tmpdir:
        import sys
        import types
        mock_fp = types.ModuleType("folder_paths")
        mock_fp.get_output_directory = lambda: tmpdir
        sys.modules["folder_paths"] = mock_fp
        try:
            (glb_path,) = node_save.save(applied_mesh, filename_prefix="TestGLB", pbr_material=mat)
            assert os.path.exists(glb_path)
            assert glb_path.endswith(".glb")
            assert os.path.getsize(glb_path) > 0
        finally:
            sys.modules.pop("folder_paths", None)


def test_companion_texture_detector():
    """Verifies companion texture discovery and ORM channel splitting."""
    from engine.texture.companion_detector import (
        find_and_load_companion_textures,
        find_companion_texture_files,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        model_path = os.path.join(tmpdir, "skyway_sn.fbx")
        with open(model_path, "w") as f:
            f.write("mock_fbx")

        # Create companion textures matching Meshy AI naming patterns
        albedo_path = os.path.join(tmpdir, "skyway_sn.png")
        normal_path = os.path.join(tmpdir, "skyway_sn_normal.png")
        rough_path = os.path.join(tmpdir, "skyway_sn_roughness.png")
        metal_path = os.path.join(tmpdir, "skyway_sn_metallic.png")

        img = Image.new("RGB", (64, 64), color=(100, 150, 200))
        img.save(albedo_path)
        img.save(normal_path)
        img.convert("L").save(rough_path)
        img.convert("L").save(metal_path)

        found = find_companion_texture_files(model_path)
        assert "albedo" in found and found["albedo"] == albedo_path
        assert "normal" in found and found["normal"] == normal_path
        assert "roughness" in found and found["roughness"] == rough_path
        assert "metallic" in found and found["metallic"] == metal_path

        loaded = find_and_load_companion_textures(model_path)
        assert "albedo" in loaded and loaded["albedo"].size == (64, 64)
        assert "normal" in loaded and loaded["normal"].mode == "RGB"
        assert "roughness" in loaded and loaded["roughness"].mode == "L"
        assert "metallic" in loaded and loaded["metallic"].mode == "L"


def _linear_uv_plane(n=60, size=100.0):
    """A bumpy plane whose UV is an exact linear function of position.

    Ground truth for any point is known, and there are no seams, so any drift the
    transfer shows is the transfer's own error and nothing else.
    """
    axis = np.linspace(0.0, size, n)
    x, y = np.meshgrid(axis, axis)
    z = 3.0 * np.sin(x / 9.0) * np.cos(y / 11.0)
    verts = np.column_stack([x.ravel(), y.ravel(), z.ravel()])
    grid = np.arange(n * n).reshape(n, n)
    faces = np.array([tri
                      for i in range(n - 1) for j in range(n - 1)
                      for tri in ([grid[i, j], grid[i, j + 1], grid[i + 1, j + 1]],
                                  [grid[i, j], grid[i + 1, j + 1], grid[i + 1, j]])])
    mesh = trimesh.Trimesh(vertices=verts, faces=faces, process=False)
    corner_uv = UV.from_vertex_uv(faces, (verts[:, :2] / size).astype(np.float32))
    return mesh, corner_uv, size


def _uv_drift(mesh, corner_uv, size):
    """Distance between each transferred corner UV and the truth, in UV units."""
    corners = np.asarray(mesh.vertices)[np.asarray(mesh.faces)].reshape(-1, 3)
    truth = corners[:, :2] / size
    return np.linalg.norm(np.asarray(corner_uv).reshape(-1, 2) - truth, axis=1)


# Worst acceptable UV drift, in texels on a 2048 px map, per decimation level. The
# budget widens at extreme reductions because the surface itself has genuinely
# moved by then, so the closest point on the source is a slightly different place.
# For scale: the nearest-centroid scheme this replaced drifted 82, 175 and 394.
DRIFT_BUDGET_TEXELS = {0.5: 1.0, 0.1: 1.0, 0.02: 3.0}


@pytest.mark.parametrize("keep", [0.5, 0.1, 0.02])
def test_decimation_keeps_uvs_on_target(keep):
    """Decimation must not slide the texture across the surface."""
    from engine.mesh_reducer import reduce_mesh

    src, src_uv, size = _linear_uv_plane()
    reduced, _ = reduce_mesh(src, target_factor=keep)
    carried = UV.transfer(src, src_uv, reduced)

    assert carried.shape == (len(reduced.faces), 3, 2)
    assert np.all(np.isfinite(carried))

    worst = _uv_drift(reduced, carried, size).max() * 2048
    assert worst < DRIFT_BUDGET_TEXELS[keep], f"worst drift {worst:.2f} texels"


def test_decimation_leaves_the_mesh_welded():
    """
    The old per-vertex scheme split every face corner into its own vertex. A welded
    triangle mesh has roughly half as many vertices as faces; assert we are near
    that and nowhere near the 3-per-face of a triangle soup.
    """
    from engine.mesh_reducer import reduce_mesh

    src, src_uv, _ = _linear_uv_plane()
    reduced, _ = reduce_mesh(src, target_factor=0.25)
    UV.transfer(src, src_uv, reduced)          # must not touch the geometry

    assert len(reduced.vertices) < len(reduced.faces), "mesh came back unwelded"


def test_unwrap_does_not_touch_the_geometry(sample_mesh):
    """
    Unwrapping used to split vertices, which made a watertight solid read as a pile
    of open shells. The corner-UV unwrap must leave the mesh exactly as it was.
    """
    assert sample_mesh.is_watertight
    before_v, before_f = len(sample_mesh.vertices), len(sample_mesh.faces)

    corner_uv, stats = unwrap_corner_uv(sample_mesh)

    assert len(sample_mesh.vertices) == before_v
    assert len(sample_mesh.faces) == before_f
    assert sample_mesh.is_watertight
    assert sample_mesh.body_count == 1
    assert corner_uv.shape == (before_f, 3, 2)
    assert stats["islands"] >= 1
    assert stats["seam_edges"] == UV.seam_edge_count(sample_mesh, corner_uv)


def test_chart_labels_find_the_seams(sample_mesh):
    corner_uv, stats = unwrap_corner_uv(sample_mesh)
    labels = UV.chart_labels(sample_mesh, corner_uv)
    assert len(labels) == len(sample_mesh.faces)
    assert len(np.unique(labels)) == stats["islands"]


def test_split_for_gpu_round_trips(sample_mesh):
    """The GPU/glTF split must reproduce the same triangles, with a UV per vertex."""
    corner_uv, _ = unwrap_corner_uv(sample_mesh)
    verts, faces, uvs, vertex_map = UV.split_for_gpu(sample_mesh, corner_uv)

    assert len(faces) == len(sample_mesh.faces)
    assert len(uvs) == len(verts)
    assert len(verts) >= len(sample_mesh.vertices)     # seams duplicate, never merge
    assert len(vertex_map) == len(sample_mesh.vertices)

    # Every split corner still sits at its original position and carries its own UV.
    assert np.allclose(verts[faces], np.asarray(sample_mesh.vertices)[sample_mesh.faces])
    assert np.allclose(uvs[faces], corner_uv, atol=1e-5)


def test_service_auto_load_companion_textures(sample_mesh):
    """Verifies that MeshService.load() automatically finds and maps companion PBR textures."""
    with tempfile.TemporaryDirectory() as tmpdir:
        obj_path = os.path.join(tmpdir, "companion_test.obj")
        sample_mesh.export(obj_path)

        # Companion PNG named identically to the OBJ
        tex_path = os.path.join(tmpdir, "companion_test.png")
        img = Image.new("RGB", (64, 64), color=(220, 110, 50))
        img.save(tex_path)

        norm_path = os.path.join(tmpdir, "companion_test_normal.png")
        Image.new("RGB", (64, 64), color=(128, 128, 255)).save(norm_path)

        svc = MeshService(autosave=False)
        res = svc.load(obj_path)

        assert res["success"] is True
        assert "textures" in res
        assert res["textures"]["has_textures"] is True
        assert svc.materials.has_textures() is True
        assert svc.materials.albedo is not None
        assert svc.materials.normal is not None


def test_repair_and_cleanup_keep_the_uv_channel(sample_mesh):
    """Repair and sliver cleanup are geometry-only; the UV channel rides along."""
    from engine.mesh_cleanup import fix_slivers
    from engine.mesh_repair import repair_mesh

    corner_uv, _ = unwrap_corner_uv(sample_mesh)

    for name, op in (("fix_slivers", lambda m: fix_slivers(m, min_angle_deg=1.0)[0]),
                     ("repair", lambda m: repair_mesh(m, strict_watertight=False)[0])):
        result = op(sample_mesh)
        carried = UV.transfer(sample_mesh, corner_uv, result)

        assert carried.shape == (len(result.faces), 3, 2), name
        assert np.all(np.isfinite(carried)), name

        # No triangle may straddle the atlas: that is what a seam smear looks like.
        span = carried.max(axis=1) - carried.min(axis=1)
        assert np.all(span < 0.5), f"{name} left {int(np.sum(span >= 0.5))} torn triangle(s)"


def test_transfer_is_exact_when_nothing_changed(sample_mesh):
    """An operation that changes nothing must return the UVs untouched, not re-project them."""
    corner_uv, _ = unwrap_corner_uv(sample_mesh)
    same = UV.transfer(sample_mesh, corner_uv, sample_mesh.copy())
    assert np.array_equal(same, corner_uv)


def test_transfer_without_source_uvs_returns_none(sample_mesh):
    assert UV.transfer(sample_mesh, None, sample_mesh.copy()) is None
