"""Model servers, accounts and memory bases from chat (v1.438.0, chat-first
plan Wave 4 group B, Part B2's "Model servers", "Accounts" and "Memory" rows).

Each of these was a page: a model server was added on the Fleet page (and its
default model picked from a list there), a second Claude / Codex / Grok
account on the Iron-Proxy card, a Notion database or a notes folder on the
Memory page. These tools close that gap the way every record tool does
(``settings.records``): each ASKS FIRST (the ordinary approval card) and
leaves an Undo on the reply that puts back what THAT change did — and refuses,
in words, when the user changed it again since.

* ``model_server_add`` — adds a server through the Fleet page's own body
  (``daemon.routes.fleet.add_fleet_node``: the kind is detected, it is probed
  once, it becomes a model Iron Jarvis may use at once). The card says whether
  it is on this PC or the user's own network, or on the internet (what is sent
  to it then leaves this PC — ``providers.egress.private_host``). Its KEY is
  never an argument: ``needs_key`` puts up the secure card
  (``model_server.<id>`` → the vault row ``endpoint_<id>_key`` the Fleet page
  uses, read only for that server — ``fleet.endpoint_auth``). The default model
  is picked from THAT server's own list (``daemon.routes.connections.
  fetch_endpoint_models``) — never a guessed name; a list that cannot be read
  is said, and no default is set.
* ``model_server_default`` — make one model of an added server the default
  (``default_provider`` + ``default_model`` through THE settings writer), the
  model checked against the server's own list. Undo puts the old default back
  only while the default is still the one this change set.
* ``model_server_remove`` — the Fleet page's remove
  (``daemon.routes.fleet.delete_fleet_node``); Undo adds the server back as it
  was (a server the user set up in Settings comes back to its settings).
* ``account_add`` — a Claude Code, Codex or Grok account through Iron-Proxy
  (``iron_proxy.client.create_profile``, the Iron-Proxy card's Add account),
  then the account's own sign-in terminal is opened
  (``daemon.routes.iron_proxy.open_account_signin``) and the reply carries a
  door into it. Iron Jarvis NEVER handles the login: the user signs in in that
  terminal, in their own browser. Iron-Proxy off is refused with where to turn
  it on. Undo removes the account only while Iron-Proxy says it still needs to
  sign in — a signed-in account (or one whose state cannot be read) is never
  removed by an Undo: it would sign that login out, so the Undo says so and
  points to the card.
* ``memory_base_add`` — a Notion database or a notes folder (an Obsidian vault
  is a notes folder) as a memory base, through the Memory page's own body
  (``daemon.routes.knowledge.add_ltm_source_record``), registered LIVE (no
  restart). A Notion token is never an argument: the secure card
  (``memory.<name>`` → ``ltm_<name>_notion``, read lazily by the base, so it
  works the moment it is saved). ``use_as_memory`` also makes it the primary of
  a new memory profile and switches to that profile. Undo removes the base
  (and the profile it made) only while both are as this change left them, and
  never while another memory profile relies on the base.

Account groups and the memory profile switch stay SETTINGS (``config_change_
protected`` for ``account_groups``, ask every time; ``config_change`` for
``brain_profile``): :func:`named_setting_value` lets chat name the accounts
and the profile by their NAMES ("put TSI and VR2 in a group called Work",
"use my memory profile Work") — resolved from the CACHED account list and the
profile table, never a call on the loop.

A credential is NEVER a tool argument, NEVER in a log row, NEVER on a card.
Snapshots and restores are BLOCKING; the tools run them off the loop.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import re
from functools import lru_cache
from typing import Any
from urllib.parse import urlsplit

from ..tools.base import SpendCheck, ToolContext, ToolResult
from . import records as R

log = logging.getLogger("ironjarvis.settings")

#: The tools this module adds and their default permission (mirrored, row for
#: row, in ``records.RECORD_TOOL_TIERS`` and ``core.config``'s table).
CONNECTIONS_TOOL_TIERS: dict[str, str] = {
    "model_server_add": "ask",
    "model_server_default": "ask",
    "model_server_remove": "ask",
    "account_add": "ask",
    "memory_base_add": "ask",
}

#: The record kinds this module adds to ``records._SNAP`` / ``records.restore``.
MODEL_SERVER = "model_server"
MODEL_DEFAULT = "model_default"
MODEL_SERVER_REMOVED = "model_server_removed"
ACCOUNT = "account"
MEMORY_BASE = "memory_base"
KINDS = (MODEL_SERVER, MODEL_DEFAULT, MODEL_SERVER_REMOVED, ACCOUNT, MEMORY_BASE)

NEEDS_THE_APP = "This can only be done while the Iron Jarvis app is running."
#: The one refusal of a credential passed as a tool argument (the Wave 1 rule:
#: a key typed into the chat sits in the model's context, the transcript and
#: the stream).
KEY_NOT_IN_CHAT = (
    "A key or token is never passed in the chat. Call this again without it (set needs_key to true "
    "for a server that asks for one): the user pastes it on the secure card."
)
ADDRESS_WITH_PASSWORD = (
    "That address has a password in it. Give the address without it; the key goes on the secure card."
)
#: Where several accounts are switched on (the Iron-Proxy card's own title,
#: ``daemon.page_doors.ACCOUNTS_CARD``).
ACCOUNTS_OFF = (
    "Several accounts is off, so no account can be added. Turn it on on the "
    "\"Several accounts (Iron-Proxy)\" card in Settings › Connections, then ask again."
)
ACCOUNTS_HREF = "/connections"
#: What an Undo of ``account_add`` says when the account signed in since.
ACCOUNT_SIGNED_IN = (
    "The account “{name}” is signed in now (or its sign-in can't be read), so this Undo would sign that "
    "login out. It was kept; remove it on the \"Several accounts (Iron-Proxy)\" card in Settings › "
    "Connections if you mean to."
)

#: The argument names a key could be passed under (refused, never read).
_KEY_ARGS = ("key", "api_key", "apikey", "token", "password", "secret", "bearer", "auth")

# --- the descriptor this call captured (records_projects' way) -------------
_DESC: contextvars.ContextVar["dict[str, Any] | None"] = contextvars.ContextVar(
    "ij_connections_desc", default=None
)


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


def _d(platform: Any) -> Any:
    return R._d(platform)  # noqa: SLF001 — the records module's own deps lookup


def _fold(text: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


def _q(text: Any) -> str:
    return f"“{' '.join(str(text or '').split())[:60]}”"


#: The arguments these tools declare (never a credential — ``needs_key`` is
#: a yes/no). Review: every OTHER argument whose name reads like a credential
#: ("access_token", "apiKey", "notion_token") is one, refused and never
#: logged — a fixed list of five names let the rest through to the card and
#: the ledger.
_DECLARED_ARGS = frozenset({
    "address", "name", "needs_key", "protocol", "make_default", "model", "server", "vendor", "kind",
    "folder", "database", "use_as_memory",
})
_SECRET_ARG_NAME = re.compile(r"(?i)key|token|secret|passw|pwd|auth|bearer|credential|cookie")


def _secret_args(args: dict[str, Any]) -> list[str]:
    """The arguments that carry (or may carry) a credential."""
    out: list[str] = []
    for k, v in (args or {}).items():
        if not isinstance(k, str) or k in _DECLARED_ARGS:
            continue
        if k in _KEY_ARGS or _SECRET_ARG_NAME.search(k):
            if v is not None and v is not False and v is not True and str(v).strip():
                out.append(k)
    return out


def _key_passed(args: dict[str, Any]) -> bool:
    """A credential passed as an argument (:func:`_secret_args`)."""
    return bool(_secret_args(args))


def _shown_address(raw: Any) -> Any:
    """An address as the card, the stream and the ledger may show it: a
    password, a query and a fragment are left out (review: an address the
    tool REFUSES for its password was still written to the ledger whole).
    Never raises."""
    if not isinstance(raw, str):
        return raw
    text = raw.strip()
    try:
        probe = text if "://" in text else "http://" + text
        parts = urlsplit(probe)
        host = parts.hostname or ""
        if parts.username or parts.password or parts.query or parts.fragment:
            port = f":{parts.port}" if parts.port else ""
            shown = f"{parts.scheme}://{host}{port}{parts.path}" + " (part left out)"
            return shown if "://" in text else shown.split("://", 1)[1]
    except ValueError:
        return "(an address Iron Jarvis cannot read)"
    try:
        from ..detections.redact import mask

        return mask(text)
    except Exception:  # noqa: BLE001
        return text


def _redacted(args: dict[str, Any]) -> dict[str, Any]:
    """What the card, the stream and the ledger show of a call's arguments."""
    drop = set(_secret_args(args)) | set(_KEY_ARGS)
    out = {k: v for k, v in (args or {}).items() if k not in drop}
    if "address" in out:
        out["address"] = _shown_address(out["address"])
    return out


def _http_problem(exc: Any) -> str:
    return " ".join(str(getattr(exc, "detail", exc) or "it was refused").split())[:300]


_NOTHING = SpendCheck(ask=False, cost_note="changes nothing by itself")


# =========================================================================== #
# Model servers
# =========================================================================== #
def clean_address(raw: Any) -> tuple[str, str]:
    """``(address, problem)``: an http(s) address with a host, no password,
    no query. A bare ``host:port`` reads as ``http://``. Pure."""
    text = str(raw or "").strip().strip("<>").strip()
    if not text:
        return "", "Give the server's address (for example http://192.168.1.20:1234)."
    if "://" not in text:
        text = "http://" + text
    try:
        parts = urlsplit(text)
        host = parts.hostname
        parts.port  # noqa: B018 — a bad port raises here
    except ValueError:
        return "", "That is not an address Iron Jarvis can open."
    if parts.scheme.lower() not in ("http", "https") or not host:
        return "", "Give the server's address starting with http:// or https://."
    if parts.username or parts.password:
        return "", ADDRESS_WITH_PASSWORD
    if parts.query or parts.fragment:
        return "", "Give the server's address without anything after a ? or #."
    return text.rstrip("/"), ""


