"""Articulation roles (contract §3b): research names each moving component's role, the design plan commits each
instance's joints (the gate), and every build is checked against them (the nudge)."""
import copy
import json

import pytest

from app import articulation as art
from app.agent import ScriptedAgent, plan_build_prompt, system_prompt, validate_research
from app.design_plan import approve_refusal
from conftest import wait_job
from test_custom_ship import RESEARCH, SUMMARY, YACHT, create_custom, research_turn, with_custom  # noqa: F401

J = {"priority": "P2", "kind": "revolute"}


def part(path, parent, axis=None, lo=-170.0, hi=170.0, origin=(0, 0, 0), rot=(0, 0, 0)):
    return {"path": path, "parent": parent, "origin_m": list(origin), "rotation_deg": list(rot),
            "joint": dict(J, axis=axis, min_deg=lo, max_deg=hi) if axis else None}


# USS Abraham Lincoln as the editor built it (parts.json, 29 Oct): six weapon mounts that only turn.
LINCOLN = [part("Hull__Main", None)] + [part(f"Sponson__{s}", "Hull__Main") for s in
                                        ("Waist_Stbd", "Waist_Port", "Fwd_Port", "Aft_Stbd", "Fwd_Stbd", "Aft_Port")] + [
    part("Weapons__Phalanx_1", "Sponson__Waist_Stbd", "z"), part("Weapons__Phalanx_2", "Sponson__Waist_Port", "z"),
    part("Weapons__RAM_1", "Sponson__Fwd_Port", "z"), part("Weapons__RAM_2", "Sponson__Aft_Stbd", "z"),
    part("Weapons__SeaSparrow_1", "Sponson__Fwd_Stbd", "z"), part("Weapons__SeaSparrow_2", "Sponson__Aft_Port", "z"),
    part("Hull__Rudder_1", "Hull__Main", "z", -35, 35)]
LINCOLN_PLAN = [{"role": "gun_mount", "parts": ["Weapons__Phalanx_1", "Weapons__Phalanx_2"], "priority": "P0"},
                {"role": "trainable_launcher", "parts": ["Weapons__RAM_1", "Weapons__RAM_2", "Weapons__SeaSparrow_1",
                                                         "Weapons__SeaSparrow_2"], "priority": "P0"},
                {"role": "rudder", "parts": ["Hull__Rudder_1"]}]
LINCOLN_RESEARCH = {"articulation": [
    {"part": "Weapons__Phalanx", "role": "gun_mount", "count": 2, "motion": "train and elevate", "priority": "P0"},
    {"part": "Weapons__RAM", "role": "trainable_launcher", "count": 2, "motion": "train and elevate", "priority": "P0"},
    {"part": "Weapons__SeaSparrow", "role": "trainable_launcher", "count": 2, "motion": "train", "priority": "P1"},
    {"part": "Hull__Rudder", "role": "rudder", "count": 4, "motion": "yaw", "priority": "P2"},
    {"part": "Island__Main", "motion": "fixed", "priority": "P0"}]}


def chains():
    inst, errs = art.plan_problems(LINCOLN_PLAN, LINCOLN_RESEARCH, "ship")
    assert errs == []
    return inst


# ---- the catalog --------------------------------------------------------------------------------

def test_roles_follow_the_project_kind():
    ids = {r["id"] for r in art.roles_for("ship")}
    assert {"gun_mount", "trainable_launcher", "rotating_radar", "rudder"} <= ids
    assert "landing_gear" not in ids and art.roles_for("crew") == []


def test_expand_names_and_nests_the_chain():
    j = art.expand(art.role("trainable_launcher"), "Weapons__RAM_1", {"Elevation": [-10, 60]})
    assert [(x["name"], x["parent"], x["axis"]) for x in j] == [
        ("Weapons__RAM_1", None, "z"), ("Weapons__RAM_1__Elevation", "Weapons__RAM_1", "y"),
        ("Weapons__RAM_1__Launcher", "Weapons__RAM_1__Elevation", "y")]
    assert (j[1]["min"], j[1]["max"], j[2]["min"], j[2]["max"]) == (-10, 60, 0, 0)


def test_prompts_carry_the_roles_for_ships_only():
    for mode in ("research", "build"):
        p = system_prompt("ship", mode, "custom")
        assert "gun_mount" in p and "Gun (elevation, y)" in p and "add_chain" in p + system_prompt("ship", "build")
    assert "gun_mount" not in system_prompt("crew", "build")
    assert "Weapons__Phalanx_1__Gun" in plan_build_prompt({"summary": ["x"], "chains": chains()})


# ---- research --------------------------------------------------------------------------------------

