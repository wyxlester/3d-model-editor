"""MAK VR-Forces export endpoints (contract §9b, §10): settings, draft, export job, SMS install."""
import json
from pathlib import Path

import pytest

from conftest import create_ready, wait_job

GOOD = {"dis_type": "1:3:15:80:1:0:0", "donor_entity": "Super_Tanker", "short_name": "Box",
        "parts": {"Roof__Searchlight": 5376}}


@pytest.fixture
def vrf(settings, tmp_path):
    """A fake VR-Forces install: a donor entity, a stock SMS and one custom SMS."""
    root = tmp_path / "vrforces"
    sms = root / "data" / "simulationModelSets"
    (sms / "EntityLevel" / "vrfSim").mkdir(parents=True)
    (sms / "EntityLevel" / "vrfSim" / "Super_Tanker.entity").write_text("<simObjects/>")
    (sms / "EntityLevel.sms").write_text("(simulation-model-set)")
    (sms / "MyShips").mkdir()
    (sms / "MyShips.sms").write_text("(simulation-model-set)")
    settings.vrf_dir = root
    settings.mak_shared_data = tmp_path / "SharedData" / "18" / "latest"
    return root


def fake_mak(ok=True):
    """Writes what run_mak_export.py writes (§9b), so install has something to copy."""
    def run(pkg: Path, log):
        out = pkg / "mak"
        for rel in ("SharedData/ModelData/Vehicles/Surface/container_ship/container_ship.flt",
                    "sms/vrfSim/Container ship.entity", "sms/gui/visuals/Entity/container_ship.leaf",
                    "sms/gui/visuals/Entity/container_ship.metx", "sms/gui/visuals/UserData/container_ship.ommx"):
            (out / rel).parent.mkdir(parents=True, exist_ok=True)
            (out / rel).write_text("x")
        report = {"ok": ok, "vrforces": "5.1.1", "name": "container_ship", "dis_type": GOOD["dis_type"],
                  "checks": [{"name": "verify_model:mak-loaded", "pass": ok, "detail": "loaded"}],
                  "pending": [], "not_exported": []}
        (pkg / "mak_export_report.json").write_text(json.dumps(report))
        return report
    return run


def scene_for(client, ship):
    return client.post("/api/scenes", json={"ship": {"project": ship["id"]}, "crew": []}).json()["scene"]


def test_health_reports_mak(make_client, vrf):
    assert make_client().get("/api/health").json()["mak"] is False      # no medfTool in the fake install
    (vrf / "bin64").mkdir()
    (vrf / "bin64" / "medfTool.exe").write_text("")
    assert make_client().get("/api/health").json()["mak"] is True


def test_save_settings_without_new_version(make_client, vrf):
    c = make_client()
    ship = create_ready(c)
    r = c.patch(f"/api/projects/{ship['id']}/mak", json=GOOD)
    assert r.status_code == 200, r.text
    assert r.json()["project"]["mak"] == GOOD
    assert r.json()["project"]["version"] == ship["version"]
    assert c.get(f"/api/projects/{ship['id']}").json()["mak"] == GOOD


@pytest.mark.parametrize("change, words", [
    ({"dis_type": "1:3:15:80"}, "7"),
    ({"donor_entity": "Nope"}, "Nope"),
    ({"parts": {"Hull__Main": 4096}}, "Hull__Main"),
    ({"parts": {"Roof__Searchlight": "radar"}}, "integer"),
    ({"short_name": "A very long label"}, "11"),
    ({"surprise": 1}, "surprise"),
])
def test_bad_settings_refused(make_client, vrf, change, words):
    c = make_client()
    ship = create_ready(c)
    r = c.patch(f"/api/projects/{ship['id']}/mak", json={**GOOD, **change})
    assert r.status_code == 400 and words in r.json()["detail"], r.text


def test_kind_is_optional_and_checked(make_client, vrf):
    c = make_client()
    ship = create_ready(c)
    r = c.patch(f"/api/projects/{ship['id']}/mak", json={**GOOD, "kind": "surface_ship"})
    assert r.status_code == 200 and r.json()["project"]["mak"]["kind"] == "surface_ship", r.text
    for bad in ("human", "spaceship"):                  # a lifeform is a DI-Guy character, not a model
        r = c.patch(f"/api/projects/{ship['id']}/mak", json={**GOOD, "kind": bad})
        assert r.status_code == 400 and "kind must be one of" in r.json()["detail"], r.text


