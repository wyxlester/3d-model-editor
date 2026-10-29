"""Crew (human) builder: me3d.crew_spec/1 -> a game-ready rigged human (contract sections 1, 2, 4, 6).

    Blender -b --python blender/build_crew.py -- --spec <spec.json> --out <dir> [--render <png_prefix>]

Must run WITHOUT --factory-startup: the body, rig and clothes come from the MPFB2 user extension
and the CC0 MakeHuman system asset pack (tools/install_mpfb_assets.sh).

Writes model.glb, model.blend, parts.json and build_report.json into <dir>; exits 1 unless ok.
"""

import argparse
import json
import math
import os
import sys
import time
import traceback

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from crew import animations as crew_anims  # noqa: E402
from crew import custom as crew_custom  # noqa: E402
from crew import equipment as crew_equipment  # noqa: E402
from crew import spec as crew_spec  # noqa: E402
from crew import two_hands as crew_two_hands  # noqa: E402

MAX_TRIANGLES = 40000
HEIGHT_TOLERANCE_M = 0.02
MAX_TEXTURE_PX = 1024


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser(prog="build_crew.py")
    p.add_argument("--spec", help="crew spec JSON (defaults to the default male)")
    p.add_argument("--out", required=True)
    p.add_argument("--render", help="also render <prefix>idle.png, <prefix>walk.png and <prefix>back.png previews")
    return p.parse_args(argv)


def reset_scene():
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob, do_unlink=True)
    for coll in (bpy.data.meshes, bpy.data.materials, bpy.data.armatures, bpy.data.actions,
                 bpy.data.cameras, bpy.data.lights, bpy.data.images):
        for item in list(coll):
            if item.users == 0:
                coll.remove(item)
    bpy.context.scene.render.fps = crew_anims.FPS


def evaluated_triangles(obj):
    dg = bpy.context.evaluated_depsgraph_get()
    mesh = obj.evaluated_get(dg).to_mesh()
    try:
        return sum(len(p.vertices) - 2 for p in mesh.polygons)
    finally:
        obj.evaluated_get(dg).to_mesh_clear()


def world_bounds(obj):
    mw = obj.matrix_world
    pts = [mw @ v.co for v in obj.data.vertices]
    return ([min(p[i] for p in pts) for i in range(3)], [max(p[i] for p in pts) for i in range(3)])


def r6(v):
    return [round(float(x), 6) for x in v]


def part_entry(obj, parent=None, bone=None):
    lo, hi = world_bounds(obj)
    mw = obj.matrix_world
    entry = {
        "path": obj.name,
        "label": crew_spec.part_label(obj.name),
        "parent": parent,
        "joint": None,
        "station": None,
        "origin_m": r6(mw.translation),
        "rotation_deg": r6([math.degrees(a) for a in mw.to_euler("XYZ")]),
        "bounds_min": r6(lo),
        "bounds_max": r6(hi),
        "triangles": evaluated_triangles(obj),
    }
    if bone:
        entry["bone"] = bone
    if obj.get("me3d_place") == crew_two_hands.PLACE:
        # Contract section 6: the points the hands close on, in the item's own (mesh) frame.
        entry["place"] = crew_two_hands.PLACE
        entry["grip_m"] = r6(obj[crew_two_hands.GRIP_PROP])
        entry["support_m"] = r6(obj[crew_two_hands.SUPPORT_PROP])
        if obj.get(crew_two_hands.STANCE_PROP):
            # It rides the right hand; in the aim the left hand holds here (it slides back toward the
            # grip when the arm cannot reach the support).
            entry["stance"] = obj[crew_two_hands.STANCE_PROP]
            entry["aim_support_m"] = r6(obj[crew_two_hands.AIM_SUPPORT_PROP])
    return entry


def downscale_textures(max_px, log):
    """MakeHuman ships 2048-4096 px textures (about 20 MB per character). At deck distance 1024 px
    is plenty, and it keeps each crew GLB small enough for the web viewer. Only the in-memory
    copies are scaled; the installed pack is untouched."""
    for img in bpy.data.images:
        if img.source != "FILE" or not img.has_data and not img.filepath:
            continue
        w, h = img.size
        if max(w, h) > max_px:
            s = max_px / float(max(w, h))
            img.scale(max(1, int(w * s)), max(1, int(h * s)))
            log("texture %s scaled %dx%d -> %dx%d" % (img.name, w, h, img.size[0], img.size[1]))


