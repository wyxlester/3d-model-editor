"""Moves made from words (contract section 6, "Custom moves").

Lester's plan, "Animation editor": prompted moves built as key poses on the shared skeleton, refined in
words ("faster on the way up"), with checks that the feet stay planted and the limbs stay out of the
body. The AI writes a move as a few timed key poses; the builder bakes it like the built-in clips.

The pose convention (what the AI writes)
---------------------------------------
Every angle is in degrees **from the neutral stance**: standing straight, arms hanging at the sides
with the elbows a little soft, hands open. A bone a key does not name is at neutral in that key. Each
bone moves by a few **named motions**, applied innermost first (twist, then the sideways motion, then
the forward one), each about the axis its name says:

=====================================  =========================================================
bones                                  motions (+ means ...), limits in degrees
=====================================  =========================================================
pelvis                                 forward (+ tips the hips forward), left (+ tilts toward
                                       its left side), turn_left (+ turns to its left)
spine_01, spine_02, spine_03           forward (+ bends forward), left, turn_left
neck_01, head                          forward (+ looks down), left (+ tips the head to its left
                                       shoulder), turn_left (+ looks to its left)
clavicle_l, clavicle_r                 up (+ shrugs the shoulder up), forward (+ shoulder forward)
upperarm_l, upperarm_r                 forward (+ swings the arm forward and up: 90 = pointing
                                       straight ahead, 180 = straight up), out (+ raises it out to
                                       the side: 90 = horizontal), twist (+ turns it outward)
lowerarm_l, lowerarm_r                 bend (+ bends the elbow; 0 is the neutral soft bend, about
                                       25 degrees, so -25 is straight and 90 is a right angle
                                       more), twist (+ turns the palm up / forward)
hand_l, hand_r                         bend (+ bends the palm toward the inside of the forearm),
                                       side (+ tips the hand toward the thumb)
=====================================  =========================================================

``"fingers_l"`` / ``"fingers_r"``: ``"open"`` (neutral), ``"fist"`` or ``"point"`` (index straight).
``"pelvis_offset"``: ``[forward, left, up]`` metres (down is negative up: -0.3 is a deep knee bend;
the legs bend to keep the feet where they are). ``"foot_l"`` / ``"foot_r"``: ``[forward, left, up,
pitch_deg]`` - only in a move with ``"feet": "free"``; otherwise the feet stay planted.

A move: ``{"name", "description", "duration_s", "loop", "feet": "planted" | "free", "keys": [{"t":
seconds, "pose": {...}}]}``. A move that does not loop starts and ends at neutral unless its keys say
otherwise (a key at 0 s or at duration_s). Each named motion eases in and out between keys (so an arm
follows the path its motions describe); a looping move is a smooth closed curve through its keys.

The pure part (validation and the table above) runs without Blender: the server checks the AI's moves
with it. The Blender part turns a move into the animation data animations.bake() takes and checks the
baked result for arms or hands through the torso.
"""

import math
import re

NAME = re.compile(r"^[a-z][a-z0-9_]{1,39}$")
BUILT_IN = ("idle", "walk", "brace", "hands_up", "squat", "raise_to_aim")
MIN_DURATION_S, MAX_DURATION_S = 0.3, 20.0
MAX_KEYS = 24
FEET = ("planted", "free")
FINGER_SHAPES = ("open", "fist", "point")

_TRUNK = {"forward": (-15, 30), "left": (-10, 10), "turn_left": (-30, 30)}
_SPINE = {"forward": (-20, 35), "left": (-20, 20), "turn_left": (-25, 25)}
_HEAD = {"forward": (-40, 45), "left": (-30, 30), "turn_left": (-60, 60)}
_CLAV = {"up": (-10, 30), "forward": (-15, 25)}
_UPPER = {"forward": (-50, 180), "out": (-30, 180), "twist": (-90, 90)}
_LOWER = {"bend": (-25, 140), "twist": (-90, 90)}
_HAND = {"bend": (-70, 80), "side": (-25, 35)}
JOINTS = {"pelvis": _TRUNK, "spine_01": _SPINE, "spine_02": _SPINE, "spine_03": _SPINE,
          "neck_01": _HEAD, "head": _HEAD}
