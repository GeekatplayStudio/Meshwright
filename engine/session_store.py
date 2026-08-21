"""
Crash-safe session store.

Every accepted mesh state is written (in a background thread) as a compressed
.npz next to a journal.json describing the operations. If the app dies, the
next start finds the unfinished session and can restore the last good state.
"""
import os
import json
import time
import threading
import numpy as np
import trimesh
from concurrent.futures import ThreadPoolExecutor

KEEP_STATES = 30


def sessions_root() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    root = os.path.join(base, "Meshwright", "sessions")
    os.makedirs(root, exist_ok=True)
    return root


class SessionStore:
    def __init__(self, session_id: str | None = None):
        self.session_id = session_id or time.strftime("%Y%m%d-%H%M%S") + f"-{os.getpid()}"
        self.dir = os.path.join(sessions_root(), self.session_id)
        os.makedirs(self.dir, exist_ok=True)
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mw-autosave")
        self._lock = threading.Lock()
        self.journal = {"session": self.session_id, "source_file": None, "states": [], "closed": False}
        self._write_journal()

    # ------------------------------------------------------------ writing
    def save_state(self, state_id: int, mesh: trimesh.Trimesh, operation: str, summary: dict):
        """Queue a snapshot; returns immediately."""
        v = np.ascontiguousarray(mesh.vertices, dtype=np.float64)
        f = np.ascontiguousarray(mesh.faces, dtype=np.int64)
        entry = {"id": state_id, "operation": operation, "time": time.strftime("%H:%M:%S"),
                 "faces": int(len(f)), "vertices": int(len(v)), "summary": summary,
                 "file": f"state_{state_id:04d}.npz"}
        self._pool.submit(self._write_state, entry, v, f)

    def _write_state(self, entry, v, f):
        path = os.path.join(self.dir, entry["file"])
        tmp = path + ".tmp"
        try:
            with open(tmp, "wb") as fh:          # explicit handle: numpy would append ".npz" to a bare name
                np.savez_compressed(fh, vertices=v, faces=f)
            os.replace(tmp, path)
            with self._lock:
                self.journal["states"].append(entry)
                self._trim_locked()
                self._write_journal()
        except Exception:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    def _trim_locked(self):
        while len(self.journal["states"]) > KEEP_STATES:
            old = self.journal["states"].pop(0)
            try:
                os.remove(os.path.join(self.dir, old["file"]))
            except OSError:
                pass

    def set_source(self, path: str):
        with self._lock:
            self.journal["source_file"] = path
            self._write_journal()

    def _write_journal(self):
        tmp = os.path.join(self.dir, "journal.json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.journal, fh, indent=1)
        os.replace(tmp, os.path.join(self.dir, "journal.json"))

    def load_state(self, state_id: int) -> trimesh.Trimesh | None:
        self.flush()
        with self._lock:
            entry = next((s for s in self.journal["states"] if s["id"] == state_id), None)
        if entry is None:
            return None
        path = os.path.join(self.dir, entry["file"])
        if not os.path.exists(path):
            return None
        data = np.load(path)
        return trimesh.Trimesh(vertices=data["vertices"], faces=data["faces"], process=False)

    def flush(self):
        """Block until queued snapshots are on disk."""
        self._pool.submit(lambda: None).result()

    def close(self, delete: bool = True):
        self.flush()
        with self._lock:
            self.journal["closed"] = True
            self._write_journal()
        self._pool.shutdown(wait=True)
        if delete:
            self.delete()

    def delete(self):
        try:
            for name in os.listdir(self.dir):
                os.remove(os.path.join(self.dir, name))
            os.rmdir(self.dir)
        except OSError:
            pass

    # ------------------------------------------------------------ recovery
    @staticmethod
    def find_recoverable() -> list:
        """Sessions that were not closed cleanly and still hold a snapshot."""
        root = sessions_root()
        found = []
        for name in sorted(os.listdir(root), reverse=True):
            jpath = os.path.join(root, name, "journal.json")
            if not os.path.exists(jpath):
                continue
            try:
                with open(jpath, encoding="utf-8") as fh:
                    j = json.load(fh)
            except Exception:
                continue
            if j.get("closed") or not j.get("states"):
                continue
            last = j["states"][-1]
            if not os.path.exists(os.path.join(root, name, last["file"])):
                continue
            found.append({"session": name, "source_file": j.get("source_file"), "last": last,
                          "states": len(j["states"])})
        return found

    @staticmethod
    def discard(session: str):
        d = os.path.join(sessions_root(), os.path.basename(session))
        try:
            for name in os.listdir(d):
                os.remove(os.path.join(d, name))
            os.rmdir(d)
        except OSError:
            pass
