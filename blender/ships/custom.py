"""Custom: a general monohull for any vessel the AI is asked for (docs/CONTRACT.md section 5,
``type: "custom"``).

The AI shapes it with params alone: principal dimensions, a bow form (plumb, raked, flared,
bulbous, axe), a stern form (transom, cruiser, swim_platform, open), sheer, deadrise and bilge,
colours, bulwark / rail / fender (tug fendering or a RHIB tube), hull windows, a list of
superstructure tiers stacked from the deck up, and an optional mast, radar arch and funnel.
Anything no param covers (fly-bridge furniture, cranes, guns, davits) is an AI custom op.

Frame: metres, +X bow, +Y port, +Z up, origin on the waterline at the middle of the length
overall. Every overhang (swim platform, fender, outboards, a RHIB tube's tails) is inside
``loa_m``; the hull itself is shortened to make room. ``beam_m`` is the breadth overall,
fenders included. The keel sits at minus ``draught_m`` and nothing goes below it.

Knobs are documented in catalog/ships/custom.json ("param_docs"); ``validate`` raises a
ValueError naming the param for anything nonsensical.
"""

import math

from mathutils import Matrix, Vector

from ._hull import smooth

TRI_BUDGET = 150_000
BOW_FORMS = ("plumb", "raked", "flared", "bulbous", "axe")
STERN_FORMS = ("transom", "cruiser", "swim_platform", "open")
PROPULSION = ("shaft", "azimuth", "outboard", "waterjet", "none")
STRAIT_TYPES = ("container", "tanker", "bunker_tanker", "bulker", "general_cargo", "ferry", "tug",
                "patrol", "frigate", "usv", "fishing", "pilot")
GLASS = (0.06, 0.08, 0.1)
STEEL = (0.45, 0.47, 0.5)
N_ABOVE = 8    # section points from the deck edge down to the boot top, inclusive
N_BELOW = 14   # section points below the waterline, down to the keel
DECK_T = 0.02  # the deck plate's top above the sheer line, metres
TIER_KEYS = {"x_aft_m", "x_fwd_m", "width_m", "height_m", "setback_m", "rake_deg", "round",
             "overhang_m", "overhang_aft_m", "open", "color", "windows"}
WINDOW_KEYS = {"color", "bottom_m", "height_m", "aft", "aft_inset_m"}
HULL_WINDOW_KEYS = {"x_aft_m", "x_fwd_m", "z_m", "height_m", "color"}


# ---------------------------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------------------------

def _num(v, what, lo=None, hi=None):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ValueError(f"{what} must be a number, got {v!r}")
    if (lo is not None and v < lo) or (hi is not None and v > hi):
        raise ValueError(f"{what} = {v} is outside {lo}..{hi}")
    return float(v)


def _opt_num(v, what, lo=None, hi=None):
    return None if v is None else _num(v, what, lo, hi)


def _rgb(v, what):
    if (not isinstance(v, (list, tuple)) or len(v) != 3
            or not all(isinstance(c, (int, float)) and not isinstance(c, bool) and 0 <= c <= 1
                       for c in v)):
        raise ValueError(f"{what} must be [r, g, b] with each value 0..1, got {v!r}")
    return tuple(float(c) for c in v)


def _choice(v, what, options):
    if v not in options:
        raise ValueError(f"{what} must be one of {options}, got {v!r}")
    return v


def _as_list(v, what):
    if v is None:
        return []
    if isinstance(v, dict):
        return [v]
    if isinstance(v, list) and all(isinstance(x, dict) for x in v):
        return v
    raise ValueError(f"{what} must be null, an object or a list of objects, got {v!r}")


