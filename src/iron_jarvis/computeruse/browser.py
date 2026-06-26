"""Browser implementations for the Computer-Use subsystem.

* :class:`FakeBrowser` — a fully deterministic, offline browser over an in-memory
  page map. Used by every test; never touches the network. Records navigations,
  clicks, and typed values; raises :class:`UnknownSelector` for unmatched
  selectors so the harness's retry/recovery path is exercised.

* :class:`PlaywrightBrowser` — the real browser. It launches an **isolated
  incognito context** (``browser.new_context()``), targets elements DOM/a11y-first
  (``get_by_role`` / ``get_by_label`` / ``get_by_text`` / css), and only ever
  produces pixels through :meth:`screenshot`. Playwright is imported LAZILY inside
  the methods so importing this module (and running the offline test-suite) never
  requires a browser binary. It is constructed but never launched in tests.
"""

from __future__ import annotations

from typing import Any

from .base import (
    Page,
    Selector,
    UnknownSelector,
    match_element,
)


# --------------------------------------------------------------------------- #
# FakeBrowser
# --------------------------------------------------------------------------- #


class FakeBrowser:
    """Deterministic in-memory browser implementing the :class:`Browser` protocol.

    ``pages`` maps ``url -> {"text": str, "a11y": [{role, name, ...}],
    "fields": [{selector, type, ...}]}``. An a11y node may carry a ``"navigate"``
    key; clicking it moves to that URL (simulating a link/button).
    """

    def __init__(self, pages: dict[str, dict[str, Any]] | None = None) -> None:
        self.pages: dict[str, dict[str, Any]] = dict(pages or {})
        self.current_url: str | None = None
        self.navigations: list[str] = []
        self.clicks: list[dict[str, Any]] = []
        self.typed: list[dict[str, Any]] = []
        self.screenshots = 0
        self.closed = False

    # -- helpers ------------------------------------------------------------
    def _page_dict(self, url: str | None) -> dict[str, Any]:
        return self.pages.get(url or "", {})

    def _a11y_nodes(self, url: str | None) -> list[dict[str, Any]]:
        """Merge declared a11y nodes with field nodes (so types are visible)."""
        data = self._page_dict(url)
        nodes: list[dict[str, Any]] = list(data.get("a11y", []))
        for f in data.get("fields", []):
            nodes.append(
                {
                    "role": f.get("role", "textbox"),
                    "name": f.get("name") or f.get("label") or f.get("selector", ""),
                    "type": f.get("type", "text"),
                    "css": f.get("selector"),
                    "selector": f.get("selector"),
                    "field": True,
                }
            )
        return nodes

    def _build_page(self, url: str | None) -> Page:
        data = self._page_dict(url)
        return Page(
            url=url or "",
            a11y_tree=self._a11y_nodes(url),
            text=str(data.get("text", "")),
        )

    def _find(self, selector: Selector, *, fields_only: bool = False) -> dict[str, Any] | None:
        for node in self._a11y_nodes(self.current_url):
            if fields_only and not node.get("field"):
                continue
            if match_element(selector, node):
                return node
        return None

    # -- Browser protocol ---------------------------------------------------
    async def navigate(self, url: str) -> Page:
        self.navigations.append(url)
        self.current_url = url
        return self._build_page(url)

    async def click(self, selector: Selector, *, fallback: bool = False) -> Page:
        node = self._find(selector)
        if node is None:
            raise UnknownSelector(f"no element for {selector.describe()}")
        self.clicks.append({"selector": selector.describe(), "fallback": fallback})
        target = node.get("navigate")
        if target:
            return await self.navigate(str(target))
        return self._build_page(self.current_url)

    async def type(self, selector: Selector, value: str) -> Page:
        node = self._find(selector)
        if node is None:
            raise UnknownSelector(f"no field for {selector.describe()}")
        self.typed.append(
            {
                "selector": selector.describe(),
                "value": value,
                "type": node.get("type", "text"),
            }
        )
        return self._build_page(self.current_url)

    async def extract(self, selector: Selector) -> str:
        node = self._find(selector)
        if node is None:
            raise UnknownSelector(f"no element for {selector.describe()}")
        return str(node.get("text") or node.get("name") or "")

    async def read(self) -> Page:
        return self._build_page(self.current_url)

    async def screenshot(self) -> bytes:
        self.screenshots += 1
        return f"PNG-FAKE::{self.current_url or ''}::{self.screenshots}".encode("utf-8")

    async def aclose(self) -> None:
        self.closed = True


