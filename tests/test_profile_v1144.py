"""The user-profile spine + language enforcement (v1.144.0).

The wave's claim is "one identity, injected at EVERY seam", so the headline
test here is :func:`test_profile_reaches_every_prompt_seam` — it drives the
REAL chat turn, the REAL stream, the REAL phone lane, a REAL agent session, and
a REAL round-table round through the actual app, capturing the system prompt
each one hands the model. That shape is deliberate: the v1.98.1 post-mortem
recorded that a test which mirrors the runtime's expression LOCALLY cannot
catch the runtime changing, and "chat has it, agents don't" is exactly the bug
this wave exists to fix — so a unit test of ``render()`` alone would have
passed all the way through the defect.

The rest covers the pure renderer, the store's contracts, the language
detector's false-positive guards, and the single-rewrite enforcement path.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.profile import ProfileStore, render
from iron_jarvis.profile.block import HOW_HEADER, MAX_BLOCK_CHARS, VOICE_HEADER, WHO_HEADER
from iron_jarvis.profile.language import detect_leak, strip_code
from iron_jarvis.profile.models import UserProfileRecord

CHINESE = "这是一个测试句子，用来检查语言泄漏的检测器是否有效。"


def _client(tmp_path):
    return TestClient(create_app(str(tmp_path)))


def _set_profile(client, **values):
    r = client.put("/profile", json={"values": values})
    assert r.status_code == 200, r.text
    return r.json()


# --------------------------------------------------------------------------- #
# (1) Schema registration — the v1.143.0 lesson, applied.
# --------------------------------------------------------------------------- #
def test_profile_table_is_registered_before_the_reconciler():
    """``_register_profile_models`` must put the table into SQLModel.metadata.

    Without it the reconciler walks a metadata that lacks the table, and a
    future additive column never self-heals on an existing DB — silently,
    because every profile seam swallows its own errors. Same trap the search
    index hit; same guard.
    """
    from sqlmodel import SQLModel

    from iron_jarvis.core.db import _register_profile_models

    _register_profile_models()
    assert "userprofilerecord" in SQLModel.metadata.tables


# --------------------------------------------------------------------------- #
# (2) The pure renderer.
# --------------------------------------------------------------------------- #
def test_empty_profile_renders_nothing():
    """An untouched install must send a byte-identical prompt to before."""
    assert render(UserProfileRecord()) == ""


def test_disabled_profile_renders_nothing_even_when_filled():
    rec = UserProfileRecord(about="anything", tone="warm", enabled=False)
    assert render(rec) == ""


def test_sections_render_in_order_with_their_headers():
    rec = UserProfileRecord(
        about="I run a small CPA firm.",
        response_length="brief",
        voice_card="Short sentences. No filler.",
    )
    out = render(rec)
    assert out.index(WHO_HEADER) < out.index(HOW_HEADER) < out.index(VOICE_HEADER)
    assert "I run a small CPA firm." in out
    assert "Short sentences. No filler." in out


def test_voice_section_carries_the_do_not_shrink_the_answer_guard():
    """The brief asked for imitation that does not drop parts of the question."""
    out = render(UserProfileRecord(voice_card="terse, lowercase"))
    assert "every part of every question" in out


def test_unknown_preset_key_is_used_verbatim_as_free_text():
    """Same three-way rule personas have always used — an unlisted preference
    is applied, not silently dropped."""
    out = render(UserProfileRecord(tone="like a Bloomberg terminal"))
    assert "like a Bloomberg terminal" in out


def test_a_preset_whose_instruction_is_empty_renders_nothing():
    """"Standard"/"balanced" mean "the model's default" — they must not spend
    tokens saying so."""
    assert render(UserProfileRecord(reading_level="standard", response_length="balanced")) == ""


def test_custom_formatting_rules_become_individual_bullets():
    out = render(UserProfileRecord(formatting_rules="No emoji.\n- Answer first."))
    assert "- No emoji." in out
    assert "- Answer first." in out


def test_block_is_bounded_against_a_pasted_memoir():
    rec = UserProfileRecord(
        about="A" * 10_000, formatting_rules="B" * 10_000, voice_card="C" * 10_000
    )
    assert len(render(rec)) <= MAX_BLOCK_CHARS


def test_include_narrows_to_the_preferences_only():
    """The round table's slice: accessibility/language yes, voice + about no."""
    rec = UserProfileRecord(
        about="I run a CPA firm.", response_length="brief", voice_card="terse"
    )
    out = render(rec, include=("how",))
    assert HOW_HEADER in out
    assert "I run a CPA firm." not in out
    assert "terse" not in out


