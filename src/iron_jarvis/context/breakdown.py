"""What fills a coding session's conversation (v1.415.0, the Session tab's
Context drop-down) — ONE shape for both coding-session views.

Chat › Code (Iron Jarvis's own harness) builds its prompt itself, so its
breakdown is EXACT from the daemon side: :func:`harness_breakdown` counts the
very strings the turn sent, with the planner's own estimator
(:func:`context.budget.estimate_tokens`) and the SAME message rule
``chat_turn._apply_compaction`` measures by (content + 4 per message), so the
parts add up to the turn's ``context.tokens`` exactly — the "System prompt"
part is whatever of the finished prompt no named part claims. Tool
descriptions are sent beside the prompt and the compaction figure has never
counted them; they are listed apart ("not counted in the percent"), never
folded in, so the percent stays the one the Compact offer keys off.

Build › Code drives the user's REAL Claude Code, whose own ``/context``
numbers come back from its ``get_context_usage`` control request (measured,
claude 2.1.316: answered with no model call, signed in or not);
:func:`claude_breakdown` puts them in the same shape in plain words. A tool
that reports only a total (Codex, OpenCode, Pi) gets no parts — the view says
so; a part a tool does not report is never invented.

Shape (every key optional except ``used``/``window``/``percent``)::

    {used, window, percent, source: "app" | "tool",
     parts:  [{key, label, tokens, kind: "used" | "buffer" | "free"}],
     extra:  [{key, label, tokens}]           # sent, but not in the percent
     lists:  [{key, label, count, items: [{name, detail, tokens}]}],
     auto_compact: {enabled, at, left, percent}}
"""

from __future__ import annotations

import json
import os
from typing import Any, Iterable

from .budget import estimate_tokens

#: Iron Jarvis's own parts, in the order the drop-down lists them.
HARNESS_LABELS: dict[str, str] = {
    "messages": "Messages",
    "rules": "Instruction files",
    "memory": "Memory and profile",
    "project": "Project and folder",
    "system": "System prompt",
    "skills": "Skills",
    "attachments": "Files you attached",
    "summary": "Summary of earlier messages",
}
BUFFER_LABEL = "Kept free for the automatic summary"
FREE_LABEL = "Free space"
BUILTIN_TOOLS_LABEL = "Built-in tools"
EXTENSION_TOOLS_LABEL = "Extension tools"
#: How many items a list keeps (the count is always the whole).
MAX_ITEMS = 60


def _int(v: Any) -> int | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return int(v) if v >= 0 else None


def _pct(part: int, window: int) -> int:
    return max(0, min(100, int(round(100 * part / window)))) if window > 0 else 0


def _message_tokens(messages: Iterable[Any], ratio: "float | None" = None) -> int:
    """``chat_turn._apply_compaction``'s own rule: content + 4 per message.
    ``ratio``: the answering model's measured characters per token, where a
    caller counts the instructions beside these messages with it (an agent
    step, ``agents.run_facts.note_step``); None is the planner's default."""
    total = 0
    for m in messages or []:
        content = m.get("content") if isinstance(m, dict) else getattr(m, "content", "")
        total += estimate_tokens(content or "", ratio) + 4
    return total


def _server_of(tool_name: str) -> str:
    """``mcp__<server>__<tool>`` -> ``<server>`` ("" for a built-in)."""
    if not tool_name.startswith("mcp__"):
        return ""
    rest = tool_name[5:]
    return rest.split("__", 1)[0] if "__" in rest else rest


def auto_compact(used: int, window: int, auto_at: float, enabled: bool) -> dict[str, Any]:
    """Where the automatic summary starts, and how far away it is."""
    at = int(window * auto_at)
    return {
        "enabled": bool(enabled),
        "at": at,
        "left": max(0, at - used),
        "percent": int(round(auto_at * 100)),
    }


