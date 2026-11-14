"""Tests for pipeline.draft_manifest and the input-error messages.
Run with:  python -m unittest discover -s tests -v"""
from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from make_fixture import make_glb  # noqa: E402
from pipeline import artparts, draft_manifest, inspect_glb, run  # noqa: E402
from pipeline.common import InputError, check_model_dir, load_config, load_manifest  # noqa: E402

CFG = load_config()
HARMONY = ROOT / "models" / "HarmonyOfTheSeas"


def write_exports(model_dir: Path, glb: Path, *, joints=True, build_ok=True, triangles=None, spec=None):
    """Blender-style exports for the fixture: parts.json, spec.json, build_report.json."""
    r = inspect_glb.analyse(glb, "+Y")
    joint = {
        "turret": {"axis": "z", "min_deg": -180.0, "max_deg": 180.0, "priority": "P0", "kind": "revolute"},
        "gun": {"axis": "x", "min_deg": -5.0, "max_deg": 70.0, "priority": "P0", "kind": "revolute"},
    }
    parts = [{"path": n, "label": n, "parent": i["parent"], "joint": joint.get(n) if joints else None,
              "station": None, "origin_m": i["pivot"], "rotation_deg": [0, 0, 0],
              "bounds_min": (i["own_bounds"] or i["subtree_bounds"])["min"],
              "bounds_max": (i["own_bounds"] or i["subtree_bounds"])["max"], "triangles": 12}
             for n, i in r["nodes"].items() if not n.startswith("socket_")]
    (model_dir / "parts.json").write_text(json.dumps(parts))
    (model_dir / "spec.json").write_text(json.dumps(spec or {
        "schema": "me3d.vehicle_spec/1", "name": "Test Box Vehicle", "params": {"length_m": 6.3},
        "meta": {"name": "stale template", "length_m": 99}, "custom_ops": [{"id": "op_1", "code": "raise SystemExit"}]}))
    (model_dir / "build_report.json").write_text(json.dumps({
        "ok": build_ok, "errors": [] if build_ok else ["boom"], "warnings": [],
        "triangles": triangles if triangles is not None else r["triangles"],
        "bounds_min": r["bounds_mak_frame"]["min"], "bounds_max": r["bounds_mak_frame"]["max"]}))


class DraftBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.dir = self.tmp / "models" / "TestBox"
        self.glb = make_glb(self.dir / "Test Box.glb", sockets={"socket_bow": (0, 2.5, 0.5)})
        self.node_map = artparts.NodeNameMap()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def draft(self, **kw):
        write_exports(self.dir, self.glb, **kw)
        return draft_manifest.draft(self.dir, CFG, self.node_map)


class TestDraft(DraftBase):
    def test_parts_from_joints(self):
        m, notes = self.draft()
        self.assertEqual(m["name"], "TestBoxVehicle")
        self.assertEqual(m["display_name"], "Test Box Vehicle")
        self.assertEqual(m["domain"], "land")
        self.assertEqual(m["forward_axis_in_blender"], "+Y")
        self.assertEqual(m["articulated_parts"], [
            {"node": "gun", "dis_part": 4416, "parent": "turret", "motion": "elevation", "min": -5.0, "max": 70.0},
            {"node": "turret", "dis_part": 4096, "parent": None, "motion": "azimuth", "min": -180.0, "max": 180.0},
        ])
        self.assertEqual(m["unmapped_joints"], [])
        self.assertTrue(m["dis_type"].startswith("TODO"))
        self.assertIn("socket_bow", m["export_info"]["sockets_blender"])
        self.assertNotIn("source_glb", m["export_info"])          # the manifest does not name the glb file
        self.assertFalse(any("meta ignored" in n for n in notes))  # this spec has no loa_m to contradict

    def test_custom_ops_never_executed(self):
        # op_1 is `raise SystemExit`; drafting must not run it
        m, _ = self.draft()
        self.assertTrue(m["articulated_parts"])

    def test_failed_build_refused(self):
        with self.assertRaisesRegex(draft_manifest.DraftError, "build failed"):
            self.draft(build_ok=False)

    def test_glb_out_of_sync_with_build_report(self):
        with self.assertRaisesRegex(draft_manifest.DraftError, "triangles"):
            self.draft(triangles=1)

    def test_no_joints_no_parts(self):
        m, _ = self.draft(joints=False)
        self.assertEqual((m["articulated_parts"], m["unmapped_joints"]), ([], []))

    def test_node_map_name_wins(self):
        self.node_map = artparts.NodeNameMap(parts={"turret": 4128})
        m, _ = self.draft()
        self.assertEqual({p["node"]: p["dis_part"] for p in m["articulated_parts"]}, {"turret": 4128, "gun": 4416})

    def test_forward_axis_from_stern_light_and_fallback(self):
        nodes = {"socket_light_stern": {"pivot": [-180.0, 0.0, 14.0]}}
        self.assertEqual(draft_manifest.infer_forward_axis(nodes, {"extent": [362, 66, 75]})[0], "+X")
        self.assertTrue(draft_manifest.infer_forward_axis({}, {"extent": [362, 66, 75]})[0].startswith("TODO"))

    def test_joint_motion_mapping(self):
        jm = draft_manifest.joint_motion
        self.assertEqual(jm({"axis": "z", "min_deg": -180, "max_deg": 180}, "+X"), "azimuth")
        self.assertEqual(jm({"axis": "x", "min_deg": -180, "max_deg": 180}, "+X"), "visual")      # propeller
        self.assertEqual(jm({"axis": "x", "min_deg": -55, "max_deg": 0}, "+X"), "rotation")       # roll (davit)
        self.assertEqual(jm({"axis": "y", "min_deg": -10, "max_deg": 60}, "+X"), "elevation")
        self.assertEqual(jm({"axis": "z", "kind": "prismatic", "min_deg": 0, "max_deg": 1}, "+X"), "translate_z")

    def test_cli_writes_draft_next_to_existing_manifest(self):
        write_exports(self.dir, self.glb)
        (self.dir / "manifest.json").write_text("{}")
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(draft_manifest.main([str(self.dir)]), 0)
        self.assertEqual((self.dir / "manifest.json").read_text(), "{}")
        self.assertTrue((self.dir / "manifest.draft.json").exists())
        self.assertIn("rename 'Test Box.glb' to model.glb", out.getvalue())


