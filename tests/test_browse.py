"""
The folder side of Meshwright's own Open dialog.

A file dialog is judged on the awkward cases, so that is what is tested here: a
folder you are not allowed into, one that is gone, one full of files that are not
models, and the memory of what was opened before.
"""
import os

import pytest

from engine import browse


@pytest.fixture(autouse=True)
def own_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))


@pytest.fixture
def folder(tmp_path):
    root = tmp_path / "models"
    (root / "sub").mkdir(parents=True)
    for name in ("a.stl", "b.glb", "c.obj", "notes.txt", "picture.png"):
        (root / name).write_bytes(b"x" * 10)
    return root


# ------------------------------------------------------------------ listing
def test_models_are_listed_and_other_files_are_counted_but_hidden(folder):
    listed = browse.listing(str(folder))
    assert [f["name"] for f in listed["files"]] == ["a.stl", "b.glb", "c.obj"]
    assert [d["name"] for d in listed["folders"]] == ["sub"]
    assert listed["other_files"] == 2, "the txt and the png should be counted, not shown"
    assert listed["error"] is None


def test_every_file_can_be_shown_when_asked(folder):
    listed = browse.listing(str(folder), show_all=True)
    assert [f["name"] for f in listed["files"]] == ["a.stl", "b.glb", "c.obj", "notes.txt", "picture.png"]
    assert [f["known"] for f in listed["files"]] == [True, True, True, False, False]


def test_a_folder_that_is_gone_says_so_rather_than_failing(tmp_path):
    listed = browse.listing(str(tmp_path / "never"))
    assert listed["error"] and "not there" in listed["error"]
    assert listed["files"] == [] and listed["folders"] == []


def test_a_file_handed_over_as_a_folder_says_so(folder):
    listed = browse.listing(str(folder / "a.stl"))
    assert listed["error"], "a file is not a folder"


def test_a_folder_that_is_not_allowed_says_who_refused(folder, monkeypatch):
    """The message must not claim the folder was deleted — it is there, and closed."""
    def refuse(path):
        raise PermissionError(13, "Access is denied")
    monkeypatch.setattr(os, "scandir", refuse)
    listed = browse.listing(str(folder))
    assert "will not let" in listed["error"]


def test_one_unreadable_file_does_not_spoil_the_whole_folder(folder, monkeypatch):
    real = os.scandir

    class Awkward:
        def __init__(self, entry):
            self._entry = entry
            self.name, self.path = entry.name, entry.path

        def is_dir(self, **kw):
            return self._entry.is_dir(**kw)

        def is_file(self, **kw):
            return self._entry.is_file(**kw)

        def stat(self, **kw):
            if self.name == "b.glb":
                raise OSError(5, "The device is not ready")
            return self._entry.stat(**kw)

    def wrapped(path):
        class Holder:
            def __enter__(self_inner):
                self_inner._it = real(path)
                return (Awkward(e) for e in self_inner._it)

            def __exit__(self_inner, *a):
                self_inner._it.close()
        return Holder()

    monkeypatch.setattr(os, "scandir", wrapped)
    listed = browse.listing(str(folder))
    assert [f["name"] for f in listed["files"]] == ["a.stl", "c.obj"]
    assert listed["error"] is None


def test_a_huge_folder_is_listed_in_part_and_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(browse, "MAX_ENTRIES", 5)
    root = tmp_path / "many"
    root.mkdir()
    for n in range(12):
        (root / f"m{n}.stl").write_bytes(b"x")
    listed = browse.listing(str(root))
    assert listed["truncated"] is True
    assert 0 < len(listed["files"]) <= 5


def test_the_path_is_broken_into_steps_that_lead_back_up(folder):
    crumbs = browse.listing(str(folder))["crumbs"]
    assert crumbs[-1]["label"] == "models"
    assert os.path.isdir(crumbs[0]["path"]), "the first step should be the drive"
    assert crumbs[-2]["path"] == os.path.dirname(str(folder))


# ------------------------------------------------------------------ places
def test_places_offers_somewhere_to_start_and_only_local_drives():
    where = browse.places()
    assert os.path.isdir(where["start"])
    assert all(d["path"].endswith(("\\", "/")) for d in where["drives"])


def test_the_dialog_reopens_where_the_last_model_came_from(folder):
    browse.remember(str(folder / "a.stl"))
    assert browse.places()["start"] == str(folder)


def test_a_folder_that_has_since_been_deleted_is_not_offered_as_the_start(tmp_path, folder):
    browse.note_folder(str(folder))
    for name in os.listdir(folder):
        path = folder / name
        if path.is_file():
            path.unlink()
    (folder / "sub").rmdir()
    folder.rmdir()
    assert os.path.isdir(browse.places()["start"])


# ------------------------------------------------------------------ recent files
def test_recently_opened_files_come_back_newest_first(folder):
    browse.remember(str(folder / "a.stl"))
    browse.remember(str(folder / "b.glb"))
    assert [f["name"] for f in browse.recent_files()] == ["b.glb", "a.stl"]


def test_opening_the_same_file_twice_does_not_list_it_twice(folder):
    browse.remember(str(folder / "a.stl"))
    browse.remember(str(folder / "b.glb"))
    browse.remember(str(folder / "a.stl"))
    assert [f["name"] for f in browse.recent_files()] == ["a.stl", "b.glb"]


def test_a_recent_file_that_has_been_deleted_is_dropped_quietly(folder):
    browse.remember(str(folder / "a.stl"))
    browse.remember(str(folder / "b.glb"))
    (folder / "a.stl").unlink()
    assert [f["name"] for f in browse.recent_files()] == ["b.glb"]


def test_the_list_of_recent_files_does_not_grow_without_end(folder, monkeypatch):
    monkeypatch.setattr(browse, "MAX_RECENT", 3)
    for n in range(6):
        path = folder / f"m{n}.stl"
        path.write_bytes(b"x")
        browse.remember(str(path))
    assert [f["name"] for f in browse.recent_files()] == ["m5.stl", "m4.stl", "m3.stl"]


def test_recent_files_can_be_forgotten(folder):
    browse.remember(str(folder / "a.stl"))
    browse.forget_all()
    assert browse.recent_files() == []


def test_a_damaged_memory_file_is_ignored_rather_than_fatal(folder):
    browse.remember(str(folder / "a.stl"))
    with open(browse.state_file(), "w", encoding="utf-8") as f:
        f.write("{ this is not json")
    assert browse.recent_files() == []
    assert os.path.isdir(browse.places()["start"])


def test_remembering_still_works_when_it_cannot_be_written_down(folder, tmp_path, monkeypatch):
    """
    Remembering is a convenience. If the place it is kept cannot be written — a
    locked-down profile, a full disk — opening models must carry on regardless.
    """
    blocked = tmp_path / "not-a-folder"
    blocked.write_text("a file standing where the folder should be")
    monkeypatch.setenv("LOCALAPPDATA", str(blocked))
    browse.remember(str(folder / "a.stl"))          # must not raise
    assert browse.recent_files() == []
    assert os.path.isdir(browse.places()["start"]), "it must still know somewhere to start"
