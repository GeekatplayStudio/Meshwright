"""
A quick look at a model file: its headline facts and a small picture of it,
*without* loading it.

Opening a model takes seconds — 7.7 s for a 67 MB GLB — because it builds a real
mesh. Choosing which file to open should not cost that, so nothing here builds one.
Every format is reduced to a sample of at most a few hundred thousand triangles,
read straight out of the file, and those are splatted into a small depth-and-normal
image which is then lit. Cost is bounded by the sample, not by the model: a 249 MB,
five-million-face STL takes about as long as a 50 MB one.

Windows itself is no help. It draws 3D thumbnails through Microsoft's 3D Viewer,
which is no longer part of Windows 11, so STL, GLB, GLTF, PLY, 3MF and OFF files
have no thumbnail and no preview handler on a normal machine — which is exactly why
the standard Open dialog shows nothing but a row of identical icons.

What is read is deliberately shallow, and the result says so when it is uncertain:
dimensions of a very large file come from the sample and are marked approximate,
and formats that cannot be sampled cheaply (FBX above the size cap, DAE, 3DS, a
huge 3MF) return facts with no picture rather than a slow one.
"""
import hashlib
import json
import os
import re
import struct
import time
import zipfile

import numpy as np

from engine import axes
from engine.estimation import describe_duration, estimate_seconds

PREVIEW_PX = 320
THUMB_PX = 96
SAMPLE = 400_000                        # triangles kept for a full-size preview
EXACT_BBOX_LIMIT = 512 * 1024 ** 2      # past this, dimensions come from the sample
TEXT_LIMIT = 400 * 1024 ** 2            # OBJ/OFF/ASCII-STL are read whole; past this, facts only
FBX_LIMIT = 64 * 1024 ** 2              # FBX has no header to read: it is parsed or not at all
INDEX_LIMIT = 256 * 1024 ** 2           # GLB index data past this: fall back to a point cloud
XML_LIMIT = 64 * 1024 ** 2              # 3MF geometry past this: facts only
CACHE_KEEP = 600                        # pictures kept on disk

# A preview is a promise about what opening the file will show, so it is turned the
# same way opening it would turn it — by the one rule in engine/axes.py, never by a
# second opinion kept here. An OBJ that its author saved lying down is previewed
# lying down, because that is how it will arrive.
Y_UP_FORMATS = axes.SPEC_Y_UP

# What Windows reports for a file that lives in the cloud and would have to be
# downloaded before it could be read. Previewing one would silently pull it down.
_FILE_ATTRIBUTE_OFFLINE = 0x1000
_FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x40000
_FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x400000
_CLOUD_ONLY = (_FILE_ATTRIBUTE_OFFLINE | _FILE_ATTRIBUTE_RECALL_ON_OPEN
               | _FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS)


class Unreadable(Exception):
    """The file cannot be looked at — carries the sentence shown to the user."""


# --------------------------------------------------------------------------- small helpers
def size_text(n: int) -> str:
    if n < 1024:
        return f"{n} bytes"
    if n < 1024 ** 2:
        return f"{n / 1024:.0f} KB"
    if n < 1024 ** 3:
        return f"{n / 1024 ** 2:.1f} MB"
    return f"{n / 1024 ** 3:.2f} GB"


def is_cloud_only(path: str) -> bool:
    """True for a OneDrive file whose contents are not on this disk yet."""
    if os.name != "nt":
        return False
    try:
        return bool(os.stat(path).st_file_attributes & _CLOUD_ONLY)
    except (OSError, AttributeError):
        return False


def _read_at(handle, offset: int, length: int, limit: int) -> bytes:
    """
    Read a byte range named by the file itself, refusing one that runs off the end.

    A file that names a range it does not contain is damaged. Memory-mapping it and
    walking off the end would kill the whole process rather than raise, so nothing
    here is mapped: ranges are checked and then read.
    """
    if offset < 0 or length < 0 or offset + length > limit:
        raise Unreadable("This file is damaged — it points at data that is not in it.")
    handle.seek(offset)
    data = handle.read(length)
    if len(data) != length:
        raise Unreadable("This file is damaged — it ends sooner than it says it does.")
    return data


def _strided(buf: bytes, count: int, cols: int, dtype, stride: int) -> np.ndarray:
    """View a packed or interleaved array inside `buf` without copying it."""
    item = np.dtype(dtype).itemsize
    shape = (count, cols) if cols > 1 else (count,)
    strides = (stride, item) if cols > 1 else (stride,)
    return np.ndarray(shape, dtype, buf, 0, strides)


