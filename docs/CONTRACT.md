# Contract: what every part of the editor agrees on

The app has five parts built in parallel: ship builders, the crew builder, the Unreal import
harness, the server, and the web app. They meet only at the shapes below. If a part needs one
of these to change, the change is made here first, and every part that reads it is updated.

The approved design is the canvas https://claude.ai/artifact/67Wgj688QSRc4PycUKmw1u and the
plan in `docs/PLAN.md`.

## 1. Coordinates and units

- **Blender source frame (all builders):** metres; **+X bow, +Y port, +Z up**. Ships have their
  origin at the **waterline amidships**, so the keel sits at minus the draught. Humans have their
  origin **between the feet**, facing +X.
- **glTF files:** exported with Blender's default **+Y up** conversion. Never re-export in
  another frame.
- This matches StraitsSim's `strait.ship_model/1` contract
  (`/Users/wyxlester/dev/straitssim-w1/docs/model-export-contract.md`), so a ship made here also
  validates there.

## 2. Names

- **Every Blender object that is a part is named `Group__Part`, or `Group__Part__Sub`**, using
  only `[A-Za-z0-9_]`, e.g. `Roof__Searchlight`, `Transom__Outboard_1`. Unreal rejects `/`, `.`
  and spaces in asset names, so they are banned.
- **Display label:** replace `__` with ` › ` and `_` with a space ("Roof › Searchlight").
- **Crew stations** are Empty objects named `station__<name>` (e.g. `station__helm`), parented to
  the part they stand on, so they move with it.
- **The nine StraitsSim sockets** (`socket_bridge_eye`, `socket_radar`, `socket_light_*`,
  `socket_bow`, `socket_prop`) are Empty objects with exactly those names.

## 3. Moving parts (articulation)

A moving part is an object **whose origin is its pivot**, with the custom property
`me3d_joint` (a JSON string):

```json
{"axis": "z", "min_deg": -150, "max_deg": 150, "priority": "P0", "kind": "revolute"}
```

`axis` is in the part's local frame. `priority` is P0, P1 or P2, as in the research's rig plan.
Children are parented in Blender, so the hierarchy survives into glTF. Fixed parts carry no
property.

## 4. Builder command line (Blender, headless)

```
Blender -b [--factory-startup] --python blender/build_ship.py -- --spec <spec.json> --out <dir>
Blender -b                      --python blender/build_crew.py -- --spec <spec.json> --out <dir>
```

`build_crew.py` must **not** use `--factory-startup`, because MPFB2 is a user extension.
Ship builds should use it, for reproducibility.

**Every build writes, into `<dir>`:**

