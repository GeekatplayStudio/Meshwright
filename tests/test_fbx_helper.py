"""
FBX is read in a helper process, and nothing ufbx hands out is freed mid-read.

Both come from one crash. The file browser drew a picture of a Tripo FBX, and the
whole Meshwright window closed with exit code 0xC0000005 and no message. The ufbx
Python binding (0.0.5, the newest there is) frees memory it still needs when a
temporary wrapper is dropped — `scene.materials[0].pbr` twice in a row is enough —
and on a large scene freeing the scene itself faults now and then, in any order.

So the reader pins every wrapper until it has finished, and runs in a child process
that leaves without freeing anything. These tests hold both halves to that, with a
real FBX for the helper and fakes for the pinning rule.
"""
import gc
import os
import sys

import numpy as np
import pytest

from engine import model_loader as ML
from engine import quicklook
from engine._fbx_worker import SAMPLE_FBX

pytestmark = pytest.mark.skipif(not ML.HAS_UFBX, reason="ufbx is not installed")

# The one-quad FBX the self-test also reads: real enough for ufbx to parse rather
# than a stand-in, and small enough to keep in the source.
QUAD_FBX = SAMPLE_FBX

# Dies the way the native reader does: an access violation, no Python traceback.
# (ctypes would catch a plain null read and raise OSError; this one is not caught.)
CRASHING_HELPER = "import faulthandler\nfaulthandler.disable()\nfaulthandler._read_null()\n"


@pytest.fixture
def quad_fbx(tmp_path):
    path = tmp_path / "quad.fbx"
    path.write_text(QUAD_FBX, encoding="ascii")
    return str(path)


@pytest.fixture
def crashing_helper(tmp_path, monkeypatch):
    script = tmp_path / "crash.py"
    script.write_text(CRASHING_HELPER)
    monkeypatch.setattr(ML, "_FBX_WORKER", str(script))
    return script


# ------------------------------------------------------------------ the helper process
def test_an_fbx_is_read_in_the_helper_and_comes_back_whole(quad_fbx):
    mesh, blobs, declared = ML._load_fbx(quad_fbx)
    assert len(mesh.faces) == 2, "the quad should come back fan-triangulated"
    assert len(mesh.vertices) == 4
    assert np.allclose(mesh.bounds, [[0, 0, 0], [10, 10, 0]])
    assert declared == "y"
    assert blobs == {}


def test_opening_an_fbx_goes_through_the_helper_end_to_end(quad_fbx):
    loaded, _stats = ML.load_model(quad_fbx)
    assert len(loaded.faces) == 2


def test_a_crashing_reader_is_an_error_not_a_closed_window(quad_fbx, crashing_helper):
    """The test process is still here to make these assertions: that is the point."""
    with pytest.raises(ML.FbxReaderCrashed) as caught:
        ML._load_fbx(quad_fbx)
    assert "Re-export" in str(caught.value), "the message should say what to do next"


def test_after_a_reader_crash_the_other_loaders_still_get_their_turn(quad_fbx, crashing_helper):
    if not ML.HAS_PYMESHLAB:
        pytest.skip("MeshLab is the loader that can read FBX after ufbx")
    loaded, _stats = ML.load_model(quad_fbx)
    assert len(loaded.faces) == 2


def test_when_nothing_can_read_it_the_reader_s_crash_is_what_is_reported(
        quad_fbx, crashing_helper, monkeypatch):
    monkeypatch.setattr(ML, "HAS_PYMESHLAB", False)
    with pytest.raises(RuntimeError) as opened:
        ML.load_model(quad_fbx)
    assert "crashed" in str(opened.value) and "quad.fbx" in str(opened.value), (
        "opening should report the reader's crash, not a generic 'corrupted or unsupported'")


def test_the_file_browser_survives_an_fbx_that_crashes_the_reader(quad_fbx, crashing_helper):
    with pytest.raises(quicklook.Unreadable):
        quicklook._read_fbx(quad_fbx, os.path.getsize(quad_fbx), 2000)


def test_an_ordinary_read_error_is_reported_as_itself_not_as_a_crash(tmp_path):
    junk = tmp_path / "junk.fbx"
    junk.write_bytes(b"this is not an FBX file at all")
    with pytest.raises(RuntimeError) as caught:
        ML._load_fbx(str(junk))
    assert not isinstance(caught.value, ML.FbxReaderCrashed)
    assert str(caught.value), "the helper's own error message should come through"


