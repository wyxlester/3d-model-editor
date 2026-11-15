"""me3d.package/1 (contract §8) -> a model folder the pipeline runs on (contract §9b).

Everything the pipeline used to infer is fixed by the editor's contract: bow +X (§1), origin at the
waterline amidships, part names `Group__Part` (§2). What VR-Forces needs on top comes from `ship/mak.json`
(the project's MAK settings): DIS type, donor entity, a DIS part code per moving part, and optionally `kind`.
The kind (pipeline/profiles.py; `surface_ship` unless the settings say otherwise) decides the platform file,
the ModelData folder, the model schema and how support points are set, so a new kind of editor model needs
no change here.

Joints come from `ship/articulation.json`. Their `axis` is in the part's local frame (§3), so it is turned
into the ship frame with the part's world `rotation_deg` (Blender XYZ Euler) before choosing the motion:
about z = azimuth, about the bow axis (x) = roll ("rotation"), about the lateral axis (y) = elevation; a full
turn about a horizontal axis is a spinning part (propeller, wheel): "visual".

Systems (sensors, weapons, other) are the donor's, fitted to the model as the pipeline drafts them
(pipeline/systems.py): sensors at the radar parts and the bridge, donor weapons rebound to the model's mounts or
removed. `mak.json` `systems_off` lists drafted changes the user turned down (by systemName).

Part codes follow the current suggestions unless the user changed them (contract §10, `parts_suggested`): a saved
code equal to the suggestion it was saved with is re-suggested now, so a rule added after the settings were saved
still applies. On 15 Nov, settings drafted by a stale server kept every weapon mount static and the export came
out with no weapons and no warning. The export now also warns about a coded part under an uncoded moving
parent, which no weapon can bind, and about weapon mounts left without a weapon system (`Prepared.warnings`).
"""
from __future__ import annotations

import json
import math
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from pipeline import draft_manifest, generate, inspect_glb, profiles, systems
from pipeline.common import safe_name

DEFAULT_KIND = "surface_ship"            # what the editor builds today
FORWARD_AXIS = "+X"                      # contract §1: +X is the bow, for every model the editor exports
AXES = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}


class PackageError(ValueError):
    pass


@dataclass
class Prepared:
    model_dir: Path
    manifest: dict
    pending: list[str] = field(default_factory=list)
    not_exported: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)      # shown as warning checks in the export report


def _read(path: Path, what: str):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise PackageError(f"{what} not found: {path}")
    except ValueError as e:
        raise PackageError(f"{what} is not valid JSON ({path}): {e}")


def rotate_xyz(v, rotation_deg) -> tuple[float, float, float]:
    """Blender XYZ Euler: v' = Rz . Ry . Rx . v."""
    ax, ay, az = (math.radians(a) for a in rotation_deg)
    x, y, z = v
    y, z = y * math.cos(ax) - z * math.sin(ax), y * math.sin(ax) + z * math.cos(ax)
    x, z = x * math.cos(ay) + z * math.sin(ay), -x * math.sin(ay) + z * math.cos(ay)
    x, y = x * math.cos(az) - y * math.sin(az), x * math.sin(az) + y * math.cos(az)
    return x, y, z


def motion_of(joint: dict) -> tuple[str, str | None]:
    """(motion, note) for a joint in the editor's frame (+X bow, +Y port, +Z up)."""
    local = AXES.get(str(joint.get("axis", "")).lower())
    if local is None:
        return "visual", f"axis {joint.get('axis')!r} is not x, y or z"
    world = rotate_xyz(local, joint.get("rotation_deg") or [0, 0, 0])
    i = max(range(3), key=lambda k: abs(world[k]))
    note = None if abs(world[i]) > 0.9 else f"axis {tuple(round(c, 2) for c in world)} is not along a ship axis"
    kind = str(joint.get("kind") or "revolute").lower()
    if kind in ("prismatic", "slider", "linear"):
        return ("translate_x", "translate_y", "translate_z")[i], note
    if i == 2:
        return "azimuth", note
    if (joint.get("min_deg") or 0) <= -180 and (joint.get("max_deg") or 0) >= 180:
        return "visual", note
    return ("rotation" if i == 0 else "elevation"), note


def suggest_codes(joints: dict[str, dict]) -> dict[str, int | None]:
    """The draft's part code for each joint ({path: joint with axis, limits, kind, rotation_deg}), as Review >
    MAK suggests them (draft_mak uses this too): Table 58 codes from the part names, in natural name order so
    numbered parts take their family's codes in turn."""
    used: set[int] = set()
    out: dict[str, int | None] = {}
    for path in sorted(joints, key=draft_manifest._natural):
        motion, _ = motion_of(joints[path])
        code = draft_manifest.suggest_code(path, used, {}, motion, "surface")
        if code is not None:
            used.add(code)
        out[path] = code
    return out


