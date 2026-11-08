"""v1.455.0 REVIEW of readiness Wave G ("chat finds the right thing").

An independent reviewer ran fresh everyday sentences through the real
matchers and the two chat lanes. Each block below pins one defect the first
cut had, red on commit 97d4a804:

* item 21 — the person's-schedule guard swallowed the APP's schedules:
  "Pause my schedule today", "Turn off my schedule this week", "Delete my
  schedule for tomorrow" armed no schedule writer (they did before Wave G);
* item 20 — the person's own HABIT and a folder named only as the
  DESTINATION read as files arriving in a folder ("Any new receipts I get I
  save to my Desktop", "When the client's documents arrive, put them in the
  client folder") and armed ``sentinel_add`` and the settings writers;
* item 23 — "mail from the IRS" (a letter: the IRS writes by post) armed an
  email rule; "get Codex to review this PR", "I can't get Codex to work",
  "Download Claude Code's release notes" offered "Install Codex for me";
  the model was told about the install press when this PC already has the
  program (the door is left out) and on surfaces that draw no press; and the
  vendor door's readiness check could scan PATH on the event loop;
* item 22 — scoping ONE task ("You should only look at the Smith folder for
  this", "Limit you to the Johnson folder for this task") armed the
  readable-folders writers;
* item 23 — the coding door read "my photo repo", "my Dropbox repository",
  "my tax-docs repository" (folders of files) as code;
* item 26 — the team offer fired on a pasted email to answer, a "how should
  I" question, a "just so you know" note, a story about yesterday and a
  coding task with no repository named.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from iron_jarvis.daemon import page_doors as P
from iron_jarvis.settings import setup_words as W
from iron_jarvis.tools import autoselect as A


@pytest.fixture(scope="module")
def d(tmp_path_factory):
    from iron_jarvis.daemon.app import create_app

    app = create_app(str(tmp_path_factory.mktemp("review") / "home"))
    return SimpleNamespace(platform=app.state.platform, app=app)


def _offered(d, text: str) -> set[str]:
    from .test_chat_arming_table_v14340 import lanes

    armed, ask = lanes(d, text)
    return set(armed) | set(ask)


# --------------------------------------------------------------------------- #
# item 21 — the app's schedules keep their writers
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "text,tool",
    [
        ("Pause my schedule today.", "schedule_update"),
        ("Turn off my schedule this week.", "schedule_update"),
        ("Delete my schedule for tomorrow.", "schedule_delete"),
        ("Pause my daily brief schedule today.", "schedule_update"),
        ("Disable our schedule for tonight.", "schedule_update"),
    ],
)
def test_a_verb_that_acts_on_a_schedule_keeps_the_writers(d, text, tool):
    """MUTATION (red): drop ``records._APP_VERB_BEFORE`` -> these arm no
    schedule writer (the person's-schedule guard reads "today", "this week",
    "for tomorrow")."""
    assert tool in _offered(d, text), text


@pytest.mark.parametrize(
    "text",
    [
        "Clear my schedule for Friday afternoon.",
        "My schedule is packed this week, help me prioritize.",
        "What's on my schedule for tomorrow?",
        "Can you fit the Smith review into my schedule?",
    ],
)
def test_the_persons_own_schedule_still_arms_nothing(d, text):
    assert not ({"schedule_create", "schedule_update", "schedule_delete"} & _offered(d, text)), text


# --------------------------------------------------------------------------- #
# item 20 — a habit, a destination: not files arriving in a folder
# --------------------------------------------------------------------------- #
HABITS = [
    "Any new receipts I get I save to my Desktop.",
    "Whenever new tax documents come in, I file them in the Documents folder.",
    "When the client's documents arrive, put them in the client folder.",
    "Whenever a new PDF shows up in my Downloads folder I rename it by hand.",
    "When a new invoice lands in C:\\Scans I file it myself.",
    "Every new scan in my Scans folder, I check by hand.",
]
REQUESTS = [
    "Whenever a new PDF shows up in my Downloads folder, rename it by client name.",
    "Whenever a new PDF shows up in my Downloads folder, I want you to rename it.",
    "When a new invoice lands in C:\\Scans, file it under the client's folder.",
    "Every new scan in my Scans folder should be renamed by date.",
    "Every new PDF I save to my Desktop should be renamed.",
    "Whenever a file is created in D:\\Exports, email it to me.",
]


@pytest.mark.parametrize("text", HABITS)
def test_a_habit_or_a_destination_is_no_folder_arrival(d, text):
    """MUTATION (each red): put the comma back in the gap before the folder;
    drop ``_NOT_A_HABIT_AFTER``; drop ``_NO_FIRST_PERSON``; make the drive
    path's quantifier greedy again (``C:\\Sca`` + "ns I file")."""
    assert not W.files_arrive_in_folder(text), text
    assert "sentinel_add" not in _offered(d, text), text
    assert "watcher_suggests" not in {n.id for n in P.not_offered(text)}, text


