import pytest
import trimesh
from engine.model_loader import load_model, get_mesh_stats


@pytest.fixture
def temp_obj_file(tmp_path):
    box = trimesh.creation.box(extents=[10, 20, 30])
    file_path = str(tmp_path / "test_cube.obj")
    box.export(file_path)
    return file_path


@pytest.fixture
def temp_stl_file(tmp_path):
    sphere = trimesh.creation.icosphere(subdivisions=2, radius=15)
    file_path = str(tmp_path / "test_sphere.stl")
    sphere.export(file_path)
    return file_path


def test_load_model_obj(temp_obj_file):
    mesh, stats = load_model(temp_obj_file)
    assert isinstance(mesh, trimesh.Trimesh)
    assert stats["face_count"] > 0
    assert stats["vertex_count"] > 0
    assert stats["filename"] == "test_cube.obj"
    assert stats["dimensions_mm"] == [10.0, 20.0, 30.0]


def test_load_model_stl(temp_stl_file):
    mesh, stats = load_model(temp_stl_file)
    assert isinstance(mesh, trimesh.Trimesh)
    assert stats["is_watertight"] is True
    assert stats["volume_cm3"] > 0


def test_load_nonexistent_file():
    with pytest.raises(FileNotFoundError):
        load_model("non_existent_file_path_123.obj")


def test_get_mesh_stats():
    mesh = trimesh.creation.cylinder(radius=10, height=50)
    stats = get_mesh_stats(mesh, "cylinder.stl")
    assert stats["face_count"] == len(mesh.faces)
    assert stats["vertex_count"] == len(mesh.vertices)
    assert "dimensions_mm" in stats
    assert "volume_cm3" in stats
