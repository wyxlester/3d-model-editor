"""Texture bake (blender/bake_textures.py, blender/texturing/): plan Step 2.

    server/.venv/bin/python -m pytest tests/blender/test_bake.py -q

The first half is plain Python (finish mapping, atlas and page arithmetic, island grouping) and
runs in a second. ``test_bake_small_ship`` builds the catalog's custom ship with a marking on its
deck, bakes it at 1/16 of the stock page sizes, and checks the textured glb (slow: runs Blender).
"""

import hashlib
import json
import os
import struct
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "blender"))
from texturing import atlas, finishes  # noqa: E402


# ------------------------------------------------------------------------------------------
# Finishes
# ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("mat,expected", [
    ("deck", "deck_nonskid"), ("deck.001", "deck_nonskid"),
    ("hull_topsides", "hull_paint"), ("bulwark", "hull_paint"),
    ("hull_antifouling", "antifouling"), ("hull_bottom", "antifouling"),
    ("hull_boot_top", "boot_top"),
    ("superstructure_1", "superstructure_paint"), ("superstructure", "superstructure_paint"),
    ("funnel", "superstructure_paint"), ("funnel_top", "superstructure_paint"),
    ("mast", "superstructure_paint"), ("arch", "superstructure_paint"),
    ("glass", "glass"), ("glass_2_0", "glass"), ("hull_glass_3", "glass"),
    ("rail", "bare_metal"), ("bronze", "bare_metal"), ("thruster_nozzle", "bare_metal"),
    ("fender", "rubber"), ("timber", "teak"), ("canopy_cloth", "canvas"),
])
def test_material_names_map_to_finishes(mat, expected):
    assert finishes.finish_for(mat, "Anything__Part")[0] == expected


@pytest.mark.parametrize("obj,expected", [
    # Lincoln's custom-op parts carry colour-named materials (mat_3d3f42): the part name decides
    ("FlightDeck__Main", "deck_nonskid"), ("Elevators__Elevator_1", "deck_nonskid"),
    ("Deck__Main", "deck_nonskid"), ("Stern__Fantail_Deck", "deck_nonskid"),
    ("Island__Bridge", "superstructure_paint"), ("Catwalks__Walk_4", "superstructure_paint"),
    ("Island__Bridge__Windows_Port", "glass"), ("Catwalks__Walk_4__Net", "canvas"),
    ("Hull__Propeller_1", "bare_metal"), ("Hull__Bulb", "hull_paint"),
])
def test_anonymous_materials_use_the_part_name(obj, expected):
    assert finishes.finish_for("mat_3d3f42", obj, 0.6, 0.0)[0] == expected


def test_named_material_wins_over_the_part_name():
    assert finishes.finish_for("hull_antifouling", "Hull__Bulb")[0] == "antifouling"


def test_unknown_falls_back_on_roughness_and_metallic():
    assert finishes.finish_for("mat_aabbcc", "Weapons__Gun", 0.4, 0.9)[0] == "bare_metal"
    assert finishes.finish_for("mat_aabbcc", "Weapons__Gun", 0.1, 0.0)[0] == "glass"
    assert finishes.finish_for("crate_paint", "Cargo__Box", 0.6, 0.0)[0] == "generic"
    assert finishes.finish_for("none", "", 0.6, 0.0)[0] == "generic"


def test_resolved_values_keep_the_spec_where_the_finish_says_so():
    assert finishes.resolved_values("generic", 0.42, 0.3) == (0.42, 0.3)
    assert finishes.resolved_values("deck_nonskid", 0.42, 0.3) == (0.9, 0.0)
    assert finishes.resolved_values("bare_metal", 0.42, 0.6) == (0.35, 0.8)   # metal_min
    assert finishes.resolved_values("glass", 0.15, 0.3) == (0.06, 0.3)


def test_every_finish_in_the_brief_exists():
    for f in ("deck_nonskid", "hull_paint", "antifouling", "boot_top", "superstructure_paint",
              "bare_metal", "glass", "rubber", "teak", "canvas"):
        assert f in finishes.FINISHES


# ------------------------------------------------------------------------------------------
# Atlases and pages
# ------------------------------------------------------------------------------------------

