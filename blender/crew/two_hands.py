"""Two-handed carry: equipment placed ``both_hands`` (contract section 6, "Placement"). Inside Blender.

Lester, 29 Oct, in Equipment placement: "carry rifle in both hands. left hand on barrel, right hand
on trigger". Every other place parents an item to one bone and leaves the arms to the animation;
a two-handed carry is the other way round. The item rides on the upper body (spine_03), across the
front of the chest, muzzle forward-down, and both arms are solved onto it frame by frame while the
animations bake. The solved arm rotations are ordinary keys, so the GLB (and Unreal) gets the hold
in every clip with no IK at runtime.

How the item is read (all in the item's own frame, as its code modelled it):

* **Grip** (right hand) and **support** (left hand) are the two points the hands close on. Custom
  item code may declare them with ``crew.two_hand_grips``; otherwise they are inferred along the
  item's longest extent: the grip is the origin when that lies 15-50% along from the back end
  (every held item is modelled with its one-hand grip there: a rifle's pistol grip), else a third
  along; the support two thirds along. The front end is the one further from the origin, so a
  rifle modelled muzzle-up with the pistol grip at the origin points its muzzle forward.
* **Down** is the side the item hangs furthest from the grip-support line (a rifle's magazine and
  pistol grip). It faces the deck in the carry.

Where it goes is searched, not hard-coded: carry angles (muzzle 30-45 degrees down, turned 15-45
degrees across the body), grip heights and sides, each pushed forward until the whole item (tested
every 3 cm along its edges) clears the torso's cross-section, keeping the first, most natural one
both arms reach within 92% of full stretch; a shoulder may come forward up to 20 degrees to get
there. All measured from the body, so it fits every body size.

hands_up (decided 29 Oct): the hands let go and go up; the item stays where it is carried, as if
on a sling. The item is a rigid mesh on spine_03; a real drop or an overhead hold would need the
item itself animated, which Unreal does not import for a mesh riding on a skeleton, and moving it
to a hand bone would break the carry in every other clip. The hands come back to the grips at the
end of the clip, so it still loops into idle.
"""

import math

from mathutils import Matrix, Quaternion, Vector

PLACE = "both_hands"
PARENT_BONE = "spine_03"
GRIP_PROP, SUPPORT_PROP = "me3d_grip", "me3d_support"

GRIP_ORIGIN_SPAN = (0.15, 0.5)   # the origin is used as the grip when it lies this far from the back end
MIN_LENGTH_M = 0.30          # shorter than this, two hands on it would overlap
GAP_M = 0.03                 # item to torso (or vest) clearance at rest
PROBE_STEP_M = 0.03          # item points tested for clearance at least this often along its edges
X_FLOOR_M = 0.10             # grip at least this far in front of the spine
PALM_INSET_M = {"r": 0.012, "l": 0.018}   # palm centre beside / below the item's centre line
REACH_OK = 0.92              # fraction of full arm reach (shoulder to wrist) a carry may use
PROTRACT_STEPS = (0, 5, 10, 15, 20)   # degrees a shoulder may come forward to reach (clavicle)
# Carry angles tried, preferred first: (muzzle below horizontal, turned toward the left) degrees.
ANGLES = [(35, 25), (35, 20), (35, 30), (30, 25), (40, 25), (30, 30), (40, 30), (35, 15),
          (30, 35), (45, 25), (45, 30), (30, 15), (45, 20), (35, 35), (40, 15), (45, 35),
          (30, 40), (35, 40), (40, 40), (30, 45), (35, 45)]
# Grip heights (spine_03 + 10 cm down to spine_01, in steps) and sides (fractions of the half shoulder
# width to the right). The far-right ones let a long back end (a stock, a pole's butt) pass beside
# the right flank, under the arm, instead of pushing the whole carry out in front of the chest.
GRIP_SIDES = (0.45, 0.3, 0.6, 0.8, 1.0, 1.2, 1.4)
# Release schedule per clip: (time_s, how far the hands have let go). Clips not listed hold
# throughout. hands_up mirrors the clip's own keys (0.0 rest, 0.6 up, 1.8 held, 2.4 rest).
RELEASE = {"hands_up": [(0.0, 0.0), (0.6, 1.0), (1.8, 1.0), (2.4, 0.0)]}
# Finger curl (degrees) per joint 1, 2, 3 when closed on the item. The right index is on the trigger:
# curled less. Thumbs close over the item a little.
CURL = {"index": (30, 40, 25), "middle": (50, 60, 35), "ring": (55, 60, 35), "pinky": (55, 60, 35),
        "thumb": (0, 20, 20)}
