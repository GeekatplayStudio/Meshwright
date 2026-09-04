import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.texture._xatlas_worker import main


def test_xatlas_worker_execution(tmp_path):
    # Create two simple open patches (two adjacent triangles each)
    p1_v = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0]], dtype=np.float64)
    p1_f = np.array([[0, 1, 2], [1, 3, 2]], dtype=np.int32)

    p2_v = np.array([[2, 0, 0], [3, 0, 0], [2, 1, 0]], dtype=np.float64)
    p2_f = np.array([[0, 1, 2]], dtype=np.int32)

    all_v = np.vstack([p1_v, p2_v])
    all_f = np.vstack([p1_f, p2_f])
    v_offsets = np.array([0, len(p1_v), len(all_v)], dtype=np.int64)
    f_offsets = np.array([0, len(p1_f), len(all_f)], dtype=np.int64)

    in_file = str(tmp_path / "input.npz")
    out_file = str(tmp_path / "output.npz")

    np.savez(
        in_file,
        vertices=all_v,
        faces=all_f,
        vertex_offsets=v_offsets,
        face_offsets=f_offsets,
    )

    exit_code = main(in_file, out_file)
    assert exit_code == 0

    out_data = np.load(out_file)
    assert "uvs" in out_data
    assert "indices" in out_data
    assert "uv_offsets" in out_data
    assert "charts" in out_data
    assert "atlas_size" in out_data

    assert len(out_data["uvs"]) > 0
    assert len(out_data["indices"]) == len(all_f)
    assert len(out_data["uv_offsets"]) == 3
    assert len(out_data["charts"]) == 2
    assert out_data["atlas_size"][0] > 0
    assert out_data["atlas_size"][1] > 0


def test_xatlas_worker_empty(tmp_path):
    in_file = str(tmp_path / "empty_input.npz")
    out_file = str(tmp_path / "empty_output.npz")

    np.savez(
        in_file,
        vertices=np.zeros((0, 3), dtype=np.float64),
        faces=np.zeros((0, 3), dtype=np.int32),
        vertex_offsets=np.array([0], dtype=np.int64),
        face_offsets=np.array([0], dtype=np.int64),
    )

    exit_code = main(in_file, out_file)
    assert exit_code == 0

    out_data = np.load(out_file)
    assert len(out_data["uvs"]) == 0
    assert len(out_data["indices"]) == 0
    # Nothing was packed, so there is no atlas — and xatlas must not have been asked
    # to generate one, which it answers by writing to a stderr we cannot catch.
    assert list(out_data["atlas_size"]) == [0, 0]
