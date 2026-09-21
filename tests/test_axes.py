"""
Which way up a model arrives.

Meshwright works in Z-up. glTF and GLB mandate Y-up and FBX declares its own, so a
model from any of them used to arrive a quarter turn onto its back and stay that
way through analysis, repair and export: a standing figure was lying down, its
height was reported as its depth, and "rest on build plate" stood it on its
shoulder.

These tests pin the three things that have to stay true together: a file that says
which way is up is stood up, a file that says nothing is never guessed at, and a
model written back out comes back identical.
"""
import numpy as np
import pytest
import trimesh

from engine import axes, quicklook
from engine.mesh_exporter import export_to_format
from engine.model_loader import load_model


def tall_in(mesh_or_extents) -> str:
    """Which axis the model is tallest along."""
    extents = getattr(mesh_or_extents, "extents", mesh_or_extents)
    return "xyz"[int(np.argmax(np.asarray(extents))) ]


@pytest.fixture
def standing():
    """A figure twice as tall as it is wide, so 'which way is up' is unambiguous."""
    return trimesh.creation.box(extents=(20, 10, 60))


def y_up_copy(mesh):
    """The same model as a Y-up file would hold it: tallest along Y."""
    turned = mesh.copy()
    turned.apply_transform(trimesh.transformations.rotation_matrix(-np.pi / 2, [1, 0, 0]))
    return turned


# ------------------------------------------------------------------ standing models up
@pytest.mark.parametrize("ext", ["glb", "gltf"])
def test_a_gltf_model_arrives_standing_up(standing, tmp_path, ext):
    """glTF mandates Y-up in its specification, so this needs no guessing."""
    path = str(tmp_path / f"figure.{ext}")
    y_up_copy(standing).export(path)
    loaded, _ = load_model(path, with_stats=False)
    assert tall_in(loaded) == "z"
    assert sorted(np.round(loaded.extents, 3)) == pytest.approx([10, 20, 60], abs=0.01)


def test_the_model_is_only_turned_not_altered(standing, tmp_path):
    path = str(tmp_path / "figure.glb")
    y_up_copy(standing).export(path)
    loaded, _ = load_model(path, with_stats=False)
    assert len(loaded.faces) == len(standing.faces)
    assert loaded.volume == pytest.approx(standing.volume, rel=1e-6)
    assert sorted(np.round(loaded.extents, 6)) == pytest.approx(sorted(np.round(standing.extents, 6)))


@pytest.mark.parametrize("ext", ["stl", "ply", "off", "3mf", "obj"])
def test_a_format_that_says_nothing_is_left_exactly_as_it_was(standing, tmp_path, ext):
    """
    Guessing would stand some of these up and lay others down with no way to tell
    which. STL and 3MF are printing formats and are already Z-up; OBJ, PLY and OFF
    record nothing at all, so what the author saved is what arrives.
    """
    path = str(tmp_path / f"figure.{ext}")
    laid_down = y_up_copy(standing)
    laid_down.export(path)
    loaded, _ = load_model(path, with_stats=False)
    assert tall_in(loaded) == "y", f"{ext} must not be rotated by Meshwright"


def test_which_way_is_up_is_decided_only_by_what_the_file_says():
    assert axes.needs_standing_up(".glb") is True
    assert axes.needs_standing_up(".gltf") is True
    assert axes.needs_standing_up(".fbx", declared="y") is True
    assert axes.needs_standing_up(".fbx", declared="z") is False
    assert axes.needs_standing_up(".fbx", declared=None) is False
    for quiet in (".obj", ".stl", ".ply", ".off", ".3mf", ".3ds", ".dae"):
        assert axes.needs_standing_up(quiet) is False, quiet


# ------------------------------------------------------------------ and back out again
@pytest.mark.parametrize("ext", ["glb", "gltf"])
def test_a_model_written_out_and_opened_again_is_the_same_way_up(standing, tmp_path, ext):
    """
    Exporting must undo what importing did. If it did not, every round trip through
    a GLB would turn the model another quarter turn.
    """
    first = str(tmp_path / f"in.{ext}")
    y_up_copy(standing).export(first)
    loaded, _ = load_model(first, with_stats=False)

    again = loaded
    for cycle in range(3):
        out = str(tmp_path / f"cycle{cycle}.{ext}")
        export_to_format(again, out, ext, align_origin=False)
        again, _ = load_model(out, with_stats=False)
        assert tall_in(again) == "z", f"turned over on round trip {cycle + 1}"
        assert sorted(np.round(again.extents, 3)) == pytest.approx([10, 20, 60], abs=0.01)


def test_an_exported_gltf_file_really_holds_y_up_so_other_programs_agree(standing, tmp_path):
    """The file itself must satisfy the specification, not merely survive our own round trip."""
    out = str(tmp_path / "out.glb")
    export_to_format(standing, out, "glb", align_origin=False)
    raw = trimesh.load(out, force="mesh", process=False)
    assert tall_in(raw) == "y", "a GLB written by Meshwright must be Y-up, as the format requires"


def test_the_size_reported_after_export_is_the_one_on_screen(standing, tmp_path):
    """The numbers in the export report describe the model, not how the file stores it."""
    out = str(tmp_path / "out.glb")
    result = export_to_format(standing, out, "glb", align_origin=False)
    assert result["dimensions_mm"] == pytest.approx([20, 10, 60], abs=0.01)


def test_exporting_for_printing_is_untouched(standing, tmp_path):
    """STL is Z-up already; the fix must not reach it."""
    out = str(tmp_path / "out.stl")
    export_to_format(standing, out, "stl", align_origin=False)
    assert tall_in(trimesh.load(out, force="mesh", process=False)) == "z"


# ------------------------------------------------------------------ the browser agrees
@pytest.mark.parametrize("ext", ["glb", "stl", "obj"])
def test_the_preview_is_drawn_the_way_the_model_will_open(standing, tmp_path, monkeypatch, ext):
    """
    A preview is a promise about what opening the file will show. Whatever the rule
    is, both sides must follow the same one — so the picture's own idea of up is
    compared against what the loader actually does.
    """
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    path = str(tmp_path / f"figure.{ext}")
    y_up_copy(standing).export(path)
    loaded, _ = load_model(path, with_stats=False)

    turned_by_preview = f".{ext}" in quicklook.Y_UP_FORMATS
    turned_by_loader = tall_in(loaded) == "z"
    assert turned_by_preview == turned_by_loader, (
        f"the browser and the viewport disagree about which way up a {ext} is")
