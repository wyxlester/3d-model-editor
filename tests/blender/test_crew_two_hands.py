"""Two-handed carry (contract section 6, place both_hands) and the eye material.

    server/.venv/bin/python -m pytest tests/blender/test_crew_two_hands.py -q

Lester, 29 Oct, in Equipment placement: "carry rifle in both hands. left hand on barrel, right hand
on trigger". Everything here is measured from the GLB (what Unreal and the web viewer get), with
the GLB's own animation clips, not from the build report: the hold has to survive export, because
Unreal receives baked keys and no IK.
"""

import json
import math
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_crew import (BLENDER, CATALOG, ROOT, _accessor_values, _build, _dominant, _gltf,  # noqa: E402
                       _node, _png_rgba, _pose, _read_accessor, _skin_data, _skinned, _world_positions,
                       needs_blender)

sys.path.insert(0, str(ROOT / "blender"))
from crew import spec as crew_spec  # noqa: E402

# An AK-47 style rifle as the AI writes one today: modelled upright as if hanging in a relaxed right
# hand, the pistol grip at the origin, muzzle up (+Z), magazine and grip on the item's -X side.
# No grips declared: the builder infers them (grip = origin, support = two thirds = handguard).
RIFLE_CODE = """bm = crew.new_bmesh()
crew.box(bm, (0.0, 0.0, 0.06), (0.05, 0.035, 0.22))          # receiver
crew.box(bm, (0.0, 0.0, 0.27), (0.045, 0.04, 0.20))          # handguard, z 0.17..0.37
crew.cylinder(bm, (0.0, 0.0, 0.52), 0.011, 0.30, 8, "Z")     # barrel, to z 0.67
crew.cylinder(bm, (0.018, 0.0, 0.30), 0.009, 0.20, 6, "Z")   # gas tube
crew.box(bm, (0.0, 0.0, -0.17), (0.06, 0.032, 0.26))         # stock, from z -0.30
crew.box(bm, (-0.05, 0.0, -0.01), (0.10, 0.03, 0.035))       # pistol grip
crew.box(bm, (-0.11, 0.0, 0.09), (0.16, 0.026, 0.06))        # magazine
crew.object_from_bmesh("Equipment__Rifle", bm, [0.12, 0.09, 0.06], roughness=0.5)
"""
# A boat hook modelled lying along +X, with its grips declared by the item code.
HOOK_CODE = """bm = crew.new_bmesh()
crew.cylinder(bm, (0.6, 0.0, 0.0), 0.016, 1.6, 8, "X")
crew.box(bm, (1.42, 0.0, 0.05), (0.03, 0.02, 0.10))
o = crew.object_from_bmesh("Equipment__BoatHook", bm, [0.6, 0.45, 0.2])
crew.two_hand_grips(o, (0.15, 0.0, 0.0), (0.55, 0.0, 0.0))
"""
HAND_TOL_M = 0.03
ARM_BONES = {"%s_%s" % (b, s) for s in "lr" for b in ("clavicle", "upperarm", "lowerarm", "hand")} | {
    "%s_0%d_%s" % (f, i, s) for s in "lr" for f in ("thumb", "index", "middle", "ring", "pinky") for i in (1, 2, 3)}


def _spec(place=None, code=RIFLE_CODE, desc="AK-47 style rifle", anims=None):
    spec = json.loads(CATALOG.read_text(encoding="utf-8"))
    for k in ("param_docs", "options", "description"):
        spec.pop(k, None)
    spec["id"] = "crew_rifle"
    if anims:
        spec["animations"] = anims
    if place:
        spec["custom_items"] = [{"id": "item_1", "description": desc, "module": "equipment", "place": place,
                                 "code": code}]
    return spec


def _fixture_build(tmp_path_factory, name, spec):
    if not Path(BLENDER).exists():
        pytest.skip("Blender not installed")
    return _build(tmp_path_factory.mktemp(name), spec)


@pytest.fixture(scope="module")
def rifle_build(tmp_path_factory):
    """The default male (life vest and all) carrying a described rifle in both hands."""
    return _fixture_build(tmp_path_factory, "two_hands", _spec("both_hands"))


