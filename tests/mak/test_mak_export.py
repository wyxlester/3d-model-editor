"""Slow gate: the MAK export script on a real package, with VR-Forces' own medfTool (contract §9b).

    server/.venv/Scripts/python -m pytest tests/mak -q

Builds an me3d.package/1 around the standard-library fixture vehicle (no Blender), runs
`mak/run_mak_export.py` as the server does (a separate process), and reads the report, not the exit code alone.
Skips without VR-Forces.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "mak"))
sys.path.insert(0, str(REPO / "mak" / "tests"))

from make_package import MAK, make_package  # noqa: E402
from pipeline.common import make_config  # noqa: E402

CFG = make_config()
HAVE = Path(CFG["medf_tool"]).exists() and (Path(CFG["sms_dir"]) / "EntityLevel" / "vrfSim" / "2S1 Gvozdika.entity").exists()


@pytest.mark.skipif(not HAVE, reason="VR-Forces 5.1.1 with medfTool not installed")
def test_export_script_on_package(tmp_path):
    pkg = make_package(tmp_path, crew=[{"id": "crew_a", "station": "station__helm"}])
    proc = subprocess.run([sys.executable, str(REPO / "mak" / "run_mak_export.py"), str(pkg)],
                          capture_output=True, text=True, timeout=600)
    report = json.loads((pkg / "mak_export_report.json").read_text())
    assert proc.returncode == (0 if report["ok"] else 1)
    assert report["ok"], proc.stdout + proc.stderr
    checks = {c["name"]: c for c in report["checks"]}
    assert checks["verify_model:mak-loaded"]["pass"]            # MAK's own reader loaded the model
    assert checks["verify_model:flt-verified"]["pass"]
    assert report["dis_type"] == MAK["dis_type"] and report["not_exported"]
    info = (pkg / "mak" / "SharedData" / "ModelData" / "Vehicles" / "Surface" / "test_boat" / "test_boat.flt")
    assert info.stat().st_size > 1000
    leaf = (pkg / "mak" / "sms" / "gui" / "visuals" / "Entity" / "test_boat.leaf").read_text()
    assert "<myName>test_boat</myName>" in leaf
