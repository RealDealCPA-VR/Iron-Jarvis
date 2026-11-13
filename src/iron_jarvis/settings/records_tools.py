"""Extensions and web services from chat (v1.448.0, Wave A "chat configures
everything", item 4 of docs/PRODUCT-READINESS-RESULTS.md 0h.5).

Two doors existed only on pages: the Tools page's "Add" (paste a server's
config, a command line or an address — ``mcp/import_config.parse_any``, then
``mcp/pack_door.add_pack``, THE one add door) and Settings › Connections ›
"Your web services" (a REST hookup: a name, an address, a key —
``integrations/hookups.add_hookup``, lifted out of ``POST /integrations``).
These tools open them from chat on the record-tool pattern
(``settings.records``): each ASKS FIRST on the approval card and leaves an
Undo on the reply that puts back ONLY what the call made and refuses in words
over newer work.

* ``mcp_server_add`` — "add this server: npx -y @modelcontextprotocol/
  server-filesystem C:\\Clients", "connect this tool server at
  https://mcp.example.com/sse", a pasted config block (every dialect
  ``parse_any`` reads), or one of the prebuilt catalog's names
  ("filesystem"). The card is ONCE-ONLY and shows the command line it will
  start (masked), like ``app_import``'s. A CREDENTIAL IS NEVER AN ARGUMENT: a
  secret-named env key, header, auth value, argument or url key found in the
  config with a VALUE is refused before any card; one left blank, as a
  placeholder (``${input:token}``, ``<your token>``) or by name is asked on
  the SECURE card (``settings/credentials``: ``tool_server.<name>__<slot>`` →
  the vault name the pack door stores — ``mcp.<name>.<KEY>`` and its header /
  auth / argument / url kin), and the server is added ONLY once every asked
  value is saved (:func:`on_secret_saved`, called by the credential store).
  Nothing of the server touches the disk before that — the plan waits in
  memory (:data:`_PENDING`, an hour at most; a restart forgets it and the card
  then says so). Undo removes the server THIS call made (``remove_pack``);
  one edited since is refused in words; a card not yet saved is withdrawn.
  Refused in words: a locked card's program (Microsoft 365), a name in use,
  the same server already added under another name, a launcher this PC lacks
  (``mcp/tools.resolve_launcher``'s own words name what to install), a
  network path as the folder.
* ``web_service_add`` — "hook up our firm's pricing API at
  https://api.vrtax.local/v1 so you can look up fees", "add a web service
  called Pricing at <url> with a bearer key". The key goes on the secure card
  only (``web_service.<id>`` → ``integration_<id>_token``, the name the page
  gives it); a private address is allowed — it is the user's own network — so
  the hookup is saved with ``allow_internal`` and the card SAYS where it
  points. A ``\\\\host`` or device spelling is refused by its text before
  anything is looked at. Undo removes the service this call made (its row,
  its saved key, its spec); one changed since is refused.

Words a person reads say "extension" (VOCABULARY.md row 22; v1.455.0 Wave E retired "tool server") and "web service" — never the
engine's words. Every snapshot, vault write and add is BLOCKING; the tools
run them through ``asyncio.to_thread``.
"""

from __future__ import annotations

import asyncio
import contextvars
import copy
import json
import logging
import re
import threading
import time
from functools import lru_cache
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

from ..core.ids import new_id
from ..tools.base import SpendCheck, ToolContext, ToolResult
from . import records as R
from .records_connections import (
    _KEY_ARGS,
    _SECRET_ARG_NAME,
    _leaves_a_local_only_chat,
    clean_address,
    place_words,
)

log = logging.getLogger("ironjarvis.settings")

#: The tools this module adds and their default permission (mirrored, row
#: for row, in ``records.RECORD_TOOL_TIERS`` and ``core.config``'s table).
TOOLS_TOOL_TIERS: dict[str, str] = {
    "mcp_server_add": "ask",
    "web_service_add": "ask",
}

#: The record kinds this module adds to ``records._SNAP`` / ``records.restore``.
TOOL_SERVER = "tool_server"
WEB_SERVICE = "web_service"
KINDS = (TOOL_SERVER, WEB_SERVICE)

#: The secure card's name prefixes (``settings/credentials.resolve_secret``).
TOOL_SERVER_SECRET = "tool_server."
WEB_SERVICE_SECRET = "web_service."

#: How long a server waits for its card (memory only).
PENDING_TTL_S = 3600.0

_NOTHING = SpendCheck(ask=False, cost_note="changes nothing by itself")

# --- words -------------------------------------------------------------------
KEY_NOT_IN_CHAT = (
    "A key, token or password is never passed in the chat. Give the server's config without the "
    "value (leave the key's value out or blank): the user pastes it on the secure card."
)
NO_CONFIG = (
    "Give the server's config: the command line (for example `npx -y @modelcontextprotocol/"
    "server-filesystem C:\\Clients`), its address (https://…/sse or …/mcp), a pasted config block, "
    "or one of the prebuilt names (filesystem, fetch, memory, git, playwright, box, github)."
)
SEVERAL_SERVERS = "That config holds {n} servers ({names}): add them one at a time, naming the one to add."
NAME_IN_USE = "An extension called {name!r} is already added. Choose another name, or remove that one first."
SAME_SERVER = "That server is already added as {other!r}."
BAD_NAME = (
    "An extension's name starts with a letter and uses letters, digits, - or _ (at most 40 characters)."
)
NETWORK_FOLDER = "A folder on a network share (\\\\host\\…) is not used as an extension's folder."
VALUE_NEEDED = (
    "The config leaves {names} to be filled in. Pass them in `values` (they are not keys: a folder, a "
    "repository path) — ask the user for them in chat."
)
VALUE_IS_A_KEY = (
    "`values` carries {name}, which is a key or token: never pass it. Leave it out and the user pastes "
    "it on the secure card."
)
VALUE_LOOKS_SECRET = "The value given for {name} looks like a key or token: never pass one in the chat."
WAITING_WORDS = (
    "Nothing is added yet: the user pastes {what} on the secure card below (never in the chat). Once "
    "{saved}, the extension {name!r} is added and starts."
)
NOT_WAITING = (
    "That server is no longer waiting to be added (Iron Jarvis restarted, or the card expired): ask "
    "for it again in chat."
)
#: A2 review: the value a card saved for a server nothing waits for any more
#: is NOT kept — an orphan vault row the user cannot see is worse than a
#: second paste.
NOT_WAITING_DROPPED = NOT_WAITING + " The value you pasted was not kept."
WAITING_ALREADY = (
    "An extension called {name!r} is already waiting for its key on a secure card: paste it there, or "
    "press Undo on that card first."
)
WITHDRAWN = "undo: withdrew the card for the extension '{name}'; nothing had been added"
NEVER_ADDED = "The extension {name!r} was never added (or was removed since), so there is nothing to undo."
CHANGED_SINCE = (
    "The extension {name!r} was changed since, so this Undo would undo that too: remove it on its row "
    "in Settings › Tools instead."
)
NOT_CARRIED = "a web service's key is never passed in the chat: set `needs_key` true and the user pastes it on the secure card"
SERVICE_NAME_IN_USE = "A web service called {name!r} is already saved. Choose another name."
SERVICE_WORDS_LOOK_SECRET = (
    "The web service's name or description looks like a key or token: never pass one in the chat — the "
    "user pastes it on the secure card."
)
SERVICE_SAME_ADDRESS = "A web service at that address is already saved as {other!r}."
SERVICE_NETWORK = (
    "That is a network share or device path, not a web address. Give the service's address starting "
    "with http:// or https://."
)
SERVICE_CHANGED = (
    "The web service {name!r} was changed since, so this Undo would undo that too: change it in "
    "Settings › Connections instead."
)
SERVICE_GONE = "The web service {name!r} was removed since, so there is nothing to undo."
SERVICE_ADDED = "Added the web service “{name}” at {address} ({where}). Jarvis can call it with rest_call now{key}."

#: Readiness 0j.5 item 18: the service's OWN Test (the Connections page's
#: Test button, ``integrations.test``) runs once it is saved — and once its
#: key is, for a keyed one — and its verdict is said in words.
SERVICE_CHECK_TIMEOUT_S = 20.0
#: Run the check at all (the test suite turns it off: tests/conftest.py).
AUTO_CHECK = True
SERVICE_ANSWERING = "Added and answering: “{name}” replied to a test request."
SERVICE_NOT_ANSWERING = "Added, but it is not answering: {why}."
SERVICE_CHECK_LATER = "It is checked once its key is saved."
#: Wave F review: under Local only the check is a request to an address off
#: this network that no card asked about (web_service_add is not a "reaches
#: outside" tool), so it is not sent.
SERVICE_NOT_TESTED_LOCAL_ONLY = (
    "It was not tested: this chat is kept on this network. Press Test beside it "
    "in Settings › Connections to try it."
)


