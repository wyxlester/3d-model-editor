"""The crew module map (contract §6 "Modules") — the guard that stops a module edit resetting the
rest of the person. The expected owners below are written out from the contract table by hand,
not read from crew_modules, so a wrong entry in the map fails here."""
import copy

import pytest

from app.crew_modules import (MODULES, ModuleRuleError, apply_module_rules, changed_modules, item_module,
                              outside)

BASE = {
    "schema": "me3d.crew_spec/1", "id": "crew_x", "name": "Crew",
    "body": {"gender": "male", "height_cm": 178, "weight": 0.5, "muscle": 0.5, "proportions": 0.5,
             "age_years": 30, "skin": "medium", "hair": "short02", "hair_color": None},
    "clothes": {"suit": "male_worksuit01", "suit_color": None, "shoes": "shoes01", "shoes_color": None,
                "hat": None, "hat_color": None},
    "equipment": ["life_vest", {"name": "radio", "color": "navy", "place": "hip"}],
    "accessories": [{"name": "gloves", "color": [0.1, 0.1, 0.1]}],
    "animations": ["idle", "walk"],
    "custom_items": [
        {"id": "item_1", "module": "equipment", "place": "right_hand", "description": "torch",
         "code": "crew.object_from_bmesh('Equipment__Torch', bm, (1, 1, 0))"},
        {"id": "item_2", "module": "hair", "description": "mohawk", "code": "crew.garment('Clothes__Mohawk')"},
    ],
}

# Contract table, by hand: (section, key, new value) -> owning module.
CONTRACT_KEYS = [
    ("body", "gender", "female", "gender"),
    ("body", "height_cm", 190, "gender"),
    ("body", "weight", 0.8, "gender"),
    ("body", "muscle", 0.9, "gender"),
    ("body", "proportions", 0.2, "gender"),
    ("body", "age_years", 55, "gender"),
    ("body", "skin", "dark", "skin"),
    ("body", "hair", "afro01", "hair"),
    ("body", "hair_color", [0.85, 0.7, 0.4], "hair"),
    ("clothes", "shirt", {"style": "polo", "color": [1, 0, 0]}, "shirt"),
    ("clothes", "pants", {"style": "cargo", "color": [0, 0, 1]}, "pants"),
    ("clothes", "suit", "male_casualsuit02", "outfit"),
    ("clothes", "suit_color", [1, 0.45, 0.1], "outfit"),
    ("clothes", "shoes", None, "shoes"),
    ("clothes", "shoes_color", [0.1, 0.1, 0.1], "shoes"),
    ("clothes", "hat", "fedora01", "accessories"),
    ("clothes", "hat_color", [0.2, 0.2, 0.2], "accessories"),
]


def spec(**edits):
    s = copy.deepcopy(BASE)
    for k, v in edits.items():
        s[k] = v
    return s


@pytest.mark.parametrize("section,key,value,module", CONTRACT_KEYS)
def test_each_contract_key_belongs_to_exactly_its_module(section, key, value, module):
    new = copy.deepcopy(BASE)
    new[section][key] = value
    assert changed_modules(BASE, new) == {module}
    assert outside(BASE, new, module) == []
    for other in MODULES:
        if other != module:
            assert outside(BASE, new, other) == [f"{section}.{key}"], other


def test_every_module_has_at_least_one_owned_change():
    seen = {m for *_, m in CONTRACT_KEYS} | {"equipment", "placement"}
    assert seen == set(MODULES)


def test_missing_key_and_null_differ():
    """A missing suit means the builder's default suit; null means none. That is a change."""
    new = copy.deepcopy(BASE)
    del new["clothes"]["hat"]
    assert changed_modules(BASE, new) == {"accessories"}


def test_unchanged_spec_changes_nothing():
    assert changed_modules(BASE, copy.deepcopy(BASE)) == set()
    assert outside(BASE, copy.deepcopy(BASE), "shirt") == []


# ---- equipment and placement ---------------------------------------------------------------

