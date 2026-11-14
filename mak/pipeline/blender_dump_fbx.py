"""Runs INSIDE Blender (headless): import an FBX and dump its node tree as JSON.

    blender -b --factory-startup -P pipeline/blender_dump_fbx.py -- <in.fbx> <out.json>

For every object: type, parent, world pivot, local rotation (deg) and scale, and world bounds of
its own mesh. Used by pipeline/verify_fbx.py to check the converted file, not the source glb.
"""
import json
import math
import sys

import bpy
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
src, out = argv[0], argv[1]

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.fbx(filepath=src)

objs = {}
for o in bpy.context.scene.objects:
    q = o.matrix_local.to_quaternion()
    angle = math.degrees(2 * math.acos(max(-1.0, min(1.0, abs(q.w)))))
    bounds = None
    if o.type == "MESH":
        pts = [o.matrix_world @ Vector(c) for c in o.bound_box]
        bounds = {"min": [min(p[i] for p in pts) for i in range(3)], "max": [max(p[i] for p in pts) for i in range(3)]}
    objs[o.name] = {
        "type": o.type,
        "parent": o.parent.name if o.parent else None,
        "pivot": list(o.matrix_world.translation),
        "local_rotation_deg": angle,
        "local_scale": list(o.matrix_local.to_scale()),
        "bounds": bounds,
    }
json.dump({"objects": objs}, open(out, "w", encoding="utf-8"), indent=1)
print(f"[blender_dump_fbx] wrote {out}")
