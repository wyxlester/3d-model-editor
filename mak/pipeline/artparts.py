"""Articulated parts: DIS codes, the FBX node-name map, motion types and frame conversion.

Sources
- DIS articulation codes: manual Table 58 (AddContentToMAK.md, 21.7) plus codes seen in the
  stock .entity files (marked "install").
- FBX parts are recognised ONLY by node name, via appData/importConfig/FBX_NodeNameMap.csv
  (manual 21.1.2). An FBX node that isn't in that map is static geometry to VR-Forces.
- .entity articulated part descriptors (DtSimArtPartDescriptor): field names and DIS parameter
  metric pairs taken from stock .entity files in the 5.1.1 install.
- Model frame: X right, Y forward, Z up (manual 21.2). .entity bodyPosition: forward/right/down (DIS).
"""
from __future__ import annotations

import csv
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------- Table 58
# code -> (name, description, default motion)
# motion: azimuth | elevation | rotation | extension | translate_x|y|z | visual
#   "visual" = animated by the renderer from entity state (wheels, rotors); no .entity descriptor.
CODES: dict[int, tuple[str, str, str]] = {}


def _add(code, name, desc, motion):
    CODES.setdefault(code, (name, desc, motion))


for c, n, d, m in [
    (1025, "rudder", "Rudder", "azimuth"),
    (1056, "left_flap", "Left flap", "elevation"),
    (1088, "right_flap", "Right flap", "elevation"),
    (1120, "left_aileron", "Left aileron", "elevation"),
    (1152, "right_aileron", "Right aileron", "elevation"),
    (1184, "helicopter_-_main_rotor", "Main rotor", "visual"),
    (1216, "helicopter_-_tail_rotor", "Tail rotor", "visual"),
    (1248, "other_aircraft_control_surfaces", "Other aircraft control surfaces", "elevation"),
    (2048, "periscope", "Periscope", "translate_z"),
    (2080, "generic_antenna", "Antenna", "translate_z"),
    (2112, "snorkel", "Snorkel", "translate_z"),
    (2144, "other_extendible_parts", "Other extendible parts", "translate_z"),
    (3072, "landing_gear", "Landing gear", "elevation"),
    (3104, "tail_hook", "Tail hook", "elevation"),
    (3136, "speed_brake", "Speed brake", "elevation"),
    (3168, "left_door_of_primary_weapon_bay", "Left weapon bay door", "elevation"),
    (3200, "right_door_of_primary_weapon_bay", "Right weapon bay door", "elevation"),
    (3232, "tank_or_apc_hatch", "Hatch on a tank or APC", "elevation"),
    (3264, "wingsweep", "Wing sweep", "azimuth"),
    (3296, "bridge_launcher", "Bridge launcher", "elevation"),
    (3328, "bridge_section_1", "Bridge section 1", "elevation"),
    (3360, "bridge_section_2", "Bridge section 2", "elevation"),
    (3392, "bridge_section_3", "Bridge section 3", "elevation"),
    (3424, "primary_blade_1", "Primary blade 1", "elevation"),
    (3456, "primary_blade_2", "Primary blade 2", "elevation"),
    (3488, "primary_boom", "Primary boom", "elevation"),
    (3520, "launcher_arm_primary", "Primary launcher arm", "elevation"),
    (3552, "other_fixed_position_parts", "Other fixed position parts", "rotation"),
]:
    _add(c, n, d, m)

for base, stem, desc, motion in [
    (4096, "primary_turret_number", "Primary turret", "azimuth"),
    (4416, "primary_gun_number", "Primary gun", "elevation"),
    (4736, "primary_launcher", "Primary launcher", "elevation"),
    (5056, "primary_defense_systems", "Primary defense systems", "azimuth"),
    (5376, "primary_radar", "Primary radar", "azimuth"),
    (5696, "secondary_turret_number", "Secondary turret", "azimuth"),
    (6016, "secondary_gun_number", "Secondary gun", "elevation"),
    (6336, "secondary_launcher", "Secondary launcher", "elevation"),
    (6656, "secondary_defense_systems", "Secondary defense systems", "azimuth"),
    (6976, "secondary_radar", "Secondary radar", "azimuth"),
]:
    for i in range(10):
        _add(base + 32 * i, f"{stem}_{i + 1}", f"{desc} {i + 1}", motion)

for c, n, d, m in [
    (7296, "deck_elevator_1", "Deck elevator 1", "translate_z"),
    (7328, "deck_elevator_2", "Deck elevator 2", "translate_z"),
    (7360, "catapult_1", "Catapult 1", "visual"),
    (7392, "catapult_2", "Catapult 2", "visual"),
    (7424, "jet_blast_deflector_1", "Jet blast deflector 1", "elevation"),
    (7456, "jet_blast_deflector_2", "Jet blast deflector 2", "elevation"),
    (7488, "arrestor_wires_1", "Arrestor wires 1", "visual"),
    (7520, "arrestor_wires_2", "Arrestor wires 2", "visual"),
    (7552, "arrestor_wires_3", "Arrestor wires 3", "visual"),
    (7584, "wing_or_rotor_fold", "Wing or rotor fold", "elevation"),
    (7616, "fuselage_fold", "Fuselage fold", "elevation"),
    (16064, "towed_array_decoy", "Towed array decoy (MAK extension)", "extension"),
    (32704, "wind_direction", "Rotates into the wind", "visual"),
    (32736, "wind_blades", "Rotates with wind speed", "visual"),
    # Not in Table 58 but used by stock .entity files in the 5.1.1 install:
    (7840, "car_door", "Car door (install)", "elevation"),
    (8192, "boom_extension", "Refuelling boom extension (install)", "extension"),
]:
    _add(c, n, d, m)

