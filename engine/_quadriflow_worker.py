"""
Out-of-process QuadriFlow runner — Geekatplay Studio

QuadriFlow is native C++ and it does not always fail politely. On a mesh it dislikes
it can abort inside Eigen — `Assertion failed: index >= 0 && index < size()` — which
takes the whole Meshwright window with it, mid-retopology, with no message.

It is also the one engine here with no way to know in advance whether a given mesh
will be accepted: it refuses non-manifold input with an exception, which is
recoverable, but the assertion is not.

So it runs in a child process. A crash becomes an exit code, the caller falls through
to the isotropic or quadric engines exactly as it would for any other failure, and the
user gets a retopologised mesh instead of a closed window.

Imports nothing from Meshwright — numpy and pyQuadriFlow only — so the parent can
launch it by file path.

    python _quadriflow_worker.py <input.npz> <output.npz>
"""
import sys

import numpy as np
from pyQuadriFlow.pyQuadriFlow import pyquadriflow


def main(in_path: str, out_path: str) -> int:
    data = np.load(in_path)
    vertices = np.asarray(data["vertices"], dtype=float).tolist()
    faces = np.asarray(data["faces"], dtype=int).tolist()
    quads, seed, sharp, adaptive = (int(x) for x in data["params"])

    result = pyquadriflow(quads, seed, vertices, faces,
                          bool(sharp), False, bool(adaptive), False, False)

    quad_faces = np.asarray(result.get("faces", []))
    if quad_faces.ndim != 2 or quad_faces.shape[1] != 4:
        return 2                       # not a quad mesh; the caller treats this as a miss

    np.savez(out_path,
             vertices=np.asarray(result.get("vertices", []), dtype=np.float64),
             faces=quad_faces.astype(np.int64))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
