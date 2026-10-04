"""v1.303.2 final-review guards for "Start fresh with what we were doing".

1. A fault a fresh start cannot fix (a 400 carrying billing_error, a 408) is
   never a resume failure — checked before the 4xx range.
2. "Already at the shell prompt" is trusted only when none of Claude Code's own
   screen furniture is visible: a row Claude drew can end like a prompt, and
   typing the restart line then would send it to the model.
"""

from __future__ import annotations

from iron_jarvis.daemon.routes import terminals as routes
from iron_jarvis.terminals.resume_failed import FAILED, classify_record


def _rec(status, error, text):
    return {
        "isApiErrorMessage": True,
        "error": error,
        "apiErrorStatus": status,
        "message": {"content": [{"type": "text", "text": text}]},
    }


def test_a_billing_400_or_a_timeout_408_is_not_a_resume_failure():
    assert classify_record(_rec(400, "billing_error", "API Error: 400 Your credit balance is too low")) is None
    assert classify_record(_rec(408, "unknown", "API Error: 408 Request Timeout")) is None
    assert classify_record(_rec(None, "server_error", "API Error: Connection lost mid-response")) is None
    # Control: a real conversation refusal is still a failure.
    assert classify_record(_rec(403, "unknown", "API Error: 403 Your organization does not have access to this model")) == FAILED
    assert classify_record(_rec(400, "invalid_request", "API Error: 400 invalid_request_error")) == FAILED


def test_claude_furniture_is_recognised_and_a_bare_prompt_is_not():
    drawn = "\u23fa Bash(echo)\n  \u23bf  the prompt reads C:\\Users\\VR>"
    assert routes._claude_ui_visible(drawn) is True
    assert routes._claude_ui_visible("? for shortcuts\nPS C:\\Users\\VR>") is True
    assert routes._claude_ui_visible("Windows PowerShell\nPS C:\\Users\\VR>") is False


class _Pane:
    def __init__(self, tail: str) -> None:
        self._tail = tail
        self.shell = "pwsh"
        self.output_seq = 1
        self.alive = True
        self.writes: list[str] = []

    def output_tail(self) -> str:
        return self._tail

    def write(self, data) -> None:
        self.writes.append(data)


def _fast(monkeypatch):
    monkeypatch.setattr(routes, "_EXIT_WAIT_S", 0.2)
    monkeypatch.setattr(routes, "_EXIT_SETTLE_S", 0.0)
    monkeypatch.setattr(routes, "_EXIT_POLL_S", 0.01)


def test_a_claude_drawn_prompt_lookalike_never_skips_the_exit_in_cmd(monkeypatch):
    _fast(monkeypatch)
    pane = _Pane("\u23fa Bash(echo)\n  \u23bf  the prompt reads C:\\Users\\VR>")
    pane.shell = "cmd"  # a cmd prompt is "C:\...>", so this row LOOKS like one
    routes._quit_claude(pane)
    assert "/exit" in pane.writes, "a Claude-drawn row ending like a prompt must not skip /exit"


def test_a_draft_in_claudes_input_box_never_skips_the_exit_in_pwsh(monkeypatch):
    _fast(monkeypatch)
    rule = "\u2500" * 40
    pane = _Pane(f"{rule}\n> run PS C:\\x>\n{rule}")
    pane._tail = f"{rule}\n> run PS C:\\x>"  # the draft is the last line, the box rule above it
    routes._quit_claude(pane)
    assert "/exit" in pane.writes, "a draft that ends like a PowerShell prompt must not skip /exit"


def test_a_real_shell_prompt_still_returns_at_once(monkeypatch):
    monkeypatch.setattr(routes, "_EXIT_WAIT_S", 0.2)
    pane = _Pane("Windows PowerShell\nPS C:\\Users\\VR>")
    assert routes._quit_claude(pane) is True
    assert pane.writes == []
