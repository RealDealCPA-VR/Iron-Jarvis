"""v1.330.0 (calm chat W11 M5): the receipt says when a turn ran without tools,
and names the endpoint that answered by the label the user gave it.

Live-hit in the wave-10 audit (fareal2__live__desk.png): with the composer on
"Auto tools", picking a fleet endpoint whose tool use is not verified served
the turn TEXT-ONLY (the explicit text-only pick, v1.125.0) and nothing said
so; the model then told the user it had no file-listing tool. And the
"answered by" tooltip named the raw provider id "fleet-sparkl4" while the
model menu calls the row "Spark proxy (L4)".

Pinned here, on BOTH chat lanes (POST /chat via chat_turn.run_chat_turn and
the /chat/stream done frame), through the real create_app:
  * route.text_only is True exactly when the daemon served a text-only pick
    (nothing armed, nothing offered), False on every other turn;
  * route.label is the fleet node's label, "" off the fleet;
  * both keys are LAST in the route object (the additive rule);
  * the armed-tools note names the label in plain words, no dash aside.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon import chat_turn
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import LLMAdapter, LLMResponse

_LABEL = "Spark proxy (L4)"
_PID = "fleet-sparkl4"


@pytest.fixture
def client(tmp_path) -> TestClient:
    with TestClient(create_app(str(tmp_path))) as c:
        yield c


class _FakeFleet(LLMAdapter):
    """A fleet endpoint whose tool use is NOT verified (tool_use False), with
    the node the real Fleet adapters carry (the label lives there). Subclasses
    LLMAdapter for the default single-chunk ``stream`` the stream lane uses."""

    provider = _PID
    model = "glm"

    def __init__(self, label: str = _LABEL, tool_use: bool = False) -> None:
        self.node = SimpleNamespace(label=label)
        self._tool_use = tool_use
        self.seen_tools: list | None = None

    def capabilities(self):
        return {
            "provider": _PID,
            "model": "glm",
            "tool_use": self._tool_use,
            "vision": False,
        }

    async def complete(self, *, system, messages, tools):
        self.seen_tools = tools
        return LLMResponse(text="glm says hi")


def _wire(client, monkeypatch, fake: _FakeFleet) -> _FakeFleet:
    platform = client.app.state.platform
    real_get = platform.providers.get
    monkeypatch.setattr(
        platform.providers,
        "get",
        lambda p, m=None: fake if p == _PID else real_get(p, m),
    )
    real_avail = platform.providers.available
    monkeypatch.setattr(
        platform.providers,
        "available",
        lambda n: True if n == _PID else real_avail(n),
    )
    return fake


def _done(text: str) -> dict:
    frames = []
    for block in text.split("\n\n"):
        lines = block.strip().splitlines()
        if not lines or lines[0] != "event: done":
            continue
        data = "".join(ln[len("data: "):] for ln in lines[1:] if ln.startswith("data: "))
        frames.append(json.loads(data))
    assert frames, text[-2000:]
    return frames[-1]


_HI = {"messages": [{"role": "user", "content": "list my folder"}]}


def test_text_only_pick_says_so_on_the_post_lane(client, monkeypatch):
    fake = _wire(client, monkeypatch, _FakeFleet())
    out = client.post("/chat", json={**_HI, "provider": _PID, "model": "glm"})
    assert out.status_code == 200, out.text
    route = out.json()["route"]
    assert out.json()["provider"] == _PID
    assert fake.seen_tools == []  # the daemon really offered nothing
    assert route["text_only"] is True
    assert route["label"] == _LABEL
    # Additive: the two new keys are the LAST two of the route object.
    assert list(route)[-2:] == ["label", "text_only"]


def test_text_only_pick_says_so_on_the_stream_lane(client, monkeypatch):
    fake = _wire(client, monkeypatch, _FakeFleet())
    r = client.post("/chat/stream", json={**_HI, "provider": _PID, "model": "glm"})
    assert r.status_code == 200, r.text
    route = _done(r.text)["route"]
    assert fake.seen_tools == []
    assert route["provider"] == _PID
    assert route["text_only"] is True
    assert route["label"] == _LABEL
    assert list(route)[-2:] == ["label", "text_only"]


def test_a_model_that_can_use_tools_is_not_text_only(client, monkeypatch):
    fake = _wire(client, monkeypatch, _FakeFleet(tool_use=True))
    flat = client.post("/chat", json={**_HI, "provider": _PID, "model": "glm"})
    streamed = client.post("/chat/stream", json={**_HI, "provider": _PID, "model": "glm"})
    assert flat.status_code == 200 and streamed.status_code == 200
    assert fake.seen_tools  # tools WERE offered
    assert flat.json()["route"]["text_only"] is False
    assert _done(streamed.text)["route"]["text_only"] is False
    # The label is about WHO answered, not about tools.
    assert flat.json()["route"]["label"] == _LABEL


def test_default_route_is_never_text_only_and_has_no_label(client):
    flat = client.post("/chat", json=_HI)
    streamed = client.post("/chat/stream", json=_HI)
    for route in (flat.json()["route"], _done(streamed.text)["route"]):
        assert route["text_only"] is False
        assert route["label"] == ""


def test_armed_tools_note_names_the_label_in_plain_words(client, monkeypatch):
    _wire(client, monkeypatch, _FakeFleet())
    body = {**_HI, "provider": _PID, "model": "glm", "tools": ["list_folder"]}
    flat = client.post("/chat", json=body).json()["reply"]
    streamed = _done(client.post("/chat/stream", json=body).text)["reply"]
    want = f"_Note: {_LABEL} can't run tools, so this turn was answered text only._"
    for reply in (flat, streamed):
        assert want in reply
        assert "—" not in reply and " - " not in reply
        assert _PID not in reply


def test_route_label_is_wording_only():
    """Off the fleet, an unlabelled node and a failing router all answer ""."""

    class _Router:
        def __init__(self, answer):
            self.answer = answer

        def _endpoint_label(self, wanted):
            if isinstance(self.answer, Exception):
                raise self.answer
            return self.answer

    p = lambda a: SimpleNamespace(router=_Router(a))  # noqa: E731
    assert chat_turn.route_label(p("x"), "anthropic") == ""
    assert chat_turn.route_label(p(_PID), _PID) == ""  # no label: the id back
    assert chat_turn.route_label(p(RuntimeError("boom")), _PID) == ""
    assert chat_turn.route_label(p("  Spark   proxy  "), _PID) == "Spark proxy"
    assert chat_turn.route_label(SimpleNamespace(), _PID) == ""
