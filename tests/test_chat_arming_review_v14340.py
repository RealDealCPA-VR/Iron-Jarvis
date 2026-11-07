"""v1.434.0 review of chat-first Wave 2 group E ("chat finds the right tool").

Found by an independent review, each pinned red before its fix:

* LINEAR TIME. The new record matchers put two whitespace runs side by side
  (``schedule\\s+(?:a|…)?\\s*`` and ``run\\s+(?:the|…)?\\s*``): "schedule" or
  "kick off" followed by a pasted run of blank lines was QUADRATIC — 5 s at
  12,000 characters — and the stream lane ran them ON THE EVENT LOOP. The
  saved-workflow names were one compiled regex PER NAME over the whole
  message (1.9 s for 500 workflows and a 100 KB paste). The cue finder
  re-tokenised the message once per cue. The pre-existing ``research`` rule of
  ``connectors.app_names._WEB_SEARCH_RX`` was quadratic on newlines too.
* The stream lane's settings/record matching now runs OFF the loop.
* A two-word OPTION label ("Don't ask", "Ask first", "Never run", "Stop and
  tell me", "Claude Code", "Gold & Red") is two everyday words: alone it armed
  the settings writers for "don't ask me what I want for dinner" — and
  ``config_set`` can lower the approval posture with no card. It now counts
  beside a word naming its row, as a switch's target, or with "by default".
  The new approval aliases "stop asking" / "without asking" were narrowed.
* Record nouns in code ("a webhook handler", "a watcher in my React code",
  "a goal-setting worksheet") and calendar EVENTS on a recurring time ("set up
  a meeting every Tuesday") no longer arm record writers.

And the safety seams the review checked, driven through the REAL lanes and the
real ``registry.invoke``: a create tool cards in every posture but "Don't
ask"; a user's deny is never lifted; the browser sidebar's ceiling keeps the
settings tools out; plan mode reads only.
"""

from __future__ import annotations

import random
import re
import subprocess
import sys
import textwrap
import threading
from types import SimpleNamespace

import pytest

from iron_jarvis.connectors import app_names as A
from iron_jarvis.daemon import chat_turn as CT
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.schemas import ChatBody
from iron_jarvis.settings import records as R
from iron_jarvis.settings import trigger as T

# --------------------------------------------------------------- linear time


def test_the_record_and_settings_matchers_are_linear_on_pathological_text():
    """A child process with a deadline (a hang detector, never a speed bar):
    200,000 characters after each trigger word. The quadratic shapes took
    minutes here; the linear ones take well under a second."""
    code = textwrap.dedent(
        """
        import time
        from iron_jarvis.settings import records as R, trigger as T
        from iron_jarvis.connectors import app_names as A
        from iron_jarvis.tools import autoselect as AS
        N = 200_000
        R.record_tools_for("warm up")
        cases = [
            ("schedule", lambda: R.record_tools_for("schedule" + " " * N + "x")),
            ("schedule nl", lambda: R.record_tools_for("schedule" + "\\n" * N + "x")),
            ("kick off", lambda: R.record_tools_for("kick off" + " " * N + "x")),
            ("run nl", lambda: R.record_tools_for("run" + "\\n" * N + "x")),
            ("research", lambda: A._WEB_SEARCH_RX.search("slack" + "\\n" * N + "x")),
            ("names", lambda: R.record_tools_for("run the " * (N // 8),
                workflow_names=[f"flow number {i}" for i in range(500)])),
            ("settings", lambda: T._cue_positions("use " * (N // 4), [])),
            # v1.448.0 (Wave A, A1): the before-word read of a schedule noun,
            # the vendor shapes, the "can you work with X" feature question
            # and the desktop reading in the ask tier.
            ("schedules", lambda: R.record_tools_for("depreciation schedules " * (N // 23))),
            ("vendor", lambda: T.settings_ask("use claude " + "x " * (N // 2))),
            ("vendor2", lambda: T.settings_ask("use " * (N // 4) + "claude for this chat")),
            ("guide", lambda: AS._guide_fires("can you work with " + "X " * (N // 2))),
            ("desktop", lambda: AS.select_ask_tools("click " + "x " * (N // 2) + "in the quickbooks desktop app")),
        ]
        for name, fn in cases:
            t0 = time.perf_counter(); fn()
            print(name, round(time.perf_counter() - t0, 3), flush=True)
        """
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60
    )
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.count("\n") == 12, out.stdout


def test_the_cue_finder_reads_the_message_once():
    """``_cue_positions`` tokenises ONCE (it used to count the tokens of the
    prefix before every cue — quadratic in the number of cues)."""
    calls = {"n": 0}
    real = T._TOKEN

    class Counting:
        def __getattr__(self, name):
            attr = getattr(real, name)
            if name in ("finditer", "findall"):
                def wrapped(*a, **k):
                    calls["n"] += 1
                    return attr(*a, **k)
                return wrapped
            return attr

    T._TOKEN = Counting()
    try:
        T._cue_positions("use " * 200 + "turn on the theme please", [])
    finally:
        T._TOKEN = real
    assert calls["n"] == 1


