"""Reviewing research before a ship is built (contract §12, Review and Export): crossing sources out,
the user's own values in the design plan, and what is kept for review afterwards (every version of
the research, everything the AI read, every job's log), exported as PDF and PowerPoint."""
import base64
import io
import json
import zipfile

from app.agent import ScriptedAgent
from app.review import excluded_note
from conftest import wait_job
from test_custom_ship import RESEARCH, SUMMARY, YACHT, create_custom, drafted, research_turn, with_custom  # noqa: F401

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
TWO_SOURCES = {**RESEARCH,
               "sources": RESEARCH["sources"] + [{"id": "S02", "title": "Forum post", "url": "https://example.org/forum",
                                                  "status": "Unverified", "what_it_proves": "Length 30 m"}],
               "claims": RESEARCH["claims"] + [{"claim": "Length", "value": 30, "unit": "m", "sources": ["S02"],
                                                "status": "Unverified", "consequence_for_model": "loa_m 30"}]}


def get(c, pid):
    return c.get(f"/api/projects/{pid}").json()


def with_two_sources(make_client):
    c, pid, agent = drafted(make_client)
    agent.steps = [("save_research", {"research": TWO_SOURCES}), ("save_design_plan", {"summary": SUMMARY, "spec": YACHT})]
    research_turn(c, pid, "add the forum post")
    return c, pid, agent


def test_crossing_a_source_out_puts_the_plan_out_of_date_and_tells_the_ai(make_client, with_custom):
    c, pid, agent = with_two_sources(make_client)
    rev = get(c, pid)["research_rev"]
    r = c.patch(f"/api/projects/{pid}/research/excluded", json={"excluded_sources": ["S02"]})
    assert r.status_code == 200, r.text
    p = r.json()["project"]
    assert p["excluded_sources"] == ["S02"] and p["research_rev"] == rev + 1
    assert c.post(f"/api/projects/{pid}/design-plan/approve").status_code == 409  # redraft first
    agent.steps = [("save_design_plan", {"summary": SUMMARY, "spec": YACHT})]
    research_turn(c, pid, "redraft the plan")
    assert "S02 (Forum post)" in str(agent.prompts[-1]) and "crossed out" in str(agent.prompts[-1])
    assert c.patch(f"/api/projects/{pid}/research/excluded", json={"excluded_sources": ["S99"]}).status_code == 400


def test_the_redraft_after_a_research_change_is_told_what_was_crossed_out(make_client, with_custom, settings):
    settings.plan_follow_up = True
    c, pid, agent = with_two_sources(make_client)
    c.patch(f"/api/projects/{pid}/research/excluded", json={"excluded_sources": ["S02"]})
    turns = iter([[("save_research", {"research": TWO_SOURCES})],                  # the user's turn
                  [("save_design_plan", {"summary": SUMMARY, "spec": YACHT})]])    # the redraft
    real_run = agent.run

    def run(session, prompt, mode, log):
        agent.steps = next(turns, [])
        return real_run(session, prompt, mode, log)
    agent.run = run
    job = research_turn(c, pid, "the beam is 6.9 m")
    assert "• Redrafting the design plan to match the research" in job["log"]
    assert "S02 (Forum post)" in str(agent.prompts[-1]) and "crossed out" in str(agent.prompts[-1])


def test_excluded_note_is_empty_until_something_is_crossed_out():
    assert excluded_note({"research": RESEARCH}) == ""
    assert "P01 (User photo)" in excluded_note({"research": RESEARCH, "excluded_sources": ["P01"]})


def test_the_users_plan_values_win_survive_a_redraft_and_are_kept(make_client, with_custom):
    c, pid, agent = drafted(make_client)
    r = c.patch(f"/api/projects/{pid}/design-plan", json={"params": {"loa_m": 26, "hull_color": [0, 0, 0.3]}})
    assert r.status_code == 200, r.text
    assert r.json()["project"]["design_plan"]["user_params"] == {"loa_m": 26, "hull_color": [0, 0, 0.3]}
    for bad in [{"params": {"loa_m": -1}}, {"params": {"loa_m": 9000}}, {"params": {"hull_color": [2, 0, 0]}},
                {"params": {"warp": 1}}, {"loa_m": 3}]:
        assert c.patch(f"/api/projects/{pid}/design-plan", json=bad).status_code == 400, bad
    agent.steps = [("save_design_plan", {"summary": SUMMARY, "spec": YACHT})]
    research_turn(c, pid, "redraft")
    assert get(c, pid)["design_plan"]["user_params"]["loa_m"] == 26  # a redraft keeps them
    assert c.patch(f"/api/projects/{pid}/design-plan", json={"params": {"hull_color": None}}).json()["project"]["design_plan"]["user_params"] == {"loa_m": 26}
    agent.steps, agent.reply = [], "Details added."
    job = wait_job(c, c.post(f"/api/projects/{pid}/design-plan/approve").json()["job_id"])
    assert job["status"] == "done", job
    assert c.builder.calls[-1][1]["params"]["loa_m"] == 26  # built with the user's value
    assert '"loa_m": 26' in str(agent.prompts[-1]) and "Keep them exactly" in str(agent.prompts[-1])
    assert c.patch(f"/api/projects/{pid}/design-plan", json={"params": {"loa_m": 30}}).status_code == 409  # approved now


