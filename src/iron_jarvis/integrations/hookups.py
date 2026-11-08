"""The user's REST hookups, by the names they gave them (v1.357.0, G84).

A *hookup* is a REST integration the user saved (Settings › Connections ›
"More ways to connect": a name, a web address, a key) — ``POST /integrations``
registers it in the :class:`IntegrationRegistry` as kind ``rest`` and keeps the
key in the vault. Until this release nothing could call one; now the
``rest_call`` tool (integrations/tools.py) does, and chat finds the hookup a
message names here.

Everything in this module reads the registry's IN-MEMORY specs only (their ids
and names), so it may run on the event loop; whether a hookup is turned on and
where it points live in the database and are read by the tool, off the loop.
Never raises.
"""

from __future__ import annotations

import re
from typing import Any

#: The integration kind a hookup is registered as (routes/comm.add_integration,
#: platform.py's boot re-registration, ``builtin.REST_SPEC``).
HOOKUP_KIND = "rest"
#: A name shorter than this is never matched in a sentence ("AI", "Q").
_MIN_NAME = 3
#: The longest message text the matcher reads (the app matcher's bound).
_MAX_TEXT = 4000
#: The built-in EXAMPLE hookup (``builtin.REST_SPEC``, "Generic REST API"):
#: always registered, saved by nobody unless they configure it. The tool can
#: still find it by name (:func:`find_hookup`), but a list of "your hookups"
#: does not offer it and a message never ARMS the tool by naming it — its id
#: words are "REST API", which any developer's question says ("how do I build
#: a REST API in Flask?" ask-armed ``rest_call`` on an install with no hookup
#: saved; v1.357.0 review).
EXAMPLE_IDS: frozenset[str] = frozenset({"rest_api"})
#: "<display name> (<id>)" — the exact form :func:`hookup_names` lists in the
#: tool's spec, so a model that copies it finds the hookup.
_LISTED_RX = re.compile(r"^(?P<shown>.*\S)\s*\((?P<sid>[a-z0-9_]+)\)$")


def rest_hookups(registry: Any) -> list[Any]:
    """Every registered hookup spec (kind ``rest``), ordered by id. In-memory."""
    try:
        return [s for s in registry.specs() if str(getattr(s, "kind", "")) == HOOKUP_KIND]
    except Exception:  # noqa: BLE001 — a registry double without specs
        return []


def _slug(text: str) -> str:
    """The id rule ``POST /integrations`` uses for a hookup's name."""
    return re.sub(r"[^a-z0-9_]+", "_", str(text or "").strip().lower()).strip("_")


def find_hookup(registry: Any, name: str) -> Any:
    """The hookup *name* means — its id, its display name (any case), the id
    its name would get, or the ``"<display name> (<id>)"`` line the tool's
    spec lists (v1.357.0 review: that exact line was refused AFTER the user
    had approved the card) — else None."""
    want = str(name or "").strip()
    if not want:
        return None
    listed = _LISTED_RX.match(want)
    if listed is not None:
        sid_wanted = listed.group("sid")
        for spec in rest_hookups(registry):
            if str(getattr(spec, "id", "")) == sid_wanted:
                return spec
    slug = _slug(want)
    for spec in rest_hookups(registry):
        sid = str(getattr(spec, "id", ""))
        shown = str(getattr(spec, "display_name", "") or "")
        if want == sid or want.casefold() == shown.casefold() or (slug and slug in (sid, _slug(shown))):
            return spec
    return None


def hookup_names(registry: Any) -> list[str]:
    """``"<display name> (<id>)"`` for each hookup the user added — what the
    tool's spec tells the model it may name (the built-in example is left
    out: :data:`EXAMPLE_IDS`). Names only, never an address or a key."""
    out: list[str] = []
    for spec in rest_hookups(registry):
        sid = str(getattr(spec, "id", ""))
        if sid in EXAMPLE_IDS:
            continue
        shown = str(getattr(spec, "display_name", "") or sid)
        out.append(shown if shown == sid else f"{shown} ({sid})")
    return out


