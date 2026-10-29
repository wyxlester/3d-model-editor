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


# Necklines and armholes are smooth curves on the body (crew._garment ``holes``), measured from
# the body snapshot, so they sit in the same place on the body at any slider setting.

def _neck_frame(arm):
    """Neck base (neck_01 head), half-width of the neck there, and the height of the top of the
    shoulders (body surface, above the neck base's x) at a given distance from the midline."""
    base = _bone_head(arm, "neck_01")
    pts = crew_custom._body_points()
    # measured on the neck itself, above where it flares into the trapezius
    neck = [abs(p.y) for p, g in pts if g == "neck_01" and base.z + 0.05 <= p.z <= base.z + 0.08]
    neck_r = max(neck) if neck else 0.06
    tags = TORSO | {"neck_01", "upperarm_l", "upperarm_r"}

    def shoulder_top(y):
        zs = [p.z for p, g in pts if g in tags and abs(abs(p.y) - y) < 0.01 and abs(p.x - base.x) < 0.06]
        return max(zs) if zs else base.z
    return base, neck_r, shoulder_top


def _neckline(arm, extra_w, drop_front, drop_back, power=2.0, side_lift=0.025):
    """A round neckline: f > 0 above it. It meets the shoulders ``extra_w`` m outside the neck
    and dips to ``drop_front`` m below the neck base at the front (``drop_back`` at the back),
    a smooth U seen from the front (``power`` > 2 flattens its bottom into a rounder scoop),
    with no notches. Returns (f, half-width)."""
    base, neck_r, shoulder_top = _neck_frame(arm)
    a = neck_r + extra_w
    # Where the curve meets the shoulder: ``side_lift`` above its top at the half-width (0 puts
    # the edge exactly there, as a tank top's strap needs; more lets a crew neck hug the neck).
    z_side = shoulder_top(a) + side_lift if side_lift <= 0.005 else max(base.z + 0.03, shoulder_top(a) + side_lift)
    z_front, z_back = base.z - drop_front, base.z - drop_back

    def f(p):
        mid = z_back + (z_front - z_back) * crew_custom._smoothstep(base.x - 0.05, base.x + 0.05, p.x)
        return p.z - (mid + (z_side - mid) * (abs(p.y) / a) ** power)
    return f, a


def _armholes(arm, strap_inner, strap_w, depth):
    """Tank-top armholes, one f per side (f > 0 inside the hole): open from the outer edge of a
    ``strap_w`` m shoulder strap down to ``depth`` m below the shoulder joint at the side. Seen
    from the front each is the inner half of an ellipse, so the edge runs smoothly from the strap
    round the armpit; back and front are cut alike."""
    _, _, shoulder_top = _neck_frame(arm)
    pts = crew_custom._body_points()
    edge = strap_inner + strap_w
    z_top = shoulder_top(edge)
    out = []
    for s, sign in (("l", 1.0), ("r", -1.0)):
        joint = _bone_head(arm, "upperarm_" + s)
        z_bot = joint.z - depth
        side = [abs(p.y) for p, g in pts if g in TORSO and abs(p.z - z_bot) < 0.015 and p.y * sign > 0]
        y_c = max(max(side) if side else 0.15, abs(joint.y)) + 0.03
        r_y = y_c - (edge - 0.025)
        q = (y_c - edge) / r_y
        r_z = (z_top - z_bot) / (1.0 + math.sqrt(1.0 - q * q))
        z_c = z_bot + r_z

        def f(p, sign=sign, y_c=y_c, r_y=r_y, r_z=r_z, z_c=z_c):
            if p.y * sign <= 0:
                return -1.0
            if p.z >= z_top:       # over the top of the shoulder the strap's outer edge runs straight
                return abs(p.y) - edge
            t = abs(p.z - z_c) / r_z
            inner = y_c - r_y * math.sqrt(1.0 - t * t) if t < 1.0 else y_c + 3.0 * r_y * (t - 1.0)
            return abs(p.y) - inner
        out.append(f)
    return out


SINGLET_STRAP_M = 0.05     # shoulder strap width (4-6 cm on an adult tank top)