def validate(p):
    """Check and normalise params; raises ValueError naming the param. Returns a new dict."""
    q = dict(p)
    L = q["loa_m"] = _num(p["loa_m"], "loa_m", 3, 400)
    B = q["beam_m"] = _num(p["beam_m"], "beam_m", 0.8, 80)
    D = q["depth_m"] = _num(p["depth_m"], "depth_m", 0.3, 50)
    T = q["draught_m"] = _num(p["draught_m"], "draught_m", 0.1, 30)
    if B >= L / 1.5:
        raise ValueError(f"beam_m {B} is too wide for loa_m {L} (keep loa/beam above 1.5)")
    if T >= D - 0.15:
        raise ValueError(f"draught_m {T} must be less than depth_m {D} (freeboard = depth - draught)")
    F = D - T
    q["bow_form"] = _choice(p["bow_form"], "bow_form", BOW_FORMS)
    q["stern_form"] = _choice(p["stern_form"], "stern_form", STERN_FORMS)
    q["stem_rake_deg"] = _num(p["stem_rake_deg"], "stem_rake_deg", 0, 60)
    q["transom_rake_deg"] = _num(p["transom_rake_deg"], "transom_rake_deg", 0, 30)
    q["swim_platform_m"] = _num(p["swim_platform_m"], "swim_platform_m", 0.3, 6)
    q["swim_platform_height_m"] = _num(p["swim_platform_height_m"], "swim_platform_height_m",
                                       0.0, 1.5)
    q["sheer_fwd_m"] = _num(p["sheer_fwd_m"], "sheer_fwd_m", 0, max(0.5, 0.12 * L))
    q["sheer_aft_m"] = _num(p["sheer_aft_m"], "sheer_aft_m", 0, max(0.3, 0.06 * L))
    q["deadrise_deg"] = _num(p["deadrise_deg"], "deadrise_deg", 0, 35)
    q["bilge"] = _num(p["bilge"], "bilge", 0, 1)
    q["fullness"] = _num(p["fullness"], "fullness", 0, 1)
    for k in ("hull_color", "boot_color", "boot_top_color", "deck_color", "rail_color",
              "fender_color", "mast_color", "arch_color", "funnel_color", "funnel_top_color"):
        q[k] = _rgb(p[k], k)
    q["bulwark_color"] = None if p["bulwark_color"] is None else _rgb(p["bulwark_color"],
                                                                      "bulwark_color")
    q["boot_top_m"] = _num(p["boot_top_m"], "boot_top_m", 0.02, 3)
    if q["boot_top_m"] > 0.7 * F:
        raise ValueError(f"boot_top_m {q['boot_top_m']} must be under 70% of the freeboard {F:.2f}")
    q["bulwark_m"] = _num(p["bulwark_m"], "bulwark_m", 0, 3)
    q["rail_m"] = _num(p["rail_m"], "rail_m", 0, 2)
    q["fender_m"] = _num(p["fender_m"], "fender_m", 0, 2)
    if q["fender_m"] > 0.2 * B:
        raise ValueError(f"fender_m {q['fender_m']} is more than 20% of beam_m {B}")
    q["helm_tier"] = int(_num(p["helm_tier"], "helm_tier", -1, 12))
    q["helm_x_m"] = _opt_num(p["helm_x_m"], "helm_x_m", -L / 2, L / 2)
    for k in ("mast", "radar", "radar_arch", "funnel"):
        if not isinstance(p[k], bool):
            raise ValueError(f"{k} must be true or false, got {p[k]!r}")
    q["mast_x_m"] = _opt_num(p["mast_x_m"], "mast_x_m", -L / 2, L / 2)
    q["mast_height_m"] = _num(p["mast_height_m"], "mast_height_m", 0.5, 60)
    q["arch_x_m"] = _opt_num(p["arch_x_m"], "arch_x_m", -L / 2, L / 2)
    q["arch_height_m"] = _num(p["arch_height_m"], "arch_height_m", 0.3, 10)
    q["arch_width_m"] = _num(p["arch_width_m"], "arch_width_m", 0.3, B)
    q["arch_rake_deg"] = _num(p["arch_rake_deg"], "arch_rake_deg", -10, 45)
    q["funnel_count"] = int(_num(p["funnel_count"], "funnel_count", 1, 2))
    q["funnel_x_m"] = _opt_num(p["funnel_x_m"], "funnel_x_m", -L / 2, L / 2)
    q["funnel_height_m"] = _num(p["funnel_height_m"], "funnel_height_m", 0.3, 40)
    q["funnel_length_m"] = _num(p["funnel_length_m"], "funnel_length_m", 0.2, 0.3 * L)
    q["funnel_width_m"] = _num(p["funnel_width_m"], "funnel_width_m", 0.2, 0.6 * B)
    q["funnel_rake_deg"] = _num(p["funnel_rake_deg"], "funnel_rake_deg", 0, 30)
    q["propulsion"] = _choice(p["propulsion"], "propulsion", PROPULSION)
    q["engines"] = int(_num(p["engines"], "engines", 1, 4))
    if p.get("strait_type") is not None:
        _choice(p["strait_type"], "strait_type", STRAIT_TYPES)

    hw = []
    for i, w in enumerate(_as_list(p["hull_windows"], "hull_windows"), start=1):
        what = f"hull_windows[{i}]"
        extra = set(w) - HULL_WINDOW_KEYS
        if extra:
            raise ValueError(f"{what} has unknown keys {sorted(extra)}; use {sorted(HULL_WINDOW_KEYS)}")
        d = {"x_aft_m": _num(w.get("x_aft_m"), f"{what}.x_aft_m", -L / 2, L / 2),
             "x_fwd_m": _num(w.get("x_fwd_m"), f"{what}.x_fwd_m", -L / 2, L / 2),
             "z_m": _num(w.get("z_m"), f"{what}.z_m", 0, F + q["sheer_fwd_m"]),
             "height_m": _num(w.get("height_m", 0.4), f"{what}.height_m", 0.05, 3),
             "color": _rgb(w.get("color", GLASS), f"{what}.color")}
        if d["x_fwd_m"] - d["x_aft_m"] < 0.3:
            raise ValueError(f"{what}: x_fwd_m must be at least 0.3 m forward of x_aft_m")
        if d["z_m"] - d["height_m"] / 2 < q["boot_top_m"]:
            raise ValueError(f"{what}: the window band dips into the boot top (z_m - height_m/2 "
                             f"< boot_top_m {q['boot_top_m']})")
        hw.append(d)
    q["hull_windows"] = hw

    if not isinstance(p["tiers"], list):
        raise ValueError(f"tiers must be a list of objects, got {p['tiers']!r}")
    tiers = []
    for i, t in enumerate(p["tiers"], start=1):
        what = f"tiers[{i}]"
        if not isinstance(t, dict):
            raise ValueError(f"{what} must be an object, got {t!r}")
        extra = set(t) - TIER_KEYS
        if extra:
            raise ValueError(f"{what} has unknown keys {sorted(extra)}; use {sorted(TIER_KEYS)}")
        d = {"x_aft_m": _num(t.get("x_aft_m"), f"{what}.x_aft_m", -L / 2, L / 2),
             "x_fwd_m": _num(t.get("x_fwd_m"), f"{what}.x_fwd_m", -L / 2, L / 2),
             "width_m": _num(t.get("width_m"), f"{what}.width_m", 0.2, None),
             "height_m": _num(t.get("height_m"), f"{what}.height_m", 0.2, 12),
             "setback_m": _num(t.get("setback_m", 0.0), f"{what}.setback_m", -1.0, None),
             "rake_deg": _num(t.get("rake_deg", 0.0), f"{what}.rake_deg", -30, 75),
             "round": _num(t.get("round", 0.0), f"{what}.round", 0, 1),
             "overhang_m": _num(t.get("overhang_m", 0.0), f"{what}.overhang_m", 0, 3),
             "overhang_aft_m": _num(t.get("overhang_aft_m", 0.0), f"{what}.overhang_aft_m", 0, 8),
             "open": bool(t.get("open", False)),
             "color": _rgb(t.get("color", (0.95, 0.95, 0.95)), f"{what}.color"),
             "windows": []}
        length = d["x_fwd_m"] - d["x_aft_m"]
        if length < 0.4:
            raise ValueError(f"{what}: x_fwd_m must be at least 0.4 m forward of x_aft_m")
        if d["width_m"] > B:
            raise ValueError(f"{what}.width_m {d['width_m']} is wider than the beam ({B} m)")
        top_w = d["width_m"] - 2 * d["setback_m"]
        if top_w > B:
            raise ValueError(f"{what}: its top (width_m - 2*setback_m = {top_w:.2f}) is wider than "
                             f"the beam ({B} m)")
        if top_w < 0.2:
            raise ValueError(f"{what}.setback_m {d['setback_m']} leaves no roof (top width "
                             f"{top_w:.2f} m)")
        if length - d["height_m"] * math.tan(math.radians(d["rake_deg"])) < 0.2 * length:
            raise ValueError(f"{what}.rake_deg {d['rake_deg']} is too steep for a tier "
                             f"{d['height_m']} m high and {length:.1f} m long")
        for j, w in enumerate(_as_list(t.get("windows"), f"{what}.windows"), start=1):
            ww = f"{what}.windows[{j}]"
            extra = set(w) - WINDOW_KEYS
            if extra:
                raise ValueError(f"{ww} has unknown keys {sorted(extra)}; use {sorted(WINDOW_KEYS)}")
            wd = {"color": _rgb(w.get("color", GLASS), f"{ww}.color"),
                  "bottom_m": _num(w.get("bottom_m", 0.35 * d["height_m"]), f"{ww}.bottom_m", 0,
                                   d["height_m"]),
                  "height_m": _num(w.get("height_m", 0.45 * d["height_m"]), f"{ww}.height_m", 0.05,
                                   d["height_m"]),
                  "aft": bool(w.get("aft", False)),
                  "aft_inset_m": _num(w.get("aft_inset_m", min(0.5, 0.15 * length)),
                                      f"{ww}.aft_inset_m", 0, 0.8 * length)}
            if wd["bottom_m"] + wd["height_m"] > d["height_m"] + 1e-6:
                raise ValueError(f"{ww}: bottom_m + height_m is above the tier's height_m")
            d["windows"].append(wd)
        tiers.append(d)
    q["tiers"] = tiers
    if q["helm_tier"] > len(tiers):
        raise ValueError(f"helm_tier {q['helm_tier']} but there are only {len(tiers)} tiers")
    if q["helm_tier"] >= 1 and tiers[q["helm_tier"] - 1]["height_m"] < 1.0:
        raise ValueError(f"helm_tier {q['helm_tier']} is under 1 m high; stand the helm on the "
                         "tier below or on deck (0)")
    if q["propulsion"] == "outboard" and q["stern_form"] == "cruiser":
        raise ValueError("propulsion 'outboard' needs a flat stern (transom, swim_platform or open), "
                         "not stern_form 'cruiser'")
    return q


# ---------------------------------------------------------------------------------------------
# Hull form
# ---------------------------------------------------------------------------------------------

def _cumlen(poly):
    s = [0.0]
    for (y0, z0), (y1, z1) in zip(poly, poly[1:]):
        s.append(s[-1] + math.hypot(y1 - y0, z1 - z0))
    return s


def _at_s(poly, cum, s):
    for i in range(len(poly) - 1):
        if cum[i + 1] >= s - 1e-12:
            seg = cum[i + 1] - cum[i]
            t = 0.0 if seg < 1e-12 else (s - cum[i]) / seg
            t = max(0.0, min(1.0, t))
            return (poly[i][0] + (poly[i + 1][0] - poly[i][0]) * t,
                    poly[i][1] + (poly[i + 1][1] - poly[i][1]) * t)
    return poly[-1]


