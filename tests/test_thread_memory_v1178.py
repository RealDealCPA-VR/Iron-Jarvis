"""v1.178.0: an agent thread's worth outlives the thread.

``POST /agents/threads/{id}/remember`` is the agent-side twin of
``POST /chat/threads/{id}/remember``. A panel could argue its way to a decision
and the decision died with the thread — rounds were persisted and searchable,
but nothing ever crossed into the long-term memory the app reads back on later
turns.

Four properties, each mutation-proven:

* the committed path writes what was EXTRACTED (the distilled note, attributed)
  into the chosen LTM source, and reports the provider that produced it;
* the mock-only path REFUSES to distill — it stores the verbatim panel and says
  so — because a mock would otherwise fabricate a memory of a real conversation
  (and its scripted "Wrote RESULT.md" would land in the brain as a fact);
* preview (the DEFAULT) writes NOTHING while still returning the extracted
  items and the exact content that would land;
* an unknown thread id 404s, and the other refusals are honest 400s.

Offline throughout: the "real" provider is a stand-in adapter injected via the
provider manager, deliberately not ``MockLLMAdapter``.
"""

from __future__ import annotations

import asyncio
import threading
import time
import types

from fastapi.testclient import TestClient

import iron_jarvis.agents.threads as _threads_mod
from iron_jarvis.agents.threads import (
    AgentThreads,
    _remember_budgets,
    panel_transcript,
    review_items,
)
from iron_jarvis.daemon.app import create_app
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
    {
        "who": "builtin:ghost",
        "role": "researcher",
        "source": "remote",
        "content": "",
        "error": "ghost is offline (not registered) — skipped.",
    },
]


class _FakeAdapter:
    """A REAL-adapter stand-in (deliberately not MockLLMAdapter)."""

    provider = "anthropic"
    model = "claude-opus-4-8"

    def __init__(self, text: str = "") -> None:
        self._text = text or (
            "## Decision\n"
            "- planner: file Form 7004 by March 16 (the 15th is a Sunday)\n"
            "## Open\n"
            "- critic: the state extension is separate, due April 15\n"
        )
        self.calls: list[dict] = []

    async def complete(self, *, system, messages, tools):
        self.calls.append({"system": system, "messages": messages, "tools": tools})
        return LLMResponse(text=self._text)


def _client(tmp_path):
    return TestClient(create_app(str(tmp_path)))


def _seed(client, msgs=None) -> str:
    tid = client.post(
        "/agents/threads", json={"title": "Extension call", "participants": _PANEL}
    ).json()["id"]
    entries = _ROUND if msgs is None else msgs
    if entries:
        AgentThreads(client.app.state.platform.engine)._append(tid, entries)
    return tid


def _notes(folder) -> str:
    """Everything actually on disk in a markdown memory store.

    Read from the FILES, not from ``ltm.search`` — a search hit's ``snippet``
    is truncated around the match, so asserting on it proves only that the
    query matched, never that the note carries what it claims to carry.
    """
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(folder.glob("*.md")))


def _brain(tmp_path):
    return tmp_path / ".ironjarvis" / "brain"


# --- the happy path: a commit writes what was extracted -----------------------


def test_commit_writes_the_distilled_panel_into_memory(tmp_path):
    client = _client(tmp_path)
    tid = _seed(client)
    fake = _FakeAdapter()
    client.app.state.platform.providers.get = lambda p, m=None: fake

    r = client.post(
        f"/agents/threads/{tid}/remember",
        json={"provider": "anthropic", "preview": False},
    )
    assert r.status_code == 200
    out = r.json()
    assert out["ok"] is True and out["preview"] is False
    assert out["distilled"] is True and out["provider"] == "anthropic"
    assert out["ref"] and "note" not in out
    assert out["title"] == "Panel: Extension call"
    assert out["participants"] == ["planner", "critic"]
    # The extracted items are the reviewable claims, carrying their section.
    assert "Decision: planner: file Form 7004 by March 16 (the 15th is a Sunday)" in (
        out["items"]
    )
    # The model saw the panel VERBATIM, with speakers attributed — the whole
    # reason this is not chat's two-speaker renderer.
    sent = fake.calls[0]["messages"][0].content
    assert "### planner (lead)" in sent and "### critic (critic)" in sent
    assert "state extension is separate" in sent
    # …and what it produced is genuinely IN the brain, not just in the response.
    blob = _notes(_brain(tmp_path))
    assert "March 16" in blob and "Extension call" in blob
    assert client.app.state.platform.ltm.search("Form 7004", source="brain")


# --- the mock path: refuse to distill, keep the words ------------------------


