"""Procedural, low-poly crew equipment, sized to the body it is put on and parented to a bone
(contract section 6: a mesh named ``Equipment__<name>``, bone-parented). Inside Blender.

Sizes come from the character's own mesh (torso cross-sections, head extent, hip width), so a
160 cm crew member gets a smaller vest than a 190 cm one without any per-size tables.
"""

import math

import bmesh
import bpy
from mathutils import Matrix, Vector

TORSO_GROUPS = {"pelvis", "spine_01", "spine_02", "spine_03"}
HEAD_GROUPS = {"head", "neck_01"}

VEST_CLEARANCE = 0.012

PARENT_BONE = {"life_vest": "spine_02", "helmet": "head"}   # fixed spots; the rest go by place


def _dominant_groups(obj):
    names = {g.index: g.name for g in obj.vertex_groups}
    out = []
    for v in obj.data.vertices:
        best, w = None, 0.0
        for g in v.groups:
            if g.weight > w:
                best, w = names.get(g.group), g.weight
        out.append(best)
    return out


def _points_all(obj):
    mw = obj.matrix_world
    return [mw @ v.co for v in obj.data.vertices]


def _points(objs, groups):
    pts = []
    for o in objs:
        dom = _dominant_groups(o)
        mw = o.matrix_world
        for v, g in zip(o.data.vertices, dom):
            if g in groups:
                pts.append(mw @ v.co)
    return pts


def _material(name, rgb, roughness=0.6):
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    mat.diffuse_color = (rgb[0], rgb[1], rgb[2], 1.0)
    mat.use_backface_culling = True  # closed meshes: export single-sided
    return mat


def _ellipse(center, rx, ry, n, z):
    return [Vector((center.x + rx * math.cos(2 * math.pi * i / n), center.y + ry * math.sin(2 * math.pi * i / n), z))
            for i in range(n)]


def _object_from_bmesh(name, bm, origin, materials, smooth=False):
    mesh = bpy.data.meshes.new(name)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(mesh)
    bm.free()
    mesh.transform(Matrix.Translation(-origin))
    for m in materials:
        mesh.materials.append(m)
    for p in mesh.polygons:
        p.use_smooth = smooth
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.matrix_world = Matrix.Translation(origin)
    return obj


def _add_box(bm, center, size, rot=None):
    res = bmesh.ops.create_cube(bm, size=1.0)
    m = Matrix.Diagonal((size[0], size[1], size[2], 1.0))
    if rot is not None:
        m = rot.to_4x4() @ m
    m = Matrix.Translation(center) @ m
    bmesh.ops.transform(bm, matrix=m, verts=res["verts"])
    return res["verts"]


def _add_cylinder(bm, center, radius, depth, segments=10, axis="Z"):
    res = bmesh.ops.create_cone(bm, cap_ends=True, segments=segments, radius1=radius, radius2=radius, depth=depth)
    rot = {"Z": Matrix.Identity(4), "X": Matrix.Rotation(math.pi / 2, 4, "Y"), "Y": Matrix.Rotation(math.pi / 2, 4, "X")}[axis]
    bmesh.ops.transform(bm, matrix=Matrix.Translation(center) @ rot, verts=res["verts"])
    return res["verts"]


def _bridge_rings(bm, rings, closed=True):
    """Quads between consecutive rings of BMVerts."""
    for a, b in zip(rings, rings[1:]):
        n = len(a)
        for i in range(n if closed else n - 1):
            j = (i + 1) % n
            bm.faces.new((a[i], a[j], b[j], b[i]))


def torso_sections(objs, armature, levels):
    """Elliptical cross-sections of the torso at the given heights: [(z, centre, rx, ry)]."""
    pts = _points(objs, TORSO_GROUPS | {"clavicle_l", "clavicle_r"})
    out = []
    for z in levels:
        band = [p for p in pts if abs(p.z - z) < 0.03 and abs(p.y) < 0.24]
        if len(band) < 8:
            band = [p for p in pts if abs(p.z - z) < 0.06]
        xs, ys = [p.x for p in band], [p.y for p in band]
        cx = 0.5 * (min(xs) + max(xs))
        out.append((z, Vector((cx, 0.0, z)), 0.5 * (max(xs) - min(xs)), max(abs(min(ys)), abs(max(ys)))))
    return out


