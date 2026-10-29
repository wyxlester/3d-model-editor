"""Crew animations, authored as data and baked onto the MPFB game_engine skeleton.

No CC0 motion capture is installed, so these are hand-keyed first drafts: readable at deck
distance, not award-winning.

How the data reads
------------------
Each animation is a list of keys ``(time_s, pose)``. A pose maps channels to values. A pose is
laid over ``BASE`` (a relaxed stance: arms down from MPFB's A-pose): a channel the key names
replaces BASE's value for it, anything neither mentions is at rest.

* ``"<bone>": [x, y, z]`` - a rotation in degrees (XYZ Euler) about the **character's** axes
  (+X forward, +Y the character's left, +Z up), relative to the bone's parent. Right-hand rule,
  so ``y > 0`` tips an upward bone (spine) forward and swings a hanging bone backward;
  ``x > 0`` lifts the left arm out sideways; ``z > 0`` turns to the character's left.
  The arm angles were solved from target directions ("upper arm 10 degrees above horizontal,
  forearm vertical") with a small rotation-difference script, then written down here.
* ``sym(bone_l=...)`` mirrors a left-side rotation to ``_r`` as ``[-x, y, -z]``.
* ``"pelvis_offset": [dx, dy, dz]`` - pelvis translation in metres.
* ``"foot_l"`` / ``"foot_r"``: ``[dx, dy, dz, pitch_deg]`` - the ankle target's offset from its
  rest position (metres) and the foot's pitch (``> 0`` = toes down). Legs are always solved by
  two-bone IK onto these targets, so a foot nobody mentions stays planted exactly where it
  stands at rest. That is what keeps idle, brace and squat from sliding or sinking.

Rotations are interpolated as quaternions (shortest path), so a pose never swings the long way
round between keys. ``"smooth"`` eases in and out of every key (pose-hold-return clips);
``"spline"`` is a looped Catmull-Rom (cycles like walk and idle).
"""

import math

FPS = 30


def sym(**bones):
    """Mirror left-side bone rotations to the right side."""
    out = {}
    for name, v in bones.items():
        out[name] = list(v)
        if name.endswith("_l"):
            out[name[:-2] + "_r"] = [-v[0], v[1], -v[2]]
    return out


def _merge(*poses):
    out = {}
    for p in poses:
        for k, v in p.items():
            out[k] = list(v)
    return out


# Arms hang at the sides, elbows softly bent (MPFB's rest is an A-pose with forearms forward).
_UPPER_DOWN = [-33.7, -0.8, -0.5]
_LOWER_DOWN = [-7.0, 25.9, 11.0]
BASE = sym(upperarm_l=_UPPER_DOWN, lowerarm_l=_LOWER_DOWN)


def _arm_swing(deg):
    """Arm swing for walking: positive swings the arm back, negative forward."""
    return [_UPPER_DOWN[0], _UPPER_DOWN[1] + deg, _UPPER_DOWN[2]], \
           [_LOWER_DOWN[0], _LOWER_DOWN[1] - max(-deg, 0) * 0.6, _LOWER_DOWN[2]]


# ---- idle: breathing and a slow weight shift, 3 s loop -----------------------------------------
_IDLE_A = {"spine_03": [0, 0.5, 0]}
_IDLE_B = _merge(
    {"pelvis_offset": [0.0, 0.02, -0.008], "pelvis": [-1.5, 0, 0], "spine_01": [1.5, 0, 0],
     "spine_03": [0, -1.8, 0], "head": [0, -1.5, 5]},
    sym(clavicle_l=[1.5, 0, 0]),
)

# ---- walk: in place, 1.1 s cycle (left heel strike at 0) ----------------------------------------
_WALK_T = 1.1


