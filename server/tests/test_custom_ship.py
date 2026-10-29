"""Ships of any type (contract §5, `type: "custom"`): research first, a design plan the user
approves, and nothing built before that approval."""
import base64
import json

import pytest

from app.agent import ScriptedAgent
from app.design_plan import name_from_brief, plan_spec, validate_custom_params
from conftest import wait_job

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")

CUSTOM_CATALOG = {
    "schema": "me3d.ship_spec/1", "type": "custom", "id": "custom_ship", "name": "Custom ship",
    "description": "Any monohull, shaped by its params",
    "params": {"loa_m": 20, "beam_m": 5, "depth_m": 2.5, "draught_m": 1.2, "bow_form": "raked",
               "stern_form": "transom", "hull_color": [1, 1, 1], "tiers": []},
    "param_docs": {"loa_m": {"doc": "Length overall", "min": 4, "max": 400},
                   "beam_m": {"doc": "Beam", "min": 1, "max": 70},
                   "depth_m": {"doc": "Depth", "min": 0.5, "max": 40},
                   "draught_m": {"doc": "Draught", "min": 0.2, "max": 25},
                   "bow_form": {"doc": "Bow", "options": ["plumb", "raked", "flared", "bulbous", "axe"]},
                   "stern_form": {"doc": "Stern", "options": ["transom", "cruiser", "swim_platform", "open"]},
                   "hull_color": {"doc": "RGB 0-1"},
                   "tiers": {"doc": "Superstructure tiers from the deck up"},
                   "fail": {"doc": "test hook"}},
    "overrides": {}, "joint_limits": {}, "custom_ops": []}

YACHT = {"params": {"loa_m": 24, "beam_m": 6, "depth_m": 3, "draught_m": 1.6, "bow_form": "raked",
                    "stern_form": "swim_platform",
                    "tiers": [{"x_aft_m": -6, "x_fwd_m": 6, "width_m": 5.2, "height_m": 2.4},
                              {"x_aft_m": -3, "x_fwd_m": 4, "width_m": 4.4, "height_m": 2.2}]}}
SUMMARY = ["About 24 m long and 6 m wide.", "Raked bow, swim-platform stern.",
           "Main deck cabin 12 m long; fly-bridge 7 m long above it.", "White hull, dark blue window band.",
           "Details to add: fly-bridge seating, radar arch, rails.", "Assumed beam 6 m: typical for 24 m yachts."]
RESEARCH = {"subject": "Generic 24 m motor yacht from the user's photo",
            "summary": {"verdict": "A generic fly-bridge motor yacht; proportions from the photo.",
                        "existing_work": "None."},
            "sources": [{"id": "P01", "title": "User photo", "url": "", "status": "Photographed",
                         "what_it_proves": "Two decks, raked bow"}],
            "claims": [{"claim": "Two superstructure decks", "sources": ["P01"], "status": "Photo reading",
                        "consequence_for_model": "Two tiers"}],
            "open_questions": [{"question": "How long is it?", "meanwhile": "24 m"}],
            "articulation": [{"part": "Arch__Radar", "motion": "spin", "priority": "P1"}]}


@pytest.fixture
def with_custom(catalog_dir):
    (catalog_dir / "ships" / "custom.json").write_text(json.dumps(CUSTOM_CATALOG))
    return catalog_dir


def create_custom(c, **body):
    r = c.post("/api/projects", json={"kind": "ship", "base": "custom", **body})
    assert r.status_code == 200, r.text
    out = r.json()
    job = wait_job(c, out["job_id"])
    assert job["status"] == "done", job
    return out, job


def research_turn(c, pid, message="research it", attachments=None):
    body = {"message": message, "mode": "research"}
    if attachments:
        body["attachments"] = attachments
    job = wait_job(c, c.post(f"/api/projects/{pid}/chat", json=body).json()["job_id"])
    assert job["status"] == "done", job
    return job


def drafted(make_client):
    """A custom ship with research saved and a draft plan, ready to approve."""
    agent = ScriptedAgent([("save_research", {"research": RESEARCH}),
                           ("save_design_plan", {"summary": SUMMARY, "spec": YACHT})])
    c = make_client(agent=agent)
    out, _ = create_custom(c, brief="create a yacht ship model to look like the image attached")
    pid = out["project"]["id"]
    research_turn(c, pid)
    return c, pid, agent


