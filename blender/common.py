"""Shared helpers for every builder that runs inside Blender (importable as ``me3d``).

Contract: docs/CONTRACT.md. Frame is metres, +X bow, +Y port, +Z up. Ships have their origin at
the waterline amidships. Every part object is named ``Group__Part`` or ``Group__Part__Sub``.

Builders use ``MeshBuilder`` to accumulate many primitives into one object (one part), then
``MeshBuilder.to_object(name, pivot=..., parent=...)``. Custom ops (AI-written code) get this
module in scope as ``me3d`` and normally use the one-shot helpers ``box``/``cylinder``.

Build-time convention: objects are created unrotated, so a parent's world position is the sum of
the locations up its chain. Overrides may rotate parts later; nothing after that relies on it.
"""

import json
import math
import os
import re
import time
import traceback

import bmesh
import bpy
from mathutils import Matrix, Vector

# --------------------------------------------------------------------------------------------
# Names (contract section 2)
# --------------------------------------------------------------------------------------------

_SEG = r"[A-Za-z0-9]+(?:_[A-Za-z0-9]+)*"
PART_RE = re.compile(rf"^{_SEG}__{_SEG}(?:__{_SEG})?$")
STATION_RE = re.compile(r"^station__[a-z0-9]+(?:_[a-z0-9]+)*$")
SOCKETS = (
    "socket_bridge_eye", "socket_radar", "socket_light_masthead", "socket_light_masthead_aft",
    "socket_light_port", "socket_light_stbd", "socket_light_stern", "socket_bow", "socket_prop",
)


def part_name(group, part, sub=None):
    """Build a part name and validate it: part_name("Bridge", "Wing_Port") -> "Bridge__Wing_Port"."""
    name = f"{group}__{part}" + (f"__{sub}" if sub else "")
    if not PART_RE.match(name):
        raise ValueError(f"bad part name {name!r}: use Group__Part[__Sub] with [A-Za-z0-9_] only")
    return name


def is_part_name(name):
    return bool(PART_RE.match(name))


def is_station_name(name):
    return bool(STATION_RE.match(name))


def label(name):
    """Display label: "Bridge__Wing_Port" -> "Bridge › Wing Port"."""
    return name.replace("__", " › ").replace("_", " ")


def classify(name):
    """'part', 'station', 'socket' or None (an illegal name)."""
    if name in SOCKETS:
        return "socket"
    if is_station_name(name):
        return "station"
    if is_part_name(name):
        return "part"
    return None


# --------------------------------------------------------------------------------------------
# Scene and materials
# --------------------------------------------------------------------------------------------

_MATS = {}
_SOCKET_FOLLOW = {}  # socket name -> (followed object name, offset in that object's local frame)


def reset_scene():
    """Empty the file: factory startup still has a cube, a camera and a light."""
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob, do_unlink=True)
    for coll in (bpy.data.meshes, bpy.data.materials, bpy.data.cameras, bpy.data.lights):
        for block in list(coll):
            coll.remove(block)
    _MATS.clear()
    _SOCKET_FOLLOW.clear()


def material(color, name=None, roughness=0.6, metallic=0.0, double_sided=False):
    """A flat principled material; cached by (colour, settings). Survives glTF as baseColorFactor."""
    rgb = tuple(round(float(c), 4) for c in color[:3])
    key = (rgb, round(roughness, 3), round(metallic, 3), double_sided, name)
    if key in _MATS:
        return _MATS[key]
    mname = name or "mat_%02x%02x%02x" % tuple(int(max(0, min(1, c)) * 255) for c in rgb)
    mat = bpy.data.materials.new(mname)
    mat.use_nodes = True
    bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
        bsdf.inputs["Roughness"].default_value = roughness
        bsdf.inputs["Metallic"].default_value = metallic
    mat.diffuse_color = (*rgb, 1.0)  # what Workbench shows
    mat.use_backface_culling = not double_sided  # glTF doubleSided follows this
    _MATS[key] = mat
    return mat


def _as_mat(m):
    if m is None:
        return material((0.7, 0.7, 0.7))
    if isinstance(m, bpy.types.Material):
        return m
    return material(m)


