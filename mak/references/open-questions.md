# Open questions

Resolve each against the install, or with a real import in the GUI, then update the generator and this file.
"Hand import" = the SOE test of 2026-11-14 (its files are MAK material and are kept out of this public repository;
`mak/tests/test_editor_rules.py` checks the same rules against the installed donor).

## Open
0. **Colours in the GUI.** The hand import showed the ship all white: MAK's renderer ignored OpenFlight
   material-palette colours. Fixed by carrying each material's colour in the face colour (palette materials are
   now white). medfTool's decrypted output shows the colours in the geometry; **the SOE rendering still needs
   checking.**
1. **FBX axis handling (FBX route only).** Blender reads our FBX back correctly; does VR-Forces honour the FBX
   header axis system?
3b. **Copying files straight into an SMS.** Is copying `output/<name>/sms/*` into `data/simulationModelSets/<sms>/`
   enough for the SOE to load them, or is File > Import Visual Models on `output/<name>/visual-import` required
   (UG 64.4.1)? Both layouts are generated.
7. **medf.** Is encrypting to `.medf` with medfTool worth it for our own content?
8. **SMS checksum files** (`smsChecksum5.1.1.cksm`). Do new `.entity` files in a custom SMS need them regenerated?
10. **FBX node-name matching (FBX route only).** Is the FBX_NodeNameMap.csv match exact and case-sensitive?
13. **Attach-point semantics.** Child attach-points are written relative to the parent part (stock 2S1 pattern).
    The hand import has no explicit parts (the SOE takes them from the model, UG 66.3), so this is unconfirmed.
14. **Support points and bounding volume.** The hand import kept the donor's values (not recalculated). Compare ours
    with SOE "Calculate Support Points and Bounding Volume from Displayed Model" (UG 66.1.1, 66.1.3).
15. **Donor weapon systems.** Cloned systems keep the donor's part codes (e.g. 2S1 needs 4128).
16. **Sensor position.** Donor sensors sit at the origin (0, 0, 0). The Blender exports give a bridge eye point
    (`socket_bridge_eye`, `station__helm`), which could set `sensor-position`.
17. **DtRwVisualMetaData.** On save, the SOE adds a `visual-meta-data` block (here `Watercraft.meta`, marking
    variance). We don't. Needed?

## Resolved
2. **8-field vs 7-field type.** `.entity` objectType = VR-Forces object super-type + 7-field DIS type (UG D.3.1).
   Entities use `1:`. Confirmed by the hand import (`1:1:3:15:80:1:0:0`).
3. **Custom visuals location.** The SOE writes `<sms>/gui/visuals/Entity/<name>.leaf` + `<name>.metx` and
   `<sms>/gui/visuals/UserData/<name>.ommx`. Generated in that layout under `output/<name>/sms/`. Also
   `output/<name>/visual-import/` holds all three in one folder, for Import Visual Models (UG 65.2.10, which reads
   every .leaf in a directory). The first pipeline attempt showed a generic warship because no .leaf existed,
   so the type fell back to the stock `1:3:-1:...` "Surface" mapping.
4. **Element definition parent.** The donor's (`Large Ship` for Super Tanker). No `.hier` entry needed.
5. **2D icon.** The donor's DtMilStdIconSchema, unchanged.
6. **XR model set.** The SOE kept the donor's XR model. We point XR at our model too, so the XR view shows the
   right ship. This is the one intended difference from the editor's leaf.
9. **SOE equivalence.** The editor's .leaf = donor element definition with `myName` and the 3D modeldefinition
   changed. `leaf.py` reproduces it line for line from the stock donor. .metx and .ommx match exactly
   (schema `Watercraft` for surface ships, filename only). The .entity differs only in the documented fields.
11/12. **Per-model articulation without editing the global map.** The OpenFlight route carries `@dis` comments
   in the model itself. Confirmed in the hand import: both radars articulate.
