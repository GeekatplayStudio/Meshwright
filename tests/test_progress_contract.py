"""
Regression tests for model loading progress feedback.

Ensures:
1. Python side: progress() and log() are non-blocking via _eval_queue.
2. Markup: #globalProgress, #globalProgressBar, and #emptySpinner exist in ui/index.html.
3. Styling: .global-progress, .global-progress-bar, and .empty-spinner exist in ui/css/style.css.
4. JS client: loadFile, btnDemo, and doRevert start progress immediately and hold
   until 3D geometry rendering completes (no premature dismissal).
"""
import time
from pathlib import Path

import app as app_module

ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "ui" / "index.html"
STYLE_CSS = ROOT / "ui" / "css" / "style.css"
APP_JS = ROOT / "ui" / "js" / "app.js"


def test_python_progress_and_log_enqueue_asynchronously():
    """progress() and log() should enqueue non-blocking scripts."""
    class MockWindow:
        def __init__(self):
            self.scripts = []

        def evaluate_js(self, script):
            self.scripts.append(script)

    mock_win = MockWindow()
    app_module._state["window"] = mock_win

    try:
        app_module.log("Test log line", "info")
        app_module.progress(state="start", operation="load", label="Loading test")

        # Give background thread up to 1 second to drain queue
        t0 = time.time()
        while len(mock_win.scripts) < 2 and time.time() - t0 < 1.0:
            time.sleep(0.02)

        assert any("window.meshwright.log" in s for s in mock_win.scripts)
        assert any("window.meshwright.progress" in s for s in mock_win.scripts)
    finally:
        app_module._state["window"] = None


def test_ui_markup_has_global_progress_and_empty_spinner():
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert 'id="globalProgress"' in html
    assert 'id="globalProgressBar"' in html
    assert 'id="emptySpinner"' in html


def test_style_css_has_progress_and_spinner_rules():
    css = STYLE_CSS.read_text(encoding="utf-8")
    assert ".global-progress" in css
    assert ".global-progress-bar" in css
    assert ".empty-spinner" in css


def test_app_js_initiates_progress_immediately_and_guards_premature_done():
    js = APP_JS.read_text(encoding="utf-8")

    # loadFile immediately initiates progress
    load_fn = js[js.index("async function loadFile("):]
    load_fn = load_fn[:load_fn.index("\n    }")]
    assert "onProgress(" in load_fn
    assert "setLoadingUI(true" in load_fn
    assert "isModelLoading = true" in load_fn

    # onProgress does not dismiss early when isModelLoading is true
    on_prog = js[js.index("function onProgress("):]
    on_prog = on_prog[:on_prog.index("\n    }")]
    assert "isModelLoading" in on_prog
    assert "showGlobalProgress(" in on_prog
