"""Research photographs and close-ups (contract §12).

Lester, 29 Oct: research "doesn't render photos and close up for me"; the standard is the PCG
dossier, whose close-ups are crops "with no generated detail or image reconstruction". These tests
guard what would fail quietly: a fetch that reaches the LAN, a page saved as a photo, a crop that is
off by a pixel or resampled, a resave that drops the photos. No test touches the internet.
"""
import base64
import hashlib
import http.server
import io
import threading

import pytest
from PIL import Image

from app import research_media as rm
from app.agent import ScriptedAgent
from conftest import create_ready, wait_job

PUBLIC = "93.184.216.34"


def png_bytes(w=64, h=48, seed=0, mode="RGB") -> bytes:
    im = Image.new(mode, (w, h))
    im.putdata([((x * 7 + y * 13 + seed) % 256, (x * 3 + seed) % 256, (y * 11) % 256) for y in range(h) for x in range(w)])
    out = io.BytesIO()
    im.save(out, format="PNG")
    return out.getvalue()


class FakeResp:
    def __init__(self, status=200, headers=None, chunks=(b"",)):
        self.status, self.headers, self._chunks = status, headers or {}, chunks
        self.read = 0
        self.closed = False

    def iter_bytes(self):
        for c in self._chunks:
            self.read += 1
            yield c

    def close(self):
        self.closed = True


def never_fetch(url):
    raise AssertionError(f"fetched {url} although it should have been refused first")


def public_resolve(host):
    return [PUBLIC]


# ---- download safety --------------------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "http://localhost/a.png",              # resolves locally to loopback, no internet needed
    "http://127.0.0.1:8787/api/files/x.png",
    "http://10.0.0.5/a.png",
    "http://192.168.1.1/a.png",
    "http://169.254.169.254/latest/meta-data/",  # cloud metadata
    "http://[::1]/a.png",
    "http://[::ffff:127.0.0.1]/a.png",     # loopback wrapped in IPv6
    "http://100.64.0.1/a.png",             # shared CGNAT space
    "http://0.0.0.0/a.png",
])
def test_download_refuses_private_and_local_hosts(url):
    with pytest.raises(rm.MediaError, match="not a public internet address"):
        rm.download(url, fetch=never_fetch)


@pytest.mark.parametrize("url", ["ftp://example.com/a.png", "file:///etc/passwd", "data:image/png;base64,AAAA",
                                 "javascript:alert(1)"])
def test_download_refuses_non_http(url):
    with pytest.raises(rm.MediaError, match="Only http and https"):
        rm.download(url, fetch=never_fetch, resolve=public_resolve)


def test_download_refuses_a_hostname_that_resolves_private():
    with pytest.raises(rm.MediaError, match="not a public"):
        rm.download("https://intranet.example/a.png", fetch=never_fetch, resolve=lambda h: [PUBLIC, "10.1.1.1"])


def test_download_rechecks_every_redirect_host():
    calls = []

    def fetch(url):
        calls.append(url)
        return FakeResp(302, {"location": "http://169.254.169.254/latest/"})

    with pytest.raises(rm.MediaError, match="not a public"):
        rm.download("https://img.example/a.png", fetch=fetch, resolve=public_resolve)
    assert calls == ["https://img.example/a.png"]


def test_download_follows_three_redirects_but_not_four():
    img = png_bytes()

    def chain(n):
        def fetch(url):
            k = int(url.rsplit("/", 1)[1])
            if k < n:
                return FakeResp(301, {"location": f"/{k + 1}"})
            return FakeResp(200, {"content-type": "image/png"}, [img])
        return fetch

    assert rm.download("https://img.example/0", fetch=chain(3), resolve=public_resolve).sha256 == \
        hashlib.sha256(img).hexdigest()
    with pytest.raises(rm.MediaError, match="redirected more than 3"):
        rm.download("https://img.example/0", fetch=chain(4), resolve=public_resolve)


