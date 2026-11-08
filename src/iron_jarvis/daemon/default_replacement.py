"""The default model never points at nothing (v1.449.0, readiness Wave C
item 24 — ease-of-use review item 47).

Two doors take a model's server away: removing a model server (a Fleet
node or the ``ollama`` / ``custom`` slot, ``routes.fleet.delete_fleet_node``)
and disconnecting an account (``DELETE /connections/{provider}``). Both used
to leave ``config.default_provider`` pointing at the name they had just
retired, so every chat after that failed with "isn't connected" until the
user found Settings › Models. Now both call :func:`apply` BEFORE anything
goes: when the leaving names hold the default, the default is moved — through
THE settings writer (ledger + Undo) — to another connected model under the
same rule the Connections page's "Make default" uses (``promote_model``), or,
when nothing else is connected, to the demo model with a plain warning.
:func:`preview` is the same decision as words, for the dashboard's confirm
("Claude Code is what answers your chats. Jarvis will use Gemini instead.")
before the press.

Every read here is CACHED (``cli_auth.never_probe``: a signed-in verdict
the daemon already holds counts, an unknown one stays unknown): a removal
must never start a CLI. A fault anywhere answers "not affected" and the
removal goes on as it always did — a refusal to remove because this module
could not decide would be worse than an honest fallback.
"""

from __future__ import annotations

from typing import Any

from ..core.logging import get_logger
from ..providers.cli_auth import never_probe as cached_verdicts

log = get_logger(__name__)

#: The demo model's provider id.
DEMO = "mock"

#: The order a replacement is looked for in: the subscriptions first (they
#: cost nothing per message), then this PC's own servers, then keys. The
#: manager's own Auto order; anything registered that it does not name follows.
PREFERRED_ORDER: tuple[str, ...] = (
    "claude-cli", "codex-cli", "ollama", "custom", "openrouter",
    "google", "openai", "anthropic", "xai",
)

#: The CLIs a keyless API name is served through (``ProviderManager._INHERIT_ALIAS``).
_INHERIT_ALIAS = {"anthropic": "claude-cli", "openai": "codex-cli"}

#: The words, in one place.
ANSWERS_NOW = "{current} is what answers your chats right now."
WILL_USE = "Jarvis will use {replacement} instead."
NOW_USES = "Jarvis now answers with {replacement}."
DEMO_WARNING = (
    "Nothing else is connected, so Jarvis will answer with the demo model "
    "(scripted replies, not real answers) until you connect a model in Settings › Connections."
)
DEMO_NOW = (
    "Nothing else is connected, so Jarvis answers with the demo model "
    "(scripted replies, not real answers) until you connect a model in Settings › Connections."
)


def names_for_node(d: Any, node_id: str) -> set[str]:
    """Every provider name a Fleet node answers under: ``fleet-<id>`` always,
    and the slot's own name (``ollama`` / ``custom``) for a config seed."""
    names = {f"fleet-{node_id}"}
    try:
        node = d.fleet.get(node_id)
    except Exception:  # noqa: BLE001 — an unreadable registry names the fleet id alone
        node = None
    if node is not None and getattr(node, "source", "") == "config":
        names.add(node_id)
    return names


def names_for_disconnect(d: Any, provider: str) -> set[str]:
    """The names a ``DELETE /connections/{provider}`` takes away. A keyless
    API name that its CLI still serves (``anthropic`` through a signed-in
    Claude Code) keeps answering after its key is dropped: nothing leaves."""
    pm = d.platform.providers
    alias = _INHERIT_ALIAS.get(provider)
    if alias:
        try:
            with cached_verdicts():
                if getattr(pm, "_inherit_cli", True) and pm.available(alias):
                    return set()
        except Exception:  # noqa: BLE001 — unknown reads as "it leaves"
            pass
    return {provider}


def label_of(d: Any, provider: str) -> str:
    """The plain name a person reads for *provider* (a Fleet node by its
    label, the rest by ``onboarding.checklist.provider_label``)."""
    if provider.startswith("fleet-"):
        try:
            node = d.fleet.get(provider[len("fleet-"):])
        except Exception:  # noqa: BLE001
            node = None
        if node is not None:
            return str(getattr(node, "label", "") or getattr(node, "base_url", "") or provider)
    if provider == DEMO:
        return "the demo model"
    try:
        from ..onboarding.checklist import provider_label

        return provider_label(d.platform, provider)
    except Exception:  # noqa: BLE001 — a label is cosmetic
        return provider or "a model"


def affected(d: Any, leaving: set[str]) -> bool:
    """True when the default model's provider is one of *leaving*. The demo
    model and Auto are never "removed" (nothing serves them that a removal
    takes away), so a junk ``leaving`` that names them moves nothing."""
    current = str(getattr(d.platform.config, "default_provider", "") or "")
    return bool(current) and current not in (DEMO, "auto") and current in leaving