def _segment_box(bm, a, b, width, thickness):
    """A thin box from point a to point b (a strap)."""
    d = b - a
    rot = Vector((0, 0, 1)).rotation_difference(d.normalized()).to_matrix()
    _add_box(bm, (a + b) / 2, (thickness, width, d.length), rot)


def build_life_vest(objs, armature, color):
    bones = armature.data.bones
    z0 = bones["pelvis"].head_local.z + 0.07
    z1 = bones["clavicle_l"].head_local.z - 0.10
    levels = [z0 + (z1 - z0) * i / 5 for i in range(6)]
    secs = torso_sections(objs, armature, levels)
    n = 16
    bm = bmesh.new()
    outer, inner = [], []
    for k, (z, c, rx, ry) in enumerate(secs):
        bulk = 0.045 if 0 < k < len(secs) - 1 else 0.03   # puffier in the middle, like a foam vest
        # VEST_CLEARANCE leaves room for a shirt under the vest. It is the same whatever is worn,
        # so changing the shirt never resizes the vest (contract section 6, "Modules").
        rx, ry = rx + VEST_CLEARANCE, ry + VEST_CLEARANCE
        outer.append([bm.verts.new(p) for p in _ellipse(c, rx + bulk, ry + bulk * 0.8, n, z)])
        inner.append([bm.verts.new(p) for p in _ellipse(c, rx + 0.008, ry + 0.006, n, z)])
    _bridge_rings(bm, outer)
    _bridge_rings(bm, list(reversed(inner)))
    _bridge_rings(bm, [inner[0], outer[0]])
    _bridge_rings(bm, [outer[-1], inner[-1]])
    # Shoulder straps: from the top of the vest at the front, over the shoulder, down the back.
    ztop, c_top, rx_top, ry_top = secs[-1]
    rx_top += VEST_CLEARANCE
    pts = _points(objs, {"spine_03", "clavicle_l", "clavicle_r", "neck_01"})
    for s in (1, -1):
        y = s * min(0.11, ry_top * 0.6)
        near = [p for p in pts if abs(p.y - y) < 0.03 and abs(p.x - c_top.x) < 0.08]
        zs = max((p.z for p in near), default=ztop + 0.1) + 0.012 + VEST_CLEARANCE
        front = Vector((c_top.x + rx_top + 0.02, y, ztop - 0.01))
        back = Vector((c_top.x - rx_top - 0.02, y, ztop - 0.01))
        top_f = Vector((c_top.x + 0.07, y, zs))
        top_b = Vector((c_top.x - 0.07, y, zs))
        for a, b in ((front, top_f), (top_f, top_b), (top_b, back)):
            _segment_box(bm, a, b, 0.07, 0.022)
    origin = Vector((secs[2][1].x, 0.0, secs[2][0]))
    mat = _material("Equipment__life_vest", color, 0.7)
    return _object_from_bmesh("Equipment__life_vest", bm, origin, [mat], smooth=True)


def build_helmet(objs, armature, color):
    pts = _points(objs, HEAD_GROUPS | {"head"})
    hair = [o for o in bpy.data.objects if o.name == "Head__Hair"]
    hair_pts = [o.matrix_world @ v.co for o in hair for v in o.data.vertices]
    head = armature.data.bones["head"]
    brow_z = head.head_local.z + 0.62 * (head.tail_local.z - head.head_local.z)
    top_z = max([p.z for p in pts + hair_pts])
    cap = [p for p in pts + hair_pts if p.z > brow_z - 0.02]
    xs, ys = [p.x for p in cap], [p.y for p in cap]
    cx = 0.5 * (min(xs) + max(xs))
    rx = 0.5 * (max(xs) - min(xs)) + 0.022
    ry = max(abs(min(ys)), abs(max(ys))) + 0.022
    rz = top_z - brow_z + 0.03
    base = Vector((cx - 0.008, 0.0, brow_z))
    bm = bmesh.new()
    n, rings = 16, 5
    shell = []
    for k in range(rings):
        a = (math.pi / 2) * k / rings
        shell.append([bm.verts.new(p) for p in _ellipse(base, rx * math.cos(a), ry * math.cos(a), n, base.z + rz * math.sin(a))])
    apex = bm.verts.new(base + Vector((0, 0, rz)))
    _bridge_rings(bm, list(reversed(shell)))
    for i in range(n):
        bm.faces.new((shell[-1][i], shell[-1][(i + 1) % n], apex))
    # Brim: a flat ring around the rim, longer at the front.
    brim = []
    for i in range(n):
        ang = 2 * math.pi * i / n
        grow = 0.035 + 0.025 * max(math.cos(ang), 0.0)
        brim.append(bm.verts.new(Vector((base.x + (rx + grow) * math.cos(ang), (ry + grow * 0.7) * math.sin(ang), base.z - 0.004))))
    for i in range(n):
        j = (i + 1) % n
        bm.faces.new((shell[0][i], brim[i], brim[j], shell[0][j]))
    mat = _material("Equipment__helmet", color, 0.35)
    return _object_from_bmesh("Equipment__helmet", bm, base, [mat], smooth=True)


