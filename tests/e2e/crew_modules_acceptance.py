#!/usr/bin/env python3
"""Acceptance: crew modules, end to end, on the real running stack (real AI, real Blender builds).

Lester's requirement (contract §6 "Modules"): describing one module, in the right panel (scoped
chat, `module` set) or the main chat (unscoped), changes and re-renders that module only, without
resetting the rest of the human. Unit tests prove the module map; this proves the whole loop:
server -> AI tools -> spec -> Blender build -> parts.json.

Not collected by pytest (the file name has no test_ prefix) and does nothing on import. Run it by
hand against a server that is already running:

    server/.venv/bin/python tests/e2e/crew_modules_acceptance.py --dry      # plan only, no AI, no writes
    server/.venv/bin/python tests/e2e/crew_modules_acceptance.py --live     # the real run (costs AI calls)

Options: --base http://localhost:8787, --out <folder for renders and results.json>,
--blender <path>, --data-dir <server data folder, for render textures>, --job-timeout <seconds per job>.

What a live run does:
  1. Creates a NEW crew project from base male_default (named "E2E crew modules ...") and waits for
     its first build. Lester's existing projects are never read or written.
  2. Runs scenarios a-g in order. Each: snapshot the project -> act -> wait for the job -> snapshot
     -> work out which modules changed with server/app/crew_modules.py -> check that only the
     expected modules changed, the build is ok with a new version, the chat job's changed_modules
     agrees, and that parts.json changed only where that module lives (paths and bounds of every
     other part identical within BOUNDS_TOL_M).
  3. Downloads model.blend of the baseline and the final version and renders front and back PNGs
     with blender/crew/render.py into --out.
  4. Prints a table, the AI's reply for each AI scenario, and writes results.json. Exit code 1 on
     any FAIL (including a scenario skipped because the one it depends on failed).

Thresholds marked HEURISTIC are sanity checks for a ~1.78 m default male at rest pose (Blender
frame: +X is where he faces, +Y is his left), not contract values.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "server"))

from app import crew_modules as cm  # noqa: E402  (pure module, no I/O)
from app.config import Settings  # noqa: E402

BOUNDS_TOL_M = 0.002          # a part "moved" when any bounds coordinate differs by more than this
POLL_S = 2.0
SINGLET = {"style": "singlet", "color": [0.8, 0.6, 0.2]}

# HEURISTIC placement checks on the centre of the item's bounds (rest pose, default male).
PLACE_CHECKS: dict[str, tuple[str, Callable[[list[float]], bool]]] = {
    "right_hand": ("centre y < -0.15 (his right side)", lambda c: c[1] < -0.15),
    "left_hand": ("centre y > 0.15 (his left side)", lambda c: c[1] > 0.15),
    "back": ("centre x < 0.0 (behind the spine)", lambda c: c[0] < 0.0),
    "chest": ("centre x > 0.08 (in front of the chest)", lambda c: c[0] > 0.08),
    "hip": ("0.7 < centre z < 1.2", lambda c: 0.7 < c[2] < 1.2),
    "head": ("centre z > 1.5", lambda c: c[2] > 1.5),
}


# ---- HTTP ----------------------------------------------------------------------------------

class Api:
    def __init__(self, base: str):
        self.base = base.rstrip("/")

    def _req(self, method: str, path: str, body: Any = None, timeout: float = 60) -> bytes:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"} if data else {})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            raise RuntimeError(f"{method} {path} -> HTTP {e.code}: {detail}") from None

    def get(self, path: str, timeout: float = 60) -> Any:
        return json.loads(self._req("GET", path, timeout=timeout))

    def post(self, path: str, body: Any) -> Any:
        return json.loads(self._req("POST", path, body))

    def download(self, path: str, dest: Path) -> Path:
        dest.write_bytes(self._req("GET", path, timeout=300))
        return dest

    def wait_job(self, job_id: str, timeout: float) -> dict:
        end, last_note = time.time() + timeout, 0.0
        while time.time() < end:
            job = self.get(f"/api/jobs/{job_id}")
            if job["status"] in ("done", "error"):
                return job
            if time.time() - last_note > 30:
                tail = (job.get("log") or ["..."])[-1]
                print(f"      ... {job['status']}: {str(tail)[:110]}", flush=True)
                last_note = time.time()
            time.sleep(POLL_S)
        return {"id": job_id, "status": "timeout", "error": f"no result after {timeout:.0f} s",
                "log": [], "result": None}


# ---- snapshots and diffs -------------------------------------------------------------------

@dataclass
class Snap:
    version: int
    spec: dict
    parts: dict[str, dict]
    report: dict


def snapshot(api: Api, pid: str) -> Snap:
    p = api.get(f"/api/projects/{pid}")
    n = p["version"]
    parts = api.get(f"/api/projects/{pid}/versions/{n}/parts.json") if n else []
    return Snap(n, p["spec"] or {}, {x["path"]: x for x in parts}, p.get("build_report") or {})


def bounds_delta(a: dict, b: dict) -> float:
    return max(abs(x - y) for k in ("bounds_min", "bounds_max") for x, y in zip(a[k], b[k]))


def part_diff(before: dict[str, dict], after: dict[str, dict]) -> dict[str, list[str]]:
    common = set(before) & set(after)
    return {"added": sorted(set(after) - set(before)),
            "removed": sorted(set(before) - set(after)),
            "moved": sorted(p for p in common if bounds_delta(before[p], after[p]) > BOUNDS_TOL_M)}


def centre(parts: list[dict]) -> list[float]:
    lo = [min(p["bounds_min"][i] for p in parts) for i in range(3)]
    hi = [max(p["bounds_max"][i] for p in parts) for i in range(3)]
    return [round(0.5 * (lo[i] + hi[i]), 3) for i in range(3)]


def suit_was_set(spec: dict) -> bool:
    return (spec.get("clothes") or {}).get("suit") is not None


# ---- scenarios -----------------------------------------------------------------------------

@dataclass
class Scenario:
    key: str
    title: str
    kind: str                                  # "spec" (no AI) or "chat"
    body: Callable[[dict, Snap], dict | None]  # (state, before) -> request body; None = cannot run
    expect: Callable[[Snap], set[str]]         # modules expected to change, given the before state
    exact: bool = True                         # False: changed must be a subset of expect
    allowed_parts: str = r"(?!)"               # regex: part paths that may be added/removed/moved
    needs_new_version: bool = True
    check: Callable[[dict, Snap, Snap, dict], list[str]] | None = None  # extra failures
    depends_on: str | None = None


@dataclass
class Outcome:
    key: str
    title: str
    expected: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    server_changed: list[str] | None = None
    build_ok: bool | None = None
    version: str = ""
    seconds: float = 0.0
    failures: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    reply: str | None = None
    parts: dict = field(default_factory=dict)
    request: dict | None = None

    @property
    def verdict(self) -> str:
        return "FAIL" if self.failures else "PASS"


def check_a(state, before, after, result):
    f = []
    clothes = after.spec.get("clothes") or {}
    if clothes.get("shirt") != SINGLET:
        f.append(f"clothes.shirt is {clothes.get('shirt')!r}, expected {SINGLET!r}")
    if clothes.get("suit") is not None:
        f.append(f"clothes.suit is {clothes.get('suit')!r}, expected null (the outfit must be cleared)")
    if "Clothes__Shirt" not in after.parts:
        f.append("no Clothes__Shirt part after choosing the singlet preset")
    if "Clothes__Suit" in after.parts:
        f.append("Clothes__Suit is still in parts.json")
    # First live run: the suit came off and left bare legs. Trousers must fill in.
    if (clothes.get("pants") or {}).get("style") != "trousers" or "Clothes__Pants" not in after.parts:
        f.append(f"no trousers after the outfit came off: pants={clothes.get('pants')!r}")
    return f


def new_items(before: Snap, after: Snap, module: str) -> list[dict]:
    old_ids = {it.get("id") for it in before.spec.get("custom_items") or [] if isinstance(it, dict)}
    return [it for it in after.spec.get("custom_items") or []
            if isinstance(it, dict) and cm.item_module(it) == module and it.get("id") not in old_ids]


def check_b(state, before, after, result):
    f = []
    if (after.spec.get("clothes") or {}).get("shirt") is not None:
        f.append("clothes.shirt preset still set: a described shirt must replace the preset")
    if not [it for it in after.spec.get("custom_items") or [] if cm.item_module(it) == "shirt"]:
        f.append("no custom item tagged module 'shirt'")
    if not [p for p in part_diff(before.parts, after.parts)["added"] if p.startswith("Clothes__")]:
        f.append("no new Clothes__ part was built for the described shirt")
    if "Clothes__Shirt" in after.parts:
        f.append("the singlet preset's Clothes__Shirt part is still there")
    return f


def check_placement(place: str, parts: list[dict]) -> str | None:
    if place not in PLACE_CHECKS:
        return None
    what, ok = PLACE_CHECKS[place]
    c = centre(parts)
    return None if ok(c) else f"HEURISTIC placement: {place} expects {what}; item centre is {c}"


def check_c(state, before, after, result):
    f = []
    items = new_items(before, after, "equipment")
    added = [p for p in part_diff(before.parts, after.parts)["added"] if p.startswith("Equipment__")]
    if len(items) != 1:
        f.append(f"expected exactly one new equipment custom item, found {len(items)}")
    if not added:
        f.append("no new Equipment__ part in parts.json")
    if items:
        item = items[0]
        place = item.get("place")
        if place not in cm.PLACES:
            f.append(f"the new item's place is {place!r}, not one of {list(cm.PLACES)}")
        state["rifle_id"], state["rifle_place"] = item.get("id"), place
    if added:
        state["rifle_parts"] = added
        if state.get("rifle_place") in cm.PLACES:
            bad = check_placement(state["rifle_place"], [after.parts[p] for p in added])
            if bad:
                f.append(bad)
    return f


def body_d(state, before):
    rid = state.get("rifle_id")
    items = copy.deepcopy(before.spec.get("custom_items") or [])
    if not rid or not any(isinstance(it, dict) and it.get("id") == rid for it in items):
        return None
    target = "back" if state.get("rifle_place") != "back" else "left_hand"
    state["rifle_target"] = target
    for it in items:
        if it.get("id") == rid:
            it["place"] = target
    return {"patch": {"custom_items": items}}


def check_d(state, before, after, result):
    f = []
    target, rid = state.get("rifle_target"), state.get("rifle_id")
    item = next((it for it in after.spec.get("custom_items") or [] if it.get("id") == rid), None)
    if not item or item.get("place") != target:
        f.append(f"item {rid} place is {item and item.get('place')!r}, expected {target!r}")
    rifle = state.get("rifle_parts") or []
    missing = [p for p in rifle if p not in after.parts]
    if missing:
        f.append(f"rifle part(s) missing after the move: {missing}")
    unmoved = [p for p in rifle if p in after.parts and p in before.parts
               and bounds_delta(before.parts[p], after.parts[p]) <= BOUNDS_TOL_M]
    if unmoved:
        f.append(f"rifle part(s) did not move: {unmoved}")
    present = [after.parts[p] for p in rifle if p in after.parts]
    if present:
        bad = check_placement(target, present)
        if bad:
            f.append(bad)
    return f


def check_e(state, before, after, result):
    notes = []
    acc = after.spec.get("accessories") or []
    clothes = after.spec.get("clothes") or {}
    acc_items = new_items(before, after, "accessories")
    if not acc and clothes.get("hat") is None and not acc_items:
        return ["no accessory, hat or accessories custom item was added"]
    state.setdefault("notes_e", []).append(
        f"accessories={json.dumps(acc)} hat={clothes.get('hat')!r} hat_color={clothes.get('hat_color')!r} "
        f"new accessories items={[it.get('id') for it in acc_items]}")
    return notes


def check_f(state, before, after, result):
    body = after.spec.get("body") or {}
    hc = body.get("hair_color")
    if not (isinstance(hc, list) and len(hc) == 3 and all(isinstance(v, (int, float)) for v in hc)):
        return [f"body.hair_color is {hc!r}, expected an [r,g,b] blonde colour"]
    r, g, b = hc
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    if not (r > b and lum > 0.35):  # HEURISTIC: blonde is light and warm
        return [f"HEURISTIC: hair_color {hc} does not read as blonde (needs r > b and luminance > 0.35, "
                f"got {lum:.2f})"]
    return []


def check_g(state, before, after, result):
    f = []
    for k in ("hair", "hair_color"):
        a, b = (before.spec.get("body") or {}).get(k, "<absent>"), (after.spec.get("body") or {}).get(k, "<absent>")
        if a != b:
            f.append(f"body.{k} changed {a!r} -> {b!r} in a shirt-scoped turn")
    hair_items_before = [it.get("id") for it in before.spec.get("custom_items") or [] if cm.item_module(it) == "hair"]
    hair_items_after = [it.get("id") for it in after.spec.get("custom_items") or [] if cm.item_module(it) == "hair"]
    if hair_items_before != hair_items_after:
        f.append(f"hair custom items changed {hair_items_before} -> {hair_items_after}")
    if "Head__Hair" in before.parts and "Head__Hair" not in after.parts:
        f.append("Head__Hair disappeared: he went bald despite the scope guard")
    return f


SHIRT_PARTS = r"^Clothes__(?!Shoes$|Hat$|Gloves$|Pants)"

SCENARIOS = [
    Scenario("a", "Shirt preset: singlet (no AI)", "spec",
             body=lambda s, b: {"patch": {"clothes": {"shirt": SINGLET}}},
             expect=lambda b: {"shirt", "pants", "outfit"},
             # Taking the suit off is an outfit change: MakeHuman un-hides the skin under it, and the life
             # vest refits to the thinner layer (seen at 2 cm in the first live run). Both belong to it.
             allowed_parts=r"^(Clothes__(Shirt|Pants|Suit)|Body__Skin|Equipment__life_vest)$", check=check_a),
    Scenario("b", "Right panel, shirt: khaki vest", "chat",
             body=lambda s, b: {"message": "Shirt: a khaki sleeveless vest, open at the front", "mode": "build",
                                "module": "shirt"},
             expect=lambda b: {"shirt"} | ({"outfit"} if suit_was_set(b.spec) else set()),
             allowed_parts=SHIRT_PARTS, check=check_b),
    Scenario("c", "Right panel, equipment: rifle", "chat",
             body=lambda s, b: {"message": "Equipment: an old rifle with a wooden stock", "mode": "build",
                                "module": "equipment"},
             expect=lambda b: {"equipment"},
             allowed_parts=r"^Equipment__", check=check_c),
    # Where an item is carried is part of the equipment module (Lester, 29 Oct: "combine equipment
    # and equipment placement, it is a duplicate"), so moving the rifle changes equipment only.
    Scenario("d", "Equipment: move the rifle to the back (no AI)", "spec",
             body=body_d, expect=lambda b: {"equipment"},
             allowed_parts="<rifle parts>", check=check_d, depends_on="c"),
    Scenario("e", "Main chat: red baseball cap", "chat",
             body=lambda s, b: {"message": "give him a red baseball cap", "mode": "build"},
             expect=lambda b: {"accessories"},
             allowed_parts=r"^(Equipment__|Clothes__)", check=check_e),
    Scenario("f", "Main chat: blonde hair", "chat",
             body=lambda s, b: {"message": "make his hair blonde", "mode": "build"},
             expect=lambda b: {"hair"},
             allowed_parts=r"^Head__(Hair|Eyebrows)$", check=check_f),
    Scenario("g", "Scope guard: 'bald' in shirt panel", "chat",
             body=lambda s, b: {"message": "Shirt: also make him bald", "mode": "build", "module": "shirt"},
             expect=lambda b: {"shirt", "outfit"}, exact=False,
             allowed_parts=SHIRT_PARTS, needs_new_version=False, check=check_g),
]


def run_scenario(api: Api, pid: str, sc: Scenario, state: dict, results: dict, timeout: float) -> Outcome:
    out = Outcome(sc.key, sc.title)
    if sc.depends_on and results.get(sc.depends_on) and results[sc.depends_on].failures:
        out.failures.append(f"SKIPPED: depends on scenario {sc.depends_on}, which failed")
        return out
    before = snapshot(api, pid)
    body = sc.body(state, before)
    if body is None:
        out.failures.append("SKIPPED: could not build the request (the item from the earlier scenario is missing)")
        return out
    out.request = body
    expected = sc.expect(before)
    out.expected = cm.ordered(expected)
    t0 = time.time()
    path = f"/api/projects/{pid}/spec" if sc.kind == "spec" else f"/api/projects/{pid}/chat"
    print(f"  [{sc.key}] {sc.title}: POST {path.split('/')[-1]} {json.dumps(body)[:140]}", flush=True)
    try:
        job_id = api.post(path, body)["job_id"]
    except RuntimeError as e:
        out.failures.append(str(e))
        return out
    job = api.wait_job(job_id, timeout)
    result = job.get("result") or {}
    out.reply = result.get("reply") if isinstance(result, dict) else None
    after = snapshot(api, pid)
    out.seconds = round(time.time() - t0, 1)
    out.version = f"v{before.version}->v{after.version}"
    if job["status"] != "done":
        out.failures.append(f"job {job['status']}: {job.get('error')}")
        out.notes += [f"log: {line}" for line in (job.get("log") or [])[-15:]]

    # Build: ok, and a new version (g may legitimately build nothing).
    new_version = after.version > before.version
    out.build_ok = bool(after.report.get("ok")) if new_version else None
    if sc.needs_new_version and not new_version:
        out.failures.append(f"no new version (still v{after.version})")
    if new_version and not after.report.get("ok"):
        out.failures.append("build_report.ok is false: " + "; ".join(after.report.get("errors") or []))
    if sc.kind == "chat" and isinstance(result, dict) and result.get("version") not in (None, after.version):
        out.failures.append(f"job result version {result.get('version')} != project version {after.version}")

    # Modules: only the expected ones, and nothing no module owns.
    changed = cm.changed_modules(before.spec, after.spec)
    out.changed = cm.ordered(changed)
    if sc.exact and changed != expected:
        out.failures.append(f"modules changed {out.changed}, expected exactly {out.expected}")
    if not sc.exact and not changed <= expected:
        out.failures.append(f"modules changed {out.changed}, allowed only a subset of {out.expected}")
    unowned = [c.path for c in cm.changes(before.spec, after.spec) if c.module not in cm.MODULES]
    if unowned:
        out.failures.append(f"keys no module owns changed (a reset?): {unowned}")
    if sc.kind == "chat" and job["status"] == "done":
        server = result.get("changed_modules") if isinstance(result, dict) else None
        out.server_changed = server
        if server is None:
            out.failures.append("chat job result has no changed_modules")
        elif set(server) != changed:
            out.failures.append(f"job result changed_modules {server} != spec diff {out.changed}")

    # Parts: everything outside the module identical (paths and bounds).
    diff = part_diff(before.parts, after.parts)
    out.parts = diff
    allowed = sc.allowed_parts
    if allowed == "<rifle parts>":
        rifle = set(state.get("rifle_parts") or [])
        stray = diff["added"] + diff["removed"] + [p for p in diff["moved"] if p not in rifle]
    else:
        rx = re.compile(allowed)
        stray = [p for k in ("added", "removed", "moved") for p in diff[k] if not rx.search(p)]
    if stray:
        detail = []
        for p in stray:
            if p in before.parts and p in after.parts:
                detail.append(f"{p} moved {bounds_delta(before.parts[p], after.parts[p]):.4f} m")
            else:
                detail.append(f"{p} {'added' if p in after.parts else 'removed'}")
        out.failures.append("parts outside the module changed: " + ", ".join(detail))

    if sc.check:
        out.failures += sc.check(state, before, after, result if isinstance(result, dict) else {})
    out.notes += state.pop(f"notes_{sc.key}", [])
    return out


# ---- rendering -----------------------------------------------------------------------------

RENDER_DRIVER = r'''
import os, sys, traceback
import bpy
argv = sys.argv[sys.argv.index("--") + 1:]
repo, front, back, orig_dir = argv[0], argv[1], argv[2], argv[3]
sys.path.insert(0, os.path.join(repo, "blender"))
try:
    # model.blend points at MPFB textures by paths relative to where it was saved (//../..). This
    # copy lives elsewhere, so re-anchor them on the original version folder; a texture that is
    # still missing would render magenta, so it fails the render rather than mislead a reviewer.
    missing = []
    for im in bpy.data.images:
        if im.packed_file or not im.filepath or im.source != "FILE":
            continue
        if im.filepath.startswith("//"):
            im.filepath = os.path.normpath(os.path.join(orig_dir, im.filepath[2:]))
            im.reload()
        if not os.path.exists(bpy.path.abspath(im.filepath)):
            missing.append(im.name + " -> " + im.filepath)
    if missing:
        raise FileNotFoundError("textures not found (the render would show them magenta): " + "; ".join(missing))
    from crew import render
    arm = bpy.data.objects.get("Crew") or next(o for o in bpy.data.objects if o.type == "ARMATURE")
    action = "idle" if "idle" in bpy.data.actions else None
    frame = 20 if action else None
    render.render_preview(front, arm, action, frame=frame, yaw_deg=35.0)
    render.render_preview(back, arm, action, frame=frame, yaw_deg=215.0)
    print("ME3D_RENDER_OK")
except Exception:
    traceback.print_exc()
    sys.stdout.flush()
    sys.exit(1)
'''


def render_version(api: Api, pid: str, n: int, out: Path, prefix: str, blender: Path,
                   data_dir: Path) -> tuple[list[Path], str | None]:
    """Render front (three-quarter, yaw 35) and back (yaw 215) of version n from its model.blend,
    with the same blender/crew/render.py the builder's --render uses. The .blend is downloaded
    through the API; data_dir is the server's data folder, used only to re-anchor texture paths."""
    blend = api.download(f"/api/files/projects/{pid}/v{n}/model.blend", out / f"{prefix}_v{n}.blend")
    driver = out / "_render_driver.py"
    driver.write_text(RENDER_DRIVER, encoding="utf-8")
    front, back = out / f"{prefix}_front.png", out / f"{prefix}_back.png"
    for p in (front, back):
        p.unlink(missing_ok=True)
    cmd = [str(blender), "-b", str(blend), "--python", str(driver), "--", str(ROOT), str(front), str(back),
           str(data_dir / "projects" / pid / f"v{n}")]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired:
        return [], "Blender render timed out"
    # Blender exits 0 even when a script fails, so the marker and the files are the evidence.
    if "ME3D_RENDER_OK" not in proc.stdout or not all(p.is_file() and p.stat().st_size for p in (front, back)):
        tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-20:])
        return [], f"render failed (exit {proc.returncode}):\n{tail}"
    blend.unlink(missing_ok=True)
    return [front, back], None


