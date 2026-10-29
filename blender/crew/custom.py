"""Helpers for crew ``custom_items`` (contract section 6, "Made by description"). Inside Blender.

The AI's code runs with this module in scope as ``crew``. The server turns every public
``def name(sig):`` plus its first docstring line into the AI's tool docs, so each public
function keeps a one-line first docstring line.

Why garments are cut from the body rather than modelled: a crew garment must fit every body
slider setting and deform with every animation on the shared skeleton. Copying the body's own
faces gives both for free - the copy inherits the body's vertex weights, so it skins exactly like
the skin it covers, and it has the body's shape whatever the sliders were.
"""

import math
import re

import bmesh
import bpy
import mathutils  # noqa: F401  (re-exported for the AI's code)
from mathutils import Matrix, Vector

from . import equipment as _eq

REGIONS = {
    "torso": ["spine_01", "spine_02", "spine_03", "clavicle_l", "clavicle_r"],
    "upper_arms": ["upperarm_l", "upperarm_r"],
    "forearms": ["lowerarm_l", "lowerarm_r"],
    "hands": ["hand_l", "hand_r"] + ["%s_0%d_%s" % (f, i, s) for s in "lr"
                                     for f in ("thumb", "index", "middle", "ring", "pinky") for i in (1, 2, 3)],
    "pelvis": ["pelvis"],
    "thighs": ["thigh_l", "thigh_r"],
    "shins": ["calf_l", "calf_r"],
    "feet": ["foot_l", "foot_r", "ball_l", "ball_r"],
    "neck": ["neck_01"],
    "head": ["head"],
}

_NAME = re.compile(r"^[A-Za-z0-9_]+$")
_SRC_ATTR = "crew_src_index"
_STATE = {"armature": None, "body": None, "source": None}


# ---------------------------------------------------------------------------------------------
# Builder hooks (private: not part of the AI's docs)

def _setup(armature, body_obj):
    """Called by build_crew before custom items run: remembers the rig and snapshots the body."""
    _STATE["armature"] = armature
    _STATE["body"] = body_obj
    _STATE["source"] = None
    if body_obj is not None:
        me = body_obj.data
        attr = me.attributes.get(_SRC_ATTR) or me.attributes.new(_SRC_ATTR, "INT", "POINT")
        attr.data.foreach_set("value", list(range(len(me.vertices))))
        src = me.copy()
        src.name = "__crew_body_source"
        _STATE["source"] = src


def _finish():
    """Called by build_crew after custom items: drops the body snapshot and the helper attribute."""
    src = _STATE.get("source")
    if src is not None and src.name in bpy.data.meshes:
        bpy.data.meshes.remove(src)
    _STATE["source"] = None
    for o in bpy.data.objects:
        if o.type == "MESH" and _SRC_ATTR in o.data.attributes:
            o.data.attributes.remove(o.data.attributes[_SRC_ATTR])


def _full_name(name, prefix):
    name = str(name)
    if "__" not in name:
        name = prefix + name
    if not _NAME.match(name) or not name.startswith(("Clothes__", "Equipment__")):
        raise ValueError("%r is not a legal crew part name (Clothes__<Name> or Equipment__<Name>, "
                         "[A-Za-z0-9_] only)" % name)
    return name


def _bones_for(regions):
    if isinstance(regions, str):
        regions = [regions]
    bones = set()
    arm_bones = {b.name for b in armature().data.bones}
    for r in regions:
        if r in REGIONS:
            bones.update(REGIONS[r])
        elif r in arm_bones:
            bones.add(r)
        else:
            raise ValueError("unknown region %r; use one of %s or a bone name" % (r, sorted(REGIONS)))
    return bones


def _rgb(color):
    c = list(color)[:3]
    if len(c) != 3:
        raise ValueError("color must be [r, g, b] in 0..1, got %r" % (color,))
    return [min(max(float(x), 0.0), 1.0) for x in c]


# ---------------------------------------------------------------------------------------------
# Public API (the AI's tool docs are generated from these)

def body():
    """The body skin mesh object (Body__Skin), at rest pose, metres, character facing +X, +Z up."""
    if _STATE["body"] is None:
        raise RuntimeError("no body mesh in this build")
    return _STATE["body"]


def armature():
    """The shared game_engine armature object (named Crew); bones are listed in REGIONS."""
    if _STATE["armature"] is None:
        raise RuntimeError("no armature in this build")
    return _STATE["armature"]


def joint_z(bone):
    """Rest-pose height in metres of a bone's head (e.g. thigh_l = hip, calf_l = knee, foot_l = ankle)."""
    return (armature().matrix_world @ armature().data.bones[bone].head_local).z


