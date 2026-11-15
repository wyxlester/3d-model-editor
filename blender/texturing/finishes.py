"""Procedural finishes: which finish a surface gets, and its Cycles node graph (plan Step 2).

A finish turns a flat spec colour into a worn, real-world surface: paint mottling, plate seams,
grime in corners (masked by the Ambient Occlusion node), rust streaks running down walls, edge wear
(masked by the Bevel node), and a procedural bump that the bake turns into the tangent normal map.

- Coordinates are WORLD metres (Geometry > Position), so detail has a real size whatever the part:
  hull plates are 2 m high and 8 m long, deck panels 3 x 9 m, non-skid grain a few centimetres.
  Plate seams of neighbouring parts line up, because they share the world grid.
- The spec's base colour stays the tint: every finish multiplies and mixes around it, so a user's
  colours still decide the look.
- ``finish_for`` is plain Python (unit tested without Blender). ``build`` needs ``bpy``.
"""

import re

# --------------------------------------------------------------------------------------------
# The finishes
# --------------------------------------------------------------------------------------------
# rough/metal: the finish's own values (None = keep the spec's). metal_min: floor on the spec value.
# seams: (Lu, Lv, width_m) plate grid on the plane facing the surface, u along the ship where it can.
# Strengths are 0..1 mix amounts; bump heights are metres.
_BASE = dict(rough=None, rough_var=0.06, metal=None, metal_min=0.0, mottle=0.08, fine=0.05,
             seams=None, seam_dark=0.0, seam_raise=0.0, grime=0.2, streak=0.1, stain=0.0,
             wear=0.1, grain_scale=30.0, grain_amp=0.0, oilcan=0.0, pattern=None)

FINISHES = {
    # bump heights are sized for 4-6 cm texels: a weld seam a few mm proud over 2 cm reads in the
    # normal map, plating "oil-canning" a couple of cm over a few metres catches grazing light
    "deck_nonskid": dict(_BASE, rough=0.9, rough_var=0.06, metal=0.0, mottle=0.16, fine=0.12,
                         seams=(9.0, 3.0, 0.02), seam_dark=0.22, seam_raise=0.004, grime=0.45,
                         streak=0.0, stain=0.45, wear=0.18, grain_scale=10.0, grain_amp=0.0025,
                         oilcan=0.02),
    "hull_paint": dict(_BASE, rough=0.55, rough_var=0.08, metal=0.0, mottle=0.06, fine=0.03,
                       seams=(8.0, 2.0, 0.02), seam_dark=0.08, seam_raise=0.005, grime=0.25,
                       streak=0.3, wear=0.2, grain_scale=12.0, grain_amp=0.0005, oilcan=0.03),
    "antifouling": dict(_BASE, rough=0.8, rough_var=0.08, metal=0.0, mottle=0.14, fine=0.08,
                        seams=(8.0, 2.0, 0.02), seam_dark=0.05, seam_raise=0.004, grime=0.15,
                        streak=0.12, wear=0.05, grain_scale=10.0, grain_amp=0.001, oilcan=0.03),
    "boot_top": dict(_BASE, rough=0.7, rough_var=0.08, metal=0.0, mottle=0.1, fine=0.06,
                     seams=(8.0, 2.0, 0.02), seam_dark=0.0, seam_raise=0.004, grime=0.1,
                     streak=0.15, wear=0.25, oilcan=0.03),
    "superstructure_paint": dict(_BASE, rough=0.55, rough_var=0.08, metal=0.0, mottle=0.05,
                                 fine=0.03, seams=(3.0, 1.5, 0.015), seam_dark=0.06,
                                 seam_raise=0.003, grime=0.32, streak=0.22, wear=0.25,
                                 grain_scale=12.0, grain_amp=0.0004, oilcan=0.012),
    "bare_metal": dict(_BASE, rough=0.35, rough_var=0.12, metal=None, metal_min=0.8, mottle=0.06,
                       fine=0.08, grime=0.2, streak=0.05, wear=0.0, grain_scale=20.0,
                       grain_amp=0.0005),
    "glass": dict(_BASE, rough=0.06, rough_var=0.04, metal=None, mottle=0.02, fine=0.0, grime=0.2,
                  streak=0.15, wear=0.0),
    "rubber": dict(_BASE, rough=0.9, rough_var=0.05, metal=0.0, mottle=0.08, fine=0.08, grime=0.1,
                   streak=0.0, wear=0.0, grain_scale=10.0, grain_amp=0.002),
    "teak": dict(_BASE, rough=0.75, rough_var=0.1, metal=0.0, mottle=0.1, fine=0.1, grime=0.3,
                 streak=0.0, stain=0.15, wear=0.0, grain_amp=0.0006, pattern="planks"),
    "canvas": dict(_BASE, rough=0.95, rough_var=0.03, metal=0.0, mottle=0.1, fine=0.15, grime=0.3,
                   streak=0.05, wear=0.0, grain_scale=15.0, grain_amp=0.002),
    # not in the brief's list: what anything unrecognised falls back on (keeps the spec's values)
    "generic": dict(_BASE),
}

