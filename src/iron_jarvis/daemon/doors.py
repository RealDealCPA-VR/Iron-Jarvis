"""Doors — honest links into the surface a chat turn just changed (v1.199.0).

The product thesis is ONE chat surface; the app's other pages are rooms the
assistant walks you into. When a turn CREATES something that lives on another
surface (a workflow, a schedule, a memory, an agent, a custom tool), the reply
carries a DOOR into that surface: ``{"href": ..., "label": ...}``, rendered by
the dashboard under the reply.

Honesty posture (the v1.165.0 route/TurnReceipt rule): a door is derived ONLY
from a tool call that actually EXECUTED OK. The call sites in both chat lanes
sit inside the same ``if ran:`` block that appends to ``tools_used`` — a
failed, denied, or never-made call can never mint a door, because the gate is
the call site, not this module. Files written by tools are EXCLUDED on
purpose: the ArtifactsRail already owns files (``documents`` +
``created_paths``), and a duplicate door is clutter.

Hrefs are PAGE-LEVEL paths only (plus the real ``?scope=`` params the
/memory page parses — see ``dashboard/lib/nav.ts`` and
``dashboard/app/memory/page.tsx``). Entity ids stay OUT of hrefs: no target
page parses them yet, and a deep link that lands nowhere is worse than a
page-level one. When a tool's ``ToolResult.data`` reliably carries the human
name of what it made, the LABEL (never the href) is enriched with it.

MIRROR NOTE (lock-step): both chat lanes — ``chat_turn.py`` (POST /chat) and
``routes/chat.py`` (POST /chat/stream) — collect doors at their ``if ran:``
site and emit ``"doors"`` next to ``tools_used``. Edit both or neither; a
door in one lane only is the exact v1.144.0-class bug this repo documents.

FIX DOORS (v1.353.0, G41): a FAILED call never mints its success door, but
a call that failed because something is not set up (an unpaired browser, an
app that signs in through the browser) leaves a door to the fix —
:func:`failure_door_for`, called at each lane's failed-tool site for a call
the registry did not refuse, into the same ``doors`` list.

THE CONNECT DOOR WITHOUT THE MODEL (v1.359.0, G41): naming an app that is
not connected — for the user's own data in it, never in research about it
(``connectors.app_names.connect_offers``) — puts that app's Connect door
under the reply whether or not the model called ``app_connect``
(:func:`with_connect_door`; the same Directory door and words, one door for
every app named, each app once). v1.360.0 (lane 10B): an everyday word with
no app of its kind connected ("check my inbox") is ONE offer naming every
app of that kind ("Connect Gmail or Microsoft 365 (…) in Settings"), never
Gmail alone.
"""

from __future__ import annotations

import re
from typing import Any

#: Hard cap on doors per turn — past this the reply reads as a link farm.
MAX_DOORS = 4

#: Longest interpolated entity name in a label. Names are stored VERBATIM by
#: their tools (the saved-workflows block learned this the hard way), so a
#: hostile or clumsy name is flattened + clipped, never trusted.
_NAME_CHARS = 60