CURL_TRIGGER_INDEX = (20, 25, 15)
FINGERS = ("thumb", "index", "middle", "ring", "pinky")
# Elbow directions (character frame: +X forward, +Y left, +Z up): the grip elbow down, back and
# out; the support elbow down and out.
POLE = {"r": Vector((-0.25, -0.6, -1.0)), "l": Vector((0.0, 0.5, -1.0))}


# ---------------------------------------------------------------------------------------------
# The item: grip, support and its axes, in its own (scaled) frame

def _mesh_points(obj):
    s = obj.matrix_world.to_scale()
    return [Vector((v.co.x * s.x, v.co.y * s.y, v.co.z * s.z)) for v in obj.data.vertices], s


def _section_centre(pts, edges, axis, at):
    """Centre of the item's cross-section by the plane ``p . axis == at`` (edge crossings), so it
    works for low-poly boxes whose vertices are only at their ends."""
    hits = []
    for a, b in edges:
        pa, pb = pts[a], pts[b]
        sa, sb = pa.dot(axis), pb.dot(axis)
        if (sa - at) * (sb - at) <= 0 and abs(sb - sa) > 1e-9:
            hits.append(pa + (pb - pa) * ((at - sa) / (sb - sa)))
    if not hits:
        hits = sorted(pts, key=lambda p: abs(p.dot(axis) - at))[:4]
    return sum(hits, Vector()) / len(hits)


def item_frame(obj):
    """(grip, support, axis, down, length) in the item's scaled local frame: axis runs from the back
    end to the front (unit), down is the side the item hangs furthest from the grip-support line."""
    pts, s = _mesh_points(obj)
    if len(pts) < 2:
        raise ValueError("%s has no geometry to hold" % obj.name)
    edges = [tuple(e.vertices) for e in obj.data.edges]
    lo = Vector([min(p[i] for p in pts) for i in range(3)])
    hi = Vector([max(p[i] for p in pts) for i in range(3)])
    k = max(range(3), key=lambda i: hi[i] - lo[i])
    axis = Vector((0, 0, 0))
    axis[k] = 1.0 if hi[k] >= -lo[k] else -1.0      # front = the end further from the origin
    declared = obj.get(GRIP_PROP), obj.get(SUPPORT_PROP)
    if declared[0] is not None and declared[1] is not None:
        grip = Vector([float(declared[0][i]) * s[i] for i in range(3)])
        support = Vector([float(declared[1][i]) * s[i] for i in range(3)])
        if (support - grip).length < 0.05:
            raise ValueError("%s: grip and support are %.0f mm apart; two hands need them further apart"
                             % (obj.name, 1000 * (support - grip).length))
        axis = (support - grip).normalized()
    else:
        ss = [p.dot(axis) for p in pts]
        s0, s1 = min(ss), max(ss)
        grip = _section_centre(pts, edges, axis, s0 + (s1 - s0) / 3.0)
        support = _section_centre(pts, edges, axis, s0 + 2.0 * (s1 - s0) / 3.0)
        # Every held item is modelled with its one-hand grip at the origin (contract section 6),
        # which on a rifle is the pistol grip and trigger. When that lies in the back part of the
        # item, on it, it is the better grip than the plain third.
        u = -s0 / (s1 - s0) if s1 > s0 else 0.0
        if GRIP_ORIGIN_SPAN[0] <= u <= GRIP_ORIGIN_SPAN[1] and min(p.length for p in pts) < 0.03:
            grip = Vector((0.0, 0.0, 0.0))
    ss = [p.dot(axis) for p in pts]
    length = max(ss) - min(ss)
    # Down: of the four directions square to the axis, the one the mesh reaches furthest along
    # from the grip-support line. Near-ties (a plain pole) prefer the item's -Z, then +X.
    ref = Vector((0, 0, -1)) if abs(axis.z) < 0.9 else Vector((1, 0, 0))
    e1 = (ref - axis * ref.dot(axis)).normalized()
    e2 = axis.cross(e1)
    best = None
    for rank, d in enumerate((e1, -e1, e2, -e2)):
        reach = max((p - grip).dot(d) for p in pts)
        if best is None or reach > best[0] + 0.005:
            best = (reach, d)
    return grip, support, axis, best[1].normalized(), length


