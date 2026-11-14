"""A small me3d.package/1 (contract §8) around the stdlib fixture glb, for the MAK export tests.

The fixture vehicle (make_fixture.make_glb) has hull > turret > gun. The articulation below is written in the
editor's frame (+X bow, +Y port, +Z up): the turret slews about z, the gun elevates about the lateral y axis.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from make_fixture import make_glb

ARTICULATION = [
    {"part": "turret", "parent": "hull", "axis": "z", "min_deg": -180.0, "max_deg": 180.0, "priority": "P0",
     "kind": "revolute", "pivot_m": [0.0, 0.0, 1.5], "rotation_deg": [0.0, 0.0, 0.0]},
    {"part": "gun", "parent": "turret", "axis": "y", "min_deg": -5.0, "max_deg": 70.0, "priority": "P0",
     "kind": "revolute", "pivot_m": [0.0, 0.8, 1.75], "rotation_deg": [0.0, 0.0, 0.0]},
]
MAK = {"dis_type": "1:1:225:99:99:99:99", "donor_entity": "2S1 Gvozdika", "short_name": None,
       "parts": {"turret": 4096, "gun": 4416}}


def make_package(root: Path, *, mak: dict | None = MAK, articulation=None, crew=None,
                 ship_id: str = "test_boat", name: str = "Test boat") -> Path:
    pkg = Path(root) / f"pkg_{ship_id}_v1_scene_x"
    if pkg.exists():
        shutil.rmtree(pkg)
    (pkg / "ship").mkdir(parents=True)
    make_glb(pkg / "ship" / f"{ship_id}.glb")
    (pkg / "ship" / f"{ship_id}.json").write_text(json.dumps(
        {"schema": "strait.ship_model/1", "id": ship_id, "name": name, "type": "container",
         "length_m": 5.0, "beam_m": 2.0, "draught_m": 0.0}))
    (pkg / "ship" / "articulation.json").write_text(json.dumps(ARTICULATION if articulation is None else articulation))
    if mak is not None:
        (pkg / "ship" / "mak.json").write_text(json.dumps(mak))
    crew = crew or []
    manifest = {"schema": "me3d.package/1", "id": pkg.name, "name": name, "created": "2026-11-14T00:00:00Z",
                "units": "metres", "source_frame": "+X bow, +Y port, +Z up; glTF files are +Y up",
                "ship": {"id": ship_id, "glb": f"ship/{ship_id}.glb", "meta": f"ship/{ship_id}.json",
                         "articulation": "ship/articulation.json"},
                "crew": crew, "research": None}
    (pkg / "manifest.json").write_text(json.dumps(manifest))
    return pkg
