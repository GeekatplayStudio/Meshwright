"""
Meshwright ComfyUI Custom Nodes — Geekatplay Studio
Author: Geekatplay Studio (Vladimir Chopine)
Provides nodes for mesh analysis, repair, reduction, comparison, preview, and export.
"""
import os
import sys

# Local utilities
from .utils import (
    format_fix_report,
    format_reduction_report,
    np_to_comfy_tensor,
    render_comparison_preview,
    render_mesh_to_np,
    to_trimesh,
)

# Ensure Meshwright root is on sys.path
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_CONFIG_PATH = os.path.join(_THIS_DIR, "meshwright_config.json")
if os.path.exists(_CONFIG_PATH):
    try:
        import json
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            _cfg = json.load(f)
            _root = _cfg.get("meshwright_root")
            if _root and os.path.isdir(_root) and _root not in sys.path:
                sys.path.insert(0, _root)
    except Exception:
        pass
_repo_candidate = os.path.abspath(os.path.join(_THIS_DIR, "..", ".."))
if os.path.exists(os.path.join(_repo_candidate, "engine", "service.py")):
    if _repo_candidate not in sys.path:
        sys.path.insert(0, _repo_candidate)

# Engine imports (loaded from Meshwright root)
try:
    from engine.demo_model import build_demo_mesh
    from engine.mesh_analysis import analyze_mesh, compare_analyses
    from engine.mesh_cleanup import fix_slivers
    from engine.mesh_exporter import export_to_format, solidity_report
    from engine.mesh_reducer import reduce_mesh
    from engine.mesh_repair import repair_mesh
    from engine.model_loader import load_model
    HAS_ENGINE = True
except ImportError:
    HAS_ENGINE = False


def _check_worse(before: dict, after: dict) -> str | None:
    if before is None or after is None:
        return None
    bc = sum(1 for i in before.get("issues", []) if i.get("severity") == "critical")
    ac = sum(1 for i in after.get("issues", []) if i.get("severity") == "critical")
    if ac > bc:
        return f"critical problems would increase from {bc} to {ac}"
    bf = before.get("stats", {}).get("face_count", 0)
    af = after.get("stats", {}).get("face_count", 0)
    if bf > 100 and af < bf * 0.05:
        return f"{100 - round(100 * af / bf)}% of the geometry would be lost"
    if af == 0:
        return "the result is empty"
    return None