def joint(bone):
    """Rest-pose world position (Vector) of a bone's head, e.g. joint("hand_r")."""
    return armature().matrix_world @ armature().data.bones[bone].head_local


def material(name, color, roughness=0.6):
    """A plain Principled material of an [r,g,b] 0..1 colour (linear), reused if the name exists."""
    mat = _eq._material(name, _rgb(color), roughness)
    return mat


def garment(name, regions, color, offset_m=0.008, thickness_m=0.006, z_min=None, z_max=None,
            trim=0.3, hide_body=True, roughness=0.8):
    """Clothes__<name> cut from the body over REGIONS (z_min/z_max = rest-pose hem heights in m); skinned, fits any body."""
    return _garment(name, regions, color, offset_m, thickness_m, z_min, z_max, trim, hide_body, roughness)


def _garment(name, regions, color, offset_m=0.008, thickness_m=0.006, z_min=None, z_max=None,
             trim=0.3, hide_body=True, roughness=0.8, cuts=()):
    """garment() plus ``cuts``: [(point, normal, bones)] - faces mainly on those bones and on the
    +normal side of the plane through point are cut away along a straight hem (sleeve length)."""
    full = _full_name(name, "Clothes__")
    bones = _bones_for(regions)
    arm, body_obj, src = armature(), body(), _STATE["source"]
    if src is None:
        raise RuntimeError("no body snapshot; garment() only works inside custom_items")
    bone_names = {b.name for b in arm.data.bones}
    group_names = [g.name for g in body_obj.vertex_groups]

    me = src.copy()
    me.name = full
    bm = bmesh.new()
    bm.from_mesh(me)
    deform = bm.verts.layers.deform.verify()
    # Hems are cut exactly on the z planes (not along the body's quads), so they are straight.
    for z, keep_above in ((z_min, True), (z_max, False)):
        if z is not None:
            geom = list(bm.verts) + list(bm.edges) + list(bm.faces)
            bmesh.ops.bisect_plane(bm, geom=geom, dist=1e-5, plane_co=(0.0, 0.0, float(z)),
                                   plane_no=(0.0, 0.0, 1.0))
    cuts = [(Vector(co), Vector(no).normalized(), set(bs)) for co, no, bs in cuts]

    def dominant(v):
        best, bw = None, 0.0
        for gi, w in v[deform].items():
            gname = group_names[gi] if gi < len(group_names) else None
            if gname in bone_names and w > bw:
                best, bw = gname, w
        return best

    for co, no, cut_bones in cuts:
        faces = [f for f in bm.faces if any(dominant(v) in cut_bones for v in f.verts)]
        if faces:
            edges = {e for f in faces for e in f.edges}
            verts = {v for f in faces for v in f.verts}
            bmesh.ops.bisect_plane(bm, geom=list(verts) + list(edges) + faces, dist=1e-5,
                                   plane_co=co, plane_no=no)
    bm.verts.index_update()
    bm.faces.index_update()
    bm.verts.ensure_lookup_table()

    # Per vertex: dominant bone, and whether it carries real weight from a bone NOT covered (trim).
    inside, outside_heavy = [], []
    for v in bm.verts:
        best, bw, heavy = None, 0.0, False
        for gi, w in v[deform].items():
            gname = group_names[gi] if gi < len(group_names) else None
            if gname not in bone_names:
                continue
            if w > bw:
                best, bw = gname, w
            if gname not in bones and w > trim:
                heavy = True
        inside.append(best in bones)
        outside_heavy.append(heavy)

    keep = set()
    for f in bm.faces:
        idx = [v.index for v in f.verts]
        if sum(inside[i] for i in idx) * 2 < len(idx) or any(outside_heavy[i] for i in idx):
            continue
        center = f.calc_center_median()
        cz = center.z
        if (z_min is not None and cz < z_min) or (z_max is not None and cz > z_max):
            continue
        if any((center - co).dot(no) > 0 and sum(dominant(v) in cb for v in f.verts) * 2 >= len(idx)
               for co, no, cb in cuts):
            continue
        keep.add(f.index)
    if not keep:
        bm.free()
        bpy.data.meshes.remove(me)
        raise ValueError("garment %s: no body surface in regions %r between z %r and %r"
                         % (full, regions, z_min, z_max))
    bmesh.ops.delete(bm, geom=[f for f in bm.faces if f.index not in keep], context="FACES_ONLY")
    bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context="VERTS")

    # Drop specks: islands much smaller than the largest piece (stray faces at region borders).
    islands, seen = [], set()
    for f in bm.faces:
        if f in seen:
            continue
        stack, isl = [f], []
        seen.add(f)
        while stack:
            cur = stack.pop()
            isl.append(cur)
            for e in cur.edges:
                for g in e.link_faces:
                    if g not in seen:
                        seen.add(g)
                        stack.append(g)
        islands.append(isl)
    biggest = max(len(i) for i in islands)
    small = [f for isl in islands if len(isl) < max(12, biggest * 0.02) for f in isl]
    if small:
        bmesh.ops.delete(bm, geom=small, context="FACES")
        bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context="VERTS")

    # The bone-weight rule cuts openings (neck, armholes) along the body's quads, which reads as a
    # saw-tooth edge. Relax those edges along themselves; the straight z hems are left alone.
    def on_hem(v):
        return (any(z is not None and abs(v.co.z - z) < 1e-4 for z in (z_min, z_max))
                or any(abs((v.co - co).dot(no)) < 1e-4 for co, no, _ in cuts))

    for _ in range(4):
        moves = {}
        for v in bm.verts:
            if not v.is_boundary or on_hem(v):
                continue
            nb = [e.other_vert(v) for e in v.link_edges if e.is_boundary]
            if len(nb) == 2:
                moves[v] = 0.5 * v.co + 0.25 * (nb[0].co + nb[1].co)
        for v, co in moves.items():
            v.co = co

    # Which original body vertices the garment covers (by the body's original vertex index).
    src_layer = bm.verts.layers.int.get(_SRC_ATTR)
    covered, border = set(), set()
    if src_layer is not None:
        for v in bm.verts:
            covered.add(v[src_layer])
            if v.is_boundary:
                border.add(v[src_layer])

    # Push out along the body normal, then turn the hem in to give the edge some thickness.
    bm.normal_update()
    normals = {v: v.normal.copy() for v in bm.verts}
    for v in bm.verts:
        v.co += normals[v] * offset_m
    if thickness_m > 0:
        edges = [e for e in bm.edges if e.is_boundary]
        if edges:
            res = bmesh.ops.extrude_edge_only(bm, edges=edges)
            new_verts = [g for g in res["geom"] if isinstance(g, bmesh.types.BMVert)]
            for v in new_verts:
                old = next((e.other_vert(v) for e in v.link_edges if e.other_vert(v) in normals), None)
                n = normals[old] if old is not None else Vector((0, 0, 0))
                v.co -= n * thickness_m
    bm.to_mesh(me)
    bm.free()
    if _SRC_ATTR in me.attributes:
        me.attributes.remove(me.attributes[_SRC_ATTR])
    me.materials.clear()
    mat = material(full, color, roughness)
    mat.use_backface_culling = False   # open garment: the inside shows at the openings
    me.materials.append(mat)
    for p in me.polygons:
        p.use_smooth = True

    obj = bpy.data.objects.new(full, me)
    bpy.context.scene.collection.objects.link(obj)
    for gname in group_names:
        obj.vertex_groups.new(name=gname)
    for g in list(obj.vertex_groups):     # only real bones stay (MPFB helper groups go)
        if g.name not in bone_names:
            obj.vertex_groups.remove(g)
    obj.parent = arm
    obj.matrix_parent_inverse = Matrix.Identity(4)
    obj.matrix_world = arm.matrix_world.copy()
    mod = obj.modifiers.new("Armature", "ARMATURE")
    mod.object = arm

    if hide_body and covered:
        _hide_body_under(body_obj, covered, border)
    return obj


