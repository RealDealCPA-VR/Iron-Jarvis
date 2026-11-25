"""12-a (build-out plan R2.5): the ONE card before coding work starts from
the phone.

``/code projA: fix the broken contact form`` gets a card that names the
folder (and the last two parts of its path), the branch rule, the time limit,
what it uses ("Uses your Claude plan (42% of this 5-hour window used)", the
master plan's calm note) and the EXACT words that will be queued, with Start
and Not now (buttons where the destination has them, "Reply 1 …" always).
Every check runs BEFORE the card: a refused one shows no card and keeps
nothing. Start makes exactly one task (checked again first); Not now makes
none; a stranger can neither ask nor answer; an hour-old card starts nothing.
"""

from __future__ import annotations

import itertools
from datetime import timedelta

import pytest

from iron_jarvis.agents import model_team as MT
from iron_jarvis.agents.orchestrator import EMERGENCY_STOP_REFUSAL
from iron_jarvis.comm.models import PendingPromptRecord
from iron_jarvis.comm.prompts import CODE_START_KIND, button_data
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.ids import utcnow
from iron_jarvis.harness import night as N
from iron_jarvis.harness import phone_code as PC
from tests._fakes.phone_back import press, set_local_only
from tests._fakes.phone_code import ME, prompts, report_json_of, rig, tasks, text, used_repo
from tests._fakes.phone_files import run

pytestmark = pytest.mark.usefixtures("no_network")

STRANGER = 999
ASK = "/code projA: fix the broken contact form"


@pytest.fixture()
def phone(platform, tmp_path):
    repo = used_repo(platform, tmp_path / "work" / "projA")
    poll, fake = rig(platform)
    return poll, fake, repo


_IDS = itertools.count(100)


def _card(platform, poll, fake, words: str = ASK) -> PendingPromptRecord:
    fake.updates = [text(next(_IDS), words)]
    res = run(poll)
    assert res[0]["status"] == "code_card", res
    [rec] = [p for p in prompts(platform, CODE_START_KIND) if p.status == "open"]
    return rec


def test_the_card_names_the_folder_the_rules_the_limit_what_it_uses_and_the_exact_words(platform, phone):
    poll, fake, repo = phone
    rec = _card(platform, poll, fake)
    card = fake.sent[-1]
    assert card["text"] == (
        "Iron Jarvis: Start coding work in projA (work/projA) now? It works on a new branch of its own, edits "
        "and runs commands only there, may take up to 90 minutes, and nothing is merged. Uses claude-opus-4-8 "
        "on this PC, at no cost.\nWhat it will do: fix the broken contact form\nReply 1 to start or 2 for not now."
    )
    assert [b["text"] for b in card["reply_markup"]["inline_keyboard"][0]] == ["Start", "Not now"]
    assert [b["callback_data"] for b in card["reply_markup"]["inline_keyboard"][0]] == [
        button_data(rec.id, 1), button_data(rec.id, 2)]
    ref = PC.read_ref(rec)
    assert ref == {"path": str(repo), "name": "projA", "words": "fix the broken contact form", "when": "now"}
    assert rec.question == "Start coding work in projA now?"
    assert tasks(platform) == []  # the card starts nothing
    # The phone chat on the PC shows the ask and the card.
    tid = poll.thread_store.resolve("tg", str(ME), "").id
    lines = [m["content"] for m in poll.thread_store.history_body(tid)]
    assert ASK in lines and any(line.startswith("Start coding work in projA") for line in lines)


def test_the_card_says_the_plan_and_how_much_of_its_window_is_used(platform, phone, monkeypatch):
    poll, fake, _repo = phone
    platform.config.default_provider, platform.config.default_model = "claude-cli", ""
    monkeypatch.setattr(MT, "usage_for", lambda p, provider, account="": {
        "account": "a1", "status": "ready", "percent": 42, "window_mins": 300})
    _card(platform, poll, fake)
    assert "Uses your Claude plan (42% of this 5-hour window used)." in fake.texts[-1]


@pytest.mark.parametrize("case", ["emergency", "not_a_repo", "pay", "local_only"])
def test_a_refused_check_shows_no_card_and_keeps_nothing(platform, tmp_path, case):
    used_repo(platform, tmp_path / "work" / "projA")
    poll, fake = rig(platform)
    words = ASK
    expect = ""
    if case == "emergency":
        platform.config.autonomy_kill_switch = True
        expect = EMERGENCY_STOP_REFUSAL
    elif case == "not_a_repo":
        plain = tmp_path / "work" / "notes"
        plain.mkdir(parents=True)
        from iron_jarvis.harness import sessions as S

        S.create(platform.engine, str(plain), "notes", {})
        words = "/code notes: tidy the readme"
        expect = "A night task works on its own branch, so its folder must be in a git repository"
    elif case == "pay":
        platform.config.default_provider, platform.config.default_model = "openai", "gpt-5"
        platform.config.team_paid_cap_usd = 0.0
        expect = "is billed per use"
    else:
        tid = poll.thread_store.resolve("tg", str(ME), "").id
        set_local_only(platform, tid)
        platform.config.default_provider, platform.config.default_model = "anthropic", "claude-sonnet-4-5"
        expect = "This chat is Local only, so coding work started from it runs only on a model on this PC"
    fake.updates = [text(next(_IDS), words)]
    res = run(poll)
    assert res[0]["status"] == "code_refused", res
    assert expect in fake.texts[-1], fake.texts[-1]
    assert "Start coding work" not in fake.texts[-1]
    assert not any(p.get("reply_markup") for p in fake.sent)
    assert prompts(platform, CODE_START_KIND) == [] and tasks(platform) == []


