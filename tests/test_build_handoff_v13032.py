"""When the next account cannot continue a carried conversation (v1.303.2),
driven through the REAL app factory + the real terminals manager, a recording
fake PTY backend and the fake Iron-Proxy.

Pinned here:

* the pane "continue on the next account" opened records ``continued_from:
  {from_id, from_title, session_id, carried_path, at}`` (row + snapshot);
* RESUME FAILURE, raised ONLY by real-shaped Claude Code records in the
  CARRIED file (a NEW assistant record after ``at`` with ``isApiErrorMessage``
  and a 4xx status or words that name the conversation): ``resume_failed:
  {line, since}`` on the activity row; the SCREEN never raises it (tool output
  under ``⎿`` reads like Claude's own error) and only clears it while Claude
  works; a ``rate_limit`` record is the limit flow; an auth error is
  ``sign_in_needed`` instead (by words/status — NOT the CLI's
  ``authentication_failed`` code, which it stamps on every 403); a transient
  or unrecognised fault is neither; a later answer clears it;
* the HANDOFF (deterministic, no model call): the first ask (<= 1,500), the
  last 6 text turns (each <= 800), no tool_result payload, the tool paths
  (<= 30), <= 8,000 in total, secrets masked, written to
  ``<home>/handoffs/<sid>/handoff.md`` — never the project — with folders
  older than 30 days pruned;
* ``POST /terminals/{id}/start-fresh-with-handoff``: refuses while Claude is
  working or waiting on a question; types Ctrl+C (clears the composer —
  measured live), ``/exit``, Enter, waits for the SHELL prompt, then
  ``claude --session-id <new> --add-dir=<that conversation's folder>
  "<prompt>"`` (``--add-dir`` is variadic in claude 2.1.289 — the ``=`` form
  keeps the prompt a prompt) quoted for the pane's shell; records the new id;
  clears ``resume_failed``; refuses with a 409 sentence when there is nothing
  to hand off or the shell prompt is not seen.
"""

from __future__ import annotations

import asyncio
import json
import re
import shlex
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.terminals import handoff as handoff_mod
from iron_jarvis.terminals.backend import FakeBackend
from iron_jarvis.terminals.handoff import build_handoff
from iron_jarvis.terminals.resume_failed import check_file

FIXTURE = Path(__file__).parent / "fixtures" / "fake_iron_proxy_v1301.py"
sys.path.insert(0, str(FIXTURE.parent))
from fake_iron_proxy_v1301 import FEATURES_PICK_PROFILE, FakeIronProxy  # noqa: E402

REAL_SESSION = "You've hit your session limit · resets 3:45pm (America/New_York)"
FOLDER = "C--Users-VR-Projects-a-very-long-folder-name-trunc-9f3a"
#: Claude Code's own words for a 400 from the API (a thinking block signed by
#: another organisation is the likeliest real one for a carried conversation).
BAD_REQUEST = (
    'API Error: 400 {"type":"error","error":{"type":"invalid_request_error","message":'
    '"messages.1.content.0: Invalid `signature` in `thinking` block"}}'
)
NO_MODEL = "API Error: 403 Your organization does not have access to this model."
LOGIN_EXPIRED = "Login expired · Please run /login"
LOST = "API Error: Connection lost mid-response. The response above may be incomplete."


# --------------------------------------------------------------------------- #
# Harness (as v1.303.0's)
# --------------------------------------------------------------------------- #
class _RecordingBackend(FakeBackend):
    instances: list["_RecordingBackend"] = []
    lock = threading.Lock()

    def __init__(self) -> None:
        super().__init__()
        self.env: dict | None = None
        self.written: list[str] = []
        with _RecordingBackend.lock:
            _RecordingBackend.instances.append(self)

    def start(self, argv, cwd, env, cols, rows) -> None:  # type: ignore[override]
        self.env = dict(env) if env is not None else None
        super().start(argv, cwd, env, cols, rows)

    def write(self, data) -> None:  # type: ignore[override]
        self.written.append(data if isinstance(data, str) else data.decode())
        super().write(data)


