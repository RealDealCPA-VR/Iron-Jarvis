"""Chat knows the apps by name, and what is connected (v1.354.0, G39).

Before this, chat recognised an app only through a short word list
(``settings.records._APP_WORDS``): "check my Gmail", "open my inbox" and "is
github connected?" armed nothing, "search my Google Drive for the lease"
armed WEB search, and the model was never told what is connected.

ONE matcher, built from the catalog itself (:data:`connectors.catalog.CATALOG`
— its names, plus the words people use for them in :data:`ALIASES`), read by:

* ``settings.records.record_tools_for`` — a named app arms ``app_connect``;
* ``daemon.chat_turn._gather_grounding`` — a short "Apps named in this
  message" section saying, for each, connected (N tools) or not (both lanes);
* the stream lane (``routes/chat.py``) — a named app that IS connected with
  tools loaded has its tools ask-armed (visible, never granted);
* ``tools.autoselect`` — the web rule does not fire when the only web signal
  is a catalog app's name ("search my Google Drive" is not a web search).

Rules that keep it honest:

* An app whose NAME is an everyday word (:data:`GENERIC` — Knowledge graph,
  Fetch web pages, Files & folders, Box, Step-by-step reasoning) counts only
  beside a connect / set-up verb ("connect Box"), never in "put it in a box".
  v1.357.0 (G82): those four took the Tools page's names (one catalog, one
  name); the names they had before stay in :data:`ALIASES`, so "set up
  sequential thinking" and "connect the filesystem" still name them.
* "my email" / "my inbox" mean Gmail only when no other mail service is named
  ("check my Outlook inbox" names no catalog app).
* Claude Code and Codex are not catalog apps; :func:`clis_named` finds them so
  the section can say whether they are signed in, from the CACHED verdict
  (``ProviderManager.cli_login_status`` — never a probe).

Pure text work here; the status reads are split by where they may run:
:func:`live_tools` is in-memory and runs ON the loop (the registry is iterated
— a worker thread could race a pack registering, the v1.311.0 rule), and
:func:`app_states` does the blocking reads (the connections table, the memory
bases) and must be called OFF the loop. Neither ever raises.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

#: The longest message text the matcher reads (the autoselect bound).
_MAX_TEXT = 4000

#: Words people use for a catalog app beyond its catalog name. Regex
#: fragments, matched case-insensitively between word boundaries.
ALIASES: dict[str, tuple[str, ...]] = {
    "github": (r"git\s*hub",),
    "postgres": (r"postgres(?:ql)?",),
    "google_maps": (r"google\s*maps",),
    # v1.354.0 re-review: "my drive" in lower case is this PC's disk ("how
    # much space is left on my drive", "clean up my drive"); only Google's own
    # capitalised name for the Drive root, "My Drive", names the app.
    "google_drive": (r"google\s*drive", r"g-?drive", r"(?-i:My\s+Drive)"),
    "onedrive": (r"one\s*drive",),
    "dropbox": (r"drop\s*box",),
    "brave_search": (r"brave\s*search",),
    # v1.357.0 (G82): the names these cards had before they took the Tools
    # page's ("Filesystem", "Web Fetch", "Knowledge Graph Memory",
    # "Sequential Thinking") — still the words people use for them.
    "filesystem": (r"file\s*system",),
    "fetch": (r"web\s*fetch",),
    "memory": (r"knowledge\s+graph\s+memory",),
    "sequential_thinking": (r"sequential\s*thinking",),
    # v1.356.0 integrate (G60, lane 6D's note): "what's on my calendar
    # tomorrow?" and "check my Outlook inbox" name Microsoft 365 (Outlook, the
    # calendar, OneDrive). "Outlook" only capitalised ("an outlook on the
    # market" is a view), and "my calendar" never as "my calendar year".
    "microsoft_365": (
        r"microsoft\s*365",
        r"office\s*365",
        r"m365",
        r"(?-i:Outlook)",
        r"outlook\.com",
        r"my\s+(?:outlook\s+)?calendar(?!\s+years?\b)",
    ),
}

#: The everyday words for mail that mean Gmail (the one mail app in the
#: catalog) — only when no other mail service is named, and never when the
#: word only describes one of the user's own records ("pause my inbox goal",
#: "my email schedule") or an address ("my email address").
_NOT_MAIL_ITSELF = (
    r"(?!\s+(?:address(?:es)?|goals?|schedules?|workflows?|rules?|watchers?|webhooks?"
    r"|sentinels?|reflex(?:es)?|zero|templates?|signatures?)\b)"
)
#: v1.354.0 re-review: "the email" is a MESSAGE the user has in hand ("rewrite
#: the email below", "summarize the email", "make the email shorter") — the
#: everyday request of a new user, which must not be told Gmail is not
#: connected. Only "my email(s)" names the mailbox, and not when it points at
#: text in the message ("my email below:", "my email draft").
_NOT_A_PASTED_ONE = r"(?!\s*(?::|(?:below|above|here|drafts?)\b))"
_GMAIL_WORDS = re.compile(
    r"\bg-?mail\b"
    r"|\bmy\s+e-?mails?\b" + _NOT_MAIL_ITSELF + _NOT_A_PASTED_ONE
    + r"|\b(?:my|the)\s+(?:e-?mail\s+)?(?:inbox|mailbox)\b" + _NOT_MAIL_ITSELF,
    re.IGNORECASE,
)
_OTHER_MAIL = re.compile(
    r"\b(?:outlook|hotmail|yahoo|icloud|exchange|proton\s*mail|aol)\b", re.IGNORECASE
)

#: Catalog apps whose name is an everyday word: they count only beside a
#: connect / set-up verb, within a short reach before the name.
GENERIC: frozenset[str] = frozenset(
    {"memory", "fetch", "filesystem", "box", "sequential_thinking"}
)

#: The verbs that make a generic name an app ("connect Box", "set up the
#: filesystem"). "add" is not one ("add a box to the diagram").
CONNECT_VERB = (
    r"(?:connect|reconnect|set\s*up|setup|install|hook\s+up|link|turn\s+on|enable"
    r"|sign\s+in\s+to|log\s+in\s+to)"
)

#: Brand names that are also everyday words: these readings are not the app.
_NOT_THE_APP: dict[str, str] = {
    "notion": r"(?<![Aa] )(?<![Tt]he )\bnotion\b(?!\s+(?:that|of)\b)",
    "slack": r"(?<![Ss]ome )(?<![Uu]p the )\bslack\b(?!\s+off\b)",
    "stripe": r"(?<![Aa] )\bstripe\b(?!\s+(?:on|of|across|down|along|pattern|through)\b)",
}

#: The subscription CLIs a message may ask about ("is Claude Code signed in?").
CLI_WORDS: dict[str, str] = {
    "claude-cli": r"\bclaude\s*code\b",
    "codex-cli": r"\bcodex\b",
}

#: Words that ask to connect, re-connect or fix an app's sign-in — a named app
#: that is already connected keeps ``app_connect`` armed only beside one.
CONNECT_INTENT = re.compile(
    r"\b(?:" + CONNECT_VERB + r"|tokens?|re-?auth\w*|renew\w*|expired|keys?)\b",
    re.IGNORECASE,
)


def _name_pattern(name: str) -> str:
    return r"\s+".join(re.escape(w) for w in name.lower().split())


@lru_cache(maxsize=1)
def _compiled() -> tuple[tuple[str, "re.Pattern[str]"], ...]:
    """``(catalog id, pattern)`` for every catalog app, in catalog order."""
    from .catalog import CATALOG

    out: list[tuple[str, re.Pattern[str]]] = []
    for c in CATALOG:
        if c.id == "gmail":
            out.append((c.id, _GMAIL_WORDS))
            continue
        if c.id in _NOT_THE_APP:
            out.append((c.id, re.compile(_NOT_THE_APP[c.id], re.IGNORECASE)))
            continue
        names = [_name_pattern(c.name), *ALIASES.get(c.id, ())]
        body = "|".join(f"(?:{n})" for n in names)
        if c.id in GENERIC:
            rx = rf"\b{CONNECT_VERB}\b[^.?!\n]{{0,40}}?\b(?:{body})\b"
        else:
            rx = rf"\b(?:{body})\b"
        out.append((c.id, re.compile(rx, re.IGNORECASE)))
    return tuple(out)


def apps_named(text: str) -> list[str]:
    """The catalog app ids *text* names, in catalog order (deduped). Never
    raises; "" or no app names ``[]``."""
    t = str(text or "")[:_MAX_TEXT]
    if not t.strip():
        return []
    out: list[str] = []
    try:
        for cid, rx in _compiled():
            if cid == "gmail" and _OTHER_MAIL.search(t) and not re.search(
                r"\bg-?mail\b", t, re.IGNORECASE
            ):
                continue
            if rx.search(t):
                out.append(cid)
    except Exception:  # noqa: BLE001 — a matcher fault names nothing
        return []
    return out


def app_name_hits(text: str) -> list[tuple[str, int, int]]:
    """``(catalog id, start, end)`` for every place *text* names an app, in
    catalog order (v1.358.0: the web rule asks WHICH app a search verb is
    aimed at, because only a CONNECTED app takes a search over). Never
    raises."""
    t = str(text or "")[:_MAX_TEXT]
    hits: list[tuple[str, int, int]] = []
    try:
        for cid, rx in _compiled():
            if cid == "gmail" and _OTHER_MAIL.search(t) and not re.search(
                r"\bg-?mail\b", t, re.IGNORECASE
            ):
                continue
            hits += [(cid, m.start(), m.end()) for m in rx.finditer(t)]
    except Exception:  # noqa: BLE001
        return []
    return hits


def app_name_spans(text: str) -> list[tuple[int, int]]:
    """Where in *text* each named app's words sit (for the web rule, which
    must not count "Google" in "Google Drive" as a web signal). Never raises."""
    return [(start, end) for _cid, start, end in app_name_hits(text)]


def clis_named(text: str) -> list[str]:
    """The subscription CLI providers *text* names ("claude-cli", "codex-cli")."""
    t = str(text or "")[:_MAX_TEXT]
    return [p for p, rx in CLI_WORDS.items() if re.search(rx, t, re.IGNORECASE)]


def wants_connect(text: str) -> bool:
    """Does *text* ask to connect / fix an app's sign-in (not just use it)?"""
    return bool(CONNECT_INTENT.search(str(text or "")[:_MAX_TEXT]))


