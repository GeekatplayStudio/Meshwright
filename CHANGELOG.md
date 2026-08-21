# Changelog

All notable changes to Meshwright. Format based on [Keep a Changelog](https://keepachangelog.com).

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