def _backend_of(pane_id: str) -> _RecordingBackend:
    found = [b for b in _RecordingBackend.instances
             if b.env is not None and b.env.get("IRONJARVIS_PANE_ID") == pane_id]
    assert found, f"no shell was started for pane {pane_id}"
    return found[-1]


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("IRON_PROXY_DATA_DIR", str(tmp_path / "ipd"))
    monkeypatch.setenv("IRONJARVIS_IRON_PROXY_BUNDLE", str(tmp_path / "no-bundle.mjs"))
    monkeypatch.delenv("IRONJARVIS_IRON_PROXY_NODE", raising=False)
    for k in ("CLAUDE_CONFIG_DIR", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    _RecordingBackend.instances = []
    monkeypatch.setattr("iron_jarvis.terminals.session.default_backend", _RecordingBackend)
    monkeypatch.setattr("iron_jarvis.daemon.routes.terminals._EXIT_SETTLE_S", 0.0)
    monkeypatch.setattr("iron_jarvis.daemon.routes.terminals._EXIT_POLL_S", 0.01)

    async def _no_watch(*_a, **_k):
        await asyncio.Event().wait()

    monkeypatch.setattr("iron_jarvis.iron_proxy.service.watch", _no_watch)
    yield


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
def proj(tmp_path):
    # An Iron Jarvis home with a space AND an apostrophe in it: the handoff
    # path and --add-dir must survive every shell's quoting.
    root = tmp_path / "J's work dir"
    root.mkdir()
    return root


@pytest.fixture
def client(proj):
    with TestClient(create_app(str(proj))) as c:
        yield c


def _session(c, pane_id: str):
    return c.app.state.platform.terminals.get(pane_id)


def _print(c, pane_id: str, *lines: str) -> None:
    _session(c, pane_id)._ingest(("\r\n".join(lines) + "\r\n").encode("utf-8"))


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _assistant(sid: str, cwd: Path, text: str, *, when: datetime | None = None, **extra) -> dict:
    """A real-shaped assistant record (the shape Claude Code 2.1.2xx writes)."""
    rec = {
        "parentUuid": "p", "isSidechain": False, "type": "assistant", "uuid": "u",
        "timestamp": _iso(when or datetime.now(timezone.utc)), "cwd": str(cwd),
        "sessionId": sid, "version": "2.1.289", "userType": "external", "entrypoint": "cli",
        "message": {"id": "m", "model": "<synthetic>" if extra.get("isApiErrorMessage") else "claude-x",
                    "role": "assistant", "type": "message",
                    "content": [{"type": "text", "text": text}]},
    }
    rec.update(extra)
    return rec


def _api_error(sid, cwd, text, *, error, status=None, when=None) -> dict:
    extra = {"error": error, "isApiErrorMessage": True}
    if status is not None:
        extra["apiErrorStatus"] = status
    return _assistant(sid, cwd, text, when=when, **extra)


def _user(text, *, when: datetime | None = None, **extra) -> dict:
    rec = {"type": "user", "timestamp": _iso(when or datetime.now(timezone.utc)),
           "isSidechain": False, "message": {"role": "user", "content": text}}
    rec.update(extra)
    return rec


def _append(path: Path, *records: dict) -> None:
    with open(path, "a", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")


def _write_session(home: str, sid: str, *records: dict) -> Path:
    path = Path(home) / "projects" / FOLDER / f"{sid}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return path


def _continued(c, fake, repo) -> dict:
    """A pane on "Work Max" hits its limit and continues on "Personal" with the
    conversation carried. Returns the ids, the carried copy and the new pane."""
    assert c.post("/iron-proxy/enable").json()["status"]["running"] is True
    work = fake.add_profile("anthropic", "Work Max")
    personal = fake.add_profile("anthropic", "Personal")
    assert c.get("/iron-proxy?discover=0").status_code == 200
    old = c.post("/terminals/launch", json={"cli": "claude", "account": work["id"], "cwd": str(repo)}).json()
    sid = old["claude_session_id"]
    then = datetime.now(timezone.utc) - timedelta(minutes=20)
    _write_session(
        work["cli"]["home"], sid,
        _user("Refactor the invoice exporter so it writes CSV and XLSX.", when=then),
        # The LAST record is fresh: the limit just happened (v1.303.0's rule).
        _assistant(sid, repo, "Starting with the exporter."),
    )
    _print(c, old["id"], "⏺ Working on it.", f"  ⎿  {REAL_SESSION}", "│ > │")
    r = c.post(f"/terminals/{old['id']}/continue-on-next")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["resumed"] is True
    carried = Path(personal["cli"]["home"]) / "projects" / FOLDER / f"{sid}.jsonl"
    assert carried.is_file()
    return {"work": work, "personal": personal, "old": old, "sid": sid,
            "pane": out["pane"], "carried": carried}


def _row(c, pane_id: str) -> dict:
    return {p["id"]: p for p in c.get("/terminals/activity").json()["panes"]}[pane_id]


def _later() -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=2)


# --------------------------------------------------------------------------- #
# continued_from
# --------------------------------------------------------------------------- #
def test_the_continued_pane_records_where_its_conversation_came_from(client, fake, repo):
    """MUTATION: drop ``continued_from`` from the row -> red."""
    c = client
    s = _continued(c, fake, repo)
    cf = s["pane"]["continued_from"]
    assert set(cf) == {"from_id", "from_title", "session_id", "carried_path", "at"}
    assert (cf["from_id"], cf["from_title"], cf["session_id"]) == (s["work"]["id"], "Work Max", s["sid"])
    assert Path(cf["carried_path"]) == s["carried"]
    assert datetime.fromisoformat(cf["at"]) <= datetime.now(timezone.utc)
    # The old pane records nothing; the activity row carries it too.
    assert "continued_from" not in _row(c, s["old"]["id"])
    assert _row(c, s["pane"]["id"])["continued_from"] == cf


def test_continued_from_survives_a_restart(proj, fake, repo, tmp_path):
    """MUTATION: the snapshot drops ``continued_from`` -> red."""
    with TestClient(create_app(str(proj))) as c1:
        s = _continued(c1, fake, repo)
        cf = s["pane"]["continued_from"]
    _RecordingBackend.instances = []
    with TestClient(create_app(str(proj))) as c2:
        row = {p["id"]: p for p in c2.get("/terminals").json()["terminals"]}[s["pane"]["id"]]
        assert row["continued_from"] == cf
        assert row["claude_session_id"] == s["sid"]


# --------------------------------------------------------------------------- #
# Resume failure — the carried FILE
# --------------------------------------------------------------------------- #
AT = "2026-10-03T20:00:00+00:00"
SID = "0b6c2d1e-1111-4222-8333-944455556666"
T_BEFORE = datetime(2026, 10, 3, 19, 59, tzinfo=timezone.utc)
T_AFTER = datetime(2026, 10, 3, 20, 1, tzinfo=timezone.utc)


def _file(tmp_path, *records) -> Path:
    path = tmp_path / f"{SID}.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return path


def test_an_api_error_after_the_handover_is_a_resume_failure(tmp_path):
    """MUTATION: ignore ``isApiErrorMessage`` records -> red."""
    found = check_file(_file(tmp_path, _api_error(SID, tmp_path, BAD_REQUEST, error="invalid_request",
                                                  status=400, when=T_AFTER)), AT)
    assert found is not None and found.kind == "failed"
    assert found.line == BAD_REQUEST and found.when == _iso(T_AFTER)


def test_an_organisation_without_the_model_is_a_failure_not_a_sign_in(tmp_path):
    """Claude Code stamps ``authentication_failed`` on EVERY upstream 403; the
    words decide. MUTATION: read the code as sign-in -> red."""
    found = check_file(_file(tmp_path, _api_error(SID, tmp_path, NO_MODEL, error="authentication_failed",
                                                  when=T_AFTER)), AT)
    assert found is not None and found.kind == "failed"


def test_an_error_from_before_the_handover_is_not_this_accounts_answer(tmp_path):
    """MUTATION: drop the ``at`` floor -> red."""
    assert check_file(_file(tmp_path, _api_error(SID, tmp_path, BAD_REQUEST, error="invalid_request",
                                                 status=400, when=T_BEFORE)), AT) is None


@pytest.mark.parametrize(
    "text,error,status",
    [
        (REAL_SESSION, "rate_limit", 429),  # the next account's own limit: the limit flow
        ("API Error: 429 Rate limited", "unknown_error", 429),  # a 429 by status alone
        (LOST, "server_error", None),  # transient
        ("API Error: 529 overloaded", "unknown_error", 529),  # transient
    ],
)
def test_a_limit_or_a_transient_fault_is_not_a_resume_failure(tmp_path, text, error, status):
    """MUTATION: treat every non-rate_limit error as a failure -> the transient
    rows go red; treat rate_limit as one -> the first goes red."""
    assert check_file(_file(tmp_path, _api_error(SID, tmp_path, text, error=error, status=status,
                                                 when=T_AFTER)), AT) is None


@pytest.mark.parametrize(
    "text,error",
    [
        ("API Error: Request timed out.", "unknown_error"),
        ("API Error: fetch failed", "unknown_error"),
        ("API Error: Request timed out.", ""),
    ],
)
def test_a_fault_with_no_status_and_no_known_words_is_not_a_failure(tmp_path, text, error):
    """Review (3): only a 4xx or words that name the conversation count.
    MUTATION: count any non-transient error -> red."""
    assert check_file(_file(tmp_path, _api_error(SID, tmp_path, text, error=error, when=T_AFTER)), AT) is None


@pytest.mark.parametrize(
    "text",
    [
        "API Error: prompt is too long: 214000 tokens > 200000 maximum",
        "API Error: messages.3.content.0: Invalid `signature` in `thinking` block",
        '{"type":"error","error":{"type":"invalid_request_error","message":"x"}}',
    ],
)
def test_words_that_name_the_conversation_are_a_failure_without_a_status(tmp_path, text):
    """MUTATION: drop the recognised words -> red."""
    found = check_file(_file(tmp_path, _api_error(SID, tmp_path, text, error="unknown_error", when=T_AFTER)), AT)
    assert found is not None and found.kind == "failed"


def test_claude_codes_own_invalid_request_code_is_a_failure(tmp_path):
    """Claude Code files a 400 as ``error: "invalid_request"``. MUTATION: drop
    the code -> red."""
    found = check_file(_file(tmp_path, _api_error(SID, tmp_path, "API Error: Bad request",
                                                  error="invalid_request", when=T_AFTER)), AT)
    assert found is not None and found.kind == "failed"


@pytest.mark.parametrize(
    "text,error,status",
    [
        (LOGIN_EXPIRED, "authentication_failed", None),  # the real record on this PC
        ("Invalid API key · Please run /login", "authentication_failed", None),
        ("API Error: 401 {\"type\":\"error\",\"error\":{\"type\":\"authentication_error\"}}", "unknown_error", 401),
    ],
)
def test_a_sign_in_problem_is_said_as_one(tmp_path, text, error, status):
    """MUTATION: drop the sign-in branch -> these read as resume failures -> red."""
    found = check_file(_file(tmp_path, _api_error(SID, tmp_path, text, error=error, status=status,
                                                  when=T_AFTER)), AT)
    assert found is not None and found.kind == "sign_in", text


def test_a_later_answer_clears_the_failure(tmp_path):
    """MUTATION: keep the first error found -> red."""
    path = _file(
        tmp_path,
        _api_error(SID, tmp_path, BAD_REQUEST, error="invalid_request", status=400, when=T_AFTER),
        _user("try again", when=T_AFTER + timedelta(seconds=5)),
        _assistant(SID, tmp_path, "Picking up where we left off.", when=T_AFTER + timedelta(seconds=9)),
    )
    assert check_file(path, AT) is None


def test_an_answer_that_quotes_error_words_is_not_an_error(tmp_path):
    """Only an ``isApiErrorMessage`` record can raise it: Claude explaining
    "Invalid `signature`" or "Please run /login" in an ANSWER clears it.
    MUTATION: read every record as an API error -> red."""
    path = _file(
        tmp_path,
        _api_error(SID, tmp_path, BAD_REQUEST, error="invalid_request", status=400, when=T_AFTER),
        _assistant(SID, tmp_path, "That 400 was an Invalid `signature` in a thinking block; "
                   "if it says Please run /login, sign in.", when=T_AFTER + timedelta(seconds=9)),
    )
    assert check_file(path, AT) is None


def test_a_sidechain_error_is_not_the_conversations(tmp_path):
    rec = _api_error(SID, tmp_path, BAD_REQUEST, error="invalid_request", status=400, when=T_AFTER)
    rec["isSidechain"] = True
    assert check_file(_file(tmp_path, rec), AT) is None


def test_the_carried_file_is_read_from_a_bounded_tail(tmp_path):
    """An error beyond the tail window is not seen (the read is bounded).
    MUTATION: read the whole file -> red."""
    filler = [_user("x" * 900, when=T_AFTER) for _ in range(40)]
    path = _file(tmp_path, _api_error(SID, tmp_path, BAD_REQUEST, error="invalid_request",
                                      status=400, when=T_AFTER), *filler)
    assert check_file(path, AT, max_bytes=8 * 1024) is None
    assert check_file(path, AT, max_bytes=1024 * 1024) is not None


# --------------------------------------------------------------------------- #
# Resume failure — the SCREEN never raises it (review)
# --------------------------------------------------------------------------- #
#: The review's fixtures: TOOL output under Claude Code's ⎿ gutter that reads
#: like Claude's own errors. A first cut flagged these and would /exit a
#: working Claude.
TOOL_OUTPUT = [
    ("⏺ Bash(curl -s https://api.example.test/v1/items)", "  ⎿  API Error: 400 Bad Request from upstream"),
    ("⏺ Bash(cat auth.log)", "  ⎿  Error: token has expired for client X"),
    ("⏺ Bash(pytest -q)", "  ⎿  AssertionError: expected 'invalid api key'"),
    ("⏺ Read(errors.md)", "  ⎿  Login expired · Please run /login"),
]


@pytest.mark.parametrize("header,line", TOOL_OUTPUT)
def test_tool_output_on_screen_never_raises_a_failure_or_a_sign_in(client, fake, repo, header, line):
    """MUTATION: let the screen raise again -> red."""
    c = client
    s = _continued(c, fake, repo)
    pid = s["pane"]["id"]
    _print(c, pid, header, line, "╭──────╮", "│ >    │", "╰──────╯")
    row = _row(c, pid)
    assert "resume_failed" not in row and "sign_in_needed" not in row


# --------------------------------------------------------------------------- #
# Resume failure — the activity row, end to end
# --------------------------------------------------------------------------- #
def test_the_activity_row_says_the_account_refused_the_conversation(client, fake, repo):
    """The carried copy gains the next account's 400: ``resume_failed: {line,
    since}`` on the activity row (``since`` = the record's own time); a later
    answer clears it. MUTATION: the activity route skips the file check -> red."""
    c = client
    s = _continued(c, fake, repo)
    pid = s["pane"]["id"]
    assert "resume_failed" not in _row(c, pid)
    when = _later()
    _append(s["carried"], _api_error(s["sid"], repo, BAD_REQUEST, error="invalid_request",
                                     status=400, when=when))
    row = _row(c, pid)
    assert row["resume_failed"] == {"line": BAD_REQUEST, "since": _iso(when)}
    assert "sign_in_needed" not in row
    # Pane rows read the cache only (info() never reads the file).
    full = {p["id"]: p for p in c.get("/terminals").json()["terminals"]}[pid]
    assert full["resume_failed"]["line"] == BAD_REQUEST
    _append(s["carried"], _assistant(s["sid"], repo, "Here is the rest.", when=when + timedelta(seconds=3)))
    assert "resume_failed" not in _row(c, pid)


def test_a_rate_limit_on_the_next_account_is_the_limit_flow(client, fake, repo):
    c = client
    s = _continued(c, fake, repo)
    _append(s["carried"], _api_error(s["sid"], repo, REAL_SESSION, error="rate_limit",
                                     status=429, when=_later()))
    _print(c, s["pane"]["id"], "⏺ Resumed.", f"  ⎿  {REAL_SESSION}", "│ > │")
    row = _row(c, s["pane"]["id"])
    assert "resume_failed" not in row and "sign_in_needed" not in row
    assert row["limit"]["line"] == REAL_SESSION  # the ContinueStrip's cue, as before


def test_a_signed_out_account_says_sign_in_and_names_it(client, fake, repo):
    """MUTATION: report a sign-in problem as a resume failure -> red."""
    c = client
    s = _continued(c, fake, repo)
    when = _later()
    _append(s["carried"], _api_error(s["sid"], repo, LOGIN_EXPIRED, error="authentication_failed", when=when))
    row = _row(c, s["pane"]["id"])
    assert "resume_failed" not in row
    assert row["sign_in_needed"] == {
        "line": LOGIN_EXPIRED, "since": _iso(when),
        "account": {"id": s["personal"]["id"], "title": "Personal"},
    }


def test_the_screen_only_clears_while_claude_is_working(client, fake, repo):
    """The file raises; while the pane shows Claude working again nothing is
    reported; idle again with no later answer, it is back. MUTATION: ignore the
    working state -> red."""
    c = client
    s = _continued(c, fake, repo)
    pid = s["pane"]["id"]
    _append(s["carried"], _api_error(s["sid"], repo, BAD_REQUEST, error="invalid_request",
                                     status=400, when=_later()))
    _print(c, pid, "⏺ Earlier answer.", "│ > │")
    assert _row(c, pid)["resume_failed"]["line"] == BAD_REQUEST
    _print(c, pid, "✻ Thinking… (esc to interrupt)")
    assert "resume_failed" not in _row(c, pid)
    _print(c, pid, "  ⎿  Interrupted by user", "⏺ Stopped.", "│ > │", "? for shortcuts")
    assert _row(c, pid)["resume_failed"]["line"] == BAD_REQUEST


def test_a_pane_that_was_not_continued_is_never_watched(client, fake, repo):
    """An ordinary Claude pane printing a 400 is not a resume failure."""
    c = client
    assert c.post("/iron-proxy/enable").status_code == 200
    pane = c.post("/terminals/launch", json={"cli": "claude", "cwd": str(repo)}).json()
    _print(c, pane["id"], "⏺ x", f"  ⎿  {BAD_REQUEST}", "│ > │")
    assert "resume_failed" not in _row(c, pane["id"])


# --------------------------------------------------------------------------- #
# The handoff
# --------------------------------------------------------------------------- #
def _conversation(tmp_path) -> Path:
    sid = SID
    t = datetime(2026, 10, 3, 18, 0, tzinfo=timezone.utc)
    recs = [
        _user("<command-name>/model</command-name>", when=t),  # a slash command echo
        _user("Caveat: meta", when=t, isMeta=True),
        _user("FIRST ASK " + "a" * 2000, when=t),
    ]
    for i in range(10):
        recs.append(_assistant(sid, tmp_path, f"answer {i} " + "b" * 1000, when=t))
        recs.append({
            "type": "assistant", "timestamp": _iso(t), "isSidechain": False,
            "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": f"t{i}", "name": "Edit",
                 "input": {"file_path": f"C:\\proj\\src\\mod{i}.py", "old_string": "x", "new_string": "y"}},
            ]},
        })
        recs.append({
            "type": "user", "timestamp": _iso(t), "isSidechain": False,
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": f"t{i}", "content": f"SECRET-PAYLOAD-{i} the whole file"},
            ]},
        })
        recs.append(_user(f"question {i}", when=t))
    recs.append({"type": "assistant", "timestamp": _iso(t), "isSidechain": False,
                 "message": {"role": "assistant", "content": [
                     {"type": "tool_use", "id": "r", "name": "Read", "input": {"path": "docs/plan.md"}},
                     {"type": "tool_use", "id": "n", "name": "NotebookEdit",
                      "input": {"notebook_path": "nb/analysis.ipynb"}},
                     {"type": "tool_use", "id": "again", "name": "Read",
                      "input": {"file_path": "C:\\proj\\src\\mod3.py"}},
                 ]}})
    recs.append(_api_error(sid, tmp_path, BAD_REQUEST, error="invalid_request", status=400, when=t))
    path = tmp_path / f"{sid}.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in recs), encoding="utf-8")
    return path


