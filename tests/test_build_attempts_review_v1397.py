"""v1.397.0 REVIEW — try one task on several models (``harness/attempts``).

Each pin here failed on the doer's merged code (b4789be7..7b9410d0) and
passes on the fix beside it. Driven like the doer's own proof: the REAL app
factory, the real build turn route, the real stream lane per attempt, real
git in a scratch repository; only the models are scripted doubles and the
model menu a fixed list. Nothing here reaches a real model or service.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.harness import attempts as A
from iron_jarvis.providers.adapters.base import LLMResponse
from tests.test_build_accounts_v1368 import enable, isolate, make_fake
from tests.test_build_attempts_v1397 import (
    BUGGY,
    MENU,
    ScriptedModel,
    World,
    attempts_turn,
    card_of,
    failing_script,
    item_for,
    make_calc_repo,
    new_session,
    passing_script,
    wire_models,
)
from tests.test_build_git_v1375 import git, no_real_gh, snapshot
from tests.test_build_sessions_v1367 import asgi, frames_of, messages_of, open_turn, setup_of


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    no_real_gh(monkeypatch)
    yield from isolate(tmp_path, monkeypatch)


@pytest.fixture
def fake(tmp_path):
    f = make_fake(tmp_path)
    yield f
    f.stop()


@pytest.fixture
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture
def repo(tmp_path) -> Path:
    return make_calc_repo(tmp_path / "proj")


GIT = shutil.which("git")
pytestmark = pytest.mark.skipif(GIT is None, reason="git is not installed")

NOTE_LINE = "# the session's own note, not committed yet\n"
SESSION_CALC = NOTE_LINE.encode() + BUGGY
FIXED_WITH_NOTE = NOTE_LINE + "def add(a, b):\n    return a + b  # fixed\n"
NOTES_MD = b"Notes the session wrote, never committed.\n"


def _text(p: Path) -> str:
    return p.read_text(encoding="utf-8").replace("\r\n", "\n")


# --------------------------------------------------------------------------- #
# 1. A session with work not committed yet can try on several models
# --------------------------------------------------------------------------- #


async def test_a_session_with_uncommitted_work_tries_on_two_models_and_keep_lands_the_result(
    app, fake, repo, monkeypatch,
):
    """The agent asks before every commit (v1.375.0), so a session that has
    done ANY work has changes not committed. The attempts must start from
    the session's CURRENT files (its uncommitted edit AND its new file), and
    Keep must leave the session with exactly the kept attempt's result —
    the session's own work kept, never lost."""
    await enable(app)
    wire_models(app, monkeypatch, World({"codex-cli": passing_script(), "ollama": failing_script()}))
    view = await new_session(app, repo)
    sid = view["id"]
    wt = Path(view["git"]["worktree"])
    head_before = git("rev-parse", "HEAD", cwd=wt)
    # The session's own work so far, not committed: an edit and a new file.
    (wt / "calc.py").write_bytes(SESSION_CALC)
    (wt / "NOTES.md").write_bytes(NOTES_MD)
    before = snapshot(repo)

    frames = await attempts_turn(app, sid, "Try this with Codex and the local model: fix add in calc.py.", "uc-1")
    card = card_of(frames)
    codex, local = item_for(card, "codex-cli"), item_for(card, "ollama")
    cwt, lwt = Path(codex["git"]["worktree"]), Path(local["git"]["worktree"])
    # Each attempt SAW the session's work: Codex's patch applied on top of the
    # note, and the new file is in both attempts' folders.
    assert _text(cwt / "calc.py") == FIXED_WITH_NOTE
    assert (cwt / "NOTES.md").read_bytes() == NOTES_MD
    assert (lwt / "NOTES.md").read_bytes() == NOTES_MD
    # The card counts only what each ATTEMPT changed (not the session's own
    # work it started from).
    assert codex["files"] == ["calc.py"], codex["files"]
    assert codex["tests"] == "passed"
    # The session's folder is untouched while they run.
    assert (wt / "calc.py").read_bytes() == SESSION_CALC

    status, kept = await asgi(app, "POST", f"/build/sessions/{sid}/attempts/keep",
                              {"turn_id": "uc-1", "attempt": codex["id"]})
    assert status == 200, kept
    # Exactly the kept result in the session's folder: the session's note,
    # Codex's fix, and the session's own new file — nothing lost.
    assert _text(wt / "calc.py") == FIXED_WITH_NOTE
    assert (wt / "NOTES.md").read_bytes() == NOTES_MD
    # The session's own commits are still the user's to make: nothing was
    # committed on the session's branch.
    assert git("rev-parse", "HEAD", cwd=wt) == head_before
    # The other attempt's folder went to the trash; its branch stays.
    assert not lwt.exists()
    assert git("rev-parse", "--verify", f"refs/heads/{local['branch']}", cwd=repo)
    assert snapshot(repo) == before


