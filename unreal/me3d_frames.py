"""Pure helpers shared by the Unreal import scripts and their tests. No `unreal` import here.

Frames (docs/CONTRACT.md section 1):

- Blender source frame: metres, +X bow, +Y port, +Z up, right-handed.
- Unreal world frame: centimetres, +X forward (bow), +Y right (starboard), +Z up, left-handed.

So a point maps (x, y, z) m  ->  (100x, -100y, 100z) cm: only Y flips.

Rotations. Blender's XYZ Euler applies X, then Y, then Z about the fixed world axes
(R = Rz @ Ry @ Rx). Unreal's FRotator applies Roll (about X), then Pitch (about Y), then
Yaw (about Z), in the same order, but with Unreal's own sign conventions:
  +Yaw   turns +X toward +Y (starboard)  - Blender +rz turns +X toward port, so yaw   = -rz
  +Pitch turns +X toward +Z (nose up)    - Blender +ry turns +X toward -Z, so    pitch = -ry
  +Roll  turns +Y toward -Z              - Blender +rx lifts port (-Y in UE), so roll  = +rx
`tests/unreal/test_frames.py` proves this with rotation matrices rather than by assertion.
"""
import json
import math
import struct

CM_PER_M = 100.0


def blender_to_ue_location_cm(location_m):
    x, y, z = location_m
    return (x * CM_PER_M, -y * CM_PER_M, z * CM_PER_M)


def blender_to_ue_rotator(rotation_deg):
    """Blender XYZ Euler degrees -> Unreal (roll, pitch, yaw) degrees."""
    rx, ry, rz = rotation_deg
    return (rx, -ry, -rz)


# ---- matrices, used by the tests and by the harness to compare rotations without Euler ambiguity
def _rx(a):
    c, s = math.cos(a), math.sin(a)
    return [[1, 0, 0], [0, c, -s], [0, s, c]]


def _ry(a):
    c, s = math.cos(a), math.sin(a)
    return [[c, 0, s], [0, 1, 0], [-s, 0, c]]


def _rz(a):
    c, s = math.cos(a), math.sin(a)
    return [[c, -s, 0], [s, c, 0], [0, 0, 1]]


def matmul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def blender_euler_matrix(rotation_deg):
    """Rotation matrix (columns = rotated axes) of a Blender XYZ Euler, in the Blender frame."""
    rx, ry, rz = (math.radians(v) for v in rotation_deg)
    return matmul(_rz(rz), matmul(_ry(ry), _rx(rx)))


def ue_rotator_axes(roll, pitch, yaw):
    """Unreal's FRotationMatrix axes (X, Y, Z) in the Unreal frame, as written in
    Engine/Source/Runtime/Core/Public/Math/RotationTranslationMatrix.h."""
    sp, cp = math.sin(math.radians(pitch)), math.cos(math.radians(pitch))
    sy, cy = math.sin(math.radians(yaw)), math.cos(math.radians(yaw))
    sr, cr = math.sin(math.radians(roll)), math.cos(math.radians(roll))
    x = (cp * cy, cp * sy, sp)
    y = (sr * sp * cy - cr * sy, sr * sp * sy + cr * cy, -sr * cp)
    z = (-(cr * sp * cy + sr * sy), cy * sr - cr * sp * sy, cr * cp)
    return x, y, z


def blender_vector_to_ue(v):
    return (v[0], -v[1], v[2])


# ---- GLB reading (no pygltflib inside Unreal's Python)
def read_glb_json(path):
    with open(path, "rb") as f:
        data = f.read()
    magic, _version, _length = struct.unpack_from("<III", data, 0)
    if magic != 0x46546C67:
        raise ValueError(f"{path} is not a GLB")
    chunk_len, chunk_type = struct.unpack_from("<II", data, 12)
    if chunk_type != 0x4E4F534A:
        raise ValueError(f"{path}: first chunk is not JSON")
    return json.loads(data[20:20 + chunk_len].decode("utf-8"))


def _parents(nodes):
    """{child node index: parent node index}."""
    return {c: i for i, n in enumerate(nodes) for c in n.get("children", [])}


def _name(nodes, i):
    return nodes[i].get("name", f"node_{i}")


