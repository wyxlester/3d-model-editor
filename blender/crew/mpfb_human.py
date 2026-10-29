"""Build a rigged, clothed human with MPFB2 (runs inside Blender).

Everything goes through MPFB's own services (HumanService.deserialize_from_dict is the same path
MPFB's "load human preset" uses), so clothes are fitted and weighted to the game_engine armature by
MPFB itself. Afterwards the result is flattened into plain game objects: modifiers applied, the
base mesh removed, the whole character turned to face +X (MakeHuman faces -Y), and objects renamed
to contract section 2 names.
"""

import importlib
import math

import bpy
from mathutils import Matrix

from . import spec as crew_spec

MPFB_MODULE = "bl_ext.blender_org.mpfb"


def load_mpfb():
    try:
        importlib.import_module(MPFB_MODULE)
    except ImportError:
        bpy.ops.preferences.addon_enable(module=MPFB_MODULE)
    from bl_ext.blender_org.mpfb import services  # noqa: E402
    return services


def pack_installed():
    services = load_mpfb()
    return services.AssetService.system_assets_pack_is_installed()


def _mesh_height(obj):
    dg = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(dg)
    mw = obj.matrix_world
    zs = [(mw @ v.co).z for v in ev.data.vertices]
    return max(zs) - min(zs)


def _remove(obj):
    data = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    if isinstance(data, bpy.types.Mesh) and data.users == 0:
        bpy.data.meshes.remove(data)


def solve_height_macro(spec, log):
    """Bisect MPFB's height macro until the barefoot base mesh is height_cm tall. Other macros
    (gender, age, weight, race...) change stature too, so this solves with them in place."""
    services = load_mpfb()
    HumanService, TargetService = services.HumanService, services.TargetService
    HumanObjectProperties = importlib.import_module(MPFB_MODULE + ".entities.objectproperties").HumanObjectProperties
    target = spec["body"]["height_cm"] / 100.0
    basemesh = HumanService.create_human(macro_detail_dict=crew_spec.macro_dict(spec, 0.5))

    def height_at(x):
        HumanObjectProperties.set_value("height", x, entity_reference=basemesh)
        TargetService.reapply_macro_details(basemesh)
        return _mesh_height(basemesh)

    x, h = crew_spec.solve_monotonic(height_at, target)
    _remove(basemesh)
    log("height macro %.4f gives %.4f m (target %.3f m)" % (x, h, target))
    return x, h


