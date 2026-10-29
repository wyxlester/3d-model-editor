"""Which bone each rigid crew item hangs from, and where it sits, read from the GLB alone (the
Unreal harness checks the import hook's placement against these).

Unreal drops the bone link of an unskinned mesh under a joint, so this map is the only thing that
keeps a life vest on the chest instead of at the feet.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "unreal"))
import me3d_frames as f  # noqa: E402


def crew_gltf():
    """The shape the crew builder exports: an armature node holding the skinned body meshes and
    the joint tree, with equipment as plain mesh nodes under joints."""
    names = ["Crew", "Body__Skin", "Root", "pelvis", "spine_01", "spine_02", "Equipment__life_vest",
             "hand_r", "grip", "Equipment__radio", "head", "Equipment__helmet"]
    i = {n: k for k, n in enumerate(names)}
    nodes = [{"name": n} for n in names]
    nodes[i["Crew"]]["children"] = [i["Body__Skin"], i["Root"]]
    nodes[i["Body__Skin"]].update(mesh=0, skin=0)
    nodes[i["Root"]]["children"] = [i["pelvis"]]
    nodes[i["pelvis"]]["children"] = [i["spine_01"], i["hand_r"]]
    nodes[i["spine_01"]]["children"] = [i["spine_02"]]
    nodes[i["spine_02"]]["children"] = [i["Equipment__life_vest"], i["head"]]
    nodes[i["Equipment__life_vest"]]["mesh"] = 1
    nodes[i["hand_r"]]["children"] = [i["grip"]]
    nodes[i["grip"]]["children"] = [i["Equipment__radio"]]   # a helper empty between bone and item
    nodes[i["Equipment__radio"]]["mesh"] = 2
    nodes[i["head"]]["children"] = [i["Equipment__helmet"]]
    nodes[i["Equipment__helmet"]]["mesh"] = 3
    joints = [i[n] for n in ("Root", "pelvis", "spine_01", "spine_02", "hand_r", "head")]
    return {"nodes": nodes, "skins": [{"joints": joints}], "scenes": [{"nodes": [i["Crew"]]}]}


def test_each_item_maps_to_the_bone_it_hangs_from():
    assert f.glb_bone_attachments(crew_gltf()) == {
        "Equipment__life_vest": "spine_02",
        "Equipment__radio": "hand_r",       # through the helper empty to the nearest joint
        "Equipment__helmet": "head",
    }


def test_skinned_meshes_are_part_of_the_skeletal_mesh_not_attachments():
    assert "Body__Skin" not in f.glb_bone_attachments(crew_gltf())


def test_a_ship_has_no_skeleton_and_so_nothing_to_attach():
    ship = {"nodes": [{"name": "Ship", "children": [1, 2]}, {"name": "Hull__Main", "mesh": 0},
                      {"name": "Crane__Boom", "mesh": 1}]}
    assert f.glb_bone_attachments(ship) == {}


def test_item_centre_follows_the_bone_chain_into_unreal_cm():
    # Crew at the origin; a joint 1.2 m up (glTF +Y), turned 90 deg about glTF +Y; the vest 0.1 m
    # along the joint's own +X, with a box 0.2 .. 0.4 m along its own +Z.
    s = 0.5 ** 0.5
    gltf = {"nodes": [{"name": "Crew", "children": [1, 2]},
                      {"name": "Body", "mesh": 0, "skin": 0},
                      {"name": "spine", "translation": [0, 1.2, 0], "rotation": [0, s, 0, s], "children": [3]},
                      {"name": "Equipment__vest", "translation": [0.1, 0, 0], "mesh": 1}],
            "skins": [{"joints": [2]}],
            "meshes": [{"primitives": []}, {"primitives": [{"attributes": {"POSITION": 0}}]}],
            "accessors": [{"min": [-0.1, -0.1, 0.2], "max": [0.1, 0.1, 0.4]}]}
    # box centre in the vest's frame: (0, 0, 0.3); in the joint: (0.1, 0, 0.3); the joint's 90 deg
    # turn about +Y sends +X to -Z and +Z to +X: (0.3, 0, -0.1); plus 1.2 m up: (0.3, 1.2, -0.1) m.
    # Unreal (100x, 100z, 100y): (30, -10, 120) cm - forward, a little to the left, chest height.
    (x, y, z), = f.glb_item_centres_cm(gltf).values()
    assert (round(x, 6), round(y, 6), round(z, 6)) == (30.0, -10.0, 120.0)


def test_clip_names_interchange_gives():
    assert f.anim_name_matches("DefaultMaleCrewidle", "idle", "DefaultMaleCrew")
    assert not f.anim_name_matches("DefaultMaleCrewwalk", "idle", "DefaultMaleCrew")
