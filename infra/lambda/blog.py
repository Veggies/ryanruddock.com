"""Blog post, image and comment validation, shared by the Lambda and
local/server.py.

No AWS imports here: local/server.py loads this module directly so the dev
backend enforces exactly the same rules as the deployed one.

A post is the JSON that Editor.js saves -- a list of typed blocks. Only the
block types the editor is configured with are accepted, and every field is
checked for shape. Inline HTML inside text fields (bold, links, ...) is stored
as-is; blog.html runs it through DOMPurify on render, which is the real XSS
boundary. The checks here keep the stored data well-formed and bounded.
"""

import base64
import binascii
import hashlib
import hmac
import json
import re
import uuid
from datetime import datetime, timezone

MAX_TITLE = 140
MAX_BLOCKS = 400
MAX_POST_BYTES = 300 * 1024        # DynamoDB items cap at 400 KB
MAX_IMAGE_BYTES = 4 * 1024 * 1024  # base64 inflates this to ~5.3 MB, under the 6 MB function URL limit

POST_ID = re.compile(r"^\d{4}-\d{2}-\d{2}-[a-f0-9]{6}$")
UPLOAD_URL = re.compile(r"^/uploads/\d{4}/\d{2}/[a-f0-9]{32}\.(jpg|png)$")

# SVG is deliberately absent: it is served from the site's own origin and can
# carry script. Animated GIFs are flattened by the browser-side resize anyway.
IMAGE_TYPES = {
    "image/jpeg": ("jpg", b"\xff\xd8\xff"),
    "image/png": ("png", b"\x89PNG\r\n\x1a\n"),
}

MAX_COMMENT_NAME = 40
MAX_COMMENT = 1000
MAX_COMMENTS = 300
VISITOR_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
COMMENT_ID = re.compile(r"^\d{4}-\d{2}-\d{2}T[\d:.+-]+#[a-f0-9]{8}$")

CALLOUT_TONES = {"info", "tip", "warning", "danger"}
LIST_STYLES = {"unordered", "ordered", "checklist"}


class Invalid(ValueError):
    """Raised with a message that is safe to show the author."""


# --------------------------------------------------------------------------
# auth
# --------------------------------------------------------------------------

def token_ok(header, expected_sha256):
    """Bearer token check. Only the token's SHA-256 is configured, so the
    secret itself never sits in Terraform state or the Lambda's environment.
    An empty expected hash disables every write route."""
    if not expected_sha256 or not isinstance(header, str):
        return False
    if not header.lower().startswith("bearer "):
        return False
    got = hashlib.sha256(header[7:].strip().encode()).hexdigest()
    return hmac.compare_digest(got, expected_sha256.lower())


# --------------------------------------------------------------------------
# posts
# --------------------------------------------------------------------------

def new_post_id(now=None):
    now = now or datetime.now(timezone.utc)
    return "%s-%s" % (now.strftime("%Y-%m-%d"), uuid.uuid4().hex[:6])


def _text(value, limit=20000):
    if not isinstance(value, str):
        raise Invalid("A block has a malformed text field.")
    return value[:limit]


def _list_items(items, depth=0):
    if not isinstance(items, list) or depth > 5:
        raise Invalid("A list block is malformed.")
    out = []
    for item in items:
        if not isinstance(item, dict):
            raise Invalid("A list block is malformed.")
        meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
        out.append({
            "content": _text(item.get("content", "")),
            "meta": {"checked": bool(meta.get("checked"))} if "checked" in meta else {},
            "items": _list_items(item.get("items", []), depth + 1),
        })
    return out


