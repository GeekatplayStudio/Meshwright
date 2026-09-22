import subprocess
from pathlib import Path

import numpy as np
import pytest

from engine import blend_import, browse
from engine.model_loader import load_model
from engine.runtime import subprocess_flags
from engine.validation import input_path


def test_blend_is_accepted_and_listed(tmp_path):
    source = tmp_path / "project.BLEND"
    source.write_bytes(b"BLENDER")
    assert input_path(str(source)) == str(source)
    assert browse.listing(str(tmp_path))["files"][0]["known"]


def test_invalid_blender_override(monkeypatch):
    monkeypatch.setenv("MESHWRIGHT_BLENDER", "missing-blender-executable")
    with pytest.raises(RuntimeError, match="MESHWRIGHT_BLENDER"):
        blend_import.find_blender()


@pytest.mark.parametrize("failure", ["timeout", "failed", "no_output"])
def test_conversion_failures_are_actionable_and_cleaned_up(tmp_path, monkeypatch, failure):
    source = tmp_path / "project.blend"
    source.write_bytes(b"BLENDER")
    monkeypatch.setattr(blend_import, "find_blender", lambda: "blender")
    folders = []

    def run(command, **kwargs):
        # The script Blender is handed always sits in the temporary folder, whatever
        # arguments follow it — so that is what to watch for cleanup.
        folders.append(Path(command[command.index("--python") + 1]).parent)
        assert command.index("--disable-autoexec") < command.index("--python")
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 300)
        return subprocess.CompletedProcess(command, int(failure == "failed"), "bad project", "")

    monkeypatch.setattr(blend_import.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="took too long|could not open|exported no geometry"):
        load_model(str(source))
    assert all(not folder.exists() for folder in folders)


def test_real_blender_scene_roundtrip(tmp_path):
    try:
        executable = blend_import.find_blender()
    except RuntimeError:
        pytest.skip("Blender is not installed")
    source = tmp_path / "scene with spaces.blend"
    script = tmp_path / "create.py"
    script.write_text("""
import bpy
import sys
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_cube_add(size=2, location=(10, 20, 30))
obj = bpy.context.object
obj.scale = (2, 3, 4)
material = bpy.data.materials.new('Textured')
material.use_nodes = True
image = bpy.data.images.new('Packed color', width=16, height=16)
image.generated_color = (0.8, 0.2, 0.1, 1)
image.pack()
texture = material.node_tree.nodes.new('ShaderNodeTexImage')
texture.image = image
shader = material.node_tree.nodes.get('Principled BSDF')
material.node_tree.links.new(texture.outputs['Color'], shader.inputs['Base Color'])
obj.data.materials.append(material)
modifier = obj.modifiers.new('Triangulate', 'TRIANGULATE')
modifier = obj.modifiers.new('Subdivide', 'SUBSURF')
modifier.subdivision_type = 'SIMPLE'
modifier.levels = 1
bpy.ops.wm.save_as_mainfile(filepath=sys.argv[-1])
""", encoding="utf-8")
    result = subprocess.run(
        [executable, "--background", "--factory-startup", "--python-exit-code", "1",
         "--python", str(script), "--", str(source)],
        check=False, capture_output=True, timeout=120, **subprocess_flags())
    assert result.returncode == 0, result.stdout
    original = source.read_bytes()
    mesh, stats = load_model(str(source))
    assert stats["filename"] == source.name
    assert stats["file_path"] == str(source)
    np.testing.assert_allclose(mesh.extents, [4, 6, 8], atol=1e-5)
    np.testing.assert_allclose(mesh.bounds.mean(axis=0), [10, 20, 30], atol=1e-5)
    assert len(mesh.faces) > 12  # the subdivision modifier was evaluated
    assert mesh.is_watertight
    assert mesh.metadata["corner_uv"] is not None
    assert mesh.metadata["embedded_textures"]
    assert source.read_bytes() == original
