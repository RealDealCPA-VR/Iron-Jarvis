"""Start at sign-in on Linux, proven against the parsers a Linux desktop really uses.

Fourth review, group 31 (LX4). The autostart entry the shell writes names the
program in ``Exec=``; the desktop-entry rules read that value through three
layers (the string escape, the Exec quoting, ``%`` field codes). Reading the
spec is not enough. ``scripts/linux_autostart_samples.mjs --out <dir>`` writes,
with the SHIPPED functions lifted from ``desktop/main.js``, one entry per odd
path (spaces, quotes, ``$``, a backtick, a backslash, ``%``, an apostrophe,
letters outside ASCII, all at once), a recorder script AT each exact path, and
``cases.json``. Each entry is then

1. checked by ``desktop-file-validate`` (exit 0, no ``error:`` line);
2. started by GLib (``Gio.DesktopAppInfo``: GNOME, Cinnamon, MATE, XFCE): the
   recorder must have been started with exactly its path and ``--hidden``;
3. turned into a unit by systemd's autostart generator (Plasma, and GNOME
   sessions run under systemd), when the machine has it;

and the negative control, an entry naming a program that does not exist, must
not start under GLib and must get no unit from the generator: that proves the
checks above look at the real path. A path with a line break gets no entry.

Runs on Linux only. On the Linux gate (``GITHUB_ACTIONS``) a missing tool
FAILS the test instead of skipping it, so the gate cannot pass it silently;
``linux-release.yml`` installs the tools in the ``python`` job.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "scripts" / "linux_autostart_samples.mjs"
SYS_PY = "/usr/bin/python3"   # the system python, where python3-gi lives (never the uv one)
GENERATOR = "/usr/lib/systemd/user-generators/systemd-xdg-autostart-generator"
ON_GATE = bool(os.environ.get("GITHUB_ACTIONS"))

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="the desktop's own parsers exist on Linux only")

# Our own launcher (not the samples' one): it reports what happened as JSON
# instead of an exit code, so a refusal and a crash read differently.
_GLIB_LAUNCH = r"""
import json, os, sys
import gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

info = Gio.DesktopAppInfo.new_from_filename(sys.argv[1])
if info is None:
    print(json.dumps({"loaded": False}))
    sys.exit(0)
pids = []
def on_pid(appinfo, pid, *data):
    pids.append(pid)
try:
    info.launch_uris_as_manager([], None, GLib.SpawnFlags.DO_NOT_REAP_CHILD, None, None, on_pid, None)
except GLib.Error as exc:
    print(json.dumps({"loaded": True, "launched": False, "error": str(exc)}))
    sys.exit(0)
