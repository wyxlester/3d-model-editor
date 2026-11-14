# glb to MAK VR-Forces importer: technical document

How the importer that turns a glTF binary model (`.glb`) into a VR-Forces 5.1.1 simulation object was built,
how to run it, what it supports, what it assumes, and what is still open.

Audience: an engineer who will run, maintain or extend it. Status as of 2026-11-14: branches `feat/mak-export` and
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
   |  convert      glb.load -> openflight.write: geometry, face colours, textures, DOF nodes + @dis comments
   v               (fbx route: headless Blender instead)
   |  verify       openflight.read_flt + check_flt (re-read), openflight.check_mak (medfTool loads it)
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
| `artparts.py` | DIS articulated-part codes (ACM Table 58), default motions and limits, FBX node-name map |
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
| `mak/from_package.py` | `me3d.package/1` + `ship/mak.json` -> a pipeline model folder and manifest (joint axes turned into the ship frame, part codes, kind, names) |
| `mak/run_mak_export.py` | CONTRACT §9b script: package -> `mak/` output + `mak_export_report.json` |
| `mak/draft_mak.py` | suggestions for the Review panel (DIS type hint, short name, donors, part codes) |
| `mak/install.py` | installs an export into a custom SMS, with backups; merges munition table entries |
| `server/app/mak.py` | settings validation, draft, export runner (subprocess, like the Unreal runner), latest export, install |
| `server/app/main.py`, `packaging.py`, `store.py` | endpoints; `ship/mak.json` and `rotation_deg` in the package; `Project.mak` |
| `web/src/components/MakPanel.tsx`, `lib/mak.ts` | Review > MAK: settings, export with checks, install dialog |

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
| Windows PowerShell 5.1 | `scripts/export_hand_import.ps1` | read-only collection of a hand import |

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
| 1 | `SharedData/ModelData/<category>/<name>/<name>.flt` (+ textures) | the model | `<SharedData>/ModelData/...` |
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
- `<name>`: the model name, an identifier. CLI: manifest `name`. Editor: the ship id.
- `<display name>`: the GUI label and the `.entity` file name. CLI: manifest `display_name`. Editor: the **ship
  project's name** (`ship/mak.json` `name`), cleaned for use as a file name. It is not the `name` in
  `ship/<id>.json`, which is the spec's and keeps the catalog template's name after a rename.
- Short name (DIS marking, 11 characters): manifest `short_name`, else the display name cut at a word boundary.

### 4.3 Report

Pipeline `report.json`: `{"model", "kind", "kind_status", "model_format", "glb", "stages": {stage: [finding]},
"ok", "pending": [code]}`; each finding is `{"level", "code", "message"}`.

Editor `mak_export_report.json` (contract §9b): `{"ok", "vrforces": "5.1.1", "name", "dis_type",
"checks": [{"name": "<stage>:<code>", "pass", "detail"}], "pending": [str], "not_exported": [str]}`. Exit
code 0 only when `ok`.

## 5. Usage guide

### 5.1 Standalone CLI

Every command takes a **model folder** (`models/<Name>/` with `model.glb` and `manifest.json`), not a `.glb` path.
Passing a `.glb` gives an error that explains the folder layout and the next command.

