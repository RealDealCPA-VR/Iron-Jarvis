"""v1.442.0 (chat-first plan, Wave 7 live run): "Stop asking me before you
run tests" — the walkthrough's request 15. Driven live through a real model
(Claude Sonnet) on a scratch daemon, the model read ``config_list`` twice and
answered that no setting covers tests. The approvals level does: under Auto a
project's own test command passes with no card (``core/auto_approve``,
``test_command``), while Accept edits lets only file edits through
(``edits_only``). The setting's help now says so, and this pin holds the words
to the code that makes them true.
"""

from __future__ import annotations

from iron_jarvis.core import auto_approve as AA
from iron_jarvis.settings.schema import get


def _help() -> str:
    return get("device.approval_mode").help


def test_the_approvals_help_names_the_level_that_runs_tests_without_asking():
    words = _help()
    assert "Auto also runs the project's own test commands" in words
    assert "Accept edits lets file edits" in words and "still asks before commands" in words
    assert "Ask before commands asks before every command, a test run included" in words


def test_the_words_match_what_each_level_really_does(tmp_path):
    """The same call, judged by the real rules: a project's test run passes
    under Auto and keeps its card under Accept edits."""
    call = ("shell", {"command": "pytest -q"})
    auto = AA.classify(*call, mode=AA.AUTO, workspace=tmp_path, posix=False)
    edits = AA.classify(*call, mode=AA.ACCEPT_EDITS, workspace=tmp_path, posix=False)
    assert (auto.kind, auto.rule) == (AA.PASS, "test_command"), auto
    assert (edits.kind, edits.rule) == (AA.ASK, "edits_only"), edits


def test_config_list_shows_the_model_what_a_setting_does_and_its_choices(tmp_path):
    """The live run's cause: ``config_list`` gave key and value only. Each
    line now carries the help and the choices' names, so a search for the
    approvals level tells the model which level runs tests without asking."""
    import asyncio

    from iron_jarvis.daemon.app import create_app
    from iron_jarvis.tools.base import ToolContext

    app = create_app(str(tmp_path))
    p = app.state.platform
    ctx = ToolContext(workspace=tmp_path, session_id="chat", agent_run_id="chat", config=p.config,
                      event_bus=p.event_bus, engine=p.engine)
    tool = p.registry.get("config_list")
    for query in ("approvals", "stop asking before tests", "test runs"):
        out = asyncio.run(tool.execute({"query": query}, ctx)).output
        line = next((ln for ln in out.splitlines() if "(device.approval_mode)" in ln), "")
        assert line, (query, out[:600])
        assert "Auto also runs the project's own test commands" in line
        assert "auto = Auto" in line and "accept_edits = Accept edits" in line
    # A long help is cut, never the whole page.
    from iron_jarvis.settings import tools as T

    assert all(len(T._what_it_does(d.key)) < T._HELP_CHARS + 400 for d in T.schema.SETTINGS if not d.pattern)
