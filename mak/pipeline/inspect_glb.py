"""Static checks on a Blender-exported .glb before conversion.

Reports bounds in the MAK model frame (X right, Y forward, Z up; manual 21.2), triangle count,
textures and the node tree, with each node's pivot, parent, local transform and subtree bounds.
Flags problems with scale, origin, size budgets and articulated parts (manual 21.4, 21.1.2).

glTF is Y-up. Blender's glTF exporter maps Blender (x, y, z) -> glTF (x, z, -y), so we map back
to Blender axes, then rotate by the manifest's forward axis so the model faces +Y.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import struct
import sys
from pathlib import Path

from . import artparts
from .common import Finding, load_config, print_findings

GLB_MAGIC = 0x46546C67
CHUNK_JSON = 0x4E4F534A
CHUNK_BIN = 0x004E4942

# angle (deg, about +Z) that brings the model's forward axis onto +Y
FORWARD_ROT = {"+Y": 0, "-Y": 180, "+X": 90, "-X": -90}


def read_glb(path: Path) -> tuple[dict, bytes]:
    data = path.read_bytes()
    magic, version, length = struct.unpack_from("<III", data, 0)
    if magic != GLB_MAGIC:
        raise ValueError(f"{path} is not a binary glTF (.glb) file")
    if version != 2:
        raise ValueError(f"glTF version {version} unsupported (need 2)")
    off, gltf, binchunk = 12, None, b""
    while off < length:
        clen, ctype = struct.unpack_from("<II", data, off)
        chunk = data[off + 8: off + 8 + clen]
        if ctype == CHUNK_JSON:
            gltf = json.loads(chunk.decode("utf-8"))
        elif ctype == CHUNK_BIN:
            binchunk = chunk
        off += 8 + clen
    if gltf is None:
        raise ValueError("no JSON chunk in glb")
    return gltf, binchunk


# --- tiny 4x4 matrix helpers (column-major like glTF) ---
def _mat_identity():
    return [1.0 if i % 5 == 0 else 0.0 for i in range(16)]


def _mat_mul(a, b):
    out = [0.0] * 16
    for c in range(4):
        for r in range(4):
            out[c * 4 + r] = sum(a[k * 4 + r] * b[c * 4 + k] for k in range(4))
    return out


def _node_matrix(node: dict):
    if "matrix" in node:
        return list(node["matrix"])
    tx, ty, tz = node.get("translation", [0, 0, 0])
    qx, qy, qz, qw = node.get("rotation", [0, 0, 0, 1])
    sx, sy, sz = node.get("scale", [1, 1, 1])
    r = [
        1 - 2 * (qy * qy + qz * qz), 2 * (qx * qy + qz * qw), 2 * (qx * qz - qy * qw), 0,
        2 * (qx * qy - qz * qw), 1 - 2 * (qx * qx + qz * qz), 2 * (qy * qz + qx * qw), 0,
        2 * (qx * qz + qy * qw), 2 * (qy * qz - qx * qw), 1 - 2 * (qx * qx + qy * qy), 0,
        0, 0, 0, 1,
    ]
    s = [sx, 0, 0, 0, 0, sy, 0, 0, 0, 0, sz, 0, 0, 0, 0, 1]
    t = _mat_identity()
    t[12], t[13], t[14] = tx, ty, tz
    return _mat_mul(t, _mat_mul(r, s))


def _apply(m, p):
    x, y, z = p
    return (m[0] * x + m[4] * y + m[8] * z + m[12],
            m[1] * x + m[5] * y + m[9] * z + m[13],
            m[2] * x + m[6] * y + m[10] * z + m[14])


def _png_size(blob: bytes):
    if blob[:8] == b"\x89PNG\r\n\x1a\n":
        return struct.unpack(">II", blob[16:24])
    return None


def _local_rotation_deg(node: dict) -> float:
    """Angle (deg) of the node's own rotation; 0 = neutral (manual 21.4: no static offsets)."""
    if "matrix" in node:
        m = node["matrix"]
        cols = [math.sqrt(m[i] ** 2 + m[i + 1] ** 2 + m[i + 2] ** 2) or 1 for i in (0, 4, 8)]
        trace = m[0] / cols[0] + m[5] / cols[1] + m[10] / cols[2]
        return math.degrees(math.acos(max(-1.0, min(1.0, (trace - 1) / 2))))
    qw = node.get("rotation", [0, 0, 0, 1])[3]
    return math.degrees(2 * math.acos(max(-1.0, min(1.0, abs(qw)))))