def effective_codes(mak: dict, suggested: dict[str, int | None]) -> tuple[dict[str, int | None], list[str]]:
    """(code per joint, warnings) from the saved settings and today's suggestions.

    - A part the settings don't name (new since they were saved): today's suggestion.
    - A saved code equal to the suggestion it was saved with (`parts_suggested`): today's suggestion, so the
      settings follow the rules as they improve.
    - A saved code the user changed: kept.
    - Settings saved before `parts_suggested` existed: kept as saved, with a warning wherever today's suggestion
      differs. Whether such a value was the user's choice can't be told, so it is never overridden silently."""
    saved = mak.get("parts") or {}
    was = mak.get("parts_suggested")
    out, warnings, legacy = {}, [], []
    for path, now in suggested.items():
        if path not in saved:
            out[path] = now
        elif isinstance(was, dict) and path in was and saved[path] == was[path]:
            out[path] = now
        else:
            out[path] = saved[path]
            if not isinstance(was, dict) and saved[path] != now:
                legacy.append(f"{path} is {saved[path] or 'static'} (suggested now: {now or 'static'})")
    if legacy:
        warnings.append("The saved MAK settings differ from today's suggestions and were saved before the settings "
                        "recorded which codes were suggestions, so they are used as saved: " + "; ".join(legacy) +
                        ". If you didn't choose these, open Review > MAK, press 'Use the current suggestions' and save.")
    return out, warnings


def chain_warnings(joints: dict[str, dict], nodes: dict, codes: dict[str, int | None]) -> list[str]:
    """A coded part whose nearest moving ancestor has no code (a gun under a static mount): the part moves on its
    own in VR-Forces, and a weapon, which needs the mount as well, cannot bind to it."""
    out = []
    for path, code in codes.items():
        if not code:
            continue
        up = (nodes.get(path) or {}).get("parent") or joints[path].get("parent")
        seen = set()
        while up and up not in joints and up not in seen:
            seen.add(up)
            up = (nodes.get(up) or {}).get("parent")
        if up in joints and not codes.get(up):
            out.append(f"{path} has part code {code}, but {up}, the moving part it sits on, is static. It turns on "
                       f"its own in VR-Forces, and a weapon cannot bind to it without {up}: give {up} a code in "
                       "Review > MAK (the draft suggests one when its name says what it is).")
    return out


def weapon_warnings(joints: dict[str, dict], nodes: dict, suggested: dict[str, int | None],
                    drafted: list[dict]) -> list[str]:
    """Weapon mounts (by name: the draft suggests a turret, gun or launcher code for them) that no weapon system
    drives after the systems draft: they move in the model only."""
    def weapon(p):
        return suggested.get(p) is not None and systems.is_weapon_part(int(suggested[p]))

    def parent_joint(p):
        up, seen = (nodes.get(p) or {}).get("parent") or joints[p].get("parent"), set()
        while up and up not in joints and up not in seen:
            seen.add(up)
            up = (nodes.get(up) or {}).get("parent")
        return up if up in joints else None

    mounts = [p for p in sorted(joints, key=draft_manifest._natural) if weapon(p) and not weapon(parent_joint(p) or "")]
    if not mounts:
        return []
    bound = {v for e in drafted if str(e.get("sysdef") or "").startswith("weapons/") and not e.get("remove")
             for v in (e.get("bind") or {}).values() if isinstance(v, str)}

    def under(p, mount):
        while p and p != mount:
            p = parent_joint(p)
        return p == mount

    bare = [m for m in mounts if not any(under(b, m) for b in bound if b in joints)]
    if not bare:
        return []
    return [f"Weapons fitted: {len(mounts) - len(bare)} of {len(mounts)} weapon mounts. No weapon system drives "
            f"{', '.join(bare)}: they move in the model only. Check their part codes in Review > MAK (the mount "
            "and the gun or launcher under it both need one) and the drafted system changes."]


