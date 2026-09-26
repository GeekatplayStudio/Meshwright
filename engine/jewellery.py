"""
Making a generated ring into one somebody could wear and cast.

An image-to-3D service produces a ring in under a minute, and the result looks right
in a render and is almost never right on a finger: the bore is an oval, its size is
whatever the generator felt like, the inside edge is cut square, and the band is thin
in places where metal will not fill. Generic repair — including the auto-repair those
services now ship — closes holes and fixes normals. None of it knows what a ring is,
and will hand back a watertight, manifold, perfectly unwearable oval.

The work splits in two, and the split is deliberate.

**Measuring happens here**, because it must be exact and must never guess. The axis of
the finger is the principal axis carrying the largest moment of inertia — a ring's
mass all sits at one radius from it — which holds whatever angle the file was saved
at. The bore is then read by firing rays outward from that axis: reading vertex
positions instead aliases badly, and a bore drawn with 180 segments reports itself
4 mm out of round when it is nothing of the kind.

**Correcting happens in Blender**, because two of the operations have no good answer
in a triangle mesh. Rounding a sharp edge by voxel filtering blurs away the 0.3 mm
engraving that casting guidelines ask for, and going fine enough to avoid that would
take 1.4 billion voxels for one ring. Blender's angle-limited bevel rounds the hard
corners and leaves flat faces and shallow detail alone — measured, on a ring with
0.3 mm grooves: every groove still 0.300 mm deep afterwards.

The model is turned into a canonical frame before it is sent — finger axis on +Z,
centred on the origin — so the script on the other side works in known coordinates
and can build its own cutters, and the result is turned back on arrival. Nothing
about the ring's real orientation has to cross the boundary.
"""
import json
import math
import os
import tempfile
from pathlib import Path

import numpy as np
import trimesh

# --------------------------------------------------------------------------- what a jeweller knows
#
# Every figure here is published, and the source is named. A number invented in this
# file would be believed by everything downstream and would cost somebody a flask of
# metal, so none of them are invented. See docs/PRD-RINGS.md for the full table.

# ISO 8653:2016 — a ring's size *is* its inner circumference in millimetres.
# US sizes step 0.81 mm of diameter, which is 2.55 mm of circumference, per size,
# with US 7 at 54.4 mm.
US_AT_54_4 = 7.0
US_STEP_MM = 2.55

CASTING = {
    # Materialise / i.materialise lost-wax design guidelines.
    "band_min_mm": 1.0,          # a ring band, gold or silver
    "wall_min_mm": 0.8,          # any other wall in gold
    "absolute_min_mm": 0.35,     # the floor for lost-wax in any metal
    "detail_min_mm": 0.4,        # a sunken feature has to be this big to show
    "engrave_min_mm": 0.3,       # depth on a surface that rubs
}

# A comfort-fit bore is domed toward the finger: narrowest in the middle, flaring at
# the rims so the band passes a knuckle. Built the other way round it grips at the
# edges, which is the thing comfort fit exists to prevent.
COMFORT_FLARE_MM = 0.25

# Round a ring only when its bore is out by more than this; below it, the difference
# is the mesh's own faceting rather than a real oval.
OVALITY_NOTICE_MM = 0.08


class NotARing(Exception):
    """This model cannot be measured as a ring — carries the sentence shown."""


def iso_size(circumference_mm: float) -> float:
    return round(float(circumference_mm), 1)


def us_size(circumference_mm: float) -> float:
    return round(US_AT_54_4 + (circumference_mm - 54.4) / US_STEP_MM, 2)


def circumference_for_us(size: float) -> float:
    return 54.4 + (float(size) - US_AT_54_4) * US_STEP_MM


# --------------------------------------------------------------------------- measuring
def finger_axis(mesh: trimesh.Trimesh) -> np.ndarray:
    """
    Which way the finger goes through.

    A ring's mass all sits at one radius from that axis, so it is the principal axis
    with the largest moment of inertia — true whatever angle the model was saved at.
    """
    try:
        components = np.asarray(mesh.principal_inertia_components, dtype=float)
        vectors = np.asarray(mesh.principal_inertia_vectors, dtype=float)
    except Exception as trouble:
        raise NotARing("This model's shape could not be measured.") from trouble
    if components.shape != (3,) or not np.isfinite(components).all():
        raise NotARing("This model's shape could not be measured.")
    axis = vectors[int(np.argmax(components))]
    return axis / np.linalg.norm(axis)


def frame_of(axis: np.ndarray):
    """A right-handed frame with `axis` as its z."""
    z = np.asarray(axis, float)
    z = z / np.linalg.norm(z)
    x = np.cross(z, [0.0, 0.0, 1.0])
    if np.linalg.norm(x) < 1e-6:
        x = np.cross(z, [0.0, 1.0, 0.0])
    x = x / np.linalg.norm(x)
    return x, np.cross(z, x), z


