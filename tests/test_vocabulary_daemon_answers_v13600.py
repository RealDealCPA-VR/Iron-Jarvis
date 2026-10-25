"""v1.360.0 lane 10C (3), G76 — ONE vocabulary in everything Iron Jarvis ANSWERS.

The v1.358.0 scan (tests/test_vocabulary_beyond_dashboard_v13580.py) read a
FIXED list of daemon files, so sentences outside it still reached people in
retired words: the Status map's "N of M tool packs running" and "N open
panes", an extension's failure reason "the pack's server couldn't be
reached" with a fix pointing at "Tools → Connected packs", the queued-task
refusals ("no assignment …", "assign the job to a worker agent instead"), the
objective retry refusals ("This mission is still running"), "no such
connector", "check the daemon logs" and more.

This is the same rule (the SAME retired-word list, parsed from
dashboard/__tests__/helpers/retiredWords.ts by the v13580 module, never
copied), widened two ways so a NEW sentence is read the day it lands:

* FULL READ — every sentence in every ``daemon/routes/*.py`` module (a glob,
  not a list: a new route module is read without anyone adding it here), the
  extension failure words (``mcp/tools.py``), the import door
  (``mcp/import_config.py``, ``mcp/import_sources.py``, ``mcp/pack_door.py``),
  the request models' 422 sentences (``daemon/schemas.py``), the Directory's
  card lines (``connectors/service.py``) and the queue (``assignments/``).
  Docstrings, log calls, dict keys, comparisons and code-shaped tokens are not
  read (the v13580 reader's rules).
* ANSWER POSITIONS — everywhere else in ``src/iron_jarvis``: the message of an
  exception (``HTTPException(detail=…)``, ``ValueError("…")`` and the like,
  which routes hand back as a 4xx sentence) and any value under an answer key
  (``detail``, ``reason``, ``fix``, ``note``, ``error``, ``summary``, ``title``,
  ``message``, ``hint``, ``words``, ``held_reason``, ``blocked_reason``,
  ``why``) in a dict or a call. (Review, v1.360.0: also the message of ANY
  raised call whatever its class (``PaneAccountRefused("…")``), a value
  written under an answer key (``out["why"] = "…"``), a variable named for
  one (``detail = "…"``) and a module sentence named ``*_MESSAGE`` /
  ``*_REASON`` / … (``PANE_TOKEN_EXPIRED_MESSAGE = "…"``): the first cut read
  none of those forms and missed real answers in them.)

``ALLOW`` lists deliberate uses, each with a reason. ``PENDING`` lists lines in
files lane 10C does not own this wave: the integrator rewords them from lane
10C's notes and deletes each entry with its fix. An entry of either list that
no longer matches anything fails, so a fixed line takes its entry with it.

The behaviour pins at the bottom drive the REAL code (the app factory, the
classifier, the routes) and walk whole answers, so a sentence built at run
time (an f-string the static reader sees in pieces) is checked as a person
reads it.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from tests.test_vocabulary_beyond_dashboard_v13580 import RETIRED, _code_shaped, _hits, py_user_strings

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "iron_jarvis"

FULL_READ: tuple[Path, ...] = (
    *sorted((SRC / "daemon" / "routes").glob("*.py")),
    SRC / "mcp" / "tools.py",
    SRC / "mcp" / "import_config.py",
    SRC / "mcp" / "import_sources.py",
    SRC / "mcp" / "pack_door.py",
    SRC / "daemon" / "schemas.py",
    SRC / "connectors" / "service.py",
    *sorted((SRC / "assignments").glob("*.py")),
    # Readiness 12A: the files that now hold person-facing sentences of the
    # wave — a failed run's plain words (the run page, Schedules, the bell,
    # the phone), the one Sign in sentence, the card / work-line / run-log
    # words every built-in reads in, Jarvis's own health answers and a
    # stopped scheduled workflow's row — read in full, not only at answer
    # positions, so a retired word in a new sentence fails the day it lands.
    SRC / "providers" / "failure_words.py",
    SRC / "providers" / "cli_auth.py",
    SRC / "agents" / "mission.py",
    SRC / "tools" / "selfcare.py",
    SRC / "scheduling" / "run_outcome.py",
)

#: Keys whose value is something a person reads.
ANSWER_KEYS = frozenset(
    {
        "detail",
        "reason",
        "fix",
        "note",
        "error",
        "summary",
        "title",
        "message",
        "hint",
        "words",
        "held_reason",
        "blocked_reason",
        "why",
    }
)

#: Exceptions whose message becomes an answer (a route hands ``str(exc)`` back).
ANSWER_EXCEPTIONS = frozenset(
    {
        "HTTPException",
        "ValueError",
        "RuntimeError",
        "PermissionError",
        "FileNotFoundError",
        "LookupError",
        "KeyError",
        "TypeError",
        "ProviderError",
        "MCPError",
        "EmergencyStopOn",
        "ChatTurnFailed",
    }
)

_OWNED_BY_10C = (
    "src/iron_jarvis/daemon/routes/",
    "src/iron_jarvis/mcp/tools.py",
    "src/iron_jarvis/mcp/import_config.py",
    "src/iron_jarvis/mcp/import_sources.py",
    "src/iron_jarvis/assignments/",
)
#: routes/chat.py is lane 10B's file this wave.
_NOT_10C = ("src/iron_jarvis/daemon/routes/chat.py",)

_MODEL = "the model reads it, never a person: VOCABULARY.md's three-layer rule migrates model text deliberately"
_VAULT = (
    "a vault row's stored description; lib/secretLabels reads it as a marker and shows "
    "'<App>: <field label>' on the Secrets page, so the words are never shown as written"
)
_FILE = "a real file name (VOCABULARY.md: a real file name is a file name)"

#: Deliberate uses: (file, a piece of the text, why).
ALLOW: list[tuple[str, str, str]] = [
    ("src/iron_jarvis/daemon/routes/agents.py", "MCP pack ", _VAULT),
    ("src/iron_jarvis/mcp/pack_door.py", "MCP pack ", _VAULT),
    ("src/iron_jarvis/daemon/routes/terminals.py", "embedded in a dashboard shell pane", _MODEL + " (the terminal assist's prompt; pinned by test_build_context_v1280)"),
    ("src/iron_jarvis/daemon/routes/terminals.py", "This pane is ", _MODEL + " (the terminal assist's prompt; pinned by test_build_context_v1280)"),
    ("src/iron_jarvis/daemon/routes/chat.py", "Connector tools the user toggled on", _MODEL + " (the chat's tools section)"),
    ("src/iron_jarvis/onboarding/doctor.py", "check daemon.log for the boot error", _FILE),
    ("src/iron_jarvis/onboarding/doctor.py", "daemon.log has the error", _FILE),
    # Readiness 12A: tools/selfcare.py is read in full now; its tools'
    # descriptions and argument help are what the MODEL reads (the tool names
    # pack_status / pack_restart are the wire).
    ("src/iron_jarvis/tools/selfcare.py", "How Iron Jarvis itself is doing, in plain words plus data", _MODEL + " (app_health's description)"),
    ("src/iron_jarvis/tools/selfcare.py", "The state of one tool pack (an MCP server)", _MODEL + " (pack_status's description)"),
    ("src/iron_jarvis/tools/selfcare.py", "The pack's name as listed under Settings > Tools.", _MODEL + " (the pack argument's help)"),
    ("src/iron_jarvis/tools/selfcare.py", "Restart ONE tool pack (an MCP server)", _MODEL + " (pack_restart's description)"),
    ("src/iron_jarvis/tools/selfcare.py", "Run the app's runtime checks (a model is connected", _MODEL + " (run_doctor's description)"),
]

_P = "not lane 10C's file this wave: the integrator rewords it from lane 10C's notes and deletes this entry"

#: Lines in files lane 10C does not own: (file, a piece of the text, why).
#: v1.360.0 integrate: EMPTY — every line lane 10C parked here (60 of them,
#: in 31 files) was reworded by the integrator from lane 10C's notes, and
#: the pins that held the old words moved on purpose. The contract stays: a
#: line a later lane cannot reword is parked here with `_P` and a reason,
#: and a stale entry fails `test_every_allow_and_pending_entry_still_matches_something`.
PENDING: list[tuple[str, str, str]] = []


# --------------------------------------------------------------------------- #
# The readers
# --------------------------------------------------------------------------- #


def _strings_under(node: ast.AST) -> list[tuple[int, str]]:
    return [
        (s.lineno, s.value)
        for s in ast.walk(node)
        if isinstance(s, ast.Constant) and isinstance(s.value, str)
    ]


def _call_name(node: ast.Call) -> str:
    fn = node.func
    if isinstance(fn, ast.Name):
        return fn.id
    if isinstance(fn, ast.Attribute):
        return fn.attr
    return ""


#: A name that holds a sentence handed back as an answer
#: (``PANE_TOKEN_EXPIRED_MESSAGE``, ``held_reason``, ``out_note``).
_ANSWER_SUFFIXES = ("_message", "_reason", "_detail", "_note", "_error", "_hint", "_fix")


def _answer_target(target: ast.AST) -> bool:
    """An assignment target that holds an answer: ``out["why"]`` (a dict
    entry under an answer key), ``detail`` / ``self.reason`` (named for one),
    or ``NO_TOKEN_MESSAGE`` (a module sentence)."""
    if isinstance(target, ast.Subscript):
        key = target.slice
        return isinstance(key, ast.Constant) and key.value in ANSWER_KEYS
    if isinstance(target, ast.Name):
        name = target.id
    elif isinstance(target, ast.Attribute):
        name = target.attr
    else:
        return False
    low = name.lower()
    return low in ANSWER_KEYS or low.endswith(_ANSWER_SUFFIXES)


def answer_strings(src: str) -> list[tuple[int, str]]:
    """Every sentence in an ANSWER position: an answer exception's message,
    the message of ANY raised call, any value under an answer key (a dict
    entry, a keyword argument or a ``d["key"] = …`` write) and any value
    assigned to a name that holds an answer. Code-shaped tokens are not
    sentences."""
    tree = ast.parse(src)
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
            # Whatever its class: a refusal type the list does not name
            # (PaneAccountRefused, ConfigWriteRefused) is still an answer.
            for arg in node.exc.args:
                found += _strings_under(arg)
            for kw in node.exc.keywords:
                if kw.arg in ("detail", "message"):
                    found += _strings_under(kw.value)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if node.value is not None and any(_answer_target(t) for t in targets):
                found += _strings_under(node.value)
        if isinstance(node, ast.Call):
            name = _call_name(node)
            if name in ANSWER_EXCEPTIONS:
                for arg in node.args:
                    found += _strings_under(arg)
                for kw in node.keywords:
                    if kw.arg in ("detail", "message"):
                        found += _strings_under(kw.value)
            else:
                for kw in node.keywords:
                    if kw.arg in ANSWER_KEYS:
                        found += _strings_under(kw.value)
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value in ANSWER_KEYS:
                    found += _strings_under(value)
    return [(line, text) for line, text in found if text.strip() and not _code_shaped(text)]


def _rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


#: v1.383.0: the harness benchmark's task data — standalone projects handed to
#: a coding agent and their hidden checks — speaks the PROJECT's words ("nothing
#: to pack" in a shipping package), never Iron Jarvis's. Only that subtree.
TASK_DATA = SRC / "harness_bench" / "tasks"


def _all_hits() -> list[tuple[str, int, str, str]]:
    hits: set[tuple[str, int, str, str]] = set()
    full = {p.resolve() for p in FULL_READ}
    for path in sorted(SRC.rglob("*.py")):
        if TASK_DATA in path.parents:
            continue
        src = path.read_text(encoding="utf-8")
        reader = py_user_strings if path.resolve() in full else answer_strings
        for line, text in reader(src):
            for word in _hits(text):
                hits.add((_rel(path), line, word, text))
    return sorted(hits)


def _listed(hit, entries) -> bool:
    return any(hit[0] == f and piece in hit[3] for f, piece, _why in entries)


# --------------------------------------------------------------------------- #
# The scan
# --------------------------------------------------------------------------- #


def test_only_the_benchmarks_task_data_is_left_out():
    """The skip is that one subtree: the benchmark's own reports and refusals
    (``harness_bench/*.py``) are still read like every other answer."""
    assert TASK_DATA.is_dir()
    bench_own = sorted((SRC / "harness_bench").glob("*.py"))
    assert bench_own and all(TASK_DATA not in p.parents for p in bench_own)
    assert any(TASK_DATA in p.parents for p in SRC.rglob("*.py"))


def test_no_retired_word_in_anything_iron_jarvis_answers():
    bad = [h for h in _all_hits() if not _listed(h, ALLOW) and not _listed(h, PENDING)]
    assert not bad, "retired words a person reads in an answer:\n" + "\n".join(
        f"  {f}:{line} [{word}] {text[:120]!r}" for f, line, word, text in bad
    )


def test_every_allow_and_pending_entry_still_matches_something():
    hits = _all_hits()
    stale = [e for e in ALLOW + PENDING if not any(h[0] == e[0] and e[1] in h[3] for h in hits)]
    assert not stale, f"stale entries (the line was fixed: delete its entry): {stale}"


def test_pending_never_hides_a_file_this_lane_owns_and_every_entry_says_why():
    for f, _piece, why in PENDING:
        owned = f.startswith(_OWNED_BY_10C) and f not in _NOT_10C
        assert not owned, f"{f} is lane 10C's own file: fix the words, do not park them"
        assert why.strip()
    for _f, _piece, why in ALLOW:
        assert why.strip()


def test_the_scan_reads_every_route_module_by_glob():
    routes = {p.name for p in FULL_READ if p.parent.name == "routes"}
    on_disk = {p.name for p in (SRC / "daemon" / "routes").glob("*.py")}
    assert routes == on_disk and "system_map.py" in routes and "assignments.py" in routes


def test_the_readers_reach_the_sentences_they_exist_for():
    """Anti-vacuity: each reader finds the lines it exists for (as words)."""
    def texts(path: Path, reader) -> list[str]:
        return [t for _l, t in reader(path.read_text(encoding="utf-8"))]

    system_map = texts(SRC / "daemon" / "routes" / "system_map.py", py_user_strings)
    assert any(" open terminal" in t for t in system_map), system_map
    assert any(" extension" in t for t in system_map)
    tools = texts(SRC / "mcp" / "tools.py", py_user_strings)
    assert "the extension's server couldn't be reached" in tools
    queue = texts(SRC / "assignments" / "store.py", py_user_strings)
    assert any("a queued task needs an assignee" in t for t in queue)
    # The answer reader, on a file outside the full set.
    updates = texts(SRC / "core" / "updates.py", answer_strings)
    assert any("restart" in t for t in updates)
    # (integrate) PENDING may be empty; the ALLOW lines are real hits, so the
    # scan reading nothing still fails here.
    assert _all_hits() and len(_all_hits()) >= len(ALLOW + PENDING), "the scan read nothing"


def test_the_answer_reader_on_planted_text():
    planted = '''"""The daemon module docstring."""
import logging
from fastapi import HTTPException
log = logging.getLogger(__name__)
PROMPT = "You run in the daemon pane."
def f(x):
    """The daemon helper docstring."""
    log.warning("the daemon failed %s", x)
    if x == "Daemon thing":
        raise HTTPException(status_code=404, detail="no such pack")
    if x:
        raise ValueError(f"the job {x} is gone")
    out = {"reason": "the mission stopped", "label": "Mission"}
    return Result(ok=False, error="the connector failed", output="the pane said hi")
'''
    found = sorted({(line, word) for line, text in answer_strings(planted) for word in _hits(text)})
    # detail, an exception message, a dict answer key, an error= keyword.
    assert found == [(10, "pack"), (12, "job"), (13, "mission"), (14, "connector")], found


def test_the_answer_reader_reads_written_answers_and_any_raised_refusal():
    """Review (v1.360.0): an answer is also WRITTEN — a module sentence named
    ``*_MESSAGE``, ``out["why"] = …``, ``detail = …`` — or RAISED through a
    refusal class the exception list does not name. The first reader missed
    all four (16 real lines across the daemon). Both directions: a prompt
    constant and a non-answer key stay unread."""
    planted = '''NO_TOKEN_MESSAGE = "start it from a Build pane"
PROMPT_TEXT = "the daemon pane prompt"
def g(out, x):
    out["why"] = "the job is gone"
    out["label"] = "the mission label"
    detail = "the daemon is shutting down"
    words = "Nothing" if x else "the brain"
    raise PaneRefused("the connector refused")
'''
    found = sorted({(line, word) for line, text in answer_strings(planted) for word in _hits(text)})
    assert found == [(1, "pane"), (4, "job"), (6, "daemon"), (7, "brain"), (8, "connector")], found


def test_the_retired_list_is_the_one_the_dashboard_uses():
    words = [w for w, _rx in RETIRED]
    for w in ("pack", "pane", "daemon", "mission", "job", "assignment", "connector", "brain", "mock"):
        assert w in words


# --------------------------------------------------------------------------- #
# Behaviour, through the real code, read as a person reads it
# --------------------------------------------------------------------------- #


def _retired_in(obj) -> list[str]:
    """Every retired word in every string of a JSON answer (keys skipped)."""
    out: list[str] = []

    def walk(v):
        if isinstance(v, str):
            if not _code_shaped(v):
                out.extend(f"{w}: {v[:100]!r}" for w in _hits(v))
        elif isinstance(v, dict):
            for item in v.values():
                walk(item)
        elif isinstance(v, (list, tuple)):
            for item in v:
                walk(item)

    walk(obj)
    return out


@pytest.fixture()
def client(tmp_path):
    from fastapi.testclient import TestClient

    from iron_jarvis.daemon.app import create_app

    app = create_app(str(tmp_path))
    with TestClient(app) as c:
        yield c


def test_the_status_map_says_terminals_and_extensions(client):
    res = client.get("/ui/system-map")
    assert res.status_code == 200, res.text
    body = res.json()
    stars = {s["key"]: s for area in body["areas"] for s in area["nodes"]}
    assert stars["build"]["unit"] == "terminal"
    assert re.match(r"^\d+ open terminals?; a terminal counts as in use while its program works\.$", stars["build"]["detail"])
    assert stars["tools"]["unit"] == "extension"
    assert re.match(r"^\d+ of \d+ extensions? running", stars["tools"]["detail"]), stars["tools"]["detail"]
    # The WHOLE map, every string a person can read on it.
    assert not _retired_in(body), _retired_in(body)


@pytest.mark.parametrize(
    "error, reason_starts",
    [
        ("MCPServerStopped: the server closed the connection (exit code 1)", "the extension's server exited"),
        ("TimeoutError: did not respond within 15s", "the extension didn't answer in time"),
        ("PermissionError: [WinError 5] Access is denied", "the extension's launcher was refused"),
        ("HTTPStatusError: 401 Unauthorized", "the extension refused our credentials"),
        ("ConnectError: connection refused", "the extension's server couldn't be reached"),
        ("FileNotFoundError: no such file C:/x/server.js", "a file the extension needs to start wasn't found"),
        ("npm error code E404 | npm error 404 '@x/nope@*' is not in this registry.", "the package @x/nope is not in its registry, so the extension's name"),
    ],
)
def test_every_extension_failure_reason_and_fix_reads_in_the_canons_words(error, reason_starts):
    from iron_jarvis.mcp.tools import classify_load_failure

    words = classify_load_failure({"command": ""}, error)
    assert words["reason"].startswith(reason_starts), words
    assert not _hits(words["reason"]) and not _hits(words["fix"]), words
    if "Connected" in words["fix"]:
        assert "Tools → Connected extensions" in words["fix"], words["fix"]


def test_the_extension_still_starting_note_says_extension():
    from iron_jarvis.mcp.tools import packs_starting_note

    note = packs_starting_note(["github"])
    assert note.startswith("These extensions are still starting") and "say the extension is still starting" in note
    assert not _hits(note), note


def test_the_queue_answers_in_the_canons_task(client):
    r = client.post("/assignments/asg_nope/cancel")
    assert r.status_code == 404 and r.json()["detail"] == "no queued task 'asg_nope'"
    r = client.post("/assignments", json={"assignee": "supervisor", "task": "do it"})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "the supervisor cannot take a queued task" in detail and "give the task to a worker agent" in detail
    for r in (client.post("/assignments", json={"assignee": "", "task": "x"}),):
        assert not _retired_in(r.json()), r.json()
    # A queued row refuses an unblock by its status, in the canon's words.
    made = client.post("/assignments", json={"assignee": "builder", "task": "tidy the folder"})
    assert made.status_code in (200, 201), made.text
    aid = (made.json().get("assignment") or made.json())["id"]
    r = client.post(f"/assignments/{aid}/unblock")
    assert r.status_code == 409, r.text
    assert r.json()["detail"].startswith(f"cannot unblock task '{aid}'") and "only a blocked task can be unblocked" in r.json()["detail"]
    assert not _retired_in(r.json())


def test_the_queue_store_refuses_in_the_canons_words(tmp_path):
    from iron_jarvis.assignments.dispatcher import HELD_BUSY, HELD_GONE

    for text in (HELD_BUSY, HELD_GONE):
        assert not _hits(text), text
    assert HELD_BUSY.format(name="builder") == "builder is busy with another queued task"


def test_a_missing_connection_and_a_rename_refusal_say_connection_and_extension(client):
    for r in (client.delete("/connectors/no-such-app-v13600"), client.post("/connectors/no-such-app-v13600/test")):
        assert r.status_code == 404, r.text
        assert r.json()["detail"] == "no such connection"
    platform = client.app.state.platform
    platform.config.mcp_servers = [{"name": "ledger", "command": "ij-no-such-launcher-v13600", "args": []}]
    r = client.patch("/mcp/servers/ledger", json={"name": "books"})
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == "the name cannot change; remove the extension and add it again under the new name"
    r = client.post("/mcp/import/apply", json={"text": "npx -y @x/y", "names": []})
    assert r.status_code == 400 and r.json()["detail"] == "choose at least one extension to add"


def test_the_auto_approve_note_says_extension_not_plug_in(client):
    """routes/agents.py's run-without-asking note (the audit's 'plug-in
    tools'). "plug-in" is retired in VOCABULARY.md but not on the scanner's
    list, so this sentence is pinned by hand."""
    for on in (True, False):
        r = client.patch("/mcp/settings", json={"auto_approve": on})
        assert r.status_code == 200, r.text
        note = r.json()["note"]
        assert "plug-in" not in note and "extension" in note, note
        assert not _hits(note)


def test_an_objective_that_is_not_one_is_not_found_in_the_canons_word(client):
    r = client.post("/missions/no-such-session/retry-failed")
    assert r.status_code == 404 and r.json()["detail"] == "objective not found"


def test_the_v13580_scan_still_reads_its_own_files():
    """The successor widens the old scan; it never replaces it."""
    from tests import test_vocabulary_beyond_dashboard_v13580 as old

    assert old.PY_FILES and all(p.is_file() for p in old.PY_FILES)
    assert json.dumps([w for w, _ in old.RETIRED]) == json.dumps([w for w, _ in RETIRED])