def place_words(address: str) -> str:
    """Where a server is, in words (the Local only rule's own reading)."""
    from ..providers.egress import private_host

    if private_host(address):
        return "on this PC or your own network"
    return "on the internet, so what is sent to it leaves this PC"


def _leaves_a_local_only_chat(address: str = "", *, cloud: bool = False) -> bool:
    """Review: in a Local only chat, a call that reaches past this network
    (an internet server's address, a cloud memory base) keeps its card in
    EVERY posture — "Don't ask" included: the chat's promise is that nothing
    leaves this network without the user's OK. Never raises (a fault asks)."""
    try:
        from ..providers.egress import current_scope, private_host

        if not current_scope().on:
            return False
        return cloud or (bool(address) and not private_host(address))
    except Exception:  # noqa: BLE001
        return True


def _top_nodes(d: Any) -> list[Any]:
    fleet = getattr(d, "fleet", None)
    try:
        return [n for n in (fleet.nodes() if fleet is not None else []) if not getattr(n, "parent_id", "")]
    except Exception:  # noqa: BLE001
        return []


def _node_label(node: Any) -> str:
    return str(getattr(node, "label", "") or getattr(node, "id", "") or "")


def find_server(d: Any, ref: Any) -> tuple[Any, str]:
    """``(node, problem)``: the added server the user named — its id, its name
    or its address (``host:port``). Only a server Iron Jarvis keeps (never a
    model a proxy reports). Never raises."""
    raw = str(ref or "").strip()
    nodes = _top_nodes(d)
    if not raw:
        return None, "Say which model server."
    fold = _fold(raw)
    exact = [n for n in nodes if n.id == raw or _fold(n.label) == fold or _fold(n.id) == fold]
    if not exact:
        from ..fleet.endpoint_auth import root_key

        want = root_key(raw if "://" in raw else f"http://{raw}") if re.search(r"[:./]", raw) else ""
        exact = [n for n in nodes if want and root_key(n.base_url) == want]
    if not exact:
        # "the DGX server", "my LM Studio box": the name inside the words.
        words = [w for w in re.findall(r"[a-z0-9]+", raw.lower())
                 if w not in ("the", "my", "our", "server", "box", "model", "node", "machine", "endpoint")]
        if words:
            exact = [n for n in nodes if all(w in _fold(n.label) + _fold(n.id) for w in words)]
    if len(exact) == 1:
        return exact[0], ""
    if len(exact) > 1:
        names = ", ".join(_node_label(n) for n in exact)
        return None, f"Several model servers match {_q(raw)} ({names}): say which one."
    have = ", ".join(_node_label(n) for n in nodes)
    tail = f" The model servers added are: {have}." if have else " No model servers are added yet."
    return None, f"There is no model server called {_q(raw)}.{tail}"


def _provider_of(node: Any) -> str:
    return f"fleet-{node.id}"


