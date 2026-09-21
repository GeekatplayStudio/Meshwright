"""
Will this model actually print on that machine, and what will be lost if it does?

The rule this module is built to is *never be confidently wrong*. A number here
sends someone to a six-hour print, and "your model is fine" when it is not costs
them the print, the resin and the evening. So everything it says is measured two
independent ways, and anything the two disagree about is reported as unconfirmed
rather than as fact.

**The measurement that decides.** Each layer is sliced and drawn at the printer's
real pixel pitch — the same picture the machine is given — and a distance transform
gives the widest circle that fits inside each island of solid. That is exactly the
question the hardware asks: an island narrower than one pixel is never masked, and
never cures. It needs no surface normals, no watertight mesh and no assumptions,
which is why it is the one that decides.

**The measurement that confirms.** A ray is fired into the surface at every vertex
and the distance to the far side is the wall thickness there. It is exact — on a
sphere of known thickness 10.000 mm it returns 10.000 — but only while the surface
it starts from faces the right way. On a model with inconsistent winding it fires
outward instead of inward and returns the gap to the neighbouring surface, which on
one real 694,000-face model read as a 0.24 mm wall throughout a solid figure. So it
is never allowed to decide anything on its own, and on a mesh whose winding or
watertightness is in doubt its findings are labelled unconfirmed.

What that costs: the two agree, and the answer is stated plainly. They disagree, and
the answer says which parts could not be confirmed and why. Either way nothing is
invented, and the check refuses outright rather than guess when the model cannot be
sliced at all.
"""
import numpy as np
import trimesh

from engine.estimation import describe_duration

MAX_LOCATION_POINTS = 400        # what the viewport highlights, as the diagnostics use
QUICK_LAYERS = 24                # a first answer in a couple of seconds
THOROUGH_LAYERS = 400            # every layer that matters, for the real answer
MAX_LAYER_PIXELS = 40_000_000    # a single layer larger than this is not rastered
MIN_USABLE_LAYERS = 0.6          # below this share of layers readable, refuse to answer


class NotMeasurable(Exception):
    """The model cannot be measured — carries the sentence shown to the user."""


# --------------------------------------------------------------------------- the deciding measurement
def _raster(section_2d, pixel, margin=4):
    """One cross-section as the printer would be given it: solid pixels, holes empty."""
    import cv2

    (minx, miny), (maxx, maxy) = section_2d.bounds
    width = int((maxx - minx) / pixel) + margin * 2
    height = int((maxy - miny) / pixel) + margin * 2
    if width < 3 or height < 3:
        return None, None
    if width * height > MAX_LAYER_PIXELS:
        raise NotMeasurable("This model is too large to check at that printer's resolution. "
                            "Try a coarser setting, or check a smaller model.")
    image = np.zeros((height, width), np.uint8)
    # Corners are placed to a sixteenth of a pixel. Rounding them to whole pixels
    # instead snaps a thin blade flat against the face it stands on, and the feature
    # is gone before anything has been measured.
    shift = 4
    unit = 1 << shift

    def draw(coords, colour):
        points = (np.asarray(coords) - [minx, miny]) / pixel + margin
        cv2.fillPoly(image, [np.round(points * unit).astype(np.int32)], colour, shift=shift)

    for polygon in section_2d.polygons_full:
        draw(polygon.exterior.coords, 255)
        for hole in polygon.interiors:
            draw(hole.coords, 0)
    return image, (minx - margin * pixel, miny - margin * pixel)