def test_the_handoff_holds_the_first_ask_the_last_turns_and_the_files(tmp_path):
    """MUTATION: copy tool_result payloads -> red. MUTATION: drop the turn cap
    -> red. MUTATION: no path list -> red."""
    text = build_handoff(_conversation(tmp_path), "Work Max", when=datetime(2026, 10, 3, 21, 0))
    assert text.startswith("# Handoff from Work Max — 2026-10-03\n")
    assert len(text) <= 8000
    first = text.split("## What was asked first\n\n", 1)[1].split("\n\n## ", 1)[0]
    assert first.startswith("FIRST ASK ") and len(first) <= 1500
    assert "SECRET-PAYLOAD" not in text  # never a tool result
    assert "<command-name>" not in text and "Caveat: meta" not in text
    assert BAD_REQUEST not in text  # the error is not a turn
    turns = re.findall(r"^\*\*(You|Claude):\*\* (.*)$", text, re.M)
    assert len(turns) == 6
    assert [who for who, _ in turns] == ["Claude", "You"] * 3
    assert turns[-1] == ("You", "question 9")
    assert turns[0][1].startswith("answer 7 ")
    for _who, body in turns:
        assert len(body) <= 800
    files = re.findall(r"^- (.+)$", text, re.M)
    assert files[-3:] == ["docs/plan.md", "nb/analysis.ipynb", "C:\\proj\\src\\mod3.py"]
    assert len(files) == len(set(files)) == 12  # each path once, the latest use last


