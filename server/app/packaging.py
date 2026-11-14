"""Scene → export package (contract §7, §8). Pure file work, no Blender.

Crew transforms come from the ship's parts.json station entries (origin_m, rotation_deg; contract
§4). Articulation pivots come from each moving part's origin_m. Nothing is invented: a missing
transform is an error naming the part, because a crew member silently placed at the origin
would look plausible in a viewer and be wrong in the sim.
"""
from __future__ import annotations

import re
import shutil
import zipfile
from pathlib import Path

from .jobs import JobError, now_iso
from .store import ProjectStore, atomic_write_json

# StraitsSim's strait.ship_model/1 `type` vocabulary. Our builder types map onto it; a sampan has
# no StraitsSim class, "fishing" is the nearest small-craft class. A spec may override with
# params.strait_type.
STRAIT_TYPES = {"container": "container", "tanker": "tanker", "sampan": "fishing"}
SAFE_ID = re.compile(r"[^a-z0-9_]+")


def safe_id(s: str) -> str:
    return SAFE_ID.sub("_", str(s).lower()).strip("_") or "x"


def _vec3(value, what: str) -> list[float]:
    if not (isinstance(value, (list, tuple)) and len(value) == 3):
        raise JobError(what)
    return [round(float(v), 6) for v in value]


def stations_of(parts: list) -> list[dict]:
    out = []
    for p in parts:
        if p.get("station"):
            st = p["station"] if isinstance(p["station"], dict) else {}
            out.append({"name": p["path"], "label": st.get("label") or p.get("label"),
                        "parent": p.get("parent"), "pose": st.get("pose"),
                        "activities": st.get("activities", []),
                        "origin_m": p.get("origin_m"), "rotation_deg": p.get("rotation_deg")})
    return out


def station_transform(parts: list, station: str) -> tuple[list[float], list[float]]:
    entry = next((p for p in parts if p.get("path") == station and p.get("station")), None)
    if entry is None:
        names = ", ".join(s["name"] for s in stations_of(parts)) or "none"
        raise JobError(f"The ship has no station called {station!r}. Stations on this version: {names}.")
    loc = _vec3(entry.get("origin_m"), f"Station {station!r} has no origin_m in parts.json, so the "
                                       "crew member cannot be placed. The ship builder must emit it.")
    rot = _vec3(entry.get("rotation_deg"), f"Station {station!r} has no rotation_deg in parts.json. "
                                           "The ship builder must emit it.")
    return loc, rot


def articulation(parts: list) -> list[dict]:
    out = []
    for p in parts:
        j = p.get("joint")
        if not j:
            continue
        pivot = _vec3(p.get("origin_m"), f"Moving part {p.get('path')!r} has no origin_m (its pivot) "
                                          "in parts.json. The ship builder must emit it.")
        # rotation_deg lets a reader turn the local joint axis into the ship frame (§8; the MAK export uses it)
        rot = p.get("rotation_deg")
        out.append({"part": p["path"], "parent": p.get("parent"), "axis": j.get("axis"),
                    "min_deg": j.get("min_deg"), "max_deg": j.get("max_deg"),
                    "priority": j.get("priority"), "kind": j.get("kind", "revolute"),
                    "pivot_m": pivot,
                    "rotation_deg": _vec3(rot, "") if isinstance(rot, (list, tuple)) and len(rot) == 3 else [0.0, 0.0, 0.0]})
    return out


def ship_meta(spec: dict, report: dict | None, project_name: str) -> dict:
    """strait.ship_model/1. Starts from the spec's `meta` (catalog-supplied speeds, propulsion,
    notes); the principal dimensions always come from `params`, because that is what was built."""
    params = spec.get("params", {}) or {}
    ship_type = spec.get("type", "")
    base = spec.get("meta") if isinstance(spec.get("meta"), dict) else {}
    meta = dict(base)
    meta.update({
        "schema": "strait.ship_model/1",
        "id": safe_id(spec.get("id", ship_type)),
        "name": base.get("name") or spec.get("name", project_name),
        "type": params.get("strait_type") or base.get("type") or STRAIT_TYPES.get(ship_type, ship_type),
        "length_m": params.get("loa_m", base.get("length_m")),
        "beam_m": params.get("beam_m", base.get("beam_m")),
        "draught_m": params.get("draught_m", base.get("draught_m")),
        "depth_m": params.get("depth_m", base.get("depth_m")),
    })
    for key in ("displacement_t", "max_speed_kn", "service_speed_kn", "propulsion", "rcs_class", "crewed"):
        if key in params:
            meta[key] = params[key]
    meta.setdefault("crewed", True)
    meta.setdefault("notes", f"Built by the 3D model editor from me3d.ship_spec/1 ({ship_type}).")
    meta["source"] = (base.get("source", "") + f"; exported by 3d_model_editor {now_iso()}").lstrip("; ")
    return {k: v for k, v in meta.items() if v is not None}