def _walk_key(phase, left, right, pelvis_dz, sway, yaw, swing_l):
    ul, ll = _arm_swing(swing_l)
    ur, lr = _arm_swing(-swing_l)
    pose = {
        "foot_l": left, "foot_r": right,
        "pelvis_offset": [0.0, sway, pelvis_dz],
        "pelvis": [0, 0, yaw], "spine_02": [0, 2.0, 0], "spine_03": [0, 0, -1.6 * yaw],
        "head": [0, -2.0, 0.6 * yaw],
        "upperarm_l": ul, "lowerarm_l": ll,
        "upperarm_r": [-ur[0], ur[1], -ur[2]], "lowerarm_r": [-lr[0], lr[1], -lr[2]],
    }
    return (phase * _WALK_T, pose)


_STRIKE = [0.20, 0, 0, -8]       # heel strike, toes slightly up
_MID = [0.02, 0, 0, 0]           # mid stance, flat
_PUSH = [-0.17, 0, 0.04, 22]     # toe off: heel up, ankle rises so the toes stay on the deck
_SWING = [0.00, 0, 0.11, -6]     # mid swing, foot clear of the deck

# ---- brace: knees bend, right hand forward-down onto a rail, hold, return; 2.6 s ----------------
_BRACED = _merge(
    {"pelvis_offset": [-0.04, 0, -0.13], "pelvis": [0, 4, 0], "spine_01": [0, 9, 0],
     "spine_02": [0, 6, 0], "spine_03": [0, 2, 0], "neck_01": [0, -6, 0], "head": [0, -9, 0]},
    {"upperarm_r": [-0.9, -47.3, 42.8], "lowerarm_r": [0.2, 29.1, -22.0], "hand_r": [0, 12, 0]},
    {"upperarm_l": [3.2, -23.0, -21.4], "lowerarm_l": [-6.0, 22.5, 10.2]},
)
_BRACED_HOLD = _merge(_BRACED, {"pelvis_offset": [-0.045, 0.01, -0.14], "spine_01": [0, 10, 0]})

# ---- hands_up: raise both hands (surrender / signal), hold, lower; 2.4 s -----------------------
_HANDS_UP = _merge(
    sym(clavicle_l=[6, 0, 0], upperarm_l=[58.6, -0.2, 0.2], lowerarm_l=[39.4, -39.3, 36.8]),
    {"spine_03": [0, -3, 0], "head": [0, -4, 0]},
)
_HANDS_UP_HOLD = _merge(_HANDS_UP, {"head": [0, -2, 6], "spine_03": [0, -2, 2]})

# ---- squat: hips back and down, arms forward for balance, hold, stand; 2.4 s -------------------
_SQUAT = _merge(
    {"pelvis_offset": [-0.11, 0, -0.40], "pelvis": [0, 12, 0], "spine_01": [0, 14, 0],
     "spine_02": [0, 8, 0], "spine_03": [0, 2, 0], "neck_01": [0, -12, 0], "head": [0, -16, 0]},
    sym(upperarm_l=[49.8, -39.3, -77.7], lowerarm_l=[7.5, 11.1, 21.7]),
)
_SQUAT_HOLD = _merge(_SQUAT, {"pelvis_offset": [-0.11, 0, -0.41]})