FINISH_NAMES = tuple(FINISHES)

# --------------------------------------------------------------------------------------------
# Which finish a surface gets
# --------------------------------------------------------------------------------------------
# Material names come from blender/ships/*.py (custom.py, _merchant.py, sampan.py ...). Full match
# on the lower-case name, with Blender's ".001" suffix removed. First rule wins.
MATERIAL_RULES = [
    (r"deck|deck_.*|.*_deck|walkway.*|non_?skid.*", "deck_nonskid"),
    (r"timber|teak.*|wood.*|planking.*", "teak"),
    (r"hull_topsides|hull_paint|hull_side.*|topsides|bulwark|gunwale_paint", "hull_paint"),
    (r"hull_antifouling|hull_bottom|antifouling|bottom_paint", "antifouling"),
    (r"hull_boot_top|boot_?top|waterline.*", "boot_top"),
    (r"(hull_)?glass(_.*)?|window.*|porthole.*", "glass"),
    (r"rail|railing.*|bronze|brass|propeller.*|thruster.*|drive_leg|outboard_leg|chrome|"
     r"stainless.*|bare_metal.*", "bare_metal"),
    (r"fender.*|tyre.*|tire.*|rubber.*", "rubber"),
    (r"canopy.*|canvas.*|awning.*|tarp.*|net|nets|netting.*", "canvas"),
    (r"superstructure(_.*)?|arch|mast|funnel(_.*)?|crane|lifeboat|hatch_cover|box_\d+|pipework|"
     r"steel|house.*|bridge.*", "superstructure_paint"),
]
_MAT_RE = [(re.compile(p), f) for p, f in MATERIAL_RULES]

# Materials without a meaningful name (custom ops' mat_3d3f42 colour names, overrides): the object
# name decides. Tokens of the part names (Group__Part__Sub, contract section 2).
_GLASS_T = {"window", "windows", "glass", "porthole", "portholes", "windscreen"}
_CANVAS_T = {"net", "nets", "netting", "canvas", "awning", "tarp", "canopy", "cover"}
_RUBBER_T = {"fender", "fenders", "tyre", "tyres", "tire", "tires", "rubber"}
_METAL_T = {"propeller", "propellers", "prop", "screw", "shaft"}
_DECK_GROUPS = {"flightdeck", "elevators", "elevator", "helideck", "helipad"}
_DECK_PARTS = {"main", "flight", "elevator", "hangar", "plate", "well", "fwd", "aft", "upper",
               "lower", "main_deck"}
_HULL_GROUPS = {"hull", "stern", "bow", "transom", "bulb", "keel"}
_SUPER_GROUPS = {"island", "superstructure", "house", "deckhouse", "bridge", "funnel", "mast",
                 "sponsons", "sponson", "catwalks", "catwalk", "wheelhouse", "cabin"}

ANONYMOUS_RE = re.compile(r"mat_[0-9a-f]{6}|mat_override_.*|material(_\d+)?")


def _clean(name):
    return re.sub(r"\.\d{3}$", "", (name or "").strip()).lower()


def _tokens(segment):
    return {t.lower() for t in segment.split("_") if t}


