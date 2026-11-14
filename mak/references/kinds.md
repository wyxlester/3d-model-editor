# Model kinds

`pipeline/profiles.py` holds one profile per kind. The manifest's `kind` picks it; older manifests with only
`domain` still work (`land` -> `ground_vehicle`, `air` -> `fixed_wing`, `surface` -> `surface_ship`, ...).
A value written in the manifest (`platform_ope`, `model_category_dir`) overrides the profile.

ACM = Adding Content manual, UG = Users Guide, install = stock EntityLevel + ModelData definitions.

| kind | DIS kind:domain | platform `.ope` | ModelData folder | schema | origin | supports | status |
|---|---|---|---|---|---|---|---|
| `ground_vehicle` | 1:1 | Ground_Vehicle | Vehicles/Wheeled | Entity | ground | lowest geometry | untested |
| `fixed_wing` | 1:2 | Fixed_Wing_Aircraft | Vehicles/FixedWing | Entity | centre of gravity | lowest geometry (gear) | untested |
| `rotary_wing` | 1:2 | Rotary_Wing_Aircraft | Vehicles/RotaryWing | Entity | centre of gravity | lowest geometry | untested |
| `surface_ship` | 1:3 | Surface_Ship | Vehicles/Surface | Watercraft | waterline | hull at the waterline | **verified** |
| `submarine` | 1:4 | Subsurface_Vehicle | Vehicles/Subsurface | Entity | waterline | donor's | untested |
| `space_object` | 1:5 | Space_Object | Vehicles/Space | Entity | centre of gravity | donor's | untested |
| `building` | 5:1 | Cultural_Feature | Structures/Buildings | Entity | ground | lowest geometry | untested |
| `static_object` | 5:1 | Cultural_Feature | Objects | Entity | ground | lowest geometry | untested |
| `munition` | 2:any | by `munition_class` | Munitions/... | Entity | centre of gravity | donor's | untested |
| `human` | 3:1 | Human | (no model) | DiGuyCharacter | - | donor's | untested |
| `animal` | 3:1 | Animal | (no model) | DiGuyCharacter | - | donor's | untested |

"verified" means compared with a hand import in the Simulation Object Editor. Every other kind reports
`kind-untested` until that has been done (`mak/scripts/export_hand_import.ps1`, then diff against the export).

## Evidence
- Origin per domain: ACM 21.2.1, Table 43.
- Schema: every stock model definition is `Entity`, except Vehicles/Surface and buoys (`Watercraft`) and
  characters (`DiGuyCharacter`) (ACM 15.1 lists the schemas; counted in the install).
- Supports are where the model touches the ground (UG 66.1.3). Stock ships: down = 0 on 144 of 159. Stock
  aircraft sit on their gear below the origin (down = 0 on 4 of 190).
- A copy starts with the donor's behaviour and systems (UG 65.3.1), so the donor must be the same kind.
- Stock type mappings use -1 as a wildcard (the hangar 5:1:0:2:1:1:0 is mapped by 5:1:-1:2:1:1:-1), so the
  donor's element definition is found by best match (`leaf.element_name_for_type`).

## Buildings and static objects
Cultural features: `gravity-aligned` is set (UG 66.1.8). With `"walkable": true` in the manifest, the model's own
`.flt` becomes the object geometry and `embed-into-terrain` is set, so characters can walk in and around it;
navigation data is generated when the object is placed in a scenario (UG 66.1.6, 66.1.7).

## Munitions
Simulated munitions have visual models and are configured like other objects (UG 66.5.1). `munition_class`
(`missile`, `ballistic_missile`, `bomb`, `rocket`, `round`, `torpedo`, `naval_mine`) picks the platform file
and the `Munitions/` folder. On top of the usual files the pipeline writes
`sms/ammunitionParams.additions.mtl`: one entry, cloned from the donor munition's entry when it has one. A
weapon can only fire what the SMS's `ammunitionParams.mtl` lists, and the `munition-type` may not contain -1
(UG 73). Left as pending, because they change other things than this object:
- merging the entry into the custom SMS's `ammunitionParams.mtl` (`pipeline.mtl.merge_ammunition`),
- detonation power (UG 74.2.2, Detonation tab),
- adding the munition to each weapon system that should fire it (UG 73).
Non-simulated ammunition (UG 66.5.3) has no model and is out of scope.

## Humans and animals
Lifeforms are DI-Guy characters (ACM 25.1-25.2; UG 66.1.10). No model is imported and `model.glb` is not
needed. The manifest names a `character_type` from `appData/settings/vrfSim/character_animations_table.mtl`
(`python -m pipeline.mtl characters [filter]`), optionally `character_appearance` and `animation`. Output: the
`.entity` (donor lifeform with the character set), plus the donor's element definition renamed and a `.metx`
when the donor has one of its own. Stock animals have none: VR-Forces shows them from the `.entity` alone.
A custom mesh needs DI-Guy authoring tools (ACM 25.2 step 1); that is an open question for DTS.

## Not done
- Rotors, propellers and wheels as `@dis animation` sequences (ACM 14.3, 21.6): they stay static.
- Damage-state geometry (`@dis switch damage`, ACM 21.5).
