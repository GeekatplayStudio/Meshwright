import os
import sys

import numpy as np
import pytest
import trimesh

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import AppApi
from engine.service import MeshService, ServiceError
from engine.validation import ValidationError


def _api():
    return AppApi(MeshService(autosave=False))


def _load_mesh(api, mesh, tmp_path, name="m.stl"):
    p = str(tmp_path / name)
    mesh.export(p)
    return api.load_model_file(p)


def test_app_api_workflow(tmp_path):
    api = _api()
    cube = trimesh.creation.box(extents=[30, 40, 50])
    res_load = _load_mesh(api, cube, tmp_path, "api_test_cube.obj")
    assert res_load["success"] is True
    assert res_load["stats"]["dimensions_mm"] == [30.0, 40.0, 50.0]
    assert res_load["preview"]["face_count"] == 12
    assert res_load["state_id"] == 1 and res_load["can_undo"] is False

    res_fix = api.auto_fix_mesh(strict_watertight=True)
    assert res_fix["success"] is True and res_fix["stats"]["is_watertight"] is True
    assert res_fix["state_id"] == 2 and res_fix["can_undo"] is True

    res_reduce = api.reduce_mesh_quality(reduction_factor=0.5)
    assert res_reduce["success"] is True
    assert res_reduce["info"]["final_faces"] < res_fix["stats"]["face_count"]


def test_shells_listed_and_removed(tmp_path):
    api = _api()
    a = trimesh.creation.box(extents=[10, 10, 10])
    b = trimesh.creation.box(extents=[2, 2, 2])
    b.apply_translation([30, 0, 0])
    res = _load_mesh(api, trimesh.util.concatenate([a, b]), tmp_path)
    assert len(res["shells"]) == 2
    assert res["shells"][0]["faces"] >= res["shells"][1]["faces"]
    res2 = api.remove_shells([1])
    assert res2["success"] and "shells" not in res2
    assert res2["stats"]["face_count"] == 12
    # bad indices are rejected cleanly
    assert api.remove_shells([5])["success"] is False
    assert api.remove_shells("1")["success"] is False


def test_rotation_matrix_validation_and_shell_sync(tmp_path):
    api = _api()
    a = trimesh.creation.box(extents=[20, 20, 20])
    b = trimesh.creation.box(extents=[4, 4, 4])
    b.apply_translation([40, 0, 0])
    _load_mesh(api, trimesh.util.concatenate([a, b]), tmp_path)
    before = api.svc.shells[1].bounds.mean(axis=0)

    r = trimesh.transformations.rotation_matrix(np.radians(90), [0, 0, 1])[:3, :3]
    res = api.apply_rotation(r.tolist())
    assert res["success"] and "centre" in res and "bounds" in res
    after = api.svc.shells[1].bounds.mean(axis=0)
    assert abs(before[1]) < 1e-6 and after[1] > 10
    assert api.apply_rotation(np.eye(3).tolist())["unchanged"] is True
    assert api.apply_rotation([[1, 0, 0], [0, 2, 0], [0, 0, 1]])["success"] is False
    assert api.apply_rotation("nope")["success"] is False


def test_rotation_moves_issue_locations_with_mesh(tmp_path):
    api = _api()
    slab = trimesh.creation.box(extents=[30, 12, 7])
    broken = trimesh.Trimesh(slab.vertices, slab.faces[:-1], process=False)
    _load_mesh(api, broken, tmp_path)
    before = np.array(api.svc.current.analysis["issues"][0]["location"]["points"])
    r = trimesh.transformations.rotation_matrix(np.radians(90), [0, 0, 1])[:3, :3]
    res = api.apply_rotation(r.tolist())
    c = np.array(res["centre"])
    expected = (r @ (before - c).T).T + c
    after = np.array(api.svc.current.analysis["issues"][0]["location"]["points"])
    assert np.allclose(after, expected, atol=1e-3)
    d = np.min(np.linalg.norm(api.svc.mesh.vertices[None, :, :] - after[:, None, :], axis=2), axis=1)
    assert d.max() < 1e-3


def test_undo_redo_and_revert_never_lose_state(tmp_path):
    api = _api()
    cube = trimesh.creation.box(extents=[20, 20, 20])
    broken = trimesh.Trimesh(cube.vertices, cube.faces[:-2], process=False)
    _load_mesh(api, broken, tmp_path)

    fixed = api.auto_fix_mesh(True)
    assert fixed["stats"]["is_watertight"] and fixed["state_id"] == 2
    # a re-analysis must agree with what we show — nothing reverted silently
    again = api.analyze_current()
    assert again["analysis"]["verdict"] == fixed["analysis"]["verdict"]
    assert again["state_id"] == 2

    u = api.undo()
    assert u["state_id"] == 1 and not u["stats"]["is_watertight"] and u["can_redo"]
    r = api.redo()
    assert r["state_id"] == 2 and r["stats"]["is_watertight"]
    assert api.redo()["success"] is False   # nothing left to redo

    rev = api.revert_to_original()
    assert rev["success"] and not rev["stats"]["is_watertight"]
    assert rev["state_id"] == 3              # revert is itself a new state...
    assert api.undo()["state_id"] == 2       # ...so it can be undone
    assert [s["id"] for s in api.list_states()["states"]] == [1, 2]