#: tool name -> door spec. ``label`` is the plain label; ``named`` (optional)
#: is the format used when ``data[name_key]`` carries the entity's human name.
#: EVERY key here is verified against the live registry by
#: ``tests/test_doors_v1199.py`` (the drift guard) — a name the registry does
#: not hold is dead code at best and a silent no-show at worst.
_CATALOG: dict[str, dict[str, str]] = {
    "workflow_create": {
        "href": "/workflows",
        "label": "Open the canvas — your workflow is saved there",
        "named": "Open the canvas — '{name}' is saved there",
        "name_key": "name",
    },
    "schedule_create": {
        "href": "/schedules",
        "label": "See your schedule",
        "named": "See your schedule — '{name}'",
        "name_key": "name",
    },
    "webhook_add": {
        "href": "/webhooks",
        "label": "See your webhook",
        "named": "See your webhook — '{name}'",
        "name_key": "slug",
    },
    # ltm_append's data is {ref, source} — a connector reference, not a human
    # title — and remember_preference's is {id, weight, scope}. Neither
    # reliably carries a name, so neither label is enriched.
    "ltm_append": {
        "href": "/memory?scope=longterm",
        "label": "See what it remembered",
    },
    "remember_preference": {
        "href": "/memory?scope=lessons",
        "label": "See the lesson it saved",
    },
    # v1.506.0 (north-star plan 6.4): keyed on CHAT's own tool, `agent_create`
    # (settings/records_projects). The key was `create_agent`, the agent-run
    # tool no chat path ever brings (capability_search/join.usable_first), so
    # an agent made in chat left no door.
    "agent_create": {
        "href": "/agents",
        "label": "Meet your new agent",
        "named": "Meet your new agent — '{name}'",
        "name_key": "name",
    },
    "tool_create": {
        "href": "/tools",
        "label": "See your new tool",
        "named": "See your new tool — '{name}'",
        "name_key": "name",
    },
    # v1.506.0 (north-star plan 6.4): every chat tool that MAKES a record has
    # a door to the page the record lives on (one row per record kind;
    # tests/test_north_star_doors_v1506.py derives the list of makers from the
    # record-tool table). Page-level addresses only, as above.
    "project_create": {
        "href": "/projects",
        "label": "Open your new project",
        "named": "Open your new project — '{name}'",
        "name_key": "name",
    },
    "reflex_create": {"href": "/reflex", "label": "See your new rule"},
    "goal_add": {"href": "/autonomy", "label": "See your goal"},
    "sentinel_add": {"href": "/sentinels", "label": "See your new watcher"},
    "persona_create": {"href": "/settings?focus=default_persona", "label": "See your personas in Settings"},
    "skill_create": {"href": "/skills", "label": "See your new skill"},
    "lesson_add": {"href": "/memory?scope=lessons", "label": "See the lesson it saved"},
    "night_task_add": {"href": "/chat?view=code", "label": "See tonight's list in Chat › Code"},
    "model_server_add": {"href": "/fleet", "label": "See your model servers"},
    "memory_base_add": {"href": "/memory", "label": "See your memory bases"},
    "mcp_server_add": {"href": "/tools", "label": "See your new extension"},
    "web_service_add": {"href": "/connections", "label": "See your web services"},
    "app_import": {"href": "/settings?section=connections-apps", "label": "See your apps in Settings"},
    # v1.506.0 (north-star plan 6.3): a backup made now — its card lists the
    # backups and holds Restore from backup.
    "backup_now": {"href": "/settings?section=system-maintenance", "label": "Open Maintenance in Settings"},
    # v1.506.0 (north-star plan 6.2): the Improvements page's suggestions.
    "suggestion_list": {"href": "/improvements", "label": "Open Improvements"},
    "suggestion_answer": {"href": "/improvements", "label": "Open Improvements"},
    # 18-a: the results of the changes you accepted (under the waiting ones).
    "change_results": {"href": "/improvements", "label": "Open Improvements"},
    # Next leaps 41-a: what a paired computer may do — its switches are on
    # the card (turning one ON is a press there, D-41.2).
    "peer_allows": {"href": "/settings?section=connections-computers", "label": "Open Your other computers"},
    "peer_allow_off": {"href": "/settings?section=connections-computers", "label": "Open Your other computers"},
    # Next leaps 43-a: the model library is the Models tab on Model servers.
    "model_library": {"href": "/fleet?view=models", "label": "Open Model servers › Models"},
    "model_download": {"href": "/fleet?view=models", "label": "Open Model servers › Models"},
    # Next leaps 40-a: the day's changes, with before and after and Undo for
    # one or many, are the Your day tab on Activity.
    "changes_list": {"href": "/activity?view=day", "label": "See your day on Activity"},
    "change_undo": {"href": "/activity?view=day", "label": "See your day on Activity"},
}

#: v1.506.0 (north-star plan 6.4): the record tools that MAKE a record and
#: whose door is decided elsewhere, on purpose: ``account_add`` and
#: ``app_connect`` in :func:`door_for` from what the call really carried (an
#: app that started leaves no door, v1.353.0); ``channel_connect`` by the
#: Notifications page step (``page_doors`` "slack_setup" / "email_rule_inbox",
#: whose own words name what is left to do there; a success door to the same
#: address would take its place).
OWN_DOOR_TOOLS = frozenset({"account_add", "app_connect", "channel_connect"})


def _clean_name(raw: Any) -> str:
    """One physical line, bounded — a stored name is not trusted layout."""
    flat = " ".join(str(raw).split())
    if len(flat) > _NAME_CHARS:
        flat = flat[: _NAME_CHARS - 1] + "…"
    return flat


