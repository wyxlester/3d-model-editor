"""Build small test .glb files with the standard library, in glTF Y-up coordinates.

Default model (Blender/MAK frame: X right, Y forward, Z up):
  hull    2 x 5 x 1.5 m box, base on z=0
  turret  child of hull, pivot at (0, 0, 1.5) = its base centre, box 1.2 x 1.6 x 0.5
  gun     child of turret, pivot at the trunnion (0, 0.8, 1.75), barrel 0.2 x 3.0 x 0.2 forward of it

Variants break one articulation rule each (manual 21.4):
  bad_pivot       turret pivot left at the world origin, geometry offset up instead
  flat_gun        gun is a sibling of turret instead of its child
  rotated_turret  turret node carries a 90 deg rotation in its neutral pose
"""
from __future__ import annotations

import json
import math
import struct
import zlib
from pathlib import Path


def _box(lo, hi):
    x0, y0, z0 = lo
    x1, y1, z1 = hi
    v = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
         (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    idx = [0, 2, 1, 0, 3, 2, 4, 5, 6, 4, 6, 7, 0, 1, 5, 0, 5, 4,
           1, 2, 6, 1, 6, 5, 2, 3, 7, 2, 7, 6, 3, 0, 4, 3, 4, 7]
    return v, idx


def _g(x, y, z):
    """Blender/MAK (x right, y forward, z up) -> glTF (x, z, -y)."""
    return (x, z, -y)


def _gbox(lo, hi):
    a, b = _g(*lo), _g(*hi)
    return _box(tuple(min(a[i], b[i]) for i in range(3)), tuple(max(a[i], b[i]) for i in range(3)))


def rgb_png(w: int, h: int, pixel: tuple) -> bytes:
    """A w x h PNG of one colour; pixel has 1, 3 or 4 values (grey, RGB, RGBA)."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    ctype = {1: 0, 3: 2, 4: 6}[len(pixel)]
    rows = b"".join(b"\0" + bytes(pixel) * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, ctype, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


# PBR fixture maps (glTF layout): normal tilted up, ORM = AO 200, roughness 64, metal 255
PBR_NORMAL = (100, 180, 230)
PBR_ORM = (200, 64, 255)


def tiny_png(w: int = 2, h: int = 2) -> bytes:
    """A valid w x h RGB PNG (red/white checker), standard library only."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    rows = b"".join(b"\0" + b"".join(b"\xff\x00\x00" if (x + y) % 2 else b"\xff\xff\xff" for x in range(w)) for y in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def make_glb(path: Path, scale: float = 1.0, lift: float = 0.0, variant: str = "ok",
             names: tuple[str, str, str] = ("hull", "turret", "gun"),
             sockets: dict[str, tuple[float, float, float]] | None = None, textured: bool = False,
             pbr: bool = False) -> Path:
    """sockets: extra empty root nodes {name: (x, y, z)} in the Blender/MAK frame, e.g. socket_bow.
    textured: add UVs (u = vertex index / 8, v = 0.25) and one material with an embedded 2x2 PNG.
    pbr (implies textured): images 0 checker, 1 normal map (PBR_NORMAL), 2 ORM (PBR_ORM), all 2x2. Materials:
      0 "Paint"    hull: checker + normal + metallic-roughness/occlusion from the ORM, factors 1
      1 "Plain"    gun: the same checker, no maps
      2 "MapsOnly" turret: no base texture, normal map only, metallic 0.25, roughness 0.5"""
    textured = textured or pbr
    prim_material = (0, 2, 1) if pbr else (0, 0, 0)
    s = scale
    hull = _gbox((-1 * s, -2.5 * s, lift * s), (1 * s, 2.5 * s, (1.5 + lift) * s))
    turret_pivot = (0, 0, (1.5 + lift) * s)
    turret_geom_local = ((-0.6 * s, -0.8 * s, 0), (0.6 * s, 0.8 * s, 0.5 * s))
    gun_local_pivot = (0, 0.8 * s, 0.25 * s)          # relative to turret pivot
    if variant == "bad_pivot":   # origin left at world zero; geometry carries the height instead
        turret_pivot = (0, 0, 0)
        turret_geom_local = ((-0.6 * s, -0.8 * s, (1.5 + lift) * s), (0.6 * s, 0.8 * s, (2.0 + lift) * s))
        gun_local_pivot = (0, 0.8 * s, (1.75 + lift) * s)
    turret = _gbox(*turret_geom_local)
    gun = _gbox((-0.1 * s, 0, -0.1 * s), (0.1 * s, 3.0 * s, 0.1 * s))

    buf = bytearray()
    views, accessors, meshes = [], [], []
    for verts, idx in (hull, turret, gun):
        pv = len(views)
        off = len(buf)
        for p in verts:
            buf += struct.pack("<3f", *p)
        views.append({"buffer": 0, "byteOffset": off, "byteLength": len(buf) - off, "target": 34962})
        off = len(buf)
        for i in idx:
            buf += struct.pack("<H", i)
        views.append({"buffer": 0, "byteOffset": off, "byteLength": len(buf) - off, "target": 34963})
        while len(buf) % 4:
            buf += b"\0"
        xs, ys, zs = zip(*verts)
        accessors.append({"bufferView": pv, "componentType": 5126, "count": len(verts), "type": "VEC3",
                          "min": [min(xs), min(ys), min(zs)], "max": [max(xs), max(ys), max(zs)]})
        accessors.append({"bufferView": pv + 1, "componentType": 5123, "count": len(idx), "type": "SCALAR"})
        prim = {"attributes": {"POSITION": len(accessors) - 2}, "indices": len(accessors) - 1}
        if textured:
            off = len(buf)
            for k in range(len(verts)):
                buf += struct.pack("<2f", k / 8.0, 0.25)
            views.append({"buffer": 0, "byteOffset": off, "byteLength": len(buf) - off, "target": 34962})
            accessors.append({"bufferView": len(views) - 1, "componentType": 5126, "count": len(verts), "type": "VEC2"})
            prim["attributes"]["TEXCOORD_0"] = len(accessors) - 1
            prim["material"] = prim_material[len(meshes)]
        meshes.append({"primitives": [prim]})
    extra = {}
    if textured:
        pngs = [("checker", tiny_png())]
        if pbr:
            pngs += [("normal", rgb_png(2, 2, PBR_NORMAL)), ("orm", rgb_png(2, 2, PBR_ORM))]
        images = []
        for iname, png in pngs:
            off = len(buf)
            buf += png
            while len(buf) % 4:
                buf += b"\0"
            views.append({"buffer": 0, "byteOffset": off, "byteLength": len(png)})
            images.append({"name": iname, "mimeType": "image/png", "bufferView": len(views) - 1})
        extra = {"images": images,
                 "textures": [{"source": i} for i in range(len(images))],
                 "materials": [{"name": "Paint", "doubleSided": True,
                                "pbrMetallicRoughness": {"baseColorFactor": [0.2, 0.4, 0.6, 1.0],
                                                         "baseColorTexture": {"index": 0},
                                                         "metallicFactor": 0.0, "roughnessFactor": 0.5}}]}
        if pbr:
            paint = extra["materials"][0]
            paint["pbrMetallicRoughness"].update(metallicFactor=1.0, roughnessFactor=1.0,
                                                 metallicRoughnessTexture={"index": 2})
            paint["normalTexture"] = {"index": 1}
            paint["occlusionTexture"] = {"index": 2}
            extra["materials"] += [
                {"name": "Plain", "pbrMetallicRoughness": {"baseColorFactor": [0.2, 0.4, 0.6, 1.0],
                                                           "baseColorTexture": {"index": 0}}},
                {"name": "MapsOnly", "normalTexture": {"index": 1},
                 "pbrMetallicRoughness": {"baseColorFactor": [0.5, 0.5, 0.5, 1.0], "metallicFactor": 0.25,
                                          "roughnessFactor": 0.5}}]

    hull_n, turret_n, gun_n = names
    turret_node = {"name": turret_n, "mesh": 1, "translation": list(_g(*turret_pivot))}
    gun_node = {"name": gun_n, "mesh": 2, "translation": list(_g(*gun_local_pivot))}
    nodes = [{"name": hull_n, "mesh": 0, "children": [1]}, turret_node, gun_node]
    if variant == "flat_gun":    # gun hangs off the hull, with its world pivot unchanged
        nodes[0]["children"] = [1, 2]
        gun_node["translation"] = list(_g(0, 0.8 * s, (1.75 + lift) * s))
    else:
        turret_node["children"] = [2]
    if variant == "rotated_turret":
        h = math.sqrt(0.5)
        turret_node["rotation"] = [0, h, 0, h]   # 90 deg about glTF Y (= model Z)

    roots = [0]
    for sname, pos in (sockets or {}).items():
        roots.append(len(nodes))
        nodes.append({"name": sname, "translation": list(_g(*pos))})

    gltf = {
        "asset": {"version": "2.0", "generator": "me3d mak test fixture"},
        "scene": 0,
        "scenes": [{"nodes": roots}],
        "nodes": nodes,
        "meshes": meshes, "accessors": accessors, "bufferViews": views,
        "buffers": [{"byteLength": len(buf)}],
        **extra,
    }
    js = json.dumps(gltf).encode()
    js += b" " * (-len(js) % 4)
    out = struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(buf))
    out += struct.pack("<II", len(js), 0x4E4F534A) + js
    out += struct.pack("<II", len(buf), 0x004E4942) + bytes(buf)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(out)
    return path


if __name__ == "__main__":
    print(make_glb(Path(__file__).parent / "fixtures" / "box_vehicle.glb"))
