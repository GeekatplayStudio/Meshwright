"""
Self-test — run the parts of Meshwright that break when a program is packaged.

    Meshwright.exe --selftest                 writes %TEMP%\\meshwright-selftest.json
    Meshwright.exe --selftest-out report.json

It exists for two reasons. The installed program is a windowed exe with no console, so
there is nothing to run "pytest" against; and the failures that come from packaging are
quiet ones. A helper process that cannot start does not raise — retopology just uses
another engine and returns a perfectly good-looking mesh. So the steps below check which
engine actually did the work, not only that a result came back.

A step whose engine is not part of this build is reported as skipped rather than failed:
the "lite" edition leaves out the GPL-licensed engines on purpose.

Exit code 0 means every step that could run, ran.
"""
import contextlib
import importlib
import json
import os
import sys
import tempfile
import time
import traceback

from engine.runtime import FROZEN, resource_path
from engine.version import __version__

# Without these the program cannot work at all.
CORE = [
    ("numpy", "numpy"), ("scipy", "scipy"), ("trimesh", "trimesh"), ("PIL", "pillow"),
    ("webview", "pywebview"), ("xatlas", "xatlas"), ("rtree", "rtree"),
]
# Every one of these is detected at run time; a build may leave some out.
ENGINES = [
    ("pymeshlab", "pymeshlab"), ("pymeshfix", "pymeshfix"), ("manifold3d", "manifold3d"),
    ("fast_simplification", "fast-simplification"), ("pyQuadriFlow", "pyQuadriFlow"),
    ("cv2", "opencv-python"), ("ufbx", "ufbx"), ("lxml", "lxml"), ("shapely", "shapely"),
    ("embreex", "embreex"),
    ("mcp", "mcp"), ("skimage", "scikit-image"),
]


def _probe(module: str, dist: str):
    """(present, version) for one import — importing is the real test, metadata is a bonus."""
    try:
        importlib.import_module(module)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    version = ""
    with contextlib.suppress(Exception):
        from importlib.metadata import version as dist_version
        version = dist_version(dist)
    return True, version


class _Run:
    def __init__(self):
        self.steps = []
        self.log_lines = []

    def log(self, message, level="info"):
        self.log_lines.append(f"[{level}] {message}")

    def step(self, name, fn, needs=None, present=None):
        if needs and not present.get(needs):
            self.steps.append({"name": name, "status": "skipped", "seconds": 0.0,
                               "detail": f"{needs} is not part of this build"})
            return
        self.log_lines.clear()
        started = time.perf_counter()
        try:
            detail = fn() or ""
            status = "passed"
        except Exception as exc:
            status = "FAILED"
            detail = f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=4)}"
            detail += "\n".join(self.log_lines[-6:])
        self.steps.append({"name": name, "status": status,
                           "seconds": round(time.perf_counter() - started, 2), "detail": str(detail)})


def _sphere_file(directory):
    import trimesh
    path = os.path.join(directory, "sphere.stl")
    trimesh.creation.icosphere(subdivisions=5, radius=20).export(path)     # 20,480 faces
    return path


