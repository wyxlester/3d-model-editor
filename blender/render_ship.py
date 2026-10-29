"""Front three-quarter preview render of a built ship, from its exported model.glb.

    Blender -b --factory-startup --python blender/render_ship.py -- --glb <model.glb> --out <png>
        [--azimuth 35] [--elevation 14] [--size 1280x720] [--sea | --no-sea]

Renders what was exported (the GLB re-imported), not the source scene. The camera stands off the
bow on the port side (+X, +Y), frames the whole model, and a flat sea sits at the waterline so the
underwater body is hidden the way it is in the simulator.
"""

import argparse
import math
import sys

import bpy
from mathutils import Vector


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ap = argparse.ArgumentParser(prog="render_ship.py")
    ap.add_argument("--glb", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--azimuth", type=float, default=35.0, help="degrees from the bow towards port")
    ap.add_argument("--elevation", type=float, default=14.0)
    ap.add_argument("--size", default="1280x720")
    ap.add_argument("--no-sea", action="store_true")
    ap.add_argument("--zoom", type=float, default=1.0, help=">1 moves the camera closer")
    return ap.parse_args(argv)


def main():
    a = parse_args()
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob, do_unlink=True)
    bpy.ops.import_scene.gltf(filepath=a.glb)
    bpy.context.view_layer.update()
    pts = []
    for ob in bpy.data.objects:
        if ob.type == "MESH":
            pts += [ob.matrix_world @ Vector(c) for c in ob.bound_box]
    lo = Vector([min(p[i] for p in pts) for i in range(3)])
    hi = Vector([max(p[i] for p in pts) for i in range(3)])
    lo.z = max(lo.z, 0.0) if not a.no_sea else lo.z
    centre = (lo + hi) / 2
    radius = (hi - lo).length / 2

    scene = bpy.context.scene
    cam_data = bpy.data.cameras.new("PreviewCam")
    cam_data.lens = 50
    cam = bpy.data.objects.new("PreviewCam", cam_data)
    scene.collection.objects.link(cam)
    az, el = math.radians(a.azimuth), math.radians(a.elevation)
    w, h = (int(v) for v in a.size.split("x"))
    fov = 2 * math.atan(36 / 2 / cam_data.lens) * min(1.0, h / w)  # the narrow side's angle
    dist = radius / math.sin(fov / 2) * 0.8 / a.zoom
    cam.location = centre + dist * Vector((math.cos(el) * math.cos(az), math.cos(el) * math.sin(az),
                                           math.sin(el)))
    cam.rotation_euler = (centre - cam.location).to_track_quat("-Z", "Y").to_euler()
    cam_data.clip_end = dist * 4
    scene.camera = cam

    sun_data = bpy.data.lights.new("PreviewSun", "SUN")
    sun_data.energy = 3.2
    sun = bpy.data.objects.new("PreviewSun", sun_data)
    sun.rotation_euler = (math.radians(45), math.radians(8), math.radians(140))
    scene.collection.objects.link(sun)
    scene.world = scene.world or bpy.data.worlds.new("PreviewWorld")
    scene.world.use_nodes = True
    bg = scene.world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs[0].default_value = (0.62, 0.7, 0.78, 1.0)
        bg.inputs[1].default_value = 1.0

    if not a.no_sea:
        s = dist * 3
        me = bpy.data.meshes.new("PreviewSea")
        me.from_pydata([(-s, -s, 0), (s, -s, 0), (s, s, 0), (-s, s, 0)], [], [(0, 1, 2, 3)])
        sea = bpy.data.objects.new("PreviewSea", me)
        scene.collection.objects.link(sea)
        mat = bpy.data.materials.new("PreviewSea")
        mat.use_nodes = True
        bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
        bsdf.inputs["Base Color"].default_value = (0.08, 0.17, 0.24, 1)
        bsdf.inputs["Roughness"].default_value = 0.35
        me.materials.append(mat)

    for engine in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "BLENDER_WORKBENCH"):
        try:
            scene.render.engine = engine
            break
        except TypeError:
            continue
    scene.view_settings.view_transform = "Standard"  # true paint colours; AgX washes them out
    scene.render.resolution_x, scene.render.resolution_y = w, h
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = a.out
    bpy.ops.render.render(write_still=True)
    print(f"[render_ship] wrote {a.out}")


if __name__ == "__main__":
    main()