def _basis(x, y, z):
    return Matrix((x, y, z)).transposed()


def _carry_axes(pitch_deg, yaw_deg):
    """World direction of the item's axis (muzzle forward-down, turned to the left) and of its down."""
    p, y = math.radians(pitch_deg), math.radians(yaw_deg)
    d = Vector((math.cos(p) * math.cos(y), math.cos(p) * math.sin(y), -math.sin(p)))
    down = Vector((0, 0, -1))
    down = (down - d * down.dot(d)).normalized()
    return d, down


# ---------------------------------------------------------------------------------------------
# The hands at rest

def _rest_hands(arm):
    """Per side: rest wrist, palm-centre offset in the hand's own (finger, across, palm) basis,
    rest hand matrix, the basis, and arm segment lengths."""
    bones = arm.data.bones
    out = {}
    for s in "lr":
        h = lambda n: bones[n + "_" + s].head_local.copy()
        wrist = h("hand")
        palm = (h("hand") + h("index_01") + h("middle_01") + h("ring_01") + h("pinky_01")) / 5
        palm = palm + 0.4 * (h("thumb_02") - palm)
        f = (h("middle_01") - wrist).normalized()
        a = h("pinky_01") - h("index_01")
        a = (a - f * a.dot(f)).normalized()
        n = f.cross(a) if s == "r" else -f.cross(a)
        basis = _basis(f, a, n)
        out[s] = {
            "wrist": wrist, "palm_local": basis.transposed() @ (palm - wrist), "basis": basis,
            "hand_mat": bones["hand_" + s].matrix_local.to_3x3(),
            "l1": (h("lowerarm") - h("upperarm")).length, "l2": (wrist - h("lowerarm")).length,
            "upper": bones["upperarm_" + s].matrix_local.copy(),
            "lower": bones["lowerarm_" + s].matrix_local.copy(),
            "upper_dir": bones["lowerarm_" + s].head_local - bones["upperarm_" + s].head_local,
            "lower_dir": wrist - bones["lowerarm_" + s].head_local,
        }
    return out


def _hand_targets(grip_w, support_w, d, down):
    """Per side: (palm centre target, target (finger, across, palm) basis). Right hand on the grip,
    handshake-like (palm facing the item's left side, knuckles along its down); left hand under the
    support, palm up, fingers wrapping across to the item's right."""
    side = d.cross(down)                     # the item's left
    right = (grip_w - side * PALM_INSET_M["r"], _basis(d, down, side))
    up = -down
    left = (support_w + down * PALM_INSET_M["l"], _basis(up.cross(-d), -d, up))
    return {"r": right, "l": left}


def _wrist_target(rest, s, palm_target, basis):
    return palm_target - basis @ rest[s]["palm_local"]


# ---------------------------------------------------------------------------------------------
# Placing the item

def _front_map(surface, tags):
    """The torso's front surface as horizontal cross-sections, one per 2 cm of height: (back x,
    front x, right y, left y) of the surface points within 5 cm of that height. Sections, not
    points, because a low-poly life vest has a ring of points only every 8 cm: a point lookup
    would let the item pass through it between rings."""
    pts = [p for p, g in surface.pts if g in tags]
    if not pts:
        return {}
    out = {}
    lo, hi = int(math.floor(min(p.z for p in pts) / 0.02)), int(math.floor(max(p.z for p in pts) / 0.02))
    for k in range(lo, hi + 1):
        zc = (k + 0.5) * 0.02
        band = [p for p in pts if abs(p.z - zc) < 0.05]
        if len(band) >= 6:
            out[k] = (min(p.x for p in band), max(p.x for p in band), min(p.y for p in band), max(p.y for p in band))
    return out