def test_place_only_change_is_placement_not_equipment():
    new = spec(equipment=["life_vest", {"name": "radio", "color": "navy", "place": "chest"}])
    assert changed_modules(BASE, new) == {"placement"}
    assert outside(BASE, new, "placement") == []
    assert outside(BASE, new, "equipment") == ["equipment[radio].place"]


def test_colour_change_is_equipment_not_placement():
    new = spec(equipment=["life_vest", {"name": "radio", "color": "red", "place": "hip"}])
    assert changed_modules(BASE, new) == {"equipment"}
    assert outside(BASE, new, "placement") == ["equipment[radio]"]


def test_colour_and_place_together_are_both():
    new = spec(equipment=["life_vest", {"name": "radio", "color": "red", "place": "chest"}])
    assert changed_modules(BASE, new) == {"equipment", "placement"}


def test_adding_or_removing_equipment_is_equipment_even_with_a_place():
    added = spec(equipment=BASE["equipment"] + [{"name": "binoculars", "place": "left_hand"}])
    assert changed_modules(BASE, added) == {"equipment"}
    removed = spec(equipment=["life_vest"])
    assert changed_modules(BASE, removed) == {"equipment"}
    assert outside(BASE, removed, "placement") == ["equipment[radio]"]


def test_equipment_order_and_string_form_are_not_changes():
    new = spec(equipment=[{"name": "radio", "color": "navy", "place": "hip"}, {"name": "life_vest", "color": None}])
    assert changed_modules(BASE, new) == set()


def test_duplicate_equipment_names_match_by_occurrence():
    old = spec(equipment=[{"name": "radio", "place": "hip"}, {"name": "radio", "place": "chest"}])
    new = spec(equipment=[{"name": "radio", "place": "hip"}, {"name": "radio", "place": "back"}])
    assert changed_modules(old, new) == {"placement"}
    assert outside(old, new, "equipment") == ["equipment[radio#2].place"]


# ---- custom items ---------------------------------------------------------------------------

def test_custom_item_add_and_remove_count_for_its_module_tag():
    for module in ("hair", "shirt", "pants", "shoes", "equipment", "accessories"):
        item = {"id": "item_9", "module": module, "description": "x", "code": "x = 1"}
        new = spec(custom_items=BASE["custom_items"] + [item])
        assert changed_modules(BASE, new) == {module}
        assert changed_modules(new, BASE) == {module}
        assert outside(BASE, new, module) == []
    gone = spec(custom_items=BASE["custom_items"][:1])  # the hair item removed
    assert changed_modules(BASE, gone) == {"hair"}
    assert outside(BASE, gone, "equipment") == ["custom_items[item_2]"]


def test_untagged_items_are_inferred_per_contract():
    eq = {"id": "a", "description": "t", "code": "o = crew.object_from_bmesh('Equipment__Torch', bm, c)"}
    cl = {"id": "b", "description": "s", "code": "crew.garment('Clothes__Singlet', ['torso'], c)"}
    assert item_module(eq) == "equipment" and item_module(cl) == "shirt"
    assert changed_modules(spec(custom_items=[]), spec(custom_items=[eq])) == {"equipment"}
    assert changed_modules(spec(custom_items=[]), spec(custom_items=[cl])) == {"shirt"}


def test_tagging_an_item_with_its_inferred_module_is_not_a_change():
    old = spec(custom_items=[{"id": "a", "description": "s", "code": "crew.garment('Clothes__S')"}])
    new = spec(custom_items=[{"id": "a", "module": "shirt", "description": "s", "code": "crew.garment('Clothes__S')"}])
    assert changed_modules(old, new) == set()


def test_retagging_an_item_touches_both_modules():
    old = spec(custom_items=[{"id": "a", "module": "shirt", "description": "s", "code": "x"}])
    new = spec(custom_items=[{"id": "a", "module": "pants", "description": "s", "code": "x"}])
    assert changed_modules(old, new) == {"shirt", "pants"}