def door_for(tool_name: str, result: Any) -> dict[str, str] | None:
    """The door a SUCCESSFUL ``tool_name`` call opens, or ``None``.

    The caller guarantees success (this is called from the ``if ran:`` block
    that also appends to ``tools_used``); this function only maps the name and
    optionally enriches the label from ``result.data``. Never raises — a door
    is decoration, and no decoration is worth failing the turn over.

    v1.353.0 integrate: ``browser_get_status`` is the one browser tool chat
    offers while browser access is off (the default), and it SUCCEEDS with
    the state as data. When that state is "not set up" it opens the same fix
    door a browser refusal leaves (:data:`_BROWSER_FIX_DOORS`): a paired
    browser with access off → "Turn on browser access"; no browser → "Pair
    your browser". A browser that is connected with access on → no door.
    """
    if tool_name == "browser_get_status":
        try:
            data = getattr(result, "data", None)
            if not isinstance(data, dict):
                return None
            code = str(data.get("code") or "")
            if not code and data.get("connected") and str(data.get("access") or "off") == "off":
                code = "BROWSER_ACCESS_OFF"
            label = _BROWSER_FIX_DOORS.get(code)
            return {"href": _BROWSER_FIX_HREF, "label": label} if label else None
        except Exception:  # noqa: BLE001 — no door beats no reply
            return None
    if tool_name == "account_add":
        # v1.438.0 (chat-first plan Wave 4 group B): the new account's OWN
        # sign-in terminal (``settings.records_connections.AccountAddTool``
        # opened it), else the accounts card. Only those two places — a
        # tool's data is never trusted as an address.
        try:
            data = getattr(result, "data", None)
            door = data.get("door") if isinstance(data, dict) else None
            if not isinstance(door, dict):
                return None
            href = str(door.get("href") or "")
            ok = href == "/connections" or re.fullmatch(r"/terminals\?focus=[A-Za-z0-9_-]{1,80}", href)
            label = _clean_name(door.get("label") or "")
            return {"href": href, "label": label} if (ok and label) else None
        except Exception:  # noqa: BLE001 — no door beats no reply
            return None
    if tool_name == "app_connect":
        # v1.442.0 (chat-first plan Wave 7, group A): an app app_connect SAVED
        # that did not start ("Saved Gmail, but it did not start") leaves the
        # button to its card in the Directory, where what is left is — Gmail's
        # one-time Google developer setup. Named from the CATALOG by the id the
        # tool carried, never the model's text; an app that started has none.
        try:
            data = getattr(result, "data", None)
            if not isinstance(data, dict) or data.get("not_started") is not True:
                # v1.506.0 (north-star 6.4) review: an app that STARTED gets no
                # door, on purpose (v1.353.0: a connected app leaves no button
                # to the Directory; tests/test_rereview_wave3_v13530.py).
                return None
            from ..connectors.catalog import get_connector

            conn = get_connector(str(data.get("app") or ""))
            if conn is None:
                return None
            name = _clean_name(conn.name)
            label = f"Finish setting up {name} in Settings" if data.get("needs_setup") else f"See why {name} did not start"
            return {"href": _APPS_FIX_HREF, "label": label}
        except Exception:  # noqa: BLE001 — no door beats no reply
            return None
    if tool_name in CLIENT_DOOR_TOOLS:
        # v1.499.0 (leap 3 Phase 3): a client changed or listed from chat
        # opens its page — the href built ONLY from an id that is a client's
        # id (``client_<12 hex>``), else the Clients page; the label names
        # the client from the record's own name, flattened and clipped.
        try:
            data = getattr(result, "data", None)
            data = data if isinstance(data, dict) else {}
            href = client_href(data.get("client_id"))
            name = _clean_name(data.get("client") or "")
            return client_door(href, name)
        except Exception:  # noqa: BLE001 — no door beats no reply
            return None
    spec = _CATALOG.get(tool_name)
    if spec is None:
        return None
    label = spec["label"]
    named = spec.get("named")
    if named:
        try:
            data = getattr(result, "data", None) or {}
            raw = data.get(spec["name_key"]) if isinstance(data, dict) else None
            name = _clean_name(raw) if raw is not None else ""
            if name:
                label = named.format(name=name)
        except Exception:  # noqa: BLE001 — plain label beats no reply
            pass
    return {"href": spec["href"], "label": label}