def _front_at(front, y, z):
    """Front x of the torso at (y, z), or None beside or above/below it. The section is taken as a
    rounded box (superellipse, power 4): fuller than an ellipse, so it errs on the side of clear."""
    sec = front.get(int(math.floor(z / 0.02)))
    if sec is None:
        return None
    x0, x1, y0, y1 = sec
    cy, ry = (y0 + y1) / 2, (y1 - y0) / 2
    t = abs(y - cy) / ry if ry > 1e-6 else 2.0
    if t >= 1.0:
        return None
    cx = (x0 + x1) / 2
    return cx + (x1 - cx) * (1.0 - t ** 4) ** 0.25


def _clear_x(front, rel, y, z):
    """Smallest grip x that puts every item point (rel = offset from the grip) GAP_M in front of the
    torso surface it overlaps."""
    need = -1e9
    for r in rel:
        f = _front_at(front, y + r.y, z + r.z)
        if f is not None:
            need = max(need, f + GAP_M - r.x)
    return need


def plan(obj, arm, surface):
    """Choose the carry for obj. Returns a dict with the item's rest world matrix and the numbers the
    report and the per-frame solve need."""
    grip, support, axis, down, length = item_frame(obj)
    if length < MIN_LENGTH_M:
        raise ValueError("%s is %.0f cm long: too short to carry in both hands (both_hands is for long "
                         "items such as a rifle, boat hook or pole; at least %.0f cm)"
                         % (obj.name, 100 * length, 100 * MIN_LENGTH_M))
    pts, scale = _mesh_points(obj)
    # Clearance is tested along every edge, not only at vertices: a low-poly pole has vertices only
    # at its ends, and its middle would pass through the body unseen.
    probe = list(pts)
    for a, b in (tuple(e.vertices) for e in obj.data.edges):
        n = int((pts[b] - pts[a]).length / PROBE_STEP_M)
        probe += [pts[a].lerp(pts[b], k / (n + 1)) for k in range(1, n + 1)]
    local = _basis(axis, down, axis.cross(down))
    rest = _rest_hands(arm)
    bones = arm.data.bones
    shoulder = {s: bones["upperarm_" + s].head_local.copy() for s in "lr"}
    half_w = abs(shoulder["r"].y - shoulder["l"].y) / 2
    z_lo, z_hi = bones["spine_01"].head_local.z, bones["spine_03"].head_local.z + 0.10
    front = _front_map(surface, {"pelvis", "spine_01", "spine_02", "spine_03", "clavicle_l", "clavicle_r",
                                 "life_vest", "thigh_l", "thigh_r"})
    x_floor = bones["spine_02"].head_local.x + X_FLOOR_M
    best = None
    for pitch, yaw in ANGLES:
        d, dn = _carry_axes(pitch, yaw)
        rot = _basis(d, dn, d.cross(dn)) @ local.transposed()
        rel = [rot @ (p - grip) for p in probe]
        sup_rel = rot @ (support - grip)
        for i in range(8):
            z = z_hi - (z_hi - z_lo) * i / 7          # chest height first, down to the belt
            for fy in GRIP_SIDES:
                y = -fy * half_w
                # An item that overlaps no part of the torso (carried beside the flank) still keeps
                # its grip a hand's width in front of the body's centre line.
                x = max(_clear_x(front, rel, y, z), x_floor)
                gw = Vector((x, y, z))
                tg = _hand_targets(gw, gw + sup_rel, d, dn)
                reach, prot = {}, {}
                for side in "lr":
                    wrist = _wrist_target(rest, side, *tg[side])
                    full = rest[side]["l1"] + rest[side]["l2"]
                    for deg in PROTRACT_STEPS:
                        reach[side], prot[side] = (wrist - _shoulder(bones, side, deg)).length / full, deg
                        if reach[side] <= REACH_OK:
                            break
                ratio = max(reach.values())
                # Prefer the earliest (most natural) carry that fits; otherwise the best reach.
                cand = (ratio, pitch, yaw, gw, rot, dict(prot))
                if best is None or (ratio <= REACH_OK < best[0]) or (best[0] > REACH_OK and ratio < best[0]):
                    best = cand
                if ratio <= REACH_OK:
                    break
            if best[0] <= REACH_OK:
                break
        if best[0] <= REACH_OK:
            break
    ratio, pitch, yaw, gw, rot, prot = best
    world = Matrix.Translation(gw - rot @ grip) @ rot.to_4x4() @ Matrix.Diagonal(scale.to_4d())
    inv = [1.0 / c if abs(c) > 1e-12 else 0.0 for c in scale]
    return {"world": world, "pitch": pitch, "yaw": yaw, "reach": ratio, "protract": prot,
            "grip": Vector([grip[i] * inv[i] for i in range(3)]),         # mesh coordinates
            "support": Vector([support[i] * inv[i] for i in range(3)]),
            "axis": axis, "down": down, "length": length}


