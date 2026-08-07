"""Train Jarvis on me (v1.145.0) — samples in, an editable voice card out.

Three properties carry this wave, and each has its own section below:

1. **Suggest, don't act** — deriving a card stores NOTHING. It is a proposal
   the user edits and saves through the ordinary PUT /profile.
2. **Real provider or an honest refusal** — a fabricated voice card would then
   be injected into every prompt forever, so the mock must never produce one.
3. **Style, never content** — the card rides in every system prompt, including
   ones routed to a cloud model, so a client name that leaks into it leaks
   everywhere. Both the prompt and a post-filter defend this.

Plus the guard the brief named explicitly: imitating the user's voice must not
shrink the answer.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.profile import render
from iron_jarvis.profile.models import UserProfileRecord, WritingSampleRecord
from iron_jarvis.profile.training import (
    DERIVE_CHAR_BUDGET,
    MAX_SAMPLES,
    MIN_DERIVE_CHARS,
    NO_SIGNAL,
    SampleStore,
    build_prompt,
    clean_card,
    derive,
)

# Long enough to clear MIN_DERIVE_CHARS in two samples.
LONG = (
    "The quarterly numbers came in flat. I would rather say that plainly than "
    "dress it up. Here is what moved, here is what did not, and here is the one "
    "line that matters for next month. No preamble. "
) * 3


def _client(tmp_path):
    return TestClient(create_app(str(tmp_path)))


class _FakeAdapter:
    """A REAL-adapter stand-in (deliberately not MockLLMAdapter), so the
    real-provider gate opens. Same pattern as tests/test_skill_learning_routes."""

    provider = "anthropic"
    model = "claude-opus-4-8"

    def __init__(self, text: str):
        self._text = text
        self.calls: list[dict] = []

    async def complete(self, *, system, messages, tools):
        from iron_jarvis.providers.adapters.base import LLMResponse

        self.calls.append({"system": system, "messages": messages})
        return LLMResponse(text=self._text)


def _use_real_adapter(client, text: str) -> _FakeAdapter:
    fake = _FakeAdapter(text)
    client.app.state.platform.providers.get = lambda provider, model=None: fake
    return fake


def _sample(text: str, label: str = "s") -> WritingSampleRecord:
    return WritingSampleRecord(label=label, text=text)


# --------------------------------------------------------------------------- #
# (1) The store.
# --------------------------------------------------------------------------- #
def test_the_sample_table_is_registered_before_the_reconciler():
    """Rides ``_register_profile_models`` with the profile row — same silent
    failure mode if it ever moves to a module nothing imports at boot."""
    from sqlmodel import SQLModel

    from iron_jarvis.core.db import _register_profile_models

    _register_profile_models()
    assert "writingsamplerecord" in SQLModel.metadata.tables


def test_samples_round_trip_and_delete(tmp_path):
    from iron_jarvis.core.db import open_db

    store = SampleStore(open_db(str(tmp_path / "t.db")))
    rec = store.add(label="email", text="Short and blunt.")
    assert [r.id for r in store.list()] == [rec.id]
    assert store.delete(rec.id) is True
    assert store.list() == []
    assert store.delete(rec.id) is False


def test_an_empty_sample_is_refused(tmp_path):
    from iron_jarvis.core.db import open_db

    store = SampleStore(open_db(str(tmp_path / "t.db")))
    with pytest.raises(ValueError):
        store.add(label="x", text="   ")


def test_the_sample_limit_is_enforced_with_a_useful_message(tmp_path):
    from iron_jarvis.core.db import open_db

    store = SampleStore(open_db(str(tmp_path / "t.db")))
    for i in range(MAX_SAMPLES):
        store.add(label=f"s{i}", text="writing")
    with pytest.raises(ValueError) as exc:
        store.add(label="one more", text="writing")
    assert "remove one first" in str(exc.value)


def test_the_list_endpoint_does_not_ship_whole_samples(tmp_path):
    """20 x 20k characters to render a list is how a page gets slow."""
    client = _client(tmp_path)
    big = ("word " * 4000).strip()
    client.post("/profile/samples", json={"label": "essay", "text": big})
    row = client.get("/profile/samples").json()["samples"][0]
    assert row["chars"] == len(big)
    assert len(row["excerpt"]) <= 280
    assert "text" not in row


# --------------------------------------------------------------------------- #
# (2) The prompt: every sample represented.
# --------------------------------------------------------------------------- #
def test_every_sample_gets_an_even_share_of_the_budget():
    """One long essay must not crowd out five short emails — taking the first N
    characters overall would do exactly that."""
    samples = [_sample("A" * 50_000, "essay")] + [
        _sample(f"short email {i}", f"e{i}") for i in range(5)
    ]
    prompt = build_prompt(samples)
    for i in range(5):
        assert f"short email {i}" in prompt
    assert len(prompt) <= DERIVE_CHAR_BUDGET + 2000  # headers + markers


def test_build_prompt_of_nothing_is_empty():
    assert build_prompt([]) == ""
    assert build_prompt([_sample("   ")]) == ""


# --------------------------------------------------------------------------- #
# (3) Cleaning the model's reply.
# --------------------------------------------------------------------------- #
def test_no_signal_produces_no_card():
    """An honest 'I can't tell' must never become an invented card."""
    assert clean_card(NO_SIGNAL) == ""
    assert clean_card(f"  {NO_SIGNAL}  ") == ""


