"""Continue on the next account (v1.303.0), driven through the REAL app factory
+ the real terminals manager, a recording fake PTY backend and the fake
Iron-Proxy (``tests/fixtures/fake_iron_proxy_v1301.py``).

Pinned here:

* the pane's limit state: the REAL account-limit shapes this PC recorded (and
  Claude Code's own prefixes) are detected in a CLAUDE pane only; the context
  window, prompt/answer lines and other "limits" are not; a later answer /
  working line clears it; the reset parses in its own zone; what Iron-Proxy is
  sent classifies with its reset under a faithful copy of its classifier; the
  activity row carries ``limit: {line, kind, since, reset_words?, reset_at?}``;
* WHICH conversation: a pane this app started records its id and carries it;
  otherwise only the one session whose last record is the limit for this cwd
  (several -> none, never the newest file);
* ``POST /terminals/{id}/continue-on-next`` end to end: Iron-Proxy is told ONLY
  the limit line (never the user's prompts above it); the next account is
  picked; the conversation ``.jsonl`` + its sibling folder are copied into the
  next account's same project folder (never overwriting, never touching the
  source, confined to that account's home); a NEW pane on the next account's
  env is typed ``claude --resume <id>`` + Enter; the old pane is untouched;
* the fresh-start paths (no conversation, a different file already there),
  the refusals (no other free account -> 409 by kind and no pane; a pane on
  this PC's login -> 409), an adopted ``~/.claude`` as the next account, and
  an Iron-Proxy that declines to park.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.terminals import continue_on
from iron_jarvis.terminals.backend import FakeBackend
from iron_jarvis.terminals.limit_state import detect_limit, parse_reset

FIXTURE = Path(__file__).parent / "fixtures" / "fake_iron_proxy_v1301.py"
sys.path.insert(0, str(FIXTURE.parent))
from fake_iron_proxy_v1301 import FEATURES_PICK_PROFILE, FakeIronProxy  # noqa: E402

LIMIT = "Usage limit reached · continuing automatically at 3pm · esc to cancel"
SECRET_PROMPT = "> summarise the Henderson 1040 and their SSN notes"
SID = "0b6c2d1e-1111-4222-8333-944455556666"


# --------------------------------------------------------------------------- #
# Harness (as v1.302.0's)
# --------------------------------------------------------------------------- #
class _RecordingBackend(FakeBackend):
    instances: list["_RecordingBackend"] = []
    lock = threading.Lock()

    def __init__(self) -> None:
        super().__init__()
        self.env: dict | None = None
        self.cwd: str | None = None
        self.written: list[str] = []
        with _RecordingBackend.lock:
            _RecordingBackend.instances.append(self)

    def start(self, argv, cwd, env, cols, rows) -> None:  # type: ignore[override]
        self.env = dict(env) if env is not None else None
        self.cwd = cwd
        super().start(argv, cwd, env, cols, rows)

    def write(self, data) -> None:  # type: ignore[override]
        self.written.append(data if isinstance(data, str) else data.decode())
        super().write(data)


def _backend_of(pane_id: str) -> _RecordingBackend:
    found = [b for b in _RecordingBackend.instances
             if b.env is not None and b.env.get("IRONJARVIS_PANE_ID") == pane_id]
    assert found, f"no shell was started for pane {pane_id}"
    return found[-1]


def _home_keys(env: dict, var: str) -> list[str]:
    return [k for k in env if k.upper() == var]


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("IRON_PROXY_DATA_DIR", str(tmp_path / "ipd"))
    monkeypatch.setenv("IRONJARVIS_IRON_PROXY_BUNDLE", str(tmp_path / "no-bundle.mjs"))
    monkeypatch.delenv("IRONJARVIS_IRON_PROXY_NODE", raising=False)
    for k in ("CLAUDE_CONFIG_DIR", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    _RecordingBackend.instances = []
    monkeypatch.setattr("iron_jarvis.terminals.session.default_backend", _RecordingBackend)

    async def _no_watch(*_a, **_k):
        await asyncio.Event().wait()

    monkeypatch.setattr("iron_jarvis.iron_proxy.service.watch", _no_watch)
    yield


@pytest.fixture
def proj(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    return root


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "my repo"
    root.mkdir()
    return root


@pytest.fixture
def fake(tmp_path):
    f = FakeIronProxy(tmp_path / "ipd", features=list(FEATURES_PICK_PROFILE)).start()
    yield f
    f.stop()


@pytest.fixture
def client(proj):
    with TestClient(create_app(str(proj))) as c:
        yield c


def _enable(c) -> None:
    r = c.post("/iron-proxy/enable")
    assert r.status_code == 200 and r.json()["status"]["running"] is True, r.text


def _refresh(c) -> None:
    assert c.get("/iron-proxy?discover=0").status_code == 200


def _session(c, pane_id: str):
    return c.app.state.platform.terminals.get(pane_id)


def _print(c, pane_id: str, *lines: str) -> None:
    """What the Claude Code TUI would print into the pane."""
    _session(c, pane_id)._ingest(("\r\n".join(lines) + "\r\n").encode("utf-8"))


def _claude_pane(c, account_id: str | None, cwd: Path) -> dict:
    accounts = {"anthropic": account_id} if account_id else {"anthropic": "default"}
    r = c.post("/terminals", json={"cwd": str(cwd), "agent_cli": "claude", "accounts": accounts})
    assert r.status_code == 200, r.text
    return r.json()


def _park(fake, pid: str, *, kind: str = "rate-limit") -> str:
    until = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    fake.states[pid].update({"status": "parked", "parkedUntil": until, "parkedReason": {"kind": kind}})
    return until


def _signals(fake) -> list[tuple[str, dict]]:
    return [(path, body) for m, path, body in fake.calls if path.endswith("/signal")]


# --------------------------------------------------------------------------- #
# Detection (pure)
# --------------------------------------------------------------------------- #
#: Masked copies of the REAL limit records on this PC (``~/.claude/projects/**``,
#: ``isApiErrorMessage`` + ``error: "rate_limit"``), as the TUI prints them under
#: Claude Code's ⎿ result gutter.
REAL_SESSION = "You've hit your session limit · resets 3:45pm (America/New_York)"
REAL_WEEKLY = "You've hit your weekly limit · resets Sep 30, 2pm (America/New_York)"
REAL_CREDITS = "You're out of usage credits. Run /usage-credits to keep using Fable 5."
REAL_ORG = "Your org is out of usage credits."
NOW = datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc)  # 11:00 America/New_York


def _screen(line: str) -> str:
    return f"⏺ Working on it.\n  ⎿  {line}\n\n╭──────╮\n│ >    │\n╰──────╯\n? for shortcuts"


@pytest.mark.parametrize(
    "line,kind,reset",
    [
        (REAL_SESSION, "session", "resets 3:45pm (America/New_York)"),
        (REAL_WEEKLY, "weekly", "resets Sep 30, 2pm (America/New_York)"),
        (REAL_CREDITS, "credits", ""),
        (REAL_ORG, "credits", ""),
        ("You've reached your Opus limit · resets 6pm", "model", "resets 6pm"),
        (LIMIT, "usage", "at 3pm"),
        ("Usage limit reached again · continuing automatically in 2 hours · esc to cancel",
         "usage", "in 2 hours"),
        ("Fable limit reached · continuing on Opus 5 uses usage credits, and the prompt to confirm",
         "model", ""),
        ("/usage-credits to continue now", "credits", ""),
    ],
)
def test_every_account_limit_shape_is_found(line, kind, reset):
    """MUTATION: drop a branch of ``_SHAPES`` -> its shape is missed -> red."""
    found = detect_limit(_screen(line), NOW)
    assert found is not None, line
    assert (found.line, found.kind, found.reset_words) == (line, kind, reset)


def test_the_reset_parses_in_its_own_zone():
    """3:45pm America/New_York on 2026-09-28 is 19:45 UTC (EDT); Sep 30, 2pm
    is 18:00 UTC. MUTATION: ignore the bracketed zone -> red (this PC's zone)."""
    assert detect_limit(_screen(REAL_SESSION), NOW).reset_at == datetime(
        2026, 9, 28, 19, 45, tzinfo=timezone.utc)
    assert detect_limit(_screen(REAL_WEEKLY), NOW).reset_at == datetime(
        2026, 9, 30, 18, 0, tzinfo=timezone.utc)
    assert parse_reset("resets Sep 1, 2pm (America/New_York)", NOW) is None  # stale, not next year
    # A zone this PC is not in: 3:45pm Tokyo after 00:00 JST Sep 29 is 06:45 UTC Sep 29.
    assert parse_reset("resets 3:45pm (Asia/Tokyo)", NOW) == datetime(
        2026, 9, 29, 6, 45, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "text",
    [
        "Context limit reached · /compact or /clear to continue",
        "> Usage limit reached",
        "❯ You've hit your session limit · resets 3:45pm",
        "⏺ Rate limit reached",
        "⏺ You've hit your session limit · resets 3:45pm (America/New_York)",
        "Rate limit reached",
        "Subagent nesting limit reached",
        "watch limit reached",
        "device limit reached",
        "The report says the usage limit reached its cap yesterday.",
        "Usage limit reached for the whole team in March",
        "grep -n 'limit reached' src/app.py",
        "⏺ The banner said: Usage limit reached · resets 3pm",
        "Build log: Usage limit reached · resets 3pm",
    ],
)
def test_nothing_but_an_account_limit_is_a_limit(text):
    """The review's no-match fixtures: the context window, a prompt the user
    typed, an answer the model wrote, other limits. MUTATION: take ``⏺`` lines
    as hits -> red."""
    assert detect_limit(text + "\n│ > │") is None


@pytest.mark.parametrize("after", ["⏺ Here is the rest of the answer.", "✻ Thinking… (esc to interrupt)"])
def test_a_later_answer_or_working_line_clears_it(after):
    """MUTATION: stop clearing -> red."""
    assert detect_limit(f"  ⎿  {REAL_SESSION}\n{after}") is None


def test_a_tool_output_limit_line_is_cleared_by_the_next_answer():
    """⎿ stays allowed (Claude Code prints the account limit under it), so a
    TOOL whose output prints a limit message reads as one only while it is the
    last thing on screen; the next answer line clears it (see ``_LEAD``)."""
    tool = "⏺ Bash(type ops.log)\n  ⎿  You've hit your session limit · resets 3pm (America/New_York)"
    assert detect_limit(tool) is not None  # the documented cost, while it is last
    assert detect_limit(tool + "\n⏺ The log only quotes an old limit message.") is None


def test_an_old_limit_out_of_the_window_is_not_live():
    assert detect_limit(LIMIT + "\n" + "\n".join(f"line {i}" for i in range(30))) is None


# --- A faithful copy of Iron-Proxy's detectFromCliOutput + resetFromBody
# (packages/core/src/quota/detect.ts, util.ts @ 2d4f645), as the v1.301 tests do.
_IP_AUTHY = re.compile(r"not (?:logged in|authenticated)|please (?:log ?in|sign in|run .*login)|invalid "
                       r"(?:api key|token)|token (?:has )?expired|authentication (?:failed|required)"
                       r"|unauthorized|re-?authenticat", re.I)
_IP_LIMIT = re.compile(r"(?:usage|rate|weekly|daily|monthly|session|5-hour|five-hour) limit|limit "
                       r"(?:reached|exceeded|hit)|you(?:'ve| have) hit your|out of (?:credits|quota)"
                       r"|quota (?:exceeded|exhausted)|too many requests|resource_exhausted|429", re.I)
_IP_BILLING = re.compile(r"credit balance|insufficient[_ ]quota|billing|payment required|upgrade your plan", re.I)
_IP_TRY = re.compile(r"(?:try again|retry)\s+(?:in|after)\s+([\d.]+\s*[a-z]+(?:\s*[\d.]+\s*[a-z]+)?)", re.I)
_IP_RESETS = re.compile(r"resets?\s+(?:at\s+)?([0-9]{1,2}(?::[0-9]{2})?\s*(?:am|pm)?)", re.I)
_IP_CLOCK = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", re.I)


def iron_proxy_reads(text: str) -> tuple[str | None, bool]:
    """``(kind, reset parsed?)`` exactly as Iron-Proxy's classifier would."""
    t = text.lower()
    if _IP_BILLING.search(t):
        return "billing", False
    if _IP_AUTHY.search(t):
        return "auth-expired", False
    if not _IP_LIMIT.search(t):
        return None, False
    kind = ("quota-exhausted" if re.search(r"usage|weekly|daily|monthly|session|5-hour|five-hour|quota|credits", t)
            else "rate-limit")
    reset = False
    m = _IP_TRY.search(text)
    if m and re.search(r"\d+(?:\.\d+)?\s*(ms|s|secs?|seconds?|m|mins?|minutes?|h|hrs?|hours?|d|days?)(?![a-z])",
                       m.group(1).lower()):
        reset = True
    elif (r := _IP_RESETS.search(text)) is not None:
        c = _IP_CLOCK.search(r.group(1))
        reset = bool(c and (c.group(2) is not None or c.group(3) is not None))
    return kind, reset


@pytest.mark.parametrize(
    "line,expect_reset",
    [(REAL_SESSION, True), (REAL_WEEKLY, True), (REAL_CREDITS, False), (REAL_ORG, False),
     (LIMIT, True), ("Fable limit reached · continuing on Opus 5 uses usage credits", False)],
)
def test_what_iron_proxy_is_sent_classifies_with_its_reset(line, expect_reset):
    """Iron-Proxy's own classifier misses "out of usage credits" and cannot
    read "resets Sep 30, 2pm"; the normalised line fixes both and is built only
    from the matched message. MUTATION: send the raw line -> red."""
    found = detect_limit(_screen(line), NOW)
    sent = found.signal_text(NOW)
    assert sent.startswith(line)
    kind, reset = iron_proxy_reads(sent)
    assert kind in ("quota-exhausted", "rate-limit"), (sent, kind)
    assert reset is expect_reset, sent


# --------------------------------------------------------------------------- #
# The activity row
# --------------------------------------------------------------------------- #
def test_the_activity_row_carries_the_limit_for_a_claude_pane_only(client, repo):
    """``limit: {line, kind, since, reset_words?, reset_at?}`` while on screen;
    ``since`` holds while the TUI repaints; an answer clears it; a plain shell
    never has one. MUTATION: drop the ``limit`` key from the activity row -> red."""
    c = client
    claude = _claude_pane(c, None, repo)
    shell = c.post("/terminals", json={"cwd": str(repo)}).json()
    _print(c, claude["id"], SECRET_PROMPT, f"  ⎿  {REAL_SESSION}", "╭────╮", "│ >  │")
    _print(c, shell["id"], f"  ⎿  {REAL_SESSION}")

    def row(pid):
        return next(p for p in c.get("/terminals/activity").json()["panes"] if p["id"] == pid)

    first = row(claude["id"])["limit"]
    assert first["line"] == REAL_SESSION and first["kind"] == "session"
    assert first["reset_words"] == "resets 3:45pm (America/New_York)"
    assert first["reset_at"].endswith("+00:00") and first["since"]
    assert "limit" not in row(shell["id"])
    # Windows' clock ticks every 15.6 ms: wait past a tick so a `since` that
    # restarted on the repaint would show a different value (mutation C5).
    import time

    time.sleep(0.05)
    _print(c, claude["id"], "? for shortcuts")
    assert row(claude["id"])["limit"]["since"] == first["since"]
    listed = {p["id"]: p for p in c.get("/terminals").json()["terminals"]}
    assert listed[claude["id"]]["limit"]["line"] == REAL_SESSION
    _print(c, claude["id"], "⏺ Picking up where we left off.")
    assert "limit" not in row(claude["id"])


# --------------------------------------------------------------------------- #
# Real-shaped conversation files
# --------------------------------------------------------------------------- #
def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _record(sid: str, cwd: Path, *, limit: bool, when: datetime | None = None,
            text: str = REAL_SESSION, resets_at: int | None = None) -> dict:
    when = when or datetime.now(timezone.utc)
    rec = {
        "parentUuid": "p", "isSidechain": False, "type": "assistant", "uuid": "u",
        "timestamp": _iso(when), "cwd": str(cwd), "sessionId": sid, "version": "2.1.288",
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
    }
    if limit:
        rec.update({"isApiErrorMessage": True, "error": "rate_limit", "apiErrorStatus": 429})
        if resets_at is not None:
            rec["quotaLimits"] = {"status": "rejected", "resetsAt": resets_at, "rateLimitType": "five_hour"}
    return rec


def _write_session(home: str, folder: str, sid: str, *records: dict, sidecar: bool = True) -> Path:
    path = Path(home) / "projects" / folder / f"{sid}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    first = {"type": "user", "timestamp": _iso(datetime.now(timezone.utc) - timedelta(minutes=30)),
             "message": {"role": "user", "content": "summarise the Henderson 1040"}}
    path.write_text("\n".join(json.dumps(r) for r in (first, *records)) + "\n", encoding="utf-8")
    if sidecar:
        side = path.parent / sid / "subagents"
        side.mkdir(parents=True, exist_ok=True)
        (side / "agent-1.jsonl").write_text('{"sub":1}\n', encoding="utf-8")
    return path


