# mak/: MAK VR-Forces 5.1.1 export

Turns an export package's ship (contract §8) into a VR-Forces entity, **without Blender**. This is the "MAK
adapter" of `docs/PLAN.md`. Contract: §9b (script and report) and §10 (`Project.mak`, endpoints).

Origin: copied from the `vrf-pipeline` project, branch `feat/openflight-writer`, commit `269398c`
(2026-11-14). That project's MAK manuals and hand-import fixtures are not copied here: they are MAK material and
this repository is public. `references/` cites manual sections instead.

## What it writes

`python mak/run_mak_export.py <package_dir>` writes `<package_dir>/mak/` and `<package_dir>/mak_export_report.json`:

| File | Role |
|---|---|
| `SharedData/ModelData/Vehicles/Surface/<name>/<name>.flt` | OpenFlight 16.1 model. Each moving part with a DIS code is a DOF node with `@dis articulated_part <code> ...` (the format MAK's own models use); material colours ride on the faces |
| `sms/gui/visuals/UserData/<name>.ommx` | model definition (schema `Watercraft`, `filename` only) |
| `sms/gui/visuals/Entity/<name>.leaf` | element definition: the donor's, renamed, 3D and XR models pointed at ours |
| `sms/gui/visuals/Entity/<name>.metx` | DIS type -> element definition |
| `sms/vrfSim/<display name>.entity` | simulation object, cloned from the donor entity (systems, signatures), with our type, parts, size and labels |
| `visual-import/` | the three definition files together, for Simulation Object Editor > File > Import Visual Models |

This layout is what the Simulation Object Editor itself wrote in a hand import of the same ship (2026-11-14):
the `.metx` and `.ommx` match exactly, and the `.leaf` matches line for line apart from the model references.

## Checks in the report
The input glb (scale, origin, part pivots and hierarchy); the written `.flt` re-read (parts, pivots, bounds, colours,
DOF limits and comments); **MAK's own reader** (`medfTool --writeNodeInfo`) loads it and sees every part; the
definitions cross-reference; the `.entity` is consistent (type, parts, systems).

## Installing into VR-Forces
`python mak/install.py <package_dir>/mak <sms>` (or Review > MAK > Install in the editor) copies the model into
SharedData and the definitions and `.entity` into a **custom** SMS made in the Simulation Object Editor (include
EntityLevel). Stock SMSs are refused. Replaced files are backed up first. Then open the SMS in the editor.

## Settings
`ME3D_VRF_DIR` (default `C:\MAK\vrforces5.1.1`), `ME3D_MAK_SHARED_DATA` (default `C:\MAK\SharedData\18\latest`),
`ME3D_MAK_FORMAT` (`flt`, or `fbx` to convert with Blender instead).

## Files
- `run_mak_export.py`: the §9b script. `from_package.py`: package -> pipeline manifest (joint axes, codes, crew
  listed as not exported). `draft_mak.py`: suggestions for the Review panel. `install.py`: install into an SMS.
- `pipeline/`: inspect -> convert -> verify -> generate -> validate (`glb.py`, `openflight.py`, `leaf.py`, ...).
- `references/`: articulation rules, file formats, checklist, open questions.
- `scripts/export_hand_import.ps1`: collects a hand import from a VR-Forces machine, for comparison.
- `tests/`: `server/.venv/Scripts/python -m pytest mak/tests -q` (tests needing VR-Forces skip without it).
