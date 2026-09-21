"""
Will it print on that machine?

These tests are built around one rule: **never be confidently wrong**. A number
here sends someone to a six-hour print, so the tests use objects whose true
dimensions are known exactly, and they check the refusals as carefully as the
answers — a model that cannot be measured must say so rather than produce a
plausible figure.
"""
import numpy as np
import pytest
import trimesh

from engine import printability as P
from engine import printers


@pytest.fixture
def cube():
    """A 30 mm cube: every wall is 30 mm, and there is nothing fine about it."""
    return trimesh.creation.box(extents=(30, 30, 30))


@pytest.fixture
def cube_with_blade(cube):
    """The same cube with a 0.6 mm blade standing 10 mm out of one face."""
    blade = trimesh.creation.box(extents=(10, 0.6, 18))
    blade.apply_translation([20, 0, 0])
    return trimesh.boolean.union([cube, blade])


RESIN = "elegoo|mars 4 ultra"            # 18 µm pixel -> 0.036 mm finest feature
FDM = "creality|ender 3 v3"              # 0.4 mm nozzle


# ------------------------------------------------------------------ the catalogue
def test_the_table_covers_both_kinds_of_printer_and_many_makers():
    listed = printers.catalogue()
    kinds = {m["technology"] for m in listed["machines"]}
    assert kinds == {"resin", "fdm"}
    assert len(listed["makers"]) >= 20
    assert len(listed["machines"]) >= 150


def test_a_machine_is_listed_once_however_its_maker_spells_it():
    """A slicer writes "ELEGOO Mars 4 Ultra"; the table says Elegoo / Mars 4 Ultra."""
    assert printers.machine_id({"maker": "ELEGOO", "model": "ELEGOO Mars 4 Ultra"}) == \
           printers.machine_id({"maker": "Elegoo", "model": "Mars 4 Ultra"})
    ids = [m["id"] for m in printers.catalogue()["machines"]]
    assert len(ids) == len(set(ids)), "the same printer is offered twice"


def test_a_resin_printers_finest_feature_comes_from_its_screen():
    mars = printers.profile(RESIN)
    assert mars["technology"] == "resin"
    assert mars["unit_mm"] == pytest.approx(0.018, abs=0.001), "153.4 mm across 8520 pixels"
    assert mars["min_feature_mm"] == pytest.approx(0.036, abs=0.001), "two pixels"


def test_a_filament_printers_finest_feature_is_its_nozzle():
    ender = printers.profile(FDM)
    assert ender["technology"] == "fdm"
    assert ender["min_feature_mm"] == pytest.approx(0.4, abs=0.001)


def test_a_printer_that_is_not_in_the_table_is_not_a_dead_end():
    """Everything can be typed in, so an unlisted machine still works."""
    mine = printers.profile(technology="resin", pixel_um=22.0, layer_mm=0.03)
    assert mine["min_feature_mm"] == pytest.approx(0.044, abs=0.0005)
    assert mine["layer_mm"] == 0.03


# ------------------------------------------------------------------ measuring what is known
def test_a_solid_block_is_reported_as_printable_on_anything(cube):
    for machine in (RESIN, FDM):
        result = P.check(cube, printers.profile(machine), with_scale=False)
        assert result["printable"], machine
        assert result["issues"] == [], machine


def test_the_wall_of_a_30mm_cube_measures_30mm(cube):
    """
    Measured over the faces, not at the corners. Every vertex of a cube is a
    corner, and a ray along a corner's normal runs down the body diagonal: sampling
    vertices would report this cube as 52 mm thick.
    """
    result = P.check(cube, printers.profile(FDM), with_scale=False)
    assert result["wall"]["median_mm"] == pytest.approx(30.0, abs=0.5)
    assert result["confirmed"] is True


def test_a_thin_blade_is_found_and_measured(cube_with_blade):
    result = P.check(cube_with_blade, printers.profile(FDM), with_scale=False)
    assert result["wall"]["thinnest_mm"] == pytest.approx(0.6, abs=0.05)


@pytest.mark.parametrize("nozzle,expect", [(0.2, "clear"), (0.4, "fragile"), (0.8, "missing")])
def test_the_same_blade_is_judged_by_the_machine_it_is_going_to(cube_with_blade, nozzle, expect):
    """
    0.6 mm of blade: a 0.2 mm nozzle makes it, a 0.4 mm nozzle makes it as one
    fragile line, a 0.8 mm nozzle cannot make it at all.
    """
    result = P.check(cube_with_blade, printers.profile(technology="fdm", nozzle_mm=nozzle),
                     with_scale=False)
    found = {issue["id"] for issue in result["issues"]}
    if expect == "clear":
        assert not found
    elif expect == "fragile":
        assert found == {"fragile_detail"}
    else:
        assert "unprintable_detail" in found
        assert not result["printable"]


def test_a_model_fine_for_resin_can_still_be_too_fine_for_filament(cube_with_blade):
    assert P.check(cube_with_blade, printers.profile(RESIN), with_scale=False)["printable"]
    assert not P.check(cube_with_blade, printers.profile(technology="fdm", nozzle_mm=0.8),
                       with_scale=False)["printable"]


