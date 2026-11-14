"""Check the converted FBX itself (not the source glb) by re-importing it in headless Blender.

Checks that conversion kept what VR-Forces needs:
  - every articulated part is present under its FBX_NodeNameMap name          (manual 21.1.2)
  - each child part is still beneath its parent part                          (manual 21.4)
  - each part pivot is where the glb said, and its rotation is neutral        (manual 21.4)
  - overall bounds match the glb (no lost geometry, axes or scale changed)    (manual 21.2)
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from .artparts import Part
from .common import Finding

SCRIPT = Path(__file__).resolve().parent / "blender_dump_fbx.py"


def dump_fbx(fbx: Path, blender: str) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "dump.json"
        res = subprocess.run([blender, "-b", "--factory-startup", "-P", str(SCRIPT), "--", str(fbx), str(out)],
                             capture_output=True, text=True, timeout=600)
        if res.returncode != 0 or not out.exists():
            tail = "\n".join((res.stdout + res.stderr).splitlines()[-20:])
            raise RuntimeError(f"FBX dump failed (exit {res.returncode}):\n{tail}")
        return json.loads(out.read_text(encoding="utf-8"))


def _ancestors(objs: dict, name: str) -> list[str]:
    out, cur = [], objs.get(name, {}).get("parent")
    while cur:
        out.append(cur)
        cur = objs.get(cur, {}).get("parent")
    return out


def compare(dump: dict, glb_report: dict, parts: list[Part], fmt: str = "fbx") -> list[Finding]:
    """Shared by both formats: `dump` comes from blender_dump_fbx.py (fbx) or openflight.as_dump (flt).
    Finding codes are prefixed with the format, e.g. fbx-pivot-moved / flt-pivot-moved."""
    f = _compare(dump, glb_report, parts)
    for x in f:
        x.code = fmt + x.code[3:] if x.code.startswith("fbx-") else x.code
        x.message = x.message.replace("the FBX", f"the {fmt.upper()}")
    return f


def _compare(dump: dict, glb_report: dict, parts: list[Part]) -> list[Finding]:
    f: list[Finding] = []
    objs = dump["objects"]
    by_node = {p.node: p for p in parts}
    longest = max(glb_report["bounds_mak_frame"]["extent"])
    tol = 0.01 + 0.005 * longest

    for p in parts:
        o = objs.get(p.fbx_name)
        if o is None:
            f.append(Finding("error", "fbx-part-missing", f"part {p.code} node '{p.fbx_name}' not found in the FBX"))
            continue
        if p.parent and by_node.get(p.parent) and by_node[p.parent].fbx_name not in _ancestors(objs, p.fbx_name):
            f.append(Finding("error", "fbx-hierarchy", f"'{p.fbx_name}' is not under '{by_node[p.parent].fbx_name}' in the FBX"))
        want = glb_report["nodes"].get(p.node, {}).get("pivot")
        if want:
            d = max(abs(a - b) for a, b in zip(o["pivot"], want))
            if d > tol:
                f.append(Finding("error", "fbx-pivot-moved", f"'{p.fbx_name}' pivot {[round(v, 3) for v in o['pivot']]} != glb {want}"))
        if o["local_rotation_deg"] > 0.5:
            f.append(Finding("error", "fbx-part-rotated", f"'{p.fbx_name}' has a {o['local_rotation_deg']:.1f} deg neutral rotation in the FBX"))

    boxes = [o["bounds"] for o in objs.values() if o["bounds"]]
    if not boxes:
        return f + [Finding("error", "fbx-no-geometry", "no meshes in the FBX")]
    mn = [min(b["min"][i] for b in boxes) for i in range(3)]
    mx = [max(b["max"][i] for b in boxes) for i in range(3)]
    g = glb_report["bounds_mak_frame"]
    d = max(max(abs(a - b) for a, b in zip(mn, g["min"])), max(abs(a - b) for a, b in zip(mx, g["max"])))
    if d > tol:
        f.append(Finding("error", "fbx-bounds-mismatch",
                         f"FBX bounds {[round(v, 3) for v in mn]}..{[round(v, 3) for v in mx]} != glb {g['min']}..{g['max']}"))
    if not f:
        f.append(Finding("info", "fbx-verified", f"{len(parts)} part(s) and bounds match the glb"))
    return f
