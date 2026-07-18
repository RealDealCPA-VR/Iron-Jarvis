"""OpenCode as a provider — restricted to models the user's own hardware serves.

OpenCode can reach three different things through one command: models on your
machines, models on its hosted free tier, and PAID remote models reached
*through* one of your own proxies. The user asked for local only, so the third
case is the dangerous one: a LiteLLM alias on a Tailscale IP looks local right
up until it forwards to OpenRouter and bills them.

Every test here is offline — the CLI runner, the config, and the proxy probe
are all injected.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from iron_jarvis.providers import opencode as oc
from iron_jarvis.providers.adapters.base import LLMMessage
from iron_jarvis.providers.adapters.opencode_cli import (
    OpencodeCliAdapter,
    parse_events,
)

# The user's real topology, trimmed: one local proxy whose "frontier" alias is
# a paid passthrough, plus OpenCode's own hosted models.
_MODELS_STDOUT = """opencode/big-pickle
opencode/deepseek-v4-flash-free
spark/fleet
spark/frontier
"""

_MODEL_INFO = {
    "data": [
        {
            "model_name": "fleet",
            "litellm_params": {"api_base": "http://spark-049d:8888/v1"},
        },
        {"model_name": "frontier", "litellm_params": {"model": "openrouter/auto"}},
    ]
}


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _runner(_argv, **_kw):
    return 0, _MODELS_STDOUT, ""


def _http_get(_url):
    return _Resp(_MODEL_INFO)


def _which(_name):
    return "/usr/bin/opencode"


# --- locality ------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("http://100.66.161.52:4000/v1", True),  # Tailscale CGNAT
        ("http://100.87.42.62:8003", True),
        ("http://192.168.1.5:8000", True),
        ("http://10.0.0.4:8000", True),
        ("http://127.0.0.1:11434", True),
        ("http://localhost:1234/v1", True),
        ("http://spark-049d:8888/v1", True),  # single-label LAN/MagicDNS name
        ("http://tower.local:8003", True),
        ("https://openrouter.ai/api/v1", False),
        ("https://api.openai.com/v1", False),
        ("https://opencode.ai/zen", False),
        ("", False),
        ("not a url", False),
    ],
)
def test_is_local_url(url, expected):
    assert oc.is_local_url(url) is expected


def test_tailscale_range_is_not_caught_by_is_private():
    """Guards the subtle bug: 100.64.0.0/10 reports is_private == False, so a
    naive check would classify the whole fleet as remote."""
    import ipaddress

    assert ipaddress.ip_address("100.66.161.52").is_private is False
    assert oc.is_local_url("http://100.66.161.52:4000/v1") is True


# --- config parsing -------------------------------------------------------------


def test_config_providers_reads_jsonc_with_comments(tmp_path):
    path = tmp_path / "opencode.jsonc"
    path.write_text(
        """{
  // a line comment
  "$schema": "https://opencode.ai/config.json",
  /* block
     comment */
  "provider": {
    "spark": {"options": {"baseURL": "http://100.66.161.52:4000/v1"}},
    "cloudy": {"options": {"baseURL": "https://api.example.com/v1"}}
  }
}""",
        encoding="utf-8",
    )
    assert oc.config_providers([path]) == {
        "spark": "http://100.66.161.52:4000/v1",
        "cloudy": "https://api.example.com/v1",
    }


def test_broken_config_is_ignored_not_fatal(tmp_path):
    bad = tmp_path / "opencode.json"
    bad.write_text("{ this is not json", encoding="utf-8")
    assert oc.config_providers([bad]) == {}


# --- the load-bearing selection -------------------------------------------------


def test_only_genuinely_local_models_are_offered():
    """Hosted models are excluded, and so is a PAID passthrough alias sitting
    behind a local-looking proxy."""
    got = oc.local_models(
        runner=_runner,
        which=_which,
        providers={"spark": "http://100.66.161.52:4000/v1"},
        http_get=_http_get,
    )
    assert got == ["spark/fleet"]
    assert "spark/frontier" not in got  # the money guard
    assert not any(m.startswith("opencode/") for m in got)


def test_provider_on_a_public_url_contributes_nothing():
    assert (
        oc.local_models(
            runner=_runner,
            which=_which,
            providers={"spark": "https://api.example.com/v1"},
            http_get=_http_get,
        )
        == []
    )


def test_unreadable_proxy_does_not_invent_locality():
    """If the proxy won't say where an alias goes, we learn nothing from it —
    provider-level locality still applies, which is why the explicit allowlist
    exists as the user's override."""

    def _boom(_url):
        raise RuntimeError("connection refused")

    got = oc.local_models(
        runner=_runner,
        which=_which,
        providers={"spark": "http://100.66.161.52:4000/v1"},
        http_get=_boom,
    )
    assert "spark/fleet" in got


