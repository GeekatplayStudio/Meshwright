"""Convert Blender projects through an installed Blender, without changing the source."""
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from engine.runtime import subprocess_flags


def find_blender() -> str:
    override = os.environ.get("MESHWRIGHT_BLENDER")
    if override:
        executable = shutil.which(override)
        if executable:
            return executable
        raise RuntimeError("MESHWRIGHT_BLENDER does not point to a Blender executable.")
    executable = shutil.which("blender")
    if executable:
        return executable
    candidates = []
    for variable in ("ProgramFiles", "ProgramW6432", "LOCALAPPDATA"):
        root = os.environ.get(variable)
        if root:
            candidates.extend(Path(root).glob("Blender Foundation/Blender */blender.exe"))
    candidates.sort(key=lambda p: tuple(int(n) for n in re.findall(r"\d+", p.parent.name)),
                    reverse=True)
    candidates.append(Path("/Applications/Blender.app/Contents/MacOS/Blender"))
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    raise RuntimeError(
        "Importing .blend files requires Blender. Install Blender or set "
        "MESHWRIGHT_BLENDER to the full path of its executable, then retry."
    )


# Written into the temporary directory so this also works in frozen builds.
_EXPORT_SCRIPT = """
import bpy
import sys

source, destination = sys.argv[sys.argv.index('--') + 1:]
bpy.ops.wm.open_mainfile(filepath=source, load_ui=False, use_scripts=False)
bpy.ops.export_scene.gltf(
    filepath=destination, export_format='GLB', check_existing=False,
    use_active_scene=True, export_apply=True, export_animations=False,
    export_yup=True, export_cameras=False, export_lights=False,
)
"""


def load_blend(file_path: str, log):
    from engine.model_loader import load_model

    executable = find_blender()
    log("Opening Blender project in the background (this may take a moment)")
    with tempfile.TemporaryDirectory(prefix="meshwright-blend-") as folder:
        script = Path(folder) / "export.py"
        output = Path(folder) / "scene.glb"
        script.write_text(_EXPORT_SCRIPT, encoding="utf-8")
        command = [executable, "--background", "--factory-startup", "--disable-autoexec",
                   "--python-exit-code", "1", "--python", str(script), "--",
                   os.path.abspath(file_path), str(output)]
        try:
            result = subprocess.run(command, check=False, capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=300,
                                    **subprocess_flags())
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Blender import timed out after 5 minutes.") from exc
        except OSError as exc:
            raise RuntimeError(f"Could not start Blender: {exc}") from exc
        if result.returncode or not output.is_file():
            detail = (result.stderr or result.stdout or "No geometry was exported.").strip()[-2000:]
            raise RuntimeError(f"Blender could not import this project: {detail}")
        mesh, _ = load_model(str(output), log=log, with_stats=False)
        if not len(mesh.faces):
            raise ValueError("Blender project contains no mesh geometry in its active scene.")
        return mesh