def build_human(spec, warnings, log):
    """Returns (armature, objects) where objects maps part path -> mesh object."""
    services = load_mpfb()
    HumanService, ObjectService = services.HumanService, services.ObjectService
    have_pack = services.AssetService.system_assets_pack_is_installed()

    height_macro, barefoot_h = solve_height_macro(spec, log)
    b, c = spec["body"], spec["clothes"]
    info = HumanService._create_default_human_info_dict()
    info["phenotype"] = crew_spec.macro_dict(spec, height_macro)
    info["rig"] = "game_engine"
    info["alternative_materials"] = {}
    info["skin_material_type"] = "GAMEENGINE"   # plain Principled BSDF + base colour texture: glTF-safe
    info["eyes_material_type"] = "GAMEENGINE"
    info["clothes_material_type"] = "GAMEENGINE"
    if have_pack:
        info["proxy"] = "%s_generic/%s_generic.proxy" % (b["gender"], b["gender"])
        info["skin_mhmat"] = crew_spec.skin_asset(b["skin"], b["gender"], b["age_years"])
        info["eyes"] = "high-poly/high-poly.mhclo"
        info["eyebrows"] = "eyebrow001/eyebrow001.mhclo"
        info["eyelashes"] = "eyelashes01/eyelashes01.mhclo"
        if b["hair"] and b["hair"] != "none":
            info["hair"] = "%s/%s.mhclo" % (b["hair"], b["hair"])
        clothes = []
        for key in ("suit", "shoes", "hat"):
            if c.get(key):
                clothes.append("%s/%s.mhclo" % (c[key], c[key]))
        info["clothes"] = clothes
    else:
        warnings.append("MakeHuman system asset pack is not installed (run tools/install_mpfb_assets.sh): "
                        "built a bare base mesh with no skin texture, hair, eyes or clothes")

    settings = HumanService.get_default_deserialization_settings()
    settings["subdiv_levels"] = 0
    # Keep MPFB's helper and joint vertex groups: the rig is fitted to the joint-* groups, and
    # without them every bone lands near the origin.
    settings["override_skin_model"] = "PRESET"
    basemesh = HumanService.deserialize_from_dict(info, settings)
    armature = ObjectService.find_object_of_type_amongst_nearest_relatives(basemesh, "Skeleton")
    if armature is None:
        raise RuntimeError("MPFB did not create the game_engine armature")

    children = [o for o in bpy.data.objects if o.type == "MESH" and o is not basemesh]
    proxy = ObjectService.find_object_of_type_amongst_nearest_relatives(basemesh, "Proxymeshes")

    objects = {}
    def role_of(o):
        src = str(o.get("MPFB_GEN_asset_source", "") or "")
        name = o.name.lower()
        return src, name

    used = set()
    for o in children:
        name = o.name.lower()
        path = None
        if o is proxy:
            path = "Body__Skin"
        elif c.get("suit") and name.endswith("." + c["suit"]):
            path = "Clothes__Suit"
        elif c.get("shoes") and name.endswith("." + c["shoes"]):
            path = "Clothes__Shoes"
        elif c.get("hat") and name.endswith("." + c["hat"]):
            path = "Clothes__Hat"
        elif name.endswith(".high-poly"):
            path = "Head__Eyes"
        elif "eyebrow" in name:
            path = "Head__Eyebrows"
        elif "eyelash" in name:
            path = "Head__Eyelashes"
        elif b["hair"] and name.endswith("." + b["hair"]):
            path = "Head__Hair"
        if path is None or path in used:
            warnings.append("unexpected MPFB object %r left out of the export" % o.name)
            _remove(o)
            continue
        used.add(path)
        objects[path] = o

    if proxy is None:
        # No pack: the base mesh itself is the body. Bake its shape keys so it can be flattened.
        objects["Body__Skin"] = basemesh
        _bake_shape_keys(basemesh)
    _flatten(armature, objects)
    if proxy is not None:
        _remove(basemesh)

    # MakeHuman faces -Y; contract section 1 wants +X. Rotate the data (not the object), so the
    # exported nodes carry identity transforms and Unreal sees a clean skeleton.
    rot = Matrix.Rotation(math.radians(90.0), 4, "Z")
    _transform_armature(armature, rot)
    for o in objects.values():
        o.data.transform(rot)
        o.data.update()

    # Feet on the floor: the lowest point of anything (shoe soles) at z = 0.
    lowest = min(_world_min_z(o) for o in objects.values())
    if abs(lowest) > 1e-5:
        lift = Matrix.Translation((0.0, 0.0, -lowest))
        _transform_armature(armature, lift)
        for o in objects.values():
            o.data.transform(lift)
            o.data.update()
        log("lifted everything by %.4f m so the soles touch z=0" % -lowest)

    armature.name = "Crew"
    armature.data.name = "Crew"
    for path, o in objects.items():
        o.name = path
        o.data.name = path
        for i, slot in enumerate(o.material_slots):
            if slot.material is not None:
                slot.material.name = path + ("" if i == 0 else "_%d" % i)
    for key, path in (("suit_color", "Clothes__Suit"), ("shoes_color", "Clothes__Shoes"),
                      ("hat_color", "Clothes__Hat")):
        if c.get(key) and path in objects:
            tint_material(objects[path], c[key])
    if b.get("hair_color"):
        for path in ("Head__Hair", "Head__Eyebrows"):
            if path in objects:
                recolour_hair(objects[path], b["hair_color"], log)
    for path, o in objects.items():
        set_alpha_mode(o, "MASK" if path in ALPHA_CLIPPED else "OPAQUE")
    if "Body__Skin" in objects:
        flatten_groin(objects["Body__Skin"], armature)
    return armature, objects, barefoot_h, height_macro


def flatten_groin(body, armature):
    """No anatomy, under clothes or round them: in front of the groin nothing on the skin may
    stand further forward than the fronts of the thighs beside it. Garments are cut from this
    skin and it shows wherever one leaves it bare, so this is the second line behind the
    trousers' own flat front. Pulls vertices back only (never out through anything fitted)."""
    mw = armature.matrix_world
    hip = mw @ armature.data.bones["thigh_l"].head_local
    pelvis_x = (mw @ armature.data.bones["pelvis"].head_local).x
    hip_y = abs(hip.y)
    verts = body.data.vertices
    groin = [v for v in verts if v.co.x > pelvis_x + 0.03 and hip.z - 0.15 <= v.co.z <= hip.z + 0.05
             and abs(v.co.y) < 0.9 * hip_y]
    fronts = {}
    for key in {round(v.co.z, 2) for v in groin}:
        xs = [v.co.x for v in verts if abs(v.co.z - key) < 0.012 and 0.55 * hip_y <= abs(v.co.y) <= 1.05 * hip_y]
        fronts[key] = max(xs) if xs else None

    def smooth(e0, e1, x):
        t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
        return t * t * (3 - 2 * t)
    for v in groin:
        cap = fronts[round(v.co.z, 2)]
        if cap is None or v.co.x <= cap:
            continue
        w = (1.0 - smooth(hip.z - 0.01, hip.z + 0.045, v.co.z)) * (1.0 - smooth(0.5 * hip_y, 0.9 * hip_y, abs(v.co.y)))
        v.co.x -= w * (v.co.x - cap)
    body.data.update()


