"""Visitor counter, guestbook and blog for ryanruddock.com.

Fronted by a Lambda function URL. Routes:
  GET  /api/state         -> {"count": N, "entries": [...]}
  POST /api/visit         -> {"count": N}          body: {"visitorId": "..."}
  POST /api/guestbook     -> {"ok": true, "entries": [...]}
  GET  /api/posts         -> {"posts": [{id, title, when, excerpt, views}, ...]}
  GET  /api/posts/<id>    -> {"post": {...}, "comments": [...]}
  POST /api/posts/<id>/view      -> {"views": N}   body: {"visitorId": "..."}
  POST /api/posts/<id>/comments  -> {"ok": true, "comments": [...]}
                                    body: {name, message, website(honeypot)}

Author-only, bearer token required (see blog.token_ok):
  POST /api/auth          -> {"ok": true}           checks the token, nothing else
  POST /api/posts         -> {"ok": true, "id": ...} body: {id?, title, blocks}
  POST /api/posts/delete  -> {"ok": true}           body: {id}
  POST /api/uploads       -> {"ok": true, "url": "/uploads/..."}  body: {type, data(base64)}
  POST /api/comments/delete -> {"ok": true}         body: {postId, id}

A comment POSTed with a valid author token is marked as the author's.

Single DynamoDB table, item shapes:
  pk="counter" sk="total"          -> count (N)
  pk="visitor" sk=<visitorId>      -> ttl (N), for one-count-per-browser
  pk="entry"   sk=<when>#<uuid>    -> name, site, message, when
  pk="post"    sk=<post id>        -> title, when, updated, excerpt, blocks, style (JSON strings)
  pk="views"   sk=<post id>        -> count (N); apart from the post, which
                                      is rewritten whole on every edit
  pk="viewer"  sk=<post id>#<visitorId> -> ttl (N), one view per browser
  pk="comments#<post id>" sk=<when>#<uuid> -> name, message, when, author

Uploaded images go to the site bucket under uploads/, which the deploy
workflow excludes from its sync so a push never deletes them.

Entry sort keys lead with an ISO-8601 timestamp, so a descending query on the
partition returns newest-first without a secondary index.
"""

import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone

import boto3
from botocore.exceptions import ClientError

import blog

TABLE = os.environ["TABLE_NAME"]
SITE_BUCKET = os.environ.get("SITE_BUCKET", "")
AUTHOR_TOKEN_SHA256 = os.environ.get("AUTHOR_TOKEN_SHA256", "")
ddb = boto3.client("dynamodb")
s3 = boto3.client("s3")

MAX_NAME = 40
MAX_SITE = 120
MAX_MESSAGE = 500
MAX_ENTRIES = 200
VISITOR_TTL_DAYS = 365

VISITOR_ID = blog.VISITOR_ID
POST_ROUTE = re.compile(r"^/api/posts/([^/]+)(?:/(view|comments))?$")
HTTP_URL = re.compile(r"^https?://[^\s]+\.[^\s]+$", re.I)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def respond(status, payload):
    # CORS headers come from the function URL's own cors block; setting them
    # here too would emit duplicates and the browser would reject the response.
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"},
        "body": json.dumps(payload),
    }


def clean(value, limit):
    """Collapse whitespace and clamp length. Stored as plain text; the page
    renders it with textContent, so there is no markup to escape."""
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()[:limit]


def clean_site(value):
    site = clean(value, MAX_SITE)
    if not site:
        return ""
    if not re.match(r"^https?://", site, re.I):
        site = "http://" + site
    # only plain http(s) survives; no javascript:/data: smuggling
    return site if HTTP_URL.match(site) else ""


def body_of(event):
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        import base64
        try:
            raw = base64.b64decode(raw).decode("utf-8", "replace")
        except Exception:
            return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


# --------------------------------------------------------------------------
# data access
# --------------------------------------------------------------------------

def read_count():
    got = ddb.get_item(
        TableName=TABLE,
        Key={"pk": {"S": "counter"}, "sk": {"S": "total"}},
        ConsistentRead=False,
    )
    return int(got.get("Item", {}).get("count", {}).get("N", "0"))


