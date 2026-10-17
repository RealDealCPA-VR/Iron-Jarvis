"""Self-care tools (v1.344.0, "Jarvis looks after itself").

Five tools that let a chat turn or an agent run look at the app's OWN health
and repair the two things a user otherwise fixes by hand on Settings:

* ``app_health``      (allow, read-only, replay-safe) — the app's health in
  plain words + data: version, the default model and whether it is reachable
  (CACHED availability, never a probe or a spawn), providers with an open
  circuit, failing background loops, packs by state with reason + fix, the
  last recent errors, disk space, interrupted jobs, the doctor's runtime
  rows that are not ok.
* ``pack_status``     (allow, read-only) — one pack or all: state, tools
  loaded, last error, reason, fix, the child's last output, failures, the
  NAMES of its env keys and secrets.
* ``pack_restart``    (ask tier, irreversible, denied under low trust) — the
  Tools page's Retry: ``mcp.tools.reload_pack``.
* ``provider_rescan`` (ask tier, denied under low trust) — the Connections
  page's Re-detect: ``routes.connections.rescan_providers``.
* ``run_doctor``      (allow, read-only) — the doctor's runtime check rows.

THREE RULES every tool here keeps:

1. ONE TRUTH. Each reads the same function its route reads
   (``routes.settings.diagnostics_snapshot``, ``routes.agents.mcp_server_row``,
   ``mcp.tools.reload_pack``, ``routes.connections.rescan_providers``,
   ``onboarding.doctor.runtime_checks``) — a tool and a page can never form
   two opinions about the same pack.
2. OFF THE LOOP, PER SECTION. Every read runs under ``asyncio.to_thread`` and
   each section of ``app_health`` is its own try: a section that cannot be
   read is NAMED in the answer (``unreadable``), the tool still answers
   ``ok=True`` with the rest — the user asked how the app is, and "the error
   ring could not be read" is part of the answer, not a crash in its place.
3. NO SECRET, NO PRIVATE PATH, NO UNMASKED ERROR. Every text that came from a
   log, a pack or a provider goes through ``detections.redact.mask`` (and is
   folded to one line); env/secret/header KEYS are listed, values never; a
   detected CLI's ``exec_path``/``base_url`` stay out of the answer.

The daemon's deps object (``d``: ``loop_health``, the orchestrator counters,
``startup``) is reached through ``platform.config_writer.d`` — the one back
reference ``create_app`` already attaches. A CLI-built platform has none, so
its loops read empty and ``diagnostics_snapshot`` says so by shape.
"""

from __future__ import annotations

import asyncio
import shutil
from types import SimpleNamespace
from typing import Any

from ..detections.redact import mask
from .base import Reversibility, RiskClass, Tool, ToolContext, ToolResult

#: How many recent errors ``app_health`` relays (the ring keeps 50).
RECENT_ERRORS_SHOWN = 10
#: One line per relayed text; a longer one is cut with a marker.
LINE_MAX = 300
#: The pack states a row may carry, in the order the summary names them.
PACK_STATES = ("ok", "starting", "failed", "failing")
#: Where the user fixes a pack / a provider by hand (the words the recovery
#: block and these tools agree on).
TOOLS_PAGE = "Settings > Tools"
CONNECTIONS_PAGE = "Settings > Connections"

SELFCARE_READERS = ("app_health", "pack_status", "run_doctor")
SELFCARE_REPAIRS = ("pack_restart", "provider_rescan")
SELFCARE_TOOLS = SELFCARE_READERS + SELFCARE_REPAIRS


# ------------------------------------------------------------------ helpers


def one_line(text: Any, limit: int = LINE_MAX) -> str:
    """``text`` masked (``redact.mask``), newlines folded to " | ", runs of
    whitespace collapsed, cut to ``limit`` with a marker. "" for nothing.
    Never raises — a masker fault leaves the text UNSHOWN rather than
    unmasked."""
    if text is None:
        return ""
    try:
        s = mask(str(text))
    except Exception:  # noqa: BLE001 — never hand out a text the masker refused
        return "[unreadable text]"
    s = " | ".join(part.strip() for part in s.splitlines() if part.strip())
    s = " ".join(s.split())
    if len(s) > limit:
        return s[: limit - 1].rstrip() + "…"
    return s


