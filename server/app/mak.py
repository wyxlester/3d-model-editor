"""The server's side of the MAK VR-Forces export (contract §9b, §10).

The export itself runs out of process (`mak/run_mak_export.py`, like the Unreal harness); this module validates
`Project.mak`, drafts suggestions, finds a scene's latest export and installs it into an SMS, using the
standard-library code in `mak/`.
"""
from __future__ import annotations

import re
import subprocess
import sys
import threading
import zipfile
from pathlib import Path
from typing import Callable

from .config import Settings
from .jobs import JobError
from .store import read_json

MakRunner = Callable[[Path, Callable[[str], None]], dict]
DIS_TYPE = re.compile(r"^-?\d+(:-?\d+){6}$")
KEYS = {"dis_type", "donor_entity", "short_name", "parts"}
REPORT = "mak_export_report.json"


class MakRefused(ValueError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _mak_modules(settings: Settings):
    root = str(settings.repo_root / "mak")
    if root not in sys.path:
        sys.path.insert(0, root)
    import draft_mak  # noqa: E402
    import install  # noqa: E402
    from pipeline.common import make_config  # noqa: E402
    return draft_mak, install, make_config


def mak_config(settings: Settings) -> dict:
    return _mak_modules(settings)[2](settings.vrf_dir, settings.mak_shared_data)


def available(settings: Settings) -> bool:
    """VR-Forces and its medfTool found (contract §10, health.mak)."""
    return settings.medf_tool.exists() and (settings.vrf_dir / "data" / "simulationModelSets").is_dir()


def validate(body, parts: list, settings: Settings) -> dict:
    """`Project.mak` from a PATCH body, or MakRefused(400) with one sentence."""
    if not isinstance(body, dict):
        raise MakRefused(400, "Send the MAK settings as an object")
    extra = sorted(set(body) - KEYS)
    if extra:
        raise MakRefused(400, f"Unknown MAK setting: {', '.join(extra)}. Allowed: {', '.join(sorted(KEYS))}")
    dis = body.get("dis_type")
    if not isinstance(dis, str) or not DIS_TYPE.match(dis.strip()):
        raise MakRefused(400, "dis_type must be 7 integers separated by colons, e.g. 1:3:15:80:1:0:0")
    donor = body.get("donor_entity")
    if not isinstance(donor, str) or not donor.strip():
        raise MakRefused(400, "donor_entity must name a stock VR-Forces entity, e.g. Super_Tanker")
    vrfsim = settings.vrf_dir / "data" / "simulationModelSets" / "EntityLevel" / "vrfSim"
    if vrfsim.exists() and not (vrfsim / f"{donor}.entity").is_file():
        raise MakRefused(400, f"No entity called {donor} in the installed EntityLevel SMS")
    short = body.get("short_name")
    if short is not None and (not isinstance(short, str) or not 0 < len(short) <= 11):
        raise MakRefused(400, "short_name is the 11-character DIS marking: 1 to 11 characters, or null")
    codes = body.get("parts") or {}
    if not isinstance(codes, dict):
        raise MakRefused(400, "parts must map moving part names to DIS codes")
    moving = {p["path"] for p in parts if p.get("joint")}
    for part, code in codes.items():
        if part not in moving:
            raise MakRefused(400, f"{part} is not a moving part of this version")
        if code is not None and (isinstance(code, bool) or not isinstance(code, int) or not 0 < code < 65536):
            raise MakRefused(400, f"The DIS code for {part} must be an integer (1-65535) or null")
    return {"dis_type": dis.strip(), "donor_entity": donor.strip(), "short_name": short, "parts": dict(codes)}


def draft(parts: list, report: dict, name: str, settings: Settings) -> dict:
    draft_mak = _mak_modules(settings)[0]
    return draft_mak.draft(parts, report or {}, name, mak_config(settings))


def custom_sms(settings: Settings) -> list[str]:
    return _mak_modules(settings)[1].custom_sms(mak_config(settings))


def make_runner(settings: Settings) -> MakRunner:
    lock = threading.Lock()

    def run(package_dir: Path, log) -> dict:
        script = settings.mak_script
        if not script.exists():
            raise JobError(f"MAK export script missing: {script}")
        report_path = package_dir / REPORT
        if report_path.exists():
            report_path.unlink()
        with lock:
            log(f"$ {script.name} {package_dir.name}")
            try:
                proc = subprocess.run([sys.executable, str(script), str(package_dir), "--vrf", str(settings.vrf_dir),
                                       "--shared-data", str(settings.mak_shared_data)],
                                      capture_output=True, text=True, errors="replace",
                                      timeout=settings.mak_timeout_s, cwd=str(settings.repo_root))
            except subprocess.TimeoutExpired:
                raise JobError(f"MAK export timed out after {settings.mak_timeout_s:.0f} s.")
        for line in (proc.stdout + proc.stderr).splitlines()[-60:]:
            log(line)
        report = read_json(report_path)
        if not isinstance(report, dict) or "ok" not in report:
            raise JobError(f"The MAK export wrote no readable report (exit code {proc.returncode}).")
        return report
    return run


def zip_output(package_dir: Path, packages_dir: Path) -> str:
    """Zips <package>/mak; returns its /api/files URL."""
    out = package_dir / "mak"
    name = f"{package_dir.name}_mak.zip"
    with zipfile.ZipFile(packages_dir / name, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(out.rglob("*")):
            if f.is_file():
                z.write(f, Path(f"{package_dir.name}_mak") / f.relative_to(out))
        if (package_dir / REPORT).exists():
            z.write(package_dir / REPORT, Path(f"{package_dir.name}_mak") / REPORT)
    return f"/api/files/packages/{name}"


def latest_export(packages_dir: Path, scene_id: str, ship_version: int) -> Path | None:
    """The package of this scene's ship version whose MAK export passed, or None."""
    for pkg in sorted(packages_dir.glob(f"pkg_*_v{ship_version}_{scene_id}"), key=lambda p: p.stat().st_mtime,
                      reverse=True):
        report = read_json(pkg / REPORT)
        if isinstance(report, dict) and report.get("ok") and (pkg / "mak").is_dir():
            return pkg
    return None


def install(package_dir: Path, sms: str, settings: Settings) -> dict:
    inst = _mak_modules(settings)[1]
    try:
        return inst.install(package_dir / "mak", sms, mak_config(settings), settings.mak_backups_dir)
    except inst.InstallError as e:
        raise MakRefused(400, str(e))


def check_install_target(sms: str, settings: Settings) -> None:
    """The install refusals that can be answered before a job starts (400)."""
    inst = _mak_modules(settings)[1]
    if not isinstance(sms, str) or not sms:
        raise MakRefused(400, "Name the custom SMS to install into")
    if sms in inst.STOCK_SMS:
        raise MakRefused(400, f"{sms} is a stock SMS; install into a custom SMS made in the Simulation Object Editor")
    if sms not in custom_sms(settings):
        have = ", ".join(custom_sms(settings)) or "none"
        raise MakRefused(400, f"No custom SMS called {sms} (custom SMSs: {have})")
