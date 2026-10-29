"""Moves made from words (contract section 6, "Custom moves"; blender/crew/moves.py).

    server/.venv/bin/python -m pytest tests/blender/test_crew_moves.py -q

Lester's plan, "Animation editor": prompted moves built as key poses on the shared skeleton, with checks
that the feet stay planted and the limbs stay out of the body. A scripted salute and a scripted "point
forward" are baked and measured in the GLB; a deliberately broken move must fail the build, naming the
limb and the time.
"""

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_crew import _gltf, _pose, _skin_data, _skinned, _world_positions, needs_blender  # noqa: E402
from test_crew_two_hands import HAND_TOL_M, ROOT, _build, _fixture_build, _palm, _spec, _t  # noqa: E402
from test_crew_aim import _rifle  # noqa: E402

sys.path.insert(0, str(ROOT / "blender"))
from crew import moves as crew_moves  # noqa: E402
from crew import spec as crew_spec  # noqa: E402

SALUTE_POSE = {"upperarm_r": {"twist": 60, "out": 90, "forward": 40}, "lowerarm_r": {"bend": 110, "twist": 60},
               "head": {"forward": -3}}
POINT_POSE = {"upperarm_r": {"forward": 90}, "lowerarm_r": {"bend": -20}, "fingers_r": "point"}
# On the way up and down the elbow bends first, so the hand rises close in front of the body.
VIA_POSE = {"upperarm_r": {"twist": 20, "out": 35, "forward": 35}, "lowerarm_r": {"bend": 125, "twist": 30}}
SALUTE = {"name": "salute", "description": "Right-hand salute", "duration_s": 2.4, "loop": False,
          "keys": [{"t": 0.3, "pose": VIA_POSE}, {"t": 0.6, "pose": SALUTE_POSE}, {"t": 1.6, "pose": SALUTE_POSE},
                   {"t": 1.95, "pose": VIA_POSE}]}
POINT = {"name": "point_forward", "description": "Point straight ahead with the right arm", "duration_s": 2.4,
         "loop": False, "keys": [{"t": 0.7, "pose": POINT_POSE}, {"t": 1.7, "pose": POINT_POSE}]}
# Deliberately broken: the right upper arm swung in across the body and the elbow bent into the chest.
BROKEN = {"name": "hug_self", "description": "broken on purpose", "duration_s": 2.0, "loop": False,
          "keys": [{"t": 1.0, "pose": {"upperarm_r": {"out": -30, "forward": 35, "twist": -60},
                                       "lowerarm_r": {"bend": 120}}}]}
HOLD = 34          # a frame inside both holds (1.1 s)


def _moves_spec(moves, place=None, anims=("idle",)):
    spec = _spec(place, anims=list(anims))
    spec["custom_moves"] = moves
    return spec


@pytest.fixture(scope="module")
def moves_build(tmp_path_factory):
    return _fixture_build(tmp_path_factory, "moves", _moves_spec([SALUTE, POINT]))


@pytest.fixture(scope="module")
def rifle_moves_build(tmp_path_factory):
    """The salute on someone carrying the rifle in both hands: the arms must stay on the rifle."""
    return _fixture_build(tmp_path_factory, "rifle_moves", _moves_spec([SALUTE], "both_hands"))


def _w(g, clip, frame):
    return _world_positions(g, _pose(g, clip, frame)[0])


def _right_brow(g, clip, frame):
    """The right brow: the right eye's centre (skinned eye mesh), 4.5 cm up and 2.5 cm out."""
    ov, _ = _pose(g, clip, frame)
    pos, _ = _skin_data(g, "Head__Eyes")
    pts = _skinned(g, "Head__Eyes", ov, [i for i, p in enumerate(pos) if p[2] > 0])     # +Z: the right
    eye = [sum(p[i] for p in pts) / len(pts) for i in range(3)]
    return [eye[0], eye[1] + 0.045, eye[2] + 0.025]


# ---------------------------------------------------------------------------------------------
# Pure logic

