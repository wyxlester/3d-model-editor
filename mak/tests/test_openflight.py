"""OpenFlight route (no Blender): glb reader, .flt writer/reader, MAK-side load, end-to-end.
Run with:  python -m unittest discover -s tests -v
MAK-side tests use medfTool.exe from the VR-Forces install and skip without it."""
from __future__ import annotations

import copy
import json
import math
import shutil
import struct
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from make_fixture import make_glb  # noqa: E402
from pipeline import artparts, generate, glb, openflight, run, verify_fbx  # noqa: E402
from pipeline import inspect_glb  # noqa: E402
from pipeline.common import layout_for, load_config  # noqa: E402

CFG = load_config()
HAVE_MEDF = Path(CFG.get("medf_tool", "")).exists()
HAVE_DONOR = generate.donor_path(CFG, "2S1 Gvozdika").exists()
HARMONY = ROOT / "models" / "HarmonyOfTheSeas"

MANIFEST = {
    "name": "WheeledTestBox", "display_name": "Test Box Vehicle (pipeline fixture)", "domain": "land",
    "dis_type": "1:1:225:99:99:99:99", "donor_entity": "2S1 Gvozdika", "platform_ope": "Ground_Vehicle.ope",
    "model_category_dir": "Vehicles/Wheeled", "forward_axis_in_blender": "+Y",
    "articulated_parts": [
        {"node": "turret", "dis_part": 4096, "parent": None},
        {"node": "gun", "dis_part": 4416, "parent": "turret", "min": -5, "max": 70},
    ],
}


def parts_for(m=MANIFEST):
    return artparts.resolve_parts(m, artparts.NodeNameMap())


def records(data: bytes):
    pos = 0
    while pos + 4 <= len(data):
        op, n = struct.unpack_from(">hH", data, pos)
        yield op, pos, n
        if op == openflight.OP["vpalette"]:
            pos += struct.unpack_from(">i", data, pos + 4)[0]
        else:
            pos += n


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def write(self, m=MANIFEST, **fixture):
        g = make_glb(self.tmp / "a.glb", **fixture)
        model = glb.load(g, m["forward_axis_in_blender"])
        res = openflight.write_flt(model, self.tmp / "out" / "TestBox.flt", parts_for(m), m["name"], m["dis_type"])
        return model, res, openflight.read_flt(res.path)


# ---------------------------------------------------------------- glb reader
class TestGlbReader(Tmp):
    def test_triangles_in_mak_frame(self):
        m = glb.load(make_glb(self.tmp / "a.glb"), "+Y")
        self.assertEqual(m.triangle_count, 36)
        self.assertEqual(m.roots, ["hull"])
        self.assertEqual(m.nodes["gun"].parent, "turret")
        self.assertEqual(tuple(round(v, 6) for v in m.nodes["gun"].pivot), (0.0, 0.8, 1.75))
        ys = [p[1] for t in m.nodes["gun"].triangles for p in t.p]
        self.assertAlmostEqual(max(ys), 3.8, places=5)
        for t in m.nodes["hull"].triangles:
            for n in t.n:
                self.assertAlmostEqual(math.sqrt(sum(c * c for c in n)), 1.0, places=6)

    def test_flat_normals_point_outwards(self):
        m = glb.load(make_glb(self.tmp / "a.glb"), "+Y")
        top = [t for t in m.nodes["hull"].triangles if all(abs(p[2] - 1.5) < 1e-6 for p in t.p)]
        self.assertTrue(top)
        self.assertTrue(all(t.n[0][2] > 0.99 for t in top))     # roof faces up: winding preserved

    def test_forward_axis_rotation(self):
        m = glb.load(make_glb(self.tmp / "a.glb"), "-Y")     # authored facing -Y: gun now points to -Y
        ys = [p[1] for t in m.nodes["gun"].triangles for p in t.p]
        self.assertAlmostEqual(min(ys), -3.8, places=5)

    def test_bounds_agree_with_inspect(self):
        g = make_glb(self.tmp / "a.glb")
        m = glb.load(g, "+Y")
        pts = [p for nd in m.nodes.values() for t in nd.triangles for p in t.p]
        b = inspect_glb.analyse(g, "+Y")["bounds_mak_frame"]
        self.assertEqual([round(min(p[i] for p in pts), 4) for i in range(3)], b["min"])
        self.assertEqual([round(max(p[i] for p in pts), 4) for i in range(3)], b["max"])

    def test_materials_and_images(self):
        m = glb.load(make_glb(self.tmp / "a.glb", textured=True), "+Y")
        self.assertEqual(len(m.images), 1)
        self.assertEqual(m.images[0].ext, ".png")
        self.assertEqual((m.materials[0].image, m.materials[0].double_sided), (0, True))
        self.assertEqual(m.nodes["hull"].triangles[0].material, 0)