ANIMATIONS = {
    "idle": {"interp": "spline", "loop": True, "duration": 3.0,
             "keys": [(0.0, _IDLE_A), (1.5, _IDLE_B)]},
    "walk": {"interp": "spline", "loop": True, "duration": _WALK_T,
             "keys": [
                 _walk_key(0.00, _STRIKE, _PUSH, -0.050, 0.000, -5, 16),
                 _walk_key(0.25, _MID, _SWING, -0.030, 0.012, 0, 0),
                 _walk_key(0.50, _PUSH, _STRIKE, -0.050, 0.000, 5, -16),
                 _walk_key(0.75, _SWING, _MID, -0.030, -0.012, 0, 0),
             ]},
    "brace": {"interp": "smooth", "loop": False, "duration": 2.6,
              "keys": [(0.0, {}), (0.55, _BRACED), (2.0, _BRACED_HOLD), (2.6, {})]},
    "hands_up": {"interp": "smooth", "loop": False, "duration": 2.4,
                 "keys": [(0.0, {}), (0.6, _HANDS_UP), (1.8, _HANDS_UP_HOLD), (2.4, {})]},
    "squat": {"interp": "smooth", "loop": False, "duration": 2.4,
              "keys": [(0.0, {}), (0.8, _SQUAT), (1.6, _SQUAT_HOLD), (2.4, {})]},
    # ---- raise_to_aim: an item carried in both hands goes from the ready carry to the aim (stock in
    # the shoulder) in 1 s, eased in and out, then is held; 2.5 s, feet planted, no loop. The body just
    # stands: the item, arms and head are moved by the two-hand hold (two_hands.HandHold), which blends
    # the carry into the aim over two_hands.RAISE_S. Contract section 6, "Stance".
    "raise_to_aim": {"interp": "smooth", "loop": False, "duration": 2.5, "keys": [(0.0, {}), (2.5, {})]},
}
# Clips whose feet never leave the deck (build_crew.check_feet holds them to it).
PLANTED = ("idle", "brace", "squat", "hands_up", "raise_to_aim")

LEG_BONES = ("thigh_l", "calf_l", "foot_l", "thigh_r", "calf_r", "foot_r")


# ---------------------------------------------------------------------------------------------
# Pure sampling (no bpy): unit-tested outside Blender.

def _channels(anim):
    names = set(BASE)
    for _, pose in anim["keys"]:
        names.update(pose)
    names.update(("foot_l", "foot_r", "pelvis_offset"))
    return sorted(names)


def is_rotation(name):
    return name not in ("foot_l", "foot_r", "pelvis_offset")


def _key_value(pose, name):
    if name in pose:
        return list(pose[name])
    if name in BASE:
        return list(BASE[name])
    return [0.0, 0.0, 0.0, 0.0] if name in ("foot_l", "foot_r") else [0.0, 0.0, 0.0]


def euler_to_quat(deg):
    """XYZ Euler (degrees, extrinsic X then Y then Z, as Blender's 'XYZ') -> (w, x, y, z)."""
    hx, hy, hz = (math.radians(a) / 2 for a in deg)
    cx, sx, cy, sy, cz, sz = math.cos(hx), math.sin(hx), math.cos(hy), math.sin(hy), math.cos(hz), math.sin(hz)
    # q = qz * qy * qx
    return (cz * cy * cx + sz * sy * sx,
            cz * cy * sx - sz * sy * cx,
            cz * sy * cx + sz * cy * sx,
            sz * cy * cx - cz * sy * sx)


def _qnorm(q):
    n = math.sqrt(sum(c * c for c in q)) or 1.0
    return tuple(c / n for c in q)


def _align(qs):
    """Flip signs so consecutive quaternions are in the same hemisphere (shortest path)."""
    out = [qs[0]]
    for q in qs[1:]:
        if sum(a * b for a, b in zip(out[-1], q)) < 0:
            q = tuple(-c for c in q)
        out.append(q)
    return out


def _slerp(a, b, u):
    dot = sum(x * y for x, y in zip(a, b))
    if dot < 0:
        b, dot = tuple(-c for c in b), -dot
    if dot > 0.9995:
        return _qnorm([x + (y - x) * u for x, y in zip(a, b)])
    th = math.acos(min(dot, 1.0))
    s = math.sin(th)
    wa, wb = math.sin((1 - u) * th) / s, math.sin(u * th) / s
    return tuple(wa * x + wb * y for x, y in zip(a, b))


def _ease(u):
    return u * u * (3 - 2 * u)


def _catmull(p0, p1, p2, p3, u):
    u2, u3 = u * u, u * u * u
    return [0.5 * ((2 * b) + (-a + c) * u + (2 * a - 5 * b + 4 * c - d) * u2 + (-a + 3 * b - 3 * c + d) * u3)
            for a, b, c, d in zip(p0, p1, p2, p3)]