def _scan_layers(mesh, profile, layer_count):
    """
    Slice the model and measure the width of every island of solid in every layer.

    Returns the islands that are narrower than the printer's finest feature, where
    they are in 3D, and how much of the model they account for.
    """
    import cv2

    smallest = profile["min_feature_mm"]
    fragile = profile["fragile_below_mm"]
    # Draw finer than the tool, or the tool is one pixel across and the shapes it
    # cannot make are too small to have been drawn in the first place. A filament
    # printer's finest feature *is* its nozzle, so at nozzle-sized pixels a wall
    # thinner than the nozzle rounds away before anything has been measured.
    ideal = smallest / 3.0
    footprint = float(mesh.extents[0]) * float(mesh.extents[1])
    # A whole build plate drawn at a third of a resin pixel is a third of a billion
    # pixels per layer. Where that will not fit, the check is made coarser and says
    # so — and if it would have to be so coarse that the tool is a single pixel
    # again, it refuses, because at that point it could not see what it is looking for.
    affordable = np.sqrt(footprint / MAX_LAYER_PIXELS) if footprint > 0 else ideal
    pixel = max(ideal, float(affordable))
    if pixel > smallest / 1.5:
        raise NotMeasurable(
            "This model is too large to check at that printer's resolution. Check a smaller "
            "model, or choose a coarser setting.")
    coarse = pixel > ideal * 1.05
    low, high = float(mesh.bounds[0][2]), float(mesh.bounds[1][2])
    height = high - low
    if height <= 0:
        raise NotMeasurable("This model is flat — there is nothing to slice.")

    total_layers = max(1, int(round(height / profile["layer_mm"])))
    wanted = min(layer_count, total_layers)
    heights = np.linspace(low + height * 0.01, high - height * 0.01, wanted)

    read = failed = 0
    missing_points, fragile_points = [], []
    missing_area = fragile_area = solid_area = 0.0
    widths = []

    for z in heights:
        try:
            section = mesh.section(plane_origin=[0, 0, float(z)], plane_normal=[0, 0, 1])
            if section is None:
                continue                                   # nothing solid at this height
            flat, to_3d = section.to_2D()
            image, origin = _raster(flat, pixel)
        except NotMeasurable:
            raise
        except Exception:
            failed += 1                                    # a torn cross-section, not a verdict
            continue
        if image is None:
            continue
        read += 1

        # Drawing a curve on a pixel grid leaves a staircase, and a staircase has
        # one-pixel notches in it that no printer would ever be asked to make.
        # Closing by a pixel removes them, so what is measured below is the model's
        # own shape rather than the way it happened to land on the grid.
        solid = cv2.morphologyEx((image > 0).astype(np.uint8), cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        solid_area += float(solid.sum()) * pixel * pixel
        reach = cv2.distanceTransform(solid, cv2.DIST_L2, 5)     # half-width at every point
        if reach.size:
            widths.append(float(reach.max()) * 2.0 * pixel)

        # A printer cannot make anything narrower than its tool, and morphological
        # opening is that statement exactly: erode by the tool, then put back what
        # survived. Whatever does not come back is what the machine cannot lay down.
        # Asking instead for the widest circle in each island would answer a
        # different question — a 30 mm cube with a 0.15 mm fin on it is one island,
        # and its widest circle is 30 mm, which says nothing about the fin.
        for limit, points, bucket in ((smallest, missing_points, "missing"),
                                      (fragile, fragile_points, "fragile")):
            radius = max(1, int(round(limit / 2.0 / pixel)))
            if radius > 64:                                # a tool that coarse means all of it
                radius = 64
            size = radius * 2 + 1
            tool = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))
            survives = cv2.morphologyEx(solid, cv2.MORPH_OPEN, tool)
            lost = ((solid > 0) & (survives == 0)).astype(np.uint8)
            if bucket == "fragile":
                lost[cut_already] = 0                      # already counted as missing outright
            else:
                cut_already = lost > 0
            if not lost.any():
                continue
            # A real feature is at least a few tool-widths of something. Anything
            # smaller is the last of the rasterising, and reporting it as a fault
            # would bury the true ones in hundreds of imaginary ones.
            floor = max(4, 3 * size)
            here = float(solid.sum())
            count, labels, stats, centroids = cv2.connectedComponentsWithStats(lost, 8)
            kept = 0.0
            for island in range(1, count):
                small = stats[island, cv2.CC_STAT_AREA]
                # ...but a model printed so small that its whole cross-section is a
                # few pixels across has no rastering noise to speak of — it is all
                # too fine. Never let the noise floor swallow a large share of the
                # layer, or "far too small to print" comes back as "prints as modelled".
                if small < floor and small < 0.2 * here:
                    continue
                kept += float(stats[island, cv2.CC_STAT_AREA])
                cx, cy = centroids[island]
                spot = np.array([origin[0] + cx * pixel, origin[1] + cy * pixel, 0.0, 1.0])
                points.append((np.asarray(to_3d) @ spot)[:3])
            area = kept * pixel * pixel
            if bucket == "missing":
                missing_area += area
            else:
                fragile_area += area

    if read == 0:
        raise NotMeasurable("None of this model's cross-sections could be read. "
                            "Repair it first, then check it again.")
    if read / max(1, read + failed) < MIN_USABLE_LAYERS:
        raise NotMeasurable(
            f"Only {read} of {read + failed} cross-sections could be read, so any answer would be "
            "a guess. Repair the model first — open holes and loose pieces are what break slicing.")

    return {
        "layers_read": read, "layers_failed": failed, "layers_total": total_layers,
        "missing": np.asarray(missing_points).reshape(-1, 3),
        "fragile": np.asarray(fragile_points).reshape(-1, 3),
        "missing_share": missing_area / solid_area if solid_area else 0.0,
        "fragile_share": fragile_area / solid_area if solid_area else 0.0,
        "narrowest_mm": float(min(widths)) if widths else None,
        "widest_mm": float(max(widths)) if widths else None,
        "coarse": bool(coarse), "raster_mm": round(pixel, 5),
    }


