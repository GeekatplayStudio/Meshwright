import time

import trimesh

from engine.service import MeshService
from engine.session_store import SessionStore


def test_autosave_and_recovery_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    svc = MeshService(autosave=True)
    p = str(tmp_path / "c.stl")
    cube = trimesh.creation.box(extents=[20, 20, 20])
    broken = trimesh.Trimesh(cube.vertices, cube.faces[:-2], process=False)
    broken.export(p)
    svc.load(p)
    svc.repair()
    svc.store.flush()
    session = svc.store.session_id
    # simulate a crash: no close() call
    del svc

    found = SessionStore.find_recoverable()
    assert [s["session"] for s in found] == [session]
    assert found[0]["last"]["operation"] == "repair"
    assert found[0]["last"]["faces"] == 14 or found[0]["last"]["faces"] > 10

    svc2 = MeshService(autosave=False)
    res = svc2.recover(session)
    assert res["success"] and res["operation"] == "recover"
    assert res["stats"]["is_watertight"]
    assert svc2.file_path == p
    assert SessionStore.find_recoverable() == []     # consumed


def test_clean_close_leaves_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    svc = MeshService(autosave=True)
    p = str(tmp_path / "c.stl")
    trimesh.creation.box().export(p)
    svc.load(p)
    svc.close()
    assert SessionStore.find_recoverable() == []


def test_snapshot_writes_are_non_blocking(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    svc = MeshService(autosave=True)
    p = str(tmp_path / "big.stl")
    trimesh.creation.icosphere(subdivisions=6).export(p)   # ~80k faces
    svc.load(p)
    t = time.perf_counter()
    svc.fix_slivers()
    elapsed = time.perf_counter() - t
    svc.store.flush()
    assert elapsed < 2.0
    svc.close()