class TestInputErrors(DraftBase):
    def test_glb_path_gives_folder_instructions(self):
        with self.assertRaises(InputError) as cm:
            check_model_dir(Path("models/Harmony of the Seas.glb"))
        msg = str(cm.exception)
        self.assertIn("is a .glb file, but the pipeline takes a model folder", msg)
        self.assertIn("models/HarmonyOfTheSeas/model.glb", msg)
        self.assertIn("python -m pipeline.draft_manifest models/HarmonyOfTheSeas", msg)

    def test_run_main_with_glb_exits_2_with_message(self):
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(run.main([str(self.glb)]), 2)
        self.assertIn("takes a model folder", err.getvalue())

    def test_folder_without_model_glb(self):
        with self.assertRaisesRegex(InputError, "rename 'Test Box.glb' to model.glb"):
            check_model_dir(self.dir)

    def test_folder_without_manifest_points_to_draft(self):
        write_exports(self.dir, self.glb)
        self.glb.rename(self.dir / "model.glb")
        with self.assertRaisesRegex(InputError, "draft one from parts.json, spec.json, build_report.json"):
            check_model_dir(self.dir)

    def test_todo_values_rejected(self):
        m, _ = self.draft()
        (self.dir / "manifest.json").write_text(json.dumps(m))
        with self.assertRaisesRegex(InputError, "still has TODO values: dis_type"):
            load_manifest(self.dir)

    def test_part_without_code_rejected(self):
        m, _ = self.draft()
        m["dis_type"] = "1:1:225:99:99:99:99"
        m["donor_entity"] = "2S1 Gvozdika"
        m["articulated_parts"].append({"node": "hull", "dis_part": None})
        (self.dir / "manifest.json").write_text(json.dumps(m))
        with self.assertRaisesRegex(InputError, "needs 'node' and a 'dis_part'"):
            load_manifest(self.dir)


@unittest.skipUnless((HARMONY / "parts.json").exists() and list(HARMONY.glob("*.glb")), "Harmony exports not present")
class TestHarmonyExports(unittest.TestCase):
    """The real Blender exports in models/HarmonyOfTheSeas (read-only)."""

    @classmethod
    def setUpClass(cls):
        cls.m, cls.notes = draft_manifest.draft(HARMONY, CFG, artparts.NodeNameMap())

    def test_identity_and_frame(self):
        self.assertEqual(self.m["name"], "HarmonyOfTheSeas")
        self.assertEqual(self.m["domain"], "surface")
        self.assertEqual(self.m["forward_axis_in_blender"], "+X")           # socket_bow at +X
        self.assertEqual((self.m["platform_ope"], self.m["model_category_dir"]), ("Surface_Ship.ope", "Vehicles/Surface"))

    def test_radars_mapped_rest_static(self):
        self.assertEqual({p["node"]: (p["dis_part"], p["motion"]) for p in self.m["articulated_parts"]},
                         {"Mast__Radar_1": (5376, "azimuth"), "Mast__Radar_2": (5408, "azimuth")})
        motions = {j["node"]: j["motion"] for j in self.m["unmapped_joints"]}
        self.assertEqual(len(motions), 28)   # 18 davits + 3 azipods + 3 propellers + 4 thruster props
        self.assertEqual(motions["Propulsion__AzipodPort"], "azimuth")
        self.assertEqual(motions["Propulsion__AzipodPort__Propeller"], "visual")
        self.assertEqual(motions["Propulsion__BowThrusterProps__T1"], "visual")
        self.assertEqual(motions["Lifesaving__Davit_P1"], "rotation")

    @unittest.skipUnless(Path(CFG["sms_dir"]).exists(), "VR-Forces install not present")
    def test_donor_is_civilian_ship(self):
        donors = self.m["export_info"]["donor_candidates"]
        self.assertTrue(donors)
        self.assertFalse(donors[0]["military"], donors)     # no carriers for a cruise ship
        self.assertEqual(self.m["donor_entity"], donors[0]["entity"])

    def test_stale_meta_flagged_and_stations_kept(self):
        self.assertTrue(any("meta ignored" in n for n in self.notes), self.notes)
        self.assertEqual(len(self.m["export_info"]["stations"]), 5)
        self.assertEqual(len(self.m["export_info"]["sockets_blender"]), 9)


if __name__ == "__main__":
    unittest.main()
