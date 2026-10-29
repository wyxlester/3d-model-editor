"""Turns a spec into a new project version by running the builder."""
from __future__ import annotations

from .builder import BuilderRunner, Log
from .specmerge import diff_keys
from .store import ProjectStore, atomic_write_json


class Builds:
    def __init__(self, store: ProjectStore, runner: BuilderRunner):
        self.store = store
        self.runner = runner

    def build(self, pid: str, spec: dict, summary: str, log: Log) -> tuple[int, dict]:
        kind = self.store.load(pid)["kind"]
        n, out_dir = self.store.reserve_version(pid)
        spec_path = out_dir / "spec.json"
        atomic_write_json(spec_path, spec)
        log(f"• Building version {n} in Blender: {summary}")
        report = self.runner.build(kind, spec_path, out_dir, log)
        atomic_write_json(out_dir / "build_report.json", report)  # normalised copy
        self.store.record_version(pid, n, report, summary)
        log(built_line(n, report))
        for e in report.get("errors", []):
            log(f"error: {e}")
        for w in report.get("warnings", []):
            log(f"warning: {w}")
        return n, report


def built_line(n: int, report: dict) -> str:
    """The progress-feed line for a finished build (contract §10: lines starting "• ")."""
    if report.get("ok"):
        return f"• Version {n} built"
    return f"• Version {n} failed to build: {'; '.join(report.get('errors') or ['unknown error'])}"


def summarise_change(old: dict, new: dict, fallback: str) -> str:
    keys = diff_keys(old, new)
    if not keys:
        return fallback
    shown = ", ".join(keys[:4]) + (f" and {len(keys) - 4} more" if len(keys) > 4 else "")
    return f"{fallback}: {shown}"