| Command | Options | Does |
|---|---|---|
| `python -m pipeline.draft_manifest models/<Name>` | `--kind <kind>`, `--munition-class <class>`, `--force` | drafts `manifest.json` from Blender exports (`parts.json`, `spec.json`, `build_report.json`); writes `manifest.draft.json` if a manifest exists and `--force` is not given; fields it cannot infer are `TODO...` |
| `python -m pipeline.run models/<Name>` | `--format flt\|fbx`, `--skip-convert`, `--no-mak-check` | all stages; writes `output/<Name>/report.json` |
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
| `name` | yes | identifier (`[A-Za-z][A-Za-z0-9_]*`); names the model, definitions and folders |
| `display_name` | yes | GUI label and `.entity` file name |
| `kind` | yes, or `domain` | one of the kinds in 6.7; older manifests with only `domain` map to a kind |
| `domain` | no | legacy: `land`, `air`, `surface`, `subsurface`, `space`, `lifeform`; filled from the kind |
| `dis_type` | yes | 7-field DIS entity type `kind:domain:country:category:subcategory:specific:extra`; must not collide with the install |
| `donor_entity` | yes | stock `.entity` name in `EntityLevel/vrfSim`; should be the same kind |
| `platform_ope`, `model_category_dir` | no | filled from the kind; set them to override |
| `forward_axis_in_blender` | yes for models | `+Y`, `-Y`, `+X`, `-X`: which Blender axis is the front |
| `articulated_parts` | no | list of `{node, dis_part, parent, motion?, min?, max?, max_rate?}`; `dis_part` from ACM Table 58; `motion` one of `azimuth`, `elevation`, `rotation`, `extension`, `translate_x/y/z`, `visual`; angles in degrees, extensions in metres, rates per second |
| `unmapped_joints` | no | joints left static, reported only |
| `short_name` | no | 11-character DIS marking |
| `walkable` | no | buildings: model becomes its own object geometry, embedded in the terrain |
| `munition_class` | munitions | `missile`, `ballistic_missile`, `bomb`, `rocket`, `round`, `torpedo`, `naval_mine` |
| `character_type` | humans, animals | a DI-Guy character from `character_animations_table.mtl` |
| `character_appearance`, `animation` | no | DI-Guy appearance and default animation |

`models/_example/manifest.json` shows the format. A manifest with any `TODO` value is refused.

### 5.3 Editor: Review > MAK

1. **Settings** (saved on the ship project, no new version): DIS type, donor entity, short name, a DIS code or
   "static" per moving part, optional `kind`. "Draft" fills suggestions.
2. **Export for VR-Forces**: builds the package, runs `mak/run_mak_export.py` in a subprocess, shows every check,
   offers a zip of `mak/`.
3. **Install into VR-Forces...**: lists custom SMSs, names every folder it will write, needs confirmation.

Endpoints (CONTRACT §10):

| Method and path | Body | Result |
|---|---|---|
| `GET /api/projects/{id}/mak/draft` | | `{dis_type_hint, short_name, donors[], parts[]}` |
| `PATCH /api/projects/{id}/mak` | `Project.mak` | `{project}`; 400 with a sentence on a bad DIS type, unknown donor, part not on the current version, bad code, unknown kind |
| `POST /api/scenes/{id}/mak-export` | | job; result = report + `{package_dir, output_dir, zip_url}`; 409 without settings |
| `GET /api/mak/sms` | | `{sms: [custom SMS names]}` |
| `POST /api/scenes/{id}/mak-install` | `{sms, confirm: true}` | job; result `{sms, written[], merged[], backups_dir}`; 400 stock/unknown SMS, 409 no passing export |
| `GET /api/health` | | includes `mak: bool` |

