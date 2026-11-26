"""SEARCH BY MEANING: the one state holder, the offer and the switch (next
leaps 16-a, build-out plan R2.11, idea 16 "Finds things by meaning from day
one"; the owner's note: "Offer this to the user with explanation of the added
memory cost and if they want to proceed. If not they can choose their own
models").

Before this, the model that searches by meaning (memory, File Search's "By
meaning", notes, recall) was picked ONCE at start from a config value: Ollama's
``nomic-embed-text`` when it answered, else a stand-in that matches WORDS
(``memory/embeddings.MockEmbedder``). Changing it took a terminal command and a
restart, and nothing on screen said what it would cost.

THE RULES (pinned in ``tests/test_next_leaps_16a_meaning_offer.py``):

* **The state lives here, in ``<home>/meaning-search.json``**, and the setting
  ``meaning_search`` (Settings › Memory & you) is read and written through it
  (``settings/writer``: one ledger row with Undo for every change). The value:
  ``None`` = never answered (state ``off``, or ``offered`` once the offer was
  shown), ``""`` = answered no (``declined``: Not now, or turned off), a
  choice (:func:`choice_id`) = ``on`` with that search model.
* **It never moves to ``on`` except by the owner's Yes** (the writer, from a
  press or a chat card). An install that already used Ollama's search model
  before this version keeps it ("set up before", read from the live model, never
  written here) until the owner ANSWERS (:func:`answered`): an offer that was
  only shown, in a session whose start-up probe Ollama missed, never ends it.
  16-a always asks: :func:`negligible` (D-16.1) is measured and shown, and
  skipping the question is 16-b's, with its own notice.
* **Only a model on this PC or the owner's own network is listed**
  (``providers/egress.private_host``): the text it searches would otherwise
  leave the network. Choosing one off this network is refused in words, and
  the embedders refuse one under a Local only chat as a second line. A
  choice must also be on one of the owner's model servers or Ollama on this
  PC (``fleet.library.targets``): an address named in a document never is.
* **The cost is MEASURED, never guessed** (:func:`measure`): disk is what the
  server reports (nothing added when it is already installed); memory is one
  probe request, then the server's own figure for the loaded model (Ollama's
  ``/api/ps``). A server that reports nothing reads "not measured" and is
  never negligible. Off the event loop, bounded (:data:`MEASURE_S`), kept per
  model. The offer names only a model that ANSWERED its probe here (a look
  goes on past one that did not), and a cost is "being measured" only while
  a look runs.
* **Not now is final** until the owner opens the setting: the offer is never
  shown again (Improvements, the card under a reply), while the plain row on
  File Search and in Settings still lets them choose.
* **Switching takes effect at once**: the platform's embedder is a
  ``SwitchableEmbedder`` and :func:`commit` swaps its base. Stored vectors name
  their model, so a row written by the old one is ranked by words until the
  re-read pass (:func:`reread_pass`, 200 rows a pass, paced by the daemon's
  meaning loop, paused before every row while a chat reply is written) has
  read it again with the new one. A row the model refuses while it still
  answers is stepped past (never one row blocking every other); a model that
  does not answer at all ends the pass until the loop's next idle look.
  Project knowledge vectors name no model:
  every change of the live model starts their walk over
  (:func:`restart_knowledge_walk`).

Everything that touches the network or the disk is BLOCKING: callers run it in
a thread (routes are plain ``def``; the loop uses ``asyncio.to_thread``).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("iron_jarvis.memory.meaning")

#: The setting's key (settings/schema).
SETTING = "meaning_search"
STATE_FILE = "meaning-search.json"

OFF, OFFERED, ON, DECLINED = "off", "offered", "on", "declined"
STATES = (OFF, OFFERED, ON, DECLINED)

#: The two kinds of server a search model is asked through.
KIND_OLLAMA = "ollama"
KIND_OPENAI = "openai"
KINDS = (KIND_OLLAMA, KIND_OPENAI)

#: One measure of one model, at most (the probe plus the server's figure).
MEASURE_S = 30.0
#: A boot probe of the chosen model (the old start-up probe's bound).
BOOT_PROBE_S = 2.0
#: The text the probe sends: fixed words, never anything of the owner's.
PROBE_TEXT = "Iron Jarvis is checking this search model."
#: How long Ollama keeps the probed model loaded (it unloads by itself after).
PROBE_KEEP_ALIVE = "1m"
#: A look for search models when a page asks, at most this often.
FIND_EVERY_S = 600.0
#: The background look while the offer is unanswered, at most once a day.
FIND_DAILY_S = 86_400.0
#: Most candidates kept.
FOUND_MAX = 30
#: Most models one look measures before it stops (each bounded by
#: :data:`MEASURE_S`): it goes on past a model that did not answer, so the
#: offer names one that did, but never walks a long list in one look.
LOOK_MEASURE_MAX = 5
#: Most models one look measures while the list ("Use another model") is
#: open (``every``): the list reads again and the next look measures the rest.
LOOK_EVERY_MAX = 8
#: A model that did not answer is measured again after this long (a busy or
#: restarting server is not "did not answer" for good).
RETRY_FAILED_S = FIND_EVERY_S
#: A look the open list asks for, at most this often (its re-reads never
#: start one look after another).
LIST_FIND_EVERY_S = 30.0
#: Rows the re-read pass reads again per pass.
REREAD_BATCH = 200
#: The most text of one row the re-read pass sends (project knowledge was
#: always clipped so; a memory row now is too).
REREAD_TEXT_MAX = 4000
#: Rows the live model refused (while it still answered) are stepped past and
#: kept here per model, at most this many, so the next pass never starts on
#: them again.
REREAD_FAILED_MAX = 1000

#: D-16.1, measured on this PC: memory while it works at most 150 MB and at
#: most 2% of this PC's memory, added disk at most 100 MB, and it unloads when
#: idle. 16-b acts on it (no question, one line in the bell); 16-a shows it.
NEGLIGIBLE_MEMORY = 150 * 1024 * 1024
NEGLIGIBLE_SHARE = 0.02
NEGLIGIBLE_DISK = 100 * 1024 * 1024

# --------------------------------------------------------------------------- #
# the words (VOCABULARY.md: "search by meaning", "search model")
# --------------------------------------------------------------------------- #
OFF_WORDS = "Search by meaning: off. It matches words for now."
NONE_FOUND = (
    "No search model was found on this PC or your network. Get one from the model library "
    "(Model servers › Models), or use a model server you run."
)
OFF_NETWORK = (
    "That search model is not on this PC or your own network, so search by meaning cannot use it: "
    "the text it searches would leave your network."
)
NOT_FOUND = "Iron Jarvis did not find a search model called {name} on this PC or your network."
NOT_A_SERVER = (
    "That search model is not on Ollama on this PC or a model server you added (Model servers), "
    "so search by meaning did not use it."
)
NOT_MEASURED = "Not measured: models like this usually need about their file size in memory"
#: The cost line of a model with no cost yet: "being measured" only while a
#: look is really running.
COST_MEASURING = "Its cost here is being measured."
COST_NOT_YET = "Its cost here is not measured yet."
#: Why a re-read pass stopped early (it carries on at the loop's next pass).
REPLY_PAUSE = "a reply is being written"
#: Why a re-read pass stopped: the model itself did not answer (the loop's
#: next idle look tries again).
NO_ANSWER = "the search model did not answer"
#: Why a probe failed, in plain words (G8: never an exception's name, an HTTP
#: code or a transport error on screen; the detail goes to the log).
WHY_NO_ANSWER = "it did not answer"
WHY_REFUSED = "it refused the request"
WHY_UNUSABLE = "its answer could not be used"
#: The last resort (the old note's terminal line, kept).
TERMINAL_LINE = "ollama pull nomic-embed-text"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def plain_why(detail: Any) -> str:
    """An embedder's or a probe's failure in plain words: :data:`WHY_REFUSED`
    (the server answered, but with an error), :data:`WHY_UNUSABLE` (an answer
    it could not read), else :data:`WHY_NO_ANSWER`. A Local only refusal is
    already plain words and is kept. The detail itself is for the log."""
    text = str(detail or "").strip()
    low = text.lower()
    if "local only" in low:
        return text.rstrip(".")
    if "request failed" in low or "did not answer" in low:
        return WHY_NO_ANSWER
    if "http " in low or "answered" in low or "returned" in low:
        return WHY_REFUSED
    if any(w in low for w in ("vector", "json", "could not read", "not numbers", "missing")):
        return WHY_UNUSABLE
    return WHY_NO_ANSWER


def _fmt(n: Any) -> str:
    try:
        from ..fleet.hardware import fmt_room

        return fmt_room(n)
    except Exception:  # noqa: BLE001
        return ""


# --------------------------------------------------------------------------- #
# a choice: "<kind>:<model> @ <address>"
# --------------------------------------------------------------------------- #
def choice_id(kind: str, model: str, where: str) -> str:
    return f"{kind}:{model} @ {where}"


def parse_choice(value: Any) -> "tuple[str, str, str] | None":
    """``(kind, model, where)`` of a choice, or None for anything else."""
    text = str(value or "").strip()
    if " @ " not in text or ":" not in text:
        return None
    head, _, where = text.rpartition(" @ ")
    kind, _, model = head.partition(":")
    kind, model, where = kind.strip(), model.strip(), where.strip()
    if kind not in KINDS or not model or not where:
        return None
    return kind, model, where


def where_words(where: str, server: str = "") -> str:
    """"on this PC" / "on <server> (your network)"."""
    try:
        from ..fleet.hardware import on_this_pc

        if on_this_pc(where):
            return "on this PC"
    except Exception:  # noqa: BLE001
        pass
    host = server
    if not host:
        try:
            from urllib.parse import urlsplit

            host = urlsplit(where if "://" in where else f"http://{where}").hostname or where
        except Exception:  # noqa: BLE001
            host = where
    return f"on {host} (your network)"


def choice_words(value: Any, server: str = "") -> str:
    """A choice as the owner reads it: "nomic-embed-text on this PC"."""
    parsed = parse_choice(value)
    if parsed is None:
        return "Off" if value == "" else ""
    _kind, model, where = parsed
    return f"{model} {where_words(where, server)}"


def _private(where: str) -> bool:
    try:
        from ..providers import egress

        return bool(egress.private_host(where))
    except Exception:  # noqa: BLE001 — cannot tell: not on this network
        return False


# --------------------------------------------------------------------------- #
# the file
# --------------------------------------------------------------------------- #
_FILE_LOCK = threading.RLock()


def _path(home: Any) -> Path:
    return Path(home) / STATE_FILE


def read(home: Any) -> dict[str, Any]:
    """What the file holds; ``{}`` when there is none or it cannot be read
    (an unreadable file is never ``on``). Never raises."""
    try:
        with _FILE_LOCK:
            raw = json.loads(_path(home).read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:  # noqa: BLE001
        log.warning("search by meaning: %s could not be read", STATE_FILE, exc_info=True)
        return {}


def _write(home: Any, data: dict[str, Any]) -> None:
    """Atomic: a temporary file beside it, then a replace."""
    path = _path(home)
    tmp = path.with_suffix(".json.tmp")
    with _FILE_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)


def update(home: Any, **fields: Any) -> dict[str, Any]:
    """Read, change *fields*, write (one lock), and keep the plan's shape
    (``state``, ``model``, ``where``, ``cost``) in step with ``value``."""
    with _FILE_LOCK:
        data = read(home)
        data.update(fields)
        data["state"] = state_of(data)
        parsed = parse_choice(data.get("value"))
        data["model"], data["where"] = (parsed[1], parsed[2]) if parsed else ("", "")
        data["cost"] = (data.get("costs") or {}).get(data.get("value") or "") if parsed else None
        data["version"] = 1
        _write(home, data)
        return data


def answered(data: dict[str, Any]) -> bool:
    """Has the owner ANSWERED (Yes with a model, Not now, or turned off)?
    Until then an install's earlier set-up (Ollama's search model found at
    start) still counts, also once the offer was shown: a session whose
    start-up probe Ollama missed shows the offer, and the next start must
    still find the model it used before. ``None`` (never answered, and what
    an Undo of the first answer puts back) is not an answer."""
    return data.get("value") is not None


def answered_or_asked(data: dict[str, Any]) -> bool:
    """Has the owner answered, or been offered once? (The card under a reply
    is drawn once, ever.)"""
    return answered(data) or bool(data.get("asked_at"))


def state_of(data: dict[str, Any]) -> str:
    value = data.get("value")
    if value:
        return ON
    if value == "":
        return DECLINED
    return OFFERED if data.get("asked_at") else OFF


# --------------------------------------------------------------------------- #
# the live embedder
# --------------------------------------------------------------------------- #
def switch_of(platform: Any) -> Any:
    """The platform's ``SwitchableEmbedder`` (under its cache), or None."""
    emb = getattr(platform, "embedder", None)
    for cand in (emb, getattr(emb, "base", None)):
        if cand is not None and callable(getattr(cand, "switch", None)):
            return cand
    return None


