"""v1.185.0: ONE remember ladder, called by both surfaces.

Chat and the round table both commit a conversation to long-term memory, and
until now they answered "what mattered here, and what do we store when no real
model can answer it?" in two places. That was not an oversight anyone could fix
in passing: chat's ladder lived INLINE inside its route handler, as a closure
inside ``register(app, d)``, so there was no symbol to call. The panel therefore
re-derived the ladder and reached back into ``daemon.routes.chat`` for the two
budgets — an ``agents`` module importing a ``daemon.routes`` module, the
layering upside down.

Four properties, each mutation-proven by reverting the fix and watching the
named assertion go red:

* BOTH live routes reach ``memory.commit.distill_or_excerpt`` — driven through
  the real endpoints, so deleting either call site is caught here rather than
  in a review;
* the layering inversion is gone: ``agents.threads`` imports nothing from
  ``daemon.routes``;
* the distill prompt is a PARAMETER, and each surface passes its own — the
  panel's attribution and never-resolve-a-disagreement instructions are
  meaningless for a two-party chat, and a single shared prompt would have to
  drop one of them;
* the behaviour the ladder exists to guarantee is now identical on both
  surfaces by construction: offline, neither fabricates, both degrade to a
  verbatim excerpt and both SAY SO.

Offline throughout — the "real" provider is a stand-in adapter that is
deliberately not ``MockLLMAdapter``.
"""

from __future__ import annotations

import inspect

from fastapi.testclient import TestClient

import iron_jarvis.agents.threads as _threads_mod
import iron_jarvis.daemon.routes.chat as _chat_mod
from iron_jarvis.agents.threads import AgentThreads
from iron_jarvis.daemon.app import create_app
from iron_jarvis.memory import commit as _commit
from iron_jarvis.providers.adapters.base import LLMResponse

_PANEL = [
    {"source": "builtin", "name": "planner", "role": "lead"},
    {"source": "builtin", "name": "critic", "role": "critic"},
]

_ROUND = [
    {"who": "user", "content": "Do we file the 1120-S extension this year?"},
    {
        "who": "builtin:planner",
        "role": "lead",
        "source": "builtin",
        "content": "Yes — file Form 7004 by March 16, the 15th is a Sunday.",
    },
    {
        "who": "builtin:critic",
        "role": "critic",
        "source": "builtin",
        "content": "Agreed, but the state extension is separate and due April 15.",
    },
]

_CHAT_MSGS = [
    {"role": "user", "content": "Do we file the 1120-S extension this year?"},
    {"role": "assistant", "content": "Yes — Form 7004 by March 16."},
]


class _FakeAdapter:
    """A REAL-adapter stand-in (deliberately not MockLLMAdapter)."""

    provider = "anthropic"
    model = "claude-opus-4-8"

    def __init__(self, text: str = "- decided: file Form 7004\n") -> None:
        self._text = text
        self.calls: list[dict] = []

    async def complete(self, *, system, messages, tools):
        self.calls.append({"system": system, "messages": messages, "tools": tools})
        return LLMResponse(text=self._text)


def _client(tmp_path):
    return TestClient(create_app(str(tmp_path)))


def _seed_panel(client) -> str:
    tid = client.post(
        "/agents/threads", json={"title": "Extension call", "participants": _PANEL}
    ).json()["id"]
    AgentThreads(client.app.state.platform.engine)._append(tid, _ROUND)
    return tid


def _seed_chat(client) -> str:
    return client.put(
        "/chat/threads/new", json={"title": "Extension call", "messages": _CHAT_MSGS}
    ).json()["id"]


def _spy(monkeypatch):
    """Record every ladder call while still running the real ladder."""
    seen: list[dict] = []
    real = _commit.distill_or_excerpt

    async def _wrapped(d, **kw):
        seen.append(dict(kw))
        return await real(d, **kw)

    monkeypatch.setattr(_commit, "distill_or_excerpt", _wrapped)
    return seen


# --- both live routes go through the ONE ladder ------------------------------


def test_both_remember_routes_call_the_shared_ladder(tmp_path, monkeypatch):
    """Driven through the REAL endpoints. Revert either call site to its inline
    copy and this sees one call instead of two — which is the whole point: a
    test that imported the ladder and called it directly would stay green while
    a route quietly grew a second implementation beside it."""
    client = _client(tmp_path)
    chat_id = _seed_chat(client)
    panel_id = _seed_panel(client)
    fake = _FakeAdapter()
    client.app.state.platform.providers.get = lambda p, m=None: fake
    seen = _spy(monkeypatch)

    assert (
        client.post(
            f"/chat/threads/{chat_id}/remember", json={"provider": "anthropic"}
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/agents/threads/{panel_id}/remember",
            json={"provider": "anthropic", "preview": False},
        ).status_code
        == 200
    )

    assert len(seen) == 2, "each surface must reach the shared ladder exactly once"
    subjects = [c["subject"] for c in seen]
    assert subjects == ["conversation", "panel"]
    # Same ladder, same budgets — the drift this module exists to prevent.
    assert _threads_mod._remember_budgets() == (
        _commit.REMEMBER_INPUT,
        _commit.REMEMBER_VERBATIM,
    )


