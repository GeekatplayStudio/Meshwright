# Geekatplay 3D MeshFix — ComfyUI Custom Nodes

Custom nodes for **ComfyUI** integrating **Geekatplay Meshwright's** advanced 3D mesh analysis, staged repair, polygon reduction, and print preparation pipeline.

---

## Features

- **Meshwright Fix Mesh 🔧**:
  - Full staged repair pipeline: Degenerate triangle cleanup, face winding unification, normal inversion fix, small hole closing, MeshFix (Attene), MeshLab non-manifold repair, Manifold3D solid reconstruction, and voxel remesh fallback.
  - Generates detailed human-readable reports detailing every issue diagnosed before and every repair applied.
- **Meshwright Reduce Mesh 📉**:
  - High-performance decimation (Quadric Edge Collapse via `fast_simplification` or PyMeshLab) or Smart Retopology (QuadriFlow curvature-aligned quads or isotropic remeshing).
  - Preserves hard edges and surface topology.
- **Meshwright Load 3D Model 📂**:
  - Load OBJ, FBX, GLB, GLTF, STL, PLY, 3MF, DAE, or OFF files, or built-in test demo models.
- **Meshwright Compare & Diagnose 🔍**:
  - Compares before and after models, outputs metric deltas (open edges, holes, non-manifold edges, readiness score), and generates a side-by-side rendered visual comparison image tensor.
- **Meshwright Save 3D Mesh 💾**:
  - Exports models to binary STL, OBJ, GLB, PLY, 3MF, or OFF with unit scaling and automatic build-plate resting.
- **Meshwright 3D Preview 👁️**:
  - CPU-rasterized shaded visual preview compatible with standard ComfyUI `Preview Image` nodes.

---

## Installation

### Automatic (Recommended)
Run the Meshwright installer or in-app installer:
1. In Meshwright: click **Install ComfyUI Nodes** in the desktop application or About dialog.
2. Or run:
   ```cmd
   python scripts/install_comfyui_nodes.py
   ```
3. Or during `./install.bat`, answer **Y** when prompted to install ComfyUI nodes.

### Manual Installation
1. Copy this entire folder (`Geekatplay-3D-MeshFix`) into your ComfyUI custom nodes directory:
   ```
   ComfyUI/custom_nodes/Geekatplay-3D-MeshFix
   ```
2. Create or update `meshwright_config.json` inside this folder pointing to your Meshwright installation:
   ```json
   {
     "meshwright_root": "C:/Path/To/Meshwright",
     "meshwright_venv": "C:/Path/To/Meshwright/.venv"
   }
   ```

---

## Example Workflow

A sample workflow is included in [`examples/mesh_fix_workflow.json`](./examples/mesh_fix_workflow.json):
1. In ComfyUI, click **Load** (or drag and drop `mesh_fix_workflow.json` onto the canvas).
2. The workflow loads a 3D model, performs staged repair, reduces polygon count, displays a side-by-side diagnostic comparison, and saves the repaired 3D file.

---

## Compatibility

- Compatible with Python 3.10 – 3.13.
- Works with standard ComfyUI `MESH` objects (`trimesh.Trimesh`, vertex/face dictionaries, and 3D file paths).
- Compatible with ComfyUI-3D-Pack, Comfy3D, TripoSR, StableFast3D, and standard ComfyUI image preview nodes.