def test_download_refuses_a_page_that_is_not_an_image():
    html = b"<!DOCTYPE html><html><body><img src='x.jpg'></body></html>"
    fetch = lambda url: FakeResp(200, {"content-type": "text/html"}, [html])
    with pytest.raises(rm.MediaError, match="That page is not an image; find the image's own URL"):
        rm.download("https://news.example/article", fetch=fetch, resolve=public_resolve)


def test_download_refuses_bytes_that_are_not_an_image():
    fetch = lambda url: FakeResp(200, {"content-type": "image/png"}, [b"\x00\x01garbage" * 50])
    with pytest.raises(rm.MediaError, match="not a readable image"):
        rm.download("https://img.example/a.png", fetch=fetch, resolve=public_resolve)


def test_download_refuses_an_unsupported_image_type():
    out = io.BytesIO()
    Image.new("RGB", (20, 20)).save(out, format="BMP")
    fetch = lambda url: FakeResp(200, {}, [out.getvalue()])
    with pytest.raises(rm.MediaError, match="only PNG, JPEG, WebP and GIF"):
        rm.download("https://img.example/a.bmp", fetch=fetch, resolve=public_resolve)


def test_download_refuses_oversize_by_header_without_reading():
    resp = FakeResp(200, {"content-length": str(16 * rm.MB)}, [b"x"] * 10)
    with pytest.raises(rm.MediaError, match="limit is 15 MB"):
        rm.download("https://img.example/a.png", fetch=lambda u: resp, resolve=public_resolve)
    assert resp.read == 0 and resp.closed


def test_download_stops_reading_once_over_the_cap():
    resp = FakeResp(200, {}, [b"x" * 1000] * 100)  # no length header: a lying or chunked server
    with pytest.raises(rm.MediaError, match="over"):
        rm.download("https://img.example/a.png", fetch=lambda u: resp, resolve=public_resolve, max_bytes=5000)
    assert resp.read == 6 and resp.closed


def test_download_measures_and_hashes_an_image():
    img = png_bytes(64, 48)
    got = rm.download("https://img.example/a.png", fetch=lambda u: FakeResp(200, {}, [img[:100], img[100:]]),
                      resolve=public_resolve)
    assert (got.mime, got.width, got.height, got.ext) == ("image/png", 64, 48, ".png")
    assert got.sha256 == hashlib.sha256(img).hexdigest() and got.data == img


def test_exif_rotated_photo_is_measured_as_displayed():
    im = Image.new("RGB", (40, 20), (200, 10, 10))
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90° on display
    out = io.BytesIO()
    im.save(out, format="JPEG", exif=exif.tobytes())
    got = rm.identify(out.getvalue())
    assert (got.mime, got.width, got.height) == ("image/jpeg", 20, 40)
    assert Image.open(io.BytesIO(rm.crop(out.getvalue(), [0, 0, 20, 40]))).size == (20, 40)


@pytest.fixture
def local_server():
    """A real HTTP server on loopback serving one PNG and a redirect to it."""
    img = png_bytes(32, 24, seed=5)
    seen = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen.setdefault("paths", []).append(self.path)
            seen["ua"] = self.headers.get("User-Agent")
            if self.path == "/hop":
                self.send_response(302)
                self.send_header("Location", "/a.png")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(img)))
            self.end_headers()
            self.wfile.write(img)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", img, seen
    srv.shutdown()


def test_real_fetch_never_reaches_a_loopback_server(local_server):
    base, _img, seen = local_server
    with pytest.raises(rm.MediaError, match="not a public"):
        rm.download(base + "/a.png")
    assert "paths" not in seen


def test_real_fetch_streams_follows_redirect_and_sends_a_browser_agent(local_server, monkeypatch):
    base, img, seen = local_server
    monkeypatch.setattr(rm, "is_public", lambda addr: True)  # let the loopback test server through
    got = rm.download(base + "/hop")
    assert got.data == img and (got.width, got.height) == (32, 24)
    assert seen["paths"] == ["/hop", "/a.png"]
    assert seen["ua"].startswith("Mozilla/5.0")


