"""Local only: a chat whose words never leave the user's own network (v1.332.0).

The user asked for "a local toggle in the chat to indicate nothing hits the
cloud in a specific session without user permission". This module is the ONE
place that answers the two questions the toggle needs:

* **Where does this model run?** :func:`stays_on_this_network` — True only for
  a provider whose inference runs on hardware the user owns AND whose address
  is on the user's own network. ``providers.local.is_local_provider`` names the
  providers that CAN be local; a ``custom`` endpoint pointed at
  ``https://ollama.com`` or OpenRouter is "local-kind" by name and a cloud by
  address, so the address decides. FAIL CLOSED: no address, an address we
  cannot read, any fault at all — the model is treated as cloud.
* **Is this chat local only right now?** :data:`LOCAL_ONLY`, a ContextVar
  holding a small frozen :class:`LocalOnlyScope` ``{on, allowed_once}``, set
  around the whole turn by the chat lanes (and around a session's model calls
  by the agent runtime). ``allowed_once`` names the ONE cloud provider the user
  said yes to for this message. A ContextVar, not a flag on a shared object,
  for the reason ``providers/budget.RUN_BUDGET_USD`` and ``mcp/interact.
  MCP_SCOPE`` are ContextVars: the router and every adapter are SHARED by every
  concurrent turn, so a value set on them would leak into the next caller.
  ``asyncio.to_thread`` and ``asyncio.create_task`` copy the context;
  ``run_in_executor`` does NOT — wrap such a call in
  ``contextvars.copy_context().run``.

:func:`check_egress` is the guard every model-calling path consults; it raises
:class:`LocalOnlyRefused`, a provider error the router never fails over from
(``no_failover``), whose sentence names the provider in words and says what to
do. The dashboard's ask-first card is the friendly front door; THIS is the
lock, and it is the server's.

No DNS lookups here, ever: the predicate runs on every model call, on the
loop, and a name that needs resolving to be judged is judged by its shape
(a single-label name, a private suffix) or not at all.
"""

from __future__ import annotations

import ipaddress
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, AsyncIterator, Iterable, Iterator
from urllib.parse import urlsplit

from .adapters.base import ProviderError
from .local import is_local_provider

#: Host suffixes that name a machine on the user's own network. ``.ts.net`` is
#: Tailscale's MagicDNS (a tailnet is the user's own network, which is how the
#: daily driver reaches its Sparks); ``.home.arpa`` is RFC 8375.
PRIVATE_SUFFIXES: tuple[str, ...] = (".local", ".lan", ".home.arpa", ".internal", ".ts.net")

#: Address blocks that are the user's own network: loopback, RFC 1918,
#: link-local, CGNAT 100.64.0.0/10 (Tailscale hands these out), and the IPv6
#: loopback / unique-local / link-local blocks. Deliberately NOT
#: ``ip.is_private``: that also admits documentation and benchmark ranges no
#: home network uses.
_PRIVATE_NETS = tuple(
    ipaddress.ip_network(n)
    for n in (
        "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
        "169.254.0.0/16", "100.64.0.0/10",
        "::1/128", "fc00::/7", "fe80::/10",
    )
)

#: A host NAME the predicate will judge: dot-separated labels of letters,
#: digits, hyphens and underscores. Anything else is not a name at all.
_HOSTNAME_RE = re.compile(r"^[a-z0-9_](?:[a-z0-9_-]{0,62})(?:\.[a-z0-9_](?:[a-z0-9_-]{0,62}))*$")

#: Tools whose whole job is to reach the internet (or a machine/service off
#: this network). A local-only turn never arms them, whatever picked them —
#: the "+" menu, a skill, the sentence, a saved setup. The web pair is the
#: read-only web tier (``tools/permissions.READ_ONLY_WEB_TOOLS``);
#: ``delegate_remote`` hands the task to another machine's agent; the Pixio
#: tools send prompts and media to a hosted generation service.
INTERNET_TOOLS: frozenset[str] = frozenset({
    "web_search", "web_fetch", "delegate_remote",
    "pixio_generate", "pixio_upload", "pixio_models", "pixio_params", "pixio_status",
})

