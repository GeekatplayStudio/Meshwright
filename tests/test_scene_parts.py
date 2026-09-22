"""
Keeping a file's objects apart until someone says otherwise.

Meshwright welds every object in a file into one mesh, which is right for a figure
saved on its own and wrong for a Blender scene lit for rendering: that arrives with
its studio floor and reflection cards fused to the model and no way to separate them
again. These tests cover the two halves of the fix — listing what is in a file, and
opening only part of it — and the rule that matters most: a chosen set that cannot
be honoured must fail loudly rather than quietly hand back the whole scene.
"""
import numpy as np
import pytest
import trimesh

from engine import scene_parts
from engine.model_loader import load_model


@pytest.fixture
def studio(tmp_path):
    """A figure with a ground plane under it and a wall behind — a lit scene."""
    scene = trimesh.Scene()
    scene.add_geometry(trimesh.creation.box(extents=(2, 2, 10)),
                       node_name="Figure", geom_name="Figure")
    scene.add_geometry(trimesh.creation.box(extents=(60, 60, 0.5)),
                       node_name="Studio ground", geom_name="Studio ground",
                       transform=trimesh.transformations.translation_matrix([0, 0, -5]))
    scene.add_geometry(trimesh.creation.box(extents=(60, 0.5, 20)),
                       node_name="Reflection card", geom_name="Reflection card",
                       transform=trimesh.transformations.translation_matrix([0, 30, 5]))
    path = tmp_path / "scene.glb"
    scene.export(str(path))
    return str(path)


# ------------------------------------------------------------------ what is in the file
def test_every_object_is_listed_with_its_own_name_and_size(studio):
    found = scene_parts.list_parts(studio)
    assert found["multi"] is True
    named = {p["name"]: p for p in found["parts"]}
    assert set(named) == {"Figure", "Studio ground", "Reflection card"}
    assert named["Figure"]["faces"] == 12
    assert sorted(named["Studio ground"]["size"]) == pytest.approx([0.5, 60, 60], abs=0.01)


def test_a_file_that_can_only_hold_one_object_is_not_worth_asking_about(tmp_path):
    path = tmp_path / "solid.stl"
    trimesh.creation.box(extents=(10, 10, 10)).export(str(path))
    assert scene_parts.list_parts(str(path))["multi"] is False


def test_a_single_object_file_is_not_worth_asking_about_either(tmp_path):
    path = tmp_path / "one.glb"
    trimesh.creation.box(extents=(10, 10, 10)).export(str(path))
    assert scene_parts.list_parts(str(path))["multi"] is False


def test_a_glb_is_listed_without_reading_its_geometry(studio, monkeypatch):
    """
    The header carries the names and the counts, so a 426 MB file can be listed
    without touching the 426 MB. Loading it as a scene would defeat the point.
    """
    def refuse(*args, **kwargs):
        raise AssertionError("the geometry was loaded just to list the objects")
    monkeypatch.setattr(trimesh, "load", refuse)
    assert len(scene_parts.list_parts(studio)["parts"]) == 3


def test_a_file_that_cannot_be_inspected_still_opens_whole(tmp_path):
    """Not being able to list the parts is not a reason to refuse the file."""
    path = tmp_path / "broken.glb"
    path.write_bytes(b"this is not a GLB at all")
    found = scene_parts.list_parts(str(path))
    assert found["multi"] is False and len(found["parts"]) == 1


# ------------------------------------------------------------------ what gets suggested
def test_an_object_the_file_marks_as_not_for_render_starts_unticked():
    """A rig's controller widgets say so in the file itself; those we can act on."""
    finished = scene_parts._finish([
        {"name": "Figure", "faces": 1200, "hidden": False},
        {"name": "WGT-hand", "faces": 0, "hidden": True},
        {"name": "Studio ground", "faces": 1, "hidden": False},
    ])
    keep = {p["name"]: p["keep"] for p in finished["parts"]}
    assert keep == {"Figure": True, "WGT-hand": False, "Studio ground": True}, (
        "a visible ground plane must not be guessed away — only the person knows")


# ------------------------------------------------------------------ opening part of it
def test_opening_one_object_leaves_the_rest_in_the_file(studio):
    only, _ = load_model(studio, with_stats=False, keep=["Figure"])
    assert len(only.faces) == 12
    assert sorted(np.round(only.extents, 2)) == pytest.approx([2, 2, 10], abs=0.05)


def test_a_kept_object_stays_where_the_file_put_it(studio):
    """
    Reading geometries without their node transforms piles them all onto the
    origin — the wall ends up inside the figure.
    """
    pair, _ = load_model(studio, with_stats=False, keep=["Figure", "Reflection card"])
    assert len(pair.faces) == 24
    assert max(np.round(pair.extents, 1)) >= 30, "the card collapsed onto the figure"


def test_opening_everything_is_what_it_always_was(studio):
    whole, _ = load_model(studio, with_stats=False)
    chosen, _ = load_model(studio, with_stats=False,
                           keep=["Figure", "Studio ground", "Reflection card"])
    assert len(whole.faces) == len(chosen.faces)
    assert sorted(np.round(whole.extents, 2)) == pytest.approx(sorted(np.round(chosen.extents, 2)))


def test_a_choice_that_cannot_be_honoured_fails_instead_of_loading_everything(studio):
    """
    The important one. Falling through to a loader that cannot filter would hand
    back the whole scene — the studio floor and all — which is the exact thing the
    choosing was for, and the person would never know.
    """
    with pytest.raises(Exception) as trouble:
        load_model(studio, with_stats=False, keep=["No Such Object"])
    assert "chosen" in str(trouble.value).lower()


def test_the_source_file_is_never_written_to(studio):
    before = open(studio, "rb").read()
    load_model(studio, with_stats=False, keep=["Figure"])
    assert open(studio, "rb").read() == before
