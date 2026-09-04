"""
The viewport renders on demand, not continuously — so anything that changes what is
on screen has to ask for a frame.

This is a structural check on ui/js/viewer.js rather than a behavioural test: there
is no JS runtime in this suite, and standing one up to assert a boolean flips is more
machinery than the invariant is worth. What it does catch is the failure that
actually happened — a method that mutates the scene, a material, the camera or a
light, and forgets to call requestRender(), leaving a panel control looking dead
until the user happens to orbit and orbit damping produces a frame by accident.
"""
import re
from pathlib import Path

import pytest

VIEWER = Path(__file__).resolve().parents[1] / "ui" / "js" / "viewer.js"

# Touching any of these changes what the next frame would look like.
MUTATES = re.compile(
    r"this\.(?:scene|mesh|wire|edgeLines|modelGroup|grid|camera|key|fill|renderer|"
    r"materials|highlightGroup|gizmo|displaceMm)\b\s*[.=]"
    r"|\.material\s*=|\.visible\s*=|needsUpdate\s*=|\.position\.(?:set|copy)\(",
)
INVALIDATES = re.compile(r"requestRender\(\)|needsRender\s*=\s*true")

# Methods that legitimately touch those without needing a frame of their own.
EXEMPT = {
    "constructor",   # init() sets needsRender directly
    "init",
    "animate",       # this is the thing that renders
    "requestRender",
    "drawCompass",   # only ever called from animate(), after the render
    "toViewer",      # pure read
    "modelMatrix",   # pure read
    "worldSphere",   # pure read
    "hasHeightMap",  # pure read
    "updateLight",   # helper; every caller invalidates, and it does too
}


def _methods():
    """(name, body) for every class method in viewer.js, in source order."""
    source = VIEWER.read_text(encoding="utf-8")
    starts = [(m.start(), m.group(1))
              for m in re.finditer(r"^    ([A-Za-z_]\w*)\s*\(", source, re.MULTILINE)]
    assert starts, "no class methods found — has viewer.js been restructured?"
    for i, (pos, name) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(source)
        yield name, source[pos:end]


SCENE_MUTATORS = [(n, b) for n, b in _methods() if n not in EXEMPT and MUTATES.search(b)]


def test_the_scan_found_something_to_check():
    """Guards against the regex silently matching nothing after a refactor."""
    assert len(SCENE_MUTATORS) >= 12, [n for n, _ in SCENE_MUTATORS]


@pytest.mark.parametrize("name,body", SCENE_MUTATORS, ids=[n for n, _ in SCENE_MUTATORS])
def test_scene_mutators_request_a_frame(name, body):
    assert INVALIDATES.search(body), (
        f"ModelViewer.{name}() changes the scene but never calls requestRender(), "
        f"so its effect will not appear until something else triggers a frame."
    )


def test_the_render_loop_is_still_demand_driven():
    """A continuous loop burns CPU and GPU on an idle desktop app."""
    source = VIEWER.read_text(encoding="utf-8")
    animate = source[source.index("    animate("):source.index("    requestRender(")]
    assert "requestAnimationFrame" in animate
    assert re.search(r"if\s*\(.*needsRender.*\)\s*\{", animate), \
        "animate() renders unconditionally again"


def test_controls_invalidate_on_change():
    """Orbiting has to produce frames, and that comes from the controls' own event."""
    source = VIEWER.read_text(encoding="utf-8")
    assert re.search(r"controls\.addEventListener\(\s*'change'", source)


def test_async_texture_loads_invalidate():
    """
    Textures arrive after the frame that requested them. Without a callback the
    model sits untextured until the user moves the camera.
    """
    source = VIEWER.read_text(encoding="utf-8")
    loader = source[source.index("applyPBRTextures("):]
    assert re.search(r"loader\.load\([^)]*,\s*\(\)\s*=>", loader), \
        "TextureLoader.load() has no onLoad callback to request a frame"
