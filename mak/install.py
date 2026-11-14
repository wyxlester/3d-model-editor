"""Copy a MAK export (contract §9b) into a custom VR-Forces SMS. The only code that writes into VR-Forces.

    python mak/install.py <package_dir>/mak <sms name> [--vrf <dir>] [--shared-data <dir>] [--backups <dir>]

- `<mak>/SharedData/...`  -> the VR-Forces SharedData version folder (the model and its textures)
- `<mak>/sms/vrfSim/...`  -> `<vrforces>/data/simulationModelSets/<sms>/vrfSim/`
- `<mak>/sms/gui/...`     -> `<vrforces>/data/simulationModelSets/<sms>/gui/` (where the Simulation Object Editor
  keeps a custom SMS's visual definitions; Users Guide 64.4)
The SMS must already exist (make it in the Simulation Object Editor, including EntityLevel) and must not be a
stock one. Every file that would be replaced is first copied to `<backups>/<sms>-<UTC>/`, mirroring the layout.
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from pipeline.common import make_config  # noqa: E402

STOCK_SMS = {"EntityLevel", "AggregateLevel", "AggregateLevelBase", "base", "MAKTest"}
SMS_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_ .-]*$")


class InstallError(ValueError):
    pass


def custom_sms(cfg: dict) -> list[str]:
    """Custom SMSs in the install (stock ones left out)."""
    d = Path(cfg["sms_dir"])
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("*.sms") if p.stem not in STOCK_SMS and (d / p.stem).is_dir())


def _targets(mak_dir: Path, cfg: dict, sms: str) -> list[tuple[Path, Path, str]]:
    """(source, destination, backup-relative path) for every file to copy."""
    out = []
    shared_src = mak_dir / "SharedData"
    for src in sorted(p for p in shared_src.rglob("*") if p.is_file()):
        rel = src.relative_to(shared_src)
        out.append((src, Path(cfg["shared_data_dir"]) / rel, f"SharedData/{rel.as_posix()}"))
    sms_root = Path(cfg["sms_dir"]) / sms
    for sub in ("vrfSim", "gui"):
        base = mak_dir / "sms" / sub
        for src in sorted(p for p in base.rglob("*") if p.is_file()):
            rel = src.relative_to(base)
            out.append((src, sms_root / sub / rel, f"sms/{sub}/{rel.as_posix()}"))
    return out


def install(mak_dir: Path, sms: str, cfg: dict, backups_root: Path) -> dict:
    mak_dir = Path(mak_dir)
    if not SMS_NAME.match(sms or "") or ".." in sms:
        raise InstallError(f"{sms!r} is not an SMS name")
    if sms in STOCK_SMS:
        raise InstallError(f"{sms} is a stock SMS; install into a custom SMS made in the Simulation Object Editor")
    sms_dir = Path(cfg["sms_dir"])
    if not (sms_dir / f"{sms}.sms").is_file() or not (sms_dir / sms).is_dir():
        have = ", ".join(custom_sms(cfg)) or "none"
        raise InstallError(f"No custom SMS called {sms} in {sms_dir} (custom SMSs: {have})")
    if not list((mak_dir / "sms" / "vrfSim").glob("*.entity")):
        raise InstallError(f"{mak_dir} has no sms/vrfSim/*.entity; run the MAK export first")
    if not list((mak_dir / "SharedData").rglob("*.flt")) and not list((mak_dir / "SharedData").rglob("*.fbx")):
        raise InstallError(f"{mak_dir} has no model file under SharedData; run the MAK export first")

    targets = _targets(mak_dir, cfg, sms)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backups_dir = Path(backups_root) / f"{sms}-{stamp}"
    backed_up = 0
    for _, dst, rel in targets:
        if dst.exists():
            b = backups_dir / rel
            b.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dst, b)
            backed_up += 1
    written = []
    for src, dst, _ in targets:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        written.append(str(dst))
    return {"sms": sms, "written": written, "backups_dir": str(backups_dir) if backed_up else None}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mak_dir", type=Path)
    ap.add_argument("sms")
    ap.add_argument("--vrf")
    ap.add_argument("--shared-data")
    ap.add_argument("--backups", type=Path, default=Path("mak_backups"))
    args = ap.parse_args(argv)
    try:
        r = install(args.mak_dir, args.sms, make_config(args.vrf, args.shared_data), args.backups)
    except InstallError as e:
        print(f"install error: {e}", file=sys.stderr)
        return 2
    print(json.dumps(r, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
