"""Second review, finding S6 (v1.511.0), decision E4: Enter in a form field
is judged as a submit.

In a coding session's own page browser, ``page_type`` with ``submit: true``
types into a field and presses Enter, which submits the field's form. A
click on that form's "Delete account" button asks the user once, every time,
in every posture (``page_click``'s spend check over computer use's one
vocabulary, ``risky_click_word``); Enter in the field submitted the same
form with no card. Now ``page_type``'s spend check reads the words of the
button the submit would press (the field's form, its first submit button)
and applies the SAME rule: a button that reads as deleting, sending or
paying asks once; a field with no form, or a form whose button cannot be
read, asks once too (the page may do anything on Enter). Without ``submit``
nothing changes. The words are read again at the action, like a click's.

The fake browser of ``tests/test_build_browser_v1376.py`` drives the lanes;
the page-side reading itself is pinned on the real Edge when it is there.
"""

from __future__ import annotations

from typing import Any

import pytest

from iron_jarvis.harness import page_act as PA
from iron_jarvis.harness import page_browser as PB
from tests.test_build_browser_review_v1376 import REAL, Site, _browsers


#: What pressing Enter in each fake field would press (its form's submit
#: button), "" for a field with no form.
FORMS = {"Email": "Delete account", "Query": "Search", "Notes": ""}


def _submit_browsers(monkeypatch):
    """A fake AgentBrowsers whose browser knows each field's form button."""
    from tests.test_build_browser_v1376 import DEV, PAGES, FakeBackend

    class FormFake(FakeBackend):
        forms = dict(FORMS)

        async def submit_label(self, target):
            name = target if isinstance(target, str) else (target.get("name") or target.get("css"))
            if name not in PAGES.get(self._url, {}).get("fields", {}):
                raise PB.BrowserProblem(f"Nothing on the page matches {name!r}.")
            return self.forms.get(name, "")

    made: list[Any] = []

    def factory(**kw):
        backend = FormFake(**kw)
        made.append(backend)
        return backend

    fields = dict(PAGES[DEV]["fields"])
    fields["Query"] = {"tag": "input", "type": "search", "autocomplete": "", "words": "query"}
    monkeypatch.setitem(PAGES[DEV], "fields", fields)
    inst = PB.AgentBrowsers(backend_factory=factory)
    inst.made = made  # type: ignore[attr-defined]
    monkeypatch.setattr(PB, "BROWSERS", inst)
    return inst


async def _type_through_the_stream(app, args, mode, decide):
    from tests.test_approval_modes_v1188 import _body as mode_body
    from tests.test_approval_modes_v1188 import _calls_stream
    from tests.test_chat_approvals_v1187 import _drive_stream

    app.state.platform.router.stream = _calls_stream([("page_type", args)])
    return await _drive_stream(app, mode_body("type it", mode, tools=["page_type"]), decide)


#: The postures where a click on "Delete account" asks (every one but "Ask
#: before every action", which cards every typed entry anyway).
POSTURES = ["yolo", "approve_for_me", "accept_edits", "auto"]


@pytest.mark.parametrize("mode", POSTURES)
async def test_a_submit_into_a_form_whose_button_reads_delete_asks_once(tmp_path, monkeypatch, mode):
    from iron_jarvis.daemon.app import create_app
    from tests.test_build_browser_v1376 import DEV
    from tests.test_chat_approvals_v1187 import _asgi_post

    inst = _submit_browsers(monkeypatch)
    try:
        await inst.open("chat", DEV, preview=DEV)
        app = create_app(str(tmp_path / "home"))
        seen: list[dict] = []

        async def deny(data):
            seen.append(data)
            status, _ = await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "deny"})
            assert status == 200

        await _type_through_the_stream(app, {"target": "Email", "text": "a@b.test", "submit": True}, mode, deny)
        assert [d["tool"] for d in seen] == ["page_type"]
        assert seen[0].get("once_only") is True and seen[0].get("once_reason") == "page"
        note = seen[0].get("cost_note") or ""
        assert "Delete account" in note and "submit" in note, seen[0]
        assert inst.made[0].typed == []  # denied: nothing was typed or sent
    finally:
        inst.shutdown()


