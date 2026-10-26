"""TONIGHT's team, scripted (the v1.396.0 review of Phase 7: MEASURED).

The doer's world (``bench_team_v1396``) runs its local member as a FLEET node
(``fleet-spark``). The live run's team is not that one:

* planner ``claude-cli`` / ``sonnet``;
* ``glm`` on the CUSTOM endpoint (``custom`` / ``glm-5.3-flash``, roles read
  code test) — a LiteLLM-style gateway that wants a key;
* ``codex`` on ``codex-cli`` / ``gpt-6-astra`` (roles code review).

This world is that team. The gateway is a REAL HTTP server on a loopback port
nothing else uses (never 8787 / 8788 / 8765): it lists its models at
``/v1/models`` and answers 401 without the key, so the checks before a night
are proven against a server, not a stub. The models themselves are the doer's
adapter doubles (no CLI, no network): the doubles never touch the gateway.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from tests._fakes import bench_team_v1396 as W

PLANNER = ("claude-cli", "sonnet")
CODEX = ("codex-cli", "gpt-6-astra")
LOCAL = ("custom", "glm-5.3-flash")
#: The gateway's key, built from pieces (never a real key's shape).
KEY = "-".join(("scratch", "gateway", "pass"))
LISTED = ("glm-5.3-flash", "qwen3-coder-next", "embed-small")


def team_toml(*, local_model: str = LOCAL[1], extra_glm: str = "", extra_codex: str = "",
              planner_model: str = PLANNER[1], codex_provider: str = CODEX[0]) -> str:
    return (
        "[planner]\n"
        f'provider = "{PLANNER[0]}"\n'
        f'model = "{planner_model}"\n\n'
        "[[members]]\n"
        'name = "glm"\n'
        f'provider = "{LOCAL[0]}"\n'
        f'model = "{local_model}"\n'
        'roles = ["read", "code", "test"]\n'
        f"{extra_glm}\n"
        "[[members]]\n"
        'name = "codex"\n'
        f'provider = "{codex_provider}"\n'
        f'model = "{CODEX[1]}"\n'
        'roles = ["code", "review"]\n'
        f"{extra_codex}"
    )


class Gateway:
    """A loopback model gateway: ``GET /v1/models`` lists :data:`LISTED`
    (401 without the key when ``key`` is set); every request is recorded
    (path, whether the key came — never the value)."""

    def __init__(self, *, key: str = KEY, models: tuple[str, ...] = LISTED) -> None:
        self.key = key
        self.models = models
        self.seen: list[tuple[str, bool]] = []
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 — the stdlib's name
                auth = self.headers.get("Authorization", "")
                had = bool(gateway.key) and auth == f"Bearer {gateway.key}"
                gateway.seen.append((self.path, had))
                if gateway.key and not had:
                    self.send_response(401)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"error": "unauthorized"}')
                    return
                if self.path.rstrip("/") in ("/v1/models", "/models"):
                    body = json.dumps({"data": [{"id": m} for m in gateway.models]}).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_response(404)
                self.end_headers()

            def log_message(self, *a: Any) -> None:  # quiet
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        assert self.port not in (8787, 8788, 8765)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class TonightDouble(W.Double):
    """The doer's double, under tonight's names (``custom`` is the local
    member; its address is the configured gateway's, read by egress)."""

    def __init__(self, provider: str, model: str | None, world: W.World) -> None:
        self.provider = provider
        self.model = model or {PLANNER[0]: PLANNER[1], CODEX[0]: CODEX[1], LOCAL[0]: LOCAL[1]}[provider]
        self.world = world
        self.raise_for = getattr(world, "raise_for", {})

    async def stream(self, *, system, messages, tools, **kw):
        self.world.kwargs.append({"provider": self.provider, **{k: v for k, v in kw.items() if k == "reasoning"}})
        failure = self.world.raise_for.get(self.provider)
        if failure is not None:
            raise failure
        async for frame in super().stream(system=system, messages=messages, tools=tools, **kw):
            yield frame


def point_custom(platform: Any, url: str, model: str = LOCAL[1]) -> None:
    """The benchmark home's custom endpoint (address + model), live."""
    platform.config.custom_base_url = url
    platform.config.custom_model = model
    platform.providers.configure_local(
        ollama_base_url=getattr(platform.config, "ollama_base_url", None),
        custom_base_url=url, custom_model=model,
    )


def wire(platform: Any, monkeypatch: Any, *, gateway: Gateway | None, save_key: bool = True,
         epoch: float = 1_900_000_000.0) -> W.World:
    """Tonight's doubles on ``platform``'s real manager, the custom endpoint
    pointed at ``gateway`` (its key saved in the home's vault when
    ``save_key``), a reading clock the test controls."""
    from iron_jarvis.harness_bench import runners as R
    from iron_jarvis.providers import router as router_mod

    world = W.World(calls=[], kwargs=[], alone_helper=False, raise_for={},
                    claude=W.Windows(start=20.0, step=0.5, resets=(int(epoch) + 4 * 3600, int(epoch) + 5 * 86400)),
                    codex=W.Windows(start=40.0, step=1.0, resets=(int(epoch) + 3 * 3600, int(epoch) + 6 * 86400)))
    if gateway is not None:
        point_custom(platform, gateway.url)
        if save_key:
            platform.secrets.set("custom_api_key", gateway.key)
    mgr = platform.providers
    for name in (PLANNER[0], CODEX[0], LOCAL[0]):
        mgr.register(name, lambda model=None, n=name: TonightDouble(n, model, world))
    real = mgr.available
    names = {PLANNER[0], CODEX[0]}
    monkeypatch.setattr(mgr, "available", lambda name: True if name in names else real(name))
    monkeypatch.setattr(mgr, "inherited_from", lambda name: None)
    monkeypatch.setattr(router_mod.random, "uniform", lambda a, b: 0.0)
    clock = {"t": float(epoch)}

    def tick() -> float:
        clock["t"] += 1.0
        return clock["t"]

    monkeypatch.setattr(R, "READING_CLOCK", tick, raising=False)
    world.clock = clock
    return world


def write_team(tmp_path: Path, **kw: Any) -> Path:
    path = tmp_path / "team.toml"
    path.write_text(team_toml(**kw), encoding="utf-8")
    return path