# --------------------------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------------------------

class MeshBuilder:
    """Accumulates primitives in WORLD coordinates; to_object() makes one part from them."""

    def __init__(self):
        self.verts = []
        self.faces = []
        self.face_mats = []
        self.mats = []

    def _mi(self, mat):
        mat = _as_mat(mat)
        if mat not in self.mats:
            self.mats.append(mat)
        return self.mats.index(mat)

    def add(self, verts, faces, mat):
        base = len(self.verts)
        mi = self._mi(mat)
        self.verts.extend(Vector(v) for v in verts)
        for f in faces:
            self.faces.append([base + i for i in f])
            self.face_mats.append(mi)
        return self

    def add_faces(self, verts, faces, mats):
        """Like add() but with one material per face."""
        base = len(self.verts)
        self.verts.extend(Vector(v) for v in verts)
        for f, m in zip(faces, mats):
            self.faces.append([base + i for i in f])
            self.face_mats.append(self._mi(m))
        return self

    def box(self, center, size, mat):
        cx, cy, cz = center
        hx, hy, hz = (s / 2.0 for s in size)
        v = [(cx + sx * hx, cy + sy * hy, cz + sz * hz)
             for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)]
        f = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
        return self.add(v, f, mat)

    def box_oriented(self, center, size, rot, mat):
        """Box of ``size`` (local x, y, z) rotated by 3x3 ``rot`` about its centre."""
        c = Vector(center)
        hx, hy, hz = (s / 2.0 for s in size)
        v = [c + rot @ Vector((sx * hx, sy * hy, sz * hz))
             for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)]
        f = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
        return self.add(v, f, mat)

    def box_minmax(self, lo, hi, mat):
        c = [(a + b) / 2 for a, b in zip(lo, hi)]
        s = [abs(b - a) for a, b in zip(lo, hi)]
        return self.box(c, s, mat)

    def cylinder(self, p0, p1, radius, mat, segments=12, radius1=None, caps=True):
        """Cylinder (or cone, with radius1) from p0 to p1."""
        p0, p1 = Vector(p0), Vector(p1)
        axis = p1 - p0
        length = axis.length
        if length < 1e-9:
            return self
        rot = axis.normalized().to_track_quat("Z", "Y").to_matrix()
        r1 = radius if radius1 is None else radius1
        v, f = [], []
        for k in range(segments):
            a = 2 * math.pi * k / segments
            d = Vector((math.cos(a), math.sin(a), 0.0))
            v.append(p0 + rot @ (d * radius))
            v.append(p0 + rot @ (d * r1 + Vector((0, 0, length))))
        for k in range(segments):
            k2 = (k + 1) % segments
            f.append((2 * k, 2 * k2, 2 * k2 + 1, 2 * k + 1))
        if caps:
            f.append(tuple(2 * k for k in reversed(range(segments))))
            f.append(tuple(2 * k + 1 for k in range(segments)))
        return self.add(v, f, mat)

    def ellipsoid(self, center, radii, mat, segments=12, rings=8):
        c = Vector(center)
        v = [c + Vector((0, 0, -radii[2]))]
        for r in range(1, rings):
            phi = -math.pi / 2 + math.pi * r / rings
            for s in range(segments):
                th = 2 * math.pi * s / segments
                v.append(c + Vector((radii[0] * math.cos(phi) * math.cos(th),
                                     radii[1] * math.cos(phi) * math.sin(th),
                                     radii[2] * math.sin(phi))))
        v.append(c + Vector((0, 0, radii[2])))
        top = len(v) - 1
        f = []
        for s in range(segments):
            s2 = (s + 1) % segments
            f.append((0, 1 + s2, 1 + s))
        for r in range(rings - 2):
            b = 1 + r * segments
            for s in range(segments):
                s2 = (s + 1) % segments
                f.append((b + s, b + s2, b + segments + s2, b + segments + s))
        b = 1 + (rings - 2) * segments
        for s in range(segments):
            s2 = (s + 1) % segments
            f.append((b + s, b + s2, top))
        return self.add(v, f, mat)

    def prism_xz(self, poly_xz, y0, y1, mat):
        """A flat plate: polygon in the XZ plane extruded from y0 to y1 (skegs, rudders, funnels)."""
        n = len(poly_xz)
        v = [(x, y0, z) for x, z in poly_xz] + [(x, y1, z) for x, z in poly_xz]
        f = [tuple(range(n)), tuple(n + i for i in reversed(range(n)))]
        for i in range(n):
            j = (i + 1) % n
            f.append((i, j, n + j, n + i))
        return self.add(v, f, mat)

    def prism_xy(self, poly_xy, z0, z1, mat):
        """Polygon in the XY plane extruded from z0 to z1 (decks, platforms following a hull)."""
        n = len(poly_xy)
        v = [(x, y, z0) for x, y in poly_xy] + [(x, y, z1) for x, y in poly_xy]
        f = [tuple(reversed(range(n))), tuple(n + i for i in range(n))]
        for i in range(n):
            j = (i + 1) % n
            f.append((i, j, n + j, n + i))
        return self.add(v, f, mat)

    def loft(self, sections, mat_fn, close_top=True, cap_aft=True, top_mat=None):
        """Symmetric loft. ``sections`` run aft to fore; each is a list of (x, y, z) points on the
        PORT half (y >= 0) from the top edge down to the centreline keel point (y = 0). The
        starboard half is mirrored. ``mat_fn(z_centroid)`` picks a material per face;
        ``close_top`` adds the deck between port and starboard top edges; ``cap_aft`` closes the
        aft section (a transom). Degenerate faces (a pointed stem) are removed afterwards."""
        n = len(sections[0])
        if any(len(s) != n for s in sections):
            raise ValueError("loft sections must all have the same number of points")
        ring_len = 2 * n - 1  # stbd top..keel..port top (keel shared)
        verts, faces, mats = [], [], []
        for sec in sections:
            port = [Vector(p) for p in sec]
            stbd = [Vector((p[0], -p[1], p[2])) for p in sec]
            ring = stbd + list(reversed(port[:-1]))  # stbd top -> keel -> port top
            verts.extend(ring)
        for i in range(len(sections) - 1):
            a, b = i * ring_len, (i + 1) * ring_len
            for k in range(ring_len - 1):
                q = (a + k, b + k, b + k + 1, a + k + 1)
                zc = sum(verts[j].z for j in q) / 4
                faces.append(q)
                mats.append(mat_fn(zc))
            if close_top:
                faces.append((a + ring_len - 1, b + ring_len - 1, b, a))
                mats.append(top_mat or mat_fn(1e9))
        if cap_aft:
            faces.append(tuple(range(ring_len)))
            mats.append(mat_fn(sum(v.z for v in verts[:ring_len]) / ring_len))
        bm = bmesh.new()
        bv = [bm.verts.new(v) for v in verts]
        for f, m in zip(faces, mats):
            try:
                face = bm.faces.new([bv[i] for i in f])
            except ValueError:
                continue
            face.material_index = self._mi(m)
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-4)
        bmesh.ops.dissolve_degenerate(bm, edges=bm.edges, dist=1e-4)
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        # open shells (a sampan) can come out inside-out: outward means away from the centreline
        zs = [v.co.z for v in bm.verts]
        zmid = (min(zs) + max(zs)) / 2
        score = sum(f.normal.dot(f.calc_center_median() - Vector((f.calc_center_median().x, 0,
                                                                   zmid))) * f.calc_area()
                    for f in bm.faces)
        if score < 0:
            for f in bm.faces:
                f.normal_flip()
        bm.verts.ensure_lookup_table()
        idx = {v: k for k, v in enumerate(bm.verts)}
        base = len(self.verts)
        self.verts.extend(v.co.copy() for v in bm.verts)
        for face in bm.faces:
            self.faces.append([base + idx[v] for v in face.verts])
            self.face_mats.append(face.material_index)
        bm.free()
        return self

    def to_object(self, name, pivot=(0.0, 0.0, 0.0), parent=None, recalc_normals=True):
        """Make the part. ``pivot`` is the world position of the object origin (its joint pivot)."""
        if not (is_part_name(name)):
            raise ValueError(f"bad part name {name!r}: use Group__Part[__Sub] with [A-Za-z0-9_]")
        if name in bpy.data.objects:
            raise ValueError(f"part {name!r} already exists")
        pv = Vector(pivot)
        me = bpy.data.meshes.new(name)
        me.from_pydata([v - pv for v in self.verts], [], self.faces)
        for m in self.mats:
            me.materials.append(m)
        for poly, mi in zip(me.polygons, self.face_mats):
            poly.material_index = mi
        me.validate(clean_customdata=False)
        if recalc_normals:
            bm = bmesh.new()
            bm.from_mesh(me)
            bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
            bm.to_mesh(me)
            bm.free()
        me.update()
        ob = bpy.data.objects.new(name, me)
        bpy.context.scene.collection.objects.link(ob)
        place(ob, pv, parent)
        return ob


