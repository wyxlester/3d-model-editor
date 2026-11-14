"""mak/run_mak_export.py: package -> <package>/mak/ + mak_export_report.json (contract §9b)."""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_mak_export  # noqa: E402
from make_package import MAK, make_package  # noqa: E402
from pipeline import generate  # noqa: E402
from pipeline.common import make_config  # noqa: E402

CFG = make_config()
HAVE_INSTALL = generate.donor_path(CFG, "2S1 Gvozdika").exists()
HAVE_MEDF = Path(CFG["medf_tool"]).exists()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)


class TestWithoutVrForces(Base):
    def test_missing_settings_gives_failed_report(self):
        pkg = make_package(self.tmp, mak=None)
        report = run_mak_export.export(pkg, CFG)
        self.assertFalse(report["ok"])
        self.assertEqual(report["checks"][0]["name"], "package")
        self.assertIn("no MAK settings", report["checks"][0]["detail"])
        self.assertEqual(json.loads((pkg / "mak_export_report.json").read_text())["ok"], False)

    def test_missing_donor_fails_the_report(self):
        cfg = make_config(self.tmp / "no_vrforces", self.tmp / "no_shared")
        pkg = make_package(self.tmp)
        report = run_mak_export.export(pkg, cfg, mak_check=False)
        self.assertFalse(report["ok"])
        failed = [c["name"] for c in report["checks"] if not c["pass"]]
        self.assertIn("generate:donor-missing", failed)
        self.assertEqual(report["dis_type"], MAK["dis_type"])
        self.assertTrue((pkg / "mak" / "SharedData" / "ModelData" / "Vehicles" / "Surface" / "test_boat" /
                         "test_boat.flt").exists())            # the model is still written

    def test_exit_code_follows_ok(self):
        pkg = make_package(self.tmp, mak=None)
        self.assertEqual(run_mak_export.main([str(pkg)]), 1)


@unittest.skipUnless(HAVE_INSTALL, "VR-Forces install not present")
class TestWithVrForces(Base):
    def test_full_export(self):
        crew = [{"id": "crew_a", "glb": "crew/crew_a.glb", "station": "station__helm", "animation": "idle",
                 "location_m": [0, 0, 0], "rotation_deg": [0, 0, 0]}]
        pkg = make_package(self.tmp, crew=crew, mak=dict(MAK, parts={"turret": 4096, "gun": None}))
        report = run_mak_export.export(pkg, CFG, mak_check=HAVE_MEDF)
        self.assertTrue(report["ok"], json.dumps(report, indent=1))
        names = {c["name"] for c in report["checks"]}
        self.assertIn("verify_model:flt-verified", names)
        if HAVE_MEDF:
            self.assertIn("verify_model:mak-loaded", names)
        self.assertEqual(report["not_exported"], ["crew_a at station__helm (VR-Forces crew need DI-Guy characters)"])
        self.assertIn("gun stays static (no DIS part code)", report["pending"])
        out = pkg / "mak"
        for rel in ("sms/gui/visuals/Entity/test_boat.leaf", "sms/gui/visuals/Entity/test_boat.metx",
                    "sms/gui/visuals/UserData/test_boat.ommx", "sms/vrfSim/Test boat.entity",
                    "visual-import/test_boat.leaf"):
            self.assertTrue((out / rel).exists(), rel)
        self.assertEqual((report["name"], report["vrforces"]), ("test_boat", "5.1.1"))


if __name__ == "__main__":
    unittest.main()
