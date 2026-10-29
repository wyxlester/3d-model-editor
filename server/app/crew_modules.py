"""Crew modules (contract §6 "Modules"): which spec keys each of the nine modules owns, what a
change between two specs touched, and the cross-module rules the screen and the AI share.

This is the guard behind "describe one module without resetting the entire human": a scoped AI
turn may only produce changes whose owner is its module. A wrong entry here silently lets the AI
reset something, so everything is table-driven and unit-tested (server/tests/test_crew_modules.py).

Pure Python, no I/O. Specs are never mutated.
"""
from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from typing import Any

MODULES = ("gender", "skin", "hair", "shirt", "pants", "outfit", "shoes", "equipment", "accessories")
# Old module ids still accepted on POST /chat (older clients, saved messages). Lester, 29 Oct:
# "combine equipment and equipment placement, it is a duplicate. when user describe a equipment,
# user will also need to describe how the human is holding the equipment" — so where an item sits
# (its `place`) is part of the equipment module, not a module of its own.
MODULE_ALIASES = {"placement": "equipment"}

# (section, key) -> module, for the dict sections body and clothes.
KEY_OWNER: dict[tuple[str, str], str] = {
    ("body", "gender"): "gender", ("body", "height_cm"): "gender", ("body", "weight"): "gender",
    ("body", "muscle"): "gender", ("body", "proportions"): "gender", ("body", "age_years"): "gender",
    ("body", "skin"): "skin",
    ("body", "hair"): "hair", ("body", "hair_color"): "hair",
    ("clothes", "shirt"): "shirt",
    ("clothes", "pants"): "pants",
    ("clothes", "suit"): "outfit", ("clothes", "suit_color"): "outfit",
    ("clothes", "shoes"): "shoes", ("clothes", "shoes_color"): "shoes",
    ("clothes", "hat"): "accessories", ("clothes", "hat_color"): "accessories",
}
# Whole top-level lists owned by one module. `equipment` and `custom_items` are split per entry.
LIST_OWNER = {"accessories": "accessories"}

PLACES = ("right_hand", "left_hand", "chest", "back", "hip", "head", "both_hands")
# both_hands is a two-handed ready carry (long items: a rifle, a boat hook); it needs both hands,
# so nothing else may be held in one (same rule as blender/crew/spec.py hand_conflicts).
HAND_PLACES = ("right_hand", "left_hand", "both_hands")
# Where an equipment entry without a place is drawn (blender/crew/spec.py DEFAULT_PLACE and
# CUSTOM_DEFAULT_PLACE): binoculars on the chest, the radio on the hip, described items in the right hand.
DEFAULT_PLACES = {"binoculars": "chest", "radio": "hip"}
CUSTOM_DEFAULT_PLACE = "right_hand"
# Worn catalog equipment with one fixed spot: no `place` (contract §6 "Placement").
FIXED_PLACE_EQUIPMENT = ("life_vest", "helmet")
SHIRT_STYLES = ("tshirt", "singlet", "long_sleeve", "polo")
PANTS_STYLES = ("shorts", "trousers", "cargo")
ACCESSORIES = ("cap", "sunglasses", "ear_defenders", "gloves")

# Modules a described (custom) item can belong to.
CUSTOM_ITEM_MODULES = ("hair", "shirt", "pants", "shoes", "equipment", "accessories")
# One-slot modules: a described item replaces the preset, and describing again replaces the
# earlier description. The value is the preset key and what it is set to when a described item
# takes over (hair "none" is bald; a missing hair key would bring the default style back).
SINGLE_SLOT: dict[str, tuple[str, str, Any]] = {
    "hair": ("body", "hair", "none"),
    "shirt": ("clothes", "shirt", None),
    "pants": ("clothes", "pants", None),
    "shoes": ("clothes", "shoes", None),
}
# Where several described items coexist (a torch and a clipboard; gloves and sunglasses).
MULTI_ITEM_MODULES = ("equipment", "accessories")