def _old_cue_positions(text: str) -> set[int]:
    out: set[int] = set()
    low = (text or "").lower().replace("’", "'")
    for m in T._CUE.finditer(low):
        out.add(max(0, len(T._TOKEN.findall(low[: m.end()])) - 1))
    return out


def test_the_cue_finder_gives_the_old_answers():
    words = ["turn", "on", "off", "use", "set", "don't", "x.y", "on.off", "a'b", "please", "i",
             "want", "the", "theme", "by", "default", "from", "now", "can", "you", "’",
             "İ", ".", "'", "go", "back", "any", "more", "keep"]
    rnd = random.Random(7)
    for _ in range(4000):
        s = "".join(rnd.choice(words) + rnd.choice([" ", "", "  ", ".", "'"]) for _ in range(rnd.randint(1, 14)))
        assert T._cue_positions(s, []) == _old_cue_positions(s), s


def _old_runs_a_named_workflow(t: str, names) -> bool:
    for name in names or ():
        n = (name or "").strip()
        if len(n) < 3:
            continue
        rx = re.compile(r"\b" + R._RUN_VERB + r"\s+(?:the\s+|my\s+|our\s+)?" + re.escape(n) + r"(?![\w-])",
                        re.IGNORECASE)
        if rx.search(t):
            return True
    return False


def test_a_saved_workflows_name_is_found_exactly_as_the_per_name_patterns_found_it():
    names_pool = ["triage", "Triage", "month-end close", "the triage", "tri", "ab", "triage-x",
                  "Month End", "c++ build", "a.b", "  spaced  "]
    toks = ["run", "start", "kick off", "kick  off", "re-run", "rerun", "the", "my", "our", " ", "\n",
            "-", "triage", "Triage", "month-end", "close", "Month", "End", "c++", "build", "a.b", "x",
            ".", "TRIAGE", "triages", "spaced", "fire off", "execute"]
    rnd = random.Random(3)
    hits = 0
    for _ in range(8000):
        names = rnd.sample(names_pool, rnd.randint(0, 5))
        t = "".join(rnd.choice(toks) + rnd.choice(["", " ", "  "]) for _ in range(rnd.randint(1, 9)))
        want = _old_runs_a_named_workflow(t, names)
        hits += want
        assert R._runs_a_named_workflow(t, names) is want, (t, names)
    assert hits > 100  # anti-vacuity: many really match


def test_a_saved_workflows_name_costs_no_regex_per_name(monkeypatch):
    """The names are LOOKED UP, never compiled one by one per message (the
    first cut compiled and ran a pattern per saved workflow)."""
    names = [f"flow number {i}" for i in range(60)]
    R._runs_a_named_workflow("warm up", names)
    compiled = {"n": 0}
    real = re.compile

    def counting(*a, **k):
        compiled["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(re, "compile", counting)
    assert R._runs_a_named_workflow("please run flow number 7 now", names) is True
    assert R._runs_a_named_workflow("please run flow number 77 now", names) is False
    assert compiled["n"] == 0


OLD_WEB_SEARCH = re.compile(
    r"\bgoogle\b|\bonline\b|\binternet\b|\bweb\s+search\b"
    r"|\bthe\s+web\b(?!\s*(?:pages?|sites?|apps?|address(?:es)?|links?)\b)"
    r"|(?:\A|[.!?\n]\s*)research\b",
    re.IGNORECASE,
)


def test_the_web_search_rule_matches_what_it_matched():
    pieces = ["research", "Research", ".", "!", "?", "\n", " ", "\t", "\r", "x", "google", "the web",
              "pages", "online", "re", "search", "web search"]
    rnd = random.Random(11)
    hits = 0
    for _ in range(8000):
        s = "".join(rnd.choice(pieces) for _ in range(rnd.randint(0, 10)))
        want = OLD_WEB_SEARCH.search(s) is not None
        hits += want
        assert (A._WEB_SEARCH_RX.search(s) is not None) is want, repr(s)
    assert hits > 500


# ------------------------------------------------------------- the stream lane


async def test_the_stream_lane_matches_settings_and_records_off_the_event_loop(tmp_path, monkeypatch):
    from tests.test_chat_approvals_v1187 import _drive_stream
    from tests.test_settings_chat_tools_v1321 import _body, _calls_stream

    app = create_app(str(tmp_path / "home"))
    app.state.platform.router.stream = _calls_stream([])
    loop_thread = threading.get_ident()
    seen: list[int] = []
    real = CT.settings_ask_tools

    def spy(*a, **k):
        seen.append(threading.get_ident())
        return real(*a, **k)

    monkeypatch.setattr(CT, "settings_ask_tools", spy)

    async def never(data):  # pragma: no cover
        raise AssertionError(data)

    await _drive_stream(app, _body("run the triage workflow now and turn on read aloud"), never)
    assert seen and all(t != loop_thread for t in seen)


ARGS = {"name": "weekday-digest", "cron": "0 8 * * 1-5", "kind": "event", "payload": {"type": "x"}}


@pytest.mark.parametrize("mode", ["always_ask", "approve_for_me", "accept_edits", "auto"])
async def test_a_create_tool_asks_first_in_every_posture_but_dont_ask(tmp_path, mode):
    """Through the REAL stream lane and the REAL registry: the card comes, and
    an "Allow once" is what lets the create run (the grant reaches invoke)."""
    from tests.test_chat_approvals_v1187 import _asgi_post, _drive_stream
    from tests.test_settings_chat_tools_v1321 import _body, _calls_stream

    app = create_app(str(tmp_path / "home"))
    app.state.platform.router.stream = _calls_stream([("schedule_create", dict(ARGS))])
    cards = []

    async def once(data):
        cards.append(data["tool"])
        assert app.state.platform.scheduler.get("weekday-digest") is None, "ran before the answer"
        status, _ = await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "once"})
        assert status == 200

    await _drive_stream(app, _body("make a schedule that runs every weekday at 8am", mode), once)
    assert cards == ["schedule_create"], mode
    assert app.state.platform.scheduler.get("weekday-digest") is not None


