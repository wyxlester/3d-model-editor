"""OpenFlight (.flt) writer and reader, standard library only: the Blender-free route.

Why OpenFlight: MAK supports OpenFlight and FBX models (manual 14.2-14.3). Only OpenFlight carries the
articulation inside the model, as WRM `@dis` comments on DOF nodes (manual 21.3-21.4), so no edits to the
install-wide FBX_NodeNameMap.csv are needed.

Records written (OpenFlight 16.1, big-endian):
  1 Header, 113 Material palette, 64 Texture palette, 67 Vertex palette + 70 vertices (colour/normal/UV),
  2 Group, 14 DOF, 73 LOD, 4 Object, 5 Face, 72 Vertex list, 10/11 Push/Pop, 31 Comment, 33 Long ID.

Layout of a written file:
  Header, palettes, Push
    Group <model name>        comment "@dis entity_type <DIS type>"
    Push
      Group | DOF | LOD per glTF node (same names and nesting), with Long ID for names over 7 chars
        DOF = declared articulated part: origin = pivot, local axes = model axes,
              limits on the motion axis, comment "@dis articulated_part <code> rotate_min p,r,y ..."
        LOD = node named High/Med/Low (FBX_NodeNameMap LOD names), comment "@dis lod ..."
        Push  Object  Push  Face Push VertexList Pop ...  Pop  <child nodes>  Pop
    Pop
  Pop
Vertices are absolute model coordinates (metres, X right, Y forward, Z up). DOF children stay in the parent
frame, as in OpenSceneGraph's DOFTransform put-matrix handling.

The comment format follows MAK's own models, as printed by `medfTool --writeNodeInfo`, e.g.
  "@dis articulated_part 4096    rotate_min 0,0,-180.0  rotate_current 0,0,0.0  rotate_max 0,0,180.0  rotate_step 0,0,45"
  "@dis articulated_part 4128    rotate_min -15.0,0,0 ..."      (pitch, roll, yaw)
"""
from __future__ import annotations

import datetime
import math
import struct
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import glb as glbmod
from .artparts import Part
from .common import Finding

FORMAT_REVISION = 1610
OP = {"header": 1, "group": 2, "object": 4, "face": 5, "push": 10, "pop": 11, "dof": 14, "comment": 31,
      "longid": 33, "texture": 64, "vpalette": 67, "vertex_c": 68, "vertex_cn": 69, "vertex_cnt": 70,
      "vertex_ct": 71, "vlist": 72, "lod": 73, "material": 113}

# FBX_NodeNameMap.csv LOD rows in the 5.1.1 install: High 50/0, Med 200/50, Low 1000/200
LOD_DEFAULTS = {"High": (0, 50.0, 0.0), "Med": (1, 200.0, 50.0), "Low": (2, 1000.0, 200.0)}

# DOF flag bits (most significant bit = bit 0)
DOF_LIMIT = {"x": 0x80000000, "y": 0x40000000, "z": 0x20000000,
             "pitch": 0x10000000, "roll": 0x08000000, "yaw": 0x04000000}
MOTION_AXIS = {"azimuth": "yaw", "elevation": "pitch", "rotation": "roll",
               "translate_x": "x", "translate_y": "y", "translate_z": "z", "extension": "y"}

FACE_PACKED_COLOR = 0x10000000
VERTEX_NO_COLOR = 0x2000
LIGHT_MODE_LIT = 2        # face colour + vertex normals ("Lit", manual 14.3)
WHITE = 0xFFFFFFFF


def _id8(name: str) -> bytes:
    return name.encode("ascii", "replace")[:7].ljust(8, b"\0")


def _text(s: str) -> bytes:
    b = s.encode("ascii", "replace") + b"\0"
    return b + b"\0" * (-len(b) % 4)


def _fmt(v: float) -> str:
    return f"{round(v, 6) + 0.0:g}"


