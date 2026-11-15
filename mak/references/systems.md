# Systems: sensors, weapons and other systems

How the importer gives a new entity its systems, and the evidence behind each rule. Code: `pipeline/systems.py`.
UG = VR-Forces 5.1.1 Users Guide; ACM = Adding Content to MAK ONE Applications; "install" = the stock
`EntityLevel` simulation model set (958 platform entities, 245 sysdefs).

## What a system is
- A `componentSystem` element in the `.entity`, with a `systemName` and a `platform` pointing at a `.sysdef`
  under `@(system-dir)` (UG 71.1, 71.4, 71.4.2). Its children (`int`, `real`, `bool`, `string`,
  `bodyPosition`, named by `paramName`) override the sysdef's `$variables`. A variable left out takes the
  sysdef's default (UG 71.5.1).
- **Categories.** The SOE groups systems as Damage, Sensors, Weapons, Movement and Additional Systems (UG 66.4).
  The install has the same folders: `damage` 17, `movement` 56, `other` 58, `sensors` 17, `weapons` 97.
- **Limits.**
  - One damage system and one movement system per object (UG 66.4.2-3).
  - `systemName`s are unique (UG 72.1.1) and do not start with a digit (UG 71.4.1).
- **Format.** A `.sysdef` is an S-expression, not XML.
  - Weapon and damage sysdefs include a `.template_sysdef` through `(system-definition (filename ...))`.
  - The template's meta-data parameters count unless `merge-with-sub-systems` is False (UG 71.4, 73.1.1-2).
  - `systems.read_sysdef` folds the template in.
- **Which systems fit an object.** A system fits only if its `allowed-state-repository-types` names the
  object's state repository (UG 71.1.4). The SOE offers only those (UG 66.4).
  - The repository is the platform `.ope`'s `(parameter-type ...)`, e.g. `Surface_Ship.ope` ->
    `surface-entity-param`.
  - The catalog reads it from the install; profiles don't carry it.
- **Not systems.**
  - Radios are declared in the `.ope` (UG 76.2, `Surface_Ship.ope` `radios`). Out of scope.
  - What every object of a platform has (collision avoidance, script controllers) is in the `.ope` too (UG 71.3).

## How systems tie to the visual model
| System | Link to the model | Evidence |
|---|---|---|
| Sensors | `sensor-position` (body frame forward/right/down), which feeds the sensor's `sensor-offset` | UG 71.4.2, 72.1.2; 1016 stock sensor systems carry it |
| Gimbaled visual sensor | `gimbaled-sensor-azimuth/elevation-art-part-type` -> parts 15424/15744 | `Baykar Bayraktar TB2.entity`; the only sensor bound to parts |
| Weapons on mounts | `*-art-part-type` parameters -> the `.entity`'s articulated parts | UG 73.1.1 (`gun-art-part-type`), `mm-gun.template_sysdef` `art-part-type-parameter-data` |
| Weapons without parts | `muzzle-offset`, launcher `attach-point` | `F-16A_Fighting_Falcon.entity` (M61), `AH-64D_Apache.entity` (Hellfire) |
| Fixed-code weapons | codes written inside the sysdef | `m1a2-multigun.sysdef` (turret 4096 / gun 4416 ...) |

- **No stock sensor is bound to a radar part** (5376+). The turning radar mesh is visual only. So the importer
  moves the radar *sensor* to the radar part's pivot, rather than binding the two.
- **Matching bound codes.** In the install, 1372 of 1383 codes that systems bind match a part in the same
  `.entity`. The other 11 are -1, i.e. no parent.
