# Wax & Metal — making an AI-generated ring castable

**Status:** proposal, nothing built
**Version:** draft 1, 25 September 2026
**Author:** Geekatplay Studio · Vladimir Chopine

---

## 1. The problem

Image-to-3D services — Tripo, Meshy, Hitem3D and the rest — will produce a ring from a
sketch in under a minute, and the result looks right in a render. It is almost never
wearable or castable:

| What arrives | Why it matters |
|---|---|
| The bore is an oval, not a circle | It will not go on a finger, and there is no "size" to quote |
| The bore is whatever size the generator felt like | Nobody's finger is 17.89 mm |
| Square-cut edges inside the bore | A sharp inner rim cuts into the finger; every real band is relieved |
| Band thinner than 1 mm in places | It will not fill in casting, and it bends in wear |
| Detail finer than the metal can hold | Prints beautifully in resin, disappears in silver |
| No shrinkage allowance | The cast ring comes out a size and a half small |

Generic STL repair — including the auto-repair built into Meshy and Tripo themselves —
closes holes and fixes normals. **None of it knows what a ring is.** It will happily
hand back a watertight, manifold, perfectly non-wearable oval.

That gap is what this is for.

## 2. Who it is for, and how small that is

Jewellers and hobbyists who generate designs with AI and cast them through lost-wax,
printing a castable-wax master on a resin printer. This is a **small market on purpose**
— it is a wedge, not the main business.

So the design constraint that follows from it: **the jewellery panel is collapsed by
default and does not intrude on anyone else's workflow.** A person printing miniatures
should never have to look at ring sizes. Someone who opens it once and picks their
printer should find it open next time and never have to think about it again.

## 3. What it does

One panel, **Jewellery (wax casting)**, collapsed until opened. Inside it, for a ring:

### 3.1 Measure — always, and honestly

| Measurement | How | Proven |
|---|---|---|
| Which way the finger goes | Largest principal moment of inertia — a ring's mass all sits at one radius from that axis | 0.01 s, correct on a model tilted 23° |
| Bore diameter, all the way round | Rays fired outward from the axis, first hit | Exact: read 16.40 / 17.90 mm off a bore built as 16.4 × 17.9 |
| Out-of-round | max − min of those radii | Caught 1.50 mm (8.8%) on the test ring |
| Ring size | ISO 8653: size = inner circumference in mm, taken at the **narrowest** point | Reported ISO 54, US 6.8 |
| Band thickness | Rays fired inward at every face (the printability check already does this) | Already in the product |
| Sharp edges | Share of adjacent faces meeting above 55° | 13% on a square-cut section |

### 3.2 Correct — each one optional, each one reversible

**Fit the bore** — the core operation. Rather than trying to repair the generator's oval,
subtract a mathematically perfect one. A D-profile solid of revolution, sized from the
ISO table, is booleaned out of the model. That fixes the circle, the size and the inner
edge relief **in one operation**, because a comfort-fit profile *is* a rounded inner edge.

Measured on the test ring: bore came out at exactly **54.0 mm circumference as asked**,
0.03 s, watertight.

> The dome goes narrowest-in-the-middle, flaring at the rims. I built it the other way
> round first: the ring then grips at the edges — the exact thing comfort fit exists to
> avoid — and the size reads wrong, because a ring's size is set by its narrowest point.

**Thicken where it is too thin** — flag anything under the casting minimum, and offer to
grow the outer surface (never the bore) to reach it.

**Round the outer edges** — optional, and with a warning attached. See §6.

**Scale for shrinkage** — one multiplier applied last, so the cast metal comes out the
size that was asked for. See §5.

### 3.3 Report

A verdict in the same shape as the printability check: what is wrong, how much, where on
the model, and what it would take to fix. Nothing is changed without being asked.

## 4. The numbers, and where they come from

These go in a table in the code with their sources beside them, not scattered as magic
constants.

