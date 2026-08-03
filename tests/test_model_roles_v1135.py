"""v1.135.0 — step-aware routing: role-based model selection in multi-step runs.

``config.model_roles`` maps a step ROLE ("plan", "synthesize", "extract",
"judge", "vision") to ``"provider:model"`` or a bare ``"model"``;
``providers/roles.resolve_role`` turns the mapping into the pair a call site
requests — THROUGH the router, never around it. These tests pin:

* the parse forms + fail-open fallback semantics (unmapped / unknown /
  unavailable / probe-error / no-manager → fallbacks unchanged, never raises);
* decompose plan/judge/assemble one-shots hit the role-resolved provider while
  the per-step mini-loops keep the SESSION's provider (scripted fakes, the
  v1.132.0 pattern), with the additive "role" key on ``llm.completed``;
* batch extraction/synthesis one-shots carry the resolved pair (the v1.133.0
  scripted-router pattern) and the mock-refusal honesty survives;
* view_image resolves the "vision" role with the same fallback rules;
* the config field defaults to {} (absent persisted key included) and the
  feature is FULLY dormant when the mapping is empty — pinned with strict fakes
  whose ``complete`` rejects any extra kwargs, and a v1.132.0-shaped e2e.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from iron_jarvis.core.config import Config, load_config
from iron_jarvis.core.events import EventType
from iron_jarvis.core.models import AgentType, SessionStatus
from iron_jarvis.documents.batch import run_batch
from iron_jarvis.providers.adapters.base import LLMAdapter, LLMMessage, LLMResponse
from iron_jarvis.providers.roles import RoleResolution, resolve_role
from iron_jarvis.providers.router import RouteResult
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.images import ViewImageTool


# ------------------------------------------------------------------ fixtures --
class _Providers:
    """Availability-probe fake mirroring ``ProviderManager.available``'s use:
    records every probe; ``boom=True`` raises (the probe-failure path)."""

    def __init__(self, available=(), boom=False):
        self._available = set(available)
        self._boom = boom
        self.probes: list[str] = []

    def available(self, name: str) -> bool:
        self.probes.append(name)
        if self._boom:
            raise RuntimeError("probe exploded")
        return name in self._available


def _cfg(**roles) -> SimpleNamespace:
    return SimpleNamespace(model_roles=dict(roles))


def _resolve(config, providers, role="plan", fp="sess", fm="small"):
    return resolve_role(
        config, providers, role, fallback_provider=fp, fallback_model=fm
    )


# ------------------------------------------------------- (a) parse + fallback --
def test_provider_model_form_resolves_when_available():
    res = _resolve(_cfg(plan="dgx:big"), _Providers(["dgx"]))
    assert (res.provider, res.model) == ("dgx", "big")
    assert res.applied and res.note == ""


def test_bare_model_form_keeps_provider_and_never_probes():
    providers = _Providers([])  # nothing available — must not matter
    res = _resolve(_cfg(plan="big-model"), providers)
    assert (res.provider, res.model) == ("sess", "big-model")
    assert res.applied and res.note == ""
    assert providers.probes == []  # bare model names no provider → no probe
    # With no fallback provider (default-route callers), only the model rides.
    res = _resolve(_cfg(plan="big-model"), providers, fp=None, fm=None)
    assert (res.provider, res.model) == (None, "big-model") and res.applied


def test_whitespace_and_empty_values():
    res = _resolve(_cfg(plan=" dgx : big "), _Providers(["dgx"]))
    assert (res.provider, res.model) == ("dgx", "big") and res.applied
    for value in ("", "   "):
        res = _resolve(_cfg(plan=value), _Providers(["dgx"]))
        assert (res.provider, res.model) == ("sess", "small")
        assert not res.applied and res.note == ""


def test_provider_only_form_uses_provider_default_model():
    res = _resolve(_cfg(plan="dgx:"), _Providers(["dgx"]))
    assert (res.provider, res.model) == ("dgx", None) and res.applied


def test_unmapped_role_falls_back_silently():
    res = _resolve(_cfg(judge="dgx:big"), _Providers(["dgx"]), role="plan")
    assert (res.provider, res.model) == ("sess", "small")
    assert not res.applied and res.note == ""


def test_unknown_or_unavailable_provider_falls_back_with_flag():
    for providers in (_Providers([]), _Providers([], boom=True), None):
        res = _resolve(_cfg(plan="ghost:big"), providers)
        assert (res.provider, res.model) == ("sess", "small")
        assert not res.applied
        assert "role_fallback" in res.note and "ghost" in res.note


def test_mapping_naming_the_current_pair_is_not_applied():
    # No audit noise (and no extra kwargs) when the mapping changes nothing.
    res = _resolve(_cfg(plan="sess:small"), _Providers(["sess"]))
    assert (res.provider, res.model) == ("sess", "small")
    assert not res.applied and res.note == ""
    res = _resolve(_cfg(plan="small"), _Providers([]))
    assert not res.applied


def test_bad_config_shapes_never_raise():
    for config in (None, SimpleNamespace(), SimpleNamespace(model_roles="oops"),
                   SimpleNamespace(model_roles={"plan": 7})):
        res = _resolve(config, _Providers(["dgx"]))
        assert (res.provider, res.model) == ("sess", "small") and not res.applied


# ---------------------------------------------------------- (b) config field --
def test_config_default_empty_and_absent_key_is_clean(tmp_path, monkeypatch):
    assert Config(project_root=tmp_path, home=tmp_path / ".ironjarvis").model_roles == {}
    monkeypatch.delenv("IRONJARVIS_HOME", raising=False)
    root = tmp_path / "proj"
    home = root / ".ironjarvis"
    home.mkdir(parents=True)
    # A persisted config WITHOUT the key loads cleanly to {} (pydantic default).
    (home / "config.toml").write_text('default_provider = "mock"\n', encoding="utf-8")
    assert load_config(root).model_roles == {}
    # A persisted mapping is honored.
    (home / "config.toml").write_text(
        '[model_roles]\nplan = "dgx:big"\nextract = "cheap"\n', encoding="utf-8"
    )
    assert load_config(root).model_roles == {"plan": "dgx:big", "extract": "cheap"}


def test_model_roles_persist_roundtrip(tmp_path, monkeypatch):
    """The WRITE path: persist_config_values serializes the dict as a proper
    [model_roles] TOML table, and a LATER scalar-only save merges without
    clobbering it (the settings-save-after-roles-save sequence)."""
    from iron_jarvis.core.config import persist_config_values

    monkeypatch.delenv("IRONJARVIS_HOME", raising=False)
    root = tmp_path / "proj"
    home = root / ".ironjarvis"
    home.mkdir(parents=True)
    persist_config_values(
        home,
        {"default_provider": "mock", "model_roles": {"plan": "dgx:big", "extract": "cheap"}},
    )
    persist_config_values(home, {"default_model": "m"})  # later save must merge
    cfg = load_config(root)
    assert cfg.model_roles == {"plan": "dgx:big", "extract": "cheap"}
    assert (cfg.default_provider, cfg.default_model) == ("mock", "m")


# ------------------------------------------------------------- (c) decompose --
class _TextOnly(LLMAdapter):
    """Text-only scripted adapter (tool_use False → prompted wrap), the
    v1.132.0 fake pattern. Records every call."""

    def __init__(self, replies, provider="local-x", model="llama3"):
        self.provider = provider
        self.model = model
        self._replies = list(replies)
        self.calls: list[tuple[str, list[LLMMessage], list]] = []

    def capabilities(self):
        return {
            "provider": self.provider,
            "model": self.model,
            "tool_use": False,
            "vision": False,
        }

    async def complete(self, *, system, messages, tools):
        self.calls.append((system, list(messages), list(tools)))
        return LLMResponse(
            text=self._replies.pop(0), usage={"input_tokens": 2, "output_tokens": 3}
        )


_LONG_TASK = (
    "Create a file named hello.txt containing a friendly greeting for the "
    "user, and after that produce a short revenue summary of everything done "
    "so far, keeping any files inside the session workspace so they can be "
    "reviewed later by the accountant."
)

#: Step 1 verifies deterministically (file), step 2 needs the model JUDGE.
_PLAN_JSON = json.dumps(
    {
        "steps": [
            {
                "goal": "write hello.txt",
                "success_criteria": "workspace contains hello.txt",
                "tools": ["write_file"],
            },
            {
                "goal": "summarize revenue",
                "success_criteria": "the summary mentions revenue",
            },
        ]
    }
)

_WRITE_HELLO = (
    "```tool_call\n"
    '{"name": "write_file", "arguments": {"path": "hello.txt",'
    ' "content": "Hello there!"}}\n'
    "```"
)


async def test_decompose_roles_route_one_shots_but_not_mini_loops(
    platform, orchestrator
):
    """plan/judge/assemble one-shots hit the ROLE provider; the per-step
    mini-loops stay on the SESSION provider; llm.completed carries the additive
    "role" key exactly on the rerouted calls."""
    brain = _TextOnly(
        [
            _PLAN_JSON,  # 1: plan
            '{"pass": true, "reason": "mentions revenue"}',  # 2: judge (step 2)
            "Assembled by brain.",  # 3: assemble
        ],
        provider="local-brain",
        model="brainy",
    )
    sess = _TextOnly(
        [
            _WRITE_HELLO,  # step 1, round 1 — fenced write_file
            "Wrote hello.txt with the greeting.",  # step 1, round 2 (final)
            "Revenue grew 12 percent this quarter.",  # step 2, round 1 (final)
        ],
        provider="local-sess",
    )
    platform.providers.register("local-brain", lambda model=None: brain)
    platform.providers.register("local-sess", lambda model=None: sess)
    platform.config.model_roles = {
        "plan": "local-brain:brainy",
        "judge": "local-brain:brainy",
        "synthesize": "local-brain:brainy",
    }
    events: list = []
    platform.event_bus.add_handler(lambda e: events.append(e))

    session = await orchestrator.run(_LONG_TASK, AgentType.BUILDER, provider="local-sess")
    assert session.status is SessionStatus.COMPLETED
    assert (Path(session.workspace_path) / "hello.txt").exists()
    final = orchestrator.transcript(session.id)["runs"][0]["result"]
    assert "Assembled by brain." in final

    # The brain saw EXACTLY the three one-shots, in role order.
    assert len(brain.calls) == 3
    assert "task planner" in brain.calls[0][0]
    assert "strict verifier" in brain.calls[1][0]
    assert "finishing a task" in brain.calls[2][0]
    # The session adapter ran ONLY the mini-loops — never a one-shot contract.
    assert len(sess.calls) == 3
    for system, _, _ in sess.calls:
        assert "task planner" not in system
        assert "strict verifier" not in system
        assert "finishing a task" not in system

    # Audit: the rerouted one-shots carry the additive "role" key + the
    # resolved provider; the mini-loop rounds carry NO role key.
    done = [e for e in events if e.type == EventType.LLM_COMPLETED]
    with_role = [e for e in done if "role" in e.payload]
    assert [e.payload["role"] for e in with_role] == ["plan", "judge", "synthesize"]
    assert all(e.payload["provider"] == "local-brain" for e in with_role)
    without_role = [e for e in done if "role" not in e.payload]
    assert without_role and all(
        e.payload["provider"] == "local-sess" for e in without_role
    )


async def test_decompose_unavailable_role_provider_falls_back_to_session(
    platform, orchestrator
):
    """A mapping to an unknown provider must change NOTHING: every call — the
    plan one-shot included — stays on the session provider, and no llm.completed
    event grows a "role" key."""
    sess = _TextOnly(
        [
            _PLAN_JSON,
            _WRITE_HELLO,
            "Wrote hello.txt.",
            "Revenue grew 12 percent.",
            '{"pass": true, "reason": "ok"}',
            "All done.",
        ],
        provider="local-solo",
    )
    platform.providers.register("local-solo", lambda model=None: sess)
    platform.config.model_roles = {"plan": "ghost-nope:big", "judge": "ghost-nope:big"}
    events: list = []
    platform.event_bus.add_handler(lambda e: events.append(e))

    session = await orchestrator.run(_LONG_TASK, AgentType.BUILDER, provider="local-solo")
    assert session.status is SessionStatus.COMPLETED
    assert len(sess.calls) == 6  # every scripted reply consumed, all on-session
    assert "task planner" in sess.calls[0][0]
    done = [e for e in events if e.type == EventType.LLM_COMPLETED]
    assert done and all("role" not in e.payload for e in done)


async def test_decompose_dormant_when_model_roles_empty(platform, orchestrator):
    """The v1.132.0 e2e shape with model_roles at its {} default: same call
    count, same provider throughout, honest failure footer intact, and zero
    "role" keys — byte-for-byte dormancy."""
    assert platform.config.model_roles == {}  # the config default
    inner = _TextOnly(
        [
            json.dumps(
                {
                    "steps": [
                        {
                            "goal": "write hello.txt",
                            "success_criteria": "workspace contains hello.txt",
                            "tools": ["write_file"],
                        },
                        {
                            "goal": "produce missing.txt",
                            "success_criteria": "workspace contains missing.txt",
                        },
                    ]
                }
            ),
            _WRITE_HELLO,
            "Wrote hello.txt with the greeting.",
            "I could not create missing.txt.",
            "Still unable to create missing.txt.",
            "hello.txt was written; missing.txt could not be produced.",
        ],
        provider="local-dorm",
    )
    platform.providers.register("local-dorm", lambda model=None: inner)
    events: list = []
    platform.event_bus.add_handler(lambda e: events.append(e))
    session = await orchestrator.run(_LONG_TASK, AgentType.BUILDER, provider="local-dorm")
    assert session.status is SessionStatus.COMPLETED
    assert len(inner.calls) == 6  # plan + (2 + 1 + 1 retry) mini rounds + assemble
    final = orchestrator.transcript(session.id)["runs"][0]["result"]
    assert "FAILED" in final and "produce missing.txt" in final
    done = [e for e in events if e.type == EventType.LLM_COMPLETED]
    assert done and all("role" not in e.payload for e in done)


# ------------------------------------------------------------------ (d) batch --
def _ext_reply(summary):
    return json.dumps(
        {
            "summary": summary,
            "facts": [],
            "entities": {"people": [], "orgs": [], "dates": [], "amounts": []},
            "figures": [],
        }
    )


MD_REPLY = "# Batch Report\n\n- combined finding one"


class _RoleRouter:
    """The v1.133.0 scripted-router pattern + provider/model kwarg capture and
    a ``manager`` availability probe (what run_batch resolves against)."""

    def __init__(self, replies, provider="anthropic", available=()):
        self.replies = list(replies)
        self.provider = provider
        self.manager = _Providers(available)
        self.calls: list[tuple[str, "str | None", "str | None"]] = []

    async def complete(
        self, *, system, messages, tools, task_class=None, provider=None, model=None
    ):
        assert len(messages) == 1 and messages[0].role == "user"
        self.calls.append((system, provider, model))
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return RouteResult(LLMResponse(text=item), self.provider, "test-model")


class _StrictRouter:
    """``complete`` REJECTS provider/model kwargs — running the dormant path
    against it proves the call shape is byte-for-byte the pre-v1.135.0 one."""

    def __init__(self, replies, provider="anthropic"):
        self.replies = list(replies)
        self.provider = provider

    async def complete(self, *, system, messages, tools, task_class=None):
        return RouteResult(LLMResponse(text=self.replies.pop(0)), self.provider, "m")


def _docs(folder: Path, **bodies: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name, body in bodies.items():
        (folder / name.replace("_", ".")).write_text(body, encoding="utf-8")


async def test_batch_extract_and_synthesize_roles_resolved(tmp_path):
    src = tmp_path / "docs"
    _docs(src, a_txt="alpha body", b_txt="bravo body")
    router = _RoleRouter(
        [_ext_reply("Alpha"), _ext_reply("Bravo"), MD_REPLY], available=["fleet"]
    )
    cfg = _cfg(extract="fleet:cheap", synthesize="fleet:big")
    res = await run_batch(src, tmp_path / "out", router, output="docx", config=cfg)
    assert (res["processed"], res["failed"], res["synthesis_errors"]) == (2, [], [])
    assert len(res["deliverables"]) == 1
    # Both extraction one-shots rode the cheap model; synthesis the big one.
    assert [(p, m) for _, p, m in router.calls[:2]] == [("fleet", "cheap")] * 2
    assert router.calls[2][1:] == ("fleet", "big")
    # The roles were probed against the router's OWN manager, once per role.
    assert router.manager.probes == ["fleet", "fleet"]


async def test_batch_unavailable_role_provider_calls_unchanged(tmp_path):
    """A mapping to an unavailable provider must leave the calls byte-for-byte
    unchanged — pinned by a router whose ``complete`` has NO provider/model
    parameters (extra kwargs would TypeError)."""
    src = tmp_path / "docs"
    _docs(src, a_txt="alpha body")
    router = _StrictRouter([_ext_reply("Alpha"), MD_REPLY])
    router.manager = _Providers([])  # "ghost" is unknown → fallback + flag
    cfg = _cfg(extract="ghost:cheap", synthesize="ghost:big")
    res = await run_batch(src, tmp_path / "out", router, output="docx", config=cfg)
    assert (res["processed"], res["failed"], res["synthesis_errors"]) == (1, [], [])


async def test_batch_dormant_with_empty_roles_and_without_config(tmp_path):
    src = tmp_path / "docs"
    _docs(src, a_txt="alpha body")
    for kwargs in ({"config": _cfg()}, {}):  # empty mapping / config absent
        router = _StrictRouter([_ext_reply("Alpha"), MD_REPLY])
        out = tmp_path / f"out-{len(kwargs)}"
        res = await run_batch(src, out, router, output="docx", **kwargs)
        assert (res["processed"], res["failed"]) == (1, [])


async def test_batch_mock_refusal_survives_role_routing(tmp_path):
    """CLAUDE.md honesty rule: even with a role applied, a route that lands on
    the offline mock still refuses to 'extract' — never fabricated facts."""
    src = tmp_path / "docs"
    _docs(src, a_txt="alpha body")
    router = _RoleRouter(
        [_ext_reply("Alpha"), MD_REPLY], provider="mock", available=["fleet"]
    )
    cfg = _cfg(extract="fleet:cheap")
    res = await run_batch(src, tmp_path / "out", router, output="docx", config=cfg)
    assert len(res["failed"]) == 1
    assert "mock" in res["failed"][0]["error"]


# ------------------------------------------------------------- (e) view_image --
def _png(path: Path) -> Path:
    from PIL import Image

    Image.new("RGBA", (4, 4), (255, 0, 0, 255)).save(path, format="PNG")
    return path


def _ctx(tmp_path, config=None) -> ToolContext:
    return ToolContext(
        workspace=tmp_path,
        session_id="s",
        agent_run_id="r",
        config=config,
        event_bus=None,
        engine=None,
    )


class _VisionRouter:
    def __init__(self, available=()):
        self.manager = _Providers(available)
        self.calls: list[tuple["str | None", "str | None"]] = []

    async def complete(
        self, *, system, messages, tools, session_id=None, provider=None, model=None
    ):
        self.calls.append((provider, model))
        return SimpleNamespace(
            response=SimpleNamespace(text="a red square"),
            provider=provider or "default-p",
            model=model or "default-m",
        )


class _StrictVisionRouter:
    manager = None

    def __init__(self):
        self.calls = 0

    async def complete(self, *, system, messages, tools, session_id=None):
        self.calls += 1
        return SimpleNamespace(
            response=SimpleNamespace(text="a red square"), provider="p", model="m"
        )


async def test_view_image_vision_role_resolved(tmp_path):
    _png(tmp_path / "dot.png")
    router = _VisionRouter(available=["viz"])
    tool = ViewImageTool(lambda: router)
    result = await tool.execute(
        {"path": "dot.png"}, _ctx(tmp_path, config=_cfg(vision="viz:eyes"))
    )
    assert result.ok and result.output == "a red square"
    assert router.calls == [("viz", "eyes")]
    assert result.data["provider"] == "viz"


async def test_view_image_dormant_and_unavailable_keep_call_shape(tmp_path):
    _png(tmp_path / "dot.png")
    # No config at all (the existing test harness shape) → strict router works.
    router = _StrictVisionRouter()
    result = await ViewImageTool(lambda: router).execute(
        {"path": "dot.png"}, _ctx(tmp_path, config=None)
    )
    assert result.ok and router.calls == 1
    # Mapped but unavailable → fallback, still no extra kwargs.
    router = _StrictVisionRouter()
    result = await ViewImageTool(lambda: router).execute(
        {"path": "dot.png"}, _ctx(tmp_path, config=_cfg(vision="ghost:eyes"))
    )
    assert result.ok and router.calls == 1


# ----------------------------------------------- (f) resolution result shape --
def test_role_resolution_dataclass_shape():
    res = RoleResolution(role="plan", provider="p", model="m", applied=True)
    assert (res.role, res.provider, res.model, res.applied, res.note) == (
        "plan", "p", "m", True, ""
    )
