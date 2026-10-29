"""Aim stance (contract §6 "Stance") on the server: who owns it, the tools, and what the AI is told.

Lester, 29 Oct, in the animation editor: "raise the rifle up to shooting position" -- and the AI
answered that it could not. These check that it now can, from the main chat and from the Equipment
module's scoped chat, and that aiming adds the raise_to_aim move and nothing else.
"""
from app import crew_modules as cm
from app.agent import module_prompt, system_prompt
from test_crew_scoped_chat import chat, errors
from test_crew_two_hands import RIFLE, TORCH

ADD_RIFLE = ("add_custom_item", {"description": "AK-47 style rifle", "module": "equipment", "place": "both_hands",
                                 "code": RIFLE})


def _rifle(**kw):
    return dict({"id": "item_1", "module": "equipment", "description": "rifle", "place": "both_hands",
                 "code": RIFLE}, **kw)


def test_stance_is_owned_by_equipment():
    old, new = {"custom_items": [_rifle()]}, {"custom_items": [_rifle(stance="aim")]}
    cs = cm.changes(old, new)
    assert [(c.path, c.module, c.new) for c in cs] == [("custom_items[item_1].stance", "equipment", "aim")]
    assert cm.changed_modules(old, new) == {"equipment"}


def test_an_equipment_turn_may_add_raise_to_aim_only_when_it_aims():
    old = {"custom_items": [_rifle()], "animations": list(cm.DEFAULT_ANIMATIONS)}
    aimed = {"custom_items": [_rifle(stance="aim")], "animations": list(cm.DEFAULT_ANIMATIONS) + ["raise_to_aim"]}
    assert cm.outside(old, aimed, "equipment") == []
    # Not aimed: adding the move is an animation change, outside equipment.
    ready = {"custom_items": [_rifle(stance="ready")], "animations": aimed["animations"]}
    assert cm.outside(old, ready, "equipment") == ["animations"]
    # Aimed, but the turn also dropped squat: refused.
    dropped = dict(aimed, animations=["idle", "walk", "brace", "hands_up", "raise_to_aim"])
    assert cm.outside(old, dropped, "equipment") == ["animations"]
    # And it is only equipment's to do.
    assert cm.outside(old, aimed, "shirt") == ["animations", "custom_items[item_1].stance"]


def test_aim_from_the_equipment_module(make_client):
    """"raise the rifle up to shooting position" in the Equipment module's chat."""
    agent, job, proj = chat(make_client, [ADD_RIFLE, ("set_stance", {"item": "item_1", "stance": "aim"}),
                                          ("rebuild", {})], module="equipment")
    assert errors(agent) == [False, False, False], agent.results
    assert "raise_to_aim" in agent.results[1][1]
    spec = proj["spec"]
    assert spec["custom_items"][0]["stance"] == "aim"
    assert spec["animations"] == ["idle", "walk", "raise_to_aim"]      # the test catalog crew has idle and walk
    assert job["result"]["changed_modules"] == ["equipment"]
    assert "set_stance aim" in agent.prompts[0]                 # the scoped prompt says how


def test_stance_tool_refusals(make_client):
    agent, _, proj = chat(make_client, [
        ("add_custom_item", {"description": "torch", "module": "equipment", "place": "hip", "code": TORCH}),
        ("set_stance", {"item": "item_1", "stance": "aim"}),            # not in both hands
        ("set_stance", {"item": "item_9", "stance": "aim"}),            # no such item
        ADD_RIFLE,
        ("set_stance", {"item": "item_1", "stance": "aim"}),            # still the torch
        ("set_stance", {"item": "item_2", "stance": "hip_fire"}),       # not a stance
        ("set_stance", {"item": "item_2", "stance": "aim"}),
        ("set_stance", {"item": "item_2", "stance": "ready"}),
    ])
    assert errors(agent) == [False, True, True, False, True, True, False, False], agent.results
    assert "Nothing is in both hands" in agent.results[1][1]
    assert "The item in both hands is item_2" in agent.results[4][1]
    items = {i["id"]: i for i in proj["spec"]["custom_items"]}
    assert items["item_2"]["stance"] == "ready" and "stance" not in items["item_1"]
    assert "raise_to_aim" in proj["spec"]["animations"]                  # left in after lowering: harmless


def test_moving_an_aimed_item_out_of_both_hands_drops_its_stance(make_client):
    agent, _, proj = chat(make_client, [ADD_RIFLE, ("set_stance", {"item": "item_1", "stance": "aim"}),
                                        ("set_place", {"item": "item_1", "place": "back"})])
    assert errors(agent) == [False, False, False]
    assert "stance" not in proj["spec"]["custom_items"][0]


def test_add_custom_item_takes_a_stance_for_both_hands_only(make_client):
    agent, _, proj = chat(make_client, [
        ("add_custom_item", dict(ADD_RIFLE[1], stance="aim")),
        ("add_custom_item", {"description": "torch", "module": "equipment", "place": "hip", "code": TORCH,
                             "stance": "aim"}),
    ])
    assert errors(agent) == [False, True], agent.results
    assert proj["spec"]["custom_items"][0]["stance"] == "aim" and "raise_to_aim" in proj["spec"]["animations"]


def test_the_ai_is_told_it_can_aim():
    prompt = system_prompt("crew", "build")
    assert "shooting position" in prompt and "set_stance aim" in prompt and "Never say aiming is not possible" in prompt
    scoped = module_prompt("equipment")
    assert "Raise the rifle to shooting position" in scoped and "set_stance aim" in scoped
    assert "set_stance" in cm.MODULE_DOCS["equipment"]