def test_the_handoff_keeps_the_newest_thirty_paths_and_fits_the_total(tmp_path):
    """MUTATION: drop the 8,000 cap -> red. MUTATION: keep the OLDEST paths -> red."""
    t = datetime(2026, 10, 3, 18, 0, tzinfo=timezone.utc)
    recs = [_user("start " + "q" * 3000, when=t)]
    for i in range(45):
        recs.append({"type": "assistant", "timestamp": _iso(t), "isSidechain": False,
                     "message": {"role": "assistant", "content": [
                         {"type": "tool_use", "id": f"w{i}", "name": "Write",
                          "input": {"file_path": f"/very/long/folder/name/that/keeps/going/file_{i:02d}.txt"}}]}})
    for i in range(6):
        recs.append(_user(f"turn {i} " + "u" * 3000, when=t))
        recs.append(_assistant(SID, tmp_path, f"reply {i} " + "r" * 3000, when=t))
    path = tmp_path / "big.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in recs), encoding="utf-8")
    text = build_handoff(path, "Work Max")
    assert len(text) <= handoff_mod.TOTAL_CAP
    files = re.findall(r"^- (.+)$", text, re.M)
    assert len(files) <= 30 and files[-1].endswith("file_44.txt")
    assert all(int(f[-6:-4]) >= 15 for f in files)


