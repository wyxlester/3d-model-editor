# Open questions

IDs (Q0, Q3b, Q16 ...) are labels, cited elsewhere; they are not list numbers, so gaps are expected.
Resolve each against the install, or with a real import in the GUI, then update the generator and this file.
"Hand import" = the SOE test of 2026-11-14 (its files are MAK material and are kept out of this public repository;
`mak/tests/test_editor_rules.py` checks the same rules against the installed donor).

## Open
- **Q1 FBX axis handling (FBX route only).** Blender reads our FBX back correctly; does VR-Forces honour the FBX
  header axis system?
- **Q3b Copying files straight into an SMS.** Is copying `output/<name>/sms/*` into `data/simulationModelSets/<sms>/`
  enough for the SOE to load them, or is File > Import Visual Models on `output/<name>/visual-import` required
  (UG 64.4.1)? Both layouts are generated.
- **Q7 medf.** Is encrypting to `.medf` with medfTool worth it for our own content?
- **Q8 SMS checksum files** (`smsChecksum5.1.1.cksm`). Do new `.entity` files in a custom SMS need them regenerated?
- **Q10 FBX node-name matching (FBX route only).** Is the FBX_NodeNameMap.csv match exact and case-sensitive?
- **Q13 Attach-point semantics.** Child attach-points are written relative to the parent part (stock 2S1 pattern).
  The hand import has no explicit parts (the SOE takes them from the model, UG 66.3), so this is unconfirmed.
- **Q14 Support points and bounding volume.** The hand import kept the donor's values (not recalculated). Compare ours
  with SOE "Calculate Support Points and Bounding Volume from Displayed Model" (UG 66.1.1, 66.1.3).
- **Q15 Donor weapon systems.** Cloned systems keep the donor's part codes (e.g. 2S1 needs 4128). **Handled in the
  draft** (`references/systems.md`): each donor weapon is kept, rebound to one of the model's mounts, or removed,
  with the reason. Whether VR-Forces drives a rebound weapon on our parts is Q19. Mounts missing parts get
  sim-only parts (Q20).
- **Q16 Sensor position.** Two different things in the `.entity` sound alike:
  - an **articulated part** (e.g. radar 5376) is visual: a DOF node in the model that VR-Forces turns in the 3D
    view, plus its part entry. Harmony's radars sit correctly on the mast (attach point 136, ±4, -56.15).
  - a **sensor system** (`componentSystem` `sensor` = visual sensor, `sensor-2` = passive radar) is the simulated
    detection. Its `sensor-position` feeds `sensor-offset` in the sensor's `.sysdef` (UG 71.4.2, 72.1.2): the
    point detection is computed from.

  The two are not linked in the install: no stock sensor is bound to a radar part. The donor's sensors sit at the
  origin (0, 0, 0); for a ship that is the waterline amidships, about 56 m below Harmony's radar. Stock ships
  mostly do the same (275 of 352 sensor systems on stock surface ships at down = 0, 77 raised).

  **Applied in the draft** (`references/systems.md`): radar sensors at the highest radar part's pivot (else
  `socket_radar`), visual and IR sensors at `socket_bridge_eye` (else `station__helm`); others keep the donor's,
  with a note. Each move is reported (`system-changed`). Harmony: passive radar at (136, -4, -56.1), visual at
  (151, 0, -38.2).

  **Still to check in VR-Forces:** Edit Properties in the SOE should show the red dots on the mast and the bridge
  (UG 66.4.4). In a scenario, compare detection near land with a sea-level copy; the intervisibility display
  starts from the bounding volume's centre, not the sensor (UG 51.1), so judge by detections.
- **Q17 DtRwVisualMetaData.** On save, the SOE adds a `visual-meta-data` block (here `Watercraft.meta`, marking
  variance). We don't. Needed?
- **Q18 More parts than Table 58 codes.** Table 58 has one rudder code (1025) and two deck elevator codes
  (7296, 7328). USS Abraham Lincoln has 4 rudders and 4 deck elevators, so the draft maps rudder 1 and elevators
  1-2 and leaves the rest static (`unmapped_joints` says why). Open: can several DOF nodes carry the same
  `@dis articulated_part` code, so one `.entity` part turns all four rudders together? Check in VR-Forces by
  giving rudders 2-4 code 1025 in the model only (one `.entity` descriptor) and moving the rudder.
- **Q19 Rebound and added weapons.** The draft rebinds a donor weapon's `*-art-part-type` parameters to the
  model's parts, or adds a stock weapon for a mount named after it, binding each parameter to a part of the family
  stock entities use for it (`Catalog.bindings`). Check in VR-Forces that the weapon slews and elevates those
  parts (UG 73.3.1) and fires from the muzzle `end-point`.