#: Tools that run a command or a script (v1.332.0). Any of them can open a
#: socket — a ``curl`` in the shell, ``urllib`` in a cell — and the two pane
#: tools type into a Build terminal, which may be running a cloud CLI. Custom
#: command tools (``tools/dynamic.CommandTool``) join them by class.
RUNS_COMMANDS_TOOLS: frozenset[str] = frozenset({
    "shell", "run_code", "repl", "code_run", "pane_send", "pane_spawn",
})

#: Tools that set an address outside this PC which later events are posted to
#: (v1.332.0): an outbound webhook sends every matching event, this chat's
#: included, to its ``target_url``; ``webhook_update`` can change that address
#: or resume a paused one.
SETS_OUTSIDE_ADDRESS_TOOLS: frozenset[str] = frozenset({
    "webhook_add", "webhook_update",
})

#: Tools that send a PROMPT to a hosted media service and spend there
#: (v1.346.0, media in chat). NOT in :data:`INTERNET_TOOLS` on purpose: the
#: user asked for media from the chat, so a local-only turn keeps the tool
#: armed and CARDS every call ("Allow once") instead of silently dropping it
#: — the one-message OK on the card is the user's consent for that prompt to
#: leave this PC. The POST lane, which can ask nobody, does not arm it.
GENERATES_MEDIA_TOOLS: frozenset[str] = frozenset({
    "generate_media",
})

#: The words a person knows each cloud provider by. Anything not here reads as
#: its id (a fleet node reads as the label the user gave it).
_FRIENDLY: dict[str, str] = {
    "claude-cli": "Claude",
    "anthropic": "Claude",
    "codex-cli": "ChatGPT",
    "openai": "OpenAI",
    "google": "Gemini",
    "grok-cli": "Grok",
    "xai": "Grok",
    "openrouter": "OpenRouter",
    "ollama": "Ollama",
    "custom": "your own model server",
    "auto": "Auto",
}


# --------------------------------------------------------------------------- #
# Where does this model run?
# --------------------------------------------------------------------------- #
def private_host(url: str) -> bool:
    """True when *url*'s host is on the user's own network (see the module
    docstring for the list). A bare ``host:port`` is read as ``http://``.
    Never raises; anything unreadable is False."""
    try:
        raw = str(url or "").strip()
        if not raw:
            return False
        if "://" not in raw:
            raw = "http://" + raw
        host = (urlsplit(raw).hostname or "").strip().lower().rstrip(".")
        if not host:
            return False
        host = host.split("%", 1)[0]  # an IPv6 zone id (fe80::1%eth0)
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            ip = None
        if ip is not None:
            mapped = getattr(ip, "ipv4_mapped", None)
            if mapped is not None:
                ip = mapped
            return any(ip in net for net in _PRIVATE_NETS if net.version == ip.version)
        if not _HOSTNAME_RE.match(host):
            return False  # not a name a resolver would take: fail closed
        if host == "localhost" or host.endswith(".localhost"):
            return True
        if "." not in host:
            # A bare NUMBER is an address, not a name: the resolver reads
            # ``134744072`` / ``0x8080808`` as 8.8.8.8 (inet_aton's forms), so
            # it is judged as the address it spells, never waved through as a
            # LAN machine's single-label name (v1.332.0 review).
            if re.fullmatch(r"0x[0-9a-f]*|[0-9]+", host):
                try:
                    num = int(host, 16) if host.startswith("0x") else (
                        int(host, 8) if len(host) > 1 and host.startswith("0") else int(host)
                    )
                    v4 = ipaddress.IPv4Address(num)
                except (ValueError, ipaddress.AddressValueError):
                    return False
                return any(v4 in net for net in _PRIVATE_NETS if net.version == 4)
            return True  # a single-label name (``spark1``) lives on the LAN
        return host.endswith(PRIVATE_SUFFIXES)
    except Exception:  # noqa: BLE001 — fail closed
        return False


