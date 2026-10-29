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
_STATE = {"armature": None, "body": None, "source": None, "points": None, "tree": None}


# ---------------------------------------------------------------------------------------------
# Builder hooks (private: not part of the AI's docs)

def _setup(armature, body_obj):
    """Called by build_crew before custom items run: remembers the rig and snapshots the body."""
    _STATE["armature"] = armature
    _STATE["body"] = body_obj
    _STATE["source"] = None
    _STATE["points"] = None
    _STATE["tree"] = None
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
    _STATE["points"] = None
    _STATE["tree"] = None
    for o in bpy.data.objects:
        if o.type == "MESH" and _SRC_ATTR in o.data.attributes:
            o.data.attributes.remove(o.data.attributes[_SRC_ATTR])


def _body_points():
    """(co, dominant bone) of every vertex of the body snapshot: rest pose, before any garment."""
    if _STATE["points"] is None:
        src, body_obj = _STATE["source"], body()
        if src is None:
            raise RuntimeError("no body snapshot; only available while garments are built")
        names = [g.name for g in body_obj.vertex_groups]
        bones = {b.name for b in armature().data.bones}
        pts = []
        for v in src.vertices:
            best, bw = None, 0.0
            for g in v.groups:
                n = names[g.group] if g.group < len(names) else None
                if n in bones and g.weight > bw:
                    best, bw = n, g.weight
            pts.append((v.co.copy(), best))
        _STATE["points"] = pts
    return _STATE["points"]


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
             trim=0.3, hide_body=True, roughness=0.8, cuts=(), holes=(), fit="auto", hem_to=None):
    """garment() plus ``cuts``: [(point, normal, bones)] - faces mainly on those bones and on the
    +normal side of the plane through point are cut away along a straight hem (sleeve length);
    ``holes``: [f(Vector) -> float] - the surface where f > 0 is cut away along the smooth curve
    f = 0 (necklines, armholes); ``fit``: "trousers", "top", None, or "auto" (from the regions);
    ``hem_to``: carry the leg hems straight down to this height (below where the skin ends)."""
    full = _full_name(name, "Clothes__")
    bones = _bones_for(regions)
    if fit == "auto":
        fit = _fit_for(bones)
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
    hole_vals = [_cut_implicit(bm, f, "__hole%d" % i) for i, f in enumerate(holes)]
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
        if any(v[lay] > 1e-7 for lay in hole_vals for v in f.verts):
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
                or any(abs((v.co - co).dot(no)) < 1e-4 for co, no, _ in cuts)
                or any(abs(v[lay]) < 1e-7 for lay in hole_vals))

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

    # Cloth hangs and bridges; skin dips into every hollow. Reshape before the offset.
    fit_info = {}
    if fit:
        fit_info = _fit(bm, fit, _dominant_bones(bm, deform, group_names, bone_names), offset_m) or {}
        # The fit moves edge vertices by different amounts; even the curved (hole) edges out
        # again along themselves. Straight z and plane hems stay exactly where they are.
        if hole_vals:
            for _ in range(6):
                moves = {}
                for v in bm.verts:
                    if not v.is_boundary or not any(abs(v[lay]) < 1e-7 for lay in hole_vals):
                        continue
                    nb = [e.other_vert(v) for e in v.link_edges if e.is_boundary]
                    if len(nb) == 2:
                        moves[v] = 0.5 * v.co + 0.25 * (nb[0].co + nb[1].co)
                for v, co in moves.items():
                    v.co = co
    if hem_to is not None:
        _drop_hems(bm, float(hem_to))
    if fit:
        _reweight(bm, deform)

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
    for attr in [_SRC_ATTR] + ["__hole%d" % i for i in range(len(holes))]:
        if attr in me.attributes:
            me.attributes.remove(me.attributes[attr])
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
        _hide_body_under(body_obj, covered, border, fit_info.get("hidden"))
    return obj


def _hide_body_under(body_obj, covered, border, hidden=None):
    """Delete body faces fully inside a garment (two vertex rings in from its edge): they cannot
    be seen, they cost triangles, and they are what pokes through cloth in extreme poses.
    ``hidden(co)``: also delete faces wholly where it is true (the crotch inside trousers, where
    the body has faces the garment was not cut from)."""
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
    doomed = [f for f in bm.faces if all(v in safe for v in f.verts)
              or (hidden is not None and all(hidden(v.co) for v in f.verts))]
    if doomed:
        bmesh.ops.delete(bm, geom=doomed, context="FACES")
        bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context="VERTS")
    bm.to_mesh(me)
    bm.free()
    me.update()


