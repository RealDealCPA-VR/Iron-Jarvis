"""v1.438.0 (Wave 4 group C, decision D5) — the independent review's pins for
the desktop shell's two switches. The shipped source is LIFTED from main.js
(the builder's harness, ``tests/test_desktop_start_settings_v14380.py``) and
run under node.

* An UNDO back to "not set" puts back what the switch was before the setting
  turned it on: "start with Windows" turned on from chat, then Undo, left the
  app starting with Windows (the shell saw "not set" and kept the login item).
  A tray press made in between is the newer choice: Undo then changes nothing.
* Two quick tray presses reach the daemon in press order (one write at a
  time), so the setting ends as the tray ends.
* The config.toml reader is linear: a top-level line with a long run of
  spaces took 1.4 s per check on the main process (every 15 seconds).
"""

from __future__ import annotations

import json
import subprocess
import time

from tests.test_desktop_start_settings_v14380 import _harness, _run, requires_node


def _run_paced(tmp_path, scenario: dict) -> dict:
    """The builder's harness, with two knobs it lacks: a step may skip the
    wait after it (``noTick``), and each PUT answers after its own delay
    (``putDelays``, ms) — so a first write can still be out when a second
    press comes."""
    src = _harness()
    a = "    await tick();\n    out.steps.push("
    b = "        }, 1);\n"
    assert a in src and b in src, "the harness changed shape"
    src = src.replace(a, "    if (!step.noTick) await tick();\n    out.steps.push(")
    src = src.replace(b, "        }, (scenario.putDelays || []).length ? scenario.putDelays.shift() : 1);\n")
    src = src.replace("(async () => {", "(async () => {\n  const done = () => new Promise((r) => setTimeout(r, 400));", 1)
    src = src.replace("  out.loginSet = loginSet;", "  await done();\n  out.loginSet = loginSet;")
    home = tmp_path / "home"
    user = tmp_path / "userData"
    home.mkdir(parents=True, exist_ok=True)
    user.mkdir(parents=True, exist_ok=True)
    script = tmp_path / "paced.js"
    script.write_text(src, encoding="utf-8")
    full = {"home": str(home), "userData": str(user), **scenario}
    proc = subprocess.run(["node", str(script), json.dumps(full)], capture_output=True, text=True,
                          encoding="utf-8", timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


@requires_node
def test_an_undo_back_to_not_set_turns_start_with_windows_off_again(tmp_path):
    out = _run(tmp_path, {"daemonUp": True, "loginOn": False, "steps": [
        {"sync": True},                                        # not set, no login item: nothing
        {"config": "start_with_windows = true\n", "sync": True},  # chat turned it on
        {"config": "", "sync": True},                          # chat's Undo: back to not set
        {"sync": True},
    ]})
    _, on, undone, later = out["steps"]
    assert on["loginOn"] is True
    assert undone["loginOn"] is False, "Undo must turn Start with Windows off again"
    assert later["loginOn"] is False and out["puts"] == [], "nothing written back to the setting"


@requires_node
def test_an_undo_back_to_not_set_puts_back_ask_when_closing(tmp_path):
    out = _run(tmp_path, {"daemonUp": True, "steps": [
        {"config": "keep_running_in_background = true\n", "sync": True},
        {"config": "", "sync": True},
    ]})
    on, undone = out["steps"]
    assert on["keepRunningPref"] is True
    assert undone["keepRunningPref"] is None, "not set again: the close question asks, as before"


@requires_node
def test_a_tray_press_since_is_never_undone_by_a_later_undo_to_not_set(tmp_path):
    out = _run(tmp_path, {"daemonUp": True, "writeConfigOnPut": True, "steps": [
        {"config": "start_with_windows = true\n", "sync": True},  # from chat
        {"tray": "login", "value": True},                        # the user's own choice (same value)
        {"sync": True},
        {"config": "", "sync": True},                            # an Undo of the chat change
    ]})
    assert out["steps"][-1]["loginOn"] is True, "the tray press is the newer choice"


@requires_node
def test_two_quick_tray_presses_reach_the_daemon_in_order(tmp_path):
    # The first write answers slowly (200 ms), the second at once: sent in
    # parallel the second lands FIRST and the daemon ends on "on".
    out = _run_paced(tmp_path, {"daemonUp": True, "writeConfigOnPut": True, "putDelays": [200, 1], "steps": [
        {"tray": "keep", "value": True, "noTick": True},
        {"tray": "keep", "value": False},
    ]})
    sent = [p["body"]["values"]["keep_running_in_background"] for p in out["puts"]]
    assert sent == [True, False]
    # The second write starts only after the first answered.
    assert out["steps"][1]["puts"] == 1, out
    config = (tmp_path / "home" / "config.toml").read_text(encoding="utf-8")
    assert "keep_running_in_background = false" in config, config


@requires_node
def test_the_toml_reader_is_linear_on_a_long_line(tmp_path):
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text(
        "system_note = x" + " " * 150000 + "y\nstart_with_windows = true\n", encoding="utf-8"
    )
    t = time.perf_counter()
    out = _run(tmp_path, {"pure": {"settings": [], "decisions": []}})
    took = time.perf_counter() - t
    assert out["pure"]["layered"]["start_with_windows"] is True
    # The old pattern spent over a second on that one line; node's own start
    # is a fraction of that. Generous: a hang detector, not a speed bar.
    assert took < 8, took
