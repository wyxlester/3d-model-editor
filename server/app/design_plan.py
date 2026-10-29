"""Design plans for `type: "custom"` ships (contract §5, the custom paragraph).

A custom ship is researched first and built only from a plan the user approved:
research turns amend the research (each save bumps `research_rev`), the AI drafts a plan
(`save_design_plan`), the user approves it, and only then does anything build.

Pure functions only, so the rules that go wrong quietly (a tier hanging off the hull, a plan
approved against research that has since changed) are unit-tested without a server or an AI.
"""
from __future__ import annotations

import copy
from typing import Any

from .specmerge import deep_merge

# Contract §5 names these forms. Used only when the catalog has no options for them.
BOW_FORMS = ["plumb", "raked", "flared", "bulbous", "axe"]
STERN_FORMS = ["transom", "cruiser", "swim_platform", "open"]
HULL_KEYS = ("loa_m", "beam_m", "depth_m", "draught_m")
PLAN_SPEC_KEYS = {"schema", "type", "id", "name", "params", "meta", "overrides", "joint_limits", "custom_ops"}
TOL = 1e-6
# Tier fields blender/ships/custom.py accepts (its TIER_KEYS); it refuses anything else, so the
# plan is refused here first, while the user can still read why.
TIER_FIELDS = {"x_aft_m", "x_fwd_m", "width_m", "height_m", "setback_m", "rake_deg", "round",
               "overhang_m", "overhang_aft_m", "open", "color", "windows"}

# Short vessel words, most specific first, for naming a project from its brief.
VESSEL_WORDS = [
    "superyacht", "motor yacht", "sailing yacht", "yacht", "catamaran", "trimaran", "patrol boat",
    "patrol vessel", "fast attack craft", "landing craft", "pilot boat", "fireboat", "fire boat",
    "tugboat", "tug", "trawler", "fishing boat", "ferry", "water taxi", "bumboat", "sampan", "junk",
    "barge", "dredger", "frigate", "corvette", "destroyer", "submarine", "lifeboat", "rib", "speedboat",
    "speed boat", "cruise ship", "research vessel", "supply vessel", "offshore supply vessel",
    "container ship", "tanker", "bulk carrier", "car carrier", "cargo ship", "dinghy", "launch",
    "workboat", "pontoon", "houseboat", "sailboat", "boat", "ship", "vessel",
]
GENERIC = {"boat", "ship", "vessel"}


def name_from_brief(brief: str | None) -> str:
    """A short project name from the user's words: "create a yacht ship model …" → "Yacht"."""
    cleaned = "".join(ch if ch.isalnum() else " " for ch in (brief or "").lower())
    text = " " + " ".join(cleaned.split()) + " "
    hits = []
    for w in VESSEL_WORDS:
        if w in GENERIC:
            continue
        found = [i for i in (text.find(f" {w} "), text.find(f" {w}s ")) if i >= 0]
        if found:
            hits.append((min(found), w))
    if not hits:
        return "Custom ship"
    # The earliest vessel word; on a tie the longer phrase ("motor yacht" over "yacht").
    word = min(hits, key=lambda h: (h[0], -len(h[1])))[1]
    return "RIB" if word == "rib" else word.capitalize()


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _rgb(v) -> bool:
    return isinstance(v, list) and len(v) == 3 and all(_num(x) and 0 <= x <= 1 for x in v)


def _doc(docs: dict, key: str) -> dict:
    d = docs.get(key) if isinstance(docs, dict) else None
    return d if isinstance(d, dict) else {}


def _field_docs(doc: dict) -> dict:
    """Per-field docs of a list-of-objects param (tiers), if the catalog gives them."""
    for k in ("items", "fields", "item_docs"):
        if isinstance(doc.get(k), dict):
            return doc[k]
    return {}


def _check_range(where: str, v, d: dict, errs: list[str]) -> None:
    if not isinstance(d, dict):
        return
    if _num(v):
        if "min" in d and _num(d["min"]) and v < d["min"] - TOL or "max" in d and _num(d["max"]) and v > d["max"] + TOL:
            errs.append(f"{where}={v} is outside {d.get('min')}..{d.get('max')}")
    opts = d.get("options")
    if isinstance(opts, list) and opts and not isinstance(v, (list, dict)) and v not in opts:
        errs.append(f"{where} must be one of {opts}, got {v!r}")