def test_research_roles_are_checked():
    bad = copy.deepcopy(RESEARCH)
    bad["articulation"] = [{"part": "A__B", "motion": "spin", "role": "warp_drive"},
                           {"part": "A__C", "motion": "spin", "role": "other"},
                           {"part": "A__D", "motion": "yaw", "role": "rudder", "count": 0}]
    errs = validate_research(bad)
    assert len(errs) == 3 and "warp_drive" in errs[0] and "reason" in errs[1] and "count" in errs[2]
    assert validate_research(RESEARCH) == []          # older research without roles still saves


# ---- the plan (the gate) ---------------------------------------------------------------------------------

def test_plan_must_cover_each_researched_component():
    _, errs = art.plan_problems([], LINCOLN_RESEARCH, "ship")
    assert len(errs) == 3 and all("plan each one" in e for e in errs)  # Phalanx x2, RAM x2, Sea Sparrow x2 (P2 rudders pass)
    short = [{"role": "gun_mount", "parts": ["Weapons__Phalanx_1"]}, LINCOLN_PLAN[1]]
    _, errs = art.plan_problems(short, LINCOLN_RESEARCH, "ship")
    assert len(errs) == 1 and "2 x gun_mount" in errs[0] and "has 1" in errs[0]
    dropped = [{"role": "gun_mount", "dropped": "the user asked for an unarmed version"}, LINCOLN_PLAN[1]]
    assert art.plan_problems(dropped, LINCOLN_RESEARCH, "ship")[1] == []


def test_plan_refuses_bad_entries():
    _, errs = art.plan_problems([{"role": "landing_gear", "parts": ["Gear__Nose"]},
                                 {"role": "gun_mount", "parts": ["Phalanx 1"]},
                                 {"role": "gun_mount", "parts": ["Weapons__P_1"], "limits": {"Barrel": [0, 1]}},
                                 {"role": "rudder", "parts": ["Hull__Rudder_1"], "limits": {"": [-200, 35]}}],
                                None, "ship")
    assert len(errs) == 4, errs
    assert "landing_gear" in errs[0] and "Group__Part" in errs[1] and "'Barrel'" in errs[2] and "-180..180" in errs[3]


def test_plan_lines_say_what_moves():
    lines = art.summary_lines(chains())
    assert lines[0].startswith("Trainable gun") and "x2" in lines[0] and "gun elevates" in lines[0]


def test_old_plan_cannot_be_approved_once_research_names_roles():
    project = {"kind": "ship", "research_rev": 2, "research": LINCOLN_RESEARCH,
               "design_plan": {"status": "draft", "research_rev": 2, "summary": ["x"]}}
    assert "joints it needs" in approve_refusal(project)
    project["design_plan"]["articulation"] = LINCOLN_PLAN
    assert approve_refusal(project) is None


# ---- after the build (the nudge) ---------------------------------------------------------------------------

def test_lincoln_as_built_is_missing_every_elevation_joint():
    found = art.check(LINCOLN, chains())
    missing = [f["part"] for f in found if f["code"] == "articulation-missing-joint"]
    assert sorted(missing) == sorted(["Weapons__Phalanx_1__Gun", "Weapons__Phalanx_2__Gun"] + [
        f"Weapons__{w}__{s}" for w in ("RAM_1", "RAM_2", "SeaSparrow_1", "SeaSparrow_2")
        for s in ("Elevation", "Launcher")])                     # the 10 sim-only parts the MAK export added
    assert {f["code"] for f in found if f["level"] == "info"} == {"articulation-limits"}   # -170..170 vs -180..180
    # The Build screen shows every report warning: one short line per component, not a paragraph per joint.
    assert art.report_lines(found) == [
        "articulation: Weapons__Phalanx_1 (gun mount): Gun missing",
        "articulation: Weapons__Phalanx_2 (gun mount): Gun missing"] + [
        f"articulation: Weapons__{w} (trainable launcher): Elevation, Launcher missing"
        for w in ("RAM_1", "RAM_2", "SeaSparrow_1", "SeaSparrow_2")]
    assert {"component", "role", "joint"} <= set(found[0])


def test_a_plan_built_before_its_details_stays_short():
    """Right after a plan's first build (hull only) every planned joint is missing: Lincoln's 26-part plan gave
    36 paragraphs (12,000 characters) that pushed the Build viewport to nothing. One line per component now."""
    plan = chains()
    lines = art.report_lines(art.check([part("Hull__Main", None)], plan))
    assert len(lines) == len(plan) and all(line.endswith(": not built") for line in lines)
    assert sum(map(len, lines)) < 80 * len(lines)