# GLib may start the program through its gio-launch-desktop helper, which
# exits non-zero when the program cannot be started: the exit codes say so.
codes = [os.waitstatus_to_exitcode(os.waitpid(pid, 0)[1]) for pid in pids]
print(json.dumps({"loaded": True, "launched": bool(pids), "codes": codes}))
"""


def _need(ok: bool, what: str) -> None:
    if ok:
        return
    if ON_GATE:
        pytest.fail(f"{what} is missing on the Linux gate (the step 'The desktop's own parsers' installs it)")
    pytest.skip(f"{what} is not installed here")


def _have_gi() -> bool:
    if not os.path.exists(SYS_PY):
        return False
    probe = subprocess.run([SYS_PY, "-I", "-c", "import gi; gi.require_version('Gio', '2.0'); from gi.repository import Gio"],
                           capture_output=True, timeout=60)
    return probe.returncode == 0


def _cases(base: Path) -> tuple[list[dict], dict]:
    """Run the shell's own entry writer. Returns the cases that must start, each
    with its entry copied to ``<base>/config/autostart/sample<N>.desktop`` (one
    folder, one generator run), and the missing-program control, copied alone
    to ``<base>/config-missing/autostart/missing.desktop``."""
    _need(shutil.which("node") is not None, "node")
    assert SAMPLES.is_file(), f"{SAMPLES} is missing"
    made = base / "made"
    proc = subprocess.run(["node", str(SAMPLES), "--out", str(made)], capture_output=True, text=True,
                          encoding="utf-8", timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    cases = json.loads((made / "cases.json").read_text(encoding="utf-8"))["cases"]
    by = {c["expect"]: [] for c in cases}
    for c in cases:
        by[c["expect"]].append(c)
    starts = by.get("starts", [])
    assert len(starts) >= 8, f"too few odd paths to prove anything: {[c['id'] for c in starts]}"
    # A path with a line break is refused: no entry at all.
    assert [c["entry"] for c in by.get("refused", [])] == [None], by.get("refused")
    (missing,) = by["no-unit"]
    assert missing["entry"] and not Path(missing["target"]).exists()

    folder = base / "config" / "autostart"
    folder.mkdir(parents=True)
    armed = []
    for i, c in enumerate(starts):
        assert c["entry"], f"the shell refused an ordinary odd path: {c['target']!r}"
        target = Path(c["target"])
        assert target.is_file() and os.access(target, os.X_OK), f"no recorder at {c['target']!r}"
        entry = folder / f"sample{i}.desktop"
        entry.write_bytes((made / c["entry"]).read_bytes())
        marker = made / c["marker"]
        armed.append({"id": c["id"], "path": c["target"], "args": c.get("args", ["--hidden"]), "entry": entry,
                      "marker": marker, "unit": f"app-sample{i}@autostart.service"})
    alone = base / "config-missing" / "autostart"
    alone.mkdir(parents=True)
    (alone / "missing.desktop").write_bytes((made / missing["entry"]).read_bytes())
    return armed, {"path": missing["target"], "entry": alone / "missing.desktop",
                   "unit": "app-missing@autostart.service"}


def _launch(base: Path, entry: Path) -> dict:
    launcher = base / "glib_launch.py"
    if not launcher.exists():
        launcher.write_text(_GLIB_LAUNCH, encoding="utf-8")
    proc = subprocess.run([SYS_PY, "-I", str(launcher), str(entry)], capture_output=True, text=True,
                          encoding="utf-8", timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _units(base: Path, config: Path, name: str) -> dict[str, str]:
    """Run systemd's autostart generator over ``config/autostart`` alone."""
    out = base / f"units-{name}"
    out.mkdir()
    empty = base / f"no-system-config-{name}"
    empty.mkdir()
    env = {**os.environ, "XDG_CONFIG_HOME": str(config), "XDG_CONFIG_DIRS": str(empty)}
    proc = subprocess.run([GENERATOR, str(out), str(out), str(out)], capture_output=True, text=True,
                          encoding="utf-8", timeout=60, env=env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return {p.name: p.read_text(encoding="utf-8", errors="replace")
            for p in out.rglob("app-*@autostart.service") if p.is_file() and not p.is_symlink()}


def test_each_entry_passes_the_desktop_entry_validator(tmp_path):
    _need(shutil.which("desktop-file-validate") is not None, "desktop-file-validate (desktop-file-utils)")
    armed, _missing = _cases(tmp_path)
    wrong = []
    for s in armed:
        proc = subprocess.run(["desktop-file-validate", str(s["entry"])], capture_output=True, text=True,
                              encoding="utf-8", timeout=60)
        said = proc.stdout + proc.stderr
        if proc.returncode != 0 or "error:" in said:
            wrong.append((s["id"], said.strip()))
    assert wrong == []


def test_glib_starts_each_entry_with_exactly_its_path(tmp_path):
    _need(_have_gi(), "GLib for the system python3 (python3-gi, gir1.2-glib-2.0)")
    armed, _missing = _cases(tmp_path)
    wrong = []
    for s in armed:
        got = _launch(tmp_path, s["entry"])
        argv = s["marker"].read_text(encoding="utf-8").splitlines() if s["marker"].exists() else None
        if not got.get("launched") or got.get("codes") != [0] or argv != [s["path"], *s["args"]]:
            wrong.append((s["id"], got, argv))
    assert wrong == []


def test_systemds_autostart_generator_makes_a_unit_for_each_entry(tmp_path):
    if not os.access(GENERATOR, os.X_OK):
        pytest.skip("this machine has no systemd autostart generator")
    armed, _missing = _cases(tmp_path)
    units = _units(tmp_path, tmp_path / "config", "all")
    wrong = [(s["id"], s["unit"]) for s in armed if "--hidden" not in units.get(s["unit"], "")]
    assert wrong == [], sorted(units)


def test_an_entry_whose_program_does_not_exist_is_never_started(tmp_path):
    """The negative control: GLib must not start it and the generator must make
    no unit for it, so a pass above means each path was read to the real file."""
    _armed, missing = _cases(tmp_path)
    checked = False
    if _have_gi():
        got = _launch(tmp_path, missing["entry"])
        # Not started: GLib would not load the entry (it looks for the program),
        # refused to spawn it, or its helper could not.
        assert got.get("loaded") is False or got.get("launched") is False \
            or any(c != 0 for c in got.get("codes", [])), got
        checked = True
    if os.access(GENERATOR, os.X_OK):
        assert missing["unit"] not in _units(tmp_path, tmp_path / "config-missing", "missing")
        checked = True
    _need(checked, "GLib for the system python3 (python3-gi)")