def world_pos(ob):
    """Build-time world position (objects are unrotated while building)."""
    p = Vector(ob.location)
    q = ob.parent
    while q is not None:
        p += q.location
        q = q.parent
    return p


def place(ob, world, parent=None):
    """Put ``ob``'s origin at ``world`` and parent it, keeping that world position."""
    if parent is not None:
        if isinstance(parent, str):
            parent = bpy.data.objects[parent]
        ob.parent = parent
        ob.matrix_parent_inverse = Matrix.Identity(4)
        ob.location = Vector(world) - world_pos(parent)
    else:
        ob.location = Vector(world)
    return ob


def box(name, center, size, color=(0.7, 0.7, 0.7), parent=None, pivot=None):
    """One-shot box part (handy in custom ops)."""
    return MeshBuilder().box(center, size, color).to_object(
        name, pivot=pivot if pivot is not None else center, parent=parent)


def cylinder(name, p0, p1, radius, color=(0.7, 0.7, 0.7), parent=None, pivot=None, segments=16):
    """One-shot cylinder part from p0 to p1 (handy in custom ops)."""
    return MeshBuilder().cylinder(p0, p1, radius, color, segments=segments).to_object(
        name, pivot=pivot if pivot is not None else p0, parent=parent)


def obj(name):
    """Look a part up by path; raises KeyError with the list of near names when missing."""
    ob = bpy.data.objects.get(name)
    if ob is None:
        near = [o.name for o in bpy.data.objects if name.split("__")[0] in o.name][:10]
        raise KeyError(f"no part {name!r}; similar: {near}")
    return ob