async def test_keep_refuses_in_words_when_the_session_changed_since_the_attempts_started(
    app, fake, repo, monkeypatch,
):
    """Keep never loses work the session did AFTER the attempts started: its
    files no longer match what the attempts started from, so Keep refuses in
    words and NOTHING moves."""
    await enable(app)
    wire_models(app, monkeypatch, World({"codex-cli": passing_script(), "ollama": failing_script()}))
    view = await new_session(app, repo)
    sid = view["id"]
    wt = Path(view["git"]["worktree"])
    (wt / "calc.py").write_bytes(SESSION_CALC)
    frames = await attempts_turn(app, sid, "Try this with Codex and the local model: fix add in calc.py.", "uc-2")
    codex = item_for(card_of(frames), "codex-cli")
    # Later work in the session, after the attempts started.
    (wt / "LATER.md").write_bytes(b"written after the attempts started\n")
    status, body = await asgi(app, "POST", f"/build/sessions/{sid}/attempts/keep",
                              {"turn_id": "uc-2", "attempt": codex["id"]})
    assert status == 409, body
    assert "changed since" in body["detail"] and "nothing" in body["detail"].lower()
    assert (wt / "calc.py").read_bytes() == SESSION_CALC
    assert (wt / "LATER.md").exists()
    assert Path(codex["git"]["worktree"]).exists()
    line = next(m for m in messages_of(app, sid) if m.get("role") == "assistant" and m.get("turnId") == "uc-2")
    assert "kept" not in line[A.LINE_KEY]


async def test_a_session_in_its_folder_with_uncommitted_work_moves_onto_the_kept_branch(
    app, fake, repo, monkeypatch,
):
    """No branch of its own, work not committed in the folder: the attempts
    still start from the folder's CURRENT files, and Keep moves the session
    onto the kept branch — the user's main checkout is byte-for-byte what it
    was (its own uncommitted changes included)."""
    await enable(app)
    wire_models(app, monkeypatch, World({"codex-cli": passing_script(), "ollama": failing_script()}))
    view = await new_session(app, repo, branch=False)
    sid = view["id"]
    (repo / "calc.py").write_bytes(SESSION_CALC)
    (repo / "NOTES.md").write_bytes(NOTES_MD)
    before = snapshot(repo)
    frames = await attempts_turn(app, sid, "Try this with Codex and the local model: fix add in calc.py.", "uc-3")
    codex = item_for(card_of(frames), "codex-cli")
    assert _text(Path(codex["folder"]) / "calc.py") == FIXED_WITH_NOTE
    assert (Path(codex["folder"]) / "NOTES.md").read_bytes() == NOTES_MD
    status, kept = await asgi(app, "POST", f"/build/sessions/{sid}/attempts/keep",
                              {"turn_id": "uc-3", "attempt": codex["id"]})
    assert status == 200, kept
    assert setup_of(app, sid)["git"]["branch"] == codex["branch"]
    assert _text(Path(codex["folder"]) / "calc.py") == FIXED_WITH_NOTE
    assert snapshot(repo) == before


# --------------------------------------------------------------------------- #
# 2. The opt-in: every phrasing is a row, both ways
# --------------------------------------------------------------------------- #

