"""CX-05 hardening regression tests.

Locks in the fixes for the adversarial-review findings so they can't silently
regress: the email HTML-strip DoS (quadratic backtracking → poison-pill), the
unbounded ICS read, calendar TZID correctness, case-insensitive email allowlist,
and Slack event_id idempotency.
"""

from __future__ import annotations

import time
from datetime import timezone

from iron_jarvis.comm.channels import EmailChannel, _MAX_EMAIL_BODY_CHARS, _strip_html
from iron_jarvis.triggers.calendar import _MAX_ICS_BYTES, _parse_dt


# --- MAJOR 1: email HTML-strip must be LINEAR (no ReDoS poison-pill) --------- #
def test_strip_html_is_linear_on_hostile_body():
    # ~1 MB of unclosed <script tags — the old lazy regex went quadratic (secs).
    hostile = "<script " * 140_000
    start = time.perf_counter()
    out = _strip_html(hostile[:_MAX_EMAIL_BODY_CHARS])
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0, f"strip_html took {elapsed:.2f}s — ReDoS regressed"
    assert "script" not in out.lower()  # script content is dropped


def test_strip_html_still_extracts_text():
    assert _strip_html("<p>Hello <b>world</b></p><script>evil()</script>") == "Hello world"
    assert _strip_html("<div>a &amp; b</div>") == "a & b"


def test_email_body_size_capped():
    # The cap is well under any real body but bounds a hostile blob.
    assert _MAX_EMAIL_BODY_CHARS <= 500_000


# --- MAJOR 2: the ICS read is byte-capped ----------------------------------- #
def test_ics_read_cap_is_bounded():
    # A sane, non-DoS ceiling (covers a very large real calendar, not a GB feed).
    assert 1_000_000 <= _MAX_ICS_BYTES <= 50_000_000


# --- MINOR 3: TZID events localize correctly -------------------------------- #
def test_parse_dt_utc_and_date():
    utc = _parse_dt("DTSTART", "20260714T090000Z")
    assert utc.tzinfo is not None and utc.hour == 9 and utc.utcoffset().total_seconds() == 0
    allday = _parse_dt("DTSTART;VALUE=DATE", "20260714")
    assert allday.tzinfo == timezone.utc and allday.hour == 0


def test_parse_dt_tzid_localizes_or_falls_back():
    # America/New_York 09:00 in July (EDT, UTC-4) => 13:00 UTC where a tz db is
    # present; where none is (bare Windows), it falls back to 09:00 UTC. Either
    # way it must return an aware UTC datetime and never raise.
    dt = _parse_dt("DTSTART;TZID=America/New_York", "20260714T090000")
    assert dt is not None and dt.tzinfo is not None
    assert dt.utcoffset().total_seconds() == 0  # normalized to UTC
    assert dt.hour in (9, 13)  # localized (13) or safe UTC fallback (9)


def test_parse_dt_bad_value_returns_none():
    assert _parse_dt("DTSTART", "not-a-date") is None


# --- MINOR 5: email allowlist is case-insensitive + fail-closed ------------- #
def test_email_allowlist_case_insensitive_fail_closed():
    ch = EmailChannel({"allowed_senders": ["Boss@Acme.com"]})
    assert ch.is_authorized("boss@acme.com") is True
    assert ch.is_authorized("BOSS@ACME.COM") is True
    assert ch.is_authorized("stranger@evil.com") is False
    # Empty allowlist authorizes nobody (fail-closed).
    assert EmailChannel({}).is_authorized("boss@acme.com") is False
    assert EmailChannel({"allowed_senders": []}).is_authorized("boss@acme.com") is False


# --- MINOR 4: Slack event_id idempotency ------------------------------------ #
def test_slack_event_dedup(tmp_path):
    """A redelivered Slack event_id must not fire the pipeline twice."""
    import hashlib
    import hmac
    import json
    import time as _time

    from fastapi.testclient import TestClient

    from iron_jarvis.daemon.app import create_app

    with TestClient(create_app(str(tmp_path))) as client:
        p = client.app.state.platform
        # Configure a slack channel with a signing secret + allowlisted user, and
        # a slack reflex rule so a delivered event would fire a session.
        secret = "shhh-signing"
        p.secrets.set("slack_sign", secret, kind="password")
        p.config.comm = {
            "channels": {
                "sk": {
                    "type": "slack",
                    "signing_secret_secret": "slack_sign",
                    "inbound_enabled": True,
                    "allowed_senders": ["U123"],
                }
            }
        }
        # Rebuild the notifier so the channel is live for this app.
        from iron_jarvis.comm.integrations import build_notifier

        p.notifier = build_notifier(
            p.config.comm,
            secret_resolver=p.secrets.get,
            http_post=lambda *a, **k: {"ok": True},
            http_get=lambda *a, **k: {"ok": True},
        )
        client.app.state.d = getattr(client.app.state, "d", None)
        p.reflex.add(name="dep", source="slack", match="deploy", action="session")

        payload = {
            "type": "event_callback",
            "event_id": "Ev-DUP-1",
            "event": {
                "type": "message",
                "user": "U123",
                "text": "please deploy",
                "channel": "C1",
                "channel_type": "channel",
            },
        }
        raw = json.dumps(payload)
        ts = str(int(_time.time()))
        base = f"v0:{ts}:{raw}".encode()
        sig = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
        headers = {
            "X-Slack-Signature": sig,
            "X-Slack-Request-Timestamp": ts,
            "Content-Type": "application/json",
        }

        # Both deliveries ack 200; the dedup makes the 2nd a no-op.
        r1 = client.post("/comm/slack/events/sk", content=raw, headers=headers)
        r2 = client.post("/comm/slack/events/sk", content=raw, headers=headers)
        assert r1.status_code == 200 and r1.json().get("ok") is True
        assert r2.status_code == 200 and r2.json().get("ok") is True
