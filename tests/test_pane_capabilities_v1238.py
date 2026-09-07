"""A pane's CAPABILITIES are real, they survive a restart, and its credential dies with it (v1.238.0).

Ship 4 lets an external harness — Claude Code, Codex, Pi — drive Jarvis from
inside a Build pane. What that harness may do is decided here, on the pane, and
this file exists because two failure modes in this area are silent:

* **The silent reset.** A pane is an in-RAM object PLUS a hand-written dict in
  ``terminals.json``. A field added to :class:`TerminalSession` and to
  ``create()`` but missed in ``snapshot()`` or ``restore()`` works perfectly all
  day and evaporates on the next daemon restart, with every unit test still
  green — because unit tests ask the live object. So the round trip is pinned
  through the real file: written, re-read by a NEW manager, and asserted after
  rehydrate.
* **The credential that outlives its shell.** ``TerminalManager`` has no
  pane-closed event and three ways for a pane to end. A token surviving any one
  of them is the whole vulnerability, so each of the three is driven against a
  store that can only be cleared by THAT path — otherwise a sibling chokepoint's
  wiring would satisfy the pin and a missing one would never be noticed.

Only ``browser`` is enforced in these ships. ``files``, ``shell``, ``extensions``
and ``memory`` are recorded and displayed, and nothing here pretends otherwise.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.browser.panetokens import MCP_TOKEN_ENV, MCP_URL_ENV, PaneTokenStore
from iron_jarvis.terminals import TerminalManager, TerminalSession
from iron_jarvis.terminals.backend import FakeBackend
from iron_jarvis.terminals.session import (
    ENFORCED_PANE_CAPABILITIES,
    PANE_CAPABILITY_KEYS,
)


class _RecordingBackend(FakeBackend):
    """A FakeBackend that keeps the environment it was started with.

    The environment is captured AT START, so anything found in ``.env`` was in
    the child's environment before the shell ran — which is the only ordering
    claim that matters for a launch recipe.
    """

    def __init__(self) -> None:
        super().__init__()
        self.env: dict | None = None

    def start(self, argv, cwd, env, cols, rows) -> None:  # type: ignore[override]
        self.env = dict(env) if env is not None else None
        super().start(argv, cwd, env, cols, rows)


@pytest.fixture(autouse=True)
def _fake_default_backend(monkeypatch):
    """restore()/rehydrate() spawn through `default_backend` — no real shells."""
    monkeypatch.setattr(
        "iron_jarvis.terminals.session.default_backend", lambda: _RecordingBackend()
    )


def _snapshot_entry(path: Path, pane_id: str) -> dict:
    """The one pane's row as it was actually written to `terminals.json`."""
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = [row for row in data["terminals"] if row["id"] == pane_id]
    assert rows, f"pane {pane_id} is not in the snapshot at all"
    return rows[0]


# --------------------------------------------------------------------------- #
# The field itself
# --------------------------------------------------------------------------- #
def test_a_new_pane_has_every_capability_off(tmp_path):
    """No capability is ever gained by default, or by upgrading the app."""
    m = TerminalManager()
    s = m.create(cwd=str(tmp_path), backend=_RecordingBackend())

    assert s.info()["capabilities"] == {key: False for key in PANE_CAPABILITY_KEYS}
    assert s.capability("browser") is False


def test_capability_reads_the_value_and_not_merely_the_key(tmp_path):
    """`capability()` is THE question every gate asks, and it was unpinned.

    A `name in self.capabilities` implementation satisfies "a new pane has
    everything off" (the mapping is empty) and satisfies the PATCH tests (they
    read `info()`), so the accessor the chat gate and the MCP grant both call
    could have been replaced by a membership test with the suite green. The
    mapping below CONTAINS every key; only reading the VALUE gets this right.
    """
    s = TerminalSession(cwd=str(tmp_path), backend=FakeBackend())
    s.capabilities = {key: False for key in PANE_CAPABILITY_KEYS}
    assert "browser" in s.capabilities  # the key is there
    assert s.capability("browser") is False  # the answer is still no
    assert s.capability("shell") is False

    s.capabilities = {"browser": True, "shell": False}
    assert s.capability("browser") is True
    assert s.capability("shell") is False
    # And an absent key is a no, not a KeyError and not an unknown.
    assert s.capability("memory") is False