def test_guard_rejects_operations_that_make_things_worse(tmp_path):
    svc = MeshService(autosave=False)
    cube = trimesh.creation.box(extents=[20, 20, 20])
    p = str(tmp_path / "c.stl")
    cube.export(p)
    svc.load(p)
    # simulate a destructive "repair" by committing a broken mesh
    broken = trimesh.Trimesh(cube.vertices, cube.faces[:-2], process=False)
    res = svc._commit(broken, "bad_op")
    assert res["success"] is False and res["rejected"] is True
    assert "critical" in res["reason"]
    assert svc.current.id == 1 and svc.mesh.is_watertight        # previous state kept
    forced = svc._commit(broken, "bad_op", force=True)
    assert forced["success"] and forced["state_id"] == 2         # explicit override works
    tiny = trimesh.creation.box(extents=[1, 1, 1])
    big = trimesh.creation.icosphere(subdivisions=3)
    svc._commit(big, "big", force=True)
    res = svc._commit(tiny, "collapse")
    assert res["rejected"] and "geometry would be lost" in res["reason"]


def test_validation_rejects_bad_inputs(tmp_path):
    svc = MeshService(autosave=False)
    with pytest.raises(ValidationError):
        svc.load(str(tmp_path / "missing.stl"))
    with pytest.raises(ValidationError):
        svc.load(__file__)          # wrong extension
    with pytest.raises(ServiceError):
        svc.repair()
    cube = trimesh.creation.box()
    p = str(tmp_path / "c.stl")
    cube.export(p)
    svc.load(p)
    with pytest.raises(ValidationError):
        svc.export_stl(str(tmp_path / "nope" / "x.stl"))
    with pytest.raises(ValidationError):
        svc.export_stl(str(tmp_path / "x.stl"), scale_unit="furlongs")
    # numbers are clamped, not rejected
    assert svc.simplify(keep_fraction=5.0)["success"]


def test_export_and_report_through_adapter(tmp_path):
    api = _api()

    class Win:
        def __init__(self, ret): self.ret = ret
        def create_file_dialog(self, *a, **k): return self.ret
    cube = trimesh.creation.box(extents=[10, 10, 10])
    _load_mesh(api, cube, tmp_path, "input.obj")

    api.set_window(Win(None))
    assert api.export_stl_file()["canceled"] is True
    out = str(tmp_path / "export_success.stl")
    api.set_window(Win([out]))
    res = api.export_stl_file(scale_unit="mm", align_origin=True)
    assert res["success"] and os.path.exists(out)
    rep = str(tmp_path / "r.json")
    api.set_window(Win(rep))
    assert api.export_report()["success"] and os.path.exists(rep)
    api.set_window(None)


@pytest.mark.parametrize("export_format", ["obj", "ply", "off", "glb", "gltf", "3mf"])
def test_export_other_formats_through_adapter(tmp_path, export_format):
    api = _api()

    class Win:
        def __init__(self, ret): self.ret = ret
        def create_file_dialog(self, *a, **k): return self.ret

    _load_mesh(api, trimesh.creation.box(), tmp_path, "input.stl")
    out = str(tmp_path / f"export.{export_format}")
    api.set_window(Win(out))
    res = api.export_model_file(export_format, scale_unit="mm", align_origin=True)
    assert res["success"] and os.path.exists(out)
    api.set_window(None)


def test_no_model_actions():
    api = _api()
    for fn in (api.auto_fix_mesh, api.reduce_mesh_quality, api.undo, api.redo, api.analyze_current):
        assert fn()["success"] is False


def test_demo_model_loads_and_is_repairable():
    """A fresh install must be provable without the user owning a 3D file."""
    api = _api()
    res = api.load_demo_model()
    assert res["success"] is True and res.get("is_demo") is True
    assert res["stats"]["face_count"] > 100
    assert res["stats"]["is_watertight"] is False        # it is meant to be broken
    ids = {i["id"] for i in res["analysis"]["issues"]}
    assert "holes" in ids

    fixed = api.auto_fix_mesh(True, False)
    assert fixed["success"] is True
    assert fixed["analysis"]["score"] > res["analysis"]["score"]
    assert fixed["stats"]["is_watertight"] is True