class MeshwrightFixMesh:
    """
    Runs Meshwright's staged repair pipeline:
    Cleanup -> Orientation -> Small holes -> MeshFix -> MeshLab -> Manifold3D Solidify -> Voxel remesh fallback.
    Measures diagnostics before and after and generates a full human-readable report.
    """
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "mesh": ("MESH",),
            },
            "optional": {
                "strict_watertight": ("BOOLEAN", {"default": True, "label": "Strict Watertight (Voxel Fallback)"}),
                "fix_slivers": ("BOOLEAN", {"default": False, "label": "Fix Needle & Cap Slivers"}),
                "min_sliver_angle": ("FLOAT", {"default": 1.0, "min": 0.1, "max": 15.0, "step": 0.5, "label": "Min Sliver Angle (deg)"}),
                "voxel_pitch": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 50.0, "step": 0.05, "label": "Voxel Pitch (0=auto)"}),
                "max_passes": ("INT", {"default": 3, "min": 1, "max": 10, "step": 1, "label": "Max Repair Passes"}),
                "force": ("BOOLEAN", {"default": False, "label": "Force Apply (Ignore Safeguards)"}),
            }
        }

    RETURN_TYPES = ("MESH", "STRING", "STRING", "BOOLEAN")
    RETURN_NAMES = ("mesh", "report", "summary", "is_watertight")
    FUNCTION = "fix_mesh"
    CATEGORY = "Meshwright 3D"

    def fix_mesh(self, mesh, strict_watertight=True, fix_slivers=False,
                 min_sliver_angle=1.0, voxel_pitch=0.0, max_passes=3, force=False):
        m = to_trimesh(mesh)
        before_analysis = analyze_mesh(m)

        work = m.copy()
        sliver_fixes = []
        if fix_slivers:
            work, s_info = fix_slivers(work, min_angle_deg=min_sliver_angle)
            c_cnt = s_info.get("collapsed", 0) + s_info.get("flipped", 0)
            if c_cnt > 0:
                sliver_fixes.append({"stage": "Slivers", "description": f"Collapsed/flipped {c_cnt} needle/cap sliver triangles"})

        repaired, report = repair_mesh(
            work,
            strict_watertight=strict_watertight,
            voxel_pitch=voxel_pitch,
            max_passes=max_passes
        )

        all_fixes = sliver_fixes + report.get("fixes", [])
        after_analysis = report.get("after", analyze_mesh(repaired))

        # Safeguard validation (unless force is requested)
        if not force:
            reason = _check_worse(before_analysis, after_analysis)
            if reason is not None:
                # Issue regression occurred; report and keep or revert depending on user preference
                report_txt = f"[Safeguard Rejected]: {reason}\n(Use force=True to override)\n\n" + \
                             format_fix_report(before_analysis, after_analysis, all_fixes, report.get("method_used", "none"), report.get("passes", 1))
                summary = f"Rejected: {reason}"
                return (m, report_txt, summary, bool(m.is_watertight))

        report_txt = format_fix_report(
            before_analysis,
            after_analysis,
            all_fixes,
            report.get("method_used", "none"),
            report.get("passes", 1)
        )

        b_score = before_analysis.get("score", 0)
        a_score = after_analysis.get("score", 0)
        is_wt = bool(repaired.is_watertight)
        summary = f"Repaired ({report.get('method_used', 'cleanup')}): Score {b_score} -> {a_score} | Watertight: {is_wt} | Faces: {len(repaired.faces):,}"

        return (repaired, report_txt, summary, is_wt)


class MeshwrightReduceMesh:
    """
    Reduces polygon count using Quadric Decimation (fast_simplification / PyMeshLab / Trimesh quadric)
    or Smart Retopology (QuadriFlow / Uniform Isotropic), preserving topology and hard edges.
    """
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "mesh": ("MESH",),
            },
            "optional": {
                "mode": (["decimate", "smart_retopo (quadriflow)", "uniform_isotropic"], {"default": "decimate"}),
                "reduction_factor": ("FLOAT", {"default": 0.5, "min": 0.01, "max": 0.99, "step": 0.05, "label": "Keep Fraction (0.5 = 50%)"}),
                "target_faces": ("INT", {"default": 0, "min": 0, "max": 10000000, "step": 500, "label": "Target Faces (0=use factor)"}),
                "preserve_sharp": ("BOOLEAN", {"default": True, "label": "Preserve Sharp Edges"}),
            }
        }

    RETURN_TYPES = ("MESH", "STRING", "FLOAT")
    RETURN_NAMES = ("mesh", "report", "reduction_pct")
    FUNCTION = "reduce"
    CATEGORY = "Meshwright 3D"

    def reduce(self, mesh, mode="decimate", reduction_factor=0.5, target_faces=0, preserve_sharp=True):
        m = to_trimesh(mesh)
        initial_count = len(m.faces)

        if initial_count < 10:
            return (m, "Mesh too small to reduce.", 0.0)

        # Retopology mode
        if "smart_retopo" in mode or "uniform" in mode:
            from engine.mesh_retopo import retopologize
            target = target_faces if target_faces > 0 else max(20, int(initial_count * reduction_factor))
            retopo_method = "quadriflow" if "smart_retopo" in mode else "isotropic"
            reduced, retopo_info = retopologize(
                m,
                target_faces=target,
                method=retopo_method,
                preserve_sharp=preserve_sharp
            )
            pct = round((1.0 - (len(reduced.faces) / float(initial_count))) * 100.0, 1)
            info = {
                "initial_faces": initial_count,
                "final_faces": len(reduced.faces),
                "reduction_percentage": pct,
                "method_used": f"retopo ({retopo_method})"
            }
        else:
            # Decimation mode
            reduced, info = reduce_mesh(
                m,
                target_factor=reduction_factor,
                target_faces=target_faces
            )
            pct = float(info.get("reduction_percentage", 0.0))

        report_txt = format_reduction_report(m, reduced, info)
        return (reduced, report_txt, pct)