# ---------------------------------------------------------------------------------------------
# From skin to cloth (private)
#
# A garment cut from the skin follows every contour of the body: the groin, the gap between the
# thighs, the buttock cleft, kneecaps, nipples. Cloth does not. It spans hollows, hangs straight
# from the widest point above, and lies flat over the fly. Everything here moves vertices only
# (rest pose, before the offset); the garment keeps the body's vertex weights, so it still fits
# any slider setting and deforms with every animation.

_ARM = ("upperarm", "lowerarm", "hand", "thumb", "index", "middle", "ring", "pinky")


def _fit_for(bones):
    """Which fit a set of covered bones gets: a top (the chest), trousers (both thighs), or none."""
    if bones & {"spine_02", "spine_03"}:
        return "top"
    if {"thigh_l", "thigh_r"} <= bones:
        return "trousers"
    if "spine_01" in bones:
        return "top"
    return None


def _dominant_bones(bm, deform, group_names, bone_names):
    out = {}
    for v in bm.verts:
        best, bw = None, 0.0
        for gi, w in v[deform].items():
            g = group_names[gi] if gi < len(group_names) else None
            if g in bone_names and w > bw:
                best, bw = g, w
        out[v] = best
    return out


def _cut_implicit(bm, f, name):
    """Split the mesh along the curve f = 0 (new vertices interpolate weights) and return a float
    layer holding f per vertex: 0 on the cut, > 0 on the side to remove."""
    lay = bm.verts.layers.float.new(name)
    for v in bm.verts:
        v[lay] = float(f(v.co))
    for e in list(bm.edges):
        a, b = e.verts
        fa, fb = a[lay], b[lay]
        if fa == 0.0 or fb == 0.0 or (fa > 0) == (fb > 0):
            continue
        t = fa / (fa - fb)
        if t < 0.04 or t > 0.96:        # snap the near vertex onto the curve instead of a sliver
            v, o, u = (a, b, t) if t < 0.04 else (b, a, 1.0 - t)
            v.co = v.co.lerp(o.co, u)
            v[lay] = 0.0
            continue
        _e, nv = bmesh.utils.edge_split(e, a, t)
        nv[lay] = 0.0
    for face in list(bm.faces):
        zero = [v for v in face.verts if v[lay] == 0.0]
        if len(zero) != 2 or not (any(v[lay] > 0 for v in face.verts) and any(v[lay] < 0 for v in face.verts)):
            continue
        if any(e.other_vert(zero[0]) is zero[1] for e in face.edges if zero[0] in e.verts):
            continue
        bmesh.utils.face_split(face, zero[0], zero[1])
    return lay


def _smoothstep(e0, e1, x):
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0) if e1 != e0 else float(x >= e0)
    return t * t * (3 - 2 * t)


def _ray_to_hull(p, d, hull):
    """Distance from p (inside the convex polygon hull) along unit d to the hull's edge."""
    best = None
    for i in range(len(hull)):
        a, b = hull[i], hull[(i + 1) % len(hull)]
        ex, ey = b[0] - a[0], b[1] - a[1]
        den = d[0] * ey - d[1] * ex
        if abs(den) < 1e-12:
            continue
        wx, wy = a[0] - p[0], a[1] - p[1]
        t = (wx * ey - ex * wy) / den
        s = (wx * d[1] - wy * d[0]) / den
        if t >= -1e-9 and -1e-9 <= s <= 1 + 1e-9 and (best is None or t < best):
            best = t
    return max(best or 0.0, 0.0)


def _hull(pts):
    if len(pts) < 3:
        return None
    return [pts[i] for i in mathutils.geometry.convex_hull_2d(pts)]


def _push_to(v, hull, frac, cap):
    """Horizontal push of v along its own normal towards the hull edge (or None)."""
    n = Vector((v.normal.x, v.normal.y))
    if hull is None or n.length < 0.3:
        return None
    n.normalize()
    push = min(_ray_to_hull((v.co.x, v.co.y), n, hull) * frac, cap(v, n))
    return n * push if push > 1e-5 else None


