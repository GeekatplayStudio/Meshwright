"""
Measuring a ring, and putting it right.

A wrong number here costs somebody a flask of metal, so the tests are built on rings
whose dimensions are known exactly: a bore built as 16.4 x 17.9 mm has to measure
back as 16.4 x 17.9 mm, and a ring asked for in size ISO 54 has to come back as ISO
54 — read off the corrected ring, not assumed from what was requested.

The Blender half is skipped where Blender is not installed. It is not mocked: the
whole point of that half is that a real modifier stack does something a triangle
mesh cannot, and a stub would prove nothing.
"""
import math

import numpy as np
import pytest
import trimesh

from engine import jewellery as J


def ring(bore_x=16.4, bore_y=17.9, band=6.0, thick=1.6, segments=180, tilt=23.0):
    """A band with a square section and, by default, an oval bore. Tilted."""
    angle = np.linspace(0, 2 * np.pi, segments, endpoint=False)
    inner = np.stack([bore_x / 2 * np.cos(angle), bore_y / 2 * np.sin(angle)], axis=1)
    outer = np.stack([(bore_x / 2 + thick) * np.cos(angle),
                      (bore_y / 2 + thick) * np.sin(angle)], axis=1)
    verts, faces = [], []
    for z in (-band / 2, band / 2):
        for loop in (inner, outer):
            verts.append(np.column_stack([loop, np.full(len(loop), z)]))
    verts = np.vstack(verts)
    n = segments

    def quad(a, b, i, j, flip=False):
        p, q, r, s = a * n + i, a * n + j, b * n + j, b * n + i
        faces.extend([[p, r, q], [p, s, r]] if flip else [[p, q, r], [p, r, s]])

    for i in range(n):
        j = (i + 1) % n
        quad(0, 1, i, j, True)
        quad(2, 3, i, j)
        quad(0, 2, i, j)
        quad(1, 3, i, j, True)
    mesh = trimesh.Trimesh(vertices=verts, faces=np.array(faces), process=True)
    mesh.fix_normals()
    if tilt:
        mesh.apply_transform(trimesh.transformations.rotation_matrix(
            np.radians(tilt), [1, 0.4, 0]))
        mesh.apply_translation([12, -5, 7])
    return mesh


def have_blender():
    from engine.blend_import import find_blender
    try:
        find_blender()
        return True
    except RuntimeError:
        return False


needs_blender = pytest.mark.skipif(not have_blender(), reason="Blender is not installed")


# ------------------------------------------------------------------ sizes
def test_a_ring_size_is_its_inner_circumference():
    """ISO 8653: the size *is* the circumference in millimetres."""
    assert J.iso_size(54.0) == 54.0
    assert J.us_size(54.4) == pytest.approx(7.0, abs=0.01)
    assert J.circumference_for_us(7.0) == pytest.approx(54.4, abs=0.01)


def test_us_sizes_step_by_the_published_amount():
    step = J.circumference_for_us(8.0) - J.circumference_for_us(7.0)
    assert step == pytest.approx(2.55, abs=0.001)


# ------------------------------------------------------------------ measuring
def test_the_bore_measures_what_it_was_built_as():
    found = J.measure(ring(bore_x=16.4, bore_y=17.9))
    assert found["bore_min_mm"] == pytest.approx(16.4, abs=0.05)
    assert found["bore_max_mm"] == pytest.approx(17.9, abs=0.05)


def test_the_finger_axis_is_found_however_the_model_was_saved():
    """A ring's mass sits at one radius from that axis, at any angle."""
    upright = J.measure(ring(tilt=0.0))
    tilted = J.measure(ring(tilt=37.0))
    assert upright["bore_min_mm"] == pytest.approx(tilted["bore_min_mm"], abs=0.05)
    assert upright["band_width_mm"] == pytest.approx(tilted["band_width_mm"], abs=0.05)


def test_an_oval_bore_is_called_out():
    found = J.measure(ring(bore_x=16.4, bore_y=17.9))
    assert found["out_of_round"] is True
    assert found["ovality_mm"] == pytest.approx(1.5, abs=0.1)


def test_a_round_bore_is_not_called_out():
    found = J.measure(ring(bore_x=17.2, bore_y=17.2))
    assert found["out_of_round"] is False


def test_the_size_comes_from_the_narrowest_point_not_the_average():
    """
    That is where the finger meets it. Averaging an oval invents a size the ring
    does not have, and it would be a size too large — the worst direction to err.
    """
    found = J.measure(ring(bore_x=16.4, bore_y=17.9))
    assert found["circumference_mm"] == pytest.approx(math.pi * 16.4, abs=0.2)


def test_it_says_at_what_size_the_bore_would_come_out_round():
    """Cutting can only remove metal, so a bore wider than the target stays oval."""
    found = J.measure(ring(bore_x=16.4, bore_y=17.9))
    assert found["round_from_iso"] == pytest.approx(math.pi * 17.9, abs=0.3)
    assert found["round_from_iso"] > found["iso_size"]


def test_a_thin_band_is_flagged_against_the_casting_minimum():
    thin = J.measure(ring(thick=0.6))
    assert thin["thinnest_wall_mm"] == pytest.approx(0.6, abs=0.05)
    assert thin["too_thin"] is True
    assert J.measure(ring(thick=1.6))["too_thin"] is False