# --------------------------------------------------------------------------------------------
# Joints, stations, sockets (contract sections 2 and 3)
# --------------------------------------------------------------------------------------------

def set_joint(ob, axis, min_deg, max_deg, priority="P1", kind="revolute", role=None, chain=None):
    if axis not in ("x", "y", "z"):
        raise ValueError("joint axis must be x, y or z")
    if priority not in ("P0", "P1", "P2"):
        raise ValueError("joint priority must be P0, P1 or P2")
    if ob.parent is None:
        raise ValueError(f"moving part {ob.name} must have a parent")
    data = {"axis": axis, "min_deg": float(min_deg), "max_deg": float(max_deg), "priority": priority, "kind": kind}
    if role is not None:                       # contract §3b: made by add_chain
        data.update(role=str(role), chain=int(chain or 0))
    ob["me3d_joint"] = json.dumps(data)
    return ob


def get_joint(ob):
    raw = ob.get("me3d_joint")
    return json.loads(raw) if raw else None


ROLES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "catalog", "articulation", "roles.json")
_ROLES = {}


def roles():
    """The articulation roles (catalog/articulation/roles.json, contract §3b), by id."""
    if not _ROLES:
        with open(ROLES_PATH, encoding="utf-8") as f:
            _ROLES.update({r["id"]: r for r in json.load(f)["roles"]})
    return _ROLES