def _block(block):
    if not isinstance(block, dict) or not isinstance(block.get("data"), dict):
        raise Invalid("A block is malformed.")
    kind, d = block.get("type"), block["data"]

    if kind == "paragraph":
        data = {"text": _text(d.get("text", ""))}
    elif kind == "header":
        level = d.get("level")
        data = {"text": _text(d.get("text", "")), "level": level if level in (2, 3, 4) else 2}
    elif kind == "list":
        style = d.get("style")
        if style not in LIST_STYLES:
            raise Invalid("A list block has an unknown style.")
        data = {"style": style, "meta": {}, "items": _list_items(d.get("items", []))}
    elif kind == "quote":
        data = {"text": _text(d.get("text", "")), "caption": _text(d.get("caption", ""), 500)}
    elif kind == "callout":
        tone = d.get("tone")
        data = {
            "emoji": _text(d.get("emoji", ""), 16),
            "text": _text(d.get("text", "")),
            "tone": tone if tone in CALLOUT_TONES else "info",
        }
    elif kind == "code":
        data = {"code": _text(d.get("code", ""), 50000)}
    elif kind == "delimiter":
        data = {}
    elif kind == "table":
        rows = d.get("content")
        if not isinstance(rows, list) or len(rows) > 100:
            raise Invalid("A table block is malformed.")
        content = []
        for row in rows:
            if not isinstance(row, list) or len(row) > 20:
                raise Invalid("A table block is malformed.")
            content.append([_text(cell, 2000) for cell in row])
        data = {"withHeadings": bool(d.get("withHeadings")), "content": content}
    elif kind == "image":
        url = (d.get("file") or {}).get("url") if isinstance(d.get("file"), dict) else None
        # only images this site uploaded itself -- no hotlinking or tracking pixels
        if not isinstance(url, str) or not UPLOAD_URL.match(url):
            raise Invalid("Images must be uploaded through the editor.")
        data = {
            "file": {"url": url},
            "caption": _text(d.get("caption", ""), 500),
            "withBorder": bool(d.get("withBorder")),
            "withBackground": bool(d.get("withBackground")),
            "stretched": bool(d.get("stretched")),
        }
    else:
        raise Invalid("Unsupported block type: %s" % str(kind)[:30])

    return {"type": kind, "data": data}


def validate_post(payload):
    """Return (title, blocks_json) or raise Invalid."""
    title = re.sub(r"\s+", " ", payload.get("title") or "").strip()[:MAX_TITLE]
    if not title:
        raise Invalid("A title is required.")

    blocks = payload.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        raise Invalid("The post is empty.")
    if len(blocks) > MAX_BLOCKS:
        raise Invalid("The post has too many blocks.")

    cleaned = json.dumps([_block(b) for b in blocks], separators=(",", ":"))
    if len(cleaned.encode()) > MAX_POST_BYTES:
        raise Invalid("The post is too long (images do not count, text does).")
    return title, cleaned


# --------------------------------------------------------------------------
# images
# --------------------------------------------------------------------------

def validate_image(payload):
    """Return (bytes, content_type, ext) or raise Invalid. The declared type
    must agree with the file's own magic bytes."""
    kind = payload.get("type")
    if kind not in IMAGE_TYPES:
        raise Invalid("Only JPEG and PNG images are accepted.")
    ext, magic = IMAGE_TYPES[kind]

    raw = payload.get("data")
    if not isinstance(raw, str) or len(raw) > MAX_IMAGE_BYTES * 4 // 3 + 4:
        raise Invalid("That image is too large.")
    try:
        blob = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        raise Invalid("The image data is malformed.")

    if not blob.startswith(magic):
        raise Invalid("The file is not the image type it claims to be.")
    return blob, kind, ext


def upload_key(ext, now=None):
    now = now or datetime.now(timezone.utc)
    return "uploads/%s/%s.%s" % (now.strftime("%Y/%m"), uuid.uuid4().hex, ext)


# --------------------------------------------------------------------------
# comments
# --------------------------------------------------------------------------

def clean_comment(value):
    """Plain text, but keep paragraph breaks: trim each line, allow at most
    one blank line in a row. The page renders it with textContent."""
    if not isinstance(value, str):
        return ""
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in value.replace("\r\n", "\n").split("\n")]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return text[:MAX_COMMENT]


def clean_name(value):
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()[:MAX_COMMENT_NAME]


def new_comment_id(now=None):
    """Sorts by time; microseconds keep two comments in one second ordered."""
    now = now or datetime.now(timezone.utc)
    return "%s#%s" % (now.isoformat(timespec="microseconds"), uuid.uuid4().hex[:8])
