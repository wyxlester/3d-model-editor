"""Crew modules (contract section 6, "Modules"): shirt and pants presets, accessories, and placing
equipment by ``place``. Inside Blender.

Locality is the point of this file: changing one module must not move or reshape any other. So
every size and position here is measured from a snapshot of the body taken right after the human
is built (``Surface``), before any shirt, pants or custom garment trims the skin under it. Two
specs that differ only in their shirt therefore measure the same body, and their binoculars,
cap and pockets land in exactly the same places.
"""

import math

import bmesh
import bpy
from mathutils import Matrix, Vector

from . import custom as crew_custom
from . import equipment as eq

TORSO = {"pelvis", "spine_01", "spine_02", "spine_03", "clavicle_l", "clavicle_r"}
HEAD = {"head", "neck_01"}
PLACE_BONE = {"chest": "spine_03", "back": "spine_03", "hip": "pelvis", "head": "head",
              "right_hand": "hand_r", "left_hand": "hand_l"}
# Clearance between a placed item and the surface it sits against. The surface is the body as
# built, before shirt and pants (so a shirt change cannot move equipment); the torso gap therefore
# clears any preset garment (offset 12 mm) as well.
GAP_M = 0.022
HEAD_GAP_M = 0.006
BACK_SLING_DEG = 35.0  # items on the back are slung diagonally, top towards the left shoulder


# ---------------------------------------------------------------------------------------------
# The body as measured once

class Surface:
    """World-space points of the built human (rest pose), each tagged with its dominant bone
    (or a tag such as "life_vest" for rigid equipment added later)."""

    def __init__(self, objs):
        self.pts = []
        for o in objs:
            for p, g in zip(eq._points_all(o), eq._dominant_groups(o)):
                self.pts.append((p, g))

    def add(self, obj, tag):
        mw = obj.matrix_world
        self.pts += [(mw @ v.co, tag) for v in obj.data.vertices]

    def band(self, tags, z, dz, pred=None):
        return [p for p, g in self.pts if g in tags and abs(p.z - z) < dz and (pred is None or pred(p))]


def snapshot(objects):
    """Surface of the human as built (body, outfit, shoes, hair), before any garment trims it."""
    return Surface([o for o in objects.values() if o.type == "MESH"])


def _bone_head(arm, name):
    return arm.matrix_world @ arm.data.bones[name].head_local


def _brow_z(arm):
    head = arm.data.bones["head"]
    return (arm.matrix_world @ head.head_local).z + 0.62 * (head.tail_local.z - head.head_local.z)


# ---------------------------------------------------------------------------------------------
# Shirt and pants presets

def _hems(arm):
    return {"hip": _bone_head(arm, "thigh_l").z, "knee": _bone_head(arm, "calf_l").z,
            "ankle": _bone_head(arm, "foot_l").z}


def _sleeve_cuts(arm, fraction):
    """Planes across each upper arm at ``fraction`` of its length from the shoulder."""
    cuts = []
    for s in "lr":
        a, b = _bone_head(arm, "upperarm_" + s), _bone_head(arm, "lowerarm_" + s)
        cuts.append((a + fraction * (b - a), b - a, {"upperarm_" + s}))
    return cuts


def build_shirt(preset, arm, log):
    style, color = preset["style"], preset["color"]
    h = _hems(arm)
    name = "Clothes__Shirt"
    hem = h["hip"] + 0.04           # overlaps the pants waist (hip + 0.12), so no skin shows between them
    if style == "singlet":
        # Spine and pelvis only (no clavicles, no arm bones): open armholes and a scooped neck.
        obj = crew_custom._garment(name, ["spine_01", "spine_02", "spine_03", "pelvis"], color,
                                   offset_m=0.012, z_min=hem)
    elif style == "long_sleeve":
        obj = crew_custom._garment(name, ["torso", "pelvis", "upper_arms", "forearms"], color,
                                   offset_m=0.012, z_min=hem)
    else:  # tshirt, polo: sleeves to just above the elbow
        obj = crew_custom._garment(name, ["torso", "pelvis", "upper_arms"], color, offset_m=0.012,
                                   z_min=hem, cuts=_sleeve_cuts(arm, 0.55))
        if style == "polo":
            neck = _bone_head(arm, "neck_01").z
            collar = crew_custom._garment("Clothes__Shirt_collar", ["neck"], color, offset_m=0.014,
                                          thickness_m=0.008, z_max=neck + 0.03, trim=0.6,
                                          hide_body=False)
            _join(obj, [collar])
    log("shirt %s: %d triangles" % (style, _tris(obj)))
    return obj


