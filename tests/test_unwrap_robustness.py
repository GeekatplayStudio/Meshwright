"""
The unwrapper must not crash, hang, or hand back a layout that would smear a texture.

xatlas has two measured failure modes — an access violation on closed surfaces
(jpcy/xatlas#146) and silent overlapping output above roughly 40,000 faces — so these
tests pin the behaviour that works around both, and the checks that catch anything
neither of them explains.
"""
import numpy as np
import pytest
import trimesh

from engine.texture import uv_channel as UV
from engine.texture import uv_patches as PATCH
from engine.texture.uv_unwrapper import UnwrapError, unwrap_corner_uv, unwrap_mesh

# 81,920 faces (subdivisions=6) is the size that used to crash; it unwraps in about
# a second now, which is cheap enough to keep in the normal run.
CLOSED_SIZES = [3, 5, 6]


def _spread(mesh, corner_uv):
    return PATCH.texel_spread(np.asarray(mesh.vertices), np.asarray(mesh.faces), corner_uv)


# ------------------------------------------------------------------ splitting
def test_a_closed_mesh_is_never_handed_to_xatlas_whole():
    """
    Every patch must have a boundary. A closed surface is exactly the input that
    leaves xatlas's boundary array unset and its convex-hull pass reading rubbish.
    """
    sphere = trimesh.creation.icosphere(subdivisions=4, radius=50.0)
    verts, faces = np.asarray(sphere.vertices), np.asarray(sphere.faces)
    assert PATCH.is_closed(faces), "the fixture should be a closed surface"

    groups = PATCH.split_into_patches(verts, faces)
    assert len(groups) >= 2
    for group in groups:
        assert not PATCH.is_closed(faces[group]), "a closed patch reached the unwrapper"


def test_splitting_covers_every_face_exactly_once():
    sphere = trimesh.creation.icosphere(subdivisions=4, radius=50.0)
    verts, faces = np.asarray(sphere.vertices), np.asarray(sphere.faces)

    groups = PATCH.split_into_patches(verts, faces, max_faces=500)
    covered = np.concatenate(groups)
    assert len(covered) == len(faces)
    assert np.array_equal(np.sort(covered), np.arange(len(faces)))
    assert all(len(g) <= 500 or len(g) <= PATCH.MIN_PATCH_FACES for g in groups)


def test_an_open_mesh_that_fits_is_left_alone():
    """No extra seams for a model xatlas could have taken whole."""
    side = 20
    axis = np.linspace(0, 100, side)
    x, y = np.meshgrid(axis, axis)
    verts = np.column_stack([x.ravel(), y.ravel(), np.zeros(side * side)])
    grid = np.arange(side * side).reshape(side, side)
    faces = np.array([tri
                      for i in range(side - 1) for j in range(side - 1)
                      for tri in ([grid[i, j], grid[i, j + 1], grid[i + 1, j + 1]],
                                  [grid[i, j], grid[i + 1, j + 1], grid[i + 1, j]])])
    assert not PATCH.is_closed(faces)

    groups = PATCH.split_into_patches(verts, faces)
    assert len(groups) == 1, "an open mesh under the cap should not be cut up"
    assert len(groups[0]) == len(faces)


def test_separate_bodies_stay_separate():
    pair = trimesh.util.concatenate([
        trimesh.creation.icosphere(subdivisions=2, radius=10.0),
        trimesh.creation.icosphere(subdivisions=2, radius=10.0).apply_translation([50, 0, 0])])
    verts, faces = np.asarray(pair.vertices), np.asarray(pair.faces)

    groups = PATCH.split_into_patches(verts, faces)
    # Two closed bodies, each opened into at least two patches.
    assert len(groups) >= 4
    assert sum(len(g) for g in groups) == len(faces)


# ------------------------------------------------------------------ validation
def test_validate_rejects_overlapping_layouts():
    # Every triangle stacked on the whole atlas: coverage far above 1.
    piled = np.tile(np.array([[[0, 0], [1, 0], [0, 1]]], dtype=np.float32), (40, 1, 1))
    assert "overlap" in PATCH.validate(piled)


def test_validate_rejects_nan_and_out_of_range():
    good = np.array([[[0.1, 0.1], [0.2, 0.1], [0.1, 0.2]]], dtype=np.float32)
    assert PATCH.validate(good) is None

    broken = good.copy(); broken[0, 0, 0] = np.nan
    assert "invalid numbers" in PATCH.validate(broken)

    outside = good.copy(); outside[0, 1, 0] = 4.0
    assert "outside the texture area" in PATCH.validate(outside)