def _live_choice(platform: Any) -> str:
    """The choice the live base answers for ("" for the stand-in)."""
    sw = switch_of(platform)
    base = sw.current() if sw is not None else None
    model = str(getattr(base, "model", "") or "")
    where = str(getattr(base, "base_url", "") or "")
    if not base or model in ("", "mock") or not where:
        return ""
    from .embeddings import OllamaEmbedder

    kind = KIND_OLLAMA if isinstance(base, OllamaEmbedder) else KIND_OPENAI
    return choice_id(kind, model, where)


def setting_value(platform: Any) -> Any:
    """The setting as the writer reads it (``None`` | ``""`` | a choice)."""
    cfg = getattr(platform, "config", None)
    data = read(getattr(cfg, "home", "."))
    if answered(data):
        return data.get("value")
    live = _live_choice(platform)
    if live:
        # Set up before this version: Ollama's model at start (also once the
        # offer was shown; only an answer ends it).
        return live
    if str(getattr(cfg, "embedder_provider", "auto") or "auto") == "mock":
        return ""  # the owner pinned words-only in config.toml
    return None


def state(platform: Any) -> str:
    value = setting_value(platform)
    if value:
        return ON
    if value == "":
        return DECLINED
    cfg = getattr(platform, "config", None)
    return OFFERED if read(getattr(cfg, "home", ".")).get("asked_at") else OFF