def test_what_the_helper_writes_comes_back_unchanged(tmp_path):
    import trimesh

    mesh = trimesh.Trimesh(vertices=[[0, 0, 0], [1, 0, 0], [0, 1, 0]], faces=[[0, 1, 2]], process=False)
    mesh.visual = trimesh.visual.TextureVisuals(uv=np.array([[0, 0], [1, 0], [0, 1]], dtype=float))
    blobs = {"albedo": b"\x89PNG not really", "normal": r"D:\textures\n.png", "__unused__": 3}
    out = str(tmp_path / "out.npz")

    ML._pack_fbx((mesh, blobs, "y"), out)
    back, back_blobs, declared = ML._unpack_fbx(out)

    assert np.array_equal(back.vertices, mesh.vertices) and np.array_equal(back.faces, mesh.faces)
    assert np.array_equal(back.visual.uv, mesh.visual.uv)
    assert back_blobs == blobs
    assert declared == "y"


def test_a_file_with_no_geometry_comes_back_as_no_mesh(tmp_path):
    out = str(tmp_path / "out.npz")
    ML._pack_fbx((None, {}, None), out)
    assert ML._unpack_fbx(out) == (None, {}, None)


# ------------------------------------------------------------------ pinning
class Wrapper:
    """
    Behaves like a ufbx wrapper: every attribute read builds a fresh object, and
    each one records when it is freed. The binding's bug is that a freed wrapper
    corrupts the next one of its kind, so "nothing freed while reading" is the rule.
    """

    def __init__(self, spec, deaths):
        self._spec, self._deaths = spec, deaths

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            value = self._spec[name]
        except KeyError:
            raise AttributeError(name) from None
        return wrap(value, self._deaths)

    def __del__(self):
        self._deaths.append(type(self).__name__)


class WrapperList(list):
    def __del__(self):
        self._deaths.append("list")


def wrap(value, deaths):
    if isinstance(value, dict):
        return Wrapper(value, deaths)
    if isinstance(value, tuple):          # a list the binding builds afresh each time
        items = WrapperList(wrap(v, deaths) for v in value)
        items._deaths = deaths
        return items
    return value


def fake_scene_spec():
    quad = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], dtype=np.float64)
    texture = {"element_id": 7, "content": b"", "video": None, "file_textures": (),
               "absolute_filename": "", "filename": "", "relative_filename": ""}
    return {
        "meshes": ({
            "num_faces": 1, "num_triangles": 2, "num_indices": 4,
            "faces": ({"index_begin": 0, "num_indices": 4},),
            "vertex_position": {"values": quad.tobytes(), "indices": [0, 1, 2, 3]},
            "vertex_uv": {"exists": False, "values": b"", "indices": []},
        },),
        "settings": {"axes": {"up": {"name": "POSITIVE_Y"}}},
        "materials": ({
            "pbr": {"base_color": {"texture": texture}, "normal_map": {"texture": None}},
            "fbx": {"diffuse_color": {"texture": texture}},
        },),
    }


def test_no_ufbx_wrapper_is_freed_while_the_scene_is_being_read(tmp_path):
    deaths = []
    scene = wrap(fake_scene_spec(), deaths)
    pinned = []

    def pin(obj):
        pinned.append(obj)
        return obj

    gc.collect()
    mesh, _blobs, declared = ML._read_fbx_scene(scene, str(tmp_path / "m.fbx"), pin)
    gc.collect()

    assert len(mesh.faces) == 2 and declared == "y"
    assert deaths == [], (
        f"{len(deaths)} ufbx wrapper(s) were freed mid-read ({sorted(set(deaths))}); "
        "with the real binding the next wrapper of that kind can point at freed memory")


def test_the_pinning_test_can_tell_when_wrappers_are_dropped(tmp_path):
    """Without pinning the fakes do get freed — so the test above is measuring something."""
    deaths = []
    scene = wrap(fake_scene_spec(), deaths)
    ML._read_fbx_scene(scene, str(tmp_path / "m.fbx"), lambda obj: obj)
    gc.collect()
    assert deaths, "the fakes were never freed, so the pinning test proves nothing"


@pytest.mark.skipif(sys.platform != "win32", reason="the crash this guards against was seen on Windows")
def test_the_helper_is_registered_for_the_frozen_program():
    from engine.runtime import WORKERS

    module, function = WORKERS["fbx"]
    assert module == "engine._fbx_worker" and function == "main"
    with open(os.path.join(os.path.dirname(ML.__file__), "..", "packaging", "meshwright.spec"),
              encoding="utf-8") as spec:
        assert "engine._fbx_worker" in spec.read(), "the frozen build would not contain the helper"
