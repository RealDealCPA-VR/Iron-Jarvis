"""A server error must be READABLE by the browser (v1.151.3).

THE INCIDENT, twice over. The @mention panel returned "Daemon offline — the
panel didn't run" while the daemon was up and answering every other request. It
was 500ing on a missing column, but the user — and I, on the first pass — spent
the round looking at connectivity.

THE CAUSE. FastAPI's ``@app.exception_handler(Exception)`` is served by
Starlette's ``ServerErrorMiddleware``, the OUTERMOST layer — outside
``CORSMiddleware``. So an unhandled 500 went back with no
``access-control-allow-origin``, the browser refused to let the page read it,
``fetch`` rejected, and ``lib/api.ts`` mapped that to ``ApiError(status=0)``,
which every page renders as "daemon offline".

That is not a chat bug. EVERY unhandled 500 in the entire app claimed the daemon
was down, which is the most misleading thing a local-first tool can say — it
sends you to check a process that is running fine.

The fix is ordering: ``ErrorEnvelopeMiddleware`` is added FIRST, so it is
INNERMOST, so the JSON it returns passes back out through CORS and is decorated
like any normal response.
"""

from __future__ import annotations

import pytest
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
        from fastapi import HTTPException

        raise HTTPException(status_code=418, detail="teapot")

    # raise_server_exceptions=False makes TestClient behave like a real server
    # (return the 500) instead of re-raising into the test.
    return TestClient(app, raise_server_exceptions=False)


def test_an_unhandled_500_carries_cors_headers(client):
    """The headline. Without this the browser cannot read the response at all."""
    r = client.get("/_boom_unhandled", headers=ORIGIN)
    assert r.status_code == 500
    assert r.headers.get("access-control-allow-origin") == ORIGIN["Origin"], (
        "a 500 without CORS headers is unreadable to the page, so it surfaces as "
        "'daemon offline' — the exact misdiagnosis this exists to prevent"
    )


def test_the_error_detail_is_the_real_one(client):
    """A readable 500 is only useful if it says what happened."""
    body = client.get("/_boom_unhandled", headers=ORIGIN).json()
    assert "RuntimeError" in body["detail"] and "kaboom" in body["detail"]


def test_a_successful_response_is_unchanged(client):
    r = client.get("/agents/mentionable", headers=ORIGIN)
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == ORIGIN["Origin"]


def test_an_http_exception_still_behaves_normally(client):
    """HTTPException is handled INSIDE the app and must not be swallowed or
    reshaped by the catch-all — a 418 stays a 418 with its own detail."""
    r = client.get("/_boom_http", headers=ORIGIN)
    assert r.status_code == 418
    assert r.json()["detail"] == "teapot"
    assert r.headers.get("access-control-allow-origin") == ORIGIN["Origin"]


def test_a_404_is_untouched(client):
    r = client.get("/_no_such_route", headers=ORIGIN)
    assert r.status_code == 404
    assert r.headers.get("access-control-allow-origin") == ORIGIN["Origin"]


def test_the_guarded_error_shape_matches_the_existing_handler(client):
    """One contract: the middleware's envelope is the same shape app.py's
    exception handler produces, so a client never sees two error formats."""
    body = client.get("/_boom_unhandled", headers=ORIGIN).json()
    assert set(body) == {"detail"}
    assert body["detail"].startswith("internal error: ")


def test_a_non_browser_client_still_gets_the_500(client):
    """No Origin header (curl, the CLI, a script) — unchanged behaviour."""
    r = client.get("/_boom_unhandled")
    assert r.status_code == 500
    assert "kaboom" in r.json()["detail"]


def test_the_middleware_is_innermost(tmp_path):
    """Ordering IS the fix: added first => innermost => its response passes back
    out through CORSMiddleware. If someone later moves the add_middleware call
    below the CORS one, the header disappears again and this catches it."""
    from iron_jarvis.daemon.auth import ErrorEnvelopeMiddleware

    app = create_app(str(tmp_path))
    stack = [m.cls.__name__ for m in app.user_middleware]
    # user_middleware is OUTERMOST-first; ours must be last (innermost) and, in
    # particular, after CORSMiddleware.
    assert ErrorEnvelopeMiddleware.__name__ in stack
    assert stack.index(ErrorEnvelopeMiddleware.__name__) > stack.index("CORSMiddleware"), (
        f"ErrorEnvelopeMiddleware must sit INSIDE CORSMiddleware; stack is {stack}"
    )
