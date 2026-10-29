"""Paths and tool locations. Everything overridable by environment variable so tests and the
end-to-end run can point the server at a throwaway data folder."""
from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    DEFAULT_BLENDER = Path(r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe")
    BLENDER_CONFIG = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming")) / "Blender Foundation/Blender/5.2"
    DEFAULT_UNREAL = Path(r"C:\Program Files\Epic Games\UE_5.8")
else:
    DEFAULT_BLENDER = Path("/Applications/Blender.app/Contents/MacOS/Blender")
    BLENDER_CONFIG = Path.home() / "Library/Application Support/Blender/5.2"
    DEFAULT_UNREAL = Path("/Users/Shared/Epic Games/UE_5.8")


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
        return self.repo_root / "unreal" / "run_import_test.sh"

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
