"""Crew builder proof (blender/build_crew.py, contract sections 1, 2, 4, 6).

    server/.venv/bin/python -m pytest tests/blender/test_crew.py -q

Builds the catalog's default male and a 160 cm variant with every piece of equipment in real
Blender (MPFB2 must be installed; the clothes checks also need the CC0 asset pack, installed by
tools/install_mpfb_assets.sh), then reads the GLB back with pygltflib. Nothing here trusts the
build report alone where the GLB itself can be measured.
"""

import json
import math
import os
import struct
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "server"))
from app.config import BLENDER_CONFIG, DEFAULT_BLENDER  # noqa: E402

import pytest

ROOT = Path(__file__).resolve().parents[2]
BLENDER = os.environ.get("BLENDER", str(DEFAULT_BLENDER))
CATALOG = ROOT / "catalog" / "crew" / "male_default.json"
MPFB_DATA = BLENDER_CONFIG / "extensions/.user/blender_org/mpfb/data"
ANIMS = ["idle", "walk", "brace", "hands_up", "squat"]
GAME_ENGINE_BONES = sorted(
    ["Root", "pelvis", "spine_01", "spine_02", "spine_03", "neck_01", "head"]
    + [f"{b}_{s}" for s in "lr" for b in ("clavicle", "upperarm", "lowerarm", "hand", "thigh", "calf", "foot", "ball")]
    + [f"{f}_0{i}_{s}" for s in "lr" for f in ("thumb", "index", "middle", "ring", "pinky") for i in (1, 2, 3)]
)

sys.path.insert(0, str(ROOT / "blender"))
from crew import animations as crew_anims  # noqa: E402  (pure Python parts only)
from crew import spec as crew_spec  # noqa: E402

pygltflib = pytest.importorskip("pygltflib")
needs_blender = pytest.mark.skipif(not Path(BLENDER).exists(), reason="Blender not installed at " + BLENDER)


# ---------------------------------------------------------------------------------------------
# Pure logic (no Blender)

def test_default_spec_normalises_cleanly():
    spec, errors, warnings = crew_spec.normalize(json.loads(CATALOG.read_text(encoding="utf-8")))
    assert errors == [] and warnings == []
    assert spec["body"]["height_cm"] == 178
    assert spec["equipment"] == [{"name": "life_vest", "color": crew_spec.EQUIPMENT_COLORS["orange"], "place": None}]
    assert spec["clothes"]["shirt"] is None and spec["clothes"]["pants"] is None
    assert spec["accessories"] == []
    assert spec["animations"] == ANIMS


def test_bad_spec_values_are_reported_not_built():
    _, errors, _ = crew_spec.normalize({"body": {"height_cm": 250, "skin": "green"},
                                        "clothes": {"suit": "spacesuit"}, "equipment": ["jetpack"],
                                        "animations": ["moonwalk"]})
    joined = " ".join(errors)
    for word in ("height_cm", "skin", "suit", "equipment", "animations"):
        assert word in joined


def test_age_macro_matches_makehuman_scale():
    assert crew_spec.age_years_to_macro(1) == 0.0
    assert crew_spec.age_years_to_macro(25) == 0.5
    assert crew_spec.age_years_to_macro(90) == 1.0
    assert abs(crew_spec.age_years_to_macro(30) - (0.5 + 0.5 * 5 / 65)) < 1e-9


def test_skin_asset_choice():
    assert crew_spec.skin_asset("dark", "male", 30) == "young_african_male/young_african_male.mhmat"
    assert crew_spec.skin_asset("light", "female", 45) == "middleage_caucasian_female/middleage_caucasian_female.mhmat"
    assert crew_spec.skin_asset("medium", "male", 70) == "old_asian_male/old_asian_male.mhmat"


def test_solver_hits_target_and_clamps():
    x, fx = crew_spec.solve_monotonic(lambda v: 1.2 + v, 1.78)
    assert abs(fx - 1.78) <= 0.0005 and abs(x - 0.58) < 0.001
    assert crew_spec.solve_monotonic(lambda v: 1.2 + v, 3.0) == (1.0, 2.2)


def test_label_rule():
    assert crew_spec.part_label("Equipment__life_vest") == "Equipment › life vest"


@pytest.mark.parametrize("name", ["idle", "walk"])
def test_loops_close(name):
    anim = crew_anims.ANIMATIONS[name]
    a, b = crew_anims.sample(anim, 0.0), crew_anims.sample(anim, anim["duration"])
    for ch in a:
        assert all(abs(x - y) < 1e-6 for x, y in zip(a[ch], b[ch])), ch


@pytest.mark.parametrize("name", ["idle", "brace", "hands_up", "squat"])
def test_planted_clips_never_move_the_feet(name):
    anim = crew_anims.ANIMATIONS[name]
    for f in range(crew_anims.frame_count(anim) + 1):
        pose = crew_anims.sample(anim, f / crew_anims.FPS)
        assert pose["foot_l"] == [0, 0, 0, 0] and pose["foot_r"] == [0, 0, 0, 0]


def test_catalog_options_are_installed():
    """Every clothes option the AI may pick must exist in MPFB's installed pack."""
    if not (MPFB_DATA / "packs" / "makehuman_system_assets.json").exists():
        pytest.skip("MakeHuman system asset pack not installed (run tools/install_mpfb_assets.sh)")
    opts = json.loads(CATALOG.read_text(encoding="utf-8"))["options"]
    for slot in ("suit", "shoes", "hat"):
        for name in opts[slot]:
            if name is not None:
                assert (MPFB_DATA / "clothes" / name / f"{name}.mhclo").exists(), name
    assert opts["animations"] == ANIMS
    assert sorted(opts["equipment"]) == sorted(crew_spec.EQUIPMENT)


# ---------------------------------------------------------------------------------------------
# Real builds

def _build(tmp: Path, spec: dict) -> dict:
    spec_path = tmp / "spec.json"
    spec_path.write_text(json.dumps(spec))
    out = tmp / "out"
    proc = subprocess.run([BLENDER, "-b", "--python", str(ROOT / "blender" / "build_crew.py"), "--",
                           "--spec", str(spec_path), "--out", str(out)],
                          capture_output=True, text=True, timeout=900)
    report_path = out / "build_report.json"
    assert report_path.exists(), proc.stdout[-3000:] + proc.stderr[-3000:]
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["_exit"] = proc.returncode
    report["_out"] = out
    return report