def harness_breakdown(
    *,
    system: str,
    messages: Iterable[Any],
    window: int,
    pieces: dict[str, Iterable[str]],
    tool_specs: Iterable[dict[str, Any]] = (),
    rule_files: Iterable[str] = (),
    auto_at: float,
    enabled: bool,
) -> dict[str, Any] | None:
    """The breakdown of ONE harness turn. ``system`` and ``messages`` are what
    the turn's context report measured (after any summary was applied);
    ``pieces`` maps a part key to the strings of that part appended to the
    prompt (a string not found in ``system`` is not counted — it was not
    sent). Never raises; None when there is no window."""
    try:
        window = int(window or 0)
        if window <= 0:
            return None
        sys_text = system or ""
        counted: dict[str, int] = {}
        # v1.415.0 review: one string is counted ONCE, whichever part names it
        # first (a brain base that cannot be used renders ``block`` ==
        # ``without_tools()``; both are handed in, and the memory part was
        # counted twice while "System prompt" shrank by the same amount).
        taken: set[str] = set()
        for key, texts in (pieces or {}).items():
            if key not in HARNESS_LABELS or key in ("messages", "system"):
                continue
            n = 0
            for t in texts or ():
                if isinstance(t, str) and t and t not in taken and t in sys_text:
                    taken.add(t)
                    n += estimate_tokens(t)
            if n:
                counted[key] = counted.get(key, 0) + n
        whole = estimate_tokens(sys_text)
        named = sum(counted.values())
        if named > whole:
            # The estimator is not additive to the token: never let the named
            # parts claim more than the prompt holds.
            scale = whole / named if named else 0
            counted = {k: int(v * scale) for k, v in counted.items()}
            named = sum(counted.values())
        counted["system"] = max(0, whole - named)
        counted["messages"] = _message_tokens(messages)
        used = sum(counted.values())
        parts = [
            {"key": k, "label": HARNESS_LABELS[k], "tokens": counted[k], "kind": "used"}
            for k in HARNESS_LABELS
            if counted.get(k)
        ]
        comp = auto_compact(used, window, auto_at, enabled)
        room = max(0, window - used)
        buffer = min(room, max(0, window - comp["at"])) if enabled else 0
        if buffer:
            parts.append({"key": "buffer", "label": BUFFER_LABEL, "tokens": buffer, "kind": "buffer"})
        free = max(0, room - buffer)
        parts.append({"key": "free", "label": FREE_LABEL, "tokens": free, "kind": "free"})

        builtin = ext = 0
        servers: dict[str, list[int]] = {}
        for spec in tool_specs or ():
            if not isinstance(spec, dict):
                continue
            try:
                n = estimate_tokens(json.dumps(spec, ensure_ascii=False, sort_keys=True))
            except (TypeError, ValueError):
                continue
            server = _server_of(str(spec.get("name") or ""))
            if server:
                ext += n
                row = servers.setdefault(server, [0, 0])
                row[0] += 1
                row[1] += n
            else:
                builtin += n
        extra = []
        if builtin:
            extra.append({"key": "tools", "label": BUILTIN_TOOLS_LABEL, "tokens": builtin})
        if ext:
            extra.append({"key": "extension_tools", "label": EXTENSION_TOOLS_LABEL, "tokens": ext})
        lists = []
        names = [str(n) for n in rule_files or () if str(n or "").strip()]
        if names:
            lists.append({
                "key": "rules", "label": "Instruction files", "count": len(names),
                "items": [{"name": n[:200]} for n in names[:MAX_ITEMS]],
            })
        if servers:
            lists.append({
                "key": "extension_tools", "label": EXTENSION_TOOLS_LABEL,
                "count": sum(c for c, _ in servers.values()),
                "items": [
                    {"name": s[:120], "detail": f"{c} tool{'' if c == 1 else 's'}", "tokens": t}
                    for s, (c, t) in sorted(servers.items(), key=lambda kv: -kv[1][1])[:MAX_ITEMS]
                ],
            })
        out: dict[str, Any] = {
            "source": "app",
            "used": used,
            "window": window,
            "percent": _pct(used, window),
            "parts": parts,
            "auto_compact": comp,
        }
        if extra:
            out["extra"] = extra
        if lists:
            out["lists"] = lists
        return out
    except Exception:  # noqa: BLE001 — a breakdown never breaks a turn
        return None


