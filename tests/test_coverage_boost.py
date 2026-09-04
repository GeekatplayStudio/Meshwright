import os
from unittest import mock

import trimesh

from app import AppApi
from engine.mesh_repair import repair_mesh
from engine.model_loader import load_model
from engine.stl_exporter import export_to_stl


def test_load_scene(tmp_path):
    box1 = trimesh.creation.box(extents=[10, 10, 10])
    box2 = trimesh.creation.box(extents=[10, 10, 10])
    box2.apply_translation([20, 0, 0])
    scene = trimesh.Scene([box1, box2])

    scene_path = str(tmp_path / "test_scene.glb")
    scene.export(scene_path)

    mesh, stats = load_model(scene_path)
    assert isinstance(mesh, trimesh.Trimesh)
    assert stats["face_count"] > 0


def test_repair_mesh_custom_pitch():
    box = trimesh.creation.box(extents=[10, 10, 10])
    repaired, report = repair_mesh(box, strict_watertight=True, voxel_pitch=0.5)
    assert repaired.is_watertight
    assert report["repaired_watertight"] is True


def test_export_to_stl_no_align(tmp_path):
    cube = trimesh.creation.box(extents=[10, 10, 10])
    cube.apply_translation([50, 50, 50])
    out_path = str(tmp_path / "output_no_align.stl")

    res = export_to_stl(cube, out_path, align_origin=False)
    assert os.path.exists(out_path)
    assert res["bounds_min"][2] == 45.0  # Center was at 50, extent 10 -> min Z is 45


def test_app_api_select_file_dialog():
    api = AppApi()
    mock_window = mock.MagicMock()
    mock_window.create_file_dialog.return_value = ["C:/models/test.obj"]
    api.set_window(mock_window)

    selected = api.select_file_dialog()
    assert selected == "C:/models/test.obj"


def test_app_api_no_model_actions():
    from engine.service import MeshService
    api = AppApi(MeshService(autosave=False))
    res_fix = api.auto_fix_mesh()
    assert res_fix["success"] is False

    res_red = api.reduce_mesh_quality()
    assert res_red["success"] is False

    res_exp = api.export_stl_file()
    assert res_exp["success"] is False
