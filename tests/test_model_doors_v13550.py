"""v1.355.0 lane 5D — model doors and forms (G46, G65, G72, G73).

Pinned as a new user meets them, through the REAL app factory (create_app + its
lifespan + the real ConnectionRegistry, SecretsManager and ProviderManager) and
the real helpers — never a stub of the thing under test:

* G46  Gemini takes an API key: the Google (Gemini) card accepts an AI Studio
  key, the adapter the real manager builds sends it as ``x-goog-api-key`` (a
  login token still rides Bearer, byte-identical to before), a request that
  names no model gets a CURRENT Gemini, and the key's Test is a model-list GET
  with the key in a header (``providers/google_key.probe_google_key``).
* G65  the five-minute sentence the create forms show says the daemon's own
  clock (``agents/runtime.SESSION_APPROVAL_TIMEOUT_S``).
* G72  the LM Studio / llama.cpp presets are addresses the existing "Fetch
  available models" route turns into each server's own model list URL.
* G73  "Download a starter model" is a catalog SET-UP job: launchable by id,
  kept out of the Build Launch list and out of AI_CLIS, typed by full path when
  the daemon's PATH predates the Ollama install.

Three pins need an edit in a file this lane does not own; each is a STRICT
xfail naming the edit, so it turns red (XPASS) the moment the integrator lands
it and the marker is taken off on purpose:
``connections/probe.py`` (a key's Test), ``daemon/routes/terminals.py``
(``launch_pane`` reads ``launch_entry``) and ``daemon/app.py``
(``_PROMOTE_DEFAULT_MODEL["google"]``).
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import LLMMessage
from iron_jarvis.providers.google_key import (
    AI_STUDIO_KEY_URL,
    GOOGLE_MODELS_URL,
    is_google_api_key,
    probe_google_key,
)
from iron_jarvis.providers.manager import GEMINI_DEFAULT_MODEL, ProviderManager
from iron_jarvis.terminals import ai_clis
from iron_jarvis.terminals.backend import FakeBackend

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "dashboard"

#: A key in Google's documented shape (AIza + 35). Never a real key.
KEY = "AIzaSyD-lane5d-test-key-0123456789abc"
#: What AI Studio makes for a NEW user since 2026-05-28: an "auth key", which
#: starts "AQ." (ai.google.dev/gemini-api/docs/api-key; Google staff on the
#: developer forum 2026-07-27: "AI Studio will now only generate Authentication
#: Key (AQ) keys"). Never a real key. (v1.355.0 review.)
NEW_KEY = "AQ.Ab8RN6L-lane5d-new-auth-key-0123456789"
LOGIN_TOKEN = "ya29.lane5d-login-token"


def _read(path: Path) -> str:
    # CRLF on the CI runner, LF here: normalise at the READER.
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _code(src: str) -> str:
    """A TS/TSX source without its comments (block, JSX-block and line), so a
    pin about what the code DOES is not tripped by a comment describing it."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", src)


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #
class _RecordingBackend(FakeBackend):
    """Keeps everything typed into each shell (the v1302 harness shape)."""

    instances: list["_RecordingBackend"] = []
    lock = threading.Lock()

    def __init__(self) -> None:
        super().__init__()
        self.env: dict | None = None
        self.written: list[str] = []
        with _RecordingBackend.lock:
            _RecordingBackend.instances.append(self)

    def start(self, argv, cwd, env, cols, rows) -> None:  # type: ignore[override]
        self.env = dict(env) if env is not None else None
        super().start(argv, cwd, env, cols, rows)

    def write(self, data) -> None:  # type: ignore[override]
        self.written.append(data if isinstance(data, str) else data.decode())
        super().write(data)