#: Messages that ASK for attempts (the user's words). (text, names or count)
ASKS = [
    ("Try this with Claude, Codex and the local model: fix the failing test.", ["Claude", "Codex", "the local model"]),
    ("Try this with Claude and Codex: fix add.", ["Claude", "Codex"]),
    ("Please try this using Codex and Claude - make add() add", ["Codex", "Claude"]),
    ("Can you try it with Codex and the local model? The parser drops a field.", ["Codex", "the local model"]),
    ("Fix the failing test. Try it on two models.", 2),
    ("Try it on three models: speed up the parser.", 3),
    ("Can you try it across three different models? The parser drops a field.", 3),
    ("try it on 2 models", 2),
    ("Try this on several models.", 3),
    ("Try it with `glm-local` and Codex", ["glm-local", "Codex"]),
    ("Now try the same thing with Codex and Claude.", ["Codex", "Claude"]),
    ("Try with Claude and Codex: add a docstring.", ["Claude", "Codex"]),
    ("Try it with Claude and Codex too: add a docstring.", ["Claude", "Codex"]),
    ("Try this with Claude and Codex, please: add a test.", ["Claude", "Codex"]),
    ("Fix add. Try it with Claude and Codex in parallel.", ["Claude", "Codex"]),
    ("Try it on two models at once: fix add.", 2),
]

#: Messages that must stay ORDINARY turns — coding words that start "Try …"
#: and name something model-ish, a single model in a coding sense, a data
#: "model", a pasted line in the middle of the message.
ORDINARY = [
    "Try this with Claude's API format",
    "Try it with the Codex CLI flags I pasted",
    "Try this with a local cache",
    "Try that with GPT-style prompts",
    "try with the local model's config",
    "Try it with the User model and see if the migration runs.",
    "Try this with the Order model instead.",
    "Try it on both models and check the foreign keys.",
    "Try it on two models first, then roll it out to the rest.",
    "Try it on the old model and compare the loss.",
    "Try it with gpt-5.5 instead of gpt-4o in the client.",
    "Try it with Claude and see what happens.",
    "Try it with Claude, Codex and see what happens.",
    "Try this with Claude and Codex later.",
    "Try it with Claude and Codex tomorrow, not now.",
    "Try it with Codex",
    "Try this with the local model: fix add.",
    "Try it with Ollama: the app should call localhost:11434.",
    "Try it on other models.",
    "Here is the README:\nTry it with Claude and Codex in two terminals.\nInstall it with pip.\nSummarize it.",
    "Summarize this.\n```\nTry it with Claude and Codex\n```\nThanks.",
    "Here is the README:\nTry it with Claude and Codex.\nInstall it with pip.\nSummarize it.",
    "What does this line in our docs mean?\n```\nTry it with Claude and Codex",
    "> Try it with Claude and Codex\nIs this line in my notes still right?",
    "Try it on both models.",
    "Did you try it with Claude and Codex?",
    "I'll try this with Claude and Codex later.",
    "We should try harder.",
    "Try to make it faster.",
]

MENU_NAMES = [*MENU, {"provider": "claude-cli", "model": "claude-sonnet-4-5", "available": True,
                      "on_this_network": False}]


@pytest.mark.parametrize("text, want", ASKS)
def test_the_words_that_ask_for_attempts_table(text, want):
    req = A.parse_request(text)
    assert req is not None, text
    if isinstance(want, int):
        assert req.count == want and not req.names, (text, req)
    else:
        assert req.names == want and req.count == 0, (text, req)


async def _ordinary(app, monkeypatch, repo, text: str, turn: str) -> list[tuple[str, dict]]:
    world = wire_models(app, monkeypatch, World({"claude-cli": ["Sure."]}))
    view = await new_session(app, repo)
    status, raw = await open_turn(app, view["id"], {"text": text, "turn_id": turn}, timeout=60.0)
    assert status == 200, (text, raw)
    frames = frames_of(raw)
    assert not any(e == A.EVENT for e, _ in frames), text
    assert A.LINE_KEY not in dict(frames).get("done", {}), text
    assert {c["provider"] for c in world.calls} <= {"claude-cli"}, text
    return frames


@pytest.mark.parametrize("text", ORDINARY)
async def test_coding_words_that_start_with_try_stay_an_ordinary_turn(app, fake, repo, monkeypatch, text):
    await enable(app)
    await _ordinary(app, monkeypatch, repo, text, "ord-x")