def _model_names(answer: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for m in answer.get("models") or []:
        name = m.get("id") if isinstance(m, dict) else m
        if isinstance(name, str) and name.strip() and name not in out:
            out.append(name.strip())
    return out


async def read_models(d: Any, node: Any) -> tuple[list[str], str]:
    """``(models, problem)``: the server's OWN list, asked with its own saved
    key only (``connections.fetch_endpoint_models``). ``problem`` is the
    page's sentence when the list could not be read."""
    from fastapi import HTTPException

    from ..daemon.routes.connections import fetch_endpoint_models

    try:
        answer = await fetch_endpoint_models(d, node.base_url, getattr(node, "protocol", "openai") or "openai")
    except HTTPException as exc:
        return [], _http_problem(exc)
    except Exception:  # noqa: BLE001 — a probe fault is a sentence
        return [], "Its model list could not be read."
    names = _model_names(answer if isinstance(answer, dict) else {})
    if names:
        return names, ""
    return [], str((answer or {}).get("error") or "It lists no models.")


def _pick(models: list[str], wanted: str) -> str:
    """The listed model the user named (exact, case-blind), else ""."""
    w = (wanted or "").strip()
    if not w:
        return ""
    for m in models:
        if m == w:
            return m
    low = [m for m in models if m.lower() == w.lower()]
    return low[0] if len(low) == 1 else ""


def _listed(models: list[str]) -> str:
    shown = ", ".join(models[:25])
    return shown + (f" and {len(models) - 25} more" if len(models) > 25 else "")


def _writer(platform: Any) -> Any:
    w = getattr(platform, "config_writer", None)
    if w is None:
        raise R.RecordError(NEEDS_THE_APP)
    return w


def _defaults(platform: Any) -> dict[str, str]:
    cfg = platform.config
    return {
        "default_provider": str(getattr(cfg, "default_provider", "") or ""),
        "default_model": str(getattr(cfg, "default_model", "") or ""),
    }


def _set_defaults(platform: Any, provider: str, model: str) -> None:
    """THE settings writer (validation, side effects, persistence); the tool's
    own Undo row journals it, so the writer's is not."""
    _writer(platform).apply(
        {"default_provider": provider, "default_model": model}, actor="chat", journal=False
    )


def _defaults_words(values: dict[str, Any] | None) -> str:
    v = values or {}
    prov = str(v.get("default_provider") or "")
    model = str(v.get("default_model") or "")
    if not prov and not model:
        return "none"
    return f"{model or 'its default model'} ({prov})" if prov else model


#: The node fields a person sets (an Undo checks only these were not changed
#: since; the sampler's readings — kind, capabilities — move on their own).
_NODE_USER_FIELDS = ("base_url", "label", "api_key_name", "protocol", "routable", "enabled", "default_model")


def _node_view(node: Any) -> dict[str, Any] | None:
    if node is None:
        return None
    return {k: getattr(node, k, None) for k in _NODE_USER_FIELDS}


def snap_model_server(platform: Any, node_id: str) -> dict[str, Any] | None:
    d = _d(platform)
    fleet = getattr(d, "fleet", None)
    node = fleet.get(node_id) if (fleet is not None and node_id) else None
    return _node_view(node)


def _stored_row(platform: Any, node_id: str) -> dict[str, Any] | None:
    for row in list(getattr(platform.config, "fleet_nodes", None) or []):
        if isinstance(row, dict) and str(row.get("id") or "") == node_id:
            return dict(row)
    return None


def _forget_key(platform: Any, d: Any, node_id: str, key_name: str) -> bool:
    """Delete the server's own key row (``endpoint_<id>_key``) when no other
    server names it. Returns True when one was removed. Never raises."""
    if not key_name or key_name != f"endpoint_{node_id}_key":
        return False
    try:
        if any(getattr(n, "api_key_name", "") == key_name for n in _top_nodes(d)):
            return False
        secrets = platform.secrets
        if not secrets.get(key_name):
            return False
        secrets.delete(key_name)
        return True
    except Exception:  # noqa: BLE001 — a leftover key is said, never a failure
        return False


def restore_model_server(platform: Any, node_id: str, meta: dict[str, Any]) -> str:
    """Undo of ``model_server_add``: the server goes (and the default model
    this change set comes back to the one before it) — only while the server
    is as this change left it. BLOCKING."""
    from ..daemon.routes.fleet import delete_fleet_node

    d = _d(platform)
    if d is None:
        raise R.RecordError(NEEDS_THE_APP)
    node = d.fleet.get(node_id)
    if node is None:
        raise R.RecordError(f"The model server {_q(node_id)} was removed since, so there is nothing to undo.")
    after = meta.get("after") if isinstance(meta.get("after"), dict) else None
    if after is not None and _node_view(node) != after:
        raise R.RecordError(
            f"The model server {_q(_node_label(node))} was changed since, so this Undo would undo that too: "
            "remove it in Settings › Connections instead."
        )
    kept_default = ""
    set_then = meta.get("defaults_set")
    if isinstance(set_then, dict):
        if _defaults(platform) == set_then:
            prior = meta.get("defaults_prior") if isinstance(meta.get("defaults_prior"), dict) else {}
            _set_defaults(platform, str(prior.get("default_provider") or ""), str(prior.get("default_model") or ""))
        else:
            kept_default = " (the default model was changed since, so it was kept)"
    key_name = str(getattr(node, "api_key_name", "") or "")
    try:
        delete_fleet_node(d, node_id)
    except Exception as exc:  # noqa: BLE001 — the route's own words
        raise R.RecordError(_http_problem(exc)) from None
    gone_key = " and its saved key" if _forget_key(platform, d, node_id, key_name) else ""
    return f"undo: removed the model server '{_node_label(node)}'{gone_key}{kept_default}"


def snap_model_default(platform: Any, _key: str) -> dict[str, Any]:
    return _defaults(platform)


def restore_model_default(platform: Any, meta: dict[str, Any]) -> str:
    """Undo of ``model_server_default`` — only while the default is still the
    one this change set. BLOCKING."""
    set_then = meta.get("defaults_set") if isinstance(meta.get("defaults_set"), dict) else None
    prior = meta.get("prior") if isinstance(meta.get("prior"), dict) else None
    if set_then is None or prior is None:
        raise R.RecordError("this change kept nothing to put back")
    if _defaults(platform) != set_then:
        raise R.RecordError(
            "The default model was changed since, so this Undo would undo that too: choose it in "
            "Settings › Models instead."
        )
    _set_defaults(platform, prior.get("default_provider") or "", prior.get("default_model") or "")
    return f"undo: the default model is {_defaults_words(prior)} again"


#: A server set up in Settings (``ollama`` / ``custom``) is made from these.
_SEED_KEYS = {"ollama": ("ollama_base_url", "ollama_model"), "custom": ("custom_base_url", "custom_model")}


def snap_model_server_removal(platform: Any, node_id: str) -> dict[str, Any] | None:
    d = _d(platform)
    node = d.fleet.get(node_id) if (d is not None and node_id) else None
    if node is None:
        return None
    seed = {k: getattr(platform.config, k, "") for k in _SEED_KEYS.get(node_id, ())}
    return {"row": _stored_row(platform, node_id), "seed": {k: v for k, v in seed.items() if v},
            "label": _node_label(node)}


def restore_model_server_removal(platform: Any, node_id: str, prior: dict[str, Any] | None) -> str:
    """Undo of ``model_server_remove``: the server as it was (a server set up
    in Settings comes back to its settings). Refused when one with that name
    is added again since. BLOCKING."""
    from ..daemon.routes.fleet import _catalog_changed
    from ..fleet.models import FleetNode

    d = _d(platform)
    if d is None:
        raise R.RecordError(NEEDS_THE_APP)
    if not isinstance(prior, dict):
        raise R.RecordError("this change kept nothing to bring back")
    if d.fleet.get(node_id) is not None:
        raise R.RecordError(f"A model server called {_q(node_id)} is added again, so there is nothing to bring back.")
    seed = dict(prior.get("seed") or {})
    if seed:
        _writer(platform).apply(seed, actor="chat", journal=False)
    row = prior.get("row")
    if isinstance(row, dict):
        d.fleet.add(FleetNode(**row))
    try:
        d.fleet.register_providers(d.platform.providers)
    except Exception:  # noqa: BLE001 — the next boot registers it
        pass
    _catalog_changed(node_id)
    return f"undo: brought back the model server '{prior.get('label') or node_id}'"


# --- tools ----------------------------------------------------------------
class ModelServerAddTool(R._RecordTool):
    name = "model_server_add"
    RECORD = MODEL_SERVER
    description = (
        "Add a model server the user runs — LM Studio, Ollama, vLLM, llama.cpp, a LiteLLM gateway, any "
        "OpenAI-compatible (or Anthropic-compatible) server (\"add my LM Studio server at "
        "192.168.1.20:1234\"): `address` is its address, `name` what to call it. If it needs a key, set "
        "`needs_key` true — NEVER pass the key: the user pastes it on a secure card. With `make_default`, "
        "its own model list is read and the default model is set from it: pass `model` (one of that "
        "list), or leave it out and you are told the list. The user approves on a card; Undo removes it."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "address": {"type": "string", "description": "e.g. http://192.168.1.20:1234 or http://dgx:11434"},
            "name": {"type": "string", "description": "What to call it (\"LM Studio\", \"DGX\")."},
            "needs_key": {"type": "boolean"},
            "protocol": {"type": "string", "enum": ["openai", "anthropic"]},
            "make_default": {"type": "boolean"},
            "model": {"type": "string", "description": "A model from the server's own list."},
        },
        "required": ["address"],
    }

    def redact_args(self, args: dict[str, Any]) -> dict[str, Any]:
        return _redacted(args)

    async def spend_check(self, args: dict[str, Any], ctx: ToolContext) -> SpendCheck | None:
        """No card for a call that is refused anyway (a key passed, a bad
        address, a server already added); else the card says WHERE it is."""
        if _key_passed(args):
            return _NOTHING
        address, problem = clean_address((args or {}).get("address"))
        if problem:
            return _NOTHING
        d = _d(self.platform)
        if d is not None and await asyncio.to_thread(_already_added, d, address):
            return _NOTHING
        words = f"Adds the model server {address} — {place_words(address)}."
        if (args or {}).get("make_default") is True:
            model = " ".join(str((args or {}).get("model") or "").split())[:80]
            words += f" It also makes {model + ' on it' if model else 'one of its models'} the default model."
        return SpendCheck(ask=True, words=words, dont_ask_skips=not _leaves_a_local_only_chat(address))

    async def capture_undo(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any] | None:
        _DESC.set(None)
        return _stash(await R.capture_create_undo(self.platform, MODEL_SERVER, "", pending=True))

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from fastapi import HTTPException

        from ..daemon.routes.fleet import FleetNodeBody, add_fleet_node

        if _key_passed(args):
            return ToolResult(ok=False, error=KEY_NOT_IN_CHAT)
        address, problem = clean_address(args.get("address"))
        if problem:
            return ToolResult(ok=False, error=problem)
        d = _d(self.platform)
        if d is None or getattr(d, "fleet", None) is None:
            return ToolResult(ok=False, error=NEEDS_THE_APP)
        same = await asyncio.to_thread(_already_added, d, address)
        if same:
            return ToolResult(ok=False, error=f"That server is already added as {_q(same)}.")
        label = " ".join(str(args.get("name") or "").split())[:60]
        protocol = str(args.get("protocol") or "openai").strip().lower() or "openai"
        keyed = args.get("needs_key") is True
        # Review: the id is fresh against the VAULT too when the server takes
        # a key. The Fleet page's Remove keeps ``endpoint_<id>_key`` (an Undo
        # of a removal needs it), so a new server under an old server's name
        # used to get that key name — and that key, sent to the NEW address
        # by the add's own detection and probe before the user pasted one.
        node_id = await asyncio.to_thread(_fresh_node_id, d, self.platform, address, label, keyed)
        if not node_id:
            return ToolResult(ok=False, error="Iron Jarvis could not choose a name for that server; give it another name.")
        key_name = f"endpoint_{node_id}_key" if keyed else ""
        body = FleetNodeBody(
            base_url=address, label=label, id=node_id, api_key_name=key_name, routable=True, protocol=protocol
        )
        try:
            out = await add_fleet_node(d, body)
        except HTTPException as exc:
            return ToolResult(ok=False, error=_http_problem(exc))
        R.bind_created(node_id)
        node = d.fleet.get(node_id)
        shown = label or node_id
        where = place_words(address)
        lines = [f"Added the model server {_q(shown)} at {address} ({where}). Iron Jarvis may use its models now."]
        if (out.get("snapshot") or {}).get("status") not in (None, "online") and not key_name:
            lines.append("It did not answer just now; it stays added and is checked again by itself.")
        data: dict[str, Any] = {"node_id": node_id}
        defaults_set: dict[str, str] | None = None
        defaults_prior = await asyncio.to_thread(_defaults, self.platform)
        if key_name:
            data["secret_requests"] = [_key_request(node_id, shown)]
            lines.append(
                "It needs a key: the user pastes it on the secure card below (never in the chat). Once it "
                "is saved, ask to make one of its models the default and its list will be read then."
            )
        elif args.get("make_default"):
            models, why = await read_models(d, node)
            chosen = _pick(models, str(args.get("model") or ""))
            if not models:
                lines.append(f"Its model list could not be read ({why}), so no default model was set.")
            elif args.get("model") and not chosen:
                lines.append(
                    f"{_q(args.get('model'))} is not in its list, so no default was set. It lists: {_listed(models)}."
                )
            elif not chosen and len(models) == 1:
                chosen = models[0]
            elif not chosen:
                lines.append(f"Which of its models should be the default? It lists: {_listed(models)}.")
            if chosen:
                await asyncio.to_thread(_set_defaults, self.platform, _provider_of(node), chosen)
                defaults_set = await asyncio.to_thread(_defaults, self.platform)
                lines.append(f"{chosen} on {shown} is now the default model.")
        node = d.fleet.get(node_id)
        _note(after=_node_view(node), defaults_set=defaults_set, defaults_prior=defaults_prior if defaults_set else None)
        card_key = f"{MODEL_DEFAULT}.{node_id}" if defaults_set else f"{MODEL_SERVER}.{node_id}"
        new = f"{address} · {where}" + (f" · default model {defaults_set['default_model']}" if defaults_set else "")
        card = R.create_card(MODEL_SERVER, node_id, new, label=f"Model server {_q(shown)}")
        if card:
            card["title"] = "Model server added"
            card["key"] = card_key
            data["record_change"] = card
        return ToolResult(ok=True, output=" ".join(lines), data=data)


def _key_row_free(platform: Any, node_id: str) -> bool:
    """No vault row ``endpoint_<id>_key`` exists yet (a read that fails reads
    as taken: never reuse a key that might be there). BLOCKING."""
    try:
        return not platform.secrets.get(f"endpoint_{node_id}_key")
    except Exception:  # noqa: BLE001 — fail closed
        return False


def _fresh_node_id(d: Any, platform: Any, address: str, label: str, keyed: bool) -> str:
    """The new server's id: the Fleet page's own rule (``_derive_id``), and —
    for a server that takes a key — one whose key row is EMPTY, so a key
    saved for an earlier server of that name is never sent to this one.
    "" when none is free. BLOCKING (a vault read)."""
    from ..daemon.routes.fleet import _derive_id

    node_id = _derive_id(d.fleet, address, label)
    if not keyed or _key_row_free(platform, node_id):
        return node_id
    taken = {getattr(n, "id", "") for n in d.fleet.nodes()}
    base = node_id[:27].rstrip("-") or "node"
    for n in range(2, 100):
        cand = f"{base}-{n}"
        if cand not in taken and _key_row_free(platform, cand):
            return cand
    return ""


def _already_added(d: Any, address: str) -> str:
    """The name of an added server at the same address ("" when none)."""
    from ..fleet.endpoint_auth import root_key

    want = root_key(address)
    if not want:
        return ""
    for n in _top_nodes(d):
        if root_key(getattr(n, "base_url", "") or "") == want:
            return _node_label(n)
    return ""


def _key_request(node_id: str, shown: str) -> dict[str, Any]:
    from ..core.ids import new_id

    return {
        "name": f"model_server.{node_id}",
        "label": f"{shown}'s key",
        "help": "",
        "why": f"to reach the model server {shown}",
        "request_id": new_id("seq"),
    }


