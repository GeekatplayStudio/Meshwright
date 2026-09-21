"""
Which printer this model is going to, and what that machine can actually resolve.

Two numbers decide whether a detail survives printing, and they are not the same
number on the two kinds of machine:

* **Resin (MSLA/LCD)** — the screen's pixel pitch, which is the build area divided
  by the LCD's resolution. An Elegoo Mars 4 Ultra is 153.4 mm across 8520 pixels,
  so one pixel is 18 µm. A feature narrower than a pixel simply is not masked, and
  does not cure; the accepted working floor is about two pixels, because a single
  lit pixel bleeds and cures unreliably.
* **Filament (FDM)** — the nozzle. The printer cannot lay a line narrower than the
  nozzle it is wearing, so a wall thinner than one nozzle width is not printed at
  all, and one below two is a single unsupported line.

The table is Meshwright's own, embedded so that it works on a PC with no slicer
installed, and it holds only what is needed to answer that question. Every figure
in it is a published specification — the same numbers on the box, in the manual and
in every slicer — and any of them can be overridden in the panel, because a nozzle
is a consumable and the owner knows what is fitted better than a table does.

If a slicer *is* installed, its machines are offered too, read from its own
configuration: those are the printers that person actually owns.
"""
import functools
import glob
import json
import os

from engine.runtime import resource_path

# A single lit resin pixel bleeds into its neighbours and cures unreliably, so two
# is the honest floor. A filament printer lays one line of one nozzle width.
RESIN_MIN_PIXELS = 2.0
FDM_MIN_NOZZLES = 1.0
# Below twice that, a feature exists but is a single unsupported line or column: it
# prints, and it snaps. Worth saying, separately from "will not appear at all".
FRAGILE_MULTIPLE = 2.0

CHITUBOX_MACHINES = r"C:\Program Files\CHITUBOX\machinecfg\*\*.cfgx"
# A slicer ships blank templates alongside its real machines. Offering "Default"
# as a printer is worse than not offering it: its numbers belong to nothing.
PLACEHOLDERS = {"default", "custom", "custom printer", "other", "new printer"}


@functools.lru_cache(maxsize=1)
def _embedded() -> list[dict]:
    try:
        with open(resource_path("engine", "data", "printers.json"), "r", encoding="utf-8") as f:
            return json.load(f)["machines"]
    except (OSError, ValueError, KeyError):
        return []


def _installed() -> list[dict]:
    """Machines from a slicer on this PC — the ones this person really owns."""
    found = []
    for path in glob.glob(CHITUBOX_MACHINES):
        try:
            with open(path, "r", encoding="utf-8") as f:
                info = json.load(f)["printerinfo"]

            def field(key):
                value = info.get(key)
                return value.get("currentvalue") if isinstance(value, dict) else value

            rx, ry = int(field("resolutionx") or 0), int(field("resolutiony") or 0)
            width, depth = float(field("machinewidth") or 0), float(field("machinedepth") or 0)
            name = (field("machinename") or "").strip()
            if not (rx and width and name):
                continue
            if name.strip().lower() in PLACEHOLDERS:
                continue                # a slicer's blank template, not a printer anyone owns
            found.append({
                "maker": (field("vendor") or "Installed").strip(), "model": name,
                "technology": "resin", "pixel_um": round(width / rx * 1000.0, 2),
                "resolution": [rx, ry],
                "build_mm": [round(width, 1), round(depth, 1), round(float(field("machineheight") or 0), 1)],
                "layer_mm": 0.05, "installed": True,
            })
        except Exception:
            continue                    # one unreadable definition is not a broken catalogue
    return found


def machine_id(machine: dict) -> str:
    """
    One name for one machine, however it was spelled.

    A slicer records "ELEGOO Mars 4 Ultra" under vendor "ELEGOO"; the table here
    calls the same printer "Mars 4 Ultra" by "Elegoo". Without folding the maker
    out of the model name the catalogue lists that printer twice.
    """
    maker = str(machine.get("maker", "")).strip()
    model = str(machine.get("model", "")).strip()
    if maker and model.upper().startswith(maker.upper()):
        model = model[len(maker):].strip() or model
    return f"{maker}|{model}".lower()


def catalogue() -> dict:
    """Every machine on offer, and which of them this PC's slicer knows about."""
    machines = {machine_id(m): dict(m, id=machine_id(m)) for m in _embedded()}
    for m in _installed():
        entry = dict(m, id=machine_id(m))
        machines.setdefault(entry["id"], entry)["installed"] = True
    listed = sorted(machines.values(), key=lambda m: (m["technology"], m["maker"].lower(), m["model"].lower()))
    return {"machines": listed,
            "makers": sorted({m["maker"] for m in listed}, key=str.lower),
            "installed": sorted(m["id"] for m in listed if m.get("installed"))}


def find(printer_id: str) -> dict | None:
    return next((m for m in catalogue()["machines"] if m["id"] == (printer_id or "").lower()), None)


def profile(printer_id: str = "", technology: str = "", pixel_um: float = 0.0,
            nozzle_mm: float = 0.0, layer_mm: float = 0.0, build_mm=None) -> dict:
    """
    Turn a chosen machine, plus anything the owner has overridden, into the two
    numbers the printability check actually needs.

    Everything can be given directly, so a machine that is not in the table is not
    a dead end: the panel's own boxes are what this reads.
    """
    machine = find(printer_id) or {}
    technology = (technology or machine.get("technology") or "fdm").lower()
    layer = float(layer_mm or machine.get("layer_mm") or (0.05 if technology == "resin" else 0.2))
    build = list(build_mm or machine.get("build_mm") or [0, 0, 0])

    if technology == "resin":
        pitch = float(pixel_um or machine.get("pixel_um") or 35.0) / 1000.0
        smallest = pitch * RESIN_MIN_PIXELS
        basis = f"{pitch * 1000:.0f} µm pixel × {RESIN_MIN_PIXELS:g}"
    else:
        nozzle = float(nozzle_mm or machine.get("nozzle_mm") or 0.4)
        pitch = nozzle
        smallest = nozzle * FDM_MIN_NOZZLES
        basis = f"{nozzle:g} mm nozzle"

    name = f"{machine.get('maker', '')} {machine.get('model', '')}".strip() or (
        "Resin printer" if technology == "resin" else "Filament printer")
    return {
        "id": machine.get("id", ""), "name": name, "technology": technology,
        "unit_mm": round(pitch, 4),
        "min_feature_mm": round(smallest, 4),
        "fragile_below_mm": round(smallest * FRAGILE_MULTIPLE, 4),
        "layer_mm": round(layer, 4),
        "build_mm": [round(float(v), 1) for v in build],
        "basis": basis,
    }
