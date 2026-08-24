"""v1.213: the Usage rollup counts Pi coding-agent sessions.

The user runs the Pi CLI (@earendil-works/pi-coding-agent) in Build terminals;
its tokens never cross the daemon's router, so /usage could not see them. Pi
records per-MESSAGE usage in ``~/.pi/agent/sessions/<folder>/<ts>_<uuid>.jsonl``
— assistant records carry ``message.usage`` with Pi's own token counts and
costs, plus ``message.provider``/``message.model``. Shapes and values here are
modeled on a real store (verified on the machine this was built on).

The merge rides the ONE usage view (``eval/usage_view.merged_usage``), so both
/usage and /fleet/usage see it — mirrored on the OpenCode fold (v1.94.0).
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.eval.pi_usage import pi_sessions_root, pi_usage
from iron_jarvis.eval.usage_view import is_local_provider, merged_usage

NOW_MS = int(time.time() * 1000)  # real-clock anchor: absolute constants age
DAY_MS = 86_400_000


def _iso(ts_ms: int) -> str:
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _fname(start_ms: int) -> str:
    dt = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)
    stem = dt.strftime("%Y-%m-%dT%H-%M-%S-") + f"{dt.microsecond // 1000:03d}Z"
    return f"{stem}_{uuid.uuid4()}.jsonl"


def _msg(ts_ms: int, provider: str, model: str, tin: int, tout: int, *,
         reasoning: int = 0, cache: int = 0, cost: float = 0.0) -> str:
    """One assistant record in the REAL on-disk shape."""
    return json.dumps({
        "type": "message", "id": "m1", "parentId": None, "timestamp": _iso(ts_ms),
        "message": {
            "role": "assistant", "provider": provider, "model": model,
            "usage": {
                "input": tin, "output": tout, "cacheRead": cache, "cacheWrite": 0,
                "reasoning": reasoning, "totalTokens": tin + tout + reasoning,
                "cost": {"input": 0, "output": 0, "cacheRead": 0,
                         "cacheWrite": 0, "total": cost},
            },
        },
    })


def _noise(ts_ms: int) -> list[str]:
    """Record types that carry no usage — present in every real session."""
    return [
        json.dumps({"type": "session", "id": "s", "timestamp": _iso(ts_ms)}),
        json.dumps({"type": "model_change", "timestamp": _iso(ts_ms),
                    "modelChange": {"model": "fleet"}}),
        json.dumps({"type": "custom", "timestamp": _iso(ts_ms)}),
        json.dumps({"type": "message", "timestamp": _iso(ts_ms),
                    "message": {"role": "user", "content": "hi"}}),
    ]


def _session(root: Path, folder: str, start_ms: int, lines: list[str],
             *, mtime_ms: "int | None" = None) -> Path:
    d = root / folder
    d.mkdir(parents=True, exist_ok=True)
    f = d / _fname(start_ms)
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if mtime_ms is not None:
        os.utime(f, (mtime_ms / 1000, mtime_ms / 1000))
    return f


def _tree(root: Path) -> Path:
    """Two working-folder dirs, three providers/models, two days, mixed record
    types — the shape of the real store on this machine."""
    d1 = NOW_MS - DAY_MS
    d2 = NOW_MS - 2 * DAY_MS
    _session(root, "--C--Users-VR-Projects-A--", d2, [
        *_noise(d2),
        _msg(d2, "local-models", "fleet", 3130, 144, reasoning=68),      # $0 local
        "this line is not json {{{",                                      # tolerated
        _msg(d1, "local-models", "fleet", 6181, 164, reasoning=12),
        _msg(d1, "openai-codex", "gpt-5.6-luna", 1344, 92, reasoning=39,
             cost=0.00948),                                               # hosted, paid
    ])
    _session(root, "--C--Users-VR-Projects-B--", d1, [
        *_noise(d1),
        _msg(d1, "local-models", "fleet", 3250, 105, reasoning=12, cache=7),
    ])
    # A session with NO usage-bearing messages (real: an aborted run) — not a run.
    _session(root, "--C--Users-VR-Projects-B--", d1 + 60_000, _noise(d1))
    return root


# --- the reader --------------------------------------------------------------


def test_reads_real_store_shapes(tmp_path):
    out = pi_usage(_tree(tmp_path), 30, now_ms=NOW_MS)
    assert out["available"] is True
    # runs = sessions with >=1 usage-bearing message; the noise-only one is not.
    assert out["totals"]["runs"] == 2
    assert out["totals"]["input_tokens"] == 3130 + 6181 + 1344 + 3250
    # Reasoning tokens are GENERATED — they count as output (OpenCode parity).
    assert out["totals"]["output_tokens"] == (144 + 68) + (164 + 12) + (92 + 39) + (105 + 12)
    assert out["totals"]["cache_tokens"] == 7
    assert out["totals"]["cost_usd"] == pytest.approx(0.00948)

    rows = {(r["provider"], r["model"]): r for r in out["by_model"]}
    fleet = rows[("pi/local-models", "fleet")]
    assert fleet["input_tokens"] == 3130 + 6181 + 3250
    assert fleet["runs"] == 2          # the model spoke in two sessions
    assert fleet["cost_usd"] == 0.0
    hosted = rows[("pi/openai-codex", "gpt-5.6-luna")]
    assert hosted["runs"] == 1
    assert hosted["cost_usd"] == pytest.approx(0.00948)

    assert len(out["by_day"]) == 2     # per-MESSAGE day bucketing (UTC)
    days = {r["day"]: r for r in out["by_day"]}
    d2_key = datetime.fromtimestamp((NOW_MS - 2 * DAY_MS) / 1000,
                                    tz=timezone.utc).strftime("%Y-%m-%d")
    assert days[d2_key]["input_tokens"] == 3130
    assert out["by_day"] == sorted(out["by_day"], key=lambda r: r["day"])
    assert "Pi's own" in out["note"]   # the attribution caveat is named


def test_days_window_skips_an_out_of_window_file(tmp_path):
    old = NOW_MS - 45 * DAY_MS
    _tree(tmp_path)
    _session(tmp_path, "--C--Users-VR-Projects-A--", old,
             [_msg(old, "local-models", "fleet", 9_999_999, 9_999)],
             mtime_ms=old)             # filename AND mtime outside the window
    out = pi_usage(tmp_path, 30, now_ms=NOW_MS)
    assert out["totals"]["input_tokens"] == 3130 + 6181 + 1344 + 3250
    assert out["totals"]["runs"] == 2


def test_an_old_message_inside_a_still_open_session_is_windowed_out(tmp_path):
    """A session started 40 days ago with activity yesterday: the file is in
    scope (mtime), but only the in-window MESSAGES count — finer than the
    OpenCode fold can be, so use the grain we have."""
    start = NOW_MS - 40 * DAY_MS
    _session(tmp_path, "--C--x--", start, [
        _msg(start, "local-models", "fleet", 1_000_000, 1),      # 40d ago
        _msg(NOW_MS - DAY_MS, "local-models", "fleet", 500, 5),  # yesterday
    ])
    out = pi_usage(tmp_path, 30, now_ms=NOW_MS)
    assert out["totals"]["input_tokens"] == 500


def test_unparseable_lines_and_alien_records_are_tolerated(tmp_path):
    _session(tmp_path, "--C--x--", NOW_MS - DAY_MS, [
        "garbage",
        '{"type": "message"}',                       # message with no body
        '{"type": "message", "message": {"usage": "not-a-dict"}}',
        json.dumps({"type": "message", "timestamp": "not-a-time",
                    "message": {"provider": "local-models", "model": "fleet",
                                "usage": {"input": 10, "output": 1,
                                          "cost": {"total": 0}}}}),
        _msg(NOW_MS - DAY_MS, "local-models", "fleet", 100, 10),
    ])
    out = pi_usage(tmp_path, 30, now_ms=NOW_MS)
    assert out["available"] is True
    # Both usage rows count — the timestampless one lands on the file's day.
    assert out["totals"]["input_tokens"] == 110


def test_missing_root_degrades_honestly(tmp_path):
    out = pi_usage(tmp_path / "ghost" / "sessions", 30, now_ms=NOW_MS)
    assert out["available"] is False
    assert "not found" in out["note"]
    assert out["totals"]["input_tokens"] == 0


def test_locator_honours_config_override():
    class _Cfg:
        pi_sessions_dir = r"C:\somewhere\else"
    assert pi_sessions_root(_Cfg) == Path(r"C:\somewhere\else")
    _Cfg.pi_sessions_dir = ""
    default = pi_sessions_root(_Cfg)
    assert default.parts[-3:] == (".pi", "agent", "sessions")


# --- classification ----------------------------------------------------------


def test_pi_local_models_counts_as_local():
    assert is_local_provider("pi/local-models") is True
    assert is_local_provider("pi/lmstudio") is True     # unrecognised = local
    assert is_local_provider("Pi/Local-Models") is True  # case-insensitive


def test_pi_hosted_vendors_never_count_as_local():
    """THE SAFETY-CRITICAL CASE (same as opencode/*): Pi reaches hosted models,
    and Pi composes ids from the vendor — 'openai-codex' must prefix-match
    'openai'. Counting that spend as local would price real money as avoided."""
    assert is_local_provider("pi/openai-codex") is False
    assert is_local_provider("pi/anthropic") is False
    assert is_local_provider("pi/google-gemini") is False


# --- the fold ----------------------------------------------------------------


class _Obs:
    def usage_summary(self, days):
        return {
            "since_days": days,
            "totals": {"input_tokens": 100, "output_tokens": 10,
                       "cost_usd": 0.0, "runs": 2},
            "by_day": [],
            "by_model": [{"provider": "custom", "model": "brain",
                          "input_tokens": 100, "output_tokens": 10,
                          "cost_usd": 0.0, "runs": 2}],
        }


class _Cfg:
    opencode_data_dir = ""

    def __init__(self, pi_dir: str):
        self.pi_sessions_dir = pi_dir


class _Platform:
    def __init__(self, pi_dir: str):
        self.observability = _Obs()
        self.config = _Cfg(pi_dir)


def test_merged_usage_folds_pi(tmp_path):
    """THE HEADLINE: the fold goes through the ONE view, so /usage and
    /fleet/usage both see Pi without either knowing how it is read."""
    _tree(tmp_path)
    out = merged_usage(_Platform(str(tmp_path)), 30)
    assert out["pi"]["available"] is True
    assert out["totals"]["input_tokens"] == 100 + 3130 + 6181 + 1344 + 3250
    assert out["totals"]["runs"] == 2 + 2
    assert out["totals"]["cost_usd"] == pytest.approx(0.00948)
    provs = {r["provider"] for r in out["by_model"]}
    assert {"custom", "pi/local-models", "pi/openai-codex"} <= provs
    assert len(out["by_day"]) == 2     # pi days flow into the merged series


def test_merge_never_raises_when_pi_is_unreadable(tmp_path, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("disk error")

    monkeypatch.setattr("iron_jarvis.eval.pi_usage.pi_usage", _boom)
    out = merged_usage(_Platform(str(tmp_path)), 30)
    assert out["pi"]["available"] is False
    assert out["totals"]["input_tokens"] == 100  # untouched


def test_absent_pi_leaves_the_rollup_alone(tmp_path):
    out = merged_usage(_Platform(str(tmp_path / "ghost")), 30)
    assert out["pi"]["available"] is False
    assert out["totals"]["input_tokens"] == 100


# --- both routes serve the block ---------------------------------------------


def test_usage_endpoint_merges_pi_totals(tmp_path):
    root = tmp_path / "pisessions"
    _tree(root)
    client = TestClient(create_app(str(tmp_path)))
    client.app.state.platform.config.pi_sessions_dir = str(root)
    out = client.get("/usage").json()
    assert out["pi"]["available"] is True
    assert out["totals"]["input_tokens"] >= 3130 + 6181 + 1344 + 3250
    assert any(r["provider"] == "pi/local-models" and r["model"] == "fleet"
               for r in out["by_model"])
    assert any(r["input_tokens"] >= 3130 for r in out["by_day"])


def test_fleet_usage_attributes_pi_local_and_hosted_correctly(tmp_path):
    """Local Pi work is avoided spend; pi/openai-codex is REAL spend and must
    land in the cloud bucket. And a Pi row is an external SOURCE — not a fleet
    node the user deleted, so no '(removed)' invention."""
    root = tmp_path / "pisessions"
    _tree(root)
    client = TestClient(create_app(str(tmp_path)))
    client.app.state.platform.config.pi_sessions_dir = str(root)
    out = client.get("/fleet/usage").json()
    assert out["local_tokens"] == (3130 + 6181 + 3250) + (144 + 68 + 164 + 12 + 105 + 12)
    assert out["cloud_tokens"] == 1344 + 92 + 39
    assert out["cloud_cost_usd"] == pytest.approx(0.00948)
    row = next(r for r in out["by_node"] if r["node_id"] == "pi/local-models")
    assert row["external"] is True
    assert row["retired"] is False
    assert row["label"] == "Pi · local-models"
    assert "removed" not in row["label"].lower()


def test_usage_endpoint_survives_missing_pi_store(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    client.app.state.platform.config.pi_sessions_dir = str(tmp_path / "ghost")
    out = client.get("/usage").json()
    assert out["pi"]["available"] is False
    assert "totals" in out and "by_model" in out  # the page still renders