def _segment(anim, t):
    """(index of the key at or before t, local parameter u in [0,1], key count)."""
    times = [k[0] for k in anim["keys"]]
    n = len(times)
    if anim["interp"] == "spline":
        tt = t % anim["duration"]
        i = max(j for j in range(n) if times[j] <= tt + 1e-9)
        t1 = times[i + 1] if i + 1 < n else anim["duration"]
        return i, (0.0 if t1 == times[i] else (tt - times[i]) / (t1 - times[i])), n
    if t <= times[0]:
        return 0, 0.0, n
    if t >= times[-1]:
        return n - 1, 0.0, n
    i = max(j for j in range(n) if times[j] <= t)
    return i, (t - times[i]) / (times[i + 1] - times[i]), n


def sample(anim, t):
    """The pose at time t (seconds): rotation channels as quaternions (w, x, y, z) in the
    character frame, translations and foot targets as plain lists."""
    i, u, n = _segment(anim, t)
    spline = anim["interp"] == "spline"
    out = {}
    for name in _channels(anim):
        vals = [_key_value(p, name) for _, p in anim["keys"]]
        if spline:
            idx = [(i - 1) % n, i, (i + 1) % n, (i + 2) % n]
        else:
            idx = [i, i, min(i + 1, n - 1), min(i + 1, n - 1)]
        if is_rotation(name):
            qs = _align([euler_to_quat(vals[k]) for k in idx])
            if spline:
                out[name] = _qnorm(_catmull(qs[0], qs[1], qs[2], qs[3], u))
            else:
                out[name] = _qnorm(_slerp(qs[1], qs[2], _ease(u)))
        else:
            if spline:
                out[name] = _catmull(vals[idx[0]], vals[idx[1]], vals[idx[2]], vals[idx[3]], u)
            else:
                e = _ease(u)
                out[name] = [a + (b - a) * e for a, b in zip(vals[idx[1]], vals[idx[2]])]
    for f in ("foot_l", "foot_r"):
        out[f][2] = max(out[f][2], 0.0)  # an ankle target never goes below its rest height
    return out


def frame_count(anim):
    """Frames to key: a loop omits the last frame's duplicate of the first only in time, not in
    keys, so the clip closes exactly (first frame == last frame)."""
    return int(round(anim["duration"] * FPS))


# ---------------------------------------------------------------------------------------------
# Baking inside Blender.

def bake_all(armature, names, log=print, hold=None):
    """Bake every named clip. ``hold`` (a two_hands.Hold, or None) solves both arms onto an item
    carried in both hands on every frame; without one the arms are exactly the clip's."""
    import bpy  # noqa: F401  (only inside Blender)
    rest = _rest_info(armature)
    actions = []
    for name in names:
        actions.append(bake(armature, name, ANIMATIONS[name], rest, hold))
        log("baked animation %s (%d frames)" % (name, frame_count(ANIMATIONS[name]) + 1))
    return actions


def _rest_info(armature):
    info = {}
    for b in armature.data.bones:
        info[b.name] = {
            "mat": b.matrix_local.copy(),
            "q": b.matrix_local.to_quaternion(),
            "head": b.head_local.copy(),
            "tail": b.tail_local.copy(),
            "length": (b.tail_local - b.head_local).length,
        }
    return info


