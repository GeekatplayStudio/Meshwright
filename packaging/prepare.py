"""
Build helpers, run by packaging\\build.ps1 with the project's .venv python.

    prepare.py version-info               writes packaging\\version_info.txt (before the freeze)
    prepare.py notices <edition>          writes THIRD_PARTY_NOTICES.txt into dist\\Meshwright (after it)
    prepare.py check <report.json> <edition>   verifies a --selftest report against the edition

The notices file is derived from what PyInstaller actually bundled, not from a
hand-kept list: MIT and BSD licences require their text to accompany a *binary*
redistribution, and a list that drifts out of date is worse than none.
"""
import ast
import importlib.metadata as md
import json
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine.version import __version__

HERE = os.path.join(ROOT, "packaging")
DIST = os.path.join(ROOT, "dist", "Meshwright")
WORK = os.path.join(ROOT, "build", "work", "meshwright")

GPL_ENGINES = {"pymeshlab", "pymeshfix"}
# The engines a full build must contain, and the ones a lite build must not.
REQUIRED_EVERYWHERE = {"skimage"}
FULL_ENGINES = {"pymeshlab", "pymeshfix", "manifold3d", "fast_simplification", "pyQuadriFlow",
                "cv2", "ufbx", "lxml", "shapely", "mcp"}


