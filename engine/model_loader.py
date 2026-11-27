import os

import numpy as np
import trimesh

from engine import axes
from engine.indexing import duplicate_mask
from engine.texture.companion_detector import (
    decode_fbx_textures,
    extract_embedded_textures,
    fbx_texture_blobs,
)
from engine.texture.uv_channel import from_vertex_uv


def _as_array(index_list, count: int) -> np.ndarray:
    """
    A ufbx index list as a numpy array.

    Uint32List exposes only __len__ and __getitem__ — no buffer protocol and no
    slicing — so numpy has to pull the values out one at a time whichever way we
    ask. fromiter does it in a single pass with the length known up front, which is
    measurably quicker than np.array on the fifteen million indices a dense model
    carries.
    """
    return np.fromiter(index_list, dtype=np.uint32, count=count)

try:
    import pymeshlab as ml
    HAS_PYMESHLAB = True
except ImportError:
    HAS_PYMESHLAB = False

try:
    import ufbx
    HAS_UFBX = True
except ImportError:
    HAS_UFBX = False


def _fan_triangulate(u_mesh, pin=None) -> np.ndarray:
    """
    Corner positions of the triangles a fan-triangulated FBX mesh produces.

    Returns a flat array indexing into the mesh's own index buffers, so the caller
    can apply it to positions and UVs alike. A polygon of n corners starting at b
    becomes n-2 triangles (b, b+1, b+2), (b, b+2, b+3), …

    Nearly every FBX in the wild is uniformly triangles or uniformly quads, so the
    common cases are built with array arithmetic; only a mesh that genuinely mixes
    polygon sizes falls back to per-face work, and even then it is grouped by size
    rather than iterated corner by corner.
    """
    pin = pin if pin is not None else (lambda obj: obj)
    polygons = pin(u_mesh.faces)        # one list for both passes; see _load_fbx
    begins = np.fromiter((f.index_begin for f in polygons), dtype=np.int64,
                         count=u_mesh.num_faces)
    sizes = np.fromiter((f.num_indices for f in polygons), dtype=np.int64,
                        count=u_mesh.num_faces)

    usable = sizes >= 3                      # points and lines carry no surface
    begins, sizes = begins[usable], sizes[usable]
    if len(sizes) == 0:
        return np.zeros(0, dtype=np.int64)

    per_face = sizes - 2                     # triangles each polygon becomes
    first_tri = np.concatenate([[0], np.cumsum(per_face)[:-1]])
    corners = np.empty(int(per_face.sum()) * 3, dtype=np.int64)

    for size in np.unique(sizes):
        group = sizes == size
        step = np.arange(size - 2, dtype=np.int64)
        # One polygon's fan, as offsets from its first corner: (size-2, 3).
        fan = np.stack([np.zeros_like(step), step + 1, step + 2], axis=1)

        source = (begins[group][:, None, None] + fan[None]).reshape(-1)
        # Scatter rather than concatenate, so triangles stay in face order even when
        # a mesh mixes polygon sizes.
        target = (first_tri[group][:, None] + step[None]) * 3
        target = (target[:, :, None] + np.arange(3)).reshape(-1)
        corners[target] = source
    return corners


# How long one FBX may take to parse in the helper before it is given up on. A
# quarter-gigabyte scan export parses in well under a minute; ten is for a slow disk.
FBX_TIMEOUT_S = 600

_FBX_WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_fbx_worker.py")


class FbxReaderCrashed(RuntimeError):
    """The FBX reader died on this file — in its own process, so Meshwright did not."""


