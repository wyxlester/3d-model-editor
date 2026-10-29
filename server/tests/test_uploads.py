"""Chat attachments (contract §10): what is accepted, and that the AI receives the file itself."""
import base64

import pytest

from app.agent import ScriptedAgent
from app.uploads import MB, UploadRejected, check_size, content_blocks, decode, resolve_type
from conftest import create_ready, wait_job

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


@pytest.mark.parametrize("name,mime,expect", [
    ("bow.png", "image/png", "image/png"),
    ("bow.JPG", "image/jpg", "image/jpeg"),          # a common wrong spelling of the type
    ("notes.md", "", "text/markdown"),               # browsers often send no type for .md
    ("data.csv", "application/octet-stream", "text/csv"),
    ("drawing.pdf", "application/pdf", "application/pdf"),
])
def test_accepted_types(name, mime, expect):
    assert resolve_type(name, mime) == expect


@pytest.mark.parametrize("name,mime,says", [
    ("spec.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "PDF"),
    ("IMG_0001.HEIC", "image/heic", "JPEG"),
    ("model.glb", "model/gltf-binary", "only pictures"),
    ("fake.png.exe", "application/octet-stream", "only pictures"),  # extension only rescues text files
])
def test_refused_types_say_what_to_do(name, mime, says):
    with pytest.raises(UploadRejected, match=says):
        resolve_type(name, mime)


def test_size_limits_are_the_models():
    check_size("a.png", "image/png", 5 * MB)
    with pytest.raises(UploadRejected, match="5 MB"):
        check_size("a.png", "image/png", 5 * MB + 1)
    check_size("a.pdf", "application/pdf", 20 * MB)
    with pytest.raises(UploadRejected, match="empty"):
        check_size("a.txt", "text/plain", 0)


def test_decode_accepts_data_urls_and_refuses_garbage():
    assert decode("data:image/png;base64," + b64(PNG)) == PNG
    with pytest.raises(UploadRejected):
        decode("not base64!!")


def test_content_blocks_put_each_file_before_the_text():
    blocks = content_blocks("Match this", [
        ({"name": "bow.png", "type": "image/png"}, PNG),
        ({"name": "ga.pdf", "type": "application/pdf"}, b"%PDF-1.4"),
        ({"name": "n.txt", "type": "text/plain"}, "hull 25 m".encode()),
    ])
    assert [b["type"] for b in blocks] == ["text", "image", "text", "document", "text", "text", "text"]
    assert blocks[1]["source"] == {"type": "base64", "media_type": "image/png", "data": b64(PNG)}
    assert blocks[3]["source"]["media_type"] == "application/pdf"
    assert blocks[5]["text"] == "hull 25 m" and blocks[-1]["text"] == "Match this"


def test_upload_then_chat_sends_the_picture_to_the_ai(make_client):
    agent = ScriptedAgent(reply="Seen it.")
    c = make_client(agent=agent)
    p = create_ready(c)
    up = c.post(f"/api/projects/{p['id']}/uploads", json={"name": "../../bow.png", "type": "image/png", "data": b64(PNG)})
    assert up.status_code == 200, up.text
    a = up.json()["attachment"]
    assert a["name"] == "bow.png" and a["size"] == len(PNG)  # path stripped from the name
    assert c.get(a["url"]).content == PNG
    job = wait_job(c, c.post(f"/api/projects/{p['id']}/chat", json={"message": "Match this bow", "attachments": [a["id"]]}).json()["job_id"])
    assert job["status"] == "done", job
    prompt = agent.prompts[0]
    assert isinstance(prompt, list) and prompt[1]["type"] == "image" and prompt[1]["source"]["data"] == b64(PNG)
    assert prompt[-1]["text"].endswith("User: Match this bow")
    msgs = c.get(f"/api/projects/{p['id']}").json()["messages"]
    assert msgs[-2]["attachments"][0]["id"] == a["id"]
    # the next turn names it in the history but does not send the picture again
    wait_job(c, c.post(f"/api/projects/{p['id']}/chat", json={"message": "and the stern?"}).json()["job_id"])
    assert isinstance(agent.prompts[1], str) and "[attached: bow.png]" in agent.prompts[1]


def test_bad_uploads_are_refused_with_400(make_client):
    c = make_client(agent=ScriptedAgent())
    p = create_ready(c)
    r = c.post(f"/api/projects/{p['id']}/uploads", json={"name": "spec.docx", "type": "application/msword", "data": b64(b"x")})
    assert r.status_code == 400 and "PDF" in r.json()["detail"]
    r = c.post(f"/api/projects/{p['id']}/chat", json={"message": "hi", "attachments": ["u_deadbeef"]})
    assert r.status_code == 400 and "attach it again" in r.json()["detail"]
    r = c.post(f"/api/projects/{p['id']}/chat", json={"message": "hi", "attachments": ["../../etc/passwd"]})
    assert r.status_code == 400
