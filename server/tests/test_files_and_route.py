import os

import pytest

from app.main import safe_data_path
from app.router import parse_route, route


def test_safe_data_path_blocks_escape(tmp_path):
    data = tmp_path / "data"
    (data / "packages").mkdir(parents=True)
    (data / "packages" / "a.zip").write_bytes(b"x")
    (tmp_path / "secret.txt").write_text("s")
    assert safe_data_path(data, "packages/a.zip") == (data / "packages" / "a.zip").resolve()
    for bad in ["../secret.txt", "packages/../../secret.txt", str(tmp_path / "secret.txt"),
                "packages", "", "packages/a.zip\x00"]:
        assert safe_data_path(data, bad) is None, bad


def test_safe_data_path_blocks_symlink_out(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (tmp_path / "secret.txt").write_text("s")
    try:
        os.symlink(tmp_path / "secret.txt", data / "link.txt")
    except OSError:  # Windows needs Developer Mode or admin to create symlinks
        pytest.skip("cannot create symlinks here")
    assert safe_data_path(data, "link.txt") is None


def test_files_endpoint(make_client, settings):
    c = make_client()
    (settings.data_dir / "packages").mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "packages" / "p.zip").write_bytes(b"PK")
    (settings.data_dir.parent / "outside.txt").write_text("secret")
    assert c.get("/api/files/packages/p.zip").content == b"PK"
    for url in ["/api/files/..%2Foutside.txt", "/api/files/packages/..%2F..%2Foutside.txt",
                "/api/files/%2e%2e/outside.txt", "/api/files/nothing"]:
        r = c.get(url)
        assert r.status_code == 404 and b"secret" not in r.content, url


PROJECTS = [{"id": "p_1", "kind": "ship", "name": "PC 303"}]


def test_route_parses_model_json():
    out = route("make the searchlight bigger", PROJECTS,
                lambda p, s: 'Sure: {"destination": "ship_build", "reason": "A part change.", "project_id": "p_1"}')
    assert out == {"destination": "ship_build", "reason": "A part change.", "project_id": "p_1"}


def test_route_falls_back_to_clarify():
    for garbage in ["I think ship build", '{"destination": "teleport"}', "{not json}", "", None]:
        assert route("hmm", PROJECTS, lambda p, s, g=garbage: g)["destination"] == "clarify", garbage

    def boom(p, s):
        raise RuntimeError("offline")
    assert route("hmm", PROJECTS, boom)["destination"] == "clarify"


def test_route_drops_unknown_project_id():
    assert parse_route('{"destination": "crew", "reason": "r", "project_id": "p_999"}', {"p_1"})["project_id"] is None


def test_route_endpoint(make_client):
    c = make_client(llm=lambda p, s: '{"destination": "assembly", "reason": "Crew on a ship."}')
    assert c.post("/api/route", json={"message": "put a gunner on the bow"}).json() == {
        "destination": "assembly", "reason": "Crew on a ship.", "project_id": None}
    assert c.post("/api/route", json={"message": ""}).status_code == 400


def test_route_is_told_which_files_are_attached(make_client):
    # Lester's yacht photo: the router only reads text, saw "the image attached", and said none came.
    seen = []
    c = make_client(llm=lambda p, s: seen.append(p) or '{"destination": "ship_research", "reason": "Opening research."}')
    c.post("/api/route", json={"message": "a yacht like the image attached", "attachments": ["yacht.jpg"]})
    assert "Attached files: yacht.jpg" in seen[-1]
    c.post("/api/route", json={"message": "a yacht"})
    assert "Attached files" not in seen[-1]
    assert c.post("/api/route", json={"message": "x", "attachments": "yacht.jpg"}).status_code == 400