| File | What |
|---|---|
| `model.glb` | the model, per sections 1 to 3 |
| `model.blend` | the source scene, for debugging and later edits |
| `parts.json` | `[{"path","label","parent","joint","station","origin_m","rotation_deg","bounds_min","bounds_max","triangles"}]` (all in the Blender world frame, metres; `origin_m` is the object's world origin, which for a moving part is its pivot; `rotation_deg` is its world XYZ Euler rotation; `joint` is the parsed `me3d_joint` or null; `station` is the parsed `me3d_station` for stations, otherwise null) |
| `build_report.json` | `{"ok": bool, "errors": [str], "warnings": [str], "triangles": int, "bounds_min": [x,y,z], "bounds_max": [x,y,z], "seconds": float}` |

The exit code is 0 only when `ok` is true. Nevertheless, **callers read `build_report.json`, not
the exit code**: Blender returns 0 even when a script raises an error, unless the script calls
`sys.exit(1)` itself.

## 5. Ship spec — `me3d.ship_spec/1`

```json
{
  "schema": "me3d.ship_spec/1",
  "type": "container | sampan | tanker",
  "id": "container_ship",
  "name": "Container ship",
  "params": {"loa_m": 200, "beam_m": 32.2, "draught_m": 11.5, "depth_m": 20.5,
             "hull_color": [0.55, 0.12, 0.10], "...type-specific": "..."},
  "overrides": {"Roof__Searchlight": {"offset_m": [0, 0, 0], "rotate_deg": [0, 0, 0],
                 "scale": [1, 1, 1], "color": [1, 1, 1], "hidden": false}},
  "joint_limits": {"Deck__Crane_1": {"min_deg": -170, "max_deg": 170}},
  "custom_ops": [{"id": "op_1", "description": "add a horn on the mast", "code": "<python>"}]
}
```

- `params` are the builder's own knobs; each builder documents its own in `catalog/ships/<type>.json`.
- `meta` (optional) holds the `strait.ship_model/1` fields that `params` don't: `type` (from
  StraitsSim's list; a sampan uses `fishing`, the closest), speeds, propulsion, `rcs_class`,
  notes, source. Length, beam, draught and depth in `ship/<id>.json` always come from `params`,
  because that is what was built.
- A catalog file is a default spec plus documentation keys: `description`, and `param_docs`
  mapping each param to `{"doc", "min", "max"}` (or `{"doc", "options": [...]}`).
- `overrides` are applied after the build, to parts by path.
- `custom_ops` run last, inside Blender, with the helper module `me3d` (from `blender/common.py`)
  in scope. This is how the AI adds geometry no builder knob covers. Every object a custom op
  creates must follow section 2.

## 6. Crew spec — `me3d.crew_spec/1`

```json
{
  "schema": "me3d.crew_spec/1",
  "id": "crew_male_default",
  "name": "Default male crew",
  "body": {"gender": "male", "height_cm": 178, "weight": 0.5, "muscle": 0.5, "age_years": 30,
           "skin": "medium"},
  "clothes": {"suit": "male_worksuit01", "shoes": "shoes01", "hat": null, "suit_color": null},
  "equipment": ["life_vest"],
  "animations": ["idle", "walk", "brace", "hands_up", "squat"]
}
```

**Made by description** (Lester, 29 Oct: "describe in words what the human should wear … what
equipment the human should hold"). Optional fields, all absent in the default spec:

| Field | Meaning |
|---|---|
| `body.hair_color` | `[r,g,b]` 0..1 or null. Recolours hair *and* eyebrows to read as that colour (blonde must look blonde, not a darkened tint), keeping the strand texture. |
| `clothes.shoes_color`, `clothes.hat_color` | `[r,g,b]` or null, like `suit_color`. |
| `custom_items` | `[{"id":"item_1","description":str,"code":str}]`: Blender Python the AI writes, run after the human and catalog equipment are built and **before** animations bake, with `bpy`, `mathutils` and helper module `crew` (`blender/crew/custom.py`) in scope. Same code rules as ship custom ops (no os/sys/subprocess, open, eval, exec). |

Objects a custom item creates are named `Clothes__<Name>` (worn, skinned to the skeleton) or
`Equipment__<Name>` (held or attached, parented to a bone), `[A-Za-z0-9_]` only, and are listed
in `parts.json` like catalog items. Garments are cut from the body's own surface for the bones
they cover and pushed out, so they fit every body slider setting and deform with every animation.
`blender/crew/custom.py` exposes, each with a one-line docstring (the AI's tool docs are generated
from them): `body()`, `armature()`, `REGIONS` (named bone sets: torso, upper_arms, forearms, hands,
pelvis, thighs, shins, feet, neck, head), `garment(name, regions, color, ...)`,
`hold(obj, hand)`, `attach(obj, bone)`, `material(name, color)`, and the low-poly mesh helpers used
by catalog equipment. A failing item is reported as a build error naming its id and line.

- **Rig:** MPFB2 `game_engine` skeleton for every human, the same skeleton for all.
- Animations are **glTF animations named exactly as listed**, on that skeleton.
- Equipment is a mesh named `Equipment__<Name>`, parented to a bone.
- **Crew catalog file** (`catalog/crew/<id>.json`): the default spec's keys, plus `description`,
  `param_docs` (body fields as `{"doc", "min", "max"}`) and `options`, which lists what is
  actually installed and allowed:

  ```json
  "options": {"suit": ["male_worksuit01"], "shoes": ["shoes01"], "hat": [null, "fedora01"],
              "skin": ["light", "medium", "dark"], "equipment": ["life_vest", "helmet"],
              "animations": ["idle", "walk", "brace", "hands_up", "squat"]}
  ```

  The server's `GET /api/catalog` and the AI's validation read `options`; a value not listed
  there is refused.

## 7. Scene (assembly) spec — `me3d.scene_spec/1`

```json
{
  "schema": "me3d.scene_spec/1",
  "id": "scene_1",
  "name": "Container ship with crew",
  "ship": {"project": "<project id>", "version": 3},
  "crew": [{"project": "<project id>", "version": 1, "station": "station__bridge_wing_port",
            "animation": "idle"}]
}
```

## 8. Export package — `me3d.package/1`

This is what goes to Unreal now and MAK later. One folder:

```
<package_id>/
  manifest.json
  ship/<ship_id>.glb          the ship (sections 1-3)
  ship/<ship_id>.json         strait.ship_model/1 metadata (StraitsSim-compatible)
  ship/articulation.json      [{"part","parent","axis","min_deg","max_deg","priority","pivot_m":[x,y,z]}]
  crew/<crew_id>.glb          one per distinct crew member
  research/research.json      the research for the ship, or absent if none
```

`manifest.json`:

```json
{
  "schema": "me3d.package/1",
  "id": "pkg_container_ship_v3",
  "name": "Container ship with crew",
  "created": "2026-10-29T12:00:00Z",
  "units": "metres",
  "source_frame": "+X bow, +Y port, +Z up; glTF files are +Y up",
  "ship": {"id": "container_ship", "glb": "ship/container_ship.glb",
           "meta": "ship/container_ship.json", "articulation": "ship/articulation.json"},
  "crew": [{"id": "crew_male_default", "glb": "crew/crew_male_default.glb",
            "station": "station__bridge_wing_port", "animation": "idle",
            "location_m": [x, y, z], "rotation_deg": [0, 0, 90]}],
  "research": "research/research.json"
}
```

`location_m` and `rotation_deg` are each crew member's world transform **in the ship's Blender
frame**, taken from their station at the moment of export.

## 9. Unreal import harness

```
unreal/run_import_test.sh <package_dir>
```

This imports the package into a throwaway UE 5.8 project (`unreal/HarnessProject`), builds a
level with the ship and its crew at their stations, saves it, reloads it, and writes
`<package_dir>/unreal_import_report.json`:

```json
{"ok": bool, "engine": "5.8", "level": "/Game/ME3D/<id>/L_<id>",
 "checks": [{"name": str, "pass": bool, "detail": str}]}
```

**The script's exit code comes from `ok`, never from Unreal's own exit code**, because Unreal
returns 0 on failure (library: `unreal-automation-runner-exits-zero-on-failed-tests`).

## 10. Server HTTP API (FastAPI, port 8787; the web app proxies `/api`)

Every body is JSON. Long work returns `{"job_id"}`; poll `GET /api/jobs/{id}`, which returns
`{"id","status":"queued|running|done|error","log":[str],"result":any,"error":str|null}`.

| Method and path | Body | Returns |
|---|---|---|
| GET `/api/health` | | `{"blender":bool,"mpfb":bool,"ai":bool,"unreal":bool}` |
| GET `/api/catalog` | | `{"ships":[{"type","name","description"}],"crew":[{"id","name"}],"clothes":[str],"equipment":[str],"animations":[str]}` |
| POST `/api/route` | `{"message","attachments"?:[file name]}` (names only; the files go with the carried message) | `{"destination":"ship_research|ship_build|crew|animation|assembly|review|clarify","reason","project_id":str|null}` |
| GET `/api/projects` | | `[ProjectSummary]` |
| POST `/api/projects` | `{"kind":"ship|crew","base":"container|sampan|tanker|male_default","name"?}` | `{"project", "job_id"}` (first build) |
| GET `/api/projects/{id}` | | `Project` (below) |
| GET `/api/projects/{id}/versions/{n}/model.glb` | | the GLB |
| GET `/api/projects/{id}/versions/{n}/parts.json` | | the parts list |
| POST `/api/projects/{id}/spec` | `{"patch": {...}}` (deep-merged into the spec; Inspect drawer edits) | `{"job_id"}` → new version |
| POST `/api/uploads` | same as below, before there is a project (Home) | `{"attachment"}`; copied into the project when a chat there names it |
| POST `/api/projects/{id}/uploads` | `{"name","type","data"}` (`data` is base64; see Attachments) | `{"attachment"}` |
| POST `/api/projects/{id}/chat` | `{"message","mode":"build|research","attachments"?:[upload id]}` | `{"job_id"}` |
| POST `/api/projects/{id}/pins` | `{"part","point":[x,y,z],"text","author"}` | `{"pin","job_id"}` |
| POST `/api/projects/{id}/pins/{pin_id}/reply` | `{"text"}` | `{"job_id"}` |
| PATCH `/api/projects/{id}/pins/{pin_id}` | `{"status":"open|resolved"}` | `{"pin"}` |
| POST `/api/projects/{id}/undo` | | `{"project"}` |
| GET `/api/jobs/{id}` | | Job |
| GET `/api/scenes` / POST `/api/scenes` | `SceneSpec` without `id` | `[Scene]` / `{"scene"}` |
| GET `/api/scenes/{id}` | | `Scene` with `stations` from the ship's parts |
| PUT `/api/scenes/{id}` | `SceneSpec` | `{"scene"}` |
| POST `/api/scenes/{id}/export` | | `{"job_id"}` → result `{"package_dir","zip_url"}` |
| POST `/api/scenes/{id}/unreal-test` | | `{"job_id"}` → result: the Unreal report |
| GET `/api/files/{path}` | | serves files under the data folder (zips, packages) |

`Project`:

```json
{
  "id": "p_ab12", "kind": "ship", "name": "Container ship", "base": "container",
  "version": 3, "spec": {}, "versions": [{"n": 3, "at": "...", "summary": "...", "ok": true}],
  "model_url": "/api/projects/p_ab12/versions/3/model.glb",
  "parts": [ /* parts.json of the current version */ ],
  "build_report": {},
  "pins": [{"id": "pin_1", "part": "Roof__Searchlight", "point": [x,y,z], "status": "open|working|waiting|resolved",
            "author": "You", "messages": [{"who": "You|AI|Customer", "text": "...", "at": "..."}],
            "before_version": 2, "after_version": 3}],
  "messages": [{"who": "You|AI", "text": "...", "at": "...", "attachments"?: [Attachment]}],
  "research": null
}
```

**Attachments** (pictures and documents sent with a chat message). An upload is stored at once and
returns `{"id","name","type","size","url"}`; the chat message then names it by `id`, and the AI
receives the file itself alongside the text: pictures as images, PDFs as documents, text files as
text. Accepted types and limits (the model's own limits):

| Kind | MIME types | Max size |
|---|---|---|
| Picture | `image/png`, `image/jpeg`, `image/webp`, `image/gif` | 5 MB |
| Document | `application/pdf` | 20 MB |
| Text | `text/plain`, `text/markdown`, `text/csv` | 1 MB |

Anything else is refused with 400 and a sentence saying what to do (e.g. save a Word file as PDF).
Uploads go to a cloud LLM with the message, so the public-sources-only rule applies to them too.

The pin `point` is in **glTF/three.js coordinates** (Y-up), because that is where the click
happened; `part` is the clicked mesh's node name.

## 11. Data layout (server)

```
data/projects/<id>/project.json
data/projects/<id>/v<n>/{spec.json, model.glb, model.blend, parts.json, build_report.json}
data/projects/<id>/uploads/<upload id>.<ext>   and <upload id>.json (name, type, size)
data/uploads/<upload id>.<ext>                 attached on Home, before a project exists
data/scenes/<id>.json
data/packages/<package_id>/...  and <package_id>.zip
```

`data/` is git-ignored.

## 12. Research — `me3d.research/1`

Stored on the project as `research`, and copied to `research/research.json` in the package. The
standard is the PCG 3-Series dossier described in `docs/PLAN.md`.

```json
{
  "schema": "me3d.research/1",
  "subject": "PCG 3-Series PC Class, PC 303 as photographed in 2025",
  "summary": {"verdict": "Coverage is strong above the waterline; dimensions remain open.",
              "existing_work": "No existing model found in the project."},
  "sources": [{"id": "S01", "title": "...", "url": "...", "status": "Official",
               "what_it_proves": "..."}],
  "claims": [{"claim": "Four outboards", "sources": ["S01", "P02"], "status": "Official + photo",
              "consequence_for_model": "Model four separate motor assemblies; no waterjets."}],
  "open_questions": [{"question": "...", "meanwhile": "..."}],
  "articulation": [{"part": "Transom__Outboard_1", "motion": "steer, tilt, prop spin", "priority": "P0"}]
}
```

`status` is one of: Official, Photographed, Photo reading, Inference, Comparison only,
Secondary lead, Unverified (a claim may combine two with " + "). The server validates this
shape and returns errors to the AI rather than saving a malformed record.
