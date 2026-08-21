import os
import sys
import trimesh

# Ensure root folder is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.model_loader import load_model, get_mesh_stats
from engine.mesh_repair import repair_mesh
from engine.mesh_reducer import reduce_mesh
from engine.stl_exporter import export_to_stl
from engine.modules.printer_profiles import check_build_volume
from engine.modules.color_printing import extract_color_information
from engine.modules.part_separation import separate_disconnected_shells


def run_tests():
    print("=== STARTING 3D MESH ENGINE AUTOMATED VERIFICATION TESTS ===")

    test_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_models")
    os.makedirs(test_dir, exist_ok=True)

    # 1. Create a synthetic non-manifold / open mesh object for testing
    box = trimesh.creation.box(extents=[50, 40, 30])
    # Introduce a broken hole by removing 2 faces
    faces_broken = box.faces[:-4]
    broken_mesh = trimesh.Trimesh(vertices=box.vertices, faces=faces_broken, process=False)
    
    obj_path = os.path.join(test_dir, "test_broken_box.obj")
    broken_mesh.export(obj_path)
    print(f"[TEST 1] Created broken test OBJ model at: {obj_path}")

    # 2. Test Model Loader
    mesh, stats = load_model(obj_path)
    print(f"[TEST 2] Loaded OBJ statistics: Vertices={stats['vertex_count']}, Faces={stats['face_count']}, Watertight={stats['is_watertight']}")
    assert stats['face_count'] > 0, "Face count must be > 0"

    # 3. Test Auto-Fix Mesh Repair Pipeline
    repaired_mesh, report = repair_mesh(mesh, strict_watertight=True)
    repaired_stats = get_mesh_stats(repaired_mesh, "repaired.obj")
    print(f"[TEST 3] Mesh Repair Complete. Final Vertices={repaired_stats['vertex_count']}, Faces={repaired_stats['face_count']}, Watertight={repaired_stats['is_watertight']}")
    print(f"         Repair Steps: {report['steps_applied']}")
    assert repaired_stats['is_watertight'], "Repaired mesh MUST be 100% watertight!"

    # 4. Test Quadric Edge Collapse Mesh Reduction
    # Create higher poly sphere to test decimation
    icosphere = trimesh.creation.icosphere(subdivisions=4, radius=25)
    high_poly_path = os.path.join(test_dir, "test_high_poly.stl")
    icosphere.export(high_poly_path)

    hp_mesh, _ = load_model(high_poly_path)
    initial_faces = len(hp_mesh.faces)
    reduced_mesh, red_info = reduce_mesh(hp_mesh, target_factor=0.5)
    print(f"[TEST 4] Decimation Test: Initial Faces={red_info['initial_faces']} -> Final Faces={red_info['final_faces']} ({red_info['reduction_percentage']}% reduced via {red_info['method_used']})")
    assert red_info['final_faces'] < initial_faces, "Final faces should be reduced"

    # 5. Test Print-Ready STL Export
    out_stl_path = os.path.join(test_dir, "export_print_ready.stl")
    export_info = export_to_stl(repaired_mesh, out_stl_path, scale_unit="mm", align_origin=True)
    print(f"[TEST 5] Exported Print-Ready STL: Size={export_info['file_size_mb']} MB, Dimensions={export_info['dimensions_mm']} mm, Bounds Min Z={export_info['bounds_min'][2]}")
    assert os.path.exists(out_stl_path), "Exported STL file must exist"
    assert export_info['bounds_min'][2] == 0.0, "Lowest Z bound must align flat to Z=0 ground plane!"

    # 6. Test Extension Modules
    build_check = check_build_volume(repaired_mesh, "bambu_x1c")
    print(f"[TEST 6] Printer Build Volume Check (Bambu X1C): Fits={build_check['fits_in_build_volume']}")

    color_check = extract_color_information(repaired_mesh)
    print(f"         Color Information Check: Color Mode={color_check['color_mode']}")

    shells = separate_disconnected_shells(repaired_mesh)
    print(f"         Disconnected Shells Check: Count={len(shells)}")

    print("\nALL VERIFICATION TESTS PASSED SUCCESSFULLY!")


if __name__ == "__main__":
    run_tests()
