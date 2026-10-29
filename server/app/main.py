"""HTTP API (contract §10). Start with:

    server/.venv/bin/uvicorn app.main:app --port 8787 --app-dir server

`create_app` takes the builder, agent, router model and Unreal runner as arguments so tests can
swap in fakes; the module-level `app` uses the real ones.
"""
from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Any, Callable

from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from .agent import AgentRunner, AgentSession, ClaudeAgentRunner, variant_prompt, variant_research_prompt
from .builder import BlenderRunner, BuilderRunner
from .builds import Builds, built_line, summarise_change
from .catalog import Catalog, CatalogError
from .config import Settings, find_claude_cli
from .jobs import JobError, JobManager
from .packaging import export_scene, stations_of
from .router import LLM, claude_llm, route
from .specmerge import deep_merge, gltf_to_blender, keep_crew_nulls, label_for, variant_id
from .store import NotFound, ProjectStore, atomic_write_json, read_json
from .uploads import UploadRejected, check_size, content_blocks, decode, resolve_type
from .uploads import TYPES as UPLOAD_TYPES

UnrealRunner = Callable[[Path, Callable[[str], None]], dict]
SCENE_ID = re.compile(r"^scene_[a-z0-9]+$")
LOCKED_SPEC_KEYS = {"schema", "id", "type"}


def make_unreal_runner(settings: Settings) -> UnrealRunner:
    # Every run imports into the one harness project; two at once overwrite each other's assets.
    lock = threading.Lock()

    def run(package_dir: Path, log) -> dict:
        script = settings.unreal_script
        if not script.exists():
            raise JobError(f"Unreal harness script missing: {script}")
        if shutil.which("bash") is None:
            raise JobError("The Unreal import test needs bash, which is not on PATH (the harness is macOS-only).")
        report_path = package_dir / "unreal_import_report.json"
        if report_path.exists():
            report_path.unlink()
        if lock.locked():
            log("Waiting for another Unreal test to finish…")
        with lock:
            log(f"$ {script} {package_dir}")
            try:
                proc = subprocess.run(["bash", str(script), str(package_dir)], capture_output=True, text=True,
                                      errors="replace", timeout=settings.unreal_timeout_s,
                                      cwd=str(settings.repo_root))
            except subprocess.TimeoutExpired:
                raise JobError(f"Unreal import test timed out after {settings.unreal_timeout_s:.0f} s.")
        for line in (proc.stdout + proc.stderr).splitlines()[-60:]:
            log(line)
        report = read_json(report_path)
        if not isinstance(report, dict) or "ok" not in report:
            raise JobError(f"Unreal wrote no readable report (script exit code {proc.returncode}).")
        return report
    return run


