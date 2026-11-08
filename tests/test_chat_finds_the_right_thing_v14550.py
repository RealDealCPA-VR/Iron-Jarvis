"""v1.455.0 (readiness Wave G, "chat finds the right thing", items 19-26 of
``docs/PRODUCT-READINESS-RESULTS.md`` 0j.5).

The re-audit at v1.452.0 (lane F-K, ``docs/PRODUCT-READINESS-LANES-v1452.md``)
ran first-time-user sentences through the real matchers. This file pins what
its fixes added beyond the shared tables (the arming rows live in
``tests/test_chat_arming_table_v14340.py``, the Guide and coding rows in
``tests/test_matcher_tables_v13610.py``, the install door rows in
``tests/test_page_doors_v14360.py``):

* item 19 — a plan used "with Jarvis" reaches the Connections door;
* item 20 — files arriving in a folder get the true watcher line, never a rule;
* item 23 — the email rule's step; the one-press install door (`install`,
  never offered for a program already on this PC); the `model_provider` door
  only for a vendor that is NOT connected; LM Studio and a Slack destination
  no longer read as an app;
* item 24 — `brain_change`'s "memory" ask is a safety card, never sent to the
  phone;
* item 26 — the team offer ("Give this to the team as an objective") for a
  long multi-part request, both ways, through both chat lanes;
* every new pattern is linear on 128,000 characters of its own words.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from iron_jarvis.connectors import app_names as AN
from iron_jarvis.core.approvals import SAFETY_REASONS
from iron_jarvis.daemon import page_doors as P
from iron_jarvis.settings import setup_words as W
from iron_jarvis.tools import autoselect as A

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "dashboard"


# --------------------------------------------------------------------------- #
# item 19 — the plan "with Jarvis" reaches the door
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "text",
    [
        "I want to use my Claude Pro subscription with Jarvis.",
        "I want to use my ChatGPT subscription with Jarvis.",
        "Can I use my ChatGPT Plus plan with you?",
        "I'd like to use my Claude Max subscription in this app.",
    ],
)
def test_a_plan_used_with_jarvis_gets_the_connections_door(text):
    """MUTATION (red): drop the "with Jarvis / with you / in this app" tail
    from ``ANSWERS_TAIL`` -> no door; drop "can I" from ``_REQUEST_AT`` -> the
    question loses it."""
    assert P.page_step(text).id == "model_provider", text


@pytest.mark.parametrize(
    "text",
    [
        "I want to use ChatGPT with you open in another window to compare.",
        "I used my Claude Pro subscription with Jarvis last week and it was fine.",
        "Compare my ChatGPT subscription with you.",
    ],
)
def test_a_plan_in_passing_gets_no_door(text):
    assert P.page_step(text) is None, text


# --------------------------------------------------------------------------- #
# item 20 — files arriving in a folder: the watcher line, never a rule
# --------------------------------------------------------------------------- #
ARRIVALS = [
    "Whenever a new PDF shows up in my Downloads folder, rename it by client name.",
    "When a new invoice lands in C:\\Scans, file it under the client's folder.",
    "Every time a PDF is saved to my Desktop, move it to Documents.",
    "Every new scan in my Scans folder should be renamed by date.",
]
NOT_ARRIVALS = [
    "Whenever a new email comes in, start an intake session.",
    "When a new invoice shows up in QuickBooks, email the client.",
    "When a file lands in my Google Drive folder, tell me.",
    "When the PDF arrives in my email, forward it to my phone.",
    "When the package arrives at my desk, put it in the garage.",
    "I save new PDFs in my Downloads folder every day.",
    "When a commit lands in my repo folder, run the tests.",
]


@pytest.mark.parametrize("text", ARRIVALS)
def test_files_arriving_in_a_folder_get_the_watcher_line(text):
    """MUTATION (red): take FILES_ARRIVE_IN_FOLDER out of ``watch_and_act`` ->
    no ``watcher_suggests`` line."""
    assert W.files_arrive_in_folder(text), text
    assert "watcher_suggests" in {n.id for n in P.not_offered(text)}, text


@pytest.mark.parametrize("text", NOT_ARRIVALS)
def test_arrivals_elsewhere_are_not_a_folder(text):
    assert not W.files_arrive_in_folder(text), text
    assert "watcher_suggests" not in {n.id for n in P.not_offered(text)}, text


def test_the_watcher_line_is_true_about_what_a_rule_cannot_hear():
    """The line says a watcher only suggests; a rule's sources really are no
    folder (``reflex/models``), which is why reflex_create is not offered."""
    line = next(n.line for n in P.NOT_OFFERED if n.id == "watcher_suggests")
    assert "sentinel_add" in line and "Proposals" in line
    from iron_jarvis.reflex.models import REFLEX_SOURCES

    assert not any(s in ("folder", "file", "files", "sentinel") for s in REFLEX_SOURCES)


# --------------------------------------------------------------------------- #
# item 23 — the email rule, the install door, the vendor door, Slack, LM Studio
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "text,want",
    [
        ("When I get an email from the IRS, forward it to my phone.", True),
        ("When I get an email from my bank, text me.", True),
        ("Whenever I receive an email from the IRS, forward it to my accountant.", True),
        ("If we get an email from the auditor, then save it to the audit folder.", True),
        ("When I get an email from the IRS, I panic.", False),
        ("When I get an email from my boss I usually reply fast.", False),
        ("I got an email from the IRS, forward it to my accountant.", False),
    ],
)
def test_mail_from_someone_with_an_order_is_a_rule(text, want):
    """MUTATION (red): drop the order after the comma -> "I panic" is a rule."""
    assert W.mail_from_rule(text) is want, text
    assert W.every_new_email(text) is want, text
    if want:
        assert "email_rule_inbox" in {s.id for s in P.page_steps(text)}


def _ready_none(_p):
    return None


def test_the_install_door_carries_the_one_install_press_and_says_so():
    door = P.with_page_door([], "Install Codex for me.")
    assert door == [{"href": "/connections", "label": "Install Codex for me", "install": "codex"}]
    door = P.with_page_door([], "Can you install Claude Code for me?")
    assert door[-1]["install"] == "claude"
    # The presses are the dashboard's own install jobs (lib/cliInstall), and
    # the daemon's launch catalog has each.
    src = (DASH / "lib" / "cliInstall.ts").read_text(encoding="utf-8")
    from iron_jarvis.terminals.ai_clis import INSTALL_JOBS

    for cli in P.INSTALL_PRESSES:
        assert f"{cli}: " in src and cli in INSTALL_JOBS, cli
    assert set(P.INSTALL_PRESSES) == set(INSTALL_JOBS)
    assert {s.install for s in P.STEPS if s.install} == set(P.INSTALL_PRESSES)
    # The line is true: nothing installs until the press.
    line = next(s.line for s in P.STEPS if s.id == "install_codex")
    assert "does not install anything until the user presses" in line


def test_an_installed_program_gets_no_install_door_and_falls_through():
    """MUTATION (red): ignore ``installed`` in ``step_not_needed`` -> the
    install door shows for a program already on this PC."""
    text = "Install Codex for me."
    out = P.with_page_door([], text, installed=lambda b: b == "codex")
    # Codex is installed: the next step the message is about (the Connections
    # door its vendor name reaches) takes the place.
    assert [d.get("install") for d in out] == [None]
    assert out[0]["label"] == "Open Connections to set up a model"
    # Not known (never looked for) keeps the door.
    assert P.with_page_door([], text, installed=lambda b: None)[0]["install"] == "codex"


def test_a_connected_vendor_gets_no_connections_door():
    """MUTATION (red): ignore ``ready`` in ``step_not_needed`` -> the door
    shows beside a vendor that already answers."""
    text = "I want to use my Claude Pro subscription with Jarvis."
    assert W.model_providers_named(text) == [("claude-cli", "anthropic")]
    assert P.with_page_door([], text, ready=lambda p: p == "claude-cli") == []
    assert P.with_page_door([], text, ready=lambda p: False)[0]["href"] == "/connections"
    assert P.with_page_door([], text, ready=_ready_none)[0]["href"] == "/connections"
    # Two vendors: the door stays while one of them is not connected.
    two = "Use Claude for this chat instead of the default, and set up Gemini."
    groups = W.model_providers_named(two)
    assert ("claude-cli", "anthropic") in groups and ("google",) in groups
    assert P.with_page_door([], two, ready=lambda p: p == "anthropic")[0]["href"] == "/connections"
    assert P.with_page_door([], two, ready=lambda p: p in ("anthropic", "google")) == []


def test_the_lanes_read_readiness_from_cached_answers_only(tmp_path, monkeypatch):
    """chat_turn's check reads ``model_team.provider_ready`` (cached sign-in
    verdicts, never a probe) and ``known_cli_presence`` (never a PATH scan)."""
    from iron_jarvis.daemon import chat_turn as CT
    from iron_jarvis.providers import manager as M

    scanned = []
    monkeypatch.setattr(M, "_probe_cli_binary", lambda b: scanned.append(b) or True)
    M.invalidate_cli_presence("codex")
    assert M.known_cli_presence("codex") is None
    ready, installed = CT._page_door_checks(object())
    assert installed("codex") is None and scanned == []
    assert CT._page_door_checks(None) == (None, None)


@pytest.mark.parametrize(
    "text,named",
    [
        ("Send me a Slack message every morning with a summary.", False),
        ("DM me on Slack when the export is done", False),
        ("Summarize what people posted in my Slack channels.", True),
        ("Read my Slack messages from today.", True),
    ],
)
def test_a_slack_message_to_me_is_a_destination_not_the_app(text, named):
    """MUTATION (red): drop the "send me a Slack message" place from
    ``_SLACK_PLACE`` -> the first row names the Slack app."""
    assert ("slack" in AN.apps_named(text)) is named, text


# --------------------------------------------------------------------------- #
# item 24 — "memory" is a safety card: never the phone
# --------------------------------------------------------------------------- #
def test_brain_change_asks_with_a_safety_reason():
    """MUTATION (red): take "memory" out of SAFETY_REASONS."""
    assert "memory" in SAFETY_REASONS
    src = (ROOT / "src" / "iron_jarvis" / "brain" / "tools.py").read_text(encoding="utf-8")
    assert 'reason="memory"' in src
    # The dashboard's once-reason list stays a superset (lib/onceReason).
    once = (DASH / "lib" / "onceReason.ts").read_text(encoding="utf-8")
    for r in SAFETY_REASONS:
        assert f'"{r}"' in once, r


def _event(**payload):
    base = {"approval_id": "apr_m", "tool": "brain_change", "args": {}, "timeout_s": 0}
    base.update(payload)
    return {"type": "approval.requested", "payload": base}


def test_a_memory_card_never_reaches_the_phone(tmp_path):
    from iron_jarvis.comm import MockChannel, Notifier
    from iron_jarvis.comm.prompts import PendingPromptStore, handle_approval_requested
    from iron_jarvis.comm.threads import CommThreadStore
    from iron_jarvis.daemon.app import create_app

    from .test_comm_approvals_v1200 import CHAT_CFG, ChatMockChannel

    mock = MockChannel()
    notifier = Notifier()
    notifier.add_channel("mock", mock)
    assert notifier.on_event(_event(once_only=True, once_reason="memory")) == {}
    assert mock.sent == []

    app = create_app(str(tmp_path))
    platform = app.state.platform
    ch = ChatMockChannel(CHAT_CFG)
    n2 = Notifier()
    n2.add_channel("tg", ch)
    tstore = CommThreadStore(platform.engine)
    pstore = PendingPromptStore(platform.engine)
    tstore.resolve("tg", "777", "Val")
    assert handle_approval_requested(
        _event(once_only=True, once_reason="memory"), store=pstore, notifier=n2, thread_store=tstore
    ) == []
    assert pstore.newest_open("tg", "777") is None
    # The control: the same ask with no reason registers.
    out = handle_approval_requested(_event(), store=pstore, notifier=n2, thread_store=tstore)
    assert len(out) == 1


# --------------------------------------------------------------------------- #
# item 26 — the team offer, both ways
# --------------------------------------------------------------------------- #
LONG_ASKS = [
    (
        "For the Johnson engagement: research the new 1099-K thresholds, compare them with last year's, "
        "draft a two-page memo for the partner, and then prepare a checklist the staff can follow when "
        "they gather the client's payment platform statements."
    ),
    (
        "Please do the following for the Smith audit:\n"
        "1. Collect the bank statements from the shared folder\n"
        "2. Reconcile them against the ledger export\n"
        "3. Draft a list of the open questions for the client\n"
        "4. Summarize the findings for the partner review on Friday"
    ),
    (
        "Research three practice-management tools for a ten-person CPA firm. Compare their prices and "
        "security features in a table. Then write a one-page recommendation for the partners, and "
        "finally draft the email I will send them with it."
    ),
]
NOT_LONG_ASKS = [
    # Each row has three or more verb-led parts, so only its own guard keeps
    # the offer out (each guard's mutation turns its row red).
    # The length floor: three parts, but one quick task.
    "Read it, summarize it, then draft a reply.",
    # The question guard: a real question, not a request.
    (
        "Should I first research the three tools, then compare their prices, then draft a memo myself, or is "
        "it better to have each vendor send its own comparison first, given how much the firm cares about "
        "security and how little time the partners have this month?"
    ),
    # The coding guard: a coding task has its own offer.
    (
        "Fix the failing test in my ClosePilot repo, then update the README, then write a changelog entry, "
        "and finally review the whole diff before I push it to the team for the release tomorrow morning at "
        "nine."
    ),
    # The pasted-payload guard: the orders are in the email being rewritten.
    (
        "Rewrite this email so it sounds friendlier and less abrupt: Hi Bob, please review the attached "
        "return before Friday. Next, send the engagement letter to the client. Finally, update the client "
        "file with the new address and call me back when you can."
    ),
    # No verb-led parts at all: a pasted note.
    (
        "The client's notes say revenue rose in Q3, margins fell because of freight costs, the new warehouse "
        "opened in August, and the owner plans to sell the old truck. Turn this into one clear paragraph."
    ),
]


@pytest.mark.parametrize("text", LONG_ASKS)
def test_a_long_multi_part_request_gets_the_team_offer(text):
    """MUTATION (red): raise MISSION_MIN_PARTS past 4 -> the rows lose it;
    drop the clause reading -> the prose rows lose it."""
    door = A.mission_offer(text)
    assert door == {"href": A.MISSION_OFFER_HREF, "label": "Give this to the team as an objective"}, text


def test_a_polite_request_shaped_as_a_question_still_gets_the_offer():
    text = (
        "Can you research the three practice-management tools, compare their prices and security, then "
        "draft a one-page memo for the partners and email it to me when it is ready for review by Friday "
        "morning at the latest?"
    )
    assert A.mission_offer(text) is not None


def test_a_local_only_body_gets_no_team_offer_even_outside_its_scope():
    """The body's own Local only (a chat whose scope is not set in this
    context) — MUTATION (red): drop the body check in ``with_mission_offer``."""
    from types import SimpleNamespace

    from iron_jarvis.daemon import chat_turn as CT

    body = SimpleNamespace(messages=[{"role": "user", "content": LONG_ASKS[1]}], local_only=False, pane_id="")
    assert [d["href"] for d in CT.with_mission_offer([], body)] == [A.MISSION_OFFER_HREF]
    body.local_only = True
    assert CT.with_mission_offer([], body) == []
    # A coding session, an escalation, the sidebar: never.
    body.local_only = False
    assert CT.with_mission_offer([], body, harness=object()) == []
    assert CT.with_mission_offer([], body, escalate=True) == []
    assert CT.with_mission_offer([], body, sidebar=True) == []


@pytest.mark.parametrize("text", NOT_LONG_ASKS)
def test_a_short_or_single_request_gets_no_team_offer(text):
    """MUTATION (red): drop the length floor, the question guard, the coding
    guard or the pasted-payload guard -> a row here gets the offer."""
    assert A.mission_offer(text) is None, text


def test_the_offer_arms_nothing_and_its_address_is_the_dashboards():
    assert A.select_auto_tools(LONG_ASKS[0], cap=99) == A.select_auto_tools(
        LONG_ASKS[0].replace("and then prepare", "and prepare"), cap=99
    )
    src = (DASH / "lib" / "missionStart.ts").read_text(encoding="utf-8")
    assert f'MISSION_OFFER_HREF = "{A.MISSION_OFFER_HREF}"' in src
    assert A.MISSION_OFFER_LABEL == "Give this to the team as an objective"


@pytest.fixture()
def app(tmp_path):
    from iron_jarvis.daemon.app import create_app

    return create_app(str(tmp_path / "home"))


def _doors_post(app, text, **body):
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        r = c.post("/chat", json={"messages": [{"role": "user", "content": text}], **body})
        assert r.status_code == 200, r.text
        return r.json().get("doors") or []


def _doors_stream(app, text, **body):
    import json as _json

    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        with c.stream("POST", "/chat/stream", json={"messages": [{"role": "user", "content": text}], **body}) as r:
            assert r.status_code == 200
            raw = "".join(r.iter_text())
    for block in raw.split("\n\n"):
        if block.startswith("event: done"):
            data = block.split("data: ", 1)[1]
            return _json.loads(data).get("doors") or []
    raise AssertionError("no done frame")


@pytest.mark.parametrize("lane", [_doors_post, _doors_stream])
def test_both_lanes_carry_the_team_offer(app, lane):
    """MIRROR NOTE (lock-step). MUTATION (red): take ``with_mission_offer``
    out of either lane."""
    hrefs = [d.get("href") for d in lane(app, LONG_ASKS[1])]
    assert A.MISSION_OFFER_HREF in hrefs
    assert A.MISSION_OFFER_HREF not in [d.get("href") for d in lane(app, NOT_LONG_ASKS[0])]
    # Never in a Local only chat (an objective runs on the default model).
    assert A.MISSION_OFFER_HREF not in [d.get("href") for d in lane(app, LONG_ASKS[1], local_only=True)]


@pytest.mark.parametrize("lane", [_doors_post, _doors_stream])
def test_both_lanes_carry_the_install_door(app, lane, monkeypatch):
    from iron_jarvis.providers import manager as M

    monkeypatch.setattr(M, "known_cli_presence", lambda b: False)
    doors = lane(app, "Install Codex for me.")
    assert {"href": "/connections", "label": "Install Codex for me", "install": "codex"} in doors
    monkeypatch.setattr(M, "known_cli_presence", lambda b: True)
    assert all(d.get("install") is None for d in lane(app, "Install Codex for me."))


# --------------------------------------------------------------------------- #
# the Guide's corpus has the words its new rows ask about
# --------------------------------------------------------------------------- #
def test_the_guide_finds_the_renamed_pages():
    from iron_jarvis.guide.corpus import GuideIndex

    index = GuideIndex()
    for query, word in (
        ("What are Watchers for?", "Watchers"),
        ("What is the Rules page?", "Rules"),
        ("What is the Model servers page?", "Model servers"),
        ("What is What agents made?", "What agents made"),
    ):
        hits = index.search(query, k=8)
        assert any(word.lower() in (sec.text + sec.heading).lower() for _score, sec in hits), query


# --------------------------------------------------------------------------- #
# LINEAR TIME — 128,000 characters of each new pattern's own words
# --------------------------------------------------------------------------- #
_LINEAR = r"""
import sys, time
from iron_jarvis.settings import setup_words as W
from iron_jarvis.settings import records as R
from iron_jarvis.settings import trigger as T
from iron_jarvis.connectors import app_names as AN
from iron_jarvis.daemon import page_doors as P
from iron_jarvis.tools import autoselect as A
pieces = [
    "whenever a new pdf shows up in my ", "every new scan in my ", "downloads folder ", "c:\\scans ",
    "when i get an email from the irs ", ", forward ", "my schedule ", "schedule time ", "my busy ",
    "only let jarvis look at ", "limit jarvis to ", "my clients folder ", "use my claude pro subscription ",
    "with jarvis ", "can i use ", "install codex ", "install claude code ", "fix the failing test in my ",
    "closepilot repo ", "what are watchers ", "how do i make a rule ", "send me a slack message ",
    "1. research the ", "then draft ", "research ", ", ", "a ", " ", "x-", "'s ", "\n- check ",
]
n = int(sys.argv[1])
t0 = time.perf_counter()
pats = list(W.ALL_PATTERNS) + [AN._SLACK_PLACE, A._GUIDE_RX, A._FEATURE_HOWTO_RX, A._REPO_TASK_RX,
        A._MISSION_LIST_PART, A._MISSION_CLAUSE_PART, A._MISSION_PAYLOAD, R._PERSONS_BEFORE,
        R._PERSONS_PREP_BEFORE, R._PERSONS_AFTER, R._SCHEDULE_NOUN] + list(T._EXTRA_REFERENCES) + list(P.ALL_PATTERNS)
for piece in pieces:
    text = (piece * (n // len(piece) + 1))[:n]
    for pat in pats:
        pat.search(text); pat.search(text + "!")
    W.files_arrive_in_folder(text); W.without_folder_arrivals(text); W.mail_from_rule(text)
    W.model_providers_named(text[:4000]); W.watch_and_act(text); W.every_new_email(text)
    R._schedule_noun_hit(text[:4000]); R._record_tools_named(text[:4000])
    A.mission_offer(text); A.coding_offer(text)
    P.page_steps(text); P.not_offered(text)
print(time.perf_counter() - t0)
"""


def test_every_new_pattern_is_linear_on_pathological_input():
    """128,000 characters of each new pattern's own words with no sentence
    end: a quadratic pattern takes minutes. In a CHILD process, so a hang
    fails by name instead of hanging the suite."""
    run = subprocess.run(
        [sys.executable, "-c", _LINEAR, "128000"],
        capture_output=True, text=True, timeout=280, cwd=str(ROOT),
        env={**os.environ, "PYTHONPATH": str(ROOT / "src") + os.pathsep + os.environ.get("PYTHONPATH", "")},
    )
    assert run.returncode == 0, run.stderr[-2000:]
    assert float(run.stdout.strip()) < 150.0, run.stdout