def add_chain(role, name, parent, pivots, priority="P1", limits=None):
    """Make a moving component's joints from its articulation role: returns one Empty per joint, nested.

    ``role`` is a role id (gun_mount, trainable_launcher, rotating_radar, rudder ...). ``name`` is the top joint,
    Group__Part (Weapons__Phalanx_1); the joints under it are named ``<name>__<Suffix>`` (Weapons__Phalanx_1__Gun).
    ``parent`` is the part it sits on. ``pivots`` is the world position of each joint, top first (a shorter list
    repeats its last point). ``limits`` overrides by suffix ("" is the top joint): {"Gun": (-10, 80)}.
    Each Empty's origin is its pivot and it carries me3d_joint with the role's axis and limits. Put the
    geometry under the joint that moves it: the barrel under ...__Gun, the housing under the mount. Every joint
    needs some geometry of its own, even a fixed one (a launcher's front face): MAK refuses an empty DOF (21.4)."""
    spec = roles().get(role)
    if spec is None:
        raise ValueError(f"unknown articulation role {role!r}; roles: {sorted(roles())}")
    if not re.match(rf"^{_SEG}__{_SEG}$", name):
        raise ValueError(f"chain name {name!r} must be Group__Part; the joints under it get __<Suffix>")
    pts = [Vector(p) for p in (pivots if pivots and not isinstance(pivots[0], (int, float)) else [pivots])]
    if not pts:
        raise ValueError("add_chain needs at least one pivot")
    out, up = [], parent
    for i, j in enumerate(spec["joints"]):
        full = f"{name}__{j['suffix']}" if j["suffix"] else name
        if full in bpy.data.objects:
            raise ValueError(f"part {full!r} already exists")
        ob = bpy.data.objects.new(full, None)
        ob.empty_display_type = "PLAIN_AXES"
        ob.empty_display_size = 0.3
        bpy.context.scene.collection.objects.link(ob)
        place(ob, pts[min(i, len(pts) - 1)], up)
        lo, hi = (limits or {}).get(j["suffix"], j["limits"])
        set_joint(ob, j["axis"], lo, hi, priority, j["kind"], role=role, chain=i)
        out.append(ob)
        up = ob
    return out


def add_station(name, location, parent, pose="stand", activities=(), label_text=None, yaw_deg=0.0):
    """A crew station Empty ``station__<name>``; +X local is the way the person faces."""
    full = name if name.startswith("station__") else f"station__{name}"
    if not is_station_name(full):
        raise ValueError(f"bad station name {full!r}: station__<lower_snake>")
    if pose not in ("stand", "sit"):
        raise ValueError("station pose must be stand or sit")
    ob = bpy.data.objects.new(full, None)
    ob.empty_display_type = "ARROWS"
    ob.empty_display_size = 0.6
    bpy.context.scene.collection.objects.link(ob)
    place(ob, location, parent)
    ob.rotation_euler = (0.0, 0.0, math.radians(yaw_deg))
    ob["me3d_station"] = json.dumps({"pose": pose, "activities": list(activities),
                                     "label": label_text or label(full)})
    return ob


def add_socket(name, location, follow=None):
    """A StraitsSim socket Empty. Sockets stay unparented (StraitsSim's validator reads node
    translation only), but they remember the part they belong to and are moved with it after
    overrides and custom ops (``update_sockets``)."""
    if name not in SOCKETS:
        raise ValueError(f"{name!r} is not one of the nine StraitsSim sockets")
    ob = bpy.data.objects.get(name)
    if ob is None:
        ob = bpy.data.objects.new(name, None)
        ob.empty_display_type = "PLAIN_AXES"
        ob.empty_display_size = 0.5
        bpy.context.scene.collection.objects.link(ob)
    ob.location = Vector(location)
    if follow is not None:
        if isinstance(follow, str):
            follow = bpy.data.objects[follow]
        bpy.context.view_layer.update()
        _SOCKET_FOLLOW[name] = (follow.name, follow.matrix_world.inverted() @ Vector(location))
    return ob


def update_sockets():
    bpy.context.view_layer.update()
    for name, (fname, local) in _SOCKET_FOLLOW.items():
        ob, f = bpy.data.objects.get(name), bpy.data.objects.get(fname)
        if ob is not None and f is not None:
            ob.location = f.matrix_world @ local


# --------------------------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------------------------

