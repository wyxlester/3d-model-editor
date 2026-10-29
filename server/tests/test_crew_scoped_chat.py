"""Scoped chat and module-shaped tools (contract §6 "Modules", §10 POST /chat "module")."""
from app.agent import ScriptedAgent
from conftest import create_ready, wait_job

TORCH = ("bm = crew.new_bmesh()\ncrew.cylinder(bm, (0, 0, 0.05), 0.02, 0.2)\n"
         "t = crew.object_from_bmesh('Equipment__Torch', bm, (1, 1, 0))\n")
CLIPBOARD = ("bm = crew.new_bmesh()\ncrew.box(bm, (0, 0, 0.1), (0.02, 0.22, 0.3))\n"
             "c = crew.object_from_bmesh('Equipment__Clipboard', bm, (0.6, 0.4, 0.2))\n")
SINGLET = "s = crew.garment('Clothes__Singlet', crew.REGIONS['torso'], (1, 1, 1))\n"
SHORTS = "s = crew.garment('Clothes__Shorts', ['pelvis', 'thighs'], (0, 0, 0.5))\n"
POLO = {"style": "polo", "color": [0.1, 0.2, 0.6]}


def chat(make_client, steps, module=None):
    """One chat turn on a fresh crew project, the AI replaced by scripted tool calls."""
    agent = ScriptedAgent(steps)
    c = make_client(agent=agent)
    p = create_ready(c, "crew", "male_default")
    body = {"message": "make it so"}
    if module:
        body["module"] = module
    r = c.post(f"/api/projects/{p['id']}/chat", json=body)
    assert r.status_code == 200, r.text
    job = wait_job(c, r.json()["job_id"])
    assert job["status"] == "done", job
    return agent, job, c.get(f"/api/projects/{p['id']}").json()


def errors(agent):
    return [e for _, _, e in agent.results]


def test_scoped_chat_refuses_an_out_of_module_change_and_changes_nothing(make_client):
    agent, job, proj = chat(make_client, [("set_body", {"patch": {"hair": "afro01"}}),
                                          ("set_clothes", {"patch": {"shirt": POLO, "shoes": None}})],
                            module="shirt")
    assert errors(agent) == [True, True], agent.results
    text = agent.results[0][1]
    assert "only change the shirt module" in text and "body.hair" in text and "hair" in text
    assert "clothes.shoes" in agent.results[1][1]  # mixed call refused whole, shirt part included
    assert proj["version"] == 1  # nothing to build
    assert proj["spec"]["body"].get("hair") is None and proj["spec"]["clothes"]["shoes"] == "shoes01"
    assert "shirt" not in proj["spec"]["clothes"]
    assert job["result"]["changed_modules"] == []
    assert agent.prompts[0].startswith("You are editing only the shirt module")


def test_scoped_chat_allows_its_own_module_with_the_outfit_side_effect(make_client):
    agent, job, proj = chat(make_client, [("set_clothes", {"patch": {"shirt": POLO}}), ("rebuild", {})],
                            module="shirt")
    assert errors(agent) == [False, False], agent.results
    assert "outfit was removed" in agent.results[0][1]
    spec = proj["spec"]
    assert spec["clothes"]["shirt"] == POLO and spec["clothes"]["suit"] is None
    assert spec["clothes"]["pants"]["style"] == "trousers"  # not left without trousers
    assert spec["clothes"]["shoes"] == "shoes01" and spec["equipment"] == ["life_vest"]
    assert job["result"]["changed_modules"] == ["shirt", "pants", "outfit"]


def test_outfit_choice_in_outfit_scope(make_client):
    steps = [("set_clothes", {"patch": {"shirt": POLO}}),
             ("add_custom_item", {"description": "shorts", "module": "pants", "code": SHORTS}),
             ("add_custom_item", {"description": "torch", "module": "equipment", "place": "right_hand",
                                  "code": TORCH})]
    agent = ScriptedAgent(steps)
    c = make_client(agent=agent)
    p = create_ready(c, "crew", "male_default")
    wait_job(c, c.post(f"/api/projects/{p['id']}/chat", json={"message": "x"}).json()["job_id"])
    assert errors(agent) == [False, False, False], agent.results
    agent.steps = [("set_clothes", {"patch": {"suit": "male_casualsuit02", "suit_color": [1, 0.45, 0.1]}})]
    agent.results.clear()
    job = wait_job(c, c.post(f"/api/projects/{p['id']}/chat",
                             json={"message": "orange casual suit", "module": "outfit"}).json()["job_id"])
    assert errors(agent) == [False], agent.results
    spec = c.get(f"/api/projects/{p['id']}").json()["spec"]
    assert spec["clothes"]["suit"] == "male_casualsuit02" and spec["clothes"]["shirt"] is None
    assert spec["clothes"].get("pants") is None
    assert [i["description"] for i in spec["custom_items"]] == ["torch"]  # shorts gone, torch kept
    assert job["result"]["changed_modules"] == ["shirt", "pants", "outfit"]


