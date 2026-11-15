"""Bake PBR texture atlases for a built ship (plan Step 2): writes model_textured.glb.

    Blender -b --factory-startup --python blender/bake_textures.py -- --blend <v>/model.blend --out <v>
        [--config bake.json] [--samples 8] [--page-scale 1.0] [--keep-decals] [--keep-images]
        [--no-verify]

Reads the version's model.blend (never writes it, and never touches model.glb), then:

1. finds decals: marking and hull-number geometry lying within 5 cm of another surface
   (``decal_gap_m``; marking-named parts may stand up to ``decal_named_max_m`` proud);
2. gives every face a finish (texturing/finishes.py) and an atlas, and lays out the shared
   ``BakeUV`` map over the atlas pages (texturing/atlas.py);
3. bakes with Cycles, in background mode, at low samples:
   - base colour (emission trick), then the decals selected-to-active onto their deck or hull;
   - ORM by the emission trick: R = whole-ship AO (Ambient Occlusion node), G = roughness,
     B = metallic (glTF's occlusion + metallicRoughness packing);
   - tangent-space normal (OpenGL, +Y) from the finishes' bump and rounded edges;
4. drops the decals (``--keep-decals`` keeps them, with their flat colours), rebuilds every
   material as Principled + image textures (AO through the glTF Material Output node), and
   writes <out>/model_textured.glb with embedded PNGs and <out>/bake_report.json;
5. re-imports the glb into an empty scene and checks the textures are bound (``--no-verify``
   skips it).

Exit code 1 when something failed; the report says what.
"""

import argparse
import json
import math
import os
import shutil
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import bpy  # noqa: E402
from mathutils import Vector  # noqa: E402
from mathutils.bvhtree import BVHTree  # noqa: E402

from texturing import atlas, finishes  # noqa: E402

DEFAULTS = {
    "samples": 4,                 # Cycles samples per texel (8 looked no cleaner on Lincoln)
    "page_scale": 1.0,            # multiplies every page size (quick test bakes)
    "bake_margin_px": 12,         # bleed around islands, at page_scale 1
    "decal_gap_m": 0.05,          # every vertex of a decal within this of a surface
    "decal_named_max_m": 0.25,    # Marking/Number parts: touching, and at most this proud
    "decal_max_area_ratio": 0.5,  # a decal is smaller than what it lies on
    "keep_decals": False,
    "atlases": {},                # per atlas: page_px, texel_m, max_pages, down_factor
    "layout": {},                 # texturing.atlas.DEFAULT_LAYOUT overrides
    "finish_settings": {},        # texturing.finishes.DEFAULT_SETTINGS overrides
}

DECAL_TOKENS = {"marking", "markings", "number", "numbers", "lettering", "letters", "decal",
                "decals", "text", "name", "pennant", "insignia"}
OUT_GLB = "model_textured.glb"
OUT_REPORT = "bake_report.json"


def log(msg):
    print(f"[bake_textures] {msg}", flush=True)


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser(prog="bake_textures.py")
    p.add_argument("--blend", required=True, help="the version's model.blend")
    p.add_argument("--out", required=True, help="folder for model_textured.glb and bake_report.json")
    p.add_argument("--config", help="JSON file with DEFAULTS overrides")
    p.add_argument("--samples", type=int)
    p.add_argument("--page-scale", type=float)
    p.add_argument("--keep-decals", action="store_true")
    p.add_argument("--keep-images", action="store_true", help="keep the baked PNGs in <out>/bake_images")
    p.add_argument("--no-verify", action="store_true")
    return p.parse_args(argv)


def load_config(args):
    cfg = json.loads(json.dumps(DEFAULTS))
    if args.config:
        with open(args.config, encoding="utf-8") as f:
            user = json.load(f)
        for k, v in user.items():
            if k not in DEFAULTS:
                raise ValueError(f"unknown bake setting {k!r}; settings: {sorted(DEFAULTS)}")
            cfg[k] = v
    if args.samples:
        cfg["samples"] = args.samples
    if args.page_scale:
        cfg["page_scale"] = args.page_scale
    if args.keep_decals:
        cfg["keep_decals"] = True
    return cfg