def export_scene(scene: dict, store: ProjectStore, packages_dir: Path, log=print) -> dict:
    ship_ref = scene.get("ship") or {}
    sid, sv = ship_ref.get("project"), int(ship_ref.get("version") or 0)
    if not store.exists(sid):
        raise JobError(f"Ship project {sid!r} does not exist.")
    ship_project = store.load(sid)
    sv = sv or ship_project["version"]
    ship_dir = store.version_dir(sid, sv)
    ship_report = store.report(sid, sv)
    if not ship_report or not ship_report.get("ok") or not (ship_dir / "model.glb").exists():
        raise JobError(f"Ship version {sv} has no successful build to export.")
    spec = store.spec(sid, sv)
    parts = store.parts(sid, sv)
    ship_id = safe_id(spec.get("id") or ship_project["base"])

    pkg_id = f"pkg_{ship_id}_v{sv}_{safe_id(scene['id'])}"
    pkg = packages_dir / pkg_id
    if pkg.exists():
        shutil.rmtree(pkg)
    (pkg / "ship").mkdir(parents=True)

    # Validate everything that can fail before copying big files.
    art = articulation(parts)
    crew_entries, crew_files = [], {}
    distinct = {}
    for c in scene.get("crew", []):
        key = (c.get("project"), int(c.get("version") or 0))
        if key not in distinct:
            if not store.exists(key[0]):
                raise JobError(f"Crew project {key[0]!r} does not exist.")
            cv = key[1] or store.load(key[0])["version"]
            crep = store.report(key[0], cv)
            if not crep or not crep.get("ok"):
                raise JobError(f"Crew project {key[0]} version {cv} has no successful build.")
            distinct[key] = (cv, safe_id(store.spec(key[0], cv).get("id") or key[0]))
    used_ids: dict[str, tuple] = {}
    for key, (cv, cid) in distinct.items():
        final = cid if cid not in used_ids else f"{cid}_{safe_id(key[0])}_v{cv}"
        used_ids[final] = key
        crew_files[key] = (final, store.version_dir(key[0], cv) / "model.glb")
    for c in scene.get("crew", []):
        key = (c.get("project"), int(c.get("version") or 0))
        cid, _ = crew_files[key]
        loc, rot = station_transform(parts, c.get("station"))
        crew_entries.append({"id": cid, "glb": f"crew/{cid}.glb", "station": c["station"],
                             "animation": c.get("animation"), "location_m": loc, "rotation_deg": rot})

    shutil.copyfile(ship_dir / "model.glb", pkg / "ship" / f"{ship_id}.glb")
    atomic_write_json(pkg / "ship" / f"{ship_id}.json", ship_meta(spec, ship_report, ship_project["name"]))
    atomic_write_json(pkg / "ship" / "articulation.json", art)
    if ship_project.get("mak"):
        atomic_write_json(pkg / "ship" / "mak.json", ship_project["mak"])     # §8, read by the MAK export (§9b)
    if crew_files:
        (pkg / "crew").mkdir()
    for cid, src in crew_files.values():
        shutil.copyfile(src, pkg / "crew" / f"{cid}.glb")
    research_rel = None
    if ship_project.get("research"):
        (pkg / "research").mkdir()
        atomic_write_json(pkg / "research" / "research.json", ship_project["research"])
        research_rel = "research/research.json"

    manifest = {
        "schema": "me3d.package/1", "id": pkg_id, "name": scene.get("name", pkg_id),
        "created": now_iso(), "units": "metres",
        "source_frame": "+X bow, +Y port, +Z up; glTF files are +Y up",
        "ship": {"id": ship_id, "glb": f"ship/{ship_id}.glb", "meta": f"ship/{ship_id}.json",
                 "articulation": "ship/articulation.json"},
        "crew": crew_entries,
        "research": research_rel,
    }
    atomic_write_json(pkg / "manifest.json", manifest)

    zip_path = packages_dir / f"{pkg_id}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(pkg.rglob("*")):
            if f.is_file():
                z.write(f, Path(pkg_id) / f.relative_to(pkg))
    log(f"Package {pkg_id}: ship + {len(crew_files)} crew model(s), {len(crew_entries)} placement(s), "
        f"{len(art)} moving part(s).")
    return {"package_dir": str(pkg.resolve()), "zip_url": f"/api/files/packages/{pkg_id}.zip",
            "package_id": pkg_id}