# ---- create from a prompt ------------------------------------------------------------------

def test_create_from_brief_names_it_stores_brief_and_builds_placeholder(make_client, with_custom):
    c = make_client()
    out, job = create_custom(c, brief="create a yacht ship model to look like the image attached")
    p = c.get(f"/api/projects/{out['project']['id']}").json()
    assert p["name"] == "Yacht" and p["base"] == "custom"
    assert p["brief"] == "create a yacht ship model to look like the image attached"
    assert p["version"] == 1 and job["result"]["version"] == 1
    assert p["spec"]["type"] == "custom" and p["spec"]["id"] == "yacht" and p["spec"]["params"]["loa_m"] == 20
    assert p["design_plan"] is None and p["research_rev"] == 0
    assert c.builder.calls[-1][0] == "ship"


def test_create_with_explicit_name_wins(make_client, with_custom):
    c = make_client()
    out, _ = create_custom(c, name="Lady Ann", brief="a yacht")
    assert out["project"]["name"] == "Lady Ann"


def test_create_without_custom_catalog_builds_nothing_and_says_so(make_client):
    c = make_client()
    out, job = create_custom(c, brief="a pilot boat like this")
    assert "custom.json is missing" in out["project"]["note"]
    assert job["result"]["version"] == 0 and c.builder.calls == []
    p = c.get(f"/api/projects/{out['project']['id']}").json()
    assert p["version"] == 0 and p["name"] == "Pilot boat" and p["spec"]["type"] == "custom"


@pytest.mark.parametrize("brief,name", [
    ("create a yacht ship model to look like the image attached", "Yacht"),
    ("build me a motor yacht with a flybridge", "Motor yacht"),
    ("a harbour tug for the strait", "Tug"),
    ("make a RIB, orange tubes", "RIB"),
    ("something that floats", "Custom ship"),
    ("a ship", "Custom ship"),
    (None, "Custom ship"),
])
def test_name_from_brief(brief, name):
    assert name_from_brief(brief) == name


def test_custom_is_listed_with_the_ship_types(make_client, with_custom):
    types = [s["type"] for s in make_client().get("/api/catalog").json()["ships"]]
    assert "custom" in types


# ---- research turns ------------------------------------------------------------------------

def test_research_turn_saves_research_and_never_builds(make_client, with_custom):
    agent = ScriptedAgent([("save_research", {"research": RESEARCH}), ("rebuild", {}),
                           ("set_params", {"patch": {"loa_m": 30}})])
    c = make_client(agent=agent)
    out, _ = create_custom(c, brief="create a yacht ship model to look like the image attached")
    pid = out["project"]["id"]
    builds_before = len(c.builder.calls)
    job = research_turn(c, pid, "the hull is dark blue")
    p = c.get(f"/api/projects/{pid}").json()
    assert job["result"]["research_saved"] and p["research"]["subject"].startswith("Generic 24 m")
    assert p["research_rev"] == 1
    assert p["version"] == 1 and len(c.builder.calls) == builds_before  # nothing built
    assert [err for _, _, err in agent.results] == [False, True, True]  # build tools are refused here
    prompt = agent.prompts[-1]
    assert "Brief (the user's original request): create a yacht" in prompt
    assert "Saved research so far: none yet." in prompt and "the hull is dark blue" in prompt
    # a second turn amends: the AI is shown what was saved
    research_turn(c, pid, "it is 26 m")
    assert '"subject": "Generic 24 m motor yacht' in agent.prompts[-1]
    assert c.get(f"/api/projects/{pid}").json()["research_rev"] == 2


def test_research_turns_resend_earlier_photos(make_client, with_custom):
    agent = ScriptedAgent([])
    c = make_client(agent=agent)
    out, _ = create_custom(c, brief="a yacht like this")
    pid = out["project"]["id"]
    up = c.post(f"/api/projects/{pid}/uploads", json={"name": "yacht.png", "type": "image/png",
                                                       "data": base64.b64encode(PNG).decode()}).json()["attachment"]
    research_turn(c, pid, "here it is", [up["id"]])
    research_turn(c, pid, "make the bow sharper")
    blocks = agent.prompts[-1]
    assert isinstance(blocks, list) and [b["type"] for b in blocks].count("image") == 1
    assert blocks[0] == {"type": "text", "text": "Attached file: yacht.png"}


