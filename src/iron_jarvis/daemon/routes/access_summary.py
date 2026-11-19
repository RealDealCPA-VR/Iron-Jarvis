"""What Jarvis can reach, read off the rules that enforce it (v1.352.0, G27).

Settings › Permissions & ledger opens with one calm block that says, in plain
words, what chat and agents can read and where they save. Two of those facts
live ONLY in the daemon, so this one read-only route hands them over:

* ``reads`` — ``core/fs_policy`` is the one read gate (``fs_read_ok``). It
  refuses the folders that hold the app's keys and records (the protected
  roots registered at boot) on every install, and — only when an install sets
  ``IRONJARVIS_FS_ALLOWLIST`` — everything outside the folders listed there.
  No approval level cards a read. ``limited`` is decided the way
  ``fs_path_allowed`` decides it (a non-blank variable), so a variable whose
  every folder fails to resolve reads as limited to nothing, which is what the
  gate then enforces; ``folders`` are ``allowlist_roots()``.
  v1.356.0 (G81): the user's own "Folders Jarvis may read" setting is a
  second limit the same gate enforces. ``chosen`` = those folders as the
  user typed them (the block names them, then "and its own working
  folders"), ``install`` = whether the installation's own limit is on;
  ``limited`` is true for either.
* ``writes`` — ``chat_files_dir``: where a chat outside a project keeps its
  folder (``config.chat_files_dir``: the "Chat files folder" setting, else
  ``<Documents>\\Iron Jarvis``). "" when it cannot be worked out.

* ``sends`` (fourth fix plan, group 32, C-PG-1) — "Sending outside this
  PC": every LIVE place outside this PC that Iron Jarvis sends to, read from
  the same stores each page writes (:func:`outside_places`). One row per
  place: ``{kind, kind_label, name, where, ask, asked, href}``. ``where`` is
  a HOST only (or a service's name: "Slack", "Notion"), never a path, a query,
  a user name or a password; ``ask`` is ``once`` (approved when it was set
  up, then it goes without asking), ``every_time`` or ``own_setting``, and
  ``asked`` says it in words. ``unread`` names each kind whose store could not
  be read, so the screen says so instead of "nothing". The AI services that
  answer chats are not here: the Models settings list them.

No path to a protected folder rides here; the block names them in words.
A sync handler on purpose: resolving the Documents folder, canonicalising
the allowlist and reading the places' stores (the database, other apps'
settings files) touch the disk, and FastAPI runs a sync handler in its
threadpool, never on the event loop (v1.153.1). Nothing here calls a network
service. Never raises: a fact that cannot be read is reported empty (or
``unread``), never as a 500 on a Settings page.
"""

from __future__ import annotations

import ipaddress
import os
from typing import Any, Callable
from urllib.parse import urlsplit

from fastapi import FastAPI

ALLOWLIST_ENV = "IRONJARVIS_FS_ALLOWLIST"

# --------------------------------------------------------------------------- #
# Sending outside this PC (C-PG-1)
# --------------------------------------------------------------------------- #
WEBHOOK = "webhook"
DESTINATION = "destination"
MODEL_SERVER = "model_server"
WEB_SERVICE = "web_service"
EXTENSION = "extension"
MEMORY_BASE = "memory_base"
SPEECH_SERVER = "speech_server"
BACKUP_COPY = "backup_copy"
APP = "app"

#: How each place is asked, as a code the screen can sort by.
ASK_ONCE = "once"
ASK_EVERY_TIME = "every_time"
ASK_OWN_SETTING = "own_setting"

KIND_LABELS = {
    WEBHOOK: "Webhook",
    DESTINATION: "Destination",
    MODEL_SERVER: "Model server",
    WEB_SERVICE: "Web service",
    EXTENSION: "Extension",
    MEMORY_BASE: "Memory base",
    SPEECH_SERVER: "Speech-to-text server",
    BACKUP_COPY: "Backup copy folder",
    APP: "App using Iron Jarvis",
}

#: The page each kind is changed on (the doors' own addresses).
PAGES = {
    WEBHOOK: "/webhooks",
    DESTINATION: "/channels",
    MODEL_SERVER: "/fleet",
    WEB_SERVICE: "/connections",
    EXTENSION: "/tools",
    MEMORY_BASE: "/memory",
    APP: "/chat",
}