def build_pants(preset, arm, surface, log):
    style, color = preset["style"], preset["color"]
    h = _hems(arm)
    waist = h["hip"] + 0.12
    if style == "shorts":
        obj = crew_custom._garment("Clothes__Pants", ["pelvis", "spine_01", "thighs"], color,
                                   z_min=0.5 * (h["hip"] + h["knee"]), z_max=waist)
    else:
        obj = crew_custom._garment("Clothes__Pants", ["pelvis", "spine_01", "thighs", "shins"], color,
                                   z_min=h["ankle"] + 0.03, z_max=waist)
        if style == "cargo":
            _join(obj, [_cargo_pocket(arm, surface, s, color, h) for s in "lr"])
    log("pants %s: %d triangles" % (style, _tris(obj)))
    return obj


def _cargo_pocket(arm, surface, s, color, h):
    """A box pocket on the outside of the thigh, half way down, skinned to that thigh."""
    z = 0.5 * (h["hip"] + h["knee"]) + 0.02
    sign = 1.0 if s == "l" else -1.0
    band = surface.band({"thigh_" + s}, z, 0.025)
    outer = max(p.y * sign for p in band)
    cx = sum(p.x for p in band) / len(band)
    bm = bmesh.new()
    eq._add_box(bm, Vector((cx, sign * (outer + 0.008 + 0.012), z)), (0.10, 0.024, 0.13))
    eq._add_box(bm, Vector((cx, sign * (outer + 0.008 + 0.026), z + 0.055)), (0.105, 0.008, 0.035))  # flap
    me = bpy.data.meshes.new("Clothes__Pants_pocket_" + s)
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(me.name, me)
    bpy.context.scene.collection.objects.link(ob)
    vg = ob.vertex_groups.new(name="thigh_" + s)
    vg.add(list(range(len(me.vertices))), 1.0, "REPLACE")
    ob.parent = arm
    ob.matrix_parent_inverse = Matrix.Identity(4)
    ob.matrix_world = arm.matrix_world.copy()
    me.transform(arm.matrix_world.inverted())
    return ob


def _join(target, others):
    """Merge meshes into target (vertex groups merge by name, so skinning carries over)."""
    for ob in bpy.context.selected_objects:
        ob.select_set(False)
    for ob in [target] + others:
        ob.select_set(True)
    bpy.context.view_layer.objects.active = target
    with bpy.context.temp_override(active_object=target, object=target,
                                   selected_objects=[target] + others,
                                   selected_editable_objects=[target] + others):
        bpy.ops.object.join()
    target.select_set(False)
    # The joined parts bring their own materials; the garment has one colour, so keep one slot.
    me = target.data
    while len(me.materials) > 1:
        me.materials.pop(index=len(me.materials) - 1)
    for p in me.polygons:
        p.material_index = 0


def _tris(obj):
    return sum(len(p.vertices) - 2 for p in obj.data.polygons)


# ---------------------------------------------------------------------------------------------
# Accessories (procedural, on the head bone; gloves are a garment)

def _head_frame(arm, surface):
    """Measurements of the head (and hair, helmet) at rest: brow height, front, half-width, top."""
    brow = _brow_z(arm)
    tags = HEAD | {"helmet"}
    pts = [p for p, g in surface.pts if g in tags]
    top = max(p.z for p in pts)
    above = [p for p in pts if p.z > brow - 0.02]
    cx = 0.5 * (min(p.x for p in above) + max(p.x for p in above))
    return {"brow": brow, "top": top, "cx": cx, "pts": pts}


def build_cap(arm, surface, color):
    f = _head_frame(arm, surface)
    base_z = f["brow"] + 0.012
    cap = [p for p in f["pts"] if p.z > base_z - 0.01]
    xs, ys = [p.x for p in cap], [p.y for p in cap]
    cx = 0.5 * (min(xs) + max(xs))
    rx = 0.5 * (max(xs) - min(xs)) + 0.008
    ry = max(abs(min(ys)), abs(max(ys))) + 0.008
    rz = f["top"] - base_z + 0.01
    base = Vector((cx, 0.0, base_z))
    bm = bmesh.new()
    n, rings = 16, 5
    shell = []
    for k in range(rings):
        a = (math.pi / 2) * k / rings
        shell.append([bm.verts.new(p) for p in eq._ellipse(base, rx * math.cos(a), ry * math.cos(a), n,
                                                            base.z + rz * math.sin(a))])
    apex = bm.verts.new(base + Vector((0, 0, rz)))
    eq._bridge_rings(bm, list(reversed(shell)))
    for i in range(n):
        bm.faces.new((shell[-1][i], shell[-1][(i + 1) % n], apex))
    # Peak: the front third of the rim pushed forward and slightly down.
    front = [i for i in range(n) if math.cos(2 * math.pi * i / n) > 0.3]
    peak = {}
    for i in front:
        ang = 2 * math.pi * i / n
        reach = 0.075 * math.cos(ang)
        peak[i] = bm.verts.new(Vector((cx + (rx + reach) * math.cos(ang), ry * 1.02 * math.sin(ang),
                                       base_z - 0.012 * math.cos(ang))))
    order = sorted(front, key=lambda i: math.sin(2 * math.pi * i / n))
    for a, b in zip(order, order[1:]):
        bm.faces.new((shell[0][a], peak[a], peak[b], shell[0][b]))
    mat = eq._material("Equipment__Cap", color, 0.8)
    mat.use_backface_culling = False   # open shell and a single-sided peak
    return eq._object_from_bmesh("Equipment__Cap", bm, base, [mat], smooth=True)


