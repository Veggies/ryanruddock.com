#!/usr/bin/env python3
"""Local stand-in for the Lambda backend, so the site is fully working offline.

Serves the repo root as static files and answers the same three routes the
deployed function does, with the same validation rules. State lives beside
this file as JSON and is gitignored.

    python3 local/server.py     ->  http://127.0.0.1:8099/
"""

import json
import os
import re
import uuid
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.dirname(HERE)
COUNTER_FILE = os.path.join(HERE, "counter.json")
GUESTBOOK_FILE = os.path.join(HERE, "guestbook.json")

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
        if length <= 0 or length > 64 * 1024:
            return {}
        try:
            parsed = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def do_GET(self):
        if self.path.split("?")[0] == "/api/state":
            with LOCK:
                count = load(COUNTER_FILE, {"count": 0}).get("count", 0)
                entries = load(GUESTBOOK_FILE, [])
            return self.send_json({"count": count, "entries": entries})
        return super().do_GET()

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

        return self.send_json({"ok": False, "error": "Not found."}, 404)


if __name__ == "__main__":
    print("serving %s on http://127.0.0.1:8099/" % SITE)
    ThreadingHTTPServer(("127.0.0.1", 8099), Handler).serve_forever()
