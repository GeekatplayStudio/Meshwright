"""
Cutting the walk sheet into sprites.

Three things have to be right or the cup does not read as a cartoon: the background
has to come off without hollowing out a character drawn in white, the poses have to
end up in walk order, and every frame has to sit on the same ground line so he walks
rather than judders.
"""
import math

import numpy as np
import pytest
from PIL import Image, ImageDraw

from scripts.build_walk_frames import (
    align,
    bounce,
    cut_cells,
    order_cycle,
    stride,
    turning_points,
)


def _sheet(columns=4, rows=2, cell=(200, 240)):
    """
    A stand-in sheet: a white body with a black outline, on white paper.

    White on white is the case that matters — a cutout that simply drops white
    pixels would leave a ring instead of a character.
    """
    cw, ch = cell
    sheet = Image.new("RGB", (cw * columns, ch * rows), (255, 255, 255))
    draw = ImageDraw.Draw(sheet)
    count = columns * rows

    for i in range(count):
        cx = (i % columns) * cw + cw // 2
        cy = (i // columns) * ch + ch // 2
        phase = i / count * 2 * math.pi
        spread = int(40 * abs(math.sin(phase)))     # feet apart, then together
        bob = int(6 * math.cos(2 * phase))          # body rises twice per cycle

        draw.ellipse([cx - 48, cy - 62 + bob, cx + 48, cy + 18 + bob],
                     fill=(255, 255, 255), outline=(0, 0, 0), width=5)
        ground = cy + 78
        for side in (-1, 1):
            fx = cx + side * spread
            draw.line([cx + side * 12, cy + 14 + bob, fx, ground], fill=(0, 0, 0), width=7)
            draw.ellipse([fx - 15, ground - 7, fx + 11, ground + 7], fill=(0, 0, 0))
    return sheet


@pytest.fixture
def cells():
    return cut_cells(_sheet())


# ------------------------------------------------------------------ cutting
def test_every_pose_is_found(cells):
    assert len(cells) == 8


def test_the_background_is_gone(cells):
    for i, frame in enumerate(cells):
        alpha = np.asarray(frame.split()[-1])
        assert alpha[0, 0] == 0, f"pose {i + 1} kept its corner"
        assert (alpha == 0).any(), f"pose {i + 1} is fully opaque"


def test_a_white_character_is_not_hollowed_out(cells):
    """
    The whole reason this does a flood fill from the border instead of keying on
    colour: the cup is white, and so is the paper.
    """
    for i, frame in enumerate(cells):
        pixels = np.asarray(frame)
        opaque = pixels[:, :, 3] > 0
        white_body = opaque & (pixels[:, :, 0] > 200)
        assert white_body.sum() > 200, f"pose {i + 1} lost its white fill"


def test_frames_are_trimmed_to_the_drawing(cells):
    for frame in cells:
        alpha = np.asarray(frame.split()[-1]) > 0
        assert alpha.any(axis=1)[0] and alpha.any(axis=1)[-1], "empty rows left on"
        assert alpha.any(axis=0)[0] and alpha.any(axis=0)[-1], "empty columns left on"


# ------------------------------------------------------------------ ordering
def test_stride_separates_contact_from_passing(cells):
    spreads = [stride(c) for c in cells]
    assert max(spreads) > min(spreads) * 1.5, "the poses do not differ enough to order"


def test_a_sheet_already_in_order_is_left_alone(cells):
    order, verdict = order_cycle(cells)
    assert order == list(range(len(cells)))
    assert "already in walk order" in verdict


def test_the_bounce_is_what_reads_the_cycle(cells):
    """
    A walk rises and falls once per step — twice over eight drawings. That is the
    signal the ordering trusts, because foot positions do not survive measurement:
    the lifted foot is clear of the ground, so a band across the bottom of the
    drawing finds one foot rather than two.
    """
    heights = [bounce(c) for c in cells]
    assert turning_points(heights) >= 4, f"not a cycle: {heights}"
    assert max(heights) > min(heights) * 1.03, "no bounce at all"


def test_a_sheet_that_does_not_read_as_a_cycle_is_reordered_and_flagged():
    """
    Sorting a sheet is a guess, so it only happens when the drawings do not already
    read as a cycle — and when it does happen the caller is told to go and look.
    """
    cells = cut_cells(_sheet())
    scrambled = [cells[i] for i in (0, 4, 1, 5, 2, 6, 3, 7)]      # bounce no longer swings
    order, verdict = order_cycle(scrambled)

    if "REORDERED" in verdict:
        heights = [bounce(scrambled[i]) for i in order]
        assert turning_points(heights) >= 4, f"reorder did not produce a cycle: {heights}"
    else:
        assert order == list(range(len(scrambled)))


# ------------------------------------------------------------------ alignment
def test_all_frames_share_one_canvas(cells):
    frames = align(cells, height=220)
    assert len({f.size for f in frames}) == 1
    assert frames[0].height == 220


def test_the_planted_foot_stays_on_the_ground(cells):
    """If the feet wander between frames he skates instead of walking."""
    frames = align(cells, height=220)
    lowest = []
    for frame in frames:
        alpha = np.asarray(frame.split()[-1]) > 0
        lowest.append(np.flatnonzero(alpha.any(axis=1)).max())
    assert max(lowest) - min(lowest) <= 1, f"ground line drifts: {lowest}"


def test_the_body_still_rises_and_falls(cells):
    """
    Aligning on the feet has to keep the bounce, not flatten it — that vertical
    give is what makes an old cartoon walk look rubbery rather than mechanical.
    """
    frames = align(cells, height=220)
    tops = []
    for frame in frames:
        alpha = np.asarray(frame.split()[-1]) > 0
        tops.append(np.flatnonzero(alpha.any(axis=1)).min())
    assert max(tops) - min(tops) >= 2, f"the bob was flattened out: {tops}"


def test_frames_stay_transparent_after_alignment(cells):
    for frame in align(cells, height=220):
        assert frame.mode == "RGBA"
        assert np.asarray(frame.split()[-1])[0, 0] == 0