def _hull_fill(verts, frac, cap, bin_m=0.01):
    """Span horizontal hollows: each vertex moves along its normal towards the convex hull of its
    own horizontal slice (cleavage, buttock cleft, the crease beside the groin, the spine)."""
    bins = {}
    for v in verts:
        bins.setdefault(int(math.floor(v.co.z / bin_m)), []).append(v)
    moves = {}
    for b, own in bins.items():
        hull = _hull([(u.co.x, u.co.y) for k in (b - 1, b, b + 1) for u in bins.get(k, ())])
        for v in own:
            m = _push_to(v, hull, frac, cap)
            if m is not None:
                moves[v] = m
    for v, m in moves.items():
        v.co.x += m.x
        v.co.y += m.y


def _drape(verts, sources, axis, frac, cap, reach_m, taper, bin_m=0.01):
    """Hang cloth from what is above: each vertex moves out towards the hull of the slices up to
    reach_m above it (carried along the limb axis and tapered by ``taper`` per metre), so the
    garment falls straight from the seat and the thigh instead of wrapping knee and calf."""
    src_bins = {}
    for u in sources:
        src_bins.setdefault(int(math.floor(u.co.z / bin_m)), []).append((u.co.x, u.co.y, u.co.z))
    bins = {}
    for v in verts:
        bins.setdefault(int(math.floor(v.co.z / bin_m)), []).append(v)
    steps = int(reach_m / bin_m) + 1
    moves = {}
    for b, own in bins.items():
        zb = (b + 0.5) * bin_m
        ax = axis(zb)
        pts = []
        for k in range(b - 1, b + steps + 1):
            for x, y, z in src_bins.get(k, ()):
                au = axis(z)
                sc = max(0.6, 1.0 - taper * max(0.0, z - zb))
                pts.append((ax[0] + (x - au[0]) * sc, ax[1] + (y - au[1]) * sc))
        hull = _hull(pts)
        for v in own:
            m = _push_to(v, hull, frac, cap)
            if m is not None:
                moves[v] = m
    for v, m in moves.items():
        v.co.x += m.x
        v.co.y += m.y


def _taubin(verts, iters, lam=0.5, mu=-0.53):
    """Smooth without shrinking (Taubin): irons out small bumps (nipples, kneecaps, muscle lines)."""
    verts = [v for v in verts if not v.is_boundary]
    for _ in range(iters):
        for f in (lam, mu):
            new = {}
            for v in verts:
                nb = [e.other_vert(v).co for e in v.link_edges]
                if nb:
                    avg = sum(nb, Vector()) / len(nb)
                    new[v] = v.co + f * (avg - v.co)
            for v, co in new.items():
                v.co = co


def _leg_axis(side):
    """Rest-pose leg axis (x, y) at height z: hip joint, knee, ankle (straight above the hip)."""
    chain = [joint(n + "_" + side) for n in ("thigh", "calf", "foot")]

    def at(z):
        if z >= chain[0].z:
            return (chain[0].x, chain[0].y)
        for a, b in zip(chain, chain[1:]):
            if z >= b.z:
                t = (a.z - z) / max(a.z - b.z, 1e-6)
                return (a.x + t * (b.x - a.x), a.y + t * (b.y - a.y))
        return (chain[-1].x, chain[-1].y)
    return at


TROUSER_EASE_M = 0.035        # most a trouser leg stands off the skin under it
TROUSER_INNER_EASE_M = 0.012  # ... on the inside of the leg, where the legs pass each other