def test_dyslexia_mode_emits_its_rules():
    out = render(UserProfileRecord(accessibility="dyslexia_friendly"))
    assert "One idea per sentence" in out
    assert "blank line between concepts" in out


# --------------------------------------------------------------------------- #
# (3) The store.
# --------------------------------------------------------------------------- #
def test_reading_a_missing_profile_does_not_create_a_row(tmp_path):
    """Every turn calls get(); a get-or-create would mint a row + a write lock
    on a machine where the user never opened /you."""
    from sqlmodel import select

    from iron_jarvis.core.db import open_db, session_scope

    engine = open_db(str(tmp_path / "t.db"))
    store = ProfileStore(engine)
    assert store.get().about == ""
    with session_scope(engine) as db:
        assert list(db.exec(select(UserProfileRecord))) == []


def test_save_is_a_partial_update(tmp_path):
    from iron_jarvis.core.db import open_db

    store = ProfileStore(open_db(str(tmp_path / "t.db")))
    store.save({"about": "kept", "tone": "warm"})
    store.save({"tone": "direct"})  # an older client that knows only `tone`
    rec = store.get()
    assert rec.about == "kept" and rec.tone == "direct"


def test_save_caps_and_ignores_unknown_keys(tmp_path):
    from iron_jarvis.core.db import open_db

    store = ProfileStore(open_db(str(tmp_path / "t.db")))
    rec = store.save({"about": "x" * 9000, "not_a_field": "nope"})
    assert len(rec.about) == 2000
    assert not hasattr(rec, "not_a_field")


def test_accessibility_preset_seeds_only_empty_fields(tmp_path):
    from iron_jarvis.core.db import open_db

    store = ProfileStore(open_db(str(tmp_path / "t.db")))
    store.save({"response_length": "thorough"})  # an explicit choice
    rec = store.apply_accessibility("dyslexia_friendly")
    assert rec.accessibility == "dyslexia_friendly"
    assert rec.formatting == "headings"       # was empty -> seeded
    assert rec.response_length == "thorough"  # was set   -> respected


# --------------------------------------------------------------------------- #
# (4) The language detector's guards. Each of these is a way the feature could
#     have become an annoyance rather than a fix.
# --------------------------------------------------------------------------- #
def test_detects_a_chinese_paragraph_in_an_english_reply():
    assert detect_leak(f"Here is the summary. {CHINESE}", "en") is not None


def test_no_language_configured_never_flags():
    assert detect_leak(CHINESE, "") is None


def test_a_single_quoted_term_is_not_leakage():
    """Ratio guard: one term inside a long English answer."""
    reply = "The Chinese word for tax is 税金, which appears on every form. " + (
        "This is a long English explanation that continues for a while. " * 6
    )
    assert detect_leak(reply, "en") is None


def test_chinese_inside_a_code_block_is_not_leakage():
    reply = "Here is the fix:\n\n```python\n# " + CHINESE + "\nx = 1\n```\n\nThat's it."
    assert detect_leak(reply, "en") is None
    assert CHINESE not in strip_code(reply)


def test_answering_a_chinese_question_in_chinese_is_not_leakage():
    """Guard (2): the user wrote in that script, so the reply is responsive."""
    assert detect_leak(CHINESE, "en", user_text="你好，请解释一下这个") is None


def test_english_reply_flags_when_the_target_language_is_chinese():
    """The rule is symmetric — it is not an English-only feature."""
    english = "This is a completely English answer that ignores the setting entirely."
    assert detect_leak(english, "zh") is not None


def test_same_script_drift_is_honestly_not_detected():
    """Pinned so nobody later 'fixes' this into a bag-of-words guesser: a
    Spanish reply under an English setting shares the Latin script and is NOT
    claimed to be caught. The module docstring says so; this asserts it."""
    assert detect_leak("Hola, aqui esta el resumen completo.", "en") is None


# --------------------------------------------------------------------------- #
# (5) THE HEADLINE — every seam.
# --------------------------------------------------------------------------- #
_MARK = "MARKER-PROFILE-ABOUT"


