"""OpenCode CLI integration — restricted to models served by hardware you own.

OpenCode can reach three very different things through one command:

* models on YOUR machines (an OpenAI-compatible server on the LAN/Tailscale),
* models on OpenCode's hosted free tier,
* PAID remote models reached *through* one of your own proxies.

That third case is why "is the base URL local?" is not a sufficient test. A
LiteLLM proxy on your own Tailscale IP can expose an alias that simply forwards
to OpenRouter — local-looking address, real money. So a model is treated as
local only when BOTH hold:

1. its OpenCode provider's ``baseURL`` is a non-global host (private, loopback,
   or CGNAT — Tailscale's 100.64.0.0/10 reports ``is_private == False``, so the
   test is ``not is_global``), and
2. if that host is a proxy that publishes its routing table, the specific alias
   resolves to a backend that is itself local — an alias with no local backend
   is a passthrough and is excluded.

Everything else is refused rather than guessed at, because guessing wrong here
spends the user's money.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

log = logging.getLogger(__name__)

#: Hostnames that are always this machine / this network.
_LOCAL_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".ts.net")

_PROBE_TIMEOUT = 4.0


def binary(which: Callable[[str], str | None] = shutil.which) -> str | None:
    """Path to the ``opencode`` executable, or None when it isn't installed."""
    found = which("opencode")
    if found:
        return found
    # npm global installs on Windows are frequently outside a GUI process' PATH.
    for cand in (
        Path.home() / "AppData" / "Roaming" / "npm" / "opencode",
        Path.home() / ".local" / "bin" / "opencode",
        Path.home() / ".opencode" / "bin" / "opencode",
    ):
        if cand.exists():
            return str(cand)
    return None


def installed(which: Callable[[str], str | None] = shutil.which) -> bool:
    return binary(which) is not None


# --- locality ------------------------------------------------------------------


def is_local_url(url: str) -> bool:
    """True when *url*'s host is on this machine or this private network.

    Uses ``not is_global`` rather than ``is_private`` on purpose: Tailscale
    hands out 100.64.0.0/10 (CGNAT), which ``is_private`` reports as False even
    though it is emphatically not the public internet.
    """
    try:
        host = (urlparse(url).hostname or "").strip().rstrip(".").lower()
    except Exception:  # noqa: BLE001 — an unparseable URL is simply not local
        return False
    if not host:
        return False
    if host == "localhost" or host.endswith(_LOCAL_SUFFIXES):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # A SINGLE-LABEL hostname ("spark-049d") cannot be a public internet
        # name — public names carry a TLD. It is a LAN / Tailscale MagicDNS
        # short name, i.e. a machine on this network. This case is not
        # theoretical: the user's proxy reports its backends exactly this way,
        # and treating them as remote hid a genuinely local model.
        return "." not in host
    return not ip.is_global


# --- OpenCode's own config -----------------------------------------------------


def _config_paths() -> list[Path]:
    home = Path.home()
    out = [
        home / ".config" / "opencode" / "opencode.jsonc",
        home / ".config" / "opencode" / "opencode.json",
    ]
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        out += [
            Path(xdg) / "opencode" / "opencode.jsonc",
            Path(xdg) / "opencode" / "opencode.json",
        ]
    return out