@pytest.mark.parametrize("text", REQUESTS)
def test_a_standing_request_about_a_folder_still_is(d, text):
    """MUTATION (red): drop ``_A_REQUEST_FOLLOWS`` -> "Every new PDF I save
    to my Desktop should be renamed" loses it."""
    assert W.files_arrive_in_folder(text), text
    assert "sentinel_add" in _offered(d, text), text


# --------------------------------------------------------------------------- #
# item 23 — a letter is not an email
# --------------------------------------------------------------------------- #
def test_mail_from_the_irs_is_a_letter_not_an_email_rule(d):
    """MUTATION (red): put bare "mail" back in ``MAIL_FROM_RULE``."""
    text = "When we get mail from the IRS, file it in the cabinet."
    assert not W.mail_from_rule(text)
    assert "reflex_create" not in _offered(d, text)
    assert W.mail_from_rule("When we get an email from the IRS, file it in the audit folder.")


# --------------------------------------------------------------------------- #
# item 23 — the install door: only the program itself, only when it is needed
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "text",
    [
        "Can you get Codex to review this PR?",
        "I can't get Codex to work.",
        "How do I get Claude Code to stop asking me?",
        "Get Claude Code to summarize the diff.",
        "Download Claude Code's latest release notes.",
        "Get the Codex logs for me.",
        "Get Codex running again.",
        # Only the "no bare get" rule keeps these out (no guard word follows).
        "Get Codex out of here, it keeps asking me questions.",
        "Get Codex off my screen.",
    ],
)
def test_the_program_put_to_work_is_no_install(text):
    """MUTATION (each red): put a bare "get" back among the install verbs;
    drop the possessive / "to" / its-things guards."""
    assert {s.id for s in P.page_steps(text)}.isdisjoint({"install_codex", "install_claude_code"}), text


@pytest.mark.parametrize(
    "text,step",
    [
        ("Install Codex for me.", "install_codex"),
        ("get me codex", "install_codex"),
        ("Get Claude Code installed on this PC.", "install_claude_code"),
        ("Reinstall Codex, it's broken.", "install_codex"),
        ("Download Claude Code.", "install_claude_code"),
    ],
)
def test_installing_the_program_still_gets_the_door(text, step):
    assert P.page_step(text).id == step, text


def test_no_install_line_when_the_program_is_already_here():
    """MUTATION (red): ignore ``installed`` in ``prompt_block`` -> the model
    is told about an "Install Codex for me" press the reply does not carry."""
    text = "Install Codex for me."
    assert "Install Codex for me" not in P.prompt_block(text, installed=lambda b: True)
    assert "Install Codex for me" in P.prompt_block(text, installed=lambda b: None)
    assert "Install Codex for me" in P.prompt_block(text)


def test_the_install_line_is_true_on_a_surface_with_no_press():
    """The prompt line reaches the phone, the browser sidebar and a Build
    terminal's chat, which draw no door: Connections' press leads."""
    line = next(s.line for s in P.STEPS if s.id == "install_codex")
    assert f"press beside Codex in {P.CONNECTIONS_PLACE}" in line
    assert "on the chat page, also under this reply" in line


