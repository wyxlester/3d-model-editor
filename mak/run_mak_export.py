"""Turn an me3d.package/1's ship into a MAK VR-Forces 5.1.1 entity (contract §9b). No Blender.

    python mak/run_mak_export.py <package_dir> [--vrf <vrforces folder>] [--shared-data <SharedData folder>]
                                                [--no-mak-check]

Writes <package_dir>/mak/ (model, definitions, .entity, visual-import/) and
<package_dir>/mak_export_report.json:
    {"ok", "vrforces": "5.1.1", "name", "dis_type", "checks": [{"name","pass","detail"}],
     "pending": [str], "not_exported": [str]}
Exit code 0 only when "ok" is true. Nothing is written into the VR-Forces install (see install.py).
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from from_package import PackageError, prepare  # noqa: E402
from pipeline import run as pipeline_run  # noqa: E402
from pipeline.common import InputError, make_config  # noqa: E402

VRFORCES = "5.1.1"
REPORT = "mak_export_report.json"


def _report(package_dir: Path, report: dict) -> dict:
    (package_dir / REPORT).write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def export(package_dir: Path, cfg: dict, mak_check: bool = True) -> dict:
    package_dir = Path(package_dir)
    base = {"ok": False, "vrforces": VRFORCES, "name": None, "dis_type": None, "checks": [], "pending": [],
            "not_exported": []}
    out_dir = package_dir / "mak"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    with tempfile.TemporaryDirectory() as work:
        try:
            prepared = prepare(package_dir, Path(work))
        except PackageError as e:
            return _report(package_dir, dict(base, checks=[{"name": "package", "pass": False, "detail": str(e)}]))
        m = prepared.manifest
        base.update(name=m["name"], dis_type=m["dis_type"], pending=list(prepared.pending),
                    not_exported=list(prepared.not_exported))
        try:
            result = pipeline_run.run(prepared.model_dir, cfg=cfg, out_dir=out_dir, mak_check=mak_check, quiet=True)
        except (InputError, ValueError) as e:
            return _report(package_dir, dict(base, checks=[{"name": "settings", "pass": False, "detail": str(e)}]))

    checks = []
    for stage, findings in result["stages"].items():
        for f in findings:
            if f["level"] == "pending":
                base["pending"].append(f["message"])
            elif f["code"] != "joints-static":            # already listed part by part in `pending`
                checks.append({"name": f"{stage}:{f['code']}", "pass": f["level"] != "error", "detail": f["message"]})
    return _report(package_dir, dict(base, ok=bool(result["ok"]), checks=checks))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("package_dir", type=Path)
    ap.add_argument("--vrf", help="VR-Forces folder (default ME3D_VRF_DIR, else C:\\MAK\\vrforces5.1.1)")
    ap.add_argument("--shared-data", help="SharedData version folder (default ME3D_MAK_SHARED_DATA)")
    ap.add_argument("--no-mak-check", action="store_true", help="don't load the model with medfTool")
    args = ap.parse_args(argv)
    report = export(args.package_dir, make_config(args.vrf, args.shared_data), mak_check=not args.no_mak_check)
    for c in report["checks"]:
        print(f"{'PASS' if c['pass'] else 'FAIL'}  {c['name']}: {c['detail']}")
    for p in report["pending"]:
        print(f"PENDING  {p}")
    for n in report["not_exported"]:
        print(f"NOT EXPORTED  {n}")
    print(f"\n{'OK' if report['ok'] else 'FAILED'}  {args.package_dir / REPORT}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
