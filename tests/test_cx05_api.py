"""CX-05 "inbound everything" — API surface tests (T4).

Covers the two pieces T4 owns, fully offline against a real daemon on a temp root:

  * REFLEX — a ``calendar`` (and ``email``/``slack``) reflex rule with an EMPTY
    ``match`` is accepted (empty match = fire on every signal of that source),
    while a webhook rule still needs a slug and workflow/remote_agent actions
    still need a target;
  * TRIGGERS — GET /triggers reports the three sources; POST /triggers/calendar
    persists the enable flag + lead time and stores the ICS URL in the VAULT
    (GET then shows ``enabled`` + ``has_url`` true) while NEVER echoing the raw
    URL in any response body; DELETE /triggers/calendar/url clears the secret.

The coordinator wires ``routes/triggers.register`` into ``create_app``; this test
registers it explicitly (with a faithfully-reconstructed ``d``) so it verifies
the module in isolation regardless of that wiring order.
"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from iron_jarvis.core.config import persist_config_values
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import triggers as triggers_routes

_ICS_URL = "https://x/y.ics"


def _make_app(tmp_path):
    """A real daemon app with the /triggers routes registered (mirrors the deps
    object create_app builds — ``platform`` + the same atomic ``_persist_config``
    + an empty ``_live_rearm`` so the live re-arm is a guarded no-op in-test)."""
    app = create_app(str(tmp_path))
    p = app.state.platform
    d = SimpleNamespace(
        platform=p,
        _live_rearm={},
        _persist_config=lambda keys: persist_config_values(
            p.config.home, {k: getattr(p.config, k, None) for k in keys}
        ),
    )
    triggers_routes.register(app, d)
    return app


# --------------------------------------------------------------------------- #
# 1. REFLEX — the new text sources accept an EMPTY match (catch-all).
# --------------------------------------------------------------------------- #
def test_calendar_email_slack_rules_accept_empty_match(tmp_path):
    with TestClient(_make_app(tmp_path)) as client:
        for source in ("calendar", "email", "slack"):
            resp = client.post(
                "/reflex/rules",
                json={"name": f"{source}-rule", "source": source, "match": "", "action": "session"},
            )
            assert resp.status_code == 200, (source, resp.text)
            body = resp.json()
            assert body["source"] == source
            assert body["match"] == ""

        # A workflow/remote_agent action still needs a target, even for a new source.
        assert client.post(
            "/reflex/rules",
            json={"source": "calendar", "match": "", "action": "workflow", "target": ""},
        ).status_code == 400
        # A webhook still needs its slug in match.
        assert client.post(
            "/reflex/rules", json={"source": "webhook", "match": "", "action": "session"}
        ).status_code == 400


# --------------------------------------------------------------------------- #
# 2. TRIGGERS — GET reports the three sources.
# --------------------------------------------------------------------------- #
def test_get_triggers_reports_three_sources(tmp_path):
    with TestClient(_make_app(tmp_path)) as client:
        resp = client.get("/triggers")
        assert resp.status_code == 200
        triggers = resp.json()["triggers"]
        assert set(triggers.keys()) == {"email", "calendar", "slack"}
        # All off by default on a fresh root.
        assert triggers["calendar"]["enabled"] is False
        assert triggers["calendar"]["has_url"] is False
        assert triggers["email"]["enabled"] is False
        assert triggers["slack"]["enabled"] is False


# --------------------------------------------------------------------------- #
# 3. TRIGGERS — POST /triggers/calendar persists + stores the URL as a SECRET,
#    and the raw URL never appears in any response body.
# --------------------------------------------------------------------------- #
def test_post_calendar_persists_and_hides_url(tmp_path):
    with TestClient(_make_app(tmp_path)) as client:
        resp = client.post(
            "/triggers/calendar", json={"enabled": True, "ics_url": _ICS_URL}
        )
        assert resp.status_code == 200
        assert _ICS_URL not in resp.text  # never echo the stored secret
        cal = resp.json()["triggers"]["calendar"]
        assert cal["enabled"] is True and cal["has_url"] is True

        # GET reflects the persisted state, still without leaking the URL.
        g = client.get("/triggers")
        assert g.status_code == 200
        assert _ICS_URL not in g.text
        assert g.json()["triggers"]["calendar"]["enabled"] is True
        assert g.json()["triggers"]["calendar"]["has_url"] is True

        # The vault now holds the raw URL (server-side only).
        assert client.app.state.platform.secrets.get("calendar_ics_url") == _ICS_URL

        # lead_minutes is settable and persists; toggling enabled off keeps the URL.
        r2 = client.post("/triggers/calendar", json={"enabled": False, "lead_minutes": 45})
        assert r2.status_code == 200
        cal2 = r2.json()["triggers"]["calendar"]
        assert cal2["enabled"] is False
        assert cal2["lead_minutes"] == 45
        assert cal2["has_url"] is True  # URL untouched when no ics_url supplied

        # A negative lead time is rejected.
        assert client.post(
            "/triggers/calendar", json={"enabled": True, "lead_minutes": -1}
        ).status_code == 400


# --------------------------------------------------------------------------- #
# 4. TRIGGERS — DELETE clears the stored ICS URL secret.
# --------------------------------------------------------------------------- #
def test_delete_calendar_url_clears_secret(tmp_path):
    with TestClient(_make_app(tmp_path)) as client:
        client.post("/triggers/calendar", json={"enabled": True, "ics_url": _ICS_URL})
        assert client.app.state.platform.secrets.get("calendar_ics_url") == _ICS_URL

        d = client.delete("/triggers/calendar/url")
        assert d.status_code == 200
        assert d.json()["removed"] is True
        assert d.json()["triggers"]["calendar"]["has_url"] is False
        assert client.app.state.platform.secrets.get("calendar_ics_url") is None
