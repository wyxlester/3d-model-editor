"""Building an approved design plan in steps the user can watch: the plan's hull first, then the
details a group at a time, each group rebuilt as its own version (contract §12, Watching the build)."""
from conftest import wait_job
from test_custom_ship import drafted, with_custom  # noqa: F401


def op(name: str, desc: str):
    return ("add_custom_op", {"description": desc, "code": f"o = me3d.box('{name}', size=(0.2, 1, 1))"})


def test_the_details_are_added_a_group_at_a_time_each_its_own_version(make_client, with_custom):
    c, pid, agent = drafted(make_client)
    agent.steps = [op("Arch__Radar", "radar arch"), ("rebuild", {}),
                   op("Rail__Port", "port rail"), op("Rail__Stbd", "starboard rail"), ("rebuild", {})]
    job = wait_job(c, c.post(f"/api/projects/{pid}/design-plan/approve").json()["job_id"])
    assert job["status"] == "done", job
    assert job["result"]["built_versions"] == [2, 3, 4]
    # The prompt asks for the details in groups, rebuilt after each, so the user sees the ship take shape.
    assert "rebuild after each group" in agent.prompts[-1]
    # Each version lands in the log as it is built, for the Build screen to follow.
    for n in (2, 3, 4):
        assert f"• Version {n} built" in job["log"]
    p = c.get(f"/api/projects/{pid}").json()
    assert [v["n"] for v in p["versions"]] == [1, 2, 3, 4] and p["version"] == 4


def test_a_long_job_keeps_every_line_written_for_people():
    # Blender's output filled the 400-line log during a plan build and pushed out "• Version 2 built",
    # so the Build screen lost the first versions. Technical lines go first; the people's lines stay.
    import threading
    from app.jobs import MAX_LOG_LINES, JobLog
    job = {"log": []}
    log = JobLog(job, threading.Lock())
    log("• Version 2 built")
    for i in range(MAX_LOG_LINES * 2):
        log(f"blender: line {i}")
    log("• Version 3 built")
    assert len(job["log"]) == MAX_LOG_LINES
    assert job["log"][0] == "• Version 2 built" and job["log"][-1] == "• Version 3 built"
    assert job["log"][-2] == f"blender: line {MAX_LOG_LINES * 2 - 1}"  # the newest technical lines stay too
    # When only people's lines are left, the oldest of those go.
    for i in range(MAX_LOG_LINES + 5):
        log(f"• step {i}")
    assert len(job["log"]) == MAX_LOG_LINES and job["log"][-1] == f"• step {MAX_LOG_LINES + 4}"