def test_something_that_is_not_a_ring_is_refused_in_words():
    with pytest.raises(J.NotARing) as trouble:
        J.measure(trimesh.creation.box(extents=(10, 10, 10)))
    assert "ring" in str(trouble.value).lower()


def test_measuring_never_changes_the_model():
    original = ring()
    before = original.vertices.copy()
    J.measure(original)
    assert np.array_equal(original.vertices, before)


# ------------------------------------------------------------------ fixing, for real
@needs_blender
def test_the_corrected_ring_is_the_size_that_was_asked_for():
    fixed, _ = J.fix(ring(), target_circumference_mm=54.0, comfort=True, bevel_mm=0.15)
    after = J.measure(fixed)
    assert after["iso_size"] == pytest.approx(54.0, abs=0.2), (
        "the size is read back off the corrected ring, not assumed from the request")
    assert fixed.is_watertight


@needs_blender
def test_correcting_does_not_move_the_ring():
    """
    It goes to Blender standing on +Z and comes back where it was. If it did not,
    undo, export and every measurement already taken would disagree with it.
    """
    original = ring()
    fixed, _ = J.fix(original, target_circumference_mm=54.0, bevel_mm=0.0)
    moved = np.linalg.norm(fixed.bounds.mean(axis=0) - original.bounds.mean(axis=0))
    assert moved < 0.05, f"the ring moved {moved:.3f} mm"


@needs_blender
def test_the_edges_come_off_without_losing_the_engraving():
    """
    The reason this goes to Blender at all. Voxel filtering rounds edges and blurs
    away the 0.3 mm detail that casting guidelines ask for; an angle-limited bevel
    rounds the corners and leaves shallow detail alone.
    """
    band = trimesh.creation.annulus(r_min=8.6, r_max=10.4, height=6.0, sections=240)
    grooves = []
    for k in range(12):
        angle = 2 * np.pi * k / 12
        cut = trimesh.creation.box(extents=(0.6, 0.8, 8.0))
        place = np.eye(4)
        place[:3, :3] = trimesh.transformations.rotation_matrix(angle, [0, 0, 1])[:3, :3]
        place[:3, 3] = [10.4 * np.cos(angle), 10.4 * np.sin(angle), 0]
        cut.apply_transform(place)
        grooves.append(cut)
    engraved = trimesh.boolean.difference([band] + grooves)

    def sharp(mesh):
        return float((np.degrees(mesh.face_adjacency_angles) > 55).mean())

    def groove_depth(mesh):
        centre = mesh.bounds.mean(axis=0)
        theta = np.linspace(0, 2 * np.pi, 1200, endpoint=False)
        dirs = np.column_stack([np.cos(theta), np.sin(theta), np.zeros_like(theta)])
        hits, idx, _ = mesh.ray.intersects_location(
            np.repeat(centre[None, :], len(theta), axis=0), dirs, multiple_hits=True)
        outer = np.zeros(len(theta))
        for ray, point in zip(idx, hits):
            outer[ray] = max(outer[ray], float(np.linalg.norm(point - centre)))
        outer = outer[outer > 0]
        return float(np.percentile(outer, 99) - np.percentile(outer, 1))

    before_sharp, before_depth = sharp(engraved), groove_depth(engraved)
    assert before_sharp > 0.1 and before_depth > 0.2

    fixed, _ = J.fix(engraved, target_circumference_mm=0.0, bevel_mm=0.15)
    assert sharp(fixed) < 0.02, "the edges were not rounded"
    assert groove_depth(fixed) > before_depth * 0.8, "the engraving was smoothed away"


@needs_blender
def test_a_thin_band_can_be_grown_to_the_casting_minimum():
    thin = ring(thick=0.6)
    fixed, _ = J.fix(thin, target_circumference_mm=0.0, bevel_mm=0.0, thicken_mm=0.5)
    assert J.measure(fixed)["outer_diameter_mm"] > J.measure(thin)["outer_diameter_mm"]


@needs_blender
def test_shrinkage_scaling_is_applied_and_is_the_last_word():
    plain, _ = J.fix(ring(), target_circumference_mm=54.0, bevel_mm=0.0, scale_percent=100.0)
    bigger, _ = J.fix(ring(), target_circumference_mm=54.0, bevel_mm=0.0, scale_percent=103.0)
    assert J.measure(bigger)["circumference_mm"] == pytest.approx(
        J.measure(plain)["circumference_mm"] * 1.03, rel=0.01)


def test_without_blender_the_refusal_says_what_to_do(monkeypatch):
    from engine import blend_import

    def missing():
        raise RuntimeError("This needs Blender, and Meshwright could not find it. "
                           "Install Blender from blender.org, or use “Find Blender…” "
                           "to point at blender.exe yourself.")
    monkeypatch.setattr(blend_import, "find_blender", missing)
    with pytest.raises(RuntimeError) as trouble:
        J.fix(ring(), target_circumference_mm=54.0)
    assert "Blender" in str(trouble.value) and "blender.exe" in str(trouble.value)
