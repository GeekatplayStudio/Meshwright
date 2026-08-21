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