def _eyes(arm, surface):
    eyes = bpy.data.objects.get("Head__Eyes")
    if eyes is not None and len(eyes.data.vertices):
        pts = [eyes.matrix_world @ v.co for v in eyes.data.vertices]
        out = {}
        for sign in (1, -1):
            side = [p for p in pts if p.y * sign > 0]
            out[sign] = Vector((max(p.x for p in side), sum(p.y for p in side) / len(side),
                                sum(p.z for p in side) / len(side)))
        return out
    z = _brow_z(arm) - 0.03
    front = max(p.x for p in surface.band({"head"}, z, 0.015, lambda p: abs(p.y) < 0.04))
    return {s: Vector((front, s * 0.032, z)) for s in (1, -1)}


def build_sunglasses(arm, surface, color):
    eyes = _eyes(arm, surface)
    z = 0.5 * (eyes[1].z + eyes[-1].z)
    head = surface.band({"head"}, z, 0.015)
    front = max(p.x for p in surface.band({"head"}, z, 0.012, lambda p: abs(p.y) < 0.05))
    half_w = max(abs(p.y) for p in head) + 0.006
    lens_x = max(front, eyes[1].x, eyes[-1].x) + 0.012
    ear_x = 0.5 * (min(p.x for p in head) + max(p.x for p in head)) - 0.01
    bm = bmesh.new()
    for s in (1, -1):
        c = Vector((lens_x, eyes[s].y, z))
        eq._add_box(bm, c, (0.006, 0.048, 0.034))
        outer = Vector((lens_x - 0.004, s * (abs(eyes[s].y) + 0.026), z + 0.008))
        eq._add_box(bm, outer, (0.008, 0.008, 0.012))                    # hinge
        eq._segment_box(bm, outer, Vector((ear_x, s * half_w, z + 0.004)), 0.006, 0.004)  # temple arm
    eq._add_box(bm, Vector((lens_x, 0.0, z + 0.01)), (0.005, max(0.01, 2 * abs(eyes[1].y) - 0.045), 0.006))
    mat = eq._material("Equipment__Sunglasses", color, 0.15)
    return eq._object_from_bmesh("Equipment__Sunglasses", bm, Vector((lens_x, 0.0, z)), [mat])


def build_ear_defenders(arm, surface, color):
    eyes = _eyes(arm, surface)
    f = _head_frame(arm, surface)
    z = 0.5 * (eyes[1].z + eyes[-1].z) - 0.02
    head = [p for p in f["pts"] if abs(p.z - z) < 0.02]
    half_w = max(abs(p.y) for p in head)
    ear_x = 0.5 * (min(p.x for p in head) + max(p.x for p in head)) - 0.012
    cup_y = half_w + 0.004 + 0.016
    bm = bmesh.new()
    for s in (1, -1):
        eq._add_cylinder(bm, Vector((ear_x, s * cup_y, z)), 0.037, 0.032, 12, "Y")
        eq._add_cylinder(bm, Vector((ear_x, s * (cup_y + 0.018), z)), 0.028, 0.008, 12, "Y")
    # Headband: an arch over the top of the head (and over a helmet if one is worn).
    top = max(p.z for p in f["pts"] if abs(p.x - ear_x) < 0.03) + 0.012
    ry, rz = cup_y, top - z
    steps = 10
    arch = [Vector((ear_x, ry * math.cos(math.pi * i / steps), z + 0.03 + (rz - 0.03) * math.sin(math.pi * i / steps)))
            for i in range(steps + 1)]
    for a, b in zip(arch, arch[1:]):
        eq._segment_box(bm, a, b, 0.022, 0.006)
    mat = eq._material("Equipment__Ear_defenders", color, 0.5)
    return eq._object_from_bmesh("Equipment__Ear_defenders", bm, Vector((ear_x, 0.0, z)), [mat], smooth=False)


HEAD_ACCESSORIES = {"cap": build_cap, "sunglasses": build_sunglasses, "ear_defenders": build_ear_defenders}


