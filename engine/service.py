"""
MeshService — engine behind desktop UI, MCP server, ComfyUI nodes, and Python API.
"""
import json
import os
import threading
import time

import numpy as np
import trimesh

from engine import validation as V
from engine.demo_model import DEMO_NAME, build_demo_mesh
from engine.estimation import describe_duration, estimate_seconds
from engine.mesh_analysis import analyze_mesh
from engine.mesh_cleanup import fix_slivers
from engine.mesh_exporter import EXPORT_FORMATS, export_to_format
from engine.mesh_reducer import reduce_mesh
from engine.mesh_repair import repair_mesh
from engine.mesh_retopo import deviation, missing_engine_message, retopologize
from engine.model_loader import load_model
from engine.preview import DEFAULT_PREVIEW_FACES, build_mesh_preview
from engine.session_store import SessionStore
from engine.texture import MaterialManager
from engine.texture import uv_channel as UV
from engine.texture_service import TextureServiceMixin

MAX_SHELLS = 200
UNDO_DEPTH = 30
SEVERITY_RANK = {"critical": 2, "warning": 1, "info": 0}

# _commit's default: take the UV channel from the state being replaced and carry it
# onto the new geometry. Passing uv= explicitly overrides that (load, unwrap).
_INHERIT = object()


class ServiceError(Exception):
    pass


class _State:
    """One accepted mesh, plus the UV channel that belongs to it.

    `uv` is an (F, 3, 2) per-face-corner array or None. It travels with the state
    rather than inside the mesh so that undo, redo and the safety guard all restore
    geometry and texture coordinates together.
    """

    def __init__(self, sid, mesh, analysis, operation, shells, uv=None):
        self.id, self.mesh, self.analysis, self.operation, self.shells = sid, mesh, analysis, operation, shells
        self.uv = uv


