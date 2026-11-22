"""Next leaps 43-a (R1.7): the model library — browse, what fits this PC,
download with the size shown first, progress and Cancel.

Every request here goes to ONE stand-in server on its own port (127.0.0.1,
an ephemeral port): it answers as Ollama (``/api/tags``, ``/api/pull``), as
Ollama's registry and Hugging Face's (``/v2/.../manifests/...``) and as
Hugging Face's search (``/api/models``). No test downloads a real model,
reaches Hugging Face or touches the owner's Ollama (the suite's conftest
points the registry and the search at a closed port and keeps the real
Ollama probe off). Time is an injected clock; a wait is a join on the
download's own thread or an Event, never a sleep.
"""

from __future__ import annotations

import asyncio
import json
import threading
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from iron_jarvis.core.config import Config
from iron_jarvis.core.db import make_engine
from iron_jarvis.core.events import EventBus, EventType
from iron_jarvis.daemon.routes import fleet as fleet_routes
from iron_jarvis.fleet import hardware as HW
from iron_jarvis.fleet import library as L
from iron_jarvis.settings import records_library as LT
from iron_jarvis.fleet import pulls as P
from iron_jarvis.fleet.models import FleetNode

#: A download size, as the registries print it (decimal).
GB = 1_000_000_000
#: Memory and drive room, as Windows shows them (binary, labelled GB).
GIB = 1024**3
#: The manifest the stand-in registry has for llama3.2 (config + two layers).
LLAMA_LAYERS = (2_019_377_376, 12_403)
LLAMA_CONFIG = 561
LLAMA_MEASURED = LLAMA_CONFIG + sum(LLAMA_LAYERS)
BIG = "sha256:6a0746a1ec1aef3e7ec53868f220ff6e389f6f8ef87a01d77c96807de94ca2aa"
SMALL = "sha256:4fa551d4f938f68b8c1e6afa9d28befb70e3f33f75d0753248d530364aeea40f"


def _recorded_pull() -> list[dict[str, Any]]:
    """An Ollama ``/api/pull`` answer as Ollama writes it: one line per
    layer's progress, then verify, write, success."""
    big = LLAMA_LAYERS[0]
    lines: list[dict[str, Any]] = [{"status": "pulling manifest"}]
    for done in (0, big // 4, big // 2, (big * 3) // 4, big):
        lines.append({"status": "pulling 6a0746a1ec1a", "digest": BIG, "total": big, "completed": done})
    lines.append({"status": "pulling 4fa551d4f938", "digest": SMALL, "total": LLAMA_LAYERS[1],
                  "completed": LLAMA_LAYERS[1]})
    lines += [{"status": "verifying sha256 digest"}, {"status": "writing manifest"}, {"status": "success"}]
    return lines


# --------------------------------------------------------------------------- #
# the stand-in server
# --------------------------------------------------------------------------- #
class StandIn:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.installed: list[dict[str, Any]] = []
        self.pull_lines = _recorded_pull()
        self.mode = "ok"  # ok | hang | error
        self.release = threading.Event()
        self.started = threading.Event()
        self.manifests: dict[str, dict[str, Any]] = {
            "/v2/library/llama3.2/manifests/latest": {
                "config": {"size": LLAMA_CONFIG},
                "layers": [{"size": LLAMA_LAYERS[0]}, {"size": LLAMA_LAYERS[1]}],
            },
            "/v2/bartowski/Qwen3-8B-GGUF/manifests/latest": {
                "config": {"size": 1000}, "layers": [{"size": 5 * GB}],
            },
        }
        self.hf_rows: list[dict[str, Any]] = [
            {"id": "bartowski/Qwen3-8B-GGUF", "downloads": 1234},
            {"id": "evil/<script>alert(1)</script>", "downloads": 1},
            {"id": "no-owner"},
        ]
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *a: Any) -> None:  # quiet
                pass

            def _note(self, body: bytes = b"") -> None:
                u = urlsplit(self.path)
                outer.requests.append({
                    "method": self.command, "path": u.path, "query": parse_qs(u.query),
                    "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body,
                })

            def _json(self, code: int, obj: Any) -> None:
                data = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:  # noqa: N802
                self._note()
                path = urlsplit(self.path).path
                if path == "/api/tags":
                    return self._json(200, {"models": outer.installed})
                if path in outer.manifests:
                    return self._json(200, outer.manifests[path])
                if path.startswith("/v2/"):
                    return self._json(404, {"errors": [{"code": "MANIFEST_UNKNOWN"}]})
                if path == "/api/models":
                    return self._json(200, outer.hf_rows)
                return self._json(404, {})

            def do_POST(self) -> None:  # noqa: N802
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else b""
                self._note(body)
                if urlsplit(self.path).path != "/api/pull":
                    return self._json(404, {})
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.end_headers()
                try:
                    if outer.mode == "error":
                        self.wfile.write(b'{"status":"pulling manifest"}\n')
                        self.wfile.write(b'{"error":"pull model manifest: file does not exist"}\n')
                        self.wfile.flush()
                        return
                    for i, line in enumerate(outer.pull_lines):
                        self.wfile.write(json.dumps(line).encode() + b"\n")
                        self.wfile.flush()
                        if outer.mode == "hang" and i == 2:
                            outer.started.set()
                            outer.release.wait(10)
                            return
                    name = json.loads(body or b"{}").get("model")
                    outer.installed.append({"name": f"{name}:latest" if ":" not in name else name,
                                            "size": LLAMA_MEASURED})
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                    return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def pulls(self) -> list[dict[str, Any]]:
        return [r for r in self.requests if r["path"] == "/api/pull"]

    def close(self) -> None:
        self.release.set()
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture()
def standin(monkeypatch):
    s = StandIn()
    for host in list(L.REGISTRY_BASES):
        monkeypatch.setitem(L.REGISTRY_BASES, host, s.url)
    monkeypatch.setattr(L, "HF_API", s.url)
    L.forget_measured()
    yield s
    s.close()
    L.forget_measured()


# --------------------------------------------------------------------------- #
# wiring
# --------------------------------------------------------------------------- #
class _Registry:
    def __init__(self, nodes: list[FleetNode]) -> None:
        self._nodes = {n.id: n for n in nodes}

    def nodes(self) -> list[FleetNode]:
        return list(self._nodes.values())

    def get(self, node_id: str):
        return self._nodes.get(node_id)

    def headers_for(self, node) -> dict[str, str]:
        return {}


class _Tick:
    """An injected clock: each read is 1.5 s after the one before, so every
    whole-percent step is far enough apart to be published."""

    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        self.t += 1.5
        return self.t