def gltf_export(path):
    props = set(bpy.ops.export_scene.gltf.get_rna_type().properties.keys())
    wanted = {
        "filepath": path,
        "export_format": "GLB",
        "use_selection": False,
        "export_apply": True,             # apply modifiers (armature stays as skinning)
        "export_yup": True,               # contract section 1: Blender's default +Y up conversion
        "export_skins": True,
        "export_morph": False,            # no shape keys
        "export_animations": True,
        "export_animation_mode": "ACTIONS",
        "export_force_sampling": True,
        "export_anim_single_armature": True,
        "export_anim_slide_to_zero": True,
        "export_reset_pose_bones": True,
        "export_def_bones": False,        # keep all 53 game_engine bones, so every crew shares one skeleton
        "export_optimize_animation_size": False,
        "export_cameras": False,
        "export_lights": False,
        "export_extras": False,
        "export_image_format": "AUTO",
    }
    kwargs = {k: v for k, v in wanted.items() if k in props}
    bpy.ops.export_scene.gltf(**kwargs)


def check_feet(armature, action_names, errors, warnings):
    """Feet planted: on idle, brace and squat the ankles must not sink below rest height or slide.
    Returns per-animation stats for the report."""
    scene = bpy.context.scene
    stats = {}
    ad = armature.animation_data
    for name in action_names:
        act = bpy.data.actions[name]
        ad.action = act
        if hasattr(ad, "action_slot") and getattr(act, "slots", None) and len(act.slots):
            ad.action_slot = act.slots[0]
        f0, f1 = (int(x) for x in act.frame_range)
        zs, xs = {"l": [], "r": []}, {"l": [], "r": []}
        for f in range(f0, f1 + 1):
            scene.frame_set(f)
            for s in ("l", "r"):
                head = armature.matrix_world @ armature.pose.bones["foot_" + s].head
                zs[s].append(head.z)
                xs[s].append(head.x)
        rest_z = {s: (armature.matrix_world @ armature.data.bones["foot_" + s].head_local).z for s in ("l", "r")}
        st = {}
        for s in ("l", "r"):
            st["foot_%s_min_z" % s] = round(min(zs[s]), 4)
            st["foot_%s_z_range" % s] = round(max(zs[s]) - min(zs[s]), 4)
            st["foot_%s_x_range" % s] = round(max(xs[s]) - min(xs[s]), 4)
            if min(zs[s]) < rest_z[s] - 0.005:
                errors.append("animation %s: foot_%s sinks %.3f m below its rest height" % (name, s, rest_z[s] - min(zs[s])))
            if name in crew_anims.PLANTED and (st["foot_%s_z_range" % s] > 0.01 or st["foot_%s_x_range" % s] > 0.01):
                errors.append("animation %s: foot_%s is not planted (moves %.3f m)" % (name, s, max(st["foot_%s_z_range" % s], st["foot_%s_x_range" % s])))
        stats[name] = st
    ad.action = None
    scene.frame_set(1)
    return stats


def run_custom_items(items, armature, errors, warnings, log):
    """Run the AI's custom_items in order with bpy, mathutils and the crew helpers in scope (same
    rules as ship custom ops: a failing item is a build error naming its id and line, and the
    remaining items still run). Runs before animations bake, so anything skinned or bone-parented
    here is animated too. The caller wraps this in crew_custom._setup/_finish.

    Returns ({name: object} of the Clothes__/Equipment__ meshes made, [(object, place)] of the
    equipment the builder still has to attach). Equipment items (module "equipment") are modelled
    at the origin and placed by the builder; an older item whose code parented its own object is
    left where it put it unless the spec gives a place."""
    import math as _math
    import mathutils
    from mathutils import Matrix, Vector
    made, to_place = {}, []
    for item in items:
        iid = item["id"]
        module = item.get("module", "shirt")
        before = set(bpy.data.objects.keys())
        ns = {"bpy": bpy, "mathutils": mathutils, "crew": crew_custom, "math": _math,
              "Vector": Vector, "Matrix": Matrix, "__name__": "custom_item_%s" % iid}
        try:
            exec(compile(item["code"], "<custom_item %s>" % iid, "exec"), ns)
        except Exception as e:  # noqa: BLE001 - reported, the build is then not ok
            tb = traceback.extract_tb(e.__traceback__)
            line = next((f.lineno for f in reversed(tb) if f.filename.startswith("<custom_item")), "?")
            errors.append("custom item %s (%s) failed at line %s: %s: %s"
                          % (iid, item.get("description", ""), line, type(e).__name__, e))
            continue
        for name in sorted(set(bpy.data.objects.keys()) - before):
            ob = bpy.data.objects[name]
            problem = None
            bone_parented = ob.parent is armature and ob.parent_type == "BONE"
            if ob.type != "MESH":
                problem = "is not a mesh"
            elif not crew_custom._NAME.match(name) or not name.startswith(("Clothes__", "Equipment__")):
                problem = "is not a legal crew part name (Clothes__<Name> or Equipment__<Name>, [A-Za-z0-9_])"
            elif name.startswith("Equipment__") and module != "equipment" and not bone_parented:
                problem = "is not parented to a bone (use crew.hold or crew.attach), so it would not move"
            elif name.startswith("Clothes__") and not any(
                    m.type == "ARMATURE" and m.object is armature for m in ob.modifiers):
                problem = "is not skinned to the crew armature (use crew.garment)"
            if problem:
                errors.append("custom item %s created %r, which %s" % (iid, name, problem))
                bpy.data.objects.remove(ob, do_unlink=True)
                continue
            made[name] = ob
            if name.startswith("Equipment__") and module == "equipment":
                if item.get("place"):
                    to_place.append((ob, item["place"]))
                elif not bone_parented:
                    to_place.append((ob, crew_spec.CUSTOM_DEFAULT_PLACE))
            log("custom item %s (%s) made %s (%d triangles)" % (
                iid, module, name, sum(len(p.vertices) - 2 for p in ob.data.polygons)))
    return made, to_place