# --------------------------------------------------------------------------- #
# PlaywrightBrowser (real; lazy import; isolated context)
# --------------------------------------------------------------------------- #


class PlaywrightBrowser:
    """Real browser over Playwright, launched in an isolated incognito context.

    Constructed cheaply; the browser is launched on first use. ALL of Playwright
    is imported lazily inside methods, so importing this module never pulls in the
    browser binaries — the offline test-suite constructs but never launches it.
    """

    def __init__(
        self,
        *,
        headless: bool = True,
        browser_name: str = "chromium",
        user_agent: str | None = None,
    ) -> None:
        self.headless = headless
        self.browser_name = browser_name
        self.user_agent = user_agent
        self._pw: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None

    # -- lifecycle ----------------------------------------------------------
    async def _ensure(self) -> Any:
        """Lazily launch the browser + an isolated incognito context."""
        if self._page is not None:
            return self._page
        from playwright.async_api import async_playwright  # lazy: no binaries on import

        self._pw = await async_playwright().start()
        launcher = getattr(self._pw, self.browser_name)
        self._browser = await launcher.launch(headless=self.headless)
        # Isolation: a fresh, disposable incognito context (no shared cookies).
        ctx_kwargs: dict[str, Any] = {}
        if self.user_agent:
            ctx_kwargs["user_agent"] = self.user_agent
        self._context = await self._browser.new_context(**ctx_kwargs)
        self._page = await self._context.new_page()
        return self._page

    # -- DOM/a11y-first locator selection -----------------------------------
    def _locator(self, page: Any, selector: Selector) -> Any:
        if selector.css:
            return page.locator(selector.css)
        if selector.role and selector.name:
            return page.get_by_role(selector.role, name=selector.name)
        if selector.name:
            # Prefer an accessible label, then visible text.
            return page.get_by_label(selector.name)
        if selector.text:
            return page.get_by_text(selector.text)
        raise UnknownSelector("empty selector")

    async def _snapshot(self, page: Any) -> Page:
        try:
            text = await page.inner_text("body")
        except Exception:  # noqa: BLE001
            text = ""
        a11y: list[dict[str, Any]] = []
        try:
            tree = await page.accessibility.snapshot() or {}
            a11y = _flatten_a11y(tree)
        except Exception:  # noqa: BLE001
            a11y = []
        return Page(url=page.url, a11y_tree=a11y, text=text)

    # -- Browser protocol ---------------------------------------------------
    async def navigate(self, url: str) -> Page:
        page = await self._ensure()
        await page.goto(url)
        return await self._snapshot(page)

    async def click(self, selector: Selector, *, fallback: bool = False) -> Page:
        page = await self._ensure()
        if fallback:
            # Labelled fallback: bounding-box / pixel click via the located element.
            box = await self._locator(page, selector).bounding_box()
            if box:
                await page.mouse.click(
                    box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
                )
            else:
                await self._locator(page, selector).click()
        else:
            await self._locator(page, selector).click()
        return await self._snapshot(page)

    async def type(self, selector: Selector, value: str) -> Page:
        page = await self._ensure()
        await self._locator(page, selector).fill(value)
        return await self._snapshot(page)

    async def extract(self, selector: Selector) -> str:
        page = await self._ensure()
        return await self._locator(page, selector).inner_text()

    async def read(self) -> Page:
        page = await self._ensure()
        return await self._snapshot(page)

    async def screenshot(self) -> bytes:
        page = await self._ensure()
        return await page.screenshot()

    async def aclose(self) -> None:
        if self._context is not None:
            await self._context.close()
        if self._browser is not None:
            await self._browser.close()
        if self._pw is not None:
            await self._pw.stop()
        self._pw = self._browser = self._context = self._page = None


def _flatten_a11y(node: dict[str, Any], out: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Flatten a Playwright accessibility snapshot into ``[{role, name}, ...]``."""
    out = [] if out is None else out
    if not isinstance(node, dict):
        return out
    role = node.get("role")
    if role:
        out.append({"role": role, "name": node.get("name", "")})
    for child in node.get("children", []) or []:
        _flatten_a11y(child, out)
    return out
