"""
The diagnostics do not just count problems, they point at them: clicking an issue
frames it in the viewport. That means every issue that can be located has to carry
real coordinates, and the code that finds them runs only when the defect is present.

A refactor of the duplicate detection once kept the totals and dropped the masks the
locations are built from, which no test noticed because no fixture had duplicates.
These build meshes that do.
"""
import numpy as np
import pytest
import trimesh

from engine.mesh_analysis import analyze_mesh


def _issues(mesh):
    return {issue["id"]: issue for issue in analyze_mesh(mesh, "fixture.stl")["issues"]}


@pytest.fixture
def defective():
    """A box with duplicate faces, duplicate vertices and unreferenced vertices."""
    box = trimesh.creation.box(extents=[10, 10, 10])
    return trimesh.Trimesh(
        vertices=np.vstack([box.vertices, box.vertices[:2]]),   # 2 duplicates, unreferenced
        faces=np.vstack([box.faces, box.faces[:3]]),            # 3 duplicate triangles
        process=False)


def test_duplicate_faces_are_counted_and_located(defective):
    issue = _issues(defective)["dupfaces"]
    assert issue["count"] == 3
    assert issue["location"] is not None
    assert len(issue["location"]["points"]) > 0


def test_duplicate_vertices_are_counted_and_located(defective):
    issue = _issues(defective)["dupverts"]
    assert issue["count"] == 2
    assert issue["location"] is not None
    assert len(issue["location"]["points"]) > 0


def test_stats_agree_with_the_issues(defective):
    report = analyze_mesh(defective, "fixture.stl")
    ids = {i["id"]: i for i in report["issues"]}
    assert report["stats"]["duplicate_faces"] == ids["dupfaces"]["count"]
    assert report["stats"]["duplicate_vertices"] == ids["dupverts"]["count"]


def test_a_clean_mesh_reports_none_of_them():
    ids = _issues(trimesh.creation.box(extents=[10, 10, 10]))
    assert "dupfaces" not in ids
    assert "dupverts" not in ids


def test_located_points_sit_on_the_model(defective):
    """A location that is not near the geometry sends the camera nowhere useful."""
    lo, hi = defective.bounds
    for name in ("dupfaces", "dupverts"):
        points = np.asarray(_issues(defective)[name]["location"]["points"])
        assert np.all(points >= lo - 1e-6) and np.all(points <= hi + 1e-6), name


def test_open_edges_are_located_on_an_open_mesh():
    """The boundary path feeds hole counting too, and shares its edge grouping."""
    plane = trimesh.Trimesh(
        vertices=[[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0]],
        faces=[[0, 1, 2], [0, 2, 3]], process=False)
    report = analyze_mesh(plane, "plane.stl")
    ids = {i["id"]: i for i in report["issues"]}

    assert report["stats"]["boundary_edges"] == 4
    assert report["stats"]["holes"] == 1
    assert ids["holes"]["location"] is not None
