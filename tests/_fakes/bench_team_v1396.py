"""A scripted TEAM for the v1.396.0 benchmark pins (the long-jobs plan's
Phase 7: MEASURED).

A REAL platform (``build_platform``) whose provider manager holds three
adapter doubles behind the real router — nothing reaches a real CLI, a real
service or the network:

* ``claude-cli`` — the PLANNER: it plays a script per (task, setup), where the
  setup is read off its own system prompt (a planner that sees "# Your model
  team" hands pieces out by ROLE through Claude Code's ``Agent`` tool; one
  that does not works alone), and answers every call with the vendor's usage
  headers in ``extra["ratelimit_headers"]`` — the exact shape the real
  claude-cli adapter carries (``claude_native/transport.assemble``);
* ``codex-cli`` — a REVIEWER member, answering with Codex's account snapshot
  in ``extra["rate_limits"]`` (``codex_native/adapter``'s shape);
* ``fleet-spark`` — a LOCAL member on the user's network (its endpoint is a
  loopback address nothing listens on; the router never probes a double: it
  has no ``_client``), writing code with Iron Jarvis's own tool names.

The usage windows move the way they move in life: each answer reports the
account's percent AFTER the call, a little higher every time, inside one
window period — until a test tells a window to reset.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from iron_jarvis.providers.adapters.base import LLMAdapter, LLMResponse, ToolCall

REPO = Path(__file__).resolve().parents[2]
SOLUTIONS = REPO / "tests" / "fixtures" / "harness_bench_solutions"

PLANNER = ("claude-cli", "claude-opus-4-8")
CODEX = ("codex-cli", "gpt-6-astra")
LOCAL = ("fleet-spark", "glm-5.3-flash")
LOCAL_ENDPOINT = "http://127.0.0.1:4111"
PROVIDERS = (PLANNER[0], CODEX[0], LOCAL[0])

#: The two frontier tasks the proof runs (their reference solutions are files).
TASK_A = "frontier-monthly-recurrence"
TASK_B = "frontier-release-ready"
#: Words that tell the two tasks apart in a prompt.
_TASK_WORDS = {TASK_A: "monthly event starting on January 31", TASK_B: "category totals are wrong"}


def solution(task_id: str, rel: str) -> str:
    return (SOLUTIONS / task_id / (rel + ".sol")).read_text(encoding="utf-8")


def team_toml(*, local_model: str = LOCAL[1], codex_roles: str = '["review"]') -> str:
    return (
        "[planner]\n"
        f'provider = "{PLANNER[0]}"\n'
        f'model = "{PLANNER[1]}"\n\n'
        "[[members]]\n"
        'name = "glm"\n'
        f'provider = "{LOCAL[0]}"\n'
        f'model = "{local_model}"\n'
        'roles = ["read", "code"]\n\n'
        "[[members]]\n"
        'name = "codex"\n'
        f'provider = "{CODEX[0]}"\n'
        f'model = "{CODEX[1]}"\n'
        f"roles = {codex_roles}\n"
    )


def _first_user(messages: Any) -> str:
    for m in messages or ():
        if getattr(m, "role", "") == "user":
            return str(getattr(m, "content", "") or "")
    return ""


def _answered(messages: Any) -> int:
    return sum(1 for m in messages or () if getattr(m, "role", "") == "assistant")


def _task_of(text: str) -> str:
    return next((t for t, words in _TASK_WORDS.items() if words in text), "")


def agent(cid: str, role: str, prompt: str, *, kind: str = "") -> list[ToolCall]:
    """Claude Code's own helper tool, asking for a ROLE (``role:<id>``) — or,
    with ``kind``, for a helper of that type on the session's own model."""
    return [ToolCall(id=cid, name="Agent", arguments={
        "description": f"{role} piece", "prompt": prompt, "subagent_type": kind or f"role:{role}"})]


def write(cid: str, path: str, content: str) -> list[ToolCall]:
    """Claude Code's own ``Write``."""
    return [ToolCall(id=cid, name="Write", arguments={"file_path": path, "content": content})]


#: The planner's scripts: (setup, task) -> its moves, one per call that
#: offers tools; a call past the script (the finish check's round, a nudge)
#: answers in words. TEAM passes A (the local member writes the fix) and fails
#: B (the member changes nothing); ALONE fails A (it changes nothing) and
#: passes B — so the report must count a task each one passes that the other
#: fails.
def planner_script(setup: str, task: str) -> list[Any]:
    if setup == "team" and task == TASK_A:
        return [agent("p-a1", "code", "Fix recur/rules.py so a monthly series never drifts or skips a month."),
                agent("p-a2", "review", "Review the change to recur/rules.py."),
                "The team fixed recur/rules.py and the reviewer approved it."]
    if setup == "team" and task == TASK_B:
        return [agent("p-b1", "code", "Fix summarize() in invoicing/report.py for quoted categories."),
                agent("p-b2", "review", "Review the change to invoicing/report.py."),
                "The team looked at invoicing/report.py."]
    if setup == "alone" and task == TASK_A:
        return ["I could not find what makes the series skip February."]
    if setup == "alone-helper" and task == TASK_A:
        # One model alone that still starts a helper — on its OWN model
        # (Claude Code's general-purpose agent): the run's numbers must hold
        # the helper's share too.
        return [agent("p-h1", "code", "Look at recur/rules.py and say what you see.", kind="general-purpose"),
                "A helper looked; I could not find what makes the series skip February."]
    if setup == "alone" and task == TASK_B:
        return [write("w-b1", "invoicing/report.py", solution(TASK_B, "invoicing/report.py")),
                write("w-b2", "CHANGELOG.md", solution(TASK_B, "CHANGELOG.md")),
                "Fixed summarize() and added the changelog entry."]
    return ["Done."]