for _s in "lr":
    JOINTS.update({"clavicle_" + _s: _CLAV, "upperarm_" + _s: _UPPER, "lowerarm_" + _s: _LOWER, "hand_" + _s: _HAND})
# Applied innermost first: the first listed is applied to the neutral bone first.
ORDER = {"twist": 0, "turn_left": 0, "side": 1, "out": 1, "left": 1, "up": 1, "bend": 2, "forward": 2}
OFFSET_LIMITS = ((-0.3, 0.3), (-0.3, 0.3), (-0.5, 0.15))
FOOT_LIMITS = ((-0.6, 0.6), (-0.4, 0.4), (0.0, 0.5), (-45, 60))
ARM_BONES = tuple("%s_%s" % (b, s) for s in "lr" for b in ("clavicle", "upperarm", "lowerarm", "hand"))


def joint_docs():
    """One line per bone group, for the AI's tool docs."""
    seen, out = set(), []
    for bone, axes in JOINTS.items():
        key = bone[:-2] if bone.endswith(("_l", "_r")) else bone
        if key in seen:
            continue
        seen.add(key)
        out.append("%s: %s" % (key + ("_l/_r" if key != bone else ""),
                                ", ".join("%s %g..%g" % (a, lo, hi) for a, (lo, hi) in axes.items())))
    return "; ".join(out)


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def validate(moves, errors, warnings=None):
    """Check custom_moves; returns the cleaned list (each move complete, keys sorted). Errors name the
    move, the key (by number and time) and the field, in words the AI can act on."""
    warnings = [] if warnings is None else warnings
    if moves is None:
        return []
    if not isinstance(moves, list):
        errors.append("custom_moves must be a list of moves")
        return []
    out, names = [], set()
    for i, m in enumerate(moves):
        if not isinstance(m, dict):
            errors.append("custom_moves[%d] must be an object {name, duration_s, loop, keys}" % i)
            continue
        name = m.get("name")
        if not isinstance(name, str) or not NAME.match(name):
            errors.append("custom_moves[%d].name must be lower-case letters, digits and _ (2-40), starting with a "
                          "letter, got %r" % (i, name))
            continue
        if name in BUILT_IN:
            errors.append("move %s: that is a built-in move's name; pick another" % name)
            continue
        if name in names:
            errors.append("move %s: the name is used twice" % name)
            continue
        names.add(name)
        bad = len(errors)
        dur = m.get("duration_s")
        if not _num(dur) or not MIN_DURATION_S <= dur <= MAX_DURATION_S:
            errors.append("move %s: duration_s must be %g..%g seconds, got %r" % (name, MIN_DURATION_S, MAX_DURATION_S, dur))
            dur = 1.0
        loop = m.get("loop", False)
        if not isinstance(loop, bool):
            errors.append("move %s: loop is true or false, got %r" % (name, loop))
        feet = m.get("feet", "planted")
        if feet not in FEET:
            errors.append("move %s: feet must be %s, got %r" % (name, " or ".join(FEET), feet))
        keys = m.get("keys")
        if not isinstance(keys, list) or not 1 <= len(keys) <= MAX_KEYS:
            errors.append("move %s: keys must be a list of 1 to %d key poses {t, pose}" % (name, MAX_KEYS))
            continue
        clean_keys, last_t = [], -1.0
        for k, key in enumerate(keys, start=1):
            where = "move %s key %d" % (name, k)
            if not isinstance(key, dict) or not isinstance(key.get("pose", {}), dict):
                errors.append("%s must be {\"t\": seconds, \"pose\": {...}}" % where)
                continue
            t = key.get("t")
            if not _num(t) or not 0 <= t <= dur + 1e-9:
                errors.append("%s: t must be 0..%g seconds (the move's duration), got %r" % (where, dur, t))
                continue
            where = "move %s key %d (%.2f s)" % (name, k, t)
            if t <= last_t:
                errors.append("%s: keys must be in time order, each later than the one before" % where)
                continue
            last_t = t
            pose = _pose(key.get("pose") or {}, feet, where, errors)
            clean_keys.append({"t": float(t), "pose": pose})
        if len(errors) == bad:
            out.append({"name": name, "description": str(m.get("description") or ""), "duration_s": float(dur),
                        "loop": bool(loop), "feet": feet, "keys": clean_keys})
    return out


