"""``generate_media`` — ONE tool that makes an image, video, audio clip or
song on whichever media service the user chose (v1.346.0, contract item 3).

THE MONEY RULE, in the tool's own :meth:`GenerateMediaTool.spend_check`: the
provider is chosen (``platform.media``, the registry — an explicit name, the
``media_provider`` setting, or the ONLY connected service), the model is
resolved, and the service is asked for a QUOTE. A quote the provider itself
calls FREE (Pixio's Maker allowance, a plan-covered model) runs without an
approval card unless ``media_ask_when_free`` is on; a paid or unknown price
ASKS before every spend, with the quote's words on the card. The quote is
cached per (provider, model, params) for :data:`QUOTE_CACHE_S` so
``execute`` does not pay a second estimate.

THE WAIT: ``execute`` starts the generation with an ``Idempotency-Key``
(``ij-<call id>``). When the provider's eta is at most
``media_inline_wait_s`` (an image, OpenAI's synchronous answer) the tool polls
inline, bounded by that wait, delivers the bytes (workspace file + Creative
gallery) and answers with the absolute path — the chat lanes then put the
picture in the reply. Longer work (a video) is handed to
``platform.media_jobs`` (:mod:`iron_jarvis.media.jobs`): the job finishes in
the background, comes home to the chat it was asked in (``ctx.thread_id``)
and rings the bell. A start the service refuses (402 shortfall, 422 final,
429 wait N s) is answered in the provider's plain words.

ASK tier (``core/config.default_permissions``), IRREVERSIBLE (money), denied
under low trust (``core/trust.LOW_TRUST_DENY``), carded in a local-only chat
(``providers/egress.reaches_outside``). Secrets never reach the output: the
provider adds the key at its transport and every error is its own words.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable

from ..core.ids import new_id
from ..core.logging import get_logger
from ..media.base import KINDS, JobHandle, JobState, MediaError, Quote
from ..media.deliver import deliver
from ..media.jobs import MediaJobSpec
from .base import Reversibility, RiskClass, SpendCheck, Tool, ToolContext, ToolResult

log = get_logger(__name__)

#: A quote is reused for this long, so the card's estimate and the start
#: that follows the user's Allow are the SAME estimate (one call paid).
QUOTE_CACHE_S = 60.0
#: The inline poll's pace: starts short, never past the cap.
INLINE_POLL_MIN_S = 1.0
INLINE_POLL_MAX_S = 5.0
#: What the output says when the service could not quote before the start.
PRICE_UNKNOWN = "price not known until it runs"

TOOL_NAME = "generate_media"


def _hash_params(provider: str, model: str, params: dict[str, Any], refs: list[str]) -> str:
    blob = json.dumps(
        {"p": provider, "m": model, "params": params, "refs": refs},
        sort_keys=True, default=str,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _clean_refs(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(p).strip() for p in raw if str(p or "").strip()][:8]


class GenerateMediaTool(Tool):
    name = TOOL_NAME
    description = (
        "Generate an image, video, audio clip or song on the user's chosen media "
        "service (Pixio, OpenAI Images, ...). The service's price is checked first: "
        "a paid generation asks the user before it runs; a free one (a daily "
        "allowance) runs at once. Images come back inline; longer work (video, "
        "music) finishes in the background and appears in this chat when ready. "
        "Give a clear, complete prompt; name a model only when the user did."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "kind": {
                "type": "string",
                "enum": list(KINDS),
                "description": "What to make: image, video, audio (a sound/voice) or music (a song).",
            },
            "prompt": {"type": "string", "description": "What the media should show or sound like."},
            "provider": {
                "type": "string",
                "description": "A media service id (pixio, openai_images) — only when the user named one.",
            },
            "model": {
                "type": "string",
                "description": "The service's model id — only when the user named one.",
            },
            "params": {
                "type": "object",
                "description": "Extra model parameters passed through to the service (size, duration, ...).",
            },
            "reference_paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Local files to upload as references, when the service takes them.",
            },
        },
        "required": ["kind", "prompt"],
    }
    reversibility = Reversibility.IRREVERSIBLE
    risk_class = RiskClass.EXTERNAL_COMMIT

    def __init__(
        self,
        platform: Any,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        self._platform = platform
        self._clock = clock
        self._sleep = sleep
        #: (provider, model, params, refs) hash -> (expires_at, Quote)
        self._quotes: dict[str, tuple[float, Quote]] = {}

    # ------------------------------------------------------------ platform
    def _registry(self) -> Any:
        try:
            return getattr(self._platform, "media", None)
        except Exception:  # noqa: BLE001 — a lazy platform with nothing behind it
            return None

    def _config(self) -> Any:
        try:
            return getattr(self._platform, "config", None)
        except Exception:  # noqa: BLE001
            return None

    def _jobs(self) -> Any:
        try:
            return getattr(self._platform, "media_jobs", None)
        except Exception:  # noqa: BLE001
            return None

    def _setting(self, name: str, default: Any) -> Any:
        cfg = self._config()
        try:
            value = getattr(cfg, name, default)
        except Exception:  # noqa: BLE001
            return default
        return default if value is None else value

    def _artifact_sink(self) -> Callable[..., Any] | None:
        try:
            artifacts = getattr(self._platform, "artifacts", None)
        except Exception:  # noqa: BLE001
            artifacts = None
        if artifacts is None:
            return None

        def sink(name, blob, filename, kind, session_id=None, project_id=None):  # noqa: ANN001
            artifacts.save(
                name, blob, kind=kind, filename=filename,
                session_id=session_id, project_id=project_id,
            )

        return sink

    # ------------------------------------------------------------ resolve
    @staticmethod
    def _kind(args: dict[str, Any]) -> str:
        kind = str(args.get("kind") or "").strip().lower()
        if kind not in KINDS:
            raise MediaError(
                "provider_error",
                f"kind must be one of {', '.join(KINDS)} (got {kind or 'nothing'})",
            )
        return kind

    @staticmethod
    def _prompt(args: dict[str, Any]) -> str:
        prompt = str(args.get("prompt") or "").strip()
        if not prompt:
            raise MediaError("provider_error", "a prompt is needed — say what to make")
        return prompt

    async def _resolve(self, args: dict[str, Any]) -> tuple[Any, str, dict[str, Any], str, list[str]]:
        """(provider, model, params, kind, reference_paths). Raises MediaError
        with plain words (unknown provider, nothing connected, no model)."""
        kind = self._kind(args)
        prompt = self._prompt(args)
        registry = self._registry()
        if registry is None:
            raise MediaError("not_configured", "no media service is set up on this install")
        provider = registry.choose(str(args.get("provider") or ""))
        if kind not in provider.kinds():
            raise MediaError(
                "unknown_model",
                f"{provider.label} cannot make {kind} (it makes: {', '.join(sorted(provider.kinds()))})",
            )
        model = str(args.get("model") or "").strip() or await provider.default_model(kind)
        extra = args.get("params") if isinstance(args.get("params"), dict) else {}
        params: dict[str, Any] = {"prompt": prompt, **{str(k): v for k, v in extra.items()}}
        refs = _clean_refs(args.get("reference_paths"))
        # v1.347.0: the provider checks the pair BEFORE the quote and the
        # start — a bare id gets its prefix, a model that needs an input file
        # (an editor) is refused with models that work from a prompt, so a
        # wrong pick costs one round and no money.
        model, params = await provider.prepare(model, params, kind, has_refs=bool(refs))
        return provider, model, params, kind, refs

    async def _quote(self, provider: Any, model: str, params: dict[str, Any], kind: str, refs: list[str]) -> Quote:
        key = _hash_params(provider.id, model, params, refs)
        now = self._clock()
        hit = self._quotes.get(key)
        if hit is not None and hit[0] > now:
            return hit[1]
        quote = await provider.quote(model, params, kind)
        self._quotes[key] = (now + QUOTE_CACHE_S, quote)
        return quote

    # ------------------------------------------------------------ the check
    async def spend_check(self, args: dict[str, Any], ctx: ToolContext) -> SpendCheck | None:
        try:
            provider, model, params, kind, refs = await self._resolve(args)
        except MediaError:
            # Nothing to quote (no service, a bad kind): no opinion — the
            # ordinary card decides and ``execute`` says the words.
            return None
        except Exception:  # noqa: BLE001 — a fault is no opinion, never a crash in the lane
            log.warning("generate_media: spend check could not resolve the call", exc_info=True)
            return None
        try:
            quote = await self._quote(provider, model, params, kind, refs)
        except MediaError as exc:
            # The service would not quote (402 before any start, a model it
            # does not know...): a paid-or-unknown price ASKS, in the
            # service's own words.
            words = f"Generate 1 {kind} on {provider.label}; {PRICE_UNKNOWN} ({exc.words})"
            return SpendCheck(ask=True, words=words, cost_note=PRICE_UNKNOWN)
        except Exception:  # noqa: BLE001
            log.warning("generate_media: quote failed", exc_info=True)
            return SpendCheck(
                ask=True,
                words=f"Generate 1 {kind} on {provider.label}; {PRICE_UNKNOWN}",
                cost_note=PRICE_UNKNOWN,
            )
        words = quote.words(kind, provider.label)
        note = quote.cost_note()
        if quote.free and not bool(self._setting("media_ask_when_free", False)):
            return SpendCheck(ask=False, words=words, cost_note=note)
        return SpendCheck(ask=True, words=words, cost_note=note)

    # ------------------------------------------------------------ execute
    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            provider, model, params, kind, refs = await self._resolve(args)
        except MediaError as exc:
            return ToolResult(ok=False, error=exc.words)
        # The cost words: the cached quote when the check just ran, else one
        # estimate now; a service that will not quote still gets its start.
        try:
            quote = await self._quote(provider, model, params, kind, refs)
            cost_note = quote.cost_note()
        except MediaError as exc:
            if exc.code in ("insufficient_credits", "content_policy"):
                return ToolResult(ok=False, error=exc.words)
            cost_note = PRICE_UNKNOWN
        except Exception:  # noqa: BLE001
            cost_note = PRICE_UNKNOWN
        # Reference files go through the provider's own upload door; the
        # urls ride the params under one key the service can read.
        if refs:
            if not getattr(provider, "has_upload", False):
                return ToolResult(
                    ok=False, error=f"{provider.label} cannot take reference files"
                )
            urls: list[str] = []
            for rel in refs:
                path = Path(rel)
                if not path.is_absolute():
                    path = (Path(ctx.workspace) / rel)
                if not path.is_file():
                    return ToolResult(ok=False, error=f"reference file not found: {rel}")
                try:
                    urls.append(await provider.upload(path))
                except MediaError as exc:
                    return ToolResult(ok=False, error=f"reference upload failed: {exc.words}")
            params = {**params, "reference_urls": urls}
        call_id = new_id("mj")
        idem_key = f"ij-{call_id}"
        try:
            handle: JobHandle = await provider.start(model, params, kind, idem_key)
        except MediaError as exc:
            return ToolResult(ok=False, error=exc.words)
        except Exception as exc:  # noqa: BLE001 — the service's transport failed; words, no key
            return ToolResult(ok=False, error=f"{provider.label} could not start the {kind}: {type(exc).__name__}")
        wait_s = float(self._setting("media_inline_wait_s", 45) or 0)
        eta = handle.eta_s
        jobs = self._jobs()
        inline = eta is not None and eta <= wait_s
        if inline or jobs is None:
            state = await self._poll_inline(provider, handle.job_id, wait_s if inline else float(self._setting("media_job_max_s", 1800)))
            if state is not None:
                return await self._settle_inline(provider, handle, state, kind, model, params, cost_note, call_id, ctx)
            if jobs is None:
                return ToolResult(
                    ok=False,
                    error=f"the {kind} is still being made on {provider.label} and nothing here can wait for it (job {handle.job_id})",
                )
        spec = MediaJobSpec(
            provider=provider.id,
            kind=kind,
            model=model,
            prompt=str(params.get("prompt") or ""),
            provider_job_id=handle.job_id,
            params=params,
            workspace=Path(ctx.workspace),
            cost_note=cost_note,
            eta_s=eta,
        )
        # v1.347.0: a NEW chat's first turn has no thread id yet — the job
        # finds its chat through the turn once the page links it. Passed only
        # when set (an agent run has none; older doubles take no such kwarg).
        turn_id = str(getattr(ctx, "turn_id", "") or "")
        job_id = jobs.start_background(
            spec,
            thread_id=str(getattr(ctx, "thread_id", "") or ""),
            session_id=ctx.session_id,
            project_id=getattr(ctx, "project_id", None),
            **({"turn_id": turn_id} if turn_id else {}),
        )
        return ToolResult(
            ok=True,
            output=(
                f"{kind} started on {provider.label} ({cost_note}); it will appear in "
                f"this chat when it is ready and the bell will say so (job {job_id})"
            ),
            data={"job_id": job_id, "started": True, "kind": kind, "provider": provider.id, "cost_note": cost_note},
        )

    async def _poll_inline(self, provider: Any, job_id: str, wait_s: float) -> JobState | None:
        """Poll until done or ``wait_s`` has passed on the injected clock;
        ``None`` = still running (the caller hands it to the background)."""
        started = self._clock()
        delay = INLINE_POLL_MIN_S
        while True:
            state: JobState = await provider.poll(job_id)
            if state.done:
                return state
            if self._clock() - started >= wait_s:
                return None
            await self._sleep(delay)
            delay = min(delay * 2, INLINE_POLL_MAX_S)

    async def _settle_inline(
        self,
        provider: Any,
        handle: JobHandle,
        state: JobState,
        kind: str,
        model: str,
        params: dict[str, Any],
        cost_note: str,
        call_id: str,
        ctx: ToolContext,
    ) -> ToolResult:
        if state.status != "succeeded":
            reason = state.error or f"{provider.label} reported a failure"
            return ToolResult(ok=False, error=f"the {kind} could not be made: {reason}")
        try:
            data = await provider.fetch(state)
        except MediaError as exc:
            return ToolResult(ok=False, error=f"the {kind} was made but could not be downloaded: {exc.words}")
        ext = state.ext or provider.ext_for(kind)
        try:
            landed = await asyncio.to_thread(
                deliver,
                data,
                kind=kind,
                provider=provider.id,
                job_id=call_id,
                ext=ext,
                workspace=Path(ctx.workspace),
                session_id=ctx.session_id,
                project_id=getattr(ctx, "project_id", None),
                artifact_sink=self._artifact_sink(),
            )
        except OSError as exc:
            return ToolResult(ok=False, error=f"the {kind} was made but could not be saved: {exc}")
        abs_path = str(landed.get("abs_path") or "")
        # What was CHARGED when the service says (Pixio's billing), beside
        # what was quoted when the two differ — the ledger row must keep
        # saying "free" for a free quote.
        charged = str(state.cost_note or "").strip()
        note = cost_note if not charged or charged == cost_note else f"{charged}; quoted {cost_note}"
        return ToolResult(
            ok=True,
            output=f"{kind} made on {provider.label} ({note}): {abs_path}",
            data={
                "media": [abs_path],
                "kind": kind,
                "provider": provider.id,
                "job_id": call_id,
                "provider_job_id": handle.job_id,
                "model": model,
                "cost_note": note,
                "artifact": landed.get("artifact") or "",
            },
            created_paths=[abs_path] if landed.get("created") else None,
        )


def media_tools(platform: Any, **kw: Any) -> list[Tool]:
    """The tool list ``platform.py`` registers (one tool today)."""
    return [GenerateMediaTool(platform, **kw)]