def _local_scale(node: dict) -> list[float]:
    if "matrix" in node:
        m = node["matrix"]
        return [round(math.sqrt(m[i] ** 2 + m[i + 1] ** 2 + m[i + 2] ** 2), 6) for i in (0, 4, 8)]
    return [float(v) for v in node.get("scale", [1, 1, 1])]


def to_mak_fn(forward_axis: str):
    a = math.radians(FORWARD_ROT[forward_axis])
    ca, sa = math.cos(a), math.sin(a)

    def to_mak(p):
        bx, by, bz = p[0], -p[2], p[1]          # glTF -> Blender
        return (bx * ca - by * sa, bx * sa + by * ca, bz)
    return to_mak


def _box_to_mak(lo, hi, to_mak):
    if lo is None:
        return None
    corners = [to_mak((x, y, z)) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
    mn = [min(c[i] for c in corners) for i in range(3)]
    mx = [max(c[i] for c in corners) for i in range(3)]
    return {"min": [round(v, 4) for v in mn], "max": [round(v, 4) for v in mx],
            "extent": [round(mx[i] - mn[i], 4) for i in range(3)]}


def analyse(path: Path, forward_axis: str = "+Y") -> dict:
    gltf, binchunk = read_glb(path)
    accessors, nodes, meshes = gltf.get("accessors", []), gltf.get("nodes", []), gltf.get("meshes", [])
    to_mak = to_mak_fn(forward_axis)

    triangles = 0
    node_names: list[str] = []
    node_info: dict[str, dict] = {}
    duplicates: list[str] = []

    def union(a, b):
        if a is None:
            return b
        if b is None:
            return a
        return ([min(a[0][i], b[0][i]) for i in range(3)], [max(a[1][i], b[1][i]) for i in range(3)])

    def walk(idx: int, parent_m, parent_name):
        nonlocal triangles
        node = nodes[idx]
        name = node.get("name", f"node{idx}")
        node_names.append(name)
        m = _mat_mul(parent_m, _node_matrix(node))
        box = None
        if "mesh" in node:
            for prim in meshes[node["mesh"]].get("primitives", []):
                pos = accessors[prim["attributes"]["POSITION"]]
                if prim.get("mode", 4) == 4:
                    n = accessors[prim["indices"]]["count"] if "indices" in prim else pos["count"]
                    triangles += n // 3
                if "min" in pos and "max" in pos:
                    mn, mx = pos["min"], pos["max"]
                    for cx in (mn[0], mx[0]):
                        for cy in (mn[1], mx[1]):
                            for cz in (mn[2], mx[2]):
                                p = _apply(m, (cx, cy, cz))
                                box = union(box, (list(p), list(p)))
        own = box
        for c in node.get("children", []):
            box = union(box, walk(c, m, name))
        if name in node_info:
            duplicates.append(name)
        else:
            pivot = to_mak((m[12], m[13], m[14]))
            node_info[name] = {
                "parent": parent_name,
                "has_mesh": "mesh" in node,
                "pivot": [round(v, 4) for v in pivot],
                "local_rotation_deg": round(_local_rotation_deg(node), 3),
                "local_scale": _local_scale(node),
                "subtree_bounds": _box_to_mak(*(box or (None, None)), to_mak),
                "own_bounds": _box_to_mak(*(own or (None, None)), to_mak),
            }
        return box

    scene = gltf.get("scenes", [{}])[gltf.get("scene", 0)] if gltf.get("scenes") else {"nodes": list(range(len(nodes)))}
    total = None
    for root in scene.get("nodes", []):
        total = union(total, walk(root, _mat_identity(), None))

    textures = []
    for i, img in enumerate(gltf.get("images", [])):
        size = None
        if "bufferView" in img and binchunk:
            bv = gltf["bufferViews"][img["bufferView"]]
            blob = binchunk[bv.get("byteOffset", 0): bv.get("byteOffset", 0) + bv["byteLength"]]
            size = _png_size(blob)
        textures.append({"name": img.get("name", f"image{i}"), "mime": img.get("mimeType"),
                         "uri": img.get("uri"), "size": list(size) if size else None})

    return {
        "file": str(path),
        "generator": gltf.get("asset", {}).get("generator"),
        "forward_axis": forward_axis,
        "bounds_mak_frame": _box_to_mak(*(total or (None, None)), to_mak),   # X right, Y forward, Z up (m)
        "triangles": triangles,
        "node_names": node_names,
        "nodes": node_info,
        "duplicate_node_names": duplicates,
        "textures": textures,
        "materials": len(gltf.get("materials", [])),
        "animations": len(gltf.get("animations", [])),
        "extensions_required": gltf.get("extensionsRequired", []),
    }


def _ancestors(nodes: dict, name: str) -> list[str]:
    out, cur = [], nodes.get(name, {}).get("parent")
    while cur:
        out.append(cur)
        cur = nodes.get(cur, {}).get("parent")
    return out


LOD_LIKE = re.compile(r"(?i)^(lod[_ ]?\d+|.*_lod\d+)$")


def check_articulation(report: dict, manifest: dict | None, node_map: artparts.NodeNameMap) -> list[Finding]:
    """Manual 21.4 (one DOF node per part, geometry beneath it, no static offsets, child parts under
    their parent part) and 21.1.2 (FBX parts are matched by node name)."""
    f: list[Finding] = []
    nodes = report.get("nodes", {})
    declared = (manifest or {}).get("articulated_parts", [])
    names = {p["node"] for p in declared}
    codes_seen: dict[int, str] = {}

    for name in report.get("duplicate_node_names", []):
        level = "error" if name in names else "warning"
        f.append(Finding(level, "duplicate-node-name", f"node name '{name}' is used more than once; FBX parts are matched by name"))

    for p in declared:
        node, code = p["node"], int(p["dis_part"])
        if code in codes_seen:
            f.append(Finding("error", "artpart-code-duplicate", f"part code {code} used by '{codes_seen[code]}' and '{node}'"))
        codes_seen[code] = node
        if code not in artparts.CODES and code not in node_map.parts.values():
            f.append(Finding("warning", "artpart-code-unknown", f"'{node}': code {code} is not in manual Table 58 or FBX_NodeNameMap.csv"))
        motion = p.get("motion") or artparts.default_motion(code)
        if motion not in artparts.MOTIONS:
            f.append(Finding("error", "artpart-motion", f"'{node}': motion '{motion}' must be one of {sorted(artparts.MOTIONS)}"))
        if p.get("min") is not None and p.get("max") is not None and p["min"] > p["max"]:
            f.append(Finding("error", "artpart-limits", f"'{node}': min {p['min']} > max {p['max']}"))
        mapped = node_map.parts.get(node)
        if mapped is not None and mapped != code:
            f.append(Finding("error", "artpart-name-conflict",
                             f"node '{node}' means part {mapped} in FBX_NodeNameMap.csv but the manifest says {code}; rename it"))

        info = nodes.get(node)
        if info is None:
            f.append(Finding("error", "artpart-node-missing", f"manifest articulated part node '{node}' not in glb nodes"))
            continue

        parent = p.get("parent")
        if parent:
            if parent not in names:
                f.append(Finding("error", "artpart-parent-unknown", f"'{node}': parent '{parent}' is not a declared articulated part"))
            elif parent not in _ancestors(nodes, node):
                f.append(Finding("error", "artpart-hierarchy",
                                 f"'{node}' must be a descendant of its parent part '{parent}' in the node tree (manual 21.4)"))
        else:
            parent_parts = [a for a in _ancestors(nodes, node) if a in names]
            if parent_parts:
                f.append(Finding("warning", "artpart-parent-undeclared",
                                 f"'{node}' sits under part '{parent_parts[0]}' but declares no parent"))

        if info["subtree_bounds"] is None:
            f.append(Finding("error", "artpart-no-geometry", f"'{node}': no geometry under the part node (manual 21.4)"))
        else:
            # the part's own mesh if it has one (a mesh node acting as DOF), else everything beneath the DOF
            b = info["own_bounds"] or info["subtree_bounds"]
            pad = [0.05 + 0.1 * e for e in b["extent"]]
            outside = [ax for i, ax in enumerate("xyz") if not (b["min"][i] - pad[i] <= info["pivot"][i] <= b["max"][i] + pad[i])]
            if outside:
                f.append(Finding("warning", "artpart-pivot",
                                 f"'{node}': pivot {info['pivot']} is outside its geometry {b['min']}..{b['max']} on {''.join(outside)}; "
                                 "set the object origin to the rotation axis in Blender"))
        if info["local_rotation_deg"] > 0.5:
            f.append(Finding("warning", "artpart-static-rotation",
                             f"'{node}' has a {info['local_rotation_deg']} deg rotation in its neutral pose; apply rotation in Blender "
                             "so all-zero DOF = neutral pose (manual 21.4)"))
        if any(abs(s - 1) > 1e-3 for s in info["local_scale"]):
            f.append(Finding("warning", "artpart-scale", f"'{node}' has scale {info['local_scale']}; apply scale in Blender"))

    for name in report.get("node_names", []):
        if name not in names and name in node_map.parts:
            f.append(Finding("warning", "artpart-undeclared",
                             f"node '{name}' matches FBX_NodeNameMap.csv (part {node_map.parts[name]}) so it will articulate "
                             "visually, but it is not declared in the manifest"))
        if LOD_LIKE.match(name) and name not in node_map.lods:
            f.append(Finding("warning", "lod-naming", f"node '{name}' looks like an LOD; VR-Forces only recognises {sorted(node_map.lods) or ['High', 'Med', 'Low']}"))
    return f


def check(report: dict, manifest: dict | None, limits: dict, node_map: artparts.NodeNameMap | None = None) -> list[Finding]:
    f: list[Finding] = []
    b = report["bounds_mak_frame"]
    if b is None:
        return [Finding("error", "no-geometry", "no mesh with POSITION bounds found")]

    ext = b["extent"]
    longest = max(ext)
    if longest > limits["max_extent_m"]:
        f.append(Finding("error", "scale-too-large", f"longest side {longest} m > {limits['max_extent_m']} m; check units in Blender"))
    if longest < limits["min_extent_m"]:
        f.append(Finding("error", "scale-too-small", f"longest side {longest} m < {limits['min_extent_m']} m; exported in cm/mm?"))

    if report["triangles"] > limits["max_triangles"]:
        f.append(Finding("warning", "poly-budget", f"{report['triangles']} triangles > budget {limits['max_triangles']}"))
    for t in report["textures"]:
        if t["size"] and max(t["size"]) > limits["max_texture_px"]:
            f.append(Finding("warning", "texture-size", f"{t['name']} is {t['size']} px"))
        if t["size"] and any(s & (s - 1) for s in t["size"]):
            f.append(Finding("warning", "texture-npot", f"{t['name']} is not power-of-two {t['size']}"))
    if report["extensions_required"]:
        f.append(Finding("warning", "gltf-extensions", f"required extensions {report['extensions_required']} may not survive FBX export"))
    if report["animations"]:
        f.append(Finding("warning", "gltf-animations", f"{report['animations']} glTF animation(s) are ignored; VR-Forces drives parts from the simulation"))

    domain = (manifest or {}).get("domain")
    tol = max(0.02, 0.02 * longest)
    if domain == "land":  # manual 21.2.1: origin on ground-contact plane, at yaw axis
        if abs(b["min"][2]) > tol:
            f.append(Finding("error", "origin-z", f"land model lowest point z={b['min'][2]} m; origin must be on the ground plane"))
    if domain in {"air", "space"} and not (b["min"][2] < 0 < b["max"][2]):
        f.append(Finding("warning", "origin-cg", "air/space origin should be at the CG, inside the model's bounds"))
    for axis, i in (("x", 0), ("y", 1)):
        centre = (b["min"][i] + b["max"][i]) / 2
        if abs(centre) > 0.25 * ext[i] + tol:
            f.append(Finding("warning", f"origin-{axis}", f"bbox centre {axis}={centre:.3f} m is far from origin; yaw axis should pass near the centre"))
    if domain in {"land", "surface", "subsurface"} and ext[1] < ext[0] * 0.6:
        f.append(Finding("warning", "forward-axis", f"model is much wider (X={ext[0]}) than long (Y={ext[1]}); is forward_axis_in_blender right?"))

    f += check_articulation(report, manifest, node_map or artparts.NodeNameMap())
    return f


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("glb", type=Path)
    ap.add_argument("--manifest", type=Path, help="manifest.json (default: next to the glb)")
    ap.add_argument("--json", action="store_true", help="print the raw report as JSON")
    args = ap.parse_args(argv)

    cfg = load_config()
    mpath = args.manifest or args.glb.parent / "manifest.json"
    manifest = json.loads(mpath.read_text(encoding="utf-8")) if mpath.exists() else None
    report = analyse(args.glb, (manifest or {}).get("forward_axis_in_blender", "+Y"))
    findings = check(report, manifest, cfg["limits"], artparts.load_node_map(cfg.get("fbx_node_name_map")))
    if args.json:
        print(json.dumps({"report": report, "findings": [x.to_dict() for x in findings]}, indent=2))
    else:
        print(json.dumps(report, indent=2))
        print_findings("inspect", findings)
    return 1 if any(x.level == "error" for x in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
