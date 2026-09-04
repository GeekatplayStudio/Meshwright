"""
Closing the model: MeshService.clear() and the AppApi.close_model() bridge
behind the "New" button and the Delete key.
"""
import numpy as np
import pytest
import trimesh
from PIL import Image

from app import AppApi
from engine.service import MeshService, ServiceError


@pytest.fixture
def textured_glb(tmp_path):
    """A small sphere carrying UVs and an embedded base colour texture."""
    mesh = trimesh.creation.icosphere(subdivisions=2, radius=10.0)
    img = Image.fromarray((np.indices((32, 32)).sum(0) % 2 * 255).astype(np.uint8)).convert("RGB")
    mesh.visual = trimesh.visual.TextureVisuals(
        uv=np.random.default_rng(0).random((len(mesh.vertices), 2)),
        material=trimesh.visual.material.PBRMaterial(baseColorTexture=img))
    path = tmp_path / "sphere.glb"
    mesh.export(path)
    return str(path)


@pytest.fixture
def service():
    svc = MeshService(log=lambda m, level="info": None, autosave=False)
    yield svc
    svc.close()


def test_clear_empties_every_kind_of_state(service, textured_glb):
    service.load(textured_glb)
    service.simplify(0.5)
    assert service.materials.has_textures()

    res = service.clear()
    assert res["success"] and res["cleared"] is True
    assert res["closed_file"] == "sphere.glb"

    assert service.file_path is None
    assert service.original is None
    assert service._states == [] and service._redo == [] and service.history == []
    assert service._next_id == 1
    # Textures are part of the model, not of the app.
    assert service.materials.albedo is None
    assert not service.materials.has_textures()


def test_operations_refuse_politely_after_clear(service, textured_glb):
    service.load(textured_glb)
    service.clear()
    for call in (service.analyze, service.repair, service.get_texture_state,
                 lambda: service.export_model("out.stl"), lambda: service.simplify(0.5)):
        with pytest.raises(ServiceError, match="No model loaded"):
            call()


def test_clearing_an_empty_workspace_is_a_no_op(service):
    res = service.clear()
    assert res["success"] is True
    assert res["cleared"] is False
    assert res["closed_file"] is None


def test_load_after_clear_starts_from_state_one(service, textured_glb):
    service.load(textured_glb)
    service.simplify(0.5)
    service.clear()

    res = service.load(textured_glb)
    assert res["state_id"] == 1
    assert res["can_undo"] is False
    assert res["textures"]["has_textures"] is True


def test_clear_drops_the_autosave_snapshot():
    """Snapshots of a model we no longer hold must not be offered for recovery."""
    import os

    from engine.session_store import SessionStore

    svc = MeshService(log=lambda m, level="info": None, autosave=True)
    try:
        mesh = trimesh.creation.icosphere(subdivisions=2, radius=10.0)
        svc.file_path = "sphere.stl"
        svc.store.set_source("sphere.stl")
        svc._commit(mesh, "load", guard=False)
        store = svc.store
        store.flush()
        assert store.journal["states"], "the snapshot should have been written"

        svc.clear()
        assert store.journal["states"] == []
        assert store.journal["source_file"] is None
        assert not [f for f in os.listdir(store.dir) if f.endswith(".npz")]
        assert os.path.basename(store.dir) not in [s["session"] for s in SessionStore.find_recoverable()]

        # The same store keeps working, so the next model autosaves again.
        svc.file_path = "sphere.stl"
        svc._commit(mesh, "load", guard=False)
        store.flush()
        assert len(store.journal["states"]) == 1
    finally:
        svc.close()


def test_close_model_bridge(textured_glb):
    api = AppApi(MeshService(log=lambda m, level="info": None, autosave=False))
    try:
        api.load_model_file(textured_glb)
        assert api.close_model() == {"success": True, "cleared": True, "closed_file": "sphere.glb"}
        # The guarded wrapper turns the follow-up failure into a UI-friendly result.
        assert api.analyze_current() == {"success": False, "error": "No model loaded."}
    finally:
        api.svc.close()