- **Q20 Azimuth-only weapon mounts (missing elevation joints), under observation.** A mount modelled with
  one azimuth joint cannot carry a stock ship weapon as it stands: every one also drives an elevating part.
  `phalanx-20mm-gun` needs turret + gun; `ram-missile-launcher` needs rot-turret + elevating-turret + launcher.
  - **What the importer does now** (decided 2026-11-15): it adds the missing parts to the `.entity` only
    ("sim-only" parts; an entity may carry parts the model does not animate, UG 66.3.1). Codes come from the
    family stock entities bind each parameter to most, and limits from the sysdef component (Phalanx gun
    -20 to 85 degrees). The weapon binds fully, trains and elevates, fires, and publishes both angles
    (UG 73.3.1).
  - **The visible effect:** the mount turns in 3D but never pitches. The model has no node for the elevation,
    so a CIWS firing upward looks level, in VR-Forces and in any other viewer of the DIS traffic.
  - **Where it is recorded on every import:**
    - a `part-sim-only` warning per part in `report.json` (and the editor report);
    - a `reason` on each `add_parts` item in the manifest;
    - a note per mount from `draft_manifest`.
  - **The fix:** add the elevating joint under the mount in Blender and draft again. The model's part is then
    used, and no sim-only part is added.
  - **To observe in VR-Forces:** the weapon slews and fires at targets above the horizon; the barrel stays
    level; any errors in the log about articulated parts the model lacks.
  - **Observation log:**
    - 2026-11-15, USS Abraham Lincoln (donor Invincible-class Carrier):
      - 6 mounts, all azimuth-only;
      - sim-only parts added: Phalanx 1-2 guns 4416/4448; RAM 1-2 elevating mounts 4288/4320 and launchers
        4736/4768; Sea Sparrow 1-2 (RAM stand-in) elevating mounts 4352/4384 and launchers 4800/4832.
    - 2026-11-15, the same entity reimported in the SOE (Physical and Systems tabs):
      - all 10 sim-only parts are listed, with the Associated Component of their weapon (Q21);
      - they have **no Animate checkbox**, while the model's own parts (turrets, radars, elevators, rudder) do:
        the SOE knows they have no node in the model;
      - the two Phalanx guns have a Fire (muzzle flash) button;
      - the six weapons are on the Systems tab (Phalanx 20mm CIWS ×2, RAM launcher ×4);
      - still to see in a scenario: the weapon slewing and firing above the horizon with the barrel level, and
        the log.
- **Q22 Sea Sparrow stand-in.** The install has no Sea Sparrow (RIM-7/ESSM) munition or launcher.
  `ram-missile-launcher` (RIM-116, a trainable box launcher like the Mk 29) stands in for mounts named Sea
  Sparrow, ESSM or RIM-7, with "stands in" in the reason. A real Sea Sparrow needs its own munition entity, an
  `ammunitionParams.mtl` entry and a weapon sysdef.

## Resolved
- **Q0 Colours in the GUI.** The first hand import showed the ship all white: MAK's renderer ignored OpenFlight
  material-palette colours. Fixed by carrying each material's colour in the face colour (palette materials are
  white). **Confirmed in the SOE** on the 2026-11-15 reimport of USS Abraham Lincoln (3D Models view):
  - the materials render distinctly: deck dark grey, hull and island light, yellow and white deck markings, and
    the red antifouling stripe at the waterline;
  - every material is brightened by the same factor of about 1.6 under the scene lighting. The deck is 0.22 in the
    model and renders at about 0.37 (pixel 94, 100, 100); the hull and island are 0.55-0.6 and render at about
    0.9 (214-237). So the hull looking near-white is lighting, not a lost colour.
- **Q21 Associated Component.** The SOE's Physical tab has an "Associated Component" column for each part.
  The importer names each part a system drives after that component's art-part entry, `<entry>-<code>` (e.g.
  `primary-turret-4096`, `azimuth-mount-4160`), as 1709 stock descriptors are named.
  - **Confirmed in the SOE** on the 2026-11-15 Lincoln reimport. The column shows `<system>:<component>`:
    - `main-30mm-gun:turret` for turrets 4096 and 4128;
    - `main-30mm-gun:main-gun-joy` for guns 4416 and 4448;
    - `RAM-Missile-Launcher:rotating-mount-point` for 4160-4256;
    - `RAM-Missile-Launcher:elevating-mount-point` for 4288-4384;
    - `RAM-Missile-Launcher:launcher` for 4736-4832.
  - Radars, rudder and deck elevators are blank, as expected: no stock system drives them (`part-visual-only`).
  - Not separated: whether the SOE matches by the name or by the bound code. Both are written, so it doesn't
    matter for the importer.
- **Q2 8-field vs 7-field type.** `.entity` objectType = VR-Forces object super-type + 7-field DIS type (UG D.3.1).
  Entities use `1:`. Confirmed by the hand import (`1:1:3:15:80:1:0:0`).
- **Q3 Custom visuals location.** The SOE writes `<sms>/gui/visuals/Entity/<name>.leaf` + `<name>.metx` and
  `<sms>/gui/visuals/UserData/<name>.ommx`. Generated in that layout under `output/<name>/sms/`. Also
  `output/<name>/visual-import/` holds all three in one folder, for Import Visual Models (UG 65.2.10, which reads
  every .leaf in a directory). The first pipeline attempt showed a generic warship because no .leaf existed,
  so the type fell back to the stock `1:3:-1:...` "Surface" mapping.
- **Q4 Element definition parent.** The donor's (`Large Ship` for Super Tanker). No `.hier` entry needed.
- **Q5 2D icon.** The donor's DtMilStdIconSchema, unchanged.
- **Q6 XR model set.** The SOE kept the donor's XR model. We point XR at our model too, so the XR view shows the
  right ship. This is the one intended difference from the editor's leaf.
- **Q9 SOE equivalence.** The editor's .leaf = donor element definition with `myName` and the 3D modeldefinition
  changed. `leaf.py` reproduces it line for line from the stock donor. .metx and .ommx match exactly
  (schema `Watercraft` for surface ships, filename only). The .entity differs only in the documented fields.
- **Q11/Q12 Per-model articulation without editing the global map.** The OpenFlight route carries `@dis` comments
  in the model itself. Confirmed in the hand import: both radars articulate.