def daemon_deps(platform: Any) -> Any:
    """The daemon's deps object for ``platform`` (``loop_health``, the
    orchestrator, ``startup``), else a shim carrying only the platform."""
    d = getattr(platform, "daemon", None)
    if d is not None and getattr(d, "platform", None) is not None:
        return d
    writer = getattr(platform, "config_writer", None)
    d = getattr(writer, "d", None) if writer is not None else None
    if d is not None and getattr(d, "platform", None) is not None:
        return d
    return SimpleNamespace(platform=platform)


def _err(exc: BaseException) -> str:
    return one_line(f"{type(exc).__name__}: {exc}", 200)


def _gb(n: int | float) -> str:
    return f"{n / 1_000_000_000:.1f} GB"


def _sentence(s: str) -> str:
    """``s`` ending in a period (a classified reason often carries none)."""
    s = (s or "").rstrip()
    return s if not s or s.endswith((".", "!", "?")) else s + "."


# The readers behind each section, as module names so a test can break ONE
# of them (monkeypatch) and watch the section get named as unreadable while
# the rest still answers. Each is a thin door to the function its route reads.


def read_diagnostics(d: Any) -> dict[str, Any]:
    from ..daemon.routes.settings import diagnostics_snapshot

    return diagnostics_snapshot(d)


def read_recent_errors(limit: int) -> list[dict[str, Any]]:
    from ..core.logging import recent_errors

    return recent_errors(limit)


def read_doctor_rows(platform: Any) -> list[dict[str, Any]]:
    """The doctor's runtime rows WITHOUT a probe (v1.344.0 review): the
    Settings doctor also ``httpx.get``s the custom gateway's model list and
    reads CLI sign-ins outside the cached view — an allow-tier reader must
    start no process and touch no network (the v1.334.0 system-map rule)."""
    from ..onboarding.doctor import runtime_checks

    return list(runtime_checks(platform, probe=False) or [])


def read_interrupted_count(platform: Any) -> int:
    from ..core.db import session_scope
    from ..daemon.routes.sessions import interrupted_rows

    with session_scope(platform.engine) as db:
        return len(interrupted_rows(db))


def read_pack_rows(d: Any) -> list[dict[str, Any]]:
    from ..daemon.routes.agents import mcp_server_row

    servers = list(getattr(d.platform.config, "mcp_servers", None) or [])
    return [mcp_server_row(d, s) for s in servers if isinstance(s, dict) and s.get("name")]


def pack_status_row(row: dict[str, Any]) -> dict[str, Any]:
    """The ``pack_status`` projection of a ``GET /mcp/servers`` row: the
    contract's keys, every pack-sourced text masked, NEVER a value (``args``,
    ``env``, ``headers``, ``auth`` and ``cwd`` stay out)."""
    return {
        "name": str(row.get("name") or ""),
        "state": row.get("state"),
        "transport": row.get("transport") or "stdio",
        "tools_loaded": int(row.get("tools_loaded") or 0),
        "tool_names": [str(t) for t in (row.get("tool_names") or [])],
        "last_error": one_line(row.get("last_error")) or None,
        "reason": one_line(row.get("reason")) or "",
        "fix": one_line(row.get("fix")) or "",
        "stderr_tail": one_line(row.get("stderr_tail"), 2000),
        "failures": int(row.get("failures") or 0),
        "env_keys": [str(k) for k in (row.get("env_keys") or [])],
        "secret_keys": [str(k) for k in (row.get("secret_keys") or [])],
        "header_names": [str(k) for k in (row.get("header_names") or [])],
        "auth_set": bool(row.get("auth_set")),
        "quarantined": [str(t) for t in (row.get("quarantined") or [])],
    }


