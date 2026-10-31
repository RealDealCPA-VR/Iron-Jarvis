"""What a coding tool SAID about its account and its conversation (v1.414.0,
the Code view's Usage and Session tabs).

The real CLIs report, in their own frames, numbers Iron Jarvis used to read
only for a limit: Claude Code's ``rate_limit_event`` carries its usage
windows (``unifiedWindows``: utilization + reset per window), Codex's
``account/rateLimits/updated`` its ``primary`` / ``secondary`` windows
(``usedPercent``, ``windowDurationMins``, ``resetsAt``) and plan. How full
the conversation is: Claude Code's ``result`` (the LAST call's prompt —
``usage.iterations[-1]`` — over ``modelUsage[*].contextWindow``), Codex's
``thread/tokenUsage/updated`` (``last.totalTokens`` over
``modelContextWindow``), OpenCode's ``usage_update`` (``used`` / ``size``).
Pi says none of these over its RPC: nothing is kept for it.

A driver NOTES what it saw (:func:`note_windows`, :func:`note_context`) and
puts it on the turn's ``usage`` event (:func:`attach`): ``windows`` [{key,
label, percent, resets_at}], ``plan`` (when the tool named it) and
``context`` {used, window}. The event log keeps it, so the tabs read it after
the process has ended too — always with the time it was SEEN, never as
"now". A number the tool did not send is left out (never 0%).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

#: Window keys the tools use -> plain words.
WINDOW_LABELS: dict[str, str] = {
    "five_hour": "5-hour window",
    "seven_day": "7-day window",
    "seven_day_opus": "7-day window (Opus)",
    "seven_day_sonnet": "7-day window (Sonnet)",
    "seven_day_oauth_apps": "7-day window (apps)",
}


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _iso(epoch: Any) -> str | None:
    t = _num(epoch)
    if t is None or t <= 0:
        return None
    try:
        return datetime.fromtimestamp(t, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except (OverflowError, OSError, ValueError):
        return None


def _mins_label(mins: Any) -> str:
    m = _num(mins)
    if m is None or m <= 0:
        return "usage window"
    m = int(m)
    if m % 1440 == 0:
        d = m // 1440
        return f"{d}-day window"
    if m % 60 == 0:
        return f"{m // 60}-hour window"
    return f"{m}-minute window"


def window_label(mins: Any, kind: Any = "") -> str:
    """A window's length as a label ("5-hour window"), from its minutes, else
    Iron-Proxy's ``kind`` ("5h", "7d")."""
    if _num(mins) is None:
        mins = {"5h": 300, "7d": 10080, "1d": 1440, "24h": 1440}.get(str(kind or "").lower())
    return _mins_label(mins)


def claude_windows(info: Any) -> list[dict[str, Any]]:
    """Claude Code's ``rate_limit_info.unifiedWindows`` as windows (a window
    with no utilization is left out)."""
    if not isinstance(info, dict):
        return []
    raw = info.get("unifiedWindows")
    out: list[dict[str, Any]] = []
    if isinstance(raw, dict):
        for key, w in raw.items():
            if not isinstance(w, dict):
                continue
            used = _num(w.get("utilization"))
            if used is None:
                continue
            k = str(key)[:40]
            out.append({
                "key": k,
                "label": WINDOW_LABELS.get(k, k.replace("_", " ")),
                "percent": int(round(used * 100)),
                "resets_at": _iso(w.get("resetsAt")),
            })
    return out


def codex_windows(limits: Any) -> tuple[list[dict[str, Any]], str]:
    """Codex's ``rateLimits`` -> (windows, plan)."""
    if not isinstance(limits, dict):
        return [], ""
    out: list[dict[str, Any]] = []
    for key in ("primary", "secondary"):
        w = limits.get(key)
        if not isinstance(w, dict):
            continue
        used = _num(w.get("usedPercent"))
        if used is None:
            continue
        out.append({
            "key": key,
            "label": _mins_label(w.get("windowDurationMins")),
            "percent": int(round(used)),
            "resets_at": _iso(w.get("resetsAt")),
        })
    plan = str(limits.get("planType") or "").strip()[:40]
    return out, plan


def claude_context(result: Any) -> tuple[int, int] | None:
    """(tokens in the LAST call's prompt + answer, the model's window) from a
    Claude Code ``result``; None when either is missing."""
    if not isinstance(result, dict):
        return None
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
    its = usage.get("iterations")
    last = its[-1] if isinstance(its, list) and its and isinstance(its[-1], dict) else None
    if last is None:
        return None
    used = sum(int(_num(last.get(k)) or 0) for k in (
        "input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens",
    ))
    window = 0
    mu = result.get("modelUsage")
    if isinstance(mu, dict):
        for row in mu.values():
            if isinstance(row, dict):
                window = max(window, int(_num(row.get("contextWindow")) or 0))
    if used <= 0 or window <= 0:
        return None
    return used, window


def codex_context(token_usage: Any) -> tuple[int, int] | None:
    if not isinstance(token_usage, dict):
        return None
    last = token_usage.get("last") if isinstance(token_usage.get("last"), dict) else {}
    used = int(_num(last.get("totalTokens")) or 0)
    window = int(_num(token_usage.get("modelContextWindow")) or 0)
    if used <= 0 or window <= 0:
        return None
    return used, window


def opencode_context(update: Any) -> tuple[int, int] | None:
    if not isinstance(update, dict):
        return None
    used = int(_num(update.get("used")) or 0)
    window = int(_num(update.get("size")) or 0)
    if used <= 0 or window <= 0:
        return None
    return used, window


def note_windows(driver: Any, windows: list[dict[str, Any]], plan: str = "") -> None:
    """Keep the newest windows the tool reported (an empty report keeps what
    was seen before — a frame with no numbers says nothing new)."""
    if windows:
        driver.seen_windows = windows
    if plan:
        driver.seen_plan = plan


def note_context(driver: Any, got: tuple[int, int] | None) -> None:
    if got is not None:
        driver.seen_context = {"used": int(got[0]), "window": int(got[1])}


def attach(event: dict[str, Any], driver: Any) -> dict[str, Any]:
    """Put what the driver saw on a ``usage`` event (only what it saw)."""
    windows = getattr(driver, "seen_windows", None)
    if windows:
        event["windows"] = list(windows)
    plan = getattr(driver, "seen_plan", "")
    if plan:
        event["plan"] = plan
    ctx = getattr(driver, "seen_context", None)
    if ctx:
        event["context"] = dict(ctx)
    return event