def test_atlas_for():
    assert atlas.atlas_for("deck_nonskid", "Deck__Main") == "Deck"
    assert atlas.atlas_for("teak", "Deck__Main") == "Deck"
    assert atlas.atlas_for("antifouling", "Hull__Rudder_1") == "Hull"
    assert atlas.atlas_for("superstructure_paint", "Island__Roof", 5.0) == "Superstructure"
    assert atlas.atlas_for("generic", "Weapons__RAM_1", 10.0) == "Details"
    assert atlas.atlas_for("generic", "Catwalks__Walk_4", 160.0) == "Superstructure"


def test_page_count_from_area_and_texel_size():
    # one 4096 page at 6 cm holds 245.76 m square; packing fills 62 % of it
    per_page = (4096 * 0.06) ** 2 * 0.62
    assert atlas.page_count(0.0, 0.06, 4096) == (0, False)
    assert atlas.page_count(per_page * 0.5, 0.06, 4096) == (1, False)
    assert atlas.page_count(per_page, 0.06, 4096) == (1, False)
    assert atlas.page_count(per_page * 1.01, 0.06, 4096) == (2, False)
    assert atlas.page_count(per_page * 20, 0.06, 4096, max_pages=8) == (8, True)
    # a finer texel needs more pages
    assert atlas.page_count(per_page, 0.03, 4096)[0] == 4


def test_assign_pages_balances_area():
    def loads(areas, n):
        pages = atlas.assign_pages(areas, n)
        return sorted(sum(a for a, p in zip(areas, pages) if p == k) for k in range(n))

    assert loads([10, 9, 8, 7], 2) == [17, 17]
    # largest first into the emptiest page: pages differ by at most the largest island
    lo, hi = loads([10, 9, 8, 1, 1, 1], 2)
    assert hi - lo <= 10 and lo + hi == 30
    assert atlas.assign_pages([10, 9, 8], 1) == [0, 0, 0]


def test_group_islands():
    assert atlas.group_islands(5, [(0, 1), (1, 2), (3, 4)]) == [[0, 1, 2], [3, 4]]
    assert atlas.group_islands(3, []) == [[0], [1], [2]]


def test_grid_cuts():
    assert atlas.grid_cuts(-166.5, 166.4, 48.0) == [-144.0, -96.0, -48.0, 0.0, 48.0, 96.0, 144.0]
    assert atlas.grid_cuts(0.0, 40.0, 48.0) == []
    assert atlas.grid_cuts(10.0, 100.0, 48.0) == [48.0, 96.0]
    assert atlas.grid_cuts(0.0, 96.0, 48.0) == [48.0]   # not on the extent's own ends


def test_poly_area_2d():
    assert atlas.poly_area_2d([(0, 0), (2, 0), (2, 3), (0, 3)]) == 6.0
    assert atlas.poly_area_2d([(0, 0), (0, 3), (2, 3), (2, 0)]) == 6.0


def test_separated_groups_keep_touching_parts_apart():
    deck = ([-10, -5, 0], [10, 5, 1])
    island = ([0, 3, 1], [4, 5, 9])        # stands on the deck
    gun = ([-8, -4, 1], [-7, -3, 2])       # stands on the deck, far from the island
    mast = ([1, 4, 9], [2, 5, 15])         # stands on the island
    groups = atlas.separated_groups([deck, island, gun, mast], pad=0.1)
    where = {i: k for k, g in enumerate(groups) for i in g}
    assert where[0] != where[1] and where[0] != where[2] and where[1] != where[3]
    assert where[2] == where[1]            # the gun and the island don't touch: one proxy
    assert len(groups) == 2


# ------------------------------------------------------------------------------------------
# End to end (Blender)
# ------------------------------------------------------------------------------------------

def _blender():
    sys.path.insert(0, str(ROOT / "server"))
    from app.config import DEFAULT_BLENDER
    return os.environ.get("BLENDER", str(DEFAULT_BLENDER))


MARKING_OP = r'''
import bpy
from mathutils import Vector
dg = bpy.context.evaluated_depsgraph_get()
hit, loc, nrm, idx, ob, mw = bpy.context.scene.ray_cast(dg, Vector((0, 0, 60)), Vector((0, 0, -1)))
assert hit, "no deck under the centreline"
me3d.box("Markings__Test_Stripe", (loc.x, loc.y, loc.z + 0.015), (3.0, 0.3, 0.03), (0.9, 0.9, 0.2),
         parent=ob.name)
'''

SCALE = 1 / 16