@pytest.fixture(scope="module")
def one_hand_build(tmp_path_factory):
    """The same rifle in the right hand."""
    return _fixture_build(tmp_path_factory, "one_hand", _spec("right_hand"))


@pytest.fixture(scope="module")
def bare_build(tmp_path_factory):
    """The same person holding nothing."""
    return _fixture_build(tmp_path_factory, "bare", _spec())


@pytest.fixture(scope="module")
def hook_build(tmp_path_factory):
    return _fixture_build(tmp_path_factory, "hook", _spec("both_hands", HOOK_CODE, "boat hook", ["idle", "walk"]))


# ---------------------------------------------------------------------------------------------
# Helpers: positions in the glTF frame (Y up, +X forward, +Z = Blender -Y = the person's right)

def _t(m):
    return [m[r][3] for r in range(3)]


def _apply(m, p):
    return [sum(m[r][k] * p[k] for k in range(3)) + m[r][3] for r in range(3)]


def _palm(w, s):
    """Palm centre of hand s from the posed joints (the same definition the builder targets)."""
    heads = [_t(w["%s_%s" % (n, s)]) for n in ("hand", "index_01", "middle_01", "ring_01", "pinky_01")]
    palm = [sum(h[i] for h in heads) / 5 for i in range(3)]
    thumb = _t(w["thumb_02_" + s])
    return [palm[i] + 0.4 * (thumb[i] - palm[i]) for i in range(3)]


def _part(report, name):
    parts = {p["path"]: p for p in json.loads((report["_out"] / "parts.json").read_text(encoding="utf-8"))}
    return parts[name]


def _grip_points(g, report, name, overrides):
    """World grip and support of an item under a pose: parts.json gives them in the item's own mesh
    frame (Blender axes), the GLB gives the item node's posed world matrix."""
    part = _part(report, name)
    w = _world_positions(g, overrides)[name]
    to_gltf = lambda v: [v[0], v[2], -v[1]]
    return _apply(w, to_gltf(part["grip_m"])), _apply(w, to_gltf(part["support_m"])), w


def _frames(g, clip):
    return _pose(g, clip, 0)[1]


HOLD_FRAMES = [("idle", 0), ("walk", "quarter"), ("walk", "mid"), ("brace", "mid"), ("squat", "mid"),
               ("hands_up", 0), ("hands_up", "last")]


