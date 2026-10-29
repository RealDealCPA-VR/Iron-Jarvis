"""Manager that owns every live terminal session for the dashboard."""

from __future__ import annotations

import base64
import json
import os
import logging
import re
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ..core.ids import new_id
from .backend import PipeBackend, PtyBackend
from .modes import restore_reset
from .session import TerminalSession, normalise_pane_capabilities
from .shells import resolve_shell

log = logging.getLogger(__name__)

#: Cap on how much per-session scrollback is persisted (bytes). Enough to show
#: meaningful history after a restart without bloating the snapshot file.
_SNAPSHOT_SCROLLBACK = 64 * 1024

#: Default cap on concurrent live sessions to prevent runaway shell spawning.
MAX_SESSIONS = 20

#: How many recently-killed (dead) sessions to retain for queryability before
#: evicting them, so a long-lived daemon's ``_sessions`` dict stays bounded.
MAX_DEAD_RETAINED = 10

#: v1.302.0: how long a boot's rehydrate may spend, IN TOTAL across every
#: pane, re-resolving accounts recorded by a snapshot that predates recorded
#: homes. A snapshot with homes never asks Iron-Proxy at all.
RESTORE_ACCOUNTS_BUDGET_S = 2.0

#: v1.406.0 (part 6): the LONGEST a boot waits for its panes to come back.
#: Measured on the user's PC: a normal restore of two or three panes takes
#: 0.85-3.3 s, and on 5 of 11 first starts after an update (2026-10-27/28)
#: every pane's ``CreateProcessW`` (the WindowsApps ``pwsh.EXE`` alias under
#: ConPTY) never returned — the lifespan waited on it until the desktop killed
#: the daemon at 90 s. Past this bound the boot goes on; a pane still starting
#: is registered the moment its shell starts (``_finish_late_restore``), stays
#: in the snapshot meanwhile, and ``restoring_ids`` lists it so the Build page
#: does not prune its saved keys.
RESTORE_WAIT_S = 8.0

#: A PTY shell that's going to die (e.g. a frozen build whose ConPTY has no
#: OpenConsole.exe host) dies within a fraction of a second; give it this long
#: to reveal itself before trusting it. Only paid ONCE per daemon run (the
#: result is cached in ``_pty_ok``), so steady-state creates have no extra cost.
_PTY_VERIFY_SECONDS = 0.7


#: Credentials the daemon holds that a pane's child must NOT inherit (v1.238.0).
#:
#: Both are spelled as literals rather than imported: this module is on the
#: daemon's import path and ``browser.panetokens`` is a deferred, heavy import
#: everywhere else in this file. ``test_mcp_auth_real_app_v1238`` pins each one
#: equal to its defining constant (``panetokens.INSTALL_TOKEN_ENV`` /
#: ``MCP_TOKEN_ENV``), because drift here does not raise — it silently stops the
#: strip from ever matching.
#:
#: ``IRONJARVIS_TOKEN`` is the INSTALL BEARER: the credential that unlocks the
#: whole RCE-by-design daemon (``/chat``, ``/terminals``, ``/documents/write``,
#: ``/settings``). Every pane's child used to inherit it verbatim from
#: ``os.environ``, right beside the pane-scoped ``IRONJARVIS_MCP_TOKEN`` — so a
#: harness that read its own environment had full install authority and the
#: capability checklist, the ``/mcp`` 403s and the deny floor were decorative
#: against precisely the actor they exist to contain.
#:
#: ``IRONJARVIS_MCP_TOKEN`` is stripped for a different reason: it is ANOTHER
#: PANE'S token. In development the daemon is often started from a shell inside
#: Build, so ``os.environ`` can carry a live pane credential; inheriting it would
#: give a pane with no capabilities the grant of the pane the daemon was launched
#: from. ``_prepare_recipe`` puts this pane's own token back in ``pane_env``,
#: which is merged AFTER the strip, so a recipe pane is unaffected.
#:
#: WHY EVERY PANE AND NOT ONLY RECIPE PANES. Stripping only for ``recipe=``
#: panes would put the boundary on the wrong noun. ``recipe`` records HOW a pane
#: was configured, not who ends up running in it: the Launch menu's own flow
#: types ``claude`` into an ordinary pane, and a user can start any harness in
#: any pane at any time by typing its name. Since the strip costs a plain shell
#: nothing it should not already be without (this credential belongs to the
#: daemon, not to the user's shell), the safe default is every pane.
#:
#: WHAT THIS COSTS, honestly. ``daemon/client.py`` documents that an in-pane
#: ``ironjarvis`` CLI inherits ``IRONJARVIS_TOKEN``. On the PACKAGED install —
#: the shipped product — it keeps working unchanged: ``daemon_token()`` falls
#: back to ``%APPDATA%/Iron Jarvis/token.txt``, which the pane can still read.
#: In a dev shell that exported the variable, an in-pane ``ironjarvis`` command
#: now needs ``--token`` (and fails LOUDLY, since v1.187's client raises on 401,
#: rather than silently doing nothing).
#:
#: AND BE HONEST ABOUT THE LIMIT: because ``token.txt`` is a readable file, this
#: is defence in depth — it removes the trivial hand-off, not the possibility.
#: A pane's process runs as the user and can read the user's files; a real
#: boundary would need the daemon's credential kept somewhere the pane cannot
#: reach. Do not describe the capability scope as a sandbox anywhere.
#:
#: NOT stripped: ``ANTHROPIC_API_KEY`` / ``PIXIO_API_KEY``, the only other
#: secrets this daemon reads from the environment. Those are the USER's own
#: keys, present in the user's own shell, and a Build pane is meant to be that
#: shell — removing them would break the user's own tooling for no gain, since
#: they were never handed to the pane BY Iron Jarvis. The secrets vault's Fernet
#: key is a file and never appears in the environment at all.
_DAEMON_ONLY_ENV = ("IRONJARVIS_TOKEN", "IRONJARVIS_MCP_TOKEN")

from ..core.claude_markers import drop_claude_session_markers  # noqa: E402


def _with_pane_env(
    env: dict | None, pane_env: dict[str, str], accounts: Any | None = None
) -> dict:
    """`env` plus the pane's identity, minus the daemon's own credentials.

    The backends REPLACE the child environment when handed one
    (`dict(env) if env is not None else os.environ.copy()`), so returning just
    the pane vars would spawn a shell with no PATH. Copying whichever base the
    backend would have used keeps behaviour identical for every existing
    caller and adds the pane's variables on top.

    The strip happens BEFORE the merge (see :data:`_DAEMON_ONLY_ENV`), so a
    recipe's freshly minted `IRONJARVIS_MCP_TOKEN` — which arrives in
    `pane_env` — still reaches the child, while an inherited one never does.
    It applies to a caller-supplied `env` too: the credential is the same
    credential whichever dict it travelled in. Since v1.356.0 (G60) one caller
    passes one deliberately: `routes/terminals.launch_pane` hands an app
    sign-in pane the daemon's environment plus the card's settings (Microsoft
    365's tool filter and account kind) — the strip still applies to it.

    `accounts` (v1.302.0, a `pane_accounts.PaneAccounts`) is applied to that
    same base BEFORE the pane's identity: the account's home variable set,
    Iron-Proxy's unset list removed, an inherited home removed for "this PC's
    login" — and nothing an account says can touch the `IRONJARVIS_*` identity.
    None (every caller before v1.302.0) changes nothing.
    """
    base = dict(env) if env is not None else os.environ.copy()
    for name in _DAEMON_ONLY_ENV:
        base.pop(name, None)
    drop_claude_session_markers(base)
    if accounts:
        accounts.apply(base)
    base.update(pane_env)
    return base


