import trimesh

PRINTER_PROFILES = {
    "bambu_x1c": {
        "name": "Bambu Lab X1-Carbon / P1P",
        "build_volume_mm": [256, 256, 256],
        "technology": "FDM",
        "nozzle_default_mm": 0.4
    },
    "prusa_mk4": {
        "name": "Original Prusa MK4",
        "build_volume_mm": [250, 210, 220],
        "technology": "FDM",
        "nozzle_default_mm": 0.4
    },
    "ender_3": {
        "name": "Creality Ender 3 / V2 / S1",
        "build_volume_mm": [220, 220, 250],
        "technology": "FDM",
        "nozzle_default_mm": 0.4
    },
    "elegoo_saturn": {
        "name": "Elegoo Saturn 3 Ultra (SLA)",
        "build_volume_mm": [218, 123, 250],
        "technology": "SLA/Resin",
        "nozzle_default_mm": 0.05
    }
}


def check_build_volume(mesh: trimesh.Trimesh, printer_key: str = "bambu_x1c") -> dict:
    """
    Checks whether the mesh fits inside the selected 3D printer build volume.
    """
    profile = PRINTER_PROFILES.get(printer_key, PRINTER_PROFILES["bambu_x1c"])
    volume_max = profile["build_volume_mm"]
    mesh_extents = mesh.extents.tolist() if mesh.extents is not None else [0, 0, 0]

    fits = True
    exceeded_axes = []

    for idx, axis_name in enumerate(['X', 'Y', 'Z']):
        if mesh_extents[idx] > volume_max[idx]:
            fits = False
            exceeded_axes.append(f"{axis_name} ({round(mesh_extents[idx], 1)}mm > {volume_max[idx]}mm)")

    return {
        "printer_name": profile["name"],
        "build_volume_mm": volume_max,
        "mesh_dimensions_mm": [round(x, 2) for x in mesh_extents],
        "fits_in_build_volume": fits,
        "exceeded_axes": exceeded_axes
    }