MODULE_DOCS = {
    "gender": "body.gender, height_cm, weight, muscle, proportions, age_years (set_body)",
    "skin": "body.skin (set_body)",
    "hair": "body.hair, body.hair_color (set_body); described hair: add_custom_item module 'hair'",
    "shirt": "clothes.shirt {style, color} or null (set_clothes); described: add_custom_item module 'shirt'",
    "pants": "clothes.pants {style, color} or null (set_clothes); described: add_custom_item module 'pants'",
    "outfit": "clothes.suit, clothes.suit_color (set_clothes)",
    "shoes": "clothes.shoes, clothes.shoes_color (set_clothes); described: add_custom_item module 'shoes'",
    "equipment": "the equipment list: names, colours and where each is held or carried (set_equipment, "
                 "set_place); described: add_custom_item module 'equipment' with its place",
    "accessories": "accessories list (set_accessories), clothes.hat, clothes.hat_color (set_clothes); "
                   "described: add_custom_item module 'accessories'",
}

_MISSING = object()


class ModuleRuleError(ValueError):
    """A change asks for two things the module rules say cannot both hold (outfit and shirt)."""


@dataclass(frozen=True)
class Change:
    path: str              # e.g. "body.hair", "equipment[radio].place", "custom_items[item_2]"
    module: str | None     # owner, or None when no module owns it (animations, name, ...)
    kind: str              # "set" (value changed), "added", "removed"
    new: Any = None        # new value for "set" changes (None when removed or set to null)


# ---- helpers ------------------------------------------------------------------------------

def item_module(item: Any) -> str:
    """The module a custom item belongs to: its tag, else inferred (equipment if it makes
    Equipment__ objects; pants if it is cut from the legs and not the torso; else shirt)."""
    if not isinstance(item, dict):
        return "shirt"
    tag = item.get("module")
    if isinstance(tag, str) and tag:
        return tag
    code = str(item.get("code") or "")
    if re.search(r"Equipment__", code):
        return "equipment"
    # A garment cut from the legs and not the torso is trousers (same rule as blender/crew/spec.py).
    if re.search(r"['\"](thighs|shins)['\"]", code) and not re.search(r"['\"]torso['\"]", code):
        return "pants"
    return "shirt"


def _entry(e: Any) -> dict:
    """An equipment/accessory entry as a dict without null fields ("radio" == {"name": "radio"})."""
    if isinstance(e, str):
        return {"name": e}
    if isinstance(e, dict):
        return {k: v for k, v in e.items() if v is not None}
    return {"name": json.dumps(e, sort_keys=True, default=str)}


def _canon(v: Any) -> str:
    return json.dumps(v, sort_keys=True, default=str)


def _keyed(entries: list, key_of) -> dict[str, Any]:
    """Index entries by key, numbering repeats (radio, radio#2) so duplicates still match."""
    out: dict[str, Any] = {}
    seen: dict[str, int] = {}
    for i, e in enumerate(entries):
        k = key_of(i, e)
        seen[k] = seen.get(k, 0) + 1
        out[k if seen[k] == 1 else f"{k}#{seen[k]}"] = e
    return out


def _as_list(v: Any) -> list:
    return v if isinstance(v, list) else ([] if v is None else [v])


# ---- what changed -------------------------------------------------------------------------

def changes(old: dict, new: dict) -> list[Change]:
    """Every difference between two crew specs, each attributed to its owning module (or None).
    Equipment order is not a change; everything else that differs is reported."""
    old, new = old or {}, new or {}
    out: list[Change] = []
    for key in sorted(set(old) | set(new), key=str):
        a, b = old.get(key, _MISSING), new.get(key, _MISSING)
        if key in ("body", "clothes") and isinstance(a if a is not _MISSING else {}, dict) \
                and isinstance(b if b is not _MISSING else {}, dict):
            out += _section_changes(key, {} if a is _MISSING else a, {} if b is _MISSING else b)
        elif key == "equipment":
            out += _equipment_changes(_as_list(None if a is _MISSING else a), _as_list(None if b is _MISSING else b))
        elif key == "custom_items":
            out += _item_changes(_as_list(None if a is _MISSING else a), _as_list(None if b is _MISSING else b))
        elif key in LIST_OWNER:
            na = [_entry(e) for e in _as_list(None if a is _MISSING else a)]
            nb = [_entry(e) for e in _as_list(None if b is _MISSING else b)]
            if na != nb:
                out.append(Change(key, LIST_OWNER[key], "set", None if b is _MISSING else b))
        elif a != b:
            kind = "added" if a is _MISSING else "removed" if b is _MISSING else "set"
            out.append(Change(key, None, kind, None if b is _MISSING else b))
    return out


