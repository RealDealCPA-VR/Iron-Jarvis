"""OneDrive / Microsoft Graph long-term-memory connector (§21 extension).

Microsoft Graph v1.0 over an injected ``httpx.Client``. Bearer auth via a lazily
resolved OAuth access token (credential key ``onedrive``).

* search  -> ``GET https://graph.microsoft.com/v1.0/me/drive/root/search(q='...')``
  (scoped to a folder item when ``folder`` is set)
* download -> the item's ``@microsoft.graph.downloadUrl`` (a short-lived pre-signed
  URL, needs no auth header) or ``GET .../items/{id}/content``
* append  -> ``PUT .../me/drive/root:/<name>:/content`` (simple upload)

Required OAuth scope: ``Files.ReadWrite`` (delegated). Read-only deployments can use
``Files.Read`` — then ``append`` 403s.
"""

from __future__ import annotations

from typing import Any

from .cloud_base import CloudDriveConnector

GRAPH = "https://graph.microsoft.com/v1.0"


class OneDriveConnector(CloudDriveConnector):
    """Search / RAG / append over a user's OneDrive via Microsoft Graph."""

    provider = "onedrive"
    name = "onedrive"

    def _search_files(
        self, query: str, headers: dict[str, str], limit: int
    ) -> list[dict[str, Any]]:
        q = (query or "").replace("'", "''")  # OData single-quote escaping
        if self.folder:
            url = f"{GRAPH}/me/drive/items/{self.folder}/search(q='{q}')"
        else:
            url = f"{GRAPH}/me/drive/root/search(q='{q}')"
        # NB: no $select — @microsoft.graph.downloadUrl is only returned by default.
        resp = self._client().get(
            url, headers=headers, params={"$top": max(1, min(int(limit), 200))}
        )
        self._raise(resp)
        data = resp.json()
        out: list[dict[str, Any]] = []
        for it in data.get("value", []):
            if "folder" in it:  # skip folders, keep files
                continue
            out.append(
                {
                    "id": it.get("id"),
                    "name": it.get("name") or "",
                    "ref": it.get("webUrl") or it.get("id") or "",
                    "download": it.get("@microsoft.graph.downloadUrl"),
                }
            )
        return out

    def _download(self, meta: dict[str, Any], headers: dict[str, str]) -> bytes:
        download_url = meta.get("download")
        if download_url:
            # Pre-authenticated URL: sending the bearer header can actually 401,
            # so fetch it bare.
            resp = self._client().get(download_url)
        else:
            resp = self._client().get(
                f"{GRAPH}/me/drive/items/{meta.get('id')}/content", headers=headers
            )
        self._raise(resp)
        return resp.content

    def _upload(self, title: str, content: str, headers: dict[str, str]) -> str:
        name = self._note_name(title)
        if self.folder:
            url = f"{GRAPH}/me/drive/items/{self.folder}:/{name}:/content"
        else:
            url = f"{GRAPH}/me/drive/root:/{name}:/content"
        up_headers = dict(headers)
        up_headers["Content-Type"] = "text/markdown"
        resp = self._client().put(
            url, headers=up_headers, content=content.encode("utf-8")
        )
        self._raise(resp)
        data = resp.json()
        return data.get("webUrl") or data.get("id") or ""
