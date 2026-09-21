import numpy as np
import trimesh

from engine.mesh_analysis import analyze_mesh
from engine.mesh_retopo import HAS_QUADRIFLOW, deviation, retopologize
from engine.service import MeshService


def _organic():
    m = trimesh.creation.icosphere(subdivisions=5)
    m.apply_scale(30)
    m.vertices += 0.8 * np.sin(m.vertices * 0.9)
    return m


def test_retopo_reaches_target_and_stays_watertight():
    m = _organic()
    for method in ("quadriflow", "isotropic", "quadric"):
        r, info = retopologize(m, 800, method=method)
        assert 600 <= info["final_faces"] <= 1100, (method, info)
        assert r.is_watertight, method
        assert info["deviation"]["relative_pct"] < 5, (method, info["deviation"])
        if method == "quadriflow" and HAS_QUADRIFLOW:
            assert info["method_used"] == "quadriflow"


def test_retopo_keeps_separate_pieces_and_closes_open_input():
    box = trimesh.creation.box(extents=[20, 20, 20]).subdivide().subdivide().subdivide()
    sph = trimesh.creation.icosphere(5, radius=6)
    sph.apply_translation([30, 0, 0])
    two = trimesh.util.concatenate([box, sph])
    two = trimesh.Trimesh(two.vertices, two.faces[:-3], process=False)
    r, info = retopologize(two, 600)
    st = analyze_mesh(r)["stats"]
    assert st["body_count"] == 2
    assert info["pieces"] == 2
    assert info["final_faces"] < 900


def test_retopo_noop_when_already_small():
    r, info = retopologize(trimesh.creation.box(), 1000)
    assert info["method_used"] == "none"


def test_deviation_zero_for_identical():
    m = trimesh.creation.icosphere(3)
    assert deviation(m, m)["max_mm"] < 0.2


def test_service_retopo_validates_and_commits(tmp_path):
    svc = MeshService(autosave=False)
    p = str(tmp_path / "o.stl")
    _organic().export(p)
    n = svc.load(p)["stats"]["face_count"]
    res = svc.retopo(500, method="quadric")
    assert res["success"] and res["state_id"] == 2
    assert res["stats"]["face_count"] < n * 0.1
    assert "deviation" in res["info"]
    import pytest

    from engine.validation import ValidationError
    with pytest.raises(ValidationError):
        svc.retopo(500, method="magic")


def test_retopo_never_returns_worse_topology_than_source():
    """Remeshers can open holes on complex shapes; the engine must repair or fall back."""
    m = trimesh.creation.icosphere(subdivisions=5)
    m.apply_scale(20)
    # a torus-like handle makes the topology non-trivial for a quad field
    tor = trimesh.creation.torus(major_radius=14, minor_radius=3)
    src = trimesh.util.concatenate([m, tor])
    assert src.is_watertight
    r, info = retopologize(src, 2000)
    assert r.is_watertight, info
    assert analyze_mesh(r)["stats"]["holes"] == 0
    assert analyze_mesh(r)["stats"]["nonmanifold_edges"] == 0


def test_small_pieces_are_decimated_not_retopologised():
    big = trimesh.creation.icosphere(subdivisions=5, radius=20)
    tiny = trimesh.creation.icosphere(subdivisions=1, radius=1)
    tiny.apply_translation([40, 0, 0])
    r, info = retopologize(trimesh.util.concatenate([big, tiny]), 2000)
    assert info["small_pieces_decimated"] >= 1
    assert analyze_mesh(r)["stats"]["body_count"] == 2


def test_failing_logger_does_not_abort_retopology():
    def boom(msg, level="info"):
        raise UnicodeEncodeError("charmap", "x", 0, 1, "boom")
    m = trimesh.creation.icosphere(subdivisions=4, radius=10)
    r, info = retopologize(m, 500, log=boom)
    assert info["final_faces"] < 900 and r.is_watertight


# ------------------------------------------------------------------ a missing engine is explained
def test_a_method_that_needs_meshlab_says_so_when_meshlab_is_missing(monkeypatch, tmp_path):
    """
    PyMeshLab is optional (the installer carries on without it, and the lite edition
    leaves it out). Picking Decimate used to answer "Retopology made no change", which
    names nothing and suggests nothing.
    """
    import pytest

    import engine.mesh_retopo as retopo
    from engine.service import ServiceError

    path = str(tmp_path / "ball.stl")
    trimesh.creation.icosphere(subdivisions=4, radius=20).export(path)
    svc = MeshService(autosave=False)
    svc.load(path)
    monkeypatch.setattr(retopo, "HAS_PYMESHLAB", False)

    for method, label in (("quadric", "Decimate"), ("isotropic", "Uniform")):
        with pytest.raises(ServiceError) as raised:
            svc.retopo(500, method=method)
        text = str(raised.value)
        assert "MeshLab" in text and label in text, text
        assert "Smart retopology" in text, "it should say what to do instead"

    # what does not need MeshLab is untouched
    assert retopo.missing_engine_message("quadriflow") is None
    assert svc.retopo(500, method="quadriflow")["success"]


def test_meshlab_methods_are_not_blocked_when_meshlab_is_present():
    import engine.mesh_retopo as retopo
    if retopo.HAS_PYMESHLAB:
        assert retopo.missing_engine_message("quadric") is None
        assert retopo.missing_engine_message("isotropic") is None