def _protraction(side, deg):
    """Shoulder brought forward: the clavicle turned about the vertical, its tip toward +X."""
    return Matrix.Rotation(math.radians(deg if side == "r" else -deg), 3, "Z")


def _shoulder(bones, side, deg):
    clav = bones["clavicle_" + side].head_local
    return clav + _protraction(side, deg) @ (bones["upperarm_" + side].head_local - clav)


class Hold:
    """The item held in both hands, and the per-frame arm solve the animation bake calls."""

    def __init__(self, obj, arm, info):
        self.obj, self.info = obj, info
        self.rest = _rest_hands(arm)
        self.curl = _curl_quats(arm)
        self.protract = {}
        for side, deg in info.get("protract", {}).items():
            if deg:
                m = arm.data.bones["clavicle_" + side].matrix_local.to_quaternion()
                self.protract["clavicle_" + side] = m.inverted() @ _protraction(side, deg).to_quaternion() @ m

    # Item pose follows its bone: world = bone tail (posed) @ matrix_basis (bone parenting).
    def item_matrix(self, arm):
        pb = arm.pose.bones[self.obj.parent_bone]
        tail = arm.matrix_world @ pb.matrix @ Matrix.Translation((0.0, pb.bone.length, 0.0))
        return tail @ self.obj.matrix_basis

    def points(self, m):
        """World (grip, support, axis, down) for an item world matrix m."""
        _, rot, _ = m.decompose()
        return m @ self.info["grip"], m @ self.info["support"], rot @ self.info["axis"], rot @ self.info["down"]

    def apply(self, arm, clip, t, view_layer):
        """Pose both arms and hands onto the item for the current frame (the clip's own pose is
        already set); hands_up lets go and comes back by RELEASE."""
        w = release(clip, t)
        pbones = arm.pose.bones
        chain = [pbones["%s_%s" % (b, s)] for s in "lr" for b in ("upperarm", "lowerarm", "hand")]
        clip_q = {pb.name: pb.rotation_quaternion.copy() for pb in chain}
        for name, q in self.protract.items():
            c = pbones[name].rotation_quaternion.copy()
            pbones[name].rotation_quaternion = (c @ q).slerp(c, w)
        if self.protract:
            view_layer.update()
        if w < 1.0:
            m = arm.matrix_world.inverted() @ self.item_matrix(arm)
            grip_w, support_w, d, dn = self.points(m)
            targets = _hand_targets(grip_w, support_w, d, dn)
            for s in "lr":
                _solve_arm(arm, self.rest[s], s, *targets[s], view_layer)
        for pb in chain:
            pb.location = Vector()
            if w > 0.0:
                held = pb.rotation_quaternion.copy() if w < 1.0 else clip_q[pb.name]
                pb.rotation_quaternion = held.slerp(clip_q[pb.name], w)
        for name, q in self.curl.items():
            pbones[name].rotation_quaternion = Quaternion().slerp(q, 1.0 - w)
        view_layer.update()


def release(clip, t):
    keys = RELEASE.get(clip)
    if not keys:
        return 0.0
    if t <= keys[0][0]:
        return keys[0][1]
    for (t0, v0), (t1, v1) in zip(keys, keys[1:]):
        if t <= t1:
            u = (t - t0) / (t1 - t0) if t1 > t0 else 1.0
            u = u * u * (3 - 2 * u)
            return v0 + (v1 - v0) * u
    return keys[-1][1]


def _aim(rest_mat, head, direction, rest_dir):
    rot = rest_dir.normalized().rotation_difference(direction.normalized()).to_matrix()
    m = (rot @ rest_mat.to_3x3()).to_4x4()
    m.translation = head
    return m


