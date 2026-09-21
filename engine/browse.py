"""
Somewhere to look for a model, and somewhere to start looking.

This is the plumbing behind Meshwright's own Open dialog: the folders down the
left, what is in the folder in the middle, and the memory of where you were last
time. Nothing here reads a model — engine.quicklook does that.

The rules it follows are the ones a file dialog is judged by:

* Nothing blocks. A disconnected network drive can take half a minute to answer,
  so drives are read from the bitmask Windows already holds and only the local
  ones are offered. A folder that will not open says so instead of hanging.
* Nothing is silently empty. A folder with no models says whether it has other
  files in it, so "empty" is never mistaken for "broken".
* What you did last time is remembered — the last folder and the last files —
  because the whole point is not having to navigate there again.
"""
import json
import os

from engine.quicklook import size_text
from engine.validation import LOAD_EXTENSIONS

MAX_RECENT = 24
MAX_ENTRIES = 5000          # a folder larger than this is listed in part, and says so

_FILE_ATTRIBUTE_HIDDEN = 0x2
_FILE_ATTRIBUTE_SYSTEM = 0x4

_DRIVE_REMOVABLE, _DRIVE_FIXED, _DRIVE_REMOTE, _DRIVE_CDROM = 2, 3, 4, 5


def state_file() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    folder = os.path.join(base, "Meshwright")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "browser.json")


def _read_state() -> dict:
    try:
        with open(state_file(), "r", encoding="utf-8") as f:
            state = json.load(f)
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(state: dict):
    try:
        with open(state_file(), "w", encoding="utf-8") as f:
            json.dump(state, f, indent=1)
    except OSError:
        pass                       # remembering is a convenience, never a reason to fail


# --------------------------------------------------------------------------- remembering
def remember(path: str):
    """Record a file that was actually opened, and the folder it came from."""
    try:
        full = os.path.abspath(path)
    except (OSError, ValueError):
        return
    state = _read_state()
    recent = [r for r in state.get("recent", [])
              if isinstance(r, str) and os.path.normcase(r) != os.path.normcase(full)]
    state["recent"] = ([full] + recent)[:MAX_RECENT]
    state["folder"] = os.path.dirname(full)
    _write_state(state)


def recent_files() -> list[dict]:
    """The files opened before, newest first, with the ones since deleted left out."""
    out = []
    for path in _read_state().get("recent", []):
        try:
            stat = os.stat(path)
        except (OSError, ValueError):
            continue
        out.append({"name": os.path.basename(path), "path": path, "is_dir": False,
                    "ext": os.path.splitext(path)[1].lower(), "size": stat.st_size,
                    "size_text": size_text(stat.st_size), "modified": stat.st_mtime,
                    "folder": os.path.dirname(path)})
    return out


def forget_all():
    state = _read_state()
    state["recent"] = []
    _write_state(state)


# --------------------------------------------------------------------------- where to look
def _drives() -> list[dict]:
    """
    The drives on this machine, without touching any of them.

    os.path.exists on a mapped drive whose server is gone blocks until it times
    out, which would freeze the dialog on open. The bitmask and the drive type
    both come out of memory Windows already has.
    """
    if os.name != "nt":
        return [{"label": "/", "path": "/", "kind": "drive"}]
    import ctypes
    out = []
    try:
        mask = ctypes.windll.kernel32.GetLogicalDrives()
        for bit in range(26):
            if not mask & (1 << bit):
                continue
            letter = f"{chr(ord('A') + bit)}:\\"
            kind = ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(letter))
            if kind in (_DRIVE_FIXED, _DRIVE_REMOVABLE):
                out.append({"label": letter[:2], "path": letter, "kind": "drive"})
    except (OSError, AttributeError):
        pass
    return out


def places() -> dict:
    """The shortcuts down the left-hand side, and where the dialog should open."""
    home = os.path.expanduser("~")
    wanted = [("Desktop", "Desktop"), ("Documents", "Documents"), ("Downloads", "Downloads"),
              ("3D Objects", "3D Objects"), ("Pictures", "Pictures")]
    folders = []
    for label, name in wanted:
        path = os.path.join(home, name)
        if os.path.isdir(path):
            folders.append({"label": label, "path": path, "kind": "folder"})
    state = _read_state()
    last = state.get("folder")
    if last and os.path.isdir(last):
        start = last
    elif folders:
        start = folders[0]["path"]
    else:
        start = home
    return {"folders": folders, "drives": _drives(), "home": home,
            "start": start, "recent_count": len(state.get("recent", []))}


def crumbs(path: str) -> list[dict]:
    """The path broken into clickable pieces, root first."""
    path = os.path.abspath(path)
    out = []
    while True:
        head, tail = os.path.split(path)
        if not tail:
            out.append({"label": path, "path": path})
            break
        out.append({"label": tail, "path": path})
        path = head
    return list(reversed(out))


def _hidden(entry) -> bool:
    if entry.name.startswith("."):
        return True
    try:
        return bool(entry.stat(follow_symlinks=False).st_file_attributes
                    & (_FILE_ATTRIBUTE_HIDDEN | _FILE_ATTRIBUTE_SYSTEM))
    except (OSError, AttributeError):
        return False


def listing(path: str, show_all: bool = False) -> dict:
    """
    What is in one folder: its sub-folders, and the model files in it.

    `show_all` adds the files Meshwright cannot open, so that a model with an
    unexpected extension can still be found and tried.
    """
    if not path:
        path = places()["start"]
    path = os.path.abspath(path)
    result = {"path": path, "crumbs": crumbs(path), "folders": [], "files": [],
              "parent": os.path.dirname(path) if os.path.dirname(path) != path else None,
              "error": None, "other_files": 0, "truncated": False}
    folders, files, others, seen = [], [], 0, 0
    try:
        # Opening it is the test. Asking first whether it exists gives the wrong
        # answer for a folder that is there but closed to you: the check fails the
        # same way a missing one does, and the person is told it was deleted.
        with os.scandir(path) as entries:
            for entry in entries:
                seen += 1
                if seen > MAX_ENTRIES:
                    result["truncated"] = True
                    break
                try:
                    if _hidden(entry):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        folders.append({"name": entry.name, "path": entry.path, "is_dir": True})
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    ext = os.path.splitext(entry.name)[1].lower()
                    known = ext in LOAD_EXTENSIONS
                    if not known:
                        others += 1
                        if not show_all:
                            continue
                    stat = entry.stat()
                    files.append({"name": entry.name, "path": entry.path, "is_dir": False,
                                  "ext": ext, "known": known, "size": stat.st_size,
                                  "size_text": size_text(stat.st_size), "modified": stat.st_mtime})
                except OSError:
                    continue                     # one unreadable entry is not a broken folder
    except PermissionError:
        result["error"] = "Windows will not let Meshwright look inside this folder."
        return result
    except FileNotFoundError:
        result["error"] = "This folder is not there any more."
        return result
    except NotADirectoryError:
        result["error"] = "This is a file, not a folder."
        return result
    except OSError as trouble:
        # A drive that is not ready (an empty card reader, a disconnected share)
        # lands here, and the reason is worth showing rather than swallowing.
        result["error"] = f"This folder could not be opened ({trouble.strerror or 'unknown reason'})."
        return result

    folders.sort(key=lambda e: e["name"].lower())
    files.sort(key=lambda e: e["name"].lower())
    result["folders"], result["files"], result["other_files"] = folders, files, others
    return result


def note_folder(path: str):
    """Remember where the user was, so the next Open starts there."""
    if path and os.path.isdir(path):
        state = _read_state()
        state["folder"] = os.path.abspath(path)
        _write_state(state)
