"""Chat sessions and the knowledge base."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..agents.chat import create_session, send_message, stream_message
from ..context import AppContext
from ..redact import redact
from ..services import files
from .deps import get_ctx, require

router = APIRouter(prefix="/api")


class MsgIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


def _own_session(ctx: AppContext, sid: int, user: dict) -> dict:
    s = ctx.db.one("SELECT * FROM chat_sessions WHERE id=?", (sid,))
    if not s or (s["user_id"] != user["id"] and user["role"] != "admin"):
        raise HTTPException(404, "Chat not found")
    return s


@router.get("/chat/sessions")
def sessions(user: dict = Depends(require("viewer")), ctx: AppContext = Depends(get_ctx)):
    return ctx.db.query("SELECT * FROM chat_sessions WHERE user_id=? ORDER BY id DESC LIMIT 50", (user["id"],))


@router.post("/chat/sessions", status_code=201)
def new_session(user: dict = Depends(require("viewer")), ctx: AppContext = Depends(get_ctx)):
    return {"id": create_session(ctx, user["id"])}


@router.get("/chat/sessions/{sid}")
def get_session(sid: int, user: dict = Depends(require("viewer")), ctx: AppContext = Depends(get_ctx)):
    s = _own_session(ctx, sid, user)
    msgs = ctx.db.query("SELECT id,role,content,trace,created_at FROM chat_messages WHERE session_id=? ORDER BY id", (sid,))
    for m in msgs:
        m["trace"] = json.loads(m["trace"]) if m["trace"] else None
        m["attachments"] = ctx.db.query("SELECT id,name,kind,media_type,size FROM chat_attachments WHERE message_id=? ORDER BY id", (m["id"],))
    return {**s, "messages": msgs}


@router.post("/chat/sessions/{sid}/messages")
def post_message(sid: int, body: MsgIn, user: dict = Depends(require("viewer")), ctx: AppContext = Depends(get_ctx)):
    _own_session(ctx, sid, user)
    return send_message(ctx, sid, body.text, user["username"])


class AttIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    data: str = Field(min_length=1)


class StreamIn(BaseModel):
    text: str | None = Field(default=None, max_length=4000)
    regenerate: bool = False
    attachments: list[AttIn] = Field(default_factory=list, max_length=4)


@router.post("/chat/sessions/{sid}/stream")
def stream(sid: int, body: StreamIn, user: dict = Depends(require("viewer")), ctx: AppContext = Depends(get_ctx)):
    _own_session(ctx, sid, user)
    if not body.regenerate and not (body.text and body.text.strip()) and not body.attachments:
        raise HTTPException(422, "text or an attachment is required")
    atts, total = [], 0
    for a in body.attachments:
        try:
            raw = files.decode_b64(a.data, files.MAX_DOC_BYTES)
            total += len(raw)
            if total > 15 * 1024 * 1024:
                raise files.FileError("attachments are larger than 15 MB in total")
            ex = files.extract(a.name, raw, allow_images=True)
            if ex.kind == "text":
                ex.text = redact(ex.text)
            atts.append(ex)
        except files.FileError as e:
            raise HTTPException(400, f"{a.name}: {e}") from e
    text = body.text.strip() if body.text and body.text.strip() else "(see attached files)"
    gen = stream_message(ctx, sid, None if body.regenerate else text, user["username"], body.regenerate, atts)
    return StreamingResponse(gen, media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


class RenameIn(BaseModel):
    title: str = Field(min_length=1, max_length=80)


@router.patch("/chat/sessions/{sid}")
def rename_session(sid: int, body: RenameIn, user: dict = Depends(require("viewer")), ctx: AppContext = Depends(get_ctx)):
    _own_session(ctx, sid, user)
    ctx.db.execute("UPDATE chat_sessions SET title=? WHERE id=?", (body.title.strip(), sid))
    return {"ok": True}


@router.delete("/chat/sessions/{sid}")
def delete_session(sid: int, user: dict = Depends(require("viewer")), ctx: AppContext = Depends(get_ctx)):
    _own_session(ctx, sid, user)
    ctx.db.execute("DELETE FROM chat_sessions WHERE id=?", (sid,))
    return {"ok": True}


# ---- knowledge base ----
class DocIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=10, max_length=200_000)


@router.get("/kb/docs")
def docs(_: dict = Depends(require("viewer")), ctx: AppContext = Depends(get_ctx)):
    return ctx.kb.list_docs()


@router.get("/kb/docs/{did}")
def doc(did: int, _: dict = Depends(require("viewer")), ctx: AppContext = Depends(get_ctx)):
    d = ctx.kb.get_doc(did)
    if not d:
        raise HTTPException(404, "Document not found")
    return d


@router.post("/kb/docs", status_code=201)
def add_doc(body: DocIn, user: dict = Depends(require("analyst")), ctx: AppContext = Depends(get_ctx)):
    did = ctx.kb.add_doc(body.title, body.content, source=f"upload:{user['username']}")
    ctx.db.audit(user["username"], "kb.add", body.title)
    return {"id": did}


class UploadIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    data: str = Field(min_length=1)
    title: str | None = Field(default=None, max_length=200)


@router.post("/kb/upload", status_code=201)
def upload_doc(body: UploadIn, user: dict = Depends(require("analyst")), ctx: AppContext = Depends(get_ctx)):
    try:
        ex = files.extract(body.name, files.decode_b64(body.data, files.MAX_DOC_BYTES))
    except files.FileError as e:
        raise HTTPException(400, f"{body.name}: {e}") from e
    if len(ex.text.strip()) < 10:
        raise HTTPException(400, f"{body.name}: the file has almost no text")
    title = (body.title or ex.name.rsplit(".", 1)[0])[:200]
    for old in ctx.db.query("SELECT id FROM kb_docs WHERE source=?", (f"file:{ex.name}",)):   # re-uploading a file replaces it
        ctx.kb.delete_doc(old["id"])
    did = ctx.kb.add_doc(title, ex.text, source=f"file:{ex.name}")
    ctx.db.audit(user["username"], "kb.upload", title, {"file": ex.name, "chars": len(ex.text)})
    return {"id": did, "title": title, "chars": len(ex.text), "truncated": ex.truncated}


@router.delete("/kb/docs/{did}")
def del_doc(did: int, user: dict = Depends(require("analyst")), ctx: AppContext = Depends(get_ctx)):
    ctx.kb.delete_doc(did)
    ctx.db.audit(user["username"], "kb.delete", str(did))
    return {"ok": True}


@router.get("/kb/search")
def kb_search(q: str, _: dict = Depends(require("viewer")), ctx: AppContext = Depends(get_ctx)):
    return {"method": "BM25 keyword ranking (not vector embeddings)", "results": ctx.kb.search(q, k=5)}