# ---- output --------------------------------------------------------------------------------

def table(outcomes: list[Outcome]) -> str:
    rows = [("Scenario", "Expected", "Changed", "Build ok", "Version", "Seconds", "Result")]
    for o in outcomes:
        ok = "-" if o.build_ok is None else ("yes" if o.build_ok else "NO")
        rows.append((f"{o.key}. {o.title}", ",".join(o.expected) or "-", ",".join(o.changed) or "-", ok,
                     o.version or "-", f"{o.seconds:.0f}", o.verdict))
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    line = lambda r: "  ".join(c.ljust(w) for c, w in zip(r, widths))  # noqa: E731
    return "\n".join([line(rows[0]), "  ".join("-" * w for w in widths)] + [line(r) for r in rows[1:]])


def print_plan(base: str) -> None:
    print("Plan (a live run, in order):")
    print("  0. POST /api/projects {kind: crew, base: male_default, name: 'E2E crew modules <time>'} and wait "
          "for the first build (a fresh project; existing projects are untouched).")
    for sc in SCENARIOS:
        if sc.key == "d":
            req = "POST /spec {patch: {custom_items: <same list, rifle item place -> 'back'>}}"
        else:
            body = sc.body({}, Snap(0, {"clothes": {"suit": "male_worksuit01"}}, {}, {}))
            req = f"POST /{'spec' if sc.kind == 'spec' else 'chat'} {json.dumps(body)}"
        exp = sc.expect(Snap(0, {"clothes": {"suit": None}}, {}, {}))
        how = "exactly" if sc.exact else "only within"
        ai = "AI" if sc.kind == "chat" else "no AI"
        print(f"  {sc.key}. {sc.title} [{ai}]\n       {req}\n       expect {how} {cm.ordered(exp)}; "
              f"parts allowed to change: {sc.allowed_parts}")
    print("  then render baseline and final front/back PNGs from model.blend with blender/crew/render.py")
    print(f"  server: {base}; AI calls in a live run: {sum(s.kind == 'chat' for s in SCENARIOS)} chat turns")


