"""Wave C (readiness plan 0h.5, item 29): the Slack / Telegram / email
destination switches are said in plain words everywhere they appear. The
catalog ``GET /comm/channel-types`` is where the words came from — "Enable
two-way (true/false)" and "Set to true …" were the wire's words drawn on
the screen (the Slack form had no copy of its own) — so the catalog itself
speaks plainly now, and each switch is typed ``bool`` so the page draws a
real toggle for every destination.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app

WIRE_WORDS = re.compile(r"true/false|set to true|set to false|\btrue\b|\bfalse\b", re.I)


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(str(tmp_path / "ij")))


def _fields(client) -> dict[str, list[dict]]:
    res = client.get("/comm/channel-types")
    assert res.status_code == 200, res.text
    return {t["type"]: t["fields"] for t in res.json()["types"]}


def test_no_destination_field_speaks_the_wires_words(client):
    for ctype, fields in _fields(client).items():
        for f in fields:
            for part in ("label", "help"):
                text = str(f.get(part) or "")
                assert not WIRE_WORDS.search(text), (ctype, f["key"], part, text)


def test_the_switches_are_typed_bool_so_every_destination_draws_a_toggle(client):
    fields = _fields(client)
    for ctype in ("slack", "telegram", "email"):
        by_key = {f["key"]: f for f in fields[ctype]}
        assert by_key["inbound_enabled"].get("type") == "bool", ctype
        assert "listen" in by_key["inbound_enabled"]["label"].lower(), ctype
    for ctype in ("slack", "telegram"):
        by_key = {f["key"]: f for f in fields[ctype]}
        assert by_key["chat_enabled"].get("type") == "bool", ctype
        assert by_key["chat_enabled"]["label"] == "Chat with Iron Jarvis", ctype