def _frame_index(g, clip, which):
    n = _frames(g, clip)
    return {"quarter": n // 4, "mid": n // 2, "last": n - 1}.get(which, which)


# ---------------------------------------------------------------------------------------------
# Pure logic

def test_both_hands_is_a_place_and_takes_both_hands():
    assert "both_hands" in crew_spec.PLACES
    rifle = {"id": "item_1", "module": "equipment", "place": "both_hands", "code": RIFLE_CODE}
    spec, errors, _ = crew_spec.normalize({"custom_items": [rifle], "equipment": ["life_vest", "radio"]})
    assert errors == [] and spec["custom_items"][0]["place"] == "both_hands"
    _, errors, _ = crew_spec.normalize({"custom_items": [rifle], "equipment": [{"name": "radio", "place": "left_hand"}]})
    assert any("both hands" in e and "radio (left_hand)" in e for e in errors), errors
    torch = {"id": "item_2", "module": "equipment", "code": "x = 1"}   # no place: right hand
    _, errors, _ = crew_spec.normalize({"custom_items": [rifle, torch]})
    assert any("item_2 (right_hand)" in e for e in errors), errors
    _, errors, _ = crew_spec.normalize({"custom_items": [rifle, dict(rifle, id="item_3")]})
    assert any("only one item" in e for e in errors), errors


# ---------------------------------------------------------------------------------------------
# The carry

@needs_blender
def test_two_hands_build_ok(rifle_build):
    r = rifle_build
    assert r["ok"] is True and r["_exit"] == 0, r["errors"]
    part = _part(r, "Equipment__Rifle")
    assert part["bone"] == "spine_03" and part["place"] == "both_hands"
    assert any("both hands" in line for line in r["log"])


@needs_blender
def test_grip_and_support_inferred_on_the_rifle(rifle_build):
    """No grips declared: the grip is the pistol grip at the origin (the one-hand grip, in the back
    half), the support two thirds along = on the handguard (z 0.17..0.37 as modelled)."""
    part = _part(rifle_build, "Equipment__Rifle")
    assert all(abs(c) < 1e-6 for c in part["grip_m"]), part["grip_m"]
    sx, sy, sz = part["support_m"]
    assert 0.17 <= sz <= 0.37 and abs(sx) < 0.03 and abs(sy) < 0.03, part["support_m"]


@needs_blender
@pytest.mark.parametrize("clip,which", HOLD_FRAMES)
def test_hands_on_grip_and_support(rifle_build, clip, which):
    """Right palm within 3 cm of the grip, left palm within 3 cm of the support, in the GLB's own
    clip. The hand without the hold (the plain animation) is ~0.5 m away, so this cannot pass by
    accident."""
    g = _gltf(rifle_build)
    ov, _ = _pose(g, clip, _frame_index(g, clip, which))
    grip, support, _ = _grip_points(g, rifle_build, "Equipment__Rifle", ov)
    w = _world_positions(g, ov)
    assert math.dist(_palm(w, "r"), grip) < HAND_TOL_M, (clip, which, math.dist(_palm(w, "r"), grip))
    assert math.dist(_palm(w, "l"), support) < HAND_TOL_M, (clip, which, math.dist(_palm(w, "l"), support))


@needs_blender
def test_hands_up_lets_go_and_the_rifle_stays_slung(rifle_build):
    """Decision (contract section 6): in hands_up the hands leave the item and go up; it stays
    where it is carried, as on a sling."""
    g = _gltf(rifle_build)
    ov, n = _pose(g, "hands_up", 0)
    ov_mid, _ = _pose(g, "hands_up", n // 2)
    w = _world_positions(g, ov_mid)
    grip, support, _ = _grip_points(g, rifle_build, "Equipment__Rifle", ov_mid)
    head_y = _t(w["head"])[1]
    for s in "lr":
        assert _t(w["hand_" + s])[1] > head_y, s                  # hands above the head
    assert math.dist(_palm(w, "r"), grip) > 0.3
    still, _, _ = _grip_points(g, rifle_build, "Equipment__Rifle", ov)
    assert math.dist(grip, still) < 0.1                           # the rifle did not go with them


@needs_blender
@pytest.mark.parametrize("clip,which", [("idle", 0), ("walk", "quarter")])
def test_long_axis_points_forward_and_down(rifle_build, clip, which):
    """The muzzle end (support side) is forward of the grip and 30-45 degrees below horizontal (a few
    degrees of slack for the clip's own torso motion)."""
    g = _gltf(rifle_build)
    ov, _ = _pose(g, clip, _frame_index(g, clip, which))
    grip, support, _ = _grip_points(g, rifle_build, "Equipment__Rifle", ov)
    d = [b - a for a, b in zip(grip, support)]
    horiz = math.hypot(d[0], d[2])
    down = math.degrees(math.atan2(-d[1], horiz))
    assert d[0] > 0 and d[0] > abs(d[2]), d                      # mostly forward
    assert 27 <= down <= 48, down


def _torso_rings(g, ov):
    """Posed torso surface (suit and skin vertices driven mainly by the spine), as {height cell:
    [(x, z)]} for an inside-the-torso test by horizontal slices."""
    rings = {}
    for name in ("Body__Skin", "Clothes__Suit"):
        if not any(n.name == name for n in g.nodes):
            continue
        _, infl = _skin_data(g, name)
        idx = [i for i, d in enumerate(_dominant(infl)) if d in ("spine_01", "spine_02", "spine_03")][::2]
        for p in _skinned(g, name, ov, idx):
            rings.setdefault(int(math.floor(p[1] / 0.02)), []).append((p[0], p[2]))
    return rings


def _inside_torso(rings, p, shrink=0.85):
    ring = rings.get(int(math.floor(p[1] / 0.02)))
    if not ring or len(ring) < 12:
        return False
    xs, zs = [q[0] for q in ring], [q[1] for q in ring]
    cx, cz = (min(xs) + max(xs)) / 2, (min(zs) + max(zs)) / 2
    rx, rz = (max(xs) - min(xs)) / 2 * shrink, (max(zs) - min(zs)) / 2 * shrink
    return ((p[0] - cx) / rx) ** 2 + ((p[2] - cz) / rz) ** 2 < 1.0


@needs_blender
@pytest.mark.parametrize("clip,which", [("idle", 0), ("walk", "quarter"), ("walk", "mid")])
def test_arms_and_rifle_stay_out_of_the_torso(rifle_build, clip, which):
    """Sampled points along both forearms and the outer two thirds of both upper arms, and every
    rifle vertex, lie outside the torso's horizontal cross-section (shrunk 15% for the soft edge)."""
    g = _gltf(rifle_build)
    ov, _ = _pose(g, clip, _frame_index(g, clip, which))
    w = _world_positions(g, ov)
    rings = _torso_rings(g, ov)
    pts = []
    for s in "lr":
        for a, b, t0 in (("upperarm_" + s, "lowerarm_" + s, 0.35), ("lowerarm_" + s, "hand_" + s, 0.0)):
            pa, pb = _t(w[a]), _t(w[b])
            for k in range(6):
                t = t0 + (1 - t0) * k / 5
                pts.append(("%s %.2f" % (a, t), [pa[i] + (pb[i] - pa[i]) * t for i in range(3)]))
    inside = [name for name, p in pts if _inside_torso(rings, p)]
    assert not inside, inside
    rifle_w = w["Equipment__Rifle"]
    node = g.nodes[_node(g, "Equipment__Rifle")]
    verts = [_apply(rifle_w, p) for prim in g.meshes[node.mesh].primitives
             for p in _accessor_values(g, prim.attributes.POSITION)]
    assert not [p for p in verts if _inside_torso(rings, p)]


@needs_blender
def test_declared_grips_are_used(hook_build):
    """A boat hook whose code declares its grips with crew.two_hand_grips: those exact points are
    used (not the thirds), and the hands are on them."""
    r = hook_build
    assert r["ok"] is True, r["errors"]
    part = _part(r, "Equipment__BoatHook")
    assert part["grip_m"] == [0.15, 0.0, 0.0] and part["support_m"] == [0.55, 0.0, 0.0]
    g = _gltf(r)
    for clip in ("idle", "walk"):
        ov, _ = _pose(g, clip, 0)
        grip, support, _ = _grip_points(g, r, "Equipment__BoatHook", ov)
        w = _world_positions(g, ov)
        assert math.dist(_palm(w, "r"), grip) < HAND_TOL_M and math.dist(_palm(w, "l"), support) < HAND_TOL_M


@needs_blender
def test_too_short_for_two_hands_is_a_build_error(tmp_path):
    code = ("bm = crew.new_bmesh()\ncrew.box(bm, (0, 0, 0.05), (0.04, 0.06, 0.12))\n"
            "crew.object_from_bmesh('Equipment__Radio2', bm, [0.1, 0.1, 0.1])\n")
    r = _build(tmp_path, _spec("both_hands", code, "handheld radio", ["idle"]))
    assert r["ok"] is False and any("too short" in e and "Equipment__Radio2" in e for e in r["errors"]), r["errors"]


# ---------------------------------------------------------------------------------------------
# One-handed builds are untouched

def _clips(g):
    """{clip: {(node name, path): raw sampler output bytes}}."""
    blob = g.binary_blob()
    out = {}
    for a in g.animations:
        chans = {}
        for ch in a.channels:
            acc = g.accessors[a.samplers[ch.sampler].output]
            bv = g.bufferViews[acc.bufferView]
            n = {"SCALAR": 1, "VEC3": 3, "VEC4": 4}[acc.type]
            start = (bv.byteOffset or 0) + (acc.byteOffset or 0)
            chans[(g.nodes[ch.target.node].name, ch.target.path)] = blob[start:start + 4 * n * acc.count]
        out[a.name] = chans
    return out


@needs_blender
def test_one_handed_carry_leaves_every_clip_byte_identical(one_hand_build, bare_build):
    """Holding the rifle in one hand changes no animation key at all: every channel of every clip
    is byte-for-byte the same as the same person holding nothing. (The two-hand solve runs only for
    both_hands.)"""
    assert one_hand_build["ok"] is True and bare_build["ok"] is True
    a, b = _clips(_gltf(one_hand_build)), _clips(_gltf(bare_build))
    assert a.keys() == b.keys()
    for clip in a:
        assert a[clip].keys() == b[clip].keys(), clip
        diff = [k for k in a[clip] if a[clip][k] != b[clip][k]]
        assert not diff, (clip, diff[:5])
    assert _part(one_hand_build, "Equipment__Rifle")["bone"] == "hand_r"


@needs_blender
def test_two_hands_changes_only_the_arms(rifle_build, one_hand_build):
    """Between the one-hand and the both-hands rifle, the only keys that differ are the arm, hand and
    finger bones: legs, spine, head and pelvis are byte-identical in every clip."""
    a, b = _clips(_gltf(rifle_build)), _clips(_gltf(one_hand_build))
    for clip in a:
        diff = {k[0] for k in a[clip] if a[clip][k] != b[clip][k]}
        assert diff and diff <= ARM_BONES, (clip, sorted(diff - ARM_BONES))
        assert {"upperarm_l", "upperarm_r", "lowerarm_l", "lowerarm_r"} <= diff, clip


# ---------------------------------------------------------------------------------------------
# Eyes (Lester's screenshot, 29 Oct: white, glowing eyes)

def _texel(rows, bpp, w, h, uv):
    x = min(w - 1, max(0, int(uv[0] % 1.0 * w)))
    y = min(h - 1, max(0, int(uv[1] % 1.0 * h)))     # glTF UV origin is the image's top-left
    return rows[y][x * bpp:(x + 1) * bpp]


@needs_blender
def test_eyes_draw_the_iris_not_a_white_shell(bare_build):
    """The eye mesh is opaque, so every face of it is seen. MakeHuman's cornea shell is mapped to a
    fully transparent, pale blue-white patch of the eye texture; drawn opaque it covers the iris
    with white (the bug). So: no eye vertex may sample a transparent texel, and the frontmost eye
    vertices (the iris and pupil) must sample a dark texel."""
    g = _gltf(bare_build)
    node = g.nodes[_node(g, "Head__Eyes")]
    mats = {g.materials[p.material].name: g.materials[p.material] for p in g.meshes[node.mesh].primitives}
    assert all(m.alphaMode in (None, "OPAQUE") for m in mats.values()), [m.alphaMode for m in mats.values()]
    prim = g.meshes[node.mesh].primitives[0]
    mat = g.materials[prim.material]
    img = g.images[g.textures[mat.pbrMetallicRoughness.baseColorTexture.index].source]
    bv = g.bufferViews[img.bufferView]
    data = g.binary_blob()[(bv.byteOffset or 0):(bv.byteOffset or 0) + bv.byteLength]
    w, h, bpp, rows = _png_rgba(data)
    pos = _accessor_values(g, prim.attributes.POSITION)
    uvs = _accessor_values(g, prim.attributes.TEXCOORD_0)
    texels = [_texel(rows, bpp, w, h, uv) for uv in uvs]
    if bpp == 4:
        clear = sum(1 for t in texels if t[3] < 128)
        assert clear == 0, "%d of %d eye vertices sample transparent texels" % (clear, len(texels))
    for side in (1, -1):                                   # glTF +Z is the person's right
        eye = [i for i, p in enumerate(pos) if p[2] * side > 0]
        front_x = max(pos[i][0] for i in eye)
        front = [i for i in eye if pos[i][0] > front_x - 0.002]
        lum = [(0.2126 * texels[i][0] + 0.7152 * texels[i][1] + 0.0722 * texels[i][2]) / 255 for i in front]
        assert sum(lum) / len(lum) < 0.35, (side, sum(lum) / len(lum))