# Card-like meshes whose texture alpha cuts out strands; everything else is solid.
ALPHA_CLIPPED = {"Head__Hair", "Head__Eyebrows", "Head__Eyelashes"}


def set_alpha_mode(obj, mode):
    """MPFB's game-engine materials wire texture alpha into the BSDF, which the glTF exporter
    turns into alphaMode BLEND for every part: Unreal then draws skin and clothes as translucent
    (no shadows, sorting errors). Solid parts get the alpha link removed (OPAQUE); hair-like
    cards get alpha through a Round node, which the exporter writes as MASK with cutoff 0.5."""
    for slot in obj.material_slots:
        mat = slot.material
        if mat is None or not mat.use_nodes:
            continue
        nt = mat.node_tree
        bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if bsdf is None:
            continue
        alpha = bsdf.inputs["Alpha"]
        src = alpha.links[0].from_socket if alpha.is_linked else None
        for link in list(alpha.links):
            nt.links.remove(link)
        alpha.default_value = 1.0
        if mode == "MASK" and src is not None:
            rnd = nt.nodes.new("ShaderNodeMath")
            rnd.operation = "ROUND"
            nt.links.new(src, rnd.inputs[0])
            nt.links.new(rnd.outputs[0], alpha)
            mat.use_backface_culling = False
        else:
            mat.use_backface_culling = True
        if hasattr(mat, "surface_render_method"):
            mat.surface_render_method = "DITHERED"
        if hasattr(mat, "blend_method"):
            try:
                mat.blend_method = "CLIP" if mode == "MASK" else "OPAQUE"
            except TypeError:
                pass


def _bake_shape_keys(obj):
    if obj.data.shape_keys is None:
        return
    bpy.context.view_layer.objects.active = obj
    obj.shape_key_add(name="__baked", from_mix=True)
    keys = obj.data.shape_keys.key_blocks
    baked = [v.co.copy() for v in keys["__baked"].data]
    obj.shape_key_clear()
    for v, co in zip(obj.data.vertices, baked):
        v.co = co


def _flatten(armature, objects):
    """Apply every modifier except the armature one (MPFB's MASK modifiers delete the skin hidden
    under clothes; that is what keeps the triangle budget) and make each mesh a plain child of the
    armature with an identity transform."""
    for o in objects.values():
        for ob in bpy.context.selected_objects:
            ob.select_set(False)
        bpy.context.view_layer.objects.active = o
        o.select_set(True)
        if o.data.shape_keys is not None:
            _bake_shape_keys(o)
        for m in list(o.modifiers):
            if m.type == "ARMATURE":
                m.object = armature
                continue
            if not m.show_viewport:
                o.modifiers.remove(m)
                continue
            bpy.ops.object.modifier_apply(modifier=m.name)
        mw = o.matrix_world.copy()
        o.parent = armature
        o.matrix_parent_inverse = Matrix.Identity(4)
        o.matrix_world = armature.matrix_world @ Matrix.Identity(4)
        o.data.transform(armature.matrix_world.inverted() @ mw)
        o.select_set(False)
        arm_mods = [m for m in o.modifiers if m.type == "ARMATURE"]
        if not arm_mods:
            m = o.modifiers.new("Armature", "ARMATURE")
            m.object = armature


def _transform_armature(armature, mat):
    for ob in bpy.context.selected_objects:
        ob.select_set(False)
    bpy.context.view_layer.objects.active = armature
    armature.select_set(True)
    armature.data.use_mirror_x = False  # X-mirror editing would re-mirror every _l/_r bone we move
    bpy.ops.object.mode_set(mode="EDIT")
    for eb in armature.data.edit_bones:
        eb.transform(mat, scale=False, roll=True)
    bpy.ops.object.mode_set(mode="OBJECT")
    armature.select_set(False)