@pytest.fixture(scope="module")
def default_build(tmp_path_factory):
    if not Path(BLENDER).exists():
        pytest.skip("Blender not installed")
    return _build(tmp_path_factory.mktemp("crew_default"), json.loads(CATALOG.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def short_build(tmp_path_factory):
    if not Path(BLENDER).exists():
        pytest.skip("Blender not installed")
    spec = json.loads(CATALOG.read_text(encoding="utf-8"))
    spec["id"] = "crew_short"
    spec["body"]["height_cm"] = 160
    spec["equipment"] = ["life_vest", "helmet", "binoculars", "radio"]
    return _build(tmp_path_factory.mktemp("crew_short"), spec)


def _gltf(report):
    return pygltflib.GLTF2().load(str(report["_out"] / "model.glb"))


def _read_accessor(g, idx):
    acc = g.accessors[idx]
    bv = g.bufferViews[acc.bufferView]
    blob = g.binary_blob()
    n = {"SCALAR": 1, "VEC3": 3, "VEC4": 4, "MAT4": 16}[acc.type]
    assert acc.componentType == 5126
    stride = bv.byteStride or 4 * n
    base = (bv.byteOffset or 0) + (acc.byteOffset or 0)
    return [struct.unpack_from("<%df" % n, blob, base + i * stride) for i in range(acc.count)]


def _mat(t=None, r=None, s=None):
    t = t or [0, 0, 0]
    x, y, z, w = r or [0, 0, 0, 1]
    s = s or [1, 1, 1]
    rot = [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
           [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
           [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]
    return [[rot[i][0] * s[0], rot[i][1] * s[1], rot[i][2] * s[2], t[i]] for i in range(3)] + [[0, 0, 0, 1]]


def _mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def _world_positions(g, overrides=None):
    """World matrix of every node, with optional per-node TRS overrides {node: {path: value}}."""
    overrides = overrides or {}
    parent = {}
    for i, n in enumerate(g.nodes):
        for c in n.children or []:
            parent[c] = i
    cache = {}

    def world(i):
        if i in cache:
            return cache[i]
        n = g.nodes[i]
        o = overrides.get(i, {})
        local = _mat(o.get("translation", n.translation), o.get("rotation", n.rotation), o.get("scale", n.scale))
        m = _mul(world(parent[i]), local) if i in parent else local
        cache[i] = m
        return m

    return {g.nodes[i].name: world(i) for i in range(len(g.nodes))}


def _node(g, name):
    return next(i for i, n in enumerate(g.nodes) if n.name == name)


def _top_y(g, node_name):
    """Highest point of a mesh node in glTF space (Y up), via its accessor max and node chain."""
    idx = _node(g, node_name)
    w = _world_positions(g)[node_name]
    tops = []
    for prim in g.meshes[g.nodes[idx].mesh].primitives:
        acc = g.accessors[prim.attributes.POSITION]
        for corner in ((acc.min[0], acc.max[1], acc.min[2]), (acc.max[0], acc.max[1], acc.max[2])):
            tops.append(sum(w[1][k] * corner[k] for k in range(3)) + w[1][3])
    return max(tops)


@needs_blender
def test_default_build_ok(default_build):
    r = default_build
    assert r["ok"] is True, r["errors"]
    assert r["_exit"] == 0
    assert r["errors"] == []
    assert 0 < r["triangles"] <= 40000
    for f in ("model.glb", "model.blend", "parts.json", "build_report.json"):
        assert (r["_out"] / f).exists(), f
    assert r["bones"] == 53
    assert r["animations"] == ANIMS
    assert abs(r["bounds_min"][2]) < 0.002   # soles on the deck


@needs_blender
def test_one_skin_with_game_engine_bones(default_build):
    g = _gltf(default_build)
    assert len(g.skins) == 1
    names = sorted(g.nodes[j].name for j in g.skins[0].joints)
    assert names == GAME_ENGINE_BONES
    skinned = [n for n in g.nodes if n.mesh is not None and n.skin is not None]
    assert skinned and all(n.skin == 0 for n in skinned)


def _is_identity(node):
    t = node.translation or [0, 0, 0]
    r = node.rotation or [0, 0, 0, 1]
    s = node.scale or [1, 1, 1]
    return (all(abs(x) < 1e-6 for x in t) and all(abs(a - b) < 1e-6 for a, b in zip(r, [0, 0, 0, 1]))
            and all(abs(x - 1) < 1e-6 for x in s) and node.matrix is None)


@needs_blender
def test_unreal_identity_armature_and_real_size(default_build):
    """UE 5.8 ignores parent transforms on skinned meshes: the armature node must be an identity
    scene root and every skinned mesh node identity too, with the human at real size in metres."""
    g = _gltf(default_build)
    roots = [g.nodes[i] for i in g.scenes[g.scene or 0].nodes]
    assert [n.name for n in roots] == ["Crew"]
    assert _is_identity(roots[0])
    for n in g.nodes:
        if n.mesh is not None and n.skin is not None:
            assert _is_identity(n), n.name
    ys = []
    for n in g.nodes:
        if n.mesh is not None and n.skin is not None:
            for prim in g.meshes[n.mesh].primitives:
                acc = g.accessors[prim.attributes.POSITION]
                ys += [acc.min[1], acc.max[1]]
    assert abs(min(ys)) < 0.002                       # soles at the origin
    assert 1.76 <= max(ys) <= 1.84, max(ys)           # 1.78 m body, plus hair


@needs_blender
def test_animations_named_exactly(default_build):
    g = _gltf(default_build)
    assert sorted(a.name for a in g.animations) == sorted(ANIMS)
    joints = {g.skins[0].joints[k] for k in range(len(g.skins[0].joints))}
    for a in g.animations:
        targets = {c.target.node for c in a.channels}
        assert targets and targets <= joints, a.name  # only bones are animated


@needs_blender
def test_height_within_2cm(default_build):
    assert abs(default_build["height_m"] - 1.78) <= 0.02
    g = _gltf(default_build)
    top = _top_y(g, "Body__Skin")   # GLB is +Y up; body top = crown of the head, soles at 0
    assert abs(top - 1.78) <= 0.02, top


@needs_blender
def test_height_slider_works(default_build, short_build):
    assert short_build["ok"] is True, short_build["errors"]
    assert abs(short_build["height_m"] - 1.60) <= 0.02
    tall = _top_y(_gltf(default_build), "Body__Skin")
    short = _top_y(_gltf(short_build), "Body__Skin")
    assert tall - short > 0.15, (tall, short)


@needs_blender
def test_equipment_parented_under_bones(short_build):
    g = _gltf(short_build)
    joints = {g.nodes[j].name for j in g.skins[0].joints}
    parent = {c: g.nodes[i].name for i, n in enumerate(g.nodes) for c in (n.children or [])}
    expected = {"Equipment__life_vest": "spine_02", "Equipment__helmet": "head",
                "Equipment__binoculars": "spine_03", "Equipment__radio": "pelvis"}
    for name, bone in expected.items():
        idx = _node(g, name)
        assert g.nodes[idx].mesh is not None, name
        assert parent[idx] == bone and bone in joints, (name, parent.get(idx))
    parts = {p["path"]: p for p in json.loads((short_build["_out"] / "parts.json").read_text(encoding="utf-8"))}
    for name, bone in expected.items():
        assert parts[name]["bone"] == bone
        assert parts[name]["label"].startswith("Equipment › ")


@needs_blender
def test_parts_json_contract_fields(default_build):
    parts = json.loads((default_build["_out"] / "parts.json").read_text(encoding="utf-8"))
    keys = {"path", "label", "parent", "joint", "station", "origin_m", "rotation_deg",
            "bounds_min", "bounds_max", "triangles"}
    assert parts and all(keys <= set(p) for p in parts)
    assert sum(p["triangles"] for p in parts) == default_build["triangles"]
    assert all(p["station"] is None and p["joint"] is None for p in parts)
    assert {"Body__Skin", "Equipment__life_vest"} <= {p["path"] for p in parts}


@needs_blender
def test_feet_on_deck_in_first_idle_frame(default_build):
    """Pose the GLB skeleton at idle's first keyframe and measure the toe (ball) joints."""
    g = _gltf(default_build)
    idle = next(a for a in g.animations if a.name == "idle")
    overrides = {}
    for ch in idle.channels:
        sampler = idle.samplers[ch.sampler]
        first = _read_accessor(g, sampler.output)[0]
        overrides.setdefault(ch.target.node, {})[ch.target.path] = list(first)
    w = _world_positions(g, overrides)
    for s in "lr":
        ball_y = w["ball_" + s][1][3]
        ankle_y = w["foot_" + s][1][3]
        assert 0.0 <= ball_y <= 0.03, ball_y      # toe joint just above the deck
        assert 0.04 <= ankle_y <= 0.12, ankle_y   # ankle at a believable height, not sunk
    # And planted for the whole clip, per the builder's own frame-by-frame check.
    for clip in ("idle", "brace", "squat"):
        for s in "lr":
            assert default_build["feet"][clip]["foot_%s_z_range" % s] < 0.01
            assert default_build["feet"][clip]["foot_%s_x_range" % s] < 0.01


@needs_blender
def test_clothes_present_and_skinned(default_build):
    if not default_build.get("mpfb_pack_installed"):
        pytest.skip("MakeHuman system asset pack not installed (run tools/install_mpfb_assets.sh)")
    g = _gltf(default_build)
    for name in ("Clothes__Suit", "Clothes__Shoes", "Head__Hair", "Head__Eyes", "Head__Eyebrows"):
        n = g.nodes[_node(g, name)]
        assert n.mesh is not None and n.skin == 0, name
    mats = {m.name: m for m in g.materials}
    assert mats["Body__Skin"].alphaMode in (None, "OPAQUE")      # skin is never translucent in Unreal
    assert mats["Clothes__Suit"].alphaMode in (None, "OPAQUE")
    assert mats["Head__Hair"].alphaMode == "MASK"
    assert mats["Body__Skin"].pbrMetallicRoughness.baseColorTexture is not None


@needs_blender
def test_bad_spec_fails_with_report(tmp_path):
    r = _build(tmp_path, {"schema": "me3d.crew_spec/1", "id": "bad", "body": {"skin": "green"}})
    assert r["ok"] is False and r["_exit"] == 1
    assert any("skin" in e for e in r["errors"])


# ---------------------------------------------------------------------------------------------
# Made by description (contract section 6): hair colour, shoe/hat tints, custom_items

BLONDE = [0.687, 0.479, 0.188]   # linear, as a Blender Base Color (sRGB #D8B878)

SINGLET_CODE = """hip = crew.joint_z("thigh_l")
crew.garment("Clothes__Singlet", ["torso", "pelvis"], [0.9, 0.9, 0.88], z_min=hip - 0.03)
"""
SHORTS_CODE = """hip = crew.joint_z("thigh_l")
knee = crew.joint_z("calf_l")
crew.garment("Shorts", ["pelvis", "thighs"], [0.05, 0.08, 0.25], z_min=0.5 * (hip + knee),
             z_max=hip + 0.12, offset_m=0.014)
"""
HOOK_CODE = """bm = crew.new_bmesh()
crew.cylinder(bm, (0, 0, 0.45), 0.014, 1.7, 8, "Z")
crew.box(bm, (0.04, 0, 1.28), (0.09, 0.012, 0.02))
crew.cylinder(bm, (0.075, 0, 1.23), 0.01, 0.1, 6, "Z")
hook = crew.object_from_bmesh("Equipment__BoatHook", bm, [0.45, 0.3, 0.12])
crew.hold(hook, "right")
"""


def _custom_spec():
    spec = json.loads(CATALOG.read_text(encoding="utf-8"))
    for k in ("param_docs", "options", "description"):
        spec.pop(k, None)
    spec["id"] = "crew_custom"
    spec["body"]["hair_color"] = BLONDE
    spec["clothes"]["suit"] = None
    spec["equipment"] = []
    spec["custom_items"] = [
        {"id": "singlet", "description": "white singlet", "code": SINGLET_CODE},
        {"id": "shorts", "description": "navy shorts to mid-thigh", "code": SHORTS_CODE},
        {"id": "boat_hook", "description": "holding a boat hook", "code": HOOK_CODE},
    ]
    return spec


def test_explicit_null_suit_means_no_suit_and_absent_means_default():
    spec, errors, _ = crew_spec.normalize({"clothes": {"suit": None}})
    assert errors == [] and spec["clothes"]["suit"] is None
    spec, errors, _ = crew_spec.normalize({"clothes": {"hat": None}})
    assert errors == [] and spec["clothes"]["suit"] == "male_worksuit01"


def test_new_fields_default_to_off():
    spec, errors, warnings = crew_spec.normalize(None)
    assert errors == [] and warnings == []
    assert spec["body"]["hair_color"] is None
    assert spec["clothes"]["shoes_color"] is None and spec["clothes"]["hat_color"] is None
    assert spec["custom_items"] == []


def test_custom_spec_normalises_cleanly():
    spec, errors, warnings = crew_spec.normalize(_custom_spec())
    assert errors == [] and warnings == []
    assert spec["body"]["hair_color"] == BLONDE
    assert [i["id"] for i in spec["custom_items"]] == ["singlet", "shorts", "boat_hook"]


@pytest.mark.parametrize("field,value", [
    ("hair_color", [1.2, 0.5, 0.5]), ("hair_color", [0.5, 0.5]), ("hair_color", "blonde"),
    ("hair_color", [0.5, "x", 0.5]), ("hair_color", [-0.1, 0.5, 0.5]),
])
def test_bad_hair_color_rejected(field, value):
    _, errors, _ = crew_spec.normalize({"body": {field: value}})
    assert any(field in e for e in errors), errors


@pytest.mark.parametrize("key", ["shoes_color", "hat_color"])
def test_shoe_and_hat_colors(key):
    spec, errors, _ = crew_spec.normalize({"clothes": {key: [0.1, 0.2, 0.3]}})
    assert errors == [] and spec["clothes"][key] == [0.1, 0.2, 0.3]
    _, errors, _ = crew_spec.normalize({"clothes": {key: [0.1, 2, 0.3]}})
    assert any(key in e for e in errors)


@pytest.mark.parametrize("items,needle", [
    ("not a list", "custom_items must be a list"),
    (["x"], "custom_items[0]"),
    ([{"id": "bad id", "code": "x=1"}], "id must match"),
    ([{"id": "a", "code": "x=1"}, {"id": "a", "code": "y=2"}], "used twice"),
    ([{"id": "a", "code": ""}], "code must be non-empty"),
    ([{"id": "a", "code": "import os\nos.remove('x')"}], "forbidden"),
    ([{"id": "a", "code": "f = open('x')"}], "forbidden"),
    ([{"id": "a", "code": "x = 1\ny = (\n"}], "custom_items a"),
])
def test_bad_custom_items_rejected(items, needle):
    _, errors, _ = crew_spec.normalize({"custom_items": items})
    assert any(needle in e for e in errors), errors


def test_custom_item_syntax_error_names_id_and_line():
    _, errors, _ = crew_spec.normalize({"custom_items": [{"id": "hook", "code": "x = 1\nif x\n"}]})
    assert len(errors) == 1 and "hook" in errors[0] and "line 2" in errors[0], errors


def test_catalog_documents_new_fields():
    docs = json.loads(CATALOG.read_text(encoding="utf-8"))["param_docs"]
    assert "hair_color" in docs["body"]
    assert "shoes_color" in docs["clothes"] and "hat_color" in docs["clothes"]
    assert "crew.garment" in docs["custom_items"]["doc"]


@pytest.fixture(scope="module")
def custom_build(tmp_path_factory):
    if not Path(BLENDER).exists():
        pytest.skip("Blender not installed")
    return _build(tmp_path_factory.mktemp("crew_custom"), _custom_spec())


def _accessor_values(g, idx):
    """Any accessor as a list of tuples (float, unsigned byte/short, normalised or not)."""
    acc = g.accessors[idx]
    bv = g.bufferViews[acc.bufferView]
    blob = g.binary_blob()
    n = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}[acc.type]
    fmt, size, scale = {5126: ("f", 4, None), 5121: ("B", 1, 255.0), 5123: ("H", 2, 65535.0)}[acc.componentType]
    stride = bv.byteStride or size * n
    base = (bv.byteOffset or 0) + (acc.byteOffset or 0)
    out = []
    for i in range(acc.count):
        v = struct.unpack_from("<%d%s" % (n, fmt), blob, base + i * stride)
        if acc.normalized and scale:
            v = tuple(x / scale for x in v)
        out.append(v)
    return out


def _skin_data(g, node_name):
    """(positions, [(joint names, weights)] per vertex) of a skinned mesh node."""
    node = g.nodes[_node(g, node_name)]
    joints = g.skins[node.skin].joints
    pos, infl = [], []
    for prim in g.meshes[node.mesh].primitives:
        p = _accessor_values(g, prim.attributes.POSITION)
        j = _accessor_values(g, prim.attributes.JOINTS_0)
        w = _accessor_values(g, prim.attributes.WEIGHTS_0)
        pos += p
        infl += [([g.nodes[joints[k]].name for k in jj], ww) for jj, ww in zip(j, w)]
    return pos, infl


def _dominant(infl):
    return [names[max(range(len(ws)), key=lambda k: ws[k])] for names, ws in infl]


def _pose(g, clip, frame):
    """Per-node TRS overrides for a clip at a keyframe index."""
    anim = next(a for a in g.animations if a.name == clip)
    overrides = {}
    for ch in anim.channels:
        vals = _read_accessor(g, anim.samplers[ch.sampler].output)
        overrides.setdefault(ch.target.node, {})[ch.target.path] = list(vals[min(frame, len(vals) - 1)])
    return overrides, len(_read_accessor(g, anim.samplers[anim.channels[0].sampler].output))


def _skinned(g, node_name, overrides, verts, only_joint=None):
    """Linear-blend-skinned world positions of the given vertex indices under a pose (or, with
    only_joint, where they would be if fully bound to that one joint)."""
    node = g.nodes[_node(g, node_name)]
    skin = g.skins[node.skin]
    ibm_raw = _read_accessor(g, skin.inverseBindMatrices)
    ibm = {g.nodes[j].name: [[m[c * 4 + r] for c in range(4)] for r in range(4)] for j, m in zip(skin.joints, ibm_raw)}
    world = _world_positions(g, overrides)
    pos, infl = _skin_data(g, node_name)
    out = []
    for i in verts:
        p = list(pos[i]) + [1.0]
        acc = [0.0, 0.0, 0.0]
        pairs = [(only_joint, 1.0)] if only_joint else zip(*infl[i])
        for name, w in pairs:
            if w <= 0:
                continue
            m = _mul(world[name], ibm[name])
            for r in range(3):
                acc[r] += w * sum(m[r][k] * p[k] for k in range(4))
        out.append(acc)
    return out


def _png_rgba(data):
    """Decode an 8-bit RGB/RGBA non-interlaced PNG (all glTF exporter output) to (w, h, rows)."""
    import zlib
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    i, idat, w = 8, b"", None
    while i < len(data):
        ln, typ = struct.unpack(">I4s", data[i:i + 8])
        body = data[i + 8:i + 8 + ln]
        if typ == b"IHDR":
            w, h, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", body)
            assert depth == 8 and ctype in (2, 6) and interlace == 0, (depth, ctype, interlace)
            bpp = 4 if ctype == 6 else 3
        elif typ == b"IDAT":
            idat += body
        i += 12 + ln
    raw = zlib.decompress(idat)
    stride = w * bpp
    rows, prev = [], bytearray(stride)
    for y in range(h):
        f = raw[y * (stride + 1)]
        line = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        if f == 1:
            for x in range(bpp, stride):
                line[x] = (line[x] + line[x - bpp]) & 255
        elif f == 2:
            line = bytearray((a + b) & 255 for a, b in zip(line, prev))
        elif f == 3:
            for x in range(stride):
                left = line[x - bpp] if x >= bpp else 0
                line[x] = (line[x] + ((left + prev[x]) >> 1)) & 255
        elif f == 4:
            for x in range(stride):
                a = line[x - bpp] if x >= bpp else 0
                b, c = prev[x], prev[x - bpp] if x >= bpp else 0
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc else (b if pb <= pc else c))) & 255
        rows.append(line)
        prev = line
    return w, h, bpp, rows


def _texture_mean_linear(g, material_name, step=3):
    """Mean linear base colour of a material's texture over its visible (alpha > 0.5) texels."""
    mat = next(m for m in g.materials if m.name == material_name)
    tex = g.textures[mat.pbrMetallicRoughness.baseColorTexture.index]
    img = g.images[tex.source]
    bv = g.bufferViews[img.bufferView]
    data = g.binary_blob()[(bv.byteOffset or 0):(bv.byteOffset or 0) + bv.byteLength]
    w, h, bpp, rows = _png_rgba(data)
    lut = [((c / 255 + 0.055) / 1.055) ** 2.4 if c / 255 > 0.04045 else c / 255 / 12.92 for c in range(256)]
    tot, n = [0.0, 0.0, 0.0], 0
    for y in range(0, h, step):
        row = rows[y]
        for x in range(0, w, step):
            o = x * bpp
            if bpp == 4 and row[o + 3] <= 127:
                continue
            for c in range(3):
                tot[c] += lut[row[o + c]]
            n += 1
    assert n > 100, "texture has almost no visible texels"
    return [t / n for t in tot]


def _lum(c):
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


@needs_blender
def test_custom_build_ok_and_listed(custom_build):
    r = custom_build
    assert r["ok"] is True and r["_exit"] == 0, r["errors"]
    assert 0 < r["triangles"] <= 40000
    parts = {p["path"]: p for p in json.loads((r["_out"] / "parts.json").read_text(encoding="utf-8"))}
    for name in ("Clothes__Singlet", "Clothes__Shorts", "Equipment__BoatHook"):
        assert name in parts and parts[name]["triangles"] > 0, name
    assert parts["Equipment__BoatHook"]["bone"] == "hand_r"
    assert "Clothes__Suit" not in parts                   # suit: null means no suit
    assert sum(p["triangles"] for p in parts.values()) == r["triangles"]


@needs_blender
def test_custom_garments_skinned_on_shared_skeleton(custom_build):
    g = _gltf(custom_build)
    assert len(g.skins) == 1
    assert sorted(g.nodes[j].name for j in g.skins[0].joints) == GAME_ENGINE_BONES
    for name in ("Clothes__Singlet", "Clothes__Shorts"):
        n = g.nodes[_node(g, name)]
        assert n.mesh is not None and n.skin == 0, name
        assert _is_identity(n), name                      # Unreal ignores skinned-mesh node transforms


@needs_blender
def test_singlet_has_no_sleeves_or_collar(custom_build):
    """A singlet covers the torso only: nothing on it may be driven mainly by an upper arm, the
    neck or the head (that would be a sleeve or a collar), and it must reach the hips."""
    g = _gltf(custom_build)
    pos, infl = _skin_data(g, "Clothes__Singlet")
    dom = _dominant(infl)
    assert not [d for d in dom if d.startswith(("upperarm", "lowerarm", "hand", "neck", "head"))]
    spine = sum(d.startswith("spine") for d in dom) / len(dom)
    assert spine > 0.6, spine
    # glTF is Y-up: the singlet spans from below the hip joint to the shoulders.
    hip_y = _world_positions(g)["thigh_l"][1][3]
    ys = [p[1] for p in pos]
    assert min(ys) < hip_y and max(ys) > hip_y + 0.4


@needs_blender
def test_shorts_cover_thighs_not_shins(custom_build):
    g = _gltf(custom_build)
    dom = _dominant(_skin_data(g, "Clothes__Shorts")[1])
    for side in ("thigh_l", "thigh_r"):
        assert dom.count(side) / len(dom) > 0.15, side
    assert not [d for d in dom if d.startswith(("calf", "foot", "ball", "spine_02", "spine_03"))]


@needs_blender
def test_shorts_follow_the_legs_when_walking(custom_build):
    """Skin the GLB's shorts with the GLB's own walk clip at the frame where the left knee swings
    furthest: the hem above it must land where the thigh bone carries it (within 3 cm), having
    moved well over 5 cm. Shorts weighted to the pelvis (the failure this guards) would stay put."""
    g = _gltf(custom_build)
    pos, infl = _skin_data(g, "Clothes__Shorts")
    dom = _dominant(infl)
    left = [i for i, d in enumerate(dom) if d == "thigh_l"]
    hem_y = min(pos[i][1] for i in left)
    hem = [i for i in left if pos[i][1] < hem_y + 0.03][::3]
    assert len(hem) >= 5
    rest_knee = _world_positions(g)["calf_l"]
    _, nframes = _pose(g, "walk", 0)
    best = None
    for f in range(0, nframes, 2):
        ov, _ = _pose(g, "walk", f)
        knee = _world_positions(g, ov)["calf_l"]
        d = math.dist([knee[r][3] for r in range(3)], [rest_knee[r][3] for r in range(3)])
        if best is None or d > best[1]:
            best = (ov, d)
    ov = best[0]
    posed = _skinned(g, "Clothes__Shorts", ov, hem)
    rigid = _skinned(g, "Clothes__Shorts", ov, hem, only_joint="thigh_l")
    for i, a, b in zip(hem, posed, rigid):
        assert math.dist(b, pos[i]) > 0.05, "the walk does not move the thigh"
        assert math.dist(a, b) < 0.03, (a, b)


@needs_blender
def test_held_item_follows_right_hand_and_stands_upright(custom_build):
    g = _gltf(custom_build)
    parent = {c: g.nodes[i].name for i, n in enumerate(g.nodes) for c in (n.children or [])}
    idx = _node(g, "Equipment__BoatHook")
    assert g.nodes[idx].mesh is not None and parent[idx] == "hand_r"
    ov, _ = _pose(g, "idle", 0)
    w = _world_positions(g, ov)
    hook, hand = w["Equipment__BoatHook"], w["hand_r"]
    grip = [hook[r][3] for r in range(3)]
    assert math.dist(grip, [hand[r][3] for r in range(3)]) < 0.12, grip   # in the hand, not beside it
    # Blender +Z (the item's long axis) is glTF +Y: in the relaxed stance the hook stands upright.
    up = [hook[r][1] for r in range(3)]
    assert up[1] / math.dist(up, [0, 0, 0]) > 0.9, up


@needs_blender
def test_hair_recoloured_to_target(custom_build, default_build):
    """Blonde must average blonde (a multiply tint over dark brown cannot), and the cut-out stays."""
    g = _gltf(custom_build)
    mats = {m.name: m for m in g.materials}
    assert mats["Head__Hair"].alphaMode == "MASK" and mats["Head__Eyebrows"].alphaMode == "MASK"
    got = _texture_mean_linear(g, "Head__Hair")
    assert all(abs(a - b) < 0.06 for a, b in zip(got, BLONDE)), (got, BLONDE)
    base = _texture_mean_linear(_gltf(default_build), "Head__Hair")
    assert _lum(got) > 2.5 * _lum(base), (got, base)
    brows = _texture_mean_linear(g, "Head__Eyebrows")
    assert all(abs(a - b) < 0.08 for a, b in zip(brows, BLONDE)), (brows, BLONDE)


@needs_blender
def test_failing_custom_item_fails_build_with_its_id(tmp_path):
    spec = _custom_spec()
    spec["animations"] = ["idle"]
    spec["custom_items"] = [{"id": "broken_torch", "description": "torch", "code": "x = 1\ncrew.hold(no_such_thing)\n"}]
    r = _build(tmp_path, spec)
    assert r["ok"] is False and r["_exit"] == 1
    msg = " ".join(r["errors"])
    assert "broken_torch" in msg and "line 2" in msg and "NameError" in msg, r["errors"]


@needs_blender
def test_custom_item_bad_name_or_loose_item_is_an_error(tmp_path):
    """Only equipment items are placed by the builder; an accessory that leaves its Equipment__
    object unparented would not move with the body, so it is an error."""
    spec = _custom_spec()
    spec["animations"] = ["idle"]
    spec["custom_items"] = [{"id": "loose", "description": "", "module": "accessories", "code":
                             "bm = crew.new_bmesh()\ncrew.box(bm, (0, 0, 1), (0.1, 0.1, 0.1))\n"
                             "crew.object_from_bmesh('Torch', bm, [1, 0, 0])\n"}]
    r = _build(tmp_path, spec)
    assert r["ok"] is False
    assert any("loose" in e and "Equipment__Torch" in e and "bone" in e for e in r["errors"]), r["errors"]


# ---------------------------------------------------------------------------------------------
# Modules (contract section 6, "Modules"): shirt/pants presets, accessories, placement, and the
# point of it all - changing one module never changes any other part of the person.

def test_shirt_and_pants_presets_validate():
    spec, errors, warnings = crew_spec.normalize({"clothes": {"suit": None,
                                                              "shirt": {"style": "polo", "color": [0.1, 0.2, 0.3]},
                                                              "pants": {"style": "cargo"}}})
    assert errors == [] and warnings == []
    assert spec["clothes"]["shirt"] == {"style": "polo", "color": [0.1, 0.2, 0.3]}
    assert spec["clothes"]["pants"] == {"style": "cargo", "color": crew_spec.GARMENT_DEFAULT_COLOR["pants"]}
    for bad in ({"style": "kilt"}, {"style": "tshirt", "color": "navy"}, {"style": "tshirt", "color": [2, 0, 0]}, 7):
        _, errors, _ = crew_spec.normalize({"clothes": {"shirt": bad}})
        assert any("clothes.shirt" in e for e in errors), (bad, errors)


def test_suit_with_shirt_warns_that_the_suit_wins():
    spec, errors, warnings = crew_spec.normalize({"clothes": {"shirt": {"style": "tshirt"}}})
    assert errors == [] and spec["clothes"]["suit"] == "male_worksuit01"
    assert any("suit" in w and "shirt" in w for w in warnings), warnings


def test_accessories_validate():
    spec, errors, warnings = crew_spec.normalize({"accessories": ["cap", {"name": "gloves", "color": [0.2, 0.2, 0.2]},
                                                                  "cap"]})
    assert errors == []
    assert spec["accessories"] == [{"name": "cap", "color": crew_spec.ACCESSORY_DEFAULT_COLOR["cap"]},
                                   {"name": "gloves", "color": [0.2, 0.2, 0.2]}]
    assert any("twice" in w for w in warnings)
    for bad, needle in ((["fedora01"], "clothes.hat"), (["monocle"], "accessories"),
                        ([{"name": "cap", "color": "navy"}], "accessories.cap.color"), ("cap", "list")):
        _, errors, _ = crew_spec.normalize({"accessories": bad})
        assert any(needle in e for e in errors), (bad, errors)
    spec, _, warnings = crew_spec.normalize({"equipment": ["helmet"], "accessories": ["cap", "sunglasses"]})
    assert [a["name"] for a in spec["accessories"]] == ["sunglasses"] and any("cap" in w for w in warnings)


def test_equipment_places():
    spec, errors, warnings = crew_spec.normalize({"equipment": [{"name": "life_vest", "place": "back"}, "binoculars",
                                                                {"name": "radio", "place": "left_hand"}]})
    assert errors == []
    assert [(e["name"], e["place"]) for e in spec["equipment"]] == [
        ("life_vest", None), ("binoculars", "chest"), ("radio", "left_hand")]
    assert any("life_vest" in w and "fixed" in w for w in warnings)
    _, errors, _ = crew_spec.normalize({"equipment": [{"name": "radio", "place": "pocket"}]})
    assert any("place" in e for e in errors)


def test_custom_item_module_and_place():
    items = [{"id": "a", "code": "crew.object_from_bmesh('Torch', crew.new_bmesh(), [1, 0, 0])"},
             {"id": "b", "code": "crew.garment('Vest', ['torso'], [1, 1, 1])"},
             {"id": "c", "module": "equipment", "place": "back", "code": "x = 1"},
             {"id": "d", "module": "shirt", "place": "back", "code": "x = 1"}]
    spec, errors, warnings = crew_spec.normalize({"custom_items": items})
    assert errors == []
    assert [(i["module"], i["place"]) for i in spec["custom_items"]] == [
        ("equipment", None), ("shirt", None), ("equipment", "back"), ("shirt", None)]
    assert any("d:" in w and "ignored" in w for w in warnings), warnings
    for bad, needle in (({"module": "cape"}, "module"), ({"module": "equipment", "place": "pocket"}, "place")):
        _, errors, _ = crew_spec.normalize({"custom_items": [dict(id="x", code="x = 1", **bad)]})
        assert any(needle in e for e in errors), errors


def test_catalog_lists_module_options():
    cat = json.loads(CATALOG.read_text(encoding="utf-8"))
    opts, docs = cat["options"], cat["param_docs"]
    assert opts["shirt_styles"] == crew_spec.SHIRT_STYLES and opts["pants_styles"] == crew_spec.PANTS_STYLES
    assert opts["accessories"] == crew_spec.ACCESSORIES and opts["places"] == crew_spec.PLACES
    assert opts["default_places"] == crew_spec.DEFAULT_PLACE
    assert sorted(opts["shoe_labels"]) == crew_spec.SHOES
    assert cat["clothes"]["shirt"] is None and cat["clothes"]["pants"] is None and cat["accessories"] == []
    for key in ("shirt", "pants"):
        assert key in docs["clothes"]
    assert "place" in docs["equipment"]["doc"] and "Do NOT parent" in docs["custom_items"]["doc"]


def _modules_spec():
    spec = json.loads(CATALOG.read_text(encoding="utf-8"))
    for k in ("param_docs", "options", "description"):
        spec.pop(k, None)
    spec["id"] = "crew_modules"
    spec["clothes"]["suit"] = None
    spec["clothes"]["shirt"] = {"style": "singlet", "color": [0.9, 0.9, 0.88]}
    spec["clothes"]["pants"] = {"style": "cargo", "color": [0.25, 0.28, 0.15]}
    spec["accessories"] = ["cap", "sunglasses"]
    spec["equipment"] = [{"name": "binoculars", "place": "back"}, {"name": "radio", "place": "right_hand"}]
    spec["animations"] = ["idle"]
    return spec


def _module_build(tmp_path_factory, name, change=None):
    if not Path(BLENDER).exists():
        pytest.skip("Blender not installed")
    spec = _modules_spec()
    if change:
        change(spec)
    return _build(tmp_path_factory.mktemp(name), spec)


@pytest.fixture(scope="module")
def modules_a(tmp_path_factory):
    return _module_build(tmp_path_factory, "modules_a")


@pytest.fixture(scope="module")
def modules_b(tmp_path_factory):
    """A with only the shirt module changed (style and colour)."""
    def change(spec):
        spec["clothes"]["shirt"] = {"style": "tshirt", "color": [0.7, 0.1, 0.1]}
    return _module_build(tmp_path_factory, "modules_b", change)


@pytest.fixture(scope="module")
def modules_c(tmp_path_factory):
    """A with only the binoculars' place changed."""
    def change(spec):
        spec["equipment"][0]["place"] = "chest"
    return _module_build(tmp_path_factory, "modules_c", change)


HAIR_CODE = """crew.garment("Clothes__Buzzcut", ["head"], [0.05, 0.04, 0.03], offset_m=0.004,
             z_min=crew.joint_z("head") + 0.1, hide_body=False)
"""
TORCH_CODE = """bm = crew.new_bmesh()
crew.cylinder(bm, (0, 0, 0.04), 0.018, 0.16, 8, "Z")
crew.object_from_bmesh("Torch", bm, [0.9, 0.8, 0.1])
"""
FLARE_CODE = """bm = crew.new_bmesh()
crew.cylinder(bm, (0, 0, 0), 0.02, 0.2, 8, "Z")
crew.object_from_bmesh("Equipment__Flare", bm, [0.9, 0.1, 0.05])
"""


@pytest.fixture(scope="module")
def modules_d(tmp_path_factory):
    """Suit and shirt both set (suit wins); described hair over body.hair "none"; a legacy
    equipment item with no module or place (goes to the right hand); a new one placed on the hip."""
    def change(spec):
        spec["clothes"]["suit"] = "male_worksuit01"
        spec["clothes"]["shirt"] = {"style": "tshirt", "color": [0.7, 0.1, 0.1]}
        spec["clothes"]["pants"] = None
        spec["body"]["hair"] = "none"
        spec["equipment"] = []
        spec["accessories"] = ["ear_defenders", "gloves"]
        spec["custom_items"] = [
            {"id": "buzzcut", "description": "short buzz cut", "module": "hair", "code": HAIR_CODE},
            {"id": "torch", "description": "a torch", "code": TORCH_CODE},
            {"id": "flare", "description": "a flare", "module": "equipment", "place": "hip", "code": FLARE_CODE},
        ]
    return _module_build(tmp_path_factory, "modules_d", change)


def _parents(g):
    return {g.nodes[c].name: g.nodes[i].name for i, n in enumerate(g.nodes) for c in (n.children or [])}


def _world_verts(g, node_name):
    """Rest-pose world positions (glTF frame, Y up, +X forward) of a mesh node's vertices."""
    w = _world_positions(g)[node_name]
    node = g.nodes[_node(g, node_name)]
    out = []
    for prim in g.meshes[node.mesh].primitives:
        for p in _accessor_values(g, prim.attributes.POSITION):
            out.append(tuple(sum(w[r][k] * p[k] for k in range(3)) + w[r][3] for r in range(3)))
    return out


@needs_blender
def test_modules_build_ok(modules_a):
    r = modules_a
    assert r["ok"] is True and r["_exit"] == 0, r["errors"]
    assert r["warnings"] == [], r["warnings"]
    assert 0 < r["triangles"] <= 40000
    parts = {p["path"]: p for p in json.loads((r["_out"] / "parts.json").read_text(encoding="utf-8"))}
    for name in ("Clothes__Shirt", "Clothes__Pants", "Equipment__Cap", "Equipment__Sunglasses",
                 "Equipment__binoculars", "Equipment__radio"):
        assert name in parts and parts[name]["triangles"] > 0, name
    assert "Clothes__Suit" not in parts


@needs_blender
def test_shirt_and_pants_skinned_on_the_one_skeleton(modules_a):
    g = _gltf(modules_a)
    assert len(g.skins) == 1
    assert sorted(g.nodes[j].name for j in g.skins[0].joints) == GAME_ENGINE_BONES
    for name in ("Clothes__Shirt", "Clothes__Pants"):
        n = g.nodes[_node(g, name)]
        assert n.mesh is not None and n.skin == 0 and _is_identity(n), name
    dom = _dominant(_skin_data(g, "Clothes__Pants")[1])
    for side in ("thigh_l", "thigh_r", "calf_l", "calf_r"):   # cargo = trousers: thighs and shins
        assert dom.count(side) / len(dom) > 0.05, side


@needs_blender
def test_singlet_has_no_arm_weighted_vertices(modules_a):
    """No vertex is driven mainly by an arm, neck or head bone (that would be a sleeve or collar),
    and no arm bone carries more than the garment trim (0.3) anywhere: a sleeve would carry 1.0."""
    g = _gltf(modules_a)
    _, infl = _skin_data(g, "Clothes__Shirt")
    assert not [d for d in _dominant(infl) if d.startswith(("upperarm", "lowerarm", "hand", "neck", "head"))]
    arm = max((w for names, ws in infl for n, w in zip(names, ws)
               if n.startswith(("upperarm", "lowerarm", "hand"))), default=0.0)
    assert arm <= 0.3, arm


@needs_blender
def test_cap_and_sunglasses_follow_the_head(modules_a):
    g = _gltf(modules_a)
    parent = _parents(g)
    assert parent["Equipment__Cap"] == "head" and parent["Equipment__Sunglasses"] == "head"
    head_y = _world_positions(g)["head"][1][3]
    cap = _world_verts(g, "Equipment__Cap")
    glasses = _world_verts(g, "Equipment__Sunglasses")
    assert min(p[1] for p in cap) > head_y + 0.05                 # on top of the head, not the neck
    lo, hi = min(p[1] for p in glasses), max(p[1] for p in glasses)
    face = [p for p in _world_verts(g, "Body__Skin") if lo <= p[1] <= hi and abs(p[2]) < 0.03]
    assert max(p[0] for p in glasses) > max(p[0] for p in face)   # lenses in front of the face


@needs_blender
def test_binoculars_on_the_back(modules_a):
    """place back: every vertex behind the torso surface (glTF +X is forward), on spine_03."""
    g = _gltf(modules_a)
    assert _parents(g)["Equipment__binoculars"] == "spine_03"
    bino = _world_verts(g, "Equipment__binoculars")
    lo, hi = min(p[1] for p in bino), max(p[1] for p in bino)
    torso = [p for n in ("Body__Skin", "Clothes__Shirt") for p in _world_verts(g, n)
             if lo <= p[1] <= hi and abs(p[2]) < 0.12]
    assert max(p[0] for p in bino) < min(p[0] for p in torso), (max(p[0] for p in bino), min(p[0] for p in torso))


@needs_blender
def test_radio_held_in_right_hand(modules_a):
    g = _gltf(modules_a)
    assert _parents(g)["Equipment__radio"] == "hand_r"
    ov, _ = _pose(g, "idle", 0)
    w = _world_positions(g, ov)
    radio, hand = w["Equipment__radio"], w["hand_r"]
    assert math.dist([radio[r][3] for r in range(3)], [hand[r][3] for r in range(3)]) < 0.12


def _material_sig(g, mat_idx):
    m = g.materials[mat_idx]
    pbr = m.pbrMetallicRoughness
    tex = None
    if pbr and pbr.baseColorTexture is not None:
        img = g.images[g.textures[pbr.baseColorTexture.index].source]
        bv = g.bufferViews[img.bufferView]
        tex = hash(g.binary_blob()[(bv.byteOffset or 0):(bv.byteOffset or 0) + bv.byteLength])
    return (m.name, tuple(pbr.baseColorFactor or ()) if pbr else None, pbr.roughnessFactor if pbr else None,
            pbr.metallicFactor if pbr else None, m.alphaMode, tex)


def _node_sig(g, i):
    """Everything about a node that a viewer could see: parent, transform, vertices, materials."""
    n = g.nodes[i]
    parent = _parents(g).get(n.name)
    sig = {"parent": parent, "t": n.translation, "r": n.rotation, "s": n.scale, "skin": n.skin}
    if n.mesh is not None:
        prims = g.meshes[n.mesh].primitives
        sig["verts"] = [_accessor_values(g, p.attributes.POSITION) for p in prims]
        sig["materials"] = [_material_sig(g, p.material) for p in prims]
    return sig


def _same_verts(a, b, tol=1e-6):
    return len(a) == len(b) and all(len(x) == len(y) and all(abs(p - q) <= tol for u, v in zip(x, y)
                                                              for p, q in zip(u, v)) for x, y in zip(a, b))


def _assert_only_changed(ga, gb, allowed, body_under=()):
    """Every node of the two GLBs is identical except those in ``allowed``. Body__Skin, when in
    ``body_under``, may lose or regain skin only where one of those garments covers it."""
    names_a = {n.name for n in ga.nodes}
    names_b = {n.name for n in gb.nodes}
    assert names_a - set(allowed) == names_b - set(allowed), names_a ^ names_b
    for name in sorted(names_a - set(allowed)):
        sa, sb = _node_sig(ga, _node(ga, name)), _node_sig(gb, _node(gb, name))
        if name == "Body__Skin" and body_under:
            assert sa["materials"] == sb["materials"], name
            _assert_body_differs_only_under(ga, gb, body_under)
            continue
        for k in ("parent", "t", "r", "s", "skin", "materials"):
            assert sa.get(k) == sb.get(k), (name, k, sa.get(k), sb.get(k))
        if "verts" in sa:
            assert _same_verts(sa["verts"], sb["verts"]), name


def _assert_body_differs_only_under(ga, gb, garments, reach=0.03):
    """The body's skin under a garment is trimmed (it cannot be seen and would poke through), so
    a different shirt trims a different patch. Everything else about the body must be identical:
    same bounds, and every vertex present in one build but not the other lies under a garment."""
    va = {tuple(round(c, 5) for c in p) for p in _world_verts(ga, "Body__Skin")}
    vb = {tuple(round(c, 5) for c in p) for p in _world_verts(gb, "Body__Skin")}
    for k in range(3):
        assert abs(min(p[k] for p in va) - min(p[k] for p in vb)) < 1e-5
        assert abs(max(p[k] for p in va) - max(p[k] for p in vb)) < 1e-5
    cover = {}
    for g in (ga, gb):
        for name in garments:
            if any(n.name == name for n in g.nodes):
                for p in _world_verts(g, name):
                    cover.setdefault(tuple(int(math.floor(c / reach)) for c in p), []).append(p)
    for p in va ^ vb:
        cell = [int(math.floor(c / reach)) for c in p]
        near = [q for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1)
                for q in cover.get((cell[0] + dx, cell[1] + dy, cell[2] + dz), ())]
        assert any(math.dist(p, q) < reach for q in near), ("body changed away from the garments", p)
    assert len(va & vb) > 0.5 * len(va)