#: A PC sold as 64 GB, as psutil reads it (a little under 64 GiB), with
#: 412 GB free as Explorer shows it.
HW64 = HW.Hardware(total_ram=int(64 * GIB * 0.995), free_ram=40 * GIB, gpus=None, model_dir="C:/models",
                   model_disk_free=412 * GIB, model_disk_total=1000 * GIB)


def _wire(tmp_path, nodes, *, hw: HW.Hardware = HW64, monkeypatch=None, persist=False):
    engine = make_engine(tmp_path / "ij.db")
    bus = EventBus()
    if persist:
        from iron_jarvis.core.db import persist_event

        bus.add_handler(lambda ev: persist_event(engine, ev))
    seen: list[tuple[str, dict[str, Any]]] = []
    real = bus.publish

    async def spy(etype, payload=None, session_id=None, **kw):
        seen.append((str(etype), dict(payload or {})))
        return await real(etype, payload, session_id=session_id, **kw)

    bus.publish = spy  # type: ignore[method-assign]
    platform = types.SimpleNamespace(
        engine=engine, event_bus=bus, config=Config(home=tmp_path, project_root=tmp_path), providers=None,
    )
    d = types.SimpleNamespace(platform=platform, fleet=_Registry(list(nodes)), fleet_sampler=None,
                              _persist_config=lambda keys: None)
    done: list[tuple[str, str]] = []
    platform._model_pulls = P.PullManager(
        engine, bus, clock=_Tick(), on_done=lambda n, m: done.append((n, m)), places=P._places_of(d)
    )
    if monkeypatch is not None:
        monkeypatch.setattr(L, "hardware_for", lambda target, force=False: hw if target.this_pc else HW.NOT_KNOWN)
    app = FastAPI()
    fleet_routes.register(app, d)
    return d, app, seen, done


def _box(url: str, kind: str = "ollama", node_id: str = "box", routable: bool = False) -> FleetNode:
    return FleetNode(id=node_id, label="Ollama box", base_url=url, kind=kind, routable=routable)


# --------------------------------------------------------------------------- #
# 1. what fits
# --------------------------------------------------------------------------- #
def test_fit_marks_against_fixed_hardware():
    by_id = {e.id: e for e in L.catalog()}
    assert {"llama3.2", "qwen3-next-80b", "qwen3.5-122b-a10b", "muse-glimmer-30b"} <= set(by_id)

    def mark(entry_id: str, hw: HW.Hardware) -> str:
        e = by_id[entry_id]
        return L.fit(L.memory_needed(e.params_b, e.about_bytes), hw)[0]

    assert mark("llama3.2", HW64) == L.FITS
    assert mark("qwen3-next-80b", HW64) == L.TIGHT
    assert mark("qwen3.5-122b-a10b", HW64) == L.TOO_BIG
    # A graphics card the model fits in decides, even on a PC with less memory.
    card = HW.Hardware(total_ram=16 * GB, gpus=(HW.Gpu("RTX", 24 * GB, 20 * GB),))
    assert L.fit(L.memory_needed(30, 18 * GB), card) == (L.FITS, "graphics")
    # Nothing read is never a guess.
    assert mark("llama3.2", HW.Hardware(total_ram=None)) == L.UNKNOWN
    assert mark("llama3.2", HW.NOT_KNOWN) == L.UNKNOWN
    assert L.fit_words(L.FITS, HW64) == "Fits this PC (64 GB)"
    # Every shipped entry was checked on a date and names a local model only.
    for e in L.catalog():
        assert e.checked and not L.is_cloud_name(e.ollama), e
    assert L.entry_for("Qwen3 8B").ollama == "qwen3:8b" and L.entry_for("qwen3:8b").id == "qwen3-8b"


def test_the_library_view_marks_the_list_and_says_only_what_was_read(tmp_path, standin, monkeypatch):
    d, app, _seen, _done = _wire(tmp_path, [_box(standin.url)], monkeypatch=monkeypatch)
    standin.installed = [{"name": "llama3.2:latest", "size": LLAMA_MEASURED}]
    with TestClient(app) as c:
        view = c.get("/fleet/library").json()
    assert view["server"]["id"] == "box" and view["server"]["downloads"] is True
    rows = {r["id"]: r for r in view["models"]}
    assert rows["llama3.2"]["installed"] is True and rows["llama3.2"]["can_download"] is False
    assert rows["qwen3-8b"]["can_download"] is True and rows["qwen3-8b"]["size_from"] == "about"
    assert rows["qwen3.5-122b-a10b"]["fit"] == L.TOO_BIG
    s = view["summary"]
    assert s.startswith("This PC has 64 GB of memory and 412 GB free on the drive Ollama uses.")
    assert "Graphics memory is not known" in s and " models below fit." in s
    # No search words, no Hugging Face request.
    assert not [r for r in standin.requests if r["path"] == "/api/models"]


def test_a_server_on_another_computer_reads_not_known(tmp_path, standin, monkeypatch):
    remote = FleetNode(id="desk", label="Desk", base_url="http://192.168.1.20:11434", kind="ollama")
    d, *_ = _wire(tmp_path, [remote], monkeypatch=monkeypatch)
    monkeypatch.setattr(L, "installed_on", lambda target, headers=None: ([], "it did not answer"))
    view = L.library_view(d, "desk")
    assert view["summary"] == L.NOT_KNOWN_WORDS
    assert {r["fit"] for r in view["models"]} == {L.UNKNOWN}
    assert view["hardware"]["this_pc"] is False and view["hardware"]["model_disk_free"] is None


def test_a_server_that_cannot_download_says_why_and_gets_no_press(tmp_path, standin, monkeypatch):
    d, app, *_ = _wire(tmp_path, [_box(standin.url, kind="vllm", node_id="v")], monkeypatch=monkeypatch)
    view = L.library_view(d, "v")
    assert view["server"]["downloads"] is False and "vLLM serves the one model" in view["server"]["download_why"]
    assert not any(r["can_download"] for r in view["models"])
    assert L.ABILITIES["vllm"]["download"] is False and L.ABILITIES["ollama"]["download"] is True
    with TestClient(app) as c:
        r = c.post("/fleet/nodes/v/models/download", json={"model": "qwen3:8b"})
    assert r.status_code == 409 and "vLLM" in r.json()["detail"]
    assert standin.pulls() == []


