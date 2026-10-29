"""Aim stance and raise_to_aim (contract section 6, "Stance").

    server/.venv/bin/python -m pytest tests/blender/test_crew_aim.py -q

Lester, 29 Oct, in the animation editor: "raise the rifle up to shooting position" -- and the AI had
to say it could not: there was no aiming stance and no move that raises the rifle. Everything here is
measured from the GLB (what Unreal and the web viewer get), clip by clip, frame by frame.

Every geometric check also runs against the READY carry, where it must fail: that is what shows
each check can fail, and that it is the aim that passes it rather than something every build does.
"""

import json
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_crew import (_accessor_values, _gltf, _node, _pose, _skin_data, _skinned, _world_positions,  # noqa: E402
                       needs_blender)
from test_crew_two_hands import (HAND_TOL_M, ROOT, _clips, _fixture_build, _frame_index, _palm, _part,  # noqa: E402
                                 _spec, _t, _apply)

sys.path.insert(0, str(ROOT / "blender"))
from crew import spec as crew_spec  # noqa: E402

RIFLE = "Equipment__Rifle"
AIM_ANIMS = ["idle", "walk", "brace", "hands_up", "squat", "raise_to_aim"]
LEVEL_DEG = 10          # muzzle within this of horizontal
HEADING_DEG = 15        # and pointing within this of the way the body faces
SHOULDER_M = 0.06       # stock end within this of the right shoulder pocket
AIM_FRAMES = [("idle", 0), ("idle", "mid"), ("walk", "quarter"), ("walk", "mid"), ("brace", "mid"),
              ("squat", "mid"), ("raise_to_aim", "last")]


def _aim_spec(stance="aim", anims=AIM_ANIMS):
    spec = _spec("both_hands", anims=list(anims))
    if stance is not None:
        spec["custom_items"][0]["stance"] = stance
    return spec


@pytest.fixture(scope="module")
def aim_build(tmp_path_factory):
    """The default male carrying the described rifle in both hands, stance aim, with raise_to_aim."""
    return _fixture_build(tmp_path_factory, "aim", _aim_spec())


@pytest.fixture(scope="module")
def ready_build(tmp_path_factory):
    """The same rifle in the ready carry (no stance given): the control every aim check must fail."""
    return _fixture_build(tmp_path_factory, "ready", _aim_spec(None, AIM_ANIMS[:5]))


@pytest.fixture(scope="module")
def ready_explicit_build(tmp_path_factory):
    return _fixture_build(tmp_path_factory, "ready_explicit", _aim_spec("ready", AIM_ANIMS[:5]))


@pytest.fixture(scope="module")
def ready_raise_build(tmp_path_factory):
    """Ready stance, plus the raise_to_aim move: carried low in every clip, raised in that one."""
    return _fixture_build(tmp_path_factory, "ready_raise", _aim_spec("ready"))


# ---------------------------------------------------------------------------------------------
# Measuring the rifle in the GLB (glTF frame: Y up, +X forward, +Z = the person's right)

def _to_gltf(v):
    return [v[0], v[2], -v[1]]


def _rifle(g, report, ov):
    """World grip, support (the one the left hand holds in this stance), stock end and the posed
    joints. The stock end is the centre of the rifle's back-most vertices along grip -> support."""
    part = _part(report, RIFLE)
    w = _world_positions(g, ov)
    m = w[RIFLE]
    grip = _apply(m, _to_gltf(part["grip_m"]))
    support = _apply(m, _to_gltf(part.get("aim_support_m") or part["support_m"]))
    far = _apply(m, _to_gltf(part["support_m"]))
    axis = [b - a for a, b in zip(grip, far)]
    n = math.sqrt(sum(c * c for c in axis))
    axis = [c / n for c in axis]
    node = g.nodes[_node(g, RIFLE)]
    verts = [_apply(m, p) for prim in g.meshes[node.mesh].primitives for p in _accessor_values(g, prim.attributes.POSITION)]
    s = [sum((p[i] - grip[i]) * axis[i] for i in range(3)) for p in verts]
    back = [p for p, x in zip(verts, s) if x < min(s) + 0.01]
    stock = [sum(p[i] for p in back) / len(back) for i in range(3)]
    return {"grip": grip, "support": support, "far_support": far, "axis": axis, "stock": stock, "w": w}


