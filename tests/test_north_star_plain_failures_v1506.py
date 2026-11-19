"""No raw exception text on screen (north-star fix plan 8.1, v1.506.0).

The daemon handed raw Python exception text to the screen in about 170 error
details (``detail=f"cannot redact: {exc}"``, ``f"{type(exc).__name__}:
{exc}"``, ``f"undo failed: {type(exc).__name__}: {exc}"``); the dashboard
rewords only a 500, so a 4xx detail reached a person as it stood.

``core/failures.plain_failure(what, exc)`` is the one rule: a sentence our own
code wrote is kept, anything else is the route's plain ``what`` line and the
raw text goes to the log (masked). This file holds:

1. the rule itself, on real raised exceptions;
2. routes driven end to end with a failure only the engine could word;
3. THE SCAN: no ``HTTPException(... detail=...)`` in ``daemon/routes/``
   interpolates a caught exception (``str(exc)``, ``f"…{exc}"``,
   ``type(exc).__name__``, or a variable made from one) outside the helper,
   unless the ``except`` catches only this package's own exception classes
   (each carries a sentence written for the user) or the site is on ALLOW
   with its reason. ``daemon/routes/chat.py`` and ``improvements.py`` are
   another lane's this wave (``PENDING_OTHER_LANE``).
"""

from __future__ import annotations

import ast
import builtins
import logging
from pathlib import Path

import pytest

from iron_jarvis.core.failures import own_sentence, plain_failure

ROOT = Path(__file__).resolve().parents[1]
ROUTES = ROOT / "src" / "iron_jarvis" / "daemon" / "routes"

WHAT = "That file could not be redacted."


def _raised(make):
    try:
        make()
    except BaseException as exc:  # noqa: BLE001
        return exc
    raise AssertionError("did not raise")


# --------------------------------------------------------------------------- #
# 1. The rule
# --------------------------------------------------------------------------- #
def _raise_in_package(stmt: str, filename: str):
    """Raise *stmt* from a frame whose code lives in the package (the rule
    reads the innermost frame's file name, as a frozen build keeps it)."""
    code = compile(stmt, filename, "exec")
    return _raised(lambda: exec(code, {}))


PKG_FILE = str(ROOT / "src" / "iron_jarvis" / "documents" / "_probe.py")
FROZEN_FILE = "iron_jarvis\\documents\\_probe.py"


@pytest.mark.parametrize(
    "stmt",
    [
        'raise ValueError("That page number is past the end of the file.")',
        'raise PermissionError("That folder is outside the folders Iron Jarvis may use.")',
        'raise RuntimeError("Choose a model first.")',
    ],
    ids=["our ValueError", "our PermissionError", "our RuntimeError"],
)
@pytest.mark.parametrize("filename", [PKG_FILE, FROZEN_FILE], ids=["source", "frozen"])
def test_a_sentence_our_code_wrote_is_kept(stmt, filename):
    exc = _raise_in_package(stmt, filename)
    assert plain_failure(WHAT, exc) == str(exc)


@pytest.mark.parametrize(
    "stmt",
    [
        'int("abc")',  # a builtin's words, raised on OUR line
        'raise ValueError("KeyError: \'agent_type\'")',
        "open(r'Z:/no/such/dir/f.txt')",  # the system's OSError, errno set
    ],
    ids=["builtin int()", "engine prefix", "system OSError"],
)
def test_engine_words_raised_in_the_package_are_still_not_kept(stmt):
    assert plain_failure(WHAT, _raise_in_package(stmt, PKG_FILE)) == WHAT


def test_the_same_plain_error_from_outside_the_package_is_not_kept():
    """A library's plain ValueError (its frame is not ours) is its words."""
    exc = _raised(lambda: (_ for _ in ()).throw(ValueError("That page number is past the end.")))
    assert plain_failure(WHAT, exc) == WHAT


def test_an_http_exception_keeps_its_detail():
    from fastapi import HTTPException

    assert plain_failure(WHAT, HTTPException(status_code=409, detail="Already running.")) == "Already running."


def test_our_own_exception_class_keeps_its_sentence():
    from iron_jarvis.workmap.clients import ClientError  # a class defined in this package

    exc = _raised(lambda: (_ for _ in ()).throw(ClientError("That client is already known.")))
    assert plain_failure(WHAT, exc) == "That client is already known."