def _section_changes(section: str, a: dict, b: dict) -> list[Change]:
    out = []
    for k in sorted(set(a) | set(b), key=str):
        va, vb = a.get(k, _MISSING), b.get(k, _MISSING)
        if va == vb:
            continue
        # A missing key and an explicit null differ: missing means "builder default", null "empty".
        kind = "added" if va is _MISSING else "removed" if vb is _MISSING else "set"
        out.append(Change(f"{section}.{k}", KEY_OWNER.get((section, k)), kind,
                          None if vb is _MISSING else vb))
    return out


def _equipment_changes(a: list, b: list) -> list[Change]:
    ka = _keyed([_entry(e) for e in a], lambda i, e: str(e.get("name")))
    kb = _keyed([_entry(e) for e in b], lambda i, e: str(e.get("name")))
    out = []
    for k in sorted(set(ka) | set(kb)):
        ea, eb = ka.get(k), kb.get(k)
        path = f"equipment[{k}]"
        if ea is None or eb is None:
            # Adding an item includes choosing where it goes; removing it removes its place too.
            out.append(Change(path, "equipment", "added" if ea is None else "removed", eb))
            continue
        content_a = {x: v for x, v in ea.items() if x != "place"}
        content_b = {x: v for x, v in eb.items() if x != "place"}
        if content_a != content_b:
            out.append(Change(path, "equipment", "set", eb))
        if ea.get("place") != eb.get("place"):
            out.append(Change(f"{path}.place", "equipment", "set", eb.get("place")))
    return out


def _item_changes(a: list, b: list) -> list[Change]:
    def key(i, it):
        return str(it.get("id")) if isinstance(it, dict) and it.get("id") else f"#{i}"
    ka, kb = _keyed(a, key), _keyed(b, key)
    out = []
    for k in sorted(set(ka) | set(kb)):
        ia, ib = ka.get(k), kb.get(k)
        path = f"custom_items[{k}]"
        if ia is None:
            out.append(Change(path, item_module(ib), "added", ib))
            continue
        if ib is None:
            out.append(Change(path, item_module(ia), "removed"))
            continue
        ma, mb = item_module(ia), item_module(ib)
        if ma != mb:  # retagged: it leaves one module and joins another
            out.append(Change(path, ma, "removed"))
            out.append(Change(path, mb, "added", ib))
            continue
        strip = lambda it: {x: v for x, v in it.items() if x not in ("place", "module")} \
            if isinstance(it, dict) else it  # noqa: E731
        if _canon(strip(ia)) != _canon(strip(ib)):
            out.append(Change(path, mb, "set", ib))
        pa = ia.get("place") if isinstance(ia, dict) else None
        pb = ib.get("place") if isinstance(ib, dict) else None
        if pa != pb:
            out.append(Change(f"{path}.place", mb, "set", pb))
    return out


def module_id(name: Any) -> str | None:
    """A module id as sent by a client, aliases resolved (placement -> equipment); None if unknown."""
    if not isinstance(name, str):
        return None
    name = MODULE_ALIASES.get(name, name)
    return name if name in MODULES else None


def changed_modules(old: dict, new: dict) -> set[str]:
    """The modules a change between two specs touched (keys no module owns are not listed)."""
    return {c.module for c in changes(old, new) if c.module in MODULES}


def ordered(modules) -> list[str]:
    return [m for m in MODULES if m in set(modules)]