def pack_words(p: dict[str, Any]) -> str:
    """One plain sentence per pack row (the ``pack_status`` projection)."""
    name = p["name"]
    state = p.get("state")
    n = p.get("tools_loaded") or 0
    if state == "ok":
        s = f"{name}: ok, {n} tool{'s' if n != 1 else ''} loaded"
        if p.get("quarantined"):
            s += f"; held back until trusted: {', '.join(p['quarantined'])}"
        return s + "."
    if state == "starting":
        return f"{name}: still starting."
    if state == "failing":
        s = f"{name}: loaded ({n} tools) but its calls fail ({p.get('failures') or 0} so far)"
    elif state == "failed":
        s = f"{name}: did not start"
    else:
        s = f"{name}: not started in this session" if not n else f"{name}: {n} tools loaded"
    s = _sentence(s)
    if p.get("last_error"):
        s += " " + _sentence(f"Last error: {p['last_error']}")
    if p.get("reason"):
        s += " " + _sentence(p["reason"])
    if p.get("fix"):
        s += " " + _sentence(f"Fix: {p['fix']}")
    if p.get("stderr_tail"):
        s += " " + _sentence(f"Last output: {p['stderr_tail'][:LINE_MAX]}")
    return s


# ------------------------------------------------------------- app_health


class AppHealthTool(Tool):
    name = "app_health"
    description = (
        "How Iron Jarvis itself is doing, in plain words plus data: the version, "
        "the default model and whether it is reachable, providers in cooldown, "
        "failing background loops, every tool pack by state with the reason and "
        "the fix, the last recent errors, disk space, interrupted jobs and the "
        "doctor's checks that are not ok. Call it when something is broken, slow "
        "or failing and you need to know what. Read-only; nothing is probed or "
        "restarted."
    )
    input_schema = {"type": "object", "properties": {}}
    reversibility = Reversibility.READONLY
    risk_class = RiskClass.READ
    replay = True
    #: Log lines and pack texts are third-party words: fenced as data.
    returns_untrusted_content = True

    def __init__(self, platform: Any) -> None:
        self._platform = platform

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        data, lines = await asyncio.to_thread(self._collect)
        return ToolResult(ok=True, output="\n".join(lines), data=data)

    # Every section below is its own try; a failure NAMES the section.
    def _collect(self) -> tuple[dict[str, Any], list[str]]:
        from .. import __version__

        platform = self._platform
        d = daemon_deps(platform)
        data: dict[str, Any] = {"version": __version__, "unreadable": []}
        lines: list[str] = [f"Iron Jarvis {__version__}."]

        def broken(section: str, exc: BaseException) -> None:
            data["unreadable"].append({"section": section, "error": _err(exc)})

        # --- the default model (cached availability only) -----------------
        try:
            cfg = platform.config
            provider = str(getattr(cfg, "default_provider", "") or "")
            model = str(getattr(cfg, "default_model", "") or "")
            rows = platform.providers.health()
            data["providers"] = [
                {"provider": r.get("provider"), "class": r.get("class"), "available": bool(r.get("available"))}
                for r in rows
            ]
            data["model"] = self._model_section(provider, model, rows)
            lines.append(data["model"]["words"])
        except Exception as exc:  # noqa: BLE001
            broken("model", exc)
            rows = []

        # --- open circuits (the router's breaker) --------------------------
        try:
            data["circuits_open"] = self._open_circuits(platform, rows)
            if data["circuits_open"]:
                lines.append(
                    "In cooldown: "
                    + ", ".join(f"{c['provider']} (retry in {c['retry_in_s']} s)" for c in data["circuits_open"])
                    + "."
                )
            else:
                lines.append("No provider is in cooldown.")
        except Exception as exc:  # noqa: BLE001
            broken("circuits", exc)

        # --- the diagnostics snapshot: db, secrets key, loops, packs -------
        snap: dict[str, Any] = {}
        try:
            snap = read_diagnostics(d) or {}
            # A failed SELECT 1 stringifies a driver exception (it can name
            # the DB path): masked and folded in data too, not only in words.
            data["db_liveness"] = one_line(snap.get("db_liveness")) or None
            data["secrets_key_valid"] = snap.get("secrets_key_valid")
            lines.append(
                f"Database: {one_line(snap.get('db_liveness')) or 'unknown'}. "
                f"Secrets key: {'valid' if snap.get('secrets_key_valid') else 'NOT valid'}."
            )
        except Exception as exc:  # noqa: BLE001
            broken("diagnostics", exc)
            snap = {}

        try:
            data["failing_loops"] = self._failing_loops(snap.get("background_loops") or {})
            if data["failing_loops"]:
                lines.append(
                    "Failing background loops: "
                    + "; ".join(f"{r['name']} ({r['last_error']})" for r in data["failing_loops"])
                    + "."
                )
            elif "background_loops" in snap:
                lines.append("Background loops: all ok.")
        except Exception as exc:  # noqa: BLE001
            broken("loops", exc)

        try:
            packs = [
                {
                    "name": str(p.get("name") or ""),
                    "state": p.get("state"),
                    "tools_loaded": int(p.get("tools_loaded") or 0),
                    "reason": one_line(p.get("reason")) or "",
                    "fix": one_line(p.get("fix")) or "",
                }
                for p in (snap.get("mcp_servers") or [])
            ]
            data["packs"] = packs
            data["packs_by_state"] = {
                state: [p["name"] for p in packs if p["state"] == state] for state in PACK_STATES
            }
            lines.append(self._packs_words(packs))
        except Exception as exc:  # noqa: BLE001
            broken("packs", exc)

        # --- the recent errors ring --------------------------------------
        try:
            recent = read_recent_errors(RECENT_ERRORS_SHOWN) or []
            data["recent_errors"] = [
                one_line(f"{r.get('ts', '')} {r.get('level', '')} {r.get('logger', '')}: {r.get('message', '')}")
                for r in recent
            ]
            if data["recent_errors"]:
                lines.append(f"Recent errors (last {len(data['recent_errors'])}):")
                lines.extend(f"- {e}" for e in data["recent_errors"])
            else:
                lines.append("No recent errors logged.")
        except Exception as exc:  # noqa: BLE001
            broken("recent errors", exc)

        # --- disk space ----------------------------------------------------
        try:
            usage = shutil.disk_usage(str(platform.config.home))
            pct = round(100.0 * usage.free / usage.total, 1) if usage.total else None
            data["disk"] = {"free_bytes": usage.free, "total_bytes": usage.total, "free_pct": pct}
            lines.append(f"Disk: {_gb(usage.free)} free of {_gb(usage.total)}.")
        except Exception as exc:  # noqa: BLE001
            broken("disk", exc)

        # --- interrupted jobs ---------------------------------------------
        try:
            data["interrupted_sessions"] = read_interrupted_count(platform)
            n = data["interrupted_sessions"]
            lines.append(
                f"Interrupted jobs offered Continue: {n}." if n else "No interrupted jobs."
            )
        except Exception as exc:  # noqa: BLE001
            broken("interrupted jobs", exc)

        # --- the doctor's runtime rows that are not ok ---------------------
        try:
            rows_d = read_doctor_rows(platform)
            data["doctor_not_ok"] = [
                {
                    "name": str(r.get("name") or ""),
                    "detail": one_line(r.get("detail")),
                    "fix": one_line(r.get("fix")),
                    "level": str(r.get("level") or ""),
                }
                for r in rows_d
                if not r.get("ok")
            ]
            if data["doctor_not_ok"]:
                lines.append(f"Doctor: {len(data['doctor_not_ok'])} check(s) not ok:")
                for r in data["doctor_not_ok"]:
                    fix = f" Fix: {r['fix']}" if r["fix"] else ""
                    lines.append(f"- {r['name']}: {r['detail']}{fix}")
            else:
                lines.append(f"Doctor: all {len(rows_d)} runtime checks ok.")
        except Exception as exc:  # noqa: BLE001
            broken("doctor", exc)

        if data["unreadable"]:
            lines.append(
                "Could not read: "
                + "; ".join(f"{u['section']} ({u['error']})" for u in data["unreadable"])
                + "."
            )
        return data, lines

    @staticmethod
    def _model_section(provider: str, model: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
        by_name = {str(r.get("provider") or ""): r for r in rows}
        out: dict[str, Any] = {"provider": provider, "model": model, "reachable": None, "detail": ""}
        label = f"{provider} / {model}" if model else provider
        if provider == "mock":
            out["reachable"] = True
            out["detail"] = "demo replies; no real model chosen"
            out["words"] = "Model: demo replies (mock); no real model has been chosen."
            return out
        if provider == "auto":
            live = [n for n, r in by_name.items() if r.get("available") and r.get("class") != "mock"]
            out["reachable"] = bool(live)
            out["detail"] = ", ".join(live)
            out["words"] = (
                f"Model: Auto, choosing among {', '.join(live)}." if live
                else "Model: Auto, but no real provider is reachable right now."
            )
            return out
        row = by_name.get(provider)
        if row is None:
            out["words"] = f"Model: {label}; its provider is not known to this install."
            return out
        out["reachable"] = bool(row.get("available"))
        if out["reachable"]:
            via = row.get("inherited_from")
            out["detail"] = f"served through {via}" if via else ""
            out["words"] = f"Model: {label} is reachable" + (f" ({out['detail']})" if via else "") + "."
            return out
        fix = row.get("sign_in_fix") or row.get("detail") or ""
        out["detail"] = one_line(fix)
        out["words"] = f"Model: {label} is NOT reachable." + (f" {out['detail']}" if out["detail"] else "")
        return out

    @staticmethod
    def _open_circuits(platform: Any, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        health = getattr(getattr(platform, "router", None), "health", None)
        if health is None or not callable(getattr(health, "circuit", None)):
            return []
        out = []
        for r in rows:
            name = str(r.get("provider") or "")
            if not name:
                continue
            c = health.circuit(name)
            if c.get("open"):
                out.append({"provider": name, "retry_in_s": int(c.get("retry_in_s") or 0)})
        return out

    @staticmethod
    def _failing_loops(loops: dict[str, Any]) -> list[dict[str, Any]]:
        out = []
        for name, row in (loops or {}).items():
            if not isinstance(row, dict):
                continue
            err = row.get("last_error") or row.get("error")
            if row.get("ok") is False or err:
                out.append({"name": str(name), "last_error": one_line(err) or "failed", "at": row.get("at")})
        return out

    @staticmethod
    def _packs_words(packs: list[dict[str, Any]]) -> str:
        if not packs:
            return "No tool packs configured."
        counts = []
        for state in PACK_STATES:
            n = sum(1 for p in packs if p["state"] == state)
            if n:
                counts.append(f"{n} {state}")
        unknown = sum(1 for p in packs if p["state"] not in PACK_STATES)
        if unknown:
            counts.append(f"{unknown} not started")
        s = f"Packs: {', '.join(counts)}."
        for p in packs:
            if p["state"] in ("failed", "failing"):
                why = p["reason"] or ("its calls fail" if p["state"] == "failing" else "did not start")
                s += " " + _sentence(f"{p['name']} {p['state']}: {why}")
                if p["fix"]:
                    s += " " + _sentence(f"Fix: {p['fix']}")
        if any(p["state"] in ("failed", "failing") for p in packs):
            s += f" Details with pack_status; fix it under {TOOLS_PAGE}."
        return s


# ------------------------------------------------------------ pack_status


class PackStatusTool(Tool):
    name = "pack_status"
    description = (
        "The state of one tool pack (an MCP server) or of all of them: whether it "
        "started, how many tools it loaded, its last error, the reason and the fix, "
        "the child's last output, call failures, and the NAMES of its env keys and "
        "secrets (never a value). Read-only."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "The pack's name as listed under Settings > Tools. Omit for every pack.",
            }
        },
    }
    reversibility = Reversibility.READONLY
    risk_class = RiskClass.READ
    replay = True
    returns_untrusted_content = True

    def __init__(self, platform: Any) -> None:
        self._platform = platform

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        name = str(args.get("name") or "").strip()
        try:
            rows = await asyncio.to_thread(self._rows)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(ok=False, error=f"could not read the pack list: {_err(exc)}")
        packs = [pack_status_row(r) for r in rows]
        if name:
            packs = [p for p in packs if p["name"] == name]
            if not packs:
                known = ", ".join(sorted(p["name"] for p in (pack_status_row(r) for r in rows))) or "none"
                return ToolResult(
                    ok=False,
                    error=f"no pack named {name!r}; configured packs: {known}",
                    data={"packs": [], "known": known},
                )
        if not packs:
            return ToolResult(ok=True, output="No tool packs are configured.", data={"packs": []})
        return ToolResult(ok=True, output="\n".join(pack_words(p) for p in packs), data={"packs": packs})

    def _rows(self) -> list[dict[str, Any]]:
        return read_pack_rows(daemon_deps(self._platform))