@pytest.mark.parametrize("mode", POSTURES)
async def test_a_submit_into_a_form_with_no_readable_button_asks_once(tmp_path, monkeypatch, mode):
    from iron_jarvis.daemon.app import create_app
    from tests.test_build_browser_v1376 import DEV
    from tests.test_chat_approvals_v1187 import _asgi_post

    inst = _submit_browsers(monkeypatch)
    try:
        await inst.open("chat", DEV, preview=DEV)
        app = create_app(str(tmp_path / "home"))
        seen: list[dict] = []

        async def deny(data):
            seen.append(data)
            status, _ = await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "deny"})
            assert status == 200

        await _type_through_the_stream(app, {"target": "Notes", "text": "hello", "submit": True}, mode, deny)
        assert [d["tool"] for d in seen] == ["page_type"]
        assert seen[0].get("once_only") is True
        assert inst.made[0].typed == []
    finally:
        inst.shutdown()


async def test_a_submit_into_a_search_form_does_not_ask_in_yolo(tmp_path, monkeypatch):
    from iron_jarvis.daemon.app import create_app
    from tests.test_build_browser_v1376 import DEV

    inst = _submit_browsers(monkeypatch)
    try:
        await inst.open("chat", DEV, preview=DEV)
        app = create_app(str(tmp_path / "home"))
        seen: list[dict] = []

        async def note(data):
            seen.append(data)

        await _type_through_the_stream(app, {"target": "Query", "text": "red shoes", "submit": True}, "yolo", note)
        assert seen == []
        assert inst.made[0].typed == [("Query", "red shoes", True)]
    finally:
        inst.shutdown()


@pytest.mark.parametrize("target", ["Email", "Notes"])
async def test_without_submit_typing_is_unchanged(tmp_path, monkeypatch, target):
    """No Enter, no submit: a field in a "Delete account" form, or one with
    no form, types with no card in yolo, exactly as before."""
    from iron_jarvis.daemon.app import create_app
    from tests.test_build_browser_v1376 import DEV

    inst = _submit_browsers(monkeypatch)
    try:
        await inst.open("chat", DEV, preview=DEV)
        app = create_app(str(tmp_path / "home"))
        seen: list[dict] = []

        async def note(data):
            seen.append(data)

        await _type_through_the_stream(app, {"target": target, "text": "x"}, "yolo", note)
        assert seen == []
        assert inst.made[0].typed == [(target, "x", False)]
    finally:
        inst.shutdown()


async def test_the_spend_check_words_say_it_submits_and_what_the_button_reads(tmp_path, monkeypatch):
    from tests.test_build_browser_v1376 import DEV, ctx

    inst = _submit_browsers(monkeypatch)
    try:
        await inst.open("build:s1", DEV, preview=DEV)
        tool = PA.PageTypeTool()
        risky = await tool.spend_check({"target": "Email", "text": "a@b.test", "submit": True}, ctx(tmp_path))
        assert risky is not None and risky.ask is True and risky.once_only is True
        assert "“Delete account”" in risky.words and "submit" in risky.words
        unknown = await tool.spend_check({"target": "Notes", "text": "x", "submit": "true"}, ctx(tmp_path))
        assert unknown is not None and unknown.ask is True and unknown.once_only is True
        assert "submit" in unknown.words
        assert await tool.spend_check({"target": "Query", "text": "x", "submit": True}, ctx(tmp_path)) is None
        assert await tool.spend_check({"target": "Email", "text": "x"}, ctx(tmp_path)) is None
        assert await tool.spend_check({"target": "Email", "text": "x", "submit": False}, ctx(tmp_path)) is None
    finally:
        inst.shutdown()


