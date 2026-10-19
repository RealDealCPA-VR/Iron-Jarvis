"""v1.346.0 lane V-providers — media services behind ``generate_media``.

Offline: every provider talks to an injected fake transport (the
``tests/test_pixio_tools.py`` pattern); no real Pixio or OpenAI call. Pins:

* ``Quote.words`` for free / paid / provisional / usd / unknown;
* ``PixioProvider`` — the FREE rule (an allowance applied or a plan-covered
  model, never "zero and we hope", never provisional), the 402/422/429 words
  carrying shortfall / inputHint / Retry-After, the Idempotency-Key on
  ``start``, the four poll states, and the key NEVER reaching the CDN host;
* ``OpenAIImagesProvider`` — never free, the list-price table, a b64 payload
  round trip through ``fetch``;
* ``FakeProvider`` — free when the prompt says free, a real PNG, the MP4
  fixture, "lost at restart";
* ``MediaRegistry.choose`` — explicit / setting / the only one / refusals;
* ``deliver`` — ``media/<provider>/``, the gallery sink's media kind;
* the settings rows, their defaults and the daemon whitelist (v1.320.2);
* ``tools/pixio.py`` really calls the lifted helpers (no second copy).
"""

from __future__ import annotations

import base64
import io
import os
import re
from pathlib import Path

import pytest

from iron_jarvis.core.config import Config
from iron_jarvis.daemon.schemas import _SETTINGS_KEYS
from iron_jarvis.media import JobState, MediaError, Quote
from iron_jarvis.media.base import NETWORK_TIMEOUT_S, MediaProvider
from iron_jarvis.media.deliver import deliver
from iron_jarvis.media.fake import FIXTURE_MP4, FakeProvider
from iron_jarvis.media.openai_images import LIST_PRICES_USD, OpenAIImagesProvider
from iron_jarvis.media.pixio import PixioProvider
from iron_jarvis.media.pixio_http import BASE_URL
from iron_jarvis.media.registry import MediaRegistry, build_registry
from iron_jarvis.settings import schema

ROOT = Path(__file__).resolve().parents[1]
_KEY = "pxio_live_secret_key_value"
_CDN = "https://cdn.pixio.test/out/gen_1.png"


def _read(rel: str) -> str:
    """Source read tolerant of a CRLF checkout (the CI runner's autocrlf)."""
    return (ROOT / rel).read_text(encoding="utf-8").replace("\r\n", "\n")


# --------------------------------------------------------------------------- #
# transport doubles
# --------------------------------------------------------------------------- #


class FakeResponse:
    def __init__(self, status_code=200, json_body=None, content=b"", headers=None):
        self.status_code = status_code
        self._json = json_body
        self.content = content
        self.headers = headers or {}

    def json(self):
        if self._json is None:
            raise ValueError("no json body")
        return self._json


class ScriptedHttp:
    """Routes ``(method, path)`` to scripted answers; a list is consumed in
    order (the last repeats). Every call is recorded."""

    def __init__(self, script):
        self._script = {k: list(v) if isinstance(v, list) else [v] for k, v in script.items()}
        self.calls: list[tuple[str, str, dict, dict | None]] = []

    def __call__(self, method, url, headers, json_body):
        self.calls.append((method, url, dict(headers), json_body))
        path = url[len(BASE_URL):] if url.startswith(BASE_URL) else url
        queue = self._script[(method, path)]
        return queue.pop(0) if len(queue) > 1 else queue[0]

    def calls_to(self, path_suffix: str):
        return [c for c in self.calls if c[1].endswith(path_suffix)]


def _never(method, url, headers, json_body):  # pragma: no cover - must not run
    raise AssertionError(f"no HTTP expected, got {method} {url}")


def _pixio(script, *, key=_KEY, config=None, clock=None):
    http = ScriptedHttp(script)
    kw = {"config": config}
    if clock is not None:
        kw["clock"] = clock
    return PixioProvider(key_resolver=lambda: key, http=http, **kw), http


def _estimate(quote: dict, status=200):
    return {("POST", "/api/v1/generations/estimate"): FakeResponse(status, quote)}


# --------------------------------------------------------------------------- #
# Quote.words
# --------------------------------------------------------------------------- #