def test_the_work_keeps_its_line_breaks_and_pasted_code():
    """The work every attempt is given is the message as written: its pasted
    code keeps its lines (it was joined into one line, so a function pasted
    with the request reached every attempt flattened)."""
    text = "Try this with Claude and Codex: fix the function below.\n```py\ndef f():\n    return 1\n```"
    req = A.parse_request(text)
    assert req is not None and req.names == ["Claude", "Codex"]
    assert req.task.startswith("fix the function below.")
    assert "```py\ndef f():\n    return 1\n```" in req.task
    last = A.parse_request("The parser drops a field.\nSee parse.py.\nTry it on two models.")
    assert last is not None and last.count == 2
    assert last.task == "The parser drops a field.\nSee parse.py."


# --------------------------------------------------------------------------- #
# 4. The pay-per-use limit counts what attempts spent
# --------------------------------------------------------------------------- #

GEMINI = ("google", "gemini-3.8-flash")


class _Priced(ScriptedModel):
    """A pay-per-use model double whose every call costs $0.75 (its own
    figure, the way a metered adapter reports it)."""

    async def stream(self, *, system, messages, tools, **kw):
        self.world.calls.append({"provider": self.provider, "model": self.model, "tools": []})
        usage = {"input_tokens": 1000, "output_tokens": 50, "cost_usd": 0.75}
        yield {"type": "text", "text": "Done."}
        yield {"type": "final", "response": LLMResponse(text="Done.", usage=usage)}


def _wire_gemini(app, monkeypatch, world: World) -> None:
    mgr = app.state.platform.providers
    mgr.register("google", lambda model=None: _Priced("google", model, world))
    real = mgr.available
    monkeypatch.setattr(mgr, "available", lambda name: True if name == "google" else real(name))


async def test_an_attempts_metered_spend_counts_toward_the_pay_limit_and_refuses_later_work(
    app, fake, repo, monkeypatch,
):
    """An attempt on a pay-per-use model spends the user's money: it counts
    toward the model team's monthly limit (work the user starts counts), so
    once the limit is reached a later attempt AND a team piece on a
    pay-per-use member are refused. It was never counted: the attempts'
    usage rows are the chat lane's, which the month's reading never saw."""
    from iron_jarvis.agents import model_team as MT

    await enable(app)
    menu = [*MENU, {"provider": GEMINI[0], "model": GEMINI[1], "available": True, "on_this_network": False}]
    world = wire_models(app, monkeypatch, World({"ollama": ["Done."]}), menu=menu)
    _wire_gemini(app, monkeypatch, world)
    platform = app.state.platform
    platform.config.team_paid_cap_usd = 1.0
    assert MT.paid_spend_this_month(platform) == 0.0
    view = await new_session(app, repo)
    sid = view["id"]
    frames = await attempts_turn(app, sid, "Try this with Gemini and the local model: fix add.", "pay-1")
    gem = item_for(card_of(frames), "google")
    assert gem["cost"] == "pay" and gem["usage"]["cost_usd"] >= 0.75
    # The attempt's metered dollars count toward the month.
    spent = MT.paid_spend_this_month(platform)
    assert spent == pytest.approx(gem["usage"]["cost_usd"]) and spent >= 0.75
    # A second spend crosses the $1.00 limit…
    await attempts_turn(app, sid, "Try this with Gemini and the local model: add a docstring.", "pay-2")
    assert MT.paid_spend_this_month(platform) >= 1.0
    # …and then a later attempt on a pay-per-use model is refused in words…
    status, raw = await open_turn(app, sid, {"text": "Try this with Gemini and the local model: add a test.",
                                             "turn_id": "pay-3"})
    assert status == 400, raw
    assert "has reached your $1.00 limit" in json.loads(raw)["detail"]
    # …and so is a team piece on a pay-per-use member.
    member = SimpleNamespace(name="gem-coder", provider=GEMINI[0], model=GEMINI[1], account="",
                             roles_json='["code"]', tools_json="[]", base_type="builder")
    assert "has reached your $1.00 limit" in MT.pay_refusal(platform, member)


# --------------------------------------------------------------------------- #
# 5/6. The attempts' folders: Branch from here, Discard all, delete, rewind
# --------------------------------------------------------------------------- #

TRY2 = "Try this with Codex and the local model: fix add in calc.py."


def _line(app, sid: str, turn: str) -> dict:
    return next(m for m in messages_of(app, sid) if m.get("role") == "assistant" and m.get("turnId") == turn)


