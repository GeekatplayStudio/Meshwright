"""
The walking cup: where he can be, and what has to be true of the drawings.

Like tests/test_viewport_invalidation.py this reads the source rather than running
it — there is no JS runtime in this suite. It guards the things that have actually
gone wrong: he teleported across the window when a second job started while he was
walking out, and the sprite window fell out of step with the sheet when a new set of
drawings cut to a different width, which shows every frame sliced down the middle.
"""
import re
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
WALKER = ROOT / "ui" / "js" / "walker.js"
APP_JS = ROOT / "ui" / "js" / "app.js"
STYLE = ROOT / "ui" / "css" / "style.css"
STRIP = ROOT / "ui" / "assets" / "walk" / "walk-strip.png"
ENGINE = (ROOT / "engine" / "service.py", ROOT / "engine" / "texture_service.py")

# The three places he can be. They drive different animations, so he must never
# wear two at once.
MOTION = ("walking", "resuming", "leaving")


@pytest.fixture(scope="module")
def js():
    return WALKER.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def css():
    return STYLE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def app_js():
    return APP_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def walks_for(app_js):
    """The operations that bring him out, as ui/js/app.js lists them."""
    listed = re.search(r"WALKS_FOR\s*=\s*new Set\(\[([^\]]*)\]\)", app_js).group(1)
    return set(re.findall(r"'([^']+)'", listed))


def body(source, name):
    """The text of one top-level function inside the module's IIFE."""
    start = source.index(f"function {name}(")
    end = source.index("\n    }", start)
    return source[start:end]


# ------------------------------------------------------------------ where he is
def test_he_is_picked_up_rather_than_teleported(js):
    """
    A job starting while he walks out must continue from where he stands. Restarting
    the crossing instead snaps him back to the right edge — on screen he vanishes
    from one side of the window and reappears on the other.
    """
    start = body(js, "start")
    assert "leaving" in start, "start() does not consider that he may be walking out"
    assert "currentX()" in start or "walkOutFrom(currentX())" in start, \
        "start() never asks where he is, so it can only restart him from the edge"
    assert "resuming" in start, "there is no state for finishing the walk he was on"


def test_finishing_the_work_sends_him_off_from_either_state(js):
    """He can be mid-crossing or mid-resume; both have to lead to the door."""
    stop = body(js, "stop")
    for state in ("walking", "resuming"):
        assert state in stop, f"stop() ignores him when he is {state}"
    assert "currentX()" in stop or "walkOutFrom(currentX())" in stop, \
        "stop() does not start his exit from where he actually is"


def test_clearing_the_workspace_leaves_him_in_no_state_at_all(js):
    reset = body(js, "reset")
    for state in MOTION:
        assert state in reset, f"reset() leaves the {state!r} class on him"


def test_every_state_the_script_uses_is_styled(js, css):
    """A rename on one side and not the other leaves him invisible or motionless."""
    for state in MOTION:
        assert f".walker.{state}" in css, f"the CSS has no rule for {state!r}"
        assert f"'{state}'" in js, f"walker.js never puts him in {state!r}"


def test_the_exit_animation_hands_back_to_the_walk(js):
    """
    Both the resume and the exit run walker-exit, so the handler that fires when it
    ends must tell them apart — otherwise a resumed walk stops dead at the left edge.
    """
    handler = js[js.index("animationend"):]
    assert "walker-exit" in handler, "nothing watches for the exit animation ending"
    assert "resuming" in handler and "leaving" in handler, \
        "the animationend handler does not distinguish resuming from leaving"


# ------------------------------------------------------------- when he turns out
def test_he_walks_for_the_model_not_for_every_step(walks_for):
    """
    Every long operation emits progress. He is only company for the waits that are
    about the model as a whole — opening one and writing one out. Hanging him off all
    of them put him on screen for most of a session, which makes him scenery instead
    of a signal that something is happening.
    """
    assert walks_for == {"load", "export", "bake_glb"}, (
        f"he now turns out for {sorted(walks_for)}; repairs, reductions, unwraps and "
        f"previews are meant to show the progress toast and nothing else"
    )


