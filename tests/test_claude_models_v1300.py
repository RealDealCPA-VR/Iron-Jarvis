"""v1.300.0 — the Claude subscription's LIVE model picker and exact --model routing.

``providers/claude_models.py`` reads the account's own picker from the
``claude`` CLI's stream-json ``initialize`` handshake (no model call), maps
each row to our canonical id, appends ``[1m]`` for 1M models, refuses ``[1m]``
on Haiku 4.5, caches for 10 minutes in-process and on disk, and falls back to
the pinned table WITH a reason. ``GET /models`` serves it for ``claude-cli``
and for a keyless ``anthropic`` that inherits the CLI, without ever blocking.

Offline: the subprocess chokepoint ``_run_handshake`` is replaced by canned
control_response JSON shaped exactly like claude 2.1.288's answer.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
import types

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers import claude_models as cm
from iron_jarvis.providers.manager import ProviderManager

# --------------------------------------------------------------------------- #
# canned handshake answers
# --------------------------------------------------------------------------- #

ACCOUNT_MAX = {"email": "user@example.test", "organization": "Org", "subscriptionType": "Claude Max",
               "apiProvider": "firstParty"}
ACCOUNT_PRO = dict(ACCOUNT_MAX, subscriptionType="Claude Pro")

#: Today's picker on the user's account (claude 2.1.288), verbatim in shape.
LIVE_ROWS = [
    {"value": "default", "resolvedModel": "claude-opus-5-5", "displayName": "Default (recommended)",
     "description": "Opus 5.5 · Best for everyday, complex tasks"},
    {"value": "opus", "resolvedModel": "claude-opus-5-5", "displayName": "Opus",
     "description": "Opus 5.5 · Best for everyday, complex tasks"},
    {"value": "claude-fable-5-1[1m]", "resolvedModel": "claude-fable-5-1", "displayName": "Fable",
     "description": "Fable 5.1 · Most capable for your hardest and longest-running tasks"},
    {"value": "sonnet", "resolvedModel": "claude-sonnet-5-5", "displayName": "Sonnet",
     "description": "Sonnet 5.5 · Efficient for routine tasks"},
    {"value": "haiku", "resolvedModel": "claude-haiku-4-5-20251001", "displayName": "Haiku",
     "description": "Haiku 4.5 · Fastest for quick answers"},
]


def _stdout(models, account=ACCOUNT_MAX) -> str:
    lines = [
        json.dumps({"type": "system", "subtype": "noise"}),
        "not json at all",
        json.dumps({
            "type": "control_response",
            "response": {"subtype": "success", "request_id": "ij-picker",
                         "response": {"models": models, "account": account, "commands": []}},
        }),
    ]
    return "\n".join(lines) + "\n"


class FakeCLI:
    """Counts handshakes; answers canned stdout (or raises)."""

    def __init__(self, stdout: str = "", exc: BaseException | None = None) -> None:
        self.stdout = stdout
        self.exc = exc
        self.calls = 0
        self.gate: threading.Event | None = None

    def __call__(self, timeout: float) -> str:
        self.calls += 1
        if self.gate is not None:
            self.gate.wait(5)
        if self.exc is not None:
            raise self.exc
        return self.stdout


@pytest.fixture(autouse=True)
def _clean_module():
    cm.reset()
    cm.set_home(None)
    yield
    deadline = time.monotonic() + 5
    while cm._refreshing and time.monotonic() < deadline:  # let a background refresh land
        time.sleep(0.01)
    cm.reset()
    cm.set_home(None)


@pytest.fixture
def fake(monkeypatch):
    f = FakeCLI(_stdout(LIVE_ROWS))
    monkeypatch.setattr(cm, "_run_handshake", f)
    return f


def _by_id(data):
    return {m["id"]: m for m in data["models"]}


# --------------------------------------------------------------------------- #
# parsing: rows -> our ids
# --------------------------------------------------------------------------- #


def test_live_rows_map_to_canonical_ids_and_default_collapses(fake):
    data = cm.discover()
    assert data["error"] is None
    assert data["subscription"] == "Claude Max"
    rows = _by_id(data)
    # `default` and `opus` are ONE model: one row, the real picker value kept.
    assert list(rows) == ["claude-opus-5-5", "claude-fable-5-1", "claude-sonnet-5-5",
                          "claude-haiku-4-5-20251001"]
    assert rows["claude-opus-5-5"]["value"] == "opus"
    assert rows["claude-opus-5-5"]["default"] is True
    assert rows["claude-sonnet-5-5"]["default"] is False
    # Labels from the description ("Sonnet 5.5"), never the bare displayName.
    assert rows["claude-sonnet-5-5"]["label"] == "Sonnet 5.5"
    assert rows["claude-opus-5-5"]["label"] == "Opus 5.5"
    # A full-id row keeps its picker value verbatim.
    assert rows["claude-fable-5-1"]["value"] == "claude-fable-5-1[1m]"
    assert all(r["pinned"] for r in rows.values())


def test_alias_rows_without_resolved_model_map_via_the_description(fake):
    # An older CLI answers alias rows with no `resolvedModel`: the family +
    # version in the description names the model.
    fake.stdout = _stdout([
        {"value": "sonnet", "displayName": "Sonnet", "description": "Sonnet 5.5 · Efficient"},
        {"value": "haiku", "displayName": "Haiku", "description": "Haiku 4.5 · Fastest"},
        {"value": "best", "displayName": "Best", "description": "Whatever is best"},  # unnameable
        {"value": "claude-opus-4-8", "displayName": "Opus 4.8", "description": ""},
    ])
    rows = _by_id(cm.discover())
    assert list(rows) == ["claude-sonnet-5-5", "claude-haiku-4-5-20251001", "claude-opus-4-8"]
    assert rows["claude-sonnet-5-5"]["native"] == "claude-sonnet-5-5[1m]"
    assert rows["claude-haiku-4-5-20251001"]["native"] == "claude-haiku-4-5-20251001"
    assert rows["claude-opus-4-8"]["label"] == "Opus 4.8"


def test_windows_and_native_on_rows(fake):
    rows = _by_id(cm.discover())
    assert rows["claude-sonnet-5-5"]["context_window"] == 1_000_000
    assert rows["claude-sonnet-5-5"]["native"] == "claude-sonnet-5-5[1m]"
    assert rows["claude-haiku-4-5-20251001"]["context_window"] == 200_000
    assert rows["claude-haiku-4-5-20251001"]["native"] == "claude-haiku-4-5-20251001"


def test_usage_credits_from_description_and_the_fable_plan_rule(fake):
    rows = list(LIVE_ROWS)
    rows[1] = dict(rows[1], description="Opus 5.5 · Draws from usage credits")
    fake.stdout = _stdout(rows, ACCOUNT_PRO)
    pro = _by_id(cm.discover(force=True))
    assert pro["claude-opus-5-5"]["usage_credits"] is True  # the picker said so
    assert pro["claude-fable-5-1"]["usage_credits"] is True  # Fable on a non-Max plan
    assert pro["claude-sonnet-5-5"]["usage_credits"] is False
    fake.stdout = _stdout(LIVE_ROWS, ACCOUNT_MAX)
    mx = _by_id(cm.discover(force=True))
    assert mx["claude-fable-5-1"]["usage_credits"] is False  # Max includes Fable
    assert not any(r["usage_credits"] for r in mx.values())


# --------------------------------------------------------------------------- #
# routing
# --------------------------------------------------------------------------- #


def test_native_model_pinned_table_with_no_discovery():
    # Nothing discovered (no CLI touched): the pinned table routes.
    assert cm.native_model("sonnet") == "claude-sonnet-5-5[1m]"
    assert cm.native_model("claude-opus-5-5") == "claude-opus-5-5[1m]"
    assert cm.native_model("claude-opus-4-8[1m]") == "claude-opus-4-8[1m]"
    assert cm.native_model("claude-sonnet-5") == "claude-sonnet-5[1m]"
    assert cm.native_model("fable") == "claude-fable-5-1[1m]"
    assert cm.native_model("haiku") == "claude-haiku-4-5-20251001"
    assert cm.native_model("claude-haiku-4-5") == "claude-haiku-4-5-20251001"
    for default in ("subscription", "default", "", "  "):
        assert cm.native_model(default) is None
        assert cm.context_window(default) is None
    assert cm.context_window("haiku") == 200_000
    assert cm.context_window("claude-sonnet-5-5[1m]") == 1_000_000


def test_haiku_with_1m_is_refused_with_a_sentence(fake):
    cm.discover()
    for ask in ("haiku[1m]", "claude-haiku-4-5-20251001[1m]", "claude-haiku-4-5[1m]"):
        with pytest.raises(ValueError) as exc:
            cm.native_model(ask)
        assert "Haiku 4.5" in str(exc.value) and "200K" in str(exc.value)


def test_unknown_ids_pass_through_unchanged_and_are_never_promised_1m():
    assert cm.native_model("claude-mystery-9") == "claude-mystery-9"
    assert cm.native_model("claude-mystery-9[1m]") == "claude-mystery-9[1m]"
    assert cm.native_model("gpt-5.5") == "gpt-5.5"
    assert cm.context_window("claude-mystery-9") is None
    assert cm.context_window("claude-mystery-9[1m]") is None


def test_routing_follows_the_live_picker_not_the_pinned_alias(fake):
    # The account moved `sonnet` to a model the table does not pin yet.
    fake.stdout = _stdout([
        {"value": "sonnet", "resolvedModel": "claude-sonnet-6", "displayName": "Sonnet",
         "description": "Sonnet 6 · New"},
        {"value": "claude-opus-6[1m]", "resolvedModel": "claude-opus-6", "displayName": "Opus",
         "description": "Opus 6 · New"},
    ])
    rows = _by_id(cm.discover())
    assert rows["claude-sonnet-6"]["pinned"] is False
    assert rows["claude-sonnet-6"]["context_window"] is None  # unknown: not promised 1M
    assert cm.native_model("sonnet") == "claude-sonnet-6"
    # 1M only because the CLI itself offered the [1m] form.
    assert rows["claude-opus-6"]["context_window"] == 1_000_000
    assert cm.native_model("claude-opus-6") == "claude-opus-6[1m]"
    # A pinned id the picker no longer lists still routes by the table.
    assert cm.native_model("claude-opus-4-8") == "claude-opus-4-8[1m]"


# --------------------------------------------------------------------------- #
# cache
# --------------------------------------------------------------------------- #


def test_one_handshake_per_ten_minutes_and_force_refreshes(fake, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(cm, "time", types.SimpleNamespace(monotonic=lambda: clock[0], sleep=time.sleep))
    cm.discover()
    cm.discover()
    clock[0] += 599
    cm.discover()
    assert fake.calls == 1
    cm.discover(force=True)
    assert fake.calls == 2
    clock[0] += 601
    cm.discover()
    assert fake.calls == 3


def test_disk_cache_answers_a_cold_start_without_a_subprocess(fake, tmp_path):
    cm.set_home(tmp_path)
    cm.discover()
    assert fake.calls == 1
    on_disk = json.loads((tmp_path / cm.CACHE_FILE).read_text(encoding="utf-8"))
    assert on_disk["error"] is None and _by_id(on_disk)["claude-sonnet-5-5"]
    cm.reset()  # a daemon restart
    served = cm.catalog(refresh=False)
    assert fake.calls == 1
    assert served["error"] is None
    assert "claude-sonnet-5-5" in _by_id(served)
    cm.reset()
    assert cm.discover()["error"] is None  # fresh on disk: no handshake either
    assert fake.calls == 1
    cm.reset()
    assert cm.native_model("sonnet") == "claude-sonnet-5-5[1m]"
    assert fake.calls == 1


@pytest.mark.parametrize(
    "exc, words",
    [
        (FileNotFoundError("the claude CLI is not installed (no `claude` on PATH)"), "not installed"),
        (subprocess.TimeoutExpired(["claude"], 20), "within 20s"),
    ],
)
def test_failure_answers_the_pinned_catalog_with_the_reason(monkeypatch, tmp_path, exc, words):
    cm.set_home(tmp_path)
    monkeypatch.setattr(cm, "_run_handshake", FakeCLI(exc=exc))
    data = cm.discover()
    assert words in data["error"]
    assert list(_by_id(data)) == list(cm.PINNED)
    assert all(r["pinned"] for r in data["models"])
    assert not (tmp_path / cm.CACHE_FILE).exists()  # a fallback is never persisted


def test_logged_out_or_garbled_handshake_is_not_the_accounts_picker(fake):
    fake.stdout = _stdout(LIVE_ROWS, account={})
    data = cm.discover(force=True)
    assert "not signed in" in data["error"]
    fake.stdout = "nothing useful\n"
    data = cm.discover(force=True)
    assert "did not answer" in data["error"]
    fake.stdout = _stdout([])
    assert "empty" in cm.discover(force=True)["error"]


def test_catalog_never_blocks_and_refreshes_once_behind_it(fake):
    fake.gate = threading.Event()  # the handshake hangs until released
    t0 = time.monotonic()
    first = cm.catalog(refresh=True)
    second = cm.catalog(refresh=True)
    assert time.monotonic() - t0 < 1.0
    assert first["error"] and "not discovered yet" in first["error"]
    assert second["error"]
    fake.gate.set()
    deadline = time.monotonic() + 5
    while cm._refreshing and time.monotonic() < deadline:
        time.sleep(0.01)
    assert fake.calls == 1  # single-flight: two reads, one handshake
    assert cm.catalog(refresh=True)["error"] is None
    assert fake.calls == 1


# --------------------------------------------------------------------------- #
# GET /models — the real route
# --------------------------------------------------------------------------- #


@pytest.fixture
def claude_cli_only(monkeypatch):
    monkeypatch.setattr(ProviderManager, "_cli_binary_present", staticmethod(lambda b: b == "claude"))


def _rows(models, provider):
    return [m for m in models if m["provider"] == provider]


def test_models_route_lists_the_live_picker_for_claude_cli_and_inherited_anthropic(
    tmp_path, claude_cli_only, fake
):
    cm.discover()  # primed (the route itself never waits for a handshake)
    client = TestClient(create_app(str(tmp_path)))
    models = client.get("/models").json()["models"]
    cli = _rows(models, "claude-cli")
    assert cli[0]["model"] == "subscription"  # the CLI's default stays first
    live = cli[1:]
    assert [m["id"] for m in live] == ["claude-opus-5-5", "claude-fable-5-1", "claude-sonnet-5-5",
                                       "claude-haiku-4-5-20251001"]
    sonnet = next(m for m in live if m["id"] == "claude-sonnet-5-5")
    assert sonnet["model"] == "claude-sonnet-5-5"
    assert sonnet["label"] == "Sonnet 5.5"
    assert sonnet["context_window"] == 1_000_000
    assert sonnet["native"] == "claude-sonnet-5-5[1m]"
    assert sonnet["usage_credits"] is False
    assert sonnet["available"] is True and sonnet["kind"] == "cli"
    haiku = next(m for m in live if m["id"] == "claude-haiku-4-5-20251001")
    assert haiku["context_window"] == 200_000 and haiku["native"] == "claude-haiku-4-5-20251001"
    # A keyless anthropic is SERVED by claude-cli: the very same rows FIRST,
    # then the curated ids the catalog does not cover (review fix — see
    # tests/test_claude_catalog_rows_v1300.py).
    anth = _rows(models, "anthropic")
    head = anth[: len(live)]
    assert [m["id"] for m in head] == [m["id"] for m in live]
    assert all(m["inherited_from"] == "claude-cli" for m in anth)
    assert [m["native"] for m in head] == [m["native"] for m in live]
    assert [m["model"] for m in anth[len(live):]] == [
        "claude-opus-4-8", "claude-sonnet-4-6", "claude-fable-5"]
    assert fake.calls == 1


def test_models_route_with_unknown_sign_in_serves_pinned_and_spawns_nothing(
    tmp_path, claude_cli_only, fake
):
    # conftest stubs _cli_signed_in -> None (unknown): no handshake may run.
    client = TestClient(create_app(str(tmp_path)))
    models = client.get("/models").json()["models"]
    ids = [m["id"] for m in _rows(models, "claude-cli")[1:]]
    assert ids == list(cm.PINNED)
    time.sleep(0.05)
    assert fake.calls == 0


def test_models_route_refreshes_in_the_background_when_signed_in(
    tmp_path, claude_cli_only, fake, monkeypatch
):
    monkeypatch.setattr(ProviderManager, "_cli_signed_in", staticmethod(lambda b: True))
    client = TestClient(create_app(str(tmp_path)))
    client.get("/models")
    deadline = time.monotonic() + 5
    while (cm._refreshing or fake.calls == 0) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert fake.calls == 1
    models = client.get("/models").json()["models"]
    assert [m["id"] for m in _rows(models, "claude-cli")[1:]][0] == "claude-opus-5-5"
    assert (tmp_path / ".ironjarvis" / cm.CACHE_FILE).exists() or any(
        p.name == cm.CACHE_FILE for p in tmp_path.rglob(cm.CACHE_FILE)
    )
    assert fake.calls == 1


# --------------------------------------------------------------------------- #
# the CLI's default, named exactly (follow-up)
# --------------------------------------------------------------------------- #


def test_subscription_routes_to_the_live_default_row_exactly(fake):
    cm.discover()
    for default in ("subscription", "default", ""):
        assert cm.native_model(default) == "claude-opus-5-5[1m]"
        assert cm.context_window(default) == 1_000_000
    # The account's default moves to a 200K model: no [1m], its own window.
    rows = [dict(LIVE_ROWS[0], resolvedModel="claude-haiku-4-5-20251001",
                 description="Haiku 4.5 · Fastest")] + LIVE_ROWS[1:]
    fake.stdout = _stdout(rows)
    cm.discover(force=True)
    assert cm.native_model("subscription") == "claude-haiku-4-5-20251001"
    assert cm.context_window("subscription") == 200_000


def test_subscription_with_no_known_default_sends_no_model(fake):
    # Pinned fallback (nothing discovered) never guesses a default.
    assert cm.native_model("subscription") is None
    assert cm.context_window("subscription") is None
    fake.exc = FileNotFoundError("no claude")
    assert cm.discover(force=True)["error"]
    assert cm.native_model("subscription") is None
    assert cm.context_window("default") is None
    # A live picker with no `default` row names none either.
    fake.exc = None
    fake.stdout = _stdout(LIVE_ROWS[1:])
    assert cm.discover(force=True)["error"] is None
    assert cm.native_model("subscription") is None


def test_child_env_is_the_shared_guard():
    base = {"PATH": "x", "ANTHROPIC_API_KEY": "sk", "ANTHROPIC_BASE_URL": "http://127.0.0.1:1",
            "ANTHROPIC_MODEL": "m", "CLAUDE_CODE_USE_BEDROCK": "1", "CLAUDE_CODE_EXTRA_BODY": "{}"}
    env = cm.child_env(base)
    assert env == {"PATH": "x", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}
    assert base["ANTHROPIC_API_KEY"] == "sk"  # the caller's dict is untouched