# --------------------------------------------------------------------------- #
# 2. the size before it starts
# --------------------------------------------------------------------------- #
def test_the_size_is_measured_from_the_registry_and_a_server_with_no_answer_reads_about(tmp_path, standin, monkeypatch):
    d, app, *_ = _wire(tmp_path, [_box(standin.url)], monkeypatch=monkeypatch)
    with TestClient(app) as c:
        measured = c.post("/fleet/nodes/box/models/size", json={"model": "llama3.2"}).json()
        # The registry has no manifest for qwen3:8b: the list's own size, marked "about".
        about = c.post("/fleet/nodes/box/models/size", json={"model": "Qwen3 8B"}).json()
    assert measured["size_bytes"] == LLAMA_MEASURED and measured["size_from"] == "measured"
    assert measured["size_words"] == "2.0 GB" and measured["free_after"] == 412 * GIB - LLAMA_MEASURED
    # The download's size as the registry prints it; the drive's room as Explorer shows it.
    assert measured["free_after_words"] == "410 GB free after"
    assert measured["source"] == "Ollama's library" and measured["can_start"] is True
    assert "free after" in measured["card_words"] and "Llama 3.2 3B" in measured["card_words"]
    assert about["model"] == "qwen3:8b" and about["size_from"] == "about"
    assert about["size_bytes"] == L.entry_for("qwen3:8b").about_bytes and about["size_words"].startswith("about ")
    manifest_reads = [r for r in standin.requests if r["path"].startswith("/v2/")]
    assert [r["path"] for r in manifest_reads] == [
        "/v2/library/llama3.2/manifests/latest", "/v2/library/qwen3/manifests/8b",
    ]
    assert standin.pulls() == [], "sizing a download never starts one"


def test_a_download_past_the_free_space_floor_is_refused_before_any_request(tmp_path, standin, monkeypatch):
    tight = HW.Hardware(total_ram=64 * GB, model_dir="C:/models", model_disk_free=12 * GB,
                        model_disk_total=100 * GB)
    d, app, seen, _ = _wire(tmp_path, [_box(standin.url)], hw=tight, monkeypatch=monkeypatch)
    with TestClient(app) as c:
        r = c.post("/fleet/nodes/box/models/download", json={"model": "llama3.2"})
        listed = c.get("/fleet/downloads").json()["downloads"]
    assert r.status_code == 409
    words = r.json()["detail"]
    assert words.startswith("Not downloaded: llama3.2 needs 2.0 GB") and "a tenth of that drive" in words
    assert standin.pulls() == [], "the model server was asked before the floor was checked"
    assert listed == [] and not [s for s in seen if s[0].startswith("model.download")]
    # The line itself: a tenth of the drive is the floor (D-43.2).
    ok = HW.Hardware(total_ram=1, model_disk_free=13 * GB, model_disk_total=100 * GB)
    assert L.floor_problem("x", 3 * GB, ok) == ""
    assert L.floor_problem("x", 3 * GB + 1, ok) != ""
    assert L.floor_problem("x", None, ok).startswith("Not downloaded: the size of x could not be read")
    assert L.floor_problem("x", 999 * GB, HW.NOT_KNOWN) == ""  # not known: said on the card, not refused


def test_a_cloud_model_or_a_strange_name_is_never_downloaded(tmp_path, standin, monkeypatch):
    d, app, *_ = _wire(tmp_path, [_box(standin.url)], monkeypatch=monkeypatch)
    with TestClient(app) as c:
        cloud = c.post("/fleet/nodes/box/models/download", json={"model": "gpt-oss:120b-cloud"})
        odd = c.post("/fleet/nodes/box/models/download", json={"model": "../../etc http://x"})
    assert cloud.status_code == 409 and "Ollama's cloud models" in cloud.json()["detail"]
    assert odd.status_code == 400
    assert standin.pulls() == [] and [r for r in standin.requests if r["path"].startswith("/v2/")] == []
    assert L.clean_model_name("hf.co/bartowski/Qwen3-8B-GGUF:Q4_K_M") == "hf.co/bartowski/Qwen3-8B-GGUF:Q4_K_M"
    assert L.clean_model_name("other.host/a/b") == ""
    assert L.manifest_url("hf.co/bartowski/Qwen3-8B-GGUF") == f"{standin.url}/v2/bartowski/Qwen3-8B-GGUF/manifests/latest"


# --------------------------------------------------------------------------- #
# 3. progress, the end, Cancel
# --------------------------------------------------------------------------- #
def test_progress_events_from_a_recorded_ollama_stream_reach_done(tmp_path, standin, monkeypatch):
    d, app, seen, done = _wire(tmp_path, [_box(standin.url)], monkeypatch=monkeypatch)
    mgr = d.platform._model_pulls
    with TestClient(app) as c:
        r = c.post("/fleet/nodes/box/models/download", json={"model": "llama3.2"})
        assert r.status_code == 200, r.text
        row = r.json()["download"]
        assert row["state"] == P.DOWNLOADING and row["bytes_total"] == LLAMA_MEASURED and row["size_from"] == "measured"
        end = mgr.wait(row["id"], timeout=20)
        listed = c.get("/fleet/downloads").json()["downloads"]
    assert end["state"] == P.DONE and end["percent"] == 100 and end["error"] == ""
    assert end["bytes_done"] == LLAMA_MEASURED - LLAMA_CONFIG or end["bytes_done"] == LLAMA_MEASURED
    pull = standin.pulls()
    assert len(pull) == 1 and json.loads(pull[0]["body"]) == {"model": "llama3.2", "name": "llama3.2", "stream": True}
    progress = [p for t, p in seen if t == EventType.MODEL_DOWNLOAD_PROGRESS]
    # Every progress event names the row as the list does, so the row never
    # reads as the bare model id while it runs.
    assert {p["name"] for p in progress} == {"Llama 3.2 3B"}, progress[:2]
    pcts = [p["percent"] for p in progress]
    assert pcts == sorted(pcts) and len(pcts) == len(set(pcts)), pcts
    assert pcts[0] == 0 and pcts[-1] == 100 and {24, 49, 74} & set(pcts), pcts
    assert len(progress) <= 101
    ended = [p for t, p in seen if t == EventType.MODEL_DOWNLOAD_ENDED]
    assert len(ended) == 1 and ended[0]["state"] == P.DONE and ended[0]["id"] == row["id"]
    assert done == [("box", "llama3.2")], "the model menu was not told to read the server again"
    assert listed[0]["id"] == row["id"] and listed[0]["state"] == P.DONE
    assert any(L.same_model(m["name"], "llama3.2") for m in standin.installed)