def _hide_body_under(body_obj, covered, border):
    """Delete body faces fully inside a garment (two vertex rings in from its edge): they cannot
    be seen, they cost triangles, and they are what pokes through cloth in extreme poses."""
    me = body_obj.data
    attr = me.attributes.get(_SRC_ATTR)
    if attr is None:
        return
    bm = bmesh.new()
    bm.from_mesh(me)
    layer = bm.verts.layers.int.get(_SRC_ATTR)
    safe = {v for v in bm.verts if v[layer] in covered and v[layer] not in border}
    for _ in range(2):
        safe -= {v for v in safe if any(o not in safe for e in v.link_edges for o in [e.other_vert(v)])}
    doomed = [f for f in bm.faces if all(v in safe for v in f.verts)]
    if doomed:
        bmesh.ops.delete(bm, geom=doomed, context="FACES")
        bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context="VERTS")
    bm.to_mesh(me)
    bm.free()
    me.update()


def attach(obj, bone):
    """Accessories/hair items only: parent obj to a bone where it is; equipment items are never parented by their code."""
    arm = armature()
    if bone not in arm.data.bones:
        raise ValueError("no bone %r; bones: %s" % (bone, sorted(b.name for b in arm.data.bones)))
    _eq.parent_to_bone(obj, arm, bone)
    return obj


