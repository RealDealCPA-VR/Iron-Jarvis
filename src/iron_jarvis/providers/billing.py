"""WHO PAYS FOR A CALL — the ONE definition of "pay per use" (v1.424.0, the
owner's decision D1).

The owner, verbatim: "Failover should remain within the same models provided.
No pay-per-use unless a pay-per-use model was chosen at the onset." Before
this, a call the user sent to a subscription (Claude Code, Codex, Grok) or to
a local model could, when that provider failed, be answered by an API key
that bills per token — in plain chat, agent runs, schedules and the one-shot
utilities — because the failover ladder (``router._FAILOVER_ORDER``) walked
the metered APIs right after the subscriptions.

This module answers ONE question three ways, and every surface that needs it
reads it here (the model team's cost words ``agents/model_team.cost_type``
delegate to :func:`billing_kind`; the router's failover filter and the
one-shot failover chain in ``daemon/app.py`` call :func:`is_pay_per_use`):

* ``free`` — the model stays on this network (``egress.stays_on_this_network``,
  the local-only lock's own predicate: Ollama, a fleet node on this network,
  OpenCode's local models, the demo model);
* ``plan`` — a subscription CLI (``claude-cli`` / ``codex-cli`` /
  ``grok-cli``), or a keyless API name a signed-in CLI serves
  (``ProviderManager.inherited_from``: a keyless ``anthropic`` is Claude
  Code). The user's plan's limits apply; nothing is billed per message;
* ``pay`` — billed per use: a keyed API (Anthropic, OpenAI, Google, xAI,
  OpenRouter, …), an OpenAI- or Anthropic-compatible endpoint whose model
  leaves this network (a LiteLLM proxy's ``frontier`` alias that goes to
  OpenRouter is pay per use; its local deployment is ``free``), and a Claude
  model that draws pay-as-you-go usage credits
  (``eval.pricing.draws_usage_credits``).

The dashboard's model menu badges say the same three things in its own words
("local", "cloud · on your plan", "cloud · pay per use") from the /models
row's ``kind`` / ``inherited_from`` / ``on_this_network`` — the facts this
module reads, so the menu and the router agree.

``unsure`` is the answer when the reading itself FAILS (an exception — never
a merely unknown address, which is a fact: a model whose address is not on
this network is not ``free``). The two callers want opposite sides: a
CANDIDATE someone might be moved to is ``pay`` when unsure (never spend
money on a guess), while the USER'S OWN PICK is ``plan`` when unsure (never
widen the failover to paid keys on a guess). Pure: no network, no process,
never raises."""

from __future__ import annotations

from typing import Any, Iterable

#: The three answers (dashboard twin: ``lib/modelTeam.ts`` COST_WORDS).
COST_FREE, COST_PLAN, COST_PAY = "free", "plan", "pay"
COST_WORDS: dict[str, str] = {COST_FREE: "free", COST_PLAN: "your plan", COST_PAY: "pay per use"}

#: Subscription CLIs: work on them is covered by the user's plan.
PLAN_CLIS = frozenset({"claude-cli", "codex-cli", "grok-cli"})


def served_cli(provider: str, manager: Any) -> str:
    """The CLI a provider is served by: itself, or (a keyless ``anthropic`` /
    ``openai``) the CLI the manager says serves it; "" otherwise. Never
    raises."""
    p = (provider or "").strip().lower()
    if p in PLAN_CLIS:
        return p
    if manager is None:
        return ""
    try:
        served = str(manager.inherited_from(p) or "")
    except Exception:  # noqa: BLE001
        return ""
    return served if served in PLAN_CLIS else ""


def billing_kind(
    provider: str,
    manager: Any = None,
    config: Any = None,
    model: str = "",
    *,
    unsure: str = COST_PAY,
) -> str:
    """``free`` / ``plan`` / ``pay`` for a call to *provider* (for *model*;
    ``""`` = the provider's own default). See the module docstring. Never
    raises: a reading that fails answers *unsure*."""
    from .egress import stays_on_this_network

    try:
        if stays_on_this_network(provider, manager, config, model):
            return COST_FREE
        cli = served_cli(provider, manager)
        if cli:
            if cli == "claude-cli":
                from ..eval.pricing import draws_usage_credits

                if draws_usage_credits(model):
                    return COST_PAY
            return COST_PLAN
    except Exception:  # noqa: BLE001
        return unsure
    return COST_PAY


