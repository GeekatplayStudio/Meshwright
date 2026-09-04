import os

import numpy as np
import trimesh

from engine.indexing import duplicate_mask
from engine.texture.companion_detector import extract_embedded_textures
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


def _fan_triangulate(u_mesh) -> np.ndarray:
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
    begins = np.fromiter((f.index_begin for f in u_mesh.faces), dtype=np.int64,
                         count=u_mesh.num_faces)
    sizes = np.fromiter((f.num_indices for f in u_mesh.faces), dtype=np.int64,
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


def load_model(file_path: str, log=None, with_stats: bool = True) -> tuple[trimesh.Trimesh, dict]:
    """
    Loads any 3D model (OBJ, FBX, GLB, GLTF, STL, PLY, 3DS, DAE, 3MF, OFF, etc.)
    and returns a normalized trimesh.Trimesh object along with metadata statistics.

    `with_stats=False` skips the summary, which costs a watertightness, volume and
    area pass over the whole mesh — seconds on a multi-million-face model, and
    wasted on a caller that is about to run the full diagnostics anyway.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    ext = os.path.splitext(file_path)[1].lower()
    mesh = None
    log = log or (lambda m: None)
    log(f"Reading {os.path.basename(file_path)} ({os.path.getsize(file_path) / 1e6:.1f} MB)")

    # Strategy 1: For FBX files, use fast native ufbx parser with full UV support
    if ext == ".fbx" and HAS_UFBX:
        try:
            log("Parsing FBX with ufbx (fast native)")
            scene = ufbx.load_file(file_path)
            all_verts, all_faces, all_uvs = [], [], []
            v_offset = 0

            for u_m in scene.meshes:
                if u_m.num_faces == 0:
                    continue
                pos_vals = np.frombuffer(u_m.vertex_position.values, dtype=np.float64).reshape(-1, 3)
                has_uv = u_m.vertex_uv.exists and len(u_m.vertex_uv.values) > 0
                v_idx = _as_array(u_m.vertex_position.indices, u_m.num_indices)
                uv_idx = _as_array(u_m.vertex_uv.indices, u_m.num_indices) if has_uv else None

                # FBX polygons are not necessarily triangles. Fan-triangulate them,
                # which is what an exporter would have done, rather than reshaping
                # blindly and raising on the first quad.
                if u_m.num_indices == u_m.num_triangles * 3:
                    corners = None                      # already triangles
                else:
                    corners = _fan_triangulate(u_m)

                tri_v_idx = v_idx if corners is None else v_idx[corners]
                tri_uv_idx = None
                if has_uv:
                    tri_uv_idx = uv_idx if corners is None else uv_idx[corners]

                if has_uv and tri_uv_idx is not None:
                    uv_vals = np.frombuffer(u_m.vertex_uv.values, dtype=np.float64).reshape(-1, 2)
                    keys = (tri_v_idx.astype(np.uint64) << 32) | tri_uv_idx.astype(np.uint64)
                    uniq_keys, inv = np.unique(keys, return_inverse=True)

                    sub_v_idx = (uniq_keys >> 32).astype(np.int64)
                    sub_uv_idx = (uniq_keys & 0xFFFFFFFF).astype(np.int64)

                    sub_verts = pos_vals[sub_v_idx]
                    sub_uvs = uv_vals[sub_uv_idx]
                    sub_faces = inv.reshape(-1, 3) + v_offset

                    all_verts.append(sub_verts)
                    all_uvs.append(sub_uvs)
                    all_faces.append(sub_faces)
                    v_offset += len(sub_verts)
                else:
                    sub_faces = tri_v_idx.reshape(-1, 3) + v_offset
                    all_verts.append(pos_vals)
                    all_faces.append(sub_faces)
                    v_offset += len(pos_vals)

            if all_verts:
                combined_v = np.vstack(all_verts)
                combined_f = np.vstack(all_faces)
                mesh = trimesh.Trimesh(vertices=combined_v, faces=combined_f, process=False)
                if all_uvs:
                    mesh.visual = trimesh.visual.TextureVisuals(uv=np.vstack(all_uvs))
        except Exception as e:
            log(f"ufbx parser error: {e}", "warn")
            mesh = None

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
        raise RuntimeError(f"Could not load 3D file '{os.path.basename(file_path)}'. Format extension '{ext}' may be corrupted or unsupported.")

    log(f"Geometry: {len(mesh.faces):,} faces, {len(mesh.vertices):,} vertices")

    # Texture data is lifted off the mesh before any welding happens, because both
    # pieces are about to be invalidated by it: UVs move to a per-corner array (which
    # survives vertex merging untouched), and the material's images are cached for
    # the texture engine. What stays behind is pure geometry.
    corner_uv = from_vertex_uv(mesh.faces, getattr(getattr(mesh, "visual", None), "uv", None))
    embedded = extract_embedded_textures(mesh)
    mesh.visual = trimesh.visual.ColorVisuals()

    if corner_uv is not None:
        log(f"Carrying {len(corner_uv):,} face UVs beside the geometry, seams intact")

    # Now the mesh can be welded properly. Merging by position alone used to tear a
    # textured model into one shell per UV chart; with UVs held per corner it is
    # simply the right thing to do.
    mesh.merge_vertices()

    # update_faces drops rows, so the UV array has to follow the same masks.
    # trimesh's own unique_faces() hashes each row; packing them into one integer
    # each answers the same question and is quicker on a dense mesh.
    for mask in (np.asarray(mesh.nondegenerate_faces()),
                 ~duplicate_mask(np.sort(mesh.faces, axis=1))):
        if corner_uv is not None:
            corner_uv = corner_uv[mask]
        mesh.update_faces(mask)
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
