"""Generic offsite RAG endpoint connector (§21 extension).

Lets a user point Iron Jarvis at *their own* external RAG / semantic-search
service over plain HTTP. The remote service owns embeddings + ranking; this
connector is a thin, DECLARATIVE adapter that delegates the query and normalises
whatever JSON comes back into the uniform LTM hit shape
``{"title", "snippet", "ref", "source"}`` — so it auto-joins ``ltm_search`` and
the fused ``recall`` tool like every other connector.

Design notes:

* The HTTP client is INJECTED (an ``httpx`` client instance, *or* a zero-arg
  factory that builds one) so tests run fully offline. Both a sync
  (``httpx.Client``) and an async (``httpx.AsyncClient``) client work — an
  awaitable result from ``.request()`` is driven to completion so the connector
  still satisfies the *synchronous* :class:`LTMConnector` contract that the LTM
  manager calls.
* Auth is resolved lazily via ``token_resolver`` (token lives in the encrypted
  vault, never on the connector). The endpoint may be unauthenticated — a
  missing/absent token simply omits the auth header.
* Request/response shaping is a declarative :class:`HttpRagConfig` dataclass with
  sensible defaults, so arbitrary services fit with no code change and the
  integration layer can build one straight from a persisted source record's JSON
  fields. Common response shapes (``results`` / ``documents`` / ``data`` /
  Pinecone ``matches`` / a bare top-level array) are handled out of the box with
  tolerant field fallbacks.
* NEVER raises on a bad response: a non-200, a timeout, malformed JSON or an
  unexpected shape all degrade to ``[]`` with a logged warning. Most offsite RAG
  endpoints are query-only, so ``append`` is read-only unless an ``ingest_url``
  is configured.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass, field, fields
from typing import Any, Callable

from .base import LTMConnector

log = logging.getLogger(__name__)

# Common keys to try when the config doesn't pin an exact path/field. Ordered by
# how canonical they are; the configured value is always tried first.
_RESULT_KEYS: tuple[str, ...] = (
    "results",
    "documents",
    "data",
    "matches",
    "hits",
    "items",
    "chunks",
)
_TITLE_KEYS: tuple[str, ...] = ("title", "name", "heading", "label")
_TEXT_KEYS: tuple[str, ...] = (
    "text",
    "snippet",
    "content",
    "chunk",
    "body",
    "passage",
    "page_content",
)
_REF_KEYS: tuple[str, ...] = ("url", "ref", "link", "uri", "source", "id", "_id")


@dataclass
class HttpRagConfig:
    """Declarative request/response mapping for an offsite RAG endpoint.

    Every field has a default, so ``HttpRagConfig()`` targets the most common
    shape (``POST {"query": ..., "k": ...}`` -> ``{"results": [{"text","title",
    "url"}]}``). All fields are plain JSON-friendly scalars/dicts so a caller can
    reconstruct one from a persisted source record via :meth:`from_dict`.
    """

    # -- request --------------------------------------------------------------
    method: str = "POST"  # "POST" | "GET"
    query_field: str = "query"  # JSON body / query-param name carrying the query
    top_k_field: str = "k"  # name carrying the result count; "" to omit it
    extra_headers: dict[str, str] = field(default_factory=dict)
    auth_scheme: str = "bearer"  # "bearer" | "header" | "none"
    auth_header: str = "Authorization"  # header name (custom for "header" scheme)
    timeout: float = 20.0  # per-request timeout, seconds

    # -- response mapping (the "field_map") -----------------------------------
    results_path: str = ""  # dotted path to the array; "" => try common keys
    title_field: str = "title"
    text_field: str = "text"  # source of the hit snippet
    ref_field: str = "url"
    score_field: str = "score"  # read for completeness; remote order is authoritative

    # -- optional write path --------------------------------------------------
    ingest_url: str = ""  # POST {title, content} here; "" => source is read-only

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "HttpRagConfig":
        """Build a config from a (possibly partial/dirty) persisted JSON dict.

        Unknown keys are ignored so old/new records both load cleanly.
        """
        data = data or {}
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known and v is not None})


def _coerce_str(value: Any) -> str:
    """Best-effort scalar -> str (join lists, stringify the rest)."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return " ".join(_coerce_str(v) for v in value if v not in (None, ""))
    return str(value)