def build_base(kind: str, model: str, where: str, *, headers: "dict[str, str] | None" = None,
               http: Any = None, timeout: float | None = None) -> Any:
    from .embeddings import OllamaEmbedder, OpenAICompatEmbedder

    kw: dict[str, Any] = {"http": http}
    if timeout is not None:
        kw["timeout"] = timeout
    if kind == KIND_OLLAMA:
        return OllamaEmbedder(where, model=model, **kw)
    return OpenAICompatEmbedder(where, model, headers=headers, **kw)


def boot_embedder(config: Any, engine: Any = None, *, http: Any = None, reachable: Any = None) -> Any:
    """THE embedder the platform builds once (``platform.py``): a
    ``SwitchableEmbedder`` (inside the persistent cache when ``engine`` is
    given) over the model the owner chose, or the stand-in.

    * Answered (:func:`answered`): the chosen model when it answers a short
      probe (:data:`BOOT_PROBE_S`), else the stand-in with the choice HELD
      (the meaning loop tries it again, with the server's key, and the row
      says it did not answer). A choice off this network never loads; only
      an Ollama choice is probed here (another server's key is never read at
      start, so it is held and ``reattach`` tries it).
    * Never answered (also once the offer was only shown): the earlier
      behaviour (``embeddings.build_embedder``'s probe of Ollama's model),
      except that "auto" never uses a server off this network. Nothing is
      written."""
    from .embeddings import (
        CachingEmbedder,
        MockEmbedder,
        SwitchableEmbedder,
        _build_base_embedder,
        _ollama_embed_ok,
    )

    home = getattr(config, "home", ".")
    data = read(home)
    base: Any = None
    held = ""
    if answered(data):
        parsed = parse_choice(data.get("value"))
        if parsed is not None:
            kind, model, where = parsed
            ok = False
            if _private(where) and kind == KIND_OLLAMA:
                try:
                    ok = (
                        bool(reachable(where)) if reachable is not None
                        else _ollama_embed_ok(where, model, http=http, timeout=BOOT_PROBE_S)
                    )
                except Exception:  # noqa: BLE001
                    ok = False
            if ok:
                base = build_base(kind, model, where, http=http)
            else:
                held = str(data.get("value") or "")
    else:
        provider = str(getattr(config, "embedder_provider", "auto") or "auto")
        url = getattr(config, "ollama_base_url", None)
        model = getattr(config, "embedder_model", "nomic-embed-text")
        if provider == "auto" and url and not _private(str(url)):
            url = None  # "auto" never sends text off this network
        base = _build_base_embedder(provider, url, model, http=http, reachable=reachable)
    if base is None or str(getattr(base, "model", "") or "") == "mock":
        # The stand-in writes this session's project knowledge rows, so a
        # later session's model must walk them all again.
        restart_knowledge_walk(home)
    sw = SwitchableEmbedder(base if base is not None else MockEmbedder(), held=held)
    return CachingEmbedder(sw, engine) if engine is not None else sw


def restart_knowledge_walk(home: Any) -> None:
    """Project knowledge rows hold bare vectors (no model's name), so the
    re-read pass walks them by id with one cursor per model. A cursor holds
    only while ONE model was live: once another model (or the stand-in) was
    live, a row it wrote may sort anywhere (ids are random), so every switch
    of the live model drops the cursors and the next walk starts over.
    Writes only when there is a cursor to drop. Never raises."""
    try:
        with _FILE_LOCK:
            if read(home).get("knowledge_read"):
                update(home, knowledge_read={})
    except Exception:  # noqa: BLE001
        log.warning("search by meaning: the knowledge walk could not be restarted", exc_info=True)


# --------------------------------------------------------------------------- #
# finding search models (BLOCKING)
# --------------------------------------------------------------------------- #
def _targets(d: Any) -> list[Any]:
    from ..fleet import library

    return library.targets(d)


def _models_on(d: Any, target: Any) -> tuple[list[dict[str, Any]], str]:
    from ..fleet import library

    if getattr(target, "kind", "") == "ollama":
        return library.installed_on(target, library.auth_headers(d, target))
    return list(getattr(target, "listed", ()) or ()), ""


#: Seams the tests replace (a fixed list of servers and their models).
TARGETS: Callable[[Any], list[Any]] = _targets
MODELS_ON: Callable[[Any, Any], tuple[list[dict[str, Any]], str]] = _models_on


def discover(d: Any) -> tuple[list[dict[str, Any]], list[str]]:
    """``(candidates, problems)``: every search model on a saved model server
    or Ollama on this PC, ON THIS NETWORK ONLY. A server off it is never
    listed (its address is not even asked). Each candidate: ``{choice, kind,
    model, where, server, this_pc, size_bytes}``. This PC first, Ollama
    first, then the smallest. Never raises."""
    from ..fleet import library, picker

    out: list[dict[str, Any]] = []
    problems: list[str] = []
    try:
        targets = list(TARGETS(d))
    except Exception:  # noqa: BLE001
        log.warning("search by meaning: the model servers could not be listed", exc_info=True)
        return [], ["the model servers could not be listed"]
    for t in targets:
        base = str(getattr(t, "base_url", "") or "")
        tkind = str(getattr(t, "kind", "") or "")
        if not base or not _private(base) or tkind == "anthropic":
            continue
        try:
            rows, why = MODELS_ON(d, t)
        except Exception:  # noqa: BLE001
            rows, why = [], "it could not be read"
        label = str(getattr(t, "label", "") or base)
        if why:
            problems.append(f"{label}: {why}")
        kind = KIND_OLLAMA if tkind == "ollama" else KIND_OPENAI
        for r in rows or []:
            name = library.clean_model_name((r or {}).get("id") if isinstance(r, dict) else r)
            if not name or library.is_cloud_name(name) or not picker.looks_like_embedding(name):
                continue
            cid = choice_id(kind, name, base)
            if any(c["choice"] == cid for c in out):
                continue
            size = (r or {}).get("size_bytes") if isinstance(r, dict) else None
            out.append({
                "choice": cid, "kind": kind, "model": name, "where": base, "server": label,
                "this_pc": bool(getattr(t, "this_pc", False)),
                "size_bytes": size if isinstance(size, int) and size > 0 else None,
            })
    out.sort(key=lambda c: (not c["this_pc"], c["kind"] != KIND_OLLAMA, c["size_bytes"] or 1 << 60))
    return out[:FOUND_MAX], problems


