"""Project store on disk (contract §11).

data/projects/<id>/project.json                 metadata, pins, messages, research, undo stack
data/projects/<id>/v<n>/{spec.json, model.glb, model.blend, parts.json, build_report.json}
data/projects/<id>/uploads/<upload id>.<ext> and .json   chat attachments
data/projects/<id>/research/photos/P01.<ext>, research/closeups/C01.png   research media (§12)

A build always makes a new version folder. A version becomes *current* when its build is ok
(or when the project has no current version yet, so a first failed build still leaves a spec to
fix). Undo moves the current pointer back; version folders are never deleted.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .design_plan import approve_refusal, on_research_saved
from .jobs import now_iso
from .research_media import MEDIA_KEYS

ID_RE = re.compile(r"^[a-z0-9_]+$")
UPLOAD_ID_RE = re.compile(r"^u_[a-f0-9]{8}$")


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


class NotFound(Exception):
    pass


class PlanRefused(Exception):
    """A design plan cannot be approved now; the message is a sentence for the user."""


class ProjectStore:
    def __init__(self, projects_dir: Path):
        self.root = projects_dir
        self.root.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()

    # ---- locking and raw access -------------------------------------------------------
    def lock(self, pid: str) -> threading.RLock:
        with self._locks_guard:
            return self._locks.setdefault(pid, threading.RLock())

    def _dir(self, pid: str) -> Path:
        if not ID_RE.match(pid or ""):
            raise NotFound(pid)
        return self.root / pid

    def exists(self, pid: str) -> bool:
        try:
            return (self._dir(pid) / "project.json").exists()
        except NotFound:
            return False

    def load(self, pid: str) -> dict:
        data = read_json(self._dir(pid) / "project.json")
        if data is None:
            raise NotFound(pid)
        return data

    def save(self, project: dict) -> None:
        project["updated"] = now_iso()
        atomic_write_json(self._dir(project["id"]) / "project.json", project)

    @contextmanager
    def edit(self, pid: str) -> Iterator[dict]:
        """Read-modify-write under the project's lock."""
        with self.lock(pid):
            project = self.load(pid)
            yield project
            self.save(project)

    # ---- projects ---------------------------------------------------------------------
    def create(self, kind: str, base: str, name: str, brief: str | None = None) -> dict:
        pid = "p_" + uuid.uuid4().hex[:8]
        project = {"id": pid, "kind": kind, "name": name, "base": base, "version": 0,
                   "versions": [], "undo_stack": [], "pins": [], "messages": [],
                   "research": None, "research_rev": 0, "design_plan": None,
                   "created": now_iso(), "updated": now_iso()}
        if brief:
            project["brief"] = brief
        self._dir(pid).mkdir(parents=True, exist_ok=False)
        self.save(project)
        return project

    def ids(self) -> list[str]:
        return sorted(p.parent.name for p in self.root.glob("*/project.json"))

    # ---- versions ---------------------------------------------------------------------
    def version_dir(self, pid: str, n: int) -> Path:
        return self._dir(pid) / f"v{int(n)}"

    def reserve_version(self, pid: str) -> tuple[int, Path]:
        with self.lock(pid):
            existing = [int(p.name[1:]) for p in self._dir(pid).glob("v*") if p.name[1:].isdigit()]
            n = max(existing, default=0) + 1
            d = self.version_dir(pid, n)
            d.mkdir(parents=True, exist_ok=False)
            return n, d

    def record_version(self, pid: str, n: int, report: dict, summary: str) -> dict:
        """Append version n to the history; make it current if it built (or nothing is)."""
        with self.edit(pid) as project:
            project["versions"].append({"n": n, "at": now_iso(), "summary": summary,
                                        "ok": bool(report.get("ok"))})
            if report.get("ok") or not project["version"]:
                if project["version"]:
                    project["undo_stack"].append(project["version"])
                project["version"] = n
        return project

    def spec(self, pid: str, n: int | None = None) -> dict:
        if n is None:
            n = self.load(pid)["version"]
        spec = read_json(self.version_dir(pid, n) / "spec.json")
        if spec is None:
            raise NotFound(f"{pid} v{n} spec")
        return spec

    def parts(self, pid: str, n: int) -> list:
        if not n:
            return []
        return read_json(self.version_dir(pid, n) / "parts.json", []) or []

    def report(self, pid: str, n: int) -> dict | None:
        if not n:
            return None
        return read_json(self.version_dir(pid, n) / "build_report.json")

    def undo(self, pid: str) -> dict:
        with self.edit(pid) as project:
            if project["undo_stack"]:
                project["version"] = project["undo_stack"].pop()
        return project

    # ---- API shapes -------------------------------------------------------------------
    def api_project(self, pid: str) -> dict:
        p = self.load(pid)
        n = p["version"]
        spec = None
        if n:
            try:
                spec = self.spec(pid, n)
            except NotFound:
                spec = None
        if spec is None:  # first build still running: show the pending spec
            spec = read_json(self.version_dir(pid, 1) / "spec.json", {}) if (self._dir(pid) / "v1").exists() else {}
        return {
            "id": p["id"], "kind": p["kind"], "name": p["name"], "base": p["base"],
            "version": n, "spec": spec,
            "versions": p["versions"],
            "model_url": f"/api/projects/{pid}/versions/{n}/model.glb" if n else None,
            "parts": self.parts(pid, n),
            "build_report": self.report(pid, n) or {},
            "pins": p["pins"], "messages": p["messages"], "research": p["research"],
            "research_rev": p.get("research_rev", 0), "design_plan": p.get("design_plan"),
            "brief": p.get("brief"), "plan_built": bool(p.get("plan_built")),
            "can_undo": bool(p["undo_stack"]),
        }

    def api_summary(self, pid: str) -> dict:
        p = self.load(pid)
        n = p["version"]
        return {"id": p["id"], "kind": p["kind"], "name": p["name"], "base": p["base"],
                "version": n, "updated": p.get("updated"),
                "model_url": f"/api/projects/{pid}/versions/{n}/model.glb" if n else None,
                "open_pins": sum(1 for pin in p["pins"] if pin["status"] != "resolved")}

    # ---- messages, pins, research -----------------------------------------------------
    def add_message(self, pid: str, who: str, text: str, attachments: list[dict] | None = None) -> dict:
        msg = {"who": who, "text": text, "at": now_iso()}
        if attachments:
            msg["attachments"] = attachments
        with self.edit(pid) as project:
            project["messages"].append(msg)
        return msg

    # ---- uploads (chat attachments) ---------------------------------------------------
    def add_upload(self, pid: str, name: str, mime: str, ext: str, raw: bytes) -> dict:
        uid = "u_" + uuid.uuid4().hex[:8]
        d = self._dir(pid) / "uploads"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{uid}{ext}").write_bytes(raw)
        meta = {"id": uid, "name": name, "type": mime, "size": len(raw),
                "url": f"/api/files/projects/{pid}/uploads/{uid}{ext}"}
        atomic_write_json(d / f"{uid}.json", meta)
        return meta

    @property
    def inbox(self) -> Path:
        """Files attached on Home, before there is a project: data/uploads/."""
        return self.root.parent / "uploads"

    def add_inbox_upload(self, name: str, mime: str, ext: str, raw: bytes) -> dict:
        uid = "u_" + uuid.uuid4().hex[:8]
        self.inbox.mkdir(parents=True, exist_ok=True)
        (self.inbox / f"{uid}{ext}").write_bytes(raw)
        meta = {"id": uid, "name": name, "type": mime, "size": len(raw), "url": f"/api/files/uploads/{uid}{ext}"}
        atomic_write_json(self.inbox / f"{uid}.json", meta)
        return meta

    def get_upload(self, pid: str, uid: str) -> tuple[dict, bytes]:
        """An upload of this project; one attached on Home is copied in on first use."""
        if not UPLOAD_ID_RE.match(uid):
            raise NotFound(uid)
        d = self._dir(pid) / "uploads"
        meta = read_json(d / f"{uid}.json")
        if meta is None:
            held = read_json(self.inbox / f"{uid}.json")
            if held is None:
                raise NotFound(uid)
            fname = Path(held["url"]).name
            d.mkdir(parents=True, exist_ok=True)
            (d / fname).write_bytes((self.inbox / fname).read_bytes())
            meta = dict(held, url=f"/api/files/projects/{pid}/uploads/{fname}")
            atomic_write_json(d / f"{uid}.json", meta)
        return meta, (d / Path(meta["url"]).name).read_bytes()

    def add_pin(self, pid: str, part: str, point: list, text: str, author: str) -> dict:
        with self.edit(pid) as project:
            pin = {"id": f"pin_{len(project['pins']) + 1}_{uuid.uuid4().hex[:4]}", "part": part,
                   "point": point, "status": "working", "author": author,
                   "messages": [{"who": author, "text": text, "at": now_iso()}],
                   "before_version": project["version"], "after_version": None}
            project["pins"].append(pin)
        return pin

    def get_pin(self, pid: str, pin_id: str) -> dict:
        for pin in self.load(pid)["pins"]:
            if pin["id"] == pin_id:
                return pin
        raise NotFound(pin_id)

    def update_pin(self, pid: str, pin_id: str, *, status: str | None = None,
                   message: tuple[str, str] | None = None, **fields: Any) -> dict:
        with self.edit(pid) as project:
            for pin in project["pins"]:
                if pin["id"] == pin_id:
                    if status:
                        pin["status"] = status
                    if message:
                        pin["messages"].append({"who": message[0], "text": message[1], "at": now_iso()})
                    pin.update(fields)
                    return pin
        raise NotFound(pin_id)

    def set_research(self, pid: str, research: dict) -> int:
        """Save the research; returns the new research revision (an approved plan goes stale).
        Photographs, close-ups and coverage are kept from the stored record whatever the new one
        says: their own tools manage them, and a resave of the text must not drop the files."""
        with self.edit(pid) as project:
            old = project.get("research") or {}
            research = {k: v for k, v in research.items() if k not in MEDIA_KEYS}
            research.update({k: old[k] for k in MEDIA_KEYS if k in old})
            project["research"] = research
            on_research_saved(project)
            return project["research_rev"]

    def research_dir(self, pid: str) -> Path:
        """data/projects/<id>/research/{photos,closeups}/: files the research tools store."""
        return self._dir(pid) / "research"

    def set_design_plan(self, pid: str, summary: list[str], spec: dict) -> dict:
        """A new draft, stamped with the research revision it was drafted against."""
        with self.edit(pid) as project:
            plan = {"status": "draft", "summary": summary, "spec": spec,
                    "research_rev": int(project.get("research_rev") or 0), "at": now_iso()}
            project["design_plan"] = plan
        return plan

    def approve_design_plan(self, pid: str) -> dict:
        """Mark the plan approved, or raise PlanRefused with the sentence for the user."""
        with self.edit(pid) as project:
            why = approve_refusal(project)
            if why:
                raise PlanRefused(why)
            plan = project["design_plan"]
            plan["status"] = "approved"
            plan["approved_at"] = now_iso()
            plan.pop("build_error", None)
        return plan

    def mark_plan_built(self, pid: str) -> None:
        """An approved plan has been built once: building is open from now on (see needs_plan)."""
        with self.edit(pid) as project:
            project["plan_built"] = True

    def unapprove_design_plan(self, pid: str, error: str) -> None:
        """The approved plan failed to build: back to draft so it can be approved again."""
        with self.edit(pid) as project:
            plan = project.get("design_plan")
            if isinstance(plan, dict) and plan.get("status") == "approved":
                plan["status"] = "draft"
                plan.pop("approved_at", None)
                plan["build_error"] = error