def validate_custom_params(params: dict, docs: dict, known: set[str] | None) -> list[str]:
    """Errors in a custom ship's params: unknown keys, catalog ranges, hull sanity, and every
    superstructure tier inside the hull and resting on the one below."""
    errs: list[str] = []
    if not isinstance(params, dict):
        return ["spec.params must be an object"]
    if known is not None:
        unknown = sorted(set(params) - known)
        if unknown:
            errs.append(f"unknown params {unknown}; known: {sorted(known)}. Anything else is a detail for "
                        "the summary (added as a custom op after approval)")
    for k, v in params.items():
        _check_range(f"params.{k}", v, _doc(docs, k), errs)
        if k.endswith("_color") and v is not None and not _rgb(v):
            errs.append(f"params.{k} must be [r,g,b] with each value 0..1")
    if not _doc(docs, "bow_form").get("options") and "bow_form" in params and params["bow_form"] not in BOW_FORMS:
        errs.append(f"params.bow_form must be one of {BOW_FORMS}")
    if not _doc(docs, "stern_form").get("options") and "stern_form" in params and params["stern_form"] not in STERN_FORMS:
        errs.append(f"params.stern_form must be one of {STERN_FORMS}")

    missing = [k for k in HULL_KEYS if not _num(params.get(k)) or params[k] <= 0]
    if missing:
        errs.append(f"the hull needs positive numbers for {missing} (metres)")
        return errs  # the fit checks below need the hull
    loa, beam, depth, draught = (float(params[k]) for k in HULL_KEYS)
    if beam > loa / 1.5 + TOL:  # the custom builder's own limit
        errs.append(f"beam_m {beam} must be at most loa_m / 1.5 ({loa / 1.5:.2f} m)")
    if draught >= depth:
        errs.append(f"draught_m {draught} must be less than depth_m {depth} (the deck would be under water)")

    for key, tiers in params.items():
        if key != "tiers":
            continue
        if not isinstance(tiers, list) or not all(isinstance(t, dict) for t in tiers):
            errs.append("params.tiers must be a list of tier objects with x_aft_m, x_fwd_m, width_m, height_m")
            continue
        fdocs = _field_docs(_doc(docs, key))
        below: dict | None = None
        for i, t in enumerate(tiers):
            w = f"params.{key}[{i}]"
            nums = {f: t.get(f) for f in ("x_aft_m", "x_fwd_m", "width_m", "height_m")}
            bad = [f for f, v in nums.items() if not _num(v)]
            if bad:
                errs.append(f"{w} needs numbers for {bad}")
                below = None
                continue
            aft, fwd, width, height = (float(nums[f]) for f in ("x_aft_m", "x_fwd_m", "width_m", "height_m"))
            extra = sorted(set(t) - TIER_FIELDS)
            if extra:
                errs.append(f"{w} has unknown fields {extra}; a tier has {sorted(TIER_FIELDS)}")
            if fwd - aft < 0.4:
                errs.append(f"{w}: x_aft_m {aft} must be at least 0.4 m aft of x_fwd_m {fwd}")
            if aft < -loa / 2 - TOL or fwd > loa / 2 + TOL:
                errs.append(f"{w} runs from x={aft} to x={fwd} m but the hull only spans "
                            f"{-loa / 2:g}..{loa / 2:g} m (origin amidships, +X bow)")
            if width < 0.2 or width > beam + TOL:
                errs.append(f"{w}: width_m {width} must be at least 0.2 and at most the beam {beam} m")
            if not 0.2 <= height <= 12:
                errs.append(f"{w}: height_m {height} must be 0.2..12 m")
            for f, v in t.items():
                _check_range(f"{w}.{f}", v, fdocs.get(f) if isinstance(fdocs.get(f), dict) else {}, errs)
            setback = t.get("setback_m", 0)
            if not _num(setback) or setback < -1:
                errs.append(f"{w}: setback_m must be a number of metres, -1 or more")
            elif not 0.2 <= width - 2 * setback <= beam + TOL:
                errs.append(f"{w}: its roof (width_m - 2 x setback_m = {width - 2 * setback:g} m) must be "
                            f"0.2 m to the beam {beam} m wide")
            if "rake_deg" in t and (not _num(t["rake_deg"]) or not -30 <= t["rake_deg"] <= 75):
                errs.append(f"{w}: rake_deg must be a number of degrees between -30 and 75")
            if "color" in t and t["color"] is not None and not _rgb(t["color"]):
                errs.append(f"{w}: color must be [r,g,b] with each value 0..1")
            if below is not None:
                # Overhangs are allowed (a bridge deck over the side decks, a sun deck over the
                # cockpit); a tier with nothing under it is not.
                if fwd <= below["x_aft_m"] or aft >= below["x_fwd_m"]:
                    errs.append(f"{w} does not sit on the tier below it (x {aft}..{fwd} vs "
                                f"{below['x_aft_m']}..{below['x_fwd_m']}); tiers stack from the deck up")
            below = {"x_aft_m": aft, "x_fwd_m": fwd}

    for key in ("mast_x_m", "arch_x_m", "funnel_x_m", "helm_x_m"):  # positions along the hull
        v = params.get(key)
        if _num(v) and abs(v) > loa / 2 + TOL:
            errs.append(f"params.{key} {v} is off the hull ({-loa / 2:g}..{loa / 2:g} m)")
    return errs