# --- the layering inversion is gone ------------------------------------------


def test_agents_threads_does_not_import_from_daemon_routes(tmp_path):
    """``agents`` reaching into ``daemon.routes`` for constants was the shape of
    the old sharing. Asserted on the SOURCE because an import that only runs
    inside a function body would not show up any other way — and the previous
    version's was exactly that (lazy, to dodge a cycle)."""
    src = inspect.getsource(_threads_mod)
    assert "daemon.routes" not in src
    assert "from ..daemon" not in src


# --- the prompt is a parameter, and each surface passes its own ---------------


def test_each_surface_passes_its_own_distill_prompt(tmp_path, monkeypatch):
    client = _client(tmp_path)
    chat_id = _seed_chat(client)
    panel_id = _seed_panel(client)
    fake = _FakeAdapter()
    client.app.state.platform.providers.get = lambda p, m=None: fake
    seen = _spy(monkeypatch)

    client.post(f"/chat/threads/{chat_id}/remember", json={"provider": "anthropic"})
    client.post(
        f"/agents/threads/{panel_id}/remember",
        json={"provider": "anthropic", "preview": False},
    )

    chat_system, panel_system = seen[0]["system"], seen[1]["system"]
    assert chat_system == _chat_mod.CHAT_DISTILL_SYSTEM
    assert panel_system == _threads_mod.PANEL_DISTILL_SYSTEM
    assert chat_system != panel_system
    # WHY they must differ, asserted rather than asserted-about: a panel note is
    # worth re-reading because it says who concluded what and what stayed open.
    assert "ATTRIBUTE" in panel_system
    assert "never resolve a disagreement the panel left open" in panel_system
    # Both instructions are meaningless with one user and one assistant, and a
    # chat model told to name speakers would invent them.
    assert "ATTRIBUTE" not in chat_system
    # The prompts genuinely reached the model, not just the ladder.
    assert fake.calls[0]["system"] == chat_system
    assert fake.calls[1]["system"] == panel_system


# --- one ladder means one honesty guarantee ----------------------------------


def test_offline_both_surfaces_degrade_identically_and_say_so(tmp_path):
    """The honest-mock rule is what the ladder is FOR. With no real provider,
    neither surface may fabricate a memory of a real conversation: both store
    the words that were actually said and both carry a note saying that is what
    happened. Sharing the ladder is what makes this one guarantee rather than
    two that agree today."""
    client = _client(tmp_path)
    chat_id = _seed_chat(client)
    panel_id = _seed_panel(client)

    chat = client.post(
        f"/chat/threads/{chat_id}/remember", json={"mode": "distill"}
    ).json()
    panel = client.post(
        f"/agents/threads/{panel_id}/remember",
        json={"mode": "distill", "preview": False},
    ).json()

    for out in (chat, panel):
        assert out["distilled"] is False
        assert "verbatim excerpt" in out["note"]
        assert "provider" not in out
    # Identical wording, because it is now literally the same sentence.
    assert chat["note"] == panel["note"]

    blob = "\n".join(
        p.read_text(encoding="utf-8")
        for p in sorted((tmp_path / ".ironjarvis" / "brain").glob("*.md"))
    )
    assert "Form 7004" in blob  # the real words landed
    assert "RESULT.md" not in blob  # the mock never spoke


# --- the clip contract is one contract ---------------------------------------


def test_a_clipped_transcript_always_says_it_was_clipped(tmp_path):
    """A silent clip is the failure mode the marker exists to prevent: the model
    would digest the last third and present it as the whole conversation, and a
    memory note is read back later as authoritative. Held for BOTH budgets and
    both subjects from one function."""
    long_text = "x" * (_commit.REMEMBER_INPUT * 2)
    for subject in ("conversation", "panel"):
        clipped = _commit.clip_with_marker(
            long_text, _commit.REMEMBER_VERBATIM, _commit.omission_marker(subject)
        )
        assert len(clipped) < len(long_text)
        assert f"middle of the {subject} omitted for length" in clipped
        # The DISTILL marker additionally tells the model to carry the omission
        # into the note — only that text is read by something that could
        # otherwise describe a third of a conversation as all of it.
        for_model = _commit.omission_marker(subject, for_model=True)
        assert "note this in the memory" in for_model

    # Under budget, nothing is touched — no marker on a whole transcript.
    short = "the whole thing"
    assert (
        _commit.clip_with_marker(short, _commit.REMEMBER_VERBATIM, "MARKER") == short
    )