def _load_fbx(file_path: str) -> tuple[trimesh.Trimesh | None, dict, str | None]:
    """
    Parse an FBX in a helper process, and hand back what _read_fbx would have.

    ufbx is native code, and its Python binding does not always survive its own
    cleanup: on a large scene, freeing it faults a few times in ten no matter what
    order things are released in (see _read_fbx). In this process that would close
    the Meshwright window, and the file browser parses FBX files merely to draw their
    picture — so looking into a folder could take the whole program down, with
    whatever was open in it. The helper reads the file, writes plain arrays, and
    leaves without freeing anything, so the fault never gets the chance to happen;
    and if some other file crashes it outright, that is an exit code here and a
    message on screen rather than a closed window. QuadriFlow and xatlas run the
    same way for the same reason.

    If no child process can be started at all, the file is read here instead.
    """
    import subprocess
    import tempfile

    from engine.runtime import subprocess_flags, worker_argv

    work = tempfile.mkdtemp(prefix="meshwright-fbx-")
    out_path = os.path.join(work, "out.npz")
    try:
        try:
            proc = subprocess.run(worker_argv("fbx", _FBX_WORKER, os.path.abspath(file_path), out_path),
                                  capture_output=True, timeout=FBX_TIMEOUT_S, check=False,
                                  **subprocess_flags())
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"Reading this FBX took longer than {FBX_TIMEOUT_S // 60} minutes and was "
                "stopped. Re-export it as GLB or OBJ and open that instead.") from None
        except (OSError, ValueError):
            return _read_fbx(file_path)

        if proc.returncode != 0 or not os.path.exists(out_path):
            detail = (proc.stderr or b"").decode("utf-8", "replace").strip().splitlines()
            if proc.returncode in (1, 2) and detail:
                raise RuntimeError(detail[-1])      # an ordinary Python error, reported as one
            raise FbxReaderCrashed(
                "The FBX reader crashed on this file. Meshwright is fine; the file is not "
                "opened. Re-export it from the program that made it — as GLB or OBJ if "
                "that program offers them — and open that instead.")
        return _unpack_fbx(out_path)
    finally:
        for name in os.listdir(work) if os.path.isdir(work) else ():
            try:
                os.remove(os.path.join(work, name))
            except OSError:
                pass
        try:
            os.rmdir(work)
        except OSError:
            pass


def _pack_fbx(result, out_path: str) -> None:
    """Write what _read_fbx returned as plain arrays, for the parent to read back."""
    mesh, blobs, declared = result
    arrays = {"declared": np.array(declared or ""), "has_mesh": np.array(mesh is not None)}
    if mesh is not None:
        arrays["vertices"] = np.asarray(mesh.vertices, dtype=np.float64)
        arrays["faces"] = np.asarray(mesh.faces, dtype=np.int64)
        uv = getattr(getattr(mesh, "visual", None), "uv", None)
        if uv is not None:
            arrays["uv"] = np.asarray(uv, dtype=np.float64)
    for channel, source in (blobs or {}).items():
        if channel == "__unused__":
            arrays["unused"] = np.array(int(source))
        elif isinstance(source, bytes):
            arrays["blob_" + channel] = np.frombuffer(source, dtype=np.uint8)
        else:
            arrays["path_" + channel] = np.array(str(source))
    np.savez(out_path, **arrays)


def _unpack_fbx(path: str) -> tuple[trimesh.Trimesh | None, dict, str | None]:
    """Read back what _pack_fbx wrote, in the same shape as _read_fbx's answer."""
    with np.load(path, allow_pickle=False) as data:
        declared = str(data["declared"]) or None
        mesh = None
        if bool(data["has_mesh"]):
            mesh = trimesh.Trimesh(vertices=data["vertices"], faces=data["faces"], process=False)
            if "uv" in data.files:
                mesh.visual = trimesh.visual.TextureVisuals(uv=data["uv"])
        blobs = {}
        for key in data.files:
            if key.startswith("blob_"):
                blobs[key[5:]] = data[key].tobytes()
            elif key.startswith("path_"):
                blobs[key[5:]] = str(data[key])
        if "unused" in data.files:
            blobs["__unused__"] = int(data["unused"])
    return mesh, blobs, declared


def _read_fbx(file_path: str, hold=None) -> tuple[trimesh.Trimesh | None, dict, str | None]:
    """
    Parse an FBX with ufbx, and get out of the scene's way. Runs in the helper; see
    _load_fbx for why not here.

    Returns (mesh, texture sources, declared up axis). Everything ufbx owns stays inside this function:
    the mesh is built from copies, and the textures come back as raw bytes or paths
    rather than decoded images.

    That last part is not tidiness. Allocating a large image while the ufbx scene is
    still alive corrupts its teardown, and the process dies with an access violation
    the moment the scene is freed — an 8192x8192 JPEG embedded in a 228 MB FBX does it
    every time, deterministically, and with no Python traceback to show for it.
    Copying the bytes out costs a few megabytes, and decoding once the scene has gone
    is reliable. Keeping the scene a local of this function is what guarantees that
    ordering, so it should not be hoisted back into load_model.

    Every ufbx object reached on the way is pinned — kept alive in one list until the
    scene is done with — and that is not tidiness either. The Python binding (ufbx
    0.0.5, the newest there is) frees something it still needs when a temporary
    wrapper is dropped: ask a freed material for its `.pbr` maps, or a freed mesh for
    its `.vertex_position`, and the next object of that kind it hands out points at
    released memory. `scene.materials[0].pbr` twice in a row is enough to kill the
    process. Two Tripo FBX exports did it in the file browser on every look, with no
    Python traceback and the whole window gone. Holding every wrapper means nothing
    is freed while the scene is in use; the cost is a few thousand small objects.

    Freeing the scene afterwards is still not safe: on the 258-mesh swamp scene it
    faulted in about one run in four, in every release order tried. So the helper
    passes `hold`, a list it keeps until it leaves with os._exit, and the scene and
    its wrappers go into that instead of being freed at all. Without `hold` — only
    when no helper could be started — they are released here and the risk is taken.
    """
    pinned = [] if hold is None else hold

    def pin(obj):
        pinned.append(obj)
        return obj

    scene = pin(ufbx.load_file(file_path))
    try:
        return _read_fbx_scene(scene, file_path, pin)
    finally:
        if hold is None:
            # Wrappers first, then the scene they point into.
            del pinned[1:]
            pinned.clear()
        del pin
        scene = None