def test_quote_words_cover_free_paid_provisional_usd_and_unknown():
    free = Quote(0.0, free=True, free_label="Maker: Avatars, 4 left today")
    assert free.words("image", "Pixio") == "Generate 1 image on Pixio: free (Maker: Avatars, 4 left today)"
    assert free.cost_note() == "free (Maker: Avatars, 4 left today)"
    paid = Quote(12.0, note="your price")
    assert paid.words("image", "Pixio") == "Generate 1 image on Pixio for 12 credits (your price)"
    assert paid.cost_note() == "12 credits (your price)"
    prov = Quote(12.0, provisional=True, note="estimate")
    assert prov.words("video", "Pixio") == "Generate 1 video on Pixio for about 12 credits (estimate)"
    usd = Quote(0.042, currency="usd", note="list price")
    assert usd.words("image", "OpenAI") == "Generate 1 image on OpenAI for $0.042 (list price)"
    unknown = Quote(None)
    assert unknown.words("music", "Pixio") == "Generate 1 music on Pixio; price not known until it runs"
    assert unknown.cost_note() == "price not known until it runs"
    assert Quote(1.0).cost_note() == "1 credit"
    assert Quote(0.0, free=True).cost_note() == "free"


# --------------------------------------------------------------------------- #
# PixioProvider — quotes and the free rule
# --------------------------------------------------------------------------- #


async def test_pixio_free_when_an_allowance_applied_and_the_label_says_what_is_left():
    p, http = _pixio(_estimate({
        "quote": {
            "status": "measured", "expectedDebit": 0, "listCost": 12, "planDiscount": 0,
            "allowance": {"applied": True, "slug": "avatars", "label": "Avatars",
                          "dailyLimit": 10, "remainingToday": 4, "nextAllowanceAt": None},
        },
        "note": "Maker allowance",
    }))
    q = await p.quote("pixio/avatar", {"prompt": "a fox"}, "image")
    assert q.free is True
    assert q.expected_cost == 0
    assert q.free_label == "Maker: Avatars, 4 left today"
    assert q.words("image", p.label) == "Generate 1 image on Pixio: free (Maker: Avatars, 4 left today)"
    method, url, headers, body = http.calls[0]
    assert (method, url) == ("POST", f"{BASE_URL}/api/v1/generations/estimate")
    assert body == {"modelId": "pixio/avatar", "params": {"prompt": "a fox"}}
    assert headers["Authorization"] == f"Bearer {_KEY}"


async def test_pixio_paid_quote_is_your_price_and_not_free():
    p, _ = _pixio(_estimate({"quote": {"status": "measured", "expectedDebit": 12, "listCost": 15,
                                       "planDiscount": 0.2, "allowance": None}}))
    q = await p.quote("pixio/flux", {"prompt": "x"}, "image")
    assert q.free is False and q.provisional is False
    assert q.expected_cost == 12 and q.currency == "credits"
    assert q.words("image", p.label) == "Generate 1 image on Pixio for 12 credits (your price)"


async def test_pixio_provisional_quote_is_never_free_even_with_an_allowance():
    """The free-rule mutation: ``not provisional`` dropped → this goes red."""
    p, _ = _pixio(_estimate({"quote": {"status": "provisional", "expectedDebit": 0,
                                       "allowance": {"applied": True, "remainingToday": 2}}}))
    q = await p.quote("pixio/veo", {"prompt": "x"}, "video")
    assert q.free is False
    assert q.provisional is True
    assert q.note == "estimate"
    assert "estimate" in q.words("video", p.label)


async def test_pixio_zero_debit_without_a_reason_is_not_free():
    """``expectedDebit == 0`` alone never earns "free": the service must say
    WHY (allowance applied, or plan-covered). Mutating ``free`` to the bare
    zero check goes red here."""
    p, _ = _pixio(_estimate({"quote": {"status": "measured", "expectedDebit": 0, "allowance": None,
                                       "planDiscount": 0}}))
    q = await p.quote("pixio/flux", {"prompt": "x"}, "image")
    assert q.free is False
    # And an allowance that exists but did NOT apply is not free either.
    p2, _ = _pixio(_estimate({"quote": {"status": "measured", "expectedDebit": 0,
                                        "allowance": {"applied": False, "remainingToday": 0}}}))
    assert (await p2.quote("pixio/flux", {}, "image")).free is False


