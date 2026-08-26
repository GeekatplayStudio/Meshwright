# Diagnostics reference

Every check Meshwright runs, what it means for printing, and how to fix it.
Click any issue in the Diagnostics panel to locate it on the model.

Severity: **critical** — will fail or misprint · **warning** — likely artifacts · **note** — worth knowing.

---

## Open holes — *critical*

Boundary edges belong to only one triangle, so the surface is not closed. A slicer cannot tell
inside from outside and will produce missing walls or a hollow shell.

*Reported as:* number of holes (closed loops of boundary edges) and total open edges.
*Fix:* **Repair mesh**. Small holes are triangulated directly; larger ones go to MeshFix or MeshLab.

**In the slicer this is the “thin shell” symptom.** A model with open edges is not a solid, so the
slicer prints a single wall with no infill — one perimeter thick — no matter what the infill setting
says. Meshwright repeats the warning when you export, but the diagnostics panel has already told
you: if this check is present, repair before exporting. A model is only ready when the panel reports
it as watertight.

## Non-manifold edges — *critical*

An edge shared by three or more faces. The solid is ambiguous — there is no single "inside".
Usually comes from overlapping geometry or booleans that were never cleaned up.

*Fix:* **Repair mesh** (MeshFix / MeshLab stages). If the count is high, the model may be a
triangle soup; `Force watertight` will rebuild it via voxels as a last resort.

## Inconsistent face winding — *warning*

Neighbouring triangles are wound in opposite directions, so normals flip across the surface.
Slicers may treat regions as inverted.

*Fix:* **Repair mesh** — the Orientation stage unifies winding.

## Inside-out mesh — *warning*

All normals point inward (negative volume). A slicer treats the model as a cavity.

*Fix:* **Repair mesh** — the Orientation stage flips it.

## Degenerate triangles — *warning*

Zero-area faces from collapsed or collinear points. They carry no normal and confuse slicers
and boolean engines.

*Fix:* **Repair mesh** (Cleanup stage) removes them.

## Duplicate faces — *warning*

Exact copies of another triangle, often from merged imports. They create doubled walls.

*Fix:* **Repair mesh** (Cleanup stage).

## Duplicate vertices — *note*

Two vertices share a position but are not merged, so edges that look joined are not connected.
This is the usual hidden cause of "holes I cannot see".

*Fix:* **Repair mesh** merges them.

## Unused vertices — *note*

Vertices not referenced by any face. Harmless, but they inflate the file.

*Fix:* **Repair mesh** (Cleanup stage).

## Sliver triangles — *note*

Triangles with a minimum angle under 1°: needles (one very short edge) or caps (one angle near 180°).
They survive slicing but cause shading artifacts, unreliable normals and problems for booleans
and further remeshing.

*Fix:* **Fix sliver triangles** — needles are collapsed onto the better-connected vertex and caps
are flipped with their neighbour, so surrounding geometry stays put. The button only appears when
slivers are present.

Every collapse and flip is checked first: a collapse must satisfy the *link condition* (the two
endpoints share exactly the two vertices opposite the edge) and a flip must not duplicate an existing
edge. Slivers that cannot be removed without tearing the surface are left in place and reported —
a handful of stubborn slivers is far better than a hole. They are harmless for printing.

## Separate shells — *note*

The file contains several disconnected pieces. Often intentional (a miniature with gems and a base),
sometimes stray fragments from a bad export.

*Fix:* the **Separate pieces** panel lists each piece with its triangle count and size, colour-codes
them in the viewport, and lets you delete the ones you do not want.

## Unusual scale — *note*

The largest dimension is under 1 mm or over 1000 mm — the source file probably uses different units
(metres, centimetres or inches are common).

*Fix:* pick the correct **Source units** in the Export panel; Meshwright scales on export.

## No geometry — *critical*

The file contained no triangles at all. Nothing to print — check the export from your 3D tool.

---

## Hollow models and wall thickness

Nothing is wrong with a watertight model whose walls are 0.4 mm thick — but a printer cannot fill
it, so it comes out as a shell. Meshwright estimates the average wall as `2 × volume ÷ surface area`
when you export, and says so when that falls under 1.2 mm on a model larger than 10 mm.

Usual causes: a surface exported from CAD with no thickness applied, a photogrammetry scan that is a
skin rather than a body, or a model already hollowed for resin printing (in which case it is
correct — check that it has drain holes).

## The readiness score

Start at 100, then subtract 35 per critical issue, 12 per warning, 3 per note. A watertight,
consistently wound, correctly oriented mesh never scores below 80.

| Score | Verdict |
|---|---|
| no issues | **Print ready** |
| any critical | **Repair required** |
| any warning | **Repair recommended** |
| notes only | **Printable, minor cleanup available** |