def test_moves_are_validated_with_errors_in_words():
    errors = []
    assert crew_moves.validate([SALUTE, POINT], errors) and errors == []
    cases = [
        (dict(SALUTE, keys=[{"t": 0.5, "pose": {"upperarm_r": {"forward": 200}}}]), "outside what the joint does"),
        (dict(SALUTE, keys=[{"t": 0.5, "pose": {"lowerarm_r": {"curl": 20}}}]), "has no motion 'curl'"),
        (dict(SALUTE, keys=[{"t": 3.0, "pose": {}}]), "t must be 0..2.4"),
        (dict(SALUTE, keys=[{"t": 0.5, "pose": {"foot_r": [0.2, 0, 0, 0]}}]), "keeps the feet planted"),
        (dict(SALUTE, keys=[{"t": 0.5, "pose": {"calf_l": {"bend": 40}}}]), "pelvis_offset"),
        (dict(SALUTE, name="Salute!"), "lower-case"),
        (dict(SALUTE, name="squat"), "built-in"),
        (dict(SALUTE, duration_s=40), "duration_s"),
        (dict(SALUTE, keys=[{"t": 0.5, "pose": {"fingers_r": "claw"}}]), "open, fist, point"),
    ]
    for move, needle in cases:
        errors = []
        crew_moves.validate([move], errors)
        assert any(needle in e for e in errors), (needle, errors)
    errors = []
    crew_moves.validate([SALUTE, SALUTE], errors)
    assert any("used twice" in e for e in errors)
    free = dict(SALUTE, feet="free", keys=[{"t": 0.5, "pose": {"foot_r": [0.2, 0, 0.05, 0]}}])
    errors = []
    assert crew_moves.validate([free], errors) and errors == []


def test_named_motions_ease_between_keys_from_and_back_to_neutral():
    """Interpolation is per named motion: halfway between neutral and the salute key, every motion is
    halfway (smoothstep); the hold holds; it ends at neutral. A loop closes on itself."""
    one = dict(SALUTE, keys=[{"t": 0.6, "pose": SALUTE_POSE}, {"t": 1.6, "pose": SALUTE_POSE}])
    at = lambda t: crew_moves.sample_named(one, t)                          # noqa: E731
    assert all(v == 0 for v in at(0.0).values()) and all(v == 0 for v in at(2.4).values())
    assert at(0.3)["upperarm_r.out"] == pytest.approx(45.0) and at(0.3)["lowerarm_r.bend"] == pytest.approx(55.0)
    assert at(0.15)["upperarm_r.out"] < 45.0 * 0.5                          # eased in
    assert at(1.1)["upperarm_r.twist"] == pytest.approx(60.0)               # held
    point = crew_moves.sample_named(POINT, 1.2)
    assert point["fingers_r.index"] == 0.0 and point["fingers_r.middle"] == 1.0
    wave = {"name": "wave", "duration_s": 1.0, "loop": True, "feet": "planted",
            "keys": [{"t": 0.0, "pose": {"lowerarm_r": {"bend": 60}}}, {"t": 0.5, "pose": {"lowerarm_r": {"bend": 100}}}]}
    a, b = crew_moves.sample_named(wave, 0.0), crew_moves.sample_named(wave, 1.0)
    assert a == pytest.approx(b) and crew_moves.sample_named(wave, 0.5)["lowerarm_r.bend"] == pytest.approx(100.0)


def test_the_spec_carries_custom_moves():
    spec, errors, _ = crew_spec.normalize({"custom_moves": [SALUTE]})
    assert errors == [] and spec["custom_moves"][0]["name"] == "salute"
    assert crew_spec.DEFAULT_SPEC["custom_moves"] == []
    _, errors, _ = crew_spec.normalize({"custom_moves": [dict(SALUTE, duration_s=0)]})
    assert any("duration_s" in e for e in errors)


# ---------------------------------------------------------------------------------------------
# The scripted moves, baked

@needs_blender
def test_moves_are_clips_in_the_glb(moves_build):
    r = moves_build
    assert r["ok"] is True, r["errors"]
    g = _gltf(r)
    assert sorted(a.name for a in g.animations) == ["idle", "point_forward", "salute"]
    for clip in ("salute", "point_forward"):
        assert _pose(g, clip, 0)[1] == 73                                  # 2.4 s at 30 fps
    assert r["custom_moves"] == ["salute", "point_forward"]