class ModelServerDefaultTool(R._RecordTool):
    name = "model_server_default"
    RECORD = MODEL_DEFAULT
    description = (
        "Make one model of an added model server the default model (\"use qwen3 from my DGX server by "
        "default\"): `server` is the server's name or address, `model` one of ITS models. With no `model` "
        "you are told the server's own list (no card) — never guess a name. The user approves on a card; "
        "the change has an Undo."
    )
    input_schema = {
        "type": "object",
        "properties": {"server": {"type": "string"}, "model": {"type": "string"}},
        "required": ["server"],
    }

    def _resolve(self, args: dict[str, Any]) -> str:
        return "default_model"

    async def spend_check(self, args: dict[str, Any], ctx: ToolContext) -> SpendCheck | None:
        if not str((args or {}).get("model") or "").strip():
            return _NOTHING  # a list, nothing changes
        d = _d(self.platform)
        node, _p = find_server(d, (args or {}).get("server")) if d is not None else (None, "")
        if node is None:
            return _NOTHING
        model = str(args.get("model")).strip()
        current = await asyncio.to_thread(_defaults, self.platform)
        if current == {"default_provider": _provider_of(node), "default_model": model}:
            return _NOTHING
        # Review: the card says where THAT model answers — judged per model
        # (a LiteLLM gateway on this network can pass a name to a cloud
        # service), by the Local only lock's own reading.
        local = await asyncio.to_thread(self._stays, _provider_of(node), model)
        where = "on this PC or your own network" if local else "on the internet, so what is sent to it leaves this PC"
        return SpendCheck(
            ask=True,
            words=f"Makes {' '.join(model.split())[:80]} on {_node_label(node)} the default model — it answers {where}.",
            dont_ask_skips=not _leaves_a_local_only_chat(cloud=not local),
        )

    def _stays(self, provider: str, model: str) -> bool:
        try:
            from ..providers.egress import stays_on_this_network

            return bool(stays_on_this_network(
                provider, getattr(self.platform, "providers", None), getattr(self.platform, "config", None), model=model
            ))
        except Exception:  # noqa: BLE001 — unsure reads as leaving
            return False

    async def capture_undo(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any] | None:
        _DESC.set(None)
        return _stash(await super().capture_undo(args, ctx))

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        d = _d(self.platform)
        if d is None or getattr(d, "fleet", None) is None:
            return ToolResult(ok=False, error=NEEDS_THE_APP)
        node, problem = find_server(d, args.get("server"))
        if node is None:
            return ToolResult(ok=False, error=problem)
        models, why = await read_models(d, node)
        shown = _node_label(node)
        wanted = str(args.get("model") or "").strip()
        if not models:
            return ToolResult(
                ok=False, error=f"{shown}'s model list could not be read ({why}), so the default was not changed."
            )
        if not wanted:
            return ToolResult(ok=True, output=f"{shown} lists: {_listed(models)}. Ask for one of them by its name.")
        chosen = _pick(models, wanted)
        if not chosen:
            return ToolResult(
                ok=False, error=f"{_q(wanted)} is not in {shown}'s list, so nothing was changed. It lists: {_listed(models)}."
            )
        before = await asyncio.to_thread(_defaults, self.platform)
        target = {"default_provider": _provider_of(node), "default_model": chosen}
        if before == target:
            return ToolResult(ok=True, output=f"{chosen} on {shown} is already the default model; nothing changed.")
        try:
            await asyncio.to_thread(_set_defaults, self.platform, target["default_provider"], chosen)
        except Exception as exc:  # noqa: BLE001 — the writer's own sentence
            return ToolResult(ok=False, error=str(exc)[:300])
        after = await asyncio.to_thread(_defaults, self.platform)
        _note(defaults_set=after)
        data: dict[str, Any] = {}
        if R._CHANGE_ID.get():  # noqa: SLF001
            data["record_change"] = self._card(
                "Default model changed", "Default model", _defaults_words(before), _defaults_words(after),
                f"{MODEL_DEFAULT}.{node.id}",
            )
        return ToolResult(ok=True, output=f"{chosen} on {shown} is now the default model.", data=data)


class ModelServerRemoveTool(R._RecordTool):
    name = "model_server_remove"
    RECORD = MODEL_SERVER_REMOVED
    description = (
        "Remove a model server the user added (\"remove the DGX server\"): `server` is its name or address. "
        "Its models are no longer used; the Undo on the reply adds it back. The user approves on a card."
    )
    input_schema = {"type": "object", "properties": {"server": {"type": "string"}}, "required": ["server"]}

    def _target(self, args: dict[str, Any]) -> str:
        return str(args.get("server") or "").strip()

    def _resolve(self, args: dict[str, Any]) -> str:
        d = _d(self.platform)
        node, _p = find_server(d, self._target(args)) if d is not None else (None, "")
        return node.id if node is not None else ""

    async def spend_check(self, args: dict[str, Any], ctx: ToolContext) -> SpendCheck | None:
        try:
            found = await asyncio.to_thread(self._resolve, args)
        except Exception:  # noqa: BLE001
            return None
        return None if found else _NOTHING

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from fastapi import HTTPException

        from ..daemon.routes.fleet import delete_fleet_node

        d = _d(self.platform)
        if d is None or getattr(d, "fleet", None) is None:
            return ToolResult(ok=False, error=NEEDS_THE_APP)
        node, problem = find_server(d, self._target(args))
        if node is None:
            return ToolResult(ok=False, error=problem)
        shown = _node_label(node)
        was_default = str(getattr(self.platform.config, "default_provider", "") or "") in (
            _provider_of(node), node.id
        )
        try:
            out = await asyncio.to_thread(delete_fleet_node, d, node.id)
        except HTTPException as exc:
            return ToolResult(ok=False, error=_http_problem(exc))
        tail = ""
        if out.get("cleared_settings"):
            tail = " It was set up in Settings, so its address there was cleared too."
        if was_default:
            tail += " It held your default model: choose another default in Settings › Models."
        data: dict[str, Any] = {}
        if R._CHANGE_ID.get():  # noqa: SLF001
            data["record_change"] = self._card(
                "Model server removed", f"Model server {_q(shown)}", node.base_url, None, f"{MODEL_SERVER}.{node.id}"
            )
        return ToolResult(ok=True, output=f"Removed the model server {_q(shown)}.{tail}", data=data)


# =========================================================================== #
# Accounts (Iron-Proxy)
# =========================================================================== #
#: What a person calls each vendor → ``harness.build_accounts.VENDORS`` key.
_VENDOR_WORDS = {
    "claude": "claude", "claudecode": "claude", "anthropic": "claude",
    "codex": "codex", "chatgpt": "codex", "openai": "codex",
    "grok": "grok", "grokcli": "grok", "xai": "grok",
}


def _vendor(raw: Any) -> str:
    return _VENDOR_WORDS.get(_fold(raw), "")


def _svc(platform: Any) -> Any:
    return getattr(platform, "iron_proxy", None)


def _account_rows(svc: Any) -> list[dict[str, Any]]:
    from ..harness import build_accounts as BA

    snap = BA.snapshot(svc)
    if snap is None:
        return []
    out: list[dict[str, Any]] = []
    for vendor in BA.VENDORS:
        out += BA.vendor_rows(snap, vendor, include_off=True)
    return out


def snap_account(platform: Any, account_id: str) -> dict[str, Any] | None:
    return None  # an add: Undo removes it


def restore_account(platform: Any, account_id: str, meta: dict[str, Any]) -> str:
    """Undo of ``account_add`` — only while Iron-Proxy says the account still
    needs to sign in, and it was not renamed since. BLOCKING (Iron-Proxy's
    own answer is read, never the cached list)."""
    from ..iron_proxy.accounts import needs_sign_in
    from ..iron_proxy.client import IronProxyError

    svc = _svc(platform)
    after = meta.get("after") if isinstance(meta.get("after"), dict) else {}
    name = str(after.get("title") or account_id)
    if svc is None or not getattr(svc, "enabled", False):
        raise R.RecordError(
            f"Several accounts is off now, so the account {_q(name)} can't be removed: turn it on again first."
        )
    client = svc.client()
    if client is None:
        raise R.RecordError("Iron-Proxy is not running right now, so the account can't be removed; try again.")
    try:
        profiles = client.profiles()
        states = client.states()
    except IronProxyError as exc:
        raise R.RecordError(exc.sentence) from None
    prof = next((p for p in profiles or [] if isinstance(p, dict) and p.get("id") == account_id), None)
    if prof is None:
        raise R.RecordError(f"The account {_q(name)} was removed since, so there is nothing to undo.")
    if after and str(prof.get("title") or "") != str(after.get("title") or ""):
        raise R.RecordError(
            f"The account {_q(name)} was renamed since, so this Undo would undo that too: remove it on the card."
        )
    state = (states or {}).get(account_id) if isinstance(states, dict) else None
    if not needs_sign_in(state):
        raise R.RecordError(ACCOUNT_SIGNED_IN.format(name=name))
    # Review: Iron-Proxy's state can still read "unauthenticated" after the
    # login LANDED — it is told to re-check, it never notices by itself
    # (v1.303.3). So it is asked to re-check NOW, and only its fresh answer
    # may let the Undo remove the account; a re-check that fails keeps it.
    from ..iron_proxy.service import REFRESH_TIMEOUT_S

    try:
        rechecked = client.refresh(account_id, timeout=REFRESH_TIMEOUT_S)
    except Exception:  # noqa: BLE001 — not checked: never sign a login out
        raise R.RecordError(ACCOUNT_SIGNED_IN.format(name=name)) from None
    fresh = next(
        (x for x in (rechecked if isinstance(rechecked, list) else [])
         if isinstance(x, dict) and x.get("profileId") == account_id),
        None,
    )
    if not needs_sign_in(fresh):
        raise R.RecordError(ACCOUNT_SIGNED_IN.format(name=name))
    try:
        client.delete_profile(account_id)
    except IronProxyError as exc:
        raise R.RecordError(exc.sentence) from None
    try:
        svc.refresh_accounts()
    except Exception:  # noqa: BLE001 — the card's next read sees it gone
        pass
    return f"undo: removed the account '{name}' (it had not signed in)"