def _pose(pose, feet, where, errors):
    out = {}
    for ch, v in pose.items():
        if ch in JOINTS:
            if not isinstance(v, dict) or not v:
                errors.append("%s: %s takes named motions, e.g. {\"%s\": 30}" % (where, ch, next(iter(JOINTS[ch]))))
                continue
            motions = {}
            for axis, deg in v.items():
                if axis not in JOINTS[ch]:
                    errors.append("%s: %s has no motion %r; it has %s" % (where, ch, axis, ", ".join(JOINTS[ch])))
                    continue
                lo, hi = JOINTS[ch][axis]
                if not _num(deg):
                    errors.append("%s: %s.%s must be a number of degrees, got %r" % (where, ch, axis, deg))
                elif not lo <= deg <= hi:
                    errors.append("%s: %s.%s %g is outside what the joint does (%g..%g degrees)"
                                  % (where, ch, axis, deg, lo, hi))
                else:
                    motions[axis] = float(deg)
            out[ch] = motions
        elif ch in ("fingers_l", "fingers_r"):
            if v not in FINGER_SHAPES:
                errors.append("%s: %s must be %s, got %r" % (where, ch, ", ".join(FINGER_SHAPES), v))
            else:
                out[ch] = v
        elif ch == "pelvis_offset":
            if not (isinstance(v, list) and len(v) == 3 and all(_num(c) for c in v)):
                errors.append("%s: pelvis_offset is [forward, left, up] metres, got %r" % (where, v))
            elif any(not lo <= c <= hi for c, (lo, hi) in zip(v, OFFSET_LIMITS)):
                errors.append("%s: pelvis_offset %r is outside %r metres" % (where, v, OFFSET_LIMITS))
            else:
                out[ch] = [float(c) for c in v]
        elif ch in ("foot_l", "foot_r"):
            if feet != "free":
                errors.append("%s: %s moves a foot, but this move keeps the feet planted; set \"feet\": \"free\" "
                              "if the feet must move (a step), or leave them out" % (where, ch))
            elif not (isinstance(v, list) and len(v) == 4 and all(_num(c) for c in v)):
                errors.append("%s: %s is [forward, left, up, pitch_deg], got %r" % (where, ch, v))
            elif any(not lo <= c <= hi for c, (lo, hi) in zip(v, FOOT_LIMITS)):
                errors.append("%s: %s %r is outside %r" % (where, ch, v, FOOT_LIMITS))
            else:
                out[ch] = [float(c) for c in v]
        elif ch in ("thigh_l", "thigh_r", "calf_l", "calf_r", "ball_l", "ball_r"):
            errors.append("%s: the legs are not posed by angle; lower the body with pelvis_offset (the knees bend "
                          "to keep the feet planted), or move a foot with foot_l/foot_r in a \"feet\": \"free\" move"
                          % where)
        else:
            errors.append("%s: unknown bone or channel %r; bones are %s" % (where, ch, ", ".join(JOINTS)))
    return out


FINGER_NAMES = ("thumb", "index", "middle", "ring", "pinky")
FINGER_CURL = {"open": {}, "fist": {f: 1.0 for f in FINGER_NAMES},
               "point": {f: 1.0 for f in FINGER_NAMES if f != "index"}}


def _flat(pose):
    """A key's pose as plain numbers: "upperarm_r.out" degrees, "fingers_r.index" curl 0..1,
    "pelvis_offset.2" metres. A channel a key does not name is 0: neutral."""
    out = {}
    for ch, v in pose.items():
        if ch in JOINTS:
            for axis, deg in v.items():
                out["%s.%s" % (ch, axis)] = float(deg)
        elif ch.startswith("fingers_"):
            for finger in FINGER_NAMES:
                out["%s.%s" % (ch, finger)] = FINGER_CURL[v].get(finger, 0.0)
        else:
            for i, c in enumerate(v):
                out["%s.%d" % (ch, i)] = float(c)
    return out


