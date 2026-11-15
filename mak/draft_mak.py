"""MAK settings suggestions for a ship version (contract §10, GET /api/projects/{id}/mak/draft).

Pure: takes the version's parts.json, build report and name. Part codes come from unambiguous node names
(radar, rudder, deck elevator, weapon mount ...; pipeline/draft_manifest.py), motions from the joint axis in the ship frame
(from_package.motion_of), donors from stock entities of similar size on the Surface_Ship platform,
civilian first (pipeline/draft_manifest.suggest_donors), and the changes that fit the donor's systems to this
model (from_package.draft_systems; the export applies the same draft, less `systems_off`). The user confirms
everything; nothing here is final.
"""
from __future__ import annotations

from pathlib import Path

from pipeline import draft_manifest, generate, inspect_glb
from from_package import DEFAULT_KIND, FORWARD_AXIS, draft_systems, effective_codes, motion_of, suggest_codes

DIS_TYPE_HINT = ("1:3:<country>:<category>:<subcategory>:<specific>:<extra> "
                 "(kind 1 = platform, domain 3 = surface; e.g. category 80 = passenger vessel, SISO-REF-010)")


def mak_bounds(report: dict) -> dict:
    """The build report's ship-frame bounds (+X bow) as the pipeline's model-frame extent (X right, Y forward)."""
    lo, hi = report.get("bounds_min") or [0, 0, 0], report.get("bounds_max") or [0, 0, 0]
    ext = [hi[i] - lo[i] for i in range(3)]
    return {"extent": [ext[1], ext[0], ext[2]]}


def draft(parts: list, report: dict, name: str, cfg: dict, donor: str | None = None, codes: dict | None = None,
          glb: Path | None = None, kind: str | None = None, mak: dict | None = None) -> dict:
    """`donor` and `codes` are the project's MAK settings when it has them (the systems draft follows them);
    `glb` is the version's model, whose socket nodes place the sensors. `mak` (the saved settings) picks the codes
    exactly as the export does (from_package.effective_codes), so the panel's systems draft is the export's."""
    jointed = sorted((p for p in parts if p.get("joint")), key=lambda p: draft_manifest._natural(p["path"]))
    joints = {p["path"]: dict(p["joint"], rotation_deg=p.get("rotation_deg") or [0, 0, 0]) for p in jointed}
    suggested = suggest_codes(joints)            # the export follows the same suggestions (contract §10)
    out_parts = []
    for p in jointed:
        j = joints[p["path"]]
        motion, _ = motion_of(j)
        code = suggested[p["path"]]
        out_parts.append({"path": p["path"], "motion": motion, "min_deg": j.get("min_deg"), "max_deg": j.get("max_deg"),
                          "priority": j.get("priority"), "suggested_code": code})
    armed = any(c["suggested_code"] and 4096 <= c["suggested_code"] < 5056 for c in out_parts)
    donors = draft_manifest.suggest_donors(cfg, "surface", mak_bounds(report), armed=armed)
    donor = donor or (donors[0]["entity"] if donors else None)
    if codes is None:
        codes = effective_codes(mak, suggested)[0] if mak else suggested
    chosen = {p["path"]: c for p in out_parts if (c := codes.get(p["path"], p["suggested_code"]))}
    by_path = {p["path"]: p for p in parts}
    rows = []
    for path, code in chosen.items():
        parent = by_path[path].get("parent")
        while parent and parent not in chosen:
            parent = (by_path.get(parent) or {}).get("parent")
        rows.append({"node": path, "dis_part": int(code), "parent": parent})
    nodes = {p["path"]: {"pivot": p.get("origin_m") or [0, 0, 0]} for p in parts}   # no sockets in parts.json
    if glb is not None and Path(glb).exists():
        try:
            nodes = inspect_glb.analyse(Path(glb), FORWARD_AXIS)["nodes"]
        except (ValueError, KeyError, IndexError, OSError):
            pass                       # an unreadable model: place sensors from parts.json alone
    entries, notes = draft_systems(rows, nodes, donor, kind or DEFAULT_KIND, cfg)
    return {"dis_type_hint": DIS_TYPE_HINT, "short_name": generate.short_name({"display_name": name}),
            "donors": [{k: d[k] for k in ("entity", "objectType", "size_lwh", "military")} for d in donors],
            "parts": out_parts,
            "systems": {"donor": donor, "changes": entries, "notes": notes}}
