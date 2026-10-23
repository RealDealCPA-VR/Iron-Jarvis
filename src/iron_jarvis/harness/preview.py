"""The page the user's Browser pane is showing, for the turn being answered
(v1.371.0).

The IJ harness screen sends the address its Browser pane shows with every
message (``POST /build/sessions/{id}/turn`` ``preview_url``). The build route
validates it and sets :data:`PREVIEW_ADDRESS` while the turn's driver task is
created (the task copies the context, like the account pin), so:

* the harness prompt names that page (:func:`prompt_line`), and
* ``page_screenshot`` looks at it WITHOUT asking — the user chose that page
  by opening it — while any other address on this PC or the user's network
  asks first, because a screenshot goes to the turn's model and a local page
  can hold anything (another app's admin page, client names).

Only an address on this PC or the user's own network is ever accepted
(:func:`local_address`, the egress module's one definition of "private").
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from urllib.parse import urlsplit

from ..providers.egress import current_scope, private_host

#: The Browser pane's address for this turn; "" = none was sent.
PREVIEW_ADDRESS: ContextVar[str] = ContextVar("ij_preview_address", default="")

#: Longest address accepted from the screen.
MAX_ADDRESS_CHARS = 2000


def local_address(url: str) -> str:
    """``url`` cleaned, when it is an http(s) address on this PC or the user's
    own network; "" for anything else (a public site, another scheme, an
    address with a user name or password in it, garbage). Never raises."""
    try:
        raw = str(url or "").strip()
        if not raw or len(raw) > MAX_ADDRESS_CHARS:
            return ""
        parts = urlsplit(raw)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return ""
        if parts.username or parts.password:
            return ""
        if parts.hostname == "0.0.0.0":
            # A dev server that listens on every address prints
            # "http://0.0.0.0:3000": a browser opens that as this PC (the
            # Browser pane does the same, lib/buildSessions.addressesIn).
            port = f":{parts.port}" if parts.port else ""
            raw = parts._replace(netloc=f"localhost{port}").geturl()
        if not private_host(raw):
            return ""
        return raw
    except Exception:  # noqa: BLE001 — unreadable is not local
        return ""


def origin(url: str) -> str:
    """``scheme://host:port`` of a local address ("" when it is not one): the
    dev server, whatever page of it is open."""
    clean = local_address(url)
    if not clean:
        return ""
    parts = urlsplit(clean)
    host = (parts.hostname or "").lower()
    if host in ("0.0.0.0", "127.0.0.1", "::1"):
        host = "localhost"
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        return ""
    return f"{parts.scheme}://{host}:{port}"


def set_preview(url: str) -> Token[str]:
    """Arm this turn's preview address (a cleaned local address or "")."""
    return PREVIEW_ADDRESS.set(local_address(url))


def reset_preview(token: Token[str]) -> None:
    PREVIEW_ADDRESS.reset(token)


def current_preview() -> str:
    return PREVIEW_ADDRESS.get()


def is_preview_page(url: str) -> bool:
    """True when ``url`` is on the same dev server the Browser pane shows."""
    shown = origin(current_preview())
    return bool(shown) and origin(url) == shown


def prompt_line(armed: "set[str] | frozenset[str]") -> str:
    """One sentence for the harness prompt naming the page the user is
    looking at — only when there is one AND the tool that can look at it is
    armed (a brief that promises a tool the turn does not have is a lie)."""
    shown = current_preview()
    if not shown or "page_screenshot" not in armed:
        return ""
    try:
        local_only = bool(current_scope().on)
    except Exception:  # noqa: BLE001 — unknown reads as the stricter words
        local_only = True
    if "page_open" in armed:
        # v1.376.0: the agent's own browser (harness/page_act).
        ask = (
            "in this session every page opened, click and typed entry asks the user first."
            if local_only else
            "you can work on that dev server without asking. Any other address on this PC or "
            "network asks the user first."
        )
        return (
            f"\n\nThe user's Browser pane is showing {shown}. To check your work there, open it in "
            "your own browser with page_open, then use page_click, page_type and page_read (the "
            "visible text, console errors and failed requests) and page_screenshot to look; "
            + ask
        )
    if local_only:
        # v1.371.0 review: a local-only turn cards EVERY page_screenshot
        # (egress.OPENS_PAGES_TOOLS) — the line must not promise otherwise.
        return (
            f"\n\nThe user's Browser pane is showing {shown}. To see how a page there "
            "looks (after a change, or when the user asks about it), call page_screenshot; "
            "in this session every screenshot asks the user first."
        )
    return (
        f"\n\nThe user's Browser pane is showing {shown}. To see how a page there "
        "looks (after a change, or when the user asks about it), call page_screenshot; "
        "you can look at that dev server without asking. Any other address on this "
        "PC or network asks the user first."
    )