# ---- the design plan -----------------------------------------------------------------------

def test_save_design_plan_stores_a_draft_against_the_research_revision(make_client, with_custom):
    c, pid, agent = drafted(make_client)
    assert [err for _, _, err in agent.results] == [False, False], agent.results
    p = c.get(f"/api/projects/{pid}").json()
    plan = p["design_plan"]
    assert plan["status"] == "draft" and plan["summary"] == SUMMARY and plan["research_rev"] == 1 == p["research_rev"]
    assert plan["spec"]["type"] == "custom" and plan["spec"]["id"] == "yacht"
    assert plan["spec"]["params"]["loa_m"] == 24 and plan["spec"]["params"]["hull_color"] == [1, 1, 1]
    assert plan["at"] and p["version"] == 1


def _params(**over):
    p = json.loads(json.dumps(YACHT["params"]))
    p.update(over)
    return p


def _tiers(*tiers):
    return _params(tiers=list(tiers))


@pytest.mark.parametrize("params,says", [
    (_tiers({"x_aft_m": -6, "x_fwd_m": 13, "width_m": 5, "height_m": 2}), "the hull only spans -12..12"),
    (_tiers({"x_aft_m": -6, "x_fwd_m": 6, "width_m": 6.5, "height_m": 2}), "at most the beam 6"),
    (_tiers({"x_aft_m": -6, "x_fwd_m": 6, "width_m": 5, "height_m": 2, "setback": 0.3}), "unknown fields ['setback']"),
    (_tiers({"x_aft_m": -6, "x_fwd_m": 6, "width_m": 5, "height_m": 2, "setback_m": 2.45}), "its roof"),
    (_tiers({"x_aft_m": -6, "x_fwd_m": 6, "width_m": 5, "height_m": 2, "rake_deg": 80}), "rake_deg must be"),
    (_params(mast_x_m=15), "params.mast_x_m 15 is off the hull"),
    (_tiers({"x_aft_m": 4, "x_fwd_m": -2, "width_m": 5, "height_m": 2}), "at least 0.4 m aft of"),
    (_tiers({"x_aft_m": -6, "x_fwd_m": 6, "width_m": 5, "height_m": 0}), "height_m 0.0 must be 0.2..12"),
    (_tiers({"x_aft_m": -6, "x_fwd_m": 0, "width_m": 5, "height_m": 2},
            {"x_aft_m": 1, "x_fwd_m": 5, "width_m": 4, "height_m": 2}), "does not sit on the tier below"),
    (_tiers({"x_aft_m": -6, "x_fwd_m": 6, "width_m": 4, "height_m": 2, "color": [2, 0, 0]}), "color must be"),
    (_params(loa_m=500), "loa_m=500 is outside 4..400"),
    (_params(bow_form="spoon"), "bow_form must be one of"),
    (_params(draught_m=3.5), "draught_m 3.5 must be less than depth_m"),
    (_params(gun_count=2), "unknown params ['gun_count']"),
    (_params(hull_color=[255, 255, 255]), "hull_color must be [r,g,b]"),
])
def test_plan_validation_refuses_what_does_not_fit(params, says):
    current = {"schema": "me3d.ship_spec/1", "type": "custom", "id": "yacht", "name": "Yacht", "params": {}}
    default = {k: v for k, v in CUSTOM_CATALOG.items() if k not in ("description", "param_docs")}
    _, errs = plan_spec({"params": params}, current, default, CUSTOM_CATALOG["param_docs"])
    assert any(says in e for e in errs), errs