def part_comment(part: Part) -> str:
    """WRM comment in the style of MAK's stock models."""
    if part.motion in ("azimuth", "elevation", "rotation"):
        axis = {"elevation": 0, "rotation": 1, "azimuth": 2}[part.motion]    # (pitch, roll, yaw)

        def vec(v):
            xs = ["0", "0", "0"]
            xs[axis] = _fmt(v)
            return ",".join(xs)
        return (f"@dis articulated_part {part.code}    rotate_min {vec(part.min)}  rotate_current {vec(0)}  "
                f"rotate_max {vec(part.max)}  rotate_step {vec(1)}")
    return f"@dis articulated_part {part.code}"


# ------------------------------------------------------------------ writer
class _Out:
    def __init__(self):
        self.buf = bytearray()

    def rec(self, opcode: int, body: bytes = b"") -> None:
        n = 4 + len(body)
        if n > 0xFFFF:
            raise ValueError(f"OpenFlight record {opcode} too long ({n} bytes)")
        self.buf += struct.pack(">hH", opcode, n) + body

    def named(self, opcode: int, name: str, body: bytes, comment: str | None = None) -> None:
        self.rec(opcode, body)
        if len(name) > 7:
            self.rec(OP["longid"], _text(name))
        if comment:
            self.rec(OP["comment"], _text(comment))


def _header(name: str) -> bytes:
    h = bytearray(320)
    struct.pack_into(">8sii32s", h, 0, _id8(name or "db"), FORMAT_REVISION, 0,
                     datetime.datetime.now().strftime("%a %b %d %H:%M:%S %Y").encode()[:31])
    struct.pack_into(">HHHHhbb", h, 48, 1, 1, 1, 1, 1, 0, 0)   # next ids, unit multiplier, units 0 = metres
    struct.pack_into(">hhi", h, 120, 1, 1, 100)                 # next DOF, vertex storage 1 = double, origin OpenFlight
    return bytes(h)


def _material(i: int, m: glbmod.Material) -> bytes:
    """Palette material is WHITE: the colour travels in the face colour instead (see packed_colour).
    MAK's renderer ignored palette diffuse colours in the hand-import test (the ship rendered white), while
    OpenSceneGraph multiplies material by face colour, so white x face colour is right for both."""
    spec = 0.04 + 0.5 * m.metallic * (1 - m.roughness)
    return struct.pack(">i12sI3f3f3f3fffi", i, m.name.encode("ascii", "replace")[:11].ljust(12, b"\0"), 0x80000000,
                       0.6, 0.6, 0.6, 1.0, 1.0, 1.0, spec, spec, spec, *m.emissive,
                       max(1.0, (1 - m.roughness) * 128.0), m.alpha, 0)


def packed_colour(rgb, alpha: float = 1.0) -> int:
    """OpenFlight packed colour: bytes a, b, g, r (OpenSceneGraph reads alpha, blue, green, red)."""
    r, g, b = (max(0, min(255, round(c * 255))) for c in rgb)
    a = max(0, min(255, round(alpha * 255)))
    return (a << 24) | (b << 16) | (g << 8) | r


def unpack_colour(v: int) -> tuple:
    return ((v & 0xFF) / 255, ((v >> 8) & 0xFF) / 255, ((v >> 16) & 0xFF) / 255, ((v >> 24) & 0xFF) / 255)


def _group(name: str) -> bytes:
    return struct.pack(">8shhihhhbbiiff", _id8(name), 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0.0, 0.0)


def _object(name: str) -> bytes:
    return struct.pack(">8sihHhhhh", _id8(name), 0, 0, 0, 0, 0, 0, 0)


def _dof(name: str, pivot, part: Part | None) -> bytes:
    o = tuple(pivot)
    px = (o[0] + 1.0, o[1], o[2])
    pxy = (o[0], o[1] + 1.0, o[2])
    lim = {k: (0.0, 0.0, 0.0, 0.0) for k in ("z", "y", "x", "pitch", "roll", "yaw")}
    flags = 0
    if part and part.motion in MOTION_AXIS:
        ax = MOTION_AXIS[part.motion]
        lim[ax] = (part.min, part.max, 0.0, 1.0)
        flags |= DOF_LIMIT[ax]
    scale = (1.0, 1.0, 1.0, 0.0)
    return struct.pack(">8si3d3d3d" + "4d" * 9 + "ii", _id8(name), 0, *o, *px, *pxy,
                       *lim["z"], *lim["y"], *lim["x"], *lim["pitch"], *lim["roll"], *lim["yaw"],
                       *scale, *scale, *scale, flags, 0)


