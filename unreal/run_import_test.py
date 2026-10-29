"""Import an me3d.package/1 into the throwaway UE 5.8 harness project and check it (Windows or Mac).

    python unreal/run_import_test.py <package_dir> [--engine <UE_5.8 folder>]

Writes <package_dir>/unreal_import_report.json (docs/CONTRACT.md section 9) and one log per Unreal
process: unreal_import.log (ship), unreal_crew_<id>.log (each crew member), unreal_verify.log.

Each import gets its own Unreal process. In a headless session, once one Import Into Level has
run, any later Interchange import starts and never finishes (5.8; wait_until_all_tasks_done
returns anyway and nothing is created), so: the ship, then each distinct crew member, each in a
fresh process on the saved level, then a last process that reloads the level and checks it.

Exit code: 0 only when the report says "ok": true. Unreal's own exit code is ignored on purpose -
UnrealEditor-Cmd exits 0 even when the Python inside it fails
(library: unreal-automation-runner-exits-zero-on-failed-tests).

--engine defaults to ME3D_UNREAL_DIR, else wherever the server finds Unreal 5.8 (server/app/config.py).
ME3D_UE_TIMEOUT: seconds per Unreal process (900).
"""
import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE / "HarnessProject" / "Harness.uproject"
SCRIPT = HERE / "import_package.py"
sys.path.insert(0, str(HERE.parent / "server"))
from app.config import DEFAULT_UNREAL, unreal_cmd  # noqa: E402


def phases(manifest):
    """[(phase, crew id or None, log name)] in the order they run."""
    out = [("import", None, "unreal_import.log")]
    seen = []
    for c in manifest.get("crew") or []:
        if isinstance(c, dict) and c.get("id") not in seen and re.fullmatch(r"[A-Za-z0-9_]+", str(c.get("id"))):
            seen.append(c["id"])
            out.append(("crew", c["id"], f"unreal_crew_{c['id']}.log"))
    out.append(("verify", None, "unreal_verify.log"))
    return out


def run_phase(cmd, pkg, phase, crew_id, log, import_logs, timeout):
    env = dict(os.environ, ME3D_PACKAGE=str(pkg), ME3D_PHASE=phase, ME3D_CREW_ID=crew_id or "",
               ME3D_IMPORT_LOG=os.pathsep.join(str(p) for p in import_logs))
    args = [str(cmd), str(PROJECT), "-run=pythonscript", f"-script={SCRIPT}", "-unattended", "-nullrhi",
            "-nosplash", "-NoSound", "-nop4", "-SCCProvider=None", f"-abslog={log}"]
    if platform.system() == "Darwin" and platform.machine() != "arm64":
        # a Rosetta parent (e.g. an x86 Python) would start the x86 slice
        # (library: x86-python-launches-an-x86-unreal...)
        args = ["/usr/bin/arch", "-arm64", *args]
    start = time.monotonic()
    try:
        rc = subprocess.run(args, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            timeout=timeout).returncode
    except subprocess.TimeoutExpired:
        rc = f"killed after {timeout:.0f} s"
    label = f"{phase} {crew_id}" if crew_id else phase
    print(f"  {label}: {time.monotonic() - start:.0f} s (Unreal exit {rc}, ignored)", flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("package_dir")
    ap.add_argument("--engine", default=os.environ.get("ME3D_UNREAL_DIR") or str(DEFAULT_UNREAL))
    a = ap.parse_args(argv)
    pkg = Path(a.package_dir).resolve()
    if not (pkg / "manifest.json").is_file():
        print(f"usage: {Path(__file__).name} <package_dir>   (the folder holding manifest.json)", file=sys.stderr)
        return 2
    cmd = unreal_cmd(Path(a.engine))
    if not cmd.is_file():
        print(f"Unreal not found at {cmd}", file=sys.stderr)
        return 3
    timeout = float(os.environ.get("ME3D_UE_TIMEOUT", "900"))
    report = pkg / "unreal_import_report.json"
    manifest = json.loads((pkg / "manifest.json").read_text(encoding="utf-8"))

    # Start from nothing: a stale report or stale assets must never pass for this run's result.
    for stale in [report, pkg / ".unreal_import_state.json", *pkg.glob("unreal_*.log")]:
        stale.unlink(missing_ok=True)
    pkg_id = str(manifest.get("id", ""))
    if re.fullmatch(r"[A-Za-z0-9_]+", pkg_id):   # anything else could escape Content/ME3D
        shutil.rmtree(HERE / "HarnessProject" / "Content" / "ME3D" / pkg_id, ignore_errors=True)

    print(f"Unreal import test: {pkg}", flush=True)
    import_logs = []
    for phase, crew_id, log_name in phases(manifest):
        log = pkg / log_name
        run_phase(cmd, pkg, phase, crew_id, log, import_logs, timeout)
        if phase != "verify":
            import_logs.append(log)
    (pkg / ".unreal_import_state.json").unlink(missing_ok=True)

    if not report.is_file():
        print(f"FAIL: no report written - see {pkg / 'unreal_verify.log'}", file=sys.stderr)
        return 1
    r = json.loads(report.read_text(encoding="utf-8"))
    checks = r.get("checks", [])
    bad = [c for c in checks if not c.get("pass")]
    print(f"{'PASS' if r.get('ok') else 'FAIL'}: {len(checks) - len(bad)}/{len(checks)} checks passed, "
          f"level {r.get('level')}, engine {r.get('engine_version', r.get('engine'))}")
    for c in checks:
        print(f"  [{'ok' if c.get('pass') else 'XX'}] {c['name']}: {c.get('detail', '')}")
    return 0 if r.get("ok") is True and checks and not bad else 1


if __name__ == "__main__":
    sys.exit(main())
