"""The identity spine at two more seams (north-star fix plan 6.9, v1.506.0).

The spine rule (``docs/rules/memory.md``, "The identity spine reaches EVERY
prompt seam") says a surface that writes to the user carries the profile
block (``profile/``) and the assistant's voice (``personas/voice.py``). Two
did not:

* the FINAL ANSWER of a planned agent run (``agents/decompose.assemble``,
  ``_ASSEMBLE_SYSTEM``): the steps ran with the profile (the runtime adds it),
  but the one call that writes what the user reads was sent with a fixed
  English system prompt, so "answer in Spanish" was lost exactly there;
* ``POST /skills/{name}/apply`` (the Skills page's "Use this skill"): its
  system prompt was only the skill's playbook.

Each case drives the REAL code with a REAL profile and persona through the
real router and adapter, and reads the system prompt the model was handed.
"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app

VOICE_MARK = "VOICE-MARK-6-9: you speak like a calm librarian."


def _spy(platform, seen: list[str]) -> None:
    """Record the system prompt of every adapter-level completion."""
    real_get = platform.providers.get

    def spy_get(p, m=None):
        adapter = real_get(p, m)
        real_complete = adapter.complete

        async def spy(*, system, messages, tools):
            seen.append(system)
            return await real_complete(system=system, messages=messages, tools=tools)

        adapter.complete = spy
        return adapter

    platform.providers.get = spy_get


def _client_with_identity(tmp_path) -> TestClient:
    """An app whose profile says "answer in Spanish" and whose default persona
    carries a marker, set through the same routes the pages use."""
    client = TestClient(create_app(str(tmp_path)))
    r = client.put("/profile", json={"values": {"language": "es", "about": "ABOUT-MARK-6-9"}})
    assert r.status_code == 200, r.text
    r = client.post("/chat/personas", json={"title": "Calm Librarian", "prompt": VOICE_MARK})
    assert r.status_code == 200, r.text
    r = client.put("/settings", json={"values": {"default_persona": "calm-librarian"}})
    assert r.status_code == 200, r.text
    return client


def _has_identity(system: str) -> bool:
    return "Spanish" in system and "ABOUT-MARK-6-9" in system and VOICE_MARK in system


async def test_a_planned_runs_final_answer_carries_the_profile_and_voice(tmp_path):
    from iron_jarvis.agents.decompose import StepResult, assemble

    client = _client_with_identity(tmp_path)
    platform = client.app.state.platform
    seen: list[str] = []
    _spy(platform, seen)

    run = SimpleNamespace(id="run-6-9", steps=0, input_tokens=0, output_tokens=0)
    session = SimpleNamespace(
        id="sess-6-9", task="sum the receipts and write a note",
        provider=platform.config.default_provider, model=platform.config.default_model,
    )
    results = [
        StepResult(index=0, goal="sum the receipts", ok=True, output="total 42", verified="model"),
        StepResult(index=1, goal="write the note", ok=True, output="note written", verified="model"),
    ]
    text = await assemble(SimpleNamespace(p=platform), run, session, results)
    assert text.strip()
    assert seen, "the assemble call never reached a model"
    final_system = seen[-1]
    # The honesty instruction is still the first thing the model reads.
    assert final_system.startswith("You are finishing a task")
    assert _has_identity(final_system), final_system


async def test_a_planned_runs_final_answer_adds_only_the_voice_without_a_profile(tmp_path):
    """An empty profile adds not one character (the profile's own contract);
    the default persona's voice is added exactly as the runtime adds it."""
    from iron_jarvis.agents.decompose import _ASSEMBLE_SYSTEM, StepResult, assemble
    from iron_jarvis.personas.voice import voice_section

    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    seen: list[str] = []
    _spy(platform, seen)
    run = SimpleNamespace(id="run-x", steps=0, input_tokens=0, output_tokens=0)
    session = SimpleNamespace(id="sess-x", task="t", provider=platform.config.default_provider,
                              model=platform.config.default_model)
    await assemble(SimpleNamespace(p=platform), run, session,
                   [StepResult(index=0, goal="g", ok=True, output="o", verified="model")])
    assert seen and seen[-1] == _ASSEMBLE_SYSTEM + voice_section(platform)


def test_applying_a_skill_carries_the_profile_and_voice(tmp_path):
    sd = tmp_path / ".ironjarvis" / "skills" / "note-maker"
    sd.mkdir(parents=True)
    (sd / "SKILL.md").write_text(
        "---\nname: note-maker\ndescription: makes notes\n---\nSKILL-MARK-6-9 write three bullets.",
        encoding="utf-8",
    )
    client = _client_with_identity(tmp_path)
    seen: list[str] = []
    _spy(client.app.state.platform, seen)
    r = client.post("/skills/note-maker/apply", json={"request": "notes on the plan"})
    assert r.status_code == 200, r.text
    assert seen, "the skill was never sent to a model"
    system = seen[-1]
    # The playbook still leads; the identity follows it.
    assert system.startswith("Fulfil the user's request by FOLLOWING this skill playbook")
    assert "SKILL-MARK-6-9" in system
    assert _has_identity(system), system
