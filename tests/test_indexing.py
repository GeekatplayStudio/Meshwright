"""
Packing integer rows into single keys has to give exactly the answer the row-wise
sort would have given — it is used to decide which edges are open, which faces are
duplicates and which corners can be welded, and a wrong answer there is a wrong
diagnosis. So every case is checked against numpy's own implementation, including
the ones where the values will not fit and the original path has to take over.
"""
import numpy as np
import pytest
import trimesh
from trimesh.grouping import group_rows

from engine.indexing import duplicate_mask, pack_rows, quantise, unique_rows


def _reference(rows):
    """What numpy.unique(axis=0) says, as (first_index, inverse, counts)."""
    _, first, inverse, counts = np.unique(
        np.asarray(rows), axis=0, return_index=True, return_inverse=True, return_counts=True)
    return first, inverse.reshape(-1), counts


def _same_grouping(rows):
    """
    Equality classes have to match. Group *order* need not, since the caller only
    ever asks which rows are equal, so compare the partitions rather than the ids.
    """
    ref_first, ref_inv, ref_counts = _reference(rows)
    first, inv, counts = unique_rows(rows)

    assert sorted(counts.tolist()) == sorted(ref_counts.tolist())
    assert sorted(first.tolist()) == sorted(ref_first.tolist())
    # Two rows are in the same class under one iff they are under the other.
    n = len(rows)
    sample = np.arange(n) if n <= 200 else np.random.default_rng(0).choice(n, 200, replace=False)
    a = inv[sample][:, None] == inv[sample][None, :]
    b = ref_inv[sample][:, None] == ref_inv[sample][None, :]
    assert np.array_equal(a, b)


# ------------------------------------------------------------------ packing
def test_packs_small_columns_into_one_key():
    rows = np.array([[0, 0], [0, 1], [1, 0], [0, 1]], dtype=np.int64)
    key = pack_rows(rows)
    assert key is not None and key.dtype == np.uint64
    assert key[1] == key[3] and key[0] != key[1]


def test_packing_uses_the_range_not_the_magnitude():
    """Large but tightly clustered values still fit — columns are shifted down."""
    rows = np.full((4, 3), 2 ** 40, dtype=np.int64)
    rows[1, 0] += 1
    assert pack_rows(rows) is not None


def test_refuses_rows_that_will_not_fit():
    wide = np.array([[0, 0, 0], [2 ** 40, 2 ** 40, 2 ** 40]], dtype=np.int64)
    assert pack_rows(wide) is None


def test_refuses_non_integer_rows():
    assert pack_rows(np.array([[0.5, 1.5]])) is None


def test_empty_input():
    first, inverse, counts = unique_rows(np.zeros((0, 2), dtype=np.int64))
    assert len(first) == len(inverse) == len(counts) == 0
    assert len(duplicate_mask(np.zeros((0, 3), dtype=np.int64))) == 0


# ------------------------------------------------------------------ agreement
@pytest.mark.parametrize("rows,name", [
    (np.array([[1, 2], [1, 2], [3, 4]], dtype=np.int64), "tiny with a duplicate"),
    (np.array([[0, 0]] * 5, dtype=np.int64), "all identical"),
    (np.arange(60, dtype=np.int64).reshape(-1, 3), "all distinct"),
    (np.array([[-5, 3], [-5, 3], [7, -2]], dtype=np.int64), "negative values"),
    (np.zeros((10, 1), dtype=np.int64), "single column"),
])
def test_agrees_with_numpy(rows, name):
    _same_grouping(rows)


def test_agrees_with_numpy_on_random_rows():
    rng = np.random.default_rng(7)
    _same_grouping(rng.integers(0, 12, size=(4000, 3)).astype(np.int64))


def test_falls_back_and_still_agrees_when_values_are_too_wide():
    rng = np.random.default_rng(3)
    wide = rng.integers(0, 2 ** 40, size=(400, 3)).astype(np.int64)
    wide[10] = wide[0]                       # guarantee a duplicate to find
    assert pack_rows(wide) is None, "this fixture is meant to exercise the fallback"
    _same_grouping(wide)


# ------------------------------------------------------------------ the real users
@pytest.mark.parametrize("name", ["icosphere", "box", "torus", "two bodies", "open plane"])
def test_edge_grouping_matches_trimesh(name):
    mesh = {
        "icosphere": lambda: trimesh.creation.icosphere(subdivisions=3, radius=20.0),
        "box": lambda: trimesh.creation.box(extents=[3, 4, 5]),
        "torus": lambda: trimesh.creation.torus(major_radius=10, minor_radius=3,
                                                major_sections=20, minor_sections=10),
        "two bodies": lambda: trimesh.util.concatenate([
            trimesh.creation.box(), trimesh.creation.box().apply_translation([5, 0, 0])]),
        "open plane": lambda: trimesh.Trimesh(
            vertices=[[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]],
            faces=[[0, 1, 2], [0, 2, 3]], process=False),
    }[name]()

    edges = mesh.edges_sorted
    _, inverse, counts = unique_rows(edges)

    expected_open = group_rows(edges, require_count=1)
    assert np.array_equal(np.sort(np.flatnonzero(counts[inverse] == 1)), np.sort(expected_open))

    expected_nonmanifold = sum(1 for g in group_rows(edges) if len(g) > 2)
    assert int((counts > 2).sum()) == expected_nonmanifold


def test_duplicate_mask_matches_numpy():
    faces = np.array([[0, 1, 2], [2, 1, 0], [0, 1, 2], [3, 4, 5]])
    sorted_faces = np.sort(faces, axis=1)
    _, first = np.unique(sorted_faces, axis=0, return_index=True)
    assert int(duplicate_mask(sorted_faces).sum()) == len(faces) - len(first)
    # rows 0, 1 and 2 are the same triangle wound differently; two are duplicates
    assert int(duplicate_mask(sorted_faces).sum()) == 2


# ------------------------------------------------------------------ quantising
def test_quantise_makes_near_equal_coordinates_exactly_equal():
    verts = np.array([[1.0, 2.0, 3.0], [1.0 + 1e-9, 2.0, 3.0], [9.0, 9.0, 9.0]])
    q = quantise(verts, 6)
    assert q is not None
    assert int(duplicate_mask(q).sum()) == 1


def test_quantise_refuses_coordinates_it_cannot_represent():
    assert quantise(np.array([[1e60, 0.0, 0.0]]), 6) is None
    assert quantise(np.array([[np.nan, 0.0, 0.0]]), 6) is None
    assert quantise(np.array([[np.inf, 0.0, 0.0]]), 6) is None
