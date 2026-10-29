"""Notion database connector (§21) — network is *injected*, never opened in tests.

The HTTP client is passed in (``http``) and only needs ``post(url, json, headers)``
and ``get(url, headers)`` returning an object with ``.json()`` — exactly the shape
of an ``httpx.Client``. Tests pass a fake, so nothing here opens a real socket.

Auth is resolved lazily via ``token_resolver`` (so the token can live in the
encrypted SecretsManager and is never held on the connector). A missing token is
handled gracefully: ``search`` returns ``[]`` and ``append`` raises a clear error.
"""

from __future__ import annotations

from typing import Any, Callable

import re

from .base import CHANGE_ACTIONS, LTMConnector, NoteOpUnsupported

_PAGE_ID_RX = re.compile(r"([0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", re.I)

#: Notion's own limits (v1.406.0 review): one text piece holds at most 2,000
#: characters and one rich_text array 100 pieces; a page of children is 100
#: blocks. A read follows at most this many pages (10,000 blocks).
TEXT_PIECE_MAX = 2000
RICH_TEXT_MAX = 100
READ_MAX_PAGES = 100


def rich_text_pieces(content: str) -> list[dict]:
    """``content`` as Notion rich_text pieces it accepts (≤ 2,000 chars each,
    ≤ 100 pieces). Longer text than that is a refusal in words, never a cut."""
    text = str(content or "")
    pieces = [text[i:i + TEXT_PIECE_MAX] for i in range(0, len(text), TEXT_PIECE_MAX)] or [""]
    if len(pieces) > RICH_TEXT_MAX:
        raise ValueError(
            f"notion: a note addition is limited to {TEXT_PIECE_MAX * RICH_TEXT_MAX:,} characters"
        )
    return [{"type": "text", "text": {"content": p}} for p in pieces]


NOTION_VERSION = "2022-06-28"
API_BASE = "https://api.notion.com/v1"


