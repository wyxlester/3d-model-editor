"""Build a ship from a me3d.ship_spec/1 file (docs/CONTRACT.md sections 4 and 5).

    Blender -b --factory-startup --python blender/build_ship.py -- --spec <spec.json> --out <dir>

Order: builder for ``type`` (params merged over the catalog defaults) -> overrides -> joint_limits
-> custom_ops -> sockets follow their parts -> checks -> export. Any error lands in
build_report.json and the process exits 1, because Blender itself exits 0 on a script error.
"""

import argparse
import importlib
import json
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import common as me3d  # noqa: E402

sys.modules["me3d"] = me3d  # custom ops and builders can `import me3d`

SHIP_TYPES = ("container", "custom", "sampan", "tanker")


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser(prog="build_ship.py")
    p.add_argument("--spec", required=True)
    p.add_argument("--out", required=True)
    return p.parse_args(argv)


def merged_params(spec, report):
    """Catalog defaults are the single source of each builder's knobs."""
    cat_path = os.path.join(ROOT, "catalog", "ships", f"{spec['type']}.json")
    with open(cat_path, encoding="utf-8") as f:
        defaults = json.load(f)["params"]
    params = dict(defaults)
    for k, v in (spec.get("params") or {}).items():
        if k not in defaults:
            report.warn(f"unknown param {k!r} for {spec['type']} ignored")
            continue
        params[k] = v
    return params


def main():
    args = parse_args()
    report = me3d.REPORT
    out = os.path.abspath(args.out)
    module = None
    try:
        with open(args.spec, encoding="utf-8") as f:
            spec = json.load(f)
        if spec.get("schema") != "me3d.ship_spec/1":
            raise ValueError(f"schema must be me3d.ship_spec/1, got {spec.get('schema')!r}")
        if spec.get("type") not in SHIP_TYPES:
            raise ValueError(f"type must be one of {SHIP_TYPES}, got {spec.get('type')!r}")
        params = merged_params(spec, report)
        me3d.reset_scene()
        module = importlib.import_module(f"ships.{spec['type']}")
        module.build(params, me3d)
    except Exception as e:
        report.error(f"builder failed: {type(e).__name__}: {e}")
        traceback.print_exc()
        me3d.write_report(out, report)
        sys.exit(1)

    me3d.apply_overrides(spec, report)
    me3d.apply_joint_limits(spec, report)
    me3d.run_custom_ops(spec, report)
    try:
        me3d.update_sockets()
        me3d.check_scene(report, need_aft_masthead=float(params.get("loa_m", 0)) >= 50)
    except Exception as e:
        report.error(f"checks failed: {type(e).__name__}: {e}")
    result = me3d.export_outputs(out, report, budget=getattr(module, "TRI_BUDGET", None))
    print(f"[build_ship] ok={result['ok']} triangles={result['triangles']} "
          f"errors={result['errors']} seconds={result['seconds']}")
    sys.exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
