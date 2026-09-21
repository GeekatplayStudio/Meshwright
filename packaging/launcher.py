r"""
Entry point of the installed program - PyInstaller builds Meshwright.exe from this file.

It is not app.py, deliberately. The same exe is started again as a helper process for
QuadriFlow and xatlas, and a helper has to be recognised and handed to its job before
anything heavy is imported: the window machinery takes a second or more to load and a
helper has no use for it.

    Meshwright.exe                      the program
    Meshwright.exe --mcp                MCP server on stdin/stdout, for AI assistants
    Meshwright.exe --selftest           check that everything packaged actually works
    Meshwright.exe --selftest-out F     ...and write the report to F
    Meshwright.exe --version
"""
import contextlib
import os
import sys

if not getattr(sys, "frozen", False):
    # Run from source (`python packaging/launcher.py`): make the project importable.
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.runtime import run_worker_if_requested

run_worker_if_requested()      # exits here if this process is a helper


def _value_of(flag, argv):
    """The argument after `flag`, or None."""
    for i, item in enumerate(argv[:-1]):
        if item == flag:
            return argv[i + 1]
    return None


def _app_data_dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    path = os.path.join(base, "Meshwright")
    os.makedirs(path, exist_ok=True)
    return path


def _report_startup_failure(exc):
    """
    Say what went wrong. The program has no console, so an exception during start-up
    would otherwise end it with no window and no explanation.
    """
    import traceback
    text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    where = None
    with contextlib.suppress(Exception):
        where = os.path.join(_app_data_dir(), "startup-error.txt")
        with open(where, "w", encoding="utf-8") as handle:
            handle.write(text)
    if os.name == "nt":
        with contextlib.suppress(Exception):
            import ctypes
            message = (
                "Meshwright could not start.\n\n"
                f"{type(exc).__name__}: {exc}\n\n"
                + (f"The full details were saved to:\n{where}\n\n" if where else "")
                + "Reinstalling Meshwright usually fixes this. If it does not, send that file "
                  "to Geekatplay Studio."
            )
            ctypes.windll.user32.MessageBoxW(None, message, "Meshwright", 0x10)


def main(argv):
    if "--version" in argv:
        from engine.version import __version__
        with contextlib.suppress(Exception):
            print(__version__, flush=True)
        return 0

    if "--selftest" in argv or "--selftest-out" in argv:
        from engine.selftest import run
        return run(_value_of("--selftest-out", argv))

    if "--mcp" in argv:
        import mcp_server
        mcp_server.mcp.run()
        return 0

    import app
    return app.main()


if __name__ == "__main__":
    try:
        code = main(sys.argv[1:])
    except SystemExit:
        raise
    except BaseException as failure:
        _report_startup_failure(failure)
        code = 1

    if getattr(sys, "frozen", False):
        # The window is closed and everything worth saving is saved. The native
        # libraries loaded by now (Qt inside MeshLab, the .NET bridge) can fault while
        # they are torn down, and that would show Windows' "has stopped working"
        # dialog over a program that finished normally.
        for stream in (sys.stdout, sys.stderr):
            with contextlib.suppress(Exception):
                stream.flush()
        os._exit(code if isinstance(code, int) else 0)
    sys.exit(code)
