"""v1.358.0 lane 8E (G76) — ONE vocabulary beyond the dashboard.

The dashboard scan (dashboard/__tests__/vocabulary.test.ts) reads only
dashboard/app, components and lib. The re-audit (v1.357.0) found the words
Iron Jarvis and its desktop shell write THEMSELVES outside it: "the daemon
log has the details" (scheduling/run_outcome.py), "the daemon keeps
stalling" and "Iron Jarvis — daemon did not start" (desktop/main.js), and the
G76 acceptance "fails on any new user-visible daemon/pack" was not true end
to end.

This is the same rule on the other side, reading the SAME retired-word list
(dashboard/__tests__/helpers/retiredWords.ts, parsed — never copied):

* desktop/main.js — every string literal a person can read (tooltips,
  notifications, dialogs, menu labels, the startup reasons under "Details:").
  Log calls (desktopLog, fileLogger, console, reportIncident) and code-shaped
  tokens (no space, no capital: "daemon", "pane:") are skipped, like the
  dashboard scan skips ids. A `${label}` piece is invisible to a static read,
  so the crash-loop words are ALSO driven under node with the code's own
  label (tests/test_desktop_words_v13580.py).
* settings/schema.py — every label, help line, option label, section, group
  name/description and placeholder; search ALIASES are kept on purpose (the
  canon keeps retired words searchable) and are not read.
* scheduling/run_outcome.py, comm/notifier.py and agents/task_door.py — every
  sentence they hold (docstrings, log calls, dict keys and comparisons are
  not read).

ALLOW is the short list of deliberate uses, each with a reason; an entry that
no longer matches anything fails (a fixed line takes its entry with it).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RETIRED_TS = ROOT / "dashboard" / "__tests__" / "helpers" / "retiredWords.ts"
MAIN_JS = ROOT / "desktop" / "main.js"
SRC = ROOT / "src" / "iron_jarvis"
PY_FILES = (
    SRC / "scheduling" / "run_outcome.py",
    SRC / "comm" / "notifier.py",
    SRC / "agents" / "task_door.py",
    # v1.358.0 (integrate): the reworded 503s ("… is not available right
    # now. Restart Iron Jarvis …", never "this daemon"), the Pixio key
    # sentences and the parked-workflow store. v1.360.0 (lane 10C): every
    # route module (routes/assignments.py included, its refusals now say
    # "task"), the extension failure words, the import door, the queue and
    # every ANSWER position in the daemon are read by the successor,
    # tests/test_vocabulary_daemon_answers_v13600.py.
    SRC / "scheduling" / "parked_runs.py",
    SRC / "daemon" / "routes" / "coach.py",
    SRC / "daemon" / "routes" / "grants.py",
    SRC / "daemon" / "routes" / "skills_curator.py",
    SRC / "daemon" / "routes" / "profile_share.py",
    SRC / "media" / "pixio_http.py",
)

#: Calls whose arguments are never shown to a person.
LOG_CALLS = frozenset(
    {
        "desktopLog",
        "fileLogger",
        "console.log",
        "console.error",
        "console.warn",
        "console.info",
        "reportIncident",
        "require",
        "process.stdout.write",
        "process.stderr.write",
    }
)

#: Deliberate uses: (file, a piece of the text, why). Empty is the goal.
ALLOW: list[tuple[str, str, str]] = [
    # v1.406.0: "Brain profile" is the feature's NAME, the user's own term
    # (the dashboard's vocabulary test allows the same label); every sentence
    # around it says "memory base".
    ("src/iron_jarvis/settings/schema.py", "Brain profile",
     "the feature's proper name, the user's own term"),
]


def _retired() -> list[tuple[str, re.Pattern[str]]]:
    """VOCABULARY.md's retired words, read from the dashboard scan's own list."""
    src = RETIRED_TS.read_text(encoding="utf-8").replace("\r\n", "\n")
    rows = re.findall(r'\{ word: "([^"]+)", re: /(.+?)/i, use:', src)
    return [(word, re.compile(rx, re.I)) for word, rx in rows]


RETIRED = _retired()


def _hits(text: str) -> list[str]:
    return [word for word, rx in RETIRED if rx.search(text)]


def _code_shaped(text: str) -> bool:
    """No whitespace and no capital: an id, a key, a route, a label."""
    return not re.search(r"\s", text) and not re.search(r"[A-Z]", text)


# --------------------------------------------------------------------------- #
# The JavaScript reader (desktop/main.js)
# --------------------------------------------------------------------------- #


def js_strings(src: str) -> list[tuple[int, str, tuple[str, ...]]]:
    """Every string-literal piece of a JS source with its line and the calls
    it sits inside (innermost last). Comments and regex literals are skipped;
    a template is split into its fixed pieces and its `${…}` code is read as
    code (so a literal inside one is found too)."""
    out: list[tuple[int, str, tuple[str, ...]]] = []
    calls: list[str] = []  # callee per open "(", "" for a plain paren
    parens: list[int] = []  # depth marker per template ${ (brace depth)
    tpl_stack: list[int] = []
    brace = 0
    last_closed = ""
    prev = ""  # last significant char
    i, n = 0, len(src)

    def callee_before(pos: int) -> str:
        j = pos - 1
        while j >= 0 and src[j] in " \t":
            j -= 1
        if j >= 0 and src[j] == ")":
            return last_closed
        k = j
        while k >= 0 and (src[k].isalnum() or src[k] in "_$."):
            k -= 1
        name = src[k + 1 : j + 1]
        return name if name and not name[0].isdigit() else ""

    def read_template(start: int) -> int:
        """Read template text from `start` (just after ` or }) to the next `
        or ${. Returns the index after the stop."""
        nonlocal brace
        j = start
        buf: list[str] = []
        while j < n:
            c = src[j]
            if c == "\\":
                buf.append(src[j : j + 2])
                j += 2
                continue
            if c == "`":
                out.append((src.count("\n", 0, start) + 1, "".join(buf), tuple(calls)))
                return j + 1
            if c == "$" and j + 1 < n and src[j + 1] == "{":
                out.append((src.count("\n", 0, start) + 1, "".join(buf), tuple(calls)))
                tpl_stack.append(brace)
                brace += 1
                return j + 2
            buf.append(c)
            j += 1
        return j

    while i < n:
        c = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if c == "/" and nxt == "/":
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == "/" and nxt == "*":
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if c in "'\"":
            j = i + 1
            buf = []
            while j < n and src[j] != c and src[j] != "\n":
                if src[j] == "\\":
                    buf.append(src[j : j + 2])
                    j += 2
                    continue
                buf.append(src[j])
                j += 1
            out.append((src.count("\n", 0, i) + 1, "".join(buf), tuple(calls)))
            i = j + 1
            prev = "a"
            continue
        if c == "`":
            i = read_template(i + 1)
            prev = "a"
            continue
        if c == "}" and tpl_stack and tpl_stack[-1] == brace - 1:
            tpl_stack.pop()
            brace -= 1
            i = read_template(i + 1)
            prev = "a"
            continue
        if c == "/" and (prev in "(,=:[!&|?{};+-*%<>~^" or prev == "" or re.search(r"\b(return|typeof|case|in|of)\s*$", src[max(0, i - 10) : i])):
            j = i + 1
            cls = False
            while j < n and src[j] != "\n":
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == "[":
                    cls = True
                elif src[j] == "]":
                    cls = False
                elif src[j] == "/" and not cls:
                    break
                j += 1
            j += 1
            while j < n and src[j].isalpha():
                j += 1
            i = j
            prev = "a"
            continue
        if c == "{":
            brace += 1
        elif c == "}":
            brace -= 1
        elif c == "(":
            calls.append(callee_before(i))
        elif c == ")":
            last_closed = calls.pop() if calls else ""
        if not c.isspace():
            prev = "a" if (c.isalnum() or c in "_$") else c
        i += 1
    del parens
    return out


def js_user_strings(src: str) -> list[tuple[int, str]]:
    """The pieces a person can read: not inside a log call, not code-shaped."""
    keep = []
    for line, text, calls in js_strings(src):
        if any(callee in LOG_CALLS for callee in calls):
            continue
        if not text.strip() or _code_shaped(text):
            continue
        keep.append((line, text))
    return keep


# --------------------------------------------------------------------------- #
# The Python reader (sentences a module holds)
# --------------------------------------------------------------------------- #

_LOG_METHODS = frozenset({"debug", "info", "warning", "warn", "error", "exception", "critical", "log"})


def py_user_strings(src: str) -> list[tuple[int, str]]:
    tree = ast.parse(src)
    skip: set[int] = set()
    for node in ast.walk(tree):
        # docstrings
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                skip.add(id(body[0].value))
        # log calls: log.warning("…"), logger.info(…), _log.error(…)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in _LOG_METHODS:
                for sub in ast.walk(node):
                    skip.add(id(sub))
        # dict keys, comparisons, subscripts: ids, never shown
        if isinstance(node, ast.Dict):
            for k in node.keys:
                if k is not None:
                    skip.add(id(k))
        if isinstance(node, ast.Compare):
            for sub in [node.left, *node.comparators]:
                for s in ast.walk(sub):
                    skip.add(id(s))
        if isinstance(node, ast.Subscript):
            for s in ast.walk(node.slice):
                skip.add(id(s))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            text = node.value
            if text.strip() and not _code_shaped(text):
                out.append((node.lineno, text))
    return out


def schema_user_strings() -> list[tuple[str, str]]:
    from iron_jarvis.settings import schema

    out: list[tuple[str, str]] = []
    for gid, label, desc in schema.GROUPS:
        out += [(f"group:{gid}", label), (f"group:{gid}", desc)]
    for d in schema.SETTINGS:
        out += [(d.key, d.label), (d.key, d.help), (d.key, d.section), (d.key, d.placeholder)]
        out += [(d.key, lbl) for _v, lbl in d.options]
    for s in schema.SECRETS:
        out += [(s.name, s.label), (s.name, s.help)]
    return [(k, t) for k, t in out if t]


# --------------------------------------------------------------------------- #
# The scan
# --------------------------------------------------------------------------- #


def _rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


def _all_hits() -> list[tuple[str, int | str, str, str]]:
    hits: list[tuple[str, int | str, str, str]] = []
    js = MAIN_JS.read_text(encoding="utf-8").replace("\r\n", "\n")
    for line, text in js_user_strings(js):
        for word in _hits(text):
            hits.append((_rel(MAIN_JS), line, word, text))
    for path in PY_FILES:
        for line, text in py_user_strings(path.read_text(encoding="utf-8")):
            for word in _hits(text):
                hits.append((_rel(path), line, word, text))
    for key, text in schema_user_strings():
        for word in _hits(text):
            hits.append(("src/iron_jarvis/settings/schema.py", key, word, text))
    return hits


def _allowed(hit: tuple[str, int | str, str, str]) -> bool:
    return any(hit[0] == f and piece in hit[3] for f, piece, _why in ALLOW)


def test_the_retired_list_is_the_dashboards_own():
    words = [w for w, _ in RETIRED]
    for w in ("daemon", "pack", "connector", "mission", "mock", "pane", "job", "assignment", "simulated"):
        assert w in words, w
    assert len(words) >= 12


def test_no_retired_word_in_anything_the_daemon_or_the_desktop_says():
    bad = [h for h in _all_hits() if not _allowed(h)]
    assert not bad, "retired words a person reads:\n" + "\n".join(
        f"  {f}:{where} [{word}] {text[:120]!r}" for f, where, word, text in bad
    )


def test_every_allow_entry_still_matches_something():
    hits = _all_hits()
    stale = [entry for entry in ALLOW if not any(h[0] == entry[0] and entry[1] in h[3] for h in hits)]
    assert not stale, f"stale ALLOW entries: {stale}"


def test_the_readers_reach_what_they_must():
    """Anti-vacuity: each reader finds the sentences it exists for."""
    js = js_user_strings(MAIN_JS.read_text(encoding="utf-8").replace("\r\n", "\n"))
    texts = [t for _l, t in js]
    assert "Iron Jarvis keeps stalling (Restart from the tray)" in texts
    assert "Iron Jarvis did not start" in texts
    assert any(t.startswith("A chat is waiting for your OK") for t in texts)
    assert len(texts) > 150, len(texts)
    run_outcome = [t for _l, t in py_user_strings(PY_FILES[0].read_text(encoding="utf-8"))]
    assert any("Iron Jarvis kept the details in its log" in t for t in run_outcome), run_outcome
    schema_texts = schema_user_strings()
    assert ("lan_access", "Let other computers on my network reach this Iron Jarvis") in schema_texts
    assert ("voice_vosk_model_path", "Voice: offline speech model folder") in schema_texts
    assert len(schema_texts) > 200, len(schema_texts)


# --------------------------------------------------------------------------- #
# The readers' own rules, on planted text (each would go red if a rule broke)
# --------------------------------------------------------------------------- #


def test_the_js_reader_finds_a_shown_word_and_skips_logs_ids_and_comments():
    planted = "\n".join(
        [
            'tray.setToolTip("Iron Jarvis — the daemon died");',
            "new Notification({ title: \"x\", body: `The ${label} keeps crashing in the pane` });",
            'desktopLog("warn", `[daemon] the daemon died ${x}`);',
            'fileLogger("daemon")(`[main] the daemon died\\n`);',
            'console.error("the daemon died");',
            'if (label === "daemon") startService("daemon", fn);',
            "// the daemon died (a comment)",
            "const re = /daemon died/i;",
            'dialog.showErrorBox("Iron Jarvis", `Lost the mission: ${a ? "Job done" : "x"}`);',
        ]
    )
    found = sorted({(line, word) for line, text in js_user_strings(planted) for word in _hits(text)})
    assert found == [(1, "daemon"), (2, "pane"), (9, "job"), (9, "mission")], found


def test_the_python_reader_finds_a_sentence_and_skips_docstrings_logs_keys_and_comparisons():
    planted = '''"""The daemon module docstring."""
import logging
log = logging.getLogger(__name__)
SENTENCE = "The daemon log has the details."
WORDS = {"job:mission": "Your objective is done."}
def f(x):
    """The daemon helper docstring."""
    log.warning("the daemon failed %s", x)
    if x == "Daemon thing":
        return {"k": x}["Mock key"]
    return f"Ran on the mock model: {x}"
'''
    found = sorted({(line, word) for line, text in py_user_strings(planted) for word in _hits(text)})
    assert found == [(4, "daemon"), (11, "mock")], found


@pytest.mark.parametrize(
    "key, old",
    [
        ("default_persona", "Default persona"),
        ("iron_proxy_enabled", "Iron-Proxy (shared accounts)"),
        ("tool_call_timeout_s", "Tool call deadline (seconds)"),
        ("autonomy_enabled", "Autonomy (the pulse)"),
        ("autonomy_max_tokens_per_day", "Max tokens / day"),
        ("chat_folder_rules", "Read a project's AGENTS.md and CLAUDE.md in chat"),
    ],
)
def test_the_unfolded_settings_rows_read_in_plain_words_and_keep_their_keys(key, old):
    """The re-audit's M (unexplained terminology) and I (raw configuration):
    the label changes, the key does not (stored config, chat tools and
    aliases all keep working), and the old words stay SEARCHABLE where a
    user might type them."""
    from iron_jarvis.settings import schema

    d = schema.get(key)
    assert d.key == key and d.label != old
    for jargon in ("the pulse", "Max tokens", "deadline", "Iron-Proxy (", "AGENTS.md and CLAUDE.md in chat"):
        assert jargon not in d.label, (key, d.label)
    assert d.help, key


def test_old_words_still_find_the_rows_in_settings_search():
    from iron_jarvis.settings import schema

    assert "default persona" in schema.get("default_persona").aliases
    assert "iron proxy" in schema.get("iron_proxy_enabled").aliases
    assert "tool call deadline" in schema.get("tool_call_timeout_s").aliases
    assert "the pulse" in schema.get("autonomy_enabled").aliases
    assert "agents.md" in schema.get("chat_folder_rules").aliases


def test_the_readme_names_what_the_app_shows_without_a_model():
    """README.md:94 named a "Simulated mode" strip the app no longer shows.
    The strip says lib/demoWords' DEMO_STATE ("Demo replies")."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    demo = (ROOT / "dashboard" / "lib" / "demoWords.ts").read_text(encoding="utf-8")
    state = re.search(r'export const DEMO_STATE = "([^"]+)";', demo).group(1)
    assert state == "Demo replies"
    assert f"**{state}** strip" in readme
    assert "Simulated mode" not in readme and "offline demo" not in readme

def test_an_agents_inbox_without_its_queue_says_so_in_the_canons_words(tmp_path):
    """routes/agents.py's 503 (the re-audit's I. daemon list): it said
    "assignments are not available on this daemon — restart it"."""
    from fastapi.testclient import TestClient

    from iron_jarvis.daemon.app import create_app

    app = create_app(str(tmp_path))
    app.state.platform.assignments = None
    res = TestClient(app).get("/agents/builder/inbox")
    assert res.status_code == 503, res.text
    detail = res.json()["detail"]
    assert detail == "Queued tasks are not available right now. Restart Iron Jarvis to bring them back."
    assert not _hits(detail), detail
