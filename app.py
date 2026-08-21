"""
Meshwright — mesh analysis, repair and STL preparation for 3D printing.
Geekatplay Studio — Vladimir Chopine

Desktop entry point. AppApi is a thin pywebview adapter over engine.service.MeshService;
all validation, state handling and autosave live in the service.
"""
import os
import sys
import json
import time
import traceback
import webview

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine.service import MeshService, ServiceError
from engine.validation import ValidationError


_state = {'window': None}


def log(message: str, level: str = "info"):
    """Send a line to the UI console (and stdout)."""
    line = f"[{time.strftime('%H:%M:%S')}] {message}"
    try:
        print(line, flush=True)
    except (UnicodeEncodeError, OSError):
        print(line.encode("ascii", "replace").decode("ascii"), flush=True)
    win = _state['window']
    if win is not None:
        try:
            win.evaluate_js(f"window.meshwright && window.meshwright.log({json.dumps(message)}, {json.dumps(level)})")
        except Exception:
            pass


def progress(**event):
    """Forward a progress event to the UI (toast + progress bar)."""
    win = _state['window']
    if win is None:
        return
    try:
        win.evaluate_js(f"window.meshwright && window.meshwright.progress({json.dumps(event)})")
    except Exception:
        pass


def _guarded(fn):
    """Turn service/validation errors into {success:false} results for the UI."""
    def wrapper(self, *args, **kwargs):
        try:
            return fn(self, *args, **kwargs)
        except (ServiceError, ValidationError) as e:
            log(str(e), "error")
            return {"success": False, "error": str(e)}
        except Exception as e:
            log(f"{fn.__name__} failed: {e}", "error")
            traceback.print_exc()
            return {"success": False, "error": str(e)}
    wrapper.__name__ = fn.__name__
    return wrapper


class AppApi:
    """Exposed to the UI through pywebview."""

    def __init__(self, service: MeshService | None = None):
        self.svc = service or MeshService(log=log, progress=progress)

    def set_window(self, window):
        _state['window'] = window

    @property
    def _window(self):
        return _state['window']

    # ------------------------------------------------------------------ dialogs
    def select_file_dialog(self) -> str:
        file_types = (
            '3D Models (*.obj;*.fbx;*.glb;*.gltf;*.stl;*.ply;*.3ds;*.dae;*.3mf;*.off)',
            'Wavefront OBJ (*.obj)',
            'Autodesk FBX (*.fbx)',
            'GLTF and GLB (*.glb;*.gltf)',
            'STL (*.stl)',
            'PLY (*.ply)',
            'All files (*.*)'
        )
        result = self._window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=False, file_types=file_types)
        return result[0] if result else ""

    def _save_dialog(self, default_name: str, file_types):
        path = self._window.create_file_dialog(webview.FileDialog.SAVE, save_filename=default_name, file_types=file_types)
        if isinstance(path, (list, tuple)):
            path = path[0] if path else None
        return path

    # ------------------------------------------------------------------ operations
    @_guarded
    def load_model_file(self, file_path: str) -> dict:
        return self.svc.load(file_path)

    @_guarded
    def auto_fix_mesh(self, strict_watertight: bool = True, force: bool = False) -> dict:
        return self.svc.repair(strict_watertight, force)

    @_guarded
    def fix_sliver_faces(self, min_angle_deg: float = 1.0, force: bool = False) -> dict:
        return self.svc.fix_slivers(min_angle_deg, force)

    @_guarded
    def reduce_mesh_quality(self, reduction_factor: float = 0.5, force: bool = False) -> dict:
        return self.svc.simplify(reduction_factor, force)

    @_guarded
    def retopologize(self, target_faces: int, method: str = "quadriflow", preserve_sharp: bool = True, adaptive: bool = True) -> dict:
        return self.svc.retopo(target_faces, method, preserve_sharp, adaptive)

    @_guarded
    def remove_shells(self, indices: list) -> dict:
        return self.svc.remove_shells(indices)

    @_guarded
    def apply_rotation(self, matrix) -> dict:
        return self.svc.rotate(matrix)

    @_guarded
    def analyze_current(self) -> dict:
        return self.svc.analyze()

    @_guarded
    def undo(self) -> dict:
        return self.svc.undo()

    @_guarded
    def redo(self) -> dict:
        return self.svc.redo()

    @_guarded
    def revert_to_original(self) -> dict:
        return self.svc.revert()

    @_guarded
    def list_states(self) -> dict:
        return {"success": True, "states": self.svc.state_list()}

    # ------------------------------------------------------------------ recovery
    @_guarded
    def recoverable_sessions(self) -> dict:
        return {"success": True, "sessions": MeshService.recoverable_sessions()}

    @_guarded
    def recover_session(self, session: str) -> dict:
        return self.svc.recover(session)

    @_guarded
    def discard_session(self, session: str) -> dict:
        MeshService.discard_session(session)
        return {"success": True}

    # ------------------------------------------------------------------ about
    ALLOWED_URLS = ("https://geekatplay.gumroad.com/coffee", "https://www.geekatplay.com")

    @_guarded
    def about_info(self) -> dict:
        """Versions of the engines in use, for the About overlay."""
        import platform
        from importlib.metadata import version, PackageNotFoundError
        libs = {}
        for name, dist in (("trimesh", "trimesh"), ("NumPy", "numpy"), ("SciPy", "scipy"), ("PyMeshLab", "pymeshlab"),
                           ("Manifold3D", "manifold3d"), ("pymeshfix (MeshFix)", "pymeshfix"),
                           ("fast-simplification", "fast-simplification"), ("pyQuadriFlow", "pyQuadriFlow"), ("scikit-image", "scikit-image"),
                           ("pywebview", "pywebview"), ("mcp", "mcp"), ("ufbx", "ufbx")):
            try:
                libs[name] = version(dist)
            except PackageNotFoundError:
                libs[name] = None
        return {"success": True, "version": "1.1.0", "python": platform.python_version(),
                "platform": f"{platform.system()} {platform.release()}", "libraries": libs, "three": "r128"}

    @_guarded
    def open_url(self, url: str) -> dict:
        """Open a whitelisted URL in the system browser."""
        import webbrowser
        if url not in self.ALLOWED_URLS:
            return {"success": False, "error": "URL not allowed."}
        webbrowser.open(url)
        return {"success": True}

    # ------------------------------------------------------------------ export
    @_guarded
    def export_stl_file(self, scale_unit: str = "mm", align_origin: bool = True) -> dict:
        self.svc._require()
        path = self._save_dialog(self.svc.default_export_name("_print_ready", ".stl"),
                                 ('STL (*.stl)', 'All files (*.*)'))
        if not path:
            return {"success": False, "canceled": True}
        return self.svc.export_stl(path, scale_unit, align_origin)

    @_guarded
    def export_report(self) -> dict:
        self.svc._require()
        path = self._save_dialog(self.svc.default_export_name("_report", ".json"),
                                 ('JSON (*.json)', 'All files (*.*)'))
        if not path:
            return {"success": False, "canceled": True}
        return self.svc.export_report(path)