# --------------------------------------------------------------------------- the confirming measurement
def _wall_thickness(mesh):
    """
    Wall thickness at every vertex, by firing a ray into the surface.

    Returns None when the mesh is in no state to be asked: a ray needs a surface
    that reliably faces outwards, and a model with inconsistent winding or open
    holes gives confident nonsense instead of an error.
    """
    try:
        if not mesh.is_winding_consistent or not mesh.is_watertight:
            return None
        # Measured over the surface rather than at the corners. A vertex sits where
        # the modelling put it, and its normal is the average of the faces meeting
        # there: on a cube every vertex is a corner, so every ray runs down the body
        # diagonal and a 30 mm cube measures 52 mm thick. Points spread over the
        # faces, each carrying its own face's normal, ask the question properly.
        faces = np.asarray(mesh.faces, np.int64)
        corners = np.asarray(mesh.vertices, np.float64)[faces]
        take = np.arange(len(faces))
        if len(faces) > 200_000:
            take = np.linspace(0, len(faces) - 1, 200_000).astype(np.int64)
        points = corners[take].mean(axis=1)
        normals = np.asarray(mesh.face_normals, np.float64)[take]
        # Start the ray a little way in. Too little and rounding lets it strike the
        # very triangle it left, which reports every wall in the model as 0.000 mm —
        # a confident, uniform, completely wrong answer. The offset scales with the
        # model so it means the same thing on a 5 mm part and a 500 mm one.
        step = max(1e-4, float(mesh.scale) * 1e-5)
        hits, ray_index, _ = mesh.ray.intersects_location(
            points - normals * step, -normals, multiple_hits=False)
        if not len(ray_index):
            return None
        distance = np.linalg.norm(hits - points[ray_index], axis=1)
        return {"points": points[ray_index], "thickness": distance}
    except Exception:
        return None                    # a confirmation that fails is simply absent


# --------------------------------------------------------------------------- putting it together
def _locate(points):
    """The location payload the viewport already knows how to highlight."""
    points = np.asarray(points, float).reshape(-1, 3)
    if not len(points):
        return None
    shown = points
    if len(points) > MAX_LOCATION_POINTS:
        shown = points[np.linspace(0, len(points) - 1, MAX_LOCATION_POINTS).astype(int)]
    low, high = points.min(axis=0), points.max(axis=0)
    return {"points": np.round(shown, 3).tolist(), "total": int(len(points)),
            "center": np.round((low + high) / 2, 3).tolist(),
            "extent": round(float(np.linalg.norm(high - low)), 3)}


