import trimesh

from engine.mesh_analysis import analyze_mesh, compare_analyses
from engine.mesh_repair import repair_mesh


def test_clean_cube_is_print_ready():
    a = analyze_mesh(trimesh.creation.box(extents=[10, 10, 10]))
    assert a["issues"] == []
    assert a["score"] == 100
    assert a["verdict"] == "Print ready"
    assert a["stats"]["genus"] == 0


def test_hole_detected_and_reported():
    cube = trimesh.creation.box(extents=[20, 20, 20])
    broken = trimesh.Trimesh(vertices=cube.vertices, faces=cube.faces[:-2], process=False)
    a = analyze_mesh(broken)
    ids = [i["id"] for i in a["issues"]]
    assert "holes" in ids
    assert a["stats"]["holes"] == 1
    assert a["stats"]["boundary_edges"] == 4
    assert a["verdict"] == "Repair required"


def test_inverted_mesh_detected():
    cube = trimesh.creation.box(extents=[10, 10, 10])
    inv = trimesh.Trimesh(vertices=cube.vertices, faces=cube.faces[:, ::-1], process=False)
    a = analyze_mesh(inv)
    assert a["stats"]["inverted_normals"] is True
    assert any(i["id"] == "inverted" for i in a["issues"])


def test_repair_report_lists_fixes_and_changes():
    cube = trimesh.creation.box(extents=[20, 20, 20])
    broken = trimesh.Trimesh(vertices=cube.vertices, faces=cube.faces[:-2], process=False)
    repaired, report = repair_mesh(broken)
    assert repaired.is_watertight
    assert report["fixes"], "at least one fix should be recorded"
    labels = {c["label"]: c for c in report["changes"]}
    assert labels["Watertight"]["after"] is True
    assert labels["Open holes"]["after"] == 0
    assert report["after"]["verdict"] == "Print ready"


def test_compare_analyses_empty_when_identical():
    a = analyze_mesh(trimesh.creation.box())
    assert compare_analyses(a, a) == []
