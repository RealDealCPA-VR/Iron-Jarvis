"""Google Drive long-term-memory connector (§21 extension).

Drive v3 REST over an injected ``httpx.Client``. Bearer auth via a lazily-resolved
OAuth access token (credential key ``google_drive``).

* search  -> ``GET https://www.googleapis.com/drive/v3/files?q=...&fields=...``
* download -> ``GET .../files/{id}?alt=media`` (native Google Docs are exported to
  ``text/plain`` via ``.../files/{id}/export``)
* append  -> multipart *simple* upload to
  ``POST https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart``

Required OAuth scope: ``https://www.googleapis.com/auth/drive`` (read + create). A
read-only deployment can use ``.../auth/drive.readonly``, but then ``append`` 403s.
"""

from __future__ import annotations

import uuid
from typing import Any

from .cloud_base import CloudDriveConnector

FILES_URL = "https://www.googleapis.com/drive/v3/files"
UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"
_GOOGLE_APPS_PREFIX = "application/vnd.google-apps"


class GoogleDriveConnector(CloudDriveConnector):
    """Search / RAG / append over a user's Google Drive."""

    provider = "google_drive"
    name = "google_drive"

    def _search_files(
        self, query: str, headers: dict[str, str], limit: int
    ) -> list[dict[str, Any]]:
        terms: list[str] = []
        q = (query or "").replace("\\", "\\\\").replace("'", "\\'")
        if q.strip():
            terms.append(f"(name contains '{q}' or fullText contains '{q}')")
        if self.folder:
            terms.append(f"'{self.folder}' in parents")
        terms.append("trashed = false")
        params = {
            "q": " and ".join(terms),
            "fields": "files(id,name,webViewLink,mimeType)",
            "pageSize": max(1, min(int(limit), 100)),
            "spaces": "drive",
        }
        resp = self._client().get(FILES_URL, headers=headers, params=params)
        self._raise(resp)
        data = resp.json()
        out: list[dict[str, Any]] = []
        for f in data.get("files", []):
            mime = f.get("mimeType", "")
            if mime == f"{_GOOGLE_APPS_PREFIX}.folder":
                continue
            out.append(
                {
                    "id": f.get("id"),
                    "name": f.get("name") or "",
                    "ref": f.get("webViewLink") or f.get("id") or "",
                    "mime": mime,
                }
            )
        return out

    def _download(self, meta: dict[str, Any], headers: dict[str, str]) -> bytes:
        fid = meta.get("id")
        mime = str(meta.get("mime") or "")
        if mime.startswith(_GOOGLE_APPS_PREFIX):
            # Native Docs/Sheets/Slides aren't downloadable as-is; export to text.
            resp = self._client().get(
                f"{FILES_URL}/{fid}/export",
                headers=headers,
                params={"mimeType": "text/plain"},
            )
        else:
            resp = self._client().get(
                f"{FILES_URL}/{fid}", headers=headers, params={"alt": "media"}
            )
        self._raise(resp)
        return resp.content

    def _upload(self, title: str, content: str, headers: dict[str, str]) -> str:
        boundary = "ironjarvis-" + uuid.uuid4().hex
        metadata: dict[str, Any] = {"name": self._note_name(title)}
        if self.folder:
            metadata["parents"] = [self.folder]
        body = (
            f"--{boundary}\r\n"
            "Content-Type: application/json; charset=UTF-8\r\n\r\n"
            f"{self._dumps(metadata)}\r\n"
            f"--{boundary}\r\n"
            "Content-Type: text/markdown; charset=UTF-8\r\n\r\n"
            f"{content}\r\n"
            f"--{boundary}--\r\n"
        ).encode("utf-8")
        up_headers = dict(headers)
        up_headers["Content-Type"] = f"multipart/related; boundary={boundary}"
        resp = self._client().post(
            UPLOAD_URL,
            headers=up_headers,
            params={"uploadType": "multipart", "fields": "id,webViewLink"},
            content=body,
        )
        self._raise(resp)
        data = resp.json()
        return data.get("webViewLink") or data.get("id") or ""