# --------------------------------------------------------------------------- #
# measuring (BLOCKING, bounded)
# --------------------------------------------------------------------------- #
def _probe(cand: dict[str, Any], headers: dict[str, str], budget_s: float) -> dict[str, Any]:
    """One probe request with :data:`PROBE_TEXT`, then (Ollama) the server's
    own loaded size for that model. ``{works, why, memory_bytes}``."""
    from ..core import http as _http
    from ..fleet.library import same_model, server_root

    root = server_root(cand["where"])
    started = time.monotonic()

    def left() -> float:
        return max(1.0, budget_s - (time.monotonic() - started))

    if cand["kind"] == KIND_OLLAMA:
        with _http.client(timeout=left(), follow_redirects=False, trust_env=False) as c:
            r = c.post(f"{root}/api/embeddings", headers=headers,
                       json={"model": cand["model"], "prompt": PROBE_TEXT, "keep_alive": PROBE_KEEP_ALIVE})
            if r.status_code != 200:
                log.info("search by meaning: %s answered %s to its probe", cand["model"], r.status_code)
                return {"works": False, "why": WHY_REFUSED, "memory_bytes": None}
            vec = (r.json() or {}).get("embedding")
            if not isinstance(vec, list) or not vec:
                return {"works": False, "why": WHY_UNUSABLE, "memory_bytes": None}
            ps = c.get(f"{root}/api/ps", headers=headers, timeout=left())
            mem = None
            if ps.status_code == 200:
                for m in (ps.json() or {}).get("models") or []:
                    name = str((m or {}).get("name") or (m or {}).get("model") or "")
                    if name and same_model(name, cand["model"]):
                        size = m.get("size")
                        mem = size if isinstance(size, int) and size > 0 else None
                        break
            return {"works": True, "why": "", "memory_bytes": mem, "dim": len(vec)}
    from .embeddings import EmbedderError, OpenAICompatEmbedder

    try:
        vec = OpenAICompatEmbedder(root, cand["model"], headers=headers, timeout=left()).embed(PROBE_TEXT)
    except EmbedderError as exc:
        log.info("search by meaning: %s did not pass its probe: %s", cand["model"], exc)
        return {"works": False, "why": plain_why(exc), "memory_bytes": None}
    return {"works": True, "why": "", "memory_bytes": None, "dim": len(vec)}


#: The probe (tests inject one: no network in the suite).
PROBE: Callable[[dict[str, Any], dict[str, str], float], dict[str, Any]] = _probe


def measure(cand: dict[str, Any], *, headers: "dict[str, str] | None" = None,
            probe: "Callable[..., dict[str, Any]] | None" = None) -> dict[str, Any]:
    """What *cand* costs on this PC: ``{works, why, memory_bytes,
    memory_measured, disk_bytes, disk_added_bytes, unloads, measured_at}``.
    A memory figure the server did not report is ``None`` with
    ``memory_measured`` False, never a guess. Never raises."""
    fn = probe or PROBE
    try:
        got = fn(cand, dict(headers or {}), MEASURE_S) or {}
    except Exception as exc:  # noqa: BLE001
        # The detail is for the log; the screen says it in plain words (G8).
        log.info("search by meaning: measuring %s failed: %s: %s", cand.get("model"), type(exc).__name__, exc)
        got = {"works": False, "why": WHY_NO_ANSWER, "memory_bytes": None}
    mem = got.get("memory_bytes")
    measured = isinstance(mem, int) and not isinstance(mem, bool) and mem > 0
    return {
        "works": bool(got.get("works")),
        "why": str(got.get("why") or "")[:200],
        # When, for :data:`RETRY_FAILED_S` (a failure is tried again later).
        "measured_s": time.time(),
        "memory_bytes": mem if measured else None,
        "memory_measured": measured,
        # Listed from what the server has installed: nothing is added to disk.
        "disk_bytes": cand.get("size_bytes"),
        "disk_added_bytes": 0,
        # Ollama unloads a model it has not used for a few minutes; another
        # server keeps what it loaded until it is told otherwise.
        "unloads": cand.get("kind") == KIND_OLLAMA,
        "measured_at": _now_iso(),
    }


def negligible(cost: "dict[str, Any] | None", total_ram: "int | None") -> bool:
    """D-16.1: small enough to turn on without asking? Only when EVERY part
    holds, measured on this PC. A figure that was not measured, or a PC whose
    memory is not known, is never negligible. Pure."""
    if not cost or not cost.get("works") or not cost.get("memory_measured"):
        return False
    mem = cost.get("memory_bytes")
    if not isinstance(mem, int) or mem <= 0 or not isinstance(total_ram, int) or total_ram <= 0:
        return False
    disk = cost.get("disk_added_bytes")
    if not isinstance(disk, int) or disk < 0:
        return False
    return (
        mem <= NEGLIGIBLE_MEMORY
        and mem <= NEGLIGIBLE_SHARE * total_ram
        and disk <= NEGLIGIBLE_DISK
        and bool(cost.get("unloads"))
    )


def cost_words(cost: "dict[str, Any] | None", cand: "dict[str, Any] | None" = None, *,
               looking: "bool | None" = None) -> str:
    """The card's cost line: "Uses about 300 MB of memory while it works.
    Already on your disk." A model with no cost yet is "being measured" only
    while a look really runs (``looking``; default: :data:`_LOOKING`), else
    "not measured yet". A model on another computer says that its memory
    figure is that computer's."""
    if not cost:
        busy = _LOOKING.is_set() if looking is None else looking
        return COST_MEASURING if busy else COST_NOT_YET
    if not cost.get("works"):
        why = str(cost.get("why") or "").rstrip(".")
        if not why or why == WHY_NO_ANSWER:
            return "It did not answer here."
        return f"It could not be used here: {why}."
    this_pc = bool((cand or {}).get("this_pc", True))
    if cost.get("memory_measured"):
        there = "" if this_pc else " on that computer"
        memory = f"Uses about {_fmt(cost.get('memory_bytes'))} of memory{there} while it works."
    else:
        size = _fmt((cand or {}).get("size_bytes") or cost.get("disk_bytes"))
        memory = f"{NOT_MEASURED}{f' (about {size})' if size else ''}."
    disk = "Already on your disk." if this_pc else "Already on that computer, so nothing is added here."
    return f"{memory} {disk}"


def _total_ram() -> "int | None":
    try:
        from ..fleet.hardware import read_memory

        total, _free = read_memory()
        return total if isinstance(total, int) and total > 0 else None
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------- #
# the look: find, then measure (BLOCKING, one at a time)
# --------------------------------------------------------------------------- #
_LOOK_LOCK = threading.Lock()
_LOOKING = threading.Event()


def _same_server(a: str, b: str) -> bool:
    """Do two addresses name one server? The same root, or both on this PC
    on the same port ("localhost" and "127.0.0.1" are one Ollama)."""
    from urllib.parse import urlsplit

    from ..fleet import library
    from ..fleet.hardware import on_this_pc

    ra, rb = library.server_root(a), library.server_root(b)
    if not ra or not rb:
        return False
    if ra == rb:
        return True
    if not (on_this_pc(ra) and on_this_pc(rb)):
        return False
    ua, ub = (urlsplit(r if "://" in r else f"http://{r}") for r in (ra, rb))
    return ua.scheme == ub.scheme and ua.port == ub.port and ua.path.rstrip("/") == ub.path.rstrip("/")


