"""
The UV unfold, checked by rasterising the atlas rather than by trusting a summary.

engine.texture.uv_patches.validate() screens layouts with total UV area, which is
cheap and catches the failure that actually happens — xatlas piling a whole model
into one chart. It cannot catch a small island sitting on top of a big one, because
the areas still sum to less than the sheet. These tests draw the triangles into a
grid and count texels claimed twice, which can.

The other check here is a round trip: bake each texel's 3D position into the atlas
through the UVs, then read it back near each corner and see whether it comes home.
Reading it back at face *centres* would prove less than it appears to — the centroid
of three corners is the same whatever order they are in, so it cannot see a face
whose corners have been permuted. These sample towards each corner in turn.
"""
import numpy as np
import pytest
import trimesh

from engine.texture.uv_channel import transfer
from engine.texture.uv_patches import validate
from engine.texture.uv_unwrapper import HAS_XATLAS, unwrap_corner_uv

pytestmark = pytest.mark.skipif(not HAS_XATLAS, reason="xatlas is not installed")

RASTER = 384          # fine enough to see a real overlap, coarse enough to stay quick


def rasterise(corner_uv, payload=None, size=RASTER):
    """
    How many triangles claim each texel, and what they wrote there.

    Pixel centres are tested strictly inside the triangle, so two triangles sharing
    an edge never both claim the same texel: an overlap in the result is a real
    overlap and not a shared border.
    """
    count = np.zeros((size, size), dtype=np.int32)
    baked = np.zeros((size, size, 3)) if payload is not None else None
    uv = np.asarray(corner_uv, dtype=np.float64) * size

    for f, tri in enumerate(uv):
        x0, x1 = max(0, int(np.floor(tri[:, 0].min()))), min(size - 1, int(np.ceil(tri[:, 0].max())))
        y0, y1 = max(0, int(np.floor(tri[:, 1].min()))), min(size - 1, int(np.ceil(tri[:, 1].max())))
        if x1 < x0 or y1 < y0:
            continue
        px, py = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)

        (ax, ay), (bx, by), (cx, cy) = tri
        area = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
        if abs(area) < 1e-12:
            continue
        w0 = ((bx - ax) * (py - ay) - (by - ay) * (px - ax)) / area
        w1 = ((cx - bx) * (py - by) - (cy - by) * (px - bx)) / area
        w2 = ((ax - cx) * (py - cy) - (ay - cy) * (px - cx)) / area
        inside = (w0 > 0) & (w1 > 0) & (w2 > 0)
        if not inside.any():
            continue

        count[y0:y1 + 1, x0:x1 + 1] += inside
        if payload is not None:
            block = baked[y0:y1 + 1, x0:x1 + 1]
            block[inside] = np.stack([w1, w2, w0], axis=-1)[inside] @ payload[f]
    return count, baked


def read_back(baked, points, size=RASTER):
    ij = np.clip((points * size).astype(int), 0, size - 1)
    return baked[ij[:, 1], ij[:, 0]]


@pytest.fixture(scope="module")
def sphere():
    """A closed manifold — the shape that used to crash xatlas outright."""
    return trimesh.creation.icosphere(subdivisions=3)


@pytest.fixture(scope="module")
def unwrapped(sphere):
    uv, stats = unwrap_corner_uv(sphere)
    return uv, stats


# ------------------------------------------------------------------ the layout
def test_the_geometry_is_not_touched(sphere):
    """
    Unwrapping produces coordinates beside the mesh, not a new mesh. A watertight
    model has to stay watertight and keep its readiness score, which is the whole
    reason UVs are held per face corner instead of inside the geometry.
    """
    before = sphere.vertices.copy()
    unwrap_corner_uv(sphere)
    assert np.array_equal(before, sphere.vertices)
    assert sphere.is_watertight


def test_every_face_gets_a_usable_triangle(sphere, unwrapped):
    uv, _ = unwrapped
    assert uv.shape == (len(sphere.faces), 3, 2)
    assert np.isfinite(uv).all()
    assert uv.min() >= -1e-4 and uv.max() <= 1 + 1e-4, "the layout leaves the sheet"

    d1, d2 = uv[:, 1] - uv[:, 0], uv[:, 2] - uv[:, 0]
    area = np.abs(d1[:, 0] * d2[:, 1] - d1[:, 1] * d2[:, 0])
    assert (area > 1e-12).all(), f"{int((area <= 1e-12).sum())} faces hold no texture at all"


