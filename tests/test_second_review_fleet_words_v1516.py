"""Second review, group 16C (v1.516.0): the Model servers page and the
account manager's words.

* F8: Refresh on the Model servers page forces a re-check. A server that
  failed three times serves a backoff penalty (up to ten minutes); a Refresh
  that honoured it left a server that came back refused until it ran out.
* F15: one probe has a total time limit. ``httpx``'s ``timeout=3.0`` bounds
  each read, not the request, so a server that trickles its answer held a
  probe (and the thread under it) for ever; and a probe makes several
  requests, so the probe as a whole is bounded too.
* F13 (decision E8): the page's errors are a plain sentence; the program's
  own words (``ConnectError: ...``) ride ``error_detail``, shown under
  Details, masked.
* A13: the account manager's sentences name no exception class.

Time is an injected clock (``probes.time`` replaced), never a sleep. Nothing
here reaches the network: every request is a stand-in.
"""

from __future__ import annotations

import pathlib
import time as real_time
import types
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from iron_jarvis.core.config import Config
from iron_jarvis.daemon.routes import fleet as fleet_routes
from iron_jarvis.fleet import probes
from iron_jarvis.fleet.models import FleetNode, NodeSnapshot
from iron_jarvis.fleet.sampler import FleetSampler

CLASS_NAMES = ("RuntimeError", "ConnectError", "OSError", "PermissionError", "ReadTimeout", "Exception:")


# --------------------------------------------------------------------------- #
# wiring
# --------------------------------------------------------------------------- #
class _Registry:
    """The surface the routes and the real sampler use, over a fixed list."""

    def __init__(self, nodes: list[FleetNode]) -> None:
        self._nodes = {n.id: n for n in nodes}
        self.reachable: dict[str, bool] = {}

    def nodes(self) -> list[FleetNode]:
        return list(self._nodes.values())

    def get(self, node_id: str):
        return self._nodes.get(node_id)

    def set_reachable(self, node_id: str, ok: bool) -> None:
        self.reachable[node_id] = ok


class _BoomSampler:
    def __getattr__(self, name):
        def _raise(*_a, **_kw):
            raise RuntimeError("sampler exploded")

        return _raise


def _wire(tmp_path: pathlib.Path, *, sampler: Any = None, nodes=(), providers: Any = None):
    cfg = Config(home=tmp_path, project_root=tmp_path)
    d = types.SimpleNamespace(
        platform=types.SimpleNamespace(config=cfg, providers=providers),
        fleet=_Registry(list(nodes)),
        fleet_sampler=sampler,
        _persist_config=lambda keys: None,
    )
    app = FastAPI()
    fleet_routes.register(app, d)
    return TestClient(app), d


def _no_class_names(text: str) -> None:
    for name in CLASS_NAMES:
        assert name not in text, (name, text)


# --------------------------------------------------------------------------- #
# F8: Refresh forces a re-check
# --------------------------------------------------------------------------- #
def test_refresh_rechecks_a_server_that_is_serving_a_backoff_penalty(tmp_path):
    node = FleetNode(id="box", base_url="http://box.test:8000", kind="openai-compat")
    registry = _Registry([node])
    down = {"on": True}
    calls: list[str] = []

    def probe(n, **_kw):
        calls.append(n.id)
        if down["on"]:
            raise ConnectionError("the server is down")
        return NodeSnapshot(node=n, status="online", evidence="direct"), []

    # A frozen logical clock: the backoff can never run out by itself.
    sampler = FleetSampler(registry, clock=lambda: 1000.0, probe=probe, node_timeout=5.0)
    for _ in range(3):
        sampler.sample_once()
    assert sampler.latest("box") is None  # never read
    assert sampler._due_nodes() == []  # serving its penalty

    down["on"] = False  # the user fixed it
    before = len(calls)
    client, _d = _wire(tmp_path, sampler=sampler, nodes=[node])
    client.get("/fleet/snapshot")  # a plain read never probes
    assert len(calls) == before
    r = client.get("/fleet/snapshot?refresh=1")
    assert r.status_code == 200, r.text
    assert len(calls) == before + 1, "Refresh did not check the server again"
    assert sampler.latest("box").status == "online"


def test_refresh_asks_the_sampler_to_force(tmp_path):
    seen: list[Any] = []

    class _Sampler:
        def touch(self):
            pass

        def snapshots(self):
            return []

        def status(self):
            return {"active": False, "interval": 30.0, "lease_expires_in": 0.0}

        def sample_once(self, *, force: bool = False):
            seen.append(force)
            return []

    client, _d = _wire(tmp_path, sampler=_Sampler())
    out = client.get("/fleet/snapshot?refresh=1").json()
    assert out["error"] == ""
    assert seen == [True]