def finish_for(material_name, object_name="", roughness=0.6, metallic=0.0):
    """(finish, reason) for one material on one object. Material name first, then the object's
    part name, then the material's own roughness and metallic values."""
    m = _clean(material_name)
    if m and not ANONYMOUS_RE.fullmatch(m):
        for rx, fin in _MAT_RE:
            if rx.fullmatch(m):
                return fin, f"material {m!r}"
    segs = [s for s in (object_name or "").split("__") if s]
    if segs:
        group = segs[0].lower()
        rest = segs[1:]
        last = _tokens(rest[-1]) if rest else set()
        every = set().union(*(_tokens(s) for s in rest)) if rest else set()
        if last & _GLASS_T:
            return "glass", f"object {object_name!r}"
        if last & _CANVAS_T:
            return "canvas", f"object {object_name!r}"
        if last & _RUBBER_T or group in ("fenders", "fender"):
            return "rubber", f"object {object_name!r}"
        if every & _METAL_T:
            return "bare_metal", f"object {object_name!r}"
        part0 = rest[0].lower() if rest else ""
        if (group in _DECK_GROUPS or "deck" in every
                or (group == "deck" and (part0.split("_")[0] in _DECK_PARTS or part0 in _DECK_PARTS))):
            return "deck_nonskid", f"object {object_name!r}"
        if group in _HULL_GROUPS:
            return "hull_paint", f"object {object_name!r}"
        if group in _SUPER_GROUPS:
            return "superstructure_paint", f"object {object_name!r}"
    if metallic >= 0.5:
        return "bare_metal", f"metallic {metallic:.2f}"
    if roughness <= 0.2:
        return "glass", f"roughness {roughness:.2f}"
    return "generic", f"roughness {roughness:.2f}, metallic {metallic:.2f}"


def resolved_values(finish, roughness, metallic):
    """The finish's base roughness and metallic for a material with the spec's values."""
    f = FINISHES[finish]
    r = roughness if f["rough"] is None else f["rough"]
    if finish == "glass":
        r = min(roughness, f["rough"])
    m = metallic if f["metal"] is None else f["metal"]
    m = max(m, f["metal_min"])
    return float(r), float(m)


# --------------------------------------------------------------------------------------------
# Node graphs (needs bpy)
# --------------------------------------------------------------------------------------------

DEFAULT_SETTINGS = {
    "ao_distance_m": 2.5,     # grime and the ORM AO channel
    "ao_samples": 16,
    "bevel_radius_m": 0.03,   # rounded edges in the normal map
    "wear_radius_m": 0.12,    # edge-wear band
    "bevel_samples": 8,
}


