# Architecture

```
                 ┌──────────────┐        ┌────────────────┐
                 │  app.py      │        │ mcp_server.py  │
                 │  pywebview   │        │ MCP over stdio │
                 └──────┬───────┘        └────────┬───────┘
                        │  thin adapters, no logic │
                        └────────────┬─────────────┘
                                     ▼
                        ┌────────────────────────┐
                        │ engine/service.py      │  validation · state · guard
                        │ MeshService            │  autosave · history
                        └───────────┬────────────┘
                                    ▼
   ┌──────────────┬────────────┬────────────┬─────────────┬──────────────┐
   │ model_loader │ mesh_      │ mesh_      │ mesh_       │ mesh_exporter│
   │              │ analysis   │ repair     │ retopo /    │              │
   │              │            │ cleanup    │ reducer     │              │
   └──────────────┴────────────┴────────────┴─────────────┴──────────────┘
        trimesh · MeshLab · MeshFix · Manifold3D · QuadriFlow · scikit-image
```

## Principles

**One engine, many front ends.** `engine/` never imports a UI framework. Adding a capability to
`MeshService` gives it to the desktop app, the MCP server and Python callers at once.

**Immutable states.** Every accepted result becomes a numbered `_State` holding its mesh, its
analysis and its shells. Undo and redo move between states; nothing else ever moves backwards.
This is why "it reverted my work" cannot happen — a revert is an explicit, undoable operation.

**Measure, do not predict.** Repair stages record a fix only when the diagnostics actually change,
and the report is a fresh analysis of the returned mesh. Reduction reports real sampled deviation.

**Refuse to make things worse.** `_commit` compares before and after; a result with more critical
issues, or with 95 % of the geometry gone, is rejected and the previous state kept. The caller can
pass `force=True` deliberately.

**Crash safety is cheap.** Snapshots are written on a single background thread as compressed `.npz`
next to a journal, so a 2 M-face save never blocks the UI. A clean exit deletes them; an unclean one
leaves them for recovery.

**Say what is happening.** Anything that can take more than a moment opens a job: the service emits
a `start` event carrying the face count and an estimated duration, then a `done` event with the real
elapsed time. The desktop adapter turns those into the bottom-right notifications; the MCP adapter
folds them into the `log` array of the tool result. Estimates come from `estimate_seconds()`, which
is a measured cost-per-million-faces table — deliberately rough, and only ever used to set
expectations, never to decide anything.

## Modules

| File | Responsibility |
|---|---|
| `engine/service.py` | The API. Validation, state stack, safety guard, autosave, history, previews, progress events |
| `engine/validation.py` | Path, number, choice, index-list and rotation-matrix sanitising |
| `engine/session_store.py` | Background snapshots, journal, crash-recovery discovery |
| `engine/model_loader.py` | Format loading with trimesh → ufbx → MeshLab fallbacks |
| `engine/mesh_analysis.py` | The 12 checks, issue locations, readiness score, before/after diffs |
| `engine/mesh_repair.py` | Six-stage repair pipeline, repeated until stable, then verified |
| `engine/mesh_cleanup.py` | Sliver removal: needle collapse and cap flipping |
| `engine/mesh_retopo.py` | QuadriFlow / isotropic / quadric reduction, per piece, verified |
| `engine/mesh_reducer.py` | Ratio-based quadric decimation with topology-safe fallback |
| `engine/mesh_exporter.py` | Unit scaling, winding fix, build-plate alignment, solidity check, seven output formats |
| `engine/preview.py` | Binary viewport payload; splits UV seams for the GPU and nowhere else |
| `engine/indexing.py` | Packed-key row de-duplication behind edge, face and corner grouping |
| `engine/texture_service.py` | Unwrap, PBR generation, texture pack round-trip, GLB bake (mixed into `MeshService`) |
| `engine/texture/uv_channel.py` | The per-corner UV channel: charts, transfer, GPU/glTF split |
| `engine/texture/uv_unwrapper.py` | Unwrapping: patch, run xatlas out of process, validate, retry |
| `engine/texture/uv_patches.py` | Splitting a mesh into patches xatlas can handle; layout validation |
| `engine/texture/companion_detector.py` | Embedded and companion texture discovery, packed-map unpacking |
| `engine/texture/material_manager.py` | The active PBR map set, Photoshop round-trip, GLB baking |
| `engine/texture/pbr_generator.py` | Normal / roughness / metallic / AO / height derived from one image |
| `engine/texture/seam_fixer.py` | UV mask rasterisation, gutter dilation, tileable edge blending |
| `engine/stl_exporter.py` | Thin STL-only wrapper kept for older scripts |
| `engine/demo_model.py` | The built-in broken test object offered on an empty viewport |
| `ui/js/viewer.js` | Three.js viewport: geometry, shading, highlights, compass, gizmo |
| `ui/js/app.js` | Panels, console, shortcuts, state UI, dialogs |