def test_secrets_are_masked_in_the_handoff(tmp_path):
    """Review (5). MUTATION: drop the mask -> red."""
    t = datetime(2026, 10, 3, 18, 0, tzinfo=timezone.utc)
    recs = [
        _user("deploy with ANTHROPIC_API_KEY=sk-ant-REALKEY1234567890 please", when=t),
        _assistant(SID, tmp_path, "Using Authorization: Bearer ghp_TOKEN1234567890 now.", when=t),
    ]
    path = tmp_path / "s.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in recs), encoding="utf-8")
    text = build_handoff(path, "Work Max")
    assert "REALKEY1234567890" not in text and "TOKEN1234567890" not in text
    assert "deploy with" in text


def test_old_handoff_folders_are_pruned_when_a_new_one_is_written(tmp_path):
    """Review (5): folders older than 30 days go; recent ones and anything not
    named like a session stay. MUTATION: no prune -> red."""
    import os

    root = tmp_path / "handoffs"
    old = root / "11111111-1111-4111-8111-111111111111"
    recent = root / "22222222-2222-4222-8222-222222222222"
    other = root / "keep me"
    for folder in (old, recent, other):
        folder.mkdir(parents=True)
        (folder / "handoff.md").write_text("x", encoding="utf-8")
    ago = time.time() - 31 * 86400
    os.utime(old / "handoff.md", (ago, ago))
    os.utime(other / "handoff.md", (ago, ago))
    path = handoff_mod.write_handoff("new", root, SID)
    assert path == root / SID / "handoff.md" and path.read_text(encoding="utf-8") == "new"
    assert not old.exists() and recent.is_dir() and other.is_dir()