def _s_at_z(poly, cum, z):
    """Arc length where a polyline with falling z crosses height z."""
    for i in range(len(poly) - 1):
        z0, z1 = poly[i][1], poly[i + 1][1]
        if z0 >= z >= z1:
            t = 0.0 if abs(z0 - z1) < 1e-12 else (z0 - z) / (z0 - z1)
            return cum[i] + t * (cum[i + 1] - cum[i])
    return cum[-1] if z < poly[-1][1] else 0.0


class CustomForm:
    """Plan and profile of the hull as functions of xi in -1 (stern) .. +1 (stem)."""

    def __init__(self, p, Lh, x0, Bh):
        self.p, self.L, self.x0, self.B = p, Lh, x0, Bh
        self.T = p["draught_m"]
        self.F = p["depth_m"] - p["draught_m"]
        self.bow, self.stern = p["bow_form"], p["stern_form"]
        f = p["fullness"]
        self.fwd0 = 0.05 + 0.35 * f
        self.aft0 = -0.3 - 0.25 * f
        rake_deg = {"plumb": 3.0, "axe": 0.0}.get(self.bow, p["stem_rake_deg"])
        h_bow = self.deck_z(1.0) - self.keel_z(1.0)
        self.rake_m = min(h_bow * math.tan(math.radians(rake_deg)), 0.3 * Lh)
        self.rake_from = max(0.2, 1.0 - 2.8 * self.rake_m / Lh)
        self.tr_m = 0.0
        if self.stern != "cruiser":
            h_tr = self.deck_z(-1.0) - self.keel_z(-1.0)
            self.tr_m = min(h_tr * math.tan(math.radians(p["transom_rake_deg"])), 0.05 * Lh)

    # -- profile -------------------------------------------------------------------------------
    def deck_z(self, xi):
        p = self.p
        return (self.F + p["sheer_fwd_m"] * smooth(0.0, 1.0, xi) ** 1.3
                + p["sheer_aft_m"] * smooth(0.0, 1.0, -xi) ** 1.5)

    def keel_z(self, xi):
        T = self.T
        k_end, start = {"axe": (-0.9, 0.93), "plumb": (-0.45, 0.72), "bulbous": (-0.55, 0.75),
                        "raked": (-0.1, 0.3), "flared": (-0.18, 0.4)}[self.bow]
        z = -T
        if xi > start:
            z = -T + (T + k_end * T) * smooth(start, 1.0, xi) ** 1.2
        if self.stern == "cruiser":
            k_aft, a0 = -0.15, 0.4
        else:
            k_aft, a0 = -0.62, 0.3
        if xi < -a0:
            z = -T + (T + k_aft * T) * smooth(a0, 1.0, -xi)
        return z

    # -- plan ----------------------------------------------------------------------------------
    def half_deck(self, xi):
        if xi > self.fwd0:
            u = min(1.0, (xi - self.fwd0) / (1.0 - self.fwd0))
            e = {"flared": 0.36, "plumb": 0.55, "raked": 0.62, "bulbous": 0.5, "axe": 0.75}[self.bow]
            return max(0.0, 1 - u * u) ** e
        if xi < self.aft0:
            u = min(1.0, (self.aft0 - xi) / (1.0 + self.aft0))
            if self.stern == "cruiser":
                return max(0.0, 1 - u ** 2.2) ** 0.5
            return 1 - 0.12 * u * u
        return 1.0

    def half_wl(self, xi):
        """Chine (or bilge) half-breadth as a fraction of B/2; never outside the deck edge."""
        a = self.fwd0 - 0.2
        h = 1.0
        if xi > a:
            u = min(1.0, (xi - a) / (1.0 - a))
            e = {"flared": 0.85, "plumb": 0.7, "raked": 0.75, "bulbous": 0.55, "axe": 1.0}[self.bow]
            h = max(0.0, 1 - u * u) ** e
        elif xi < self.aft0:
            u = min(1.0, (self.aft0 - xi) / (1.0 + self.aft0))
            h = max(0.0, 1 - u ** 2.0) ** 0.7 if self.stern == "cruiser" else 1 - 0.18 * u * u
        return min(self.half_deck(xi), 0.94 * h)

    def deadrise(self, xi):
        d = self.p["deadrise_deg"]
        return d + max(0.0, 48.0 - d) * smooth(0.05, 1.0, xi) ** 1.4

    # -- sections ------------------------------------------------------------------------------
    def section_yz(self, xi):
        """Port half-section deck edge -> keel as (y, z), N_ABOVE + 1 + N_BELOW points, with
        vertices exactly at the boot top and the waterline so the paint bands are crisp."""
        zd = self.deck_z(xi)
        zk = min(self.keel_z(xi), zd - 0.1)
        yd = self.B / 2 * max(0.0, self.half_deck(xi))
        yc = self.B / 2 * max(0.0, self.half_wl(xi))
        zc = zk + yc * math.tan(math.radians(self.deadrise(xi)))
        zc = min(zc, zd - 0.15 * (zd - zk))
        side = [(yc + (yd - yc) * h * h, zc + h * (zd - zc)) for h in
                (1 - i / 30 for i in range(31))]
        bottom = [(yc * (1 - t), zc + (zk - zc) * t) for t in (i / 30 for i in range(1, 31))]
        poly = side + bottom
        bil = self.p["bilge"]
        corner_s = None
        cum = _cumlen(poly)
        s_c = cum[30]
        r = bil * 0.9 * min(s_c, cum[-1] - s_c)
        if r > 1e-3:
            A, C, Bp = _at_s(poly, cum, s_c - r), poly[30], _at_s(poly, cum, s_c + r)
            curve = []
            for k in range(1, 12):
                t = k / 12
                curve.append(tuple((1 - t) ** 2 * a + 2 * (1 - t) * t * c + t * t * b
                                   for a, c, b in zip(A, C, Bp)))
            pre = [q for q, s in zip(poly, cum) if s < s_c - r] + [A]
            post = [Bp] + [q for q, s in zip(poly, cum) if s > s_c + r]
            poly = pre + curve + post
            cum = _cumlen(poly)
        else:
            corner_s = s_c
        zb = min(self.p["boot_top_m"], zd - 0.02)
        s_b, s_0, S = _s_at_z(poly, cum, zb), _s_at_z(poly, cum, 0.0), cum[-1]
        ss = [s_b * i / (N_ABOVE - 1) for i in range(N_ABOVE)]
        ss.append(s_0)
        ss += [s_0 + (S - s_0) * i / N_BELOW for i in range(1, N_BELOW + 1)]
        if corner_s is not None:  # keep a hard chine sharp: move the nearest interior point onto it
            k = min((i for i in range(1, len(ss) - 1) if i not in (N_ABOVE - 1, N_ABOVE)),
                    key=lambda i: abs(ss[i] - corner_s))
            if abs(ss[k] - corner_s) < 0.6 * (S / len(ss)) + 1e-9:
                ss[k] = corner_s
        pts = [_at_s(poly, cum, s) for s in ss]
        pts[-1] = (0.0, zk)
        return pts

    def x_of(self, xi, z):
        x = xi * self.L / 2
        zd, zk = self.deck_z(xi), self.keel_z(xi)
        g = max(0.0, min(1.0, (z - zk) / max(1e-6, zd - zk)))
        if xi > self.rake_from and self.rake_m > 0:
            xs = self.rake_from * self.L / 2
            fk = (self.L / 2 - self.rake_m - xs) / (self.L / 2 - xs)
            x = xs + (x - xs) * (fk + (1 - fk) * g ** 1.3)
        if self.tr_m > 0 and xi < -0.85:
            x += self.tr_m * g * smooth(0.85, 1.0, -xi)
        return x + self.x0

    def sections(self, n=84):
        t = [i / n for i in range(n + 1)]
        xis = sorted(set([round(-1 + 2 * (0.5 - 0.5 * math.cos(math.pi * u)), 5) for u in t]
                         + [0.995, -0.995]))
        return [[(self.x_of(xi, z), y, z) for y, z in self.section_yz(xi)] for xi in xis]

    # -- queries -------------------------------------------------------------------------------
    def _bisect(self, f, x):
        lo, hi = -1.0, 1.0
        if x <= f(lo):
            return -1.0
        if x >= f(hi):
            return 1.0
        for _ in range(48):
            mid = (lo + hi) / 2
            if f(mid) < x:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2

    def deck_xi(self, x):
        return self._bisect(lambda xi: self.x_of(xi, self.deck_z(xi)), x)

    def deck_at(self, x):
        return self.deck_z(self.deck_xi(x))

    def deck_half(self, x):
        return self.B / 2 * max(0.0, self.half_deck(self.deck_xi(x)))

    def half_breadth_at(self, x, z):
        if not (self.x_of(-1.0, z) <= x <= self.x_of(1.0, z)):
            return 0.0
        xi = self._bisect(lambda u: self.x_of(u, z), x)
        pts = self.section_yz(xi)
        for (y0, z0), (y1, z1) in zip(pts, pts[1:]):
            if min(z0, z1) - 1e-9 <= z <= max(z0, z1) + 1e-9:
                return y0 if abs(z1 - z0) < 1e-9 else y0 + (y1 - y0) * (z - z0) / (z1 - z0)
        return 0.0

    def stem_x(self, z):
        return self.x_of(1.0, z)

    def stern_x(self, z):
        return self.x_of(-1.0, z)

    def deck_edge(self):
        """Port deck edge, stern to stem, as (x, y, z); the stem point has y = 0."""
        out = []
        for sec in self.sections(84):
            x, y, z = sec[0]
            out.append((x, y, z))
        return out


