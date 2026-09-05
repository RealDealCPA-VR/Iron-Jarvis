"""Every HTTP response carries ``Cache-Control: no-store`` (v1.230.0, audit FP1).

The dashboard dropped ``cache: "no-store"`` from its fetch init so Chromium can
use the CORS preflight cache again (the daemon answers max-age 600; with the
client-side option every GET cost an OPTIONS). The re-grade proved the header
is load-bearing: with NEITHER side saying no-store, Chromium writes session JSON
(client file names) into the Electron disk cache. So the daemon says it, on
every response, from INSIDE the CORS layer — the preflight CORSMiddleware
answers itself must keep its max-age and stay cacheable.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app

ORIGIN = {"Origin": "http://127.0.0.1:8788"}


@pytest.fixture
def client(tmp_path):
    app = create_app(str(tmp_path))

    @app.get("/_boom_unhandled")
    def _boom():  # noqa: ANN202
        raise RuntimeError("kaboom")

    @app.get("/_boom_http")
    def _boom_http():  # noqa: ANN202
        raise HTTPException(status_code=418, detail="teapot")

    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    ("path", "status"),
    [
        ("/health", 200),
        ("/sessions", 200),
        ("/_no_such_route_v1230", 404),
        ("/_boom_http", 418),
        ("/_boom_unhandled", 500),
    ],
)
def test_every_response_is_no_store(client, path, status):
    r = client.get(path, headers=ORIGIN)
    assert r.status_code == status, r.text
    assert r.headers.get("cache-control") == "no-store", (
        f"{path} answered {status} without Cache-Control: no-store — with the "
        "dashboard no longer sending cache:'no-store', this header is the only "
        "thing keeping session JSON out of the Electron disk cache"
    )
    # Still readable by the page: the header sits INSIDE the CORS layer.
    assert r.headers.get("access-control-allow-origin") == ORIGIN["Origin"]


def test_preflight_keeps_its_max_age_and_is_not_no_store(client):
    """The point of the change: the OPTIONS preflight stays cacheable."""
    r = client.options(
        "/sessions",
        headers={
            **ORIGIN,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert r.status_code == 200, r.text
    assert r.headers.get("access-control-max-age") == "600"
    assert r.headers.get("cache-control") != "no-store", (
        "NoStoreMiddleware must sit INSIDE CORSMiddleware: a no-store on the "
        "preflight defeats the cache the client-side change was made to restore"
    )


def test_a_401_from_the_token_guard_is_no_store(tmp_path, monkeypatch):
    monkeypatch.setenv("IRONJARVIS_TOKEN", "sekrit")
    client = TestClient(create_app(str(tmp_path)), raise_server_exceptions=False)
    r = client.get("/sessions", headers=ORIGIN)
    assert r.status_code == 401, r.text
    assert r.headers.get("cache-control") == "no-store"