def _candidates(d: Any, leaving: set[str]) -> list[str]:
    pm = d.platform.providers
    try:
        registered = [str(n) for n in getattr(pm, "_factories", {})]
    except Exception:  # noqa: BLE001
        registered = []
    ordered = [p for p in PREFERRED_ORDER if p in registered]
    ordered += [p for p in registered if p not in ordered]
    out: list[str] = []
    for name in ordered:
        if name in leaving or name in (DEMO, "auto"):
            continue
        # A keyless API name is its CLI by another name: one entry, the CLI's.
        alias = _INHERIT_ALIAS.get(name)
        if alias and alias in ordered and alias not in leaving:
            try:
                if pm.inherited_from(name) == alias:
                    continue
            except Exception:  # noqa: BLE001
                pass
        out.append(name)
    return out


#: Replacements that need a MODEL of their own before they can answer: a
#: server with nothing listed would trade a dead default for an error. Every
#: other provider answers with its adapter's own default on an empty model
#: (checked: google, openrouter, xai, grok-cli, opencode-cli, the demo).
_NEEDS_A_MODEL = ("ollama", "custom")


def _fleet_model(d: Any, node_id: str) -> str:
    """A Fleet node's model: its own default, else the first one the sampler
    has SEEN it list (a cached reading), else "" (the node is skipped)."""
    try:
        node = d.fleet.get(node_id)
    except Exception:  # noqa: BLE001
        node = None
    own = str(getattr(node, "default_model", "") or "").strip()
    if own:
        return own
    try:
        # The sampler holds what each node LISTED (``platform.fleet_sampler``;
        # the registry holds no readings); a daemon without one lists nothing.
        sampler = getattr(getattr(d, "platform", None), "fleet_sampler", None)
        for snap in sampler.snapshots() if sampler is not None else ():
            snap_node = getattr(snap, "node", None)
            if str(getattr(snap_node, "id", "") or getattr(snap, "id", "") or "") != node_id:
                continue
            for entry in getattr(snap, "models", None) or ():
                mid = str(getattr(entry, "id", "") or "").strip()
                if mid:
                    return mid
    except Exception:  # noqa: BLE001 - a snapshot that cannot be read lists nothing
        pass
    return ""


def _model_for(d: Any, name: str) -> str:
    """The model that goes with a replacement, NEVER the model the leaving
    server answered under: the Connections page's own pairing for a key
    (``_PROMOTE_DEFAULT_MODEL``; the plan's default for a keyless name a CLI
    serves), the plan's default for a CLI, the server's model for a local
    slot or a Fleet node, and "" - the adapter's own default - for a provider
    the table does not name. ``promote_model`` itself is not used here: its
    fallback is the CURRENT ``default_model``, which is the removed server's."""
    from .routes.settings import PLAN_DEFAULT_MODEL, _first_local_model

    if name in _NEEDS_A_MODEL:
        try:
            return _first_local_model(d, name)
        except Exception:  # noqa: BLE001
            return ""
    if name in ("claude-cli", "codex-cli"):
        return PLAN_DEFAULT_MODEL
    if name.startswith("fleet-"):
        return _fleet_model(d, name[len("fleet-"):])
    try:
        if d.platform.providers.inherited_from(name):
            return PLAN_DEFAULT_MODEL
    except Exception:  # noqa: BLE001 - a probe fault keeps the table's pairing
        pass
    try:
        table = getattr(d, "_PROMOTE_DEFAULT_MODEL", None) or {}
        return str(table.get(name, "") or "")
    except Exception:  # noqa: BLE001
        return ""


def _key_refused(d: Any, name: str) -> bool:
    """True when the key this provider holds NOW was checked and refused
    (the Directory's save, a Test press - process memory, never a probe):
    a present key is not a working key, and the Connections card already
    says so in amber."""
    try:
        from ..connectors.service import key_check

        checked = key_check(d.platform, name)
    except Exception:  # noqa: BLE001 - no memory reads as "not refused"
        return False
    return checked is not None and not checked[0]


def pick(d: Any, leaving: set[str]) -> dict[str, Any]:
    """The replacement for a default among *leaving*: the first connected
    model in :data:`PREFERRED_ORDER` with a model to pair it with, else the
    demo model (``demo: True``). Reads cached answers only."""
    pm = d.platform.providers
    with cached_verdicts():
        for name in _candidates(d, leaving):
            try:
                ok = bool(pm.available(name))
            except Exception:  # noqa: BLE001 — a probe fault is "not connected"
                ok = False
            if not ok:
                continue
            if _key_refused(d, name):
                continue  # a key the service refused is not a connection
            model = _model_for(d, name)
            if (name in _NEEDS_A_MODEL or name.startswith("fleet-")) and not model:
                continue  # a server with nothing on it would trade a dead default for an error
            return {"provider": name, "model": model, "label": label_of(d, name), "demo": False}
    return {"provider": DEMO, "model": "", "label": label_of(d, DEMO), "demo": True}