def _target_for(d: Any, where: str) -> Any:
    """The model server (``fleet.library.targets``: a saved one, or Ollama
    on this PC) whose address is *where*'s, or None. Never raises."""
    try:
        for t in TARGETS(d):
            if _same_server(str(getattr(t, "base_url", "") or ""), where):
                return t
    except Exception:  # noqa: BLE001
        pass
    return None


def _headers_for(d: Any, where: str) -> dict[str, str]:
    t = _target_for(d, where)
    if t is None:
        return {}
    try:
        from ..fleet import library

        return library.auth_headers(d, t)
    except Exception:  # noqa: BLE001
        return {}


def _candidate(d: Any, choice: str, data: "dict[str, Any] | None" = None) -> "dict[str, Any] | None":
    """*choice* as a candidate: the one the last look found, else one built
    from its model server (a model the look has not listed yet); None when
    its address is none of the owner's servers."""
    found = (data if data is not None else read(d.platform.config.home)).get("found") or []
    for c in found:
        if c.get("choice") == choice:
            return c
    parsed = parse_choice(choice)
    if parsed is None:
        return None
    kind, model, where = parsed
    t = _target_for(d, where)
    if t is None:
        return None
    return {"choice": choice, "kind": kind, "model": model, "where": where,
            "server": str(getattr(t, "label", "") or where), "this_pc": bool(getattr(t, "this_pc", False)),
            "size_bytes": None}


def _needs_measure(known: "dict[str, Any] | None") -> bool:
    """No cost yet, or a "did not answer" older than :data:`RETRY_FAILED_S`
    (a busy or restarting server is tried again, never written off for
    good). A model that answered keeps its cost."""
    if known is None or known.get("works") is None:
        return True
    if known.get("works"):
        return False
    at = known.get("measured_s")
    return not isinstance(at, (int, float)) or time.time() - float(at) >= RETRY_FAILED_S


def _look(d: Any, *, every: bool, measure_any: bool, pause: "Callable[[], bool] | None") -> dict[str, Any]:
    home = d.platform.config.home
    try:
        found, problems = discover(d)
        data = read(home)
        costs = dict(data.get("costs") or {})
        measured = 0
        most = LOOK_EVERY_MAX if every else LOOK_MEASURE_MAX
        for cand in found if measure_any else ():
            known = costs.get(cand["choice"])
            if not _needs_measure(known):
                if known.get("works") and not every:  # type: ignore[union-attr]
                    break  # a model that answered: enough to offer
                continue
            if (pause is not None and pause()) or measured >= most:
                break
            costs[cand["choice"]] = measure(cand, headers=_headers_for(d, cand["where"]))
            measured += 1
            if costs[cand["choice"]].get("works") and not every:
                break
        keep = {c["choice"] for c in found} | {str(data.get("value") or ""), str(data.get("last_choice") or "")}
        costs = {k: v for k, v in costs.items() if k in keep}
        return update(home, found=found, found_at=_now_iso(), found_problems=problems[:6], costs=costs,
                      looked_at_s=time.time())
    except Exception:  # noqa: BLE001
        log.warning("search by meaning: the look for search models failed", exc_info=True)
        return read(home)


def look(d: Any, *, every: bool = False, measure_any: bool = True,
         clock: Callable[[], float] = time.monotonic,
         pause: "Callable[[], bool] | None" = None) -> dict[str, Any]:
    """Find the search models, then measure them in order until ONE answers
    (at most :data:`LOOK_MEASURE_MAX` measures; one already measured that
    works ends it at once), so the offer names a model that answered here;
    ``every`` (the list is open): each one without a cost, at most
    :data:`LOOK_EVERY_MAX` a look; ``measure_any`` False: find only. A
    "did not answer" is measured again after :data:`RETRY_FAILED_S`.
    ``pause`` (``replying``) stops measuring while a reply is written. Saved
    in the file. One look at a time; a second caller gets the file as it
    is. Never raises."""
    home = d.platform.config.home
    if not _LOOK_LOCK.acquire(blocking=False):
        return read(home)
    _LOOKING.set()
    try:
        return _look(d, every=every, measure_any=measure_any, pause=pause)
    finally:
        _LOOKING.clear()
        _LOOK_LOCK.release()


def look_soon(d: Any, *, every: bool = False, measure_any: bool = True) -> bool:
    """:func:`look` on its own thread, paused while a reply is written; False
    when one is already running. The look is marked running BEFORE this
    returns, so the view read right after says ``looking`` (the page then
    reads again)."""
    if not _LOOK_LOCK.acquire(blocking=False):
        return False
    _LOOKING.set()

    def run() -> None:
        try:
            _look(d, every=every, measure_any=measure_any, pause=replying)
        finally:
            _LOOKING.clear()
            _LOOK_LOCK.release()

    try:
        threading.Thread(target=run, name="meaning-search-look", daemon=True).start()
    except Exception:  # noqa: BLE001 — no thread: no look, never a stuck flag
        _LOOKING.clear()
        _LOOK_LOCK.release()
        return False
    return True


def look_is_due(data: dict[str, Any], every_s: float) -> bool:
    at = data.get("looked_at_s")
    return not isinstance(at, (int, float)) or time.time() - float(at) >= every_s


def page_look(d: Any, *, every: bool = False) -> bool:
    """The look a page's read asks for (``GET /memory/meaning``; ``every``:
    the model list is open). The row's look runs at most every
    :data:`FIND_EVERY_S`; the open list's whenever a model found has no cost
    yet, else at most every :data:`LIST_FIND_EVERY_S` (its re-reads never
    start one look after another). Light on the PC (G7): with search by
    meaning on, or after Not now, the row shows no offer, so its look only
    finds; it measures only while the list is open. True when a look
    started."""
    data = read(d.platform.config.home)
    costs = data.get("costs") or {}
    if every:
        unmeasured = any(_needs_measure(costs.get(c.get("choice"))) for c in data.get("found") or [])
        if not (unmeasured or look_is_due(data, LIST_FIND_EVERY_S)):
            return False
        return look_soon(d, every=True)
    if not look_is_due(data, FIND_EVERY_S):
        return False
    return look_soon(d, measure_any=state(d.platform) in (OFF, OFFERED))


# --------------------------------------------------------------------------- #
# answering: the writer's two steps (settings/writer)
# --------------------------------------------------------------------------- #
class Prepared:
    """What :func:`prepare` checked, for :func:`commit` to apply."""

    def __init__(self, value: Any, base: Any = None) -> None:
        self.value = value
        self.base = base


