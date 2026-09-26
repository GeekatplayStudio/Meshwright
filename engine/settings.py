"""
The handful of things Meshwright remembers between runs.

Not preferences in the usual sense — those live with the controls that set them.
This is for the answers that are about *this machine*: where Blender was installed,
which printer sits on the bench. Getting them wrong costs the person a hunt through
folders, so once told, Meshwright should not ask again.

Kept beside the crash-recovery snapshots in %LOCALAPPDATA%\\Meshwright, as one small
JSON file. Failing to read or write it is never fatal: a locked-down profile or a
full disk should cost someone a remembered setting, not the ability to open a model.
"""
import json
import os
import threading

_lock = threading.Lock()


def settings_file() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    folder = os.path.join(base, "Meshwright")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "settings.json")


def read() -> dict:
    try:
        with open(settings_file(), "r", encoding="utf-8") as handle:
            found = json.load(handle)
        return found if isinstance(found, dict) else {}
    except (OSError, ValueError):
        return {}


def get(key: str, default=None):
    return read().get(key, default)


def put(key: str, value) -> dict:
    """Store one setting. Returns everything, as it now stands."""
    with _lock:
        current = read()
        if value is None:
            current.pop(key, None)
        else:
            current[key] = value
        try:
            with open(settings_file(), "w", encoding="utf-8") as handle:
                json.dump(current, handle, indent=1)
        except OSError:
            pass                # remembering is a convenience, never a requirement
        return current