async def test_pixio_plan_covered_model_is_free_unless_not_covered():
    p, _ = _pixio(_estimate({"quote": {"status": "measured", "expectedDebit": 0, "listCost": 8,
                                       "planDiscount": 1, "allowance": None}}))
    q = await p.quote("pixio/flux", {}, "image")
    assert q.free is True and q.free_label == "covered by your plan"
    p2, _ = _pixio(_estimate({"quote": {"status": "measured", "expectedDebit": 0, "listCost": 8,
                                        "planDiscount": 1, "notCoveredBy": "free", "allowance": None}}))
    assert (await p2.quote("pixio/flux", {}, "image")).free is False


async def test_pixio_without_a_key_refuses_before_any_http():
    p = PixioProvider(key_resolver=lambda: None, http=_never)
    assert p.available() is False
    with pytest.raises(MediaError) as exc:
        await p.quote("pixio/flux", {}, "image")
    assert exc.value.code == "not_configured"
    assert "pixio" in exc.value.words and "PIXIO_API_KEY" in exc.value.words
    assert PixioProvider(key_resolver=lambda: _KEY, http=_never).available() is True


# --------------------------------------------------------------------------- #
# PixioProvider — the three refusals, in plain words
# --------------------------------------------------------------------------- #


async def test_pixio_402_carries_the_shortfall():
    p, _ = _pixio(_estimate({"error": "Insufficient credits", "code": "insufficient_credits",
                             "availableCredits": 3, "requiredCredits": 12, "shortfall": 9}, 402))
    with pytest.raises(MediaError) as exc:
        await p.quote("pixio/flux", {}, "image")
    err = exc.value
    assert err.code == "insufficient_credits" and err.shortfall == 9
    assert "9 more needed" in err.words and "3 available" in err.words and "12 required" in err.words
    assert _KEY not in err.words


async def test_pixio_422_is_final_and_carries_the_input_hint():
    p, _ = _pixio({("POST", "/api/v1/generate"): FakeResponse(
        422, {"error": "Content policy", "code": "content_policy", "inputHint": "remove the brand name"})})
    with pytest.raises(MediaError) as exc:
        await p.start("pixio/flux", {"prompt": "x"}, "image", "ij-1")
    err = exc.value
    assert err.code == "content_policy" and err.final is True
    assert err.input_hint == "remove the brand name" and "remove the brand name" in err.words


async def test_pixio_429_carries_retry_after_seconds():
    p, _ = _pixio({("POST", "/api/v1/generate"): FakeResponse(
        429, {"error": "Concurrency limit", "code": "concurrency_limit"}, headers={"Retry-After": "17"})})
    with pytest.raises(MediaError) as exc:
        await p.start("pixio/flux", {"prompt": "x"}, "image", "ij-1")
    err = exc.value
    assert err.code == "concurrency_limit" and err.retry_after_s == 17
    assert "wait 17 s" in err.words


# --------------------------------------------------------------------------- #
# PixioProvider — start / poll / fetch
# --------------------------------------------------------------------------- #


async def test_pixio_start_sends_the_idempotency_key_and_returns_the_content_id():
    p, http = _pixio({("POST", "/api/v1/generate"): FakeResponse(202, {"contentId": "gen_7", "status": "pending"})})
    handle = await p.start("pixio/flux", {"prompt": "a fox"}, "image", "ij-job-abc")
    assert handle.job_id == "gen_7"
    assert handle.eta_s == 20  # the image default when the service gives no eta
    method, url, headers, body = http.calls[-1]
    assert (method, url) == ("POST", f"{BASE_URL}/api/v1/generate")
    assert headers["Idempotency-Key"] == "ij-job-abc"
    assert headers["Authorization"] == f"Bearer {_KEY}"
    assert body == {"providerId": "pixio", "modelId": "pixio/flux", "params": {"prompt": "a fox"}}
    video = await PixioProvider(
        key_resolver=lambda: _KEY,
        http=ScriptedHttp({("POST", "/api/v1/generate"): FakeResponse(202, {"contentId": "v1", "etaSeconds": 240})}),
    ).start("pixio/veo", {}, "video", "ij-2")
    assert video.eta_s == 240