def test_start_makes_exactly_one_task_with_the_cards_own_words(platform, phone):
    poll, fake, repo = phone
    rec = _card(platform, poll, fake)
    fake.updates = [text(next(_IDS), "1")]
    res = run(poll)
    assert res[0]["status"] == "code_started"
    assert fake.texts[-1] == "Iron Jarvis: → Starting coding work in projA."
    [task] = tasks(platform)
    assert task["words"] == "fix the broken contact form" and task["folder"] == str(repo)
    assert task["state"] == N.QUEUED and task["start_now"] is True
    assert task["asked_from"] == "comm:tg" and task["from_phone"] is True
    assert "report_json" not in task  # where it reports stays off every page
    report = PC.report_of({"report_json": report_json_of(platform, task["id"])})
    assert report["channel"] == "tg" and report["sender_id"] == str(ME) and report["name"] == "projA"
    # A second answer makes nothing more: the card is answered (a "1" now is
    # plain chat), and its button hears "That was already answered".
    fake.updates = [text(next(_IDS), "1"), press(next(_IDS), button_data(rec.id, 1), sender=ME)]
    run(poll)
    assert len(tasks(platform)) == 1
    assert fake.answered()[-1] == "That was already answered"


def test_start_by_button_says_starting_on_the_button_and_makes_one_task(platform, phone):
    poll, fake, _repo = phone
    rec = _card(platform, poll, fake)
    fake.updates = [press(next(_IDS), button_data(rec.id, 1), sender=ME)]
    res = run(poll)
    assert res[0]["status"] == "code_started" and res[0]["pressed"] is True
    assert fake.edits() == ["Starting"]
    # The changed button IS the reply: no second message about it.
    assert not any("→ Starting" in t for t in fake.texts)
    assert len(tasks(platform)) == 1


def test_not_now_makes_none(platform, phone):
    poll, fake, _repo = phone
    rec = _card(platform, poll, fake)
    fake.updates = [text(next(_IDS), "2")]
    res = run(poll)
    assert res[0]["status"] == "code_not_now"
    assert fake.texts[-1] == "Iron Jarvis: → OK, nothing was started."
    assert tasks(platform) == []
    with session_scope(platform.engine) as db:
        assert db.get(PendingPromptRecord, rec.id).status == "answered"


def test_a_stranger_can_neither_ask_nor_answer(platform, phone):
    poll, fake, _repo = phone
    fake.updates = [text(next(_IDS), ASK, sender=STRANGER)]
    assert run(poll)[0]["status"] == "unauthorized"
    assert prompts(platform, CODE_START_KIND) == []
    rec = _card(platform, poll, fake)
    fake.updates = [press(next(_IDS), button_data(rec.id, 1), sender=STRANGER), text(next(_IDS), "1", sender=STRANGER)]
    res = run(poll)
    assert [r["status"] for r in res] == ["unauthorized", "unauthorized"]
    assert tasks(platform) == []
    with session_scope(platform.engine) as db:
        assert db.get(PendingPromptRecord, rec.id).status == "open"


def test_start_checks_again_and_a_refusal_starts_nothing(platform, phone):
    poll, fake, _repo = phone
    rec = _card(platform, poll, fake)
    platform.config.autonomy_kill_switch = True
    fake.updates = [press(next(_IDS), button_data(rec.id, 1), sender=ME)]
    res = run(poll)
    assert res[0]["status"] == "code_refused"
    assert fake.edits() == ["Not started"]
    assert fake.texts[-1] == f"Iron Jarvis: Nothing was started: {EMERGENCY_STOP_REFUSAL}"
    assert tasks(platform) == []


def test_an_hour_old_card_starts_nothing(platform, phone):
    poll, fake, _repo = phone
    rec = _card(platform, poll, fake)
    with session_scope(platform.engine) as db:
        row = db.get(PendingPromptRecord, rec.id)
        row.created_at = utcnow() - timedelta(seconds=PC.CARD_TTL_S + 60)
        db.add(row)
        db.commit()
    fake.updates = [text(next(_IDS), "1")]
    run(poll)
    assert tasks(platform) == []
    with session_scope(platform.engine) as db:
        assert db.get(PendingPromptRecord, rec.id).status == "expired"