def test_bullets_headings_and_fences_are_stripped():
    out = clean_card("```\n## Voice\n- Short sentences.\n1. Plain words.\n```")
    assert out == "Short sentences.\nPlain words."


def test_lines_that_narrate_the_content_are_dropped():
    """Rule 3's post-filter: the card goes into every prompt, so a line that
    smuggles in what the samples were ABOUT would leak it everywhere."""
    out = clean_card(
        "Short sentences.\n"
        "Writes about the Henderson audit and their tax position.\n"
        "Plain words."
    )
    assert "Henderson" not in out
    assert "Short sentences." in out and "Plain words." in out


def test_the_card_is_capped():
    assert len(clean_card("\n".join(f"line {i} " + "x" * 400 for i in range(30)))) <= 900


# --------------------------------------------------------------------------- #
# (4) Derivation: honest outcomes, never an invented card.
# --------------------------------------------------------------------------- #
async def test_a_thin_corpus_is_refused_before_any_model_call():
    called = False

    async def complete(system, prompt):
        nonlocal called
        called = True
        return "Short sentences."

    card, reason = await derive(complete, [_sample("two words")])
    assert card == "" and str(MIN_DERIVE_CHARS) in reason
    assert called is False, "a thin corpus must not cost a completion"


async def test_a_declining_model_yields_a_reason_not_a_card():
    async def complete(system, prompt):
        return NO_SIGNAL

    card, reason = await derive(complete, [_sample(LONG), _sample(LONG)])
    assert card == ""
    assert "consistent voice" in reason


async def test_a_good_reply_becomes_a_card():
    async def complete(system, prompt):
        assert "SAMPLE 1" in prompt
        return "- Short declarative sentences.\n- Plain words, no jargon."

    card, reason = await derive(complete, [_sample(LONG), _sample(LONG)])
    assert reason == ""
    assert card == "Short declarative sentences.\nPlain words, no jargon."


def test_too_thin_is_checked_by_the_route_before_the_provider_gate(tmp_path):
    """Telling someone to connect a model when their real problem is two pasted
    sentences sends them off to fix the wrong thing."""
    client = _client(tmp_path)
    client.post("/profile/samples", json={"label": "x", "text": "too short"})
    r = client.post("/profile/voice/derive")
    assert r.status_code == 200
    assert r.json()["card"] == ""
    assert str(MIN_DERIVE_CHARS) in r.json()["reason"]


def test_derive_refuses_honestly_when_only_the_mock_is_available(tmp_path):
    """The honest-mock rule — the ONE thing in this app it is worst to invent.

    Nothing is stubbed here on purpose: a fresh install's default provider IS
    the mock, so this is the out-of-the-box behaviour, not a contrived one.
    """
    client = _client(tmp_path)
    client.post("/profile/samples", json={"label": "a", "text": LONG})
    client.post("/profile/samples", json={"label": "b", "text": LONG})
    r = client.post("/profile/voice/derive")
    assert r.status_code == 400
    assert "real model" in r.json()["detail"]
    # ...and nothing was written to the profile.
    assert client.get("/profile").json()["profile"]["voice_card"] == ""


def test_deriving_stores_nothing_until_the_user_saves(tmp_path):
    """Suggest-don't-act, end to end."""
    client = _client(tmp_path)
    client.post("/profile/samples", json={"label": "a", "text": LONG})
    client.post("/profile/samples", json={"label": "b", "text": LONG})
    fake = _use_real_adapter(client, "Short declarative sentences.")

    proposed = client.post("/profile/voice/derive").json()
    assert fake.calls, "the model was genuinely consulted"
    assert proposed["card"] == "Short declarative sentences."
    assert client.get("/profile").json()["profile"]["voice_card"] == "", "not stored yet"

    client.put(
        "/profile",
        json={"values": {"voice_card": proposed["card"], "voice_source": proposed["source"]}},
    )
    saved = client.get("/profile").json()["profile"]
    assert saved["voice_card"] == "Short declarative sentences."
    assert saved["voice_source"] == "2 writing samples"


