"""``config_list`` names the models that can be chosen on the default-model
line (v1.484.1).

Seen live: "switch my default to the fast model" made the model read
``config_list``, which showed only the current value, so it asked the user
which model they meant. The line now names what the pickers offer, from the
model catalog's kept answer only: no endpoint is asked and nothing is built.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.settings import tools as ST
from iron_jarvis.tools.base import ToolContext

pytestmark = pytest.mark.usefixtures("no_network")

ROWS = [
    {"provider": "claude-cli", "model": "subscription", "label": "Your Claude plan", "available": True},
    {"provider": "anthropic", "model": "claude-haiku-4-5", "label": "Haiku 4.5", "available": True},
    {"provider": "openai", "model": "gpt-6", "label": "gpt-6", "available": True},
    {"provider": "google", "model": "gemini-3.8-flash", "label": "Gemini Flash", "available": False},
    {"provider": "mock", "model": "demo", "label": "Demo", "available": True},
    {"provider": "anthropic", "model": "claude-haiku-4-5", "label": "Haiku 4.5", "available": True},
]


@pytest.fixture()
def platform(tmp_path):
    c = TestClient(create_app(str(tmp_path / "home")))
    p = c.app.state.platform
    yield p, ToolContext(workspace=tmp_path, session_id="chat", agent_run_id="chat", config=p.config,
                         event_bus=p.event_bus, engine=p.engine)
    try:
        p.scheduler.shutdown()
    except Exception:  # noqa: BLE001
        pass


def _list(p, ctx, query="default model"):
    return asyncio.run(ST.ConfigListTool(p).execute({"query": query}, ctx)).output


def _line(out):
    return next(line for line in out.splitlines() if "(default_model)" in line)


def test_the_default_model_line_names_the_models_that_can_be_chosen(platform):
    p, ctx = platform
    p._model_catalog = SimpleNamespace(_entry=SimpleNamespace(rows=ROWS))
    line = _line(_list(p, ctx))
    assert "Models that can be chosen now" in line
    assert "claude-cli subscription (Your Claude plan)" in line
    assert "anthropic claude-haiku-4-5 (Haiku 4.5)" in line and line.count("claude-haiku-4-5 (") == 1
    assert "openai gpt-6" in line and "(gpt-6)" not in line
    assert "gemini" not in line and "mock" not in line


def test_only_the_default_model_line_carries_them(platform):
    p, ctx = platform
    p._model_catalog = SimpleNamespace(_entry=SimpleNamespace(rows=ROWS))
    out = _list(p, ctx, "theme")
    assert "Models that can be chosen" not in out


def test_no_kept_catalog_leaves_the_line_as_before(platform, monkeypatch):
    p, ctx = platform
    p._model_catalog = None
    built = []
    import iron_jarvis.daemon.routes.connections as C

    monkeypatch.setattr(C, "selectable_models", lambda d: built.append(1) or ROWS)
    line = _line(_list(p, ctx))
    assert "Models that can be chosen" not in line and built == []


def test_a_long_list_is_cut_and_says_how_many_more(platform):
    p, ctx = platform
    many = [{"provider": "custom", "model": f"m{i}", "label": "", "available": True} for i in range(40)]
    p._model_catalog = SimpleNamespace(_entry=SimpleNamespace(rows=many))
    line = _line(_list(p, ctx))
    assert f"custom m{ST.MODEL_CHOICES_MAX - 1};" in line and f"custom m{ST.MODEL_CHOICES_MAX};" not in line
    assert f"and {40 - ST.MODEL_CHOICES_MAX} more" in line