# --------------------------------------------------------------------------- #
# What a reply was sent, as its own record keeps it (the intelligence plan,
# release 6): a plain chat's "What is filling this chat"
# --------------------------------------------------------------------------- #

#: The record's shape version (a reader skips a record it does not know).
RECORD_VERSION = 1
#: The parts a record may hold, in the order a list shows them.
RECORD_PARTS: tuple[str, ...] = (
    "messages", "attachments", "memory", "project", "rules", "skills", "summary", "system",
)
#: A PLAIN CHAT's words for the same parts (VOCABULARY.md: no "tokens", no
#: "context", no "prompt"). A coding session keeps :data:`HARNESS_LABELS`.
CHAT_LABELS: dict[str, str] = {
    "messages": "Your messages and the replies",
    "attachments": "Files you attached",
    "memory": "What Iron Jarvis remembered",
    "project": "Your project and folder",
    "rules": "Instruction files",
    "skills": "Skills",
    "summary": "Summary of earlier messages",
    "system": "Iron Jarvis's own instructions",
}
CHAT_FREE_LABEL = "Room left"
CHAT_TOOLS_LABEL = "Tools it was given"
#: The fold under it: Iron Jarvis's own tools, and each extension by name.
CHAT_TOOL_ORIGINS_LABEL = "Where they came from"
CHAT_BUILTIN_TOOLS = "Iron Jarvis's own tools"
#: The most extensions one record names (the rest are summed under one name).
MAX_ORIGINS = 24
OTHER_EXTENSIONS = "other extensions"


def over_room(record: Any) -> bool:
    """True when a record's chat HOLDS MORE THAN THE ROOM the model has (its
    saved percent is above 100, or its parts add up to more than the room).
    ONE decision for the page's list and the chat's answer: in that state
    neither says a share of the room for a part (it would be above 100);
    both say each part's share of what the chat HOLDS, which adds up to 100.
    False for a record with no room; never raises."""
    try:
        window = int(record.get("window") or 0)
        if window <= 0:
            return False
        pct = record.get("percent")
        return (pct is not None and int(pct) > 100) or int(record.get("used") or 0) > window
    except Exception:  # noqa: BLE001
        return False


def tool_origins(specs: Iterable[Any], ratio: "float | None" = None) -> "dict[str, Any] | None":
    """Where one model call's tool definitions came from, by SIZE and COUNT:
    ``{"builtin": [count, tokens], "extensions": {name: [count, tokens]}}``.
    Extension NAMES only (the ``mcp__<name>__`` part of a tool's name): never
    a definition's text. Each size is an ESTIMATE of that definition as sorted
    JSON. None for no tools; never raises."""
    try:
        builtin = [0, 0]
        ext: dict[str, list[int]] = {}
        seen = False
        for spec in specs or ():
            if not isinstance(spec, dict):
                continue
            fn = spec.get("function")
            name = str(spec.get("name") or (fn.get("name") if isinstance(fn, dict) else "") or "")
            try:
                n = estimate_tokens(json.dumps(spec, ensure_ascii=False, sort_keys=True, default=str), ratio)
            except (TypeError, ValueError):
                continue
            seen = True
            server = _server_of(name)
            row = ext.setdefault(server[:120], [0, 0]) if server else builtin
            row[0] += 1
            row[1] += n
        if not seen:
            return None
        if len(ext) > MAX_ORIGINS:
            ranked = sorted(ext.items(), key=lambda kv: -kv[1][1])
            rest = [sum(v[0] for _, v in ranked[MAX_ORIGINS:]), sum(v[1] for _, v in ranked[MAX_ORIGINS:])]
            ext = dict(ranked[:MAX_ORIGINS])
            ext[OTHER_EXTENSIONS] = rest
        return {"builtin": builtin, "extensions": ext}
    except Exception:  # noqa: BLE001 — a record never breaks a reply
        return None