@needs_blender
def test_salute_puts_the_fingertips_at_the_right_brow(moves_build):
    """Held: the right middle fingertip within 5 cm of the right brow, the elbow out to the side near
    shoulder height. (Control: in the neutral first frame it is 70+ cm away.)"""
    g = _gltf(moves_build)
    w = _w(g, "salute", HOLD)
    d = math.dist(_fingertip(w), _right_brow(g, "salute", HOLD))
    assert d < 0.05, d
    shoulder, elbow = _t(w["upperarm_r"]), _t(w["lowerarm_r"])
    assert abs(elbow[1] - shoulder[1]) < 0.12 and elbow[2] - shoulder[2] > 0.15        # out to the right
    assert math.dist(_fingertip(_w(g, "salute", 0)), _right_brow(g, "salute", 0)) > 0.7


def _fingertip(w, s="r"):
    """The middle fingertip: the last joint carried on by about the last bone's length (the GLB has joints,
    not tips)."""
    a, b = _t(w["middle_02_" + s]), _t(w["middle_03_" + s])
    return [b[i] + 0.9 * (b[i] - a[i]) for i in range(3)]


@needs_blender
def test_point_forward_points_ahead_with_the_index(moves_build):
    """Held: the right hand straight ahead of the shoulder at shoulder height (within 15 degrees), the index
    straight and the other fingers curled. (Control: the left hand still hangs.)"""
    g = _gltf(moves_build)
    w = _w(g, "point_forward", HOLD)
    shoulder, hand = _t(w["upperarm_r"]), _t(w["hand_r"])
    d = [b - a for a, b in zip(shoulder, hand)]
    assert d[0] > 0.4
    assert math.degrees(math.atan2(math.hypot(d[1], d[2]), d[0])) < 15, d
    wrist = _t(w["hand_r"])
    reach = {f: math.dist(_t(w["%s_03_r" % f]), wrist) for f in ("index", "middle", "ring")}
    assert reach["index"] > reach["middle"] + 0.02 and reach["index"] > reach["ring"] + 0.02, reach
    assert _t(w["hand_l"])[1] < _t(w["upperarm_l"])[1] - 0.4               # the left arm hangs


@needs_blender
@pytest.mark.parametrize("clip", ["salute", "point_forward"])
def test_moves_start_and_end_standing_with_feet_planted(moves_build, clip):
    g = _gltf(moves_build)
    first, last = _w(g, clip, 0), _w(g, clip, 72)
    for b in ("hand_r", "hand_l", "head"):
        assert math.dist(_t(first[b]), _t(last[b])) < 0.01, b
    for s in "lr":
        pts = [_t(_w(g, clip, f)["foot_" + s]) for f in range(0, 73, 6)]
        assert max(math.dist(p, pts[0]) for p in pts) < 0.01, (clip, s)
    assert moves_build["feet"][clip]["foot_l_x_range"] < 0.01


# ---------------------------------------------------------------------------------------------
# The checks

@needs_blender
def test_a_broken_move_fails_the_build_naming_the_limb_and_time(tmp_path):
    r = _build(tmp_path, _moves_spec([BROKEN]))
    assert r["ok"] is False and r["_exit"] == 1
    hits = [e for e in r["errors"] if e.startswith("move hug_self:") and "passes through the body" in e]
    assert any("right forearm" in e for e in hits), r["errors"]
    assert all(" s" in e for e in hits)                                    # says when


@needs_blender
def test_a_joint_past_its_limit_fails_before_anything_is_built(tmp_path):
    bad = dict(SALUTE, keys=[{"t": 0.5, "pose": {"lowerarm_r": {"bend": 175}}}])
    r = _build(tmp_path, _moves_spec([bad]))
    assert r["ok"] is False and any("lowerarm_r.bend 175 is outside" in e for e in r["errors"]), r["errors"]


@needs_blender
def test_with_a_rifle_in_both_hands_the_arms_stay_on_it(rifle_moves_build):
    r = rifle_moves_build
    assert r["ok"] is True, r["errors"]
    assert any("move salute" in w and "both hands" in w for w in r["warnings"]), r["warnings"]
    g = _gltf(r)
    ov, _ = _pose(g, "salute", HOLD)
    rifle = _rifle(g, r, ov)
    assert math.dist(_palm(rifle["w"], "r"), rifle["grip"]) < HAND_TOL_M
    assert math.dist(_palm(rifle["w"], "l"), rifle["support"]) < HAND_TOL_M