def _to_segment(p, a, b):
    ab = [y - x for x, y in zip(a, b)]
    t = sum((p[i] - a[i]) * ab[i] for i in range(3)) / (sum(c * c for c in ab) or 1.0)
    t = min(max(t, 0.0), 1.0)
    return math.dist(p, [a[i] + t * ab[i] for i in range(3)])


def _facing(w):
    """The way the body faces, from the hips: left thigh to right thigh turned a quarter to the front,
    level (glTF +X forward, +Z the person's right)."""
    a, b = _t(w["thigh_l"]), _t(w["thigh_r"])
    across = [b[0] - a[0], 0.0, b[2] - a[2]]          # toward the person's right (+Z)
    f = [across[2], 0.0, -across[0]]                   # +Z -> +X
    n = math.hypot(f[0], f[2]) or 1.0
    return [f[0] / n, 0.0, f[2] / n]


def _chest_forward(g, w):
    """The way the chest faces: spine_03's turn from its rest pose applied to the rest forward (+X)."""
    rest = _world_positions(g)["spine_03"]
    m = w["spine_03"]
    # rotation part: posed @ rest^-1 (rest is orthonormal up to the armature's uniform scale)
    r = [[sum(m[i][k] * rest[j][k] for k in range(3)) for j in range(3)] for i in range(3)]
    sc = sum(rest[k][0] ** 2 for k in range(3))
    return [r[i][0] / sc for i in range(3)]


def _muzzle(r):
    """(degrees above/below horizontal, degrees off the body's facing) of the rifle's long axis."""
    d = r["axis"]
    elev = math.degrees(math.atan2(d[1], math.hypot(d[0], d[2])))
    f = _facing(r["w"])
    heading = math.degrees(math.acos(max(-1.0, min(1.0, (d[0] * f[0] + d[2] * f[2]) / (math.hypot(d[0], d[2]) or 1)))))
    return elev, heading


def _shoulder_pocket(g, w):
    """The right shoulder pocket, defined from the skeleton alone: halfway along the right collarbone
    from its inner end (clavicle_r) to the shoulder joint (upperarm_r), 8 cm in front of that line
    (the chest's depth there) the way the chest faces."""
    c, u = _t(w["clavicle_r"]), _t(w["upperarm_r"])
    f = _chest_forward(g, w)
    return [c[i] + 0.5 * (u[i] - c[i]) + 0.08 * f[i] for i in range(3)]


def _at(g, report, clip, which):
    ov, _ = _pose(g, clip, _frame_index(g, clip, which))
    out = _rifle(g, report, ov)
    out["pocket"] = _shoulder_pocket(g, out["w"])
    return out


# ---------------------------------------------------------------------------------------------
# Pure logic

def test_stance_is_validated_and_owned_by_the_two_handed_item():
    rifle = {"id": "item_1", "module": "equipment", "place": "both_hands", "code": "x = 1"}
    spec, errors, _ = crew_spec.normalize({"custom_items": [dict(rifle, stance="aim")]})
    assert errors == [] and spec["custom_items"][0]["stance"] == "aim"
    spec, errors, _ = crew_spec.normalize({"custom_items": [rifle]})
    assert spec["custom_items"][0]["stance"] == "ready"                 # the default
    _, errors, _ = crew_spec.normalize({"custom_items": [dict(rifle, stance="hip_fire")]})
    assert any("stance must be one of" in e for e in errors), errors
    spec, _, warnings = crew_spec.normalize({"custom_items": [dict(rifle, place="right_hand", stance="aim")]})
    assert spec["custom_items"][0]["stance"] is None and any("stance" in w for w in warnings)