def preview(d: Any, leaving: set[str]) -> dict[str, Any]:
    """What a removal of *leaving* would do to the default, as words — for
    the confirm the dashboard shows BEFORE the press. Never raises."""
    try:
        current = str(getattr(d.platform.config, "default_provider", "") or "")
        if not affected(d, leaving):
            return {"affected": False, "current": None, "replacement": None, "demo": False, "words": ""}
        current_label = label_of(d, current)
        chosen = pick(d, leaving)
        head = ANSWERS_NOW.format(current=current_label)
        tail = DEMO_WARNING if chosen["demo"] else WILL_USE.format(replacement=chosen["label"])
        return {
            "affected": True,
            "current": {"provider": current, "label": current_label},
            "replacement": chosen,
            "demo": bool(chosen["demo"]),
            "words": f"{head} {tail}",
        }
    except Exception:  # noqa: BLE001 — a preview must never fail a page
        log.debug("default replacement preview failed", exc_info=True)
        return {"affected": False, "current": None, "replacement": None, "demo": False, "words": ""}


def apply(d: Any, leaving: set[str], *, actor: str) -> dict[str, Any] | None:
    """Move the default off *leaving* BEFORE the removal, through the settings
    writer. Returns what was done (``{from, to, model, demo, words}``) or
    ``None`` when the default was not among *leaving*. Never raises: a fault
    is logged and the removal goes on (an honest "not re-pointed" is better
    than a refusal nobody can act on)."""
    try:
        if not affected(d, leaving):
            return None
        current = str(d.platform.config.default_provider or "")
        chosen = pick(d, leaving)
        # The model is ALWAYS written with the provider - "" means the new
        # adapter's own default. Leaving ``default_model`` alone would keep
        # the removed server's model name beside the new provider.
        values: dict[str, Any] = {"default_provider": chosen["provider"], "default_model": chosen["model"]}
        from .routes.settings import config_writer

        config_writer(d).apply(values, actor=actor)
        words = DEMO_NOW if chosen["demo"] else NOW_USES.format(replacement=chosen["label"])
        log.info(
            "default model moved off %s (removed) to %s/%s", current, chosen["provider"], chosen["model"]
        )
        return {
            "from": current,
            "from_label": label_of(d, current),
            "to": chosen["provider"],
            "to_label": chosen["label"],
            "model": chosen["model"],
            "demo": bool(chosen["demo"]),
            "words": words,
        }
    except Exception:  # noqa: BLE001
        log.warning("the default model could not be moved before the removal", exc_info=True)
        return None


#: The words for a keyless name its CLI keeps serving after its key is dropped.
KEPT_THROUGH_CLI = (
    "{current} keeps answering your chats through {cli}; its model is now "
    "the plan's own default, which that sign-in accepts."
)


def keep_through_cli(d: Any, provider: str, *, actor: str) -> dict[str, Any] | None:
    """A keyless API name its signed-in CLI still serves keeps the default -
    but the key's MODEL may not survive the hand-over: Codex refuses a model
    its plan does not offer (the v1.445.0 lesson behind ``promote_model``),
    so ``openai`` + ``gpt-4o-mini`` served through Codex failed its first
    answer. Re-pair the default with the plan's own default (what "Use
    ChatGPT for answers" writes) through the settings writer. Returns what
    was done, or ``None`` when the default is not this name or already pairs
    with the plan's default. Never raises."""
    try:
        from .routes.settings import PLAN_DEFAULT_MODEL, config_writer

        cfg = d.platform.config
        current = str(getattr(cfg, "default_provider", "") or "")
        if current != provider:
            return None
        if str(getattr(cfg, "default_model", "") or "") == PLAN_DEFAULT_MODEL:
            return None
        alias = _INHERIT_ALIAS.get(provider)
        if not alias:
            return None
        config_writer(d).apply({"default_model": PLAN_DEFAULT_MODEL}, actor=actor)
        words = KEPT_THROUGH_CLI.format(current=label_of(d, provider), cli=label_of(d, alias))
        log.info("default %s kept through %s; model re-paired to %s", provider, alias, PLAN_DEFAULT_MODEL)
        return {
            "from": provider,
            "from_label": label_of(d, provider),
            "to": provider,
            "to_label": label_of(d, provider),
            "model": PLAN_DEFAULT_MODEL,
            "demo": False,
            "kept": True,
            "words": words,
        }
    except Exception:  # noqa: BLE001
        log.warning("the kept default's model could not be re-paired", exc_info=True)
        return None
