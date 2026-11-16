"""Second review, group 15 part B: what the browser tools keep on disk and
where the isolated browser may go (v1.515.0; findings B4, B5, B7).

B4. A tab switch, a page load and a download used to reach the event bus
    (and so the event log, the backups and every ``/events`` subscriber) with
    the FULL address the add-on reported, query string included. The bus now
    gets the tab id and the site only for tab and page events, and download
    addresses without their query or fragment; the backend's in-memory active
    tab keeps the full address for the tools, and ``local_path`` stays exact.

B5. Text typed through ``web_action`` into a sensitive field used to be kept
    in plain text in the approvals table and handed whole to the risk card.
    The queue now keeps a marker plus a digest (an approval still matches the
    same typed text), the card gets redacted arguments, the typing itself
    still uses the live value, and ``GET /computeruse/approvals`` masks the
    typed value of an older row.

B7. The isolated browser's site list was checked on the typed address only.
    A redirect or a click that lands off the list is now refused by name, its
    content is never returned, and the browser leaves the page; a read or a
    typed action re-checks the page it is on. No list set: unchanged.

Offline: the module's FakeBrowser, a temp database, no daemon.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import select

import iron_jarvis.computeruse.models  # noqa: F401  (register tables before init_db)
from iron_jarvis.browser import protocol as P
from iron_jarvis.browser.extension_backend import (
    EVENT_DOWNLOAD_COMPLETED,
    EVENT_NAVIGATION_COMPLETED,
    EVENT_TAB_ACTIVATED,
    ExtensionBackend,
)
from iron_jarvis.computeruse import (
    ApprovalQueue,
    ComputerUsePolicy,
    CUContext,
    FakeBrowser,
    computeruse_tools,
)
from iron_jarvis.computeruse.models import ApprovalRequest
from iron_jarvis.core.db import init_db, make_engine, session_scope
from iron_jarvis.daemon.routes import computeruse as cu_routes
from iron_jarvis.tools.base import ToolContext

SECRET_WORD = "test-code-value"
TYPED = "test-pass-value"


# --------------------------------------------------------------------------- #
# B4: what the add-on's events publish
# --------------------------------------------------------------------------- #


def _backend_capturing() -> tuple[ExtensionBackend, list[tuple[str, dict]]]:
    backend = ExtensionBackend()
    seen: list[tuple[str, dict]] = []

    async def _capture(name: str, payload: dict[str, Any]) -> None:
        seen.append((name, json.loads(json.dumps(payload))))

    backend._publish = _capture  # type: ignore[method-assign]
    return backend, seen


def _flat(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


async def test_b4_tab_switch_publishes_tab_and_site_only_and_keeps_the_full_url_in_memory():
    backend, seen = _backend_capturing()
    url = f"https://accounts.example.test/reset/step-two?code={SECRET_WORD}#frag-part"
    await backend._handle_event(
        {
            "event": P.EVENT_TAB_ACTIVATED,
            "payload": {"tab_id": 7, "title": "Reset your password", "url": url},
        }
    )
    assert [name for name, _ in seen] == [EVENT_TAB_ACTIVATED]
    published = seen[0][1]
    assert published == {"tab_id": 7, "site": "https://accounts.example.test"}
    text = _flat(published)
    for leaked in (SECRET_WORD, "reset/step-two", "frag-part", "Reset your password", "?"):
        assert leaked not in text, f"{leaked!r} reached the bus: {text}"
    # The tools still read the full address of the active tab from memory.
    assert backend.active_tab is not None
    assert backend.active_tab["url"] == url
    assert backend.active_tab["title"] == "Reset your password"


async def test_b4_navigation_publishes_tab_and_site_only_and_updates_the_cache():
    backend, seen = _backend_capturing()
    await backend._handle_event(
        {
            "event": P.EVENT_TAB_ACTIVATED,
            "payload": {"tab_id": 7, "title": "Home", "url": "https://site.example.test/"},
        }
    )
    url = f"https://site.example.test:8443/signin/callback?code={SECRET_WORD}"
    await backend._handle_event(
        {
            "event": P.EVENT_NAVIGATION_COMPLETED,
            "payload": {"tab_id": 7, "title": "Signed in", "url": url, "page_version": 4},
        }
    )
    nav = [payload for name, payload in seen if name == EVENT_NAVIGATION_COMPLETED]
    assert nav == [{"tab_id": 7, "site": "https://site.example.test:8443"}]
    text = _flat(nav)
    for leaked in (SECRET_WORD, "signin", "callback", "Signed in"):
        assert leaked not in text
    assert backend.active_tab["url"] == url


async def test_b4_an_unreadable_tab_publishes_no_site_at_all():
    """Without the site grant the add-on sends ``url: null``: no site is guessed."""
    backend, seen = _backend_capturing()
    await backend._handle_event(
        {
            "event": P.EVENT_NAVIGATION_COMPLETED,
            "payload": {"tab_id": 3, "title": None, "url": None},
        }
    )
    assert seen == [(EVENT_NAVIGATION_COMPLETED, {"tab_id": 3})]


async def test_b4_download_publishes_urls_without_query_and_the_exact_local_path(tmp_path):
    backend, seen = _backend_capturing()
    local = str(tmp_path / "Downloads" / "report final (1).pdf")
    await backend._handle_event(
        {
            "event": P.EVENT_DOWNLOAD_COMPLETED,
            "payload": {
                "download_id": 11,
                "filename": local,
                "source_url": f"https://files.example.test/get/report.pdf?code={SECRET_WORD}#x",
                "final_url": f"https://cdn.example.test/blob/42?sig={SECRET_WORD}",
                "tab_id": 7,
                "bytes": 2048,
                "mime": "application/pdf",
            },
        }
    )
    assert [name for name, _ in seen] == [EVENT_DOWNLOAD_COMPLETED]
    published = seen[0][1]
    assert SECRET_WORD not in _flat(published)
    assert published["source_url"] == "https://files.example.test/get/report.pdf"
    assert published["final_url"] == "https://cdn.example.test/blob/42"
    assert published["local_path"] == local
    assert published["filename"] == local
    # The kept list in memory is the add-on's report as verified, unchanged.
    kept = backend.recent_downloads
    assert kept and kept[-1]["local_path"] == local
    assert kept[-1]["source_url"].endswith(f"?code={SECRET_WORD}#x"), (
        "the backend's own record (what an acting tool's result names) is unchanged"
    )


async def test_b4_the_downloads_ledger_still_records_from_the_published_event(tmp_path):
    """The one consumer of the download event that reads an address."""
    from types import SimpleNamespace

    from iron_jarvis.browser.downloads import DownloadLedger, handler_for

    backend, seen = _backend_capturing()
    local = str(tmp_path / "a report.pdf")
    await backend._handle_event(
        {
            "event": P.EVENT_DOWNLOAD_COMPLETED,
            "payload": {
                "download_id": 1,
                "filename": local,
                "source_url": f"https://files.example.test/get?code={SECRET_WORD}",
            },
        }
    )
    ledger = DownloadLedger(tmp_path / "home")
    handler_for(ledger)(SimpleNamespace(type=seen[0][0], payload=seen[0][1]))
    rows = ledger.entries()
    assert rows and rows[0]["path"] == local
    assert rows[0]["site"] == "https://files.example.test/get"


# --------------------------------------------------------------------------- #
# Computer use fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def engine(tmp_path):
    e = make_engine(str(tmp_path / "cu.db"))
    init_db(e)
    return e


@pytest.fixture
def approvals(engine):
    return ApprovalQueue(engine)


def _policy(**kw: Any) -> ComputerUsePolicy:
    base: dict[str, Any] = dict(
        enabled=True,
        domain_allowlist=["example.com"],
        action_allowlist=["navigate", "read", "extract", "screenshot", "wait", "type", "click"],
    )
    base.update(kw)
    return ComputerUsePolicy(**base)


def _ctx(engine, tmp_path, run: str = "r") -> ToolContext:
    return ToolContext(
        workspace=tmp_path,
        session_id="s",
        agent_run_id=run,
        config=None,
        event_bus=None,
        engine=engine,
    )


def _rows(engine) -> list[ApprovalRequest]:
    with session_scope(engine) as db:
        return list(db.exec(select(ApprovalRequest)))


LOGIN = {
    "https://example.com/login": {
        "text": "Sign in to your account",
        "a11y": [{"role": "heading", "name": "Sign in"}],
        "fields": [{"selector": "#password", "type": "password", "name": "Password"}],
    }
}


# --------------------------------------------------------------------------- #
# B5: the typed value at rest
# --------------------------------------------------------------------------- #


async def test_b5_a_pending_typed_value_is_never_stored_and_never_on_the_card(
    approvals, engine, tmp_path
):
    cards: list[dict[str, Any]] = []

    def resolver(req, ctx, *, name, args):  # the wide shape a chat lane uses
        cards.append({"args": dict(args), "action_json": req.action_json})
        return False

    cu = CUContext(policy=_policy(), browser=FakeBrowser(LOGIN), approvals=approvals,
                   approval_resolver=resolver)
    tools = {t.name: t for t in computeruse_tools(cu)}
    await cu.browser.navigate("https://example.com/login")
    res = await tools["web_action"].execute(
        {"kind": "type", "css": "#password", "value": TYPED}, _ctx(engine, tmp_path)
    )
    assert res.ok is False
    rows = _rows(engine)
    assert len(rows) == 1
    assert TYPED not in rows[0].action_json
    stored = json.loads(rows[0].action_json)
    assert stored["value"].startswith("***REDACTED*** (sha256:")
    assert cards and TYPED not in _flat(cards[0])
    assert cu.browser.typed == []


async def test_b5_an_approval_still_matches_the_same_text_and_the_real_text_is_typed(
    approvals, engine, tmp_path
):
    cu = CUContext(policy=_policy(), browser=FakeBrowser(LOGIN), approvals=approvals,
                   approval_resolver=None)
    tools = {t.name: t for t in computeruse_tools(cu)}
    ctx = _ctx(engine, tmp_path)
    await cu.browser.navigate("https://example.com/login")
    first = await tools["web_action"].execute(
        {"kind": "type", "css": "#password", "value": TYPED}, ctx
    )
    assert first.ok is False
    # A human approves the same request on the Browser page.
    denied = _rows(engine)[0]
    with session_scope(engine) as db:
        db.add(ApprovalRequest(run_id="r", action_json=denied.action_json,
                               reason=denied.reason, status="approved"))
        db.commit()
    # A DIFFERENT text is not covered by that approval.
    other = await tools["web_action"].execute(
        {"kind": "type", "css": "#password", "value": "test-other-value"}, ctx
    )
    assert other.ok is False and cu.browser.typed == []
    # The same text is, and the browser receives the real text.
    same = await tools["web_action"].execute(
        {"kind": "type", "css": "#password", "value": TYPED}, ctx
    )
    assert same.ok is True, same.error
    assert [t["value"] for t in cu.browser.typed] == [TYPED]
    assert all(TYPED not in r.action_json for r in _rows(engine))


def _route_client(approvals) -> TestClient:
    from types import SimpleNamespace

    app = FastAPI()
    d = SimpleNamespace(platform=SimpleNamespace(computeruse=SimpleNamespace(approvals=approvals)))
    cu_routes.register(app, d)
    return TestClient(app)


def test_b5_the_route_masks_the_typed_value_of_an_older_row(approvals, engine):
    old = {"kind": "type", "selector": {"css": "#password"}, "value": TYPED, "fallback": False}
    click = {"kind": "click", "selector": {"name": "Delete"}, "value": None, "fallback": False}
    with session_scope(engine) as db:
        db.add(ApprovalRequest(run_id="r", action_json=json.dumps(old),
                               reason="typing into a password field", status="pending"))
        db.add(ApprovalRequest(run_id="r", action_json=json.dumps(click),
                               reason="destructive", status="pending"))
        db.commit()
    body = _route_client(approvals).get("/computeruse/approvals").json()
    assert len(body["approvals"]) == 2
    assert TYPED not in json.dumps(body)
    by_kind = {json.loads(a["action_json"])["kind"]: json.loads(a["action_json"])
               for a in body["approvals"]}
    assert by_kind["type"]["value"].startswith("***REDACTED***")
    assert by_kind["type"]["selector"] == {"css": "#password"}
    assert by_kind["click"] == click


# --------------------------------------------------------------------------- #
# B7: where the isolated browser lands
# --------------------------------------------------------------------------- #

OFF_TEXT = "Off-list page body that must never be returned"


def _site_pages() -> dict[str, dict[str, Any]]:
    return {
        "https://example.com/go": {"redirect": "https://elsewhere.test/landing", "text": "hop"},
        "https://example.com/hop": {"redirect": "https://www.example.com/home", "text": "hop"},
        "https://www.example.com/home": {"text": "Welcome home", "a11y": []},
        "https://example.com/links": {
            "text": "A page with links",
            "a11y": [
                {"role": "link", "name": "Away", "navigate": "https://elsewhere.test/landing"},
                {"role": "link", "name": "Stay", "navigate": "https://example.com/next"},
            ],
        },
        "https://example.com/next": {"text": "The next page", "a11y": []},
        "https://elsewhere.test/landing": {
            "text": OFF_TEXT,
            "a11y": [{"role": "heading", "name": "Elsewhere", "text": OFF_TEXT}],
            "fields": [{"selector": "#q", "type": "text", "name": "Search"}],
        },
    }


def _cu(policy: ComputerUsePolicy, approvals, **kw: Any) -> tuple[CUContext, dict]:
    cu = CUContext(policy=policy, browser=FakeBrowser(_site_pages()), approvals=approvals, **kw)
    return cu, {t.name: t for t in computeruse_tools(cu)}


async def test_b7_a_redirect_off_the_list_is_refused_by_name_and_left(approvals, engine, tmp_path):
    cu, tools = _cu(_policy(), approvals)
    res = await tools["browse"].execute({"url": "https://example.com/go"}, _ctx(engine, tmp_path))
    assert res.ok is False
    assert res.error == (
        "That page sent the browser on to elsewhere.test, which is not on your list "
        "of allowed sites, so it was not read."
    )
    assert OFF_TEXT not in _flat([res.output, res.error, res.data])
    assert cu.browser.current_url == "about:blank"
    assert cu.last_screen is None, "the off-list page must not reach the live view"


async def test_b7_a_redirect_that_stays_on_the_list_works(approvals, engine, tmp_path):
    cu, tools = _cu(_policy(), approvals)
    res = await tools["browse"].execute({"url": "https://example.com/hop"}, _ctx(engine, tmp_path))
    assert res.ok is True, res.error
    assert "Welcome home" in res.output
    assert res.data["url"] == "https://www.example.com/home"


async def test_b7_a_click_that_navigates_off_the_list_is_refused_and_left(
    approvals, engine, tmp_path
):
    cu, tools = _cu(_policy(), approvals)
    ctx = _ctx(engine, tmp_path)
    ok = await tools["browse"].execute({"url": "https://example.com/links"}, ctx)
    assert ok.ok is True
    res = await tools["web_action"].execute({"kind": "click", "role": "link", "name": "Away"}, ctx)
    assert res.ok is False
    assert "elsewhere.test" in res.error
    assert "not on your list of allowed sites" in res.error
    assert OFF_TEXT not in _flat([res.output, res.error, res.data])
    assert "landing" not in _flat([res.output, res.data])
    assert cu.browser.current_url == "about:blank"
    # An on-list click is unchanged.
    await tools["browse"].execute({"url": "https://example.com/links"}, ctx)
    stay = await tools["web_action"].execute({"kind": "click", "role": "link", "name": "Stay"}, ctx)
    assert stay.ok is True and stay.data["url"] == "https://example.com/next"


async def test_b7_reads_and_typing_recheck_the_page_they_are_on(approvals, engine, tmp_path):
    async def _no_router():  # pragma: no cover - must never be reached
        raise AssertionError("the vision model was called on an off-list page")

    cu, tools = _cu(_policy(), approvals, router_resolver=lambda: _no_router)
    ctx = _ctx(engine, tmp_path)
    # Something (a timed redirect, an older call) left the browser off the list.
    await cu.browser.navigate("https://elsewhere.test/landing")
    extract = await tools["web_extract"].execute({"role": "heading", "name": "Elsewhere"}, ctx)
    assert extract.ok is False and "elsewhere.test" in extract.error
    assert OFF_TEXT not in _flat([extract.output, extract.error, extract.data])

    await cu.browser.navigate("https://elsewhere.test/landing")
    typed = await tools["web_action"].execute({"kind": "type", "css": "#q", "value": "a report"}, ctx)
    assert typed.ok is False and "elsewhere.test" in typed.error
    assert cu.browser.typed == []

    await cu.browser.navigate("https://elsewhere.test/landing")
    shots = cu.browser.screenshots
    look = await tools["web_look"].execute({}, ctx)
    assert look.ok is False and "elsewhere.test" in look.error
    assert cu.browser.screenshots == shots, "no screenshot of an off-list page"
    assert cu.browser.current_url == "about:blank"


async def test_b7_no_allowlist_set_is_unchanged(approvals, engine, tmp_path):
    cu, tools = _cu(_policy(domain_allowlist=[]), approvals)
    ctx = _ctx(engine, tmp_path)
    await cu.browser.navigate("https://elsewhere.test/landing")
    extract = await tools["web_extract"].execute({"role": "heading", "name": "Elsewhere"}, ctx)
    assert extract.ok is True and extract.data["raw"] == OFF_TEXT
    typed = await tools["web_action"].execute({"kind": "type", "css": "#q", "value": "a report"}, ctx)
    assert typed.ok is True
    assert cu.browser.current_url == "https://elsewhere.test/landing"
    # browse with no list is refused exactly as before (the typed address is checked).
    res = await tools["browse"].execute({"url": "https://example.com/next"}, ctx)
    assert res.ok is False and res.error.startswith("policy denied: domain not on allowlist")


# --------------------------------------------------------------------------- #
# Lead review: two more places the same values rested
# --------------------------------------------------------------------------- #


def test_the_run_trace_never_keeps_typed_text():
    """The trace is stored on the run and returned by the runs route."""
    from iron_jarvis.computeruse.base import Action, Selector
    from iron_jarvis.computeruse.trace import TraceRecorder

    trace = TraceRecorder()
    trace.record_action(Action(kind="type", selector=Selector(), value="test-pass-value"))
    kept = str(trace._events)
    assert "test-pass-value" not in kept and "REDACTED" in kept


def test_a_click_results_download_addresses_carry_no_query(tmp_path):
    """A browser tool's result is stored in the ledger and read by the model."""
    from iron_jarvis.browser.downloads import site_of

    link = "https://files.example.test/report.pdf?sig=test-code-value#frag"
    assert site_of(link) == "https://files.example.test/report.pdf"
    import inspect

    from iron_jarvis.browser import tools as T

    src = inspect.getsource(T).replace("\r\n", "\n")
    block = src[src.index('data["download"] = ('):src.index("def _approval_action")]
    assert 'for key in ("source_url", "final_url")' in block and "site_of(" in block
