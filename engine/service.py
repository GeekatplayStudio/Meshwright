"""
MeshService — the single engine behind the desktop UI, the MCP server and
the Python API. No UI framework here.

Guarantees
  * Every mutating call validates its input (engine.validation).
  * Every accepted result becomes an immutable numbered state; undo/redo move
    between states, nothing else ever moves backwards.
  * A change that makes the mesh worse (more critical problems, or most of
    the geometry gone) is rejected unless force=True — the previous state stays.
  * Every accepted state is snapshotted to disk in the background for crash
    recovery.
"""
import os
import time
import json
import base64
import threading
import numpy as np
import trimesh
from trimesh.grouping import group_rows

from engine import validation as V
from engine.model_loader import load_model
from engine.mesh_analysis import analyze_mesh
from engine.mesh_repair import repair_mesh
from engine.mesh_reducer import reduce_mesh
from engine.mesh_cleanup import fix_slivers
from engine.mesh_retopo import retopologize, deviation
from engine.stl_exporter import export_to_stl
from engine.session_store import SessionStore

MAX_SHELLS = 200
UNDO_DEPTH = 30
SEVERITY_RANK = {"critical": 2, "warning": 1, "info": 0}


class ServiceError(Exception):
    pass


class _State:
    __slots__ = ("id", "mesh", "analysis", "operation", "shells")

    def __init__(self, sid, mesh, analysis, operation, shells):
        self.id, self.mesh, self.analysis, self.operation, self.shells = sid, mesh, analysis, operation, shells


def estimate_seconds(operation: str, faces: int) -> float:
    """
    Rough wall-clock estimate, measured on a mid-range desktop. Used only to warn
    the user before a long operation and to drive the progress bar; never exact.
    """
    f = max(1, faces) / 1e6
    return {
        "load": 8 * f + 1,
        "analyse": 4 * f + 0.2,
        "repair": 25 * f + 0.5,
        "fix_slivers": 30 * f + 0.3,
        "simplify": 6 * f + 0.3,
        "retopo": 45 * f + 3,
    }.get(operation, 5 * f + 0.5)


def describe_duration(seconds: float) -> str:
    if seconds < 3:
        return "a moment"
    if seconds < 60:
        return f"about {int(round(seconds / 5.0)) * 5} seconds"
    import math
    mins = seconds / 60.0
    lo = max(1, int(mins))
    hi = max(lo + 1, int(math.ceil(mins * 1.4)))
    return f"about {lo}–{hi} minutes"