@pytest.mark.parametrize(
    "make",
    [
        lambda: {}["path"],  # KeyError
        lambda: None + 1,  # TypeError
        lambda: open(ROOT / "no-such-dir" / "f.txt", encoding="utf-8"),  # the SYSTEM's OSError
        lambda: int("abc"),  # a builtin's ValueError, raised on OUR line
        lambda: __import__("json").loads("{nope"),  # a library's ValueError subclass
        lambda: __import__("base64").b64decode("a", validate=True),  # binascii.Error
        lambda: (_ for _ in ()).throw(Exception("boom")),  # a bare Exception
        lambda: (_ for _ in ()).throw(ValueError("Traceback (most recent call last):\n  x")),
        lambda: (_ for _ in ()).throw(RuntimeError("KeyError: 'agent_type'")),
    ],
    ids=["KeyError", "TypeError", "system OSError", "int()", "JSONDecodeError", "binascii",
         "bare Exception", "a stack", "an engine prefix"],
)
def test_anything_else_is_the_plain_line_and_the_raw_text_is_logged(make, caplog):
    exc = _raised(make)
    with caplog.at_level(logging.WARNING, logger="iron_jarvis.core.failures"):
        assert plain_failure(WHAT, exc) == WHAT
    assert any(WHAT in r.getMessage() and type(exc).__name__ in r.getMessage() for r in caplog.records)


def test_a_secret_in_the_raw_text_is_masked_in_the_log(caplog):
    exc = _raised(lambda: (_ for _ in ()).throw(OSError(5, "denied", "https://x.example/?api_key=abcdef0123456789")))
    with caplog.at_level(logging.WARNING, logger="iron_jarvis.core.failures"):
        plain_failure(WHAT, exc)
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "abcdef0123456789" not in logged


def test_never_raises_and_never_empty():
    assert plain_failure("", None) == "Iron Jarvis hit a problem doing that. Try again."
    assert own_sentence(None) == ""

    class Weird(Exception):
        def __str__(self):
            raise RuntimeError("no")

    assert plain_failure(WHAT, Weird()) == WHAT


# --------------------------------------------------------------------------- #
# 2. End to end: an engine failure reaches the screen as a plain line
# --------------------------------------------------------------------------- #
def _client(tmp_path):
    from fastapi.testclient import TestClient

    from iron_jarvis.daemon.app import create_app

    return TestClient(create_app(str(tmp_path)))


def test_a_redact_that_hits_a_library_error_says_the_plain_line(tmp_path, monkeypatch, caplog):
    import iron_jarvis.documents.redact as redact_mod

    client = _client(tmp_path)

    def boom(*a, **k):
        raise KeyError("/Root")

    monkeypatch.setattr(redact_mod, "redact_file", boom)
    f = tmp_path / "a.pdf"
    f.write_bytes(b"%PDF-1.4\n%%EOF\n")
    with caplog.at_level(logging.WARNING, logger="iron_jarvis.core.failures"):
        r = client.post(
            "/documents/redact/apply",
            json={"path": str(f), "terms": ["x"], "output_path": str(tmp_path / "out.pdf")},
        )
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "That file could not be redacted. The original is unchanged."
    # The raw text went to the log instead.
    assert any("/Root" in rec.getMessage() for rec in caplog.records)


def test_a_connect_that_breaks_inside_says_the_plain_line(tmp_path, monkeypatch):
    import iron_jarvis.connectors as C

    client = _client(tmp_path)

    def boom(*a, **k):
        raise TypeError("'NoneType' object is not subscriptable")

    monkeypatch.setattr(C, "connect", boom)
    r = client.post("/connectors/github/connect", json={"values": {}})
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert "TypeError" not in detail and "NoneType" not in detail
    assert detail == "That connection could not be set up. Check what you entered and try again."


# --------------------------------------------------------------------------- #
# 3. THE SCAN
# --------------------------------------------------------------------------- #
#: Another lane owns these files this wave (plan 8.1): their sites are listed
#: for the lead, not scanned here.
PENDING_OTHER_LANE = {"chat.py", "improvements.py"}

#: The keys whose value a page shows as words.
ANSWER_KEYS = {"detail", "error", "reason", "note", "words", "why", "message", "summary", "fix", "hint"}