def read_entries():
    got = ddb.query(
        TableName=TABLE,
        KeyConditionExpression="pk = :p",
        ExpressionAttributeValues={":p": {"S": "entry"}},
        ScanIndexForward=False,   # newest first
        Limit=MAX_ENTRIES,
    )
    entries = []
    for item in got.get("Items", []):
        entries.append({
            "name": item.get("name", {}).get("S", ""),
            "site": item.get("site", {}).get("S", ""),
            "message": item.get("message", {}).get("S", ""),
            "when": item.get("when", {}).get("S", ""),
        })
    return entries


def count_visit(visitor_id):
    """Increment only for a browser we have not seen. The conditional put is
    the dedupe: it fails for a known id, so the counter is never touched."""
    if not visitor_id or not VISITOR_ID.match(visitor_id):
        return read_count()

    expires = int((datetime.now(timezone.utc) + timedelta(days=VISITOR_TTL_DAYS)).timestamp())
    try:
        ddb.put_item(
            TableName=TABLE,
            Item={
                "pk": {"S": "visitor"},
                "sk": {"S": visitor_id},
                "ttl": {"N": str(expires)},
            },
            ConditionExpression="attribute_not_exists(pk)",
        )
    except ClientError as err:
        if err.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return read_count()   # returning visitor
        raise

    bumped = ddb.update_item(
        TableName=TABLE,
        Key={"pk": {"S": "counter"}, "sk": {"S": "total"}},
        UpdateExpression="ADD #c :one",
        ExpressionAttributeNames={"#c": "count"},
        ExpressionAttributeValues={":one": {"N": "1"}},
        ReturnValues="UPDATED_NEW",
    )
    return int(bumped["Attributes"]["count"]["N"])


def sign(name, site, message):
    now = datetime.now(timezone.utc)
    # The sort key carries microseconds so two entries in the same second still
    # order by time; the displayed timestamp stays at second precision.
    when = now.isoformat(timespec="seconds")
    ddb.put_item(
        TableName=TABLE,
        Item={
            "pk": {"S": "entry"},
            "sk": {"S": "%s#%s" % (now.isoformat(timespec="microseconds"), uuid.uuid4().hex[:8])},
            "name": {"S": name},
            "site": {"S": site},
            "message": {"S": message},
            "when": {"S": when},
        },
    )


def list_posts():
    got = ddb.query(
        TableName=TABLE,
        KeyConditionExpression="pk = :p",
        ExpressionAttributeValues={":p": {"S": "post"}},
        ProjectionExpression="sk, title, #w, excerpt",
        ExpressionAttributeNames={"#w": "when"},   # WHEN is a reserved word
    )
    views = read_all_views()
    posts = [{
        "id": item["sk"]["S"],
        "title": item.get("title", {}).get("S", ""),
        "when": item.get("when", {}).get("S", ""),
        "excerpt": item.get("excerpt", {}).get("S", ""),
        "views": views.get(item["sk"]["S"], 0),
    } for item in got.get("Items", [])]
    # ids start with the date but not the time, so sort on the full timestamp
    return sorted(posts, key=lambda p: p["when"], reverse=True)


def read_post(post_id):
    got = ddb.get_item(TableName=TABLE, Key={"pk": {"S": "post"}, "sk": {"S": post_id}})
    item = got.get("Item")
    if not item:
        return None
    return {
        "id": post_id,
        "title": item.get("title", {}).get("S", ""),
        "when": item.get("when", {}).get("S", ""),
        "updated": item.get("updated", {}).get("S", ""),
        "blocks": json.loads(item.get("blocks", {}).get("S", "[]")),
        "style": json.loads(item.get("style", {}).get("S", "{}")),
    }


