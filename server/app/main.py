"""HTTP API (contract §10). Start with:

    server/.venv/bin/uvicorn app.main:app --port 8787 --app-dir server

`create_app` takes the builder, agent, router model and Unreal runner as arguments so tests can
swap in fakes; the module-level `app` uses the real ones.
"""
from __future__ import annotations

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

from . import crew_modules
from .agent import (AgentRunner, AgentSession, ClaudeAgentRunner, custom_research_context, module_prompt,
                    plan_build_prompt)
from .builder import BlenderRunner, BuilderRunner
from .builds import Builds, summarise_change
from .catalog import Catalog, CatalogError
from .config import Settings, find_claude_cli
from .jobs import JobError, JobManager
from .design_plan import name_from_brief, needs_plan
from .packaging import export_scene, safe_id, stations_of
from .router import LLM, claude_llm, route
from .specmerge import deep_merge, gltf_to_blender, keep_crew_nulls, label_for
from .store import NotFound, PlanRefused, ProjectStore, atomic_write_json, read_json
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
               unreal: UnrealRunner | None = None, workers: int = 3) -> FastAPI:
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
        projects = [store.api_summary(pid) for pid in store.ids()]
        return route(msg, projects, llm, names[:10])

    # ---- projects -----------------------------------------------------------------------
    @app.get("/api/projects")
    def list_projects():
        out = [store.api_summary(pid) for pid in store.ids()]
        return sorted(out, key=lambda p: p.get("updated") or "", reverse=True)

    @app.post("/api/projects")
    def create_project(body: dict = Body(...)):
        kind, base = body.get("kind"), body.get("base")
        if kind not in ("ship", "crew"):
            raise HTTPException(400, "kind must be ship or crew")
        brief = body.get("brief")
        if brief is not None and (not isinstance(brief, str) or len(brief) > 4000):
            raise HTTPException(400, "brief must be text of up to 4000 characters")
        brief = brief.strip() if isinstance(brief, str) and brief.strip() else None
        if kind == "ship" and base == "custom":
            return create_custom_ship(body.get("name"), brief)
        spec = catalog.default_spec(kind, base)
        name = body.get("name") or spec.get("name") or base
        project = store.create(kind, base, name, brief)
        pid = project["id"]
        n, d = store.reserve_version(pid)  # v1 holds the spec while the first build runs
        atomic_write_json(d / "spec.json", spec)

        def first_build(log):
            report = builds.runner.build(kind, d / "spec.json", d, log)
            atomic_write_json(d / "build_report.json", report)
            store.record_version(pid, n, report, f"Created from the {base} catalog default")
            if not report.get("ok"):
                raise JobError("First build failed: " + "; ".join(report.get("errors") or ["unknown"]),
                               result={"version": n, "build_report": report})
            return {"version": n, "build_report": report}

        job_id = jobs.submit("build", first_build)
        return {"project": store.api_project(pid), "job_id": job_id}

    def create_custom_ship(name: Any, brief: str | None) -> dict:
        """Contract §5 `type: "custom"`: a ship of any type from a prompt. v1 is the catalog's plain
        placeholder hull; the real shape comes only from an approved design plan."""
        if name is not None and (not isinstance(name, str) or len(name) > 80):
            raise HTTPException(400, "name must be text of up to 80 characters")
        name = (name or "").strip() or name_from_brief(brief)
        try:
            spec = catalog.default_spec("ship", "custom")
            note = None
        except CatalogError:
            # The custom builder's catalog file is not installed: keep a minimal spec so research and
            # planning can start, and build nothing (a build would fail on the missing builder).
            spec = {"schema": "me3d.ship_spec/1", "type": "custom", "params": {}, "overrides": {},
                    "joint_limits": {}, "custom_ops": []}
            note = ("The custom ship builder is not installed yet (catalog/ships/custom.json is missing), so "
                    "there is no placeholder model. Research and the design plan still work.")
        spec["type"], spec["name"] = "custom", name
        spec["id"] = safe_id(name) if safe_id(name) != "x" else "custom_ship"
        project = store.create("ship", "custom", name, brief)
        pid = project["id"]
        n, d = store.reserve_version(pid)  # v1 holds the spec (built only when the catalog exists)
        atomic_write_json(d / "spec.json", spec)

        def first_build(log):
            if note:
                log(note)
                return {"version": 0, "note": note}
            report = builds.runner.build("ship", d / "spec.json", d, log)
            atomic_write_json(d / "build_report.json", report)
            store.record_version(pid, n, report, "Created as a plain placeholder hull; research and an approved "
                                                 "design plan shape it")
            if not report.get("ok"):
                raise JobError("First build failed: " + "; ".join(report.get("errors") or ["unknown"]),
                               result={"version": n, "build_report": report})
            return {"version": n, "build_report": report}

        job_id = jobs.submit("build", first_build)
        out = store.api_project(pid)
        if note:
            out["note"] = note
        return {"project": out, "job_id": job_id}

    def refuse_unplanned_build(project: dict) -> None:
        why = needs_plan(project)
        if why:
            raise HTTPException(409, why)

    @app.post("/api/projects/{pid}/design-plan/approve")
    def approve_plan(pid: str):
        project = need_project(pid)
        if project.get("kind") != "ship" or project.get("base") != "custom":
            raise HTTPException(400, "Only custom ships have a design plan.")
        try:
            plan = store.approve_design_plan(pid)
        except PlanRefused as e:
            raise HTTPException(409, str(e))

        def work(log):
            log("Building the approved design plan.")
            try:
                built = build_or_fail(pid, plan["spec"], "Built from the approved design plan", log)
            except JobError as e:
                store.unapprove_design_plan(pid, str(e))
                store.add_message(pid, "AI", "The approved plan did not build, so the plan is back to draft: "
                                             f"{e}. Ask me in Research to fix it, then approve it again.")
                raise
            store.mark_plan_built(pid)
            log("Adding the plan's details.")
            pics = reference_pictures(pid, set())
            text = plan_build_prompt(plan)
            prompt: str | list[dict] = content_blocks(text, pics) if pics else text
            try:
                session, reply = run_agent(pid, prompt, "build", log)
            except Exception as e:
                reply = f"I built the approved plan but could not add its details: {e}. Ask me in Build to add them."
                store.add_message(pid, "AI", reply)
                return dict(built, reply=reply, built_versions=[built["version"]])
            store.add_message(pid, "AI", reply)
            return {"version": store.load(pid)["version"], "build_report": store.report(pid, store.load(pid)["version"]),
                    "reply": reply, "built_versions": [built["version"]] + session.built_versions}

        return {"job_id": jobs.submit("build", work)}

    def reference_pictures(pid: str, exclude: set[str], limit: int = 4) -> list[tuple[dict, bytes]]:
        """The latest pictures attached earlier in this project's chat. The AI has no memory between
        turns, so a custom ship's research and detail turns get the photos again (capped: each costs
        tokens on every turn)."""
        seen: list[str] = []
        for m in reversed(store.load(pid)["messages"]):
            for a in reversed(m.get("attachments") or []):
                uid = a.get("id")
                if uid and uid not in exclude and uid not in seen and str(a.get("type", "")).startswith("image/"):
                    seen.append(uid)
        out = []
        for uid in seen[:limit]:
            try:
                out.append(store.get_upload(pid, uid))
            except (NotFound, OSError):
                continue
        return list(reversed(out))

    @app.get("/api/projects/{pid}")
    def get_project(pid: str):
        need_project(pid)
        return store.api_project(pid)

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
        refuse_unplanned_build(project)
        if not project["version"]:
            raise HTTPException(409, "The first build has not finished yet")
        old = store.spec(pid)
        new = deep_merge(old, patch)
        if project.get("kind") == "crew":
            new = keep_crew_nulls(new, patch)
            try:  # the same outfit vs shirt/pants rule the AI's tools apply
                new, _notes = crew_modules.apply_module_rules(old, new)
            except crew_modules.ModuleRuleError as e:
                raise HTTPException(400, str(e))
        summary = summarise_change(old, new, "Edited in Inspect")
        return {"job_id": jobs.submit("build", lambda log: build_or_fail(pid, new, summary, log))}

    @app.post("/api/projects/{pid}/undo")
    def undo(pid: str):
        need_project(pid)
        store.undo(pid)
        return {"project": store.api_project(pid)}

    # ---- AI: chat and pins ------------------------------------------------------------
    def run_agent(pid: str, prompt: str | list[dict], mode: str, log, pin_id: str | None = None,
                  module: str | None = None) -> tuple[AgentSession, str]:
        session = AgentSession(store, builds, catalog, pid, log, settings.repo_root, pin_id=pin_id, mode=mode,
                               module=module)
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
        project = need_project(pid)
        message, mode = body.get("message"), body.get("mode", "build")
        if not isinstance(message, str) or not message.strip():
            raise HTTPException(400, "message is required")
        if mode not in ("build", "research"):
            raise HTTPException(400, "mode must be build or research")
        module = body.get("module")
        if module is not None:
            if module not in crew_modules.MODULES:
                raise HTTPException(400, f"module must be one of {list(crew_modules.MODULES)}")
            if project.get("kind") != "crew" or mode != "build":
                raise HTTPException(400, "module is only for build chats on crew projects")
        if mode == "build":
            refuse_unplanned_build(project)
        custom = project.get("kind") == "ship" and project.get("base") == "custom"
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
            if module:
                text = module_prompt(module) + text
            sent = list(files)
            if custom:
                now = store.load(pid)
                if mode == "research":
                    spec_now = store.spec(pid, now["version"] or 1)
                    try:
                        entry = catalog.entry("ship", "custom")
                    except CatalogError:
                        entry = {}
                    text = custom_research_context(now, spec_now, entry) + text
                elif (now.get("design_plan") or {}).get("summary"):
                    text = ("The approved design plan for this ship:\n" +
                            "\n".join(f"- {s}" for s in now["design_plan"]["summary"]) + "\n\n" + text)
                # Earlier photos again: the AI does not remember the last turn's pictures.
                sent = reference_pictures(pid, {meta["id"] for meta, _ in files}) + sent
            prompt: str | list[dict] = content_blocks(text, sent) if sent else text
            try:
                session, reply = run_agent(pid, prompt, mode, log, module=module)
            except Exception as e:
                store.add_message(pid, "AI", f"Sorry, I hit a problem and changed nothing: {e}")
                raise JobError(f"AI run failed: {e}")
            store.add_message(pid, "AI", reply)
            version = store.load(pid)["version"]
            result = {"reply": reply, "version": version,
                      "built_versions": session.built_versions, "research_saved": session.research_saved}
            if custom:
                result["plan_saved"] = session.plan_saved
            if session.kind == "crew":
                # What is now on screen versus what was there before the turn, so the screen can
                # show which modules changed (a failed last build leaves the earlier version shown).
                result["changed_modules"] = crew_modules.ordered(
                    crew_modules.changed_modules(session.start_spec, store.spec(pid, version)))
            return result

        return {"job_id": jobs.submit("chat", work)}

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
        refuse_unplanned_build(store.load(pid))
        if not isinstance(part, str) or not part or not isinstance(text, str) or not text.strip():
            raise HTTPException(400, "part and text are required")
        if point is not None and not (isinstance(point, list) and len(point) == 3):
            raise HTTPException(400, "point must be [x,y,z]")
        pin = store.add_pin(pid, part, point, text, body.get("author") or "You")
        return {"pin": pin, "job_id": jobs.submit("pin", pin_job(pid, pin["id"]))}

    @app.post("/api/projects/{pid}/pins/{pin_id}/reply")
    def reply_pin(pid: str, pin_id: str, body: dict = Body(...)):
        need_project(pid)
        text = body.get("text")
        if not isinstance(text, str) or not text.strip():
            raise HTTPException(400, "text is required")
        store.get_pin(pid, pin_id)
        refuse_unplanned_build(store.load(pid))
        store.update_pin(pid, pin_id, status="working", message=(body.get("author") or "You", text))
        return {"job_id": jobs.submit("pin", pin_job(pid, pin_id))}

    @app.patch("/api/projects/{pid}/pins/{pin_id}")
    def patch_pin(pid: str, pin_id: str, body: dict = Body(...)):
        need_project(pid)
        status = body.get("status")
        if status not in ("open", "resolved"):
            raise HTTPException(400, "status must be open or resolved")
        return {"pin": store.update_pin(pid, pin_id, status=status)}

    # ---- jobs ---------------------------------------------------------------------------
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
        return {"schema": "me3d.scene_spec/1", "name": body.get("name") or "Untitled scene",
                "ship": {"project": ship["project"], "version": int(ship.get("version") or
                                                                    store.load(ship["project"])["version"])},
                "crew": [{"project": c["project"],
                          "version": int(c.get("version") or store.load(c["project"])["version"]),
                          "station": c["station"], "animation": c.get("animation") or "idle"}
                         for c in crew]}

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
        return {"job_id": jobs.submit("export", lambda log: export_scene(
            scene, store, settings.packages_dir, log))}

    @app.post("/api/scenes/{sid}/unreal-test")
    def unreal_test(sid: str):
        scene = load_scene(sid)

        def work(log):
            pkg = export_scene(scene, store, settings.packages_dir, log)
            return unreal(Path(pkg["package_dir"]), log)
        return {"job_id": jobs.submit("unreal", work)}

    # ---- files --------------------------------------------------------------------------
    @app.get("/api/files/{path:path}")
    def files(path: str):
        target = safe_data_path(settings.data_dir, path)
        if target is None:
            raise HTTPException(404, "not found")
        return FileResponse(target)

    return app


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
