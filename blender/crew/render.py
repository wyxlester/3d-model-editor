"""Preview renders of a built crew member (front three-quarter view). Inside Blender."""

import math

import bpy
from mathutils import Vector


def render_preview(path, armature, action_name=None, frame=None, resolution=(720, 960), engine="BLENDER_EEVEE",
                   yaw_deg=35.0):
    """Render one still; yaw_deg is the camera bearing around the character (35 = front three-quarter, 215 = back)."""
    scene = bpy.context.scene
    if action_name:
        armature.animation_data.action = bpy.data.actions[action_name]
        _assign_slot(armature)
        rng = bpy.data.actions[action_name].frame_range
        scene.frame_set(int(frame if frame is not None else rng[0]))
    cam_data = bpy.data.cameras.new("PreviewCam")
    cam = bpy.data.objects.new("PreviewCam", cam_data)
    scene.collection.objects.link(cam)
    target = Vector((0.0, 0.0, 0.95))
    # Front three-quarter: the character faces +X, so stand off on +X, a little to its left (+Y).
    d = 4.2
    yaw = math.radians(yaw_deg)
    cam.location = target + Vector((d * math.cos(yaw), d * math.sin(yaw), 0.35))
    cam.rotation_euler = (target - cam.location).to_track_quat("-Z", "Y").to_euler()
    cam_data.lens = 50
    scene.camera = cam

    sun_data = bpy.data.lights.new("PreviewSun", "SUN")
    sun_data.energy = 3.5
    sun = bpy.data.objects.new("PreviewSun", sun_data)
    sun.rotation_euler = (math.radians(50), math.radians(10), math.radians(60))
    scene.collection.objects.link(sun)
    if scene.world is None:
        scene.world = bpy.data.worlds.new("PreviewWorld")
    scene.world.use_nodes = True
    bg = scene.world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs[0].default_value = (0.55, 0.6, 0.65, 1.0)
        bg.inputs[1].default_value = 0.9

    floor_mesh = bpy.data.meshes.new("PreviewFloor")
    s = 3.0
    floor_mesh.from_pydata([(-s, -s, 0), (s, -s, 0), (s, s, 0), (-s, s, 0)], [], [(0, 1, 2, 3)])
    floor = bpy.data.objects.new("PreviewFloor", floor_mesh)
    scene.collection.objects.link(floor)
    mat = bpy.data.materials.new("PreviewFloor")
    mat.diffuse_color = (0.25, 0.27, 0.3, 1)
    floor_mesh.materials.append(mat)

    try:
        scene.render.engine = engine
    except TypeError:
        scene.render.engine = "BLENDER_WORKBENCH"
    scene.render.resolution_x, scene.render.resolution_y = resolution
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = False
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)

    for ob in (cam, sun, floor):
        bpy.data.objects.remove(ob, do_unlink=True)


def _assign_slot(armature):
    ad = armature.animation_data
    act = ad.action
    if hasattr(ad, "action_slot") and act is not None and getattr(act, "slots", None):
        if ad.action_slot is None and len(act.slots):
            ad.action_slot = act.slots[0]
