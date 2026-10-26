"""Shared harness for the v1.395.0 model-team router tests (Phase 6).

A REAL platform (``build_platform``) with recording adapter doubles behind the
real provider manager, an Iron-Proxy stand-in that only answers the CACHED
snapshot (``cached_accounts`` — never a request), and a fleet-sampler stand-in
whose last pass says how busy a local box is. Nothing here reaches a real
service: the snapshot is data, the "models" are doubles.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

from iron_jarvis.iron_proxy import pin as P
from iron_jarvis.platform import build_platform
from iron_jarvis.providers import hold as HOLD
from iron_jarvis.providers.adapters.base import LLMAdapter, LLMResponse, ProviderError

LOCAL = ("ollama", "qwen3:8b")


def iso(seconds: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


class FakeSvc:
    """Iron-Proxy's CACHED snapshot only (``build_accounts.snapshot``)."""

    enabled = True

    def __init__(self) -> None:
        self.profiles: list[dict[str, Any]] = []
        self.states: dict[str, dict[str, Any]] = {}

    def add(self, pid: str, provider: str, title: str, *, percent: float | None = None,
            window_mins: int = 300, resets_in: float = 3600, status: str = "ready") -> None:
        self.profiles.append({"id": pid, "provider": provider, "title": title, "lane": "cli",
                              "order": len(self.profiles), "enabled": True})
        state: dict[str, Any] = {"status": status}
        if percent is not None:
            state["windows"] = [{"kind": "5h", "usedPercent": percent, "windowMins": window_mins,
                                 "resetsAt": iso(resets_in)}]
        self.states[pid] = state

    def set_percent(self, pid: str, percent: float, *, resets_in: float = 3600, window_mins: int = 300) -> None:
        self.states[pid]["windows"] = [{"kind": "5h", "usedPercent": percent, "windowMins": window_mins,
                                        "resetsAt": iso(resets_in)}]

    def park(self, pid: str, *, until_in: float = 1800) -> None:
        self.states[pid].update({"status": "parked", "parkedUntil": iso(until_in),
                                 "parkedReason": {"kind": "quota-exhausted"}})

    def cached_accounts(self):
        return (self.profiles, self.states)

    def running(self) -> bool:
        return True


class FakeSampler:
    """The fleet sampler's LAST PASS per node id (``latest``)."""

    def __init__(self) -> None:
        self.waiting: dict[str, float] = {}

    def latest(self, node_id: str):
        if node_id not in self.waiting:
            return None
        return SimpleNamespace(status="online", metrics=SimpleNamespace(requests_waiting=self.waiting[node_id]))


class Rec(LLMAdapter):
    """A recording model: who was called, under which pin and hold."""

    def __init__(self, provider: str, model: str | None, log: list, fail: set, answers: dict) -> None:
        self.provider = provider
        self.model = model or "default"
        self.log = log
        self.fail = fail
        self.answers = answers

    def capabilities(self):
        return {"tool_use": True, "vision": True}

    def _note(self, messages) -> None:
        hold = HOLD.current_hold()
        self.log.append({
            "provider": self.provider, "model": self.model, "pin": P.current(),
            "hold": (hold.provider, hold.model) if hold is not None else None,
            "task": next((str(m.content) for m in messages or () if getattr(m, "role", "") == "user"), ""),
        })
        if self.provider in self.fail:
            raise ProviderError(f"{self.provider}: overloaded", status_code=529, transient=True)

    def _answer(self, messages) -> str:
        return str(self.answers.get(self.provider) or f"{self.provider} did it.")

    async def complete(self, *, system, messages, tools, **kw):
        self._note(messages)
        return LLMResponse(text=self._answer(messages), usage={"input_tokens": 2, "output_tokens": 1})

    async def stream(self, *, system, messages, tools, **kw):
        self._note(messages)
        text = self._answer(messages)
        resp = LLMResponse(text=text, usage={"input_tokens": 2, "output_tokens": 1})
        yield {"type": "text", "text": text}
        yield {"type": "final", "response": resp}


PROVIDERS = ("ollama", "claude-cli", "codex-cli", "openai", "google")


class World(SimpleNamespace):
    pass


def make_world(tmp_path, monkeypatch, *, available: set[str] | None = None) -> World:
    p = build_platform(str(tmp_path))
    log: list[dict] = []
    fail: set[str] = set()
    answers: dict[str, str] = {}
    p.config.ollama_base_url = "http://127.0.0.1:11434"
    p.providers.configure_local(ollama_base_url="http://127.0.0.1:11434", ollama_model=LOCAL[1],
                                custom_base_url="", custom_model="")
    for name in PROVIDERS:
        p.providers.register(name, lambda model=None, n=name: Rec(n, model, log, fail, answers))
    up = set(PROVIDERS) if available is None else set(available)
    real = p.providers.available
    monkeypatch.setattr(p.providers, "available", lambda name: (name in up) if name in PROVIDERS else real(name))
    monkeypatch.setattr(p.providers, "inherited_from", lambda name: None)
    p.config.default_provider = "google"
    p.config.default_model = "gemini-3.8-flash"
    svc = FakeSvc()
    svc.add("c-work", "anthropic", "Work Max", percent=12)
    svc.add("c-spare", "anthropic", "Spare")
    svc.add("x-plus", "openai", "Codex Plus", percent=40)
    p.iron_proxy = svc
    sampler = FakeSampler()
    p.fleet_sampler = sampler
    return World(p=p, log=log, fail=fail, answers=answers, svc=svc, sampler=sampler, up=up)


def member(p, name: str, provider: str, model: str = "", roles=("code",), **kw):
    return p.agents_registry.register(name, f"You are {name}.", [], description=f"{name} works",
                                      provider=provider, model=model, roles=list(roles), **kw)


def note(p, name: str, role: str, outcomes, *, scope: str = "s-old", piece: str = "") -> None:
    """Write ``outcomes`` (oldest first) into ``name``'s record for ``role``:
    True = a piece that passed, False = one that failed its check."""
    from iron_jarvis.agents import team_record as TR

    for i, ok in enumerate(outcomes):
        sid = f"{name}-{role}-{i}-{scope}"
        TR.note_run(p.engine, member=name, role=role, session_id=sid, scope=scope, piece=piece,
                    finished=True)
        if not ok:
            TR.credit_check(p.engine, [sid], TR.CHECK_FAILED)