def sent_record(
    *,
    system: str,
    messages: Iterable[Any],
    window: int,
    pieces: dict[str, Iterable[str]],
    window_known: bool = True,
    percent: Any = None,
    left_out: Any = None,
) -> "dict[str, Any] | None":
    """THE RECORD of what one reply (or one agent run) was sent when it began:
    the size of each part, the room the model has, and how full that is.
    Counted exactly the way :func:`harness_breakdown` counts a coding
    session's (the same strings, the same estimator), so the parts add up to
    the fullness the chat's meter shows.

    Numbers only. A part that holds nothing, or was not handed in, is ABSENT
    (never 0). ``window`` 0 (nothing knows the room) keeps the sizes and no
    share. ``left_out``: earlier messages the PLANNER did not send so the
    reply fits (the context report's ``dropped``; the fit made for each
    model call, ``chat_turn._fit_turn_transcript``, may leave out more, so
    read it as "at least"). When it is above 0 the "messages" size is what
    the chat holds, not what was sent. None when nothing could be measured;
    never raises."""
    try:
        b = harness_breakdown(
            system=system, messages=messages, window=max(1, int(window or 0)),
            pieces=pieces, auto_at=1.0, enabled=False,
        )
        if not b:
            return None
        parts = {
            p["key"]: int(p["tokens"]) for p in b["parts"]
            if p.get("kind") == "used" and p.get("key") in RECORD_PARTS and int(p.get("tokens") or 0) > 0
        }
        if not parts:
            return None
        out: dict[str, Any] = {"v": RECORD_VERSION, "parts": parts, "used": int(b["used"])}
        win = int(window or 0)
        if win > 0:
            out["window"] = win
            out["window_known"] = bool(window_known)
            pct = _int(percent)
            out["percent"] = pct if pct is not None else int(round(100 * b["used"] / win))
        n = _int(left_out)
        if n:
            out["left_out"] = n
        return out
    except Exception:  # noqa: BLE001 — a record never breaks a reply
        return None


def decode_record(raw: Any) -> "dict[str, Any] | None":
    """A saved record (the row's ``parts_json``, text or dict) as a clean
    dict, or None: an older row, a version this code does not know, junk."""
    try:
        r = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
        if not isinstance(r, dict) or r.get("v") != RECORD_VERSION:
            return None
        parts_in = r.get("parts") if isinstance(r.get("parts"), dict) else {}
        parts = {k: n for k in RECORD_PARTS if (n := _int(parts_in.get(k)))}
        out: dict[str, Any] = {"v": RECORD_VERSION, "parts": parts, "used": _int(r.get("used")) or sum(parts.values())}
        if _int(r.get("window")):
            out["window"] = _int(r.get("window"))
            out["window_known"] = r.get("window_known") is not False
            if _int(r.get("percent")) is not None:
                out["percent"] = _int(r.get("percent"))
        if _int(r.get("left_out")):
            out["left_out"] = _int(r.get("left_out"))
        if r.get("kind") == "code":
            out["kind"] = "code"
        tools = r.get("tools")
        if isinstance(tools, dict):
            t: dict[str, Any] = {}
            b = tools.get("builtin")
            if isinstance(b, list) and len(b) == 2 and _int(b[0]) is not None and _int(b[1]) is not None:
                t["builtin"] = [_int(b[0]), _int(b[1])]
            ext = {}
            for name, row in (tools.get("extensions") or {}).items() if isinstance(tools.get("extensions"), dict) else ():
                if isinstance(row, list) and len(row) == 2 and _int(row[0]) is not None and _int(row[1]) is not None:
                    ext[_clip(name, 120)] = [_int(row[0]), _int(row[1])]
            if ext:
                t["extensions"] = ext
            if t:
                out["tools"] = t
        if _int(r.get("cache_input")) is not None:
            out["cache_input"] = _int(r.get("cache_input"))
        sent = r.get("sent")
        if isinstance(sent, dict):
            s = {k: n for k in ("system", "messages", "tools", "steps") if (n := _int(sent.get(k))) is not None}
            if s:
                out["sent"] = s
        return out if (parts or out.get("tools") or out.get("sent")) else None
    except Exception:  # noqa: BLE001
        return None


