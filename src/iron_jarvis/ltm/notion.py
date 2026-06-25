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

from .base import LTMConnector

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
                    "paragraph": {
                        "rich_text": [{"type": "text", "text": {"content": content}}]
                    },
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
