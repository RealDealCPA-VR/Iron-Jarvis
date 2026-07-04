"""Pixio generative-media tools — scripted-HTTP transport, fully offline."""

from __future__ import annotations

from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.pixio import (
    _BASE_URL,
    PixioGenerateTool,
    PixioModelsTool,
    PixioParamsTool,
    PixioStatusTool,
    pixio_tools,
)

_KEY = "pxio_live_test"


def _ctx(tmp_path):
    return ToolContext(
        workspace=tmp_path,
        session_id="s",
        agent_run_id="r",
        config=None,
        event_bus=None,
        engine=None,
    )


class FakeResponse:
    def __init__(self, status_code=200, json_body=None, content=b""):
        self.status_code = status_code
        self._json = json_body
        self.content = content

    def json(self):
        if self._json is None:
            raise ValueError("no json body")
        return self._json


class ScriptedHttp:
    """Injected transport: routes ``(method, path)`` to scripted responses.

    A list value is consumed in order (last response repeats) — that is how the
    pending→succeeded poll sequence is scripted. Every call is recorded so tests
    can assert on auth headers, request bodies, and call counts."""

    def __init__(self, script):
        self._script = {k: list(v) if isinstance(v, list) else [v] for k, v in script.items()}
        self.calls: list[tuple[str, str, dict, dict | None]] = []

    def __call__(self, method, url, headers, json_body):
        self.calls.append((method, url, headers, json_body))
        path = url[len(_BASE_URL):] if url.startswith(_BASE_URL) else url
        queue = self._script[(method, path)]
        return queue.pop(0) if len(queue) > 1 else queue[0]


def _never_called(method, url, headers, json_body):  # pragma: no cover - must not run
    raise AssertionError("HTTP must not be touched without an API key")


# --- happy path: generate -> poll -> output saved into the workspace -----------


async def test_generate_polls_and_saves_output_into_workspace(tmp_path):
    http = ScriptedHttp(
        {
            ("POST", "/api/v1/generate"): FakeResponse(200, {"contentId": "gen_1"}),
            ("GET", "/api/v1/generations/gen_1"): [
                FakeResponse(200, {"status": "processing"}),
                FakeResponse(
                    200,
                    {"status": "succeeded", "outputUrl": "https://cdn.pixio.test/out/gen_1.png"},
                ),
            ],
            ("GET", "https://cdn.pixio.test/out/gen_1.png"): FakeResponse(
                200, content=b"\x89PNG fake bytes"
            ),
        }
    )
    tool = PixioGenerateTool(key_resolver=lambda: _KEY, http=http, poll_seconds=0)

    res = await tool.execute(
        {"model_id": "pixio/flux", "params": {"prompt": "a fox"}}, _ctx(tmp_path)
    )

    assert res.ok is True
    assert res.data["generation_id"] == "gen_1"
    assert res.data["status"] == "succeeded"
    assert res.data["output_url"] == "https://cdn.pixio.test/out/gen_1.png"
    assert res.data["saved_path"] == "pixio/gen_1.png"  # ext guessed from the URL
    assert (tmp_path / "pixio" / "gen_1.png").read_bytes() == b"\x89PNG fake bytes"
    assert "gen_1.png" in res.output and "https://cdn.pixio.test/out/gen_1.png" in res.output

    # The generate POST carried the documented body + bearer auth.
    method, url, headers, body = http.calls[0]
    assert (method, url) == ("POST", f"{_BASE_URL}/api/v1/generate")
    assert headers["Authorization"] == f"Bearer {_KEY}"
    assert body == {"providerId": "pixio", "modelId": "pixio/flux", "params": {"prompt": "a fox"}}
    # The CDN download must NOT leak the bearer key to a third-party host.
    assert http.calls[-1][2] == {}


# --- key handling ---------------------------------------------------------------


async def test_missing_key_is_a_clear_error_and_no_http(tmp_path):
    tool = PixioModelsTool(key_resolver=lambda: None, http=_never_called)
    res = await tool.execute({}, _ctx(tmp_path))
    assert res.ok is False
    assert "PIXIO_API_KEY" in res.error and "pixio" in res.error


# --- error mapping ----------------------------------------------------------------


async def test_402_maps_to_insufficient_credits(tmp_path):
    http = ScriptedHttp(
        {("POST", "/api/v1/generate"): FakeResponse(402, {"error": "Insufficient credits"})}
    )
    tool = PixioGenerateTool(key_resolver=lambda: _KEY, http=http, poll_seconds=0)
    res = await tool.execute({"model_id": "pixio/flux", "params": {}}, _ctx(tmp_path))
    assert res.ok is False
    assert "402" in res.error and "credits" in res.error.lower()