async def test_pixio_poll_maps_the_four_statuses():
    p, _ = _pixio({("GET", "/api/v1/generations/gen_7"): [
        FakeResponse(200, {"status": "pending"}),
        FakeResponse(200, {"status": "processing"}),
        FakeResponse(200, {"status": "succeeded", "outputUrl": _CDN, "billing": {"creditsCharged": 12}}),
    ], ("GET", "/api/v1/generations/gen_bad"): FakeResponse(200, {"status": "failed", "error": "nsfw filter tripped"})})
    assert (await p.poll("gen_7")).status == "pending"
    assert (await p.poll("gen_7")).status == "running"
    done = await p.poll("gen_7")
    assert done.status == "succeeded" and done.output_url == _CDN and done.ext == ".png"
    assert done.cost_note == "12 credits charged"
    bad = await p.poll("gen_bad")
    assert bad.status == "failed" and "nsfw filter tripped" in bad.error


async def test_pixio_fetch_never_sends_the_key_to_the_cdn_host():
    """The API calls carry the bearer (anti-vacuity); the CDN GET carries NO
    headers at all. Mutating fetch_public to reuse the API headers goes red."""
    p, http = _pixio({
        ("GET", "/api/v1/generations/gen_7"): FakeResponse(200, {"status": "succeeded", "outputUrl": _CDN}),
        ("GET", _CDN): FakeResponse(200, content=b"\x89PNG fake bytes"),
    })
    state = await p.poll("gen_7")
    blob = await p.fetch(state)
    assert blob == b"\x89PNG fake bytes"
    api_call = http.calls_to("/generations/gen_7")[0]
    assert api_call[2]["Authorization"] == f"Bearer {_KEY}"
    cdn_call = http.calls_to("gen_1.png")[0]
    assert cdn_call[2] == {}, cdn_call[2]
    assert all(_KEY not in str(v) for v in cdn_call[2].values())


async def test_pixio_fetch_download_failure_is_a_plain_error():
    p, _ = _pixio({("GET", _CDN): FakeResponse(503, content=b"")})
    with pytest.raises(MediaError) as exc:
        await p.fetch(JobState("succeeded", output_url=_CDN))
    assert "download failed (503)" in exc.value.words


# --------------------------------------------------------------------------- #
# PixioProvider — models and the default model
# --------------------------------------------------------------------------- #


_MODELS = {"models": [
    {"id": "pixio/flux", "name": "Flux", "type": "image"},
    {"id": "pixio/sd-cheap", "name": "SD", "category": "image"},
    {"id": "pixio/veo", "name": "Veo", "type": "video"},
    {"id": "pixio/song", "name": "Song", "kind": "music"},
]}


async def test_pixio_default_model_is_the_cheapest_of_the_kind_by_your_credits():
    p, http = _pixio({
        ("GET", "/api/v1/models"): FakeResponse(200, _MODELS),
        ("GET", "/api/v1/pricing"): FakeResponse(200, {"pricing": [
            {"modelId": "pixio/flux", "listCredits": 12, "yourCredits": 9},
            {"modelId": "pixio/sd-cheap", "listCredits": 10, "yourCredits": 4},
            {"modelId": "pixio/veo", "listCredits": 80, "yourCredits": 1},  # a VIDEO model: never for images
        ]}),
    })
    assert await p.default_model("image") == "pixio/sd-cheap"
    assert await p.default_model("video") == "pixio/veo"
    assert await p.default_model("music") == "pixio/song"  # not priced -> the first of its kind
    assert len(http.calls_to("/models")) == 1, "the models list is cached"


async def test_pixio_default_model_falls_back_to_the_first_of_the_kind_without_pricing():
    p, _ = _pixio({
        ("GET", "/api/v1/models"): FakeResponse(200, _MODELS),
        ("GET", "/api/v1/pricing"): FakeResponse(500, {"error": "boom"}),
    })
    assert await p.default_model("image") == "pixio/flux"
    with pytest.raises(MediaError) as exc:
        await p.default_model("audio")
    assert exc.value.code == "unknown_model" and "audio" in exc.value.words