def _backend_of(pane_id: str) -> _RecordingBackend:
    found = [
        b for b in _RecordingBackend.instances
        if b.env is not None and b.env.get("IRONJARVIS_PANE_ID") == pane_id
    ]
    assert found, f"no shell was started for pane {pane_id}"
    return found[-1]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("IRON_PROXY_DATA_DIR", str(tmp_path / "ipd"))
    monkeypatch.setenv("IRONJARVIS_IRON_PROXY_BUNDLE", str(tmp_path / "no-bundle.mjs"))
    monkeypatch.delenv("IRONJARVIS_IRON_PROXY_NODE", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    _RecordingBackend.instances = []
    monkeypatch.setattr("iron_jarvis.terminals.session.default_backend", _RecordingBackend)

    async def _no_watch(*_a, **_k):
        await asyncio.Event().wait()

    monkeypatch.setattr("iron_jarvis.iron_proxy.service.watch", _no_watch)
    proj = tmp_path / "proj"
    proj.mkdir()
    with TestClient(create_app(str(proj))) as c:
        yield c


def _platform(c: TestClient):
    return c.app.state.d.platform


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self) -> dict:
        return self._payload


class _FakeGemini:
    """The Generative Language API, as the adapter's async ``http`` sees it."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def post(self, url, *, headers=None, json=None):
        self.calls.append({"url": url, "headers": dict(headers or {}), "json": json or {}})
        return _FakeResponse(
            {
                "candidates": [
                    {"content": {"role": "model", "parts": [{"text": "Hello from Gemini"}]},
                     "finishReason": "STOP"}
                ]
            }
        )


def _ask(adapter) -> str:
    res = asyncio.run(
        adapter.complete(system="", messages=[LLMMessage(role="user", content="hi")], tools=[])
    )
    return res.text


# --------------------------------------------------------------------------- #
# G46 — Gemini takes an API key
# --------------------------------------------------------------------------- #
def test_the_gemini_card_takes_an_ai_studio_key(client):
    """MUTATION: drop ``key_secret_name`` from the google spec -> the key POST
    400s ("does not accept an API key") -> red."""
    r = client.post("/connections/google/key", json={"key": KEY})
    assert r.status_code == 200, r.text
    rows = {c["provider"]: c for c in client.get("/connections").json()["connections"]}
    row = rows["google"]
    assert row["connected"] is True
    # The key is the primary way (the card shows it first); the login stays.
    assert row["method"] == "api_key"
    assert row["supports_api_key"] is True and row["supports_oauth"] is True
    assert AI_STUDIO_KEY_URL.split("://", 1)[1] in row["key_help"]
    assert KEY not in json.dumps(rows), "a key never comes back out"
    assert _platform(client).providers.available("google") is True


def test_a_pasted_key_reaches_gemini_as_x_goog_api_key_and_a_gemini_model_answers(client):
    """The adapter the REAL manager builds, reading the REAL vault through the
    REAL registry. MUTATION: build the google adapter with ``oauth=True`` and
    no per-request decision (the pre-v1.355.0 factory) -> the key rides
    ``Authorization: Bearer`` -> red."""
    assert client.post("/connections/google/key", json={"key": KEY}).status_code == 200
    adapter = _platform(client).providers.get("google")
    http = _FakeGemini()
    adapter._http = http

    assert _ask(adapter) == "Hello from Gemini"
    sent = http.calls[-1]
    assert sent["url"].endswith(f"/models/{GEMINI_DEFAULT_MODEL}:generateContent")
    assert sent["headers"]["x-goog-api-key"] == KEY
    assert "Authorization" not in sent["headers"]


@pytest.mark.parametrize("key", [NEW_KEY, KEY], ids=["auth-key-AQ", "standard-key-AIza"])
def test_a_new_users_key_reaches_gemini_as_x_goog_api_key(client, key):
    """THE new user: since 2026-05-28 AI Studio makes only "AQ." auth keys. A
    rule that knew only "AIza" sent such a key as ``Authorization: Bearer``
    (Google refuses a key there). MUTATION: drop "AQ." from
    ``GOOGLE_KEY_PREFIXES`` -> the auth-key case rides Bearer -> red."""
    assert client.post("/connections/google/key", json={"key": key}).status_code == 200
    adapter = _platform(client).providers.get("google")
    http = _FakeGemini()
    adapter._http = http

    assert _ask(adapter) == "Hello from Gemini"
    sent = http.calls[-1]["headers"]
    assert sent["x-goog-api-key"] == key
    assert "Authorization" not in sent


def test_the_header_follows_the_stored_credential_on_the_same_cached_adapter(client):
    """A login token keeps the pre-v1.355.0 Bearer header; replacing the login
    with a pasted key switches the SAME cached adapter to x-goog-api-key with
    no restart. MUTATION: decide the header once in ``__init__`` -> the second
    request still sends Bearer -> red."""
    p = _platform(client)
    p.secrets.set_oauth("google_oauth", {"access_token": LOGIN_TOKEN})
    p.connections._upsert("google", method="oauth", status="connected")
    adapter = p.providers.get("google")
    http = _FakeGemini()
    adapter._http = http

    _ask(adapter)
    first = http.calls[-1]["headers"]
    assert first["Authorization"] == f"Bearer {LOGIN_TOKEN}"
    assert "x-goog-api-key" not in first

    assert client.delete("/connections/google").status_code == 200
    assert client.post("/connections/google/key", json={"key": KEY}).status_code == 200
    assert p.providers.get("google") is adapter, "the cached adapter is the one re-used"
    _ask(adapter)
    second = http.calls[-1]["headers"]
    assert second["x-goog-api-key"] == KEY
    assert "Authorization" not in second


def test_every_door_sends_a_new_user_to_the_same_key_page():
    """The card's "Get one at", the first-run key door and the daemon's own
    help all name ONE page (where AI Studio keys are made)."""
    assert AI_STUDIO_KEY_URL == "https://aistudio.google.com/apikey"
    for rel in ("components/onboarding/ConnectDoors.tsx", "components/settings/pages/ConnectionsPage.tsx"):
        assert f'keyUrl: "{AI_STUDIO_KEY_URL}"' in _read(DASH / rel), rel


def test_a_request_naming_no_model_gets_a_current_gemini():
    """gemini-1.5-flash was retired by Google: Auto (and a bare "google")
    asked for it and got a 404. And it must be a model a NEW key can use:
    Google limits the 2.5 models to accounts that already used them and
    names 3.8 Flash for new projects (docs read 2026-10-19), so a 2.5 default
    would refuse the very user G46 is for. MUTATION: put the old default (or
    gemini-2.5-flash) back -> red."""
    pm = ProviderManager(credential_resolver=lambda name: KEY if name == "google" else None)
    assert GEMINI_DEFAULT_MODEL == "gemini-3.8-flash"
    assert not GEMINI_DEFAULT_MODEL.startswith(("gemini-1.", "gemini-2."))
    assert pm.get("google").model == GEMINI_DEFAULT_MODEL
    # The reasoning table knows it (a model the app has never heard of would
    # offer no levels and no default).
    from iron_jarvis.providers.reasoning import reasoning_default

    assert reasoning_default("google", GEMINI_DEFAULT_MODEL) == "medium"


@pytest.mark.parametrize(
    "credential, is_key",
    [
        (KEY, True),
        ("  " + KEY + "\n", True),  # a paste with stray whitespace
        (NEW_KEY, True),  # what AI Studio makes for a new user today
        (" " + NEW_KEY + "\r\n", True),
        (LOGIN_TOKEN, False),
        ("tok", False),  # anything else keeps the old Bearer header
        ("", False),
        (None, False),
        (42, False),
    ],
)
def test_one_rule_tells_a_key_from_a_login_token(credential, is_key):
    assert is_google_api_key(credential) is is_key


class _Get:
    """``httpx.get``'s shape, answering one canned status."""

    def __init__(self, status: int = 200, payload: dict | None = None, exc: Exception | None = None):
        self.status, self.payload, self.exc = status, payload or {}, exc
        self.calls: list[dict] = []

    def __call__(self, url, *, headers=None, params=None, timeout=None):
        self.calls.append({"url": url, "headers": dict(headers or {}), "params": dict(params or {})})
        if self.exc:
            raise self.exc
        return _FakeResponse(self.payload, self.status)


