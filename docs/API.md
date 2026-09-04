# Python API

Everything the desktop app and the MCP server can do is a method on `MeshService`.

```python
from engine.service import MeshService

svc = MeshService()                    # autosave=True by default
svc.load("miniature.stl")
print(svc.current.analysis["verdict"])  # 'Repair required'

svc.repair()                            # measured, verified, undoable
svc.fix_slivers(min_angle_deg=1.0)
svc.retopo(20000, method="quadriflow")
result = svc.export_model("miniature_print_ready.stl", "stl", scale_unit="mm", align_origin=True)
for warning in result["result"]["warnings"]:
    print(warning)                      # e.g. 'not a closed solid - 412 edges are open'

svc.export_report("miniature_report.json")
svc.close()                             # clears the crash-recovery snapshots
```

## Constructor

```python
MeshService(log=None, autosave=True, progress=None)
```

- `log(message, level)` — called for every step; `level` is `info` / `ok` / `warn` / `error`.
  A logger that raises can never abort an operation.
- `autosave` — write a background snapshot of every accepted state for crash recovery.
- `progress(**event)` — called when a long operation starts and finishes:

  ```python
  {"state": "start", "operation": "retopo", "label": "Smart retopology to 50,000 faces",
   "faces": 1994490, "eta": 92.7, "eta_text": "about 1–3 minutes"}
  {"state": "done",  "operation": "retopo", "label": "...", "elapsed": 88.4}
  ```

  Use it to drive a progress bar or a status line. `engine.service.estimate_seconds(operation, faces)`
  and `describe_duration(seconds)` are available on their own if you want the estimate without
  running anything.

## Operations

| Method | Returns |
|---|---|
| `load(path)` | Full result: analysis, stats, shells, preview, state id |
| `load_demo()` | The same, for the built-in test object — no file needed |
| `analyze()` | Fresh diagnostics for the current mesh |
| `repair(strict_watertight=True, force=False)` | Result + `report` with `fixes`, `changes`, `passes` |
| `fix_slivers(min_angle_deg=1.0, force=False)` | Result + `info` (`before`, `after`, `collapsed`, `flipped`, `skipped`) |
| `simplify(keep_fraction=0.5, force=False)` | Result + `info` including `deviation` |
| `retopo(target_faces, method="quadriflow", preserve_sharp=True, adaptive=True)` | Result + `info` |
| `remove_shells(indices)` | Result for the remaining geometry |
| `rotate(matrix)` | `stats`, `centre`, `bounds` — no geometry payload |
| `undo()` / `redo()` / `revert()` | Result for the state you land on |
| `state_list()` | `[{id, operation, verdict, score, faces}, …]` |
| `export_model(path, export_format="stl", scale_unit="mm", align_origin=True)` | `{"result": {...}}` — format is stl, obj, ply, off, glb, gltf or 3mf |
| `export_stl(path, scale_unit="mm", align_origin=True)` | The same, fixed to STL |
| `report()` / `export_report(path)` | The full JSON report |

### What an export returns

`result["result"]` describes the file that was written, and answers the question a slicer cannot ask:

| Key | |
|---|---|
| `is_solid` | `True` only when the mesh is watertight **and** encloses a volume |
| `is_watertight`, `volume_cm3`, `avg_wall_mm` | The measurements behind that verdict |
| `warnings` | Plain-English problems: not a closed solid, no volume, walls too thin to fill |
| `format`, `filename`, `file_size_mb`, `face_count`, `vertex_count` | The file itself |
| `dimensions_mm`, `bounds_min`, `bounds_max` | After unit scaling and build-plate alignment |

An open surface is sliced as a single-wall shell with no infill, so treat a non-empty `warnings`
list as a failed print waiting to happen. Inside-out meshes are corrected during export.

## Properties

- `svc.mesh` — the current `trimesh.Trimesh`
- `svc.current` — the current state (`id`, `mesh`, `analysis`, `operation`, `shells`)
- `svc.shells` — list of `trimesh.Trimesh` when the model has several pieces
- `svc.original` — a pristine copy of the loaded file
- `svc.history` — journal of accepted operations

## Errors

```python
from engine.service import ServiceError        # no model loaded, impossible request
from engine.validation import ValidationError  # bad path, bad number, bad matrix
```

Both are exceptions here. The desktop and MCP adapters convert them to
`{"success": false, "error": "..."}`.

## The safety guard

Mutating calls run through `_commit`, which refuses a result that would increase the number of
**critical** issues or discard more than 95 % of the geometry:

```python
res = svc.repair()
if not res["success"] and res.get("rejected"):
    print(res["reason"])            # 'critical problems would increase from 0 to 2'
    res = svc.repair(force=True)    # apply anyway, deliberately
```

`simplify` and `retopo` are exempt from the geometry-loss rule — dropping faces is the point — but
they still verify the result and log a warning if new problems appear.

`fix_slivers` reports `skipped`: slivers it refused to remove because the collapse or flip would
have torn the surface. A few stubborn slivers are harmless; a hole is not.

## Crash recovery

```python
for s in MeshService.recoverable_sessions():
    print(s["session"], s["source_file"], s["last"])
svc.recover(session_id)             # restores the last good state
MeshService.discard_session(session_id)
```

## Lower-level modules

If you want a single algorithm without the state machine:

```python
from engine.mesh_analysis import analyze_mesh, compare_analyses
from engine.mesh_repair   import repair_mesh
from engine.mesh_cleanup  import fix_slivers
from engine.mesh_retopo   import retopologize, deviation
from engine.mesh_reducer  import reduce_mesh
from engine.mesh_exporter import export_to_format, solidity_report
from engine.model_loader  import load_model
```

Each is pure: mesh in, mesh + report out. No global state, no UI.


## Textures and the viewport

```python
svc.load("dragon.fbx")          # UVs and any PBR maps come with the model
svc.corner_uv                   # (F, 3, 2) per-face-corner UVs, or None
svc.unwrap_uvs()                # build a layout; refuses silently replacing an existing one
svc.unwrap_uvs(force=True)      # ... unless you say so
svc.get_texture_state()         # what exists: channels, version, has_uv — no pixels
svc.get_texture_maps()          # the base64 maps, only worth calling when the version moved
svc.get_uv_layout()             # UV-space edges for a 2D view
svc.export_texture_pack(folder) # every channel plus a UV guide, gutters padded
svc.bake_and_export_glb(path)   # one self-contained file
```

Texture coordinates live in a per-face-corner array beside the mesh, never inside it, so the
geometry stays welded and the diagnostics measure the real thing. `_commit()` carries the channel
onto whatever an operation returns — by reindexing when the faces were only permuted, and by
re-projecting onto the old surface when the geometry genuinely changed.

```python
svc.preview_max_faces = 900_000     # when the viewport starts drawing a simplified copy
svc.set_preview_detail(0.5)         # draw half the model
svc.set_preview_detail(1.0)         # draw all of it
svc.set_preview_detail(None)        # let Meshwright choose
```

Viewport detail changes what is drawn and nothing else: the mesh, every measurement and every
export always use all of it.

```python
svc.clear()                     # empty the workspace: mesh, history, snapshot and textures
```