def plan_spec(proposed: Any, current: dict, default: dict | None, docs: dict) -> tuple[dict, list[str]]:
    """The full spec a plan would build: the proposal's params over the catalog default (or the
    current spec when there is no catalog), with the project's own schema/type/id/name kept."""
    if not isinstance(proposed, dict):
        return {}, ["spec must be an object"]
    errs: list[str] = []
    extra = sorted(set(proposed) - PLAN_SPEC_KEYS)
    if extra:
        errs.append(f"unknown spec fields {extra}; a spec has {sorted(PLAN_SPEC_KEYS)}")
    if proposed.get("custom_ops"):
        errs.append("leave custom_ops empty: list each detail in the summary; they are added as custom ops "
                    "after the user approves the plan")
    base = copy.deepcopy(default if default else current)
    for k in ("params", "overrides", "joint_limits", "meta"):
        if k in proposed and proposed[k] is not None and not isinstance(proposed[k], dict):
            errs.append(f"spec.{k} must be an object")
    if errs:
        return {}, errs
    spec = deep_merge(base, {k: proposed[k] for k in ("params", "overrides", "joint_limits", "meta")
                             if isinstance(proposed.get(k), dict)})
    for k in ("schema", "id", "name"):
        if k in current:
            spec[k] = current[k]
    spec["schema"] = "me3d.ship_spec/1"
    spec["type"] = "custom"
    spec["custom_ops"] = []
    for k, empty in (("params", {}), ("overrides", {}), ("joint_limits", {})):
        spec.setdefault(k, empty)
    known = None
    if default:
        known = set((default.get("params") or {})) | set(docs if isinstance(docs, dict) else [])
    errs += validate_custom_params(spec["params"], docs or {}, known)
    return spec, errs


def check_summary(summary: Any) -> list[str]:
    if not isinstance(summary, list) or not summary:
        return ["summary must be a non-empty list of plain-English lines"]
    if not all(isinstance(s, str) and s.strip() for s in summary):
        return ["every summary line must be non-empty text"]
    if len(summary) > 40:
        return ["keep the summary to 40 lines or fewer"]
    return []


def approve_refusal(project: dict) -> str | None:
    """Why this project's plan cannot be approved now (a sentence for the user), or None."""
    plan = project.get("design_plan")
    if not plan:
        return "There is no design plan yet. Ask the AI in Research to draft one."
    status = plan.get("status")
    if status == "approved":
        return "This design plan is already approved."
    if status == "stale":
        return "The research changed after this plan was approved. Ask the AI in Research to redraft the plan."
    if plan.get("research_rev") != project.get("research_rev", 0):
        return ("The research changed after this plan was drafted. Ask the AI in Research to redraft the plan, "
                "then approve it.")
    return None


def needs_plan(project: dict) -> str | None:
    """The sentence that refuses a build of a custom ship whose FIRST build Lester has not approved
    yet, else None. Lester's rule (29 Oct, via the lead): only the first build needs his approval.
    Once an approved plan has been built (`plan_built`), build chat, Inspect and pins stay open even
    when the plan later goes stale; a stale plan only offers a rebuild through redraft + approve."""
    if project.get("kind") != "ship" or project.get("base") != "custom":
        return None
    plan = project.get("design_plan") or {}
    if project.get("plan_built") or plan.get("status") == "approved":
        return None
    if not plan:
        return ("This ship is built from a design plan, and there is none yet. Go to Research and ask the AI "
                "to draft one; approving it builds the ship.")
    return "This ship is built from its design plan. Go to Research and approve the plan first; that builds the ship."


def on_research_saved(project: dict) -> None:
    """Every research save bumps the revision; an approved plan no longer matches it."""
    project["research_rev"] = int(project.get("research_rev") or 0) + 1
    plan = project.get("design_plan")
    if isinstance(plan, dict) and plan.get("status") == "approved":
        plan["status"] = "stale"