def test_a_keys_test_lists_one_model_with_the_key_in_a_header_only():
    get = _Get(200, {"models": [{"name": "models/gemini-2.5-flash"}]})
    ok, detail = probe_google_key(KEY, get=get)
    assert ok is True and "credential accepted" in detail
    call = get.calls[0]
    assert call["url"] == GOOGLE_MODELS_URL
    assert call["headers"] == {"x-goog-api-key": KEY}
    assert KEY not in call["url"] and KEY not in json.dumps(call["params"])


@pytest.mark.parametrize(
    "status, payload, ok, words",
    [
        # Google answers a bad key with a 400, not a 401.
        (400, {"error": {"code": 400, "message": "API key not valid. Please pass a valid API key.",
                         "status": "INVALID_ARGUMENT", "details": [{"reason": "API_KEY_INVALID"}]}},
         False, "rejected the key"),
        (403, {"error": {"message": "Generative Language API has not been used"}}, False, "rejected the key"),
        (429, {"error": {"message": "quota"}}, True, "rate-limited"),
        (500, {"error": {"message": "backend"}}, False, "HTTP 500"),
        (400, {"error": {"message": "Request contains an invalid argument."}}, False, "HTTP 400"),
    ],
)
def test_a_keys_test_reads_googles_answers(status, payload, ok, words):
    got_ok, detail = probe_google_key(KEY, get=_Get(status, payload))
    assert got_ok is ok and words in detail
    if words == "rejected the key":
        assert "aistudio.google.com/apikey" in detail  # where to get a working one
    assert KEY not in detail