def _check_reason(detail: str) -> str:
    """The Test's detail ("GET https://… -> 401", "ConnectError: …") as the
    reason a person reads. Never the address or an exception's class."""
    import re as _re

    text = str(detail or "")
    m = _re.search(r"->\s*(\d{3})", text)
    if m:
        code = int(m.group(1))
        if code in (401, 403):
            return "it refused the test request (it may need a key, or its key was refused)"
        if code == 404:
            return "nothing answered at that address (404); check the address"
        if code == 429:
            return "it is busy right now (429); try again later"
        if code >= 500:
            return f"it had a problem of its own ({code})"
        return f"it answered with an error ({code})"
    if "refused unsafe" in text:
        return "Iron Jarvis would not send a request to that address"
    if any(w in text.lower() for w in ("timeout", "timed out")):
        return "it did not answer in time"
    return "it could not be reached"


def check_service(platform: Any, iid: str) -> tuple[bool, str]:
    """``(answering, words)`` from the saved service's own Test (the same
    ``integrations.test`` the Connections page's Test button runs: one GET of
    its address, its key as a bearer token when it has one, the same SSRF
    guard). BLOCKING (one bounded HTTP GET) — call it off the loop. Never
    raises; the words never carry the key (the Test's detail never holds it)."""
    shown = iid
    try:
        reg = getattr(platform, "integrations", None)
        spec = reg.get_spec(iid) if reg is not None else None
        shown = str(getattr(spec, "display_name", "") or iid)
        if reg is None:
            return False, SERVICE_NOT_ANSWERING.format(why="it could not be tested here")
        out = reg.test(iid, platform.secrets.get)
    except Exception:  # noqa: BLE001 — a test never fails the add
        return False, SERVICE_NOT_ANSWERING.format(why="it could not be tested")
    if out.get("ok"):
        return True, SERVICE_ANSWERING.format(name=shown)
    return False, SERVICE_NOT_ANSWERING.format(why=_check_reason(str(out.get("detail") or "")))


async def check_service_bounded(platform: Any, iid: str) -> tuple[bool, str]:
    """:func:`check_service` off the loop, within :data:`SERVICE_CHECK_TIMEOUT_S`."""
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(check_service, platform, iid), SERVICE_CHECK_TIMEOUT_S
        )
    except asyncio.TimeoutError:
        return False, SERVICE_NOT_ANSWERING.format(why="it did not answer in time")


def check_service_within(platform: Any, iid: str) -> tuple[bool, str]:
    """:func:`check_service` on a daemon thread, waited for at most
    :data:`SERVICE_CHECK_TIMEOUT_S` (Wave F review). BLOCKING, for a caller
    that is already off the loop (the secure card's save): a service that
    trickles its answer must not hold the card's Save for as long as it likes
    (the Test's GET has per-phase timeouts, not a whole-answer bound)."""
    import threading

    box: list[tuple[bool, str]] = []

    def _run() -> None:
        try:
            box.append(check_service(platform, iid))
        except Exception:  # noqa: BLE001 — check_service never raises; belt and braces
            box.append((False, SERVICE_NOT_ANSWERING.format(why="it could not be tested")))

    t = threading.Thread(target=_run, name="web-service-check", daemon=True)
    t.start()
    t.join(SERVICE_CHECK_TIMEOUT_S)
    if box:
        return box[0]
    return False, SERVICE_NOT_ANSWERING.format(why="it did not answer in time")
SERVICE_KEY_WORDS = (
    " It needs a key: the user pastes it on the secure card below (never in the chat); calls carry it "
    "as a bearer token once it is saved"
)
#: v1.455.0 (readiness Wave H item 31): how calls carry the key, by kind.
SERVICE_KEY_HOW = {
    "bearer": "as a bearer token",
    "header": "in the {header} header",
    "basic": "as the password of a sign-in as {user}",
}


def service_key_words(kind: str, header: str = "", user: str = "") -> str:
    """The added-line's key sentence for one way of sending the key."""
    how = SERVICE_KEY_HOW.get(kind, SERVICE_KEY_HOW["bearer"]).format(header=header, user=user)
    return (
        " It needs a key: the user pastes it on the secure card below (never in the chat); calls carry it "
        f"{how} once it is saved"
    )

#: v1.448.0: a few shapes that are PLACEHOLDERS in a pasted config, never
#: the value ("<your token>", "YOUR_TOKEN_HERE", "xxx", "...", "changeme").
_PLACEHOLDER_RX = re.compile(
    r"^\s*(?:<[^<>]{1,80}>|\[[^\[\]]{1,80}\]|\{\{[^{}]{1,80}\}\}|your[-_ ][\w -]{0,60}|"
    r"(?:insert|enter|paste|put|replace|add)[-_ ][\w -]{0,60}|x{3,}|\.{3,}|…|changeme|change[-_ ]me|"
    r"to[-_ ]?do|tbd|n/?a|none|null|redacted|\*{3,}|(?:api[-_ ]?)?(?:key|token|secret|password)[-_ ]?"
    r"(?:here|goes[-_ ]here|value)?)\s*$",
    re.IGNORECASE,
)