#: Claude Code's folder name for a long cwd is truncated/hashed — never recompute it.
FOLDER = "C--Users-VR-Projects-a-very-long-folder-name-trunc-9f3a"


def _three(fake):
    work = fake.add_profile("anthropic", "Work Max")
    personal = fake.add_profile("anthropic", "Personal")
    return work, personal


# --------------------------------------------------------------------------- #
# Continue on the next account — end to end
# --------------------------------------------------------------------------- #
def test_a_pane_this_app_started_carries_its_recorded_conversation(client, fake, repo):
    """The pane was started from Launch, so its conversation id is RECORDED;
    that file is found by glob (whatever the folder name) and carried, the new
    pane resumes it by id and records it too. MUTATION: hand Iron-Proxy the
    whole tail -> the prompt reaches it -> red. MUTATION: copy into a recomputed
    folder name -> red."""
    c = client
    _enable(c)
    work, personal = _three(fake)
    _refresh(c)
    old = c.post("/terminals/launch", json={"cli": "claude", "account": work["id"], "cwd": str(repo)}).json()
    sid = old["claude_session_id"]
    assert _backend_of(old["id"]).written == [f"claude --session-id {sid}\r"]
    # Its last record is an ordinary one (the limit was shown on screen only):
    # nothing but the RECORDED id can find this file.
    source = _write_session(work["cli"]["home"], FOLDER, sid, _record(sid, repo, limit=False))
    source_bytes = source.read_bytes()
    _print(c, old["id"], SECRET_PROMPT, "⏺ Working on it.", f"  ⎿  {REAL_SESSION}", "│ > │")

    r = c.post(f"/terminals/{old['id']}/continue-on-next")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["resumed"] is True and out["session_id"] == sid and out["parked"] is True
    assert out["from"] == {"id": work["id"], "title": "Work Max"}
    assert out["to"] == {"id": personal["id"], "title": "Personal"}
    assert out["reset_at"].endswith("+00:00")  # parsed from the words on screen
    assert out["note"].startswith('Carried your conversation over to "Personal".')
    pane = out["pane"]
    assert pane["name"] == "Claude Code · Personal (continued)" and pane["cwd"] == str(repo)
    assert pane["accounts"]["anthropic"]["id"] == personal["id"]
    assert pane["claude_session_id"] == sid

    # Iron-Proxy heard ONLY the limit message (normalised), for the right account.
    [(path, body)] = _signals(fake)
    assert path == f"/iron/profiles/{work['id']}/signal"
    assert body["text"].startswith(REAL_SESSION) and "try again in" in body["text"]
    assert all("Henderson" not in json.dumps(b) for _m, _p, b in fake.calls)

    dest = Path(personal["cli"]["home"]) / "projects" / FOLDER
    assert (dest / f"{sid}.jsonl").read_bytes() == source_bytes
    assert (dest / sid / "subagents" / "agent-1.jsonl").is_file()
    assert source.read_bytes() == source_bytes
    be = _backend_of(pane["id"])
    assert be.env["CLAUDE_CONFIG_DIR"] == personal["cli"]["home"]
    assert be.written == [f"claude --resume {sid}\r"]
    assert _backend_of(old["id"]).written == [f"claude --session-id {sid}\r"]  # untouched
    assert _session(c, old["id"]).alive