def _read_fbx_scene(scene, file_path: str, pin) -> tuple[trimesh.Trimesh | None, dict, str | None]:
    """The body of _load_fbx: everything it reads goes through `pin` first."""
    mesh = None
    all_verts, all_faces, all_uvs = [], [], []
    v_offset = 0

    for u_m in pin(scene.meshes):
        pin(u_m)
        if u_m.num_faces == 0:
            continue
        positions = pin(u_m.vertex_position)
        uv_attr = pin(u_m.vertex_uv)
        pos_vals = np.frombuffer(pin(positions.values), dtype=np.float64).reshape(-1, 3)
        has_uv = uv_attr.exists and len(pin(uv_attr.values)) > 0
        v_idx = _as_array(pin(positions.indices), u_m.num_indices)
        uv_idx = _as_array(pin(uv_attr.indices), u_m.num_indices) if has_uv else None

        # FBX polygons are not necessarily triangles. Fan-triangulate them, which is
        # what an exporter would have done, rather than reshaping blindly and raising
        # on the first quad.
        if u_m.num_indices == u_m.num_triangles * 3:
            corners = None                      # already triangles
        else:
            corners = _fan_triangulate(u_m, pin)

        tri_v_idx = v_idx if corners is None else v_idx[corners]
        tri_uv_idx = None
        if has_uv:
            tri_uv_idx = uv_idx if corners is None else uv_idx[corners]

        if has_uv and tri_uv_idx is not None:
            uv_vals = np.frombuffer(uv_attr.values, dtype=np.float64).reshape(-1, 2)
            keys = (tri_v_idx.astype(np.uint64) << 32) | tri_uv_idx.astype(np.uint64)
            uniq_keys, inv = np.unique(keys, return_inverse=True)

            sub_v_idx = (uniq_keys >> 32).astype(np.int64)
            sub_uv_idx = (uniq_keys & 0xFFFFFFFF).astype(np.int64)

            all_verts.append(pos_vals[sub_v_idx])
            all_uvs.append(uv_vals[sub_uv_idx])
            all_faces.append(inv.reshape(-1, 3) + v_offset)
            v_offset += len(sub_v_idx)
        else:
            all_verts.append(pos_vals)
            all_faces.append(tri_v_idx.reshape(-1, 3) + v_offset)
            v_offset += len(pos_vals)

    if all_verts:
        # vstack copies, so nothing handed back still points into ufbx's memory.
        mesh = trimesh.Trimesh(vertices=np.vstack(all_verts), faces=np.vstack(all_faces),
                               process=False)
        if all_uvs:
            mesh.visual = trimesh.visual.TextureVisuals(uv=np.vstack(all_uvs))

    # An FBX states which way is up in its own header; the caller stands the model
    # up with it. It is read here because only here is the scene still alive, and it
    # is returned rather than applied so that everything that opens an FBX — the file
    # browser included — turns it the same way, once.
    declared = {"POSITIVE_X": "x", "NEGATIVE_X": "x", "POSITIVE_Y": "y", "NEGATIVE_Y": "y",
                "POSITIVE_Z": "z", "NEGATIVE_Z": "z"}.get(pin(pin(pin(scene.settings).axes).up).name)

    return mesh, (fbx_texture_blobs(scene, file_path, pin) if mesh is not None else {}), declared