#: The tool that points each kind somewhere (``tools/destinations.py``): when
#: its own check asks every time, the row says so.
PLACE_TOOLS = {
    WEBHOOK: "webhook_update",
    MODEL_SERVER: "model_server_add",
    WEB_SERVICE: "web_service_add",
    EXTENSION: "mcp_server_add",
    MEMORY_BASE: "memory_base_add",
    SPEECH_SERVER: "config_change",
    BACKUP_COPY: "config_change",
}

NEW_PLACE_ASKS = "Pointing it somewhere new asks every time."
ON_THIS_PC_PROGRAM = "a program on this PC"
WEBHOOK_ASKED = "Approved when it was set up: its events go out without asking."
DESTINATION_ASKED = (
    "The alerts you picked go there without asking; a message from chat or an agent follows its "
    "approval setting ({level})."
)
SERVER_ASKED = "Approved when it was added: it gets your chats when it answers them."
SETTING_ASKED = "Approved when it was set: it gets {words}."
SERVICE_ASKED = "Reading from it follows its approval setting ({level}); anything else asks every time."
EXTENSION_AUTO_ASKED = "Its tools run without asking ({label} is on)."
EXTENSION_LEVEL_ASKED = "Its tools follow their approval setting ({level})."
EXTENSION_PROGRAM = " It runs on this PC and may reach the internet itself."
MEMORY_ASKED = "Approved when it was added: searches and what Jarvis saves there go without asking."
APP_ASKED = (
    "Approved when you connected it: what it looks up (titles, folders, file names, short summaries) "
    "goes to {model} with the rest of that chat; client work and Local only work are never shared."
)
APP_MODELS = {"codex": "Codex's model"}
APP_DEFAULT_MODEL = "Claude's model"

#: Destination types, in the service's own name.
DESTINATION_NAMES = {
    "slack": "Slack", "discord": "Discord", "telegram": "Telegram", "email": "Email", "sms": "Text message",
    "teams": "Microsoft Teams", "whatsapp": "WhatsApp", "signal": "Signal", "matrix": "Matrix",
    "pushover": "Pushover", "ntfy": "ntfy", "webhook": "A web address",
}
#: Memory bases outside this PC, by kind (a folder base is on this PC).
MEMORY_PLACES = {"notion": "Notion", "google_drive": "Google Drive", "onedrive": "OneDrive", "dropbox": "Dropbox"}