def test_static_part_and_missing_short_name_allowed(make_client, vrf):
    c = make_client()
    ship = create_ready(c)
    body = {**GOOD, "short_name": None, "parts": {"Roof__Searchlight": None}}
    assert c.patch(f"/api/projects/{ship['id']}/mak", json=body).status_code == 200


def test_crew_has_no_mak_settings(make_client, vrf):
    c = make_client()
    crew = create_ready(c, kind="crew", base="male_default")
    assert c.patch(f"/api/projects/{crew['id']}/mak", json=GOOD).status_code == 400
    assert c.get(f"/api/projects/{crew['id']}/mak/draft").status_code == 400


def test_draft(make_client, vrf):
    c = make_client()
    ship = create_ready(c)
    d = c.get(f"/api/projects/{ship['id']}/mak/draft").json()
    assert d["parts"] == [{"path": "Roof__Searchlight", "motion": "azimuth", "min_deg": -150, "max_deg": 150,
                           "priority": "P1", "suggested_code": None}]
    assert d["dis_type_hint"].startswith("1:3:")
    assert d["short_name"] == "Container"


def test_export_needs_settings(make_client, vrf):
    c = make_client(mak=fake_mak())
    ship = create_ready(c)
    scene = scene_for(c, ship)
    r = c.post(f"/api/scenes/{scene['id']}/mak-export")
    assert r.status_code == 409 and "DIS type" in r.json()["detail"]


def test_export_job_writes_settings_into_package(make_client, vrf, settings):
    c = make_client(mak=fake_mak())
    ship = create_ready(c)
    c.patch(f"/api/projects/{ship['id']}/mak", json=GOOD)
    scene = scene_for(c, ship)
    job = wait_job(c, c.post(f"/api/scenes/{scene['id']}/mak-export").json()["job_id"])
    assert job["status"] == "done", job
    res = job["result"]
    assert res["ok"] is True and res["checks"][0]["name"] == "verify_model:mak-loaded"
    pkg = Path(res["package_dir"])
    # the settings plus the ship project's name, which names the VR-Forces entity
    assert json.loads((pkg / "ship" / "mak.json").read_text()) == {**GOOD, "name": ship["name"]}
    art = json.loads((pkg / "ship" / "articulation.json").read_text())
    assert art[0]["rotation_deg"] == [0, 0, 0]
    assert Path(res["output_dir"]) == pkg / "mak"
    assert c.get(res["zip_url"]).status_code == 200


def test_install_flow(make_client, vrf, settings):
    c = make_client(mak=fake_mak())
    ship = create_ready(c)
    c.patch(f"/api/projects/{ship['id']}/mak", json=GOOD)
    scene = scene_for(c, ship)
    url = f"/api/scenes/{scene['id']}/mak-install"
    assert c.post(url, json={"sms": "MyShips", "confirm": True}).status_code == 409      # nothing exported yet
    wait_job(c, c.post(f"/api/scenes/{scene['id']}/mak-export").json()["job_id"])
    assert c.get("/api/mak/sms").json() == {"sms": ["MyShips"]}
    assert c.post(url, json={"sms": "MyShips"}).status_code == 400                       # no confirm
    r = c.post(url, json={"sms": "EntityLevel", "confirm": True})
    assert r.status_code == 400 and "stock" in r.json()["detail"]
    assert c.post(url, json={"sms": "Nope", "confirm": True}).status_code == 400
    job = wait_job(c, c.post(url, json={"sms": "MyShips", "confirm": True}).json()["job_id"])
    assert job["status"] == "done", job
    sms = vrf / "data" / "simulationModelSets" / "MyShips"
    assert (sms / "vrfSim" / "Container ship.entity").exists()
    assert (sms / "gui" / "visuals" / "Entity" / "container_ship.leaf").exists()
    assert (settings.mak_shared_data / "ModelData/Vehicles/Surface/container_ship/container_ship.flt").exists()
    assert len(job["result"]["written"]) == 5 and job["result"]["sms"] == "MyShips"


def test_install_refuses_failed_export(make_client, vrf):
    c = make_client(mak=fake_mak(ok=False))
    ship = create_ready(c)
    c.patch(f"/api/projects/{ship['id']}/mak", json=GOOD)
    scene = scene_for(c, ship)
    wait_job(c, c.post(f"/api/scenes/{scene['id']}/mak-export").json()["job_id"])
    r = c.post(f"/api/scenes/{scene['id']}/mak-install", json={"sms": "MyShips", "confirm": True})
    assert r.status_code == 409
