"""The 413 refusal must be READABLE BY THE BROWSER (v1.195.0).

`BodyLimitMiddleware` used to be added AFTER the CORS block in `create_app`,
which put it outside `CORSMiddleware`. Its 413 therefore went back with no
`access-control-allow-origin`, and per the incident `ErrorEnvelopeMiddleware`
was written for (v1.151.3), a browser then refuses to let the page read the
response at all: `fetch` rejects, `lib/api.ts` maps it to `ApiError(status=0)`,
and every page renders that as "daemon offline".

It never bit before because the guard only fired on a `content-length` the
dashboard never sent. v1.195.0 closed the chunked-body bypass, so the guard
became reachable for real — and the user dropping an oversized file into chat
would have been told their daemon was down instead of that the file is too big.

The DoS property must survive the move: the guard still sits OUTSIDE
`TokenAuthMiddleware`, so an oversized body is refused before the token check
and before the body is buffered.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app

ORIGIN = "http://127.0.0.1:8788"  # the dashboard's own origin


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("IRONJARVIS_MAX_BODY_MB", "1")
    monkeypatch.delenv("IRONJARVIS_TOKEN", raising=False)
    return TestClient(create_app(str(tmp_path)))


def _over_cap() -> str:
    return "A" * (2 * 1024 * 1024)  # 2 MB against a 1 MB cap


def test_the_413_carries_cors_headers_so_the_browser_can_read_it(client):
    r = client.post(
        "/documents/upload",
        json={"filename": "x.bin", "content_b64": _over_cap()},
        headers={"Origin": ORIGIN},
    )
    assert r.status_code == 413, r.text
    # THE ASSERTION THAT MATTERS: without this header the browser discards the
    # response and the user is told the daemon is offline.
    assert r.headers.get("access-control-allow-origin") == ORIGIN, (
        "the 413 must be readable cross-origin — BodyLimitMiddleware has drifted "
        "back outside CORSMiddleware in create_app"
    )
    assert "too large" in r.text


def test_a_normal_request_is_unaffected(client):
    r = client.post(
        "/documents/upload",
        json={"filename": "small.txt", "content_b64": "aGVsbG8="},
        headers={"Origin": ORIGIN},
    )
    assert r.status_code == 200, r.text
    assert r.headers.get("access-control-allow-origin") == ORIGIN


def test_the_oversized_body_is_still_refused_without_a_token(tmp_path, monkeypatch):
    """The guard stays OUTSIDE TokenAuthMiddleware: an unauthenticated oversized
    body is refused 413 (not 401) — i.e. before the body is buffered, which is
    the whole point of the DoS guard."""
    monkeypatch.setenv("IRONJARVIS_MAX_BODY_MB", "1")
    monkeypatch.setenv("IRONJARVIS_TOKEN", "tok-secret")
    guarded = TestClient(create_app(str(tmp_path)))
    r = guarded.post(
        "/documents/upload",
        json={"filename": "x.bin", "content_b64": _over_cap()},
        headers={"Origin": ORIGIN},
    )
    assert r.status_code == 413, (
        f"expected the body guard to refuse before the token check, got {r.status_code}"
    )