def test_a_typed_claude_carries_only_the_session_that_hit_the_limit(client, fake, repo):
    """No recorded id (the user typed `claude`): ONLY a session whose LAST
    record is the limit record for this pane's cwd — never the newest file.
    MUTATION: pick the newest by mtime -> the busier session is carried -> red.
    MUTATION: ignore the cwd -> red."""
    c = client
    _enable(c)
    work, personal = _three(fake)
    _refresh(c)
    old = _claude_pane(c, work["id"], repo)
    hit = "11111111-2222-4333-8444-555555555555"
    reset_epoch = int((datetime.now(timezone.utc) + timedelta(hours=2)).timestamp())
    _write_session(work["cli"]["home"], FOLDER, hit,
                   _record(hit, repo, limit=True, resets_at=reset_epoch))
    # Newer, same cwd, still working (not a limit) — the newest by mtime:
    busy = "22222222-3333-4444-8555-666666666666"
    later = _write_session(work["cli"]["home"], FOLDER, busy, _record(busy, repo, limit=False))
    future = datetime.now().timestamp() + 30
    os.utime(later, (future, future))
    # A limit in ANOTHER folder's project:
    other = "33333333-4444-4555-8666-777777777777"
    _write_session(work["cli"]["home"], "C--elsewhere", other,
                   _record(other, Path("C:/elsewhere"), limit=True))
    _print(c, old["id"], f"  ⎿  {REAL_SESSION}")
    out = c.post(f"/terminals/{old['id']}/continue-on-next").json()
    assert out["resumed"] is True and out["session_id"] == hit
    assert out["pane"]["claude_session_id"] == hit
    # The reset is the limit record's own (quotaLimits.resetsAt), not the words.
    assert out["reset_at"] == datetime.fromtimestamp(reset_epoch, tz=timezone.utc).isoformat()
    assert _backend_of(out["pane"]["id"]).written == [f"claude --resume {hit}\r"]