def test_raise_to_aim_needs_something_in_both_hands():
    spec, errors, warnings = crew_spec.normalize({"animations": ["idle", "raise_to_aim"]})
    assert errors == [] and spec["animations"] == ["idle"] and any("raise_to_aim" in w for w in warnings)
    rifle = {"id": "item_1", "module": "equipment", "place": "both_hands", "code": "x = 1"}
    spec, _, _ = crew_spec.normalize({"animations": ["idle", "raise_to_aim"], "custom_items": [rifle]})
    assert spec["animations"] == ["idle", "raise_to_aim"]
    assert "raise_to_aim" not in crew_spec.DEFAULT_SPEC["animations"]   # only on request


# ---------------------------------------------------------------------------------------------
# The aim

@needs_blender
def test_aim_build_ok(aim_build):
    r = aim_build
    assert r["ok"] is True and r["_exit"] == 0, r["errors"]
    assert r["animations"] == AIM_ANIMS
    part = _part(r, RIFLE)
    assert part["bone"] == "hand_r" and part["stance"] == "aim" and part["place"] == "both_hands"


@needs_blender
@pytest.mark.parametrize("clip,which", AIM_FRAMES)
def test_muzzle_level_and_forward(aim_build, ready_build, clip, which):
    """In the aim the rifle's long axis is within 10 degrees of horizontal and points within 15 degrees
    of the way the body faces, in every aimed clip (brace and squat lean the body; the rifle stays level)."""
    elev, heading = _muzzle(_at(_gltf(aim_build), aim_build, clip, which))
    assert abs(elev) <= LEVEL_DEG and heading <= HEADING_DEG, (clip, which, elev, heading)
    if clip == "idle" and which == 0:
        elev, heading = _muzzle(_at(_gltf(ready_build), ready_build, "idle", 0))
        assert abs(elev) > LEVEL_DEG, ("the ready carry passes the aim check", elev)


@needs_blender
@pytest.mark.parametrize("clip,which", AIM_FRAMES)
def test_stock_in_the_right_shoulder(aim_build, ready_build, clip, which):
    """The stock end is within 6 cm of the right shoulder pocket (defined from clavicle_r and upperarm_r)."""
    r = _at(_gltf(aim_build), aim_build, clip, which)
    d = math.dist(r["stock"], r["pocket"])
    assert d < SHOULDER_M, (clip, which, d)
    if clip == "idle" and which == 0:
        ready = _at(_gltf(ready_build), ready_build, "idle", 0)
        assert math.dist(ready["stock"], ready["pocket"]) > 0.15


def _eye_to_bore(g, report, clip, which):
    """(height above, distance beside) the bore line of the right eye's centre (skinned eye mesh)."""
    ov, _ = _pose(g, clip, _frame_index(g, clip, which))
    r = _rifle(g, report, ov)
    pos, _ = _skin_data(g, "Head__Eyes")
    pts = _skinned(g, "Head__Eyes", ov, [i for i, p in enumerate(pos) if p[2] > 0])   # +Z: the right eye
    eye = [sum(p[i] for p in pts) / len(pts) for i in range(3)]
    e = [eye[i] - r["stock"][i] for i in range(3)]
    t = sum(e[i] * r["axis"][i] for i in range(3))
    perp = [e[i] - r["axis"][i] * t for i in range(3)]
    return perp[1], math.hypot(perp[0], perp[2])


@needs_blender
@pytest.mark.parametrize("clip,which", [("idle", 0), ("walk", "mid"), ("brace", "mid"), ("squat", "mid")])
def test_eye_behind_the_sights(aim_build, ready_build, clip, which):
    """Cheek on the stock: the right eye 4-12 cm above the bore line and within 5 cm of beside it."""
    up, side = _eye_to_bore(_gltf(aim_build), aim_build, clip, which)
    assert 0.04 <= up <= 0.12 and side <= 0.05, (clip, which, up, side)
    if clip == "idle":
        up, side = _eye_to_bore(_gltf(ready_build), ready_build, "idle", 0)
        assert not (0.04 <= up <= 0.12 and side <= 0.05), ("the ready carry passes", up, side)


