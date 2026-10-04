"""Iron-Proxy routes (v1.301.0): shared subscription accounts on Connections.

The dashboard talks to THESE routes; only the daemon talks to Iron-Proxy. The
Iron-Proxy control token (from ``<data_dir>/proxy.json``) never leaves the
daemon: no response here carries it, and no error sentence can (they are built
from Iron-Proxy's own message/hint, never from request headers).

Shapes:

* ``GET /iron-proxy`` → ``{status, accounts, discovered, providers_used_by_jarvis}``
  (``status`` = ``service.status()``; accounts/discovered ``[]`` and the reason
  in ``status.error`` when Iron-Proxy is off or not running). ``discover_error``
  is added only when the login scan itself failed. ``?discover=0`` (v1.302.0,
  the Build Launch menu's light read) skips the login scan — which makes
  Iron-Proxy run every vendor CLI's own status command — and answers
  ``discovered: []`` with ``discover_skipped: true``; everything else is the
  same.
* ``POST /iron-proxy/enable`` / ``POST /iron-proxy/disable`` → the same body as
  ``GET /iron-proxy`` after starting/stopping (the flag is persisted).
* ``POST /iron-proxy/accounts`` ``{provider, title}`` → 201 account.
* ``POST /iron-proxy/accounts/adopt`` ``{provider, home, title?}`` → 201 account.
* ``PATCH /iron-proxy/accounts/{id}`` ``{enabled?, title?}`` (PARTIAL) → account.
* ``DELETE /iron-proxy/accounts/{id}`` → ``{ok: true}``.
* ``POST /iron-proxy/accounts/reorder`` ``{provider, ids}`` → ``{ok: true}``.
* ``POST /iron-proxy/accounts/{id}/unpark`` → ``{ok: true}``.
* ``POST /iron-proxy/accounts/{id}/signin`` → ``{terminal_id, name}`` — a Build
  pane whose shell runs the account's login command with that account's env.
* ``POST /iron-proxy/accounts/{id}/signout`` → ``{ok: true}``.
* ``POST /iron-proxy/accounts/{id}/open`` ``{cli?}`` → ``{terminal_id, name}``
  (v1.302.0) — a NEW Build pane on that account with its CLI started (claude
  for anthropic, codex for openai, grok for xai); ``/terminals/launch``'s own
  internals.

Every account route answers 409 with ONE plain sentence when Iron-Proxy is off
or not running; an Iron-Proxy error passes its ``hint`` (else its message)
through as ``detail``.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import shutil
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from ...iron_proxy.client import IronProxyError
from ...iron_proxy.service import JARVIS_TO_PROXY

#: Which Iron Jarvis provider runs as each Iron-Proxy provider's accounts.
PROVIDERS_USED_BY_JARVIS = {proxy: ij for ij, proxy in JARVIS_TO_PROXY.items()}

#: cmd.exe metacharacters: an argument carrying any of them (or a space) is
#: quoted, or the shell would act on it.
_CMD_META = set(' \t&|<>^%()"')

#: Host variables Iron-Proxy copies from ITS OWN process into a login command's
#: env (``adapters/cli/runner.ts`` ``baseEnv``), plus the three it forces for
#: headless runs. A sign-in pane keeps the SHELL's values for all of these: the
#: proxy's PATH is not the pane's, and ``CI=1``/``NO_COLOR`` would turn an
#: interactive login non-interactive.
_HOST_ENV = frozenset(
    k.upper()
    for k in (
        "PATH", "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA",
        "TEMP", "TMP", "TMPDIR", "SYSTEMROOT", "COMSPEC", "PATHEXT", "LANG", "LC_ALL",
        "TERM", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "SHELL", "NO_COLOR",
        "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "FORCE_COLOR", "CI",
    )
)

#: API-key variables Iron-Proxy strips from every vendor-CLI spawn
#: (``adapters/cli/specs.ts`` ``COMMON_STRIP``) so the SUBSCRIPTION is used. A
#: sign-in pane strips them too, or the CLI would sign in with a key instead.
_STRIP_ENV = frozenset(
    {
        "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "GEMINI_API_KEY",
        "GOOGLE_API_KEY", "XAI_API_KEY", "GROK_API_KEY",
    }
)

def _said(exc: IronProxyError) -> str:
    """Iron-Proxy's sentence for a route answer, with its CLI / tray-switcher
    instructions ("iron-proxy profiles list", "open the switcher") rewritten
    into what an Iron Jarvis user can do (``accounts.plain_hint``)."""
    text = exc.sentence
    try:
        from ...iron_proxy.accounts import plain_hint

        return plain_hint(text) or text
    except Exception:  # noqa: BLE001 — a rewrite never loses the sentence
        return text


_OFF = "Iron-Proxy is off; turn it on in Connections first."
_NOT_RUNNING = "Iron-Proxy is not running right now."


class IronProxyAccountCreate(BaseModel):
    provider: str
    title: str


class IronProxyAdopt(BaseModel):
    provider: str
    home: str
    title: str | None = None


class IronProxyAccountPatch(BaseModel):
    enabled: bool | None = None
    title: str | None = None


class IronProxyReorder(BaseModel):
    provider: str
    ids: list[str]


class IronProxyOpen(BaseModel):
    #: The catalog CLI to start (v1.302.0); default = the account's own CLI.
    cli: str | None = None


# --------------------------------------------------------------------------- #
# Views
# --------------------------------------------------------------------------- #
def _state_view(state: dict[str, Any] | None) -> dict[str, Any]:
    st = state if isinstance(state, dict) else {}
    out: dict[str, Any] = {"status": str(st.get("status") or "unknown"),
                           "served": int(st.get("served") or 0)}
    if st.get("parkedUntil"):
        out["parkedUntil"] = st["parkedUntil"]
    reason = st.get("parkedReason")
    if isinstance(reason, dict) and reason.get("kind"):
        out["parkedReason"] = {"kind": reason["kind"]}
        if reason.get("message"):
            out["parkedReason"]["message"] = reason["message"]
    if st.get("lastUsedAt"):
        out["lastUsedAt"] = st["lastUsedAt"]
    return out


def _usage_view(report: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(report, dict):
        return None
    windows = report.get("windows") if isinstance(report.get("windows"), dict) else {}

    def _req(w: str) -> int:
        win = windows.get(w)
        return int(win.get("requests") or 0) if isinstance(win, dict) else 0

    out: dict[str, Any] = {
        "requests_5h": _req("5h"),
        "requests_7d": _req("7d"),
        "parks_7d": int(report.get("parks7d") or 0),
    }
    est = report.get("estimate")
    if isinstance(est, dict) and est.get("minutesLeft") is not None:
        out["minutes_left"] = est["minutesLeft"]
    return out


def _account_view(
    profile: dict[str, Any],
    state: dict[str, Any] | None = None,
    usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cli = profile.get("cli") if isinstance(profile.get("cli"), dict) else {}
    out: dict[str, Any] = {
        "id": profile.get("id"),
        "title": profile.get("title"),
        "provider": profile.get("provider"),
        "lane": profile.get("lane"),
        "order": profile.get("order"),
        "enabled": bool(profile.get("enabled", True)),
        "state": _state_view(state),
        "home": cli.get("home"),
        "home_adopted": bool(cli.get("adopted")),
    }
    u = _usage_view(usage)
    if u is not None:
        out["usage"] = u
    return out


def _discovered_view(items: Any, profiles: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """This PC's existing CLI logins. ``adopted_profile_id`` is Iron-Proxy's
    ``adoptedProfileId`` when it reports one, else the id of an account whose
    CLI home IS that login's home (so the card never offers "Use this PC's
    login" twice)."""
    by_home: dict[tuple[str, str], str] = {}
    for p in profiles or []:
        cli = p.get("cli") if isinstance(p, dict) and isinstance(p.get("cli"), dict) else {}
        if cli.get("home") and p.get("id"):
            by_home[(str(p.get("provider")), _home_key(cli["home"]))] = str(p["id"])
    out: list[dict[str, Any]] = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        adopted = it.get("adoptedProfileId") or by_home.get(
            (str(it.get("provider")), _home_key(it.get("home") or ""))
        )
        row: dict[str, Any] = {
            "provider": it.get("provider"),
            "home": it.get("home"),
            "signed_in": it.get("status") == "ok",
            "adopted_profile_id": adopted or None,
        }
        if it.get("suggestedTitle"):
            row["title"] = it["suggestedTitle"]
        out.append(row)
    return out


