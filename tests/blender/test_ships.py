"""Ship builders, end to end through Blender (slow: runs Blender headless).

    server/.venv/bin/python -m pytest tests/blender/test_ships.py -q

Every geometric check is measured on the EXPORTED model.glb re-imported into a fresh Blender,
using each object's matrix_world, never raw glTF node translations: a check that reads only a
node's own translation measures rotated or parented children wrongly (library:
glb-contract-validator-reads-node-translation-only-so-rigs-export-flattened).
"""

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "server"))
from app.config import DEFAULT_BLENDER  # noqa: E402

import pytest
from pygltflib import GLTF2

ROOT = Path(__file__).resolve().parents[2]
BLENDER = os.environ.get("BLENDER", str(DEFAULT_BLENDER))
BUILD = ROOT / "blender" / "build_ship.py"
TYPES = ("container", "sampan", "tanker")
# the general "custom" builder: its catalog default plus three vessels made from params alone
CUSTOM = ("custom", "custom_yacht", "custom_tug", "custom_rhib")
ALL = TYPES + CUSTOM
BUDGET = {"container": 200_000, "tanker": 200_000, "sampan": 50_000,
          **{c: 150_000 for c in CUSTOM}}
SOCKETS = ("socket_bridge_eye", "socket_radar", "socket_light_masthead",
           "socket_light_masthead_aft", "socket_light_port", "socket_light_stbd",
           "socket_light_stern", "socket_bow", "socket_prop")
STATIONS = {
    "container": {"station__helm", "station__bridge_wing_port", "station__bridge_wing_stbd",
                  "station__deck_fwd", "station__deck_aft"},
    "tanker": {"station__helm", "station__bridge_wing_port", "station__bridge_wing_stbd",
               "station__deck_fwd", "station__deck_aft"},
    "sampan": {"station__helm", "station__bow", "station__passenger_1"},
    "custom": {"station__helm", "station__deck_fwd", "station__deck_aft"},
    "custom_yacht": {"station__helm", "station__deck_fwd", "station__deck_aft"},
    "custom_tug": {"station__helm", "station__deck_fwd", "station__deck_aft"},
    "custom_rhib": {"station__helm", "station__deck_fwd", "station__deck_aft"},
}
JOINTS = {  # moving parts each ship must have, with their axis
    "container": {"Mast__Radar_1": "z", "Lifeboat__Davit_Port": "x", "Lifeboat__Davit_Stbd": "x",
                  "Hull__Rudder": "z"},
    "tanker": {"Mast__Radar_1": "z", "Deck__Provision_Crane": "z",
               "Deck__Provision_Crane__Jib": "y", "Lifeboat__Davit_Port": "x",
               "Hull__Rudder": "z"},
    "sampan": {"Transom__Outboard": "z", "Transom__Outboard_Tilt": "y"},
    "custom": {"Radar__Scanner": "z", "Hull__Rudder_1": "z", "Hull__Rudder_2": "z"},
    "custom_yacht": {"Radar__Scanner": "z", "Hull__Rudder_1": "z", "Hull__Rudder_2": "z"},
    "custom_tug": {"Radar__Scanner": "z", "Hull__Azimuth_1": "z", "Hull__Azimuth_2": "z"},
    "custom_rhib": {"Radar__Scanner": "z", "Transom__Outboard_1": "z", "Transom__Outboard_2": "z"},
}
STATION_TOL = 0.3   # metres between a station and the deck surface under it

MEASURE = r'''
import bpy, json, sys, math
from mathutils import Vector
glb, out = sys.argv[sys.argv.index("--") + 1:]
for ob in list(bpy.data.objects):
    bpy.data.objects.remove(ob, do_unlink=True)
bpy.ops.import_scene.gltf(filepath=glb)
bpy.context.view_layer.update()
dg = bpy.context.evaluated_depsgraph_get()
res = {"objects": {}, "tris": 0}
allp = []
for ob in bpy.data.objects:
    d = {"parent": ob.parent.name if ob.parent else None, "type": ob.type,
         "world": list(ob.matrix_world.translation),
         "forward": list(ob.matrix_world.to_3x3() @ Vector((1, 0, 0))),
         "props": {k: ob[k] for k in ob.keys() if k.startswith("me3d")}}
    if ob.type == "MESH":
        me = ob.evaluated_get(dg).to_mesh()
        pts = [ob.matrix_world @ v.co for v in me.vertices]
        d["tris"] = sum(len(p.vertices) - 2 for p in me.polygons)
        res["tris"] += d["tris"]
        ob.evaluated_get(dg).to_mesh_clear()
        if pts:
            d["min"] = [min(p[i] for p in pts) for i in range(3)]
            d["max"] = [max(p[i] for p in pts) for i in range(3)]
            allp += pts
    res["objects"][ob.name] = d
res["min"] = [min(p[i] for p in allp) for i in range(3)]
res["max"] = [max(p[i] for p in allp) for i in range(3)]
# deck under each station: cast down from just above it
res["deck_hits"] = {}
for name, d in res["objects"].items():
    if name.startswith("station__"):
        o = Vector(d["world"]) + Vector((0, 0, 0.29))
        hit, loc, *_ = bpy.context.scene.ray_cast(dg, o, Vector((0, 0, -1)), distance=0.6)
        res["deck_hits"][name] = loc.z if hit else None
json.dump(res, open(out, "w"))
'''