def _fit(mesh, profile):
    """Does it fit on the plate, and if not by how much."""
    build = profile.get("build_mm") or [0, 0, 0]
    if not all(build):
        return None
    size = np.asarray(mesh.extents, float)
    plate = np.asarray(build, float)
    # The model may be turned on the plate, so compare its footprint either way round.
    footprint = sorted(size[:2])
    bed = sorted(plate[:2])
    fits = footprint[0] <= bed[0] and footprint[1] <= bed[1] and size[2] <= plate[2]
    return {"fits": bool(fits), "size_mm": [round(float(v), 1) for v in size],
            "build_mm": [round(float(v), 1) for v in plate],
            "largest_scale": round(float(min(bed[0] / max(footprint[0], 1e-9),
                                             bed[1] / max(footprint[1], 1e-9),
                                             plate[2] / max(size[2], 1e-9))), 2)}


def suggested_scale(mesh, profile, target_share=0.001, layers=12):
    """
    How much bigger the model has to be before nothing is lost.

    This is the answer people actually want and the only one that risks nothing:
    it changes no geometry, and it is exact for the size it names.
    """
    low, high = 1.0, 8.0
    try:
        if _scan_layers(mesh, profile, layers)["missing_share"] <= target_share:
            return 1.0
    except NotMeasurable:
        return None
    for _ in range(6):
        middle = (low + high) / 2.0
        trial = mesh.copy()
        trial.apply_scale(middle)
        try:
            share = _scan_layers(trial, profile, layers)["missing_share"]
        except NotMeasurable:
            return None
        if share <= target_share:
            high = middle
        else:
            low = middle
    return round(high, 2)


