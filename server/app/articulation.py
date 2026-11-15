"""Articulation roles: what a moving component needs from the model's hierarchy (contract §3b).

A simulator can only move what the model gives it a joint for. A weapon mount that only turns cannot show its
barrel pitching, and VR-Forces then adds a sim-only part (mak/references/open-questions.md Q20). The fix
belongs before the build: research names each moving component's role, the design plan commits each
instance's chain of joints, and the check below runs after every build.

The roles live in catalog/articulation/roles.json (shared with blender/common.py `add_chain`). They are
simulator-neutral: motion, axis, pivot, neutral pose and nesting, with the MAK evidence attached as data
(checked by mak/tests/test_roles.py). A role is tagged with the kinds it applies to (MAK profile kinds), and
`kinds_for_project` maps an editor project kind onto them, so a new kind is data, not code.

Pure functions only: no Blender, no server state.
"""
from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

ROLES_PATH = Path(__file__).resolve().parents[2] / "catalog" / "articulation" / "roles.json"
PRIORITIES = ("P0", "P1", "P2")
AXES = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}
_SEG = r"[A-Za-z0-9]+(?:_[A-Za-z0-9]+)*"
TOP_RE = re.compile(rf"^{_SEG}__{_SEG}$")          # Group__Part: the chain's children add __<suffix>
MOTION_WORDS = {"azimuth": "turns", "elevation": "elevates", "rotation": "swings", "spin": "spins",
                "translate_x": "slides fore-and-aft", "translate_y": "slides sideways", "translate_z": "rises"}
TOL = 1e-6


@lru_cache(maxsize=4)
def _load(path: str) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    data["by_id"] = {r["id"]: r for r in data.get("roles", [])}
    return data


def catalog(path: Path | None = None) -> dict:
    return _load(str(path or ROLES_PATH))


def kinds_for(project_kind: str | None, path: Path | None = None) -> list[str]:
    """The MAK profile kinds an editor project kind stands for (`ship` -> `surface_ship`)."""
    return list(catalog(path).get("kinds_for_project", {}).get(project_kind or "", []))


def roles_for(project_kind: str | None, path: Path | None = None) -> list[dict]:
    kinds = set(kinds_for(project_kind, path))
    return [r for r in catalog(path)["roles"] if kinds & set(r.get("kinds", []))]


def role(role_id: str, path: Path | None = None) -> dict | None:
    return catalog(path)["by_id"].get(role_id)


def chain_text(r: dict) -> str:
    """"mount (azimuth, z) -> Gun (elevation, y)": the role's chain on one line."""
    return " -> ".join(f"{j['suffix'] or 'mount'} ({j['motion']}, {j['axis']}"
                       f"{', fixed' if j['limits'][0] == j['limits'][1] else ''})" for j in r["joints"])


def prompt_block(project_kind: str | None, path: Path | None = None) -> str:
    """The roles for this kind, for the research and build prompts."""
    rows = roles_for(project_kind, path)
    if not rows:
        return ""
    lines = ["Articulation roles (catalog/articulation/roles.json). Each moving component the real object has is "
             "one of these, and needs every joint of its chain, each nested under the one before, origin at the "
             "pivot, in the neutral pose. Example names show the pattern (Group__Part for the top joint, "
             "Group__Part__<Suffix> for the ones under it):"]
    for r in rows:
        lines.append(f"- {r['id']}: {r['label']}. Chain: {chain_text(r)}. e.g. {r['example']}. "
                     f"Pivots: " + "; ".join(f"{j['suffix'] or 'mount'} at {j['pivot']}" for j in r["joints"]))
    lines.append("- other: anything else that moves; give its motion and say why no role fits.")
    return "\n".join(lines)


def expand(r: dict, top: str, limits: dict | None = None) -> list[dict]:
    """The joints one instance needs, top-down. `limits` overrides by suffix ("" for the top joint)."""
    out, parent = [], None
    for j in r["joints"]:
        name = f"{top}__{j['suffix']}" if j["suffix"] else top
        lo, hi = (limits or {}).get(j["suffix"], j["limits"])
        out.append({"name": name, "parent": parent, "suffix": j["suffix"], "motion": j["motion"], "axis": j["axis"],
                    "kind": j["kind"], "min": float(lo), "max": float(hi), "pivot": j["pivot"],
                    "neutral": j["neutral"], "holds": j["holds"]})
        parent = name
    return out