def canonical_transform(mesh: trimesh.Trimesh) -> np.ndarray:
    """
    The transform that stands a ring up: finger axis on +Z, centred on the origin.

    Everything sent to Blender goes through this, so the script on the other side
    builds its cutters in coordinates it can rely on, and the answer is turned back
    on arrival. The ring's real orientation never has to cross the boundary.
    """
    x, y, z = frame_of(finger_axis(mesh))
    centre = np.asarray(mesh.bounds, float).mean(axis=0)
    rotate = np.eye(4)
    rotate[:3, :3] = np.stack([x, y, z])        # rows: world -> canonical
    rotate[:3, 3] = -rotate[:3, :3] @ centre
    return rotate


def bore_profile(mesh: trimesh.Trimesh, samples: int = 720, heights: int = 9):
    """
    The inner radius at every angle, at several heights up the band.

    Rays fired outward from the axis, first hit. Reading vertices instead aliases:
    a bore drawn with 180 segments leaves most angular bins empty, and an empty bin
    quietly reports the *outer* wall — which reads as a bore 4 mm out of round that
    is nothing of the kind.
    """
    standing = mesh.copy()
    standing.apply_transform(canonical_transform(mesh))
    half = float(np.percentile(np.abs(standing.vertices[:, 2]), 98)) * 0.8
    if half <= 0:
        raise NotARing("This model is flat — there is no band to measure.")

    # Is there a hole at all? Rays fired from the middle of a *solid* still hit its
    # far side, so without this a pendant or a bead measures as a ring and is handed
    # back a confident, invented finger size — which is the worst thing this could do.
    try:
        if standing.is_watertight and bool(standing.contains(np.zeros((1, 3)))[0]):
            raise NotARing(
                "This model is solid through the middle, so it is not a ring. The jewellery "
                "check only understands rings — a pendant or a bead has no bore to measure.")
    except NotARing:
        raise
    except Exception:
        pass                    # an open mesh cannot answer; the ray count below still can

    theta = np.linspace(0.0, 2.0 * np.pi, samples, endpoint=False)
    directions = np.column_stack([np.cos(theta), np.sin(theta), np.zeros(samples)])
    rows = []
    for height in np.linspace(-half, half, heights):
        origin = np.array([0.0, 0.0, height])
        hits, index, _ = standing.ray.intersects_location(
            np.repeat(origin[None, :], samples, axis=0), directions, multiple_hits=False)
        if len(index) < samples * 0.5:
            continue                        # this height is not inside the bore
        radius = np.linalg.norm(hits - origin, axis=1)
        rows.append((float(height), radius))
    if not rows:
        raise NotARing(
            "No ring-shaped hole was found in this model. The jewellery check only "
            "understands rings — a pendant or a solid shape has nothing to measure.")
    return standing, rows