def _timeline(move):
    """(t, flat pose) keys: neutral added at 0 s, and at the end unless the move loops."""
    keys = [(k["t"], _flat(k["pose"])) for k in move["keys"]]
    dur = move["duration_s"]
    if not keys or keys[0][0] > 1e-6:
        keys.insert(0, (0.0, {}))
    if not move["loop"] and keys[-1][0] < dur - 1e-6:
        keys.append((dur, {}))
    if move["loop"] and len(keys) > 1 and keys[-1][0] >= dur - 1e-6:
        keys.pop()                       # a loop closes on its first key by itself
    return keys


def _smooth(u):
    return u * u * (3 - 2 * u)


def sample_named(move, t):
    """The move's pose at t seconds as named numbers (see _flat): eased in and out between keys, or for
    a loop a closed Catmull-Rom curve through them. Pure Python."""
    keys = _timeline(move)
    dur = move["duration_s"]
    names = sorted(set().union(*[set(k[1]) for k in keys]))
    times = [k[0] for k in keys]
    n = len(keys)

    def val(i, c):
        return keys[i % n][1].get(c, 0.0)

    if move["loop"]:
        tt = t % dur if dur > 0 else 0.0
        i = max(j for j in range(n) if times[j] <= tt + 1e-9)
        t1 = times[i + 1] if i + 1 < n else dur
        u = 0.0 if t1 <= times[i] else (tt - times[i]) / (t1 - times[i])
        out = {}
        for c in names:
            p0, p1, p2, p3 = val(i - 1, c), val(i, c), val(i + 1, c), val(i + 2, c)
            out[c] = 0.5 * ((2 * p1) + (-p0 + p2) * u + (2 * p0 - 5 * p1 + 4 * p2 - p3) * u * u
                            + (-p0 + 3 * p1 - 3 * p2 + p3) * u ** 3)
        return out
    if t <= times[0]:
        return {c: val(0, c) for c in names}
    if t >= times[-1]:
        return {c: val(n - 1, c) for c in names}
    i = max(j for j in range(n) if times[j] <= t)
    u = _smooth((t - times[i]) / (times[i + 1] - times[i]))
    return {c: val(i, c) + (val(i + 1, c) - val(i, c)) * u for c in names}


def arm_keys(move):
    """The arm bones a move poses (overridden while an item is held in both hands)."""
    return sorted({ch for k in move["keys"] for ch in k["pose"] if ch in ARM_BONES or ch.startswith("fingers_")})


# ---------------------------------------------------------------------------------------------
# Inside Blender