async def test_a_users_deny_on_a_create_tool_is_never_lifted_to_ask(tmp_path):
    from tests.test_chat_approvals_v1187 import _drive_stream
    from tests.test_settings_chat_tools_v1321 import _body, _calls_stream

    app = create_app(str(tmp_path / "home"))
    app.state.platform.permissions._base["schedule_create"] = "deny"
    app.state.platform.router.stream = _calls_stream([("schedule_create", dict(ARGS))])

    async def never(data):  # pragma: no cover
        raise AssertionError(f"a denied tool carded: {data}")

    frames = await _drive_stream(app, _body("make a schedule that runs every weekday at 8am", "yolo"), never)
    assert app.state.platform.scheduler.get("weekday-digest") is None
    done = [d for e, d in frames if e == "tool_call" and d.get("status") == "finished"]
    assert done and done[0]["ok"] is False


def test_a_users_deny_on_a_settings_tool_keeps_it_out_of_the_armed_set(tmp_path):
    """Arming is granting: an armed tool gets an "allow" override, which lifts
    a base deny for any tool off the deny floor. The settings tools now arm in
    their own place, outside the cap — never over the user's own deny (the
    coding-session plan already skips a denied tool)."""
    app = create_app(str(tmp_path / "home"))
    app.state.platform.permissions._base["config_set"] = "deny"
    body = ChatBody(messages=[{"role": "user", "content": "turn on follow-up suggestions"}], auto_tools=True)
    armed = set(CT._resolve_armed_tools(SimpleNamespace(platform=app.state.platform), body)[0])
    assert "config_set" not in armed
    assert {"config_list", "config_secret"} <= armed


async def test_a_users_deny_on_config_set_holds_through_the_real_lane(tmp_path):
    from tests.test_chat_approvals_v1187 import _drive_stream
    from tests.test_settings_chat_tools_v1321 import _body, _calls_stream

    app = create_app(str(tmp_path / "home"))
    app.state.platform.permissions._base["config_set"] = "deny"
    assert app.state.platform.config.chat_followups is False
    app.state.platform.router.stream = _calls_stream([("config_set", {"key": "chat_followups", "value": True})])

    async def never(data):  # pragma: no cover
        raise AssertionError(data)

    await _drive_stream(app, _body("turn on follow-up suggestions"), never)
    assert app.state.platform.config.chat_followups is False


@pytest.mark.parametrize(
    ("tool", "text"),
    [
        ("web_search", "search the web for the 2026 standard mileage rate"),
        ("write_document", "write a memo about the audit and save it as a Word document"),
    ],
)
def test_no_sentence_arms_a_tool_the_user_denied(tmp_path, tool, text):
    """The same hole beside every auto pass (found by this review, older than
    group E): the sentence armed a tool the user had set to "Never run", and
    the lanes' allow override for an armed tool lifted the deny. An explicit
    deny wins (``tools.permissions`` says so for the web tools)."""
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    body = ChatBody(messages=[{"role": "user", "content": text}], auto_tools=True)
    assert tool in CT._resolve_armed_tools(SimpleNamespace(platform=p), body)[0]  # control
    p.permissions._base[tool] = "deny"
    assert tool not in CT._resolve_armed_tools(SimpleNamespace(platform=p), body)[0]


