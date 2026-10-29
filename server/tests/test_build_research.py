"""Changing a model from its research (contract §12, Updating the model from the research).

Lester, 29 Oct, on Harmony of the Seas: "do a photo research ... and update the model accordingly";
the AI answered that the project "is too large for me to open". get_project was 47k characters (77
parts and seven custom ops' code), more than the agent SDK hands back, and Build's AI had no way to
read the photo research at all. Now get_project is an overview with details on request, and Build's
AI can read the research and look at its photographs."""
import json

from app.agent import AgentSession, ScriptedAgent
from app.catalog import Catalog
from conftest import create_ready, wait_job
from test_research_media import png_bytes

BIG_CODE = "o = me3d.box('Lifesaving__Davit_S1', size=(1, 1, 1))\n" + "# placing davits along the deck\n" * 90


def session(c, settings, pid):
    store = c.app.state.store
    return AgentSession(store, None, Catalog(settings.catalog_dir), pid, lambda line: None, settings.repo_root)


def with_photo_research(c, pid):
    """Stored research with a photograph, a close-up, and a source the user crossed out."""
    store = c.app.state.store
    media = store.research_dir(pid)
    (media / "photos").mkdir(parents=True, exist_ok=True)
    (media / "closeups").mkdir(parents=True, exist_ok=True)
    (media / "photos" / "P01.png").write_bytes(png_bytes(64, 48))
    (media / "closeups" / "C01.png").write_bytes(png_bytes(16, 12, seed=3))
    url = f"/api/files/projects/{pid}/research/photos/P01.png"
    research = {
        "subject": "Harmony of the Seas", "summary": {"verdict": "Model the post-refit ship."},
        "sources": [{"id": "S1", "title": "Builder", "status": "Official"},
                    {"id": "S2", "title": "Forum post", "status": "Unverified"}],
        "claims": [{"claim": "Hull colour", "value": "white", "sources": ["P01"], "status": "Photographed",
                    "consequence_for_model": "hull_color white"},
                   {"claim": "Funnels", "value": 3, "sources": ["S2"], "status": "Unverified",
                    "consequence_for_model": "three funnels"}],
        "open_questions": [{"q": "Davit count", "why": "not visible"}],
        "photos": [{"id": "P01", "title": "Starboard bow after the refit", "vessel": "Harmony of the Seas",
                    "views": ["starboard", "bow"], "status": "baseline", "observation": "Two funnel casings, dark blue boot top.",
                    "modelling_use": "Funnels and livery", "width": 64, "height": 48, "file": url}],
        "closeups": [{"id": "C01", "photo": "P01", "box": [0, 0, 16, 12], "title": "Twin funnel casings",
                      "what_it_shows": "Two separate casings side by side.", "claims": [],
                      "file": f"/api/files/projects/{pid}/research/closeups/C01.png"}],
        "coverage": {"note": "No overhead photograph."},
    }
    store.set_research(pid, research)
    with store.edit(pid) as project:  # photographs and close-ups are stored by their own tools
        project["research"].update({k: research[k] for k in ("photos", "closeups", "coverage")})
    store.set_excluded_sources(pid, ["S2"])
    return research


def test_get_project_is_an_overview_small_enough_to_read(make_client, settings):
    c = make_client()
    pid = create_ready(c)["id"]
    s = session(c, settings, pid)
    s.spec["custom_ops"] = [{"id": f"op_{i}", "description": f"davits row {i}", "code": BIG_CODE} for i in range(7)]
    s.parts = [{"path": f"Lifesaving__Davit_S{i}", "label": f"Davit S{i}", "parent": "Hull", "joint": None,
                "station": None, "origin_m": [i * 1.23456, 2.5, 30.0], "bounds_min": [i, 1.11111, 29.0],
                "bounds_max": [i + 1, 3.33333, 31.0]} for i in range(300)]
    assert len(json.dumps([o["code"] for o in s.spec["custom_ops"]])) > 20_000
    text, err = s.call("get_project", {})
    assert not err and len(text) < 30_000
    out = json.loads(text)
    # Custom ops are listed without their code; get_custom_op reads one in full.
    assert out["spec"]["custom_ops"][0] == {"id": "op_0", "description": "davits row 0", "code_lines": BIG_CODE.count("\n") + 1}
    code, err = s.call("get_custom_op", {"id": "op_3"})
    assert not err and json.loads(code)["code"] == BIG_CODE
    assert s.call("get_custom_op", {"id": "op_99"})[1]
    # Parts are one line each, rounded; a list too long says how to get the rest.
    assert "Lifesaving__Davit_S1 | Davit S1" in out["parts"][1]
    assert "more parts" in out["parts"][-1] and "get_parts" in out["parts"][-1]
    rows, err = s.call("get_parts", {"match": "Davit_S29"})
    assert not err and "Lifesaving__Davit_S29 " in rows and "Lifesaving__Davit_S28 " not in rows
    # The research is pointed to, not pasted.
    assert out["research"] is None


def test_builds_ai_reads_the_photo_research(make_client, settings):
    c = make_client()
    pid = create_ready(c)["id"]
    with_photo_research(c, pid)
    s = session(c, settings, pid)
    out = json.loads(s.call("get_project", {})[0])
    assert out["research"]["photos"] == 1 and "get_research" in out["research"]["read_with"]
    text, err = s.call("get_research", {})
    assert not err
    r = json.loads(text)
    assert r["verdict"] == "Model the post-refit ship."
    assert r["photos"][0]["observation"] == "Two funnel casings, dark blue boot top."
    assert r["closeups"][0]["what_it_shows"] == "Two separate casings side by side."
    assert r["claims"][0]["consequence_for_model"] == "hull_color white"
    # A claim resting only on a crossed-out source is marked, and the crossed-out note is given.
    assert r["claims"][1]["not_used"] is True and "S2 (Forum post)" in r["excluded"]


def test_builds_ai_can_look_at_a_photograph_or_close_up(make_client, settings):
    c = make_client()
    pid = create_ready(c)["id"]
    with_photo_research(c, pid)
    s = session(c, settings, pid)
    for pid_ in ("P01", "C01"):
        blocks, err = s.call_content("view_photo", {"id": pid_})
        assert not err and [b["type"] for b in blocks] == ["text", "image"], blocks
    assert "Two funnel casings" in s.call_content("view_photo", {"id": "P01"})[0][0]["text"]
    assert s.call_content("view_photo", {"id": "P99"})[1]


def test_update_the_model_from_the_research(make_client, settings):
    agent = ScriptedAgent([("get_research", {}), ("set_params", {"patch": {"bays": 6}}), ("rebuild", {})],
                          reply="Matched the funnels.")
    c = make_client(agent=agent)
    p = create_ready(c)
    pid = p["id"]
    r = c.post(f"/api/projects/{pid}/research/apply")
    assert r.status_code == 409 and "no research" in r.json()["detail"]
    with_photo_research(c, pid)
    r = c.post(f"/api/projects/{pid}/research/apply")
    assert r.status_code == 200, r.text
    job = wait_job(c, r.json()["job_id"])
    assert job["status"] == "done", job
    assert job["label"].startswith("Updating") and job["label"].endswith("from the research")
    assert agent.modes[-1] == "build" and "get_research" in str(agent.prompts[-1])
    assert "rebuild after each group" in str(agent.prompts[-1])
    p = c.get(f"/api/projects/{pid}").json()
    assert p["version"] == 2 and p["research_applied_rev"] == p["research_rev"]
    assert p["messages"][-1]["text"] == "Matched the funnels."
