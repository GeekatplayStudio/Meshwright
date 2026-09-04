import json

import numpy as np
import trimesh

from engine.mesh_analysis import analyze_mesh
from engine.mesh_cleanup import fix_slivers


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


def _topology(m):
    import numpy as np
    _, counts = np.unique(m.edges_sorted, axis=0, return_counts=True)
    return int((counts == 1).sum()), int((counts > 2).sum())


def test_fix_slivers_never_opens_a_watertight_mesh():
    """The collapse/flip operations must respect the link condition."""
    import numpy as np
    m = trimesh.creation.icosphere(subdivisions=4)
    m.apply_scale(20)
    v, f = m.vertices.copy(), m.faces.copy()
    # sprinkle needles across the surface
    for k in range(0, 200, 9):
        a, b, c = f[k]
        v = np.vstack([v, v[a] + (v[b] - v[a]) * 0.0012])
        n = len(v) - 1
        f = np.vstack([f[np.arange(len(f)) != k], [[a, n, c], [n, b, c]]])
        for i in [i for i, fa in enumerate(f) if a in fa and b in fa and n not in fa]:
            fa = list(f[i])
            o = [x for x in fa if x not in (a, b)][0]
            ia, ib = fa.index(a), fa.index(b)
            t1, t2 = ([a, n, o], [n, b, o]) if (ib - ia) % 3 == 1 else ([n, a, o], [b, n, o])
            f = np.vstack([f[np.arange(len(f)) != i], [t1, t2]])
    bad = trimesh.Trimesh(v, f, process=False)
    assert bad.is_watertight
    assert analyze_mesh(bad)["stats"]["sliver_faces"] > 10

    fixed, info = fix_slivers(bad)
    b0, n0 = _topology(bad)
    b1, n1 = _topology(fixed)
    assert b1 <= b0 and n1 <= n0, info      # never more open or non-manifold edges
    assert fixed.is_watertight
    # Either slivers were removed, or the engine explains which ones it refused to touch.
    assert info["after"] <= info["before"]
    assert info["after"] < info["before"] or info["skipped"] > 0, info


def test_fix_slivers_reports_what_it_could_not_remove():
    m = trimesh.creation.box(extents=[10, 10, 10])
    fixed, info = fix_slivers(m)
    assert info["before"] == 0 and info["after"] == 0
    assert info["skipped"] == 0
    assert fixed.is_watertight