## Data flow for one operation

1. Adapter calls e.g. `svc.repair(strict_watertight=True)`.
2. Service validates arguments and takes the lock.
3. The algorithm runs, logging each step (the log reaches the UI console live).
4. `_commit` splits shells, analyses the exact mesh it will keep, and compares with the previous state.
5. Rejected → previous state kept, reason returned. Accepted → new state, snapshot queued, history appended.
6. The result carries the analysis, stats, shells and a compact binary preview.

## Texture coordinates live beside the mesh, not inside it

A UV is a property of a *face corner*, not of a vertex. Two faces meeting along a seam
share a position but need different UVs there, so the usual per-vertex storage forces
that vertex to be duplicated.

That duplication is harmless to a renderer and poisonous to a mesh analyser. A split
vertex breaks the edge that joined two triangles, so a watertight solid with a normal
UV layout reads as a pile of disconnected shells with hundreds of open boundary edges —
and the repair pipeline dutifully closes holes that were never there. Measured on a
clean textured sphere before this was fixed: load reported *Repair required, score 59,
9 shells*; decimating to 25% pushed it to 201 shells; repair then inflated 1,279 faces
into 168,811.

So Meshwright keeps UVs in a separate `(F, 3, 2)` array — one UV per corner of every
face — held on `_State` next to the mesh:

```
_State
├── mesh      welded trimesh: real topology, what analysis and repair see
└── uv        (F, 3, 2) float32, or None
```

**Nothing in the geometry pipeline knows about it.** `mesh_repair`, `mesh_reducer`,
`mesh_cleanup` and `mesh_retopo` take a mesh and return a mesh. `_commit` then carries
the UV channel from the old state onto the new geometry in one place, via
`uv_channel.transfer()`.

`transfer()` projects every corner of the new mesh to its closest point on the *source
surface* — an exact AABB query through `trimesh.proximity` (Rtree), not a
nearest-centroid guess — and reads the UV interpolated there. A corner whose closest
source face sits in a different UV chart from the rest of its own face would drag a
texel from the far side of the atlas, so those are re-evaluated against the chart the
face actually belongs to. Chart membership comes from `chart_labels()`: a connected-
components pass over face adjacency where two neighbours join only if they agree on the
UV at both ends of their shared edge.

Measured drift on a seam-free test surface with an exactly known UV map, in texels on a
2048 px map (the previous scheme in brackets):

| Operation | Mean | Worst |
|---|---|---|
| Decimate to 50% | 0.01 (9.9) | 0.04 (82) |
| Decimate to 10% | 0.03 (29.3) | 0.12 (175) |
| Decimate to 2% | 0.14 (75.9) | 0.46 (394) |
| Repair | 3.33 (12.9) | 23.6 (775) |

Vertices are split into the per-vertex form at exactly two boundaries, and both are
edges of the system rather than parts of it:

- `preview.py` → the GPU vertex buffer. Open-edge overlays are still found on the welded
  mesh and re-pointed at the split buffer through `split_for_gpu`'s `vertex_map`.
- `mesh_exporter.py` / `material_manager.py` → OBJ, glTF and GLB. STL, PLY, OFF and 3MF
  carry no UVs and are written from the welded mesh unchanged.

`unwrap_corner_uv()` follows the same rule: xatlas returns a vertex-split mesh, and its
split is unpacked straight back into a corner array. Unwrapping therefore does not touch
the geometry at all — a print-ready model stays *Print ready, score 100, watertight*
and simply gains texture coordinates.

## Working around xatlas

xatlas is the right unwrapper and there is no pip-installable alternative that
segments and packs charts. Version 0.0.11 also has two failure modes that ordinary
3D-printing models walk straight into. Both were found by measurement here, and both
are worked around by controlling what xatlas is given.