#: Sites whose interpolated text IS worded for a person, each with the reason.
#: An entry that no longer matches a site fails (a fixed line takes its entry).
ALLOW: list[tuple[str, str, str]] = [
    # v1.516.0 (second review F13, decision E8): the Model servers page's
    # `_err(exc)` entry is gone; its errors are plain_failure sentences and
    # the program's words ride `error_detail`, folded under Details.
    ("settings.py", "The disk is full",
     "`full` is a yes/no read off the exception (ENOSPC), never its text; the raw text is logged above"),
]

#: Calls that turn an exception INTO words for a person (what they return is
#: never the raw text): this file's helper and the existing wording helpers.
WORDING_HELPERS = {
    "plain_failure",  # core/failures (this change)
    "friendly_error",  # providers/failure_words: a model's failure in words
    "plain_reason",  # providers/failure_words
    "_plain_validation_words",  # routes/agents: the extension import's validation words
    "_unavailable_sentence",  # terminals/pane_accounts: the account manager's words
    "_read_failed_words",  # routes/fleet (v1.516.0): plain_failure for the polled read, logged once per five minutes
    "_why_not_reopened",  # terminals/manager: a terminal's program in words, else plain_failure (v1.539.0 uses it for POST /terminals)
}

_BUILTIN_NAMES = set(dir(builtins))


def _call_name(expr) -> str:
    if not isinstance(expr, ast.Call):
        return ""
    f = expr.func
    return f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""


def _names(node) -> set[str]:
    """Names read by *node*, not counting what a wording helper's call reads."""
    out: set[str] = set()
    stack = [node]
    while stack:
        n = stack.pop()
        if _call_name(n) in WORDING_HELPERS:
            continue
        if isinstance(n, ast.Name):
            out.add(n.id)
        stack.extend(ast.iter_child_nodes(n))
    return out


def _imports(tree) -> dict[str, str]:
    """local name -> the module it came from ('.'-prefixed when relative)."""
    out: dict[str, str] = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            mod = "." * n.level + (n.module or "")
            for a in n.names:
                out[a.asname or a.name] = mod
        elif isinstance(n, ast.Import):
            for a in n.names:
                out[a.asname or a.name.split(".")[0]] = a.name
    return out


def _own_class(expr, imports: dict[str, str], classes: set[str]) -> bool:
    """Is this ``except`` type one of THIS package's exception classes?"""
    if isinstance(expr, ast.Tuple):
        return all(_own_class(e, imports, classes) for e in expr.elts)
    if isinstance(expr, ast.Name):
        if expr.id == "HTTPException":
            return True  # a route's own words, re-raised
        if expr.id in _BUILTIN_NAMES:
            return False
        if expr.id in classes:
            return True
        mod = imports.get(expr.id, "")
        return mod.startswith(".") or mod.startswith("iron_jarvis")
    if isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name):
        mod = imports.get(expr.value.id, "")
        return mod.startswith(".") or mod.startswith("iron_jarvis")
    return False


def _detail(call):
    for kw in call.keywords:
        if kw.arg == "detail":
            return kw.value
    return call.args[1] if len(call.args) >= 2 else None


def _is_http(call) -> bool:
    f = call.func
    return (isinstance(f, ast.Name) and f.id == "HTTPException") or (
        isinstance(f, ast.Attribute) and f.attr == "HTTPException"
    )


def _helper_call(expr) -> bool:
    """The whole expression is a wording helper's answer (sliced or not)."""
    while isinstance(expr, ast.Subscript):
        expr = expr.value
    return _call_name(expr) in WORDING_HELPERS


