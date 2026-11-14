# Articulated parts: what the model must look like

Sources: "manual" = Adding Content to MAK ONE Applications (shipped with MAK ONE products; not in this repository);
"UG" = VR-Forces 5.1.1 Users Guide (shipped with VR-Forces; not in this repository); "install" = files under `C:\MAK`.

## Two model routes
- **OpenFlight (`flt`, default, no Blender).** Each part is written as a DOF node: origin = pivot, local axes =
  model axes, limits on its motion axis. It carries a WRM comment in the same format as MAK's stock models (as
  printed by `medfTool --writeNodeInfo` on 2S19MstaS.medf):
  `@dis articulated_part 4096    rotate_min 0,0,-180  rotate_current 0,0,0  rotate_max 0,0,180  rotate_step 0,0,1`.
  The vector order is (pitch, roll, yaw): elevation goes in the first value, roll in the second, azimuth in the
  third. Translational parts get just `@dis articulated_part <code>`, since MAK doesn't use the motion data
  (manual 21.4). The root group carries `@dis entity_type <DIS type>`, as stock models do. Node names are free;
  rule 1 below applies only to FBX.
- **FBX (`fbx`, Blender).** Parts are matched by node name through FBX_NodeNameMap.csv (rule 1).

## How a part reaches VR-Forces (two separate halves)

| Half | Where it lives | What reads it | Built by |
|---|---|---|---|
| **Visual**: the turret mesh actually turns | node names in the FBX, matched against `appData/importConfig/FBX_NodeNameMap.csv` | renderer (VR-Vantage) | `convert` renames nodes |
| **Simulation**: the sim knows it can slew the turret | `DtSimArtPartDescriptorList` in the `.entity` (+ weapon systems referring to part codes) | vrfSim | `generate` builds from manifest + glb pivots |

Both are keyed on the **DIS articulated part code** (manual Table 58). The SOE merges parts from sim
components and the visual model into one list; overrides go into `.entity` (UG 66.3). A part can exist only in
the sim (it will not move visually, UG 66.3.1) or only visually (wheels and rotors are animated by the
renderer, UG 46.14). Parts that come from the model cannot be removed in the SOE (UG 66.3.5).

## Model rules (checked by `inspect_glb.check_articulation` and `verify_fbx`)

1. **(FBX only) One node per part, named exactly as in FBX_NodeNameMap.csv** (manual 21.1.2). FBX has no standard for
   moving parts. VR-Forces only recognises names in the map, e.g. `turret_turn` (4096),
   `main_cannon_elev` (4416), `wheel_1_1_group` (9120). Any other name is static geometry.
   - In Blender you may use any name. The manifest declares `node` + `dis_part`, and `convert` renames the
     node to the map's name for that code. If the map has no name for a code, `generate` writes
     `FBX_NodeNameMap.additions.csv`, to be appended to the installed map at deploy time.
   - Codes in the installed map: turrets 4096/4128/4160, guns 4416/4448/4480, wheels 9120-9728.
2. **All of a part's geometry sits beneath its node** (manual 21.4). LODs of a part go under the part node.
3. **Child parts nest under their parent part**: gun under turret, turret under body (manual 21.4, figure 130).
   The manifest `parent` must be an ancestor in the node tree.
4. **The node origin is the pivot**: set the object origin to the rotation axis (turret ring centre, gun
   trunnion, door hinge). The pivot becomes the `.entity` attach-point.
5. **No static offsets.** With all DOF values zero, the model is in its neutral pose (manual 21.4). Apply
   rotation and scale in Blender (`convert` also bakes them, and `verify_fbx` checks the result).
6. **Neutral pose** = turret facing forward (+Y), gun level, doors closed, gear down/extended as
   appropriate. Limits in the manifest are measured from that pose.
7. **Unique node names.** Parts are matched by name, so duplicates break the match.
8. **LOD nodes** must be named `High`, `Med`, `Low` (map rows `@dis lod 0/1/2`). Other LOD-like names are
   flagged.
9. **Damage state**: the map has `SimpleVehicle` -> `Wreck` (DamageState row). Switch nodes
   (`@dis switch damage`, `@dis state ...`, manual 21.5) need FBX user properties. See open questions.
10. glTF animations are dropped. VR-Forces drives parts from simulation state; flipbook animation uses switch
    nodes (manual 21.5.1, 21.6).

## Frames and units
- Model: X right, Y forward, Z up, metres (manual 21.2).
- `.entity` bodyPosition: forward = model Y, right = model X, down = -model Z.
- Root part attach-point = absolute pivot. Child part attach-point = pivot minus the parent's pivot (stock 2S1:
  4096 at (-1.31, 0, -1.71), its 4128 child at (0.2, 0, -0.15)).
- `end-point` for guns and launchers = muzzle offset from the pivot (stock 2S1 gun: forward 4.2).
- Manifest limits are degrees or metres; `.entity` stores radians or metres.

## .entity descriptor shapes (from 1,545 stock .entity files)
```xml
<DtSimArtPartDescriptor paramName="primary-turret-4096">
  <DtRwList paramName="art-part-param-list"><int paramName="param-type">11</int><int paramName="param-type">12</int></DtRwList>
  <bodyPosition paramName="attach-point" forward=".." right=".." down=".."/>
  <bodyPosition paramName="end-point" .../>
  <int paramName="art-part-type">4096</int>
  <int paramName="parent-part-type">-1</int>      <!-- -1 or 0 = none; else parent code -->
  ...motion fields...
  <string paramName="comment"/>
</DtSimArtPartDescriptor>
```
| motion | param pair (DIS metric) | fields |
|---|---|---|
| extension | 3, 4 | extension, extension-rate, max-extension, max-extension-rate |
| translate_x / y / z | 5,6 / 7,8 / 9,10 | vectors: max-translation, max-translation-rate, min-translation, translation, translation-rate |
| azimuth | 11, 12 | azimuth, azimuth-neutral-offset, azimuth-rate, max-azimuth, max-azimuth-rate, min-azimuth |
| elevation | 13, 14 | elevation, elevation-rate, max-elevation, max-elevation-rate, min-elevation |
| rotation | 15, 16 | max-rotation, max-rotation-rate, min-rotation, position, rotation-rate |
| visual | (none) | not written to .entity |

## Systems refer to parts by code
Weapon `componentSystem`s carry `gun-art-part-type`, `turret-art-part-type`, `elevating-turret-art-part-type`,
`rot-turret-art-part-type`, `launcher-art-part-type` and others (UG 73.1.1: `(art-part-list (primary-gun (art-part-type
$gun-art-part-type) (attach-point ...)))`). When the systems are cloned from a donor, they keep the donor's codes.
`validate` warns (`entity-system-artpart`) when a system names a code the entity has no part for. Example: the
2S1 cannon expects a separate 4128 elevating mount.

## Codes worth knowing (manual Table 58)
turret 1-10: 4096 + 32(n-1) · gun 1-10: 4416 + 32(n-1) · launcher: 4736+ · radar: 5376+ · secondary turret
5696+ / gun 6016+ · landing gear 3072 · hatch 3232 · periscope 2048 · main/tail rotor 1184/1216 ·
wheels left n = 9120 + 64(n-1), right n = 9152 + 64(n-1) · towed array decoy 16064 (MAK).
Seen in the install but not in Table 58: car door 7840, boom extension 8192.