class NotionConnector(LTMConnector):
    """Query and append to a single Notion database over an injected HTTP client."""

    name = "notion"

    def __init__(
        self,
        database_id: str,
        token_resolver: Callable[[], str | None],
        http: Any,
        title_property: str = "Name",
    ) -> None:
        self.database_id = database_id
        self.token_resolver = token_resolver
        self.http = http
        self.title_property = title_property

    # -- auth -------------------------------------------------------------
    def _headers(self) -> dict[str, str] | None:
        token = self.token_resolver()
        if not token:
            return None
        return {
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }

    @staticmethod
    def _json(resp: Any) -> dict[str, Any]:
        raise_for_status = getattr(resp, "raise_for_status", None)
        if callable(raise_for_status):
            raise_for_status()
        return resp.json()

    # -- LTMConnector -----------------------------------------------------
    def search(self, query: str, k: int = 5) -> list[dict[str, Any]]:
        headers = self._headers()
        if headers is None:
            return []  # missing token -> empty, never a crash
        url = f"{API_BASE}/databases/{self.database_id}/query"
        body: dict[str, Any] = {"page_size": k}
        if query.strip():
            body["filter"] = {
                "property": self.title_property,
                "title": {"contains": query},
            }
        data = self._json(self.http.post(url, json=body, headers=headers))
        hits = [self._parse_page(page) for page in data.get("results", [])]
        return hits[:k]

    def append(self, title: str, content: str) -> str:
        headers = self._headers()
        if headers is None:
            raise RuntimeError(
                "Notion token not configured; set the Notion token secret to append."
            )
        url = f"{API_BASE}/pages"
        body = {
            "parent": {"database_id": self.database_id},
            "properties": {
                self.title_property: {"title": [{"text": {"content": title}}]}
            },
            "children": [
                {
                    "object": "block",
                    "type": "paragraph",
                    # v1.406.0 review: in pieces Notion accepts (see update).
                    "paragraph": {"rich_text": rich_text_pieces(content)},
                }
            ],
        }
        data = self._json(self.http.post(url, json=body, headers=headers))
        return data.get("id", "")

    # -- parsing ----------------------------------------------------------
    def _parse_page(self, page: dict[str, Any]) -> dict[str, Any]:
        title = self._extract_title(page)
        return {
            "title": title,
            "snippet": title,
            "ref": page.get("url") or page.get("id", ""),
            "source": self.name,
        }

    def _extract_title(self, page: dict[str, Any]) -> str:
        props = page.get("properties", {})
        candidates: list[Any] = []
        if self.title_property in props:
            candidates.append(props[self.title_property])
        candidates.extend(v for k, v in props.items() if k != self.title_property)
        for prop in candidates:
            if isinstance(prop, dict) and prop.get("type") == "title":
                return "".join(
                    rt.get("plain_text", "") for rt in prop.get("title", [])
                ).strip()
        return str(page.get("id", ""))

    # -- note operations (v1.406.0, Brain Profiles) -------------------------
    @staticmethod
    def page_id(ref: str) -> str:
        """The page id inside a Notion ref (an id, or a notion.so link)."""
        found = _PAGE_ID_RX.findall(str(ref or ""))
        if not found:
            raise NoteOpUnsupported(f"notion: {ref!r} does not name a Notion page")
        return found[-1].replace("-", "")

    def _need_headers(self) -> dict[str, str]:
        headers = self._headers()
        if headers is None:
            raise RuntimeError("Notion token not configured; set the Notion token secret.")
        return headers

    def _patch(self, url: str, body: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        patch = getattr(self.http, "patch", None)
        if not callable(patch):
            raise NoteOpUnsupported("notion: this connection cannot change pages")
        return self._json(patch(url, json=body, headers=headers))

    def read(self, ref: str) -> str:
        headers = self._need_headers()
        pid = self.page_id(ref)
        lines: list[str] = []
        # v1.406.0 review: Notion answers 100 blocks a page; an addition lands
        # at the END, so a note past 100 blocks was never read back and every
        # save to it said "Not confirmed". Follow the cursor, bounded.
        cursor = ""
        for _ in range(READ_MAX_PAGES):
            url = f"{API_BASE}/blocks/{pid}/children?page_size=100"
            if cursor:
                url += f"&start_cursor={cursor}"
            data = self._json(self.http.get(url, headers=headers))
            for block in data.get("results", []) or []:
                kind = block.get("type", "")
                inner = block.get(kind) if isinstance(block.get(kind), dict) else {}
                text = "".join(rt.get("plain_text", "") or (rt.get("text") or {}).get("content", "")
                               for rt in (inner or {}).get("rich_text", []) or [])
                if text:
                    lines.append(text)
            cursor = str(data.get("next_cursor") or "") if data.get("has_more") else ""
            if not cursor:
                break
        return "\n\n".join(lines)

    def update(self, ref: str, content: str) -> str:
        headers = self._need_headers()
        pid = self.page_id(ref)
        # v1.406.0 review: Notion refuses a text piece over 2,000 characters
        # (a 400 the save path read as "unavailable", so the note was queued
        # and every Retry failed the same way). One paragraph, in pieces.
        body = {"children": [{
            "object": "block", "type": "paragraph",
            "paragraph": {"rich_text": rich_text_pieces(content)},
        }]}
        self._patch(f"{API_BASE}/blocks/{pid}/children", body, headers)
        return ref

    def change(self, action: str, ref: str, to: str = "") -> str:
        action = str(action or "").strip().lower()
        if action not in CHANGE_ACTIONS:
            raise NoteOpUnsupported(f"notion: unknown change {action!r}")
        headers = self._need_headers()
        pid = self.page_id(ref)
        if action in ("archive", "delete"):
            # Notion's own archive: the page goes to its trash, restorable.
            self._patch(f"{API_BASE}/pages/{pid}", {"archived": True}, headers)
            return ref
        if action == "rename" and str(to or "").strip():
            self._patch(f"{API_BASE}/pages/{pid}", {"properties": {
                self.title_property: {"title": [{"text": {"content": str(to).strip()}}]}
            }}, headers)
            return ref
        raise NoteOpUnsupported(f"notion: Iron Jarvis cannot {action} a page — do it in Notion")
