"""Idea 33-a: the notes of a recording are CHECKED by code, and written by a
model the user's data may go to (D-33.1).

* An item stays only when its time is inside the recording and the words it
  quotes were said within a minute of that time, in the record of what was
  heard: an item quoting words not said is dropped, and the notes say how
  many were.
* Who writes them: a model on this PC or your network when there is one, with
  no question; otherwise the chat's OWN model (never another cloud model in
  its place), only after the user's yes on the card, once per chat and
  provider; never under Local only; never the demo model. Every call is
  pinned to the model named (no failover moves the transcript).
* On the phone (no card there) the notes wait for that yes, and the reply
  says where it is given.
* An Undo pressed while the notes are being written brings nothing home.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis.documents import recording as R
from iron_jarvis.documents import recording_jobs as J
from tests._fakes.recordings import CLOUD_CHOICE, LOCAL_CHOICE, NOTES_ANSWER, NotesModel

pytestmark = pytest.mark.timeout(120)

LINES = [
    R.Line(0.5, "good morning everyone"),
    R.Line(70.2, "we decided to move the launch to march"),
    R.Line(95.0, "i will send the revised plan on friday"),
    R.Line(110.0, "maria will check the numbers"),
    R.Line(400.0, "and we agreed to cut the budget in half"),
]


def _item(time: str, said: str, note: str = "n") -> dict:
    return {"time": time, "said": said, "note": note}


def test_an_item_quoting_words_not_said_is_dropped_and_counted():
    raw = {"decisions": [_item("01:10", "we decided to move the launch to march"),
                         _item("01:12", "we agreed to double the budget")],
           "you_owe": [], "they_owe": []}
    checked = R.check_notes(raw, LINES, 420.0)
    assert [it["note"] for it in checked.items["decisions"]] == ["n"]
    assert checked.items["decisions"][0]["time"] == "01:10"
    assert checked.dropped == 1
    assert R.dropped_words(1) == "1 item was left out: its words are not in the recording near the time given."


def test_words_said_but_far_from_the_time_given_are_dropped():
    # "we agreed to cut the budget in half" WAS said, at 6:40, not near 1:12.
    raw = {"decisions": [_item("01:12", "we agreed to cut the budget in half")], "you_owe": [], "they_owe": []}
    assert R.check_notes(raw, LINES, 420.0).dropped == 1
    raw = {"decisions": [_item("06:20", "we agreed to cut the budget in half")], "you_owe": [], "they_owe": []}
    assert R.check_notes(raw, LINES, 420.0).dropped == 0, "within a minute of its line"


def test_a_time_outside_the_recording_or_not_a_time_is_dropped():
    for t in ("99:00", "751", "12:75", "", "soon"):
        raw = {"decisions": [_item(t, "we decided to move the launch to march")], "you_owe": [], "they_owe": []}
        assert R.check_notes(raw, LINES, 420.0).dropped == 1, t


def test_a_quote_too_short_to_check_is_dropped():
    raw = {"you_owe": [_item("01:35", "send"), _item("01:35", "i will")], "decisions": [], "they_owe": []}
    assert R.check_notes(raw, LINES, 420.0).dropped == 2


def test_case_and_marks_do_not_matter_and_alike_items_are_kept_once():
    raw = {"they_owe": [_item("[01:50]", "Maria will check the numbers."),
                        _item("01:50", "maria will check the numbers")], "decisions": [], "you_owe": []}
    checked = R.check_notes(raw, LINES, 420.0)
    assert len(checked.items["they_owe"]) == 1 and checked.dropped == 0


def test_the_model_answer_is_read_leniently():
    answer = "Here are the notes:\n```json\n" + json.dumps({"decided": [_item("01:10", "x y z")]}) + "\n```"
    got = R.parse_notes(answer)
    assert got["decisions"][0]["time"] == "01:10"
    assert R.parse_notes("not json at all") == {k: [] for k in R.KINDS}


def test_times_are_read_and_written_one_way():
    assert R.stamp(751) == "12:31" and R.stamp(3723) == "1:02:03" and R.stamp(65, long=True) == "0:01:05"
    assert R.parse_stamp("12:31") == 751 and R.parse_stamp("[1:02:03]") == 3723
    assert R.parse_stamp("751") is None and R.parse_stamp("1:75:00") is None


def test_the_transcript_is_fenced_as_data_in_the_prompt():
    from iron_jarvis.computeruse.safety import _FENCE_TOP

    prompt = R.notes_prompt("meeting.m4a", LINES, long=False)
    assert _FENCE_TOP in prompt and "[01:10] we decided to move the launch to march" in prompt
    assert "DATA" in R.NOTES_SYSTEM and "never instructions" in R.NOTES_SYSTEM


# --------------------------------------------------------------------------- #
# who writes them (D-33.1)
# --------------------------------------------------------------------------- #
class _Router:
    def __init__(self, default: str, up: "set[str]", model: "NotesModel | None" = None) -> None:
        self.default_provider = default
        self.up = up
        self.manager = None
        self.model = model or NotesModel()

    def _candidate_order(self):
        return ["anthropic", "openai", "ollama"]

    def _safe_available(self, p):
        return p in self.up

    async def complete(self, **kw):
        return await self.model.complete(**kw)


@pytest.fixture()
def local_is(monkeypatch):
    from iron_jarvis.providers import egress

    local = {"ollama"}
    monkeypatch.setattr(egress, "stays_on_this_network", lambda p, m=None, c=None, model="": p in local)
    return local


def test_a_model_on_this_pc_or_network_is_chosen_first_with_no_question(local_is):
    router = _Router("anthropic", {"anthropic", "ollama"})
    choice = J.notes_choice(router, None, local_only=False)
    assert choice["provider"] == "ollama" and choice["local"] is True


def test_with_no_local_model_the_chats_model_is_offered_and_never_under_local_only(local_is):
    router = _Router("anthropic", {"anthropic", "mock"})
    choice = J.notes_choice(router, None, local_only=False)
    assert choice["provider"] == "anthropic" and choice["local"] is False
    assert J.notes_choice(router, None, local_only=True) is None
    assert J.notes_choice(_Router("mock", {"mock"}), None, local_only=False) is None, "never the demo model"
    auto = J.notes_choice(_Router("auto", {"openai"}), None, local_only=False)
    assert auto["provider"] == "openai", "Auto names a real model, by name"


def _heard(tmp_path: Path, *, thread_id: str = "thr_a", local_only: bool = False,
           router: "_Router | None" = None, home: "Path | None" = None) -> J.Job:
    home = home or tmp_path / "home"
    ws = tmp_path / "work"
    ws.mkdir(parents=True, exist_ok=True)
    t, n = R.target_paths(ws, "meeting.m4a")
    tr = R.Transcriber(R.THIS_PC, "the offline speech model on this PC", model="C:/fake")
    state = J.new_state(source=ws / "meeting.m4a", sha256="x", workspace=ws, thread_id=thread_id, turn_id="t1",
                        local_only=local_only, transcriber=tr, transcript=t, notes=n)
    state.update({"status": J.STATUS_NOTES, "duration": 420.0, "heard_s": 420.0, "transcript_written": True})
    J.save_lines(home, state["id"], LINES)
    J.save_state(home, state)
    env = J.Env(engine=None, event_bus=None, config=SimpleNamespace(home=home),
                router_resolver=lambda: router)
    return J.Job(state=state, env=env, transcriber=tr)


def test_the_notes_are_written_checked_and_saved_and_every_call_is_pinned(tmp_path, monkeypatch):
    router = _Router("ollama", {"ollama"})
    job = _heard(tmp_path, router=router)
    monkeypatch.setattr(J, "notes_choice", lambda r, c, local_only, chat=None: dict(LOCAL_CHOICE))
    asyncio.run(J._notes(job))
    st = job.state
    assert st["status"] == J.STATUS_DONE and st["notes_written"] is True
    assert st["counts"] == {"decisions": 1, "you_owe": 1, "they_owe": 1, "dropped": 2}
    text = Path(st["notes"]).read_text(encoding="utf-8")
    assert "- [01:10] The launch moves to March." in text
    assert "budget" not in text, "the item quoting words never said is not in the notes"
    assert "2 items were left out" in text and R.WHO_OWES_WORDS in text
    assert st["notes_by"] == "Ollama on this PC"
    calls = router.model.calls
    assert calls and all(c["pin"] is True and c["provider"] == "ollama" for c in calls)
    card = J.card_of(st)
    assert card["counts_words"] == "1 decision, 1 thing you owe, 1 thing they owe"
    assert card["items"]["you_owe"][0]["time"] == "01:35"


def test_a_cloud_model_is_asked_once_per_chat_and_only_the_press_sends(tmp_path, monkeypatch):
    router = _Router("anthropic", {"anthropic"})
    monkeypatch.setattr(J, "notes_choice", lambda r, c, local_only, chat=None: dict(CLOUD_CHOICE))
    job = _heard(tmp_path, router=router)
    asyncio.run(J._notes(job))
    assert job.state["status"] == J.STATUS_ASK
    assert J.card_of(job.state)["offer"]["name"] == "Claude"
    assert router.model.calls == [], "nothing is sent before the yes"

    async def _press():
        out = await J.notes_after_yes(job.env, job.id, provider="anthropic", model=None, thread_id="thr_a",
                                      local_only=False)
        await J.live(job.id).task
        return out

    out = asyncio.run(_press())
    assert out["ok"] is True
    after = J.load_state(job.env.home, job.id)
    assert after["notes_written"] is True and after["notes_by"] == "Claude"
    assert router.model.calls and all(c["provider"] == "anthropic" and c["pin"] for c in router.model.calls)
    assert J.consent_given(job.env.home, "thr_a", "anthropic")
    assert not J.consent_given(job.env.home, "thr_a", "openai"), "a yes for one provider is not a yes for another"
    assert not J.consent_given(job.env.home, "thr_b", "anthropic"), "nor in another chat"

    # The next recording in the same chat is written without asking again...
    again = _heard(tmp_path / "2", router=router, home=job.env.home)
    asyncio.run(J._notes(again))
    assert again.state["status"] == J.STATUS_DONE and again.state["notes_written"] is True
    # ...but one in another chat asks.
    other = _heard(tmp_path / "3", thread_id="thr_b", router=router, home=job.env.home)
    asyncio.run(J._notes(other))
    assert other.state["status"] == J.STATUS_ASK


def test_a_press_for_a_model_that_is_no_longer_the_choice_sends_nothing(tmp_path, monkeypatch):
    router = _Router("anthropic", {"anthropic"})
    job = _heard(tmp_path, router=router)
    monkeypatch.setattr(J, "notes_choice", lambda r, c, local_only, chat=None: dict(CLOUD_CHOICE))
    asyncio.run(J._notes(job))
    # Settings changed: the notes would now go to OpenAI.
    monkeypatch.setattr(J, "notes_choice", lambda r, c, local_only, chat=None: {
        "provider": "openai", "model": None, "name": "ChatGPT", "local": False})
    out = asyncio.run(J.notes_after_yes(job.env, job.id, provider="anthropic", model=None, thread_id="thr_a",
                                        local_only=False))
    assert out["ok"] is False and out["changed"] is True
    assert out["card"]["offer"]["name"] == "ChatGPT", "the card asks again, in its new words"
    assert router.model.calls == []
    assert not J.consent_given(job.env.home, "thr_a", "anthropic")


def test_a_press_in_a_chat_made_local_only_sends_nothing(tmp_path, monkeypatch):
    router = _Router("anthropic", {"anthropic"})
    job = _heard(tmp_path, router=router)
    monkeypatch.setattr(J, "notes_choice",
                        lambda r, c, local_only, chat=None: None if local_only else dict(CLOUD_CHOICE))
    asyncio.run(J._notes(job))
    out = asyncio.run(J.notes_after_yes(job.env, job.id, provider="anthropic", model=None, thread_id="thr_a",
                                        local_only=True))
    assert out["ok"] is False and router.model.calls == []
    assert "Local only" in out["card"]["notes_why"]


def test_a_local_only_turn_never_offers_a_cloud_model(tmp_path, monkeypatch, local_is):
    router = _Router("anthropic", {"anthropic"})
    job = _heard(tmp_path, local_only=True, router=router)
    asyncio.run(J._notes(job))
    assert job.state["status"] == J.STATUS_DONE and job.state.get("offer") is None
    assert "Local only" in job.state["notes_why"] and router.model.calls == []


def test_the_demo_model_never_writes_notes(tmp_path, monkeypatch):
    router = _Router("ollama", {"ollama"}, model=NotesModel(provider="mock"))
    router.model.provider = "mock"

    async def as_mock(**kw):
        router.model.calls.append(kw)
        return SimpleNamespace(provider="mock", response=SimpleNamespace(text=json.dumps(NOTES_ANSWER)))

    router.complete = as_mock
    job = _heard(tmp_path, router=router)
    monkeypatch.setattr(J, "notes_choice", lambda r, c, local_only, chat=None: dict(LOCAL_CHOICE))
    asyncio.run(J._notes(job))
    assert job.state["notes_written"] is False
    assert "demo model" in job.state["notes_why"] and not Path(job.state["notes"]).exists()


# --------------------------------------------------------------------------- #
# the chat's OWN model (D-33.1)
# --------------------------------------------------------------------------- #
def test_the_cloud_notes_go_to_the_chats_own_model_and_no_other(local_is):
    """A chat on ChatGPT (not the default, Claude) is offered ChatGPT, with its
    model; a chat whose own model is not available is offered NOTHING, never
    the default in its place; a model on this PC or your network still comes
    first; a chat on the default or on Auto follows the default route."""
    from iron_jarvis.providers.egress import friendly_name

    router = _Router("anthropic", {"anthropic", "openai"})
    own = J.notes_choice(router, None, local_only=False, chat=("openai", "gpt-test"))
    assert own == {"provider": "openai", "model": "gpt-test", "name": friendly_name("openai"), "local": False}
    down = _Router("anthropic", {"anthropic"})
    assert J.notes_choice(down, None, local_only=False, chat=("openai", "gpt-test")) is None, (
        "never another cloud model in place of the chat's own")
    words = J.nobody_words(down, ("openai", "gpt-test"), local_only=False)
    assert friendly_name("openai") in words and "the model this chat uses" in words
    assert "not sent anywhere" in words
    with_local = _Router("anthropic", {"anthropic", "openai", "ollama"})
    assert J.notes_choice(with_local, None, local_only=False, chat=("openai", "gpt-test"))["provider"] == "ollama"
    for follows in (None, ("", ""), ("auto", ""), ("mock", "")):
        assert J.notes_choice(router, None, local_only=False, chat=follows)["provider"] == "anthropic", follows
    assert J.notes_choice(router, None, local_only=True, chat=("openai", "gpt-test")) is None


def test_a_recording_handed_in_a_chat_on_another_model_offers_that_model(tmp_path, local_is):
    """The chat's model rides the job (the turn's choice), so the card names it
    and the press sends to it, pinned."""
    router = _Router("anthropic", {"anthropic", "openai"})
    job = _heard(tmp_path, router=router)
    job.state["chat_model"] = ["openai", "gpt-test"]
    asyncio.run(J._notes(job))
    assert job.state["status"] == J.STATUS_ASK
    offer = J.card_of(job.state)["offer"]
    assert offer["provider"] == "openai" and offer["model"] == "gpt-test"

    async def _press():
        out = await J.notes_after_yes(job.env, job.id, provider="openai", model="gpt-test", thread_id="thr_a",
                                      local_only=False)
        await J.live(job.id).task
        return out

    assert asyncio.run(_press())["ok"] is True
    calls = router.model.calls
    assert calls and all(c["provider"] == "openai" and c["model"] == "gpt-test" and c["pin"] for c in calls)


def test_the_tool_keeps_the_chats_own_model_with_the_job():
    from iron_jarvis.documents.recording_tools import _chat_model_of

    assert _chat_model_of(SimpleNamespace(chat_model=("openai", "gpt-test"))) == ("openai", "gpt-test")
    assert _chat_model_of(SimpleNamespace(chat_model=("", ""))) is None
    assert _chat_model_of(SimpleNamespace()) is None
    tr = R.Transcriber(R.THIS_PC, "the offline speech model on this PC", model="C:/fake")
    st = J.new_state(source=Path("m.m4a"), sha256="x", workspace=Path("."), thread_id="t", turn_id="u",
                     local_only=False, transcriber=tr, transcript=Path("a"), notes=Path("b"),
                     chat_model=("openai", "gpt-test"))
    assert J.chat_model_of(st) == ("openai", "gpt-test")
    assert J.chat_model_of({"chat_model": None}) is None


# --------------------------------------------------------------------------- #
# the phone: the yes waits for the card on this PC
# --------------------------------------------------------------------------- #
def test_on_the_phone_the_notes_wait_for_the_yes_and_the_reply_says_where(tmp_path, monkeypatch):
    from iron_jarvis.documents.recording_tools import find_earlier, result_words

    router = _Router("anthropic", {"anthropic"})
    monkeypatch.setattr(J, "notes_choice", lambda r, c, local_only, chat=None: dict(CLOUD_CHOICE))
    monkeypatch.setattr(J, "_answers_the_phone", lambda: True)
    job = _heard(tmp_path, router=router)
    Path(job.state["transcript"]).write_text("[01:10] we decided to move the launch to march\n", encoding="utf-8")
    asyncio.run(J._notes(job))
    st = job.state
    assert st["status"] == J.STATUS_ASK and st["offer"]["name"] == "Claude", "it waits for the yes"
    assert router.model.calls == [], "nothing is sent from the phone"
    said = result_words(st, finished=True, phone=True)
    assert "Iron Jarvis on this PC" in said and "Write the notes" in said and "nothing was sent" in said
    assert "card under your reply" not in said and "asked on the recording's card" not in said
    # On this PC the same recording in the same folder is that job, with its card.
    earlier = find_earlier(job.env.home, "x", Path(st["workspace"]))
    assert earlier is not None and earlier["id"] == job.id
    assert J.card_of(earlier)["offer"]["name"] == "Claude"
    assert "card under your reply asks the user" in result_words(earlier, finished=True, phone=False)
    monkeypatch.setattr(J, "_answers_the_phone", lambda: False)

    async def _press():
        out = await J.notes_after_yes(job.env, job.id, provider="anthropic", model=None, thread_id="",
                                      local_only=False)
        await J.live(job.id).task
        return out

    assert asyncio.run(_press())["ok"] is True
    assert J.load_state(job.env.home, job.id)["notes_written"] is True


# --------------------------------------------------------------------------- #
# an Undo while the notes are written brings nothing home
# --------------------------------------------------------------------------- #
class _GatedModel(NotesModel):
    """Answers only when the test lets it: the Undo lands while it writes."""

    def __init__(self) -> None:
        super().__init__()
        self.asked: "asyncio.Event | None" = None
        self.answer_now: "asyncio.Event | None" = None

    async def complete(self, **kw):
        self.asked.set()
        await self.answer_now.wait()
        return await super().complete(**kw)


def _reports(monkeypatch) -> list:
    from iron_jarvis.agents import chat_delivery

    sent: list = []
    monkeypatch.setattr(chat_delivery, "report_to_chat",
                        lambda platform, tid, report: sent.append((tid, report)) or {"ok": True})
    return sent


async def _undo_while_writing(job: J.Job, model: "_GatedModel") -> bool:
    """The tool's ``revert`` stops the job first; the model answers after."""
    await model.asked.wait()
    stopping = asyncio.ensure_future(J.stop(job.id, "undo"))
    while not job.stopped:
        await asyncio.sleep(0)  # a yield, not a wait: stop() marks the job before it awaits
    model.answer_now.set()
    stopped = await stopping
    await job.task
    return stopped