async def test_pixio_default_model_honours_the_config_pin_without_a_call():
    cfg = Config(home=Path("."), project_root=Path("."), media_default_models={"image": "pixio/my-pick"})
    p = PixioProvider(key_resolver=lambda: _KEY, http=_never, config=cfg)
    assert await p.default_model("image") == "pixio/my-pick"


async def test_pixio_models_cache_expires_after_ten_minutes():
    now = [1000.0]
    p, http = _pixio({("GET", "/api/v1/models"): FakeResponse(200, _MODELS)}, clock=lambda: now[0])
    assert [m["id"] for m in await p.models("image")] == ["pixio/flux", "pixio/sd-cheap"]
    now[0] += 599
    await p.models("video")
    assert len(http.calls_to("/models")) == 1
    now[0] += 2
    await p.models("video")
    assert len(http.calls_to("/models")) == 2


def test_every_metadata_call_is_bounded():
    assert NETWORK_TIMEOUT_S <= 20


# --------------------------------------------------------------------------- #
# OpenAIImagesProvider
# --------------------------------------------------------------------------- #

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


class OneCall:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def __call__(self, method, url, headers, json_body):
        self.calls.append((method, url, dict(headers), json_body))
        return self.response


async def test_openai_images_is_never_free_and_quotes_the_list_table():
    p = OpenAIImagesProvider(key_resolver=lambda: "sk-test", http=_never)
    assert p.kinds() == {"image"} and p.available() is True
    assert await p.default_model("image") == "gpt-image-1"
    for (model, quality, size), price in LIST_PRICES_USD.items():
        q = await p.quote(model, {"quality": quality, "size": size}, "image")
        assert q.free is False and q.provisional is False
        assert q.currency == "usd" and q.expected_cost == price and q.note == "list price"
    q = await p.quote("gpt-image-1", {}, "image")
    assert q.expected_cost == 0.042  # the medium / 1024x1024 default
    assert q.words("image", p.label) == "Generate 1 image on OpenAI for $0.042 (list price)"
    odd = await p.quote("gpt-image-1", {"size": "9999x1"}, "image")
    assert odd.free is False and odd.expected_cost is None
    assert "price not known" in odd.words("image", p.label)
    assert OpenAIImagesProvider(key_resolver=lambda: None, http=_never).available() is False


async def test_openai_images_round_trips_a_b64_payload_through_poll_and_fetch():
    http = OneCall(FakeResponse(200, {"data": [{"b64_json": base64.b64encode(_PNG).decode()}]}))
    p = OpenAIImagesProvider(key_resolver=lambda: "sk-test", http=http)
    handle = await p.start("gpt-image-1", {"prompt": "a fox", "size": "1024x1024"}, "image", "ij-9")
    assert handle.eta_s == 0 and handle.job_id.startswith("oai-")
    method, url, headers, body = http.calls[0]
    assert (method, url) == ("POST", "https://api.openai.com/v1/images/generations")
    assert headers["Authorization"] == "Bearer sk-test"
    assert body == {"model": "gpt-image-1", "n": 1, "prompt": "a fox", "size": "1024x1024"}
    state = await p.poll(handle.job_id)
    assert state.status == "succeeded" and state.ext == ".png" and state.output_url == ""
    assert await p.fetch(state) == _PNG
    assert (await p.poll("oai-unknown")).status == "failed"
    dalle = OneCall(FakeResponse(200, {"data": [{"b64_json": base64.b64encode(_PNG).decode()}]}))
    await OpenAIImagesProvider(key_resolver=lambda: "sk-test", http=dalle).start("dall-e-3", {"prompt": "x"}, "image", "ij")
    assert dalle.calls[0][3]["response_format"] == "b64_json"