def test_the_storage_report_lists_handoffs(tmp_path):
    from iron_jarvis.maintenance import storage_report

    (tmp_path / "handoffs" / SID).mkdir(parents=True)
    (tmp_path / "handoffs" / SID / "handoff.md").write_text("abc", encoding="utf-8")
    rows = {r["dir"]: r for r in storage_report(tmp_path)["categories"]}
    assert rows["handoffs"]["bytes"] == 3 and rows["handoffs"]["clearable"] is False


# --------------------------------------------------------------------------- #
# Start fresh with a handoff — end to end
# --------------------------------------------------------------------------- #
PROMPTS = {"pwsh": "PS C:\\Users\\VR\\my repo> ", "cmd": "C:\\Users\\VR\\my repo>", "bash": "$ "}


def _shell_answers_exit(c, pane_id: str, prompt: str) -> threading.Thread:
    """The fake pane: once "/exit" + Enter arrive, Claude exits and the SHELL
    prints its prompt."""
    be, sess = _backend_of(pane_id), _session(c, pane_id)

    def run() -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            w = list(be.written)
            if "/exit" in w and "\r" in w[w.index("/exit"):]:
                sess._ingest(("\r\nResume this session with:\r\nclaude --resume x\r\n" + prompt).encode())
                return
            time.sleep(0.005)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def _pwsh_tokens(line: str) -> list[str]:
    assert line.startswith("& ")
    rest = line[2:]
    tokens = [m.group(1).replace("''", "'") for m in re.finditer(r"'((?:[^']|'')*)'", rest)]
    assert re.sub(r"'((?:[^']|'')*)'", "", rest).strip() == "", line  # nothing unquoted
    return tokens


