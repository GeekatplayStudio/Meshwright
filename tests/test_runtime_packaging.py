"""
The pieces that only matter once Meshwright is frozen into Meshwright.exe.

A frozen build cannot be exercised from a normal test run, so the branches that differ
are tested by flipping the flag. The end-to-end proof — that the real exe works — is
`Meshwright.exe --selftest`, which packaging\\build.ps1 runs on every build.
"""
import os
import subprocess
import sys

import pytest

import app
from engine import runtime
from engine.version import __version__


class _Left(Exception):
    """Stands in for os._exit, which would otherwise end the test run itself."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


@pytest.fixture
def no_real_exit(monkeypatch):
    def fake_exit(code):
        raise _Left(code)
    monkeypatch.setattr(os, "_exit", fake_exit)


# ------------------------------------------------------------------ helper command lines
def test_from_source_the_helper_command_is_exactly_what_it_always_was():
    """Running from source must not change: the child is `python script in out`."""
    assert not runtime.FROZEN
    argv = runtime.worker_argv("xatlas", "/some/_xatlas_worker.py", "in.npz", "out.npz")
    assert argv == [sys.executable, "/some/_xatlas_worker.py", "in.npz", "out.npz"]


def test_frozen_the_helper_is_the_exe_itself_with_a_flag(monkeypatch):
    monkeypatch.setattr(runtime, "FROZEN", True)
    argv = runtime.worker_argv("quadriflow", "/ignored.py", "in.npz", "out.npz")
    assert argv == [sys.executable, runtime.WORKER_FLAG, "quadriflow", "in.npz", "out.npz"]
    assert "/ignored.py" not in argv, "the script does not exist inside a frozen program"


def test_helpers_never_flash_a_console_window():
    flags = runtime.subprocess_flags()
    if os.name == "nt":
        assert flags["creationflags"] & subprocess.CREATE_NO_WINDOW
    else:
        assert flags == {}


def test_every_registered_helper_is_importable_and_takes_two_paths():
    """The frozen build finds helpers by name, so a rename would break it silently."""
    import importlib
    import inspect
    for name, (module, function) in runtime.WORKERS.items():
        job = getattr(importlib.import_module(module), function)
        assert len(inspect.signature(job).parameters) == 2, name


# ------------------------------------------------------------------ recognising a helper
def test_an_ordinary_launch_is_left_alone(no_real_exit):
    assert runtime.run_worker_if_requested(["Meshwright.exe"]) is None
    assert runtime.run_worker_if_requested(["Meshwright.exe", "--mcp"]) is None
    # right flag, wrong shape: not ours to run
    assert runtime.run_worker_if_requested(["x", runtime.WORKER_FLAG, "xatlas"]) is None


def test_a_helper_runs_its_job_and_leaves_with_its_exit_code(monkeypatch, no_real_exit):
    seen = {}

    def job(in_path, out_path):
        seen["paths"] = (in_path, out_path)
        return 0

    fake = type(sys)("fake_worker")
    fake.main = job
    monkeypatch.setitem(sys.modules, "fake_worker", fake)
    monkeypatch.setitem(runtime.WORKERS, "fake", ("fake_worker", "main"))

    with pytest.raises(_Left) as left:
        runtime.run_worker_if_requested(["x", runtime.WORKER_FLAG, "fake", "a.npz", "b.npz"])
    assert left.value.code == 0
    assert seen["paths"] == ("a.npz", "b.npz")


def test_a_helper_that_fails_reports_failure_rather_than_success(monkeypatch, no_real_exit):
    def job(in_path, out_path):
        raise ValueError("the mesh was not manifold")

    fake = type(sys)("fake_failing")
    fake.main = job
    monkeypatch.setitem(sys.modules, "fake_failing", fake)
    monkeypatch.setitem(runtime.WORKERS, "boom", ("fake_failing", "main"))

    with pytest.raises(_Left) as left:
        runtime.run_worker_if_requested(["x", runtime.WORKER_FLAG, "boom", "a", "b"])
    assert left.value.code == 1


def test_an_unknown_helper_name_is_an_error_not_a_crash(no_real_exit):
    with pytest.raises(_Left) as left:
        runtime.run_worker_if_requested(["x", runtime.WORKER_FLAG, "nonsense", "a", "b"])
    assert left.value.code == 1


# ------------------------------------------------------------------ where files are
def test_resource_path_finds_the_interface_from_source():
    assert os.path.exists(runtime.resource_path("ui", "index.html"))


def test_resource_path_uses_the_bundle_folder_when_frozen(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, "FROZEN", True)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert runtime.resource_path("ui", "index.html") == str(tmp_path / "ui" / "index.html")


# ------------------------------------------------------------------ one version, everywhere
def test_the_about_panel_and_the_mcp_server_report_the_same_version():
    assert app.AppApi.about_info(app.AppApi.__new__(app.AppApi))["version"] == __version__


# ------------------------------------------------------------------ WebView2 missing
class _Winforms:
    def __init__(self, renderer):
        self.renderer = renderer


def _pretend_backend(monkeypatch, renderer):
    """Install a stand-in for pywebview's Windows backend that has chosen `renderer`."""
    from webview import platforms
    fake = _Winforms(renderer)
    monkeypatch.setitem(sys.modules, "webview.platforms.winforms", fake)
    monkeypatch.setattr(platforms, "winforms", fake, raising=False)


