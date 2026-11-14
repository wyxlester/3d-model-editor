"""glb -> a model file VR-Forces loads. MAK accepts OpenFlight or FBX, not glTF (manual 14.2).

Two routes, chosen by model_format (ME3D_MAK_FORMAT, default flt):
  flt (default)  pure Python OpenFlight writer (pipeline/openflight.py). No Blender. Articulated parts become
                 DOF nodes with "@dis articulated_part" comments inside the model (manual 21.3-21.4).
  fbx            headless Blender (pipeline/blender_glb_to_fbx.py). Part nodes are renamed to the names in
                 FBX_NodeNameMap.csv, because FBX parts are recognised only by node name (manual 21.1.2).

Optional later step: encrypt/compress to .medf with medfTool.exe (manual 19.19). Not wired in yet.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from . import artparts, glb as glbmod, openflight
from .common import InputError, check_model_dir, load_config, load_manifest, layout_for

SCRIPT = Path(__file__).resolve().parent / "blender_glb_to_fbx.py"


def convert(glb: Path, fbx: Path, forward_axis: str, blender: str, renames: dict[str, str] | None = None) -> None:
    """FBX route (Blender)."""
    fbx.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        cmd = [blender, "-b", "--factory-startup", "-P", str(SCRIPT), "--", str(glb), str(fbx), forward_axis]
        if renames:
            rj = Path(tmp) / "renames.json"
            rj.write_text(json.dumps(renames), encoding="utf-8")
            cmd.append(str(rj))
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if res.returncode != 0 or not fbx.exists():
        tail = "\n".join((res.stdout + res.stderr).splitlines()[-30:])
        raise RuntimeError(f"Blender conversion failed (exit {res.returncode}):\n{tail}")


def convert_flt(glb: Path, flt: Path, manifest: dict, parts: list[artparts.Part],
                lod_names: dict | None = None) -> openflight.WriteResult:
    """OpenFlight route (no Blender)."""
    model = glbmod.load(glb, manifest["forward_axis_in_blender"])
    return openflight.write_flt(model, flt, parts, manifest["name"], manifest.get("dis_type"), lod_names)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("model_dir", type=Path, help="models/<name>")
    args = ap.parse_args(argv)
    try:
        cfg, manifest = load_config(), load_manifest(check_model_dir(args.model_dir))
    except InputError as e:
        print(f"input error: {e}", file=sys.stderr)
        return 2
    fmt = cfg.get("model_format", "flt")
    lay = layout_for(manifest, fmt=fmt)
    if fmt == "flt":
        parts = artparts.resolve_parts(manifest, artparts.NodeNameMap())
        res = convert_flt(args.model_dir / "model.glb", lay.model_file, manifest, parts)
        for w in res.warnings:
            print(f"warning: {w}")
    else:
        parts = artparts.resolve_parts(manifest, artparts.load_node_map(cfg.get("fbx_node_name_map")))
        convert(args.model_dir / "model.glb", lay.model_file, manifest["forward_axis_in_blender"], cfg["blender"],
                artparts.rename_map(parts))
    print(f"wrote {lay.model_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