# ---------------------------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------------------------

def ribbon(mb, rings, mat, closed=False, caps=True):
    """Join rings (equal-length lists of points) into a tube; caps the ends of an open path."""
    m = len(rings[0])
    verts = [v for r in rings for v in r]
    faces = []
    n = len(rings)
    for i in range(n if closed else n - 1):
        a, b = i * m, ((i + 1) % n) * m
        for k in range(m):
            k2 = (k + 1) % m
            faces.append((a + k, a + k2, b + k2, b + k))
    if caps and not closed:
        faces.append(tuple(reversed(range(m))))
        faces.append(tuple((n - 1) * m + k for k in range(m)))
    mb.add(verts, faces, mat)


def plan_normals(path, cx, closed):
    """Outward unit normals (in plan) for a path around a hull or a house centred on x = cx."""
    out = []
    n = len(path)
    for i, P in enumerate(path):
        a = path[(i - 1) % n] if (closed or i > 0) else P
        b = path[(i + 1) % n] if (closed or i < n - 1) else P
        tx, ty = b[0] - a[0], b[1] - a[1]
        ln = math.hypot(tx, ty)
        if ln < 1e-9:
            nx, ny = (1.0, 0.0) if P[0] > cx else (-1.0, 0.0)
        else:
            nx, ny = ty / ln, -tx / ln
        rx, ry = P[0] - cx, P[1]
        if nx * rx + ny * ry < 0:
            nx, ny = -nx, -ny
        out.append((nx, ny))
    return out


def sweep(mb, path, normals, profile, mat, closed=False, scales=None):
    """Sweep a 2D profile [(u outward, v up)] along a 3D path with plan normals."""
    rings = []
    for i, (P, N) in enumerate(zip(path, normals)):
        s = scales[i] if scales else 1.0
        rings.append([(P[0] + N[0] * u * s, P[1] + N[1] * u * s, P[2] + v * s)
                      for u, v in profile])
    ribbon(mb, rings, mat, closed=closed)


# ---------------------------------------------------------------------------------------------
# Superstructure tiers
# ---------------------------------------------------------------------------------------------