class MeshService:
    def __init__(self, log=None, autosave: bool = True, progress=None):
        self.log = log or (lambda m, level="info": None)
        self._progress = progress or (lambda **kw: None)
        self.lock = threading.RLock()
        self.store = SessionStore() if autosave else None
        self.file_path = None
        self.original = None
        self._states = []          # undo stack (oldest .. current)
        self._redo = []
        self._next_id = 1
        self.history = []          # journal of accepted operations

    # ================================================================ state
    @property
    def current(self) -> _State | None:
        return self._states[-1] if self._states else None

    @property
    def mesh(self) -> trimesh.Trimesh | None:
        return self.current.mesh if self.current else None

    @property
    def shells(self) -> list:
        return self.current.shells if self.current else []

    def _require(self) -> _State:
        if not self._states:
            raise ServiceError("No model loaded.")
        return self.current

    def progress(self, **kw):
        try:
            self._progress(**kw)
        except Exception:
            pass

    def _job(self, operation: str, label: str, faces: int | None = None):
        """Announce a long operation with an estimate, and close it out afterwards."""
        svc = self
        n = faces if faces is not None else (len(self.mesh.faces) if self.mesh is not None else 0)
        eta = estimate_seconds(operation, n)

        class Job:
            def __enter__(s):
                s.t = time.perf_counter()
                svc.progress(state="start", operation=operation, label=label,
                             faces=int(n), eta=round(eta, 1), eta_text=describe_duration(eta))
                svc.log(f"{label} — {int(n):,} faces, {describe_duration(eta)}…")
                return s

            def __exit__(s, et, ev, tb):
                elapsed = time.perf_counter() - s.t
                if et is None:
                    svc.progress(state="done", operation=operation, label=label, elapsed=round(elapsed, 2))
                    svc.log(f"{label} done in {elapsed:.2f}s", "ok")
                else:
                    svc.progress(state="error", operation=operation, label=label, error=str(ev))
        return Job()

    def _timer(self, label):
        svc = self

        class T:
            def __enter__(s):
                s.t = time.perf_counter()
                svc.log(f"{label}…")

            def __exit__(s, et, ev, tb):
                if et is None:
                    svc.log(f"{label} done in {time.perf_counter() - s.t:.2f}s", "ok")
        return T()

    # ---------------------------------------------------------------- commit
    def _prepare(self, mesh: trimesh.Trimesh):
        """Shell bookkeeping + analysis of the exact mesh we will keep."""
        shells = []
        try:
            bodies = int(mesh.body_count)
        except Exception:
            bodies = 1
        if bodies > 1:
            with self._timer(f"Separating {bodies} shells"):
                parts = sorted(mesh.split(only_watertight=False), key=lambda p: len(p.faces), reverse=True)
                if len(parts) > MAX_SHELLS:
                    self.log(f"Only the {MAX_SHELLS} largest shells are listed individually", "warn")
                    parts = parts[:MAX_SHELLS] + [trimesh.util.concatenate(parts[MAX_SHELLS:])]
                shells = parts
                mesh = trimesh.util.concatenate(parts)
        with self._timer("Analysing"):
            analysis = analyze_mesh(mesh, self.file_path or "")
        return mesh, analysis, shells

    def _worse(self, before: dict, after: dict) -> str | None:
        """Reason the new state is unacceptable, or None."""
        if before is None:
            return None
        bc = sum(1 for i in before["issues"] if i["severity"] == "critical")
        ac = sum(1 for i in after["issues"] if i["severity"] == "critical")
        if ac > bc:
            return f"critical problems would increase from {bc} to {ac}"
        bf, af = before["stats"]["face_count"], after["stats"]["face_count"]
        if bf > 100 and af < bf * 0.05:
            return f"{100 - round(100 * af / bf)}% of the geometry would be lost"
        if af == 0:
            return "the result is empty"
        return None

    def _commit(self, mesh: trimesh.Trimesh, operation: str, detail: dict | None = None,
                force: bool = False, guard: bool = True) -> dict:
        mesh, analysis, shells = self._prepare(mesh)
        prev = self.current
        if guard and prev is not None and not force:
            reason = self._worse(prev.analysis, analysis)
            if reason:
                self.log(f"{operation} rejected: {reason}. Previous state kept (use force to override).", "error")
                return {"success": False, "rejected": True, "reason": reason,
                        "would_be": {"verdict": analysis["verdict"], "score": analysis["score"]},
                        "state_id": prev.id}
        st = _State(self._next_id, mesh, analysis, operation, shells)
        self._next_id += 1
        self._states.append(st)
        if len(self._states) > UNDO_DEPTH:
            self._states.pop(0)
        self._redo.clear()
        entry = {"state_id": st.id, "operation": operation, "time": time.strftime("%H:%M:%S"),
                 "verdict": analysis["verdict"], "score": analysis["score"],
                 "faces": analysis["stats"]["face_count"]}
        if detail:
            entry.update(detail)
        self.history.append(entry)
        if self.store:
            self.store.save_state(st.id, mesh, operation, {"verdict": analysis["verdict"], "score": analysis["score"]})
        for it in analysis["issues"]:
            self.log(f"{it['title']}: {it['detail']}", "warn" if it["severity"] != "info" else "info")
        self.log(f"State #{st.id} ({operation}): {analysis['verdict']} (score {analysis['score']}), "
                 f"{analysis['stats']['face_count']:,} faces", "ok" if analysis["score"] >= 80 else "warn")
        return self._result(st)

    def _result(self, st: _State, extra: dict | None = None) -> dict:
        out = {"success": True, "state_id": st.id, "operation": st.operation,
               "analysis": st.analysis, "stats": st.analysis["stats"],
               "can_undo": len(self._states) > 1, "can_redo": bool(self._redo)}
        if st.shells:
            out["shells"] = [{
                "index": i, "faces": int(len(p.faces)), "watertight": bool(p.is_watertight),
                "size_mm": [round(float(x), 2) for x in p.extents],
                "volume_cm3": round(abs(float(p.volume)) / 1000.0, 3) if p.is_watertight else None,
            } for i, p in enumerate(st.shells)]
            out["shell_face_counts"] = [len(p.faces) for p in st.shells]
        out["preview"] = self.preview(st.mesh)
        if extra:
            out.update(extra)
        return out

    # ================================================================ operations
    def load(self, path: str) -> dict:
        with self.lock:
            p = V.input_path(path)
            size_mb = os.path.getsize(p) / 1e6
            self.progress(state="start", operation="load", label=f"Loading {os.path.basename(p)}",
                          faces=0, eta=round(2 + size_mb * 0.25, 1),
                          eta_text=describe_duration(2 + size_mb * 0.25))
            self.log(f"Opening {p} ({size_mb:.1f} MB)")
            t0 = time.perf_counter()
            mesh, _ = load_model(p, log=self.log)
            self.file_path = p
            self.original = mesh.copy()
            self._states.clear()
            self._redo.clear()
            self.history.clear()
            if self.store:
                self.store.set_source(p)
            res = self._commit(mesh, "load", guard=False)
            elapsed = time.perf_counter() - t0
            self.progress(state="done", operation="load", label=f"Loaded {os.path.basename(p)}",
                          elapsed=round(elapsed, 2))
            self.log(f"Ready in {elapsed:.2f}s total", "ok")
            return res

    def analyze(self) -> dict:
        with self.lock:
            st = self._require()
            with self._timer("Re-analysing"):
                st.analysis = analyze_mesh(st.mesh, self.file_path or "")
            return {"success": True, "state_id": st.id, "analysis": st.analysis, "stats": st.analysis["stats"]}

    def repair(self, strict_watertight=True, force=False) -> dict:
        with self.lock:
            st = self._require()
            strict = V.boolean(strict_watertight, True)
            with self._job("repair", "Repairing mesh"):
                repaired, report = repair_mesh(st.mesh, strict_watertight=strict, log=self.log)
            for f in report["fixes"]:
                self.log(f"{f['stage']}: {f['description']}", "ok")
            if not report["fixes"]:
                self.log("Nothing needed changing")
            res = self._commit(repaired, "repair", {"fixes": report["fixes"], "changes": report["changes"],
                                                    "passes": report["passes"]}, force=V.boolean(force))
            res["report"] = report
            return res

    def fix_slivers(self, min_angle_deg=1.0, force=False) -> dict:
        with self.lock:
            st = self._require()
            ang = V.number(min_angle_deg, "min_angle_deg", 0.05, 15.0, 1.0)
            with self._job("fix_slivers", "Fixing sliver triangles"):
                cleaned, info = fix_slivers(st.mesh, min_angle_deg=ang, log=self.log)
            self.log(f"Slivers {info['before']} → {info['after']} (collapsed {info['collapsed']}, flipped {info['flipped']})", "ok")
            res = self._commit(cleaned, "fix_slivers", info, force=V.boolean(force))
            res["info"] = info
            return res

    def simplify(self, keep_fraction=0.5, force=False) -> dict:
        with self.lock:
            st = self._require()
            keep = V.number(keep_fraction, "keep_fraction", 0.01, 0.99, 0.5)
            with self._job("simplify", f"Decimating to {int(keep * 100)}% of faces"):
                reduced, info = reduce_mesh(st.mesh, target_factor=keep)
            info["deviation"] = deviation(st.mesh, reduced)
            self.log(f"{info['initial_faces']:,} → {info['final_faces']:,} faces ({info['method_used']}); "
                     f"deviation max {info['deviation']['max_mm']} mm", "ok")
            # Decimation is an explicit request to drop geometry, so the regression guard is
            # advisory here: we warn if new problems appeared instead of refusing the result.
            before = st.analysis
            res = self._commit(reduced, "simplify", info, guard=False)
            bc = sum(1 for i in before["issues"] if i["severity"] == "critical")
            ac = sum(1 for i in res["analysis"]["issues"] if i["severity"] == "critical")
            if ac > bc:
                self.log(f"Simplify introduced {ac - bc} new critical problem type(s) — run Repair, or undo (Ctrl+Z)", "warn")
                res["warning"] = "Simplify introduced new problems; run Repair or undo."
            res["info"] = info
            return res

    def retopo(self, target_faces, method="quadriflow", preserve_sharp=True, adaptive=True) -> dict:
        """Smart retopology / aggressive reduction to an absolute face count."""
        with self.lock:
            st = self._require()
            current = st.analysis["stats"]["face_count"]
            target = int(V.number(target_faces, "target_faces", 20, max(20, current), 1000))
            meth = V.choice(method, "method", ("quadriflow", "isotropic", "quadric"), "quadriflow")
            verb = {"quadriflow": "Smart retopology", "quadric": "Decimating", "isotropic": "Uniform remeshing"}[meth]
            with self._job("retopo", f"{verb} to {target:,} faces"):
                out, info = retopologize(st.mesh, target, method=meth, preserve_sharp=V.boolean(preserve_sharp, True),
                                         adaptive=V.boolean(adaptive, True), log=self.log)
            if info["method_used"] == "none":
                raise ServiceError(info.get("reason", "Retopology made no change."))
            dev = info["deviation"]
            self.log(f"{info['initial_faces']:,} → {info['final_faces']:,} faces ({info['method_used']}); "
                     f"surface deviation max {dev['max_mm']} mm ({dev['relative_pct']}% of size), mean {dev['mean_mm']} mm",
                     "ok" if dev["relative_pct"] < 2 else "warn")
            res = self._commit(out, "retopo", info, guard=False)
            res["info"] = info
            return res

    def remove_shells(self, indices) -> dict:
        with self.lock:
            st = self._require()
            if not st.shells:
                raise ServiceError("Model has a single shell.")
            drop = V.index_list(indices, "indices", len(st.shells))
            keep = [s for i, s in enumerate(st.shells) if i not in drop]
            if not keep:
                raise ServiceError("Cannot remove every shell.")
            removed = sum(len(st.shells[i].faces) for i in drop)
            self.log(f"Removing {len(drop)} shell(s), {removed:,} faces")
            mesh = trimesh.util.concatenate(keep) if len(keep) > 1 else keep[0].copy()
            return self._commit(mesh, "remove_shells", {"removed": drop, "faces_removed": removed}, guard=False)

    def rotate(self, matrix) -> dict:
        """Bake a model-space rotation about the bbox centre. Returns stats + exact bounds (no geometry)."""
        with self.lock:
            st = self._require()
            r = V.rotation_matrix(matrix)
            if np.allclose(r, np.eye(3), atol=1e-9):
                return {"success": True, "unchanged": True, "state_id": st.id}
            centre = st.mesh.bounds.mean(axis=0)
            m = np.eye(4)
            m[:3, :3] = r
            m[:3, 3] = centre - r @ centre
            mesh = st.mesh.copy()
            mesh.apply_transform(m)
            shells = [s.copy() for s in st.shells]
            for s in shells:
                s.apply_transform(m)
            analysis = json.loads(json.dumps(st.analysis))   # deep copy, then move locations
            for it in analysis["issues"]:
                loc = it.get("location")
                if loc:
                    pts = np.asarray(loc["points"], dtype=float)
                    loc["points"] = np.round((r @ (pts - centre).T).T + centre, 3).tolist()
                    loc["center"] = np.round(r @ (np.asarray(loc["center"]) - centre) + centre, 3).tolist()
            analysis["stats"] = analyze_mesh(mesh, self.file_path or "")["stats"]
            new = _State(self._next_id, mesh, analysis, "rotate", shells)
            self._next_id += 1
            self._states.append(new)
            if len(self._states) > UNDO_DEPTH:
                self._states.pop(0)
            self._redo.clear()
            self.history.append({"state_id": new.id, "operation": "rotate", "matrix": r.round(6).tolist(),
                                 "time": time.strftime("%H:%M:%S")})
            if self.store:
                self.store.save_state(new.id, mesh, "rotate", {"verdict": analysis["verdict"], "score": analysis["score"]})
            ext = mesh.extents
            self.log(f"State #{new.id} (rotate): size now {ext[0]:.1f} × {ext[1]:.1f} × {ext[2]:.1f} mm", "ok")
            b = mesh.bounds
            return {"success": True, "state_id": new.id, "stats": analysis["stats"], "centre": centre.tolist(),
                    "bounds": [b[0].tolist(), b[1].tolist()], "can_undo": len(self._states) > 1, "can_redo": False}

    # ---------------------------------------------------------------- history
    def undo(self) -> dict:
        with self.lock:
            if len(self._states) < 2:
                raise ServiceError("Nothing to undo.")
            st = self._states.pop()
            self._redo.append(st)
            self.log(f"Undo: back to state #{self.current.id} ({self.current.operation})", "warn")
            self.history.append({"operation": "undo", "to_state": self.current.id, "time": time.strftime("%H:%M:%S")})
            return self._result(self.current)

    def redo(self) -> dict:
        with self.lock:
            if not self._redo:
                raise ServiceError("Nothing to redo.")
            st = self._redo.pop()
            self._states.append(st)
            self.log(f"Redo: forward to state #{st.id} ({st.operation})", "ok")
            self.history.append({"operation": "redo", "to_state": st.id, "time": time.strftime("%H:%M:%S")})
            return self._result(st)

    def revert(self) -> dict:
        with self.lock:
            if self.original is None:
                raise ServiceError("No model loaded.")
            self.log("Reverting to the original file (explicit request)", "warn")
            return self._commit(self.original.copy(), "revert", guard=False)

    def state_list(self) -> list:
        return [{"id": s.id, "operation": s.operation, "verdict": s.analysis["verdict"], "score": s.analysis["score"],
                 "faces": s.analysis["stats"]["face_count"]} for s in self._states]

    # ---------------------------------------------------------------- recovery
    @staticmethod
    def recoverable_sessions() -> list:
        return SessionStore.find_recoverable()

    def recover(self, session: str) -> dict:
        with self.lock:
            sessions = {s["session"]: s for s in SessionStore.find_recoverable()}
            if session not in sessions:
                raise ServiceError("That session is no longer recoverable.")
            info = sessions[session]
            store = SessionStore(session_id=session)   # reopens the folder
            store.journal["states"] = [info["last"]]
            mesh = store.load_state(info["last"]["id"])
            store._pool.shutdown(wait=False)
            if mesh is None:
                raise ServiceError("Snapshot could not be read.")
            self.file_path = info.get("source_file")
            self.original = mesh.copy()
            self._states.clear()
            self._redo.clear()
            self.history.clear()
            self.log(f"Recovered state #{info['last']['id']} ({info['last']['operation']}) from session {session}", "ok")
            res = self._commit(mesh, "recover", guard=False)
            SessionStore.discard(session)
            return res

    @staticmethod
    def discard_session(session: str):
        SessionStore.discard(session)

    # ---------------------------------------------------------------- export
    def export_stl(self, path: str, scale_unit="mm", align_origin=True) -> dict:
        with self.lock:
            st = self._require()
            p = V.output_path(path, ".stl")
            unit = V.choice(scale_unit, "scale_unit", ("mm", "cm", "in"), "mm")
            with self._job("export", f"Exporting {os.path.basename(p)}"):
                res = export_to_stl(st.mesh, p, scale_unit=unit, align_origin=V.boolean(align_origin, True))
            self.log(f"Saved {res['file_size_mb']} MB, {res['face_count']:,} faces", "ok")
            self.history.append({"operation": "export_stl", "path": p, "time": time.strftime("%H:%M:%S")})
            return {"success": True, "result": res}

    def report(self) -> dict:
        st = self._require()
        analysis = {k: v for k, v in st.analysis.items()}
        analysis["issues"] = [{k: v for k, v in it.items() if k != "location"} for it in analysis["issues"]]
        return {
            "application": "Meshwright",
            "studio": "Geekatplay Studio",
            "author": "Vladimir Chopine",
            "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
            "source_file": self.file_path,
            "state_id": st.id,
            "analysis": analysis,
            "operations": self.history,
            "states": self.state_list(),
        }

    def export_report(self, path: str) -> dict:
        with self.lock:
            p = V.output_path(path, ".json")
            doc = self.report()
            with open(p, "w", encoding="utf-8") as fh:
                json.dump(doc, fh, indent=2)
            self.log(f"Report saved to {p}", "ok")
            return {"success": True, "path": p}

    def default_export_name(self, suffix: str, ext: str) -> str:
        base = os.path.splitext(os.path.basename(self.file_path))[0] if self.file_path else "model"
        return f"{base}{suffix}{ext}"

    # ---------------------------------------------------------------- preview
    @staticmethod
    def preview(mesh: trimesh.Trimesh) -> dict:
        v = np.ascontiguousarray(mesh.vertices, dtype=np.float32)
        f = np.ascontiguousarray(mesh.faces, dtype=np.uint32)
        edges = mesh.edges_sorted
        groups = group_rows(edges, require_count=1) if len(edges) else []
        b = np.ascontiguousarray(edges[groups], dtype=np.uint32) if len(groups) else np.zeros((0, 2), np.uint32)
        return {
            "vertices": base64.b64encode(v.tobytes()).decode("ascii"),
            "faces": base64.b64encode(f.tobytes()).decode("ascii"),
            "boundary_edges": base64.b64encode(b.tobytes()).decode("ascii"),
            "face_count": int(len(f)),
        }

    def close(self):
        if self.store:
            self.store.close(delete=True)