@pytest.mark.parametrize("lane", ["post", "stream"])
def test_both_lanes_leave_the_install_line_out_when_installed(d, lane, monkeypatch):
    """MIRROR NOTE (lock-step): both lanes hand the platform to
    ``page_steps_block``. MUTATION (red): drop it in either lane."""
    from iron_jarvis.daemon import chat_turn as CT
    from iron_jarvis.providers import manager as M

    seen: list[str] = []
    real = CT.page_steps_block

    def spy(body, platform=None):
        out = real(body, platform)
        seen.append(out)
        return out

    monkeypatch.setattr(CT, "page_steps_block", spy)
    monkeypatch.setattr(M, "known_cli_presence", lambda b: True)
    from fastapi.testclient import TestClient

    msg = {"messages": [{"role": "user", "content": "Install Codex for me."}]}
    with TestClient(d.app) as c:
        if lane == "post":
            assert c.post("/chat", json=msg).status_code == 200
        else:
            with c.stream("POST", "/chat/stream", json=msg) as r:
                assert r.status_code == 200
                "".join(r.iter_text())
    assert seen and all("Install Codex for me" not in s for s in seen), seen


def test_the_vendor_check_never_scans_path_on_the_loop(monkeypatch):
    """``provider_ready`` reads cached sign-in verdicts, but a CLI's
    availability first asks whether it is installed — a PATH scan for a
    binary never looked for (after Re-detect). MUTATION (red): drop the
    ``known_cli_presence`` gate in ``_page_door_checks``' ``ready``."""
    from iron_jarvis.daemon import chat_turn as CT
    from iron_jarvis.providers import manager as M

    scanned: list[str] = []
    monkeypatch.setattr(M, "_probe_cli_binary", lambda b: scanned.append(b) or True)
    M.invalidate_cli_presence()
    try:
        from iron_jarvis.daemon.app import create_app
        import tempfile

        app = create_app(tempfile.mkdtemp() + "/home")
        M.invalidate_cli_presence()
        ready, _installed = CT._page_door_checks(app.state.platform)
        for p in ("claude-cli", "codex-cli", "anthropic", "openai"):
            assert ready(p) is None, p
        assert scanned == []
    finally:
        M.invalidate_cli_presence()


# --------------------------------------------------------------------------- #
# item 22 — one task's folder is not the lasting setting
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "text",
    [
        "You should only look at the Smith folder for this.",
        "Only let you see my Clients folder for this chat.",
        "Only let Jarvis touch the Smith folder while doing this.",
        "Limit you to the Johnson folder for this task.",
        "Only let Jarvis look at C:\\Clients for now.",
    ],
)
def test_scoping_one_task_arms_no_settings_writer(d, text):
    """MUTATION (red): drop ``trigger._JUST_THIS_TASK`` from either shape."""
    assert not ({"config_set", "config_change", "config_change_protected"} & _offered(d, text)), text


@pytest.mark.parametrize(
    "text",
    [
        "Only let Jarvis look at C:\\Clients.",
        "Limit Jarvis to my Clients folder.",
        "Only let Jarvis read my Clients folder from now on.",
    ],
)
def test_the_lasting_setting_still_arms(d, text):
    assert "config_set" in _offered(d, text), text


# --------------------------------------------------------------------------- #
# item 23 — a folder of files is not a code repository
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "text,want",
    [
        ("Update the client list in my Dropbox repository.", False),
        ("Delete the duplicates in my photo repo.", False),
        ("Rename the files in my tax-docs repository by year.", False),
        ("Fix the README in my ClosePilot repo.", True),
        ("Add a README to our tax-tools repository.", True),
        ("Fix the login bug in our client-portal repo.", True),
    ],
)
def test_the_coding_door_reads_a_repository_of_code(text, want):
    """MUTATION (red): drop the storage-word guard in ``_REPO_WORDS``."""
    assert (A.coding_offer(text) is not None) is want, text