**It crashes on closed surfaces.** On a mesh where every edge has an opposite —
precisely what a watertight, print-ready model is — `createBoundaries` never marks a
boundary vertex, and the convex-hull pass then reads uninitialised data (upstream
[jpcy/xatlas#146](https://github.com/jpcy/xatlas/issues/146)). An 81,920-face
icosphere takes the process down with an access violation. Remove one triangle so the
surface has a boundary and the same mesh unwraps in 0.1 s. Small closed meshes happen
to survive, but they are relying on the same uninitialised read.

**It gives up quietly above about 40,000 faces.** Past that it stops segmenting and
returns one chart covering the whole surface, with no error. Measured total UV area of
1.02–1.57 inside a unit square, where anything above 1.0 is overlap, and a texture
painted on it smears.

So `uv_patches.split_into_patches()` breaks the mesh into groups that are each **open**
and **under a face cap**, and they all go into one atlas so they still pack into a
single texture. A component that is already open and small enough is passed through
whole, so a model xatlas could have handled gains no extra seams.

Then the result is checked rather than trusted:

- `validate()` rejects non-finite values, UVs outside the atlas, and total UV coverage
  above 0.95 — the signature of overlapping charts.
- `texel_spread()` measures how unevenly the texture is stretched, as the ratio between
  the 95th and 5th percentile of per-triangle texel density. 1.0 is perfectly even.

If the layout is invalid, or merely uneven, the patch cap is halved and it is tried
again — up to three times, keeping the most even valid result. This matters because
xatlas's own segmentation is erratic at size: on an 81,920-face sphere the texel spread
was 18.1× at 20,480 faces per patch, 9.1× at 10,240 and 2.2× at 5,120, while a
180,000-face torus was already 1.2× at 22,500. No single constant serves both, so the
system measures instead of guessing.

Measured after this change — every one of these previously crashed or took minutes:

| Model | Faces | Time | Patches | Islands | Texel spread |
|---|---|---|---|---|---|
| Icosphere | 20,480 | 1.1 s | 2 | 10 | 1.21 |
| Icosphere | 81,920 | 1.1 s | 16 | 16 | 2.20 |
| Icosphere | 327,680 | 2.4 s | 32 | 32 | 1.40 |
| Torus | 180,000 | 2.5 s | 24 | 60 | 1.15 |
| Open surface | 159,048 | 3.9 s | 24 | 24 | 1.26 |

That last row was **121 seconds** when handed to xatlas whole, and with *higher*
distortion (0.79–1.24 against 0.87–1.10).

`_xatlas_worker.py` still runs the whole thing in a child process. The known failure
modes are designed around now, but xatlas is native code and an unknown crash in it
would close the window without a word; one process spawn is cheap insurance. The worker
imports nothing from Meshwright — numpy and xatlas only — so it can be launched by file
path with no `sys.path` setup.

## Texture data is fetched, not pushed

Encoding a six-channel 2048px PBR set to base64 costs about 375 ms and 12.8 MB.
That was being paid on *every* operation, because the result of a repair, a reduce,
an undo or even a rotate carried the maps with it. The MCP server was worse: its
`_strip()` dropped the binary preview but not the textures, so one `repair` on a
textured model returned 9.27 MB to an assistant reading through a context window.

So results carry a summary and nothing else:

```
"textures": { "has_textures": true, "has_uv": true,
              "texture_version": 3, "channels": ["albedo", "normal", …] }
```

`MaterialManager.version` is bumped whenever a channel actually changes. The UI
holds the last version it fetched and calls `get_texture_maps()` only when the two
differ; the UV wireframe comes from `get_uv_layout()` when the 2D view opens, keyed
on `state_id`. Measured on the same six-channel set:

| | Before | After |
|---|---|---|
| One operation result | 375 ms · 12.8 MB | **1.4 ms · 0.17 kB** |
| One MCP tool result | 9.27 MB | **under 1 kB** |
| Ten operations in a row | ~3,700 ms | **12 ms** |

The maps still cost 375 ms to encode — but once, when they change, instead of on
every undo.

## Seam gutters

The unwrapper splits models into patches, so there are more seams than a
single-chart layout would have. A texture only covers the triangles of the layout;
the gutters between charts are empty, and GPU filtering and mipmapping blend that
emptiness in as dark fringes along every seam.

`seam_fixer.dilate_map_set()` bleeds the surface colours outward before anything
leaves the app — the exported texture pack and the baked GLB both go through it, and
so does any OBJ/glTF export that carries a material. The UV mask is rasterised once
per texture size and shared across all six channels; it used to be rebuilt per
channel. The maps held in memory are never modified: padding produces copies, so
what the user loaded is what they still have.

## Why loading a dense model is fast

Two things dominate a large load, and both were once far worse than they needed to be.

**Reindex, don't re-project.** Separating a multi-body model renumbers its faces, so
`_prepare()` returns the permutation it applied and `_commit()` reindexes the UV
channel with it. Before, that permutation was thrown away and the channel was rebuilt
by projecting every corner onto the old surface — twenty million closest-point queries
against five million faces, which on the test model ran for over ten minutes to
recover a mapping that was already correct. Re-projection is now reserved for the case
that genuinely needs it: geometry that actually changed.

**Group rows by packing, not by sorting bytes.** Which edges are open, which faces are
duplicates, which corners can be welded — all the same question, and
`numpy.unique(axis=0)` answers it by viewing each row as an opaque block and sorting
those. `engine/indexing.py` packs each row into one 64-bit integer instead, shifting
each column down by its own minimum so only its range costs bits. The result is
identical; where the values will not fit, the original path still runs.

Measured on a 225 MB FBX with 4,984,030 triangles:

| Stage | Was | Now |
|---|---|---|
| Whole load | did not finish | **27 s** |
| Viewport vertex buffer | 5.6 s | **1.8 s** |
| Diagnostics pass | 10.1 s | **7 s** |
| FBX read and weld | 8.4 s | **6 s** |

What remains is honest work: the diagnostics themselves, shell separation, and
serialising the viewport payload — which for this model is 147 MB of base64 (60 MB of
indices, 31 MB of positions, 21 MB of UVs). That payload, not the backend, is the next
thing worth attacking; it wants a viewport level of detail rather than a faster encoder.

## Viewport level of detail

A five-million-triangle model is 147 MB of base64 by the time it reaches the page. So
`preview.py` decimates above a face cap — the *display* only. The mesh, the
diagnostics, every operation and every export use all of it.

Two details make that honest rather than a lie the user has to discover:

- **Decimation is per shell.** The viewport colours separate pieces by walking the
  face array in shell order, so reducing the whole mesh at once would shuffle that
  correspondence. Each shell is reduced on its own and the new per-piece counts go
  back with the payload.
- **The open-edge overlay is dropped while reduced**, because it would be marking
  edges of the simplified copy. The result says so, and the interface says so.

UVs come across through a nearest-vertex lookup rather than an exact replay:
`fast_simplification`'s `replay_simplification` gives an exact mapping but measured
9.6 seconds against 0.7 for a KD-tree query, on a decimation that itself took 4. For a
mesh that exists to be looked at, that trade is the right way round.

## Working around QuadriFlow

Like xatlas, QuadriFlow is native code that can abort rather than fail — an Eigen
assertion, which in-process ends the application mid-retopology. It runs in a child
process (`engine/_quadriflow_worker.py`) with a two-minute budget per piece, and a
crash or a timeout falls through to the engines that are predictable.

Its cost genuinely is unpredictable: on one model, from the same source at different
decimation levels, 2 s at 20,000 faces, over four minutes at 32,000 and 72,000, then
7.5 s at 189,000. There is no input size that makes it reliable, which is why the
answer is a bounded wait and a good fallback rather than tuning.

## The preview payload

The viewport never receives an STL. `MeshService.preview` sends raw `Float32` vertices, `Uint32`
faces and `Uint32` boundary-edge pairs, base64-encoded. The browser builds a `BufferGeometry`
directly. A 1.3 M-face model reaches the viewport in a few hundred milliseconds; the previous
STL-and-reparse route took minutes.

Rotation does not send geometry at all: the viewport rotates its own group instantly, the backend
bakes the same matrix, and both sides re-derive the offset from the returned bounds.

## Coordinate spaces

Models are Z-up; the viewport is Y-up. Geometry is rotated −90° about X on load and recentred on
the build plate. Issue locations arrive in model space and are converted the same way, so a
highlight lands exactly on the triangle it describes (verified to 0.0000 mm in tests).

## Testing

The texture layer is verified against ground truth rather than against itself: a bumpy
plane with a UV map that is an exact linear function of position, so the correct answer
for any point is known and any drift is the transfer's own error. Tests assert a budget
in texels, that decimation leaves the mesh welded, that unwrapping does not touch the
geometry, and that `split_for_gpu` round-trips every triangle and UV.

The rest of the suite covers the analysis checks, each repair stage, sliver geometry (including "must never open
a watertight mesh"), retopology quality ("must not return worse topology than the source"), the
safety guard, undo/redo/revert, validation, crash recovery and the adapters.

```bash
.venv\Scripts\python -m pip install -r requirements-dev.txt
npm test
```
