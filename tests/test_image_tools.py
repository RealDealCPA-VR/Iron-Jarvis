"""Image tools — tiny real Pillow images + a fake ModelRouter, fully offline."""

from __future__ import annotations

import base64
from pathlib import Path

from PIL import Image

from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.images import (
    ImageConvertTool,
    ImageInfoTool,
    ImageResizeTool,
    ViewImageTool,
    image_tools,
)


def _ctx(tmp_path) -> ToolContext:
    return ToolContext(
        workspace=tmp_path,
        session_id="s",
        agent_run_id="r",
        config=None,
        event_bus=None,
        engine=None,
    )


def _png(path: Path, size=(32, 16), color=(255, 0, 0, 255)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", size, color).save(path, format="PNG")
    return path


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeRouteResult:
    def __init__(self, text: str) -> None:
        self.response = _FakeResponse(text)
        self.provider = "fake"
        self.model = "fake-vision"


class FakeRouter:
    """Records the exact completion request; returns a canned RouteResult-alike."""

    def __init__(self, text: str = "a red square") -> None:
        self.text = text
        self.calls: list[dict] = []

    async def complete(
        self,
        *,
        provider=None,
        model=None,
        system,
        messages,
        tools,
        session_id=None,
        task_class=None,
    ):
        self.calls.append(
            {
                "system": system,
                "messages": messages,
                "tools": tools,
                "session_id": session_id,
            }
        )
        return _FakeRouteResult(self.text)


class ExplodingRouter:
    async def complete(self, **kwargs):
        raise RuntimeError("provider melted")


# --- view_image: the eyes ---------------------------------------------------------


async def test_view_image_happy_path_sends_multimodal_message(tmp_path):
    _png(tmp_path / "pic.png")
    router = FakeRouter()
    tool = ViewImageTool(lambda: router)

    res = await tool.execute(
        {"path": "pic.png", "question": "What color is it?"}, _ctx(tmp_path)
    )

    assert res.ok is True
    assert res.output == "a red square"
    assert res.data["media_type"] == "image/png"

    call = router.calls[0]
    assert "visual analyst" in call["system"]
    assert call["tools"] == []
    assert call["session_id"] == "s"
    (msg,) = call["messages"]
    assert msg.role == "user"
    assert "What color is it?" in msg.content
    image = msg.images[0]
    assert image["media_type"] == "image/png"
    assert image["data_b64"]  # non-empty payload…
    assert base64.b64decode(image["data_b64"]) == (tmp_path / "pic.png").read_bytes()


async def test_view_image_default_question_used_when_omitted(tmp_path):
    _png(tmp_path / "pic.png")
    router = FakeRouter()
    res = await ViewImageTool(lambda: router).execute({"path": "pic.png"}, _ctx(tmp_path))
    assert res.ok is True
    assert "Describe this image in detail" in router.calls[0]["messages"][0].content


async def test_view_image_rejects_unsupported_suffix(tmp_path):
    (tmp_path / "pic.tiff").write_bytes(b"not really a tiff")
    res = await ViewImageTool(lambda: FakeRouter()).execute(
        {"path": "pic.tiff"}, _ctx(tmp_path)
    )
    assert res.ok is False
    assert "unsupported" in res.error
    assert "png" in res.error and "webp" in res.error and "gif" in res.error


async def test_view_image_over_8mb_points_to_image_resize(tmp_path):
    (tmp_path / "huge.png").write_bytes(b"\0" * (8 * 1024 * 1024 + 1))
    res = await ViewImageTool(lambda: FakeRouter()).execute(
        {"path": "huge.png"}, _ctx(tmp_path)
    )
    assert res.ok is False
    assert "image_resize" in res.error


async def test_view_image_empty_model_text_is_an_honest_vision_error(tmp_path):
    _png(tmp_path / "pic.png")
    res = await ViewImageTool(lambda: FakeRouter(text="")).execute(
        {"path": "pic.png"}, _ctx(tmp_path)
    )
    assert res.ok is False
    assert "vision" in res.error and "Anthropic" in res.error


async def test_view_image_router_exception_is_wrapped_not_raised(tmp_path):
    _png(tmp_path / "pic.png")
    res = await ViewImageTool(lambda: ExplodingRouter()).execute(
        {"path": "pic.png"}, _ctx(tmp_path)
    )
    assert res.ok is False
    assert "provider melted" in res.error


async def test_view_image_allows_absolute_paths_like_read_document(tmp_path):
    # Documents precedent: reads may target absolute paths outside the workspace.
    outside = _png(tmp_path / "outside" / "pic.png")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    router = FakeRouter()
    res = await ViewImageTool(lambda: router).execute(
        {"path": str(outside)}, _ctx(workspace)
    )
    assert res.ok is True
    assert res.output == "a red square"


async def test_view_image_relative_escape_is_blocked(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _png(tmp_path / "secret.png")
    res = await ViewImageTool(lambda: FakeRouter()).execute(
        {"path": "../secret.png"}, _ctx(workspace)
    )
    assert res.ok is False
    assert "escapes" in res.error


# --- image_convert ------------------------------------------------------------------


async def test_convert_png_to_jpg_produces_rgb_jpeg(tmp_path):
    _png(tmp_path / "a.png", color=(255, 0, 0, 128))  # alpha forces flattening
    res = await ImageConvertTool().execute(
        {"source": "a.png", "target": "out/a.jpg"}, _ctx(tmp_path)
    )
    assert res.ok is True
    assert res.data["format"] == "JPEG"
    with Image.open(tmp_path / "out" / "a.jpg") as img:  # parents auto-created
        assert img.format == "JPEG"
        assert img.mode == "RGB"
        assert (img.width, img.height) == (32, 16)


async def test_convert_rejects_unsupported_target_suffix(tmp_path):
    _png(tmp_path / "a.png")
    res = await ImageConvertTool().execute(
        {"source": "a.png", "target": "a.tiff"}, _ctx(tmp_path)
    )
    assert res.ok is False
    assert "unsupported" in res.error and "bmp" in res.error


# --- image_resize -------------------------------------------------------------------


async def test_resize_respects_max_width_and_aspect_ratio(tmp_path):
    _png(tmp_path / "wide.png", size=(64, 32))
    res = await ImageResizeTool().execute(
        {"source": "wide.png", "target": "small.png", "max_width": 32},
        _ctx(tmp_path),
    )
    assert res.ok is True
    assert (res.data["width"], res.data["height"]) == (32, 16)  # aspect kept
    assert (res.data["original_width"], res.data["original_height"]) == (64, 32)
    with Image.open(tmp_path / "small.png") as img:
        assert (img.width, img.height) == (32, 16)


async def test_resize_never_upscales(tmp_path):
    _png(tmp_path / "tiny.png", size=(32, 16))
    res = await ImageResizeTool().execute(
        {"source": "tiny.png", "max_width": 999, "max_height": 999}, _ctx(tmp_path)
    )
    assert res.ok is True
    assert (res.data["width"], res.data["height"]) == (32, 16)  # unchanged


async def test_resize_defaults_to_overwriting_the_source(tmp_path):
    _png(tmp_path / "pic.png", size=(64, 32))
    res = await ImageResizeTool().execute(
        {"source": "pic.png", "max_height": 8}, _ctx(tmp_path)
    )
    assert res.ok is True
    assert res.data["target"] == "pic.png"
    with Image.open(tmp_path / "pic.png") as img:
        assert (img.width, img.height) == (16, 8)


async def test_resize_requires_at_least_one_bound(tmp_path):
    _png(tmp_path / "pic.png")
    res = await ImageResizeTool().execute({"source": "pic.png"}, _ctx(tmp_path))
    assert res.ok is False
    assert "max_width" in res.error and "max_height" in res.error


# --- image_info ---------------------------------------------------------------------


async def test_image_info_reports_format_dimensions_mode_and_bytes(tmp_path):
    path = _png(tmp_path / "pic.png")
    res = await ImageInfoTool().execute({"path": "pic.png"}, _ctx(tmp_path))
    assert res.ok is True
    assert res.data["format"] == "PNG"
    assert (res.data["width"], res.data["height"]) == (32, 16)
    assert res.data["mode"] == "RGBA"
    assert res.data["bytes"] == path.stat().st_size
    assert "PNG 32x16 RGBA" in res.output


# --- factory ------------------------------------------------------------------------


def test_factory_builds_the_four_tools_under_one_permission():
    tools = image_tools(lambda: FakeRouter())
    assert [t.name for t in tools] == [
        "view_image",
        "image_convert",
        "image_resize",
        "image_info",
    ]
    assert all(t.perm_key() == "images" for t in tools)  # one permission switch