# --------------------------------------------------------------------------------------------
# Materials in
# --------------------------------------------------------------------------------------------

def material_values(mat):
    """(tint rgb, roughness, metallic, double_sided) of a flat source material."""
    if mat is None:
        return (0.7, 0.7, 0.7), 0.6, 0.0, False
    bsdf = None
    if mat.node_tree is not None:
        bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is not None:
        c = tuple(bsdf.inputs["Base Color"].default_value[:3])
        r = float(bsdf.inputs["Roughness"].default_value)
        m = float(bsdf.inputs["Metallic"].default_value)
    else:
        c, r, m = tuple(mat.diffuse_color[:3]), float(mat.roughness), float(mat.metallic)
    return c, r, m, not mat.use_backface_culling


# --------------------------------------------------------------------------------------------
# Decals
# --------------------------------------------------------------------------------------------

def _world_mesh(ob):
    mw = ob.matrix_world
    verts = [mw @ v.co for v in ob.data.vertices]
    polys = [tuple(p.vertices) for p in ob.data.polygons]
    return verts, polys


def find_decals(meshes, cfg):
    """[{name, target, max_gap_m, reason}]: thin geometry lying on another part's surface."""
    gap, named_max = float(cfg["decal_gap_m"]), float(cfg["decal_named_max_m"])
    info = {}
    for ob in meshes:
        if not ob.data.polygons:
            continue
        verts, polys = _world_mesh(ob)
        lo = Vector([min(v[i] for v in verts) for i in range(3)])
        hi = Vector([max(v[i] for v in verts) for i in range(3)])
        info[ob.name] = {"ob": ob, "verts": verts, "tree": BVHTree.FromPolygons(verts, polys),
                         "lo": lo, "hi": hi, "area": atlas.world_area(ob)}

    def near(a, b, pad):
        return all(a["lo"][i] - pad <= b["hi"][i] and b["lo"][i] - pad <= a["hi"][i] for i in range(3))

    def dists(cand, other):
        """Distance of each of cand's vertices to other's surface."""
        out = []
        for v in cand["verts"]:
            hit = other["tree"].find_nearest(v)
            out.append(hit[3] if hit[0] is not None else 1e9)
        return out

    cands = {}
    for name, d in info.items():
        ob = d["ob"]
        if ob.children or ob.get("me3d_joint") or ob.get("me3d_station"):
            continue
        tokens = {t.lower() for seg in name.split("__")[1:] for t in seg.split("_")}
        named = bool(tokens & DECAL_TOKENS) or name.split("__")[0].lower() in ("markings", "marking")
        per_other = {}
        for oname, o in info.items():
            if oname == name or not near(d, o, named_max):
                continue
            per_other[oname] = dists(d, o)
        if not per_other:
            continue
        nverts = len(d["verts"])
        closest = [min(ds[i] for ds in per_other.values()) for i in range(nverts)]
        if max(closest) <= gap + 1e-4:
            cands[name] = ("within %.0f cm of a surface" % (gap * 100), per_other)
        elif named:
            for oname, ds in per_other.items():
                if min(ds) <= gap + 1e-4 and max(ds) <= named_max + 1e-4:
                    cands[name] = ("marking-named, on a surface, %.0f cm proud" % (max(ds) * 100),
                                   per_other)
                    break

    def body(ob):
        """The moving part an object rides on: its nearest jointed ancestor (or itself), else None."""
        while ob is not None:
            if ob.get("me3d_joint"):
                return ob.name
            ob = ob.parent
        return None

    decals = []
    for name, (reason, per_other) in sorted(cands.items()):
        best, best_d = None, 1e9
        for oname, ds in per_other.items():
            if oname in cands:
                continue
            m = max(ds) if min(ds) <= gap + 1e-4 else 1e9
            if m < best_d:
                best, best_d = oname, m
        if best is None or best_d > named_max + gap:
            continue
        if info[name]["area"] > float(cfg["decal_max_area_ratio"]) * info[best]["area"]:
            continue
        if body(info[name]["ob"]) != body(info[best]["ob"]):
            continue   # baked onto a part that moves differently, it would stop following its own
        decals.append({"name": name, "target": best, "max_gap_m": round(best_d, 4), "reason": reason})
    return decals