async def test_a_branched_session_never_keeps_the_origins_attempts(app, fake, repo, monkeypatch):
    """Branch from here copies the conversation — and the attempts card in it
    pointed at the ORIGIN's folders. A session with no branch of its own,
    branched from one that tried on several models, pressed Keep and ADOPTED
    the origin's attempt branch (two sessions in one worktree) and sent the
    origin's other attempts to the trash. The copy now names where it came
    from and can do nothing to those folders; the origin still can."""
    await enable(app)
    wire_models(app, monkeypatch, World({"codex-cli": passing_script(), "ollama": failing_script()}))
    view = await new_session(app, repo, branch=False)
    sid = view["id"]
    card = card_of(await attempts_turn(app, sid, TRY2, "br-1"))
    codex, local = item_for(card, "codex-cli"), item_for(card, "ollama")
    status, branched = await asgi(app, "POST", f"/build/sessions/{sid}/branch", {"turn_id": "br-1", "at": "reply"})
    assert status == 201, branched
    bid = branched["id"]
    copied = _line(app, bid, "br-1")[A.LINE_KEY]
    assert copied["from_session"] == sid
    assert all("git" not in it for it in copied["items"])
    for path in ("keep", "discard"):
        status, body = await asgi(app, "POST", f"/build/sessions/{bid}/attempts/{path}",
                                  {"turn_id": "br-1", "attempt": codex["id"]})
        assert status == 409 and body["detail"] == A.KEEP_COPIED, (path, body)
    assert "git" not in setup_of(app, bid)
    assert Path(codex["git"]["worktree"]).exists() and Path(local["git"]["worktree"]).exists()
    # Deleting the copy leaves the origin's folders alone.
    status, _ = await asgi(app, "DELETE", f"/build/sessions/{bid}")
    assert status == 200
    assert Path(codex["git"]["worktree"]).exists() and Path(local["git"]["worktree"]).exists()
    # The origin keeps its own.
    status, kept = await asgi(app, "POST", f"/build/sessions/{sid}/attempts/keep",
                              {"turn_id": "br-1", "attempt": codex["id"]})
    assert status == 200, kept
    assert setup_of(app, sid)["git"]["branch"] == codex["branch"]


async def test_discard_all_sends_every_attempts_folder_to_the_trash_and_keeps_the_branches(
    app, fake, repo, monkeypatch,
):
    await enable(app)
    wire_models(app, monkeypatch, World({"codex-cli": passing_script(), "ollama": failing_script()}))
    view = await new_session(app, repo)
    sid = view["id"]
    before = snapshot(repo)
    card = card_of(await attempts_turn(app, sid, TRY2, "dc-1"))
    folders = [Path(it["git"]["worktree"]) for it in card["items"]]
    assert all(f.exists() for f in folders)
    status, out = await asgi(app, "POST", f"/build/sessions/{sid}/attempts/discard", {"turn_id": "dc-1"})
    assert status == 200, out
    assert sorted(out["discarded"]) == sorted(it["id"] for it in card["items"])
    assert not any(f.exists() for f in folders)
    for it in card["items"]:
        assert git("rev-parse", "--verify", f"refs/heads/{it['branch']}", cwd=repo)
    saved = _line(app, sid, "dc-1")[A.LINE_KEY]
    assert all(it.get("trashed") for it in saved["items"]) and saved.get("discarded_at")
    trash = Path(app.state.platform.config.home) / "trash"
    assert len(list(trash.rglob("worktree-try-*"))) >= len(folders)
    # Nothing left to discard, and Keep says the folder is gone.
    status, again = await asgi(app, "POST", f"/build/sessions/{sid}/attempts/discard", {"turn_id": "dc-1"})
    assert status == 409 and again["detail"] == A.DISCARD_NONE
    status, kept = await asgi(app, "POST", f"/build/sessions/{sid}/attempts/keep",
                              {"turn_id": "dc-1", "attempt": card["items"][0]["id"]})
    assert status == 409 and kept["detail"] == A.KEEP_GONE
    assert snapshot(repo) == before