def _set_window_icon(title: str, ico_path: str):
    """Windows: replace the default python icon on the title bar / taskbar."""
    if sys.platform != "win32" or not os.path.exists(ico_path):
        return
    try:
        import ctypes
        from ctypes import wintypes
        u32 = ctypes.windll.user32
        u32.FindWindowW.restype = wintypes.HWND
        u32.LoadImageW.restype = wintypes.HANDLE
        hwnd = u32.FindWindowW(None, title)
        if not hwnd:
            return
        IMAGE_ICON, LR_LOADFROMFILE, WM_SETICON = 1, 0x10, 0x80
        big = u32.LoadImageW(None, ico_path, IMAGE_ICON, 256, 256, LR_LOADFROMFILE)
        small = u32.LoadImageW(None, ico_path, IMAGE_ICON, 32, 32, LR_LOADFROMFILE)
        if big:
            u32.SendMessageW(hwnd, WM_SETICON, 1, big)
        if small:
            u32.SendMessageW(hwnd, WM_SETICON, 0, small)
    except Exception:
        pass


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    api = AppApi()
    ui_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ui')

    window = webview.create_window(
        title='Meshwright — Geekatplay Studio',
        url=os.path.join(ui_dir, 'index.html'),
        js_api=api,
        width=1380,
        height=860,
        min_size=(1024, 700),
        background_color='#111315'
    )
    api.set_window(window)

    def on_drop(event):
        # Browsers hide real file paths from JavaScript; pywebview delivers
        # them to Python-side drop handlers as 'pywebviewFullPath'.
        files = (event.get('dataTransfer') or {}).get('files') or []
        for f in files:
            path = f.get('pywebviewFullPath')
            if path:
                window.evaluate_js(f"window.meshwright.load({json.dumps(path)})")
                break

    def on_loaded():
        window.dom.get_element('#dropZone').on('drop', on_drop)
        _set_window_icon(window.title, os.path.join(ui_dir, 'assets', 'icon.ico'))
        log("Meshwright ready — Geekatplay Studio")
        sessions = MeshService.recoverable_sessions()
        if sessions:
            window.evaluate_js(f"window.meshwright.offerRecovery({json.dumps(sessions)})")

    def on_closing():
        api.svc.close()

    window.events.loaded += on_loaded
    window.events.closing += on_closing
    webview.start(debug=False)


if __name__ == '__main__':
    main()
