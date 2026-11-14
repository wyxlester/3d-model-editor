"""MAK settings suggestions for a ship version (contract §10, GET /api/projects/{id}/mak/draft).

Pure: takes the version's parts.json, build report and name. Part codes come from unambiguous node names
(radar, rudder, deck elevator, weapon mount ...; pipeline/draft_manifest.py), motions from the joint axis in the ship frame
(from_package.motion_of), donors from stock entities of similar size on the Surface_Ship platform,
civilian first (pipeline/draft_manifest.suggest_donors). The user confirms everything; nothing here is final.
"""
from __future__ import annotations

from pipeline import draft_manifest, generate
from from_package import motion_of

DIS_TYPE_HINT = ("1:3:<country>:<category>:<subcategory>:<specific>:<extra> "
                 "(kind 1 = platform, domain 3 = surface; e.g. category 80 = passenger vessel, SISO-REF-010)")


def mak_bounds(report: dict) -> dict:
    """The build report's ship-frame bounds (+X bow) as the pipeline's model-frame extent (X right, Y forward)."""
    lo, hi = report.get("bounds_min") or [0, 0, 0], report.get("bounds_max") or [0, 0, 0]
    ext = [hi[i] - lo[i] for i in range(3)]
    return {"extent": [ext[1], ext[0], ext[2]]}


def draft(parts: list, report: dict, name: str, cfg: dict) -> dict:
    jointed = sorted((p for p in parts if p.get("joint")), key=lambda p: draft_manifest._natural(p["path"]))
    used: set[int] = set()
    counters: dict[int, int] = {}
    out_parts = []
    for p in jointed:
        j = dict(p["joint"], rotation_deg=p.get("rotation_deg") or [0, 0, 0])
        motion, _ = motion_of(j)
        code = draft_manifest.suggest_code(p["path"], used, counters, motion, "surface")
        if code is not None:
            used.add(code)
        out_parts.append({"path": p["path"], "motion": motion, "min_deg": j.get("min_deg"), "max_deg": j.get("max_deg"),
                          "priority": j.get("priority"), "suggested_code": code})
    armed = any(c["suggested_code"] and 4096 <= c["suggested_code"] < 5056 for c in out_parts)
    donors = draft_manifest.suggest_donors(cfg, "surface", mak_bounds(report), armed=armed)
    return {"dis_type_hint": DIS_TYPE_HINT, "short_name": generate.short_name({"display_name": name}),
            "donors": [{k: d[k] for k in ("entity", "objectType", "size_lwh", "military")} for d in donors],
            "parts": out_parts}