def _resolve(d: Any, value: Any) -> Any:
    """Chat's and the press's words to one stored value: ``None``, ``""``
    ("off"), or a choice ("on" = the one chosen before, else the first that
    answered here, else the first not measured yet; a bare model name = the
    one found with that name). A choice must be on this network AND on one of
    the owner's model servers (or Ollama on this PC): an address read from a
    page or a document is never one (``ValueError`` with the words)."""
    if value is None:
        return None
    text = str(value).strip()
    low = text.lower()
    if low in ("", "off", "false", "no", "none", "not now"):
        return ""
    parsed = parse_choice(text)
    if parsed is not None:
        if not _private(parsed[2]):
            raise ValueError(OFF_NETWORK)
        if _target_for(d, parsed[2]) is None:
            raise ValueError(NOT_A_SERVER)
        return text
    data = read(d.platform.config.home)
    found = list(data.get("found") or [])
    if not found:
        # Asked before any look (a fresh start): look now, bounded.
        data = look(d)
        found = list(data.get("found") or [])
    if low in ("on", "true", "yes"):
        last = str(data.get("last_choice") or "")
        if parse_choice(last) is not None:
            return last
        costs = data.get("costs") or {}

        def works(c: dict[str, Any]) -> "bool | None":
            cost = costs.get(c["choice"])
            return None if cost is None else bool(cost.get("works"))

        for want in (True, None):
            for c in found:
                if works(c) is want:
                    return c["choice"]
        raise ValueError(NONE_FOUND)
    from ..fleet.library import same_model

    for c in found:
        if same_model(c["model"], text):
            return c["choice"]
    raise ValueError(NOT_FOUND.format(name=text[:80]))


def prepare(d: Any, value: Any, *, restoring: bool = False) -> Prepared:
    """Check a new value BEFORE anything changes (the writer's validate
    step). A choice must be on this network and must answer one probe; the
    base it built is kept for :func:`commit`. Raises ``ValueError`` with the
    sentence the owner reads. BLOCKING."""
    from .embeddings import EmbedderError

    clean = _resolve(d, value)
    if not clean:
        return Prepared(clean)
    kind, model, where = parse_choice(clean)  # type: ignore[misc]
    if not _private(where):
        raise ValueError(OFF_NETWORK)
    base = build_base(kind, model, where, headers=_headers_for(d, where))
    try:
        vec = base.embed(PROBE_TEXT)
        if not vec:
            raise EmbedderError("it sent no vector")
    except EmbedderError as exc:
        # The detail (a transport error, an HTTP code) is for the log; the
        # owner reads plain words (G8).
        log.info("search by meaning: %s did not pass its probe: %s", model, exc)
        why = plain_why(exc)
        if why == WHY_NO_ANSWER:
            raise ValueError(
                f"{model} did not answer {where_words(where)}, so search by meaning was not changed."
            ) from None
        raise ValueError(
            f"{model} could not be used {where_words(where)} ({why}), so search by meaning was not changed."
        ) from None
    return Prepared(clean, base)


def commit(d: Any, prepared: Prepared, *, actor: str = "") -> None:
    """Apply what :func:`prepare` checked: the file, then the live embedder,
    at once (no restart). Wakes the re-read pass."""
    from .embeddings import MockEmbedder

    platform = d.platform
    home = platform.config.home
    value = prepared.value
    data = read(home)
    fields: dict[str, Any] = {"value": value, "answered_at": _now_iso() if value is not None else None,
                              "how": "yes" if value else "",
                              # The live model changes: the knowledge walk starts over,
                              # and every row is tried again by the model chosen now.
                              "knowledge_read": {}, "reread_failed": {}}
    if value:
        fields["last_choice"] = value
    elif data.get("value"):
        fields["last_choice"] = data.get("value")
    update(home, **fields)
    sw = switch_of(platform)
    if sw is not None:
        sw.switch(prepared.base if value and prepared.base is not None else MockEmbedder())
    if value:
        REREAD_WAKE.set()
    log.info("search by meaning: %s (%s)", state_of({"value": value}), actor or "a change")


def _deps_of(platform: Any) -> Any:
    d = getattr(getattr(platform, "config_writer", None), "d", None)
    if d is not None:
        return d
    from types import SimpleNamespace

    return SimpleNamespace(platform=platform)


def card_check(platform: Any, value: Any) -> Any:
    """Chat's card for a change of ``meaning_search`` (``config_change``'s
    ``spend_check``): turning it on, or to another model, ASKS with the cost
    measured on this PC on the card ("Turn on search by meaning with
    nomic-embed-text on this PC. Uses about 300 MB of memory while it works.
    Already on your disk."); a model not measured yet is measured first
    (bounded). Turning it off runs without a card (it has an Undo). None
    when the value cannot be read (the writer then says why). BLOCKING."""
    from ..tools.base import SpendCheck

    d = _deps_of(platform)
    try:
        clean = _resolve(d, value)
    except ValueError:
        return None
    if not clean:
        return SpendCheck(ask=False, cost_note="search by meaning off")
    home = platform.config.home
    data = read(home)
    # A model the last look did not list (a choice named in full) is built
    # from its own model server; one on none of the owner's servers was
    # refused by ``_resolve`` above. Every card carries a MEASURED cost.
    cand = _candidate(d, clean, data)
    if cand is None:
        return None
    cost = (data.get("costs") or {}).get(clean)
    if cost is None:
        cost = measure(cand, headers=_headers_for(d, cand["where"]))
        update(home, costs={**(read(home).get("costs") or {}), clean: cost})
    server = str(cand.get("server") or "")
    line = cost_words(cost, cand, looking=False)
    words = f"Turn on search by meaning with {choice_words(clean, server)}. {line}"
    return SpendCheck(ask=True, words=words, cost_note=line)


def mark_offered(platform: Any) -> bool:
    """The offer was shown under a reply: ``off`` -> ``offered``. True only
    for the call that made that move, so the card is drawn once. Never
    raises."""
    try:
        if state(platform) != OFF:
            return False
        with _FILE_LOCK:
            data = read(platform.config.home)
            if answered_or_asked(data):
                return False
            update(platform.config.home, asked_at=_now_iso())
        return True
    except Exception:  # noqa: BLE001
        log.warning("search by meaning: the offer could not be marked", exc_info=True)
        return False


def reattach(d: Any) -> bool:
    """A chosen model that did not answer at start (or a server that needs
    its key, which start-up does not read): try it again and switch to it
    when it answers. True when it switched. BLOCKING; never raises."""
    sw = switch_of(d.platform)
    held = str(getattr(sw, "held", "") or "")
    if sw is None or not held or setting_value(d.platform) != held:
        return False
    try:
        prepared = prepare(d, held)
    except Exception:  # noqa: BLE001
        return False
    # The stand-in served until now: the knowledge walk starts over.
    restart_knowledge_walk(d.platform.config.home)
    sw.switch(prepared.base, held="")
    REREAD_WAKE.set()
    return True


# --------------------------------------------------------------------------- #
# what the screens and chat read
# --------------------------------------------------------------------------- #
def status_words(platform: Any, data: "dict[str, Any] | None" = None) -> str:
    value = setting_value(platform)
    parsed = parse_choice(value)
    if not parsed:
        return OFF_WORDS
    sw = switch_of(platform)
    model = parsed[1]
    if sw is not None and str(getattr(sw, "held", "") or "") == value:
        return (f"Search by meaning is on, but {model} did not answer when Iron Jarvis started, "
                "so it matches words for now.")
    return f"Search by meaning: on, with {choice_words(value)}."


def view(d: Any) -> dict[str, Any]:
    """``GET /memory/meaning``: ``{state, value, words, offer, choice,
    candidates: [{choice, model, server, where_words, this_pc, cost,
    cost_words, negligible}], looking, looked_at, problems, how,
    terminal_line}``. Reads the file only (the look runs on its own
    thread). Never raises."""
    platform = d.platform
    data = read(platform.config.home)
    st = state(platform)
    value = setting_value(platform)
    costs = data.get("costs") or {}
    ram = _total_ram()
    looking = _LOOKING.is_set()
    cands = []
    for c in data.get("found") or []:
        cost = costs.get(c.get("choice"))
        this_pc = bool(c.get("this_pc"))
        cands.append({
            "choice": c.get("choice"), "model": c.get("model"), "server": c.get("server"),
            "where_words": where_words(str(c.get("where") or ""), str(c.get("server") or "")),
            "this_pc": this_pc, "cost": cost, "cost_words": cost_words(cost, c, looking=looking),
            # D-16.1 is measured on THIS PC: another computer's memory figure
            # is never weighed against this PC's memory.
            "negligible": this_pc and negligible(cost, ram),
            "works": None if cost is None else bool(cost.get("works")),
        })
    # The offer names only a model that ANSWERED here, with its measured cost.
    best = next((c for c in cands if c["works"] is True), None)
    return {
        "state": st,
        "value": value,
        "how": "set up before" if (value and not answered(data)) else str(data.get("how") or ""),
        "words": status_words(platform, data),
        "on": bool(parse_choice(value)),
        "choice_words": choice_words(value),
        # The offer: never answered, and a model that answered its probe.
        "offer": st in (OFF, OFFERED) and best is not None,
        "best": best,
        "candidates": cands,
        "looking": looking,
        "looked_at": data.get("found_at") or "",
        "problems": list(data.get("found_problems") or [])[:6],
        "none_found": NONE_FOUND,
        "terminal_line": TERMINAL_LINE,
    }


#: Most search models chat's list and pick card name.
CHAT_CHOICES_MAX = 12
#: The pick card's way to turn it off (``_resolve`` reads "off").
CHAT_OFF = ("off", "Off: searches match words")
NOT_LOOKED_YET = (
    "Iron Jarvis has not looked for search models yet; config_change with value \"on\" looks now "
    "and asks with what it costs here."
)


def _chat_rows(platform: Any) -> "tuple[list[dict[str, Any]], dict[str, Any]]":
    data = read(platform.config.home)
    costs = data.get("costs") or {}
    rows = []
    for c in (data.get("found") or [])[:CHAT_CHOICES_MAX]:
        cost = costs.get(c.get("choice"))
        where = where_words(str(c.get("where") or ""), str(c.get("server") or ""))
        rows.append({
            "choice": str(c.get("choice") or ""),
            "words": f"{c.get('model')} {where}: {cost_words(cost, c, looking=False)}",
            "works": None if cost is None else bool(cost.get("works")),
        })
    return rows, data


def chat_choices(platform: Any) -> list[tuple[str, str]]:
    """Chat's pick card for ``meaning_search`` with no value ("switch the
    search model"): ``(value, words)`` of every search model found that can
    be chosen (never one that did not answer), each with what it costs here,
    then Off. Fewer than two: no card. Reads the file only (no look, no
    probe; the choice's own card measures it). BLOCKING (one small read);
    never raises."""
    try:
        rows, _data = _chat_rows(platform)
    except Exception:  # noqa: BLE001
        return []
    out = [(r["choice"], r["words"]) for r in rows if r["choice"] and r["works"] is not False]
    return out + [CHAT_OFF] if out else []


def chat_line(platform: Any) -> str:
    """``config_list``'s line for ``meaning_search``: every search model
    found, what each costs here, and the value that chooses it, so chat can
    name and price the options (G9). Reads the file only. BLOCKING (one
    small read); never raises; "" when it cannot be read."""
    try:
        rows, data = _chat_rows(platform)
    except Exception:  # noqa: BLE001
        return ""
    if not data.get("found_at"):
        return " " + NOT_LOOKED_YET
    if not rows:
        return " " + NONE_FOUND
    shown = "; ".join(f"{r['words']} (value: {r['choice']})" for r in rows)
    return (
        " Search models found on this PC or your network, with what each costs here (set meaning_search "
        f"to a value; \"off\" turns it off): {shown}"
    )


# --------------------------------------------------------------------------- #
# the daemon's meaning loop (the master plan's pacing: idle, a fixed number a
# pass, paused while a reply is written)
# --------------------------------------------------------------------------- #
#: First look after start (never competes with boot).
LOOP_FIRST_S = 120.0
#: Between passes while rows still wait to be read again.
LOOP_PACE_S = 30.0
#: Between looks with nothing to do (a switch wakes it sooner).
LOOP_IDLE_S = 3600.0
#: The wait is cut into steps this long, to notice a switch.
LOOP_STEP_S = 60.0


def replying() -> bool:
    """Is a chat reply being written right now? (The pass waits.)"""
    try:
        from ..core.turns import CHAT_INFLIGHT

        return CHAT_INFLIGHT.count() > 0
    except Exception:  # noqa: BLE001 — cannot tell: wait
        return True


def one_pass(d: Any) -> dict[str, Any]:
    """One look of the meaning loop. BLOCKING; raises only on a real fault.

    * a chosen model held at start (it did not answer, or needs its key):
      try it again (:func:`reattach`);
    * on: read again at most :data:`REREAD_BATCH` rows the old model wrote;
    * never answered: look for search models (and measure the first) at most
      once a day, so the offer can wait on Improvements with its cost."""
    platform = d.platform
    out: dict[str, Any] = {"reattached": reattach(d)}
    st = state(platform)
    if st == ON:
        out.update(reread_pass(platform))
    elif st in (OFF, OFFERED) and look_is_due(read(platform.config.home), FIND_DAILY_S):
        look(d, pause=replying)
        out["looked"] = True
    return out


async def meaning_loop(
    d: Any,
    tick: Callable[[bool, "BaseException | None"], None],
    *,
    sleep: Callable[[float], Any] | None = None,
    first_s: float = LOOP_FIRST_S,
    pace_s: float = LOOP_PACE_S,
    idle_s: float = LOOP_IDLE_S,
    step_s: float = LOOP_STEP_S,
) -> None:
    """Paced: the first look ``first_s`` after start; then ``pace_s`` while
    rows still wait, else up to ``idle_s`` (a switch on wakes it at the next
    ``step_s``). Paused while a chat reply is written. Off the loop
    (``asyncio.to_thread``); one fault is reported on its health row and the
    loop goes on. Never raises (but cancellation)."""
    import asyncio

    nap = sleep or asyncio.sleep
    await nap(first_s)
    while True:
        wait_s = idle_s
        # Cleared BEFORE the pass, so a switch made while it runs still wakes
        # the next one.
        REREAD_WAKE.clear()
        try:
            if replying():
                wait_s = pace_s
            else:
                got = await asyncio.to_thread(one_pass, d)
                tick(True, None)
                if got.get("more"):
                    wait_s = pace_s
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — never kills the daemon
            log.warning("search by meaning: a pass failed", exc_info=True)
            tick(False, exc)
        waited = 0.0
        while waited < wait_s and not REREAD_WAKE.is_set():
            step = min(step_s, wait_s - waited)
            await nap(step)
            waited += step