def measure(mesh: trimesh.Trimesh) -> dict:
    """Everything a jeweller would check, before anything is changed."""
    standing, rows = bore_profile(mesh)
    per_height = [(h, float(r.min()) * 2.0, float(r.max()) * 2.0) for h, r in rows]
    narrowest = min(lo for _, lo, _ in per_height)
    widest_at_narrow = max(hi for _, _, hi in per_height)
    mid = per_height[len(per_height) // 2]

    # A ring's size is set by its narrowest inner point, not its average: that is
    # where the finger actually meets it.
    circumference = math.pi * narrowest
    ovality = mid[2] - mid[1]

    result = {
        "is_ring": True,
        "bore_min_mm": round(narrowest, 3),
        "bore_max_mm": round(widest_at_narrow, 3),
        "ovality_mm": round(ovality, 3),
        "out_of_round": bool(ovality > OVALITY_NOTICE_MM),
        "circumference_mm": round(circumference, 2),
        "iso_size": iso_size(circumference),
        "us_size": us_size(circumference),
        "band_width_mm": round(float(np.ptp(standing.vertices[:, 2])), 2),
        "outer_diameter_mm": round(float(max(np.ptp(standing.vertices[:, 0]),
                                             np.ptp(standing.vertices[:, 1]))), 2),
        "profile": [{"height_mm": round(h, 2), "min_mm": round(lo, 3), "max_mm": round(hi, 3)}
                    for h, lo, hi in per_height],
    }
    # Cutting a true bore can only take metal away. Where the generator's bore is
    # already wider than the size asked for, making it round would mean *adding*
    # material, which this cannot do — so say plainly the smallest size at which the
    # bore does come out truly round, rather than quietly leaving it oval.
    result["round_from_iso"] = iso_size(math.pi * widest_at_narrow)
    result["round_from_us"] = us_size(math.pi * widest_at_narrow)
    result["thinnest_wall_mm"] = _wall_thickness(mesh)
    result["too_thin"] = bool(result["thinnest_wall_mm"] is not None
                              and result["thinnest_wall_mm"] < CASTING["band_min_mm"])
    return result


def _wall_thickness(mesh: trimesh.Trimesh):
    """
    Thinnest wall, by firing a ray into the surface at every face.

    Withheld rather than guessed at when the mesh cannot support the question: a ray
    needs a surface that reliably faces outwards, and on a model with inconsistent
    winding it fires the wrong way and returns confident nonsense.
    """
    try:
        if not mesh.is_winding_consistent or not mesh.is_watertight:
            return None
        faces = np.asarray(mesh.faces, np.int64)
        points = np.asarray(mesh.vertices, float)[faces].mean(axis=1)
        normals = np.asarray(mesh.face_normals, float)
        step = max(1e-4, float(mesh.scale) * 1e-5)
        hits, index, _ = mesh.ray.intersects_location(points - normals * step, -normals,
                                                      multiple_hits=False)
        if not len(index):
            return None
        distance = np.linalg.norm(hits - points[index], axis=1)
        return round(float(np.percentile(distance, 0.5)), 3)
    except Exception:
        return None


# --------------------------------------------------------------------------- the Blender side
#
# Written out beside the model each time rather than shipped as a file, so a frozen
# build needs nothing extra on disk, and reports through JSON rather than stdout —
# Blender prints a great deal of its own and picking our line out of it is guesswork.

BLENDER_SCRIPT = '''
import bpy, bmesh, json, math, sys
from mathutils import Vector

source, destination, options_path, report_path = sys.argv[sys.argv.index("--") + 1:]
with open(options_path, "r", encoding="utf-8") as handle:
    opt = json.load(handle)
report = {"steps": []}


def note(text):
    report["steps"].append(text)


def import_mesh(path):
    # The STL operators were renamed in Blender 4.2; support both so this works on
    # whatever the person already has installed.
    if hasattr(bpy.ops.wm, "stl_import"):
        bpy.ops.wm.stl_import(filepath=path)
    else:
        bpy.ops.import_mesh.stl(filepath=path)


def export_mesh(path):
    if hasattr(bpy.ops.wm, "stl_export"):
        bpy.ops.wm.stl_export(filepath=path)
    else:
        bpy.ops.export_mesh.stl(filepath=path)


bpy.ops.wm.read_factory_settings(use_empty=True)
import_mesh(source)
ring = bpy.context.scene.objects[0]
bpy.context.view_layer.objects.active = ring
ring.select_set(True)
report["faces_in"] = len(ring.data.polygons)

# The model arrives standing on +Z and centred, so every cutter below is built in
# coordinates this script can rely on.
height = max(ring.dimensions.z * 2.0, 4.0)


def apply(modifier):
    bpy.ops.object.modifier_apply(modifier=modifier.name)


# ---- 1. a true, correctly sized, comfort-fit bore -------------------------------
if opt.get("bore_radius_mm"):
    radius = float(opt["bore_radius_mm"])
    flare = float(opt.get("comfort_flare_mm") or 0.0)
    steps = 24 if flare > 0 else 2

    # A solid of revolution, narrowest in the middle and flaring at the rims. Built
    # as a closed profile: revolved from an open one it is a surface, and nothing
    # can be subtracted with a surface.
    mesh = bpy.data.meshes.new("bore")
    bm = bmesh.new()
    ring_verts = []
    for i in range(steps + 1):
        t = -1.0 + 2.0 * i / steps
        r = radius + flare * t * t
        z = t * height / 2.0
        ring_verts.append([bm.verts.new((r * math.cos(2 * math.pi * k / opt["sections"]),
                                         r * math.sin(2 * math.pi * k / opt["sections"]), z))
                           for k in range(opt["sections"])])
    for a, b in zip(ring_verts, ring_verts[1:]):
        for k in range(opt["sections"]):
            j = (k + 1) % opt["sections"]
            bm.faces.new((a[k], a[j], b[j], b[k]))
    for cap, flip in ((ring_verts[0], True), (ring_verts[-1], False)):
        face = bm.faces.new(cap[::-1] if flip else cap)
        face.normal_update()
    bm.normal_update()
    bm.to_mesh(mesh)
    bm.free()
    cutter = bpy.data.objects.new("bore", mesh)
    bpy.context.collection.objects.link(cutter)

    boolean = ring.modifiers.new(name="bore", type="BOOLEAN")
    boolean.operation = "DIFFERENCE"
    boolean.object = cutter
    boolean.solver = "EXACT"
    bpy.context.view_layer.objects.active = ring
    apply(boolean)
    bpy.data.objects.remove(cutter, do_unlink=True)
    note("bore cut to %.3f mm radius%s" % (radius, ", comfort flare %.2f mm" % flare if flare else ""))

# ---- 2. take the sharpness off ---------------------------------------------------
if opt.get("bevel_mm"):
    bevel = ring.modifiers.new(name="bevel", type="BEVEL")
    bevel.width = float(opt["bevel_mm"])
    bevel.segments = int(opt.get("bevel_segments") or 4)
    bevel.limit_method = "ANGLE"
    # Limiting by angle is the whole point: it rounds the hard corners and leaves
    # flat faces and shallow engraving alone, which voxel filtering cannot do.
    bevel.angle_limit = math.radians(float(opt.get("bevel_angle_deg") or 30.0))
    bevel.miter_outer = "MITER_ARC"
    bevel.use_clamp_overlap = True
    # Read the settings before applying: applying removes the modifier, and asking
    # it afterwards reports zeros — a message that says nothing happened when it did.
    said = "edges rounded by %.2f mm above %.0f degrees" % (
        bevel.width, math.degrees(bevel.angle_limit))
    apply(bevel)
    note(said)

# ---- 3. grow a band that is too thin to cast ------------------------------------
if opt.get("thicken_mm"):
    solidify = ring.modifiers.new(name="thicken", type="SOLIDIFY")
    solidify.thickness = float(opt["thicken_mm"])
    solidify.offset = 1.0             # outward only: the bore has just been made correct
    solidify.use_rim = True
    apply(solidify)
    note("grown outward by %.2f mm to reach the casting minimum" % solidify.thickness)

# ---- 4. scale for what the wax and the metal will lose ---------------------------
if opt.get("scale_percent") and abs(float(opt["scale_percent"]) - 100.0) > 1e-6:
    factor = float(opt["scale_percent"]) / 100.0
    ring.scale = (factor, factor, factor)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    note("scaled to %.2f%% to allow for shrinkage" % float(opt["scale_percent"]))

report["faces_out"] = len(ring.data.polygons)
export_mesh(destination)
with open(report_path, "w", encoding="utf-8") as handle:
    json.dump(report, handle)
'''


def fix(mesh: trimesh.Trimesh, *, target_circumference_mm: float = 0.0,
        comfort: bool = True, bevel_mm: float = 0.0, bevel_angle_deg: float = 30.0,
        thicken_mm: float = 0.0, scale_percent: float = 100.0,
        sections: int = 256, log=None) -> tuple:
    """
    Hand the ring to Blender, standing up, and take back what it returns.

    Returns (mesh, report). Raises RuntimeError with something actionable in it when
    Blender is missing or refuses the job.
    """
    from engine.blend_import import _run

    log = log or (lambda *a, **k: None)
    to_canonical = canonical_transform(mesh)
    standing = mesh.copy()
    standing.apply_transform(to_canonical)

    options = {
        "bore_radius_mm": (target_circumference_mm / (2.0 * math.pi)) if target_circumference_mm else 0.0,
        "comfort_flare_mm": COMFORT_FLARE_MM if comfort else 0.0,
        "bevel_mm": float(bevel_mm or 0.0),
        "bevel_angle_deg": float(bevel_angle_deg),
        "bevel_segments": 4,
        "thicken_mm": float(thicken_mm or 0.0),
        "scale_percent": float(scale_percent or 100.0),
        "sections": int(sections),
    }

    with tempfile.TemporaryDirectory(prefix="meshwright-ring-") as folder:
        source = Path(folder) / "ring.stl"
        destination = Path(folder) / "fixed.stl"
        settings_path = Path(folder) / "options.json"
        report_path = Path(folder) / "report.json"
        standing.export(str(source))
        with open(settings_path, "w", encoding="utf-8") as handle:
            json.dump(options, handle)

        log("Handing the ring to Blender")
        _run(BLENDER_SCRIPT,
             [str(source), str(destination), str(settings_path), str(report_path)],
             folder, "correct this ring")
        if not destination.is_file():
            raise RuntimeError("Blender returned no geometry for this ring.")
        fixed = trimesh.load(str(destination), force="mesh", process=True)
        report = {}
        if report_path.is_file():
            try:
                with open(report_path, "r", encoding="utf-8") as handle:
                    report = json.load(handle)
            except (OSError, ValueError):
                report = {}

    if not len(fixed.faces):
        raise RuntimeError("Blender returned an empty ring.")
    # Back where it came from, so undo, export and every measurement already taken
    # still line up with it.
    fixed.apply_transform(np.linalg.inv(to_canonical))
    for step in report.get("steps", []):
        log(step, "ok")
    return fixed, report