async def test_429_maps_to_concurrency_one_liner(tmp_path):
    http = ScriptedHttp({("POST", "/api/v1/generate"): FakeResponse(429, {})})
    tool = PixioGenerateTool(key_resolver=lambda: _KEY, http=http, poll_seconds=0)
    res = await tool.execute({"model_id": "pixio/flux", "params": {}}, _ctx(tmp_path))
    assert res.ok is False
    assert "one generation at a time" in res.error


async def test_failed_generation_surfaces_api_detail(tmp_path):
    http = ScriptedHttp(
        {
            ("POST", "/api/v1/generate"): FakeResponse(200, {"id": "gen_bad"}),
            ("GET", "/api/v1/generations/gen_bad"): FakeResponse(
                200, {"status": "failed", "error": "nsfw filter tripped"}
            ),
        }
    )
    tool = PixioGenerateTool(key_resolver=lambda: _KEY, http=http, poll_seconds=0)
    res = await tool.execute({"model_id": "pixio/flux", "params": {}}, _ctx(tmp_path))
    assert res.ok is False
    assert "nsfw filter tripped" in res.error
    assert res.data["status"] == "failed"


# --- wait=false: fire and forget -----------------------------------------------


async def test_wait_false_returns_id_without_polling(tmp_path):
    # `id` (not `contentId`) exercises the response-field tolerance.
    http = ScriptedHttp({("POST", "/api/v1/generate"): FakeResponse(200, {"id": "gen_9"})})
    tool = PixioGenerateTool(key_resolver=lambda: _KEY, http=http, poll_seconds=0)
    res = await tool.execute(
        {"model_id": "pixio/veo", "params": {"prompt": "x"}, "wait": False}, _ctx(tmp_path)
    )
    assert res.ok is True
    assert res.data == {"generation_id": "gen_9", "status": "pending"}
    assert "pixio_status" in res.output  # tells the agent how to follow up
    assert len(http.calls) == 1  # the POST only — no generations/ poll


# --- pixio_status: same delivery behavior on a later check ----------------------


async def test_status_downloads_on_succeeded(tmp_path):
    http = ScriptedHttp(
        {
            ("GET", "/api/v1/generations/gen_2"): FakeResponse(
                200, {"status": "succeeded", "outputUrl": "https://cdn.pixio.test/out/clip"}
            ),
            ("GET", "https://cdn.pixio.test/out/clip"): FakeResponse(200, content=b"movie"),
        }
    )
    tool = PixioStatusTool(key_resolver=lambda: _KEY, http=http)
    res = await tool.execute({"generation_id": "gen_2"}, _ctx(tmp_path))
    assert res.ok is True
    assert res.data["saved_path"] == "pixio/gen_2.bin"  # no extension in URL -> .bin
    assert (tmp_path / "pixio" / "gen_2.bin").read_bytes() == b"movie"


# --- discovery tools ---------------------------------------------------------------


async def test_models_lists_compact_ids(tmp_path):
    http = ScriptedHttp(
        {
            ("GET", "/api/v1/models"): FakeResponse(
                200,
                {"models": [{"id": "pixio/flux", "name": "Flux", "type": "image"}]},
            )
        }
    )
    tool = PixioModelsTool(key_resolver=lambda: _KEY, http=http)
    res = await tool.execute({}, _ctx(tmp_path))
    assert res.ok is True
    assert "pixio/flux — Flux — image" in res.output
    assert res.data["count"] == 1


async def test_params_urlencodes_the_model_id(tmp_path):
    # 'pixio/flux' must reach the API as modelId=pixio%2Fflux.
    http = ScriptedHttp(
        {
            ("GET", "/api/v1/params?modelId=pixio%2Fflux"): FakeResponse(
                200, {"required": ["prompt"], "optional": {"steps": 20}}
            )
        }
    )
    tool = PixioParamsTool(key_resolver=lambda: _KEY, http=http)
    res = await tool.execute({"model_id": "pixio/flux"}, _ctx(tmp_path))
    assert res.ok is True
    assert res.data["params"]["required"] == ["prompt"]
    assert "prompt" in res.output


# --- factory ------------------------------------------------------------------------


def test_factory_builds_the_four_tools():
    tools = pixio_tools(key_resolver=lambda: _KEY)
    assert [t.name for t in tools] == [
        "pixio_models",
        "pixio_params",
        "pixio_generate",
        "pixio_status",
    ]
    assert all(t.perm_key() == "pixio" for t in tools)  # one permission switch
