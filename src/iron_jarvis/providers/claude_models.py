"""The Claude subscription's LIVE model picker, and exact ``--model`` routing (v1.300.0).

The ``claude-cli`` provider (and a keyless ``anthropic``, which inherits it) runs
through the user's logged-in ``claude`` CLI. Which models that account can use
is the CLI's answer, not ours: its stream-json ``initialize`` handshake returns
the account's own picker (rows ``{value, resolvedModel, displayName,
description, ...}``) plus ``account.subscriptionType`` in ~0.4-0.7 s with NO
model call. Design after NousResearch's MIT "claude-subscription-directsdk"
(``model_catalog.py`` + ``directsdk_setup.discover_models``) with one
deliberate difference: they PIN aliases (``sonnet`` -> ``claude-sonnet-5``),
which was already stale on this account (it offers Sonnet 5.5). Here routing
follows the LIVE picker; :data:`PINNED` is only the fallback catalog and the
metadata (context window) for ids it knows.

Why ``[1m]`` matters: behind a loopback ``ANTHROPIC_BASE_URL`` (the v1.300.0
transport) Claude Code applies gateway defaults, and Sonnet gets 200K unless
``[1m]`` is selected. :func:`native_model` therefore appends ``[1m]`` for every
model whose window is 1M, and refuses an explicit ``[1m]`` on a 200K model
(Haiku 4.5: the API answers ``400 The long context beta is not yet available
for this subscription`` — measured 2026-10-02).

Nothing here may block the event loop: :func:`discover` runs a subprocess and
is for threads; the ``/models`` route reads :func:`catalog`, which serves the
in-process / on-disk cache (or the pinned table) at once and refreshes behind
it on a daemon thread. Failures are never silent: the result carries ``error``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

ONE_M = 1_000_000

#: Pinned metadata: canonical id -> (label, context window). Fallback catalog
#: when the handshake fails, and the window source for ids the live picker
#: names. Order is the fallback picker's order.
PINNED: dict[str, tuple[str, int]] = {
    "claude-opus-5-5": ("Opus 5.5", ONE_M),
    "claude-sonnet-5-5": ("Sonnet 5.5", ONE_M),
    "claude-haiku-4-5-20251001": ("Haiku 4.5", 200_000),
    "claude-fable-5-1": ("Fable 5.1", ONE_M),
    "claude-opus-5": ("Opus 5", ONE_M),
    "claude-sonnet-5": ("Sonnet 5", ONE_M),
    "claude-opus-4-8": ("Opus 4.8", ONE_M),
}

#: Fallback alias map — used only when no live picker has been seen. The live
#: picker's ``value -> resolvedModel`` pairs override it.
PINNED_ALIASES: dict[str, str] = {
    "opus": "claude-opus-5-5",
    "sonnet": "claude-sonnet-5-5",
    "haiku": "claude-haiku-4-5-20251001",
    "fable": "claude-fable-5-1",
    "claude-haiku-4-5": "claude-haiku-4-5-20251001",
}

#: Ids meaning "the CLI's own default" — no ``--model`` is sent.
DEFAULT_IDS = frozenset({"", "subscription", "default", "auto"})

CACHE_TTL_S = 600.0  # one handshake per 10 minutes
FAILURE_TTL_S = 60.0  # a failed handshake is retried sooner, never every call
CACHE_FILE = "claude_models.json"

_DESC_MODEL = re.compile(r"^\s*(opus|sonnet|haiku|fable)\s+(\d+(?:\.\d+)*)", re.I)

_lock = threading.Lock()  # guards the fields below
_flight = threading.Lock()  # single-flight: one handshake at a time
_mem: dict[str, Any] | None = None  # the last catalog served (success or fallback)
_mem_at: float = 0.0  # time.monotonic() of _mem
_home: Path | None = None
_refreshing = False


# --------------------------------------------------------------------------- #
# configuration + the subprocess chokepoint
# --------------------------------------------------------------------------- #


def set_home(home: "Path | str | None") -> None:
    """Where the disk cache lives (``<home>/claude_models.json``). Idempotent."""
    global _home
    with _lock:
        _home = Path(home) if home else None


def reset() -> None:
    """Forget the in-process cache (tests; a sign-out)."""
    global _mem, _mem_at, _refreshing
    with _lock:
        _mem, _mem_at, _refreshing = None, 0.0, False


def child_env(base: "dict[str, str] | None" = None) -> dict[str, str]:
    """THE shared env guard for every ``claude`` child Iron Jarvis spawns on the
    subscription path (this module's picker handshake AND the v1.300.0
    transport — import it, never copy it).

    Returns a NEW dict built from ``base`` (default: ``os.environ``; ``base`` is
    never mutated): the user's own login, never an override. Every
    ``ANTHROPIC_*`` variable (API key, auth token, base URL, model pins) and the
    cloud-provider switches (``CLAUDE_CODE_USE_BEDROCK/VERTEX/FOUNDRY``) plus
    ``CLAUDE_CODE_EXTRA_BODY`` are removed, so the child is the SUBSCRIPTION's,
    and ``CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`` is set. A caller that
    needs its own loopback relay sets ``ANTHROPIC_BASE_URL`` on the RESULT,
    after the guard — never before it."""
    env = dict(os.environ if base is None else base)
    for key in list(env):
        if key.startswith("ANTHROPIC_") or key in (
            "CLAUDE_CODE_USE_BEDROCK",
            "CLAUDE_CODE_USE_VERTEX",
            "CLAUDE_CODE_USE_FOUNDRY",
            "CLAUDE_CODE_EXTRA_BODY",
        ):
            env.pop(key, None)
    env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    return env


def _claude_binary() -> str | None:
    return shutil.which("claude")


def _run_handshake(timeout: float) -> str:
    """Spawn ``claude -p`` in stream-json mode, send one ``initialize`` control
    request, return stdout. THE subprocess chokepoint (tests replace it)."""
    exe = _claude_binary()
    if not exe:
        raise FileNotFoundError("the claude CLI is not installed (no `claude` on PATH)")
    argv = [exe, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose"]
    frame = json.dumps(
        {"type": "control_request", "request_id": "ij-picker", "request": {"subtype": "initialize"}}
    )
    # ignore_cleanup_errors: on Windows the CLI's own child can still hold the
    # cwd for a moment after it exits (measured); a temp-dir leak is not an error.
    with tempfile.TemporaryDirectory(prefix="ij-claude-picker-", ignore_cleanup_errors=True) as cwd:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=child_env(),
            cwd=cwd,
            start_new_session=os.name != "nt",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            out, _err = proc.communicate((frame + "\n").encode("utf-8"), timeout=timeout)
        except subprocess.TimeoutExpired:
            # subprocess.run(timeout=) kills only the child, then blocks on pipes
            # a grandchild still holds (the v1.228.0 lesson): kill the TREE.
            from ..sandbox.native import _kill_tree

            _kill_tree(proc)
            try:
                proc.communicate(timeout=5)
            except Exception:  # noqa: BLE001 — the TimeoutExpired below is the answer
                pass
            raise
    return (out or b"").decode("utf-8", errors="replace")


# --------------------------------------------------------------------------- #
# parsing
# --------------------------------------------------------------------------- #


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _strip_1m(model: str) -> str:
    return model[:-4] if model.lower().endswith("[1m]") else model


def _canonical_from_description(desc: str) -> str | None:
    m = _DESC_MODEL.match(desc or "")
    if not m:
        return None
    guess = f"claude-{m.group(1).lower()}-{m.group(2).replace('.', '-')}"
    return PINNED_ALIASES.get(guess, guess)


def _row_canonical(row: dict) -> str | None:
    """Our id for a picker row: ``resolvedModel`` / a full-id ``value`` (minus
    ``[1m]``), else the description's family + version. ``None`` = an alias row
    we cannot name (skipped, never guessed)."""
    for key in ("resolvedModel", "value"):
        raw = _strip_1m(str(row.get(key) or "").strip()).lower()
        if raw.startswith("claude-"):
            return PINNED_ALIASES.get(raw, raw)
    return _canonical_from_description(str(row.get("description") or ""))


def _label(row: dict, model_id: str) -> str:
    head = str(row.get("description") or "").split("·")[0].strip()
    if _DESC_MODEL.match(head):
        return head
    pinned = PINNED.get(model_id)
    return pinned[0] if pinned else (str(row.get("displayName") or "").strip() or model_id)


def _usage_credits(desc: str, model_id: str, subscription: str | None) -> bool:
    if "usage credit" in (desc or "").lower():
        return True
    # Anthropic's plan rule: Fable bills to usage credits from the first
    # request on every plan but Max; the picker only says so at request time.
    plan = (subscription or "").lower()
    return bool(plan) and "max" not in plan and model_id.startswith("claude-fable")


def _parse(stdout: str) -> dict:
    """Handshake stdout -> catalog dict. Raises ValueError on anything odd."""
    response = None
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("type") == "control_response":
            response = row.get("response") or {}
            break
    if response is None:
        raise ValueError("the claude CLI did not answer the initialize handshake")
    if response.get("subtype") == "error":
        raise ValueError(f"the claude CLI refused the handshake: {response.get('error') or 'error'}")
    inner = response.get("response") if isinstance(response.get("response"), dict) else response
    native = inner.get("models")
    if not isinstance(native, list) or not native:
        raise ValueError("the claude CLI's picker was empty")
    account = inner.get("account") if isinstance(inner.get("account"), dict) else {}
    subscription = str(account.get("subscriptionType") or "").strip() or None
    if not subscription and not account.get("email"):
        # Logged out, the handshake still answers with a generic list — it is
        # not this account's picker, so it must not pose as one.
        raise ValueError("the claude CLI is not signed in (its picker is not the account's)")
    offered_1m = {
        _strip_1m(str(r.get("value") or "")).lower()
        for r in native
        if isinstance(r, dict) and str(r.get("value") or "").lower().endswith("[1m]")
    }
    rows: dict[str, dict] = {}
    for r in native:
        if not isinstance(r, dict):
            continue
        mid = _row_canonical(r)
        if not mid:
            continue
        value = str(r.get("value") or "").strip()
        desc = str(r.get("description") or "").strip()
        pinned = mid in PINNED
        if pinned:
            window: int | None = PINNED[mid][1]
        elif mid in offered_1m or _strip_1m(str(r.get("resolvedModel") or "")).lower() in offered_1m:
            window = ONE_M  # the CLI itself offered the [1m] form
        else:
            window = None  # unknown: never promised 1M, never refused [1m]
        is_default = value.lower() == "default"
        prev = rows.get(mid)
        if prev is not None:
            # `default` and `opus` are one model: one row, keep the real value.
            if prev["value"].lower() == "default" and not is_default:
                prev["value"] = value
            prev["default"] = prev["default"] or is_default
            prev["usage_credits"] = prev["usage_credits"] or _usage_credits(desc, mid, subscription)
            continue
        rows[mid] = {
            "id": mid,
            "value": value or mid,
            "label": _label(r, mid),
            "description": desc,
            "native": mid + "[1m]" if window == ONE_M else mid,
            "context_window": window,
            "usage_credits": _usage_credits(desc, mid, subscription),
            "pinned": pinned,
            "default": is_default,
        }
    if not rows:
        raise ValueError("the claude CLI's picker named no model we can route")
    return {"models": list(rows.values()), "subscription": subscription, "at": _now_iso(), "error": None}


def pinned_catalog(error: str | None = None) -> dict:
    """The fallback catalog (the pinned table), with ``error`` saying why."""
    models = [
        {
            "id": mid,
            "value": mid + "[1m]" if win == ONE_M else mid,
            "label": label,
            "description": "",
            "native": mid + "[1m]" if win == ONE_M else mid,
            "context_window": win,
            "usage_credits": False,
            "pinned": True,
            "default": False,
        }
        for mid, (label, win) in PINNED.items()
    ]
    return {"models": models, "subscription": None, "at": _now_iso(), "error": error}


# --------------------------------------------------------------------------- #
# cache
# --------------------------------------------------------------------------- #


def _disk_path() -> Path | None:
    with _lock:
        return (_home / CACHE_FILE) if _home is not None else None


def _read_disk() -> dict | None:
    path = _disk_path()
    if path is None:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("models"), list) or not data["models"]:
        return None
    if data.get("error"):
        return None  # only a real discovery is ever persisted
    return data


def _write_disk(data: dict) -> None:
    path = _disk_path()
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:  # a cache that cannot be written is only slower
        log.debug("claude model cache not written: %s", exc)


def _age_s(data: dict) -> float:
    try:
        at = datetime.fromisoformat(str(data.get("at")))
        return max(0.0, (datetime.now(timezone.utc) - at).total_seconds())
    except (TypeError, ValueError):
        return float("inf")


def _fresh(now: float) -> dict | None:
    with _lock:
        if _mem is None:
            return None
        ttl = FAILURE_TTL_S if _mem.get("error") else CACHE_TTL_S
        return _mem if now - _mem_at < ttl else None


def _remember(data: dict, *, at: float) -> None:
    global _mem, _mem_at
    with _lock:
        _mem, _mem_at = data, at


def _copy(data: dict) -> dict:
    return json.loads(json.dumps(data))


def discover(*, force: bool = False, timeout: float = 20.0) -> dict:
    """The account's live picker as our catalog (see the module docstring).

    BLOCKING (a subprocess, <1 s typical) — call from a thread, never the loop.
    Cached in-process for 10 minutes and on disk at ``<home>/claude_models.json``
    (a cold start within the TTL reads the disk, no subprocess). ``force``
    always runs the handshake. Any failure answers :func:`pinned_catalog` with
    ``error`` set (retried after a minute, not on every call)."""
    if not force:
        hit = _fresh(time.monotonic())
        if hit is not None:
            return _copy(hit)
        _load_disk_once(time.monotonic())
        hit = _fresh(time.monotonic())
        if hit is not None:
            return _copy(hit)
    with _flight:
        if not force:  # a concurrent caller may have just refreshed it
            hit = _fresh(time.monotonic())
            if hit is not None:
                return _copy(hit)
        try:
            data = _parse(_run_handshake(timeout))
        except subprocess.TimeoutExpired:
            data = pinned_catalog(f"the claude CLI did not answer its model picker within {timeout:g}s")
        except Exception as exc:  # noqa: BLE001 — every failure is the pinned table + a reason
            data = pinned_catalog(str(exc) or exc.__class__.__name__)
        if data.get("error"):
            log.info("claude model discovery fell back to the pinned table: %s", data["error"])
        else:
            _write_disk(data)
        _remember(data, at=time.monotonic())
        return _copy(data)


def _refresh_in_background(timeout: float) -> bool:
    global _refreshing
    with _lock:
        if _refreshing:
            return False
        _refreshing = True

    def _run() -> None:
        global _refreshing
        try:
            discover(force=True, timeout=timeout)
        except Exception:  # noqa: BLE001 — a background refresh never raises
            pass
        finally:
            with _lock:
                _refreshing = False

    threading.Thread(target=_run, name="claude-models-refresh", daemon=True).start()
    return True


def _load_disk_once(now: float) -> None:
    """Cold start: adopt the disk cache as the in-process one, with its real age."""
    with _lock:
        cold = _mem is None
    if cold:
        disk = _read_disk()
        if disk is not None:
            _remember(disk, at=now - _age_s(disk))


def catalog(*, refresh: bool = True, timeout: float = 20.0) -> dict:
    """NON-BLOCKING read for the ``/models`` route: the fresh in-process
    catalog, else the disk cache, else the pinned table — at once. When that
    answer is stale (or not a discovery) and ``refresh`` is true, ONE
    background handshake refreshes it for the next call. The caller decides
    ``refresh`` (the route passes "the CLI is known to be signed in")."""
    now = time.monotonic()
    _load_disk_once(now)
    hit = _fresh(now)
    if hit is not None:
        return _copy(hit)
    if refresh:
        _refresh_in_background(timeout)
    with _lock:
        have = _mem
    if have is not None:
        return _copy(have)
    return pinned_catalog("not discovered yet — the claude CLI's picker is being read")


def _known() -> dict:
    """The catalog routing consults: in-process, else disk, else pinned. Never spawns."""
    _load_disk_once(time.monotonic())
    with _lock:
        have = _mem
    return have or pinned_catalog()


# --------------------------------------------------------------------------- #
# routing
# --------------------------------------------------------------------------- #


def _lookup(model_id: str) -> tuple[str, dict | None]:
    """``(canonical id, catalog row or None)`` for an id / alias / picker value."""
    base = _strip_1m((model_id or "").strip()).lower()
    data = _known()
    rows = [r for r in data.get("models") or [] if isinstance(r, dict)]
    for r in rows:
        if base == str(r.get("id") or "").lower():
            return r["id"], r
    for r in rows:  # a live picker value ("sonnet", "claude-fable-5-1[1m]")
        if base and base == _strip_1m(str(r.get("value") or "")).lower():
            return r["id"], r
    canon = PINNED_ALIASES.get(base, base)
    for r in rows:
        if canon == str(r.get("id") or "").lower():
            return r["id"], r
    return canon, None


def _default_row() -> dict | None:
    """The LIVE picker row the CLI's own default resolves to (``default: True``
    — only a real discovery sets it; the pinned table never guesses one)."""
    for r in _known().get("models") or []:
        if isinstance(r, dict) and r.get("default") and r.get("id"):
            return r
    return None


def context_window(model_id: str) -> int | None:
    """The window (tokens) our ``--model`` selection gets, or ``None`` when
    unknown (an id neither the picker nor the table names, or the CLI default
    before any discovery has named it)."""
    if (model_id or "").strip().lower() in DEFAULT_IDS:
        row = _default_row()
        return context_window(row["id"]) if row is not None else None
    canon, row = _lookup(model_id)
    if row is not None and row.get("context_window"):
        return int(row["context_window"])
    if canon in PINNED:
        return PINNED[canon][1]
    return None


def native_model(model_id: str) -> str | None:
    """Our id / an alias / a full id (with or without ``[1m]``) -> the exact
    ``claude --model`` value. 1M models get ``[1m]``; a 200K model asked for
    ``[1m]`` raises ValueError with a sentence; an id nobody knows passes
    through UNCHANGED. "subscription"/"default"/"" (the CLI's default) become
    the live default row's exact value (incl. ``[1m]``) when discovery has
    named it — behind the loopback relay a bare default would get the gateway's
    200K — else ``None`` (send no ``--model``)."""
    raw = (model_id or "").strip()
    if raw.lower() in DEFAULT_IDS:
        row = _default_row()
        return native_model(row["id"]) if row is not None else None
    wants_1m = raw.lower().endswith("[1m]")
    canon, row = _lookup(raw)
    window = context_window(canon)
    if window is None:
        if row is not None:  # discovered but unpinned: exact id, [1m] as asked
            return canon + "[1m]" if wants_1m else canon
        return raw
    if window >= ONE_M:
        return canon + "[1m]"
    if wants_1m:
        label = (row or {}).get("label") or (PINNED.get(canon) or (canon,))[0]
        raise ValueError(
            f"{label} has a {window // 1000}K context window and cannot run with [1m] — "
            f"pick it without [1m], or choose a 1M model."
        )
    return canon