# ---------------------------------------------------------------- writer / reader
class TestFltWriter(Tmp):
    def test_round_trip_structure(self):
        model, res, f = self.write()
        self.assertEqual((res.faces, f.faces, res.vertices), (36, 36, 108))
        self.assertEqual((f.revision, f.units), (1610, 0))
        self.assertEqual(f.nodes["turret"].kind, "dof")
        self.assertEqual(f.nodes["gun"].parent, "turret")      # child DOFs follow the Object, not inside it
        self.assertEqual(f.nodes["o_gun"].parent, "gun")
        dump = openflight.as_dump(f)["objects"]
        self.assertEqual(dump["gun"]["parent"], "turret")
        self.assertEqual(dump["turret"]["parent"], "hull")
        self.assertEqual(dump["WheeledTestBox"]["comment"], "@dis entity_type 1:1:225:99:99:99:99")

    def test_dof_origin_limits_and_comment(self):
        _, _, f = self.write()
        gun = f.nodes["gun"]
        self.assertEqual(tuple(round(v, 6) for v in gun.origin), (0.0, 0.8, 1.75))
        self.assertEqual(gun.limits["pitch"][:2], (-5.0, 70.0))
        self.assertTrue(gun.dof_flags & openflight.DOF_LIMIT["pitch"])
        self.assertEqual(gun.comment, "@dis articulated_part 4416    rotate_min -5,0,0  rotate_current 0,0,0  "
                                      "rotate_max 70,0,0  rotate_step 1,0,0")
        self.assertTrue(f.nodes["turret"].comment.startswith("@dis articulated_part 4096    rotate_min 0,0,-180"))
        self.assertEqual(f.nodes["turret"].limits["yaw"][:2], (-180.0, 180.0))

    def test_records_well_formed(self):
        _, res, _ = self.write()
        data = res.path.read_bytes()
        ops = [op for op, _, _ in records(data)]
        self.assertEqual(ops[0], 1)
        self.assertEqual(struct.unpack_from(">H", data, 2)[0], 324)       # full 16.1 header
        self.assertEqual(ops.count(10), ops.count(11))                     # balanced push/pop
        for op, pos, n in records(data):
            self.assertEqual(n % 4, 0, f"record {op} at {pos} has length {n}")

    def test_long_names_use_long_id(self):
        m = copy.deepcopy(MANIFEST)
        m["articulated_parts"][0]["node"] = "Turret_Assembly_Main"
        m["articulated_parts"][1]["parent"] = "Turret_Assembly_Main"
        _, res, f = self.write(m, names=("hull", "Turret_Assembly_Main", "gun"))
        self.assertIn("Turret_Assembly_Main", f.nodes)
        self.assertIn(openflight.OP["longid"], [op for op, _, _ in records(res.path.read_bytes())])

    def test_textures_written_and_uv_flipped(self):
        _, res, f = self.write(textured=True)
        self.assertEqual([p.name for p in res.textures], ["WheeledTestBox_tex0.png"])
        self.assertTrue(res.textures[0].read_bytes().startswith(b"\x89PNG"))
        self.assertEqual((f.textures, f.materials), (["WheeledTestBox_tex0.png"], 1))
        data = res.path.read_bytes()
        vp = next(pos for op, pos, _ in records(data) if op == openflight.OP["vpalette"])
        u, v = struct.unpack_from(">2f", data, vp + 8 + 4 + 4 + 24 + 12)
        self.assertAlmostEqual(v, 0.75)                                    # glTF v 0.25 -> OpenFlight 0.75
        face = next(pos for op, pos, _ in records(data) if op == openflight.OP["face"])
        tex, mat = struct.unpack_from(">hh", data, face + 4 + 24)
        self.assertEqual((tex, mat), (0, 0))

    def test_verify_compare_clean(self):
        g = make_glb(self.tmp / "a.glb")
        _, _, f = self.write()
        found = verify_fbx.compare(openflight.as_dump(f), inspect_glb.analyse(g, "+Y"), parts_for(), fmt="flt")
        found += openflight.check_flt(f, parts_for(), MANIFEST["dis_type"])
        self.assertEqual([x.code for x in found], ["flt-verified"], found)

    def test_check_flt_catches_wrong_part(self):
        _, _, f = self.write()
        wrong = parts_for(dict(MANIFEST, articulated_parts=[{"node": "turret", "dis_part": 4128, "parent": None},
                                                            {"node": "hull", "dis_part": 3232, "parent": None}]))
        codes = {x.code for x in openflight.check_flt(f, wrong, MANIFEST["dis_type"])}
        self.assertIn("flt-part-comment", codes)
        self.assertIn("flt-part-not-dof", codes)


