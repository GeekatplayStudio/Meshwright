# User guide

## Opening a model

Drag a file onto the window, or press <kbd>Ctrl</kbd>+<kbd>O</kbd>.
Supported: **OBJ, FBX, GLB, GLTF, STL, PLY, 3MF, DAE, OFF, 3DS**.

Loading shows every step in the **Activity console** (top-right button, or <kbd>Ctrl</kbd>+<kbd>`</kbd>):
which parser is used, the triangle count, vertex merging, analysis and timings. A two-million-triangle
model takes roughly 20 seconds.

## Reading the diagnostics

The score ring gives you the verdict at a glance. Below it, every issue is listed with a severity dot:

- 🔴 **critical** — the model will fail or misprint
- 🟠 **warning** — expect artifacts
- 🔵 **note** — worth knowing

**Click any issue** to fly the camera to it. Exact locations are marked in cyan, with a translucent
sphere for the general area so even a single bad triangle is findable. Click again, press
<kbd>Esc</kbd>, or use **Clear** to dismiss.

**Re-analyse** re-runs the checks on the current mesh at any time. **Save JSON** writes the full
report — diagnostics plus every operation applied — for records or a client.

See [DIAGNOSTICS.md](DIAGNOSTICS.md) for what each check means.

## Repairing

Tick **Force watertight** (default) to allow the voxel rebuild as a last resort — it guarantees a
closed solid but smooths fine detail. Untick it if detail matters more than a guaranteed seal.

Press **Repair mesh** (<kbd>Ctrl</kbd>+<kbd>R</kbd>). Afterwards:

- **What was fixed** lists only the stages that actually changed something.
- **Before → after** shows each metric that moved, improvements in green.
- If issues remain, it says so — the report is measured on the repaired mesh, not predicted.

**Fix sliver triangles** appears only when slivers are present. It merges needle triangles into
their larger neighbours and flips cap triangles, leaving surrounding geometry in place.

If a repair would make things worse, Meshwright refuses it, keeps your previous state and offers
**Apply anyway**. That is deliberate — see [Safety](#safety).

## Separate pieces

When a model contains several disconnected pieces they are colour-coded in the viewport and listed
with triangle counts and sizes. Tick the ones you want gone — they turn red — then **Remove selected**
(<kbd>Del</kbd>). <kbd>Ctrl</kbd>+<kbd>A</kbd> selects all; **Highlight** toggles the colouring off.

You cannot remove every piece.

## Orientation

Turn on **Rotate** in the viewport toolbar (<kbd>R</kbd>) and drag the rings, or use **X / Y / Z +90°**
in the Orientation panel. Rotation is instant in the viewport and applies to the exported STL.

## Reducing polygons

Pick an engine, set a target, press **Reduce polygons**. Presets go from 500 to 50k, or type an exact
face count. The result line reports the surface deviation in millimetres — the honest measure of what
you lost. Full guidance in [REDUCTION.md](REDUCTION.md).

## Exporting

Choose the **source units** of your file (mm, cm or in) so the model is scaled correctly, and leave
**Rest on build plate** ticked to centre it in X/Y and drop it to Z = 0. **Export STL**
(<kbd>Ctrl</kbd>+<kbd>S</kbd>) writes a binary STL.

## The viewport

| Control | |
|---|---|
| Drag | Orbit |
| Scroll | Zoom |
| Right-drag | Pan |
| Compass / preset buttons | Top, Front, Right, Iso, Bottom, Back, Left |
| <kbd>1</kbd>–<kbd>7</kbd> | The same views |
| <kbd>F</kbd> | Fit |

**Shaded · Clay · Normals · X-ray** change the material. **Wire** overlays the wireframe, **Edges**
highlights open boundary edges in red, **Plate** toggles the build plate grid.

The **Light** panel moves the key light — useful for reading surface detail before printing.

Drag the divider between viewport and panel to resize; double-click it to reset. The width is remembered.

## Safety

Every change creates a **numbered state**, shown in the top bar.

- <kbd>Ctrl</kbd>+<kbd>Z</kbd> undo, <kbd>Ctrl</kbd>+<kbd>Y</kbd> redo — the only ways backwards.
- **Revert to original file** discards the session and reloads the file as opened. It asks first, and
  it is itself undoable.
- A change that would add critical problems or discard most of the geometry is **rejected**; your
  previous state is kept and you are offered "Apply anyway".
- Every accepted state is snapshotted to disk in the background. If Meshwright does not close cleanly,
  the next start offers to **recover** it. Snapshots live in `%LOCALAPPDATA%\Meshwright\sessions` and
  are removed on a clean exit.

## Keyboard

Press <kbd>?</kbd> at any time for the full list.