def build_accessories(items, arm, surface, log):
    """Head accessories now (they are measured from the head, so need nothing else); gloves are
    returned for building as a garment once the body snapshot for garments exists."""
    out = {}
    for item in items:
        if item["name"] in HEAD_ACCESSORIES:
            obj = HEAD_ACCESSORIES[item["name"]](arm, surface, item["color"])
            eq.parent_to_bone(obj, arm, "head")
            surface.add(obj, "head_accessory")   # a head-placed item sits clear of the cap
            out[obj.name] = obj
            log("accessory %s on bone head, %d triangles" % (obj.name, _tris(obj)))
    return out


def build_gloves(color, log):
    obj = crew_custom._garment("Clothes__Gloves", ["hands"], color, offset_m=0.004, thickness_m=0.004,
                               trim=0.35, roughness=0.9)
    log("gloves: %d triangles" % _tris(obj))
    return obj


# ---------------------------------------------------------------------------------------------
# Placement

def _local_bounds(obj, rot=None):
    """Bounds of obj's mesh at its own scale (and an optional rotation), relative to its origin."""
    m = Matrix.Diagonal(obj.matrix_world.to_scale().to_4d())
    if rot is not None:
        m = rot @ m
    pts = [m @ v.co for v in obj.data.vertices]
    return (Vector([min(p[i] for p in pts) for i in range(3)]),
            Vector([max(p[i] for p in pts) for i in range(3)]))


def place(obj, where, arm, surface, log=print):
    """Attach obj by place: hands via crew.hold; chest/back/hip/head against the measured surface
    near the bone, so it sits right on any body size. The mesh is taken as modelled: origin =
    grip, +Z up, +X forward."""
    scale = obj.matrix_world.to_scale()
    obj.parent = None
    obj.matrix_world = Matrix.Diagonal(scale.to_4d())
    if where in ("right_hand", "left_hand"):
        crew_custom.hold(obj, where.split("_")[0])
        log("placed %s in %s" % (obj.name, where))
        return obj
    rot = Matrix.Identity(4)
    torso = TORSO | {"life_vest"}

    def footprint(tags, lo, hi, z):
        """Surface points behind the item's own footprint (its bounds once centred at y=0, z), so
        a tall or wide item clears every part of the body it overlaps, not just its middle."""
        half = 0.5 * (hi - lo)
        return [p for p, g in surface.pts
                if g in tags and abs(p.z - z) <= half.z + 0.01 and abs(p.y) <= half.y + 0.01]

    if where in ("chest", "back"):
        z = _bone_head(arm, "spine_03").z + (0.03 if where == "chest" else 0.04)
        if where == "back":
            rot = Matrix.Rotation(math.radians(BACK_SLING_DEG), 4, "X")
        lo, hi = _local_bounds(obj, rot)
        pts = footprint(torso, lo, hi, z)
        if where == "chest":
            x = max(p.x for p in pts) + GAP_M - lo.x
        else:
            x = min(p.x for p in pts) - GAP_M - hi.x
        target = Vector((x, 0.0, z)) - Vector((0, 0.5 * (lo.y + hi.y), 0.5 * (lo.z + hi.z)))
    elif where == "hip":
        z = _bone_head(arm, "pelvis").z - 0.01
        band = surface.band(torso | {"thigh_r"}, z, 0.03)
        cx = 0.5 * (min(p.x for p in band) + max(p.x for p in band))
        lo, hi = _local_bounds(obj)
        half = 0.5 * (hi - lo)
        side = min(p.y for p, g in surface.pts if g in torso | {"thigh_r"} and abs(p.z - z) < half.z + 0.01
                   and abs(p.x - cx) < half.x + 0.01)
        target = Vector((cx, side - GAP_M - hi.y, z)) - Vector((0.5 * (lo.x + hi.x), 0, 0.5 * (lo.z + hi.z)))
    elif where == "head":
        z = _brow_z(arm) + 0.03
        lo, hi = _local_bounds(obj)
        pts = footprint(HEAD | {"helmet", "head_accessory"}, lo, hi, z)
        target = Vector((max(p.x for p in pts) + HEAD_GAP_M - lo.x, 0.0, z)) - Vector(
            (0, 0.5 * (lo.y + hi.y), 0.5 * (lo.z + hi.z)))
    else:
        raise ValueError("place must be one of %s, got %r" % (sorted(PLACE_BONE), where))
    obj.matrix_world = Matrix.Translation(target) @ rot @ Matrix.Diagonal(scale.to_4d())
    eq.parent_to_bone(obj, arm, PLACE_BONE[where])
    log("placed %s on %s (bone %s)" % (obj.name, where, PLACE_BONE[where]))
    return obj
