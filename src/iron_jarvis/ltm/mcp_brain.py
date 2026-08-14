"""An MCP-served brain as a long-term-memory source (kind ``mcp``).

The user's own memory server — an Obsidian brain behind ``mcp-remote``, a
hosted notes MCP, anything speaking the protocol — plugs into the SAME
LongTermMemory surface as the local brain/Obsidian/Notion connectors.

Tool names vary per server, so the connector DISCOVERS them: the first tool
whose name (then description) matches a search-ish pattern serves
:meth:`search`, an append-ish one serves :meth:`append`, and arguments are
mapped from the tool's OWN input schema (query/q/text…, title/name…,
content/body…). Results normalize to the uniform hit shape
``{title, snippet, ref, source}`` whether the server returns JSON lists or
plain text.

Connection is LAZY: registering the source does nothing over the network, so
boot can never hang on a remote brain and a dead server degrades to an honest
error at query time. The bearer token is resolved from the encrypted vault at
connect time — never stored on the record.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from typing import Any, Callable

from .base import LTMConnector


def _resolve_maybe_async(value: Any) -> Any:
    """The LTM contract is SYNC; the real MCPClient's methods are ASYNC (test
    fakes are sync). Run a coroutine to completion — from a worker thread
    (FastAPI sync routes, the graph builder) a private loop is safe; if a loop
    is already running (agent tool path) hop to a helper thread."""
    if not asyncio.iscoroutine(value):
        return value
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(value)
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(asyncio.run, value).result()

_SEARCH_RX = re.compile(r"search|query|recall|retrieve|find|lookup", re.IGNORECASE)
_APPEND_RX = re.compile(
    r"append|add|create|write|save|store|note|ingest|upsert", re.IGNORECASE
)
_LIST_RX = re.compile(r"\b(list|all|recent|browse|index|enumerate)", re.IGNORECASE)
_QUERY_KEYS = ("query", "q", "text", "search", "keywords", "prompt", "input")
_TITLE_KEYS = ("title", "name", "subject", "summary", "heading", "filename", "path")
_CONTENT_KEYS = ("content", "text", "body", "note", "markdown", "data")
_LIMIT_KEYS = ("k", "limit", "top_k", "max_results", "count")
_RESULT_LIST_KEYS = ("results", "hits", "items", "notes", "documents", "matches")

#: Health probing (v1.173.0). v1.172.0 gave LOCAL bases a ``health()``; a remote
#: brain reported ``available: null`` — and an MCP-served brain is exactly the
#: kind that goes dark (its tools load once, at daemon boot), so the honest
#: unknown was least useful precisely where it mattered most.
#:
#: A page poll must never wait on a dead server, so the probe runs on a
#: THROWAWAY daemon thread with a deadline. Overrunning it reports
#: ``available: None`` — "the check timed out" is not proof the base is broken,
#: and a false red would send the user hunting a bug that isn't there.
_HEALTH_TIMEOUT = 4.0
#: How long a verdict is reused: the Memory page and Connections both poll on a
#: timer, and an uncached probe would open a socket every few seconds per base.
_HEALTH_TTL = 60.0
#: An unknown expires SOONER than a real verdict — it means "couldn't tell yet",
#: and a server that came back should not stay grey for a whole minute.
_HEALTH_UNKNOWN_TTL = 15.0
#: Substrings that make a connection failure a CREDENTIAL problem rather than a
#: reachability one — the two have different fixes, and saying "cannot reach"
#: for a 401 sends the user to restart a server that is running fine.
#:
#: These are WORDS, unambiguous wherever they appear. The status CODES are
#: handled separately (:func:`_is_auth_failure`): a bare "401"/"403" substring
#: matched the endpoint URL too, so a brain on ``http://host:4013/mcp`` that
#: was simply DOWN got diagnosed as "the server refused the credentials" —
#: the same mis-routing this split exists to prevent, in the other direction.
_AUTH_MARKERS = (
    "unauthorized",
    "unauthorised",
    "forbidden",
    "invalid token",
    "authentication",
    "api key",
)
#: Anything that can carry digits which are NOT a status code: the endpoint url
#: and any ``host:port``. Stripped before the status codes are looked for.
_LOCATION_RX = re.compile(r"\w+://\S+|:\d{2,5}\b")
#: A status code as its own number (never a slice of 4013 / 14031).
_AUTH_STATUS_RX = re.compile(r"(?<!\d)(?:401|403)(?!\d)")


def _is_auth_failure(text: str) -> bool:
    """Does this failure text mean "the credentials were refused"?"""
    low = text.lower()
    if any(m in low for m in _AUTH_MARKERS):
        return True
    return bool(_AUTH_STATUS_RX.search(_LOCATION_RX.sub(" ", low)))


def _content_text(res: dict[str, Any] | None) -> str:
    """Flatten an MCP tools/call result's text content blocks."""
    parts: list[str] = []
    for c in (res or {}).get("content") or []:
        if isinstance(c, dict) and c.get("type") == "text":
            parts.append(str(c.get("text", "")))
    return "\n".join(parts)