def test_custom_equipment_place_is_placement_and_code_is_equipment():
    items = copy.deepcopy(BASE["custom_items"])
    items[0]["place"] = "back"
    assert changed_modules(BASE, spec(custom_items=items)) == {"placement"}
    items = copy.deepcopy(BASE["custom_items"])
    items[0]["code"] += "\n# longer"
    assert changed_modules(BASE, spec(custom_items=items)) == {"equipment"}


def test_keys_no_module_owns_are_outside_every_module():
    new = spec(animations=["idle"], name="Renamed")
    assert changed_modules(BASE, new) == set()
    for m in MODULES:
        assert outside(BASE, new, m) == ["animations", "name"]


def test_accessories_list_is_accessories():
    new = spec(accessories=[{"name": "gloves", "color": [0.1, 0.1, 0.1]}, "sunglasses"])
    assert changed_modules(BASE, new) == {"accessories"}
    assert outside(BASE, new, "equipment") == ["accessories"]


# ---- the outfit side effects a scoped edit may make --------------------------------------------

def test_shirt_scope_may_clear_the_suit_only_with_a_shirt_change():
    new = copy.deepcopy(BASE)
    new["clothes"]["shirt"] = {"style": "tshirt", "color": [1, 1, 1]}
    new["clothes"]["suit"] = None
    assert outside(BASE, new, "shirt") == []
    only_suit = copy.deepcopy(BASE)
    only_suit["clothes"]["suit"] = None
    assert outside(BASE, only_suit, "shirt") == ["clothes.suit"]
    swapped = copy.deepcopy(new)
    swapped["clothes"]["suit"] = "male_casualsuit02"  # a different suit is never a side effect
    assert outside(BASE, swapped, "shirt") == ["clothes.suit"]


def test_outfit_scope_may_clear_shirt_pants_and_their_items():
    old = copy.deepcopy(BASE)
    old["clothes"].update(suit=None, shirt={"style": "polo"}, pants={"style": "shorts"})
    old["custom_items"].append({"id": "item_3", "module": "pants", "description": "x", "code": "x"})
    new = copy.deepcopy(old)
    new["clothes"].update(suit="male_worksuit01", shirt=None, pants=None)
    new["custom_items"] = [i for i in new["custom_items"] if i["id"] != "item_3"]
    assert outside(old, new, "outfit") == []
    # Removing hair while choosing an outfit is still outside.
    new["custom_items"] = [i for i in new["custom_items"] if i["id"] != "item_2"]
    assert outside(old, new, "outfit") == ["custom_items[item_2]"]


# ---- apply_module_rules ----------------------------------------------------------------------

def test_setting_a_shirt_clears_the_suit():
    new = copy.deepcopy(BASE)
    new["clothes"]["shirt"] = {"style": "singlet", "color": [1, 1, 1]}
    out, notes = apply_module_rules(BASE, new)
    assert out["clothes"]["suit"] is None and notes
    assert out["clothes"]["shirt"] == {"style": "singlet", "color": [1, 1, 1]}
    assert BASE["clothes"]["suit"] == "male_worksuit01"  # inputs untouched


def test_replacing_the_outfit_with_a_shirt_never_leaves_him_without_trousers():
    # First live run, 29 Oct: singlet over the work suit -> suit removed -> bare legs.
    new = copy.deepcopy(BASE)
    new["clothes"]["shirt"] = {"style": "singlet", "color": [1, 1, 1]}
    out, notes = apply_module_rules(BASE, new)
    assert out["clothes"]["pants"] == {"style": "trousers", "color": [0.05, 0.07, 0.15]}
    assert any("trousers" in n for n in notes)
    new = copy.deepcopy(BASE)
    new["clothes"]["pants"] = {"style": "shorts", "color": [0, 0, 0]}
    out, _ = apply_module_rules(BASE, new)
    assert out["clothes"]["shirt"]["style"] == "tshirt" and out["clothes"]["pants"]["style"] == "shorts"
    # a described pair of trousers already covers the legs: no fill-in on top of it
    new = copy.deepcopy(BASE)
    new["clothes"]["shirt"] = {"style": "singlet", "color": [1, 1, 1]}
    new["custom_items"].append({"id": "item_9", "module": "pants", "description": "loose trousers", "code": "x"})
    out, _ = apply_module_rules(BASE, new)
    assert out["clothes"].get("pants") is None
    # no outfit to take off: nothing is filled in
    old = copy.deepcopy(BASE)
    old["clothes"]["suit"] = None
    new = copy.deepcopy(old)
    new["clothes"]["shirt"] = {"style": "polo", "color": [1, 1, 1]}
    out, _ = apply_module_rules(old, new)
    assert out["clothes"].get("pants") is None