def test_a_hand_made_session_grants_nothing(tmp_path):
    """The gate's seam is fail-closed for a pane that predates the field.

    `TerminalSession.capability` is what `_filter_browser_tools` and the MCP
    route ask. A session built by hand (or restored from an old snapshot) has
    `capabilities is None`, and None must read as no, never as unknown.
    """
    s = TerminalSession(cwd=str(tmp_path), backend=FakeBackend())
    assert s.capabilities is None
    assert s.capability("browser") is False
    assert s.effective_capabilities() == {key: False for key in PANE_CAPABILITY_KEYS}


def test_a_pane_keeps_the_capabilities_it_was_created_with(tmp_path):
    m = TerminalManager()
    s = m.create(
        cwd=str(tmp_path),
        backend=_RecordingBackend(),
        capabilities={"browser": True, "files": True},
    )

    assert s.capability("browser") is True
    assert s.capability("files") is True
    assert s.capability("shell") is False
    assert s.info()["capabilities"]["browser"] is True


def test_a_capability_outside_the_canonical_five_is_dropped(tmp_path):
    """The set is fixed, so an invented name cannot round-trip looking official.

    Built BY HAND with a raw mapping rather than through `create()`, which
    normalises on the way in: with a normalised pane, `info()` could hand back
    `self.capabilities` verbatim and this still passed. The raw mapping here is
    the one a hand-edited `terminals.json` produces, and only
    `effective_capabilities()` can satisfy the assertions.
    """
    s = TerminalSession(cwd=str(tmp_path), backend=FakeBackend())
    s.capabilities = {"root": True, "browser": "false", "shell": "yes"}

    rendered = s.info()["capabilities"]
    assert "root" not in rendered
    assert set(rendered) == set(PANE_CAPABILITY_KEYS)
    assert rendered["browser"] is False, "a stored string must not render as ticked"
    assert rendered["shell"] is True
    assert s.capability("root") is False


def test_only_browser_is_enforced_in_these_ships():
    """The copy on the checklist says four of the five are not yet enforced.

    This constant is what that copy is checked against; widening it without
    building the gate is how a surface starts claiming enforcement that does not
    exist (the v1.218.0 lesson).
    """
    assert ENFORCED_PANE_CAPABILITIES == ("browser",)
    assert set(ENFORCED_PANE_CAPABILITIES).issubset(set(PANE_CAPABILITY_KEYS))


# --------------------------------------------------------------------------- #
# GATE 2: the chat lanes and the checklist give the SAME answer (v1.238.0)
# --------------------------------------------------------------------------- #
def _chat_deps(manager: TerminalManager, registry=None, access: str = "interactive"):
    """The dependency object both chat lanes hand `_resolve_armed_tools`.

    `terminals` is the REAL manager holding REAL `TerminalSession` objects — the
    thing gate 2 looks the pane up in — rather than a `get()` stand-in, because
    the defect this pins was precisely a gate reading a pane differently from
    the way the pane reads itself.
    """
    from types import SimpleNamespace

    return SimpleNamespace(
        platform=SimpleNamespace(
            terminals=manager,
            registry=registry,
            browser=SimpleNamespace(
                access=lambda: access, connected=True,
                backend=SimpleNamespace(active_tab={"title": "T", "url": "https://x/"}),
            ),
        )
    )