class MeshwrightLoadModel:
    """
    Loads 3D models (OBJ, FBX, GLB/GLTF, STL, PLY, 3MF, DAE, OFF) or the built-in demo model.
    """
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {},
            "optional": {
                "file_path": ("STRING", {"default": "", "multiline": False, "label": "3D File Path (leave empty for Demo Model)"}),
            }
        }

    RETURN_TYPES = ("MESH", "STRING")
    RETURN_NAMES = ("mesh", "diagnostics")
    FUNCTION = "load_model"
    CATEGORY = "Meshwright 3D"

    def load_model(self, file_path=""):
        cleaned_path = file_path.strip().strip('"').strip("'")
        if not cleaned_path or cleaned_path.lower() in ("demo", "builtin"):
            mesh = build_demo_mesh()
            diag = analyze_mesh(mesh, "demo_model.obj")
            desc = f"Loaded built-in Demo Model: {len(mesh.faces):,} triangles, {len(mesh.vertices):,} vertices. Readiness: {diag['score']}/100 ({diag['verdict']})"
            return (mesh, desc)

        if not os.path.exists(cleaned_path):
            raise FileNotFoundError(f"File not found: {cleaned_path}")

        mesh, stats = load_model(cleaned_path)
        diag = analyze_mesh(mesh, cleaned_path)
        desc = (
            f"Loaded {os.path.basename(cleaned_path)}: "
            f"{len(mesh.faces):,} triangles, {len(mesh.vertices):,} vertices | "
            f"Readiness Score: {diag['score']}/100 ({diag['verdict']})"
        )
        return (mesh, desc)


class MeshwrightCompareMesh:
    """
    Compares two meshes side-by-side: generates detailed diagnostics, list of resolved issues,
    and a side-by-side rendered visual comparison image (Before in coral/red vs After in teal/green).
    """
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "mesh_before": ("MESH",),
                "mesh_after": ("MESH",),
            }
        }

    RETURN_TYPES = ("STRING", "INT", "INT", "IMAGE")
    RETURN_NAMES = ("report", "score_before", "score_after", "comparison_image")
    FUNCTION = "compare"
    CATEGORY = "Meshwright 3D"

    def compare(self, mesh_before, mesh_after):
        mb = to_trimesh(mesh_before)
        ma = to_trimesh(mesh_after)

        diag_b = analyze_mesh(mb)
        diag_a = analyze_mesh(ma)

        diffs = compare_analyses(diag_b, diag_a)

        lines = [
            "=" * 60,
            "  GEEKATPLAY MESHWRIGHT - BEFORE & AFTER COMPARISON",
            "=" * 60,
            f"Print Readiness: {diag_b['score']}/100 ({diag_b['verdict']})  -->  {diag_a['score']}/100 ({diag_a['verdict']})",
            f"Watertight Solid: {diag_b['stats']['is_watertight']}  -->  {diag_a['stats']['is_watertight']}",
            f"Winding Consistent: {diag_b['stats']['is_winding_consistent']}  -->  {diag_a['stats']['is_winding_consistent']}",
            "",
            "--- METRIC CHANGES ---"
        ]

        if not diffs:
            lines.append("  No numeric metric differences detected.")
        else:
            for d in diffs:
                tag = "[+] IMPROVED" if d.get("improved") else "[-] CHANGED"
                lines.append(f"  {tag:<14} {d['label']:<20}: {d['before']} -> {d['after']}")

        lines.append("=" * 60)
        report_txt = "\n".join(lines)

        # Render side-by-side visual comparison
        comp_np = render_comparison_preview(mb, ma)
        comp_tensor = np_to_comfy_tensor(comp_np)

        return (report_txt, int(diag_b["score"]), int(diag_a["score"]), comp_tensor)


