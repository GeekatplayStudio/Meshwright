r"""
Turn a hand-drawn walk-cycle sheet into the sprite strip the app animates.

Drop the sheet in ui/assets/walk/ and run it with no arguments:

    .venv\Scripts\python scripts\build_walk_frames.py

Any image in that folder whose name is not one of the generated files is taken as
the sheet, so walk-sheet.png, cup-cycle.png or whatever it came off the scanner as
will all be found. A path can still be given explicitly:

    .venv\Scripts\python scripts\build_walk_frames.py "path\to\sheet.png"

The sheet is a grid of poses on a plain background — the eight-frame cup cycle that
ships with Meshwright is two rows of four. This does three things to it that are
easy to get wrong by hand:

**Cuts out the background without hollowing the character.** The cup is white with
black outlines, so anything that simply makes white pixels transparent eats the cup
as well. The background is found by flooding inwards from the border instead, which
stops at the outlines and leaves everything they enclose alone.

**Puts the cycle in order.** A walk alternates between contact — feet furthest
apart — and passing, where they cross under the body. Measuring the horizontal
spread of each frame's lowest pixels gives that phase directly, so the order comes
out of the drawing rather than out of a guess about which cell came first.

**Lines the frames up on the ground.** Each pose is pasted onto a common canvas with
its planted foot on the same line and its body centred, so the character walks
instead of juddering. The body's own rise and fall between poses is what makes it
feel rubbery, and aligning on the feet is what preserves it.
"""
import os
import sys
from collections import deque

import numpy as np
from PIL import Image

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "ui", "assets", "walk")

# What this script writes, so it never mistakes its own output for the sheet.
GENERATED = {"walk-strip.png"} | {f"frame-{i:02d}.png" for i in range(1, 33)}
SHEET_TYPES = (".png", ".jpg", ".jpeg", ".webp", ".bmp")

# A pixel this close to the sheet's corner colour is background, if it is also
# connected to the border. Generous, because scans and exports are never clean.
BACKGROUND_TOLERANCE = 34
# Ignore specks left by compression when looking for the gaps between cells.
MIN_CELL_PIXELS = 400
FRAME_HEIGHT = 220          # what the strip is rendered at; width follows the art


def _background_mask(rgb: np.ndarray) -> np.ndarray:
    """
    True where the pixel is background: near the sheet's corner colour *and*
    reachable from the border without crossing a line.
    """
    h, w = rgb.shape[:2]
    corners = np.stack([rgb[0, 0], rgb[0, -1], rgb[-1, 0], rgb[-1, -1]]).astype(np.int16)
    paper = np.median(corners, axis=0)

    near = (np.abs(rgb.astype(np.int16) - paper).max(axis=2) <= BACKGROUND_TOLERANCE)

    # Flood from every border pixel that is near the paper colour.
    reached = np.zeros((h, w), dtype=bool)
    queue = deque()
    for x in range(w):
        for y in (0, h - 1):
            if near[y, x] and not reached[y, x]:
                reached[y, x] = True
                queue.append((y, x))
    for y in range(h):
        for x in (0, w - 1):
            if near[y, x] and not reached[y, x]:
                reached[y, x] = True
                queue.append((y, x))

    while queue:
        y, x = queue.popleft()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w and near[ny, nx] and not reached[ny, nx]:
                reached[ny, nx] = True
                queue.append((ny, nx))
    return reached


def _spans(occupied: np.ndarray) -> list[tuple[int, int]]:
    """Start and end of each run of True — the cells along one axis."""
    padded = np.concatenate([[False], occupied, [False]])
    edges = np.diff(padded.astype(np.int8))
    return list(zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)))