def test_cancel_ends_the_row_as_stopped_and_says_ollama_keeps_the_part(tmp_path, standin, monkeypatch):
    standin.mode = "hang"
    d, app, seen, done = _wire(tmp_path, [_box(standin.url)], monkeypatch=monkeypatch)
    with TestClient(app) as c:
        row = c.post("/fleet/nodes/box/models/download", json={"model": "llama3.2"}).json()["download"]
        assert standin.started.wait(10), "the stand-in never started streaming"
        r = c.post(f"/fleet/downloads/{row['id']}/cancel")
    assert r.status_code == 200
    got = r.json()["download"]
    assert got["state"] == P.STOPPED and got["error"] == P.CANCELLED_WORDS
    assert "Ollama keeps the part already downloaded" in got["error"]
    assert done == [] and not any(L.same_model(m["name"], "llama3.2") for m in standin.installed)
    ended = [p for t, p in seen if t == EventType.MODEL_DOWNLOAD_ENDED]
    assert [e["state"] for e in ended] == [P.STOPPED]


def test_one_download_at_a_time_per_server(tmp_path, standin, monkeypatch):
    standin.mode = "hang"
    d, app, *_ = _wire(tmp_path, [_box(standin.url)], monkeypatch=monkeypatch)
    with TestClient(app) as c:
        first = c.post("/fleet/nodes/box/models/download", json={"model": "llama3.2"}).json()["download"]
        assert standin.started.wait(10)
        second = c.post("/fleet/nodes/box/models/download", json={"model": "qwen3:8b"})
        c.post(f"/fleet/downloads/{first['id']}/cancel")
    assert second.status_code == 409
    assert "Ollama box is already downloading Llama 3.2 3B" in second.json()["detail"]
    assert len(standin.pulls()) == 1


def test_a_server_that_says_no_is_a_failed_row_in_its_own_words(tmp_path, standin, monkeypatch):
    standin.mode = "error"
    d, app, seen, done = _wire(tmp_path, [_box(standin.url)], monkeypatch=monkeypatch)
    with TestClient(app) as c:
        row = c.post("/fleet/nodes/box/models/download", json={"model": "llama3.2"}).json()["download"]
        end = d.platform._model_pulls.wait(row["id"], timeout=20)
    assert end["state"] == P.FAILED
    assert end["error"] == "The model server said: pull model manifest: file does not exist"
    assert done == []


def test_a_download_an_earlier_daemon_left_running_is_settled_as_stopped(tmp_path):
    from iron_jarvis.core.db import session_scope
    from iron_jarvis.fleet.pull_models import ModelDownload

    engine = make_engine(tmp_path / "ij.db")
    first = P.PullManager(engine)
    with session_scope(engine) as db:
        db.add(ModelDownload(id="dl_old", node_id="ollama", model="qwen3:8b", state=P.DOWNLOADING, bytes_done=5))
        db.commit()
    again = P.PullManager(engine)
    assert again.settle() == 1 and again.settle() == 0
    row = again.get("dl_old")
    assert row["state"] == P.STOPPED and row["error"] == P.APP_CLOSED_WORDS and row["finished_at"]
    assert first is not again


# --------------------------------------------------------------------------- #
# 4. Hugging Face only when typed, only the words
# --------------------------------------------------------------------------- #
def test_the_hugging_face_search_sends_only_the_typed_words(tmp_path, standin, monkeypatch):
    d, app, *_ = _wire(tmp_path, [_box(standin.url)], monkeypatch=monkeypatch)
    with TestClient(app) as c:
        c.get("/fleet/library?node=box")
        assert not [r for r in standin.requests if r["path"] == "/api/models"]
        view = c.get("/fleet/library", params={"node": "box", "q": "  qwen\n8b   coder  "}).json()
    asked = [r for r in standin.requests if r["path"] == "/api/models"]
    assert len(asked) == 1
    q = {k: v[0] for k, v in asked[0]["query"].items()}
    assert q == {"search": "qwen 8b coder", **L.HF_FIXED_PARAMS}
    sent = asked[0]["headers"]
    assert not {"authorization", "cookie"} & set(sent), sent
    found = view["search"]["models"]
    assert [m["model"] for m in found] == ["hf.co/bartowski/Qwen3-8B-GGUF"], "outside text was not kept to a name"
    assert found[0]["params_b"] == 8 and found[0]["source"] == "huggingface"
    # Its size is measured from Hugging Face's own manifest before it starts.
    pv = L.preview(d, "box", found[0]["model"])
    assert pv["size_from"] == "measured" and pv["source"] == "Hugging Face"


# --------------------------------------------------------------------------- #
# 5. this PC's numbers
# --------------------------------------------------------------------------- #
def test_graphics_memory_from_nvidia_smi_is_read_through_procs_and_kept_a_minute(monkeypatch):
    calls: list[list[str]] = []

    def runner(argv, *, timeout):
        calls.append(list(argv))
        assert timeout == HW.NVIDIA_SMI_TIMEOUT_S
        return types.SimpleNamespace(returncode=0, timed_out=False, stdout=b"NVIDIA GeForce RTX 4090, 24564, 22000\n")

    monkeypatch.setattr(HW, "nvidia_smi_path", lambda: "C:/nv/nvidia-smi.exe")
    cards = HW.read_gpus(runner=runner)
    assert cards == (HW.Gpu("NVIDIA GeForce RTX 4090", 24564 * 1024 * 1024, 22000 * 1024 * 1024),)
    assert calls == [["C:/nv/nvidia-smi.exe", *HW.NVIDIA_SMI_ARGS]]
    hung = HW.read_gpus(runner=lambda argv, timeout: types.SimpleNamespace(returncode=-1, timed_out=True, stdout=b""))
    assert hung is None and HW.parse_nvidia_smi("garbage") is None
    # The real call goes through core/procs with its bound.
    from iron_jarvis.core import procs

    seen: list[Any] = []
    monkeypatch.setattr(procs, "run", lambda argv, timeout: seen.append(timeout) or types.SimpleNamespace(
        returncode=0, timed_out=False, stdout=b"A, 8192, 100\n"))
    t = [0.0]
    monkeypatch.setattr(HW, "_gpu_cache", None)
    assert HW.gpus(clock=lambda: t[0])[0].name == "A"
    t[0] = HW.CACHE_S - 1
    HW.gpus(clock=lambda: t[0])
    assert seen == [HW.NVIDIA_SMI_TIMEOUT_S], "nvidia-smi ran twice inside a minute"
    t[0] = HW.CACHE_S + 1
    HW.gpus(clock=lambda: t[0])
    assert len(seen) == 2


def test_the_drive_is_read_on_the_nearest_folder_that_exists(tmp_path):
    free, total = HW.disk_of(tmp_path / "not" / "made" / "yet")
    assert isinstance(free, int) and isinstance(total, int) and 0 < free <= total
    assert HW.disk_of("\\\\server\\share\\models") == (None, None)
    assert HW.ollama_models_dir({"OLLAMA_MODELS": str(tmp_path)}) == tmp_path
    assert HW.on_this_pc("http://localhost:11434") and HW.on_this_pc("http://127.0.0.1:1")
    assert not HW.on_this_pc("http://192.168.1.20:11434")


# --------------------------------------------------------------------------- #
# 6. chat: model_download asks every time, low trust keeps it away
# --------------------------------------------------------------------------- #
async def test_model_download_asks_every_time_and_shows_the_size_on_its_card(tmp_path, standin, monkeypatch):
    d, *_ = _wire(tmp_path, [_box(standin.url)], monkeypatch=monkeypatch)
    platform = types.SimpleNamespace(config_writer=types.SimpleNamespace(d=d))
    tool = LT.ModelDownloadTool(platform)
    check = await tool.spend_check({"model": "llama3.2"}, None)
    assert check.ask is True and check.once_only is True and check.dont_ask_skips is False
    assert check.attended_only is True and check.irreversible is True
    assert "Llama 3.2 3B" in check.words and "2.0 GB" in check.words and "free after" in check.words
    # Even when the size could not be read, it still asks: no download without a card.
    monkeypatch.setattr(L, "preview", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    again = await tool.spend_check({"model": "qwen3:8b"}, None)
    assert again.ask is True and again.once_only is True
    # Only a name refused on its face (pure, repeated by execute) gets no card.
    assert (await tool.spend_check({"model": "x:cloud"}, None)).ask is False
    assert standin.pulls() == []


async def test_model_download_from_chat_starts_and_returns_a_download_card(tmp_path, standin, monkeypatch):
    from iron_jarvis.daemon.chat_turn import config_cards_from_result

    d, *_ = _wire(tmp_path, [_box(standin.url)], monkeypatch=monkeypatch)
    platform = types.SimpleNamespace(config_writer=types.SimpleNamespace(d=d))
    result = await LT.ModelDownloadTool(platform).execute({"model": "Llama 3.2 3B"}, None)
    assert result.ok, result.error
    assert "not finished" in result.output and "Model servers › Models" in result.output
    card = result.data["model_download"]
    # Waited off the loop: the download publishes its events on this loop.
    await asyncio.to_thread(d.platform._model_pulls.wait, card["id"], 20)
    cards = config_cards_from_result("model_download", result)
    assert cards == [{"kind": "download", **{k: card[k] for k in (
        "id", "node_id", "model", "name", "server", "source", "size_words", "free_after_words", "bytes_total")}}]
    assert cards[0]["size_words"] == "2.0 GB" and cards[0]["server"] == "Ollama box"
    # Another tool's data never makes a download card.
    assert config_cards_from_result("model_library", result) == []


async def test_model_library_from_chat_says_what_fits_and_never_searches(tmp_path, standin, monkeypatch):
    d, *_ = _wire(tmp_path, [_box(standin.url)], monkeypatch=monkeypatch)
    platform = types.SimpleNamespace(config_writer=types.SimpleNamespace(d=d))
    result = await LT.ModelLibraryTool(platform).execute({}, None)
    assert result.ok and "This PC has 64 GB of memory" in result.output
    assert "Qwen3 8B (qwen3:8b)" in result.output and "Fits this PC (64 GB)" in result.output
    assert result.data["door"] == "/fleet?view=models"
    assert not [r for r in standin.requests if r["path"] == "/api/models"]


def test_the_download_is_kept_from_a_low_run_at_the_real_engine():
    from iron_jarvis.core.models import PermissionMode
    from iron_jarvis.core.trust import LOW_TRUST_DENY, low_trust_overrides
    from iron_jarvis.tools.permissions import PermissionEngine
    from iron_jarvis.core.config import default_permissions

    assert "model_download" in LOW_TRUST_DENY and "model_library" not in LOW_TRUST_DENY
    perms = default_permissions()
    assert perms["model_download"] == "ask" and perms["model_library"] == "allow"
    engine = PermissionEngine({"model_download": "allow"})
    base = {"model_download": "allow"}
    assert engine.authorize("model_download", {"model": "qwen3:8b"}, base).allowed is True
    low = low_trust_overrides(base)
    decision = engine.authorize("model_download", {"model": "qwen3:8b"}, low, session_allow={"model_download"})
    assert decision.allowed is False and decision.mode is PermissionMode.DENY


def test_the_real_registry_has_both_tools_and_both_lanes_arm_them(tmp_path):
    from iron_jarvis.daemon.app import create_app
    from iron_jarvis.daemon.chat_turn import post_record_tools, settings_ask_tools

    app = create_app(str(tmp_path))
    d = types.SimpleNamespace(platform=app.state.platform)
    reg = d.platform.registry
    assert reg.get("model_download") is not None and reg.get("model_library") is not None
    text = "download the newest coding model that fits my PC"
    stream = settings_ask_tools(d, text, [], [])
    assert {"model_library", "model_download"} <= set(stream)
    post = post_record_tools(d, text, [])
    assert "model_library" in post and "model_download" not in post, "the POST lane cannot show a card"


@pytest.mark.parametrize(
    "text, want",
    [
        ("download the newest coding model that fits", ["model_library", "model_download"]),
        ("pull qwen3:8b into ollama", ["model_library", "model_download"]),
        ("can you download llama 3.2 for me?", ["model_library", "model_download"]),
        ("which local models fit my PC?", ["model_library"]),
        ("how do I download a model?", ["model_library"]),
        ("download the PDF from that email", []),
        ("which model am I using right now", []),
        ("", []),
    ],
)
def test_the_words_arm_the_tools(text, want):
    assert LT.library_tools_for(text) == want


# --------------------------------------------------------------------------- #
# 7. the doors and the health check
# --------------------------------------------------------------------------- #
def test_the_page_door_says_the_library_downloads_now():
    from iron_jarvis.daemon import page_doors as PD

    step = next(s for s in PD.STEPS if s.id == "local_model")
    assert PD.page_step("download a local model") == step
    assert step.door() == {"href": "/fleet?view=models", "label": "Open the model library"}
    assert "ollama pull" not in step.line and "model_download" in step.line and "model_library" in step.line
    assert "less than a tenth" in step.line and "cannot download for LM Studio" in step.line


def test_the_doctor_reads_graphics_memory_as_the_library_does(monkeypatch):
    import importlib

    # ``onboarding/__init__`` rebinds the name ``doctor`` to the function.
    doctor = importlib.import_module("iron_jarvis.onboarding.doctor")
    assert doctor.check_graphics_memory in doctor.CHECKS
    monkeypatch.setattr(HW, "nvidia_smi_path", lambda: None)
    none = doctor.check_graphics_memory()
    assert none["ok"] is True and "judges which models fit by the PC's memory" in none["detail"]
    monkeypatch.setattr(HW, "nvidia_smi_path", lambda: "C:/nv/nvidia-smi.exe")
    monkeypatch.setattr(HW, "gpus", lambda **k: None)
    broken = doctor.check_graphics_memory()
    assert broken["ok"] is False and broken["fix"]
    # nvidia-smi's answer for a 24 GB card (MiB): read as the card's box says it.
    monkeypatch.setattr(HW, "gpus", lambda **k: (HW.Gpu("RTX", 24564 * 1024 * 1024, 1),))
    assert doctor.check_graphics_memory()["detail"] == "Graphics memory is read for the model library: RTX (24 GB)."


# --------------------------------------------------------------------------- #
# 8. the layer that would have refused: the real stream lane, under Don't ask
# --------------------------------------------------------------------------- #
async def test_under_dont_ask_the_stream_lane_still_cards_a_download_and_deny_starts_nothing(
    tmp_path, standin, monkeypatch
):
    import iron_jarvis.daemon.routes.chat  # noqa: F401 — the lane under test
    from iron_jarvis.daemon.app import create_app
    from tests.test_chat_approvals_v1187 import _asgi_post, _drive_stream
    from tests.test_settings_chat_tools_v1321 import _calls_stream

    app = create_app(str(tmp_path))
    p = app.state.platform
    stream = _calls_stream([("model_download", {"model": "llama3.2"})])
    p.router.stream = stream
    p.config_writer.d.fleet.add(_box(standin.url))
    monkeypatch.setattr(L, "hardware_for", lambda target, force=False: HW64 if target.this_pc else HW.NOT_KNOWN)
    seen: list[dict] = []

    async def decide(data):
        seen.append(dict(data))
        status, _ = await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "deny"})
        assert status == 200

    body = {"messages": [{"role": "user", "content": "download llama3.2 into ollama"}], "auto_tools": True,
            "approval_mode": "yolo"}
    await _drive_stream(app, body, decide)
    assert "model_download" in stream.tools[0], "the words did not arm the download"
    assert [c.get("tool") for c in seen] == ["model_download"], "Don't ask skipped the download's card"
    card = seen[0]
    assert card.get("once_only") is True, card
    assert "2.0 GB" in str(card.get("why") or ""), card
    assert standin.pulls() == [], "a denied card still started the download"