def _fold(text: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


def _q(text: Any) -> str:
    return f"“{' '.join(str(text or '').split())[:60]}”"


def _placeholder_like(value: Any) -> bool:
    v = str(value or "")
    return not v.strip() or bool(_PLACEHOLDER_RX.match(v)) or "${" in v


# --- the descriptor this call captured (records_connections' way) -----------
_DESC: contextvars.ContextVar["dict[str, Any] | None"] = contextvars.ContextVar("ij_tools_desc", default=None)


def _stash(desc: "dict[str, Any] | None") -> "dict[str, Any] | None":
    _DESC.set(desc)
    return desc


def _note(**fields: Any) -> None:
    """Add ``fields`` to the descriptor this call captured (the registry
    journals it only after a SUCCESSFUL execute). No-op with no capture."""
    desc = _DESC.get()
    if not desc:
        return
    try:
        meta = json.loads(desc.get("pre_inline") or "{}")
    except (TypeError, ValueError):
        return
    if not isinstance(meta, dict):
        return
    meta.update(fields)
    desc["pre_inline"] = json.dumps(meta, default=str)


# --- secret-named arguments (the Wave 1 rule) --------------------------------
def _secret_args(args: dict[str, Any], declared: frozenset[str]) -> list[str]:
    out: list[str] = []
    for k, v in (args or {}).items():
        if not isinstance(k, str) or k in declared:
            continue
        if k in _KEY_ARGS or _SECRET_ARG_NAME.search(k):
            if v is not None and v is not False and v is not True and str(v).strip():
                out.append(k)
    return out


def _network_spelling(text: Any) -> bool:
    """A UNC share, a ``//host`` spelling without a scheme, or a device path
    (``\\\\?\\``, ``\\\\.\\``) — refused by its TEXT, never looked at (the
    v1.379.0 rule: resolving a share opens an SMB connection)."""
    t = str(text or "").strip()
    if not t:
        return False
    if t.startswith(("\\\\", "//")) and "://" not in t[:10]:
        return True
    return "\\\\?\\" in t or "\\\\.\\" in t or t.lower().startswith(("file:", "smb:", "unc:"))


# =========================================================================== #
# Extensions
# =========================================================================== #
@lru_cache(maxsize=1)
def _catalog_specs() -> dict[str, Any]:
    """The prebuilt catalog's rows as ``PackSpec``s by id and folded name, a
    ``<placeholder>`` argument turned into a ``${input:placeholder}`` ask.
    Never raises (an unreadable catalog offers nothing)."""
    from ..mcp.import_config import PackSpec, mark_placeholders

    out: dict[str, Any] = {}
    try:
        from ..connectors.catalog import mcp_catalog_rows

        for row in mcp_catalog_rows():
            rid = str(row.get("id") or "")
            if not rid:
                continue
            args = [re.sub(r"^<([\w-]+)>$", r"${input:\1}", str(a)) for a in (row.get("args") or [])]
            spec = PackSpec(
                name=rid,
                transport=str(row.get("transport") or "stdio"),
                command=str(row.get("command") or ""),
                args=args,
                env={str(k): str(v) for k, v in dict(row.get("env") or {}).items()},
                url=str(row.get("url") or ""),
                source="catalog",
                origin="catalog",
            )
            mark_placeholders(spec)
            out[rid] = spec
            out[_fold(row.get("name"))] = spec
    except Exception:  # noqa: BLE001
        return out
    return out


_URL_RX = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_LAUNCH_RX = re.compile(
    r"\b(?:npx|uvx|pipx|bunx|docker|node|python3?|uv|deno)\b[^\n]*", re.IGNORECASE
)


def parse_server_config(text: Any) -> list[Any]:
    """Every server ``text`` describes: the whole text as ``parse_any`` reads
    it, else a config block, an address or a command line inside a sentence
    ("add this server: npx -y …"), else a prebuilt catalog name. Never
    raises; ``[]`` for nothing recognisable."""
    from ..mcp.import_config import mark_placeholders, parse_any

    raw = str(text or "").strip().strip("`")
    if not raw:
        return []
    specs = parse_any(raw)
    if not specs:
        # A2 review: the first "{" to the last "}" by find/rfind — the old
        # ``\{.*\}`` search re-scanned the text from every brace with no
        # closing one (60,000 braces: 1.2 s, on the loop in redact_args).
        i, j = raw.find("{"), raw.rfind("}")
        if i != -1 and j > i:
            specs = parse_any(raw[i:j + 1])
    if not specs:
        m = _URL_RX.search(raw)
        if m is not None:
            specs = parse_any(m.group(0).rstrip(".,;)"))
    if not specs:
        m = _LAUNCH_RX.search(raw)
        if m is not None:
            specs = parse_any(re.split(r"[.!?](?:\s|$)", m.group(0), 1)[0].strip())
    if not specs:
        cat = _catalog_specs().get(raw.lower()) or _catalog_specs().get(_fold(raw))
        if cat is not None:
            specs = [copy.deepcopy(cat)]
    for spec in specs:
        mark_placeholders(spec)
    return specs


def _slot(where: str, key: Any = "") -> str:
    return f"{where}__{key}" if key not in ("", None) else where


def _slot_vault_name(pack: str, slot: str) -> str:
    """The vault row a card slot of ``pack`` lands in — the pack door's own
    names (``mcp/secrets``), so the saved value IS the value the server
    launches with."""
    from ..mcp.secrets import URL_KEY, arg_key, vault_name

    where, _, key = slot.partition("__")
    if where == "env":
        return vault_name(pack, key)
    if where == "header":
        return vault_name(pack, f"header.{key}")
    if where == "auth":
        return vault_name(pack, "auth.value")
    if where == "arg":
        return vault_name(pack, arg_key(int(key)))
    if where == "url":
        return vault_name(pack, URL_KEY)
    raise ValueError(f"unknown slot {slot!r}")


def _slot_label(pack: str, slot: str) -> str:
    where, _, key = slot.partition("__")
    if where == "env":
        return f"{pack}: {key}"
    if where == "header":
        return f"{pack}: the {key} header"
    if where == "auth":
        return f"{pack}: its access token"
    if where == "arg":
        return f"{pack}: the key its command line takes"
    return f"{pack}: its address's key"


class Plan:
    """What one ``mcp_server_add`` call worked out: the spec with every
    placeholder the model may fill filled in, the slots the secure card asks
    for, and the row the add is expected to store. Pure after construction."""

    def __init__(self, platform: Any, args: dict[str, Any]) -> None:
        from ..mcp.import_config import NAME_RX, safe_name
        from ..mcp.secrets import _secret_flag, is_secret_key, split_args, split_url

        self.problem = ""
        self.spec: Any = None
        self.name = ""
        self.slots: list[str] = []
        #: ask name → slot, for a placeholder the card fills.
        self.fill_slots: dict[str, str] = {}
        #: Arg indexes the card fills (vaulted at the add).
        self.vaulted_args: list[int] = []
        self.values: dict[str, str] = {}
        config = str((args or {}).get("config") or "")
        specs = parse_server_config(config)
        if not specs:
            self.problem = NO_CONFIG
            return
        wanted = str((args or {}).get("name") or "").strip()
        if len(specs) > 1:
            pick = [s for s in specs if wanted and (_fold(s.name) == _fold(wanted) or _fold(safe_name(s.name)) == _fold(safe_name(wanted)))]
            if len(pick) != 1:
                self.problem = SEVERAL_SERVERS.format(n=len(specs), names=", ".join(s.name for s in specs))
                return
            specs = pick
        spec = copy.deepcopy(specs[0])
        name = safe_name(wanted) if wanted else (spec.name if NAME_RX.match(spec.name or "") else safe_name(spec.name))
        if not NAME_RX.match(name or ""):
            self.problem = BAD_NAME
            return
        spec.name = name
        cwd = str((args or {}).get("cwd") or "").strip()
        if cwd:
            spec.cwd = cwd
        if _network_spelling(spec.cwd) or any(_network_spelling(a) for a in spec.args):
            self.problem = NETWORK_FOLDER
            return
        values = {str(k): str(v) for k, v in dict((args or {}).get("values") or {}).items() if v is not None}
        for k, v in values.items():
            if is_secret_key(k) or k.lower() in _KEY_ARGS or _SECRET_ARG_NAME.search(k):
                self.problem = VALUE_IS_A_KEY.format(name=k)
                return
            if v.strip() and _looks_like_a_secret(v):
                self.problem = VALUE_LOOKS_SECRET.format(name=k)
                return
        # ---- a value IN the config is refused: the card is the only way in.
        for key, value in list(spec.env.items()):
            if is_secret_key(key):
                if value and not _placeholder_like(value):
                    self.problem = KEY_NOT_IN_CHAT
                    return
                spec.env[key] = ""
                self.slots.append(_slot("env", key))
            elif value and _looks_like_a_secret(value):
                # A2 review: a token under an innocent key (``FOLDER:
                # ghp_…``) would be stored PLAIN — the door vaults by name.
                self.problem = KEY_NOT_IN_CHAT
                return
        for key, value in list(spec.headers.items()):
            if is_secret_key(key):
                if value and not _placeholder_like(value):
                    self.problem = KEY_NOT_IN_CHAT
                    return
                spec.headers[key] = ""
                self.slots.append(_slot("header", key))
            elif value and _looks_like_a_secret(value):
                self.problem = KEY_NOT_IN_CHAT
                return
        if spec.auth is not None:
            if spec.auth.get("value") and not _placeholder_like(spec.auth.get("value")):
                self.problem = KEY_NOT_IN_CHAT
                return
            spec.auth["value"] = ""
            self.slots.append(_slot("auth"))
        if spec.transport == "stdio":
            _shown, arg_values = split_args(list(spec.args))
            # A2 review: a PLACEHOLDER after a secret flag (``--token
            # ${input:token}``) is asked on the card, not refused as a value.
            if any(not _placeholder_like(spec.args[i]) for i in arg_values):
                self.problem = KEY_NOT_IN_CHAT
                return
        else:
            _shown_url, url_value = split_url(spec.url)
            # A2 review: ``?key=…`` — a secret-named query parameter is a
            # credential ``mask`` does not know; the door would store it plain.
            if url_value or _secret_query_names(spec.url):
                self.problem = KEY_NOT_IN_CHAT
                return
        # ---- placeholders: a secret-shaped name is a card slot, the rest `values`.
        needed: list[str] = []
        for fill in list(spec.fills):
            where = str(fill.get("where") or "")
            key = fill.get("key")
            names = dict(fill.get("names") or {})
            for ph, ask in names.items():
                ask = str(ask)
                if where == "env" and is_secret_key(str(key)):
                    continue  # already a slot (the env key itself)
                if where == "header" and is_secret_key(str(key)):
                    continue
                after_flag = (
                    where == "arg" and isinstance(key, int) and key > 0 and _secret_flag(spec.args[key - 1])
                )
                if is_secret_key(ask) or after_flag:
                    slot = _slot("arg", key) if where == "arg" else _slot(where, key)
                    if slot not in self.slots:
                        self.slots.append(slot)
                    self.fill_slots[ask] = slot
                    if where == "arg" and isinstance(key, int):
                        self.vaulted_args.append(int(key))
                elif ask in values:
                    continue
                else:
                    needed.append(ask)
        if needed:
            self.problem = VALUE_NEEDED.format(names=", ".join(dict.fromkeys(needed)))
            return
        self.values = {k: v for k, v in values.items() if k not in self.fill_slots}
        # Fill the non-secret placeholders now (a COPY; the card's values come later).
        from ..mcp.import_config import fill_placeholders

        filled = copy.deepcopy(spec)
        missing = fill_placeholders(filled, {**self.values, **{a: "" for a in self.fill_slots}})
        missing = [m for m in missing if m not in self.fill_slots]
        if missing:
            self.problem = VALUE_NEEDED.format(names=", ".join(missing))
            return
        for i, a in enumerate(filled.args):
            if i not in self.vaulted_args and a and _looks_like_a_secret(a):
                self.problem = KEY_NOT_IN_CHAT
                return
        self.spec = spec
        self.name = name
        # ---- refusals the door would give: said here, before any card.
        from ..connectors.locked_pack import locked_elsewhere_words, unlocked_copy

        app = unlocked_copy({"name": name, "command": spec.command, "args": list(spec.args), "url": spec.url})
        if app:
            self.problem = locked_elsewhere_words(app)
            return
        if R.snap_app(platform, name) is not None:
            self.problem = NAME_IN_USE.format(name=name)
            return
        if pending_for(name) is not None:
            # A2 review: a second plan under a WAITING name would be the one
            # the first card's save adds — refused until that card is answered.
            self.problem = WAITING_ALREADY.format(name=name)
            return
        other = same_server_as(platform, spec)
        if other:
            self.problem = SAME_SERVER.format(other=other)
            return
        if spec.transport == "stdio":
            from ..mcp.tools import resolve_launcher

            try:
                resolve_launcher(spec.command)
            except Exception as exc:  # noqa: BLE001 — the launcher's own plain words
                self.problem = f"That server cannot start on this PC: {exc}"
                return

    # -- what the card and the Undo need ------------------------------------
    def preview(self) -> str:
        """The once-only card's sentence: what it starts or connects to,
        masked, and what the card asks for. Never a value."""
        from ..mcp.secrets import split_args, split_url

        spec = self.spec
        if spec.transport in ("http", "sse") or spec.url:
            shown_url, _ = split_url(spec.url)
            what = f"it connects to {shown_url}"
        else:
            shown_args, _ = split_args(list(self.filled_spec().args))
            what = f"it starts the program: {' '.join([spec.command, *shown_args]).strip()}"
        what = " ".join(str(R.masked_args(what)).split())
        if len(what) > 300:
            what = what[:299].rstrip() + "…"
        tail = ""
        if self.slots:
            tail = f" It needs {self._asked_words()}, which the user pastes on a secure card (never in the chat)."
        return f"Adds the extension {self.name}; {what}.{tail}"

    def _asked_words(self) -> str:
        parts = [_slot_label(self.name, s).split(": ", 1)[1] for s in self.slots]
        return ", ".join(parts)

    def filled_spec(self, card_values: dict[str, str] | None = None) -> Any:
        """A COPY of the spec with the model's ``values`` filled in and, when
        given, the card's values by slot (the add)."""
        from ..mcp.import_config import fill_placeholders

        spec = copy.deepcopy(self.spec)
        typed = dict(self.values)
        card = dict(card_values or {})
        for ask, slot in self.fill_slots.items():
            if slot in card:
                typed[ask] = card[slot]
        fill_placeholders(spec, typed)
        for slot, value in card.items():
            where, _, key = slot.partition("__")
            if where == "env":
                spec.env[key] = value
            elif where == "header":
                spec.headers[key] = value
            elif where == "auth" and spec.auth is not None:
                spec.auth["value"] = value
        for ask, slot in self.fill_slots.items():
            if slot not in card:
                where, _, key = slot.partition("__")
                if where == "arg" and isinstance(key, str) and key.isdigit() and int(key) < len(spec.args):
                    spec.args[int(key)] = f"${{input:{ask}}}"
        # A2 review: the placeholders are FILLED now — ``routes/agents.
        # _body_from_spec`` re-runs ``fill_placeholders`` for a spec whose
        # ``asks`` is non-empty, which dropped a card-saved env value and
        # refused an argument ("type a value for folder first": the catalog's
        # filesystem was never added).
        spec.fills = []
        return spec

    def expected(self) -> dict[str, Any]:
        """The stored row this add makes, in :func:`comparable` form — what
        the Undo compares the row with (pinned equal to the real row)."""
        from ..mcp.secrets import is_secret_key

        spec = self.filled_spec()
        row: dict[str, Any] = {"name": self.name, "transport": spec.transport}
        if spec.transport == "stdio":
            row["command"] = spec.command
            row["args"] = ["<vaulted>" if i in self.vaulted_args else a for i, a in enumerate(spec.args)]
            row["arg_secret_idx"] = sorted(self.vaulted_args)
            row["env"] = {k: v for k, v in spec.env.items() if not is_secret_key(k)}
            row["env_secrets"] = sorted(k for k in spec.env if is_secret_key(k))
            row["cwd"] = spec.cwd
        else:
            row["url"] = spec.url
            row["headers"] = {k: v for k, v in spec.headers.items() if not is_secret_key(k)}
            row["header_secrets"] = sorted(k for k in spec.headers if is_secret_key(k))
            row["auth"] = (
                {"header": spec.auth.get("header"), "format": spec.auth.get("format")} if spec.auth else None
            )
        row.update({"enabled": True, "auto_approve": False, "local_only": False, "locked": ""})
        return row


def masked_config(text: Any) -> str:
    """The ``config`` argument as the ledger, the card and the stream may
    show it: each server it describes with the pack door's own masking
    (``split_args``: the value after a secret-named flag; ``split_url``; a
    secret-named env / header / auth VALUE replaced whole), else the text
    through ``mask``. Never raises; never a value."""
    from ..detections.redact import MASK, mask
    from ..mcp.secrets import is_secret_key, split_args

    raw = str(text or "")
    try:
        specs = parse_server_config(raw)
    except Exception:  # noqa: BLE001
        specs = []
    if not specs:
        return _mask_text(raw)
    lines: list[str] = []
    for spec in specs:
        if spec.transport == "stdio":
            shown, _ = split_args(list(spec.args))
            line = " ".join([spec.command, *shown]).strip()
        else:
            line = _mask_url(spec.url)
        extras: list[str] = []
        for k, v in spec.env.items():
            extras.append(f"{k}={MASK if v and (is_secret_key(k) or _looks_like_a_secret(v)) else mask(v)}")
        for k, v in spec.headers.items():
            extras.append(f"{k}: {MASK if v and (is_secret_key(k) or _looks_like_a_secret(v)) else mask(v)}")
        if spec.auth and spec.auth.get("value"):
            extras.append(f"auth: {MASK}")
        lines.append(f"{spec.name}: {line}" + (f" ({'; '.join(extras)})" if extras else ""))
    return _mask_text(" | ".join(lines))


def _mask_text(text: str) -> str:
    """``mask`` plus the shapes it does not read: a secret-named query
    parameter in any address, a bare ``Bearer <token>``."""
    from ..detections.redact import MASK, mask

    out = _URL_RX.sub(lambda m: _mask_url(m.group(0)), mask(str(text or "")))
    return re.sub(r"(?i)\b(bearer)\s+" + _TOKEN_PART, lambda m: f"{m.group(1)} {MASK}", out)


#: A2 review: a header VALUE that is itself a credential whatever the
#: header is called ("X-Trace: Bearer <token>") — ``mask`` reads the
#: Bearer shape only behind an ``Authorization`` header.
#: The token part is at least 12 characters holding a digit or a mark —
#: "Basic accounting" and "Token Ring Project" are words, never a credential.
_TOKEN_PART = r"(?!\*{3})(?=\S*[\d=_\-.+/])\S{12,}"
_CREDENTIAL_VALUE_RX = re.compile(r"^\s*(?:bearer|basic|token)\s+" + _TOKEN_PART + r"\s*$", re.IGNORECASE)


def _looks_like_a_secret(value: str) -> bool:
    """A VALUE shaped like a credential (``detections.redact.mask`` changes
    it, or it is a bare ``Bearer <token>``)."""
    v = str(value)
    if _CREDENTIAL_VALUE_RX.match(v):
        return True
    try:
        from ..detections.redact import mask

        return mask(v) != v
    except Exception:  # noqa: BLE001
        return False


def _secret_query_names(url: Any) -> list[str]:
    """The query parameters of ``url`` whose NAME says key, token, …
    (``?key=…``, ``?api_key=…``, ``?token=…``): a credential in an address
    the pack door would store plain, since ``mask`` reads only a few names."""
    from ..mcp.secrets import is_secret_key

    try:
        query = urlsplit(str(url or "")).query
    except ValueError:
        return []
    out: list[str] = []
    for part in query.split("&"):
        name = part.split("=", 1)[0]
        if name and (is_secret_key(name) or _SECRET_ARG_NAME.search(name)):
            out.append(name)
    return out


def _mask_url(url: str) -> str:
    """``mask`` plus every secret-named query parameter's value."""
    from ..detections.redact import MASK, mask

    shown = mask(str(url or ""))
    names = set(_secret_query_names(shown))
    if not names:
        return shown
    head, sep, query = shown.partition("?")
    if not sep:
        return shown
    parts = []
    for part in query.split("&"):
        name, eq, _value = part.partition("=")
        parts.append(f"{name}={MASK}" if eq and name in names else part)
    return head + "?" + "&".join(parts)


def comparable(row: dict[str, Any] | None) -> dict[str, Any] | None:
    """A stored pack row reduced to what an Undo compares: the user-set
    parts, vaulted argument positions as ``<vaulted>`` (their masked text
    depends on the value), vault rows by their KEYS. Never a value."""
    if not isinstance(row, dict):
        return None
    from ..mcp.tools import pack_enabled

    transport = str(row.get("transport") or "stdio").lower()
    out: dict[str, Any] = {"name": str(row.get("name") or ""), "transport": transport}
    if transport == "stdio":
        arg_secrets = {str(k) for k in dict(row.get("arg_secrets") or {})}
        out["command"] = str(row.get("command") or "")
        out["args"] = ["<vaulted>" if str(i) in arg_secrets else str(a) for i, a in enumerate(row.get("args") or [])]
        out["arg_secret_idx"] = sorted(int(k) for k in arg_secrets if k.isdigit())
        out["env"] = {str(k): str(v) for k, v in dict(row.get("env") or {}).items()}
        out["env_secrets"] = sorted(str(k) for k in dict(row.get("env_secrets") or {}))
        out["cwd"] = str(row.get("cwd") or "")
    else:
        out["url"] = "<vaulted>" if row.get("url_secret") else str(row.get("url") or "")
        out["headers"] = {str(k): str(v) for k, v in dict(row.get("headers") or {}).items()}
        out["header_secrets"] = sorted(str(k) for k in dict(row.get("header_secrets") or {}))
        auth = row.get("auth") if isinstance(row.get("auth"), dict) else None
        out["auth"] = {"header": auth.get("header"), "format": auth.get("format")} if auth else None
    out.update(
        {
            "enabled": pack_enabled(row),
            "auto_approve": bool(row.get("auto_approve")),
            "local_only": bool(row.get("local_only")),
            "locked": str(row.get("locked") or ""),
        }
    )
    return out


def same_server_as(platform: Any, spec: Any) -> str:
    """The stored pack that launches or reaches the SAME server as ``spec``
    (``mcp/pack_door._server_identity``), under any name; "" when none."""
    from ..mcp.pack_door import _server_identity

    try:
        want = _server_identity(spec.to_config())
    except Exception:  # noqa: BLE001
        return ""
    if want == ("stdio", "", "", ""):
        return ""
    for row in list(getattr(platform.config, "mcp_servers", None) or []):
        if isinstance(row, dict) and _server_identity(row) == want:
            return str(row.get("name") or "")
    return ""


# --- the plans waiting for a card (memory only) ------------------------------
_PENDING: dict[str, dict[str, Any]] = {}
_PENDING_LOCK = threading.Lock()


def _sweep(now: float | None = None) -> None:
    now = time.monotonic() if now is None else now
    for name in [n for n, p in _PENDING.items() if now - float(p.get("at") or 0) > PENDING_TTL_S]:
        _PENDING.pop(name, None)


def pending_for(name: str) -> dict[str, Any] | None:
    with _PENDING_LOCK:
        _sweep()
        p = _PENDING.get(name)
        return dict(p) if p else None


def forget_pending(name: str) -> None:
    with _PENDING_LOCK:
        _PENDING.pop(name, None)


def resolve_tool_server_secret(arg: str) -> tuple[str, str, str]:
    """``(pack, slot, vault name)`` for a ``tool_server.<arg>`` credential.
    The pack is the longest WAITING pack name the arg starts with (a pack
    name may itself hold ``__``), else the part before the first ``__``.
    Raises ``ValueError`` for a shape no slot has."""
    arg = str(arg or "")
    with _PENDING_LOCK:
        _sweep()
        names = sorted((n for n in _PENDING if arg.startswith(n + "__")), key=len, reverse=True)
    if names:
        pack = names[0]
        slot = arg[len(pack) + 2:]
    else:
        pack, sep, slot = arg.partition("__")
        if not sep:
            # A bare pack name is its one token (``mcp.<pack>.TOKEN``), the
            # shape ``config_secret`` may name for a pack the chat is not
            # adding right now.
            pack, slot = arg, "env__TOKEN"
    if not pack or not slot:
        raise ValueError("no slot")
    return pack, slot, _slot_vault_name(pack, slot)


def on_secret_saved(platform: Any, arg: str) -> dict[str, Any]:
    """The credential store saved a ``tool_server.<arg>`` value: mark its slot
    and, when every asked slot is saved, ADD the server through the one door
    with the values read back from the vault. Answers the card's words
    (``settings.credentials``' ``_app_outcome`` keys: app, app_name,
    tools_loaded, load_reason, waiting_for). BLOCKING. Never raises, never a
    value."""
    try:
        pack, slot, _vault = resolve_tool_server_secret(arg)
    except ValueError:
        return {}
    with _PENDING_LOCK:
        _sweep()
        waiting = pack in _PENDING
    if not waiting:
        if R.snap_app(platform, pack) is not None:
            # A2 review: an EXISTING pack's key (``config_secret``'s bare
            # name) — stored where the pack reads it; the plain "saved" words.
            return {}
        # A2 review: nothing waits and no pack of that name holds the row
        # (Iron Jarvis restarted, the card expired or was withdrawn): the
        # value just saved would be an orphan nobody can see — it goes.
        try:
            platform.secrets.delete(_vault)
        except Exception:  # noqa: BLE001
            pass
        return {"app": pack, "app_name": pack, "tools_loaded": 0, "load_reason": NOT_WAITING_DROPPED}
    with _PENDING_LOCK:
        plan = _PENDING.get(pack)
        if plan is None:
            return {"app": pack, "app_name": pack, "tools_loaded": 0, "load_reason": NOT_WAITING_DROPPED}
        saved = set(plan.setdefault("saved", []))
        saved.add(slot)
        plan["saved"] = sorted(saved)
        left = [s for s in plan["plan"].slots if s not in saved]
        if left:
            return {
                "app": pack,
                "app_name": pack,
                "tools_loaded": 0,
                "waiting_for": [_slot_label(pack, s).split(": ", 1)[1] for s in left],
            }
        _PENDING.pop(pack, None)
    p: Plan = plan["plan"]
    secrets = platform.secrets
    card_values: dict[str, str] = {}
    for s in p.slots:
        try:
            card_values[s] = str(secrets.get(_slot_vault_name(pack, s)) or "")
        except Exception:  # noqa: BLE001
            card_values[s] = ""
    result = _add_through_door(platform, p.filled_spec(card_values))
    out: dict[str, Any] = {"app": pack, "app_name": pack}
    if result is None or not result.get("added"):
        why = (result or {}).get("why") or "it was not added"
        # The card's rows are orphans now: only the ones the card wrote, and
        # only while no pack of that name holds them.
        if R.snap_app(platform, pack) is None:
            for s in p.slots:
                try:
                    secrets.delete(_slot_vault_name(pack, s))
                except Exception:  # noqa: BLE001
                    pass
        out.update({"tools_loaded": 0, "load_reason": f"the extension was not added: {why}"})
        return out
    entry = result["added"]
    loaded = int(entry.get("tools_loaded") or 0)
    out["tools_loaded"] = loaded
    if not loaded:
        out["load_reason"] = f"saved, but it did not start{R._why_not_started(pack)}"
    return out


def _add_through_door(platform: Any, spec: Any) -> dict[str, Any] | None:
    """Add ``spec`` through ``routes/agents._apply_specs`` → the one
    ``pack_door.add_pack``. ``{"added": entry}`` or ``{"why": words}``.
    BLOCKING."""
    from ..daemon.routes.agents import _apply_specs
    from ..mcp.pack_door import persist_for

    door = SimpleNamespace(platform=platform, _persist_config=persist_for(platform))
    try:
        result = _apply_specs(door, [spec], [spec.name], {}, False, False)
    except Exception as exc:  # noqa: BLE001 — names only
        return {"why": f"{type(exc).__name__}"}
    added = list(result.get("added") or [])
    if added:
        return {"added": added[0]}
    why = next((str(s.get("why") or "") for s in result.get("skipped") or []), "") or "it was not added"
    return {"why": why}


def restore_tool_server(platform: Any, name: str, desc: dict[str, Any]) -> str:
    """Undo of ``mcp_server_add``: the server THIS call made goes
    (``remove_pack``) — only while its row is still what the add stored; a
    card never saved is withdrawn. BLOCKING."""
    from ..mcp.pack_door import PackDoorError, remove_pack

    change_id = str(desc.get("change_id") or "")
    pending = pending_for(name)
    row = R.snap_app(platform, name)
    if row is None:
        if pending is not None and (not change_id or pending.get("change_id") == change_id):
            forget_pending(name)
            secrets = platform.secrets
            for s in pending.get("saved") or []:
                try:
                    secrets.delete(_slot_vault_name(name, s))
                except Exception:  # noqa: BLE001
                    pass
            return WITHDRAWN.format(name=name)
        raise R.RecordError(NEVER_ADDED.format(name=name))
    after = desc.get("after") if isinstance(desc.get("after"), dict) else None
    if after is None or comparable(row) != after:
        raise R.RecordError(CHANGED_SINCE.format(name=name))
    try:
        out = remove_pack(platform, name)
    except PackDoorError as exc:
        raise R.RecordError(exc.detail) from None
    keys = int(out.get("secrets_removed") or 0)
    tail = f" and its {keys} saved key{'s' if keys != 1 else ''}" if keys else ""
    return f"undo: removed the extension '{name}'{tail}"


class McpServerAddTool(R._RecordTool):
    name = "mcp_server_add"
    RECORD = TOOL_SERVER
    description = (
        "Add an extension (an MCP server) the user wants Jarvis to use: `config` is its command line "
        "(\"npx -y @modelcontextprotocol/server-filesystem C:\\Clients\"), its address (https://…/sse or "
        "…/mcp), a pasted config block (Claude Desktop / Claude Code / Codex / VS Code shape), or a "
        "prebuilt name (filesystem, fetch, memory, git, playwright, box, github); `name` what to call "
        "it. A `<folder>`-style blank is filled from `values` ({\"folder\": \"C:\\\\Clients\"}). NEVER "
        "pass a key, token or password — leave it out: the user pastes it on a secure card, and the "
        "server is added once it is saved. To bring over a server another program on this PC already "
        "has, use app_import instead. The user approves on a card that shows what will start; Undo "
        "removes it again."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "config": {"type": "string", "description": "the command line, address, config block or prebuilt name"},
            "name": {"type": "string", "description": "what to call it (letters, digits, - or _)"},
            "values": {
                "type": "object",
                "description": "non-secret blanks the config leaves (a folder, a repository path), by name",
                "additionalProperties": {"type": "string"},
            },
            "cwd": {"type": "string", "description": "the folder it starts in (optional)"},
        },
        "required": ["config"],
    }
    # v1.489.0: an extension the user named only by what it does ("one for
    # spreadsheets") is asked for on a card (tools/details). A key in the
    # answer is refused like one in the call.
    user_details = {
        "config": {
            "title": "The extension's command line, address or config block",
            "what": "the extension's command line, address or config block",
            "description": "For example npx -y @modelcontextprotocol/server-filesystem C:\\Clients. Leave any key out.",
        },
    }
    user_details_lead = "To add this extension, Iron Jarvis needs to know how it starts."
    _DECLARED = frozenset({"config", "name", "values", "cwd"})

    def redact_args(self, args: dict[str, Any]) -> dict[str, Any]:
        drop = set(_secret_args(args, self._DECLARED)) | set(_KEY_ARGS)
        out = {k: v for k, v in (args or {}).items() if k not in drop}
        if "config" in out:
            out["config"] = masked_config(out["config"])
        if isinstance(out.get("values"), dict):
            # A value the model put under a secret-shaped name is refused;
            # it is never written anywhere either. A2 review: nor is a
            # secret-SHAPED value under an innocent name ("folder": "Bearer
            # …") — the call is refused, and the ledger row shows the mask.
            from ..detections.redact import MASK
            from ..mcp.secrets import is_secret_key

            out["values"] = {
                k: (MASK if _looks_like_a_secret(str(v)) else v)
                for k, v in out["values"].items()
                if not (is_secret_key(str(k)) or _SECRET_ARG_NAME.search(str(k)))
            }
        if isinstance(out.get("cwd"), str):
            out["cwd"] = _mask_text(out["cwd"])
        return R.masked_args(out)

    def _target(self, args: dict[str, Any]) -> str:
        return ""

    async def capture_undo(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any] | None:
        _DESC.set(None)
        return _stash(await R.capture_create_undo(self.platform, TOOL_SERVER, "", pending=True))

    async def spend_check(self, args: dict[str, Any], ctx: ToolContext) -> SpendCheck | None:
        """No card for a call refused anyway (a key passed, no config, a name
        in use, a missing launcher); else the ONCE-ONLY card that shows what
        will start — also when the page armed the tool by name (arming alone
        would otherwise grant it). "Don't ask" keeps its meaning; a Local
        only chat keeps the card for an address off this network."""
        if _secret_args(args, self._DECLARED):
            return _NOTHING
        try:
            plan = await asyncio.to_thread(Plan, self.platform, args)
        except Exception:  # noqa: BLE001 — unsure: the ordinary card
            return None
        if plan.problem:
            return _NOTHING
        url = plan.spec.url if plan.spec.transport != "stdio" else ""
        return SpendCheck(
            ask=True,
            words=plan.preview(),
            once_only=True,
            dont_ask_skips=not _leaves_a_local_only_chat(url),
        )

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        if _secret_args(args, self._DECLARED):
            return ToolResult(ok=False, error=KEY_NOT_IN_CHAT)
        plan = await asyncio.to_thread(Plan, self.platform, args)
        if plan.problem:
            return ToolResult(ok=False, error=plan.problem)
        name = plan.name
        if plan.slots:
            # The card first: nothing touches the disk until every asked
            # value is saved (on_secret_saved adds it then).
            change_id = R._CHANGE_ID.get()
            with _PENDING_LOCK:
                _sweep()
                _PENDING[name] = {"plan": plan, "change_id": change_id, "at": time.monotonic(), "saved": []}
            R.bind_created(name)
            _note(after=plan.expected(), waited_for_card=True)
            requests = [
                {
                    "name": f"{TOOL_SERVER_SECRET}{name}__{slot}",
                    "label": _slot_label(name, slot),
                    "help": "",
                    "why": f"so the extension {name} can start",
                    "request_id": new_id("seq"),
                }
                for slot in plan.slots
            ]
            what = plan._asked_words()
            saved = "it is saved" if len(plan.slots) == 1 else "they are saved"
            data: dict[str, Any] = {"secret_requests": requests, "tool_server": name, "waiting_for": plan.slots}
            card = R.create_card(TOOL_SERVER, name, f"waiting for {what}", label=f"Extension {_q(name)}")
            if card:
                card["title"] = "Extension waiting for its key"
                data["record_change"] = card
            return ToolResult(ok=True, output=WAITING_WORDS.format(what=what, saved=saved, name=name), data=data)
        result = await asyncio.to_thread(_add_through_door, self.platform, plan.filled_spec())
        if result is None or not result.get("added"):
            return ToolResult(ok=False, error=f"The extension {name!r} was not added: {(result or {}).get('why')}.")
        R.bind_created(name)
        row = await asyncio.to_thread(R.snap_app, self.platform, name)
        _note(after=comparable(row))
        entry = result["added"]
        loaded = int(entry.get("tools_loaded") or 0)
        if loaded:
            state = f"{loaded} tool{'s' if loaded != 1 else ''}"
            output = f"Added the extension {name!r} ({state}). Jarvis can use its tools now."
        else:
            state = R.NOT_STARTED_STATE
            output = f"Added the extension {name!r}, but it did not start{await asyncio.to_thread(R._why_not_started, name)}"
        data = {"tool_server": name, "tools_loaded": loaded}
        card = R.create_card(TOOL_SERVER, name, state, label=f"Extension {_q(name)}")
        if card:
            card["title"] = "Extension added"
            data["record_change"] = card
        return ToolResult(ok=True, output=output, data=data)