async def test_deleting_a_session_takes_its_attempts_folders_to_the_trash(app, fake, repo, monkeypatch):
    """Attempts the user never kept used to stay beside the repository
    forever once their session was deleted (nothing knew them any more)."""
    await enable(app)
    wire_models(app, monkeypatch, World({"codex-cli": passing_script(), "ollama": failing_script()}))
    view = await new_session(app, repo)
    sid = view["id"]
    card = card_of(await attempts_turn(app, sid, TRY2, "del-1"))
    folders = [Path(it["git"]["worktree"]) for it in card["items"]]
    status, out = await asgi(app, "DELETE", f"/build/sessions/{sid}")
    assert status == 200, out
    assert not any(f.exists() for f in folders)
    for it in card["items"]:
        assert git("rev-parse", "--verify", f"refs/heads/{it['branch']}", cwd=repo)


async def test_rewinding_past_a_try_turn_with_its_folders_still_there_is_refused(app, fake, repo, monkeypatch):
    """A rewind cuts the card away — the only place Keep and Discard all
    live — and left the attempts' folders on disk with nothing that knows
    them. It is refused in words (nothing touched) until one is kept or all
    are discarded; after Keep it goes through and SAYS the kept attempt
    stays in the session."""
    await enable(app)
    wire_models(app, monkeypatch, World({"codex-cli": passing_script(), "ollama": failing_script()}))
    view = await new_session(app, repo)
    sid = view["id"]
    card = card_of(await attempts_turn(app, sid, TRY2, "rw-1"))
    status, preview = await asgi(app, "GET", f"/build/sessions/{sid}/rewind?to=rw-1")
    assert status == 200, preview
    assert preview["can_rewind"] is False and "Discard all" in preview["detail"]
    status, out = await asgi(app, "POST", f"/build/sessions/{sid}/rewind", {"to": "rw-1"})
    assert status == 409, out
    assert any(m.get("turnId") == "rw-1" for m in messages_of(app, sid))
    codex = item_for(card, "codex-cli")
    status, kept = await asgi(app, "POST", f"/build/sessions/{sid}/attempts/keep",
                              {"turn_id": "rw-1", "attempt": codex["id"]})
    assert status == 200, kept
    status, preview = await asgi(app, "GET", f"/build/sessions/{sid}/rewind?to=rw-1")
    assert preview["can_rewind"] is True, preview
    assert "stays in this session" in preview["note"]
    status, out = await asgi(app, "POST", f"/build/sessions/{sid}/rewind", {"to": "rw-1"})
    assert status == 200 and out["rewound"] is True, out
    assert "stays in this session" in out["note"]


async def test_a_cut_off_card_in_a_session_without_a_branch_can_still_be_kept(app, fake, repo, monkeypatch):
    """A card rebuilt from the frames (a Stop before the card, a shutdown, a
    crash) had none of the card's own facts: it read as "merge into the
    session's branch", and a session working in its folder directly was told
    "This session's own branch is gone". Its finished attempt keeps the
    right way now."""
    from tests.test_build_attempts_v1397 import SLEEPER, shell
    from tests.test_build_sessions_v1367 import turn_frames, until

    await enable(app)
    wire_models(app, monkeypatch, World({
        "codex-cli": passing_script(),
        "ollama": [shell("s1", SLEEPER.format(n="local"))],
    }))
    view = await new_session(app, repo, branch=False)
    sid = view["id"]
    import asyncio

    task = asyncio.create_task(open_turn(app, sid, {"text": TRY2, "turn_id": "co-1"}, timeout=120.0))
    assert await until(lambda: any(e == A.EVENT and d.get("provider") == "codex-cli" and d.get("state") == "done"
                                   for e, d in turn_frames(app, "co-1")), 60.0)
    await app.state.platform.turn_logs.shutdown()
    await asyncio.wait_for(task, 30.0)
    card = _line(app, sid, "co-1")[A.LINE_KEY]
    assert card["adopt"] is True and card["base"] == "main"
    codex = item_for(card, "codex-cli")
    status, kept = await asgi(app, "POST", f"/build/sessions/{sid}/attempts/keep",
                              {"turn_id": "co-1", "attempt": codex["id"]})
    assert status == 200, kept
    assert setup_of(app, sid)["git"]["branch"] == codex["branch"]


# --------------------------------------------------------------------------- #
# 3. A restart: the card says "cut off", nothing restarts on its own
# --------------------------------------------------------------------------- #


