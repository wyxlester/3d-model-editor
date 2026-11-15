---
date: 2026-11-15
project: vrf-pipeline (MAK VR-Forces importer for 3d_model_editor)
branch: feat/mak-kinds (here), feat/model-kinds (vrf-pipeline)
session: e8abf2c4 (continues d8a4bbaa)
status: in progress
summary: The importer now gives an imported entity the systems it should have (sensors placed on the model, missing damage and baseline systems, weapons for the model's mounts) and ties each part to the system component that drives it. A reimport of USS Abraham Lincoln in the Simulation Object Editor confirmed the systems, the part associations and the model's colours. The team Slack updates now carry before/after and feature screenshots, the earlier ones included.
written_by: Claude Code (Bryan's session)
---

# MAK importer: systems from the model, and the Lincoln reimport

**Asked:** name exports after the model folder and map every joint that has a DIS part; list the part codes stock
VR-Forces entities use; plan and build how sensors, weapons and other systems are added from the visual model,
grounded in the manuals; include the systems a model should have, with parameters, and set each articulated part's
associated component; document how to verify an import against a hand import; record the reimport results; keep
the docs up to date before every commit; post team updates with before/after and feature images, make that the
default for future updates, and add images to the earlier updates.

## Done
- **Names and part codes** (14 Nov evening, after the last note): the export is named after the model folder
  (vrf-pipeline) or the ship project (editor), not the template ("Custom ship"). Every joint with a counterpart
  in the manual's Table 58 gets a code (rudder, deck elevators, catapults, weapon mounts ...); USS Abraham
  Lincoln maps 11 of 16 joints. Table 58 has one rudder and two deck-elevator codes, so rudders 2-4 and elevators
  3-4 stay static (open question Q18).
- **Stock part codes listed** (`docs/stock-part-codes.md` in vrf-pipeline): the codes used by all 1,389 stock
  individual entities, by group, and the Table 58 codes each group leaves unused. Findings: no stock entity uses
  the rudder code; deck elevators and catapults appear only on one sample ship; code 8192 is a refuelling boom on
  tankers but a gun actuator or launcher on 78 stock ships (its label was fixed).
- **Systems fitted to the model** (vrf-pipeline `9c9e697`, here `d4bb320`):
  - A catalog of the install's 245 system definitions (`pipeline/systems.py`): categories, which platforms
    may use each, the part parameters weapons bind, ammunition, and how stock entities use them.
  - Sensors move to where the model sees from: radar sensors to the highest radar part, visual sensors to the
    bridge eye. Before, they sat at the donor's position, for a ship the waterline (Q16).
  - Donor weapons are kept, rebound to the model's mounts, or removed with the reason.
  - Review > MAK lists each drafted change with its reason and a checkbox; unticked ones are saved as
    `systems_off`.
  - Side fix: 833 stock donors have no bounding-volume offset, which centred the volume on the waterline.
- **Systems the model should have** (vrf-pipeline `6ec7747`, here `060cdee`). After the first reimport showed
  only the donor's systems, "Undamageable" and empty Associated Component cells:
  - Damage and movement when the donor has none: the one most stock entities of the same kind and class
    (military or civilian) use. Lincoln's donor had no damage system; it now gets surface-default, which 70% of
    stock military ships have.
  - The class baseline: sensors and additional systems at least half of that class has.
  - Systems that drive a part the model has (periscope, folding wing, cargo door, refuelling ...).
  - Weapons for mounts named after them: Phalanx ×2, RAM ×2; the RAM launcher stands in for Sea Sparrow ×2
    (the install has no Sea Sparrow, Q22).
  - Each part a system drives is named after that system's component, as stock entities are; that is what fills
    the editor's Associated Component column.
- **Missing elevation joints (decision, recorded for observation, Q20).** Lincoln's mounts only rotate. Every
  stock ship weapon also drives an elevating part, so the importer adds those parts to the simulation only.
  The weapons aim and fire, but the barrels stay level in 3D. Every import warns about each such part
  (`part-sim-only`); the fix is to add the elevation joints in Blender.
- **Reimport of Lincoln in the editor confirmed:**
  - damage shows Surface Entity Default and all six weapons are listed;
  - Associated Component is filled, e.g. `main-30mm-gun:turret` (Q21 resolved);
  - the sim-only parts have no Animate checkbox, as expected;
  - the model's colours render (deck dark grey, hull and island light, markings, antifouling stripe): Q0, the
    "white ship" from the first hand import, is resolved.
- **Docs:** a step-by-step guide to `export_hand_import.ps1` for checking an import against a hand import
  (`mak/TECHNICAL.md` 5.5); the technical document, references and open questions brought up to date; and a
  rule in vrf-pipeline's `CLAUDE.md` to check the docs before every commit.
- **Team updates** posted to the team Slack channel: the systems update (midday), and an evening update covering
  the work of the forked sessions (joint planning, the stale-server guards, texture bake, effect maps, `.medf`
  packaging, the open-question sweep), with three before/after images in its thread.
- **Images on the earlier updates** (evening): nine images added as thread replies to four earlier updates, not
  re-sent to the channel:
  - before/after, from SOE screenshots recovered from vrf-pipeline's git history (`working/` had been cleaned):
    generic warship and all-white Harmony → Lincoln with its own model and colours; support points; systems tab;
    Associated Component; colours. Where no corrected Harmony shot exists, the "after" is Lincoln and the caption
    says so.
  - feature screenshots from the running editor: the Review > MAK export panel and Install dialog, the drafted
    systems list, the articulation roles and the plan's moving parts, and the stale-server banner with the marked
    part codes.
  - The 14 Nov sensor-position note has no image: nothing visual to show.
- **`slack-update` skill** (user-level, in Bryan's `~/.claude/skills/`, not in a repo): drafting a team update now
  includes images by default, before/after for visible changes and single labelled screenshots for features with
  no "before". It searches git history for screenshots, captures sharp shots of the editor with headless Chrome
  (`scripts/cdp_shot.mjs`), labels panels (red before, green after, blue feature), shows the draft before posting,
  and posts images as captioned thread replies.
- **Gates:** vrf-pipeline 159 tests; `mak/tests` 179 (+8 skipped without the Harmony and Lincoln files); server
  321; web typecheck and 135 tests; MAK's own reader loads Lincoln with all 11 parts.
- **Commits** (all pushed, nothing merged): here `2fae944`, `d4bb320`, `0d76019`, `060cdee`, `756baf5`,
  `36d7e4a` on `feat/mak-kinds`; vrf-pipeline `89e4bbc` ... `0bcaf64` on `feat/model-kinds`.

## Decisions
- Weapon mounts without an elevation joint get sim-only elevating parts rather than no weapon (the simulation
  works; the 3D model cannot show the pitch).
- Sea Sparrow mounts use the RAM launcher as a reported stand-in.
- Systems a model should have follow what most stock entities of the same kind and class have (at least half),
  not the real ship's fit.
- Docs are checked and updated before every commit (vrf-pipeline `CLAUDE.md`).
- Team updates show what changed: images wherever they help, captioned honestly (a paler baked texture, a
  different ship or date is said in the caption). Kept as a user-level skill rather than in `CLAUDE.md`, because
  the updates cover both repos.

## Open
- [ ] **Bryan:** run a scenario with the reimported Lincoln. Does a CIWS turn to a target and fire, with the
  barrel level (Q19, Q20)? Do the sensors detect from the mast and bridge (Q16)? Do the sensors' red dots in
  Edit Properties sit there?
- [ ] **Bryan:** add elevation joints to Lincoln's six weapon mounts in Blender, then draft again; the sim-only
  parts go away. *Now done through the editor instead: the plan's articulation roles give each mount its
  elevation joint (session 7b16c568); Lincoln has been rebuilt that way, its export still to redo.*
- [ ] **Bryan:** restart the editor (`start.ps1`). On the evening of 15 Nov its server was running code older than
  the page (the top bar's "Restart the editor" banner), so MAK settings drafted now still use the old rules.
- [ ] Baked textures render paler than stock, and the editor's normal and AO debug views of the baked Lincoln look
  flat: check that the effect maps reach the render (Q25).
- [ ] **Bryan / Lester:** review and merge `feat/mak-export` and `feat/mak-kinds`.
- [ ] **DTS:** a real Sea Sparrow needs its own munition, ammunition entry and launcher (Q22).
- [ ] **DTS:** can several model parts share one part code, so rudders 2-4 turn with rudder 1 (Q18)?
- [ ] Carried from 14 Nov: waterline vs keel supports; Import Visual Models vs copying files; one hand import
  per new kind; crew as DI-Guy characters.

## Links
- `mak/TECHNICAL.md` (6.11 systems, 5.5 hand-import check, 9 limitations), `mak/references/systems.md`,
  `mak/references/open-questions.md` (Q16-Q22)
- `docs/CONTRACT.md` §9b, §10 (`systems_off`, draft `systems`, `add_parts`)
- Previous note: `2026-11-14 vrf-pipeline d8a4bbaa.md`