def _innermost(adapter: Any) -> Any:
    inner = adapter
    for _ in range(4):
        nxt = getattr(inner, "inner", None)
        if nxt is None:
            break
        inner = nxt
    return inner


def endpoint_url(provider: str, manager: Any = None, config: Any = None) -> str:
    """The base URL *provider*'s adapter talks to, or ``""`` when unknown.

    Read off the adapter the manager builds for it (a fleet node's
    ``base_url``, else the adapter's ``_endpoint``) — what will REALLY be
    called — then the manager's configured ollama/custom URL, then the
    config's. Never raises."""
    p = (provider or "").strip().lower()
    if manager is not None:
        try:
            inner = _innermost(manager.get(p))
            node = getattr(inner, "node", None)
            url = str(getattr(node, "base_url", "") or "") if node is not None else ""
            url = url or str(getattr(inner, "_endpoint", "") or "")
            if url:
                return url
        except Exception:  # noqa: BLE001 — fall through to the configured URL
            pass
        attr = {"ollama": "_ollama_base_url", "custom": "_custom_base_url"}.get(p)
        if attr:
            try:
                url = str(getattr(manager, attr, "") or "")
                if url:
                    return url
            except Exception:  # noqa: BLE001
                pass
    if config is not None:
        key = {"ollama": "ollama_base_url", "custom": "custom_base_url"}.get(p)
        if key:
            try:
                return str(getattr(config, key, "") or "")
            except Exception:  # noqa: BLE001
                return ""
    return ""


def stays_on_this_network(provider: str, manager: Any = None, config: Any = None) -> bool:
    """True when a call to *provider* never leaves the user's own network.

    * a provider ``is_local_provider`` does not name → False (every cloud API
      and every subscription CLI: their inference is someone else's);
    * ``opencode-cli`` → True (this daemon offers it local-only by
      construction — ``config.opencode_local_models``);
    * ``ollama`` / ``custom`` / ``fleet-*`` → True only when the endpoint's
      host passes :func:`private_host`; no URL → False;
    * ``mock`` → True. A deliberate reading, not a loophole: the offline demo
      model runs inside this process and sends nothing anywhere, so refusing
      it would refuse the one model a fresh install has.

    Pure, cheap, never raises."""
    try:
        p = (provider or "").strip().lower()
        if p == "mock":
            return True
        if not is_local_provider(p):
            return False
        if p == "opencode-cli":
            return True
        url = endpoint_url(p, manager, config)
        return bool(url) and private_host(url)
    except Exception:  # noqa: BLE001 — fail closed
        return False


#: The three answers :func:`model_place` gives (v1.351.0, G14).
PLACE_THIS_PC = "this_pc"
PLACE_NETWORK = "network"
PLACE_CLOUD = "cloud"


def _loopback_host(url: str) -> bool:
    """True when *url*'s host is THIS computer (127.0.0.0/8, ::1,
    ``localhost``). Never raises; anything unreadable is False."""
    try:
        raw = str(url or "").strip()
        if not raw:
            return False
        if "://" not in raw:
            raw = "http://" + raw
        host = (urlsplit(raw).hostname or "").strip().lower().rstrip(".")
        if host == "localhost" or host.endswith(".localhost"):
            return True
        try:
            ip = ipaddress.ip_address(host.split("%", 1)[0])
        except ValueError:
            return False
        mapped = getattr(ip, "ipv4_mapped", None)
        return (mapped or ip).is_loopback
    except Exception:  # noqa: BLE001
        return False


def model_place(provider: str, manager: Any = None, config: Any = None) -> str:
    """WHERE the model behind *provider* runs, in one of three words
    (v1.351.0, G14): :data:`PLACE_THIS_PC`, :data:`PLACE_NETWORK` or
    :data:`PLACE_CLOUD`.

    The SAME predicate as :func:`stays_on_this_network` — anything it calls
    cloud is cloud here, so the system prompt, the route object and the
    local-only lock can never disagree. Inside "stays on this network" the
    address splits the two local answers: a loopback endpoint (and the
    offline demo model, which runs inside this process) is THIS PC; a fleet
    node, a LAN box or a tailnet machine is the user's NETWORK, and so is a
    local-by-construction provider whose address we cannot read (the honest
    wider word — never "this PC" on a guess). Pure, cheap, never raises."""
    try:
        if not stays_on_this_network(provider, manager, config):
            return PLACE_CLOUD
        p = (provider or "").strip().lower()
        if p == "mock":
            return PLACE_THIS_PC
        url = endpoint_url(p, manager, config)
        return PLACE_THIS_PC if _loopback_host(url) else PLACE_NETWORK
    except Exception:  # noqa: BLE001 — fail closed: cloud
        return PLACE_CLOUD