def test_plan_validation_accepts_a_good_yacht_and_refuses_custom_ops():
    current = {"type": "custom", "id": "yacht", "name": "Yacht", "params": {}}
    default = {k: v for k, v in CUSTOM_CATALOG.items() if k not in ("description", "param_docs")}
    spec, errs = plan_spec(YACHT, current, default, CUSTOM_CATALOG["param_docs"])
    assert errs == [] and spec["name"] == "Yacht" and spec["custom_ops"] == []
    # a bridge deck wider than the saloon below it (over the side decks) is a real yacht shape
    wide_upper = {"params": _tiers({"x_aft_m": -6, "x_fwd_m": 6, "width_m": 4.4, "height_m": 2.4},
                                   {"x_aft_m": -7, "x_fwd_m": 4, "width_m": 5.8, "height_m": 2.2,
                                    "overhang_aft_m": 1, "setback_m": 0.1})}
    assert plan_spec(wide_upper, current, default, CUSTOM_CATALOG["param_docs"])[1] == []
    _, errs = plan_spec(dict(YACHT, custom_ops=[{"id": "op_1", "description": "x", "code": "pass"}]),
                        current, default, CUSTOM_CATALOG["param_docs"])
    assert any("leave custom_ops empty" in e for e in errs)
    # without a catalog the contract's forms and the hull fit still apply
    assert any("bow_form must be one of" in e for e in validate_custom_params(_params(bow_form="spoon"), {}, None))


def test_refused_plan_saves_nothing_and_tells_the_ai(make_client, with_custom):
    bad = {"params": _tiers({"x_aft_m": -6, "x_fwd_m": 20, "width_m": 5, "height_m": 2})}
    agent = ScriptedAgent([("save_design_plan", {"summary": SUMMARY, "spec": bad}),
                           ("save_design_plan", {"summary": [], "spec": YACHT})])
    c = make_client(agent=agent)
    out, _ = create_custom(c, brief="a yacht")
    research_turn(c, out["project"]["id"])
    assert [err for _, _, err in agent.results] == [True, True]
    assert "Plan not saved" in agent.results[0][1] and "summary must be" in agent.results[1][1]
    assert c.get(f"/api/projects/{out['project']['id']}").json()["design_plan"] is None


def test_design_plan_tool_is_only_for_custom_ships(make_client, with_custom):
    agent = ScriptedAgent([("save_design_plan", {"summary": SUMMARY, "spec": YACHT})])
    c = make_client(agent=agent)
    body = c.post("/api/projects", json={"kind": "ship", "base": "container"}).json()
    wait_job(c, body["job_id"])
    research_turn(c, body["project"]["id"])
    assert agent.results[0][2] is True and "Unknown tool" in agent.results[0][1]


# ---- approve and build ---------------------------------------------------------------------

def test_approve_builds_the_plan_then_adds_details(make_client, with_custom):
    c, pid, agent = drafted(make_client)
    agent.steps = [("add_custom_op", {"description": "radar arch",
                                      "code": "o = me3d.box('Arch__Radar', size=(0.2, 3, 1))"}),
                   ("rebuild", {})]
    agent.reply = "Added the radar arch."
    r = c.post(f"/api/projects/{pid}/design-plan/approve")
    assert r.status_code == 200, r.text
    job = wait_job(c, r.json()["job_id"])
    assert job["status"] == "done", job
    p = c.get(f"/api/projects/{pid}").json()
    assert p["design_plan"]["status"] == "approved"
    assert p["version"] == 3 and job["result"]["built_versions"] == [2, 3]
    plan_build = c.builder.calls[-2][1]
    assert plan_build["params"]["loa_m"] == 24 and len(plan_build["params"]["tiers"]) == 2
    assert p["spec"]["custom_ops"][0]["description"] == "radar arch"
    assert agent.modes[-1] == "build"
    assert "- Details to add: fly-bridge seating, radar arch, rails." in agent.prompts[-1]
    assert p["messages"][-1] == {**p["messages"][-1], "who": "AI", "text": "Added the radar arch."}


def test_approve_refused_without_a_plan_or_when_research_moved_on(make_client, with_custom):
    c = make_client(agent=ScriptedAgent([("save_research", {"research": RESEARCH})]))
    out, _ = create_custom(c, brief="a yacht")
    pid = out["project"]["id"]
    r = c.post(f"/api/projects/{pid}/design-plan/approve")
    assert r.status_code == 409 and "no design plan yet" in r.json()["detail"]

    c, pid, agent = drafted(make_client)
    agent.steps = [("save_research", {"research": RESEARCH})]  # research changes after the draft
    research_turn(c, pid, "actually it has three decks")
    r = c.post(f"/api/projects/{pid}/design-plan/approve")
    assert r.status_code == 409 and "changed after this plan was drafted" in r.json()["detail"]
    assert c.get(f"/api/projects/{pid}").json()["design_plan"]["status"] == "draft"


