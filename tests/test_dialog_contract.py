"""
The file dialogs return a path, and the interface uses the return value as one:

    const path = await api().select_file_dialog();
    if (path) loadFile(path);

So they must return a string or nothing, in every case — including the ones where
something went wrong. Wrapping them in the @_guarded decorator, which answers with
{"success": false, "error": ...}, produced a truthy object that was then handed to
loadFile() as a path; the user saw "Failed: path.split is not a function" instead of
a dialog. These tests pin the contract on the failure paths, which is where it broke.
"""

import pytest

import app as app_module
from app import AppApi
from engine.service import MeshService

PATH_DIALOGS = ("select_file_dialog", "select_folder_dialog", "select_image_dialog")


@pytest.fixture
def api():
    service = MeshService(log=lambda m, level="info": None, autosave=False)
    adapter = AppApi(service)
    yield adapter
    app_module._state["window"] = None
    service.close()


class BrokenWindow:
    """A window whose native dialog fails — a missing WebView2, a COM error."""

    def create_file_dialog(self, *args, **kwargs):
        raise RuntimeError("the platform dialog backend failed")


class CancellingWindow:
    """The user pressed Escape. pywebview answers None."""

    def create_file_dialog(self, *args, **kwargs):
        return None


class ChoosingWindow:
    def __init__(self, choice):
        self.choice = choice

    def create_file_dialog(self, *args, **kwargs):
        return self.choice


@pytest.mark.parametrize("name", PATH_DIALOGS)
def test_returns_a_string_before_the_window_exists(api, name):
    api.set_window(None)
    assert getattr(api, name)() == ""


@pytest.mark.parametrize("name", PATH_DIALOGS)
def test_returns_a_string_when_the_user_cancels(api, name):
    api.set_window(CancellingWindow())
    assert getattr(api, name)() == ""


@pytest.mark.parametrize("name", PATH_DIALOGS)
def test_returns_a_string_when_the_dialog_itself_fails(api, name):
    """The regression: this used to answer with a dict, which the UI read as a path."""
    api.set_window(BrokenWindow())
    result = getattr(api, name)()
    assert isinstance(result, str), f"{name}() returned {type(result).__name__}: {result!r}"
    assert result == ""


@pytest.mark.parametrize("name", PATH_DIALOGS)
def test_returns_the_chosen_path(api, name):
    api.set_window(ChoosingWindow(["D:/models/part.obj"]))
    assert getattr(api, name)() == "D:/models/part.obj"


def test_folder_dialog_accepts_a_bare_string(api):
    """create_file_dialog returns a plain string for FOLDER on some backends."""
    api.set_window(ChoosingWindow("D:/models"))
    assert api.select_folder_dialog() == "D:/models"


def test_save_dialog_answers_none_rather_than_an_error_object(api):
    for window in (None, CancellingWindow(), BrokenWindow()):
        api.set_window(window)
        assert api._save_dialog("out.stl", ()) is None

    api.set_window(ChoosingWindow(["D:/models/out.stl"]))
    assert api._save_dialog("out.stl", ()) == "D:/models/out.stl"


def test_a_failed_dialog_cancels_the_export_instead_of_starting_one(api):
    """
    Callers branch on falsiness. A truthy error object would have sent a dict into
    the export path; the operation must simply report itself cancelled.
    """
    api.load_demo_model()
    api.set_window(BrokenWindow())

    for call in (lambda: api.export_model_file("stl"),
                 lambda: api.export_report(),
                 lambda: api.export_baked_glb(),
                 lambda: api.export_texture_folder()):
        result = call()
        assert result["success"] is False
        assert result.get("canceled") is True, result


def test_a_failed_image_dialog_does_not_reach_the_engine(api):
    api.load_demo_model()
    api.set_window(BrokenWindow())
    result = api.generate_pbr_maps("")
    assert result["success"] is False
    assert result.get("canceled") is True