def test_several_sessions_at_the_limit_carry_none(client, fake, repo):
    c = client
    _enable(c)
    work, _personal = _three(fake)
    _refresh(c)
    old = _claude_pane(c, work["id"], repo)
    for sid in ("11111111-2222-4333-8444-555555555555", "22222222-3333-4444-8555-666666666666"):
        _write_session(work["cli"]["home"], FOLDER, sid, _record(sid, repo, limit=True))
    _print(c, old["id"], f"  ⎿  {REAL_SESSION}")
    out = c.post(f"/terminals/{old['id']}/continue-on-next").json()
    assert out["resumed"] is False and out["note"].startswith(continue_on.SEVERAL)
    sid = out["pane"]["claude_session_id"]  # fresh, with its own new id
    assert _backend_of(out["pane"]["id"]).written == [f"claude --session-id {sid}\r"]


def test_a_recorded_id_the_user_moved_away_from_falls_through(client, fake, repo):
    """The recorded conversation went quiet long before the limit (the user
    /clear-ed or /resume-d elsewhere): the session that hit the limit is carried.
    MUTATION: trust the recorded id regardless of its age -> red."""
    c = client
    _enable(c)
    work, _personal = _three(fake)
    _refresh(c)
    old = c.post("/terminals/launch", json={"cli": "claude", "account": work["id"], "cwd": str(repo)}).json()
    stale = old["claude_session_id"]
    _write_session(work["cli"]["home"], FOLDER, stale,
                   _record(stale, repo, limit=False, when=datetime.now(timezone.utc) - timedelta(hours=1)))
    moved = "44444444-5555-4666-8777-888888888888"
    _write_session(work["cli"]["home"], FOLDER, moved, _record(moved, repo, limit=True))
    _print(c, old["id"], f"  ⎿  {REAL_SESSION}")
    out = c.post(f"/terminals/{old['id']}/continue-on-next").json()
    assert out["session_id"] == moved