def test_starting_and_stopping_agree_on_that_set(app_js):
    """
    If the gate covered only `start`, a repair finishing would count down the load he
    is actually walking for and send him off mid-model.
    """
    handler = app_js[app_js.index("function onProgress("):]
    handler = handler[:handler.index("\n    }")]
    gate = handler.index("WALKS_FOR.has(")
    for call in ("walker().start(", "walker().stop()"):
        assert handler.index(call) > gate, f"{call} is not behind the WALKS_FOR gate"


def test_the_operations_he_waits_on_are_ones_the_engine_emits(walks_for):
    """
    The names are a contract between Python and the browser, matched as strings. A
    rename on the engine side would not break anything loudly — the cup would simply
    stop turning up, which nobody would report as a bug.
    """
    emitted = set()
    for path in ENGINE:
        source = path.read_text(encoding="utf-8")
        emitted |= set(re.findall(r"_job\(\s*[\"']([a-z_]+)[\"']", source))
        emitted |= set(re.findall(r"operation=[\"']([a-z_]+)[\"']", source))

    missing = walks_for - emitted
    assert not missing, (
        f"app.js waits on {sorted(missing)}, which no engine job reports — "
        f"the engine emits {sorted(emitted)}"
    )


# ------------------------------------------------------------------ the balloon
def test_the_offer_takes_itself_down(js):
    """
    Clicking him stops him where he stands. If nothing ever dismisses the balloon he
    stays stopped for the rest of the session.
    """
    toggle = body(js, "toggleBalloon")
    assert re.search(r"setTimeout\(\s*hideBalloon", toggle), \
        "the balloon has no timer, so it only closes if the user notices it"


def test_dismissing_it_cancels_the_timer_and_starts_him_walking(js):
    hide = body(js, "hideBalloon")
    assert "clearTimeout" in hide, \
        "a stale timer will close a balloon the user has just reopened"
    assert re.search(r"remove\(\s*'paused'", hide), \
        "he is never let go, so he stands still after the balloon closes"


# ------------------------------------------------------------------ the drawings
def test_the_sprite_window_matches_the_sheet(css):
    """
    The strip is one image, N frames wide, inside a window one frame wide. If the
    declared frame width is not the real one the window shows parts of two drawings
    and every step lands further out of register than the last.

    scripts/build_walk_frames.py prints this number after it cuts a sheet; this is
    what makes forgetting to carry it across a failure rather than a puzzle.
    """
    frames = int(re.search(r"--walk-frames:\s*(\d+)", css).group(1))
    declared = int(re.search(r"--walk-frame-w:\s*(\d+)px", css).group(1))
    declared_h = int(re.search(r"--walk-frame-h:\s*(\d+)px", css).group(1))

    with Image.open(STRIP) as strip:
        width, height = strip.size

    assert width % frames == 0, \
        f"{STRIP.name} is {width}px, which is not {frames} whole frames"
    assert width // frames == declared, (
        f"--walk-frame-w is {declared}px but the strip cuts to {width // frames}px — "
        f"re-run scripts/build_walk_frames.py and copy the size it prints"
    )
    assert height == declared_h, f"--walk-frame-h is {declared_h}px, the strip is {height}px"


def test_the_strip_is_stepped_once_per_drawing(css):
    """steps() and the frame count are two statements of the same fact."""
    frames = int(re.search(r"--walk-frames:\s*(\d+)", css).group(1))
    steps = int(re.search(r"animation:\s*walker-steps[^;]*steps\((\d+)\)", css).group(1))
    assert steps == frames, f"the strip has {frames} drawings but is stepped {steps} times"


def test_the_drawings_carry_the_bounce_so_the_css_does_not(css):
    """
    His body rises and falls twice across the eight drawings while his feet stay on
    one ground line. A CSS bob on top of that is a second bounce running at its own
    phase, and two bounces that disagree read as a judder. The sway may rotate; it
    may not lift him.
    """
    sway = css[css.index("@keyframes walker-sway"):]
    sway = sway[:sway.index("}", sway.index("{", sway.index("{") + 1))]
    assert "translateY" not in sway, \
        "the sway lifts him, which fights the lift already drawn into the frames"


def test_he_stands_on_one_ground_line(css):
    """
    Every drawing is bottom-aligned in its cell, so the element sits on the floor of
    the window. If it were centred his shoes would leave the ground.
    """
    walker = css[css.index(".walker {"):]
    walker = walker[:walker.index("\n}")]
    assert re.search(r"bottom:\s*\d", walker), "he is not pinned to the bottom edge"