# =========================================================================== #
# Web services (REST hookups)
# =========================================================================== #
def _hookups(platform: Any) -> Any:
    return getattr(platform, "integrations", None)


def _service_view(platform: Any, iid: str) -> dict[str, Any] | None:
    reg = _hookups(platform)
    if reg is None or reg.get_spec(iid) is None:
        return None
    cfg = dict(reg.stored_config(iid) or {})
    spec = reg.get_spec(iid)
    rec = reg.get_record(iid)
    return {
        "display_name": str(getattr(spec, "display_name", "") or ""),
        "description": str(getattr(spec, "description", "") or ""),
        "enabled": bool(getattr(rec, "enabled", False)) if rec is not None else False,
        "config": {k: v for k, v in cfg.items() if k != "http_get"},
    }


def _address_in_use(platform: Any, address: str) -> str:
    """The display name of a saved service at the same address; "" when none."""
    from ..integrations.hookups import rest_hookups

    reg = _hookups(platform)
    if reg is None:
        return ""
    want = address.rstrip("/").lower()
    for spec in rest_hookups(reg):
        try:
            cfg = reg.stored_config(spec.id)
        except Exception:  # noqa: BLE001
            continue
        if str(cfg.get("base_url") or "").rstrip("/").lower() == want:
            return str(getattr(spec, "display_name", "") or spec.id)
    return ""