# --------------------------------------------------------------------------- #
# Status — ON the loop (in-memory) and OFF it (blocking), never raising.
# --------------------------------------------------------------------------- #
def live_tools(platform: Any, app_ids: list[str]) -> dict[str, list[str]]:
    """``{catalog id: its loaded tool names}`` for each named MCP app that is
    configured — in-memory reads only (the config list + the registry), so
    this runs ON the loop. An app with no tools loaded maps to ``[]``; an app
    that is not configured (or not an MCP app) is absent. Never raises."""
    out: dict[str, list[str]] = {}
    try:
        from .catalog import get_connector
        from .service import _configured_name
    except Exception:  # noqa: BLE001
        return out
    for cid in app_ids:
        try:
            conn = get_connector(cid)
            if conn is None or conn.connect_via != "mcp":
                continue
            server = _configured_name(platform, cid)
            if not server:
                continue
            out[cid] = list(platform.registry.mcp_names(server))
        except Exception:  # noqa: BLE001 — one app's fault names nothing
            continue
    return out


def app_states(
    platform: Any,
    app_ids: list[str],
    live: dict[str, list[str]],
    clis: list[str] | None = None,
) -> list[dict[str, Any]]:
    """One row per named app (and named CLI): ``{id, name, state, tools,
    needs, note}`` where ``state`` is ``connected`` / ``not_working`` /
    ``off`` (v1.356.0: saved, but the user switched it off) / ``signin``
    (v1.358.0: saved and loaded, its sign-in on this PC unfinished) /
    ``not_connected`` (CLIs: ``signed_in`` / ``signed_out`` /
    ``not_installed`` / ``unknown``). BLOCKING (the connections table, the
    memory bases): call it OFF the loop. Never raises; a row it cannot read
    is left out rather than guessed."""
    from .catalog import get_connector

    rows: list[dict[str, Any]] = []
    oauth_rows: "dict[str, dict] | None" = None

    def _oauth(provider: str) -> dict:
        nonlocal oauth_rows
        if oauth_rows is None:
            try:
                oauth_rows = {
                    str(r.get("provider")): r for r in (platform.connections.status() or [])
                }
            except Exception:  # noqa: BLE001 — unknown, not "none"
                oauth_rows = {}
        return oauth_rows.get(provider) or {}

    for cid in app_ids:
        try:
            conn = get_connector(cid)
            if conn is None:
                continue
            row: dict[str, Any] = {
                "id": cid, "name": conn.name, "state": "not_connected", "tools": 0,
                "needs": "", "note": "",
            }
            if conn.connect_via == "mcp":
                if cid in live and _switched_off(platform, cid):
                    # v1.356.0 integrate (G78): saved with its key, its tools
                    # not loaded on purpose — never "set up, but not working".
                    row["state"] = "off"
                elif cid in live:
                    n = len(live[cid])
                    row["tools"] = n
                    row["state"] = "connected" if n else "not_working"
                    if not n:
                        row["note"] = _load_reason(platform, cid)
                    else:
                        # v1.358.0 (integrate, lane 8D review): an app that
                        # signs in on this PC reads as its Directory card
                        # does — "Sign in to Microsoft to finish" — until its
                        # own check says signed in; never "connected" here
                        # while the card says otherwise.
                        unfinished = _signin_unfinished(platform, conn, cid)
                        if unfinished:
                            row["state"] = "signin"
                            row["note"] = unfinished
                else:
                    row["needs"] = conn.needs
            else:
                st = _oauth(conn.provider)
                if st.get("connected"):
                    row["state"] = "connected"
                    if conn.connect_via == "oauth" and _memory_base_missing(platform, conn.provider):
                        row["note"] = (
                            "its files reach chat through a memory base, and none points"
                            " at it yet (the Memory page adds one)"
                        )
                elif conn.connect_via == "oauth" and not st.get("oauth_client_configured"):
                    row["needs"] = conn.needs
            rows.append(row)
        except Exception:  # noqa: BLE001 — one app's fault drops only its row
            continue
    for cli in clis or []:
        try:
            st = platform.providers.cli_login_status(cli) or {}
            if not st.get("installed"):
                state = "not_installed"
            elif st.get("signed_in") is True:
                state = "signed_in"
            elif st.get("signed_in") is False:
                state = "signed_out"
            else:
                state = "unknown"
            from ..providers.failure_words import plain_name

            rows.append({"id": cli, "name": plain_name(cli), "state": state, "tools": 0,
                         "needs": "", "note": ""})
        except Exception:  # noqa: BLE001
            continue
    return rows


