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
  written here). 16-a always asks: :func:`negligible` (D-16.1) is measured and
  shown, and skipping the question is 16-b's, with its own notice.
* **Only a model on this PC or the owner's own network is listed**
  (``providers/egress.private_host``): the text it searches would otherwise
  leave the network. Choosing one off this network is refused in words, and
  the embedders refuse one under a Local only chat as a second line.
* **The cost is MEASURED, never guessed** (:func:`measure`): disk is what the
  server reports (nothing added when it is already installed); memory is one
  probe request, then the server's own figure for the loaded model (Ollama's
  ``/api/ps``). A server that reports nothing reads "not measured" and is
  never negligible. Off the event loop, bounded (:data:`MEASURE_S`), kept per
  model.
* **Not now is final** until the owner opens the setting: the offer is never
  shown again (Improvements, the card under a reply), while the plain row on
  File Search and in Settings still lets them choose.
* **Switching takes effect at once**: the platform's embedder is a
  ``SwitchableEmbedder`` and :func:`commit` swaps its base. Stored vectors name
  their model, so a row written by the old one is ranked by words until the
  re-read pass (:func:`reread_pass`, 200 rows a pass, paced by the daemon's
  meaning loop) has read it again with the new one.

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
#: Rows the re-read pass reads again per pass.
REREAD_BATCH = 200

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
NOT_MEASURED = "Not measured: models like this usually need about their file size in memory"
#: The last resort (the old note's terminal line, kept).
TERMINAL_LINE = "ollama pull nomic-embed-text"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


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


