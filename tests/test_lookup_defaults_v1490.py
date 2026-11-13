"""The three Feature lookup settings are ON by default (v1.490.0).

The owner, 2026-11-13, after the measurement of v1.489.0 (111 practice
requests, right tool used: 74% -> 97% on Claude Haiku): "Turn them on".

What is pinned here:

* a fresh install (nothing saved) has all three on, in a FRESH interpreter
  that never loaded this suite's conftest;
* a value the user saved, on or off, still wins;
* the suite itself runs with them off (the conftest seam), so the thousands
  of scripted chat turns are unchanged;
* with them on, the demo model (what a fresh install answers with) is never
  asked the routing question, and a model whose known window cannot hold the
  list is not asked either.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from types import SimpleNamespace

import pytest

from iron_jarvis.capability_search import route as RT
from iron_jarvis.core import config as C
from iron_jarvis.core.config import Config, load_config, persist_config_values
from iron_jarvis.settings import schema

KEYS = ("capability_search", "capability_look_first", "capability_route")


def test_a_fresh_install_has_all_three_on(tmp_path):
    """In a fresh interpreter: the product's own defaults, not the suite's."""
    code = textwrap.dedent(
        """
        import pathlib, sys
        from iron_jarvis.core.config import Config, load_config
        from iron_jarvis.settings import schema
        root = pathlib.Path(sys.argv[1])
        made = Config(project_root=root, home=root)
        loaded = load_config(root)
        keys = ("capability_search", "capability_look_first", "capability_route")
        print([getattr(made, k) for k in keys], [getattr(loaded, k) for k in keys],
              [schema.config_default(schema.BY_KEY[k]) for k in keys])
        """
    )
    out = subprocess.run([sys.executable, "-c", code, str(tmp_path)], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-800:]
    assert out.stdout.strip() == "[True, True, True] [True, True, True] [True, True, True]", out.stdout


def test_the_suite_itself_runs_with_them_off(tmp_path):
    """The conftest seam: a bare test's Config has all three off."""
    assert C.LOOKUP_ON_BY_DEFAULT is False, "tests/conftest.py no longer turns the lookup defaults off"
    made = Config(project_root=tmp_path, home=tmp_path)
    assert [getattr(made, k) for k in KEYS] == [False, False, False]


def test_all_three_follow_the_one_switch(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "LOOKUP_ON_BY_DEFAULT", True)
    made = Config(project_root=tmp_path, home=tmp_path)
    assert [getattr(made, k) for k in KEYS] == [True, True, True]
    assert [schema.config_default(schema.BY_KEY[k]) for k in KEYS] == [True, True, True]
    monkeypatch.setattr(C, "LOOKUP_ON_BY_DEFAULT", False)
    made = Config(project_root=tmp_path, home=tmp_path)
    assert [getattr(made, k) for k in KEYS] == [False, False, False]


@pytest.mark.parametrize("key", KEYS)
def test_a_saved_off_stays_off(tmp_path, monkeypatch, key):
    """Only changed settings are saved, so a saved value is the user's own
    choice: the new default never turns back on what they turned off."""
    monkeypatch.setattr(C, "LOOKUP_ON_BY_DEFAULT", True)
    home = load_config(tmp_path).home
    persist_config_values(home, {key: False})
    loaded = load_config(tmp_path)
    assert getattr(loaded, key) is False
    for other in KEYS:
        if other != key:
            assert getattr(loaded, other) is True, other


def test_a_saved_on_stays_on_when_the_default_is_off(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "LOOKUP_ON_BY_DEFAULT", False)
    home = load_config(tmp_path).home
    persist_config_values(home, {"capability_search": True})
    loaded = load_config(tmp_path)
    assert loaded.capability_search is True and loaded.capability_route is False


# --------------------------------------------------------------------------- #
# on by default must cost a fresh install nothing
# --------------------------------------------------------------------------- #
def _d(default_provider: str):
    config = SimpleNamespace(capability_search=True, capability_route=True, default_provider=default_provider)
    return SimpleNamespace(platform=SimpleNamespace(config=config))


def _body(provider: str = "", text: str = "please set up a weekly reminder for me"):
    return SimpleNamespace(provider=provider, auto_tools=True, turn_id="t1",
                           messages=[SimpleNamespace(role="user", content=text)])


def test_the_demo_model_is_never_asked_the_routing_question():
    """A fresh install answers with the demo model: with the settings on by
    default it would have been called once more on every message."""
    assert RT.wanted(_d("mock"), _body()) is False, "the default is the demo model"
    assert RT.wanted(_d("claude-cli"), _body("mock")) is False, "the turn picked the demo model"
    assert RT.wanted(_d("mock"), _body("MOCK ")) is False
    # ...and a real model still is (the guard is about the demo only).
    assert RT.wanted(_d("claude-cli"), _body()) is True
    assert RT.wanted(_d("mock"), _body("claude-cli")) is True, "a real pick over the demo default"
    assert RT.wanted(_d("auto"), _body()) is True


def test_a_model_too_small_for_the_list_is_not_asked():
    text = "word " * 4000  # about 5,000 tokens of list
    assert RT.fits_window(text, None) is True, "an unknown window asks as before"
    assert RT.fits_window(text, 0) is True
    assert RT.fits_window(text, 200_000) is True
    assert RT.fits_window(text, 4096) is False
    assert RT.fits_window(text, "junk") is True, "never raises"


@pytest.mark.asyncio
async def test_no_call_is_made_when_the_list_does_not_fit(monkeypatch):
    calls = []

    class Router:
        async def complete(self, **kw):  # noqa: ANN003
            calls.append(kw)
            raise AssertionError("the routing call was made on a model too small for the list")

    entry = SimpleNamespace(id="schedule_create")
    monkeypatch.setattr(RT, "catalog", lambda platform: ("schedule_create: make a schedule\n" * 600, {"schedule_create": entry}))
    d = SimpleNamespace(platform=SimpleNamespace(router=Router(), config=None))
    assert await RT.route_request(d, _body("custom"), "custom", "tiny", window=4096) is None
    assert calls == []