def chat_reading(record: Any, tool_names: Iterable[Any] = ()) -> "dict[str, Any] | None":
    """A saved record in the Context drop-down's own shape (the module
    docstring's), with a PLAIN CHAT's words: the list behind the fullness
    meter, "What is filling this chat". ``tool_names`` are the reply's tool
    names (the row's ``tools_sent_json``). The tools are listed apart, as in
    a coding session: they are sent with each message and are not part of
    the percent. None when the record names no room (no share can be said)."""
    try:
        r = decode_record(record)
        if not r or not r.get("window"):
            return None
        window, used = int(r["window"]), int(r["used"])
        parts = [
            {"key": k, "label": CHAT_LABELS[k], "tokens": r["parts"][k], "kind": "used"}
            for k in RECORD_PARTS if r["parts"].get(k)
        ]
        parts.append({"key": "free", "label": CHAT_FREE_LABEL, "tokens": max(0, window - used), "kind": "free"})
        out: dict[str, Any] = {
            "source": "app", "used": used, "window": window,
            "percent": r["percent"] if r.get("percent") is not None else _pct(used, window),
            "parts": parts,
        }
        if over_room(r):
            # The chat holds more than the room: the page says each part's
            # share of what the chat holds (never a share above 100).
            out["over"] = True
        tools = r.get("tools") or {}
        builtin = tools.get("builtin") or [0, 0]
        ext = tools.get("extensions") or {}
        total = int(builtin[1]) + sum(int(v[1]) for v in ext.values())
        count = int(builtin[0]) + sum(int(v[0]) for v in ext.values())
        if total and count:
            out["extra"] = [{"key": "tools", "label": CHAT_TOOLS_LABEL, "tokens": total}]
            items: list[dict[str, Any]] = []
            names = [str(n) for n in tool_names or () if isinstance(n, str) and n]
            own = [n for n in names if not _server_of(n)]
            if builtin[0]:
                items.append({
                    "name": CHAT_BUILTIN_TOOLS,
                    "detail": f"{builtin[0]} tool{'' if builtin[0] == 1 else 's'}",
                    "tokens": int(builtin[1]),
                })
            for s, (c, t) in sorted(ext.items(), key=lambda kv: -kv[1][1])[:MAX_ITEMS]:
                items.append({"name": s[:120], "detail": f"{c} tool{'' if c == 1 else 's'}", "tokens": int(t)})
            # ``count`` is the rows of the fold (a list's count above its
            # items reads "and N more"); how many tools is in each row.
            out["lists"] = [{"key": "tools", "label": CHAT_TOOL_ORIGINS_LABEL, "count": len(items), "items": items}]
            if own:
                out["tool_names"] = own[:MAX_ITEMS]
        return out
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------- #
# Claude Code's own /context (get_context_usage)
# --------------------------------------------------------------------------- #

#: Claude Code's category ids -> plain words (an id not here keeps Claude
#: Code's own name).
CLAUDE_LABELS: dict[str, str] = {
    "messages": "Messages",
    "memory_files": "Instruction files",
    "system_prompt": "System prompt",
    "system_tools": BUILTIN_TOOLS_LABEL,
    "mcp_tools": EXTENSION_TOOLS_LABEL,
    "mcp_instructions": "Extension instructions",
    "custom_agents": "Your agents",
    "skills": "Skills",
    "autocompact_buffer": BUFFER_LABEL,
    "compact_buffer": BUFFER_LABEL,
    "free_space": FREE_LABEL,
    "system_tools_deferred": "Built-in tools (loaded when needed)",
    "mcp_tools_deferred": "Extension tools (loaded when needed)",
}


