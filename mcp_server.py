"""
Meshwright MCP server — Geekatplay Studio, Vladimir Chopine

Exposes the mesh engine to MCP clients (Claude Desktop, Claude Code, IDEs…)
over stdio. Same engine.service.MeshService as the desktop app, so every
call is validated, guarded against regressions and undoable.

Run:            python mcp_server.py
Claude Desktop: add to claude_desktop_config.json
  "meshwright": {"command": "python", "args": ["D:/path/to/mcp_server.py"]}
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.mcpserver import MCPServer

from engine.service import MeshService, ServiceError
from engine.validation import ValidationError

mcp = MCPServer("Meshwright", version="1.0.0", instructions=(
    "Mesh analysis and repair for 3D printing by Geekatplay Studio. "
    "Load a model, inspect diagnostics, repair, simplify, rotate, remove shells, then export STL. "
    "Every change is a numbered state; undo/redo/revert are available and bad results are rejected automatically."
))

_log_lines: list[str] = []


def _log(message: str, level: str = "info"):
    _log_lines.append(f"[{level}] {message}")
    if len(_log_lines) > 500:
        del _log_lines[:-500]


service = MeshService(log=_log, autosave=True)


def _strip(res: dict) -> dict:
    """
    Tool results must be small.

    The mesh preview is binary geometry, and the texture block used to carry every
    map as base64 — a repair on a textured model returned 9 MB, which is no use to
    an assistant working through a context window. Both are dropped; the texture
    summary that remains says what exists without the pixels.
    """
    res = dict(res)
    res.pop("preview", None)
    res.pop("uv_layout", None)
    textures = res.get("textures")
    if isinstance(textures, dict):
        res["textures"] = {k: v for k, v in textures.items()
                           if k in ("has_textures", "has_uv", "channels")}
    return res


def _call(fn, *args, **kwargs) -> str:
    _log_lines.clear()
    try:
        out = _strip(fn(*args, **kwargs))
    except (ServiceError, ValidationError) as e:
        out = {"success": False, "error": str(e)}
    out["log"] = list(_log_lines)
    return json.dumps(out, indent=1)


@mcp.tool()
def load_model(path: str) -> str:
    """Load a 3D model (OBJ, FBX, GLB/GLTF, STL, PLY, 3MF, DAE, OFF) and return full diagnostics."""
    return _call(service.load, path)


@mcp.tool()
def analyze() -> str:
    """Re-run diagnostics on the current mesh: issues with severity and locations, stats, readiness score."""
    return _call(service.analyze)


@mcp.tool()
def repair(strict_watertight: bool = True, force: bool = False) -> str:
    """Run the staged repair pipeline (cleanup, orientation, hole filling, MeshFix, MeshLab, Manifold3D,
    voxel remesh as last resort). Rejected automatically if it would make things worse unless force=True."""
    return _call(service.repair, strict_watertight, force)


@mcp.tool()
def fix_slivers(min_angle_deg: float = 1.0, force: bool = False) -> str:
    """Collapse needle triangles and flip cap triangles thinner than min_angle_deg degrees."""
    return _call(service.fix_slivers, min_angle_deg, force)


@mcp.tool()
def simplify(keep_fraction: float = 0.5, force: bool = False) -> str:
    """Quadric decimation. keep_fraction=0.5 keeps half of the faces."""
    return _call(service.simplify, keep_fraction, force)


@mcp.tool()
def retopologize(target_faces: int, method: str = "quadriflow", preserve_sharp: bool = True, adaptive: bool = True) -> str:
    """Smart retopology / aggressive low-poly reduction to an absolute face count.
    method: quadriflow (clean curvature-aligned quads, best for organic/low-poly), isotropic (uniform triangles),
    quadric (topology-preserving collapse, keeps hard edges). Reports surface deviation in mm."""
    return _call(service.retopo, target_faces, method, preserve_sharp, adaptive)


@mcp.tool()
def remove_shells(indices: list[int]) -> str:
    """Delete disconnected pieces by index (see 'shells' in the last result; 0 is the largest)."""
    return _call(service.remove_shells, indices)


@mcp.tool()
def rotate(axis: str, degrees: float) -> str:
    """Rotate the model about its centre around axis 'x', 'y' or 'z' (model space, Z up)."""
    import numpy as np
    import trimesh
    ax = {"x": [1, 0, 0], "y": [0, 1, 0], "z": [0, 0, 1]}.get(str(axis).lower())
    if ax is None:
        return json.dumps({"success": False, "error": "axis must be x, y or z"})
    r = trimesh.transformations.rotation_matrix(np.radians(float(degrees)), ax)[:3, :3]
    return _call(service.rotate, r.tolist())


@mcp.tool()
def undo() -> str:
    """Go back one state."""
    return _call(service.undo)


@mcp.tool()
def redo() -> str:
    """Go forward one state."""
    return _call(service.redo)


@mcp.tool()
def revert() -> str:
    """Discard every change and return to the file as loaded."""
    return _call(service.revert)


@mcp.tool()
def states() -> str:
    """List the numbered states in the current session."""
    return json.dumps({"success": True, "states": service.state_list()}, indent=1)


@mcp.tool()
def export_stl(path: str, scale_unit: str = "mm", align_origin: bool = True) -> str:
    """Write the current mesh as a print-ready binary STL. scale_unit: mm, cm or in (source units).

    The result reports is_solid and any warnings: an open surface is sliced by a
    printer as a single-wall shell with no infill, so repair it before printing."""
    return _call(service.export_stl, path, scale_unit, align_origin)


@mcp.tool()
def export_report(path: str) -> str:
    """Write the diagnostics + operation history as JSON."""
    return _call(service.export_report, path)


@mcp.resource("meshwright://report")
def current_report() -> str:
    """The current diagnostics and history as JSON."""
    try:
        return json.dumps(service.report(), indent=1)
    except ServiceError as e:
        return json.dumps({"error": str(e)})


if __name__ == "__main__":
    mcp.run()
