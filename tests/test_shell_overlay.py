"""
Piece colours must not sit on top of a texture.

A structural check on the UI sources, in the style of test_viewport_invalidation.py:
there is no JS runtime in this suite, and what needs guarding is a wiring rule rather
than a computation.

The rule exists because of a failure that was invisible from every direction. The
shell overlay paints per-piece vertex colours directly onto the mesh material, and
`Viewer.setMode` deliberately will not fight that — it records the new mode and skips
the material assignment while `shellMode` is on. `shellHighlight` starts true, so any
model in two or more pieces switched the overlay on the moment it loaded, before its
textures had finished arriving.

The result on a 49-piece model with an 8192x8192 embedded texture: the map was
decoded, the UVs were on the geometry, `viewer.mode` read "pbr", the PBR button was
lit — and the model drew flat grey, because the overlay was over all of it. Nothing
reported an error, and clicking PBR again did nothing either.
"""
import re
from pathlib import Path

import pytest

APP_JS = Path(__file__).resolve().parents[1] / "ui" / "js" / "app.js"
VIEWER_JS = Path(__file__).resolve().parents[1] / "ui" / "js" / "viewer.js"


@pytest.fixture(scope="module")
def app_js():
    return APP_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def viewer_js():
    return VIEWER_JS.read_text(encoding="utf-8")


def test_the_overlay_really_does_block_a_shading_mode(viewer_js):
    """
    The premise of everything below. If setMode ever starts applying the material
    regardless, these tests are guarding a problem that no longer exists and should
    be revisited rather than left to pass for the wrong reason.
    """
    body = viewer_js[viewer_js.index("setMode(mode) {"):]
    body = body[:body.index("\n    }")]
    assert "shellMode" in body, \
        "setMode no longer consults shellMode — re-check whether this file still applies"


def test_a_textured_model_opens_showing_its_texture(app_js):
    show = app_js[app_js.index("function showModel(res) {"):]
    show = show[:show.index("\n    }")]
    assert "dropShellHighlight()" in show, \
        "a model that arrives with textures still opens under the piece-colour overlay"
    assert "has_textures" in show, "the overlay is dropped unconditionally, even with no texture"


def test_choosing_a_shading_mode_takes_the_overlay_down(app_js):
    """Otherwise the button lights up and the viewport does not change."""
    handler = app_js[app_js.index("button[data-mode]').forEach(b => b.addEventListener"):]
    handler = handler[:handler.index("}));")]
    assert "dropShellHighlight()" in handler, \
        "clicking a shading mode leaves the overlay up, so the click does nothing visible"
    assert handler.index("dropShellHighlight()") < handler.index("setMode("), \
        "the overlay must come down before setMode, or setMode still skips the material"


def test_dropping_the_overlay_keeps_the_button_honest(app_js):
    """
    The Highlight button is how the piece colours come back, so it has to stop looking
    pressed when something else has taken them away.
    """
    fn = app_js[app_js.index("function dropShellHighlight() {"):]
    fn = fn[:fn.index("\n    }")]
    assert "shellHighlight = false" in fn
    assert re.search(r"btnShellsHighlight[\s\S]*remove\(\s*'active'", fn), \
        "the Highlight button would still look active with the overlay gone"
    assert "hideShells()" in fn, "the viewer is never told to put the material back"


def test_hiding_the_shells_restores_whatever_mode_was_chosen(viewer_js):
    """
    hideShells is the other half: it has to reapply the current mode's material, or
    dropping the overlay would leave the mesh on the shell material for ever.
    """
    fn = viewer_js[viewer_js.index("hideShells() {"):]
    fn = fn[:fn.index("\n    }")]
    assert "this.materials[this.mode]" in fn, \
        "hideShells does not put the chosen shading mode back on the mesh"