def test_tonight_queues_it_for_the_night_window(platform, phone):
    poll, fake, _repo = phone
    _card(platform, poll, fake, "/code projA tonight: fix the broken contact form")
    assert fake.texts[-1].startswith(
        "Iron Jarvis: Queue coding work in projA (work/projA) for tonight (it starts between 11:00 PM and 7:00 AM)?")
    fake.updates = [text(next(_IDS), "1")]
    assert run(poll)[0]["status"] == "code_queued"
    assert fake.texts[-1].startswith("Iron Jarvis: → Queued for tonight in projA: it starts between 11:00 PM")
    [task] = tasks(platform)
    assert task["start_now"] is False and task["from_phone"] is True


def test_code_alone_says_how_and_names_the_folders(platform, phone):
    poll, fake, _repo = phone
    fake.updates = [text(next(_IDS), "/code")]
    assert run(poll)[0]["status"] == "code_usage"
    assert fake.texts[-1] == f"Iron Jarvis: {PC.USAGE} Folders you have used: projA."


def test_help_lists_code(platform, phone):
    poll, fake, _repo = phone
    from iron_jarvis.reflex.commands import _HELP

    assert "/code <folder>: <what to do>" in _HELP


def test_the_card_kinds_and_answers_are_one_set_of_words():
    from iron_jarvis.comm import prompts as P

    assert (PC.CODE_START_KIND, PC.CODE_FOLDER_KIND) == (P.CODE_START_KIND, P.CODE_FOLDER_KIND)
    assert PC.START_OPTIONS == P.CODE_START_OPTIONS == ["Start", "Not now"]
    assert P.pressed_label(P.CODE_START_KIND, "Start") == "Starting"
    assert P.pressed_label(P.CODE_START_KIND, "Not now") == "Not started"


def test_the_chats_card_follows_the_reply_with_its_buttons_and_no_still_waiting_line(platform, tmp_path):
    used_repo(platform, tmp_path / "work" / "projA")

    async def turn(platform_, personas, body, **kw):
        # What the chat's code_start does inside the phone's own turn: the
        # sender the door proved asks, and the card rides that sender.
        who = PC.asking()
        assert who is not None and who.sender_id == str(ME) and who.channel == "tg"
        card = await PC.from_words(platform_, who, "projA", "fix the broken contact form")
        who.cards.append(card)
        return {"reply": "It waits for your Start.", "provider": "mock", "model": "m", "tools_used": ["code_start"],
                "escalate": False, "escalate_reason": "", "documents": []}

    from tests._fakes.phone_back import FakeTelegramBack, telegram_back
    from tests._fakes.phone_files import poller

    fake = FakeTelegramBack([])
    poll = poller(platform, telegram_back(fake, chat_id=ME), turn=turn)
    poll.thread_store.resolve("tg", str(ME), "Val")
    fake.updates = [text(next(_IDS), "in the projA folder, fix the broken contact form")]
    res = run(poll)
    assert res[0]["status"] == "chat" and res[0]["cards"] == 1
    reply, card = fake.sent[-2], fake.sent[-1]
    assert reply["text"] == "Iron Jarvis: It waits for your Start."  # no "(Still waiting …)" first
    assert card["text"].startswith("Iron Jarvis: Start coding work in projA (work/projA) now?")
    assert [b["text"] for b in card["reply_markup"]["inline_keyboard"][0]] == ["Start", "Not now"]
    assert PC.asking() is None  # the door's sender never outlives its turn
    assert tasks(platform) == []


def test_two_cards_in_one_turn_send_only_the_one_still_open(platform, tmp_path):
    used_repo(platform, tmp_path / "work" / "projA")
    used_repo(platform, tmp_path / "work" / "projB")

    async def turn(platform_, personas, body, **kw):
        who = PC.asking()
        for folder in ("projA", "projB"):  # the second closes the first: one card to answer
            card = await PC.from_words(platform_, who, folder, "fix the broken contact form")
            who.cards.append(card)
        return {"reply": "It waits for your Start.", "provider": "mock", "model": "m", "tools_used": ["code_start"],
                "escalate": False, "escalate_reason": "", "documents": []}

    from tests._fakes.phone_back import FakeTelegramBack, telegram_back
    from tests._fakes.phone_files import poller

    fake = FakeTelegramBack([])
    poll = poller(platform, telegram_back(fake, chat_id=ME), turn=turn)
    tid = poll.thread_store.resolve("tg", str(ME), "Val").id
    fake.updates = [text(next(_IDS), "fix the contact form in projA, no, projB")]
    res = run(poll)
    assert res[0]["status"] == "chat" and res[0]["cards"] == 1
    cards = [str(s.get("text") or "") for s in fake.sent
             if str(s.get("text") or "").startswith("Iron Jarvis: Start coding work in")]
    assert len(cards) == 1 and cards[0].startswith("Iron Jarvis: Start coding work in projB"), cards
    on_pc = [m["content"] for m in poll.thread_store.history_body(tid)]
    assert not [c for c in on_pc if c.startswith("Start coding work in projA")], on_pc
    assert [p.status for p in prompts(platform, CODE_START_KIND)].count("open") == 1
