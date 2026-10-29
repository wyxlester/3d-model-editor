from app.specmerge import keep_crew_nulls, deep_merge, diff_keys, gltf_to_blender, label_for


def test_dicts_merge_recursively():
    base = {"params": {"loa_m": 200, "beam_m": 32}, "name": "x"}
    assert deep_merge(base, {"params": {"beam_m": 30}}) == {"params": {"loa_m": 200, "beam_m": 30}, "name": "x"}


def test_lists_replace_not_concatenate():
    base = {"equipment": ["life_vest", "radio"]}
    assert deep_merge(base, {"equipment": ["binoculars"]}) == {"equipment": ["binoculars"]}
    assert deep_merge(base, {"equipment": []}) == {"equipment": []}


def test_null_deletes_key_at_any_depth():
    base = {"overrides": {"A__B": {"offset_m": [0, 0, 1], "hidden": True}, "C__D": {}}}
    assert deep_merge(base, {"overrides": {"A__B": {"hidden": None}}}) == \
        {"overrides": {"A__B": {"offset_m": [0, 0, 1]}, "C__D": {}}}
    assert deep_merge(base, {"overrides": {"A__B": None}}) == {"overrides": {"C__D": {}}}
    assert deep_merge(base, {"missing": None}) == base


def test_null_inside_new_subtree_is_dropped():
    assert deep_merge({}, {"overrides": {"A__B": {"hidden": None, "scale": [1, 1, 2]}}}) == \
        {"overrides": {"A__B": {"scale": [1, 1, 2]}}}


def test_dict_replaces_scalar_and_scalar_replaces_dict():
    assert deep_merge({"a": 1}, {"a": {"b": 2}}) == {"a": {"b": 2}}
    assert deep_merge({"a": {"b": 2}}, {"a": 3}) == {"a": 3}


def test_inputs_not_mutated():
    base = {"params": {"loa_m": 1}, "list": [1]}
    patch = {"params": {"loa_m": 2}, "list": [2]}
    out = deep_merge(base, patch)
    out["list"].append(3)
    out["params"]["x"] = 1
    assert base == {"params": {"loa_m": 1}, "list": [1]}
    assert patch == {"params": {"loa_m": 2}, "list": [2]}


def test_gltf_point_back_to_blender_frame():
    # Blender (1, 2, 3) exports to glTF (1, 3, -2); the inverse must bring it back.
    assert gltf_to_blender([1, 3, -2]) == [1, 2, 3]


def test_label():
    assert label_for("Roof__Searchlight") == "Roof › Searchlight"
    assert label_for("Transom__Outboard_1") == "Transom › Outboard 1"


def test_diff_keys():
    assert diff_keys({"a": {"b": 1, "c": 2}}, {"a": {"b": 1, "c": 3}, "d": 1}) == ["a.c", "d"]
    assert diff_keys({"a": 1, "gone": 2}, {"a": 1}) == ["gone"]


def test_crew_null_means_empty_slot_not_default():
    old = {"clothes": {"suit": "male_worksuit01", "shoes": "shoes01"}, "body": {"hair_color": [1, 1, 0]}}
    patch = {"clothes": {"suit": None}, "body": {"hair_color": None}}
    new = keep_crew_nulls(deep_merge(old, patch), patch)
    assert new["clothes"] == {"suit": None, "shoes": "shoes01"}  # absent would rebuild the default suit
    assert new["body"] == {"hair_color": None}
    # other sections keep deep_merge's delete-on-null (ship overrides rely on it)
    assert keep_crew_nulls({"overrides": {}}, {"overrides": {"A__B": None}}) == {"overrides": {}}
