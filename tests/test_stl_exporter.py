import os
import trimesh
from engine.stl_exporter import export_to_stl


def test_export_to_stl_mm(tmp_path):
    cube = trimesh.creation.box(extents=[10, 20, 30])
    out_path = str(tmp_path / "output_mm.stl")

    res = export_to_stl(cube, out_path, scale_unit="mm", align_origin=True)

    assert os.path.exists(out_path)
    assert res["file_size_bytes"] > 0
    assert res["bounds_min"][2] == 0.0
    assert res["dimensions_mm"] == [10.0, 20.0, 30.0]


def test_export_to_stl_cm(tmp_path):
    cube = trimesh.creation.box(extents=[10, 10, 10])
    out_path = str(tmp_path / "output_cm.stl")

    res = export_to_stl(cube, out_path, scale_unit="cm", align_origin=True)

    assert os.path.exists(out_path)
    assert res["dimensions_mm"] == [100.0, 100.0, 100.0]
    assert res["bounds_min"][2] == 0.0


def test_export_to_stl_inch(tmp_path):
    cube = trimesh.creation.box(extents=[1, 1, 1])
    out_path = str(tmp_path / "output_in.stl")

    res = export_to_stl(cube, out_path, scale_unit="in", align_origin=True)

    assert os.path.exists(out_path)
    assert res["dimensions_mm"] == [25.4, 25.4, 25.4]