_ORD = ["1st", "2nd", "3rd"] + [f"{i}th" for i in range(4, 11)]
for i in range(10):
    _add(9120 + 64 * i, f"{_ORD[i]}_left_wheel", f"Left wheel {i + 1}", "visual")
    _add(9152 + 64 * i, f"{_ORD[i]}_right_wheel", f"Right wheel {i + 1}", "visual")

WEAPON_FAMILIES = (4416, 4736, 6016, 6336)          # gun / launcher bases: muzzle end-point matters


def is_weapon(code: int) -> bool:
    return any(base <= code < base + 320 for base in WEAPON_FAMILIES)


# ---------------------------------------------------------------- motions
# DIS articulated-parameter metric pairs (value, rate), as used in stock art-part-param-list.
MOTION_PARAMS = {
    "extension": (3, 4), "translate_x": (5, 6), "translate_y": (7, 8), "translate_z": (9, 10),
    "azimuth": (11, 12), "elevation": (13, 14), "rotation": (15, 16),
}
MOTIONS = set(MOTION_PARAMS) | {"visual"}
ROTATIONAL = {"azimuth", "elevation", "rotation"}

# defaults in manifest units: degrees (rotational) or metres (translational); rate per second
DEFAULT_LIMITS = {
    "azimuth": (-180.0, 180.0, 45.0),
    "elevation": (-10.0, 60.0, 45.0),
    "rotation": (0.0, 90.0, 45.0),
    "extension": (0.0, 1.0, 1.0),
    "translate_x": (0.0, 1.0, 1.0),
    "translate_y": (0.0, 1.0, 1.0),
    "translate_z": (0.0, 1.0, 1.0),
}


def default_motion(code: int) -> str:
    return CODES.get(code, ("", "", "azimuth"))[2]


# ---------------------------------------------------------------- frames
def model_to_body(p) -> tuple[float, float, float]:
    """Model (x right, y forward, z up) -> DIS body (forward, right, down)."""
    x, y, z = p
    return (y, x, -z)


# ---------------------------------------------------------------- FBX node-name map
@dataclass
class NodeNameMap:
    parts: dict[str, int] = field(default_factory=dict)     # node name -> art part code
    lods: set[str] = field(default_factory=set)
    damage: set[str] = field(default_factory=set)
    source: str = ""

    def canonical_name(self, code: int) -> str | None:
        """First node name in the map for this code (that is how MAK's own FBX models are named)."""
        return next((n for n, c in self.parts.items() if c == code), None)


ART_RE = re.compile(r"@dis\s+articulated_part\s+(\d+)")


def load_node_map(path: str | Path | None) -> NodeNameMap:
    m = NodeNameMap(source=str(path or ""))
    if not path or not Path(path).exists():
        return m
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            node, comment, kind = (row.get("Node") or "").strip(), (row.get("Comment") or ""), (row.get("Type") or "")
            if not node:
                continue
            hit = ART_RE.search(comment)
            if hit:
                m.parts.setdefault(node, int(hit.group(1)))
            elif "@dis lod" in comment:
                m.lods.add(node)
            elif kind.strip().lower() == "damagestate":
                m.damage.add(node)
    return m


# ---------------------------------------------------------------- manifest parts
@dataclass
class Part:
    node: str            # node name in the glb
    fbx_name: str        # node name written to the FBX (canonical map name when one exists)
    code: int
    parent: str | None   # parent part's glb node name
    motion: str
    min: float
    max: float
    max_rate: float
    in_map: bool         # fbx_name is recognised by FBX_NodeNameMap.csv as this code


def resolve_parts(manifest: dict, node_map: NodeNameMap) -> list[Part]:
    """Turn manifest articulated_parts into Parts with FBX names and limits filled in."""
    out: list[Part] = []
    for p in manifest.get("articulated_parts", []):
        code = int(p["dis_part"])
        motion = p.get("motion") or default_motion(code)
        lo, hi, rate = DEFAULT_LIMITS.get(motion, (0.0, 0.0, 0.0))
        lo, hi, rate = p.get("min", lo), p.get("max", hi), p.get("max_rate", rate)
        if node_map.parts.get(p["node"]) == code:
            fbx_name, in_map = p["node"], True
        else:
            canon = node_map.canonical_name(code)
            fbx_name, in_map = (canon, True) if canon else (p["node"], False)
        out.append(Part(p["node"], fbx_name, code, p.get("parent"), motion, float(lo), float(hi), float(rate), in_map))
    return out


def rename_map(parts: list[Part]) -> dict[str, str]:
    return {p.node: p.fbx_name for p in parts if p.node != p.fbx_name}


def map_additions(parts: list[Part]) -> list[list[str]]:
    """Rows to append to FBX_NodeNameMap.csv for parts whose code has no name in the map."""
    return [[p.fbx_name, f"@dis articulated_part {p.code}", "comment", CODES.get(p.code, ("", f"Part {p.code}", ""))[1]]
            for p in parts if not p.in_map]


def to_radians(motion: str, v: float) -> float:
    return math.radians(v) if motion in ROTATIONAL else v
