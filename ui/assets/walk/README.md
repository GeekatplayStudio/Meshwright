# The walking cup

`cup2.png` is the hand-drawn walk sheet — eight poses, two rows of four.
`walk-strip.png` is what the application animates from: the same eight drawings cut
out, made transparent, put in cycle order and lined up on the ground, side by side.

Both are committed, because Meshwright ships as a ZIP and nobody should have to run a
build step to get the cup. Regenerate the strip after editing the sheet with:

```
.venv\Scripts\python scripts\build_walk_frames.py
```

It finds the sheet in this folder on its own; if there is more than one it takes the
most recent and says which. A sheet exported with transparency is used as drawn — its
own alpha knows the difference between the paper and a white highlight on the cup,
which no amount of guessing does. Only a sheet on flat paper is flood-filled from the
border, and it fills from the border rather than keying on white so that a white
character does not come back hollow.

It also writes `frame-01.png` … `frame-08.png` for reference. The application never
loads them; only the strip is used.

**Copy the frame size it prints into `--walk-frame-w` in `ui/css/style.css`.** The
strip is one image eight frames wide inside a window one frame wide, so a stale width
shows parts of two drawings at once. `tests/test_walker_states.py` fails if the two
disagree, and names the fix.

The script prints its reading of the cycle. What it is measuring is the bounce: a walk
rises and falls once per step, so eight drawings should show two rises. Foot positions
look like the more obvious signal but are not — the lifted foot is clear of the ground,
so a band across the bottom of a pose finds one foot rather than two.

```
Cycle order: 1 2 3 4 5 6 7 8  (the sheet is already in walk order)
 frame  pose  height    phase  stride
     1     1     350      mid    0.82
     2     2     322     down    0.56
     3     3     347      mid    0.48
     4     4     374       up    0.27
     5     5     336      mid    0.72
     6     6     314     down    0.57
     7     7     334      mid    0.49
     8     8     357      mid    0.27

The body rises and falls 2 time(s) over 8 drawings — a walk should show two, one per step.
```

That bounce is the animation. The cutter aligns every frame on the feet, which keeps
the 35px of rise and fall in the drawings while planting him on one ground line — so
the CSS deliberately adds no bob of its own. Two bounces at different phases judder.

If the sheet does not read as a cycle the script re-sorts it and says so — that result
is a guess and worth looking at before it ships.