def test_approve_is_refused_twice_and_for_other_ships(make_client, with_custom):
    c, pid, agent = drafted(make_client)
    agent.steps = []
    wait_job(c, c.post(f"/api/projects/{pid}/design-plan/approve").json()["job_id"])
    r = c.post(f"/api/projects/{pid}/design-plan/approve")
    assert r.status_code == 409 and "already approved" in r.json()["detail"]
    body = c.post("/api/projects", json={"kind": "ship", "base": "container"}).json()
    assert c.post(f"/api/projects/{body['project']['id']}/design-plan/approve").status_code == 400


def test_failed_plan_build_returns_the_plan_to_draft(make_client, with_custom):
    c, pid, agent = drafted(make_client)
    # make the plan's build fail through the fake builder's hook
    proj = c.app.state.store.load(pid)
    proj["design_plan"]["spec"]["params"]["fail"] = "tier collides with mast"
    c.app.state.store.save(proj)
    job = wait_job(c, c.post(f"/api/projects/{pid}/design-plan/approve").json()["job_id"])
    assert job["status"] == "error" and "tier collides with mast" in job["error"]
    p = c.get(f"/api/projects/{pid}").json()
    assert p["design_plan"]["status"] == "draft" and "tier collides" in p["design_plan"]["build_error"]
    assert p["version"] == 1 and agent.modes == ["research"]  # no detail turn on a failed base build
    # nothing was built from a plan, so the first-build gate still holds
    assert p["plan_built"] is False
    r = c.post(f"/api/projects/{pid}/chat", json={"message": "make it red", "mode": "build"})
    assert r.status_code == 409 and "approve the plan first" in r.json()["detail"]


# ---- building is refused until a plan is approved ---------------------------------------------

def test_build_chat_spec_and_pins_refused_before_approval_and_allowed_after(make_client, with_custom):
    c, pid, agent = drafted(make_client)
    r = c.post(f"/api/projects/{pid}/chat", json={"message": "make it red", "mode": "build"})
    assert r.status_code == 409 and "approve the plan first" in r.json()["detail"]
    r = c.post(f"/api/projects/{pid}/spec", json={"patch": {"params": {"loa_m": 30}}})
    assert r.status_code == 409
    r = c.post(f"/api/projects/{pid}/pins", json={"part": "Hull__Main", "text": "lower"})
    assert r.status_code == 409
    assert len([m for m in c.get(f"/api/projects/{pid}").json()["messages"] if m["text"] == "make it red"]) == 0

    assert c.get(f"/api/projects/{pid}").json()["plan_built"] is False
    agent.steps = []
    wait_job(c, c.post(f"/api/projects/{pid}/design-plan/approve").json()["job_id"])
    assert c.get(f"/api/projects/{pid}").json()["plan_built"] is True
    agent.steps = [("set_params", {"patch": {"hull_color": [0.8, 0.1, 0.1]}}), ("rebuild", {})]
    r = c.post(f"/api/projects/{pid}/chat", json={"message": "make it red", "mode": "build"})
    assert r.status_code == 200
    job = wait_job(c, r.json()["job_id"])
    assert job["status"] == "done", job
    p = c.get(f"/api/projects/{pid}").json()
    assert p["spec"]["params"]["hull_color"] == [0.8, 0.1, 0.1]
    assert "The approved design plan for this ship:" in agent.prompts[-1]
    r = c.post(f"/api/projects/{pid}/spec", json={"patch": {"params": {"loa_m": 25}}})
    assert r.status_code == 200


