"""v1.368.0 ship-step pins (the Phase 5 review's leftovers).

The ``claude auth status`` probe (``providers/cli_auth``) passed the PARENT
Claude Code session's markers to its child when the daemon was started from
inside one — the messaging token among them. The same leak v1.303.1 closed
for Build panes and ``claude_models.child_env``.
"""

from __future__ import annotations

from iron_jarvis.core import procs
from iron_jarvis.providers import cli_auth


class _Res:
    returncode = 0
    stdout = b"{}"
    stderr = b""

    def check_timeout(self):
        return self


def test_the_sign_in_probe_never_inherits_a_parent_claude_sessions_markers(monkeypatch):
    seen: dict = {}

    def fake_run(argv, **kw):
        seen["env"] = kw.get("env")
        return _Res()

    monkeypatch.setattr(procs, "run", fake_run)
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_CHILD_SESSION", "1")
    monkeypatch.setenv("CLAUDE_CODE_MESSAGING_TOKEN", "not-for-the-probe")
    monkeypatch.setenv("IJ_KEEP_ME", "kept")
    cli_auth._run_probe(["claude", "auth", "status"])
    env = seen["env"]
    assert env is not None, "the probe must be handed an explicit environment"
    upper = {k.upper() for k in env}
    assert not upper & {"CLAUDECODE", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_MESSAGING_TOKEN"}
    assert env.get("IJ_KEEP_ME") == "kept", "only the markers are dropped"