#: v1.353.0 (G41) — A REFUSAL LEAVES A BUTTON TO THE FIX. The browser codes
#: whose fix is a press on the Browser page (Settings › Connections ›
#: Browser; ``/computeruse`` redirects there with its query), and the words
#: on that press. Keys are ``browser/errors.BrowserErrorCode`` values, read
#: off the failed result's ``data["code"]`` — the envelope every browser tool
#: refusal carries. Codes whose fix is the MODEL's next call (a stale
#: element, a tab id, a page that did not load) are deliberately absent: a
#: button there would send the user to fix what is not theirs to fix.
_BROWSER_FIX_DOORS: dict[str, str] = {
    "BROWSER_NOT_CONNECTED": "Pair your browser",
    "PAIRING_REQUIRED": "Pair your browser",
    "AUTHENTICATION_FAILED": "Pair your browser",
    "BROWSER_ACCESS_OFF": "Turn on browser access",
    "PERMISSION_DENIED": "Grant site access",
}
_BROWSER_FIX_HREF = "/computeruse"

#: Where an app that signs in through the browser is connected: Settings ›
#: Connections › Apps & tools (the Directory), whose card for that app holds
#: its Connect press. Chat cannot connect one (``app_connect`` refuses an
#: OAuth app by design), so the refusal's fix is that page.
_APPS_FIX_HREF = "/settings?section=connections-apps"


def failure_door_for(tool_name: str, args: Any, result: Any) -> dict[str, str] | None:
    """The door a FAILED ``tool_name`` call leaves: a button to the fix, or
    ``None`` (v1.353.0, G41).

    The success-only :func:`door_for` is untouched — a failed call never
    mints ITS door. This is the other half: when a call failed because
    something is not SET UP, and the fix is a press on a page, the reply
    carries that press. Both chat lanes call this at their failed-tool site
    for a call the registry did not refuse (a declined card, "not armed" or
    "low trust" is the user's or the rules' answer, not a missing setup), and
    the entry rides the SAME ``doors`` field and ``collect_doors`` cap as a
    success door. MIRROR NOTE (lock-step): ``chat_turn.py`` and
    ``routes/chat.py`` carry the same append — edit both or neither.

    Two fixes today, each read off what the failed call really carried —
    never off its error WORDS:

    * a browser tool's refusal whose ``data["code"]`` is a setup code
      (:data:`_BROWSER_FIX_DOORS`) → "Pair your browser" (and friends) to the
      Browser page;
    * ``app_connect`` for a catalog app that signs in through the browser
      (``connect_via == "oauth"``) → "Connect <App> in Settings" to the
      Directory. The label is the CATALOG's name, never the model's text.

    Never raises — a door is decoration, and no decoration is worth failing
    the turn over."""
    try:
        if getattr(result, "ok", False):
            return None
        name = str(tool_name or "")
        if name == "suggestion_answer":
            # v1.506.0 (north-star plan 6.2): a suggestion chat could not
            # answer (gone, blocked, answered only on its page) leaves the
            # door to the Improvements page — the one address the tool names.
            data = getattr(result, "data", None)
            if isinstance(data, dict) and data.get("door") == "/improvements":
                return {"href": "/improvements", "label": "Open Improvements"}
            return None
        if name.startswith("browser_"):
            data = getattr(result, "data", None)
            code = str(data.get("code") or "") if isinstance(data, dict) else ""
            label = _BROWSER_FIX_DOORS.get(code)
            if label:
                return {"href": _BROWSER_FIX_HREF, "label": label}
            return None
        if name == "app_connect":
            from ..connectors.catalog import get_connector, get_link_card

            app = str(args.get("app") or "").strip().lower() if isinstance(args, dict) else ""
            conn = get_connector(app) if app else None
            if conn is not None and getattr(conn, "connect_via", "") == "oauth":
                return {"href": _APPS_FIX_HREF, "label": connect_label([conn.name])}
            # v1.353.0 integrate: the browser ("puppeteer" until G43) is set up
            # on the Browser page -- the SAME button a browser refusal leaves.
            if conn is None and app and get_link_card(app) is not None:
                return {"href": _BROWSER_FIX_HREF, "label": _BROWSER_FIX_DOORS["BROWSER_NOT_CONNECTED"]}
        return None
    except Exception:  # noqa: BLE001 — no door beats no reply
        return None


#: v1.359.0 (G41): the most apps one Connect door names before "and N more".
_CONNECT_NAMES_MAX = 3
#: The words of a Directory door this module made (:func:`connect_label`) —
#: read back only by :func:`with_connect_door`, so the words and their reader
#: live side by side.
_CONNECT_LABEL_RX = re.compile(r"\AConnect (.+) in Settings\Z")