@needs_blender
def test_changing_the_shirt_changes_nothing_else(modules_a, modules_b):
    """The point of modules: B is A with only clothes.shirt changed. Every other part - pants,
    cap, sunglasses, binoculars, radio, shoes, hair, eyes, every bone - is identical in the GLB:
    same vertices, same transform, same parent, same materials. Proves 'without resetting the
    entire human' at the builder level."""
    assert modules_b["ok"] is True, modules_b["errors"]
    ga, gb = _gltf(modules_a), _gltf(modules_b)
    sa, sb = _node_sig(ga, _node(ga, "Clothes__Shirt")), _node_sig(gb, _node(gb, "Clothes__Shirt"))
    assert not _same_verts(sa["verts"], sb["verts"]) and sa["materials"] != sb["materials"]  # it did change
    _assert_only_changed(ga, gb, {"Clothes__Shirt"}, body_under=("Clothes__Shirt",))


@needs_blender
def test_changing_a_place_moves_only_that_item(modules_a, modules_c):
    """C is A with only the binoculars' place changed (back -> chest): the binoculars move to the
    front of the chest; nothing else changes at all, the body included."""
    assert modules_c["ok"] is True, modules_c["errors"]
    ga, gc = _gltf(modules_a), _gltf(modules_c)
    _assert_only_changed(ga, gc, {"Equipment__binoculars"})
    back = _world_verts(ga, "Equipment__binoculars")
    chest = _world_verts(gc, "Equipment__binoculars")
    assert sum(p[0] for p in chest) / len(chest) - sum(p[0] for p in back) / len(back) > 0.2