# ---- research ------------------------------------------------------------------------------

def research_problems(entries: Any, project_kind: str | None, path: Path | None = None) -> list[str]:
    """Errors in research `articulation` entries' role fields. `role` is optional (older research has none);
    when given it must be a role for this kind, or `other`."""
    if not isinstance(entries, list):
        return []
    known = {r["id"] for r in roles_for(project_kind, path)}
    errs = []
    for i, a in enumerate(entries):
        if not isinstance(a, dict) or "role" not in a:
            continue
        rid = a.get("role")
        if rid == "other":
            if not a.get("reason") and not a.get("why"):
                errs.append(f"articulation[{i}] has role 'other': add a reason saying why no role fits")
        elif rid not in known:
            errs.append(f"articulation[{i}].role {rid!r} is not a role for this kind; one of "
                        f"{sorted(known) + ['other']}")
        count = a.get("count", 1)
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            errs.append(f"articulation[{i}].count must be a whole number, 1 or more")
        if a.get("priority") is not None and a.get("priority") not in PRIORITIES:
            errs.append(f"articulation[{i}].priority must be one of {PRIORITIES}")
    return errs


# ---- the design plan (the gate) -----------------------------------------------------------------

def plan_problems(entries: Any, research: dict | None, project_kind: str | None,
                  path: Path | None = None) -> tuple[list[dict], list[str]]:
    """(instances, errors) for a design plan's `articulation` list.

    Each entry is {role, parts: [top joint names], priority?, limits?: {suffix: [min, max]}, dropped?}.
    An instance is {role, top, priority, joints}. Refused: an unknown role, a bad name, a limit override on a
    joint the role does not have or outside the role's range, and a P0/P1 research component (with a role) that
    has fewer planned instances than its count, unless an entry for that role says why it is `dropped`."""
    errs: list[str] = []
    instances: list[dict] = []
    if entries is None:
        entries = []
    if not isinstance(entries, list):
        return [], ["articulation must be a list of {role, parts, priority?, limits?, dropped?}"]
    known = {r["id"]: r for r in roles_for(project_kind, path)}
    planned: dict[str, int] = {}
    dropped: dict[str, str] = {}
    seen: set[str] = set()
    for i, e in enumerate(entries):
        w = f"articulation[{i}]"
        if not isinstance(e, dict):
            errs.append(f"{w} must be an object")
            continue
        r = known.get(e.get("role"))
        if r is None:
            errs.append(f"{w}.role {e.get('role')!r} is not a role for this kind; one of {sorted(known)}")
            continue
        if isinstance(e.get("dropped"), str) and e["dropped"].strip():
            dropped[r["id"]] = e["dropped"].strip()
            if not e.get("parts"):
                continue
        parts = e.get("parts")
        if not isinstance(parts, list) or not parts or not all(isinstance(p, str) for p in parts):
            errs.append(f"{w}.parts must list the top joint's name for each instance, e.g. [{r['example']!r}]")
            continue
        limits = e.get("limits") or {}
        suffixes = {j["suffix"]: j for j in r["joints"]}
        if not isinstance(limits, dict):
            errs.append(f"{w}.limits must be {{suffix: [min, max]}}")
            limits = {}
        for sfx, lim in limits.items():
            j = suffixes.get(sfx)
            if j is None:
                errs.append(f"{w}.limits names {sfx!r}; {r['id']} has joints {sorted(suffixes)} ('' is the top one)")
            elif not (isinstance(lim, list) and len(lim) == 2 and all(isinstance(x, (int, float)) for x in lim)
                      and lim[0] <= lim[1]):
                errs.append(f"{w}.limits[{sfx!r}] must be [min, max] with min <= max")
            elif j["kind"] == "revolute" and (lim[0] < -180 - TOL or lim[1] > 180 + TOL):
                errs.append(f"{w}.limits[{sfx!r}] {lim} is outside -180..180 degrees")
        prio = e.get("priority", "P1")
        if prio not in PRIORITIES:
            errs.append(f"{w}.priority must be one of {PRIORITIES}")
        for top in parts:
            if not TOP_RE.match(top):
                errs.append(f"{w}: {top!r} must be a Group__Part name (letters, digits, underscore); the joints "
                            "under it are named for you")
                continue
            if top in seen:
                errs.append(f"{w}: {top!r} is planned twice")
                continue
            seen.add(top)
            ok = {k: v for k, v in limits.items() if k in suffixes and isinstance(v, list) and len(v) == 2}
            instances.append({"role": r["id"], "top": top, "priority": prio, "joints": expand(r, top, ok)})
            planned[r["id"]] = planned.get(r["id"], 0) + 1
    for a in (research or {}).get("articulation") or []:
        if not isinstance(a, dict) or a.get("role") in (None, "other") or a.get("priority", "P1") not in ("P0", "P1"):
            continue
        rid, want = a["role"], a.get("count", 1) if isinstance(a.get("count"), int) else 1
        if rid not in known or rid in dropped:
            continue
        if planned.get(rid, 0) < want:
            errs.append(f"the research lists {want} x {rid} ({a.get('part')}, {a.get('priority', 'P1')}) but the plan "
                        f"has {planned.get(rid, 0)}: plan each one in articulation, or give the role an entry with "
                        "`dropped` saying why")
    return instances, errs