def test_an_unconfigured_pane_is_denied_by_the_gate_and_by_its_own_checkbox(tmp_path):
    """THE SHIP-4 S1, pinned at the join.

    A pane created the way the Build page creates one records nothing. Until
    v1.238.0 the checklist rendered Browser UNTICKED and the chat gate armed the
    whole `browser_*` roster on that same pane. Both halves are asserted here,
    against ONE pane, so they cannot drift apart again without this failing.
    """
    from iron_jarvis.daemon.chat_turn import (
        _browser_section,
        _filter_browser_tools,
        _pane_browser_allowed,
    )
    from iron_jarvis.daemon.schemas import ChatBody

    m = TerminalManager()
    pane = m.create(cwd=str(tmp_path), backend=_RecordingBackend())
    d = _chat_deps(m)

    # What the popover renders.
    assert pane.info()["capabilities"]["browser"] is False
    # What the gate answers about the very same pane.
    assert _pane_browser_allowed(d, pane.id) is False
    # Lane A — the tool filter both chat lanes apply last.
    body = ChatBody(messages=[], pane_id=pane.id)
    assert _filter_browser_tools(
        d, body, ["browser_read_page", "browser_click", "read_file"]
    ) == ["read_file"]
    # Lane B — the ambient prompt section. A pane that cannot call a browser
    # tool must not be told in the system prompt that it has one.
    assert _browser_section(d, pane.id) == ""


def test_ticking_the_box_is_what_arms_the_roster(tmp_path):
    """The other direction, so the test above cannot pass by arming nothing."""
    from iron_jarvis.daemon.chat_turn import (
        BROWSER_HEADING,
        _browser_section,
        _filter_browser_tools,
        _pane_browser_allowed,
    )
    from iron_jarvis.daemon.schemas import ChatBody

    m = TerminalManager()
    pane = m.create(cwd=str(tmp_path), backend=_RecordingBackend())
    d = _chat_deps(m)
    body = ChatBody(messages=[], pane_id=pane.id)

    pane.update_capabilities({"browser": True})

    assert pane.info()["capabilities"]["browser"] is True
    assert _pane_browser_allowed(d, pane.id) is True
    assert _filter_browser_tools(d, body, ["browser_read_page", "read_file"]) == [
        "browser_read_page",
        "read_file",
    ]
    assert BROWSER_HEADING in _browser_section(d, pane.id)

    # And unticking it takes effect on the next turn, with no relaunch — the
    # same live read the harness's token gets.
    pane.update_capabilities({"browser": False})
    assert _pane_browser_allowed(d, pane.id) is False
    assert _filter_browser_tools(d, body, ["browser_read_page", "read_file"]) == [
        "read_file"
    ]


def test_a_pane_id_that_names_no_pane_is_not_a_way_around_the_gate(tmp_path):
    """`pane_id` is a client-supplied string. If an unknown one answered yes,
    gate 2 would be skippable by typing anything at all into that field."""
    from iron_jarvis.daemon.chat_turn import _filter_browser_tools, _pane_browser_allowed
    from iron_jarvis.daemon.schemas import ChatBody

    m = TerminalManager()
    m.create(cwd=str(tmp_path), backend=_RecordingBackend(), capabilities={"browser": True})
    d = _chat_deps(m)

    assert _pane_browser_allowed(d, "term_not_a_pane") is False
    body = ChatBody(messages=[], pane_id="term_not_a_pane")
    assert _filter_browser_tools(d, body, ["browser_read_page", "read_file"]) == [
        "read_file"
    ]
    # A pane-LESS surface (the main chat page, the phone lane) is untouched:
    # gate 2 does not apply there, and gate 1 still does.
    assert _pane_browser_allowed(d, "") is True