def _solve_arm(arm, rest, s, palm_target, basis, view_layer):
    """Analytic two-bone IK (as the legs in animations.py): wrist onto the point that puts the palm
    centre on palm_target with the hand turned to basis; elbow toward POLE; the forearm takes the
    wrist's twist so the hand is not wrung."""
    pb = arm.pose.bones
    up, lo, hand = pb["upperarm_" + s], pb["lowerarm_" + s], pb["hand_" + s]
    hand_rot = basis @ rest["basis"].transposed() @ rest["hand_mat"]
    wrist = _wrist_target({s: rest}, s, palm_target, basis)
    shoulder = up.head.copy()
    l1, l2 = rest["l1"], rest["l2"]
    dv = wrist - shoulder
    dist = min(max(dv.length, abs(l1 - l2) + 1e-4), (l1 + l2) * 0.9995)
    u = dv.normalized()
    pole = POLE[s]
    v = (pole - u * pole.dot(u)).normalized()
    a = (l1 * l1 - l2 * l2 + dist * dist) / (2 * dist)
    h = math.sqrt(max(l1 * l1 - a * a, 0.0))
    elbow = shoulder + u * a + v * h
    wrist = shoulder + u * dist
    up.matrix = _aim(rest["upper"], shoulder, elbow - shoulder, rest["upper_dir"])
    view_layer.update()
    lm = _aim(rest["lower"], elbow, wrist - elbow, rest["lower_dir"])
    # Twist: the part of (forearm -> hand) rotation about the forearm's own axis, given to the forearm.
    lp = lm.to_3x3()
    rel_now = lp.inverted() @ hand_rot
    rel_rest = rest["lower"].to_3x3().inverted() @ rest["hand_mat"]
    q = (rel_now @ rel_rest.inverted()).to_quaternion()
    twist = Quaternion((q.w, 0.0, q.y, 0.0))
    if twist.magnitude > 1e-9:
        twist.normalize()
        lm = (lp @ twist.to_matrix()).to_4x4()
        lm.translation = elbow
    lo.matrix = lm
    view_layer.update()
    hm = hand_rot.to_4x4()
    hm.translation = lo.tail.copy()
    hand.matrix = hm
    view_layer.update()


def _curl_quats(arm):
    """Local rotations that close each finger on the item (constant: a curl is relative to the hand)."""
    bones = arm.data.bones
    out = {}
    for s in "lr":
        h = lambda n: bones[n + "_" + s].head_local
        f = (h("middle_01") - h("hand")).normalized()
        across = h("pinky_01") - h("index_01")
        across = (across - f * across.dot(f)).normalized()
        palm_n = f.cross(across) if s == "r" else -f.cross(across)
        for finger in FINGERS:
            degs = CURL_TRIGGER_INDEX if (finger == "index" and s == "r") else CURL[finger]
            for i, deg in enumerate(degs, start=1):
                name = "%s_0%d_%s" % (finger, i, s)
                if name not in bones or not deg:
                    continue
                b = bones[name]
                bdir = (b.tail_local - b.head_local).normalized()
                ax = bdir.cross(palm_n)              # turning about it moves the tip toward the palm
                if ax.length < 1e-6:
                    continue
                ax.normalize()
                if ax.cross(bdir).dot(palm_n) < 0:
                    ax = -ax
                d = Quaternion(ax, math.radians(deg))
                m = b.matrix_local.to_quaternion()
                out[name] = m.inverted() @ d @ m
    return out


def place_both_hands(obj, arm, surface, log=print):
    """Place obj across the front of the body on spine_03 and return the Hold the bake uses."""
    from . import equipment as eq
    info = plan(obj, arm, surface)
    obj.parent = None
    obj.matrix_world = info["world"]
    eq.parent_to_bone(obj, arm, PARENT_BONE)
    obj["me3d_place"] = PLACE
    obj[GRIP_PROP] = list(info["grip"])
    obj[SUPPORT_PROP] = list(info["support"])
    if info["reach"] > REACH_OK:
        log("both_hands %s: arms at %.0f%% of their reach (long item); hands may fall short"
            % (obj.name, 100 * info["reach"]))
    log("placed %s in both hands on %s: muzzle %d deg down, %d deg across, %.2f m long, reach %.0f%%, "
        "shoulders forward %s deg" % (obj.name, PARENT_BONE, info["pitch"], info["yaw"], info["length"],
                                      100 * info["reach"], info["protract"]))
    return Hold(obj, arm, info)
