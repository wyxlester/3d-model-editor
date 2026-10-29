"""Crew spec (me3d.crew_spec/1, contract section 6): defaults, validation and the mapping from spec
fields to MPFB2 inputs. Pure Python, no bpy, so it is unit-tested outside Blender."""

import copy
import re

SCHEMA = "me3d.crew_spec/1"

ANIMATIONS = ["idle", "walk", "brace", "hands_up", "squat"]
EQUIPMENT = ["life_vest", "helmet", "binoculars", "radio"]
EQUIPMENT_COLORS = {
    "orange": [0.95, 0.35, 0.03],
    "navy": [0.03, 0.06, 0.20],
    "yellow": [0.95, 0.75, 0.05],
    "white": [0.90, 0.90, 0.88],
    "red": [0.70, 0.05, 0.04],
    "black": [0.03, 0.03, 0.03],
}
EQUIPMENT_DEFAULT_COLOR = {"life_vest": "orange", "helmet": "white", "binoculars": "black", "radio": "black"}
SKINS = ["light", "medium", "dark"]
GENDERS = ["male", "female"]

# MPFB/MakeHuman asset names in the CC0 system pack (vendor/makehuman/PROVENANCE.md).
SUITS = ["male_worksuit01", "male_casualsuit01", "male_casualsuit02", "male_casualsuit03",
         "male_casualsuit04", "male_casualsuit05", "male_casualsuit06", "male_elegantsuit01",
         "female_casualsuit01", "female_casualsuit02", "female_elegantsuit01", "female_sportsuit01"]
SHOES = ["shoes01", "shoes02", "shoes03", "shoes04", "shoes05", "shoes06"]
HATS = ["fedora01", "fedora_cocked"]
HAIRS = ["short01", "short02", "short03", "short04", "afro01", "bob01", "bob02", "braid01",
         "long01", "ponytail01"]

DEFAULT_SPEC = {
    "schema": SCHEMA,
    "id": "crew_male_default",
    "name": "Default male crew",
    "body": {"gender": "male", "height_cm": 178, "weight": 0.5, "muscle": 0.5, "age_years": 30,
             "skin": "medium", "hair": None, "hair_color": None},
    "clothes": {"suit": "male_worksuit01", "shoes": "shoes01", "hat": None, "suit_color": None,
                "shoes_color": None, "hat_color": None},
    "equipment": ["life_vest"],
    "animations": list(ANIMATIONS),
    "custom_items": [],
}

# Same code rules as ship custom ops (server/app/agent.py FORBIDDEN_CODE): geometry only. The
# server checks first; this is the second check, on the machine that actually runs the code.
FORBIDDEN_CODE = [r"\bimport\s+(os|sys|subprocess|socket|shutil|urllib|requests|http|ctypes|pathlib)\b",
                  r"\bfrom\s+(os|sys|subprocess|socket|shutil|urllib|requests|http|ctypes|pathlib)\b",
                  r"__import__", r"\bopen\s*\(", r"\beval\s*\(", r"\bexec\s*\(",
                  r"bpy\.ops\.wm\.(quit|save|open)", r"bpy\.ops\.export", r"\bcompile\s*\("]

DEFAULT_HAIR = {"male": "short02", "female": "bob02"}
SKIN_RACE = {"light": "caucasian", "medium": "asian", "dark": "african"}

# MakeHuman's age macro: 0 -> 1 year, 0.5 -> 25 years, 1 -> 90 years, linear in between.
_MIN_AGE, _MID_AGE, _MAX_AGE = 1.0, 25.0, 90.0
_ID = re.compile(r"^[A-Za-z0-9_]+$")


def deep_merge(base, patch):
    out = copy.deepcopy(base)
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def age_years_to_macro(years):
    y = min(max(float(years), _MIN_AGE), _MAX_AGE)
    if y <= _MID_AGE:
        return 0.5 * (y - _MIN_AGE) / (_MID_AGE - _MIN_AGE)
    return 0.5 + 0.5 * (y - _MID_AGE) / (_MAX_AGE - _MID_AGE)


def skin_asset(skin, gender, age_years):
    """The system-pack skin for a skin tone, gender and age: young below 40, middleage to 60, old after."""
    stage = "young" if age_years < 40 else ("middleage" if age_years < 60 else "old")
    name = "%s_%s_%s" % (stage, SKIN_RACE[skin], gender)
    return name + "/" + name + ".mhmat"


def race_weights(skin):
    """Lean the body shape toward the race that matches the skin tone, without going to an extreme."""
    w = {"asian": 0.1, "caucasian": 0.1, "african": 0.1}
    w[SKIN_RACE[skin]] = 0.8
    return w