# --------------------------------------------------------------------------- #
# item 26 — the team offer: not for these
# --------------------------------------------------------------------------- #
NOT_OBJECTIVES = [
    # Someone else's words, pasted to be answered.
    (
        "Draft a reply to the email below:\n\nHi team, before Friday please do the following:\n"
        "1. Send the W-2s to the client\n2. Review the depreciation schedule\n3. Update the engagement letter\n"
        "4. Email me the final numbers\nThanks, Bob"
    ),
    (
        "Here's an email from the client, what do they want?\n\nHi, could you 1. send the 1099s, 2. review the "
        "payroll register, 3. update our address, and 4. check the Q3 numbers? Thanks so much, Mary from Acme"
    ),
    # A question about how to do the work.
    (
        "Can you explain how I should research the 1099-K rules, compare them with last year, draft a memo and "
        "then email it to the partners? I want to understand the steps before I start doing it myself next week."
    ),
    # Telling, not asking.
    (
        "Just so you know what my day looks like: research the new rules, review the Smith file, draft the "
        "Jones memo, email the partners, and then call the bank. No need to do anything at all, I am just "
        "telling you."
    ),
    (
        "Yesterday I had to research the new rules, review the Smith file, draft the Jones memo, email the "
        "partners and then call the bank before lunch on Friday. What a day it was, honestly, I am completely exhausted now."
    ),
    # A coding task with no repository named.
    (
        "Add a dark mode toggle to the settings page component, then write tests for it in settings.test.tsx, "
        "then update the README, and review the diff before I merge the pull request. This is for the release."
    ),
    (
        "Find the bug in the calculate_tax function, write a failing test that shows it, fix the function, and "
        "then update the docstring and the type hints so the CI passes again before the release next Tuesday."
    ),
]


@pytest.mark.parametrize("text", NOT_OBJECTIVES)
def test_these_are_not_a_teams_objective(text):
    """MUTATION (each red): drop ``_MISSION_PASTED``, ``_MISSION_HOW``,
    ``_MISSION_TELLING`` or ``_MISSION_CODE`` from ``mission_offer``."""
    assert len(text.strip()) >= A.MISSION_MIN_CHARS
    assert A.mission_offer(text) is None, text


def test_a_real_objective_still_gets_the_offer():
    from .test_chat_finds_the_right_thing_v14550 import LONG_ASKS

    for text in LONG_ASKS:
        assert A.mission_offer(text) is not None, text
    cpa = (
        "For the Lee family: gather last year's return, compare the income with this year's W-2s, list "
        "anything missing, and draft a short email to them asking for the missing documents by the end of "
        "next week please."
    )
    assert A.mission_offer(cpa) is not None


def test_a_task_on_a_named_repository_is_the_coding_offers_not_the_teams():
    """Review: with ``_MISSION_CODE`` the builder's coding row no longer
    needs ``coding_offer``; this one names a repository and no code word.
    MUTATION (red): drop the ``coding_offer`` guard in ``mission_offer``."""
    text = (
        "Update my ClosePilot repo for the new release: research the three auth libraries we looked at, compare "
        "their licences and their prices, draft a migration plan for the partners, and then outline the steps."
    )
    assert A.coding_offer(text) is not None and A._MISSION_CODE.search(text) is None
    assert A.mission_offer(text) is None


@pytest.mark.parametrize(
    "text", ["Install Claude Code for my wife.", "Install Codex on my brother's laptop.", "Install Codex for my team."],
)
def test_installing_for_someone_else_is_no_door(text):
    """MUTATION (red): drop the people from the install rule's device guard."""
    assert P.page_step(text) is None, text


def test_a_bulleted_list_of_orders_gets_the_offer():
    """Review: the builder's list rows are NUMBERED, and "1. Collect" also
    reads as a sentence ending before "Collect", so dropping the list reading
    (``_MISSION_LIST_PART``) stayed green. A bulleted list after a colon has
    no sentence end before its verbs. MUTATION (red): count no list parts."""
    text = (
        "Please take care of these for the Smith audit before Friday:\n"
        "- collect the bank statements from the shared folder for all twelve months\n"
        "- reconcile them against the general ledger export the client sent last week\n"
        "- draft the list of open questions for the client\n"
        "- summarize the findings for the partner review"
    )
    assert len(text) >= A.MISSION_MIN_CHARS
    assert A.mission_offer(text) is not None