# ---- cropping ---------------------------------------------------------------------------------

@pytest.mark.parametrize("fmt", ["PNG", "JPEG", "WEBP", "GIF"])
def test_crop_is_exactly_the_source_region(fmt):
    src = Image.open(io.BytesIO(png_bytes(64, 48, seed=3)))
    buf = io.BytesIO()
    src.save(buf, format=fmt)
    data = buf.getvalue()
    out = Image.open(io.BytesIO(rm.crop(data, [5, 7, 20, 18])))
    assert out.format == "PNG" and out.size == (20, 18)
    decoded = Image.open(io.BytesIO(data))
    decoded.load()
    region = decoded.crop((5, 7, 25, 25))
    assert list(out.getdata()) == list(region.getdata())


@pytest.mark.parametrize("box, msg", [
    ([50, 0, 20, 20], "goes outside"), ([0, 40, 16, 16], "goes outside"), ([-1, 0, 20, 20], "goes outside"),
    ([0, 0, 15, 30], "at least 16"), ([0, 0, 20.5, 20], "whole pixels"), ([0, 0, 20], r"\[x, y, w, h\]"),
    ([0, 0, True, 20], "whole pixels"),
])
def test_crop_refuses_bad_boxes(box, msg):
    with pytest.raises(rm.MediaError, match=msg):
        rm.crop(png_bytes(64, 48), box)


def test_crop_accepts_the_whole_photo_edge_to_edge():
    assert Image.open(io.BytesIO(rm.crop(png_bytes(64, 48), [0, 0, 64, 48]))).size == (64, 48)


# ---- record keeping ---------------------------------------------------------------------------

def test_ids_never_reuse_a_removed_number():
    assert rm.next_id("P", [], 0) == ("P01", 1)
    assert rm.next_id("P", ["P01", "P02"], 2) == ("P03", 3)
    assert rm.next_id("P", ["P01"], 3) == ("P04", 4)  # P02, P03 were removed
    assert rm.next_id("C", ["C09"], 0) == ("C10", 10)


def test_coverage_comes_from_views_and_skips_excluded_photos():
    r = {"photos": [{"id": "P01", "views": ["port", "bow"], "status": "baseline"},
                    {"id": "P02", "views": ["port", "top"], "status": "excluded"},
                    {"id": "P03", "views": ["port", "interior"], "status": "comparison"}],
         "coverage": {"note": "No overhead.", "top": "P99"}}
    rm.refresh(r)
    assert r["photo_count"] == 3
    assert r["coverage"] == {"port": "P01, P03", "starboard": None, "bow": "P01", "stern": None, "top": None,
                             "details": None, "interior": "P03", "note": "No overhead."}


# ---- the AI's tools, through the chat endpoint ------------------------------------------------

GOOD = {"subject": "Malacca Strait fishing sampan", "summary": {"verdict": "Enough to model.", "existing_work": "None"},
        "sources": [{"id": "S01", "title": "Museum page", "url": "https://m.example", "status": "Official",
                     "what_it_proves": "Hull form"}],
        "claims": [{"claim": "Single outboard", "sources": ["S01", "P01"], "status": "Official + Photographed",
                    "consequence_for_model": "One motor"}],
        "open_questions": [], "articulation": []}


def photo_args(url="https://upload.example/sampan.png", **kw):
    return dict({"image_url": url, "title": "Sampan at Malacca river", "vessel": "Malacca sampan",
                 "views": ["port", "bow"], "credit": "Jane Doe / Wikimedia Commons", "date": "Taken 2019",
                 "observation": "Planked hull, outboard on transom", "source": "https://commons.example/File:S.png",
                 "status": "baseline", "rights": "CC BY-SA 4.0", "modelling_use": "hull shape"}, **kw)