def build_shirt(preset, arm, log):
    style, color = preset["style"], preset["color"]
    h = _hems(arm)
    name = "Clothes__Shirt"
    hem = h["hip"] + 0.04           # overlaps the pants waist (hip + 0.12), so no skin shows between them
    # The thighs are in every top's region: round the hips the skin above the hem is partly
    # thigh-driven, and leaving it out cut holes in the shirt there that the trousers showed through.
    if style == "singlet":
        # Tank top: a round neck scooped to below the collarbones, narrow straps over the top of
        # the shoulders, and deep armholes that leave shoulder, deltoid and armpit bare.
        neck, a = _neckline(arm, 0.03, 0.12, 0.05, power=3.0, side_lift=0.0)
        torso_len = _bone_head(arm, "upperarm_l").z - h["hip"]
        holes = [neck] + _armholes(arm, a, SINGLET_STRAP_M, 0.36 * torso_len)
        # The straps lie on the top of the shoulder, which the upper arm partly drives, so the
        # upper arms are in the region (else the bone rule saws into the straps) and the
        # armhole curves alone decide where the cloth ends.
        obj = crew_custom._garment(name, ["torso", "pelvis", "thighs", "neck", "upper_arms"], color, offset_m=0.012,
                                   thickness_m=0.003, z_min=hem, holes=holes)
    else:
        neck, _ = _neckline(arm, 0.015 if style == "polo" else 0.012, 0.05 if style == "polo" else 0.045, -0.01)
        regions = ["torso", "pelvis", "thighs", "neck", "upper_arms"] + (["forearms"] if style == "long_sleeve" else [])
        cuts = () if style == "long_sleeve" else _sleeve_cuts(arm, 0.55)   # tshirt, polo: to above the elbow
        obj = crew_custom._garment(name, regions, color, offset_m=0.012, z_min=hem, cuts=cuts, holes=[neck])
        if style == "polo":
            _polo_collar(obj, arm)
    log("shirt %s: %d triangles" % (style, _tris(obj)))
    return obj


def _boundary_loops(bm):
    """Boundary edge loops of a bmesh, each an ordered list of verts."""
    left = dict.fromkeys(e for e in bm.edges if e.is_boundary)     # ordered: builds are repeatable
    loops = []
    while left:
        e = next(iter(left))
        del left[e]
        loop = [e.verts[0], e.verts[1]]
        while True:
            nxt = [x for x in loop[-1].link_edges if x in left]
            if not nxt:
                break
            del left[nxt[0]]
            v = nxt[0].other_vert(loop[-1])
            if v is loop[0]:
                break
            loop.append(v)
        loops.append(loop)
    return loops