def _capabilities_or_none(raw: Any) -> dict[str, bool] | None:
    """The five canonical capabilities, or ``None`` when nothing was supplied.

    ``None`` is kept distinct from all-``False`` on PURPOSE at the persistence
    boundary: a snapshot written before this field existed has no key at all, and
    restoring it as ``None`` says "this pane was never configured" rather than
    "someone unticked every box". Both read as no capabilities everywhere else.
    """
    return normalise_pane_capabilities(raw) if isinstance(raw, Mapping) else None


#: v1.333.0: a pane id that can name its scrollback file. Every id this app
#: mints matches; anything else (a hand-edited snapshot) keeps its scrollback
#: inline in the index, as every pane did before.
_FILE_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


def _scrollback_dir(state_path: Path) -> Path:
    """Where each pane's scrollback file lives: ``terminals-scrollback/``
    beside ``terminals.json``."""
    return state_path.with_name(state_path.stem + "-scrollback")


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def _stop_background_of(sessions: "list[TerminalSession]") -> None:
    """v1.333.0 (review): a pane whose shell ended ON ITS OWN (``exit``) and
    that nobody closed is dropped by ``purge_dead`` without a ``kill()`` —
    so its job was never swept and its hidden background processes (a
    ``Start-Process`` dev server) ran on with no pane left to stop them, and
    the job handle leaked. Sweep it as a close would, on its own thread:
    ``purge_dead`` runs under the manager's lock, often on a request."""
    sweeps = [
        getattr(s.backend, "stop_background", None) for s in sessions
    ]
    sweeps = [fn for fn in sweeps if callable(fn)]
    if not sweeps:
        return

    def _run() -> None:
        for fn in sweeps:
            try:
                fn()
            except Exception:  # noqa: BLE001 — a sweep never breaks the manager
                log.debug("dropped pane sweep failed", exc_info=True)

    threading.Thread(target=_run, name="pane-sweep", daemon=True).start()


_CONTINUED_KEYS = ("from_id", "from_title", "session_id", "carried_path", "at")


def _continued_from_or_none(raw: Any) -> dict[str, Any] | None:
    """A snapshot's ``continued_from`` (v1.303.2): its five keys, each a
    string or None, or None for anything else (an older snapshot has none)."""
    if not isinstance(raw, Mapping) or not raw.get("session_id"):
        return None
    return {k: (raw.get(k) if isinstance(raw.get(k), str) else None) for k in _CONTINUED_KEYS}


def _account_records(accounts: Any) -> dict[str, dict[str, Any]] | None:
    """What a pane records about its accounts (`PaneAccounts.records`), or None
    for a pane that has none (every pane before v1.302.0)."""
    records = getattr(accounts, "records", None) if accounts is not None else None
    if not records:
        return None
    return {str(p): dict(r) for p, r in records.items()}


