"""Run with:  python -m unittest discover -s tests -v
Tests that need the VR-Forces install (donor .entity) or Blender skip when those are missing."""
from __future__ import annotations

import copy
import json
import math
import shutil
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from make_fixture import make_glb  # noqa: E402
from pipeline import artparts, generate, inspect_glb, run, validate  # noqa: E402
from pipeline.common import layout_for, load_config  # noqa: E402

CFG = load_config()
HAVE_DONOR = generate.donor_path(CFG, "2S1 Gvozdika").exists()
HAVE_BLENDER = Path(CFG["blender"]).exists()

MANIFEST = {
    "name": "WheeledTestBox", "display_name": "Test Box Vehicle (pipeline fixture)", "domain": "land",
    "dis_type": "1:1:225:99:99:99:99", "donor_entity": "2S1 Gvozdika", "platform_ope": "Ground_Vehicle.ope",
    "model_category_dir": "Vehicles/Wheeled", "forward_axis_in_blender": "+Y",
    "articulated_parts": [
        {"node": "turret", "dis_part": 4096, "parent": None},
        {"node": "gun", "dis_part": 4416, "parent": "turret", "min": -5, "max": 70},
    ],
}

# Stand-in for appData/importConfig/FBX_NodeNameMap.csv (same format as the 5.1.1 install, with BOM)
NODE_MAP_CSV = "﻿Node,Comment,Type,Description\n" \
    "turret_turn,@dis articulated_part 4096,comment,Primary Turret Number 1\n" \
    "main_cannon_elev,@dis articulated_part 4416,comment,Primary Gun Number 1\n" \
    "wheel_1_1_group,@dis articulated_part 9120,comment,1st Left Wheel\n" \
    "SimpleVehicle,Wreck,DamageState,Match node name to NORMAL/DAMAGE state\n" \
    "High,@dis lod 0 switch_in 50 switch_out 0,,\n" \
    "Med,@dis lod 1 switch_in 200 switch_out 50,,\n" \
    "Low,@dis lod 2 switch_in 1000 switch_out 200,,\n"


def manifest(**over):
    m = copy.deepcopy(MANIFEST)
    m.update(over)
    return m


