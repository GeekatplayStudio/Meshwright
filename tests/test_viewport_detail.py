"""
Viewport level of detail.

A dense model is drawn simplified so it appears quickly. The rule that makes that
acceptable is that it is *only* the drawing: the mesh, the diagnostics, every
operation and every export use all of it. These tests hold that line, and check the
things a decimated preview can quietly get wrong — the piece colouring that walks the
face array, and the UVs that have to come along.
"""
import base64
import os
import sys

import numpy as np
import pytest
import trimesh

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.preview import DEFAULT_PREVIEW_FACES, build_mesh_preview
from engine.service import MeshService
from engine.texture.uv_unwrapper import unwrap_corner_uv


def _arr(payload, key, dtype, width):
    return np.frombuffer(base64.b64decode(payload[key]), dtype=dtype).reshape(-1, width)


@pytest.fixture
def dense():
    """20,480 faces — enough to exercise the reduction with a small cap."""
    return trimesh.creation.icosphere(subdivisions=5, radius=40.0)


@pytest.fixture
def service():
    svc = MeshService(log=lambda m, level="info": None, autosave=False)
    yield svc
    svc.close()


# ------------------------------------------------------------------ the payload
def test_a_small_model_is_sent_whole(dense):
    payload = build_mesh_preview(dense, None, max_faces=DEFAULT_PREVIEW_FACES)
    assert payload["detail"]["reduced"] is False
    assert payload["detail"]["faces_shown"] == len(dense.faces)
    assert payload["detail"]["fraction"] == 1.0


def test_a_dense_model_is_drawn_simplified(dense):
    payload = build_mesh_preview(dense, None, max_faces=4_000)

    detail = payload["detail"]
    assert detail["reduced"] is True
    assert detail["faces_total"] == len(dense.faces)
    assert detail["faces_shown"] < detail["faces_total"]
    assert 0 < detail["fraction"] < 1

    faces = _arr(payload, "faces", np.uint32, 3)
    verts = _arr(payload, "vertices", np.float32, 3)
    assert len(faces) == detail["faces_shown"]
    assert faces.max() < len(verts), "a face indexes past the vertex buffer"


def test_the_simplified_mesh_still_looks_like_the_model(dense):
    """Decimation may lose detail; it may not move the model or lose a limb."""
    payload = build_mesh_preview(dense, None, max_faces=3_000)
    verts = _arr(payload, "vertices", np.float32, 3)

    lo, hi = dense.bounds
    assert np.all(verts.min(axis=0) >= lo - 1.0)
    assert np.all(verts.max(axis=0) <= hi + 1.0)
    # A sphere of radius 40 keeps its radius.
    radius = np.linalg.norm(verts - dense.bounds.mean(axis=0), axis=1)
    assert 35 < radius.mean() < 42


def test_uvs_survive_the_reduction(dense):
    corner_uv, _ = unwrap_corner_uv(dense)
    payload = build_mesh_preview(dense, corner_uv, max_faces=3_000)

    assert "uvs" in payload
    uvs = _arr(payload, "uvs", np.float32, 2)
    verts = _arr(payload, "vertices", np.float32, 3)
    assert len(uvs) == len(verts), "every drawn vertex needs a UV"
    assert np.isfinite(uvs).all()
    assert uvs.min() >= -0.01 and uvs.max() <= 1.01


def test_piece_colouring_still_lines_up(service):
    """
    The viewport colours separate pieces by walking the face array in shell order, so
    a decimated preview has to report the decimated per-piece counts.
    """
    pair = trimesh.util.concatenate([
        trimesh.creation.icosphere(subdivisions=4, radius=20.0),
        trimesh.creation.icosphere(subdivisions=4, radius=10.0).apply_translation([60, 0, 0])])
    service.file_path = "pair.stl"
    service._commit(pair, "load", guard=False)
    service.preview_max_faces = 2_000

    res = service.set_preview_detail(None)
    detail = res["detail"]
    assert detail["reduced"] is True

    counts = res["shell_face_counts"]
    assert len(counts) == 2, "both pieces must survive the reduction"
    assert all(c > 0 for c in counts)
    assert sum(counts) == detail["faces_shown"], "piece counts must add up to what is drawn"


def test_open_edge_overlay_is_dropped_when_the_mesh_on_screen_is_not_the_real_one():
    """
    The overlay marks the real open edges. Once the drawn mesh is a decimated copy it
    would be marking edges that mesh happens to have, which is worse than silence —
    the interface says so instead.
    """
    side = 40
    axis = np.linspace(0, 100, side)
    x, y = np.meshgrid(axis, axis)
    verts = np.column_stack([x.ravel(), y.ravel(), np.zeros(side * side)])
    grid = np.arange(side * side).reshape(side, side)
    faces = np.array([tri
                      for i in range(side - 1) for j in range(side - 1)
                      for tri in ([grid[i, j], grid[i, j + 1], grid[i + 1, j + 1]],
                                  [grid[i, j], grid[i + 1, j + 1], grid[i + 1, j]])])
    plane = trimesh.Trimesh(vertices=verts, faces=faces, process=False)

    full = build_mesh_preview(plane, None)
    reduced = build_mesh_preview(plane, None, detail=0.3)

    assert reduced["detail"]["reduced"] is True

    assert len(base64.b64decode(full["boundary_edges"])) > 0
    assert len(base64.b64decode(reduced["boundary_edges"])) == 0
    assert reduced["detail"]["open_edges_hidden"] is True


# ------------------------------------------------------------------ the promise
def test_detail_never_touches_the_model(service, dense):
    service.file_path = "sphere.stl"
    loaded = service._commit(dense, "load", guard=False)
    faces_before = loaded["stats"]["face_count"]
    verdict_before = loaded["analysis"]["verdict"]

    service.set_preview_detail(0.1)

    assert len(service.mesh.faces) == faces_before
    assert service.current.analysis["verdict"] == verdict_before
    assert service.analyze()["stats"]["face_count"] == faces_before


def test_detail_can_be_raised_and_handed_back(service, dense):
    service.file_path = "sphere.stl"
    service._commit(dense, "load", guard=False)
    service.preview_max_faces = 2_000

    auto = service.set_preview_detail(None)["detail"]
    assert auto["reduced"] is True

    full = service.set_preview_detail(1.0)["detail"]
    assert full["reduced"] is False
    assert full["faces_shown"] == len(dense.faces)

    half = service.set_preview_detail(0.5)["detail"]
    assert 0.4 < half["fraction"] < 0.6


def test_detail_is_validated(service, dense):
    service.file_path = "sphere.stl"
    service._commit(dense, "load", guard=False)
    # Out of range clamps rather than raising; the slider cannot send nonsense but a
    # script can.
    assert service.set_preview_detail(5.0)["detail"]["fraction"] <= 1.0
    assert service.set_preview_detail(0.0)["detail"]["faces_shown"] > 0
