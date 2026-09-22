"""
What a file actually holds, before any of it is welded together.

Meshwright has always loaded a model by flattening it: every object in the file is
concatenated into one mesh, because one mesh is what the diagnostics, the repair
and the slicer all want. That is right for a figure saved on its own and wrong for
everything else. A Blender project lit for rendering brings its studio floor and
reflection cards; a scene exported to GLB brings whatever else was in the scene;
and all of it arrives fused to the model, impossible to separate again by hand.

This module answers the question that has to come first — *what is in here?* — as
cheaply as each format allows:

* **.blend** — Blender itself lists it, which takes a second or two against the
  minutes an export costs, and reports the collections the author organised it into.
* **GLB and glTF** — read from the file's own JSON header. The node names, the mesh
  each node points at and the triangle counts are all there, so a 426 MB file can be
  listed without touching the 426 MB.
* **Everything else** — loaded as a scene and enumerated.

Nothing here decides what to throw away. It reports `hidden` for the objects the
file itself marks as not-for-render, and suggests those be left out; the rest is the
person's call. That restraint is deliberate: in a real project the 200 x 200 ground
plane was named "Studio ground - excluded from model validation" and carried no flag
at all to say so, while a genuine floor tile in a printed diorama would look exactly
the same to any rule that tried to guess.
"""
import json
import os
import struct

import numpy as np

# Formats that can only ever hold one object. Asking about them is noise.
SINGLE_OBJECT = {".stl", ".ply", ".off"}


def _suggested(part: dict) -> bool:
    """Leave out what carries no geometry, and what the file says not to render."""
    return bool(part.get("faces")) and not part.get("hidden")


def _finish(parts: list, armatures=None, scene_name="") -> dict:
    for index, part in enumerate(parts):
        part.setdefault("id", part.get("name") or f"part-{index}")
        part.setdefault("group", "")
        part.setdefault("hidden", False)
        part["keep"] = _suggested(part)
    groups = []
    for part in parts:
        if part["group"] not in groups:
            groups.append(part["group"])
    return {
        "parts": parts,
        "groups": groups,
        "armatures": armatures or [],
        "scene": scene_name,
        "total_faces": int(sum(p.get("faces") or 0 for p in parts)),
        "multi": len([p for p in parts if p.get("faces")]) > 1,
    }


# --------------------------------------------------------------------------- glTF, read from its header
def _gltf_parts(path: str) -> dict:
    """
    Names and sizes out of the JSON header, without reading the geometry.

    A node is what a modelling program calls an object, so nodes are what this
    lists; the mesh a node points at may be shared by several of them.
    """
    ext = os.path.splitext(path)[1].lower()
    if ext == ".glb":
        with open(path, "rb") as handle:
            header = handle.read(20)
            if len(header) < 20 or header[:4] != b"glTF":
                raise ValueError("not a GLB")
            length = struct.unpack("<I", header[12:16])[0]
            doc = json.loads(handle.read(length))
    else:
        with open(path, "rb") as handle:
            doc = json.loads(handle.read())

    accessors = doc.get("accessors") or []
    meshes = doc.get("meshes") or []

    def triangles(mesh):
        total = 0
        for prim in mesh.get("primitives") or []:
            if prim.get("mode", 4) != 4:
                continue
            if "indices" in prim and prim["indices"] < len(accessors):
                total += accessors[prim["indices"]].get("count", 0) // 3
            else:
                position = prim.get("attributes", {}).get("POSITION")
                if position is not None and position < len(accessors):
                    total += accessors[position].get("count", 0) // 3
        return total

    def span(mesh):
        low = np.full(3, np.inf)
        high = np.full(3, -np.inf)
        for prim in mesh.get("primitives") or []:
            position = prim.get("attributes", {}).get("POSITION")
            if position is None or position >= len(accessors):
                continue
            accessor = accessors[position]
            if accessor.get("min") and accessor.get("max"):
                low = np.minimum(low, np.asarray(accessor["min"][:3], float))
                high = np.maximum(high, np.asarray(accessor["max"][:3], float))
        if not np.isfinite(low).all():
            return [0.0, 0.0, 0.0]
        return [round(float(v), 4) for v in (high - low)]

    # A node's parent chain is the nearest thing glTF has to a collection, and
    # Blender writes its collections out as exactly those empty parent nodes.
    nodes = doc.get("nodes") or []
    parent = {}
    for index, node in enumerate(nodes):
        for child in node.get("children") or []:
            parent[child] = index

    def group_of(index):
        seen = set()
        walk = parent.get(index)
        while walk is not None and walk not in seen:
            seen.add(walk)
            if "mesh" not in nodes[walk]:
                return nodes[walk].get("name") or ""
            walk = parent.get(walk)
        return ""

    parts = []
    for index, node in enumerate(nodes):
        if "mesh" not in node or node["mesh"] >= len(meshes):
            continue
        mesh = meshes[node["mesh"]]
        name = node.get("name") or mesh.get("name") or f"object {index + 1}"
        parts.append({
            # The name is the handle: it is what trimesh will call this node when the
            # chosen parts are assembled, so listing and loading agree on identity.
            "id": name,
            "name": name,
            "faces": triangles(mesh),
            "size": span(mesh),
            "group": group_of(index),
            "rigged": "skin" in node,
        })
    return _finish(parts, scene_name=doc.get("scenes", [{}])[doc.get("scene", 0)].get("name", "")
                   if doc.get("scenes") else "")


