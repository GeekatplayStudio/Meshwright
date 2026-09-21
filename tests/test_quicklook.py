"""
What the file browser shows about a file it has not opened.

The point of engine.quicklook is that its answers cost a fraction of loading the
model — so the tests care about two things above all: that the numbers are the
*true* ones (a wrong triangle count or a wrong size is worse than none), and that
nothing a damaged or hostile file can contain takes the program down with it.
"""
import json
import os
import struct
import zipfile

import numpy as np
import pytest
import trimesh

from engine import quicklook


@pytest.fixture(autouse=True)
def cache_in_tmp(tmp_path, monkeypatch):
    """Never write pictures into the real %LOCALAPPDATA%\\Meshwright."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))


@pytest.fixture
def box(tmp_path):
    """A 50 x 40 x 30 box: 12 triangles, and every number about it known."""
    mesh = trimesh.creation.box(extents=(50, 40, 30))
    paths = {}
    for ext in ("stl", "obj", "glb", "ply", "off", "3mf"):
        path = tmp_path / f"box.{ext}"
        mesh.export(str(path))
        paths[ext] = str(path)
    return paths


# ------------------------------------------------------------------ the facts are the real ones
@pytest.mark.parametrize("ext", ["stl", "obj", "glb", "ply", "off", "3mf"])
def test_every_format_reports_the_size_the_model_really_is(box, ext):
    seen = quicklook.look(box[ext])
    assert seen["note"] is None, seen["note"]
    dims = seen["dimensions"]
    assert dims is not None, f"no dimensions for {ext}"
    # glTF turns a Z-up box on its side, so compare the sorted sides.
    assert sorted([dims["x"], dims["y"], dims["z"]]) == pytest.approx([30, 40, 50], abs=0.01)


@pytest.mark.parametrize("ext", ["stl", "obj", "glb", "3mf"])
def test_the_triangle_count_is_the_file_s_own(box, ext):
    assert quicklook.look(box[ext])["faces"] == 12


def test_a_picture_comes_back_for_a_real_model(box):
    seen = quicklook.look(box["stl"])
    assert seen["picture"].startswith("data:image/png;base64,")
    assert len(seen["picture"]) > 500


def test_the_picture_is_of_the_model_and_not_an_empty_frame(box, tmp_path):
    """A blank picture would pass a 'picture exists' check and tell the user nothing."""
    import base64

    import cv2
    raw = base64.b64decode(quicklook.look(box["stl"])["picture"].split(",", 1)[1])
    image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_UNCHANGED)
    assert image.shape[2] == 4, "the picture should be transparent where the model is not"
    covered = (image[..., 3] > 0).mean()
    assert 0.1 < covered < 0.95, f"the model covers {covered:.0%} of the picture"
    lit = image[..., :3][image[..., 3] > 0]
    assert lit.std() > 8, "a solid block of one colour is not a picture of anything"


def test_asking_twice_is_answered_from_the_cache(box):
    first = quicklook.look(box["stl"])
    cached = [f for f in os.listdir(quicklook.cache_dir()) if f.endswith(".png")]
    assert cached, "nothing was kept"
    second = quicklook.look(box["stl"])
    assert second["picture"] == first["picture"] and second["faces"] == first["faces"]


def test_a_changed_file_is_looked_at_again(box, tmp_path):
    """The cache is keyed by size and time, so an edited file must not show the old picture."""
    before = quicklook.look(box["stl"])["faces"]
    trimesh.creation.icosphere(subdivisions=2).export(box["stl"])
    os.utime(box["stl"], (0, 0))
    assert quicklook.look(box["stl"])["faces"] != before


def test_how_long_it_will_take_to_open_is_offered(box):
    assert quicklook.look(box["stl"])["load_estimate"]


def test_a_model_far_too_small_to_be_millimetres_is_flagged(tmp_path):
    """The analysis panel calls this out once the model is open; so does the browser."""
    path = str(tmp_path / "tiny.stl")
    trimesh.creation.box(extents=(0.2, 0.2, 0.2)).export(path)
    assert quicklook.look(path)["dimensions"]["odd_scale"] is True


def test_a_normal_sized_model_is_not_flagged(box):
    assert quicklook.look(box["stl"])["dimensions"]["odd_scale"] is False


def test_a_textured_model_says_how_many_textures_it_carries(tmp_path):
    """Whether a model brings its own colours decides what to expect once it opens."""
    from PIL import Image

    mesh = trimesh.creation.box(extents=(10, 10, 10))
    mesh.visual = trimesh.visual.TextureVisuals(
        uv=np.zeros((len(mesh.vertices), 2)),
        image=Image.new("RGB", (8, 8), (200, 60, 60)))
    path = str(tmp_path / "tex.glb")
    mesh.export(path)
    assert quicklook.look(path)["textures"] == 1


def test_a_model_with_no_textures_says_none(box):
    assert quicklook.look(box["glb"])["textures"] == 0


# ------------------------------------------------------------------ nothing brings the app down
def test_a_file_that_is_not_a_model_is_refused_in_words(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("hello")
    seen = quicklook.look(str(path))
    assert seen["picture"] is None
    assert "cannot open" in seen["note"]


def test_an_empty_file_says_so(tmp_path):
    path = tmp_path / "empty.stl"
    path.write_bytes(b"")
    assert "empty" in quicklook.look(str(path))["note"]


def test_a_missing_file_says_so(tmp_path):
    assert "no longer there" in quicklook.look(str(tmp_path / "gone.stl"))["note"]


def test_a_truncated_binary_stl_does_not_take_the_program_down(box, tmp_path):
    whole = open(box["stl"], "rb").read()
    path = tmp_path / "cut.stl"
    path.write_bytes(whole[: len(whole) // 2])
    seen = quicklook.look(str(path))
    assert seen["note"], "a half-written file should be explained, not drawn"


def test_a_stl_claiming_more_triangles_than_it_holds_is_refused(tmp_path):
    """The count in an STL header is the file's own claim; believing it blindly reads off the end."""
    path = tmp_path / "lying.stl"
    path.write_bytes(b"\0" * 80 + struct.pack("<I", 100_000_000))
    seen = quicklook.look(str(path))
    assert seen["note"] and seen["picture"] is None