def test_samples_survive_derivation_so_the_card_can_be_redone(tmp_path):
    """A summary you cannot re-run is a summary you cannot correct."""
    client = _client(tmp_path)
    client.post("/profile/samples", json={"label": "a", "text": LONG})
    client.post("/profile/samples", json={"label": "b", "text": LONG})
    _use_real_adapter(client, "Short sentences.")
    client.post("/profile/voice/derive")
    assert len(client.get("/profile/samples").json()["samples"]) == 2


# --------------------------------------------------------------------------- #
# (5) The guard the brief named: match the voice, answer the whole question.
# --------------------------------------------------------------------------- #
def test_the_voice_never_licenses_a_shorter_answer(tmp_path):
    """"imitate the user's communication style without ignoring parts of their
    questions" — the guard must ride WITH the card, in the same block, and the
    card must not be able to displace it (both are capped)."""
    rec = UserProfileRecord(voice_card="Terse. One line answers. Never elaborate.")
    block = render(rec)
    assert "Terse." in block
    assert "never change what you answer" in block.lower()
    assert "address every part of every question in full" in block


def test_a_pasted_memoir_as_a_voice_card_cannot_evict_the_guard():
    block = render(UserProfileRecord(voice_card="Z" * 50_000))
    assert "every part of every question" in block


def test_the_voice_card_reaches_agent_runs_too(tmp_path):
    """Wave 1 wired the seams; this pins that the card specifically rides them —
    a voice learned in /train that only chat used would be the same defect in a
    new coat."""
    client = _client(tmp_path)
    client.put("/profile", json={"values": {"voice_card": "VOICE-CARD-MARKER"}})

    systems: list[str] = []
    platform = client.app.state.platform
    real_get = platform.providers.get

    def spy_get(p, m=None):
        adapter = real_get(p, m)
        real_complete = adapter.complete

        async def spy(*, system, messages, tools):
            systems.append(system)
            return await real_complete(system=system, messages=messages, tools=tools)

        adapter.complete = spy
        return adapter

    platform.providers.get = spy_get
    assert client.post("/sessions", json={"task": "do x", "wait": True}).status_code == 200
    assert any("VOICE-CARD-MARKER" in s for s in systems)


# --------------------------------------------------------------------------- #
# (6) The wizard's status board reads the EXISTING subsystems.
# --------------------------------------------------------------------------- #
def test_training_status_counts_what_is_already_connected(tmp_path):
    client = _client(tmp_path)
    before = client.get("/profile/training").json()
    assert before["about"] is False and before["samples"] == 0
    assert before["memory_bases"] >= 1  # the built-in brain

    client.put("/profile", json={"values": {"about": "I run a CPA firm."}})
    client.post("/profile/samples", json={"label": "a", "text": LONG})
    after = client.get("/profile/training").json()
    assert after["about"] is True
    # Stored stripped (the store trims before capping) — assert on that.
    assert after["samples"] == 1 and after["sample_chars"] == len(LONG.strip())


def test_training_status_survives_a_broken_subsystem(tmp_path, monkeypatch):
    """One dead base must cost its own number, not the page."""
    client = _client(tmp_path)
    platform = client.app.state.platform
    monkeypatch.setattr(
        type(platform.ltm),
        "sources",
        lambda self: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    r = client.get("/profile/training")
    assert r.status_code == 200
    assert r.json()["memory_bases"] == 0


# --------------------------------------------------------------------------- #
# (7) Documents ride the same converter /ltm/ingest-document uses.
# --------------------------------------------------------------------------- #
def test_a_document_sample_is_converted_to_text(tmp_path):
    import base64

    client = _client(tmp_path)
    body = base64.b64encode(b"# Notes\n\nI write in short lines.").decode()
    r = client.post(
        "/profile/samples",
        json={"label": "", "filename": "notes.md", "content_b64": body},
    )
    assert r.status_code == 200, r.text
    sample = r.json()["sample"]
    assert sample["origin"] == "document:notes.md"
    assert "short lines" in sample["excerpt"]


def test_an_unreadable_document_is_an_honest_error(tmp_path):
    client = _client(tmp_path)
    r = client.post(
        "/profile/samples",
        json={"filename": "empty.txt", "content_b64": base64_of(b"   ")},
    )
    assert r.status_code == 422
    assert "no readable text" in r.json()["detail"]


def base64_of(data: bytes) -> str:
    import base64

    return base64.b64encode(data).decode()