class _G:
    """Small node-graph builder: numbers become value nodes, sockets pass through."""

    def __init__(self, nt, x0=-1800):
        self.nt = nt
        self.x = x0
        self.y = 0

    def node(self, kind, **props):
        n = self.nt.nodes.new(kind)
        n.location = (self.x, self.y)
        self.y -= 160
        if self.y < -4000:
            self.y = 0
            self.x += 220
        for k, v in props.items():
            setattr(n, k, v)
        return n

    def _set(self, sock, v):
        if v is None:
            return
        if hasattr(v, "is_linked") or hasattr(v, "links"):  # a socket
            self.nt.links.new(v, sock)
        elif isinstance(v, (tuple, list)):
            sock.default_value = tuple(v) if len(sock.default_value) == len(v) else (*v, 1.0)
        else:
            sock.default_value = v

    def math(self, op, a, b=None, clamp=False):
        n = self.node("ShaderNodeMath", operation=op, use_clamp=clamp)
        self._set(n.inputs[0], a)
        if b is not None:
            self._set(n.inputs[1], b)
        return n.outputs[0]

    def add(self, a, b):
        return self.math("ADD", a, b)

    def mul(self, a, b):
        return self.math("MULTIPLY", a, b)

    def sub(self, a, b):
        return self.math("SUBTRACT", a, b)

    def maxf(self, a, b):
        return self.math("MAXIMUM", a, b)

    def clamp01(self, a):
        return self.math("ADD", a, 0.0, clamp=True)

    def vmath(self, op, a, b=None):
        n = self.node("ShaderNodeVectorMath", operation=op)
        self._set(n.inputs[0], a)
        if b is not None:
            self._set(n.inputs[1], b)
        return n.outputs["Value"] if op in ("DOT_PRODUCT", "LENGTH", "DISTANCE") else n.outputs[0]

    def sep(self, vec):
        n = self.node("ShaderNodeSeparateXYZ")
        self._set(n.inputs[0], vec)
        return n.outputs[0], n.outputs[1], n.outputs[2]

    def comb(self, x, y, z):
        n = self.node("ShaderNodeCombineXYZ")
        for i, v in enumerate((x, y, z)):
            self._set(n.inputs[i], v)
        return n.outputs[0]

    def noise(self, vec, scale, detail=2.0, rough=0.5):
        n = self.node("ShaderNodeTexNoise")
        n.noise_dimensions = "3D"
        self._set(n.inputs["Vector"], vec)
        n.inputs["Scale"].default_value = scale
        n.inputs["Detail"].default_value = detail
        n.inputs["Roughness"].default_value = rough
        return n.outputs["Fac"]

    def white(self, vec):
        n = self.node("ShaderNodeTexWhiteNoise")
        n.noise_dimensions = "3D"
        self._set(n.inputs["Vector"], vec)
        return n.outputs["Value"]

    def ramp(self, v, lo, hi):
        """0 below lo, 1 above hi (or the reverse when lo > hi), smoothstep between."""
        n = self.node("ShaderNodeMapRange", interpolation_type="SMOOTHSTEP", clamp=True)
        self._set(n.inputs["Value"], v)
        n.inputs["From Min"].default_value = lo
        n.inputs["From Max"].default_value = hi
        n.inputs["To Min"].default_value = 0.0
        n.inputs["To Max"].default_value = 1.0
        return n.outputs["Result"]

    def mix(self, fac, a, b, blend="MIX"):
        n = self.node("ShaderNodeMix", data_type="RGBA", blend_type=blend, clamp_factor=True)
        ins = [s for s in n.inputs if s.type == "RGBA"]
        self._set(n.inputs[0], fac)
        self._set(ins[0], a)
        self._set(ins[1], b)
        return next(s for s in n.outputs if s.type == "RGBA")

    def mixf(self, fac, a, b):
        n = self.node("ShaderNodeMix", data_type="FLOAT", clamp_factor=True)
        ins = [s for s in n.inputs if s.type == "VALUE"]
        self._set(ins[0], fac)   # Factor
        self._set(ins[1], a)
        self._set(ins[2], b)
        return next(s for s in n.outputs if s.type == "VALUE")

    def scale_col(self, col, s):
        """Colour x scalar (Mix node in MULTIPLY mode with a grey)."""
        n = self.node("ShaderNodeVectorMath", operation="SCALE")
        self._set(n.inputs[0], col)
        self._set(n.inputs["Scale"], s)
        return n.outputs[0]


def _grid_dist(g, x, length):
    """Metres from x to the nearest multiple of ``length``."""
    f = g.math("FRACT", g.math("DIVIDE", x, length))
    return g.mul(g.math("MINIMUM", f, g.sub(1.0, f)), length)


def _plate_seams(g, u, v, lu, lv, width):
    """Seam mask (1 on a seam) of a plate grid lu x lv, rows staggered by half a plate."""
    row = g.math("FLOOR", g.math("DIVIDE", v, lv))
    odd = g.mul(g.math("FRACT", g.mul(row, 0.5)), 2.0)         # 0 or 1
    us = g.add(u, g.mul(odd, lu * 0.5))
    du = _grid_dist(g, us, lu)
    dv = _grid_dist(g, v, lv)
    return g.maxf(g.ramp(du, width, 0.0), g.ramp(dv, width, 0.0))