- **Families each parameter takes** (install scan; a part's family comes from its Table 58 range, ACM 21.7).
  The sysdef's `art-part-category` alone is not enough:

  | Parameter | Stock entities bind it to |
  |---|---|
  | `turret-art-part-type`, `rot-turret-art-part-type` | turret codes |
  | `elevating-turret-art-part-type` | turret, gun and launcher codes |
  | `gun-art-part-type` | gun codes, and 8192 / 8224 / 8256 (e.g. Arleigh Burke MK45 gun on 8192) |
  | `launcher-art-part-type` | launcher codes, and 8192 / 8224 / 8288 |

  The catalog records these (`Catalog.bindings`) and checks against them. A parameter no stock entity binds
  falls back to its category.

## Ammunition
- **Valid names.** A weapon's `ammunition-list` may only name entries of `ammunitionParams.mtl` (UG 73.2.1-2).
  Counts are set in the `.entity`: `num-rounds`, `num-missiles`, `num-<ammo>` (UG 73.1.1).
- **Guided munitions** are entities of their own, with movement and warhead systems (UG 73.3;
  `SM-2_Standard_Missile.entity`). A munition we import gets an `ammunitionParams.additions.mtl` entry
  (`generate.write_munition_tables`).
- **Weapons need sensors.** In practice a weapon needs a sensor to find targets (UG 66.4, 73.1).

## What the importer does
1. **Clone.** `generate.build_entity` starts from the donor's `.entity` (UG 65.3.1), with all its systems.
2. **Edit.** `systems.apply_systems` applies the manifest's optional `systems` list. Without the list nothing
   changes.
3. **Draft.** `draft_manifest` writes that list from the model and the donor (`systems.draft_systems`). In the
   editor, `from_package` drafts it at export for the saved donor, less `mak.json` `systems_off` (the changes
   unticked in Review > MAK). Every entry carries a `reason`:
   - **Sensors.**
     - Radar sensors (sysdef name contains `radar`) go to the highest radar part (5376 / 6976 families), else
       `socket_radar`.
     - `visual-` and `infrared-` sensors go to `socket_bridge_eye`, else `station__helm`.
     - Others (sonar, gimbaled) keep the donor's position, with a note.
   - **Donor weapons.**
     - Kept when the model has the donor's codes.
     - Otherwise rebound to a free weapon mount of the model. The parameters are assigned top-down (rot-turret,
       turret, elevating-turret, gun, launcher), each to a different part where stock entities bind that
       parameter.
     - Otherwise removed, with the reason.
     - A fixed-code weapon is removed unless the model has all its codes.
   - **The model's own mounts.**
     - For a mount no weapon uses, the draft looks for a stock weapon whose name the node names (`phalanx` ->
       `phalanx-20mm-gun`, `ram` -> `ram-missile-launcher`). The weapon must suit the platform's repository.
     - **Stand-ins:** when no stock weapon matches, a reported stand-in is used. Sea Sparrow, ESSM and RIM-7 ->
       `ram-missile-launcher` (Q22).
     - **Sim-only parts:** when the mount lacks parts the weapon drives, they are added to the `.entity` only
       (`add_parts`, UG 66.3.1). Each takes the next free code of the family stock entities bind that parameter
       to most, and the limits of the sysdef component that drives it. The weapon aims and fires in the
       simulation, but the model cannot show what it has no joint for. This is recorded as a `part-sim-only`
       warning (Q20, closed 2026-11-15: models built with the editor's articulation roles have the joints, so this
       is the fallback for older models).
     - **Fixed sim-only parts:** a component whose stock entry has an empty `art-part-param-list` has no motion,
       e.g. RAM's `primary-launcher`, the launch point under the elevating mount. It is added with motion
       `fixed`: an empty list and no limits, as Arleigh Burke's 4992. It is reported as `part-sim-only-fixed`
       (info), since nothing can show.
     - Ammunition counts are the median of stock entities that use the weapon.
   - **Damage and movement.** One of each (UG 66.4.2-3). If the donor has none, the most common one for the
     **class** is added: stock entities of the same kind, military or civilian (`systems.class_of`). Lincoln's
     Invincible donor has no damage system (the SOE showed Undamageable), so it gets `surface-default`, which 70%
     of military ships have.
   - **Class baseline.** Sensors and additional systems that at least 50% of the class has, and the donor lacks,
     are added with the stock median parameters. Sensors are placed by the rules above.
     - Military ships: active radar 92%, TDL surface message generator 86%, spot report generator 86% and
       receiver 84%.
     - Civilian ships: visual sensor.
   - **Systems that drive a part the model has.** Added when the platform allows them: periscope (2048-2144),
     folding wing (7584), cargo door (7648), car doors, refuelling booms. Landing gear and engine nacelles belong
     to movement systems, so they get a note instead.
   - **Sensor for weapons.** If weapons remain and the entity has no sensor, the draft adds the sensor most stock
     entities of the kind use (UG 66.4).
   - Everything else stays the donor's.

## Parts and their components (UG 66.3)
An entity's parts come from its systems' components (each sysdef's art-part lists) and from its visual model.
The catalog records every art-part entry with its component (`Sysdef.art_entries`), e.g.:
- phalanx: `turret` -> `primary-turret`, `main-gun-joy` -> `primary-gun`;
- RAM: `rotating-mount-point` -> `azimuth-mount`, `elevating-mount-point` -> `elevating-mount`, `launcher` ->
  `primary-launcher`;
- periscope: `periscope` -> 2048.

After the systems are applied, `systems.associate_parts` names every part a system drives `<entry>-<code>`,
as stock entities do (1709 descriptors, e.g. 2S1 `azimuth-mount-4096`). A repeated name gets `-<code>` again.
It reports `part-component` (part -> system / component) and `part-visual-only` (parts no component drives:
radars, rudders and deck elevators have no stock system). The SOE's "Associated Component" column shows each
of these parts as `<system>:<component>`, e.g. `main-30mm-gun:turret` (confirmed on the Lincoln reimport, Q21).
4. **Check.** `validate` checks the written `.entity` (`systems.check_entity_systems`, `check_ammunition`):

| Finding | Level | Check |
|---|---|---|
| `system-sysdef-missing` | error | sysdef is in the simulation model set (UG 71.4) |
| `system-not-allowed` | error | sysdef allows the platform's state repository (UG 71.1.4) |
| `system-name-duplicate`, `system-name-digit` | error | UG 72.1.1, 71.4.1 |
| `system-movement-count`, `system-damage-count` | error | one each (UG 66.4.2-3) |
| `entity-system-artpart` | warning | bound code is one of the entity's parts |
| `system-artpart-category` | warning | bound part's family is one stock entities bind that parameter to |
| `system-position-outside` | warning | `sensor-position` inside the bounding volume (+5 %, +1 m) |
| `system-weapon-no-sensor` | warning | UG 66.4, 73.1 |
| `system-ammunition-unknown` | warning | ammunition in `ammunitionParams.mtl` or our additions (UG 73.2.1) |
| `system-part-unowned` | info | weapon parts that no system drives (they turn in the model only) |

Generate also reports `part-sim-only` (warning, one per sim-only part, Q20), `part-component` and
`part-visual-only` (info).

## Manifest `systems`
```json
"systems": [
  {"system": "sensor", "position_from": "Mast__Radar_1", "reason": "..."},
  {"system": "weapon", "bind": {"turret-art-part-type": "Weapons__Gun_1", "gun-art-part-type": 4448}},
  {"system": "Nimitz-class Carrier-weapon", "remove": true},
  {"system": "weapon-1", "sysdef": "weapons/phalanx-20mm-gun.sysdef",
   "bind": {"turret-art-part-type": "Weapons__Phalanx_1", "gun-art-part-type": "Weapons__Phalanx_1_Gun"},
   "params": {"num-rounds": 4500}}
]
```
- **`system`** is the `systemName`. The donor's systems are edited; a new name with a `sysdef` adds one,
  after the donor's.
- **`bind`** maps a part parameter to a manifest part node, which resolves to its code, or to a code directly.
- **`position_from`** takes a glb node: a part, a `socket_*` or a `station__*`. Its pivot is converted to the
  body frame and written to `position_param` (default `sensor-position`).
- **`params`** sets any other sysdef variable. The XML type comes from the sysdef's `variable-type`.
- **Re-draft after changing `donor_entity`.** System names are the donor's; generate reports
  `system-unknown` for names the donor doesn't have.

## Checked against
- **Harmony** (donor Super_Tanker):
  - passive radar sensor at `Mast__Radar_1` (forward 136, right -4, down -56.1);
  - visual sensor at `socket_bridge_eye` (151, 0, -38.2);
  - medfTool loads the model, and validate is clean.
- **USS Abraham Lincoln** (donor Invincible-class Carrier):
  - active radar at `Radar__Scanner` (-21.4, 28, -51.4), visual sensor at the bridge eye.
  - With the Nimitz donor, its four weapons (Phalanx ×2, vertical launchers ×2) are removed: the model's mounts
    are azimuth-only.
  - With its own donor: `surface-default` damage added; Phalanx ×2, RAM ×2 and Sea Sparrow ×2 (the RAM
    launcher standing in) added on the six mounts, with 10 sim-only parts and 10 `part-sim-only` warnings (Q20).
    The rebuild with articulation roles (editor v13) binds every weapon to its own joints; only the RAM
    template's fixed launch points stay sim-only (`part-sim-only-fixed`).
  - Parts named after their components (`primary-turret-4096`, `azimuth-mount-4160`, `primary-gun-4416`,
    `elevating-mount-4288`, `primary-launcher-4736` ...). Radars, rudder and deck elevators are visual-only.
  - Validate is otherwise clean and medfTool loads the model.
- **Not yet checked in VR-Forces** (Q16, Q19): that the sensors detect from the new positions, and that a
  rebound or added weapon slews the model's parts. To check:
  - open the entity in the SOE and Edit Properties: the red dot marks the system's position (UG 66.4.4);
  - in a scenario, the turret should slew toward a target (UG 73.3.1).
