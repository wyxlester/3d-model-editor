"""Is the running server older than the code on disk? (contract §10, health.code)

The server loads its Python once, at start, and `start.ps1` runs it without auto-reload. On 15 Nov a backend
started on 14 Nov kept drafting MAK settings with two-day-old rules while the export (a fresh process) ran the
new code. The weapon mounts were saved without part codes, and the export came out with no weapons. Nothing
said the server was stale.

This records the code it started with (each watched file's modification time and size, cheap to re-read)
and reports which files have changed since. The web app shows a restart banner when any have. Auto-reload
is not used: it would kill a Blender build or an AI turn half-way.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

# What the running server imports or hands to its own subprocesses' drafts: its own code, the MAK pipeline
# (imported in-process for the settings draft), the Blender helpers and the articulation roles.
WATCH = ("server/app", "mak", "blender", "catalog/articulation")
SUFFIXES = {".py", ".json"}
SKIP = {"__pycache__", "tests", "node_modules", ".venv"}
CACHE_S = 5.0


def snapshot(root: Path) -> dict[str, tuple[int, int]]:
    """{relative path: (mtime_ns, size)} for every watched file."""
    out: dict[str, tuple[int, int]] = {}
    for top in WATCH:
        base = Path(root) / top
        if not base.is_dir():
            continue
        for f in base.rglob("*"):
            if f.suffix not in SUFFIXES or SKIP & set(f.relative_to(root).parts) or not f.is_file():
                continue
            st = f.stat()
            out[f.relative_to(root).as_posix()] = (st.st_mtime_ns, st.st_size)
    return out


class CodeVersion:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.started = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        self.at_start = snapshot(self.root)
        self._cache: tuple[float, dict] | None = None

    def changed(self) -> list[str]:
        now = snapshot(self.root)
        return sorted(k for k in set(now) | set(self.at_start) if now.get(k) != self.at_start.get(k))

    def status(self) -> dict:
        """{"stale", "started", "changed": first few paths, "changed_count"}; re-read at most every 5 s."""
        if self._cache and time.monotonic() - self._cache[0] < CACHE_S:
            return self._cache[1]
        changed = self.changed()
        out = {"stale": bool(changed), "started": self.started, "changed": changed[:5], "changed_count": len(changed)}
        self._cache = (time.monotonic(), out)
        return out

    def warning(self) -> str | None:
        """One sentence for a report or log when stale, else None."""
        s = self.status()
        if not s["stale"]:
            return None
        more = f" and {s['changed_count'] - len(s['changed'])} more" if s["changed_count"] > len(s["changed"]) else ""
        return (f"The editor's server has been running since {s['started']} and its code has changed since "
                f"({', '.join(s['changed'])}{more}). Drafts and checks it makes use the old code: restart the editor "
                "(start.ps1), then re-save Review > MAK.")
