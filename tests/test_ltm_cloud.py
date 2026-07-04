"""Cloud-drive LTM connectors — Google Drive / OneDrive / Dropbox.

Fully offline: every connector is driven through an ``httpx.MockTransport`` so the
real ``httpx.Client`` request-building path is exercised (URLs, params, headers,
bodies) without a socket ever opening. The three providers share
:class:`iron_jarvis.ltm.cloud_base.CloudDriveConnector`; the search/rank/skip logic
is verified most deeply against Google Drive, with focused search+download+append
coverage for OneDrive and Dropbox.
"""

from __future__ import annotations

import json

import httpx
import pytest

from iron_jarvis.ltm.cloud_base import chunk_text
from iron_jarvis.ltm.dropbox import DropboxConnector
from iron_jarvis.ltm.gdrive import GoogleDriveConnector
from iron_jarvis.ltm.onedrive import OneDriveConnector


# --------------------------------------------------------------------------
# A deterministic, offline embedder: a tiny keyword bag so ranking is provable.
# --------------------------------------------------------------------------
class KeywordEmbedder:
    VOCAB = ("budget", "forecast", "quarterly", "recipe", "dog", "invoice")

    def embed(self, text: str) -> list[float]:
        t = (text or "").lower()
        return [float(t.count(word)) for word in self.VOCAB]


BUDGET_TEXT = (
    "Quarterly budget forecast\n"
    "The budget forecast for the next quarter projects strong revenue.\n"
    "Line items include payroll, rent, and a quarterly forecast reserve.\n"
) + "\n".join(f"budget detail line {i}" for i in range(60))  # force >1 chunk

DOG_TEXT = "A recipe for a happy dog.\nThe dog loves this recipe every day.\n"

INVOICE_TEXT = "Invoice #42 payable on receipt. Terms net 30.\n"


def _token():
    return "live-oauth-token"


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# --------------------------------------------------------------------------
# chunk_text unit behaviour
# --------------------------------------------------------------------------
def test_chunk_text_splits_by_lines_and_chars():
    assert chunk_text("") == []
    assert chunk_text("   \n  ") == []
    small = chunk_text("one\ntwo\nthree", max_lines=40, max_chars=1500)
    assert small == ["one\ntwo\nthree"]
    many = chunk_text("\n".join(str(i) for i in range(100)), max_lines=40)
    assert len(many) >= 2  # 100 lines / 40 -> multiple chunks
    # An oversized single line is windowed by characters.
    giant = chunk_text("x" * 3200, max_chars=1000)
    assert len(giant) >= 3