def _name_rx(words: str) -> "re.Pattern[str] | None":
    parts = [w for w in re.split(r"[\s_\-]+", str(words or "").strip()) if w]
    if not parts or len("".join(parts)) < _MIN_NAME:
        return None
    body = r"[\s_\-]+".join(re.escape(w) for w in parts)
    return re.compile(rf"(?<![\w]){body}(?![\w])", re.IGNORECASE)


def hookups_named(registry: Any, text: str) -> list[str]:
    """The ids of the hookups *text* names, by display name or id (word-bounded,
    any case, spaces/underscores/hyphens alike), in id order. ``[]`` for none.
    The built-in example (:data:`EXAMPLE_IDS`) is never named this way."""
    t = str(text or "")[:_MAX_TEXT]
    if not t.strip():
        return []
    out: list[str] = []
    try:
        for spec in rest_hookups(registry):
            sid = str(getattr(spec, "id", ""))
            if sid in EXAMPLE_IDS:
                continue  # never armed by name
            shown = str(getattr(spec, "display_name", "") or "")
            for words in (shown, sid):
                rx = _name_rx(words)
                if rx is not None and rx.search(t):
                    out.append(sid)
                    break
    except Exception:  # noqa: BLE001 — a matcher fault names nothing
        return []
    return out


#: The tool chat ask-arms when a message names a hookup.
REST_CALL_TOOL = "rest_call"


def named_hookup_ask_tools(
    platform: Any,
    text: str,
    armed: Any,
    ask_armed: Any,
    *,
    low_trust: bool,
    local_only: bool,
) -> list[str]:
    """THE STREAM LANE'S HOOKUPS-BY-NAME ARMING (v1.357.0, G84): ``["rest_call"]``
    when the message names a saved hookup, else ``[]``.

    The mirror of the v1.354.0 apps-by-name rule
    (``chat_turn.named_app_ask_tools``): the tool is ASK-armed — visible to
    the model, never granted, so every call shows the ordinary approval card —
    and only by the STREAM lane (the POST lane, which can ask nobody, never
    arms it by name). A turn that started in low trust, or a local-only turn,
    arms nothing here: the call reaches outside this PC, and those turns keep
    the "+" menu as the one door (where a local-only turn cards every call,
    ``providers.egress.reaches_outside``). A name already armed is not armed
    twice. *platform* carries both registries: the tool registry
    (``platform.registry``) and the hookups (``platform.integrations``).
    Never raises."""
    try:
        if low_trust or local_only:
            return []
        if REST_CALL_TOOL in set(armed or []) | set(ask_armed or []):
            return []
        if platform.registry.get(REST_CALL_TOOL) is None:
            return []
        named = hookups_named(getattr(platform, "integrations", None), text)
        return [REST_CALL_TOOL] if named else []
    except Exception:  # noqa: BLE001 — arming stays as it was
        return []


# --------------------------------------------------------------------------- #
# THE add and remove of a hookup (v1.448.0, Wave A item 4). ``POST
# /integrations`` (the page's "Add a service") and the chat's
# ``web_service_add`` (settings/records_tools) both come through here, so the
# id rule, the spec, the stored config and what survives a restart cannot
# drift. Nothing here logs, returns or raises a VALUE — names only.
# --------------------------------------------------------------------------- #
#: v1.455.0 (Wave H item 31): the add door's refusals, in plain words.
NAME_REQUIRED = "Give the web service a name (letters and digits)."
ADDRESS_REQUIRED = "Give the web service's address (it starts with http:// or https://)."
NAME_IN_USE = "A web service named “{name}” is already saved. Give this one another name."


def hookup_id(name: Any) -> str:
    """The id a hookup's display name gets (the page's own rule)."""
    return _slug(str(name or ""))


