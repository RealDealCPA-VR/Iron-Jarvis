"""v1.303.0 — a parent Claude Code session's markers never reach a Build pane or a `claude` child.

Found by the LIVE end-to-end run of "Continue on the next account": a daemon started
from inside a Claude Code session handed `CLAUDE_CODE_CHILD_SESSION` (and the parent's
messaging token) to every Build pane; Claude Code in the pane printed "Transcript
saving is off — inherited CLAUDE_CODE_CHILD_SESSION marker", so there was no
conversation file to carry. Asserted on the env the BACKEND / the child received.
"""

from __future__ import annotations

from iron_jarvis.core.claude_markers import CLAUDE_SESSION_MARKERS, drop_claude_session_markers
from iron_jarvis.providers import claude_models
from iron_jarvis.terminals import TerminalManager
from iron_jarvis.terminals.backend import FakeBackend


class _RecordingBackend(FakeBackend):
    def __init__(self) -> None:
        super().__init__()
        self.env: dict | None = None

    def start(self, argv, cwd, env, cols, rows) -> None:  # type: ignore[override]
        self.env = dict(env) if env is not None else None
        super().start(argv, cwd, env, cols, rows)


def _with_markers(monkeypatch) -> None:
    for name in CLAUDE_SESSION_MARKERS:
        monkeypatch.setenv(name, "parent-value")
    monkeypatch.setenv("MY_OWN_TOOL_SETTING", "keep-me")
    monkeypatch.setenv("CLAUDE_EFFORT_UNRELATED_USER_VAR", "keep-me-too")


def test_a_build_pane_never_inherits_a_parent_claude_session(tmp_path, monkeypatch):
    _with_markers(monkeypatch)
    backend = _RecordingBackend()
    TerminalManager().create(cwd=str(tmp_path), backend=backend, name="p")
    env = {k.upper(): v for k, v in (backend.env or {}).items()}
    leaked = [m for m in CLAUDE_SESSION_MARKERS if m in env]
    assert not leaked, f"a Build pane inherited the parent Claude session's markers: {leaked}"
    # Everything else the user's shell had is still there.
    assert env["MY_OWN_TOOL_SETTING"] == "keep-me"
    assert env["CLAUDE_EFFORT_UNRELATED_USER_VAR"] == "keep-me-too"
    assert env["IRONJARVIS_BUILD"] == "1"


def test_the_claude_child_env_drops_them_too(monkeypatch):
    _with_markers(monkeypatch)
    env = claude_models.child_env()
    assert not [m for m in CLAUDE_SESSION_MARKERS if m in env]
    assert env["MY_OWN_TOOL_SETTING"] == "keep-me"


def test_the_marker_list_names_the_child_session_flag_and_the_messaging_token():
    # The two that did the damage live.
    assert "CLAUDE_CODE_CHILD_SESSION" in CLAUDE_SESSION_MARKERS
    assert "CLAUDE_CODE_MESSAGING_TOKEN" in CLAUDE_SESSION_MARKERS
    env = {"claude_code_child_session": "1", "PATH": "x"}
    assert drop_claude_session_markers(env) == {"PATH": "x"}  # case-insensitive, as Windows is