# --------------------------------------------------------------------------- #
# F13 (E8): a plain sentence on screen, the program's words under Details
# --------------------------------------------------------------------------- #
def test_a_broken_fleet_stack_reads_as_one_plain_sentence_with_details(tmp_path):
    client, _d = _wire(tmp_path, sampler=_BoomSampler())
    for path in ("/fleet", "/fleet/snapshot?refresh=1"):
        out = client.get(path).json()
        assert out["nodes"] == []
        _no_class_names(out["error"])
        assert "sampler exploded" not in out["error"]
        assert out["error"] == fleet_routes.FLEET_READ_FAILED
        assert "RuntimeError: sampler exploded" in out["error_detail"]
    # The page's plain answer when nothing failed.
    ok_client, _ = _wire(tmp_path, sampler=_EmptySampler())
    assert ok_client.get("/fleet").json()["error_detail"] == ""


def test_a_failure_the_page_polls_is_logged_once_not_every_poll(caplog):
    """Lead's review: the Model servers page reads ``GET /fleet`` every two
    seconds and ``plain_failure`` logs the raw text each time. The same
    failure is logged once per ``READ_FAILED_LOG_EVERY_S``; the words on the
    page are the same every time."""
    fleet_routes._READ_FAILED_LOGGED.clear()
    now = [1000.0]
    exc = RuntimeError("sampler exploded")
    caplog.set_level("WARNING")

    def logged():
        return [r for r in caplog.records if "sampler exploded" in r.getMessage()]

    said = [fleet_routes._read_failed_words(exc, clock=lambda: now[0]) for _ in range(5)]
    assert said == [fleet_routes.FLEET_READ_FAILED] * 5
    assert len(logged()) == 1
    now[0] += fleet_routes.READ_FAILED_LOG_EVERY_S - 1
    fleet_routes._read_failed_words(exc, clock=lambda: now[0])
    assert len(logged()) == 1
    now[0] += 2
    fleet_routes._read_failed_words(exc, clock=lambda: now[0])
    assert len(logged()) == 2
    # A different failure is its own line, at once.
    fleet_routes._read_failed_words(RuntimeError("the table is gone"), clock=lambda: now[0])
    assert [r for r in caplog.records if "the table is gone" in r.getMessage()]
    fleet_routes._READ_FAILED_LOGGED.clear()


class _EmptySampler:
    def touch(self):
        pass

    def snapshots(self):
        return []

    def status(self):
        return {"active": False, "interval": 30.0, "lease_expires_in": 0.0}

    def sample_once(self, *, force: bool = False):
        return []


class _Providers:
    def __init__(self, exc: BaseException) -> None:
        self.exc = exc

    def get(self, provider, model=None):
        exc = self.exc

        class _Adapter:
            provider = "fleet-box"
            model = "qwen"

            async def complete(self, **_kw):
                raise exc

        return _Adapter()


@pytest.mark.parametrize(
    "exc, sentence",
    [
        (httpx.ConnectError("[Errno 111] Connection refused"), "The model server could not be reached."),
        (httpx.ReadTimeout("timed out"), "The model server did not answer in time."),
    ],
)
def test_a_check_that_failed_says_why_in_words(tmp_path, exc, sentence):
    node = FleetNode(id="box", base_url="http://box.test:8000", kind="openai-compat", routable=True)
    client, _d = _wire(tmp_path, sampler=_EmptySampler(), nodes=[node], providers=_Providers(exc))
    out = client.post("/fleet/nodes/box/verify", json={"model": "qwen"}).json()
    assert out["tool_use"] is None
    assert out["error"] == sentence
    _no_class_names(out["error"])
    assert type(exc).__name__ in out["error_detail"]

    rows = client.post("/fleet/verify-all").json()["results"]
    assert rows[0]["error"] == sentence
    assert type(exc).__name__ in rows[0]["error_detail"]


def test_the_details_never_carry_a_secret(tmp_path):
    exc = httpx.ConnectError("refused; api_key=test-token-value")
    node = FleetNode(id="box", base_url="http://box.test:8000", kind="openai-compat", routable=True)
    client, _d = _wire(tmp_path, sampler=_EmptySampler(), nodes=[node], providers=_Providers(exc))
    out = client.post("/fleet/nodes/box/verify", json={}).json()
    assert "ConnectError" in out["error_detail"]
    assert "test-token-value" not in out["error_detail"]
    assert "test-token-value" not in out["error"]


def test_the_add_form_probe_says_a_plain_sentence(tmp_path, monkeypatch):
    def boom(*_a, **_kw):
        raise PermissionError(13, "Permission denied", "C:\\somewhere")

    monkeypatch.setattr(probes, "detect_kind", boom)
    client, _d = _wire(tmp_path, sampler=_EmptySampler())
    out = client.post("/fleet/probe", json={"base_url": "http://box.test:8000"}).json()
    assert out["error"] == fleet_routes.PROBE_FAILED
    _no_class_names(out["error"])
    assert "Permission denied" in out["error_detail"]


# --------------------------------------------------------------------------- #
# F15: one probe has a total time limit
# --------------------------------------------------------------------------- #
class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now