def grip_point(hand="right"):
    """Rest-pose (A-pose) world position of the palm centre of hand "right" or "left"."""
    s = _side(hand)
    j = joint
    palm = (j("hand_" + s) + j("index_01_" + s) + j("middle_01_" + s) + j("ring_01_" + s) + j("pinky_01_" + s)) / 5
    return palm + 0.4 * (j("thumb_02_" + s) - palm)


def _relaxed_hand(s):
    """(hand bone rest matrix, hand bone matrix and palm centre in the relaxed arms-down stance
    the animations start from). Poses the armature briefly, then puts it back to rest."""
    from . import animations as anims
    from mathutils import Quaternion
    arm = armature()
    pbones = arm.pose.bones
    saved = {pb.name: (pb.rotation_mode, pb.rotation_quaternion.copy(), pb.location.copy()) for pb in pbones}
    mw = arm.matrix_world.copy()
    try:
        for pb in pbones:
            pb.rotation_mode = "QUATERNION"
            pb.rotation_quaternion = Quaternion()
            pb.location = Vector()
        for ch, deg in anims.BASE.items():
            if ch in pbones:
                m = arm.data.bones[ch].matrix_local.to_quaternion()
                pbones[ch].rotation_quaternion = m.inverted() @ Quaternion(anims.euler_to_quat(deg)) @ m
        bpy.context.view_layer.update()
        posed = mw @ pbones["hand_" + s].matrix
        heads = [mw @ pbones[n + s].head for n in ("hand_", "index_01_", "middle_01_", "ring_01_", "pinky_01_")]
        palm = sum(heads, Vector()) / len(heads)
        palm = palm + 0.4 * ((mw @ pbones["thumb_02_" + s].head) - palm)
    finally:
        for pb in pbones:
            mode, q, loc = saved[pb.name]
            pb.rotation_quaternion = q
            pb.location = loc
            pb.rotation_mode = mode
        bpy.context.view_layer.update()
    rest = mw @ arm.data.bones["hand_" + s].matrix_local
    return rest, posed, palm


def hold(obj, hand="right"):
    """Builder's own: equipment is modelled at the origin (grip, +Z up, +X forward), unparented, and placed by its place."""
    s = _side(hand)
    rest, posed, palm = _relaxed_hand(s)
    scale = Matrix.Diagonal(obj.matrix_world.to_scale().to_4d())
    # Where it should be with the arm hanging relaxed: grip in the palm, axes = world axes. Carry
    # that back through the hand bone to the rest pose, where parenting happens.
    want = Matrix.Translation(palm) @ scale
    obj.parent = None
    obj.matrix_world = rest @ posed.inverted() @ want
    return attach(obj, "hand_" + s)


def two_hand_grips(obj, grip, support):
    """Long items (rifle, boat hook): item-local (x,y,z) m of the right-hand grip and left-hand support (else inferred along it)."""
    # Used only when the item's place is both_hands (contract section 6): the right hand closes on
    # `grip` (a rifle's pistol grip / trigger), the left on `support` (its handguard or barrel).
    # Given in the coordinates the item was modelled in, before any placing. Without this call
    # the builder infers both along the item's longest extent: the grip is the origin if it lies
    # 15-50% along from the back end, else a third along; the support two thirds; the front is
    # the end further from the origin.
    from . import two_hands as _two
    obj[_two.GRIP_PROP] = [float(c) for c in grip]
    obj[_two.SUPPORT_PROP] = [float(c) for c in support]
    return obj


def _side(hand):
    h = str(hand).lower()
    if h in ("right", "r", "hand_r"):
        return "r"
    if h in ("left", "l", "hand_l"):
        return "l"
    raise ValueError("hand must be 'right' or 'left', got %r" % (hand,))


def new_bmesh():
    """An empty bmesh to build an item in with box()/cylinder(), then object_from_bmesh()."""
    return bmesh.new()


def box(bm, center, size, rot=None):
    """Add a box to bm: center (x,y,z) m, size (x,y,z) full lengths m, optional 3x3 rotation Matrix."""
    return _eq._add_box(bm, Vector(center), size, rot)


def cylinder(bm, center, radius, depth, segments=10, axis="Z"):
    """Add a capped cylinder to bm along axis "X", "Y" or "Z" (low-poly: keep segments 6-12)."""
    return _eq._add_cylinder(bm, Vector(center), radius, depth, segments, axis)


def object_from_bmesh(name, bm, color, origin=(0, 0, 0), roughness=0.6, smooth=False):
    """Turn bm into object Equipment__<name> with a material of that colour; origin (m) becomes its pivot."""
    full = _full_name(name, "Equipment__")
    mat = material(full, color, roughness)
    return _eq._object_from_bmesh(full, bm, Vector(origin), [mat], smooth=smooth)
