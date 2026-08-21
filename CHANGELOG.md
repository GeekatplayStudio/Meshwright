# Changelog

All notable changes to Meshwright. Format based on [Keep a Changelog](https://keepachangelog.com).

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