def test_add_custom_item_replace_semantics(make_client):
    agent, _, proj = chat(make_client, [
        ("add_custom_item", {"description": "singlet", "module": "shirt", "code": SINGLET}),
        ("add_custom_item", {"description": "white singlet", "module": "shirt", "code": SINGLET}),  # replaces
        ("add_custom_item", {"description": "torch", "module": "equipment", "place": "right_hand", "code": TORCH}),
        ("add_custom_item", {"description": "clipboard", "module": "equipment", "place": "left_hand",
                             "code": CLIPBOARD}),  # alongside
        ("add_custom_item", {"description": "vest over singlet", "module": "shirt", "code": SINGLET,
                             "replace": False}),
        ("add_custom_item", {"description": "only a torch", "module": "equipment", "place": "hip", "code": TORCH,
                             "replace": True}),
    ])
    assert errors(agent) == [False] * 6, agent.results
    assert "replacing item_1" in agent.results[1][1]
    items = [(i["module"], i["description"]) for i in proj["spec"]["custom_items"]]
    assert items == [("shirt", "white singlet"), ("shirt", "vest over singlet"), ("equipment", "only a torch")]


def test_custom_item_rules(make_client):
    agent, _, proj = chat(make_client, [
        ("add_custom_item", {"description": "x", "code": SINGLET}),  # no module
        ("add_custom_item", {"description": "x", "module": "gender", "code": SINGLET}),  # not a custom module
        ("add_custom_item", {"description": "torch", "module": "equipment", "code": TORCH}),  # no place
        ("add_custom_item", {"description": "torch", "module": "equipment", "place": "pocket", "code": TORCH}),
        ("add_custom_item", {"description": "torch", "module": "equipment", "place": "right_hand",
                             "code": TORCH + "crew.hold(t, 'right')\n"}),  # parents itself
        ("add_custom_item", {"description": "torch", "module": "equipment", "place": "right_hand",
                             "code": TORCH + "t.parent = crew.armature()\n"}),
        ("add_custom_item", {"description": "singlet", "module": "equipment", "place": "chest", "code": SINGLET}),
        ("add_custom_item", {"description": "torch", "module": "shirt", "code": TORCH}),
        ("add_custom_item", {"description": "singlet", "module": "shirt", "place": "chest", "code": SINGLET}),
    ])
    assert errors(agent) == [True] * 9, agent.results
    assert "module is required" in agent.results[0][1]
    assert "needs a place" in agent.results[2][1]
    assert "must not parent" in agent.results[4][1] and "must not parent" in agent.results[5][1]
    assert proj["version"] == 1 and "custom_items" not in proj["spec"]


def test_placement_scope_moves_only_the_place(make_client):
    agent, _, proj = chat(make_client, [
        ("set_equipment", {"equipment": ["life_vest", {"name": "binoculars", "place": "chest"}]}),
        ("add_custom_item", {"description": "torch", "module": "equipment", "place": "right_hand", "code": TORCH}),
    ])
    assert errors(agent) == [False, False]
    agent = ScriptedAgent([("set_place", {"item": "binoculars", "place": "left_hand"}),
                           ("set_place", {"item": "item_1", "place": "hip"}),
                           ("set_place", {"item": "life_vest", "place": "back"}),  # fixed spot
                           ("set_place", {"item": "rope", "place": "back"}),
                           ("set_equipment", {"equipment": ["life_vest"]})])  # equipment change: refused
    c = make_client(agent=agent)
    # Re-use the stored project: the store is per settings, so a second client sees it.
    job = wait_job(c, c.post(f"/api/projects/{proj['id']}/chat",
                             json={"message": "move things", "module": "placement"}).json()["job_id"])
    assert errors(agent) == [False, False, True, True, True], agent.results
    assert "only change the placement module" in agent.results[4][1]
    spec = c.get(f"/api/projects/{proj['id']}").json()["spec"]
    assert spec["equipment"] == ["life_vest", {"name": "binoculars", "place": "left_hand"}]
    assert spec["custom_items"][0]["place"] == "hip" and spec["custom_items"][0]["code"] == TORCH
    assert job["result"]["changed_modules"] == ["placement"]


