"""The agent's browser tools (v1.376.0, IJ harness part 2, Phase 5):
``page_open``, ``page_click``, ``page_type`` and ``page_read``.

They drive ONE invisible Edge per Build session (``harness/page_browser``:
the lifecycle, the processes, the address rules, the thread). Neither Claude
Code nor Codex has a built-in browser for a local dev server (Codex's ``web``
tool browses the public web, which is ``web_search`` / ``web_fetch`` here),
so the four pass through every dialect under these names.

WHO DECIDES WHAT THEY MAY DO:

* ``page_open`` is ASK tier with its own spend check (``page_screenshot``'s
  rule, v1.371.0): the page the Browser pane shows, or an address the user
  already allowed for this browser, opens without a card; any other address
  on this PC or the user's network asks ("Allow once" — allowed, it stays
  allowed until the browser closes); a public site is refused in words.
* ``page_click`` / ``page_type`` / ``page_read`` are ALLOW tier: they act on
  the page the browser is on, and the browser is only ever on a page the
  rules opened (every navigation is checked, ``page_browser``). The user's
  permission table can still make them ask or deny.
* Local only (``providers/egress.OPENS_PAGES_TOOLS``): ``page_open``,
  ``page_click`` and ``page_type`` card EVERY call (a page loads what it
  names); ``page_read`` reads what is already loaded.
* Low trust refuses all four (``core/trust.LOW_TRUST_DENY``).
* ``page_type`` never types into a password field, a payment field or a
  file picker, and never types text that looks like a key (refused in
  words, not carded — ``page_browser.typing_refusal``).

Every result is the page's own text, so every tool is
``returns_untrusted_content`` (the lanes fence it and scan it for
instructions). A Stop closes the browser at once (:func:`run_action`).
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Awaitable, Callable

from ..tools.base import (
    Reversibility,
    RiskClass,
    SpendCheck,
    Tool,
    ToolContext,
    ToolResult,
)
from . import page_browser as PB
from .preview import current_preview, is_preview_page, local_address

#: The longest visible text a read hands the model.
READ_CHARS = PB.MAX_TEXT_CHARS
#: How much of the page an OPEN shows the model (page_read has the rest).
OPEN_TEXT_CHARS = 600


def _turn_handle(ctx: Any) -> Any:
    """The running turn's handle (its Stop), or None."""
    turn_id = str(getattr(ctx, "turn_id", "") or "")
    if not turn_id:
        return None
    try:
        from ..core.turns import TURNS

        return TURNS.handle(turn_id)
    except Exception:  # noqa: BLE001
        return None


def _close_in_background(key: str, reason: str) -> None:
    """Close ``key``'s browser from a thread of its own (a cancelled task
    must not await; the close is bounded and never raises)."""
    threading.Thread(
        target=PB.BROWSERS.close_blocking, args=(key, reason), name="ij-agent-browser-close",
        daemon=True,
    ).start()