def test_an_undo_while_the_notes_are_written_after_the_yes_reports_nothing(tmp_path, monkeypatch):
    sent = _reports(monkeypatch)
    model = _GatedModel()
    router = _Router("anthropic", {"anthropic"}, model=model)
    monkeypatch.setattr(J, "notes_choice", lambda r, c, local_only, chat=None: dict(CLOUD_CHOICE))
    job = _heard(tmp_path, router=router)
    asyncio.run(J._notes(job))
    assert job.state["status"] == J.STATUS_ASK

    async def _go():
        model.asked, model.answer_now = asyncio.Event(), asyncio.Event()
        out = await J.notes_after_yes(job.env, job.id, provider="anthropic", model=None, thread_id="thr_a",
                                      local_only=False)
        assert out["ok"] is True
        live = J.live(job.id)
        return await _undo_while_writing(live, model), live

    stopped, live = asyncio.run(_go())
    assert stopped is True
    assert sent == [], "no report says the transcript is ready while Undo removes it"
    assert live.state["status"] == J.STATUS_UNDONE and live.state["notes_written"] is False
    assert not Path(live.state["notes"]).exists()


def test_an_undo_while_a_restarted_job_writes_its_notes_reports_nothing(tmp_path, monkeypatch):
    sent = _reports(monkeypatch)
    model = _GatedModel()
    router = _Router("ollama", {"ollama"}, model=model)
    monkeypatch.setattr(J, "notes_choice", lambda r, c, local_only, chat=None: dict(LOCAL_CHOICE))
    job = _heard(tmp_path, router=router)
    Path(job.state["source"]).write_bytes(b"RIFF")  # the recording is still there

    async def _go():
        model.asked, model.answer_now = asyncio.Event(), asyncio.Event()
        assert await J.reconcile_at_boot(job.env) == 1
        live = J.live(job.id)
        return await _undo_while_writing(live, model), live

    stopped, live = asyncio.run(_go())
    assert stopped is True
    assert sent == []
    assert live.state["status"] == J.STATUS_UNDONE and live.state["notes_written"] is False


