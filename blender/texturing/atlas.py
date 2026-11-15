"""Atlases and the shared ``BakeUV`` map (plan Step 2).

Every face goes to one of four atlases, by its finish and its object:

- Deck: deck finishes (non-skid, teak);
- Hull: hull paint, antifouling, boot top;
- Superstructure: anything else on a large object (``superstructure_min_area_m2``) or in a house
  group (Island, Superstructure, Funnel ...);
- Details: the rest (weapons, rails, radars, small fittings).

An atlas has one or more square pages. The page count comes from the atlas's surface area and a
target texel size (``texel_m``), so a 330 m carrier deck spans several 4096 px pages the way the
stock Nimitz spans 17. Faces facing straight down get ``down_factor`` of the density: nobody sees the
underside of a flight deck.

UVs: the deck's up-facing faces get a top projection (world X/Y), everything else smart project.
Large objects are first cut along a world grid (``tile_m``) so a 330 m deck or hull side packs as
squarish islands instead of one long strip. Islands are scaled to world metres, spread over the
pages, then each page is packed with a margin. Objects keep their names, origins, parents and
joints: only geometry is cut and UVs added.

The page arithmetic and island grouping are plain Python (unit tested). The rest needs ``bpy``.
"""

import math
import time

ATLAS_NAMES = ("Deck", "Hull", "Superstructure", "Details")

DEFAULT_ATLASES = {
    # page_px: page size; texel_m: target size of one texel; max_pages: cap per atlas
    "Deck": {"page_px": 4096, "texel_m": 0.06, "max_pages": 8, "down_factor": 0.25},
    "Hull": {"page_px": 4096, "texel_m": 0.08, "max_pages": 8, "down_factor": 0.5},
    "Superstructure": {"page_px": 2048, "texel_m": 0.05, "max_pages": 8, "down_factor": 0.25},
    "Details": {"page_px": 2048, "texel_m": 0.03, "max_pages": 8, "down_factor": 0.25},
}

DEFAULT_LAYOUT = {
    "tile_m": 48.0,                    # cut objects larger than this along a world grid
    "superstructure_min_area_m2": 40.0,
    "pack_efficiency": 0.62,           # share of a page that packing fills (page-count estimate)
    "margin_px": 8,                    # gap between islands, at each page's own size
    "angle_limit_deg": 66.0,           # smart project
    "up_cos": 0.7,                     # deck faces with normal z above this get the top projection
    "down_cos": -0.7,                  # faces with normal z below this get down_factor
    "pack_shape": "AABB",              # shape_method; CONCAVE packed Lincoln no tighter, 60x slower
    "pack_rotate": "CARDINAL",         # rotate_method: CARDINAL (90 degree steps) or ANY (slower)
}

DECK_FINISHES = {"deck_nonskid", "teak"}
HULL_FINISHES = {"hull_paint", "antifouling", "boot_top"}
HOUSE_GROUPS = {"island", "superstructure", "house", "deckhouse", "bridge", "funnel",
                "wheelhouse", "cabin"}


# --------------------------------------------------------------------------------------------
# Plain Python
# --------------------------------------------------------------------------------------------

def atlas_for(finish, object_name="", object_area_m2=0.0, min_area_m2=40.0):
    """The atlas a face with ``finish`` on object ``object_name`` (world area) goes to."""
    if finish in DECK_FINISHES:
        return "Deck"
    if finish in HULL_FINISHES:
        return "Hull"
    group = (object_name or "").split("__")[0].lower()
    if group in HOUSE_GROUPS or object_area_m2 >= min_area_m2:
        return "Superstructure"
    return "Details"


def page_count(area_m2, texel_m, page_px, efficiency=0.62, max_pages=8):
    """(pages, capped): how many page_px pages hold ``area_m2`` at ``texel_m`` per texel."""
    if area_m2 <= 0:
        return 0, False
    per_page = (page_px * texel_m) ** 2 * efficiency
    n = max(1, int(math.ceil(area_m2 / per_page - 1e-9)))
    return (min(n, max_pages), n > max_pages)


def assign_pages(areas, pages):
    """Spread islands (their areas) over ``pages`` pages, largest first into the emptiest page.
    Returns one page index per island, in input order."""
    if pages <= 1:
        return [0] * len(areas)
    load = [0.0] * pages
    out = [0] * len(areas)
    for i in sorted(range(len(areas)), key=lambda k: -areas[k]):
        p = min(range(pages), key=lambda k: (load[k], k))
        out[i] = p
        load[p] += areas[i]
    return out