def test_mock_only_refuses_to_distill_and_stores_the_verbatim_panel(tmp_path):
    """No real provider: the memory is the panel's own words. A mock digest
    would be a fabricated recollection of a real conversation — and the mock's
    scripted 'Wrote RESULT.md' would enter the brain as an established fact."""
    client = _client(tmp_path)
    tid = _seed(client)

    r = client.post(
        f"/agents/threads/{tid}/remember", json={"mode": "distill", "preview": False}
    )
    assert r.status_code == 200
    out = r.json()
    assert out["distilled"] is False
    assert "verbatim excerpt" in out["note"]
    assert "provider" not in out
    blob = _notes(_brain(tmp_path))
    assert "Form 7004 by March 16" in blob  # the panel's actual words
    assert "planner (lead)" in blob  # attributed, not flattened
    assert "RESULT.md" not in blob  # the mock never spoke
    # An honest skip is part of how the panel concluded — it is not dropped.
    assert "ghost is offline" in blob


# --- suggest-don't-act: preview is the default and writes nothing ------------


def test_preview_is_the_default_and_writes_nothing(tmp_path):
    client = _client(tmp_path)
    tid = _seed(client)
    fake = _FakeAdapter()
    client.app.state.platform.providers.get = lambda p, m=None: fake
    before = _notes(_brain(tmp_path))

    r = client.post(f"/agents/threads/{tid}/remember", json={"provider": "anthropic"})
    assert r.status_code == 200
    out = r.json()
    assert out["preview"] is True and out["ref"] == ""
    # It still shows exactly what WOULD land — a preview with no content is
    # a diff nobody can review.
    assert out["items"] and "March 16" in " ".join(out["items"])
    assert "Committed from the agent panel" in out["content"]
    assert _notes(_brain(tmp_path)) == before
    assert "March 16" not in _notes(_brain(tmp_path))

    # The commit is the EXPLICIT call, and only then does the brain grow.
    r2 = client.post(
        f"/agents/threads/{tid}/remember",
        json={"provider": "anthropic", "preview": False},
    )
    assert r2.status_code == 200 and r2.json()["ref"]
    assert "March 16" in _notes(_brain(tmp_path))


# --- honest refusals ---------------------------------------------------------


def test_unknown_thread_404s_and_bad_input_400s(tmp_path):
    client = _client(tmp_path)
    tid = _seed(client)
    assert (
        client.post("/agents/threads/athr_nope/remember", json={}).status_code == 404
    )
    assert (
        client.post(f"/agents/threads/{tid}/remember", json={"mode": "summarize"})
        .status_code
        == 400
    )
    assert (
        client.post(f"/agents/threads/{tid}/remember", json={"source": "ghost"})
        .status_code
        == 400
    )
    empty = _seed(client, msgs=[])
    assert client.post(f"/agents/threads/{empty}/remember", json={}).status_code == 400


def test_full_mode_reaches_a_custom_source(tmp_path):
    client = _client(tmp_path)
    tid = _seed(client)
    vault = tmp_path / "vault"
    vault.mkdir()
    client.post(
        "/ltm/sources",
        json={"name": "panel_vault", "kind": "markdown", "path": str(vault)},
    )
    r = client.post(
        f"/agents/threads/{tid}/remember",
        json={"mode": "full", "source": "panel_vault", "preview": False},
    )
    assert r.status_code == 200
    out = r.json()
    assert out["source"] == "panel_vault" and out["distilled"] is False
    assert "note" not in out  # full mode was ASKED for; nothing degraded
    assert "March 16" in _notes(vault)
    assert _notes(_brain(tmp_path)) == ""  # the named source, and only it


# --- the pure pieces ---------------------------------------------------------


def test_panel_transcript_keeps_every_speaker_distinct(tmp_path):
    text = panel_transcript("Extension call", _PANEL, _ROUND, None)
    assert "### You" in text
    assert "### planner (lead)" in text and "### critic (critic)" in text
    assert "Iron Jarvis" not in text  # chat's two-speaker vocabulary must not leak
    assert "_ghost is offline (not registered) — skipped._" in text


def test_review_items_falls_back_when_there_are_no_bullets():
    """The verbatim path has headings and prose, not bullets. An empty review
    list would read as 'there is nothing to commit'."""
    assert review_items("## Key\n- one\n- two\n") == ["Key: one", "Key: two"]
    plain = review_items("### planner (lead)\n\nFile by March 16.\n")
    assert plain == ["planner (lead): File by March 16."]
    assert review_items("") == []


# --- review findings (adversarial pass) --------------------------------------


