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
    """True when the default model's provider is one of *leaving*."""
    current = str(getattr(d.platform.config, "default_provider", "") or "")
    return bool(current) and current in leaving


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


def _model_for(d: Any, name: str) -> str:
    """The model that goes with a replacement: the Connections page's own
    rule for a key or a plan, the server's first model for a local slot."""
    from .routes.settings import _first_local_model, promote_model

    if name in ("ollama", "custom"):
        try:
            return _first_local_model(d, name)
        except Exception:  # noqa: BLE001
            return ""
    if name in ("claude-cli", "codex-cli"):
        return "subscription"
    if name.startswith("fleet-"):
        try:
            node = d.fleet.get(name[len("fleet-"):])
            return str(getattr(node, "default_model", "") or "")
        except Exception:  # noqa: BLE001
            return ""
    try:
        return str(promote_model(d, name) or "")
    except Exception:  # noqa: BLE001
        return ""


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
            model = _model_for(d, name)
            if name in ("ollama", "custom") and not model:
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
        values: dict[str, Any] = {"default_provider": chosen["provider"]}
        if chosen["model"]:
            values["default_model"] = chosen["model"]
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