def test_no_conversation_starts_fresh_with_a_new_id(client, fake, repo):
    c = client
    _enable(c)
    work, _personal = _three(fake)
    _refresh(c)
    old = _claude_pane(c, work["id"], repo)
    _print(c, old["id"], f"  ⎿  {REAL_SESSION}")
    out = c.post(f"/terminals/{old['id']}/continue-on-next").json()
    assert out["resumed"] is False and "session_id" not in out
    assert out["note"].startswith("No conversation from this pane was found")
    sid = out["pane"]["claude_session_id"]
    assert continue_on.is_session_id(sid)
    assert _backend_of(out["pane"]["id"]).written == [f"claude --session-id {sid}\r"]


def test_a_different_file_already_there_is_never_overwritten(client, fake, repo):
    """MUTATION: overwrite -> the next account's own file is replaced -> red."""
    c = client
    _enable(c)
    work, personal = _three(fake)
    _refresh(c)
    old = _claude_pane(c, work["id"], repo)
    sid = "55555555-6666-4777-8888-999999999999"
    _write_session(work["cli"]["home"], FOLDER, sid, _record(sid, repo, limit=True))
    theirs = Path(personal["cli"]["home"]) / "projects" / FOLDER / f"{sid}.jsonl"
    theirs.parent.mkdir(parents=True)
    theirs.write_text('{"from":"personal"}\n', encoding="utf-8")
    _print(c, old["id"], f"  ⎿  {REAL_SESSION}")
    out = c.post(f"/terminals/{old['id']}/continue-on-next").json()
    assert out["resumed"] is False
    assert "already holds a different conversation" in out["note"]
    assert theirs.read_text(encoding="utf-8") == '{"from":"personal"}\n'


def test_no_other_free_account_is_409_before_anything_is_told(client, fake, repo):
    """By kind, no pane, and Iron-Proxy is NOT told (refusals come first).
    MUTATION: tell Iron-Proxy before the check -> a signal is recorded -> red."""
    c = client
    _enable(c)
    work, personal = _three(fake)
    _park(fake, personal["id"], kind="rate-limit")
    _refresh(c)
    old = _claude_pane(c, work["id"], repo)
    _print(c, old["id"], f"  ⎿  {REAL_SESSION}")
    before = len(c.get("/terminals").json()["terminals"])
    shells = len(_RecordingBackend.instances)
    r = c.post(f"/terminals/{old['id']}/continue-on-next")
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail.startswith("No other Claude account can take over right now:")
    assert '"Personal" is at its request limit until' in detail
    assert _signals(fake) == []
    assert len(c.get("/terminals").json()["terminals"]) == before
    assert len(_RecordingBackend.instances) == shells


def test_a_full_canvas_is_refused_before_anything_is_told_or_copied(client, fake, repo):
    """MUTATION: check the pane cap after copying -> a copy and a signal land -> red."""
    c = client
    _enable(c)
    work, personal = _three(fake)
    _refresh(c)
    old = _claude_pane(c, work["id"], repo)
    sid = "66666666-7777-4888-8999-aaaaaaaaaaaa"
    _write_session(work["cli"]["home"], FOLDER, sid, _record(sid, repo, limit=True))
    _print(c, old["id"], f"  ⎿  {REAL_SESSION}")
    terms = c.app.state.platform.terminals
    terms.max_sessions = sum(1 for i in terms.list() if i.get("alive"))
    r = c.post(f"/terminals/{old['id']}/continue-on-next")
    assert r.status_code == 429 and r.json()["detail"] == continue_on.FULL
    assert _signals(fake) == []
    assert not (Path(personal["cli"]["home"]) / "projects").exists()


def test_the_only_account_says_there_is_no_other(client, fake, repo):
    c = client
    _enable(c)
    work = fake.add_profile("anthropic", "Work Max")
    _refresh(c)
    old = _claude_pane(c, work["id"], repo)
    _print(c, old["id"], f"  ⎿  {REAL_SESSION}")
    r = c.post(f"/terminals/{old['id']}/continue-on-next")
    assert r.status_code == 409 and r.json()["detail"] == continue_on.NO_OTHER


