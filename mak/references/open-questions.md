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
  - 2026-11-15: PBRProbe and the PBR Lincoln were deployed by copying the model into SharedData, then using the
    SOE's Import Visual Models and add-entity dialogs. Both loaded and rendered, so that route works. Copying the
    `sms/*` files alone (as the editor's installer does) is still untested.
- **Q10 FBX node-name matching (FBX route only).** Is the FBX_NodeNameMap.csv match exact and case-sensitive?
- **Q13 Attach-point semantics.** Child attach-points are written relative to the parent part (stock 2S1 pattern).
  The hand import has no explicit parts (the SOE takes them from the model, UG 66.3), so this is unconfirmed.
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
  - 2026-11-15: every import so far went through the SOE's Import Visual Models, which may write the block
    itself, so this is not settled. A copy-only deploy (Q3b) answers both.
- **Q18 More parts than Table 58 codes.** Table 58 has one rudder code (1025) and two deck elevator codes
  (7296, 7328). USS Abraham Lincoln has 4 rudders and 4 deck elevators, so the draft maps rudder 1 and elevators
  1-2 and leaves the rest static (`unmapped_joints` says why). Open: can several DOF nodes carry the same
  `@dis articulated_part` code, so one `.entity` part turns all four rudders together? Check in VR-Forces by
  giving rudders 2-4 code 1025 in the model only (one `.entity` descriptor) and moving the rudder.
- **Q19 Rebound and added weapons.** The draft rebinds a donor weapon's `*-art-part-type` parameters to the
  model's parts, or adds a stock weapon for a mount named after it, binding each parameter to a part of the family
  stock entities use for it (`Catalog.bindings`). Check in VR-Forces that the weapon slews and elevates those
  parts (UG 73.3.1) and fires from the muzzle `end-point`.
- **Q22 Sea Sparrow stand-in.** The install has no Sea Sparrow (RIM-7/ESSM) munition or launcher.
  `ram-missile-launcher` (RIM-116, a trainable box launcher like the Mk 29) stands in for mounts named Sea
  Sparrow, ESSM or RIM-7, with "stands in" in the reason. A real Sea Sparrow needs its own munition entity, an
  `ammunitionParams.mtl` entry and a weapon sysdef.

- **Q24 Load time with the `.medf` package.** The first baked Lincoln loaded slowly in the SOE as `.flt` + 18
  PNGs (106 MB). The pipeline now also writes `.medf` + `.meif` beside them (6.13). The manual says MAK loads
  an MEDF version in the requested file's folder instead (ACM 19.19).
  - **To check:** load time with the package against without it, and whether the `.medf` is the one loaded
    (renaming it away should bring the slow load back).
- **Q25 Baked texture tone and detail against stock** (observation log).
  - 2026-11-15, the first reimport of Lincoln baked in the editor (v13, test type `1:3:225:1:1:97:0`).
    Screenshots: vrf-pipeline `working/probe_perspective.png`, `probe_topdown.png`, `probe_normal.png`,
    `probe_oe_pbr_ao.png`.
    - **Renders:** the deck has a non-skid tone with stains, and the markings are painted into the texture (no
      floating strips). Turrets and radars keep their parts.
    - **Too light:** mean grey in the Perspective View, against the stock Nimitz (`stock.png`) from the same
      angle:
      - deck about 158 against 136;
      - hull about 235 against 148 (near white);
      - island about 225 against 172.
      The bake writes sRGB colour from the spec colours, which were chosen when face colours were written as
      linear bytes (Q0). MAK shows texture bytes as they are, under bright lighting.
    - **Deck stains** read as large pale blotches at this distance; the stock deck shows wear and many more
      markings.
    - **Debug Visuals > Normals:** flat colours per face direction at this range (deck green, hull magenta). The
      normal-map detail (4 cm texels) is too fine to judge from here; a close-up is needed.
    - **OE PBR AO:** an even grey with some darkening along edges and deck lines.

## Resolved
- **Sweep of 2026-11-15.** Q7, Q8, Q14 and Q15 are answered; Q20 no longer applies. Each entry says how.
- **Q20 Azimuth-only weapon mounts (missing elevation joints): no longer applicable.** The question was how
  a weapon behaves when its mount has only an azimuth joint, which the importer fills with "sim-only" parts
  (UG 66.3.1). It no longer arises for the models we make:
  - **Why:** the 3d-model-editor now plans each moving part's chain of joints before the build (articulation
    roles, editor CONTRACT §3b, `references/articulation.md` "Upstream"), and Blender builds every joint with
    `me3d.add_chain`.
  - **Evidence:** USS Abraham Lincoln rebuilt that way (editor v13) and run through the pipeline on 2026-11-15:
    - every weapon binds to the model's own joints: Phalanx turret and gun, RAM and Sea Sparrow mount and
      launcher elevation;
    - no moving part is sim-only; the only sim-only parts are the RAM template's fixed launch points, which have
      no motion in stock either (`part-sim-only-fixed`, info).
    - Earlier, a test ship with a Phalanx and a RAM also exported fully bound with no `part-sim-only` warning.
  - **Kept as a fallback:** a model built without the roles (the older Lincoln in `models/`) still gets sim-only
    parts and a `part-sim-only` warning per part (`references/systems.md`), and the in-scenario look of a level
    barrel was never observed. The SOE side was seen once: sim-only parts are listed with their Associated
    Component and no Animate checkbox (2026-11-15).
  - **Observation log (kept):**
    - 2026-11-15, Lincoln (old build, donor Invincible-class Carrier): 6 azimuth-only mounts, 10 sim-only parts
      (Phalanx guns 4416/4448; RAM and Sea Sparrow elevating mounts 4288-4384 and launchers 4736-4832);
    - the same entity in the SOE: all 10 listed with their Associated Component, no Animate checkbox, a Fire
      button on the Phalanx guns, six weapons on the Systems tab;
    - editor roles: the post-build check named the same 10 missing joints on the old Lincoln
      (`server/tests/test_articulation.py`);
    - editor v13 Lincoln (roles): no moving sim-only parts; 4 fixed launch points only.
