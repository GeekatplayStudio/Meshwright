# MCP server

Meshwright exposes its full engine over the [Model Context Protocol](https://modelcontextprotocol.io),
so an AI assistant or editor can analyse and repair meshes directly. It is the *same*
`engine.service.MeshService` the desktop app uses — same validation, same safety guard, same undo.

```bash
python mcp_server.py        # stdio transport
npm run mcp                 # same thing
```

## Claude Desktop

`claude_desktop_config.json`:

```jsonc
{
  "mcpServers": {
    "meshwright": {
      "command": "python",
      "args": ["D:/path/to/Meshwright/mcp_server.py"]
    }
  }
}
```

## Claude Code

```bash
claude mcp add meshwright -- python D:/path/to/Meshwright/mcp_server.py
```

## Tools

| Tool | Arguments | Does |
|---|---|---|
| `load_model` | `path` | Load OBJ/FBX/GLB/GLTF/STL/PLY/3MF/DAE/OFF and return full diagnostics |
| `analyze` | — | Re-run diagnostics on the current mesh |
| `repair` | `strict_watertight=true`, `force=false` | Staged repair pipeline, verified afterwards |
| `fix_slivers` | `min_angle_deg=1.0`, `force=false` | Collapse needles, flip caps |
| `simplify` | `keep_fraction=0.5`, `force=false` | Quadric decimation by ratio |
| `retopologize` | `target_faces`, `method="quadriflow"`, `preserve_sharp=true`, `adaptive=true` | Smart retopology / aggressive reduction to an absolute face count |
| `remove_shells` | `indices` | Delete disconnected pieces by index (0 is largest) |
| `rotate` | `axis`, `degrees` | Rotate about the model centre |
| `undo` / `redo` | — | Move between numbered states |
| `revert` | — | Back to the file as loaded |
| `states` | — | List states in this session |
| `export_stl` | `path`, `scale_unit="mm"`, `align_origin=true` | Write a print-ready binary STL |
| `export_report` | `path` | Write diagnostics + history as JSON |

`method` for `retopologize`: `quadriflow` (clean curvature-aligned quads — best for organic and
low-poly), `isotropic` (uniform triangles), `quadric` (topology-preserving collapse, keeps hard edges).

## Resource

`meshwright://report` — the current diagnostics and operation history as JSON.

## Result shape

Every tool returns JSON. The heavy binary preview is stripped, so results stay small.

```jsonc
{
  "success": true,
  "state_id": 2,
  "operation": "repair",
  "analysis": {
    "stats": { "face_count": 44962, "is_watertight": true, "body_count": 23, "...": "..." },
    "issues": [ { "id": "slivers", "severity": "info", "title": "Sliver triangles",
                  "detail": "...", "count": 186 } ],
    "score": 94,
    "verdict": "Printable, minor cleanup available"
  },
  "can_undo": true,
  "can_redo": false,
  "log": ["[info] Stage 1: ...", "[ok] State #2 (repair): ..."]
}
```

The `log` array is the same step-by-step trace the desktop console shows — useful for explaining
to a user what actually happened.

## Safety

A destructive result is refused rather than committed:

```jsonc
{
  "success": false,
  "rejected": true,
  "reason": "critical problems would increase from 0 to 2",
  "would_be": { "verdict": "Repair required", "score": 21 },
  "state_id": 1
}
```

Pass `force=true` to apply it anyway — deliberately, never by accident. Nothing is destroyed either
way: `undo` returns to the previous state, and every accepted state is snapshotted to disk.

## Notes

- The server keeps one session. Loading a new model resets the state history.
- Paths are validated: the file must exist, have a supported extension and be under 2 GB.
  Output paths must be in an existing folder.
- Autosave snapshots live in `%LOCALAPPDATA%\Meshwright\sessions` and are removed on a clean exit.
