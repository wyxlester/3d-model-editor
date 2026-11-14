"""mak/install.py: copy an export into a custom SMS, never a stock one, backing up what it replaces."""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import install  # noqa: E402
from pipeline.common import make_config  # noqa: E402


class TestInstall(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.vrf = self.tmp / "vrforces"
        self.shared = self.tmp / "SharedData" / "18" / "latest"
        sms = self.vrf / "data" / "simulationModelSets"
        (sms / "MyShips" / "vrfSim").mkdir(parents=True)
        (sms / "MyShips.sms").write_text("(simulation-model-set)")
        (sms / "EntityLevel").mkdir()
        (sms / "EntityLevel.sms").write_text("(simulation-model-set)")
        self.cfg = make_config(self.vrf, self.shared)
        # an export, as run_mak_export.py leaves it
        self.out = self.tmp / "pkg" / "mak"
        for rel, text in {
            "SharedData/ModelData/Vehicles/Surface/boat/boat.flt": "flt",
            "SharedData/ModelData/Vehicles/Surface/boat/boat_tex0.png": "png",
            "sms/vrfSim/Boat.entity": "<simObjects/>",
            "sms/gui/visuals/Entity/boat.leaf": "<leaf/>",
            "sms/gui/visuals/Entity/boat.metx": "<metx/>",
            "sms/gui/visuals/UserData/boat.ommx": "<ommx/>",
            "visual-import/boat.leaf": "<leaf/>",
        }.items():
            p = self.out / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        self.backups = self.tmp / "backups"

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_copies_model_and_sms_files(self):
        r = install.install(self.out, "MyShips", self.cfg, self.backups)
        sms = self.vrf / "data" / "simulationModelSets" / "MyShips"
        self.assertEqual((sms / "vrfSim" / "Boat.entity").read_text(), "<simObjects/>")
        self.assertEqual((sms / "gui" / "visuals" / "Entity" / "boat.leaf").read_text(), "<leaf/>")
        self.assertEqual((sms / "gui" / "visuals" / "UserData" / "boat.ommx").read_text(), "<ommx/>")
        self.assertEqual((self.shared / "ModelData/Vehicles/Surface/boat/boat.flt").read_text(), "flt")
        self.assertEqual(len(r["written"]), 6)                       # visual-import/ is not installed
        self.assertFalse((sms / "visual-import").exists())
        self.assertEqual(r["sms"], "MyShips")

    def test_backs_up_replaced_files(self):
        target = self.vrf / "data" / "simulationModelSets" / "MyShips" / "vrfSim" / "Boat.entity"
        target.write_text("old")
        r = install.install(self.out, "MyShips", self.cfg, self.backups)
        self.assertEqual(target.read_text(), "<simObjects/>")
        backed = Path(r["backups_dir"]) / "sms" / "vrfSim" / "Boat.entity"
        self.assertEqual(backed.read_text(), "old")

    def test_no_backup_dir_when_nothing_replaced(self):
        r = install.install(self.out, "MyShips", self.cfg, self.backups)
        self.assertIsNone(r["backups_dir"])

    def test_refuses_stock_sms(self):
        for name in ("EntityLevel", "AggregateLevel", "base", "AggregateLevelBase", "MAKTest"):
            with self.assertRaisesRegex(install.InstallError, "stock"):
                install.install(self.out, name, self.cfg, self.backups)

    def test_refuses_unknown_sms(self):
        with self.assertRaisesRegex(install.InstallError, "MyShps.*MyShips"):
            install.install(self.out, "MyShps", self.cfg, self.backups)

    def test_refuses_path_like_names(self):
        for bad in ("../EntityLevel", "My/Ships", ""):
            with self.assertRaises(install.InstallError):
                install.install(self.out, bad, self.cfg, self.backups)

    def test_refuses_incomplete_export(self):
        shutil.rmtree(self.out / "sms" / "vrfSim")
        with self.assertRaisesRegex(install.InstallError, "entity"):
            install.install(self.out, "MyShips", self.cfg, self.backups)

    def test_custom_sms_listing(self):
        self.assertEqual(install.custom_sms(self.cfg), ["MyShips"])


if __name__ == "__main__":
    unittest.main()