async def test_a_restart_cut_off_never_starts_attempts_again_and_never_says_press_retry(
    app, fake, repo, monkeypatch,
):
    """A try turn a restart cut off: the boot's carry-on pass (v1.391.0)
    starts NOTHING (no new attempts, no new branches, no plain turn of the
    request), and the line says so in its own words — the old line said
    "press Retry", and a Retry starts every attempt again (or, for the "Try
    on…" control's message, a plain turn on the session's own model)."""
    import asyncio

    from iron_jarvis.daemon.routes import build as build_routes
    from tests.test_build_attempts_v1397 import SLEEPER, shell
    from tests.test_build_sessions_v1367 import turn_frames, until

    await enable(app)
    world = wire_models(app, monkeypatch, World({
        "codex-cli": passing_script(),
        "ollama": [shell("s1", SLEEPER.format(n="local"))],
    }))
    view = await new_session(app, repo)
    sid = view["id"]
    task = asyncio.create_task(open_turn(app, sid, {"text": TRY2, "turn_id": "rs-1"}, timeout=120.0))
    assert await until(lambda: any(e == A.EVENT and d.get("provider") == "codex-cli" and d.get("state") == "done"
                                   for e, d in turn_frames(app, "rs-1")), 60.0)
    await app.state.platform.turn_logs.shutdown()
    await asyncio.wait_for(task, 30.0)
    line = _line(app, sid, "rs-1")
    assert line["error"] == A.CUT_OFF_LINE and "Retry" not in line["error"]
    worktrees = sorted(p.name for p in (repo.parent / "proj.ij-worktrees").iterdir())
    calls = len(world.calls)
    count = len(messages_of(app, sid))
    # The boot's carry-on pass.
    resumed = await build_routes.resume_after_restart(app.state.d)
    assert resumed == []
    assert len(messages_of(app, sid)) == count
    assert len(world.calls) == calls
    assert sorted(p.name for p in (repo.parent / "proj.ij-worktrees").iterdir()) == worktrees


def test_a_crash_rebuilt_attempts_line_never_says_press_retry():
    from iron_jarvis.daemon.routes.build import replayed_reply_line

    frames = [
        ["attempt", json.dumps({"id": "try1", "label": "Codex", "provider": "codex-cli", "model": "gpt-5.5",
                                "state": "running", "steps": 2, "since": 1, "ask": None,
                                "card": {"base": "ij/s", "base_commit": "a" * 40, "adopt": False}})],
        ["error", json.dumps({"detail": "cut off", "title": "Iron Jarvis stopped"})],
    ]
    lines = replayed_reply_line("cr-2", frames, started_at=1_700_000_000.0)
    assert lines is not None
    line = lines[-1]
    assert line["error"] == A.CUT_OFF_LINE
    assert line[A.LINE_KEY]["base"] == "ij/s" and line[A.LINE_KEY]["items"][0]["why"] == "cut_off"


# --------------------------------------------------------------------------- #
# The session's own cap bounds the attempts, together
# --------------------------------------------------------------------------- #


class _Costly(ScriptedModel):
    """A scripted model whose every call reports its own cost."""

    COST = {"codex-cli": 0.01, "ollama": 2.0}

    async def stream(self, *, system, messages, tools, **kw):
        async for frame in super().stream(system=system, messages=messages, tools=tools, **kw):
            if frame["type"] == "final":
                frame["response"].usage = {**(frame["response"].usage or {}),
                                           "cost_usd": self.COST.get(self.provider, 0.0)}
            yield frame