# --------------------------------------------------------------------------------------------
# Bake materials
# --------------------------------------------------------------------------------------------

def bake_material(name, finish, values, settings):
    """A material whose output can be switched between colour, ORM and normal for baking."""
    tint, rough, metal, _ = values
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (600, 0)
    s = finishes.build(nt, finish, tint, rough, metal, settings)
    em_col = nt.nodes.new("ShaderNodeEmission")
    em_col.location = (300, 200)
    nt.links.new(s["color"], em_col.inputs["Color"])
    orm = nt.nodes.new("ShaderNodeCombineColor")
    orm.location = (100, 0)
    for i, key in enumerate(("ao", "roughness", "metallic")):
        v = s[key]
        if hasattr(v, "links"):
            nt.links.new(v, orm.inputs[i])
        else:
            orm.inputs[i].default_value = float(v)
    em_orm = nt.nodes.new("ShaderNodeEmission")
    em_orm.location = (300, 0)
    nt.links.new(orm.outputs[0], em_orm.inputs["Color"])
    diff = nt.nodes.new("ShaderNodeBsdfDiffuse")
    diff.location = (300, -200)
    nt.links.new(s["normal"], diff.inputs["Normal"])
    img = nt.nodes.new("ShaderNodeTexImage")
    img.location = (600, -300)
    img.interpolation = "Closest"
    for n in nt.nodes:
        n.select = False
    img.select = True
    nt.nodes.active = img
    return {"mat": mat, "out": out, "img": img, "color": em_col.outputs[0], "orm": em_orm.outputs[0],
            "normal": diff.outputs[0]}


def decal_material(name, finish, values, settings):
    """Emission of the decal's finished colour, or white for the coverage mask."""
    tint, rough, metal, _ = values
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    s = finishes.build(nt, finish, tint, rough, metal, settings, edges=False)
    mix = nt.nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    ins = [x for x in mix.inputs if x.type == "RGBA"]
    nt.links.new(s["color"], ins[0])
    ins[1].default_value = (1.0, 1.0, 1.0, 1.0)
    mix.inputs[0].default_value = 0.0
    em = nt.nodes.new("ShaderNodeEmission")
    nt.links.new(next(o for o in mix.outputs if o.type == "RGBA"), em.inputs["Color"])
    nt.links.new(em.outputs[0], out.inputs["Surface"])
    return {"mat": mat, "mask": mix.inputs[0]}


def set_pass(bms, images_of_page, key):
    for bm in bms:
        nt = bm["mat"].node_tree
        nt.links.new(bm[key], bm["out"].inputs["Surface"])
        if images_of_page is not None:
            bm["img"].image = images_of_page[bm["page"]]


# --------------------------------------------------------------------------------------------
# Final materials
# --------------------------------------------------------------------------------------------

def gltf_output_group():
    """The node group the glTF exporter reads occlusion from (named exactly like this)."""
    ng = bpy.data.node_groups.get("glTF Material Output")
    if ng is None:
        ng = bpy.data.node_groups.new("glTF Material Output", "ShaderNodeTree")
        ng.interface.new_socket("Occlusion", in_out="INPUT", socket_type="NodeSocketFloat")
        ng.interface.new_socket("Thickness", in_out="INPUT", socket_type="NodeSocketFloat")
    return ng