def test_a_keys_test_that_cannot_reach_google_says_so_without_the_request():
    ok, detail = probe_google_key(KEY, get=_Get(exc=OSError(f"boom {KEY}")))
    assert ok is False and detail == "could not reach google: OSError"


def test_a_login_tokens_test_is_unchanged(monkeypatch):
    """The OAuth path keeps the tokeninfo check (no regression)."""
    from iron_jarvis.connections import probe

    seen: list[str] = []

    def fake_get(url, headers=None, params=None, timeout=None):
        seen.append(url)
        return _FakeResponse({"email": "me@example.com"}, 200)

    monkeypatch.setattr("httpx.get", fake_get)
    ok, _ = probe.live_probe("google", LOGIN_TOKEN)
    assert ok is True
    assert seen == ["https://www.googleapis.com/oauth2/v3/tokeninfo"]


@pytest.mark.xfail(
    strict=True,
    reason=(
        "INTEGRATOR: connections/probe.py live_probe must send a Google API key to "
        "providers.google_key.probe_google_key (tokeninfo answers 400 for every key). "
        "Remove this marker when that edit lands."
    ),
)
@pytest.mark.parametrize("key", [NEW_KEY, KEY], ids=["auth-key-AQ", "standard-key-AIza"])
def test_connections_test_turns_green_for_a_pasted_key(client, monkeypatch, key):
    """The acceptance line: paste the key on the card, press Test, it is green.
    Drives POST /connections/google/test through the real registry and the
    real live_probe; only the network answer is faked. Both key shapes (a new
    user's "AQ." auth key and an older "AIza" key)."""

    def fake_get(url, headers=None, params=None, timeout=None):
        if url == GOOGLE_MODELS_URL and (headers or {}).get("x-goog-api-key") == key:
            return _FakeResponse({"models": [{"name": "models/gemini-3.8-flash"}]}, 200)
        return _FakeResponse({"error": "invalid_token"}, 400)

    monkeypatch.setattr("httpx.get", fake_get)
    assert client.post("/connections/google/key", json={"key": key}).status_code == 200
    result = client.post("/connections/google/test").json()
    assert result["ok"] is True, result


