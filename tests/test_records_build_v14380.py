"""v1.438.0 (chat-first plan Wave 4 group A): the night shift and a folder's
coding-session defaults and team from chat — ``settings.records_build``.

"Queue 'update the README' for tonight", "what's queued for tonight?",
"cancel the README task", "move it to the top"; "Use Codex for this repo",
"Codex writes, Claude reviews in D:\\code\\shop", "stop using the team for
this folder". Nine tools: ``night_task_add`` / ``night_task_list`` /
``night_task_cancel`` / ``night_task_move`` and ``build_folder_show`` /
``build_defaults_set`` / ``build_defaults_clear`` / ``build_team_set`` /
``build_team_clear``.

Every behaviour test drives the REAL stream lane (``/chat/stream``): the
sentence must ARM the tool, the card must appear (ask tier), "Allow once"
runs it through the real ``registry.invoke``, and the card's Undo
(``GET /config/changes/{id}`` → ``POST /undo/{id}``) puts the record back —
read straight from its store, never through the snapshot under test. The
arming table (``tests/_records_build_table_v14380.py``) is pinned both ways
through the very functions both lanes call.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.change_claims import claimed_change_note
from iron_jarvis.harness import folder_defaults as FD
from iron_jarvis.harness import night as N
from iron_jarvis.harness import team as HT
from iron_jarvis.settings import records as R
from iron_jarvis.settings import records_build as RB
from tests import _records_build_table_v14380 as T
from tests.test_build_git_v1375 import make_repo
from tests.test_chat_approvals_v1187 import _asgi_post, _drive_stream
from tests.test_records_projects_v14350 import _finished, _lanes
from tests.test_settings_chat_tools_v1321 import _body, _calls_stream
from tests.test_settings_records_v1321 import _app, _approve_all, _card

NIGHT = frozenset({"night_task_add", "night_task_cancel", "night_task_move", "night_task_list"})
FOLDER = frozenset({"build_defaults_set", "build_defaults_clear", "build_team_set", "build_team_clear", "build_folder_show"})
MINE = NIGHT | FOLDER
WRITERS = MINE - RB.READERS


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


async def _undo(app, card, *, status: int = 200) -> dict:
    client = TestClient(app)
    look = client.get(f"/config/changes/{card['change_id']}")
    assert look.status_code == 200, look.text
    r = client.post(f"/undo/{look.json()['action_id']}")
    assert r.status_code == status, r.text
    return r.json()


def _words(frames, tool) -> list[str]:
    return [f.get("output") or f.get("error") or "" for f in _finished(frames, tool)]


def _tasks(p) -> list[dict]:
    return N.list_tasks(p.engine)


def _queued(p) -> list[dict]:
    return N.rows_in(p.engine, N.QUEUED)


async def _seen_cards(app, seen):
    async def approve(data):
        seen.append(data)
        status, _ = await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "once"})
        assert status == 200

    return approve


def _member(p, name: str, roles: list[str]) -> None:
    p.agents_registry.register(name, "You code.", [], base_type="builder", provider="mock", model="mock", roles=roles)


@pytest.fixture()
def repo(tmp_path):
    return make_repo(tmp_path / "shop")


@pytest.fixture(scope="module")
def d(tmp_path_factory):
    app = create_app(str(tmp_path_factory.mktemp("arming") / "home"))
    return SimpleNamespace(platform=app.state.platform)


# --------------------------------------------------------------------------- #
# the arming table, both ways
# --------------------------------------------------------------------------- #


def test_the_table_is_big_enough():
    assert len(T.NIGHT_REQUESTS) >= 30 and len(T.FOLDER_REQUESTS) >= 30
    assert len(T.NEAR_MISSES) >= 30


def test_a_night_request_offers_the_night_tools_in_the_stream_lane_only(d):
    bad = []
    for text in T.NIGHT_REQUESTS:
        armed, ask = _lanes(d, text)
        if not NIGHT <= set(ask) or set(ask) & FOLDER - {"build_folder_show"}:
            bad.append((text, "stream", sorted(set(ask) & MINE)))
        if set(armed) & MINE:
            bad.append((text, "post", sorted(set(armed) & MINE)))
    assert bad == []


def test_a_folder_request_offers_the_folder_tools_in_the_stream_lane_only(d):
    bad = []
    for text in T.FOLDER_REQUESTS:
        armed, ask = _lanes(d, text)
        if not FOLDER <= set(ask) or set(ask) & (NIGHT - {"night_task_list"}):
            bad.append((text, "stream", sorted(set(ask) & MINE)))
        if set(armed) & MINE:
            bad.append((text, "post", sorted(set(armed) & MINE)))
    assert bad == []


def test_a_question_keeps_only_the_reader():
    for text in T.NIGHT_QUESTIONS:
        assert RB.build_tools_for(text) == ["night_task_list"], text
        assert set(R.record_tools_for(text)) & MINE == {"night_task_list"}, text
    for text in T.FOLDER_QUESTIONS:
        assert RB.build_tools_for(text) == ["build_folder_show"], text
        assert set(R.record_tools_for(text)) & MINE == {"build_folder_show"}, text
    for text, want in T.READ_REQUESTS.items():
        assert set(RB.build_tools_for(text)) == want, text
    # This module keeps its OWN question guard (its readers outlive the
    # records' guard), so a request typed as a question arms no writer here.
    for text in T.QUESTION_WRITES:
        assert not set(RB.build_tools_for(text)) & WRITERS, text
        assert not set(R.record_tools_for(text)) & WRITERS, text


def test_an_everyday_sentence_arms_none_of_them(d):
    bad = []
    for text in T.NEAR_MISSES:
        armed, ask = _lanes(d, text)
        stray = (set(armed) | set(ask)) & MINE
        if stray:
            bad.append((text, sorted(stray)))
    assert bad == []


def test_record_tools_for_makes_one_call_into_the_module(monkeypatch):
    calls = []
    monkeypatch.setattr(RB, "build_tools_for", lambda t: calls.append(t) or ["night_task_add"])
    assert "night_task_add" in R.record_tools_for("anything at all")
    assert calls == ["anything at all"]


def test_the_matchers_are_linear_on_pathological_text():
    """A child process with a deadline (a hang detector, never a speed bar):
    every input below is 40k+ characters built to make a backtracking rule
    quadratic. The arming read runs per turn, so a slow one would stall it."""
    code = textwrap.dedent(
        """
        import time
        from iron_jarvis.settings.records_build import build_tools_for, _clauses, _night_request, _folder_request
        N = 40_000
        cases = [
            "queue " + " " * N + "tonight",
            "codex " + " " * N + "writes",
            "codex writes" + " x" * N + " claude reviews",
            "cancel the " + "a " * N + "task",
            "use codex " + "x" * N + " for this repo",
            "move " + "x " * N + "to the top of tonight's list",
            "tonight " * N,
            "codex " * N,
            "set the default " * 5000 + " for this repo",
            "D:\\\\" + "a\\\\" * N,
            "have it " * 5000 + "fix the tests overnight",
            "please " * N + "queue it for tonight",
            "i want to " * 5000 + "queue it",
        ]
        t0 = time.perf_counter()
        for c in cases:
            build_tools_for(c)
            for cl in _clauses(c[:60000]):
                _night_request(cl)
            _folder_request(c, _clauses(c))
        print(round(time.perf_counter() - t0, 2))
        """
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr


# --------------------------------------------------------------------------- #
# the night shift, through the real stream lane
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_night_task_add_asks_with_when_it_runs_queues_it_and_undo_takes_it_off(tmp_path, repo):
    app = _app(tmp_path / "home", [("night_task_add", {"words": "update the README", "folder": str(repo)})])
    p = app.state.platform
    seen: list[dict] = []
    frames = await _drive_stream(app, _body(f"Queue 'update the README' for tonight in {repo}"), await _seen_cards(app, seen))
    assert [c["tool"] for c in seen] == ["night_task_add"], "queuing a night task asks first"
    note = seen[0].get("cost_note") or ""
    assert "11:00 PM" in note and "7:00 AM" in note and str(repo) in note, note
    assert "on its own" in note, "the card says it runs unattended"
    (task,) = _tasks(p)
    assert task["state"] == N.QUEUED and task["words"] == "update the README" and task["folder"] == str(repo)
    card = _card(frames, "Night task queued")
    assert "update the README" in card["new"]
    out = await _undo(app, card)
    assert "took" in out["output"] and "off tonight's list" in out["output"]
    assert _tasks(p) == []


@pytest.mark.asyncio
async def test_undo_of_a_queued_task_refuses_once_it_started_or_was_edited(tmp_path, repo):
    app = _app(tmp_path / "home", [("night_task_add", {"words": "fix lint", "folder": str(repo)}),
                                   ("night_task_add", {"words": "write docs", "folder": str(repo)}),
                                   ("night_task_add", {"words": "bump deps", "folder": str(repo)})])
    p = app.state.platform
    frames = await _drive_stream(app, _body(f"queue 'fix lint' and 'write docs' for tonight in {repo}"), await _approve_all(app, []))
    cards = [c for c in next(d for ev, d in frames if ev == "done")["config_cards"] if c.get("title") == "Night task queued"]
    assert len(cards) == 3
    first, second, third = (next(t for t in _tasks(p) if t["words"] == w) for w in ("fix lint", "write docs", "bump deps"))
    assert N.mark_running(p.engine, first["id"])
    N.edit_task(p.engine, second["id"], {"words": "write the API docs"})
    assert N.mark_running(p.engine, third["id"]) and N.finish_task(p.engine, third["id"], N.DONE)
    by_words = {w: next(c for c in cards if w in c["new"]) for w in ("fix lint", "write docs", "bump deps")}
    started = await _undo(app, by_words["fix lint"], status=409)
    assert "already started" in started["detail"]
    edited = await _undo(app, by_words["write docs"], status=409)
    assert "changed after it was queued" in edited["detail"]
    ended = await _undo(app, by_words["bump deps"], status=409)
    assert "already run" in ended["detail"]
    assert {t["words"] for t in _tasks(p)} == {"fix lint", "write the API docs", "bump deps"}, (
        "Undo never destroys newer work — an ended task's record stays too"
    )


@pytest.mark.asyncio
async def test_night_task_add_refuses_a_folder_that_is_not_a_repository_in_the_routes_words(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    app = _app(tmp_path / "home", [("night_task_add", {"words": "fix it", "folder": str(plain)})])
    frames = await _drive_stream(app, _body(f"queue 'fix it' for tonight in {plain}"), await _approve_all(app, []))
    (err,) = _words(frames, "night_task_add")
    assert "repository" in err.lower() or "git" in err.lower(), err
    assert _tasks(app.state.platform) == []


def test_a_network_folder_is_refused_by_its_text_before_the_disk(monkeypatch, tmp_path):
    import os

    def boom(*_a, **_k):  # pragma: no cover — must never run
        raise AssertionError("the disk was touched for a network folder")

    monkeypatch.setattr(os.path, "isdir", boom)
    monkeypatch.setattr("iron_jarvis.core.fs_policy.root_problem", boom)
    platform = SimpleNamespace(engine=None)
    for raw in ("\\\\server\\share\\repo", "//server/share/repo", "\\\\?\\UNC\\server\\x"):
        assert RB.resolve_folder(platform, SimpleNamespace(project_id=None), raw) == ("", RB.NETWORK_FOLDER)
        assert RB._existing_folder(platform, SimpleNamespace(project_id=None), raw) == ("", RB.NETWORK_FOLDER)


def test_with_no_folder_named_the_project_folder_is_used_else_it_asks_never_guesses(tmp_path, repo):
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    c = TestClient(app)
    pid = c.post("/projects", json={"name": "Shop", "root": str(repo)}).json()["id"]
    bare = c.post("/projects", json={"name": "Notes"}).json()["id"]
    assert RB.resolve_folder(p, SimpleNamespace(project_id=pid), "") == (str(repo), "")
    assert RB.resolve_folder(p, SimpleNamespace(project_id=bare), "") == ("", RB.WHICH_FOLDER)
    assert RB.resolve_folder(p, SimpleNamespace(project_id=None), "") == ("", RB.WHICH_FOLDER)
    assert RB.resolve_folder(p, SimpleNamespace(project_id=None), "code\\shop") == ("", RB.FULL_PATH)


@pytest.mark.asyncio
async def test_night_task_list_reads_without_a_card(tmp_path, repo):
    app = _app(tmp_path / "home", [("night_task_list", {})])
    p = app.state.platform
    a = N.add_task(p.engine, folder=str(repo), words="first thing")
    b = N.add_task(p.engine, folder=str(repo), words="second thing")

    async def never(data):  # pragma: no cover
        raise AssertionError(data)

    frames = await _drive_stream(app, _body("what's queued for tonight?"), never)
    (out,) = _words(frames, "night_task_list")
    assert out.index("first thing") < out.index("second thing")
    assert a["id"] in out and b["id"] in out and "1." in out and "2." in out


@pytest.mark.asyncio
async def test_night_task_cancel_and_undo_puts_it_back_in_its_place(tmp_path, repo):
    app = _app(tmp_path / "home", [("night_task_cancel", {"task": "README"})])
    p = app.state.platform
    first = N.add_task(p.engine, folder=str(repo), words="update the README")
    N.add_task(p.engine, folder=str(repo), words="fix lint")
    N.add_note(p.engine, first["id"], "info", "a note the user had")
    before = N.get_task(p.engine, first["id"])
    seen: list[str] = []
    frames = await _drive_stream(app, _body("cancel the README task"), await _approve_all(app, seen))
    assert seen == ["night_task_cancel"]
    after = N.get_task(p.engine, first["id"])
    assert after["state"] == N.STOPPED
    card = _card(frames, "Night task cancelled")
    assert card["new"].startswith("cancelled")
    out = await _undo(app, card)
    assert "back on tonight's list" in out["output"]
    back = N.get_task(p.engine, first["id"])
    assert back["state"] == N.QUEUED and back["position"] == before["position"]
    assert back["notes"] == before["notes"], "the cancel's own note is taken back, the user's kept"
    assert [t["words"] for t in _queued(p)] == ["update the README", "fix lint"]


@pytest.mark.asyncio
async def test_undo_of_a_cancel_refuses_when_queued_again_or_changed_or_it_was_running(tmp_path, repo):
    app = _app(tmp_path / "home", [("night_task_cancel", {"task": "README"}), ("night_task_cancel", {"task": "parser"}),
                                   ("night_task_cancel", {"task": "lint"})])
    p = app.state.platform
    a = N.add_task(p.engine, folder=str(repo), words="update the README")
    b = N.add_task(p.engine, folder=str(repo), words="refactor the parser")
    c = N.add_task(p.engine, folder=str(repo), words="fix lint")
    assert N.mark_running(p.engine, c["id"])
    frames = await _drive_stream(app, _body("cancel the README task"), await _approve_all(app, []))
    cards = [x for x in next(d for ev, d in frames if ev == "done")["config_cards"] if x.get("title") == "Night task cancelled"]
    assert len(cards) == 3
    by = {k: next(x for x in cards if k in x["label"]) for k in ("README", "parser", "lint")}
    N.add_task(p.engine, folder=str(repo), words="update the README")  # queued again by the user
    N.add_note(p.engine, b["id"], "info", "the user wrote on it since")  # changed since
    again = await _undo(app, by["README"], status=409)
    assert "queued" in again["detail"] and "again" in again["detail"]
    changed = await _undo(app, by["parser"], status=409)
    assert "changed after it was cancelled" in changed["detail"]
    running = await _undo(app, by["lint"], status=409)
    assert "running" in running["detail"] and "cannot start it again" in running["detail"]
    assert N.get_task(p.engine, a["id"])["state"] == N.STOPPED
    assert N.get_task(p.engine, b["id"])["state"] == N.STOPPED


@pytest.mark.asyncio
async def test_night_task_move_to_the_top_and_undo_puts_the_order_back_but_never_over_a_newer_list(tmp_path, repo):
    app = _app(tmp_path / "home", [("night_task_move", {"task": "lint", "to": "top"})])
    p = app.state.platform
    for w in ("update the README", "refactor the parser", "fix lint"):
        N.add_task(p.engine, folder=str(repo), words=w)
    frames = await _drive_stream(app, _body("move the lint task to the top of tonight's list"), await _approve_all(app, []))
    assert [t["words"] for t in _queued(p)] == ["fix lint", "update the README", "refactor the parser"]
    card = _card(frames, "Tonight's order changed")
    assert card["old"] == "number 3 of 3" and card["new"] == "number 1 of 3"
    await _undo(app, card)
    assert [t["words"] for t in _queued(p)] == ["update the README", "refactor the parser", "fix lint"]
    # A second move, then a newer task: Undo leaves the list as it is.
    p.router.stream = _calls_stream([("night_task_move", {"task": "lint", "to": "top"})])
    frames = await _drive_stream(app, _body("move the lint task to the top of tonight's list"), await _approve_all(app, []))
    card = _card(frames, "Tonight's order changed")
    N.add_task(p.engine, folder=str(repo), words="a newer task")
    out = await _undo(app, card, status=409)
    assert "changed after this move" in out["detail"]
    assert [t["words"] for t in _queued(p)][0] == "fix lint"


@pytest.mark.asyncio
async def test_deny_on_the_card_changes_nothing(tmp_path, repo):
    app = _app(tmp_path / "home", [("night_task_add", {"words": "x", "folder": str(repo)}),
                                   ("build_defaults_set", {"folder": str(repo), "view": "chat_code", "approval_mode": "auto"})])
    p = app.state.platform
    seen: list[str] = []

    async def deny(data):
        seen.append(data["tool"])
        status, _ = await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "deny"})
        assert status == 200

    await _drive_stream(app, _body(f"queue 'x' for tonight and use Codex for {repo}"), deny)
    assert seen == ["night_task_add", "build_defaults_set"]
    assert _tasks(p) == [] and FD.read(p.engine, str(repo)) == {}


# --------------------------------------------------------------------------- #
# a folder's defaults and team
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_build_defaults_set_keeps_the_other_fields_says_the_account_is_not_kept_and_undo_restores(tmp_path, repo):
    app = _app(tmp_path / "home", [("build_defaults_set", {"folder": str(repo), "view": "chat_code", "approval_mode": "accept_edits",
                                                           "account": "work"})])
    p = app.state.platform
    FD.save(p.engine, str(repo), "code", {"reasoning": "high"})
    FD.save(p.engine, str(repo), "tool", {"harness": "codex", "mode": "plan"})
    seen: list[str] = []
    frames = await _drive_stream(app, _body(f"set the default approval level for {repo} to Accept edits"), await _approve_all(app, seen))
    assert seen == ["build_defaults_set"]
    assert FD.read(p.engine, str(repo)) == {"code": {"reasoning": "high", "approval_mode": "accept_edits"},
                                             "tool": {"harness": "codex", "mode": "plan"}}
    (out,) = _words(frames, "build_defaults_set")
    assert "account is not kept" in out
    card = _card(frames, "Folder defaults set")
    assert "Accept edits" in card["new"] and "Accept edits" not in card["old"]
    await _undo(app, card)
    assert FD.read(p.engine, str(repo)) == {"code": {"reasoning": "high"}, "tool": {"harness": "codex", "mode": "plan"}}


@pytest.mark.asyncio
async def test_dont_ask_and_full_access_are_never_a_folder_default(tmp_path, repo):
    app = _app(tmp_path / "home", [("build_defaults_set", {"folder": str(repo), "view": "chat_code", "approval_mode": "yolo"}),
                                   ("build_defaults_set", {"folder": str(repo), "view": "build_code", "tool": "codex", "mode": "full"})])
    p = app.state.platform
    frames = await _drive_stream(app, _body(f"use Codex for {repo}"), await _approve_all(app, []))
    errors = _words(frames, "build_defaults_set")
    assert any("Don't ask" in e and "per session" in e for e in errors), errors
    assert any("Full access" in e and "per session" in e for e in errors), errors
    assert FD.read(p.engine, str(repo)) == {}


@pytest.mark.asyncio
async def test_undo_of_folder_defaults_refuses_over_a_newer_change(tmp_path, repo):
    app = _app(tmp_path / "home", [("build_defaults_set", {"folder": str(repo), "view": "build_code", "tool": "codex"})])
    p = app.state.platform
    frames = await _drive_stream(app, _body(f"use Codex for {repo}"), await _approve_all(app, []))
    card = _card(frames, "Folder defaults set")
    FD.save(p.engine, str(repo), "tool", {"harness": "claude-code"})  # the user's newer change
    out = await _undo(app, card, status=409)
    assert "changed again" in out["detail"]
    assert FD.read(p.engine, str(repo)) == {"tool": {"harness": "claude-code"}}


@pytest.mark.asyncio
async def test_build_defaults_clear_and_undo(tmp_path, repo):
    app = _app(tmp_path / "home", [("build_defaults_clear", {"folder": str(repo)})])
    p = app.state.platform
    FD.save(p.engine, str(repo), "tool", {"harness": "codex"})
    frames = await _drive_stream(app, _body(f"clear the defaults for {repo}"), await _approve_all(app, []))
    assert FD.read(p.engine, str(repo)) == {}
    card = _card(frames, "Folder defaults cleared")
    await _undo(app, card)
    assert FD.read(p.engine, str(repo)) == {"tool": {"harness": "codex"}}


@pytest.mark.asyncio
async def test_a_folder_that_does_not_exist_is_refused_in_words(tmp_path):
    missing = tmp_path / "nope"
    app = _app(tmp_path / "home", [("build_defaults_set", {"folder": str(missing), "view": "build_code", "tool": "codex"})])
    frames = await _drive_stream(app, _body(f"use Codex for {missing}"), await _approve_all(app, []))
    (err,) = _words(frames, "build_defaults_set")
    assert "no folder" in err and str(missing) in err


@pytest.mark.asyncio
async def test_build_team_set_with_members_and_undo_puts_back_none(tmp_path, repo):
    app = _app(tmp_path / "home", [("build_team_set", {"folder": str(repo), "workers": ["coder"], "reviewer": "checker"})])
    p = app.state.platform
    _member(p, "coder", ["code", "read"])
    _member(p, "checker", ["review"])
    seen: list[str] = []
    frames = await _drive_stream(app, _body(f"Codex writes, Claude reviews in {repo}"), await _approve_all(app, seen))
    assert seen == ["build_team_set"]
    assert HT.folder_team(p.engine, str(repo)) == {"workers": ["coder"], "reviewer": "checker"}
    card = _card(frames, "Folder team set")
    assert card["old"] == "none" and "coder" in card["new"] and "checker reviews" in card["new"]
    await _undo(app, card)
    assert HT.folder_team(p.engine, str(repo)) is None


@pytest.mark.asyncio
async def test_build_team_set_refuses_in_the_teams_own_words_and_saves_nothing(tmp_path, repo):
    app = _app(tmp_path / "home", [("build_team_set", {"folder": str(repo), "workers": ["ghost"]}),
                                   ("build_team_set", {"folder": str(repo), "workers": ["coder"], "reviewer": "coder"})])
    p = app.state.platform
    _member(p, "coder", ["code"])
    frames = await _drive_stream(app, _body(f"use a team for {repo}"), await _approve_all(app, []))
    errors = _words(frames, "build_team_set")
    assert any("no member of your model team named ghost" in e for e in errors), errors
    assert any("named twice" in e for e in errors), errors
    assert HT.folder_team(p.engine, str(repo)) is None


@pytest.mark.asyncio
async def test_a_model_seat_goes_through_the_routes_member_body_and_undo_keeps_the_member(tmp_path, repo, monkeypatch):
    from iron_jarvis.daemon.routes import team as team_routes

    app = _app(tmp_path / "home", [("build_team_set", {"folder": str(repo), "workers": ["codex-cli/gpt-5"]})])
    p = app.state.platform
    calls = []

    def fake(d, *, provider, model, role, local_only=False, thread_id=""):
        calls.append((provider, model, role, local_only))
        _member(p, "gpt-5", ["code", "read", "test"])
        return {"name": "gpt-5", "provider": provider, "model": model, "label": "GPT-5", "roles": ["code"], "created": True}

    monkeypatch.setattr(team_routes, "member_for_model", fake)
    frames = await _drive_stream(app, _body(f"use a team for {repo}"), await _approve_all(app, []))
    assert calls == [("codex-cli", "gpt-5", "worker", False)]
    (out,) = _words(frames, "build_team_set")
    assert "made the model-team member gpt-5" in out
    card = _card(frames, "Folder team set")
    undone = await _undo(app, card)
    assert "stay on your model team" in undone["output"]
    assert HT.folder_team(p.engine, str(repo)) is None
    assert p.agents_registry.get("gpt-5") is not None, "the member the change made is kept"


@pytest.mark.asyncio
async def test_build_team_clear_and_undo_but_never_over_a_newer_team(tmp_path, repo):
    app = _app(tmp_path / "home", [("build_team_clear", {"folder": str(repo)})])
    p = app.state.platform
    _member(p, "coder", ["code"])
    _member(p, "other", ["code"])
    HT.set_folder_team(p.engine, str(repo), {"workers": ["coder"], "reviewer": ""})
    frames = await _drive_stream(app, _body(f"stop using the team for {repo}"), await _approve_all(app, []))
    assert HT.folder_team(p.engine, str(repo)) is None
    card = _card(frames, "Folder team removed")
    HT.set_folder_team(p.engine, str(repo), {"workers": ["other"], "reviewer": ""})
    out = await _undo(app, card, status=409)
    assert "changed again" in out["detail"]
    assert HT.folder_team(p.engine, str(repo))["workers"] == ["other"]


@pytest.mark.asyncio
async def test_build_folder_show_reads_without_a_card(tmp_path, repo):
    app = _app(tmp_path / "home", [("build_folder_show", {"folder": str(repo)})])
    p = app.state.platform
    FD.save(p.engine, str(repo), "tool", {"harness": "codex", "mode": "plan"})

    async def never(data):  # pragma: no cover
        raise AssertionError(data)

    frames = await _drive_stream(app, _body(f"what are the defaults for {repo}?"), never)
    (out,) = _words(frames, "build_folder_show")
    assert "Build › Code defaults: Codex, Plan" in out and "Coding team: none" in out


# --------------------------------------------------------------------------- #
# registration
# --------------------------------------------------------------------------- #


def test_the_nine_are_registered_with_their_tiers_kept_from_low_trust_and_off_every_roster(tmp_path):
    from iron_jarvis.agents.types import _DEFINITIONS
    from iron_jarvis.core.trust import LOW_TRUST_DENY

    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    for name in MINE:
        assert p.registry.get(name) is not None, name
        assert R.RECORD_TOOL_TIERS[name] == RB.TIERS[name], name
        assert p.permissions.authorize(name, {}).allowed is (name in RB.READERS), name
        assert (name in LOW_TRUST_DENY) is (name in WRITERS), name
        for definition in _DEFINITIONS.values():
            assert name not in definition.tools, (name, definition.type)


def test_the_route_bodies_are_the_tools_bodies():
    """Never a second copy of a rule: the routes and the tools call the same
    lifted functions."""
    import inspect

    from iron_jarvis.daemon.routes import night as NR
    from iron_jarvis.daemon.routes import team as TR

    night = inspect.getsource(NR.register)
    assert "await add_night_task(d, body)" in night and "cancel_night_task(d, task_id)" in night
    assert "member_for_model(" in inspect.getsource(TR.register)
    src = open(RB.__file__, encoding="utf-8").read()
    for call in ("add_night_task", "cancel_night_task", "member_for_model", "FD.save", "HT.validate", "HT.set_folder_team"):
        assert call in src, call
    # The folder door and the team rules live in their own modules only.
    assert "root_problem(" not in src and "MAX_WORKERS" not in src


# --------------------------------------------------------------------------- #
# Wave 5's honesty note: a claim the turn did not make
# --------------------------------------------------------------------------- #

CLAIMS = [
    # (reply, tools that ran, note expected)
    ("I've queued 'Update the README' for tonight.", [], "No night task was queued"),
    ("I've queued 'Update the README' for tonight.", ["night_task_add"], ""),
    ("Done — I added it to tonight's list.", [], "No night task was queued"),
    ("I cancelled the README task on tonight's list.", [], "No night task was cancelled"),
    ("I cancelled the README task on tonight's list.", ["night_task_cancel"], ""),
    ("I set Codex as the default for D:\\code\\shop.", [], "No folder's defaults"),
    ("I set Codex as the default for D:\\code\\shop.", ["build_defaults_set"], ""),
    ("I've made Codex this repo's default.", [], "No folder's defaults"),
    ("I set up a coding team for this repo: Codex writes, Claude reviews.", [], "No folder's coding team"),
    ("I set up a coding team for this repo: Codex writes, Claude reviews.", ["build_team_set"], ""),
    ("I stopped using the team for this folder.", [], "No folder's coding team"),
    ("I stopped using the team for this folder.", ["build_team_clear"], ""),
    ("I cleared this folder's defaults.", [], "No folder's defaults"),
    ("I cleared this folder's defaults.", ["build_defaults_clear"], ""),
    # never a claim about these
    ("I scheduled a meeting for tonight.", [], ""),
    ("I can queue it for tonight if you like.", [], ""),
    ("Shall I queue that for tonight?", [], ""),
    ("I updated the README in D:\\code\\shop.", [], ""),
    ("Your team at work reviewed the code.", [], ""),
    ("I queued the song on Spotify.", [], ""),
    ("Tonight I fixed dinner.", [], ""),
    ("You queued it for tonight earlier.", [], ""),
    ("I added three tasks to your to-do list for tonight.", [], ""),
    ("The night task will run at 11 PM once you approve.", [], ""),
    ("I queued a reminder for tonight's call.", [], ""),
    ("I queued the interview for tonight.", [], ""),
    # "in this repo" reads as the user's content to the shared scanner; the
    # folder's own coding records are still judged there.
    ("I set Codex as the default in this repo.", [], "No folder's defaults"),
    ("I set Codex as the default in this repo.", ["build_defaults_set"], ""),
    ("The night task for the README is now queued for tonight in this repo.", [], "No night task"),
]


@pytest.mark.parametrize("reply,tools,want", CLAIMS)
def test_a_claim_about_the_night_shift_or_a_folder_is_checked_both_ways(reply, tools, want):
    note = claimed_change_note(reply, tools, [], reply_local_only=True)
    if want:
        assert want in note, (reply, note)
    else:
        assert note == "", (reply, note)


def test_a_card_answers_a_claim_too():
    reply = "I've queued 'Update the README' for tonight."
    card = {"kind": "change", "title": "Night task queued", "key": "night.n1", "old": None, "new": "x"}
    assert claimed_change_note(reply, ["something_else"], [card], reply_local_only=True) == ""
    assert json.dumps(card)  # a plain card, as the lanes hand it over