def test_ollama_on_this_pc_is_never_a_target_while_the_suite_keeps_the_real_one_away(tmp_path, monkeypatch):
    from iron_jarvis.providers import cli_detect, ollama_seen

    d = types.SimpleNamespace(fleet=_Registry([]), fleet_sampler=None)
    monkeypatch.setattr(ollama_seen, "state", lambda *a, **k: ollama_seen.OllamaState(True, True, True))
    assert cli_detect._OLLAMA_PROBE_FORCED_OFF is True
    assert L.targets(d) == []
    monkeypatch.setattr(cli_detect, "_OLLAMA_PROBE_FORCED_OFF", False)
    (here,) = L.targets(d)
    assert here.id == L.THIS_PC_OLLAMA and here.this_pc and here.seen_only and here.downloads


# --------------------------------------------------------------------------- #
# 9. the words are true on the owner's own PC
# --------------------------------------------------------------------------- #
def test_memory_and_drive_room_read_as_windows_shows_them():
    """A PC sold as 64 GB reads "64 GB" (psutil gives a little under 64 GiB),
    never "68 GB"; a download's size stays as its registry prints it."""
    assert L.fit_words(L.FITS, HW.Hardware(total_ram=int(64 * GIB * 0.995))) == "Fits this PC (64 GB)"
    for sold in (16, 32, 64, 128):
        # What psutil reads: the memory less what the hardware keeps back.
        pc = HW.Hardware(total_ram=sold * GIB - 300 * 1024 * 1024)
        assert L.fit_words(L.FITS, pc) == f"Fits this PC ({sold} GB)", sold
    # This PC's own reading (31.1 GB usable, as Windows Settings says it).
    assert L.fmt_room(33_410_154_496) == "31 GB"
    here = L.Target("box", "Ollama box", "ollama", "http://127.0.0.1:1", True)
    card = HW.Hardware(total_ram=int(64 * GIB * 0.995), gpus=(HW.Gpu("RTX", 24564 * 1024 * 1024, 1),),
                       model_disk_free=412 * GIB, model_disk_total=1000 * GIB)
    assert L.summary_words(here, card, []).startswith(
        "This PC has 64 GB of memory, a graphics card with 24 GB and 412 GB free on the drive Ollama uses."
    )
    # The memory a model needs is in the PC's own units, so the two compare.
    row = L.entry_view(L.entry_for("qwen3:8b"), card, [], downloads=True)
    assert row["memory_words"] == f"about {L.fmt_room(row['memory_bytes'])}"
    # A download's size: as Ollama's registry and "ollama list" print it.
    assert L.fmt_bytes(LLAMA_MEASURED) == "2.0 GB" and L.fmt_room(LLAMA_MEASURED) == "1.9 GB"
    low = HW.Hardware(total_ram=1, model_disk_free=12 * GIB, model_disk_total=100 * GIB)
    # The size as the registry prints it, the drive as Windows shows it, and
    # no figure computed from both (12 - 3.0 never reads as 9.2).
    assert L.floor_problem("x", 3 * GB, low) == (
        "Not downloaded: x needs 3.0 GB and the drive Ollama uses has 12 GB free; a tenth of that drive "
        "(10 GB) has to stay free. Make room on that drive first."
    )