def _projected_seams(g, pos, nrm, seams):
    """Plate seams on the plane each surface faces: walls facing +-Y use (x, z), +-X use (y, z),
    decks and bottoms (x, y). Blended by |n|^4 so curved hulls change plane smoothly."""
    lu, lv, w = seams
    px, py, pz = g.sep(pos)
    nx, ny, nz = g.sep(nrm)
    wx = g.math("POWER", g.math("ABSOLUTE", nx), 4.0)
    wy = g.math("POWER", g.math("ABSOLUTE", ny), 4.0)
    wz = g.math("POWER", g.math("ABSOLUTE", nz), 4.0)
    tot = g.add(g.add(wx, wy), g.add(wz, 1e-4))
    sx = _plate_seams(g, py, pz, lu, lv, w)
    sy = _plate_seams(g, px, pz, lu, lv, w)
    sz = _plate_seams(g, px, py, lu, lv, w)
    acc = g.add(g.add(g.mul(sx, wx), g.mul(sy, wy)), g.mul(sz, wz))
    return g.math("DIVIDE", acc, tot)


def _planks(g, pos, tint):
    """Teak deck: 15 cm planks along the ship, 5 m butt joints, dark caulking. Returns
    (colour, seam mask, height)."""
    px, py, _ = g.sep(pos)
    width, length = 0.15, 5.0
    row = g.math("FLOOR", g.math("DIVIDE", py, width))
    off = g.mul(g.white(g.comb(row, 0.0, 0.0)), length)
    u = g.add(px, off)
    seam_w = _grid_dist(g, py, width)
    butt = _grid_dist(g, u, length)
    caulk = g.maxf(g.ramp(seam_w, 0.008, 0.002), g.ramp(butt, 0.006, 0.0))
    plank = g.white(g.comb(row, g.math("FLOOR", g.math("DIVIDE", u, length)), 1.0))
    grain = g.noise(g.comb(g.mul(px, 0.4), g.mul(py, 25.0), 0.0), 3.0, 4.0, 0.6)
    shade = g.add(0.85, g.add(g.mul(plank, 0.25), g.mul(g.sub(grain, 0.5), 0.3)))
    col = g.scale_col(tint, shade)
    col = g.mix(caulk, col, (0.02, 0.018, 0.015))
    height = g.add(g.mul(caulk, -0.002), g.mul(grain, 0.0004))
    return col, caulk, height