class Windows:
    """One account's usage windows: each answer reports them AFTER the call,
    ``step`` points higher; :meth:`reset` starts a new window period."""

    def __init__(self, *, start: float, step: float, resets: tuple[int, int]) -> None:
        self.percent = [start, start / 4]
        self.step = step
        self.resets = list(resets)
        self.silent = False

    def tick(self) -> tuple[float, float]:
        self.percent = [self.percent[0] + self.step, self.percent[1] + self.step / 10]
        return self.percent[0], self.percent[1]

    def reset(self, *, to: float = 1.0, later: int = 5 * 3600) -> None:
        self.percent[0] = to
        self.resets[0] += later


class World(SimpleNamespace):
    pass


class Double(LLMAdapter):
    def __init__(self, provider: str, model: str | None, world: World) -> None:
        self.provider = provider
        self.model = model or {PLANNER[0]: PLANNER[1], CODEX[0]: CODEX[1], LOCAL[0]: LOCAL[1]}[provider]
        self.world = world
        if provider == LOCAL[0]:
            # On the user's own network (egress reads the endpoint); the
            # router's liveness probe needs a ``_client``, which a double lacks.
            self._endpoint = LOCAL_ENDPOINT

    def capabilities(self):
        return {"tool_use": True, "vision": True}

    async def complete(self, *, system, messages, tools, **kw):
        final = None
        async for frame in self.stream(system=system, messages=messages, tools=tools, **kw):
            if frame["type"] == "final":
                final = frame["response"]
        return final

    async def stream(self, *, system, messages, tools, **kw):
        w = self.world
        first = _first_user(messages)
        w.calls.append({"provider": self.provider, "tools": bool(tools)})
        extra: dict[str, Any] = {}
        if self.provider == PLANNER[0]:
            setup = "team" if "# Your model team" in (system or "") else (
                "alone-helper" if w.alone_helper else "alone")
            # Stateless: the step is how many times it has answered in THIS
            # conversation (every repeat plays its script from the top).
            step = _answered(messages)
            script = planner_script(setup, _task_of(first))
            move = script[step] if (step < len(script) and tools) else "Done."
            usage = {"input_tokens": 400 if setup == "team" else 900, "output_tokens": 50,
                     "cost_usd": 0.005 if setup == "team" else 0.0125}
            if not w.claude.silent:
                five, week = w.claude.tick()
                extra["ratelimit_headers"] = {
                    "anthropic-ratelimit-unified-5h-utilization": f"{five / 100:.4f}",
                    "anthropic-ratelimit-unified-5h-reset": str(w.claude.resets[0]),
                    "anthropic-ratelimit-unified-7d-utilization": f"{week / 100:.4f}",
                    "anthropic-ratelimit-unified-7d-reset": str(w.claude.resets[1]),
                    "anthropic-ratelimit-unified-status": "allowed",
                }
        elif self.provider == CODEX[0]:
            move = "I read the change.\nVERDICT: approved"
            usage = {"input_tokens": 300, "output_tokens": 30, "cost_usd": 0.002}
            if not w.codex.silent:
                primary, secondary = w.codex.tick()
                extra["rate_limits"] = {
                    "planType": "plus",
                    "primary": {"usedPercent": primary, "windowDurationMins": 300, "resetsAt": w.codex.resets[0]},
                    "secondary": {"usedPercent": secondary, "windowDurationMins": 10080,
                                  "resetsAt": w.codex.resets[1]},
                }
        else:
            step = _answered(messages)
            if "recur/rules.py" in first and step == 0 and tools:
                move = [ToolCall(id="l-a1", name="write_file", arguments={
                    "path": "recur/rules.py", "content": solution(TASK_A, "recur/rules.py")})]
            elif "recur/rules.py" in first:
                move = "Rewrote how a month is added: the series keeps its day or the month's last day."
            else:
                move = "I read invoicing/report.py; it looks right as it is."
            usage = {"input_tokens": 700, "output_tokens": 80}
        resp = (LLMResponse(text=move, usage=usage, extra=extra) if isinstance(move, str)
                else LLMResponse(text="", tool_calls=list(move), finish_reason="tool_use", usage=usage,
                                 extra=extra))
        if resp.text:
            yield {"type": "text", "text": resp.text}
        yield {"type": "final", "response": resp}


def wire(platform: Any, monkeypatch: Any, *, epoch: float = 1_900_000_000.0) -> World:
    """The doubles on ``platform``'s real provider manager; every provider
    there; a reading clock the test controls (one second per answer)."""
    from iron_jarvis.harness_bench import runners as R
    from iron_jarvis.providers import router as router_mod

    world = World(calls=[], alone_helper=False,
                  claude=Windows(start=20.0, step=0.5, resets=(int(epoch) + 4 * 3600, int(epoch) + 5 * 86400)),
                  codex=Windows(start=40.0, step=1.0, resets=(int(epoch) + 3 * 3600, int(epoch) + 6 * 86400)))
    mgr = platform.providers
    for name in PROVIDERS:
        mgr.register(name, lambda model=None, n=name: Double(n, model, world))
    real = mgr.available
    monkeypatch.setattr(mgr, "available", lambda name: True if name in PROVIDERS else real(name))
    monkeypatch.setattr(mgr, "inherited_from", lambda name: None)
    monkeypatch.setattr(router_mod.random, "uniform", lambda a, b: 0.0)
    clock = {"t": float(epoch)}

    def tick() -> float:
        clock["t"] += 1.0
        return clock["t"]

    # (raising=False: the clock is v1.396.0's — a pin run against older code
    # still wires the doubles.)
    monkeypatch.setattr(R, "READING_CLOCK", tick, raising=False)
    world.clock = clock
    return world
