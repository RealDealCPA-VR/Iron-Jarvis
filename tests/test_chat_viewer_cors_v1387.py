"""Readiness 12B review (G05): the viewer header crosses CORS on BOTH branches,
and a stream needs no client-side no-store.

The chat page names itself with ``X-IJ-Viewer`` on ``POST /chat/stream`` and on
the rejoin ``GET /chat/turns/{id}/stream``. The dashboard (127.0.0.1:8788) and
the daemon (127.0.0.1:8787) are different origins, so the browser asks first
(an OPTIONS preflight) and sends the header only when the daemon's answer
allows it. ``daemon/app.py`` has TWO CORS branches — the loopback regex (the
desktop app) and ``IRONJARVIS_CORS_ORIGINS`` (a deployment) — each must allow
it, keep the preflight's max-age (cacheable), and answer with a preflight
that is not no-store.

The real browser half is ``dashboard/e2e/chat-waiting-where-you-are.spec.ts``
(Edge, the scratch daemon): the stream carries the header, and since the
stream reader dropped ``cache: "no-store"`` its preflight goes out once, not
once per turn. That is safe only because the daemon says no-store on the
STREAM responses themselves — pinned here.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app

pytestmark = pytest.mark.usefixtures("no_network")

WEB = "http://127.0.0.1:8788"
ASK = "authorization,content-type,x-ij-viewer"


def _preflight(client: TestClient, path: str, method: str):
    return client.options(
        path,
        headers={"Origin": WEB, "Access-Control-Request-Method": method, "Access-Control-Request-Headers": ASK},
    )


@pytest.mark.parametrize("branch", ["loopback", "configured"])
@pytest.mark.parametrize(
    ("path", "method"),
    [("/chat/stream", "POST"), ("/chat/turns/turn-x/stream", "GET"), ("/chat/turns/turn-x/away", "POST")],
)
def test_both_cors_branches_allow_the_viewer_header(tmp_path, monkeypatch, branch, path, method):
    if branch == "configured":
        monkeypatch.setenv("IRONJARVIS_CORS_ORIGINS", f"https://jarvis.example, {WEB}")
    else:
        monkeypatch.delenv("IRONJARVIS_CORS_ORIGINS", raising=False)
    client = TestClient(create_app(str(tmp_path)), base_url="http://127.0.0.1:8787")
    r = _preflight(client, path, method)
    assert r.status_code == 200, r.text
    assert r.headers.get("access-control-allow-origin") == WEB
    allowed = {h.strip().lower() for h in r.headers.get("access-control-allow-headers", "").split(",")}
    assert "x-ij-viewer" in allowed, r.headers
    assert method in r.headers.get("access-control-allow-methods", "")
    assert r.headers.get("access-control-max-age") == "600"  # cacheable …
    assert r.headers.get("cache-control") != "no-store"  # … and not undone


def test_a_streams_own_responses_say_no_store(tmp_path):
    """The stream reader sends no ``cache`` key any more (it defeated the
    preflight cache); the daemon's own no-store must cover the stream and the
    rejoin, or the browser could keep a turn's frames on disk."""
    client = TestClient(create_app(str(tmp_path)), base_url="http://127.0.0.1:8787")
    body = {"messages": [{"role": "user", "content": "hello"}], "turn_id": "turn-ns", "thread_id": "th-ns"}
    r = client.post("/chat/stream", json=body, headers={"Origin": WEB, "X-IJ-Viewer": "view-1"})
    assert r.status_code == 200, r.text
    assert r.headers.get("cache-control") == "no-store"
    assert r.headers.get("access-control-allow-origin") == WEB
    assert "event: done" in r.text
    again = client.get("/chat/turns/turn-ns/stream", headers={"Origin": WEB, "X-IJ-Viewer": "view-2"})
    assert again.status_code == 200, again.text
    assert again.headers.get("cache-control") == "no-store"