def test_a_finished_download_says_the_model_menu_only_where_the_menu_lists_it(tmp_path, standin, monkeypatch):
    d, app, seen, _done = _wire(tmp_path, [_box(standin.url)], monkeypatch=monkeypatch)
    mgr = d.platform._model_pulls
    # A saved model server that is not routable never reaches the model menu.
    assert [t.in_menu for t in L.targets(d)] == [False]
    with TestClient(app) as c:
        row = c.post("/fleet/nodes/box/models/download", json={"model": "llama3.2"}).json()["download"]
        mgr.wait(row["id"], timeout=20)
        listed = c.get("/fleet/downloads").json()["downloads"]
    assert row["server"] == "Ollama box" and row["in_menu"] is False
    (ended,) = [p for t, p in seen if t == EventType.MODEL_DOWNLOAD_ENDED]
    assert ended["state"] == P.DONE and ended["server"] == "Ollama box" and ended["in_menu"] is False
    assert listed[0]["state"] == P.DONE and listed[0]["in_menu"] is False
    # Once that server is routable, its row says so (read at the time it is listed).
    d.fleet._nodes["box"] = _box(standin.url, routable=True)
    assert [t.in_menu for t in L.targets(d)] == [True]
    assert mgr.list()[0]["in_menu"] is True
    # Ollama on this PC that is not a saved server is NOT in the model menu:
    # it is listed only when no Ollama address is saved, and with none saved
    # the menu lists no Ollama model. Its row says the step instead.
    monkeypatch.setattr(L, "_ollama_seen_here", lambda: True)
    monkeypatch.setattr(L, "_this_pc_ollama_address", lambda: standin.url)
    (t,) = L.targets(types.SimpleNamespace(fleet=_Registry([]), fleet_sampler=None))
    assert t.id == L.THIS_PC_OLLAMA and t.in_menu is False and t.menu_step == L.CONNECT_OLLAMA_STEP


async def test_with_no_ollama_address_saved_a_download_into_ollama_here_never_claims_the_model_menu(
    tmp_path, standin, monkeypatch
):
    """The first-run door's case: Ollama installed on this PC, its address
    not saved yet. The model menu lists no Ollama model then
    (``build_selectable_models`` adds one only ``if cfg.ollama_base_url``), so
    the row, the ended event, the list and chat's line say where the model is
    and the step that puts it in the menu, never "in the model menu"."""
    from iron_jarvis.daemon.routes.connections import build_selectable_models

    monkeypatch.setattr(L, "_ollama_seen_here", lambda: True)
    monkeypatch.setattr(L, "_this_pc_ollama_address", lambda: standin.url)
    d, app, seen, _done = _wire(tmp_path, [], monkeypatch=monkeypatch)
    assert not d.platform.config.ollama_base_url
    menu = await asyncio.to_thread(
        build_selectable_models, d, providers=types.SimpleNamespace(available=lambda p: False)
    )
    # No Ollama row anyone can pick (a detected one is listed greyed).
    assert not [m for m in menu if m.get("provider") == "ollama" and m.get("available") is not False], menu
    (t,) = L.targets(d)
    assert (t.id, t.in_menu, t.menu_step) == (L.THIS_PC_OLLAMA, False, L.CONNECT_OLLAMA_STEP)
    mgr = d.platform._model_pulls
    with TestClient(app) as c:
        row = c.post(f"/fleet/nodes/{L.THIS_PC_OLLAMA}/models/download", json={"model": "llama3.2"}).json()["download"]
        mgr.wait(row["id"], timeout=20)
        listed = c.get("/fleet/downloads").json()["downloads"]
    (ended,) = [p for t_, p in seen if t_ == EventType.MODEL_DOWNLOAD_ENDED]
    for said in (row, ended, listed[0]):
        assert said["server"] == "Ollama on this PC" and said["in_menu"] is False, said
        assert said["menu_step"] == L.CONNECT_OLLAMA_STEP, said
    assert ended["state"] == P.DONE
    platform = types.SimpleNamespace(config_writer=types.SimpleNamespace(d=d))
    chat = await LT.ModelDownloadTool(platform).execute({"model": "llama3.2"}, None)
    assert chat.ok, chat.error
    assert f"Once it is done it is on Ollama on this PC. {L.CONNECT_OLLAMA_STEP}" in chat.output, chat.output
    assert "Once it is done it is in the model menu" not in chat.output
    # Waited off the loop: the download publishes its events on this loop.
    await asyncio.to_thread(mgr.wait, chat.data["model_download"]["id"], 20)

    # Saved at this PC's address, the menu lists Ollama's models: then it is.
    d.platform.config.ollama_base_url = "http://localhost:11434"
    (t,) = L.targets(d)
    assert (t.in_menu, t.menu_step) == (True, "")
    assert (mgr.list()[0]["in_menu"], mgr.list()[0]["menu_step"]) == (True, "")
    # Saved at another computer's address: the menu's Ollama is that one.
    d.platform.config.ollama_base_url = "http://192.168.1.50:11434"
    (t,) = L.targets(d)
    assert (t.in_menu, t.menu_step) == (False, L.CONNECT_OLLAMA_STEP)


async def test_model_download_from_chat_says_where_the_model_lands(tmp_path, standin, monkeypatch):
    d, *_ = _wire(tmp_path, [_box(standin.url)], monkeypatch=monkeypatch)
    platform = types.SimpleNamespace(config_writer=types.SimpleNamespace(d=d))
    first = await LT.ModelDownloadTool(platform).execute({"model": "llama3.2"}, None)
    assert first.ok, first.error
    assert "Once it is done it is on Ollama box." in first.output and "model menu" not in first.output
    # Waited off the loop: the download publishes its events on this loop.
    end = await asyncio.to_thread(d.platform._model_pulls.wait, first.data["model_download"]["id"], 20)
    assert end["state"] == P.DONE
    d.fleet._nodes["box"] = _box(standin.url, routable=True)
    again = await LT.ModelDownloadTool(platform).execute({"model": "llama3.2"}, None)
    assert again.ok, again.error
    assert "Once it is done it is in the model menu." in again.output
    await asyncio.to_thread(d.platform._model_pulls.wait, again.data["model_download"]["id"], 20)


async def test_model_library_from_chat_says_how_far_a_running_download_has_got(tmp_path, standin, monkeypatch):
    standin.mode = "hang"
    d, app, *_ = _wire(tmp_path, [_box(standin.url)], monkeypatch=monkeypatch)
    platform = types.SimpleNamespace(config_writer=types.SimpleNamespace(d=d))
    with TestClient(app) as c:
        row = c.post("/fleet/nodes/box/models/download", json={"model": "llama3.2"}).json()["download"]
        assert standin.started.wait(10)
        result = await LT.ModelLibraryTool(platform).execute({}, None)
        c.post(f"/fleet/downloads/{row['id']}/cancel")
    assert result.ok, result.error
    line = next(x for x in result.output.splitlines() if x.startswith("Downloading now"))
    assert "Llama 3.2 3B (llama3.2) into Ollama box" in line and "% so far" in line, line
    assert "Cancel is on its card and on Model servers › Models" in line