def final_material(name, imgs, double_sided):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (500, 0)
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (200, 0)
    nt.links.new(bsdf.outputs[0], out.inputs["Surface"])
    uv = nt.nodes.new("ShaderNodeUVMap")
    uv.uv_map = atlas.UV_NAME
    uv.location = (-800, 0)

    def tex(img, y):
        n = nt.nodes.new("ShaderNodeTexImage")
        n.image = img
        n.location = (-500, y)
        nt.links.new(uv.outputs[0], n.inputs["Vector"])
        return n

    col = tex(imgs["color"], 300)
    nt.links.new(col.outputs["Color"], bsdf.inputs["Base Color"])
    orm = tex(imgs["orm"], 0)
    sep = nt.nodes.new("ShaderNodeSeparateColor")
    sep.location = (-200, 0)
    nt.links.new(orm.outputs["Color"], sep.inputs[0])
    nt.links.new(sep.outputs[1], bsdf.inputs["Roughness"])
    nt.links.new(sep.outputs[2], bsdf.inputs["Metallic"])
    grp = nt.nodes.new("ShaderNodeGroup")
    grp.node_tree = gltf_output_group()
    grp.location = (200, -400)
    nt.links.new(sep.outputs[0], grp.inputs["Occlusion"])
    nrm = tex(imgs["normal"], -300)
    nmap = nt.nodes.new("ShaderNodeNormalMap")
    nmap.space = "TANGENT"
    nmap.uv_map = atlas.UV_NAME
    nmap.location = (-200, -300)
    nt.links.new(nrm.outputs["Color"], nmap.inputs["Color"])
    nt.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
    mat.use_backface_culling = not double_sided
    return mat


# --------------------------------------------------------------------------------------------
# Export and verification
# --------------------------------------------------------------------------------------------

def export_glb(path):
    props = set(bpy.ops.export_scene.gltf.get_rna_type().properties.keys())
    wanted = {
        "filepath": path, "export_format": "GLB", "use_selection": False,
        "export_yup": True, "export_apply": True, "export_extras": True,   # like model.glb
        "export_image_format": "AUTO",                                     # like build_crew.py
        "export_materials": "EXPORT", "export_texcoords": True, "export_normals": True,
        "export_cameras": False, "export_lights": False,
    }
    bpy.ops.export_scene.gltf(**{k: v for k, v in wanted.items() if k in props})


