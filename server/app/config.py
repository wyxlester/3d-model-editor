"""Paths and tool locations. Everything overridable by environment variable so tests and the
end-to-end run can point the server at a throwaway data folder."""
from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
IS_WINDOWS = sys.platform == "win32"

UNREAL_VERSION = (5, 8)


def engine_version(engine_dir: Path) -> tuple[int, int] | None:
    """(major, minor) from <engine>/Engine/Build/Build.version, or None if it is not an engine."""
    try:
        v = json.loads((Path(engine_dir) / "Engine/Build/Build.version").read_text(encoding="utf-8"))
        return int(v["MajorVersion"]), int(v["MinorVersion"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def pick_unreal(candidates, fallback: Path) -> Path:
    """The first candidate folder that is an Unreal 5.8 engine, else `fallback`."""
    for c in candidates:
        if c and engine_version(Path(c)) == UNREAL_VERSION:
            return Path(c)
    return fallback


def _registered_unreal() -> list[str]:
    """Where Windows says engines are: the Epic launcher's install of 5.8 (HKLM), then engines
    registered per user (HKCU ...\\Builds: a source build, or an installed engine registered by
    opening a project with it - how this repo's machines have it on D:)."""
    import winreg
    found = []
    for hive, key, value in ((winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\EpicGames\Unreal Engine\5.8", "InstalledDirectory"),
                             (winreg.HKEY_CURRENT_USER, r"Software\Epic Games\Unreal Engine\Builds", None)):
        try:
            with winreg.OpenKey(hive, key) as k:
                if value:
                    found.append(winreg.QueryValueEx(k, value)[0])
                else:
                    for i in range(winreg.QueryInfoKey(k)[1]):
                        found.append(winreg.EnumValue(k, i)[1])
        except OSError:
            continue
    return found


if IS_WINDOWS:
    DEFAULT_BLENDER = Path(r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe")
    BLENDER_CONFIG = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming")) / "Blender Foundation/Blender/5.2"
    DEFAULT_UNREAL = pick_unreal(_registered_unreal(), Path(r"C:\Program Files\Epic Games\UE_5.8"))
else:
    DEFAULT_BLENDER = Path("/Applications/Blender.app/Contents/MacOS/Blender")
    BLENDER_CONFIG = Path.home() / "Library/Application Support/Blender/5.2"
    DEFAULT_UNREAL = Path("/Users/Shared/Epic Games/UE_5.8")


def unreal_cmd(engine_dir: Path) -> Path:
    """The headless editor binary inside an engine folder."""
    if IS_WINDOWS:
        return Path(engine_dir) / "Engine/Binaries/Win64/UnrealEditor-Cmd.exe"
    return Path(engine_dir) / "Engine/Binaries/Mac/UnrealEditor-Cmd"


def unreal_editor(engine_dir: Path) -> Path:
    """The editor with its window, inside an engine folder."""
    if IS_WINDOWS:
        return Path(engine_dir) / "Engine/Binaries/Win64/UnrealEditor.exe"
    return Path(engine_dir) / "Engine/Binaries/Mac/UnrealEditor.app/Contents/MacOS/UnrealEditor"


def _env_path(name: str, default: Path) -> Path:
    v = os.environ.get(name)
    return Path(v).expanduser().resolve() if v else default


@dataclass
class Settings:
    repo_root: Path = REPO_ROOT
    data_dir: Path = field(default_factory=lambda: _env_path("ME3D_DATA_DIR", REPO_ROOT / "data"))
    catalog_dir: Path = field(default_factory=lambda: _env_path("ME3D_CATALOG_DIR", REPO_ROOT / "catalog"))
    blender: Path = field(default_factory=lambda: _env_path("ME3D_BLENDER", DEFAULT_BLENDER))
    mpfb_dir: Path = field(default_factory=lambda: _env_path(
        "ME3D_MPFB_DIR", BLENDER_CONFIG / "extensions/blender_org/mpfb"))
    unreal_dir: Path = field(default_factory=lambda: _env_path("ME3D_UNREAL_DIR", DEFAULT_UNREAL))
    build_timeout_s: float = float(os.environ.get("ME3D_BUILD_TIMEOUT_S", "900"))
    unreal_timeout_s: float = float(os.environ.get("ME3D_UNREAL_TIMEOUT_S", "3600"))
    agent_model: str = os.environ.get("ME3D_AGENT_MODEL", "claude-opus-5-5")
    route_model: str = os.environ.get("ME3D_ROUTE_MODEL", "claude-haiku-5-5")
    # After a research turn with too few photographs, run a photo-finding turn in the same job.
    photo_follow_up: bool = os.environ.get("ME3D_PHOTO_FOLLOW_UP", "1") != "0"
    # After a custom-ship research turn that changed the facts but left the design plan behind,
    # redraft the plan in the same job, so Approve is never left off for want of a click.
    plan_follow_up: bool = os.environ.get("ME3D_PLAN_FOLLOW_UP", "1") != "0"

    @property
    def projects_dir(self) -> Path:
        return self.data_dir / "projects"

    @property
    def scenes_dir(self) -> Path:
        return self.data_dir / "scenes"

    @property
    def packages_dir(self) -> Path:
        return self.data_dir / "packages"

    @property
    def trash_dir(self) -> Path:
        """Deleted projects: data/trash/<id>-<UTC timestamp>/, kept so they can be recovered by hand."""
        return self.data_dir / "trash"

    @property
    def unreal_script(self) -> Path:
        return self.repo_root / "unreal" / "run_import_test.py"

    @property
    def unreal_cmd(self) -> Path:
        return unreal_cmd(self.unreal_dir)

    @property
    def unreal_editor(self) -> Path:
        return unreal_editor(self.unreal_dir)

    @property
    def harness_project(self) -> Path:
        return self.repo_root / "unreal" / "HarnessProject" / "Harness.uproject"

    def builder_script(self, kind: str) -> Path:
        return self.repo_root / "blender" / ("build_ship.py" if kind == "ship" else "build_crew.py")


def find_claude_cli() -> str | None:
    found = shutil.which("claude")
    if found:
        return found
    for p in ("/opt/homebrew/bin/claude", "/usr/local/bin/claude", str(Path.home() / ".claude/local/claude"),
              str(Path.home() / ".local/bin/claude.exe")):
        if Path(p).exists():
            return p
    return None