def outside(old: dict, new: dict, allowed_module: str) -> list[str]:
    """Paths changed outside `allowed_module`. Empty means the change stays inside the module."""
    return [c.path for c in outside_changes(old, new, allowed_module)]


def outside_changes(old: dict, new: dict, allowed_module: str) -> list[Change]:
    """The changes outside `allowed_module` (see `outside`).

    The only cross-module changes allowed are the ones the outfit rule itself makes: a shirt or
    pants change may clear the suit (and fill the other half with its plain default), and an outfit choice may clear shirt and pants and remove
    their described items. They count only when the module really changed in that direction."""
    allowed_module = module_id(allowed_module) or allowed_module
    cs = changes(old, new)
    own = [c for c in cs if c.module == allowed_module]
    new_suit = ((new or {}).get("clothes") or {}).get("suit")

    def side_effect(c: Change) -> bool:
        # Always clothed: with no outfit, a bare top or bare legs get the plain default, whichever
        # module's change exposed them.
        if (c.path in ("clothes.shirt", "clothes.pants") and c.kind in ("added", "set")
                and new_suit is None and c.new == FILL_IN[c.path.split(".")[1]]):
            return True
        if allowed_module in ("shirt", "pants") and own:
            return c.path == "clothes.suit" and c.kind == "set" and c.new is None
        if allowed_module == "outfit" and new_suit is not None:
            if c.path in ("clothes.shirt", "clothes.pants") and c.kind == "set" and c.new is None:
                return True
            return c.module in ("shirt", "pants") and c.kind == "removed" and c.path.startswith("custom_items[")
        return False

    return [c for c in cs if c.module != allowed_module and not side_effect(c)]


# ---- the rules the screen and the AI share --------------------------------------------------

# What fills the other half when a shirt or trousers replace a full outfit.
FILL_IN = {"shirt": {"style": "tshirt", "color": [0.85, 0.85, 0.82]},
           "pants": {"style": "trousers", "color": [0.05, 0.07, 0.15]}}
FILL_IN_NOTE = {"shirt": "A plain white T-shirt was added so he is not left without a top.",
                "pants": "Plain navy trousers were added so he is not left without trousers."}


def apply_module_rules(old: dict, new: dict) -> tuple[dict, list[str]]:
    """Make `new` obey the module rules given what changed since `old`. Returns (spec, notes),
    notes saying in plain words what else the rules changed. Raises ModuleRuleError when one
    change chooses both sides of a rule.

    - Outfit vs shirt/pants: choosing an outfit (a non-null suit) sets shirt and pants to null and
      removes shirt/pants described items; setting a shirt or pants, preset or described, sets the
      suit to null (a missing suit would bring the builder's default suit back, so null is written).
    - A described item replaces its module's preset (hair, shirt, pants, shoes): the preset key is
      emptied. Choosing a preset again removes that module's described items.
    """
    cs = changes(old, new)
    out = copy.deepcopy(new)
    notes: list[str] = []

    def preset_set(module: str) -> bool:
        section, key, empty = SINGLE_SLOT[module]
        return any(c.path == f"{section}.{key}" and c.kind != "removed" and c.new != empty for c in cs)

    def described_set(module: str) -> bool:
        return any(c.module == module and c.path.startswith("custom_items[") and c.kind in ("added", "set")
                   and not c.path.endswith(".place") for c in cs)

    clothes_new = out.get("clothes") if isinstance(out.get("clothes"), dict) else {}
    outfit_set = any(c.path == "clothes.suit" and c.kind != "removed" and c.new is not None for c in cs)
    top_set = {m: preset_set(m) or described_set(m) for m in ("shirt", "pants")}
    if outfit_set and any(top_set.values()):
        raise ModuleRuleError("An outfit fills both shirt and pants, so it cannot be chosen together with a "
                              "shirt or pants in one change. Choose one.")
    for m in SINGLE_SLOT:
        if preset_set(m) and described_set(m):
            raise ModuleRuleError(f"{m}: choose either a preset or a described item, not both in one change.")

    def remove_items(modules: tuple[str, ...]) -> list[str]:
        items = out.get("custom_items")
        if not isinstance(items, list):
            return []
        gone = [it for it in items if item_module(it) in modules]
        if gone:
            out["custom_items"] = [it for it in items if item_module(it) not in modules]
        return [str(it.get("id")) if isinstance(it, dict) else "?" for it in gone]

    if outfit_set:
        cleared = []
        for k in ("shirt", "pants"):
            if clothes_new.get(k) is not None:
                out["clothes"][k] = None
                cleared.append(k)
        gone = remove_items(("shirt", "pants"))
        if cleared or gone:
            notes.append("The outfit replaces the shirt and trousers: " +
                         ", ".join(cleared + [f"described item {g}" for g in gone]) + " removed.")
    elif any(top_set.values()):
        if "suit" not in clothes_new or clothes_new.get("suit") is not None:
            out.setdefault("clothes", {})
            if not isinstance(out["clothes"], dict):
                out["clothes"] = {}
            out["clothes"]["suit"] = None
            notes.append("The outfit was removed, because a shirt or trousers now show instead.")

    for m, (section, key, empty) in SINGLE_SLOT.items():
        sec = out.get(section) if isinstance(out.get(section), dict) else None
        if described_set(m):
            if sec is None:
                out[section] = sec = {}
            if key not in sec or sec[key] != empty:
                sec[key] = empty
                notes.append(f"The {m} preset was cleared: the described {m} replaces it.")
        elif preset_set(m):
            gone = remove_items((m,))
            if gone:
                notes.append(f"Described {m} item(s) {', '.join(gone)} removed: the chosen preset replaces them.")
    notes += ensure_clothed(out)
    return out, notes