# --------------------------------------------------------------------------
# Google Drive — deepest coverage (search, chunk+rank, skip bad file, append)
# --------------------------------------------------------------------------
def _gdrive_handler(calls):
    files = {
        "1": ("budget.txt", BUDGET_TEXT),
        "2": ("broken.txt", None),  # download 500s -> must be skipped
        "3": ("dog.txt", DOG_TEXT),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path
        if path == "/drive/v3/files":  # search (list)
            assert request.headers["authorization"] == "Bearer live-oauth-token"
            listing = [
                {
                    "id": fid,
                    "name": name,
                    "webViewLink": f"https://drive.google.com/file/d/{fid}/view",
                    "mimeType": "text/plain",
                }
                for fid, (name, _body) in files.items()
            ]
            return httpx.Response(200, json={"files": listing})
        if path.startswith("/drive/v3/files/"):  # download by id
            fid = path.rsplit("/", 1)[-1]
            name, body = files[fid]
            if body is None:
                return httpx.Response(500, text="boom")
            return httpx.Response(200, content=body.encode("utf-8"))
        if path == "/upload/drive/v3/files":  # simple multipart upload
            return httpx.Response(
                200,
                json={
                    "id": "new-id",
                    "webViewLink": "https://drive.google.com/file/d/new-id/view",
                },
            )
        return httpx.Response(404)

    return handler


def test_gdrive_search_ranks_and_skips_bad_file():
    calls: list[httpx.Request] = []
    conn = GoogleDriveConnector(
        _token, _client(_gdrive_handler(calls)), embedder=KeywordEmbedder()
    )
    hits = conn.search("budget forecast", k=5)

    # broken.txt (500 on download) is skipped, never raising.
    titles = [h["title"] for h in hits]
    assert "broken.txt" not in titles
    assert set(titles) == {"budget.txt", "dog.txt"}
    # Correctly shaped hits.
    top = hits[0]
    assert set(top) == {"title", "snippet", "ref", "source"}
    assert top["source"] == "google_drive"
    # Semantic ranking puts the budget file first; snippet comes from its content.
    assert top["title"] == "budget.txt"
    assert "budget" in top["snippet"].lower()
    assert top["ref"].startswith("https://drive.google.com/")


def test_gdrive_lexical_fallback_without_embedder():
    calls: list[httpx.Request] = []
    conn = GoogleDriveConnector(_token, _client(_gdrive_handler(calls)))  # no embedder
    hits = conn.search("dog recipe", k=5)
    assert hits, "expected lexical hits"
    assert hits[0]["title"] == "dog.txt"
    assert "dog" in hits[0]["snippet"].lower()


def test_gdrive_no_token_returns_empty_and_append_raises():
    conn = GoogleDriveConnector(lambda: None, _client(_gdrive_handler([])))
    assert conn.search("anything") == []
    with pytest.raises(RuntimeError, match="no access token"):
        conn.append("Note", "body")


def test_gdrive_append_uploads_multipart():
    calls: list[httpx.Request] = []
    conn = GoogleDriveConnector(
        _token, _client(_gdrive_handler(calls)), folder="folder-xyz"
    )
    ref = conn.append("My Big Idea", "the plan in markdown")
    assert ref == "https://drive.google.com/file/d/new-id/view"
    upload = [r for r in calls if r.url.path == "/upload/drive/v3/files"]
    assert len(upload) == 1
    req = upload[0]
    assert req.method == "POST"
    assert req.url.params["uploadType"] == "multipart"
    assert req.headers["content-type"].startswith("multipart/related; boundary=")
    body = req.content.decode("utf-8")
    assert '"name":"my-big-idea.md"' in body
    assert '"parents":["folder-xyz"]' in body
    assert "the plan in markdown" in body


def test_gdrive_search_query_and_folder_scope():
    calls: list[httpx.Request] = []
    conn = GoogleDriveConnector(
        _token, _client(_gdrive_handler(calls)), folder="root-folder"
    )
    conn.search("budget", k=3)
    search_req = next(r for r in calls if r.url.path == "/drive/v3/files")
    q = search_req.url.params["q"]
    assert "name contains 'budget'" in q
    assert "'root-folder' in parents" in q
    assert "trashed = false" in q


# --------------------------------------------------------------------------
# OneDrive — search via downloadUrl, skip a bad file, append via PUT
# --------------------------------------------------------------------------
def _onedrive_handler(calls):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        url = str(request.url)
        if "/search(q=" in url:
            value = [
                {
                    "id": "A",
                    "name": "budget.txt",
                    "webUrl": "https://onedrive.live.com/a",
                    "@microsoft.graph.downloadUrl": "https://dl.example/a",
                    "file": {},
                },
                {
                    "id": "B",
                    "name": "broken.txt",
                    "webUrl": "https://onedrive.live.com/b",
                    "@microsoft.graph.downloadUrl": "https://dl.example/b",
                    "file": {},
                },
            ]
            return httpx.Response(200, json={"value": value})
        if url == "https://dl.example/a":
            return httpx.Response(200, content=BUDGET_TEXT.encode("utf-8"))
        if url == "https://dl.example/b":
            return httpx.Response(404, text="gone")  # skipped
        if request.method == "PUT" and url.endswith(":/content"):
            return httpx.Response(
                201, json={"id": "up1", "webUrl": "https://onedrive.live.com/up1"}
            )
        return httpx.Response(404)

    return handler


def test_onedrive_search_downloads_and_skips_bad():
    calls: list[httpx.Request] = []
    conn = OneDriveConnector(
        _token, _client(_onedrive_handler(calls)), embedder=KeywordEmbedder()
    )
    hits = conn.search("budget forecast", k=5)
    titles = [h["title"] for h in hits]
    assert titles == ["budget.txt"]  # broken.txt (404) skipped
    assert hits[0]["source"] == "onedrive"
    assert hits[0]["ref"] == "https://onedrive.live.com/a"
    # The pre-signed download URL must be fetched WITHOUT a bearer header.
    dl = next(r for r in calls if str(r.url) == "https://dl.example/a")
    assert "authorization" not in {k.lower() for k in dl.headers}


def test_onedrive_append_puts_content():
    calls: list[httpx.Request] = []
    conn = OneDriveConnector(_token, _client(_onedrive_handler(calls)))
    ref = conn.append("Meeting Notes", "body text")
    assert ref == "https://onedrive.live.com/up1"
    put = next(r for r in calls if r.method == "PUT")
    assert put.url.path.endswith("/me/drive/root:/meeting-notes.md:/content")
    assert put.content.decode("utf-8") == "body text"


# --------------------------------------------------------------------------
# Dropbox — search_v2 double-wrap, header-arg download, upload
# --------------------------------------------------------------------------
def _dropbox_handler(calls):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        url = str(request.url)
        if url.endswith("/2/files/search_v2"):
            matches = [
                {
                    "metadata": {
                        "metadata": {
                            ".tag": "file",
                            "id": "id:a",
                            "name": "budget.txt",
                            "path_lower": "/notes/budget.txt",
                            "path_display": "/Notes/budget.txt",
                        }
                    }
                },
                {
                    "metadata": {
                        "metadata": {
                            ".tag": "file",
                            "id": "id:b",
                            "name": "broken.txt",
                            "path_lower": "/notes/broken.txt",
                            "path_display": "/Notes/broken.txt",
                        }
                    }
                },
            ]
            return httpx.Response(200, json={"matches": matches})
        if url.endswith("/2/files/download"):
            arg = json.loads(request.headers["dropbox-api-arg"])
            if arg["path"] == "/notes/budget.txt":
                return httpx.Response(200, content=BUDGET_TEXT.encode("utf-8"))
            return httpx.Response(409, text="not_found")  # skipped
        if url.endswith("/2/files/upload"):
            return httpx.Response(200, json={"path_display": "/Notes/my-note.md"})
        return httpx.Response(404)

    return handler


def test_dropbox_search_downloads_and_skips_bad():
    calls: list[httpx.Request] = []
    conn = DropboxConnector(
        _token, _client(_dropbox_handler(calls)), embedder=KeywordEmbedder()
    )
    hits = conn.search("budget forecast", k=5)
    assert [h["title"] for h in hits] == ["budget.txt"]  # broken skipped
    assert hits[0]["source"] == "dropbox"
    assert hits[0]["ref"] == "/Notes/budget.txt"
    # download used the header-arg convention with an empty body.
    dl = next(r for r in calls if str(r.url).endswith("/2/files/download"))
    assert json.loads(dl.headers["dropbox-api-arg"])["path"] == "/notes/budget.txt"
    assert dl.content == b""


def test_dropbox_empty_query_returns_empty_without_calling_api():
    calls: list[httpx.Request] = []
    conn = DropboxConnector(_token, _client(_dropbox_handler(calls)))
    assert conn.search("   ") == []
    assert calls == []  # search_v2 requires a non-empty query


def test_dropbox_append_uploads_with_arg_header():
    calls: list[httpx.Request] = []
    conn = DropboxConnector(_token, _client(_dropbox_handler(calls)), folder="/Notes")
    ref = conn.append("My Note", "hello world")
    assert ref == "/Notes/my-note.md"
    up = next(r for r in calls if str(r.url).endswith("/2/files/upload"))
    arg = json.loads(up.headers["dropbox-api-arg"])
    assert arg["path"] == "/Notes/my-note.md"
    assert arg["mode"] == "overwrite"
    assert up.headers["content-type"] == "application/octet-stream"
    assert up.content.decode("utf-8") == "hello world"


def test_search_survives_total_api_failure():
    def boom(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    for cls in (GoogleDriveConnector, OneDriveConnector, DropboxConnector):
        conn = cls(_token, _client(boom), embedder=KeywordEmbedder())
        assert conn.search("budget") == []  # never raises
