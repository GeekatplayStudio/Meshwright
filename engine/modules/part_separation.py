import trimesh


def separate_disconnected_shells(mesh: trimesh.Trimesh) -> list[trimesh.Trimesh]:
    """
    Separates a multi-body mesh into individual connected sub-meshes (shells/parts).
    Useful when a single OBJ/FBX file contains multiple separate 3D objects.
    """
    sub_meshes = mesh.split(only_watertight=False)
    return sub_meshes if isinstance(sub_meshes, (list, tuple, range)) else [sub_meshes]


def slice_mesh_plane(mesh: trimesh.Trimesh, plane_origin=(0, 0, 0), plane_normal=(0, 0, 1)) -> tuple[trimesh.Trimesh, trimesh.Trimesh]:
    """
    Cuts a mesh in half along a cutting plane.
    Returns (mesh_above, mesh_below).
    """
    slice_above = trimesh.intersections.slice_mesh_plane(mesh=mesh, plane_origin=plane_origin, plane_normal=plane_normal)
    slice_below = trimesh.intersections.slice_mesh_plane(mesh=mesh, plane_origin=plane_origin, plane_normal=[-n for n in plane_normal])
    return slice_above, slice_below