def _clip(v: Any, n: int) -> str:
    return " ".join(str(v or "").split())[:n]


def claude_breakdown(resp: Any) -> dict[str, Any] | None:
    """Claude Code's ``get_context_usage`` answer in the shared shape; None
    when it carries no window. Never raises."""
    try:
        if not isinstance(resp, dict):
            return None
        window = _int(resp.get("maxTokens")) or 0
        used = _int(resp.get("totalTokens"))
        if window <= 0 or used is None:
            return None
        parts: list[dict[str, Any]] = []
        extra: list[dict[str, Any]] = []
        for c in resp.get("categories") or []:
            if not isinstance(c, dict):
                continue
            tokens = _int(c.get("tokens"))
            if not tokens:
                continue
            cid = _clip(c.get("id"), 60)
            label = CLAUDE_LABELS.get(cid) or _clip(c.get("name"), 60) or cid
            if not label:
                continue
            kind = str(c.get("kind") or "")
            if c.get("isDeferred") or kind == "deferred":
                extra.append({"key": cid, "label": label, "tokens": tokens})
                continue
            if kind not in ("used", "buffer", "free"):
                kind = "free" if cid == "free_space" else "buffer" if cid.endswith("buffer") else "used"
            parts.append({"key": cid, "label": label, "tokens": tokens, "kind": kind})
        pct = _int(resp.get("percentage"))
        out: dict[str, Any] = {
            "source": "tool",
            "used": used,
            "window": window,
            "percent": max(0, min(100, pct)) if pct is not None else _pct(used, window),
            "parts": parts,
        }
        if extra:
            out["extra"] = extra
        lists: list[dict[str, Any]] = []
        files = [f for f in resp.get("memoryFiles") or [] if isinstance(f, dict) and f.get("path")]
        if files:
            lists.append({
                "key": "rules", "label": "Instruction files", "count": len(files),
                "items": [
                    {
                        "name": os.path.basename(str(f.get("path")).replace("\\", "/").rstrip("/"))[:200] or "file",
                        "detail": _clip(f.get("type"), 40),
                        "path": str(f.get("path"))[:1000],
                        **({"tokens": t} if (t := _int(f.get("tokens"))) is not None else {}),
                    }
                    for f in files[:MAX_ITEMS]
                ],
            })
        tools = [t for t in resp.get("mcpTools") or [] if isinstance(t, dict)]
        if tools:
            servers: dict[str, list[int]] = {}
            for t in tools:
                s = _clip(t.get("serverName"), 120) or "extension"
                row = servers.setdefault(s, [0, 0])
                row[0] += 1
                row[1] += _int(t.get("tokens")) or 0
            lists.append({
                "key": "extension_tools", "label": EXTENSION_TOOLS_LABEL, "count": len(tools),
                "items": [
                    {"name": s, "detail": f"{c} tool{'' if c == 1 else 's'}", "tokens": n}
                    for s, (c, n) in sorted(servers.items(), key=lambda kv: -kv[1][1])[:MAX_ITEMS]
                ],
            })
        skills = resp.get("skills") if isinstance(resp.get("skills"), dict) else None
        if skills and _int(skills.get("totalSkills")):
            fm = [s for s in skills.get("skillFrontmatter") or [] if isinstance(s, dict) and s.get("name")]
            lists.append({
                "key": "skills", "label": "Skills", "count": _int(skills.get("totalSkills")) or len(fm),
                "items": [
                    {"name": _clip(s.get("name"), 120), **({"tokens": n} if (n := _int(s.get("tokens"))) is not None else {})}
                    for s in fm[:MAX_ITEMS]
                ],
            })
        if lists:
            out["lists"] = lists
        at = _int(resp.get("autoCompactThreshold"))
        if resp.get("isAutoCompactEnabled") is True and at:
            out["auto_compact"] = {
                "enabled": True, "at": at, "left": max(0, at - used), "percent": _pct(at, window),
            }
        elif resp.get("isAutoCompactEnabled") is False:
            out["auto_compact"] = {"enabled": False}
        model = _clip(resp.get("model"), 120)
        if model:
            out["model"] = model
        return out
    except Exception:  # noqa: BLE001
        return None