@pytest.mark.skipif(os.name != "nt", reason="the WebView2 check only exists on Windows")
def test_the_internet_explorer_fallback_is_treated_as_a_missing_runtime(monkeypatch):
    _pretend_backend(monkeypatch, "mshtml")
    assert app._webview2_missing() is True


@pytest.mark.skipif(os.name != "nt", reason="the WebView2 check only exists on Windows")
def test_a_working_runtime_is_not_reported_missing(monkeypatch):
    _pretend_backend(monkeypatch, "edgechromium")
    assert app._webview2_missing() is False


@pytest.mark.skipif(os.name != "nt", reason="the WebView2 check only exists on Windows")
def test_if_the_check_cannot_be_made_startup_carries_on_as_before(monkeypatch):
    """Any surprise must fall back to 'not missing', never block a working machine."""
    monkeypatch.setitem(sys.modules, "webview.platforms.winforms", None)   # makes the import raise
    assert app._webview2_missing() is False


def test_the_missing_runtime_dialog_offers_the_download_and_opens_it_on_yes(monkeypatch):
    shown = {}
    opened = []

    def fake_message(title, text, ask=False):
        shown.update(title=title, text=text, ask=ask)
        return True

    monkeypatch.setattr(app, "_show_message", fake_message)
    import webbrowser
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url))

    app._explain_window_failure("the WebView2 runtime is not installed")
    assert shown["ask"] is True
    assert "WebView2" in shown["text"] and "free" in shown["text"]
    assert opened == [app.WEBVIEW2_URL]


def test_saying_no_to_the_dialog_does_not_open_a_browser(monkeypatch):
    monkeypatch.setattr(app, "_show_message", lambda *a, **k: False)
    import webbrowser
    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url))
    app._explain_window_failure("x", detail="boom")
    assert opened == []


# ------------------------------------------------------------------ startup failures are visible
def test_a_startup_crash_is_written_down_and_shown(monkeypatch, tmp_path):
    """The installed program has no console; without this it would just vanish."""
    import ctypes
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "mw_launcher", os.path.join(os.path.dirname(os.path.dirname(__file__)), "packaging", "launcher.py"))
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    boxes = []

    class _User32:
        @staticmethod
        def MessageBoxW(hwnd, text, title, flags):
            boxes.append((title, text))
            return 1

    class _Windll:
        user32 = _User32()

    monkeypatch.setattr(ctypes, "windll", _Windll(), raising=False)
    try:
        raise ImportError("DLL load failed while importing scipy")
    except ImportError as failure:
        launcher._report_startup_failure(failure)

    report = tmp_path / "Meshwright" / "startup-error.txt"
    assert report.exists() and "DLL load failed" in report.read_text(encoding="utf-8")
    if os.name == "nt":
        assert boxes and "could not start" in boxes[0][1] and str(report) in boxes[0][1]


# ------------------------------------------------------------------ the self-test itself
def test_the_self_test_passes_from_source(tmp_path):
    """
    The same check the build runs against the frozen exe. From source it doubles as
    proof that every engine in this environment really works.
    """
    import json

    from engine.selftest import run
    out = tmp_path / "selftest.json"
    code = run(str(out))
    report = json.loads(out.read_text(encoding="utf-8"))
    failed = [s for s in report["steps"] if s["status"] == "FAILED"]
    assert code == 0 and not failed, failed
    assert report["version"] == __version__
    assert any(s["status"] == "passed" for s in report["steps"])