def _lod(name: str, switch_in: float, switch_out: float, centre) -> bytes:
    return struct.pack(">8sid dhhi3ddd".replace(" ", ""), _id8(name), 0, switch_in, switch_out, 0, 0, 0,
                       *centre, 0.0, 0.0)


def _face(tex: int, mat: int, double_sided: bool, blend: bool, colour: int = WHITE, transparency: int = 0) -> bytes:
    return struct.pack(">8sihbbHHbbhhhhhiHBBiB7sIIhhIIhh",
                       _id8("p"), 0, 0, 1 if double_sided else 0, 0, 0, 0, 0, 1 if blend else 0,
                       -1, tex, mat, 0, 0, 0, transparency, 0, 0, FACE_PACKED_COLOR, LIGHT_MODE_LIT, b"\0" * 7,
                       colour, colour, -1, 0, 0, 0, 0, -1)


@dataclass
class WriteResult:
    path: Path
    textures: list[Path] = field(default_factory=list)
    faces: int = 0
    vertices: int = 0
    warnings: list[str] = field(default_factory=list)


def write_flt(model: glbmod.Model, out: Path, parts: list[Part], name: str, dis_type: str | None = None,
              lod_names: dict[str, tuple] | None = None) -> WriteResult:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    res = WriteResult(out, warnings=list(model.warnings))
    by_node = {p.node: p for p in parts}
    lods = lod_names if lod_names is not None else LOD_DEFAULTS

    # textures beside the model (manual: textures in the same directory as the model files)
    tex_index: dict[int, int] = {}
    for i, img in enumerate(model.images):
        if img.data is None:
            res.warnings.append(f"image '{img.name}' is external ({img.uri}); not copied")
            continue
        fname = f"{name}_tex{len(tex_index)}{img.ext}"
        (out.parent / fname).write_bytes(img.data)
        res.textures.append(out.parent / fname)
        tex_index[i] = len(tex_index)

    w = _Out()
    w.rec(OP["header"], _header(name))
    for i, m in enumerate(model.materials):
        w.rec(OP["material"], _material(i, m))
    for img_i, pat in tex_index.items():
        w.rec(OP["texture"], struct.pack(">200siii", res.textures[pat].name.encode()[:199].ljust(200, b"\0"), pat, 0, 0))

    # vertex palette: one 64-byte vertex record per triangle corner
    vbytes = bytearray()
    offsets: dict[str, list[tuple[int, int, int]]] = {}
    for nname, node in model.nodes.items():
        tri_offsets = []
        for t in node.triangles:
            o = []
            for k in range(3):
                o.append(8 + len(vbytes))
                u, v = t.uv[k]
                vbytes += struct.pack(">hHHH3d3f2fIIi", OP["vertex_cnt"], 64, 0, VERTEX_NO_COLOR,
                                      *t.p[k], *t.n[k], u, 1.0 - v, WHITE, 0, 0)
            tri_offsets.append(tuple(o))
        offsets[nname] = tri_offsets
    res.vertices = len(vbytes) // 64
    w.rec(OP["vpalette"], struct.pack(">i", 8 + len(vbytes)))
    w.buf += vbytes

    def faces(node: glbmod.Node):
        if not node.triangles:
            return
        w.named(OP["object"], f"o_{node.name}", _object(f"o_{node.name}"))
        w.rec(OP["push"])
        for t, offs in zip(node.triangles, offsets[node.name]):
            mat = model.materials[t.material] if t.material is not None and t.material < len(model.materials) else None
            tex = tex_index.get(mat.image, -1) if mat and mat.image is not None else -1
            blend = bool(mat and (mat.blend or mat.alpha < 1.0))
            # the material colour rides on the face; with a texture it tints it, as glTF baseColorFactor does
            colour = WHITE if mat is None else packed_colour(mat.diffuse, mat.alpha)
            transparency = round((1.0 - mat.alpha) * 65535) if blend else 0
            w.rec(OP["face"], _face(tex, -1 if t.material is None else t.material, bool(mat and mat.double_sided), blend,
                                    colour, transparency))
            w.rec(OP["push"])
            w.rec(OP["vlist"], struct.pack(">3i", *offs))
            w.rec(OP["pop"])
            res.faces += 1
        w.rec(OP["pop"])

    def emit(nname: str):
        node = model.nodes[nname]
        part = by_node.get(nname)
        if part:
            w.named(OP["dof"], nname, _dof(nname, node.pivot, part), part_comment(part))
        elif nname in lods:
            level, sin, sout = lods[nname]
            w.named(OP["lod"], nname, _lod(nname, sin, sout, node.pivot),
                    f"@dis lod {level} switch_in {_fmt(sin)} switch_out {_fmt(sout)}")
        else:
            w.named(OP["group"], nname, _group(nname))
        w.rec(OP["push"])
        faces(node)
        for c in node.children:
            emit(c)
        w.rec(OP["pop"])

    w.rec(OP["push"])
    w.named(OP["group"], name, _group(name), f"@dis entity_type {dis_type}" if dis_type else None)
    w.rec(OP["push"])
    for r in model.roots:
        emit(r)
    w.rec(OP["pop"])
    w.rec(OP["pop"])
    out.write_bytes(bytes(w.buf))
    missing = [p.node for p in parts if p.node not in model.nodes]
    if missing:
        res.warnings.append(f"articulated part nodes not in the model: {missing}")
    return res