HOLD_FRAMES = AIM_FRAMES + [("hands_up", 0), ("hands_up", "mid"), ("hands_up", "last"), ("raise_to_aim", 0),
                            ("raise_to_aim", "quarter"), ("raise_to_aim", "mid")]


@needs_blender
@pytest.mark.parametrize("clip,which", HOLD_FRAMES)
def test_palms_on_the_grips(aim_build, clip, which):
    """Right palm on the grip, left palm on the handguard, in every frame checked, hands_up and the
    raise included: the rifle rides the right hand, so the hands never let go. Aimed, the left hand
    holds at aim_support_m; carried low it holds at support_m; during the raise it slides between
    them, so it is checked against that stretch of the item, and at the hold frames against the
    point itself."""
    g = _gltf(aim_build)
    r = _at(g, aim_build, clip, which)
    d = math.dist(_palm(r["w"], "r"), r["grip"])
    assert d < HAND_TOL_M, (clip, which, "r", d)
    d = _to_segment(_palm(r["w"], "l"), r["support"], r["far_support"])
    assert d < HAND_TOL_M, (clip, which, "l", d)
    if (clip, which) in AIM_FRAMES:
        d = math.dist(_palm(r["w"], "l"), r["support"])
        assert d < HAND_TOL_M, (clip, which, "l at aim_support_m", d)
    if (clip, which) == ("raise_to_aim", 0):
        d = math.dist(_palm(r["w"], "l"), r["far_support"])
        assert d < HAND_TOL_M, (clip, which, "l at support_m (the ready hold)", d)


@needs_blender
def test_the_left_hand_is_on_the_handguard_not_the_grip(aim_build):
    part = _part(aim_build, RIFLE)
    gap = math.dist(part["grip_m"], part["aim_support_m"])
    assert 0.12 <= gap <= math.dist(part["grip_m"], part["support_m"]) + 1e-6, gap


@needs_blender
def test_hands_up_lifts_the_rifle_overhead(aim_build):
    """Decision (contract section 6): an aimed rifle rides the right hand, so in hands_up it cannot stay
    slung; both hands lift it level over the head and bring it back to the aim."""
    g = _gltf(aim_build)
    r = _at(g, aim_build, "hands_up", "mid")
    head_top = _t(r["w"]["head"])[1] + 0.1
    assert min(r["grip"][1], r["support"][1]) > head_top
    assert abs(r["axis"][1]) < math.sin(math.radians(LEVEL_DEG))
    end = _at(g, aim_build, "hands_up", "last")
    start = _at(g, aim_build, "idle", 0)
    assert math.dist(end["grip"], start["grip"]) < 0.05


# ---------------------------------------------------------------------------------------------
# raise_to_aim

@needs_blender
def test_raise_starts_at_the_ready_carry_and_ends_at_the_aim(aim_build, ready_build):
    """First frame: the rifle where the ready carry has it (the ready build's idle); last frame: where
    the aim has it (this build's idle). And it really travels: the two are far apart."""
    g, gr = _gltf(aim_build), _gltf(ready_build)
    first = _at(g, aim_build, "raise_to_aim", 0)
    last = _at(g, aim_build, "raise_to_aim", "last")
    ready = _at(gr, ready_build, "idle", 0)
    aimed = _at(g, aim_build, "idle", 0)
    ready["support"] = _apply(ready["w"][RIFLE], _to_gltf(_part(ready_build, RIFLE)["support_m"]))
    for key in ("grip", "stock"):
        assert math.dist(first[key], ready[key]) < 0.03, (key, math.dist(first[key], ready[key]))
        assert math.dist(last[key], aimed[key]) < 0.03, (key, math.dist(last[key], aimed[key]))
    assert math.degrees(math.acos(min(1.0, sum(a * b for a, b in zip(first["axis"], ready["axis"]))))) < 3
    assert math.degrees(math.acos(min(1.0, sum(a * b for a, b in zip(last["axis"], aimed["axis"]))))) < 3
    assert math.dist(first["stock"], last["stock"]) > 0.25


