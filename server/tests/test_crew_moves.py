"""Moves made from words on the server (contract §6 "Custom moves"): the tools, the moves module, and
what the AI is told. Lester's plan, "Animation editor": prompted moves as key poses on the shared
skeleton, refined in words ("faster on the way up")."""
from pathlib import Path

from app import crew_modules as cm
from app.agent import AgentSession, crew_moves, module_prompt, system_prompt
from test_crew_aim import ADD_RIFLE
from test_crew_scoped_chat import chat, errors

ROOT = Path(__file__).resolve().parents[2]
SALUTE_POSE = {"upperarm_r": {"twist": 60, "out": 90, "forward": 40}, "lowerarm_r": {"bend": 110, "twist": 60}}
SALUTE = {"name": "salute", "description": "Right-hand salute", "duration_s": 2.4,
          "keys": [{"t": 0.6, "pose": SALUTE_POSE}, {"t": 1.6, "pose": SALUTE_POSE}]}


def test_add_a_move_in_the_moves_module(make_client):
    agent, job, proj = chat(make_client, [("add_move", SALUTE), ("rebuild", {})], module="moves")
    assert errors(agent) == [False, False], agent.results
    moves = proj["spec"]["custom_moves"]
    assert [m["name"] for m in moves] == ["salute"]
    assert moves[0]["loop"] is False and moves[0]["feet"] == "planted" and moves[0]["keys"][0]["t"] == 0.6
    assert job["result"]["changed_modules"] == ["moves"]
    assert agent.prompts[0].startswith("You are editing only the moves module")


def test_bad_moves_are_refused_in_words_the_ai_can_act_on(make_client):
    agent, _, proj = chat(make_client, [
        ("add_move", dict(SALUTE, keys=[{"t": 0.5, "pose": {"upperarm_r": {"forward": 200}}}])),
        ("add_move", dict(SALUTE, keys=[{"t": 0.5, "pose": {"elbow_r": {"bend": 90}}}])),
        ("add_move", dict(SALUTE, keys=[{"t": 0.5, "pose": {"foot_l": [0.3, 0, 0, 0]}}])),
        ("add_move", dict(SALUTE, keys=[{"t": 0.5, "pose": {"thigh_l": {"forward": 30}}}])),
        ("add_move", dict(SALUTE, name="idle")),
        ("add_move", dict(SALUTE, keys=[{"t": 1.0, "pose": {}}, {"t": 0.5, "pose": {}}])),
        ("add_move", SALUTE),
        ("add_move", SALUTE),
    ])
    assert errors(agent) == [True, True, True, True, True, True, False, True], agent.results
    text = [r[1] for r in agent.results]
    assert "upperarm_r.forward 200 is outside what the joint does (-50..180 degrees)" in text[0]
    assert "unknown bone or channel 'elbow_r'" in text[1]
    assert "feet planted" in text[2] or "keeps the feet planted" in text[2]
    assert "pelvis_offset" in text[3]
    assert "built-in" in text[4]
    assert "time order" in text[5]
    assert "refine_move" in text[7]
    assert len(proj["spec"]["custom_moves"]) == 1


def test_refine_faster_and_rename_and_remove(make_client):
    faster = [{"t": 0.3, "pose": SALUTE_POSE}, {"t": 1.6, "pose": SALUTE_POSE}]
    agent, _, proj = chat(make_client, [
        ("add_move", SALUTE),
        ("refine_move", {"name": "salute", "keys": faster}),                  # "faster on the way up"
        ("refine_move", {"name": "salute", "time_scale": 0.5}),               # "twice as fast"
        ("refine_move", {"name": "salute", "new_name": "smart_salute"}),
        ("refine_move", {"name": "nope", "duration_s": 3}),
        ("add_move", dict(SALUTE, name="wave")),
        ("remove_move", {"name": "wave"}),
        ("remove_move", {"name": "wave"}),
    ], module="moves")
    assert errors(agent) == [False, False, False, False, True, False, False, True], agent.results
    [m] = proj["spec"]["custom_moves"]
    assert m["name"] == "smart_salute" and m["duration_s"] == 1.2
    assert [k["t"] for k in m["keys"]] == [0.15, 0.8]


def test_scopes_keep_moves_and_equipment_apart(make_client):
    agent, _, proj = chat(make_client, [("add_move", SALUTE)], module="equipment")
    assert errors(agent) == [True] and "custom_moves" in agent.results[0][1]
    assert not proj["spec"].get("custom_moves")
    agent, _, _ = chat(make_client, [ADD_RIFLE], module="moves")
    assert errors(agent) == [True]


def test_arm_moves_with_a_rifle_in_both_hands_say_the_arms_stay_on_it(make_client):
    agent, _, _ = chat(make_client, [ADD_RIFLE, ("add_move", SALUTE)])
    assert errors(agent) == [False, False]
    assert "carried in both hands" in agent.results[1][1] and "upperarm_r" in agent.results[1][1]


def test_the_ai_is_told_the_convention_and_the_recipes(tmp_path):
    prompt = system_prompt("crew", "build")
    assert "add_move" in prompt and "refine_move" in prompt
    scoped = module_prompt("moves")
    assert "faster on the way up" in scoped and "salute = upperarm_r" in scoped
    session = AgentSession.__new__(AgentSession)
    session.repo_root = ROOT
    doc = session._move_doc()
    for needle in ("neutral stance", "upperarm_l, upperarm_r", "twist (+ turns it outward)", "fingers_r",
                   "pelvis_offset", "upperarm_l/_r: forward -50..180", "salute = "):
        assert needle in doc, needle
    assert "moves" in cm.MODULE_DOCS and "add_move" in cm.MODULE_DOCS["moves"]


def test_the_server_uses_the_builders_rules():
    """One definition: the module the server loads is blender/crew/moves.py itself."""
    mv = crew_moves(ROOT)
    assert Path(mv.__file__).resolve() == (ROOT / "blender" / "crew" / "moves.py").resolve()
    errs = []
    assert mv.validate([SALUTE], errs) and errs == []