def check(mesh: trimesh.Trimesh, profile: dict, thorough: bool = False,
          with_scale: bool = True) -> dict:
    """
    Measure a model against one printer.

    Never invents a verdict: where the two measurements disagree, or the mesh is in
    no state to be measured, that is what comes back.
    """
    if mesh is None or not len(mesh.faces):
        raise NotMeasurable("There is no model to check.")

    # Whether it fits on the plate is settled first, and cheaply. It is the one
    # answer that is always available, and a model too big to print is also the
    # case most likely to be too big to slice at the printer's resolution — so
    # finding that out first means the person is told the useful thing either way.
    fit = _fit(mesh, profile)
    oversized = fit and not fit["fits"]
    try:
        scan = _scan_layers(mesh, profile, THOROUGH_LAYERS if thorough else QUICK_LAYERS)
    except NotMeasurable:
        if not oversized:
            raise
        scan = None

    confirm = _wall_thickness(mesh) if scan is not None else None
    smallest = profile["min_feature_mm"]

    doubts = []
    if scan is None:
        doubts.append("the model is larger than the printer, and too large to check for fine "
                      "detail at its resolution; scale it to fit and check it again")
    elif scan["coarse"]:
        doubts.append(f"this model is large, so it was checked at {scan['raster_mm'] * 1000:.0f} µm "
                      "rather than finer; very small details may be missed")
    if confirm is None:
        doubts.append("the model is not a closed, consistently wound solid, so wall thickness "
                      "could not be measured a second way")
    if scan and scan["layers_failed"]:
        doubts.append(f"{scan['layers_failed']} of {scan['layers_read'] + scan['layers_failed']} "
                      "cross-sections could not be read")

    agreed = None
    if confirm is not None and scan is not None:
        thin = confirm["thickness"] < smallest
        agreed = {
            "share_of_surface": float(thin.mean()),
            "thinnest_mm": float(np.percentile(confirm["thickness"], 0.5)),
            "median_mm": float(np.median(confirm["thickness"])),
            "points": confirm["points"][thin],
        }
        # Two measurements of the same model should tell the same story. When they
        # do not, that is itself the finding, and it is reported rather than hidden.
        sliced_share = scan["missing_share"]
        if abs(agreed["share_of_surface"] - sliced_share) > 0.25:
            doubts.append(
                f"the two measurements disagree — slicing finds {sliced_share:.0%} of the model "
                f"too fine, measuring the walls finds {agreed['share_of_surface']:.0%}")

    issues = []
    if scan is not None and (scan["missing_share"] > 0 or len(scan["missing"])):
        issues.append({
            "id": "unprintable_detail", "severity": "critical",
            "title": f"Detail finer than this printer can make",
            "detail": (f"{len(scan['missing']):,} places are narrower than {smallest:.3f} mm "
                       f"({profile['basis']}), which is {scan['missing_share']:.1%} of the model. "
                       "They will not appear in the print at all."),
            "count": int(len(scan["missing"])), "location": _locate(scan["missing"]),
        })
    if scan is not None and len(scan["fragile"]):
        issues.append({
            "id": "fragile_detail", "severity": "warning",
            "title": "Detail thin enough to break",
            "detail": (f"{len(scan['fragile']):,} places are under {profile['fragile_below_mm']:.3f} mm. "
                       "They will print, as a single line or column, and snap easily."),
            "count": int(len(scan["fragile"])), "location": _locate(scan["fragile"]),
        })

    if oversized:
        issues.append({
            "id": "too_big", "severity": "critical", "title": "Larger than the build volume",
            "detail": (f"The model is {fit['size_mm'][0]} × {fit['size_mm'][1]} × {fit['size_mm'][2]} mm "
                       f"and the plate is {fit['build_mm'][0]} × {fit['build_mm'][1]} × {fit['build_mm'][2]} mm. "
                       f"It would fit at {fit['largest_scale']:.2f}× or smaller."),
            "count": 1, "location": None,
        })

    scale = (suggested_scale(mesh, profile)
             if (with_scale and scan is not None and scan["missing_share"] > 0) else None)
    # A scale that would not fit on the plate is still worth saying: "it would need
    # to be four times bigger, which your printer cannot hold" is an answer. Going
    # quiet instead leaves the person with a problem and no number.
    scale_fits = None if scale is None or not fit else bool(scale <= fit["largest_scale"])

    printable = not any(i["severity"] == "critical" for i in issues)
    return {
        "printer": profile,
        "verdict": ("Prints as modelled" if printable and not issues else
                    "Prints, with fragile detail" if printable else
                    "Some detail will be lost"),
        "printable": printable,
        "issues": issues,
        "narrowest_mm": scan["narrowest_mm"] if scan else None,
        "widest_mm": scan["widest_mm"] if scan else None,
        "missing_share": scan["missing_share"] if scan else None,
        "fragile_share": scan["fragile_share"] if scan else None,
        "layers_read": scan["layers_read"] if scan else 0,
        "layers_total": scan["layers_total"] if scan else 0,
        "thorough": bool(thorough),
        "confirmed": confirm is not None and not doubts,
        "doubts": doubts,
        "wall": None if agreed is None else {
            "thinnest_mm": round(agreed["thinnest_mm"], 4),
            "median_mm": round(agreed["median_mm"], 4),
            "share_below": round(agreed["share_of_surface"], 4),
        },
        "fit": fit,
        "suggested_scale": scale,
        "suggested_scale_fits": scale_fits,
        # A bare multiplier means nothing next to a model saved at 0.99 mm tall, which
        # is how most generated models arrive. The height it would end up at does.
        "suggested_height_mm": None if not scale else round(float(mesh.extents[2]) * scale, 1),
        "height_mm": round(float(mesh.extents[2]), 2),
        "estimate": describe_duration((scan["layers_total"] if scan else 0) * 0.02),
    }