def restore_web_service(platform: Any, iid: str, desc: dict[str, Any]) -> str:
    """Undo of ``web_service_add``: the service THIS call made goes (its
    spec, its row, its saved key) — only while it is as the add left it.
    BLOCKING."""
    from ..integrations.hookups import remove_hookup

    now = _service_view(platform, iid)
    shown = str(desc.get("shown") or iid)
    if now is None:
        raise R.RecordError(SERVICE_GONE.format(name=shown))
    after = desc.get("after") if isinstance(desc.get("after"), dict) else None
    if after is None or now != after:
        raise R.RecordError(SERVICE_CHANGED.format(name=shown))
    from ..mcp.pack_door import persist_for

    out = remove_hookup(platform, iid, persist=persist_for(platform))
    tail = " and its saved key" if out.get("key_removed") else ""
    return f"undo: removed the web service '{shown}'{tail}"


class WebServiceAddTool(R._RecordTool):
    name = "web_service_add"
    RECORD = WEB_SERVICE
    description = (
        "Save a web service (a REST API of the user's — their firm's pricing API, an internal tool) so "
        "Jarvis can call it with rest_call (\"hook up our pricing API at https://api.example.com/v1\"): "
        "`name` what to call it, `address` its base address (http:// or https://), `description` what "
        "it is for. If it takes a key or bearer token, set `needs_key` true — NEVER pass the key: the "
        "user pastes it on a secure card. How calls carry the key: `key_in` \"bearer\" (the default, "
        "Authorization: Bearer), \"header\" (set `header` to the header's name, e.g. X-API-Key) or "
        "\"basic\" (a username and password sign-in: set `username`; the password is the key on the card). "
        "An address on the user's own network is fine. The user approves on a card that says where it "
        "points; Undo removes it."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "address": {"type": "string", "description": "e.g. https://api.example.com/v1"},
            "description": {"type": "string"},
            "needs_key": {"type": "boolean"},
            "key_in": {"type": "string", "enum": ["bearer", "header", "basic"]},
            "header": {"type": "string", "description": "the header's name when key_in is header"},
            "username": {"type": "string", "description": "the sign-in's username when key_in is basic"},
        },
        "required": ["name", "address"],
    }
    # v1.489.0: an address the user did not say is asked for on a card
    # (tools/details). Never the key.
    user_details = {
        "address": {
            "title": "The service's address",
            "what": "the service's address",
            "description": "Starting with http:// or https://, for example https://api.example.com/v1",
        },
    }
    user_details_lead = "To save this web service, Iron Jarvis needs its address."
    _DECLARED = frozenset({"name", "address", "description", "needs_key", "key_in", "header", "username"})

    def redact_args(self, args: dict[str, Any]) -> dict[str, Any]:
        drop = set(_secret_args(args, self._DECLARED)) | set(_KEY_ARGS)
        out = {k: v for k, v in (args or {}).items() if k not in drop}
        # A2 review: an address with ``?key=…`` is refused — and its ledger
        # row masks the key (``mask`` knows ``api_key=``, not ``key=``).
        for k in ("address", "description", "name", "header", "username"):
            if isinstance(out.get(k), str):
                out[k] = _mask_text(out[k])
        return R.masked_args(out)

    @staticmethod
    def _auth(args: dict[str, Any]) -> tuple[str, str, str, str]:
        """``(kind, header, username, problem)`` for how calls carry the key
        (v1.455.0). Only meaningful with ``needs_key``; without a key nothing
        travels, so the way is not checked."""
        from ..integrations.builtin import auth_problem

        a = args or {}
        kind = str(a.get("key_in") or "bearer").strip().lower()
        header = " ".join(str(a.get("header") or "").split())
        user = str(a.get("username") or "").strip()
        if a.get("needs_key") is not True:
            return "bearer", "", "", ""
        if _mask_text(header) != header or _mask_text(user) != user:
            return kind, header, user, SERVICE_WORDS_LOOK_SECRET
        return kind, header, user, auth_problem(kind, header, user)

    def _target(self, args: dict[str, Any]) -> str:
        return ""

    def _check(self, args: dict[str, Any]) -> tuple[str, str, str, str]:
        """``(id, display name, address, problem)``. BLOCKING (registry reads)."""
        from ..integrations.hookups import EXAMPLE_IDS, _slug

        if _secret_args(args, self._DECLARED):
            return "", "", "", NOT_CARRIED
        shown = " ".join(str((args or {}).get("name") or "").split())[:60]
        # A2 review: a credential in the NAME or the description would land
        # in config.toml and on the card as words.
        about = str((args or {}).get("description") or "")
        if _mask_text(shown) != shown or _mask_text(about) != about:
            return "", "", "", SERVICE_WORDS_LOOK_SECRET
        raw = str((args or {}).get("address") or "")
        if _network_spelling(raw):
            return "", shown, "", SERVICE_NETWORK
        address, problem = clean_address(raw)
        if problem:
            return "", shown, "", problem.replace("the server's", "the service's")
        iid = _slug(shown)
        if not iid:
            return "", shown, address, "Give the web service a name (letters and digits)."
        reg = _hookups(platform=self.platform)
        if reg is None:
            return "", shown, address, "Web services can be saved only while the Iron Jarvis app is running."
        if iid in EXAMPLE_IDS or reg.get_spec(iid) is not None:
            return "", shown, address, SERVICE_NAME_IN_USE.format(name=shown)
        other = _address_in_use(self.platform, address)
        if other:
            return "", shown, address, SERVICE_SAME_ADDRESS.format(other=other)
        _kind, _header, _user, auth = self._auth(args)
        if auth:
            return "", shown, address, auth
        return iid, shown, address, ""

    async def capture_undo(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any] | None:
        _DESC.set(None)
        return _stash(await R.capture_create_undo(self.platform, WEB_SERVICE, "", pending=True))

    async def spend_check(self, args: dict[str, Any], ctx: ToolContext) -> SpendCheck | None:
        try:
            iid, shown, address, problem = await asyncio.to_thread(self._check, args)
        except Exception:  # noqa: BLE001
            return None
        if problem:
            return _NOTHING
        words = f"Saves the web service {_q(shown)} at {address} — {place_words(address)}."
        if (args or {}).get("needs_key") is True:
            words += " Its key is pasted on a secure card afterwards."
        return SpendCheck(ask=True, words=words, dont_ask_skips=not _leaves_a_local_only_chat(address))

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from ..integrations.hookups import add_hookup
        from ..mcp.pack_door import persist_for
        from ..providers.egress import current_scope as egress_scope
        from ..providers.egress import private_host

        iid, shown, address, problem = await asyncio.to_thread(self._check, args)
        if problem:
            return ToolResult(ok=False, error=problem)
        keyed = (args or {}).get("needs_key") is True
        kind, header, user, _problem = self._auth(args)
        description = " ".join(str((args or {}).get("description") or "").split())[:300]
        key_name = f"integration_{iid}_token" if keyed else ""
        private = bool(private_host(address))
        try:
            await asyncio.to_thread(
                add_hookup,
                self.platform,
                name=shown,
                base_url=address,
                description=description,
                auth_secret=key_name,
                allow_internal=private,
                persist=persist_for(self.platform),
                auth_kind=kind,
                auth_header=header,
                auth_user=user,
            )
        except ValueError as exc:
            return ToolResult(ok=False, error=str(exc))
        R.bind_created(iid)
        view = await asyncio.to_thread(_service_view, self.platform, iid)
        _note(after=view, shown=shown)
        where = place_words(address)
        data: dict[str, Any] = {"web_service": iid}
        if keyed:
            data["secret_requests"] = [
                {
                    "name": f"{WEB_SERVICE_SECRET}{iid}",
                    "label": f"{shown}'s password" if kind == "basic" else f"{shown}'s key",
                    "help": "",
                    "why": f"to call the web service {shown}",
                    "request_id": new_id("seq"),
                }
            ]
        card = R.create_card(WEB_SERVICE, iid, f"{address} · {where}", label=f"Web service {_q(shown)}")
        if card:
            card["title"] = "Web service added"
            data["record_change"] = card
        output = SERVICE_ADDED.format(
            name=shown, address=address, where=where,
            key=service_key_words(kind, header, user) if keyed else "",
        )
        if keyed:
            # Its Test needs the key; it runs when the card saves it
            # (``settings.credentials`` → :func:`on_web_service_key_saved`).
            output += " " + SERVICE_CHECK_LATER
        elif AUTO_CHECK and egress_scope().on and not private:
            # Wave F review: under Local only an internet address is not sent
            # a request no card asked about.
            output += " " + SERVICE_NOT_TESTED_LOCAL_ONLY
        elif AUTO_CHECK:
            # Readiness 0j.5 item 18: never "added" alone — the service's own
            # Test says whether it answers.
            answering, words = await check_service_bounded(self.platform, iid)
            data["check"] = {"answering": answering, "words": words}
            output += " " + words
        return ToolResult(ok=True, output=output, data=data)