@pytest.fixture
def fake_web(monkeypatch):
    """image_url -> bytes, served through the real identify() so measurement and hashing are real."""
    web = {}

    def download(url):
        if url not in web:
            raise rm.MediaError("The image URL answered HTTP 404, not the image.")
        return rm.identify(web[url])
    monkeypatch.setattr(rm, "download", download)
    return web


def test_add_photo_stores_measures_and_serves_the_file(make_client, fake_web):
    img = png_bytes(64, 48, seed=1)
    fake_web["https://upload.example/sampan.png"] = img
    agent = ScriptedAgent([("add_photo", photo_args())])
    c = make_client(agent=agent)
    p = create_ready(c)
    wait_job(c, c.post(f"/api/projects/{p['id']}/chat", json={"message": "x", "mode": "research"}).json()["job_id"])
    name, text, err = agent.results[0]
    assert not err and "Added P01" in text, text
    proj = c.get(f"/api/projects/{p['id']}").json()
    ph = proj["research"]["photos"][0]
    assert ph["id"] == "P01" and (ph["width"], ph["height"]) == (64, 48)
    assert ph["sha256"] == hashlib.sha256(img).hexdigest() and ph["image_url"] == "https://upload.example/sampan.png"
    assert ph["file"] == f"/api/files/projects/{p['id']}/research/photos/P01.png"
    assert proj["research"]["photo_count"] == 1 and proj["research_rev"] == 1
    assert proj["research"]["coverage"]["port"] == "P01" and proj["research"]["coverage"]["top"] is None
    served = c.get(ph["file"])
    assert served.status_code == 200 and served.content == img and served.headers["content-type"] == "image/png"


def test_add_photo_dedupes_by_hash_and_ids_increment(make_client, fake_web):
    a, b, d = png_bytes(seed=1), png_bytes(seed=2), png_bytes(seed=3)
    fake_web.update({"https://x.example/a.png": a, "https://mirror.example/a-copy.png": a,
                     "https://x.example/b.png": b, "https://x.example/d.png": d})
    agent = ScriptedAgent([
        ("add_photo", photo_args("https://x.example/a.png")),
        ("add_photo", photo_args("https://mirror.example/a-copy.png")),
        ("add_photo", photo_args("https://x.example/b.png")),
        ("remove_photo", {"id": "P02"}),
        ("add_photo", photo_args("https://x.example/d.png")),
    ])
    c = make_client(agent=agent)
    p = create_ready(c)
    wait_job(c, c.post(f"/api/projects/{p['id']}/chat", json={"message": "x", "mode": "research"}).json()["job_id"])
    texts = [t for _, t, _ in agent.results]
    assert "already stored as P01" in texts[1] and not agent.results[1][2]
    assert "Added P02" in texts[2] and "Added P03" in texts[4], texts
    proj = c.get(f"/api/projects/{p['id']}").json()
    assert [x["id"] for x in proj["research"]["photos"]] == ["P01", "P03"]
    assert proj["research"]["photo_count"] == 2
    assert proj["research_rev"] == 4  # three adds and a removal; the duplicate changed nothing


def test_save_research_keeps_photos_and_accepts_photo_citations(make_client, fake_web):
    fake_web["https://upload.example/sampan.png"] = png_bytes()
    research = dict(GOOD, photos=[], closeups=[], photo_count=99, coverage={"note": "AI overwrite"})
    agent = ScriptedAgent([("add_photo", photo_args()), ("set_coverage", {"note": "No overhead view."}),
                           ("save_research", {"research": research})])
    c = make_client(agent=agent)
    p = create_ready(c)
    wait_job(c, c.post(f"/api/projects/{p['id']}/chat", json={"message": "x", "mode": "research"}).json()["job_id"])
    assert not agent.results[2][2], agent.results[2]
    assert "kept as stored" in agent.results[2][1]
    r = c.get(f"/api/projects/{p['id']}").json()["research"]
    assert [x["id"] for x in r["photos"]] == ["P01"] and r["photo_count"] == 1
    assert r["coverage"]["note"] == "No overhead view." and r["subject"] == GOOD["subject"]


