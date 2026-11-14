# Open questions

IDs (Q0, Q3b, Q16 ...) are labels, cited elsewhere; they are not list numbers, so gaps are expected.
Resolve each against the install, or with a real import in the GUI, then update the generator and this file.
"Hand import" = the SOE test of 2026-11-14 (its files are MAK material and are kept out of this public repository;
`mak/tests/test_editor_rules.py` checks the same rules against the installed donor).

## Open
- **Q0 Colours in the GUI.** The hand import showed the ship all white: MAK's renderer ignored OpenFlight
  material-palette colours. Fixed by carrying each material's colour in the face colour (palette materials are
  now white). medfTool's decrypted output shows the colours in the geometry; **the SOE rendering still needs
  checking.**
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
- **Q15 Donor weapon systems.** Cloned systems keep the donor's part codes (e.g. 2S1 needs 4128).
- **Q16 Sensor position.** Two different things in the `.entity` sound alike:
  - an **articulated part** (e.g. radar 5376) is visual: a DOF node in the model that VR-Forces turns in the 3D
    view, plus its part entry. Harmony's radars sit correctly on the mast (attach point 136, ±4, -56.15).
  - a **sensor system** (`componentSystem` `sensor` = visual sensor, `sensor-2` = passive radar) is the simulated
    detection. Its `sensor-position` is the point line-of-sight checks start from (UG ch. 51; it feeds
    `sensor-offset` in the sensor's `.sysdef`).

  The two are not linked: the turning radar mesh does not carry the simulated radar. The `.entity` is cloned from
  the donor, whose sensors sit at the origin (0, 0, 0); for a ship that is the waterline amidships, about 56 m
  below Harmony's radar.

  Impact (reasoned, **not checked in VR-Forces**):
  - Detection runs from sea level, so a coastline, island or terrain in between can block it where the mast
    would see over.
  - Geometric horizon: about 30 km from 56 m, near zero from sea level. Whether VR-Forces models the earth's
    curvature in these checks is unconfirmed.
  - With the donor's short ranges (visual 4 km, passive radar 10 km) the difference over open water is probably
    small; it matters most near land.
  - Stock ships mostly do the same: 275 of 352 sensor systems on stock surface ships sit at down = 0, 77 are
    raised. So this is a realism gap, not an error.

  Option, **not applied**: set `sensor-position` from the model when it has the information, else keep the
  donor's: the radar sensor at the pivot of a radar part (codes 5376 and up, already in `articulated_parts`);
  the visual sensor at `socket_bridge_eye`, or `station__helm`, when the Blender exports have them. Open points:
  which sensor system matches which part (by its `.sysdef` name), and a report finding that says where each
  sensor was placed. Then check in VR-Forces with the line-of-sight display (UG ch. 51).
- **Q17 DtRwVisualMetaData.** On save, the SOE adds a `visual-meta-data` block (here `Watercraft.meta`, marking
  variance). We don't. Needed?

## Resolved
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
