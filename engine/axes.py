"""
Which way is up.

Meshwright works in Z-up, because it prepares models for printing and every slicer,
every build plate and every STL in the world is Z-up. Its viewport, its build-plate
alignment and the height it reports all assume it.

Half the formats it opens do not agree. glTF and GLB *mandate* +Y up in their
specification, and FBX writes its own answer into the file. A model from either one
arrived rotated a quarter turn onto its back, and stayed that way through analysis,
repair and export — so a figure generated in ComfyUI was lying down in the viewport,
its height was reported as its depth, and "rest on build plate" stood it on its
shoulder.

The rule here is narrow on purpose: rotate only when the file *says* which way is up.

* glTF and GLB are rotated always, because their specification leaves no choice.
* FBX is rotated according to what its own header declares, which ufbx reads.
* Everything else is left exactly as it was. OBJ, PLY, OFF and 3DS record nothing
  about orientation, and guessing from the shape of the model would stand some of
  them up and lay others down with no way to tell the cases apart. STL and 3MF are
  printing formats and are already Z-up.

Export mirrors import: a GLB written by Meshwright is turned back to Y-up, so it
opens the right way up in Blender and in every viewer, and so that opening it again
returns exactly the model that was exported.
"""
import numpy as np
import trimesh

# Formats whose own specification fixes the up axis, whatever the file says.
SPEC_Y_UP = {".glb", ".gltf"}

# Y-up to Z-up is a quarter turn about X: (x, y, z) -> (x, -z, y), so what the file
# called height ends up along Z, where the rest of Meshwright looks for it.
_TO_Z_UP = trimesh.transformations.rotation_matrix(np.pi / 2.0, [1, 0, 0])
_TO_Y_UP = trimesh.transformations.rotation_matrix(-np.pi / 2.0, [1, 0, 0])


def to_z_up(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Stand a Y-up model up. Mutates and returns the mesh."""
    mesh.apply_transform(_TO_Z_UP)
    return mesh


def to_y_up(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Lay a Z-up model back down, for writing a format that expects Y-up."""
    mesh.apply_transform(_TO_Y_UP)
    return mesh


def needs_standing_up(ext: str, declared: str | None = None) -> bool:
    """
    Should a model from this file be turned a quarter turn to stand up?

    `declared` is what the file itself said, for the one format that says: "y", "z",
    "x" or None. A format with no declaration and no specification is never rotated.
    """
    ext = (ext or "").lower()
    if ext in SPEC_Y_UP:
        return True
    return declared == "y"
