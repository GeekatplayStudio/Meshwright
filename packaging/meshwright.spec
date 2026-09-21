# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller recipe for Meshwright.exe — Geekatplay Studio.

Do not run this directly; packaging\\build.ps1 does, and checks the result.

Two editions, chosen with MESHWRIGHT_EDITION:

  full   everything, including PyMeshLab and pymeshfix (both GPL-3)
  lite   the same program without those two. It still repairs, decimates and retopologises;
         it just has fewer engines to fall back on. Choose it when the licence of what
         you distribute matters more than the last bit of repair quality.

The result is a *folder* (dist\\Meshwright\\Meshwright.exe plus _internal\\), not a single
file. A single-file exe unpacks ~450 MB into a temp folder on every launch, which costs
10-20 seconds each time and is what antivirus products dislike most.
"""
import os
import sys

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_submodules,
    copy_metadata,
)

ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))            # noqa: F821 - provided by PyInstaller
sys.path.insert(0, ROOT)

EDITION = os.environ.get("MESHWRIGHT_EDITION", "full").strip().lower()
if EDITION not in ("full", "lite"):
    raise SystemExit(f"MESHWRIGHT_EDITION must be 'full' or 'lite', not '{EDITION}'")

GPL_ENGINES = ["pymeshlab", "pymeshfix"]

SITE = None
for entry in sys.path:
    if entry.replace("\\", "/").endswith("site-packages") and os.path.isdir(os.path.join(entry, "numpy")):
        SITE = entry
        break
if SITE is None:
    raise SystemExit("Could not find the environment's site-packages; run this with the project's .venv python.")


# ------------------------------------------------------------------ data files
def tree(source, target, skip=()):
    """(source file, folder inside the bundle) for every file under `source`."""
    out = []
    for folder, _dirs, files in os.walk(source):
        for name in files:
            full = os.path.join(folder, name)
            if any(part in full.replace("\\", "/") for part in skip):
                continue
            rel = os.path.relpath(folder, source)
            out.append((full, os.path.normpath(os.path.join(target, rel))))
    return out


datas = []
# The interface. Individual walk-cycle frames are reference material for the script
# that cuts the animated strip; only the strip itself is used at run time.
datas += tree(os.path.join(ROOT, "ui"), "ui", skip=("/walk/frame-", "/walk/README"))
datas += tree(os.path.join(ROOT, "comfyui_nodes"), "comfyui_nodes", skip=("__pycache__",))

# Package data the libraries read at run time.
datas += collect_data_files("trimesh")
# The printer table, read by engine.printers so the check works with no slicer installed.
datas += [(os.path.join(ROOT, "engine", "data", "printers.json"), os.path.join("engine", "data"))]

# importlib.metadata needs the .dist-info folders for the About panel's version table.
for dist in ("trimesh", "numpy", "scipy", "pymeshlab", "manifold3d", "pymeshfix", "fast_simplification",
             "pyQuadriFlow", "pywebview", "mcp", "ufbx", "xatlas", "pillow", "opencv-python", "rtree",
             "embreex",
             "lxml", "shapely", "scikit-image"):
    if EDITION == "lite" and dist in GPL_ENGINES:
        continue                                                       # not in this build: do not claim it
    try:
        datas += copy_metadata(dist)
    except Exception:                                                  # not installed: fine
        pass

# ------------------------------------------------------------------ native code
binaries = []
quadriflow_dll = os.path.join(SITE, "pyQuadriFlow", "clib", "ctypes_QuadriFlow.dll")
if os.path.exists(quadriflow_dll):
    # Loaded by ctypes from a path built off __file__, which PyInstaller's analysis
    # cannot see — so it has to be placed by hand, exactly where the loader looks.
    binaries += [(quadriflow_dll, os.path.join("pyQuadriFlow", "clib"))]

# ------------------------------------------------------------------ imports
hiddenimports = [
    # The helper processes are found by name at run time (engine.runtime.WORKERS).
    "engine._quadriflow_worker",
    "engine.texture._xatlas_worker",
    # Imported lazily from app.py when the ComfyUI button is pressed.
    "scripts.install_comfyui_nodes",
    # pywebview picks its Windows backend by name.
    "webview.platforms.winforms",
    "webview.platforms.edgechromium",
    "clr_loader",
    "pythonnet",
    "_cffi_backend",
    "mcp_server",
]
# Only trimesh is swept: it loads its file-format handlers by name. The others are
# reached by ordinary imports, which the analysis follows on its own — and sweeping
# them backfires: mcp.cli calls sys.exit() when the optional `typer` is missing, which
# takes the whole freeze down with it. Anything genuinely dynamic that is missing shows
# up in the self-test (Meshwright.exe --selftest), which is what it is for.
hiddenimports += collect_submodules("trimesh", filter=lambda name: not name.startswith(
    ("trimesh.viewer", "trimesh.interfaces")), on_error="ignore")
# scikit-image loads its submodules lazily, which static analysis cannot see. Only marching
# cubes is used (trimesh's voxel remesh, the last-resort repair stage), so only that corner.
hiddenimports += collect_submodules("skimage", filter=lambda name: name.startswith(
    ("skimage.measure", "skimage._shared")), on_error="ignore")

excludes = [
    "tkinter", "matplotlib", "IPython", "jupyter", "notebook",
    "pytest", "_pytest", "ruff", "sphinx", "docutils",
    "PyQt5", "PyQt6", "PySide2", "PySide6", "wx", "gi", "gtk",
    "pyglet", "pyrender", "vtk", "vtkmodules",
]
if EDITION == "lite":
    excludes += GPL_ENGINES

a = Analysis(                                                          # noqa: F821
    [os.path.join(SPECPATH, "launcher.py")],                           # noqa: F821
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

# ------------------------------------------------------------------ trimming
# Files the libraries ship for building against them, not for running: link libraries,
# debug symbols, headers, and their own test suites. And OpenCV's 30 MB video codec,
# which nothing here uses. (OpenCV itself stays: gutter-filling a 4096px texture takes
# 1.5 s with it and 18 s without.)
# Not ".pyi": those look like type-hint clutter, but scikit-image (and anything else using
# `lazy_loader`) reads its stubs at run time to learn where its submodules live. Dropping them
# leaves skimage.measure hollow — the compiled parts present, the map to them missing — and the
# voxel-remesh repair stage, which needs marching cubes, silently stops working.
_DROP_SUFFIXES = (".lib", ".pdb", ".exp", ".h", ".hpp")
_DROP_PARTS = ("opencv_videoio_ffmpeg", "/pymeshlab/tests/")


def keep(entry):
    name = entry[0].replace("\\", "/").lower()
    return not (name.endswith(_DROP_SUFFIXES) or any(part in name for part in _DROP_PARTS))


a.datas = [d for d in a.datas if keep(d)]
a.binaries = [b for b in a.binaries if keep(b)]

pyz = PYZ(a.pure)                                                      # noqa: F821

exe = EXE(                                                             # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Meshwright",
    icon=os.path.join(ROOT, "ui", "assets", "icon.ico"),
    version=os.path.join(SPECPATH, "version_info.txt") if os.path.exists(os.path.join(SPECPATH, "version_info.txt")) else None,  # noqa: F821
    console=False,                 # a window program: no black console box behind it
    upx=False,                     # UPX makes antivirus suspicious and start-up slower
    disable_windowed_traceback=False,
)

coll = COLLECT(                                                        # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Meshwright",
)
