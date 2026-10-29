"""Deleting a ship or crew project (Lester, 29 Oct: "should also allow user to delete ship. if choose
delete ship, render a dialog box to confirm with user, before actually deleting").

DELETE /api/projects/{id} moves the folder to data/trash/<id>-<timestamp>/; afterwards the project
is gone from every list and lookup. Refused (409, with a sentence) while a scene uses it or one of
its jobs is queued or running. Its variants stay, still naming it in derived_from."""
import json
import threading

from app.agent import ScriptedAgent
from conftest import create_ready, wait_job


class HeldAgent(ScriptedAgent):
    """Holds its turn until `go` is set, so a job is running when the test asks for a delete."""

    def __init__(self):
        super().__init__(reply="ok")
        self.go, self.started = threading.Event(), threading.Event()

    def run(self, session, prompt, mode, log):
        self.started.set()
        assert self.go.wait(10)
        return super().run(session, prompt, mode, log)


def scene(c, ship, crew=()):
    r = c.post("/api/scenes", json={
        "schema": "me3d.scene_spec/1", "name": "Boarding drill", "ship": {"project": ship["id"]},
        "crew": [{"project": m["id"], "station": "station__helm", "animation": "idle"} for m in crew]})
    assert r.status_code == 200, r.text
    return r.json()["scene"]


def test_delete_moves_the_folder_to_trash_and_then_it_is_gone(make_client, settings):
    c = make_client()
    ship = create_ready(c)
    keep = create_ready(c)
    r = c.delete(f"/api/projects/{ship['id']}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deleted"] == ship["id"] and body["trash"].startswith(f"trash/{ship['id']}-")
    moved = settings.data_dir / body["trash"]
    assert json.loads((moved / "project.json").read_text())["id"] == ship["id"]  # recoverable by hand
    assert (moved / "v1" / "model.glb").is_file() and (moved / "v1" / "spec.json").is_file()
    assert not (settings.projects_dir / ship["id"]).exists()
    # gone from every list, route and API
    assert c.get(f"/api/projects/{ship['id']}").status_code == 404
    assert c.get(f"/api/projects/{ship['id']}/versions/1/model.glb").status_code == 404
    assert [p["id"] for p in c.get("/api/projects").json()] == [keep["id"]]
    assert c.post(f"/api/projects/{ship['id']}/chat", json={"message": "hi"}).status_code == 404
    assert c.delete(f"/api/projects/{ship['id']}").status_code == 404  # deleting twice
    assert c.get(f"/api/projects/{keep['id']}").status_code == 200  # the other one is untouched


def test_a_ship_a_scene_uses_is_refused_with_the_scene_named(make_client, settings):
    c = make_client()
    ship = create_ready(c)
    s = scene(c, ship)
    r = c.delete(f"/api/projects/{ship['id']}")
    assert r.status_code == 409
    said = r.json()["detail"]
    assert "“Boarding drill” (as the ship)" in said and said.startswith(f"{ship['name']} can't be deleted")
    assert "scenes can't be deleted yet" in said
    assert c.get(f"/api/projects/{ship['id']}").status_code == 200  # nothing moved
    assert not settings.trash_dir.exists()
    assert c.get(f"/api/scenes/{s['id']}").status_code == 200


def test_crew_a_scene_uses_is_refused_and_can_go_once_taken_off(make_client):
    c = make_client()
    ship = create_ready(c)
    crew = create_ready(c, "crew", "male_default")
    s = scene(c, ship, [crew])
    r = c.delete(f"/api/projects/{crew['id']}")
    assert r.status_code == 409
    said = r.json()["detail"]
    assert "“Boarding drill” (as crew)" in said and "Take it off in Assembly first" in said
    # take them off the scene, and the delete goes through
    assert c.put(f"/api/scenes/{s['id']}", json={**s, "crew": []}).status_code == 200
    assert c.delete(f"/api/projects/{crew['id']}").status_code == 200
    assert c.get(f"/api/projects/{crew['id']}").status_code == 404


def test_refused_while_one_of_its_jobs_is_running(make_client):
    agent = HeldAgent()
    c = make_client(agent=agent)
    ship = create_ready(c)
    job_id = c.post(f"/api/projects/{ship['id']}/chat", json={"message": "make the hull red"}).json()["job_id"]
    assert agent.started.wait(10)
    r = c.delete(f"/api/projects/{ship['id']}")
    assert r.status_code == 409
    assert "being worked on" in r.json()["detail"] and "make the hull red" in r.json()["detail"]
    assert c.get(f"/api/projects/{ship['id']}").status_code == 200
    agent.go.set()
    assert wait_job(c, job_id)["status"] == "done"
    assert c.delete(f"/api/projects/{ship['id']}").status_code == 200


def test_a_variant_of_a_deleted_project_still_lists_and_opens(make_client):
    c = make_client()
    src = create_ready(c)
    out = c.post(f"/api/projects/{src['id']}/variants", json={"name": "Refit"}).json()
    assert wait_job(c, out["job_id"])["status"] == "done"
    vid = out["project"]["id"]
    assert c.delete(f"/api/projects/{src['id']}").status_code == 200
    listed = {p["id"]: p for p in c.get("/api/projects").json()}
    assert list(listed) == [vid]
    assert listed[vid]["derived_from"]["project"] == src["id"]  # still names it
    v = c.get(f"/api/projects/{vid}").json()
    assert v["version"] == 1 and v["derived_from"]["name"] == src["name"]
    # the variant still works: approve it, and a variant of the variant can be made
    assert c.post(f"/api/projects/{vid}/approve").status_code == 200
    again = c.post(f"/api/projects/{vid}/variants", json={"name": "Refit 2"})
    assert again.status_code == 200, again.text
    assert wait_job(c, again.json()["job_id"])["status"] == "done"
    # the job centre does not trip over the deleted project's finished jobs
    assert c.get("/api/jobs").status_code == 200