def test_a_pane_on_this_pcs_login_is_refused(client, fake, repo):
    """Never this PC's login implicitly, in either direction."""
    c = client
    plain = c.post("/terminals", json={"cwd": str(repo)}).json()  # Iron-Proxy off: no accounts
    assert "accounts" not in plain
    _enable(c)
    fake.add_profile("anthropic", "Personal")
    _refresh(c)
    for pane in (_claude_pane(c, None, repo), plain):
        r = c.post(f"/terminals/{pane['id']}/continue-on-next")
        assert r.status_code == 409 and r.json()["detail"] == continue_on.THIS_PC
    assert c.post("/terminals/term_nope/continue-on-next").status_code == 404


def test_an_iron_proxy_that_declines_to_park_still_continues_and_says_so(client, fake, repo, monkeypatch):
    """MUTATION: take Iron-Proxy's answer even when it is the same account ->
    the pane runs on Work Max -> red."""
    c = client
    _enable(c)
    work, personal = _three(fake)
    _refresh(c)
    monkeypatch.setattr(fake, "signal", lambda pid, body: {"parked": False})
    old = _claude_pane(c, work["id"], repo)
    _print(c, old["id"], f"  ⎿  {REAL_SESSION}")
    out = c.post(f"/terminals/{old['id']}/continue-on-next").json()
    assert out["parked"] is False and out["to"]["id"] == personal["id"]
    assert 'Iron-Proxy did not mark "Work Max" as limited' in out["note"]
    assert _backend_of(out["pane"]["id"]).env["CLAUDE_CONFIG_DIR"] == personal["cli"]["home"]


def test_without_a_limit_on_screen_iron_proxy_is_not_told(client, fake, repo):
    c = client
    _enable(c)
    work, _personal = _three(fake)
    _refresh(c)
    old = _claude_pane(c, work["id"], repo)
    _print(c, old["id"], SECRET_PROMPT)
    out = c.post(f"/terminals/{old['id']}/continue-on-next").json()
    assert _signals(fake) == []
    assert "No limit message was on screen" in out["note"]


def test_an_adopted_default_home_receives_the_conversation(client, fake, repo, tmp_path, monkeypatch):
    """The next account is this PC's adopted ``~/.claude``: the copy goes to
    ``~/.claude/projects/<same folder>`` and the pane sets NO CLAUDE_CONFIG_DIR."""
    home = tmp_path / "user"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    c = client
    _enable(c)
    work = fake.add_profile("anthropic", "Work Max")
    fake.add_profile("anthropic", "This PC", home=str(home / ".claude"), adopted=True)
    _refresh(c)
    old = _claude_pane(c, work["id"], repo)
    sid = "77777777-8888-4999-8aaa-bbbbbbbbbbbb"
    _write_session(work["cli"]["home"], FOLDER, sid, _record(sid, repo, limit=True))
    _print(c, old["id"], f"  ⎿  {REAL_SESSION}")
    out = c.post(f"/terminals/{old['id']}/continue-on-next").json()
    assert out["resumed"] is True
    assert (home / ".claude" / "projects" / FOLDER / f"{sid}.jsonl").is_file()
    assert _home_keys(_backend_of(out["pane"]["id"]).env, "CLAUDE_CONFIG_DIR") == []


def test_the_recorded_id_survives_a_restart_and_resume_uses_it(proj, fake, repo, tmp_path, monkeypatch):
    """After a restart, Resume reopens the conversation BY ID while that id is
    still the newest conversation in its folder; once the user has /clear-ed
    into a newer one it falls back to `claude --continue`. MUTATION: the
    snapshot drops ``claude_session_id`` -> red. MUTATION: skip the "newest"
    check -> the second restart still resumes the old id -> red."""
    claude = tmp_path / "pc-claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    with TestClient(create_app(str(proj))) as c1:
        pane = c1.post("/terminals/launch", json={"cli": "claude", "cwd": str(repo)}).json()
        sid = pane["claude_session_id"]
    _write_session(str(claude), FOLDER, sid, _record(sid, repo, limit=False))
    _RecordingBackend.instances = []
    with TestClient(create_app(str(proj))) as c2:
        # Decided in restore (boot); a row read touches no file — `info()` runs
        # on the event loop for the pane tools. MUTATION: decide in info() -> red.
        def _no_fs(*_a, **_k):
            raise AssertionError("info() read the filesystem")

        real_newest = continue_on.newest_recorded
        monkeypatch.setattr(continue_on, "newest_recorded", _no_fs)
        row = {p["id"]: p for p in c2.get("/terminals").json()["terminals"]}[pane["id"]]
        assert row["claude_session_id"] == sid
        assert row["resume_cli"] == "claude" and row["resume_command"] == f"claude --resume {sid}"
        monkeypatch.setattr(continue_on, "newest_recorded", real_newest)
    newer = "abababab-1111-4222-8333-444444444444"
    later = _write_session(str(claude), FOLDER, newer, _record(newer, repo, limit=False))
    ahead = datetime.now().timestamp() + 30
    os.utime(later, (ahead, ahead))
    _RecordingBackend.instances = []
    with TestClient(create_app(str(proj))) as c3:
        row = {p["id"]: p for p in c3.get("/terminals").json()["terminals"]}[pane["id"]]
        assert row["resume_command"] == "claude --continue"


def test_a_recorded_conversation_that_went_quiet_before_the_limit_is_not_carried(client, fake, repo):
    """The recorded id is the only (so the newest) conversation, but nothing
    was written to it near the limit: it is not the one that ran out, so the
    new pane starts fresh. MUTATION: trust the recorded id whatever its last
    record's age -> it is carried -> red."""
    c = client
    _enable(c)
    work, _personal = _three(fake)
    _refresh(c)
    old = c.post("/terminals/launch", json={"cli": "claude", "account": work["id"], "cwd": str(repo)}).json()
    sid = old["claude_session_id"]
    quiet = _write_session(work["cli"]["home"], FOLDER, sid, _record(
        sid, repo, limit=False, when=datetime.now(timezone.utc) - timedelta(hours=1)))
    an_hour_ago = datetime.now().timestamp() - 3600
    os.utime(quiet, (an_hour_ago, an_hour_ago))
    _print(c, old["id"], f"  ⎿  {REAL_SESSION}")
    out = c.post(f"/terminals/{old['id']}/continue-on-next").json()
    assert out["resumed"] is False and "session_id" not in out