def test_only_an_explicit_false_defeats_the_preview(tmp_path):
    """A body that has not DECIDED must not commit.

    ``bool(body.get("preview", True))`` maps every falsy JSON value onto False,
    so ``{"preview": null}`` — the shape a client sends for a field it has not
    set — wrote the panel into the brain and reported ``"preview": false``.
    That is suggest-don't-act failing in the one direction nothing can undo, so
    only a recognisable false commits.
    """
    client = _client(tmp_path)
    tid = _seed(client)
    fake = _FakeAdapter()
    client.app.state.platform.providers.get = lambda p, m=None: fake

    for undecided in (None, "", {}):
        r = client.post(
            f"/agents/threads/{tid}/remember",
            json={"provider": "anthropic", "preview": undecided},
        )
        assert r.status_code == 200
        assert r.json()["preview"] is True, undecided
        assert r.json()["ref"] == ""
        assert _notes(_brain(tmp_path)) == ""

    # …and the ways a client really does spell "false" all still commit.
    for yes in (False, "false", "0"):
        r = client.post(
            f"/agents/threads/{tid}/remember",
            json={"provider": "anthropic", "preview": yes},
        )
        assert r.json()["preview"] is False and r.json()["ref"], yes


def test_the_verbatim_excerpt_is_clipped_to_CHATS_budget(tmp_path):
    """The ONE piece genuinely shared with chat is the pair of budgets, and the
    clip is what makes sharing them mean anything.

    An unclipped commit is not merely long: a memory note is read back later as
    authoritative, so a transcript that lost its middle without saying so would
    have the app quoting a third of a panel as the whole of it. Pin both halves
    — the marker is present, and the size is chat's number, not a second one
    that can drift away from it.
    """
    input_budget, verbatim_budget = _remember_budgets()
    from iron_jarvis.daemon.routes import chat as _chat

    assert (input_budget, verbatim_budget) == (
        _chat._REMEMBER_INPUT,
        _chat._REMEMBER_VERBATIM,
    )

    client = _client(tmp_path)
    long_round = [
        {"who": "builtin:planner", "role": "lead", "source": "builtin",
         "content": f"turn {i}: " + "x" * 400}
        for i in range(120)
    ]
    tid = _seed(client, msgs=long_round)
    r = client.post(
        f"/agents/threads/{tid}/remember", json={"mode": "full", "preview": False}
    )
    assert r.status_code == 200
    blob = _notes(_brain(tmp_path))
    assert "[… middle of the panel omitted for length …]" in blob
    # Header + trailer ride along, so allow a little slack over the budget —
    # what must never happen is the whole 48k transcript landing.
    assert len(blob) < verbatim_budget + 1_000


async def test_remember_never_blocks_the_event_loop(tmp_path):
    """The daemon is ONE loop (v1.153.1) and every step here is a blocking one:
    a DB read, a JSON parse of up to 400 entries, a markdown render, and an
    ``ltm.append`` that WRITES — to a local vault, or over the network for a
    Notion/cloud/http_rag source. Deleting any offload used to leave this file
    green.

    Asserts both halves the repo's offload tests assert: STRUCTURAL (the work
    ran on a worker thread) and BEHAVIOURAL (the loop kept ticking while the
    slow append ran).
    """
    client = _client(tmp_path)
    tid = _seed(client)
    platform = client.app.state.platform
    seen: dict[str, str] = {}
    main = threading.main_thread().name

    def _record(name, fn):
        def _wrapped(*a, **kw):
            seen[name] = threading.current_thread().name
            return fn(*a, **kw)

        return _wrapped

    conn = platform.ltm.get("brain")
    real_append = conn.append

    def _slow_append(title, content):
        seen["append"] = threading.current_thread().name
        time.sleep(0.30)  # a network-backed memory store, honestly modelled
        return real_append(title, content)

    conn.append = _slow_append
    threads = AgentThreads(platform.engine)
    threads.get = _record("get", threads.get)
    original_render = _threads_mod.panel_transcript
    original_items = _threads_mod.review_items
    original_load = _threads_mod._load_round
    _threads_mod.panel_transcript = _record("render", original_render)
    _threads_mod.review_items = _record("items", original_items)
    _threads_mod._load_round = _record("load", original_load)

    ticks = 0
    stop = False

    async def _ticker():
        nonlocal ticks
        while not stop:
            await asyncio.sleep(0.01)
            ticks += 1

    ticker = asyncio.ensure_future(_ticker())
    try:
        out = await threads.remember(
            tid,
            types.SimpleNamespace(platform=platform),
            mode="full",
            preview=False,
        )
    finally:
        stop = True
        ticker.cancel()
        try:
            await ticker
        except asyncio.CancelledError:
            pass
        _threads_mod.panel_transcript = original_render
        _threads_mod.review_items = original_items
        _threads_mod._load_round = original_load
        conn.append = real_append

    assert out["ref"]
    for step in ("get", "load", "render", "items", "append"):
        assert seen.get(step), f"{step} never ran"
        assert seen[step] != main, f"{step} ran ON the event loop"
    assert ticks >= 5, f"event loop was starved (only {ticks} ticks)"


