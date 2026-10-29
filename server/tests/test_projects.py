import json

from conftest import create_ready, wait_job


def test_create_builds_v1_and_serves_it(make_client, settings):
    c = make_client()
    p = create_ready(c)
    assert p["version"] == 1 and p["build_report"]["ok"]
    assert p["spec"]["params"]["loa_m"] == 200
    assert "param_docs" not in p["spec"]  # catalog docs are not part of the spec
    assert [x["path"] for x in p["parts"] if x["station"]] == ["station__helm", "station__bow"]
    glb = c.get(p["model_url"])
    assert glb.status_code == 200 and glb.content[:4] == b"glTF"
    assert c.get(f"/api/projects/{p['id']}/versions/1/parts.json").json()[0]["path"] == "Hull__Main"
    d = settings.data_dir / "projects" / p["id"]
    assert (d / "project.json").exists() and (d / "v1" / "spec.json").exists()
    assert [s["id"] for s in c.get("/api/projects").json()] == [p["id"]]


def test_spec_patch_makes_new_version_and_undo_goes_back(make_client):
    c = make_client()
    p = create_ready(c)
    job = wait_job(c, c.post(f"/api/projects/{p['id']}/spec", json={"patch": {"params": {"beam_m": 30}}}).json()["job_id"])
    assert job["status"] == "done", job
    p2 = c.get(f"/api/projects/{p['id']}").json()
    assert p2["version"] == 2 and p2["spec"]["params"]["beam_m"] == 30 and p2["spec"]["params"]["loa_m"] == 200
    assert [v["n"] for v in p2["versions"]] == [1, 2]
    assert "params.beam_m" in p2["versions"][1]["summary"]
    p3 = c.post(f"/api/projects/{p['id']}/undo").json()["project"]
    assert p3["version"] == 1 and p3["spec"]["params"]["beam_m"] == 32.2
    # undo with nothing left is a no-op, not an error
    assert c.post(f"/api/projects/{p['id']}/undo").json()["project"]["version"] == 1


def test_failed_build_surfaces_report_errors_and_keeps_previous_version(make_client):
    c = make_client()
    p = create_ready(c)
    job = wait_job(c, c.post(f"/api/projects/{p['id']}/spec",
                             json={"patch": {"params": {"fail": "beam_m must be positive"}}}).json()["job_id"])
    assert job["status"] == "error"
    assert "Hull: beam_m must be positive" in job["error"]
    assert job["result"]["build_report"]["ok"] is False
    p2 = c.get(f"/api/projects/{p['id']}").json()
    assert p2["version"] == 1  # failed version is recorded but not current
    assert p2["versions"][-1] == {**p2["versions"][-1], "n": 2, "ok": False}


def test_missing_report_is_a_failed_build(make_client):
    c = make_client()
    p = create_ready(c)
    job = wait_job(c, c.post(f"/api/projects/{p['id']}/spec",
                             json={"patch": {"params": {"fail": "no_report"}}}).json()["job_id"])
    assert job["status"] == "error" and "no build_report.json" in job["error"]


def test_locked_spec_keys_and_bad_requests(make_client):
    c = make_client()
    p = create_ready(c)
    assert c.post(f"/api/projects/{p['id']}/spec", json={"patch": {"id": "x"}}).status_code == 400
    assert c.post("/api/projects", json={"kind": "ship", "base": "battleship"}).status_code == 400
    assert c.post("/api/projects", json={"kind": "ship", "base": "../crew/male_default"}).status_code == 400
    assert c.get("/api/projects/p_nope").status_code == 404


def test_crew_project_and_catalog(make_client):
    c = make_client()
    p = create_ready(c, "crew", "male_default")
    assert p["kind"] == "crew" and p["spec"]["body"]["height_cm"] == 178
    assert c.builder.calls[-1][0] == "crew"
    cat = c.get("/api/catalog").json()
    assert cat["ships"] == [{"type": "container", "name": "Container ship", "description": "A feeder container ship",
                             "category": None}]
    assert cat["crew"] == [{"id": "male_default", "name": "Default male crew", "category": None}]
    assert "binoculars" in cat["equipment"] and "squat" in cat["animations"] and "cap01" in cat["clothes"]


def test_project_json_is_written_atomically(settings, make_client):
    c = make_client()
    p = create_ready(c)
    d = settings.data_dir / "projects" / p["id"]
    assert not list(d.glob(".*.tmp"))
    json.loads((d / "project.json").read_text())


def test_health_shape(make_client):
    h = make_client().get("/api/health").json()
    assert set(h) == {"blender", "mpfb", "ai", "unreal"} and all(isinstance(v, bool) for v in h.values())


def test_the_real_catalog_offers_a_default_male_and_a_default_female():
    # Lester, 29 Oct: "start new should have default male and default female".
    from pathlib import Path
    from app.catalog import Catalog
    cat = Catalog(Path(__file__).resolve().parents[2] / "catalog")
    crew = {c["id"]: c for c in cat.summary()["crew"]}
    assert {"male_default", "female_default"} <= set(crew)
    assert crew["female_default"]["name"] == "Default female crew"
    assert cat.default_spec("crew", "female_default")["body"]["gender"] == "female"
