"""
Splitting a mesh into patches xatlas can actually handle — Geekatplay Studio
Author: Vladimir Chopine

xatlas 0.0.11 has two failure modes that are hit by ordinary 3D-printing models,
and both were measured on this project rather than assumed:

**Closed surfaces.** On a mesh where every edge has an opposite — which is exactly
what a watertight, print-ready model is — xatlas never marks a boundary vertex, and
its convex-hull pass then reads uninitialised data (upstream issue jpcy/xatlas#146).
A 81,920-face icosphere crashes the process with an access violation; remove a
single triangle so the surface has a boundary and it unwraps in a tenth of a second.
Small closed meshes survive, but they are relying on the same uninitialised read.

**Size.** Above roughly forty thousand faces in one mesh, xatlas stops segmenting
and returns a single chart covering the whole surface. It does not report an error;
the UVs simply overlap, and a texture painted on them smears. Measured total UV area
of 1.02 to 1.57 in a unit square, where anything above 1.0 is impossible without
overlap.

So Meshwright never hands xatlas a whole mesh. It hands it patches, each one open
and under a face cap, added to a single atlas so they still pack into one texture.
The cost is extra seams; the benefit is that it works, and it is far quicker — a
159,048-face surface went from 121 seconds to 7.7, with *lower* distortion.
"""
import numpy as np
import trimesh
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

# Faces per patch to start from. This is a starting point, not a fixed rule: the
# unwrapper measures the layout it gets back and halves the cap if the texel density
# came out uneven, because xatlas's own chart segmentation is unreliable at size and
# a smaller patch is flatter whether it segments or not.
#
# Measured on an 81,920-face sphere, texel-density spread against patch size:
#   20,480 faces/patch -> 18.1x    10,240 -> 9.1x    5,120 -> 2.2x
# while a 180,000-face torus is already 1.2x at 22,500 faces/patch. One number
# cannot serve both shapes, hence the retry.
DEFAULT_PATCH_FACES = 16_000

# A patch cannot be usefully split below this; a handful of triangles is already a
# trivially flattenable disk.
MIN_PATCH_FACES = 8


def face_components(vertices_count: int, faces: np.ndarray) -> np.ndarray:
    """Label each face with its connected component, by shared edges."""
    if len(faces) == 0:
        return np.zeros(0, dtype=np.int64)
    shell = trimesh.Trimesh(vertices=np.zeros((vertices_count, 3)), faces=faces, process=False)
    pairs = np.asarray(shell.face_adjacency)
    if len(pairs) == 0:
        return np.arange(len(faces), dtype=np.int64)
    graph = coo_matrix((np.ones(len(pairs), dtype=np.int8), (pairs[:, 0], pairs[:, 1])),
                       shape=(len(faces), len(faces)))
    return connected_components(graph, directed=False)[1]


def is_closed(faces: np.ndarray) -> bool:
    """True when every edge is shared by two faces — the shape that crashes xatlas."""
    if len(faces) == 0:
        return False
    edges = np.sort(np.vstack([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]]), axis=1)
    _, counts = np.unique(edges, axis=0, return_counts=True)
    return not np.any(counts == 1)


def split_into_patches(vertices: np.ndarray, faces: np.ndarray,
                       max_faces: int = DEFAULT_PATCH_FACES) -> list[np.ndarray]:
    """
    Break a mesh into face-index groups that are each open and under `max_faces`.

    A component that is already open and small enough is returned whole, so a model
    that xatlas can handle directly gains no extra seams. Anything else is bisected
    along its longest axis and the halves re-checked; because every half is a proper
    subset of a closed surface, it necessarily has a boundary.
    """
    if len(faces) == 0:
        return []
    cap = max(MIN_PATCH_FACES, int(max_faces))

    patches: list[np.ndarray] = []
    pending = [np.flatnonzero(face_components(len(vertices), faces) == label)
               for label in np.unique(face_components(len(vertices), faces))]

    while pending:
        group = pending.pop()
        if len(group) == 0:
            continue
        block = faces[group]
        if len(group) <= MIN_PATCH_FACES or (len(group) <= cap and not is_closed(block)):
            patches.append(group)
            continue

        centres = vertices[block].mean(axis=1)
        axis = int(np.argmax(centres.max(axis=0) - centres.min(axis=0)))
        order = np.argsort(centres[:, axis], kind="stable")
        halves = (group[order[:len(order) // 2]], group[order[len(order) // 2:]])

        if not all(len(h) for h in halves):        # cannot bisect further
            patches.append(group)
            continue

        for half in halves:
            labels = face_components(len(vertices), faces[half])
            for label in np.unique(labels):
                pending.append(half[labels == label])

    return patches


def localise(vertices: np.ndarray, faces: np.ndarray, group: np.ndarray):
    """A patch as its own little mesh: (patch vertices, patch faces with local indices)."""
    block = faces[group]
    used = np.unique(block)
    remap = np.zeros(len(vertices), dtype=np.int32)
    remap[used] = np.arange(len(used), dtype=np.int32)
    return vertices[used], remap[block]


# ------------------------------------------------------------------ validation
def uv_area(corner_uv: np.ndarray) -> float:
    """Total area the UV triangles cover. Above ~1.0 in a unit atlas means overlap."""
    d1 = corner_uv[:, 1] - corner_uv[:, 0]
    d2 = corner_uv[:, 2] - corner_uv[:, 0]
    return float(0.5 * np.abs(d1[:, 0] * d2[:, 1] - d1[:, 1] * d2[:, 0]).sum())


# A correctly packed atlas leaves gutters between charts, so real results measure
# around 0.55-0.85. Anything near or above 1.0 is xatlas having given up.
MAX_VALID_UV_AREA = 0.95

# Ratio between the 95th and 5th percentile of texel density. 1.0 would be a
# perfectly even layout; a good unwrap of an awkward shape lands around 1.2-2.5.
# Past this the texture is noticeably sharper in some places than others, and the
# patches are worth halving.
GOOD_TEXEL_SPREAD = 3.0


def texel_spread(vertices: np.ndarray, faces: np.ndarray, corner_uv: np.ndarray) -> float:
    """
    How unevenly the texture is stretched over the surface.

    Each triangle's UV area divided by its real area gives the texel density there;
    the ratio of the 95th to the 5th percentile says how far apart the best and
    worst-served parts of the model are. 1.0 is perfectly even.
    """
    d1 = corner_uv[:, 1] - corner_uv[:, 0]
    d2 = corner_uv[:, 2] - corner_uv[:, 0]
    flat = 0.5 * np.abs(d1[:, 0] * d2[:, 1] - d1[:, 1] * d2[:, 0])

    tri = vertices[faces]
    solid = 0.5 * np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1)

    usable = (flat > 1e-14) & (solid > 1e-14)
    if usable.sum() < 8:
        return 1.0
    density = flat[usable] / solid[usable]
    low, high = np.percentile(density, [5, 95])
    return float(high / max(low, 1e-12))


def validate(corner_uv: np.ndarray) -> str | None:
    """The reason this UV layout is unusable, or None if it is fine."""
    if corner_uv is None or len(corner_uv) == 0:
        return "no UV coordinates were produced"
    if not np.isfinite(corner_uv).all():
        return "the layout contains invalid numbers"
    if corner_uv.min() < -0.01 or corner_uv.max() > 1.01:
        return "the layout falls outside the texture area"
    covered = uv_area(corner_uv)
    if covered > MAX_VALID_UV_AREA:
        return f"the charts overlap each other (UV coverage {covered:.2f})"
    return None
