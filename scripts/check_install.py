r"""Report what Meshwright can actually do with the current interpreter.

Run it directly for a support snapshot:

    .venv\Scripts\python scripts\check_install.py

Exit code 0 = ready to run, 1 = a required package is missing.
"""
import importlib
import importlib.metadata as md
import platform
import sys

REQUIRED = [
    ("numpy", "numerics"),
    ("scipy", "numerics"),
    ("trimesh", "mesh core"),
    ("webview", "desktop window"),
    ("PIL", "texture images"),
    ("xatlas", "UV unwrapping"),
    ("rtree", "exact UV transfer after repair and reduction"),
]

OPTIONAL = [
    ("pymeshlab", "non-manifold repair, decimation, isotropic remesh"),
    ("pymeshfix", "hole filling and self-intersection repair"),
    ("manifold3d", "guaranteed-manifold solid reconstruction"),
    ("fast_simplification", "fast quadric decimation"),
    ("pyQuadriFlow", "smart quad retopology"),
    ("skimage", "voxel remesh (last-resort repair)"),
    ("ufbx", "FBX import"),
    ("lxml", "3MF and DAE files"),
    ("shapely", "2D sections"),
    ("mcp", "MCP server for AI assistants"),
    ("cv2", "texture gutter dilation"),
]

DIST_NAMES = {"webview": "pywebview", "skimage": "scikit-image",
              "fast_simplification": "fast-simplification", "PIL": "pillow", "cv2": "opencv-python"}


def _version(module_name):
    try:
        return md.version(DIST_NAMES.get(module_name, module_name))
    except Exception:
        mod = sys.modules.get(module_name)
        return getattr(mod, "__version__", "?")


def _probe(module_name):
    try:
        importlib.import_module(module_name)
        return True, _version(module_name)
    except Exception as exc:  # a broken wheel raises more than ImportError
        return False, str(exc).splitlines()[0][:70]


def _webview2_note():
    """pywebview draws the window with Edge WebView2; Windows 10 may not have it."""
    if not sys.platform.startswith("win"):
        return None
    import winreg

    key = r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(root, key) as handle:
                version, _ = winreg.QueryValueEx(handle, "pv")
                if version and version != "0.0.0.0":
                    return None
        except OSError:
            continue
    return ["The Edge WebView2 runtime was not found - Meshwright draws its window with it.",
            "Install the free Evergreen Bootstrapper from",
            "https://developer.microsoft.com/microsoft-edge/webview2/"]


def main():
    print("Meshwright environment check")
    print("-" * 60)
    print(f"Python      {platform.python_version()} ({platform.architecture()[0]})")
    print(f"Interpreter {sys.executable}")
    print(f"Platform    {platform.platform()}")
    print("-" * 60)

    missing_required = []
    for name, what in REQUIRED:
        ok, info = _probe(name)
        print(f"  {'[OK]     ' if ok else '[MISSING]'} {name:<20} {info:<12} {what}")
        if not ok:
            missing_required.append(name)

    print("  " + "-" * 58)
    missing_optional = []
    for name, what in OPTIONAL:
        ok, info = _probe(name)
        print(f"  {'[OK]     ' if ok else '[skipped]'} {name:<20} {info:<12} {what}")
        if not ok:
            missing_optional.append(name)

    note = _webview2_note()
    if note:
        print(f"  [!]       {note[0]}")
        for line in note[1:]:
            print(f"            {line}")

    print("-" * 60)
    if missing_required:
        print("NOT READY - required packages missing: " + ", ".join(missing_required))
        print("Run install.bat again; if it keeps failing, send install-log.txt to support.")
        return 1
    if missing_optional:
        print("Ready. Some optional engines are unavailable: " + ", ".join(missing_optional))
        print("Meshwright still runs; those specific repair or retopology methods are skipped.")
    else:
        print("Ready - every engine is available.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
