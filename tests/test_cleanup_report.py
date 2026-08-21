import json
import numpy as np
import trimesh
from engine.mesh_cleanup import fix_slivers
from engine.mesh_analysis import analyze_mesh


def _needle_box():
    m = trimesh.creation.box(extents=[20, 20, 20]).subdivide()
    v, f = m.vertices.copy(), m.faces.copy()
    a, b, c = f[0]
    v = np.vstack([v, v[a] + (v[b] - v[a]) * 0.002])
    n = len(v) - 1
    f = np.vstack([f[1:], [[a, n, c], [n, b, c]]])
    adj = [i for i, fa in enumerate(f) if a in fa and b in fa and n not in fa]
    for i in adj:
        fa = list(f[i])
        o = [x for x in fa if x not in (a, b)][0]
        ia, ib = fa.index(a), fa.index(b)
        t1, t2 = ([a, n, o], [n, b, o]) if (ib - ia) % 3 == 1 else ([n, a, o], [b, n, o])
        f = np.vstack([f[np.arange(len(f)) != i], [t1, t2]])
    return trimesh.Trimesh(v, f, process=False)


def test_fix_slivers_collapses_needle_and_stays_watertight():
    m = _needle_box()
    assert analyze_mesh(m)["stats"]["sliver_faces"] > 0
    r, info = fix_slivers(m)
    assert info["after"] == 0 and info["collapsed"] >= 1
    assert r.is_watertight


def test_issue_locations_present():
    cube = trimesh.creation.box(extents=[20, 20, 20])
    broken = trimesh.Trimesh(cube.vertices, cube.faces[:-2], process=False)
    holes = [i for i in analyze_mesh(broken)["issues"] if i["id"] == "holes"][0]
    assert holes["location"]["total"] == 4
    assert len(holes["location"]["center"]) == 3


def test_export_report(tmp_path):
    from engine.service import MeshService
    svc = MeshService(autosave=False)
    p = str(tmp_path / "c.stl")
    trimesh.creation.box().export(p)
    svc.load(p)
    svc.fix_slivers()
    out = str(tmp_path / "r.json")
    assert svc.export_report(out)["success"]
    doc = json.loads(open(out, encoding="utf-8").read())
    assert doc["application"] == "Meshwright" and doc["studio"] == "Geekatplay Studio"
    assert doc["analysis"]["verdict"] == "Print ready"
    assert [o["operation"] for o in doc["operations"]] == ["load", "fix_slivers"]
    assert all("location" not in i for i in doc["analysis"]["issues"])
