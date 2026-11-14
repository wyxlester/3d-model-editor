"""Runs INSIDE Blender (headless). Do not import from the pipeline package.

    blender -b --factory-startup -P pipeline/blender_glb_to_fbx.py -- <in.glb> <out.fbx> <forward_axis> [rename.json]

- Imports the glb and rotates the scene so the model's forward axis becomes +Y (manual 21.2: OSG frame,
  X right / Y forward / Z up).
- Applies rotation and scale to every mesh and empty, so each articulated-part node has an identity
  rotation in its neutral pose (manual 21.4: "no static offsets"). Locations are kept, so each part's
  origin stays at its pivot.
- Renames part nodes to the names VR-Forces knows from FBX_NodeNameMap.csv (manual 21.1.2) using
  rename.json {"old": "new"}.
- Exports FBX with no axis conversion and copies textures next to it.
"""
import json
import math
import sys

import bpy

argv = sys.argv[sys.argv.index("--") + 1:]
src, dst, forward = argv[0], argv[1], argv[2]
renames = json.load(open(argv[3], encoding="utf-8")) if len(argv) > 3 else {}
ROT = {"+Y": 0, "-Y": 180, "+X": 90, "-X": -90}[forward]

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=src)
scene_objs = list(bpy.context.scene.objects)

# --- rename part nodes (before anything else can collide on names)
by_name = {o.name: o for o in scene_objs}
for old, new in renames.items():
    if old not in by_name:
        sys.exit(f"[blender_glb_to_fbx] ERROR rename source '{old}' not found")
    if new in by_name and new != old:
        sys.exit(f"[blender_glb_to_fbx] ERROR rename target '{new}' already used by another node")
for old, new in renames.items():
    by_name[old].name = new
    if by_name[old].name != new:
        sys.exit(f"[blender_glb_to_fbx] ERROR could not rename '{old}' to '{new}' (got '{by_name[old].name}')")

# --- face +Y
roots = [o for o in scene_objs if o.parent is None]
if ROT:
    pivot = bpy.data.objects.new("_pivot", None)
    bpy.context.scene.collection.objects.link(pivot)
    for o in roots:
        o.parent = pivot
    pivot.rotation_euler[2] = math.radians(ROT)
    bpy.context.view_layer.update()
    for o in roots:
        mw = o.matrix_world.copy()
        o.parent = None
        o.matrix_world = mw
    bpy.data.objects.remove(pivot)

# --- bake rotation/scale into geometry for meshes AND empties (part nodes may be either)
bpy.ops.object.select_all(action="DESELECT")
targets = [o for o in bpy.context.scene.objects if o.type in {"MESH", "EMPTY"}]
for o in targets:
    o.select_set(True)
if targets:
    bpy.context.view_layer.objects.active = targets[0]
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)

bpy.ops.export_scene.fbx(
    filepath=dst,
    use_selection=False,
    object_types={"EMPTY", "MESH"},
    axis_forward="Y",          # identity: keep Blender's Z-up, model already faces +Y
    axis_up="Z",
    apply_unit_scale=True,
    apply_scale_options="FBX_SCALE_ALL",
    bake_space_transform=False,
    path_mode="COPY",
    embed_textures=False,
    add_leaf_bones=False,
)
print(f"[blender_glb_to_fbx] wrote {dst}")