def test_explicit_allowlist_overrides_detection():
    class _Cfg:
        opencode_local_models = "spark/fleet, spark/other"

    assert oc.allowed_models(_Cfg()) == ["spark/fleet", "spark/other"]


def test_empty_allowlist_falls_back_to_detection():
    class _Cfg:
        opencode_local_models = ""

    got = oc.allowed_models(
        _Cfg(),
        runner=_runner,
        which=_which,
        providers={"spark": "http://100.66.161.52:4000/v1"},
        http_get=_http_get,
    )
    assert got == ["spark/fleet"]


# --- the adapter refuses before it spawns ---------------------------------------


def _adapter(model="", allowed=("spark/fleet",), runner=None):
    return OpencodeCliAdapter(
        model=model,
        allowed=lambda: list(allowed),
        runner=runner or (lambda *a, **k: (0, "", "")),
        which=_which,
    )


def _complete(adapter):
    return asyncio.run(
        adapter.complete(
            system="", messages=[LLMMessage(role="user", content="hi")], tools=[]
        )
    )


def test_a_paid_passthrough_model_is_refused_without_running_anything():
    spawned = []

    def _spy(argv, **_kw):
        spawned.append(argv)
        return 0, "", ""

    with pytest.raises(RuntimeError, match="not one of your local models"):
        _complete(_adapter(model="spark/frontier", runner=_spy))
    assert spawned == []  # the refusal must precede the subprocess, or it billed


def test_a_hosted_model_is_refused():
    with pytest.raises(RuntimeError, match="not one of your local models"):
        _complete(_adapter(model="opencode/big-pickle"))


def test_no_local_models_is_an_honest_error_not_a_silent_remote_call():
    with pytest.raises(RuntimeError, match="no LOCAL models"):
        _complete(_adapter(model="", allowed=()))


def test_blank_model_uses_the_first_LOCAL_model_not_opencodes_default():
    """OpenCode's own default could be a paid model; ours never is."""
    seen = {}

    def _run(argv, **_kw):
        seen["argv"] = argv
        return 0, json.dumps(
            {"type": "text", "part": {"type": "text", "text": "ok"}}
        ), ""

    _complete(_adapter(model="", allowed=("spark/fleet",), runner=_run))
    assert "-m" in seen["argv"]
    assert seen["argv"][seen["argv"].index("-m") + 1] == "spark/fleet"


# --- event parsing --------------------------------------------------------------


def test_parse_events_joins_text_and_totals_tokens():
    stdout = "\n".join(
        [
            json.dumps({"type": "step_start", "part": {"type": "step-start"}}),
            json.dumps({"type": "text", "part": {"type": "text", "text": "PO"}}),
            json.dumps({"type": "text", "part": {"type": "text", "text": "NG"}}),
            json.dumps(
                {
                    "type": "step_finish",
                    "part": {"type": "step-finish", "tokens": {"input": 12, "output": 3}},
                }
            ),
        ]
    )
    text, usage = parse_events(stdout)
    assert text == "PONG"
    assert usage == {"input_tokens": 12, "output_tokens": 3}


def test_parse_events_survives_noise_and_reports_nothing_rather_than_garbage():
    text, usage = parse_events("boot banner\nnot json\n\n")
    assert text == ""
    assert usage == {"input_tokens": 0, "output_tokens": 0}


def test_empty_output_is_an_error_not_an_empty_success():
    with pytest.raises(RuntimeError, match="no output"):
        _complete(_adapter(model="spark/fleet", runner=lambda *a, **k: (0, "", "")))


def test_nonzero_exit_surfaces_the_cli_error():
    with pytest.raises(RuntimeError, match="exited 2"):
        _complete(
            _adapter(model="spark/fleet", runner=lambda *a, **k: (2, "", "boom")),
        )


def test_capabilities_declare_no_tool_use():
    """OpenCode runs its own tool loop and returns final text; claiming tool_use
    would let the router hand it agent work that then stalls on empty calls."""
    caps = _adapter().capabilities()
    assert caps["tool_use"] is False and caps["vision"] is False
