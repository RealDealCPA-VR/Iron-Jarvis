"""v1.256.0 — the reliability wave: R-01 storage, R-02 pack failures, R-03 model targets.

Each item exists because of something MEASURED on the live install, and each test
pins the behaviour that measurement demanded:

* **R-01.** 814 MB of state, of which ``artifacts/`` was 783 MB — 186 files, 57
  of them videos totalling 676 MB, the largest 71.6 MB, none newer than August.
  Nothing pruned it and no screen reported it. The clear MOVES rather than
  deletes, because the undo journal's two file kinds cannot reverse "remove
  bytes that already existed": ``file_restore`` would need a pre-image of the
  very bytes being freed and ``file_delete`` inverts to UNLINKING, which would
  destroy rather than restore. Recoverability is the move; freeing the disk is a
  second, deliberate press.
* **R-02.** ``brave_search`` failed at every boot for weeks with
  "FileNotFoundError: [WinError 2] The system cannot find the file specified".
  Accurate, recorded since v1.229.0, and it names nothing a person can do.
* **R-03.** ``routing_model`` was ``ollama:qwen3.6:27b`` while
  ``ollama_base_url`` was empty and nothing answered on Ollama's port, so the
  cheap router could never run and no surface said so.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.maintenance import (
    TRASH_DIRNAME,
    clear_media,
    media_candidates,
    purge_trash,
    storage_report,
)
from iron_jarvis.mcp.tools import classify_load_failure
from iron_jarvis.onboarding.doctor import CHECKS, check_model_targets

DAY = 86400.0


def _aged(path: Path, days: float, data: bytes = b"x" * 1000) -> Path:
    """A file that looks ``days`` old, so the cutoff is exercised, not simulated."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    when = time.time() - days * DAY
    os.utime(path, (when, when))
    return path


# --------------------------------------------------------------------------- #
# R-01 — what the app is keeping, and clearing it
# --------------------------------------------------------------------------- #
def test_the_report_names_every_category_and_only_media_is_clearable(tmp_path):
    home = tmp_path / "home"
    _aged(home / "artifacts" / "clip.mp4", 40)
    _aged(home / "creative-thumbs" / "t.png", 40, b"p" * 50)
    _aged(home / "backups" / "ironjarvis-backup-x.tar.gz", 40, b"b" * 200)
    _aged(home / "undo" / "pre.bin", 40, b"u" * 10)

    rep = storage_report(home)
    by_dir = {c["dir"]: c for c in rep["categories"]}

    # The two media folders are the ONLY clearable ones. Backups and undo
    # history are what a user needs when something has gone wrong.
    assert by_dir["artifacts"]["clearable"] is True
    assert by_dir["creative-thumbs"]["clearable"] is True
    assert by_dir["backups"]["clearable"] is False
    assert by_dir["undo"]["clearable"] is False
    # Every category is present even when empty, so the screen never implies a
    # folder does not exist just because it happens to be empty today.
    for wanted in ("artifacts", "creative-thumbs", "backups", "undo", "ocr", "codelab", TRASH_DIRNAME):
        assert wanted in by_dir, wanted
    assert by_dir["artifacts"]["bytes"] == 1000
    assert rep["total_bytes"] == sum(c["bytes"] for c in rep["categories"])


def test_the_dry_run_and_the_clear_agree_about_what_moves(tmp_path):
    """The confirm card is built from the dry run, so the number the user agrees
    to has to be the number that moves — one implementation, not an estimate."""
    home = tmp_path / "home"
    _aged(home / "artifacts" / "old.mp4", 40, b"v" * 5000)
    _aged(home / "artifacts" / "nested" / "older.mp4", 90, b"v" * 3000)
    fresh = _aged(home / "artifacts" / "today.png", 0, b"p" * 400)

    plan = media_candidates(home, 30)
    assert plan["files"] == 2
    assert plan["bytes"] == 8000
    # Largest first, so the confirm card leads with what actually matters.
    assert [f["rel"] for f in plan["largest"]] == ["artifacts/old.mp4", "artifacts/nested/older.mp4"]

    done = clear_media(home, 30)
    assert (done["moved"], done["bytes"]) == (plan["files"], plan["bytes"])
    assert fresh.exists(), "a file newer than the cutoff must never be touched"