def _switched_off(platform: Any, cid: str) -> bool:
    """True when the app's saved pack row is switched OFF (v1.356.0, G78) —
    the loader's own reading (``connectors.service.pack_on``). Any fault reads
    as on (the older answer)."""
    try:
        from .service import _configured_name, _server_cfg, pack_on

        cfg = _server_cfg(platform, _configured_name(platform, cid) or cid)
        return cfg is not None and not pack_on(cfg)
    except Exception:  # noqa: BLE001
        return False


def _signin_unfinished(platform: Any, conn: Any, cid: str) -> str:
    """The card's own line ("Sign in to Microsoft to finish.") while an app
    that signs in on this PC has not; "" once signed in, or for any other
    app (v1.358.0, integrate). The Directory's reading
    (``connectors.service._signin_state``): ``signin_seen.seen`` is cached
    and never blocks; an unsure answer is "not signed in yet"."""
    if not getattr(conn, "signin_args", None):
        return ""
    try:
        from . import signin_seen
        from .service import _configured_name, _server_cfg, signin_identity, signin_needed_line
    except Exception:  # noqa: BLE001
        return ""
    try:
        server = _configured_name(platform, cid) or cid
        # v1.358.0 re-review: the FIRST read after a restart had no verdict
        # and told the model "not signed in yet" for a user who is signed
        # in; this reader is already off the loop, so it waits (bounded) for
        # the check that read starts. A still-unknown answer reads as before.
        verdict = signin_seen.seen_settled(
            platform, server, signin_identity(_server_cfg(platform, server))
        )
    except Exception:  # noqa: BLE001 — unsure is "not signed in yet"
        verdict = None
    if verdict is not None and getattr(verdict, "signed_in", False):
        return ""
    try:
        return signin_needed_line(conn)
    except Exception:  # noqa: BLE001
        return ""