def _home_key(home: str) -> str:
    """Compare CLI homes the way the filesystem does (case-insensitive and
    separator-agnostic on Windows)."""
    key = os.path.normpath(str(home)) if home else ""
    return key.lower() if os.name == "nt" else key


def _sorted_accounts(accounts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(accounts, key=lambda a: (str(a.get("provider")), a.get("order") or 0))


# --------------------------------------------------------------------------- #
# Sign-in pane helpers
# --------------------------------------------------------------------------- #
def _signin_env(login_env: dict[str, Any], base: dict[str, str] | None = None) -> dict[str, str]:
    """The pane's environment: the SHELL's base (what the backend would have
    used — CLAUDE.md ``_with_pane_env``), minus the API-key variables, plus the
    account's own variables from the login command (``CLAUDE_CONFIG_DIR``,
    ``CODEX_HOME``, ``GROK_HOME``, its ``cli.env``) — never the proxy's host
    variables."""
    env = dict(os.environ if base is None else base)
    for key in list(env):
        if key.upper() in _STRIP_ENV:
            env.pop(key, None)
    for key, value in (login_env or {}).items():
        if not isinstance(key, str) or not isinstance(value, str):
            continue
        if key.upper() in _HOST_ENV or key.upper() in _STRIP_ENV:
            continue
        env[key] = value
    return env


def _command_line(shell: str, binary: str, args: list[str], env: dict[str, str]) -> str:
    """The login command as a line for THIS pane's shell. The binary is
    resolved against the pane's PATH when it can be, so the line does not
    depend on the shell's own lookup."""
    path = env.get("PATH", env.get("Path"))
    resolved = shutil.which(binary, path=path) if path is not None else shutil.which(binary)
    exe = resolved or binary
    return shell_line(shell, [exe, *[str(a) for a in args]])


def shell_line(shell: str, argv: list[str]) -> str:
    """``argv`` as ONE line typed into a pane running ``shell``, every token
    quoted for that shell: PowerShell (``& 'a' 'b'``, ``'`` doubled), cmd
    (``"…"`` around a token with a space or a metacharacter, ``"`` doubled) and
    POSIX (``shlex``). The sign-in pane's quoting, shared (v1.303.2: the
    Build pane's "start fresh with a handoff" types its ``claude`` line with it)."""
    argv = [str(a) for a in argv]
    name = (shell or "").lower()
    if name in ("pwsh", "powershell") or name.endswith(("pwsh.exe", "powershell.exe")):
        return "& " + " ".join("'" + a.replace("'", "''") + "'" for a in argv)
    if name == "cmd" or name.endswith("cmd.exe"):
        return " ".join(
            '"' + a.replace('"', '""') + '"' if (not a or _CMD_META & set(a)) else a
            for a in argv
        )
    return shlex.join(argv)


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
def register(app: FastAPI, d) -> None:
    """Attach the Iron-Proxy routes; ``d`` is the create_app deps object."""

    def _svc():
        svc = getattr(d.platform, "iron_proxy", None)
        if svc is None:
            raise HTTPException(status_code=404, detail="Iron-Proxy is not part of this build.")
        return svc

    async def _client(svc):
        if not svc.enabled:
            raise HTTPException(status_code=409, detail=_OFF)
        client = await asyncio.to_thread(svc.client)
        if client is None:
            err = svc.status().get("error")
            raise HTTPException(status_code=409, detail=err or _NOT_RUNNING)
        return client

    def _fail(exc: IronProxyError) -> HTTPException:
        """An Iron-Proxy error as an HTTP error the dashboard can show. A 401
        from Iron-Proxy is OUR token being refused, not the user's: it must
        never reach the dashboard as a 401 (that reads as the daemon's own
        sign-in failing)."""
        if exc.code == "UNREACHABLE":
            return HTTPException(status_code=409, detail=_NOT_RUNNING)
        status = exc.status or 502
        if status == 401:
            if exc.details.get("profileId"):
                return HTTPException(status_code=409, detail=_said(exc))
            # The client already re-read proxy.json once (a restarted proxy).
            return HTTPException(
                status_code=409,
                detail="Iron-Proxy refused Iron Jarvis's access token; "
                "turn Iron-Proxy off and on again.",
            )
        if status < 400 or status >= 500:
            status = 502
        return HTTPException(status_code=status, detail=_said(exc))

    async def _call(fn, *args, **kwargs):
        try:
            return await asyncio.to_thread(fn, *args, **kwargs)
        except IronProxyError as exc:
            raise _fail(exc) from None

    async def _changed(svc) -> None:
        """An account changed: refresh the snapshot provider availability
        reads, so a just-added (or removed) account counts at once."""
        await asyncio.to_thread(svc.refresh_accounts)

    async def _view(svc, discover: bool = True) -> dict[str, Any]:
        body: dict[str, Any] = {
            "status": svc.status(),
            "accounts": [],
            "discovered": [],
            "providers_used_by_jarvis": dict(PROVIDERS_USED_BY_JARVIS),
        }
        if not svc.enabled:
            return body
        running = await asyncio.to_thread(svc.check)
        client = await asyncio.to_thread(svc.client) if running else None
        if client is None:
            body["status"] = svc.status()
            return body
        async def _no_scan() -> list[Any]:
            return []

        profiles, states, usage, discovered = await asyncio.gather(
            asyncio.to_thread(client.profiles),
            asyncio.to_thread(client.states),
            asyncio.to_thread(client.usage),
            asyncio.to_thread(client.discover, 20.0) if discover else _no_scan(),
            return_exceptions=True,
        )
        for got in (profiles, states):
            if isinstance(got, BaseException):
                if isinstance(got, IronProxyError) and got.code == "UNREACHABLE":
                    await asyncio.to_thread(svc.check)
                st = svc.status()
                if st["running"] and not st.get("error"):
                    st["error"] = (
                        _said(got) if isinstance(got, IronProxyError)
                        else "Iron-Proxy could not list its accounts."
                    )
                body["status"] = st
                return body
        # The SAME read refreshes the snapshot provider availability reads.
        svc.note_accounts(profiles, states)
        states_map = states if isinstance(states, dict) else {}
        usage_map: dict[str, Any] = {}
        if isinstance(usage, list):
            usage_map = {u.get("profileId"): u for u in usage if isinstance(u, dict)}
        body["accounts"] = _sorted_accounts(
            [
                _account_view(p, states_map.get(p.get("id")), usage_map.get(p.get("id")))
                for p in (profiles if isinstance(profiles, list) else [])
                if isinstance(p, dict)
            ]
        )
        if isinstance(discovered, BaseException):
            body["discover_error"] = (
                _said(discovered) if isinstance(discovered, IronProxyError)
                else "Iron-Proxy could not look for existing logins."
            )
        else:
            body["discovered"] = _discovered_view(
                discovered, [p for p in profiles if isinstance(p, dict)]
            )
        if not discover:
            body["discover_skipped"] = True
        body["status"] = svc.status()
        return body

    @app.get("/iron-proxy")
    async def iron_proxy_view(discover: bool = True) -> dict[str, Any]:
        """Iron-Proxy on Connections: ``status`` (enabled/running/owned/url/
        version/error/bundled — never the token), ``accounts`` (grouped by
        provider in failover order, each with its state chip data and usage),
        ``discovered`` (this PC's existing CLI logins not yet an account) and
        ``providers_used_by_jarvis`` (which Iron Jarvis provider runs as each
        Iron-Proxy provider's accounts). ``?discover=0``: no login scan
        (``discovered: []``, ``discover_skipped: true``) — the light read."""
        return await _view(_svc(), discover=discover)

    @app.post("/iron-proxy/enable")
    async def iron_proxy_enable() -> dict[str, Any]:
        """Turn Iron-Proxy on: persist the switch, then reuse a running
        Iron-Proxy or start the bundled one. Answers like ``GET /iron-proxy``;
        a start that failed is ``status.error`` in one sentence."""
        svc = _svc()
        await asyncio.to_thread(svc.set_enabled, True)
        await asyncio.to_thread(svc.start)
        return await _view(svc)

    @app.post("/iron-proxy/disable")
    async def iron_proxy_disable() -> dict[str, Any]:
        """Turn Iron-Proxy off: persist the switch and stop the Iron-Proxy that
        Iron Jarvis started (one the user started keeps running). CLI calls go
        back to the default login."""
        svc = _svc()
        await asyncio.to_thread(svc.set_enabled, False)
        await asyncio.to_thread(svc.stop)
        return await _view(svc)

    @app.post("/iron-proxy/accounts", status_code=201)
    async def iron_proxy_add_account(body: IronProxyAccountCreate) -> dict[str, Any]:
        """Add a subscription (CLI-lane) account; sign it in next."""
        svc = _svc()
        client = await _client(svc)
        prof = await _call(client.create_profile, body.provider, body.title, "cli")
        await _changed(svc)
        return _account_view(prof)

    @app.post("/iron-proxy/accounts/adopt", status_code=201)
    async def iron_proxy_adopt(body: IronProxyAdopt) -> dict[str, Any]:
        """Use a login this PC already has (a vendor CLI's own home) as an account."""
        svc = _svc()
        client = await _client(svc)
        prof = await _call(client.adopt, body.provider, body.home, body.title or None)
        await _changed(svc)
        return _account_view(prof)

    @app.post("/iron-proxy/accounts/reorder")
    async def iron_proxy_reorder(body: IronProxyReorder) -> dict[str, Any]:
        """Set one provider's failover order (first id runs first)."""
        svc = _svc()
        client = await _client(svc)
        await _call(client.reorder, body.provider, body.ids)
        await _changed(svc)
        return {"ok": True}

    @app.patch("/iron-proxy/accounts/{account_id}")
    async def iron_proxy_patch_account(
        account_id: str, body: IronProxyAccountPatch
    ) -> dict[str, Any]:
        """PARTIAL update: only the fields sent change (``enabled``, ``title``)."""
        patch = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
        if not patch:
            raise HTTPException(status_code=400, detail="Send enabled or title to change.")
        if "title" in patch and not str(patch["title"]).strip():
            raise HTTPException(status_code=400, detail="An account needs a title.")
        svc = _svc()
        client = await _client(svc)
        prof = await _call(client.update_profile, account_id, **patch)
        await _changed(svc)
        return _account_view(prof)

    @app.delete("/iron-proxy/accounts/{account_id}")
    async def iron_proxy_delete_account(account_id: str) -> dict[str, Any]:
        """Remove an account from Iron-Proxy."""
        svc = _svc()
        client = await _client(svc)
        await _call(client.delete_profile, account_id)
        await _changed(svc)
        return {"ok": True}

    @app.post("/iron-proxy/accounts/{account_id}/unpark")
    async def iron_proxy_unpark(account_id: str) -> dict[str, Any]:
        """Make a parked account usable again now."""
        svc = _svc()
        client = await _client(svc)
        await _call(client.unpark, account_id)
        await _changed(svc)
        return {"ok": True}

    @app.post("/iron-proxy/accounts/{account_id}/signout")
    async def iron_proxy_signout(account_id: str) -> dict[str, Any]:
        """Sign an account out (its vendor CLI's own logout)."""
        svc = _svc()
        client = await _client(svc)
        await _call(client.logout, account_id)
        await _changed(svc)
        return {"ok": True}

    @app.post("/iron-proxy/accounts/{account_id}/signin")
    async def iron_proxy_signin(account_id: str) -> dict[str, Any]:
        """Open a Build pane named ``Sign in: <title>`` whose shell runs the
        account's login command AS that account (its home variable set, API-key
        variables cleared). Answers ``{terminal_id, name}``; the dashboard opens
        Build focused on it."""
        svc = _svc()
        client = await _client(svc)
        profiles = await _call(client.profiles)
        prof = next(
            (p for p in (profiles or []) if isinstance(p, dict) and p.get("id") == account_id),
            None,
        )
        if prof is None:
            raise HTTPException(status_code=404, detail="Iron-Proxy has no account with that id.")
        if prof.get("lane") != "cli":
            raise HTTPException(
                status_code=400,
                detail="Only a subscription (CLI) account signs in from a terminal; "
                "API-key accounts are managed in Iron-Proxy.",
            )
        cmd = await _call(client.login_command, account_id)
        binary = str((cmd or {}).get("binary") or "").strip()
        if not binary:
            raise HTTPException(status_code=502, detail="Iron-Proxy gave no login command.")
        args = [str(a) for a in ((cmd or {}).get("args") or [])]
        env = _signin_env((cmd or {}).get("env") or {})
        name = f"Sign in: {prof.get('title') or account_id}"
        try:
            session = await asyncio.to_thread(
                d.platform.terminals.create, None, None, 100, 30, env=env, name=name
            )
        except RuntimeError as exc:  # the session cap
            raise HTTPException(status_code=429, detail=str(exc)) from None
        # Nobody is attached until the dashboard opens Build: keep the output
        # (the sign-in URL/code) in the pane's tail so the attach replays it.
        session.start_autodrain()
        line = _command_line(getattr(session, "shell", ""), binary, args, env)
        await asyncio.to_thread(session.write, line + "\r")
        return {"terminal_id": session.id, "name": session.pane_name or name}

    @app.post("/iron-proxy/accounts/{account_id}/open")
    async def iron_proxy_open(
        account_id: str, body: IronProxyOpen | None = None
    ) -> dict[str, Any]:
        """Open a NEW Build pane on this account with its CLI started in it
        (v1.302.0) — the card's "Open in Build". Answers ``{terminal_id,
        name}``; the dashboard opens Build focused on it. An account that
        cannot be used right now is a 409 sentence and no pane."""
        from ...terminals.pane_accounts import CLI_PROVIDER, PROVIDER_CLI
        from ..schemas import TerminalLaunch
        from .terminals import launch_pane

        svc = _svc()
        client = await _client(svc)
        profiles = await _call(client.profiles)
        prof = next(
            (p for p in (profiles or []) if isinstance(p, dict) and p.get("id") == account_id),
            None,
        )
        if prof is None:
            raise HTTPException(status_code=404, detail="Iron-Proxy has no account with that id.")
        if prof.get("lane") != "cli":
            raise HTTPException(
                status_code=400,
                detail="Only a subscription (CLI) account opens in Build; "
                "API-key accounts are managed in Iron-Proxy.",
            )
        provider = str(prof.get("provider") or "")
        cli = ((body.cli if body is not None else None) or "").strip().lower()
        cli = cli or PROVIDER_CLI.get(provider, "")
        if not cli:
            raise HTTPException(
                status_code=400, detail=f"Build has no CLI for {provider} accounts yet."
            )
        if CLI_PROVIDER.get(cli) != provider:
            raise HTTPException(
                status_code=400,
                detail=f'"{cli}" does not run as a {provider} account.',
            )
        session = await asyncio.to_thread(
            launch_pane, d.platform, TerminalLaunch(cli=cli, account=account_id)
        )
        return {"terminal_id": session.id, "name": session.pane_name}