def friendly_name(provider: str, manager: Any = None) -> str:
    """The name a person reads for *provider*: the table above, a fleet node's
    own label, else the id. Never raises."""
    p = (provider or "").strip()
    if p.lower() in _FRIENDLY:
        return _FRIENDLY[p.lower()]
    if p.startswith("fleet-") and manager is not None:
        try:
            node = getattr(_innermost(manager.get(p)), "node", None)
            label = " ".join(str(getattr(node, "label", "") or "").split())
            if label:
                return label[:80]
        except Exception:  # noqa: BLE001 — wording only
            pass
    return p or "a cloud model"


# --------------------------------------------------------------------------- #
# Is this chat local only right now?
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class LocalOnlyScope:
    """The local-only posture of the work in flight in THIS context.

    ``on`` — nothing may reach a model off this network without the user's
    OK. ``allowed_once`` — the ONE provider name the user approved for this
    message ("" = none). It is never remembered past the turn that carries it.
    """

    on: bool = False
    allowed_once: str = ""


_OFF = LocalOnlyScope()

LOCAL_ONLY: ContextVar[LocalOnlyScope] = ContextVar("iron_jarvis_local_only", default=_OFF)


def make_scope(on: Any, allowed_once: Any = "") -> LocalOnlyScope:
    """A normalised scope: ``allowed_once`` only means something while ``on``."""
    flag = on is True
    once = " ".join(str(allowed_once or "").split()) if flag else ""
    return LocalOnlyScope(on=flag, allowed_once=once)


@contextmanager
def local_only_scope(on: Any, allowed_once: Any = "") -> Iterator[LocalOnlyScope]:
    """Set :data:`LOCAL_ONLY` for the body of the ``with`` (reset after)."""
    scope = make_scope(on, allowed_once)
    token = LOCAL_ONLY.set(scope)
    try:
        yield scope
    finally:
        try:
            LOCAL_ONLY.reset(token)
        except (ValueError, LookupError):  # a token from another context
            LOCAL_ONLY.set(_OFF)


def current_scope() -> LocalOnlyScope:
    """The scope of the work in flight in THIS context (off by default)."""
    try:
        return LOCAL_ONLY.get()
    except Exception:  # noqa: BLE001 — never raises
        return _OFF


async def scoped_frames(frames: AsyncIterator[Any], scope: LocalOnlyScope) -> AsyncIterator[Any]:
    """*frames* iterated with *scope* set in the iterating task's context.

    For a turn that is a generator (the stream lane): the scope is set when
    the first frame is asked for, so every task the turn creates from then on
    (the model pump, the tool rounds) copies it, and it is reset when the
    stream ends however it ends. A scope that is off sets nothing at all.

    Closing this generator closes *frames* (in both cases): a dropped
    connection must still reach the turn's own ``finally`` blocks at once,
    never at garbage collection."""
    token = LOCAL_ONLY.set(scope) if scope.on else None
    try:
        async for frame in frames:
            yield frame
    finally:
        try:
            close = getattr(frames, "aclose", None)
            if close is not None:
                await close()
        finally:
            if token is not None:
                try:
                    LOCAL_ONLY.reset(token)
                except (ValueError, LookupError):
                    LOCAL_ONLY.set(_OFF)


