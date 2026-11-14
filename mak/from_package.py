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
"""
from __future__ import annotations

import json
import math
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from pipeline import inspect_glb, profiles

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


def prepare(package_dir: Path, work_dir: Path) -> Prepared:
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
    codes = mak.get("parts") or {}
    unknown = sorted(set(codes) - set(joints))
    if unknown:
        raise PackageError(f"MAK settings name parts that are not moving parts of this version: {', '.join(unknown)}")

    name = _model_name(ship.get("id") or manifest.get("id") or "ship")
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
    (model_dir / "manifest.json").write_text(json.dumps(out, indent=2), encoding="utf-8")

    not_exported = [f"{c.get('id')} at {c.get('station')} (VR-Forces crew need DI-Guy characters)"
                    for c in manifest.get("crew") or [] if isinstance(c, dict)]
    return Prepared(model_dir, out, pending, not_exported)
