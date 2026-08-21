import os
import numpy as np
import trimesh

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


def load_model(file_path: str, log=None) -> tuple[trimesh.Trimesh, dict]:
    """
    Loads any 3D model (OBJ, FBX, GLB, GLTF, STL, PLY, 3DS, DAE, 3MF, OFF, etc.)
    and returns a normalized trimesh.Trimesh object along with metadata statistics.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    ext = os.path.splitext(file_path)[1].lower()
    mesh = None
    log = log or (lambda m: None)
    log(f"Reading {os.path.basename(file_path)} ({os.path.getsize(file_path) / 1e6:.1f} MB)")

    # Strategy 1: Standard Trimesh loader (handles STL, OBJ, GLB, GLTF, PLY, 3MF, DAE, OFF, etc.)
    try:
        log("Parsing with trimesh")
        loaded = trimesh.load(file_path, force='mesh', skip_materials=True, process=False)
        if isinstance(loaded, trimesh.Scene):
            # Combine scene geometries into a single mesh
            geometries = list(loaded.geometry.values())
            if len(geometries) > 0:
                mesh = trimesh.util.concatenate(geometries)
            else:
                raise ValueError("Scene contains no 3D geometry.")
        elif isinstance(loaded, trimesh.Trimesh):
            mesh = loaded
    except Exception:
        mesh = None

    # Strategy 2: If FBX or Trimesh failed, try ufbx for FBX files
    if mesh is None and ext in ['.fbx'] and HAS_UFBX:
        try:
            log("Parsing FBX with ufbx")
            opts = ufbx.LoadOpts()
            ufbx_scene = ufbx.load_file(file_path, opts)
            vertices = []
            faces = []
            v_offset = 0

            for u_mesh in ufbx_scene.meshes:
                # Position array
                mesh_verts = [ (u_mesh.vertices[i].x, u_mesh.vertices[i].y, u_mesh.vertices[i].z) 
                               for i in range(len(u_mesh.vertices)) ]
                vertices.extend(mesh_verts)

                # Triangulate polygons
                for poly in u_mesh.faces:
                    p_start = poly.index_begin
                    p_num = poly.num_indices
                    for t in range(1, p_num - 1):
                        i0 = u_mesh.vertex_indices[p_start] + v_offset
                        i1 = u_mesh.vertex_indices[p_start + t] + v_offset
                        i2 = u_mesh.vertex_indices[p_start + t + 1] + v_offset
                        faces.append([i0, i1, i2])
                v_offset += len(mesh_verts)

            if len(vertices) > 0 and len(faces) > 0:
                mesh = trimesh.Trimesh(vertices=np.array(vertices), faces=np.array(faces), process=True)
        except Exception:
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
        except Exception:
            pass

    if mesh is None:
        raise RuntimeError(f"Could not load 3D file '{os.path.basename(file_path)}'. Format extension '{ext}' may be corrupted or unsupported.")

    log(f"Geometry: {len(mesh.faces):,} faces, {len(mesh.vertices):,} vertices")
    log("Merging duplicate vertices")
    mesh.merge_vertices()
    mesh.update_faces(mesh.nondegenerate_faces())
    mesh.update_faces(mesh.unique_faces())
    mesh.remove_unreferenced_vertices()

    stats = get_mesh_stats(mesh, file_path)
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
        "vertex_count": int(len(mesh.vertices)),
        "face_count": int(len(mesh.faces)),
        "is_watertight": is_watertight,
        "is_winding_consistent": is_winding_consistent,
        "is_manifold": is_watertight and is_winding_consistent,
        "bounds_min": [round(x, 2) for x in bounds[0]],
        "bounds_max": [round(x, 2) for x in bounds[1]],
        "dimensions_mm": [round(x, 2) for x in extents],
        "volume_cm3": round(volume_cm3, 3),
        "surface_area_cm2": round(area_cm2, 2)
    }