@pytest.mark.xfail(
    strict=True,
    reason=(
        "INTEGRATOR: daemon/app.py _PROMOTE_DEFAULT_MODEL['google'] is the retired "
        "gemini-1.5-flash; set it to providers.manager.GEMINI_DEFAULT_MODEL. "
        "Remove this marker when that edit lands."
    ),
)
def test_choosing_gemini_for_answers_picks_a_current_model(client):
    """Make default / "Use Google (Gemini) for answers" write this id as the
    default model; the retired one 404s on the first chat."""
    assert client.app.state.d._PROMOTE_DEFAULT_MODEL["google"] == GEMINI_DEFAULT_MODEL


# --------------------------------------------------------------------------- #
# G65 — the five-minute sentence says the daemon's own clock
# --------------------------------------------------------------------------- #
def test_the_unattended_sentence_says_the_runtimes_own_wait():
    """MUTATION: change SESSION_APPROVAL_TIMEOUT_S (or the sentence) -> red."""
    from iron_jarvis.agents.runtime import SESSION_APPROVAL_TIMEOUT_S, _ask_timeout_for

    words = _read(DASH / "lib" / "unattendedWords.ts")
    minutes = int(re.search(r"UNATTENDED_ASK_MINUTES = (\d+);", words).group(1))
    assert minutes * 60 == SESSION_APPROVAL_TIMEOUT_S
    sentence = re.search(r'UNATTENDED_ASK_NOTE =\s*"([^"]+)"', words).group(1)
    assert f"waits {minutes} minutes" in sentence
    # The three doors the sentence sits under really are unattended.
    for origin in ("schedule:nightly", "goal:g1", "workflow:report"):
        assert _ask_timeout_for(origin) == SESSION_APPROVAL_TIMEOUT_S, origin


@pytest.mark.parametrize(
    "page", ["app/schedules/page.tsx", "app/autonomy/page.tsx", "app/workflows/page.tsx"]
)
def test_each_unattended_form_renders_the_one_sentence(page):
    """One component, one sentence — never a second copy in a page."""
    src = _read(DASH / page)
    assert 'import { UnattendedAskNote } from "@/components/UnattendedAskNote";' in src
    assert "<UnattendedAskNote" in src
    assert "waits 5 minutes" not in _code(src)


# --------------------------------------------------------------------------- #
# G72 — the presets are addresses the existing fetch understands
# --------------------------------------------------------------------------- #
def test_the_local_server_presets_list_through_the_existing_fetch():
    """The chips press the form's own "Fetch available models"; the route turns
    each preset into that server's model list (LM Studio and llama.cpp's
    llama-server both answer the OpenAI-compatible /v1/models)."""
    from iron_jarvis.daemon.routes.connections import _model_list_urls

    presets = _read(DASH / "lib" / "localServerPresets.ts")
    found = dict(re.findall(r'id: "(\w+)",[^}]*?baseUrl: "([^"]+)"', presets, re.S))
    assert found == {
        "lmstudio": "http://localhost:1234/v1",
        "llamacpp": "http://localhost:8080/v1",
    }
    assert _model_list_urls(found["lmstudio"])[0] == "http://localhost:1234/v1/models"
    assert _model_list_urls(found["llamacpp"])[0] == "http://localhost:8080/v1/models"
    assert 'label: "LM Studio on this PC"' in presets and 'label: "llama.cpp"' in presets


# --------------------------------------------------------------------------- #
# G73 — Download a model for me, without a terminal
# --------------------------------------------------------------------------- #
def _starter() -> dict:
    entry = ai_clis.launch_entry("ollama-starter")
    assert entry is not None
    return entry


def test_the_starter_download_is_a_catalog_id_kept_off_the_launch_menu():
    """Launchable by id (the route accepts only catalog ids), but not a CLI:
    the Launch menu, the classifier and the bell read AI_CLIS, unchanged.
    MUTATION: append the job to AI_CLIS -> red."""
    entry = _starter()
    assert entry["command"] == "ollama pull llama3.2"
    assert entry["agent_cli"] == "ollama"  # the pane is labelled as the CLI it runs
    assert ai_clis.launch_entry("  Ollama-Starter ") is entry  # the route's own normalising
    assert ai_clis.launch_entry("claude")["command"] == "claude"
    assert ai_clis.launch_entry("not-a-cli") is None
    ids = {c["id"] for c in ai_clis.AI_CLIS}
    assert "ollama-starter" not in ids
    assert {r["id"] for r in ai_clis.detect_ai_clis(probe=False)} == ids