def on_web_service_key_saved(platform: Any, arg: str) -> dict[str, Any]:
    """The credential store saved ``web_service.<id>``'s key: run the service's
    own Test with it (readiness 0j.5 item 18) and answer ``{"check":
    {answering, words}}`` for the card. BLOCKING. Never raises, never a value."""
    try:
        iid = str(arg or "").strip()
        if not AUTO_CHECK:
            return {}
        reg = getattr(platform, "integrations", None)
        if not iid or reg is None or reg.get_spec(iid) is None:
            return {}
        answering, words = check_service_within(platform, iid)
        return {"check": {"answering": answering, "words": words}}
    except Exception:  # noqa: BLE001
        return {}


# =========================================================================== #
# Undo plumbing for settings.records
# =========================================================================== #
def _snap_pending(_platform: Any, _name: str) -> None:
    return None


SNAP = {TOOL_SERVER: _snap_pending, WEB_SERVICE: _snap_pending}


def restore_kind(platform: Any, record: str, name: str, prior: Any, desc: dict[str, Any]) -> str:
    """``records.restore`` for this module's kinds. BLOCKING."""
    if record == TOOL_SERVER:
        return restore_tool_server(platform, name, desc)
    if record == WEB_SERVICE:
        return restore_web_service(platform, name, desc)
    raise R.RecordError(f"unknown record kind {record!r}")