def _persisted_accounts(records: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """The snapshot's copy: id + title + source, and for an Iron-Proxy account
    the home VALUE the shell was started with and its unset names (a folder
    path, not a secret) — so a restore re-applies them without asking
    Iron-Proxy (boot never waits on it). Never a note."""
    out: dict[str, dict[str, Any]] = {}
    for p, r in records.items():
        if not isinstance(r, Mapping):
            continue
        row = {k: r.get(k) for k in ("id", "title", "source")}
        if r.get("home"):
            row["home"] = r["home"]
            row["unset"] = list(r.get("unset") or [])
        out[str(p)] = row
    return out


class TerminalManager:
    """Create, look up, list, and kill multiple live terminal sessions.

    Caps the number of *live* sessions (``max_sessions``) so a misbehaving UI
    can't spawn unbounded shells. Killed sessions stay queryable (with
    ``alive=False``) but no longer count against the cap.
    """

    def __init__(
        self,
        *,
        max_sessions: int = MAX_SESSIONS,
        max_dead_retained: int = MAX_DEAD_RETAINED,
        state_path: Path | None = None,
        pane_tokens: Any | None = None,
        mcp_sessions: Any | None = None,
        recipe_preparer: Callable[..., Any] | None = None,
        mcp_url: str = "",
    ) -> None:
        self.max_sessions = max_sessions
        self.max_dead_retained = max_dead_retained
        #: Where the live-session snapshot is persisted so terminals survive a
        #: daemon restart / app update (None = persistence disabled, e.g. tests).
        self.state_path = Path(state_path) if state_path else None
        self._sessions: dict[str, TerminalSession] = {}
        # Adaptive backend health: None = unverified, True = the real PTY works
        # in this environment, False = it spawns dead shells (frozen build
        # missing the ConPTY host) so go straight to a pipe-based shell. Set once
        # by the first verified create; skips the verify wait thereafter.
        self._pty_ok: bool | None = None
        # The /terminals endpoints are sync `def`, so Starlette runs them on
        # concurrent threadpool threads. Guard the dict so a list() iteration can't
        # race a create/kill ("dictionary changed size during iteration" -> 500) and
        # the cap check-then-act can't overshoot. Reentrant: create() calls
        # purge_dead() while holding it. Per-element syscalls (info()/start()/kill())
        # run on a SNAPSHOT outside the lock so polling can't be blocked by a spawn.
        self._lock = threading.RLock()
        # Panes whose credential exists but whose session row does not YET.
        # `create()` mints the token BEFORE the spawn (the one moment a child
        # environment can be set) and registers the session only after the shell
        # has started — a ConPTY spawn is the slowest part of the call. Without
        # this set, a concurrent close (`purge_dead` runs `sync_panes(live)`,
        # and every /terminals handler is a sync `def` on Starlette's
        # threadpool) revoked the in-flight pane's brand-new token, and the
        # harness's first call answered "the pane token expired when Iron
        # Jarvis restarted" — naming a restart that never happened.
        self._pending_panes: set[str] = set()
        #: create() calls that passed the cap check and have not yet registered
        #: (or put their id in `_pending_panes`) -- counted by the cap (v1.303.3).
        self._reserved = 0
        #: v1.406.0 (part 6): snapshot entries whose restore the boot stopped
        #: waiting for (``RESTORE_WAIT_S``), by pane id, in snapshot order.
        #: Each still holds its ``_reserved`` slot; the snapshot keeps the
        #: entry as it was read, so quitting before the shell starts loses
        #: nothing. Guarded by ``_lock``.
        self._restoring: dict[str, dict[str, Any]] = {}
        #: Set by ``kill_all``: a restore that finishes after shutdown began
        #: is closed instead of registered.
        self._closing = False
        # --- pane capability tokens (v1.238.0) -------------------------------
        # The manager owns the store because it owns the only two facts a pane
        # token is made of: the pane id (minted here, before the spawn) and the
        # moment the pane goes away. Wiring it from outside and forgetting one
        # of the three close paths IS the vulnerability, so it is built here by
        # default rather than left to a caller to remember.
        if pane_tokens is None:
            from ..browser.panetokens import PaneTokenStore  # deferred: heavy pkg

            pane_tokens = PaneTokenStore()
        #: Pane-scoped capability tokens for harnesses running inside a pane.
        self.pane_tokens = pane_tokens
        # The store must read LIVE capabilities, so it resolves through this
        # manager's own `get`. Only set when the caller left it unwired — a
        # store handed in already pointed somewhere is not second-guessed.
        try:
            if getattr(pane_tokens, "pane_lookup", None) is None:
                pane_tokens.pane_lookup = self.get
        except Exception:  # pragma: no cover - a stand-in without the attribute
            log.debug("pane token store has no pane_lookup", exc_info=True)
        #: The outward MCP server's session registry, when one exists. Optional:
        #: `/mcp` is another lane's route and the manager must work without it.
        #: Revoked alongside the tokens at all three close paths.
        self.mcp_sessions = mcp_sessions
        #: `(*, recipe, pane_id, token, capabilities) -> Mapping[str, str]` —
        #: the launch-recipe seam (plan 13.3). Whatever it returns is merged
        #: into `pane_env` BEFORE the spawn, which is the only moment a child
        #: environment can still be changed. `None` = no recipe machinery, and
        #: every pane then behaves exactly as it does today.
        self.recipe_preparer = recipe_preparer
        #: Where a harness should POST its MCP calls, exported as
        #: IRONJARVIS_MCP_URL beside the token. Empty until a caller sets it —
        #: the manager does not know the daemon's own address.
        self.mcp_url = mcp_url
        #: v1.302.0 — `(request, *, restoring, titles) -> PaneAccounts`, the
        #: Iron-Proxy account resolver (`pane_accounts.resolve` bound to the
        #: daemon's service; wired by the terminals routes). `None` = no
        #: accounts machinery: `accounts=` is ignored and a restored pane that
        #: recorded accounts comes back on this PC's login with a note.
        self.account_resolver: Callable[..., Any] | None = None
        #: v1.302.0 — `() -> None`, run on a background thread after a
        #: rehydrate restored a pane on an Iron-Proxy account: reads Iron-Proxy
        #: (no spawn) so the chips' cached snapshot is current. OFF the boot
        #: path by construction. Wired by the terminals routes.
        self.account_verifier: Callable[[], Any] | None = None
        #: Set for the duration of a rehydrate: the shared deadline of
        #: :data:`RESTORE_ACCOUNTS_BUDGET_S` for legacy re-resolution.
        self._restore_deadline: float | None = None

    # --- pane accounts (v1.302.0) ------------------------------------------

    def resolve_accounts(
        self,
        request: Mapping[str, Any] | None,
        *,
        restoring: bool = False,
        titles: Mapping[str, str] | None = None,
    ) -> Any | None:
        """The accounts a pane would start on (`pane_accounts.PaneAccounts`),
        or None without a resolver. BLOCKING (loopback HTTP to Iron-Proxy) —
        call off the event loop. Raises `pane_accounts.PaneAccountRefused`
        (never while `restoring`)."""
        if self.account_resolver is None:
            return None
        return self.account_resolver(request, restoring=restoring, titles=titles)

    def create(
        self,
        cwd: str | None = None,
        shell: str | None = None,
        cols: int = 80,
        rows: int = 24,
        *,
        backend: PtyBackend | None = None,
        env: dict | None = None,
        name: str | None = None,
        agent_cli: str | None = None,
        capabilities: Mapping[str, Any] | None = None,
        recipe: str | None = None,
        accounts: Any | None = None,
        claude_session_id: str | None = None,
        below_normal: bool = False,
    ) -> TerminalSession:
        """Create, start, and register a new session.

        ``cwd`` defaults to the user's home; ``shell`` defaults via
        :func:`resolve_shell`. Raises :class:`RuntimeError` at the cap.

        ``capabilities`` is what this pane is allowed to do (plan 4.2). Absent
        means none, so a pane never gains a capability by default.

        ``recipe`` names a launch recipe (plan 13.3). When set, the pane is
        given a capability token in its child environment before the shell
        starts — the one moment that is possible — and the recipe preparer, if
        one is wired, contributes the rest of the harness configuration.

        ``accounts`` (v1.302.0) is which subscription account each vendor CLI
        in this pane runs as: a request mapping (``{provider: id | "default"}``,
        resolved here through :attr:`account_resolver` — BLOCKING, and a
        refusal raises before anything is spawned) or an already-resolved
        ``PaneAccounts``. ``None`` = today's behaviour exactly.

        ``below_normal`` (v1.333.0) starts the shell below normal priority —
        for a pane an AGENT opens (``pane_spawn``); what the shell starts
        inherits it. Panes the user opens leave it False.
        """
        cwd = cwd or str(Path.home())
        # `shell_name` (was `name`): the PARAMETER `name` is the pane's
        # human handle now, and shadowing it here would silently label
        # every pane after its shell.
        shell_name, argv = resolve_shell(shell)
        with self._lock:
            # Evict stale dead sessions first so the dict can't grow without bound,
            # then enforce the cap — counting the creates still IN FLIGHT and
            # reserving this one's slot in the SAME lock hold. Registration
            # happens after the possibly-slow account resolution + spawn below;
            # counting only registered sessions let 20 concurrent creates open
            # 16 panes against a cap of 5 under load (test_concurrency_fixes,
            # v1.303.3). The reservation is released on every exit path.
            self.purge_dead()
            live = sum(1 for s in self._sessions.values() if s.alive)
            if live + len(self._pending_panes) + self._reserved >= self.max_sessions:
                raise RuntimeError(
                    f"terminal session cap reached ({self.max_sessions})"
                )
            self._reserved += 1
        try:
            return self._create_reserved(
                cwd, shell_name, argv, cols, rows, backend, env, name, agent_cli,
                capabilities, recipe, accounts, claude_session_id, below_normal,
            )
        finally:
            with self._lock:
                self._reserved -= 1

    def _create_reserved(
        self, cwd, shell_name, argv, cols, rows, backend, env, name, agent_cli,
        capabilities, recipe, accounts, claude_session_id, below_normal=False,
    ) -> TerminalSession:
        """The body of :meth:`create` once its cap slot is reserved."""
        if accounts is not None and isinstance(accounts, Mapping):
            # Resolved BEFORE the id is minted: a refused account must leave
            # no pane, no token and no pending entry behind.
            accounts = self.resolve_accounts(accounts)

        # THE PANE KNOWS WHERE IT IS (v1.217.0). A coding CLI started in here
        # otherwise has no way to tell it is inside Build, which pane it
        # occupies, or which project folder it was opened against — so a skill
        # running in it cannot address its siblings and cannot refuse to act
        # when it is NOT inside Build. Adapted from herdr, which injects
        # HERDR_WORKSPACE_ID / HERDR_TAB_ID / HERDR_PANE_ID for the same reason
        # and gates its agent skill on `test "${HERDR_ENV:-}" = 1`.
        #
        # THE ID IS MINTED BEFORE THE SPAWN, and that ordering is the feature.
        # The first cut set these on the session AFTER `_spawn` returned, with
        # a comment saying they were "applied to anything the pane starts
        # next" — nothing applied them. `pane_env()` had no consumer anywhere
        # in the codebase, so the shell (and therefore every CLI the user
        # launches into it) inherited none of them and a skill gating on
        # IRONJARVIS_BUILD would have refused to run inside Build forever.
        pane_id = new_id("term")
        pane_env = {
            "IRONJARVIS_BUILD": "1",
            "IRONJARVIS_PANE_ID": pane_id,
            "IRONJARVIS_PANE_CWD": cwd,
        }
        if name:
            pane_env["IRONJARVIS_PANE_NAME"] = name
        if agent_cli:
            pane_env["IRONJARVIS_PANE_CLI"] = agent_cli
        caps = _capabilities_or_none(capabilities)
        with self._lock:
            self._pending_panes.add(pane_id)
        try:
            if recipe:
                pane_env.update(self._prepare_recipe(recipe, pane_id, caps, cwd))
            session = self._spawn(
                cwd,
                shell_name,
                argv,
                cols,
                rows,
                backend,
                _with_pane_env(env, pane_env, accounts),
                # Passed only when set: a user's pane calls `_spawn` exactly
                # as before v1.333.0 (tests and seams replace `_spawn`).
                **({"below_normal": True} if below_normal else {}),
            )
            session.id = pane_id
            session.pane_name = name
            session.agent_cli = agent_cli
            session.capabilities = caps
            session.pane_env_extra = pane_env
            session.accounts = _account_records(accounts)
            session.claude_session_id = claude_session_id or None
            with self._lock:
                self._sessions[session.id] = session
                # Dropped INSIDE the same lock hold that registers the session,
                # so there is no instant in which the pane is in neither the
                # pending set nor `_sessions` — that instant is the bug.
                self._pending_panes.discard(pane_id)
        except BaseException:
            with self._lock:
                self._pending_panes.discard(pane_id)
            raise
        self._persist()  # keep the restart-survival snapshot current
        return session

    # --- launch recipes and pane tokens (v1.238.0) ------------------------

    def _prepare_recipe(
        self,
        recipe: str,
        pane_id: str,
        capabilities: Mapping[str, bool] | None,
        cwd: str = "",
    ) -> dict[str, str]:
        """Environment for a harness pane, minted and built BEFORE the spawn.

        The token is minted here rather than by the recipe because the store is
        the manager's: a recipe that minted its own would be a second place a
        credential is created and a second place one can be forgotten. The
        preparer only adds configuration on top, and a preparer that fails must
        not cost the pane its shell — the launch degrades to a pane with a token
        and no harness configuration, which is what the diagnostics panel is for.
        """
        from ..browser.panetokens import MCP_TOKEN_ENV, MCP_URL_ENV  # deferred

        extra: dict[str, str] = {}
        try:
            extra[MCP_TOKEN_ENV] = self.pane_tokens.mint(pane_id, capabilities or {})
        except Exception:  # noqa: BLE001 - a failed mint must not lose the pane
            log.warning("could not mint a pane capability token", exc_info=True)
            return {}
        if self.mcp_url:
            extra[MCP_URL_ENV] = self.mcp_url
        if self.recipe_preparer is None:
            return extra
        try:
            prepared = self.recipe_preparer(
                recipe=recipe,
                pane_id=pane_id,
                token=extra[MCP_TOKEN_ENV],
                capabilities=dict(capabilities or {}),
                # THE FOLDER RIDES THE SEAM. A recipe writes its config into the
                # pane's own working directory (`_confined`), and a preparer
                # called without one returns argv pointing at a `.mcp.json` it
                # never wrote — the config half of the feature simply does not
                # happen.
                cwd=cwd,
            )
        except Exception:  # noqa: BLE001 - see the docstring
            log.warning("launch recipe %s could not be prepared", recipe, exc_info=True)
            return extra
        if isinstance(prepared, Mapping):
            extra.update({str(k): str(v) for k, v in prepared.items()})
        return extra

    @staticmethod
    def _revoke_pane_tokens_for(store: Any, pane_ids: list[str]) -> None:
        """`store.revoke_pane` for each id, never raising into a close path."""
        for pane_id in pane_ids:
            try:
                store.revoke_pane(pane_id)
            except Exception:  # noqa: BLE001
                log.warning(
                    "could not revoke credentials for pane %s", pane_id, exc_info=True
                )

    def _revoke_pane_tokens(self, pane_ids: list[str]) -> None:
        """Drop every credential naming a pane that is gone.

        Called from ALL THREE close paths, because there is no pane-closed event
        and a token that survives one of them is the whole vulnerability. A
        failing store is logged at WARNING rather than swallowed: a revocation
        that silently did not happen leaves a live credential behind.
        """
        for store in (self.pane_tokens, self.mcp_sessions):
            if store is not None:
                self._revoke_pane_tokens_for(store, pane_ids)

    def _spawn(
        self, cwd, name, argv, cols, rows, backend, env, *, below_normal: bool = False
    ) -> TerminalSession:
        """Spawn a session, transparently falling back to a pipe-based shell if
        the real PTY spawns a shell that dies immediately.

        A test-injected ``backend`` is trusted as-is. Otherwise, the FIRST
        production spawn is liveness-verified: if the shell dies within
        :data:`_PTY_VERIFY_SECONDS` (the ConPTY-host-missing failure mode), we
        remember it (``_pty_ok = False``) and use :class:`PipeBackend` for this
        and all future terminals — commands still run, just without a full TTY.
        """
        low = {"below_normal": True} if below_normal else {}
        if backend is not None:  # explicit backend (tests) — no verify/fallback
            session = TerminalSession(
                cwd=cwd, shell=name, argv=argv, cols=cols, rows=rows, backend=backend, **low
            )
            session.start(env=env)
            return session

        # Known-bad PTY in this environment → pipe shell straight away (no wait).
        if self._pty_ok is False:
            return self._pipe_session(cwd, name, argv, cols, rows, env, **low)

        from .backend import ConPtyUnavailable, mark_conpty_broken

        session = TerminalSession(cwd=cwd, shell=name, argv=argv, cols=cols, rows=rows, **low)
        try:
            session.start(env=env)
        except ConPtyUnavailable:
            # v1.248.0: the raw ConPTY could not make a PSEUDOCONSOLE here (a
            # bad command or folder is a different error and still reaches
            # the caller). Stop offering it for the rest of this process and
            # spawn on the next backend in `default_backend`'s order.
            log.warning("raw ConPTY unavailable; using the next backend", exc_info=True)
            mark_conpty_broken()
            session = TerminalSession(cwd=cwd, shell=name, argv=argv, cols=cols, rows=rows, **low)
            session.start(env=env)

        if self._pty_ok is True:  # already verified healthy — trust it, no wait
            return session

        # First spawn of the daemon's life: verify the shell STAYS alive.
        #
        # v1.406.0 (part 6): ...unless the console has already SPOKEN. The
        # wait exists for a PTY with no console host (the frozen build that
        # dropped OpenConsole.exe, v1.190.0): its shell dies silently. The raw
        # ConPTY (a pushing backend) cannot even be created without its host
        # (CreatePseudoConsole fails -> ConPtyUnavailable above), and the host
        # prints its first bytes the moment the shell connects — so output on
        # a pushing session IS the proof, and waiting out the full 0.7 s cost
        # every boot that restored panes ~0.7 of its ~0.9 s rehydrate step.
        # A backend that is POLLED (pywinpty, pipe) records nothing until
        # read, and keeps the full wait exactly as before.
        pushing = session.pushes_output
        deadline = time.monotonic() + _PTY_VERIFY_SECONDS
        while time.monotonic() < deadline:
            if not session.alive:
                break
            if pushing and session.output_seq > 0:
                break
            time.sleep(0.02 if pushing else 0.04)
        if session.alive:
            self._pty_ok = True
            return session

        # The real PTY produced a dead shell — this environment can't host one.
        self._pty_ok = False
        try:
            session.kill()
        except Exception:  # pragma: no cover - defensive
            pass
        return self._pipe_session(cwd, name, argv, cols, rows, env, **low)

    @staticmethod
    def _pipe_session(cwd, name, argv, cols, rows, env, below_normal=False) -> TerminalSession:
        session = TerminalSession(
            cwd=cwd, shell=name, argv=argv, cols=cols, rows=rows, backend=PipeBackend(),
            below_normal=below_normal,
        )
        session.start(env=env)
        session.degraded = True  # UI hint: basic shell, no full TTY
        return session

    def get(self, id: str) -> TerminalSession | None:
        with self._lock:
            return self._sessions.get(id)

    def list(self) -> list[dict[str, Any]]:
        with self._lock:  # snapshot under the lock; query alive/info outside it
            items = list(self._sessions.values())
        return [s.info() for s in items]

    def purge_dead(self) -> int:
        """Evict all but the most recently added dead sessions.

        Retains the last ``max_dead_retained`` dead sessions (insertion order)
        so just-killed sessions stay queryable for a bounded window, while
        older dead entries are dropped. Returns the number evicted.
        """
        with self._lock:
            dead = [sid for sid, s in self._sessions.items() if not s.alive]
            stale = dead[: -self.max_dead_retained] if self.max_dead_retained else dead
            dropped = []
            for sid in stale:
                gone_session = self._sessions.pop(sid, None)  # tolerate an already-removed key
                if gone_session is not None:
                    dropped.append(gone_session)
            live = [sid for sid, s in self._sessions.items() if s.alive]
            gone = [sid for sid in dead if sid not in live]
            # A pane mid-`create()` holds a minted token and no session row yet.
            # It is as live as anything in the dict, so the sweep below must be
            # told about it or it revokes the credential the child is about to
            # be spawned with.
            live_or_pending = live + sorted(self._pending_panes)
        # CHOKEPOINT 2 of 3. This is where a shell that died ON ITS OWN is
        # noticed, and it is the one a reader forgets — nobody called kill(), so
        # nothing else would ever drop that pane's credential. `sync_panes` and
        # not `revoke_pane`: a dead session is RETAINED for a while for
        # queryability, and a retained pane must still lose its token, so the
        # authority is the live list rather than the eviction list — plus the
        # panes still being created, which no list of dead sessions can name.
        if self.pane_tokens is not None:
            try:
                self.pane_tokens.sync_panes(live_or_pending)
            except Exception:  # noqa: BLE001
                log.warning("could not sync pane capability tokens", exc_info=True)
        if self.mcp_sessions is not None:
            self._revoke_pane_tokens_for(self.mcp_sessions, gone)
        _stop_background_of(dropped)
        return len(stale)

    def kill(self, id: str) -> bool:
        with self._lock:
            session = self._sessions.get(id)
        if session is None:
            return False
        session.kill()
        # CHOKEPOINT 1 of 3, and named explicitly rather than left to the
        # purge_dead() call below: closing a pane must revoke its credential
        # even if the retention policy later changes and this session is still
        # in the dict.
        self._revoke_pane_tokens([id])
        self.purge_dead()
        self._persist()  # drop the closed session from the snapshot
        return True

    def kill_all(self) -> None:
        # v1.333.0 (review): THE SHUTDOWN SNAPSHOT CARRIES THE SCROLLBACK
        # INLINE too. Since v1.333.0 the 30 s snapshot keeps each pane's
        # history in its own file and the index holds none; an OLDER app
        # (a downgrade, or a reinstall of the previous version) reads only
        # the index and would restore every pane EMPTY. Written once per
        # clean shutdown, so an update or downgrade after a normal quit keeps
        # the history; a crash still has the per-pane files. A restore
        # prefers the inline copy: it is written only here (or by an older
        # app), always after the files, so it is never the older one.
        with self._lock:
            # A pane whose boot restore is still starting is closed when (if)
            # its shell starts; its snapshot entry is kept below as read.
            self._closing = True
        try:
            self.snapshot(inline=True)
        except Exception:  # noqa: BLE001 — persistence never blocks a shutdown
            log.debug("shutdown terminal snapshot failed", exc_info=True)
        with self._lock:
            items = list(self._sessions.values())
        for session in items:
            try:
                session.kill()
            except Exception:  # pragma: no cover - defensive
                pass
        # CHOKEPOINT 3 of 3. Shutdown: nothing survives, so nothing is spared —
        # revoke_all rather than a loop, so a pane whose kill() raised above
        # still loses its token.
        for store in (self.pane_tokens, self.mcp_sessions):
            if store is None:
                continue
            try:
                store.revoke_all()
            except Exception:  # noqa: BLE001
                log.warning("could not revoke pane credentials", exc_info=True)

    # --- Restart / update survival ---------------------------------------
    # A live shell is a child of the daemon, so an update (which restarts the
    # daemon) necessarily kills it — running programs can't be resurrected. But
    # we persist each session's identity, directory, size, and recent scrollback
    # so that on the next boot the PANES come back: a fresh shell in the same
    # cwd, under the SAME id (so the dashboard's saved layout matches), with the
    # prior history shown above the new prompt.

    def _persist(self) -> None:
        """Best-effort snapshot after a mutation; never raises."""
        try:
            self.snapshot()
        except Exception:  # pragma: no cover - persistence must never break terminals
            log.debug("terminal snapshot failed", exc_info=True)

    # SCROLLBACK IS ONE FILE PER PANE (v1.333.0, B5). The snapshot was ONE
    # file holding every pane's last 64 KB as base64, rewritten whole every
    # 30 s whenever ANY pane printed: one busy Claude beside five quiet
    # shells rewrote all six (~88 KB each), ~1.5 GB of disk writes a day for
    # six busy panes. Now `terminals.json` is the INDEX (identity, folder,
    # size, accounts, ...; small) and each pane's raw scrollback lives in
    # `terminals-scrollback/<id>.bin`, written only when THAT pane printed
    # since its last write. The index is rewritten only when what it says
    # changed. Same promise: a crash loses at most ~30 s of scrollback.
    # Reading stays compatible: an index entry that still carries
    # `scrollback_b64` (written before v1.333.0) is restored from it.

    def _entry(self, s: TerminalSession, inline: bool = False) -> dict[str, Any]:
        """One pane's index entry (no scrollback unless its id cannot name a
        file — see :data:`_FILE_SAFE_ID` — or ``inline``: the shutdown
        snapshot, see :meth:`kill_all`)."""
        entry: dict[str, Any] = {
            "id": s.id,
            "shell": s.shell,
            "argv": list(s.argv),
            "cwd": s.cwd,
            "cols": s.cols,
            "rows": s.rows,
            # v1.217.0: the pane's identity is part of the pane.
            # Dropping it here would silently un-name every pane
            # on restart, and an agent addressing panes by name
            # would find none of them after a daemon restart —
            # which is exactly when it most needs to.
            "name": s.pane_name,
            "agent_cli": s.agent_cli,
            # v1.245.0: an unanswered Resume offer survives
            # another restart (restore reads either field).
            "resume_cli": s.resume_cli,
            # v1.238.0: WITHOUT THIS LINE the pane's capabilities
            # silently reset to none on every daemon restart, and
            # every unit test stays green because they all ask the
            # live object. `capabilities` is written as stored —
            # `null` for a pane that was never configured — so a
            # reader can still tell "never set" from "all unticked".
            "capabilities": (
                dict(s.capabilities)
                if isinstance(s.capabilities, dict)
                else None
            ),
            # v1.302.0: which account each CLI ran as (ids +
            # titles + source; never a note — a restore writes
            # its own). Only for a pane that has accounts, so
            # every other entry is byte-identical to v1.301.0.
            **(
                {"accounts": _persisted_accounts(s.accounts)}
                if getattr(s, "accounts", None)
                else {}
            ),
            # v1.303.0: the conversation this app started here.
            **(
                {"claude_session_id": s.claude_session_id}
                if getattr(s, "claude_session_id", None)
                else {}
            ),
            # v1.303.2: where this pane's conversation came from
            # (its resume-failure check and handoff read it).
            **(
                {"continued_from": dict(s.continued_from)}
                if getattr(s, "continued_from", None)
                else {}
            ),
            # v1.333.0: a pane an agent opened comes back below normal.
            **({"below_normal": True} if getattr(s, "below_normal", False) else {}),
        }
        # v1.333.0: what a daemon CRASH would leave running from this pane —
        # its hidden background processes, by (pid, start time); the next
        # boot stops the exact matches (terminals/pane_jobs.py).
        members = getattr(s.backend, "background_members", None)
        if callable(members):
            try:
                background = members()
            except Exception:  # noqa: BLE001
                background = []
            if background:
                entry["background"] = background
        if inline or not _FILE_SAFE_ID.match(str(s.id)):
            sb = s.scrollback_bytes()[-_SNAPSHOT_SCROLLBACK:]
            entry["scrollback_b64"] = base64.b64encode(sb).decode("ascii")
        return entry

    def _write_scrollbacks(self, sessions: list[TerminalSession]) -> int:
        """Write the scrollback file of each pane that printed since its last
        write (its ``output_seq`` moved). Returns how many were written."""
        folder = _scrollback_dir(self.state_path)
        wrote = 0
        for s in sessions:
            # What the file holds: how far the output got, and WHICH tail (a
            # restore replaces the buffer without printing anything).
            tail = getattr(s, "_tail", b"")
            seq = (getattr(s, "output_seq", 0), len(tail), id(tail))
            if not _FILE_SAFE_ID.match(str(s.id)) or getattr(s, "_snap_seq", None) == seq:
                continue
            try:
                folder.mkdir(parents=True, exist_ok=True)
                _write_atomic(folder / f"{s.id}.bin", s.scrollback_bytes()[-_SNAPSHOT_SCROLLBACK:])
            except OSError:  # retried on the next tick: _snap_seq is not advanced
                log.debug("could not write a pane's scrollback", exc_info=True)
                continue
            s._snap_seq = seq
            wrote += 1
        return wrote

    def _write_index(self, entries: list[dict[str, Any]], text: str) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(self.state_path)
        self._last_index = text
        # A scrollback file whose pane is no longer in the index is history
        # nobody can restore: remove it (and any torn temp file). Never while
        # a pane is being created or restored (a restore reads its file), and
        # never the file of a LIVE pane this manager holds: two snapshots
        # can overlap (the 30 s loop and a create's), and the one that listed
        # the panes first must not delete what the other just wrote (review).
        with self._lock:
            if self._reserved:
                return
            held = {sid for sid, s in self._sessions.items() if s.alive}
        keep = {f"{i}.bin" for i in held | {e["id"] for e in entries}}
        folder = _scrollback_dir(self.state_path)
        try:
            names = [p for p in folder.iterdir() if p.is_file()]
        except OSError:
            return
        for p in names:
            if p.name not in keep:
                try:
                    p.unlink()
                except OSError:
                    pass

    def _live_for_snapshot(self) -> list[TerminalSession]:
        with self._lock:
            return [s for s in self._sessions.values() if s.alive]

    def _index_text(
        self, sessions: list[TerminalSession], inline: bool = False
    ) -> tuple[list[dict[str, Any]], str]:
        entries: list[dict[str, Any]] = []
        for s in sessions:
            try:
                entries.append(self._entry(s, inline))
            except Exception:  # pragma: no cover - skip an odd session, keep the rest
                continue
        # v1.406.0 (part 6): a pane the boot stopped waiting for is still the
        # user's pane. Its entry is written back exactly as it was read (its
        # scrollback file is kept: `_write_index` never prunes while a slot is
        # reserved), so a snapshot taken before its shell starts — the 30 s
        # loop, a create, the shutdown — cannot drop it.
        with self._lock:
            held = {e.get("id") for e in entries}
            pending = [
                dict(e)
                for rid, e in getattr(self, "_restoring", {}).items()
                if rid not in held
            ]
        entries.extend(pending)
        return entries, json.dumps({"terminals": entries})

    def restoring_ids(self) -> list[str]:
        """Ids of panes a boot restore is still starting (v1.406.0 part 6).
        `GET /terminals` reports them so the Build page keeps their saved
        view, thread and layout keys while their shells start."""
        with self._lock:
            return list(getattr(self, "_restoring", {}))

    def snapshot(self, *, inline: bool = False) -> None:
        """Persist all LIVE sessions under ``state_path`` (atomic writes; no-op
        when persistence is disabled): every scrollback that changed, and the
        index, always. ``inline`` also puts each pane's scrollback in the
        index, as every snapshot did before v1.333.0 (see :meth:`kill_all`)."""
        if not self.state_path:
            return
        sessions = self._live_for_snapshot()
        self._write_scrollbacks(sessions)
        entries, text = self._index_text(sessions, inline)
        self._write_index(entries, text)

    def snapshot_if_changed(self) -> bool:
        """Write only what changed since the last write (v1.245.0; per pane
        since v1.333.0): the scrollback file of each pane that printed, and
        the index only when what it says changed. The daemon's 30 s loop
        calls this, so a quiet afternoon costs nothing, one busy pane costs
        one file, and a crash loses at most ~30 s of scrollback. Returns True
        when it wrote. BLOCKING (file writes): call it off the loop."""
        if not self.state_path:
            return False
        sessions = self._live_for_snapshot()
        wrote = self._write_scrollbacks(sessions) > 0
        entries, text = self._index_text(sessions)
        if text != getattr(self, "_last_index", None):
            self._write_index(entries, text)
            wrote = True
        return wrote

    def _saved_scrollback(self, entry: Mapping[str, Any], rid: str) -> bytes:
        """A snapshot entry's scrollback: inline (an index written before
        v1.333.0, or an id that cannot name a file), else its own file."""
        sb = entry.get("scrollback_b64")
        if sb:
            try:
                return base64.b64decode(sb)
            except Exception:  # pragma: no cover - bad data, keep the fresh shell
                return b""
        if not self.state_path or not _FILE_SAFE_ID.match(str(rid)):
            return b""
        try:
            return (_scrollback_dir(self.state_path) / f"{rid}.bin").read_bytes()[
                -_SNAPSHOT_SCROLLBACK:
            ]
        except OSError:
            return b""

    def restore(
        self,
        entry: dict[str, Any],
        *,
        env: dict | None = None,
        backend: PtyBackend | None = None,
        register: bool = True,
    ) -> TerminalSession | None:
        """Re-open ONE persisted session under its original id, with its prior
        scrollback preloaded. Returns None at the session cap.

        ``register=False`` (v1.311.0) leaves the session out of the manager's
        dict: ``rehydrate`` restores panes concurrently and registers them
        afterwards IN SNAPSHOT ORDER, so the rail keeps the user's order."""
        cwd = entry.get("cwd") or str(Path.home())
        if not Path(cwd).is_dir():  # the folder may have moved/been deleted
            cwd = str(Path.home())
        name = entry.get("shell")
        argv = entry.get("argv")
        if not argv:
            name, argv = resolve_shell(name)
        try:
            cols = int(entry.get("cols") or 80)
            rows = int(entry.get("rows") or 24)
        except (TypeError, ValueError):
            cols, rows = 80, 24
        with self._lock:
            self.purge_dead()
            if sum(1 for s in self._sessions.values() if s.alive) >= self.max_sessions:
                return None
        rid = entry.get("id") or new_id("term")
        pane_name = entry.get("name") or None
        # The CLI that was running here — or one still waiting to be resumed
        # from an EARLIER restart the user has not answered yet.
        agent_cli = entry.get("agent_cli") or entry.get("resume_cli") or None
        pane_env = {
            "IRONJARVIS_BUILD": "1",
            "IRONJARVIS_PANE_ID": rid,
            "IRONJARVIS_PANE_CWD": cwd,
        }
        if pane_name:
            pane_env["IRONJARVIS_PANE_NAME"] = pane_name
        if agent_cli:
            # Kept in the fresh shell's environment: the likeliest next process
            # in this pane is that same CLI, resumed.
            pane_env["IRONJARVIS_PANE_CLI"] = agent_cli
        accounts = self._restore_accounts(entry.get("accounts"))
        spawn_env = _with_pane_env(env, pane_env, accounts)
        session = self._spawn(
            cwd,
            name or "shell",
            list(argv),
            cols,
            rows,
            backend,
            spawn_env,
            **({"below_normal": True} if entry.get("below_normal") is True else {}),
        )
        session.accounts = _account_records(accounts)
        sid = entry.get("claude_session_id")
        session.claude_session_id = sid if isinstance(sid, str) and sid else None
        session.continued_from = _continued_from_or_none(entry.get("continued_from"))
        session.id = rid
        session.pane_name = pane_name
        # v1.245.0: the shell is FRESH — whatever CLI ran here died with the
        # old daemon. The pane offers to resume it (resume_cli) and no longer
        # claims it is running: the chip used to say "claude" over a bare
        # shell for as long as the pane lived.
        session.agent_cli = None
        session.resume_cli = agent_cli
        if agent_cli == "claude" and session.claude_session_id:
            # Decided HERE (the boot rehydrate, off the serving loop): rows
            # only read it, and `info()` runs on the loop for pane tools.
            session.decide_claude_resume()
        # The other half of the round trip. An entry written before this field
        # existed has no key, which normalises to no capabilities — the safe
        # default, and the reason this is read through `_capabilities_or_none`
        # rather than assigned raw.
        session.capabilities = _capabilities_or_none(entry.get("capabilities"))
        # NO TOKEN IS MINTED HERE, and that is the restart-recovery rule (D19).
        # A restored pane keeps its id but runs a DIFFERENT shell process, so
        # re-minting would hand a live credential to whatever now occupies that
        # pane. `/mcp` answers 401 until the user relaunches the harness.
        session.pane_env_extra = pane_env
        old = self._saved_scrollback(entry, rid)
        if old:
            # v1.333.0: the old program's bytes are followed by
            # `restore_reset`. That program is DEAD, and a replay that left
            # xterm in its mouse mode sent a mouse report for every cell the
            # pointer crossed — the wheel stopped scrolling, selecting needed
            # Shift, and the reports were typed into the fresh shell as text
            # (terminals/modes.py).
            # Once: a tail that already ends with it (a pane restored and
            # closed again before its new shell printed) gets no second copy.
            reset = restore_reset(old, rows)
            prior = old if old.endswith(reset) else old + reset
            # v1.337.2: PREPEND, under the lock the output path records with.
            # The new shell was spawned above and a pushing backend has been
            # recording its first bytes (ConPTY's start-up and the prompt)
            # since; ASSIGNING the tail threw them away, so the pane never saw
            # the prompt and the first key typed landed at column 1.
            with session._read_lock:
                session._tail[:0] = prior
            # A snapshot that FILLED its cap was sliced at a raw byte
            # offset — serve its replay from a safe boundary, not the cut.
            session._tail_truncated = len(old) >= _SNAPSHOT_SCROLLBACK
        # v1.338.0: the saved size can be stale; the first pane to attach may
        # start this untouched shell again at its real size
        # (`TerminalSession.restart_at_size`).
        session._respawn_env = spawn_env
        session._untouched = True
        if register:
            with self._lock:
                self._sessions[rid] = session
        return session

    def _restore_accounts(self, recorded: Any) -> Any | None:
        """The accounts a RESTORED pane comes back on (v1.302.0), never another
        one, and WITHOUT WAITING ON IRON-PROXY: a recorded home is applied
        again as it was at spawn (no call — `pane_accounts.from_recorded`);
        the post-boot check updates the chip. Only an account recorded before
        homes were is re-resolved by id, bounded by the rehydrate's shared
        :data:`RESTORE_ACCOUNTS_BUDGET_S`; past it, or on any failure, it comes
        back on this PC's login with a note. Never raises."""
        if not isinstance(recorded, Mapping) or not recorded:
            return None
        from .pane_accounts import from_recorded, late_note

        out, legacy, titles = from_recorded(recorded)
        if legacy:
            resolved, late = self._bounded_resolve(legacy, titles)
            if resolved is not None:
                out.merge(resolved)
            else:
                for provider, pid in legacy.items():
                    title = titles.get(provider) or pid
                    out.use_default(
                        provider,
                        note=late_note(title) if late else (
                            f'This terminal came back on this PC\'s login instead of "{title}".'
                        ),
                    )
        return out if out else None

    def _bounded_resolve(
        self, request: dict[str, str], titles: dict[str, str]
    ) -> tuple[Any | None, bool]:
        """`(resolved, timed_out)` — `resolve_accounts(restoring=True)` on a
        worker thread, given only what is left of the restore budget. A
        resolution still running past it is abandoned (its answer unused)."""
        if self.account_resolver is None:
            return None, False
        deadline = self._restore_deadline
        if deadline is None:
            deadline = time.monotonic() + RESTORE_ACCOUNTS_BUDGET_S
        box: dict[str, Any] = {}

        def work() -> None:
            try:
                box["out"] = self.resolve_accounts(request, restoring=True, titles=titles)
            except Exception:  # noqa: BLE001 — a restore must never lose the pane
                log.warning("could not re-resolve a restored pane's accounts", exc_info=True)

        worker = threading.Thread(target=work, name="pane-accounts-restore", daemon=True)
        worker.start()
        worker.join(max(0.0, deadline - time.monotonic()))
        if worker.is_alive():
            return None, True
        return box.get("out"), False

    def _verify_accounts_later(self) -> None:
        """After a rehydrate restored panes on Iron-Proxy accounts: read
        Iron-Proxy on a background thread (never the boot path) so the chips'
        cached snapshot says whether each account is still there / parked."""
        verifier = self.account_verifier
        if verifier is None:
            return

        def run() -> None:
            try:
                verifier()
            except Exception:  # noqa: BLE001 — a chip refresh never matters more
                log.debug("post-boot account check failed", exc_info=True)

        threading.Thread(target=run, name="pane-accounts-verify", daemon=True).start()

    def rehydrate(self, *, env: dict | None = None, backend: PtyBackend | None = None) -> int:
        """On boot, re-open every persisted session. Best-effort per entry;
        returns how many were restored. No-op without a snapshot file."""
        if not self.state_path or not self.state_path.is_file():
            return 0
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return 0
        entries = data.get("terminals") if isinstance(data, dict) else data
        if not isinstance(entries, list):
            return 0
        entries = [e for e in entries if isinstance(e, dict)]
        if not entries:
            return 0
        # v1.333.0: a daemon that CRASHED closed no pane, so the hidden
        # background processes its panes started are still running. Stop the
        # exact (pid, start time) matches that are still hidden — never one
        # with a window (terminals/pane_jobs.py). After a clean shutdown they
        # are already gone and nothing matches.
        leftovers = [
            pair for e in entries for pair in (e.get("background") or []) if isinstance(pair, list)
        ]
        if leftovers:
            from .pane_jobs import stop_leftovers

            stop_leftovers(leftovers)
        # THE PANES COME BACK CONCURRENTLY (v1.311.0, finding
        # boot-blocked-by-mcp-and-pane-respawn). Each restore is a shell spawn
        # (~0.3 s for pwsh under ConPTY) and they ran one after another inside
        # the lifespan: 0.85-1.0 s of every boot on the user's 3-pane install.
        # They are independent processes, so they spawn side by side and boot
        # waits for the SLOWEST one, not the sum.
        #
        # THE CAP IS DECIDED UP FRONT: concurrent restores would each pass
        # `restore`'s own check-then-act before any of them registered, so
        # only as many entries as there are free slots are attempted, and
        # those slots are RESERVED (`_reserved`, the v1.303.3 create() rule)
        # until the sessions are registered — a create() racing the boot
        # cannot overshoot either.
        with self._lock:
            self.purge_dead()
            live = sum(1 for s in self._sessions.values() if s.alive)
            free = self.max_sessions - live - len(self._pending_panes) - self._reserved
            batch = entries[: max(0, free)]
            self._reserved += len(batch)
        slots: list[TerminalSession | None] = [None] * len(batch)

        def _restore_at(i: int) -> None:
            try:
                session = self.restore(batch[i], env=env, backend=backend, register=False)
                if session is not None:
                    # No pane is attached at boot, so without a reader the fresh
                    # shell's output (banner + prompt) never reaches the tail —
                    # a studio session resumed against the STALE replayed tail
                    # would then type briefs into a bare shell. Drain from the
                    # start; it yields whenever a Build pane attaches.
                    session.start_autodrain()
                    slots[i] = session
            except Exception:  # pragma: no cover - one bad entry mustn't skip the rest
                log.debug("failed to restore a terminal", exc_info=True)

        restored = 0
        on_accounts = False
        # THE BOOT WAITS AT MOST `RESTORE_WAIT_S` (v1.406.0, part 6). Each
        # restore runs on its own DAEMON thread — never a ThreadPoolExecutor,
        # whose workers the interpreter joins at exit, so a spawn stuck inside
        # Windows would also hang the daemon's own shutdown. A restore that
        # finishes inside the bound is registered below exactly as before (in
        # snapshot order, one snapshot); one that finishes later registers
        # itself; one that never finishes keeps its slot and its entry.
        finished = [False] * len(batch)
        waiting = {"on": True}

        def _run(i: int) -> None:
            try:
                _restore_at(i)
            finally:
                with self._lock:
                    if waiting["on"]:
                        finished[i] = True
                        return
                self._finish_late_restore(slots[i], batch[i])

        self._restore_deadline = time.monotonic() + RESTORE_ACCOUNTS_BUDGET_S
        late: list[int] = []
        try:
            threads = [
                threading.Thread(target=_run, args=(i,), name=f"pane-restore_{i}", daemon=True)
                for i in range(len(batch))
            ]
            for t in threads:
                t.start()
            wait_until = time.monotonic() + RESTORE_WAIT_S
            for t in threads:
                t.join(max(0.0, wait_until - time.monotonic()))
            # Registered AFTER the join, in snapshot order, under one lock hold.
            with self._lock:
                waiting["on"] = False
                for i, session in enumerate(slots):
                    if not finished[i]:
                        late.append(i)
                        rid = batch[i].get("id")
                        if rid:
                            self._restoring[str(rid)] = batch[i]
                        continue
                    if session is None:
                        continue
                    self._sessions[session.id] = session
                    restored += 1
                    on_accounts = on_accounts or any(
                        a.get("source") == "iron-proxy"
                        for a in (session.accounts or {}).values()
                    )
        finally:
            self._restore_deadline = None
            with self._lock:
                # A restore still starting keeps its slot until it finishes.
                self._reserved -= len(batch) - len(late)
        if late:
            log.warning(
                "%d of %d terminal panes had not started after %g s; boot goes on "
                "and each opens the moment its shell starts",
                len(late),
                len(batch),
                RESTORE_WAIT_S,
            )
        if len(entries) > len(batch):
            log.warning(
                "restored %d of %d terminal panes: the session cap (%d) was reached",
                restored,
                len(entries),
                self.max_sessions,
            )
        if restored:
            self._persist()  # ONE snapshot of the freshly-restored (same) set
        if on_accounts:
            self._verify_accounts_later()
        return restored

    def _finish_late_restore(
        self, session: TerminalSession | None, entry: Mapping[str, Any]
    ) -> None:
        """A boot restore that finished after the boot stopped waiting
        (v1.406.0, part 6): register it (or close it, when shutdown began),
        release its slot, and persist. Runs on the restore's own thread;
        never raises."""
        rid = str(entry.get("id") or "")
        close_it = False
        with self._lock:
            self._restoring.pop(rid, None)
            self._reserved -= 1
            if session is not None:
                if self._closing:
                    close_it = True
                else:
                    self._sessions[session.id] = session
        if close_it and session is not None:
            try:
                session.kill()
            except Exception:  # noqa: BLE001 — shutdown: nothing to report to
                pass
            return
        log.info(
            "a terminal pane %s after the boot stopped waiting for it",
            "opened" if session is not None else "could not be reopened",
        )
        self._persist()
