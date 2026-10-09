"""v1.322.0 — web_fetch SSRF: DNS is vetted and redirects are walked by hand.

THE DEFECTS, read in ``tools/webfetch.py`` before the fix:

* the guard checked only the URL's host STRING ("we do not resolve DNS here"),
  so ``http://anything.example/`` whose A record is 127.0.0.1 was fetched;
* ``httpx.Client(follow_redirects=True)`` followed a 302 to an internal address
  and SENT that request; the post-redirect check only refused the RESPONSE.

These tests drive the REAL production fetch (``WebFetchTool._default_http_get``
→ ``_vet_url`` → ``_send_pinned`` → a real ``httpx.Client``); the fakes sit at
the I/O edge only: the DNS resolver (``host -> [ip]``) and the network
(``httpx.MockTransport``, which records every request httpx really sent).
"""

from __future__ import annotations

import threading
from pathlib import Path

import httpx
import pytest

from iron_jarvis.core.db import init_db, make_engine
from iron_jarvis.core.events import EventBus
from iron_jarvis.tools import webfetch
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.webfetch import WebFetchTool

_PAGE = "<html><head><title>Public page</title></head><body><p>Hello world.</p></body></html>"

#: The fake DNS: hostname -> the addresses it "resolves" to.
_DNS = {
    "public.example": ["93.184.216.34"],
    "www.public.example": ["93.184.216.35"],
    "rebind.example": ["127.0.0.1"],
    "intranet.example": ["10.0.0.7"],
    "metadata.example": ["169.254.169.254"],
    "mixed.example": ["93.184.216.34", "192.168.1.20"],
    "v6loop.example": ["::1"],
    "v6mapped.example": ["::ffff:127.0.0.1"],
    "cgnat.example": ["100.100.100.200"],
}


@pytest.fixture
def ctx(tmp_path: Path) -> ToolContext:
    engine = make_engine(str(tmp_path / "ws.db"))
    init_db(engine)
    return ToolContext(
        workspace=tmp_path, session_id="s1", agent_run_id="r1",
        config=None, event_bus=EventBus(), engine=engine,
    )


@pytest.fixture(autouse=True)
def _no_proxy(monkeypatch):
    """No proxy, so the connection is always pinned (the proxied branch has
    its own test below)."""
    monkeypatch.setattr(webfetch, "_proxied", lambda scheme, host: False)


class _Net:
    """The network edge: records each request httpx sent; ``routes`` maps the
    LOGICAL host (the Host header) to a response factory."""

    def __init__(self, routes: dict):
        self.routes = routes
        self.sent: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.sent.append(request)
        host = request.headers["host"].split(":")[0]
        return self.routes[host](request)


class _Resolver:
    def __init__(self):
        self.calls: list[str] = []
        self.threads: list[threading.Thread] = []

    def __call__(self, host: str) -> list[str]:
        self.calls.append(host)
        self.threads.append(threading.current_thread())
        return list(_DNS.get(host, []))


def _page(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "text/html"}, text=_PAGE)


def _redirect(to: str):
    return lambda request: httpx.Response(302, headers={"location": to})


def _tool(net: _Net, resolver: _Resolver) -> WebFetchTool:
    return WebFetchTool(resolver=resolver, transport=httpx.MockTransport(net.handler))


@pytest.mark.parametrize(
    "host",
    ["rebind.example", "intranet.example", "metadata.example", "mixed.example",
     "v6loop.example", "v6mapped.example", "cgnat.example"],
)
async def test_a_public_looking_hostname_resolving_inward_is_refused_before_any_request(ctx, host):
    net = _Net({host: _page})
    resolver = _Resolver()
    res = await _tool(net, resolver).execute({"url": f"http://{host}/admin"}, ctx)
    assert res.ok is False
    assert res.error.startswith("refused: "), res.error
    assert host in res.error
    assert resolver.calls == [host]
    assert net.sent == [], "a request reached the network before the refusal"


async def test_dns_is_resolved_off_the_event_loop(ctx):
    net = _Net({"public.example": _page})
    resolver = _Resolver()
    loop_thread = threading.current_thread()
    res = await _tool(net, resolver).execute({"url": "https://public.example/"}, ctx)
    assert res.ok is True, res.error
    assert resolver.threads and all(t is not loop_thread for t in resolver.threads)


