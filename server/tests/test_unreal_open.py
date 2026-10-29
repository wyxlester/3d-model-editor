"""Open in Unreal: the editor, with its window, on the level a Unreal test built - and never at the
same time as a test, which rebuilds that very level."""
import dataclasses
import threading
import time

from fastapi.testclient import TestClient

from app.agent import ScriptedAgent
from app.config import unreal_editor
from app.main import create_app
from app.unreal_editor import UnrealEditorWindow
from conftest import FakeBuilder, create_ready, wait_job

LEVEL = "/Game/ME3D/pkg_boat_v1/L_pkg_boat_v1"
COMMITTED = '{\n\t"FileVersion": 3,\n\t"EngineAssociation": "5.8"\n}\n'
# what the editor writes on a machine whose 5.8 is registered only under an ID
REWRITTEN = '{\n\t"FileVersion": 3,\n\t"EngineAssociation": "{B96A885C-4879-0F00-1CDF-12B24F9D70ED}"\n}'


class FakeEditor:
    """Stands in for subprocess.Popen: records the command; 'open' until closed."""

    def __init__(self):
        self.commands, self.open = [], False

    def __call__(self, cmd, **kw):
        self.commands.append(cmd)
        self.open = True
        return self

    def poll(self):
        return None if self.open else 0


def setup(tmp_path, settings, unreal=None):
    engine = tmp_path / "UE_5.8"
    unreal_editor(engine).parent.mkdir(parents=True)
    unreal_editor(engine).write_text("")
    s = dataclasses.replace(settings, repo_root=tmp_path, unreal_dir=engine)
    umap = s.harness_project.parent / "Content/ME3D/pkg_boat_v1/L_pkg_boat_v1.umap"
    umap.parent.mkdir(parents=True)
    umap.write_text("")
    s.harness_project.write_text(COMMITTED)
    editor = FakeEditor()
    app = create_app(s, runner=FakeBuilder(), agent=ScriptedAgent(), llm=lambda p, q: "{}",
                     unreal=unreal or (lambda d, log: {"ok": True, "checks": []}),
                     unreal_window=UnrealEditorWindow(s, popen=editor, keep_interval=0.01))
    return TestClient(app), editor, s


def a_scene(c):
    ship = create_ready(c)
    return c.post("/api/scenes", json={"schema": "me3d.scene_spec/1", "name": "Boat",
                                       "ship": {"project": ship["id"], "version": ship["version"]},
                                       "crew": []}).json()["scene"]


def test_opens_the_test_project_on_that_level_and_frames_it(tmp_path, settings):
    c, editor, s = setup(tmp_path, settings)
    r = c.post("/api/unreal/open", json={"level": LEVEL})
    assert r.status_code == 200, r.text
    cmd = editor.commands[0]
    text = cmd if isinstance(cmd, str) else " ".join(cmd)
    assert str(s.unreal_editor) in text and str(s.harness_project) in text
    assert f"open_test_level.py {LEVEL}" in text and text.count(LEVEL) == 2   # opened, then framed


def eventually(check, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if check():
            return True
        time.sleep(0.01)
    return False


def test_the_project_file_stays_as_committed_whatever_the_editor_writes(tmp_path, settings):
    c, editor, s = setup(tmp_path, settings)
    assert c.post("/api/unreal/open", json={"level": LEVEL}).status_code == 200
    s.harness_project.write_text(REWRITTEN)            # on opening, pointed at this machine's engine
    assert eventually(lambda: s.harness_project.read_text() == COMMITTED)
    s.harness_project.write_text(REWRITTEN)            # and again as it closes
    editor.open = False
    assert eventually(lambda: s.harness_project.read_text() == COMMITTED)


def test_only_levels_a_test_makes_and_only_if_still_on_disk(tmp_path, settings):
    c, editor, _ = setup(tmp_path, settings)
    for bad in ("", "/Game/Other/L_x", "/Game/ME3D/../x/L_x", "/Game/ME3D/a/L_b", f"{LEVEL} -run=x"):
        assert c.post("/api/unreal/open", json={"level": bad}).status_code == 400, bad
    assert c.post("/api/unreal/open", json={"level": "/Game/ME3D/gone/L_gone"}).status_code == 404
    assert editor.commands == []


def test_no_test_while_the_editor_has_the_level_open_and_no_second_editor(tmp_path, settings):
    c, editor, _ = setup(tmp_path, settings)
    scene = a_scene(c)
    assert c.post("/api/unreal/open", json={"level": LEVEL}).status_code == 200
    r = c.post(f"/api/scenes/{scene['id']}/unreal-test")
    assert r.status_code == 409 and "Close Unreal" in r.json()["detail"]
    assert c.post("/api/unreal/open", json={"level": LEVEL}).status_code == 409
    editor.open = False   # the user closed Unreal
    r = c.post(f"/api/scenes/{scene['id']}/unreal-test")
    assert r.status_code == 200 and wait_job(c, r.json()["job_id"])["status"] == "done"


def test_no_opening_while_a_test_is_running(tmp_path, settings):
    release = threading.Event()

    def slow_test(_pkg, _log):
        release.wait(5)
        return {"ok": True, "checks": []}

    c, editor, _ = setup(tmp_path, settings, unreal=slow_test)
    job = c.post(f"/api/scenes/{a_scene(c)['id']}/unreal-test").json()["job_id"]
    r = c.post("/api/unreal/open", json={"level": LEVEL})
    release.set()
    assert r.status_code == 409 and "still running" in r.json()["detail"]
    assert wait_job(c, job)["status"] == "done"
    assert c.post("/api/unreal/open", json={"level": LEVEL}).status_code == 200