def _load_reason(platform: Any, cid: str) -> str:
    """The pack's own load record, in a few words ("" when none)."""
    try:
        from ..mcp.tools import load_status
        from .service import _configured_name

        rec = dict(load_status(_configured_name(platform, cid) or cid) or {})
        state = str(rec.get("state") or "")
        if state == "starting":
            return "it is still starting"
        reason = " ".join(str(rec.get("reason") or "").split())
        return reason[:160]
    except Exception:  # noqa: BLE001
        return ""


def _memory_base_missing(platform: Any, provider: str) -> bool:
    """True only when the memory bases were READ and none is this drive."""
    try:
        from .service import _CLOUD_DRIVE_KINDS, _memory_kinds

        if provider not in _CLOUD_DRIVE_KINDS:
            return False
        kinds = _memory_kinds(platform)
        return kinds is not None and provider not in kinds
    except Exception:  # noqa: BLE001
        return False


#: Where an app is connected: the Directory (its nav name since v1.313.0),
#: which lives in Settings' Connections group.
APPS_PLACE = "the Directory in Settings › Connections"


def _needs_words(needs: str) -> str:
    """"Needs a one-time Google developer setup" → "It needs a one-time
    Google developer setup first."""
    n = " ".join(str(needs or "").split()).rstrip(". ")
    if not n:
        return ""
    if n.lower().startswith("needs "):
        n = "It needs " + n[6:]
    return f" {n} first."


