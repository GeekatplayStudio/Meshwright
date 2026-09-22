"""
Doing something to some of the pieces, and nothing to the rest.

A model made of separate pieces usually needs different treatment in different
places: the figure kept and the base thinned, two halves fused into one solid, an
arm moved clear of the body before it is printed. What these tests care about is
that each operation touches only what was chosen — the commonest way for an
operation like this to be wrong is to quietly reshape everything.
"""
import numpy as np
import pytest
import trimesh

from engine.service import MeshService, ServiceError
from engine.validation import ValidationError


def build(offsets, tmp_path, size=10.0):
    """One STL holding a box at each x offset — one piece per box."""
    parts = []
    for x in offsets:
        box = trimesh.creation.box(extents=(size, size, size))
        box.apply_translation([x, 0, 0])
        parts.append(box)
    path = tmp_path / "pieces.stl"
    trimesh.util.concatenate(parts).export(str(path))
    return str(path)


@pytest.fixture
def three(tmp_path):
    svc = MeshService(autosave=False)
    svc.load(build((0, 30, 60), tmp_path))
    assert len(svc.shells) == 3
    return svc


# ------------------------------------------------------------------ moving
def test_moving_a_piece_moves_only_that_piece(three):
    before = [s.bounds.mean(axis=0).copy() for s in three.shells]
    three.move_pieces([1], [0, 0, 25])
    after = [s.bounds.mean(axis=0) for s in three.shells]
    moved = [i for i, (a, b) in enumerate(zip(before, after)) if not np.allclose(a, b, atol=1e-6)]
    assert moved == [1], f"pieces {moved} moved; only piece 1 was asked to"
    assert after[1][2] - before[1][2] == pytest.approx(25, abs=0.01)


def test_moving_changes_no_geometry_only_position(three):
    faces, volume = len(three.mesh.faces), three.mesh.volume
    three.move_pieces([0], [5, 5, 5])
    assert len(three.mesh.faces) == faces
    assert three.mesh.volume == pytest.approx(volume, rel=1e-9)


def test_moving_keeps_the_texture_coordinates_it_had(tmp_path):
    """
    A translation changes no topology, so the UV channel belongs to the moved mesh
    exactly as it stands. Re-projecting it instead would sample the texture from
    wherever the piece has just been dragged to.
    """
    svc = MeshService(autosave=False)
    svc.load(build((0, 30), tmp_path))
    faces = len(svc.mesh.faces)
    svc.current.uv = np.tile(np.array([[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]]), (faces, 1, 1))
    original = svc.corner_uv.copy()
    svc.move_pieces([0], [1, 2, 3])
    assert svc.corner_uv is not None
    assert np.array_equal(svc.corner_uv, original)


def test_a_move_of_nothing_is_not_a_new_state(three):
    before = three.current.id
    result = three.move_pieces([0], [0, 0, 0])
    assert result.get("unchanged") and three.current.id == before


# ------------------------------------------------------------------ merging
def test_overlapping_pieces_become_one_solid(tmp_path):
    svc = MeshService(autosave=False)
    svc.load(build((0, 6), tmp_path))            # 10 mm boxes 6 mm apart overlap
    result = svc.merge_pieces([0, 1])
    assert result["success"]
    entry = svc.history[-1]
    assert entry["bodies_after"] == 1
    assert result["stats"]["is_watertight"]


def test_pieces_that_do_not_touch_are_not_pretended_to_be_joined(tmp_path):
    """No amount of arithmetic fuses two boxes with a gap between them."""
    svc = MeshService(autosave=False)
    svc.load(build((0, 30), tmp_path))
    svc.merge_pieces([0, 1])
    assert svc.history[-1]["bodies_after"] == 2


def test_merging_needs_two_pieces(three):
    with pytest.raises(ServiceError, match="at least two"):
        three.merge_pieces([1])


def test_merging_leaves_the_pieces_that_were_not_chosen_alone(tmp_path):
    svc = MeshService(autosave=False)
    svc.load(build((0, 6, 60), tmp_path))
    far = svc.shells[2].bounds.mean(axis=0).copy()
    svc.merge_pieces([0, 1])
    assert len(svc.shells) == 2, "the untouched piece should still be its own piece"
    kept = [s for s in svc.shells if np.allclose(s.bounds.mean(axis=0), far, atol=1e-6)]
    assert kept, "the piece that was not merged has moved or gone"


# ------------------------------------------------------------------ reducing
def test_reducing_one_piece_leaves_the_others_at_full_detail(tmp_path):
    svc = MeshService(autosave=False)
    dense = trimesh.creation.icosphere(subdivisions=3, radius=5)
    plain = trimesh.creation.box(extents=(10, 10, 10))
    plain.apply_translation([40, 0, 0])
    path = tmp_path / "mix.stl"
    trimesh.util.concatenate([dense, plain]).export(str(path))
    svc.load(str(path))
    counts = sorted(len(s.faces) for s in svc.shells)
    result = svc.optimize_pieces([int(np.argmax([len(s.faces) for s in svc.shells]))], 0.25)
    assert result["info"]["faces_after"] < result["info"]["faces_before"]
    assert min(len(s.faces) for s in svc.shells) == counts[0], "the simple piece was reduced too"


# ------------------------------------------------------------------ isolating
def test_isolating_keeps_only_what_was_chosen(three):
    three.isolate_pieces([0, 2])
    assert len(three.shells) == 2


def test_isolating_everything_changes_nothing(three):
    before = three.current.id
    result = three.isolate_pieces([0, 1, 2])
    assert result.get("unchanged") and three.current.id == before


# ------------------------------------------------------------------ refusals
@pytest.mark.parametrize("call", ["move_pieces", "merge_pieces", "optimize_pieces", "isolate_pieces"])
def test_nothing_selected_is_refused_in_words(three, call):
    work = getattr(three, call)
    with pytest.raises(ServiceError, match="No pieces were selected"):
        work([], [1, 0, 0]) if call == "move_pieces" else work([])


@pytest.mark.parametrize("call", ["move_pieces", "merge_pieces", "optimize_pieces", "isolate_pieces"])
def test_a_piece_that_does_not_exist_is_refused(three, call):
    work = getattr(three, call)
    with pytest.raises(ValidationError):
        work([99], [1, 0, 0]) if call == "move_pieces" else work([99])


def test_a_single_piece_model_says_so_rather_than_failing_oddly(tmp_path):
    svc = MeshService(autosave=False)
    svc.load(build((0,), tmp_path))
    with pytest.raises(ServiceError, match="single piece"):
        svc.move_pieces([0], [1, 0, 0])


def test_every_piece_operation_can_be_undone(three):
    start = len(three.mesh.faces)
    three.isolate_pieces([0])
    assert len(three.mesh.faces) < start
    three.undo()
    assert len(three.mesh.faces) == start
    assert len(three.shells) == 3
