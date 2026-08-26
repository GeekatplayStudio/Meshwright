"""
A built-in test object.

Meshwright ships no 3D models — it works on files you already have — but a fresh
install should be provable in one click, so this builds a small object in memory
with the exact defects the program is for: an open hole, a patch of inside-out
triangles, duplicate vertices and a second loose piece.
"""
import numpy as np
import trimesh

DEMO_NAME = "meshwright-demo.stl"


def build_demo_mesh() -> trimesh.Trimesh:
    """A 40 mm sphere with a hole punched in the top, plus a small loose cube."""
    body = trimesh.creation.icosphere(subdivisions=3, radius=20.0)
    vertices = np.asarray(body.vertices, dtype=np.float64).copy()
    faces = np.asarray(body.faces, dtype=np.int64).copy()
    centers = body.triangles_center

    # Punch a hole: drop the cap above 14 mm, which is one connected patch.
    keep = centers[:, 2] < 14.0
    faces = faces[keep]

    # Turn a patch inside out so the winding check has something real to find.
    kept_centers = centers[keep]
    flipped = np.argsort(kept_centers[:, 0])[:10]
    faces[flipped] = faces[flipped][:, ::-1]

    shell = trimesh.Trimesh(vertices=vertices, faces=faces, process=True)

    # A second, disconnected piece — separate shells are the other classic surprise.
    cube = trimesh.creation.box(extents=(8.0, 8.0, 8.0))
    cube.apply_translation([26.0, 0.0, 0.0])

    demo = trimesh.util.concatenate([shell, cube])
    demo.apply_translation([0.0, 0.0, -float(demo.bounds[0][2])])
    return demo