def test_stale_plan_keeps_building_open_and_offers_a_rebuild(make_client, with_custom):
    """Lester's rule: only the FIRST build needs his approval. After that a stale plan does not
    block building; it only offers a rebuild from a redrafted plan."""
    c, pid, agent = drafted(make_client)
    agent.steps = []
    wait_job(c, c.post(f"/api/projects/{pid}/design-plan/approve").json()["job_id"])
    assert c.get(f"/api/projects/{pid}").json()["design_plan"]["status"] == "approved"
    agent.steps = [("save_research", {"research": RESEARCH})]
    research_turn(c, pid, "the arch is black")
    p = c.get(f"/api/projects/{pid}").json()
    assert p["design_plan"]["status"] == "stale" and p["research_rev"] == 2
    assert "redraft it" in agent.results[-1][1]
    agent.steps = [("add_custom_op", {"description": "horn",
                                      "code": "o = me3d.box('Mast__Horn', size=(0.2, 0.2, 0.2))"}), ("rebuild", {})]
    r = c.post(f"/api/projects/{pid}/chat", json={"message": "add a horn", "mode": "build"})
    assert r.status_code == 200, r.text
    assert wait_job(c, r.json()["job_id"])["status"] == "done"
    assert c.get(f"/api/projects/{pid}").json()["spec"]["custom_ops"][-1]["description"] == "horn"
    assert c.post(f"/api/projects/{pid}/spec", json={"patch": {"params": {"loa_m": 25}}}).status_code == 200
    r = c.post(f"/api/projects/{pid}/pins", json={"part": "Hull__Main", "text": "lower"})
    assert r.status_code == 200
    wait_job(c, r.json()["job_id"])
    r = c.post(f"/api/projects/{pid}/design-plan/approve")
    assert r.status_code == 409 and "changed after this plan was approved" in r.json()["detail"]
    # redrafting makes it approvable again
    agent.steps = [("save_design_plan", {"summary": SUMMARY, "spec": YACHT})]
    research_turn(c, pid, "redraft the plan")
    p = c.get(f"/api/projects/{pid}").json()
    assert p["design_plan"]["status"] == "draft" and p["design_plan"]["research_rev"] == 2
    before = p["version"]
    agent.steps = []
    r = c.post(f"/api/projects/{pid}/design-plan/approve")
    assert r.status_code == 200
    job = wait_job(c, r.json()["job_id"])
    assert job["status"] == "done", job
    p = c.get(f"/api/projects/{pid}").json()
    assert p["version"] > before and p["design_plan"]["status"] == "approved"
    assert p["spec"]["params"]["loa_m"] == 24 and p["spec"]["custom_ops"] == []  # rebuilt from the plan


def test_other_ships_still_build_from_chat(make_client, with_custom):
    c = make_client(agent=ScriptedAgent([]))
    body = c.post("/api/projects", json={"kind": "ship", "base": "container"}).json()
    wait_job(c, body["job_id"])
    assert c.post(f"/api/projects/{body['project']['id']}/chat",
                  json={"message": "hi", "mode": "build"}).status_code == 200


# ---- variants of a custom ship (Lester, 29 Oct: they start already built) -------------------

def make_variant(c, pid, **body):
    r = c.post(f"/api/projects/{pid}/variants", json=body)
    assert r.status_code == 200, r.text
    job = wait_job(c, r.json()["job_id"])
    assert job["status"] == "done", job
    return c.get(f"/api/projects/{r.json()['project']['id']}").json()


def test_variant_of_a_built_custom_ship_starts_built_with_the_plan_as_history(make_client, with_custom):
    c, pid, agent = drafted(make_client)
    agent.steps = []
    wait_job(c, c.post(f"/api/projects/{pid}/design-plan/approve").json()["job_id"])
    src = c.get(f"/api/projects/{pid}").json()
    assert src["plan_built"] and src["design_plan"]["status"] == "approved"

    v = make_variant(c, pid, name="Lady Ann II", brief="a darker hull")
    assert v["plan_built"] is True
    assert v["design_plan"]["summary"] == src["design_plan"]["summary"]
    assert v["design_plan"]["status"] == "approved" and v["research_rev"] == src["research_rev"]
    # History under the variant's own id, never the source's.
    assert v["design_plan"]["spec"]["id"] == v["spec"]["id"] != src["spec"]["id"]
    assert v["design_plan"]["spec"]["name"] == "Lady Ann II"
    assert v["brief"] == "a darker hull" and v["draft"]["stage"] == "ready"
    # The plan gate does not block it: build chat, Inspect edits and pins all go through.
    assert c.post(f"/api/projects/{v['id']}/chat", json={"message": "white hull", "mode": "build"}).status_code == 200
    assert c.post(f"/api/projects/{v['id']}/spec", json={"patch": {"params": {"loa_m": 25}}}).status_code == 200
    assert c.post(f"/api/projects/{v['id']}/pins", json={"part": "Hull__Main", "point": [0, 0, 0],
                                                         "text": "raise this"}).status_code == 200
    # No new approval is asked for: the plan is already approved.
    assert c.post(f"/api/projects/{v['id']}/design-plan/approve").status_code == 409
    # The source is unchanged.
    assert c.get(f"/api/projects/{pid}").json()["design_plan"] == src["design_plan"]