def run(out_path=None) -> int:
    from engine.service import MeshService
    from engine.texture import seam_fixer
    from engine.texture import uv_channel as UV

    result = {"version": __version__, "frozen": FROZEN, "python": sys.version.split()[0],
              "executable": sys.executable, "core": {}, "engines": {}, "steps": []}
    present = {}
    for module, dist in CORE:
        ok, info = _probe(module, dist)
        result["core"][module] = {"present": ok, "info": info}
        present[module] = ok
    for module, dist in ENGINES:
        ok, info = _probe(module, dist)
        result["engines"][module] = {"present": ok, "info": info}
        present[module] = ok

    run_ = _Run()
    work = tempfile.mkdtemp(prefix="meshwright-selftest-")
    svc = MeshService(log=run_.log, autosave=False)

    def demo():
        r = svc.load_demo()
        assert r["success"], r
        return f"{r['stats']['face_count']} faces"

    def repair():
        r = svc.repair()
        assert r["success"], r
        return r["analysis"]["verdict"]

    def sphere():
        r = svc.load(_sphere_file(work))
        assert r["success"] and r["stats"]["face_count"] > 20000, r
        return f"{r['stats']['face_count']:,} faces"

    def retopo(method, expect):
        def go():
            r = svc.retopo(1500, method=method)
            assert r["success"], r
            used = r["info"]["method_used"]
            assert expect in used, (
                f"asked for {method} but the result came from '{used}' - "
                f"the engine did not run and a fallback covered for it")
            n = r["stats"]["face_count"]
            svc.undo()
            return f"{used}, {n:,} faces"
        return go

    def unwrap():
        r = svc.unwrap_uvs()
        assert r.get("success"), r
        # the layout's own numbers ride along on the committed state's history entry
        stats = svc.history[-1] if svc.history else {}
        return f"{stats.get('islands', '?')} islands"

    def dilate():
        import numpy as np
        from PIL import Image
        assert seam_fixer.HAS_CV2, "OpenCV is not in this build"
        mask = np.zeros((64, 64), np.uint8)
        mask[20:40, 20:40] = 255
        out = seam_fixer.dilate_texture_gutters(Image.new("RGB", (64, 64), (200, 50, 50)),
                                                mask=mask, padding_px=4)
        assert out.size == (64, 64)

    def transfer():
        import numpy as np
        import trimesh
        assert UV.HAS_RTREE, "rtree is not in this build"
        m = trimesh.creation.icosphere(subdivisions=2)
        pts, faces = UV.closest_surface_point(m, np.array([[0.0, 0.0, 2.0]]))
        assert len(faces) == 1 and abs(np.linalg.norm(pts[0]) - 1.0) < 0.1

    def voxel_rebuild():
        # The last-resort repair stage ("Force watertight", on by default). It is the only
        # thing that can seal a mesh the other engines give up on, and in a build without
        # MeshLab and MeshFix it is the main one — yet nothing else here reaches it.
        import trimesh
        ball = trimesh.creation.icosphere(subdivisions=2, radius=5.0)
        rebuilt = ball.voxelized(pitch=1.0).fill().marching_cubes
        assert len(rebuilt.faces) > 100, "marching cubes produced nothing"
        return f"{len(rebuilt.faces)} faces"

    def voxel_repair():
        # End to end through the service. With MeshLab and MeshFix present they seal this,
        # and the report says so; without them (the "lite" edition) it can only be the voxel
        # stage, which is the case this exists to catch.
        import numpy as np
        import trimesh
        soup = trimesh.Trimesh(vertices=np.array([[0, 0, 0], [10, 0, 0], [0, 10, 0], [5, 5, 10]], float),
                               faces=[[0, 1, 2], [0, 1, 3]], process=False)
        path = os.path.join(work, "soup.stl")
        soup.export(path)
        svc.load(path)
        r = svc.repair(strict_watertight=True)
        assert r["success"] and r["stats"]["is_watertight"], (
            f"repair could not seal a two-triangle soup: {r['analysis']['verdict']}")
        used = [f["stage"] for f in r["report"]["fixes"]]
        return "stages: " + ", ".join(used)

    def manifold():
        import manifold3d as m3d
        assert m3d.Manifold.cube([1, 1, 1]).volume() > 0.99

    def meshfix():
        import numpy as np
        import pymeshfix
        import trimesh
        s = trimesh.creation.icosphere(subdivisions=2)
        fixer = pymeshfix.MeshFix(np.asarray(s.vertices, float), np.asarray(s.faces[:-3], np.int32))
        fixer.repair()
        assert len(fixer.faces) >= len(s.faces) - 3

    def quick_look():
        """
        The file browser's picture of a model must come from reading the file, not
        from loading it — so this checks the facts against a file whose contents are
        known, and insists a picture came back. A packaging failure here is silent
        otherwise: the browser would simply show every file as a plain icon.
        """
        from engine import quicklook
        path = os.path.join(work, "look.stl")
        svc.export_model(path, "stl")
        seen = quicklook.look(path)
        assert seen["faces"], f"no triangle count from {seen.get('format')}: {seen.get('note')}"
        assert seen["picture"], f"no picture drawn: {seen.get('note')}"
        assert seen["dimensions"], "no dimensions"
        return f"{seen['format']}, {seen['faces']:,} faces, {len(seen['picture']) // 1024} KB picture"

    def printability():
        """
        The printer check, against a shape whose answer is known: a 0.6 mm blade on
        a 30 mm cube must survive a resin screen and be lost to a 0.8 mm nozzle. It
        also proves the embedded printer table shipped, and that the exact
        wall-thickness rays really run — without Embree they silently do not.
        """
        import trimesh

        from engine import printability as PA
        from engine import printers

        listed = printers.catalogue()
        assert len(listed["machines"]) > 100, f"printer table missing: {len(listed['machines'])}"
        blade = trimesh.creation.box(extents=(10, 0.6, 18))
        blade.apply_translation([20, 0, 0])
        part = trimesh.boolean.union([trimesh.creation.box(extents=(30, 30, 30)), blade])

        fine = PA.check(part, printers.profile("elegoo|mars 4 ultra"), with_scale=False)
        coarse = PA.check(part, printers.profile(technology="fdm", nozzle_mm=0.8), with_scale=False)
        assert fine["printable"], f"a resin screen should make a 0.6 mm blade: {fine['verdict']}"
        assert not coarse["printable"], "a 0.8 mm nozzle cannot make a 0.6 mm blade"
        assert fine["wall"] is not None, "wall thickness was not measured — is Embree present?"
        assert abs(fine["wall"]["thinnest_mm"] - 0.6) < 0.1, fine["wall"]
        return (f"{len(listed['machines'])} printers; blade measured "
                f"{fine['wall']['thinnest_mm']:.2f} mm, lost at 0.8 mm nozzle")

    def export():
        sizes = []
        for ext in ("stl", "obj", "glb"):
            path = os.path.join(work, f"out.{ext}")
            r = svc.export_model(path, ext)
            assert r["success"] and os.path.getsize(path) > 100, (ext, r)
            sizes.append(f"{ext} {os.path.getsize(path) // 1024} KB")
        return ", ".join(sizes)

    def window_stack():
        if os.name != "nt":
            return "not Windows"
        from webview.platforms import winforms
        assert winforms.renderer != "mshtml", "WebView2 is missing - the window would be drawn by Internet Explorer"
        return f"renderer {winforms.renderer}"

    def files():
        missing = [p for p in ("ui/index.html", "ui/js/app.js", "ui/js/viewer.js", "ui/css/style.css",
                               "ui/vendor/three.min.js", "ui/assets/icon.ico",
                               "comfyui_nodes/Geekatplay-3D-MeshFix/__init__.py")
                   if not os.path.exists(resource_path(*p.split("/")))]
        assert not missing, f"missing from the program folder: {missing}"
        from scripts.install_comfyui_nodes import detect_comfyui_installations
        detect_comfyui_installations()

    def mcp():
        import asyncio

        import mcp_server
        try:
            tools = asyncio.run(mcp_server.mcp.list_tools())
        finally:
            with contextlib.suppress(Exception):
                mcp_server.service.close()
        assert len(tools) >= 10, f"only {len(tools)} tools"
        return f"{len(tools)} tools"

    steps = [
        ("core libraries import", lambda: None, None),
        ("program files present", files, None),
        ("window (WebView2)", window_stack, None),
        ("load the built-in demo", demo, None),
        ("repair", repair, None),
        ("load a 20k-face sphere", sphere, None),
        ("retopology: QuadriFlow helper process", retopo("quadriflow", "quadriflow"), "pyQuadriFlow"),
        ("retopology: uniform (MeshLab)", retopo("isotropic", "isotropic"), "pymeshlab"),
        ("retopology: decimate (MeshLab)", retopo("quadric", "quadric"), "pymeshlab"),
        ("UV unwrap: xatlas helper process", unwrap, "xatlas"),
        ("gutter fill (OpenCV)", dilate, "cv2"),
        ("exact UV transfer (rtree)", transfer, "rtree"),
        ("voxel rebuild: marching cubes (scikit-image)", voxel_rebuild, "skimage"),
        ("solid boolean (Manifold3D)", manifold, "manifold3d"),
        ("hole filling (MeshFix)", meshfix, "pymeshfix"),
        ("export STL / OBJ / GLB", export, None),
        ("file browser: read and draw a model without loading it", quick_look, "cv2"),
        ("printer check: measure a known blade against two machines", printability, "embreex"),
        ("seal a two-triangle soup, end to end", voxel_repair, "skimage"),
        ("MCP server tools", mcp, "mcp"),
    ]
    core_missing = [m for m, v in result["core"].items() if not v["present"]]
    for name, fn, needs in steps:
        if name == "core libraries import":
            run_.steps.append({"name": name, "status": "FAILED" if core_missing else "passed", "seconds": 0.0,
                               "detail": f"missing: {core_missing}" if core_missing else f"{len(CORE)} present"})
            continue
        run_.step(name, fn, needs=needs, present=present)

    result["steps"] = run_.steps
    result["ok"] = not any(s["status"] == "FAILED" for s in run_.steps)
    with contextlib.suppress(Exception):
        svc.close()

    target = out_path or os.path.join(tempfile.gettempdir(), "meshwright-selftest.json")
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2)
    for s in run_.steps:
        line = f"{s['status']:8s} {s['name']:44s} {s['seconds']:6.2f}s  {s['detail'].splitlines()[0] if s['detail'] else ''}"
        with contextlib.suppress(Exception):
            print(line, flush=True)
    with contextlib.suppress(Exception):
        print(f"\n{'ALL PASSED' if result['ok'] else 'FAILED'}  ({target})", flush=True)
    return 0 if result["ok"] else 1