def _hits_from_text(text: str, source: str, k: int) -> list[dict[str, Any]]:
    """Normalize a server's reply to uniform hits — JSON first, prose fallback."""
    data: Any = None
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        data = None
    items: list[Any] | None = None
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        for key in _RESULT_LIST_KEYS:
            if isinstance(data.get(key), list):
                items = data[key]
                break
    hits: list[dict[str, Any]] = []
    if items is not None:
        for it in items[:k]:
            if isinstance(it, dict):
                title = str(
                    it.get("title") or it.get("name") or it.get("path")
                    or it.get("id") or "note"
                )
                snippet = str(
                    it.get("snippet") or it.get("content") or it.get("text")
                    or it.get("excerpt") or ""
                )[:500]
                ref = str(
                    it.get("ref") or it.get("path") or it.get("id")
                    or it.get("url") or title
                )
            else:
                s = str(it)
                title, snippet, ref = s[:80], s[:500], s[:200]
            hits.append({"title": title, "snippet": snippet, "ref": ref, "source": source})
        return hits
    # Plain text: paragraphs become hits (an honest degrade, never empty-silent
    # when the server DID answer).
    for block in [b.strip() for b in text.split("\n\n") if b.strip()][:k]:
        first = block.splitlines()[0][:80]
        hits.append({"title": first, "snippet": block[:500], "ref": first, "source": source})
    return hits