@needs_blender
def test_suit_wins_over_shirt(modules_d):
    r = modules_d
    assert r["ok"] is True, r["errors"]
    g = _gltf(r)
    names = {n.name for n in g.nodes}
    assert "Clothes__Suit" in names and "Clothes__Shirt" not in names
    assert any("suit" in w and "shirt" in w for w in r["warnings"]), r["warnings"]


@needs_blender
def test_described_hair_replaces_bald_preset(modules_d):
    g = _gltf(modules_d)
    names = {n.name for n in g.nodes}
    assert "Head__Hair" not in names
    n = g.nodes[_node(g, "Clothes__Buzzcut")]
    assert n.mesh is not None and n.skin == 0


@needs_blender
def test_ear_defenders_and_gloves(modules_d):
    g = _gltf(modules_d)
    assert _parents(g)["Equipment__Ear_defenders"] == "head"
    dom = _dominant(_skin_data(g, "Clothes__Gloves")[1])
    assert all(d.startswith(("hand", "thumb", "index", "middle", "ring", "pinky")) for d in dom)


@needs_blender
def test_equipment_items_are_placed_by_the_builder(modules_d):
    """A legacy item (no module, no place, not parented) goes to the right hand; a new one with
    place hip goes on the pelvis at the right side (Blender -Y is glTF +Z)."""
    g = _gltf(modules_d)
    parent = _parents(g)
    assert parent["Equipment__Torch"] == "hand_r"
    assert parent["Equipment__Flare"] == "pelvis"
    w = _world_positions(g)
    assert w["Equipment__Flare"][2][3] - w["pelvis"][2][3] > 0.1