def build(nt, finish, tint, roughness, metallic, settings=None, edges=True):
    """Add ``finish``'s graph to node tree ``nt``. Returns sockets:
    color (linear RGB), roughness, metallic, normal (bump on bevel), ao (whole-ship AO, 0..1).
    ``edges=False`` leaves out the Bevel nodes (edge wear, rounded edges): decals use it, since
    Bevel on a joined proxy of touching decals would round edges that are not there."""
    if finish not in FINISHES:
        raise ValueError(f"unknown finish {finish!r}; finishes: {FINISH_NAMES}")
    s = dict(DEFAULT_SETTINGS, **(settings or {}))
    f = FINISHES[finish]
    rough0, metal0 = resolved_values(finish, roughness, metallic)
    tint = tuple(float(c) for c in tint[:3])
    g = _G(nt)

    geo = g.node("ShaderNodeNewGeometry")
    pos, nrm = geo.outputs["Position"], geo.outputs["Normal"]
    _, _, nz = g.sep(nrm)
    side = g.sub(1.0, g.math("ABSOLUTE", nz))          # 1 on walls, 0 on decks

    ao_node = g.node("ShaderNodeAmbientOcclusion", samples=int(s["ao_samples"]), inside=False,
                     only_local=False)
    ao_node.inputs["Distance"].default_value = float(s["ao_distance_m"])
    ao = ao_node.outputs["AO"]

    edge = None
    if edges:
        wear_bevel = g.node("ShaderNodeBevel", samples=int(s["bevel_samples"]))
        wear_bevel.inputs["Radius"].default_value = float(s["wear_radius_m"])
        edge = g.sub(1.0, g.vmath("DOT_PRODUCT", wear_bevel.outputs["Normal"], nrm))

    big = g.noise(pos, 0.12, 3.0, 0.55)        # ~8 m blotches
    mid = g.noise(pos, 0.9, 3.0, 0.55)         # ~1 m
    fine = g.noise(pos, 6.0, 2.0, 0.5)         # ~15 cm

    # colour: tint, mottled
    shade = g.add(1.0, g.add(g.mul(g.sub(big, 0.5), 2.0 * f["mottle"]),
                             g.mul(g.sub(fine, 0.5), 2.0 * f["fine"])))
    seam = None
    height = g.mul(g.noise(pos, f["grain_scale"], 2.0, 0.6), f["grain_amp"])
    if f["pattern"] == "planks":
        col, seam, h2 = _planks(g, pos, tint)
        col = g.scale_col(col, shade)
        height = g.add(height, h2)
    else:
        col = g.scale_col(tint, shade)
        if f["seams"]:
            seam = _projected_seams(g, pos, nrm, f["seams"])
            col = g.scale_col(col, g.sub(1.0, g.mul(seam, f["seam_dark"])))
            height = g.add(height, g.mul(seam, f["seam_raise"]))
    if f["oilcan"]:
        height = g.add(height, g.mul(g.noise(pos, 0.35, 1.0, 0.5), f["oilcan"]))

    # deck stains (oil, tyre rubber): big soft blobs
    if f["stain"]:
        blob = g.ramp(g.noise(pos, 0.07, 4.0, 0.6), 0.52, 0.68)
        streaky = g.ramp(g.noise(g.comb(g.mul(g.sep(pos)[0], 0.15), g.sep(pos)[1], 0.0), 0.6,
                                 3.0, 0.6), 0.55, 0.7)
        st = g.mul(g.maxf(blob, g.mul(streaky, 0.6)), f["stain"])
        col = g.mix(st, col, g.scale_col(col, 0.55))

    # grime where the AO is dark, broken up by noise
    patch = g.noise(pos, 0.5, 3.0, 0.6)
    grime = None
    if f["grime"]:
        grime = g.clamp01(g.mul(g.mul(g.ramp(ao, 0.95, 0.45), g.add(0.4, g.mul(patch, 1.2))),
                                f["grime"]))
        col = g.mix(grime, col, g.mix(0.5, g.scale_col(col, 0.45), (0.05, 0.045, 0.035), "MIX"))

    # rust streaks running down walls, stronger under things (low AO) and in patches
    if f["streak"]:
        px, py, pz = g.sep(pos)
        # ~10 cm wide, metres long; only the top of the noise range, so streaks stay sparse
        sv = g.comb(g.mul(px, 9.0), g.mul(py, 9.0), g.mul(pz, 0.15))
        streak = g.ramp(g.noise(sv, 1.0, 4.0, 0.55), 0.62, 0.78)
        streak = g.mul(g.mul(streak, side), g.add(0.35, g.mul(g.ramp(ao, 1.0, 0.6), 0.65)))
        streak = g.clamp01(g.mul(g.mul(streak, g.add(0.3, patch)), f["streak"]))
        col = g.mix(streak, col, g.mix(0.6, g.scale_col(col, 0.6), (0.18, 0.075, 0.03)))

    # edge wear: primer / bare steel on convex edges, in broken patches
    wear = None
    if f["wear"] and edge is not None:
        wear = g.mul(g.mul(g.ramp(edge, 0.015, 0.12), g.ramp(g.noise(pos, 2.5, 3.0, 0.6), 0.5,
                                                              0.64)), f["wear"])
        col = g.mix(wear, col, (0.15, 0.1, 0.07))     # primer and rust showing through

    # roughness and metallic
    rough = g.add(rough0, g.mul(g.sub(mid, 0.5), 2.0 * f["rough_var"]))
    if grime is not None:
        rough = g.add(rough, g.mul(grime, 0.12))
    if wear is not None:
        rough = g.mixf(wear, rough, 0.4)
    rough = g.math("MAXIMUM", g.math("MINIMUM", rough, 1.0), 0.03)
    metal = metal0
    if wear is not None and metal0 < 0.5:
        metal = g.mixf(wear, metal0, 0.7)

    # normal: procedural bump on a slightly rounded edge
    bump = g.node("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 1.0
    bump.inputs["Distance"].default_value = 1.0
    g._set(bump.inputs["Height"], height)
    if edges:
        bevel = g.node("ShaderNodeBevel", samples=int(s["bevel_samples"]))
        bevel.inputs["Radius"].default_value = float(s["bevel_radius_m"])
        g._set(bump.inputs["Normal"], bevel.outputs["Normal"])

    return {"color": col, "roughness": rough, "metallic": metal, "normal": bump.outputs["Normal"],
            "ao": ao}