class Report:
    def __init__(self):
        self.t0 = time.time()
        self.errors = []
        self.warnings = []

    def error(self, msg):
        self.errors.append(str(msg))

    def warn(self, msg):
        self.warnings.append(str(msg))


REPORT = Report()


# --------------------------------------------------------------------------------------------
# Spec application (contract section 5)
# --------------------------------------------------------------------------------------------

def apply_overrides(spec, report=None):
    report = report or REPORT
    for path, ov in (spec.get("overrides") or {}).items():
        ob = bpy.data.objects.get(path)
        if ob is None:
            report.warn(f"override for unknown part {path!r} ignored")
            continue
        try:
            if "offset_m" in ov:
                # offset is in the world (ship) frame; convert to the parent's frame
                off = Vector(ov["offset_m"])
                if ob.parent is not None:
                    bpy.context.view_layer.update()
                    off = ob.parent.matrix_world.to_3x3().inverted() @ off
                ob.location = ob.location + off
            if "rotate_deg" in ov:
                r = ov["rotate_deg"]
                ob.rotation_euler = tuple(a + math.radians(float(b))
                                          for a, b in zip(ob.rotation_euler, r))
            if "scale" in ov:
                ob.scale = tuple(a * float(b) for a, b in zip(ob.scale, ov["scale"]))
            if ov.get("color") is not None and ob.type == "MESH":
                m = material(ov["color"], name=f"mat_override_{path}")
                ob.data.materials.clear()
                ob.data.materials.append(m)
                for poly in ob.data.polygons:
                    poly.material_index = 0
            if ov.get("hidden") and ob.type == "MESH":
                # keep the node (children and the hierarchy survive), drop the geometry
                ob.data = bpy.data.meshes.new(f"{path}_hidden")
                ob.modifiers.clear()
                ob["me3d_hidden"] = True
        except Exception as e:  # a bad value must not stop the rest of the build
            report.error(f"override {path}: {type(e).__name__}: {e}")


def apply_joint_limits(spec, report=None):
    report = report or REPORT
    for path, lim in (spec.get("joint_limits") or {}).items():
        ob = bpy.data.objects.get(path)
        if ob is None:
            report.warn(f"joint_limits for unknown part {path!r} ignored")
            continue
        j = get_joint(ob)
        if j is None:
            report.warn(f"joint_limits for {path!r} ignored: it is not a moving part")
            continue
        for k in ("min_deg", "max_deg"):
            if k in lim:
                j[k] = float(lim[k])
        if j["min_deg"] > j["max_deg"]:
            report.error(f"joint_limits {path}: min_deg {j['min_deg']} > max_deg {j['max_deg']}")
        ob["me3d_joint"] = json.dumps(j)


def run_custom_ops(spec, report=None):
    """Run the AI's custom ops in order, with ``me3d`` and ``bpy`` in scope. A failing op is an
    error in the build report (the build is then not ok) and the remaining ops still run."""
    import sys
    report = report or REPORT
    me3d = sys.modules.get("me3d") or sys.modules[__name__]
    for op in spec.get("custom_ops") or []:
        oid = op.get("id", "?")
        before = set(bpy.data.objects.keys())
        ns = {"me3d": me3d, "bpy": bpy, "math": math, "Vector": Vector, "Matrix": Matrix,
              "__name__": f"custom_op_{oid}"}
        try:
            exec(compile(op.get("code", ""), f"<custom_op {oid}>", "exec"), ns)
        except Exception as e:
            tb = traceback.extract_tb(e.__traceback__)
            line = next((f.lineno for f in reversed(tb) if f.filename.startswith("<custom_op")), "?")
            report.error(f"custom op {oid} ({op.get('description', '')}) failed at line {line}: "
                         f"{type(e).__name__}: {e}")
            continue
        for name in sorted(set(bpy.data.objects.keys()) - before):
            if classify(name) is None:
                report.error(f"custom op {oid} created {name!r}, which is not a legal part name "
                             f"(Group__Part[__Sub], [A-Za-z0-9_])")


# --------------------------------------------------------------------------------------------
# Export (contract section 4)
# --------------------------------------------------------------------------------------------