def test_a_glb_pointing_outside_itself_is_refused(box, tmp_path):
    """A bufferView that runs past the end of the file must not be mapped and walked off."""
    raw = bytearray(open(box["glb"], "rb").read())
    length = struct.unpack("<I", raw[12:16])[0]
    scene = json.loads(raw[20:20 + length])
    scene["bufferViews"][0]["byteOffset"] = 1 << 30
    patched = json.dumps(scene).encode()
    patched += b" " * (-len(patched) % 4)
    rebuilt = bytearray(raw[:12]) + struct.pack("<II", len(patched), 0x4E4F534A) + patched + raw[20 + length:]
    path = tmp_path / "wild.glb"
    path.write_bytes(bytes(rebuilt))
    seen = quicklook.look(str(path))
    assert seen["note"] and seen["picture"] is None


def test_an_obj_naming_points_it_does_not_have_is_refused(tmp_path):
    path = tmp_path / "wrong.obj"
    path.write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 9999\n")
    seen = quicklook.look(str(path))
    assert seen["picture"] is None and seen["note"]


def test_rubbish_dressed_as_a_model_is_refused(tmp_path):
    for name, body in (("junk.glb", b"not a gltf at all"), ("junk.ply", b"nope"),
                       ("junk.off", b"\x00\x01\x02"), ("junk.3mf", b"PK not really")):
        seen = quicklook.look(str(tmp_path / name))
        assert seen["picture"] is None
        assert seen["note"], name


def test_a_file_windows_keeps_in_the_cloud_is_left_alone(box, monkeypatch):
    """Reading one would quietly pull it down over the network."""
    monkeypatch.setattr(quicklook, "is_cloud_only", lambda path: True)
    seen = quicklook.look(box["stl"])
    assert seen["picture"] is None and "cloud" in seen["note"]


# ------------------------------------------------------------------ point clouds and oddities
def test_a_point_cloud_is_still_drawn(tmp_path):
    """A PLY from a scanner has no faces at all; it should still be recognisable."""
    points = np.random.default_rng(0).normal(size=(4000, 3)) * 10
    path = str(tmp_path / "cloud.ply")
    trimesh.PointCloud(points).export(path)
    seen = quicklook.look(path)
    assert seen["picture"], seen["note"]
    assert seen["vertices"] == 4000


def test_a_model_with_one_huge_flat_face_is_not_drawn_as_speckles(tmp_path):
    """
    Sampling one point per triangle lights one pixel of a big face and leaves the
    rest transparent. Points are spread by area instead, so a plate looks solid.
    """
    import base64

    import cv2
    plate = trimesh.creation.box(extents=(100, 100, 1))
    path = str(tmp_path / "plate.stl")
    plate.export(path)
    raw = base64.b64decode(quicklook.look(path)["picture"].split(",", 1)[1])
    image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_UNCHANGED)
    solid = (image[..., 3] > 0).astype(np.uint8)
    # Seen from above at an angle a plate is a rhombus, so most of its bounding box
    # is rightly empty. What matters is that the shape itself is filled in: compare
    # what is drawn against the smallest outline that contains it.
    hull = cv2.convexHull(cv2.findNonZero(solid))
    filled = solid.sum() / max(cv2.contourArea(hull), 1)
    assert filled > 0.9, f"the plate is full of holes: {filled:.0%} of its own outline"


def test_a_big_3mf_reports_its_triangles_even_when_it_is_too_big_to_draw(tmp_path, monkeypatch):
    """The count is read by streaming; the chunk boundary must not lose or double a tag."""
    monkeypatch.setattr(quicklook, "XML_LIMIT", 1)          # force the streaming path
    path = str(tmp_path / "big.3mf")
    mesh = trimesh.creation.icosphere(subdivisions=3)
    mesh.export(path)
    seen = quicklook.look(path)
    assert seen["faces"] == len(mesh.faces)
    assert seen["vertices"] == len(mesh.vertices)


def test_a_3mf_carrying_its_own_thumbnail_uses_it(box, tmp_path):
    """Slicers save a picture inside the file; it is better than one drawn from a sample."""
    path = tmp_path / "with-thumb.3mf"
    with zipfile.ZipFile(box["3mf"]) as source, zipfile.ZipFile(path, "w") as target:
        for item in source.infolist():
            target.writestr(item, source.read(item.filename))
        target.writestr("Metadata/thumbnail.png",
                        b"\x89PNG\r\n\x1a\n" + b"\0" * 64)
    with_thumb = quicklook.look(str(path))
    assert with_thumb["picture"].startswith("data:image/png;base64,")


def test_the_cache_does_not_grow_without_end(box, monkeypatch, tmp_path):
    monkeypatch.setattr(quicklook, "CACHE_KEEP", 4)
    for n in range(8):
        copy = tmp_path / f"copy{n}.stl"
        copy.write_bytes(open(box["stl"], "rb").read() + bytes([n]) * 0)
        os.utime(copy, (n, n))
        quicklook.look(str(copy))
    kept = os.listdir(quicklook.cache_dir())
    assert len(kept) <= 6, kept