async def test_openai_images_refusals_are_plain_words_without_the_key():
    p = OpenAIImagesProvider(key_resolver=lambda: "sk-test", http=OneCall(FakeResponse(401, {"error": {"message": "bad key sk-test"}})))
    with pytest.raises(MediaError) as exc:
        await p.start("gpt-image-1", {"prompt": "x"}, "image", "ij")
    assert exc.value.code == "not_configured" and "sk-test" not in exc.value.words
    p = OpenAIImagesProvider(key_resolver=lambda: "sk-test", http=OneCall(FakeResponse(
        400, {"error": {"code": "content_policy_violation", "message": "rejected by the safety system"}})))
    with pytest.raises(MediaError) as exc:
        await p.start("gpt-image-1", {"prompt": "x"}, "image", "ij")
    assert exc.value.code == "content_policy" and exc.value.final is True
    p = OpenAIImagesProvider(key_resolver=lambda: "sk-test", http=OneCall(FakeResponse(429, {}, headers={"retry-after": "5"})))
    with pytest.raises(MediaError) as exc:
        await p.start("gpt-image-1", {"prompt": "x"}, "image", "ij")
    assert exc.value.code == "concurrency_limit" and exc.value.retry_after_s == 5
    with pytest.raises(MediaError) as exc:
        await OpenAIImagesProvider(key_resolver=lambda: None, http=_never).start("gpt-image-1", {}, "image", "ij")
    assert exc.value.code == "not_configured"


# --------------------------------------------------------------------------- #
# FakeProvider
# --------------------------------------------------------------------------- #


async def test_fake_is_off_unless_enabled_and_quotes_free_only_when_the_prompt_says_so(monkeypatch):
    monkeypatch.delenv("IRONJARVIS_MEDIA_FAKE", raising=False)
    assert FakeProvider().available() is False
    monkeypatch.setenv("IRONJARVIS_MEDIA_FAKE", "1")
    assert FakeProvider().available() is True
    p = FakeProvider(enabled=True)
    free = await p.quote("fake/image", {"prompt": "a FREE fox"}, "image")
    assert free.free is True and free.free_label
    paid = await p.quote("fake/image", {"prompt": "a fox"}, "image")
    assert paid.free is False and paid.expected_cost == 7


async def test_fake_image_is_a_real_png_after_its_delay():
    from PIL import Image

    now = [0.0]
    p = FakeProvider(enabled=True, clock=lambda: now[0])
    handle = await p.start("fake/image", {"prompt": "a fox"}, "image", "ij-1")
    assert handle.eta_s == 1
    assert (await p.poll(handle.job_id)).status == "running"
    now[0] += 1.0
    state = await p.poll(handle.job_id)
    assert state.status == "succeeded" and state.ext == ".png"
    img = Image.open(io.BytesIO(await p.fetch(state)))
    assert img.format == "PNG" and img.size == (256, 256)
    # The same idempotency key returns the same job.
    again = await p.start("fake/image", {"prompt": "a fox"}, "image", "ij-1")
    assert again.job_id == handle.job_id


async def test_fake_video_is_the_fixture_after_the_configured_delay():
    assert FIXTURE_MP4.exists()
    fixture = FIXTURE_MP4.read_bytes()
    assert fixture[4:8] == b"ftyp", "a real MP4 container"
    now = [0.0]
    p = FakeProvider(enabled=True, delay_s=3, clock=lambda: now[0])
    handle = await p.start("fake/video", {"prompt": "waves"}, "video", "ij-v")
    assert handle.eta_s == 3
    now[0] += 2.9
    assert (await p.poll(handle.job_id)).status == "running"
    now[0] += 0.2
    state = await p.poll(handle.job_id)
    assert state.status == "succeeded" and state.ext == ".mp4"
    assert await p.fetch(state) == fixture
    assert (await p.poll("fake-nope")).error == "lost at restart"
    audio = await p.start("fake/audio", {"prompt": "rain"}, "audio", "ij-a")
    now[0] += 5
    assert (await p.poll(audio.job_id)).payload[:4] == b"RIFF"


# --------------------------------------------------------------------------- #
# MediaRegistry.choose
# --------------------------------------------------------------------------- #


class Stub(MediaProvider):
    def __init__(self, id_, label, on):
        self.id, self.label, self._on = id_, label, on

    def kinds(self):
        return {"image"}

    def available(self):
        return self._on

    async def default_model(self, kind):
        return "m"

    async def models(self, kind):
        return []

    async def quote(self, model, params, kind):
        return Quote(None)

    async def start(self, model, params, kind, idem_key):
        raise NotImplementedError

    async def poll(self, job_id):
        raise NotImplementedError