def test_clearing_MOVES_so_the_press_is_recoverable(tmp_path):
    """THE DESIGN DECISION, pinned. The journal cannot reverse a bulk media
    delete, so the clear moves into <home>/trash/<stamp>/ keeping relative paths
    and NAMES what it moved. A future change that makes this unlink instead has
    silently removed the only way back."""
    home = tmp_path / "home"
    src = _aged(home / "artifacts" / "clips" / "render.mp4", 40, b"v" * 2048)

    res = clear_media(home, 30)

    assert res["moved"] == 1
    assert not src.exists(), "the file left the media folder"
    assert res["names"] == ["artifacts/clips/render.mp4"], "the manifest names it"
    recovered = list((home / TRASH_DIRNAME).rglob("render.mp4"))
    assert len(recovered) == 1, "it is still on disk, recoverable"
    assert recovered[0].read_bytes() == b"v" * 2048, "byte-for-byte, not a stub"
    # The relative path survives, so putting it back is a move rather than a guess.
    assert recovered[0].parent.name == "clips"


def test_the_trash_is_reported_as_its_own_category_so_space_is_not_claimed_freed(tmp_path):
    """A clear that moved 700 MB into the app's own folder has freed nothing
    yet. The report says so, and the second press is what frees it."""
    home = tmp_path / "home"
    _aged(home / "artifacts" / "big.mp4", 40, b"v" * 4096)

    clear_media(home, 30)
    after = {c["dir"]: c for c in storage_report(home)["categories"]}
    assert after["artifacts"]["bytes"] == 0
    assert after["trash"]["bytes"] == 4096, "still counted, because it is still on disk"

    gone = purge_trash(home)
    assert (gone["deleted"], gone["bytes"]) == (1, 4096)
    assert {c["dir"]: c for c in storage_report(home)["categories"]}["trash"]["bytes"] == 0


def test_the_clear_never_touches_backups_undo_or_code(tmp_path):
    home = tmp_path / "home"
    keep = [
        _aged(home / "backups" / "ironjarvis-backup-old.tar.gz", 400, b"b" * 100),
        _aged(home / "undo" / "ancient.bin", 400, b"u" * 100),
        _aged(home / "codelab" / "proj" / "main.py", 400, b"c" * 100),
        _aged(home / "ocr" / "cached.json", 400, b"o" * 100),
    ]
    _aged(home / "artifacts" / "old.mp4", 400, b"v" * 100)

    res = clear_media(home, 30)

    assert res["moved"] == 1, "only the media file, however old the others are"
    for p in keep:
        assert p.exists(), f"{p.name} must survive a media clear"


def test_the_routes_answer_and_the_unknown_action_names_the_new_ones(tmp_path):
    """Driven through the REAL app factory: a route that imports cleanly can
    still 500 on its first request, and this project has shipped an unreachable
    route before."""
    app = create_app(str(tmp_path))
    home = app.state.platform.config.home
    _aged(home / "artifacts" / "old.mp4", 40, b"v" * 7000)
    fresh = _aged(home / "artifacts" / "new.png", 0, b"p" * 70)

    with TestClient(app) as c:
        rep = c.get("/maintenance/storage")
        assert rep.status_code == 200, rep.text
        body = rep.json()
        assert body["candidates"]["files"] == 1
        assert body["older_than_days"] == 30

        done = c.post("/diagnostics/repair", json={"action": "clear_media", "older_than_days": 30})
        assert done.status_code == 200, done.text
        assert done.json()["moved"] == 1
        assert fresh.exists()

        purged = c.post("/diagnostics/repair", json={"action": "purge_trash"})
        assert purged.status_code == 200
        assert purged.json()["deleted"] == 1

        bad = c.post("/diagnostics/repair", json={"action": "nonsense"})
        assert bad.status_code == 400
        # The error lists what IS valid; a stale list here lies to the caller.
        assert "clear_media" in bad.json()["detail"]
        assert "purge_trash" in bad.json()["detail"]


