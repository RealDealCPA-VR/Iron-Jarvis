"""Iron Jarvis - a local-first AI operating system.

A multi-agent orchestrator on your own machine: a supervisor delegates to
specialist agents that work in sandboxed, review-gated git worktrees, backed by
layered + long-term memory, a fail-closed permission engine, providers/router
(Anthropic / OpenAI / Google / local Ollama / offline mock), scheduler, webhooks,
integrations, comm channels, an encrypted secrets vault, opt-in computer use, and
a Next.js control center.
"""

__version__ = "1.414.6"


def _platform_without_wmi() -> None:
    """v1.414.3: Python's ``platform`` module never asks WMI here.

    On Windows, Python 3.12 answers ``platform.machine()`` / ``uname()`` /
    ``win32_ver()`` by sending two WMI queries over COM, and SQLAlchemy calls
    ``platform.machine()`` while it loads. On the user's PC that query
    sometimes never returned: the query thread stayed stuck from startup and
    COM's own RPC worker threads looped on it, two full CPU cores until the
    app restarted (measured on the installed daemon: about 200% CPU, the
    ``_wmi.pyd`` thread still alive; a quiet daemon's query had finished).
    Without WMI the module falls back to ``sys.getwindowsversion()`` and
    ``PROCESSOR_ARCHITECTURE`` — the same answers on this PC (release, version,
    edition, machine), instantly and with no COM. Never raises."""
    import sys

    if sys.platform != "win32":
        return
    try:
        import platform
    except Exception:  # noqa: BLE001
        return
    if not hasattr(platform, "_wmi_query"):
        return

    def _no_wmi(*_args: object, **_kwargs: object) -> None:
        raise OSError("Iron Jarvis does not ask WMI for platform facts")

    platform._wmi_query = _no_wmi  # type: ignore[attr-defined]


_platform_without_wmi()