def test_equipment_scope_cannot_move_and_set_equipment_keeps_places(make_client):
    agent, _, proj = chat(make_client, [
        ("set_equipment", {"equipment": ["life_vest", {"name": "binoculars", "place": "chest"}]})])
    agent = ScriptedAgent([("set_place", {"item": "binoculars", "place": "back"}),
                           ("set_equipment", {"equipment": ["life_vest", {"name": "binoculars", "color": "red"}]})])
    c = make_client(agent=agent)
    job = wait_job(c, c.post(f"/api/projects/{proj['id']}/chat",
                             json={"message": "red binoculars", "module": "equipment"}).json()["job_id"])
    assert errors(agent) == [True, False], agent.results
    spec = c.get(f"/api/projects/{proj['id']}").json()["spec"]
    assert spec["equipment"][1] == {"name": "binoculars", "color": "red", "place": "chest"}
    assert job["result"]["changed_modules"] == ["equipment"]


def test_main_chat_reports_changed_modules(make_client):
    agent, job, _ = chat(make_client, [("set_body", {"patch": {"hair_color": [0.85, 0.7, 0.4]}}),
                                       ("set_accessories", {"accessories": ["sunglasses"]}),
                                       ("set_accessories", {"accessories": ["tiara"]})])
    assert errors(agent) == [False, False, True], agent.results
    assert job["result"]["changed_modules"] == ["hair", "accessories"]
    assert not agent.prompts[0].startswith("You are editing only")


def test_ship_chat_has_no_changed_modules(make_client):
    c = make_client()
    p = create_ready(c)
    job = wait_job(c, c.post(f"/api/projects/{p['id']}/chat", json={"message": "x"}).json()["job_id"])
    assert "changed_modules" not in job["result"]


def test_chat_module_validation(make_client):
    c = make_client()
    crew = create_ready(c, "crew", "male_default")
    ship = create_ready(c)
    r = c.post(f"/api/projects/{crew['id']}/chat", json={"message": "x", "module": "hat"})
    assert r.status_code == 400 and "module must be one of" in r.text
    r = c.post(f"/api/projects/{ship['id']}/chat", json={"message": "x", "module": "shirt"})
    assert r.status_code == 400 and "crew" in r.text
    r = c.post(f"/api/projects/{crew['id']}/chat", json={"message": "x", "module": "shirt", "mode": "research"})
    assert r.status_code == 400
    # A refused request records no message.
    assert c.get(f"/api/projects/{crew['id']}").json()["messages"] == []


def test_post_spec_applies_the_outfit_rule_both_ways(make_client):
    c = make_client()
    p = create_ready(c, "crew", "male_default")
    pid = p["id"]

    def patch(body):
        r = c.post(f"/api/projects/{pid}/spec", json={"patch": body})
        if r.status_code != 200:
            return r
        job = wait_job(c, r.json()["job_id"])
        assert job["status"] == "done", job
        return c.get(f"/api/projects/{pid}").json()["spec"]

    spec = patch({"clothes": {"shirt": POLO}})
    assert spec["clothes"]["shirt"] == POLO and spec["clothes"]["suit"] is None
    spec = patch({"clothes": {"pants": {"style": "shorts", "color": [0, 0, 0]}}})
    assert spec["clothes"]["pants"]["style"] == "shorts" and spec["clothes"]["suit"] is None
    spec = patch({"clothes": {"suit": "male_worksuit01"}})
    assert spec["clothes"]["suit"] == "male_worksuit01"
    assert spec["clothes"]["shirt"] is None and spec["clothes"]["pants"] is None
    assert spec["equipment"] == ["life_vest"]  # nothing else touched
    r = patch({"clothes": {"suit": "male_casualsuit02", "shirt": POLO}})
    assert r.status_code == 400 and "outfit" in r.text