def _display_name(*candidates) -> str:
    """The first usable name, made safe as a file name: VR-Forces names the .entity file after it."""
    for c in candidates:
        if isinstance(c, str):
            clean = re.sub(r"\s+", " ", re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", c)).strip(" .")
            if clean:
                return clean
    return "ship"


def _model_name(ship_id: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_]", "_", ship_id) or "ship"
    return name if name[0].isalpha() else f"s_{name}"


def draft_systems(parts: list[dict], nodes: dict, donor: str, kind: str, cfg: dict | None,
                  off=()) -> tuple[list[dict], list[str]]:
    """The pipeline's `systems` draft for this donor (systems.draft_systems), less the changes in `off`.
    ([], []) when the install or the donor isn't there."""
    if not cfg or not donor:
        return [], []
    catalog = systems.load_catalog(cfg)
    path = generate.donor_path(cfg, donor)
    if catalog is None or not path.exists():
        return [], []
    profile = profiles.profile_for({"kind": kind})
    entries, notes = systems.draft_systems(path.read_text(encoding="utf-8", errors="replace"), parts, nodes, catalog,
                                           catalog.repositories.get(profile.platform_ope),
                                           systems.group_for_profile(profile))
    off = set(off or ())
    return [e for e in entries if e["system"] not in off], notes + [
        f"{e['system']}: kept as the donor has it (turned off in the MAK settings)" for e in entries if e["system"] in off]


def prepare(package_dir: Path, work_dir: Path, cfg: dict | None = None) -> Prepared:
    package_dir, work_dir = Path(package_dir), Path(work_dir)
    manifest = _read(package_dir / "manifest.json", "Package manifest.json")
    if manifest.get("schema") != "me3d.package/1":
        raise PackageError(f"{package_dir} is not an me3d.package/1 (schema {manifest.get('schema')!r})")
    ship = manifest.get("ship") or {}
    glb = package_dir / ship.get("glb", "")
    if not glb.is_file():
        raise PackageError(f"ship model not found: {glb}")
    mak_path = package_dir / "ship" / "mak.json"
    if not mak_path.exists():
        raise PackageError("The ship has no MAK settings (ship/mak.json): set its DIS type and donor first.")
    mak = _read(mak_path, "ship/mak.json")
    meta = _read(package_dir / ship["meta"], "Ship metadata") if ship.get("meta") else {}
    articulation = _read(package_dir / ship["articulation"], "articulation.json") if ship.get("articulation") else []

    joints = {j["part"]: j for j in articulation if isinstance(j, dict) and j.get("part")}
    unknown = sorted(set(mak.get("parts") or {}) - set(joints))
    if unknown:
        raise PackageError(f"MAK settings name parts that are not moving parts of this version: {', '.join(unknown)}")
    suggested = suggest_codes(joints)
    codes, warnings = effective_codes(mak, suggested)

    # Model files are named after the ship project (mak.json `name`, §8), like the .entity: the ship id is the
    # spec's, which can be the template's ("custom_vessel" for USS Abraham Lincoln). Older packages: the id.
    name = safe_name(mak.get("name") if isinstance(mak.get("name"), str) else "")         or _model_name(ship.get("id") or manifest.get("id") or "ship")
    model_dir = work_dir / name
    if model_dir.exists():
        shutil.rmtree(model_dir)
    model_dir.mkdir(parents=True)
    shutil.copyfile(glb, model_dir / "model.glb")

    # parent = nearest ancestor in the model's node tree that is itself an exported part
    nodes = inspect_glb.analyse(model_dir / "model.glb", FORWARD_AXIS)["nodes"]
    mapped = {p for p, c in codes.items() if isinstance(c, int)}
    pending, parts, unmapped = [], [], []
    for part, j in joints.items():
        motion, note = motion_of(j)
        if note:
            pending.append(f"{part}: {note}; treated as {motion}")
        if part not in mapped:
            unmapped.append({"node": part, "motion": motion, "min": j.get("min_deg"), "max": j.get("max_deg"),
                             "priority": j.get("priority"), "reason": "no DIS part code in the MAK settings"})
            pending.append(f"{part} stays static (no DIS part code)")
            continue
        parent = (nodes.get(part) or {}).get("parent") or j.get("parent")
        while parent and parent not in mapped:
            parent = (nodes.get(parent) or {}).get("parent")
        parts.append({"node": part, "dis_part": int(codes[part]), "parent": parent, "motion": motion,
                      "min": float(j.get("min_deg") or 0.0), "max": float(j.get("max_deg") or 0.0)})

    kind = mak.get("kind") or DEFAULT_KIND
    try:
        profile = profiles.profile_for({"kind": kind})
    except profiles.ProfileError as e:
        raise PackageError(f"MAK settings: {e}")
    if profile.route != "model":
        raise PackageError(f"MAK settings: kind '{kind}' is a DI-Guy character, not a model; it cannot be the ship")
    out = {
        "name": name,
        # The ship project's name (mak.json `name`, §8). The metadata's name is the spec's, which keeps the
        # catalog template's name ("Container ship (200 m feeder, gearless)") when a project is renamed.
        "display_name": _display_name(mak.get("name"), meta.get("name"), manifest.get("name"), name),
        "kind": kind,
        "domain": profile.domain,
        "forward_axis_in_blender": FORWARD_AXIS,
        "model_category_dir": profile.category,
        "platform_ope": profile.platform_ope,
        "dis_type": mak.get("dis_type", ""),
        "donor_entity": mak.get("donor_entity", ""),
        "articulated_parts": parts,
        "unmapped_joints": unmapped,
    }
    if mak.get("short_name"):
        out["short_name"] = mak["short_name"]
    drafted, notes = draft_systems(parts, nodes, out["donor_entity"], kind, cfg, mak.get("systems_off"))
    if drafted:
        out["systems"] = drafted
    pending += [f"systems: {n}" for n in notes]
    warnings += chain_warnings(joints, nodes, codes)
    warnings += weapon_warnings(joints, nodes, suggested, drafted)
    (model_dir / "manifest.json").write_text(json.dumps(out, indent=2), encoding="utf-8")

    not_exported = [f"{c.get('id')} at {c.get('station')} (VR-Forces crew need DI-Guy characters)"
                    for c in manifest.get("crew") or [] if isinstance(c, dict)]
    return Prepared(model_dir, out, pending, not_exported, warnings)