def _glb_json(path):
    data = Path(path).read_bytes()
    magic, _, _ = struct.unpack_from("<4sII", data, 0)
    assert magic == b"glTF"
    jlen, jtype = struct.unpack_from("<I4s", data, 12)
    gl = json.loads(data[20:20 + jlen])
    blen, _ = struct.unpack_from("<I4s", data, 20 + jlen)
    return gl, data[28 + jlen:28 + jlen + blen]


def _png_size(blob):
    assert blob[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", blob[16:24])


@pytest.fixture(scope="module")
def baked(tmp_path_factory):
    blender = _blender()
    out = tmp_path_factory.mktemp("bake")
    spec = {"schema": "me3d.ship_spec/1", "type": "custom", "params": {},
            "custom_ops": [{"id": "marking", "description": "a stripe on the deck",
                            "code": MARKING_OP}]}
    (out / "spec.json").write_text(json.dumps(spec))
    proc = subprocess.run([blender, "-b", "--factory-startup", "--python",
                           str(ROOT / "blender" / "build_ship.py"), "--", "--spec",
                           str(out / "spec.json"), "--out", str(out)],
                          capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-3000:]
    before = hashlib.sha256((out / "model.glb").read_bytes()).hexdigest()
    proc = subprocess.run([blender, "-b", "--factory-startup", "--python",
                           str(ROOT / "blender" / "bake_textures.py"), "--", "--blend",
                           str(out / "model.blend"), "--out", str(out), "--page-scale", str(SCALE),
                           "--samples", "1"],
                          capture_output=True, text=True, timeout=900)
    report = json.loads((out / "bake_report.json").read_text(encoding="utf-8"))
    return {"dir": out, "proc": proc, "report": report, "glb_before": before}


def test_bake_small_ship(baked):
    out, proc, rep = baked["dir"], baked["proc"], baked["report"]
    assert proc.returncode == 0 and rep["ok"], (rep["errors"], proc.stdout[-3000:])
    # model.glb untouched, model_textured.glb written
    assert hashlib.sha256((out / "model.glb").read_bytes()).hexdigest() == baked["glb_before"]
    gl, binc = _glb_json(out / "model_textured.glb")

    # every page at its planned size, and the planned size is the stock size x the scale
    assert rep["pages"]
    for p in rep["pages"]:
        stock = atlas.DEFAULT_ATLASES[p["atlas"]]["page_px"]
        assert p["px"] == max(16, round(stock * SCALE))
    assert {p["atlas"] for p in rep["pages"]} >= {"Deck", "Hull"}

    # each material: base colour, normal, and one ORM image for metal-rough and occlusion
    sizes = {}
    for i, img in enumerate(gl["images"]):
        bv = gl["bufferViews"][img["bufferView"]]
        blob = binc[bv.get("byteOffset", 0):bv.get("byteOffset", 0) + bv["byteLength"]]
        assert img["mimeType"] == "image/png"
        sizes[i] = _png_size(blob)
    page_px = {f"{p['atlas']}_{p['index']}": p["px"] for p in rep["pages"]}
    for m in gl["materials"]:
        pbr = m["pbrMetallicRoughness"]
        src = lambda t: gl["textures"][t["index"]]["source"]  # noqa: E731
        base, mr = src(pbr["baseColorTexture"]), src(pbr["metallicRoughnessTexture"])
        nrm, occ = src(m["normalTexture"]), src(m["occlusionTexture"])
        assert occ == mr, f"{m['name']}: AO is not packed with metal-rough"
        px = page_px[m["name"].replace("_double_sided", "")]
        for s in (base, mr, nrm):
            assert sizes[s] == (px, px)

    # every primitive has UVs and a material
    for mesh in gl["meshes"]:
        for prim in mesh["primitives"]:
            assert "TEXCOORD_0" in prim["attributes"], mesh["name"]
            assert "material" in prim

    # the marking is a decal: listed, baked onto the deck, and gone from the glb
    names = {n.get("name") for n in gl["nodes"]}
    assert "Markings__Test_Stripe" in {d["name"] for d in rep["decals"]}
    assert "Markings__Test_Stripe" not in names
    # moving parts and their hierarchy survive
    parts = json.loads((out / "parts.json").read_text(encoding="utf-8"))
    for p in parts:
        if p["joint"]:
            assert p["path"] in names, p["path"]
    assert rep["reimport"]["untextured"] == [] and rep["reimport"]["without_uv"] == []