def _host(address: Any) -> str:
    """The host an address names, lower case, or "" (none, or on this PC).
    Never a path, a query, a user name, a password or a port."""
    text = str(address or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = "http://" + text
    try:
        host = (urlsplit(text).hostname or "").strip().lower().rstrip(".")
    except ValueError:
        return ""
    if not host or host == "localhost" or host.endswith(".localhost"):
        return ""
    try:
        ip = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return host
    mapped = getattr(ip, "ipv4_mapped", None)
    if (mapped or ip).is_loopback or (mapped or ip).is_unspecified:
        return ""
    return host


def _share_host(folder: Any) -> str:
    """The machine a network-share folder (``\\\\host\\share``) is on, or ""."""
    text = str(folder or "").strip().replace("/", "\\")
    if not text.startswith("\\\\"):
        return ""
    return _host(text.lstrip("\\").split("\\", 1)[0])


def _level(config: Any, tool: str) -> str:
    """A tool's own approval setting, in the Settings row's words."""
    from ...core.config import default_permissions
    from ...settings import schema

    try:
        value = str((getattr(config, "permissions", None) or {}).get(tool) or "")
    except Exception:  # noqa: BLE001
        value = ""
    value = value or default_permissions().get(tool, "ask")
    try:
        row = next(s for s in schema.SETTINGS if s.key == "permissions.{tool}")
        return dict(row.options).get(value, value)
    except Exception:  # noqa: BLE001 — the value itself, never a guess
        return value


def _new_place(kind: str, words: str) -> str:
    from ...tools import destinations

    tool = PLACE_TOOLS.get(kind, "")
    return f"{words} {NEW_PLACE_ASKS}" if tool and destinations.still_asks(tool) else words


def _row(kind: str, name: Any, where: str, ask: str, asked: str, href: str = "") -> dict[str, str]:
    return {
        "kind": kind,
        "kind_label": KIND_LABELS[kind],
        "name": " ".join(str(name or "").split())[:80],
        "where": where,
        "ask": ask,
        "asked": asked,
        "href": href or PAGES.get(kind, ""),
    }


def _webhooks(d: Any, config: Any) -> list[dict[str, str]]:
    from sqlmodel import select

    from ...core.db import session_scope
    from ...webhooks.models import WebhookRecord

    out = []
    with session_scope(d.platform.engine) as db:
        rows = list(db.exec(select(WebhookRecord).where(WebhookRecord.direction == "outbound")))
    for rec in sorted(rows, key=lambda r: str(r.slug)):
        host = _host(rec.target_url) if rec.enabled else ""
        if host:
            out.append(_row(WEBHOOK, rec.slug, host, ASK_ONCE, _new_place(WEBHOOK, WEBHOOK_ASKED)))
    return out


def _destinations(d: Any, config: Any) -> list[dict[str, str]]:
    channels = (getattr(config, "comm", None) or {}).get("channels") or {}
    level = _level(config, "notify")
    out = []
    for name in sorted(channels):
        spec = channels.get(name) or {}
        kind = str(spec.get("type") or "").strip().lower() if isinstance(spec, dict) else ""
        where = DESTINATION_NAMES.get(kind, kind.capitalize())
        out.append(_row(DESTINATION, name, where, ASK_OWN_SETTING, DESTINATION_ASKED.format(level=level)))
    return out


def _model_servers(d: Any, config: Any) -> list[dict[str, str]]:
    fleet = getattr(d, "fleet", None)
    out = []
    # The two addresses set in Settings are listed by their setting below.
    for node in fleet.routable_nodes() if fleet is not None else []:
        host = _host(node.base_url) if getattr(node, "source", "") != "config" else ""
        if host:
            out.append(_row(MODEL_SERVER, node.label or node.id, host, ASK_ONCE,
                            _new_place(MODEL_SERVER, SERVER_ASKED)))
    return out


def _setting_places(d: Any, config: Any) -> list[dict[str, str]]:
    """Every setting that sets a place (``SettingDef.destination``)."""
    from ...settings import schema
    from ...tools import destinations

    labels = {s.key: s.label for s in schema.SETTINGS}
    out = []
    for key, words in sorted(destinations.destination_settings().items()):
        value = getattr(config, key, "") or ""
        if key in destinations.FOLDER_SETTINGS:
            kind, host = BACKUP_COPY, _share_host(value)
        else:
            kind = SPEECH_SERVER if key == "voice_transcribe_base_url" else MODEL_SERVER
            host = _host(value)
        if host:
            out.append(_row(kind, labels.get(key, key), host, ASK_ONCE,
                            _new_place(kind, SETTING_ASKED.format(words=words)), href=f"/settings?focus={key}"))
    return out


def _web_services(d: Any, config: Any) -> list[dict[str, str]]:
    from ...integrations.hookups import rest_hookups

    reg = getattr(d.platform, "integrations", None)
    level = _level(config, "rest_call")
    out = []
    for spec in rest_hookups(reg) if reg is not None else []:
        rec = reg.get_record(spec.id)
        if rec is None or not getattr(rec, "enabled", False):
            continue
        host = _host(reg.stored_config(spec.id).get("base_url"))
        if host:
            out.append(_row(WEB_SERVICE, getattr(spec, "display_name", "") or spec.id, host, ASK_EVERY_TIME,
                            SERVICE_ASKED.format(level=level)))
    return out


def _extensions(d: Any, config: Any) -> list[dict[str, str]]:
    from ...mcp.tools import pack_enabled, pack_grants_auto
    from ...settings import schema

    packs = [s for s in (getattr(config, "mcp_servers", None) or []) if isinstance(s, dict)]
    # The same reading as the boot step's: either flag opens every extension.
    if bool(getattr(config, "mcp_auto_approve", False)) or any(pack_grants_auto(s) for s in packs):
        label = next((s.label for s in schema.SETTINGS if s.key == "mcp_auto_approve"), "")
        asked, ask = EXTENSION_AUTO_ASKED.format(label=label), ASK_ONCE
    else:
        asked, ask = EXTENSION_LEVEL_ASKED.format(level=_level(config, "mcp_call")), ASK_OWN_SETTING
    out = []
    for row in packs:
        if not pack_enabled(row):
            continue
        if str(row.get("transport") or "stdio").lower() == "stdio":
            where, words = ON_THIS_PC_PROGRAM, asked + EXTENSION_PROGRAM
        else:
            where, words = _host(row.get("url")), _new_place(EXTENSION, asked)
        if where:
            out.append(_row(EXTENSION, row.get("name"), where, ask, words))
    return out


def _memory_bases(d: Any, config: Any) -> list[dict[str, str]]:
    from ...ltm.sources import CustomSourceStore

    out = []
    for rec in CustomSourceStore(d.platform.engine).list():
        kind = str(rec.kind or "")
        if kind in MEMORY_PLACES:
            where = MEMORY_PLACES[kind]
        elif kind == "ssh":
            where = _host(rec.host)
        elif kind == "http_rag":
            where = _host(rec.endpoint_url)
        elif kind == "mcp" and "://" in str(rec.path or ""):
            where = _host(rec.path)
        else:
            where = ""  # a folder on this PC, or a program started on it
        if where:
            out.append(_row(MEMORY_BASE, rec.name, where, ASK_ONCE, _new_place(MEMORY_BASE, MEMORY_ASKED)))
    return out


def _apps(d: Any, config: Any) -> list[dict[str, str]]:
    conns = getattr(d.platform, "app_connections", None)
    out = []
    for row in conns.status() if conns is not None else []:
        if row.get("connected"):
            model = APP_MODELS.get(str(row.get("app") or ""), APP_DEFAULT_MODEL)
            out.append(_row(APP, row.get("label") or row.get("app"), str(row.get("label") or ""), ASK_ONCE,
                            APP_ASKED.format(model=model)))
    return out


#: ``(kinds it reports as unread, reader)`` in the order the row lists them.
READERS: tuple[tuple[tuple[str, ...], Callable[[Any, Any], list[dict[str, str]]]], ...] = (
    ((MODEL_SERVER,), _model_servers),
    ((MODEL_SERVER, SPEECH_SERVER, BACKUP_COPY), _setting_places),
    ((WEB_SERVICE,), _web_services),
    ((EXTENSION,), _extensions),
    ((APP,), _apps),
    ((MEMORY_BASE,), _memory_bases),
    ((WEBHOOK,), _webhooks),
    ((DESTINATION,), _destinations),
)


def outside_places(d: Any) -> dict[str, Any]:
    """C-PG-1: ``{places, unread}`` — every live place outside this PC that
    Iron Jarvis sends to. BLOCKING (database and file reads, no network);
    never raises: a store that cannot be read is named in ``unread``."""
    config = getattr(getattr(d, "platform", None), "config", None)
    places: list[dict[str, str]] = []
    unread: list[str] = []
    for kinds, reader in READERS:
        try:
            places.extend(reader(d, config))
        except Exception:  # noqa: BLE001 — said, never shown as nothing
            unread.extend(k for k in kinds if k not in unread)
    return {"places": places, "unread": unread}


def access_facts(config: Any) -> dict[str, Any]:
    """The facts the Settings block renders. Never raises."""
    from ...core import fs_policy

    install = bool(os.environ.get(ALLOWLIST_ENV, "").strip())
    try:
        chosen = fs_policy.setting_folders()
    except Exception:  # noqa: BLE001 — an unreadable setting is reported empty
        chosen = []
    limited = install or bool(chosen)
    folders: list[str] = []
    if limited:
        try:
            folders = [
                str(p)
                for p in fs_policy.allowlist_roots()
                # The "nothing satisfies both limits" marker is not a folder
                # anyone chose: an empty list says "reads no files".
                if p != fs_policy.NOTHING_READABLE
            ]
        except Exception:  # noqa: BLE001 — an unreadable list is reported empty
            folders = []
    try:
        chat_dir = str(config.chat_files_dir)
    except Exception:  # noqa: BLE001 — no Documents folder resolvable
        chat_dir = ""
    return {
        "reads": {"limited": limited, "folders": folders, "chosen": chosen, "install": install},
        "writes": {"chat_files_dir": chat_dir},
    }


def register(app: FastAPI, d) -> None:
    """Attach the route to *app*; ``d`` is the create_app deps object."""

    @app.get("/settings/access")
    def settings_access() -> dict[str, Any]:
        """What chat and agents can read and where they save, from the rules
        that enforce it: ``{reads: {limited, folders, chosen, install},
        writes: {chat_files_dir}, sends: {places, unread}}`` (C-PG-1: where it
        sends outside this PC). Read-only; never fails."""
        return {**access_facts(getattr(d.platform, "config", None)), "sends": outside_places(d)}