`Project.mak` / `ship/mak.json`:
`{"dis_type": "1:3:15:80:1:0:0", "donor_entity": "Super_Tanker", "short_name": null,
"parts": {"Mast__Radar_1": 5376, "Lifesaving__Davit_P1": null}, "kind": "surface_ship"}`; the package copy
also carries `"name"` (the project's name).

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

### 5.5 Comparing with a hand import
`scripts/export_hand_import.ps1` runs on the machine where an import was done by hand in the SOE. It collects the
test SMS(s), the model files, files changed since a start time, SOE logs and `medfTool` node info into one zip;
it only reads the install. `export_info.json` is UTF-8 with a BOM. The zip is diffed against the pipeline's
output; this is how every visual and behavioural fix so far was found (8.2).

## 6. Supported features

### 6.1 OpenFlight writer
- Geometry from every triangle primitive (indexed or not), normals (flat when absent), UVs, double-sided flags.
- Materials: base colour **on each face** (VR-Forces ignored palette colours: the first hand import showed a
  white ship), textures extracted from the glb.
- One DOF node per moving part: origin at the pivot, limits on its motion axis, and a comment in MAK's own
  format (`@dis articulated_part <code> rotate_min p,r,y ...`); the root carries `@dis entity_type`.
- Verified by re-reading the file and by `medfTool --writeNodeInfo` (MAK's reader lists every part).

### 6.2 Articulated parts
Part codes from ACM Table 58 (`artparts.py`); default motion and limits per code family; child parts under their
parent part; `.entity` descriptors in the stock shapes (parameter pairs, attach points relative to the parent's,
weapon end points at the muzzle); visual-only parts (rotors etc.) are in the model but not in the `.entity`.

### 6.3 Element definition from the donor
The donor's element definition is found through the install's `.metx` mappings, **exact type first, else best
match** (stock mappings use -1 as a wildcard, e.g. a hangar's `5:1:0:2:1:1:0` is mapped by `5:1:-1:2:1:1:-1`).
It is copied with only its name and its 3D/XR main-model references changed. Boost `class_id`/`tracking_level`
attributes are kept on first occurrences only, as Boost writes them. Matches the SOE's own file line for line.

### 6.4 Simulation object
Cloned from the donor (systems, signatures, behaviours; UG 65.3.1), then: object type `1:` + DIS type,
articulated parts, bounding volume from the model, support points, labels, kind flags. The donor's object
geometry and navigation data are dropped (they describe the donor's shape).

### 6.5 Support points
| Rule | Kinds | How |
|---|---|---|
| waterline | surface ship | where the hull crosses z = 0: front pair at +30% waterline length, 90% of the half-beam there; rear centre at -35%; down = 0 (stock ships: down = 0 on 144 of 159) |
| ground | ground vehicle, aircraft, building, static object | lowest 5% of the model: widest points give the left/right pair (front end when they run the length), the low point furthest from them gives the third (main gear + nose wheel on an aircraft) |
| donor | submarine, space object, munition, human, animal | kept from the donor, with a warning |

### 6.6 Donor and manifest suggestions
`draft_manifest` infers forward axis, kind/domain (from `spec.json` schema/params or the geometry), maps Blender
joints to motions and part codes (only unambiguous names, e.g. "radar"), uses `socket_bow` for the bow, and ranks
stock donors on the kind's platform by bounding-volume similarity, preferring civilian or military donors to match
the model's weapon parts. `spec.json` `meta` is not trusted; `custom_ops` code is never executed.

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
`character-unknown`. Warnings: `entity-system-artpart` (a donor weapon system refers to a part the model lacks),
`entity-support-outside`, `entity-attach-outside`, `dis-type-kind`. Inspect: `origin-z`, `origin-cg`,
`scale-too-large/small`, `forward-axis`, pivot and hierarchy rules, `kind-untested`.

## 7. Assumptions

| Assumption | Basis |
|---|---|
| Model frame: X right, Y forward, Z up (OSG style); the glb is converted glTF -> Blender -> forward axis | ACM 21.2 |
| Origin: land = ground-contact plane at the yaw axis; air/space = centre of gravity; surface/subsurface = waterline at the centre of buoyancy | ACM 21.2.1, Table 43 |
| Editor ships: +X bow, +Y port, +Z up, origin at the waterline amidships; parts named `Group__Part`; joint pivot = node origin; joint axis local, turned by `rotation_deg` | CONTRACT §1-§3 |
| A moving part is one node with its geometry beneath it, no rotation or scale in the neutral pose, child parts under the parent | ACM 21.4 |
| The donor is the same kind, and its systems suit the new object | UG 65.3.1 (a copy keeps the donor's behaviour) |
| `.entity` object type = super-type `1` (individual) + DIS type | UG D.3.1 |
| Model schema: `Watercraft` for ships, `Entity` otherwise, `DiGuyCharacter` for characters | ACM 15.1; counted in the install |
| Ship supports belong on the waterline, not the keel | install (144 of 159 stock ships); the SOE's calculate button put them on the keel. Open question |
| Copying the files into a custom SMS is enough (no Import Visual Models step needed) | worked in the hand imports; open question |
| `spec.json` `meta` may be stale; `custom_ops` is untrusted code | observed in the Blender exports |
| When manual and install disagree, the install is right | project rule |

## 8. Verification and tests

### 8.1 Test suites

| Suite | Command | Count (2026-11-14) | Covers |
|---|---|---|---|
| vrf-pipeline | `python -m unittest discover -s tests` | 109 | artparts, inspect rules, OpenFlight write/read, colours, entity building, hand-import comparisons, drafts, profiles, one full run per kind |
| editor `mak/tests` | `server/.venv/Scripts/python -m pytest mak/tests -q` | 127 (+5 skipped without the Harmony files) | the pipeline tests, package conversion, install, draft, export script, editor rules (no MAK-derived files) |
| editor `tests/mak` | `server/.venv/Scripts/python -m pytest tests/mak -q` | 1 (slow) | export of a built package with the real `medfTool` |
| editor server | `server/.venv/Scripts/python -m pytest server/tests -q` | 320 | endpoints, settings validation, export job with a fake runner, install refusals and backups |
| editor web | `cd web && npm run typecheck && npm test` | 133 | panel logic, DIS type checks |

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
- **Sensors stay at the donor's positions** (usually the origin). A sensor system in the `.entity` (`sensor` =
  visual, `sensor-2` = passive radar) is the simulated detection, and its `sensor-position` is where line-of-sight
  checks start (UG ch. 51). It is separate from the articulated radar, which is only the visual model turning on
  the mast. Harmony's sensors therefore look from the waterline amidships, about 56 m below its radar: terrain or
  a coastline in between can block detection that the mast would see over, and the geometric horizon is near zero
  instead of about 30 km. With the donor's short ranges (4 km visual, 10 km passive radar) the open-water effect
  is probably small. Stock ships mostly do the same (275 of 352 sensor systems at down = 0). Not checked in
  VR-Forces. Details and the fix option: `references/open-questions.md` Q16.
- Detonation power and weapon ammunition lists for a new munition are set by hand (UG 73, 74.2.2).
- Humans and animals use a stock DI-Guy character; a custom mesh would need DI-Guy authoring tools (ACM 25.2).
- Editor: exports ships only; crew are listed in `not_exported`. The Review panel has no kind field, and its
  donor suggestions search ship donors only.
- FBX route (Blender) exists but is not offered in the editor UI.
- No `.medf` encryption of the output (ACM 19.19).
- Short name is cut at a word boundary within 11 characters ("Harmony of").
- One SharedData version at a time; VR-Forces 5.1.1 only.

## 10. Outstanding work

Planned, not done:
1. One hand import per new kind (ground vehicle, aircraft, building, munition, human) and the comparison, then mark
   the profile `verified`.
2. Editor crew as DI-Guy characters: character picker in Review > MAK, contract change for crew settings.
3. Kind field and per-kind donor/part suggestions in the Review panel.
4. Animation sequences for rotors, propellers and wheels; damage switch nodes.
5. Sensor position from the model (option, not applied): radar sensor at a radar part's pivot (codes 5376 and up),
   visual sensor at `socket_bridge_eye` or `station__helm` when present, else the donor's; a report finding says
   where each sensor went (Q16).
6. Install test against a real custom SMS.
7. Review and merge `feat/mak-export` and `feat/mak-kinds`.

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
21.6 (animation), Table 58 (part codes), 25.1-25.2 (DI-Guy). UG 62.4 (navigation data), 64.4 (SMS visuals),
65.2.10 (Import Visual Models), 65.3.1 (copy a model), 66.1.1-66.1.10 (size, supports, object geometry,
cultural features, lifeform visuals), 66.5 (munitions), 73 (weapon ammunition), 74.2.2 (detonation power),
D.3.1 (object super-types).