def bake(armature, name, anim, rest, hold=None):
    import bpy
    from mathutils import Euler, Matrix, Quaternion, Vector

    for pb in armature.pose.bones:
        pb.rotation_mode = "QUATERNION"
    if armature.animation_data is None:
        armature.animation_data_create()
    action = bpy.data.actions.new(name)
    action.use_fake_user = True
    armature.animation_data.action = action

    nframes = frame_count(anim)
    prev_q = {}
    pbones = armature.pose.bones
    view_layer = bpy.context.view_layer
    for f in range(nframes + 1):
        t = f / FPS
        pose = sample(anim, t)
        for pb in pbones:
            pb.rotation_quaternion = Quaternion()
            pb.location = Vector()
        for ch, v in pose.items():
            if ch == "pelvis_offset":
                r = rest["pelvis"]["mat"].to_3x3()
                pbones["pelvis"].location = r.inverted() @ Vector(v)
            elif ch in ("foot_l", "foot_r") or ch not in pbones:
                continue
            else:
                d = Quaternion(v)
                m = rest[ch]["q"]
                pbones[ch].rotation_quaternion = m.inverted() @ d @ m
        view_layer.update()
        for side in ("l", "r"):
            _solve_leg(armature, rest, side, pose["foot_" + side], view_layer)
        if hold is not None:
            hold.apply(armature, name, t, view_layer)
        frame = f + 1
        for pb in pbones:
            q = pb.rotation_quaternion.copy()
            if pb.name in prev_q and q.dot(prev_q[pb.name]) < 0:
                q.negate()
                pb.rotation_quaternion = q
            prev_q[pb.name] = q
            pb.keyframe_insert("rotation_quaternion", frame=frame, group=pb.name)
            if pb.name == "pelvis":
                pb.keyframe_insert("location", frame=frame, group=pb.name)
    for fc in _fcurves(action):
        for kp in fc.keyframe_points:
            kp.interpolation = "LINEAR"
    action.frame_range = (1, nframes + 1)
    if hasattr(action, "use_frame_range"):
        action.use_frame_range = True
    if anim.get("loop") and hasattr(action, "use_cyclic"):
        action.use_cyclic = True

    # Park it on a muted NLA track so the glTF exporter (ACTIONS mode) finds every clip.
    track = armature.animation_data.nla_tracks.new()
    track.name = name
    strip = track.strips.new(name, 1, action)
    strip.name = name
    track.mute = True
    armature.animation_data.action = None
    for pb in pbones:
        pb.rotation_quaternion = Quaternion()
        pb.location = Vector()
    return action


def _fcurves(action):
    if hasattr(action, "fcurves") and len(getattr(action, "fcurves", [])):
        return list(action.fcurves)
    out = []
    for layer in getattr(action, "layers", []):
        for strip in layer.strips:
            for bag in strip.channelbags:
                out.extend(bag.fcurves)
    return out


def _solve_leg(armature, rest, side, target, view_layer):
    """Analytic two-bone IK: put the ankle (foot head) on target, knee bending forward, foot
    keeping its rest orientation plus pitch."""
    from mathutils import Matrix, Vector
    pb = armature.pose.bones
    thigh, calf, foot = pb["thigh_" + side], pb["calf_" + side], pb["foot_" + side]
    rt, rc, rf = rest["thigh_" + side], rest["calf_" + side], rest["foot_" + side]
    l1 = rt["length"]
    l2 = (rf["head"] - rc["head"]).length
    hip = thigh.head.copy()
    ankle = rf["head"] + Vector(target[:3])
    d_vec = ankle - hip
    d = d_vec.length
    d = min(max(d, abs(l1 - l2) + 1e-4), (l1 + l2) * 0.9995)
    u = d_vec.normalized()
    ankle = hip + u * d
    fwd = Vector((1.0, 0.12 if side == "l" else -0.12, 0.0))
    v = fwd - u * fwd.dot(u)
    v.normalize()
    a = (l1 * l1 - l2 * l2 + d * d) / (2 * d)
    h = math.sqrt(max(l1 * l1 - a * a, 0.0))
    knee = hip + u * a + v * h

    def aim(rest_bone, head, direction, rest_dir):
        rot = rest_dir.normalized().rotation_difference(direction.normalized()).to_matrix()
        m = (rot @ rest_bone["mat"].to_3x3()).to_4x4()
        m.translation = head
        return m

    thigh.matrix = aim(rt, hip, knee - hip, rt["tail"] - rt["head"])
    view_layer.update()
    calf.matrix = aim(rc, knee, ankle - knee, rf["head"] - rc["head"])
    view_layer.update()
    pitch = Matrix.Rotation(math.radians(target[3]), 3, "Y")
    m = (pitch @ rf["mat"].to_3x3()).to_4x4()
    m.translation = ankle
    foot.matrix = m
    view_layer.update()