# --------------------------------------------------------------------------- #
# the re-read pass (BLOCKING; the daemon's meaning loop paces it)
# --------------------------------------------------------------------------- #
#: Set when a model is switched on, so the loop starts a pass at once.
REREAD_WAKE = threading.Event()




def _model_answers(sw: Any, model: str) -> "bool | None":
    """Does the live search model still answer? One probe with
    :data:`PROBE_TEXT` (fixed words, never the owner's), straight to the
    server, never the cache. None when the live model is no longer *model*.
    Never raises."""
    base = sw.current() if sw is not None else None
    if base is None or str(getattr(base, "model", "") or "") != model:
        return None
    try:
        return bool(base.embed(PROBE_TEXT))
    except Exception:  # noqa: BLE001 — not answering
        return False


def reread_pass(platform: Any, *, limit: int = REREAD_BATCH,
                pause: "Callable[[], bool] | None" = None) -> dict[str, Any]:
    """Read again, with the live search model, at most *limit* stored rows
    it did not write: memory rows whose vector names another model (or none),
    then project knowledge rows (bare vectors: walked once per model by a
    cursor kept in the file, dropped whenever the live model changes:
    :func:`restart_knowledge_walk`). Only while search by meaning is on with
    a model on this network (``skipped`` says why not). Paused BEFORE EVERY
    ROW while a chat reply is written (``pause``, default :func:`replying`):
    the pass stops with ``more`` and goes on at the loop's next pass.

    Each row's text is clipped to :data:`REREAD_TEXT_MAX`. A row the model
    refuses while it still answers :data:`PROBE_TEXT` is STEPPED PAST (a
    memory row is kept in the file's ``reread_failed`` for that model, so no
    later pass starts on it again; a knowledge row's cursor moves past it):
    one row never keeps every older row matched by words. A model that does
    not answer at all ends the pass (:data:`NO_ANSWER`, no ``more``: the
    loop's next idle look tries again, never a request every 30 s).
    ``{read, more}`` (+ ``skipped``, + ``failed``: rows stepped past)."""
    wait = pause if pause is not None else replying
    from sqlmodel import select

    from ..core.db import session_scope
    from ..core.models import ProjectKnowledge
    from .embeddings import EmbedderError, embed_with_model
    from .models import MemoryRecord
    from .retrieval import decode_embedding, encode_embedding

    emb = getattr(platform, "embedder", None)
    sw = switch_of(platform)
    if emb is None or sw is None or not parse_choice(setting_value(platform)) or getattr(sw, "held", ""):
        return {"skipped": "off", "read": 0, "more": False}
    where = str(getattr(sw, "where", "") or "")
    model = str(getattr(emb, "model", "") or "")
    if not where or not _private(where) or model in ("", "mock"):
        return {"skipped": "not on this network", "read": 0, "more": False}
    from sqlalchemy import not_

    home = platform.config.home
    failed_all = dict(read(home).get("reread_failed") or {})
    failed = [str(i) for i in failed_all.get(model) or []]
    skip = set(failed)
    newly: list[str] = []
    done = refused = 0

    def finish(res: dict[str, Any]) -> dict[str, Any]:
        if newly:
            failed_all[model] = (failed + newly)[-REREAD_FAILED_MAX:]
            update(home, reread_failed=failed_all)
        if refused:
            res["failed"] = refused
        return res

    def model_gone(answers: "bool | None") -> "dict[str, Any] | None":
        """The pass's end when the model changed or does not answer."""
        if answers is None:
            return {"skipped": "the search model changed", "more": True}
        if not answers:
            return {"skipped": NO_ANSWER, "more": False}
        return None

    # A row this model wrote starts with its tag (``encode_embedding``), so
    # the rows to read again are found by that prefix: ids only, newest
    # first, never every row's vector in memory; the rows this model refused
    # are left out.
    tag = '{"model": ' + json.dumps(model)
    with session_scope(platform.engine) as db:
        ids = [i for i in db.exec(
            select(MemoryRecord.id)
            .where(not_(MemoryRecord.embedding_json.startswith(tag)))  # type: ignore[union-attr]
            .where(MemoryRecord.text != "")
            .order_by(MemoryRecord.created_at.desc())
            .limit(limit + 1 + len(skip))
        ) if i not in skip]
    for rid in ids[:limit]:
        if wait():
            return finish({"skipped": REPLY_PAUSE, "read": done, "more": True})
        with session_scope(platform.engine) as db:
            row = db.get(MemoryRecord, rid)
            full = str(getattr(row, "text", "") or "")
        text = full[:REREAD_TEXT_MAX]
        if row is None or not text.strip() or decode_embedding(row.embedding_json)[1] == model:
            continue
        try:
            vec, made_by = embed_with_model(emb, text)
        except EmbedderError:
            gone = model_gone(_model_answers(sw, model))
            if gone is not None:
                return finish({"read": done, **gone})
            newly.append(rid)  # refused, while the model answers: step past it
            refused += 1
            continue
        if made_by != model:
            return finish({"skipped": "the search model changed", "read": done, "more": True})
        with session_scope(platform.engine) as db:
            live = db.get(MemoryRecord, rid)
            if live is not None and live.text == full:
                live.embedding_json = encode_embedding(vec, made_by)
                db.add(live)
                db.commit()
        done += 1
    more = len(ids) > limit
    left = limit - done - refused
    paused = False
    stop: "dict[str, Any] | None" = None
    if left > 0 and not more:
        cursors = dict(read(home).get("knowledge_read") or {})
        after = str(cursors.get(model) or "")
        with session_scope(platform.engine) as db:
            stmt = select(ProjectKnowledge).order_by(ProjectKnowledge.id)
            if after:
                stmt = stmt.where(ProjectKnowledge.id > after)
            krows = list(db.exec(stmt.limit(left + 1)))
        last = after
        for k in krows[:left]:
            if wait():
                more = paused = True
                break
            text = (k.text or "")[:REREAD_TEXT_MAX]
            if text.strip():
                try:
                    vec, made_by = embed_with_model(emb, text)
                except EmbedderError:
                    stop = model_gone(_model_answers(sw, model))
                    if stop is not None:
                        more = bool(stop["more"])
                        break
                    refused += 1  # refused, while the model answers: the cursor moves past it
                    last = k.id
                    continue
                if made_by != model:
                    more = True
                    break
                with session_scope(platform.engine) as db:
                    live = db.get(ProjectKnowledge, k.id)
                    if live is not None:
                        live.embedding_json = json.dumps([float(x) for x in vec])
                        db.add(live)
                        db.commit()
                done += 1
            last = k.id
        else:
            more = len(krows) > left
        if last != after:
            cursors[model] = last
            update(home, knowledge_read=cursors)
    if paused:
        return finish({"skipped": REPLY_PAUSE, "read": done, "more": True})
    if stop is not None:
        return finish({"read": done, **stop})
    return finish({"read": done, "more": more})