| Rule | Value | Source |
|---|---|---|
| Ring size definition | Inner circumference in mm | [ISO 8653:2016](https://www.iso.org/standard/16029.html) |
| US size step | 0.81 mm diameter / 2.55 mm circumference per whole size | [Ring size](https://en.wikipedia.org/wiki/Ring_size) |
| Ring band minimum wall | **1.0 mm** (both gold and silver) | [i.materialise gold](https://i.materialise.com/en/3d-printing-materials/gold/design-guide), [Materialise silver](https://www.materialise.com/en/academy/industrial/design-am/silver) |
| General wall minimum | 0.8 mm gold, 0.6–0.8 mm silver | same |
| Absolute floor, lost-wax | 0.35 mm | [Materialise lost-wax guidelines](https://www.materialise.com/en/academy/industrial/design-am/gold) |
| Prong diameter | ≥ 0.8 mm | [PriceScope](https://www.pricescope.com/community/threads/what-is-the-minimum-size-prongs-that-can-be-wax-cast.211740/) |
| Engraving depth | ≥ 0.3 mm on surfaces that rub; depth:width ≤ 1:1 | Materialise guidelines |
| Sunken detail, to be visible | 0.4 × 0.4 × 0.4 mm | Materialise guidelines |
| Comfort-fit profile | D-shape, roughly 2 mm wide × 1.8 mm tall | [Ring design guidance](https://misterjewel.com/3d-jewelry-design-technical-tips-for-superior-quality-in-manufacturing-and-casting/) |
| Comfort fit sizing | Fits about half a size looser than flat fit | [Larson Jewelers](https://www.larsonjewelers.com/pages/comfortfit) |
| Print + cure shrinkage | 0.8% rising to ~1.25% at plateau, measured on an 18 mm bore | [Liqcreate](https://www.liqcreate.com/supportarticles/linear-shrinkage-of-3d-printing-resins/) |
| Casting shrinkage | ~2.6% average, material dependent | [3dprinting.com](https://3dprinting.com/resin/best-castable-resin/) |
| Supports | Outer bottom of the shank; **never inside the bore** | [ifun3d](https://ifun3d.com/blog/3d-printing/resin/3d-printed-rings-vs-pendants-jewelry-resin-guide) |

### The important distinction

There are **two different minimum feature sizes**, and the product already gets one of
them right:

1. **What the printer can resolve** — 18 µm on an Elegoo Mars 4 Ultra. Already measured
   by the existing print check, against 216 machines.
2. **What survives casting in metal** — 0.35 mm absolute, 1.0 mm for a band. That is
   **twenty times coarser**, and it is the limit that actually binds.

A ring can pass the print check perfectly and still come out of the kiln as a puddle.
The jewellery check is the stricter one, and must be shown as a separate verdict rather
than folded into the existing score.

## 5. Shrinkage: the one that loses rings

Print-and-cure shrink stacks on top of metal shrink. Published figures put the total
around 2–4%, but it depends on the resin, the investment, the metal and the kiln — so a
single hard-coded number would be worse than useless.

**Design:** a per-material compensation field, defaulting to a published figure, with the
arithmetic shown plainly — *"printing at 103.0%: an ISO 54 ring casts as ISO 54"* — and a
note that it should be dialled in with a test cast. It is applied **last**, after every
other correction, and recorded in the export report so a known-good factor can be
reused.

This is the single most valuable number in the feature and the one we cannot know for the
user. It must be theirs to set, and obvious that it is.

## 6. Rounding the outer edges — solved, by Blender

**The voxel approach does not work, and this was measured.** Rounding by closing then
opening a voxel solid does round edges — sharp faces 13% → 0%, watertight, 12 s at 0.1 mm
voxels — but 0.1 mm voxels cannot hold the 0.3 mm engraving the same guidelines demand,
and going fine enough is not affordable: a 20 mm ring at the printer's own 18 µm would be
**1.4 billion voxels**.

**Blender's angle-limited Bevel modifier does work**, and by a wide margin:

| | Voxel rounding | Blender bevel |
|---|---|---|
| Sharp faces | 13% → 0% | 35% → **0%** |
| 0.3 mm engraving | blurred away | **100% survived, 0.300 mm** |
| Faces produced | 259,112 | **13,104** |
| Round trip | 12 s | **1.5 s** |
| At 125,952 faces in | — | **1.8 s**, 194,880 out, watertight |

Because the bevel is limited by edge angle, it rounds the hard corners and leaves flat
faces and shallow detail untouched — which is precisely what the voxel filter could not
do, and precisely what a jeweller means by "take the sharpness off".

This moves outer-edge rounding **out of Phase 3 research and into Phase 2**.

### Meshwright already talks to Blender

`engine/blend_import.py` runs an installed Blender headless to open `.blend` projects,
and `find_blender()` locates it. The same bridge runs a modifier stack on any mesh. What
this opens up beyond bevelling:

- **Solidify** — thicken a band that is under the casting minimum, outward only
- **Remesh** and the exact **Boolean** solver, as alternatives where trimesh struggles

### What depending on it costs

- **Blender must be installed.** It already must be, for `.blend` import — but that is an
  optional format and this would be a core operation. The fallback has to be graceful, in
  the pattern already used for a missing PyMeshLab: say what is needed, say which copy of
  Meshwright lacks it, and point at what still works.
- **Its Python API moves between versions.** The STL operator used here,
  `bpy.ops.wm.stl_import`, is Blender 4.2 and newer; before that it was
  `bpy.ops.import_mesh.stl`. A version probe and a fallback are required, and this is the
  main ongoing maintenance cost.
- **About 1.5 s per launch**, nearly all of it startup — the bevel itself took 0.02 s.
  Fine for a committed operation with a progress toast; too slow to drag a slider against.
  Preview approximately in the viewport, commit for real.
- **Licence.** Blender is GPL-3. Driving an installed copy as a separate process is
  arm's-length and fine; bundling it into the installer would not be.

## 6a. What we should still not pretend to do

**Stone settings, prongs and bezels** are out of scope. Checking a prong is one thing;
building or repairing one is jewellery CAD, and MatrixGold already exists.

**We do not become a design tool.** The generator designs. This makes what it produced
manufacturable.

## 7. Has anyone done it

| Who | What they do | Gap |
|---|---|---|
| Meshy / Tripo auto-repair | Holes, non-manifold, normals | No idea what a ring is |
| meshcast.app and similar | Generic printability | Same |
| [JewelCraft](https://blender-addons.org/jewelcraft/) (Blender, free, open source) | Ring sizing, gems, prongs, weight in alloys | A **design** add-on inside Blender; does not take a broken mesh and correct it |
| MatrixGold, RhinoGold, 3Design, JewelCAD | Full professional jewellery CAD | Design from scratch, thousands of pounds, steep |

Nobody is taking AI output and making it castable against jewellery rules. The wedge is
real and narrow.

## 8. Libraries — what exists, what is usable

| Library | Offers | Verdict |
|---|---|---|
| **OpenVDB 13** | A real level-set `Fillet` that rounds only concave regions, plus `Offset` | **No pip wheel.** Would have to be vendored or built. Best-in-class, not practical now |
| **MeshLib 3.1.4** | Mesh-to-SDF, offsetting, "double-offset blending for seamless fillets" | **Free for non-commercial use only** — unusable in a distributed product |
| **CadQuery 2.8 / build123d 0.13** | Real B-rep fillets and chamfers via OCCT | Cannot fillet an arbitrary AI mesh. **Useful for generating the parametric bore and cutters** |
| **An installed Blender** | Angle-limited Bevel, Solidify, Remesh, exact Booleans | **The answer for edge rounding — measured, §6.** Already reachable through `engine/blend_import.py` |
| **libigl 2.6.3 / pygalmesh / VTK** | Geometry processing | Available; nothing needed from them yet |
| **Already in the product** | trimesh, manifold3d (booleans), scipy (distance transforms), scikit-image (marching cubes), embreex (rays), PyMeshLab | **Everything Phase 1 and 2 need is already installed** |

No new dependency is required to build the valuable part.

## 9. What it takes

Sizes are relative to what exists; the pattern the codebase already follows is
engine module → tests → service → API → panel → drive the real window.

### Phase 1 — Measure and report *(the whole value, none of the risk)*
- `engine/jewellery.py`: axis, bore profile, ovality, size, thickness against casting rules
- Casting-rule table with sources
- Verdict in the existing issue shape, so faults highlight on the model like every other
- Panel, collapsed by default, with ring size in ISO / US / UK
- **Proven feasible end to end. All components measured.**

### Phase 2 — Correct
- Parametric comfort-fit bore by boolean subtraction *(proven: exact size, 0.03 s)*
- Thicken-to-minimum where the band is too thin
- Shrinkage compensation, applied last, shown as arithmetic
- Edge rounding through Blender's bevel, with a graceful refusal when Blender is absent
  *(proven: detail preserved, 1.8 s on a 126k-face ring)*
- Each an undoable state, as every operation already is

### Phase 3 — Only if wanted
- Other items: pendants, bands, bangles. The measurement frame generalises; the bore does not
- A weight estimate in silver / gold / platinum, which JewelCraft shows people want

### What is already there and does not need building
- Resin printer profiles, including the user's Elegoo, with real pixel pitch
- Thickness measurement by ray casting, with the two-measurement honesty rule
- Boolean unions via manifold3d, voxel remesh, decimation, undo/redo, crash recovery
- Per-piece selection and operations, and the right-click menu to hang actions on

## 10. Open questions

1. **Which metals to ship rules for?** Silver and gold cover most of it; platinum and
   brass differ. One table, extendable.
2. **Where does the panel live?** A collapsed card beside *Print check* is the obvious
   place — both answer "will this survive manufacture?"
3. **How is the ring size chosen** — a size picker (ISO/US/UK), a target diameter, or
   "keep what it is, just make it round"? All three are reasonable; the third may be the
   most common.
4. **Should the check refuse non-rings?** A pendant run through ring measurement gives
   nonsense. Detecting "is this ring-shaped" needs a confidence test and a graceful
   refusal.
5. **Is a test-cast calibration record worth keeping** — remembering that this resin plus
   this investment needed 103.2%?

## 11. How we would know it works

Not by "it looks right". The same standard the rest of the product is held to:

- A ring built with a **known** bore measures back to that bore, within a hundredth of a
  millimetre *(already demonstrated: 16.40 / 17.90 read off exactly)*
- A corrected bore measures the **exact circumference asked for** *(demonstrated: 54.0)*
- A corrected ring is watertight, is one solid, and lost no volume it should have kept
- Thinning the band by enlarging the bore is **caught and reported**, not silently allowed
  *(it happened in testing — the wall fell to 0.95 mm, under the 1.0 mm minimum)*
- Every published figure in the rules table traces to a source in this document
- Driven in the real window, on a real generated ring, before it ships