def build_binoculars(objs, armature, color):
    """Modelled at the origin (hinge at the origin, barrels upright, eyepieces up); the builder
    places it by its place, and adds the neck strap when that place is the chest."""
    center = Vector((0.0, 0.0, 0.0))
    bm = bmesh.new()
    for s in (1, -1):
        _add_cylinder(bm, center + Vector((0, s * 0.036, 0)), 0.026, 0.12, 10, "Z")
        _add_cylinder(bm, center + Vector((0, s * 0.036, -0.068)), 0.03, 0.025, 10, "Z")  # objective lens bell
    _add_box(bm, center + Vector((0, 0, 0.02)), (0.03, 0.05, 0.035))  # hinge bridge
    mat = _material("Equipment__binoculars", color, 0.45)
    return _object_from_bmesh("Equipment__binoculars", bm, center, [mat], smooth=False)


def add_binocular_strap(obj, armature):
    """Neck strap from the placed binoculars' eyepieces up to the base of the neck on each side."""
    inv = obj.matrix_world.inverted()
    neck = armature.matrix_world @ armature.data.bones["neck_01"].head_local
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    for s in (1, -1):
        a = obj.matrix_world @ Vector((0, s * 0.05, 0.06))
        b = Vector((neck.x + 0.02, s * 0.065, neck.z - 0.01))
        _segment_box(bm, inv @ a, inv @ b, 0.014, 0.006)
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()


def build_radio(objs, armature, color):
    """Modelled at the origin (body centre at the origin, belt clip on its +Y side, upright)."""
    center = Vector((0.0, 0.0, 0.0))
    bm = bmesh.new()
    _add_box(bm, center, (0.035, 0.06, 0.11))
    _add_box(bm, center + Vector((0, 0.02, 0.0)), (0.012, 0.02, 0.08))  # belt clip
    _add_cylinder(bm, center + Vector((-0.008, -0.015, 0.095)), 0.006, 0.08, 6, "Z")  # antenna
    _add_cylinder(bm, center + Vector((-0.008, 0.016, 0.062)), 0.008, 0.015, 6, "Z")  # knob
    mat = _material("Equipment__radio", color, 0.5)
    return _object_from_bmesh("Equipment__radio", bm, center, [mat], smooth=False)


BUILDERS = {"life_vest": build_life_vest, "helmet": build_helmet, "binoculars": build_binoculars,
            "radio": build_radio}


def parent_to_bone(obj, armature, bone_name):
    """Bone parenting that keeps the object exactly where it is (rest pose)."""
    world = obj.matrix_world.copy()
    bone = armature.data.bones[bone_name]
    obj.parent = armature
    obj.parent_type = "BONE"
    obj.parent_bone = bone_name
    obj.matrix_parent_inverse = Matrix.Identity(4)
    bone_tail = armature.matrix_world @ bone.matrix_local @ Matrix.Translation((0.0, bone.length, 0.0))
    obj.matrix_basis = bone_tail.inverted() @ world


def add_equipment(items, armature, body_objs, log=print):
    """Builds every catalog item. Items with one fixed spot (life vest, helmet) are parented now;
    the others are returned unparented in ``to_place`` as (object, place), for the builder to
    attach by place after custom items have run. Returns (objects by name, to_place)."""
    out, to_place = {}, []
    for item in items:
        obj = BUILDERS[item["name"]](body_objs, armature, item["color"])
        out[obj.name] = obj
        tris = sum(len(p.vertices) - 2 for p in obj.data.polygons)
        if item.get("place"):
            to_place.append((obj, item["place"]))
            log("equipment %s built, %d triangles, to go on %s" % (obj.name, tris, item["place"]))
        else:
            parent_to_bone(obj, armature, PARENT_BONE[item["name"]])
            log("equipment %s on bone %s, %d triangles" % (obj.name, PARENT_BONE[item["name"]], tris))
    return out, to_place
