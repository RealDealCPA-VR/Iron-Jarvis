"""Detect locally-installed CLI-backed inference providers (§5).

Some coding CLIs (Grok, …) ship an authenticated inference session on disk:
installing the CLI and logging in is enough to *use its models* without an API
key. This module discovers those models so Iron Jarvis can offer them as
routable providers — a NEW CLI that installs an inference provider auto-appears
once its registry entry exists.

Design goals:

* **Extensible** — a new CLI provider is one :data:`CLI_PROVIDERS` entry.
* **Fast** — enumeration reads on-disk cache files; it does NOT shell out on the
  hot path (a best-effort binary fallback exists but is opt-in / tolerated).
* **Robust** — a missing binary, missing file, or malformed JSON yields ``[]``
  or a skipped provider. Nothing here ever raises; the daemon calls it at boot.

The Grok strategy was verified live against ``cli-chat-proxy.grok.com``: the
session bearer in ``~/.grok/auth.json`` calls the Responses API directly (see
:mod:`iron_jarvis.providers.adapters.grok_cli`). :func:`grok_session` exposes
that credential to the adapter, re-read fresh each call (the CLI keeps it
refreshed).
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

# --------------------------------------------------------------------------- #
# Public result type
# --------------------------------------------------------------------------- #


@dataclass
class DetectedModel:
    """One model exposed by a locally-installed inference CLI."""

    provider: str  # routing provider id, e.g. "grok-cli"
    model: str  # model id, e.g. "grok-build"
    name: str  # human label, e.g. "Grok Build"
    available: bool  # binary present AND a usable session/backend
    source: str = "cli"  # discovery channel
    base_url: str | None = None  # routing hint for the adapter
    exec_path: str | None = None  # resolved binary path (launch fallback)
    context_window: int | None = None
    detail: str = ""  # human note when unavailable (e.g. "not logged in")

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "name": self.name,
            "available": self.available,
            "source": self.source,
            "base_url": self.base_url,
            "exec_path": self.exec_path,
            "context_window": self.context_window,
            "detail": self.detail,
        }


# --------------------------------------------------------------------------- #
# Grok
# --------------------------------------------------------------------------- #

#: The proxy the Grok CLI session talks to (Responses API). Overridable via the
#: same env var the real CLI honours, so a redirected install still works.
GROK_PROXY_BASE = os.environ.get(
    "GROK_CLI_CHAT_PROXY_BASE_URL", "https://cli-chat-proxy.grok.com/v1"
)
#: Minimum client version the proxy accepts on completion endpoints; used only
#: as a floor when the on-disk version can't be read.
GROK_MIN_VERSION = "0.2.82"


def grok_home() -> Path:
    """Resolve GROK_HOME (the CLI defaults it to ``~/.grok``)."""
    env = os.environ.get("GROK_HOME")
    return Path(env) if env else Path.home() / ".grok"


def _grok_binary() -> str | None:
    """Locate ``grok`` on PATH or in its tool-home bin dir."""
    found = shutil.which("grok")
    if found:
        return found
    bindir = grok_home() / "bin"
    exts = ["", ".exe", ".cmd", ".bat"] if os.name == "nt" else [""]
    for ext in exts:
        cand = bindir / ("grok" + ext)
        if cand.is_file():
            return str(cand)
    return None


def _read_json(path: Path) -> Any:
    """Parse a JSON file, or None on any error (missing / malformed / locked)."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — robustness: never raise from detection
        return None


def _grok_client_version() -> str:
    """The ``x-grok-client-version`` value the proxy requires on completions.

    Reads ``version.json`` then ``models_cache.json`` on disk (the CLI writes
    both); falls back to a known-good floor. Never raises.
    """
    home = grok_home()
    ver = _read_json(home / "version.json")
    if isinstance(ver, dict) and ver.get("version"):
        return str(ver["version"])
    cache = _read_json(home / "models_cache.json")
    if isinstance(cache, dict) and cache.get("grok_version"):
        return str(cache["grok_version"])
    return GROK_MIN_VERSION


def _grok_session_entry(auth: Any) -> dict[str, Any] | None:
    """The active session object inside ``auth.json`` (keyed by issuer::client).

    Returns the first entry carrying a non-empty ``key`` (bearer token).
    """
    if not isinstance(auth, dict):
        return None
    for value in auth.values():
        if isinstance(value, dict) and value.get("key"):
            return value
    return None


def grok_session() -> dict[str, Any] | None:
    """Read the current Grok session credential fresh from ``~/.grok``.

    Returns ``{token, base_url, expires_at, version, email}`` or ``None`` when
    there's no usable session (missing/locked/malformed ``auth.json``, or no
    bearer key). Re-reads every call — the CLI refreshes the token in place, so
    a cached copy would go stale. Never raises.
    """
    entry = _grok_session_entry(_read_json(grok_home() / "auth.json"))
    if entry is None:
        return None
    token = str(entry.get("key") or "")
    if not token:
        return None
    return {
        "token": token,
        "base_url": GROK_PROXY_BASE,
        "expires_at": entry.get("expires_at"),
        "version": _grok_client_version(),
        "email": entry.get("email"),
    }


