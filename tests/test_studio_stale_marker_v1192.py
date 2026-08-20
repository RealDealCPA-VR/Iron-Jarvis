"""The Creative Studio terminal driver must never trust a STALE working marker.

The terminal tail is APPEND-ONLY: every "esc to interrupt" status-bar repaint a
turn painted stays in it long after that turn is over. Two places read the
marker and both used to treat a leftover as a live turn.

* :func:`studio_say`'s exited-CLI refusal — the SAFETY gate. It called
  ``derive_phase(tail, ready=True)`` with no ``output_age``, and derive_phase
  checks the marker BEFORE the prompt-at-end check, trusting it forever when the
  age is unknown. A CLI that crashed back to the shell mid-turn therefore read
  as "thinking", the 409 never fired, and the brief was bracket-pasted + Entered
  into a BARE SHELL PROMPT, where it runs as a shell command in the user's
  folder.
* :func:`_type_and_submit`'s swallowed-Enter recovery — it confirmed the turn
  started with ``marker in tail``, which the PREVIOUS turn's repaints already
  satisfy, so from the second brief onward the confirmation was vacuously true
  on the first poll and the one-Enter nudge could never fire.
"""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app


def test_studio_say_refuses_an_exited_cli_whose_stale_marker_lingers(tmp_path):
    """SAFETY: a mid-turn crash leaves the last 'esc to interrupt' repaint in the
    tail right next to the shell prompt that replaced it. The say gate must judge
    that marker by its AGE (as studio_tail does) and still refuse — otherwise the
    brief is typed into a bare PowerShell prompt and RUNS."""
    client = TestClient(create_app(str(tmp_path)))
    term = client.post("/terminals", json={"cwd": str(tmp_path)})
    tid = term.json()["id"]
    try:
        app = client.app
        session = app.state.platform.terminals.get(tid)
        session._studio_ready = True
        # The exact shape of a crash-out: a status-bar repaint from the turn that
        # died, then the crash text, then the shell prompt — all inside the 600
        # char window derive_phase looks at.
        session._tail = bytearray(
            b"* Cerebrating... (14s - esc to interrupt)\r\n"
            b"Killed.\r\n"
            b"PS C:\\work> "
        )
        # It DID print, a while ago — the marker is a leftover, not a live turn.
        session.last_output_at = time.monotonic() - 300.0

        r = client.post(f"/creative/studio/{tid}/say", json={"text": "make a video"})

        assert r.status_code == 409, r.text
        assert "exited" in r.json()["detail"]
    finally:
        client.delete(f"/terminals/{tid}")


def test_studio_say_still_accepts_a_genuinely_running_turn(tmp_path):
    """The freshness guard must not turn into a blanket refusal: a marker with
    FRESH output is a live turn, no prompt is waiting, and the brief goes in."""
    client = TestClient(create_app(str(tmp_path)))
    term = client.post("/terminals", json={"cwd": str(tmp_path)})
    tid = term.json()["id"]
    try:
        app = client.app
        session = app.state.platform.terminals.get(tid)
        session._studio_ready = True
        session._tail = bytearray(b"* Cerebrating... (14s - esc to interrupt)\r\n")
        session.last_output_at = time.monotonic()  # painting right now

        r = client.post(f"/creative/studio/{tid}/say", json={"text": "make a video"})

        assert r.status_code == 200, r.text
        assert r.json()["typed"] is True
    finally:
        client.delete(f"/terminals/{tid}")


class _StaleTailSession:
    """A studio terminal on its SECOND brief: the tail already carries the
    previous turn's status-bar repaints."""

    alive = True

    def __init__(self, *, submit_lands: bool) -> None:
        self.writes: list[str] = []
        # What one finished turn leaves behind — the append-only tail keeps
        # every repaint it painted.
        self.tail = "* Cerebrating... (3s - esc to interrupt)\n" * 40 + "Done.\n"
        self._submit_lands = submit_lands

    def output_tail(self) -> str:
        return self.tail

    def write(self, d) -> None:
        self.writes.append(d)
        if d == "\r" and self._submit_lands:
            self.tail += "* Cerebrating... (1s - esc to interrupt)\n"


def test_swallowed_submit_still_gets_its_recovery_enter_on_a_stale_tail(monkeypatch):
    """The documented one-Enter recovery must survive a tail full of the PREVIOUS
    turn's markers. Confirming with a bare ``marker in tail`` matched that
    leftover on the first poll and returned 'turn started' — the brief then sat
    unsubmitted in the composer and the generation silently never began."""
    from iron_jarvis.daemon.routes.creative import (
        _PASTE_BEGIN,
        _PASTE_END,
        _type_and_submit,
    )

    # _type_and_submit imports `time` locally; skip its real settle/poll waits.
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    brief = "A 15-second cinematic night pursuit"
    s = _StaleTailSession(submit_lands=False)  # the Enter was swallowed

    _type_and_submit(s, brief)

    assert s.writes[0] == _PASTE_BEGIN + brief + _PASTE_END
    # The submit Enter, then the recovery Enter the stale marker used to suppress.
    assert s.writes[1:] == ["\r", "\r"]


def test_a_real_new_turn_on_a_stale_tail_needs_no_recovery_enter(monkeypatch):
    """Counterpart: when the submit DOES land, the new repaint pushes the marker
    count above the baseline and no extra Enter is sent (an unconditional nudge
    would submit an empty message into a running turn)."""
    from iron_jarvis.daemon.routes.creative import (
        _PASTE_BEGIN,
        _PASTE_END,
        _type_and_submit,
    )

    # _type_and_submit imports `time` locally; skip its real settle/poll waits.
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    brief = "A 15-second cinematic night pursuit"
    s = _StaleTailSession(submit_lands=True)

    _type_and_submit(s, brief)

    assert s.writes == [_PASTE_BEGIN + brief + _PASTE_END, "\r"]
