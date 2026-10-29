"""Background jobs (contract §10: {"id","status","log","result","error"}).

Jobs run on a small thread pool, one at a time per project (see JobManager). Blender builds are
additionally serialised by the builder's own lock, so two agent jobs can think at the same time
but never both run Blender.
"""
from __future__ import annotations

import threading
import time
import traceback
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable

MAX_LOG_LINES = 400


class JobError(Exception):
    """A failure whose message is meant for the user (shown as the job's error)."""

    def __init__(self, message: str, result: Any = None):
        super().__init__(message)
        self.result = result


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class JobLog:
    def __init__(self, job: dict, lock: threading.Lock):
        self._job = job
        self._lock = lock

    def __call__(self, line: str) -> None:
        with self._lock:
            log = self._job["log"]
            for part in str(line).splitlines() or [""]:
                log.append(part)
            trim_log(log)


def trim_log(log: list[str], limit: int = MAX_LOG_LINES) -> None:
    """Keep the log to `limit` lines, dropping the oldest technical lines first: the lines for people
    ("• ", contract §10) are what Research and Build replay as steps and versions, and a plan build's
    Blender output would otherwise push its first versions out. Only people's lines left: oldest go."""
    extra = len(log) - limit
    if extra <= 0:
        return
    drop = [i for i, line in enumerate(log) if not line.startswith("• ")][:extra]
    for i in reversed(drop):
        del log[i]
    if len(log) > limit:
        del log[: len(log) - limit]


class JobManager:
    """Jobs for different projects run side by side. Jobs for the same project run one after
    another, in the order sent: each AI turn reads the project's spec when it starts, so two at
    once would each build from the old spec and the later version would drop the earlier change."""

    def __init__(self, workers: int = 6, on_finish: Callable[[dict], None] | None = None):
        self._on_finish = on_finish  # called with the finished job (as get() returns it), e.g. to keep its log
        self._jobs: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="me3d-job")
        # project -> jobs waiting behind the one running for it. A key is present while a job for
        # that project is running (or handed to the pool), even when nothing is waiting.
        self._lanes: dict[str, deque[tuple[dict, Callable[[JobLog], Any]]]] = {}

    def submit(self, kind: str, fn: Callable[[JobLog], Any], *, project: str | None = None,
               label: str | None = None) -> str:
        job_id = "job_" + uuid.uuid4().hex[:10]
        job = {"id": job_id, "kind": kind, "status": "queued", "log": [], "result": None,
               "error": None, "created": now_iso(), "finished": None, "finished_at": None,
               "project": project, "label": label or kind}
        with self._lock:
            self._jobs[job_id] = job
            if project is not None:
                if project in self._lanes:
                    job["log"].append("• Waiting for the change before it on this model to finish")
                    self._lanes[project].append((job, fn))
                    return job_id
                self._lanes[project] = deque()
        self._pool.submit(self._run, job, fn)
        return job_id

    def _start_next(self, project: str | None) -> None:
        if project is None:
            return
        with self._lock:
            lane = self._lanes.get(project)
            if not lane:
                self._lanes.pop(project, None)
                return
            job, fn = lane.popleft()
        self._pool.submit(self._run, job, fn)

    def _run(self, job: dict, fn: Callable[[JobLog], Any]) -> None:
        log = JobLog(job, self._lock)
        with self._lock:
            job["status"] = "running"
        try:
            result = fn(log)
            with self._lock:
                job["result"] = result
                job["status"] = "done"
        except JobError as e:
            with self._lock:
                job["error"] = str(e)
                job["result"] = e.result
                job["status"] = "error"
        except Exception as e:  # unexpected: keep the traceback in the log, a sentence in error
            log(traceback.format_exc())
            with self._lock:
                job["error"] = f"{type(e).__name__}: {e}"
                job["status"] = "error"
        finally:
            with self._lock:
                job["finished"] = now_iso()
                job["finished_at"] = time.time()
            if self._on_finish:
                try:
                    self._on_finish(self.get(job["id"]))
                except Exception:  # keeping a log must never stop the next job
                    traceback.print_exc()
            self._start_next(job["project"])

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            return {"id": job["id"], "kind": job["kind"], "status": job["status"],
                    "log": list(job["log"]), "result": job["result"], "error": job["error"],
                    "created": job["created"], "finished": job["finished"],
                    "project": job["project"], "label": job["label"]}

    def listing(self, recent_s: float = 300) -> list[dict]:
        """Jobs queued or running, and those that finished in the last `recent_s` seconds, oldest
        first, without their logs: `last` is the latest line written for people ("• ")."""
        cutoff = time.time() - recent_s
        with self._lock:
            rows = []
            for job in self._jobs.values():
                if job["status"] not in ("queued", "running") and (job["finished_at"] or 0) <= cutoff:
                    continue
                said = [line[2:] for line in job["log"] if line.startswith("• ")]
                rows.append({"id": job["id"], "kind": job["kind"], "label": job["label"],
                             "project": job["project"], "status": job["status"],
                             "last": said[-1] if said else "", "error": job["error"],
                             "created": job["created"], "finished": job["finished"]})
        return sorted(rows, key=lambda r: r["created"])

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