def test_a_job_that_ends_normally_still_reports_once(tmp_path, monkeypatch):
    """The guard is for an Undo only: the same path without one comes home."""
    sent = _reports(monkeypatch)
    router = _Router("ollama", {"ollama"})
    monkeypatch.setattr(J, "notes_choice", lambda r, c, local_only, chat=None: dict(LOCAL_CHOICE))
    job = _heard(tmp_path, router=router)
    Path(job.state["source"]).write_bytes(b"RIFF")

    async def _go():
        assert await J.reconcile_at_boot(job.env) == 1
        await J.live(job.id).task

    asyncio.run(_go())
    assert len(sent) == 1 and sent[0][0] == "thr_a"
    assert sent[0][1]["words"].startswith("The transcript is ready")


# --------------------------------------------------------------------------- #
# a failure after the transcript was saved says so
# --------------------------------------------------------------------------- #
def test_a_failure_after_the_transcript_is_saved_says_the_transcript_is_saved(tmp_path, monkeypatch):
    job = _heard(tmp_path)
    job.state["status"] = J.STATUS_HEARING

    async def heard(j):
        j.state["transcript_written"] = True
        j.state["status"] = J.STATUS_NOTES

    async def disk_full(j):
        raise OSError("No space left on device")

    monkeypatch.setattr(J, "_hear", heard)
    monkeypatch.setattr(J, "_notes", disk_full)
    asyncio.run(J._run(job))
    st = job.state
    assert st["status"] == J.STATUS_DONE and not st.get("error")
    assert "The transcript is saved" in st["notes_why"] and "No space left" in st["notes_why"]

    before = _heard(tmp_path / "2")
    before.state["status"] = J.STATUS_HEARING
    before.state["transcript_written"] = False

    async def cannot_read(j):
        raise OSError("the recording cannot be opened")

    monkeypatch.setattr(J, "_hear", cannot_read)
    asyncio.run(J._run(before))
    assert before.state["status"] == J.STATUS_FAILED and "Nothing was saved" in before.state["error"]
