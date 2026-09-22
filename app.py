"""
Meshwright — mesh analysis, repair and STL preparation for 3D printing.
Geekatplay Studio — Vladimir Chopine

Desktop entry point. AppApi is a thin pywebview adapter over engine.service.MeshService;
all validation, state handling and autosave live in the service.
"""
import json
import os
import queue
import sys
import threading
import time
import traceback

import webview

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine.runtime import resource_path
from engine.service import MeshService, ServiceError
from engine.validation import ValidationError
from engine.version import __version__

WEBVIEW2_URL = "https://developer.microsoft.com/microsoft-edge/webview2/"

_state = {'window': None}
_eval_queue: queue.Queue = queue.Queue()


def _eval_worker():
    """Background worker draining JS evaluation tasks without blocking the engine."""
    while True:
        try:
            script = _eval_queue.get()
            win = _state['window']
            if win is not None:
                try:
                    win.evaluate_js(script)
                except Exception:
                    pass
        except Exception:
            pass
        finally:
            _eval_queue.task_done()


_eval_thread = threading.Thread(target=_eval_worker, daemon=True)
_eval_thread.start()


def log(message: str, level: str = "info"):
    """Send a line to the UI console (and stdout)."""
    line = f"[{time.strftime('%H:%M:%S')}] {message}"
    try:
        print(line, flush=True)
    except (UnicodeEncodeError, OSError):
        print(line.encode("ascii", "replace").decode("ascii"), flush=True)
    _eval_queue.put(f"window.meshwright && window.meshwright.log({json.dumps(message)}, {json.dumps(level)})")


