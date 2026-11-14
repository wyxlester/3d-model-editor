# VR-Forces 5.1.1 import artefacts

Sources: "Adding Content to MAK ONE Applications" (rev MAK-ART-1-240905; shipped with MAK ONE products, not in this repository),
the VR-Forces 5.1.1 Users Guide ("UG x.y", shipped with VR-Forces), and the files in the local install
(`C:\MAK`). "Guide x.y" / "manual x.y" = section of the Adding Content manual.
**Phase 1 task:** expand each section below with every parameter used by similar stock entities.

## How the pieces connect (guide 14.1, figure 70)
```
DIS type on the network (e.g. 1:1:222:28:21:6:0)
  -> entity type map (.metx)          maps type -> element definition name
  -> element definition (.leaf)       visuals per model set: 3D, 3D Colorized (XR), 2D icon
       3D/XR: DtModelSchema  { modeldefinition = <model def name>, modeldefinitionschema = ... }
       2D:    DtMilStdIconSchema { fillglyph = ..., ... }
  -> model definition (.ommx)         schema Entity, parameter filename = $(SHARED_DATA_DIR)/...
  -> model file (.fbx / .flt / .medf)
Simulation side (separate): .entity in <sms>/vrfSim  { objectType, platform .ope, art parts, supports }
```

## Load locations and order
- Visual definitions for stock SMSs: `C:\MAK\SharedData\18\latest\ModelData\Configuration\Definitions\Entity`
  (guide 16.3, 17.2.1). Subfolders by category (Vehicles/Wheeled, ...) hold per-model `.ommx`.
- Files load in **alphabetical order, and the last loaded wins** for duplicate entries (guide 16.1, 17.2).
  Only the first `.hier` loads. New content goes in **new** files (guide 16.2, 17.3).
- A custom SMS can carry its own visuals in `data/simulationModelSets/<sms>/gui/visuals` (guide 14.4.1).
- `.sms` file: Lisp-like `(simulation-model-set (variable-bindings ...))`. The `vrfSim` dir holds `.entity`
  files and `.sog` object groups.

## Model file (guide 14.2, 14.3, 21.1.2, 21.2)
- Formats: OpenFlight or FBX (glTF is not listed). Stock models are encrypted `.medf` (medfTool.exe, guide 19.19).
- FBX carries geometry and textures only. LODs and articulated parts come from **node names**, mapped via
  `C:\MAK\vrforces5.1.1\appData\importConfig\FBX_NodeNameMap.csv` (columns Node, Comment, Type, Description).
- Texture search paths: `appData/importConfig/FBX_TexturePath.csv`. Default: textures beside the model.
- Frame: OSG ENU, X right, Y forward, Z up. MAK converts to DIS NED itself.
- Origin (table 43): land = ground plane at yaw axis; air/space = CG; surface/subsurface = waterline at
  centre of buoyancy.
- Articulated parts (21.4): one DOF/null per part, children beneath it, comment `@dis articulated_part <code>`,
  no static offsets (all-zero DOF = neutral pose). Codes: table 58 (turret 4096, gun 4416, ...).
- Lighting mode: use Lit; Gouraud modes are unsupported (14.3).

## .ommx: model definition (guide 16)
Boost XML: `DtModelDefinitionManager/myDefinitions/item{first=name, second{Name, Schema, Parameters/ParameterMap, Keywords}}`.
Schema by domain, from the stock files: surface ships use **`Watercraft`** (114 of 114 in Vehicles/Surface);
subsurface, ground, air, space and lifeforms use `Entity`. The SOE writes only `filename` (hand import). Stock
Watercraft definitions also carry `buoyancyLODDistance` and `daylightControlledLights`.
Template: `pipeline/templates/model_definition.ommx.tmpl`.

## .leaf: element definition (guide 17)
Boost XML: `EntityLeaf/myElementDefinitions/item{myName, myParentName, myModelSet3D, myModelSetXR, myModelSet2D}`.
Each model set has `myVisualizerDefinitions` items with `myName` (schema), `myAttributes`, `myUuidString`,
`myPreUuidIndex`. Model set ids seen: 3D=0, XR=1, 2D=5. `myParentName` places it in the `.hier` tree.
The main 3D model is a `DtModelSchema` visualizer, always UUID `f5ff6339-d803-4764-aaa3-480a7676d88d`, attributes
`modeldefinition` + `type=DtEntityArticulatedStateModelVisualizer`. Ships also have `DtTrailEffectSchema` and
`DtEntityWakeSchema` (wake geometry: beamwidth, bow and prop-wash offsets...).
Generator: `pipeline/leaf.py`. It clones the donor's element definition (donor objectType -> stock .metx -> .leaf)
and changes `myName` and the 3D/XR `modeldefinition`, as the SOE does. Boost `class_id`/`tracking_level`/`version`
attributes go on the first occurrence of each class only; `normalise_item` re-applies them. It reproduces the
editor's leaf line for line (`mak/tests/test_editor_rules.py`).

## .metx: entity type map (guide 18)
Boost XML: `EntityTypeMap/myMappings/item{first=<7-field DIS type, -1 = wildcard>, second=<element def name>}`.
Template: `pipeline/templates/entity_type_map.metx.tmpl`.

## .entity: simulation object (Users Guide "UG" ch. 64-66, appendix D; stock files)
Plain XML (not Boost): `<simObjects entityVersion="1.0"><simObject objectType=".." matchType=".." platform="@(platforms-dir)/Ground_Vehicle.ope">`.
- `objectType` has 8 fields = VR-Forces object super-type (`1` = Individual, UG D.3.1) + the 7-field DIS type.
- `DtSimArtPartDescriptorList`: see `articulation.md`.
- `boundingVolume offsetType="custom"`: `dimensions length/width/height` + `offset forward/right/down` (box centre).
  Used for sim intersection tests (UG 66.1.1), not the GUI selection box.
- `bodyPosition` `left-support` / `right-support` / `other-support`: ground-contact points (UG 66.1.3).
- `componentSystem systemName=.. platform=@(system-dir)\...sysdef`: damage, movement, sensors, weapons. Weapons
  refer to art-part codes.
- `string` params: `gui-label`, `gui-unique-id`, `short-name`, `gui-categories`, `object-geometry-filename`
  (embarkation geometry: OpenFlight/MEDF/GDB only, UG 66.1.6).
- Creating a model by copying an existing one keeps all its behaviour and systems (UG 65.3.1). That is what the
  generator does with `donor_entity`. Generator: DONE (`generate.build_entity`).

## Where a custom SMS keeps things (UG 64.3-64.4)
- A new SMS stores its own model and element definitions in `data/simulationModelSets/<sms>/gui/visuals`, and loads
  only the files it created. Bring ours in with SOE File > Import Visual Models (UG 65.2.10), which imports all
  leaf files in a directory.
- The recommended way to make a new SMS is to *include* an existing one (UG 64.7, 64.7.1), so upgrades are easier.

## Boost-serialization gotchas
- `class_id`/`tracking_level`/`version` attributes appear only on the **first** occurrence of each class in a
  file. When moving an item to a new file, the first item must carry them.
- `<count>` must equal the number of items.