def test_no_variant_of_a_custom_ship_until_its_plan_is_built(make_client, with_custom):
    # Lester, 29 Oct ("Not until built"): a variant starts already built, so a variant of the
    # placeholder would skip the plan he never approved. (Replaces the merge's earlier test, which
    # allowed it before he decided.)
    c = make_client(agent=ScriptedAgent())
    out, _ = create_custom(c, brief="a yacht")
    pid = out["project"]["id"]
    assert c.get(f"/api/projects/{pid}").json()["version"] == 1  # built, but only the placeholder
    r = c.post(f"/api/projects/{pid}/variants", json={})
    assert r.status_code == 409 and "Approve its design plan" in r.json()["detail"]
    assert not any((p.get("derived_from") or {}).get("project") == pid for p in c.get("/api/projects").json())
    assert c.get(f"/api/projects").json()[0].get("plan_built") is False  # the list says why


def test_custom_ship_jobs_carry_their_project_and_a_plain_label(make_client, with_custom):
    """The job centre lists jobs by project: the create and approve jobs must say which ship."""
    c, pid, agent = drafted(make_client)
    agent.steps = []
    jobs = c.get("/api/jobs").json()["jobs"]
    first = next(j for j in jobs if j["kind"] == "build")
    assert first["project"] == pid and first["label"] == "First build of Yacht"
    job = wait_job(c, c.post(f"/api/projects/{pid}/design-plan/approve").json()["job_id"])
    assert job["project"] == pid and job["label"] == "Building Yacht from the approved plan"
    assert "• Building the approved design plan" in job["log"]



# ---- categories (Lester, 29 Oct: a described ship starts in "Other"; research files it) -------

SHIP_CATEGORIES = {"ship": ["Commercial vessels", "Military vessels", "Passenger and leisure", "Other"],
                   "crew": ["Navy", "Other"]}


@pytest.fixture
def with_categories(with_custom):
    (with_custom / "categories.json").write_text(json.dumps(SHIP_CATEGORIES))
    data = dict(CUSTOM_CATALOG, category="Other")
    (with_custom / "ships" / "custom.json").write_text(json.dumps(data))
    return with_custom


def category_of(c, pid):
    listed = next(p for p in c.get("/api/projects").json() if p["id"] == pid)
    assert listed["category"] == c.get(f"/api/projects/{pid}").json()["category"]
    return listed["category"]


def test_real_catalog_files_custom_ships_under_other():
    from app.config import REPO_ROOT
    cats = json.loads((REPO_ROOT / "catalog" / "categories.json").read_text())
    custom = json.loads((REPO_ROOT / "catalog" / "ships" / "custom.json").read_text())
    assert custom["category"] == "Other" and "Other" in cats["ship"]
    assert "Passenger and leisure" in cats["ship"]


def test_custom_ship_starts_under_other(make_client, with_categories):
    c = make_client()
    out, _ = create_custom(c, brief="a motor yacht")
    assert out["project"]["category"] == "Other"
    assert category_of(c, out["project"]["id"]) == "Other"


def test_set_category_accepts_a_catalog_category_and_refuses_an_invented_one(make_client, with_categories):
    agent = ScriptedAgent([("set_category", {"category": "Superyachts"}),
                           ("set_category", {"category": "passenger  and leisure"})])
    c = make_client(agent=agent)
    out, _ = create_custom(c, brief="a motor yacht")
    pid = out["project"]["id"]
    research_turn(c, pid)
    (_, refused, refused_err), (_, ok, ok_err) = agent.results
    assert refused_err and "not one of the catalog's ship categories" in refused
    assert "Passenger and leisure" in refused  # it tells the AI what it may pick
    assert not ok_err and ok == "Filed under Passenger and leisure."
    assert category_of(c, pid) == "Passenger and leisure"