# --------------------------------------------------------------------------- #
# R-02 — a pack that cannot start hands you the fix
# --------------------------------------------------------------------------- #
def test_a_missing_launcher_is_named_even_when_a_shell_can_find_it(monkeypatch):
    """THE BUG THIS PINS, and it is the one I shipped first. An earlier cut only
    named the launcher when `_find` ALSO came back empty — but a GUI-launched
    daemon inherits a different PATH than a shell, so on the machine this was
    written on `_find("npx")` succeeded while the pack still could not start,
    and the classifier fell through to a generic sentence in exactly the case it
    exists for. Whether some shell can find npx does not change that the pack
    failed; the probe may only choose the WORDING."""
    import iron_jarvis.terminals.ai_clis as ai_clis

    monkeypatch.setattr(ai_clis, "_find", lambda cmd: r"C:\nvm4w\nodejs\npx.cmd")
    v = classify_load_failure(
        {"command": "npx", "args": ["-y", "@brave/brave-search-mcp-server"]},
        "FileNotFoundError: [WinError 2] The system cannot find the file specified",
    )
    # MUTUALLY EXCLUSIVE, deliberately. An earlier version of this test
    # asserted only `"npx" in reason` and `"PATH" in reason` — which BOTH
    # branches satisfy, so reverting the fix left it green and it pinned
    # nothing at all. These are the halves that actually differ.
    assert "exists on this PC" in v["reason"], v["reason"]
    assert "isn't installed" not in v["reason"], "this is the found-but-invisible case"
    assert "Restart Iron Jarvis" in v["fix"], v["fix"]
    assert "nodejs.org" not in v["fix"], "reinstalling is not the remedy here"

    monkeypatch.setattr(ai_clis, "_find", lambda cmd: None)
    absent = classify_load_failure({"command": "npx"}, "FileNotFoundError: [WinError 2]")
    assert "isn't installed" in absent["reason"]
    assert "exists on this PC" not in absent["reason"]
    assert "nodejs.org" in absent["fix"], "name where to get it"
    assert "Restart Iron Jarvis" not in absent["fix"]


def test_each_failure_shape_gets_its_own_plain_sentence():
    cases = {
        "TimeoutError: did not respond within 15s": "didn't answer in time",
        "PermissionError: [WinError 5] Access is denied": "refused by the operating system",
        "HTTPStatusError: 401 Unauthorized": "refused our credentials",
        "ConnectError: connection refused": "couldn't be reached",
    }
    for raw, expect in cases.items():
        v = classify_load_failure({"command": "npx"}, raw)
        assert expect in v["reason"], f"{raw} -> {v['reason']}"
        assert v["fix"], raw


def test_an_unknown_failure_keeps_the_raw_text_and_offers_no_guess():
    """Never invent a cause. An unrecognised exception keeps its own words and
    an empty fix, because a confident wrong instruction is worse than none."""
    v = classify_load_failure({"command": "npx"}, "RuntimeError: something nobody predicted")
    assert v["reason"] == "RuntimeError: something nobody predicted"
    assert v["fix"] == ""
    # No error at all is not a failure to explain.
    assert classify_load_failure({"command": "npx"}, None) == {"reason": "", "fix": ""}


def test_the_load_record_carries_the_reason_and_fix_for_both_surfaces():
    """The classification lives on the record so the Tools row and the Overview
    hero read the SAME sentence instead of each interpreting the raw text."""
    from iron_jarvis.mcp import tools as mcp_tools

    mcp_tools._record_load(
        "brave_search",
        error="FileNotFoundError: [WinError 2] The system cannot find the file specified",
        tools_loaded=0,
        cfg={"command": "npx"},
    )
    rec = mcp_tools.load_status("brave_search")
    assert rec is not None
    assert rec["last_error"], "the raw text stays — a bug report needs it"
    assert rec["reason"] and rec["reason"] != rec["last_error"]
    assert "npx" in rec["reason"]
    # A clean load leaves no reason to show.
    mcp_tools._record_load("brave_search", error=None, tools_loaded=3, cfg={"command": "npx"})
    assert mcp_tools.load_status("brave_search")["reason"] == ""