# ------------------------------------------------- what was approved lands ---


class _CountingAdapter:
    """Answers DIFFERENTLY every call — so a second distillation is visible."""

    def __init__(self):
        self.calls = 0

    async def complete(self, **kw):
        from iron_jarvis.providers.adapters.base import LLMResponse

        self.calls += 1
        return LLMResponse(text=f"- distillation number {self.calls}")


def test_the_commit_writes_the_TEXT_THE_USER_APPROVED(tmp_path):
    """THE REVIEW FINDING. `preview=False` re-ran the whole ladder — a SECOND
    distillation — so the text the user read was not the text that reached
    memory. A model asked twice does not answer twice the same, which makes the
    preview a decoration rather than a decision."""
    client = _client(tmp_path)
    tid = _seed(client)
    fake = _CountingAdapter()
    client.app.state.platform.providers.get = lambda p, m=None: fake

    preview = client.post(
        f"/agents/threads/{tid}/remember", json={"provider": "anthropic"}
    ).json()
    assert preview["preview"] is True and fake.calls == 1
    assert "distillation number 1" in preview["content"]

    committed = client.post(
        f"/agents/threads/{tid}/remember",
        json={"provider": "anthropic", "preview": False, "content": preview["content"]},
    ).json()
    assert committed["ref"], "nothing was written"
    # THE POINT: no second model call, and what landed is what was approved.
    assert fake.calls == 1, "the commit re-distilled"
    assert "distillation number 1" in committed["content"]
    assert "distillation number 2" not in _notes(_brain(tmp_path))
    assert "distillation number 1" in _notes(_brain(tmp_path))
    # ...and it does not CLAIM a distillation it did not perform.
    assert committed["distilled"] is False
    assert "approved" in (committed.get("note") or "")


def test_a_commit_without_approved_content_still_runs_the_ladder(tmp_path):
    """Backwards compatible: a caller that never previewed is unchanged."""
    client = _client(tmp_path)
    tid = _seed(client)
    fake = _CountingAdapter()
    client.app.state.platform.providers.get = lambda p, m=None: fake

    out = client.post(
        f"/agents/threads/{tid}/remember",
        json={"provider": "anthropic", "preview": False},
    ).json()
    assert out["preview"] is False and out["ref"]
    assert fake.calls == 1 and out["distilled"] is True


def test_the_preview_and_the_commit_share_one_header(tmp_path):
    """Two code paths build the header now (the approved commit skips the
    ladder). If they drift, the note approved and the note stored differ by
    their first line."""
    client = _client(tmp_path)
    tid = _seed(client)
    fake = _CountingAdapter()
    client.app.state.platform.providers.get = lambda p, m=None: fake

    preview = client.post(
        f"/agents/threads/{tid}/remember", json={"provider": "anthropic"}
    ).json()
    header = preview["content"].split(chr(10) + chr(10), 1)[0]
    assert "Committed from the agent panel" in header
    committed = client.post(
        f"/agents/threads/{tid}/remember",
        json={"provider": "anthropic", "preview": False, "content": preview["content"]},
    ).json()
    # COUNT, do not merely find it: `startswith` is trivially true when the
    # header lands TWICE, which is exactly the bug this assertion missed.
    assert committed["content"].startswith(header)
    assert committed["content"].count("Committed from the agent panel") == 1
    # ...and what landed is byte-identical to what was approved.
    assert committed["content"] == preview["content"]


def test_the_review_list_never_truncates_silently():
    """REVIEW FINDING. Both caps bit silently in the ONE payload the user reads
    before an irreversible write: a 41st claim vanished, and a long claim lost
    its tail with nothing to say so. A preview that shows less than what lands
    means the user approves one thing and another is written."""
    from iron_jarvis.agents.threads import review_items

    many = "\n".join(f"- claim {n}" for n in range(60))
    items = review_items(many)
    assert any("only the first" in i for i in items), "40 of 60 shown in silence"

    long_claim = "- " + ("x" * 900)
    got = review_items(long_claim)
    assert got[0].endswith("…"), "a clipped claim did not say it was clipped"
    assert len(got[0]) <= 300