def glb_mesh_nodes(gltf):
    """{node name: parent node name or None} for every node that carries a mesh."""
    nodes = gltf.get("nodes", [])
    parent = _parents(nodes)
    return {_name(nodes, i): (_name(nodes, parent[i]) if i in parent else None)
            for i, n in enumerate(nodes) if "mesh" in n}


def glb_bone_attachments(gltf):
    """{mesh node name: bone name} for every rigid (unskinned) mesh hung under a skeleton joint:
    crew equipment (contract section 6: `Equipment__<Name>`, parented to a bone). Unreal imports
    each as a separate static mesh in its rest-pose place and drops the bone link, so the importer
    attaches it to the bone named here."""
    nodes = gltf.get("nodes", [])
    joints = {j for s in gltf.get("skins", []) for j in s.get("joints", [])}
    parent = _parents(nodes)
    out = {}
    for i, n in enumerate(nodes):
        if "mesh" not in n or "skin" in n:
            continue
        p = parent.get(i)
        while p is not None and p not in joints:
            p = parent.get(p)
        if p is not None:
            out[_name(nodes, i)] = _name(nodes, p)
    return out


def _mat4_mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def _node_matrix(n):
    """A glTF node's local matrix (rows), from `matrix` (column-major) or T * R * S."""
    if "matrix" in n:
        m = n["matrix"]
        return [[m[c * 4 + r] for c in range(4)] for r in range(4)]
    tx, ty, tz = n.get("translation", (0, 0, 0))
    x, y, z, w = n.get("rotation", (0, 0, 0, 1))
    sx, sy, sz = n.get("scale", (1, 1, 1))
    r = [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
         [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
         [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]
    return [[r[0][0] * sx, r[0][1] * sy, r[0][2] * sz, tx],
            [r[1][0] * sx, r[1][1] * sy, r[1][2] * sz, ty],
            [r[2][0] * sx, r[2][1] * sy, r[2][2] * sz, tz],
            [0, 0, 0, 1]]


def glb_item_centres_cm(gltf):
    """{item: (x, y, z)}: the centre of each bone-hung item's bounding box in the rest pose, in
    the crew's own frame converted to Unreal (cm, +X forward, +Y right, +Z up). Worked out from
    the GLB alone - node transforms and the POSITION min/max - so the harness can check Unreal's
    placement without trusting the importer's (or the hook's) conversion. glTF (x, y, z) metres,
    +Y up, is Blender (x, -z, y), which section 1 maps to Unreal (100x, 100z, 100y)."""
    nodes = gltf.get("nodes", [])
    parent = _parents(nodes)
    by_name = {_name(nodes, i): i for i in range(len(nodes))}
    acc = gltf.get("accessors", [])
    out = {}
    for item in glb_bone_attachments(gltf):
        i = by_name[item]
        world, p = _node_matrix(nodes[i]), parent.get(i)
        while p is not None:
            world = _mat4_mul(_node_matrix(nodes[p]), world)
            p = parent.get(p)
        prims = gltf["meshes"][nodes[i]["mesh"]]["primitives"]
        los = [acc[pr["attributes"]["POSITION"]]["min"] for pr in prims]
        his = [acc[pr["attributes"]["POSITION"]]["max"] for pr in prims]
        lo = [min(v[k] for v in los) for k in range(3)]
        hi = [max(v[k] for v in his) for k in range(3)]
        c = [(lo[k] + hi[k]) / 2 for k in range(3)] + [1.0]
        gx, gy, gz = (sum(world[r][k] * c[k] for k in range(4)) for r in range(3))
        out[item] = (gx * CM_PER_M, gz * CM_PER_M, gy * CM_PER_M)
    return out


def glb_animation_names(gltf):
    return [a.get("name", f"anim_{i}") for i, a in enumerate(gltf.get("animations", []))]


def anim_name_matches(asset_name, anim, glb_stem):
    """Interchange names a glTF clip '<file stem><clip>' (seen on 5.8.3); accept the plain and
    separator forms too so a naming change in a later engine does not read as a missing clip."""
    return asset_name in (anim, glb_stem + anim, f"{glb_stem}_{anim}", f"{glb_stem}_Anim_{anim}")


def distance(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))
