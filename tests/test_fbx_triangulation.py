"""
FBX polygons are not necessarily triangles.

The loader used to reshape the index buffer straight to (-1, 3), which raises on the
first quad. It now fan-triangulates, which is what an exporter would have done. These
tests check that against a reference implementation, because the vectorised version
is not obviously correct by reading, and no FBX fixture ships with the repo.
"""
import time

import numpy as np
import pytest

from engine.model_loader import _fan_triangulate


class FakeFace:
    def __init__(self, index_begin, num_indices):
        self.index_begin = index_begin
        self.num_indices = num_indices


class FakeMesh:
    """Just enough of a ufbx mesh for the triangulator."""

    def __init__(self, polygon_sizes):
        self.faces = []
        begin = 0
        for size in polygon_sizes:
            self.faces.append(FakeFace(begin, size))
            begin += size
        self.num_faces = len(self.faces)
        self.num_indices = begin


def _reference(polygon_sizes):
    """The obvious loop, kept as the thing the fast version must agree with."""
    out, begin = [], 0
    for size in polygon_sizes:
        for j in range(size - 2):
            out.extend([begin, begin + j + 1, begin + j + 2])
        begin += size
    return np.array(out, dtype=np.int64)


@pytest.mark.parametrize("sizes,name", [
    ([3, 3, 3], "triangles"),
    ([4, 4, 4, 4], "quads"),
    ([5], "pentagon"),
    ([8], "octagon"),
    ([3, 4, 5, 6, 3], "mixed"),
    ([4, 3, 7, 3, 4], "mixed, unsorted"),
])
def test_matches_the_reference_fan(sizes, name):
    assert np.array_equal(_fan_triangulate(FakeMesh(sizes)), _reference(sizes)), name


def test_triangle_count_is_n_minus_two_per_polygon():
    sizes = [3, 4, 5, 9]
    corners = _fan_triangulate(FakeMesh(sizes))
    assert len(corners) == sum(s - 2 for s in sizes) * 3


def test_every_triangle_stays_inside_its_own_polygon():
    """A fan must never reference a corner belonging to a different face."""
    sizes = [4, 6, 3, 5]
    corners = _fan_triangulate(FakeMesh(sizes)).reshape(-1, 3)

    bounds, begin = [], 0
    for size in sizes:
        bounds.append((begin, begin + size))
        begin += size

    for tri in corners:
        owner = [(lo, hi) for lo, hi in bounds if lo <= tri[0] < hi]
        assert len(owner) == 1
        lo, hi = owner[0]
        assert np.all((tri >= lo) & (tri < hi)), f"{tri} crosses a polygon boundary"


def test_degenerate_polygons_are_dropped_not_crashed_on():
    """Points and lines appear in real FBX files and carry no surface."""
    assert len(_fan_triangulate(FakeMesh([1, 2]))) == 0
    assert np.array_equal(_fan_triangulate(FakeMesh([1, 4, 2])), _reference([4]) + 1)


def test_empty_mesh():
    assert len(_fan_triangulate(FakeMesh([]))) == 0


def test_large_quad_mesh_is_not_triangulated_one_polygon_at_a_time():
    """
    The first version of this looped in Python over every polygon and every corner.
    A quarter of a million quads is an ordinary game asset; it must not take seconds.
    """
    sizes = [4] * 250_000
    mesh = FakeMesh(sizes)

    start = time.perf_counter()
    corners = _fan_triangulate(mesh)
    elapsed = time.perf_counter() - start

    assert len(corners) == 250_000 * 2 * 3
    assert elapsed < 1.0, f"took {elapsed:.2f}s for 250,000 quads"
