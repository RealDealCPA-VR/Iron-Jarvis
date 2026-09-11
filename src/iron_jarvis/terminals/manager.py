"""Manager that owns every live terminal session for the dashboard."""

from __future__ import annotations

import base64
import json
import os
import logging
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ..core.ids import new_id
from .backend import PipeBackend, PtyBackend
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


def _with_pane_env(env: dict | None, pane_env: dict[str, str]) -> dict:
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
    credential whichever dict it travelled in, and no caller in this repository
    passes one deliberately.
    """
    base = dict(env) if env is not None else os.environ.copy()
    for name in _DAEMON_ONLY_ENV:
        base.pop(name, None)
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


def _snapshot_key(sessions: "list[TerminalSession]") -> tuple:
    """What a snapshot of these sessions covers (v1.245.0): which panes, how
    far each one's output had got (``output_seq``), and the identity fields a
    snapshot carries. Equal keys mean the snapshot on disk is already current."""
    return tuple(
        sorted(
            (
                s.id,
                getattr(s, "output_seq", 0),
                s.pane_name or "",
                s.agent_cli or "",
                getattr(s, "resume_cli", None) or "",
            )
            for s in sessions
        )
    )


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
        """
        cwd = cwd or str(Path.home())
        # `shell_name` (was `name`): the PARAMETER `name` is the pane's
        # human handle now, and shadowing it here would silently label
        # every pane after its shell.
        shell_name, argv = resolve_shell(shell)
        with self._lock:
            # Evict stale dead sessions first so the dict can't grow without bound,
            # then enforce the cap. (Registration happens after the possibly-slow
            # spawn+verify below; a rare concurrent create may overshoot the cap
            # by one, which is harmless for a human-driven, bounded action.)
            self.purge_dead()
            live = sum(1 for s in self._sessions.values() if s.alive)
            if live >= self.max_sessions:
                raise RuntimeError(
                    f"terminal session cap reached ({self.max_sessions})"
                )

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
                cwd, shell_name, argv, cols, rows, backend, _with_pane_env(env, pane_env)
            )
            session.id = pane_id
            session.pane_name = name
            session.agent_cli = agent_cli
            session.capabilities = caps
            session.pane_env_extra = pane_env
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
        self, cwd, name, argv, cols, rows, backend, env
    ) -> TerminalSession:
        """Spawn a session, transparently falling back to a pipe-based shell if
        the real PTY spawns a shell that dies immediately.

        A test-injected ``backend`` is trusted as-is. Otherwise, the FIRST
        production spawn is liveness-verified: if the shell dies within
        :data:`_PTY_VERIFY_SECONDS` (the ConPTY-host-missing failure mode), we
        remember it (``_pty_ok = False``) and use :class:`PipeBackend` for this
        and all future terminals — commands still run, just without a full TTY.
        """
        if backend is not None:  # explicit backend (tests) — no verify/fallback
            session = TerminalSession(
                cwd=cwd, shell=name, argv=argv, cols=cols, rows=rows, backend=backend
            )
            session.start(env=env)
            return session

        # Known-bad PTY in this environment → pipe shell straight away (no wait).
        if self._pty_ok is False:
            return self._pipe_session(cwd, name, argv, cols, rows, env)

        from .backend import ConPtyUnavailable, mark_conpty_broken

        session = TerminalSession(cwd=cwd, shell=name, argv=argv, cols=cols, rows=rows)
        try:
            session.start(env=env)
        except ConPtyUnavailable:
            # v1.248.0: the raw ConPTY could not make a PSEUDOCONSOLE here (a
            # bad command or folder is a different error and still reaches
            # the caller). Stop offering it for the rest of this process and
            # spawn on the next backend in `default_backend`'s order.
            log.warning("raw ConPTY unavailable; using the next backend", exc_info=True)
            mark_conpty_broken()
            session = TerminalSession(cwd=cwd, shell=name, argv=argv, cols=cols, rows=rows)
            session.start(env=env)

        if self._pty_ok is True:  # already verified healthy — trust it, no wait
            return session

        # First spawn of the daemon's life: verify the shell STAYS alive.
        deadline = time.monotonic() + _PTY_VERIFY_SECONDS
        while time.monotonic() < deadline:
            if not session.alive:
                break
            time.sleep(0.04)
        if session.alive:
            self._pty_ok = True
            return session

        # The real PTY produced a dead shell — this environment can't host one.
        self._pty_ok = False
        try:
            session.kill()
        except Exception:  # pragma: no cover - defensive
            pass
        return self._pipe_session(cwd, name, argv, cols, rows, env)

    @staticmethod
    def _pipe_session(cwd, name, argv, cols, rows, env) -> TerminalSession:
        session = TerminalSession(
            cwd=cwd, shell=name, argv=argv, cols=cols, rows=rows, backend=PipeBackend()
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
            for sid in stale:
                self._sessions.pop(sid, None)  # tolerate an already-removed key
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

    def snapshot(self) -> None:
        """Persist all LIVE sessions to ``state_path`` (atomic write; no-op when
        persistence is disabled)."""
        if not self.state_path:
            return
        with self._lock:
            sessions = [s for s in self._sessions.values() if s.alive]
        # What this write covers, so snapshot_if_changed can tell "nothing
        # printed since" from "a pane printed" without writing to find out.
        self._last_snapshot_key = _snapshot_key(sessions)
        out: list[dict[str, Any]] = []
        for s in sessions:
            try:
                sb = s.scrollback_bytes()[-_SNAPSHOT_SCROLLBACK:]
                out.append(
                    {
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
                        "scrollback_b64": base64.b64encode(sb).decode("ascii"),
                    }
                )
            except Exception:  # pragma: no cover - skip an odd session, keep the rest
                continue
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"terminals": out}), encoding="utf-8")
        tmp.replace(self.state_path)

    def snapshot_if_changed(self) -> bool:
        """Write the snapshot only when it would differ from the last one
        (v1.245.0). The daemon's 30 s loop calls this, so a quiet afternoon
        costs nothing and a crash loses at most ~30 s of scrollback. Returns
        True when it wrote. BLOCKING (JSON + atomic write): call it off the loop.
        """
        if not self.state_path:
            return False
        with self._lock:
            sessions = [s for s in self._sessions.values() if s.alive]
        if _snapshot_key(sessions) == getattr(self, "_last_snapshot_key", None):
            return False
        self.snapshot()
        return True

    def restore(
        self, entry: dict[str, Any], *, env: dict | None = None, backend: PtyBackend | None = None
    ) -> TerminalSession | None:
        """Re-open ONE persisted session under its original id, with its prior
        scrollback preloaded. Returns None at the session cap."""
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
        session = self._spawn(
            cwd,
            name or "shell",
            list(argv),
            cols,
            rows,
            backend,
            _with_pane_env(env, pane_env),
        )
        session.id = rid
        session.pane_name = pane_name
        # v1.245.0: the shell is FRESH — whatever CLI ran here died with the
        # old daemon. The pane offers to resume it (resume_cli) and no longer
        # claims it is running: the chip used to say "claude" over a bare
        # shell for as long as the pane lived.
        session.agent_cli = None
        session.resume_cli = agent_cli
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
        sb = entry.get("scrollback_b64")
        if sb:
            try:
                session._tail = bytearray(base64.b64decode(sb))
                # A snapshot that FILLED its cap was sliced at a raw byte
                # offset — serve its replay from a safe boundary, not the cut.
                session._tail_truncated = len(session._tail) >= _SNAPSHOT_SCROLLBACK
            except Exception:  # pragma: no cover - bad data, keep the fresh shell
                pass
        with self._lock:
            self._sessions[rid] = session
        return session

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
        restored = 0
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            try:
                session = self.restore(entry, env=env, backend=backend)
                if session is not None:
                    # No pane is attached at boot, so without a reader the fresh
                    # shell's output (banner + prompt) never reaches the tail —
                    # a studio session resumed against the STALE replayed tail
                    # would then type briefs into a bare shell. Drain from the
                    # start; it yields whenever a Build pane attaches.
                    session.start_autodrain()
                    restored += 1
            except Exception:  # pragma: no cover - one bad entry mustn't skip the rest
                log.debug("failed to restore a terminal", exc_info=True)
        if restored:
            self._persist()  # rewrite with the freshly-restored (same) set
        return restored