def _floats(text: bytes, cols: int) -> np.ndarray:
    """Parse whitespace-separated numbers into an (n, cols) array."""
    flat = np.fromstring(text, sep=" ", dtype=np.float64)      # sep="" is the deprecated form, not this
    usable = (len(flat) // cols) * cols
    return flat[:usable].reshape(-1, cols).astype(np.float32)


def _sample_surface(tri: np.ndarray, budget: int):
    """
    Scatter `budget` points over the triangles, each carrying its triangle's normal.

    Points are spread by area rather than one per triangle: a single big flat face
    would otherwise light one pixel and leave the rest of itself as holes, which is
    what made an earlier version of this look like static.
    """
    edge1, edge2 = tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]
    normals = np.cross(edge1, edge2)
    twice_area = np.linalg.norm(normals, axis=1)
    normals = (normals / np.maximum(twice_area, 1e-20)[:, None]).astype(np.float32)
    solid = twice_area > 0
    if not solid.any():
        raise Unreadable("Every triangle in this file is flat — there is nothing to show.")
    tri, normals, twice_area = tri[solid], normals[solid], twice_area[solid]

    rng = np.random.default_rng(0)                    # a fixed seed: the same file looks the same twice
    chosen = rng.choice(len(tri), size=budget, p=twice_area / twice_area.sum())
    u = rng.random((budget, 1), dtype=np.float32)
    v = rng.random((budget, 1), dtype=np.float32)
    outside = (u + v) > 1
    u, v = np.where(outside, 1 - u, u), np.where(outside, 1 - v, v)
    picked = tri[chosen]
    points = picked[:, 0] + u * (picked[:, 1] - picked[:, 0]) + v * (picked[:, 2] - picked[:, 0])
    return points.astype(np.float32), normals[chosen]