class MeshwrightPreview3D:
    """
    Renders software-shaded 3D preview of a mesh as an image tensor compatible with ComfyUI's Preview Image.
    """
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "mesh": ("MESH",),
            }
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("preview_image",)
    FUNCTION = "preview"
    CATEGORY = "Meshwright 3D"
    OUTPUT_NODE = True

    def preview(self, mesh):
        m = to_trimesh(mesh)
        img_np = render_mesh_to_np(m, width=512, height=512, tint_rgb=(0.22, 0.65, 0.85))
        tensor = np_to_comfy_tensor(img_np)
        return (tensor,)


class MeshwrightSaveMesh:
    """
    Exports a 3D mesh to standard formats (STL, OBJ, GLB, PLY, 3MF, OFF) with unit scaling
    and build-plate alignment, checking printability solidity.
    """
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "mesh": ("MESH",),
            },
            "optional": {
                "filename_prefix": ("STRING", {"default": "Meshwright/fixed_model"}),
                "format": (["stl", "obj", "glb", "ply", "3mf", "off"], {"default": "stl"}),
                "scale_unit": (["mm", "cm", "in"], {"default": "mm"}),
                "align_origin": ("BOOLEAN", {"default": True, "label": "Rest on build plate"}),
            }
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("file_path", "solidity_status")
    FUNCTION = "save_mesh"
    CATEGORY = "Meshwright 3D"
    OUTPUT_NODE = True

    def save_mesh(self, mesh, filename_prefix="Meshwright/fixed_model",
                  format="stl", scale_unit="mm", align_origin=True):
        m = to_trimesh(mesh)

        # Try to locate ComfyUI output directory if available
        output_dir = "output"
        try:
            import folder_paths
            output_dir = folder_paths.get_output_directory()
        except ImportError:
            pass

        sub_dir = os.path.dirname(filename_prefix)
        base_name = os.path.basename(filename_prefix) or "meshwright_model"
        target_dir = os.path.join(output_dir, sub_dir) if sub_dir else output_dir
        os.makedirs(target_dir, exist_ok=True)

        ext = f".{format.lower()}"
        out_path = os.path.join(target_dir, f"{base_name}{ext}")

        # Ensure unique name if exists
        counter = 1
        while os.path.exists(out_path):
            out_path = os.path.join(target_dir, f"{base_name}_{counter:03d}{ext}")
            counter += 1

        export_to_format(
            m,
            out_path,
            export_format=format.lower(),
            scale_unit=scale_unit,
            align_origin=align_origin
        )
        solidity = solidity_report(m)

        status = f"Exported {format.upper()} ({scale_unit}) to {out_path}. Printable solid: {solidity.get('is_solid', False)}"
        return (out_path, status)


class MeshwrightTextDisplay:
    """
    Displays text reports and diagnostics on the ComfyUI canvas.
    """
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "text": ("STRING", {"forceInput": True}),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("text",)
    FUNCTION = "show_text"
    CATEGORY = "Meshwright 3D"
    OUTPUT_NODE = True

    def show_text(self, text):
        return {"ui": {"text": [text]}, "result": (text,)}


NODE_CLASS_MAPPINGS = {
    "MeshwrightFixMesh": MeshwrightFixMesh,
    "MeshwrightReduceMesh": MeshwrightReduceMesh,
    "MeshwrightLoadModel": MeshwrightLoadModel,
    "MeshwrightCompareMesh": MeshwrightCompareMesh,
    "MeshwrightSaveMesh": MeshwrightSaveMesh,
    "MeshwrightPreview3D": MeshwrightPreview3D,
    "MeshwrightTextDisplay": MeshwrightTextDisplay,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MeshwrightFixMesh": "Meshwright Fix Mesh 🔧",
    "MeshwrightReduceMesh": "Meshwright Reduce Mesh 📉",
    "MeshwrightLoadModel": "Meshwright Load 3D Model 📂",
    "MeshwrightCompareMesh": "Meshwright Compare & Diagnose 🔍",
    "MeshwrightSaveMesh": "Meshwright Save 3D Mesh 💾",
    "MeshwrightPreview3D": "Meshwright 3D Preview 👁️",
    "MeshwrightTextDisplay": "Meshwright Report Display 📋",
}
