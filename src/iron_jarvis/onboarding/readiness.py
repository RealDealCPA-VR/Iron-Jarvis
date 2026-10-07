"""First-run detection + the combined readiness report.

``is_first_run`` answers "is this a brand-new install?" so the dashboard can show
a welcome overlay. ``readiness`` bundles the machine diagnostic (:func:`doctor`)
and the getting-started checklist into one payload the daemon/CLI can render.
"""

from __future__ import annotations

from .checklist import (
    _has_any,
    _provider_connected,
    default_is_mock,
    getting_started,
    provider_label,
    voice_backend_present,
)
from .doctor import CHECKS, REQUIRED, RECOMMENDED, _result

#: v1.310.0: the developer-toolchain rows the END-USER card never shows. A
#: packaged install runs without uv / git / Node / pnpm (and ships its own
#: Python), so on a non-developer PC the Overview's first card used to list
#: four amber "not found" rows and hand the user an ``irm ... | iex`` to paste
#: into PowerShell. They stay in ``doctor.CHECKS`` -- the ``ironjarvis doctor``
#: CLI and source installs legitimately want them; only /onboarding drops them.
DEV_TOOLCHAIN_CHECKS = frozenset({"python", "uv", "git", "node", "pnpm"})

#: v1.310.0: the plain words for each remaining row, shown beside the mono
#: name ("Reading old .doc and .xls files", not "old Office files" / a
#: snake_case id). An unknown future row falls back to its name, de-underscored.
CHECK_LABELS: dict[str, str] = {
    "browser": "A web browser for browsing tasks",
    "pdf scan routing": "Spotting scanned pages inside PDFs",
    "local_ocr": "Reading scanned pages on this PC",
    "old Office files": "Reading old .doc and .xls files",
    "guide_docs": "The built-in help guides",
    "browser_addon": "The browser add-on",
}

#: The providers the ONE "use this for answers" press accepts (W2-1): the two
#: subscription CLIs, the two local endpoints and the API providers. Never
#: mock, and never a browser-vault row (it is not an inference provider).
USE_MODEL_LOCAL = ("ollama", "custom")
USE_MODEL_CLIS = ("claude-cli", "codex-cli")


def _check_label(name: str) -> str:
    return CHECK_LABELS.get(name) or name.replace("_", " ").strip().capitalize()


def end_user_checks() -> dict:
    """The machine checks a NON-developer should see (v1.310.0, W2-2).

    Runs only the non-toolchain checks (the dev rows are never even probed
    here), with the same never-raise contract as :func:`doctor`. Every row
    keeps ``name``/``ok``/``detail``/``fix``/``level`` and gains a plain
    ``label``. Deliberately NOT ``doctor(platform)``: the runtime checks run a
    full ``PRAGMA integrity_check`` plus endpoint probes, and /onboarding is
    fetched on every Overview load.
    """
    checks: list[dict] = []
    for fn in CHECKS:
        name = getattr(fn, "__name__", "check").replace("check_", "")
        if name in DEV_TOOLCHAIN_CHECKS:
            continue
        try:
            row = fn()
        except Exception as exc:  # noqa: BLE001 — diagnostics must never crash
            row = _result(
                name,
                False,
                f"check '{name}' failed to run: {exc}",
                fix="This is a bug in the doctor check; please report it.",
                level=RECOMMENDED,
            )
        if row.get("name") in DEV_TOOLCHAIN_CHECKS:
            continue
        row["label"] = _check_label(str(row.get("name") or name))
        checks.append(row)
    ok = all(c["ok"] for c in checks if c.get("level") == REQUIRED)
    return {"ok": ok, "checks": checks}


def model_choice(platform) -> dict:
    """Which model answers right now, and which real ones could (W2-2).

    Shape::

        {"default_provider": str, "default_model": str, "is_mock": bool,
         "usable": [{"provider": str, "label": str, "local": bool}, ...]}

    CHEAP by contract: it reads ``providers.health()`` -- the same cached
    availability /health serves on every poll -- and never lists a local
    endpoint's models, probes the network or runs an integrity check.
    ``usable`` holds only what ``POST /onboarding/use-model`` accepts. An API
    row served THROUGH a signed-in CLI (``inherited_from``) is left out: it
    is the same account as the CLI row already listed, and two doors for one
    login read like two different things.
    """
    cfg = platform.config
    usable: list[dict] = []
    try:
        from ..providers.manager import API_PROVIDERS

        rows = {r.get("provider"): r for r in platform.providers.health()}
        order = list(USE_MODEL_CLIS) + list(USE_MODEL_LOCAL) + list(API_PROVIDERS)
        for name in order:
            row = rows.get(name)
            if not row or not row.get("available") or row.get("inherited_from"):
                continue
            usable.append(
                {
                    "provider": name,
                    "label": provider_label(platform, name),
                    "local": name in USE_MODEL_LOCAL,
                }
            )
    except Exception:  # noqa: BLE001 — the Overview never breaks on health
        usable = []
    return {
        "default_provider": str(getattr(cfg, "default_provider", "") or ""),
        "default_model": str(getattr(cfg, "default_model", "") or ""),
        "is_mock": default_is_mock(platform),
        "usable": usable,
    }


def is_first_run(platform) -> bool:
    """True for a brand-new install: no sessions, no chat threads, AND no real
    provider connected.

    A fresh checkout with only the offline mock model and no history is a first
    run; running any session, chatting, or wiring a real model flips it to
    False. Chatting IS using the app (one-surface thesis), so a chat thread
    ends first-run just like an agent session does.
    """
    from ..core.models import ChatThreadRecord, Session

    has_history = _has_any(platform.engine, Session) or _has_any(
        platform.engine, ChatThreadRecord
    )
    return not has_history and not _provider_connected(platform)


def readiness(platform) -> dict:
    """One payload combining diagnostics, the checklist, version, and first-run.

    Shape::

        {
          "version": str,
          "first_run": bool,
          "doctor": {ok, checks},          # end-user rows only (v1.310.0)
          "model": {default_provider, default_model, is_mock, usable},
          "checklist": [ {key, title, detail, done, action, optional}, ... ],
          "next_step": {step dict} | None,   # first incomplete REQUIRED step
          "voice": {"available": bool, "backend": str | None},
        }

    ``next_step`` skips OPTIONAL steps (e.g. ``set_up_voice``) so voice never
    becomes the nudged "do this next" — it stays a pure opt-in.
    """
    from .. import __version__

    # v1.310.0: the END-USER rows (no developer toolchain), each with a plain
    # label -- the full machine report stays on GET /doctor and the CLI.
    diagnostic = end_user_checks()
    steps = getting_started(platform)
    # Only REQUIRED (non-optional) incomplete steps can be the next step: the
    # optional voice item must never be advertised as "do this next".
    next_step = next(
        (s for s in steps if not s["done"] and not s.get("optional")), None
    )
    voice_available, voice_backend = voice_backend_present(platform)
    return {
        "version": __version__,
        "first_run": is_first_run(platform),
        "doctor": diagnostic,
        "model": model_choice(platform),
        "checklist": steps,
        "next_step": next_step,
        "voice": {"available": voice_available, "backend": voice_backend},
    }