def test_lincoln_with_the_chains_built_passes():
    fixed = copy.deepcopy(LINCOLN)
    for c in chains():
        for j in c["joints"][1:]:
            fixed.append(part(j["name"], j["parent"], j["axis"], j["min"], j["max"]))
    for p in fixed:
        if p["path"].startswith("Weapons__") and p["path"].count("__") == 1:
            p["joint"].update(min_deg=-180.0, max_deg=180.0)
    assert [f for f in art.check(fixed, chains()) if f["level"] == "warning"] == []


def test_wrong_axis_and_wrong_parent_are_named():
    plan = chains()[:1]
    parts = [part("Hull__Main", None), part("Weapons__Phalanx_1", "Hull__Main", "z", -180, 180),
             part("Weapons__Phalanx_1__Gun", "Hull__Main", "z", -20, 85)]
    codes = {f["code"] for f in art.check(parts, plan)}
    assert {"articulation-not-nested", "articulation-wrong-motion"} <= codes
    # a part turned 90 degrees about x has its local z along the ship's y: an elevation axis, so it passes
    parts[2] = part("Weapons__Phalanx_1__Gun", "Weapons__Phalanx_1", "z", -20, 85, rot=(90, 0, 0))
    assert not [f for f in art.check(parts, plan) if f["level"] == "warning"]


def test_a_joint_with_no_geometry_under_it_is_named():
    """MAK refuses a DOF node with nothing under it (manual 21.4, inspect_glb artpart-no-geometry)."""
    plan = chains()[:1]
    parts = [part("Hull__Main", None), part("Weapons__Phalanx_1", "Hull__Main", "z", -180, 180),
             part("Weapons__Phalanx_1__Gun", "Weapons__Phalanx_1", "y", -20, 85),
             dict(part("Weapons__Phalanx_1_Base", "Weapons__Phalanx_1"), triangles=12)]
    parts[1]["triangles"] = parts[2]["triangles"] = 0
    found = [(f["code"], f["part"]) for f in art.check(parts, plan) if f["level"] == "warning"]
    assert found == [("articulation-no-geometry", "Weapons__Phalanx_1__Gun")]


def test_role_tagged_joints_are_checked_without_a_plan():
    parts = [part("Hull__Main", None), part("Weapons__Phalanx_1", "Hull__Main", "z", -180, 180)]
    parts[1]["joint"].update(role="gun_mount", chain=0)
    assert [f["part"] for f in art.check(parts, None) if f["level"] == "warning"] == ["Weapons__Phalanx_1__Gun"]


# ---- through the server ----------------------------------------------------------------------------------

ARMED = dict(copy.deepcopy(RESEARCH), articulation=[
    {"part": "Weapons__Phalanx", "role": "gun_mount", "count": 1, "motion": "train and elevate", "priority": "P0"}])
ARMED_PLAN = [{"role": "gun_mount", "parts": ["Weapons__Phalanx_1"], "priority": "P0"}]


def test_plan_tool_refuses_then_stores_the_chains_and_the_build_reports_them(make_client, with_custom):
    agent = ScriptedAgent([("save_research", {"research": ARMED}),
                           ("save_design_plan", {"summary": SUMMARY, "spec": YACHT}),
                           ("save_design_plan", {"summary": SUMMARY, "spec": YACHT, "articulation": ARMED_PLAN})])
    c = make_client(agent=agent)
    out, _ = create_custom(c, brief="a patrol boat with a CIWS")
    pid = out["project"]["id"]
    research_turn(c, pid)
    refused = [t for n, t, err in agent.results if n == "save_design_plan" and err]
    assert len(refused) == 1 and "1 x gun_mount" in refused[0]
    plan = c.get(f"/api/projects/{pid}").json()["design_plan"]
    assert [j["name"] for j in plan["chains"][0]["joints"]] == ["Weapons__Phalanx_1", "Weapons__Phalanx_1__Gun"]

    agent.steps = [("rebuild", {})]
    job = wait_job(c, c.post(f"/api/projects/{pid}/design-plan/approve").json()["job_id"])
    assert job["status"] == "done", job
    report = job["result"]["build_report"]
    assert [f["part"] for f in report["articulation"] if f["level"] == "warning"] == [
        "Weapons__Phalanx_1", "Weapons__Phalanx_1__Gun"]            # the fake builder makes no weapons
    assert [w for w in report["warnings"] if w.startswith("articulation: ")] == [
        "articulation: Weapons__Phalanx_1 (gun mount): not built"]       # one short line per component
    rebuilt = json.loads(next(t for n, t, err in agent.results if n == "rebuild"))
    assert "Articulation: 2 joint(s)" in rebuilt["note"] and len(rebuilt["articulation"]) == 2
    assert "Weapons__Phalanx_1__Gun" in agent.prompts[-1]           # the build turn is told the chain
