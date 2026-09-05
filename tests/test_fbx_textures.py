"""
Textures an FBX carries inside itself.

FBX files routinely embed their artwork rather than shipping it alongside, and
trimesh cannot open FBX at all — so if the ufbx pass does not lift the images off the
scene, nothing further down ever sees them: the companion-file scan looks for images
*next to* the model, and an embedded texture leaves none there. A 228 MB Hi3D export
with an 8192x8192 JPEG inside it loaded as an untextured grey model for exactly that
reason.

The scene is faked here rather than checked in. A real FBX big enough to exercise
this is a quarter of a gigabyte, and every rule worth guarding — which material slot
maps to which channel, stubs losing to real artwork, and the separation that keeps
decoding away from the live scene — is about our own logic, not about ufbx's parser.
"""
import io

import pytest
from PIL import Image

from engine.texture.companion_detector import decode_fbx_textures, fbx_texture_blobs


def png_bytes(size=(64, 64), colour=(200, 120, 40)):
    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, format="PNG")
    return buf.getvalue()


class Texture:
    """Stands in for a ufbx texture element."""
    def __init__(self, name, content=None, filename="", element_id=1):
        self.name = name
        self.content = content
        self.filename = self.relative_filename = self.absolute_filename = filename
        self.element_id = element_id
        self.video = None
        self.file_textures = ()


class Slot:
    def __init__(self, texture=None):
        self.texture = texture


class Bag:
    """A ufbx material's `pbr` or `fbx` view: named slots, most of them empty."""
    def __init__(self, **slots):
        self._slots = slots

    def __getattr__(self, name):
        return self._slots.get(name, Slot())


class Material:
    def __init__(self, pbr=None, fbx=None, name="m"):
        self.name = name
        self.pbr = pbr if pbr is not None else Bag()
        self.fbx = fbx if fbx is not None else Bag()


class Scene:
    def __init__(self, *materials):
        self.materials = list(materials)


# ------------------------------------------------------------------ the mapping
def test_an_embedded_diffuse_map_becomes_the_albedo_channel(tmp_path):
    scene = Scene(Material(pbr=Bag(base_color=Slot(Texture("Diffuse", png_bytes())))))
    maps = decode_fbx_textures(fbx_texture_blobs(scene, str(tmp_path / "m.fbx")))
    assert set(maps) == {"albedo"}
    assert maps["albedo"].size == (64, 64)


def test_each_pbr_slot_lands_on_its_own_channel(tmp_path):
    scene = Scene(Material(pbr=Bag(
        base_color=Slot(Texture("c", png_bytes(), element_id=1)),
        normal_map=Slot(Texture("n", png_bytes(), element_id=2)),
        roughness=Slot(Texture("r", png_bytes(), element_id=3)),
        metalness=Slot(Texture("m", png_bytes(), element_id=4)),
        ambient_occlusion=Slot(Texture("o", png_bytes(), element_id=5)),
        displacement_map=Slot(Texture("h", png_bytes(), element_id=6)),
    )))
    maps = decode_fbx_textures(fbx_texture_blobs(scene, str(tmp_path / "m.fbx")))
    assert set(maps) == {"albedo", "normal", "roughness", "metallic", "ao", "height"}


def test_colour_channels_stay_colour_and_data_channels_go_flat(tmp_path):
    """A greyscale map has no business travelling as three copies of itself."""
    scene = Scene(Material(pbr=Bag(
        base_color=Slot(Texture("c", png_bytes(), element_id=1)),
        roughness=Slot(Texture("r", png_bytes(), element_id=2)),
    )))
    maps = decode_fbx_textures(fbx_texture_blobs(scene, str(tmp_path / "m.fbx")))
    assert maps["albedo"].mode == "RGB"
    assert maps["roughness"].mode == "L"


def test_an_older_material_is_read_through_the_legacy_slots(tmp_path):
    """Not every FBX resolves onto the pbr view; the classic properties still work."""
    scene = Scene(Material(fbx=Bag(diffuse_color=Slot(Texture("d", png_bytes())))))
    maps = decode_fbx_textures(fbx_texture_blobs(scene, str(tmp_path / "m.fbx")))
    assert set(maps) == {"albedo"}


def test_one_texture_reached_two_ways_is_not_reported_as_unused(tmp_path):
    """
    The same image is normally visible through both the pbr and the legacy view of a
    material. That is one texture, not two, and telling the user otherwise is noise.
    """
    shared = Texture("Diffuse", png_bytes(), element_id=7)
    scene = Scene(Material(pbr=Bag(base_color=Slot(shared)),
                           fbx=Bag(diffuse_color=Slot(shared))))
    blobs = fbx_texture_blobs(scene, str(tmp_path / "m.fbx"))
    assert "__unused__" not in blobs