def test_both_chat_lanes_go_through_the_one_filter(tmp_path):
    """One implementation, imported by both lanes.

    The gate above is worth what its reach is worth, and its reach is the two
    call sites of `_resolve_armed_tools`. Source-pinned because the streaming
    lane lives in another module and a lane that quietly stopped calling it
    would leave every behavioural test in this file green.
    """
    from pathlib import Path as _P

    import iron_jarvis.daemon.chat_turn as ct
    import iron_jarvis.daemon.routes.chat as rc

    for mod in (ct, rc):
        src = _P(mod.__file__).read_text(encoding="utf-8").replace("\r\n", "\n")
        assert "_resolve_armed_tools, d, body, _tool_cap" in src, (
            f"{mod.__name__} no longer arms through the shared resolver"
        )
    # And the resolver applies the browser filter LAST, after every fill pass.
    body = _P(ct.__file__).read_text(encoding="utf-8").replace("\r\n", "\n")
    assert "armed = _filter_browser_tools(d, body, explicit + auto)" in body


# --------------------------------------------------------------------------- #
# THE SILENT-RESET TRAP: create -> snapshot -> restore
# --------------------------------------------------------------------------- #
def test_capabilities_survive_a_daemon_restart(tmp_path):
    """The round trip, through the real file, into a NEW manager.

    Two assertions on purpose: the first fails if `snapshot()` drops the key,
    the second if `restore()` never reads it back. Either omission alone leaves
    a pane that is correct until the next restart and then silently ungranted.
    """
    sp = tmp_path / "terminals.json"
    m1 = TerminalManager(state_path=sp)
    s = m1.create(
        cwd=str(tmp_path),
        capabilities={"browser": True, "files": True},
    )
    m1.snapshot()

    assert _snapshot_entry(sp, s.id)["capabilities"] == {
        "files": True,
        "shell": False,
        "browser": True,
        "extensions": False,
        "memory": False,
    }

    m2 = TerminalManager(state_path=sp)  # the daemon after a restart
    assert m2.rehydrate() == 1
    restored = m2.get(s.id)
    assert restored is not None
    assert restored.capability("browser") is True
    assert restored.capability("files") is True
    assert restored.capability("shell") is False


def test_a_snapshot_written_before_this_field_restores_with_no_capabilities(tmp_path):
    """An upgrade must not invent a capability the user never granted."""
    m = TerminalManager()
    restored = m.restore(
        {"id": "term_old", "shell": "sh", "argv": ["sh"], "cwd": str(tmp_path)}
    )

    assert restored is not None
    assert restored.capabilities is None
    assert restored.capability("browser") is False


def test_a_stored_string_does_not_come_back_as_a_granted_capability(tmp_path):
    """`bool("false")` is True, and these values arrive from JSON.

    A pane whose stored value is the STRING "false" (a hand-edited
    `terminals.json`, an older writer, a client that posted strings) must come
    back disabled. This is why truthiness has one owner
    (`browser.panetokens.capability_enabled`) instead of a local `bool()`.
    """
    m = TerminalManager()
    restored = m.restore(
        {
            "id": "term_str",
            "shell": "sh",
            "argv": ["sh"],
            "cwd": str(tmp_path),
            "capabilities": {"browser": "false", "files": "no", "shell": "yes"},
        }
    )

    assert restored is not None
    assert restored.capability("browser") is False
    assert restored.capability("files") is False
    assert restored.capability("shell") is True  # an honest yes still means yes


# --------------------------------------------------------------------------- #
# PATCH is PARTIAL, twice over
# --------------------------------------------------------------------------- #
@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "iron_jarvis.terminals.session.default_backend", lambda: _RecordingBackend()
    )
    from iron_jarvis.daemon.app import create_app

    app = create_app(str(tmp_path))
    with TestClient(app) as c:
        yield c


def _make(client, **body) -> dict:
    r = client.post("/terminals", json={"cwd": None, **body})
    assert r.status_code == 200, r.text
    return r.json()


def test_capabilities_are_settable_and_readable_over_the_api(client):
    pane = _make(client, capabilities={"browser": True})
    assert pane["capabilities"]["browser"] is True

    listed = client.get("/terminals").json()["terminals"]
    assert [p["capabilities"]["browser"] for p in listed if p["id"] == pane["id"]] == [
        True
    ]


