"""me3d.package/1 -> the pipeline's model folder (mak/from_package.py, contract §9b)."""
from __future__ import annotations

import copy
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import from_package  # noqa: E402
from make_package import ARTICULATION, MAK, make_package  # noqa: E402
from pipeline.common import load_manifest  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def convert(self, **kw):
        pkg = make_package(self.tmp, **kw)
        return from_package.prepare(pkg, self.tmp / "work")


class TestPrepare(Base):
    def test_model_folder_and_fixed_editor_facts(self):
        p = self.convert()
        self.assertTrue((p.model_dir / "model.glb").exists())
        m = load_manifest(p.model_dir)                  # passes the pipeline's own manifest checks
        self.assertEqual((m["name"], m["display_name"]), ("test_boat", "Test boat"))
        self.assertEqual((m["domain"], m["forward_axis_in_blender"]), ("surface", "+X"))   # contract §1
        self.assertEqual((m["model_category_dir"], m["platform_ope"]), ("Vehicles/Surface", "Surface_Ship.ope"))
        self.assertEqual((m["dis_type"], m["donor_entity"]), (MAK["dis_type"], MAK["donor_entity"]))

    def test_joints_become_parts_with_codes_and_motions(self):
        m = self.convert().manifest
        self.assertEqual(m["articulated_parts"], [
            {"node": "turret", "dis_part": 4096, "parent": None, "motion": "azimuth", "min": -180.0, "max": 180.0},
            {"node": "gun", "dis_part": 4416, "parent": "turret", "motion": "elevation", "min": -5.0, "max": 70.0},
        ])
        self.assertEqual(m["unmapped_joints"], [])

    def test_part_without_code_stays_static(self):
        mak = dict(MAK, parts={"turret": 4096, "gun": None})
        p = self.convert(mak=mak)
        self.assertEqual([x["node"] for x in p.manifest["articulated_parts"]], ["turret"])
        self.assertEqual([x["node"] for x in p.manifest["unmapped_joints"]], ["gun"])
        self.assertIn("gun stays static (no DIS part code)", p.pending)

    def test_parent_skips_unmapped_ancestors(self):
        mak = dict(MAK, parts={"turret": None, "gun": 4416})
        m = self.convert(mak=mak).manifest
        self.assertEqual(m["articulated_parts"][0]["parent"], None)

    def test_local_axis_turned_by_part_rotation(self):
        art = copy.deepcopy(ARTICULATION)
        art[1]["axis"] = "x"
        art[1]["rotation_deg"] = [0.0, 0.0, 90.0]      # local x points along world +Y (lateral) -> elevation
        m = self.convert(articulation=art).manifest
        self.assertEqual(m["articulated_parts"][1]["motion"], "elevation")

    def test_full_turn_about_horizontal_axis_is_visual(self):
        art = copy.deepcopy(ARTICULATION)
        art[1].update(axis="x", min_deg=-180.0, max_deg=180.0)     # a propeller
        m = self.convert(articulation=art).manifest
        self.assertEqual(m["articulated_parts"][1]["motion"], "visual")

    def test_kind_comes_from_the_settings_and_defaults_to_a_ship(self):
        self.assertEqual(self.convert().manifest["kind"], "surface_ship")
        m = self.convert(mak=dict(MAK, kind="ground_vehicle")).manifest
        self.assertEqual((m["kind"], m["domain"], m["platform_ope"], m["model_category_dir"], m["forward_axis_in_blender"]),
                         ("ground_vehicle", "land", "Ground_Vehicle.ope", "Vehicles/Wheeled", "+X"))
        with self.assertRaisesRegex(from_package.PackageError, "kind must be one of"):
            self.convert(mak=dict(MAK, kind="spaceship"))
        with self.assertRaisesRegex(from_package.PackageError, "DI-Guy"):
            self.convert(mak=dict(MAK, kind="human"))

    def test_crew_listed_as_not_exported(self):
        crew = [{"id": "crew_a", "glb": "crew/crew_a.glb", "station": "station__helm", "animation": "idle",
                 "location_m": [0, 0, 0], "rotation_deg": [0, 0, 0]}]
        p = self.convert(crew=crew)
        self.assertEqual(p.not_exported, ["crew_a at station__helm (VR-Forces crew need DI-Guy characters)"])

    def test_short_name_carried(self):
        m = self.convert(mak=dict(MAK, short_name="TB")).manifest
        self.assertEqual(m["short_name"], "TB")

    def test_ship_id_starting_with_digit(self):
        m = self.convert(ship_id="3_series", name="3-Series").manifest
        self.assertEqual(m["name"], "s_3_series")


class TestRefusals(Base):
    def test_no_mak_settings(self):
        with self.assertRaisesRegex(from_package.PackageError, "no MAK settings"):
            self.convert(mak=None)

    def test_not_a_package(self):
        (self.tmp / "x").mkdir()
        with self.assertRaisesRegex(from_package.PackageError, "manifest.json"):
            from_package.prepare(self.tmp / "x", self.tmp / "work")

    def test_unknown_part_in_settings(self):
        with self.assertRaisesRegex(from_package.PackageError, "rudder"):
            self.convert(mak=dict(MAK, parts={"turret": 4096, "rudder": 1025}))


if __name__ == "__main__":
    unittest.main()
