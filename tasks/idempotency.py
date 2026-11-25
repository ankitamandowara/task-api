"""In-memory Idempotency-Key support.

STORE = {"<path>|<key>": {"body": <request body>, "response": {"status": int, "data": ...}}}

Only successful (2xx) responses are stored, so a client can fix bad input and retry
with the same key. The store lives in process memory: it resets when the server restarts
(fine for this assignment; use Redis/DB in production).
"""
import copy
import json
import threading

from rest_framework.response import Response

from .errors import error_response

STORE = {}
_LOCK = threading.Lock()


def _store_key(request):
    key = request.headers.get("Idempotency-Key")
    if not key:
        return None
    # Scoped by path so the same key on a different endpoint/task is a different request.
    return f"{request.path}|{key.strip()}"


def _fingerprint(request):
    return json.dumps(request.data, sort_keys=True, default=str)


def lookup(request):
    """Return a Response to send straight back (replay or 422), or None to carry on."""
    store_key = _store_key(request)
    if store_key is None:
        return None
    with _LOCK:
        entry = STORE.get(store_key)
    if entry is None:
        return None
    if entry["body"] != _fingerprint(request):
        return error_response(
            "IDEMPOTENCY_KEY_REUSED",
            "Idempotency-Key was already used with a different request body",
            [{"field": "Idempotency-Key", "issue": "reused with a different body"}],
            422,
        )
    stored = entry["response"]
    return Response(copy.deepcopy(stored["data"]), status=stored["status"])


def remember(request, response):
    """Call only after a successful create."""
    store_key = _store_key(request)
    if store_key is None or not (200 <= response.status_code < 300):
        return
    with _LOCK:
        STORE[store_key] = {
            "body": _fingerprint(request),
            "response": {"status": response.status_code, "data": copy.deepcopy(response.data)},
        }


def clear():
    """Used by tests."""
    with _LOCK:
        STORE.clear()