#: v1.415.0 review: a driver's answer meaning "the tool ANSWERED: it does not
#: know right now" (Pi right after it compacts) — unlike None ("could not
#: ask"), it retires any older reading kept for the session.
NOT_KNOWN_NOW: dict[str, Any] = {"not_known_now": True}


def is_not_known_now(raw: Any) -> bool:
    return isinstance(raw, dict) and raw.get("not_known_now") is True


def total_only(used: Any, window: Any, *, at: Any = None) -> dict[str, Any] | None:
    """A tool that says only how full: the total, no parts."""
    u, w = _int(used), _int(window)
    if u is None or not w:
        return None
    out: dict[str, Any] = {"source": "tool", "used": u, "window": w, "percent": _pct(u, w), "parts": []}
    if isinstance(at, str) and at:
        out["at"] = at
    return out


def clean(raw: Any) -> dict[str, Any] | None:
    """A breakdown as it may be STORED (the reply line keeps it) or sent: only
    the shape's own keys, whole tokens, words cut. None when it holds no
    window."""
    if not isinstance(raw, dict):
        return None
    window, used, pct = _int(raw.get("window")), _int(raw.get("used")), _int(raw.get("percent"))
    if not window or used is None:
        return None
    out: dict[str, Any] = {
        "source": "tool" if raw.get("source") == "tool" else "app",
        "used": used, "window": window,
        "percent": max(0, min(100, pct)) if pct is not None else _pct(used, window),
    }

    def rows(v: Any, with_kind: bool) -> list[dict[str, Any]]:
        got = []
        for r in v or []:
            if not isinstance(r, dict):
                continue
            n = _int(r.get("tokens"))
            label = _clip(r.get("label"), 80)
            if n is None or not label:
                continue
            row: dict[str, Any] = {"key": _clip(r.get("key"), 60), "label": label, "tokens": n}
            if with_kind:
                k = r.get("kind")
                row["kind"] = k if k in ("used", "buffer", "free") else "used"
            got.append(row)
        return got[:40]

    out["parts"] = rows(raw.get("parts"), True)
    extra = rows(raw.get("extra"), False)
    if extra:
        out["extra"] = extra
    lists = []
    for lst in raw.get("lists") or []:
        if not isinstance(lst, dict):
            continue
        items = []
        for it in lst.get("items") or []:
            name = _clip(it.get("name"), 200) if isinstance(it, dict) else ""
            if not name:
                continue
            item: dict[str, Any] = {"name": name}
            if _clip(it.get("detail"), 80):
                item["detail"] = _clip(it.get("detail"), 80)
            if isinstance(it.get("path"), str) and it["path"].strip():
                item["path"] = it["path"].strip()[:1000]
            n = _int(it.get("tokens"))
            if n is not None:
                item["tokens"] = n
            items.append(item)
        label = _clip(lst.get("label"), 80)
        count = _int(lst.get("count"))
        if label and count:
            lists.append({"key": _clip(lst.get("key"), 60), "label": label, "count": count,
                          "items": items[:MAX_ITEMS]})
    if lists:
        out["lists"] = lists[:10]
    ac = raw.get("auto_compact")
    if isinstance(ac, dict):
        if ac.get("enabled") is False:
            out["auto_compact"] = {"enabled": False}
        elif _int(ac.get("at")):
            out["auto_compact"] = {
                "enabled": True, "at": _int(ac.get("at")), "left": _int(ac.get("left")) or 0,
                **({"percent": _int(ac.get("percent"))} if _int(ac.get("percent")) is not None else {}),
            }
    for k in ("model", "at"):
        if isinstance(raw.get(k), str) and raw[k].strip():
            out[k] = _clip(raw[k], 120)
    return out