async def run_action(ctx: Any, action: "Callable[[str, str], Awaitable[PB.ActResult]]") -> "PB.ActResult | ToolResult":
    """Run ONE browser action for ``ctx``'s session — raced against the
    turn's Stop. DECISION (v1.376.0): a Stop never leaves an action running:
    Playwright's driver finishes a click or a page load on its own after the
    caller gives up, so a Stop (or any cancel of this call: a helper stopped
    with its turn, a deadline, shutdown) CLOSES the session's browser. The
    next turn opens a fresh one. Returns the action's result, or the
    ToolResult a refusal / a Stop answers with."""
    key = PB.browser_key(ctx)
    preview = current_preview()
    handle = _turn_handle(ctx)
    task = asyncio.ensure_future(action(key, preview))
    try:
        if handle is None:
            return await task
        stop = asyncio.ensure_future(handle.wait_stopped())
        try:
            await asyncio.wait({task, stop}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            if not stop.done():
                stop.cancel()
        if task.done():
            return task.result()
        task.cancel()
        await asyncio.wait({task})
        await PB.BROWSERS.close(key, "stopped")
        return ToolResult(ok=False, error=PB.STOPPED)
    except PB.BrowserProblem as exc:
        return ToolResult(ok=False, error=str(exc))
    except asyncio.CancelledError:
        if not task.done():
            task.cancel()
        _close_in_background(key, "stopped")
        raise


def _place(res: "PB.ActResult") -> str:
    shown = PB.where(res.url)
    if shown:
        return shown
    return "a blank page" if res.url.startswith("about:") else "a page that did not load"


def _summary(res: "PB.ActResult") -> list[str]:
    title = PB._line(res.title)
    head = f"{res.did}. The agent's browser is on {_place(res)}" + (f" — \"{title}\"" if title else "") + "."
    lines = [head, *res.blocked]
    waiting = []
    if res.new_console:
        waiting.append(f"{res.new_console} console error{'s' if res.new_console != 1 else ''}")
    if res.new_failed:
        waiting.append(f"{res.new_failed} failed request{'s' if res.new_failed != 1 else ''}")
    if waiting:
        lines.append("Waiting to be read: " + " and ".join(waiting) + ". Call page_read to see them.")
    return lines


def _data(res: "PB.ActResult") -> dict[str, Any]:
    return {"url": PB.where(res.url), "title": PB._line(res.title), "blocked": list(res.blocked)}


def _target_args(args: dict[str, Any]) -> "dict[str, str] | str":
    """What to act on: ``css`` wins; ``role`` + ``target`` is that role with
    that name; ``target`` alone is the words on it."""
    css = str(args.get("css") or "").strip()
    target = str(args.get("target") or "").strip()
    role = str(args.get("role") or "").strip()
    if css:
        return {"css": css}
    if role and target:
        return {"role": role, "name": target}
    return target


_TARGET_PROPS: dict[str, Any] = {
    "role": {
        "type": "string",
        "description": "Optional: the kind of element (button, link, checkbox, tab, textbox …) "
        "when the words alone match more than one thing.",
    },
    "css": {
        "type": "string",
        "description": "Optional: a CSS selector instead of target (e.g. \"#buy\").",
    },
}


class _PageTool(Tool):
    #: The page's text is a third party's words (fenced + scanned by the lanes).
    returns_untrusted_content = True


class PageOpenTool(_PageTool):
    name = "page_open"
    description = (
        "Open a page in your own invisible browser (Microsoft Edge) and keep it open for the "
        "session: use it for the user's dev server, e.g. http://localhost:5173/cart, then "
        "page_click, page_type, page_read and page_screenshot work on that page. Only pages on "
        "this PC or the user's own network open; not public websites. The page the user's "
        "Browser shows opens without asking; another local address asks the user first. "
        "Returns where the browser is and the start of the page's text; page_read has the "
        "rest, with the console errors and failed requests."
    )
    reversibility = Reversibility.READONLY
    risk_class = RiskClass.READ
    input_schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The page address, e.g. http://localhost:5173/cart"},
        },
        "required": ["url"],
    }

    async def spend_check(self, args: dict[str, Any], ctx: ToolContext) -> "SpendCheck | None":
        """The Browser pane's page, or an address the user already allowed
        for this browser, opens without a card; any other local page asks; a
        non-local address has no opinion (execute refuses it in words)."""
        url = local_address(PB.normalize((args or {}).get("url")))
        if not url:
            return None
        if is_preview_page(url) or PB.BROWSERS.allowed(PB.browser_key(ctx), url):
            return SpendCheck(ask=False)
        return SpendCheck(
            ask=True,
            words=(
                f"Open {url} in the agent's browser. It is not the page open in this session's "
                "Browser; once you allow it, the agent can keep working on that address until its "
                "browser closes."
            ),
        )

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        url = local_address(PB.normalize((args or {}).get("url")))
        if not url:
            return ToolResult(ok=False, error=PB.NOT_LOCAL)
        background = _background()
        out = await run_action(ctx, lambda key, preview: PB.BROWSERS.open(
            key, url, preview=preview, background=background,
        ))
        if isinstance(out, ToolResult):
            return out
        lines = _summary(out)
        text = " ".join(str(out.text or "").split())
        if text:
            # The start of the page, so the model knows where it landed.
            lines.append("The page starts: " + text[:OPEN_TEXT_CHARS])
        return ToolResult(ok=True, output="\n".join(lines), data=_data(out))


class PageClickTool(_PageTool):
    name = "page_click"
    description = (
        "Click something on the page open in your browser (page_open first): give the words on "
        "it as target (\"Add to cart\"), with role when the words match more than one thing, or "
        "a css selector. Waits for any page it opens to load. Returns where the browser is "
        "after the click."
    )
    risk_class = RiskClass.PAGE_ACTION
    input_schema = {
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "description": "The words on the button or link (or text on the page) to click.",
            },
            **_TARGET_PROPS,
        },
        "required": [],
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        target = _target_args(args or {})
        if not target:
            return ToolResult(ok=False, error="Say what to click: target (its words) or css.")
        out = await run_action(ctx, lambda key, preview: PB.BROWSERS.click(key, target, preview=preview))
        if isinstance(out, ToolResult):
            return out
        return ToolResult(ok=True, output="\n".join(_summary(out)), data=_data(out))