def connect_label(names: Any) -> str:
    """The Directory door's words for the apps *names* (v1.359.0, G41): one
    app reads "Connect Google Drive in Settings" (the words this door has had
    since v1.353.0), two "Connect Dropbox and Google Drive in Settings",
    more than three "Connect A, B and 2 more apps in Settings". Names are
    the CATALOG's (never the model's text), flattened and clipped."""
    ns = [n for n in (_clean_name(x) for x in (names or [])) if n]
    if not ns:
        return "Connect your apps in Settings"
    if len(ns) > _CONNECT_NAMES_MAX:
        ns = ns[: _CONNECT_NAMES_MAX - 1] + [f"{len(ns) - (_CONNECT_NAMES_MAX - 1)} more apps"]
    words = ns[0] if len(ns) == 1 else ", ".join(ns[:-1]) + " and " + ns[-1]
    return f"Connect {words} in Settings"


def offered_app(tool_name: str, args: Any) -> str:
    """The catalog id an ``app_connect`` call named, or ``""`` for any other
    call (v1.359.0, G41). Both chat lanes note it for a call that put
    app_connect's OWN card or answer in front of the user (it ran, or — the
    stream lane — it carded, whatever the answer), so the message's Connect
    door leaves that app out (:func:`with_connect_door`). A call the
    registry refused before anyone saw it (not armed, low trust) is not
    noted: the door is how that user still gets to connect. The id is
    normalised the matcher's way ("Google Drive" → ``google_drive``).
    Never raises."""
    try:
        if str(tool_name or "") != "app_connect" or not isinstance(args, dict):
            return ""
        from ..connectors.catalog import get_connector

        raw = str(args.get("app") or "").strip().lower()
        if not raw:
            return ""
        for cand in (raw, re.sub(r"[\s-]+", "_", raw)):
            conn = get_connector(cand)
            if conn is not None:
                return str(conn.id)
        from ..connectors.app_names import apps_named

        named = apps_named(raw)
        return named[0] if named else ""
    except Exception:  # noqa: BLE001 — no door beats no reply
        return ""


def with_connect_door(entries: Any, offers: Any, offered: Any = ()) -> list[Any]:
    """The turn's door entries with the CONNECT DOOR for the apps the user's
    message named and that are not connected (v1.359.0, G41 — the door no
    longer waits for the model to call ``app_connect``).

    *offers* is ``connectors.app_names.connect_offers`` (``[{id, name}]``,
    read when the message arrived; research about an app is never one);
    *offered* the app ids :func:`offered_app` noted this turn — an app
    app_connect already put its own card or answer up for gets no second
    offer. ONE DOOR, ONE ADDRESS: every app is connected on the Directory
    (``_APPS_FIX_HREF``, the address :func:`failure_door_for` already uses),
    so the apps share one door — a fix door app_connect left for an OAuth
    app is widened to name the others too ("Connect Dropbox and Google Drive
    in Settings"), never a second link to the same place, and each app is
    named once. The door is kept even past :data:`MAX_DOORS` (it takes the
    last place). Both chat lanes call this right before their done frame /
    response, then :func:`collect_doors` as before — MIRROR NOTE
    (lock-step). Never raises (the entries come back unchanged)."""
    base = list(entries or [])
    try:
        skip = {str(a) for a in (offered or ()) if a}
        names: list[str] = []
        for row in offers or []:
            if not isinstance(row, dict):
                continue
            cid = str(row.get("id") or "")
            name = _clean_name(row.get("name") or "")
            # v1.360.0 (lane 10B): a CHOICE ("Gmail or Microsoft 365 (…)" for
            # "check my inbox" with no mail app) carries every app it names;
            # an app_connect card for any one of them was the offer.
            ids = {str(i) for i in (row.get("ids") or []) if i} or {cid}
            if cid and name and not (ids & skip) and name not in names:
                names.append(name)
        if not names:
            return base
        out: list[Any] = []
        at: "int | None" = None
        shown: list[str] = []
        for e in base:
            if isinstance(e, dict) and e.get("href") == _APPS_FIX_HREF:
                m = _CONNECT_LABEL_RX.match(str(e.get("label") or ""))
                if m and m.group(1) not in shown:
                    shown.append(m.group(1))
                if at is None:
                    at = len(out)
                    out.append(e)
                continue
            out.append(e)
        door = {"href": _APPS_FIX_HREF, "label": connect_label(shown + [n for n in names if n not in shown])}
        if at is not None:
            out[at] = door
            return out
        kept = collect_doors(out)
        if len(kept) >= MAX_DOORS:
            return kept[: MAX_DOORS - 1] + [door]
        return out + [door]
    except Exception:  # noqa: BLE001 — no door beats no reply
        return base


