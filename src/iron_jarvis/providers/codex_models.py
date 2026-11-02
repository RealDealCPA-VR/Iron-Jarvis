"""Codex's OWN model list, read from the file Codex writes (v1.424.0, D2).

The audit (docs/HARNESS-BUILD-AUDIT-2026-11-01.md, gap D2): a ChatGPT
subscription served through the ``codex`` CLI could only be used on "Codex's
default" — every picker listed that one row — while a keyless ``openai`` name
the CLI serves listed curated API ids (``gpt-4o``…) that a ChatGPT plan cannot
run, so a pick of one failed inside Codex and could be moved elsewhere.

Codex itself keeps the models the signed-in account may use in
``<CODEX_HOME or ~/.codex>/models_cache.json`` (refreshed by Codex; read on
this PC on 2026-11-02 with codex-cli 0.157.0): ``{"models": [{slug,
display_name, description, default_reasoning_level,
supported_reasoning_levels: [{effort, description}], visibility: "list" |
"hide", priority, context_window, effective_context_window_percent, …}]}``
plus account fields Iron Jarvis never reads. This module is the ONE reader of
that file: the model menus (``routes/connections``), Build › Code's Codex list
(``hosted/service``), the reasoning table (``providers/reasoning``), the
Codex adapter's "not offered" refusal and the context-window rung
(``daemon/chat_turn.refresh_codex_windows``) all read what it parsed.

RULES:

* **Never starts Codex.** A route reads :func:`catalog`, which answers the
  last reading at once and refreshes a stale one on a background thread; only
  :func:`read_catalog` touches the disk (BLOCKING — a thread, or a test).
* **No file, no change.** With nothing read (no Codex, an unreadable file)
  every caller behaves exactly as before this module existed (``None``).
* **What Codex offers.** ``visibility: "list"`` rows are offered, in Codex's
  own ``priority`` order; a ``hide`` row is not offered but is still a model
  Codex KNOWS (it runs one asked for by name — never refused).
* **The plan's default** — the model Codex starts on with no ``-m``: the
  ``model`` named in Codex's own ``config.toml`` (a selected ``profile``'s
  first), else the first offered row by priority. That second half follows
  codex-rs's model manager (it sorts the presets by ``priority`` and marks
  the first visible one default when none is marked); it was NOT watched on a
  live ``model/list`` call, which needs a signed-in Codex.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

#: The file Codex writes its model list to, under its home.
CATALOG_FILE = "models_cache.json"
#: Codex's own settings (read for ``model`` / ``profile`` only).
CONFIG_FILE = "config.toml"
#: A reading is trusted this long before it is refreshed (in the background).
TTL_S = 300.0
#: The largest list file read (the real one is ~300 KB).
MAX_BYTES = 8_000_000
#: The largest config.toml read.
MAX_CONFIG_BYTES = 1_000_000
#: Ids meaning "Codex's own default" (no ``-m``).
PLACEHOLDER_MODELS = frozenset({"", "subscription", "default", "auto"})
#: The effort words Iron Jarvis has (``reasoning.ALL_LEVELS``), in its order.
#: Codex also lists ``max`` / ``ultra`` (and ``minimal`` on older models): no
#: Iron Jarvis menu offers those, so they are never offered here either.
APP_LEVELS: tuple[str, ...] = ("low", "medium", "high", "xhigh")

_lock = threading.Lock()  # guards the fields below
#: ``home`` — the Codex home the reading is OF: a reading of another home
#: (``CODEX_HOME`` changed) is not this one's and is read again.
_state: dict[str, Any] = {"at": 0.0, "data": None, "busy": False, "home": ""}
#: Moves whenever what was read CHANGES (the model catalog's fingerprint).
_generation = 0


#: TESTS ONLY (``tests/conftest.py``): the home read when ``CODEX_HOME`` is
#: unset, instead of the developer's real ``~/.codex`` — several suites
#: delete ``CODEX_HOME`` to isolate Iron-Proxy leases, and a model list is now
#: read on every ``GET /models``. None in the app: Codex's own rule.
TEST_HOME_FALLBACK: "Path | str | None" = None


def codex_home() -> Path:
    """``CODEX_HOME`` when set, else ``~/.codex`` (Codex's own rule)."""
    override = (os.environ.get("CODEX_HOME") or "").strip()
    if override:
        return Path(override)
    return cli_default_home()


def cli_default_home() -> Path:
    """The home Codex uses with ``CODEX_HOME`` UNSET — ``~/.codex`` (an
    adopted Iron-Proxy account's child runs so). The tests' fallback folder
    instead while a suite runs (:data:`TEST_HOME_FALLBACK`), so no reader of
    this module ever reaches the developer's real one (v1.424.0 review)."""
    if TEST_HOME_FALLBACK is not None:
        return Path(TEST_HOME_FALLBACK)
    return Path.home() / ".codex"


def reset() -> None:
    """Forget the last reading (tests)."""
    global _generation
    with _lock:
        _state.update({"at": 0.0, "data": None, "busy": False, "home": ""})
        _generation += 1


def generation() -> int:
    """A number that moves whenever the reading changes. Never raises."""
    return _generation


# --------------------------------------------------------------------------- #
# reading the files (BLOCKING)
# --------------------------------------------------------------------------- #


def _levels(raw: Any) -> list[str]:
    """Codex's ``supported_reasoning_levels`` as effort words, its order."""
    out: list[str] = []
    for item in raw if isinstance(raw, list) else []:
        word = item.get("effort") if isinstance(item, dict) else item
        word = str(word or "").strip().lower()
        if word and word not in out:
            out.append(word)
    return out


def parse(data: Any) -> list[dict[str, Any]]:
    """The model rows of a ``models_cache.json`` (a dict with ``models``, or
    the list itself): ``{id, label, description, efforts, default_effort,
    window, listed, priority}`` — ``efforts`` Codex's own words in its order,
    ``window`` the EFFECTIVE window (``context_window`` x
    ``effective_context_window_percent``, None when not stated), ``listed``
    True for a row Codex offers. Pure; never raises; nothing else is kept
    (the file's account fields are never read)."""
    models = data.get("models") if isinstance(data, dict) else data
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for m in models if isinstance(models, list) else []:
        if not isinstance(m, dict):
            continue
        slug = str(m.get("slug") or m.get("id") or "").strip()
        if not slug or slug in seen or len(slug) > 120:
            continue
        seen.add(slug)
        win = m.get("context_window")
        window: "int | None" = None
        if isinstance(win, int) and not isinstance(win, bool) and win > 0:
            window = win
            pct = m.get("effective_context_window_percent")
            if isinstance(pct, (int, float)) and not isinstance(pct, bool) and 0 < pct <= 100:
                window = int(win * pct / 100)
        prio = m.get("priority")
        rows.append({
            "id": slug,
            "label": " ".join(str(m.get("display_name") or slug).split())[:80] or slug,
            "description": " ".join(str(m.get("description") or "").split())[:200],
            "efforts": _levels(m.get("supported_reasoning_levels")),
            "default_effort": str(m.get("default_reasoning_level") or "").strip().lower(),
            "window": window,
            "listed": m.get("visibility") in (None, "list"),
            "priority": prio if isinstance(prio, (int, float)) and not isinstance(prio, bool) else None,
        })
    return rows


def configured_model(home: "Path | None" = None) -> str:
    """The model Codex's own ``config.toml`` names (a selected profile's
    first, then the top-level ``model``), "" when it names none or cannot be
    read. BLOCKING; never raises."""
    try:
        import tomllib

        path = (home or codex_home()) / CONFIG_FILE
        if not path.is_file() or path.stat().st_size > MAX_CONFIG_BYTES:
            return ""
        cfg = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — no setting
        return ""
    if not isinstance(cfg, dict):
        return ""
    profile = cfg.get("profile")
    profiles = cfg.get("profiles")
    if isinstance(profile, str) and isinstance(profiles, dict):
        chosen = profiles.get(profile)
        if isinstance(chosen, dict) and isinstance(chosen.get("model"), str) and chosen["model"].strip():
            return chosen["model"].strip()
    model = cfg.get("model")
    return model.strip() if isinstance(model, str) else ""


def _default_of(rows: list[dict[str, Any]], configured: str) -> str:
    """The model Codex starts on (the module docstring): the configured one
    when Codex knows it, else the first offered row by priority."""
    known = {r["id"] for r in rows}
    if configured and configured in known:
        return configured
    if configured:
        return ""  # a model Codex's list does not hold: not known here
    offered = [r for r in rows if r["listed"]]
    if not offered:
        return ""
    ordered = sorted(
        enumerate(offered),
        key=lambda ir: (ir[1]["priority"] is None, ir[1]["priority"] or 0, ir[0]),
    )
    return ordered[0][1]["id"]


def _written_at(raw: Any, path: Path) -> "float | None":
    """When Codex WROTE this list (epoch seconds): its own ``fetched_at``,
    else the file's modification time; None when neither can be read.
    v1.424.0 (review): the age decides whether the list is EVIDENCE."""
    stamp = raw.get("fetched_at") if isinstance(raw, dict) else None
    if isinstance(stamp, str) and stamp.strip():
        try:
            from datetime import datetime, timezone

            text = stamp.strip().replace("Z", "+00:00")
            # Python's parser takes at most six fraction digits; Codex writes 7+.
            if "." in text:
                head, _, rest = text.partition(".")
                digits = "".join(ch for ch in rest if ch.isdigit())
                tail = rest[len(digits):]
                text = f"{head}.{digits[:6]}{tail}" if digits else f"{head}{tail}"
            when = datetime.fromisoformat(text)
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            return when.timestamp()
        except (TypeError, ValueError, OverflowError):
            pass
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _read(base: Path) -> "dict[str, Any] | None":
    """Parse ``base``'s list (BLOCKING): ``{"models", "default", "written"}``
    or None. Touches no module state; never raises."""
    try:
        path = base / CATALOG_FILE
        if path.is_file() and path.stat().st_size <= MAX_BYTES:
            raw = json.loads(path.read_text(encoding="utf-8"))
            rows = parse(raw)
            if rows:
                return {"models": rows, "default": _default_of(rows, configured_model(base)),
                        "written": _written_at(raw, path)}
    except Exception:  # noqa: BLE001 — an unreadable list is "not known"
        log.debug("reading Codex's model list failed", exc_info=True)
    return None


def _menu_part(data: "dict[str, Any] | None") -> Any:
    return None if data is None else (data["models"], data["default"])


def read_catalog(home: "Path | None" = None) -> "dict[str, Any] | None":
    """READ the files now (BLOCKING): ``{"models": rows, "default": id,
    "written": epoch | None}`` or None when Codex's list is missing or
    unreadable. The reading is kept (the next :func:`catalog` answers it).
    Never raises."""
    global _generation
    base = home or codex_home()
    data = _read(base)
    with _lock:
        # The menus' fingerprint moves with what they SHOW, never with the
        # write time alone (Codex rewrites the file on every start).
        if _menu_part(data) != _menu_part(_state["data"]):
            _generation += 1
        _state.update({"at": time.monotonic(), "data": data, "busy": False, "home": str(base)})
    return _copy(data)


def _copy(data: "dict[str, Any] | None") -> "dict[str, Any] | None":
    if data is None:
        return None
    return {"models": [dict(r, efforts=list(r["efforts"])) for r in data["models"]],
            "default": data["default"], "written": data.get("written")}


# --------------------------------------------------------------------------- #
# the cached reading (NEVER blocks)
# --------------------------------------------------------------------------- #


def catalog() -> "dict[str, Any] | None":
    """The last reading at once (None when nothing was read or there is no
    list). A reading older than :data:`TTL_S` (or none at all) is refreshed on
    a background thread for the next call. Never blocks; never raises."""
    try:
        home = str(codex_home())
        with _lock:
            other = bool(_state["at"]) and _state["home"] != home
            stale = time.monotonic() - float(_state["at"] or 0.0) > TTL_S or not _state["at"] or other
            start = stale and not _state["busy"]
            if start:
                _state["busy"] = True
            have = None if other else _state["data"]
        if start:
            threading.Thread(target=read_catalog, name="codex-models", daemon=True).start()
        return _copy(have)
    except Exception:  # noqa: BLE001
        return None


def offered(data: "dict[str, Any] | None | object" = ...) -> list[dict[str, Any]]:
    """The rows Codex offers (``visibility: list``), in Codex's priority
    order; ``[]`` when nothing is known. Never blocks. ``data`` (v1.424.0
    review): another reading's rows (an account's own list); omitted = this
    app's own cached reading."""
    if data is ...:
        data = catalog()
    if not data:
        return []
    rows = [r for r in data["models"] if r["listed"]]
    return [r for _, r in sorted(
        enumerate(rows), key=lambda ir: (ir[1]["priority"] is None, ir[1]["priority"] or 0, ir[0]),
    )]


def default_model() -> str:
    """The model Codex starts on with no ``-m`` ("" = not known). Never blocks."""
    data = catalog()
    return str(data["default"]) if data else ""


def lookup(model: Any) -> "dict[str, Any] | None":
    """The row for ``model`` (a placeholder reads the default's row), else
    None. Never blocks."""
    data = catalog()
    if not data:
        return None
    name = str(model or "").strip()
    if name.lower() in PLACEHOLDER_MODELS:
        name = str(data["default"] or "")
    return next((r for r in data["models"] if r["id"] == name), None) if name else None


def _holds(data: "dict[str, Any] | None", model: Any) -> "bool | None":
    name = str(model or "").strip()
    if name.lower() in PLACEHOLDER_MODELS or not data:
        return None
    # v1.424.0 (review): case-blind — "GPT-6-Sol" is the listed gpt-6-sol;
    # whether Codex itself is case-blind is not known, so never refused.
    low = name.lower()
    return any(str(r["id"]).lower() == low for r in data["models"])


def runs(model: Any) -> "bool | None":
    """Does Codex know ``model``? True / False, or None when that cannot be
    said (no list read, or a placeholder — Codex's own default always runs).
    A hidden row counts as known: Codex runs a model asked for by name that
    it does not list in its picker. Never blocks."""
    return _holds(catalog(), model)


#: v1.424.0 (review): a list counts as EVIDENCE that a model is not on the
#: plan only while it is this recent. Codex rewrites ``models_cache.json``
#: whenever it starts on that home and its copy is older than five minutes,
#: so an older file means Codex has not run there since — a model released
#: meanwhile, or a login changed since (an API key runs models a plan does
#: not), would be refused on a stale reading. Older than this: not known, so
#: never refused (Codex's own answer decides).
EVIDENCE_MAX_AGE_S = 24 * 3600.0


def _same_home(a: Path, b: Path) -> bool:
    try:
        return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(os.path.abspath(str(b)))
    except (OSError, ValueError):
        return False


def evidence(home: "Path | str | None" = None) -> "dict[str, Any] | None":
    """The list of the home a call RUNS IN (``home``; None = this app's own,
    :func:`codex_home`), when it is EVIDENCE: read and recent
    (:data:`EVIDENCE_MAX_AGE_S`). None otherwise — nothing may be refused on
    it. BLOCKING only for ANOTHER home (an Iron-Proxy account's own folder:
    its own plan's list, read now); this app's own home answers the cached
    reading. Never raises (v1.424.0 review)."""
    try:
        target = Path(home) if home else None
        if target is None or _same_home(target, codex_home()):
            data = catalog()
        else:
            data = _read(target)
        if not data:
            return None
        written = data.get("written")
        if not isinstance(written, (int, float)) or time.time() - float(written) > EVIDENCE_MAX_AGE_S:
            return None
        return data
    except Exception:  # noqa: BLE001 — unsure: no evidence
        return None


def refuses(model: Any, home: "Path | str | None" = None) -> "dict[str, Any] | None":
    """The evidence list when Codex on ``home`` would NOT run ``model``
    (positive evidence: :func:`evidence` is read, recent, and does not hold
    the model — hidden rows count as held); None when it may run or nothing
    can be said. BLOCKING for another home (see :func:`evidence`)."""
    name = str(model or "").strip()
    if name.lower() in PLACEHOLDER_MODELS:
        return None
    data = evidence(home)
    return data if _holds(data, name) is False else None


def effort_levels(model: Any) -> "tuple[str, ...] | None":
    """The levels Iron Jarvis may offer for ``model`` (:data:`APP_LEVELS`
    that Codex lists for it, in Iron Jarvis's order), or None when not known
    (no list, a model it does not hold, or a row that names no levels). A
    placeholder reads the default's row. Never blocks."""
    row = lookup(model)
    if row is None or not row["efforts"]:
        return None
    return tuple(x for x in APP_LEVELS if x in row["efforts"])


def offered_words(limit: int = 6, data: "dict[str, Any] | None | object" = ...) -> str:
    """``GPT-6-Astra, GPT-6-Sol, …`` — the names Codex offers, for a
    sentence. "" when nothing is known. ``data``: as :func:`offered`."""
    rows = offered(data)
    names = [r["label"] for r in rows[:limit]]
    more = len(rows) - len(names)
    return ", ".join(names) + (f" and {more} more" if more > 0 else "")


__all__ = [
    "APP_LEVELS",
    "CATALOG_FILE",
    "PLACEHOLDER_MODELS",
    "catalog",
    "cli_default_home",
    "codex_home",
    "configured_model",
    "default_model",
    "EVIDENCE_MAX_AGE_S",
    "effort_levels",
    "evidence",
    "generation",
    "lookup",
    "offered",
    "offered_words",
    "parse",
    "read_catalog",
    "refuses",
    "reset",
    "runs",
]