def build_modules(spec, armature, objects, equipment, warnings, log, holds=None):
    """Shirt, pants, accessories, custom items and placement, in the order that keeps modules
    independent (see blender/crew/modules.py). Mutates objects/equipment; returns errors. An item
    placed both_hands appends its two_hands.Hold to ``holds`` for the animation bake."""
    from crew import modules as crew_modules
    errors = []
    c = spec["clothes"]
    surface = crew_modules.snapshot(objects)
    equipment_objs, to_place = crew_equipment.add_equipment(spec["equipment"], armature,
                                                            list(objects.values()), log)
    equipment.update(equipment_objs)
    for tag in ("life_vest", "helmet"):
        if "Equipment__" + tag in equipment:
            surface.add(equipment["Equipment__" + tag], tag)
    equipment.update(crew_modules.build_accessories(spec["accessories"], armature, surface, log))

    items = spec["custom_items"]
    described = {i["module"] for i in items}
    if c.get("suit"):
        dropped = [i["id"] for i in items if i["module"] in ("shirt", "pants")]
        if dropped:
            warnings.append("clothes.suit %s is set, so it is drawn instead of the shirt/pants custom "
                            "items %s" % (c["suit"], ", ".join(dropped)))
            items = [i for i in items if i["module"] not in ("shirt", "pants")]
    crew_custom._setup(armature, objects.get("Body__Skin"))
    try:
        if not c.get("suit"):
            for key, build in (("shirt", lambda p: crew_modules.build_shirt(p, armature, log)),
                               ("pants", lambda p: crew_modules.build_pants(p, armature, surface, log))):
                if not c.get(key):
                    continue
                if key in described:
                    warnings.append("clothes.%s is set but a %s custom item describes it; the described "
                                    "one is drawn" % (key, key))
                    continue
                ob = build(c[key])
                objects[ob.name] = ob
        if any(a["name"] == "gloves" for a in spec["accessories"]):
            color = next(a["color"] for a in spec["accessories"] if a["name"] == "gloves")
            ob = crew_modules.build_gloves(color, log)
            objects[ob.name] = ob
        if items:
            made, more = run_custom_items(items, armature, errors, warnings, log)
            to_place += more
            for name, ob in made.items():
                (equipment if name.startswith("Equipment__") else objects)[name] = ob
        stance = next((i["stance"] for i in items if i.get("place") == crew_two_hands.PLACE and i.get("stance")),
                      "ready")
        for ob, where in to_place:
            if where == crew_two_hands.PLACE:
                try:
                    hold = crew_two_hands.place_both_hands(ob, armature, surface, log, stance=stance,
                                                           moves=spec["animations"])
                except ValueError as e:
                    errors.append(str(e))
                    continue
                if holds is not None:
                    holds.append(hold)
                continue
            crew_modules.place(ob, where, armature, surface, log)
            if ob.name == "Equipment__binoculars" and where == "chest":
                crew_equipment.add_binocular_strap(ob, armature)
    finally:
        crew_custom._finish()
    return errors