class PageTypeTool(_PageTool):
    name = "page_type"
    description = (
        "Type text into a field of the page open in your browser: target is the field's label, "
        "placeholder or name (or give css). It replaces what the field held; submit presses "
        "Enter after. It never types into a password field, a payment field or a file picker, "
        "and never types a key or a token."
    )
    risk_class = RiskClass.PAGE_ACTION
    input_schema = {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "The field's label, placeholder or name."},
            "text": {"type": "string", "description": "What to type."},
            "submit": {"type": "boolean", "description": "Press Enter after typing (default false)."},
            **_TARGET_PROPS,
        },
        "required": ["text"],
    }

    def redact_args(self, args: dict[str, Any]) -> dict[str, Any]:
        """What was typed is masked in the ledger (a value that looks like a
        key is refused anyway; this keeps an e-mail address or a name typed
        into a dev page out of the transcript at rest only when it reads as
        a secret)."""
        out = dict(args or {})
        if "text" in out:
            out["text"] = PB._mask(str(out.get("text") or ""))
        return out

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        a = args or {}
        target = _target_args(a)
        if not target:
            return ToolResult(ok=False, error="Say which field to type into: target (its label) or css.")
        text = str(a.get("text") if a.get("text") is not None else "")
        submit = str(a.get("submit")).strip().lower() in ("true", "1", "yes")
        out = await run_action(ctx, lambda key, preview: PB.BROWSERS.type(
            key, target, text, submit=submit, preview=preview,
        ))
        if isinstance(out, ToolResult):
            return out
        return ToolResult(ok=True, output="\n".join(_summary(out)), data=_data(out))


class PageReadTool(_PageTool):
    name = "page_read"
    description = (
        "Read the page open in your browser: its visible text, the buttons, links and fields "
        "you can act on, and the console errors, failed requests and dialogs since the last "
        "read (each read starts a fresh list). Use it after page_open or a click to see what "
        "happened, and to find a JavaScript error."
    )
    reversibility = Reversibility.READONLY
    risk_class = RiskClass.READ
    input_schema = {"type": "object", "properties": {}, "required": []}

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        out = await run_action(ctx, lambda key, preview: PB.BROWSERS.read(key, preview=preview))
        if isinstance(out, ToolResult):
            return out
        return ToolResult(ok=True, output=read_text(out), data={
            **_data(out),
            "console": list(out.console),
            "failed": list(out.failed),
            "dialogs": list(out.dialogs),
        })


def read_text(res: "PB.ActResult") -> str:
    """A read in words: the page, what can be acted on, and the logs since
    the last read (each list says when lines fell out of it)."""
    title = PB._line(res.title)
    parts = [f"Page: {title or '(no title)'}", f"Address: {_place(res)}"]
    parts += res.blocked
    text = (res.text or "").strip()
    if len(text) > READ_CHARS:
        text = text[:READ_CHARS] + f"\n[… cut: the page has {len(res.text):,} characters of text]"
    parts.append("\nVisible text:\n" + (text or "(none)"))
    if res.elements:
        rows = [
            f"- {e.get('role') or 'element'} \"{e.get('name') or ''}\"" + (" (disabled)" if e.get("disabled") else "")
            for e in res.elements
        ]
        parts.append("\nThings you can click or type into:\n" + "\n".join(rows))

    def _list(title_: str, rows: list[str], dropped: int) -> str:
        head = f"\n{title_} since the last read ({len(rows) + dropped}):"
        if not rows:
            return f"\n{title_} since the last read: none."
        more = f"\n- … and {dropped} older ones not kept" if dropped else ""
        return head + "\n" + "\n".join(f"- {r}" for r in rows) + more

    parts.append(_list("Console errors", res.console, res.dropped.get("console", 0)))
    parts.append(_list("Failed requests", res.failed, res.dropped.get("failed", 0)))
    if res.dialogs or res.dropped.get("dialog"):
        parts.append(_list("Dialogs", res.dialogs, res.dropped.get("dialog", 0)))
    return "\n".join(parts)


def _background() -> bool:
    try:
        from ..core import procs

        return procs.in_background_lane()
    except Exception:  # noqa: BLE001
        return False


def page_act_tools() -> list[Tool]:
    return [PageOpenTool(), PageClickTool(), PageTypeTool(), PageReadTool()]


__all__ = [
    "OPEN_TEXT_CHARS", "PageClickTool", "PageOpenTool", "PageReadTool", "PageTypeTool",
    "page_act_tools", "read_text", "run_action",
]
