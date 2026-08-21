# Reducing polygons

Meshwright offers three engines behind one **Reduce** panel. All of them target an
**absolute face count**, and all of them report how far the result strays from the original.

## Choosing an engine

| | Best for | Keeps hard edges | Topology | Speed |
|---|---|---|---|---|
| **Smart retopo** (QuadriFlow) | Sculpts, scans, organic shapes, true low-poly | Optional | Rebuilt as clean quads | Slowest |
| **Decimate** (quadric) | Mechanical parts, CAD, anything with flat faces and crisp edges | Yes, best | Preserved | Fast |
| **Uniform** (isotropic) | Noisy scans, meshes with wildly uneven triangle sizes | No | Rebuilt as even triangles | Fast |

**Rule of thumb:** if the model was sculpted or scanned, use *Smart retopo*. If it came from CAD,
use *Decimate*. If triangle sizes are a mess, use *Uniform* first, then decimate.

## Surface deviation

After every reduction Meshwright samples both surfaces and reports:

- **max** — the worst deviation anywhere, in millimetres
- **mean** — the average deviation
- **% of size** — the maximum relative to the model's largest dimension

Under ~1 % is visually indistinguishable on a print. Above ~5 % you are losing detail you may care
about. The number is shown in the panel, the console and the JSON report.

## Worked example

The miniature in the README: **1,994,490 triangles, 23 separate pieces, genus 114, watertight.**

| Target | Engine | Result | Time | Max deviation |
|---|---|---|---|---|
| 50,000 | Smart retopo | 41,198 faces, watertight, 23 pieces | ~90 s | 0.043 mm (4.3 %) |

The readiness score stayed at **94** — identical to the source.

## How Meshwright keeps reduction safe

Remeshers are not obliged to hand back a valid solid, and on complex topology they often do not.
QuadriFlow on this miniature produced 24 holes, 12 non-manifold edges and 6 extra shells on its own.

Meshwright therefore:

1. **Processes each piece separately**, so a multi-part model keeps its parts and each gets a
   proportional share of the face budget.
2. **Skips retopology on small pieces** (under ~4,000 triangles, or a target under 400) — a quad
   field cannot be built cleanly on a 200-triangle gem, so those are decimated instead.
3. **Pre-decimates very dense pieces** before building the quad field. QuadriFlow does not need
   two million input faces to produce 45,000 quads; this roughly halves the time with no loss of accuracy.
4. **Verifies every piece** afterwards. If the source was watertight and the result is not, MeshFix
   repairs it; if that still fails, the piece falls back to the next engine.
5. **Cleans the assembled result** — merges vertices, drops degenerate and duplicate faces, fills
   any remaining small holes, unifies winding.
6. **Measures deviation** and records everything in the JSON report.

A reduction never hands back a worse mesh than it was given. If something is still wrong, the
Diagnostics panel says so immediately — and <kbd>Ctrl</kbd>+<kbd>Z</kbd> takes you back.

## Going really low-poly

The presets go down to **500 faces**. Below a few hundred, use *Smart retopo* — quadric collapse
produces increasingly ugly slivers at extreme ratios, while a quad field degrades gracefully.
Check the deviation figure: at low-poly targets it is the only honest measure of what you lost.