def group_islands(n_faces, shared_edges):
    """Union-find: faces 0..n_faces-1 joined by ``shared_edges`` (pairs of face indices whose
    common edge has matching UVs on both sides). Returns a list of islands (sorted face lists)."""
    parent = list(range(n_faces))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for a, b in shared_edges:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
    groups = {}
    for f in range(n_faces):
        groups.setdefault(find(f), []).append(f)
    return [sorted(v) for _, v in sorted(groups.items())]


def poly_area_2d(pts):
    """Shoelace area of a 2D polygon."""
    a = 0.0
    n = len(pts)
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        a += x0 * y1 - x1 * y0
    return abs(a) * 0.5


def grid_cuts(lo, hi, tile):
    """World coordinates of the cut planes for an extent lo..hi: the multiples of ``tile``
    strictly inside it, or none when the extent is no larger than a tile."""
    if tile <= 0 or hi - lo <= tile:
        return []
    k0 = math.floor(lo / tile) + 1
    k1 = math.ceil(hi / tile) - 1
    return [k * tile for k in range(k0, k1 + 1) if lo + 1e-6 < k * tile < hi - 1e-6]


# --------------------------------------------------------------------------------------------
# Blender
# --------------------------------------------------------------------------------------------

UV_NAME = "BakeUV"
PAGE_ATTR = "me3d_page"


def _bpy():
    import bmesh
    import bpy
    from mathutils import Matrix, Vector
    return bpy, bmesh, Matrix, Vector


def make_single_user(objects):
    """Linked duplicates share a mesh, but each needs its own place in the atlas."""
    n = 0
    for ob in objects:
        if ob.data.users > 1:
            ob.data = ob.data.copy()
            n += 1
    return n


def world_area(ob):
    mw = ob.matrix_world
    tot = 0.0
    for p in ob.data.polygons:
        vs = [mw @ ob.data.vertices[i].co for i in p.vertices]
        for k in range(1, len(vs) - 1):
            tot += (vs[k] - vs[0]).cross(vs[k + 1] - vs[0]).length * 0.5
    return tot


def cut_tiles(ob, tile_m):
    """Bisect a large mesh along the world planes x, y, z = k * tile_m. Returns the cut count."""
    bpy, bmesh, Matrix, Vector = _bpy()
    me = ob.data
    if not me.polygons or tile_m <= 0:
        return 0
    mw = ob.matrix_world
    pts = [mw @ v.co for v in me.vertices]
    imw = mw.inverted()
    nrm_m = mw.to_3x3().transposed()  # world normal -> local normal (inverse transpose)
    bm = bmesh.new()
    bm.from_mesh(me)
    cuts = 0
    for axis in range(3):
        lo = min(p[axis] for p in pts)
        hi = max(p[axis] for p in pts)
        for c in grid_cuts(lo, hi, tile_m):
            wco = Vector((0.0, 0.0, 0.0))
            wco[axis] = c
            wno = Vector((0.0, 0.0, 0.0))
            wno[axis] = 1.0
            geom = list(bm.verts) + list(bm.edges) + list(bm.faces)
            bmesh.ops.bisect_plane(bm, geom=geom, plane_co=imw @ wco,
                                   plane_no=(nrm_m @ wno).normalized(), dist=1e-5)
            cuts += 1
    if cuts:
        bm.to_mesh(me)
        me.update()
    bm.free()
    return cuts


def separated_groups(boxes, pad):
    """Greedy colouring: split items (world AABBs ``(lo, hi)``, largest first) into groups in which
    no two boxes come within ``pad`` of each other. Cycles' Bevel node only sees its own object,
    so parts joined into one bake proxy must not touch. Returns lists of indices."""
    order = sorted(range(len(boxes)), key=lambda i: -sum(
        (boxes[i][1][k] - boxes[i][0][k]) for k in range(3)))
    groups = []

    def clash(a, b):
        return all(a[0][k] - pad <= b[1][k] and b[0][k] - pad <= a[1][k] for k in range(3))

    for i in order:
        for g in groups:
            if not any(clash(boxes[i], boxes[j]) for j in g):
                g.append(i)
                break
        else:
            groups.append([i])
    return [sorted(g) for g in groups]


