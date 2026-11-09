import base64
import io
import json
import zipfile

from app.services import files
from conftest import inject
from test_chat_stream import new_chat, parse_sse

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40


def b64(b):
    return base64.b64encode(b).decode()


def make_docx(text):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
                   f'<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Title</w:t></w:r></w:p><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>')
    return buf.getvalue()


def make_pdf(text):
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(72, 720, text)
    c.save()
    return buf.getvalue()


def test_extract_types():
    assert files.extract("a.md", b"# Hi\nbody text here").text.startswith("# Hi")
    assert "restart the pool" in files.extract("r.docx", make_docx("restart the pool now")).text
    assert files.extract("r.docx", make_docx("x y z w")).text.startswith("## Title")
    assert "restart the pool" in files.extract("r.pdf", make_pdf("restart the pool now")).text
    assert files.extract("p.png", PNG, allow_images=True).kind == "image"


def test_extract_rejects():
    import pytest
    for name, data in [("a.exe", b"MZ\x00\x00" + b"\x00" * 10), ("a.bin", b"\x00\x01\x02"), ("a.pdf", b"%PDF-1.4 junk"), ("a.docx", b"notzip")]:
        with pytest.raises(files.FileError):
            files.extract(name, data)
    with pytest.raises(files.FileError):
        files.extract("p.png", PNG)                                  # images not allowed here
    with pytest.raises(files.FileError):
        files.extract("big.png", PNG + b"0" * (files.MAX_IMAGE_BYTES + 1), allow_images=True)
    with pytest.raises(files.FileError):
        files.extract("x.weird", b"hello world")


def test_filename_is_sanitised():
    assert files.extract("../../etc/pass wd<>.txt", b"hello there friend").name == "pass wd__.txt"


def test_kb_upload(env):
    r = env.client.post("/api/kb/upload", json={"name": "Pool runbook.md", "data": b64(b"## Fix\n- restart the pool service now")})
    assert r.status_code == 201
    docs = env.client.get("/api/kb/docs").json()
    assert any(d["title"] == "Pool runbook" and d["source"] == "file:Pool runbook.md" for d in docs)
    assert env.client.get("/api/kb/search", params={"q": "restart pool"}).json()["results"][0]["title"] == "Pool runbook"
    r = env.client.post("/api/kb/upload", json={"name": "s.txt", "data": b64(b"password=hunter2hunter2 and more words")})
    assert "hunter2" not in env.client.get(f"/api/kb/docs/{r.json()['id']}").text


def test_kb_upload_errors_and_roles(env):
    from conftest import make_user
    assert env.client.post("/api/kb/upload", json={"name": "a.exe", "data": b64(b"MZ\x00\x00")}).status_code == 400
    assert env.client.post("/api/kb/upload", json={"name": "a.txt", "data": b64(b"hi")}).status_code == 400   # nearly empty
    h = make_user(env, "viewer1", "viewer")
    assert env.client.post("/api/kb/upload", json={"name": "a.txt", "data": b64(b"some real content")}, headers=h).status_code == 403


def test_chat_text_attachment_reaches_model_and_history(env):
    from app.llm.base import LLMProvider, LLMResponse
    seen = []

    class Spy(LLMProvider):
        name, model, is_real = "spy", "s", True
        def complete(self, system, messages, tools=None, *a, **k):
            seen.append(messages)
            return LLMResponse(text="ok")

    env.ctx.llm.safe_get = lambda: Spy()
    sid = new_chat(env)
    body = {"text": "what does this say?", "attachments": [{"name": "notes.txt", "data": b64(b"the db pool is 5 and password=hunter2hunter2")}]}
    ev = parse_sse(env.client.post(f"/api/chat/sessions/{sid}/stream", json=body).text)
    assert ev[-1][0] == "done"
    last = seen[0][-1]["content"]
    assert '<attachment name="notes.txt">' in last and "db pool is 5" in last and "hunter2" not in last
    msgs = env.client.get(f"/api/chat/sessions/{sid}").json()["messages"]
    assert msgs[0]["attachments"][0]["name"] == "notes.txt"
    # follow-up still sees the document
    env.client.post(f"/api/chat/sessions/{sid}/stream", json={"text": "and the pool size?"})
    assert "db pool is 5" in json.dumps([m.get("content") for m in seen[1]])


def test_chat_image_passed_as_image_block(env):
    from app.llm.base import LLMProvider, LLMResponse
    seen = []

    class Spy(LLMProvider):
        name, model, is_real = "spy", "s", True
        def complete(self, system, messages, tools=None, *a, **k):
            seen.append(messages)
            return LLMResponse(text="I see it")

    env.ctx.llm.safe_get = lambda: Spy()
    sid = new_chat(env)
    env.client.post(f"/api/chat/sessions/{sid}/stream", json={"text": "look", "attachments": [{"name": "s.png", "data": b64(PNG)}]})
    im = seen[0][-1]["images"][0]
    assert im["format"] == "png" and im["media_type"] == "image/png" and im["data"] == PNG


def test_image_without_vision_model_is_explained(env):
    sid = new_chat(env)
    ev = parse_sse(env.client.post(f"/api/chat/sessions/{sid}/stream", json={"text": "what is this?", "attachments": [{"name": "s.png", "data": b64(PNG)}]}).text)
    assert "could not look at it" in next(d for e, d in ev if e == "answer")["text"]


def test_attachment_limits(env):
    sid = new_chat(env)
    url = f"/api/chat/sessions/{sid}/stream"
    assert env.client.post(url, json={"text": "x", "attachments": [{"name": "a.exe", "data": b64(b"MZ\x00\x00\x00")}]}).status_code == 400
    five = [{"name": f"{i}.txt", "data": b64(b"hello there")} for i in range(5)]
    assert env.client.post(url, json={"text": "x", "attachments": five}).status_code == 422
    assert env.client.post(url, json={"attachments": [{"name": "a.txt", "data": b64(b"hello there")}]}).status_code == 200   # attachment only


def test_converters_include_images():
    from app.llm.azure_openai import to_openai_messages
    from app.llm.bedrock import to_bedrock_messages
    m = [{"role": "user", "content": "q", "images": [{"format": "png", "media_type": "image/png", "data": PNG}]}]
    assert to_bedrock_messages(m)[0]["content"][1]["image"]["format"] == "png"
    c = to_openai_messages("s", m)[1]["content"]
    assert c[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_kb_reupload_replaces(env):
    for body in (b"first version of the runbook text", b"second version of the runbook text"):
        env.client.post("/api/kb/upload", json={"name": "rb.md", "data": b64(body)})
    docs = [d for d in env.client.get("/api/kb/docs").json() if d["source"] == "file:rb.md"]
    assert len(docs) == 1 and "second" in env.client.get(f"/api/kb/docs/{docs[0]['id']}").json()["content"]
