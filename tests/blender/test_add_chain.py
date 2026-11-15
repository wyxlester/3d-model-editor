"""me3d.add_chain through Blender (slow: runs Blender headless), contract §3b.

    server/.venv/bin/python -m pytest tests/blender/test_add_chain.py -q

A custom op builds two weapons the way the build AI is told to: one add_chain call per instance, the geometry
parented under the joints it returns. The exported model must carry each joint nested under the one before,
with the role's axis, and the server's post-build check must find nothing missing.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "server"))
from test_ships import catalog, glb_nodes, run_build  # noqa: E402
from app import articulation  # noqa: E402

WEAPONS = """
ciws = me3d.add_chain("gun_mount", "Weapons__Phalanx_1", "Hull__Main", [(3.0, 0.0, 2.0), (3.0, 0.0, 2.6)], "P0")
me3d.box("Weapons__Phalanx_1_Base", center=(3.0, 0.0, 2.3), size=(0.8, 0.8, 0.6), parent=ciws[0])
me3d.cylinder("Weapons__Phalanx_1_Barrel", (3.0, 0.0, 2.6), (4.2, 0.0, 2.6), 0.05, parent=ciws[1])
ram = me3d.add_chain("trainable_launcher", "Weapons__RAM_1", "Hull__Main",
                     [(-3.0, 0.0, 2.0), (-3.0, 0.0, 2.5), (-2.4, 0.0, 2.5)])
me3d.cylinder("Weapons__RAM_1_Pedestal", (-3.0, 0.0, 2.0), (-3.0, 0.0, 2.3), 0.3, parent=ram[0])
me3d.box("Weapons__RAM_1_Box", center=(-2.7, 0.0, 2.5), size=(0.6, 0.5, 0.4), parent=ram[1])
me3d.box("Weapons__RAM_1_Muzzle", center=(-2.38, 0.0, 2.5), size=(0.04, 0.4, 0.3), parent=ram[2])
"""


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    spec = catalog("custom")
    spec["custom_ops"] = [{"id": "op_weapons", "description": "add a CIWS and a RAM launcher", "code": WEAPONS}]
    spec["joint_limits"] = {"Weapons__Phalanx_1__Gun": {"min_deg": -10, "max_deg": 80}}   # a joint the op makes
    out = tmp_path_factory.mktemp("chains")
    proc, report, parts = run_build(spec, out)
    assert report["ok"], (report["errors"], proc.stdout[-2000:], proc.stderr[-2000:])
    return {"report": report, "parts": {p["path"]: p for p in parts}, "list": parts, "out": out}


def test_chain_joints_are_nested_with_the_role_axes(built):
    p = built["parts"]
    want = {"Weapons__Phalanx_1": ("z", "Hull__Main", 0), "Weapons__Phalanx_1__Gun": ("y", "Weapons__Phalanx_1", 1),
            "Weapons__RAM_1": ("z", "Hull__Main", 0), "Weapons__RAM_1__Elevation": ("y", "Weapons__RAM_1", 1),
            "Weapons__RAM_1__Launcher": ("y", "Weapons__RAM_1__Elevation", 2)}
    for name, (axis, parent, i) in want.items():
        j = p[name]["joint"]
        assert (j["axis"], p[name]["parent"], j["chain"], j["role"] in ("gun_mount", "trainable_launcher")) == \
            (axis, parent, i, True), (name, p[name])
    assert p["Weapons__RAM_1__Elevation"]["joint"]["min_deg"] == -20 and p["Weapons__Phalanx_1"]["joint"]["priority"] == "P0"
    assert p["Weapons__Phalanx_1__Gun"]["origin_m"] == pytest.approx([3.0, 0.0, 2.6], abs=1e-3)
    assert p["Weapons__Phalanx_1_Barrel"]["parent"] == "Weapons__Phalanx_1__Gun"


def test_joint_limits_reach_joints_a_custom_op_made(built):
    """joint_limits apply after the custom ops: before, every planned weapon joint was 'unknown part ... ignored'."""
    j = built["parts"]["Weapons__Phalanx_1__Gun"]["joint"]
    assert (j["min_deg"], j["max_deg"]) == (-10, 80)
    assert not [w for w in built["report"]["warnings"] if "joint_limits" in w]


def test_the_glb_keeps_the_hierarchy(built):
    _, names, parent = glb_nodes(built["out"] / "model.glb")
    assert parent["Weapons__Phalanx_1__Gun"] == "Weapons__Phalanx_1"
    assert parent["Weapons__Phalanx_1_Barrel"] == "Weapons__Phalanx_1__Gun"
    assert parent["Weapons__RAM_1__Launcher"] == "Weapons__RAM_1__Elevation"


def test_the_post_build_check_finds_nothing_missing(built):
    planned = [{"role": "gun_mount", "top": "Weapons__Phalanx_1", "priority": "P0",
                "joints": articulation.expand(articulation.role("gun_mount"), "Weapons__Phalanx_1")}]
    found = articulation.check(built["list"], planned)
    assert not [f for f in found if f["level"] == "warning"], found
    # the RAM launcher was not planned, but its add_chain role tags make it checked too
    assert not [f for f in found if f["part"].startswith("Weapons__RAM_1") and f["level"] == "warning"]