async def test_a_risky_submit_that_was_never_checked_or_changed_since_is_not_made(tmp_path, monkeypatch):
    """Read again AT the action, like a click: a submit the user was not
    asked about as it reads now is refused and nothing is typed."""
    from tests.test_build_browser_v1376 import DEV, ctx

    inst = _submit_browsers(monkeypatch)
    try:
        await inst.open("build:s1", DEV, preview=DEV)
        backend = inst.made[0]
        args = {"target": "Email", "text": "a@b.test", "submit": True}
        res = await PA.PageTypeTool().execute(args, ctx(tmp_path))
        assert res.ok is False and "was not asked" in res.error
        assert "Delete account" not in res.error  # never the page's own words
        # Checked while the button read "Save"; the page then changed it.
        backend.forms = {**FORMS, "Email": "Save"}
        assert await PA.PageTypeTool().spend_check(args, ctx(tmp_path)) is None
        backend.forms = dict(FORMS)
        res = await PA.PageTypeTool().execute(args, ctx(tmp_path))
        assert res.ok is False and "was not asked" in res.error
        assert backend.typed == []
        # Checked as it reads (the card), then typed and sent.
        check = await PA.PageTypeTool().spend_check(args, ctx(tmp_path))
        assert check is not None and check.ask is True
        res = await PA.PageTypeTool().execute(args, ctx(tmp_path))
        assert res.ok, res.error
        assert backend.typed == [("Email", "a@b.test", True)]
        # A harmless form needs no check; without submit nothing is read.
        assert (await PA.PageTypeTool().execute({"target": "Query", "text": "x", "submit": True}, ctx(tmp_path))).ok
        assert (await PA.PageTypeTool().execute({"target": "Notes", "text": "y"}, ctx(tmp_path))).ok
    finally:
        inst.shutdown()


def test_what_needs_asking_is_the_click_vocabulary_or_nothing_readable():
    assert PB.submit_needs_asking("Delete account")
    assert PB.submit_needs_asking("Pay now")
    assert PB.submit_needs_asking("")
    assert PB.submit_needs_asking("   ")
    assert not PB.submit_needs_asking("Search")
    assert not PB.submit_needs_asking("Save")


FORM_PAGE = """<html><head><title>Admin</title></head><body>
<p id=out>nothing yet</p>
<form id=danger onsubmit="event.preventDefault();document.getElementById('out').textContent='account deleted'">
  <label for=reason id=lab>Reason</label><input id=reason>
  <button type=button>Cancel</button>
  <button id=go>Delete <span>account</span></button>
</form>
<form onsubmit="event.preventDefault();document.getElementById('out').textContent='searched'">
  <input id=q placeholder="Search"><input type=submit value="Search">
</form>
<form><input id=lone></form>
<input id=loose placeholder="Loose">
</body></html>"""


@REAL
@pytest.mark.timeout(180)
async def test_real_edge_reads_the_button_a_submit_would_press():
    """The page side: the field's form, its first submit button (a type=button
    before it is skipped), read with the click path's own words — through a
    <label> target too. No form, or no submit button: nothing readable."""
    site = Site({"/": FORM_PAGE})
    b = _browsers()
    key = "build:submit"
    try:
        await b.open(key, site.base, preview=site.base)
        assert await b.submit_words(key, {"css": "#reason"}) == "Delete account"
        assert await b.submit_words(key, {"css": "#lab"}) == "Delete account"
        assert await b.submit_words(key, {"css": "#q"}) == "Search"
        assert await b.submit_words(key, {"css": "#lone"}) == ""
        assert await b.submit_words(key, {"css": "#loose"}) == ""
        b._checked.clear()  # as if no check had run
        with pytest.raises(PB.BrowserProblem, match="was not asked"):
            await b.type(key, {"css": "#reason"}, "moving on", submit=True, preview=site.base)
        assert "nothing yet" in (await b.read(key, preview=site.base)).text
        await b.submit_words(key, {"css": "#reason"})  # the card's check
        await b.type(key, {"css": "#reason"}, "moving on", submit=True, preview=site.base)
        assert "account deleted" in (await b.read(key, preview=site.base)).text
        await b.type(key, {"css": "#q"}, "red shoes", submit=True, preview=site.base)  # harmless
        assert "searched" in (await b.read(key, preview=site.base)).text
    finally:
        b.shutdown()
        site.close()
