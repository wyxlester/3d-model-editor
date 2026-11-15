---
date: 2026-11-15
project: 3d-model-editor (ship generator), for the vrf-pipeline MAK importer
branch: feat/mak-kinds (here), feat/model-kinds (vrf-pipeline)
session: b44a76a0 (started from 7691d21a)
status: done
summary: The generator can now bake PBR textures for a built ship. Each surface gets a procedural finish (non-skid deck, hull plating, paint with grime, streaks and edge wear), and Cycles bakes them into colour, ORM and normal atlases at stock VR-Forces resolution, in model_textured.glb. Lincoln is baked in about 10 minutes.
written_by: Claude Code (Bryan's session)
---

# Generator: UVs, procedural finishes and the PBR texture bake

**Asked:** plan Step 2. Bake standard glTF PBR textures for a built ship (base colour, OpenGL tangent normal, ORM with
R AO, G roughness, B metal) so a MAK import looks like a stock ship. Test it on Lincoln, time it, and render the result.

## Done
- **`blender/texturing/finishes.py`**: one Cycles node graph per finish:
  - the finishes are `deck_nonskid`, `hull_paint`, `antifouling`, `boot_top`, `superstructure_paint`, `bare_metal`,
    `glass`, `rubber`, `teak` and `canvas`, plus a `generic` fallback;
  - coordinates are world metres: hull plates 2 × 8 m, deck panels 3 × 9 m, weld seams;
  - each finish has grime where the AO is dark, rust streaks down walls, edge wear from the Bevel node, and a bump;
  - the spec colour stays the tint.
  - Which finish: the material name first (`deck`, `hull_topsides`, `superstructure_<n>` ...). Lincoln's custom-op
    parts have colour-named materials (`mat_3d3f42`), so for those the part name decides (FlightDeck, Windows, Net,
    Propeller ...). Anything else falls back on its roughness and metallic.
- **`blender/texturing/atlas.py`**:
  - **Atlases:** faces go to Deck, Hull, Superstructure and Details atlases.
  - **Pages:** the page count follows the area and a target texel size: Deck 6 cm and Hull 8 cm at 4096²,
    Superstructure 5 cm and Details 3 cm at 2048².
  - **UVs:** one shared `BakeUV`: a top projection for the deck, smart project for the rest. Large meshes are cut
    on a 48 m world grid so a 330 m hull side packs as plates. Islands are scaled to metres, spread over the pages
    and packed per page.
- **`blender/bake_textures.py`** (CLI, CONTRACT §4b). It bakes colour, then decals selected-to-active, then ORM
  (AO from the AO node), then the tangent normal. It writes `model_textured.glb` (embedded PNGs) and
  `bake_report.json`, then re-imports the glb to check every texture is bound. It never writes `model.glb`.
- **Decals:** thin parts within 5 cm of another part's surface are baked into its colour and dropped (35 on
  Lincoln). Marking-named parts may stand up to 25 cm proud. A decal never has a joint or children, and rides on
  the same moving part as its target. `--keep-decals` keeps them.
- **Lincoln v13** (`data/projects/p_c18a192b/v13/model_textured.glb`):
  - **Pages:** 6 pages (Deck ×2, Hull, Superstructure ×2, Details). Achieved texels: 4.2 cm deck, 4.3 cm hull,
    4.0 cm superstructure, 2.4 cm details.
  - **Checks:** 184 meshes re-imported, all with UVs and all five maps bound. The glb is 118 MB.
  - **Time:** 628 s on a 16-thread CPU. UV layout 1 s, colour 208 s, decals 21 s, ORM 181 s, normal 195 s.
- **Speed fixes found on the way:**
  - Blender bakes selected objects one by one, re-syncing Cycles for each (~27 s a pass for 184 parts). Parts are
    now joined into 5 bake proxies. Touching parts go to different proxies, because the Bevel node only sees its
    own object and otherwise rounded every joint between parts (noise in the normal map).
  - AABB packing filled Lincoln's pages exactly as well as concave packing, in 1 s instead of 80 s.
  - At 4 samples the pages look as clean as at 8, in half the time.
- **Tests:** `tests/blender/test_bake.py`, 44 passing. 43 fast ones cover mapping, page arithmetic, island grouping
  and proxies. The slow one bakes a custom ship with a deck marking at 1/16 size.
- **Docs:** `docs/CONTRACT.md` §4b and §11, `AGENTS.md` layout, `TECHNICAL.md` 6.12, tests, limitations and
  outstanding work (both copies), and the shared technical document (Claude Doc) at rev 34.
- **Commits** (not pushed): vrf-pipeline `ebf90f8` (`docs/TECHNICAL.md`) on `feat/model-kinds`; here the code, the docs and
  this note on `feat/mak-kinds`.

## Decisions
- One material per atlas page (`Deck_0`, `Hull_0` ...), plus `<page>_double_sided` where needed. The ORM image is
  used for both `metallicRoughnessTexture` and `occlusionTexture`.
- AO comes from the Ambient Occlusion node in the ORM emission pass (whole ship), not a separate AO bake.
- Decal colour is composited from a selected-to-active bake plus a coverage mask, so misses never overwrite the
  base colour.
- Defaults: 4 samples, AABB packing, 90° island rotation, 12 px bake margin, 8 px gap between islands.

## Open
- [ ] Wire the bake into the editor (Step 3): a "Bake textures" job, a Flat / Textured viewer toggle with an
      environment map, and MAK export and packaging using `model_textured.glb`.
- [ ] **Bryan:** export Lincoln's baked model to MAK, import it into VR-Forces, and compare it with the stock Nimitz
      from the same angle; check Debug Visuals for normal and AO.
- [ ] Two small inverted patches in Lincoln's deck normal map come from twisted source faces; fix in the hull
      builder if they show in VR-Forces.
- [ ] If 118 MB glbs or 10-minute bakes are a problem: JPEG colour pages, GPU baking, or lower default texel targets.

## Links
- `blender/bake_textures.py`, `blender/texturing/`, `tests/blender/test_bake.py`, `docs/CONTRACT.md` §4b
- Previous note: `2026-11-15 vrf-pipeline 7691d21a.md` (the plan and the importer side)