def create_app(settings: Settings | None = None, runner: BuilderRunner | None = None,
               agent: AgentRunner | None = None, llm: LLM | None = None,
               unreal: UnrealRunner | None = None, workers: int = 6) -> FastAPI:
    settings = settings or Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    store = ProjectStore(settings.projects_dir)
    catalog = Catalog(settings.catalog_dir)
    builds = Builds(store, runner or BlenderRunner(settings))
    agent = agent or ClaudeAgentRunner(model=settings.agent_model)
    llm = llm or claude_llm(settings.route_model)
    unreal = unreal or make_unreal_runner(settings)
    jobs = JobManager(workers=workers)

    app = FastAPI(title="3D model editor server")
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                       allow_methods=["*"], allow_headers=["*"])
    app.state.settings, app.state.store, app.state.jobs = settings, store, jobs

    @app.exception_handler(NotFound)
    async def _nf(_req, exc):
        return JSONResponse({"detail": f"Not found: {exc}"}, status_code=404)

    @app.exception_handler(CatalogError)
    async def _ce(_req, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    def need_project(pid: str) -> dict:
        if not store.exists(pid):
            raise NotFound(pid)
        return store.load(pid)

    def build_or_fail(pid: str, spec: dict, summary: str, log) -> dict:
        n, report = builds.build(pid, spec, summary, log)
        if not report.get("ok"):
            errs = "; ".join(report.get("errors") or ["unknown error"])
            raise JobError(f"Build failed (version {n} kept for inspection, not shown): {errs}",
                           result={"version": n, "build_report": report})
        return {"version": n, "build_report": report}

    def start_project(project: dict, spec: dict) -> tuple[int, Path]:
        """Write v1's spec now, so the screen can show it while the first build runs."""
        n, d = store.reserve_version(project["id"])
        atomic_write_json(d / "spec.json", spec)
        return n, d

    def first_build(project: dict, n: int, d: Path, summary: str, log) -> dict:
        log(f"• Building version {n} in Blender: {summary}")
        report = builds.runner.build(project["kind"], d / "spec.json", d, log)
        atomic_write_json(d / "build_report.json", report)
        store.record_version(project["id"], n, report, summary)
        log(built_line(n, report))
        if not report.get("ok"):
            raise JobError("First build failed: " + "; ".join(report.get("errors") or ["unknown"]),
                           result={"version": n, "build_report": report})
        return {"version": n, "build_report": report}

    # ---- health and catalog -----------------------------------------------------------
    @app.get("/api/health")
    def health():
        return {"blender": settings.blender.exists(), "mpfb": settings.mpfb_dir.is_dir(),
                "ai": find_claude_cli() is not None, "unreal": settings.unreal_dir.is_dir()}

    @app.get("/api/catalog")
    def get_catalog():
        return catalog.summary()

    @app.post("/api/route")
    def post_route(body: dict = Body(...)):
        msg = body.get("message")
        if not isinstance(msg, str) or not msg.strip():
            raise HTTPException(400, "message is required")
        names = body.get("attachments") or []
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
            raise HTTPException(400, "attachments must be a list of file names")
        return route(msg, listed_projects(), llm, names[:10])

    # ---- projects -----------------------------------------------------------------------
    def with_category(p: dict) -> dict:
        """A project made before categories existed is in its catalog base's category."""
        if not p.get("category"):
            p["category"] = catalog.category(p["kind"], p["base"])
        return p

    def listed_projects() -> list[dict]:
        """Every project except discarded variants."""
        return [with_category(s) for s in (store.api_summary(pid) for pid in store.ids()) if not s["discarded"]]

    @app.get("/api/projects")
    def list_projects():
        return sorted(listed_projects(), key=lambda p: p.get("updated") or "", reverse=True)

    @app.post("/api/projects")
    def create_project(body: dict = Body(...)):
        kind, base = body.get("kind"), body.get("base")
        if kind not in ("ship", "crew"):
            raise HTTPException(400, "kind must be ship or crew")
        spec = catalog.default_spec(kind, base)
        name = body.get("name") or spec.get("name") or base
        project = store.create(kind, base, name, category=catalog.category(kind, base))
        n, d = start_project(project, spec)
        job_id = jobs.submit("build", project=project["id"], label=f"First build of {name}", fn=lambda log: first_build(
            project, n, d, f"Created from the {base} catalog default", log))
        return {"project": store.api_project(project["id"]), "job_id": job_id}

    def not_a_draft(project: dict, doing: str) -> None:
        if project.get("discarded"):
            raise HTTPException(409, f"{project['name']} was discarded, so it can't be {doing}.")
        if project.get("draft"):
            raise HTTPException(409, f"{project['name']} is a variant awaiting review. Approve it before it is {doing}.")

    def draft_running(project: dict) -> bool:
        """The variant's own job is still going (a server restart ends it, so a lost job counts as finished)."""
        job = jobs.get((project.get("draft") or {}).get("job_id") or "")
        return bool(job and job["status"] in ("queued", "running"))

    @app.post("/api/projects/{pid}/variants")
    def create_variant(pid: str, body: dict = Body(...)):
        source = need_project(pid)
        not_a_draft(source, "copied")
        if not source["version"]:
            raise HTTPException(409, "The first build has not finished yet")
        version = body.get("version", source["version"])
        if not isinstance(version, int) or isinstance(version, bool):
            raise HTTPException(400, "version must be a whole number")
        built = next((v for v in source["versions"] if v["n"] == version), None)
        if built is None:
            raise HTTPException(404, f"{source['name']} has no version {version}")
        if not built["ok"]:
            raise HTTPException(400, f"Version {version} did not build. Pick a version that did.")
        name, brief = body.get("name"), body.get("brief")
        if name is not None and not isinstance(name, str) or brief is not None and not isinstance(brief, str):
            raise HTTPException(400, "name and brief must be text")
        name = (name or "").strip() or f"{source['name']} (variant)"
        brief = (brief or "").strip() or None
        research = bool(body.get("research"))
        if research and (source["kind"] != "ship" or not brief):
            raise HTTPException(400, "Research needs a ship and a description of what is different")
        scene = None
        if body.get("scene"):
            if source["kind"] != "ship":
                raise HTTPException(400, "Only a ship variant can bring a scene along")
            scene = load_scene(body["scene"])
            if scene["ship"]["project"] != pid:
                raise HTTPException(400, f"That scene is not on {source['name']}")

        spec = copy.deepcopy(store.spec(pid, version))
        spec["id"], spec["name"] = variant_id(name, store.spec_ids()), name
        draft = {"stage": "copying", "job_id": None, "research": research,
                 "scene": scene["id"] if scene else None, "error": None}
        project = store.create_variant(source, version, name, brief, draft)
        vid = project["id"]
        n, d = start_project(project, spec)
        if brief:
            store.add_message(vid, "You", brief)

        def work(log):
            log(f"• Copying {source['name']} v{version}")
            try:
                result = first_build(project, n, d, f"Copied from {source['name']} v{version}", log)
            except JobError as e:
                store.set_draft(vid, stage="failed", error=str(e))
                raise
            failure = None
            try:
                if research:
                    store.set_draft(vid, stage="researching")
                    log("• Researching the changes from public sources")
                    _, reply = run_agent(vid, variant_research_prompt(source, brief, project["research"]),
                                         "research", log)
                    store.add_message(vid, "AI", reply)
                if brief:
                    store.set_draft(vid, stage="changing")
                    log("• The AI is making the changes")
                    _, reply = run_agent(vid, variant_prompt(source, version, brief), "build", log)
                    store.add_message(vid, "AI", reply)
                    result["reply"] = reply
            except Exception as e:
                store.add_message(vid, "AI", f"Sorry, I hit a problem and stopped: {e}")
                failure = e
            result["version"] = store.load(vid)["version"]
            result["build_report"] = store.report(vid, result["version"]) or {}
            if failure:
                store.set_draft(vid, stage="ready", error=f"The AI step failed ({failure}). What you see is the "
                                                           "last version that built.")
                log("• The AI step failed. You can still review what was built.")
                raise JobError(f"AI run failed: {failure}", result=result)
            store.set_draft(vid, stage="ready")
            log("• Ready for your review")
            return result

        store.set_draft(vid, job_id=jobs.submit("variant", work, project=vid, label=f"Making the variant {name}"))
        return {"project": store.api_project(vid), "job_id": store.load(vid)["draft"]["job_id"]}

    @app.post("/api/projects/{pid}/approve")
    def approve_variant(pid: str):
        project = need_project(pid)
        draft = project.get("draft")
        if not draft or project.get("discarded"):
            raise HTTPException(409, f"{project['name']} is not awaiting review")
        if draft_running(project):
            raise HTTPException(409, "The variant is still being made. Approve it when it is ready.")
        if not project["version"] or not (store.report(pid, project["version"]) or {}).get("ok"):
            raise HTTPException(409, "Nothing built to approve. Discard this variant and try again.")
        with store.edit(pid) as p:
            p["draft"] = None
        scene = None
        if draft.get("scene"):
            try:
                scene = copy_scene_to(load_scene(draft["scene"]), pid, lambda line: None)
            except NotFound:  # the original scene was deleted meanwhile
                scene = None
        return {"project": store.api_project(pid), "scene": scene}

    @app.post("/api/projects/{pid}/discard")
    def discard_variant(pid: str):
        project = need_project(pid)
        if not project.get("draft") or project.get("discarded"):
            raise HTTPException(409, "Only a variant awaiting review can be discarded")
        if draft_running(project):
            raise HTTPException(409, "The variant is still being made. Discard it when it is ready.")
        with store.edit(pid) as p:
            p["discarded"] = True
        return {"project": store.api_project(pid)}

    @app.get("/api/projects/{pid}")
    def get_project(pid: str):
        need_project(pid)
        return with_category(store.api_project(pid))

    @app.patch("/api/projects/{pid}")
    def patch_project(pid: str, body: dict = Body(...)):
        """Rename a project or file it under a category. Neither touches the spec or makes a version."""
        need_project(pid)
        fields = {}
        for key in ("category", "name"):
            if key in body:
                value = body[key]
                if not isinstance(value, str) or not value.strip() or len(value.strip()) > 60:
                    raise HTTPException(400, f"{key} must be text of 1 to 60 characters")
                fields[key] = " ".join(value.split())
        if not fields or set(body) - {"category", "name"}:
            raise HTTPException(400, "Send category and/or name, nothing else")
        with store.edit(pid) as p:
            p.update(fields)
        return {"project": with_category(store.api_project(pid))}

    def version_file(pid: str, n: int, name: str) -> Path:
        need_project(pid)
        f = store.version_dir(pid, n) / name
        if not f.is_file():
            raise HTTPException(404, f"{name} not found for version {n}")
        return f

    @app.get("/api/projects/{pid}/versions/{n}/model.glb")
    def get_glb(pid: str, n: int):
        return FileResponse(version_file(pid, n, "model.glb"), media_type="model/gltf-binary")

    @app.get("/api/projects/{pid}/versions/{n}/parts.json")
    def get_parts(pid: str, n: int):
        return FileResponse(version_file(pid, n, "parts.json"), media_type="application/json")

    @app.post("/api/projects/{pid}/spec")
    def patch_spec(pid: str, body: dict = Body(...)):
        project = need_project(pid)
        patch = body.get("patch")
        if not isinstance(patch, dict) or not patch:
            raise HTTPException(400, "patch must be a non-empty object")
        locked = LOCKED_SPEC_KEYS & set(patch)
        if locked:
            raise HTTPException(400, f"These spec fields cannot be changed: {sorted(locked)}")
        if not project["version"]:
            raise HTTPException(409, "The first build has not finished yet")
        old = store.spec(pid)
        new = deep_merge(old, patch)
        if project.get("kind") == "crew":
            new = keep_crew_nulls(new, patch)
        summary = summarise_change(old, new, "Edited in Inspect")
        return {"job_id": jobs.submit("build", lambda log: build_or_fail(pid, new, summary, log),
                                      project=pid, label="Rebuilding with your changes")}

    @app.post("/api/projects/{pid}/undo")
    def undo(pid: str):
        need_project(pid)
        store.undo(pid)
        return {"project": store.api_project(pid)}

    # ---- AI: chat and pins ------------------------------------------------------------
    def run_agent(pid: str, prompt: str | list[dict], mode: str, log, pin_id: str | None = None) -> tuple[AgentSession, str]:
        session = AgentSession(store, builds, catalog, pid, log, settings.repo_root, pin_id=pin_id, mode=mode)
        reply = agent.run(session, prompt, mode, log)
        if mode == "build" and session.dirty:
            log("The AI left unbuilt changes; rebuilding so the screen matches its reply.")
            session.t_rebuild()
        if session.built_versions and session.version != session.built_versions[-1]:  # last build failed
            last = store.report(pid, session.built_versions[-1]) or {}
            errs = "; ".join(last.get("errors") or [])
            reply += f"\n\n(The last rebuild failed, so the previous version is still shown: {errs})"
        return session, reply or "(no reply)"

    def attached_names(m: dict) -> str:
        """Earlier attachments are named in the history, not re-sent: one copy per message is enough."""
        names = [a.get("name", "") for a in m.get("attachments") or []]
        return f" [attached: {', '.join(names)}]" if names else ""

    def checked_upload(body: dict) -> tuple[str, str, bytes]:
        name, mime, data = body.get("name"), body.get("type"), body.get("data")
        if not isinstance(name, str) or not name.strip() or not isinstance(data, str):
            raise HTTPException(400, "name and data are required")
        name = Path(name.replace("\\", "/")).name[:120] or "file"
        try:
            mime = resolve_type(name, mime if isinstance(mime, str) else "")
            raw = decode(data)
            check_size(name, mime, len(raw))
        except UploadRejected as e:
            raise HTTPException(400, str(e))
        return name, mime, raw

    @app.post("/api/projects/{pid}/uploads")
    def upload(pid: str, body: dict = Body(...)):
        need_project(pid)
        name, mime, raw = checked_upload(body)
        return {"attachment": store.add_upload(pid, name, mime, UPLOAD_TYPES[mime][0], raw)}

    @app.post("/api/uploads")
    def upload_from_home(body: dict = Body(...)):
        """Home has no project yet: hold the file until the chat it is carried into names it."""
        name, mime, raw = checked_upload(body)
        return {"attachment": store.add_inbox_upload(name, mime, UPLOAD_TYPES[mime][0], raw)}

    @app.post("/api/projects/{pid}/chat")
    def chat(pid: str, body: dict = Body(...)):
        need_project(pid)
        message, mode = body.get("message"), body.get("mode", "build")
        if not isinstance(message, str) or not message.strip():
            raise HTTPException(400, "message is required")
        if mode not in ("build", "research"):
            raise HTTPException(400, "mode must be build or research")
        ids = body.get("attachments") or []
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids) or len(ids) > 10:
            raise HTTPException(400, "attachments must be a list of up to 10 upload ids")
        try:
            files = [store.get_upload(pid, i) for i in ids]
        except NotFound as e:
            raise HTTPException(400, f"Attachment {e} was not found; attach it again")
        store.add_message(pid, "You", message, [meta for meta, _ in files])

        def work(log):
            history = store.load(pid)["messages"][-12:-1]
            context = "\n".join(f"{m['who']}: {m['text']}" + attached_names(m) for m in history)
            text = (f"Earlier in this chat:\n{context}\n\n" if context else "") + f"User: {message}"
            prompt: str | list[dict] = content_blocks(text, files) if files else text
            try:
                session, reply = run_agent(pid, prompt, mode, log)
            except Exception as e:
                store.add_message(pid, "AI", f"Sorry, I hit a problem and changed nothing: {e}")
                raise JobError(f"AI run failed: {e}")
            store.add_message(pid, "AI", reply)
            return {"reply": reply, "version": store.load(pid)["version"],
                    "built_versions": session.built_versions, "research_saved": session.research_saved}

        what = "Research" if mode == "research" else "Chat"
        return {"job_id": jobs.submit("chat", work, project=pid, label=f"{what}: {short(message)}")}

    def pin_prompt(pid: str, pin: dict) -> str:
        parts = store.parts(pid, store.load(pid)["version"])
        part = next((p for p in parts if p.get("path") == pin["part"]), None)
        where = ""
        if pin.get("point"):
            where = (f" at point {pin['point']} (viewer, Y-up), which is "
                     f"{[round(v, 3) for v in gltf_to_blender(pin['point'])]} in the ship frame")
        detail = ""
        if part:
            detail = (f"\nThat part: bounds {part.get('bounds_min')} to {part.get('bounds_max')}, "
                      f"joint {json.dumps(part.get('joint'))}, parent {part.get('parent')}.")
        else:
            detail = "\nThat part is not in the current build's parts list (it may be a sub-mesh)."
        thread = "\n".join(f"{m['who']}: {m['text']}" for m in pin["messages"])
        return (f"The user pinned part `{pin['part']}` (label {label_for(pin['part'])}){where}.{detail}\n"
                f"Pin id: {pin['id']}. Thread so far:\n{thread}\n\n"
                "Make the change they ask for on that part, rebuild, then answer with reply_to_pin "
                "(status 'waiting' if you changed something, 'resolved' if no change is needed, "
                "'open' if you need their input).")

    def pin_job(pid: str, pin_id: str):
        def work(log):
            pin = store.get_pin(pid, pin_id)
            before = store.load(pid)["version"]
            store.update_pin(pid, pin_id, status="working", before_version=before)
            try:
                session, reply = run_agent(pid, pin_prompt(pid, pin), "build", log, pin_id=pin_id)
            except Exception as e:
                store.update_pin(pid, pin_id, status="open",
                                 message=("AI", f"Sorry, I hit a problem and changed nothing: {e}"))
                raise JobError(f"AI run failed: {e}")
            after = store.load(pid)["version"]
            fields = {"after_version": after if after != before else None}
            if session.pin_replied:
                pin = store.update_pin(pid, pin_id, **fields)
            else:
                pin = store.update_pin(pid, pin_id, status="waiting", message=("AI", reply), **fields)
            return {"pin": pin, "reply": reply, "version": after}
        return work

    @app.post("/api/projects/{pid}/pins")
    def create_pin(pid: str, body: dict = Body(...)):
        need_project(pid)
        part, text = body.get("part"), body.get("text")
        point = body.get("point")
        if not isinstance(part, str) or not part or not isinstance(text, str) or not text.strip():
            raise HTTPException(400, "part and text are required")
        if point is not None and not (isinstance(point, list) and len(point) == 3):
            raise HTTPException(400, "point must be [x,y,z]")
        pin = store.add_pin(pid, part, point, text, body.get("author") or "You")
        return {"pin": pin, "job_id": jobs.submit("pin", pin_job(pid, pin["id"]), project=pid,
                                                  label=f"Comment on {label_for(part)}")}

    @app.post("/api/projects/{pid}/pins/{pin_id}/reply")
    def reply_pin(pid: str, pin_id: str, body: dict = Body(...)):
        need_project(pid)
        text = body.get("text")
        if not isinstance(text, str) or not text.strip():
            raise HTTPException(400, "text is required")
        store.get_pin(pid, pin_id)
        store.update_pin(pid, pin_id, status="working", message=(body.get("author") or "You", text))
        part = store.get_pin(pid, pin_id)["part"]
        return {"job_id": jobs.submit("pin", pin_job(pid, pin_id), project=pid, label=f"Reply on {label_for(part)}")}

    @app.patch("/api/projects/{pid}/pins/{pin_id}")
    def patch_pin(pid: str, pin_id: str, body: dict = Body(...)):
        need_project(pid)
        status = body.get("status")
        if status not in ("open", "resolved"):
            raise HTTPException(400, "status must be open or resolved")
        return {"pin": store.update_pin(pid, pin_id, status=status)}

    # ---- jobs ---------------------------------------------------------------------------
    @app.get("/api/jobs")
    def list_jobs():
        rows = jobs.listing()
        for r in rows:
            p = store.load(r["project"]) if r["project"] and store.exists(r["project"]) else None
            r["project_name"], r["project_kind"] = (p["name"], p["kind"]) if p else (None, None)
        return {"jobs": rows}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "no such job")
        return job

    # ---- scenes and export --------------------------------------------------------------
    def scene_path(sid: str) -> Path:
        if not SCENE_ID.match(sid or ""):
            raise NotFound(sid)
        return settings.scenes_dir / f"{sid}.json"

    def load_scene(sid: str) -> dict:
        s = read_json(scene_path(sid))
        if s is None:
            raise NotFound(sid)
        return s

    def validate_scene(body: dict) -> dict:
        if not isinstance(body, dict):
            raise HTTPException(400, "scene must be an object")
        ship = body.get("ship") or {}
        if not store.exists(ship.get("project", "")) or store.load(ship["project"])["kind"] != "ship":
            raise HTTPException(400, "ship.project must be an existing ship project")
        crew = body.get("crew") or []
        if not isinstance(crew, list):
            raise HTTPException(400, "crew must be a list")
        for i, c in enumerate(crew):
            if not isinstance(c, dict) or not store.exists(c.get("project", "")) \
                    or store.load(c["project"])["kind"] != "crew":
                raise HTTPException(400, f"crew[{i}].project must be an existing crew project")
            if not isinstance(c.get("station"), str):
                raise HTTPException(400, f"crew[{i}].station is required")
        for ref in [ship, *crew]:
            p = store.load(ref["project"])
            if p.get("draft") or p.get("discarded"):
                raise HTTPException(400, f"{p['name']} is a variant that has not been approved. "
                                         "Approve it before putting it in a scene.")
        return {"schema": "me3d.scene_spec/1", "name": body.get("name") or "Untitled scene",
                "ship": {"project": ship["project"], "version": int(ship.get("version") or
                                                                    store.load(ship["project"])["version"])},
                "crew": [{"project": c["project"],
                          "version": int(c.get("version") or store.load(c["project"])["version"]),
                          "station": c["station"], "animation": c.get("animation") or "idle"}
                         for c in crew]}

    def copy_scene_to(scene: dict, pid: str, log) -> dict:
        """A copy of `scene` on project pid's current version, with the same crew. Crew whose station
        that version lacks keep it and are reported, so the user moves them rather than losing them."""
        project = store.load(pid)
        names = {s["name"] for s in stations_of(store.parts(pid, project["version"]))}
        missing = sorted({c["station"] for c in scene["crew"]} - names)
        new = {"schema": "me3d.scene_spec/1", "id": "scene_" + uuid.uuid4().hex[:8],
               "name": f"{project['name']} with crew",
               "ship": {"project": pid, "version": project["version"]},
               "crew": copy.deepcopy(scene["crew"])}
        atomic_write_json(scene_path(new["id"]), new)
        log(f"Copied the scene {scene['name']!r} onto the variant."
            + (f" These crew points are gone: {', '.join(missing)}. Move the crew on them." if missing else ""))
        return {"id": new["id"], "missing_stations": missing}

    def with_stations(scene: dict) -> dict:
        ship = scene["ship"]
        try:
            parts = store.parts(ship["project"], ship["version"])
        except NotFound:
            parts = []
        return dict(scene, stations=stations_of(parts))

    @app.get("/api/scenes")
    def list_scenes():
        settings.scenes_dir.mkdir(parents=True, exist_ok=True)
        return [read_json(p) for p in sorted(settings.scenes_dir.glob("scene_*.json"))]

    @app.post("/api/scenes")
    def create_scene(body: dict = Body(...)):
        scene = validate_scene(body)
        scene = {"schema": scene["schema"], "id": "scene_" + uuid.uuid4().hex[:8], **{
            k: v for k, v in scene.items() if k != "schema"}}
        atomic_write_json(scene_path(scene["id"]), scene)
        return {"scene": with_stations(scene)}

    @app.get("/api/scenes/{sid}")
    def get_scene(sid: str):
        return with_stations(load_scene(sid))

    @app.put("/api/scenes/{sid}")
    def put_scene(sid: str, body: dict = Body(...)):
        load_scene(sid)
        scene = validate_scene(body)
        scene = {"schema": scene["schema"], "id": sid, **{k: v for k, v in scene.items() if k != "schema"}}
        atomic_write_json(scene_path(sid), scene)
        return {"scene": with_stations(scene)}

    @app.post("/api/scenes/{sid}/export")
    def export(sid: str):
        scene = load_scene(sid)
        return {"job_id": jobs.submit("export", label=f"Export {scene.get('name', sid)}", fn=lambda log: export_scene(
            scene, store, settings.packages_dir, log))}

    @app.post("/api/scenes/{sid}/unreal-test")
    def unreal_test(sid: str):
        scene = load_scene(sid)

        def work(log):
            pkg = export_scene(scene, store, settings.packages_dir, log)
            return unreal(Path(pkg["package_dir"]), log)
        return {"job_id": jobs.submit("unreal", work, label=f"Unreal test of {scene.get('name', sid)}")}

    # ---- files --------------------------------------------------------------------------
    @app.get("/api/files/{path:path}")
    def files(path: str):
        target = safe_data_path(settings.data_dir, path)
        if target is None:
            raise HTTPException(404, "not found")
        return FileResponse(target)

    return app


def short(text: str, n: int = 60) -> str:
    """One line of at most n characters, for a job label."""
    one = " ".join(str(text).split())
    return one if len(one) <= n else one[: n - 1].rstrip() + "…"


def safe_data_path(data_dir: Path, rel: str) -> Path | None:
    """Resolve `rel` under data_dir; None if it escapes (.., absolute, symlink out) or is not a file."""
    root = data_dir.resolve()
    if not rel or "\x00" in rel:
        return None
    target = (root / rel).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        return None
    return target


app = create_app()