def _polo_collar(obj, arm):
    """A turned-down collar: a stand rising from the neckline against the neck, folded over into
    a fall that lies on the shirt round the neck and ends in two points at the front, either side
    of a short button placket. It carries the neckline's own weights, so it moves with it."""
    from mathutils.bvhtree import BVHTree
    base = _bone_head(arm, "neck_01")
    me = obj.data
    bm = bmesh.new()
    bm.from_mesh(me)
    deform = bm.verts.layers.deform.verify()
    bm.normal_update()
    tree = BVHTree.FromBMesh(bm)
    loops = [l for l in _boundary_loops(bm) if len(l) > 8]
    neck = max(loops, key=lambda l: sum(v.co.z for v in l) / len(l))
    # The boundary is the hem turned in; the visible rim is one edge out from it.
    rim = []
    for v in neck:
        inner = [e.other_vert(v) for e in v.link_edges if not e.is_boundary]
        rim.append(inner[0] if len(inner) == 1 else v)
    cx = base.x + 0.01
    up = Vector((0, 0, 1))
    # Open the ring at the front centre: start the strip just past it.
    front = max(range(len(rim)), key=lambda i: rim[i].co.x - 4 * abs(rim[i].co.y))
    rim = rim[front:] + rim[:front]
    rim = [v for v in rim if not (v.co.x > cx and abs(v.co.y) < 0.008)]
    if rim[0].co.y * rim[-1].co.y > 0:      # walk so the strip runs from one side of the front to the other
        rim = rim[1:] + rim[:1]
    # Resample the rim evenly (the cut leaves vertices at uneven spacing) and smooth it, so the
    # collar is a clean band rather than a ruff.
    cum = [0.0]
    for a, b in zip(rim, rim[1:]):
        cum.append(cum[-1] + (b.co - a.co).length)
    n = max(12, int(cum[-1] / 0.012))
    samples = []
    j = 0
    for k in range(n + 1):
        d = cum[-1] * k / n
        while j < len(cum) - 2 and cum[j + 1] < d:
            j += 1
        t = (d - cum[j]) / max(cum[j + 1] - cum[j], 1e-9)
        samples.append(rim[j].co.lerp(rim[j + 1].co, t))
    pos = samples
    for _ in range(3):
        pos = [pos[0]] + [0.25 * pos[i - 1] + 0.5 * pos[i] + 0.25 * pos[i + 1] for i in range(1, len(pos) - 1)] + [pos[-1]]
    rows = []
    for p in pos:
        o = Vector((p.x - cx, p.y, 0.0)).normalized()
        frontness = crew_custom._smoothstep(0.55, 0.95, o.x)
        stand = p + up * 0.022 + o * 0.002
        fall = stand + o * (0.030 + 0.012 * frontness) - up * (0.036 + 0.030 * frontness)
        fall += Vector((0, -math.copysign(0.012 * frontness, p.y), 0))   # points towards the placket
        rows.append([p, stand, fall, o])
    for _ in range(2):
        falls = [r[2] for r in rows]
        for i in range(1, len(rows) - 1):
            rows[i][2] = 0.25 * falls[i - 1] + 0.5 * falls[i] + 0.25 * falls[i + 1]
    for r in rows:      # the fall lies on the shirt, never in it
        hit = tree.find_nearest(r[2])
        if hit[0] is not None and (r[2] - hit[0]).dot(hit[1]) < 0.005:
            r[2] = hit[0] + hit[1] * 0.005
    # Skinned like the skin nearest each point (the stand like the neck it stands against), so
    # the collar keeps its place on the neck when the head and spine bend.
    index = {g.name: g.index for g in obj.vertex_groups}
    strip = []
    for p, stand, fall, o in rows:
        row = []
        for co in (p, stand, fall):
            nv = bm.verts.new(co)
            for name, w in crew_custom._skin_weights_by_name(co).items():
                if name in index:
                    nv[deform][index[name]] = w
            row.append(nv)
        strip.append((row, o))
    for (a, oa), (b, ob) in zip(strip, strip[1:]):
        for i in (0, 1):
            face = bm.faces.new((a[i], b[i], b[i + 1], a[i + 1]))
            face.normal_update()
            if face.normal.dot(oa + ob) < 0:
                face.normal_flip()
    # Placket: a raised strip down the front from the neckline, with two buttons' worth of length.
    zf = min(v.co.z for v in rim if v.co.x > cx) if any(v.co.x > cx for v in rim) else base.z - 0.05
    faces = [f for f in bm.faces if abs(f.calc_center_median().y) < 0.03 and f.calc_center_median().x > cx
             and zf - 0.13 < f.calc_center_median().z < zf + 0.01 and f.normal.x > 0.3]
    if faces:
        _raised_patch(bm, faces, [(Vector((0, 0.015, 0)), Vector((0, 1, 0))),
                                  (Vector((0, -0.015, 0)), Vector((0, -1, 0))),
                                  (Vector((0, 0, zf - 0.11)), Vector((0, 0, -1)))], 0.0025, 0.004)
    _canonical_order(bm)
    bm.to_mesh(me)
    bm.free()
    me.update()


def _canonical_order(bm):
    """Sort vertices and faces by position. bmesh's plane cuts create vertices in an order that
    differs from run to run; the same spec must give the same mesh, vertex for vertex, or a change
    to another module would look like a change to this one."""
    def key(co):
        return (round(co.z, 5), round(co.y, 5), round(co.x, 5))
    for seq, at in ((bm.verts, lambda v: v.co), (bm.faces, lambda f: f.calc_center_median())):
        rank = {x: i for i, x in enumerate(sorted(seq, key=lambda x: key(at(x))))}
        seq.sort(key=lambda x: rank[x])