class AccountAddTool(R._RecordTool):
    name = "account_add"
    RECORD = ACCOUNT
    description = (
        "Add a Claude Code, Codex or Grok account (\"add a second Claude account\"): `vendor` is claude, "
        "codex or grok, `name` what to call it (\"Work\"). Iron-Proxy holds the account; its sign-in opens "
        "in a terminal in Build, where the user signs in in their own browser — never ask for a password "
        "or a code. Needs \"Several accounts\" switched on. The user approves on a card; Undo removes the "
        "account while it has not signed in."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "vendor": {"type": "string", "enum": ["claude", "codex", "grok"]},
            "name": {"type": "string"},
        },
        "required": ["vendor", "name"],
    }

    def redact_args(self, args: dict[str, Any]) -> dict[str, Any]:
        return _redacted(args)

    def _problem(self, args: dict[str, Any]) -> str:
        """Why the call is refused before anything is asked ("" when not)."""
        if _key_passed(args):
            return "A password or code is never passed in the chat: the user signs in in the account's own terminal."
        vendor = _vendor((args or {}).get("vendor"))
        if not vendor:
            return "Say which kind of account: Claude Code, Codex or Grok."
        name = " ".join(str((args or {}).get("name") or "").split())
        if not name:
            return "Give the account a name you will recognise (for example Work)."
        svc = _svc(self.platform)
        if svc is None or not getattr(svc, "enabled", False):
            return ACCOUNTS_OFF
        from ..harness import build_accounts as BA

        for row in _account_rows(svc):
            if row.get("vendor") == vendor and _fold(row.get("name")) == _fold(name):
                return f"There is already a {BA.VENDORS[vendor]['label']} account called {_q(name)}."
        return ""

    async def spend_check(self, args: dict[str, Any], ctx: ToolContext) -> SpendCheck | None:
        try:
            return _NOTHING if await asyncio.to_thread(self._problem, args) else None
        except Exception:  # noqa: BLE001 — unsure: the ordinary card
            return None

    async def capture_undo(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any] | None:
        _DESC.set(None)
        return _stash(await R.capture_create_undo(self.platform, ACCOUNT, "", pending=True))

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from fastapi import HTTPException

        from ..harness import build_accounts as BA
        from ..iron_proxy.client import IronProxyError

        problem = await asyncio.to_thread(self._problem, args)
        if problem:
            return ToolResult(ok=False, error=problem)
        d = _d(self.platform)
        if d is None:
            return ToolResult(ok=False, error=NEEDS_THE_APP)
        svc = _svc(self.platform)
        vendor = _vendor(args.get("vendor"))
        label = BA.VENDORS[vendor]["label"]
        name = " ".join(str(args.get("name") or "").split())[:60]
        client = await asyncio.to_thread(svc.client)
        if client is None:
            err = (svc.status() or {}).get("error") if hasattr(svc, "status") else ""
            return ToolResult(ok=False, error=str(err or "Iron-Proxy is not running right now; try again in a moment."))
        try:
            prof = await asyncio.to_thread(client.create_profile, BA.VENDORS[vendor]["proxy"], name, "cli")
        except IronProxyError as exc:
            from ..daemon.routes.iron_proxy import _said

            return ToolResult(ok=False, error=_said(exc))
        pid = str((prof or {}).get("id") or "")
        if not pid:
            return ToolResult(ok=False, error="Iron-Proxy did not say which account it made.")
        R.bind_created(pid)
        _note(after={"id": pid, "title": str(prof.get("title") or name), "provider": prof.get("provider")})
        try:
            await asyncio.to_thread(svc.refresh_accounts)
        except Exception:  # noqa: BLE001 — the card's next read lists it
            pass
        door = {"href": ACCOUNTS_HREF, "label": f"Sign in {name} on the accounts card"}
        try:
            from ..daemon.routes.iron_proxy import open_account_signin

            pane = await open_account_signin(d, svc, client, pid)
            door = {"href": f"/terminals?focus={pane['terminal_id']}", "label": f"Sign in {name} in its terminal"}
            how = (
                f"Its sign-in is open in a terminal in Build named {_q(pane.get('name') or 'Sign in: ' + name)}: "
                "the user finishes it there, in their own browser. Iron Jarvis never sees the login."
            )
        except (HTTPException, IronProxyError) as exc:
            how = (
                f"Its sign-in terminal could not be opened ({_http_problem(exc) if isinstance(exc, HTTPException) else exc.sentence}). "
                "The user signs it in with its Sign in button on the \"Several accounts (Iron-Proxy)\" card."
            )
        data: dict[str, Any] = {"account_id": pid, "door": door}
        card = R.create_card(ACCOUNT, pid, "needs to sign in", label=f"{label} account {_q(name)}")
        if card:
            card["title"] = "Account added"
            data["record_change"] = card
        return ToolResult(ok=True, output=f"Added the {label} account {_q(name)}. It still needs to sign in. {how}", data=data)


def account_group_ids(platform: Any, value: Any) -> tuple[Any, str]:
    """``account_groups`` with each account the user NAMED ("TSI") turned into
    its Iron-Proxy id, read from the CACHED account list (never a call). An id
    stays as it is; a name no account has stays too (the writer's own check
    refuses it in words); a name two accounts share is refused here. Pure on
    the cache; never raises."""
    try:
        if not isinstance(value, dict):
            return value, ""
        rows = _account_rows(_svc(platform))
        if not rows:
            return value, ""
        ids = {str(r.get("id")) for r in rows}
        out: dict[str, Any] = {}
        for group, items in value.items():
            if not isinstance(items, list):
                return value, ""
            got: list[Any] = []
            for raw in items:
                s = str(raw).strip() if isinstance(raw, str) else raw
                if not isinstance(s, str) or s in ids:
                    got.append(s)
                    continue
                bare = re.sub(r"\s*\((?:claude code|codex|grok)\)\s*$", "", s, flags=re.IGNORECASE)
                hits = [r for r in rows if _fold(r.get("name")) == _fold(bare) and _fold(bare)]
                if len(hits) > 1:
                    which = ", ".join(f"{r['name']} ({r['vendor_label']})" for r in hits)
                    return value, f"two accounts are called “{bare}” ({which}): name it with its kind"
                got.append(str(hits[0]["id"]) if hits else s)
            out[group] = got
        return out, ""
    except Exception:  # noqa: BLE001 — the value as sent; the writer checks it
        return value, ""


# =========================================================================== #
# Memory bases
# =========================================================================== #
#: What a person calls each kind → the record's kind.
_BASE_KINDS = {"notion": "notion", "folder": "markdown", "obsidian": "markdown", "markdown": "markdown",
               "notes": "markdown", "vault": "markdown"}
_NOTION_ID = re.compile(r"([0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12})")


def _slug(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]+", "_", (name or "").strip().lower()).strip("_")[:40]


def _network_path(text: str) -> bool:
    t = (text or "").strip()
    return t.startswith(("\\\\", "//")) or bool(re.match(r"^[a-zA-Z]+://", t))


def _folder_problem(path: str) -> str:
    """Why *path* cannot be a notes folder ("" when it can). BLOCKING."""
    from pathlib import Path

    from ..core.fs_policy import fs_read_ok

    p = (path or "").strip().strip('"')
    if not p:
        return "Give the folder's full location (for example D:\\vault)."
    if _network_path(p):
        return "A memory base folder must be on this PC, not a network share."
    folder = Path(p)
    if not folder.is_absolute():
        return "Give the folder's full location, starting with its drive (for example D:\\vault)."
    ok, why = fs_read_ok(folder)
    if not ok:
        return why
    if not folder.is_dir():
        return f"There is no folder at {p}."
    return ""


def _record_view(rec: Any) -> dict[str, Any] | None:
    if rec is None:
        return None
    return {k: getattr(rec, k, None) for k in ("kind", "path", "database_id", "token_secret", "roles_json")}


def _secret_exists(platform: Any, name: str) -> bool:
    """A vault row *name* holds a value (a read that fails reads as there:
    the Undo then keeps it). BLOCKING."""
    try:
        return bool(platform.secrets.get(name))
    except Exception:  # noqa: BLE001 — fail closed
        return True


def snap_memory_base(platform: Any, name: str) -> dict[str, Any] | None:
    from ..ltm.sources import CustomSourceStore

    return _record_view(CustomSourceStore(platform.engine).get(name)) if name else None


def _profiles_using(platform: Any, base: str, *, but: str = "") -> list[str]:
    try:
        from ..brain.profiles import BrainProfileStore

        return [p["name"] for p in BrainProfileStore(platform).list() if p.get("primary") == base and p.get("id") != but]
    except Exception:  # noqa: BLE001
        return []


def restore_memory_base(platform: Any, name: str, meta: dict[str, Any]) -> str:
    """Undo of ``memory_base_add``: the base goes (and the memory profile it
    made, and the profile choice before it) — only while the base and that
    profile are as this change left them, and never while another profile
    relies on the base. BLOCKING."""
    from ..brain.profiles import BrainProfileStore
    from ..daemon.routes.knowledge import remove_ltm_source_record
    from ..ltm.sources import CustomSourceStore

    d = _d(platform)
    if d is None:
        raise R.RecordError(NEEDS_THE_APP)
    rec = CustomSourceStore(platform.engine).get(name)
    if rec is None:
        raise R.RecordError(f"The memory base {_q(name)} was removed since, so there is nothing to undo.")
    after = meta.get("after") if isinstance(meta.get("after"), dict) else None
    if after is not None and _record_view(rec) != after:
        raise R.RecordError(
            f"The memory base {_q(name)} was changed since, so this Undo would undo that too: remove it on the "
            "Memory page instead."
        )
    made = meta.get("profile") if isinstance(meta.get("profile"), dict) else None
    others = _profiles_using(platform, name, but=str((made or {}).get("id") or ""))
    if others:
        raise R.RecordError(
            f"The memory profile {_q(others[0])} uses the memory base {_q(name)} now, so it was kept: change that "
            "profile first."
        )
    store = BrainProfileStore(platform)
    said = ""
    if made:
        now = store.get(str(made.get("id") or ""))
        if now is not None and (now.get("name"), now.get("primary")) != (made.get("name"), made.get("primary")):
            raise R.RecordError(
                f"The memory profile {_q(made.get('name'))} was changed since, so this Undo would undo that too."
            )
        chosen_now = str(getattr(platform.config, "brain_profile", "") or "")
        if now is not None:
            store.delete(str(made["id"]))
        prior = str(meta.get("profile_prior") or "")
        if chosen_now == made.get("id") and prior and store.get(prior) is not None:
            _writer(platform).apply({"brain_profile": prior}, actor="chat", journal=False)
            said = " and its memory profile (your earlier profile is chosen again)"
        else:
            said = " and its memory profile"
    remove_ltm_source_record(d, name)
    token = str(getattr(rec, "token_secret", "") or "")
    if token and token == f"ltm_{name}_notion" and meta.get("token_existed") is not True:
        try:
            platform.secrets.delete(token)
            said += "; its saved token was removed too"
        except Exception:  # noqa: BLE001
            pass
    return f"undo: removed the memory base '{name}'{said}"