def _spy_complete(platform, seen: dict):
    """Capture the system prompt of any adapter-level completion."""
    real_get = platform.providers.get

    def spy_get(p, m=None):
        adapter = real_get(p, m)
        real_complete = adapter.complete

        async def spy(*, system, messages, tools):
            seen.setdefault("systems", []).append(system)
            return await real_complete(system=system, messages=messages, tools=tools)

        adapter.complete = spy
        return adapter

    platform.providers.get = spy_get
    return seen


async def test_profile_reaches_every_prompt_seam(tmp_path, monkeypatch):
    """chat + stream + phone + agent session + round table, one profile."""
    from iron_jarvis.agents.orchestrator import Orchestrator
    from iron_jarvis.comm import InboundMessage, MockChannel, Notifier
    from iron_jarvis.comm.inbound import InboundPoller
    from iron_jarvis.comm.threads import CommThreadStore
    from iron_jarvis.daemon.chat_turn import run_chat_turn

    app = create_app(str(tmp_path))
    client = TestClient(app)
    platform = app.state.platform
    _set_profile(client, about=_MARK, response_length="brief")

    seen: dict = {}
    _spy_complete(platform, seen)

    # (a) POST /chat
    assert client.post(
        "/chat", json={"messages": [{"role": "user", "content": "hi"}]}
    ).status_code == 200
    assert any(_MARK in s for s in seen["systems"]), "chat seam"

    # (b) POST /chat/stream — the lock-step inline copy.
    seen["systems"].clear()
    captured: dict = {}

    async def fake_stream(*, provider=None, model=None, system, messages, tools,
                          session_id=None, task_class=None):
        captured["system"] = system
        adapter = TestClient  # placeholder, replaced below
        adapter = platform.providers.get(
            provider or platform.router.default_provider, model
        )
        async for frame in adapter.stream(system=system, messages=messages, tools=tools):
            if frame.get("type") == "final":
                yield {**frame, "provider": adapter.provider, "model": adapter.model}
            else:
                yield frame

    platform.router.stream = fake_stream
    assert client.post(
        "/chat/stream", json={"messages": [{"role": "user", "content": "hi"}]}
    ).status_code == 200
    assert _MARK in captured["system"], "stream seam"

    # (c) An AGENT session — the seam that had NOTHING before this wave.
    seen["systems"].clear()
    assert client.post(
        "/sessions", json={"task": "do x", "wait": True}
    ).status_code == 200
    assert any(_MARK in s for s in seen["systems"]), "agent-runtime seam"

    # (d) The phone lane (it runs through chat_turn, so this pins the wiring).
    seen["systems"].clear()

    class _PhoneChannel(MockChannel):
        supports_inbound = True

        def has_credentials(self) -> bool:
            return True

    ch = _PhoneChannel(
        {"inbound_enabled": True, "chat_enabled": True, "allowed_senders": ["777"]}
    )
    notifier = Notifier()
    notifier.add_channel("tg", ch)
    poller = InboundPoller(
        notifier,
        Orchestrator(platform),
        platform.engine,
        event_bus=platform.event_bus,
        thread_store=CommThreadStore(platform.engine),
        chat_turn=run_chat_turn,
        personas=app.state.inbound_poller.personas,
        platform=platform,
    )
    await poller._handle(
        "tg", ch, InboundMessage(sender_id="777", text="hi", update_id=1, reply_to="777")
    )
    assert any(_MARK in s for s in seen["systems"]), "phone seam"

    # (e) The round table takes the PREFERENCES slice (see render's `include`).
    seen["systems"].clear()
    r = client.post(
        "/agents/threads",
        json={
            "title": "panel",
            "participants": [
                {"source": "builtin", "name": "builder", "role": "engineer"},
                {"source": "builtin", "name": "reviewer", "role": "critic"},
            ],
        },
    )
    assert r.status_code == 200, r.text
    tid = r.json()["id"]
    say = client.post(f"/agents/threads/{tid}/say", json={"message": "what do you think?"})
    assert say.status_code == 200, say.text
    panel = [s for s in seen.get("systems", [])]
    assert panel, "round table produced no completion"
    assert any("Keep answers SHORT" in s for s in panel), "round-table preferences"
    assert not any(_MARK in s for s in panel), "panel must not get the WHO section"


def test_no_profile_means_no_added_prompt_text(tmp_path):
    """The regression that matters most for existing installs: with an empty
    profile, not one character is added to the system prompt."""
    from iron_jarvis.profile.block import profile_block

    client = _client(tmp_path)
    assert profile_block(client.app.state.platform) == ""