def test_every_flagged_place_can_be_shown_on_the_model(cube_with_blade):
    """A fault the viewport cannot point at is not much use."""
    result = P.check(cube_with_blade, printers.profile(technology="fdm", nozzle_mm=0.8),
                     with_scale=False)
    issue = next(i for i in result["issues"] if i["id"] == "unprintable_detail")
    assert issue["location"] and issue["location"]["points"]
    spots = np.asarray(issue["location"]["points"])
    inside = np.logical_and(spots >= cube_with_blade.bounds[0] - 1,
                            spots <= cube_with_blade.bounds[1] + 1).all(axis=1)
    assert inside.all(), "a highlight was placed off the model"


def test_printing_it_bigger_is_offered_and_the_size_offered_really_works(cube_with_blade):
    printer = printers.profile(technology="fdm", nozzle_mm=0.8)
    result = P.check(cube_with_blade, printer)
    scale = result["suggested_scale"]
    assert scale and scale > 1.0
    bigger = cube_with_blade.copy()
    bigger.apply_scale(scale)
    assert P.check(bigger, printer, with_scale=False)["missing_share"] <= 0.001, (
        f"{scale}x was offered but still loses detail")


def test_a_model_far_too_small_for_the_machine_is_never_called_printable():
    """
    The noise floor that hides rastering artefacts must not hide the model itself.
    A 1 mm figure on a 0.4 mm nozzle is two extrusions wide: almost none of it can
    be made, and saying "prints as modelled" would send someone to a wasted print.
    """
    speck = trimesh.creation.icosphere(subdivisions=3, radius=0.5)      # 1 mm across
    result = P.check(speck, printers.profile(FDM), with_scale=False)
    assert not result["printable"], result["verdict"]
    assert any(i["id"] == "unprintable_detail" for i in result["issues"])


def test_the_same_speck_is_fine_on_a_machine_fine_enough_for_it():
    speck = trimesh.creation.icosphere(subdivisions=3, radius=0.5)
    assert P.check(speck, printers.profile(RESIN), with_scale=False)["printable"]


def test_a_model_too_big_for_the_plate_is_told_so_and_by_how_much():
    slab = trimesh.creation.box(extents=(400, 400, 400))
    result = P.check(slab, printers.profile(RESIN), with_scale=False)
    fit = next(i for i in result["issues"] if i["id"] == "too_big")
    assert "153.4" in fit["detail"]
    assert result["fit"]["largest_scale"] < 1.0


# ------------------------------------------------------------------ refusing to be wrong
def test_wall_thickness_is_refused_on_a_mesh_that_cannot_be_trusted(cube):
    """
    A ray needs a surface that faces outwards. On a model with inconsistent winding
    it fires the wrong way and returns the gap to the neighbouring surface: one real
    694,000-face model measured a uniform 0.24 mm wall throughout a solid figure.
    That number must never reach anyone.
    """
    torn = cube.copy()
    torn.faces = np.vstack([torn.faces[:6], torn.faces[6:][:, ::-1]])    # flip half the faces
    assert not torn.is_winding_consistent
    result = P.check(torn, printers.profile(FDM), with_scale=False)
    assert result["wall"] is None
    assert result["confirmed"] is False
    assert any("consistently wound" in d for d in result["doubts"])


def test_an_open_model_still_gets_an_answer_but_an_unconfirmed_one(cube):
    """Slicing needs no normals, so the useful half of the check survives a hole."""
    holed = cube.copy()
    holed.update_faces(np.arange(len(holed.faces)) > 1)
    assert not holed.is_watertight
    result = P.check(holed, printers.profile(FDM), with_scale=False)
    assert result["confirmed"] is False and result["doubts"]
    assert result["layers_read"] > 0, "the measurement that needs no normals should still run"


def test_a_model_that_cannot_be_sliced_at_all_refuses_rather_than_guesses():
    rubbish = trimesh.Trimesh(vertices=np.zeros((3, 3)), faces=np.array([[0, 1, 2]]))
    with pytest.raises(P.NotMeasurable):
        P.check(rubbish, printers.profile(FDM), with_scale=False)


def test_an_empty_workspace_refuses():
    with pytest.raises(P.NotMeasurable):
        P.check(None, printers.profile(FDM))


def test_a_disagreement_between_the_two_measurements_is_reported(cube, monkeypatch):
    """When they do not tell the same story, that is the finding."""
    def pretend_thin(mesh):
        return {"points": np.asarray(mesh.vertices, float),
                "thickness": np.full(len(mesh.vertices), 0.01)}
    monkeypatch.setattr(P, "_wall_thickness", pretend_thin)
    result = P.check(cube, printers.profile(FDM), with_scale=False)
    assert result["confirmed"] is False
    assert any("disagree" in d for d in result["doubts"])


def test_the_check_never_changes_the_model(cube_with_blade):
    """It reports; the person decides whether anything is altered."""
    before = cube_with_blade.vertices.copy(), cube_with_blade.faces.copy()
    P.check(cube_with_blade, printers.profile(technology="fdm", nozzle_mm=0.8))
    assert np.array_equal(cube_with_blade.vertices, before[0])
    assert np.array_equal(cube_with_blade.faces, before[1])