def save_post(post_id, title, blocks_json, style_json, excerpt):
    """Create, or overwrite an existing post while keeping its original
    publish time. Returns the id, or None if an edit names a missing post."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    when = now
    if post_id:
        existing = read_post(post_id)
        if not existing:
            return None
        when = existing["when"] or now
    else:
        post_id = blog.new_post_id()

    ddb.put_item(
        TableName=TABLE,
        Item={
            "pk": {"S": "post"},
            "sk": {"S": post_id},
            "title": {"S": title},
            "when": {"S": when},
            "updated": {"S": now},
            "blocks": {"S": blocks_json},
            "style": {"S": style_json},
            "excerpt": {"S": excerpt},
        },
    )
    return post_id


def read_all_views():
    got = ddb.query(
        TableName=TABLE,
        KeyConditionExpression="pk = :p",
        ExpressionAttributeValues={":p": {"S": "views"}},
    )
    return {item["sk"]["S"]: int(item.get("count", {}).get("N", "0")) for item in got.get("Items", [])}


def read_views(post_id):
    got = ddb.get_item(TableName=TABLE, Key={"pk": {"S": "views"}, "sk": {"S": post_id}})
    return int(got.get("Item", {}).get("count", {}).get("N", "0"))


def count_post_view(post_id, visitor_id):
    """Same dedupe as count_visit, keyed per post: the conditional put fails
    for a browser that has already viewed this post."""
    if not visitor_id or not VISITOR_ID.match(visitor_id):
        return read_views(post_id)

    expires = int((datetime.now(timezone.utc) + timedelta(days=VISITOR_TTL_DAYS)).timestamp())
    try:
        ddb.put_item(
            TableName=TABLE,
            Item={
                "pk": {"S": "viewer"},
                "sk": {"S": "%s#%s" % (post_id, visitor_id)},
                "ttl": {"N": str(expires)},
            },
            ConditionExpression="attribute_not_exists(pk)",
        )
    except ClientError as err:
        if err.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return read_views(post_id)
        raise

    bumped = ddb.update_item(
        TableName=TABLE,
        Key={"pk": {"S": "views"}, "sk": {"S": post_id}},
        UpdateExpression="ADD #c :one",
        ExpressionAttributeNames={"#c": "count"},
        ExpressionAttributeValues={":one": {"N": "1"}},
        ReturnValues="UPDATED_NEW",
    )
    return int(bumped["Attributes"]["count"]["N"])


def read_comments(post_id):
    got = ddb.query(
        TableName=TABLE,
        KeyConditionExpression="pk = :p",
        ExpressionAttributeValues={":p": {"S": "comments#" + post_id}},
        Limit=blog.MAX_COMMENTS,   # oldest first, like a conversation
    )
    return [{
        "id": item["sk"]["S"],
        "name": item.get("name", {}).get("S", ""),
        "message": item.get("message", {}).get("S", ""),
        "when": item.get("when", {}).get("S", ""),
        "author": item.get("author", {}).get("BOOL", False),
    } for item in got.get("Items", [])]


def add_comment(post_id, name, message, author):
    now = datetime.now(timezone.utc)
    ddb.put_item(
        TableName=TABLE,
        Item={
            "pk": {"S": "comments#" + post_id},
            "sk": {"S": blog.new_comment_id(now)},
            "name": {"S": name},
            "message": {"S": message},
            "when": {"S": now.isoformat(timespec="seconds")},
            "author": {"BOOL": author},
        },
    )


def post_exists(post_id):
    got = ddb.get_item(
        TableName=TABLE,
        Key={"pk": {"S": "post"}, "sk": {"S": post_id}},
        ProjectionExpression="pk",
    )
    return "Item" in got


def comment_route(post_id, event):
    data = body_of(event)
    if data.get("website"):
        # honeypot: a field people never see, so only a bot fills it. Report
        # success so the bot has no reason to adapt.
        return respond(200, {"ok": True, "comments": read_comments(post_id)})

    message = blog.clean_comment(data.get("message"))
    if not message:
        return respond(400, {"ok": False, "error": "Write a comment first."})
    if not post_exists(post_id):
        return respond(404, {"ok": False, "error": "No such post."})

    # a typed name is unverified; the author badge is the one mark that is,
    # since only the author token can set it
    author = blog.token_ok((event.get("headers") or {}).get("authorization"), AUTHOR_TOKEN_SHA256)
    name = blog.clean_name(data.get("name")) or "Anonymous Neopian"

    add_comment(post_id, name, message, author)
    return respond(200, {"ok": True, "comments": read_comments(post_id)})


def store_image(blob, content_type, ext):
    key = blog.upload_key(ext)
    s3.put_object(
        Bucket=SITE_BUCKET,
        Key=key,
        Body=blob,
        ContentType=content_type,
        # every upload gets a fresh random name, so it never needs revalidating
        CacheControl="public, max-age=31536000, immutable",
    )
    return "/" + key


def author_route(method, path, event):
    """The write side of the blog. Returns None for paths it does not own."""
    if method != "POST" or path not in ("/api/auth", "/api/posts", "/api/posts/delete",
                                        "/api/uploads", "/api/comments/delete"):
        return None

    headers = event.get("headers") or {}
    if not blog.token_ok(headers.get("authorization"), AUTHOR_TOKEN_SHA256):
        return respond(401, {"ok": False, "error": "Wrong or missing author token."})

    data = body_of(event)
    try:
        if path == "/api/auth":
            return respond(200, {"ok": True})

        if path == "/api/posts":
            post_id = data.get("id") or ""
            if post_id and not blog.POST_ID.match(post_id):
                return respond(400, {"ok": False, "error": "Bad post id."})
            title, blocks_json, style_json, excerpt = blog.validate_post(data)
            saved = save_post(post_id, title, blocks_json, style_json, excerpt)
            if not saved:
                return respond(404, {"ok": False, "error": "That post no longer exists."})
            return respond(200, {"ok": True, "id": saved})

        if path == "/api/posts/delete":
            post_id = data.get("id") or ""
            if not blog.POST_ID.match(post_id):
                return respond(400, {"ok": False, "error": "Bad post id."})
            # images the post used stay in uploads/; they are cheap and an
            # image may be shared with another post
            ddb.delete_item(TableName=TABLE, Key={"pk": {"S": "post"}, "sk": {"S": post_id}})
            return respond(200, {"ok": True})

        if path == "/api/comments/delete":
            post_id, comment_id = data.get("postId") or "", data.get("id") or ""
            if not blog.POST_ID.match(post_id) or not blog.COMMENT_ID.match(comment_id):
                return respond(400, {"ok": False, "error": "Bad comment id."})
            ddb.delete_item(TableName=TABLE, Key={"pk": {"S": "comments#" + post_id}, "sk": {"S": comment_id}})
            return respond(200, {"ok": True, "comments": read_comments(post_id)})

        if not SITE_BUCKET:
            return respond(503, {"ok": False, "error": "Image uploads are not configured."})
        blob, content_type, ext = blog.validate_image(data)
        return respond(200, {"ok": True, "url": store_image(blob, content_type, ext)})

    except blog.Invalid as err:
        return respond(400, {"ok": False, "error": str(err)})


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

def handler(event, context):
    http = event.get("requestContext", {}).get("http", {})
    method = http.get("method", "GET").upper()
    path = (event.get("rawPath") or "/").rstrip("/") or "/"

    try:
        if method == "GET" and path == "/api/state":
            return respond(200, {"count": read_count(), "entries": read_entries()})

        if method == "POST" and path == "/api/visit":
            visitor = clean(body_of(event).get("visitorId"), 64)
            return respond(200, {"count": count_visit(visitor)})

        if method == "POST" and path == "/api/guestbook":
            data = body_of(event)
            message = clean(data.get("message"), MAX_MESSAGE)
            if not message:
                return respond(400, {"ok": False, "error": "Message is required."})

            sign(
                clean(data.get("name"), MAX_NAME) or "Anonymous Neopian",
                clean_site(data.get("site")),
                message,
            )
            return respond(200, {"ok": True, "entries": read_entries()})

        if method == "GET" and path == "/api/posts":
            return respond(200, {"posts": list_posts()})

        routed = POST_ROUTE.match(path)
        if routed and blog.POST_ID.match(routed.group(1)):
            post_id, sub = routed.groups()
            if method == "GET" and not sub:
                post = read_post(post_id)
                if not post:
                    return respond(404, {"ok": False, "error": "No such post."})
                return respond(200, {"post": post, "comments": read_comments(post_id)})
            if method == "POST" and sub == "view":
                visitor = clean(body_of(event).get("visitorId"), 64)
                return respond(200, {"views": count_post_view(post_id, visitor)})
            if method == "POST" and sub == "comments":
                return comment_route(post_id, event)
        elif routed and method == "GET":
            return respond(404, {"ok": False, "error": "No such post."})

        authored = author_route(method, path, event)
        if authored:
            return authored

        return respond(404, {"ok": False, "error": "Not found."})

    except ClientError:
        # never surface AWS internals to the page
        return respond(500, {"ok": False, "error": "Storage is unavailable right now."})