def verify_glb(path):
    """Re-import into an empty scene: every mesh has UVs and textured materials."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=path)
    res = {"objects": 0, "meshes": 0, "without_uv": [], "materials": {}, "untextured": []}
    for ob in bpy.data.objects:
        res["objects"] += 1
        if ob.type != "MESH" or not ob.data.polygons:
            continue
        res["meshes"] += 1
        if not ob.data.uv_layers:
            res["without_uv"].append(ob.name)
    for mat in bpy.data.materials:
        if mat.node_tree is None or mat.users == 0:
            continue
        bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if bsdf is None:
            continue

        def fed(sock, depth=0):
            """The image behind a socket, through any normal-map / separate nodes."""
            for lk in sock.links:
                n = lk.from_node
                if n.type == "TEX_IMAGE" and n.image is not None:
                    return n.image.size[:]
                for s in n.inputs:
                    if depth < 4:
                        r = fed(s, depth + 1)
                        if r:
                            return r
            return None

        b = {k: fed(bsdf.inputs[k]) for k in ("Base Color", "Roughness", "Metallic", "Normal")}
        occ = None
        for n in mat.node_tree.nodes:
            if n.type == "GROUP" and n.node_tree and "glTF" in n.node_tree.name and "Occlusion" in n.inputs:
                occ = fed(n.inputs["Occlusion"])
        b["Occlusion"] = occ
        res["materials"][mat.name] = {k: list(v) if v else None for k, v in b.items()}
        if not all(b.values()):
            res["untextured"].append(mat.name)
    return res


# --------------------------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------------------------

def main():
    args = parse_args()
    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)
    t0 = time.time()
    timings = {}
    report = {"ok": False, "errors": [], "warnings": [], "source": os.path.abspath(args.blend),
              "output": OUT_GLB}
    tick = [time.time()]

    def lap(name):
        now = time.time()
        timings[name] = round(now - tick[0], 2)
        tick[0] = now
        log(f"{name}: {timings[name]} s")

    img_dir = os.path.join(out_dir, "bake_images")
    try:
        cfg = load_config(args)
        report["settings"] = cfg
        bpy.ops.wm.open_mainfile(filepath=os.path.abspath(args.blend))
        scene = bpy.context.scene
        meshes = [ob for ob in scene.objects if ob.type == "MESH"]
        lap("load")

        # 1. decals
        decals = find_decals(meshes, cfg)
        decal_names = {d["name"] for d in decals}
        report["decals"] = decals
        report["decals_kept"] = bool(cfg["keep_decals"])
        log(f"{len(decals)} decals: {', '.join(sorted(decal_names)) or '-'}")
        lap("decals")

        # 2. finishes and atlases
        targets = [ob for ob in meshes if ob.name not in decal_names]
        atlas.make_single_user(targets)
        lcfg = dict(atlas.DEFAULT_LAYOUT, **cfg["layout"])
        lcfg["margin_px"] = max(1.0, lcfg["margin_px"] * cfg["page_scale"])
        acfg = {}
        for name in atlas.ATLAS_NAMES:
            a = dict(atlas.DEFAULT_ATLASES[name], **(cfg["atlases"].get(name) or {}))
            a["page_px"] = max(16, int(round(a["page_px"] * cfg["page_scale"])))
            a["texel_m"] = a["texel_m"] / cfg["page_scale"]
            acfg[name] = a
        mat_info = {}     # material name -> values
        finish_of = {}    # (ob, slot) -> finish
        slot_atlas = {}
        finish_table = {}
        for ob in targets:
            area = atlas.world_area(ob)
            slot_atlas[ob.name] = {}
            slots = list(ob.material_slots) or [None]
            for i, sl in enumerate(slots):
                mat = sl.material if sl is not None else None
                mname = mat.name if mat else "none"
                vals = mat_info.setdefault(mname, material_values(mat))
                fin, why = finishes.finish_for(mname, ob.name, vals[1], vals[2])
                finish_of[(ob.name, i)] = fin
                slot_atlas[ob.name][i] = atlas.atlas_for(fin, ob.name, area,
                                                         lcfg["superstructure_min_area_m2"])
                row = finish_table.setdefault(f"{mname} -> {fin}", {"objects": 0, "why": why})
                row["objects"] += 1
        report["finishes"] = finish_table
        cuts = sum(atlas.cut_tiles(ob, lcfg["tile_m"]) for ob in targets)
        log(f"tiling cuts: {cuts}")
        pages, warns = atlas.layout(targets, slot_atlas, acfg, lcfg, log=log)
        report["warnings"].extend(warns)
        lap("uv_layout")

        # 3. images and bake materials
        kinds = {"color": "sRGB", "orm": "Non-Color", "normal": "Non-Color"}
        images = {k: {} for k in kinds}
        for p in pages:
            stem = f"{p['atlas']}_{p['index']}"
            p["images"] = {}
            for k, cs in kinds.items():
                img = bpy.data.images.new(f"{stem}_{k}", p["px"], p["px"], alpha=False,
                                          float_buffer=False)
                img.colorspace_settings.name = cs
                img.generated_color = (0.5, 0.5, 1.0, 1.0) if k == "normal" else (0, 0, 0, 1)
                images[k][p["id"]] = img
                p["images"][k] = f"{stem}_{k}.png"
        settings = cfg["finish_settings"]
        bake_mats = {}   # (source material, page) -> bake material dict
        for ob in targets:
            me = ob.data
            if not me.polygons:
                continue
            slots = list(ob.material_slots) or [None]
            src = [(sl.material.name if sl is not None and sl.material else "none") for sl in slots]
            pages_attr = [0] * len(me.polygons)
            me.attributes[atlas.PAGE_ATTR].data.foreach_get("value", pages_attr)
            new_slots, new_index = [], []
            for poly, pg in zip(me.polygons, pages_attr):
                si = poly.material_index if poly.material_index < len(src) else 0
                key = (src[si], pg, finish_of[(ob.name, si)])
                if key not in bake_mats:
                    bm = bake_material(f"bake__{pg}__{src[si]}__{key[2]}", key[2], mat_info[src[si]],
                                       settings)
                    bm["page"] = pg
                    bm["double_sided"] = mat_info[src[si]][3]
                    bake_mats[key] = bm
                if key not in new_slots:
                    new_slots.append(key)
                new_index.append(new_slots.index(key))
            for sl in ob.material_slots:
                sl.link = "DATA"
            me.materials.clear()
            for key in new_slots:
                me.materials.append(bake_mats[key]["mat"])
            me.polygons.foreach_set("material_index", new_index)
            me.update()
        bms = list(bake_mats.values())
        lap("materials")

        # 4. bake
        scene.render.engine = "CYCLES"
        scene.cycles.device = "CPU"
        scene.cycles.samples = int(cfg["samples"])
        if hasattr(scene.cycles, "use_denoising"):
            scene.cycles.use_denoising = False
        margin = max(2, int(round(cfg["bake_margin_px"] * cfg["page_scale"])))
        for d in decals:
            bpy.data.objects[d["name"]].hide_render = True
        bakeable = [ob for ob in targets if ob.data.polygons]
        # Blender bakes selected objects one by one, re-syncing the whole Cycles scene for each:
        # ~0.3 s x 200 parts per pass. Joined world-space copies bake in a few syncs. Parts that
        # touch go to different copies: the Bevel node (rounded edges, edge wear) only sees its own
        # object, and would round off every place where two joined parts meet.
        fs = dict(finishes.DEFAULT_SETTINGS, **settings)
        pad = max(fs["bevel_radius_m"], fs["wear_radius_m"]) + 0.02
        boxes = []
        for ob in bakeable:
            pts = [ob.matrix_world @ Vector(c) for c in ob.bound_box]
            boxes.append(([min(p[k] for p in pts) for k in range(3)],
                          [max(p[k] for p in pts) for k in range(3)]))
        groups = atlas.separated_groups(boxes, pad)
        proxies = [make_proxy([bakeable[i] for i in g], f"BakeProxy_{k}") for k, g in enumerate(groups)]
        report["bake_proxies"] = len(proxies)
        log(f"{len(bakeable)} parts baked as {len(proxies)} proxies")
        for ob in bakeable:
            ob.hide_render = True
        atlas._set_mode(proxies, "OBJECT")

        def bake(kind, **kw):
            bpy.ops.object.bake(type=kind, margin=margin, margin_type="EXTEND", use_clear=True,
                                target="IMAGE_TEXTURES", **kw)

        set_pass(bms, images["color"], "color")
        bake("EMIT")
        lap("bake_color")

        report["decal_bakes"] = bake_decals(decals, bakeable, bms, images["color"], pages, cfg,
                                            settings, mat_info, report)
        lap("bake_decals")

        atlas._set_mode(proxies, "OBJECT")
        set_pass(bms, images["orm"], "orm")
        bake("EMIT")
        lap("bake_orm")
        set_pass(bms, images["normal"], "normal")
        bake("NORMAL", normal_space="TANGENT", normal_r="POS_X", normal_g="POS_Y", normal_b="POS_Z")
        lap("bake_normal")
        for px in proxies:
            bpy.data.objects.remove(px, do_unlink=True)
        for ob in bakeable:
            ob.hide_render = False

        # 5. save the pages
        os.makedirs(img_dir, exist_ok=True)
        for p in pages:
            for k in kinds:
                img = images[k][p["id"]]
                img.filepath_raw = os.path.join(img_dir, p["images"][k])
                img.file_format = "PNG"
                img.save()
                img.filepath = img.filepath_raw
                img.reload()
                img.colorspace_settings.name = kinds[k]
        lap("save_images")

        # 6. final materials, decals out, export
        finals = {}
        for p in pages:
            for ds in (False, True):
                if any(bm["page"] == p["id"] and bm["double_sided"] == ds for bm in bms):
                    nm = f"{p['atlas']}_{p['index']}" + ("_double_sided" if ds else "")
                    finals[(p["id"], ds)] = final_material(
                        nm, {k: images[k][p["id"]] for k in kinds}, ds)
        swap = {bm["mat"].name: finals[(bm["page"], bm["double_sided"])] for bm in bms}
        for ob in targets:
            me = ob.data
            for i, m in enumerate(me.materials):
                if m is not None and m.name in swap:
                    me.materials[i] = swap[m.name]
            if atlas.PAGE_ATTR in me.attributes:
                me.attributes.remove(me.attributes[atlas.PAGE_ATTR])
        for bm in bms:
            bpy.data.materials.remove(bm["mat"])
        if cfg["keep_decals"]:
            kept = [bpy.data.objects[d["name"]] for d in decals]
            for ob in kept:
                ob.hide_render = False
                uvl = ob.data.uv_layers.get(atlas.UV_NAME) or ob.data.uv_layers.new(name=atlas.UV_NAME)
                ob.data.uv_layers.active = uvl
        else:
            for d in decals:
                bpy.data.objects.remove(bpy.data.objects[d["name"]], do_unlink=True)
        missing_uv = [ob.name for ob in scene.objects
                      if ob.type == "MESH" and ob.data.polygons and not ob.data.uv_layers]
        if missing_uv:
            report["errors"].append(f"objects without UVs: {missing_uv}")
        glb = os.path.join(out_dir, OUT_GLB)
        export_glb(glb)
        report["glb_bytes"] = os.path.getsize(glb)
        lap("export")

        report["pages"] = pages
        report["materials_out"] = sorted(m.name for m in finals.values())
        if not args.no_verify:
            v = verify_glb(glb)
            report["reimport"] = v
            if v["without_uv"]:
                report["errors"].append(f"re-imported meshes without UVs: {v['without_uv']}")
            if v["untextured"]:
                report["errors"].append(f"re-imported materials missing textures: {v['untextured']}")
            for p in pages:
                m = v["materials"].get(f"{p['atlas']}_{p['index']}") or \
                    v["materials"].get(f"{p['atlas']}_{p['index']}_double_sided")
                if not m or m["Base Color"] != [p["px"], p["px"]]:
                    report["errors"].append(f"{p['atlas']} page {p['index']}: re-imported texture "
                                            f"size {m and m['Base Color']} != {p['px']}")
            lap("verify")
    except Exception as e:
        report["errors"].append(f"{type(e).__name__}: {e}")
        traceback.print_exc()
    finally:
        if not args.keep_images and os.path.isdir(img_dir):
            shutil.rmtree(img_dir, ignore_errors=True)
    report["timings_s"] = timings
    report["seconds"] = round(time.time() - t0, 2)
    report["ok"] = not report["errors"]
    with open(os.path.join(out_dir, OUT_REPORT), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1)
    log(f"ok={report['ok']} pages={len(report.get('pages') or [])} decals={len(report.get('decals') or [])} "
        f"seconds={report['seconds']} errors={report['errors']}")
    sys.stdout.flush()
    os._exit(0 if report["ok"] else 1)


def make_proxy(objects, name):
    """One world-space mesh joined from copies of ``objects`` (materials, BakeUV and the page
    attribute come along). The originals are left as they are."""
    copies = []
    for ob in objects:
        me = ob.data.copy()
        me.transform(ob.matrix_world)
        if ob.matrix_world.determinant() < 0:
            me.flip_normals()
        cp = bpy.data.objects.new(f"{name}_part", me)
        bpy.context.scene.collection.objects.link(cp)
        copies.append(cp)
    atlas._set_mode(copies, "OBJECT")
    if len(copies) > 1:
        bpy.ops.object.join()
    proxy = bpy.context.view_layer.objects.active
    proxy.name = name
    proxy.hide_render = False
    return proxy


def bake_decals(decals, bakeable, bms, color_images, pages, cfg, settings, mat_info, report):
    """Bake the decals selected-to-active onto a joined copy of the parts they lie on, into
    temporary colour and coverage images, then composite them over the colour pages."""
    import numpy as np

    if not decals:
        return []
    targets = sorted({d["target"] for d in decals})
    page_px = {p["id"]: p["px"] for p in pages}
    dmats = {}
    for d in decals:
        ob = bpy.data.objects[d["name"]]
        mat = ob.material_slots[0].material if ob.material_slots else None
        mname = mat.name if mat else "none"
        vals = mat_info.setdefault(mname, material_values(mat))
        fin, _ = finishes.finish_for(mname, ob.name, vals[1], vals[2])
        if mname not in dmats:
            dmats[mname] = decal_material(f"decal__{mname}", fin, vals, settings)
        d["_orig"] = [sl.material for sl in ob.material_slots]
        for sl in ob.material_slots:
            sl.link = "DATA"
        ob.data.materials.clear()
        ob.data.materials.append(dmats[mname]["mat"])
    tproxy = make_proxy([bpy.data.objects[t] for t in targets], "DecalTargets")
    t_mats = {m.name for m in tproxy.data.materials if m}
    mine = [bm for bm in bms if bm["mat"].name in t_mats]
    pids = sorted({bm["page"] for bm in mine})
    dproxy = make_proxy([bpy.data.objects[d["name"]] for d in decals], "Decals")
    ext = max(d["max_gap_m"] for d in decals) + 0.03
    atlas._set_mode([dproxy, tproxy], "OBJECT")
    bpy.context.view_layer.objects.active = tproxy
    # byte images: colour in sRGB like the page itself, so compositing needs no conversion
    tmp = {}
    for mode, cs in (("col", "sRGB"), ("msk", "Non-Color")):
        tmp[mode] = {}
        for pid in pids:
            im = bpy.data.images.new(f"tmp_decal_{mode}_{pid}", page_px[pid], page_px[pid], alpha=False)
            im.colorspace_settings.name = cs
            im.generated_color = (0, 0, 0, 1)
            tmp[mode][pid] = im
    for mode, mask in (("col", 0.0), ("msk", 1.0)):
        for dm in dmats.values():
            dm["mask"].default_value = mask
        for bm in mine:
            bm["img"].image = tmp[mode][bm["page"]]
        bpy.ops.object.bake(type="EMIT", use_selected_to_active=True, cage_extrusion=ext,
                            max_ray_distance=ext + 0.03, use_clear=True, margin=0,
                            target="IMAGE_TEXTURES")
    done = []
    for pid in pids:
        n = page_px[pid] * page_px[pid] * 4
        base = np.empty(n, dtype=np.float32)
        col = np.empty(n, dtype=np.float32)
        msk = np.empty(n, dtype=np.float32)
        color_images[pid].pixels.foreach_get(base)
        tmp["col"][pid].pixels.foreach_get(col)
        tmp["msk"][pid].pixels.foreach_get(msk)
        m = msk.reshape(-1, 4)[:, :1]
        b = base.reshape(-1, 4)
        b[:, :3] = b[:, :3] * (1.0 - m) + col.reshape(-1, 4)[:, :3] * m
        color_images[pid].pixels.foreach_set(base)
        color_images[pid].update()
        covered = int((m > 0.5).sum())
        partial = float(((m > 0.02) & (m < 0.98)).mean())
        page = next(p for p in pages if p["id"] == pid)
        done.append({"page": f"{page['atlas']}_{page['index']}", "texels": covered,
                     "partial_share": round(partial, 5)})
        log(f"decals onto {page['atlas']} page {page['index']}: {covered} texels")
        del base, col, msk, m, b
    for bm in mine:
        bm["img"].image = color_images[bm["page"]]
    for ims in tmp.values():
        for im in ims.values():
            bpy.data.images.remove(im)
    bpy.data.objects.remove(tproxy, do_unlink=True)
    bpy.data.objects.remove(dproxy, do_unlink=True)
    # decals get their own materials back (kept decals keep their flat colours)
    for d in decals:
        ob = bpy.data.objects[d["name"]]
        ob.data.materials.clear()
        for mt in d.pop("_orig"):
            ob.data.materials.append(mt)
    for dm in dmats.values():
        bpy.data.materials.remove(dm["mat"])
    return {"cage_extrusion_m": round(ext, 3), "targets": targets, "pages": done}


if __name__ == "__main__":
    main()