def _cmd_tokens(line: str) -> list[str]:
    out, i = [], 0
    while i < len(line):
        if line[i] == " ":
            i += 1
            continue
        if line[i] == '"':
            j, buf = i + 1, []
            while True:
                if line[j] == '"' and line[j + 1:j + 2] == '"':
                    buf.append('"')
                    j += 2
                elif line[j] == '"':
                    break
                else:
                    buf.append(line[j])
                    j += 1
            out.append("".join(buf))
            i = j + 1
        else:
            j = line.find(" ", i)
            j = len(line) if j < 0 else j
            out.append(line[i:j])
            i = j
    return out


PARSE = {"pwsh": _pwsh_tokens, "cmd": _cmd_tokens, "bash": shlex.split}


@pytest.mark.parametrize("shell", ["pwsh", "cmd", "bash"])
def test_start_fresh_types_exit_waits_then_starts_claude_on_the_handoff(client, fake, repo, proj, shell):
    """In the SAME pane: Ctrl+C (clears a half-typed draft), ``/exit``, Enter,
    the shell prompt, then ONE line — ``claude --session-id <new>
    --add-dir=<this conversation's folder> "<prompt>"`` — quoted for the pane's
    shell (a home with a space and an apostrophe). MUTATION: no Ctrl+C -> red.
    MUTATION: --add-dir the whole handoffs root -> red. MUTATION: split
    ``--add-dir`` and its folder into two tokens -> red (claude 2.1.289 reads
    the prompt as a second directory). MUTATION: type the line before the
    prompt is back -> red. MUTATION: no quoting -> red."""
    c = client
    s = _continued(c, fake, repo)
    pid = s["pane"]["id"]
    sess = _session(c, pid)
    sess.shell = shell
    _append(s["carried"], _api_error(s["sid"], repo, BAD_REQUEST, error="invalid_request",
                                     status=400, when=_later()))
    _print(c, pid, "⏺ Earlier answer.", f"  ⎿  {BAD_REQUEST}", "│ > │", "? for shortcuts")
    assert "resume_failed" in _row(c, pid)
    # Another conversation's handoff in the same root must stay out of reach.
    other = proj / ".ironjarvis" / "handoffs" / "33333333-3333-4333-8333-333333333333"
    other.mkdir(parents=True)
    (other / "handoff.md").write_text("WORK SECRETS", encoding="utf-8")
    be = _backend_of(pid)
    typed_before = list(be.written)
    _shell_answers_exit(c, pid, PROMPTS[shell])

    r = c.post(f"/terminals/{pid}/start-fresh-with-handoff")
    assert r.status_code == 200, r.text
    out = r.json()
    new_sid = out["session_id"]
    assert re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", new_sid)
    assert new_sid != s["sid"]
    folder = proj / ".ironjarvis" / "handoffs" / s["sid"]
    path = folder / "handoff.md"
    assert Path(out["handoff_path"]) == path and path.is_file()
    assert path.read_text(encoding="utf-8").startswith("# Handoff from Work Max — ")
    assert not list(repo.rglob("*.md"))  # never into the user's project

    typed = be.written[len(typed_before):]
    assert typed[:3] == ["\x03", "/exit", "\r"]
    line = typed[-1]
    assert line.endswith("\r") and typed.count(line) == 1
    tokens = PARSE[shell](line[:-1])
    assert tokens == [
        "claude", "--session-id", new_sid, f"--add-dir={folder}",
        f"Read the handoff at {path} and continue that work from where it stopped.",
    ]
    assert "--add-dir" not in tokens
    # The prompt was back before the line went in.
    assert be.written.index(line) > be.written.index("/exit")

    pane = out["pane"]
    assert pane["claude_session_id"] == new_sid and "resume_failed" not in pane
    assert "resume_failed" not in _row(c, pid)  # the old error stays in the file; not watched now
    assert out["note"].startswith('Started a fresh conversation on "Personal"')
    # A second press has nothing to hand off.
    again = c.post(f"/terminals/{pid}/start-fresh-with-handoff")
    assert again.status_code == 409 and "already left" in again.json()["detail"]


def test_claude_that_does_not_exit_leaves_the_pane_alone_and_says_what_to_type(client, fake, repo, monkeypatch):
    """No shell prompt within the bound: nothing after /exit is typed, the id is
    unchanged, and the sentence names the saved handoff and the line.
    MUTATION: type the line anyway -> red."""
    monkeypatch.setattr("iron_jarvis.daemon.routes.terminals._EXIT_WAIT_S", 0.3)
    c = client
    s = _continued(c, fake, repo)
    pid = s["pane"]["id"]
    _session(c, pid).shell = "pwsh"
    be = _backend_of(pid)
    before = list(be.written)
    r = c.post(f"/terminals/{pid}/start-fresh-with-handoff")
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail.startswith("Could not see the shell prompt in this pane")
    assert "if Claude has exited, run: " in detail and "--add-dir=" in detail
    assert str(Path(s["sid"]) / "handoff.md") in detail
    typed = be.written[len(before):]
    assert typed[:2] == ["\x03", "/exit"] and all(t in ("\x03", "/exit", "\r") for t in typed)
    assert _session(c, pid).claude_session_id == s["sid"]