def test_the_dashboard_asks_for_the_daemons_job_by_name():
    lib = _read(DASH / "lib" / "starterModel.ts")
    assert f'STARTER_MODEL_CLI = "{_starter()["id"]}"' in lib
    assert f'STARTER_MODEL = "{ai_clis.STARTER_MODEL}"' in lib
    assert "ollama pull" not in _code(lib), "the dashboard never sends a command"


def test_every_launch_menu_line_is_byte_identical():
    for entry in ai_clis.AI_CLIS:
        for shell in ("pwsh", "cmd", "bash", None):
            assert ai_clis.launch_line(entry, shell) == entry["command"], (entry["id"], shell)


def test_the_starter_line_is_plain_when_ollama_is_on_the_daemons_path(monkeypatch):
    monkeypatch.setattr(ai_clis.shutil, "which", lambda exe: f"/usr/bin/{exe}")
    assert ai_clis.launch_line(_starter(), "pwsh") == "ollama pull llama3.2"


#: Where Ollama's per-user installer puts it, with a space to prove the quoting.
_INSTALLED = r"C:\Users\Jo Doe\AppData\Local\Programs\Ollama\ollama.exe"


@pytest.mark.parametrize(
    "shell, line",
    [
        ("pwsh", "& '" + _INSTALLED + "' 'pull' 'llama3.2'"),
        ("powershell", "& '" + _INSTALLED + "' 'pull' 'llama3.2'"),
        ("cmd", '"' + _INSTALLED + '" pull llama3.2'),
    ],
)
def test_ollama_installed_after_iron_jarvis_started_is_typed_by_full_path(monkeypatch, shell, line):
    """The door's own step 1 installs Ollama while the app runs: the pane's
    shell inherits the daemon's older PATH and would say "not recognized".
    MUTATION: drop the full-path branch -> the plain command -> red."""
    from iron_jarvis.providers import cli_detect

    monkeypatch.setattr(ai_clis.shutil, "which", lambda exe: None)
    monkeypatch.setattr(cli_detect, "_ollama_binary", lambda: _INSTALLED)
    assert ai_clis.launch_line(_starter(), shell) == line


def test_with_ollama_nowhere_the_pane_still_runs_the_plain_command(monkeypatch):
    """The pane then says what the shell says; nothing is guessed."""
    from iron_jarvis.providers import cli_detect

    monkeypatch.setattr(ai_clis.shutil, "which", lambda exe: None)
    monkeypatch.setattr(cli_detect, "_ollama_binary", lambda: None)
    assert ai_clis.launch_line(_starter(), "pwsh") == "ollama pull llama3.2"


def test_the_launch_menu_route_never_offers_the_download(client):
    clis = {c["id"] for c in client.get("/terminals/ai-clis").json()["clis"]}
    assert "ollama-starter" not in clis
    assert "ollama" in clis


@pytest.mark.xfail(
    strict=True,
    reason=(
        "INTEGRATOR: daemon/routes/terminals.py launch_pane must look the id up with "
        "terminals.ai_clis.launch_entry, record agent_cli=cat.get('agent_cli') or cli_id, "
        "and type launch_line(cat, session.shell). Remove this marker when that edit lands."
    ),
)
def test_download_a_starter_model_opens_a_pane_running_the_download(client, monkeypatch):
    """The acceptance line, through the real route: one press, a NEW pane,
    the download typed AND started (Enter), no command for the user to copy."""
    monkeypatch.setattr(ai_clis.shutil, "which", lambda exe: f"/usr/bin/{exe}")
    r = client.post("/terminals/launch", json={"cli": "ollama-starter"})
    assert r.status_code == 200, r.text
    row = r.json()
    assert row["name"] == "Ollama starter model"
    assert row["agent_cli"] == "ollama"
    assert _backend_of(row["id"]).written == ["ollama pull llama3.2\r"]