# --------------------------------------------------------------------------- #
# R-03 — a setting that cannot work says so
# --------------------------------------------------------------------------- #
#: A config with nothing configured — every probe overrides only what it means to.
_BASE_CFG = {
    "routing_model": "",
    "default_provider": "auto",
    "ollama_base_url": "",
    "custom_base_url": "",
}


def _platform(health: list[dict], **cfg_kw):
    # ONE merged dict, not defaults-plus-kwargs: SimpleNamespace raises
    # "got multiple values for keyword argument" when an override repeats a
    # default, which is a harness bug that reads exactly like a code failure.
    return SimpleNamespace(
        config=SimpleNamespace(**{**_BASE_CFG, **cfg_kw}),
        providers=SimpleNamespace(health=lambda: health),
    )


def test_a_router_with_no_address_says_which_and_why(tmp_path):
    """The live shape this shipped for: routing_model=ollama:… with an empty
    ollama_base_url, which ProviderManager.available treats as unavailable."""
    r = check_model_targets(
        _platform(
            [{"provider": "ollama", "available": False}, {"provider": "custom", "available": True}],
            routing_model="ollama:qwen3.6:27b",
        )
    )
    assert r["ok"] is False
    assert "cheap router" in r["detail"]
    assert "ollama:qwen3.6:27b" in r["detail"]
    assert "no address is configured" in r["detail"]
    # The app's own rule, said out loud: it refuses rather than substituting, so
    # the user knows the setting is inert rather than quietly redirected.
    assert "refuses" in r["detail"]
    assert "Set an address" in r["fix"]


def test_an_address_that_is_set_but_dead_reads_differently(tmp_path):
    """Configured is not connected — and the fix differs, so the words must."""
    r = check_model_targets(
        _platform(
            [{"provider": "ollama", "available": False}],
            routing_model="ollama:qwen3.6:27b",
            ollama_base_url="http://127.0.0.1:11434",
        )
    )
    assert r["ok"] is False
    assert "not answering" in r["detail"]
    assert "no address is configured" not in r["detail"]


def test_a_reachable_or_unset_target_stays_quiet():
    """A check that cries wolf is a check people learn to ignore."""
    assert check_model_targets(
        _platform([{"provider": "custom", "available": True}], routing_model="custom:fleet")
    )["ok"] is True
    assert check_model_targets(_platform([{"provider": "custom", "available": True}]))["ok"] is True


def test_an_unreachable_default_provider_is_caught_too():
    r = check_model_targets(
        _platform([{"provider": "ollama", "available": False}], default_provider="ollama")
    )
    assert r["ok"] is False and "the default model" in r["detail"]
    # 'auto' and 'mock' are not targets anyone typed — they must not warn.
    assert check_model_targets(
        _platform([{"provider": "mock", "available": True}], default_provider="mock")
    )["ok"] is True


def test_the_check_never_raises_and_is_wired_into_the_runtime_checks():
    """A doctor check that is defined but never runs is a check that does not
    exist (the v1.239.0 lesson)."""
    import importlib

    # `from iron_jarvis.onboarding import doctor` binds the doctor FUNCTION —
    # the package re-exports it (onboarding/__init__.py) and it shadows the
    # module of the same name. Reaching the module needs import_module; this
    # trap has cost this project time twice.
    doctor_mod = importlib.import_module("iron_jarvis.onboarding.doctor")

    broken = SimpleNamespace(config=SimpleNamespace(), providers=SimpleNamespace())
    r = check_model_targets(broken)
    assert r["name"] == "model_targets" and r["ok"] is False

    src = Path(doctor_mod.__file__).read_text(encoding="utf-8")
    assert "checks.append(check_model_targets(platform))" in src, (
        "check_model_targets must be called from runtime_checks, not merely defined"
    )
    # It is a runtime check (needs a platform), so it is NOT in the static list.
    assert check_model_targets not in CHECKS