def test_toggling_one_capability_does_not_clear_the_others(client):
    """The popover toggles ONE box. A whole-mapping replacement here would wipe
    four capabilities the user never touched — the shape of bug that destroyed a
    credential in the remote-agent registry."""
    pane = _make(client, capabilities={"browser": True, "files": True})

    r = client.patch(f"/terminals/{pane['id']}", json={"browser_unused": True})
    assert r.status_code == 200, r.text  # no capabilities key at all: keep everything
    assert r.json()["capabilities"]["browser"] is True

    r = client.patch(f"/terminals/{pane['id']}", json={"capabilities": {"shell": True}})
    assert r.status_code == 200, r.text
    caps = r.json()["capabilities"]
    assert caps["shell"] is True
    assert caps["browser"] is True, "an omitted key must keep its value"
    assert caps["files"] is True


def test_a_capability_can_be_turned_off_explicitly(client):
    pane = _make(client, capabilities={"browser": True, "files": True})
    r = client.patch(
        f"/terminals/{pane['id']}", json={"capabilities": {"browser": False}}
    )
    assert r.status_code == 200, r.text
    assert r.json()["capabilities"]["browser"] is False
    assert r.json()["capabilities"]["files"] is True


def test_renaming_a_pane_does_not_disturb_its_capabilities(client):
    pane = _make(client, capabilities={"browser": True})
    r = client.patch(f"/terminals/{pane['id']}", json={"name": "builder"})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "builder"
    assert r.json()["capabilities"]["browser"] is True


def test_a_capability_change_is_persisted_at_once(client, tmp_path):
    """Not "until the next restart". The PATCH route snapshots for exactly this."""
    pane = _make(client)
    client.patch(f"/terminals/{pane['id']}", json={"capabilities": {"browser": True}})

    files = list(Path(tmp_path).rglob("terminals.json"))
    assert files, "the daemon wrote no terminals snapshot"
    assert _snapshot_entry(files[0], pane["id"])["capabilities"]["browser"] is True


# --------------------------------------------------------------------------- #
# Revocation: all three close paths, each proven alone
# --------------------------------------------------------------------------- #
class _OnlyRevokePane(PaneTokenStore):
    """A store the sync and revoke-all paths cannot clear.

    So a token that is gone after `kill()` is gone BECAUSE kill revoked it, not
    because the `purge_dead()` it happens to call swept it up. Without this,
    wiring any one chokepoint would satisfy all three pins.
    """

    def sync_panes(self, *args, **kwargs) -> int:
        return 0

    def revoke_all(self, *args, **kwargs) -> int:
        return 0


class _OnlySyncPanes(PaneTokenStore):
    def revoke_pane(self, *args, **kwargs) -> int:
        return 0

    def revoke_all(self, *args, **kwargs) -> int:
        return 0


class _OnlyRevokeAll(PaneTokenStore):
    def revoke_pane(self, *args, **kwargs) -> int:
        return 0

    def sync_panes(self, *args, **kwargs) -> int:
        return 0


class _FakeSessionRegistry:
    """Stand-in for the outward MCP server's session registry."""

    def __init__(self) -> None:
        self.revoked: list[str] = []
        self.revoked_all = 0

    def revoke_pane(self, *args, **kwargs) -> int:
        self.revoked.append(args[0] if args else kwargs.get("pane_id", ""))
        return 1

    def revoke_all(self, *args, **kwargs) -> int:
        self.revoked_all += 1
        return 1


def _harness_pane(manager: TerminalManager, tmp_path) -> TerminalSession:
    """A pane launched with a recipe — i.e. one holding a capability token."""
    return manager.create(
        cwd=str(tmp_path),
        backend=_RecordingBackend(),
        capabilities={"browser": True},
        recipe="claude",
    )


