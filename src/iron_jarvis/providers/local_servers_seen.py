"""Are LM Studio or llama.cpp answering on this PC? (v1.358.0, lane 8D)

The ``providers/ollama_seen`` shape for the two other model servers people
run on their own PC. Before this, a running LM Studio was never detected: the
user had to know to open the "Paste an API key" door, pick "Model servers on
your network" and press its chip (v1.355.0, G72). Now the same surfaces that
offer a detected Ollama in one press (``GET /onboarding``'s ``usable`` rows,
the Connections page's Accounts card, and — once ``/health`` carries
:func:`health_field` — the chat's connect doors) offer these too.

THE PROBE: a read-only ``GET <server>/models`` on each server's DOCUMENTED
default address (LM Studio's local server listens on 1234, llama.cpp's
``llama-server`` on 8080; both answer the OpenAI-compatible model list under
``/v1``), :data:`PROBE_TIMEOUT_S` each, on this PC only (``localhost``), no
redirects, no proxy. A server counts as SEEN only when it answers that list
with at least one model a chat can use (embedding models are not counted):
port 8080 is a common port for other programs, so a llama.cpp is named only
when its list says so (llama-server writes ``"owned_by": "llamacpp"`` on
every row) — a different server there is never called llama.cpp.

:func:`seen` NEVER blocks: it returns the last answer and, when that is older
than :data:`TTL_S`, starts ONE background refresh (a closed localhost port can
take the whole timeout to refuse on Windows). :func:`probe_now` blocks (≤ the
probe timeouts): it is for the press, which runs on the threadpool and needs a
fresh answer before it saves anything. Nothing is ever saved from here.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class LocalServer:
    """One model server people run on their own PC, at its documented default."""

    key: str
    #: Its own name ("LM Studio").
    name: str
    #: The press's words ("LM Studio (on this PC)").
    label: str
    #: Where it answers by default (OpenAI-compatible, ``/v1``).
    base_url: str
    #: When set, a row of its model list must carry this ``owned_by`` before
    #: the server is called by this name (a shared port).
    owned_by: str = ""


#: The two servers (lib/localServerPresets.ts names the same addresses for the
#: endpoint forms' chips; a test holds the two equal).
SERVERS: tuple[LocalServer, ...] = (
    LocalServer("lmstudio", "LM Studio", "LM Studio (on this PC)", "http://localhost:1234/v1"),
    LocalServer(
        "llamacpp", "llama.cpp", "llama.cpp (on this PC)", "http://localhost:8080/v1", owned_by="llamacpp"
    ),
)
KEYS: tuple[str, ...] = tuple(s.key for s in SERVERS)

#: How long one answer is served before a background refresh is started.
TTL_S = 20.0
#: The longest one server's probe waits.
PROBE_TIMEOUT_S = 1.5

#: Tests (and a box that must never be probed) turn the probe off: every
#: server then reads "not answering", exactly what a PC without them sees.
PROBE_FORCED_OFF = os.environ.get("IRONJARVIS_LOCAL_SERVER_PROBE", "").strip().lower() in (
    "0",
    "off",
    "false",
    "no",
)


@dataclass(frozen=True)
class Seen:
    """What one probe saw: the chat models the server lists (in its order)."""

    server: LocalServer
    models: tuple[str, ...] = field(default_factory=tuple)

    @property
    def answering(self) -> bool:
        return bool(self.models)


_lock = threading.Lock()
#: (monotonic time of the answer, key -> models) — None until first asked.
_answer: tuple[float, dict[str, tuple[str, ...]]] | None = None
_refreshing = False


def server_for(key: str) -> LocalServer | None:
    return next((s for s in SERVERS if s.key == key), None)


def _chat_models(body: Any, owned_by: str = "") -> tuple[str, ...]:
    """The chat model ids of an OpenAI-compatible model list (embedding /
    reranker names dropped). With ``owned_by``, nothing unless a row says it."""
    rows = body.get("data") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        return ()
    if owned_by and not any(
        isinstance(r, dict) and str(r.get("owned_by") or "").strip().lower() == owned_by
        for r in rows
    ):
        return ()
    try:
        from ..fleet.picker import looks_like_embedding
    except Exception:  # noqa: BLE001
        looks_like_embedding = lambda *_a: False  # noqa: E731
    out: list[str] = []
    for r in rows:
        mid = str(r.get("id") or "").strip() if isinstance(r, dict) else ""
        if mid and mid not in out and not looks_like_embedding(mid):
            out.append(mid)
    return tuple(out)


def probe_one(server: LocalServer, *, timeout_s: float | None = None) -> tuple[str, ...]:
    """The chat models ``server`` lists right now (``()`` when it does not
    answer that way). One read-only GET, bounded by ``timeout_s`` — else
    :data:`PROBE_TIMEOUT_S` read when it RUNS (a default bound at import
    would ignore the module's own knob, which :func:`probe_now`'s join
    honours); never raises."""
    if PROBE_FORCED_OFF:
        return ()
    if timeout_s is None:
        timeout_s = PROBE_TIMEOUT_S
    try:
        from ..core import http as _http

        with _http.client(timeout=timeout_s, follow_redirects=False, trust_env=False) as c:
            r = c.get(f"{server.base_url.rstrip('/')}/models")
            if r.status_code != 200:
                return ()
            return _chat_models(r.json(), server.owned_by)
    except Exception:  # noqa: BLE001 — not running is the normal case
        return ()


def record(found: dict[str, tuple[str, ...]]) -> None:
    """Remember an answer someone just probed."""
    global _answer
    with _lock:
        _answer = (time.monotonic(), {k: tuple(v) for k, v in found.items()})


def probe_now(keys: "tuple[str, ...] | None" = None) -> dict[str, Seen]:
    """Ask now (blocking, ≤ the probe timeout per server, the servers asked
    side by side), remember and return what each answered."""
    wanted = [s for s in SERVERS if keys is None or s.key in keys]
    found: dict[str, tuple[str, ...]] = {}
    threads = []

    def _one(s: LocalServer) -> None:
        found[s.key] = probe_one(s)

    for s in wanted:
        t = threading.Thread(target=_one, args=(s,), name=f"local-server-{s.key}", daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join(PROBE_TIMEOUT_S + 2.0)
    with _lock:
        merged = dict(_answer[1]) if _answer is not None else {}
    merged.update({k: found.get(k, ()) for k in (s.key for s in wanted)})
    record(merged)
    return {s.key: Seen(s, merged.get(s.key, ())) for s in wanted}


def seen(*, spawn: Callable[[Callable[[], None]], None] | None = None) -> dict[str, Seen]:
    """The last answer for every server (all "not answering" before the first
    lands), without blocking. A stale or missing answer starts ONE background
    refresh; ``spawn`` lets a test run it inline."""
    global _refreshing
    now = time.monotonic()
    with _lock:
        answer = _answer
        stale = answer is None or now - answer[0] >= TTL_S
        start = stale and not _refreshing
        if start:
            _refreshing = True
    if start:

        def _run() -> None:
            global _refreshing
            try:
                probe_now()
            finally:
                with _lock:
                    _refreshing = False

        try:
            (spawn or _thread)(_run)
        except Exception:  # noqa: BLE001 — no thread: the next read tries again
            with _lock:
                _refreshing = False
        with _lock:
            answer = _answer
    models = answer[1] if answer is not None else {}
    return {s.key: Seen(s, tuple(models.get(s.key, ()))) for s in SERVERS}


def _thread(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, name="local-servers-seen", daemon=True).start()


def forget() -> None:
    """Drop the answer, so the next read asks again (tests)."""
    global _answer, _refreshing
    with _lock:
        _answer = None
        _refreshing = False


# --------------------------------------------------------------------------- #
# What the surfaces read.
# --------------------------------------------------------------------------- #
def _norm(url: str) -> str:
    return str(url or "").strip().rstrip("/").lower().replace("127.0.0.1", "localhost")


def slot_state(cfg, server: LocalServer) -> str:
    """Where this server stands against the ONE "your own model server" slot
    (``custom_base_url``): ``"free"`` (nothing saved — one press saves it),
    ``"saved"`` (this server is what is saved) or ``"taken"`` (another address
    is saved there; it is never replaced from here)."""
    saved = _norm(getattr(cfg, "custom_base_url", None) or "")
    if not saved:
        return "free"
    return "saved" if saved == _norm(server.base_url) else "taken"


def rows(cfg) -> list[dict[str, Any]]:
    """Every server for the Connections page: ``{key, name, label, base_url,
    running, models (count), slot}`` — from the cached answer only."""
    out: list[dict[str, Any]] = []
    for key, s in seen().items():
        out.append(
            {
                "key": key,
                "name": s.server.name,
                "label": s.server.label,
                "base_url": s.server.base_url,
                "running": s.answering,
                "models": len(s.models),
                "slot": slot_state(cfg, s.server),
            }
        )
    return out


def press_ready(cfg) -> list[LocalServer]:
    """The servers ONE press can connect and choose right now: answering on
    this PC with a chat model, and the model-server slot free (the press saves
    it) — the same rule as a detected Ollama with no address saved. Cached
    answer only; never blocks."""
    return [
        s.server
        for s in seen().values()
        if s.answering and slot_state(cfg, s.server) == "free"
    ]


def health_field(cfg) -> list[dict[str, str]]:
    """``[{key, label, base_url}]`` for ``/health``'s ``custom`` row
    (``detected_servers``): what :func:`press_ready` offers, so the chat's
    connect doors offer it as they offer a detected Ollama."""
    return [{"key": s.key, "label": s.label, "base_url": s.base_url} for s in press_ready(cfg)]
