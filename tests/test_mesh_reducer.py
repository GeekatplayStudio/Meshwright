import trimesh
from engine.mesh_reducer import reduce_mesh


def test_reduce_mesh_factor():
    ico = trimesh.creation.icosphere(subdivisions=4, radius=20)
    initial_faces = len(ico.faces)

    reduced, info = reduce_mesh(ico, target_factor=0.5)

    assert len(reduced.faces) < initial_faces
    assert info["final_faces"] < initial_faces
    assert info["reduction_percentage"] > 0


def test_reduce_mesh_target_faces():
    ico = trimesh.creation.icosphere(subdivisions=3, radius=20)
    target = 300

    reduced, info = reduce_mesh(ico, target_faces=target)

    assert abs(len(reduced.faces) - target) < 50
    assert info["final_faces"] < len(ico.faces)


def test_reduce_tiny_mesh():
    pyramid = trimesh.creation.cone(radius=5, height=10)
    reduced, info = reduce_mesh(pyramid, target_factor=0.5)
    assert len(reduced.faces) > 0


def test_simplify_never_rejected_on_problem_mesh(tmp_path):
    import numpy as np
    import trimesh
    from engine.service import MeshService
    m = trimesh.creation.icosphere(subdivisions=4)
    rng = np.random.default_rng(1)
    keep = np.ones(len(m.faces), bool)
    keep[rng.choice(len(m.faces), 80, replace=False)] = False
    p = str(tmp_path / "holey.stl")
    trimesh.Trimesh(m.vertices, m.faces[keep], process=False).export(p)
    svc = MeshService(autosave=False)
    before = svc.load(p)["stats"]["face_count"]
    res = svc.simplify(0.3)
    assert res["success"] and not res.get("rejected")
    assert res["stats"]["face_count"] < before * 0.5
