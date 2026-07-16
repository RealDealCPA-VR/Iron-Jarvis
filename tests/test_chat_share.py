"""Share a chat thread (POST /chat/threads/{id}/share).

Full mode is a deterministic verbatim transcript (markdown or a
self-contained HTML page, entities escaped). Compact mode rides the one-shot
LLM path and must NEVER fabricate: a mock default either fails over to a real
adapter or refuses with the connect-a-model hint. Offline throughout — real
adapters are stand-ins injected via the provider manager.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import LLMResponse


def _client(tmp_path):
    return TestClient(create_app(str(tmp_path)))


def _seed(client, msgs=None) -> str:
    msgs = msgs or [
        {"role": "user", "content": "What is the S-corp deadline?",
         "attachmentNames": ["notes.pdf"]},
        {"role": "assistant", "content": "March 16 (the 15th is a Sunday).",
         "toolsUsed": ["web_search"], "interrupted": True},
    ]
    return client.put("/chat/threads/new", json={"messages": msgs}).json()["id"]


class _FakeAdapter:
    """A REAL-adapter stand-in (deliberately not MockLLMAdapter)."""

    provider = "anthropic"
    model = "claude-opus-4-8"

    def __init__(self, text="- Deadline confirmed: March 16."):
        self._text = text
        self.calls: list[dict] = []

    async def complete(self, *, system, messages, tools):
        self.calls.append({"system": system, "messages": messages})
        return LLMResponse(text=self._text)


# --- full transcript ----------------------------------------------------------


def test_share_full_markdown_is_verbatim(tmp_path):
    client = _client(tmp_path)
    tid = _seed(client)
    r = client.post(f"/chat/threads/{tid}/share", json={"mode": "full"})
    assert r.status_code == 200
    out = r.json()
    assert out["mode"] == "full" and out["format"] == "markdown"
    assert out["messages"] == 2
    body = out["content"]
    # Verbatim content under role headings, with honest footnotes.
    assert "# What is the S-corp deadline?" in body
    assert "### You" in body and "### Iron Jarvis" in body
    assert "March 16 (the 15th is a Sunday)." in body
    assert "Attached: notes.pdf" in body
    assert "Tools used: web_search" in body
    assert "interrupted mid-stream" in body


def test_share_full_html_page_escapes_content(tmp_path):
    client = _client(tmp_path)
    tid = _seed(client, msgs=[
        {"role": "user", "content": "run <script>alert(1)</script> & tell me"},
        {"role": "assistant", "content": "no"},
    ])
    r = client.post(
        f"/chat/threads/{tid}/share", json={"mode": "full", "format": "html"}
    )
    assert r.status_code == 200
    page = r.json()["content"]
    assert page.lstrip().startswith("<!DOCTYPE html>")
    assert "</html>" in page and "<title>" in page
    # Message text is escaped — a shared page must never execute chat content.
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


def test_share_validation_and_missing(tmp_path):
    client = _client(tmp_path)
    tid = _seed(client)
    assert client.post(
        f"/chat/threads/{tid}/share", json={"mode": "verbose"}
    ).status_code == 400
    assert client.post(
        f"/chat/threads/{tid}/share", json={"format": "pdf"}
    ).status_code == 400
    assert client.post(
        "/chat/threads/nope/share", json={}
    ).status_code == 404
    empty = client.put("/chat/threads/new", json={"messages": []}).json()["id"]
    r = client.post(f"/chat/threads/{empty}/share", json={})
    assert r.status_code == 400
    assert "no messages" in r.json()["detail"]


# --- compact (one-shot LLM) ---------------------------------------------------


def test_share_compact_on_mock_default_refuses_honestly(tmp_path):
    client = _client(tmp_path)
    tid = _seed(client)
    r = client.post(f"/chat/threads/{tid}/share", json={"mode": "compact"})
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "connect a model" in detail
    # The hint points at the path that DOES work offline.
    assert "full transcript" in detail


def test_share_compact_with_real_adapter(tmp_path):
    client = _client(tmp_path)
    tid = _seed(client)
    fake = _FakeAdapter()
    mgr = client.app.state.platform.providers
    mgr.get = lambda provider, model=None: fake
    r = client.post(
        f"/chat/threads/{tid}/share",
        json={"mode": "compact", "provider": "anthropic"},
    )
    assert r.status_code == 200
    out = r.json()
    assert out["provider"] == "anthropic"
    assert "— compacted" in out["content"]
    assert "Deadline confirmed: March 16." in out["content"]
    # The model saw the actual transcript, not a summary of a summary.
    sent = fake.calls[0]["messages"][0].content
    assert "March 16 (the 15th is a Sunday)." in sent


def test_share_compact_mock_default_fails_over_to_real(tmp_path):
    """Default provider mock + a connected real provider: the digest must come
    from the REAL adapter (fabricated mock digests destroy trust)."""
    from iron_jarvis.providers.adapters.mock import MockLLMAdapter

    client = _client(tmp_path)
    tid = _seed(client)
    fake = _FakeAdapter()
    mgr = client.app.state.platform.providers
    real_get, real_avail = mgr.get, mgr.available
    mgr.available = lambda p: p == "anthropic" or real_avail(p)
    mgr.get = (
        lambda provider, model=None: fake
        if provider == "anthropic"
        else real_get(provider, model)
    )
    resolved = mgr.get("mock")
    assert isinstance(resolved, MockLLMAdapter)  # the default really is mock
    r = client.post(f"/chat/threads/{tid}/share", json={"mode": "compact"})
    assert r.status_code == 200
    out = r.json()
    assert out["provider"] == "anthropic"
    assert "Deadline confirmed: March 16." in out["content"]


def test_share_compact_empty_digest_is_422(tmp_path):
    client = _client(tmp_path)
    tid = _seed(client)
    mgr = client.app.state.platform.providers
    mgr.get = lambda provider, model=None: _FakeAdapter(text="   ")
    r = client.post(
        f"/chat/threads/{tid}/share",
        json={"mode": "compact", "provider": "anthropic"},
    )
    assert r.status_code == 422
    assert "empty digest" in r.json()["detail"]


def test_share_compact_html_wraps_digest(tmp_path):
    client = _client(tmp_path)
    tid = _seed(client)
    mgr = client.app.state.platform.providers
    mgr.get = lambda provider, model=None: _FakeAdapter()
    r = client.post(
        f"/chat/threads/{tid}/share",
        json={"mode": "compact", "provider": "anthropic", "format": "html"},
    )
    assert r.status_code == 200
    page = r.json()["content"]
    assert page.lstrip().startswith("<!DOCTYPE html>")
    assert "Deadline confirmed: March 16." in page
