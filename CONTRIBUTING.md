# Contributing to Meshwright

Thanks for helping improve Meshwright — Geekatplay Studio, Vladimir Chopine.

## Setup

```bash
git clone https://github.com/GeekatplayStudio/Meshwright.git
cd Meshwright
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
npm install                 # vendors Three.js, installs eslint
```

Always work inside the `.venv`. Meshwright depends on numpy, scipy and several compiled mesh
libraries; installing them into a shared interpreter can upgrade numpy underneath other projects.
Keep the bounds in `requirements.txt` — an unbounded `numpy>=x` is how that happens.

Three.js is vendored into `ui/vendor/` by `npm install` (see `install.ps1`). If the viewport is
blank, re-run the installer — the app never loads scripts from a CDN, so it works offline.

## Running

```bash
.venv\Scripts\python app.py         # desktop app  (or start.bat)
.venv\Scripts\python mcp_server.py  # MCP server   (or run-mcp.bat)
```

## Tests and linting

```bash
npm test              # pytest with coverage
npm run lint          # eslint (ui/js) + ruff (python)
```

Everything must pass before a pull request. Add a test with every behaviour change — the suite is
the reason the safety guarantees hold.

## Architecture in one paragraph

`engine/` holds all logic and knows nothing about any UI. `engine/service.py` is the single entry
point: it validates input (`engine/validation.py`), runs the operation, commits an immutable
numbered state, snapshots it for crash recovery, and refuses results that are measurably worse.
`app.py` is a thin pywebview adapter and `mcp_server.py` a thin MCP adapter over that same service.
Adding a feature means adding it to the service — both front ends get it. See
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Style

- Python: ruff-clean, 4-space indent, type hints on public functions, docstrings that say *why*.
- JavaScript: eslint-clean, no frameworks, no build step, no external requests at runtime.
- Comments explain intent and non-obvious trade-offs, not what the line already says.

## Reporting bugs

Please include the model (or one that reproduces it), the **Activity console** output, and the
JSON report (`Diagnostics → Save JSON`). Those three make almost any mesh bug reproducible.

## Licence

Contributions are accepted under the MIT licence of this project. Do not add a dependency with a
licence more restrictive than the ones already listed in [docs/LICENSES.md](docs/LICENSES.md)
without discussing it first.