def test_a_recorded_id_that_is_no_longer_the_newest_is_not_carried(client, fake, repo):
    """K2 for continue: the recorded conversation is recent but the user has
    since /clear-ed into a newer one, which is the one that hit the limit — the
    scan carries THAT one. MUTATION: skip the "newest" check -> the recorded
    (stale) id is carried -> red."""
    c = client
    _enable(c)
    work, _personal = _three(fake)
    _refresh(c)
    old = c.post("/terminals/launch", json={"cli": "claude", "account": work["id"], "cwd": str(repo)}).json()
    recorded = old["claude_session_id"]
    _write_session(work["cli"]["home"], FOLDER, recorded, _record(recorded, repo, limit=False))
    cleared = "cdcdcdcd-1111-4222-8333-444444444444"
    later = _write_session(work["cli"]["home"], FOLDER, cleared, _record(cleared, repo, limit=True))
    ahead = datetime.now().timestamp() + 30
    os.utime(later, (ahead, ahead))
    _print(c, old["id"], f"  ⎿  {REAL_SESSION}")
    out = c.post(f"/terminals/{old['id']}/continue-on-next").json()
    assert out["session_id"] == cleared


def test_a_round_trip_brings_the_grown_conversation_back(client, fake, repo):
    """K1: ``--resume`` KEEPS the id, so A -> B -> A finds A holding the OLDER
    prefix of the conversation that grew on B. It is replaced (resumed both
    times, A's file == B's) and A's old copy is MOVED into Iron Jarvis's trash,
    never deleted; the sibling folder follows the same rule. MUTATION: treat a
    prefix as divergence -> the way back starts fresh -> red. MUTATION: skip
    the trash copy -> red."""
    c = client
    _enable(c)
    work, personal = _three(fake)
    _refresh(c)
    a = c.post("/terminals/launch", json={"cli": "claude", "account": work["id"], "cwd": str(repo)}).json()
    sid = a["claude_session_id"]
    a_file = _write_session(work["cli"]["home"], FOLDER, sid, _record(sid, repo, limit=False))
    a_side = a_file.parent / sid / "subagents" / "agent-1.jsonl"
    first = a_file.read_bytes()
    _print(c, a["id"], f"  ⎿  {REAL_SESSION}")
    there = c.post(f"/terminals/{a['id']}/continue-on-next").json()
    assert there["resumed"] is True and there["to"]["id"] == personal["id"]
    b = there["pane"]
    assert b["claude_session_id"] == sid

    # The conversation grows on B (append-only), the subagent log too.
    b_file = Path(personal["cli"]["home"]) / "projects" / FOLDER / f"{sid}.jsonl"
    with open(b_file, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(_record(sid, repo, limit=False, text="more work on B")) + "\n")
    b_side = b_file.parent / sid / "subagents" / "agent-1.jsonl"
    with open(b_side, "a", encoding="utf-8") as fh:
        fh.write('{"sub":2}\n')
    fake.states[work["id"]].update({"status": "ready", "parkedUntil": None, "parkedReason": None})
    _refresh(c)
    _print(c, b["id"], f"  ⎿  {REAL_SESSION}")
    back = c.post(f"/terminals/{b['id']}/continue-on-next").json()
    assert back["resumed"] is True, back["note"]
    assert back["to"]["id"] == work["id"] and back["session_id"] == sid
    assert a_file.read_bytes() == b_file.read_bytes() != first
    assert a_side.read_bytes() == b_side.read_bytes()
    trash = c.app.state.platform.config.home / "trash"
    kept = list(trash.glob(f"*/claude-carry/{FOLDER}/{sid}.jsonl"))
    assert len(kept) == 1 and kept[0].read_bytes() == first
    assert list(trash.glob(f"*/claude-carry/{FOLDER}/{sid}/subagents/agent-1.jsonl"))
    assert _backend_of(back["pane"]["id"]).written == [f"claude --resume {sid}\r"]


def test_a_diverged_copy_is_never_replaced(tmp_path):
    """Not a prefix (same length but different, or longer): refused, nothing
    written, nothing in the trash. A diverged sibling file is kept; a new one is
    copied; a prefix one is replaced with the old copy kept. MUTATION: replace
    any differing file -> red."""
    sid = "efefefef-1111-4222-8333-444444444444"
    src = _write_session(str(tmp_path / "a"), FOLDER, sid, _record(sid, tmp_path, limit=False))
    nxt = tmp_path / "b"
    dest = nxt / "projects" / FOLDER / f"{sid}.jsonl"
    dest.parent.mkdir(parents=True)
    diverged = src.read_bytes()[:-5] + b"XXXX\n"
    dest.write_bytes(diverged)
    trash = tmp_path / "trash"
    got = continue_on.carry_over(src, str(nxt), "Personal", trash_root=trash)
    assert got.resumed is False and "already holds a different conversation" in got.note
    assert dest.read_bytes() == diverged and not trash.exists()

    # The sibling folder: one file diverged, one is an older prefix, one is new.
    # The main file agrees now — rewritten with the SAME size inside the SAME
    # clock tick (mtime put back): v1.303.3 found `filecmp.cmp` answering from
    # its (size, mtime) cache here. MUTATION: compare with filecmp -> red.
    before = dest.stat()
    dest.write_bytes(src.read_bytes())
    os.utime(dest, ns=(before.st_atime_ns, before.st_mtime_ns))
    src_side = src.parent / sid / "subagents"
    (src_side / "agent-2.jsonl").write_text('{"a":1}\n{"a":2}\n', encoding="utf-8")
    (src_side / "agent-3.jsonl").write_text('{"new":1}\n', encoding="utf-8")
    dst_side = dest.parent / sid / "subagents"
    dst_side.mkdir(parents=True)
    (dst_side / "agent-1.jsonl").write_text('{"theirs":1}\n', encoding="utf-8")  # diverged
    (dst_side / "agent-2.jsonl").write_text('{"a":1}\n', encoding="utf-8")  # an older prefix
    got = continue_on.carry_over(src, str(nxt), "Personal", trash_root=trash)
    assert got.resumed is True
    assert (dst_side / "agent-1.jsonl").read_text(encoding="utf-8") == '{"theirs":1}\n'
    assert (dst_side / "agent-2.jsonl").read_text(encoding="utf-8") == '{"a":1}\n{"a":2}\n'
    assert (dst_side / "agent-3.jsonl").read_text(encoding="utf-8") == '{"new":1}\n'
    kept = list(trash.glob(f"*/claude-carry/{FOLDER}/{sid}/subagents/agent-2.jsonl"))
    assert len(kept) == 1 and kept[0].read_text(encoding="utf-8") == '{"a":1}\n'