- **Q14 Support points and bounding volume: answered.** The question was whether our support points and bounding
  volume agree with what the SOE would compute ("Calculate Support Points and Bounding Volume from Displayed
  Model", UG 66.1.1, 66.1.3).
  - **Bounding volume:** computed from the model's bounds (`generate.supports_and_volume`, with a created offset
    where donors have none). It matched the second hand import of Harmony (TECHNICAL 8.2). On the 2026-11-15 PBR
    Lincoln import, the SOE's Physical tab showed our values unchanged: 332.9 x 80.3 m, which is the glb's
    extent.
  - **Supports:** the SOE's calculate button put Harmony's supports on the keel. 144 of 159 stock ships have
    them on the waterline, so the importer computes them from the hull at the waterline (`supports-waterline`).
    The SOE kept them as written: Lincoln PBR left/right (92.07, ±14.06), other (-117.94, 0), identical to our
    `.entity`.
  - Waterline against keel is now a design choice backed by the install, not an unknown. Whether the DTS team
    prefers the keel stays in TECHNICAL's list of questions for them.
- **Q15 Donor weapon systems: answered and handled.** Cloned systems keep the donor's part codes (e.g. 2S1 needs
  4128). The draft keeps each donor weapon, rebinds it to one of the model's mounts, or removes it, with the
  reason (`references/systems.md`).
  - Confirmed in the SOE on the 2026-11-15 Lincoln reimport: the weapons are on the Systems tab, and each bound
    part's Associated Component names its weapon (Q21).
  - Whether a rebound weapon slews our parts in a scenario is a separate check, Q19.
- **Q8 SMS checksum files: not needed.** The `smsChecksum<version>.cksm` files sit only in the stock `EntityLevel`
  SMS, one per release (4.4 to 5.1.1). They are the reference the SMS upgrade tool compares an old SMS against
  when moving to a new VR-Forces version (UG 64.5.2, Table 52); nothing reads them when an SMS loads. New
  `.entity` files in a custom SMS loaded without touching them (PBRProbe and the PBR Lincoln, 2026-11-15).
- **Q7 medf: yes, done by default.** The question was whether encrypting our content to `.medf` with medfTool is
  worth it.
  - The first baked Lincoln (a `.flt` plus 18 PNGs, 106 MB) loaded slowly.
  - The manual recommends preprocessing every model, since OpenFlight loads slowly; MAK loads an MEDF version it
    finds beside the requested file (ACM 19.19).
  - medfTool turns the folder into the format stock models ship in: DXT1 colour, BC5 normal maps, full mip
    chains. Baked Lincoln: 16 s, 74 MB from 111 MB.
  - So the pipeline's package stage now does it by default (`pipeline/medf.py`, TECHNICAL 6.13). The measured
    load-time gain is Q24.
- **Q23 Effect maps on an OpenFlight model.** The importer writes the glb's PBR maps beside each diffuse as
  `<diffuse>_NML.png` and `<diffuse>_MTL_GLS_AO.png` (ACM 12.4, 12.4.1), the names the stock PBR ships use
  (Gerald Ford, Arleigh Burke; their `.medf` references only the diffuse).
  - **Evidence from the install:**
    - `vrvOsg.dll` has a "name based effect texture" path;
    - `normal_map.frag.glsl` reads `.ra` ("RGB and RG (BC5) normal maps");
    - `effects.frag.glsl` reads MTL as metalness and switches to GGX lighting;
    - `medfTool` compresses our `_NML` files to BC5 as it does stock ones.
  - **Confirmed in the SOE** on 2026-11-15 with `PBRProbe` (`scripts/make_pbr_probe.py`, test DIS type
    `1:3:225:1:1:98:0`), by sampling the Left Side View (mean grey):
    - (a) **a plain `.flt` gets the maps by name**: D (metal 255) renders at 84 against A's 118, dark and
      reflective;
    - AO works: E's rows alternate about 112 and 117 in step with its 32 px AO bands, while A stays at about 116.
      The effect is small because AO only scales the ambient light;
    - (b) **channels:** B (RGBA, Y also in alpha) and C (plain RGB) render the same: left half 124, right 117.
      MAK reads Y from green for an uncompressed map; the copy in alpha does no harm and is kept;
    - (c) **direction:** the half tilted toward the top of the image is the brighter one under the high light,
      so glTF's +Y (OpenGL) convention is right, with no flip;
    - (d) **colour space:** F's texture bands and G's face colours of the same bytes render identically, band
      for band (51 ... 203). Textures and face colours share one colour space;
    - H, the full stock-style set (normal, metal 200, gloss 180): left half 103-109 against the right half's
      100-103, darker overall than A from the metal. The same direction as B, damped by the metal;
    - in that light, byte 0 renders at about 51 and byte 255 at about 203 (haze and ambient lift the blacks).
      This is in line with Q0's brightening of mid greys;
    - in the Perspective View, D reflects the sea and sky (bluish), and every letter is upright, so the UVs are
      the right way up.
  - Screenshots: vrf-pipeline `working/probe_left.png` (Left Side View, measured) and
    `working/probe_perspective.png`. Panel means were sampled from the PNGs: lower 60% of each face, each half,
    and each AO band.
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