def answered_or_asked(data: dict[str, Any]) -> bool:
    """Has the owner answered, or been offered once? Until then an install's
    earlier set-up (Ollama's search model found at start) still counts."""
    return "value" in data or bool(data.get("asked_at"))


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
    if answered_or_asked(data):
        return data.get("value")
    live = _live_choice(platform)
    if live:
        return live  # set up before this version: Ollama's model at start
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

    * Answered or asked (the file says so): the chosen model when it answers
      a short probe (:data:`BOOT_PROBE_S`), else the stand-in with the choice
      HELD (the meaning loop tries it again, with the server's key, and the
      row says it did not answer). A choice off this network never loads.
    * Never answered: the earlier behaviour (``embeddings.build_embedder``'s
      probe of Ollama's model), except that "auto" never uses a server off
      this network. Nothing is written."""
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
    if answered_or_asked(data):
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
    sw = SwitchableEmbedder(base if base is not None else MockEmbedder(), held=held)
    return CachingEmbedder(sw, engine) if engine is not None else sw


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
                return {"works": False, "why": f"it answered {r.status_code}", "memory_bytes": None}
            vec = (r.json() or {}).get("embedding")
            if not isinstance(vec, list) or not vec:
                return {"works": False, "why": "it sent no vector", "memory_bytes": None}
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
        return {"works": False, "why": str(exc), "memory_bytes": None}
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
        got = {"works": False, "why": f"it did not answer ({type(exc).__name__})", "memory_bytes": None}
    mem = got.get("memory_bytes")
    measured = isinstance(mem, int) and not isinstance(mem, bool) and mem > 0
    return {
        "works": bool(got.get("works")),
        "why": str(got.get("why") or "")[:200],
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


def cost_words(cost: "dict[str, Any] | None", cand: "dict[str, Any] | None" = None) -> str:
    """The card's cost line: "Uses about 300 MB of memory while it works.
    Already on your disk." """
    if not cost:
        return "Its cost here is being measured."
    if not cost.get("works"):
        why = str(cost.get("why") or "").rstrip(".")
        return f"It did not answer here{': ' + why if why else ''}."
    if cost.get("memory_measured"):
        memory = f"Uses about {_fmt(cost.get('memory_bytes'))} of memory while it works."
    else:
        size = _fmt((cand or {}).get("size_bytes") or cost.get("disk_bytes"))
        memory = f"{NOT_MEASURED}{f' (about {size})' if size else ''}."
    this_pc = bool((cand or {}).get("this_pc", True))
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


def _headers_for(d: Any, where: str) -> dict[str, str]:
    try:
        from ..fleet import library

        root = library.server_root(where)
        for t in TARGETS(d):
            if library.server_root(str(getattr(t, "base_url", "") or "")) == root:
                return library.auth_headers(d, t)
    except Exception:  # noqa: BLE001
        pass
    return {}


def look(d: Any, *, every: bool = False, clock: Callable[[], float] = time.monotonic) -> dict[str, Any]:
    """Find the search models, then measure the first that has no cost yet
    (``every``: each one without a cost). Saved in the file. One look at a
    time; a second caller gets the file as it is. Never raises."""
    home = d.platform.config.home
    if not _LOOK_LOCK.acquire(blocking=False):
        return read(home)
    _LOOKING.set()
    try:
        found, problems = discover(d)
        data = read(home)
        costs = dict(data.get("costs") or {})
        for cand in found:
            if cand["choice"] in costs and costs[cand["choice"]].get("works") is not None:
                continue
            costs[cand["choice"]] = measure(cand, headers=_headers_for(d, cand["where"]))
            if not every:
                break
        keep = {c["choice"] for c in found} | {str(data.get("value") or ""), str(data.get("last_choice") or "")}
        costs = {k: v for k, v in costs.items() if k in keep}
        return update(home, found=found, found_at=_now_iso(), found_problems=problems[:6], costs=costs,
                      looked_at_s=time.time())
    except Exception:  # noqa: BLE001
        log.warning("search by meaning: the look for search models failed", exc_info=True)
        return read(home)
    finally:
        _LOOKING.clear()
        _LOOK_LOCK.release()


def look_soon(d: Any, *, every: bool = False) -> bool:
    """:func:`look` on its own thread; False when one is already running."""
    if _LOOKING.is_set():
        return False
    threading.Thread(target=look, args=(d,), kwargs={"every": every},
                     name="meaning-search-look", daemon=True).start()
    return True


def look_is_due(data: dict[str, Any], every_s: float) -> bool:
    at = data.get("looked_at_s")
    return not isinstance(at, (int, float)) or time.time() - float(at) >= every_s


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
    works; a bare model name = the one found with that name)."""
    if value is None:
        return None
    text = str(value).strip()
    low = text.lower()
    if low in ("", "off", "false", "no", "none", "not now"):
        return ""
    if parse_choice(text) is not None:
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
        for c in found:
            if (costs.get(c["choice"]) or {}).get("works") is not False:
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
        raise ValueError(
            f"{model} did not answer {where_words(where)} ({str(exc).rstrip('.')}), so search by meaning "
            "was not changed."
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
                              "how": "yes" if value else ""}
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
    cand = next((c for c in data.get("found") or [] if c.get("choice") == clean), None)
    cost = (data.get("costs") or {}).get(clean)
    if cost is None and cand is not None:
        cost = measure(cand, headers=_headers_for(d, cand["where"]))
        update(home, costs={**(read(home).get("costs") or {}), clean: cost})
    server = str((cand or {}).get("server") or "")
    words = f"Turn on search by meaning with {choice_words(clean, server)}. {cost_words(cost, cand)}"
    return SpendCheck(ask=True, words=words, cost_note=cost_words(cost, cand))


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
    cands = []
    for c in data.get("found") or []:
        cost = costs.get(c.get("choice"))
        cands.append({
            "choice": c.get("choice"), "model": c.get("model"), "server": c.get("server"),
            "where_words": where_words(str(c.get("where") or ""), str(c.get("server") or "")),
            "this_pc": bool(c.get("this_pc")), "cost": cost, "cost_words": cost_words(cost, c),
            "negligible": negligible(cost, ram), "works": None if cost is None else bool(cost.get("works")),
        })
    best = next((c for c in cands if c["works"] is not False), None)
    return {
        "state": st,
        "value": value,
        "how": "set up before" if (value and not answered_or_asked(data)) else str(data.get("how") or ""),
        "words": status_words(platform, data),
        "on": bool(parse_choice(value)),
        "choice_words": choice_words(value),
        # The offer: never answered, and a model to offer.
        "offer": st in (OFF, OFFERED) and best is not None,
        "best": best,
        "candidates": cands,
        "looking": _LOOKING.is_set(),
        "looked_at": data.get("found_at") or "",
        "problems": list(data.get("found_problems") or [])[:6],
        "none_found": NONE_FOUND,
        "terminal_line": TERMINAL_LINE,
    }


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
        look(d)
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


def reread_pass(platform: Any, *, limit: int = REREAD_BATCH) -> dict[str, Any]:
    """Read again, with the live search model, at most *limit* stored rows
    it did not write: memory rows whose vector names another model (or none),
    then project knowledge rows (bare vectors: walked once per model by a
    cursor kept in the file). Only while search by meaning is on with a model
    on this network (``skipped`` says why not). ``{done, read, more}``."""
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

    done = 0
    more = False
    # A row this model wrote starts with its tag (``encode_embedding``), so
    # the rows to read again are found by that prefix: ids only, newest
    # first, never every row's vector in memory.
    tag = '{"model": ' + json.dumps(model)
    with session_scope(platform.engine) as db:
        ids = list(db.exec(
            select(MemoryRecord.id)
            .where(not_(MemoryRecord.embedding_json.startswith(tag)))  # type: ignore[union-attr]
            .where(MemoryRecord.text != "")
            .order_by(MemoryRecord.created_at.desc())
            .limit(limit + 1)
        ))
    for rid in ids[:limit]:
        with session_scope(platform.engine) as db:
            row = db.get(MemoryRecord, rid)
            text = str(getattr(row, "text", "") or "")
        if row is None or not text.strip() or decode_embedding(row.embedding_json)[1] == model:
            continue
        try:
            vec, made_by = embed_with_model(emb, text)
        except EmbedderError:
            return {"skipped": "the search model did not answer", "read": done, "more": True}
        if made_by != model:
            return {"skipped": "the search model changed", "read": done, "more": True}
        with session_scope(platform.engine) as db:
            live = db.get(MemoryRecord, rid)
            if live is not None and live.text == text:
                live.embedding_json = encode_embedding(vec, made_by)
                db.add(live)
                db.commit()
        done += 1
    more = len(ids) > limit
    left = limit - done
    if left > 0 and not more:
        data = read(platform.config.home)
        cursors = dict(data.get("knowledge_read") or {})
        after = str(cursors.get(model) or "")
        with session_scope(platform.engine) as db:
            stmt = select(ProjectKnowledge).order_by(ProjectKnowledge.id)
            if after:
                stmt = stmt.where(ProjectKnowledge.id > after)
            krows = list(db.exec(stmt.limit(left + 1)))
        last = after
        for k in krows[:left]:
            text = (k.text or "")[:4000]
            if text.strip():
                try:
                    vec, made_by = embed_with_model(emb, text)
                except EmbedderError:
                    more = True
                    break
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
            update(platform.config.home, knowledge_read=cursors)
    return {"read": done, "more": more}