def test_closing_a_pane_revokes_its_token(tmp_path):
    store = _OnlyRevokePane()
    m = TerminalManager(pane_tokens=store)
    s = _harness_pane(m, tmp_path)
    assert store.has_pane(s.id), "the harness pane never received a token"

    assert m.kill(s.id) is True
    assert not store.has_pane(s.id)


def test_a_shell_that_died_on_its_own_loses_its_token(tmp_path):
    """`purge_dead` is where that death is NOTICED — nobody called kill().

    And the pane is RETAINED here (it is within `max_dead_retained`), so an
    eviction-driven revoke would miss it entirely. The live list is the
    authority, which is why this path calls `sync_panes`.
    """
    store = _OnlySyncPanes()
    m = TerminalManager(pane_tokens=store)
    s = _harness_pane(m, tmp_path)
    assert store.has_pane(s.id)

    s.backend._alive = False  # the shell exited by itself; nothing was told
    assert m.purge_dead() == 0, "the dead pane should still be retained"
    assert m.get(s.id) is not None
    assert not store.has_pane(s.id)


def test_shutting_every_pane_down_revokes_every_token(tmp_path):
    store = _OnlyRevokeAll()
    m = TerminalManager(pane_tokens=store)
    first = _harness_pane(m, tmp_path)
    second = _harness_pane(m, tmp_path)
    assert sorted(store.pane_ids()) == sorted([first.id, second.id])

    m.kill_all()
    assert store.pane_ids() == []


def test_the_mcp_session_registry_is_revoked_on_the_same_paths(tmp_path):
    """An MCP session outliving its pane would be a second live handle."""
    registry = _FakeSessionRegistry()
    m = TerminalManager(mcp_sessions=registry)
    s = _harness_pane(m, tmp_path)

    m.kill(s.id)
    assert s.id in registry.revoked

    other = _harness_pane(m, tmp_path)
    m.kill_all()
    assert registry.revoked_all == 1
    assert other.id != s.id


def test_a_store_that_raises_never_breaks_a_close(tmp_path):
    class _Angry(PaneTokenStore):
        def revoke_pane(self, *args, **kwargs) -> int:
            raise RuntimeError("boom")

        def sync_panes(self, *args, **kwargs) -> int:
            raise RuntimeError("boom")

    m = TerminalManager(pane_tokens=_Angry())
    s = _harness_pane(m, tmp_path)
    assert m.kill(s.id) is True  # the pane still closes


# --------------------------------------------------------------------------- #
# Restart recovery: a restored pane is NOT re-credentialed
# --------------------------------------------------------------------------- #
def test_a_restored_pane_gets_no_token(tmp_path):
    """The pane id comes back; the shell process does not.

    Silently re-minting would hand a live credential to whatever now occupies
    that pane id. `/mcp` answers 401 until the user relaunches the harness.
    """
    sp = tmp_path / "terminals.json"
    m1 = TerminalManager(state_path=sp)
    s = _harness_pane(m1, tmp_path)
    assert m1.pane_tokens.has_pane(s.id)
    m1.snapshot()

    m2 = TerminalManager(state_path=sp)
    assert m2.rehydrate() == 1
    restored = m2.get(s.id)
    assert restored is not None
    assert restored.capability("browser") is True  # the CONFIG survives
    assert not m2.pane_tokens.has_pane(s.id)  # the CREDENTIAL does not
    assert MCP_TOKEN_ENV not in (restored.backend.env or {})


# --------------------------------------------------------------------------- #
# The recipe seam: the token is in the child environment before the spawn
# --------------------------------------------------------------------------- #
def test_the_capability_token_reaches_the_child_before_the_shell_starts(tmp_path):
    """There is exactly one moment a child's environment can be set, and this
    asserts against the environment the BACKEND was handed — not against a dict
    on the session, which is the check that passed while v1.217.0's identity
    reached no shell at all."""
    backend = _RecordingBackend()
    m = TerminalManager(mcp_url="http://127.0.0.1:8787/mcp")
    s = m.create(
        cwd=str(tmp_path),
        backend=backend,
        capabilities={"browser": True},
        recipe="claude",
    )

    assert backend.env is not None
    token = backend.env[MCP_TOKEN_ENV]
    assert backend.env[MCP_URL_ENV] == "http://127.0.0.1:8787/mcp"

    grant = m.pane_tokens.resolve(token)
    assert grant is not None
    assert grant.pane_id == s.id
    assert grant.allows("browser") is True
    assert token != s.id and s.id not in token  # never derived from the pane id