def tools_record_tools(platform: Any) -> list[Any]:
    return [McpServerAddTool(platform), WebServiceAddTool(platform)]


# =========================================================================== #
# Arming: which of these a chat message asks for
# =========================================================================== #
#: The longest message the matchers read (a pasted page is not a request).
_MAX_TEXT = 20_000

_ADD = r"(?:add|install|connect|set\s+up|setup|register|hook\s+up|plug\s+in|load|enable|configure|wire\s+up|bring\s+in|put\s+in|use|save|link)"
#: What an extension is called.
_SERVER_WORD = (
    r"(?:mcp(?:\s+servers?)?|tool(?:s)?\s+servers?|model\s+context\s+protocol(?:\s+servers?)?"
    r"|mcp\s+(?:extension|connector|pack|plugin))"
)
#: A command line that starts a server.
_COMMAND_LINE = re.compile(
    r"\b(?:npx\s+(?:-y\s+|--yes\s+)?@?[\w./@-]+|uvx\s+[\w.-]+|pipx\s+run\s+[\w.-]+|bunx\s+@?[\w./-]+"
    r"|docker\s+run\b[^\n]{0,120}?\b[\w./-]+/[\w.-]+)",
    re.IGNORECASE,
)
_SERVER_ADDRESS = re.compile(r"https?://[^\s\"'<>]+/(?:sse|mcp)\b", re.IGNORECASE)
_CONFIG_BLOCK = re.compile(r"\"?mcpServers\"?\s*:|\[mcp_servers\.|\"command\"\s*:\s*\"", re.IGNORECASE)
_MCP_ADD = re.compile(
    r"\b" + _ADD + r"\b[^.?!\n]{0,80}?\b" + _SERVER_WORD + r"\b"
    + r"|\b" + _SERVER_WORD + r"\b[^.?!\n]{0,40}?\b(?:to|into|for)\s+(?:iron\s+)?jarvis\b"
    + r"|\b(?:paste|here(?:'s| is))\b[^.?!\n]{0,30}?\b(?:the\s+)?(?:config|configuration|command)\b[^.?!\n]{0,40}?\b"
    + _SERVER_WORD + r"\b",
    re.IGNORECASE,
)
#: A server named plainly beside its command line or address ("add this
#: server: npx …", "connect this tool server at https://…").
_SERVER_PLAIN = re.compile(
    r"\b" + _ADD + r"\b[^.?!\n]{0,40}?\b(?:this|that|the|a|an|my|our)\s+(?:[\w-]+\s+){0,2}?servers?\b",
    re.IGNORECASE,
)
#: Not a request to ADD: a question, a removal, a restart, a fault report.
_MCP_NOT_ADD = re.compile(
    r"\b(?:remove|delete|disconnect|uninstall|restart|reload|retry|fix|debug|why|crash\w*|fail\w*|broke\w*"
    r"|isn['’]?t\s+working|not\s+working|what\s+is|what['’]?s|explain|how\s+does|turn\s+off|disable)\b",
    re.IGNORECASE,
)
#: The thing is the user's content, not Iron Jarvis's ("the MCP of the church").
_MCP_OF_SOMETHING = re.compile(r"\bmcp\s+(?:of|at|for)\s+(?:the|my|our|a)\b", re.IGNORECASE)
#: Another program's server: app_import's (never this tool's).
_FROM_OTHER_PROGRAM = re.compile(
    r"\b(?:i\s+use\s+|from\s+|in\s+)(?:my\s+)?(?:claude\s+desktop|claude\s+code|codex|cursor|vs\s?code|windsurf|gemini\s+cli)\b",
    re.IGNORECASE,
)

