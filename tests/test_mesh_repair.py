import trimesh

from engine.mesh_repair import repair_mesh


def test_repair_clean_mesh():
    cube = trimesh.creation.box(extents=[10, 10, 10])
    repaired, report = repair_mesh(cube, strict_watertight=True)
    assert repaired.is_watertight
    assert report["repaired_watertight"] is True
    assert report["final_faces"] > 0


def test_repair_broken_mesh_with_holes():
    cube = trimesh.creation.box(extents=[20, 20, 20])
    # Create an open hole by removing 2 faces
    broken_faces = cube.faces[:-2]
    broken = trimesh.Trimesh(vertices=cube.vertices, faces=broken_faces, process=False)

    assert not broken.is_watertight
    repaired, report = repair_mesh(broken, strict_watertight=True)

    assert repaired.is_watertight
    assert report["repaired_watertight"] is True
    assert len(report["steps_applied"]) > 0


def test_repair_voxel_fallback():
    # Construct a severe non-manifold triangle soup
    v = [[0, 0, 0], [10, 0, 0], [0, 10, 0], [5, 5, 10]]
    f = [[0, 1, 2], [0, 1, 3]]
    soup = trimesh.Trimesh(vertices=v, faces=f, process=False)

    repaired, report = repair_mesh(soup, strict_watertight=True)
    assert repaired.is_watertight
    assert report["repaired_watertight"] is True


def test_repair_reports_passes_and_verified_after():
    cube = trimesh.creation.box(extents=[20, 20, 20])
    broken = trimesh.Trimesh(vertices=cube.vertices, faces=cube.faces[:-2], process=False)
    repaired, report = repair_mesh(broken, strict_watertight=True)
    assert report["passes"] >= 1
    # "after" is a fresh analysis of the returned mesh, not a prediction
    from engine.mesh_analysis import analyze_mesh
    assert report["after"]["stats"]["face_count"] == len(repaired.faces)
    assert analyze_mesh(repaired)["verdict"] == report["after"]["verdict"]


# ------------------------------------------------------------------ the voxel stage puts the model back
def test_voxel_rebuild_lands_where_the_model_was():
    """
    trimesh returns the marching-cubes surface in voxel index space. A 40 mm sphere came
    back 151 units across and centred on (75, 75, 75); the earlier test only checked that
    the result was watertight, on a one-unit fin at the origin where the two spaces are
    almost the same. So the sphere is large and well away from the origin here, where a
    missed scale or a missed shift cannot hide.
    """
    import numpy as np

    import engine.mesh_repair as MRep
    ball = trimesh.creation.icosphere(subdivisions=3, radius=20.0)
    ball.apply_translation([100.0, -50.0, 30.0])
    pitch = 40.0 / 100.0

    rebuilt = MRep._voxel_rebuild(ball, pitch)

    assert np.all(np.abs(rebuilt.bounds - ball.bounds) <= 2.0 * pitch + 0.8), (rebuilt.bounds, ball.bounds)
    assert abs(rebuilt.volume - ball.volume) / ball.volume < 0.06, "the rebuilt solid is the wrong size"
    assert rebuilt.is_watertight


def test_voxel_stage_of_a_repair_keeps_the_model_in_place():
    """The same, through the whole repair, with every earlier engine out of the way."""
    from unittest.mock import patch

    import numpy as np

    import engine.mesh_repair as MRep
    fin = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1], [0, -1, 0]], dtype=float)
    fin = fin * 30.0 + np.array([100.0, -50.0, 30.0])                     # big, and far from the origin
    mesh = trimesh.Trimesh(vertices=fin, faces=[[0, 1, 2], [0, 1, 3], [0, 1, 4]], process=False)

    with patch.object(MRep, "HAS_MESHFIX", False), patch.object(MRep, "HAS_PYMESHLAB", False), \
            patch.object(MRep, "HAS_MANIFOLD", False):
        repaired, report = MRep.repair_mesh(mesh, strict_watertight=True)

    assert report["method_used"] == "voxel_remesh"
    assert repaired.is_watertight
    tolerance = 2.0 * (max(mesh.extents) / 150.0) + 0.02 * max(mesh.extents)
    assert np.all(np.abs(repaired.bounds - mesh.bounds) <= tolerance), \
        f"the repaired model moved: {repaired.bounds.tolist()} vs {mesh.bounds.tolist()}"


def test_a_voxel_rebuild_that_does_not_line_up_is_refused():
    """A result in the wrong place must not be returned; the caller keeps the mesh it had."""
    from unittest.mock import PropertyMock, patch

    import numpy as np
    import pytest
    from trimesh.voxel.base import VoxelGrid

    import engine.mesh_repair as MRep
    ball = trimesh.creation.icosphere(subdivisions=2, radius=20.0)
    ball.apply_translation([100.0, 0.0, 0.0])

    # A grid whose transform cannot bring the result home.
    with patch.object(VoxelGrid, "transform", new_callable=PropertyMock, return_value=np.eye(4)):
        with pytest.raises(ValueError, match="does not line up"):
            MRep._voxel_rebuild(ball, 0.4)