def test_the_real_after_download_step_tells_the_model_menu_and_reads_the_drive_again(tmp_path, standin, monkeypatch):
    """Through ``pulls.manager_for`` (no step injected): what makes "the model
    appears in the model menu" true. The menu's list of that server is
    forgotten (``fleet-<id>``, or ``ollama`` for Ollama on this PC), the drive
    is read again, and the servers are checked again on their own thread."""
    from iron_jarvis.providers import discovery

    cleared: list[str] = []
    monkeypatch.setattr(discovery, "clear", lambda provider="": cleared.append(provider))
    forgot: list[bool] = []
    real_forget = HW.forget
    monkeypatch.setattr(HW, "forget", lambda: (forgot.append(True), real_forget())[-1])
    monkeypatch.setattr(L, "hardware_for", lambda target, force=False: HW64 if target.this_pc else HW.NOT_KNOWN)

    class Sampler:
        def __init__(self) -> None:
            self.checked = threading.Event()
            self.force: Any = None

        def sample_once(self, force: bool = False) -> None:
            self.force = force
            self.checked.set()

        def snapshots(self) -> list:
            return []

    def daemon(name: str, nodes: list[FleetNode]) -> Any:
        engine = make_engine(tmp_path / f"{name}.db")
        platform = types.SimpleNamespace(engine=engine, event_bus=None,
                                         config=Config(home=tmp_path, project_root=tmp_path))
        return types.SimpleNamespace(platform=platform, fleet=_Registry(nodes), fleet_sampler=Sampler())

    saved = daemon("saved", [_box(standin.url, routable=True)])
    started = P.start_download(saved, "box", "llama3.2")
    mgr = P.manager_for(saved)
    assert mgr is saved.platform._model_pulls and mgr.on_done is not None
    end = mgr.wait(started["download"]["id"], timeout=20)
    assert end["state"] == P.DONE and end["in_menu"] is True and end["server"] == "Ollama box"
    assert "fleet-box" in cleared, cleared
    assert forgot, "this PC's drive was not read again"
    assert saved.fleet_sampler.checked.wait(10) and saved.fleet_sampler.force is True

    cleared.clear()
    monkeypatch.setattr(L, "_ollama_seen_here", lambda: True)
    monkeypatch.setattr(L, "_this_pc_ollama_address", lambda: standin.url)
    here = daemon("here", [])
    started = P.start_download(here, L.THIS_PC_OLLAMA, "llama3.2")
    end = P.manager_for(here).wait(started["download"]["id"], timeout=20)
    # No Ollama address saved here: on Ollama on this PC, not in the model menu.
    assert end["state"] == P.DONE and end["server"] == "Ollama on this PC" and end["in_menu"] is False
    assert end["menu_step"] == L.CONNECT_OLLAMA_STEP
    assert "ollama" in cleared and "fleet-box" not in cleared, cleared


def test_the_after_download_step_keeps_ollama_on_this_pc_on_the_first_read_after(monkeypatch):
    """The page re-reads the library ONCE, on the ended event. The step asks
    Ollama again instead of dropping its answer: a dropped answer reads "not
    seen" until a background probe lands, and that one re-read lost Ollama on
    this PC from the servers ("No model server yet")."""
    from iron_jarvis.providers import cli_detect, ollama_seen

    monkeypatch.setattr(cli_detect, "_OLLAMA_PROBE_FORCED_OFF", False)
    monkeypatch.setattr(ollama_seen, "_answer", None)
    monkeypatch.setattr(ollama_seen, "_refreshing", False)
    monkeypatch.setattr(HW, "forget", lambda: None)
    asked: list[Any] = []

    def detect(configured_url=None):
        asked.append(configured_url)
        return [cli_detect.DetectedModel(provider="ollama", model="llama3.2:latest", name="llama3.2",
                                         available=True, exec_path="C:/Ollama/ollama.exe")]

    monkeypatch.setattr(cli_detect, "detect_ollama", detect)
    ollama_seen.probe_now(None)
    d = types.SimpleNamespace(fleet=_Registry([]), fleet_sampler=None)
    assert [t.id for t in L.targets(d)] == [L.THIS_PC_OLLAMA]
    P._after_download(d)(L.THIS_PC_OLLAMA, "llama3.2")
    assert len(asked) == 2, "Ollama on this PC was not asked again"
    assert [t.id for t in L.targets(d)] == [L.THIS_PC_OLLAMA], "the first read after lost Ollama on this PC"
    assert L.library_view(d)["server"]["id"] == L.THIS_PC_OLLAMA


def test_download_progress_stays_off_the_activity_page(tmp_path, standin, monkeypatch):
    """Every event is stored; the Activity page (the audit timeline) shows a
    download ONCE, as its end, never its up to 100 progress steps."""
    from sqlmodel import select

    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.models import EventRecord
    from iron_jarvis.eval.observability import AuditTimeline

    from iron_jarvis.core.db import init_db

    d, app, *_ = _wire(tmp_path, [_box(standin.url)], monkeypatch=monkeypatch, persist=True)
    init_db(d.platform.engine)
    with TestClient(app) as c:
        row = c.post("/fleet/nodes/box/models/download", json={"model": "llama3.2"}).json()["download"]
        d.platform._model_pulls.wait(row["id"], timeout=20)
    with session_scope(d.platform.engine) as db:
        stored = [r.type for r in db.exec(select(EventRecord))]
    assert stored.count("model.download.progress") >= 3 and stored.count("model.download.ended") == 1, stored
    timeline = AuditTimeline(d.platform.engine)
    for kind in (None, "action"):
        shown = [e["type"] for e in timeline.query(kind=kind)["entries"]]
        assert shown == ["model.download.ended"], (kind, shown)
    assert timeline.query(type="model.download.progress")["entries"] == []


def test_with_no_model_server_the_list_still_shows_what_fits_this_pc(tmp_path, monkeypatch):
    d = types.SimpleNamespace(fleet=_Registry([]), fleet_sampler=None)
    monkeypatch.setattr(L, "hardware_for", lambda target, force=False: HW64 if target.this_pc else HW.NOT_KNOWN)
    view = L.library_view(d)
    assert view["servers"] == [] and view["server"] is None
    assert view["summary"].startswith(L.NO_SERVER_WORDS + " This PC has 64 GB of memory.")
    assert len(view["models"]) == len(L.catalog()) and not any(r["can_download"] for r in view["models"])
    assert {r["fit"] for r in view["models"]} >= {L.FITS, L.TOO_BIG}
