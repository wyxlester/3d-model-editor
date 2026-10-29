"""Categories for finding ships and crew (contract §5, §6, §10): defaults from catalog/categories.json,
each catalog base's default category, a project's own category, and PATCH to change it or rename."""
import json

from conftest import create_ready, wait_job

CATEGORIES = {"ship": ["Commercial vessels", "Military vessels", "Fishing and small craft", "Other"],
              "crew": ["Navy", "Merchant crew", "Other"]}


def with_categories(catalog_dir):
    (catalog_dir / "categories.json").write_text(json.dumps(CATEGORIES))
    for folder, name, category in (("ships", "container", "Commercial vessels"), ("crew", "male_default", "Merchant crew")):
        path = catalog_dir / folder / f"{name}.json"
        data = json.loads(path.read_text())
        data["category"] = category
        path.write_text(json.dumps(data))


def summary(c, pid):
    return next(p for p in c.get("/api/projects").json() if p["id"] == pid)


def test_catalog_lists_the_default_categories_and_each_bases_category(make_client, catalog_dir):
    with_categories(catalog_dir)
    cat = make_client().get("/api/catalog").json()
    assert cat["categories"] == CATEGORIES
    assert cat["ships"][0]["category"] == "Commercial vessels"
    assert cat["crew"][0]["category"] == "Merchant crew"


def test_catalog_without_categories_file_still_works(make_client):
    cat = make_client().get("/api/catalog").json()
    assert cat["categories"] == {"ship": [], "crew": []} and cat["ships"][0]["category"] is None


def test_category_is_not_part_of_the_spec(make_client, catalog_dir):
    with_categories(catalog_dir)
    c = make_client()
    p = create_ready(c)
    assert "category" not in p["spec"]
    assert "category" not in c.builder.calls[0][1]  # Blender never sees it


def test_new_projects_take_their_bases_category_and_variants_their_sources(make_client, catalog_dir):
    with_categories(catalog_dir)
    c = make_client()
    ship, crew = create_ready(c), create_ready(c, "crew", "male_default")
    assert ship["category"] == "Commercial vessels" and summary(c, ship["id"])["category"] == "Commercial vessels"
    assert summary(c, crew["id"])["category"] == "Merchant crew"
    c.patch(f"/api/projects/{ship['id']}", json={"category": "Military vessels"})
    r = c.post(f"/api/projects/{ship['id']}/variants", json={"name": "Gunboat"}).json()
    wait_job(c, r["job_id"])
    assert summary(c, r["project"]["id"])["category"] == "Military vessels"


def test_a_project_from_before_categories_falls_back_to_its_bases(make_client, catalog_dir, settings):
    with_categories(catalog_dir)
    c = make_client()
    p = create_ready(c)
    path = settings.data_dir / "projects" / p["id"] / "project.json"
    data = json.loads(path.read_text())
    del data["category"]
    path.write_text(json.dumps(data))
    assert summary(c, p["id"])["category"] == "Commercial vessels"
    assert c.get(f"/api/projects/{p['id']}").json()["category"] == "Commercial vessels"


def test_patch_sets_category_and_name(make_client, catalog_dir):
    with_categories(catalog_dir)
    c = make_client()
    p = create_ready(c)
    out = c.patch(f"/api/projects/{p['id']}", json={"category": "  Military vessels ", "name": "PC 303"}).json()["project"]
    assert out["category"] == "Military vessels" and out["name"] == "PC 303"
    assert summary(c, p["id"])["name"] == "PC 303"
    assert out["spec"]["name"] == "Container ship"  # the spec's name (exported metadata) is not renamed
    # A category of the user's own is fine; it is a label, not a closed list.
    assert c.patch(f"/api/projects/{p['id']}", json={"category": "Pilot boats"}).json()["project"]["category"] == "Pilot boats"


def test_patch_refuses_bad_values(make_client):
    c = make_client()
    p = create_ready(c)
    for body in ({}, {"category": ""}, {"category": "   "}, {"category": 5}, {"name": ""}, {"category": "x" * 61},
                 {"version": 3}):
        assert c.patch(f"/api/projects/{p['id']}", json=body).status_code == 400, body
    assert c.patch("/api/projects/p_nothere", json={"category": "Other"}).status_code == 404
