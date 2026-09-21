"""Visitor counter + guestbook for ryanruddock.com.

Fronted by a Lambda function URL. Routes:
  GET  /api/state      -> {"count": N, "entries": [...]}
  POST /api/visit      -> {"count": N}          body: {"visitorId": "..."}
  POST /api/guestbook  -> {"ok": true, "entries": [...]}

Single DynamoDB table, three item shapes:
  pk="counter" sk="total"          -> count (N)
  pk="visitor" sk=<visitorId>      -> ttl (N), for one-count-per-browser
  pk="entry"   sk=<when>#<uuid>    -> name, site, message, when

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

TABLE = os.environ["TABLE_NAME"]
ddb = boto3.client("dynamodb")

MAX_NAME = 40
MAX_SITE = 120
MAX_MESSAGE = 500
MAX_ENTRIES = 200
VISITOR_TTL_DAYS = 365

VISITOR_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
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

        return respond(404, {"ok": False, "error": "Not found."})

    except ClientError:
        # never surface AWS internals to the page
        return respond(500, {"ok": False, "error": "Storage is unavailable right now."})
