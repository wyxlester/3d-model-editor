"""Working on several models at once (contract §10, jobs): jobs on different projects run side by
side; jobs on the same project run one after another, so a second change starts from the first
one's result instead of overwriting it; GET /api/jobs says what is going on, for every page."""
import threading
import time

from app.agent import ScriptedAgent
from app.jobs import JobManager
from conftest import create_ready, wait_job


def wait_until(cond, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_jobs_on_one_project_queue_and_other_projects_do_not_wait():
    jm = JobManager(workers=4)
    gate, order, running = threading.Event(), [], []

    def job(name):
        def fn(log):
            running.append(name)
            assert gate.wait(5)
            order.append(name)
            return name
        return fn

    a1 = jm.submit("chat", job("a1"), project="p_a", label="first on A")
    a2 = jm.submit("chat", job("a2"), project="p_a", label="second on A")
    b1 = jm.submit("chat", job("b1"), project="p_b", label="on B")
    assert wait_until(lambda: sorted(running) == ["a1", "b1"])  # A's second waits; B does not
    assert jm.get(a2)["status"] == "queued"
    assert any("Waiting for the change before it" in line for line in jm.get(a2)["log"])
    gate.set()
    assert wait_until(lambda: all(jm.get(j)["status"] == "done" for j in (a1, a2, b1)))
    assert order.index("a1") < order.index("a2")
    jm.shutdown()


def test_a_failed_job_frees_its_project_for_the_next():
    jm = JobManager(workers=2)

    def boom(log):
        raise RuntimeError("no")
    first = jm.submit("chat", boom, project="p_a")
    second = jm.submit("chat", lambda log: "ok", project="p_a")
    assert wait_until(lambda: jm.get(second)["status"] == "done")
    assert jm.get(first)["status"] == "error"
    jm.shutdown()


def test_listing_shows_active_and_recently_finished_jobs():
    jm = JobManager(workers=2)
    gate = threading.Event()

    def slow(log):
        log("raw detail")
        log("• Building version 2 in Blender: AI edit")
        log("more raw detail")
        assert gate.wait(5)
    running = jm.submit("chat", slow, project="p_a", label="Chat: make the hull red")
    done = jm.submit("export", lambda log: None, label="Export")
    assert wait_until(lambda: jm.get(done)["status"] == "done" and jm.get(running)["status"] == "running")
    assert wait_until(lambda: any(j["last"].startswith("Building version 2") for j in jm.listing()))
    rows = {j["id"]: j for j in jm.listing()}
    assert rows[running]["project"] == "p_a" and rows[running]["label"] == "Chat: make the hull red"
    assert rows[running]["last"] == "Building version 2 in Blender: AI edit"  # the latest line for people
    assert rows[done]["status"] == "done" and rows[done]["project"] is None
    assert jm.listing(recent_s=0) and all(j["status"] in ("queued", "running") for j in jm.listing(recent_s=0))
    gate.set()
    jm.shutdown()


class GatedAgent(ScriptedAgent):
    """Each run waits for `go`; counts how many runs overlap. Steps are chosen by the prompt's text."""

    def __init__(self, steps_by_word):
        super().__init__()
        self.steps_by_word = steps_by_word
        self.go = threading.Event()
        self.lock = threading.Lock()
        self.running = self.most = self.runs = 0

    def run(self, session, prompt, mode, log):
        with self.lock:
            self.running += 1
            self.runs += 1
            self.most = max(self.most, self.running)
        try:
            assert self.go.wait(10)
            text = str(prompt).rsplit("User:", 1)[-1]  # this message, not the chat history before it
            self.steps = next(s for word, s in self.steps_by_word.items() if word in text)
            return super().run(session, prompt, mode, log)
        finally:
            with self.lock:
                self.running -= 1


HIDE = ("set_part_override", {"part": "Roof__Searchlight", "override": {"hidden": True}})
BEAM = ("set_params", {"patch": {"beam_m": 30}})


def test_two_models_change_at_once_and_one_models_changes_stack(make_client):
    agent = GatedAgent({"searchlight": [HIDE, ("rebuild", {})], "beam": [BEAM, ("rebuild", {})]})
    c = make_client(agent=agent)
    container, sampan = create_ready(c), create_ready(c)
    chat = lambda p, text: c.post(f"/api/projects/{p['id']}/chat", json={"message": text}).json()["job_id"]  # noqa: E731

    first = chat(container, "hide the searchlight")
    other = chat(sampan, "hide the searchlight")
    second = chat(container, "beam 30 please")  # sent while the first is still running
    assert wait_until(lambda: agent.running == 2)
    assert c.get(f"/api/jobs/{second}").json()["status"] == "queued"

    listed = {j["id"]: j for j in c.get("/api/jobs").json()["jobs"]}
    assert listed[first]["project"] == container["id"] and listed[first]["project_name"] == container["name"]
    assert listed[first]["project_kind"] == "ship"
    assert listed[first]["label"] == "Chat: hide the searchlight"
    assert listed[second]["status"] == "queued"

    agent.go.set()
    for j in (first, other, second):
        assert wait_job(c, j)["status"] == "done"
    assert agent.most == 2  # the two models overlapped; the container's two changes did not
    spec = c.get(f"/api/projects/{container['id']}").json()["spec"]
    # The second change started from the first one's result, so both are there.
    assert spec["overrides"]["Roof__Searchlight"] == {"hidden": True} and spec["params"]["beam_m"] == 30
    assert c.get(f"/api/projects/{container['id']}").json()["version"] == 3


def test_jobs_are_labelled_for_people(make_client):
    c = make_client()
    p = create_ready(c)
    pin = c.post(f"/api/projects/{p['id']}/pins", json={"part": "Roof__Searchlight", "text": "raise it"}).json()
    wait_job(c, pin["job_id"])
    edit = c.post(f"/api/projects/{p['id']}/spec", json={"patch": {"params": {"beam_m": 31}}}).json()["job_id"]
    wait_job(c, edit)
    labels = {j["id"]: j["label"] for j in c.get("/api/jobs").json()["jobs"]}
    assert labels[pin["job_id"]] == "Comment on Roof › Searchlight"
    assert labels[edit] == "Rebuilding with your changes"
    assert any(label == "First build of Container ship" for label in labels.values())