def run_build(spec, out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    spec_path = out_dir / "spec.json"
    spec_path.write_text(json.dumps(spec))
    proc = subprocess.run(
        [BLENDER, "-b", "--factory-startup", "--python", str(BUILD), "--",
         "--spec", str(spec_path), "--out", str(out_dir)],
        capture_output=True, text=True, timeout=180)
    report = json.loads((out_dir / "build_report.json").read_text(encoding="utf-8"))
    parts_file = out_dir / "parts.json"
    parts = json.loads(parts_file.read_text(encoding="utf-8")) if parts_file.exists() else []
    return proc, report, parts


def measure(glb):
    glb = Path(glb)
    script = glb.parent / "_measure.py"
    script.write_text(MEASURE)
    out = glb.parent / "measure.json"
    proc = subprocess.run([BLENDER, "-b", "--factory-startup", "--python", str(script), "--",
                           str(glb), str(out)], capture_output=True, text=True, timeout=180)
    assert out.exists(), proc.stdout[-3000:] + proc.stderr[-3000:]
    return json.loads(out.read_text(encoding="utf-8"))


def catalog(t):
    return json.loads((ROOT / "catalog" / "ships" / f"{t}.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------------------------------------
# Three vessels from the general custom builder, by params alone (no custom ops): the range
# it has to cover is "recognisably that kind of vessel at simulator distances".
# ------------------------------------------------------------------------------------------

WHITE, TINT = [0.94, 0.94, 0.93], [0.04, 0.05, 0.07]
EXAMPLES = {
    # ~35 m modern motor yacht: sharp raked bow, white hull, dark window bands, hull windows,
    # two closed superstructure tiers and an open fly-bridge with a radar arch
    "custom_yacht": {
        "loa_m": 35.0, "beam_m": 7.4, "depth_m": 4.1, "draught_m": 2.0,
        "bow_form": "raked", "stem_rake_deg": 40, "stern_form": "swim_platform",
        "transom_rake_deg": 12, "swim_platform_m": 1.8, "swim_platform_height_m": 0.35,
        "sheer_fwd_m": 0.9, "sheer_aft_m": 0.0, "deadrise_deg": 14, "bilge": 0.15,
        "fullness": 0.25,
        "hull_color": WHITE, "boot_color": [0.08, 0.1, 0.16], "boot_top_color": [0.2, 0.21, 0.24],
        "boot_top_m": 0.3, "deck_color": [0.58, 0.43, 0.28],
        "bulwark_m": 0.95, "bulwark_color": None, "rail_m": 0.0, "fender_m": 0.0,
        "hull_windows": {"x_aft_m": -7.5, "x_fwd_m": 5.5, "z_m": 1.3, "height_m": 0.42,
                         "color": TINT},
        "tiers": [
            {"x_aft_m": -10.5, "x_fwd_m": 7.5, "width_m": 6.9, "height_m": 2.6, "setback_m": 0.2,
             "rake_deg": 40, "round": 0.6, "overhang_m": 0.25, "color": WHITE,
             "windows": {"color": TINT, "bottom_m": 0.7, "height_m": 1.5}},
            {"x_aft_m": -8.5, "x_fwd_m": 3.5, "width_m": 6.0, "height_m": 2.4, "setback_m": 0.25,
             "rake_deg": 45, "round": 0.75, "overhang_m": 0.35, "overhang_aft_m": 2.5,
             "color": WHITE, "windows": {"color": TINT, "bottom_m": 0.6, "height_m": 1.4}},
            {"x_aft_m": -6.0, "x_fwd_m": 1.0, "width_m": 5.4, "height_m": 1.0, "rake_deg": 25,
             "round": 0.6, "open": True, "color": WHITE,
             "windows": {"color": TINT, "bottom_m": 0.45, "height_m": 0.5}},
        ],
        "helm_tier": 2, "mast": False, "radar": True,
        "radar_arch": True, "arch_x_m": -4.0, "arch_height_m": 2.4, "arch_width_m": 4.4,
        "arch_rake_deg": 20, "arch_color": WHITE, "funnel": False,
        "propulsion": "shaft", "engines": 2, "strait_type": "ferry",
    },
    # ~30 m harbour tug (ASD): high flared bow, round bilge, heavy fendering, a deckhouse with the
    # wheelhouse raised on a short neck, all-round windows flaring out, twin funnels, mast, two
    # azimuth pods
    "custom_tug": {
        "loa_m": 30.0, "beam_m": 11.0, "depth_m": 5.8, "draught_m": 4.4,
        "bow_form": "flared", "stem_rake_deg": 15, "stern_form": "cruiser",
        "sheer_fwd_m": 1.8, "sheer_aft_m": 0.1, "deadrise_deg": 6, "bilge": 1.0, "fullness": 0.7,
        "hull_color": [0.1, 0.12, 0.16], "boot_color": [0.5, 0.1, 0.08],
        "boot_top_color": [0.92, 0.92, 0.9], "boot_top_m": 0.35, "deck_color": [0.45, 0.15, 0.12],
        "bulwark_m": 1.0, "bulwark_color": [0.1, 0.12, 0.16], "rail_m": 0.0,
        "fender_m": 0.7, "fender_color": [0.05, 0.05, 0.05], "hull_windows": None,
        "tiers": [
            {"x_aft_m": -4.0, "x_fwd_m": 7.0, "width_m": 7.8, "height_m": 2.5, "setback_m": 0.1,
             "rake_deg": 5, "round": 0.2, "color": WHITE,
             "windows": {"color": TINT, "bottom_m": 1.1, "height_m": 0.6}},
            {"x_aft_m": 1.2, "x_fwd_m": 4.8, "width_m": 4.6, "height_m": 1.5, "color": WHITE},
            {"x_aft_m": 0.5, "x_fwd_m": 5.5, "width_m": 8.0, "height_m": 2.7, "setback_m": -0.3,
             "rake_deg": -12, "round": 0.2, "overhang_m": 0.3, "color": WHITE,
             "windows": {"color": TINT, "bottom_m": 0.9, "height_m": 1.5, "aft": True}},
        ],
        "mast": True, "mast_x_m": 3.0, "mast_height_m": 4.5, "radar": True,
        "radar_arch": False, "funnel": True, "funnel_count": 2, "funnel_x_m": -2.6,
        "funnel_height_m": 3.2, "funnel_length_m": 1.6, "funnel_width_m": 0.9,
        "funnel_rake_deg": 6, "funnel_color": [0.95, 0.6, 0.1],
        "funnel_top_color": [0.05, 0.05, 0.05],
        "propulsion": "azimuth", "engines": 2, "strait_type": "tug",
    },
    # ~12 m patrol RHIB: deep-V hard-chine hull inside an inflatable collar that runs past the
    # open transom, a small console with a windscreen, a radar arch over the helm, two outboards
    "custom_rhib": {
        "loa_m": 12.0, "beam_m": 3.5, "depth_m": 1.5, "draught_m": 0.75,
        "bow_form": "raked", "stem_rake_deg": 28, "stern_form": "open", "transom_rake_deg": 10,
        "sheer_fwd_m": 0.35, "sheer_aft_m": 0.0, "deadrise_deg": 24, "bilge": 0.0,
        "fullness": 0.3,
        "hull_color": [0.32, 0.34, 0.36], "boot_color": [0.12, 0.12, 0.13],
        "boot_top_color": [0.12, 0.12, 0.13], "boot_top_m": 0.15,
        "deck_color": [0.25, 0.26, 0.27], "bulwark_m": 0.0, "rail_m": 0.0,
        "fender_m": 0.6, "fender_color": [0.17, 0.17, 0.18], "hull_windows": None,
        "tiers": [
            {"x_aft_m": -1.6, "x_fwd_m": 0.4, "width_m": 1.6, "height_m": 1.25, "setback_m": 0.1,
             "rake_deg": 35, "round": 0.4, "color": [0.3, 0.32, 0.34],
             "windows": {"color": TINT, "bottom_m": 0.75, "height_m": 0.45}},
        ],
        "helm_tier": 0, "mast": False, "radar": True,
        "radar_arch": True, "arch_x_m": -2.0, "arch_height_m": 2.3, "arch_width_m": 2.2,
        "arch_rake_deg": 6, "arch_color": [0.3, 0.32, 0.34], "funnel": False,
        "propulsion": "outboard", "engines": 2, "strait_type": "patrol",
    },
}
EXPECT_PARTS = {  # parts each custom vessel must have (contract section 2 names)
    "custom": {"Hull__Main", "Deck__Main", "Hull__Bulwark", "Hull__Fender",
               "Superstructure__Tier_1", "Mast__Main", "Radar__Scanner",
               "Hull__Propeller_1", "Hull__Propeller_2"},
    "custom_yacht": {"Hull__Main", "Deck__Main", "Hull__Bulwark", "Hull__Swim_Platform",
                     "Hull__Windows", "Superstructure__Tier_1", "Superstructure__Tier_2",
                     "Superstructure__Tier_3", "Arch__Radar", "Radar__Scanner"},
    "custom_tug": {"Hull__Main", "Deck__Main", "Hull__Bulwark", "Hull__Fender",
                   "Superstructure__Tier_1", "Superstructure__Tier_2", "Superstructure__Tier_3",
                   "Funnel__Port", "Funnel__Stbd", "Mast__Main", "Radar__Scanner"},
    "custom_rhib": {"Hull__Main", "Deck__Main", "Hull__Fender", "Superstructure__Tier_1",
                    "Arch__Radar", "Radar__Scanner"},
}


def example_spec(name):
    """A full custom spec: the catalog default with an example's params laid over it."""
    spec = copy.deepcopy(catalog("custom"))
    spec["id"] = name
    spec["params"].update(copy.deepcopy(EXAMPLES[name]))
    return spec


def spec_for(t):
    return example_spec(t) if t in EXAMPLES else catalog(t)


def station_offenders(m):
    """Stations with no deck surface within STATION_TOL below/at them."""
    bad = []
    for name, hit in m["deck_hits"].items():
        z = m["objects"][name]["world"][2]
        if hit is None or abs(hit - z) > STATION_TOL:
            bad.append((name, z, hit))
    return bad


def glb_nodes(path):
    g = GLTF2().load(str(path))
    names = {n.name for n in g.nodes}
    parent = {}
    for i, n in enumerate(g.nodes):
        for c in n.children or []:
            parent[g.nodes[c].name] = n.name
    return g, names, parent


_CACHE = {}


@pytest.fixture(scope="module")
def ship(tmp_path_factory):
    def get(t):
        if t not in _CACHE:
            out = tmp_path_factory.mktemp(t)
            proc, report, parts = run_build(spec_for(t), out)
            _CACHE[t] = {"proc": proc, "report": report, "parts": parts, "out": out,
                         "m": measure(out / "model.glb") if report["ok"] else None}
        return _CACHE[t]
    return get


@pytest.mark.parametrize("t", ALL)
def test_build_ok_and_files(ship, t):
    s = ship(t)
    assert s["report"]["ok"], s["report"]["errors"]
    assert s["proc"].returncode == 0
    for f in ("model.glb", "model.blend", "parts.json", "build_report.json"):
        assert (s["out"] / f).stat().st_size > 0, f
    assert set(s["report"]) >= {"ok", "errors", "warnings", "triangles", "bounds_min",
                                "bounds_max", "seconds"}


@pytest.mark.parametrize("t", ALL)
def test_glb_has_every_part_and_only_legal_names(ship, t):
    s = ship(t)
    _, names, _ = glb_nodes(s["out"] / "model.glb")
    paths = {p["path"] for p in s["parts"]}
    assert paths <= names, sorted(paths - names)
    import re
    seg = r"[A-Za-z0-9]+(?:_[A-Za-z0-9]+)*"
    legal = re.compile(rf"^(?:{seg}__{seg}(?:__{seg})?|station__[a-z0-9_]+|socket_[a-z_]+)$")
    assert all(legal.match(n) for n in names), [n for n in names if not legal.match(n)]
    for p in s["parts"]:
        assert set(p) >= {"path", "label", "parent", "joint", "station", "origin_m",
                          "rotation_deg", "bounds_min", "bounds_max", "triangles"}
        assert p["label"] == p["path"].replace("__", " › ").replace("_", " ")


@pytest.mark.parametrize("t", ALL)
def test_principal_dimensions(ship, t):
    s, prm = ship(t), spec_for(t)["params"]
    m = s["m"]
    loa = m["max"][0] - m["min"][0]
    assert abs(loa - prm["loa_m"]) <= 0.02 * prm["loa_m"], loa
    assert abs(m["min"][2] + prm["draught_m"]) <= 0.05 * prm["draught_m"], m["min"][2]
    assert m["max"][2] > 0.5, "nothing above the waterline"
    beam = m["max"][1] - m["min"][1]
    assert abs(beam - prm["beam_m"]) <= 0.05 * prm["beam_m"], beam
    assert abs((m["max"][0] + m["min"][0]) / 2) < 0.06 * loa, "origin not near amidships"


@pytest.mark.parametrize("t", ALL)
def test_triangle_budget(ship, t):
    s = ship(t)
    assert s["m"]["tris"] <= BUDGET[t], s["m"]["tris"]
    assert s["report"]["triangles"] <= BUDGET[t]


@pytest.mark.parametrize("t", ALL)
def test_stations_stand_on_deck(ship, t):
    s = ship(t)
    m = s["m"]
    found = {n for n in m["objects"] if n.startswith("station__")}
    assert STATIONS[t] <= found, STATIONS[t] - found
    assert station_offenders(m) == []
    by_path = {p["path"]: p for p in s["parts"]}
    for name in found:
        p = by_path[name]
        o = m["objects"][name]
        assert p["parent"] and p["parent"] in by_path, name
        assert o["parent"] == p["parent"], name  # hierarchy survived into glTF
        st = p["station"]
        assert st["pose"] in ("stand", "sit") and st["label"] and isinstance(st["activities"], list)
        # parts.json origin is the station's world position, which crew placement relies on
        assert all(abs(a - b) < 0.01 for a, b in zip(p["origin_m"], o["world"])), name
        assert abs(p["origin_m"][2] - m["deck_hits"][name]) <= STATION_TOL, name
        assert len(p["rotation_deg"]) == 3
        assert o["forward"][2] == pytest.approx(0, abs=1e-3), "station tilted"
    assert by_path["station__helm"]["station"]["pose"] == ("sit" if t == "sampan" else "stand")


@pytest.mark.parametrize("t", ALL)
def test_sockets(ship, t):
    s, prm = ship(t), spec_for(t)["params"]
    _, names, _ = glb_nodes(s["out"] / "model.glb")
    need = [x for x in SOCKETS if prm["loa_m"] >= 50 or x != "socket_light_masthead_aft"]
    assert set(need) <= names, set(need) - names
    m = s["m"]
    bow = m["objects"]["socket_bow"]["world"]
    assert abs(bow[2]) < 0.05 and bow[0] > 0.4 * prm["loa_m"] * 0.5
    assert m["objects"]["socket_prop"]["world"][2] < 0
    assert m["objects"]["socket_light_port"]["world"][1] > 0 > m["objects"]["socket_light_stbd"]["world"][1]


@pytest.mark.parametrize("t", ALL)
def test_joints(ship, t):
    s = ship(t)
    m = s["m"]
    _, names, gparent = glb_nodes(s["out"] / "model.glb")
    joints = {p["path"]: p for p in s["parts"] if p["joint"]}
    for name, axis in JOINTS[t].items():
        assert name in joints, name
        assert joints[name]["joint"]["axis"] == axis
    for name, p in joints.items():
        j = p["joint"]
        assert j["kind"] == "revolute" and j["priority"] in ("P0", "P1", "P2")
        assert j["min_deg"] <= j["max_deg"]
        assert name in names and gparent.get(name), f"{name} has no parent in the GLB"
        assert json.loads(m["objects"][name]["props"]["me3d_joint"]) == j  # survives as extras
        # origin_m is the pivot, and the pivot survived export
        assert all(abs(a - b) < 0.01 for a, b in zip(p["origin_m"], m["objects"][name]["world"]))


# ------------------------------------------------------------------------------------------
# Spec edits: overrides, custom ops, failures
# ------------------------------------------------------------------------------------------

HORN = """
me3d.box("Mast__Horn", center=(0.0, 0.0, 3.0), size=(0.3, 0.2, 0.2), color=(0.9, 0.9, 0.1),
         parent="Hull__Main")
"""


@pytest.fixture(scope="module")
def edited(ship, tmp_path_factory):
    spec = catalog("sampan")
    spec["overrides"] = {"Canopy__Awning": {"offset_m": [0, 0, 0.5]},
                         "Transom__Outboard_Tilt": {"rotate_deg": [0, 60, 0]}}
    spec["joint_limits"] = {"Transom__Outboard": {"min_deg": -20, "max_deg": 20}}
    spec["custom_ops"] = [{"id": "op_1", "description": "add a horn on the mast", "code": HORN}]
    out = tmp_path_factory.mktemp("sampan_edited")
    proc, report, parts = run_build(spec, out)
    return {"proc": proc, "report": report, "parts": parts, "out": out,
            "m": measure(out / "model.glb"), "base": ship("sampan")}


def test_override_moves_part_and_its_socket(edited):
    assert edited["report"]["ok"], edited["report"]["errors"]
    b, e = edited["base"]["m"]["objects"], edited["m"]["objects"]
    assert e["Canopy__Awning"]["max"][2] == pytest.approx(b["Canopy__Awning"]["max"][2] + 0.5,
                                                          abs=0.01)
    assert e["socket_radar"]["world"][2] == pytest.approx(b["socket_radar"]["world"][2] + 0.5,
                                                          abs=0.01)
    pj = {p["path"]: p for p in edited["parts"]}
    pb = {p["path"]: p for p in edited["base"]["parts"]}
    assert pj["Canopy__Awning"]["bounds_max"][2] == pytest.approx(
        pb["Canopy__Awning"]["bounds_max"][2] + 0.5, abs=0.01)
    assert pj["Transom__Outboard"]["joint"]["min_deg"] == -20


def test_tilted_outboard_is_measured_through_the_chain(edited):
    """Tilting the outboard 60 degrees lifts the propeller (a grandchild) out of the water.
    The propeller's own glTF node translation does not change at all, so only a world-space
    measurement sees it; parts.json must report the world position too."""
    b, e = edited["base"]["m"]["objects"], edited["m"]["objects"]
    zb, ze = b["Transom__Outboard__Propeller"]["world"][2], e["Transom__Outboard__Propeller"]["world"][2]
    assert ze > zb + 0.3, (zb, ze)
    g0, _, _ = glb_nodes(edited["base"]["out"] / "model.glb")
    g1, _, _ = glb_nodes(edited["out"] / "model.glb")
    t0 = next(n.translation for n in g0.nodes if n.name == "Transom__Outboard__Propeller")
    t1 = next(n.translation for n in g1.nodes if n.name == "Transom__Outboard__Propeller")
    assert t0 == pytest.approx(t1, abs=1e-5)  # the raw node translation is blind to the tilt
    pj = {p["path"]: p for p in edited["parts"]}
    assert pj["Transom__Outboard__Propeller"]["origin_m"][2] == pytest.approx(ze, abs=0.01)
    assert pj["Transom__Outboard_Tilt"]["rotation_deg"][1] == pytest.approx(60, abs=0.1)
    assert e["socket_prop"]["world"][2] == pytest.approx(ze, abs=0.01)


def test_custom_op_adds_a_part(edited):
    _, names, gparent = glb_nodes(edited["out"] / "model.glb")
    assert "Mast__Horn" in names and gparent["Mast__Horn"] == "Hull__Main"
    horn = next(p for p in edited["parts"] if p["path"] == "Mast__Horn")
    assert horn["label"] == "Mast › Horn" and horn["triangles"] == 12
    assert horn["bounds_max"][2] == pytest.approx(3.1, abs=1e-3)


def test_broken_custom_op_fails_loudly(tmp_path):
    spec = catalog("sampan")
    spec["custom_ops"] = [{"id": "op_bad", "description": "broken", "code":
                           "x = 1\nraise ValueError('boom horn')\n"}]
    proc, report, _ = run_build(spec, tmp_path)
    assert report["ok"] is False
    assert proc.returncode == 1
    assert any("op_bad" in e and "boom horn" in e and "line 2" in e for e in report["errors"]), \
        report["errors"]


def test_custom_op_with_illegal_name_fails(tmp_path):
    spec = catalog("sampan")
    spec["custom_ops"] = [{"id": "op_name", "description": "bad name", "code":
                           "me3d.MeshBuilder().box((0,0,3),(1,1,1),(1,0,0))\n"
                           "import bpy\nob = bpy.data.objects.new('my horn.001', None)\n"
                           "bpy.context.scene.collection.objects.link(ob)\n"}]
    proc, report, _ = run_build(spec, tmp_path)
    assert report["ok"] is False and proc.returncode == 1
    assert any("my horn.001" in e for e in report["errors"]), report["errors"]


def test_station_check_catches_a_floating_station(tmp_path):
    """The on-deck check can fail: lift the bowman's station 1 m off the foredeck."""
    spec = catalog("sampan")
    spec["custom_ops"] = [{"id": "op_lift", "description": "lift the bow station",
                           "code": "bpy.data.objects['station__bow'].location.z += 1.0\n"}]
    proc, report, _ = run_build(spec, tmp_path)
    assert report["ok"], report["errors"]
    bad = station_offenders(measure(tmp_path / "model.glb"))
    assert [b[0] for b in bad] == ["station__bow"], bad


def test_unknown_type_and_bad_param_fail(tmp_path):
    spec = catalog("sampan")
    spec["type"] = "submarine"
    proc, report, _ = run_build(spec, tmp_path / "a")
    assert not report["ok"] and proc.returncode == 1 and "submarine" in report["errors"][0]
    spec = copy.deepcopy(catalog("container"))
    spec["params"]["loa_m"] = 10
    proc, report, _ = run_build(spec, tmp_path / "b")
    assert not report["ok"] and "loa_m" in report["errors"][0]


# ------------------------------------------------------------------------------------------
# The general custom builder
# ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("t", CUSTOM)
def test_custom_has_its_parts(ship, t):
    s = ship(t)
    _, names, gparent = glb_nodes(s["out"] / "model.glb")
    assert EXPECT_PARTS[t] <= names, sorted(EXPECT_PARTS[t] - names)
    # the superstructure is a chain: each tier hangs off the one below, tier 1 off the hull
    n = len(spec_for(t)["params"]["tiers"])
    for k in range(1, n + 1):
        want = "Hull__Main" if k == 1 else f"Superstructure__Tier_{k - 1}"
        assert gparent.get(f"Superstructure__Tier_{k}") == want, k
    assert f"Superstructure__Tier_{n + 1}" not in names


@pytest.mark.parametrize("t", CUSTOM)
def test_custom_tiers_stack_and_match_their_params(ship, t):
    """Each tier's floor is the roof of the one below; each is as tall as its height_m and no
    wider than its width_m; tier 1 stands on the deck."""
    s, prm = ship(t), spec_for(t)["params"]
    o = s["m"]["objects"]
    pj = {p["path"]: p for p in s["parts"]}
    tiers = prm["tiers"]
    for k, tier in enumerate(tiers, start=1):
        name = f"Superstructure__Tier_{k}"
        base = o[name]["world"][2]                       # the pivot is the tier's floor
        assert pj[name]["origin_m"][2] == pytest.approx(base, abs=0.01)
        assert o[name]["max"][2] == pytest.approx(base + tier["height_m"], abs=0.01), name
        if k > 1:
            below = o[f"Superstructure__Tier_{k - 1}"]
            assert base == pytest.approx(below["max"][2], abs=0.01), name
            assert o[name]["min"][2] == pytest.approx(base, abs=0.01), name
        half = max(abs(o[name]["min"][1]), abs(o[name]["max"][1]))
        widest = tier["width_m"] / 2 + max(0.0, -tier.get("setback_m", 0.0)) \
            + tier.get("overhang_m", 0.0) + 0.04
        assert half <= widest, (name, half, widest)
        assert o[name]["min"][0] >= tier["x_aft_m"] - tier.get("overhang_aft_m", 0) - 0.4, name
    deck = o["Deck__Main"]
    t1 = o["Superstructure__Tier_1"]
    assert deck["min"][2] - 0.05 <= t1["world"][2] <= deck["max"][2] + 0.01


@pytest.mark.parametrize("t", CUSTOM)
def test_custom_freeboard_matches_depth(ship, t):
    """The lowest point of the deck (amidships, where sheer is zero) is depth - draught up."""
    s, prm = ship(t), spec_for(t)["params"]
    deck = s["m"]["objects"]["Deck__Main"]
    fb = prm["depth_m"] - prm["draught_m"]
    assert deck["min"][2] == pytest.approx(fb - 0.02, abs=0.03), deck["min"][2]
    # and the bow is higher by the sheer
    assert deck["max"][2] == pytest.approx(fb + prm["sheer_fwd_m"] + 0.02, abs=0.1), deck["max"][2]


def test_custom_examples_differ_where_their_params_do(ship):
    """The forms are not cosmetic: the RHIB's tube runs past its open transom and the yacht's
    swim platform is the aft-most thing, at the height asked for."""
    rh = ship("custom_rhib")["m"]["objects"]
    y = ship("custom_yacht")
    tug = ship("custom_tug")
    assert rh["Hull__Fender"]["min"][0] < rh["Hull__Main"]["min"][0] - 0.3
    yo = y["m"]["objects"]
    assert yo["Hull__Swim_Platform"]["min"][0] == pytest.approx(y["m"]["min"][0], abs=0.01)
    assert yo["Hull__Swim_Platform"]["max"][2] == pytest.approx(0.35, abs=0.01)
    # the tug's round-bilge hull is beamier for its length than the yacht's
    tm, ym = tug["m"], y["m"]
    assert (tm["max"][1] - tm["min"][1]) / (tm["max"][0] - tm["min"][0]) > \
        1.5 * (ym["max"][1] - ym["min"][1]) / (ym["max"][0] - ym["min"][0])

def _bad(tmp_path, sub, **patch):
    spec = catalog("custom")
    spec["params"].update(patch)
    proc, report, _ = run_build(spec, tmp_path / sub)
    assert report["ok"] is False and proc.returncode == 1
    return " ".join(report["errors"])


def test_custom_rejects_nonsense(tmp_path):
    assert "loa_m" in _bad(tmp_path, "neg", loa_m=-20)
    tier = dict(catalog("custom")["params"]["tiers"][0], width_m=9.0)   # beam is 6 m
    err = _bad(tmp_path, "wide", tiers=[tier])
    assert "tiers[1].width_m" in err and "wider than the beam" in err
    assert "bow_form" in _bad(tmp_path, "bow", bow_form="clipper")
    assert "draught_m" in _bad(tmp_path, "deep", draught_m=3.5)          # depth is 2.9 m
    assert "helm_tier" in _bad(tmp_path, "helm", helm_tier=3)


def test_custom_default_is_a_plain_workboat():
    """A new custom project builds instantly as a ~20 m workboat with one wheelhouse."""
    prm = catalog("custom")["params"]
    assert 15 <= prm["loa_m"] <= 25 and len(prm["tiers"]) == 1
    assert set(catalog("custom")["param_docs"]) == set(prm)


VARIANTS = {  # every bow, stern and propulsion option, plus rail and arch, over the default boat
    "plumb_cruiser_none_rail": {"bow_form": "plumb", "stern_form": "cruiser", "propulsion": "none",
                                "bulwark_m": 0.0, "rail_m": 1.0, "fender_m": 0.0},
    "flared_platform_jet_funnel": {"bow_form": "flared", "stern_form": "swim_platform",
                                   "propulsion": "waterjet", "funnel": True, "funnel_x_m": -0.5},
    "bulbous_transom_shaft_60m": {"bow_form": "bulbous", "stern_form": "transom",
                                  "propulsion": "shaft", "engines": 1, "loa_m": 60.0,
                                  "beam_m": 12.0, "depth_m": 7.0, "draught_m": 3.5},
    "axe_open_outboards_arch": {"bow_form": "axe", "stern_form": "open", "propulsion": "outboard",
                                "engines": 3, "rail_m": 0.9, "radar_arch": True, "mast": False},
}


@pytest.mark.parametrize("v", sorted(VARIANTS))
def test_custom_variants_build_true_to_size(tmp_path, v):
    spec = catalog("custom")
    spec["params"].update(VARIANTS[v])
    prm = spec["params"]
    proc, report, parts = run_build(spec, tmp_path)
    assert report["ok"] and proc.returncode == 0, report["errors"]
    assert report["warnings"] == [], report["warnings"]   # e.g. "socket ... missing"
    lo, hi = report["bounds_min"], report["bounds_max"]
    assert abs(hi[0] - lo[0] - prm["loa_m"]) <= 0.02 * prm["loa_m"], (lo, hi)
    assert abs(hi[1] - lo[1] - prm["beam_m"]) <= 0.05 * prm["beam_m"], (lo, hi)
    assert lo[2] == pytest.approx(-prm["draught_m"], abs=0.05 * prm["draught_m"])
    paths = {p["path"] for p in parts}
    assert ("Hull__Rail" in paths) == (prm["rail_m"] > 0)
    assert ("Hull__Bulb" in paths) == (prm["bow_form"] == "bulbous")
    assert ("Arch__Radar" in paths) == prm["radar_arch"]


def test_container_ship_with_an_empty_deck_builds(tmp_path):
    """No bays and no cranes: a cleared deck, as a plan for a hull that is not a container ship may set."""
    spec = copy.deepcopy(catalog("container"))
    spec["params"].update({"bays": 0, "cranes": 0, "tiers": 0})
    proc, report, parts = run_build(spec, tmp_path)
    assert report["ok"], report["errors"]
    assert not [p for p in parts if p["path"].startswith("Cargo__")]
