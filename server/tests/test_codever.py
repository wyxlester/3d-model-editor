"""A server older than its code says so (contract §10, health.code; app/codever.py).

On 15 Nov a backend started on 14 Nov drafted MAK settings with two-day-old rules, so USS Abraham Lincoln's
weapon mounts were saved without part codes and its export came out with no weapons. Nothing said the server
was stale."""
import os
import time

from app import codever
from app.codever import CodeVersion
from conftest import wait_job
from test_mak import GOOD, create_ready, fake_mak, scene_for, vrf  # noqa: F401


def write(root, rel, text):
    f = root / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)
    return f


def test_a_changed_or_new_file_makes_it_stale(tmp_path, monkeypatch):
    monkeypatch.setattr(codever, "CACHE_S", 0)
    f = write(tmp_path, "mak/pipeline/draft_manifest.py", "RULES = 1\n")
    write(tmp_path, "server/app/main.py", "x = 1\n")
    write(tmp_path, "mak/tests/test_x.py", "")                 # tests and caches are not the server's code
    cv = CodeVersion(tmp_path)
    assert cv.status()["stale"] is False and cv.warning() is None
    write(tmp_path, "mak/tests/test_y.py", "")
    write(tmp_path, "server/app/__pycache__/main.cpython-311.pyc", "")
    assert cv.status()["stale"] is False
    f.write_text("RULES = 2  # the weapon mount rule\n")
    os.utime(f, ns=(time.time_ns(), time.time_ns() + 10**9))
    write(tmp_path, "catalog/articulation/roles.json", "{}")
    s = cv.status()
    assert s["stale"] is True and s["changed"] == ["catalog/articulation/roles.json", "mak/pipeline/draft_manifest.py"]
    assert "restart the editor (start.ps1)" in cv.warning()


def test_health_and_the_export_report_say_so(make_client, vrf, monkeypatch):
    c = make_client(mak=fake_mak())
    code = c.get("/api/health").json()["code"]
    assert code["stale"] is False and code["started"].endswith("Z")
    ship = create_ready(c)
    c.patch(f"/api/projects/{ship['id']}/mak", json=GOOD)
    scene = scene_for(c, ship)

    monkeypatch.setattr(codever, "CACHE_S", 0)
    monkeypatch.setattr(codever, "snapshot", lambda root: {"mak/pipeline/draft_manifest.py": (1, 1)})
    assert c.get("/api/health").json()["code"]["stale"] is True
    job = wait_job(c, c.post(f"/api/scenes/{scene['id']}/mak-export").json()["job_id"])
    first = job["result"]["checks"][0]
    assert (first["name"], first["level"], first["pass"]) == ("editor:server-stale", "warning", True)
    assert any("restart the editor" in line for line in job["log"])
