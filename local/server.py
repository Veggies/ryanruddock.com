#!/usr/bin/env python3
"""Local stand-in for the Lambda backend, so the site is fully working offline.

Serves the repo root as static files and answers the same routes the
deployed function does, with the same validation rules -- blog validation is
imported straight from infra/lambda/blog.py. State lives beside this file as
JSON, uploaded images under local/uploads/, and all of it is gitignored.

    python3 local/server.py     ->  http://127.0.0.1:8099/

The blog author token is "dev" unless BLOG_TOKEN is set.
"""

import hashlib
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.dirname(HERE)
COUNTER_FILE = os.path.join(HERE, "counter.json")
GUESTBOOK_FILE = os.path.join(HERE, "guestbook.json")
POSTS_FILE = os.path.join(HERE, "posts.json")
UPLOADS_DIR = os.path.join(HERE, "uploads")

sys.path.insert(0, os.path.join(SITE, "infra", "lambda"))
import blog  # noqa: E402

TOKEN_SHA256 = hashlib.sha256(os.environ.get("BLOG_TOKEN", "dev").encode()).hexdigest()
VIEWS_FILE = os.path.join(HERE, "views.json")
COMMENTS_FILE = os.path.join(HERE, "comments.json")
POST_ROUTE = re.compile(r"^/api/posts/([^/]+)(?:/(view|comments))?$")

MAX_NAME = 40
MAX_SITE = 120
MAX_MESSAGE = 500
MAX_ENTRIES = 200

VISITOR_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
HTTP_URL = re.compile(r"^https?://[^\s]+\.[^\s]+$", re.I)

LOCK = Lock()


def load(path, default):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def save(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, path)


def clean(value, limit):
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()[:limit]


