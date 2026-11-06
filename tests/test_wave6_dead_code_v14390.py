"""Wave 6, group E (v1.439.0): dead code, and nothing orphaned without a reason.

Decision D6 of docs/CHAT-FIRST-PLAN-2026-11-04.md, approved as recommended:

* the SEVEN dashboard modules imported only by tests are removed —
  EventStream, FirstRunWizard, ModelSwitcher, AppGrid, HealthCard, PowerTips,
  tileDrag — and nothing may import them again;
* the round table's leftover routes are removed (the rooms behind chat
  @-mentions, the phone and remote inbound stay — ``agents/threads.py``), and
  so are two older copies of current routes;
* every route the daemon registers either has a CALLER (the dashboard, the
  desktop app, the browser add-on, the CLI) or a written decision in
  docs/ROUTES-WITHOUT-A-SCREEN.md. That third check is MAINTAINED: a new route
  nobody calls fails here until the doc says why it exists.

The caller check is a static read of the clients' source (string literals,
constants and small path helpers expanded, comments left out). It reads PATHS,
not methods, so it is lenient where two methods share a path — it can miss an
orphan, never invent one; the doc says so.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app

REPO = Path(__file__).resolve().parents[1]
DASH = REPO / "dashboard"
DOC = REPO / "docs" / "ROUTES-WITHOUT-A-SCREEN.md"

# --------------------------------------------------------------------------- #
# 1. the seven modules
# --------------------------------------------------------------------------- #
DEAD_MODULES = {
    "EventStream": "components/EventStream.tsx",
    "FirstRunWizard": "components/FirstRunWizard.tsx",
    "ModelSwitcher": "components/ModelSwitcher.tsx",
    "AppGrid": "components/overview/AppGrid.tsx",
    "HealthCard": "components/overview/HealthCard.tsx",
    "PowerTips": "components/PowerTips.tsx",
    "tileDrag": "lib/tileDrag.ts",
}


def _dashboard_sources():
    for top in ("app", "components", "lib", "__tests__", "e2e"):
        root = DASH / top
        if not root.is_dir():
            continue
        for p in root.rglob("*"):
            if p.suffix in (".ts", ".tsx", ".js", ".mjs") and "node_modules" not in p.parts:
                yield p


def test_the_seven_dead_modules_are_gone_and_nothing_imports_them():
    for name, rel in DEAD_MODULES.items():
        assert not (DASH / rel).exists(), f"{rel} is back"
    # An import, a dynamic import, a vi.mock or a next/dynamic loader of any
    # of them — by alias or by relative path.
    stems = "|".join(Path(rel).with_suffix("").as_posix() for rel in DEAD_MODULES.values())
    names = "|".join(DEAD_MODULES)
    rx = re.compile(
        r"""(?:from|import\(|require\(|vi\.mock\()\s*["'](?:@/(?:%s)|\.{1,2}/(?:[\w./]*/)?(?:%s))["']"""
        % (stems, names)
    )
    hits = []
    for p in _dashboard_sources():
        text = p.read_text(encoding="utf-8", errors="replace")
        for m in rx.finditer(text):
            hits.append(f"{p.relative_to(DASH).as_posix()}: {m.group(0)}")
    assert hits == [], hits


# --------------------------------------------------------------------------- #
# 2. the removed routes
# --------------------------------------------------------------------------- #
REMOVED = [
    ("GET", "/agents/threads"),
    ("POST", "/agents/threads"),
    ("PUT", "/agents/threads/{thread_id}/participants"),
    ("DELETE", "/agents/threads/{thread_id}"),
    ("POST", "/agents/threads/{thread_id}/say"),
    ("POST", "/agents/threads/{thread_id}/remember"),
    ("POST", "/projects/{project_id}/world/room"),
    ("GET", "/providers"),
    ("POST", "/providers/endpoint-models"),
]


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    return create_app(str(tmp_path_factory.mktemp("wave6")))


def _registered(app) -> set[tuple[str, str]]:
    out = set()
    for r in app.routes:
        methods = getattr(r, "methods", None) or {"WS"}
        for m in methods:
            if m != "HEAD":
                out.add((m, r.path))
    return out


@pytest.mark.parametrize("method,path", REMOVED)
def test_each_removed_route_is_gone(app, method, path):
    assert (method, path) not in _registered(app)
    concrete = re.sub(r"\{[^}]+\}", "x1", path)
    r = TestClient(app).request(method, concrete, json={})
    assert r.status_code in (404, 405), (method, path, r.status_code)


def test_the_rooms_the_routes_left_behind_still_serve_one_room(app):
    """The backend stays: ``GET /agents/threads/{id}`` (the chat page's
    mirror, the mission screen's transcript) still answers."""
    assert ("GET", "/agents/threads/{thread_id}") in _registered(app)
    assert TestClient(app).get("/agents/threads/athr_none").status_code == 404


# --------------------------------------------------------------------------- #
# 3. every route has a caller, or a reason written down
# --------------------------------------------------------------------------- #
P = "\x00"  # a value only known at run time
_STR = re.compile(r"""(["'`])((?:\x00|/|https?://|wss?://)[^"'`\n]*)\1""")
_CONST = re.compile(r"""(?:const|let|var)\s+([A-Za-z_]\w*)\s*(?::\s*[\w<>\[\]| ]+)?=\s*(["'`])(/[^"'`\n]*)\2""")
_FN_ARROW = re.compile(r"""(?:const|let)\s+([A-Za-z_]\w*)\s*=\s*\([^)]*\)\s*(?::\s*\w+\s*)?=>\s*`(/[^`\n]*)`""")
_FN_DECL = re.compile(r"""function\s+([A-Za-z_]\w*)\s*\([^)]*\)\s*(?::\s*\w+\s*)?\{\s*return\s+`(/[^`\n]*)`""")
_PYCONST = re.compile(r"""^\s*([A-Z_][A-Z0-9_]*)\s*(?::\s*\w+\s*)?=\s*["'](/[^"'\n]*)["']""", re.M)
#: Template names that hold the daemon's ADDRESS, not a path segment.
_ADDRESS_NAMES = {"API_BASE", "apiBase", "DAEMON", "DAEMON_URL", "daemonUrl", "BASE_URL", "baseUrl", "ORIGIN"}


def _client_files():
    globs = [
        ("dashboard/app", "**/*.ts*"), ("dashboard/components", "**/*.ts*"), ("dashboard/lib", "**/*.ts*"),
        ("desktop", "*.js"), ("extensions/chrome/src", "**/*.ts"),
        # the add-on's daemon address is injected at build time from here
        ("extensions/chrome", "*.mjs"),
    ]
    for top, pat in globs:
        for p in (REPO / top).glob(pat):
            if p.is_file() and "node_modules" not in p.parts:
                yield p
    for rel in ("src/iron_jarvis/daemon/cli.py", "src/iron_jarvis/daemon/client.py"):
        yield REPO / rel


def _strip_comments(text: str, suffix: str) -> str:
    if suffix == ".py":
        return text
    return "\n".join(
        "" if line.lstrip().startswith(("//", "*", "/*", "{/*")) else line for line in text.splitlines()
    )


def _client_paths() -> set[tuple[str, ...]]:
    texts = {}
    consts: dict[str, str] = {}
    for p in _client_files():
        t = _strip_comments(p.read_text(encoding="utf-8", errors="replace"), p.suffix)
        texts[p] = t
        for m in _CONST.finditer(t):
            consts.setdefault(m.group(1), m.group(3))
        for m in _PYCONST.finditer(t):
            consts.setdefault(m.group(1), m.group(2))
    out: set[tuple[str, ...]] = set()
    for p, t in texts.items():
        local = dict(consts)
        local.update({m.group(1): m.group(3) for m in _CONST.finditer(t)})
        helpers = {m.group(1): m.group(2) for m in _FN_ARROW.finditer(t)}
        helpers.update({m.group(1): m.group(2) for m in _FN_DECL.finditer(t)})

        def expand(m: re.Match) -> str:
            inner = m.group(1).strip()
            if inner in _ADDRESS_NAMES:
                return ""
            name = re.match(r"([A-Za-z_]\w*)(\(.*\))?$", inner)
            if name:
                if name.group(2) is not None and name.group(1) in helpers:
                    return helpers[name.group(1)]
                if name.group(2) is None and name.group(1) in local:
                    return local[name.group(1)]
            return P

        for _ in range(3):  # a helper may name a constant, which names another
            t = re.sub(r"\$\{([^{}]*)\}", expand, t)
        if p.suffix == ".py":
            t = re.sub(r"(?<![\w$])\{[^{}\n]*\}", P, t)  # f-string fields
        t = re.sub(r"""["'`]\s*\+\s*[^"'`;\n]{1,80}?\s*\+\s*["'`]""", P, t)  # "a/" + x + "/b"
        t = re.sub(r"""/(["'`])\s*\+\s*[\w.()\[\]]+""", "/" + P + r"\1", t)  # "a/" + x
        for m in _STR.finditer(t):
            s = m.group(2)
            s = re.sub(r"^(?:https?|wss?)://[^/]*", "", s)
            s = re.split(r"[?#]", s, maxsplit=1)[0]
            # a run-time value glued onto a segment (`/view${hash}`,
            # `/${part}${query}`) is a query or a suffix, not more path
            s = re.sub(r"(?<=[^/])\x00.*$", "", s)
            if not s.startswith("/"):
                continue
            segs = tuple(s.strip("/").split("/"))
            out.add(segs)
    return out


def _route_called(path: str, client: set[tuple[str, ...]]) -> bool:
    segs = path.strip("/").split("/")
    for cand in client:
        if len(cand) != len(segs):
            continue
        ok = True
        literal = 0
        for i, (want, got) in enumerate(zip(segs, cand)):
            if want.startswith("{") and want.endswith("}"):
                if not got:
                    ok = False
                    break
            elif got == want:
                literal += 1
            elif got == P and i == len(segs) - 1 and i > 0:
                continue  # a dynamic last segment: `/x/${id}/${verb}`
            else:
                ok = False
                break
        if ok and literal:
            return True
    return False


_ROW = re.compile(r"^\|\s*(GET|POST|PUT|PATCH|DELETE|WS)\s*\|\s*`([^`]+)`\s*\|\s*([^|]+?)\s*\|", re.M)


def _doc_rows() -> dict[tuple[str, str], str]:
    text = DOC.read_text(encoding="utf-8")
    rows = {}
    for m in _ROW.finditer(text):
        rows[(m.group(1), m.group(2))] = m.group(3).strip().upper()
    return rows


def test_the_doc_and_the_daemon_agree(app):
    rows = _doc_rows()
    registered = _registered(app)
    assert rows, "the decision table is empty"
    for (method, path), decision in rows.items():
        if decision.startswith("REMOVE"):
            assert (method, path) not in registered, f"{method} {path} is marked REMOVE but still registered"
            assert (method, path) in set(REMOVED), f"{method} {path}: REMOVE rows are pinned in REMOVED too"
        else:
            assert (method, path) in registered, f"{method} {path} is in the doc but not registered"
    for method, path in REMOVED:
        assert rows.get((method, path), "").startswith("REMOVE"), f"{method} {path} needs its REMOVE row"


def test_every_route_has_a_caller_or_a_written_reason(app):
    client = _client_paths()
    rows = _doc_rows()
    missing = sorted(
        f"{m} {p}" for m, p in _registered(app) if (m, p) not in rows and not _route_called(p, client)
    )
    assert missing == [], (
        "these routes have no caller in the dashboard, the desktop app, the add-on or the CLI, "
        "and no row in docs/ROUTES-WITHOUT-A-SCREEN.md saying why they exist:\n" + "\n".join(missing)
    )


def test_the_caller_check_sees_a_real_caller_and_misses_none_it_should_see():
    """CONTROL for the static read: a route the dashboard calls through a
    path helper (``${base(id)}/commit``), through a constant prefix
    (``${SESSIONS}/…/steer``), through the daemon's address (``${API_BASE}/
    audit/export``) and through a dynamic last segment (``/goals/${id}/${verb}``)
    is found; a route nobody names is not."""
    client = _client_paths()
    for path in (
        "/build/sessions/{thread_id}/git/commit",
        "/build/app/sessions/{sid}/steer",
        "/audit/export",
        "/goals/{goal_id}/pause",
        "/agents/threads/{thread_id}",
    ):
        assert _route_called(path, client), path
    assert not _route_called("/agents/threads/{thread_id}/say", client)
    assert not _route_called("/projects/{project_id}/world/room", client)
    assert not _route_called("/nothing/calls/this/{x}", client)
