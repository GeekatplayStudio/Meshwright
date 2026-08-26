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

54 tests cover the analysis checks, each repair stage, sliver geometry (including "must never open
a watertight mesh"), retopology quality ("must not return worse topology than the source"), the
safety guard, undo/redo/revert, validation, crash recovery and the adapters.

```bash
.venv\Scripts\python -m pip install -r requirements-dev.txt
npm test
```