def _cfg(provider="auto"):
    return Config(home=Path("."), project_root=Path("."), media_provider=provider)


def test_registry_chooses_the_explicit_name_then_the_setting_then_the_only_one():
    pixio, oai = Stub("pixio", "Pixio", True), Stub("openai_images", "OpenAI", True)
    reg = MediaRegistry([pixio, oai], config=_cfg("auto"))
    assert reg.choose("openai_images") is oai
    assert reg.choose("Pixio") is pixio
    assert MediaRegistry([pixio, oai], config=_cfg("openai_images")).choose() is oai
    assert MediaRegistry([pixio, Stub("openai_images", "OpenAI", False)], config=_cfg("auto")).choose() is pixio
    assert MediaRegistry([pixio, oai], config=_cfg("auto")).available() == [pixio, oai]


def test_registry_refusals_name_the_known_services_and_what_to_connect():
    pixio_off, oai_off = Stub("pixio", "Pixio", False), Stub("openai_images", "OpenAI", False)
    with pytest.raises(MediaError) as exc:
        MediaRegistry([pixio_off, oai_off], config=_cfg()).choose("midjourney")
    assert exc.value.code == "unknown_provider" and "pixio" in exc.value.words and "openai_images" in exc.value.words
    with pytest.raises(MediaError) as exc:
        MediaRegistry([pixio_off, oai_off], config=_cfg()).choose("pixio")
    # v1.353.0 (G40): the place a person goes, never a vault name or an
    # environment variable (changed on purpose; tests/test_chat_guide_media_doors_v13530.py).
    assert exc.value.code == "not_configured"
    assert "Settings › Connections › Accounts & keys" in exc.value.words
    assert "secret 'pixio'" not in exc.value.words and "PIXIO_API_KEY" not in exc.value.words
    with pytest.raises(MediaError) as exc:
        MediaRegistry([pixio_off, oai_off], config=_cfg()).choose()
    assert exc.value.code == "not_configured"
    assert "No media service is connected" in exc.value.words
    # v1.353.0 (G40): each service is named by its label ("Pixio", "OpenAI"),
    # no longer by the vault name the old hint carried ("secret 'pixio'").
    assert "Pixio" in exc.value.words and "OpenAI" in exc.value.words
    assert "secret '" not in exc.value.words
    with pytest.raises(MediaError) as exc:
        MediaRegistry([pixio_off, oai_off], config=_cfg("stability")).choose()
    assert "Media provider setting" in exc.value.words
    both = MediaRegistry([Stub("pixio", "Pixio", True), Stub("openai_images", "OpenAI", True)], config=_cfg())
    with pytest.raises(MediaError) as exc:
        both.choose()
    assert "More than one" in exc.value.words and "pixio, openai_images" in exc.value.words


def test_the_platform_attaches_a_registry_with_pixio_and_openai_and_the_fake_only_by_env(platform, monkeypatch):
    reg = platform.media
    assert isinstance(reg, MediaRegistry)
    assert reg.known_ids() == ["pixio", "openai_images"]
    monkeypatch.setenv("IRONJARVIS_MEDIA_FAKE", "1")
    assert build_registry(platform).known_ids() == ["pixio", "openai_images", "fake"]
    monkeypatch.delenv("IRONJARVIS_MEDIA_FAKE")
    # Pixio reads the vault secret 'pixio' (then the env) — a key-presence read, no network.
    monkeypatch.delenv("PIXIO_API_KEY", raising=False)
    assert reg.get("pixio").available() is False
    platform.secrets.set("pixio", _KEY)
    assert reg.get("pixio").available() is True


# --------------------------------------------------------------------------- #
# deliver
# --------------------------------------------------------------------------- #