# --------------------------------------------------------------------------- anything trimesh can open
def _scene_parts(path: str) -> dict:
    import trimesh

    loaded = trimesh.load(path, process=False)
    if isinstance(loaded, trimesh.Trimesh):
        return _finish([{"name": os.path.basename(path), "faces": int(len(loaded.faces)),
                         "size": [round(float(v), 4) for v in loaded.extents]}])
    parts = []
    for piece in loaded.dump():
        parts.append({
            "name": piece.metadata.get("name") or f"object {len(parts) + 1}",
            "faces": int(len(piece.faces)),
            "size": [round(float(v), 4) for v in piece.extents],
        })
    return _finish(parts)


# --------------------------------------------------------------------------- the one entry point
def list_parts(path: str, log=None) -> dict:
    """
    Everything the file holds, and whether it is worth asking about.

    Never raises for an ordinary file: a format that cannot be inspected comes back
    as a single part, which is the truthful answer — Meshwright will open it whole.
    """
    log = log or (lambda *a, **k: None)
    ext = os.path.splitext(path)[1].lower()
    size = os.path.getsize(path) if os.path.exists(path) else 0

    if ext in SINGLE_OBJECT:
        return _finish([{"name": os.path.basename(path), "faces": 0, "size": [0, 0, 0]}])

    try:
        if ext == ".blend":
            from engine.blend_import import list_objects
            found = list_objects(path, log=log)
            return _finish(found.get("parts") or [], found.get("armatures"), found.get("scene", ""))
        if ext in (".glb", ".gltf"):
            return _gltf_parts(path)
        return _scene_parts(path)
    except Exception as trouble:
        # Being unable to list the parts is not a reason to refuse the file; it just
        # means the usual behaviour — open all of it — is the only one on offer.
        log(f"Could not list what is in this file ({trouble}); it will be opened whole", "warn")
        return _finish([{"name": os.path.basename(path), "faces": 0, "size": [0, 0, 0]}])


def assemble(path: str, keep):
    """
    Open only the named objects, each in its own place, welded into one mesh.

    The placement matters more than it looks: a file's objects are positioned by its
    scene graph, and reading the geometries without their node transforms piles every
    one of them onto the origin.
    """
    import trimesh

    scene = trimesh.load(path, process=False)
    if isinstance(scene, trimesh.Trimesh):
        return scene

    wanted = {str(name) for name in keep}
    pieces = []
    for node in scene.graph.nodes_geometry:
        transform, geometry = scene.graph[node]
        if node not in wanted and geometry not in wanted:
            continue
        piece = scene.geometry[geometry].copy()
        piece.apply_transform(transform)
        pieces.append(piece)
    if not pieces:
        raise ValueError("None of the chosen objects are in this file.")
    return trimesh.util.concatenate(pieces) if len(pieces) > 1 else pieces[0]