def _grok_models_from_cache() -> list[dict[str, Any]]:
    """Enumerate models from ``models_cache.json`` (``.models`` map). Fast path."""
    cache = _read_json(grok_home() / "models_cache.json")
    if not isinstance(cache, dict):
        return []
    models = cache.get("models")
    if not isinstance(models, dict):
        return []
    out: list[dict[str, Any]] = []
    for model_id, entry in models.items():
        info = entry.get("info") if isinstance(entry, dict) else None
        info = info if isinstance(info, dict) else {}
        out.append(
            {
                "id": str(info.get("id") or model_id),
                "name": str(info.get("name") or model_id),
                "base_url": info.get("base_url"),
                "context_window": info.get("context_window"),
            }
        )
    return out


def _grok_models_from_binary(exec_path: str) -> list[dict[str, Any]]:
    """Best-effort fallback: parse ``grok models`` stdout. Tolerates failure.

    Only used when the cache is absent but the binary exists. Shelling out is
    slow, so this is deliberately off the hot path.
    """
    try:
        import re
        import subprocess

        proc = subprocess.run(
            [exec_path, "models"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        # Grok model ids look like ``grok-build`` / ``grok-composer-2.5-fast``.
        for token in re.findall(r"grok[a-z0-9.\-]+", proc.stdout or ""):
            mid = token.strip(".-")
            if mid and mid not in seen:
                seen.add(mid)
                out.append({"id": mid, "name": mid, "base_url": None,
                            "context_window": None})
        return out
    except Exception:  # noqa: BLE001 — best-effort, never fatal
        return []


def detect_grok() -> list[DetectedModel]:
    """Detect Grok CLI models. Returns ``[]`` when the binary isn't present."""
    exec_path = _grok_binary()
    if not exec_path:
        return []
    session = grok_session()
    available = session is not None
    detail = "" if available else "not logged in — run `grok login`"

    models = _grok_models_from_cache()
    if not models:  # cache absent but binary here → best-effort probe
        models = _grok_models_from_binary(exec_path)

    out: list[DetectedModel] = []
    for m in models:
        out.append(
            DetectedModel(
                provider="grok-cli",
                model=m["id"],
                name=m["name"],
                available=available,
                source="cli",
                base_url=m.get("base_url") or GROK_PROXY_BASE,
                exec_path=exec_path,
                context_window=m.get("context_window"),
                detail=detail,
            )
        )
    return out


# --------------------------------------------------------------------------- #
# Ollama (bonus generality — best-effort, non-fatal, never hard-depended-on)
# --------------------------------------------------------------------------- #

OLLAMA_API = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")


def detect_ollama() -> list[DetectedModel]:
    """Enumerate local Ollama models if a binary or reachable server exists.

    Kept intentionally light: Ollama is handled more fully elsewhere. If the
    server isn't running we still surface installed models as unavailable (so
    the user sees them), and we never raise if the probe fails.
    """
    exec_path = shutil.which("ollama")
    tags: Any = None
    try:  # short, non-fatal reachability probe
        import httpx

        with httpx.Client(timeout=1.5) as c:
            r = c.get(f"{OLLAMA_API}/api/tags")
            if r.status_code == 200:
                tags = r.json()
    except Exception:  # noqa: BLE001 — server not running is normal
        tags = None

    if not exec_path and tags is None:
        return []  # neither installed nor reachable → nothing to report

    reachable = tags is not None
    out: list[DetectedModel] = []
    models = (tags or {}).get("models") if isinstance(tags, dict) else None
    for m in models or []:
        if not isinstance(m, dict):
            continue
        mid = str(m.get("name") or m.get("model") or "").strip()
        if not mid:
            continue
        out.append(
            DetectedModel(
                provider="ollama",
                model=mid,
                name=mid,
                available=reachable,
                source="cli",
                base_url=f"{OLLAMA_API}/v1",
                exec_path=exec_path,
                detail="" if reachable else "ollama not running",
            )
        )
    return out


# --------------------------------------------------------------------------- #
# Registry + top-level entry point
# --------------------------------------------------------------------------- #


@dataclass
class CliProvider:
    """A known CLI-backed inference provider. Add one to onboard a new CLI."""

    id: str
    name: str
    binaries: list[str]
    home: Callable[[], Path]
    detect: Callable[[], list[DetectedModel]]
    extra: dict[str, Any] = field(default_factory=dict)


#: Registry of CLI-backed providers. A NEW provider = one entry here.
CLI_PROVIDERS: list[CliProvider] = [
    CliProvider(
        id="grok",
        name="Grok CLI",
        binaries=["grok"],
        home=grok_home,
        detect=detect_grok,
    ),
    CliProvider(
        id="ollama",
        name="Ollama",
        binaries=["ollama"],
        home=lambda: Path.home() / ".ollama",
        detect=detect_ollama,
    ),
]


def detect_cli_providers() -> list[DetectedModel]:
    """Enumerate every locally-installed CLI inference provider's models.

    Robust by contract: a failing provider strategy is skipped, never fatal —
    the daemon calls this at boot and must not crash on a half-installed CLI.
    """
    out: list[DetectedModel] = []
    for provider in CLI_PROVIDERS:
        try:
            out.extend(provider.detect())
        except Exception:  # noqa: BLE001 — one bad CLI must not sink the rest
            continue
    return out


def grok_session_expired(session: dict[str, Any] | None) -> bool:
    """True when a Grok session's ``expires_at`` is in the past (or unparseable
    → treated as NOT expired, so a format change doesn't brick a live token)."""
    if not session:
        return False
    raw = session.get("expires_at")
    if not raw:
        return False
    try:
        text = str(raw).replace("Z", "+00:00")
        exp = datetime.fromisoformat(text)
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return exp < datetime.now(timezone.utc)
    except Exception:  # noqa: BLE001 — unknown format → don't block the call
        return False
