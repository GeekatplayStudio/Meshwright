# Third-party licences

Meshwright itself is **MIT** (see [LICENSE](../LICENSE)). It builds on the work below — with thanks
to every author. Versions are whatever `pip` installs; the in-app About panel shows exactly what is
present on your machine.

## Permissive — no restriction on redistribution

| Component | Licence | Source |
|---|---|---|
| trimesh | MIT | https://github.com/mikedh/trimesh |
| QuadriFlow | BSD-3-Clause | https://github.com/hjwdzh/QuadriFlow |
| pyQuadriFlow (wrapper) | MIT | https://github.com/satabol/pyQuadriFlow |
| Manifold3D | Apache-2.0 | https://github.com/elalish/manifold |
| fast-simplification | MIT | https://github.com/pyvista/fast-simplification |
| NumPy | BSD-3-Clause | https://numpy.org |
| SciPy | BSD-3-Clause | https://scipy.org |
| scikit-image | BSD-3-Clause | https://scikit-image.org |
| Three.js | MIT | https://threejs.org |
| pywebview | BSD-3-Clause | https://pywebview.flowrl.com |
| ufbx | MIT | https://github.com/ufbx/ufbx |
| MCP Python SDK | MIT | https://github.com/modelcontextprotocol/python-sdk |
| Pillow | MIT-CMU | https://python-pillow.org |

## Copyleft — read before redistributing binaries

| Component | Licence | Source |
|---|---|---|
| PyMeshLab / MeshLab | GPL-3.0 | https://github.com/cnr-isti-vclab/PyMeshLab |
| pymeshfix / MeshFix | GPL-3.0 | https://github.com/pyvista/pymeshfix |

**What this means**

- Running Meshwright with these installed is fine, for any purpose including commercial work.
- Distributing a **bundled binary** that includes them makes the combined work subject to the GPL-3.
- Both are **optional**. If they are missing, Meshwright detects it and the pipeline degrades
  gracefully: repair falls back to trimesh and Manifold3D, and reduction falls back to
  fast-simplification. The About panel shows which engines are actually present.

To build a fully permissive distribution, omit `pymeshlab` and `pymeshfix` from
`requirements-optional.txt` (or install with `install.bat -NoOptional`). Expect weaker results on
badly broken meshes and on retopology of topologically complex models, where MeshFix is what
repairs QuadriFlow's output.

## Attribution

- **MeshFix** — Marco Attene, *A lightweight approach to repairing digitized polygon meshes*,
  The Visual Computer, 2010.
- **QuadriFlow** — Jingwei Huang, Yichao Zhou, Matthias Nießner, Jonathan Shewchuk, Leonidas Guibas,
  *QuadriFlow: A Scalable and Robust Method for Quadrangulation*, SGP 2018.
- **MeshLab** — Cignoni et al., *MeshLab: an Open-Source Mesh Processing Tool*, 2008.
- **Manifold** — Emmett Lalish et al.
