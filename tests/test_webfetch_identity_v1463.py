"""v1.463.0: web_fetch says what it is first, and a refused page says so.

Measured on this PC against 90 ordinary sites (2026-11-09): a request that
only claims to be Chrome read 61 of them; one that says what it is read 70;
the two together 71. The live record held 14 failed fetches out of 26.

Sites that check (Wikipedia, cdc.gov, dol.gov) refuse or stall a client that
claims to be a browser and is not. So the honest name goes first, the browser
name is the ONE retry, and a page both names are refused says that asking
again will not help.

Driven through the real fetch path (the tool, DNS vetting, the pinned send, a
real ``httpx.Client``) with the network replaced at the transport.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from iron_jarvis.tools import webfetch
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.webfetch import WebFetchTool

_PAGE = "<html><head><title>Public page</title></head><body><p>Hello world.</p></body></html>"
_IP = "93.184.216.34"


@pytest.fixture
def ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(
        workspace=tmp_path, session_id="s1", agent_run_id="r1",
        config=None, event_bus=None, engine=None,
    )


@pytest.fixture(autouse=True)
def _no_proxy(monkeypatch):
    monkeypatch.setattr(webfetch, "_proxied", lambda scheme, host: False)


class _Site:
    """A site that answers by WHO is asking. ``serves`` says which names get
    the page; any other name gets ``otherwise`` (a status, or an exception)."""

    def __init__(self, serves=("honest", "browser"), otherwise=403):
        self.serves = serves
        self.otherwise = otherwise
        self.asked: list[str] = []
        self.sent: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.sent.append(request)
        ua = request.headers.get("user-agent", "")
        who = "browser" if ua == webfetch._UA else "honest" if ua == webfetch._HONEST_UA else "other"
        self.asked.append(who)
        if who in self.serves:
            return httpx.Response(200, headers={"content-type": "text/html"}, text=_PAGE)
        if isinstance(self.otherwise, Exception):
            raise self.otherwise
        return httpx.Response(self.otherwise, text="no")


def _tool(site: _Site, dns=None) -> WebFetchTool:
    return WebFetchTool(
        resolver=lambda host: list((dns or {"site.example": [_IP]}).get(host, [])),
        transport=httpx.MockTransport(site.handler),
    )


async def _fetch(site: _Site, ctx, url="https://site.example/page"):
    return await _tool(site).execute({"url": url}, ctx)


# ----------------------------------------------------------- who goes first --


async def test_the_honest_name_goes_first_and_one_request_is_enough(ctx):
    site = _Site()
    res = await _fetch(site, ctx)
    assert res.ok is True, res.error
    assert site.asked == ["honest"]


async def test_the_honest_name_says_nothing_about_the_user_or_this_pc():
    import getpass
    import socket

    ua = webfetch._HONEST_UA
    assert ua.startswith("IronJarvis/")
    for private in (getpass.getuser(), socket.gethostname(), "@"):
        assert private.lower() not in ua.lower()


async def test_every_request_asks_for_a_page_the_way_a_reader_does(ctx):
    site = _Site(serves=("browser",))
    await _fetch(site, ctx)
    assert len(site.sent) == 2
    for sent in site.sent:
        assert sent.headers["accept"].startswith("text/html")
        assert sent.headers["accept-language"].startswith("en")


# ------------------------------------------------------------ the one retry --


@pytest.mark.parametrize("status", [401, 403, 406, 429, 202])
async def test_a_site_that_refuses_the_honest_name_is_asked_once_as_a_browser(ctx, status):
    site = _Site(serves=("browser",), otherwise=status)
    res = await _fetch(site, ctx)
    assert res.ok is True, res.error
    assert "Hello world." in res.output
    assert site.asked == ["honest", "browser"]


@pytest.mark.parametrize(
    "stall",
    [httpx.ReadTimeout("stalled"), httpx.RemoteProtocolError("dropped"), httpx.ReadError("cut")],
)
async def test_a_site_that_stalls_or_drops_one_name_is_asked_as_the_other(ctx, stall):
    site = _Site(serves=("browser",), otherwise=stall)
    res = await _fetch(site, ctx)
    assert res.ok is True, res.error
    assert site.asked == ["honest", "browser"]


async def test_never_more_than_two_requests_for_one_page(ctx):
    site = _Site(serves=(), otherwise=403)
    await _fetch(site, ctx)
    assert site.asked == ["honest", "browser"]


@pytest.mark.parametrize("status", [404, 410, 500, 503])
async def test_a_missing_or_broken_page_is_not_asked_for_twice(ctx, status):
    """Not about who is asking: the second name would get the same answer."""
    site = _Site(serves=(), otherwise=status)
    res = await _fetch(site, ctx)
    assert res.ok is False
    assert site.asked == ["honest"]
    assert res.error == f"HTTP {status} fetching https://site.example/page"


async def test_a_host_that_cannot_be_reached_is_not_tried_twice(ctx):
    site = _Site(serves=(), otherwise=httpx.ConnectError("no route"))
    res = await _fetch(site, ctx)
    assert res.ok is False and "ConnectError" in res.error
    assert site.asked == ["honest"]


async def test_an_inward_address_is_refused_with_no_request_under_either_name(ctx):
    site = _Site()
    tool = _tool(site, dns={"site.example": ["127.0.0.1"]})
    res = await tool.execute({"url": "https://site.example/admin"}, ctx)
    assert res.ok is False and res.error.startswith("refused: ")
    assert site.sent == []


async def test_the_retry_is_vetted_and_pinned_like_the_first_request(ctx):
    site = _Site(serves=("browser",))
    calls: list[str] = []

    def resolver(host):
        calls.append(host)
        return [_IP]

    tool = WebFetchTool(resolver=resolver, transport=httpx.MockTransport(site.handler))
    res = await tool.execute({"url": "https://site.example/page"}, ctx)
    assert res.ok is True
    assert calls == ["site.example", "site.example"]
    for sent in site.sent:
        assert sent.url.host == _IP
        assert sent.headers["host"] == "site.example"
        assert sent.extensions.get("sni_hostname") == "site.example"


# ------------------------------------------------------- what a refusal says --


@pytest.mark.parametrize("status", [401, 403, 429])
async def test_a_page_refused_under_both_names_says_asking_again_will_not_help(ctx, status):
    site = _Site(serves=(), otherwise=status)
    res = await _fetch(site, ctx)
    assert res.ok is False
    assert res.error == (
        f"HTTP {status} fetching https://site.example/page."
        " The site refuses automated readers, so asking for this address again"
        " will not help. Use another source or web_search for the fact, or ask"
        " the user to open the page."
    )
    assert res.data == {"url": "https://site.example/page", "status": status}


async def test_a_stall_under_both_names_reports_the_first_failure(ctx):
    site = _Site(serves=(), otherwise=httpx.ReadTimeout("stalled"))
    res = await _fetch(site, ctx)
    assert res.ok is False
    assert res.error == "ReadTimeout: stalled"
    assert site.asked == ["honest", "browser"]


async def test_a_refusal_then_a_stall_reports_the_refusal(ctx):
    """The honest name got an answer; the browser name got none. The answer
    is what the model is told."""

    class _Mixed(_Site):
        def handler(self, request):
            self.sent.append(request)
            if request.headers["user-agent"] == webfetch._HONEST_UA:
                self.asked.append("honest")
                return httpx.Response(403, text="no")
            self.asked.append("browser")
            raise httpx.ReadTimeout("stalled")

    site = _Mixed()
    res = await _fetch(site, ctx)
    assert res.ok is False
    assert res.error.startswith("HTTP 403 fetching https://site.example/page.")
    assert site.asked == ["honest", "browser"]


async def test_two_different_refusals_report_the_first_one(ctx):
    class _Two(_Site):
        def handler(self, request):
            self.sent.append(request)
            honest = request.headers["user-agent"] == webfetch._HONEST_UA
            return httpx.Response(403 if honest else 429, text="no")

    res = await _fetch(_Two(), ctx)
    assert res.ok is False
    assert res.data["status"] == 403
