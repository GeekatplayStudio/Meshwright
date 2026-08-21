import os
import trimesh
from engine.modules.printer_profiles import check_build_volume, PRINTER_PROFILES
from engine.modules.color_printing import extract_color_information, export_to_3mf
from engine.modules.part_separation import separate_disconnected_shells, slice_mesh_plane


def test_printer_profiles():
    cube = trimesh.creation.box(extents=[100, 100, 100])
    res = check_build_volume(cube, "bambu_x1c")
    assert res["fits_in_build_volume"] is True
    assert res["printer_name"] == PRINTER_PROFILES["bambu_x1c"]["name"]

    huge_cube = trimesh.creation.box(extents=[500, 500, 500])
    res_huge = check_build_volume(huge_cube, "bambu_x1c")
    assert res_huge["fits_in_build_volume"] is False
    assert len(res_huge["exceeded_axes"]) > 0


def test_color_printing_and_3mf(tmp_path):
    cube = trimesh.creation.box(extents=[10, 10, 10])
    color_info = extract_color_information(cube)
    assert color_info["color_mode"] in ["vertex", "face", "texture", "none"]

    out_3mf = str(tmp_path / "model.3mf")
    res_3mf = export_to_3mf(cube, out_3mf)
    assert os.path.exists(out_3mf)
    assert res_3mf["format"] == "3MF"


def test_part_separation():
    cube1 = trimesh.creation.box(extents=[10, 10, 10])
    cube2 = trimesh.creation.box(extents=[10, 10, 10])
    cube2.apply_translation([50, 0, 0])

    multi_body = trimesh.util.concatenate([cube1, cube2])
    shells = separate_disconnected_shells(multi_body)

    assert len(shells) == 2


def test_slice_mesh_plane():
    cube = trimesh.creation.box(extents=[20, 20, 20])
    above, below = slice_mesh_plane(cube, plane_origin=(0, 0, 0), plane_normal=(0, 0, 1))

    assert isinstance(above, trimesh.Trimesh)
    assert isinstance(below, trimesh.Trimesh)
