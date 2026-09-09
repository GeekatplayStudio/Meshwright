# Changelog

All notable changes to Meshwright. Format based on [Keep a Changelog](https://keepachangelog.com).

## [Unreleased]

### Added
- **Reliable model loading progress and global visual feedback.** Selecting or dropping a file now
  immediately activates loading feedback on the client side, rather than waiting for an engine roundtrip.
  A glowing amber-to-cyan progress bar pinned to the top of the 3D viewport animates in synchronization
  with background jobs, accompanied by an animated loading spinner and active status prompt in the
  empty workspace. The Python-to-WebView bridge dispatches UI events through a dedicated non-blocking
  background queue, eliminating WebView2 semaphore deadlocks and dropped events.
- **Continuous 2D UV island boundary contours.** The 2D UV Island Unfold viewer now extracts true
  closed 2D boundary loops directly in UV space, rendering crisp continuous outlines against active
  PBR texture channels rather than disjoint floating specks.
- **A cup who waits with you.** Opening a model or writing one out sends a hand-drawn cup strolling
  along the bottom of the window; he leaves when the work is done. Repairs, reductions, unwraps and
  previews keep their progress toast and nothing more — he is on screen for the waits that are about
  the file itself, because a character who turns out for everything is scenery rather than a signal.
  Click him and he stops, under a speech balloon, to ask whether you would like to buy Vlad a coffee.

  He is animated from an eight-frame walk sheet cut by `scripts/build_walk_frames.py`, which lifts
  the background without hollowing out a character drawn in white, checks the cycle order against
  the drawing, and lines every pose up on the ground so he walks rather than skates. The order is
  read from how the body rises and falls — a walk bounces once per step, twice over eight drawings —
  because foot positions do not survive measurement: the lifted foot is clear of the ground, so a
  band across the bottom of a pose finds one foot rather than two.

  He is drawn in colour on a transparent sheet, walks at twelve frames a second, and is timed by
  his stride rather than by the clock — `PX_PER_CYCLE` sets how much ground two steps cover and
  every journey's duration is derived from the distance, so he does not skate on a wide window. He
  keeps walking for as long as the work takes rather than stopping off-screen after one crossing,
  and a job that starts while he is walking out turns him round from where he stands instead of
  snapping him back to the far edge. The speech balloon closes on a click elsewhere, on `Esc`, or
  by itself after seven seconds, and he carries on walking when it does.

  The hop on each step is in the drawings, not the stylesheet: the cutter aligns every pose on the
  feet, which keeps the 35px of rise and fall the artist drew while planting him on one ground
  line. A CSS bob on top runs at its own phase, and two bounces that disagree read as a judder — so
  the layer that used to bob now only leans.

### Fixed
- **Textures scrambled across half the model, and a load that could run out of memory.**
  Both showed up on the same AI-generated GLB — 694,078 faces in 366 loose shells, with a UV atlas
  made of 13,314 islands — and they were unrelated.

  Separating a model into shells renumbers its faces, and the UV channel is meant to be permuted to
  match. It never was: the check for "the faces did not move" was tested first, and it only compares
  *how many* faces there are, which separation does not change. So the permutation branch below it
  was unreachable and every shell was painted with some other shell's artwork. 50.8% of faces on the
  reported model, which is why half of it looked shattered. Single-shell models were unaffected,
  which is why it went unnoticed.

  The viewport's simplified copy had its own version of the same fault. Texture coordinates rode
  through the decimation as one UV per welded vertex, chosen arbitrarily from the corners meeting
  there — but a vertex on a UV seam has several, and on an island-heavy atlas that is 45% of them.
  The display copy now carries UVs per face corner, transferred after decimation and split at seams
  exactly as the full-detail path already did. Wrong faces went from 90.8% to 1.8%, and it costs
  about half a second: the transfer picks each face's island by true surface distance over a short
  candidate list, rather than by nearest triangle centroid.

  Separately, the loader parks the model's images on `mesh.metadata` for the texture engine to pick
  up, and left them there. `mesh.copy()` and `mesh.submesh()` deep-copy that dictionary, so a model
  with 4096px maps cloned them once per shell and died with a `MemoryError` before drawing anything.
  Ownership now transfers properly and the images are dropped from the metadata once bound.

- **Loading progress bar disappeared before the 3D model was visible on screen.** Python previously
  emitted `state: 'done'` before returning the model data across the desktop IPC bridge. The frontend
  dismissed the toast and progress bar immediately, leaving several seconds of heavy base64 decoding
  and WebGL geometry buffer upload with zero visual indication that work was continuing. The progress bar
  is now held through the Three.js rendering stage and dismisses only when the model is rendered.
- **Unwrapping UVs removed loaded textures from the 3D model.** Generating or adjusting a UV unwrap
  previously cleared all active materials (`self.materials.clear()`), wiping out albedo, normal and
  roughness maps and resetting the viewport to plain plastic. Textures are now preserved across unwraps
  and remain mapped onto the surface and displayed in the 2D unfold view.
- **An FBX with its texture baked inside loaded as a grey model, or crashed the program.** Three
  faults in a row on the same file, a 228 MB Hi3D export carrying an 8192×8192 JPEG:

  The FBX parser only ever read geometry and UVs, so the material — and the artwork inside it — was
  never looked at. Nothing downstream could recover it either: trimesh cannot open FBX at all, and
  the companion-file scan looks for images *beside* the model, which a file that embeds its own has
  none of. Textures are now read from the material, through the normalised view that covers Phong,
  Lambert, Arnold, Maya and Blender-style materials alike, embedded or referenced by name.

  Decoding them then took the whole program down without a message. Allocating a large image while
  the FBX scene is still open corrupts its teardown, and the process dies the moment it is released.
  The bytes are now copied out and the scene closed before anything is decoded — which also cut peak
  memory on that model from 2.4 GB to 0.95 GB.

  Finally, a model in more than one piece never showed its texture. The piece-colour overlay paints
  straight onto the mesh, and the viewport will not fight it — so with 49 pieces the map was loaded,
  the UVs were there, the button said PBR, and the model still drew flat grey. A textured model now
  opens showing its texture, and choosing any shading mode takes the overlay down instead of lighting
  the button up and changing nothing. **Highlight** puts the piece colours back.

- **A reduced model showed a wrong colour on a handful of triangles near its seams.** Carrying UVs
  through an edit rebuilds some corners from a neighbouring triangle, which extends that triangle's
  plane and can land a hair past the edge of the texture sheet — measured 0.0077 outside on 108 of
  119,994 corners taking a 2.96M-face model down to 40,000. The viewer samples with repeat wrapping,
  so a hair past the edge fetches a colour from the opposite side of the atlas, on about a hundred
  faces. The transfer now holds its answer inside the range the source itself used, which leaves a
  deliberately tiled layout untouched.

- **Loading a model could fail outright with `IndexError: boolean index did not match indexed
  array`.** The two passes that drop degenerate and duplicate faces built both of their masks up
  front. The first pass shortens the face array, so the second mask was then too long by exactly
  the number of faces the first had removed, and any model with both kinds of bad face refused to
  open. Each mask is now measured against the faces that are actually there when it is applied.

## [1.3.0] — 2026-09-04

### Fixed
- **Textured models were diagnosed as broken, and repair then destroyed them.** UV coordinates were
  stored per vertex, which forces a vertex to be duplicated at every texture seam. That duplication
  breaks the edge joining two triangles, so the analyser read a clean watertight model as a pile of
  disconnected shells with hundreds of open holes — and repair closed holes that were never there.
  A clean textured sphere GLB loaded as *Repair required, score 59, 9 shells*; decimating it to 25%
  produced 201 shells; repair then turned 1,279 faces into 168,811. Meshwright now keeps texture
  coordinates in a per-face-corner array beside the mesh, so the geometry stays welded and analysis,
  repair and reduction all see the real topology. The same model now loads and survives the whole
  pipeline as *Print ready, score 100, watertight, 0 shells*.
- **Reducing or repairing a textured model slid the texture across the surface.** UVs were carried
  over by matching each new triangle to the source triangle with the nearest *centroid*, then
  projecting all three of its corners onto that one triangle and clamping anything that fell
  outside — which snapped those corners onto the triangle's edge. Every corner is now projected to
  its true closest point on the source surface, and corners that land across a UV seam are
  re-evaluated inside the chart their own face belongs to. Measured against a known-exact UV map,
  worst-case drift after decimating to 2% of the original face count fell from 394 texels to 0.46
  on a 2048 px map; after repair, from 775 to 24.
- **Decimation left the mesh as an unwelded triangle soup.** The old UV transfer computed every face
  corner independently and could weld almost none of them back together, so a 1,280-face result came
  back with 3,785 vertices instead of 642 — losing smooth shading, inflating exports and breaking
  watertightness. Decimation now returns a properly welded mesh.
- **Unwrapping a print-ready model reported it as broken.** Unwrap split vertices at every seam, so a
  model scoring 100 dropped to 59 with "7 open holes" without a single triangle changing. Unwrapping
  no longer touches the geometry.
- **Unwrapping crashed the whole application on ordinary watertight models.** xatlas has a bug on
  closed surfaces — where every edge has an opposite, which is exactly what a print-ready model is —
  that leaves its boundary data unset and its convex-hull pass reading uninitialised memory
  ([jpcy/xatlas#146](https://github.com/jpcy/xatlas/issues/146)). An 81,920-face sphere took the
  window down with an access violation and no message. Meshwright now splits every model into open
  patches before unwrapping, so xatlas is never given the input that breaks it, and the patches are
  packed into one atlas so the result is still a single texture layout. The same sphere now unwraps
  in about a second.
- **Unwrapping large models silently produced overlapping UVs.** Above roughly 40,000 faces xatlas
  stops segmenting and returns one chart covering the whole surface, with no error — measured UV
  coverage of 1.02 to 1.57 in a unit square, where anything above 1.0 means charts sitting on top of
  each other and a texture that smears. Every layout is now measured before it is accepted, and the
  patches are halved and retried if it overlaps or if the texture density came out uneven.
- **Unwrapping was extremely slow on dense models.** A 159,048-face surface took 121 seconds; it now
  takes 3.9, with *lower* distortion. A 327,680-face model went from crashing to 2.4 seconds.
- **Re-unwrapping a textured model silently scrambled it.** A new UV layout invalidates any texture
  painted for the old one. Unwrap now asks first, and clears maps that no longer apply.
- **A stray image file beat the texture the model actually declared.** Companion-file scanning ran
  before the model's own material, so a leftover `*_diffuse.png` in the folder overrode the embedded
  base colour. The material a file declares is now the authority; the folder scan fills the gaps.
- **`metallicRoughness` texture files were ignored.** The standard glTF export name — what Sketchfab,
  Blender and most exporters write — matched no pattern, so metallic and roughness were silently
  lost, and with no separate base-colour file the packed map could be loaded as albedo. It is now
  recognised and unpacked, alongside `metalRough`, `RMA` and `occlusionRoughnessMetallic`.
- **`dilation_pixels=` was ignored by the seam dilator.** Every caller silently got the 16 px default,
  including the ComfyUI node and the project's own test.
- **Every successful UV unwrap reported an error.** A button reference was scoped to the wrong
  function, so the handler threw before its success message.
- **The UV island count was the mesh body count.** A sphere unwrapped into 7 charts reported "1
  island". The real chart count now comes from xatlas, along with the atlas size.
- **A recovered crash session inherited the previous model's textures.** `recover()` did not clear the
  material set the way `load()` does.
- **Texture failures were swallowed silently** by a bare `except: pass` around the texture state.
- Exporting a texture pack wrote the same UV guide image twice, under two names.

- **Every mesh operation re-sent the whole texture set.** A repair, a reduce, an undo or even a
  rotate re-encoded all six maps to base64 and shipped them to the interface: 375 ms and 12.8 MB
  each time, for data that had not changed. Results now carry a version number and a list of
  channels; the maps are fetched only when that version moves. Ten operations in a row went from
  about 3.7 seconds of encoding to 12 milliseconds.
- **The MCP server returned megabytes of texture data to assistants.** `_strip()` dropped the binary
  mesh preview but not the textures, so a single `repair` on a textured model returned 9.27 MB —
  enough to swamp a context window. Tool results are now under a kilobyte.
- **Texture seams could show dark fringes.** The gutters between UV islands were left empty, so GPU
  filtering and mipmapping blended that emptiness into the edge of every island. Exported texture
  packs, baked GLBs and textured OBJ/glTF exports now have their gutters padded. The maps held in
  memory are untouched — padding is applied to copies on the way out.
- **Generated normal maps had a false ridge along all four borders.** The gradient pass assumed the
  source image tiled, so it read the opposite edge as if it were adjacent. It no longer does unless
  you say the image tiles.

- **Loading a dense FBX took minutes.** A 225 MB, 5-million-triangle model effectively hung: it
  loaded in about 27 seconds, of which ten minutes and counting were spent re-projecting texture
  coordinates that were already correct. Separating a multi-body model renumbers its faces, and the
  commit step responded by projecting twenty million points onto a five-million-face surface to
  recover a mapping it could have simply permuted. It now carries the permutation through and
  reindexes, which is exact and instant.
- **Row-wise de-duplication was the next bottleneck.** Deciding which edges are open, which faces
  are duplicates and which corners can be welded all reduce to "which rows of this array are equal",
  and `numpy.unique(axis=0)` answers that by sorting each row as a block of bytes. Packing each row
  into a single 64-bit key instead is exact and several times quicker: building the viewport vertex
  buffer went from 5.6 s to 1.8 s, and the diagnostics pass from 10.1 s to 7 s on the same model.
  Where the values will not fit in a key, the original path still runs.
- **The loader paid for a summary the application discards.** `load_model()` measured
  watertightness, volume and area on every load — three seconds on a dense model — and the desktop
  app threw the result away before running the full diagnostics. It is now optional.
- The FBX index buffers are read with `np.fromiter`, and duplicate faces found with packed keys
  rather than trimesh's row hash, together about a second and a half on a five-million-face model.
- Hole counting no longer re-derives the open edges the diagnostics pass had already found.
- **A failed file dialog was reported as if a file had been chosen.** The dialog methods return a
  path, and the interface treats the return value as one. Wrapping them in the error decorator made
  them answer with `{success: false, ...}` on failure — a truthy object that was then handed to the
  loader as a path, so a dialog that could not open surfaced as *"path.split is not a function"*.
  They now return an empty string in every failure case and log the real reason.
- **Several viewport controls stopped repainting.** Moving to on-demand rendering saved the CPU an
  idle 60 fps, but six methods that change the scene never asked for a frame: the light sliders, the
  diagnostic locator, the piece highlighting, the displacement slider, the environment and the
  preset views all appeared dead until the camera happened to move. Every scene-mutating method now
  invalidates, and a test enumerates them so the next one cannot be forgotten.
- **FBX polygons are triangulated with array arithmetic** rather than a Python loop over every
  corner, and are covered by tests against a reference implementation for triangles, quads, n-gons
  and meshes that mix them.
- The UV worker no longer asks xatlas to pack an empty set, which wrote a complaint to a native
  stderr the parent could not catch.

- **Textures from Meshy and Hi3D models loaded flat.** Those generators embed a 2x2 placeholder in
  the FBX material and ship the real 2048px maps as files beside it. Treating the material as
  authoritative — correct in general — meant the placeholder won, so a fully textured model appeared
  untextured. Placeholders are now ignored in favour of the real artwork. On a Meshy export that is
  the difference between one 2x2 image and four 2048px maps.
- **Smart retopology could take ten minutes and then close the application.** QuadriFlow can abort
  inside Eigen on a mesh it dislikes, which in-process ends Meshwright mid-operation. It now runs in
  a child process with a two-minute budget per piece; a crash or a stall falls through to uniform
  remeshing, and the log says which engine did the work.
- **Preparing a dense piece for smart retopology took 73 seconds.** Decimating five million faces to
  two hundred thousand went straight to MeshLab's topology-preserving collapse. Doing it in two
  steps — fast-simplification for the bulk, MeshFix to sew the surface closed — takes 27 seconds end
  to end instead of 82, and the finished retopology deviated 4.95% from the original rather than
  9.68%. MeshLab still runs when that chain cannot produce a manifold.
- **The 2D UV view was unreadable on a dense model.** It drew one triangle in every few hundred,
  which is a field of specks rather than a wireframe. It now draws the island outlines — the seams
  and open edges that define the layout — and says that is what it is doing.
- **The diagnostics crashed on a model with duplicate faces.** A refactor kept the totals and dropped
  the masks the issue locations are built from, so clicking "Duplicate faces" raised a NameError.

### Added
- **Viewport detail.** A model too dense to draw quickly is shown simplified, with a slider in the
  viewport toolbar to raise it to the full mesh and a message saying what is on screen. It is the
  picture only: diagnostics, repair, reduce, retopology and export always use every triangle. A
  five-million-triangle model's viewport payload drops from 148 MB to 27 MB.
- **Image tiles seamlessly** option for PBR generation: blends the source edges and wraps the
  surface gradients, for repeating materials.
- **Displace** slider and a **Height** viewport channel, so the generated height map can be seen and
  used instead of only exported.
- Unwrapping now reports what it actually produced — islands, seam edges, atlas size and whether the
  texture density came out even — instead of a chart count that was really the mesh's body count.
- **New** button in the top bar (<kbd>Ctrl</kbd>+<kbd>N</kbd>) and <kbd>Delete</kbd> both close the
  model and empty the workspace — mesh, undo history, autosave snapshot and textures — after a
  confirmation. <kbd>Delete</kbd> still removes ticked pieces when the Separate pieces panel has a
  selection.
- OBJ, GLB and glTF exports now carry the model's UV coordinates and PBR maps. STL, PLY, OFF and 3MF
  cannot store them and are unchanged.
- `rtree` is now a required package. It bundles libspatialindex, installs as a pure wheel on every
  supported Python, and provides the AABB queries behind the exact UV transfer. Without it the
  transfer falls back to a slower KD-tree candidate search rather than failing.

## [1.2.0] — 2026-08-26

### Fixed
- **The installer said Python was missing when it was not — and stopped when it really was.**
  Windows ships a placeholder `python.exe` (App Execution Alias) that prints *"Python was not
  found; run without arguments to install from the Microsoft Store"* and exits with an error.
  `install.ps1` ran `python --version` inside a `try/catch`, which never sees a native exit code,
  so it printed `[OK] Detected`, kept going, and failed one step later with
  `[ERROR] Could not create the virtual environment.` The installer now *probes* candidates by
  running them — the py launcher, every `python`/`python3` on `PATH`, the registry entries written
  by the python.org installer, and the usual install folders including conda and uv — keeps only
  real 64-bit interpreters that have `venv` and `ensurepip`, prefers the tested 3.10–3.13 series,
  and when nothing is usable explains how to install Python and how to switch the placeholder off.
- **One missing wheel aborted the whole installation.** `pip install -r requirements.txt` is
  all-or-nothing, and several dependencies are compiled extensions with no build for the newest
  Python for months after its release (`ufbx` has none for 3.13/3.14 and falls back to a source
  build that needs Visual C++). Required packages now live in `requirements.txt` — all of them
  pure-wheel installs — and the mesh engines moved to `requirements-optional.txt`, which the
  installer installs **one at a time**, keeping going and reporting what it skipped.
- **Exported models could slice as a thin, hollow shell.** An open surface has no inside, so a
  slicer prints it as a single-wall shell with no infill. Export now measures the result and warns
  on screen when the file is not a closed solid, when it encloses no volume, or when its walls
  average under 1.2 mm; inside-out meshes are turned the right way out on the way to the file.

### Added
- **Load demo model.** Meshwright ships no 3D models and several people expected one to come with
  it. The empty viewport now says so and offers a built-in test object, built in memory: a sphere
  with a hole, a patch of flipped faces and a loose second piece — Repair takes it to watertight.
- `install.bat -Check` reports every Python on the machine and every engine in the environment
  without changing anything, and every run writes `install-log.txt` for support.
- `install.bat -Recreate`, `-NoOptional` and `-Python <path>`, plus `scripts/check_install.py`,
  which prints exactly which engines a copy has.
- The installer refuses to run from inside a downloaded ZIP or a folder it cannot write to, and
  explains what to do instead; `start.bat` says to run `install.bat` first instead of falling back
  to a system Python that may not exist.

### Changed
- Export offers STL, OBJ, PLY, OFF, GLB, glTF and 3MF, and names files
  `<original>-GS-<timestamp>-fixed.<ext>`.
- `numpy` upper bound raised to `<2.6`.

## [1.1.2] — 2026-08-21

### Fixed
- **The installer could break other Python projects.** `requirements.txt` used unbounded version
  ranges, so `pip install -r requirements.txt` on a shared interpreter upgraded numpy to the latest
  release and broke unrelated packages that pin it (numba, pyarrow and friends). Every dependency
  now has an upper bound (`numpy>=1.26,<2.4`), and the installer creates an isolated `.venv` inside
  the project folder by default. `start.bat`, `start.ps1` and the new `run-mcp.bat` use it
  automatically; `install.ps1 -Global` opts back into a system-wide install.

### Added
- `requirements-dev.txt` for the test and lint tooling.
- `run-mcp.bat` to start the MCP server from the project environment.

## [1.1.1] — 2026-08-21

### Fixed
- **Sliver repair tore the mesh.** Edge collapses and flips were applied without checking whether
  they were topologically valid, so fixing slivers on a closed model could open holes and create
  non-manifold edges — which the safety guard then (correctly) rejected, making the button look
  broken. Collapses now require the **link condition** and flips require that the replacement edge
  does not already exist; a pass that would still worsen the topology is discarded. On a
  1,994,490-face miniature: 186 slivers → 3, mesh stays watertight, and the 3 it refuses to touch
  are reported instead of silently forced.
- Shell colours were washed out by the key light on large models; the palette is now deeper and the
  material less reflective.

### Added
- **Progress notifications.** Long operations report the face count and an estimated duration before
  starting, show a live elapsed timer and progress bar in the bottom-right of the workspace, and
  report the real elapsed time when they finish. Toasts can be dismissed or left to fade.
- Result toasts for repair, sliver fixing and reduction, including surface deviation, and a toast
  when a change is rejected by the safety guard.

## [1.1.0] — 2026-08-21

### Added
- **Smart retopology** — QuadriFlow (BSD-3) rebuilds a model as clean, curvature-aligned quads;
  three reduction engines (smart retopo / decimate / uniform) with an absolute face target,
  low-poly presets and measured surface deviation in millimetres.
- **MCP server** (`mcp_server.py`) exposing 15 tools over stdio, plus a `meshwright://report` resource.
- **Service layer** (`engine.service.MeshService`) shared by the desktop app, MCP and Python callers.
- **Undo / redo** with numbered states, plus a full keyboard shortcut set and an in-app help dialog.
- **Crash recovery** — every accepted state is snapshotted in a background thread and offered for
  recovery if the app does not exit cleanly.
- **Safety guard** — a change that would add critical problems or discard most of the geometry is
  rejected, keeping the previous state, with an explicit "apply anyway".
- **Issue locations** — click any diagnostic to fly to it; exact spots plus a general-area marker.
- **About panel** listing every engine with its installed version.
- **Sliver repair** — needle triangles collapsed, cap triangles flipped into larger neighbours.
- **JSON report** export of diagnostics and the full operation history.
- MeshFix added to the repair pipeline; scikit-image enables the voxel remesh fallback.
- Input validation layer (`engine.validation`) applied to every public call.

### Fixed
- Retopology could return a mesh with new holes and split shells on topologically complex models;
  every piece is now verified and repaired with MeshFix, or falls back to another engine.
- Simplify was silently rejected by the safety guard on already-imperfect meshes and appeared to do nothing.
- Issue highlights were offset from the model, and did not follow the model after rotation.
- Rotation re-uploaded the whole mesh on every step; it is now instant with a background sync.
- Drag-and-drop reported "full path unavailable"; paths now come from the Python-side drop handler.
- The Open dialog crashed on an invalid file-type filter label.
- A Unicode console write could abort an operation on Windows.
- A failing logger could abort a mesh operation.
- Voxel remesh silently did nothing when scikit-image was missing.

### Changed
- Rebuilt UI: minimal dark theme, resizable panel, activity console, XYZ compass, preset views,
  rotation gizmo, shell colour-coding that no longer clashes with selection or highlight colours.
- trimesh upgraded to 5.0; Three.js vendored locally so the app works offline.
- Renamed to **Meshwright** with a new application icon and Geekatplay Studio branding.

## [1.0.0]
- Initial release: load, analyse, repair, decimate and export STL.