class Tier:
    """One tier: plan rings at its base and top, and the window/roof pieces on it."""

    N_FRONT = 10

    def __init__(self, t, z0, form, margin):
        self.t, self.z0 = t, z0
        self.h = t["height_m"]
        self.z1 = z0 + self.h
        self.xa, self.xf = t["x_aft_m"], t["x_fwd_m"]
        self.hw = t["width_m"] / 2
        Lb = self.xf - self.xa
        R = t["round"] * min(2 * self.hw, Lb * 0.8)
        inset = t["windows"][0]["aft_inset_m"] if t["windows"] else min(0.5, 0.15 * Lb)
        inset = min(inset, max(0.05, Lb - R - 0.05))
        port = [(self.xa, self.hw), (self.xa + inset, self.hw)]
        for k in range(self.N_FRONT + 1):
            th = (math.pi / 2) * k / self.N_FRONT
            port.append((self.xf - R + R * math.sin(th), self.hw * math.cos(th)))
        clipped = []
        for x, y in port:
            lim = max(0.03, form.deck_half(x) - margin)
            clipped.append((x, min(y, lim) if y > 0 else 0.0))
        ring = clipped + [(x, -y) for x, y in reversed(clipped[:-1])]
        self.base = [(x, y, z0) for x, y in ring]
        k = (Lb - self.h * math.tan(math.radians(t["rake_deg"]))) / Lb
        sy = (self.hw - t["setback_m"]) / self.hw
        self.top = [(self.xa + (x - self.xa) * k, y * sy, self.z1) for x, y in ring]
        self.n = len(ring)
        self.normals = plan_normals(self.base, (self.xa + self.xf) / 2, closed=True)
        self.x_top_fwd = self.xa + Lb * k

    def at(self, i, z):
        """Ring point i interpolated to height z."""
        u = (z - self.z0) / self.h
        b, t = self.base[i], self.top[i]
        return (b[0] + (t[0] - b[0]) * u, b[1] + (t[1] - b[1]) * u, z)

    def half_width_at(self, x, z):
        """Half-width of the tier's wall at x, height z (0 outside it)."""
        pts = [self.at(i, z) for i in range(self.n // 2 + 1)]
        best = 0.0
        for (x0, y0, _), (x1, y1, _) in zip(pts, pts[1:]):
            if min(x0, x1) - 1e-9 <= x <= max(x0, x1) + 1e-9 and abs(x1 - x0) > 1e-9:
                best = max(best, y0 + (y1 - y0) * (x - x0) / (x1 - x0))
        return best

    def covers(self, x):
        return self.xa <= x <= self.x_top_fwd

    def build(self, me3d, name, parent, skirt_to=None):
        t = self.t
        mat = me3d.material(t["color"], f"superstructure_{name[-1]}")
        mb = me3d.MeshBuilder()
        if t["open"]:
            th = min(0.12, 0.25 * self.hw)
            rings = []
            for i in range(self.n):
                nx, ny = self.normals[i]
                ob, ot = self.base[i], self.top[i]
                rings.append([ob, ot, (ot[0] - nx * th, ot[1] - ny * th, ot[2]),
                              (ob[0] - nx * th, ob[1] - ny * th, ob[2])])
            ribbon(mb, rings, mat, closed=True)
            # a floor plate so the tier can be stood on even where it overhangs the tier below
            mb.add([(x, y, self.z0) for x, y, _ in self.base]
                   + [(x, y, self.z0 + 0.04) for x, y, _ in self.base],
                   [tuple(reversed(range(self.n))), tuple(self.n + i for i in range(self.n))]
                   + [(i, (i + 1) % self.n, self.n + (i + 1) % self.n, self.n + i)
                      for i in range(self.n)], mat)
        else:
            ribbon(mb, [self.base, self.top], mat, closed=False, caps=False)
            mb.add(self.base, [tuple(reversed(range(self.n)))], mat)
            mb.add(self.top, [tuple(range(self.n))], mat)
        if skirt_to is not None and skirt_to < self.z0 - 0.005:
            low = [(x, y, skirt_to) for x, y, _ in self.base]
            mb.add(low + list(self.base), [tuple(reversed(range(self.n))),
                                           tuple(self.n + i for i in range(self.n))]
                   + [(i, (i + 1) % self.n, self.n + (i + 1) % self.n, self.n + i)
                      for i in range(self.n)], mat)
        # roof slab with overhang (a visor over the windscreen, shade over the aft deck)
        ov, ova = t["overhang_m"], t["overhang_aft_m"]
        if (ov > 0 or ova > 0) and not t["open"]:
            slab_t = min(0.15, 0.3 * self.h)
            ring = []
            for i, (x, y, z) in enumerate(self.top):
                nx, ny = self.normals[i]
                px, py = x + nx * ov, y + ny * ov
                if i in (0, self.n - 1):
                    px -= ova
                ring.append((px, py))
            n = self.n
            mb.add([(x, y, self.z1 - slab_t) for x, y in ring] + [(x, y, self.z1) for x, y in ring],
                   [tuple(reversed(range(n))), tuple(n + i for i in range(n))]
                   + [(i, (i + 1) % n, n + (i + 1) % n, n + i) for i in range(n)], mat)
        # window bands: a thin solid strip standing proud of the walls
        for k, w in enumerate(t["windows"]):
            gm = me3d.material(w["color"], f"glass_{name[-1]}_{k}", roughness=0.15, metallic=0.3)
            za, zb = self.z0 + w["bottom_m"], self.z0 + w["bottom_m"] + w["height_m"]
            idx = list(range(self.n)) if w["aft"] else list(range(1, self.n - 1))
            rings = []
            for i in idx:
                nx, ny = self.normals[i]
                a, b = self.at(i, za), self.at(i, zb)
                rings.append([(a[0] + nx * 0.035, a[1] + ny * 0.035, za),
                              (b[0] + nx * 0.035, b[1] + ny * 0.035, zb),
                              (b[0] - nx * 0.03, b[1] - ny * 0.03, zb),
                              (a[0] - nx * 0.03, a[1] - ny * 0.03, za)])
            ribbon(mb, rings, gm, closed=w["aft"])
        return mb.to_object(name, pivot=((self.xa + self.xf) / 2, 0, self.z0), parent=parent)


# ---------------------------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------------------------

def _overhangs(p):
    """Metres of the length overall taken by fittings: (aft of the transom TOP, aft of the hull's
    lowest stern point, forward of the stem, outboard/jet scale)."""
    d, L = p["fender_m"], p["loa_m"]
    s = max(0.6, min(1.5, L / 10))
    aft = [0.0]
    platform = p["swim_platform_m"] if p["stern_form"] == "swim_platform" else 0.0
    if d > 0:
        aft.append(0.6 * d + 0.3 if p["stern_form"] == "open" else 0.9 * d)
    if p["propulsion"] == "outboard":
        aft.append(0.75 * s + 0.2)
    if p["propulsion"] == "waterjet":
        aft.append(0.5 * s)
    return max(aft), platform, (0.9 * d if d > 0 else 0.0), s


def build(params, me3d):
    p = validate(params)
    L, B, T = p["loa_m"], p["beam_m"], p["draught_m"]
    fit_aft, platform, fwd_over, s_eng = _overhangs(p)
    d = p["fender_m"]
    Bh = B - 2 * 0.9 * d
    aft_over = max(fit_aft, platform)
    for _ in range(2):  # fittings hang off the transom top, which a raked transom sets forward
        Lh = L - aft_over - fwd_over
        if Lh < 0.6 * L:
            raise ValueError(f"the overhangs ({aft_over + fwd_over:.1f} m) leave too little hull "
                             f"in loa_m {L}; shorten swim_platform_m or fender_m")
        form = CustomForm(p, Lh, (aft_over - fwd_over) / 2, Bh)
        aft_over = max(platform, fit_aft - form.tr_m, 0.0)
    x_st = form.stern_x(form.deck_z(-1.0))           # transom top
    x_bow = form.stem_x(form.deck_z(1.0))

    # -- hull -----------------------------------------------------------------------------------
    hull_m = me3d.material(p["hull_color"], "hull_topsides")
    boot_m = me3d.material(p["boot_color"], "hull_antifouling")
    btop_m = me3d.material(p["boot_top_color"], "hull_boot_top")
    deck_m = me3d.material(p["deck_color"], "deck")
    zb = p["boot_top_m"]

    def mat_fn(zc):
        return boot_m if zc < 0 else (btop_m if zc < zb else hull_m)

    mb = me3d.MeshBuilder()
    mb.loft(form.sections(84), mat_fn, close_top=True, top_mat=deck_m)
    hull = mb.to_object("Hull__Main", pivot=(0, 0, 0))

    # the deck as its own part (so it can be recoloured or hidden on its own): a thin plate whose
    # top is DECK_T above the hull's sheer line
    dm = me3d.MeshBuilder()
    rings = []
    for sec in form.sections(84):
        x, y, z = sec[0]
        if y < 0.01:
            continue
        y -= 0.005
        rings.append([(x, y, z + DECK_T), (x, -y, z + DECK_T), (x, -y, z - DECK_T),
                      (x, y, z - DECK_T)])
    ribbon(dm, rings, deck_m)
    deck = dm.to_object("Deck__Main", pivot=(0, 0, form.deck_z(0.0)), parent=hull)

    if p["bow_form"] == "bulbous":
        bl = 0.06 * Lh
        zc = -0.55 * T
        cx = min(form.stem_x(zc) + 0.2 * bl, x_bow - bl / 2 - 0.02)
        me3d.MeshBuilder().ellipsoid((cx, 0, zc), (bl / 2, min(0.1 * Bh, 0.4 * T), 0.22 * T),
                                     boot_m, 16, 10).to_object("Hull__Bulb", pivot=(cx, 0, zc),
                                                               parent=hull)

    if p["stern_form"] == "swim_platform":
        zp = p["swim_platform_height_m"]
        x0p = -L / 2
        x1p = form.stern_x(zp) + 0.15
        yh = 0.88 * Bh / 2 * form.half_deck(-1.0)
        me3d.MeshBuilder().box_minmax((x0p, -yh, zp - 0.22), (x1p, yh, zp), deck_m).to_object(
            "Hull__Swim_Platform", pivot=(x1p, 0, zp), parent=hull)

    # deck-edge path: starboard stern -> stem -> port stern
    edge = [q for q in form.deck_edge() if q[1] > 1e-3]
    stem = (x_bow, 0.0, form.deck_z(1.0))
    path = [(x, -y, z) for x, y, z in edge] + [stem] + list(reversed(edge))
    closed_stern = p["stern_form"] != "open"
    x_mid = form.x0
    if closed_stern and edge and edge[0][1] > 0.05:
        x_t, y_t, z_t = edge[0]
        across = [(x_t, y_t * f, z_t) for f in (0.5, 0.0, -0.5)]
        path = path + across
    loop = closed_stern

    t_bw = max(0.05, min(0.15, 0.004 * L + 0.03))
    if p["bulwark_m"] > 0:
        bw_m = me3d.material(p["bulwark_color"] or p["hull_color"], "bulwark")
        bm = me3d.MeshBuilder()
        prof = [(0.0, -0.05), (0.0, p["bulwark_m"]), (-t_bw, p["bulwark_m"]), (-t_bw, -0.05)]
        sweep(bm, path, plan_normals(path, x_mid, loop), prof, bw_m, closed=loop)
        bm.to_object("Hull__Bulwark", pivot=(0, 0, form.deck_z(0)), parent=hull)

    if p["rail_m"] > 0:
        rl_m = me3d.material(p["rail_color"], "rail", roughness=0.3, metallic=0.6)
        rm = me3d.MeshBuilder()
        z_off = p["bulwark_m"]
        inset = t_bw / 2 if p["bulwark_m"] > 0 else 0.06
        r = max(0.02, min(0.04, 0.0015 * L + 0.015))
        normals = plan_normals(path, x_mid, loop)
        rail_path = [(x - nx * inset, y - ny * inset, z + z_off + p["rail_m"])
                     for (x, y, z), (nx, ny) in zip(path, normals)]
        prof = [(r * math.cos(a), r * math.sin(a)) for a in
                (2 * math.pi * k / 6 for k in range(6))]
        sweep(rm, rail_path, normals, prof, rl_m, closed=loop)
        acc = 9e9
        for (x, y, z), (nx, ny) in zip(path, normals):
            if acc >= 1.6:
                xb, yb = x - nx * inset, y - ny * inset
                rm.cylinder((xb, yb, z + z_off - 0.02), (xb, yb, z + z_off + p["rail_m"]), r * 0.8,
                            rl_m, 6)
                acc = 0.0
                last = (x, y)
            else:
                acc = math.hypot(x - last[0], y - last[1])
        rm.to_object("Hull__Rail", pivot=(0, 0, form.deck_z(0)), parent=hull)

    if d > 0:
        fd_m = me3d.material(p["fender_color"], "fender", roughness=0.85)
        fm = me3d.MeshBuilder()
        fpath = list(path)
        normals = plan_normals(fpath, x_mid, loop)
        scales = [1.0] * len(fpath)
        if not loop:   # a RHIB tube runs past the transom and ends in cones
            ext = 0.6 * d + 0.3
            s0, s1 = fpath[0], fpath[-1]
            fpath = [(s0[0] - ext, s0[1], s0[2]), (s0[0] - 0.55 * ext, s0[1], s0[2])] + fpath + \
                    [(s1[0] - 0.55 * ext, s1[1], s1[2]), (s1[0] - ext, s1[1], s1[2])]
            normals = [normals[0], normals[0]] + normals + [normals[-1], normals[-1]]
            scales = [0.55, 0.95] + scales + [0.95, 0.55]
        rr = d / 2
        cu, cv = 0.4 * d, -0.15 * d
        prof = [(cu + rr * math.cos(a), cv + rr * math.sin(a))
                for a in (2 * math.pi * k / 12 for k in range(12))]
        # cones shrink about the tube's centre, not the deck edge
        rings = []
        for P, N, s in zip(fpath, normals, scales):
            rings.append([(P[0] + N[0] * (cu + (u - cu) * s), P[1] + N[1] * (cu + (u - cu) * s),
                           P[2] + cv + (v - cv) * s) for u, v in prof])
        ribbon(fm, rings, fd_m, closed=loop)
        fm.to_object("Hull__Fender", pivot=(0, 0, form.deck_z(0)), parent=hull)

    if p["hull_windows"]:
        hm = me3d.MeshBuilder()
        for k, w in enumerate(p["hull_windows"]):
            gm = me3d.material(w["color"], f"hull_glass_{k}", roughness=0.15, metallic=0.3)
            xa, xf, zc, h = w["x_aft_m"], w["x_fwd_m"], w["z_m"], w["height_m"]
            n = max(4, int((xf - xa) / 0.4))
            taper = min(0.6, 0.3 * (xf - xa))
            for sgn in (1, -1):
                rings = []
                for i in range(n + 1):
                    x = xa + (xf - xa) * i / n
                    e = min(1.0, (x - xa) / taper, (xf - x) / taper) if taper > 0 else 1.0
                    hh = max(0.04, h * math.sqrt(max(0.0, e))) / 2
                    lo, hi = zc - hh, zc + hh
                    ylo, yhi = form.half_breadth_at(x, lo), form.half_breadth_at(x, hi)
                    if ylo <= 0.05 or yhi <= 0.05:
                        continue
                    rings.append([(x, sgn * (ylo + 0.025), lo), (x, sgn * (yhi + 0.025), hi),
                                  (x, sgn * (yhi - 0.03), hi), (x, sgn * (ylo - 0.03), lo)])
                if len(rings) >= 2:
                    ribbon(hm, rings, gm)
        if hm.faces:
            hm.to_object("Hull__Windows", pivot=(0, 0, 0), parent=hull)

    # -- superstructure -------------------------------------------------------------------------
    margin = (t_bw if p["bulwark_m"] > 0 else 0.0) + 0.03
    tiers, objs = [], []
    parent = hull
    for i, t in enumerate(p["tiers"], start=1):
        if i == 1:
            xs = [t["x_aft_m"] + (t["x_fwd_m"] - t["x_aft_m"]) * k / 12 for k in range(13)]
            decks = [form.deck_at(x) for x in xs]
            z0, skirt = max(decks) + DECK_T, min(decks) - 0.05
        else:
            z0, skirt = tiers[-1].z1, None
        tier = Tier(t, z0, form, margin)
        ob = tier.build(me3d, f"Superstructure__Tier_{i}", parent, skirt_to=skirt)
        tiers.append(tier)
        objs.append(ob)
        parent = ob

    def support(x):
        """The highest tier whose roof spans x, as (z, object, tier) - else the deck."""
        for tier, ob in reversed(list(zip(tiers, objs))):
            if tier.covers(x) and not tier.t["open"]:
                return tier.z1, ob, tier
        return form.deck_at(x) + DECK_T, deck, None

    # -- helm, lights ---------------------------------------------------------------------------
    k = p["helm_tier"]
    if k < 0:
        closed = [i for i, t in enumerate(tiers, start=1) if not t.t["open"] and t.h >= 1.0]
        k = closed[-1] if closed else 0
    if k >= 1:
        ht, hob = tiers[k - 1], objs[k - 1]
        x_front_eye = ht.xa + (ht.xf - ht.xa) * (1 - 1.6 * math.tan(math.radians(ht.t["rake_deg"]))
                                                 / (ht.xf - ht.xa))
        x_h = p["helm_x_m"] if p["helm_x_m"] is not None else max(ht.xa + 0.3,
                                                                  x_front_eye - min(1.2, 0.3 * (ht.xf - ht.xa)))
        z_h = ht.z0
        helm_parent = hob
        z_l = ht.z0 + min(1.3, 0.6 * ht.h)
        x_l = min(x_h + 0.4, ht.xa + 0.9 * (ht.xf - ht.xa))
        y_l = ht.half_width_at(x_l, z_l) + 0.05
        light_follow = hob
    else:
        x_h = p["helm_x_m"] if p["helm_x_m"] is not None else (
            tiers[0].xa - 0.6 if tiers else x_st + 0.3 * (x_bow - x_st))
        z_h = form.deck_at(x_h) + DECK_T
        helm_parent = deck
        if tiers:
            t1 = tiers[0]
            z_l = t1.z0 + 0.6 * t1.h
            x_l = t1.xa + 0.8 * (t1.xf - t1.xa)
            y_l = t1.half_width_at(x_l, z_l) + 0.05
            light_follow = objs[0]
        else:
            x_l = x_st + 0.7 * (x_bow - x_st)
            z_l = form.deck_at(x_l) + 0.6
            y_l = form.deck_half(x_l)
            light_follow = hull
    me3d.add_station("helm", (x_h, 0.0, z_h), helm_parent, "stand", ["steer", "con", "lookout"],
                     "Helm")
    me3d.add_socket("socket_bridge_eye", (x_h + 0.3, 0.0, z_h + 1.7), follow=helm_parent)
    me3d.add_socket("socket_light_port", (x_l, y_l, z_l), follow=light_follow)
    me3d.add_socket("socket_light_stbd", (x_l, -y_l, z_l), follow=light_follow)
    me3d.add_socket("socket_bow", (form.stem_x(0.0), 0, 0.0), follow=hull)
    zs = form.deck_z(-1.0)
    me3d.add_socket("socket_light_stern", (x_st + 0.1, 0, zs + p["bulwark_m"] + 0.4), follow=hull)

    # deck stations fore and aft of the first tier, when there is clear deck there
    x_house_f = tiers[0].xf if tiers else (x_h + 0.6)
    x_house_a = tiers[0].xa if tiers else (x_h - 0.6)
    if k == 0:
        x_house_a = min(x_house_a, x_h - 0.6)
    x_fs = (x_house_f + (x_st + 0.85 * (x_bow - x_st))) / 2
    if x_fs - x_house_f > 0.6 and form.deck_half(x_fs) > 0.4:
        me3d.add_station("deck_fwd", (x_fs, 0.0, form.deck_at(x_fs) + DECK_T), deck, "stand",
                         ["mooring", "anchoring", "lookout"], "Foredeck")
    x_as = (x_house_a + x_st + 0.4) / 2
    if x_house_a - x_as > 0.6:
        me3d.add_station("deck_aft", (x_as, 0.0, form.deck_at(x_as) + DECK_T), deck, "stand",
                         ["mooring", "lookout"], "Aft deck", yaw_deg=180.0)

    # -- mast, radar arch, funnel ---------------------------------------------------------------
    top_tier = next((t for t in reversed(tiers) if not t.t["open"]), None)
    radar_on = None
    masthead = None
    if p["radar_arch"]:
        ref = tiers[-1] if tiers else None
        x_a = p["arch_x_m"] if p["arch_x_m"] is not None else (
            (ref.xa + ref.x_top_fwd) / 2 if ref else x_st + 0.35 * (x_bow - x_st))
        zbase, sob, stier = support(x_a)
        lim = stier.half_width_at(x_a, zbase) - 0.05 if stier else form.deck_half(x_a) - 0.1
        hw = max(0.2, min(p["arch_width_m"] / 2, lim))
        h = p["arch_height_m"]
        back = h * math.tan(math.radians(p["arch_rake_deg"]))
        chord = max(0.25, min(1.4, 0.4 * h))
        thick = max(0.08, min(0.25, 0.05 * h + 0.05))
        am = me3d.MeshBuilder()
        a_m = me3d.material(p["arch_color"], "arch")
        for sgn in (1, -1):
            y0, y1 = sgn * hw, sgn * (hw - 0.15 * h * 0.3)
            poly = [(x_a + chord / 2, zbase - 0.05), (x_a - chord / 2, zbase - 0.05),
                    (x_a - back - chord * 0.35, zbase + h), (x_a - back + chord * 0.35, zbase + h)]
            am.prism_xz(poly, min(y0, y0 - sgn * thick), max(y0, y0 - sgn * thick), a_m)
        am.box_minmax((x_a - back - chord * 0.4, -hw, zbase + h - thick * 1.2),
                      (x_a - back + chord * 0.4, hw, zbase + h), a_m)
        arch = am.to_object("Arch__Radar", pivot=(x_a, 0, zbase), parent=sob)
        radar_on = (arch, x_a - back, zbase + h)
        masthead = (x_a - back, zbase + h + 0.15, arch)

    if p["mast"]:
        x_m = p["mast_x_m"] if p["mast_x_m"] is not None else (
            top_tier.xa + 0.4 * (top_tier.x_top_fwd - top_tier.xa) if top_tier
            else x_st + 0.55 * (x_bow - x_st))
        zbase, sob, _ = support(x_m)
        h = p["mast_height_m"]
        r = max(0.05, min(0.4, 0.004 * L + 0.04))
        mm = me3d.MeshBuilder()
        m_m = me3d.material(p["mast_color"], "mast")
        mm.cylinder((x_m, 0, zbase - 0.05), (x_m, 0, zbase + h), r, m_m, 10, radius1=r * 0.55)
        ya = max(0.4, min(0.45 * B, 0.12 * h + 0.6))
        mm.box((x_m, 0, zbase + 0.78 * h), (r * 1.2, 2 * ya, r * 1.0), m_m)
        plat_z = zbase + 0.55 * h
        mm.box((x_m + r + 0.25, 0, plat_z), (0.6 + 2 * r, 0.6 + 2 * r, 0.06), m_m)
        mast = mm.to_object("Mast__Main", pivot=(x_m, 0, zbase), parent=sob)
        masthead = (x_m, zbase + h + 0.12, mast)
        if radar_on is None:
            radar_on = (mast, x_m + r + 0.25, plat_z + 0.03)

    if p["radar"]:
        if radar_on is None:
            x_r = top_tier.xa + 0.5 * (top_tier.x_top_fwd - top_tier.xa) if top_tier else 0.0
            zr, rob, _ = support(x_r)
            radar_on = (rob, x_r, zr)
        rob, x_r, zr = radar_on
        span = max(0.8, min(4.5, 0.05 * L + 0.4))
        rm = me3d.MeshBuilder()
        rm.cylinder((x_r, 0, zr), (x_r, 0, zr + 0.25), 0.12, STEEL, 10)
        rm.box((x_r, 0, zr + 0.33), (0.25, span, 0.16), (0.92, 0.92, 0.9))
        scanner = rm.to_object("Radar__Scanner", pivot=(x_r, 0, zr), parent=rob)
        me3d.set_joint(scanner, "z", -180, 180, "P0")
        me3d.add_socket("socket_radar", (x_r, 0, zr + 0.35), follow=scanner)
    else:
        x_r = top_tier.xa + 0.5 * (top_tier.x_top_fwd - top_tier.xa) if top_tier else 0.0
        zr, rob, _ = support(x_r)
        me3d.add_socket("socket_radar", (x_r, 0, zr + 0.5), follow=rob)

    if masthead is None:
        if top_tier is not None:
            xm = top_tier.xa + 0.7 * (top_tier.x_top_fwd - top_tier.xa)
            masthead = (xm, top_tier.z1 + 0.8, objs[tiers.index(top_tier)])
        else:
            xm = x_st + 0.7 * (x_bow - x_st)
            masthead = (xm, form.deck_at(xm) + 2.0, hull)
    me3d.add_socket("socket_light_masthead", (masthead[0], 0, masthead[1]), follow=masthead[2])
    if L >= 50:
        xa_ = x_st + 0.15 * (x_bow - x_st)
        za_, aob, _ = support(xa_)
        me3d.add_socket("socket_light_masthead_aft", (xa_, 0, za_ + 3.0), follow=aob)

    if p["funnel"]:
        x_f = p["funnel_x_m"] if p["funnel_x_m"] is not None else (
            tiers[0].xa + 0.25 * (tiers[0].xf - tiers[0].xa) if tiers else x_st + 0.35 * (x_bow - x_st))
        zbase, sob, _ = support(x_f)
        h, fl, fw = p["funnel_height_m"], p["funnel_length_m"], p["funnel_width_m"]
        rake = h * math.tan(math.radians(p["funnel_rake_deg"]))
        f_m = me3d.material(p["funnel_color"], "funnel")
        ft_m = me3d.material(p["funnel_top_color"], "funnel_top")
        ys = [0.0] if p["funnel_count"] == 1 else [0.55 * fw + 0.1, -(0.55 * fw + 0.1)]
        names = ["Funnel__Main"] if p["funnel_count"] == 1 else ["Funnel__Port", "Funnel__Stbd"]
        for yc, nm in zip(ys, names):
            fm = me3d.MeshBuilder()

            def band(za, zb_, mat):
                ra, rb = rake * (za - zbase) / h, rake * (zb_ - zbase) / h
                poly = [(x_f + fl / 2 - ra, za), (x_f - fl / 2 - ra, za),
                        (x_f - fl / 2 - rb, zb_), (x_f + fl / 2 - rb, zb_)]
                fm.prism_xz(poly, yc - fw / 2, yc + fw / 2, mat)
            zt = zbase + h
            cut = zt - min(0.25 * h, 0.12 * h + 0.3)
            band(zbase - 0.05, cut, f_m)
            band(cut, zt, ft_m)
            fm.to_object(nm, pivot=(x_f, yc, zbase), parent=sob)

    # -- propulsion -----------------------------------------------------------------------------
    n = p["engines"]
    ys = {1: [0.0], 2: [0.25, -0.25], 3: [0.3, 0.0, -0.3], 4: [0.36, 0.13, -0.13, -0.36]}[n]
    ys = [y * Bh for y in ys]
    spacing = (Bh * 0.5 / n) if n > 1 else Bh * 0.4
    prop_socket = None
    kind = p["propulsion"]
    suffix = (lambda i: "" if n == 1 else f"_{i}")
    x_hb = form.stern_x(-0.6 * T)   # hull's aft end low down
    if kind == "shaft":
        bronze = me3d.material((0.62, 0.45, 0.2), "bronze", roughness=0.35, metallic=0.9)
        r_p = min(0.3 * T, 0.42 * spacing)
        z_hub = -T + r_p + 0.06 * T
        x_p = x_hb + 0.06 * Lh + 1.2 * r_p
        c = max(0.3, 1.1 * r_p)
        for i, y in enumerate(ys, start=1):
            pm = me3d.MeshBuilder()
            pm.cylinder((x_p - 0.1, y, z_hub), (x_p + 0.35 * Lh * 0.25, y, z_hub + 0.3 * T),
                        0.12 * r_p + 0.02, bronze, 10)
            pm.cylinder((x_p - 0.25 * r_p, y, z_hub), (x_p + 0.3 * r_p, y, z_hub), 0.18 * r_p, bronze,
                        12)
            for kb in range(4):
                a = 2 * math.pi * kb / 4
                rot = Matrix.Rotation(a, 3, "X") @ Matrix.Rotation(math.radians(25), 3, "Z")
                ctr = (x_p, y - math.sin(a) * r_p * 0.58, z_hub + math.cos(a) * r_p * 0.58)
                pm.box_oriented(ctr, (0.12 * r_p, 0.42 * r_p, 0.84 * r_p), rot, bronze)
            prop = pm.to_object(f"Hull__Propeller{suffix(i)}", pivot=(x_p, y, z_hub), parent=hull)
            x_r = x_p - r_p - 0.3 * c
            z_top = -0.1 * T
            z_bot = max(-T, z_hub - 0.95 * r_p)
            rd = me3d.MeshBuilder()
            rd.prism_xz([(x_r + 0.3 * c, z_bot), (x_r + 0.3 * c, z_top), (x_r - 0.7 * c, z_top),
                         (x_r - 0.7 * c, z_bot)], y - 0.04 - 0.02 * c, y + 0.04 + 0.02 * c, boot_m)
            rudder = rd.to_object(f"Hull__Rudder{suffix(i)}", pivot=(x_r, y, z_top), parent=hull)
            me3d.set_joint(rudder, "z", -35, 35, "P1")
            if prop_socket is None:
                prop_socket = ((x_p, y, z_hub), prop)
    elif kind == "azimuth":
        r_p = min(0.24 * T, 0.38 * spacing)
        z_hub = -T + 1.15 * r_p
        x_z = x_hb + 0.1 * Lh + r_p
        z_top = -0.25 * T
        az_m = me3d.material((0.3, 0.3, 0.32), "thruster", roughness=0.5, metallic=0.6)
        noz_m = me3d.material((0.3, 0.3, 0.32), "thruster_nozzle", roughness=0.5, metallic=0.6,
                              double_sided=True)
        for i, y in enumerate(ys, start=1):
            zm = me3d.MeshBuilder()
            zm.cylinder((x_z, y, z_top), (x_z, y, z_hub), 0.22 * r_p + 0.05, az_m, 12)
            zm.cylinder((x_z + 0.6 * r_p, y, z_hub), (x_z - 0.4 * r_p, y, z_hub), 0.3 * r_p, az_m, 12)
            zm.cylinder((x_z - 0.5 * r_p, y, z_hub), (x_z - 1.4 * r_p, y, z_hub), 1.05 * r_p,
                        noz_m, 16, caps=False)
            unit = zm.to_object(f"Hull__Azimuth{suffix(i)}", pivot=(x_z, y, z_top), parent=hull)
            me3d.set_joint(unit, "z", -180, 180, "P0")
            if prop_socket is None:
                prop_socket = ((x_z - 0.95 * r_p, y, z_hub), unit)
    elif kind in ("outboard", "waterjet"):
        s = s_eng
        z_tr = form.deck_z(-1.0)
        ob_m = me3d.material((0.1, 0.1, 0.11), "outboard", roughness=0.4)
        alu = me3d.material((0.6, 0.62, 0.64), "drive_leg", roughness=0.4, metallic=0.6)
        span = min(0.5 * Bh, 0.75 * s * n)
        ys2 = [0.0] if n == 1 else [span / 2 - span * i / (n - 1) for i in range(n)]
        for i, y in enumerate(ys2, start=1):
            if kind == "outboard":
                x_s = form.stern_x(z_tr - 0.1) - 0.15 * s
                z_p = z_tr - 0.08
                om = me3d.MeshBuilder()
                om.box((x_s + 0.06, y, z_p - 0.05), (0.18 * s, 0.3 * s, 0.25 * s), alu)
                om.ellipsoid((x_s - 0.25 * s, y, z_p + 0.35 * s), (0.36 * s, 0.24 * s, 0.38 * s),
                             ob_m, 14, 8)
                z_g = -T + 0.22 * s
                om.box((x_s - 0.2 * s, y, (z_p + z_g) / 2), (0.2 * s, 0.1 * s, z_p - z_g), ob_m)
                om.box((x_s - 0.25 * s, y, -0.08), (0.45 * s, 0.32 * s, 0.025), ob_m)
                om.cylinder((x_s - 0.05 * s, y, z_g), (x_s - 0.5 * s, y, z_g), 0.07 * s, ob_m, 10,
                            radius1=0.05 * s)
                om.box((x_s - 0.25 * s, y, (z_g + -T) / 2), (0.2 * s, 0.03, z_g + T), ob_m)
                xp = x_s - 0.52 * s
                for kb in range(3):
                    a = 2 * math.pi * kb / 3
                    rot = Matrix.Rotation(a, 3, "X") @ Matrix.Rotation(math.radians(25), 3, "Z")
                    om.box_oriented((xp, y - math.sin(a) * 0.1 * s, z_g + math.cos(a) * 0.1 * s),
                                    (0.025, 0.09 * s, 0.14 * s), rot, alu)
                unit = om.to_object(f"Transom__Outboard{suffix(i)}", pivot=(x_s, y, z_p),
                                    parent=hull)
                me3d.set_joint(unit, "z", -35, 35, "P0")
                sock = (xp, y, z_g)
            else:
                x_s = form.stern_x(-0.25 * T)
                z_j = -0.2 * T
                jm = me3d.MeshBuilder()
                jm.box((x_s - 0.2 * s, y, z_j), (0.4 * s, 0.32 * s, 0.32 * s), alu)
                jm.cylinder((x_s - 0.35 * s, y, z_j), (x_s - 0.5 * s, y, z_j), 0.12 * s, alu, 12)
                unit = jm.to_object(f"Transom__Waterjet{suffix(i)}", pivot=(x_s, y, z_j),
                                    parent=hull)
                me3d.set_joint(unit, "z", -30, 30, "P1")
                sock = (x_s - 0.5 * s, y, z_j)
            if prop_socket is None or abs(y) < abs(prop_socket[0][1]):
                prop_socket = (sock, unit)
    if prop_socket is None:
        prop_socket = ((x_hb + 0.08 * Lh, 0, -0.5 * T), hull)
    me3d.add_socket("socket_prop", prop_socket[0], follow=prop_socket[1])
    return hull