async def test_a_denied_tool_the_sentence_names_never_runs_in_the_real_lane(tmp_path):
    from tests.test_chat_approvals_v1187 import _drive_stream
    from tests.test_settings_chat_tools_v1321 import _body, _calls_stream

    app = create_app(str(tmp_path / "home"))
    app.state.platform.permissions._base["write_document"] = "deny"
    app.state.platform.router.stream = _calls_stream(
        [("write_document", {"path": "memo.docx", "content": "The audit memo."})]
    )

    async def never(data):  # pragma: no cover
        raise AssertionError(data)

    frames = await _drive_stream(
        app, _body("write a memo about the audit and save it as a Word document"), never
    )
    assert not list((tmp_path / "home").rglob("memo.docx"))
    done = [d for e, d in frames if e == "tool_call" and d.get("status") == "finished"]
    assert done and done[0]["ok"] is False


async def test_the_browser_sidebars_ceiling_keeps_the_settings_tools_out(tmp_path, monkeypatch):
    """The settings tools sit outside the six-tool cap — never outside a
    ceiling the caller set on purpose (the sidebar's browser family)."""
    from iron_jarvis.browser.panel import browser_tool_ceiling
    from iron_jarvis.daemon.routes.chat import stream_chat_turn
    from iron_jarvis.personas.builtins import BUILTIN_PERSONAS
    from iron_jarvis.platform import build_platform
    from tests.test_build_sessions_v1367 import Script

    platform = build_platform(str(tmp_path / "home"))
    script = Script(["ok."])
    monkeypatch.setattr(platform.router, "stream", script.stream)
    monkeypatch.setattr(platform.router, "complete", script.complete)
    ceiling = browser_tool_ceiling(platform)
    body = ChatBody(messages=[{"role": "user", "content": "turn on read aloud and make a schedule "
                                                           "that runs every weekday at 8am"}],
                    auto_tools=True)
    gen = await stream_chat_turn(platform, BUILTIN_PERSONAS, body, tool_ceiling=ceiling, arm_family=ceiling)
    async for _ in gen:
        pass
    tools = set(script.calls[0]["tools"])
    assert not {t for t in tools if t.startswith("config_") or t.startswith("schedule_")}, tools
    assert tools <= set(ceiling) | {"escalate_to_agent", "workflow_draft"}, tools


async def test_plan_mode_coding_session_reads_settings_and_changes_none(tmp_path):
    """Through ``POST /build/sessions/{id}/turn``: plan mode arms only the
    reader for a settings request."""
    from tests.test_build_sessions_v1367 import Script, new_session, wire
    from tests.test_build_turns_v1367 import _run

    app = create_app(str(tmp_path / "home"))
    work = tmp_path / "work"
    work.mkdir()
    sid = new_session(app, work, mode="plan")["id"]
    script = wire(app, Script(["ok."]))
    await _run(app, sid, "turn on read aloud", "bt-plan-settings")
    tools = set(script.calls[0]["tools"])
    assert "config_list" in tools
    assert not tools & {"config_set", "config_secret", "config_change", "config_change_protected"}


# ------------------------------------------------------------- the words


@pytest.fixture(scope="module")
def d(tmp_path_factory):
    app = create_app(str(tmp_path_factory.mktemp("review") / "home"))
    return SimpleNamespace(platform=app.state.platform)


SETTINGS_WRITERS = {"config_set", "config_change", "config_change_protected", "config_secret"}


@pytest.mark.parametrize(
    "text",
    [
        "don't ask me what I want for dinner, just pick something",
        "don't ask how I know",
        "I'll ask my boss first and get back to you",
        "please stop and tell me what you think",
        "use Claude Code to refactor my repo",
        "never run this script on a Friday",
        "make the logo gold and red",
        "stop asking clarifying questions and just write it",
    ],
)
def test_an_everyday_sentence_never_arms_config_set(d, text):
    """``config_set`` is GRANTED in both lanes; one of its settings is the
    approval posture (Accept edits is allow tier). An everyday sentence must
    not hand it to the model."""
    body = ChatBody(messages=[{"role": "user", "content": text}], auto_tools=True)
    armed = set(CT._resolve_armed_tools(d, body)[0])
    assert not armed & SETTINGS_WRITERS, (text, armed)
    assert T.settings_ask(text).writers is False


@pytest.mark.parametrize(
    "text",
    [
        "set approvals to don't ask",
        "switch to Arc Cyan",
        "go back to Liquid Glass",
        "don't ask me for approval anymore",
        "stop asking me for permission every time",
        "Stop asking me before you run tests.",
        "Approve tests without asking.",
        "use OpenAI Images for pictures by default",
        "make the app dark",
        "stop reading your replies out loud",
    ],
)
def test_a_real_request_still_arms_the_writers(d, text):
    assert T.settings_ask(text).writers is True, text
