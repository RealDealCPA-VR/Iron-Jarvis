"""The media provider contract (v1.346.0, contract item 1).

A :class:`MediaProvider` answers five questions about one service: what it
can make (``kinds``), whether it is connected (``available`` — a key is
present; NO network), what a generation will cost (``quote`` → :class:`Quote`),
how to start one (``start`` → :class:`JobHandle`, idempotent by the key the
caller mints) and how it is going (``poll`` → :class:`JobState`); ``fetch``
turns a finished state into bytes. Every network call runs OFF the event loop
(``asyncio.to_thread``) and is bounded by :data:`NETWORK_TIMEOUT_S`.

:class:`Quote` carries the money words. ``free`` is the provider's own verdict
(an allowance applied, a plan-covered model) — never "the price is zero and
we hope"; a ``provisional`` quote is never free. :meth:`Quote.words` is the
one sentence the approval card shows; :meth:`Quote.cost_note` the short form
the tool output and the bell carry.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Callable

#: The kinds a provider may offer. ``music`` is separate from ``audio`` (a
#: song with lyrics vs. a sound effect / voice) because the services price
#: and model them apart.
KINDS: tuple[str, ...] = ("image", "video", "audio", "music")

#: Bound on every metadata round-trip (models, pricing, estimate, start,
#: poll, a CDN download). A provider that runs a SYNCHRONOUS generation
#: (OpenAI Images) names its own longer bound for that one call.
NETWORK_TIMEOUT_S = 20.0

#: Models lists are cached this long per provider (contract: at most 10 min).
MODELS_CACHE_S = 600.0

#: (method, url, headers, json_body) -> response-ish with ``.status_code`` /
#: ``.content`` / ``.json()`` / optional ``.headers``. Injected by tests.
HttpRequest = Callable[[str, str, dict[str, str], "dict[str, Any] | None"], Any]


def default_http(
    method: str, url: str, headers: dict[str, str], json_body: dict[str, Any] | None
) -> Any:
    """Production transport — httpx imported lazily, bounded, follows redirects.

    One bound for every call this package makes; media downloads can be tens
    of MB so the READ bound is generous while connect stays short."""
    import httpx

    timeout = httpx.Timeout(NETWORK_TIMEOUT_S, connect=10.0, read=120.0)
    return httpx.request(
        method, url, headers=headers, json=json_body, timeout=timeout, follow_redirects=True
    )


def fetch_public(url: str, http: HttpRequest | None = None) -> bytes:
    """Download a PUBLIC url with NO headers — a CDN host must never see the
    service key. SYNC (callers run it off the loop). Raises :class:`MediaError`
    on a non-2xx answer; never fabricates bytes."""
    if not url:
        raise MediaError("provider_error", "the finished generation has no output url")
    resp = (http or default_http)("GET", url, {}, None)
    status = int(getattr(resp, "status_code", 0) or 0)
    if not 200 <= status < 300:
        raise MediaError("provider_error", f"the output download failed ({status})")
    return bytes(getattr(resp, "content", b"") or b"")


class MediaError(Exception):
    """A plain-words failure the chat can show as it is.

    ``code``: ``not_configured`` (no key) | ``insufficient_credits`` (402;
    ``shortfall`` set when known) | ``content_policy`` (422, ``final`` — a
    retry with the same input cannot pass; ``input_hint`` when the service
    said what to change) | ``concurrency_limit`` (429, ``retry_after_s``) |
    ``unknown_model`` | ``unknown_provider`` | ``provider_error``.
    The message NEVER carries a key: providers build it from status codes and
    the service's own detail text, which is masked at the edges that log it.
    """

    def __init__(
        self,
        code: str,
        words: str,
        *,
        retry_after_s: int | None = None,
        shortfall: float | None = None,
        input_hint: str = "",
        final: bool = False,
        status: int = 0,
    ) -> None:
        super().__init__(words)
        self.code = code
        self.words = words
        self.retry_after_s = retry_after_s
        self.shortfall = shortfall
        self.input_hint = input_hint
        self.final = final
        self.status = status

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.words


def _amount(cost: float, currency: str) -> str:
    if currency == "usd":
        return f"${cost:.2f}" if cost >= 0.1 else f"${cost:.3f}".rstrip("0").rstrip(".")
    if float(cost).is_integer():
        return f"{int(cost)} credits" if int(cost) != 1 else "1 credit"
    return f"{cost:g} credits"


@dataclass
class Quote:
    """What one generation will cost, in the provider's own words.

    ``expected_cost`` ``None`` = the service could not say (the card then
    reads "price not known until it runs" and asks). ``free`` is set ONLY
    when the provider verified it (see the module docstring); ``free_label``
    is the reason in words ("Maker: Avatars, 4 left today"). ``provisional``
    = the service's own "provisional" estimate status — a number, not a
    promise, so it is never free. ``note`` is the parenthetical: "your
    price" (a measured Pixio quote), "estimate" (provisional), "list price"
    (a static table). ``raw`` is the service's answer for the ledger.
    """

    expected_cost: float | None
    currency: str = "credits"
    free: bool = False
    free_label: str = ""
    provisional: bool = False
    note: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    def cost_note(self) -> str:
        """The short form: ``free (Maker: 4 left today)`` / ``12 credits (your
        price)`` / ``about 12 credits (estimate)`` / ``price not known until it
        runs``."""
        if self.free:
            return f"free ({self.free_label})" if self.free_label else "free"
        if self.expected_cost is None:
            return "price not known until it runs"
        amount = _amount(self.expected_cost, self.currency)
        if self.provisional:
            amount = f"about {amount}"
        return f"{amount} ({self.note})" if self.note else amount

    def words(self, kind: str, provider_label: str) -> str:
        """The card sentence: ``Generate 1 image on Pixio for 12 credits (your
        price)`` / ``… on Pixio; price not known until it runs`` / ``… on
        Pixio: free (Maker: 4 left today)``."""
        head = f"Generate 1 {kind} on {provider_label}"
        if self.free:
            return f"{head}: {self.cost_note()}"
        if self.expected_cost is None:
            return f"{head}; price not known until it runs"
        return f"{head} for {self.cost_note()}"


@dataclass
class JobHandle:
    """A started generation. ``eta_s`` is the provider's guess at how long it
    takes (``None`` = no idea): the tool waits inline when it is short."""

    job_id: str
    eta_s: int | None = None


@dataclass
class JobState:
    """One poll's answer. ``status``: pending | running | succeeded | failed.
    A finished job carries ``output_url`` (a public, expiring CDN link) OR
    ``payload`` (bytes the provider already holds — OpenAI's b64 answer, the
    fake's generated file); ``ext`` is the provider's say on the file
    extension (``.png``), "" when it does not know (then the url decides).
    ``cost_note`` is what was actually charged, in words, when known."""

    status: str
    output_url: str = ""
    payload: bytes | None = None
    error: str = ""
    cost_note: str = ""
    ext: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def done(self) -> bool:
        return self.status in ("succeeded", "failed")


class MediaProvider(abc.ABC):
    """One media service. Subclasses set ``id`` and ``label`` and implement
    the async methods; every network call inside them runs off the loop and
    is bounded (see the module docstring)."""

    id: str = ""
    label: str = ""

    @abc.abstractmethod
    def kinds(self) -> set[str]:
        """Which of :data:`KINDS` this service can make."""

    @abc.abstractmethod
    def available(self) -> bool:
        """A key is present. NO network — this is read on every chat turn."""

    @abc.abstractmethod
    async def default_model(self, kind: str) -> str:
        """The model to use for ``kind`` when the caller named none."""

    @abc.abstractmethod
    async def models(self, kind: str) -> list[dict[str, Any]]:
        """Models of ``kind`` (``{"id", "name", ...}``), cached ≤ 10 min."""

    @abc.abstractmethod
    async def quote(self, model: str, params: dict[str, Any], kind: str) -> Quote:
        """What this generation will cost. Raises :class:`MediaError`."""

    @abc.abstractmethod
    async def start(
        self, model: str, params: dict[str, Any], kind: str, idem_key: str
    ) -> JobHandle:
        """Start the generation (idempotent by ``idem_key`` where the service
        supports it). Raises :class:`MediaError` with plain words."""

    @abc.abstractmethod
    async def poll(self, job_id: str) -> JobState:
        """The job's current state. Never raises for a job the service does
        not know — that is a failed state with words."""

    async def prepare(
        self, model: str, params: dict[str, Any], kind: str, *, has_refs: bool = False
    ) -> tuple[str, dict[str, Any]]:
        """Check a (model, params) pair BEFORE anything is quoted or started
        (v1.347.0): normalise the model id, fill a prompt the model calls by
        another name, and refuse — in words that name models that WOULD work
        — a model that cannot make ``kind`` from a prompt. Default: unchanged.
        Raises :class:`MediaError`."""
        return model, params

    async def fetch(self, state: JobState) -> bytes:
        """The finished bytes. Default: the payload the provider holds, else a
        public download of ``output_url`` with NO headers (never the key)."""
        if state.payload is not None:
            return state.payload
        return await _to_thread(fetch_public, state.output_url, self._public_http())

    def _public_http(self) -> HttpRequest | None:
        """The transport used for public downloads (tests inject one)."""
        return None

    @property
    def has_upload(self) -> bool:
        """Whether :meth:`upload` is a real door (reference files)."""
        return False

    async def upload(self, path: Any, *, mime: str = "") -> str:
        """Publish a local file and return a url a generation param can use.
        Only when :attr:`has_upload`."""
        raise MediaError("provider_error", f"{self.label} cannot take reference files")

    def ext_for(self, kind: str) -> str:
        """A sensible default extension per kind when nothing else says."""
        return {"image": ".png", "video": ".mp4", "audio": ".mp3", "music": ".mp3"}.get(
            kind, ".bin"
        )


async def _to_thread(fn: Callable[..., Any], *args: Any) -> Any:
    import asyncio

    return await asyncio.to_thread(fn, *args)