class MeshService(TextureServiceMixin):
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
        self.materials = MaterialManager()
        self._original_uv = None   # UV channel of the file as it was opened
        # How much of the model the viewport draws. None lets it choose, so a dense
        # model appears quickly; the interface can raise it to 1.0 on request. This
        # only ever affects what is drawn — never the mesh, the diagnostics or an
        # export.
        self.preview_detail: float | None = None
        self.preview_max_faces: int = DEFAULT_PREVIEW_FACES

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

    @property
    def corner_uv(self):
        """Per-face-corner UVs of the current state, or None."""
        return self.current.uv if self.current else None

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
        """
        Shell bookkeeping + analysis of the exact mesh we will keep.

        Returns (mesh, analysis, shells, face_order). Separating a multi-body model
        renumbers its faces, and `face_order` says how: face i of the returned mesh
        was face `face_order[i]` of the one passed in. It is None when nothing moved.

        The components are found here rather than through `mesh.split()`, which does
        the same work but throws the mapping away. Keeping it means a UV channel that
        was already correct can simply be permuted, instead of being re-projected
        onto geometry it already matched — on a five-million-face model that is the
        difference between a hundred milliseconds and several minutes.
        """
        shells = []
        face_order = None
        try:
            bodies = int(mesh.body_count)
        except Exception:
            bodies = 1
        if bodies > 1:
            with self._timer(f"Separating {bodies} shells"):
                groups = trimesh.graph.connected_components(
                    mesh.face_adjacency, nodes=np.arange(len(mesh.faces)), min_len=1)
                groups = sorted(groups, key=len, reverse=True)
                if len(groups) > MAX_SHELLS:
                    self.log(f"Only the {MAX_SHELLS} largest shells are listed individually", "warn")
                    groups = groups[:MAX_SHELLS] + [np.concatenate(groups[MAX_SHELLS:])]
                shells = mesh.submesh(groups, only_watertight=False, append=False)
                mesh = trimesh.util.concatenate(shells)
                face_order = np.concatenate(groups) if len(groups) > 1 else np.asarray(groups[0])
        with self._timer("Analysing"):
            analysis = analyze_mesh(mesh, self.file_path or "")
        return mesh, analysis, shells, face_order

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

    def _carry_uv(self, source_mesh, source_uv, final_mesh, matches_source: bool, face_order):
        """
        Bring a UV channel onto the mesh that is about to be committed.

        Three cases, cheapest first. The UVs already describe this geometry and the
        faces did not move: use them. They describe it but the faces were permuted by
        shell separation: permute them the same way, which is exact. Otherwise the
        geometry genuinely changed, and every corner has to be re-projected onto the
        old surface — the expensive path, and the only one that loses accuracy.
        """
        if source_uv is None or source_mesh is None:
            return None

        if matches_source:
            # The permutation is tested first on purpose. Separating shells reorders
            # the faces but does not change how many there are, so the "nothing moved"
            # test below is still true at that point — and taking it would hand back a
            # UV channel in the old face order, mapping every shell to some other
            # shell's artwork. A 366-piece model came out with half its surface
            # painted from the wrong part of the atlas.
            if face_order is not None and len(source_uv) == len(face_order):
                return np.ascontiguousarray(source_uv[face_order])
            if len(source_uv) == len(final_mesh.faces):
                return source_uv

        with self._job("uv_transfer", "Carrying texture coordinates across",
                       faces=len(final_mesh.faces)):
            carried = UV.transfer(source_mesh, source_uv, final_mesh)
        if carried is None:
            self.log("Texture coordinates could not be carried across this change", "warn")
        return carried

    def _commit(self, mesh: trimesh.Trimesh, operation: str, detail: dict | None = None,
                force: bool = False, guard: bool = True, uv=_INHERIT) -> dict:
        prev_state = self.current
        if uv is _INHERIT:
            src_mesh = prev_state.mesh if prev_state else None
            src_uv = prev_state.uv if prev_state else None
            same_faces = False           # the operation reshaped the mesh; re-project
        else:
            src_mesh, src_uv = mesh, uv
            same_faces = True            # uv was built for exactly this mesh

        mesh, analysis, shells, face_order = self._prepare(mesh)
        new_uv = self._carry_uv(src_mesh, src_uv, mesh, same_faces, face_order)
        prev = self.current
        if guard and prev is not None and not force:
            reason = self._worse(prev.analysis, analysis)
            if reason:
                self.log(f"{operation} rejected: {reason}. Previous state kept (use force to override).", "error")
                return {"success": False, "rejected": True, "reason": reason,
                        "would_be": {"verdict": analysis["verdict"], "score": analysis["score"]},
                        "state_id": prev.id}
        st = _State(self._next_id, mesh, analysis, operation, shells, uv=new_uv)
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
                "index": i, "faces": len(p.faces), "watertight": bool(p.is_watertight),
                "size_mm": [round(float(x), 2) for x in p.extents],
                "volume_cm3": round(abs(float(p.volume)) / 1000.0, 3) if p.is_watertight else None,
            } for i, p in enumerate(st.shells)]
            out["shell_face_counts"] = [len(p.faces) for p in st.shells]
        out["preview"] = self._build_preview(st)
        out["has_uv"] = st.uv is not None
        if "shell_face_counts" in out["preview"]:
            # The viewport is drawing a decimated copy, so the piece sizes it needs
            # to colour by are the decimated ones.
            out["shell_face_counts"] = out["preview"]["shell_face_counts"]
        try:
            out["textures"] = self.get_texture_state()
        except Exception as exc:
            # Never let a texture problem hide a successful mesh operation, but do
            # not let it vanish silently either.
            self.log(f"Texture state unavailable: {exc}", "warn")
        if extra:
            out.update(extra)
        return out

    # ================================================================ operations
    def inspect_parts(self, path: str) -> dict:
        """
        What is in a file, before opening it. Changes nothing and keeps no state.

        Cheap by design — a Blender project is listed in under two seconds where
        exporting it takes minutes, and a glTF is read from its header without
        touching its geometry — so it is affordable to ask before committing.
        """
        from engine.scene_parts import list_parts
        return {"success": True, **list_parts(V.input_path(path), log=self.log)}

    def load(self, path: str, keep=None) -> dict:
        with self.lock:
            p = V.input_path(path)
            if keep is not None:
                keep = [str(name) for name in keep][:5000]
                if not keep:
                    raise ServiceError("No objects were chosen, so there is nothing to open.")
            size_mb = os.path.getsize(p) / 1e6
            self.progress(state="start", operation="load", label=f"Loading {os.path.basename(p)}",
                          faces=0, eta=round(2 + size_mb * 0.25, 1),
                          eta_text=describe_duration(2 + size_mb * 0.25))
            self.log(f"Opening {p} ({size_mb:.1f} MB)")
            t0 = time.perf_counter()
            mesh, _ = load_model(p, log=self.log, with_stats=False, keep=keep)
            corner_uv = mesh.metadata.pop("corner_uv", None)
            self.file_path = p
            self._states.clear()
            self._redo.clear()
            self.history.clear()
            self.materials.clear()
            if self.store:
                self.store.set_source(p)
            mesh, corner_uv = self._detect_and_bind_textures(p, mesh, corner_uv)
            self.original = mesh.copy()
            self._original_uv = corner_uv
            res = self._commit(mesh, "load", guard=False, uv=corner_uv)
            elapsed = time.perf_counter() - t0
            self.progress(state="done", operation="load", label=f"Loaded {os.path.basename(p)}",
                          elapsed=round(elapsed, 2))
            self.log(f"Ready in {elapsed:.2f}s total", "ok")
            return res

    def load_demo(self) -> dict:
        """Load the built-in test object."""
        with self.lock:
            self.progress(state="start", operation="load", label="Loading the demo model",
                          faces=0, eta=1.0, eta_text="a moment")
            t0 = time.perf_counter()
            mesh = build_demo_mesh()
            self.file_path = DEMO_NAME
            self.original = mesh.copy()
            self._original_uv = None
            self._states.clear()
            self._redo.clear()
            self.history.clear()
            self.materials.clear()
            if self.store:
                self.store.set_source(DEMO_NAME)
            self.log("Loaded the built-in demo model: a sphere with a hole, "
                     "flipped faces and a loose second piece. Try Repair on it.", "ok")
            res = self._commit(mesh, "load", guard=False)
            self.progress(state="done", operation="load", label="Loaded the demo model",
                          elapsed=round(time.perf_counter() - t0, 2))
            res["is_demo"] = True
            return res

    def clear(self) -> dict:
        """Drop the loaded model and return to an empty workspace.

        Undo history, the autosave snapshot and the texture set all go with it:
        after this the service is in the same state as a freshly started app.
        """
        with self.lock:
            had_model = bool(self._states) or self.file_path is not None
            name = os.path.basename(self.file_path) if self.file_path else None
            self.file_path = None
            self.original = None
            self._original_uv = None
            self._states.clear()
            self._redo.clear()
            self.history.clear()
            self._next_id = 1
            self.materials.clear()
            if self.store:
                # The snapshots describe a model we no longer hold; keeping them would
                # offer a stale recovery on the next start.
                self.store.reset()
            if had_model:
                self.log(f"Closed {name or 'the model'} — workspace is empty", "ok")
            return {"success": True, "cleared": had_model, "closed_file": name}

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
            unavailable = missing_engine_message(meth)
            if unavailable:
                raise ServiceError(unavailable)
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

    # ------------------------------------------------------------------ working on chosen pieces
    #
    # A model made of separate pieces is usually a model that needs different things
    # done to different parts of it: the figure kept and the base thinned, two halves
    # fused into one solid, an arm moved clear of the body before printing. These all
    # take the same shape — take the pieces apart, do something to the chosen ones,
    # put them back — so they share one helper and differ only in that middle step.

    def _chosen(self, indices, what: str):
        """(state, sorted chosen indices, the rest) — or a refusal that names the reason."""
        st = self._require()
        if not st.shells:
            raise ServiceError(f"This model is a single piece, so there is nothing to {what}.")
        picked = sorted(set(V.index_list(indices, "indices", len(st.shells))))
        if not picked:
            raise ServiceError(f"No pieces were selected to {what}.")
        others = [s for i, s in enumerate(st.shells) if i not in set(picked)]
        return st, picked, others

    def _piece_vertices(self, st, picked):
        """
        Which vertices belong to the chosen pieces.

        The mesh is the pieces concatenated in order, so each one owns a contiguous
        run of faces; and because a piece is a connected component, no vertex is
        shared with another. That makes a straight translation safe to apply in
        place — which matters, because doing it in place is what lets the texture
        coordinates come through untouched instead of being re-projected onto
        geometry that has just moved.
        """
        counts = [len(s.faces) for s in st.shells]
        starts = np.concatenate([[0], np.cumsum(counts)])
        rows = np.concatenate([np.arange(starts[i], starts[i + 1], dtype=np.int64)
                               for i in picked]) if picked else np.zeros(0, np.int64)
        return np.unique(np.asarray(st.mesh.faces, np.int64)[rows])

    def move_pieces(self, indices, offset) -> dict:
        """Shift the chosen pieces bodily. Geometry moves; nothing else changes."""
        with self.lock:
            st, picked, _ = self._chosen(indices, "move")
            shift = np.asarray([V.number(v, "offset", -1e6, 1e6, 0.0) for v in list(offset)[:3]], float)
            if shift.shape != (3,):
                raise ServiceError("A move needs three numbers: x, y and z.")
            if np.allclose(shift, 0):
                return {"success": True, "unchanged": True, "state_id": st.id}

            mesh = st.mesh.copy()
            vertices = self._piece_vertices(st, picked)
            mesh.vertices[vertices] += shift
            self.log(f"Moved {len(picked)} piece(s) by "
                     f"({shift[0]:.2f}, {shift[1]:.2f}, {shift[2]:.2f}) mm")
            # Topology, face order and vertex count are all untouched, so the UV
            # channel belongs to this mesh exactly as it stands.
            return self._commit(mesh, "move_pieces",
                                {"moved": picked, "offset": [round(float(v), 4) for v in shift]},
                                guard=False, uv=st.uv)

    def merge_pieces(self, indices) -> dict:
        """
        Fuse the chosen pieces into one solid where they touch.

        A boolean union, not a concatenation: two halves that overlap come out as a
        single watertight body a slicer can fill. Pieces that do not touch cannot be
        fused by any amount of arithmetic, and the result says so rather than
        implying a join that is not there.
        """
        with self.lock:
            st, picked, others = self._chosen(indices, "merge")
            if len(picked) < 2:
                raise ServiceError("Merging needs at least two pieces.")
            parts = [st.shells[i].copy() for i in picked]
            before = sum(len(p.faces) for p in parts)
            with self._job("merge_pieces", f"Merging {len(picked)} pieces", faces=before):
                try:
                    fused = trimesh.boolean.union(parts)
                except Exception as exc:
                    raise ServiceError(
                        "These pieces could not be fused — a boolean union needs each of them to "
                        f"be a closed solid. Repair the model first, then try again. ({exc})")
            if fused is None or not len(fused.faces):
                raise ServiceError("Merging produced nothing; the pieces may not be closed solids.")
            try:
                bodies = int(fused.body_count)
            except Exception:
                bodies = 1
            if bodies > 1:
                self.log(f"{len(picked)} pieces are now one object, but {bodies} of them do not "
                         "touch, so they remain separate bodies", "warn")
            else:
                self.log(f"{len(picked)} pieces fused into one solid", "ok")
            mesh = trimesh.util.concatenate([fused] + others) if others else fused
            return self._commit(mesh, "merge_pieces",
                                {"merged": picked, "bodies_after": bodies,
                                 "faces_before": before, "faces_after": int(len(fused.faces))},
                                guard=False)

    def optimize_pieces(self, indices, keep_fraction: float = 0.5) -> dict:
        """Reduce only the chosen pieces, leaving the rest at full detail."""
        with self.lock:
            st, picked, _ = self._chosen(indices, "reduce")
            keep = V.number(keep_fraction, "keep_fraction", 0.01, 0.99, 0.5)
            before = sum(len(st.shells[i].faces) for i in picked)
            with self._job("simplify", f"Reducing {len(picked)} piece(s)", faces=before):
                rebuilt, after = [], 0
                for index, shell in enumerate(st.shells):
                    if index not in set(picked):
                        rebuilt.append(shell)
                        continue
                    reduced, _info = reduce_mesh(shell.copy(), target_factor=keep, log=self.log)
                    rebuilt.append(reduced)
                    after += len(reduced.faces)
            mesh = trimesh.util.concatenate(rebuilt) if len(rebuilt) > 1 else rebuilt[0]
            self.log(f"{before:,} → {after:,} faces across {len(picked)} piece(s); "
                     "the rest of the model is untouched", "ok")
            res = self._commit(mesh, "optimize_pieces",
                               {"pieces": picked, "faces_before": before, "faces_after": after},
                               guard=False)
            res["info"] = {"pieces": len(picked), "faces_before": before, "faces_after": after}
            return res

    def isolate_pieces(self, indices) -> dict:
        """Keep only the chosen pieces — the inverse of removing them."""
        with self.lock:
            st, picked, _ = self._chosen(indices, "isolate")
            if len(picked) == len(st.shells):
                return {"success": True, "unchanged": True, "state_id": st.id}
            dropped = [i for i in range(len(st.shells)) if i not in set(picked)]
            return self.remove_shells(dropped)

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
            # A rigid transform leaves the topology alone, so the UV channel is
            # still exactly right — carry it, do not re-project it.
            new = _State(self._next_id, mesh, analysis, "rotate", shells, uv=st.uv)
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
            return self._commit(self.original.copy(), "revert", guard=False, uv=self._original_uv)

    def state_list(self) -> list:
        return [{"id": s.id, "operation": s.operation, "verdict": s.analysis["verdict"], "score": s.analysis["score"],
                 "faces": s.analysis["stats"]["face_count"]} for s in self._states]

    # ---------------------------------------------------------------- recovery
    recoverable_sessions = staticmethod(SessionStore.find_recoverable)
    discard_session = staticmethod(SessionStore.discard)

    def recover(self, session: str) -> dict:
        with self.lock:
            mesh, info = SessionStore.restore_session(session)
            if mesh is None:
                raise ServiceError("Snapshot could not be read or session no longer recoverable.")
            self.file_path = info.get("source_file")
            self.original = mesh.copy()
            # Snapshots hold geometry only, so anything the previous model had loaded
            # would be applied to a mesh it does not belong to.
            self._original_uv = None
            self._states.clear()
            self._redo.clear()
            self.history.clear()
            self.materials.clear()
            self.log(f"Recovered state #{info['last']['id']} ({info['last']['operation']}) from session {session}", "ok")
            res = self._commit(mesh, "recover", guard=False, uv=None)
            SessionStore.discard(session)
            return res

    # ---------------------------------------------------------------- export
    def export_stl(self, path: str, scale_unit="mm", align_origin=True) -> dict:
        return self.export_model(path, "stl", scale_unit, align_origin)

    def export_model(self, path: str, export_format="stl", scale_unit="mm", align_origin=True) -> dict:
        with self.lock:
            st = self._require()
            fmt = V.choice(export_format, "export_format", tuple(EXPORT_FORMATS), "stl")
            p = V.output_path(path, f".{fmt}")
            unit = V.choice(scale_unit, "scale_unit", ("mm", "cm", "in"), "mm")
            with self._job("export", f"Exporting {os.path.basename(p)}"):
                res = export_to_format(st.mesh, p, fmt, scale_unit=unit,
                                       align_origin=V.boolean(align_origin, True),
                                       corner_uv=st.uv,
                                       material=self.materials.as_trimesh_material(corner_uv=st.uv))
            if res.get("has_uv"):
                self.log(f"Wrote UV coordinates and {res['texture_channels']} texture map(s) into the {fmt.upper()}", "ok")
            self.log(f"Saved {res['file_size_mb']} MB, {res['face_count']:,} faces", "ok")
            for w in res.get("warnings", []):
                self.log(w, "warn")
            self.history.append({"operation": f"export_{fmt}", "path": p, "time": time.strftime("%H:%M:%S")})
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

    def default_fixed_export_name(self, ext: str) -> str:
        """Default mesh export name: original-GS-YYYYMMDD-HHMMSS-fixed.ext."""
        base = os.path.splitext(os.path.basename(self.file_path))[0] if self.file_path else "model"
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        return f"{base}-GS-{timestamp}-fixed{ext}"

    # ---------------------------------------------------------------- preview
    preview = staticmethod(build_mesh_preview)

    def _build_preview(self, st: _State) -> dict:
        return build_mesh_preview(
            st.mesh, st.uv,
            max_faces=self.preview_max_faces,
            shell_face_counts=[len(p.faces) for p in st.shells] if st.shells else None,
            detail=self.preview_detail,
        )

    def set_preview_detail(self, fraction=None) -> dict:
        """
        Choose how much of the model the viewport draws, as a fraction of its faces.

        None hands the choice back to Meshwright, which picks whatever keeps loading
        quick. Nothing about the model changes — this is the display only, and the
        result says exactly what is now on screen.
        """
        with self.lock:
            st = self._require()
            if fraction is None:
                self.preview_detail = None
            else:
                self.preview_detail = float(V.number(fraction, "detail", 0.01, 1.0, 1.0))

            with self._job("preview", "Rebuilding the viewport", faces=len(st.mesh.faces)):
                preview = self._build_preview(st)

            detail = preview["detail"]
            self.log(f"Viewport now drawing {detail['faces_shown']:,} of "
                     f"{detail['faces_total']:,} triangles "
                     f"({detail['fraction'] * 100:.0f}%)", "ok")
            out = {"success": True, "state_id": st.id, "preview": preview, "detail": detail}
            if "shell_face_counts" in preview:
                out["shell_face_counts"] = preview["shell_face_counts"]
            elif st.shells:
                out["shell_face_counts"] = [len(p.faces) for p in st.shells]
            return out

    # ------------------------------------------------------------------ jewellery
    def blender_status(self) -> dict:
        """Where Blender is, and whether it can actually be run."""
        from engine import settings
        from engine.blend_import import find_blender
        try:
            found = find_blender()
            return {"success": True, "found": True, "path": found,
                    "chosen": bool(settings.get("blender_path", ""))}
        except RuntimeError as why:
            return {"success": True, "found": False, "path": "", "reason": str(why),
                    "chosen": bool(settings.get("blender_path", ""))}

    def set_blender_path(self, path: str = "") -> dict:
        """
        Remember where Blender is. An empty path goes back to searching for it.

        Checked before it is stored: being told the wrong path and finding out at
        the end of a job is worse than being told now.
        """
        from engine import settings
        from engine.blend_import import blender_path
        wanted = str(path or "").strip().strip('"')
        if wanted and not blender_path(wanted):
            raise ServiceError("That is not a Blender executable. Pick blender.exe itself, "
                               "usually in Program Files\\Blender Foundation.")
        settings.put("blender_path", wanted or None)
        self.log(f"Blender: {wanted}" if wanted else "Blender: back to searching for it")
        return self.blender_status()

    def measure_ring(self) -> dict:
        """Everything a jeweller would check. Changes nothing."""
        from engine import jewellery
        with self.lock:
            st = self._require()
            try:
                found = jewellery.measure(st.mesh)
            except jewellery.NotARing as why:
                raise ServiceError(str(why))
            found["rules"] = jewellery.CASTING
            self.log(f"Ring: bore {found['bore_min_mm']}–{found['bore_max_mm']} mm, "
                     f"size ISO {found['iso_size']} (US {found['us_size']})",
                     "warn" if found["out_of_round"] or found["too_thin"] else "ok")
            return {"success": True, "state_id": st.id, "ring": found}

    def fix_ring(self, target_iso: float = 0.0, target_us: float = 0.0,
                 comfort: bool = True, bevel_mm: float = 0.15,
                 min_thickness_mm: float = 0.0, scale_percent: float = 100.0,
                 repair_first: bool = True) -> dict:
        """
        Put the ring right: repair the mesh, then hand it to Blender for the
        geometry a triangle mesh cannot fix well — a true, sized, comfort-fit bore
        and edges taken off without losing the engraving.
        """
        from engine import jewellery
        with self.lock:
            st = self._require()
            circumference = 0.0
            if target_us:
                circumference = jewellery.circumference_for_us(
                    V.number(target_us, "target_us", 0.5, 20.0, 7.0))
            elif target_iso:
                circumference = V.number(target_iso, "target_iso", 35.0, 90.0, 54.0)
            bevel = V.number(bevel_mm, "bevel_mm", 0.0, 2.0, 0.15)
            wanted_wall = V.number(min_thickness_mm, "min_thickness_mm", 0.0, 10.0, 0.0)
            scale = V.number(scale_percent, "scale_percent", 90.0, 115.0, 100.0)

            if repair_first:
                self.log("Repairing the mesh before the ring is corrected")
                repaired = self.repair(strict_watertight=True, force=True)
                if not repaired.get("success"):
                    return repaired
                st = self._require()

            try:
                before = jewellery.measure(st.mesh)
            except jewellery.NotARing as why:
                raise ServiceError(str(why))

            # Solidify grows the outside; the bore has just been made correct and must
            # not move. Only the shortfall is added, and only if there is one.
            thicken = 0.0
            if wanted_wall and before.get("thinnest_wall_mm") is not None:
                shortfall = wanted_wall - before["thinnest_wall_mm"]
                if shortfall > 0.01:
                    thicken = round(shortfall, 3)

            with self._job("fix_ring", "Correcting the ring in Blender",
                           faces=len(st.mesh.faces)):
                try:
                    fixed, report = jewellery.fix(
                        st.mesh, target_circumference_mm=circumference, comfort=bool(comfort),
                        bevel_mm=bevel, thicken_mm=thicken, scale_percent=scale, log=self.log)
                except RuntimeError as why:
                    raise ServiceError(str(why))

            after = jewellery.measure(fixed)
            if circumference and after["ovality_mm"] > jewellery.OVALITY_NOTICE_MM:
                # Cutting a bore can only take metal away, so a bore already wider
                # than the size asked for stays where it is. Say so; do not let a
                # ring that is still oval pass as corrected.
                self.log(
                    f"The bore is still {after['ovality_mm']:.2f} mm out of round: it was already "
                    f"wider than ISO {after['iso_size']:.0f} in places, and rounding those would "
                    f"mean adding metal. Size ISO {after['round_from_iso']:.0f} "
                    f"(US {after['round_from_us']:.1f}) or larger comes out truly round.", "warn")

            detail = {"iso_size": after["iso_size"], "us_size": after["us_size"],
                      "ovality_mm": after["ovality_mm"], "thickened_mm": thicken,
                      "scale_percent": scale}
            result = self._commit(fixed, "fix_ring", detail, guard=False)
            result["ring"] = after
            result["ring_before"] = before
            result["blender"] = report
            return result

    # ------------------------------------------------------------------ printability
    def printers(self) -> dict:
        """Every machine on offer, and which of them a slicer on this PC knows about."""
        from engine import printers as P
        return {"success": True, **P.catalogue()}

    def printability(self, printer_id: str = "", technology: str = "", pixel_um: float = 0.0,
                     nozzle_mm: float = 0.0, layer_mm: float = 0.0, thorough: bool = False) -> dict:
        """
        Measure the current model against one printer. Reports; changes nothing.

        Whether anything is done about what it finds is the person's decision, so
        this never touches the mesh and never creates a state.
        """
        from engine import printability as PA
        from engine import printers as P

        with self.lock:
            st = self._require()
            profile = P.profile(
                printer_id=str(printer_id or "")[:120],
                technology=V.choice(technology, "technology", ("", "resin", "fdm"), "") if technology else "",
                pixel_um=V.number(pixel_um, "pixel_um", 0.0, 500.0, 0.0),
                nozzle_mm=V.number(nozzle_mm, "nozzle_mm", 0.0, 5.0, 0.0),
                layer_mm=V.number(layer_mm, "layer_mm", 0.0, 2.0, 0.0))
            label = f"Checking against {profile['name']}"
            with self._job("printability", label, faces=len(st.mesh.faces)):
                try:
                    report = PA.check(st.mesh, profile, thorough=bool(thorough))
                except PA.NotMeasurable as why:
                    raise ServiceError(str(why))
            lost = report.get("missing_share")
            self.log(f"{profile['name']}: {report['verdict']}"
                     + (f" — {lost:.1%} of the model is finer than {profile['min_feature_mm']:.3f} mm"
                        if lost else ""),
                     "warn" if not report["printable"] else "ok")
            for doubt in report["doubts"]:
                self.log(f"Not confirmed: {doubt}", "warn")
            return {"success": True, "state_id": st.id, "report": report}

    def close(self):
        if self.store:
            self.store.close(delete=True)