def is_pay_per_use(
    provider: str, manager: Any = None, config: Any = None, model: str = "", *, unsure: bool = True
) -> bool:
    """True when a call to *provider* (for *model*) is billed per use. A
    reading that fails answers *unsure* (``True`` — a CANDIDATE is never
    spent on a guess; pass ``False`` for the user's own pick). Never raises."""
    kind = billing_kind(
        provider, manager, config, model, unsure=COST_PAY if unsure else COST_PLAN
    )
    return kind == COST_PAY


def adapter_pays(adapter: Any, manager: Any = None, config: Any = None, *, unsure: bool = True) -> bool:
    """:func:`is_pay_per_use` for the adapter that would REALLY answer (its
    ``provider`` and ``model`` — a keyless ``anthropic`` resolves to the
    ``claude-cli`` adapter, which is the plan). Never raises."""
    try:
        provider = str(getattr(adapter, "provider", "") or "")
        model = str(getattr(adapter, "model", "") or "")
    except Exception:  # noqa: BLE001
        return unsure
    return is_pay_per_use(provider, manager, config, model, unsure=unsure)


# --------------------------------------------------------------------------- #
# the one sentence a failure carries when a pay-per-use model was LEFT OUT
# --------------------------------------------------------------------------- #

#: The attribute a failure carries when the failover left a pay-per-use model
#: out: the provider ids, in the order they would have been tried. Read by
#: ``failure_words`` (the chat's fix line, a run's reason) and by the
#: one-shot utilities' HTTP answer.
LEFT_OUT_ATTR = "paid_left_out"

#: How far the cause chain is walked (the router wraps once; a lane may wrap
#: again). Bounded so a cyclic chain can never spin.
_CHAIN = 6


def mark_left_out(exc: BaseException | None, providers: Iterable[str]) -> None:
    """Stamp *exc* with the pay-per-use providers the failover did NOT move
    the call to (deduped, order kept). An empty list stamps nothing. Never
    raises."""
    if exc is None:
        return
    try:
        names: list[str] = []
        for p in providers:
            p = str(p or "").strip()
            if p and p not in names:
                names.append(p)
        if not names:
            return
        prior = list(getattr(exc, LEFT_OUT_ATTR, ()) or ())
        for p in names:
            if p not in prior:
                prior.append(p)
        setattr(exc, LEFT_OUT_ATTR, tuple(prior))
    except Exception:  # noqa: BLE001 — wording only, never a new failure
        pass


def left_out(exc: BaseException | None) -> tuple[str, ...]:
    """The pay-per-use providers a failure says it was NOT moved to (outermost
    stamp first along the cause chain), else ``()``. Never raises."""
    seen = 0
    cur = exc
    while cur is not None and seen < _CHAIN:
        try:
            got = getattr(cur, LEFT_OUT_ATTR, None)
            if got:
                return tuple(str(p) for p in got if p)
        except Exception:  # noqa: BLE001
            pass
        cur = cur.__cause__ or cur.__context__
        seen += 1
    return ()


def _join(names: list[str]) -> str:
    if len(names) <= 1:
        return names[0] if names else ""
    if len(names) == 2:
        return f"{names[0]} or {names[1]}"
    return ", ".join(names[:-1]) + f", or {names[-1]}"


def not_moved_sentence(providers: Iterable[str], manager: Any = None) -> str:
    """The ONE sentence a failure carries when the failover left pay-per-use
    models out (decision D1): "Iron Jarvis didn't switch to OpenAI, which
    bills per use, because you didn't choose a pay-per-use model for this." — the
    providers by their plain names (``failure_words.plain_name``: a fleet
    node by the label the user gave it). ``""`` for none. Never raises."""
    try:
        from .failure_words import plain_name

        names: list[str] = []
        for p in providers:
            name = plain_name(str(p or ""), manager) or str(p or "")
            if name and name not in names:
                names.append(name)
        if not names:
            return ""
        which = "bills" if len(names) == 1 else "bill"
        return (
            f"Iron Jarvis didn't switch to {_join(names)}, which {which} per use, "
            "because you didn't choose a pay-per-use model for this."
        )
    except Exception:  # noqa: BLE001 — wording only
        return ""


def not_moved_for(exc: BaseException | None, manager: Any = None) -> str:
    """:func:`not_moved_sentence` for the providers *exc* was stamped with,
    else ``""``. Never raises."""
    return not_moved_sentence(left_out(exc), manager)


__all__ = [
    "COST_FREE", "COST_PAY", "COST_PLAN", "COST_WORDS", "LEFT_OUT_ATTR", "PLAN_CLIS",
    "adapter_pays", "billing_kind", "is_pay_per_use", "left_out", "mark_left_out",
    "not_moved_for", "not_moved_sentence", "served_cli",
]