def test_a_bare_spec_is_dressed_by_the_builder():
    # Lester, 29 Oct: "human models should always be clothed and have a default shirt and pants".
    from crew.spec import CLOTHED_DEFAULT, normalize
    spec, errors, warnings = normalize({"schema": "me3d.crew_spec/1", "id": "bare",
                                        "clothes": {"suit": None, "shirt": None, "pants": None}})
    assert not errors
    assert spec["clothes"]["shirt"] == CLOTHED_DEFAULT["shirt"] and spec["clothes"]["pants"] == CLOTHED_DEFAULT["pants"]
    assert any("trousers" in w for w in warnings)
    # a described top counts; the suit counts for both
    spec, _, _ = normalize({"schema": "me3d.crew_spec/1", "id": "x", "clothes": {"suit": None},
                            "custom_items": [{"id": "item_1", "module": "shirt", "description": "vest",
                                              "code": "crew.garment('Clothes__Vest', ['torso'], (1, 1, 1))"}]})
    assert spec["clothes"]["shirt"] is None and spec["clothes"]["pants"] == CLOTHED_DEFAULT["pants"]
    spec, _, _ = normalize({"schema": "me3d.crew_spec/1", "id": "y"})
    assert spec["clothes"]["shirt"] is None and spec["clothes"]["suit"]