def test_a_pane_without_a_recipe_is_given_no_credential(tmp_path):
    """A CLI with no recipe launches exactly as it does today."""
    backend = _RecordingBackend()
    m = TerminalManager(mcp_url="http://127.0.0.1:8787/mcp")
    m.create(cwd=str(tmp_path), backend=backend, capabilities={"browser": True})

    assert MCP_TOKEN_ENV not in backend.env
    assert MCP_URL_ENV not in backend.env
    assert len(m.pane_tokens) == 0


def test_the_recipe_preparer_is_given_the_pane_and_its_token(tmp_path):
    calls: list[dict] = []

    def preparer(*args, **kwargs):
        calls.append(dict(kwargs))
        return {"CLAUDE_CONFIG": "/tmp/.mcp.json"}

    backend = _RecordingBackend()
    m = TerminalManager(recipe_preparer=preparer)
    s = m.create(
        cwd=str(tmp_path),
        backend=backend,
        capabilities={"browser": True},
        recipe="claude",
    )

    assert len(calls) == 1
    assert calls[0]["recipe"] == "claude"
    assert calls[0]["pane_id"] == s.id
    assert calls[0]["token"] == backend.env[MCP_TOKEN_ENV]
    assert calls[0]["capabilities"]["browser"] is True
    # What the recipe returns is in the child environment too, and before start.
    assert backend.env["CLAUDE_CONFIG"] == "/tmp/.mcp.json"


def test_a_recipe_that_blows_up_still_leaves_a_usable_pane(tmp_path):
    """Degrade, never lose the shell: the harness simply has no configuration,
    which is what the pane's diagnostics are for."""

    def preparer(*args, **kwargs):
        raise RuntimeError("no claude here")

    backend = _RecordingBackend()
    m = TerminalManager(recipe_preparer=preparer)
    s = m.create(cwd=str(tmp_path), backend=backend, recipe="claude")

    assert s.alive
    assert backend.env[MCP_TOKEN_ENV]  # the token was already minted


# --------------------------------------------------------------------------- #
# The grant is read LIVE
# --------------------------------------------------------------------------- #
def test_unticking_a_capability_takes_effect_without_re_minting(tmp_path):
    """The harness keeps its credential; what the credential BUYS changes.

    Re-minting on every toggle would break the running harness, and caching the
    mint-time snapshot would let a revoked capability keep working until relaunch.
    """
    m = TerminalManager()
    backend = _RecordingBackend()
    s = m.create(
        cwd=str(tmp_path),
        backend=backend,
        capabilities={"browser": True},
        recipe="claude",
    )
    token = backend.env[MCP_TOKEN_ENV]
    assert m.pane_tokens.resolve(token).allows("browser") is True

    s.update_capabilities({"browser": False})

    grant = m.pane_tokens.resolve(token)
    assert grant is not None, "the token itself must still be valid"
    assert grant.live is True, "the grant must be read off the live pane"
    assert grant.allows("browser") is False


def test_a_token_stops_resolving_once_its_pane_is_gone(tmp_path):
    m = TerminalManager()
    backend = _RecordingBackend()
    s = m.create(
        cwd=str(tmp_path),
        backend=backend,
        capabilities={"browser": True},
        recipe="claude",
    )
    token = backend.env[MCP_TOKEN_ENV]

    m.kill(s.id)
    assert m.pane_tokens.resolve(token) is None
