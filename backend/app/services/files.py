"""Turn uploaded files into text (or recognise images). Used by the knowledge base and by chat attachments.
Everything extracted is treated as untrusted data and passed through redaction before it is stored or sent to a model."""
from __future__ import annotations

import base64
import io
import re
import zipfile
from dataclasses import dataclass
from xml.etree import ElementTree as ET

MAX_DOC_BYTES = 10 * 1024 * 1024
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_TEXT_CHARS = 200_000
TEXT_EXT = {".txt", ".md", ".markdown", ".log", ".json", ".yaml", ".yml", ".csv", ".tsv", ".ini", ".toml", ".conf", ".cfg", ".xml",
            ".html", ".htm", ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rb", ".rs", ".c", ".h", ".cpp", ".cs", ".sh", ".sql",
            ".tf", ".properties", ".env.example", ".rst"}
IMAGE_MAGIC = [(b"\x89PNG\r\n\x1a\n", "image/png", "png"), (b"\xff\xd8\xff", "image/jpeg", "jpeg"),
               (b"GIF87a", "image/gif", "gif"), (b"GIF89a", "image/gif", "gif")]


class FileError(ValueError):
    pass


@dataclass
class Extracted:
    kind: str                # text | image
    name: str
    text: str = ""
    media_type: str = ""
    fmt: str = ""            # png | jpeg | gif | webp (image only)
    data: bytes = b""
    truncated: bool = False


def decode_b64(s: str, limit: int) -> bytes:
    if not isinstance(s, str):
        raise FileError("file data must be base64 text")
    s = s.split(",", 1)[1] if s.startswith("data:") and "," in s else s
    if len(s) > limit * 4 // 3 + 16:
        raise FileError(f"file is larger than {limit // (1024 * 1024)} MB")
    try:
        return base64.b64decode(s, validate=False)
    except Exception as e:  # noqa: BLE001
        raise FileError("file data is not valid base64") from e


def sniff_image(data: bytes) -> tuple[str, str] | None:
    for magic, mt, fmt in IMAGE_MAGIC:
        if data.startswith(magic):
            return mt, fmt
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", "webp"
    return None


def _docx_text(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            if sum(i.file_size for i in z.infolist()) > 60 * 1024 * 1024:
                raise FileError("document is too large when unpacked")
            xml = z.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError) as e:
        raise FileError("this is not a valid .docx file") from e
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    root = ET.fromstring(xml)
    paras = []
    for p in root.iter(f"{ns}p"):
        t = "".join(n.text or "" for n in p.iter() if n.tag == f"{ns}t")
        style = p.find(f"{ns}pPr/{ns}pStyle")
        sv = style.get(f"{ns}val", "") if style is not None else ""
        if t.strip():
            paras.append(("## " if sv.lower().startswith("heading") else "") + t)
    return "\n\n".join(paras)


def _pdf_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader
        r = PdfReader(io.BytesIO(data))
        if r.is_encrypted:
            raise FileError("this PDF is password protected")
        out = []
        for i, page in enumerate(r.pages[:200]):
            t = (page.extract_text() or "").strip()
            if t:
                out.append(f"[page {i + 1}]\n{t}")
        return "\n\n".join(out)
    except FileError:
        raise
    except Exception as e:  # noqa: BLE001
        raise FileError(f"could not read this PDF: {type(e).__name__}") from e


def extract(name: str, data: bytes, allow_images: bool = False) -> Extracted:
    name = re.sub(r"[^\w.\- ()]", "_", (name or "file").split("/")[-1].split("\\")[-1])[:120] or "file"
    low = name.lower()
    img = sniff_image(data)
    if img:
        if not allow_images:
            raise FileError("images are not supported here")
        if len(data) > MAX_IMAGE_BYTES:
            raise FileError("image is larger than 5 MB")
        return Extracted("image", name, media_type=img[0], fmt=img[1], data=data)
    if len(data) > MAX_DOC_BYTES:
        raise FileError("file is larger than 10 MB")
    if data[:5] == b"%PDF-" or low.endswith(".pdf"):
        text = _pdf_text(data)
        if not text.strip():
            raise FileError("no text found in this PDF (a scanned PDF needs OCR, which is not supported)")
    elif low.endswith(".docx") or data[:2] == b"PK" and low.endswith((".docx", ".docm")):
        text = _docx_text(data)
        if not text.strip():
            raise FileError("no text found in this document")
    else:
        if b"\x00" in data[:4096]:
            raise FileError("binary file: supported types are text, Markdown, PDF, .docx and images")
        if not any(low.endswith(e) for e in TEXT_EXT) and "." in low:
            raise FileError(f"unsupported file type '.{low.rsplit('.', 1)[-1]}'")
        text = data.decode("utf-8", errors="replace")
    trunc = len(text) > MAX_TEXT_CHARS
    return Extracted("text", name, text=text[:MAX_TEXT_CHARS], truncated=trunc)
