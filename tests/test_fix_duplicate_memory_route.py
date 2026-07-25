"""POST /memory was registered TWICE (v1.94.1 fix).

``routes/learning.py`` declared the endpoint at two places. FastAPI dispatches
the FIRST match, so the second (richer) handler was dead code — while
``/openapi.json`` advertised ITS schema, because OpenAPI generation keeps the
LAST registration for a (method, path). Net effect: ``scope_id`` was documented
but silently dropped, and the served layer default was "user" while the
documented model said "project".

The first test here is the general guard — it fails on ANY duplicated route in
the whole app, not just this one — because the specific bug is far less likely
to recur than the class of bug.
"""

from __future__ import annotations

from collections import defaultdict

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app


def _routes(app):
    """Every (method, path) the app serves, ignoring the automatic HEAD/OPTIONS."""
    for r in app.routes:
        path, methods = getattr(r, "path", None), getattr(r, "methods", None)
        if not path or not methods:
            continue
        for m in methods:
            if m not in ("HEAD", "OPTIONS"):
                yield (m, path), r


def test_no_duplicate_route_registrations(tmp_path):
    """No (method, path) may be registered twice: the later handler would be
    unreachable while OpenAPI documents it as if it were live."""
    app = create_app(str(tmp_path))
    seen: dict[tuple[str, str], list[str]] = defaultdict(list)
    for key, r in _routes(app):
        fn = getattr(r, "endpoint", None)
        seen[key].append(
            f"{fn.__module__}:{fn.__code__.co_firstlineno}" if fn else "?"
        )
    dupes = {k: v for k, v in seen.items() if len(v) > 1}
    assert not dupes, f"duplicate route registrations (first wins, rest are dead): {dupes}"


def test_openapi_schema_matches_the_handler_that_actually_runs(tmp_path):
    """The documented request body must be the one dispatch really uses."""
    app = create_app(str(tmp_path))
    spec = TestClient(app).get("/openapi.json").json()
    ref = spec["paths"]["/memory"]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"]["$ref"]
    props = spec["components"]["schemas"][ref.rsplit("/", 1)[-1]]["properties"]
    assert "scope_id" in props  # documented...
    # ...and honored: a scoped write is readable back only at that scope.
    client = TestClient(app)
    w = client.post(
        "/memory",
        json={"layer": "project", "key": "k", "text": "scoped", "scope_id": "proj-1"},
    )
    assert w.status_code == 200, w.text
    assert w.json()["scope_id"] == "proj-1"
    assert client.get("/memory/project/k", params={"scope_id": "proj-1"}).json()[
        "text"
    ] == "scoped"
    # The unscoped read must NOT see it (scope_id=None means IS NULL, not "any").
    assert client.get("/memory/project/k").status_code == 404


def test_layer_default_stays_user(tmp_path):
    """The live default was "user"; the dead handler's model said "project".
    Preserve what actually shipped so a body omitting ``layer`` is unaffected."""
    client = TestClient(create_app(str(tmp_path)))
    body = client.post("/memory", json={"key": "pref", "text": "dark mode"}).json()
    assert body["layer"] == "user"
    assert client.get("/memory/user/pref").json()["text"] == "dark mode"


def test_blank_key_falls_back_and_the_response_echoes_the_record(tmp_path):
    """Carried over from the handler that was live: a blank key becomes "note".
    The response reports the RECORD, so the substitution is visible instead of
    being reflected back as sent."""
    client = TestClient(create_app(str(tmp_path)))
    body = client.post("/memory", json={"key": "   ", "text": "x"}).json()
    assert body["key"] == "note"
    assert client.get("/memory/user/note").json()["text"] == "x"


def test_unknown_layer_is_400_and_db_errors_stay_500(tmp_path, monkeypatch):
    """An unknown layer is bad input; anything else must NOT be laundered into
    a 400 (the old live handler caught bare Exception and hid real failures)."""
    app = create_app(str(tmp_path))
    client = TestClient(app)
    r = client.post("/memory", json={"layer": "bogus", "key": "k", "text": "t"})
    assert r.status_code == 400
    assert "layer" in r.json()["detail"]

    def _boom(*a, **kw):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(app.state.platform.memory, "write", _boom, raising=False)
    try:
        r = client.post("/memory", json={"key": "k", "text": "t"})
    except RuntimeError:
        return  # TestClient re-raises server errors — also proof it isn't a 400
    assert r.status_code == 500