def _strip_jsonc(raw: str) -> str:
    """Drop // and /* */ comments so ``json`` can read a .jsonc file."""
    raw = re.sub(r"/\*.*?\*/", "", raw, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", raw)


def config_providers(paths: list[Path] | None = None) -> dict[str, str]:
    """``{provider_id: base_url}`` from OpenCode's config ("" when unset)."""
    out: dict[str, str] = {}
    for path in paths if paths is not None else _config_paths():
        try:
            if not path.is_file():
                continue
            data = json.loads(_strip_jsonc(path.read_text("utf-8")))
        except Exception:  # noqa: BLE001 — a broken config never breaks detection
            continue
        for pid, spec in (data.get("provider") or {}).items():
            if not isinstance(spec, dict):
                continue
            base = str(((spec.get("options") or {}).get("baseURL") or "")).strip()
            out.setdefault(str(pid), base)
    return out


# --- model listing --------------------------------------------------------------


def _run(argv: list[str], timeout: float = 25.0) -> tuple[int, str, str]:
    proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
        argv, capture_output=True, text=True, timeout=timeout,
        encoding="utf-8", errors="replace",
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def list_models(runner: Callable[..., tuple[int, str, str]] | None = None,
                which: Callable[[str], str | None] = shutil.which) -> list[str]:
    """Every ``provider/model`` OpenCode knows about, local or not."""
    exe = binary(which)
    if not exe:
        return []
    try:
        code, out, _err = (runner or _run)([exe, "models"])
    except Exception:  # noqa: BLE001 — detection never raises
        return []
    if code != 0:
        return []
    return [
        ln.strip()
        for ln in out.splitlines()
        if "/" in ln.strip() and not ln.strip().startswith(("#", "-", "$"))
    ]


def _passthrough_aliases(base_url: str, http_get: Callable[..., Any] | None = None) -> set[str]:
    """Aliases a proxy forwards to a REMOTE backend (so: not local).

    Reads a LiteLLM-style ``/model/info``. An entry whose backend ``api_base``
    is missing, or is itself a public host, is a passthrough — the request
    leaves your network even though the proxy in front of it did not.
    Unreadable proxy => empty set, and the caller stays conservative by other
    means (an explicit allowlist).
    """
    root = base_url.rstrip("/")
    for suffix in ("/v1", "/chat/completions"):
        if root.endswith(suffix):
            root = root[: -len(suffix)].rstrip("/")
    out: set[str] = set()
    try:
        if http_get is None:
            import httpx

            def http_get(url: str) -> Any:  # type: ignore[misc]
                return httpx.get(url, timeout=_PROBE_TIMEOUT)

        resp = http_get(f"{root}/model/info")
        data = resp.json() if hasattr(resp, "json") else {}
    except Exception:  # noqa: BLE001 — a proxy that won't answer teaches nothing
        return out
    for row in (data or {}).get("data") or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("model_name") or "").strip()
        if not name:
            continue
        api_base = str((row.get("litellm_params") or {}).get("api_base") or "").strip()
        if not api_base or not is_local_url(api_base):
            out.add(name)
    return out


def local_models(
    *,
    runner: Callable[..., tuple[int, str, str]] | None = None,
    which: Callable[[str], str | None] = shutil.which,
    providers: dict[str, str] | None = None,
    http_get: Callable[..., Any] | None = None,
) -> list[str]:
    """``provider/model`` entries genuinely served by hardware the user owns."""
    provs = providers if providers is not None else config_providers()
    local_provs = {pid: url for pid, url in provs.items() if url and is_local_url(url)}
    if not local_provs:
        return []
    passthrough: dict[str, set[str]] = {}
    for pid, url in local_provs.items():
        passthrough[pid] = _passthrough_aliases(url, http_get)
    out: list[str] = []
    for entry in list_models(runner=runner, which=which):
        pid, _, model = entry.partition("/")
        if pid not in local_provs:
            continue  # hosted/remote provider (e.g. OpenCode's own free tier)
        if model in passthrough.get(pid, set()):
            continue  # local-looking proxy alias that forwards off-network
        out.append(entry)
    return out


def allowed_models(config: Any, **kw: Any) -> list[str]:
    """The models this install may use: an explicit allowlist, else auto-detect.

    ``opencode_local_models`` (CSV) is the user's override and wins outright —
    detection is a convenience, not a cage.
    """
    raw = (getattr(config, "opencode_local_models", "") or "").strip()
    if raw:
        return [m.strip() for m in raw.split(",") if m.strip()]
    return local_models(**kw)
