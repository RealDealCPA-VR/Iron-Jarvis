"""Browser error codes and their model-actionable remedies (D15).

Every failure the Browser capability can produce is one of the seventeen codes
below, and every code carries a remedy sentence written *for the model that just
failed* — the next action it should take, named. D15's rule is the whole point of
this module: "do not return opaque generic failures where a recovery action is
known."

The silent failure this file exists to prevent: a tool that answers
``{"ok": false, "error": "browser error"}``. A model reading that has three bad
options — retry the identical call, invent a different call, or tell the user
something is broken — and in this repository the third one has cost a release
before (a truncated listing read as complete, and the model then reported that a
file did not exist). A code plus a remedy turns each of those into one correct
next step: "call browser_read_page and retry with the new element ID."

Wire shape is a two-key envelope, ``{"code", "message"}``, matching the response
frame of :mod:`iron_jarvis.browser.protocol` verbatim. Nothing else is ever put
on the wire for a failure: an extra field here becomes an extra field every
consumer of the protocol — the extension included — has to learn.
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class BrowserErrorCode(str, Enum):
    """The seventeen browser error codes of D15.

    ``str`` mixin, so a code JSON-serialises as its own value and a comparison
    against a plain string read off the wire succeeds. That mirrors
    :class:`~iron_jarvis.core.models.PermissionMode` and every other enum this
    repository puts into a payload: a bare :class:`Enum` renders as
    ``"BrowserErrorCode.STALE_ELEMENT"`` inside an f-string and would silently
    ship that to the model.
    """

    BROWSER_NOT_CONNECTED = "BROWSER_NOT_CONNECTED"
    BROWSER_ACCESS_OFF = "BROWSER_ACCESS_OFF"
    READ_ONLY_MODE = "READ_ONLY_MODE"
    TAB_NOT_FOUND = "TAB_NOT_FOUND"
    PAGE_NOT_READY = "PAGE_NOT_READY"
    ELEMENT_NOT_FOUND = "ELEMENT_NOT_FOUND"
    STALE_ELEMENT = "STALE_ELEMENT"
    STALE_SNAPSHOT = "STALE_SNAPSHOT"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    ACTION_TIMEOUT = "ACTION_TIMEOUT"
    NAVIGATION_FAILED = "NAVIGATION_FAILED"
    DOWNLOAD_FAILED = "DOWNLOAD_FAILED"
    UNSUPPORTED_PAGE = "UNSUPPORTED_PAGE"
    EXTENSION_ERROR = "EXTENSION_ERROR"
    PAIRING_REQUIRED = "PAIRING_REQUIRED"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    CONNECTION_REPLACED = "CONNECTION_REPLACED"


#: Remedy sentence per code, in the enum's own order.
#:
#: Each value is a :meth:`str.format` template. Placeholders are filled by
#: :func:`browser_error`; an *unfilled* placeholder is left as written rather
#: than raising, because a formatting mistake on an error path would replace a
#: useful failure with a ``KeyError`` traceback — the exact opaque outcome D15
#: forbids. The §7.1 vocabulary rule applies to these strings: the surface is
#: always "the Browser page in Iron Jarvis", and the Chrome add-on is named only
#: where Chrome's own UI already uses that word.
REMEDIES: dict[BrowserErrorCode, str] = {
    BrowserErrorCode.BROWSER_NOT_CONNECTED: (
        "Your browser is not connected to Iron Jarvis. Ask the user to open the "
        "Browser page in Iron Jarvis and pair their browser, then retry."
    ),
    BrowserErrorCode.BROWSER_ACCESS_OFF: (
        "Browser access is off. Ask the user to set Browser access to Read only "
        "or Interactive on the Browser page in Iron Jarvis, then retry."
    ),
    BrowserErrorCode.READ_ONLY_MODE: (
        "Browser access is Read only, so this tool cannot change the page. The "
        "reading tools still work; ask the user to switch Browser access to "
        "Interactive if you should act on the page."
    ),
    BrowserErrorCode.TAB_NOT_FOUND: (
        "No tab {tab_id} is open. Call browser_list_tabs and retry with an id "
        "from that list."
    ),
    BrowserErrorCode.PAGE_NOT_READY: (
        "Tab {tab_id} has not finished loading. Call browser_read_page again "
        "before acting on it."
    ),
    # Verbatim from the implementation plan, section 9.3.
    BrowserErrorCode.ELEMENT_NOT_FOUND: (
        "No element {element_id} in snapshot {snapshot_id}. Call "
        "browser_read_page to list what is on the page now."
    ),
    # Verbatim from the implementation plan, section 9.3 — do not reword.
    BrowserErrorCode.STALE_ELEMENT: (
        "The page changed after the previous snapshot. Call browser_read_page "
        "and retry using the new element ID."
    ),
    # Verbatim from the implementation plan, section 9.3 — do not reword.
    BrowserErrorCode.STALE_SNAPSHOT: (
        "No current snapshot for this tab. Call browser_read_page and retry "
        "with the new element ID."
    ),
    # Verbatim from the implementation plan, section 6 (host permission absent).
    BrowserErrorCode.PERMISSION_DENIED: (
        "Site access has not been granted to the Iron Jarvis browser add-on. "
        "Open the Browser page in Iron Jarvis and press Grant site access."
    ),
    BrowserErrorCode.ACTION_TIMEOUT: (
        "Your browser did not answer in time. Call browser_get_status to check "
        "the connection, then retry the call once."
    ),
    BrowserErrorCode.NAVIGATION_FAILED: (
        "Navigation to {url} failed. Check the address, then retry once or ask "
        "the user to open the page themselves."
    ),
    BrowserErrorCode.DOWNLOAD_FAILED: (
        "The download did not complete. Ask the user to check their browser's "
        "downloads, then retry."
    ),
    # Verbatim from the implementation plan, section 9.7.
    BrowserErrorCode.UNSUPPORTED_PAGE: (
        "{scheme} pages are closed to add-ons by Chrome. Ask the user to switch "
        "to a normal tab."
    ),
    BrowserErrorCode.EXTENSION_ERROR: (
        "Your browser reported an error: {detail}. Call browser_get_status; if "
        "it persists, ask the user to reload the add-on from chrome://extensions."
    ),
    BrowserErrorCode.PAIRING_REQUIRED: (
        "This browser is not paired with Iron Jarvis. Ask the user to open the "
        "Browser page in Iron Jarvis and press Pair."
    ),
    BrowserErrorCode.AUTHENTICATION_FAILED: (
        "Your browser's pairing was refused. Ask the user to press Forget on "
        "the Browser page in Iron Jarvis and pair the browser again."
    ),
    BrowserErrorCode.CONNECTION_REPLACED: (
        "Another browser connected and replaced this one, so this call was "
        "abandoned rather than left hanging. Call browser_get_status and retry "
        "against the connected browser."
    ),
}


def remedy(code: BrowserErrorCode | str, **fmt: Any) -> str:
    """The remedy sentence for ``code``, with ``fmt`` substituted where asked.

    Never raises. An unknown code and a missing placeholder both degrade to
    something a model can still act on, because this function runs *inside*
    failure handling: raising here would turn a recoverable browser failure into
    a 500 and lose the code the caller had already worked out.
    """
    try:
        key = BrowserErrorCode(code)
    except ValueError:
        return f"Browser call failed: {code}."
    template = REMEDIES.get(key, "")
    if not fmt:
        return template
    try:
        return template.format(**fmt)
    except (KeyError, IndexError, ValueError):
        # A caller that forgot a placeholder still gets the generic remedy,
        # which names the right next call even without the specifics.
        return template


def browser_error(code: BrowserErrorCode | str, **fmt: Any) -> dict[str, str]:
    """The ``{"code", "message"}`` envelope for the wire and for a tool result.

    One builder, so the daemon side, the browser tools and the extension's own
    ``errors.ts`` cannot drift into two shapes. Two keys exactly: a third key
    added here would have to be understood by the extension, the response frame,
    the tool result and the ledger.
    """
    key = code.value if isinstance(code, BrowserErrorCode) else str(code)
    return {"code": key, "message": remedy(code, **fmt)}


class BrowserError(Exception):
    """A browser failure carrying its :class:`BrowserErrorCode`.

    Every :class:`~iron_jarvis.browser.service.BrowserService` method raises this
    rather than returning ``None`` for a failure. The reason is the v1.229.0
    lesson restated: a sentinel return is indistinguishable from "nothing
    happened", so a caller that forgets to check it reports success. An exception
    carrying a code cannot be silently ignored, and :meth:`envelope` hands the
    tool layer the exact wire shape with no second formatting step.
    """

    def __init__(
        self,
        code: BrowserErrorCode | str,
        message: str = "",
        **fmt: Any,
    ) -> None:
        self.code: str = code.value if isinstance(code, BrowserErrorCode) else str(code)
        #: The remedy the model reads. An explicit ``message`` wins, so a caller
        #: that knows more than the template (the extension's own words, say) can
        #: say it without inventing a new code.
        self.message: str = message or remedy(code, **fmt)
        self.fmt: dict[str, Any] = dict(fmt)
        super().__init__(f"{self.code}: {self.message}")

    def envelope(self) -> dict[str, str]:
        """The ``{"code", "message"}`` envelope for a response frame or tool result."""
        return {"code": self.code, "message": self.message}


__all__ = [
    "REMEDIES",
    "BrowserError",
    "BrowserErrorCode",
    "browser_error",
    "remedy",
]
