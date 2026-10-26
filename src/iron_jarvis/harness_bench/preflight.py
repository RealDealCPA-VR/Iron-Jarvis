"""What a benchmark of Iron Jarvis's own models is checked for BEFORE its
first task (v1.396.0 review of the long-jobs plan's Phase 7: MEASURED).

A team run is unattended for hours. A typo in a member's model, a provider
the benchmark's home does not have, a gateway that is down or wants a key the
home lacks, a CLI the last check said is signed out: each of those used to
surface only at task 1 — or worse, as a run that quietly worked without the
member (the planner did the piece itself, and the row read as "the team").
:func:`check_models` finds them first, in one plain sentence each, for the
planner and every member, and says what it CANNOT know without starting a
vendor's CLI.

THE RULES:

* never a model call, never a sign-in, never a CLI started: a CLI's sign-in is
  the CACHED answer only (``cli_auth.cached_verdicts`` / ``never_probe`` — a
  fresh process has none, which is SAID: "not known yet");
* a model server on the user's network (``custom``, ``ollama``, a fleet node)
  is asked ONCE, bounded (:data:`PROBE_TIMEOUT_S`), for its model list —
  with the key the benchmark's home saved for THAT server (the app's own
  probe, ``routes/connections._probe_endpoint_models``: no redirects, so the
  key never travels to another host), and the words name its host only;
* a problem REFUSES the run (``BenchRefused``); anything else is a NOTE the
  dry run lists and a real run says before its first task.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable
from urllib.parse import urlsplit

#: One bounded ask of a model server for its model list.
PROBE_TIMEOUT_S = 5.0
#: How many of a server's models a refusal names.
LISTED_IN_WORDS = 8
#: Providers whose model servers are asked for their list.
_ENDPOINT_SLOTS = frozenset({"custom", "ollama"})
#: The CLIs whose sign-in is a cached verdict: provider -> (binary, name).
_CLIS: dict[str, tuple[str, str]] = {
    "claude-cli": ("claude", "Claude Code"),
    "codex-cli": ("codex", "Codex"),
}
#: The words ``claude --model`` takes besides a full id (Claude Code's own
#: aliases; ``claude_models.PINNED_ALIASES`` maps them).
_CLAUDE_ALIASES = frozenset({"sonnet", "opus", "haiku", "fable", "opusplan", "sonnet[1m]", "opus[1m]"})


@dataclass
class Ready:
    """What the checks found: ``problems`` refuse the run, ``notes`` are said."""

    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _host(url: str) -> str:
    """``host:port`` of an address — never its path or query (a key can ride
    one)."""
    try:
        parts = urlsplit(url if "://" in url else "http://" + url)
        host = parts.hostname or ""
        return f"{host}:{parts.port}" if parts.port else host
    except ValueError:
        return "the saved address"


def _known_provider(manager: Any, provider: str) -> bool:
    factories = getattr(manager, "_factories", None)
    if isinstance(factories, dict):
        return provider in factories
    try:
        manager.get(provider)
        return True
    except Exception:  # noqa: BLE001
        return False


def _cached_sign_in(binary: str) -> bool | None:
    """The last sign-in answer this process has for a CLI, or None. Never
    starts the CLI."""
    try:
        from ..providers.cli_auth import DEFAULT_PROBE, cached_verdicts, never_probe

        with cached_verdicts(), never_probe():
            return DEFAULT_PROBE.verdict(binary)
    except Exception:  # noqa: BLE001
        return None


def _endpoint(platform: Any, provider: str) -> tuple[str, str]:
    """``(base_url, key)`` the provider's adapter will call, read from the
    benchmark's home ("" when none is saved). BLOCKING (a vault read)."""
    from ..providers.egress import endpoint_url

    url = endpoint_url(provider, getattr(platform, "providers", None), getattr(platform, "config", None))
    if not url:
        return "", ""
    key = ""
    fleet = getattr(platform, "fleet", None)
    try:
        if fleet is not None:
            key = fleet.slot_key(provider) if provider in _ENDPOINT_SLOTS else ""
            key = key or fleet.key_for_url(url) or ""
    except Exception:  # noqa: BLE001 — no key read: the probe says what the server wants
        key = ""
    return url, key


def _probe(platform: Any, url: str, key: str) -> dict[str, Any]:
    """The app's own model-list probe (bounded, no redirects). BLOCKING."""
    from ..daemon.routes.connections import _probe_endpoint_models

    ollama_ok = None
    fleet = getattr(platform, "fleet", None)
    try:
        if fleet is not None:
            ollama_ok = fleet.ollama_style_ok(url)
    except Exception:  # noqa: BLE001
        ollama_ok = None
    try:
        return _probe_endpoint_models(url, key, timeout_s=PROBE_TIMEOUT_S, ollama_ok=ollama_ok)
    except Exception as exc:  # noqa: BLE001 — a probe that breaks is said, never raised
        return {"models": [], "error": f"the check failed ({type(exc).__name__})", "reason": "error"}