# ------------------------------------------------------------------ version resource
def version_info():
    parts = [int(x) for x in re.findall(r"\d+", __version__)][:4]
    parts += [0] * (4 - len(parts))
    tup = ", ".join(str(x) for x in parts)
    dotted = ".".join(str(x) for x in parts)
    text = f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(filevers=({tup}), prodvers=({tup}), mask=0x3f, flags=0x0,
                    OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Geekatplay Studio'),
      StringStruct('FileDescription', 'Meshwright - mesh analysis and repair for 3D printing'),
      StringStruct('FileVersion', '{dotted}'),
      StringStruct('InternalName', 'Meshwright'),
      StringStruct('LegalCopyright', 'Copyright (c) Geekatplay Studio - Vladimir Chopine'),
      StringStruct('OriginalFilename', 'Meshwright.exe'),
      StringStruct('ProductName', 'Meshwright'),
      StringStruct('ProductVersion', '{__version__}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ])
"""
    path = os.path.join(HERE, "version_info.txt")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    print(f"wrote {path} ({__version__})")


# ------------------------------------------------------------------ notices
def _bundled_top_level():
    """Top-level module names PyInstaller packed, from its own table of contents."""
    names = set()
    toc = os.path.join(WORK, "PYZ-00.toc")
    if os.path.exists(toc):
        with open(toc, encoding="utf-8") as handle:
            # The file is the repr of (pyz path, [(module name, source path, type), ...]).
            _pyz, modules = ast.literal_eval(handle.read())
        names.update(entry[0].split(".")[0] for entry in modules)
    internal = os.path.join(DIST, "_internal")
    if os.path.isdir(internal):
        for item in os.listdir(internal):
            base = item.split(".")[0]
            if base and not base.endswith(("-info", "libs")) and not item.lower().endswith(".dll"):
                names.add(base)
    return names


def _license_text(dist):
    out = []
    for f in dist.files or []:
        low = f.name.lower()
        if any(k in low for k in ("license", "licence", "copying", "notice")) and ".dist-info" in str(f).replace("\\", "/"):
            try:
                out.append(f"--- {f.name} ---\n{dist.locate_file(f).read_text(encoding='utf-8', errors='replace').strip()}")
            except OSError:
                pass
    return "\n\n".join(out)


def _license_name(meta):
    for key in ("License-Expression", "License"):
        value = (meta.get(key) or "").strip()
        if value and len(value) < 120 and "\n" not in value:
            return value
    classifiers = [c.split("::")[-1].strip() for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
    return ", ".join(classifiers) or "see the licence text below"


def notices(edition):
    top = _bundled_top_level()
    mapping = md.packages_distributions()
    chosen = {}
    for module in sorted(top):
        for dist_name in mapping.get(module, []):
            chosen[dist_name] = md.distribution(dist_name)
    if edition == "lite":
        chosen = {n: d for n, d in chosen.items() if n.lower() not in GPL_ENGINES}
    skip = {"pyinstaller", "pyinstaller-hooks-contrib", "altgraph", "pefile", "pywin32-ctypes", "pip", "setuptools",
            "pytest", "ruff"}
    entries = sorted((n for n in chosen if n.lower() not in skip), key=str.lower)

    gpl = [n for n in entries if n.lower() in GPL_ENGINES]
    lines = [
        "MESHWRIGHT - THIRD-PARTY NOTICES",
        f"Meshwright {__version__}, {edition} edition. Copyright (c) Geekatplay Studio - Vladimir Chopine.",
        "Meshwright itself is released under the MIT licence (see LICENSE).",
        "",
        "This program bundles the open-source components listed below. Each is used under its own",
        "licence, reproduced in full after the list.",
        "",
    ]
    if gpl:
        lines += [
            "GNU GENERAL PUBLIC LICENCE COMPONENTS",
            "This edition includes " + " and ".join(gpl) + ", which are licensed under the GNU GPL v3.",
            "Their source code is available from:",
            "  PyMeshLab  https://github.com/cnr-isti-vclab/PyMeshLab",
            "  pymeshfix  https://github.com/pyvista/pymeshfix",
            "Meshwright's own source code is at https://github.com/GeekatplayStudio/Meshwright",
            "A build that leaves these components out can be made from the same source (see packaging/README.md).",
            "",
        ]
    lines += ["COMPONENTS", "=" * 10]
    for name in entries:
        meta = chosen[name].metadata
        lines.append(f"{meta['Name']} {meta['Version']} - {_license_name(meta)}"
                     + (f"  [{meta['Home-page']}]" if meta.get("Home-page") else ""))
    lines += ["", "LICENCE TEXTS", "=" * 13]
    for name in entries:
        text = _license_text(chosen[name])
        lines += ["", f"##### {chosen[name].metadata['Name']} {chosen[name].metadata['Version']}",
                  text or "(no licence file is distributed with this package; see its home page)"]

    os.makedirs(DIST, exist_ok=True)
    path = os.path.join(DIST, "THIRD_PARTY_NOTICES.txt")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    print(f"wrote {path}: {len(entries)} components, {os.path.getsize(path) // 1024} KB")
    # PyInstaller tucks data files under _internal\; the licence belongs beside the exe.
    shutil.copyfile(os.path.join(ROOT, "LICENSE"), os.path.join(DIST, "LICENSE"))


# ------------------------------------------------------------------ verification
def check(report_path, edition):
    with open(report_path, encoding="utf-8") as handle:
        report = json.load(handle)
    problems = []
    if not report.get("ok"):
        problems += [f"step failed: {s['name']}" for s in report["steps"] if s["status"] == "FAILED"] or ["report not ok"]
    if not report.get("frozen"):
        problems.append("the report did not come from a frozen program")
    engines = {name: info["present"] for name, info in report["engines"].items()}
    # In every edition: the voxel rebuild is the last-resort repair, and in "lite" the main one.
    problems += [f"required in every edition but missing: {n}" for n in sorted(REQUIRED_EVERYWHERE) if not engines.get(n)]
    if edition == "full":
        problems += [f"engine missing from the full edition: {n}" for n in sorted(FULL_ENGINES) if not engines.get(n)]
    else:
        problems += [f"GPL engine present in the lite edition: {n}" for n in sorted(GPL_ENGINES) if engines.get(n)]
    if problems:
        print("SELF-TEST CHECK FAILED")
        for item in problems:
            print("  -", item)
        return 1
    passed = sum(1 for s in report["steps"] if s["status"] == "passed")
    skipped = sum(1 for s in report["steps"] if s["status"] == "skipped")
    print(f"self-test check ok: {passed} passed, {skipped} skipped ({edition} edition, version {report['version']})")
    return 0


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "version-info":
        version_info()
    elif command == "notices":
        notices(sys.argv[2] if len(sys.argv) > 2 else "full")
    elif command == "check":
        sys.exit(check(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "full"))
    else:
        print(__doc__)
        sys.exit(2)
