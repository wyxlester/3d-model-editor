import json
import zipfile
from pathlib import Path

from conftest import SHIP_PARTS, FakeBuilder, create_ready, wait_job


def scene_body(ship, crew, stations):
    return {"schema": "me3d.scene_spec/1", "name": "Patrol boat with crew",
            "ship": {"project": ship["id"], "version": ship["version"]},
            "crew": [{"project": crew["id"], "version": crew["version"], "station": s, "animation": "idle"}
                     for s in stations]}


def test_scene_stations_and_export_package(make_client, settings):
    c = make_client()
    ship, crew = create_ready(c), create_ready(c, "crew", "male_default")
    wait_job(c, c.post(f"/api/projects/{ship['id']}/chat", json={"message": "research", "mode": "research"}).json()["job_id"])
    scene = c.post("/api/scenes", json=scene_body(ship, crew, ["station__helm", "station__bow"])).json()["scene"]
    assert scene["id"].startswith("scene_")
    assert [s["name"] for s in scene["stations"]] == ["station__helm", "station__bow"]
    assert scene["stations"][0]["label"] == "Helm" and scene["stations"][0]["origin_m"] == [3.5, -0.6, 2.1]
    assert c.get(f"/api/scenes/{scene['id']}").json()["stations"] == scene["stations"]
    assert [s["id"] for s in c.get("/api/scenes").json()] == [scene["id"]]

    job = wait_job(c, c.post(f"/api/scenes/{scene['id']}/export").json()["job_id"])
    assert job["status"] == "done", job
    pkg = Path(job["result"]["package_dir"])
    assert pkg.is_absolute() and pkg.parent == (settings.data_dir / "packages").resolve()
    m = json.loads((pkg / "manifest.json").read_text())
    assert m["schema"] == "me3d.package/1" and m["units"] == "metres"
    assert m["ship"] == {"id": "container_ship", "glb": "ship/container_ship.glb",
                         "meta": "ship/container_ship.json", "articulation": "ship/articulation.json"}
    # station -> crew transform, taken verbatim from the station's world origin/rotation
    assert [(e["station"], e["location_m"], e["rotation_deg"]) for e in m["crew"]] == [
        ("station__helm", [3.5, -0.6, 2.1], [0.0, 0.0, 90.0]),
        ("station__bow", [8.0, 0.0, 1.5], [0.0, 0.0, 0.0])]
    # same crew project+version twice -> one GLB
    assert {e["glb"] for e in m["crew"]} == {"crew/crew_male_default.glb"}
    assert sorted(p.name for p in (pkg / "crew").iterdir()) == ["crew_male_default.glb"]
    assert (pkg / "ship" / "container_ship.glb").read_bytes()[:4] == b"glTF"
    art = json.loads((pkg / "ship" / "articulation.json").read_text())
    assert art == [{"part": "Roof__Searchlight", "parent": "Hull__Main", "axis": "z", "min_deg": -150,
                    "max_deg": 150, "priority": "P1", "kind": "revolute", "pivot_m": [2.0, 0.0, 4.5],
                    "rotation_deg": [0.0, 0.0, 0.0]}]
    assert not (pkg / "ship" / "mak.json").exists()        # only written when the ship has MAK settings (§8)
    meta = json.loads((pkg / "ship" / "container_ship.json").read_text())
    assert meta["schema"] == "strait.ship_model/1" and meta["id"] == "container_ship"
    assert (meta["type"], meta["length_m"], meta["beam_m"], meta["draught_m"]) == ("container", 200, 32.2, 11.5)
    assert meta["propulsion"] == "shaft_rudder"  # from the spec's catalog meta
    assert m["research"] is None and not (pkg / "research").exists()  # scripted agent saved nothing
    z = c.get(job["result"]["zip_url"])
    assert z.status_code == 200
    names = zipfile.ZipFile(__import__("io").BytesIO(z.content)).namelist()
    assert f"{pkg.name}/manifest.json" in names

    report = wait_job(c, c.post(f"/api/scenes/{scene['id']}/unreal-test").json()["job_id"])
    assert report["result"] == {"ok": True, "checks": []}


def test_export_refuses_station_without_transform(make_client, monkeypatch):
    import conftest
    parts = json.loads(json.dumps(SHIP_PARTS))
    del parts[2]["origin_m"]
    monkeypatch.setattr(conftest, "SHIP_PARTS", parts)
    c = make_client(builder=FakeBuilder())
    ship, crew = create_ready(c), create_ready(c, "crew", "male_default")
    scene = c.post("/api/scenes", json=scene_body(ship, crew, ["station__helm"])).json()["scene"]
    job = wait_job(c, c.post(f"/api/scenes/{scene['id']}/export").json()["job_id"])
    assert job["status"] == "error" and "station__helm" in job["error"] and "origin_m" in job["error"]


def test_export_refuses_unknown_station(make_client):
    c = make_client()
    ship, crew = create_ready(c), create_ready(c, "crew", "male_default")
    scene = c.post("/api/scenes", json=scene_body(ship, crew, ["station__crows_nest"])).json()["scene"]
    job = wait_job(c, c.post(f"/api/scenes/{scene['id']}/export").json()["job_id"])
    assert job["status"] == "error" and "station__helm" in job["error"]


def test_scene_validation_and_put(make_client):
    c = make_client()
    ship, crew = create_ready(c), create_ready(c, "crew", "male_default")
    assert c.post("/api/scenes", json=scene_body(crew, crew, [])).status_code == 400  # crew as ship
    assert c.post("/api/scenes", json=scene_body(ship, ship, ["station__bow"])).status_code == 400  # ship as crew
    scene = c.post("/api/scenes", json=scene_body(ship, crew, [])).json()["scene"]
    body = scene_body(ship, crew, ["station__bow"])
    put = c.put(f"/api/scenes/{scene['id']}", json=body).json()["scene"]
    assert put["id"] == scene["id"] and put["crew"][0]["station"] == "station__bow"
    assert c.get("/api/scenes/scene_nothere").status_code == 404
    assert c.get("/api/scenes/..%2fprojects").status_code == 404