def _dig(obj: Any, dotted: str) -> Any:
    """Walk ``obj`` following a dotted path (``a.b.c``); None on any miss."""
    cur = obj
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


class HttpRagConnector(LTMConnector):
    """Delegate LTM search to an arbitrary external HTTP RAG endpoint."""

    name = "http_rag"

    def __init__(
        self,
        name: str,
        endpoint_url: str,
        http: Any,
        token_resolver: Callable[[], str | None] | None = None,
        config: HttpRagConfig | None = None,
    ) -> None:
        self.name = name or "http_rag"
        self.endpoint_url = endpoint_url
        # ``http`` is either a client instance (``.request``/``.post``/``.get``)
        # or a zero-arg factory producing one. Instances are caller-owned;
        # factory-built clients are closed after each use.
        self.http = http
        self.token_resolver = token_resolver or (lambda: None)
        self.config = config or HttpRagConfig()

    # -- LTMConnector ---------------------------------------------------------
    def search(self, query: str, k: int = 5) -> list[dict[str, Any]]:
        method = (self.config.method or "POST").upper()
        payload: dict[str, Any] = {self.config.query_field: query}
        if self.config.top_k_field:
            payload[self.config.top_k_field] = k
        try:
            if method == "GET":
                resp = self._request(method, self.endpoint_url, params=payload)
            else:
                resp = self._request(method, self.endpoint_url, json=payload)
        except Exception as exc:  # noqa: BLE001 — network/timeout never crashes recall
            log.warning("http_rag '%s' request failed: %s", self.name, exc)
            return []
        data = self._read_json(resp)
        if data is None:
            return []
        try:
            items = self._extract_results(data)
            hits = [self._to_hit(item) for item in items]
        except Exception as exc:  # noqa: BLE001 — a weird shape yields no hits, no crash
            log.warning("http_rag '%s' response shape not understood: %s", self.name, exc)
            return []
        return hits[:k]

    def append(self, title: str, content: str) -> str:
        if not self.config.ingest_url:
            raise RuntimeError(
                f"LTM source '{self.name}' is a read-only offsite RAG endpoint; "
                "no ingest_url is configured, so it cannot store notes."
            )
        try:
            resp = self._request(
                "POST", self.config.ingest_url, json={"title": title, "content": content}
            )
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"LTM source '{self.name}' ingest request failed: {exc}"
            ) from exc
        status = int(getattr(resp, "status_code", 200) or 200)
        if status >= 400:
            raise RuntimeError(
                f"LTM source '{self.name}' ingest returned HTTP {status}"
            )
        data = self._read_json(resp) or {}
        if isinstance(data, dict):
            for key in ("ref", "id", "url", "_id"):
                if data.get(key):
                    return _coerce_str(data[key])
        return self.config.ingest_url

    # -- HTTP plumbing --------------------------------------------------------
    def _request(
        self,
        method: str,
        url: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        headers = self._headers()
        client, created = self._client()
        try:
            raw = self._call(client, method.upper(), url, json=json, params=params, headers=headers)
            return self._drive(raw)
        finally:
            if created:
                self._close(client)

    def _client(self) -> tuple[Any, bool]:
        """Resolve ``self.http`` to ``(client, created)``.

        A client instance is not callable; a factory is. Factory-built clients
        are ours to close (``created=True``); injected instances are not.
        """
        if callable(self.http):
            return self.http(), True
        return self.http, False

    def _call(
        self,
        client: Any,
        method: str,
        url: str,
        *,
        json: dict[str, Any] | None,
        params: dict[str, Any] | None,
        headers: dict[str, str],
    ) -> Any:
        request = getattr(client, "request", None)
        if callable(request):
            return request(
                method,
                url,
                json=json,
                params=params,
                headers=headers,
                timeout=self.config.timeout,
            )
        # Minimal fake clients may only expose post/get (Notion-style).
        if method == "GET":
            return client.get(url, params=params, headers=headers)
        return client.post(url, json=json, headers=headers)

    @staticmethod
    def _drive(value: Any) -> Any:
        """Run an awaitable to completion so a sync ``search`` can use an async client."""
        if not inspect.isawaitable(value):
            return value
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(value)
        # Already inside a running loop: drive the coroutine on a private loop in
        # a worker thread (a coroutine isn't bound to a loop until it's run).
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, value).result()

    def _close(self, client: Any) -> None:
        for attr in ("aclose", "close"):
            fn = getattr(client, attr, None)
            if callable(fn):
                try:
                    self._drive(fn())
                except Exception:  # noqa: BLE001 — teardown must never surface
                    pass
                return

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/json"}
        if self.config.extra_headers:
            headers.update(self.config.extra_headers)
        scheme = (self.config.auth_scheme or "none").lower()
        if scheme != "none":
            token = self.token_resolver()
            if token:
                name = self.config.auth_header or "Authorization"
                headers[name] = f"Bearer {token}" if scheme == "bearer" else token
        return headers

    @staticmethod
    def _read_json(resp: Any) -> Any:
        """Parse a response body, tolerating non-200 and malformed JSON -> None."""
        if resp is None:
            return None
        status = int(getattr(resp, "status_code", 200) or 200)
        if status >= 400:
            log.warning("http_rag endpoint returned HTTP %s", status)
            return None
        try:
            return resp.json()
        except Exception as exc:  # noqa: BLE001 — malformed body degrades to no hits
            log.warning("http_rag response was not valid JSON: %s", exc)
            return None

    # -- response normalisation ----------------------------------------------
    def _extract_results(self, data: Any) -> list[Any]:
        # 1) explicit configured path wins when it lands on a list
        if self.config.results_path:
            found = _dig(data, self.config.results_path)
            if isinstance(found, list):
                return found
        # 2) a bare top-level array
        if isinstance(data, list):
            return data
        # 3) the common wrapper keys
        if isinstance(data, dict):
            for key in _RESULT_KEYS:
                value = data.get(key)
                if isinstance(value, list):
                    return value
        return []

    def _to_hit(self, item: Any) -> dict[str, Any]:
        if not isinstance(item, dict):
            text = _coerce_str(item)
            return {
                "title": (text[:60].strip() or "result"),
                "snippet": text,
                "ref": "",
                "source": self.name,
            }
        title = self._field(item, self.config.title_field, _TITLE_KEYS)
        snippet = self._field(item, self.config.text_field, _TEXT_KEYS)
        ref = self._field(item, self.config.ref_field, _REF_KEYS)
        if not title:
            title = snippet[:60].strip() or ref or "result"
        return {"title": title, "snippet": snippet, "ref": ref, "source": self.name}

    @staticmethod
    def _field(item: dict[str, Any], primary: str, candidates: tuple[str, ...]) -> str:
        """First non-empty of the configured field then common candidates.

        Also digs into a nested ``metadata`` dict (Pinecone-style items keep the
        text/title there).
        """
        keys: list[str] = []
        if primary:
            keys.append(primary)
        keys.extend(c for c in candidates if c != primary)
        for key in keys:
            if item.get(key) not in (None, ""):
                return _coerce_str(item[key])
        meta = item.get("metadata")
        if isinstance(meta, dict):
            for key in keys:
                if meta.get(key) not in (None, ""):
                    return _coerce_str(meta[key])
        return ""