def _check_endpoint(platform: Any, who: str, provider: str, model: str, ready: Ready,
                    seen: dict[str, dict[str, Any]]) -> None:
    url, key = _endpoint(platform, provider)
    place = "the custom endpoint" if provider == "custom" else (
        "the Ollama endpoint" if provider == "ollama" else f"the model server {provider}")
    if not url:
        ready.problems.append(
            f"{who}: {place} has no address saved in the benchmark's home, so {model} cannot be "
            "reached. Save the endpoint in that home first (or point --root at a home that has it)."
        )
        return
    host = _host(url)
    got = seen.get(url)
    if got is None:
        got = seen[url] = _probe(platform, url, key)
    reason = str(got.get("reason") or "")
    models = [str(m) for m in got.get("models") or []]
    if reason in ("unreachable", "timeout", "bad_address", "error"):
        ready.problems.append(
            f"{who}: {place} ({host}) did not answer ({got.get('error') or reason}). "
            "Start it, or fix its address in the benchmark's home."
        )
        return
    if reason == "needs_key":
        ready.problems.append(
            f"{who}: {place} ({host}) asks for a key, and the benchmark's home has none saved for it. "
            "Save the key for that endpoint in the benchmark's home."
        )
        return
    if reason == "refused_key":
        ready.problems.append(
            f"{who}: {place} ({host}) refused the key saved in the benchmark's home. Save the right key there."
        )
        return
    if reason == "server_error":
        ready.problems.append(f"{who}: {place} ({host}) answered with an error when asked for its models.")
        return
    if not models:
        ready.notes.append(
            f"{who}: {place} ({host}) answered but did not list its models, so {model} is not checked."
        )
        return
    if model in models:
        ready.notes.append(f"{who}: {place} ({host}) answered and lists {model}.")
        return
    near = [m for m in models if m.lower() == model.lower()]
    shown = ", ".join(models[:LISTED_IN_WORDS]) + (", …" if len(models) > LISTED_IN_WORDS else "")
    ready.problems.append(
        f"{who}: {model} is not one of the models {place} ({host}) lists"
        + (f" (it lists {near[0]}: the name must match exactly)" if near else f" ({shown})")
        + "."
    )


def _check_claude_model(who: str, model: str, ready: Ready) -> None:
    """A Claude model the CLI will not take: an id Iron Jarvis's catalog (the
    cached picker, else its pinned table) does not know and that is neither
    one of Claude Code's aliases nor a full ``claude-…`` id."""
    try:
        from ..providers import claude_models as C

        _canon, row = C._lookup(model)
        known = row is not None or _canon in C.PINNED
    except Exception:  # noqa: BLE001
        known = False
    low = model.strip().lower()
    if known or low in _CLAUDE_ALIASES:
        return
    if low.startswith("claude-"):
        ready.notes.append(
            f"{who}: {model} is not in Iron Jarvis's list of Claude models; Claude Code decides when "
            "it starts (a model it does not take fails the first task)."
        )
        return
    ready.problems.append(
        f"{who}: Claude Code does not name a model {model!r}. Use one of its aliases (sonnet, opus, "
        "haiku, fable) or a full id such as claude-sonnet-5-5."
    )


def check_models(platform: Any, models: Iterable[tuple[str, str, str]]) -> Ready:
    """Check each ``(who, provider, model)`` — the planner and every member —
    see the module docstring. BLOCKING (cached reads, at most one bounded
    request per model server)."""
    from ..agents import model_team as MT

    ready = Ready()
    manager = getattr(platform, "providers", None)
    seen: dict[str, dict[str, Any]] = {}
    signed: set[str] = set()
    for who, provider, model in models:
        if manager is None or not _known_provider(manager, provider):
            ready.problems.append(
                f"{who}: {provider!r} is not a provider the benchmark's home has (check the spelling: "
                "claude-cli, codex-cli, custom, ollama, or a fleet node's fleet-<id>)."
            )
            continue
        cli = _CLIS.get(provider)
        is_endpoint = provider in _ENDPOINT_SLOTS or provider.startswith("fleet-")
        if is_endpoint:
            _check_endpoint(platform, who, provider, model, ready, seen)
            continue
        state = MT.provider_ready(platform, provider)
        if state is False:
            if cli is not None:
                binary, name = cli
                try:
                    installed = bool(manager._cli_binary_present(binary))
                except Exception:  # noqa: BLE001
                    installed = True
                ready.problems.append(
                    f"{who}: {name} is not installed on this PC." if not installed else
                    f"{who}: {name} is signed out (the last sign-in check said so). Sign in to {name} first."
                )
            else:
                ready.problems.append(f"{who}: {provider} is not connected in the benchmark's home.")
            continue
        if cli is not None and provider not in signed:
            signed.add(provider)
            binary, name = cli
            if _cached_sign_in(binary) is None:
                ready.notes.append(
                    f"whether {name} is signed in is not known yet (Iron Jarvis does not start {name} to "
                    "ask; the first task will tell, as \"did not run\")."
                )
        if provider == "claude-cli":
            _check_claude_model(who, model, ready)
        elif provider == "codex-cli":
            ready.notes.append(f"{who}: Codex checks the model name {model} itself when it starts.")
    return ready


def roles_note(members: Iterable[Any]) -> str:
    """One sentence naming the roles no member holds ("" when every role is
    held): the planner does those pieces itself."""
    from ..agents import model_team as MT

    held = {r for m in members for r in getattr(m, "roles", ()) or ()}
    missing = [r for r in MT.ROLE_IDS if r not in held]
    if not missing:
        return ""
    return ("No member holds the role(s) " + ", ".join(missing)
            + ": the planner does those pieces itself (or asks a member by name).")


def iron_proxy_note(platform: Any) -> str:
    """Said when Iron-Proxy is off in the benchmark's home: every model runs
    on this PC's own sign-in, and the team router has NO usage numbers, so
    its rule that keeps new work off an account at 80% of a window cannot
    apply."""
    svc = getattr(platform, "iron_proxy", None)
    if svc is not None and getattr(svc, "enabled", False):
        return ""
    try:
        from ..harness import build_accounts as BA

        pct = BA.NEW_WORK_MAX_PERCENT
    except Exception:  # noqa: BLE001
        pct = 80
    return (
        "Iron-Proxy is off in the benchmark's home: every model runs on this PC's own sign-in, and the "
        f"team router has no usage numbers, so its rule that keeps new work off an account at {pct:g}% "
        "of a window cannot apply."
    )
