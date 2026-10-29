"""Open a level the Unreal test built in the Unreal editor, with its window: the second half of
the Review page's "Build and Open in Unreal" (contract §10, POST /api/unreal/open).

The test project is shared: every test rebuilds its level in place, so while an editor this server
started has the project open, a new test is refused (and so is a second editor). An editor opened
by hand on the same project is not seen.

Harness.uproject stays as committed ("EngineAssociation": "5.8", which every machine with a
launcher install of 5.8 resolves - the Mac). Whenever the editor rewrites it - its "Project file is
out of date" Update does, stamping the running engine's ID (on the Windows PC with 5.8 on D:, an ID
no other machine has) - that would point other machines at an engine they do not have. The
committed file lists plugins as 5.8 expects, so that prompt should not appear; as a safety net,
while the editor runs the committed bytes are put back.
"""
from __future__ import annotations

import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from .config import Settings

LEVEL = re.compile(r"^/Game/ME3D/([A-Za-z0-9_]+)/L_\1$")


def launch_command(editor: Path, project: Path, level: str, repo_root: Path):
    """The editor on `project`, opening `level`, then running unreal/open_test_level.py once.
    Unreal reads a value with spaces only as -Name="a b c" (the quote after the '='), so on
    Windows the command line is written out here; a list there would quote the whole argument.
    macOS passes arguments as they are and Unreal re-quotes them itself."""
    script = (repo_root / "unreal" / "open_test_level.py").as_posix()
    if sys.platform == "win32":
        return f'"{editor}" "{project}" {level} -ExecCmds="py {script} {level}"'
    return [str(editor), str(project), level, f"-ExecCmds=py {script} {level}"]


def detached() -> dict:
    """Its own process group on Windows, so stopping the server leaves Unreal open."""
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS}
    return {"start_new_session": True}


def keep_file(path: Path, original: bytes, proc, interval: float = 2.0) -> None:
    """Until `proc` has exited, put `original` back whenever `path` changes (see the module note),
    with one last look after it exits. Meant for a daemon thread."""
    while True:
        running = proc.poll() is None
        try:
            if path.read_bytes() != original:
                path.write_bytes(original)
        except OSError:
            pass   # the editor is writing it right now; next time round
        if not running:
            return
        time.sleep(interval)


class Refused(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class UnrealEditorWindow:
    def __init__(self, settings: Settings, popen: Callable[..., subprocess.Popen] = subprocess.Popen,
                 keep_interval: float = 2.0):
        self.settings = settings
        self.popen = popen
        self.keep_interval = keep_interval
        self.proc: subprocess.Popen | None = None
        self.level: str | None = None
        self.lock = threading.Lock()

    def is_open(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def open(self, level: str) -> dict:
        m = LEVEL.match(level or "")
        if not m:
            raise Refused(400, f"Not a level a Unreal test makes: {level!r}")
        project = self.settings.harness_project
        umap = project.parent / "Content" / "ME3D" / m.group(1) / f"L_{m.group(1)}.umap"
        if not umap.is_file():
            raise Refused(404, "That test level is not on disk any more. Build and Open in Unreal again.")
        editor = self.settings.unreal_editor
        if not editor.is_file():
            raise Refused(503, f"Unreal 5.8 not found at {self.settings.unreal_dir} (set ME3D_UNREAL_DIR).")
        with self.lock:
            if self.is_open():
                raise Refused(409, f"Unreal is already open on the test project, showing {self.level}. "
                                   "Close it to open another test level.")
            committed = project.read_bytes()
            self.proc = self.popen(launch_command(editor, project, level, self.settings.repo_root),
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, cwd=str(project.parent), **detached())
            self.level = level
            threading.Thread(target=keep_file, args=(project, committed, self.proc, self.keep_interval),
                             daemon=True, name="keep Harness.uproject").start()
        return {"opened": True, "level": level, "project": str(project)}

    def refuse_if_open(self) -> None:
        """For the Unreal test: it would rebuild the level the editor has open."""
        if self.is_open():
            raise Refused(409, f"Close Unreal first: it has the test project open ({self.level}), "
                               "and a new test rebuilds that level.")