def _raised_patch(bm, faces, planes, lift, sink):
    """Copy ``faces`` of a garment, trim the copy by planes [(point, normal)] (keeping the side
    the normal points away from), lift it ``lift`` m off the garment along its normals and close
    its edge with a wall sinking ``sink`` m back into the garment. The copy keeps the garment's
    vertex weights, so it moves with the cloth under it (pockets, flaps, plackets)."""
    geom = bmesh.ops.duplicate(bm, geom=list(faces))["geom"]
    for co, no in planes:
        g = [x for x in geom if x.is_valid]
        cut = bmesh.ops.bisect_plane(bm, geom=g, dist=1e-6, plane_co=co, plane_no=no, clear_outer=True)
        geom = [x for x in g if x.is_valid] + [x for x in cut["geom_cut"] if x.is_valid]
    # Ordered containers throughout (not sets): the same spec must give the same mesh, vertex
    # for vertex, or a change elsewhere would look like a change to the trousers.
    pf = list(dict.fromkeys(x for x in geom if isinstance(x, bmesh.types.BMFace) and x.is_valid))
    if not pf:
        return
    for f in pf:
        f.normal_update()
    pv = list(dict.fromkeys(v for f in pf for v in f.verts))
    in_patch = set(pf)
    # Area-weighted normals, half blended with the patch's mean: the plane cuts leave slivers
    # whose own normals would throw single vertices out of line.
    mean = sum((f.normal * f.calc_area() for f in pf), Vector()).normalized()
    nrm = {}
    for v in pv:
        n = sum((f.normal * f.calc_area() for f in v.link_faces if f in in_patch), Vector())
        n = n.normalized() if n.length > 1e-12 else mean
        nrm[v] = (n + mean).normalized()
    for v in pv:
        v.co += nrm[v] * lift
    edges = [e for e in dict.fromkeys(e for f in pf for e in f.edges) if sum(f in in_patch for f in e.link_faces) == 1]
    res = bmesh.ops.extrude_edge_only(bm, edges=edges)
    for v in res["geom"]:
        if isinstance(v, bmesh.types.BMVert):
            old = next((e.other_vert(v) for e in v.link_edges if e.other_vert(v) in nrm), None)
            if old is not None:
                v.co -= nrm[old] * (lift + sink)


def build_pants(preset, arm, surface, log):
    style, color = preset["style"], preset["color"]
    h = _hems(arm)
    waist = h["hip"] + 0.12
    if style == "shorts":
        obj = crew_custom._garment("Clothes__Pants", ["pelvis", "spine_01", "thighs"], color,
                                   z_min=0.5 * (h["hip"] + h["knee"]), z_max=waist)
    else:
        # The skin stops above the shoe (MPFB removes it under the socks), so the hem is carried
        # on down, straight, to just above the ankle joint: trousers break over the shoe.
        obj = crew_custom._garment("Clothes__Pants", ["pelvis", "spine_01", "thighs", "shins"], color,
                                   z_min=h["ankle"] + 0.03, z_max=waist, hem_to=h["ankle"] + 0.025)
        if style == "cargo":
            _cargo_pockets(obj, h)
    log("pants %s: %d triangles" % (style, _tris(obj)))
    return obj


def _cargo_pockets(obj, h):
    """Bellows pockets with a flap on the outside of each thigh, half way down: cut from the
    trousers' own surface and stood off it, so they lie flush on the leg and move with it."""
    me = obj.data
    bm = bmesh.new()
    bm.from_mesh(me)
    bm.normal_update()
    zc = 0.5 * (h["hip"] + h["knee"]) + 0.01
    for sign in (1.0, -1.0):
        near = [v for v in bm.verts if abs(v.co.z - zc) < 0.02 and v.co.y * sign > 0]
        outer = max(near, key=lambda v: v.co.y * sign)
        cx, out_y = outer.co.x + 0.005, abs(outer.co.y)

        def faces(z0, z1, half):     # the outer half of this leg only (not the inner legs at the crotch)
            return [f for f in bm.faces if f.normal.y * sign > 0.15 and f.calc_center_median().y * sign > 0.5 * out_y
                    and z0 - 0.03 < f.calc_center_median().z < z1 + 0.03
                    and abs(f.calc_center_median().x - cx) < half + 0.03]

        def box(z0, z1, half):
            return [(Vector((0, 0, z0)), Vector((0, 0, -1))), (Vector((0, 0, z1)), Vector((0, 0, 1))),
                    (Vector((cx - half, 0, 0)), Vector((-1, 0, 0))), (Vector((cx + half, 0, 0)), Vector((1, 0, 0)))]
        _raised_patch(bm, faces(zc - 0.075, zc + 0.05, 0.055), box(zc - 0.075, zc + 0.05, 0.055), 0.008, 0.004)
        bm.normal_update()
        _raised_patch(bm, faces(zc + 0.03, zc + 0.07, 0.058), box(zc + 0.03, zc + 0.07, 0.058), 0.012, 0.005)
        bm.normal_update()
    _canonical_order(bm)
    bm.to_mesh(me)
    bm.free()
    me.update()


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
