# Building the Windows program and installer

`Meshwright.exe` is the program frozen with [PyInstaller](https://pyinstaller.org) so that it runs
on a PC with no Python. `Meshwright-Setup-<version>.exe` is an [Inno Setup](https://jrsoftware.org/isinfo.php)
installer around it. One command builds both.

```powershell
.venv\Scripts\python -m pip install -r requirements-build.txt     # once
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```

You need the project's `.venv` (from `install.bat`) and, for the installer, Inno Setup 6
(`winget install JRSoftware.InnoSetup`). Without Inno Setup the build still produces and tests
`dist\Meshwright\Meshwright.exe`; it just skips the installer and says so.

| Option | |
|---|---|
| `-Edition full` | Default. Everything, including PyMeshLab and pymeshfix. |
| `-Edition lite` | The same program without those two engines. See *Editions*. |
| `-Fast` | A quick, larger installer for testing the build itself. **Never publish one.** |
| `-SkipInstaller` | Stop after the frozen program and its self-test. |
| `-NoWebView2` | Leave Microsoft's WebView2 installer out of the setup. Not recommended. |

Output goes to `dist\`. The whole thing takes about ten minutes; almost all of it is PyInstaller.

## What the build does, and what it refuses to do

1. Stamps the version (from `engine/version.py`, the one place it is written) onto the exe.
2. Freezes the program (`packaging/meshwright.spec`).
3. Writes `THIRD_PARTY_NOTICES.txt` from what was **actually bundled**, with each licence's text.
   MIT and BSD licences require that text to travel with a binary; a hand-kept list drifts.
4. **Runs the frozen program's own self-test with only Windows on `PATH`**, and fails the build if
   anything in it fails. Nothing is published that was not proven to work.
5. Downloads Microsoft's WebView2 installer and embeds it **only if it carries a valid Microsoft
   signature**.
6. Compiles the installer.

### The self-test

```powershell
dist\Meshwright\Meshwright.exe --selftest-out report.json
```

It is the only practical way to check a windowed program, and it exists because packaging failures
are quiet ones. A helper process that cannot start does not raise: retopology just uses another
engine and returns a good-looking mesh. So each step asserts *which* engine did the work. The 20
steps cover the window stack, both helper processes, every mesh engine, texture processing, the file
browser's previews, the printer check, all three export formats and the MCP server.

It is also what to send to someone whose copy misbehaves: `report.json` says exactly what is present
and what failed. From source, `python -m pytest tests/test_runtime_packaging.py` runs the same steps.

## Editions

**full** contains PyMeshLab and pymeshfix, both licensed GPL-3. Running Meshwright with them is fine.
*Distributing* a bundle that contains them means that bundle is offered under the GPL-3 — Meshwright's
source is public on GitHub, and `THIRD_PARTY_NOTICES.txt` says where the source of each GPL component
lives. That is a decision about how you want to distribute, not a technical one.

**lite** leaves both out. Everything still works — it repairs, decimates and retopologises with
trimesh, Manifold3D, QuadriFlow and fast-simplification, and the self-test verifies that — but two
things change, and they are worth knowing before you choose.

- **Repair seals holes by rebuilding, not patching.** MeshFix closes a hole in place; without it the
  repair falls through to the voxel rebuild, which re-samples the whole surface. Measured on a detailed
  40 mm model with 465 faces torn out: the two are about equally accurate (largest distance from the
  original surface 1.07 mm with MeshFix, 0.80 mm with the voxel rebuild), but the voxel rebuild took
  4.7 s instead of 0.4 s and left **463,496 faces instead of 18,870**. Reduce the result afterwards.
- **Uniform and Decimate reduction are unavailable**, because both run in MeshLab. Choosing either
  says so and points to Smart retopology, which works without it (QuadriFlow, and the viewport's
  display simplification, are unaffected).

The self-test reports the three steps that lite skips (`skipped ... is not part of this build`), so a
lite build is never mistaken for a broken full one.

## How a windowed program runs its helper processes

QuadriFlow and xatlas run in a child process so that a native crash becomes an exit code rather than a
closed window. From source the child is `python worker.py`. Frozen there is no Python, and
`sys.executable` is `Meshwright.exe` itself, so the same exe is started again with
`--meshwright-worker <name> <in> <out>` and `packaging/launcher.py` hands it to the right job before
anything heavy is imported (`engine/runtime.py`). A new helper needs an entry in `runtime.WORKERS` and a
hidden import in the spec.

## Size

About 415 MB installed and a 112 MB installer (full edition, measured). Almost all of it is PyMeshLab (130 MB, its own copy
of Qt), OpenCV (83 MB) and SciPy (70 MB with its libraries). OpenCV stays deliberately: filling the gutters
of a 4096 px texture takes 1.5 s with it and 18 s without. Only its 30 MB video codec is dropped.

## Testing the installer safely

**Do not test an installer or uninstaller against your real `%LOCALAPPDATA%\Meshwright`.** That folder
holds crash-recovery snapshots, which after a crash can be the only copy of someone's unsaved work.

An earlier version of the uninstall rule deleted that whole folder. Inno Setup *merges* the previous
install's uninstall log when you install over it, so upgrading to a corrected installer did not remove the
old rule — and uninstalling deleted the folder anyway. The rule is now non-destructive (it removes the
folders only if they are already empty), but the way to test it is the same either way:

1. Park the real folder by renaming it (`Meshwright` → `Meshwright.parked`).
2. Create a decoy `sessions\DECOY\journal.json` and a `startup-error.txt` in a fresh `Meshwright` folder.
3. **Clean-install** (not an upgrade), then uninstall: `Meshwright-Setup-x.y.z.exe /VERYSILENT /NOICONS /DIR="C:\temp\mw"`
   and `C:\temp\mw\unins000.exe /VERYSILENT`.
4. The decoy session must still be there; only `startup-error.txt` should be gone.
5. Delete the decoy, rename the parked folder back.

## Things worth knowing

- **Per-user by default.** The installer needs no administrator rights and no UAC prompt; it installs to
  `%LOCALAPPDATA%\Programs\Meshwright`. A dialog offers "all users".
- **WebView2.** Meshwright draws its window with the Microsoft Edge WebView2 runtime. Windows 11 has it; some
  Windows 10 machines do not. The installer installs it only if missing. Without it pywebview does *not*
  fail — it silently falls back to Internet Explorer's engine, which cannot run the interface — so the
  program checks for that and shows a message with the download link instead.
- **Not code-signed.** Windows SmartScreen may warn on first run ("More info" → "Run anyway"), and some
  antivirus products are suspicious of any PyInstaller program. A code-signing certificate is what fixes it;
  submitting the installer to Microsoft Defender's false-positive form helps in the meantime. The build uses
  a folder, not a single-file exe, partly for this reason: a one-file exe unpacks ~450 MB to a temp folder on
  every launch (10–20 s) and is what antivirus products dislike most.
- **Visual C++ runtime** is bundled with the program, so it does not depend on the redistributable being installed.
- **Claude Desktop / MCP:** `Meshwright.exe --mcp` speaks MCP on stdin/stdout. Point the client at the installed exe.
- **Not on this machine's PATH.** Nothing is added to `PATH`; the shortcut and `Meshwright.exe` are the entry points.
- **Last gate: a clean PC.** The build tests with a stripped `PATH`, which catches everything except what only a
  truly clean Windows would: run the installer once on a machine (or VM) that has never had Python or this project.