def _set_mode(objects, mode):
    bpy = _bpy()[0]
    vl = bpy.context.view_layer
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for ob in vl.objects:
        ob.select_set(False)
    for ob in objects:
        ob.select_set(True)
    if objects:
        vl.objects.active = objects[0]
    if mode != "OBJECT" and objects:
        bpy.ops.object.mode_set(mode=mode)


def _select_faces_edit(objects, wanted):
    """In edit mode: select exactly the faces in wanted[ob.name] (a set of indices)."""
    bpy, bmesh, _, _ = _bpy()
    for ob in objects:
        bm = bmesh.from_edit_mesh(ob.data)
        bm.faces.ensure_lookup_table()
        uvl = bm.loops.layers.uv.get(UV_NAME)
        sel = wanted.get(ob.name, ())
        for v in bm.verts:
            v.select = False
        for e in bm.edges:
            e.select = False
        for f in bm.faces:
            on = f.index in sel
            f.select = on
            if on:
                for v in f.verts:
                    v.select = True
                for e in f.edges:
                    e.select = True
            if uvl is not None:
                for lp in f.loops:
                    lu = lp[uvl]
                    for attr in ("select", "select_edge"):
                        if hasattr(lu, attr):
                            setattr(lu, attr, on)
        bm.select_flush_mode()
        bmesh.update_edit_mesh(ob.data, loop_triangles=False, destructive=False)


def _op_kwargs(op, wanted):
    props = set(op.get_rna_type().properties.keys())
    return {k: v for k, v in wanted.items() if k in props}