def test_the_islands_do_not_sit_on_top_of_each_other(unwrapped):
    """
    The check validate() cannot make. Overlapping islands smear two parts of the
    model with the same texels, and the total-area screen lets it through whenever
    the areas still fit inside the sheet.
    """
    uv, _ = unwrapped
    count, _ = rasterise(uv)
    used = int((count >= 1).sum())
    doubled = int((count > 1).sum())
    assert used > 0, "nothing was drawn into the atlas"
    assert 100 * doubled / used < 0.5, (
        f"{doubled:,} of {used:,} used texels are claimed by more than one face "
        f"({100 * doubled / used:.2f}%)")


def test_a_texel_leads_back_to_the_place_it_came_from(sphere, unwrapped):
    """
    Bake position into the atlas through the UVs, read it back towards each corner.
    Catches a face mapped to the wrong island, and a face whose corners were shuffled.
    """
    uv, _ = unwrapped
    corners = sphere.vertices[sphere.faces]
    count, baked = rasterise(uv, payload=corners)

    # 0.8 of the way out to each corner: far enough that a swapped pair gives a
    # different answer, still inside the triangle so it lands on a texel it owns.
    probe_uv = np.concatenate([uv[:, k] * 0.8 + uv.mean(axis=1) * 0.2 for k in range(3)])
    probe_3d = np.concatenate([corners[:, k] * 0.8 + corners.mean(axis=1) * 0.2
                               for k in range(3)])

    landed = read_back(count[:, :, None], probe_uv)[:, 0] > 0
    assert landed.mean() > 0.8, f"only {100 * landed.mean():.0f}% of probes hit a texel"

    err = np.linalg.norm(read_back(baked, probe_uv) - probe_3d, axis=1)
    drift = np.percentile(err[landed], 99) / np.linalg.norm(sphere.extents) * 100
    assert drift < 2.0, f"a texel leads {drift:.2f}% of the model away from its own surface"


def test_the_engine_agrees_the_layout_is_usable(unwrapped):
    uv, stats = unwrapped
    assert validate(uv) is None
    assert stats["islands"] >= 1 and stats["seam_edges"] > 0
    assert stats["has_uv"] is True


# ------------------------------------------------- carrying UVs through an edit
def test_reduction_keeps_the_uvs_on_the_sheet():
    """
    A corner rebuilt from a neighbouring triangle extends that triangle's plane, so
    it can land just past the edge of the atlas. The viewer samples with
    RepeatWrapping, which turns a hair past the edge into a colour from the opposite
    side of the sheet — so the transfer holds its answer inside the range the source
    actually used.
    """
    source = trimesh.creation.icosphere(subdivisions=4)
    source_uv, _ = unwrap_corner_uv(source)
    assert source_uv.min() >= 0 and source_uv.max() <= 1

    target = source.simplify_quadric_decimation(face_count=len(source.faces) // 8)
    moved = transfer(source, source_uv, target)

    assert moved is not None and len(moved) == len(target.faces)
    assert moved.min() >= source_uv.min() - 1e-6, f"UVs fell off the sheet at {moved.min():.4f}"
    assert moved.max() <= source_uv.max() + 1e-6, f"UVs ran past the sheet at {moved.max():.4f}"


def test_a_tiled_source_is_not_squashed_into_the_unit_square():
    """
    Holding the transfer inside the source's range must mean *its* range, not 0..1 —
    a layout that deliberately tiles past 1 has to survive an edit intact.
    """
    source = trimesh.creation.icosphere(subdivisions=3)
    base, _ = unwrap_corner_uv(source)
    tiled = base * 3.0                                   # three repeats across

    target = source.simplify_quadric_decimation(face_count=len(source.faces) // 4)
    moved = transfer(source, tiled, target)

    assert moved is not None
    assert moved.max() > 1.5, f"the tiling was clamped away — max is {moved.max():.3f}"
    assert moved.max() <= tiled.max() + 1e-6
