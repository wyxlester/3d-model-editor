"""Full pipeline for one model: inspect -> convert -> verify model -> generate -> validate -> report.

    python -m pipeline.run models/<name> [--skip-convert] [--format flt|fbx] [--no-mak-check]

model_format (ME3D_MAK_FORMAT, or --format):
  flt  OpenFlight, written in pure Python. No Blender. Verified by re-reading the file, and by loading it with
       MAK's own reader (medfTool --writeNodeInfo) when VR-Forces is installed.
  fbx  FBX via headless Blender, verified by re-importing it in Blender.

Exit code 0 = no errors (pending items are allowed), 1 = errors, 2 = bad input.
The report is written to output/<name>/report.json.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import artparts, convert, generate, inspect_glb, openflight, validate, verify_fbx
from . import glb as glbmod
from .common import (Finding, InputError, Layout, check_model_dir, has_errors, layout_for, load_config,
                     load_manifest, print_findings)


def _verify_flt(lay, report, parts, manifest, cfg, mak_check: bool, model_dir: Path) -> list[Finding]:
    flt = openflight.read_flt(lay.model_file)
    f = verify_fbx.compare(openflight.as_dump(flt), report, parts, fmt="flt")
    colours = openflight.expected_colours(glbmod.load(model_dir / "model.glb", manifest["forward_axis_in_blender"]))
    f += openflight.check_flt(flt, parts, manifest.get("dis_type"), colours)
    tool = cfg.get("medf_tool")
    if not mak_check:
        f.append(Finding("info", "mak-check-skipped", "--no-mak-check given"))
    elif tool and Path(tool).exists():
        try:
            f += openflight.check_mak(openflight.mak_node_info(lay.model_file, tool), parts, manifest.get("dis_type"))
        except Exception as e:  # noqa: BLE001
            f.append(Finding("error", "mak-load-failed", str(e)))
    else:
        f.append(Finding("warning", "mak-check-unavailable", f"medfTool not found at {tool}; MAK-side load not checked"))
    return f


def run(model_dir: Path, skip_convert: bool = False, output_root: Path | None = None,
        node_map: artparts.NodeNameMap | None = None, fmt: str | None = None, mak_check: bool = True,
        cfg: dict | None = None, out_dir: Path | None = None, quiet: bool = False) -> dict:
    """`out_dir`, when given, is the exact output folder (otherwise <output_root>/<name>)."""
    cfg = cfg or load_config()
    fmt = fmt or cfg.get("model_format", "flt")
    manifest = load_manifest(model_dir)
    lay = layout_for(manifest, output_root, fmt)
    if out_dir is not None:
        lay = Layout(Path(out_dir), lay.name, lay.category, fmt)
    lay.root.mkdir(parents=True, exist_ok=True)
    stages: dict[str, list[Finding]] = {}
    if fmt == "fbx":
        if node_map is None:
            node_map = artparts.load_node_map(cfg.get("fbx_node_name_map"))
    else:
        node_map = artparts.NodeNameMap()     # OpenFlight: names are free, parts are marked by @dis comments
    parts = artparts.resolve_parts(manifest, node_map)

    glb = model_dir / "model.glb"
    report = inspect_glb.analyse(glb, manifest["forward_axis_in_blender"])
    if manifest["domain"] == "surface":     # support points on the waterline (glb.waterline_supports)
        report["waterline_supports"] = glbmod.waterline_supports(glbmod.load(glb, manifest["forward_axis_in_blender"]))
    stages["inspect"] = inspect_glb.check(report, manifest, cfg["limits"], node_map)
    if fmt == "fbx" and not node_map.parts:
        stages["inspect"].append(Finding("warning", "no-node-map", f"FBX_NodeNameMap.csv not found at {node_map.source}; part names unchecked"))
    for old, new in artparts.rename_map(parts).items():
        stages["inspect"].append(Finding("info", "artpart-rename", f"'{old}' will be written to the FBX as '{new}' (FBX_NodeNameMap.csv)"))
    unmapped = manifest.get("unmapped_joints") or []
    if unmapped:
        stages["inspect"].append(Finding("info", "joints-static",
                                         f"{len(unmapped)} Blender joint(s) have no DIS part code and stay static: "
                                         + ", ".join(j["node"] for j in unmapped[:6]) + (" ..." if len(unmapped) > 6 else "")))

    if not has_errors(stages["inspect"]):
        if skip_convert:
            stages["convert"] = [Finding("warning", "convert-skipped", "--skip-convert given")]
        else:
            for other in ("flt", "fbx"):        # drop a model file left by a run in the other format
                stale = lay.model_dir / f"{lay.name}.{other}"
                if other != fmt and stale.exists():
                    stale.unlink()
            try:
                if fmt == "flt":
                    res = convert.convert_flt(glb, lay.model_file, manifest, parts)
                    stages["convert"] = [Finding("info", "wrote-flt", f"{lay.model_file} ({res.faces} faces, "
                                                 f"{len(res.textures)} texture(s))")]
                    stages["convert"] += [Finding("warning", "convert-warning", w) for w in res.warnings]
                else:
                    convert.convert(glb, lay.model_file, manifest["forward_axis_in_blender"], cfg["blender"],
                                    artparts.rename_map(parts))
                    stages["convert"] = [Finding("info", "wrote-fbx", str(lay.model_file))]
            except Exception as e:  # noqa: BLE001 - report, don't crash
                stages["convert"] = [Finding("error", "convert-failed", str(e))]
            if lay.model_file.exists() and not has_errors(stages["convert"]):
                try:
                    if fmt == "flt":
                        stages["verify_model"] = _verify_flt(lay, report, parts, manifest, cfg, mak_check, model_dir)
                    else:
                        stages["verify_model"] = verify_fbx.compare(verify_fbx.dump_fbx(lay.model_file, cfg["blender"]), report, parts)
                except Exception as e:  # noqa: BLE001
                    stages["verify_model"] = [Finding("error", "verify-failed", str(e))]
        stages["generate"] = generate.generate(manifest, lay, report, cfg, node_map)
        stages["validate"] = validate.validate(lay.root, cfg)

    result = {
        "model": manifest["name"],
        "model_format": fmt,
        "glb": report,
        "stages": {k: [x.to_dict() for x in v] for k, v in stages.items()},
        "ok": not any(has_errors(v) for v in stages.values()),
        "pending": [x.code for v in stages.values() for x in v if x.level == "pending"],
    }
    lay.report.write_text(json.dumps(result, indent=2), encoding="utf-8")
    if not quiet:
        for name, findings in stages.items():
            print_findings(name, findings)
        print(f"\n{'OK' if result['ok'] else 'FAILED'}  report: {lay.report}")
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("model_dir", type=Path)
    ap.add_argument("--skip-convert", action="store_true")
    ap.add_argument("--format", choices=["flt", "fbx"], help="override ME3D_MAK_FORMAT")
    ap.add_argument("--no-mak-check", action="store_true", help="flt: don't load the result with medfTool")
    args = ap.parse_args(argv)
    try:
        result = run(check_model_dir(args.model_dir), args.skip_convert, fmt=args.format, mak_check=not args.no_mak_check)
    except (InputError, ValueError, FileNotFoundError) as e:
        print(f"input error: {e}", file=sys.stderr)
        return 2
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
