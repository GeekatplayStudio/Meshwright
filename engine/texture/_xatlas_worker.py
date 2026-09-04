"""
Out-of-process xatlas runner — Geekatplay Studio

The parent hands over a set of patches (see uv_patches.py — each open and under a
face cap, because xatlas crashes on closed meshes and silently degenerates on large
ones). They are added to a single atlas so they still pack into one texture.

This still runs as a child process even though the known failure modes are now
designed around: xatlas is native code, a crash in it would close the Meshwright
window with no message, and that is not a risk worth carrying for the cost of one
process spawn.

Deliberately imports nothing from Meshwright — numpy and xatlas only — so the parent
can launch it by file path without worrying about sys.path.

    python _xatlas_worker.py <input.npz> <output.npz>
"""
import sys

import numpy as np
import xatlas


def main(in_path: str, out_path: str) -> int:
    data = np.load(in_path)
    vertices = np.ascontiguousarray(data["vertices"], dtype=np.float64)
    faces = np.ascontiguousarray(data["faces"], dtype=np.int32)
    v_offsets = data["vertex_offsets"]
    f_offsets = data["face_offsets"]
    count = len(f_offsets) - 1

    atlas = xatlas.Atlas()
    for i in range(count):
        atlas.add_mesh(vertices[v_offsets[i]:v_offsets[i + 1]],
                       faces[f_offsets[i]:f_offsets[i + 1]])
    if count:
        # Calling generate() with nothing added makes xatlas print a complaint to
        # the native stderr the parent cannot catch, and there is nothing to pack.
        atlas.generate()

    uv_parts, index_parts, uv_offsets, charts = [], [], [0], []
    for i in range(count):
        _, indices, uvs = atlas[i]
        uv_parts.append(np.asarray(uvs, dtype=np.float32))
        index_parts.append(np.asarray(indices, dtype=np.int64))
        uv_offsets.append(uv_offsets[-1] + len(uvs))
        try:
            charts.append(int(atlas.get_mesh_chart_count(i)))
        except Exception:
            charts.append(1)

    np.savez(
        out_path,
        uvs=np.concatenate(uv_parts) if uv_parts else np.zeros((0, 2), np.float32),
        indices=np.concatenate(index_parts) if index_parts else np.zeros((0, 3), np.int64),
        uv_offsets=np.asarray(uv_offsets, dtype=np.int64),
        charts=np.asarray(charts, dtype=np.int64),
        atlas_size=np.asarray([int(atlas.width), int(atlas.height)] if count else [0, 0], dtype=np.int64),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