def test_save_research_refuses_a_citation_of_a_photo_not_stored(make_client):
    agent = ScriptedAgent([("save_research", {"research": GOOD})])  # cites P01, nothing stored
    c = make_client(agent=agent)
    p = create_ready(c)
    wait_job(c, c.post(f"/api/projects/{p['id']}/chat", json={"message": "x", "mode": "research"}).json()["job_id"])
    assert agent.results[0][2] and "unknown sources ['P01']" in agent.results[0][1]


def test_add_photo_from_an_upload_is_credited_to_lester(make_client, monkeypatch):
    monkeypatch.setattr(rm, "download", never_fetch)
    img = png_bytes(40, 30, seed=9)
    c = make_client()
    p = create_ready(c)
    up = c.post(f"/api/projects/{p['id']}/uploads", json={"name": "my sampan.png", "type": "image/png",
                                                          "data": base64.b64encode(img).decode()}).json()["attachment"]
    args = {k: v for k, v in photo_args().items() if k not in ("image_url", "credit", "date", "source")}
    agent = ScriptedAgent([("add_photo", dict(args, upload_id=up["id"])),
                           ("add_photo", dict(args, upload_id="u_00000000"))])
    c2 = make_client(agent=agent)
    wait_job(c2, c2.post(f"/api/projects/{p['id']}/chat", json={"message": "x", "mode": "research"}).json()["job_id"])
    assert not agent.results[0][2], agent.results[0]
    ph = c2.get(f"/api/projects/{p['id']}").json()["research"]["photos"][0]
    assert ph["credit"] == "Supplied by Lester" and ph["upload"] == up["id"] and ph["image_url"] is None
    assert "my sampan.png" in ph["source"] and ph["sha256"] == hashlib.sha256(img).hexdigest()
    assert agent.results[1][2] and "No attachment 'u_00000000'" in agent.results[1][1]


def test_add_closeup_stores_the_exact_crop_and_refuses_out_of_bounds(make_client, fake_web):
    img = png_bytes(64, 48, seed=4)
    fake_web["https://upload.example/sampan.png"] = img
    agent = ScriptedAgent([
        ("add_photo", photo_args()),
        ("add_closeup", {"photo": "P01", "box": [10, 12, 30, 20], "title": "Outboard",
                         "what_it_shows": "One outboard on the transom", "claims": ["Single outboard"]}),
        ("add_closeup", {"photo": "P01", "box": [50, 0, 30, 20], "title": "x", "what_it_shows": "y"}),
        ("add_closeup", {"photo": "P07", "box": [0, 0, 20, 20], "title": "x", "what_it_shows": "y"}),
    ])
    c = make_client(agent=agent)
    p = create_ready(c)
    wait_job(c, c.post(f"/api/projects/{p['id']}/chat", json={"message": "x", "mode": "research"}).json()["job_id"])
    assert not agent.results[1][2] and "Close-up C01" in agent.results[1][1]
    assert agent.results[2][2] and "goes outside the photo, which is 64x48" in agent.results[2][1]
    assert agent.results[3][2] and "No photograph 'P07'" in agent.results[3][1]
    r = c.get(f"/api/projects/{p['id']}").json()["research"]
    cu = r["closeups"]
    assert len(cu) == 1 and cu[0] == {"id": "C01", "photo": "P01", "box": [10, 12, 30, 20], "title": "Outboard",
                                      "what_it_shows": "One outboard on the transom", "claims": ["Single outboard"],
                                      "file": f"/api/files/projects/{p['id']}/research/closeups/C01.png"}
    crop = Image.open(io.BytesIO(c.get(cu[0]["file"]).content))
    assert crop.size == (30, 20)
    assert list(crop.getdata()) == list(Image.open(io.BytesIO(img)).crop((10, 12, 40, 32)).getdata())