# ------------------------------------------------------------------ reader
@dataclass
class FltNode:
    name: str
    kind: str
    parent: str | None
    comment: str = ""
    origin: tuple | None = None
    axes: tuple | None = None          # (point on x axis, point in xy plane)
    dof_flags: int = 0
    limits: dict = field(default_factory=dict)
    faces: int = 0
    bounds: list | None = None


@dataclass
class FltFile:
    revision: int
    units: int
    nodes: dict[str, FltNode]
    materials: int
    textures: list[str]
    faces: int
    face_colours: dict = field(default_factory=dict)    # (material index, packed colour) -> face count


def read_flt(path: Path) -> FltFile:
    """Parse the records this module writes (and common OpenFlight files) into a node tree."""
    data = Path(path).read_bytes()
    pos, n = 0, len(data)
    nodes: dict[str, FltNode] = {}
    stack: list[str | None] = []
    last: FltNode | None = None
    parent_obj_owner: dict[str, str] = {}
    palette = b""
    revision = units = 0
    materials, textures, total_faces = 0, [], 0
    face_colours: dict = {}
    face_pending = False
    owner_of_object: str | None = None

    def owner(name):
        # faces belong to the nearest non-Object ancestor
        while name and nodes[name].kind == "object":
            name = nodes[name].parent
        return name

    while pos + 4 <= n:
        op, length = struct.unpack_from(">hH", data, pos)
        if length < 4:
            raise ValueError(f"bad record length {length} at {pos}")
        body = data[pos + 4: pos + length]
        if op == OP["header"]:
            revision = struct.unpack_from(">i", body, 8)[0]
            units = body[58]
            last = None
        elif op == OP["material"]:
            materials += 1
        elif op == OP["texture"]:
            textures.append(body[:200].split(b"\0")[0].decode("ascii", "replace"))
        elif op == OP["vpalette"]:
            size = struct.unpack_from(">i", body, 0)[0]
            palette = data[pos: pos + size]
            pos += size
            continue
        elif op in (OP["group"], OP["object"], OP["dof"], OP["lod"]):
            kind = {OP["group"]: "group", OP["object"]: "object", OP["dof"]: "dof", OP["lod"]: "lod"}[op]
            name = body[:8].split(b"\0")[0].decode("ascii", "replace")
            parent = stack[-1] if stack else None
            node = FltNode(name, kind, parent)
            if kind == "dof":
                v = struct.unpack_from(">9d", body, 12)
                node.origin, node.axes = v[0:3], (v[3:6], v[6:9])
                lim = struct.unpack_from(">36d", body, 84)
                for i, k in enumerate(("z", "y", "x", "pitch", "roll", "yaw")):
                    node.limits[k] = lim[i * 4: i * 4 + 4]
                node.dof_flags = struct.unpack_from(">I", body, 372)[0]
            last = node
            key = name
            k = 1
            while key in nodes:
                key, k = f"{name}#{k}", k + 1
            node.name = key
            nodes[key] = node
        elif op == OP["longid"] and last is not None and last.kind != "face":
            long = body.split(b"\0")[0].decode("ascii", "replace")
            old = last.name
            if long != old and long not in nodes:
                nodes[long] = nodes.pop(old)
                last.name = long
                for nd in nodes.values():
                    if nd.parent == old:
                        nd.parent = long
                stack[:] = [long if s == old else s for s in stack]
        elif op == OP["comment"] and last is not None:
            last.comment = body.split(b"\0")[0].decode("ascii", "replace")
        elif op == OP["push"]:
            stack.append(last.name if last is not None and last.kind != "face" else (stack[-1] if stack else None))
            last = None
        elif op == OP["pop"]:
            if stack:
                stack.pop()
            last = None
        elif op == OP["face"]:
            total_faces += 1
            mat_index = struct.unpack_from(">h", body, 26)[0]
            colour = struct.unpack_from(">I", body, 52)[0]
            face_colours[(mat_index, colour)] = face_colours.get((mat_index, colour), 0) + 1
            last = FltNode("face", "face", stack[-1] if stack else None)
        elif op == OP["vlist"]:
            parent = stack[-2] if len(stack) >= 2 else None    # Face's push is on top
            own = owner(parent)
            for (off,) in struct.iter_unpack(">i", body):
                if off + 12 > len(palette):
                    raise ValueError(f"vertex offset {off} outside the vertex palette")
                vop = struct.unpack_from(">h", palette, off)[0]
                p = struct.unpack_from(">3d", palette, off + 8) if vop in (68, 69, 70, 71) else (0.0, 0.0, 0.0)
                if own:
                    b = nodes[own].bounds
                    nodes[own].bounds = [list(p), list(p)] if b is None else \
                        [[min(b[0][i], p[i]) for i in range(3)], [max(b[1][i], p[i]) for i in range(3)]]
            if own:
                nodes[own].faces += 1
        pos += length
    return FltFile(revision, units, nodes, materials, textures, total_faces, face_colours)


