"""The ONE decision ladder that turns a rendered transcript into a memory note.

Two surfaces commit a conversation to long-term memory: ``POST
/chat/threads/{id}/remember`` (a two-party chat) and
``AgentThreads.remember`` (an N-agent round table). They ask the same question —
"what mattered here, and what do we store when no real model can answer it?" —
and until v1.185.0 they answered it in two places.

That duplication was NOT an oversight anybody could have fixed in passing:
chat's ladder lived INLINE inside its route handler, as a closure inside
``register(app, d)``, so there was no importable symbol to call. The panel
implementation therefore re-derived the ladder and reached back into
``daemon.routes.chat`` for the two budgets — an agents module importing from a
route module, which is the layering upside down. This module is the fix: the
ladder lives here, both routes call it, and nothing imports a route to get it.

WHAT IS SHARED AND WHAT IS NOT. Shared: the budgets, the clip contract, the
mock refusal, the failover hop, the one-shot call, and the degrade-don't-refuse
outcome. NOT shared, and deliberately so:

* **The transcript renderer.** Chat's speaker vocabulary is two words wide
  ("You" / "Iron Jarvis") because a chat has two speakers; flattening five
  panelists into one voice would discard the only thing a round table produces
  that a chat cannot — who concluded what.
* **The system prompt**, which is why it is a PARAMETER here and not a constant.
  A panel prompt must ATTRIBUTE each claim to the agent that made it and must
  never resolve a disagreement the panel left open. Both instructions are
  meaningless for a two-party chat, and a single prompt trying to serve both
  would either lose the attribution or tell a chat model to name speakers that
  do not exist.

THE HONEST-MOCK RULE IS THE POINT OF THE LADDER. A mock adapter would fabricate
a memory of a real conversation, and a memory note is read back later as
authoritative — so a mock is refused, a real provider is tried instead, and with
none connected the outcome DEGRADES to a verbatim excerpt and says so. It
degrades rather than refuses because memory has to keep working offline. That is
why :attr:`Distillation.note` is not optional decoration: it is the only thing
distinguishing "the model summarized this" from "no model saw this".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Distill-mode input budget (chars) for committing a thread to memory —
#: clipped head+tail with an EXPLICIT omission marker. The model must never
#: receive a silently truncated conversation and present its digest as complete.
REMEMBER_INPUT = 24_000

#: Verbatim-excerpt budget when a thread is committed WITHOUT a model.
REMEMBER_VERBATIM = 8_000


def clip_with_marker(text: str, budget: int, marker: str) -> str:
    """Head+tail clip with an EXPLICIT omission marker.

    A silently truncated transcript is the one thing that must never happen
    here: the model would present a digest of the last third as the whole
    conversation, and a memory note is read back later as authoritative.
    """
    if len(text) <= budget:
        return text
    head, tail = budget // 3, budget * 2 // 3
    return text[:head] + marker + text[-tail:]


def omission_marker(subject: str, *, for_model: bool = False) -> str:
    """The marker a clip leaves behind, named for what was clipped.

    ``for_model`` adds the instruction to carry the omission into the note —
    only the DISTILL input needs it, because only that text is read by a model
    that could otherwise describe a third of a conversation as all of it.
    """
    tail = " — note this in the memory " if for_model else " "
    return f"\n\n[… middle of the {subject} omitted for length{tail}…]\n\n"


@dataclass(frozen=True)
class Distillation:
    """What the ladder decided, and how honestly it got there.

    ``note`` is empty ONLY on a real distillation. Every degraded path fills it,
    and every caller surfaces it — a verbatim excerpt presented as a summary is
    the exact failure the honest-mock rule exists to prevent.
    """

    #: The text to store: a model's digest, or the verbatim excerpt.
    body: str
    #: True ONLY when a real model produced ``body``.
    distilled: bool
    #: The provider that actually answered ("" when none did).
    provider: str
    #: Why this is not a distillation ("" when it is one).
    note: str


async def distill_or_excerpt(
    d: Any,
    *,
    transcript: str,
    mode: str,
    system: str,
    subject: str,
    provider: str = "",
    model: str = "",
) -> Distillation:
    """Distill ``transcript`` with a real model, or degrade to an excerpt.

    ``mode`` ``"full"`` skips the model entirely and returns the excerpt with no
    note — the user asked for the verbatim text, so there is nothing to
    apologize for. ``"distill"`` runs the ladder.

    ``system`` is the distill instruction; see the module docstring for why it
    is a parameter. ``subject`` names what is being clipped ("conversation",
    "panel") and appears in the omission markers and the degraded notes.

    Never raises for a provider problem: an unreachable provider, a thrown
    completion and an empty response all degrade to the excerpt with a note.
    """
    verbatim = clip_with_marker(
        transcript, REMEMBER_VERBATIM, omission_marker(subject)
    )
    if (mode or "").strip().lower() != "distill":
        return Distillation(body=verbatim, distilled=False, provider="", note="")

    from ..providers.adapters.base import LLMMessage
    from ..providers.adapters.mock import MockLLMAdapter

    want_provider = provider or d.platform.config.default_provider
    want_model = model or d.platform.config.default_model
    try:
        adapter = d.platform.providers.get(want_provider, want_model)
    except Exception:  # noqa: BLE001 — an unreachable provider IS offline here
        adapter = None
    # A mock would FABRICATE a memory of a real conversation. Hop to a real
    # provider; with none connected, fall through to the excerpt.
    if adapter is not None and isinstance(adapter, MockLLMAdapter):
        adapter, want_provider = d._failover_adapter("mock")
    if adapter is None:
        return Distillation(
            body=verbatim,
            distilled=False,
            provider="",
            note=(
                "no real model connected — stored a verbatim excerpt, not a"
                " distillation"
            ),
        )

    clipped = clip_with_marker(
        transcript, REMEMBER_INPUT, omission_marker(subject, for_model=True)
    )
    try:
        resp, used_provider, _model = await d._one_shot_complete(
            want_provider,
            adapter,
            system=system,
            messages=[LLMMessage(role="user", content=clipped)],
        )
        digest = (resp.text or "").strip()
    except Exception as exc:  # noqa: BLE001 — degrade, never lose the memory
        return Distillation(
            body=verbatim,
            distilled=False,
            provider="",
            note=f"distillation failed ({exc}) — stored a verbatim excerpt",
        )
    if not digest:
        return Distillation(
            body=verbatim,
            distilled=False,
            provider="",
            note="the model returned nothing — stored a verbatim excerpt",
        )
    return Distillation(
        body=digest, distilled=True, provider=str(used_provider or ""), note=""
    )