def dry_run(api: Api, blender: Path) -> int:
    print(f"Dry run against {api.base} (no project is created, the AI is not called)\n")
    ok = True
    try:
        health = api.get("/api/health", timeout=5)
    except Exception as e:  # noqa: BLE001
        print(f"  server: DOWN ({e})")
        return 2
    print(f"  server: up; health {json.dumps(health)}")
    for k in ("blender", "mpfb", "ai"):
        if not health.get(k):
            print(f"  WARNING: server reports {k}=false; a live run will fail")
            ok = False
    catalog = api.get("/api/catalog")
    crew_ids = [c.get("id") for c in catalog.get("crew") or []]
    has_base = any(i in ("male_default", "crew_male_default") for i in crew_ids)
    print(f"  catalog crew: {crew_ids} -> male_default {'present' if has_base else 'MISSING'}")
    ok &= has_base
    projects = api.get("/api/projects")
    print(f"  existing projects: {len(projects)} (left alone)")
    print(f"  crew_modules imported from {cm.__file__}: {len(cm.MODULES)} modules")
    print(f"  blender for renders: {blender} ({'found' if blender.exists() else 'NOT FOUND'})")
    ok &= blender.exists()
    print(f"  render helper: {ROOT / 'blender/crew/render.py'} "
          f"({'found' if (ROOT / 'blender/crew/render.py').is_file() else 'NOT FOUND'})\n")
    print_plan(api.base)
    print(f"\nDry run {'OK' if ok else 'found problems (see WARNING / MISSING / NOT FOUND above)'}")
    return 0 if ok else 1


