"""Chat attachments (contract §10, Attachments): which files are accepted, and how they reach the AI.

Pure functions only, so the rules that can go wrong quietly (a type let through that the model then
rejects mid-run, a size limit off by a factor) are unit-tested without a server or an AI call.
"""
from __future__ import annotations

import base64

MB = 1024 * 1024

# MIME type → (stored extension, kind, max bytes). The limits are the model's own: a bigger image
# or PDF would be accepted here and then fail the whole AI run.
TYPES: dict[str, tuple[str, str, int]] = {
    "image/png": (".png", "image", 5 * MB),
    "image/jpeg": (".jpg", "image", 5 * MB),
    "image/webp": (".webp", "image", 5 * MB),
    "image/gif": (".gif", "image", 5 * MB),
    "application/pdf": (".pdf", "document", 20 * MB),
    "text/plain": (".txt", "text", 1 * MB),
    "text/markdown": (".md", "text", 1 * MB),
    "text/csv": (".csv", "text", 1 * MB),
}

# Browsers often send these as "" or application/octet-stream; trust the extension for text only.
TEXT_EXT = {".txt": "text/plain", ".md": "text/markdown", ".csv": "text/csv"}


class UploadRejected(ValueError):
    """The file cannot be sent to the AI; the message says what to do instead."""


def resolve_type(name: str, mime: str) -> str:
    """The MIME type the file is stored and sent as, or UploadRejected with a sentence for the user."""
    mime = (mime or "").split(";")[0].strip().lower()
    if mime == "image/jpg":
        mime = "image/jpeg"
    if mime not in TYPES:
        ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
        mime = TEXT_EXT.get(ext, mime)
    if mime in TYPES:
        return mime
    lower = name.lower()
    if lower.endswith((".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx")):
        raise UploadRejected(f"{name}: Office files can't be read by the AI yet. Save it as PDF and attach that.")
    if lower.endswith((".heic", ".heif")):
        raise UploadRejected(f"{name}: iPhone HEIC photos can't be read. Export it as JPEG and attach that.")
    raise UploadRejected(f"{name}: only pictures (PNG, JPEG, WebP, GIF), PDFs and text files can be attached.")


def check_size(name: str, mime: str, size: int) -> None:
    limit = TYPES[mime][2]
    if size <= 0:
        raise UploadRejected(f"{name} is empty.")
    if size > limit:
        raise UploadRejected(f"{name} is {size / MB:.1f} MB; the limit for this kind of file is {limit // MB} MB.")


def decode(data: str) -> bytes:
    """Base64 from the browser, with or without a data: URL prefix."""
    if data.startswith("data:") and "," in data:
        data = data.split(",", 1)[1]
    try:
        return base64.b64decode(data, validate=True)
    except (ValueError, TypeError):
        raise UploadRejected("The file did not arrive intact. Try attaching it again.")


def content_blocks(text: str, files: list[tuple[dict, bytes]]) -> list[dict]:
    """One user turn for the model: each file as its native block, labelled by name, then the text."""
    blocks: list[dict] = []
    for meta, raw in files:
        kind = TYPES[meta["type"]][1]
        blocks.append({"type": "text", "text": f"Attached file: {meta['name']}"})
        if kind == "image":
            blocks.append({"type": "image", "source": {"type": "base64", "media_type": meta["type"],
                                                      "data": base64.b64encode(raw).decode()}})
        elif kind == "document":
            blocks.append({"type": "document", "title": meta["name"],
                           "source": {"type": "base64", "media_type": "application/pdf",
                                      "data": base64.b64encode(raw).decode()}})
        else:
            blocks.append({"type": "text", "text": raw.decode("utf-8", errors="replace")})
    blocks.append({"type": "text", "text": text})
    return blocks