# --------------------------------------------------------------------------- #
# Confinement and the conversation reader (unit)
# --------------------------------------------------------------------------- #
def test_the_copy_is_confined_to_the_next_accounts_home(tmp_path):
    """A destination that would resolve outside the next account's home (a
    folder name that climbs) is refused and nothing is written. MUTATION: drop
    the ``_inside`` check -> red."""
    nxt = tmp_path / "b"
    nxt.mkdir()
    sid = "88888888-9999-4aaa-8bbb-cccccccccccc"
    real = _write_session(str(tmp_path / "a"), FOLDER, sid, _record(sid, tmp_path, limit=True))

    class _Source:  # a real file whose folder NAME climbs out of the projects dir
        stem, name = sid, f"{sid}.jsonl"
        parent = SimpleNamespace(name=os.path.join("..", "..", "out"))

        def __fspath__(self):
            return str(real)

        def with_suffix(self, _s):
            return real.with_suffix("")

        def resolve(self):
            return real.resolve()

    got = continue_on.carry_over(_Source(), str(nxt), "Personal")
    assert got.resumed is False and "outside" in got.note
    assert not (tmp_path / "out").exists()


def test_only_session_shaped_files_are_read(tmp_path):
    home = tmp_path / "h"
    _write_session(str(home), FOLDER, "not-a-uuid;rm", _record("x", tmp_path, limit=True))
    found = continue_on.find_conversation(str(home), str(tmp_path), None,
                                          datetime.now(timezone.utc).timestamp())
    assert found.path is None


def test_the_last_record_is_read_from_a_bounded_tail(tmp_path):
    sid = "99999999-aaaa-4bbb-8ccc-dddddddddddd"
    path = _write_session(str(tmp_path), FOLDER, sid, _record(sid, tmp_path, limit=True))
    big = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(['{"pad": "' + "x" * 1_000_000 + '"}'] + big) + "\n", encoding="utf-8")
    rec = continue_on.last_record(path)
    assert rec is not None and continue_on.is_limit_record(rec)


@pytest.mark.skipif(sys.platform != "win32", reason="a Windows sharing violation")
def test_a_destination_held_open_is_retried_then_said(tmp_path):
    """A Claude still running on the destination account holds its jsonl open
    (Python's own open() has no FILE_SHARE_DELETE, like a running process): the
    replace is retried briefly, then the carry stops with the plain words, the
    temp copy is gone and the old copy is in the trash. MUTATION: drop the
    temp-file cleanup -> a `.ij-carry-` file is left -> red."""
    sid = "a1a1a1a1-1111-4222-8333-444444444444"
    src = _write_session(str(tmp_path / "a"), FOLDER, sid, _record(sid, tmp_path, limit=False))
    older = src.read_bytes()
    with open(src, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(_record(sid, tmp_path, limit=False, text="grew")) + "\n")
    nxt = tmp_path / "b"
    dest = nxt / "projects" / FOLDER / f"{sid}.jsonl"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(older)
    trash = tmp_path / "trash"
    with open(dest, "rb"):
        got = continue_on.carry_over(src, str(nxt), "Work Max", trash_root=trash)
    assert got.resumed is False
    assert got.note.startswith('The conversation file is open in another pane on "Work Max" — '
                               "close Claude there and press Continue again.")
    assert not list(dest.parent.glob(".*ij-carry-*"))
    assert dest.read_bytes() == older
    assert [p.read_bytes() for p in trash.glob(f"*/claude-carry/{FOLDER}/{sid}.jsonl")] == [older]


def test_claude_home_is_the_panes_recorded_account_home(tmp_path, monkeypatch):
    """The home a pane's Claude runs on is its RECORDED account home — never
    ~/.claude or an inherited CLAUDE_CONFIG_DIR; "this PC's login" is
    ~/.claude; a pane with no account follows the environment. MUTATION:
    ignore the recorded home -> red."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "inherited"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "user"))
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    on_account = SimpleNamespace(accounts={"anthropic": {
        "id": "prof_0002", "title": "Personal", "source": "iron-proxy", "home": str(tmp_path / "acct")}})
    this_pc = SimpleNamespace(accounts={"anthropic": {"id": None, "title": "this PC's login",
                                                      "source": "default"}})
    plain = SimpleNamespace(accounts=None)
    assert continue_on.claude_home(on_account) == str(tmp_path / "acct")
    assert continue_on.claude_home(this_pc) == str(tmp_path / "user" / ".claude")
    assert continue_on.claude_home(plain) == str(tmp_path / "inherited")