# --------------------------------------------------------------------------- #
# item 26 — a SAVED Local only chat gets no team offer, whatever the body says
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("lane", ["post", "stream"])
def test_a_saved_local_only_chat_gets_no_team_offer(d, lane):
    """The body says local_only false; the saved chat says Local only (its
    scope is set around the whole turn). MUTATION (red): drop the
    ``current_scope().on`` check in ``with_mission_offer``."""
    from fastapi.testclient import TestClient

    from .test_chat_finds_the_right_thing_v14550 import LONG_ASKS

    with TestClient(d.app) as c:
        r = c.put(
            "/chat/threads/new",
            json={"messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
                  "setup": {"local_only": True}},
        )
        assert r.status_code == 200, r.text
        tid = r.json()["id"]
        body = {"messages": [{"role": "user", "content": LONG_ASKS[1]}], "thread_id": tid, "local_only": False}
        if lane == "post":
            r = c.post("/chat", json=body)
            assert r.status_code == 200, r.text
            doors = r.json().get("doors") or []
        else:
            with c.stream("POST", "/chat/stream", json=body) as r:
                assert r.status_code == 200
                raw = "".join(r.iter_text())
            doors = []
            for block in raw.split("\n\n"):
                if block.startswith("event: done"):
                    doors = json.loads(block.split("data: ", 1)[1]).get("doors") or []
        assert A.MISSION_OFFER_HREF not in [x.get("href") for x in doors]
        # Control: the same request in a chat that is not Local only.
        plain = {"messages": [{"role": "user", "content": LONG_ASKS[1]}]}
        if lane == "post":
            doors = c.post("/chat", json=plain).json().get("doors") or []
            assert A.MISSION_OFFER_HREF in [x.get("href") for x in doors]


# --------------------------------------------------------------------------- #
# LINEAR TIME — the review's new patterns on 128,000 characters of their words
# --------------------------------------------------------------------------- #
_LINEAR = r"""
import sys, time
from iron_jarvis.settings import setup_words as W, records as R, trigger as T
from iron_jarvis.daemon import page_doors as P
from iron_jarvis.tools import autoselect as A
pats = [W.FILES_ARRIVE_IN_FOLDER, W.MAIL_FROM_RULE, R._APP_VERB_BEFORE, A._MISSION_PASTED, A._MISSION_HOW,
        A._MISSION_TELLING, A._MISSION_CODE, A._REPO_TASK_RX, *T._EXTRA_REFERENCES,
        *P._install_rules(P._CLAUDE), *P._install_rules(P._CODEX)]
pieces = ["every new pdf i ", "any new receipts i get i save to my ", "c:\aaaaaaaaaaaa ", "in my downloads folder ",
          "only let jarvis look at the smith folder ", "for this ", "pause my ", "here is the ", "explain how ",
          "just so you know ", "a-b-c-d-", "x.y.z.", "my photo-tools ", "get codex ", "install claude code ",
          "download codex's ", "limit you to my a b ", "e-mail from ", " ", "-"]
n = int(sys.argv[1]); t0 = time.perf_counter(); worst = 0.0
for piece in pieces:
    text = (piece * (n // len(piece) + 1))[:n]
    for pat in pats:
        a = time.perf_counter(); pat.search(text); pat.search(text + "!"); worst = max(worst, time.perf_counter() - a)
    W.files_arrive_in_folder(text); A.mission_offer(text); A.coding_offer(text); P.page_steps(text)
    P.prompt_block(text, installed=lambda b: True)
print(worst, time.perf_counter() - t0)
"""


def test_the_reviews_patterns_are_linear():
    """In a CHILD process with a deadline; no single search may take a second
    on 128,000 characters (a quadratic one takes minutes)."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    run = subprocess.run(
        [sys.executable, "-c", _LINEAR, "128000"], capture_output=True, text=True, timeout=280, cwd=str(root),
        env={**os.environ, "PYTHONPATH": str(root / "src") + os.pathsep + os.environ.get("PYTHONPATH", "")},
    )
    assert run.returncode == 0, run.stderr[-2000:]
    worst, total = (float(x) for x in run.stdout.split())
    assert worst < 5.0 and total < 120.0, run.stdout