# ---------------------------------------------------------------- MAK side
@unittest.skipUnless(HAVE_MEDF, "medfTool.exe (VR-Forces install) not present")
class TestMakLoadsFlt(Tmp):
    def test_medftool_sees_parts(self):
        _, res, _ = self.write(textured=True)
        info = openflight.mak_node_info(res.path, CFG["medf_tool"])
        self.assertIn("@dis articulated_part 4096", info["turret"][0])
        self.assertIn("@dis articulated_part 4416", info["gun"][0])
        self.assertEqual(info["WheeledTestBox"], ["@dis entity_type 1:1:225:99:99:99:99"])
        self.assertEqual([x.code for x in openflight.check_mak(info, parts_for(), MANIFEST["dis_type"])], ["mak-loaded"])

    def test_check_mak_flags_missing_part(self):
        _, res, _ = self.write()
        info = openflight.mak_node_info(res.path, CFG["medf_tool"])
        extra = parts_for(dict(MANIFEST, articulated_parts=MANIFEST["articulated_parts"]
                               + [{"node": "hull", "dis_part": 3232, "parent": None}]))
        self.assertIn("mak-part-missing", {x.code for x in openflight.check_mak(info, extra, MANIFEST["dis_type"])})


# ---------------------------------------------------------------- end to end without Blender
@unittest.skipUnless(HAVE_DONOR, "VR-Forces install not present")
class TestEndToEndFlt(Tmp):
    def run_model(self, m=MANIFEST, **fixture):
        d = self.tmp / "models" / m["name"]
        make_glb(d / "model.glb", **fixture)
        (d / "manifest.json").write_text(json.dumps(m))
        return run.run(d, output_root=self.tmp / "output", fmt="flt", mak_check=HAVE_MEDF)

    def test_run_without_blender(self):
        r = self.run_model(textured=True)
        self.assertTrue(r["ok"], json.dumps(r["stages"], indent=1))
        codes = {x["code"] for x in r["stages"]["verify_model"]}
        self.assertIn("flt-verified", codes)
        if HAVE_MEDF:
            self.assertIn("mak-loaded", codes)
        out = self.tmp / "output" / "WheeledTestBox"
        lay = layout_for(MANIFEST, self.tmp / "output", "flt")
        self.assertTrue(lay.model_file.exists() and (lay.model_dir / "WheeledTestBox_tex0.png").exists())
        ommx = ET.parse(lay.ommx).getroot()
        self.assertIn(".flt", [i.findtext("second") for i in ommx.iter("item") if i.findtext("first") == "filename"][0])
        self.assertFalse((out / "appData").exists())     # no FBX_NodeNameMap additions on this route
        self.assertNotIn("artpart-rename", {x["code"] for x in r["stages"]["inspect"]})

    def test_forward_axis_rotation(self):
        r = self.run_model(dict(MANIFEST, forward_axis_in_blender="-Y"))
        self.assertTrue(r["ok"], json.dumps(r["stages"], indent=1))


@unittest.skipUnless((HARMONY / "model.glb").exists(), "Harmony model not present")
class TestHarmonyFlt(Tmp):
    def test_harmony_writes_and_verifies(self):
        m = json.loads((HARMONY / "manifest.json").read_text(encoding="utf-8"))
        model = glb.load(HARMONY / "model.glb", m["forward_axis_in_blender"])
        parts = parts_for(m)
        res = openflight.write_flt(model, self.tmp / "H.flt", parts, m["name"], m["dis_type"])
        self.assertEqual(res.faces, 17758)
        f = openflight.read_flt(res.path)
        report = inspect_glb.analyse(HARMONY / "model.glb", m["forward_axis_in_blender"])
        found = verify_fbx.compare(openflight.as_dump(f), report, parts, fmt="flt") + openflight.check_flt(f, parts, m["dis_type"])
        self.assertEqual([x.code for x in found], ["flt-verified"], found)


if __name__ == "__main__":
    unittest.main()