def test_texel_spread_is_one_for_an_even_layout():
    # A flat square mapped linearly: every triangle gets the same texel density.
    verts = np.array([[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0]], dtype=float)
    faces = np.array([[0, 1, 2], [0, 2, 3]])
    corner_uv = (verts[faces][:, :, :2] / 10.0).astype(np.float32)
    assert PATCH.texel_spread(verts, faces, corner_uv) == pytest.approx(1.0, abs=1e-6)


# ------------------------------------------------------------------ end to end
@pytest.mark.parametrize("subdivisions", CLOSED_SIZES)
def test_closed_spheres_unwrap_without_crashing(subdivisions):
    """
    subdivisions=6 is 81,920 faces — the exact case that took the whole process down
    with an access violation before patching.
    """
    sphere = trimesh.creation.icosphere(subdivisions=subdivisions, radius=50.0)
    before = (len(sphere.vertices), len(sphere.faces), sphere.is_watertight)

    corner_uv, stats = unwrap_corner_uv(sphere)

    assert PATCH.validate(corner_uv) is None
    assert corner_uv.shape == (len(sphere.faces), 3, 2)
    assert (len(sphere.vertices), len(sphere.faces), sphere.is_watertight) == before
    assert stats["islands"] >= 1 and stats["patches"] >= 2
    assert stats["seam_edges"] == UV.seam_edge_count(sphere, corner_uv)


@pytest.mark.parametrize("shape", ["torus", "cylinder", "box"])
def test_other_closed_shapes_unwrap_evenly(shape):
    mesh = {
        "torus": lambda: trimesh.creation.torus(major_radius=40, minor_radius=12,
                                                major_sections=60, minor_sections=30),
        "cylinder": lambda: trimesh.creation.cylinder(radius=20, height=60, sections=64),
        "box": lambda: trimesh.creation.box(extents=[30, 20, 10]),
    }[shape]()

    corner_uv, _ = unwrap_corner_uv(mesh)
    assert PATCH.validate(corner_uv) is None
    # These are all easy shapes to flatten; anything wild means the split went wrong.
    assert _spread(mesh, corner_uv) < PATCH.GOOD_TEXEL_SPREAD


def test_an_uneven_layout_is_retried_at_a_smaller_patch_size():
    """
    A dense sphere comes back badly stretched at the starting cap, so the unwrapper
    must halve and try again rather than accept it.
    """
    sphere = trimesh.creation.icosphere(subdivisions=5, radius=50.0)

    coarse, coarse_stats = unwrap_corner_uv(sphere, max_patch_faces=40_000)
    fine, fine_stats = unwrap_corner_uv(sphere, max_patch_faces=2_000)

    # Smaller patches are flatter, so they must not be worse.
    assert fine_stats["texel_spread"] <= coarse_stats["texel_spread"] + 0.01
    assert fine_stats["patches"] > coarse_stats["patches"]
    assert PATCH.validate(coarse) is None and PATCH.validate(fine) is None


def test_stats_describe_what_actually_happened():
    sphere = trimesh.creation.icosphere(subdivisions=4, radius=50.0)
    _, stats = unwrap_corner_uv(sphere)
    for key in ("faces", "vertices", "patches", "islands", "seam_edges",
                "atlas_size", "uv_coverage", "texel_spread", "retries", "even"):
        assert key in stats, f"stats is missing {key}"
    assert stats["faces"] == len(sphere.faces)
    assert stats["vertices"] == len(sphere.vertices)
    assert 0.0 < stats["uv_coverage"] <= PATCH.MAX_VALID_UV_AREA


def test_empty_mesh_is_refused_with_a_readable_message():
    empty = trimesh.Trimesh(vertices=np.zeros((0, 3)), faces=np.zeros((0, 3), dtype=int), process=False)
    with pytest.raises(UnwrapError, match="no faces"):
        unwrap_corner_uv(empty)


def test_unwrap_mesh_still_returns_the_split_form():
    """ComfyUI and glTF want per-vertex UVs; that path must keep working."""
    sphere = trimesh.creation.icosphere(subdivisions=3, radius=10.0)
    split, stats = unwrap_mesh(sphere)

    assert split.visual.uv is not None
    assert len(split.visual.uv) == len(split.vertices)
    assert len(split.faces) == len(sphere.faces)
    assert stats["unwrapped_vertices"] >= stats["original_vertices"]
    assert np.allclose(np.asarray(split.vertices)[split.faces],
                       np.asarray(sphere.vertices)[sphere.faces])
