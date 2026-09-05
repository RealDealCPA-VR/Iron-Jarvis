"""``GET /sessions`` answers a weak ETag and a bodiless 304 (v1.230.0, audit FP3).

The Overview polls the session list every 5 s and the list rarely changes
between ticks; the default 200-row listing was ~60 KB per tick (~1 GB/day) for
a six-row widget. Now the response carries ``ETag: W/"<sha1 of the body>"``,
a repeat GET with ``If-None-Match`` naming it is a 304 with no body, and any
change to any row (a new session, a status flip) is a 200 with a new tag. The
tag is exposed through CORS — without ``Access-Control-Expose-Headers`` the
dashboard could never read it and the whole path would silently never engage.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import Session as SessionRow, SessionStatus
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes.sessions import _etag_matches

ORIGIN = {"Origin": "http://127.0.0.1:8788"}


def _client(tmp_path):
    return TestClient(create_app(str(tmp_path)))


def _seed(engine, *ids: str) -> None:
    with session_scope(engine) as db:
        for sid in ids:
            db.add(SessionRow(id=sid, task=f"task {sid}", status=SessionStatus.COMPLETED))
        db.commit()


def test_repeat_get_with_the_etag_is_304_and_a_change_is_200_with_a_new_tag(tmp_path):
    with _client(tmp_path) as client:
        _seed(client.app.state.platform.engine, "s1")

        r1 = client.get("/sessions?limit=50", headers=ORIGIN)
        assert r1.status_code == 200, r1.text
        etag = r1.headers.get("etag")
        assert etag and etag.startswith('W/"'), r1.headers
        assert [s["id"] for s in r1.json()["sessions"]] == ["s1"]

        # Same list, tag presented: nothing crosses the wire but the status.
        r2 = client.get("/sessions?limit=50", headers={**ORIGIN, "If-None-Match": etag})
        assert r2.status_code == 304, r2.text
        assert r2.content == b""
        assert r2.headers.get("etag") == etag
        # Still no-store (FP1) — the client sends the tag itself; the browser
        # cache never holds session JSON.
        assert r2.headers.get("cache-control") == "no-store"

        # The list changes: the SAME tag no longer matches -> 200, new tag, new body.
        _seed(client.app.state.platform.engine, "s2")
        r3 = client.get("/sessions?limit=50", headers={**ORIGIN, "If-None-Match": etag})
        assert r3.status_code == 200, r3.text
        new_etag = r3.headers.get("etag")
        assert new_etag and new_etag != etag
        assert {s["id"] for s in r3.json()["sessions"]} == {"s1", "s2"}

        # And the new tag validates in turn.
        r4 = client.get("/sessions?limit=50", headers={**ORIGIN, "If-None-Match": new_etag})
        assert r4.status_code == 304


def test_a_stale_or_absent_tag_is_a_normal_200(tmp_path):
    with _client(tmp_path) as client:
        r = client.get("/sessions", headers={**ORIGIN, "If-None-Match": 'W/"not-this-one"'})
        assert r.status_code == 200
        assert r.json() == {"sessions": []}
        assert r.headers.get("etag")
        # The tag is deterministic: two reads of an unchanged list agree.
        assert client.get("/sessions", headers=ORIGIN).headers.get("etag") == r.headers.get("etag")


def test_the_tag_is_readable_cross_origin(tmp_path):
    """ETag is not a CORS-safelisted response header: the dashboard (8788)
    reads the daemon (8787) cross-origin and sees NOTHING unless it is exposed."""
    with _client(tmp_path) as client:
        r = client.get("/sessions", headers=ORIGIN)
        assert r.status_code == 200
        exposed = [h.strip().lower() for h in r.headers.get("access-control-expose-headers", "").split(",")]
        assert "etag" in exposed, r.headers


def test_the_project_scoped_listing_is_tagged_too(tmp_path):
    with _client(tmp_path) as client:
        r = client.get("/sessions?project_id=proj_x", headers=ORIGIN)
        assert r.status_code == 200
        etag = r.headers.get("etag")
        assert etag
        r2 = client.get("/sessions?project_id=proj_x", headers={**ORIGIN, "If-None-Match": etag})
        assert r2.status_code == 304


def test_weak_comparison_rules():
    assert _etag_matches('W/"abc"', 'W/"abc"')
    assert _etag_matches('"abc"', 'W/"abc"')  # strength is ignored (RFC 7232 §2.3.2)
    assert _etag_matches('W/"zzz", W/"abc"', 'W/"abc"')  # a list
    assert _etag_matches("*", 'W/"abc"')
    assert not _etag_matches('W/"abd"', 'W/"abc"')
    assert not _etag_matches(None, 'W/"abc"')
    assert not _etag_matches("", 'W/"abc"')