def test_deliver_writes_under_media_provider_and_tags_the_gallery_copy(tmp_path):
    calls = []

    def sink(name, blob, filename, kind, session_id, **kw):
        calls.append((name, blob, filename, kind, session_id, kw))

    out = deliver(b"movie", kind="video", provider="pixio", job_id="gen/7", ext=".mp4",
                  workspace=tmp_path, session_id="chat", project_id="proj_1", artifact_sink=sink)
    assert out["rel"] == "media/pixio/gen_7.mp4"
    assert Path(out["abs_path"]).is_absolute() and Path(out["abs_path"]).read_bytes() == b"movie"
    assert out["artifact"] == "creative-gen_7" and out["created"] is True
    assert calls == [("creative-gen_7", b"movie", "gen_7.mp4", "video", "chat", {"project_id": "proj_1"})]
    again = deliver(b"movie2", kind="video", provider="pixio", job_id="gen/7", ext="mp4",
                    workspace=tmp_path, session_id="chat", artifact_sink=sink)
    assert again["created"] is False and calls[-1][5] == {}  # no project -> no keyword
    png = deliver(b"png", kind="image", provider="openai_images", job_id="oai-1", ext=".png",
                  workspace=tmp_path, session_id=None, artifact_sink=sink)
    assert png["rel"] == "media/openai_images/oai-1.png" and calls[-1][3] == "image"
    legacy = deliver(b"x", kind="", provider="pixio", job_id="g", ext=".bin",
                     workspace=tmp_path, session_id="s", rel_dir="pixio")
    assert legacy["rel"] == "pixio/g.bin" and legacy["artifact"] == ""


def test_the_pixio_tools_call_the_lifted_helpers_instead_of_a_second_copy():
    src = _read("src/iron_jarvis/tools/pixio.py")
    assert "from ..media.deliver import ArtifactSink, deliver" in src
    assert "from ..media.base import fetch_public" in src
    body = src[src.index("async def _deliver("):src.index("def _failed(")]
    assert "deliver," in body and 'rel_dir="pixio"' in body
    assert "write_bytes" not in body and "mkdir" not in body, "the write lives in media/deliver.py"
    assert "fetch_public, url, self._http" in body
    assert "def pixio_publish" not in src and "def _http_error" not in src


# --------------------------------------------------------------------------- #
# settings rows + the daemon whitelist (the v1.320.2 rule)
# --------------------------------------------------------------------------- #

_ROWS = {
    "media_provider": "auto",
    "media_ask_when_free": False,
    "media_inline_wait_s": 45,
    "media_job_max_s": 1800,
    "media_default_models": {},
}


def test_the_media_settings_rows_exist_with_their_defaults_and_are_whitelisted():
    cfg = Config(home=Path("."), project_root=Path("."))
    for key, default in _ROWS.items():
        d = schema.BY_KEY[key]
        assert d.store == "config" and d.put is True and d.section == "Media", key
        assert getattr(cfg, key) == default, key
        assert key in _SETTINGS_KEYS, key
    assert [v for v, _ in schema.BY_KEY["media_provider"].options] == ["auto", "pixio", "openai_images"]
    assert schema.BY_KEY["media_ask_when_free"].type == "bool"


def test_a_media_setting_saved_through_put_settings_reaches_the_registry(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from iron_jarvis.daemon.app import create_app

    # This PC may carry a real key in its environment; the fresh home has none.
    monkeypatch.delenv("PIXIO_API_KEY", raising=False)
    client = TestClient(create_app(str(tmp_path)))
    r = client.put("/settings", json={"values": {"media_provider": "pixio", "media_ask_when_free": True}})
    assert r.status_code == 200, r.text
    platform = client.app.state.platform
    assert platform.config.media_provider == "pixio" and platform.config.media_ask_when_free is True
    with pytest.raises(MediaError) as exc:
        platform.media.choose()
    assert "Pixio is not connected" in exc.value.words  # the setting is what the registry reads
    assert client.get("/settings").json()["settings"]["media_provider"] == "pixio"


def test_no_secret_shape_leaks_from_the_provider_words():
    """Every refusal built here is from status codes and the service's text."""
    src = _read("src/iron_jarvis/media/pixio.py") + _read("src/iron_jarvis/media/openai_images.py")
    assert not re.search(r"f\"[^\"]*\{key\}", src.replace("Bearer {key}", "")), "a key in an f-string message"
    assert "Bearer {key}" in src  # anti-vacuity: the auth header is built here


def test_the_fake_fixture_is_tracked_and_small():
    assert os.path.getsize(FIXTURE_MP4) < 10_000
