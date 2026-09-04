# The walking cup

`cup-ref.png` is the hand-drawn walk sheet — eight poses, two rows of four.
`walk-strip.png` is what the application animates from: the same eight drawings cut
out, made transparent, put in cycle order and lined up on the ground, side by side.

Both are committed, because Meshwright ships as a ZIP and nobody should have to run a
build step to get the cup. Regenerate the strip after editing the sheet with:

```
.venv\Scripts\python scripts\build_walk_frames.py
```

It finds the sheet in this folder on its own. It also writes `frame-01.png` …
`frame-08.png` for reference, which are not committed — only the strip is used.

The script prints its reading of the cycle. What it is measuring is the bounce: a walk
rises and falls once per step, so eight drawings should show two rises. Foot positions
look like the more obvious signal but are not — the lifted foot is clear of the ground,
so a band across the bottom of a pose finds one foot rather than two.

```
Cycle order: 1 2 3 4 5 6 7 8  (the sheet is already in walk order)
 frame  pose  height    phase  stride
     1     1     356      mid    0.79
     2     2     330     down    0.55
     3     3     354      mid    0.47
     4     4     397       up    0.27
     ...
```

If the sheet does not read as a cycle the script re-sorts it and says so — that result
is a guess and worth looking at before it ships.