def _stride_for(total: int, budget: int) -> int:
    return max(1, total // max(1, budget))


# --------------------------------------------------------------------------- format readers
#
# Each reader returns (facts, sample). `sample` is (points, normals or None) in the
# file's own coordinates, or None when this file can only be described, not drawn.

def _read_stl(path, size, budget):
    with open(path, "rb") as f:
        head = f.read(84)
        if len(head) < 84:
            raise Unreadable("This STL file is too short to be a model.")
        count = struct.unpack("<I", head[80:84])[0]
        if 84 + 50 * count != size:
            return _read_stl_ascii(path, size, budget)

        record = np.dtype([("normal", "<f4", (3,)), ("corners", "<f4", (3, 3)), ("attr", "<u2")])
        exact = size <= EXACT_BBOX_LIMIT
        stride = _stride_for(count, budget)
        low, high = np.full(3, np.inf), np.full(3, -np.inf)
        kept, seen = [], 0
        # One sequential pass: the exact size comes from every triangle, the picture
        # from every stride-th one. Read in blocks so memory does not follow the file.
        for start in range(0, count, 500_000):
            block = np.fromfile(f, dtype=record, count=min(500_000, count - start))
            if not len(block):
                break
            corners = np.ascontiguousarray(block["corners"])
            if exact:
                flat = corners.reshape(-1, 3)
                low, high = np.minimum(low, flat.min(0)), np.maximum(high, flat.max(0))
            kept.append(corners[(-seen) % stride::stride])
            seen += len(block)
            if not exact and sum(len(k) for k in kept) >= budget * 3:
                break
    triangles = np.concatenate(kept) if kept else np.zeros((0, 3, 3), np.float32)
    if not len(triangles):
        raise Unreadable("This STL file contains no triangles.")
    if not exact:
        flat = triangles.reshape(-1, 3)
        low, high = flat.min(0), flat.max(0)
    facts = {"format": "STL (binary)", "faces": count, "vertices": count * 3,
             "bounds": (low, high), "bounds_exact": exact}
    return facts, _sample_surface(triangles, budget)


def _read_stl_ascii(path, size, budget):
    if size > TEXT_LIMIT:
        raise Unreadable("This STL file is written as text and is too large to look inside quickly.")
    with open(path, "rb") as f:
        data = f.read()
    if b"facet" not in data:
        raise Unreadable("This does not look like an STL file.")
    count = data.count(b"facet normal")
    corners = _floats(b" ".join(re.findall(rb"vertex\s+([^\r\n]+)", data)), 3)
    usable = (len(corners) // 3) * 3
    if not usable:
        raise Unreadable("This STL file contains no triangles.")
    triangles = corners[:usable].reshape(-1, 3, 3)
    facts = {"format": "STL (text)", "faces": count or len(triangles), "vertices": len(corners),
             "bounds": (corners.min(0), corners.max(0)), "bounds_exact": True}
    return facts, _sample_surface(triangles, budget)


def _read_glb(path, size, budget):
    with open(path, "rb") as f:
        header = f.read(20)                       # 12-byte file header, then the JSON chunk's own
        if len(header) < 20 or header[:4] != b"glTF":
            raise Unreadable("This does not look like a GLB file.")
        json_length = struct.unpack("<I", header[12:16])[0]
        if json_length <= 0 or 20 + json_length > size:
            raise Unreadable("This GLB file is damaged — its description does not fit inside it.")
        try:
            scene = json.loads(f.read(json_length))            # the read continues from byte 20
        except (ValueError, UnicodeDecodeError):
            raise Unreadable("This GLB file's description is damaged.")
        return _from_gltf(scene, f, 20 + json_length + 8, size, budget, "GLB")


def _read_gltf(path, size, budget):
    if size > TEXT_LIMIT:
        raise Unreadable("This glTF file is too large to look inside quickly.")
    try:
        with open(path, "rb") as f:
            scene = json.loads(f.read())
    except (ValueError, UnicodeDecodeError):
        raise Unreadable("This glTF file could not be read.")
    buffers = scene.get("buffers") or [{}]
    source = buffers[0].get("uri")
    if not source or source.startswith("data:"):
        # The geometry is inline base64 (or absent); the facts still come out of the JSON.
        facts = _gltf_facts(scene, "glTF")
        return facts, None
    companion = os.path.join(os.path.dirname(path), source.replace("/", os.sep))
    if not os.path.isfile(companion):
        facts = _gltf_facts(scene, "glTF")
        facts["note"] = f"Its geometry lives in {os.path.basename(companion)}, which is not in this folder."
        return facts, None
    with open(companion, "rb") as f:
        return _from_gltf(scene, f, 0, os.path.getsize(companion), budget, "glTF")


def _gltf_facts(scene, label):
    accessors = scene.get("accessors") or []
    faces = vertices = 0
    low, high = np.full(3, np.inf), np.full(3, -np.inf)
    for mesh in scene.get("meshes") or []:
        for prim in mesh.get("primitives") or []:
            position = prim.get("attributes", {}).get("POSITION")
            if position is None or position >= len(accessors):
                continue
            acc = accessors[position]
            vertices += acc.get("count", 0)
            if "indices" in prim and prim["indices"] < len(accessors):
                faces += accessors[prim["indices"]].get("count", 0) // 3
            else:
                faces += acc.get("count", 0) // 3
            if acc.get("min") and acc.get("max"):
                low = np.minimum(low, np.asarray(acc["min"][:3], float))
                high = np.maximum(high, np.asarray(acc["max"][:3], float))
    if not np.isfinite(low).all():
        low, high = np.zeros(3), np.zeros(3)
    return {"format": label, "faces": faces, "vertices": vertices,
            "bounds": (low, high), "bounds_exact": True,
            "textures": len(scene.get("images") or []),
            "compressed": bool(scene.get("extensionsUsed"))}


def _from_gltf(scene, handle, binary_at, binary_size, budget, label):
    facts = _gltf_facts(scene, label)
    accessors, views = scene.get("accessors") or [], scene.get("bufferViews") or []
    if facts["compressed"]:
        # Draco or meshopt: the positions in the file are packed, not coordinates.
        facts["note"] = "This file is compressed, so its picture appears once it is open."
        return facts, None
    if not facts["vertices"]:
        return facts, None

    index_types = {5121: np.uint8, 5123: np.uint16, 5125: np.uint32}

    def fetch(index, cols, dtype):
        acc = accessors[index]
        view = views[acc["bufferView"]]
        item = np.dtype(dtype).itemsize * cols
        stride = view.get("byteStride") or item
        start = binary_at + view.get("byteOffset", 0) + acc.get("byteOffset", 0)
        span = stride * (acc["count"] - 1) + item
        return _strided(_read_at(handle, start, span, binary_size), acc["count"], cols, dtype, stride)

    triangles, clouds, index_bytes = [], [], 0
    for mesh in scene.get("meshes") or []:
        for prim in mesh.get("primitives") or []:
            position = prim.get("attributes", {}).get("POSITION")
            if position is None or position >= len(accessors) or "bufferView" not in accessors[position]:
                continue
            if prim.get("mode", 4) != 4:                       # not triangles: lines or points
                continue
            share = max(1, int(budget * accessors[position]["count"] / max(1, facts["vertices"])))
            indexed = ("indices" in prim and prim["indices"] < len(accessors)
                       and "bufferView" in accessors[prim["indices"]])
            if indexed:
                acc = accessors[prim["indices"]]
                index_bytes += acc["count"] * np.dtype(index_types.get(acc["componentType"], np.uint32)).itemsize
            if indexed and index_bytes <= INDEX_LIMIT:
                acc = accessors[prim["indices"]]
                raw = fetch(prim["indices"], 1, index_types.get(acc["componentType"], np.uint32))
                usable = (len(raw) // 3) * 3
                if not usable:
                    continue
                faces = raw[:usable].reshape(-1, 3)
                picked = np.asarray(faces[::_stride_for(len(faces), share)], np.int64)
                points = fetch(position, 3, np.float32)
                if picked.max(initial=0) >= len(points):
                    raise Unreadable("This file is damaged — it refers to points it does not contain.")
                triangles.append(points[picked])
            else:
                points = fetch(position, 3, np.float32)
                clouds.append(np.array(points[::_stride_for(len(points), share)]))
    if triangles:
        return facts, _sample_surface(np.concatenate(triangles).astype(np.float32), budget)
    if clouds:
        return facts, (np.concatenate(clouds).astype(np.float32), None)
    return facts, None


def _read_obj(path, size, budget):
    if size > TEXT_LIMIT:
        raise Unreadable("This OBJ file is too large to look inside quickly.")
    with open(path, "rb") as f:
        data = f.read()
    vertex_lines = re.findall(rb"(?m)^v[ \t]+([^\r\n]+)", data)
    if not vertex_lines:
        raise Unreadable("This OBJ file contains no points.")
    columns = max(3, len(vertex_lines[0].split()))
    points = _floats(b"\n".join(vertex_lines), columns)[:, :3]
    face_count = data.count(b"\nf ") + data.count(b"\nf\t") + (1 if data[:2] in (b"f ", b"f\t") else 0)
    facts = {"format": "OBJ", "faces": face_count, "vertices": len(points),
             "bounds": (points.min(0), points.max(0)), "bounds_exact": True}

    if not face_count:
        return facts, (points[::_stride_for(len(points), budget)], None)   # a point cloud
    # Only every stride-th face line is turned into text: the scan is C-speed and
    # what it materialises stays proportional to the sample, not to the model.
    stride = _stride_for(face_count, budget)
    corners = []
    for n, match in enumerate(re.finditer(rb"(?m)^f[ \t]+([^\r\n]+)", data)):
        if n % stride:
            continue
        pieces = match.group(1).split()
        if len(pieces) < 3:
            continue
        try:
            fan = [int(p.split(b"/")[0]) for p in pieces[:3]]
        except ValueError:
            continue
        corners.append(fan)
    if not corners:
        return facts, (points[::_stride_for(len(points), budget)], None)
    ids = np.asarray(corners, np.int64)
    ids = np.where(ids < 0, ids + len(points), ids - 1)            # OBJ counts from 1, or back from the end
    ids = ids[(ids >= 0).all(1) & (ids < len(points)).all(1)]
    if not len(ids):
        raise Unreadable("This OBJ file's triangles point outside its own list of points.")
    return facts, _sample_surface(points[ids], budget)


def _read_ply(path, size, budget):
    with open(path, "rb") as f:
        header, chunk = b"", b""
        while b"end_header" not in header:
            chunk = f.read(4096)
            if not chunk:
                raise Unreadable("This PLY file has no readable header.")
            header += chunk
            if len(header) > 1 << 20:
                raise Unreadable("This PLY file has no readable header.")
        text = header[:header.index(b"end_header")].decode("ascii", "replace")
        body_at = header.index(b"end_header") + len(b"end_header")
        body_at += 2 if header[body_at:body_at + 2] == b"\r\n" else 1

        encoding = "ascii" if "format ascii" in text else (
            "little" if "binary_little_endian" in text else "big")
        counts, properties, element = {}, {}, None
        for line in text.splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[0] == "element":
                element = parts[1]
                counts[element] = int(parts[2])
                properties[element] = []
            elif parts[:1] == ["property"] and element:
                properties[element].append(parts[1:])
        vertices, faces = counts.get("vertex", 0), counts.get("face", 0)
        facts = {"format": f"PLY ({'text' if encoding == 'ascii' else 'binary'})",
                 "faces": faces, "vertices": vertices, "bounds": None, "bounds_exact": True}
        if not vertices:
            raise Unreadable("This PLY file contains no points.")

        # Points only: a PLY face is a variable-length list, which cannot be sampled
        # by arithmetic, and scans — the usual source of PLY — are point clouds anyway.
        sizes = {"char": 1, "uchar": 1, "int8": 1, "uint8": 1, "short": 2, "ushort": 2,
                 "int16": 2, "uint16": 2, "int": 4, "uint": 4, "int32": 4, "uint32": 4,
                 "float": 4, "float32": 4, "double": 8, "float64": 8}
        names = [p[-1] for p in properties.get("vertex", [])]
        kinds = [p[-2] if len(p) > 1 else "float" for p in properties.get("vertex", [])]
        if names[:3] != ["x", "y", "z"] or encoding == "big":
            facts["note"] = "Its picture appears once it is open."
            return facts, None
        if encoding == "ascii":
            if size > TEXT_LIMIT:
                raise Unreadable("This PLY file is written as text and is too large to look inside quickly.")
            f.seek(body_at)
            rows = f.read().splitlines()[:vertices]
            points = _floats(b"\n".join(rows), max(3, len(names) or 3))[:, :3]
        else:
            if any(k not in sizes for k in kinds):
                facts["note"] = "Its picture appears once it is open."
                return facts, None
            row = sum(sizes[k] for k in kinds)
            kind = kinds[0]
            if kind not in ("float", "float32", "double", "float64"):
                facts["note"] = "Its picture appears once it is open."
                return facts, None
            dtype = np.float32 if sizes[kind] == 4 else np.float64
            stride = _stride_for(vertices, budget)
            # Points are interleaved with whatever else the file records per point, so
            # reaching the last one means holding the rows in between. Past the cap,
            # read what fits and say the dimensions are approximate.
            reach = vertices if row * vertices <= INDEX_LIMIT else max(1, INDEX_LIMIT // row)
            span = row * (reach - 1) + sizes[kind] * 3
            points = np.array(_strided(_read_at(f, body_at, span, size), reach, 3, dtype, row)[::stride],
                              np.float32)
    if not len(points):
        raise Unreadable("This PLY file contains no points.")
    facts["bounds"] = (points.min(0), points.max(0))
    facts["bounds_exact"] = len(points) == vertices
    return facts, (points, None)


def _read_off(path, size, budget):
    if size > TEXT_LIMIT:
        raise Unreadable("This OFF file is too large to look inside quickly.")
    with open(path, "rb") as f:
        data = re.sub(rb"#[^\r\n]*", b"", f.read())            # OFF allows comments anywhere
    marker = data.split(None, 1)
    if not marker or not marker[0].upper().endswith(b"OFF"):   # OFF, COFF, NOFF, STOFF…
        raise Unreadable("This does not look like an OFF file.")
    # After the marker the file is just numbers: three counts, then the points.
    numbers = np.fromstring(data[len(marker[0]):], sep=" ", dtype=np.float64)
    if len(numbers) < 3:
        raise Unreadable("This OFF file's header could not be read.")
    vertices, faces = int(numbers[0]), int(numbers[1])
    points = numbers[3:3 + vertices * 3]
    if len(points) < 3:
        raise Unreadable("This OFF file contains no points.")
    points = points[: (len(points) // 3) * 3].reshape(-1, 3).astype(np.float32)
    facts = {"format": "OFF", "faces": faces, "vertices": vertices,
             "bounds": (points.min(0), points.max(0)), "bounds_exact": len(points) == vertices}
    return facts, (points[::_stride_for(len(points), budget)], None)


def _read_3mf(path, size, budget):
    try:
        archive = zipfile.ZipFile(path)
        names = archive.namelist()
    except (zipfile.BadZipFile, OSError):
        raise Unreadable("This 3MF file could not be opened — it may be damaged.")
    model = next((n for n in names if n.lower().endswith(".model")), None)
    if model is None:
        raise Unreadable("This 3MF file contains no model.")
    thumbnail = next((n for n in names if n.lower().endswith((".png", ".jpg", ".jpeg"))
                      and "thumbnail" in n.lower()), None)
    info = archive.getinfo(model)
    facts = {"format": "3MF", "faces": 0, "vertices": 0, "bounds": None, "bounds_exact": True}

    if info.file_size > XML_LIMIT:
        # Counting alone means decompressing the whole description; do it in a stream
        # so a 446 MB one costs time but not memory, and stop short of drawing it.
        triangle_tag, vertex_tag = b"<triangle ", b"<vertex "
        carry = len(triangle_tag) - 1        # the most a tag can straddle a chunk boundary
        triangles = points = 0
        with archive.open(model) as stream:
            tail = b""
            while True:
                chunk = stream.read(1 << 24)
                if not chunk:
                    break
                buffer, held = tail + chunk, len(tail)
                # A tag lying across the join is counted this round; one that ended
                # inside the carried tail was counted last round, so counting starts
                # past it. Either way each tag is counted exactly once.
                triangles += buffer[max(0, held - len(triangle_tag) + 1):].count(triangle_tag)
                points += buffer[max(0, held - len(vertex_tag) + 1):].count(vertex_tag)
                tail = buffer[-carry:]
        facts.update(faces=triangles, vertices=points)
        facts["note"] = "It is too large to draw quickly; its picture appears once it is open."
        return facts, (None if thumbnail is None else ("embedded", archive.read(thumbnail)))

    with archive.open(model) as stream:
        text = stream.read()
    # Counts come from counting, not from what the pattern below happens to match:
    # a writer that orders or spaces its attributes differently must still report
    # the right number of triangles, even if it costs the picture.
    facts.update(faces=text.count(b"<triangle "), vertices=text.count(b"<vertex "))
    coords = re.findall(rb'<vertex x="([^"]*)" y="([^"]*)" z="([^"]*)"', text)
    corners = re.findall(rb'<triangle v1="([^"]*)" v2="([^"]*)" v3="([^"]*)"', text)
    if not coords:
        return facts, (None if thumbnail is None else ("embedded", archive.read(thumbnail)))
    points = np.array(coords, dtype=np.bytes_).astype(np.float32)
    facts["bounds"] = (points.min(0), points.max(0))
    if not corners:
        return facts, (points[::_stride_for(len(points), budget)], None)
    ids = np.array(corners[::_stride_for(len(corners), budget)], dtype=np.bytes_).astype(np.int64)
    ids = ids[(ids >= 0).all(1) & (ids < len(points)).all(1)]
    if not len(ids):
        return facts, (points[::_stride_for(len(points), budget)], None)
    return facts, _sample_surface(points[ids], budget)


def _read_fbx(path, size, budget):
    """
    FBX is the one format here that has to be parsed rather than sampled: it keeps
    no header a stranger can read. So it is parsed the way the rest of Meshwright
    parses it — including the teardown order that keeps ufbx from taking the whole
    process down with it — and only below a size where that is quick.
    """
    if size > FBX_LIMIT:
        raise Unreadable("This FBX file is too large to look inside quickly — "
                         "its picture appears once it is open.")
    from engine.model_loader import HAS_UFBX, _load_fbx
    if not HAS_UFBX:
        raise Unreadable("FBX files can only be pictured once they are open.")
    try:
        mesh, _blobs, declared = _load_fbx(path)
    except Exception:
        raise Unreadable("This FBX file could not be read.")
    if mesh is None or not len(mesh.faces):
        raise Unreadable("This FBX file contains no geometry.")
    low, high = mesh.bounds[0], mesh.bounds[1]
    # An FBX says which way is up, so the picture need not fall back to the habit
    # of the format: it is drawn the same way up as the model will open.
    facts = {"format": "FBX", "faces": len(mesh.faces), "vertices": len(mesh.vertices),
             "bounds": (low, high), "bounds_exact": True,
             "up": declared if declared in ("y", "z") else None}
    corners = np.asarray(mesh.vertices, np.float32)[np.asarray(mesh.faces, np.int64)]
    return facts, _sample_surface(corners[::_stride_for(len(corners), budget)], budget)


READERS = {
    ".stl": _read_stl, ".glb": _read_glb, ".gltf": _read_gltf, ".obj": _read_obj,
    ".ply": _read_ply, ".off": _read_off, ".3mf": _read_3mf, ".fbx": _read_fbx,
}
DESCRIBE_ONLY = {".blend": "Blender project (requires Blender)", ".dae": "COLLADA", ".3ds": "3D Studio"}


# --------------------------------------------------------------------------- drawing
def _shade(points, normals, up, px):
    """Splat the sample into a depth image, light it, and return BGRA pixels."""
    import cv2

    p = np.asarray(points, np.float32)
    if up == "z":                                     # stand a Z-up model up in a Y-up picture
        p = np.stack([p[:, 0], p[:, 2], -p[:, 1]], 1)
    n = None
    if normals is not None:
        n = np.asarray(normals, np.float32)
        if up == "z":
            n = np.stack([n[:, 0], n[:, 2], -n[:, 1]], 1)

    angle, tilt = np.radians(35.0), np.radians(28.0)
    ca, sa, ct, st = np.cos(angle), np.sin(angle), np.cos(tilt), np.sin(tilt)

    def turn(v):
        x = v[:, 0] * ca + v[:, 2] * sa
        back = -v[:, 0] * sa + v[:, 2] * ca
        return x, v[:, 1] * ct - back * st, v[:, 1] * st + back * ct

    x, y, depth = turn(p)
    width = max(float(x.max() - x.min()), float(y.max() - y.min()), 1e-9)
    scale = 0.88 * px / width
    cx, cy = (x.min() + x.max()) / 2, (y.min() + y.max()) / 2
    col = np.clip(((x - cx) * scale + px / 2).astype(np.int32), 0, px - 1)
    row = np.clip((px / 2 - (y - cy) * scale).astype(np.int32), 0, px - 1)
    cell = row * px + col

    nearest = np.full(px * px, -np.inf, np.float32)
    np.maximum.at(nearest, cell, depth)
    covered = np.isfinite(nearest).reshape(px, px).astype(np.float32)

    if n is None:
        # No normals (a point cloud): light the surface the depths describe.
        z = np.where(np.isfinite(nearest), nearest, 0).reshape(px, px).astype(np.float32)
        blur = (5, 5)
        smooth = cv2.GaussianBlur(z * covered, blur, 1.2) / np.maximum(cv2.GaussianBlur(covered, blur, 1.2), 1e-3)
        dy, dx = np.gradient(smooth)
        cliff = 0.06 / scale * px
        edge = (np.abs(dx) > cliff) | (np.abs(dy) > cliff)          # silhouettes, not slopes
        nx, ny = np.where(edge, 0, -dx * scale), np.where(edge, 0, dy * scale)
        inv = 1.0 / np.sqrt(nx * nx + ny * ny + 1)
        field = np.dstack([nx * inv, ny * inv, inv])
    else:
        nx, ny, nz = turn(n)
        # Everything within about two pixels of the front surface contributes, so
        # sculpted detail finer than a pixel averages out instead of showing as grain.
        front = depth >= nearest[cell] - 2.0 / scale
        facing = np.where(nz < 0, -1.0, 1.0)         # a back-facing triangle is lit as if it faced us
        cells = cell[front]
        field = np.stack([np.bincount(cells, weights=(c * facing)[front], minlength=px * px)
                          for c in (nx, ny, nz)], 1).reshape(px, px, 3).astype(np.float32)
        blur = (5, 5)
        density = np.maximum(cv2.GaussianBlur(covered, blur, 1.0), 1e-3)
        field = np.dstack([cv2.GaussianBlur(field[..., i] * covered, blur, 1.0) / density for i in range(3)])
        field /= np.maximum(np.linalg.norm(field, axis=2, keepdims=True), 1e-6)

    light = np.array([-0.45, 0.6, 0.66], np.float32)
    light /= np.linalg.norm(light)
    lit = 0.28 + 0.72 * np.clip(field @ light, 0, 1) + 0.14 * (1 - field[..., 2]) ** 2
    colour = np.clip(lit[..., None] * np.array([214, 196, 184], np.float32), 0, 255).astype(np.uint8)
    solid = cv2.dilate(covered, np.ones((3, 3), np.uint8)) > 0     # close the gaps between splats
    return np.dstack([colour, (solid * 255).astype(np.uint8)])


def _png(points, normals, up, px) -> bytes:
    import cv2
    ok, buffer = cv2.imencode(".png", _shade(points, normals, up, px))
    if not ok:
        raise Unreadable("The picture could not be drawn.")
    return buffer.tobytes()


# --------------------------------------------------------------------------- the cache
def cache_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    folder = os.path.join(base, "Meshwright", "previews")
    os.makedirs(folder, exist_ok=True)
    return folder


def _key(path: str, stat, px: int) -> str:
    raw = f"{os.path.normcase(os.path.abspath(path))}|{stat.st_size}|{int(stat.st_mtime)}|{px}|1"
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()


def _data_url(picture: bytes) -> str:
    """A picture drawn here is a PNG; one taken out of a 3MF may be a JPEG."""
    import base64
    kind = "image/png" if picture[:4] == b"\x89PNG" else "image/jpeg"
    return f"data:{kind};base64," + base64.b64encode(picture).decode("ascii")


def _prune(folder: str):
    try:
        entries = [e for e in os.scandir(folder) if e.is_file()]
    except OSError:
        return
    if len(entries) <= CACHE_KEEP:
        return
    entries.sort(key=lambda e: e.stat().st_mtime)
    for entry in entries[: len(entries) - CACHE_KEEP]:
        try:
            os.remove(entry.path)
        except OSError:
            pass


# --------------------------------------------------------------------------- what the UI asks for
def _dimensions(bounds, exact):
    """
    Sizes in millimetres, which is what the rest of Meshwright calls them.

    No mesh format records its unit, so millimetres is a convention, not a fact.
    The analysis panel treats a model as suspicious when its largest side is under
    1 mm or over a metre, and says the file may use different units; the same test
    is made here, so the two never disagree about the same model.
    """
    if bounds is None:
        return None
    low, high = np.asarray(bounds[0], float), np.asarray(bounds[1], float)
    span = np.where(np.isfinite(high - low), high - low, 0.0)
    largest = float(span.max()) if len(span) else 0.0
    return {"x": round(float(span[0]), 3), "y": round(float(span[1]), 3),
            "z": round(float(span[2]), 3), "unit": "mm", "approx": not exact,
            "odd_scale": bool(largest and (largest < 1.0 or largest > 1000.0))}


def look(path: str, px: int = PREVIEW_PX) -> dict:
    """
    Everything the browser shows about one file: its facts and, when it can be had
    cheaply, a picture. Never raises: trouble comes back as `note`, in a sentence
    meant for the person reading it.
    """
    result = {"path": path, "name": os.path.basename(path), "ext": os.path.splitext(path)[1].lower(),
              "picture": None, "note": None, "faces": None, "vertices": None,
              "dimensions": None, "textures": None, "format": None,
              "size_bytes": None, "size_text": None, "modified": None, "load_estimate": None}
    try:
        stat = os.stat(path)
    except OSError:
        result["note"] = "This file is no longer there."
        return result
    result["size_bytes"] = stat.st_size
    result["size_text"] = size_text(stat.st_size)
    result["modified"] = time.strftime("%d %b %Y, %H:%M", time.localtime(stat.st_mtime))

    ext = result["ext"]
    if ext in DESCRIBE_ONLY:
        result["format"] = DESCRIBE_ONLY[ext]
        result["note"] = "Its picture appears once it is open."
        return result
    if ext not in READERS:
        result["format"] = (ext[1:].upper() or "File")
        result["note"] = "Meshwright cannot open this kind of file."
        return result
    if not stat.st_size:
        result["format"] = ext[1:].upper()
        result["note"] = "This file is empty."
        return result
    if is_cloud_only(path):
        result["format"] = ext[1:].upper()
        result["note"] = "This file is kept in the cloud. Open it to download it."
        return result

    folder = cache_dir()
    key = _key(path, stat, px)
    facts_file = os.path.join(folder, key + ".json")
    picture_file = os.path.join(folder, key + ".png")
    try:
        with open(facts_file, "r", encoding="utf-8") as f:
            cached = json.load(f)
        if os.path.isfile(picture_file):
            with open(picture_file, "rb") as f:
                cached["picture"] = _data_url(f.read())
        cached.update(path=path, name=result["name"], ext=ext)
        os.utime(facts_file, None)             # a picture still in use is not the one pruned
        return cached
    except (OSError, ValueError):
        pass

    budget = SAMPLE if px >= PREVIEW_PX else max(40_000, SAMPLE // 8)
    picture = None
    try:
        facts, sample = READERS[ext](path, stat.st_size, budget)
        result["format"] = facts.get("format") or ext[1:].upper()
        result["faces"] = int(facts.get("faces") or 0) or None
        result["vertices"] = int(facts.get("vertices") or 0) or None
        result["textures"] = facts.get("textures")
        result["dimensions"] = _dimensions(facts.get("bounds"), facts.get("bounds_exact", True))
        result["note"] = facts.get("note")
        if result["faces"]:
            result["load_estimate"] = describe_duration(estimate_seconds("load", result["faces"]))
        if sample is not None:
            if isinstance(sample[0], str):                      # a picture the file carries itself
                picture = sample[1]
            else:
                points, normals = sample
                up = facts.get("up") or ("y" if ext in Y_UP_FORMATS else "z")
                picture = _png(points, normals, up, px)
    except Unreadable as trouble:
        result["note"] = str(trouble)
    except MemoryError:
        result["note"] = "This file is too large to look inside without opening it."
    except Exception:
        result["note"] = "This file could not be read without opening it."

    try:
        keep = {k: v for k, v in result.items() if k != "picture"}
        with open(facts_file, "w", encoding="utf-8") as f:
            json.dump(keep, f)
        if picture:
            with open(picture_file, "wb") as f:
                f.write(picture)
        _prune(folder)
    except OSError:
        pass

    if picture:
        result["picture"] = _data_url(picture)
    return result