@pytest.mark.parametrize(
    "screen,words",
    [
        (("✻ Thinking… (esc to interrupt)",), "Claude is still answering — press Esc, then try again."),
        (("Do you want to proceed?", "❯ 1. Yes", "  2. No"), "Claude is waiting for an answer in this pane"),
    ],
)
def test_a_busy_claude_is_never_exited(client, fake, repo, screen, words):
    """Review (6). MUTATION: drop the busy refusal -> red."""
    c = client
    s = _continued(c, fake, repo)
    pid = s["pane"]["id"]
    _print(c, pid, *screen)
    be = _backend_of(pid)
    before = list(be.written)
    r = c.post(f"/terminals/{pid}/start-fresh-with-handoff")
    assert r.status_code == 409 and r.json()["detail"].startswith(words)
    assert be.written == before


def test_a_pane_not_opened_by_continue_is_refused(client, fake, repo):
    """MUTATION: drop the refusal -> red."""
    c = client
    assert c.post("/iron-proxy/enable").status_code == 200
    pane = c.post("/terminals/launch", json={"cli": "claude", "cwd": str(repo)}).json()
    be = _backend_of(pane["id"])
    before = list(be.written)
    r = c.post(f"/terminals/{pane['id']}/start-fresh-with-handoff")
    assert r.status_code == 409
    assert r.json()["detail"].startswith("This pane was not opened by Continue on the next account")
    assert be.written == before
    assert c.post("/terminals/nope/start-fresh-with-handoff").status_code == 404


def test_a_continued_pane_with_nothing_carried_is_refused(client, fake, repo):
    """Continue started fresh (no conversation found): nothing to hand off."""
    c = client
    assert c.post("/iron-proxy/enable").status_code == 200
    work = fake.add_profile("anthropic", "Work Max")
    fake.add_profile("anthropic", "Personal")
    assert c.get("/iron-proxy?discover=0").status_code == 200
    old = c.post("/terminals/launch", json={"cli": "claude", "account": work["id"], "cwd": str(repo)}).json()
    _print(c, old["id"], "⏺ x", f"  ⎿  {REAL_SESSION}", "│ > │")
    out = c.post(f"/terminals/{old['id']}/continue-on-next").json()
    assert out["resumed"] is False
    cf = out["pane"]["continued_from"]
    assert cf["carried_path"] is None and cf["session_id"] == out["pane"]["claude_session_id"]
    r = c.post(f"/terminals/{out['pane']['id']}/start-fresh-with-handoff")
    assert r.status_code == 409 and r.json()["detail"].startswith("No conversation was carried")


BS = chr(92)


@pytest.mark.parametrize(
    "tail,shell,expected",
    [
        (f"Bye!\nPS C:{BS}Users{BS}VR{BS}my repo> ", "pwsh", True),
        (f"Bye!\nPS C:{BS}Users{BS}VR{BS}my repo> ", "powershell", True),
        # MEASURED LIVE (claude 2.1.289, ConPTY): the prompt lands glued to the
        # leftovers of the row it overwrites, with SI bytes between.
        (f"uto mode on (shift+tab to cycle)\x0f\x0fPS C:{BS}Users{BS}VR{BS}x>", "pwsh", True),
        ("╰──────╯\n? for shortcuts", "pwsh", False),
        ("│ > │", "pwsh", False),
        ("user@host MINGW64 ~/my repo\n$ ", "bash", True),
        ("vr@box:~/my repo$ ", "bash", True),
        ("bash-5.2$ ", "bash", True),
        ("$ ", "bash", True),  # the SI bytes Claude Code leaves on exit
        ("vr@box ~ % ", "zsh", True),
        ("Context left until auto-compact: 12%", "bash", False),
        ("  ⎿  echo the total is 5$", "bash", False),
        ("Price: US$", "bash", False),
        (f"PS C:{BS}x> ", "bash", False),
    ],
)
def test_the_shell_prompt_is_read_for_the_panes_shell(tail, shell, expected):
    """Review (7): POSIX is ANCHORED. MUTATION: any line ending in $ -> red.
    MUTATION: anchor the PowerShell prompt at line start -> the live glued
    line goes red."""
    assert handoff_mod.at_shell_prompt(tail, shell) is expected


def test_the_tail_strips_the_modes_claude_code_resets_on_exit(client):
    """MEASURED LIVE: Claude Code leaves with ESC[>4m / ESC[<u / ESC 7 / ESC 8;
    the tail's ANSI strip must remove them whole, or ">4m<u" is glued in front
    of the prompt. MUTATION: drop the private CSI parameters -> red."""
    r = client.post("/terminals", json={})
    sess = _session(client, r.json()["id"])
    sess._ingest(b"\x1b[?u\x1b[>4m\x1b[<u\x1b7\x1b8PS C:\\x> ")
    assert sess.output_tail().endswith("PS C:\\x> ")
    assert ">4m" not in sess.output_tail() and "<u" not in sess.output_tail()


def test_the_cmd_prompt_is_read():
    bs = chr(92)
    assert handoff_mod.at_shell_prompt(f"C:{bs}Users{bs}VR>", "cmd") is True
    assert handoff_mod.at_shell_prompt("? for shortcuts", "cmd") is False
