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

import copy
import json
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .design_plan import approve_refusal, on_research_saved
from .jobs import now_iso
from .research_media import MEDIA_KEYS

ID_RE = re.compile(r"^[a-z0-9_]+$")
UPLOAD_ID_RE = re.compile(r"^u_[a-f0-9]{8}$")


def _replace(tmp: str, path: Path, tries: int = 50) -> None:
    """os.replace, retried briefly on Windows, where it fails while another thread has the target
    open (a request reading project.json as a job saves it). The read ends within milliseconds."""
    for attempt in range(tries):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == tries - 1:
                raise
            time.sleep(0.01)


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        _replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def read_json(path: Path, default: Any = None, tries: int = 50) -> Any:
    for attempt in range(tries):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return default
        except PermissionError:  # Windows: the file is being replaced this instant (see _replace)
            if attempt == tries - 1:
                raise
            time.sleep(0.01)


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
    def create(self, kind: str, base: str, name: str, *, derived_from: dict | None = None,
               brief: str | None = None, research: dict | None = None, draft: dict | None = None,
               category: str | None = None) -> dict:
        """`brief` is the request the project was made from: a custom ship's description, or what a
        variant was asked to change (one field, Lester 29 Oct). `category` is where it is filed."""
        pid = "p_" + uuid.uuid4().hex[:8]
        project = {"id": pid, "kind": kind, "name": name, "base": base, "version": 0,
                   "versions": [], "undo_stack": [], "pins": [], "messages": [],
                   "research": copy.deepcopy(research), "research_rev": 0, "design_plan": None,
                   "derived_from": derived_from, "brief": brief,
                   "draft": draft, "discarded": False, "category": category,
                   "created": now_iso(), "updated": now_iso()}
        self._dir(pid).mkdir(parents=True, exist_ok=False)
        self.save(project)
        return project

    def create_variant(self, source: dict, version: int, name: str, brief: str | None, draft: dict) -> dict:
        """A new project of the source's kind and base, recording where it came from, held as a
        draft until approved. The caller writes its v1 spec. Research comes along; pins, chat and
        undo history do not."""
        return self.create(source["kind"], source["base"], name, brief=brief, research=source.get("research"),
                           derived_from={"project": source["id"], "version": version, "name": source["name"]},
                           draft=draft, category=source.get("category"))

    def set_draft(self, pid: str, **fields: Any) -> dict:
        """Update a variant's review state (stage, error, job_id…)."""
        with self.edit(pid) as project:
            project["draft"] = {**(project.get("draft") or {}), **fields}
        return project["draft"]

    def spec_ids(self) -> set[str]:
        """Every spec id any version of any project has used (contract §5: variant ids avoid them)."""
        ids = set()
        for f in self.root.glob("*/v*/spec.json"):
            spec = read_json(f)
            if isinstance(spec, dict) and isinstance(spec.get("id"), str):
                ids.add(spec["id"])
        return ids

    def ids(self) -> list[str]:
        return sorted(p.parent.name for p in self.root.glob("*/project.json"))

    def trash(self, pid: str, trash_dir: Path) -> Path:
        """Delete a project by moving its whole folder to trash_dir/<id>-<UTC timestamp>/ (Lester,
        29 Oct: delete, with the files kept so it can be recovered by hand). It is then gone from
        every list and lookup, which all read data/projects. Returns the folder it was moved to."""
        src = self._dir(pid)
        with self.lock(pid):
            if not (src / "project.json").exists():
                raise NotFound(pid)
            trash_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            dest, n = trash_dir / f"{pid}-{stamp}", 2
            while dest.exists():  # deleted twice in one second (a recovered copy deleted again)
                dest, n = trash_dir / f"{pid}-{stamp}-{n}", n + 1
            shutil.move(str(src), str(dest))
        return dest

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
            "excluded_sources": list(p.get("excluded_sources") or []),
            "plan_built": bool(p.get("plan_built")),
            "derived_from": p.get("derived_from"), "brief": p.get("brief"),
            "draft": p.get("draft"), "discarded": bool(p.get("discarded")), "category": p.get("category"),
            "can_undo": bool(p["undo_stack"]),
            # The version Undo returns to, so the button can say so (not derivable from `versions`
            # once an undo has been followed by a new change).
            "undo_to": p["undo_stack"][-1] if p["undo_stack"] else None,
        }

    def api_summary(self, pid: str) -> dict:
        p = self.load(pid)
        n = p["version"]
        return {"id": p["id"], "kind": p["kind"], "name": p["name"], "base": p["base"],
                "version": n, "updated": p.get("updated"),
                "model_url": f"/api/projects/{pid}/versions/{n}/model.glb" if n else None,
                "open_pins": sum(1 for pin in p["pins"] if pin["status"] != "resolved"),
                "derived_from": p.get("derived_from"), "draft": p.get("draft"),
                "discarded": bool(p.get("discarded")), "plan_built": bool(p.get("plan_built")),
                "category": p.get("category")}

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
            # Every save is kept as the next version, so the research can be reviewed as it was.
            d = self._dir(pid) / "research" / "versions"
            n = 1 + max((int(f.stem[1:]) for f in d.glob("r*.json") if f.stem[1:].isdigit()), default=0)
            atomic_write_json(d / f"r{n}.json", {"n": n, "at": now_iso(), "research": research})
            return project["research_rev"]

    def research_history(self, pid: str) -> list[dict]:
        """Every saved version of the research, oldest first, without the research itself."""
        out = []
        for f in (self._dir(pid) / "research" / "versions").glob("r*.json"):
            rec = read_json(f)
            if isinstance(rec, dict):
                r = rec.get("research") or {}
                out.append({"n": rec["n"], "at": rec["at"], "subject": r.get("subject"),
                            "verdict": (r.get("summary") or {}).get("verdict"),
                            "sources": len(r.get("sources") or []), "claims": len(r.get("claims") or [])})
        return sorted(out, key=lambda h: h["n"])

    def research_version(self, pid: str, n: int) -> dict:
        rec = read_json(self._dir(pid) / "research" / "versions" / f"r{int(n)}.json")
        if rec is None:
            raise NotFound(f"{pid} research version {n}")
        return rec

    def set_excluded_sources(self, pid: str, ids: list[str]) -> int:
        """Sources the user crossed out. It changes what the plan may rest on, so like a research save
        it bumps the revision (a draft plan must be redrafted, an approved one goes stale)."""
        with self.edit(pid) as project:
            project["excluded_sources"] = list(ids)
            on_research_saved(project)
            return project["research_rev"]

    def set_plan_params(self, pid: str, params: dict) -> dict:
        """The user's own values for the draft design plan's params; they win when it is built."""
        with self.edit(pid) as project:
            plan = project.get("design_plan")
            if not isinstance(plan, dict) or plan.get("status") != "draft":
                raise PlanRefused("Only a draft design plan can be changed.")
            plan["user_params"] = params
        return plan

    # ---- research materials and job logs ----------------------------------------------
    def add_material(self, pid: str, kind: str, title: str, text: str, source: dict) -> dict:
        """Something the research AI read: a web search's results or a fetched page, as it saw them."""
        with self.lock(pid):
            d = self._dir(pid) / "materials"
            n = 1 + max((int(f.stem[1:]) for f in d.glob("m*.json") if f.stem[1:].isdigit()), default=0)
            rec = {"id": f"m{n}", "kind": kind, "title": title, "at": now_iso(), "source": source,
                   "chars": len(text), "text": text}
            atomic_write_json(d / f"m{n}.json", rec)
        return rec

    def materials(self, pid: str) -> list[dict]:
        out = [{k: v for k, v in rec.items() if k != "text"}
               for rec in (read_json(f) for f in (self._dir(pid) / "materials").glob("m*.json")) if isinstance(rec, dict)]
        return sorted(out, key=lambda m: int(m["id"][1:]))

    def material(self, pid: str, mid: str) -> dict:
        if not re.match(r"^m\d+$", mid or ""):
            raise NotFound(mid)
        rec = read_json(self._dir(pid) / "materials" / f"{mid}.json")
        if rec is None:
            raise NotFound(mid)
        return rec

    def save_job_log(self, pid: str, job: dict) -> None:
        """Keep a finished job's log with its project. Checks the folder, never project.json: on Windows
        even a stat of a file opens it, and that can make a concurrent save of it fail."""
        try:
            if not self._dir(pid).is_dir():
                return
        except NotFound:
            return
        atomic_write_json(self._dir(pid) / "logs" / f"{job['id']}.json",
                          {k: job.get(k) for k in ("id", "kind", "label", "status", "error", "created", "finished", "log")})

    def job_logs(self, pid: str) -> list[dict]:
        logs = [rec for rec in (read_json(f) for f in (self._dir(pid) / "logs").glob("*.json")) if isinstance(rec, dict)]
        return sorted(logs, key=lambda j: j.get("created") or "")

    def research_dir(self, pid: str) -> Path:
        """data/projects/<id>/research/{photos,closeups}/: files the research tools store."""
        return self._dir(pid) / "research"

    def set_design_plan(self, pid: str, summary: list[str], spec: dict) -> dict:
        """A new draft, stamped with the research revision it was drafted against."""
        with self.edit(pid) as project:
            plan = {"status": "draft", "summary": summary, "spec": spec,
                    "research_rev": int(project.get("research_rev") or 0), "at": now_iso()}
            old = project.get("design_plan")
            if isinstance(old, dict) and old.get("user_params"):
                plan["user_params"] = old["user_params"]  # the user's values outlive a redraft
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
