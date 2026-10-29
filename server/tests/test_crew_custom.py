"""Crew 'made by description' (contract §6): colour fields, custom items, coloured equipment."""
from pathlib import Path

from app.agent import AgentSession, ScriptedAgent, module_helpers, system_prompt
from conftest import create_ready, wait_job

SINGLET = ("s = crew.garment('Clothes__Singlet', crew.REGIONS['torso'], (1, 1, 1))\n"
           "s.name = 'Clothes__Singlet'\n")


def _run_crew(make_client, steps):
    agent = ScriptedAgent(steps)
    c = make_client(agent=agent)
    p = create_ready(c, "crew", "male_default")
    job = wait_job(c, c.post(f"/api/projects/{p['id']}/chat", json={"message": "x"}).json()["job_id"])
    assert job["status"] == "done", job
    return agent, c.get(f"/api/projects/{p['id']}").json()["spec"]


def test_hair_and_clothing_colours(make_client):
    agent, spec = _run_crew(make_client, [
        ("set_body", {"patch": {"hair_color": [0.85, 0.7, 0.4]}}),
        ("set_body", {"patch": {"hair_color": [1.2, 0.7, 0.4]}}),
        ("set_body", {"patch": {"hair_color": [0.5, 0.5]}}),
        ("set_body", {"patch": {"hair_color": "blonde"}}),
        ("set_clothes", {"patch": {"shoes_color": [0.1, 0.1, 0.1], "hat_color": None}}),
        ("set_clothes", {"patch": {"hat_color": [0, 0, -0.1]}}),
        ("set_clothes", {"patch": {"suit": None}}),
    ])
    assert [e for _, _, e in agent.results] == [False, True, True, True, False, True, False], agent.results
    assert spec["body"]["hair_color"] == [0.85, 0.7, 0.4]
    assert spec["clothes"]["shoes_color"] == [0.1, 0.1, 0.1]
    # null is kept as null, not deleted: a deleted key is refilled from the builder's default suit.
    assert "suit" in spec["clothes"] and spec["clothes"]["suit"] is None


def test_custom_items_add_validate_remove(make_client):
    agent, spec = _run_crew(make_client, [
        ("add_custom_item", {"description": "white singlet", "code": SINGLET}),
        ("add_custom_item", {"description": "torch", "code":
                             "t = crew.cylinder('Equipment__Torch', 0.02, 0.2)\ncrew.hold(t, 'right')"}),
        ("add_custom_item", {"description": "bad name", "code": "o = crew.box('Hat__Cap', (1, 1, 1))"}),
        ("add_custom_item", {"description": "bad name", "code": "o.name = 'singlet'"}),
        ("add_custom_item", {"description": "evil", "code": "import os\nos.system('ls')"}),
        ("add_custom_item", {"description": "evil", "code": "open('/etc/passwd')"}),
        ("add_custom_item", {"description": "broken", "code": "def x(:\n  pass"}),
        ("add_custom_item", {"description": "", "code": "x = 1"}),
        ("remove_custom_item", {"id": "item_1"}),
        ("remove_custom_item", {"id": "item_9"}),
        ("add_custom_item", {"description": "white singlet again", "code": SINGLET}),
    ])
    errs = [e for _, _, e in agent.results]
    assert errs == [False, False, True, True, True, True, True, True, False, True, False], agent.results
    assert "Clothes__<Name>" in agent.results[2][1]
    assert "syntax" in agent.results[6][1]
    # item_1 removed, then its id reused for the next item.
    assert [(i["id"], i["description"]) for i in spec["custom_items"]] == [
        ("item_2", "torch"), ("item_1", "white singlet again")]


def test_equipment_accepts_coloured_entries(make_client):
    agent, spec = _run_crew(make_client, [
        ("set_equipment", {"equipment": [{"name": "life_vest", "color": "navy"}, "binoculars"]}),
        ("set_equipment", {"equipment": [{"name": "life_vest", "color": [0.1, 0.2, 0.3]}]}),
        ("set_equipment", {"equipment": [{"name": "life_vest", "color": "teal"}]}),
        ("set_equipment", {"equipment": [{"name": "rifle"}]}),
        ("set_equipment", {"equipment": [{"name": "life_vest", "size": 2}]}),
        ("set_equipment", {"equipment": [3]}),
    ])
    assert [e for _, _, e in agent.results] == [False, False, True, True, True, True], agent.results
    assert spec["equipment"] == [{"name": "life_vest", "color": [0.1, 0.2, 0.3]}]


def test_crew_and_ship_tool_sets_are_disjoint(make_client, settings):
    c = make_client()
    ship = create_ready(c)
    crew = create_ready(c, "crew", "male_default")
    from app.catalog import Catalog
    store = c.app.state.store
    catalog = Catalog(settings.catalog_dir)
    repo = Path(__file__).resolve().parents[2]

    def names(pid):
        s = AgentSession(store, None, catalog, pid, lambda m: None, repo)
        return {n for n, _, _ in s.tool_specs("build")}, s

    ship_tools, _ = names(ship["id"])
    crew_tools, crew_session = names(crew["id"])
    assert {"add_custom_item", "remove_custom_item", "set_body", "set_clothes", "set_equipment"} <= crew_tools
    assert not crew_tools & {"add_custom_op", "remove_custom_op", "set_params", "set_part_override",
                             "set_joint_limits"}
    assert not ship_tools & {"add_custom_item", "remove_custom_item", "set_body", "set_clothes"}
    # A ship-only tool called on a crew session is refused as unknown.
    text, err = crew_session.call("add_custom_op", {"description": "x", "code": "x = 1"})
    assert err and "Unknown tool" in text
    # The equipment schema accepts objects as well as names.
    eq = next(sch for n, _, sch in crew_session.tool_specs("build") if n == "set_equipment")
    kinds = [o["type"] for o in eq["properties"]["equipment"]["items"]["oneOf"]]
    assert kinds == ["string", "object"]


def test_crew_prompt_does_not_refuse_off_catalog_items():
    p = system_prompt("crew", "build")
    assert "not a limit" in p and "add_custom_item" in p and "suit to null" in p
    assert "cannot be made yet" not in p


def test_module_helpers_reads_a_helper_file(tmp_path):
    f = tmp_path / "custom.py"
    f.write_text(
        'import bpy\n'
        'REGIONS = {"torso": ["spine_01"], "thighs": ["thigh_l", "thigh_r"]}\n'
        '_PRIVATE = 1\n'
        'def garment(name, regions, color, offset=0.01, *, close=True):\n'
        '    """Cut a garment from the body surface over these regions."""\n'
        'def hold(obj, hand="right"):\n'
        '    """Put obj in a hand."""\n'
        'def _secret():\n'
        '    """hidden"""\n')
    doc = module_helpers(f, "crew")
    lines = doc.splitlines()
    assert "crew.REGIONS: keys torso, thighs" in lines
    assert ("crew.garment(name, regions, color, offset=0.01, *, close=True) — "
            "Cut a garment from the body surface over these regions.") in lines
    assert "crew.hold(obj, hand='right') — Put obj in a hand." in lines
    assert "_secret" not in doc and "_PRIVATE" not in doc
    assert "not readable" in module_helpers(tmp_path / "missing.py", "crew")
    (tmp_path / "broken.py").write_text("def (:")
    assert "not readable" in module_helpers(tmp_path / "broken.py", "crew")
