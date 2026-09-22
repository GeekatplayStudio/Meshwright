"""
Open a Blender project by asking Blender, without touching the source file.

A .blend is Blender's own memory image; nothing else reads it reliably. So an
installed Blender is run headless, told to open the project and write out a glTF,
and Meshwright loads that.

The project usually holds more than the model. A rigged figure arrives with the
rig's controller widgets, and a scene lit for rendering arrives with its studio
floor and reflection cards. Blender's own visibility flags catch some of it — a rig
widget is marked "don't render" — but not all. In one real project the 200 x 200
ground plane is named "Studio ground - excluded from model validation" and is, as
far as every flag in the file is concerned, a fully visible, renderable object: its
author's intent lives in the name and nowhere else. No rule can be written that
picks that out without also throwing away somebody's floor tile.

So this module does two things and guesses at neither. It skips what Blender itself
would not render, and it can list what remains — name, size, triangle count and the
collection it sits in — so the person says which parts they want. Only then does it
export, and only the parts they kept.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from engine.runtime import subprocess_flags

BLENDER_TIMEOUT = 900          # a large project takes minutes to open and write out


def find_blender() -> str:
    override = os.environ.get("MESHWRIGHT_BLENDER")
    if override:
        executable = shutil.which(override) or (override if os.path.isfile(override) else None)
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


# Both scripts are written into a temporary folder, so this works in a frozen build
# too, and both report through a JSON file rather than through stdout — Blender
# prints a great deal of its own, and picking our line out of it would be guesswork.
_LIST_SCRIPT = '''
import bpy, json, sys

source, report = sys.argv[sys.argv.index("--") + 1:]
bpy.ops.wm.open_mainfile(filepath=source, load_ui=False, use_scripts=False)

layers = {}
def walk(layer):
    layers[layer.collection.name] = layer
    for child in layer.children:
        walk(child)
walk(bpy.context.view_layer.layer_collection)

parts = []
for ob in bpy.context.scene.objects:
    if ob.type != "MESH":
        continue
    collection = ob.users_collection[0].name if ob.users_collection else ""
    layer = layers.get(collection)
    parts.append({
        "name": ob.name,
        "faces": len(ob.data.polygons),
        "size": [round(v, 4) for v in ob.dimensions],
        "group": collection,
        "rigged": any(m.type == "ARMATURE" for m in ob.modifiers),
        # Blender's own answer to "would this be in the render?"
        "hidden": bool(ob.hide_render or ob.hide_get() or not ob.visible_get()
                       or (layer is not None and layer.exclude)),
    })

armatures = [{"name": ob.name, "bones": len(ob.data.bones)}
             for ob in bpy.context.scene.objects if ob.type == "ARMATURE"]
with open(report, "w", encoding="utf-8") as handle:
    json.dump({"parts": parts, "armatures": armatures,
               "scene": bpy.context.scene.name}, handle)
'''

_EXPORT_SCRIPT = '''
import bpy, json, sys

source, destination, wanted = sys.argv[sys.argv.index("--") + 1:]
bpy.ops.wm.open_mainfile(filepath=source, load_ui=False, use_scripts=False)

keep = None
if wanted != "-":
    with open(wanted, "r", encoding="utf-8") as handle:
        keep = set(json.load(handle))

options = dict(
    filepath=destination, export_format="GLB", check_existing=False,
    use_active_scene=True, export_apply=True, export_animations=False,
    export_yup=True, export_cameras=False, export_lights=False,
    # What Blender would keep out of a render is not part of the model either.
    use_visible=True, use_renderable=True,
)
if keep is not None:
    # Exporting a chosen set means selecting it; the exporter has no name filter.
    bpy.ops.object.select_all(action="DESELECT")
    chosen = 0
    for ob in bpy.context.scene.objects:
        if ob.type == "MESH" and ob.name in keep:
            ob.select_set(True)
            chosen += 1
        elif ob.type == "ARMATURE":
            # The armature carries the pose its meshes are bound to, so it travels
            # with them; without it a skinned part exports in its bind pose.
            ob.select_set(True)
    if not chosen:
        raise SystemExit("None of the chosen objects are in this project.")
    options["use_selection"] = True

bpy.ops.export_scene.gltf(**options)
'''


def _run(script_body: str, arguments: list, folder: str, what: str):
    script = Path(folder) / "task.py"
    script.write_text(script_body, encoding="utf-8")
    command = [find_blender(), "--background", "--factory-startup", "--disable-autoexec",
               "--python-exit-code", "1", "--python", str(script), "--", *arguments]
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=BLENDER_TIMEOUT,
                                **subprocess_flags())
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Blender took too long to {what}.") from exc
    except OSError as exc:
        raise RuntimeError(f"Could not start Blender: {exc}") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout or "").strip()[-2000:]
        raise RuntimeError(f"Blender could not {what}: {detail}")
    return result


def list_objects(file_path: str, log=None) -> dict:
    """
    What is in the project, without exporting any of it.

    This is the cheap half. Opening a project takes a second or two where writing it
    out takes minutes, which is what makes it practical to ask before committing.
    """
    log = log or (lambda *a, **k: None)
    log("Looking inside the Blender project")
    with tempfile.TemporaryDirectory(prefix="meshwright-blend-") as folder:
        report = Path(folder) / "parts.json"
        _run(_LIST_SCRIPT, [os.path.abspath(file_path), str(report)], folder, "read this project")
        if not report.is_file():
            raise RuntimeError("Blender did not report what is in this project.")
        with open(report, "r", encoding="utf-8") as handle:
            return json.load(handle)


def load_blend(file_path: str, log=None, keep=None):
    """
    Bring a Blender project in, optionally only the objects named in `keep`.

    Names are as `list_objects` reports them; None means everything Blender would
    render.
    """
    from engine.model_loader import load_model

    log = log or (lambda *a, **k: None)
    log("Opening Blender project in the background (this may take a moment)")
    with tempfile.TemporaryDirectory(prefix="meshwright-blend-") as folder:
        output = Path(folder) / "scene.glb"
        wanted = "-"
        if keep is not None:
            chosen = Path(folder) / "keep.json"
            with open(chosen, "w", encoding="utf-8") as handle:
                json.dump(list(keep), handle)
            wanted = str(chosen)
            log(f"Exporting {len(keep)} of the project's objects")
        _run(_EXPORT_SCRIPT, [os.path.abspath(file_path), str(output), wanted],
             folder, "open this project")
        if not output.is_file():
            raise RuntimeError("Blender exported no geometry from this project.")
        mesh, _ = load_model(str(output), log=log, with_stats=False)
        if not len(mesh.faces):
            raise ValueError("Blender project contains no mesh geometry in its active scene.")
        return mesh