@needs_blender
def test_raise_takes_about_a_second_then_holds(aim_build):
    g = _gltf(aim_build)
    n = _pose(g, "raise_to_aim", 0)[1]
    assert abs(n - 76) <= 1                                             # 2.5 s at 30 fps, no loop
    track = [_rifle(g, aim_build, _pose(g, "raise_to_aim", f)[0])["stock"] for f in range(n)]
    end = track[-1]
    moved = [math.dist(p, end) for p in track]
    settled = next(f for f, d in enumerate(moved) if d < 0.01)
    assert 25 <= settled <= 36, settled                                 # about 1 s (30 frames)
    assert max(moved[36:]) < 0.01                                       # then held
    step = [math.dist(a, b) for a, b in zip(track, track[1:31])]
    assert step[0] < step[15] and step[-1] < step[15]                   # eased in and out


@needs_blender
def test_ready_stance_with_raise_carries_low_and_raises(ready_raise_build, ready_build):
    """Stance ready plus the raise move: idle is the ready carry, the raise still ends in the aim."""
    r = ready_raise_build
    assert r["ok"] is True, r["errors"]
    g = _gltf(r)
    idle = _at(g, r, "idle", 0)
    ready = _at(_gltf(ready_build), ready_build, "idle", 0)
    assert math.dist(idle["grip"], ready["grip"]) < 0.03
    elev, heading = _muzzle(_at(g, r, "raise_to_aim", "last"))
    assert abs(elev) <= LEVEL_DEG and heading <= HEADING_DEG


# ---------------------------------------------------------------------------------------------
# Feet, and nothing else changed

@needs_blender
@pytest.mark.parametrize("clip", ["idle", "brace", "squat", "hands_up", "raise_to_aim"])
def test_feet_planted(aim_build, clip):
    """Both ankles stay within 1 cm of where they start, in every third frame of the planted clips.
    (Control: in walk, which steps, the same measure exceeds 5 cm.)"""
    g = _gltf(aim_build)

    def travel(c, s):
        n = _pose(g, c, 0)[1]
        pts = [_t(_world_positions(g, _pose(g, c, f)[0])["foot_" + s]) for f in range(0, n, 3)]
        return max(math.dist(p, pts[0]) for p in pts)

    for s in "lr":
        assert travel(clip, s) < 0.01, (clip, s, travel(clip, s))
    assert travel("walk", "l") > 0.05


@needs_blender
def test_ready_stance_is_the_carry_it_always_was(ready_build, ready_explicit_build):
    """No stance and stance ready are the same build, byte for byte, with the rifle still riding spine_03
    (the carry and its hands_up release are untouched by the aim work)."""
    a, b = _clips(_gltf(ready_build)), _clips(_gltf(ready_explicit_build))
    assert a == b
    for r in (ready_build, ready_explicit_build):
        part = _part(r, RIFLE)
        assert part["bone"] == "spine_03" and "stance" not in part


@needs_blender
def test_aim_changes_the_clips(aim_build, ready_build):
    """The control for the test above: the aim does change the arm and head keys."""
    a, b = _clips(_gltf(aim_build)), _clips(_gltf(ready_build))
    diff = {k[0] for k in a["idle"] if a["idle"][k] != b["idle"].get(k)}
    assert {"upperarm_r", "lowerarm_l", "head", "neck_01"} <= diff
    legs = {"%s_%s" % (bn, s) for s in "lr" for bn in ("thigh", "calf", "foot", "ball")}
    assert not diff & legs, sorted(diff & legs)                         # the legs are the clip's own
