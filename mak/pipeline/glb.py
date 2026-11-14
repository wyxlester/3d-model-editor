"""Full glb geometry reader (standard library only), for writing other formats without Blender.

load(path, forward_axis) returns the node tree with every triangle in the MAK model frame
(X right, Y forward, Z up; manual 21.2), using the same glTF -> Blender -> forward-axis mapping as
inspect_glb. Positions are absolute (world) coordinates, as OpenFlight expects under DOF nodes.

Supported: triangle primitives (mode 4, indexed or not), POSITION, NORMAL, TEXCOORD_0, materials
(pbrMetallicRoughness base colour/texture, emissive, alpha mode, double-sided), embedded images.
Not supported (reported as warnings): sparse accessors, non-triangle modes, external buffers, skins, morph targets.
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field
from pathlib import Path

from .inspect_glb import _apply, _mat_identity, _mat_mul, _node_matrix, read_glb, to_mak_fn

COMP = {5120: ("b", 1, 127.0), 5121: ("B", 1, 255.0), 5122: ("h", 2, 32767.0),
        5123: ("H", 2, 65535.0), 5125: ("I", 4, None), 5126: ("f", 4, None)}
NCOMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16}


@dataclass
class Material:
    name: str
    diffuse: tuple = (0.8, 0.8, 0.8)
    alpha: float = 1.0
    emissive: tuple = (0.0, 0.0, 0.0)
    metallic: float = 1.0
    roughness: float = 1.0
    image: int | None = None          # index into Model.images
    double_sided: bool = False
    blend: bool = False


@dataclass
class Image:
    name: str
    mime: str | None
    data: bytes | None
    uri: str | None

    @property
    def ext(self) -> str:
        if self.mime == "image/jpeg":
            return ".jpg"
        if self.mime == "image/png":
            return ".png"
        if self.uri and "." in self.uri:
            return "." + self.uri.rsplit(".", 1)[1].lower()
        return ".png"


@dataclass
class Triangle:
    p: tuple            # three (x, y, z) in MAK frame
    n: tuple            # three unit normals
    uv: tuple           # three (u, v), glTF convention (v down)
    material: int | None


@dataclass
class Node:
    name: str
    parent: str | None
    children: list[str] = field(default_factory=list)
    pivot: tuple = (0.0, 0.0, 0.0)   # MAK frame, world
    triangles: list[Triangle] = field(default_factory=list)


@dataclass
class Model:
    nodes: dict[str, Node]
    roots: list[str]
    materials: list[Material]
    images: list[Image]
    warnings: list[str]

    @property
    def triangle_count(self) -> int:
        return sum(len(n.triangles) for n in self.nodes.values())


def _accessor(gltf: dict, binchunk: bytes, idx: int) -> list[tuple]:
    a = gltf["accessors"][idx]
    if "sparse" in a:
        raise ValueError(f"accessor {idx}: sparse accessors are not supported")
    fmt, size, norm = COMP[a["componentType"]]
    n = NCOMP[a["type"]]
    count = a["count"]
    if "bufferView" not in a:
        return [(0,) * n] * count
    bv = gltf["bufferViews"][a["bufferView"]]
    if bv.get("buffer", 0) != 0:
        raise ValueError("only the glb's embedded buffer is supported")
    base = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
    stride = bv.get("byteStride") or size * n
    st = struct.Struct("<" + fmt * n)
    out = [st.unpack_from(binchunk, base + k * stride) for k in range(count)]
    if a.get("normalized") and norm:
        out = [tuple(max(v / norm, -1.0) for v in t) for t in out]
    return out


def _normalise(v):
    length = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
    return (v[0] / length, v[1] / length, v[2] / length) if length > 1e-12 else (0.0, 0.0, 1.0)


def _det3(m) -> float:
    return (m[0] * (m[5] * m[10] - m[9] * m[6]) - m[4] * (m[1] * m[10] - m[9] * m[2])
            + m[8] * (m[1] * m[6] - m[5] * m[2]))


def load(path: Path, forward_axis: str = "+Y") -> Model:
    gltf, binchunk = read_glb(Path(path))
    to_mak = to_mak_fn(forward_axis)
    warnings: list[str] = []

    images = []
    for i, img in enumerate(gltf.get("images", [])):
        data = None
        if "bufferView" in img:
            bv = gltf["bufferViews"][img["bufferView"]]
            data = binchunk[bv.get("byteOffset", 0): bv.get("byteOffset", 0) + bv["byteLength"]]
        elif img.get("uri", "").startswith("data:"):
            warnings.append(f"image {i}: data URIs are not supported; texture dropped")
        images.append(Image(img.get("name") or f"image{i}", img.get("mimeType"), data, img.get("uri")))

    textures = gltf.get("textures", [])
    materials = []
    for i, m in enumerate(gltf.get("materials", [])):
        pbr = m.get("pbrMetallicRoughness", {})
        base = pbr.get("baseColorFactor", [1, 1, 1, 1])
        tex = pbr.get("baseColorTexture")
        image = textures[tex["index"]].get("source") if tex and tex["index"] < len(textures) else None
        if image is not None and images[image].data is None and not images[image].uri:
            image = None
        materials.append(Material(
            name=m.get("name") or f"mat{i}", diffuse=tuple(base[:3]), alpha=float(base[3]),
            emissive=tuple(m.get("emissiveFactor", [0, 0, 0])),
            metallic=float(pbr.get("metallicFactor", 1.0)), roughness=float(pbr.get("roughnessFactor", 1.0)),
            image=image, double_sided=bool(m.get("doubleSided")), blend=m.get("alphaMode") == "BLEND"))

    gnodes, meshes = gltf.get("nodes", []), gltf.get("meshes", [])
    nodes: dict[str, Node] = {}
    roots: list[str] = []
    if any("skin" in n for n in gnodes) or any("targets" in p for m in meshes for p in m.get("primitives", [])):
        warnings.append("skins and morph targets are ignored (static geometry only)")

    def unique(name: str) -> str:
        if name not in nodes:
            return name
        k = 1
        while f"{name}_{k}" in nodes:
            k += 1
        warnings.append(f"duplicate node name '{name}' written as '{name}_{k}'")
        return f"{name}_{k}"

    def walk(idx: int, parent_m, parent: str | None):
        gn = gnodes[idx]
        m = _mat_mul(parent_m, _node_matrix(gn))
        name = unique(gn.get("name") or f"node{idx}")
        node = Node(name, parent, pivot=to_mak((m[12], m[13], m[14])))
        nodes[name] = node
        if parent:
            nodes[parent].children.append(name)
        else:
            roots.append(name)
        if "mesh" in gn:
            flip = _det3(m) < 0
            nm = [m[0], m[1], m[2], m[4], m[5], m[6], m[8], m[9], m[10]]
            for pi, prim in enumerate(meshes[gn["mesh"]].get("primitives", [])):
                mode = prim.get("mode", 4)
                if mode != 4:
                    warnings.append(f"{name}: primitive {pi} mode {mode} skipped (only triangles)")
                    continue
                attrs = prim["attributes"]
                pos = _accessor(gltf, binchunk, attrs["POSITION"])
                nor = _accessor(gltf, binchunk, attrs["NORMAL"]) if "NORMAL" in attrs else None
                uvs = _accessor(gltf, binchunk, attrs["TEXCOORD_0"]) if "TEXCOORD_0" in attrs else None
                idx_list = [i[0] for i in _accessor(gltf, binchunk, prim["indices"])] if "indices" in prim \
                    else list(range(len(pos)))
                wpos = [to_mak(_apply(m, p)) for p in pos]
                if nor:
                    wnor = [_normalise(to_mak((nm[0] * n[0] + nm[3] * n[1] + nm[6] * n[2],
                                               nm[1] * n[0] + nm[4] * n[1] + nm[7] * n[2],
                                               nm[2] * n[0] + nm[5] * n[1] + nm[8] * n[2]))) for n in nor]
                for t in range(0, len(idx_list) - 2, 3):
                    tri = [idx_list[t], idx_list[t + 1], idx_list[t + 2]]
                    if flip:
                        tri = [tri[0], tri[2], tri[1]]
                    p = tuple(wpos[i] for i in tri)
                    if nor:
                        n = tuple(wnor[i] for i in tri)
                    else:   # flat normal
                        a, b, c = p
                        u = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
                        v = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
                        fn = _normalise((u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]))
                        n = (fn, fn, fn)
                    uv = tuple(tuple(uvs[i][:2]) for i in tri) if uvs else ((0.0, 0.0),) * 3
                    node.triangles.append(Triangle(p, n, uv, prim.get("material")))
        for c in gn.get("children", []):
            walk(c, m, name)

    scene = gltf["scenes"][gltf.get("scene", 0)] if gltf.get("scenes") else {"nodes": list(range(len(gnodes)))}
    for r in scene.get("nodes", []):
        walk(r, _mat_identity(), None)
    return Model(nodes, roots, materials, images, warnings)


def waterline_supports(model: Model, front: float = 0.3, rear: float = 0.35, inset: float = 0.9) -> dict | None:
    """Ship support points on the waterline (z = 0, the surface-domain origin plane; manual 21.2.1).

    Stock surface ships keep their supports at the waterline (144 of 159 in EntityLevel have down = 0), in the
    stock pattern: a front pair (left/right) and a rear centre point. The Simulation Object Editor's
    "Calculate Support Points" button puts them on the keel instead (9.3 m down on Harmony of the Seas), which
    is not what MAK's own ships do. Positions come from where the hull crosses z = 0: the front pair sits
    `front` x waterline length ahead of its middle, `inset` x the hull's half-beam at that station; the rear
    point `rear` x length behind. Returns body-frame (forward, right, down) points, or None when nothing
    crosses the waterline (e.g. a submarine model)."""
    pts = []
    for node in model.nodes.values():
        for t in node.triangles:
            for a, b in ((0, 1), (1, 2), (2, 0)):
                p, q = t.p[a], t.p[b]
                if p[2] != q[2] and min(p[2], q[2]) <= 0.0 <= max(p[2], q[2]):
                    s = -p[2] / (q[2] - p[2])
                    pts.append((p[0] + s * (q[0] - p[0]), p[1] + s * (q[1] - p[1])))
    if not pts:
        return None
    y0, y1 = min(p[1] for p in pts), max(p[1] for p in pts)
    length = y1 - y0
    mid = (y0 + y1) / 2
    yf, yr = mid + front * length, mid - rear * length

    def half_beam(y: float) -> float:
        for w in (0.03, 0.06, 0.12, 0.5):          # widen the station until the hull is hit
            near = [abs(p[0]) for p in pts if abs(p[1] - y) <= w * length]
            if near:
                return max(near)
        return max(abs(p[0]) for p in pts)

    side = inset * half_beam(yf)
    cx = (min(p[0] for p in pts) + max(p[0] for p in pts)) / 2
    r = lambda v: round(v, 3) + 0.0   # noqa: E731
    return {"left": [r(yf), r(cx - side), 0.0], "right": [r(yf), r(cx + side), 0.0], "other": [r(yr), r(cx), 0.0],
            "waterline_length": r(length), "waterline_beam": r(2 * max(abs(p[0] - cx) for p in pts))}