def _fit_trousers(bm, dom, offset_m):
    verts = list(bm.verts)
    hip = joint("thigh_l")
    hip_z, hip_y = hip.z, abs(hip.y)
    pelvis_x = joint("pelvis").x
    # Under the shirt (its hem is at hip + 0.04) the trousers must not grow: the shirt is only a
    # few millimetres further out there.
    tuck_z = hip_z + 0.03
    crotch = [v.co.z for v in verts if dom[v] == "pelvis" and abs(v.co.y) < 0.03]
    z_c = min(crotch) if crotch else hip_z - 0.1

    # 1. Flat fly: in front of the groin nothing may stand further forward than the fronts of
    # the thighs beside it (male bodies carry a bulge there; female ones a cleft).
    def thigh_front(z):
        xs = [v.co.x for v in verts if abs(v.co.z - z) < 0.012 and 0.55 * hip_y <= abs(v.co.y) <= 1.05 * hip_y]
        return max(xs) if xs else None
    def flat_fly(margin=0.0):
        groin = [v for v in verts if v.co.x > pelvis_x + 0.03 and z_c - 0.01 <= v.co.z <= hip_z + 0.05]
        fronts = {key: thigh_front(key) for key in {round(v.co.z, 2) for v in groin} | {round(z_c + 0.045, 2)}}
        for v in groin:
            wz = 1.0 - _smoothstep(hip_z - 0.01, hip_z + 0.045, v.co.z)
            wy = 1.0 - _smoothstep(0.5 * hip_y, 0.9 * hip_y, abs(v.co.y))
            cap = fronts[round(v.co.z, 2)]
            if cap is not None and v.co.x > cap - margin:
                v.co.x -= wz * wy * (v.co.x - cap + margin)
        return fronts
    fronts = flat_fly()

    # 2. Legs hang from the seat and the thighs, and the inner legs close over the crotch.
    # They stop short of the middle by the offset still to come, so the two never cross.
    for side, s in (("l", 1.0), ("r", -1.0)):
        same = [v for v in verts if v.co.y * s > 0 and v.co.z <= tuck_z]
        legs = [v for v in same if v.co.z < z_c]

        def cap(v, n, s=s):
            if n.y * s < -0.5:              # facing the other leg
                near = _smoothstep(z_c - 0.08, z_c - 0.01, v.co.z)
                return (1 - near) * TROUSER_INNER_EASE_M + near * max(0.0, abs(v.co.y) - (offset_m + 0.0006))
            return TROUSER_EASE_M
        _drape(legs, same, _leg_axis(side), 0.8, cap, reach_m=0.24, taper=0.9)
    bm.normal_update()

    # 3. The fly runs down to the crotch seam rather than curling under between the legs, and
    # below the seam the opening between the legs starts as a shallow arch, not a pinch.
    z_u = z_c + 0.045
    ref = fronts.get(round(z_u, 2))
    if ref is not None:
        r = 0.08
        zone = [v for v in verts if z_c - 0.08 <= v.co.z <= z_u + 0.02 and abs(v.co.y) <= 0.6 * hip_y]
        need, room = {}, {}
        for v in zone:
            dz = min(z_u - v.co.z, z_u - z_c)
            target = ref - 0.006 - (r - math.sqrt(max(0.0, r * r - dz * dz))) - 0.35 * max(0.0, z_c - v.co.z)
            w = 1.0 - _smoothstep(0.3 * hip_y, 0.5 * hip_y, abs(v.co.y))
            room[v] = max(0.0, w * (target - v.co.x)) if v.co.z <= z_u else 0.0
            if v.co.x > pelvis_x:
                need[v] = room[v]
        # Spread the move over the neighbourhood, so a vertex the rule leaves behind (the pole
        # where the legs meet) cannot stay back as a dimple: each moves at least as far as asked
        # and as far as its neighbours' average, but never past its own target.
        move = {v: need.get(v, 0.0) for v in zone}
        for _ in range(12):
            nxt = {}
            for v in zone:
                nb = [move.get(e.other_vert(v), 0.0) for e in v.link_edges]
                nxt[v] = min(room[v], max(need.get(v, 0.0), sum(nb) / len(nb) if nb else 0.0))
            move = nxt
        for v, dx in move.items():
            v.co.x += dx
    bm.normal_update()

    # 4. Across the seat and the front: span the buttock cleft and the creases beside the groin.
    seat = [v for v in verts if v.co.z >= z_c]

    def seat_cap(v, n):
        return 0.03 * (1.0 - _smoothstep(hip_z, tuck_z, v.co.z))
    _hull_fill(seat, 1.0, seat_cap)
    bm.normal_update()
    _taubin(verts, 10)
    # Where the legs meet the fly, iron the junction of the two inner legs and the fly flat.
    _taubin([v for v in verts if z_c - 0.05 <= v.co.z <= z_c + 0.05 and abs(v.co.y) < 0.4 * hip_y
             and v.co.x > pelvis_x - 0.02], 25)
    # Spanning and smoothing may have carried the fly forward again; it still may not stand
    # proud of the thighs (3 mm in hand: the offset along the normals adds a little more at the
    # middle, which faces straight forward, than at the thighs, which face partly sideways).
    flat_fly(0.003)
    # Under a shirt's hem (hip + 0.04 and up) the waistband sits 4 mm closer to the skin, so the
    # shirt stays clear of it when the belly folds (squat).
    bm.normal_update()
    for v in verts:
        v.co -= v.normal * (0.004 * _smoothstep(hip_z + 0.02, hip_z + 0.05, v.co.z))
    return {"hidden": lambda co: abs(co.y) < 0.35 * hip_y and z_c - 0.04 < co.z < hip_z - 0.03}


