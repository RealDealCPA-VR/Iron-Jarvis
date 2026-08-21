"""A non-ASCII signature must REFUSE, never raise (v1.195.0, TOFIX finding 3).

``hmac.compare_digest`` raises ``TypeError`` when both arguments are ``str`` and
either holds a non-ASCII character. Three call sites compared client-supplied
header strings, so a signature header carrying any byte >= 0x80 turned a
fail-closed refusal into an unhandled exception — on ``/comm/slack/events/`` that
means a 500 on a deliberately TOKEN-EXEMPT route, reachable with no bearer.

``daemon/auth.py:token_matches`` already fixed this class for the bearer token
(compare the UTF-8 ENCODED forms); these tests pin the same behaviour on the
Slack receiver and on both webhook verifiers, and prove nothing changed for a
valid signature or for an ordinary ASCII-wrong one.

The Slack case is driven through a RAW ASGI scope on purpose: httpx (and so
Starlette's TestClient) refuses to encode a non-ASCII header client-side, while a
real HTTP client just puts the bytes on the wire and Starlette decodes them
latin-1 — which is exactly what produced the reported 500.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.webhooks.security import sign, sign_v2, verify, verify_signed

SIGNING = "8f742231b10e8888abcd99yyyzzz85a5"

# The exact bytes from the recorded repro: "v0=caf" + UTF-8 "é". A latin-1 decode
# (what Starlette does to header bytes) yields a str holding U+00C3/U+00A9, both
# non-ASCII, which is all compare_digest needs to raise.
NON_ASCII_SIG_BYTES = b"v0=caf\xc3\xa9"
NON_ASCII_SIG = NON_ASCII_SIG_BYTES.decode("latin-1")


# --- webhooks/security.py: verify + verify_signed ----------------------------


def test_verify_refuses_non_ascii_signature_instead_of_raising():
    payload = b'{"hello":"world"}'
    secret = "s3cr3t"

    # The defect: this used to raise TypeError out of a function whose contract
    # is "return a bool".
    assert verify(payload, secret, NON_ASCII_SIG) is False

    # Unchanged behaviour for everything else.
    assert verify(payload, secret, sign(payload, secret)) is True
    assert verify(payload, secret, "sha256=" + sign(payload, secret)) is True
    assert verify(payload, secret, "deadbeef") is False
    assert verify(payload, secret, None) is False
    # The "empty secret accepts anything" branch is preserved EXACTLY — it must
    # still short-circuit before any comparison happens.
    assert verify(payload, "", NON_ASCII_SIG) is True


def test_verify_signed_refuses_non_ascii_signature_instead_of_raising():
    payload = b'{"hello":"world"}'
    secret = "s3cr3t"
    ts = str(int(time.time()))

    assert verify_signed(ts, payload, secret, NON_ASCII_SIG) is False

    good = sign_v2(ts, payload, secret)
    assert verify_signed(ts, payload, secret, good) is True
    assert verify_signed(ts, payload, secret, "sha256=" + good) is True
    assert verify_signed(ts, payload, secret, "deadbeef") is False
    assert verify_signed(ts, payload, secret, None) is False
    assert verify_signed(ts, payload, "", NON_ASCII_SIG) is True
    # Skew check still fires ahead of the compare.
    assert verify_signed(str(int(time.time()) - 4000), payload, secret, good) is False


def test_non_ascii_signature_is_utf8_encoded_not_filtered():
    """A legitimately non-ASCII signature still matches ITSELF.

    Stripping or ASCII-filtering the candidate would also stop the TypeError,
    but it would make distinct inputs compare equal. Encoding both sides keeps
    the mapping injective (same reasoning as ``token_matches``).
    """
    payload = b"x"
    # Monkey-free check: sign() is hex, so build the pathological pair by hand.
    assert hmac.compare_digest(
        NON_ASCII_SIG.encode("utf-8"), NON_ASCII_SIG.encode("utf-8")
    )
    assert not hmac.compare_digest(
        NON_ASCII_SIG.encode("utf-8"), "v0=caf".encode("utf-8")
    )
    assert verify(payload, "s", NON_ASCII_SIG) is False


# --- daemon/routes/comm.py: the token-exempt Slack receiver ------------------


def _add_slack(client: TestClient, name: str = "team") -> None:
    r = client.post(
        "/comm/channels",
        json={
            "type": "slack",
            "name": name,
            "config": {
                "token": "xoxb-test",
                "channel": "#general",
                "signing_secret": SIGNING,
            },
        },
    )
    assert r.status_code == 200, r.text


def _raw_post(app, path: str, body: bytes, extra_headers: list[tuple[bytes, bytes]]):
    """POST through the full ASGI stack with RAW header bytes.

    TestClient cannot express this case (httpx raises on a non-ASCII header
    value), so we speak ASGI directly — the same thing uvicorn hands the app
    when a real client puts those bytes on the wire. Host must be loopback or
    HostOriginGuardMiddleware rejects before the route runs.
    """
    headers: list[tuple[bytes, bytes]] = [
        (b"host", b"127.0.0.1:8787"),
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
        *extra_headers,
    ]
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": ("127.0.0.1", 54321),
        "server": ("127.0.0.1", 8787),
    }
    captured: dict = {"status": None, "body": b""}

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            captured["status"] = message["status"]
        elif message["type"] == "http.response.body":
            captured["body"] += message.get("body") or b""

    asyncio.run(app(scope, receive, send))
    return captured


def _signed_header_bytes(body: bytes, secret: str = SIGNING) -> list[tuple[bytes, bytes]]:
    ts = str(int(time.time()))
    sig = "v0=" + hmac.new(
        secret.encode(), f"v0:{ts}:".encode() + body, hashlib.sha256
    ).hexdigest()
    return [
        (b"x-slack-request-timestamp", ts.encode()),
        (b"x-slack-signature", sig.encode()),
    ]


def test_slack_non_ascii_signature_403s_not_500s(tmp_path):
    app = create_app(str(tmp_path))
    _add_slack(TestClient(app))
    body = json.dumps({"type": "url_verification", "challenge": "c-123"}).encode()
    ts = str(int(time.time())).encode()

    got = _raw_post(
        app,
        "/comm/slack/events/team",
        body,
        [
            (b"x-slack-request-timestamp", ts),
            (b"x-slack-signature", NON_ASCII_SIG_BYTES),
        ],
    )
    # Before the fix this was 500 {"detail": "internal error: TypeError: ..."}.
    assert got["status"] == 403, got
    assert b"invalid slack signature" in got["body"], got


def test_slack_valid_and_ascii_wrong_signatures_are_unchanged(tmp_path):
    app = create_app(str(tmp_path))
    _add_slack(TestClient(app))
    body = json.dumps({"type": "url_verification", "challenge": "c-123"}).encode()

    good = _raw_post(
        app, "/comm/slack/events/team", body, _signed_header_bytes(body)
    )
    assert good["status"] == 200, good
    assert json.loads(good["body"])["challenge"] == "c-123"

    wrong = _raw_post(
        app,
        "/comm/slack/events/team",
        body,
        _signed_header_bytes(body, secret="wrong-secret"),
    )
    assert wrong["status"] == 403, wrong
    assert b"invalid slack signature" in wrong["body"], wrong
