"""
Tests for Meshwright ComfyUI Custom Nodes — Geekatplay Studio
"""
import importlib
import json
import os

import numpy as np
import trimesh

nodes_mod = importlib.import_module("comfyui_nodes.Geekatplay-3D-MeshFix.nodes")
utils_mod = importlib.import_module("comfyui_nodes.Geekatplay-3D-MeshFix.utils")

MeshwrightFixMesh = nodes_mod.MeshwrightFixMesh
MeshwrightReduceMesh = nodes_mod.MeshwrightReduceMesh
MeshwrightLoadModel = nodes_mod.MeshwrightLoadModel
MeshwrightCompareMesh = nodes_mod.MeshwrightCompareMesh
MeshwrightSaveMesh = nodes_mod.MeshwrightSaveMesh
MeshwrightPreview3D = nodes_mod.MeshwrightPreview3D
MeshwrightTextDisplay = nodes_mod.MeshwrightTextDisplay
NODE_CLASS_MAPPINGS = nodes_mod.NODE_CLASS_MAPPINGS
NODE_DISPLAY_NAME_MAPPINGS = nodes_mod.NODE_DISPLAY_NAME_MAPPINGS

to_trimesh = utils_mod.to_trimesh
render_mesh_to_np = utils_mod.render_mesh_to_np
render_comparison_preview = utils_mod.render_comparison_preview

from scripts.install_comfyui_nodes import install_nodes


def test_node_mappings():
    assert "MeshwrightFixMesh" in NODE_CLASS_MAPPINGS
    assert "MeshwrightReduceMesh" in NODE_CLASS_MAPPINGS
    assert "MeshwrightLoadModel" in NODE_CLASS_MAPPINGS
    assert "MeshwrightCompareMesh" in NODE_CLASS_MAPPINGS
    assert "MeshwrightSaveMesh" in NODE_CLASS_MAPPINGS
    assert "MeshwrightPreview3D" in NODE_CLASS_MAPPINGS
    assert "MeshwrightTextDisplay" in NODE_CLASS_MAPPINGS

    assert len(NODE_CLASS_MAPPINGS) == len(NODE_DISPLAY_NAME_MAPPINGS)


def test_to_trimesh_conversions(tmp_path):
    cube = trimesh.creation.box(extents=[10, 10, 10])
    
    # 1. From trimesh directly
    assert to_trimesh(cube) is cube

    # 2. From dict with vertices and faces
    d = {"vertices": cube.vertices.tolist(), "faces": cube.faces.tolist()}
    m = to_trimesh(d)
    assert len(m.faces) == len(cube.faces)

    # 3. From dict with 'mesh'
    assert to_trimesh({"mesh": cube}) is cube

    # 4. From file path
    stl_path = str(tmp_path / "test.stl")
    cube.export(stl_path)
    loaded = to_trimesh(stl_path)
    assert len(loaded.faces) == len(cube.faces)


def test_fix_mesh_node_repair_hole():
    cube = trimesh.creation.box(extents=[20, 20, 20])
    broken_faces = cube.faces[:-2]  # punch hole
    broken = trimesh.Trimesh(vertices=cube.vertices, faces=broken_faces, process=False)
    assert not broken.is_watertight

    node = MeshwrightFixMesh()
    repaired, report, summary, is_wt = node.fix_mesh(broken, strict_watertight=True)

    assert repaired.is_watertight
    assert is_wt is True
    assert "GEEKATPLAY MESHWRIGHT - REPAIR REPORT" in report
    assert "WHAT WAS WRONG BEFORE" in report
    assert "ACTIONS & FIXES APPLIED" in report
    assert "Repaired" in summary


def test_reduce_mesh_node():
    ico = trimesh.creation.icosphere(subdivisions=3, radius=15)
    initial_faces = len(ico.faces)

    node = MeshwrightReduceMesh()
    # Decimate mode (keep 50%)
    reduced, report, pct = node.reduce(ico, mode="decimate", reduction_factor=0.5)

    assert len(reduced.faces) < initial_faces
    assert pct > 0
    assert "GEEKATPLAY MESHWRIGHT - REDUCTION REPORT" in report
    assert "Reduced Triangles:" in report


def test_load_model_node_demo():
    node = MeshwrightLoadModel()
    mesh, diag = node.load_model("")
    assert len(mesh.faces) > 0
    assert "Loaded built-in Demo Model" in diag


def test_compare_mesh_node():
    cube = trimesh.creation.box(extents=[15, 15, 15])
    broken = trimesh.Trimesh(vertices=cube.vertices, faces=cube.faces[:-2], process=False)

    node = MeshwrightCompareMesh()
    report, score_b, score_a, comp_img = node.compare(broken, cube)

    assert "BEFORE & AFTER COMPARISON" in report
    assert score_a >= score_b
    assert comp_img is not None


def test_preview_3d_node():
    cube = trimesh.creation.box(extents=[10, 10, 10])
    node = MeshwrightPreview3D()
    (tensor,) = node.preview(cube)
    assert tensor is not None


def test_save_mesh_node(tmp_path, monkeypatch):
    cube = trimesh.creation.box(extents=[10, 10, 10])
    node = MeshwrightSaveMesh()

    out_prefix = str(tmp_path / "output_test")
    file_path, status = node.save_mesh(cube, filename_prefix=out_prefix, format="stl")

    assert os.path.isfile(file_path)
    assert file_path.endswith(".stl")
    assert "Printable solid: True" in status


def test_render_mesh_software_rasterizer():
    cube = trimesh.creation.box(extents=[10, 10, 10])
    img = render_mesh_to_np(cube, width=128, height=128)
    assert img.shape == (128, 128, 3)
    assert img.dtype == np.float32
    assert img.min() >= 0.0 and img.max() <= 1.0

    comp = render_comparison_preview(cube, cube)
    assert comp.shape[0] == 512
    assert comp.shape[1] == 1028


def test_install_nodes_to_target(tmp_path):
    # Simulate a ComfyUI directory
    comfy_dir = tmp_path / "MockComfyUI"
    custom_nodes = comfy_dir / "custom_nodes"
    custom_nodes.mkdir(parents=True)

    res = install_nodes(str(comfy_dir))
    assert res["success"] is True

    installed_folder = custom_nodes / "Geekatplay-3D-MeshFix"
    assert installed_folder.is_dir()
    assert (installed_folder / "__init__.py").is_file()
    assert (installed_folder / "nodes.py").is_file()
    assert (installed_folder / "meshwright_config.json").is_file()
    assert (installed_folder / "examples" / "mesh_fix_workflow.json").is_file()
    assert (installed_folder / "examples" / "sample_model.obj").is_file()

    # Verify meshwright_config.json content
    with open(installed_folder / "meshwright_config.json", "r", encoding="utf-8") as f:
        cfg = json.load(f)
    assert "meshwright_root" in cfg


def test_workflow_json_validity():
    workflow_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "comfyui_nodes", "Geekatplay-3D-MeshFix", "examples", "mesh_fix_workflow.json"
    )
    assert os.path.isfile(workflow_path)

    with open(workflow_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert "nodes" in data
    assert "links" in data
    node_types = {n["type"] for n in data["nodes"]}
    assert "MeshwrightLoadModel" in node_types
    assert "MeshwrightFixMesh" in node_types
    assert "MeshwrightReduceMesh" in node_types
    assert "MeshwrightCompareMesh" in node_types
    assert "MeshwrightSaveMesh" in node_types