async def test_a_redirect_to_an_internal_address_is_refused_without_sending_it(ctx):
    net = _Net({
        "public.example": _redirect("http://intranet.example/secrets"),
        "intranet.example": _page,
    })
    resolver = _Resolver()
    res = await _tool(net, resolver).execute({"url": "https://public.example/go"}, ctx)
    assert res.ok is False
    assert res.error.startswith("refused after redirect: "), res.error
    assert res.data["url"] == "http://intranet.example/secrets"
    # Only the FIRST request was ever sent; the internal one never left.
    assert [r.headers["host"] for r in net.sent] == ["public.example"]


async def test_a_redirect_to_a_literal_metadata_ip_is_refused_without_sending_it(ctx):
    net = _Net({"public.example": _redirect("http://169.254.169.254/latest/meta-data/")})
    resolver = _Resolver()
    res = await _tool(net, resolver).execute({"url": "https://public.example/"}, ctx)
    assert res.ok is False
    assert res.error.startswith("refused after redirect: "), res.error
    assert len(net.sent) == 1
    assert all(r.url.host != "169.254.169.254" for r in net.sent)


async def test_a_public_redirect_is_followed_hop_by_hop_and_the_final_name_reported(ctx):
    net = _Net({
        "public.example": _redirect("/landing"),
        "www.public.example": _page,
    })
    # A relative Location resolves against the LOGICAL URL (the name), then a
    # second hop moves to another public host.
    net.routes["public.example"] = (
        lambda request: _redirect("https://www.public.example/final")(request)
        if request.url.path == "/landing" else _redirect("/landing")(request)
    )
    resolver = _Resolver()
    res = await _tool(net, resolver).execute({"url": "https://public.example/"}, ctx)
    assert res.ok is True, res.error
    assert res.data["url"] == "https://www.public.example/final"
    assert [(r.headers["host"], r.url.path) for r in net.sent] == [
        ("public.example", "/"), ("public.example", "/landing"),
        ("www.public.example", "/final"),
    ]
    assert resolver.calls == ["public.example", "public.example", "www.public.example"]


async def test_too_many_redirects_is_an_honest_error(ctx):
    net = _Net({"public.example": _redirect("/again")})
    res = await _tool(net, _Resolver()).execute({"url": "https://public.example/"}, ctx)
    assert res.ok is False
    assert "TooManyRedirects" in res.error
    assert len(net.sent) == webfetch._MAX_REDIRECTS + 1


async def test_the_connection_is_pinned_to_the_vetted_ip_with_the_real_name_kept(ctx):
    net = _Net({"public.example": _page})
    res = await _tool(net, _Resolver()).execute({"url": "https://public.example:8443/a?b=1"}, ctx)
    assert res.ok is True, res.error
    (sent,) = net.sent
    # The socket goes to the address that was vetted — DNS is not asked again.
    assert sent.url.host == "93.184.216.34"
    assert sent.url.port == 8443 and sent.url.path == "/a" and sent.url.query == b"b=1"
    # ...while HTTP and TLS still speak to the NAME (Host header; SNI + cert check).
    assert sent.headers["host"] == "public.example:8443"
    assert sent.extensions.get("sni_hostname") == "public.example"
    # What the user sees is the name, never the pinned IP.
    assert res.data["url"] == "https://public.example:8443/a?b=1"


async def test_through_a_proxy_the_url_is_not_pinned_but_dns_is_still_vetted(ctx, monkeypatch):
    monkeypatch.setattr(webfetch, "_proxied", lambda scheme, host: True)
    net = _Net({"public.example": _page})
    resolver = _Resolver()
    res = await _tool(net, resolver).execute({"url": "https://public.example/"}, ctx)
    assert res.ok is True, res.error
    assert net.sent[0].url.host == "public.example"
    assert resolver.calls == ["public.example"]
    bad = await _tool(net, resolver).execute({"url": "https://rebind.example/"}, ctx)
    assert bad.ok is False and bad.error.startswith("refused: ")


@pytest.mark.parametrize(
    "url",
    ["http://[::ffff:127.0.0.1]/", "http://[::ffff:a9fe:a9fe]/", "http://100.64.0.1/",
     "http://[2002:7f00:1::]/"],
)
async def test_mapped_and_non_global_literals_are_refused_before_io(ctx, url):
    net = _Net({})
    resolver = _Resolver()
    res = await _tool(net, resolver).execute({"url": url}, ctx)
    assert res.ok is False and res.error.startswith("refused: "), res.error
    assert net.sent == [] and resolver.calls == []