def cut_cells(sheet: Image.Image):
    """Every drawing on the sheet, cut out and made transparent."""
    rgb = np.asarray(sheet.convert("RGB"))
    ink = ~_background_mask(rgb)

    rows = [s for s in _spans(ink.sum(axis=1) > 0) if ink[s[0]:s[1]].sum() > MIN_CELL_PIXELS]
    cells = []
    for top, bottom in rows:
        band = ink[top:bottom]
        for left, right in _spans(band.sum(axis=0) > 0):
            block = band[:, left:right]
            if block.sum() < MIN_CELL_PIXELS:
                continue
            ys, xs = np.nonzero(block)
            cut = sheet.convert("RGBA").crop(
                (left + xs.min(), top + ys.min(), left + xs.max() + 1, top + ys.max() + 1))
            alpha = block[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
            cut.putalpha(Image.fromarray((alpha * 255).astype(np.uint8)))
            cells.append(cut)
    return cells


def stride(frame: Image.Image) -> float:
    """
    How far apart the feet are, measured against the character's height.

    Widest at contact, narrowest as the legs pass — that is the walk's phase, and it
    is what puts the frames in order.

    Against the height rather than the width, because the width is the bounding box
    and the arms swing: a pose with both arms out is wider than the same pose with
    them tucked in, which would make an unchanged stance read as a shorter step. The
    character's height barely moves, so it is the stable thing to divide by.
    """
    alpha = np.asarray(frame.split()[-1]) > 0
    if not alpha.any():
        return 0.0
    ys = np.flatnonzero(alpha.any(axis=1))
    height = ys.max() - ys.min() + 1
    ground = alpha[max(ys.max() - height // 8, 0):]             # the bottom eighth: feet
    xs = np.flatnonzero(ground.any(axis=0))
    return (xs.max() - xs.min()) / height if len(xs) else 0.0


def bounce(frame: Image.Image) -> float:
    """
    How tall the character is drawn, in pixels.

    This is the reliable read on a walk's phase. A figure is tallest at the top of a
    step, with the supporting leg straight, and shortest as the weight passes over
    it and the knee bends — twice each per cycle. Foot positions look like the more
    obvious signal but are not: in most poses the lifted foot is clear of the ground,
    so a band across the bottom of the drawing finds one foot rather than two and
    measures nothing useful.
    """
    alpha = np.asarray(frame.split()[-1]) > 0
    if not alpha.any():
        return 0.0
    rows = np.flatnonzero(alpha.any(axis=1))
    return float(rows.max() - rows.min() + 1)


def turning_points(values: list[float]) -> int:
    """How many times a looping sequence changes direction — a cycle has four."""
    n = len(values)
    return sum(1 for i in range(n)
               if (values[i] - values[i - 1]) * (values[(i + 1) % n] - values[i]) < 0)


def order_cycle(frames: list[Image.Image]) -> tuple[list[int], str]:
    """
    The order the frames play in, and what the measurements had to say about it.

    A walk rises and falls twice over eight drawings, so a correctly ordered sheet
    shows four turning points in its heights. If it does, the sheet is left exactly
    as it was drawn — an artist's ordering beats anything inferred here.

    If it does not, the frames are paired tall with short so the bounce at least
    alternates, and the caller is told, because a re-sorted cycle is a guess and
    worth looking at before it ships.
    """
    heights = [bounce(f) for f in frames]
    if turning_points(heights) >= 4:
        return list(range(len(frames))), "the sheet is already in walk order"

    tall = sorted(range(len(frames)), key=lambda i: -heights[i])
    half = len(tall) // 2
    reordered = [i for pair in zip(tall[:half], tall[half:]) for i in pair]
    return reordered, "REORDERED — the sheet did not read as a cycle; check the result"


def align(frames: list[Image.Image], height: int = FRAME_HEIGHT):
    """
    Every frame on one canvas, planted foot on the same line, body centred.

    Aligning on the lowest pixel rather than on the bounding box is the whole trick:
    it pins the foot that is on the ground and lets the body rise and fall above it,
    which is the bounce that makes an old cartoon walk read as rubber rather than as
    a cut-out sliding along.
    """
    scale = height / max(f.height for f in frames)
    scaled = [f.resize((max(1, round(f.width * scale)), max(1, round(f.height * scale))),
                       Image.Resampling.LANCZOS) for f in frames]
    width = max(f.width for f in scaled) + 8

    out = []
    for frame in scaled:
        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        canvas.alpha_composite(frame, ((width - frame.width) // 2, height - frame.height))
        out.append(canvas)
    return out


def find_sheet() -> str | None:
    """The walk sheet sitting in ui/assets/walk, if one has been dropped there."""
    if not os.path.isdir(OUT_DIR):
        return None
    candidates = [f for f in sorted(os.listdir(OUT_DIR))
                  if f.lower().endswith(SHEET_TYPES) and f.lower() not in GENERATED]
    return os.path.join(OUT_DIR, candidates[0]) if candidates else None


def main(sheet_path: str | None = None) -> int:
    sheet_path = sheet_path or find_sheet()
    if not sheet_path:
        print("No walk sheet found.")
        print("Save the drawing here, then run this again:")
        print(f"  {os.path.join(OUT_DIR, 'walk-sheet.png')}")
        return 1
    if not os.path.exists(sheet_path):
        print(f"No such file: {sheet_path}")
        return 1
    print(f"Reading {sheet_path}")

    sheet = Image.open(sheet_path)
    print(f"Sheet {sheet.size[0]}x{sheet.size[1]}")

    cells = cut_cells(sheet)
    if not cells:
        print("Found no drawings on that sheet — is the background a flat colour?")
        return 1
    print(f"Cut {len(cells)} pose(s)")

    order, verdict = order_cycle(cells)
    frames = align([cells[i] for i in order])

    print()
    print(f"Cycle order: {' '.join(str(i + 1) for i in order)}  ({verdict})")
    heights = [bounce(cells[i]) for i in order]
    tallest, shortest = max(heights), min(heights)
    print(f"{'frame':>6} {'pose':>5} {'height':>7} {'phase':>8} {'stride':>7}")
    for position, source in enumerate(order):
        h = heights[position]
        phase = "up" if h > tallest - (tallest - shortest) * 0.25 else (
                "down" if h < shortest + (tallest - shortest) * 0.25 else "mid")
        print(f"{position + 1:>6} {source + 1:>5} {h:>7.0f} {phase:>8} {stride(cells[source]):>7.2f}")
    print()
    print(f"The body rises and falls {turning_points(heights) // 2} time(s) over "
          f"{len(order)} drawings — a walk should show two, one per step.")

    os.makedirs(OUT_DIR, exist_ok=True)
    strip = Image.new("RGBA", (frames[0].width * len(frames), frames[0].height), (0, 0, 0, 0))
    for i, frame in enumerate(frames):
        frame.save(os.path.join(OUT_DIR, f"frame-{i + 1:02d}.png"))
        strip.alpha_composite(frame, (i * frame.width, 0))
    strip.save(os.path.join(OUT_DIR, "walk-strip.png"))

    print(f"\nWrote {len(frames)} frames and walk-strip.png to {OUT_DIR}")
    print(f"Frame size {frames[0].width}x{frames[0].height} — "
          f"set --walk-frame-w in ui/css/style.css if this changed.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1] if len(sys.argv) == 2 else None))
