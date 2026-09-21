"""
Where Meshwright is running from, and how it starts its helper processes.

There are two ways of being run: from source (`python app.py`, or start.bat) and frozen
into Meshwright.exe by PyInstaller. Almost nothing has to care which, but three things do.

Data files
    Beside the source they sit next to the modules. Frozen, PyInstaller unpacks them
    into a folder of its own (`sys._MEIPASS`), and `__file__` no longer means what it
    says on the tin.

Helper processes
    QuadriFlow and xatlas run in a child process so that a native crash becomes an exit
    code instead of a closed window. From source the child is `python worker.py`.
    Frozen there is no Python and no worker.py on disk, and `sys.executable` is
    Meshwright.exe itself — so the same exe is started again with a flag saying "you are
    a worker", and the launcher hands it straight to the right job before the window
    machinery has been imported.

No console
    The frozen program is a windowed exe. A child started from a windowed parent gets a
    console window of its own unless it is told not to, and one would flash on screen
    for every retopology.

This module imports nothing but the standard library, so a helper process can reach it
without paying for numpy, scipy or anything else.
"""
import contextlib
import importlib
import os
import subprocess
import sys

FROZEN = bool(getattr(sys, "frozen", False))

# What the launcher looks for. Anything else in argv[1] is left alone.
WORKER_FLAG = "--meshwright-worker"

# name -> (module, function). Frozen builds import these by name, so the spec lists
# them as hidden imports; from source they are still run by file path.
WORKERS = {
    "quadriflow": ("engine._quadriflow_worker", "main"),
    "xatlas": ("engine.texture._xatlas_worker", "main"),
}


def app_root() -> str:
    """The folder that holds `ui/`, `comfyui_nodes/` and the licence texts."""
    if FROZEN:
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resource_path(*parts: str) -> str:
    """A path inside the program's own files, correct whether frozen or not."""
    return os.path.join(app_root(), *parts)


def worker_argv(name: str, script_path: str, in_path: str, out_path: str) -> list:
    """
    The command line that starts a helper process.

    `script_path` is only used from source, where the helper is a plain script. It is
    kept as a parameter so the source-run behaviour stays exactly what it always was.
    """
    if FROZEN:
        return [sys.executable, WORKER_FLAG, name, in_path, out_path]
    return [sys.executable, script_path, in_path, out_path]


def subprocess_flags() -> dict:
    """Extra `subprocess.run` keywords: never pop a console window up for a helper."""
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def _say(text: str) -> None:
    """
    Write to the parent's pipe even when there is no `sys.stderr`.

    A windowed exe may have `sys.stderr` set to None, and the parent reads the last
    line of the pipe to tell the user why a helper failed. Going through the file
    descriptor reaches it regardless.
    """
    data = (text.rstrip() + "\n").encode("utf-8", "replace")
    try:
        os.write(2, data)
    except OSError:
        pass


def run_worker_if_requested(argv=None) -> None:
    """
    If this process was started as a helper, run that job and exit; otherwise return.

    Called first thing by the launcher, before any heavy import.
    """
    argv = sys.argv if argv is None else argv
    if len(argv) != 5 or argv[1] != WORKER_FLAG:
        return

    _, _, name, in_path, out_path = argv
    code = 1
    try:
        module, function = WORKERS[name]
        job = getattr(importlib.import_module(module), function)
        code = int(job(in_path, out_path) or 0)
    except KeyError:
        _say(f"unknown helper '{name}'")
    except SystemExit as stop:
        code = stop.code if isinstance(stop.code, int) else 1
    except BaseException as exc:                      # noqa: BLE001 - report anything, then leave
        _say(f"{type(exc).__name__}: {exc}")
        code = 1

    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):          # a windowed exe may have no stream at all
            stream.flush()
    # Leave without unwinding. The native libraries these jobs load have a habit of
    # faulting while they are torn down, which would turn a finished job into a crash
    # and lose the exit code the parent is waiting for.
    os._exit(code)
