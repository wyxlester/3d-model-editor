"""mak/draft_mak.py: MAK settings suggestions from a version's parts.json + build report (contract §10)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import draft_mak  # noqa: E402
from pipeline.common import make_config  # noqa: E402

PARTS = [
    {"path": "Hull__Main", "parent": None, "joint": None, "station": None, "origin_m": [0, 0, 0], "rotation_deg": [0, 0, 0]},
    {"path": "Mast__Radar_2", "parent": "Mast__Main", "origin_m": [136, -4, 56], "rotation_deg": [0, 0, 0],
     "joint": {"axis": "z", "min_deg": -180, "max_deg": 180, "priority": "P1", "kind": "revolute"}, "station": None},
    {"path": "Mast__Radar_1", "parent": "Mast__Main", "origin_m": [136, 4, 56], "rotation_deg": [0, 0, 0],
     "joint": {"axis": "z", "min_deg": -180, "max_deg": 180, "priority": "P1", "kind": "revolute"}, "station": None},
    {"path": "Lifesaving__Davit_P1", "parent": "Superstructure__Base", "origin_m": [-128, 22, 15], "rotation_deg": [0, 0, 0],
     "joint": {"axis": "x", "min_deg": -55, "max_deg": 0, "priority": "P2", "kind": "revolute"}, "station": None},
    {"path": "Propulsion__Azipod__Propeller", "parent": "Propulsion__Azipod", "origin_m": [-161, 0, -6],
     "rotation_deg": [0, 0, 0], "joint": {"axis": "x", "min_deg": -180, "max_deg": 180, "priority": "P0"}, "station": None},
]
REPORT = {"bounds_min": [-181.06, -33.1, -9.3], "bounds_max": [181.06, 33.1, 65.3]}


class TestDraft(unittest.TestCase):
    def setUp(self):
        self.d = draft_mak.draft(PARTS, REPORT, "Harmony of the Seas", make_config("Z:/none", "Z:/none"))

    def test_parts_only_moving_ones_in_order(self):
        self.assertEqual([p["path"] for p in self.d["parts"]],
                         ["Lifesaving__Davit_P1", "Mast__Radar_1", "Mast__Radar_2", "Propulsion__Azipod__Propeller"])

    def test_codes_and_motions(self):
        by = {p["path"]: p for p in self.d["parts"]}
        self.assertEqual((by["Mast__Radar_1"]["suggested_code"], by["Mast__Radar_1"]["motion"]), (5376, "azimuth"))
        self.assertEqual(by["Mast__Radar_2"]["suggested_code"], 5408)
        self.assertEqual((by["Lifesaving__Davit_P1"]["suggested_code"], by["Lifesaving__Davit_P1"]["motion"]),
                         (None, "rotation"))
        self.assertEqual(by["Propulsion__Azipod__Propeller"]["motion"], "visual")
        self.assertEqual((by["Mast__Radar_1"]["min_deg"], by["Mast__Radar_1"]["priority"]), (-180, "P1"))

    def test_hint_short_name_and_no_donors_without_install(self):
        self.assertTrue(self.d["dis_type_hint"].startswith("1:3:"))
        self.assertEqual(self.d["short_name"], "Harmony of")
        self.assertEqual(self.d["donors"], [])

    def test_ship_frame_to_mak_bounds(self):
        b = draft_mak.mak_bounds(REPORT)
        self.assertEqual([round(v, 2) for v in b["extent"]], [66.2, 362.12, 74.6])   # width, length, height


if __name__ == "__main__":
    unittest.main()