class TmpDir(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        p = self.tmp / "FBX_NodeNameMap.csv"
        p.write_text(NODE_MAP_CSV, encoding="utf-8")
        self.node_map = artparts.load_node_map(p)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def codes(self, glb_variant="ok", m=None, **kw):
        r = inspect_glb.analyse(make_glb(self.tmp / f"{glb_variant}.glb", variant=glb_variant, **kw),
                                (m or MANIFEST)["forward_axis_in_blender"])
        return r, {x.code for x in inspect_glb.check(r, m or MANIFEST, CFG["limits"], self.node_map)}


# ---------------------------------------------------------------- artparts
class TestArtParts(TmpDir):
    def test_table58_codes(self):
        self.assertEqual(artparts.CODES[4096][0], "primary_turret_number_1")
        self.assertEqual(artparts.CODES[4448][0], "primary_gun_number_2")
        self.assertEqual(artparts.CODES[4704][0], "primary_gun_number_10")
        self.assertEqual(artparts.CODES[9152][0], "1st_right_wheel")
        self.assertEqual(artparts.CODES[9728][0], "10th_right_wheel")
        self.assertEqual(artparts.CODES[6976][0], "secondary_radar_1")

    def test_default_motions(self):
        self.assertEqual(artparts.default_motion(4096), "azimuth")
        self.assertEqual(artparts.default_motion(4416), "elevation")
        self.assertEqual(artparts.default_motion(2048), "translate_z")
        self.assertEqual(artparts.default_motion(9120), "visual")
        self.assertTrue(artparts.is_weapon(4416) and artparts.is_weapon(4768) and not artparts.is_weapon(4096))

    def test_frame_conversion(self):
        # model (x right, y fwd, z up) -> body (fwd, right, down)
        self.assertEqual(artparts.model_to_body((1.0, 2.0, 3.0)), (2.0, 1.0, -3.0))

    def test_load_node_map(self):
        self.assertEqual(self.node_map.parts["turret_turn"], 4096)
        self.assertEqual(self.node_map.canonical_name(4416), "main_cannon_elev")
        self.assertEqual(self.node_map.lods, {"High", "Med", "Low"})
        self.assertIn("SimpleVehicle", self.node_map.damage)

    def test_resolve_renames_to_map_names(self):
        parts = artparts.resolve_parts(MANIFEST, self.node_map)
        self.assertEqual(artparts.rename_map(parts), {"turret": "turret_turn", "gun": "main_cannon_elev"})
        gun = parts[1]
        self.assertEqual((gun.motion, gun.min, gun.max), ("elevation", -5.0, 70.0))
        self.assertEqual(artparts.map_additions(parts), [])

    def test_unmapped_code_needs_map_row(self):
        m = manifest(articulated_parts=[{"node": "hatch", "dis_part": 3232, "parent": None}])
        parts = artparts.resolve_parts(m, self.node_map)
        self.assertEqual(artparts.rename_map(parts), {})
        self.assertEqual(artparts.map_additions(parts), [["hatch", "@dis articulated_part 3232", "comment", "Hatch on a tank or APC"]])


# ---------------------------------------------------------------- inspect
class TestInspect(TmpDir):
    def test_bounds_and_nodes(self):
        r, _ = self.codes()
        self.assertEqual(r["triangles"], 36)
        self.assertEqual(r["node_names"], ["hull", "turret", "gun"])
        self.assertEqual(r["bounds_mak_frame"]["min"], [-1.0, -2.5, 0.0])
        self.assertEqual(r["bounds_mak_frame"]["max"], [1.0, 3.8, 2.0])
        self.assertEqual(r["nodes"]["turret"]["pivot"], [0.0, 0.0, 1.5])
        self.assertEqual(r["nodes"]["gun"]["pivot"], [0.0, 0.8, 1.75])
        self.assertEqual(r["nodes"]["gun"]["parent"], "turret")

    def test_clean_fixture_has_no_errors_or_part_warnings(self):
        r = inspect_glb.analyse(make_glb(self.tmp / "a.glb"), "+Y")
        f = inspect_glb.check(r, MANIFEST, CFG["limits"], self.node_map)
        self.assertFalse([x for x in f if x.level in ("error", "warning")], f)

    def test_centimetre_export_is_caught(self):
        self.assertIn("scale-too-large", self.codes(scale=100)[1])

    def test_floating_origin_is_caught(self):
        self.assertIn("origin-z", self.codes(lift=1.0)[1])

    def test_wrong_forward_axis_is_flagged(self):
        self.assertIn("forward-axis", self.codes(m=manifest(forward_axis_in_blender="+X"))[1])

    def test_missing_artpart_node(self):
        m = manifest(articulated_parts=[{"node": "rotor", "dis_part": 1184, "parent": None}])
        self.assertIn("artpart-node-missing", self.codes(m=m)[1])

    def test_pivot_not_at_rotation_axis(self):
        self.assertIn("artpart-pivot", self.codes("bad_pivot")[1])

    def test_gun_not_under_turret(self):
        self.assertIn("artpart-hierarchy", self.codes("flat_gun")[1])

    def test_static_rotation_in_neutral_pose(self):
        self.assertIn("artpart-static-rotation", self.codes("rotated_turret")[1])

    def test_duplicate_code(self):
        m = manifest(articulated_parts=[{"node": "turret", "dis_part": 4096, "parent": None},
                                        {"node": "gun", "dis_part": 4096, "parent": "turret"}])
        self.assertIn("artpart-code-duplicate", self.codes(m=m)[1])

    def test_unknown_code_and_bad_motion(self):
        m = manifest(articulated_parts=[{"node": "turret", "dis_part": 12345, "parent": None, "motion": "spin"}])
        codes = self.codes(m=m)[1]
        self.assertIn("artpart-code-unknown", codes)
        self.assertIn("artpart-motion", codes)

    def test_parent_not_declared(self):
        m = manifest(articulated_parts=[{"node": "gun", "dis_part": 4416, "parent": "turret"}])
        self.assertIn("artpart-parent-unknown", self.codes(m=m)[1])

    def test_node_name_means_another_code(self):
        # node already named like the map's 4096 turret, but declared as a different part
        m = manifest(articulated_parts=[{"node": "turret_turn", "dis_part": 4128, "parent": None}])
        _, codes = self.codes(m=m, names=("hull", "turret_turn", "gun"))
        self.assertIn("artpart-name-conflict", codes)

    def test_mapped_node_not_declared(self):
        m = manifest(articulated_parts=[])
        _, codes = self.codes(m=m, names=("hull", "turret_turn", "gun"))
        self.assertIn("artpart-undeclared", codes)

    def test_lod_naming(self):
        _, codes = self.codes(m=manifest(articulated_parts=[]), names=("LOD0", "turret", "gun"))
        self.assertIn("lod-naming", codes)


# ---------------------------------------------------------------- generate + validate
class TestGenerateValidate(TmpDir):
    def setUp(self):
        super().setUp()
        self.lay = layout_for(MANIFEST, self.tmp / "out")
        self.glb = inspect_glb.analyse(make_glb(self.tmp / "a.glb"), "+Y")

    def gen(self, m=None):
        f = generate.generate(m or MANIFEST, self.lay, self.glb, CFG, self.node_map)
        self.lay.model_file.parent.mkdir(parents=True, exist_ok=True)
        self.lay.model_file.write_bytes(b"stub")
        return f

    def test_missing_model_file_is_error(self):
        generate.generate(MANIFEST, self.lay, self.glb, CFG, self.node_map)
        self.assertIn("model-file-missing", {x.code for x in validate.validate(self.lay.root, CFG)})

    @unittest.skipUnless(Path(CFG["definitions_dir"]).exists(), "VR-Forces install not present")
    def test_dis_type_collision_with_install(self):
        self.gen(manifest(dis_type="5:-1:-1:-1:-1:-1:-1"))   # stock 'Nun' buoy
        self.assertIn("dis-type-collision", {x.code for x in validate.validate(self.lay.root, CFG)})

    def test_node_map_additions_written(self):
        self.lay = layout_for(MANIFEST, self.tmp / "out", fmt="fbx")   # FBX route only
        m = manifest(articulated_parts=[{"node": "turret", "dis_part": 3232, "parent": None}])
        f = self.gen(m)
        self.assertIn("node-map-additions", {x.code for x in f})
        rows = self.lay.node_map_additions.read_text(encoding="utf-8").splitlines()
        self.assertEqual(rows[1], "turret,@dis articulated_part 3232,comment,Hatch on a tank or APC")

    @unittest.skipUnless(HAVE_DONOR, "donor .entity not present")
    def test_entity_from_donor(self):
        f = self.gen()
        self.assertFalse([x for x in f if x.level == "error"], f)
        so = ET.parse(self.lay.entity(MANIFEST["display_name"])).getroot().find("simObject")
        self.assertEqual(so.get("objectType"), "1:" + MANIFEST["dis_type"])
        parts = {int(d.find("int[@paramName='art-part-type']").text): d
                 for d in so.findall("DtSimArtPartDescriptorList/DtSimArtPartDescriptor")}
        self.assertEqual(sorted(parts), [4096, 4416])

        def pos(d, name):
            e = d.find(f"bodyPosition[@paramName='{name}']")
            return tuple(float(e.get(k)) for k in ("forward", "right", "down"))
        self.assertEqual(pos(parts[4096], "attach-point"), (0.0, 0.0, -1.5))     # absolute: 1.5 m up
        self.assertEqual(pos(parts[4416], "attach-point"), (0.8, 0.0, -0.25))    # relative to turret
        self.assertEqual(pos(parts[4416], "end-point"), (3.0, 0.0, 0.0))         # muzzle 3 m ahead
        self.assertEqual(parts[4416].find("int[@paramName='parent-part-type']").text, "4096")
        self.assertEqual([int(x.text) for x in parts[4416].findall("DtRwList/int")], [13, 14])
        self.assertAlmostEqual(float(parts[4416].find("real[@paramName='max-elevation']").text), math.radians(70), places=9)
        self.assertNotIn('"-0"', ET.tostring(so, encoding="unicode"))
        dims = so.find("boundingVolume/dimensions")
        self.assertEqual((dims.get("length"), dims.get("width"), dims.get("height")), ("6.3", "2", "2"))
        self.assertEqual(so.find("string[@paramName='gui-label']").text, MANIFEST["display_name"])
        self.assertFalse(so.find("string[@paramName='object-geometry-filename']").text)

        v = validate.validate(self.lay.root, CFG)
        self.assertFalse([x for x in v if x.level == "error"], v)

    @unittest.skipUnless(HAVE_DONOR, "donor .entity not present")
    def test_visual_only_parts_are_not_in_entity(self):
        m = manifest(articulated_parts=[{"node": "turret", "dis_part": 4096, "parent": None},
                                        {"node": "gun", "dis_part": 1184, "parent": "turret"}])   # rotor: visual
        self.gen(m)
        so = ET.parse(self.lay.entity(MANIFEST["display_name"])).getroot().find("simObject")
        codes = [d.find("int[@paramName='art-part-type']").text for d in so.findall("DtSimArtPartDescriptorList/DtSimArtPartDescriptor")]
        self.assertEqual(codes, ["4096"])

    @unittest.skipUnless(HAVE_DONOR, "donor .entity not present")
    def test_validate_catches_tampered_entity(self):
        self.gen()
        path = self.lay.entity(MANIFEST["display_name"])
        tree = ET.parse(path)
        so = tree.getroot().find("simObject")
        so.set("objectType", "1:1:1:225:1:1:1:1")
        gun = [d for d in so.findall("DtSimArtPartDescriptorList/DtSimArtPartDescriptor")
               if d.find("int[@paramName='art-part-type']").text == "4416"][0]
        gun.find("int[@paramName='parent-part-type']").text = "4160"
        gun.find("real[@paramName='min-elevation']").text = "2"
        tree.write(path, encoding="utf-8", xml_declaration=True)
        codes = {x.code for x in validate.validate(self.lay.root, CFG)}
        self.assertTrue({"entity-objecttype-mismatch", "entity-artpart-parent", "entity-artpart-limits"} <= codes, codes)

    @unittest.skipUnless(HAVE_DONOR, "donor .entity not present")
    def test_donor_weapon_parts_flagged_when_model_lacks_them(self):
        self.gen(manifest(articulated_parts=[]))
        codes = {x.code for x in validate.validate(self.lay.root, CFG)}
        self.assertIn("entity-system-artpart", codes)   # 2S1 cannon wants 4096/4416/4128


# ---------------------------------------------------------------- end to end
@unittest.skipUnless(HAVE_BLENDER and HAVE_DONOR, "Blender or VR-Forces install not present")
class TestEndToEndFbx(TmpDir):
    """The Blender (FBX) route."""
    def run_model(self, variant="ok", m=None):
        model_dir = self.tmp / "models" / "WheeledTestBox"
        make_glb(model_dir / "model.glb", variant=variant)
        (model_dir / "manifest.json").write_text(json.dumps(m or MANIFEST))
        return run.run(model_dir, output_root=self.tmp / "output", node_map=self.node_map, fmt="fbx")

    def test_run_with_blender(self):
        result = self.run_model()
        self.assertTrue(result["ok"], json.dumps(result["stages"], indent=1))
        self.assertIn("fbx-verified", {x["code"] for x in result["stages"]["verify_model"]})
        self.assertTrue((self.tmp / "output/WheeledTestBox/SharedData/ModelData/Vehicles/Wheeled/WheeledTestBox/WheeledTestBox.fbx").exists())

    def test_forward_axis_rotation_keeps_parts_neutral(self):
        # same model authored facing -Y: conversion must rotate it and leave part DOFs unrotated
        model_dir = self.tmp / "models" / "WheeledTestBox"
        make_glb(model_dir / "model.glb")
        m = manifest(forward_axis_in_blender="-Y")
        (model_dir / "manifest.json").write_text(json.dumps(m))
        result = run.run(model_dir, output_root=self.tmp / "output", node_map=self.node_map, fmt="fbx")
        verify = {x["code"] for x in result["stages"]["verify_model"]}
        self.assertEqual(verify, {"fbx-verified"}, json.dumps(result["stages"]["verify_model"], indent=1))


if __name__ == "__main__":
    unittest.main()