def as_dump(flt: FltFile) -> dict:
    """Same shape as blender_dump_fbx.py output, so verify_fbx.compare can check both formats."""
    objs = {}
    for name, nd in flt.nodes.items():
        if nd.kind == "object":
            continue
        parent = nd.parent
        while parent and flt.nodes[parent].kind == "object":
            parent = flt.nodes[parent].parent
        angle = 0.0
        if nd.kind == "dof" and nd.axes:
            ax = [nd.axes[0][i] - nd.origin[i] for i in range(3)]
            length = math.sqrt(sum(c * c for c in ax)) or 1.0
            angle = math.degrees(math.acos(max(-1.0, min(1.0, ax[0] / length))))
        objs[name] = {"type": nd.kind, "parent": parent,
                      "pivot": list(nd.origin) if nd.origin else [0.0, 0.0, 0.0],
                      "local_rotation_deg": angle, "local_scale": [1.0, 1.0, 1.0],
                      "bounds": {"min": nd.bounds[0], "max": nd.bounds[1]} if nd.bounds else None,
                      "comment": nd.comment}
    return {"objects": objs}


def expected_colours(model: glbmod.Model) -> dict[int, int]:
    return {i: packed_colour(m.diffuse, m.alpha) for i, m in enumerate(model.materials)}


