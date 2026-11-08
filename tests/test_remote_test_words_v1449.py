"""A remote agent's Test is worded through the failure rule (v1.449.0).

The readiness audit (docs/PRODUCT-READINESS-RESULTS.md 0h.5 item 19):
``POST /agents/remote/{name}/test`` answered only the probe's raw text
("probe failed", "request failed: ConnectError: [Errno 11001] …", "remote
returned HTTP 503: upstream down") and the card printed it as it was — the
one Test in the app not worded like a connection Test (v1.420.0, C6.6).

Now ``RemoteAgentRegistry.run`` answers TYPED facts beside its unchanged
``detail`` (``kind``: timeout | unreachable | http | shape | door, and the
HTTP ``status``), and ``remote.remote_test_words`` turns them into ``title``
+ ``fix`` (the headline of a timeout, an unreachable address or an error
status through ``failure_words.friendly_error``; the fix said for a Test on
the agent's card — never "Retry", never "this chat") plus the masked
``technical`` text. ``detail`` stays byte-identical for a headless caller.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from iron_jarvis.agents import remote
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers import failure_words as fw


class _FakeResp:
    def __init__(self, status_code: int, payload, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload


def _client(tmp_path) -> TestClient:
    client = TestClient(create_app(str(tmp_path)))
    r = client.post(
        "/agents/remote",
        json={"name": "hermes", "base_url": "http://192.168.1.9:9000/run", "kind": "http-task", "token": "t"},
    )
    assert r.status_code == 200, r.text
    return client


def _post_answering(monkeypatch, make):
    async def fake_post(self, url, json=None, headers=None, **kw):
        return make()

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)


def _post_raising(monkeypatch, exc: BaseException):
    async def fake_post(self, url, json=None, headers=None, **kw):
        raise exc

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)


# --------------------------------------------------------------------------
# the words, from the typed facts
# --------------------------------------------------------------------------


def test_an_unreachable_remote_reads_the_classifiers_headline_and_a_tests_fix():
    probe = {"ok": False, "detail": "request failed: ConnectError: [Errno 11001] getaddrinfo failed", "kind": "unreachable"}
    w = remote.remote_test_words("hermes", probe)
    assert w["title"] == "Hermes isn't connected right now"
    assert w["fix"] == remote.REMOTE_TEST_FIX["unreachable"]
    assert "Retry" not in w["fix"] and "chat" not in w["fix"]
    assert w["technical"] == probe["detail"]
    # The classifier's own words, never a second wording.
    assert w["title"] == fw.friendly_error(remote._RemoteProbeFailure("hermes", "unreachable", None), provider="hermes")["title"]


def test_a_timeout_names_the_time_limit_on_the_card():
    w = remote.remote_test_words("hermes", {"ok": False, "detail": "hermes did not answer within 15s", "kind": "timeout"})
    assert w["title"] == "Hermes didn't answer in time"
    assert "time limit" in w["fix"] and "Test again" in w["fix"]


@pytest.mark.parametrize("status", [401, 403])
def test_a_refused_token_points_at_the_cards_token(status):
    w = remote.remote_test_words("hermes", {"ok": False, "detail": f"remote returned HTTP {status}", "kind": "http", "status": status})
    assert w["title"] == "Hermes refused the token"
    assert "token" in w["fix"]


def test_an_error_status_is_answered_with_an_error():
    w = remote.remote_test_words("hermes", {"ok": False, "detail": "remote returned HTTP 503: upstream down", "kind": "http", "status": 503})
    assert w["title"] == "Hermes answered with an error"
    assert w["fix"] == remote.REMOTE_TEST_FIX["http"]
    assert w["technical"] == "remote returned HTTP 503: upstream down"


def test_a_wrong_shape_points_at_the_dialect():
    w = remote.remote_test_words("hermes", {"ok": False, "detail": "remote returned a non-JSON body", "kind": "shape"})
    assert w["title"] == "Hermes answered, but not in the shape Iron Jarvis expected"
    assert "dialect" in w["fix"]


def test_a_reply_is_one_line_and_a_door_keeps_its_own_sentence():
    assert remote.remote_test_words("hermes", {"ok": True, "detail": "ok"}) == {
        "title": "Hermes replied",
        "fix": "",
        "technical": "",
    }
    door = "desk said this computer is not allowed to send it tasks."
    w = remote.remote_test_words("desk", {"ok": False, "detail": door, "kind": "door", "status": 403})
    assert w == {"title": door, "fix": "", "technical": ""}


def test_the_technical_text_is_masked_and_bounded():
    detail = "request failed: 401 for token sk-ant-api03-" + "a" * 60 + " " + "x" * 600
    w = remote.remote_test_words("hermes", {"ok": False, "detail": detail, "kind": "unreachable"})
    assert "sk-ant-api03-" + "a" * 60 not in w["technical"]
    assert len(w["technical"]) <= 400


def test_an_untyped_old_probe_still_gets_plain_words():
    w = remote.remote_test_words("hermes", {"ok": False, "detail": "probe failed"})
    assert w["title"] == "Hermes didn't pass the check"
    assert w["fix"] == remote.REMOTE_TEST_FIX["other"]
    assert w["technical"] == "probe failed"


def test_the_words_never_raise():
    assert remote.remote_test_words("", {"ok": False, "detail": None, "kind": 7, "status": "x"})["title"]
    assert remote.remote_test_words("hermes", {}) is not None


# --------------------------------------------------------------------------
# run() carries the typed facts beside its unchanged detail
# --------------------------------------------------------------------------


def _record() -> remote.RemoteAgentRecord:
    return remote.RemoteAgentRecord(
        name="hermes", base_url="http://192.168.1.9:9000/run", kind="http-task", secret_name="", timeout_s=30
    )


@pytest.mark.asyncio
async def test_run_types_an_unreachable_address(monkeypatch):
    _post_raising(monkeypatch, httpx.ConnectError("[Errno 11001] getaddrinfo failed"))
    out = await remote.RemoteAgentRegistry.run(None, _record(), "x", None)
    assert out["ok"] is False and out["kind"] == "unreachable"
    assert out["detail"].startswith("request failed: ")
    # A TimeoutError the TRANSPORT raised (not the run's own deadline, the
    # v1.228.0 rule) keeps its own words and is typed the same way.
    _post_raising(monkeypatch, TimeoutError("socket read"))
    out = await remote.RemoteAgentRegistry.run(None, _record(), "x", None)
    assert out["kind"] == "unreachable" and out["detail"] == "request failed: socket read"


@pytest.mark.asyncio
async def test_run_types_an_error_status_and_a_wrong_shape(monkeypatch):
    _post_answering(monkeypatch, lambda: _FakeResp(503, None, text="upstream down"))
    out = await remote.RemoteAgentRegistry.run(None, _record(), "x", None)
    assert out["kind"] == "http" and out["status"] == 503
    assert out["detail"] == "remote returned HTTP 503: upstream down"
    _post_answering(monkeypatch, lambda: _FakeResp(200, None, text="<html>"))
    out = await remote.RemoteAgentRegistry.run(None, _record(), "x", None)
    assert out["kind"] == "shape" and out["detail"] == "remote returned a non-JSON body"
    _post_answering(monkeypatch, lambda: _FakeResp(200, {"nothing": 1}))
    out = await remote.RemoteAgentRegistry.run(None, _record(), "x", None)
    assert out["kind"] == "shape"


# --------------------------------------------------------------------------
# the route: title + fix + technical beside the unchanged detail
# --------------------------------------------------------------------------


def test_the_test_route_answers_words_and_keeps_detail(tmp_path, monkeypatch):
    client = _client(tmp_path)
    _post_raising(monkeypatch, httpx.ConnectError("[Errno 11001] getaddrinfo failed"))
    r = client.post("/agents/remote/hermes/test")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert body["detail"].startswith("request failed: ")
    assert body["title"] == "Hermes isn't connected right now"
    assert body["fix"] == remote.REMOTE_TEST_FIX["unreachable"]
    assert body["technical"].startswith("request failed: ")

    _post_answering(monkeypatch, lambda: _FakeResp(200, {"result": "ok"}))
    body = client.post("/agents/remote/hermes/test").json()
    assert body == {"ok": True, "detail": "hermes replied", "title": "Hermes replied", "fix": "", "technical": ""}


def test_no_test_word_is_the_engines(tmp_path, monkeypatch):
    """No title or fix names an exception class, an errno or 'probe'."""
    client = _client(tmp_path)
    cases = [
        lambda: _FakeResp(503, None, text="upstream down"),
        lambda: _FakeResp(401, None, text="nope"),
        lambda: _FakeResp(200, None, text="<html>"),
    ]
    for make in cases:
        _post_answering(monkeypatch, make)
        body = client.post("/agents/remote/hermes/test").json()
        for key in ("title", "fix"):
            assert "probe" not in body[key].lower(), body
            assert "Error" not in body[key] and "Errno" not in body[key], body
            assert "HTTP" not in body[key], body