class McpBrainConnector(LTMConnector):
    """Long-term memory over an MCP server (HTTP/SSE url or stdio command)."""

    def __init__(
        self,
        name: str,
        *,
        url: str = "",
        headers: "dict[str, str] | None" = None,
        token_resolver: "Callable[[], str | None] | None" = None,
        command: str = "",
        args: "list[str] | None" = None,
        env: "dict[str, str] | None" = None,
        client: Any = None,
    ) -> None:
        self.name = name
        self._url = url
        self._headers = dict(headers or {})
        self._token_resolver = token_resolver
        self._command = command
        self._args = list(args or [])
        self._env = dict(env or {})
        self._client = client  # injected in tests; built lazily otherwise
        self._tools: "list[dict[str, Any]] | None" = None
        # Health-probe state (v1.173.0): the cached verdict + its stamp, and a
        # flag so a poll that arrives while a probe is still hanging does not
        # pile a second thread onto the same dead server.
        self._health_lock = threading.Lock()
        self._health: "tuple[float, dict[str, Any]] | None" = None
        self._probing = False
        # The in-flight probe's answer box and its "done" signal. A caller that
        # loses the race WAITS for this instead of inventing an unknown — the
        # Memory page mounts two consumers of /ltm/sources in one commit, and
        # a coin-flip grey card on a healthy base is the opposite of the job.
        self._probe_box: "dict[str, dict[str, Any]]" = {}
        self._probe_done: "threading.Event | None" = None

    # -- lazy connection ----------------------------------------------------
    def _build_client(self) -> Any:
        """The transport/client ONLY — no tools fetch. Split out of
        :meth:`_connect` so :meth:`health` can force a FRESH ``tools/list``:
        ``self._tools`` is cached for the life of the process, so answering the
        probe from it would report green for a server that has since gone
        dark — the precise lie this health check exists to stop."""
        if self._client is None:
            from ..mcp.client import HttpTransport, MCPClient, StdioTransport

            headers = dict(self._headers)
            if self._token_resolver is not None:
                tok = self._token_resolver()
                if tok:
                    headers.setdefault("Authorization", f"Bearer {tok}")
            if self._url:
                transport: Any = HttpTransport(self._url, headers=headers)
            elif self._command:
                transport = StdioTransport(
                    self._command, self._args, env=self._env or None
                )
            else:
                raise RuntimeError(f"{self.name}: no MCP url or command configured")
            self._client = MCPClient(transport, name=self.name)
        return self._client

    def _connect(self) -> Any:
        client = self._build_client()
        if self._tools is None:
            self._tools = _resolve_maybe_async(client.list_tools())
        return client

    def _relist(self, client: Any) -> None:
        """One fresh ``tools/list``, ADOPTED ONLY IF IT HAS TOOLS.

        The cache is repaired, never downgraded: an empty answer from a server
        that is restarting must not blank a known-good list, because
        :meth:`_connect` re-lists only when ``_tools is None`` and every later
        search would then raise "no search-like tool" for the life of the
        process — silent, permanent blindness dressed up as "no such note"."""
        fresh = _resolve_maybe_async(client.list_tools())
        if isinstance(fresh, list) and fresh:
            self._tools = list(fresh)

    def _pick(
        self,
        rx: re.Pattern[str],
        *,
        exclude: "re.Pattern[str] | None" = None,
        tools: "list[dict[str, Any]] | None" = None,
    ) -> "dict[str, Any] | None":
        """First tool whose NAME (then description) matches *rx* — skipping any
        whose name matches *exclude* (the append pick must never grab
        ``search_notes`` just because "note" appears in it).

        *tools* scans a caller-supplied list instead of the cache: the health
        probe judges the list it just fetched WITHOUT publishing it, so a
        transient bad answer stays a verdict and never becomes state."""

        def ok(t: dict[str, Any]) -> bool:
            return exclude is None or not exclude.search(str(t.get("name", "")))

        candidates = self._tools if tools is None else tools
        for t in candidates or []:
            if ok(t) and rx.search(str(t.get("name", ""))):
                return t
        for t in candidates or []:
            if ok(t) and rx.search(str(t.get("description", ""))):
                return t
        return None

    def _pick_or_refresh(
        self, rx: re.Pattern[str], *, exclude: "re.Pattern[str] | None" = None
    ) -> "dict[str, Any] | None":
        """:meth:`_pick`, and when it finds nothing, ONE fresh ``tools/list``
        before giving up. Belt and braces for the cache: a base whose tools
        were empty or partial at boot (an `mcp-remote` bridge still starting)
        self-heals on the next call instead of staying dead until a restart."""
        tool = self._pick(rx, exclude=exclude)
        if tool is not None:
            return tool
        try:
            self._relist(self._build_client())
        except Exception:  # noqa: BLE001 — the caller's own error is the report
            return None
        return self._pick(rx, exclude=exclude)

    @staticmethod
    def _schema_keys(tool: dict[str, Any]) -> list[str]:
        props = (tool.get("inputSchema") or {}).get("properties") or {}
        return list(props.keys())

    @staticmethod
    def _map_arg(keys: list[str], prefs: tuple[str, ...], value: str) -> dict[str, Any]:
        for k in prefs:
            if k in keys:
                return {k: value}
        return {keys[0]: value} if keys else {"query": value}

    # -- availability (v1.173.0) --------------------------------------------
    def _label(self) -> str:
        """What to SHOW as this base's location — the url, or the command line
        that starts the server. Never a secret: the bearer token lives in the
        vault and rides in a header, never in either of these."""
        if self._url:
            return self._url
        if self._command:
            return " ".join([self._command, *self._args]).strip()
        return ""

    def location(self) -> str:
        """The base's location, with NO network work — so a caller that skips
        the probe (a spent budget on ``/ltm/sources``) can still show the row
        the user is most likely to be confused about with something to check
        against reality."""
        return self._label()

    @staticmethod
    def _failure_detail(exc: BaseException) -> str:
        """What went wrong AND where to fix it.

        The surface named here must be the one that actually manages this base:
        MCP-kind LTM bases are added/removed on the MEMORY page's Long-term
        tab (``POST/DELETE /ltm/sources``). The Connections page manages MCP
        TOOL servers — a different registry, where this base does not appear —
        so sending the user there is an instruction that cannot work."""
        text = f"{type(exc).__name__}: {exc}"[:300]
        if _is_auth_failure(text):
            return (
                f"the server refused the credentials ({text}) — re-add this base "
                "on the Memory page with a fresh token"
            )
        return (
            f"cannot connect to the MCP server ({text}) — check it is running "
            "and the endpoint is right, then re-add this base on the Memory "
            "page's Long-term tab"
        )

    def _unknown(self, detail: str) -> dict[str, Any]:
        return {"available": None, "detail": detail, "path": self._label()}

    def _probe(self) -> dict[str, Any]:
        """One real check: connect, re-list the tools, and require a
        SEARCH-LIKE one. Probing what search actually NEEDS (the same
        ``_pick(_SEARCH_RX)``) rather than "a socket opened" is the whole
        point — a server that answers but exposes no search tool is a base
        that silently returns nothing to every recall."""
        path = self._label()
        if self._client is None and not self._url and not self._command:
            return {
                "available": False,
                "path": path,
                "detail": (
                    "no MCP url or command is configured for this base — re-add "
                    "it with the server's endpoint URL (or the command that "
                    "starts it)"
                ),
            }
        try:
            client = self._build_client()
            tools = _resolve_maybe_async(client.list_tools())
        except Exception as exc:  # noqa: BLE001 — every failure is a verdict
            return {"available": False, "path": path, "detail": self._failure_detail(exc)}
        # The verdict is judged from a LOCAL list. A health check is a READER:
        # it must never be able to make the base worse. Publishing this answer
        # unconditionally meant one probe landing on a restarting server (empty
        # or non-list ``tools``) blanked ``_tools`` for the life of the
        # process — and since search re-lists only when the cache is None, every
        # later recall raised "no search-like tool", which `_merge_search`
        # swallows into an empty result. A transient outage became permanent,
        # silent blindness that looks exactly like "no such note".
        fresh = list(tools) if isinstance(tools, list) else []
        if fresh:
            # Adopt only a real list, and in ONE rebinding, so a concurrent
            # search sees the old list or the new one, never a half-built one.
            # Refreshing is a bonus: a server that gained tools since boot
            # starts working without a restart.
            self._tools = fresh
        tool = self._pick(_SEARCH_RX, tools=fresh)
        if tool is None:
            names = ", ".join(str(t.get("name", "?")) for t in fresh[:5]) or "none"
            return {
                "available": False,
                "path": path,
                "detail": (
                    f"connects, but exposes no search-like tool (tools: {names}) — "
                    "memory recall cannot reach this base; check it is the right "
                    "server, then re-add this base on the Memory page's "
                    "Long-term tab"
                ),
            }
        return {
            "available": True,
            "detail": "",
            "path": path,
            "tool": str(tool.get("name") or ""),
        }

    def _remember(self, verdict: dict[str, Any]) -> None:
        with self._health_lock:
            self._health = (time.monotonic(), dict(verdict))

    def cached_health(self) -> "dict[str, Any] | None":
        """The last verdict while it is still fresh, or None — lets a caller
        (the /ltm/sources listing) reuse a known answer without paying for a
        network probe, and skip probing entirely when its own budget is spent."""
        with self._health_lock:
            entry = self._health
        if entry is None:
            return None
        stamp, verdict = entry
        ttl = _HEALTH_UNKNOWN_TTL if verdict.get("available") is None else _HEALTH_TTL
        if (time.monotonic() - stamp) > ttl:
            return None
        return dict(verdict)

    def invalidate_health(self) -> None:
        """Drop the cached verdict — an explicit "re-check now" from the UI."""
        with self._health_lock:
            self._health = None

    def health(
        self, *, timeout: "float | None" = None, refresh: bool = False
    ) -> dict[str, Any]:
        """``{available, detail, path}`` in the v1.172.0 shape (plus ``tool``
        when it is available), CACHED and BOUNDED.

        ``available`` is True only when the server answers AND offers a
        search-like tool; False with a detail naming the fix when it does not;
        and None — never False — when the probe outran its deadline.

        Blocking work happens on a throwaway daemon thread that is ABANDONED on
        timeout: a hung MCP handshake has no cancel, and joining it would move
        the hang into whatever called us. Callers on the event loop must still
        hop to a thread (``asyncio.to_thread``); FastAPI's sync routes already
        run in the threadpool.

        Concurrent callers SHARE one probe: the second one waits for the first
        one's answer instead of inventing an unknown. Two consumers of
        ``/ltm/sources`` mount together on /memory, so "an earlier check is
        running" used to be a coin flip that painted a healthy base grey — and
        the card loads once, so the grey stuck.
        """
        if not refresh:
            cached = self.cached_health()
            if cached is not None:
                return cached
        budget = _HEALTH_TIMEOUT if timeout is None else max(0.05, float(timeout))
        with self._health_lock:
            if self._probing:
                # An earlier probe on this same server is still running. Ride
                # ALONG with it rather than piling on a second thread — and
                # rather than answering "unknown" while an answer is seconds
                # away. Note the ordering below: a refresh has NOT dropped the
                # cache at this point, so pressing Re-check during a hang can
                # never turn a known-good verdict grey.
                waiter, box = self._probe_done, self._probe_box
                joined = True
            else:
                if refresh:
                    # Only now, owning the probe, is the old verdict dropped
                    # (inlined: invalidate_health() takes this same lock).
                    self._health = None
                self._probing = True
                waiter = self._probe_done = threading.Event()
                box = self._probe_box = {}
                joined = False

        if joined:
            verdict = None
            if waiter is not None and waiter.wait(budget):
                verdict = box.get("verdict")
            if verdict is not None:
                # A real, fresh answer: cache it here too. The caller that
                # STARTED it may already have given up and stored an unknown.
                self._remember(verdict)
                return dict(verdict)
            # Still no answer. A cached verdict — the one a Re-check was asking
            # about — is more honest than a bare unknown.
            cached = self.cached_health()
            if cached is not None:
                return cached
            return self._unknown(
                "a check of this base is still running (the server has not "
                "answered yet) — its availability is unknown for now"
            )

        def run() -> None:
            try:
                box["verdict"] = self._probe()
            finally:
                with self._health_lock:
                    self._probing = False
                if waiter is not None:
                    waiter.set()  # after the box is filled, never before

        worker = threading.Thread(
            target=run, name=f"ltm-health-{self.name}", daemon=True
        )
        worker.start()
        worker.join(budget)
        verdict = box.get("verdict")
        if verdict is None:
            # Do NOT report False here: an unanswered check is not a broken base.
            verdict = self._unknown(
                f"could not check in time (waited {budget:g}s) — the server may "
                "be slow or unreachable, so its availability is unknown"
            )
        self._remember(verdict)
        return verdict

    # -- the LTMConnector contract ------------------------------------------
    def search(self, query: str, k: int = 5) -> list[dict[str, Any]]:
        client = self._connect()
        tool = self._pick_or_refresh(_SEARCH_RX)
        if tool is None:
            raise RuntimeError(
                f"{self.name}: the MCP server exposes no search-like tool"
            )
        keys = self._schema_keys(tool)
        args = self._map_arg(keys, _QUERY_KEYS, query)
        for lk in _LIMIT_KEYS:
            if lk in keys:
                args[lk] = k
                break
        res = _resolve_maybe_async(client.call_tool(str(tool.get("name")), args))
        text = _content_text(res)
        if (res or {}).get("isError"):
            raise RuntimeError(f"{self.name}: {text[:300] or 'search failed'}")
        return _hits_from_text(text, self.name, k)

    def list_items(self, limit: int = 60) -> list[dict[str, Any]]:
        """OPTIONAL enumeration for the Memory list/graph views: when the
        server exposes a list-ish tool (list_notes / get_recent / browse…),
        return up to *limit* items in the uniform hit shape. Raises when the
        server has no such tool — the graph then honestly omits this source
        (exactly like Notion/RAG endpoints), rather than showing a fake
        sample. Search/append are unaffected either way."""
        client = self._connect()
        tool = self._pick_or_refresh(_LIST_RX, exclude=_SEARCH_RX)
        if tool is None:
            raise RuntimeError(
                f"{self.name}: the MCP server exposes no list/browse-style tool"
            )
        keys = self._schema_keys(tool)
        args: dict[str, Any] = {}
        for lk in _LIMIT_KEYS:
            if lk in keys:
                args[lk] = limit
                break
        res = _resolve_maybe_async(client.call_tool(str(tool.get("name")), args))
        text = _content_text(res)
        if (res or {}).get("isError"):
            raise RuntimeError(f"{self.name}: {text[:300] or 'list failed'}")
        return _hits_from_text(text, self.name, limit)

    def append(self, title: str, content: str) -> str:
        client = self._connect()
        tool = self._pick_or_refresh(_APPEND_RX, exclude=_SEARCH_RX)
        if tool is None:
            raise RuntimeError(
                f"{self.name}: read-only — the MCP server exposes no "
                "append/create-style tool"
            )
        keys = self._schema_keys(tool)
        args = self._map_arg(keys, _TITLE_KEYS, title)
        content_key = next(
            (c for c in _CONTENT_KEYS if c in keys and c not in args), None
        )
        if content_key is not None:
            args[content_key] = content
        elif len(keys) >= 2:
            spare = next((k2 for k2 in keys if k2 not in args), None)
            if spare:
                args[spare] = content
        else:
            # Single-argument tool: fold title + content into one payload.
            only = next(iter(args))
            args[only] = f"{title}\n\n{content}"
        res = _resolve_maybe_async(client.call_tool(str(tool.get("name")), args))
        text = _content_text(res)
        if (res or {}).get("isError"):
            raise RuntimeError(f"{self.name}: {text[:300] or 'append failed'}")
        return text.strip()[:200] or title