class LocalOnlyRefused(ProviderError):
    """Nothing was sent: this chat is local only and *provider* runs off the
    user's network. Never transient, never failed over from (``no_failover``,
    the flag ``router.is_no_failover`` reads for Iron-Proxy's account
    sentences). ``code`` is what the dashboard keys its two actions on."""

    code = "local_only"
    no_failover = True

    def __init__(self, provider: str, model: str = "", *, manager: Any = None) -> None:
        self.provider = str(provider or "")
        self.model = str(model or "")
        name = friendly_name(self.provider, manager)
        super().__init__(
            f"This chat is local only, so nothing was sent to {name}. Pick a"
            f" local model, or allow {name} for this one message.",
            transient=False,
        )

    def payload(self) -> dict[str, Any]:
        """The refusal as the routes and the stream's error frame carry it."""
        return {
            "detail": str(self),
            "code": self.code,
            "provider": self.provider,
            "model": self.model,
        }


def refusal_of(exc: BaseException | None) -> "LocalOnlyRefused | None":
    """*exc* (or what it was raised from) when it is a local-only refusal."""
    seen = 0
    while exc is not None and seen < 6:
        if isinstance(exc, LocalOnlyRefused):
            return exc
        exc = exc.__cause__ or exc.__context__
        seen += 1
    return None


def is_allowed_once(scope: LocalOnlyScope, *names: str) -> bool:
    """True when the user's one-message OK covers any of *names*."""
    once = (scope.allowed_once or "").strip().lower()
    return bool(once) and any((n or "").strip().lower() == once for n in names)


def egress_ok(
    provider: str, *, manager: Any = None, config: Any = None,
    also: Iterable[str] = (), scope: "LocalOnlyScope | None" = None,
) -> bool:
    """True when a call to *provider* is allowed under the current scope:
    the scope is off, the provider stays on this network, or the user allowed
    it (or one of the *also* names it was asked for by) for this message."""
    sc = scope if scope is not None else current_scope()
    if not sc.on:
        return True
    if stays_on_this_network(provider, manager, config):
        return True
    return is_allowed_once(sc, provider, *also)


def check_egress(
    provider: str, model: str = "", *, manager: Any = None, config: Any = None,
    also: Iterable[str] = (),
) -> None:
    """Raise :class:`LocalOnlyRefused` when the current scope is on, *provider*
    does not stay on this network, and the user has not allowed it (or one of
    the *also* names) for this message. Silent otherwise."""
    if not egress_ok(provider, manager=manager, config=config, also=also):
        raise LocalOnlyRefused(provider, model, manager=manager)


# --------------------------------------------------------------------------- #
# Tools that reach past this PC: in a local-only turn they always ask
# --------------------------------------------------------------------------- #
def _schema_props(tool: Any) -> dict[str, Any]:
    for attr in ("input_schema", "parameters"):
        schema = getattr(tool, attr, None)
        if isinstance(schema, dict) and isinstance(schema.get("properties"), dict):
            return schema["properties"]
    return {}


def _browser_reaches_out(tool: Any) -> bool:
    """A browser-add-on tool that acts or loads an address. Read off the tool
    itself: ``min_access`` above ``read_only`` (an unrecognised level counts
    as acting, the tools' own strictest reading), or a ``url`` argument."""
    from ..browser.tools import _ACCESS_RANK, _BrowserTool

    if not isinstance(tool, _BrowserTool):
        return False
    rank = _ACCESS_RANK.get(str(getattr(tool, "min_access", "")), _ACCESS_RANK["interactive"])
    return rank > _ACCESS_RANK["read_only"] or "url" in _schema_props(tool)