def _mesh_stats(ob, depsgraph):
    """World-space bounds and triangle count of the evaluated mesh (modifiers applied)."""
    ev = ob.evaluated_get(depsgraph)
    me = ev.to_mesh()
    try:
        tris = sum(len(p.vertices) - 2 for p in me.polygons)
        mw = ob.matrix_world
        pts = [mw @ v.co for v in me.vertices]
    finally:
        ev.to_mesh_clear()
    return pts, tris


def _r(v, nd=4):
    return [round(float(a), nd) for a in v]


def collect_parts():
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    parts, all_pts, total = [], [], 0
    for ob in sorted(bpy.data.objects, key=lambda o: o.name):
        kind = classify(ob.name)
        if kind not in ("part", "station"):
            continue
        tris, pts = 0, []
        if ob.type == "MESH":
            pts, tris = _mesh_stats(ob, dg)
        origin = ob.matrix_world.translation
        if not pts:
            pts = [origin.copy()]
        elif kind == "part":
            all_pts.extend(pts)
        total += tris
        lo = [min(p[i] for p in pts) for i in range(3)]
        hi = [max(p[i] for p in pts) for i in range(3)]
        st = ob.get("me3d_station")
        parts.append({
            "path": ob.name,
            "label": label(ob.name),
            "parent": ob.parent.name if ob.parent else None,
            "joint": get_joint(ob),
            "station": json.loads(st) if st else None,
            "origin_m": _r(origin),
            "rotation_deg": _r([math.degrees(a) for a in ob.matrix_world.to_euler("XYZ")], 3),
            "bounds_min": _r(lo),
            "bounds_max": _r(hi),
            "triangles": tris,
        })
        if ob.get("me3d_hidden"):
            parts[-1]["hidden"] = True
    if all_pts:
        lo = [min(p[i] for p in all_pts) for i in range(3)]
        hi = [max(p[i] for p in all_pts) for i in range(3)]
    else:
        lo = hi = [0.0, 0.0, 0.0]
    return parts, total, _r(lo), _r(hi)


def check_scene(report=None, budget=None, need_aft_masthead=True):
    """Contract checks that are cheap inside Blender: names, sockets, joints, budget."""
    report = report or REPORT
    for ob in bpy.data.objects:
        if classify(ob.name) is None:
            report.error(f"object {ob.name!r} breaks the naming rule (Group__Part[__Sub])")
        j = ob.get("me3d_joint")
        if j and ob.parent is None:
            report.error(f"moving part {ob.name} has no parent")
    need = [s for s in SOCKETS if need_aft_masthead or s != "socket_light_masthead_aft"]
    for s in need:
        if s not in bpy.data.objects:
            report.warn(f"socket {s} missing; StraitsSim will guess it")


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)


def export_outputs(out_dir, report=None, budget=None):
    """Write model.glb, model.blend, parts.json and build_report.json into ``out_dir``.
    Returns the build report dict."""
    report = report or REPORT
    os.makedirs(out_dir, exist_ok=True)
    parts, tris, lo, hi = [], 0, [0, 0, 0], [0, 0, 0]
    try:
        parts, tris, lo, hi = collect_parts()
        if budget and tris > budget:
            report.warn(f"{tris} triangles is over the budget of {budget}")
        write_json(os.path.join(out_dir, "parts.json"), parts)
        bpy.ops.export_scene.gltf(
            filepath=os.path.join(out_dir, "model.glb"), export_format="GLB",
            export_yup=True, export_apply=True, export_extras=True, use_selection=False)
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(out_dir, "model.blend"), copy=True)
    except Exception as e:
        report.error(f"export failed: {type(e).__name__}: {e}")
    return write_report(out_dir, report, tris, lo, hi)


def write_report(out_dir, report=None, tris=0, lo=(0, 0, 0), hi=(0, 0, 0)):
    report = report or REPORT
    os.makedirs(out_dir, exist_ok=True)
    data = {"ok": not report.errors, "errors": report.errors, "warnings": report.warnings,
            "triangles": int(tris), "bounds_min": list(lo), "bounds_max": list(hi),
            "seconds": round(time.time() - report.t0, 2)}
    write_json(os.path.join(out_dir, "build_report.json"), data)
    return data