def _fit_top(bm, dom):
    verts = list(bm.verts)
    torso = [v for v in verts if dom[v] is not None and not dom[v].startswith(_ARM)]
    armpit = min(joint("upperarm_l").z, joint("upperarm_r").z) - 0.05
    _hull_fill(torso, 1.0, lambda v, n: 0.03)
    bm.normal_update()
    # Hang from the chest (and the bust) down to the hem; front and back only.
    below = [v for v in torso if v.co.z < armpit]
    px = joint("pelvis").x

    def cap(v, n):
        return 0.04 if abs(n.x) > 0.6 else 0.0
    _drape(below, torso, lambda z: (px, 0.0), 1.0, cap, reach_m=0.14, taper=0.25)
    bm.normal_update()
    _taubin(verts, 10)


def _snapshot_tree():
    """(BVH tree, vertex positions, polygons, per-vertex [(group index, weight)]) of the body
    snapshot; group indices are the body object's vertex groups."""
    if _STATE["tree"] is None:
        from mathutils.bvhtree import BVHTree
        src = _STATE["source"]
        cos = [v.co.copy() for v in src.vertices]
        polys = [tuple(p.vertices) for p in src.polygons]
        groups = [[(g.group, g.weight) for g in v.groups] for v in src.vertices]
        _STATE["tree"] = (BVHTree.FromPolygons(cos, polys), cos, polys, groups)
    return _STATE["tree"]


def _skin_weights_at(co):
    """{body group index: weight} of the body surface nearest to co, interpolated over that face
    (rest pose; None if there is no body). Returns (weights, distance)."""
    from mathutils.interpolate import poly_3d_calc
    tree, cos, polys, groups = _snapshot_tree()
    loc, _n, idx, dist = tree.find_nearest(co)
    if idx is None:
        return None, None
    poly = polys[idx]
    out = {}
    for i, b in zip(poly, poly_3d_calc([cos[i] for i in poly], loc)):
        for gi, w in groups[i]:
            out[gi] = out.get(gi, 0.0) + b * w
    return {gi: w for gi, w in out.items() if w > 1e-4}, dist


def _skin_weights_by_name(co):
    """_skin_weights_at with bone names as keys."""
    names = [g.name for g in body().vertex_groups]
    w, _ = _skin_weights_at(co)
    return {names[gi]: x for gi, x in (w or {}).items() if gi < len(names)}


def _reweight(bm, deform):
    """Skin every vertex the fit moved like the body surface nearest to where it now is, so
    cloth and the skin just under it move together in every pose; a vertex left on the body
    keeps its own weights."""
    for v in bm.verts:
        new, dist = _skin_weights_at(v.co)
        if new is None or dist < 1e-5:
            continue
        dv = v[deform]
        dv.clear()
        for gi, w in new.items():
            dv[gi] = w


def _drop_hems(bm, z):
    """Extend every boundary below the knees straight down to height z (same x, y; the new ring
    copies the weights of the ring above it)."""
    knee = max(joint("calf_l").z, joint("calf_r").z)
    edges = [e for e in bm.edges if e.is_boundary and all(z + 0.005 < v.co.z < knee for v in e.verts)]
    if not edges:
        return
    res = bmesh.ops.extrude_edge_only(bm, edges=edges)
    for v in res["geom"]:
        if isinstance(v, bmesh.types.BMVert):
            v.co.z = z
    bm.normal_update()


def _fit(bm, fit, dom, offset_m):
    bm.normal_update()
    if fit == "trousers":
        return _fit_trousers(bm, dom, offset_m)
    elif fit == "top":
        _fit_top(bm, dom)


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
