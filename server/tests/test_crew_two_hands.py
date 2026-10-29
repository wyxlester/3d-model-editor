"""Two-handed carry (contract §6 Placement, place both_hands).

Lester, 29 Oct, in Equipment placement: "carry rifle in both hands. left hand on barrel, right hand
on trigger". The AI could not, because every place was one bone. These check that the place is
accepted by the tools, that the hands are not double-booked, and that the AI is told how to do it.
"""
from app import crew_modules as cm
from app.agent import AgentSession, ScriptedAgent, describe_tool, system_prompt
from conftest import create_ready, wait_job

RIFLE = ("bm = crew.new_bmesh()\ncrew.box(bm, (0, 0, 0.2), (0.05, 0.04, 0.9))\n"
         "o = crew.object_from_bmesh('Equipment__Rifle', bm, (0.1, 0.1, 0.1))\n"
         "crew.two_hand_grips(o, (0, 0, 0), (0, 0, 0.3))\n")
TORCH = ("bm = crew.new_bmesh()\ncrew.cylinder(bm, (0, 0, 0.05), 0.02, 0.2)\n"
         "t = crew.object_from_bmesh('Equipment__Torch', bm, (1, 1, 0))\n")


def _run(make_client, steps):
    agent = ScriptedAgent(steps)
    c = make_client(agent=agent)
    p = create_ready(c, "crew", "male_default")
    job = wait_job(c, c.post(f"/api/projects/{p['id']}/chat", json={"message": "x"}).json()["job_id"])
    assert job["status"] == "done", job
    return agent, c.get(f"/api/projects/{p['id']}").json()["spec"]


def test_both_hands_is_a_place_owned_by_placement():
    assert "both_hands" in cm.PLACES
    old = {"custom_items": [{"id": "item_1", "module": "equipment", "place": "right_hand", "code": RIFLE}]}
    new = {"custom_items": [{"id": "item_1", "module": "equipment", "place": "both_hands", "code": RIFLE}]}
    assert cm.changed_modules(old, new) == {"placement"}


def test_hand_conflicts():
    rifle = {"id": "item_1", "module": "equipment", "description": "rifle", "place": "both_hands", "code": RIFLE}
    assert cm.hand_conflicts({"equipment": ["life_vest", "radio", "binoculars"], "custom_items": [rifle]}) == []
    # A radio moved into a hand, or a described item with no place (held in the right hand), clashes.
    msg = cm.hand_conflicts({"equipment": [{"name": "radio", "place": "left_hand"}], "custom_items": [rifle]})
    assert len(msg) == 1 and "radio (left_hand)" in msg[0] and "item_1 (rifle)" in msg[0]
    torch = {"id": "item_2", "module": "equipment", "code": TORCH}
    assert "item_2 (right_hand)" in cm.hand_conflicts({"custom_items": [rifle, torch]})[0]
    two = cm.hand_conflicts({"custom_items": [rifle, dict(rifle, id="item_3")]})
    assert len(two) == 1 and "Only one" in two[0]
    # Without a both_hands item, hands are as before: a torch in each hand is fine.
    assert cm.hand_conflicts({"custom_items": [torch, dict(torch, id="item_4", place="left_hand")]}) == []


def test_tools_accept_both_hands_and_refuse_a_clash(make_client):
    agent, spec = _run(make_client, [
        ("add_custom_item", {"description": "AK-47 style rifle", "module": "equipment", "place": "right_hand",
                             "code": RIFLE}),
        ("set_place", {"item": "item_1", "place": "both_hands"}),
        ("add_custom_item", {"description": "torch", "module": "equipment", "place": "left_hand", "code": TORCH}),
        ("add_custom_item", {"description": "torch", "module": "equipment", "place": "hip", "code": TORCH}),
        ("set_place", {"item": "item_2", "place": "right_hand"}),
        ("set_equipment", {"equipment": ["life_vest", {"name": "binoculars", "place": "right_hand"}]}),
        ("set_equipment", {"equipment": ["life_vest", "binoculars"]}),
    ])
    assert [e for _, _, e in agent.results] == [False, False, True, False, True, True, False], agent.results
    assert "both hands" in agent.results[2][1] and "move" in agent.results[2][1]
    items = {i["id"]: i for i in spec["custom_items"]}
    assert items["item_1"]["place"] == "both_hands" and items["item_2"]["place"] == "hip"
    assert spec["equipment"] == ["life_vest", "binoculars"]


def test_ai_is_told_how_to_carry_in_both_hands(tmp_path):
    prompt = system_prompt("crew", "build")
    assert "both_hands" in prompt and "both hands" in prompt
    session = AgentSession.__new__(AgentSession)
    session.repo_root = __import__("pathlib").Path(__file__).resolve().parents[2]
    session.kind = "crew"
    doc = session._custom_item_doc()
    assert "crew.two_hand_grips" in doc and "both_hands" in doc
    assert "crew.two_hand_grips(obj, grip, support)" in doc
    assert describe_tool("set_place", {"item": "rifle", "place": "both_hands"}) == "Moving rifle into both hands"