# --------------------------------------------------------------------------- #
# v1.499.0 (vision plan leap 3 Phase 3): a door to a client's page
# --------------------------------------------------------------------------- #
#: The client tools whose success opens the client's page (or the Clients page).
CLIENT_DOOR_TOOLS = frozenset({
    "client_list", "client_answer", "client_update", "client_preference_add",
    "client_preference_change", "client_preference_forget",
})
#: The Clients page.
CLIENTS_HREF = "/clients"
def client_href(client_id: Any) -> str:
    """``/clients/<id>`` for an id that is a client's id (``client_<12
    hex>``, ``workmap.clients.CLIENT_ID_RE``, matched WHOLE: a trailing
    newline is no id), else ``/clients``: a name, a path or anything a model
    wrote never becomes part of an address."""
    from ..workmap.clients import CLIENT_ID_RE

    cid = client_id if isinstance(client_id, str) else ""
    return f"{CLIENTS_HREF}/{cid}" if CLIENT_ID_RE.fullmatch(cid) else CLIENTS_HREF


def _possessive(name: str) -> str:
    try:
        from ..settings.records_clients import possessive

        return possessive(name)
    except Exception:  # noqa: BLE001
        return name


def client_door(href: str, name: str = "") -> dict[str, str]:
    """The door's words: "Open Hartleys' page" for one client's page, "Open
    your clients" for the Clients page."""
    if href != CLIENTS_HREF and name:
        return {"href": href, "label": f"Open {_possessive(name)} page"}
    return {"href": CLIENTS_HREF, "label": "Open your clients"}


def _named_pairs(named: Any) -> list[tuple[str, str]]:
    """``named`` as ``[(id, name)]``: a mapping id → name (or id → (name,
    signature, aliases), ``ClientMap.names``' shape), or a list of
    ``{id, name}`` / ``(id, name)``."""
    out: list[tuple[str, str]] = []
    items: Any
    if isinstance(named, dict):
        items = list(named.items())
    else:
        items = list(named or ())
    for item in items:
        cid, name = "", ""
        if isinstance(item, dict):
            cid, name = str(item.get("id") or ""), str(item.get("name") or "")
        elif isinstance(item, (tuple, list)) and len(item) == 2:
            cid = str(item[0] or "")
            val = item[1]
            name = str(val[0] if isinstance(val, (tuple, list)) and val else val or "")
        if cid and (cid, name) not in out:
            out.append((cid, name))
    return out


def with_client_door(entries: Any, named: Any) -> list[Any]:
    """The turn's door entries with AT MOST ONE door for the confirmed
    clients the OWNER's own message named (``named``: their ids and names,
    ``wall.current().named`` with the map's names; never the model's words).
    One client named → its page; several → the Clients page. Dashboard turns
    only (the lanes decide); added LAST only while there is room under
    :data:`MAX_DOORS`, and never beside a door that already leads to the
    Clients page or a client's page (a client tool's own door, the page
    door). The href is built only from an id matching
    ``^client_[0-9a-f]{12}$``. Never raises (the entries come back
    unchanged)."""
    base = list(entries or [])
    try:
        pairs = _named_pairs(named)
        if not pairs:
            return base
        if len(pairs) == 1:
            href = client_href(pairs[0][0])
            door = client_door(href, _clean_name(pairs[0][1]))
        else:
            door = client_door(CLIENTS_HREF)
        kept = collect_doors(base)
        if len(kept) >= MAX_DOORS:
            return base
        if any(str(e.get("href") or "").startswith(CLIENTS_HREF) for e in kept):
            return base
        return base + [door]
    except Exception:  # noqa: BLE001 — no door beats no reply
        return base


def collect_doors(entries: Any) -> list[dict[str, str]]:
    """Dedupe by href (first seen wins, order preserved), cap at MAX_DOORS.

    Accepts the raw per-turn list the lanes accumulate — ``None`` entries
    (calls that opened no door) are skipped, so call sites can append
    ``door_for(...)`` unconditionally.
    """
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        href = entry.get("href")
        if not href or href in seen:
            continue
        seen.add(href)
        out.append(entry)
        if len(out) >= MAX_DOORS:
            break
    return out