def ensure_clothed(spec: dict) -> list[str]:
    """Lester, 29 Oct: "human models should always be clothed and have a default shirt and pants".
    With no full outfit (suit null), a top or trousers that has neither a preset nor a described
    item gets the plain default. A missing suit key means the catalog's default suit, so clothed.
    Changes `spec` in place; returns the notes saying what was added."""
    clothes = spec.get("clothes")
    if not isinstance(clothes, dict) or "suit" not in clothes or clothes.get("suit") is not None:
        return []
    items = spec.get("custom_items") if isinstance(spec.get("custom_items"), list) else []
    notes = []
    for m in ("shirt", "pants"):
        if clothes.get(m) is None and not any(item_module(it) == m for it in items):
            clothes[m] = copy.deepcopy(FILL_IN[m])
            notes.append(FILL_IN_NOTE[m])
    return notes


def hand_conflicts(spec: dict) -> list[str]:
    """Why the spec's hands cannot do what it asks: two items in both hands, or an item in both
    hands and another held in one. Empty when there is no both_hands item or nothing clashes."""
    held: list[tuple[str, str]] = []
    for e in spec.get("equipment") or []:
        name = e if isinstance(e, str) else e.get("name") if isinstance(e, dict) else None
        if name is None or name in FIXED_PLACE_EQUIPMENT:
            continue
        place = (e.get("place") if isinstance(e, dict) else None) or DEFAULT_PLACES.get(name, CUSTOM_DEFAULT_PLACE)
        held.append((str(name), place))
    for it in spec.get("custom_items") or []:
        if isinstance(it, dict) and item_module(it) == "equipment":
            label = f"{it.get('id')} ({it['description']})" if it.get("description") else str(it.get("id"))
            held.append((label, it.get("place") or CUSTOM_DEFAULT_PLACE))
    both = [n for n, p in held if p == "both_hands"]
    if not both:
        return []
    if len(both) > 1:
        return [f"Only one item can be carried in both hands; {', '.join(both)} are all in both hands."]
    others = [f"{n} ({p})" for n, p in held if p in ("right_hand", "left_hand")]
    if others:
        return [f"{both[0]} is carried in both hands, so nothing else can be held in a hand: move "
                f"{', '.join(others)} to chest, back, hip or head first."]
    return []