def test_every_research_version_what_was_read_and_job_logs_are_kept(make_client, with_custom):
    c, pid, _ = with_two_sources(make_client)
    hist = c.get(f"/api/projects/{pid}/research/history").json()
    assert [h["n"] for h in hist] == [1, 2] and hist[1]["sources"] == 2
    assert c.get(f"/api/projects/{pid}/research/history/2").json()["research"]["sources"][1]["id"] == "S02"
    logs = c.get(f"/api/projects/{pid}/logs").json()
    assert len(logs) >= 3 and all("log" in j for j in logs)  # the first build and both research turns


def test_materials_are_kept_with_the_text_the_ai_saw(make_client, with_custom):
    class Reading(ScriptedAgent):
        def run(self, session, prompt, mode, log):
            session.record_material("WebSearch", {"query": "yacht length"}, [{"type": "text", "text": "24 m"}])
            session.record_material("WebFetch", {"url": "https://example.org/yacht"}, "Yacht page text")
            return "Read two things."
    c = make_client(agent=Reading())
    out, _ = create_custom(c, brief="a yacht")  # waits for the first build, as the team's tests do
    pid = out["project"]["id"]
    research_turn(c, pid)
    mats = c.get(f"/api/projects/{pid}/materials").json()
    assert [(m["kind"], m["title"]) for m in mats] == [("search", "yacht length"), ("page", "https://example.org/yacht")]
    assert "text" not in mats[0] and c.get(f"/api/projects/{pid}/materials/m2").json()["text"] == "Yacht page text"
    assert c.get(f"/api/projects/{pid}/materials/x1").status_code == 404


def test_exports_carry_the_plan_the_photos_and_the_crossed_out_sources(make_client, with_custom, settings):
    c, pid, _ = with_two_sources(make_client)
    c.patch(f"/api/projects/{pid}/research/excluded", json={"excluded_sources": ["S02"]})
    store = c.app.state.store
    photo_dir = store.research_dir(pid) / "photos"
    photo_dir.mkdir(parents=True, exist_ok=True)
    (photo_dir / "P01.png").write_bytes(PNG)
    with store.edit(pid) as p:
        p["research"]["photos"] = [{"id": "P01", "title": "Port side", "credit": "Example", "status": "baseline",
                                    "file": f"/api/files/projects/{pid}/research/photos/P01.png"}]
        p["design_plan"]["user_params"] = {"loa_m": 26}
    pdf = c.get(f"/api/projects/{pid}/research/export.pdf")
    assert pdf.status_code == 200 and pdf.content[:5] == b"%PDF-" and b"/Subtype /Image" in pdf.content
    assert "attachment" in pdf.headers["content-disposition"]
    pptx = c.get(f"/api/projects/{pid}/research/export.pptx").content
    with zipfile.ZipFile(io.BytesIO(pptx)) as z:
        names = z.namelist()
        text = " ".join(z.read(n).decode("utf-8") for n in names if n.startswith("ppt/slides/slide"))
    assert any(n.startswith("ppt/media/") for n in names)
    for expected in ("Excluded by you", "Not used", "Port side", "Your value", "Length: 30 m"):
        assert expected in text, expected
    assert json.loads(c.get(f"/api/projects/{pid}/research/export.json").content)["subject"] == RESEARCH["subject"]
    assert c.get(f"/api/projects/{pid}/research/export.docx").status_code == 404
    other = c.post("/api/projects", json={"kind": "ship", "base": "container"}).json()["project"]["id"]
    assert c.get(f"/api/projects/{other}/research/export.pdf").status_code == 404