def add_hookup(
    platform: Any,
    *,
    name: str,
    base_url: str,
    description: str = "",
    auth_secret: str = "",
    allow_internal: bool = False,
    persist: Any = None,
    auth_kind: str = "bearer",
    auth_header: str = "",
    auth_user: str = "",
) -> str:
    """Register a REST hookup live, store its config and keep it in
    ``config.custom_integrations`` so it survives a restart; returns its id.
    ``auth_secret`` is the VAULT NAME of its bearer token (the caller writes
    the value — the page's route from its body, the chat's card later; an
    empty name = no token). ``allow_internal`` lets its address be on the
    user's own network (the chat door sets it for a private address; the
    page's route never did). Raises ``ValueError`` in plain words for a
    missing name or address or a name in use. BLOCKING.

    v1.455.0 (readiness Wave H item 31): ``auth_kind`` says how the key
    travels (``builtin.AUTH_KINDS``: bearer — the default, stored as nothing
    so an older config reads the same — header, basic); ``auth_header`` names
    the header for ``header``, ``auth_user`` the username for ``basic``. The
    KEY itself is only ever the vault row ``auth_secret`` names. The refusals
    are plain words (they used to say "integration name is required")."""
    from .base import IntegrationSpec
    from .builtin import REST_SPEC, RestApiIntegration, auth_problem

    shown = str(name or "").strip()
    iid = hookup_id(shown)
    if not iid:
        raise ValueError(NAME_REQUIRED)
    if not str(base_url or "").strip():
        raise ValueError(ADDRESS_REQUIRED)
    kind = str(auth_kind or "bearer").strip().lower()
    problem = auth_problem(kind, auth_header, auth_user)
    if problem:
        raise ValueError(problem)
    registry = platform.integrations
    if registry.get_spec(iid) is not None:
        raise ValueError(NAME_IN_USE.format(name=shown or iid))
    registry.register(
        IntegrationSpec(
            id=iid,
            kind=HOOKUP_KIND,
            display_name=shown,
            description=str(description or "").strip(),
            required_secrets=[],
            config_schema=REST_SPEC.config_schema,
        ),
        lambda cfg, resolver: RestApiIntegration(cfg, resolver),
    )
    config: dict[str, Any] = {"base_url": str(base_url).strip()}
    if auth_secret:
        config["auth_secret"] = str(auth_secret)
    if allow_internal:
        config["allow_internal"] = True
    if kind == "header":
        config["auth_kind"] = "header"
        config["auth_header"] = str(auth_header).strip()
    elif kind == "basic":
        config["auth_kind"] = "basic"
        config["auth_user"] = str(auth_user)
    registry.configure(iid, config)
    registry.enable(iid, True)
    customs = [c for c in (platform.config.custom_integrations or []) if c.get("id") != iid]
    customs.append({"id": iid, "name": shown, "description": str(description or "").strip()})
    platform.config.custom_integrations = customs
    _persist(platform, persist)
    return iid


def remove_hookup(platform: Any, iid: str, *, persist: Any = None) -> dict[str, Any]:
    """Remove a hookup the user added: its spec (live), its stored row, its
    ``custom_integrations`` entry and — when its config named the hookup's
    OWN token row (``integration_<id>_token``) — that vault row. The built-in
    example is never removed. Returns what went; raises ``KeyError`` for an
    unknown id. BLOCKING."""
    registry = platform.integrations
    if iid in EXAMPLE_IDS or registry.get_spec(iid) is None:
        raise KeyError(iid)
    cfg = dict(registry.stored_config(iid) or {})
    key_name = str(cfg.get("auth_secret") or "")
    key_removed = False
    if key_name == f"integration_{iid}_token":
        try:
            key_removed = bool(platform.secrets.delete(key_name))
        except Exception:  # noqa: BLE001 — a leftover row is not a failed removal
            key_removed = False
    registry.unregister(iid)
    customs = [c for c in (platform.config.custom_integrations or []) if c.get("id") != iid]
    platform.config.custom_integrations = customs
    _persist(platform, persist)
    return {"removed": iid, "key_removed": key_removed}


def _persist(platform: Any, persist: Any) -> None:
    if callable(persist):
        persist(["custom_integrations"])
        return
    from ..core import config as _config

    cfg = platform.config
    _config.persist_config_values(cfg.home, {"custom_integrations": cfg.custom_integrations})