def load_model(file_path: str, log=None, with_stats: bool = True,
               keep=None) -> tuple[trimesh.Trimesh, dict]:
    """
    Loads any 3D model (BLEND, OBJ, FBX, GLB, GLTF, STL, PLY, 3DS, DAE, 3MF, OFF, etc.)
    and returns a normalized trimesh.Trimesh object along with metadata statistics.

    `with_stats=False` skips the summary, which costs a watertightness, volume and
    area pass over the whole mesh — seconds on a multi-million-face model, and
    wasted on a caller that is about to run the full diagnostics anyway.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    ext = os.path.splitext(file_path)[1].lower()
    mesh = None
    fbx_maps = {}                       # what the FBX declares on its own materials
    fbx_blobs = {}                      # ...before it has been decoded, see _load_fbx
    declared_up = None                  # which way the file itself says is up, if it says
    fbx_error = None                    # why ufbx could not read it, for the final message
    # Every log call in here passes a level, so the stand-in has to accept one too —
    # otherwise load_model() without a logger dies on the first message it writes.
    log = log or (lambda *a, **k: None)
    log(f"Reading {os.path.basename(file_path)} ({os.path.getsize(file_path) / 1e6:.1f} MB)")

    if ext == ".blend":
        from engine.blend_import import load_blend

        # The intermediate GLB has already passed through normalization and UV
        # extraction. Keep that metadata and report the original project name.
        mesh = load_blend(file_path, log, keep=keep)
        return mesh, get_mesh_stats(mesh, file_path) if with_stats else {}

    # Strategy 1: For FBX files, use fast native ufbx parser with full UV support
    if ext == ".fbx" and HAS_UFBX:
        try:
            log("Parsing FBX with ufbx (fast native)")
            mesh, fbx_blobs, declared_up = _load_fbx(file_path)
        except Exception as e:
            log(f"ufbx parser error: {e}", "warn")
            mesh, fbx_blobs, declared_up = None, {}, None
            fbx_error = e

        # The scene is out of scope now, and only here is it safe to turn what it
        # declared into images (see _load_fbx). Nothing else in the pipeline would
        # ever see them: trimesh cannot open FBX at all, and a file that embeds its
        # artwork has no companion images beside it for the folder scan to find.
        if fbx_blobs:
            fbx_maps = decode_fbx_textures(fbx_blobs, log)

    # Only the objects the person kept, each still where the file put it.
    #
    # Deliberately outside the fallback chain below: a chosen set that cannot be
    # honoured has to fail and say so. Letting it fall through to the loaders that
    # cannot filter would quietly hand back the whole scene — the studio floor and
    # all — which is the exact thing the choosing was for.
    if mesh is None and keep is not None:
        from engine.scene_parts import assemble
        mesh = assemble(file_path, keep)

    # Strategy 2: Standard Trimesh loader (handles STL, OBJ, GLB, GLTF, PLY, 3MF, DAE, OFF, etc.)
    if mesh is None:
        try:
            log("Parsing with trimesh")
            loaded = trimesh.load(file_path, force='mesh', skip_materials=False, process=False)
            if isinstance(loaded, trimesh.Scene):
                geometries = list(loaded.geometry.values())
                if len(geometries) > 0:
                    mesh = trimesh.util.concatenate(geometries)
                else:
                    raise ValueError("Scene contains no 3D geometry.")
            elif isinstance(loaded, trimesh.Trimesh):
                mesh = loaded
        except Exception as e:
            log(f"trimesh loader failed: {e}", "warn")
            mesh = None

    # Strategy 3: Try PyMeshLab
    if mesh is None and HAS_PYMESHLAB:
        try:
            log("Parsing with MeshLab")
            ms = ml.MeshSet()
            ms.load_new_mesh(file_path)
            current = ms.current_mesh()
            v = current.vertex_matrix()
            f = current.face_matrix()
            mesh = trimesh.Trimesh(vertices=v, faces=f, process=True)
        except Exception as e:
            log(f"PyMeshLab loader failed: {e}", "warn")
            mesh = None

    if mesh is None:
        if fbx_error is not None:
            # MeshLab reads some FBX too, but when it also fails, ufbx's reason is the
            # one that says what happened — "corrupted or unsupported" would send
            # someone looking in the wrong place.
            raise RuntimeError(f"Could not open '{os.path.basename(file_path)}': {fbx_error}")
        raise RuntimeError(f"Could not load 3D file '{os.path.basename(file_path)}'. Format extension '{ext}' may be corrupted or unsupported.")

    log(f"Geometry: {len(mesh.faces):,} faces, {len(mesh.vertices):,} vertices")

    # Stand the model up if its own format says which way that is. Meshwright works
    # in Z-up throughout — viewport, build plate, reported height — and glTF, GLB and
    # FBX do not. See engine/axes.py for why nothing else is touched.
    if axes.needs_standing_up(ext, declared_up):
        axes.to_z_up(mesh)
        log(f"Turned upright: {ext[1:].upper()} files are Y-up, Meshwright works in Z-up")

    # Texture data is lifted off the mesh before any welding happens, because both
    # pieces are about to be invalidated by it: UVs move to a per-corner array (which
    # survives vertex merging untouched), and the material's images are cached for
    # the texture engine. What stays behind is pure geometry.
    corner_uv = from_vertex_uv(mesh.faces, getattr(getattr(mesh, "visual", None), "uv", None))
    embedded = extract_embedded_textures(mesh)
    # What the FBX itself declares outranks anything read off the trimesh visual. The
    # visual built for an FBX is a bare TextureVisuals carrying UVs, whose default
    # material reports a 2x2 stub as its image — and a stub that arrives first would
    # otherwise keep the real map out, then be discarded as a placeholder further
    # down, leaving a fully textured model looking as though it had none.
    embedded.update(fbx_maps)
    mesh.visual = trimesh.visual.ColorVisuals()

    if corner_uv is not None:
        log(f"Carrying {len(corner_uv):,} face UVs beside the geometry, seams intact")

    # Now the mesh can be welded properly. Merging by position alone used to tear a
    # textured model into one shell per UV chart; with UVs held per corner it is
    # simply the right thing to do.
    mesh.merge_vertices()

    # Dropping faces has to happen one pass at a time, and each mask has to be
    # measured against the faces that are actually there when it is applied. Building
    # both up front looks tidier and is wrong: the first pass shortens the array, so
    # the second mask is then too long by however many faces the first one removed.
    def drop(keep):
        """Apply a keep-mask to the faces and to the UV channel together."""
        nonlocal corner_uv
        keep = np.asarray(keep)
        if keep.dtype == bool and keep.all():
            return
        if corner_uv is not None:
            corner_uv = corner_uv[keep]
        mesh.update_faces(keep)

    drop(mesh.nondegenerate_faces())
    # trimesh's own unique_faces() hashes each row; packing them into one integer
    # each answers the same question and is quicker on a dense mesh.
    drop(~duplicate_mask(np.sort(mesh.faces, axis=1)))
    mesh.remove_unreferenced_vertices()

    if corner_uv is not None and len(corner_uv) != len(mesh.faces):
        log("UV channel no longer matches the geometry after cleanup; dropping it", "warn")
        corner_uv = None

    # Handed over in metadata so every existing caller of load_model() keeps working.
    mesh.metadata["corner_uv"] = corner_uv
    mesh.metadata["embedded_textures"] = embedded

    stats = get_mesh_stats(mesh, file_path) if with_stats else {}
    return mesh, stats


def get_mesh_stats(mesh: trimesh.Trimesh, file_path: str = "") -> dict:
    """
    Computes comprehensive structural and geometric metadata for the mesh.
    """
    is_watertight = bool(mesh.is_watertight)
    is_winding_consistent = bool(mesh.is_winding_consistent)
    
    # Calculate volume (cm³) and surface area (cm²) assuming unit is mm
    # If dimensions are in mm, 1 mm³ = 0.001 cm³, 1 mm² = 0.01 cm²
    volume_mm3 = abs(mesh.volume) if is_watertight else 0.0
    volume_cm3 = volume_mm3 / 1000.0
    area_mm2 = float(mesh.area)
    area_cm2 = area_mm2 / 100.0

    bounds = mesh.bounds.tolist() if mesh.bounds is not None else [[0, 0, 0], [0, 0, 0]]
    extents = mesh.extents.tolist() if mesh.extents is not None else [0, 0, 0]

    return {
        "filename": os.path.basename(file_path) if file_path else "model.stl",
        "file_path": file_path,
        "vertex_count": len(mesh.vertices),
        "face_count": len(mesh.faces),
        "is_watertight": is_watertight,
        "is_winding_consistent": is_winding_consistent,
        "is_manifold": is_watertight and is_winding_consistent,
        "bounds_min": [round(x, 2) for x in bounds[0]],
        "bounds_max": [round(x, 2) for x in bounds[1]],
        "dimensions_mm": [round(x, 2) for x in extents],
        "volume_cm3": round(volume_cm3, 3),
        "surface_area_cm2": round(area_cm2, 2)
    }