async def test_the_sessions_cost_cap_bounds_the_attempts_together(app, fake, repo, monkeypatch):
    """The optional per-session cap (decision 8) bounds a reply — and an
    attempts turn is one reply. The attempts ran with NO cap at all. Now the
    attempts share it: once the others' spend plus this one's reaches it, the
    attempt ends the step-limit way and its column says so."""
    from tests.test_build_attempts_v1397 import shell
    from tests.test_build_sessions_v1367 import turn_frames, until

    await enable(app)

    async def after_local_spent(_world):
        assert await until(lambda: any(
            e == A.EVENT and d.get("provider") == "ollama" and d.get("state") == "done"
            for e, d in turn_frames(app, "cap-1")), 30.0)
        return shell("s1", "echo one")

    world = World({
        "codex-cli": [after_local_spent, shell("s2", "echo two"), shell("s3", "echo three"),
                      "Did all three."],
        "ollama": ["Done in one go."],
    })
    wire_models(app, monkeypatch, world)
    mgr = app.state.platform.providers
    for prov in ("codex-cli", "ollama"):
        mgr.register(prov, lambda model=None, _p=prov: _Costly(_p, model, world))
    view = await new_session(app, repo)
    sid = view["id"]
    status, _ = await asgi(app, "PATCH", f"/build/sessions/{sid}", {"cap": {"usd": 1.0}})
    assert status == 200
    card = card_of(await attempts_turn(app, sid, TRY2, "cap-1"))
    codex = item_for(card, "codex-cli")
    assert codex["why"] == "stopped" and "cost limit" in codex["words"], codex
    # It stopped at the first round after the local attempt's $2.00 landed:
    # its second and third commands never ran.
    commands = [c for c in world.calls if c["provider"] == "codex-cli"]
    assert len(commands) <= 2, commands
    assert "Did all three." not in json.dumps(card)


# --------------------------------------------------------------------------- #
# 7. The safety floors still card inside an attempt, on its own row
# --------------------------------------------------------------------------- #


async def _asking_row(app, turn: str, provider: str):
    from tests.test_build_sessions_v1367 import turn_frames, until

    def asking():
        return next((d for e, d in turn_frames(app, turn)
                     if e == A.EVENT and d.get("provider") == provider
                     and isinstance(d.get("ask"), dict) and d["ask"].get("id")), None)

    return await until(asking, 60.0)


async def test_the_git_guard_cards_an_attempts_commit_once_only_even_in_yolo(app, fake, repo, monkeypatch):
    import asyncio

    from tests.test_build_attempts_v1397 import shell

    await enable(app)
    wire_models(app, monkeypatch, World({
        "codex-cli": ["Nothing to do."],
        "ollama": [shell("g1", "git commit -am wip"), "Tried to commit."],
    }))
    view = await new_session(app, repo, approval_mode="yolo")
    sid = view["id"]
    task = asyncio.create_task(open_turn(app, sid, {"text": TRY2, "turn_id": "gg-1"}, timeout=120.0))
    row = await _asking_row(app, "gg-1", "ollama")
    assert row is not None, "the attempt's commit asked on its row"
    ask = row["ask"]
    assert ask["tool"] == "shell" and ask.get("once_only") is True and ask.get("once_reason") == "git", ask
    status, _ = await asgi(app, "POST", f"/chat/approvals/{ask['id']}", {"decision": "deny"})
    assert status == 200
    status, raw = await task
    assert status == 200
    local = item_for(card_of(frames_of(raw)), "ollama")
    wt = Path(local["git"]["worktree"])
    # Nothing was committed on the attempt's branch by the agent.
    assert git("rev-list", "--count", "HEAD", cwd=wt) == git("rev-list", "--count", "HEAD", cwd=repo)


async def test_a_local_only_attempts_command_cards_once_only(app, fake, repo, monkeypatch):
    import asyncio

    from tests.test_build_attempts_v1397 import shell

    await enable(app)
    menu = [*MENU, {"provider": "ollama", "model": "qwen-local", "available": True, "on_this_network": True}]
    wire_models(app, monkeypatch, World({"ollama": [shell("l1", "echo hi"), "Ran it."]}), menu=menu)
    view = await new_session(app, repo, approval_mode="yolo")
    sid = view["id"]
    status, _ = await asgi(app, "PATCH", f"/build/sessions/{sid}", {"local_only": True})
    assert status == 200
    task = asyncio.create_task(open_turn(app, sid, {
        "text": "say hi", "turn_id": "lo-a",
        "try_on": [{"provider": "ollama", "model": "glm-local"}, {"provider": "ollama", "model": "qwen-local"}],
    }, timeout=120.0))
    row = await _asking_row(app, "lo-a", "ollama")
    assert row is not None
    assert row["ask"].get("once_only") is True and row["ask"]["tool"] == "shell", row["ask"]
    for _ in range(2):
        r = await _asking_row(app, "lo-a", "ollama")
        if r is None:
            break
        await asgi(app, "POST", f"/chat/approvals/{r['ask']['id']}", {"decision": "once"})
        await asyncio.sleep(0.2)
    status, raw = await task
    assert status == 200 and frames_of(raw)[-1][0] == "done"
