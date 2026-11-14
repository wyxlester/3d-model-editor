"""Draft models/<Name>/manifest.json from a Blender project's exports.

    python -m pipeline.draft_manifest models/<Name> [--force]

Reads (all optional except the glb):
  *.glb               node tree, pivots, bounds, socket_* marker nodes
  parts.json          per-part path, parent, origin, joint {axis, min_deg, max_deg, kind, priority}, station
  spec.json           name, schema and params (loa/beam/draught). `meta` is NOT trusted (it can be left over from
                      the template the model was built from), and `custom_ops` code is NEVER executed.
  build_report.json   Blender build result; the draft refuses a failed build or a glb that doesn't match it.

Writes manifest.json, or manifest.draft.json if a manifest already exists (unless --force).
Fields only a person can decide are written as "TODO: ..."; load_manifest() rejects them until filled.
Joints are mapped to DIS part codes only when the node name makes the code obvious (radar, turret, gun...).
The rest go to "unmapped_joints", which the pipeline reports but treats as static geometry.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

from . import artparts, inspect_glb
from .common import load_config

DOMAIN_DEFAULTS = {   # platform .ope and ModelData category, from the 5.1.1 install
    "land": ("Ground_Vehicle.ope", "Vehicles/Wheeled"),
    "air": ("Fixed_Wing_Aircraft.ope", "Vehicles/FixedWing"),
    "surface": ("Surface_Ship.ope", "Vehicles/Surface"),
    "subsurface": ("Subsurface_Vehicle.ope", "Vehicles/Subsurface"),
    "space": ("Space_Object.ope", "Vehicles/Space"),
    "lifeform": ("Human.ope", "Lifeforms"),
}

# node-name keyword -> (first code, step between instances). Only unambiguous names.
NAME_CODES = [
    (re.compile(r"(?i)radar"), 5376, 32),
    (re.compile(r"(?i)turret"), 4096, 32),
    (re.compile(r"(?i)(gun|cannon|barrel)"), 4416, 32),
    (re.compile(r"(?i)launcher"), 4736, 32),
    (re.compile(r"(?i)periscope"), 2048, 0),
    (re.compile(r"(?i)snorkel"), 2112, 0),
    (re.compile(r"(?i)antenna"), 2080, 0),
    (re.compile(r"(?i)hatch"), 3232, 0),
    (re.compile(r"(?i)landing_?gear"), 3072, 0),
    (re.compile(r"(?i)main_?rotor"), 1184, 0),
    (re.compile(r"(?i)tail_?rotor"), 1216, 0),
]

TODO = "TODO: "


class DraftError(Exception):
    pass


def _natural(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def identifier(text: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", text)
    out = "".join(w[:1].upper() + w[1:] for w in words)
    return out if out[:1].isalpha() else "Model" + out


def find_glb(model_dir: Path) -> Path:
    if (model_dir / "model.glb").exists():
        return model_dir / "model.glb"
    glbs = sorted(model_dir.glob("*.glb"))
    if len(glbs) == 1:
        return glbs[0]
    if not glbs:
        raise DraftError(f"no .glb in {model_dir}")
    raise DraftError(f"several .glb files in {model_dir}: {[g.name for g in glbs]}; keep one")


def _load(model_dir: Path, name: str):
    p = model_dir / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


# ------------------------------------------------------------------ inference
def infer_forward_axis(nodes: dict, bounds: dict) -> tuple[str, str]:
    """Blender-frame forward axis. Prefers explicit markers, else the longest horizontal axis (sign unknown)."""
    def axis_of(p):
        i = 0 if abs(p[0]) >= abs(p[1]) else 1
        return ("+" if p[i] >= 0 else "-") + "XY"[i]
    for name in ("socket_bow", "socket_front", "socket_nose"):
        if name in nodes and max(abs(nodes[name]["pivot"][0]), abs(nodes[name]["pivot"][1])) > 0.1:
            return axis_of(nodes[name]["pivot"]), f"{name} marker"
    if "socket_light_stern" in nodes:
        p = nodes["socket_light_stern"]["pivot"]
        ax = axis_of(p)
        return ("-" if ax[0] == "+" else "+") + ax[1], "opposite socket_light_stern"
    ext = bounds["extent"]
    ax = "X" if ext[0] >= ext[1] else "Y"
    return f"{TODO}+{ax} or -{ax} (longest horizontal axis; which end is the front?)", "longest axis"


def infer_domain(spec: dict | None, bounds: dict) -> tuple[str, str]:
    schema = ((spec or {}).get("schema") or "").lower()
    params = (spec or {}).get("params") or {}
    if "ship" in schema or "draught_m" in params:
        if "sub" in schema:
            return "subsurface", "spec schema"
        return "surface", "spec schema/draught"
    for key, dom in (("aircraft", "air"), ("plane", "air"), ("heli", "air"), ("vehicle", "land"), ("tank", "land")):
        if key in schema:
            return dom, "spec schema"
    if abs(bounds["min"][2]) <= max(0.02, 0.02 * max(bounds["extent"])):
        return "land", "model base sits on z=0"
    return f"{TODO}land | air | surface | subsurface | space | lifeform", "unknown"


def joint_motion(joint: dict, forward: str) -> str:
    """Blender joint axis -> VR-Forces motion, given the Blender forward axis."""
    axis = (joint.get("axis") or "").lower()
    kind = (joint.get("kind") or "revolute").lower()
    fwd = forward[-1].lower() if not forward.startswith(TODO) else "y"
    if kind in ("prismatic", "slider", "linear"):
        return "translate_z" if axis == "z" else ("translate_y" if axis == fwd else "translate_x")
    full_turn = joint.get("min_deg", 0) <= -180 and joint.get("max_deg", 0) >= 180
    if axis == "z":
        return "azimuth"
    if full_turn:
        return "visual"          # continuous spin about a horizontal axis: propellers, wheels, rotors
    return "rotation" if axis == fwd else "elevation"


def suggest_code(node: str, used: set[int], counters: dict) -> int | None:
    leaf = node.split("__")[-1] if "__" in node else node
    for rx, base, step in NAME_CODES:
        if rx.search(leaf) or rx.search(node):
            if step == 0:
                return None if base in used else base
            n = counters.get(base, 0)
            while base + step * n in used and n < 10:
                n += 1
            if n >= 10:
                return None
            counters[base] = n + 1
            return base + step * n
    return None


ARMED_PENALTY = 2.0   # a donor's systems are copied into the new entity (users guide 65.3.1)


def suggest_donors(cfg: dict, domain: str, bounds: dict, top: int = 3, armed: bool = False) -> list[dict]:
    """Stock entities on the domain's platform with the closest bounding volume (log-size distance).
    Military donors (weapon systems or the "Military" GUI category: IFF, data links, radars) are pushed down
    unless `armed`, and civilian ones are pushed down when it is."""
    ope = DOMAIN_DEFAULTS.get(domain, (None,))[0]
    vrfsim = Path(cfg["sms_dir"]) / cfg.get("donor_sms", "EntityLevel") / "vrfSim"
    if not ope or not vrfsim.exists():
        return []
    want = (bounds["extent"][1], bounds["extent"][0], bounds["extent"][2])     # length, width, height (MAK frame)
    dims_re = re.compile(r'<dimensions length="([\d.]+)" width="([\d.]+)" height="([\d.]+)"')
    scored = []
    for p in vrfsim.glob("*.entity"):
        text = p.read_text(encoding="utf-8", errors="replace")
        if f"/{ope}\"" not in text:
            continue
        m = dims_re.search(text)
        if not m:
            continue
        have = [max(float(v), 0.01) for v in m.groups()]
        score = sum(abs(math.log(max(w, 0.01) / h)) for w, h in zip(want, have))
        has_weapons = bool(re.search(r'systemName="weapon', text))
        cats = re.search(r'paramName="gui-categories">([^<]*)', text)
        military = has_weapons or bool(cats and '"Military"' in cats.group(1))
        if military != armed:
            score += ARMED_PENALTY
        ot = re.search(r'objectType="([^"]+)"', text)
        scored.append({"entity": p.stem, "objectType": ot.group(1) if ot else None,
                       "size_lwh": have, "military": military, "score": round(score, 3)})
    return sorted(scored, key=lambda d: d["score"])[:top]


# ------------------------------------------------------------------ draft
def draft(model_dir: Path, cfg: dict | None = None, node_map: artparts.NodeNameMap | None = None) -> tuple[dict, list[str]]:
    cfg = cfg or load_config()
    node_map = node_map if node_map is not None else artparts.load_node_map(cfg.get("fbx_node_name_map"))
    notes: list[str] = []
    glb_path = find_glb(model_dir)
    spec, parts, build = _load(model_dir, "spec.json"), _load(model_dir, "parts.json"), _load(model_dir, "build_report.json")

    # Blender frame (no forward rotation) so parts.json and the glb are directly comparable
    report = inspect_glb.analyse(glb_path, "+Y")
    nodes, bounds = report["nodes"], report["bounds_mak_frame"]

    if build is not None:
        if not build.get("ok", False) or build.get("errors"):
            raise DraftError(f"build_report.json says the Blender build failed: {build.get('errors')}")
        if build.get("triangles") not in (None, report["triangles"]):
            raise DraftError(f"glb has {report['triangles']} triangles but build_report.json says {build['triangles']}; re-export both")
        for k, i in (("bounds_min", "min"), ("bounds_max", "max")):
            if k in build and max(abs(a - b) for a, b in zip(build[k], bounds[i])) > 0.01:
                raise DraftError(f"glb {i} bounds {bounds[i]} != build_report.json {build[k]}; re-export both")
        for w in build.get("warnings") or []:
            notes.append(f"build warning: {w}")

    parts = parts or []
    drift = [p["path"] for p in parts if p["path"] in nodes and (
        nodes[p["path"]]["parent"] != p.get("parent")
        or max(abs(a - b) for a, b in zip(nodes[p["path"]]["pivot"], p.get("origin_m", nodes[p["path"]]["pivot"]))) > 0.01)]
    missing = [p["path"] for p in parts if p["path"] not in nodes]
    if missing:
        raise DraftError(f"parts.json lists nodes that are not in the glb: {missing[:5]}")
    if drift:
        notes.append(f"parts.json pivot/parent differs from the glb for {drift[:5]}; the glb wins")

    forward, why_fwd = infer_forward_axis(nodes, bounds)
    domain, why_dom = infer_domain(spec, bounds)
    notes.append(f"forward_axis_in_blender = {forward} (from {why_fwd})")
    notes.append(f"domain = {domain} (from {why_dom})")

    display = (spec or {}).get("name") or glb_path.stem
    ope, category = DOMAIN_DEFAULTS.get(domain, (f"{TODO}platform .ope", f"{TODO}ModelData category"))

    # spec.meta can be stale template data: say so when it contradicts the geometry
    meta = (spec or {}).get("meta") or {}
    loa = ((spec or {}).get("params") or {}).get("loa_m")
    if meta.get("length_m") and loa and abs(meta["length_m"] - loa) > 0.05 * loa:
        notes.append(f"spec.json meta ('{meta.get('name')}', {meta['length_m']} m) contradicts params.loa_m {loa} m: meta ignored")

    # joints -> articulated parts
    jointed = sorted((p for p in parts if p.get("joint")), key=lambda p: _natural(p["path"]))
    used: set[int] = set()
    counters: dict[int, int] = {}
    mapped: dict[str, dict] = {}
    unmapped: list[dict] = []
    for p in jointed:
        j = p["joint"]
        motion = joint_motion(j, forward)
        entry = {"node": p["path"], "motion": motion,
                 "min": j.get("min_deg"), "max": j.get("max_deg"), "priority": j.get("priority")}
        code = node_map.parts.get(p["path"]) or suggest_code(p["path"], used, counters)
        if code is None:
            entry["reason"] = "no obvious DIS part code: add it to articulated_parts with a dis_part (manual Table 58) to articulate it"
            if motion == "visual":
                entry["reason"] = "continuous spin (propeller/wheel): visual-only, and needs an FBX_NodeNameMap code to animate"
            unmapped.append(entry)
            continue
        used.add(code)
        mapped[p["path"]] = {"node": p["path"], "dis_part": code, "parent": None, "motion": motion,
                             "min": j.get("min_deg"), "max": j.get("max_deg")}
    # parent = nearest ancestor that is itself a mapped part
    for name, entry in mapped.items():
        cur = nodes[name]["parent"]
        while cur and cur not in mapped:
            cur = nodes.get(cur, {}).get("parent")
        entry["parent"] = cur
    for e in unmapped:
        cur = nodes[e["node"]]["parent"]
        while cur and not any(q["path"] == cur and q.get("joint") for q in parts):
            cur = nodes.get(cur, {}).get("parent")
        e["parent_joint"] = cur

    stations = [{"node": p["path"], "label": (p.get("station") or {}).get("label"),
                 "activities": (p.get("station") or {}).get("activities"), "pivot_blender": p.get("origin_m")}
                for p in parts if p.get("station")]
    sockets = {n: i["pivot"] for n, i in nodes.items() if n.startswith("socket_")}

    # weapon-like joints (turret/gun/launcher codes) suggest an armed donor
    armed = any(artparts.is_weapon(p["dis_part"]) or 4096 <= p["dis_part"] < 4416 for p in mapped.values())
    donors = suggest_donors(cfg, domain, inspect_glb.analyse(glb_path, forward)["bounds_mak_frame"], armed=armed) \
        if not forward.startswith(TODO) and not domain.startswith(TODO) else []
    if donors:
        notes.append(f"donor suggestions ({'military' if armed else 'civilian'} preferred, closest size on the same platform): "
                     + ", ".join(f"{d['entity']} {d['size_lwh']}{' [military]' if d['military'] else ''}" for d in donors))

    manifest = {
        "_draft": "Drafted by pipeline.draft_manifest from the Blender exports. Fill every TODO, check the rest, "
                  "then run: python -m pipeline.run " + model_dir.as_posix(),
        "name": identifier(display),
        "display_name": display,
        "domain": domain,
        "dis_type": f"{TODO}7-field DIS type unique in the install, e.g. kind:domain:country:category:subcategory:specific:extra",
        "donor_entity": donors[0]["entity"] if donors else f"{TODO}stock .entity name in {cfg.get('donor_sms', 'EntityLevel')}/vrfSim",
        "platform_ope": ope,
        "model_category_dir": category,
        "forward_axis_in_blender": forward,
        "articulated_parts": list(mapped.values()),
        "unmapped_joints": unmapped,
        "export_info": {
            "spec_params": {k: v for k, v in ((spec or {}).get("params") or {}).items() if k.endswith("_m")},
            "stations": stations,
            "sockets_blender": sockets,
            "donor_candidates": donors,
        },
    }
    return manifest, notes


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("model_dir", type=Path, help="models/<Name> holding the .glb and the Blender exports")
    ap.add_argument("--force", action="store_true", help="overwrite an existing manifest.json")
    args = ap.parse_args(argv)
    if args.model_dir.suffix.lower() == ".glb":
        print(f"input error: '{args.model_dir.as_posix()}' is a .glb file; give its model folder, e.g. "
              f"python -m pipeline.draft_manifest {args.model_dir.parent.as_posix()}", file=sys.stderr)
        return 2
    try:
        manifest, notes = draft(args.model_dir)
    except (DraftError, FileNotFoundError) as e:
        print(f"draft error: {e}", file=sys.stderr)
        return 1
    target = args.model_dir / "manifest.json"
    if target.exists() and not args.force:
        target = args.model_dir / "manifest.draft.json"
    target.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    for n in notes:
        print(f"  - {n}")
    todos = [k for k, v in manifest.items() if isinstance(v, str) and v.startswith(TODO)]
    print(f"\nwrote {target}")
    print(f"  {len(manifest['articulated_parts'])} articulated part(s) mapped, {len(manifest['unmapped_joints'])} joint(s) left static")
    if todos:
        print(f"  fill in before running the pipeline: {', '.join(todos)}")
    if target.name == "manifest.draft.json":
        print("  manifest.json already exists, so the draft was written alongside it (use --force to overwrite)")
    glb = find_glb(args.model_dir)
    if glb.name != "model.glb":
        print(f"  the pipeline reads model.glb: rename '{glb.name}' to model.glb before running it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