def progress(**event):
    """Forward a progress event to the UI (toast + progress bar)."""
    _eval_queue.put(f"window.meshwright && window.meshwright.progress({json.dumps(event)})")


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
    #
    # These return a path, and the interface treats the return value as one:
    #     const path = await api().select_file_dialog();
    #     if (path) loadFile(path);
    # So they must return a string or nothing — never the {success: false} object
    # the @_guarded decorator produces, which is truthy and would be handed to
    # loadFile() as if it were a path. Failures are caught and logged here instead.

    MODEL_FILE_TYPES = (
        '3D Models (*.blend;*.obj;*.fbx;*.glb;*.gltf;*.stl;*.ply;*.3ds;*.dae;*.3mf;*.off)',
        'Wavefront OBJ (*.obj)',
        'Autodesk FBX (*.fbx)',
        'Blender project (*.blend)',
        'GLTF and GLB (*.glb;*.gltf)',
        'STL (*.stl)',
        'PLY (*.ply)',
        'All files (*.*)',
    )
    IMAGE_FILE_TYPES = (
        'Images (*.png;*.jpg;*.jpeg;*.webp;*.bmp;*.tga;*.tif;*.tiff)',
        'PNG (*.png)',
        'JPEG (*.jpg;*.jpeg)',
        'All files (*.*)',
    )

    def _dialog(self, what: str, mode, **kwargs):
        """
        Open one native dialog and return the single path it produced.

        Returns None when there is no window yet, when the user cancels, or when
        the platform dialog itself fails — the caller cannot tell those apart, and
        does not need to: in all three cases nothing was chosen.
        """
        window = self._window
        if window is None:
            log(f"Cannot open the {what} dialog yet — the window is still starting.", "warn")
            return None
        try:
            result = window.create_file_dialog(mode, **kwargs)
        except Exception as exc:
            log(f"The {what} dialog could not be opened: {exc}", "error")
            traceback.print_exc()
            return None
        if isinstance(result, (list, tuple)):
            return result[0] if result else None
        return result or None

    def select_file_dialog(self) -> str:
        """Windows' own Open dialog, still reachable from inside Meshwright's."""
        return self._dialog("open model", webview.FileDialog.OPEN,
                            allow_multiple=False, file_types=self.MODEL_FILE_TYPES) or ""

    def select_folder_dialog(self) -> str:
        return self._dialog("choose folder", webview.FileDialog.FOLDER) or ""

    def select_image_dialog(self) -> str:
        return self._dialog("choose image", webview.FileDialog.OPEN,
                            allow_multiple=False, file_types=self.IMAGE_FILE_TYPES) or ""

    def _save_dialog(self, default_name: str, file_types):
        """Where to save. None means the user cancelled or the dialog failed."""
        return self._dialog("save", webview.FileDialog.SAVE,
                            save_filename=default_name, file_types=file_types)

    # ------------------------------------------------------------------ Meshwright's own file browser
    #
    # Windows' Open dialog can show a picture of a Word document but not of a model:
    # it draws 3D thumbnails through Microsoft's 3D Viewer, which is not part of
    # Windows 11 any more. So Meshwright browses for itself, and engine.quicklook
    # draws each file — from a sample, in a fraction of the time opening it costs.

    @_guarded
    def browse_places(self) -> dict:
        from engine import browse
        return {"success": True, **browse.places()}

    @_guarded
    def browse_folder(self, path: str = "", show_all: bool = False) -> dict:
        from engine import browse
        listed = browse.listing(path, show_all)
        if not listed["error"]:
            browse.note_folder(listed["path"])
        return {"success": True, **listed}

    @_guarded
    def browse_recent(self) -> dict:
        from engine import browse
        return {"success": True, "files": browse.recent_files()}

    @_guarded
    def browse_forget_recent(self) -> dict:
        from engine import browse
        browse.forget_all()
        return {"success": True}

    @_guarded
    def browse_look(self, path: str, px: int = 0) -> dict:
        """One file's facts and picture. Slow only the first time: the result is cached."""
        from engine import quicklook
        return {"success": True, **quicklook.look(path, px or quicklook.PREVIEW_PX)}

    # ------------------------------------------------------------------ printability
    @_guarded
    def list_printers(self) -> dict:
        return self.svc.printers()

    @_guarded
    def check_printability(self, printer_id: str = "", technology: str = "", pixel_um: float = 0.0,
                           nozzle_mm: float = 0.0, layer_mm: float = 0.0,
                           thorough: bool = False) -> dict:
        return self.svc.printability(printer_id=printer_id, technology=technology,
                                     pixel_um=pixel_um, nozzle_mm=nozzle_mm,
                                     layer_mm=layer_mm, thorough=thorough)

    @_guarded
    def detect_comfyui(self) -> dict:
        from scripts.install_comfyui_nodes import detect_comfyui_installations
        return {"success": True, "paths": detect_comfyui_installations()}

    @_guarded
    def install_comfyui_nodes(self, target_path: str) -> dict:
        from scripts.install_comfyui_nodes import install_nodes
        res = install_nodes(target_path)
        if res.get("success"):
            log(f"Installed ComfyUI custom nodes to: {res['destination']}")
        else:
            log(f"Failed to install ComfyUI custom nodes: {res.get('error')}", "error")
        return res

    # ------------------------------------------------------------------ operations
    @_guarded
    def load_model_file(self, file_path: str) -> dict:
        result = self.svc.load(file_path)
        if result.get("success"):
            from engine import browse
            browse.remember(file_path)      # so the browser can offer it again next time
        return result

    @_guarded
    def load_demo_model(self) -> dict:
        """Built-in test object, so a fresh install can be tried without a file."""
        return self.svc.load_demo()

    @_guarded
    def close_model(self) -> dict:
        """Empty the workspace: no model, no history, no textures."""
        return self.svc.clear()

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
    def set_preview_detail(self, fraction=None) -> dict:
        """How much of the model the viewport draws. Display only."""
        return self.svc.set_preview_detail(fraction)

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

    # ------------------------------------------------------------------ texture & PBR
    @_guarded
    def unwrap_model_uvs(self, force: bool = False) -> dict:
        """Unwrap UVs. Returns needs_confirm instead of acting if UVs already exist."""
        return self.svc.unwrap_uvs(force=bool(force))

    @_guarded
    def generate_pbr_maps(self, image_path: str = "", normal_strength: float = 2.5,
                          opengl_normal: bool = True, min_roughness: float = 0.2,
                          max_roughness: float = 0.85, metallic: float = 0.0,
                          ao_intensity: float = 1.0, tileable: bool = False) -> dict:
        path = image_path.strip() if image_path else self.select_image_dialog()
        if not path:
            return {"success": False, "canceled": True}
        return self.svc.generate_pbr_textures(
            path,
            normal_strength=float(normal_strength),
            opengl_normal=bool(opengl_normal),
            min_roughness=float(min_roughness),
            max_roughness=float(max_roughness),
            metallic=float(metallic),
            ao_intensity=float(ao_intensity),
            tileable=bool(tileable),
        )

    @_guarded
    def export_texture_folder(self) -> dict:
        dir_path = self.select_folder_dialog()
        if not dir_path:
            return {"success": False, "canceled": True}
        return self.svc.export_texture_pack(dir_path)

    @_guarded
    def reload_texture_folder(self, folder_path: str = "") -> dict:
        return self.svc.reload_texture_pack(folder_path or None)

    @_guarded
    def get_texture_state(self) -> dict:
        """Light summary: what channels exist and their version, no pixels."""
        return self.svc.get_texture_state()

    @_guarded
    def get_texture_maps(self) -> dict:
        """The base64 maps. Only worth calling when texture_version has moved."""
        return self.svc.get_texture_maps()

    @_guarded
    def get_uv_layout(self) -> dict:
        """UV wireframe for the 2D unfold view."""
        return self.svc.get_uv_layout()

    @_guarded
    def export_baked_glb(self) -> dict:
        self.svc._require()
        default_name = self.svc.default_fixed_export_name("-textured.glb")
        path = self._save_dialog(default_name, ('Binary glTF (*.glb)', 'All files (*.*)'))
        if not path:
            return {"success": False, "canceled": True}
        return self.svc.bake_and_export_glb(path)

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
        from importlib.metadata import PackageNotFoundError, version
        libs = {}
        for name, dist in (("trimesh", "trimesh"), ("NumPy", "numpy"), ("SciPy", "scipy"), ("PyMeshLab", "pymeshlab"),
                           ("Manifold3D", "manifold3d"), ("pymeshfix (MeshFix)", "pymeshfix"),
                           ("fast-simplification", "fast-simplification"), ("pyQuadriFlow", "pyQuadriFlow"), ("scikit-image", "scikit-image"),
                           ("pywebview", "pywebview"), ("mcp", "mcp"), ("ufbx", "ufbx")):
            try:
                libs[name] = version(dist)
            except PackageNotFoundError:
                libs[name] = None
        return {"success": True, "version": __version__, "python": platform.python_version(),
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
        """Backward-compatible entry point for scripts and the MCP examples."""
        return self.export_model_file("stl", scale_unit, align_origin)

    @_guarded
    def export_model_file(self, export_format: str = "stl", scale_unit: str = "mm", align_origin: bool = True) -> dict:
        self.svc._require()
        export_formats = {
            "stl": ("STL", "*.stl"), "obj": ("Wavefront OBJ", "*.obj"),
            "ply": ("PLY", "*.ply"), "off": ("OFF", "*.off"),
            "glb": ("Binary glTF", "*.glb"), "gltf": ("glTF", "*.gltf"),
            "3mf": ("3MF", "*.3mf"),
        }
        fmt = str(export_format).lower()
        if fmt not in export_formats:
            raise ValidationError("Export format is not supported.")
        label, pattern = export_formats[fmt]
        path = self._save_dialog(self.svc.default_fixed_export_name(f".{fmt}"),
                                 (f'{label} ({pattern})', 'All files (*.*)'))
        if not path:
            return {"success": False, "canceled": True}
        return self.svc.export_model(path, fmt, scale_unit, align_origin)

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


def _show_message(title: str, text: str, ask: bool = False) -> bool:
    """
    A native message box; with `ask`, a Yes/No one that returns whether Yes was chosen.

    The installed program is a windowed exe with no console, so anything printed is
    never seen. This is the only way a person finds out why nothing happened.
    """
    if os.name != "nt":
        print(f"{title}\n{text}", flush=True)
        return False
    import ctypes
    MB_YESNO, MB_ICONQUESTION, MB_ICONERROR, IDYES = 0x4, 0x20, 0x10, 6
    flags = (MB_YESNO | MB_ICONQUESTION) if ask else MB_ICONERROR
    answer = ctypes.windll.user32.MessageBoxW(None, text, title, flags)
    return answer == IDYES


def _webview2_missing() -> bool:
    """
    True when pywebview would fall back to the Internet Explorer engine.

    It never raises for a missing WebView2 runtime. It logs one line and opens a window
    drawn by MSHTML, which cannot run this interface, so the person gets a broken blank
    window and no explanation. The choice is made when its `winforms` backend is
    imported, which `webview.start()` does anyway — doing it a moment earlier lets us
    say something useful first. If anything about that is unexpected the answer is
    "no", and start-up carries on exactly as it always did.
    """
    if os.name != "nt":
        return False
    try:
        from webview.platforms import winforms
    except Exception:
        return False
    return getattr(winforms, "renderer", None) == "mshtml"


def _explain_window_failure(reason: str, detail: str = "") -> None:
    """Tell the person what is missing and offer to open the page that fixes it."""
    print(flush=True)
    print(f"Meshwright could not open its window: {reason}", flush=True)
    print("Meshwright draws its window with the Microsoft Edge WebView2 runtime.", flush=True)
    print(f"It is free from Microsoft: {WEBVIEW2_URL}", flush=True)
    text = (
        "Meshwright draws its window with the Microsoft Edge WebView2 runtime, and this "
        "computer does not have a working copy.\n\n"
        "It is free from Microsoft and already part of Windows 11. Installing it takes under "
        "a minute, and then Meshwright will start normally.\n\n"
        + (f"Technical detail: {detail}\n\n" if detail else "")
        + "Open the download page now?"
    )
    if _show_message("Meshwright cannot open its window", text, ask=True):
        import webbrowser
        webbrowser.open(WEBVIEW2_URL)


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if _webview2_missing():
        _explain_window_failure("the WebView2 runtime is not installed")
        return 1
    api = AppApi()
    ui_dir = resource_path('ui')

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
    try:
        webview.start(debug=False)
    except Exception as exc:
        # The window itself failed to open — almost always a missing web view
        # runtime rather than anything to do with meshes. Say which.
        traceback.print_exc()
        _explain_window_failure(str(exc), detail=str(exc))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