def test_refused_category_changes_nothing(make_client, with_categories):
    agent = ScriptedAgent([("set_category", {"category": "Luxury toys"})])
    c = make_client(agent=agent)
    out, _ = create_custom(c, brief="a motor yacht")
    research_turn(c, out["project"]["id"])
    assert agent.results[0][2] is True
    assert category_of(c, out["project"]["id"]) == "Other"


def test_set_category_is_research_only(make_client, with_categories):
    agent = ScriptedAgent([("set_category", {"category": "Military vessels"})])
    c = make_client(agent=agent)
    p = c.post("/api/projects", json={"kind": "ship", "base": "container"}).json()
    wait_job(c, p["job_id"])
    pid = p["project"]["id"]
    job = wait_job(c, c.post(f"/api/projects/{pid}/chat", json={"message": "file it"}).json()["job_id"])
    assert job["status"] == "done", job
    assert agent.results[0][1:] == ("Unknown tool set_category.", True)
    assert category_of(c, pid) != "Military vessels"


def test_yacht_research_files_it_under_passenger_and_leisure_and_says_so(make_client, with_categories):
    reply = ("A generic 24 m fly-bridge motor yacht; the plan is ready to approve. "
             "I filed it under Passenger and leisure.")
    agent = ScriptedAgent([("save_research", {"research": RESEARCH}),
                           ("set_category", {"category": "Passenger and leisure"}),
                           ("save_design_plan", {"summary": SUMMARY, "spec": YACHT})], reply=reply)
    c = make_client(agent=agent)
    out, _ = create_custom(c, brief="create a yacht ship model to look like the image attached")
    pid = out["project"]["id"]
    job = research_turn(c, pid)
    assert all(not err for _, _, err in agent.results), agent.results
    assert "Filed under: Other" in agent.prompts[0]  # the AI is told where it starts
    assert category_of(c, pid) == "Passenger and leisure"
    assert job["result"]["reply"] == reply
    assert c.get(f"/api/projects/{pid}").json()["messages"][-1]["text"] == reply
    assert job["result"]["built_versions"] == []  # filing it builds nothing


def test_research_prompt_and_progress_line_for_filing():
    from app.agent import CUSTOM_RESEARCH_PROMPT, describe_tool
    assert "set_category" in CUSTOM_RESEARCH_PROMPT and "Passenger and leisure" in CUSTOM_RESEARCH_PROMPT
    assert describe_tool("set_category", {"category": "Passenger and leisure"}) == \
        "Filing it under Passenger and leisure"


def test_a_research_change_redrafts_the_plan_in_the_same_job(make_client, with_custom, settings):
    # Lester, 29 Oct: "why can't i approve yet?" The research moved on and the plan was left behind,
    # so Approve stayed off. Now the same job redrafts the plan and Approve is available again.
    settings.plan_follow_up = True
    c, pid, agent = drafted(make_client)
    turns = iter([[("save_research", {"research": RESEARCH})],                     # the user's turn
                  [("save_design_plan", {"summary": SUMMARY, "spec": YACHT})]])    # the redraft
    real_run = agent.run

    def run(session, prompt, mode, log):
        agent.steps = next(turns, [])
        return real_run(session, prompt, mode, log)
    agent.run = run
    job = research_turn(c, pid, "the beam is 6.9 m")
    p = c.get(f"/api/projects/{pid}").json()
    assert "• Redrafting the design plan to match the research" in job["log"]
    assert p["design_plan"]["status"] == "draft" and p["design_plan"]["research_rev"] == p["research_rev"]
    assert c.post(f"/api/projects/{pid}/design-plan/approve").status_code == 200


def test_no_redraft_when_the_plan_is_current(make_client, with_custom, settings):
    settings.plan_follow_up = True
    c, pid, agent = drafted(make_client)
    agent.steps = []
    n = len(agent.prompts)
    job = research_turn(c, pid, "what is the hull colour?")
    assert len(agent.prompts) == n + 1 and "Redrafting" not in " ".join(job["log"])