def reaches_outside(tool: Any, registry: Any = None) -> bool:
    """True when *tool* can carry this chat's words past this PC by itself
    (v1.332.0, L3): a browser tool that acts or loads an address (the add-on's
    family and computer use's isolated browser), a channel send (``notify``),
    a command or script runner, a webhook that posts later events to an
    outside address, or a pack tool (an MCP pack can be a remote service).
    Derived from the tool's class and the registry's own pack list,
    never from a name prefix. Never raises; a fault reads as True for a known
    tool (fail closed) and False for a missing one (nothing to run)."""
    if tool is None:
        return False
    try:
        name = str(getattr(tool, "name", "") or "")
        if (
            name in RUNS_COMMANDS_TOOLS
            or name in SETS_OUTSIDE_ADDRESS_TOOLS
            or name in GENERATES_MEDIA_TOOLS
        ):
            return True
        if _browser_reaches_out(tool):
            return True
        from ..comm.tools import NotifyTool
        from ..computeruse.tools import BrowseTool, WebActionTool
        from ..mcp.tools import MCPRemoteTool
        from ..tools.dynamic import CommandTool

        if isinstance(tool, (NotifyTool, BrowseTool, WebActionTool, MCPRemoteTool, CommandTool)):
            return True
        if registry is not None:
            try:
                if name in set(registry.mcp_names()):
                    return True
            except Exception:  # noqa: BLE001 — a double without the list
                pass
        return False
    except Exception:  # noqa: BLE001 — fail closed
        return True


def ask_first_tools(registry: Any, names: "Iterable[str] | None" = None) -> frozenset[str]:
    """The tools (of *names*, else every registered one) that must ASK in a
    local-only turn (v1.332.0, L3). The ONE list both chat lanes read: the
    stream lane cards each call in every approval mode with "Allow once"
    only, and the POST lane, which can ask nobody, does not arm them."""
    try:
        pool = list(names) if names is not None else list(registry.names())
    except Exception:  # noqa: BLE001
        return frozenset()
    out: set[str] = set()
    for n in pool:
        try:
            tool = registry.get(n)
        except Exception:  # noqa: BLE001
            tool = None
        if reaches_outside(tool, registry):
            out.add(str(n))
    return frozenset(out)


def asks_first(registry: Any, name: str, scope: "LocalOnlyScope | None" = None) -> bool:
    """True when this call must show its card whatever the approval mode:
    the turn is local only and the tool reaches outside this PC. The user's
    one-message OK for a MODEL does not cover a tool."""
    sc = scope if scope is not None else current_scope()
    if not sc.on:
        return False
    try:
        tool = registry.get(name)
    except Exception:  # noqa: BLE001
        return False
    return reaches_outside(tool, registry)


def route_flags(provider: str, manager: Any = None) -> dict[str, bool]:
    """The receipt keys every chat lane puts on its route object:
    ``local_only`` (this turn ran under the toggle), ``allowed_once``
    (it was answered off this network with the user's one-message OK) and,
    since v1.351.0 (G14, additive, LAST), ``on_this_network`` — whether the
    model that ANSWERED runs on the user's own network (this PC included),
    from :func:`stays_on_this_network`, the one predicate the local-only lock
    and the system prompt's "where you run" line also read."""
    sc = current_scope()
    here = stays_on_this_network(provider, manager)
    return {
        "local_only": bool(sc.on),
        "allowed_once": bool(sc.on and sc.allowed_once and not here),
        "on_this_network": bool(here),
    }


# --------------------------------------------------------------------------- #
# Long-term memory bases: which ones are on this PC
# --------------------------------------------------------------------------- #
def ltm_connector_is_local(connector: Any) -> bool:
    """True for a memory base that reads files on this PC (the markdown
    brain, an Obsidian vault). Every other kind — a cloud drive, Notion, an
    HTTP retrieval service, a remote machine over SSH, an MCP brain — is
    treated as remote: fail closed."""
    try:
        from ..ltm.base import MarkdownDirConnector

        return isinstance(connector, MarkdownDirConnector)
    except Exception:  # noqa: BLE001 — fail closed
        return False


def local_ltm(ltm: Any) -> Any:
    """A stand-in for *ltm* holding only its local bases (same search API),
    or *ltm* itself when every base is local. ``None`` stays ``None``."""
    if ltm is None:
        return None
    try:
        conns = list(ltm.connectors())
    except Exception:  # noqa: BLE001 — unreadable → nothing remote leaks
        conns = None
    if conns is not None and all(ltm_connector_is_local(c) for c in conns):
        return ltm
    from ..ltm.manager import LongTermMemory

    out = LongTermMemory()
    for c in conns or []:
        if ltm_connector_is_local(c):
            try:
                out.register(c)
            except Exception:  # noqa: BLE001
                continue
    return out