def check_flt(flt: FltFile, parts: list[Part], dis_type: str | None,
              colours: dict[int, int] | None = None) -> list[Finding]:
    """OpenFlight-specific checks on top of verify_fbx.compare: comments, DOF limits, header, face colours."""
    f: list[Finding] = []
    if colours:
        wrong = {(m, c): n for (m, c), n in flt.face_colours.items() if m >= 0 and colours.get(m) != c}
        if wrong:
            (m, c), n = next(iter(wrong.items()))
            f.append(Finding("error", "flt-face-colour",
                             f"{sum(wrong.values())} face(s) don't carry their material colour, e.g. material {m}: "
                             f"{unpack_colour(c)} instead of {unpack_colour(colours.get(m, WHITE))}"))
    if flt.revision != FORMAT_REVISION:
        f.append(Finding("warning", "flt-revision", f"format revision {flt.revision}, expected {FORMAT_REVISION}"))
    if flt.units != 0:
        f.append(Finding("error", "flt-units", f"vertex units {flt.units}, expected 0 (metres)"))
    for p in parts:
        nd = flt.nodes.get(p.node)
        if nd is None:
            continue   # reported by compare()
        if nd.kind != "dof":
            f.append(Finding("error", "flt-part-not-dof", f"'{p.node}' is a {nd.kind} record, not a DOF"))
        if not nd.comment.startswith(f"@dis articulated_part {p.code}"):
            f.append(Finding("error", "flt-part-comment", f"'{p.node}' comment is '{nd.comment}', expected @dis articulated_part {p.code}"))
        ax = MOTION_AXIS.get(p.motion)
        if ax and nd.kind == "dof":
            lo, hi = nd.limits[ax][0], nd.limits[ax][1]
            if not (nd.dof_flags & DOF_LIMIT[ax]) or abs(lo - p.min) > 1e-6 or abs(hi - p.max) > 1e-6:
                f.append(Finding("error", "flt-dof-limits", f"'{p.node}' {ax} limits {lo}..{hi}, expected {p.min}..{p.max}"))
    if dis_type and not any(nd.comment == f"@dis entity_type {dis_type}" for nd in flt.nodes.values()):
        f.append(Finding("warning", "flt-entity-type", f"no '@dis entity_type {dis_type}' comment on the root"))
    return f


# ------------------------------------------------------------------ MAK-side check
def mak_node_info(flt_path: Path, medf_tool: str, timeout: int = 300) -> dict[str, list[str]]:
    """Load the model with MAK's own reader (medfTool --writeNodeInfo) and return {node name: [comments]}."""
    with tempfile.TemporaryDirectory() as tmp:
        info = Path(tmp) / "info.txt"
        res = subprocess.run([medf_tool, "-f", str(flt_path), "--writeNodeInfo", str(info), "--outputDirectory", tmp],
                             capture_output=True, text=True, timeout=timeout)
        if res.returncode != 0 or not info.exists():
            tail = "\n".join((res.stdout + res.stderr).splitlines()[-15:])
            raise RuntimeError(f"medfTool could not load {flt_path.name} (exit {res.returncode}):\n{tail}")
        converted = [p for p in Path(tmp).iterdir() if p.suffix.lower() in (".ive", ".medf")]
        out: dict[str, list[str]] = {}
        for line in info.read_text(encoding="utf-8", errors="replace").splitlines()[1:]:
            if " - " in line:
                name, comment = line.split(" - ", 1)
                out.setdefault(name.strip(), []).append(comment.strip())
        out["__converted_bytes__"] = [str(sum(p.stat().st_size for p in converted))]
        return out


def check_mak(info: dict[str, list[str]], parts: list[Part], dis_type: str | None) -> list[Finding]:
    f: list[Finding] = []
    if int(info.get("__converted_bytes__", ["0"])[0]) == 0:
        f.append(Finding("error", "mak-no-geometry", "medfTool loaded the file but produced no geometry"))
    for p in parts:
        comments = info.get(p.node, [])
        if not any(c.startswith(f"@dis articulated_part {p.code}") for c in comments):
            f.append(Finding("error", "mak-part-missing", f"MAK's reader does not see '{p.node}' with @dis articulated_part {p.code} (got {comments})"))
    if dis_type and not any(f"@dis entity_type {dis_type}" in c for cs in info.values() for c in cs):
        f.append(Finding("warning", "mak-entity-type", "MAK's reader does not see the @dis entity_type comment"))
    if not f:
        f.append(Finding("info", "mak-loaded", f"medfTool loaded the model; {len(parts)} part(s) carry @dis articulated_part"))
    return f