# ----------------------------------------------------------- pack_restart


class PackRestartTool(Tool):
    name = "pack_restart"
    description = (
        "Restart ONE tool pack (an MCP server): its running child is stopped, "
        "the pack is started again and its tools registered afresh. The same "
        "thing as Retry on Settings > Tools. Use it for a pack that did not "
        "start or whose calls keep failing. The user approves first."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "The pack's name as listed under Settings > Tools."}
        },
        "required": ["name"],
    }
    #: A restart kills and respawns a child process; nothing can put the old
    #: one back. Not replay-safe for the same reason. The risk class keeps
    #: the fail-safe default: the tool does not declare its way down.
    reversibility = Reversibility.IRREVERSIBLE
    replay = False
    returns_untrusted_content = True

    def __init__(self, platform: Any) -> None:
        self._platform = platform

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from ..mcp.tools import reload_pack

        name = str(args.get("name") or "").strip()
        if not name:
            return ToolResult(ok=False, error="pack_restart needs the pack's name")
        platform = self._platform
        try:
            r = await asyncio.to_thread(reload_pack, platform, name, home=platform.config.home)
        except KeyError:
            servers = [str(s.get("name")) for s in (getattr(platform.config, "mcp_servers", None) or []) if isinstance(s, dict) and s.get("name")]
            return ToolResult(
                ok=False,
                error=f"no pack named {name!r}; configured packs: {', '.join(sorted(servers)) or 'none'}",
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(ok=False, error=f"{name} could not be restarted: {_err(exc)}")
        data = {
            "name": name,
            "ok": bool(r.get("ok")),
            "tools_loaded": int(r.get("tools_loaded") or 0),
            "last_error": one_line(r.get("last_error")) or None,
            "reason": one_line(r.get("reason")) or "",
            "fix": one_line(r.get("fix")) or "",
            "stderr_tail": one_line(r.get("stderr_tail"), 2000),
        }
        if data["ok"]:
            n = data["tools_loaded"]
            return ToolResult(ok=True, output=f"Restarted {name}: {n} tool{'s' if n != 1 else ''} loaded.", data=data)
        s = f"{name} did not start"
        if data["last_error"]:
            s += f": {data['last_error']}"
        s = _sentence(s)
        if data["reason"]:
            s += " " + _sentence(data["reason"])
        if data["fix"]:
            s += " " + _sentence(f"Fix: {data['fix']}")
        if data["stderr_tail"]:
            s += " " + _sentence(f"Last output: {data['stderr_tail'][:LINE_MAX]}")
        s += f" The user can fix it under {TOOLS_PAGE}."
        return ToolResult(ok=False, error=s, data=data)


# -------------------------------------------------------- provider_rescan


class ProviderRescanTool(Tool):
    name = "provider_rescan"
    description = (
        "Look again for model providers on this PC: re-detect installed CLIs "
        "(Claude Code, Codex, Grok, Ollama) and re-check their sign-in, then "
        "refresh the model list. The same thing as Re-detect on Settings > "
        "Connections. Says which providers are available now and what changed. "
        "The user approves first."
    )
    input_schema = {"type": "object", "properties": {}}
    #: A rescan re-probes sign-ins and rebuilds the catalog; not undoable,
    #: not replay-safe (a probe is a spawn). Risk class: the fail-safe default.
    reversibility = Reversibility.IRREVERSIBLE
    replay = False

    def __init__(self, platform: Any) -> None:
        self._platform = platform

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            data = await asyncio.to_thread(self._rescan)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(ok=False, error=f"the rescan failed: {_err(exc)}")
        now = data["available_now"]
        words = [f"Available now: {', '.join(now) if now else 'no provider'}."]
        if data["changed"]:
            words.append(
                "Changed: "
                + ", ".join(
                    f"{c['provider']} ({'now available' if c['now'] else 'no longer available'})"
                    for c in data["changed"]
                )
                + "."
            )
        else:
            words.append("Nothing changed.")
        off = [r for r in data["detected"] if not r["available"] and r.get("detail")]
        for r in off:
            words.append(f"{r['provider']}: {r['detail']}")
        if not now:
            words.append(f"Connect a provider under {CONNECTIONS_PAGE}.")
        return ToolResult(ok=True, output="\n".join(words), data=data)

    def _rescan(self) -> dict[str, Any]:
        from ..daemon.routes.connections import rescan_providers

        platform = self._platform
        d = daemon_deps(platform)
        before = self._availability(platform)
        answer = rescan_providers(d) or {}
        after = self._availability(platform)
        detected = [
            {
                "provider": str(r.get("provider") or ""),
                "name": str(r.get("name") or ""),
                "model": str(r.get("model") or ""),
                "available": bool(r.get("available")),
                "source": str(r.get("source") or ""),
                "detail": one_line(r.get("detail")),
            }
            for r in (answer.get("detected") or [])
            if isinstance(r, dict)
        ]
        changed = [
            {"provider": name, "was": before.get(name), "now": after.get(name)}
            for name in sorted(set(before) | set(after))
            if before.get(name) != after.get(name)
        ]
        available_now = sorted(n for n, ok in after.items() if ok)
        return {"detected": detected, "available_now": available_now, "changed": changed}

    @staticmethod
    def _availability(platform: Any) -> dict[str, bool]:
        try:
            return {
                str(r.get("provider") or ""): bool(r.get("available"))
                for r in platform.providers.health()
                if r.get("provider") and r.get("class") != "mock"
            }
        except Exception:  # noqa: BLE001 — a status fault never fails the rescan
            return {}


# ------------------------------------------------------------- run_doctor


class RunDoctorTool(Tool):
    name = "run_doctor"
    description = (
        "Run the app's runtime checks (a model is connected, CLIs are signed in, "
        "the secrets key decrypts, the database passes its integrity check, every "
        "tool pack started, backups and mirrors work) and list each row with its "
        "fix. Read-only; the Doctor on Settings' rows, except the custom gateway's "
        "model check, which calls the network and runs only from Settings."
    )
    input_schema = {"type": "object", "properties": {}}
    reversibility = Reversibility.READONLY
    risk_class = RiskClass.READ
    replay = True
    returns_untrusted_content = True

    def __init__(self, platform: Any) -> None:
        self._platform = platform

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            rows = await asyncio.to_thread(read_doctor_rows, self._platform)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(ok=False, error=f"the doctor could not run: {_err(exc)}")
        checks = [
            {
                "name": str(r.get("name") or ""),
                "ok": bool(r.get("ok")),
                "detail": one_line(r.get("detail")),
                "fix": one_line(r.get("fix")),
                "level": str(r.get("level") or ""),
            }
            for r in rows
        ]
        not_ok = [c for c in checks if not c["ok"]]
        lines = [f"{len(checks)} runtime checks, {len(not_ok)} not ok."]
        for c in checks:
            mark = "ok" if c["ok"] else "NOT OK"
            fix = f" Fix: {c['fix']}" if (not c["ok"] and c["fix"]) else ""
            lines.append(f"- {c['name']} [{mark}, {c['level']}]: {c['detail']}{fix}")
        return ToolResult(ok=True, output="\n".join(lines), data={"checks": checks, "not_ok": not_ok})


def selfcare_tools(platform: Any) -> list[Tool]:
    """The five self-care tools, bound to ``platform`` (a ``_LazyPlatform``
    at build time — the registry fills before the Platform object exists)."""
    return [
        AppHealthTool(platform),
        PackStatusTool(platform),
        PackRestartTool(platform),
        ProviderRescanTool(platform),
        RunDoctorTool(platform),
    ]