def _fake_time(monkeypatch) -> _Clock:
    clock = _Clock()
    monkeypatch.setattr(
        probes,
        "time",
        types.SimpleNamespace(
            monotonic=clock.monotonic, perf_counter=real_time.perf_counter, time=real_time.time
        ),
    )
    return clock


class _Resp:
    def __init__(self, status: int = 404, body: Any = None) -> None:
        self.status_code = status
        self._body = body if body is not None else {}
        self.text = ""

    def json(self):
        return self._body


def test_detection_stops_asking_once_its_time_is_spent(monkeypatch):
    clock = _fake_time(monkeypatch)
    asked: list[str] = []

    def slow(url, headers=None):
        asked.append(url)
        clock.now += 6.0  # every answer takes six seconds
        return _Resp(404)

    kind, reason = probes.detect_kind("http://box.test:8000", get=slow)
    assert kind == "unknown"
    assert len(asked) <= 2, f"detection kept asking for {clock.now:.0f} s: {asked}"
    assert clock.now <= probes.PROBE_TOTAL_S + 6.0
    assert reason == probes.PROBE_OUT_OF_TIME


def test_a_probe_stops_asking_once_its_time_is_spent(monkeypatch):
    clock = _fake_time(monkeypatch)
    asked: list[str] = []

    def slow(url, headers=None):
        asked.append(url)
        clock.now += 60.0  # one answer outlasts the probe's whole limit
        return _Resp(200, {"models": []})

    node = FleetNode(id="box", base_url="http://box.test:11434", kind="ollama")
    snap, _children = probes.probe_node(node, get=slow)
    # /api/ps answered (late); /api/tags, a bonus, is not asked past the limit.
    assert asked == ["http://box.test:11434/api/ps"]
    assert snap.status == "online"


class _TrickleResponse:
    """A server that sends one byte a second, six hundred times."""

    def __init__(self, clock: _Clock) -> None:
        self.status_code = 200
        self.headers = httpx.Headers({})
        self.request = None
        self._clock = clock

    def iter_raw(self):
        for _ in range(600):
            self._clock.now += 1.0
            yield b" "

    def read(self) -> bytes:
        return b"".join(self.iter_raw())


def test_one_request_has_a_total_time_limit(monkeypatch):
    clock = _fake_time(monkeypatch)

    class _Stream:
        def __init__(self, *_a, **_kw) -> None:
            self.resp = _TrickleResponse(clock)

        def __enter__(self):
            return self.resp

        def __exit__(self, *_exc):
            return False

    def whole_get(*_a, **_kw):
        # What ``httpx.get`` does: read the whole answer, however long it takes.
        resp = _TrickleResponse(clock)
        resp.read()
        return resp

    monkeypatch.setattr(httpx, "stream", _Stream)
    monkeypatch.setattr(httpx, "get", whole_get)
    with pytest.raises(httpx.TimeoutException):
        probes._get("http://box.test:8000/metrics")
    assert clock.now <= probes.GET_TOTAL_S + 1.0, f"one request ran for {clock.now:.0f} s"
    # And it reads as "timed out" on the page.
    assert probes._human_error(httpx.ReadTimeout("x")) == "timed out"
    # Lead's review: an error with no phrase of its own is a phrase too,
    # never the library's text or a class name on a server's row.
    assert probes._human_error(ValueError("<html>502 Bad Gateway</html>")) == "could not be read"
    assert probes._human_error(KeyError()) == "could not be read"
    assert probes._human_error(Exception("[SSL: CERTIFICATE_VERIFY_FAILED] bad cert")) == "certificate problem"


# --------------------------------------------------------------------------- #
# A13: the account manager's sentences name no exception class
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "exc, words",
    [
        (httpx.ConnectError("refused"), "did not answer at"),
        (httpx.ReadTimeout("slow"), "did not answer at"),
    ],
)
def test_the_account_managers_unreachable_sentence_is_plain(exc, words):
    from iron_jarvis.iron_proxy.client import IronProxyClient, IronProxyError

    def handler(request):
        raise exc

    client = IronProxyClient("http://127.0.0.1:9", "test-token-value")
    client._http = httpx.Client(base_url=client.url, transport=httpx.MockTransport(handler))
    with pytest.raises(IronProxyError) as info:
        client._call("GET", "/health")
    message = info.value.message
    assert info.value.code == "UNREACHABLE"
    assert words in message
    _no_class_names(message)
    assert "(" not in message, message
    client.close()


def test_the_account_managers_start_failure_is_plain():
    from iron_jarvis.iron_proxy.service import IronProxyService

    class _Fake:
        _error = ""

        def _start_locked(self):
            raise OSError(5, "Access is denied")

    fake = _Fake()
    IronProxyService._start_guarded(fake)  # type: ignore[arg-type]
    assert fake._error.startswith("Iron-Proxy could not be started")
    _no_class_names(fake._error)
    assert "(" not in fake._error, fake._error