def neutral_axes(arm):
    """Per bone, the axis (armature frame, as the bake's per-bone delta rotation sees it) of each named
    motion, measured on this skeleton in the neutral stance. Inside Blender."""
    import bpy
    from mathutils import Quaternion, Vector
    from . import animations as anims
    pbones = arm.pose.bones
    for pb in pbones:
        pb.rotation_mode = "QUATERNION"
        pb.rotation_quaternion = Quaternion()
        pb.location = Vector()
    for ch, v in anims.BASE.items():
        m = arm.data.bones[ch].matrix_local.to_quaternion()
        pbones[ch].rotation_quaternion = m.inverted() @ Quaternion(anims.euler_to_quat(v)) @ m
    bpy.context.view_layer.update()

    def delta(name):
        return (pbones[name].matrix.to_3x3() @ arm.data.bones[name].matrix_local.to_3x3().inverted()).to_quaternion()

    X, Y, Z = Vector((1, 0, 0)), Vector((0, 1, 0)), Vector((0, 0, 1))
    axes = {}
    for b in ("pelvis", "spine_01", "spine_02", "spine_03", "neck_01", "head"):
        axes[b] = {"forward": Y, "left": -X, "turn_left": Z}
    for s in "lr":
        sign = 1.0 if s == "l" else -1.0
        h = lambda n: pbones["%s_%s" % (n, s)].head.copy()          # noqa: E731
        axes["clavicle_" + s] = {"up": X * sign, "forward": -Z * sign}
        upper = (h("lowerarm") - h("upperarm")).normalized()
        fore = (h("hand") - h("lowerarm")).normalized()
        axes["upperarm_" + s] = {"forward": -Y, "out": X * sign, "twist": upper * -sign}
        hinge = upper.cross(fore)
        if hinge.length < 0.05:                                    # a straight neutral arm: the side axis
            hinge = -Y
        hinge.normalize()
        if hinge.dot(-Y) < 0:                                      # a bend brings the hand forward and up
            hinge = -hinge
        up_d = delta("upperarm_" + s).inverted()
        axes["lowerarm_" + s] = {"bend": up_d @ hinge, "twist": up_d @ (fore * -sign)}
        f = (h("middle_01") - h("hand")).normalized()
        across = h("pinky_01") - h("index_01")
        across = (across - f * across.dot(f)).normalized()
        palm = f.cross(across) if s == "r" else -f.cross(across)
        lo_d = delta("lowerarm_" + s).inverted()
        axes["hand_" + s] = {"bend": lo_d @ f.cross(palm).normalized(), "side": lo_d @ f.cross(-across).normalized()}
    for pb in pbones:
        pb.rotation_quaternion = Quaternion()
    bpy.context.view_layer.update()
    return axes


def to_anim(move, axes, finger_curls, fps=30):
    """A validated move as animations.bake() data: one key per frame, each the move's named motions at
    that time (sample_named) turned into rotations (character-frame XYZ Euler degrees laid over BASE,
    like the built-in clips). Interpolating the named motions, not the composed rotations, keeps an arm
    on an anatomical path: a salute's hand rises up the side to the brow instead of swinging out in
    front and coming down like a wave (what a straight slerp between neutral and the salute did).
    ``finger_curls``: {"fist": {bone: Quaternion}} (finger_curls(arm))."""
    from mathutils import Quaternion
    from . import animations as anims
    curl = finger_curls.get("fist", {})

    def bone_euler(bone, motions):
        q = Quaternion(anims.euler_to_quat(anims.BASE.get(bone, [0.0, 0.0, 0.0])))
        for axis, deg in sorted(motions.items(), key=lambda kv: ORDER[kv[0]]):
            q = Quaternion(axes[bone][axis], math.radians(deg)) @ q
        return [math.degrees(a) for a in q.to_euler("XYZ")]

    dur = move["duration_s"]
    n = int(round(dur * fps))
    keys = []
    for f in range(n + 1):
        named = sample_named(move, f / fps)
        bones, lists, fingers = {}, {}, {}
        for ch, v in named.items():
            head, part = ch.split(".", 1)
            if head in JOINTS:
                bones.setdefault(head, {})[part] = v
            elif head.startswith("fingers_"):
                fingers.setdefault(head[-1], {})[part] = v
            else:
                lists.setdefault(head, {})[int(part)] = v
        pose = {b: bone_euler(b, m) for b, m in bones.items()}
        for side, amounts in fingers.items():
            for bone, q in curl.items():
                if bone.endswith("_" + side):
                    amt = min(max(amounts.get(bone.split("_")[0], 0.0), 0.0), 1.0)
                    pose[bone] = [math.degrees(a) for a in Quaternion().slerp(q, amt).to_euler("XYZ")]
        for ch, parts in lists.items():
            pose[ch] = [parts.get(i, 0.0) for i in range(4 if ch.startswith("foot_") else 3)]
        keys.append((f / fps, pose))
    return {"interp": "smooth", "loop": move["loop"], "duration": dur, "keys": keys,
            "planted": move["feet"] == "planted", "custom": True}


def finger_curls(arm):
    """Finger rotations (bake convention: armature-frame delta) for a fist and for pointing."""
    from . import two_hands
    curl = two_hands._curl_quats(arm)          # local (m^-1 d m); turn back into the delta d
    out = {"fist": {}, "point": {}}
    for name, q in curl.items():
        m = arm.data.bones[name].matrix_local.to_quaternion()
        d = m @ q @ m.inverted()
        out["fist"][name] = d
        if not name.startswith("index_"):
            out["point"][name] = d
    return out


