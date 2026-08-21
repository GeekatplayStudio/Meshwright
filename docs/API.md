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
svc.export_stl("miniature_print_ready.stl", scale_unit="mm", align_origin=True)
svc.export_report("miniature_report.json")
svc.close()                             # clears the crash-recovery snapshots
```

## Constructor

```python
MeshService(log=None, autosave=True)
```

- `log(message, level)` — called for every step; `level` is `info` / `ok` / `warn` / `error`.
  A logger that raises can never abort an operation.
- `autosave` — write a background snapshot of every accepted state for crash recovery.

## Operations

| Method | Returns |
|---|---|
| `load(path)` | Full result: analysis, stats, shells, preview, state id |
| `analyze()` | Fresh diagnostics for the current mesh |
| `repair(strict_watertight=True, force=False)` | Result + `report` with `fixes`, `changes`, `passes` |
| `fix_slivers(min_angle_deg=1.0, force=False)` | Result + `info` (`before`, `after`, `collapsed`, `flipped`) |
| `simplify(keep_fraction=0.5, force=False)` | Result + `info` including `deviation` |
| `retopo(target_faces, method="quadriflow", preserve_sharp=True, adaptive=True)` | Result + `info` |
| `remove_shells(indices)` | Result for the remaining geometry |
| `rotate(matrix)` | `stats`, `centre`, `bounds` — no geometry payload |
| `undo()` / `redo()` / `revert()` | Result for the state you land on |
| `state_list()` | `[{id, operation, verdict, score, faces}, …]` |
| `export_stl(path, scale_unit="mm", align_origin=True)` | `{"result": {...}}` |
| `report()` / `export_report(path)` | The full JSON report |

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
from engine.stl_exporter  import export_to_stl
from engine.model_loader  import load_model
```

Each is pure: mesh in, mesh + report out. No global state, no UI.