def test_a_missing_suit_is_written_as_null_when_pants_are_set():
    old = copy.deepcopy(BASE)
    del old["clothes"]["suit"]
    new = copy.deepcopy(old)
    new["clothes"]["pants"] = {"style": "shorts", "color": [0, 0, 0]}
    out, _ = apply_module_rules(old, new)
    assert "suit" in out["clothes"] and out["clothes"]["suit"] is None


def test_described_shirt_clears_suit_and_shirt_preset():
    old = copy.deepcopy(BASE)
    old["clothes"]["shirt"] = {"style": "polo"}
    old["clothes"]["suit"] = None
    new = copy.deepcopy(old)
    new["custom_items"].append({"id": "item_3", "module": "shirt", "description": "singlet", "code": "x"})
    out, _ = apply_module_rules(old, new)
    assert out["clothes"]["shirt"] is None and out["clothes"]["suit"] is None


def test_choosing_an_outfit_clears_shirt_pants_and_their_items_only():
    old = copy.deepcopy(BASE)
    old["clothes"].update(suit=None, shirt={"style": "polo"}, pants={"style": "shorts"})
    old["custom_items"] += [{"id": "item_3", "module": "pants", "description": "x", "code": "x"},
                            {"id": "item_4", "description": "untagged singlet", "code": "crew.garment('Clothes__S')"}]
    new = copy.deepcopy(old)
    new["clothes"]["suit"] = "male_casualsuit02"
    out, notes = apply_module_rules(old, new)
    assert out["clothes"]["shirt"] is None and out["clothes"]["pants"] is None
    assert [i["id"] for i in out["custom_items"]] == ["item_1", "item_2"]  # torch and mohawk stay
    assert out["equipment"] == BASE["equipment"] and out["body"] == BASE["body"] and notes


def test_outfit_and_shirt_in_one_change_is_refused():
    new = copy.deepcopy(BASE)
    new["clothes"].update(suit="male_casualsuit02", shirt={"style": "polo"})
    with pytest.raises(ModuleRuleError):
        apply_module_rules(BASE, new)


def test_suit_colour_alone_does_not_trigger_the_outfit_rule():
    old = copy.deepcopy(BASE)
    old["clothes"]["pants"] = {"style": "shorts"}  # inconsistent legacy state: leave it alone
    new = copy.deepcopy(old)
    new["clothes"]["suit_color"] = [1, 0, 0]
    assert apply_module_rules(old, new) == (new, [])


def test_described_hair_makes_preset_bald_and_preset_hair_removes_described():
    old = spec(custom_items=[])
    new = spec(custom_items=[{"id": "item_1", "module": "hair", "description": "mohawk", "code": "x"}])
    out, _ = apply_module_rules(old, new)
    assert out["body"]["hair"] == "none"
    new = copy.deepcopy(BASE)
    new["body"]["hair"] = "afro01"
    out, _ = apply_module_rules(BASE, new)
    assert [i["id"] for i in out["custom_items"]] == ["item_1"]  # mohawk gone, torch kept
    new = copy.deepcopy(BASE)
    new["body"]["hair_color"] = [0.9, 0.8, 0.5]  # colour only: the described hair stays
    assert apply_module_rules(BASE, new)[0]["custom_items"] == BASE["custom_items"]


def test_unrelated_changes_pass_through_untouched():
    new = copy.deepcopy(BASE)
    new["body"]["height_cm"] = 185
    new["equipment"] = ["life_vest"]
    assert apply_module_rules(BASE, new) == (new, [])
