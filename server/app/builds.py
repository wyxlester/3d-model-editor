"""Turns a spec into a new project version by running the builder."""
from __future__ import annotations

import json

from . import articulation as art
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
        check_articulation(self.store.load(pid), out_dir, report)
        atomic_write_json(out_dir / "build_report.json", report)  # normalised copy
        self.store.record_version(pid, n, report, summary)
        log(built_line(n, report))
        for e in report.get("errors", []):
            log(f"error: {e}")
        for w in report.get("warnings", []):
            log(f"warning: {w}")
        return n, report


def check_articulation(project: dict, out_dir, report: dict) -> None:
    """Contract §3b, the check after every build: the built joints against the plan's moving components (and
    any joint built with add_chain). Findings go to report["articulation"]; the warnings are also added to
    report["warnings"], so the build AI, the progress feed and the Build screen all see them. Never fails a build."""
    if not report.get("ok"):
        return
    try:
        parts = json.loads((out_dir / "parts.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    chains = (project.get("design_plan") or {}).get("chains") or []
    if not chains and not art.instances_from_parts(parts):
        return
    findings = art.check(parts, chains)
    report["articulation"] = findings
    report["warnings"] = list(report.get("warnings") or []) + art.report_lines(findings)


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
