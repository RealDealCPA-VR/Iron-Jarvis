"""Review pins for ``settings.records_build`` (v1.438.0, chat-first plan Wave 4
group A): the defects the independent review found, each red on the first cut.

* A Local only chat queued a night task that runs on a CLOUD model (the
  task's words leave the network, unattended, at night).
* ``build_team_set`` made model-team members BEFORE it knew the team could be
  saved (three workers, an unknown member) — and a refusal at the save said
  nothing about the members it had made.
* ``build_team_set`` read the agent registry ON the event loop.
* The night card's own check failing fell back to the ordinary card, which
  the Auto posture's judge may approve — unattended work without its card.
* Arming: an everyday "cancel the task", "update the client file tonight",
  "add tests to tonight's agenda" offered the night writers; "queue 'add
  docstrings to utils.py' for tonight" (a file name's dot cut the clause) and
  "stop the running night task" offered nothing.
* The honesty note said "No night task was queued" under "I've queued the
  print job for tonight" and "No folder's coding team was changed" under "I
  updated the team section of the README".
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.change_claims import claimed_change_note
from iron_jarvis.harness import night as N
from iron_jarvis.providers import egress
from iron_jarvis.settings import records as R
from iron_jarvis.settings import records_build as RB
from tests.test_build_git_v1375 import make_repo

NIGHT_WRITERS = {"night_task_add", "night_task_cancel", "night_task_move"}
FOLDER_WRITERS = {"build_defaults_set", "build_defaults_clear", "build_team_set", "build_team_clear"}
WRITERS = NIGHT_WRITERS | FOLDER_WRITERS


@pytest.fixture()
def repo(tmp_path):
    return make_repo(tmp_path / "shop")


def _ctx():
    return SimpleNamespace(project_id=None, session_id="chat", workspace=None)


def _tool(p, name):
    tool = p.registry.get(name)
    assert tool is not None, name
    return tool


# --------------------------------------------------------------------------- #
# Local only: a night task never runs on a model that leaves the network
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_local_only_chat_never_queues_a_night_task_on_a_cloud_model(tmp_path, repo):
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    tool = _tool(p, "night_task_add")
    p.config.default_provider, p.config.default_model = "openai", "gpt-5"
    with egress.local_only_scope(True):
        out = await tool.execute({"words": "update the README", "folder": str(repo)}, _ctx())
    assert not out.ok and "Local only" in (out.error or ""), out
    assert N.list_tasks(p.engine) == [], "nothing was queued"
    # A named cloud model is refused the same way.
    with egress.local_only_scope(True):
        out = await tool.execute(
            {"words": "update the README", "folder": str(repo), "provider": "openai", "model": "gpt-5"}, _ctx()
        )
    assert not out.ok and "Local only" in (out.error or "")
    # A model team may hand pieces to cloud members: refused too.
    p.config.default_provider, p.config.default_model = "mock", ""
    with egress.local_only_scope(True):
        out = await tool.execute({"words": "update the README", "folder": str(repo), "team": True}, _ctx())
    assert not out.ok and "team" in (out.error or "").lower()
    # Try on several models: every model must stay on the network.
    with egress.local_only_scope(True):
        out = await tool.execute(
            {"words": "update the README", "folder": str(repo),
             "try_on": [{"provider": "mock", "model": ""}, {"provider": "openai", "model": "gpt-5"}]},
            _ctx(),
        )
    assert not out.ok and "Local only" in (out.error or "")
    assert N.list_tasks(p.engine) == []


@pytest.mark.asyncio
async def test_a_local_only_chat_may_still_queue_a_night_task_on_a_local_model(tmp_path, repo):
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    p.config.default_provider, p.config.default_model = "mock", ""
    with egress.local_only_scope(True):
        out = await _tool(p, "night_task_add").execute({"words": "update the README", "folder": str(repo)}, _ctx())
    assert out.ok, out.error
    assert [t["words"] for t in N.list_tasks(p.engine)] == ["update the README"]


# --------------------------------------------------------------------------- #
# build_team_set: nothing is made before the team is known to fit
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_build_team_set_makes_no_member_before_a_count_or_unknown_name_is_refused(tmp_path, repo, monkeypatch):
    from iron_jarvis.daemon.routes import team as team_routes

    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    calls: list[tuple] = []

    def fake(d, *, provider, model, role, local_only=False, thread_id=""):  # pragma: no cover — must not run
        calls.append((provider, model, role))
        return {"name": model, "created": True}

    monkeypatch.setattr(team_routes, "member_for_model", fake)
    tool = _tool(p, "build_team_set")
    three = await tool.execute(
        {"folder": str(repo), "workers": ["codex-cli/a", "codex-cli/b", "codex-cli/c"]}, _ctx()
    )
    assert not three.ok and "2" in (three.error or ""), three
    unknown = await tool.execute({"folder": str(repo), "workers": ["codex-cli/a"], "reviewer": "Nobody"}, _ctx())
    assert not unknown.ok and "Nobody" in (unknown.error or ""), unknown
    assert calls == [], "no model-team member was made for a team that could not be saved"


@pytest.mark.asyncio
async def test_a_team_of_model_seats_is_still_made_and_saved(tmp_path, repo, monkeypatch):
    from iron_jarvis.daemon.routes import team as team_routes
    from iron_jarvis.harness import team as HT

    app = create_app(str(tmp_path / "home"))
    p = app.state.platform

    def fake(d, *, provider, model, role, local_only=False, thread_id=""):
        roles = ["code", "read", "test"] if role == "worker" else ["review"]
        p.agents_registry.register(model, "You code.", [], base_type="builder", provider="mock", model="mock",
                                   roles=roles)
        return {"name": model, "label": model, "created": True}

    monkeypatch.setattr(team_routes, "member_for_model", fake)
    out = await _tool(p, "build_team_set").execute(
        {"folder": str(repo), "workers": ["codex-cli/writer"], "reviewer": "claude-cli/checker"}, _ctx()
    )
    assert out.ok, out.error
    assert HT.folder_team(p.engine, str(repo)) == {"workers": ["writer"], "reviewer": "checker"}


@pytest.mark.asyncio
async def test_a_member_without_the_role_is_refused_before_a_model_seat_is_made(tmp_path, repo, monkeypatch):
    from iron_jarvis.daemon.routes import team as team_routes

    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    p.agents_registry.register("checker", "You review.", [], base_type="builder", provider="mock", model="mock",
                               roles=["review"])
    calls: list = []
    monkeypatch.setattr(team_routes, "member_for_model", lambda *a, **k: calls.append(k) or {"name": "x"})
    out = await _tool(p, "build_team_set").execute(
        {"folder": str(repo), "workers": ["checker"], "reviewer": "claude-cli/sonnet"}, _ctx()
    )
    assert not out.ok and "Writing code" in (out.error or ""), out.error
    assert calls == []


@pytest.mark.asyncio
async def test_build_team_set_says_the_members_it_made_when_the_save_is_refused(tmp_path, repo, monkeypatch):
    from iron_jarvis.daemon.routes import team as team_routes
    from iron_jarvis.harness import team as HT

    app = create_app(str(tmp_path / "home"))
    p = app.state.platform

    def fake(d, *, provider, model, role, local_only=False, thread_id=""):
        p.agents_registry.register("gpt-x", "You code.", [], base_type="builder", provider="mock", model="mock",
                                   roles=["code", "read", "test"])
        return {"name": "gpt-x", "label": "GPT X", "created": True}

    def refuse(*_a, **_k):
        raise HT.TeamRefused("The team could not be saved.")

    monkeypatch.setattr(team_routes, "member_for_model", fake)
    monkeypatch.setattr(HT, "set_folder_team", refuse)
    out = await _tool(p, "build_team_set").execute({"folder": str(repo), "workers": ["codex-cli/gpt-x"]}, _ctx())
    assert not out.ok
    assert "gpt-x" in (out.error or "") and "model team" in (out.error or ""), out.error


@pytest.mark.asyncio
async def test_build_team_set_reads_the_model_team_off_the_event_loop(tmp_path, repo, monkeypatch):
    from iron_jarvis.agents import model_team as MT

    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    loop_thread = threading.get_ident()
    threads: list[int] = []
    real = MT.team_members

    def spy(platform):
        threads.append(threading.get_ident())
        return real(platform)

    monkeypatch.setattr(MT, "team_members", spy)
    await _tool(p, "build_team_set").execute({"folder": str(repo), "workers": ["nobody"]}, _ctx())
    assert threads and all(t != loop_thread for t in threads), "the registry read ran on the loop"


# --------------------------------------------------------------------------- #
# the night card fails closed
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_the_night_card_asks_even_when_the_window_cannot_be_read(tmp_path, repo, monkeypatch):
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform

    def boom(_platform):
        raise RuntimeError("window unreadable")

    monkeypatch.setattr(RB, "_night_words", boom)
    check = await _tool(p, "night_task_add").spend_check({"words": "update the README", "folder": str(repo)}, _ctx())
    assert check is not None and check.ask, "unattended work never loses its own card"


@pytest.mark.asyncio
async def test_the_night_card_reads_the_live_window(tmp_path, repo):
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    p.config.night_window_start = "01:30"
    check = await _tool(p, "night_task_add").spend_check({"words": "fix lint", "folder": str(repo)}, _ctx())
    assert check is not None and "1:30 AM" in check.words, check.words


# --------------------------------------------------------------------------- #
# arming, fresh sentences both ways
# --------------------------------------------------------------------------- #

FRESH_NIGHT = [
    "queue a task for tonight to update the changelog in C:\\work\\api",
    "tonight, refactor the auth module in this repo",
    "please have Iron Jarvis fix the flaky test overnight",
    "add 'upgrade pytest' to the night queue",
    "line up a night task: rename the config module",
    "can you cancel the changelog night task",
    "scratch the parser task from tonight's queue",
    "take 'upgrade pytest' off the night shift",
    "put the auth refactor first on tonight's list",
    "move 'upgrade pytest' to the bottom of the night queue",
    "while I'm asleep, write unit tests for the billing module",
    "work on the README overnight in D:\\code\\shop",
    "queue up the dependency upgrade for overnight",
    "queue 'add docstrings to utils.py' for tonight",
    "queue 'fix the bug in cart.js' for tonight in this repo",
    "stop the night task that's running",
    "kill the running night task",
    "add a night task to bump the version in pyproject.toml",
]
FRESH_NOT_NIGHT = [
    "queue the client call for tonight",
    "I'll do the payroll tonight",
    "my team at the firm reviews returns",
    "set my default printer",
    "Codex says the tests pass",
    "cancel tonight's dinner",
    "move the meeting to the top of the agenda",
    "use Claude for the cover letter",
    "tonight I need to finish the tax return",
    "add the Smith return to my list for tonight",
    "queue the print job",
    "move the task to the top",
    "move it up",
    "my team writes, the partner reviews",
    "Sarah writes the memo and John reviews it",
    "use the team folder on the shared drive",
    "set the default currency to USD in this workbook",
    "change the defaults in Excel",
    "work on the report tonight",
    "run the payroll report overnight",
    "fix the spreadsheet tonight",
    "please review the code in this folder",
    "use Codex to explain this function",
    "use a template for this folder",
    "make this folder the default save location",
    "set Outlook as the default mail app",
    "use this repo",
    "clear the folder",
    "the build failed tonight",
    "take the trash out tonight",
    "move the files to the top folder",
    "bump the meeting up",
    "move the 1099 task to the top of my list",
    "cancel the night shift party",
    "queue the documents for review",
    "cancel the task",
    "delete the task",
    "remove that task",
    "drop the task, we don't need it",
    "cancel the task on my phone",
    "cancel the Smith task in Karbon",
    "update the client file tonight",
    "add tests to tonight's agenda",
    "Claude reviews and Codex writes better than people think",
]


@pytest.mark.parametrize("text", FRESH_NIGHT)
def test_a_fresh_night_request_offers_the_night_writers(text):
    assert NIGHT_WRITERS <= set(R.record_tools_for(text)), text


FRESH_FOLDER = [
    "use Opus for D:\\code\\shop from now on",
    "set this repo's default to Codex",
    "for D:\\code\\api, default to Claude Code",
    "make Sonnet the default model in this folder",
    "Claude writes, Codex reviews for D:\\code\\shop",
    "have Opus write and Sonnet review in this codebase",
    "clear the coding team for this repo",
    "remove the team from D:\\code\\shop",
    "reset the defaults for this repo to the app's",
    "change the default approval level in this repo to Ask first",
    "set the default effort for this folder to high",
    "use Grok for this repo",
]


@pytest.mark.parametrize("text", FRESH_FOLDER)
def test_a_fresh_folder_request_offers_the_folder_writers(text):
    assert FOLDER_WRITERS <= set(R.record_tools_for(text)), text


@pytest.mark.parametrize("text", FRESH_NOT_NIGHT)
def test_a_fresh_everyday_sentence_offers_no_writer(text):
    assert not (WRITERS & set(R.record_tools_for(text))), text


# --------------------------------------------------------------------------- #
# the honesty note, fresh sentences
# --------------------------------------------------------------------------- #

NO_NOTE = [
    "I've queued the print job for tonight.",
    "I queued your email to send tonight.",
    "I've saved the team roster to the folder.",
    "I updated the team section of the README in this repo.",
    "I changed the team page on the website in this repo.",
    "I removed the defaults section from the folder's README.",
]
NEEDS_NOTE = [
    "I've queued 'update the README' for tonight.",
    "I added the lint cleanup to tonight's list.",
    "I cleared the defaults for this repo.",
    "I removed the coding team from this folder.",
    "I set up a coding team for this repo: Codex writes, Claude reviews.",
]


@pytest.mark.parametrize("reply", NO_NOTE)
def test_an_unrelated_sentence_gets_no_night_or_folder_note(reply):
    assert claimed_change_note(reply, [], []) == "", reply


@pytest.mark.parametrize("reply", NEEDS_NOTE)
def test_a_false_night_or_folder_claim_still_gets_its_note(reply):
    assert claimed_change_note(reply, [], []) != "", reply


def test_the_fresh_matchers_stay_linear():
    import subprocess
    import sys
    import textwrap

    code = textwrap.dedent(
        """
        import time
        from iron_jarvis.settings import records_build as RB
        from iron_jarvis.daemon.change_claims import claimed_change_note
        for text in ("cancel the " + "x " * 3000 + "task", "Claude reviews " + "a " * 3000,
                     "queue " + "a.b" * 2000 + " for tonight", "stop the " + "night " * 2000 + "task"):
            t0 = time.perf_counter()
            RB.build_tools_for(text)
            claimed_change_note("I queued the " + text, [], [])
            assert time.perf_counter() - t0 < 5, text[:40]
        """
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr

