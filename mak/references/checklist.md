# Import correctness checklist

Each check has a tier and the code that implements it, or TODO. Articulation rules are explained in `articulation.md`.

## Tier 1: input (.glb), `pipeline/inspect_glb.py`
| Check | Code | Status |
|---|---|---|
| Valid glTF 2.0 binary | read_glb | done |
| Real-world scale (0.05 m to 400 m longest side) | scale-too-large / scale-too-small | done |
| Origin per domain (manual 21.2.1) | origin-z / origin-x / origin-y / origin-cg | done (heuristic) |
| Faces +Y after forward-axis rotation | forward-axis | done (heuristic) |
| Triangle budget | poly-budget | done |
| Texture size and power-of-two | texture-size / texture-npot | done (PNG only) |
| glTF animations will be dropped | gltf-animations | done |
| **Articulation (manual 21.4, 21.1.2)** | | |
| Part node exists, name unique | artpart-node-missing / duplicate-node-name | done |
| Part code known (Table 58 or map), not duplicated | artpart-code-unknown / artpart-code-duplicate | done |
| Motion valid, min <= max | artpart-motion / artpart-limits | done |
| Child part is beneath its parent part | artpart-hierarchy / artpart-parent-unknown / artpart-parent-undeclared | done |
| Geometry beneath the part node | artpart-no-geometry | done |
| Pivot on the part (origin at rotation axis) | artpart-pivot | done (heuristic) |
| No static rotation or scale on part nodes | artpart-static-rotation / artpart-scale | done |
| Node name doesn't mean a different code in the map | artpart-name-conflict | done |
| Mapped node names that aren't declared | artpart-undeclared | done |
| LOD nodes named High/Med/Low | lod-naming | done |
| Khronos gltf-validator clean | - | TODO (optional external tool) |

## Tier 1b: converted model (`verify_model` stage)
Codes are prefixed `flt-` or `fbx-`. The FBX is re-imported in Blender; the OpenFlight file is re-read by
`openflight.read_flt`.
| Check | Code | Status |
|---|---|---|
| Every part present (FBX: under its map name) | *-part-missing | done |
| Part hierarchy kept | *-hierarchy | done |
| Pivots kept, parts unrotated in neutral pose | *-pivot-moved / *-part-rotated | done |
| Bounds match the glb (axes, scale, no lost geometry) | *-bounds-mismatch | done |
| flt: part is a DOF with the right `@dis articulated_part` comment and limits | flt-part-not-dof / flt-part-comment / flt-dof-limits | done |
| flt: header revision 1610, units metres; root `@dis entity_type` | flt-revision / flt-units / flt-entity-type | done |
| flt: **MAK's own reader** (medfTool) loads it, sees every part comment, produces geometry | mak-loaded / mak-part-missing / mak-no-geometry / mak-load-failed | done |

## Tier 2: output, `pipeline/validate.py`
| Check | Code | Status |
|---|---|---|
| XML parses, no `${...}` left | xml-parse / unfilled-placeholder | done |
| `.ommx` filename resolves to the FBX | model-file-missing | done |
| DIS type format and no collision with the install | dis-type-format / dis-type-collision | done |
| Model definition name is unique | model-def-collision | done |
| `.metx` element name defined in `.leaf` | element-def-missing | done (once .leaf exists) |
| `.entity` objectType = `1:` + `.metx` type | entity-objecttype-format / -mismatch / entity-supertype | done |
| Art parts: unique, parents exist, (value, rate) params, fields match, limits | entity-artpart-* | done |
| Systems refer only to parts the entity has | entity-system-artpart | done |
| Attach points and supports inside the bounding volume | entity-attach-outside / entity-support-outside | done |
| Donor object geometry not inherited | entity-object-geometry | done |
| FBX_NodeNameMap additions pending deploy | node-map-additions (generate) | done |

## Tier 3: regression, `tests/`
`tests/test_pipeline.py`: fixture glbs (`tests/make_fixture.py`) including one per articulation fault;
.entity generation against the 2S1 donor; Blender end-to-end, including the -Y forward-axis rotation.
TODO: golden-file comparison of the generated files.

## Tier 3b: hand import comparison
On the test machine, do the import in the SOE: copy the donor via "New Model from Existing (Copy)" (UG 65.3.1)
into a new SMS that includes EntityLevel, and point it at the pipeline's model file. Then run
`powershell -ExecutionPolicy Bypass -File mak/scripts/export_hand_import.ps1 -SmsName <sms> -Since "<start time>"`
and bring the zip back. Compare its `sms/<sms>/vrfSim/*.entity` and `sms/<sms>/gui/visuals/*` with `output/<name>`.
This is the spec for the `.leaf` generator and for attach-point and support conventions (open questions 4, 9, 13, 14).

## Tier 4: runtime smoke test (TODO, needs licence and headless setup)
1. Deploy output into a **copy** of an SMS (never stock), via SOE "Import Visual Models" (UG 65.2.10).
2. In the SOE visual panel, tick Animate on each articulated part (UG 66.3.3) and check it moves about the
   right pivot.
3. Launch `vrfSimDIS.exe` with that SMS and a minimal scenario. Create the entity with `remoteControlDIS.exe`, set
   an articulated part (UG "Set > Appearance Articulated Part"), and check it moves, keeps the right type, and
   the log shows no model-load errors.
