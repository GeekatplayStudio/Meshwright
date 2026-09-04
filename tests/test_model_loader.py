import pytest
import trimesh

from engine.model_loader import get_mesh_stats, load_model


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


def test_degenerate_and_duplicate_faces_are_both_dropped_with_their_uvs(tmp_path):
    """
    Cleanup runs in two passes, and each mask has to be measured against the faces
    that are there when it is applied. Building both up front is wrong the moment
    the first pass removes anything: the second mask is then too long, and the UV
    channel it is applied to raises

        IndexError: boolean index did not match indexed array along axis 0

    A real 3.1-million-face model hit this on twelve degenerate triangles.
    """
    import numpy as np
    import trimesh

    box = trimesh.creation.box(extents=[10, 10, 10])
    faces = np.vstack([
        box.faces,
        [[0, 0, 1], [2, 2, 3]],      # degenerate: removed by the first pass
        box.faces[:4],               # duplicates: removed by the second
    ])
    dirty = trimesh.Trimesh(vertices=box.vertices, faces=faces, process=False)
    dirty.visual = trimesh.visual.TextureVisuals(
        uv=np.random.default_rng(0).random((len(box.vertices), 2)))

    path = tmp_path / "dirty.glb"
    dirty.export(path)

    mesh, _ = load_model(str(path), with_stats=False)
    corner_uv = mesh.metadata.get("corner_uv")

    assert len(mesh.faces) == len(box.faces), "the junk faces should be gone"
    assert corner_uv is not None, "the UV channel was dropped instead of following the masks"
    assert len(corner_uv) == len(mesh.faces)


def test_a_clean_mesh_keeps_every_face(tmp_path):
    import numpy as np
    import trimesh

    box = trimesh.creation.box(extents=[10, 10, 10])
    box.visual = trimesh.visual.TextureVisuals(
        uv=np.random.default_rng(1).random((len(box.vertices), 2)))
    path = tmp_path / "clean.glb"
    box.export(path)

    mesh, _ = load_model(str(path), with_stats=False)
    assert len(mesh.faces) == len(box.faces)
    assert len(mesh.metadata["corner_uv"]) == len(mesh.faces)
