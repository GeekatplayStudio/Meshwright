"""
Smart retopology has to be survivable.

QuadriFlow can abort inside Eigen rather than raise, which in-process ends Meshwright
mid-operation, and its field optimiser can run for ten minutes on a mesh it cannot
solve. Neither is predictable from the input. So it runs in a child process on a
budget, and everything below is about what happens when that budget runs out.
"""
import numpy as np
import pytest
import trimesh

import engine.mesh_retopo as R


@pytest.fixture
def sphere():
    return trimesh.creation.icosphere(subdivisions=4, radius=40.0)


def test_it_runs_out_of_process():
    """In-process, an abort in QuadriFlow closes the application."""
    import os
    assert os.path.exists(R._QUADRIFLOW_WORKER)
    source = (R._QUADRIFLOW_WORKER and open(R._QUADRIFLOW_WORKER, encoding="utf-8").read())
    assert "import engine" not in source, "the worker must not depend on Meshwright's package path"


def test_the_budget_is_bounded():
    """Ten minutes of maybe is worse than two minutes and a fallback."""
    assert 30 <= R.QUADRIFLOW_TIMEOUT_S <= 300


def test_a_crashing_worker_is_reported_not_raised(sphere, monkeypatch):
    class Crashed:
        returncode = -1073741819          # 0xC0000005, an access violation
        stdout = b""
        stderr = b""

    monkeypatch.setattr(R.subprocess, "run", lambda *a, **k: Crashed())
    messages = []
    result = R._run_quadriflow(sphere, 200, 0, True, True,
                               lambda m, level="info": messages.append(m))

    assert result is None, "a crash must be reported as 'no result', not raised"
    assert any("uniform remeshing" in m for m in messages), messages


def test_a_stalled_worker_gives_up_and_says_so(sphere, monkeypatch):
    def timeout(*args, **kwargs):
        raise R.subprocess.TimeoutExpired(cmd="quadriflow", timeout=R.QUADRIFLOW_TIMEOUT_S)

    monkeypatch.setattr(R.subprocess, "run", timeout)
    messages = []
    result = R._run_quadriflow(sphere, 200, 0, True, True,
                               lambda m, level="info": messages.append(m))

    assert result is None
    assert any("did not converge" in m for m in messages), messages


def test_retopology_still_produces_a_mesh_when_quadriflow_never_works(sphere, monkeypatch):
    """The whole point of the fallback chain: the user gets a result either way."""
    monkeypatch.setattr(R, "_run_quadriflow", lambda *a, **k: None)

    out, info = R.retopologize(sphere, 500, method="quadriflow")

    assert len(out.faces) > 0
    assert info["method_used"] != "none"
    assert "quadriflow" not in info["method_used"]
    assert info["final_faces"] < len(sphere.faces)


# ------------------------------------------------------------------ pre-decimation
def test_pre_decimation_returns_something_quadriflow_can_use(sphere):
    """QuadriFlow refuses anything that is not a closed manifold."""
    dense = trimesh.creation.icosphere(subdivisions=5, radius=40.0)
    reduced = R._pre_decimate(dense, 2_000, lambda *a, **k: None)

    assert len(reduced.faces) < len(dense.faces)
    assert R._is_manifold(reduced), "the quad field cannot be built on this"


def test_pre_decimation_leaves_a_small_mesh_alone(sphere):
    assert R._pre_decimate(sphere, len(sphere.faces) * 2, lambda *a, **k: None) is sphere


def test_pre_decimation_falls_back_when_the_quick_engine_tears_the_surface(monkeypatch):
    """
    fast-simplification is quick but can leave a non-manifold; MeshLab is slow and
    always manifold. The quick one is only accepted when the result is usable.
    """
    dense = trimesh.creation.icosphere(subdivisions=5, radius=40.0)

    torn = trimesh.Trimesh(vertices=dense.vertices, faces=dense.faces[:len(dense.faces) // 2],
                           process=False)
    monkeypatch.setattr(R.fastsim, "simplify",
                        lambda v, f, **kw: (np.asarray(torn.vertices), np.asarray(torn.faces)))
    monkeypatch.setattr(R, "_closed_copy", lambda m: m)      # deny the repair too

    messages = []
    out = R._pre_decimate(dense, 2_000, lambda m, level="info": messages.append(m))

    assert R._is_manifold(out), "an unusable pre-decimation was accepted"
    assert any("slower one" in m for m in messages), messages


def test_is_manifold_recognises_what_quadriflow_needs():
    closed = trimesh.creation.icosphere(subdivisions=2, radius=10.0)
    assert R._is_manifold(closed)

    plane = trimesh.Trimesh(vertices=[[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]],
                            faces=[[0, 1, 2], [0, 2, 3]], process=False)
    assert not R._is_manifold(plane), "an open surface is not a manifold solid"

    two = trimesh.util.concatenate([closed, closed.copy().apply_translation([50, 0, 0])])
    assert not R._is_manifold(two), "two bodies are not one manifold"
