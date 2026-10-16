"""v1.339.0 — a sidebar reopened mid-answer is shown the question and the
answer so far.

The sidebar's turn runs in the daemon (``PanelTurns``), so closing the side
panel never stopped it — but ``open`` replayed only FINISHED exchanges, and the
deltas that kept arriving landed in the reopened panel under no question,
starting mid-sentence. ``history`` now carries ``live`` while a turn runs.
"""

from __future__ import annotations

from iron_jarvis.browser import protocol as P
from tests._fakes.panel_harness import _BROWSER_SENTENCE, _FIRST, Gate, RealApp


def _open(app: RealApp) -> dict:
    n = len([1 for e, _p in app.panel.events() if e == P.PANEL_EVENT_HISTORY])
    app.panel.send(P.PANEL_ACTION_OPEN)
    return app.panel.wait_for(P.PANEL_EVENT_HISTORY, "open never replayed", nth=n + 1)


def test_reopening_mid_answer_replays_the_question_and_the_answer_so_far(tmp_path, monkeypatch):
    gate = Gate()
    with RealApp(tmp_path, monkeypatch) as app:
        app.pair()
        app.stub_tools()
        app.two_rounds(gate)
        app.panel.send(P.PANEL_ACTION_SEND, text=_BROWSER_SENTENCE)
        app.panel.wait_for(P.PANEL_EVENT_DELTA, "the turn never started generating")

        view = _open(app)  # the panel was closed and opened again, mid-turn
        assert view["turns"] == []
        assert view["live"] == {"question": _BROWSER_SENTENCE, "answer": _FIRST}

        gate.release()
        app.panel.wait_for(P.PANEL_EVENT_DONE, "the turn never finished")
        after = _open(app)
        # Finished: the exchange is history now, and nothing is live.
        assert "live" not in after
        assert [t["role"] for t in after["turns"]] == ["user", "assistant"]
        assert after["turns"][0]["text"] == _BROWSER_SENTENCE


def test_an_idle_sidebar_has_nothing_live(tmp_path, monkeypatch):
    with RealApp(tmp_path, monkeypatch) as app:
        app.pair()
        assert "live" not in _open(app)
