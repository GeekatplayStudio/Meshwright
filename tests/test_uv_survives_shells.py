"""
Texture coordinates have to keep describing the geometry they are attached to, on
the two paths that renumber or rebuild faces underneath them.

Both cases here were found on one AI-generated model: 366 loose shells and a UV
atlas made of 13,314 islands. Half its surface came out painted from the wrong
part of the atlas, and the two causes were independent.
"""
import base64

import numpy as np
import trimesh

from engine.preview import build_mesh_preview
from engine.service import MeshService
from engine.texture import uv_channel as UV


def _flat_uv(mesh: trimesh.Trimesh, u: float, v: float) -> np.ndarray:
    """A UV channel that paints the whole mesh from one point of the atlas."""
    return np.full((len(mesh.faces), 3, 2), (u, v), dtype=np.float32)


def _two_shells():
    """
    Two separate boxes, the smaller one first.

    Order matters: shell separation sorts by size, so putting the small box first
    guarantees the faces are reordered and the UV channel has to move with them.
    """
    small = trimesh.creation.box(extents=[2, 2, 2])
    big = trimesh.creation.box(extents=[10, 10, 10]).subdivide()
    big.apply_translation([40, 0, 0])
    mesh = trimesh.util.concatenate([small, big])
    uv = np.vstack([_flat_uv(small, 0.1, 0.1), _flat_uv(big, 0.9, 0.9)])
    return mesh, uv, small, big


def test_uv_follows_the_faces_when_shells_are_separated(tmp_path):
    """
    Separating shells permutes the faces without changing how many there are, so a
    "did the face count stay the same?" test says nothing. Each face must still
    carry its own artwork afterwards.
    """
    mesh, uv, _small, _big = _two_shells()
    svc = MeshService(autosave=False)
    res = svc._commit(mesh, "load", guard=False, uv=uv)
    assert res["success"]

    st = svc.current
    assert st.uv is not None, "the UV channel was dropped entirely"
    assert len(st.uv) == len(st.mesh.faces)

    # Which box each committed face sits on, decided by geometry alone.
    centroids = np.asarray(st.mesh.triangles).mean(axis=1)
    on_big = centroids[:, 0] > 20.0
    assert on_big.any() and (~on_big).any(), "the fixture no longer has two shells"

    got = st.uv[:, 0, :]                                    # one corner is enough here
    assert np.allclose(got[on_big], [0.9, 0.9], atol=1e-6), \
        "faces of the large shell are reading the small shell's UVs"
    assert np.allclose(got[~on_big], [0.1, 0.1], atol=1e-6), \
        "faces of the small shell are reading the large shell's UVs"


def test_uv_survives_a_load_through_the_service(tmp_path):
    """The same thing end to end, from a real file that separates into two shells."""
    small = trimesh.creation.box(extents=[2, 2, 2])
    big = trimesh.creation.box(extents=[10, 10, 10]).subdivide()
    big.apply_translation([40, 0, 0])
    vertices = np.vstack([small.vertices, big.vertices])
    faces = np.vstack([small.faces, big.faces + len(small.vertices)])
    uv = np.vstack([np.full((len(small.vertices), 2), 0.1),
                    np.full((len(big.vertices), 2), 0.9)]).astype(np.float32)
    path = str(tmp_path / "two_shells.glb")
    trimesh.Trimesh(vertices=vertices, faces=faces, process=False,
                    visual=trimesh.visual.TextureVisuals(uv=uv)).export(path)

    svc = MeshService(autosave=False)
    svc.load(path)
    st = svc.current
    assert st.uv is not None, "loading dropped the UV channel"

    centroids = np.asarray(st.mesh.triangles).mean(axis=1)
    on_big = centroids[:, 0] > 20.0
    assert on_big.any() and (~on_big).any()
    big_uv = st.uv[on_big].reshape(-1, 2)
    small_uv = st.uv[~on_big].reshape(-1, 2)
    assert np.allclose(big_uv, 0.9, atol=1e-4), "the large shell came back with the small shell's UVs"
    assert np.allclose(small_uv, 0.1, atol=1e-4), "the small shell came back with the large shell's UVs"


def _seamed_sphere():
    """
    A sphere whose atlas is two islands, so that most welded vertices carry two
    different UVs — the case a per-vertex UV array cannot represent.
    """
    mesh = trimesh.creation.icosphere(subdivisions=4, radius=10.0)
    centroids = np.asarray(mesh.triangles).mean(axis=1)
    uv = np.zeros((len(mesh.faces), 3, 2), dtype=np.float32)
    east = centroids[:, 0] >= 0
    uv[east] = (0.05, 0.05)                                 # one island
    uv[~east] = (0.95, 0.95)                                # the other, far across the sheet
    return mesh, uv


def test_decimated_preview_keeps_each_face_on_its_own_island():
    """
    The viewport draws a simplified copy of a dense model. Its UVs used to be
    collapsed to one per welded vertex, chosen arbitrarily from the corners meeting
    there — so every vertex on a seam had a coin flip, and the texture shattered.
    """
    mesh, uv = _seamed_sphere()
    preview = build_mesh_preview(mesh, uv, detail=0.25)
    assert preview["detail"]["reduced"], "the fixture is not being decimated"
    assert "uvs" in preview, "the display copy lost its UVs"

    faces = np.frombuffer(base64.b64decode(preview["faces"]), np.uint32).reshape(-1, 3)
    verts = np.frombuffer(base64.b64decode(preview["vertices"]), np.float32).reshape(-1, 3)
    uvs = np.frombuffer(base64.b64decode(preview["uvs"]), np.float32).reshape(-1, 2)

    shown = uvs[faces]                                      # (F, 3, 2), as the GPU reads it
    # Every corner must come from one of the two islands, never averaged between them.
    near_east = np.abs(shown - 0.05).max(axis=2) < 0.2
    near_west = np.abs(shown - 0.95).max(axis=2) < 0.2
    assert np.all(near_east | near_west), "corners were interpolated across the seam"

    # And a face must not be reading the island on the far side of the model.
    centroids = verts[faces].mean(axis=1)
    east_face = centroids[:, 0] >= 0
    face_uv = shown.mean(axis=1)
    wrong = np.count_nonzero(
        (east_face & (np.abs(face_uv - 0.05).max(axis=1) > 0.2))
        | (~east_face & (np.abs(face_uv - 0.95).max(axis=1) > 0.2)))
    # Faces straddling the seam legitimately pick one side; everything else must be right.
    assert wrong <= 0.08 * len(faces), f"{wrong} of {len(faces)} display faces read the wrong island"


def test_fast_transfer_matches_the_exact_one_closely():
    """
    The viewport's transfer trades exactness for speed. It still has to agree with
    the real one on which island every face belongs to.
    """
    mesh, uv = _seamed_sphere()
    target = mesh.copy()
    target.vertices += np.random.default_rng(0).normal(scale=1e-4, size=target.vertices.shape)
    target = trimesh.Trimesh(vertices=target.vertices, faces=target.faces, process=False)

    exact = UV.transfer(mesh, uv, target)
    quick = UV.transfer_fast(mesh, uv, target)
    assert quick is not None and quick.shape == exact.shape
    differing = np.abs(quick - exact).max(axis=(1, 2)) > 0.2
    assert differing.mean() < 0.02, "the quick transfer disagrees about islands"


def test_fast_transfer_is_a_no_op_on_identical_geometry():
    mesh, uv = _seamed_sphere()
    same = UV.transfer_fast(mesh, uv, mesh.copy())
    assert np.array_equal(same, uv)
    assert UV.transfer_fast(mesh, None, mesh.copy()) is None