#: A web service / REST API of the user's.
_SERVICE_WORD = (
    r"(?:web\s+services?|rest\s+(?:api|service|endpoint)s?|(?:internal|company|firm['’]?s?|our|my|private|in-?house|pricing|billing|crm|erp|[\w-]+['’]s)\s+api"
    r"|api\s+(?:endpoint|base|server)|json\s+api|graphql\s+(?:api|endpoint))"
)
_HOOK_VERB = r"(?:hook\s+up|add|connect|register|set\s+up|setup|save|wire\s+up|link|plug\s+in|integrate|point\s+(?:you|jarvis)\s+at|give\s+(?:you|jarvis)\s+access\s+to|let\s+(?:you|jarvis)\s+(?:call|use|reach|query))"
_HTTP_ADDRESS = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_SERVICE_ADD = re.compile(
    r"\b" + _HOOK_VERB + r"\b[^.?!\n]{0,60}?\b" + _SERVICE_WORD + r"\b"
    + r"|\b" + _HOOK_VERB + r"\b[^.?!\n]{0,60}?\bapi\b[^.?!\n]{0,80}?https?://"
    + r"|\bweb\s+service\s+(?:called|named)\b",
    re.IGNORECASE,
)
#: Code work ABOUT an API, a call to one, or the API's own news — never a save.
_SERVICE_NOT_ADD = re.compile(
    r"\b(?:build|write|code|implement|design|create|make|develop|scaffold|mock|document|test|deploy|publish|expose|version)\b[^.?!\n]{0,40}?\b(?:api|endpoint|service)s?\b"
    r"|\b(?:changed|updated|deprecated|announced|released|raised|terms|pricing\s+changed|is\s+down|went\s+down|outage|error|failed|returned|broke)\b"
    r"|\b(?:call|query|fetch|hit|ping|look\s+up|get)\b[^.?!\n]{0,30}?\b(?:the|our|my)\s+(?:[\w-]+\s+)?api\b(?![^.?!\n]{0,60}?https?://)"
    r"|\b(?:to|into|in)\s+(?:the|my|our|this)\s+(?:project|repo|repository|codebase|app|code|backend|frontend)\b",
    re.IGNORECASE,
)


def tools_tools_for(text: str) -> tuple[str, ...]:
    """The tools of this module a chat message asks for, in a fixed order.
    Called ONCE from ``records.record_tools_for``. A QUESTION arms nothing
    (both are writers). Linear-time matchers only; never raises."""
    return _tools_tools_for(str(text or "")[:_MAX_TEXT])


@lru_cache(maxsize=256)
def _tools_tools_for(t: str) -> tuple[str, ...]:
    if not t.strip():
        return ()
    out: list[str] = []
    try:
        from .records_connections import without_addresses

        if R.is_question(without_addresses(t)):
            return ()
        if _wants_tool_server(t):
            out.append("mcp_server_add")
        if _wants_web_service(t):
            out.append("web_service_add")
    except Exception:  # noqa: BLE001 — a matcher fault arms nothing new
        return ()
    return tuple(out)


def _wants_tool_server(t: str) -> bool:
    if _MCP_OF_SOMETHING.search(t):
        return False
    config = bool(_COMMAND_LINE.search(t) or _SERVER_ADDRESS.search(t) or _CONFIG_BLOCK.search(t))
    if _MCP_ADD.search(t):
        if _FROM_OTHER_PROGRAM.search(t) and not config:
            return False  # "add the QuickBooks server I use in Claude Desktop": app_import
        return not (_MCP_NOT_ADD.search(t) and not config)
    if config and (_SERVER_PLAIN.search(t) or re.search(r"\b" + _ADD + r"\b", t, re.IGNORECASE)):
        return not re.search(r"\b(?:remove|delete|disconnect|uninstall)\b", t, re.IGNORECASE)
    return False


def _wants_web_service(t: str) -> bool:
    if not _SERVICE_ADD.search(t):
        return False
    if _SERVICE_NOT_ADD.search(t):
        return False
    # An MCP server's address is an extension, not a web service.
    if _SERVER_ADDRESS.search(t) or _COMMAND_LINE.search(t):
        return False
    return True


#: The tests clear the cache through the public name.
tools_tools_for.cache_clear = _tools_tools_for.cache_clear  # type: ignore[attr-defined]
tools_tools_for.cache_info = _tools_tools_for.cache_info  # type: ignore[attr-defined]
