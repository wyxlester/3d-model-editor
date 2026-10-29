import json
import sys
import time
from pathlib import Path

import pytest

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))

from app.builder import read_report  # noqa: E402
from app.config import Settings  # noqa: E402

# Smallest valid GLB: 12-byte header + one JSON chunk.
_JSON = json.dumps({"asset": {"version": "2.0"}}).encode()
_JSON += b" " * (-len(_JSON) % 4)
TINY_GLB = (b"glTF" + (2).to_bytes(4, "little") + (12 + 8 + len(_JSON)).to_bytes(4, "little")
            + len(_JSON).to_bytes(4, "little") + b"JSON" + _JSON)

SHIP_PARTS = [
    {"path": "Hull__Main", "label": "Hull › Main", "parent": None, "joint": None, "station": None,
     "origin_m": [0, 0, 0], "rotation_deg": [0, 0, 0], "bounds_min": [-10, -2, -1], "bounds_max": [10, 2, 3],
     "triangles": 100},
    {"path": "Roof__Searchlight", "label": "Roof › Searchlight", "parent": "Hull__Main",
     "joint": {"axis": "z", "min_deg": -150, "max_deg": 150, "priority": "P1", "kind": "revolute"},
     "station": None, "origin_m": [2.0, 0.0, 4.5], "rotation_deg": [0, 0, 0],
     "bounds_min": [1.8, -0.2, 4.3], "bounds_max": [2.2, 0.2, 4.7], "triangles": 40},
    {"path": "station__helm", "label": "station helm", "parent": "Hull__Main", "joint": None,
     "station": {"pose": "stand", "activities": ["steer"], "label": "Helm"},
     "origin_m": [3.5, -0.6, 2.1], "rotation_deg": [0, 0, 90], "bounds_min": [3.4, -0.7, 2.0],
     "bounds_max": [3.6, -0.5, 2.2], "triangles": 0},
    {"path": "station__bow", "label": "station bow", "parent": "Hull__Main", "joint": None,
     "station": {"pose": "stand", "activities": ["lookout"], "label": "Bow lookout"},
     "origin_m": [8.0, 0.0, 1.5], "rotation_deg": [0, 0, 0], "bounds_min": [8, 0, 1.5],
     "bounds_max": [8, 0, 1.5], "triangles": 0},
]


class FakeBuilder:
    """Writes what a real builder writes (§4). params.fail -> ok:false report with errors;
    params.drop_stations -> those stations are left out of parts.json."""

    def __init__(self):
        self.calls = []

    def build(self, kind, spec_path, out_dir, log):
        spec = json.loads(Path(spec_path).read_text())
        self.calls.append((kind, spec))
        log("fake blender: building")
        out_dir = Path(out_dir)
        fail = spec.get("params", {}).get("fail")
        if fail == "no_report":
            return read_report(out_dir, " (exit code 0)")
        if not fail:
            (out_dir / "model.glb").write_bytes(TINY_GLB)
        dropped = set(spec.get("params", {}).get("drop_stations") or [])
        parts = [p for p in SHIP_PARTS if p["path"] not in dropped] if kind == "ship" else [
            {"path": "Body__Main", "label": "Body › Main", "parent": None, "joint": None, "station": None,
             "origin_m": [0, 0, 0], "rotation_deg": [0, 0, 0], "bounds_min": [0, 0, 0],
             "bounds_max": [0.5, 0.3, 1.8], "triangles": 10}]
        (out_dir / "parts.json").write_text(json.dumps(parts))
        report = {"ok": not fail, "errors": [f"Hull: {fail}"] if fail else [], "warnings": [],
                  "triangles": 140, "bounds_min": [-10, -2, -1], "bounds_max": [10, 2, 5], "seconds": 0.01}
        (out_dir / "build_report.json").write_text(json.dumps(report))
        return read_report(out_dir)


@pytest.fixture
def catalog_dir(tmp_path):
    root = tmp_path / "catalog"
    (root / "ships").mkdir(parents=True)
    (root / "crew").mkdir(parents=True)
    (root / "ships" / "container.json").write_text(json.dumps({
        "schema": "me3d.ship_spec/1", "type": "container", "id": "container_ship", "name": "Container ship",
        "description": "A feeder container ship",
        "params": {"loa_m": 200, "beam_m": 32.2, "draught_m": 11.5, "depth_m": 20.5,
                   "hull_color": [0.55, 0.12, 0.1]},
        "param_docs": {"loa_m": {"min": 50, "max": 400}, "fail": {"doc": "test hook"},
                       "drop_stations": {"doc": "test hook"}},
        "meta": {"schema": "strait.ship_model/1", "type": "container", "length_m": 999,
                 "propulsion": "shaft_rudder", "source": "catalog"},
        "overrides": {}, "joint_limits": {}, "custom_ops": []}))
    (root / "crew" / "male_default.json").write_text(json.dumps({
        "schema": "me3d.crew_spec/1", "id": "crew_male_default", "name": "Default male crew",
        "body": {"gender": "male", "height_cm": 178, "weight": 0.5, "muscle": 0.5, "age_years": 30, "skin": "medium"},
        "clothes": {"suit": "male_worksuit01", "shoes": "shoes01", "hat": None, "suit_color": None},
        "equipment": ["life_vest"], "animations": ["idle", "walk"],
        "param_docs": {"height_cm": {"min": 150, "max": 200}},
        "options": {"suit": ["male_worksuit01", "male_casualsuit02"], "shoes": ["shoes01"],
                    "hat": ["cap01"], "equipment": ["life_vest", "binoculars"],
                    "animations": ["idle", "walk", "brace", "hands_up", "squat"]}}))
    return root


@pytest.fixture
def settings(tmp_path, catalog_dir):
    # The photo follow-up turn is off by default here: scripted agents replay their steps on every
    # turn. The tests of that turn switch it on (test_research_media).
    return Settings(data_dir=tmp_path / "data", catalog_dir=catalog_dir, photo_follow_up=False,
                    plan_follow_up=False)


@pytest.fixture
def make_client(settings):
    from fastapi.testclient import TestClient
    from app.agent import ScriptedAgent
    from app.main import create_app

    def make(agent=None, llm=None, unreal=None, builder=None):
        builder = builder or FakeBuilder()
        app = create_app(settings, runner=builder, agent=agent or ScriptedAgent(),
                         llm=llm or (lambda p, s: "{}"), unreal=unreal or (lambda d, log: {"ok": True, "checks": []}))
        client = TestClient(app)
        client.builder = builder
        return client
    return make


def wait_job(client, job_id, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.02)
    raise TimeoutError(job_id)


def create_ready(client, kind="ship", base="container"):
    body = client.post("/api/projects", json={"kind": kind, "base": base}).json()
    job = wait_job(client, body["job_id"])
    assert job["status"] == "done", job
    return client.get(f"/api/projects/{body['project']['id']}").json()


def pytest_configure(config):
    config.addinivalue_line("markers", "live: real Claude call (opt in with -m live)")


def pytest_collection_modifyitems(config, items):
    if "live" in (config.getoption("-m") or ""):
        return
    skip = pytest.mark.skip(reason="live AI test; run with -m live")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)