def layout(objects, slot_atlas, atlases=None, settings=None, log=print):
    """Lay out ``BakeUV`` on ``objects``. ``slot_atlas[ob.name][slot]`` names each material slot's
    atlas. Writes a FACE int attribute ``me3d_page`` (global page id) and returns the page list:
    [{"id", "atlas", "index", "px", "faces", "area_m2", "texel_m", "target_texel_m"}...] plus warnings."""
    bpy, bmesh, Matrix, Vector = _bpy()
    cfg_a = {k: dict(DEFAULT_ATLASES[k], **((atlases or {}).get(k) or {})) for k in ATLAS_NAMES}
    cfg = dict(DEFAULT_LAYOUT, **(settings or {}))
    warnings = []
    up_cos, down_cos = cfg["up_cos"], cfg["down_cos"]
    tool = bpy.context.scene.tool_settings
    tool.use_uv_select_sync = True
    if hasattr(tool, "mesh_select_mode"):
        tool.mesh_select_mode = (False, False, True)

    # 1. UV layer, face atlases, deck top faces
    face_atlas = {}   # ob.name -> list of atlas name per face (None: no atlas)
    top_faces = {}    # ob.name -> set of faces that get the top projection
    for ob in objects:
        me = ob.data
        uv = me.uv_layers.get(UV_NAME) or me.uv_layers.new(name=UV_NAME)
        me.uv_layers.active = uv
        uv.active_render = True
        nm = ob.matrix_world.to_3x3().inverted().transposed()
        fa, tops = [], set()
        for p in me.polygons:
            a = slot_atlas.get(ob.name, {}).get(p.material_index)
            fa.append(a)
            if a == "Deck" and (nm @ p.normal).normalized().z > up_cos:
                tops.add(p.index)
        face_atlas[ob.name] = fa
        top_faces[ob.name] = tops

    t_start = time.time()
    # 2. smart project everything that is not a deck top, atlas by atlas
    _set_mode(objects, "EDIT")
    try:
        for atlas in ATLAS_NAMES:
            want = {ob.name: {i for i, a in enumerate(face_atlas[ob.name])
                              if a == atlas and i not in top_faces[ob.name]} for ob in objects}
            if not any(want.values()):
                continue
            _select_faces_edit(objects, want)
            bpy.ops.uv.smart_project(**_op_kwargs(bpy.ops.uv.smart_project, {
                "angle_limit": math.radians(cfg["angle_limit_deg"]), "island_margin": 0.0,
                "area_weight": 0.0, "correct_aspect": True, "scale_to_bounds": False}))
    finally:
        _set_mode(objects, "OBJECT")

    log(f"[atlas] smart project: {time.time() - t_start:.1f} s")
    t_start = time.time()
    # 3. top projection, islands, world-metre scale
    islands = []   # dicts: ob, atlas, faces, area (scaled), down
    for ob in objects:
        me = ob.data
        if not me.polygons:
            continue
        bm = bmesh.new()
        bm.from_mesh(me)
        bm.faces.ensure_lookup_table()
        uvl = bm.loops.layers.uv[UV_NAME]
        mw = ob.matrix_world
        nm = mw.to_3x3().inverted().transposed()
        tile = cfg["tile_m"] if cfg["tile_m"] > 0 else 1e9
        fa = face_atlas[ob.name]
        tops = top_faces[ob.name]
        for i in tops:
            f = bm.faces[i]
            c = mw @ f.calc_center_median()
            ox = math.floor(c.x / tile) * 1000.0   # separate islands per world tile
            oy = math.floor(c.y / tile) * 1000.0
            for lp in f.loops:
                w = mw @ lp.vert.co
                lp[uvl].uv = (w.x + ox, w.y + oy)
        # islands: faces of one atlas and one world tile, joined over edges with matching UVs.
        # The tile split is what turns a 330 m hull side into squarish pieces: cut_tiles made the
        # edges, this makes them seams.
        cell = []
        for f in bm.faces:
            c = mw @ f.calc_center_median()
            cell.append((math.floor(c.x / tile), math.floor(c.y / tile), math.floor(c.z / tile)))
        pairs = []
        for e in bm.edges:
            lf = e.link_loops
            if len(lf) != 2:
                continue
            l1, l2 = lf
            f1, f2 = l1.face.index, l2.face.index
            if fa[f1] is None or fa[f1] != fa[f2] or cell[f1] != cell[f2]:
                continue
            uv1 = {l1.vert.index: l1[uvl].uv, l1.link_loop_next.vert.index: l1.link_loop_next[uvl].uv}
            uv2 = {l2.vert.index: l2[uvl].uv, l2.link_loop_next.vert.index: l2.link_loop_next[uvl].uv}
            if all((uv1[k] - uv2[k]).length < 1e-5 for k in uv1 if k in uv2):
                pairs.append((f1, f2))
        for isl in group_islands(len(bm.faces), pairs):
            atlas = fa[isl[0]]
            if atlas is None:
                continue
            a3 = auv = nz = 0.0
            for i in isl:
                f = bm.faces[i]
                ws = [mw @ lp.vert.co for lp in f.loops]
                fa3 = sum((ws[k] - ws[0]).cross(ws[k + 1] - ws[0]).length * 0.5
                          for k in range(1, len(ws) - 1))
                a3 += fa3
                auv += poly_area_2d([tuple(lp[uvl].uv) for lp in f.loops])
                nz += (nm @ f.normal).normalized().z * fa3
            if a3 <= 1e-10:
                a3 = 1e-10
            down = nz / a3 < down_cos
            factor = cfg_a[atlas]["down_factor"] if down else 1.0
            k = math.sqrt(a3 / max(auv, 1e-14)) * factor
            cx = sum(lp[uvl].uv.x for i in isl for lp in bm.faces[i].loops)
            cy = sum(lp[uvl].uv.y for i in isl for lp in bm.faces[i].loops)
            nl = sum(len(bm.faces[i].loops) for i in isl)
            cx, cy = cx / nl, cy / nl
            for i in isl:
                for lp in bm.faces[i].loops:
                    u, v = lp[uvl].uv
                    lp[uvl].uv = ((u - cx) * k, (v - cy) * k)
            islands.append({"ob": ob.name, "atlas": atlas, "faces": isl,
                            "area": a3 * factor * factor, "a3": a3, "down": down})
        bm.to_mesh(me)
        bm.free()

    log(f"[atlas] islands: {len(islands)} in {time.time() - t_start:.1f} s")
    # 4. pages per atlas, islands spread over them
    pages = []
    page_of = {}  # (ob, face) -> page id
    for atlas in ATLAS_NAMES:
        mine = [isl for isl in islands if isl["atlas"] == atlas]
        if not mine:
            continue
        c = cfg_a[atlas]
        total = sum(isl["area"] for isl in mine)
        n, capped = page_count(total, c["texel_m"], c["page_px"], cfg["pack_efficiency"],
                               c["max_pages"])
        if capped:
            warnings.append(f"{atlas}: {total:.0f} m2 needs more than {c['max_pages']} pages at "
                            f"{c['texel_m'] * 100:.0f} cm; capped, so texels are coarser")
        idx = assign_pages([isl["area"] for isl in mine], n)
        base = len(pages)
        for k in range(n):
            pages.append({"id": base + k, "atlas": atlas, "index": k, "px": int(c["page_px"]),
                          "target_texel_m": c["texel_m"], "faces": 0, "area_m2": 0.0})
        for isl, k in zip(mine, idx):
            isl["page"] = base + k
            pages[base + k]["faces"] += len(isl["faces"])
            pages[base + k]["area_m2"] += isl["a3"]
            for f in isl["faces"]:
                page_of[(isl["ob"], f)] = base + k

    # write the page attribute
    for ob in objects:
        me = ob.data
        attr = me.attributes.get(PAGE_ATTR) or me.attributes.new(PAGE_ATTR, "INT", "FACE")
        vals = [page_of.get((ob.name, i), -1) for i in range(len(me.polygons))]
        attr.data.foreach_set("value", vals)

    # 5. pre-place each page's islands inside 0..1 (packing picks the closest UDIM tile), then pack
    by_page = {}
    for isl in islands:
        by_page.setdefault(isl["page"], []).append(isl)
    for pid, isls in by_page.items():
        ext = 1e-9
        boxes = []
        for isl in isls:
            me = bpy.data.objects[isl["ob"]].data
            uvd = me.uv_layers[UV_NAME].data
            loops = [li for f in isl["faces"] for li in me.polygons[f].loop_indices]
            us = [uvd[li].uv[0] for li in loops]
            vs = [uvd[li].uv[1] for li in loops]
            boxes.append((me, loops, min(us), min(vs)))
            ext = max(ext, max(us) - min(us), max(vs) - min(vs))
        s = 0.9 / ext
        for me, loops, u0, v0 in boxes:
            uvd = me.uv_layers[UV_NAME].data
            for li in loops:
                u, v = uvd[li].uv
                uvd[li].uv = ((u - u0) * s + 0.05, (v - v0) * s + 0.05)

    _set_mode(objects, "EDIT")
    try:
        for p in pages:
            want = {}
            for (obn, f), pid in page_of.items():
                if pid == p["id"]:
                    want.setdefault(obn, set()).add(f)
            members = [ob for ob in objects if ob.name in want]
            _select_faces_edit(objects, want)
            margin = cfg["margin_px"] / float(p["px"])
            t_pack = time.time()
            bpy.ops.uv.pack_islands(**_op_kwargs(bpy.ops.uv.pack_islands, {
                "udim_source": "CLOSEST_UDIM", "rotate": True, "rotate_method": cfg["pack_rotate"],
                "scale": True, "merge_overlap": False, "margin_method": "FRACTION",
                "margin": margin, "pin": False, "shape_method": cfg["pack_shape"]}))
            log(f"[atlas] packed {p['atlas']} page {p['index']}: {p['faces']} faces on "
                f"{len(members)} objects ({time.time() - t_pack:.1f} s)")
    finally:
        _set_mode(objects, "OBJECT")

    # 6. measure what packing gave
    for p in pages:
        uv_area = uv_all = a3 = 0.0
        lo, hi = [1e9, 1e9], [-1e9, -1e9]
        for isl in by_page.get(p["id"], []):
            ob = bpy.data.objects[isl["ob"]]
            me = ob.data
            uvd = me.uv_layers[UV_NAME].data
            for f in isl["faces"]:
                pts = [tuple(uvd[li].uv) for li in me.polygons[f].loop_indices]
                for u, v in pts:
                    lo = [min(lo[0], u), min(lo[1], v)]
                    hi = [max(hi[0], u), max(hi[1], v)]
                fa2 = poly_area_2d(pts)
                uv_all += fa2
                if not isl["down"]:
                    uv_area += fa2
            if not isl["down"]:
                a3 += isl["a3"]
        p["uv_min"] = [round(lo[0], 4), round(lo[1], 4)]
        p["uv_max"] = [round(hi[0], 4), round(hi[1], 4)]
        p["area_m2"] = round(p["area_m2"], 1)
        p["texel_m"] = round(math.sqrt(a3 / (uv_area * p["px"] ** 2)), 4) if uv_area > 0 else None
        p["fill"] = round(uv_all, 3)   # share of the page the islands cover
        if lo[0] < -1e-4 or lo[1] < -1e-4 or hi[0] > 1 + 1e-4 or hi[1] > 1 + 1e-4:
            warnings.append(f"{p['atlas']} page {p['index']}: UVs outside 0..1 after packing "
                            f"({p['uv_min']} .. {p['uv_max']})")
    return pages, warnings