def connected_ids(rows: list[dict[str, Any]]) -> list[str]:
    """The catalog ids of the rows :func:`app_states` read as ``connected``
    (v1.358.0): the apps a search may be taken over by (the web rule's
    ``apps_on``). CLI rows are never apps. Never raises."""
    try:
        return [
            str(r.get("id")) for r in rows or []
            if r.get("state") == "connected" and r.get("id") and r.get("id") not in CLI_WORDS
        ]
    except Exception:  # noqa: BLE001
        return []


def apps_section(rows: list[dict[str, Any]]) -> str:
    """The grounding section for the named apps ("" when none). Plain facts
    the model must not contradict, plus what to do for an app that is not
    connected — say so and offer to connect it, never search the web for
    what is inside it. v1.358.0 (the re-audit's regression): a question ABOUT
    the app itself (its docs, pricing, how to use it) is ordinary web
    research, so the line no longer forbids the web outright. Never raises."""
    if not rows:
        return ""
    lines: list[str] = []
    try:
        for r in rows:
            name = str(r.get("name") or r.get("id") or "")
            state = r.get("state")
            if state == "connected":
                n = int(r.get("tools") or 0)
                line = f"- {name}: connected" + (
                    f" ({n} {'tool' if n == 1 else 'tools'})." if n else "."
                )
                if r.get("note"):
                    line = line.rstrip(".") + f"; {r['note']}."
            elif state == "not_working":
                why = f" ({r['note']})" if r.get("note") else ""
                # v1.354.0 re-review: said only when answering NEEDS the app
                # — a matched word is a hint, not the request ("rewrite my
                # email below" needs no Gmail), so the model must not open
                # every such reply with "it is not connected".
                line = (
                    f"- {name}: set up, but not working right now{why}. If answering"
                    f" needs {name}, say so and point to {APPS_PLACE} to fix it."
                )
            elif state == "signin":
                # v1.358.0 (integrate): saved and loaded, sign-in unfinished —
                # the Directory card's own words and press.
                why = str(r.get("note") or "").strip()
                line = (
                    f"- {name}: saved, but not signed in yet" + (f" ({why.rstrip('.')})" if why else "")
                    + f". If answering needs {name}, say so and point to its Sign in"
                    f" button on its card in {APPS_PLACE}."
                )
            elif state == "off":
                line = (
                    f"- {name}: switched off by the user (kept with its key; its"
                    f" tools are not loaded). If answering needs {name}, say so and"
                    " point to Settings › Tools or its card in"
                    f" {APPS_PLACE} to turn it on. Never ask for its key again."
                )
            elif state == "not_connected":
                # v1.358.0: "Never search the web in its place" read as "never
                # search the web" to a model asked "look up the Stripe API docs"
                # — it offered to connect Stripe instead of reading the docs.
                # What the web cannot do is reach what is INSIDE the user's
                # app; a question about the app itself is web research.
                line = (
                    f"- {name}: not connected.{_needs_words(r.get('needs', ''))} If"
                    f" answering needs what is in the user's own {name}, tell the"
                    " user plainly and offer to connect it (the app_connect tool"
                    f" does that when it is offered; otherwise {APPS_PLACE}). Never"
                    " search the web for what is in it. A question about"
                    f" {name} itself (its docs, pricing or how to use it) may be"
                    " answered from the web. If answering needs neither, say"
                    " nothing about it."
                )
            elif state == "signed_in":
                line = f"- {name}: signed in."
            elif state == "signed_out":
                line = (
                    f"- {name}: installed but not signed in (its Sign in button is in"
                    " Settings › Connections)."
                )
            elif state == "not_installed":
                line = f"- {name}: not installed on this PC."
            else:
                line = f"- {name}: sign-in not checked yet."
            lines.append(line)
    except Exception:  # noqa: BLE001 — no section beats a broken one
        return ""
    return (
        "\n\n# Apps named in this message\n"
        "What is connected right now (checked when this message arrived):\n"
        + "\n".join(lines)
    )