def _scan() -> list[tuple[str, int, str]]:
    hits: list[tuple[str, int, str]] = []
    for path in sorted(ROUTES.glob("*.py")):
        if path.name in PENDING_OTHER_LANE:
            continue
        src = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        tree = ast.parse(src)
        imports = _imports(tree)
        classes = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
        parent: dict[ast.AST, ast.AST] = {}
        for n in ast.walk(tree):
            for ch in ast.iter_child_nodes(n):
                parent[ch] = n

        def ancestors(n):
            while n in parent:
                n = parent[n]
                yield n

        def function_of(n):
            return next(
                (a for a in ancestors(n) if isinstance(a, (ast.FunctionDef, ast.AsyncFunctionDef))), None
            )

        # Per handler: does it catch only our own classes, and which names in
        # its body carry the caught exception (its name, and every variable
        # made from it there: a fixpoint). A variable made in a handler and
        # used after it is judged by that handler ("escaped", per function).
        per_handler: dict[ast.AST, tuple[bool, set[str]]] = {}
        escaped: dict[tuple[int, str], bool] = {}
        for h in ast.walk(tree):
            if not (isinstance(h, ast.ExceptHandler) and h.name):
                continue
            own = h.type is not None and _own_class(h.type, imports, classes)
            names = {h.name}
            changed = True
            while changed:
                changed = False
                for stmt in h.body:
                    for n in ast.walk(stmt):
                        if not isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign)) or n.value is None:
                            continue
                        if _helper_call(n.value) or not (_names(n.value) & names):
                            continue
                        targets = n.targets if isinstance(n, ast.Assign) else [n.target]
                        for t in targets:
                            new = _names(t) - names
                            if new:
                                names |= new
                                changed = True
            per_handler[h] = (own, names)
            fn_id = id(function_of(h))
            for nm in names - {h.name}:
                escaped[(fn_id, nm)] = escaped.get((fn_id, nm), True) and own
        # What a person reads: an HTTPException's detail, and any value under
        # an answer key of a dict a route returns or records (JSONResponse
        # content, a step's "words", a skipped row's "reason", …).
        shown: list[tuple[ast.AST, ast.AST]] = []
        for c in ast.walk(tree):
            if isinstance(c, ast.Call) and _is_http(c):
                det = _detail(c)
                if det is not None:
                    shown.append((c, det))
            elif isinstance(c, ast.Dict):
                for k, v in zip(c.keys, c.values):
                    if isinstance(k, ast.Constant) and k.value in ANSWER_KEYS and v is not None:
                        shown.append((v, v))
        for c, d in shown:
            if _helper_call(d):
                continue
            used = _names(d)
            bad = False
            for h in ancestors(c):
                if h in per_handler:
                    own, names = per_handler[h]
                    hit = used & names
                    if hit and not own:
                        bad = True
                    used = used - hit
            fn_id = id(function_of(c))
            if any(not escaped.get((fn_id, nm), True) for nm in used):
                bad = True
            if bad:
                hits.append((path.name, c.lineno, ast.unparse(d)))
    return sorted(set(hits))


def _allowed(hit) -> bool:
    return any(hit[0] == f and snippet in hit[2] for f, snippet, _ in ALLOW)


def test_no_route_puts_a_caught_exceptions_text_on_screen():
    left = [f"{f}:{line}: {d[:140]}" for f, line, d in _scan() if not _allowed((f, line, d))]
    assert left == [], "\n".join(left)


def test_every_allow_entry_still_matches_a_site_and_says_why():
    hits = _scan()
    for f, snippet, why in ALLOW:
        assert len(why.strip()) > 15, (f, snippet)
        assert any(h[0] == f and snippet in h[2] for h in hits), f"stale ALLOW entry {f}: {snippet!r}"


def test_the_scan_sees_what_it_must_and_skips_what_it_may():
    """Anti-vacuity: the scanner flags each shape it exists for, on a probe."""
    probe = '''
from fastapi import HTTPException
from .x import Refused

def a():
    try:
        pass
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"cannot redact: {exc}")

def b():
    try:
        pass
    except OSError as exc:
        msg = str(exc)
        raise HTTPException(500, msg)

def c():
    try:
        pass
    except KeyError as exc:
        raise HTTPException(400, detail=f"{type(exc).__name__}")

def ok_own():
    try:
        pass
    except Refused as exc:
        raise HTTPException(400, detail=str(exc))

def ok_helper():
    try:
        pass
    except Exception as exc:
        raise HTTPException(400, detail=plain_failure("That did not work.", exc))
'''
    import tempfile

    global ROUTES
    real = ROUTES
    with tempfile.TemporaryDirectory() as d:
        Path(d, "probe.py").write_text(probe, encoding="utf-8")
        ROUTES = Path(d)
        try:
            found = {(line, text.split("(")[0]) for _f, line, text in _scan()}
        finally:
            ROUTES = real
    assert len(found) == 3, found