def test_a_second_material_naming_a_different_image_is_reported(tmp_path):
    """One set of maps travels with the model, so the rest are said out loud."""
    scene = Scene(
        Material(pbr=Bag(base_color=Slot(Texture("a", png_bytes(), element_id=1)))),
        Material(pbr=Bag(base_color=Slot(Texture("b", png_bytes(), element_id=2)))),
    )
    said = []
    blobs = fbx_texture_blobs(scene, str(tmp_path / "m.fbx"))
    assert blobs.get("__unused__") == 1
    maps = decode_fbx_textures(blobs, log=lambda m, level="info": said.append(m))
    assert set(maps) == {"albedo"}
    assert any("not used" in m for m in said)


# ------------------------------------------------------------------ what is real
def test_an_exporter_stub_is_not_mistaken_for_artwork(tmp_path):
    """Several generators write a 2x2 into the material. It is not a texture."""
    scene = Scene(Material(pbr=Bag(base_color=Slot(Texture("stub", png_bytes((2, 2)))))))
    maps = decode_fbx_textures(fbx_texture_blobs(scene, str(tmp_path / "m.fbx")))
    assert maps == {}


def test_a_texture_named_but_not_embedded_is_found_beside_the_model(tmp_path):
    """
    An FBX may only name its images. The path it names was written on the exporter's
    machine, so only the basename can still mean anything here.
    """
    (tmp_path / "brick.png").write_bytes(png_bytes())
    named = Texture("Diffuse", None, filename=r"C:\somewhere\that\never\existed\brick.png")
    scene = Scene(Material(pbr=Bag(base_color=Slot(named))))

    maps = decode_fbx_textures(fbx_texture_blobs(scene, str(tmp_path / "m.fbx")))
    assert set(maps) == {"albedo"}


def test_a_texture_that_is_neither_embedded_nor_on_disk_is_skipped(tmp_path):
    scene = Scene(Material(pbr=Bag(base_color=Slot(Texture("gone", None, "nowhere.png")))))
    assert fbx_texture_blobs(scene, str(tmp_path / "m.fbx")) == {}


def test_unreadable_bytes_are_reported_rather_than_raised(tmp_path):
    scene = Scene(Material(pbr=Bag(base_color=Slot(Texture("bad", b"not an image")))))
    said = []
    maps = decode_fbx_textures(fbx_texture_blobs(scene, str(tmp_path / "m.fbx")),
                               log=lambda m, level="info": said.append(m))
    assert maps == {}
    assert any("albedo" in m for m in said)


# ------------------------------------------- the separation that avoids the crash
def test_nothing_is_decoded_while_the_scene_is_still_in_hand(tmp_path):
    """
    This is the invariant that keeps the loader alive, not a style preference.

    Allocating a large image while the ufbx scene is still referenced corrupts its
    teardown, and the process dies with an access violation when the scene is freed —
    an 8192x8192 JPEG inside a 228 MB FBX does it every time, with no traceback.
    engine.model_loader._load_fbx therefore keeps the scene as its own local and
    decodes only after returning, which works because this half hands back bytes and
    paths and never an image.
    """
    scene = Scene(Material(pbr=Bag(base_color=Slot(Texture("c", png_bytes())))))
    blobs = fbx_texture_blobs(scene, str(tmp_path / "m.fbx"))

    for channel, source in blobs.items():
        if channel == "__unused__":
            continue
        assert isinstance(source, (bytes, str)), (
            f"{channel} came back as {type(source).__name__} — fbx_texture_blobs must "
            f"not decode anything while the caller still holds the scene")
        assert not isinstance(source, Image.Image)


def test_the_loader_keeps_the_scene_out_of_the_decoding_step():
    """
    A structural check on engine/model_loader.py: the ufbx scene must stay inside
    _load_fbx, and decoding must happen in load_model after it has returned. Moving
    either across that line brings the crash back.
    """
    import inspect

    from engine import model_loader

    inner = inspect.getsource(model_loader._load_fbx)
    outer = inspect.getsource(model_loader.load_model)

    assert "ufbx.load_file" in inner, "_load_fbx no longer owns the scene"
    assert "ufbx.load_file" not in outer, \
        "the scene is being opened in load_model, where it outlives the decode"
    assert "decode_fbx_textures" not in inner, \
        "textures are being decoded while _load_fbx still holds the scene"
    assert "decode_fbx_textures" in outer, "the blobs are never turned into images"


@pytest.mark.parametrize("channel", ["albedo", "normal", "roughness"])
def test_the_file_s_own_material_outranks_a_stub_from_the_visual(channel):
    """
    load_model merges what the FBX declares over what it read off the trimesh visual.
    The visual built for an FBX carries UVs and a default material whose image is a
    2x2 stub; if that arrived first and kept the real map out, it would then be
    discarded downstream as a placeholder and the model would look untextured.
    """
    import inspect

    from engine import model_loader

    source = inspect.getsource(model_loader.load_model)
    assert "embedded.update(fbx_maps)" in source, (
        "fbx_maps must overwrite the visual's entries, not defer to them")
    assert "embedded.setdefault" not in source