def _clamp01(name, v, errors):
    try:
        f = float(v)
    except (TypeError, ValueError):
        errors.append("%s must be a number between 0 and 1, got %r" % (name, v))
        return 0.5
    if not 0.0 <= f <= 1.0:
        errors.append("%s must be between 0 and 1, got %r" % (name, v))
        return min(max(f, 0.0), 1.0)
    return f


def _color(name, v, errors):
    if v is None:
        return None
    if isinstance(v, str) and v in EQUIPMENT_COLORS:
        return list(EQUIPMENT_COLORS[v])
    if isinstance(v, (list, tuple)) and len(v) in (3, 4) and all(isinstance(c, (int, float)) for c in v):
        return [min(max(float(c), 0.0), 1.0) for c in v[:3]]
    errors.append("%s must be [r,g,b] in 0..1 or one of %s, got %r" % (name, sorted(EQUIPMENT_COLORS), v))
    return None


def _strict_color(name, v, errors):
    """A colour for the made-by-description fields: null or [r,g,b] (linear, as a Blender Base
    Color) with every channel a number in 0..1. Out-of-range is an error, not clamped."""
    if v is None:
        return None
    if (isinstance(v, (list, tuple)) and len(v) == 3
            and all(isinstance(c, (int, float)) and not isinstance(c, bool) for c in v)):
        if all(0.0 <= float(c) <= 1.0 for c in v):
            return [float(c) for c in v]
    errors.append("%s must be null or [r,g,b] with each value between 0 and 1, got %r" % (name, v))
    return None


def custom_items(items, errors):
    """Validate custom_items: a list of {"id", "description", "code"} with unique [A-Za-z0-9_] ids,
    code that compiles and uses none of the forbidden calls. Returns the cleaned list."""
    if items is None:
        return []
    if not isinstance(items, list):
        errors.append("custom_items must be a list, got %r" % type(items).__name__)
        return []
    out, ids = [], set()
    for k, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append("custom_items[%d] must be {\"id\", \"description\", \"code\"}, got %r" % (k, item))
            continue
        iid = item.get("id")
        if not isinstance(iid, str) or not _ID.match(iid):
            errors.append("custom_items[%d].id must match [A-Za-z0-9_]+, got %r" % (k, iid))
            continue
        if iid in ids:
            errors.append("custom_items id %r is used twice; ids must be unique" % iid)
            continue
        ids.add(iid)
        desc = item.get("description", "")
        if not isinstance(desc, str):
            errors.append("custom_items %s: description must be text, got %r" % (iid, desc))
            desc = str(desc)
        code = item.get("code")
        if not isinstance(code, str) or not code.strip():
            errors.append("custom_items %s: code must be non-empty Python text" % iid)
            continue
        bad = [p for p in FORBIDDEN_CODE if re.search(p, code)]
        if bad:
            errors.append("custom_items %s: code uses a forbidden call (%s); geometry only" % (iid, bad[0]))
            continue
        try:
            compile(code, "<custom_item %s>" % iid, "exec")
        except SyntaxError as e:
            errors.append("custom_items %s (%s) failed at line %s: SyntaxError: %s" % (iid, desc, e.lineno, e.msg))
            continue
        out.append({"id": iid, "description": desc, "code": code})
    return out