def live_run(api: Api, out: Path, blender: Path, data_dir: Path, timeout: float) -> int:
    out.mkdir(parents=True, exist_ok=True)
    print(f"Live run against {api.base}; output in {out}\n")
    health = api.get("/api/health", timeout=5)
    if not (health.get("blender") and health.get("mpfb") and health.get("ai")):
        print(f"Server is not ready for a live run: {health}")
        return 2

    name = f"E2E crew modules {time.strftime('%Y-%m-%d %H:%M:%S')}"
    t0 = time.time()
    created = api.post("/api/projects", {"kind": "crew", "base": "male_default", "name": name})
    pid = created["project"]["id"]
    print(f"Created test project {pid} ({name}); waiting for the first build ...", flush=True)
    job = api.wait_job(created["job_id"], timeout)
    base = snapshot(api, pid)
    if job["status"] != "done" or not base.report.get("ok"):
        print(f"First build failed: {job.get('error')}\n" + "\n".join((job.get("log") or [])[-20:]))
        return 1
    print(f"First build ok: v{base.version}, {len(base.parts)} parts, {time.time() - t0:.0f} s\n")

    state: dict = {}
    results: dict[str, Outcome] = {}
    for sc in SCENARIOS:
        try:
            o = run_scenario(api, pid, sc, state, results, timeout)
        except Exception as e:  # noqa: BLE001 - one broken scenario must not hide the others
            o = Outcome(sc.key, sc.title, failures=[f"script error: {type(e).__name__}: {e}"])
        results[sc.key] = o
        print(f"      -> {o.verdict} ({o.seconds:.0f} s) changed={o.changed}"
              + (f"\n         " + "\n         ".join(o.failures) if o.failures else ""), flush=True)

    final = snapshot(api, pid)
    renders, render_errors = [], []
    print("\nRendering baseline and final ...", flush=True)
    for n, prefix in ((base.version, "baseline"), (final.version, "final")):
        files, err = render_version(api, pid, n, out, prefix, blender, data_dir)
        renders += files
        if err:
            render_errors.append(f"{prefix} v{n}: {err}")

    outcomes = list(results.values())
    print("\n" + table(outcomes))
    for o in outcomes:
        if o.failures or o.notes or o.reply:
            print(f"\n[{o.key}] {o.title} - {o.verdict}")
            for f in o.failures:
                print(f"   FAIL  {f}")
            for n_ in o.notes:
                print(f"   note  {n_}")
            print(f"   parts {json.dumps(o.parts)}")
            if o.server_changed is not None:
                print(f"   server changed_modules: {o.server_changed}")
            if o.reply:
                print("   AI reply:\n      " + o.reply.replace("\n", "\n      "))
    print(f"\nTest project: {pid} (final v{final.version}); open it in the app to look at it.")
    for r in renders:
        print(f"Render: {r}")
    for e in render_errors:
        print(f"RENDER FAIL: {e}")
    (out / "results.json").write_text(json.dumps({
        "project": pid, "base": api.base, "baseline_version": base.version, "final_version": final.version,
        "renders": [str(r) for r in renders], "render_errors": render_errors,
        "scenarios": [dict(o.__dict__, verdict=o.verdict) for o in outcomes]}, indent=1, default=str),
        encoding="utf-8")
    print(f"Results: {out / 'results.json'}")
    failed = [o.key for o in outcomes if o.failures] + (["render"] if render_errors else [])
    print(f"\n{'ALL PASS' if not failed else 'FAILED: ' + ', '.join(failed)} ({time.time() - t0:.0f} s total)")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry", action="store_true", help="check the server and print the plan; no AI, no writes")
    mode.add_argument("--live", action="store_true", help="run every scenario against the real AI and Blender")
    ap.add_argument("--base", default="http://localhost:8787", help="server base URL")
    ap.add_argument("--out", type=Path, default=None,
                    help="folder for renders and results.json (default: data/e2e/crew_modules_<time>, git-ignored)")
    ap.add_argument("--blender", type=Path, default=Settings().blender, help="Blender executable for renders")
    ap.add_argument("--data-dir", type=Path, default=Settings().data_dir,
                    help="the server's data folder (only to find MPFB textures for the renders)")
    ap.add_argument("--job-timeout", type=float, default=1800, help="seconds to wait for each job")
    args = ap.parse_args(argv)
    api = Api(args.base)
    if args.dry:
        return dry_run(api, args.blender)
    out = args.out or ROOT / "data" / "e2e" / f"crew_modules_{time.strftime('%Y%m%d_%H%M%S')}"
    return live_run(api, out.resolve(), args.blender, args.data_dir.resolve(), args.job_timeout)


if __name__ == "__main__":
    sys.exit(main())