class TorsoCheck:
    """Arms and hands through the torso, frame by frame (inside Blender). The torso is the rest body's
    horizontal cross-sections (spine and pelvis surface, vest included), each carried by the spine bone
    it belongs to, so a bent or turned body is checked as posed. A point counts as inside when it is
    within the section's ellipse shrunk to 85% (the soft edge, as the GLB tests do)."""

    SEGMENTS = ("pelvis", "spine_01", "spine_02", "spine_03")
    TAGS = {"pelvis", "spine_01", "spine_02", "spine_03", "life_vest"}
    SHRINK = 0.85
    LIMBS = [("upper arm", "upperarm", "lowerarm", 0.35), ("forearm", "lowerarm", "hand", 0.0),
             ("hand", "hand", "middle_01", 0.0)]

    def __init__(self, arm, surface):
        bones = arm.data.bones
        pts = [p for p, g in surface.pts if g in self.TAGS]
        self.sections = {}
        if pts:
            lo, hi = int(math.floor(min(p.z for p in pts) / 0.02)), int(math.floor(max(p.z for p in pts) / 0.02))
            for k in range(lo, hi + 1):
                zc = (k + 0.5) * 0.02
                band = [p for p in pts if abs(p.z - zc) < 0.02]
                if len(band) >= 12:
                    xs, ys = [p.x for p in band], [p.y for p in band]
                    self.sections[k] = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2,
                                        (max(xs) - min(xs)) / 2 * self.SHRINK, (max(ys) - min(ys)) / 2 * self.SHRINK)
        tops = {"pelvis": bones["spine_01"].head_local.z, "spine_01": bones["spine_02"].head_local.z,
                "spine_02": bones["spine_03"].head_local.z,
                "spine_03": min(bones["clavicle_l"].head_local.z, bones["neck_01"].head_local.z)}
        self.ranges = {"pelvis": (bones["thigh_l"].head_local.z, tops["pelvis"])}
        for a, b in zip(self.SEGMENTS[1:], self.SEGMENTS[:-1]):
            self.ranges[a] = (tops[b], tops[a])
        self.rest = {b: bones[b].matrix_local.copy() for b in self.SEGMENTS}

    def _inside(self, p):
        sec = self.sections.get(int(math.floor(p.z / 0.02)))
        if sec is None:
            return False
        cx, cy, rx, ry = sec
        return rx > 0 and ry > 0 and ((p.x - cx) / rx) ** 2 + ((p.y - cy) / ry) ** 2 < 1.0

    def hits(self, arm):
        """Names of the limbs ("right forearm") with a sampled point inside the posed torso."""
        pb = arm.pose.bones
        back = {b: self.rest[b] @ pb[b].matrix.inverted() for b in self.SEGMENTS}
        out = []
        for s, side in (("r", "right"), ("l", "left")):
            for label, a, b, t0 in self.LIMBS:
                pa, pbt = pb["%s_%s" % (a, s)].head, pb["%s_%s" % (b, s)].head
                for k in range(6):
                    t = t0 + (1 - t0) * k / 5
                    p = pa.lerp(pbt, t)
                    if any(self.ranges[seg][0] <= (back[seg] @ p).z < self.ranges[seg][1]
                           and self._inside(back[seg] @ p) for seg in self.SEGMENTS):
                        out.append("%s %s" % (side, label))
                        break
        return out


def describe_hits(name, frames, fps):
    """{limb: [frame, ...]} -> build errors in words."""
    out = []
    for limb, fs in sorted(frames.items()):
        a, b = min(fs) / fps, max(fs) / fps
        when = "at %.1f s" % a if b - a < 0.05 else "from %.1f s to %.1f s" % (a, b)
        out.append("move %s: the %s passes through the body %s; move it further out to the side or forward "
                   "of the chest (upperarm out/forward, lowerarm bend) at the keys around then" % (name, limb, when))
    return out