def normalize(spec):
    """Merge a spec over the defaults and validate it. Returns (spec, errors, warnings).

    The returned spec is complete: every field the builder reads is present. Equipment entries are
    normalised to {"name", "color"} dicts."""
    errors, warnings = [], []
    spec = deep_merge(DEFAULT_SPEC, spec or {})
    if spec.get("schema") != SCHEMA:
        errors.append("schema must be %r, got %r" % (SCHEMA, spec.get("schema")))
    if not _ID.match(str(spec.get("id", ""))):
        errors.append("id must match [A-Za-z0-9_]+, got %r" % spec.get("id"))

    body = spec["body"]
    if body.get("gender") not in GENDERS:
        errors.append("body.gender must be one of %s, got %r" % (GENDERS, body.get("gender")))
        body["gender"] = "male"
    try:
        h = float(body.get("height_cm"))
        if not 140 <= h <= 210:
            errors.append("body.height_cm must be between 140 and 210, got %r" % h)
        body["height_cm"] = min(max(h, 140.0), 210.0)
    except (TypeError, ValueError):
        errors.append("body.height_cm must be a number, got %r" % body.get("height_cm"))
        body["height_cm"] = 178.0
    for k in ("weight", "muscle"):
        body[k] = _clamp01("body." + k, body.get(k), errors)
    body["proportions"] = _clamp01("body.proportions", body.get("proportions", 0.5), errors)
    try:
        a = float(body.get("age_years"))
        if not 18 <= a <= 80:
            errors.append("body.age_years must be between 18 and 80, got %r" % a)
        body["age_years"] = min(max(a, 18.0), 80.0)
    except (TypeError, ValueError):
        errors.append("body.age_years must be a number, got %r" % body.get("age_years"))
        body["age_years"] = 30.0
    if body.get("skin") not in SKINS:
        errors.append("body.skin must be one of %s, got %r" % (SKINS, body.get("skin")))
        body["skin"] = "medium"
    if body.get("hair") is None:
        body["hair"] = DEFAULT_HAIR[body["gender"]]
    elif body["hair"] not in HAIRS and body["hair"] != "none":
        errors.append("body.hair must be one of %s or \"none\", got %r" % (HAIRS, body["hair"]))
    body["hair_color"] = _strict_color("body.hair_color", body.get("hair_color"), errors)

    clothes = spec["clothes"]
    if clothes.get("suit") is not None and clothes["suit"] not in SUITS:
        errors.append("clothes.suit must be one of %s or null, got %r" % (SUITS, clothes["suit"]))
    if clothes.get("shoes") is not None and clothes["shoes"] not in SHOES:
        errors.append("clothes.shoes must be one of %s or null, got %r" % (SHOES, clothes["shoes"]))
    if clothes.get("hat") is not None and clothes["hat"] not in HATS:
        errors.append("clothes.hat must be one of %s or null, got %r" % (HATS, clothes["hat"]))
    clothes["suit_color"] = _color("clothes.suit_color", clothes.get("suit_color"), errors)
    for key in ("shoes_color", "hat_color"):
        clothes[key] = _strict_color("clothes." + key, clothes.get(key), errors)

    eq_out, seen = [], set()
    for item in spec.get("equipment") or []:
        if isinstance(item, str):
            item = {"name": item}
        if not isinstance(item, dict) or item.get("name") not in EQUIPMENT:
            errors.append("equipment entries must be one of %s (or {\"name\",\"color\"}), got %r" % (EQUIPMENT, item))
            continue
        if item["name"] in seen:
            warnings.append("equipment %r listed twice; using the first" % item["name"])
            continue
        seen.add(item["name"])
        color = _color("equipment.%s.color" % item["name"], item.get("color"), errors)
        eq_out.append({"name": item["name"],
                       "color": color or list(EQUIPMENT_COLORS[EQUIPMENT_DEFAULT_COLOR[item["name"]]])})
    spec["equipment"] = eq_out
    if "helmet" in seen and clothes.get("hat"):
        warnings.append("both a hat and a helmet were asked for; the hat is left off")
        clothes["hat"] = None

    anims = spec.get("animations") or []
    bad = [a for a in anims if a not in ANIMATIONS]
    if bad:
        errors.append("animations must be from %s, got unknown %s" % (ANIMATIONS, bad))
    spec["animations"] = [a for a in anims if a in ANIMATIONS]
    spec["custom_items"] = custom_items(spec.get("custom_items"), errors)
    return spec, errors, warnings


def macro_dict(spec, height_macro=0.5):
    """The MPFB phenotype dict for a normalised spec (height is solved separately)."""
    b = spec["body"]
    return {
        "gender": 1.0 if b["gender"] == "male" else 0.0,
        "age": age_years_to_macro(b["age_years"]),
        "muscle": b["muscle"],
        "weight": b["weight"],
        "proportions": b["proportions"],
        "height": height_macro,
        "cupsize": 0.5,
        "firmness": 0.5,
        "race": race_weights(b["skin"]),
    }


def solve_monotonic(f, target, lo=0.0, hi=1.0, tol=0.0005, max_iter=30):
    """Find x in [lo, hi] with f(x) == target for an increasing f, by bisection.
    Returns (x, f(x)); clamps to the end of the range when the target is out of reach."""
    flo, fhi = f(lo), f(hi)
    if target <= flo:
        return lo, flo
    if target >= fhi:
        return hi, fhi
    x, fx = lo, flo
    for _ in range(max_iter):
        x = 0.5 * (lo + hi)
        fx = f(x)
        if abs(fx - target) <= tol:
            break
        if fx < target:
            lo = x
        else:
            hi = x
    return x, fx


def part_label(path):
    """Contract section 2: '__' becomes ' › ' and '_' becomes a space."""
    return path.replace("__", " › ").replace("_", " ")
