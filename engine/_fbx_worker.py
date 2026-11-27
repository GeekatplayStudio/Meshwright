"""
Out-of-process FBX reader — Geekatplay Studio

ufbx is native code, and its Python binding (0.0.5, the newest there is) can fault:
it frees memory a later call still reads, and on a large scene freeing the scene
itself faults a few times in ten. In the Meshwright process either one closes the
window with no message, and the file browser parses an FBX just to draw its picture.
So the reading happens here. A crash becomes an exit code the parent turns into a
message; and since this process leaves with os._exit without freeing the scene, the
teardown fault never happens at all.

    python _fbx_worker.py <model.fbx> <output.npz>

Exit codes: 0 done, 1 a Python error (its message is the last line of stderr).
Anything else is the native reader dying, and the parent says so.

Unlike the other helpers this imports Meshwright's own loader, so that the FBX is
read by exactly the code that reads it in-process. From source it is started by file
path, so it puts the project root on sys.path itself.
"""
import os
import sys

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Everything the reader touches, kept until the process ends; see engine.model_loader._read_fbx.
_HOLD = []

# A single quad in ASCII FBX 7.4, Y up. The self-test reads it through this helper so
# every build proves the frozen program can still open an FBX, and the tests use it too.
SAMPLE_FBX = """; FBX 7.4.0 project file
FBXHeaderExtension:  {
\tFBXHeaderVersion: 1003
\tFBXVersion: 7400
}
GlobalSettings:  {
\tVersion: 1000
\tProperties70:  {
\t\tP: "UpAxis", "int", "Integer", "",1
\t\tP: "UpAxisSign", "int", "Integer", "",1
\t\tP: "FrontAxis", "int", "Integer", "",2
\t\tP: "FrontAxisSign", "int", "Integer", "",1
\t\tP: "CoordAxis", "int", "Integer", "",0
\t\tP: "CoordAxisSign", "int", "Integer", "",1
\t\tP: "UnitScaleFactor", "double", "Number", "",1
\t}
}
Objects:  {
\tGeometry: 1000, "Geometry::Quad", "Mesh" {
\t\tVertices: *12 {
\t\t\ta: 0,0,0,10,0,0,10,10,0,0,10,0
\t\t}
\t\tPolygonVertexIndex: *4 {
\t\t\ta: 0,1,2,-4
\t\t}
\t\tGeometryVersion: 124
\t}
\tModel: 2000, "Model::Quad", "Mesh" {
\t\tVersion: 232
\t}
}
Connections:  {
\tC: "OO",2000,0
\tC: "OO",1000,2000
}
"""


def main(fbx_path: str, out_path: str) -> int:
    from engine.model_loader import _pack_fbx, _read_fbx

    _pack_fbx(_read_fbx(fbx_path, hold=_HOLD), out_path)
    return 0


if __name__ == "__main__":
    code = 1
    try:
        code = main(sys.argv[1], sys.argv[2])
    except BaseException as exc:                      # noqa: BLE001 - report anything, then leave
        os.write(2, f"{type(exc).__name__}: {exc}\n".encode("utf-8", "replace"))
    # Leave without freeing the scene: that is the point of being a separate process.
    os._exit(code)