def summary_lines(instances: list[dict]) -> list[str]:
    """Plain lines for the plan card: one per role, with its chain and instances."""
    by_role: dict[str, list[str]] = {}
    for inst in instances:
        by_role.setdefault(inst["role"], []).append(inst["top"])
    out = []
    for rid, tops in by_role.items():
        r = role(rid) or {"label": rid, "joints": []}
        moves = ", then ".join(f"{(j['suffix'] or 'it').lower()} {MOTION_WORDS.get(j['motion'], j['motion'])}"
                               for j in r["joints"] if j["limits"][0] != j["limits"][1] or len(r["joints"]) == 1)
        out.append(f"{r['label']} x{len(tops)} ({', '.join(tops)}): {moves}.")
    return out


# ---- after a build (the nudge) -----------------------------------------------------------------

def _rotate(v, rotation_deg) -> tuple[float, float, float]:
    """Blender XYZ Euler, v' = Rz . Ry . Rx . v (as mak/from_package.rotate_xyz)."""
    ax, ay, az = (math.radians(a) for a in rotation_deg)
    x, y, z = v
    y, z = y * math.cos(ax) - z * math.sin(ax), y * math.sin(ax) + z * math.cos(ax)
    x, z = x * math.cos(ay) + z * math.sin(ay), -x * math.sin(ay) + z * math.cos(ay)
    x, y = x * math.cos(az) - y * math.sin(az), x * math.sin(az) + y * math.cos(az)
    return x, y, z


def world_axis(joint: dict, rotation_deg) -> str | None:
    """The ship-frame axis (x, y or z) a part's local joint axis points along, or None."""
    local = AXES.get(str(joint.get("axis", "")).lower())
    if local is None:
        return None
    w = _rotate(local, rotation_deg or [0, 0, 0])
    i = max(range(3), key=lambda k: abs(w[k]))
    return "xyz"[i] if abs(w[i]) > 0.9 else None


def _is_prismatic(joint: dict) -> bool:
    return str(joint.get("kind") or "revolute").lower() in ("prismatic", "slider", "linear")


def _has_geometry(name: str, by: dict, kids: dict) -> bool:
    """Whether the part or anything under it has triangles. A part without a `triangles` count (older
    parts.json, test stand-ins) counts as having geometry."""
    todo, seen = [name], set()
    while todo:
        n = todo.pop()
        if n in seen:
            continue
        seen.add(n)
        t = (by.get(n) or {}).get("triangles")
        if t is None or t > 0:
            return True
        todo += kids.get(n, [])
    return False