def clean_site(value):
    site = clean(value, MAX_SITE)
    if not site:
        return ""
    if not re.match(r"^https?://", site, re.I):
        site = "http://" + site
    return site if HTTP_URL.match(site) else ""


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=SITE, **kwargs)

    def log_message(self, fmt, *args):
        pass

    def send_json(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_body(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            return {}
        # large enough for a base64 image upload, same as the function URL
        if length <= 0 or length > 6 * 1024 * 1024:
            return {}
        try:
            parsed = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def translate_path(self, path):
        # uploads live in local/ in dev, but at /uploads/ on the site
        route = path.split("?")[0]
        if route.startswith("/uploads/") and blog.UPLOAD_URL.match(route):
            return os.path.join(UPLOADS_DIR, route[len("/uploads/"):])
        return super().translate_path(path)

    def do_GET(self):
        route = self.path.split("?")[0]
        if route == "/api/state":
            with LOCK:
                count = load(COUNTER_FILE, {"count": 0}).get("count", 0)
                entries = load(GUESTBOOK_FILE, [])
            return self.send_json({"count": count, "entries": entries})

        if route == "/api/posts":
            with LOCK:
                posts = load(POSTS_FILE, {})
                views = load(VIEWS_FILE, {})
            listing = [{"id": k, "title": v["title"], "when": v["when"],
                        "views": len(views.get(k, []))} for k, v in posts.items()]
            listing.sort(key=lambda p: p["when"], reverse=True)
            return self.send_json({"posts": listing})

        routed = POST_ROUTE.match(route)
        if routed and not routed.group(2):
            post_id = routed.group(1)
            with LOCK:
                post = load(POSTS_FILE, {}).get(post_id)
                comments = load(COMMENTS_FILE, {}).get(post_id, [])
            if not post:
                return self.send_json({"ok": False, "error": "No such post."}, 404)
            return self.send_json({
                "post": dict(post, id=post_id, blocks=json.loads(post["blocks"])),
                "comments": comments,
            })

        return super().do_GET()

    def post_subroute(self, post_id, sub):
        data = self.read_body()
        with LOCK:
            exists = post_id in load(POSTS_FILE, {})
        if not exists:
            return self.send_json({"ok": False, "error": "No such post."}, 404)

        if sub == "view":
            visitor = clean(data.get("visitorId"), 64)
            with LOCK:
                views = load(VIEWS_FILE, {})
                seen = views.setdefault(post_id, [])
                if blog.VISITOR_ID.match(visitor or "") and visitor not in seen:
                    seen.append(visitor)
                    save(VIEWS_FILE, views)
            return self.send_json({"views": len(seen)})

        if data.get("website"):
            with LOCK:
                comments = load(COMMENTS_FILE, {}).get(post_id, [])
            return self.send_json({"ok": True, "comments": comments})
        message = blog.clean_comment(data.get("message"))
        if not message:
            return self.send_json({"ok": False, "error": "Write a comment first."}, 400)
        author = blog.token_ok(self.headers.get("Authorization"), TOKEN_SHA256)
        name = blog.clean_name(data.get("name")) or "Anonymous Neopian"

        now = datetime.now(timezone.utc)
        with LOCK:
            all_comments = load(COMMENTS_FILE, {})
            thread = all_comments.setdefault(post_id, [])
            thread.append({"id": blog.new_comment_id(now), "name": name, "message": message,
                           "when": now.isoformat(timespec="seconds"), "author": author})
            del thread[:-blog.MAX_COMMENTS]
            save(COMMENTS_FILE, all_comments)
        return self.send_json({"ok": True, "comments": thread})

    def author_route(self, route):
        if not blog.token_ok(self.headers.get("Authorization"), TOKEN_SHA256):
            return self.send_json({"ok": False, "error": "Wrong or missing author token."}, 401)
        data = self.read_body()
        try:
            if route == "/api/auth":
                return self.send_json({"ok": True})

            if route == "/api/posts":
                post_id = data.get("id") or ""
                if post_id and not blog.POST_ID.match(post_id):
                    return self.send_json({"ok": False, "error": "Bad post id."}, 400)
                title, blocks_json = blog.validate_post(data)
                now = datetime.now(timezone.utc).isoformat(timespec="seconds")
                with LOCK:
                    posts = load(POSTS_FILE, {})
                    if post_id and post_id not in posts:
                        return self.send_json({"ok": False, "error": "That post no longer exists."}, 404)
                    when = posts[post_id]["when"] if post_id else now
                    post_id = post_id or blog.new_post_id()
                    posts[post_id] = {"title": title, "when": when, "updated": now, "blocks": blocks_json}
                    save(POSTS_FILE, posts)
                return self.send_json({"ok": True, "id": post_id})

            if route == "/api/posts/delete":
                post_id = data.get("id") or ""
                if not blog.POST_ID.match(post_id):
                    return self.send_json({"ok": False, "error": "Bad post id."}, 400)
                with LOCK:
                    posts = load(POSTS_FILE, {})
                    posts.pop(post_id, None)
                    save(POSTS_FILE, posts)
                return self.send_json({"ok": True})

            if route == "/api/comments/delete":
                post_id, comment_id = data.get("postId") or "", data.get("id") or ""
                if not blog.POST_ID.match(post_id) or not blog.COMMENT_ID.match(comment_id):
                    return self.send_json({"ok": False, "error": "Bad comment id."}, 400)
                with LOCK:
                    all_comments = load(COMMENTS_FILE, {})
                    thread = [c for c in all_comments.get(post_id, []) if c["id"] != comment_id]
                    all_comments[post_id] = thread
                    save(COMMENTS_FILE, all_comments)
                return self.send_json({"ok": True, "comments": thread})

            blob, _, ext = blog.validate_image(data)
            key = blog.upload_key(ext)
            dest = os.path.join(HERE, key)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as fh:
                fh.write(blob)
            return self.send_json({"ok": True, "url": "/" + key})

        except blog.Invalid as err:
            return self.send_json({"ok": False, "error": str(err)}, 400)

    def do_POST(self):
        route = self.path.split("?")[0]

        if route == "/api/visit":
            visitor = clean(self.read_body().get("visitorId"), 64)
            with LOCK:
                state = load(COUNTER_FILE, {"count": 0, "seen": []})
                state.setdefault("seen", [])
                if VISITOR_ID.match(visitor or "") and visitor not in state["seen"]:
                    state["seen"].append(visitor)
                    state["count"] = int(state.get("count", 0)) + 1
                    save(COUNTER_FILE, state)
                count = state.get("count", 0)
            return self.send_json({"count": count})

        if route == "/api/guestbook":
            data = self.read_body()
            message = clean(data.get("message"), MAX_MESSAGE)
            if not message:
                return self.send_json({"ok": False, "error": "Message is required."}, 400)

            entry = {
                "name": clean(data.get("name"), MAX_NAME) or "Anonymous Neopian",
                "site": clean_site(data.get("site")),
                "message": message,
                "when": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            with LOCK:
                entries = load(GUESTBOOK_FILE, [])
                entries.insert(0, entry)
                del entries[MAX_ENTRIES:]
                save(GUESTBOOK_FILE, entries)
            return self.send_json({"ok": True, "entries": entries})

        routed = POST_ROUTE.match(route)
        if routed and routed.group(2) and blog.POST_ID.match(routed.group(1)):
            return self.post_subroute(routed.group(1), routed.group(2))

        if route in ("/api/auth", "/api/posts", "/api/posts/delete", "/api/uploads", "/api/comments/delete"):
            return self.author_route(route)

        return self.send_json({"ok": False, "error": "Not found."}, 404)


if __name__ == "__main__":
    print("serving %s on http://127.0.0.1:8099/" % SITE)
    ThreadingHTTPServer(("127.0.0.1", 8099), Handler).serve_forever()
