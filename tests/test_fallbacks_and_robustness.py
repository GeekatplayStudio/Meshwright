import os
import sys
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import trimesh

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import engine.mesh_reducer as MR
import engine.mesh_repair as MRep
import engine.model_loader as ML
from app import AppApi
from engine.service import MeshService


def test_mesh_reducer_pymeshlab_fallback():
    # Force HAS_FAST_SIMPLIFY to False to exercise Strategy 2 (PyMeshLab)
    mesh = trimesh.creation.icosphere(subdivisions=3)
    initial_faces = len(mesh.faces)
    logs = []

    with patch.object(MR, "HAS_FAST_SIMPLIFY", False):
        reduced, info = MR.reduce_mesh(mesh, target_factor=0.5, log=lambda m, lvl: logs.append((m, lvl)))
        assert info["method_used"] == "pymeshlab"
        assert len(reduced.faces) < initial_faces
        assert reduced.is_watertight


def test_mesh_reducer_trimesh_quadric_fallback():
    # Force both fast_simplify and pymeshlab to False to exercise Strategy 3 (Trimesh quadric)
    mesh = trimesh.creation.icosphere(subdivisions=3)
    initial_faces = len(mesh.faces)
    logs = []

    with patch.object(MR, "HAS_FAST_SIMPLIFY", False), patch.object(MR, "HAS_PYMESHLAB", False):
        reduced, info = MR.reduce_mesh(mesh, target_factor=0.5, log=lambda m, lvl: logs.append((m, lvl)))
        assert info["method_used"] == "trimesh_quadric"
        assert len(reduced.faces) < initial_faces


def test_mesh_reducer_fast_simplify_exception_triggers_fallback():
    mesh = trimesh.creation.icosphere(subdivisions=2)
    logs = []

    with patch("fast_simplification.simplify", side_effect=RuntimeError("Simulated C++ crash")):
        reduced, info = MR.reduce_mesh(mesh, target_factor=0.5, log=lambda m, lvl: logs.append((m, lvl)))
        assert any("fast_simplification skipped" in m for m, _ in logs)
        assert info["method_used"] in ("pymeshlab", "trimesh_quadric")


def test_mesh_repair_pymeshlab_stage():
    # Create an open cylinder with a hole and non-manifold edges
    cyl = trimesh.creation.cylinder(radius=10, height=20, sections=16)
    # Remove top face cap to make it open/non-watertight
    cyl = trimesh.Trimesh(vertices=cyl.vertices, faces=cyl.faces[:-16], process=False)
    assert not cyl.is_watertight

    logs = []
    with patch.object(MRep, "HAS_MESHFIX", False):
        repaired, report = MRep.repair_mesh(cyl, strict_watertight=True, log=lambda m, lvl="info": logs.append(m))
        assert report["before"]["stats"]["face_count"] > 0
        assert "method_used" in report


def test_mesh_repair_manifold3d_stage():
    # Non-manifold edge (three triangles sharing one edge) which fill_holes cannot fix
    v = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1], [0, -1, 0]], dtype=float)
    f = np.array([[0, 1, 2], [0, 1, 3], [0, 1, 4]], dtype=int)
    nonman_mesh = trimesh.Trimesh(vertices=v, faces=f, process=False)

    logs = []
    with patch.object(MRep, "HAS_MESHFIX", False), patch.object(MRep, "HAS_PYMESHLAB", False):
        repaired, report = MRep.repair_mesh(nonman_mesh, strict_watertight=True, voxel_pitch=0.5, log=lambda m, lvl="info": logs.append(m))
        assert "method_used" in report
        assert any("Stage 5: Manifold3D" in l for l in logs)


def test_mesh_repair_voxel_remesh_stage():
    # Non-manifold edge where all prior engines are disabled
    v = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1], [0, -1, 0]], dtype=float)
    f = np.array([[0, 1, 2], [0, 1, 3], [0, 1, 4]], dtype=int)
    nonman_mesh = trimesh.Trimesh(vertices=v, faces=f, process=False)

    logs = []
    with patch.object(MRep, "HAS_MESHFIX", False), patch.object(MRep, "HAS_PYMESHLAB", False), patch.object(MRep, "HAS_MANIFOLD", False):
        repaired, report = MRep.repair_mesh(nonman_mesh, strict_watertight=True, voxel_pitch=0.5, log=lambda m, lvl="info": logs.append(m))
        assert report["method_used"] == "voxel_remesh"
        assert repaired.is_watertight


def test_model_loader_file_not_found():
    with pytest.raises(FileNotFoundError):
        ML.load_model("non_existent_file_12345.obj")


def test_model_loader_trimesh_fallback(tmp_path):
    # Test loading OBJ via Strategy 2 (Trimesh)
    box = trimesh.creation.box(extents=[10, 10, 10])
    obj_path = str(tmp_path / "fallback_cube.obj")
    box.export(obj_path)

    loaded, stats = ML.load_model(obj_path)
    assert len(loaded.faces) == 12
    assert stats["vertex_count"] > 0


def test_app_api_dialogs_with_and_without_window():
    svc = MeshService(autosave=False)
    api = AppApi(svc)

    # Without window attached: safely returns empty / None
    api.set_window(None)
    assert api.select_file_dialog() == ""
    assert api.select_folder_dialog() == ""
    assert api.select_image_dialog() == ""
    assert api._save_dialog("test.stl", ()) is None

    # With mocked window attached:
    mock_win = MagicMock()
    mock_win.create_file_dialog.side_effect = lambda mode, **kw: ["D:/test_path/file.obj"]
    api.set_window(mock_win)

    assert api.select_file_dialog() == "D:/test_path/file.obj"
    assert api.select_image_dialog() == "D:/test_path/file.obj"
    assert api.select_folder_dialog() == "D:/test_path/file.obj"
    assert api._save_dialog("test.stl", ()) == "D:/test_path/file.obj"

    # Reset state window
    api.set_window(None)


def test_model_loader_ufbx_quad_triangulation(tmp_path):
    class MockFace:
        def __init__(self, index_begin, num_indices):
            self.index_begin = index_begin
            self.num_indices = num_indices

    class MockMesh:
        def __init__(self):
            self.num_faces = 1
            self.num_triangles = 2
            self.num_indices = 4
            self.faces = [MockFace(0, 4)]
            self.vertex_position = MagicMock()
            self.vertex_position.values = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], dtype=np.float64).tobytes()
            self.vertex_position.indices = np.array([0, 1, 2, 3], dtype=np.uint32)
            self.vertex_uv = MagicMock()
            self.vertex_uv.exists = False
            self.vertex_uv.values = b""

    mock_scene = MagicMock()
    mock_scene.meshes = [MockMesh()]

    fake_fbx = str(tmp_path / "quad.fbx")
    with open(fake_fbx, "wb") as f:
        f.write(b"fake fbx binary")

    with patch("ufbx.load_file", return_value=mock_scene):
        loaded, stats = ML.load_model(fake_fbx)
        assert len(loaded.faces) == 2
        assert len(loaded.vertices) == 4