def main():
    t0 = time.time()
    args = parse_args()
    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)
    errors, warnings, log_lines = [], [], []

    def log(msg):
        log_lines.append(msg)
        print("[build_crew]", msg)

    report = {"ok": False, "errors": errors, "warnings": warnings, "triangles": 0,
              "bounds_min": [0, 0, 0], "bounds_max": [0, 0, 0], "seconds": 0.0}
    try:
        raw = None
        if args.spec:
            with open(args.spec, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        spec, spec_errors, spec_warnings = crew_spec.normalize(raw)
        errors.extend(spec_errors)
        warnings.extend(spec_warnings)
        if spec_errors:
            raise ValueError("invalid crew spec: " + "; ".join(spec_errors))

        from crew import mpfb_human  # imports MPFB; done here so a spec error is reported first
        reset_scene()
        report["mpfb_pack_installed"] = mpfb_human.pack_installed()
        armature, objects, barefoot_h, height_macro = mpfb_human.build_human(spec, warnings, log)
        equipment, holds = {}, []
        errors.extend(build_modules(spec, armature, objects, equipment, warnings, log, holds))

        actions = crew_anims.bake_all(armature, spec["animations"], log, holds[0] if holds else None)
        report["feet"] = check_feet(armature, [a.name for a in actions], errors, warnings)

        # Parts, triangles, bounds - at rest pose.
        parts = [part_entry(o) for o in objects.values()]
        parts += [part_entry(o, parent=None, bone=o.parent_bone) for o in equipment.values()]
        tris = sum(p["triangles"] for p in parts)
        lo = [min(p["bounds_min"][i] for p in parts) for i in range(3)]
        hi = [max(p["bounds_max"][i] for p in parts) for i in range(3)]
        report.update({"triangles": tris, "bounds_min": r6(lo), "bounds_max": r6(hi)})
        report["height_m"] = round(barefoot_h, 4)
        report["overall_height_m"] = round(hi[2] - lo[2], 4)
        report["height_macro"] = round(height_macro, 4)
        report["bones"] = len(armature.data.bones)
        report["bone_names"] = sorted(b.name for b in armature.data.bones)
        report["skeleton"] = "mpfb_game_engine"
        report["animations"] = [a.name for a in actions]
        report["animation_seconds"] = {a.name: crew_anims.ANIMATIONS[a.name]["duration"] for a in actions}
        report["parts"] = len(parts)

        target_h = spec["body"]["height_cm"] / 100.0
        if abs(barefoot_h - target_h) > HEIGHT_TOLERANCE_M:
            errors.append("height %.3f m is more than 2 cm from the requested %.3f m (MPFB's height range "
                          "with these other settings cannot reach it)" % (barefoot_h, target_h))
        if tris > MAX_TRIANGLES:
            errors.append("%d triangles is over the %d budget" % (tris, MAX_TRIANGLES))
        if lo[2] < -0.005:
            errors.append("the model goes %.3f m below the floor at rest" % -lo[2])

        with open(os.path.join(out, "parts.json"), "w", encoding="utf-8") as fh:
            json.dump(parts, fh, indent=1)
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(out, "model.blend"), compress=True)
        downscale_textures(MAX_TEXTURE_PX, log)
        gltf_export(os.path.join(out, "model.glb"))
        if not os.path.exists(os.path.join(out, "model.glb")):
            errors.append("glTF export wrote no file")

        if args.render:
            from crew import render
            still = "idle" if "idle" in bpy.data.actions else None
            render.render_preview(args.render + "idle.png", armature, still, frame=20)
            render.render_preview(args.render + "back.png", armature, still, frame=20, yaw_deg=215.0)
            if "walk" in bpy.data.actions:
                walk = crew_anims.frame_count(crew_anims.ANIMATIONS["walk"])
                render.render_preview(args.render + "walk.png", armature, "walk", frame=1 + walk // 4)
            armature.animation_data.action = None
    except Exception as exc:  # noqa: BLE001 - the report is the interface
        errors.append("%s: %s" % (type(exc).__name__, exc))
        traceback.print_exc()

    report["ok"] = not errors
    report["seconds"] = round(time.time() - t0, 2)
    report["log"] = log_lines
    with open(os.path.join(out, "build_report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1)
    print("[build_crew] ok=%s triangles=%s seconds=%s" % (report["ok"], report["triangles"], report["seconds"]))
    if not report["ok"]:
        for e in errors:
            print("[build_crew] ERROR", e)
        sys.stdout.flush()
        sys.exit(1)


if __name__ == "__main__":
    main()
