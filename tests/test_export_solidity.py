"""
Exporting has to answer one question a slicer cannot: is this a solid?

An open surface slices into a single-wall shell with no infill — the "my STL
came out one layer thick" report — so the exporter must recognise that case,
say so, and never quietly flip a model inside out.
"""
import os
import sys

import numpy as np
import trimesh

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.mesh_exporter import export_to_format, prepare_for_export, solidity_report


def _open_surface() -> trimesh.Trimesh:
    """A box with one face removed: looks closed on screen, is not a solid."""
    box = trimesh.creation.box(extents=[20, 20, 20])
    return trimesh.Trimesh(vertices=box.vertices.copy(), faces=box.faces[:-2].copy(), process=False)


def _hollow_shell() -> trimesh.Trimesh:
    """A 40 mm cube shell with 0.4 mm walls — watertight, but not fillable."""
    outer = trimesh.creation.box(extents=[40, 40, 40])
    inner = trimesh.creation.box(extents=[39.2, 39.2, 39.2])
    inner.invert()
    return trimesh.util.concatenate([outer, inner])


def test_solid_box_reports_no_warnings():
    report = solidity_report(trimesh.creation.box(extents=[20, 20, 20]))
    assert report["is_solid"] is True
    assert report["warnings"] == []
    assert report["avg_wall_mm"] > 3


def test_open_surface_is_reported_as_not_solid():
    report = solidity_report(_open_surface())
    assert report["is_solid"] is False
    assert report["boundary_edges"] > 0
    assert len(report["warnings"]) == 1
    assert "not a closed solid" in report["warnings"][0]


def test_thin_shell_is_reported():
    report = solidity_report(_hollow_shell())
    assert report["avg_wall_mm"] < 1.0
    assert any("hollow" in w for w in report["warnings"])


def test_small_thin_part_is_not_flagged():
    """A 5 mm part is legitimately thin; only real models get judged."""
    small = trimesh.creation.box(extents=[5, 5, 0.6])
    assert solidity_report(small)["warnings"] == []


def test_export_carries_the_warning(tmp_path):
    out = str(tmp_path / "surface.stl")
    res = export_to_format(_open_surface(), out, "stl")
    assert os.path.exists(out)
    assert res["is_solid"] is False
    assert res["warnings"] and "not a closed solid" in res["warnings"][0]


def test_inside_out_mesh_is_turned_the_right_way_out(tmp_path):
    """An inverted mesh has negative volume; a slicer reads it as a cavity."""
    box = trimesh.creation.box(extents=[10, 10, 10])
    box.invert()
    assert box.volume < 0

    fixed = prepare_for_export(box, "mm", True)
    assert fixed.volume > 0

    out = str(tmp_path / "fixed.stl")
    res = export_to_format(box, out, "stl")
    assert res["is_solid"] is True
    assert res["warnings"] == []
    assert trimesh.load(out).volume > 0


def test_units_and_grounding_still_apply(tmp_path):
    out = str(tmp_path / "scaled.stl")
    res = export_to_format(trimesh.creation.box(extents=[1, 2, 3]), out, "stl", scale_unit="in")
    assert res["dimensions_mm"] == [25.4, 50.8, 76.2]
    assert res["bounds_min"][2] == 0.0
    assert np.isclose(res["volume_cm3"], 25.4 * 50.8 * 76.2 / 1000.0, rtol=1e-3)