def test_removing_a_photo_removes_its_closeups_and_files(make_client, fake_web, settings):
    fake_web["https://upload.example/sampan.png"] = png_bytes()
    agent = ScriptedAgent([("add_photo", photo_args()),
                           ("add_closeup", {"photo": "P01", "box": [0, 0, 20, 20], "title": "a", "what_it_shows": "b"}),
                           ("remove_photo", {"id": "P01"})])
    c = make_client(agent=agent)
    p = create_ready(c)
    wait_job(c, c.post(f"/api/projects/{p['id']}/chat", json={"message": "x", "mode": "research"}).json()["job_id"])
    assert "close-ups C01" in agent.results[2][1]
    r = c.get(f"/api/projects/{p['id']}").json()["research"]
    assert r["photos"] == [] and r["closeups"] == [] and r["photo_count"] == 0
    media = settings.data_dir / "projects" / p["id"] / "research"
    assert not list(media.glob("*/*.png"))


def test_photo_tools_show_the_ai_the_picture_and_exist_only_in_research(make_client, fake_web, settings):
    from app.agent import AgentSession
    from app.store import ProjectStore
    fake_web["https://upload.example/sampan.png"] = png_bytes()
    p = create_ready(make_client())
    store = ProjectStore(settings.projects_dir)
    s = AgentSession(store, None, _NoCatalog(), p["id"], lambda m: None, settings.repo_root, mode="research")
    blocks, err = s.call_content("add_photo", photo_args())
    assert not err and [b["type"] for b in blocks] == ["text", "image"]
    assert Image.open(io.BytesIO(base64.b64decode(blocks[1]["data"]))).size == (64, 48)
    research_tools = {n for n, _, _ in s.tool_specs("research")}
    assert {"add_photo", "remove_photo", "add_closeup", "remove_closeup", "set_coverage"} <= research_tools
    assert not research_tools & {n for n, _, _ in s.tool_specs("build")}
    text, err = s.call("add_closeup", {"photo": "P01", "box": [0, 0, 16, 16], "title": "a", "what_it_shows": "b"})
    assert not err and "C01" in text  # call() still returns plain text for the existing callers


class _NoCatalog:
    def entry(self, kind, base):
        from app.catalog import CatalogError
        raise CatalogError(base)


def test_claim_status_may_combine_two_statuses():
    from app.agent import validate_research
    ok = dict(GOOD, claims=[dict(GOOD["claims"][0], status="Official + Photographed")])
    assert validate_research(ok, ["P01"]) == []
    for bad in ("Official + photo", "Official + Inference + Unverified", "verified"):
        r = dict(GOOD, claims=[dict(GOOD["claims"][0], status=bad)])
        assert any("claims[0].status" in e for e in validate_research(r, ["P01"])), bad


def test_a_403_is_retried_once_with_an_honest_agent_on_the_same_checked_url():
    img, calls = png_bytes(), []

    def fetch(url, user_agent=rm.USER_AGENT):
        calls.append((url, user_agent))
        if user_agent == rm.USER_AGENT:
            return FakeResp(403, {}, [b"Please respect our robot policy"])
        return FakeResp(200, {}, [img])

    got = rm.download("https://upload.example/a.jpg", fetch=fetch, resolve=public_resolve)
    assert got.data == img
    assert [ua for _, ua in calls] == [rm.USER_AGENT, rm.TOOL_USER_AGENT]
    assert rm.TOOL_USER_AGENT.startswith("me3d-research/1 (") and "http" in rm.TOOL_USER_AGENT
    always = lambda url, user_agent=rm.USER_AGENT: FakeResp(403, {}, [b"no"])
    with pytest.raises(rm.MediaError, match="HTTP 403"):
        rm.download("https://upload.example/a.jpg", fetch=always, resolve=public_resolve)
