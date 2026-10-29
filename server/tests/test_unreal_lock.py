"""Two Unreal tests at once must not overlap: they import into one shared harness project, and
an overlap overwrites the other run's assets without any error."""
import threading

from app.config import Settings, unreal_cmd
from app.main import make_unreal_runner

FAKE = """import json, os, sys, time
pkg = sys.argv[1]
with open(os.environ["TRACE"], "a") as f:
    f.write(f"start {pkg}\\n")
time.sleep(0.4)
with open(os.environ["TRACE"], "a") as f:
    f.write(f"end {pkg}\\n")
with open(os.path.join(pkg, "unreal_import_report.json"), "w") as f:
    json.dump({"ok": True, "checks": [], "engine_arg": sys.argv[sys.argv.index("--engine") + 1]}, f)
"""


def test_unreal_runs_never_overlap(tmp_path, monkeypatch):
    (tmp_path / "unreal").mkdir()
    (tmp_path / "unreal" / "run_import_test.py").write_text(FAKE)
    engine = tmp_path / "UE_5.8"
    unreal_cmd(engine).parent.mkdir(parents=True)
    unreal_cmd(engine).write_text("")   # the runner refuses to start without the editor binary
    trace = tmp_path / "trace.txt"
    monkeypatch.setenv("TRACE", str(trace))
    run = make_unreal_runner(Settings(repo_root=tmp_path, data_dir=tmp_path / "data", unreal_dir=engine))

    pkgs = [tmp_path / f"pkg{i}" for i in range(2)]
    for p in pkgs:
        p.mkdir()
    reports = []
    threads = [threading.Thread(target=lambda p=p: reports.append(run(p, lambda _l: None))) for p in pkgs]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    events = [line.split()[0] for line in trace.read_text().splitlines()]
    assert events == ["start", "end", "start", "end"], events
    assert [r["engine_arg"] for r in reports] == [str(engine)] * 2   # the launcher is told which engine