def instances_from_parts(parts: list[dict], path: Path | None = None) -> list[dict]:
    """Instances named by the joints themselves: a top joint built with add_chain carries `role` and
    `chain` 0. Used for joints the plan does not cover (catalog ships, details added later)."""
    out = []
    for p in parts:
        j = p.get("joint") or {}
        r = role(j.get("role")) if j.get("role") else None
        if r and j.get("chain", 0) == 0:
            out.append({"role": r["id"], "top": p["path"], "priority": j.get("priority", "P1"),
                        "joints": expand(r, p["path"])})
    return out


def check(parts: list[dict], planned: list[dict] | None, path: Path | None = None) -> list[dict]:
    """Findings for a build's parts.json against the planned instances (plus any role-tagged ones).
    Each finding: {level: warning|info, code, part, message}."""
    by = {p.get("path"): p for p in parts if p.get("path")}
    kids: dict[str, list[str]] = {}
    for p in parts:
        if p.get("parent"):
            kids.setdefault(p["parent"], []).append(p["path"])
    planned = list(planned or [])
    tops = {i["top"] for i in planned}
    instances = planned + [i for i in instances_from_parts(parts, path) if i["top"] not in tops]
    findings: list[dict] = []
    covered: set[str] = set()

    def add(level, code, part, msg):
        findings.append({"level": level, "code": code, "part": part, "message": msg})

    for inst in instances:
        r = role(inst["role"], path) or {"label": inst["role"]}
        for want in inst["joints"]:
            name = want["name"]
            covered.add(name)
            p = by.get(name)
            what = f"{name} ({want['motion']}, part of {r['label'].lower()} {inst['top']})"
            if p is None:
                effect = ("the simulation has to add it on its own and the model cannot show it moving"
                          if want["parent"] else "nothing of this component can move")
                add("warning", "articulation-missing-joint", name,
                    f"{what} is missing: {effect}. Make it with me3d.add_chain('{inst['role']}', "
                    f"'{inst['top']}', ...) or add the joint under {want['parent'] or 'the part it sits on'}, "
                    f"origin at {want['pivot']}.")
                continue
            if not _has_geometry(name, by, kids):
                add("warning", "articulation-no-geometry", name,
                    f"{what} has no geometry under it: every joint needs some (manual 21.4), even a fixed one. "
                    f"Parent {want['holds']} under it.")
            j = p.get("joint")
            if not j:
                add("warning", "articulation-missing-joint", name,
                    f"{what} exists but has no joint: give it me3d_joint (axis {want['axis']}), origin at "
                    f"{want['pivot']}.")
                continue
            if want["parent"]:
                anc, seen = p.get("parent"), set()
                while anc and anc != want["parent"] and anc not in seen:
                    seen.add(anc)
                    anc = (by.get(anc) or {}).get("parent")
                if anc != want["parent"]:
                    add("warning", "articulation-not-nested", name,
                        f"{what} is not under {want['parent']}: parent it there so it moves with it "
                        "(a child part's joint must be a descendant of its parent part's).")
            got = world_axis(j, p.get("rotation_deg"))
            if got != want["axis"] or _is_prismatic(j) != (want["kind"] == "prismatic"):
                add("warning", "articulation-wrong-motion", name,
                    f"{what} moves {'along' if _is_prismatic(j) else 'about'} {got or 'no ship axis'}; its role "
                    f"needs {'a slide along' if want['kind'] == 'prismatic' else 'a turn about'} {want['axis']}.")
            lo, hi = j.get("min_deg"), j.get("max_deg")
            if (isinstance(lo, (int, float)) and isinstance(hi, (int, float)) and want["motion"] != "spin"
                    and (abs(lo - want["min"]) > 0.5 or abs(hi - want["max"]) > 0.5)):
                add("info", "articulation-limits", name,
                    f"{name} moves {lo:g}..{hi:g}; its role's limits are {want['min']:g}..{want['max']:g}.")
    for p in parts:
        if p.get("joint") and p.get("path") not in covered:
            add("info", "articulation-unplanned", p["path"],
                f"{p['path']} moves but no planned component covers it.")
    return findings


def report_lines(findings: list[dict]) -> list[str]:
    """Build-report warnings ("articulation: ...") for the warning-level findings."""
    return [f"articulation: {f['message']}" for f in findings if f["level"] == "warning"]
