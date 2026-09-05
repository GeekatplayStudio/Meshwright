"""
Texture and PBR material orchestration mixin for MeshService.

Everything here works with the per-corner UV channel held on the current state
(`_State.uv`), never with UVs baked into the mesh's vertices — see
engine/texture/uv_channel.py for why that distinction matters.
"""
import os

from engine import validation as V
from engine.texture import (
    generate_pbr_material,
    unwrap_corner_uv,
)
from engine.texture import uv_channel as UV

# Above this, an automatic unwrap on load costs more than the user wants to wait
# for something they did not ask for. They can still unwrap by hand.
AUTO_UNWRAP_MAX_FACES = 100_000


class TextureServiceMixin:
    """Methods providing UV unwrapping, PBR generation, and material baking."""

    def unwrap_uvs(self, force: bool = False) -> dict:
        """
        Build a fresh UV layout for the current mesh.

        The geometry is not touched: xatlas's vertex split is unpacked straight into
        the per-corner channel, so a watertight model stays watertight and its
        diagnostics score does not move.
        """
        with self.lock:
            st = self._require()
            if st.uv is not None and not V.boolean(force, False):
                # Re-unwrapping throws away the mapping the loaded textures were
                # painted against. Let the UI ask before that happens.
                return {
                    "success": False,
                    "needs_confirm": True,
                    "state_id": st.id,
                    "has_textures": self.materials.has_textures(),
                    "reason": "This model already has UV coordinates. Unwrapping again "
                              "replaces them with a new layout, and any texture painted "
                              "for the old one will no longer line up.",
                }

            replacing = st.uv is not None
            with self._job("unwrap", "Unwrapping UV coordinates"):
                corner_uv, stats = unwrap_corner_uv(st.mesh)

            if replacing and self.materials.has_textures():
                self.log("Loaded texture maps preserved as reference for the unfolded UV layout", "info")

            res = self._commit(st.mesh.copy(), "unwrap_uv", detail=stats, guard=False, uv=corner_uv)
            self.log(f"UV layout: {stats['islands']} island(s), {stats['seam_edges']:,} seam edge(s), "
                     f"{stats['atlas_size'][0]}×{stats['atlas_size'][1]} atlas", "ok")
            if stats.get("retries"):
                self.log(f"Re-split {stats['retries']} time(s) to even out the texture density", "info")
            if not stats.get("even", True):
                self.log(f"Texture density varies by about {stats['texel_spread']:.1f}× across this model — "
                         "some areas will look sharper than others. Its shape is hard to flatten.", "warn")
            res["uv_stats"] = stats
            res["uv_layout"] = self.get_uv_layout()
            return res

    def generate_pbr_textures(self, image_input, normal_strength: float = 2.5,
                              opengl_normal: bool = True, min_roughness: float = 0.2,
                              max_roughness: float = 0.85, metallic: float = 0.0,
                              ao_intensity: float = 1.0, tileable: bool = False) -> dict:
        with self.lock:
            st = self._require()
            with self._job("pbr_gen", "Generating PBR texture maps"):
                maps = generate_pbr_material(
                    image_input,
                    normal_strength=normal_strength,
                    opengl_normal=opengl_normal,
                    min_roughness=min_roughness,
                    max_roughness=max_roughness,
                    metallic_amount=metallic,
                    ao_intensity=ao_intensity,
                    tileable=V.boolean(tileable, False),
                )
                self.materials.set_material_maps(maps)

            if st.uv is None:
                # Without UVs there is nowhere for the maps to land, so unwrap — but
                # say plainly that the layout is new and the artwork will not match it.
                if 0 < len(st.mesh.faces) <= AUTO_UNWRAP_MAX_FACES:
                    self.log("Model has no UV coordinates — unwrapping so the maps have somewhere to land", "warn")
                    corner_uv, stats = unwrap_corner_uv(st.mesh)
                    self._commit(st.mesh.copy(), "auto_unwrap", detail=stats, guard=False, uv=corner_uv)
                else:
                    self.log(f"Model has no UV coordinates and is too dense to unwrap automatically "
                             f"({len(st.mesh.faces):,} faces). Use Unwrap UVs first.", "warn")

            state = self.get_texture_state()
            self.log(f"Generated PBR maps from image ({maps['albedo'].size[0]}x{maps['albedo'].size[1]})", "ok")
            return state

    def export_texture_pack(self, output_dir: str) -> dict:
        with self.lock:
            st = self._require()
            res = self.materials.export_texture_pack(output_dir, st.mesh, st.uv)
            self.log(f"Exported {res['count']} texture files to {output_dir}", "ok")
            return {"success": True, "result": res}

    def reload_texture_pack(self, input_dir: str | None = None) -> dict:
        with self.lock:
            self._require()
            res = self.materials.reload_texture_pack(input_dir)
            self.log(f"Reloaded textures ({', '.join(res['reloaded_channels'])}) from {res['directory']}", "ok")
            state = self.get_texture_state()
            state["reloaded"] = res
            return state

    def get_texture_state(self) -> dict:
        """
        What the UI needs after every operation: what exists, not the pixels.

        The maps and the UV wireframe are both large — a full 2048px set is around
        13 MB of base64 and a third of a second to encode — and they were being
        rebuilt and shipped on every repair, reduce, undo and rotate. Callers watch
        `texture_version` and `state_id` and fetch the heavy parts only when one of
        them moves.
        """
        with self.lock:
            st = self._require()
            summary = self.materials.summary()
            return {
                "success": True,
                "has_textures": summary["has_textures"],
                "has_uv": st.uv is not None,
                "texture_version": summary["version"],
                "channels": summary["channels"],
                "state_id": st.id,
            }

    def get_texture_maps(self) -> dict:
        """The maps themselves, as base64 PNGs. Fetched on demand — see above."""
        with self.lock:
            self._require()
            payload = self.materials.get_ui_payload()
            return {"success": True, "texture_version": payload["version"], "maps": payload}

    def get_uv_layout(self, max_edges: int = 15000) -> dict:
        """UV-space edges for the 2D unfold canvas. Fetched when that view opens."""
        with self.lock:
            st = self._require()
            layout = UV.wireframe_lines(st.mesh, st.uv, max_edges=max_edges)
            layout["success"] = True
            layout["state_id"] = st.id
            return layout

    def bake_and_export_glb(self, path: str) -> dict:
        with self.lock:
            st = self._require()
            p = V.output_path(path, ".glb")
            with self._job("bake_glb", f"Baking PBR materials to {os.path.basename(p)}"):
                res = self.materials.bake_and_export_glb(st.mesh, p, st.uv)
            self.log(f"Saved baked GLB ({res['size_mb']} MB) to {p}", "ok")
            return res

    def _detect_and_bind_textures(self, path: str, mesh, corner_uv=None):
        """
        Pick up whatever textures came with the file and make sure they have UVs to
        sit on. Returns (mesh, corner_uv) — the geometry is never modified here.
        """
        try:
            from engine.texture.companion_detector import (
                find_and_load_companion_textures,
            )
            maps = find_and_load_companion_textures(path, mesh)
            if not maps:
                return mesh, corner_uv

            self.materials.set_material_maps(maps)
            self.log(f"Texture maps found: {', '.join(sorted(maps))}", "ok")

            if corner_uv is None:
                if 0 < len(mesh.faces) <= AUTO_UNWRAP_MAX_FACES:
                    self.log("The file has textures but no UV coordinates — unwrapping one", "warn")
                    corner_uv, stats = unwrap_corner_uv(mesh)
                    self.log(f"Generated a {stats['islands']}-island layout. It is a new layout, so the "
                             "texture will not line up with the original artwork.", "warn")
                else:
                    self.log(f"The file has textures but no UV coordinates, and at {len(mesh.faces):,} faces "
                             "it is too dense to unwrap automatically. Use Unwrap UVs if you want one.", "warn")
        except Exception as e:
            self.log(f"Texture detection: {e}", "warn")
        return mesh, corner_uv
