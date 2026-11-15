# glb to MAK VR-Forces importer: technical document

How the importer that turns a glTF binary model (`.glb`) into a VR-Forces 5.1.1 simulation object was built,
how to run it, what it supports, what it assumes, and what is still open.

Audience: an engineer who will run, maintain or extend it. Status as of 2026-11-15: branches `feat/mak-export` and
`feat/mak-kinds` of this repository; standalone project `vrf-pipeline`, branch `feat/model-kinds`.

> **Not yet verified end to end in VR-Forces for anything but one surface ship.** Every other model kind is
> generated, checked by MAK's own model reader and unit-tested against the stock install, but has not been
> compared with a hand import in the Simulation Object Editor. See [Limitations](#9-limitations).

References: **ACM** = *Adding Content to MAK ONE Applications* reference manual; **UG** = *VR-Forces 5.1.1
Users Guide*. Section numbers only; no manual text is reproduced here (licence). "The install" means the stock
VR-Forces 5.1.1 content: the `EntityLevel` simulation model set and `SharedData/18/latest/ModelData`. Where the
manuals and the install disagree, the install wins and the difference is recorded in
`references/open-questions.md`.

---

## Contents
1. [Purpose and scope](#1-purpose-and-scope)
2. [Architecture](#2-architecture)
3. [Dependencies and configuration](#3-dependencies-and-configuration)
4. [Output](#4-output)
5. [Usage guide](#5-usage-guide)
6. [Supported features](#6-supported-features)
7. [Assumptions](#7-assumptions)
8. [Verification and tests](#8-verification-and-tests)
9. [Limitations](#9-limitations)
10. [Outstanding work](#10-outstanding-work)
11. [Appendix: glossary and manual index](#11-appendix)

---

## 1. Purpose and scope

VR-Forces cannot load glTF. A new simulation object needs a model in OpenFlight or FBX (ACM 14.2) plus four
definition files that tie a DIS entity type to that model and give the simulation its behaviour. The importer:

- converts the glb to **OpenFlight 16.1** in pure Python (no Blender), with moving parts as DOF nodes that carry
  MAK's own `@dis articulated_part` comments;
- writes the model definition, element definition, type map and simulation object (`.entity`) in the layout the
  Simulation Object Editor (SOE) itself writes;
- checks the result: re-reads the OpenFlight file, has **MAK's own reader (`medfTool --writeNodeInfo`)** load it,
  and cross-validates the definitions and `.entity`;
- never writes into the VR-Forces install, except through an explicit, confirmed install into a **custom**
  simulation model set (SMS), with backups.

Two entry points share one code base:

| Entry point | Input | Used by |
|---|---|---|
| `vrf-pipeline` CLI | a model folder: `model.glb` + `manifest.json` (+ optional Blender exports) | anyone with a glb |
| Editor MAK export | an `me3d.package/1` export package (CONTRACT §8) + the ship project's MAK settings | Review > MAK in the editor |

## 2. Architecture

### 2.1 Stages

```
model.glb + manifest.json
   |
   |  inspect      inspect_glb.analyse/check: bounds, node tree, pivots, origin and part rules per kind
   v
   |  supports     glb.add_supports: waterline or ground-contact support points from the geometry
   v
   |  convert      glb.load -> openflight.write: geometry, face colours, textures + effect maps, DOF nodes + @dis comments
   v               (fbx route: headless Blender instead)
   |  verify       openflight.read_flt + check_flt (re-read), openflight.check_mak (medfTool loads it)
   v
   |  package      medf.package: medfTool writes <name>.medf + .meif textures beside the model (ACM 19.19)
   v
   |  generate     .ommx, .leaf (cloned from the donor), .metx, .entity (cloned from the donor),
   v               munition table entry, visual-import/
   |  validate     validate.validate: cross-references, collisions with the install, .entity consistency
   v
report.json  (every stage's findings: error | warning | info | pending)
```

Stages stop on the first stage with errors, except that `generate` and `validate` run whenever `inspect` passed,
so a report always shows as much as possible. Humans and animals skip inspect/convert/verify (no model; see 6.9).

### 2.2 Modules

Pipeline (`vrf-pipeline/pipeline/`, copied to `mak/pipeline/`):

| Module | Responsibility |
|---|---|
| `common.py` | config, manifest loading and checks, output `Layout`, `Finding` |
| `profiles.py` | **one profile per model kind**: DIS kind/domain, platform `.ope`, ModelData folder, model schema, origin rule, support rule, entity flags, verification status |
| `inspect_glb.py` | glb JSON/bounds analysis without reading vertices; origin, scale, forward-axis and articulation rules |
| `glb.py` | full glb geometry reader (positions, normals, UVs, materials, images) into the MAK frame; `waterline_supports`, `ground_supports` |
| `openflight.py` | OpenFlight 16.1 writer and reader; DOF nodes, `@dis` comments, face colours, textures; `medfTool` check |
| `effectmaps.py` | glTF PBR maps -> MAK effect maps beside each diffuse: `_NML` (normal) and `_MTL_GLS_AO` (metal, gloss, AO) (6.12) |
| `png.py` | PNG reader and writer (standard library), for repacking texture maps |
| `medf.py` | packages the written model with `medfTool` as `.medf` + `.meif` (pre-compressed, mip-mapped textures), which VR-Forces loads instead of the `.flt` and PNGs (ACM 19.19) |
| `artparts.py` | DIS articulated-part codes (ACM Table 58), default motions and limits, FBX node-name map |
| `systems.py` | **systems**: sysdef reader and catalog of the install (categories, platforms allowed, part parameters, ammunition, stock usage), applying the manifest's `systems` to the cloned `.entity`, drafting them from the model, system checks |
| `convert.py` | model conversion entry (OpenFlight, or FBX via Blender) |
| `verify_fbx.py` | compares a written model's dump with the glb (parts, pivots, bounds) |
| `leaf.py` | element definitions: finds the donor's (best-match type lookup), renames and retargets it, Boost serialisation details |
| `generate.py` | writes `.ommx`, `.leaf`, `.metx`, `.entity`; DI-Guy character entities; munition table entries |
| `validate.py` | output validation and collision checks against the install |
| `mtl.py` | reads MAK `.mtl` tables: ammunition entries, DI-Guy character table |
| `draft_manifest.py` | drafts `manifest.json` from Blender exports; donor suggestions |
| `run.py` | runs all stages and writes the report |
| `blender_*.py` | scripts run inside Blender for the optional FBX route |

Editor integration (`mak/`, `server/app/`, `web/src/`):

| File | Responsibility |
|---|---|
| `mak/from_package.py` | `me3d.package/1` + `ship/mak.json` -> a pipeline model folder and manifest (joint axes turned into the ship frame, part codes, kind, names, the systems draft less `systems_off`) |
| `mak/run_mak_export.py` | CONTRACT §9b script: package -> `mak/` output + `mak_export_report.json` |
| `mak/draft_mak.py` | suggestions for the Review panel (DIS type hint, short name, donors, part codes, system changes for the chosen donor) |
| `mak/install.py` | installs an export into a custom SMS, with backups; merges munition table entries |
| `server/app/mak.py` | settings validation, draft, export runner (subprocess, like the Unreal runner), latest export, install |
| `server/app/main.py`, `packaging.py`, `store.py` | endpoints; `ship/mak.json` and `rotation_deg` in the package; `Project.mak` |
| `web/src/components/MakPanel.tsx`, `lib/mak.ts` | Review > MAK: settings, system changes with a checkbox each, export with checks, install dialog |

### 2.3 How the two copies relate

The pipeline was developed in `vrf-pipeline` and copied into `mak/pipeline/`. `mak/README.md` records the source
branch and commit. The copies are identical except:
- `common.py`: the editor copy builds its config from environment variables (`make_config`) instead of
  `pipeline.config.json`;
- `run.py`: `run()` takes `cfg`, `out_dir` and `quiet` so the export script can direct the output.

A change to the pipeline is made in `vrf-pipeline`, tested there, then copied file by file; `common.py` and
`run.py` are patched by hand.

## 3. Dependencies and configuration

### 3.1 Software

| Dependency | Needed for | Notes |
|---|---|---|
| Python 3.10+ | everything | **standard library only**; nothing to install for the pipeline |
| VR-Forces 5.1.1 | real exports | donor `.entity` files, stock definitions (collision checks, donor visuals), `medfTool.exe`, `character_animations_table.mtl`. Unit tests that need it skip without it |
| MAK SharedData 18 (`latest`) | real exports | `ModelData/Configuration/Definitions/Entity` |
| Blender 5.2 | FBX route only | headless; not used by default |
| Editor server venv (FastAPI) | editor integration | `server/.venv` |
| Node.js | editor web app | `web/` |
| Windows PowerShell 5.1 | `scripts/export_hand_import.ps1` | read-only collection of a hand import, on the VR-Forces machine (5.5) |

### 3.2 Configuration

Standalone (`vrf-pipeline/pipeline.config.json`):

| Key | Meaning | Default |
|---|---|---|
| `vrforces_root` | VR-Forces folder | `C:/MAK/vrforces5.1.1` |
| `shared_data_dir` | SharedData version folder | `C:/MAK/SharedData/18/latest` |
| `definitions_dir` | stock element/model definitions | `<shared>/ModelData/Configuration/Definitions/Entity` |
| `sms_dir`, `donor_sms` | where donors are read from | `<vrf>/data/simulationModelSets`, `EntityLevel` |
| `fbx_node_name_map` | FBX route part names | `<vrf>/appData/importConfig/FBX_NodeNameMap.csv` |
| `medf_tool` | MAK model reader | `<vrf>/bin64/medfTool.exe` |
| `model_format` | `flt` (default) or `fbx` | `flt` |
| `blender` | Blender executable | FBX route only |
| `limits` | `max_triangles` 100000, `max_texture_px` 4096, `max_extent_m` 400, `min_extent_m` 0.05 | inspect warnings/errors |
| `effect_maps` (optional) | `{"enabled": true, "normal_green": "up"}` | writing MAK effect maps from the glb's PBR maps (6.12); `"down"` flips the normal map's Y |
| `medf_package` (optional) | `true` | the package stage (`medf.py`); needs `medf_tool` |

Editor (environment variables; contract §9b):

| Variable | Default |
|---|---|
| `ME3D_VRF_DIR` | `C:\MAK\vrforces5.1.1` |
| `ME3D_MAK_SHARED_DATA` | `C:\MAK\SharedData\18\latest` |
| `ME3D_MAK_FORMAT` | `flt` |
| `ME3D_BLENDER` | the standard Blender 5.2 path |

The server's `Settings` also has `mak_timeout_s` (export subprocess timeout) and derives `mak_script`,
`medf_tool` and `mak_backups_dir` (`data/mak_backups/`). `/api/health` reports `mak: true` when VR-Forces and its
`medfTool` are found.

## 4. Output

### 4.1 Files

Written to `output/<name>/` (CLI) or `<package_dir>/mak/` (editor). The layout mirrors the install, so deploying is
a copy.

| # | File | Role | Deploys to |
|---|---|---|---|
| 1 | `SharedData/ModelData/<category>/<name>/<name>.flt` (+ textures and their `_NML` / `_MTL_GLS_AO` effect maps) | the model | `<SharedData>/ModelData/...` |
| 2 | `sms/gui/visuals/UserData/<name>.ommx` | model definition: schema + `filename` | `<sms>/gui/visuals/UserData/` |
| 3 | `sms/gui/visuals/Entity/<name>.leaf` | element definition: the donor's, renamed, 3D and XR main model pointed at ours | `<sms>/gui/visuals/Entity/` |
| 4 | `sms/gui/visuals/Entity/<name>.metx` | DIS type -> element definition | `<sms>/gui/visuals/Entity/` |
| 5 | `sms/vrfSim/<display name>.entity` | simulation object, cloned from the donor | `<sms>/vrfSim/` |
| - | `visual-import/` | files 2-4 together, for SOE File > Import Visual Models (UG 65.2.10) | not installed |
| - | `sms/ammunitionParams.additions.mtl` | munitions only: one ammunition entry | merged into `<sms>/ammunitionParams.mtl` |
| - | `appData/importConfig/FBX_NodeNameMap.additions.csv` | FBX route only: node names for part codes the installed map lacks | appended to the installed map |
| - | `report.json` / `<package_dir>/mak_export_report.json` | findings | - |

Humans and animals have no files 1-2 (no model), and files 3-4 only when the donor has an element definition of
its own.

The `.ommx`, `.leaf` and `.metx` are Boost-serialisation XML; the `.entity` is plain XML. Their structure is
copied from real files in the install, never invented (`references/file-formats.md`).

### 4.2 Names
- `<name>`: the model name. It names the model file, its folder and the `.ommx`/`.leaf`/`.metx`, so it must be
  safe as a file name and as XML text: letters, digits, spaces and `_ - . ( ) +`, starting with a letter or digit,
  not ending in a space or dot (`common.NAME_RE`). Spaces are normal in the install (1143 of 1213 stock element
  definitions have them). CLI: the model folder's name (`draft_manifest`; `spec.json`'s name can be the
  template's, e.g. "Custom ship"). Editor: the ship project's name (`ship/mak.json` `name`), else the ship id.
- `<display name>`: the GUI label and the `.entity` file name. CLI: manifest `display_name`. Editor: the **ship
  project's name** (`ship/mak.json` `name`), cleaned for use as a file name. It is not the `name` in
  `ship/<id>.json`, which is the spec's and keeps the catalog template's name after a rename.
- Short name (DIS marking, 11 characters): manifest `short_name`, else the display name cut at a word boundary.

### 4.3 Report

Pipeline `report.json`: `{"model", "kind", "kind_status", "model_format", "glb", "stages": {stage: [finding]},
"ok", "pending": [code]}`; each finding is `{"level", "code", "message"}`.

Editor `mak_export_report.json` (contract §9b): `{"ok", "vrforces": "5.1.1", "name", "dis_type",
"checks": [{"name": "<stage>:<code>", "pass", "level", "detail"}], "pending": [str], "not_exported": [str]}`. Exit
code 0 only when `ok`. Warning-level checks pass but are shown with `!` and counted in the panel's headline. The
editor's own come first, as `editor:settings`:
- stale saved settings;
- a coded part under an uncoded moving part;
- "Weapons fitted: N of M weapon mounts".

The server adds `editor:server-stale` when it runs older code than is on disk.

## 5. Usage guide

### 5.1 Standalone CLI

Every command takes a **model folder** (`models/<Name>/` with `model.glb` and `manifest.json`), not a `.glb` path.
Passing a `.glb` gives an error that explains the folder layout and the next command.

| Command | Options | Does |
|---|---|---|
| `python -m pipeline.draft_manifest models/<Name>` | `--kind <kind>`, `--munition-class <class>`, `--force` | drafts `manifest.json` from Blender exports (`parts.json`, `spec.json`, `build_report.json`); writes `manifest.draft.json` if a manifest exists and `--force` is not given; fields it cannot infer are `TODO...` |
| `python -m pipeline.run models/<Name>` | `--format flt\|fbx`, `--skip-convert`, `--no-mak-check`, `--no-medf` | all stages; writes `output/<Name>/report.json` |
| `python -m pipeline.inspect_glb <file.glb>` | `--manifest <path>`, `--json` | inspect only |
| `python -m pipeline.generate models/<Name>` | | definitions and `.entity` only |
| `python -m pipeline.validate output/<Name>` | `--json` | validation only |
| `python -m pipeline.mtl characters [filter]` | | lists DI-Guy characters for humans/animals |
| `python -m unittest discover -s tests -v` | | test suite |

Exit codes: `0` no errors (pending items allowed), `1` errors, `2` bad input (with a sentence saying what to do).

Typical flow:
```
python -m pipeline.draft_manifest models/MyShip            # if Blender exports are present
# fill every TODO in models/MyShip/manifest.json
python -m pipeline.run models/MyShip
```

### 5.2 Manifest reference

| Key | Required | Meaning |
|---|---|---|
| `name` | yes | model name (4.2 rule; spaces allowed); names the model, definitions and folders. Drafted from the model folder |
| `display_name` | yes | GUI label and `.entity` file name |
| `kind` | yes, or `domain` | one of the kinds in 6.7; older manifests with only `domain` map to a kind |
| `domain` | no | legacy: `land`, `air`, `surface`, `subsurface`, `space`, `lifeform`; filled from the kind |
| `dis_type` | yes | 7-field DIS entity type `kind:domain:country:category:subcategory:specific:extra`; must not collide with the install |
| `donor_entity` | yes | stock `.entity` name in `EntityLevel/vrfSim`; should be the same kind |
| `platform_ope`, `model_category_dir` | no | filled from the kind; set them to override |
| `forward_axis_in_blender` | yes for models | `+Y`, `-Y`, `+X`, `-X`: which Blender axis is the front |
| `articulated_parts` | no | list of `{node, dis_part, parent, motion?, min?, max?, max_rate?}`; `dis_part` from ACM Table 58; `motion` one of `azimuth`, `elevation`, `rotation`, `extension`, `translate_x/y/z`, `visual`; angles in degrees, extensions in metres, rates per second |
| `unmapped_joints` | no | joints left static, reported only |
| `systems` | no | changes to the donor's systems (6.11): `{system, position_from?, bind?, params?, remove?, sysdef?, reason?}`; drafted by `draft_manifest`; absent = the donor's systems unchanged |
| `short_name` | no | 11-character DIS marking |
| `walkable` | no | buildings: model becomes its own object geometry, embedded in the terrain |
| `munition_class` | munitions | `missile`, `ballistic_missile`, `bomb`, `rocket`, `round`, `torpedo`, `naval_mine` |
| `character_type` | humans, animals | a DI-Guy character from `character_animations_table.mtl` |
| `character_appearance`, `animation` | no | DI-Guy appearance and default animation |

`models/_example/manifest.json` shows the format. A manifest with any `TODO` value is refused.

### 5.3 Editor: Review > MAK

1. **Settings** (saved on the ship project, no new version): DIS type, donor entity, short name, a DIS code or
   "static" per moving part, optional `kind`. "Draft" fills suggestions. **Systems** lists the changes the export
   makes to the donor's systems (6.11) for the donor picked, each with its reason and a checkbox; unticked ones
   are saved as `systems_off` and the donor's system is kept as it is.
   - Part codes follow the suggestions unless changed by hand: the settings save each part's suggestion
     (`parts_suggested`), and a saved code equal to it takes today's suggestion, so rules added later apply.
   - Settings saved before 15 Nov have no `parts_suggested`. Rows whose code differs from today's suggestion are
     marked, with a "Use the current suggestions" button.
   - The systems list picks codes the way the export does.
2. **Export for VR-Forces**: builds the package, runs `mak/run_mak_export.py` in a subprocess, shows every check
   (warnings marked `!` and counted), offers a zip of `mak/`. If the editor's server is older than its code on disk,
   Home and this panel say to restart it (`health.code`). Its drafts use the old rules: on 15 Nov a server started
   the day before left Lincoln's mounts uncoded.
3. **Install into VR-Forces...**: lists custom SMSs, names every folder it will write, needs confirmation.

Endpoints (CONTRACT §10):

| Method and path | Body | Result |
|---|---|---|
| `GET /api/projects/{id}/mak/draft[?donor=]` | | `{dis_type_hint, short_name, donors[], parts[], systems: {donor, changes[], notes[]}}`; the systems draft is for `donor`, else the saved donor, else the first suggestion |
| `PATCH /api/projects/{id}/mak` | `Project.mak` | `{project}`; 400 with a sentence on a bad DIS type, unknown donor, part not on the current version, bad code, unknown kind, `systems_off` not a list of names |
| `POST /api/scenes/{id}/mak-export` | | job; result = report + `{package_dir, output_dir, zip_url}`; 409 without settings |
| `GET /api/mak/sms` | | `{sms: [custom SMS names]}` |
| `POST /api/scenes/{id}/mak-install` | `{sms, confirm: true}` | job; result `{sms, written[], merged[], backups_dir}`; 400 stock/unknown SMS, 409 no passing export |
| `GET /api/health` | | includes `mak: bool` |

`Project.mak` / `ship/mak.json`:
`{"dis_type": "1:3:15:80:1:0:0", "donor_entity": "Super_Tanker", "short_name": null,
"parts": {"Mast__Radar_1": 5376, "Lifesaving__Davit_P1": null}, "kind": "surface_ship", "systems_off": ["weapon"]}`;
`kind` and `systems_off` are optional. The package copy also carries `"name"` (the project's name).

Scripts, also usable without the server:
```
python mak/run_mak_export.py <package_dir> [--vrf <dir>] [--shared-data <dir>] [--no-mak-check]
python mak/install.py <package_dir>/mak <sms> [--vrf <dir>] [--shared-data <dir>] [--backups <dir>]
```

### 5.4 Deploying into VR-Forces
- Create a **custom SMS** in the SOE that includes `EntityLevel`. The installer refuses stock SMSs
  (`EntityLevel`, `AggregateLevel`, `AggregateLevelBase`, `base`, `MAKTest`) and names that don't exist.
- The installer copies the model into SharedData and `vrfSim/` + `gui/` into the SMS, backs up every file it
  replaces to `data/mak_backups/<sms>-<UTC time>/`, and merges a munition's ammunition entry into the SMS's
  `ammunitionParams.mtl` (backed up first; merging twice changes nothing).
- Alternative: copy the model by hand and use SOE File > Import Visual Models on `visual-import/` (UG 65.2.10).
- Then open the SMS in the SOE and check the entity.

### 5.5 Verifying against a hand import

The pipeline's output is checked by importing the same model **by hand** in the Simulation Object Editor (SOE) and
comparing the two. `scripts/export_hand_import.ps1` (in the editor: `mak/scripts/export_hand_import.ps1`) collects
what the SOE wrote into one zip on the VR-Forces machine, so it can be compared anywhere. Every visual and
behavioural fix so far came from such a comparison (8.2). Do one after any change to what the importer writes, and
one per new model kind before its profile is marked `verified` (6.7).

**1. Before the hand import**
- Generate the model: `python -m pipeline.run models/<name>` (output in `output/<name>/`) or, in the editor,
  Review > MAK > Export for VR-Forces (output in `<package_dir>/mak/`). It must pass.
- On the VR-Forces machine, copy the model folder `SharedData/ModelData/<category>/<name>/` into
  `C:\MAK\SharedData\18\latest\ModelData\<category>\<name>\`. The SOE will point at this file.
- **Write down the time** you start. The script collects everything changed after it.

**2. The hand import, in the SOE**
1. Create a custom SMS that includes `EntityLevel` (e.g. `PipelineTest`). Never use a stock SMS.
2. Copy the manifest's `donor_entity` with "New Model from Existing (Copy)" (UG 65.3.1) and rename it to the
   manifest's `display_name`.
3. Set the DIS type to the manifest's `dis_type`, and point the 3D model at the `.flt` from step 1.
4. Do by hand what the pipeline should have done, so the two can be compared: add the articulated parts
   (UG 66.3), "Calculate Support Points and Bounding Volume from Displayed Model" (UG 66.1.1, 66.1.3), and for
   systems use Edit Properties (UG 66.4.4) to place sensors and bind weapons where the model has them.
5. Save, and close the SOE so every file is written.

**3. Run the script** (on the VR-Forces machine; Windows PowerShell 5.1, no Python needed)

```
powershell -ExecutionPolicy Bypass -File export_hand_import.ps1 -SmsName PipelineTest `
    -ModelName "USS Abraham Lincoln" -EntityName "USS Abraham Lincoln" -ModelCategory Vehicles/Surface `
    -Since "2026-11-15 09:30"
```

| Parameter | Default | Set it to |
|---|---|---|
| `-SmsName` | (required) | the test SMS; several as `A,B` |
| `-ModelName` | `HarmonyOfTheSeas` | the manifest's `name`: the model folder and file name |
| `-EntityName` | `Harmony of the Seas` | the manifest's `display_name`: the `.entity` file name or its GUI label |
| `-ModelCategory` | `Vehicles/Surface` | the manifest's `model_category_dir` (e.g. `Vehicles/Wheeled`) |
| `-Since` | 24 hours ago | the start time from step 1 |
| `-VrfRoot`, `-SharedData`, `-MakRoot` | the standard `C:\MAK` install | another install |
| `-OutDir` | the Desktop | where the zip goes |
| `-MaxSmsMB` | 500 | above this, only the SMS's `.entity`, `gui/` and index files are copied |

The defaults are Harmony's: **set `-ModelName`, `-EntityName` and `-ModelCategory` for any other model**, or the
entity and model are not found. The script only reads the install: it copies into a temporary folder, zips it,
and deletes the folder. It ends by printing the zip's path, each entity found (file and `objectType`) and
warnings. A warning such as "no .entity named or labelled ..." or "model folder not found" means a parameter is
wrong: fix it and run it again.

**4. What the zip holds** (`vrf-hand-import-<ModelName>-<time>.zip`)

| Path | Contents |
|---|---|
| `sms/<sms>/...`, `sms/<sms>.sms` | the test SMS in full: `vrfSim/*.entity`, `gui/visuals/Entity/*.leaf`, `*.metx`, `gui/visuals/UserData/*.ommx` |
| `model/<category>/<ModelName>/` | the model files the SOE used |
| `mak/node_info_<file>.txt` | the model as MAK's own reader sees it (`medfTool --writeNodeInfo`) |
| `changed/...` | any other file the SOE changed after `-Since` (definitions, `importConfig`, settings, other SMSs), path relative to `C:\MAK` |
| `logs/`, `version/` | SOE and VR-Forces logs since `-Since`; the VR-Forces build |
| `export_info.json` | parameters, entities found, notes, and every file with its SHA-256. UTF-8 **with a BOM**: read it with `encoding="utf-8-sig"` |

**5. Compare** (unzip into a scratch folder, outside `output/`)

| Hand import (zip) | Pipeline output | Look at |
|---|---|---|
| `sms/<sms>/vrfSim/<entity>.entity` | `sms/vrfSim/<display name>.entity` | `objectType`; `DtSimArtPartDescriptorList` (codes, attach points, limits); `boundingVolume`; supports; each `componentSystem` (`sensor-position`, `*-art-part-type`) |
| `sms/<sms>/gui/visuals/Entity/*.leaf`, `*.metx` | `sms/gui/visuals/Entity/` | model definition references, DIS type |
| `sms/<sms>/gui/visuals/UserData/*.ommx` | `sms/gui/visuals/UserData/` | schema, `filename` |
| `mak/node_info_*.txt` | `report.json` `verify_model` (`mak-loaded`) | the same parts and `@dis` comments |
| `changed/` | (nothing) | files the SOE touched that the pipeline never writes: a step the pipeline misses |

Diff a pair with `git diff --no-index --word-diff <hand file> <pipeline file>`. Differences that are expected:
the SOE adds a `visual-meta-data` block (open question Q17) and an empty `additional-detection-types` list on
sensors, and its `.leaf` points at its own copy of the `.ommx` (`<name>_1`). Anything else is either a pipeline
bug (fix the generator, not the output) or an SOE convention to adopt; record which in
`references/open-questions.md`.

**6. Keep the result**
- vrf-pipeline: copy the SOE's files into `tests/fixtures/hand_import/`, say where each came from in its
  `README.md`, and extend `tests/test_hand_import.py` so the generator must keep matching them.
- 3d-model-editor: the repository is public and these files are MAK material, so **commit none of them**. Turn
  what was learnt into rules in `mak/tests/test_editor_rules.py`, which check against the installed donor instead.
- Mark a kind's profile `status="verified"` in `profiles.py` only after a clean comparison.

## 6. Supported features

### 6.1 OpenFlight writer
- Geometry from every triangle primitive (indexed or not), normals (flat when absent), UVs, double-sided flags.
- Materials: base colour **on each face** (VR-Forces ignored palette colours: the first hand import showed a
  white ship; confirmed rendering in the SOE on the 2026-11-15 reimport, Q0), textures extracted from the glb,
  and PBR maps written as MAK effect maps beside them (6.12).
- One DOF node per moving part: origin at the pivot, limits on its motion axis, and a comment in MAK's own
  format (`@dis articulated_part <code> rotate_min p,r,y ...`); the root carries `@dis entity_type`.
- Verified by re-reading the file and by `medfTool --writeNodeInfo` (MAK's reader lists every part).

### 6.2 Articulated parts
Part codes from ACM Table 58 (`artparts.py`); default motion and limits per code family; child parts under their
parent part; `.entity` descriptors in the stock shapes (parameter pairs, attach points relative to the parent's,
weapon end points at the muzzle); visual-only parts (rotors etc.) are in the model but not in the `.entity`.

**Planned upstream, in the editor (CONTRACT §3b).** The hierarchy these rules need is planned before Blender
builds the model, not discovered at export:
- `catalog/articulation/roles.json` lists the moving components ("roles"). Each gives its chain of joints with
  motion, axis, pivot, neutral pose and what geometry each joint holds: `gun_mount` (mount az -> Gun el),
  `naval_gun`, `trainable_launcher` (mount az -> Elevation el -> Launcher, fixed), `vertical_launcher`,
  `rotating_radar`, `director`, `rudder`, `mast_extension`, `crane`, `davit`, `deck_elevator`, `hatch`,
  `landing_gear`, `control_surface`, `rotor`, `propeller`, `wheel`.
- Roles are tagged with MAK profile kinds, and a project kind maps onto them (`ship` -> `surface_ship`), so a new
  kind needs data, not code.
- The flow:
  - research names each moving component's role and count;
  - the design plan lists each instance by name and cannot be saved or approved if a P0/P1 component is missing;
  - the build AI makes each one with `me3d.add_chain`;
  - every build is checked against the plan (`articulation-missing-joint`, `-no-geometry`, `-not-nested`,
    `-wrong-motion`), and the build AI fixes the warnings in the same turn.
- `mak/tests/test_roles.py` keeps the roles honest against the install:
  - example names get the role's Table 58 codes;
  - motions match `motion_of`;
  - each weapon role's chain binds its stock sysdef whole, with no sim-only part (Phalanx, Mk 45, RAM, VLS);
  - limits match the sysdef within a degree.
- End to end, a custom ship built with `add_chain` for a Phalanx and a RAM launcher exports with both weapons
  fully bound, no `part-sim-only` warning, and medfTool loading all 7 parts.

### 6.3 Element definition from the donor
The donor's element definition is found through the install's `.metx` mappings, **exact type first, else best
match** (stock mappings use -1 as a wildcard, e.g. a hangar's `5:1:0:2:1:1:0` is mapped by `5:1:-1:2:1:1:-1`).
It is copied with only its name and its 3D/XR main-model references changed. Boost `class_id`/`tracking_level`
attributes are kept on first occurrences only, as Boost writes them. Matches the SOE's own file line for line.

### 6.4 Simulation object
Cloned from the donor (systems, signatures, behaviours; UG 65.3.1), then: object type `1:` + DIS type,
articulated parts, the manifest's system changes (6.11), bounding volume from the model (a custom offset is added
when the donor has none: 833 stock entities use `ground-center`), support points, labels, kind flags. The donor's
object geometry and navigation data are dropped (they describe the donor's shape).

### 6.5 Support points
| Rule | Kinds | How |
|---|---|---|
| waterline | surface ship | where the hull crosses z = 0: front pair at +30% waterline length, 90% of the half-beam there; rear centre at -35%; down = 0 (stock ships: down = 0 on 144 of 159) |
| ground | ground vehicle, aircraft, building, static object | lowest 5% of the model: widest points give the left/right pair (front end when they run the length), the low point furthest from them gives the third (main gear + nose wheel on an aircraft) |
| donor | submarine, space object, munition, human, animal | kept from the donor, with a warning |

### 6.6 Donor and manifest suggestions
`draft_manifest` infers forward axis, kind/domain (from `spec.json` schema/params or the geometry), maps Blender
joints to motions and part codes, uses `socket_bow` for the bow, and ranks
stock donors on the kind's platform by bounding-volume similarity, preferring civilian or military donors to match
the model's weapon parts. `spec.json` `meta` is not trusted; `custom_ops` code is never executed.

Part codes (`draft_manifest.NAME_CODES`): every joint whose name has a Table 58 counterpart gets the first free
code of that part. The node's own name (after the last `__`) is tried first, then its whole path, split into
words (`Weapons__SeaSparrow_1` -> "weapons sea sparrow 1"). Rules: radar (10 codes), rudder (1), deck elevator on a
ship (2; on an aircraft an elevator is "other control surfaces"), catapult (2), blast deflector (2), arresting wires
(3), turret, gun, launcher (10 each), periscope, snorkel, antenna, hatch, landing gear, rotors, tail hook, speed
brake, wing sweep, wing/rotor fold, left/right flap and aileron, boom. A weapon mount (`Weapons__...`, Phalanx,
CIWS, RAM, Sea Sparrow ...) that trains in azimuth is a turret, and an elevating part under it a gun, or a launcher
for missiles, as on the stock carriers (Nimitz-class: turrets 4096/4192, guns 4416/4448, launchers 4736/4768).
When the model has more of a part than Table 58 has codes, the rest stay static and `unmapped_joints` says why
(USS Abraham Lincoln: 11 of 16 joints mapped; elevators 3-4 and rudders 2-4 static).

### 6.7 Model kinds (`profiles.py`)

| kind | DIS kind:domain | platform `.ope` | ModelData folder | schema | origin | supports | status |
|---|---|---|---|---|---|---|---|
| `surface_ship` | 1:3 | Surface_Ship | Vehicles/Surface | Watercraft | waterline | waterline | **verified** |
| `submarine` | 1:4 | Subsurface_Vehicle | Vehicles/Subsurface | Entity | waterline | donor | untested |
| `ground_vehicle` | 1:1 | Ground_Vehicle | Vehicles/Wheeled | Entity | ground | ground | untested |
| `fixed_wing` | 1:2 | Fixed_Wing_Aircraft | Vehicles/FixedWing | Entity | centre of gravity | ground | untested |
| `rotary_wing` | 1:2 | Rotary_Wing_Aircraft | Vehicles/RotaryWing | Entity | centre of gravity | ground | untested |
| `space_object` | 1:5 | Space_Object | Vehicles/Space | Entity | centre of gravity | donor | untested |
| `building` | 5:1 | Cultural_Feature | Structures/Buildings | Entity | ground | ground | untested |
| `static_object` | 5:1 | Cultural_Feature | Objects | Entity | ground | ground | untested |
| `munition` | 2:any | by `munition_class` | Munitions/... | Entity | centre of gravity | donor | untested |
| `human` | 3:1 | Human | (no model) | DiGuyCharacter | - | donor | untested |
| `animal` | 3:1 | Animal | (no model) | DiGuyCharacter | - | donor | untested |

Adding a kind is a row here plus a verification run. Code elsewhere reads the profile; it does not branch on the
kind. `tests/test_profiles.py` checks each profile against the install: its DIS kind/domain fits at least 90% of
the stock entities on its platform, and its schema is the one the stock model definitions in its folder use.

### 6.8 Buildings and munitions
- **Buildings / static objects**: cultural features, `gravity-aligned` set (UG 66.1.8). With `walkable`, the
  model's own `.flt` becomes the object geometry and `embed-into-terrain` is set, so characters can walk in and
  around it; navigation data is generated when it is placed (UG 66.1.6-66.1.7).
- **Munitions** (simulated: missiles, bombs, rockets, torpedoes, mines; UG 66.5.1): normal model route plus an
  `ammunitionParams` entry cloned from the donor munition's entry (warhead, guidance mode, targets), with the
  new munition type. The type may not contain -1 (UG 73). Pending items name what must be done by hand.

### 6.9 Humans and animals
Lifeforms are DI-Guy characters (ACM 25.1-25.2; UG 66.1.10). No model is read or written. The `.entity` is the
donor lifeform with `character-type` (checked against `character_animations_table.mtl`), appearance, head and
default animation set, and `di-guy-enabled` on. The donor's element definition is renamed when it has one; stock
animals have none, and VR-Forces shows them from the `.entity` alone.

### 6.10 Validation (selected checks)
Errors: `dis-type-collision`, `model-def-collision`, `model-file-missing`, `ommx-no-filename`,
`element-def-missing`, `leaf-model-def-missing`, `leaf-boost-class-ids`, `entity-objecttype-mismatch`,
`entity-artpart-parent`, `entity-artpart-params`, `entity-artpart-limits`, `munition-type-wildcard`,
`character-unknown`, and for systems `system-sysdef-missing`, `system-not-allowed`, `system-name-duplicate`,
`system-name-digit`, `system-movement-count`, `system-damage-count`, `system-unknown`, `system-bind-unknown`,
`system-position-unknown`. Warnings: `entity-system-artpart` (a system refers to a part the model lacks),
`system-artpart-category`, `system-position-outside`, `system-weapon-no-sensor`, `system-ammunition-unknown`,
`entity-support-outside`, `entity-attach-outside`, `dis-type-kind`. Inspect: `origin-z`, `origin-cg`,
`scale-too-large/small`, `forward-axis`, pivot and hierarchy rules, `kind-untested`.

### 6.11 Systems (`systems.py`)
The `.entity`'s sensors, weapons and other systems are the donor's (UG 65.3.1), edited by the manifest's
`systems` list. Full rules and evidence: `references/systems.md`.
- **Catalog.** Every `.sysdef` in the SMS is read (S-expressions; weapon and damage templates folded in, UG 71.4,
  73.1.1). For each: categories, the state repositories it allows (UG 71.1.4; matched to the platform `.ope`'s
  `parameter-type`), its part parameters (`*-art-part-type` with their `art-part-category`), fixed part codes,
  ammunition, and how stock entities use it. Cached in the system temp folder.
- **What ties a system to the model.** Sensors: `sensor-position`, which feeds the sensor's offset (UG 71.4.2,
  72.1.2). Weapons: part parameters bound to the entity's articulated parts. Stock entities bind
  `elevating-turret-art-part-type` to turret, gun and launcher codes, and `gun-art-part-type` also to 8192, so
  the check uses what the install does, not the category alone. No stock sensor is bound to a radar part.
- **Draft** (`draft_manifest`; in the editor, at export, less `systems_off`):
  - radar sensors at the highest radar part (else `socket_radar`); visual and IR sensors at `socket_bridge_eye`
    (else `station__helm`); others keep the donor's position, with a note;
  - donor weapons kept when the model has their parts, else rebound to a free mount of the model (parameters
    assigned top-down, each to a part of a family stock entities bind it to), else removed with the reason;
  - a stock weapon added for a mount whose name names it (`phalanx`, `ram`), or a reported stand-in (Sea Sparrow ->
    the RAM launcher, Q22); ammunition counts are the stock median. Parts the mount lacks are added to the
    `.entity` only (**sim-only parts**, UG 66.3.1): the weapon aims and fires, but the model never shows that
    motion; each is a `part-sim-only` warning (Q20). A part the stock component gives no motion (an empty
    `art-part-param-list`, e.g. RAM's `primary-launcher` launch point) is added as a **fixed** sim-only part,
    written as stock files write it (empty list, no limits, e.g. Arleigh Burke's 4992). It is reported as
    `part-sim-only-fixed` (info), since there is nothing to show;
  - damage or movement added when the donor has none (UG 66.4.2-3): the most common for the **class**, stock
    entities of the same kind, military or civilian;
  - the class baseline added: sensors and additional systems at least 50% of the class has (military ships:
    active radar, TDL, spot reports), with stock median parameters;
  - systems that drive a part the model has (periscope, folding wing, cargo door ...);
  - a sensor added when weapons remain and there is none (UG 66.4).
- **Apply** (`generate`): edits the cloned `componentSystem`s in place; new ones go after the donor's, in the
  stock form. Each change is a `system-changed`/`-added`/`-removed` finding with its reason. A manifest without
  `systems` changes nothing.
- **Parts and components** (UG 66.3): every part a system drives is named after that component's art-part entry,
  `<entry>-<code>`, as stock entities name theirs (e.g. `primary-turret-4096`, `azimuth-mount-4160`). The report
  lists part -> system / component (`part-component`) and the parts no stock system drives (`part-visual-only`:
  radars, rudders, deck elevators). The SOE shows the component in its Associated Component column, e.g.
  `main-30mm-gun:turret` (confirmed on the Lincoln reimport, Q21). Sim-only parts have no Animate checkbox
  there.
- Results:
  - Harmony: the passive radar sensor moves to `Mast__Radar_1` (136, -4, -56.1 body) and the visual sensor to the
    bridge eye.
  - Lincoln: the radar sensor moves to `Radar__Scanner`; `surface-default` damage is added (the donor had none,
    shown as Undamageable); Phalanx ×2, RAM ×2 and Sea Sparrow ×2 (RAM stand-in) are added with 10 sim-only
    parts, because its mounts are azimuth-only (Q20).

### 6.12 PBR maps as MAK effect maps (`effectmaps.py`)
VR-Forces lights models with a GGX shader and reads PBR inputs from **effect maps**: image files beside the
diffuse texture, found by name (ACM 12.4). The stock PBR ships (Gerald Ford, Arleigh Burke) use three per
material, all 4096²: `X.png` (diffuse), `X_NML.png` (normal) and the composite `X_MTL_GLS_AO.png` (R metal,
G gloss, B AO; a composite lists its codes in R, G, B order, ACM 12.4.1). The model references only `X.png`.

From each glTF material with a normal, metallic-roughness or occlusion texture, the writer makes:

| glTF | MAK file | Content |
|---|---|---|
| `baseColorTexture` | `<name>_tex<n>.png` | as before; a white 4x4 when the material has maps but no base texture (the face colour tints it) |
| `normalTexture` | `<name>_tex<n>_NML.png` | RGBA: R = X, G = Y (+Y up, as glTF), B = Z, A = Y; `normalTexture.scale` applied. The shader reads `.ra` (`normal_map.frag.glsl`), so Y also goes in alpha; the probe showed MAK reads green from plain RGB too (Q23) |
| `metallicRoughnessTexture` (B metal, G roughness) + `occlusionTexture` (R) | `<name>_tex<n>_MTL_GLS_AO.png` | R = metal x `metallicFactor`, G = 255 - roughness x `roughnessFactor`, B = AO with `strength`. Constant from the factors where a map is missing |

- A diffuse shared by materials with different maps is written once per set, so each set has its own name.
- Images used only as effect maps are not written as plain textures.
- `png.py` decodes PNGs in pure Python: about 4-24 s per 4096² map, depending on the PNG filter.
- Findings:
  - `texture-jpeg-effect` (warning, inspect): an effect map is JPEG; skipped, since only PNGs can be read.
  - `flt-texture-missing` (error, verify): a palette texture is not beside the model.
  - `flt-effect-maps` (info, verify): lists the effect maps written.
  - `effect-maps-flt-only` (warning): the FBX route doesn't write them.
- `scripts/make_pbr_probe.py` builds `PBRProbe`, a barge with eight test panels. Imported into the SOE on
  2026-11-15, it confirmed (Q23):
  - effect maps beside a `.flt` load by name;
  - metal and AO show;
  - the normal's +Y points to the top of the image;
  - textures and face colours share one colour space.
- `medfTool` recognises the names: it compresses `_NML` maps to two-channel BC5 (ATI2N), as on stock ships.
- The editor's generator makes the textures themselves, in a separate on-demand step
  (`blender/bake_textures.py`, CONTRACT §4b):
  - each face gets a procedural finish in world metres (non-skid deck, hull plating, antifouling, boot top,
    superstructure paint, bare metal, glass, rubber, teak, canvas): the spec colour stays the tint, with
    grime where the AO is dark, rust streaks, edge wear from the Bevel node, plate seams and a bump;
  - faces go into Deck, Hull, Superstructure and Details atlases; the page count follows the area and a target
    texel size (Deck and Hull 4096², the others 2048²), on one shared UV map;
  - Cycles bakes base colour, ORM (R AO, G roughness, B metal) and an OpenGL tangent normal. Markings and
    other decals within 5 cm of a surface are baked into the colour and dropped;
  - it writes `model_textured.glb` beside `model.glb`, which it leaves alone. Lincoln: 6 pages, 35 decals,
    118 MB, about 10 minutes on a 16-thread CPU at 4 samples.
- **Drafting from a baked glb:** `draft_manifest` takes `model_textured.glb` (as `model.glb`) with its
  `bake_report.json` beside it. The bake cuts large faces into plates and drops decals, so for a baked glb:
  - the triangle count against `build_report.json` is noted, not refused (the bounds are still checked);
  - `parts.json` nodes listed as decals in `bake_report.json` are left out (Lincoln: 15 624 against 15 184
    triangles, 35 decals);
  - without `bake_report.json`, a mismatch is refused as before.

### 6.13 Packaging for fast loading (`medf.py`)
- **The problem:** the first baked Lincoln (6 PNG pages at 4096² and 2048², plus 12 effect maps, 106 MB) loaded
  slowly in the SOE (2026-11-15). Plain OpenFlight "load[s] slowly", and MAK recommends preprocessing every model
  (ACM terrain notes, 19.19). Raw PNGs are decoded, mip-mapped and compressed at every load.
- **The stage:** after the model is verified, `medfTool --directory <model folder> -x flt -m png` writes
  `<name>.medf` and one `<image>_png.meif` per texture beside the `.flt`. This is the format stock models ship
  in:
  - DXT1 colour and BC5 (ATI2N) normal maps, as on the stock Gerald Ford;
  - full mip chains;
  - effect maps keep their names (`X_png.meif`, `X_NML_png.meif`, `X_MTL_GLS_AO_png.meif`).
- **Loading:** MAK checks the requested file's folder for an MEDF version and loads it instead (19.19). So the
  `.ommx` keeps naming the `.flt`, and the `.flt` and PNGs stay as the readable source. The installer copies
  the whole folder.
- **Cost:** baked Lincoln took 16 s and came out at 74 MB from 111 MB.
- **Findings:** `medf-packaged` (info), `medf-image-missing` (warning: a texture had no `.meif` made),
  `medf-failed` (error), `medf-unavailable` (warning: no `medfTool`).
- **Options:** `--no-medf` or `"medf_package": false` skips the stage.
- **Not yet measured:** the load-time gain in VR-Forces (Q24).

## 7. Assumptions

| Assumption | Basis |
|---|---|
| Model frame: X right, Y forward, Z up (OSG style); the glb is converted glTF -> Blender -> forward axis | ACM 21.2 |
| Origin: land = ground-contact plane at the yaw axis; air/space = centre of gravity; surface/subsurface = waterline at the centre of buoyancy | ACM 21.2.1, Table 43 |
| Editor ships: +X bow, +Y port, +Z up, origin at the waterline amidships; parts named `Group__Part`; joint pivot = node origin; joint axis local, turned by `rotation_deg` | CONTRACT §1-§3 |
| A moving part is one node with its geometry beneath it, no rotation or scale in the neutral pose, child parts under the parent | ACM 21.4 |
| What a moving component needs (its chain of joints) is the same for every simulator that drives parts by DIS articulation, so it is planned once, in the editor, as simulator-neutral roles | ACM 21.4, 21.5.1; UG 73.3.1; `mak/tests/test_roles.py` (each weapon role binds its stock sysdef whole) |
| The donor is the same kind, and its systems suit the new object once fitted and completed from the stock class (6.11) | UG 65.3.1 (a copy keeps the donor's behaviour) |
| A system most stock entities of the same kind and class (military or civilian) have is one the new object should have | UG 66.4; install counts (e.g. 70% of military ships have surface-default damage) |
| A part named `<entry>-<code>` after the component that drives it is tied to that component; a part the model lacks can live in the `.entity` only | UG 66.3, 66.3.1; install (1709 stock descriptors use the name); confirmed in the SOE (Q21); the in-scenario behaviour of sim-only parts is still open (Q20) |
| A sensor detects from its `sensor-position`; the radar part's pivot or the bridge eye is a better place than the donor's | UG 71.4.2, 72.1.2; not yet checked in VR-Forces (Q16) |
| A weapon drives whatever parts its part parameters name, so rebinding them to the model's parts works | UG 73.1.1, 73.3.1; install (1372 of 1383 bindings match the entity's parts); not yet checked in VR-Forces (Q19) |
| `.entity` object type = super-type `1` (individual) + DIS type | UG D.3.1 |
| Model schema: `Watercraft` for ships, `Entity` otherwise, `DiGuyCharacter` for characters | ACM 15.1; counted in the install |
| Ship supports belong on the waterline, not the keel | install (144 of 159 stock ships); the SOE's calculate button put them on the keel. Open question |
| Copying the files into a custom SMS is enough (no Import Visual Models step needed) | worked in the hand imports; open question |
| `spec.json` `meta` may be stale; `custom_ops` is untrusted code | observed in the Blender exports |
| Effect maps beside a `.flt` diffuse are found by name, as for stock `.medf` models; `_MTL_GLS_AO` is read as R metal, G gloss, B AO; normal maps are +Y up | ACM 12.4, 12.4.1; install (stock PBR ships, `effects.frag.glsl`, `normal_map.frag.glsl`, `medfTool` compresses `_NML` to BC5); confirmed in the SOE with `PBRProbe` (Q23) |
| When manual and install disagree, the install is right | project rule |

## 8. Verification and tests

### 8.1 Test suites

| Suite | Command | Count (2026-11-15) | Covers |
|---|---|---|---|
| vrf-pipeline | `python -m unittest discover -s tests` | 177 | artparts, inspect rules, OpenFlight write/read, colours, entity building, hand-import comparisons, drafts, profiles, one full run per kind, systems (sysdef reader, catalog, apply, checks, drafts), PNG codec, effect maps and `.medf` packaging |
| editor `mak/tests` | `server/.venv/Scripts/python -m pytest mak/tests -q` | 208 (+8 skipped without the Harmony and Lincoln files) | the pipeline tests, package conversion, systems draft and `systems_off`, install, draft, export script, editor rules (no MAK-derived files), articulation roles against the install (`test_roles.py`), codes following suggestions and the export's own warnings |
| editor `tests/mak` | `server/.venv/Scripts/python -m pytest tests/mak -q` | 1 (slow) | export of a built package with the real `medfTool` |
| editor server | `server/.venv/Scripts/python -m pytest server/tests -q` | 339 | endpoints, settings validation (`systems_off` included), export job with a fake runner, install refusals and backups, articulation roles (research, plan gate, post-build check on Lincoln's parts), stale-server check, `parts_suggested` |
| editor Blender | `server/.venv/Scripts/python -m pytest tests/blender/test_add_chain.py -q` | 4 (slow) | `me3d.add_chain` builds nested joints that survive into the glb and pass the post-build check; `joint_limits` reach joints a custom op made |
| editor bake | `server/.venv/Scripts/python -m pytest tests/blender/test_bake.py -q` | 44 (1 slow) | finish and atlas mapping, page counts, island grouping, bake proxies; a custom ship with a deck marking baked at 1/16 size (pages, ORM packing, UVs, the marking dropped, joints kept, `model.glb` untouched) |
| editor web | `cd web && npm run typecheck && npm test` | 140 | panel logic, DIS type checks, system change text, build-report warning split, codes following suggestions, stale-server text |

Tests that need VR-Forces skip without it. The MAK manuals and SOE-written files are not in the editor repository
(licence), so the editor's tests rebuild expectations from the installed donor instead.

### 8.2 Hand imports
The same ship (Harmony of the Seas, DIS type `1:3:15:80:1:0:0`, donor `Super_Tanker`, two radars) was imported by
hand in the SOE twice and compared file by file:

| Found | Fix |
|---|---|
| entity showed a generic warship: no `.leaf` existed, the type fell back to a stock visual | `.leaf` cloned from the donor's element definition |
| ship rendered white | colour written on each face |
| support points were the tanker's | computed from the hull at the waterline |
| donor's navigation data kept without its geometry | cleared |
| entity named after the catalog template | named after the ship project |

After the fixes, the bounding volume and both radars (attach points, axes, limits) match the hand import.

## 9. Limitations

- **Only `surface_ship` is verified in VR-Forces.** All other kinds report `kind-untested`.
- Install into a real custom SMS has been exercised only on a fake install tree in tests.
- Rotors, propellers and wheels are static: no `@dis animation` sequences (ACM 14.3, 21.6).
- No damage-state geometry (`@dis switch damage`, ACM 21.5) and no LODs.
- **Effect maps** are confirmed on a test model only (Q23), not yet on a baked ship. Emissive (`_EMM`) and
  SensorFX (`_SNR`) maps are not written, and the FBX route writes no effect maps. Large maps are slow to repack
  (pure-Python PNG decoding).
- **Baked colours render lighter than stock** (Q25). Mean grey in the SOE's Perspective View, against the stock
  Nimitz from the same angle: deck about 158 against 136, hull about 235 against 148. The bake writes sRGB colour
  (glTF's convention) from the spec colours; MAK shows texture bytes as they are, under bright lighting.
- **The texture bake** runs from the command line only (no button or job in the editor yet). It is CPU-only
  (about 10 minutes for Lincoln) and makes a large glb (118 MB). Finishes are chosen from material and part
  names, so an odd name falls back on a generic paint. A twisted source face can bake an inverted patch into
  the normal map.
- **Systems follow the stock class, not the real ship.** The draft adds what most stock entities of the kind
  have, the weapons its mounts are named after, and the systems that drive its parts (6.11). Ranges, rounds and
  other values are the donor's or the stock median, not the real ship's.
- **Sensor and weapon changes are not checked in VR-Forces yet**: that detection runs from the new positions
  (Q16) and that a rebound or added weapon slews the model's parts (Q19). Sonar and gimbaled sensors keep the
  donor's positions.
- **Missing elevation joints: weapons never visibly pitch.** Every stock ship weapon drives an elevating gun or
  launcher part as well as its mount. Where the model has only the azimuth joint (all six Lincoln mounts), the
  missing parts are sim-only: the weapon aims and fires in the simulation, but the 3D mount only turns. Each
  import reports it (`part-sim-only` warnings) and open question Q20 keeps an observation log. Fix: add the
  elevating joints in Blender. Ships the editor builds from a plan now get them at the source (6.2, CONTRACT §3b);
  Lincoln was built before that and needs its plan redrafted and rebuilt. Sea Sparrow has no stock weapon; the RAM launcher stands in (Q22). Guns without
  parts (`muzzle-offset`, as on the F-16) are not drafted.
- Detonation power and weapon ammunition lists for a new munition are set by hand (UG 73, 74.2.2).
- Humans and animals use a stock DI-Guy character; a custom mesh would need DI-Guy authoring tools (ACM 25.2).
- Editor: exports ships only; crew are listed in `not_exported`. The Review panel has no kind field, and its
  donor suggestions search ship donors only.
- FBX route (Blender) exists but is not offered in the editor UI.
- Short name is cut at a word boundary within 11 characters ("Harmony of").
- Table 58 has one rudder code and two deck elevator codes, so further rudders and elevators stay static. Whether
  several DOF nodes may share one code (all rudders turning together) is untested
  (`references/open-questions.md` Q18).
- One SharedData version at a time; VR-Forces 5.1.1 only.

## 10. Outstanding work

Planned, not done:
1. One hand import per new kind (ground vehicle, aircraft, building, munition, human) and the comparison, then mark
   the profile `verified`.
2. Editor crew as DI-Guy characters: character picker in Review > MAK, contract change for crew settings.
3. Kind field and per-kind donor/part suggestions in the Review panel.
4. Animation sequences for rotors, propellers and wheels; damage switch nodes.
5. Check the systems in VR-Forces: the SOE's Edit Properties shows each system's position (UG 66.4.4); detection
   from the mast (Q16); a weapon slewing the model's parts in a scenario (Q19, Q20).
   Give Lincoln's six weapon mounts their elevation joints, so their weapons pitch in 3D and need no sim-only
   parts (Q20): redraft its research and plan with the articulation roles (six weapon chains), approve, rebuild.
6. Install test against a real custom SMS.
   Lincoln's textures are baked (`model_textured.glb`, v13) and the first reimport (2026-11-15) shows them
   rendering (Q25). Next:
   - measure the load time with the `.medf` package (Q24);
   - calibrate the colour tone against stock (Q25);
   - take a close-up to judge the normal-map detail;
   - wire the bake into the editor: a "Bake textures" job, a Flat / Textured viewer toggle, and MAK export and
     packaging using the baked glb with its `bake_report.json`.
7. Review and merge `feat/mak-export` and `feat/mak-kinds`.
8. Live check of the articulation roles with the real AI: a research turn names the roles, the plan lists every
   chain, and the build turn uses `add_chain` and clears its own `articulation` warnings.

Open questions for the follow-up with the DTS team:
- Ship supports on the waterline (stock ships) or on the keel (SOE calculate button)? Does the ship float at the
  right height?
- Is copying files into an SMS enough, or is Import Visual Models required?
- Can a custom human or animal mesh be used without DI-Guy authoring tools?
- Is cloning a stock munition's detonation entry acceptable, and who adds new munitions to launchers?
- Aircraft: supports at the landing gear? How is the centre of gravity judged?
- Buildings as entities (cultural features) or as terrain props (ACM 1.10)?
- Which donor for passenger ships (`Super_Tanker` or `Cruise_Ship`)?

## 11. Appendix

### Glossary
| Term | Meaning |
|---|---|
| SMS | simulation model set: a folder of `.entity` files, systems and GUI definitions (`data/simulationModelSets/<sms>`) |
| SOE | Simulation Object Editor, VR-Forces' tool for SMSs |
| DIS type | 7-field entity type `kind:domain:country:category:subcategory:specific:extra` |
| object type | the `.entity` form: super-type + DIS type (8 fields) |
| `.ope` | platform file: the class of simulation object (ship, ground vehicle, human ...) |
| donor | the stock `.entity` the new object is cloned from |
| DOF node | OpenFlight degree-of-freedom node; MAK articulates parts through them |
| element / model definition | `.leaf` / `.ommx`: which visuals show a type, and which model file a visual uses |
| DI-Guy | MAK's character system for humans and animals |

### Manual sections used
ACM 14.1-14.3 (model formats), 15.1 (schemas), 16-18 (model/element definitions, mapping), 19.19 (MEDF),
21.1.2 (FBX node names), 21.2-21.2.1 (frames, origins), 21.4 (articulated parts), 21.5 (appearances),
21.6 (animation), Table 58 (part codes), 25.1-25.2 (DI-Guy), 12.4-12.4.1 (effect maps). UG 62.4 (navigation data), 64.4 (SMS visuals),
65.2.10 (Import Visual Models), 65.3.1 (copy a model), 66.1.1-66.1.10 (size, supports, object geometry,
cultural features, lifeform visuals), 66.5 (munitions), 73 (weapon ammunition), 74.2.2 (detonation power),
D.3.1 (object super-types).