# --------------------------------------------------------------------------- #
# A saved chat's own setting
# --------------------------------------------------------------------------- #
def setup_says_local_only(setup_json: Any) -> bool:
    """True when a saved thread's ``setup_json`` carries ``local_only: true``."""
    import json

    try:
        data = json.loads(setup_json or "{}")
    except Exception:  # noqa: BLE001
        return False
    return isinstance(data, dict) and data.get("local_only") is True


def thread_local_only(engine: Any, thread_id: Any) -> bool:
    """True when the saved chat *thread_id* is switched to local only.
    Blocking (one row read): call it off the loop. A missing id, a missing
    row or any read failure is False — the caller then goes by what the
    request itself says."""
    tid = str(thread_id or "").strip()[:80]
    if not tid or engine is None:
        return False
    try:
        from sqlmodel import select

        from ..core.db import session_scope
        from ..core.models import ChatThreadRecord as T

        with session_scope(engine) as db:
            row = db.exec(select(T.setup_json).where(T.id == tid)).first()
        return setup_says_local_only(row)
    except Exception:  # noqa: BLE001 — a read failure is "the body decides"
        return False


def may_recall_local_only_chats() -> bool:
    """True only for work that itself stays on this network: the scope is on
    and the user has NOT allowed a model off it for this message (v1.332.0).
    Everything else (a chat that is not local only, an agent run, a turn sent
    with the one-message OK) must not be handed a local-only chat's text by
    recall or a history search."""
    sc = current_scope()
    return bool(sc.on and not sc.allowed_once)


def local_only_thread_ids(engine: Any) -> "frozenset[str] | None":
    """The ids of every saved chat switched to local only (v1.332.0), or
    ``None`` when the store cannot be read (the caller then leaves past chats
    out entirely: fail closed). Blocking: one indexed read, off the loop."""
    if engine is None:
        return None
    try:
        from sqlmodel import select

        from ..core.db import session_scope
        from ..core.models import ChatThreadRecord as T

        with session_scope(engine) as db:
            rows = list(db.exec(
                select(T.id, T.setup_json).where(
                    T.setup_json.contains("local_only")  # type: ignore[union-attr]
                )
            ))
        return frozenset(tid for tid, setup in rows if setup_says_local_only(setup))
    except Exception:  # noqa: BLE001 — unreadable: the caller fails closed
        return None


async def effective_local_only(engine: Any, local_only: Any, thread_id: Any) -> bool:
    """A request's local-only verdict: what the request says OR what the saved
    chat it names says. A stale client cannot switch a saved local-only chat
    off by leaving the flag out. The row read runs off the loop."""
    if local_only is True:
        return True
    tid = str(thread_id or "").strip()
    if not tid:
        return False
    import asyncio

    try:
        return await asyncio.to_thread(thread_local_only, engine, tid)
    except Exception:  # noqa: BLE001 — a read failure is "the request decides"
        return False


__all__ = [
    "INTERNET_TOOLS",
    "LOCAL_ONLY",
    "LocalOnlyRefused",
    "LocalOnlyScope",
    "PRIVATE_SUFFIXES",
    "RUNS_COMMANDS_TOOLS",
    "SETS_OUTSIDE_ADDRESS_TOOLS",
    "GENERATES_MEDIA_TOOLS",
    "ask_first_tools",
    "asks_first",
    "check_egress",
    "current_scope",
    "effective_local_only",
    "egress_ok",
    "endpoint_url",
    "friendly_name",
    "is_allowed_once",
    "local_ltm",
    "local_only_scope",
    "local_only_thread_ids",
    "ltm_connector_is_local",
    "may_recall_local_only_chats",
    "make_scope",
    "model_place",
    "PLACE_CLOUD",
    "PLACE_NETWORK",
    "PLACE_THIS_PC",
    "private_host",
    "reaches_outside",
    "refusal_of",
    "route_flags",
    "scoped_frames",
    "setup_says_local_only",
    "stays_on_this_network",
    "thread_local_only",
]