def test_a_broken_profile_costs_its_block_not_the_turn(tmp_path, monkeypatch):
    from iron_jarvis.profile import block as mod

    monkeypatch.setattr(
        mod, "render", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    client = _client(tmp_path)
    assert mod.profile_block(client.app.state.platform) == ""
    assert client.post(
        "/chat", json={"messages": [{"role": "user", "content": "hi"}]}
    ).status_code == 200


# --------------------------------------------------------------------------- #
# (6) The persona voice now reaches agent runs (item 13's actual cause).
# --------------------------------------------------------------------------- #
def test_agent_runs_carry_the_configured_persona_voice(tmp_path):
    from iron_jarvis.personas.voice import VOICE_HEADER as PV_HEADER

    client = _client(tmp_path)
    client.post(
        "/chat/personas",
        json={"title": "Tax Ninja", "prompt": "TAX-NINJA-PROMPT: you are a stealthy CPA."},
    )
    assert client.put(
        "/settings", json={"values": {"default_persona": "tax-ninja"}}
    ).status_code == 200

    seen: dict = {}
    _spy_complete(client.app.state.platform, seen)
    assert client.post("/sessions", json={"task": "do x", "wait": True}).status_code == 200
    systems = seen["systems"]
    assert any("TAX-NINJA-PROMPT" in s for s in systems)
    # ...and it is SCOPED, so it cannot be read as a second role assignment.
    assert any(PV_HEADER in s and "does not change your role" in s for s in systems)


# --------------------------------------------------------------------------- #
# (7) Enforcement end-to-end: one rewrite, billed, honest either way.
# --------------------------------------------------------------------------- #
def _leaky_router(platform, replies: list[str], seen: dict):
    """Route completions through a canned list of replies, recording calls."""
    from iron_jarvis.providers.adapters.base import LLMResponse
    from iron_jarvis.providers.router import RouteResult

    async def fake_complete(*, provider=None, model=None, system, messages, tools,
                            task_class=None):
        seen.setdefault("calls", []).append({"system": system, "messages": messages})
        text = replies[min(len(seen["calls"]) - 1, len(replies) - 1)]
        return RouteResult(
            LLMResponse(text=text, tool_calls=[],
                        usage={"input_tokens": 10, "output_tokens": 5}),
            "mock", "mock",
        )

    platform.router.complete = fake_complete
    return seen


def test_leaked_reply_is_rewritten_once_and_the_note_is_honest(tmp_path, monkeypatch):
    client = _client(tmp_path)
    platform = client.app.state.platform
    _set_profile(client, language="en", enforce_language=True)

    seen: dict = {}
    _leaky_router(platform, [f"Sure. {CHINESE}", "Sure. Here is the answer."], seen)

    r = client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200
    body = r.json()
    assert len(seen["calls"]) == 2, "exactly ONE corrective completion"
    assert CHINESE not in body["reply"]
    assert "Here is the answer" in body["reply"]
    assert "rewritten in English" in body["reply"]


def test_a_model_that_keeps_leaking_keeps_its_original_reply_and_says_so(tmp_path):
    """A second wrong answer is not an improvement — and hiding that the
    setting is unachievable on this model would be the dishonest option."""
    client = _client(tmp_path)
    platform = client.app.state.platform
    _set_profile(client, language="en", enforce_language=True)

    seen: dict = {}
    _leaky_router(platform, [f"First. {CHINESE}", f"Second. {CHINESE}"], seen)

    body = client.post(
        "/chat", json={"messages": [{"role": "user", "content": "hi"}]}
    ).json()
    assert len(seen["calls"]) == 2, "still exactly one retry — never a loop"
    assert "First." in body["reply"]  # the ORIGINAL was kept
    assert "kept answering outside English" in body["reply"]


def test_enforcement_off_means_instruction_only(tmp_path):
    client = _client(tmp_path)
    platform = client.app.state.platform
    _set_profile(client, language="en", enforce_language=False)

    seen: dict = {}
    _leaky_router(platform, [f"Sure. {CHINESE}"], seen)
    body = client.post(
        "/chat", json={"messages": [{"role": "user", "content": "hi"}]}
    ).json()
    assert len(seen["calls"]) == 1, "no rewrite when enforcement is off"
    assert CHINESE in body["reply"]
    # ...but the instruction still rode in the prompt.
    assert "Write EVERY response in English" in seen["calls"][0]["system"]


def test_the_rewrite_goes_through_the_REAL_router_to_a_REAL_adapter(tmp_path):
    """The tests above stub ``router.complete``, which cannot see what the
    router hands an ADAPTER — and the adapters build their tool payload with
    ``for t in tools``, so passing None there is a TypeError inside the provider
    on exactly the path this feature exists for. This one drives the real router
    and asserts the adapter was called with a LIST both times.
    """
    from iron_jarvis.providers.adapters.base import LLMResponse

    client = _client(tmp_path)
    platform = client.app.state.platform
    _set_profile(client, language="en", enforce_language=True)

    seen: list = []
    replies = [f"Sure. {CHINESE}", "Sure. Here it is."]
    real_get = platform.providers.get

    def spy_get(p, m=None):
        adapter = real_get(p, m)

        async def canned(*, system, messages, tools):
            seen.append(tools)
            # Mimic every real adapter: build the tool payload by ITERATING.
            _ = [t for t in tools]
            return LLMResponse(
                text=replies[min(len(seen) - 1, len(replies) - 1)],
                tool_calls=[],
                usage={},
            )

        adapter.complete = canned
        return adapter

    platform.providers.get = spy_get
    r = client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200, r.text
    assert len(seen) == 2, "the rewrite reached an adapter"
    assert all(isinstance(t, list) for t in seen), f"tools must be a list, got {seen}"
    assert seen[1] == [], "a rewrite must not re-offer tools"
    assert "rewritten in English" in r.json()["reply"]


def test_a_clean_reply_costs_no_extra_completion(tmp_path):
    """The common path must be a regex, not a model call."""
    client = _client(tmp_path)
    _set_profile(client, language="en", enforce_language=True)
    seen: dict = {}
    _leaky_router(client.app.state.platform, ["A perfectly normal English reply."], seen)
    client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert len(seen["calls"]) == 1


def test_the_rewrite_is_billed_on_the_usage_ledger(tmp_path):
    """An invisible extra completion is how token spend becomes unexplainable."""
    from sqlmodel import select

    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.models import AgentRun

    client = _client(tmp_path)
    platform = client.app.state.platform
    _set_profile(client, language="en", enforce_language=True)
    _leaky_router(platform, [f"Sure. {CHINESE}", "Sure. Here it is."], {})

    client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    with session_scope(platform.engine) as db:
        runs = list(db.exec(select(AgentRun).where(AgentRun.session_id == "chat")))
    assert runs, "the chat turn was not billed at all"
    assert runs[-1].output_tokens == 10, "both completions counted (5 + 5)"


# --------------------------------------------------------------------------- #
# (8) Routes.
# --------------------------------------------------------------------------- #
def test_get_profile_returns_the_exact_string_the_model_will_see(tmp_path):
    client = _client(tmp_path)
    _set_profile(client, about="I am a CPA.", tone="direct")
    from iron_jarvis.profile.block import profile_block

    payload = client.get("/profile").json()
    assert payload["preview"] == profile_block(client.app.state.platform)
    assert payload["preview_chars"] == len(payload["preview"])
    assert payload["preview_limit"] == MAX_BLOCK_CHARS


def test_options_lists_every_vocabulary_the_page_needs(tmp_path):
    opts = _client(tmp_path).get("/profile/options").json()
    assert {"tone", "writing_style", "formatting", "reading_level",
            "response_length", "accessibility", "language"} <= set(opts)
    assert any(o["code"] == "en" for o in opts["language"])
    assert any(o["key"] == "dyslexia_friendly" for o in opts["accessibility"])


def test_profile_survives_a_restart_on_the_same_root(tmp_path):
    root = str(tmp_path)
    with TestClient(create_app(root)) as c1:
        c1.put("/profile", json={"values": {"about": "persisted"}})
    with TestClient(create_app(root)) as c2:
        assert c2.get("/profile").json()["profile"]["about"] == "persisted"


@pytest.mark.parametrize("mode", ["dyslexia_friendly", "screen_reader", ""])
def test_accessibility_modes_round_trip(tmp_path, mode):
    client = _client(tmp_path)
    r = client.post("/profile/accessibility", json={"mode": mode})
    assert r.status_code == 200
    assert r.json()["profile"]["accessibility"] == mode
