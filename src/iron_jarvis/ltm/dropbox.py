"""Dropbox long-term-memory connector (§21 extension).

Dropbox API v2 over an injected ``httpx.Client``. Bearer auth via a lazily resolved
OAuth access token (credential key ``dropbox``).

* search  -> ``POST https://api.dropboxapi.com/2/files/search_v2``
* download -> ``POST https://content.dropboxapi.com/2/files/download`` with the
  ``Dropbox-API-Arg`` header (path in the header, empty request body)
* append  -> ``POST https://content.dropboxapi.com/2/files/upload``

Required OAuth scopes: ``files.metadata.read``, ``files.content.read`` (search +
download) and ``files.content.write`` (append). A ``folder`` config value must be a
Dropbox path such as ``/Notes`` (root is ``""``).
"""

from __future__ import annotations

from typing import Any

from .cloud_base import CloudDriveConnector

SEARCH_URL = "https://api.dropboxapi.com/2/files/search_v2"
DOWNLOAD_URL = "https://content.dropboxapi.com/2/files/download"
UPLOAD_URL = "https://content.dropboxapi.com/2/files/upload"


class DropboxConnector(CloudDriveConnector):
    """Search / RAG / append over a user's Dropbox."""

    provider = "dropbox"
    name = "dropbox"

    def _search_files(
        self, query: str, headers: dict[str, str], limit: int
    ) -> list[dict[str, Any]]:
        if not (query or "").strip():
            return []  # Dropbox search_v2 requires a non-empty query
        options: dict[str, Any] = {
            "max_results": max(1, min(int(limit), 1000)),
            "file_status": "active",
            "filename_only": False,
        }
        if self.folder:
            options["path"] = self.folder
        body = {"query": query, "options": options}
        req_headers = dict(headers)
        req_headers["Content-Type"] = "application/json"
        resp = self._client().post(SEARCH_URL, headers=req_headers, json=body)
        self._raise(resp)
        data = resp.json()
        out: list[dict[str, Any]] = []
        for match in data.get("matches", []):
            # search_v2 double-wraps: match.metadata.metadata is the file entry.
            md = (match.get("metadata") or {}).get("metadata") or {}
            if md.get(".tag") == "folder":
                continue
            path = md.get("path_lower") or md.get("id")
            if not path:
                continue
            out.append(
                {
                    "id": md.get("id") or path,
                    "name": md.get("name") or "",
                    "ref": md.get("path_display") or path,
                    "path": path,
                }
            )
        return out

    def _download(self, meta: dict[str, Any], headers: dict[str, str]) -> bytes:
        arg = {"path": meta.get("path") or meta.get("id")}
        req_headers = dict(headers)
        req_headers["Dropbox-API-Arg"] = self._dumps(arg)
        resp = self._client().post(DOWNLOAD_URL, headers=req_headers, content=b"")
        self._raise(resp)
        return resp.content

    def _upload(self, title: str, content: str, headers: dict[str, str]) -> str:
        name = self._note_name(title)
        folder = (self.folder or "").rstrip("/")
        path = f"{folder}/{name}" if folder else f"/{name}"
        arg = {"path": path, "mode": "overwrite", "autorename": True, "mute": True}
        req_headers = dict(headers)
        req_headers["Dropbox-API-Arg"] = self._dumps(arg)
        req_headers["Content-Type"] = "application/octet-stream"
        resp = self._client().post(
            UPLOAD_URL, headers=req_headers, content=content.encode("utf-8")
        )
        self._raise(resp)
        data = resp.json()
        return data.get("path_display") or data.get("path_lower") or data.get("id") or ""
