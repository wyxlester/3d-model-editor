"""Variants: a new project copied from one version of another, then changed by the AI from an
optional brief, and kept as a draft until the user approves it (contract §10)."""
import json
import threading
from pathlib import Path

from app.agent import ScriptedAgent, describe_tool
from app.router import parse_route
from app.specmerge import variant_id
from conftest import create_ready, wait_job

HIDE = ("set_part_override", {"part": "Roof__Searchlight", "override": {"hidden": True}})


def make_variant(c, source, **body):
    r = c.post(f"/api/projects/{source['id']}/variants", json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    job = wait_job(c, out["job_id"])
    return c.get(f"/api/projects/{out['project']['id']}").json(), job


def approve(c, v):
    r = c.post(f"/api/projects/{v['id']}/approve")
    assert r.status_code == 200, r.text
    return r.json()


def scene_for(c, ship, crew, stations):
    return c.post("/api/scenes", json={
        "schema": "me3d.scene_spec/1", "name": "Boarding", "ship": {"project": ship["id"], "version": ship["version"]},
        "crew": [{"project": crew["id"], "version": crew["version"], "station": s, "animation": "idle"}
                 for s in stations]}).json()["scene"]


class GatedAgent(ScriptedAgent):
    """Holds the AI step until `go` is set, so a test can look at a variant mid-build."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.go, self.started = threading.Event(), threading.Event()

    def run(self, session, prompt, mode, log):
        self.started.set()
        assert self.go.wait(10)
        return super().run(session, prompt, mode, log)


def test_variant_id_is_readable_and_unique():
    assert variant_id("PC 303 with 25 mm gun", set()) == "pc_303_with_25_mm_gun"
    assert variant_id("Container ship", {"container_ship"}) == "container_ship_2"
    assert variant_id("Container ship", {"container_ship", "container_ship_2"}) == "container_ship_3"
    assert variant_id("  ¿?  ", set()) == "variant"
    assert len(variant_id("x" * 200, set())) <= 40


def test_describe_tool_says_what_the_ai_is_doing_in_plain_words():
    assert describe_tool("set_params", {"patch": {"hull_color": [0, 0, 0], "beam_m": 30}}) == "Changing hull color, beam m"
    assert describe_tool("set_part_override", {"part": "Roof__Searchlight", "override": {"hidden": True}}) == "Hiding Roof › Searchlight"
    assert describe_tool("set_part_override", {"part": "Roof__Searchlight", "override": None}) == "Resetting Roof › Searchlight"
    assert describe_tool("set_part_override", {"part": "Roof__Searchlight", "override": {"scale": [2, 2, 2]}}) == "Adjusting Roof › Searchlight"
    assert describe_tool("add_custom_op", {"description": "a 25 mm gun on the foredeck", "code": "..."}) == "Adding a 25 mm gun on the foredeck"
    assert describe_tool("add_custom_op", {"description": "Small searchlight", "code": "..."}) == "Adding small searchlight"
    assert describe_tool("add_custom_op", {"description": "PC 303 horn", "code": "..."}) == "Adding PC 303 horn"
    assert describe_tool("set_clothes", {"patch": {"hat": "cap01"}}) == "Changing clothes: hat"
    assert describe_tool("add_custom_item", {"description": "White singlet", "code": "..."}) == "Making white singlet"
    assert describe_tool("remove_custom_item", {"id": "item_1"}) == "Removing a made item"
    assert describe_tool("set_equipment", {"equipment": ["life_vest", "binoculars"]}) == "Setting equipment: life vest, binoculars"
    assert describe_tool("WebSearch", {"query": "PC 303 gun"}) == "Searching the web: PC 303 gun"
    assert describe_tool("WebFetch", {"url": "https://www.example.gov.sg/a/b"}) == "Reading www.example.gov.sg"
    assert describe_tool("get_project", {}) == "Looking at the current model"
    assert describe_tool("rebuild", {}) is None  # the build logs its own line
    assert describe_tool("mystery", None) == "Using mystery"


def test_describe_tool_covers_the_crew_module_and_design_plan_tools():
    # Equipment and accessories entries may be objects (crew modules): the feed names them, never a dict.
    assert describe_tool("set_equipment", {"equipment": ["radio", {"name": "life_vest", "color": "orange"}]}) \
        == "Setting equipment: radio, life vest"
    assert describe_tool("set_place", {"item": "radio", "place": "right_hand"}) == "Moving radio to the right hand"
    assert describe_tool("set_accessories", {"accessories": [{"name": "sunglasses"}, "gloves"]}) \
        == "Setting accessories: sunglasses, gloves"
    assert describe_tool("save_design_plan", {"summary": ["x"], "spec": {}}) == "Drafting the design plan"


def test_variant_without_brief_is_an_exact_copy_awaiting_review(make_client):
    agent = ScriptedAgent()
    c = make_client(agent=agent)
    src = create_ready(c)
    wait_job(c, c.post(f"/api/projects/{src['id']}/spec", json={"patch": {"params": {"beam_m": 30}}}).json()["job_id"])
    src = c.get(f"/api/projects/{src['id']}").json()
    v, job = make_variant(c, src, name="Container ship, refit")
    assert job["status"] == "done", job
    assert v["kind"] == "ship" and v["base"] == "container" and v["name"] == "Container ship, refit"
    assert v["version"] == 1 and [x["n"] for x in v["versions"]] == [1]
    assert v["derived_from"] == {"project": src["id"], "version": 2, "name": src["name"]}
    assert v["draft"] == {"stage": "ready", "job_id": job["id"], "research": False, "scene": None, "error": None}
    assert v["brief"] is None and v["messages"] == [] and v["pins"] == []
    assert v["spec"]["id"] == "container_ship_refit" and v["spec"]["name"] == "Container ship, refit"
    strip = lambda s: {k: x for k, x in s.items() if k not in ("id", "name")}  # noqa: E731
    assert strip(v["spec"]) == strip(src["spec"])  # beam_m 30 came along
    assert agent.prompts == []  # no brief, no AI
    assert c.get(f"/api/projects/{src['id']}").json()["version"] == 2  # source untouched
    summary = next(p for p in c.get("/api/projects").json() if p["id"] == v["id"])
    assert summary["derived_from"]["project"] == src["id"] and summary["draft"]["stage"] == "ready"
    # the log tells a person what happened, in lines marked "• "
    steps = [line for line in job["log"] if line.startswith("• ")]
    assert steps[0].startswith("• Copying Container ship v2") and steps[-1] == "• Ready for your review"
    assert any(line.startswith("• Version 1 built") for line in steps)
    # approving makes it an ordinary project
    out = approve(c, v)
    assert out["project"]["draft"] is None and out["scene"] is None


def test_variant_from_an_older_version(make_client):
    c = make_client()
    src = create_ready(c)
    wait_job(c, c.post(f"/api/projects/{src['id']}/spec", json={"patch": {"params": {"beam_m": 30}}}).json()["job_id"])
    v, _ = make_variant(c, src, version=1, name="Original beam")
    assert v["spec"]["params"]["beam_m"] == 32.2 and v["derived_from"]["version"] == 1


def test_variant_name_defaults_and_ids_do_not_collide(make_client):
    c = make_client()
    src = create_ready(c)
    a, _ = make_variant(c, src)
    b, _ = make_variant(c, src)
    assert a["name"] == "Container ship (variant)"
    assert a["spec"]["id"] == "container_ship_variant" and b["spec"]["id"] == "container_ship_variant_2"
    same, _ = make_variant(c, src, name="Container ship")
    assert same["spec"]["id"] == "container_ship_2"  # the source's id is taken


def test_brief_drives_the_ai_into_v2(make_client):
    agent = ScriptedAgent([("get_project", {}), HIDE, ("rebuild", {})], reply="Removed the searchlight.")
    c = make_client(agent=agent)
    src = create_ready(c)
    v, job = make_variant(c, src, name="Dark running", brief="Same ship without the searchlight")
    assert job["status"] == "done", job
    assert job["result"]["version"] == 2 and job["result"]["reply"] == "Removed the searchlight."
    assert v["version"] == 2 and v["spec"]["overrides"]["Roof__Searchlight"] == {"hidden": True}
    assert v["brief"] == "Same ship without the searchlight" and v["draft"]["stage"] == "ready"
    assert [(m["who"], m["text"]) for m in v["messages"]] == [
        ("You", "Same ship without the searchlight"), ("AI", "Removed the searchlight.")]
    assert agent.modes == ["build"]
    prompt = agent.prompts[0]
    assert "Same ship without the searchlight" in prompt and src["name"] in prompt and "v1" in prompt
    assert "• The AI is making the changes" in job["log"] and "• Version 2 built" in job["log"]
    assert c.get(f"/api/projects/{src['id']}").json()["spec"]["overrides"] == {}  # source untouched
    # undo goes back to the exact copy
    assert c.post(f"/api/projects/{v['id']}/undo").json()["project"]["version"] == 1


def test_stages_are_visible_while_it_builds_and_approval_waits(make_client):
    agent = GatedAgent([HIDE, ("rebuild", {})])
    c = make_client(agent=agent)
    src = create_ready(c)
    r = c.post(f"/api/projects/{src['id']}/variants", json={"name": "Slow", "brief": "hide the searchlight"}).json()
    vid = r["project"]["id"]
    assert agent.started.wait(10)
    mid = c.get(f"/api/projects/{vid}").json()
    assert mid["draft"]["stage"] == "changing" and mid["version"] == 1  # the copy is already on screen
    assert c.post(f"/api/projects/{vid}/approve").status_code == 409
    assert c.post(f"/api/projects/{vid}/discard").status_code == 409
    agent.go.set()
    assert wait_job(c, r["job_id"])["status"] == "done"
    assert approve(c, {"id": vid})["project"]["version"] == 2


def test_research_runs_before_the_build_and_sees_the_original_research(make_client):
    agent = ScriptedAgent(reply="ok")
    c = make_client(agent=agent)
    src = create_ready(c)
    research = {"schema": "me3d.research/1", "subject": "PC 303", "summary": {"verdict": "Good."},
                "sources": [], "claims": [], "open_questions": [], "articulation": []}
    with c.app.state.store.edit(src["id"]) as p:
        p["research"] = research
    v, job = make_variant(c, src, name="Gun refit", brief="25 mm gun on the foredeck", research=True)
    assert job["status"] == "done", job
    assert agent.modes == ["research", "build"] and v["draft"]["research"] is True
    assert "25 mm gun on the foredeck" in agent.prompts[0] and '"subject": "PC 303"' in agent.prompts[0]
    assert v["research"] == research  # copied; the scripted agent saved nothing new
    assert [m["who"] for m in v["messages"]] == ["You", "AI", "AI"]
    assert "• Researching the changes from public sources" in job["log"]


def test_ai_failure_keeps_the_copy_reviewable(make_client):
    class Boom(ScriptedAgent):
        def run(self, session, prompt, mode, log):
            raise RuntimeError("model unreachable")
    c = make_client(agent=Boom())
    src = create_ready(c)
    v, job = make_variant(c, src, name="Try", brief="add a horn")
    assert job["status"] == "error" and "model unreachable" in job["error"]
    assert v["version"] == 1 and v["build_report"]["ok"]
    assert v["draft"]["stage"] == "ready" and "model unreachable" in v["draft"]["error"]
    assert v["messages"][-1]["who"] == "AI" and "model unreachable" in v["messages"][-1]["text"]
    assert approve(c, v)["project"]["version"] == 1  # the copy can still be kept


def test_failed_copy_cannot_be_approved(make_client):
    c = make_client()
    src = create_ready(c)
    # A copy fails to build if Blender (here, the fake builder) rejects the copied spec.
    spec_path = c.app.state.store.version_dir(src["id"], 1) / "spec.json"
    spec = json.loads(spec_path.read_text())
    spec["params"]["fail"] = "hull broke"
    spec_path.write_text(json.dumps(spec))
    v, job = make_variant(c, src, name="Broken")
    assert job["status"] == "error" and v["draft"]["stage"] == "failed" and "hull broke" in v["draft"]["error"]
    assert c.post(f"/api/projects/{v['id']}/approve").status_code == 409
    assert c.post(f"/api/projects/{v['id']}/discard").status_code == 200


def test_discard_hides_the_draft_and_leaves_the_original(make_client):
    c = make_client()
    src = create_ready(c)
    v, _ = make_variant(c, src, name="Nope")
    out = c.post(f"/api/projects/{v['id']}/discard").json()["project"]
    assert out["discarded"] is True
    assert [p["id"] for p in c.get("/api/projects").json()] == [src["id"]]
    assert c.post(f"/api/projects/{v['id']}/approve").status_code == 409
    assert c.post(f"/api/projects/{src['id']}/discard").status_code == 409  # only drafts can be discarded
    assert c.get(f"/api/projects/{src['id']}").json()["version"] == 1


def test_drafts_stay_out_of_scenes_and_variants_until_approved(make_client):
    c = make_client()
    ship, crew = create_ready(c), create_ready(c, "crew", "male_default")
    cox, _ = make_variant(c, crew, name="Coxswain")
    body = {"schema": "me3d.scene_spec/1", "name": "x", "ship": {"project": ship["id"], "version": 1},
            "crew": [{"project": cox["id"], "version": 1, "station": "station__helm", "animation": "idle"}]}
    r = c.post("/api/scenes", json=body)
    assert r.status_code == 400 and "approve" in r.text.lower()
    assert c.post(f"/api/projects/{cox['id']}/variants", json={}).status_code == 409
    approve(c, cox)
    assert c.post("/api/scenes", json=body).status_code == 200


def test_crew_variant(make_client):
    agent = ScriptedAgent([("set_clothes", {"patch": {"hat": "cap01"}}), ("rebuild", {})], reply="Gave him a cap.")
    c = make_client(agent=agent)
    src = create_ready(c, "crew", "male_default")
    v, job = make_variant(c, src, name="Deckhand in a cap", brief="wearing a cap")
    assert job["status"] == "done", job
    assert v["kind"] == "crew" and v["spec"]["clothes"]["hat"] == "cap01"
    assert v["spec"]["id"] == "deckhand_in_a_cap" and v["spec"]["schema"] == "me3d.crew_spec/1"


def test_bad_requests_are_refused(make_client):
    c = make_client()
    ship, crew = create_ready(c), create_ready(c, "crew", "male_default")
    wait_job(c, c.post(f"/api/projects/{ship['id']}/spec", json={"patch": {"params": {"fail": "boom"}}}).json()["job_id"])
    post = lambda p, body: c.post(f"/api/projects/{p['id']}/variants", json=body)  # noqa: E731
    assert post({"id": "p_nothere"}, {}).status_code == 404
    assert post(ship, {"version": 9}).status_code == 404
    assert post(ship, {"version": 2}).status_code == 400  # v2 failed to build
    assert post(ship, {"version": "1"}).status_code == 400
    assert post(ship, {"brief": 5}).status_code == 400
    assert post(ship, {"research": True}).status_code == 400  # research needs a brief
    assert post(crew, {"brief": "x", "research": True}).status_code == 400  # and a ship
    assert post(crew, {"scene": "scene_x"}).status_code == 400  # scenes belong to ships
    assert post(ship, {"scene": "scene_nothere"}).status_code == 404
    other = create_ready(c)
    s = scene_for(c, other, crew, ["station__helm"])
    assert post(ship, {"scene": s["id"]}).status_code == 400  # another ship's scene
    assert len(c.get("/api/projects").json()) == 3  # nothing was created by a refused request
    assert c.post(f"/api/projects/{ship['id']}/approve").status_code == 409  # not a draft


def test_scene_comes_along_on_approval_and_lost_stations_are_reported(make_client):
    agent = ScriptedAgent([("set_params", {"patch": {"drop_stations": ["station__bow"]}}), ("rebuild", {})],
                          reply="Replaced the bow lookout position with a gun mount.")
    c = make_client(agent=agent)
    ship, crew = create_ready(c), create_ready(c, "crew", "male_default")
    s = scene_for(c, ship, crew, ["station__helm", "station__bow"])
    v, job = make_variant(c, ship, name="Gunboat", brief="gun where the bow lookout stands", scene=s["id"])
    assert job["status"] == "done", job
    assert v["draft"]["scene"] == s["id"]
    assert len(c.get("/api/scenes").json()) == 1  # nothing copied before approval
    new_scene = approve(c, v)["scene"]
    assert new_scene["missing_stations"] == ["station__bow"]
    got = c.get(f"/api/scenes/{new_scene['id']}").json()
    assert got["ship"] == {"project": v["id"], "version": 2} and got["name"] == "Gunboat with crew"
    assert [x["station"] for x in got["crew"]] == ["station__helm", "station__bow"]  # kept, flagged
    assert [x["name"] for x in got["stations"]] == ["station__helm"]
    assert c.get(f"/api/scenes/{s['id']}").json()["ship"]["project"] == ship["id"]  # original untouched


def test_two_crew_variants_export_under_their_own_names(make_client):
    c = make_client()
    ship, crew = create_ready(c), create_ready(c, "crew", "male_default")
    cox, _ = make_variant(c, crew, name="Coxswain")
    approve(c, cox)
    s = c.post("/api/scenes", json={
        "schema": "me3d.scene_spec/1", "name": "Two", "ship": {"project": ship["id"], "version": 1},
        "crew": [{"project": crew["id"], "version": 1, "station": "station__helm", "animation": "idle"},
                 {"project": cox["id"], "version": 1, "station": "station__bow", "animation": "idle"}]}).json()["scene"]
    job = wait_job(c, c.post(f"/api/scenes/{s['id']}/export").json()["job_id"])
    assert job["status"] == "done", job
    m = json.loads((Path(job["result"]["package_dir"]) / "manifest.json").read_text())
    assert [e["glb"] for e in m["crew"]] == ["crew/crew_male_default.glb", "crew/coxswain.glb"]


def test_router_knows_variants():
    for dest in ("ship_variant", "crew_variant"):
        assert parse_route(json.dumps({"destination": dest, "reason": "r", "project_id": "p_1"}), {"p_1"}) == {
            "destination": dest, "reason": "r", "project_id": "p_1"}