def _world_min_z(obj):
    mw = obj.matrix_world
    return min((mw @ v.co).z for v in obj.data.vertices)


HAIR_MAX_PX = 1024


def _srgb_to_linear(c):
    import numpy as np
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _linear_to_srgb(c):
    import numpy as np
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1.0 / 2.4) - 0.055)


def recolour_pixels(px, rgb, is_float=False):
    """Recolour flat RGBA pixels to average ``rgb`` (linear 0..1) over the visible (alpha > 0.5)
    texels, keeping the strand pattern as relative brightness. A multiply tint cannot do this: dark
    brown times yellow is darker brown. Here the pattern is first reduced to luminance relative to
    its own mean, softened (bright blonde hair has less contrast than dark hair), then scaled so the
    clipped result still averages the target. Alpha is untouched."""
    import numpy as np
    a = px.reshape(-1, 4)
    lin = a[:, :3] if is_float else _srgb_to_linear(a[:, :3])
    lum = lin @ np.array([0.2126, 0.7152, 0.0722])
    vis = a[:, 3] > 0.5
    if not vis.any():
        vis = np.ones(len(a), dtype=bool)
    rel = np.maximum(lum / max(float(lum[vis].mean()), 1e-4), 0.0) ** 0.6
    rel /= max(float(rel[vis].mean()), 1e-4)
    target = np.array(rgb[:3], dtype=np.float64)
    t_lum = float(target @ np.array([0.2126, 0.7152, 0.0722]))
    s = 1.0
    for _ in range(8):   # clipping at 1.0 pulls the mean down; push the scale back up to compensate
        out = np.clip(target[None, :] * (rel * s)[:, None], 0.0, 1.0)
        got = float((out[vis] @ np.array([0.2126, 0.7152, 0.0722])).mean())
        if got <= 1e-6 or abs(got - t_lum) < 0.002:
            break
        s *= t_lum / got
    res = a.astype(np.float64).copy()
    res[:, :3] = out if is_float else _linear_to_srgb(out)
    return res.astype(np.float32).ravel()


def recolour_hair(obj, rgb, log=print):
    """Replace the base-colour texture of a hair-like part with a recoloured copy (alpha kept, so
    the MASK cut-out still works). Baked into pixels, so it survives glTF export and Unreal."""
    import numpy as np
    for slot in obj.material_slots:
        mat = slot.material
        if mat is None or not mat.use_nodes:
            continue
        nt = mat.node_tree
        bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if bsdf is None or not bsdf.inputs["Base Color"].is_linked:
            if bsdf is not None:
                bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
            continue
        src = bsdf.inputs["Base Color"].links[0].from_node
        if src.type != "TEX_IMAGE" or src.image is None:
            continue
        img = src.image
        work = img.copy()
        w, h = work.size
        if max(w, h) > HAIR_MAX_PX:
            f = HAIR_MAX_PX / float(max(w, h))
            work.scale(max(1, int(w * f)), max(1, int(h * f)))
            w, h = work.size
        px = np.empty(w * h * 4, dtype=np.float32)
        work.pixels.foreach_get(px)
        out = recolour_pixels(px, rgb, is_float=work.is_float)
        bpy.data.images.remove(work)
        new = bpy.data.images.new(obj.name + "_recoloured", w, h, alpha=True)
        new.colorspace_settings.name = "sRGB"
        new.pixels.foreach_set(out)
        new.pack()
        for n in nt.nodes:
            if n.type == "TEX_IMAGE" and n.image is img:
                n.image = new
        log("%s recoloured to %s (%dx%d texture)" % (obj.name, [round(x, 3) for x in rgb], w, h))


def tint_material(obj, rgb):
    """Multiply the base-colour texture by a colour. The glTF exporter turns a MULTIPLY mix of a
    texture and a constant into baseColorFactor, so the tint survives into Unreal."""
    for slot in obj.material_slots:
        mat = slot.material
        if mat is None or not mat.use_nodes:
            continue
        nt = mat.node_tree
        bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if bsdf is None:
            continue
        base = bsdf.inputs["Base Color"]
        if base.is_linked:
            src = base.links[0].from_socket
            mix = nt.nodes.new("ShaderNodeMix")
            mix.data_type = "RGBA"
            mix.blend_type = "MULTIPLY"
            mix.inputs["Factor"].default_value = 1.0
            nt.links.new(src, mix.inputs[6])
            mix.inputs[7].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
            nt.links.new(mix.outputs[2], base)
        else:
            base.default_value = (rgb[0], rgb[1], rgb[2], 1.0)
