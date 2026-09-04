# User guide

## Opening a model

Drag a file onto the window, or press <kbd>Ctrl</kbd>+<kbd>O</kbd>.
Supported: **OBJ, FBX, GLB, GLTF, STL, PLY, 3MF, DAE, OFF, 3DS**.

Meshwright ships no 3D models — it is a workshop for files you already have, so nothing has to be
downloaded and no model folder has to be configured. To try it without a file of your own, click
**Load demo model** in the empty viewport: a small object is built in memory with a hole in it, a
patch of inside-out triangles and a loose second piece, which **Repair** takes to watertight.

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

## Textures

Meshwright keeps whatever textures came with your model, and keeps them lined up through
repair and reduction.

**What it picks up on open.** Textures baked into the file (GLB, glTF, FBX) are read from
the model's own material. Textures the file only points at — an OBJ with its `.mtl`, or a
folder of PNGs beside the model — are found by name, including the layouts Meshy, Tripo,
Sketchfab and Blender export: `model.png`, `*_baseColor`, `*_Normal_OpenGL`, `*_Roughness`,
`*_AO`, and packed `*_ORM` / `*_metallicRoughness` maps, which are unpacked into their
separate channels. It also looks in `textures/`, `images/`, `materials/` and `.fbm`
subfolders. If a file both declares a texture and has a stray image sitting beside it, the
one the file declares wins.

**Repair and Reduce keep the mapping.** Texture coordinates travel with the model through
every operation, and the model stays a proper welded solid while they do — repairing or
decimating a textured model does not turn it into a pile of pieces, and the diagnostics
score reflects the real geometry. Worst-case drift after decimating to a fiftieth of the
original face count is under half a texel on a 2048 px map.

**Unwrap UVs** builds a fresh layout for a model that has none. It does not change the
geometry at all: a watertight model stays watertight and its score does not move. If the
model already has UVs, Meshwright asks first — a new layout invalidates any texture that
was painted for the old one.

Unwrapping reports what it produced: how many **islands** the layout has, how many
**seam edges**, the atlas size, and whether the texture density came out even. An uneven
result — some parts of the model getting noticeably more texture detail than others —
means the shape is hard to flatten; it is a warning, not a failure, and the layout is
still usable. Meshwright re-splits and retries automatically to get the evenness it can.

Dense models are fine: a 327,000-face model unwraps in about two seconds. If the
unwrapper cannot produce a usable layout at all you get a message saying so, not a
crash — **Reduce** the model first, then unwrap.

**If your textures look flat or missing**, check what came with the file. Some
generators — Meshy and Hi3D among them — put a 2×2 placeholder inside the FBX and ship
the real 2048px maps as separate PNGs beside it; Meshwright ignores the placeholder and
uses the real files. And a model can simply have no UV coordinates: an untextured
"generate" export from Meshy has none, and nothing can map a texture onto it until you
**Unwrap UVs**.

**Load Image / Generate PBR** derives normal, roughness, metallic, ambient-occlusion and
height maps from any photo or texture.

Tick **Image tiles seamlessly** if your source is a repeating material — the edges are
then blended so it tiles without a visible join, and the surface detail carries across
them. Leave it off for a photograph or a texture painted for this particular model;
treating those as tiling stamps a hard fake ridge down all four borders of the normal
map.

**Displace** pushes the surface out along the height map, in millimetres. It moves real
vertices, so it shows detail on a dense model and almost nothing on a low-poly one — the
**Height** button in the viewport shows the map itself either way.

**Export Maps** writes every channel plus a transparent UV guide you can open as a layer
in Photoshop or GIMP; **Reload Maps** picks your edits back up. **Save Baked GLB** writes
one self-contained file with the maps embedded.

Anything Meshwright writes out has its seam gutters padded — the colour at the edge of
each UV island is bled outward into the empty space around it, so the texture does not
show dark fringes along the seams when a renderer filters or mipmaps it.

## Exporting

Pick the **format** — STL, OBJ, PLY, OFF, GLB, glTF or 3MF — and the **source units** of your file
(mm, cm or in) so the model is scaled correctly, and leave **Rest on build plate** ticked to centre
it in X/Y and drop it to Z = 0. **Export** (<kbd>Ctrl</kbd>+<kbd>S</kbd>) writes the file; STL is
binary. Files are named `<original>-GS-<timestamp>-fixed.<ext>` by default, so an export never
overwrites the model you started from.

OBJ, GLB and glTF carry your texture coordinates and maps out with the model. STL, PLY, OFF
and 3MF have no way to store them, so those are written as geometry only — which is what a
slicer wants anyway.

### “Saved, but this is not a printable solid”

Meshwright measures the mesh as it writes it and says so when the result is not a closed volume.
This matters because a slicer fills the **inside** of a solid: an open surface has no inside, so it
is sliced as a single-wall shell — one perimeter thick, no infill — whatever the infill setting says.

| Warning | What it means | What to do |
|---|---|---|
| *not a closed solid — N edges are open* | The mesh is a surface, not a body | Press **Repair**, then export again; the diagnostics must say watertight |
| *encloses no volume* | The surfaces lie on top of each other | The source model has zero thickness — give it thickness where it was made |
| *hollow with walls averaging X mm* | It is a genuine shell, thinner than a nozzle can fill | Correct for vase-mode prints; otherwise thicken it |

Inside-out models are turned the right way out on the way to the file, so a mesh a slicer used to
read as a cavity comes out as a solid.

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

## Knowing what it is doing

Anything that takes more than a moment announces itself in the bottom-right corner: what it is doing,
how many faces are involved and roughly how long to expect, with a live timer and progress bar. When
it finishes, the notification reports the real elapsed time and the outcome. Click **×** to dismiss,
or leave it — result notifications fade on their own.

The same information, with timestamps, goes to the **Activity console** and to the terminal, so you
can follow a long run without watching the window.

Estimates scale with the model: repairing two million faces is announced as "about 1–2 minutes",
while a small part is "a moment".

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

## Viewport detail

A model with millions of triangles takes a while to draw, so Meshwright shows a
simplified version of it and tells you: *"Viewport showing 18% of this model"*. Drag
the **Detail** slider in the viewport toolbar up for the full mesh, or down if you
want the view to spin more freely.

This is the picture only. The diagnostics, repair, reduce, retopology and every export
always use every triangle in the model — the number in the Geometry panel is the real
one. While the view is simplified the open-edge overlay is switched off, because it
would be marking edges of the simplified copy rather than of your model.

## Starting over

**New** in the top bar (<kbd>Ctrl</kbd>+<kbd>N</kbd>) closes the model and empties the
workspace — mesh, history, diagnostics and textures. <kbd>Delete</kbd> does the same, unless
you have pieces ticked in **Separate pieces**, in which case it removes those instead.
Either way you are asked to confirm before anything with unsaved changes is thrown away.

## Keyboard

Press <kbd>?</kbd> at any time for the full list. The ones worth learning:
<kbd>Ctrl</kbd>+<kbd>O</kbd> open · <kbd>Ctrl</kbd>+<kbd>N</kbd> close the model ·
<kbd>Ctrl</kbd>+<kbd>Z</kbd> undo · <kbd>Ctrl</kbd>+<kbd>R</kbd> repair ·
<kbd>Ctrl</kbd>+<kbd>S</kbd> export · <kbd>W</kbd> wireframe · <kbd>F</kbd> fit ·
<kbd>1</kbd>–<kbd>7</kbd> standard views.