class MemoryBaseAddTool(R._RecordTool):
    name = "memory_base_add"
    RECORD = MEMORY_BASE
    description = (
        "Add a memory base Iron Jarvis searches and saves to (\"use Notion as your long-term memory\", "
        "\"add my Obsidian vault at D:\\vault as memory\"): `kind` is notion or folder (an Obsidian vault "
        "is a folder), `name` what to call it, `folder` the folder's full location, `database` the Notion "
        "database's link or id. A Notion token is NEVER passed: the user pastes it on a secure card. "
        "`use_as_memory` also makes it the memory profile's primary base (where Jarvis saves what it "
        "learns). Works at once, no restart. The user approves on a card; Undo removes it."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["notion", "folder"]},
            "name": {"type": "string"},
            "folder": {"type": "string"},
            "database": {"type": "string", "description": "The Notion database's link or id."},
            "use_as_memory": {"type": "boolean"},
        },
        "required": ["kind", "name"],
    }

    def redact_args(self, args: dict[str, Any]) -> dict[str, Any]:
        return _redacted(args)

    def _plan(self, args: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """``(what to add, problem)``. BLOCKING (a folder is looked at)."""
        if _key_passed(args):
            return None, "A Notion token is never passed in the chat: the user pastes it on the secure card."
        kind = _BASE_KINDS.get(_fold((args or {}).get("kind")), "")
        if not kind:
            return None, "A memory base is a Notion database or a notes folder (an Obsidian vault is a folder)."
        name = " ".join(str((args or {}).get("name") or "").split())
        slug = _slug(name)
        if not slug:
            return None, "Give the memory base a name (for example Notion or Vault)."
        ltm = getattr(self.platform, "ltm", None)
        from ..ltm.sources import CustomSourceStore

        if slug in (ltm.sources() if ltm is not None else []) or CustomSourceStore(self.platform.engine).get(slug):
            return None, f"There is already a memory base called {_q(slug)}; choose another name."
        plan: dict[str, Any] = {"name": slug, "kind": kind}
        if kind == "markdown":
            path = str((args or {}).get("folder") or "").strip().strip('"')
            problem = _folder_problem(path)
            if problem:
                return None, problem
            plan["path"] = path
        else:
            m = _NOTION_ID.search(str((args or {}).get("database") or ""))
            if not m:
                return None, (
                    "A Notion memory base needs the Notion database to keep notes in: ask the user for the "
                    "database's link (Share → Copy link) and pass it as `database`."
                )
            plan["database_id"] = m.group(1).replace("-", "")
            plan["token_secret"] = f"ltm_{slug}_notion"
        return plan, ""

    async def spend_check(self, args: dict[str, Any], ctx: ToolContext) -> SpendCheck | None:
        try:
            plan, problem = await asyncio.to_thread(self._plan, args)
        except Exception:  # noqa: BLE001
            return None
        if problem or plan is None:
            return _NOTHING
        # The card says where what Jarvis saves would GO (a privacy call).
        if plan["kind"] == "notion":
            words = f"Adds the Notion database {plan['database_id']} as a memory base — Notion is a cloud service."
        else:
            words = f"Adds the folder {plan['path']} on this PC as a memory base."
        if (args or {}).get("use_as_memory") is True:
            words += " It also becomes where Jarvis saves what it learns (a new memory profile, chosen now)."
        return SpendCheck(ask=True, words=words,
                          dont_ask_skips=not _leaves_a_local_only_chat(cloud=plan["kind"] == "notion"))

    async def capture_undo(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any] | None:
        _DESC.set(None)
        return _stash(await R.capture_create_undo(self.platform, MEMORY_BASE, "", pending=True))

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from fastapi import HTTPException

        from ..daemon.routes.knowledge import add_ltm_source_record, remove_ltm_source_record
        from ..daemon.schemas import LTMSourceBody
        from ..ltm.sources import CustomSourceStore

        d = _d(self.platform)
        if d is None:
            return ToolResult(ok=False, error=NEEDS_THE_APP)
        plan, problem = await asyncio.to_thread(self._plan, args)
        if plan is None:
            return ToolResult(ok=False, error=problem)
        slug = plan["name"]
        # Review: a token row this change did not make (a base of that name
        # removed earlier keeps its token) is never deleted by its Undo.
        token_name = plan.get("token_secret", "")
        token_existed = bool(token_name) and await asyncio.to_thread(_secret_exists, self.platform, token_name)
        body = LTMSourceBody(
            name=slug, kind=plan["kind"], path=plan.get("path", ""), database_id=plan.get("database_id", ""),
            token_secret=plan.get("token_secret", ""),
        )
        try:
            await asyncio.to_thread(add_ltm_source_record, d, body)
        except HTTPException as exc:
            # "saved but not loadable": nothing half-made is left behind.
            if await asyncio.to_thread(CustomSourceStore(self.platform.engine).get, slug) is not None:
                await asyncio.to_thread(remove_ltm_source_record, d, slug)
            return ToolResult(ok=False, error=_http_problem(exc))
        R.bind_created(slug)
        rec = await asyncio.to_thread(CustomSourceStore(self.platform.engine).get, slug)
        what = f"the Notion database {plan['database_id']}" if plan["kind"] == "notion" else f"the folder {plan['path']}"
        lines = [f"Added the memory base {_q(slug)} ({what}). It is searched from the next message, with no restart."]
        data: dict[str, Any] = {}
        made: dict[str, Any] | None = None
        prior_choice = str(getattr(self.platform.config, "brain_profile", "") or "")
        if args.get("use_as_memory") is True:
            made, why = await asyncio.to_thread(self._make_profile, slug, str(args.get("name") or slug))
            if made:
                lines.append(f"It is now your memory profile's primary base (the profile {_q(made['name'])}).")
            else:
                lines.append(f"It was not made your memory profile ({why}).")
        if plan["kind"] == "notion":
            from ..core.ids import new_id

            data["secret_requests"] = [{
                "name": f"memory.{slug}",
                "label": f"{slug}'s Notion token",
                "help": "Your Notion integration's token (Settings → Connections → Develop or manage integrations).",
                "why": f"to read and save notes in {slug}",
                "request_id": new_id("seq"),
            }]
            lines.append(
                "It needs the Notion integration's token: the user pastes it on the secure card below (never "
                "in the chat), and shares the database with that integration in Notion. It works as soon as "
                "the token is saved."
            )
        _note(after=_record_view(rec), profile=made, profile_prior=prior_choice if made else None,
              token_existed=token_existed)
        key = f"memory_profile.{slug}" if made else f"{MEMORY_BASE}.{slug}"
        card = R.create_card(MEMORY_BASE, slug, what + (" · your memory profile's base" if made else ""),
                             label=f"Memory base {_q(slug)}")
        if card:
            card["title"] = "Memory base added"
            card["key"] = key
            data["record_change"] = card
        return ToolResult(ok=True, output=" ".join(lines), data=data)

    def _make_profile(self, base: str, label: str) -> tuple[dict[str, Any] | None, str]:
        """A new memory profile whose primary base is *base*, chosen for the
        app. BLOCKING."""
        from ..brain.profiles import BrainProfileStore, ProfileError

        store = BrainProfileStore(self.platform)
        taken = {_fold(p.get("name")) for p in store.list()}
        name = " ".join(label.split())[:50] or base
        if _fold(name) in taken:
            name = f"{name} memory"
        n = 2
        while _fold(name) in taken:
            name = f"{label[:44]} memory {n}"
            n += 1
        try:
            prof = store.create({"name": name, "primary": base})
            _writer(self.platform).apply({"brain_profile": prof["id"]}, actor="chat", journal=False)
        except (ProfileError, R.RecordError) as exc:
            return None, str(exc)
        except Exception as exc:  # noqa: BLE001
            return None, str(exc)[:200]
        return {"id": prof["id"], "name": prof["name"], "primary": prof["primary"]}, ""


def profile_id_for(platform: Any, value: Any) -> tuple[Any, str]:
    """``brain_profile`` named by the profile's NAME ("Work") → its id. An id
    or "" stays as it is; a name no profile has is refused in words. BLOCKING
    (one table read); never raises."""
    try:
        if not isinstance(value, str) or not value.strip() or re.fullmatch(r"bp_[A-Za-z0-9_-]{1,64}", value.strip()):
            return value, ""
        from ..brain.profiles import BrainProfileStore

        want = _fold(re.sub(r"\s+(?:memory\s+)?profile$", "", value.strip(), flags=re.IGNORECASE))
        rows = BrainProfileStore(platform).list()
        hits = [p for p in rows if _fold(p.get("name")) == want]
        if len(hits) == 1:
            return hits[0]["id"], ""
        have = ", ".join(p["name"] for p in rows)
        if len(hits) > 1:
            return value, f"two memory profiles are called {_q(value)}"
        tail = f" (the profiles: {have})" if have else " (there are no memory profiles yet)"
        return value, f"there is no memory profile called {_q(value)}{tail}"
    except Exception:  # noqa: BLE001
        return value, ""


def merged_account_groups(current: Any, new: Any) -> Any:
    """Review: chat's ``account_groups`` value read as a CHANGE to the user's
    groups, never a replacement of all of them. "Put TSI in a group called
    Firm" sends ``{"Firm": [TSI]}``; written as it was, it silently deleted
    every other group the user had. Now: a group the value names is set to
    its list (an EMPTY list removes it); a group it does not name is kept,
    minus the accounts the value puts elsewhere (one group per account — a
    group left with none is dropped, and the card shows the result); an empty
    value ``{}`` still clears them all. Group names match case-blind (the
    value's spelling wins). Pure; anything not a dict of lists is returned as
    sent (the writer's own check words it)."""
    if not isinstance(new, dict) or not isinstance(current, dict):
        return new
    if not new:
        return {}
    if any(not isinstance(v, list) for v in new.values()):
        return new
    named = {_fold(g) for g in new}
    moved = {str(i) for ids in new.values() for i in ids}
    out: dict[str, Any] = {}
    for group, ids in current.items():
        if _fold(group) in named:
            continue
        if not isinstance(ids, list):
            out[group] = ids
            continue
        kept = [i for i in ids if str(i) not in moved]
        if kept:
            out[group] = kept
    for group, ids in new.items():
        if ids:
            out[group] = ids
    return out


def chat_account_groups(platform: Any, value: Any) -> tuple[Any, str]:
    """``account_groups`` as chat sends it → the value to save: names turned
    into ids (:func:`account_group_ids`), then merged into the user's groups
    (:func:`merged_account_groups`). Reads the CACHED snapshot and the live
    config only. Never raises."""
    ids, problem = account_group_ids(platform, value)
    if problem:
        return value, problem
    try:
        current = getattr(platform.config, "account_groups", None) or {}
    except Exception:  # noqa: BLE001
        current = {}
    return merged_account_groups(current, ids), ""


def named_setting_value(platform: Any, key: str, value: Any) -> tuple[Any, str]:
    """The settings tools' value with the things the user NAMED turned into
    their ids (``account_groups``, ``brain_profile``). BLOCKING; never raises."""
    if key == "account_groups":
        from .tools import _coerce
        from . import schema

        return chat_account_groups(platform, _coerce(schema.get(key), value))
    if key == "brain_profile":
        return profile_id_for(platform, value)
    return value, ""


# =========================================================================== #
# Undo
# =========================================================================== #
def _snap_pending(_platform: Any, _name: str) -> None:
    return None


SNAP = {
    MODEL_SERVER: _snap_pending,
    MODEL_DEFAULT: snap_model_default,
    MODEL_SERVER_REMOVED: snap_model_server_removal,
    ACCOUNT: _snap_pending,
    MEMORY_BASE: _snap_pending,
}


def restore_kind(platform: Any, record: str, name: str, prior: Any, desc: dict[str, Any]) -> str:
    """``records.restore`` for this module's kinds. BLOCKING."""
    if record == MODEL_SERVER:
        return restore_model_server(platform, name, desc)
    if record == MODEL_DEFAULT:
        return restore_model_default(platform, desc)
    if record == MODEL_SERVER_REMOVED:
        return restore_model_server_removal(platform, name, prior)
    if record == ACCOUNT:
        return restore_account(platform, name, desc)
    if record == MEMORY_BASE:
        return restore_memory_base(platform, name, desc)
    raise R.RecordError(f"unknown record kind {record!r}")


def connections_record_tools(platform: Any) -> list[Any]:
    return [
        ModelServerAddTool(platform),
        ModelServerDefaultTool(platform),
        ModelServerRemoveTool(platform),
        AccountAddTool(platform),
        MemoryBaseAddTool(platform),
    ]


# =========================================================================== #
# Arming: which of these a chat message asks for
# =========================================================================== #
#: A model server by the program it runs or what it is called.
_BRAND = (
    r"(?:lm\s*studio|ollama|vllm|llama\.?cpp|llama[\s-]server|litellm|sglang|koboldcpp|localai"
    r"|text[\s-]generation[\s-]inference|tabbyapi)"
)
_SERVER_NOUN = r"(?:servers?|box(?:es)?|machines?|endpoints?|gateway|instance|rig)"
#: Review: words after a server's name that make it someone's CONTENT ("the
#: AI server costs", "the Ollama server docs", "the GPU box specs").
_NOT_AFTER_SERVER = (
    r"(?!\s+(?:costs?|prices?|pricing|market|markets|rack|racks|room|rooms|cabinet|specs?|"
    r"specifications|quotes?|budget|names?|address(?:es)?|info|information|details|logs?|docs?|"
    r"documentation|diagram|manual|guide|invoice|bill|warranty|purchase|order)\b)"
)
_QUALIFIED = (
    r"(?:(?:model|inference|llm|ai|gpu|local[\s-]model|openai[\s-]compatible|anthropic[\s-]compatible)\s+"
    + _SERVER_NOUN + r")"
)
_ADDRESS = r"(?:https?://[^\s,;]{1,200}|\b\d{1,3}(?:\.\d{1,3}){3}(?::\d{1,5})?\b|\blocalhost:\d{1,5}\b|\b[a-z][\w-]{0,40}:\d{2,5}\b)"
#: Servers that are something else (a game's, a chat app's, a mail server).
_NOT_A_MODEL_SERVER = (
    r"(?:minecraft|discord|game|gaming|mail|e-?mail|smtp|imap|web|http|file|print|sql|database|db|dns"
    r"|dhcp|vpn|ftp|media|plex|exchange|teams|slack|mcp|git|build|dev|staging|production|prod|backup"
    r"|nas|valheim|terraria|ark|rust|radius|ldap|proxy|time|ntp|sftp|ssh|jenkins|sharepoint|wordpress"
    r"|application|app|api|test|home|license|sync|print)"
)
#: Review: a server's name may be two or three words ("the DGX Spark
#: server"); every word is checked against the other kinds of server.
_NAMED_SERVER = (
    r"\b(?:the|my|our|that)\s+(?:(?!" + _NOT_A_MODEL_SERVER + r"\b)[a-z][\w.-]{1,30}\s+){1,3}"
    r"(?:servers?|box|machine|node)\b"
)
_SERVER_CTX = re.compile(r"\b" + _BRAND + r"\b|\b" + _QUALIFIED + r"\b|" + _NAMED_SERVER, re.IGNORECASE)
_BRAND_THING = re.compile(
    r"\b" + _BRAND + r"(?:\s+(?:server|box|machine|endpoint|instance|gateway|node|rig))?\b" + _NOT_AFTER_SERVER,
    re.IGNORECASE,
)
_ADDRESS_RX = re.compile(_ADDRESS, re.IGNORECASE)
_ADD_VERB = re.compile(
    r"\b(?:add|connect|register|hook\s+up|set\s+up|plug\s+in|point\s+(?:you|jarvis|iron\s+jarvis)\s+(?:at|to))\b",
    re.IGNORECASE,
)
_SERVER_THING = re.compile(
    r"(?:\b(?:" + _BRAND + r")\s+(?:server|box|machine|endpoint|instance|gateway|node|rig)\b|\b" + _QUALIFIED
    + r"\b)" + _NOT_AFTER_SERVER,
    re.IGNORECASE,
)
#: Review: with an address in the clause, a server noun counts only when the
#: word before it is not another kind of server ("set up the Plex server at …").
_PLAIN_SERVER = re.compile(
    r"\b(?!" + _NOT_A_MODEL_SERVER + r"\b)[a-z][\w.-]*\s+(?:servers?|endpoints?|gateway)\b" + _NOT_AFTER_SERVER
    + r"|^\s*(?:servers?|endpoints?|gateway)\b|\bmodels?\b",
    re.IGNORECASE,
)
#: Review: a verb that also means a CABLE ("connect the GPU box to the
#: monitor") counts for a model server only with a program's name or an
#: address in the clause.
_CABLE_VERB = re.compile(r"\b(?:connect|hook\s+up|plug\s+in)\b", re.IGNORECASE)
#: …and never when it joins the thing to a DEVICE ("…to the network switch").
_TO_A_DEVICE = re.compile(
    r"\b(?:to|into)\s+(?:the|my|our|a|an|this|that)\s+(?:[\w-]+\s+)?(?:monitors?|screens?|tvs?|printers?|"
    r"switch(?:es)?|routers?|network|wi-?fi|ups|power|outlets?|keyboards?|mouse|docks?|hubs?|cables?|"
    r"internet|vpn|domain|nas|projector|speakers?|camera)\b",
    re.IGNORECASE,
)
#: Review: the clause puts the thing INTO the user's content ("…to the
#: budget", "…in the asset list"), never a change to Iron Jarvis.
_INTO_CONTENT = re.compile(
    r"\b(?:to|into|in|on|onto|from)\s+(?:the|my|our|a|an|this|that|your)\s+(?:[\w-]+\s+){0,2}?"
    r"(?:budget|list(?!\s+of\s+(?:model\s+)?(?:servers|models|endpoints))|checklist|inventory|spreadsheet|sheet|doc|document|diagram|invoice|report|e-?mail|"
    r"letter|slides?|deck|notes|proposal|quote|readme|file|wiki|page|requirements|bookmarks|agenda|"
    r"minutes|memo|newsletter|presentation)\b",
    re.IGNORECASE,
)
#: Review: a writing task ABOUT these things ("draft an email to set up a
#: Codex account trial") is the user's content.
_WRITING_TASK = re.compile(
    r"\b(?:draft|write|compose|summari[sz]e|explain|describe|compare)\b[^.?!\n]{0,40}?"
    r"\b(?:e-?mails?|letters?|memos?|notes?|messages?|posts?|docs?|documents?|paragraphs?|"
    r"guides?|summar(?:y|ies)|reports?|slides?|tables?|lists?)\b",
    re.IGNORECASE,
)
_USE_AT = re.compile(r"\buse\b[^.?!\n]{0,60}?\b(?:at|on)\s+" + _ADDRESS, re.IGNORECASE)
_DEFAULT_CUE = re.compile(
    r"\b(?:make|set|use|switch\s+to|choose|pick)\b[^.?!\n]{0,80}?\b(?:(?:my|the|your|as)\s+)?default\b"
    r"|\bby\s+default\b"
    r"|\b(?:switch|change|set|move)\s+(?:my|the|your)\s+default(?:\s+model)?\s+(?:to|over\s+to)\b",
    re.IGNORECASE,
)
_REMOVE_VERB = re.compile(
    r"\b(?:remove|delete|forget|disconnect|drop|get\s+rid\s+of|stop\s+using|unregister)\b", re.IGNORECASE
)

#: A Claude Code / Codex / Grok account (an Iron-Proxy account).
_VENDOR = r"(?:claude(?:\s+code)?|codex|chatgpt|openai|grok|xai)"
_ACCOUNT_ADD = re.compile(
    r"\b(?:add|set\s+up|connect|create|register|link|make)\b[^.?!\n]{0,30}?"
    r"\b(?:(?:a|an|another|my|one\s+more|a\s+second|second|third|new|work|personal|other|extra|separate"
    r"|spare|backup)\s+){0,3}" + _VENDOR + r"\s+(?:max\s+|pro\s+|plus\s+|team\s+)?"
    r"(?:accounts?|logins?|subscriptions?|plans?)\b"
    # Review: …never a thing ABOUT the account ("the Claude account
    # details", "a ChatGPT plan comparison", "a Codex account trial")…
    r"(?!\s+(?:details?|info(?:rmation)?|comparisons?|summar(?:y|ies)|numbers?|pages?|pric(?:e|es|ing)|"
    r"costs?|invoices?|bills?|billing|receipts?|settings|trials?|passwords?|e-?mails?|letters?|docs?|"
    r"documents?|notes|reviews?|reports?|overviews?|guides?|features?|benefits|tiers?|limits?|usage|"
    r"statements?|sections?|instructions|steps|questions?|options|types|charges?|fees?|renewals?|"
    r"cancellations?|tables?|lists?|managers?|roles?|reps?|representatives?|executives?|holders?|owners?|"
    r"teams?|ids?|codes?)\b)"
    # …nor one for someone else ("for my daughter's school project")…
    r"(?!\s+for\s+(?:each|every|all)\b)"
    r"(?!\s+for\s+(?:(?:my|his|her|their|our|the|a|an)\s+)?(?:daughter|son|kids?|children|child|wife|"
    r"husband|partner|mom|mother|dad|father|friend|clients?|interns?|employees?|staff|students?|"
    r"school|class|niece|nephew|brother|sister|grandma|grandpa|parents?)\b)"
    # …never into another product ("add my ChatGPT account TO Zapier").
    r"(?!\s+(?:to|into|in|on|with|inside)\s+(?!(?:iron\s+)?jarvis\b|you\b|this\s+(?:pc|app|computer)\b"
    r"|here\b|several\b|iron[\s-]proxy\b))",
    re.IGNORECASE,
)
_GROUP_VERB = re.compile(
    r"\b(?:put|add|move|place|group|split|separate|keep)\b[^.?!\n]{0,80}?\b(?:in|into|to|as)\s+"
    r"(?:(?:a|an|the|one|its\s+own|their\s+own|separate|different|new|two)\s+){0,2}groups?\b"
    r"(?!\s+(?:chats?|texts?|messages?|calls?|threads?|emails?|photos?|projects?|lessons?|trips?|albums?"
    r"|plans?|discounts?|rates?|bookings?|tours?|sessions?|therapy|polic(?:y|ies))\b)",
    re.IGNORECASE,
)
#: Review: the words "accounts" / "logins" / "subscriptions" themselves — a
#: vendor's name alone is not one ("put my Claude notes in a group").
_ACCOUNT_WORDS = re.compile(r"\b(?:accounts?|logins?|subscriptions?)\b", re.IGNORECASE)
_ACCOUNT_GROUPS = re.compile(r"\baccount\s+groups?\b|\bgroups?\s+(?:of|for)\s+(?:my\s+)?accounts\b", re.IGNORECASE)

#: A memory base (Notion / a notes folder) as Jarvis's memory.
_MEMORY_PLACE = r"(?:notion|obsidian|(?:my\s+)?(?:notes?\s+)?(?:vault|folder)|[a-z]:\\[^\s]{1,120})"
_AS_MEMORY = (
    r"\bas\s+(?:your|my|the|a|jarvis'?s?)?\s*(?:long[\s-]term\s+|durable\s+|permanent\s+|second\s+|main\s+)?"
    r"(?:memory|memories|brain|memory\s+base|knowledge\s+base)\b"
)
_MEMORY_RULES = (
    re.compile(
        r"\b(?:use|make|set\s+up|add|connect|keep|treat|point)\b[^.?!\n]{0,40}?\b" + _MEMORY_PLACE
        + r"[^.?!\n]{0,60}?" + _AS_MEMORY,
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:add|connect|set\s+up|create|register|use)\b[^.?!\n]{0,60}?\bmemory\s+bases?\b(?!\s+(?:class|code|file|schema|table))", re.IGNORECASE),
    re.compile(
        r"\b(?:remember|save|keep|store|put)\b[^.?!\n]{0,20}?\b(?:things|everything|what\s+you\s+learn|"
        r"what\s+you\s+remember|your\s+(?:notes|memories)|memories|long[\s-]term\s+memory)\b"
        # Review: "save everything ABOUT the Smith audit in Notion" is content.
        r"(?!\s+(?:about|on|regarding|concerning|from|related|relating|of)\b)[^.?!\n]{0,30}?"
        r"\b(?:in|to|into)\s+(?:my\s+)?(?:notion|obsidian)\b",
        re.IGNORECASE,
    ),
)

#: The longest message the matchers read (a pasted page is not a request).
_MAX_TEXT = 20_000


_PATH_RX = re.compile(r"\b[a-zA-Z]:\\[^\s]{0,200}")


def without_addresses(text: str) -> str:
    """*text* with every address and Windows path read as one word — so a
    question test never cuts a sentence at an IP's or a file name's dots.
    Linear; never raises."""
    try:
        t = str(text or "")[:_MAX_TEXT]
        return _PATH_RX.sub("place", _ADDRESS_RX.sub("address", t))
    except Exception:  # noqa: BLE001
        return str(text or "")


def _account_names() -> tuple[str, ...]:
    """The account names Iron-Proxy lists now — the CACHED snapshot of the
    registered service, never a call. ``()`` when off or not read."""
    try:
        from ..iron_proxy.service import current

        return tuple(sorted({str(r.get("name") or "") for r in _account_rows(current()) if r.get("name")}))
    except Exception:  # noqa: BLE001
        return ()


def connections_tools_for(text: str) -> tuple[str, ...]:
    """The tools of this module (and ``config_change_protected`` for an
    account group) a chat message asks for, in a fixed order. Called ONCE
    from ``records.record_tools_for`` (which drops writers for a question).
    Linear-time matchers only; never raises."""
    return _connections_tools_for(str(text or "")[:_MAX_TEXT], _account_names())


@lru_cache(maxsize=256)
def _connections_tools_for(t: str, names: tuple[str, ...]) -> tuple[str, ...]:
    if not t.strip():
        return ()
    out: list[str] = []
    try:
        for clause in re.split(r"(?<=[.!?;\n])\s+|\s+(?:and\s+then|then|but)\s+", t)[:40]:
            out += _clause_tools(clause, names)
    except Exception:  # noqa: BLE001 — a matcher fault arms nothing new
        return ()
    return tuple(dict.fromkeys(out))


def _clause_tools(c: str, names: tuple[str, ...]) -> list[str]:
    out: list[str] = []
    if _WRITING_TASK.search(c):
        return out  # review: writing ABOUT a server or an account is content
    ctx = bool(_SERVER_CTX.search(c))
    address = bool(_ADDRESS_RX.search(c))
    server_thing = bool(_SERVER_THING.search(c)) or bool(
        re.search(_NAMED_SERVER + _NOT_AFTER_SERVER, c, re.IGNORECASE)
    )
    brand = bool(_BRAND_THING.search(c))
    into_content = bool(_INTO_CONTENT.search(c))
    add = _ADD_VERB.search(c)
    if add and _CABLE_VERB.fullmatch(add.group(0)) and (not (brand or address) or _TO_A_DEVICE.search(c)):
        add = None  # review: "connect the GPU box to the monitor" is a cable
    if into_content:
        add = None  # review: "add the AI server to the asset list"
    if add and (server_thing or (brand and address) or (address and _PLAIN_SERVER.search(c))):
        out += ["model_server_add", "model_server_default"]
    elif not into_content and _USE_AT.search(c) and (brand or server_thing):
        out += ["model_server_add", "model_server_default"]
    if _DEFAULT_CUE.search(c) and (ctx or address):
        out.append("model_server_default")
    if not into_content and _REMOVE_VERB.search(c) and (server_thing or (brand and address)):
        out.append("model_server_remove")
    if _ACCOUNT_ADD.search(c):
        out.append("account_add")
    grouped = bool(_ACCOUNT_GROUPS.search(c))
    if not grouped and not into_content and _GROUP_VERB.search(c):
        if _ACCOUNT_WORDS.search(c):
            grouped = True
        elif names:
            # Review: an account's NAME counts only where it stands as the
            # thing moved ("put TSI and VR2 in…", "move Personal into…") —
            # never as a word inside another noun phrase ("the Work files",
            # "the personal photos", "the TSI invoices").
            low = c.lower()
            grouped = any(
                re.search(
                    r"\b" + re.escape(n.lower())
                    + r"(?:\s+(?:accounts?|logins?))?\s*(?:,|&|\band\b|\bor\b|\b(?:in|into|to|as|together)\b|$)",
                    low,
                )
                for n in names if len(n) >= 2
            )
    if grouped:
        out.append("config_change_protected")
    if any(rx.search(c) for rx in _MEMORY_RULES):
        out.append("memory_base_add")
    return out


#: The tests clear the cache through the public name.
connections_tools_for.cache_clear = _connections_tools_for.cache_clear  # type: ignore[attr-defined]
