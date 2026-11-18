"""v1.538.0 (fourth review, group 29, MS2/MS3): the connected apps' bridge SHIPS.

Claude desktop, Claude Code and Codex start Iron Jarvis's bridge from their own
settings. Today that names ``ironjarvis.exe mcp-stdio`` inside the install folder,
which every update and every Quit empties of running programs (and on a Linux
AppImage is a temporary mount), so a connected app is cut off each time. Group 29
ships the bridge as its OWN small program, copied into the user's home at start.

A packaged build cannot run in this suite (PyInstaller + electron-builder), so the
chain is pinned link by link, by execution where a link can run here:

  packaging/ironjarvis.spec   builds ``ironjarvis-bridge`` from stdio_shim.py itself
                              (``iron_jarvis`` and ``ssl`` excluded), one file, into
                              packaging/dist/bridge/ with ``bridge-id.txt`` beside it
  desktop/package.json        ships that folder to resources/bridge
  desktop/afterPack.js        REFUSES a build without it (run here with node) and
                              inventories "bridge"
  release.yml                 runs ``--check`` on the frozen bridge, then checks the
                              SIGNED one's signature and runs it again
  maintenance.create_backup   leaves <home>/bridge/ out (run here)
  daemon/app.py               refreshes the copy at start, off the loop, bounded
  onboarding/doctor.py        one row says whether a checked copy is installed (run)
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "packaging" / "ironjarvis.spec"
SHIM = ROOT / "src" / "iron_jarvis" / "mcpserver" / "stdio_shim.py"
PACKAGE_JSON = ROOT / "desktop" / "package.json"
AFTER_PACK = ROOT / "desktop" / "afterPack.js"
RELEASE = ROOT / ".github" / "workflows" / "release.yml"
APP = ROOT / "src" / "iron_jarvis" / "daemon" / "app.py"
LOOP_WORDS = ROOT / "dashboard" / "lib" / "loopWords.ts"
BUILD_DAEMON = ROOT / "packaging" / "build_daemon.ps1"


def _read(path: Path) -> str:
    # CRLF normalised at the reader: CI's Windows runners check out with CRLF.
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _spec_tree() -> ast.Module:
    return ast.parse(_read(SPEC))


def _spec_constant(name: str):
    for node in _spec_tree().body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return node.value
    raise AssertionError(f"packaging/ironjarvis.spec defines no {name}")


def _literal(name: str):
    return ast.literal_eval(_spec_constant(name))


def _calls(func_name: str) -> list[ast.Call]:
    return [
        n for n in ast.walk(_spec_tree())
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == func_name
    ]


def _kw(call: ast.Call, name: str):
    for k in call.keywords:
        if k.arg == name:
            return k.value
    return None


# --------------------------------------------------------------------------
# The spec builds the bridge
# --------------------------------------------------------------------------


def test_the_spec_builds_the_bridge_from_the_shim_itself_with_the_app_left_out() -> None:
    script = _spec_constant("BRIDGE_SCRIPT")
    parts = [ast.literal_eval(a) for a in script.args if isinstance(a, ast.Constant)]
    assert ast.unparse(script.args[0]) == "SRC", ast.unparse(script)
    assert parts == ["iron_jarvis", "mcpserver", "stdio_shim.py"], ast.unparse(script)
    excludes = _literal("BRIDGE_EXCLUDES")
    assert "iron_jarvis" in excludes and "ssl" in excludes, excludes
    bridge_analyses = [
        c for c in _calls("Analysis")
        if c.args and ast.unparse(c.args[0]) == "[BRIDGE_SCRIPT]"
    ]
    assert len(bridge_analyses) == 1, "no Analysis([BRIDGE_SCRIPT], ...) in the spec"
    assert ast.unparse(_kw(bridge_analyses[0], "excludes")) == "BRIDGE_EXCLUDES"
    # The spec never imports the app's own bridge module (it would pull the app in).
    imported = [
        ast.unparse(n) for n in ast.walk(_spec_tree()) if isinstance(n, (ast.Import, ast.ImportFrom))
    ]
    assert not [i for i in imported if "mcpserver" in i or "stdio_shim" in i], imported


def test_the_bridge_is_one_file_named_apart_from_the_daemon() -> None:
    name = _literal("BRIDGE_NAME")
    assert name == "ironjarvis-bridge"
    daemon = [c for c in _calls("EXE") if isinstance(_kw(c, "name"), ast.Constant)]
    assert [ast.literal_eval(_kw(c, "name")) for c in daemon] == ["ironjarvis"]
    assert name != "ironjarvis" and f"{name}.exe" != "ironjarvis.exe"
    bridge = [c for c in _calls("EXE") if ast.unparse(_kw(c, "name") or ast.Constant("")) == "BRIDGE_NAME"]
    assert len(bridge) == 1, "no EXE(..., name=BRIDGE_NAME) in the spec"
    exe = bridge[0]
    args = [ast.unparse(a) for a in exe.args]
    # One file: the binaries and data ride INSIDE the exe (no exclude_binaries).
    assert "bridge_a.binaries" in args and "bridge_a.datas" in args, args
    flag = _kw(exe, "exclude_binaries")
    assert flag is None or ast.literal_eval(flag) is False
    assert ast.literal_eval(_kw(exe, "console")) is True  # it speaks over stdio
    assert _literal("BRIDGE_DIR") == "bridge"
    assert _literal("BRIDGE_ID_FILE") == "bridge-id.txt"


def test_the_spec_fails_a_bridge_that_pulled_in_the_app_or_grew() -> None:
    text = _read(SPEC)
    block = text[text.index("BRIDGE_SCRIPT ="):]
    assert "_bridge_leaks" in block and "raise SystemExit" in block
    assert re.search(r"BRIDGE_MAX_BYTES = \d", block)
    assert "os.path.getsize" in block and "> BRIDGE_MAX_BYTES" in block


def _spec_content_id():
    fn = next(
        n for n in _spec_tree().body
        if isinstance(n, ast.FunctionDef) and n.name == "_bridge_content_id"
    )
    namespace: dict = {}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(SPEC), "exec"), namespace)
    return namespace["_bridge_content_id"]


def test_the_spec_content_id_is_lf_normalised_sha256() -> None:
    content_id = _spec_content_id()
    lf = b"a line\nanother\n"
    assert content_id(lf) == hashlib.sha256(lf).hexdigest()
    assert content_id(lf.replace(b"\n", b"\r\n")) == content_id(lf)
    assert re.fullmatch(r"[0-9a-f]{64}", content_id(SHIM.read_bytes()))


def test_the_spec_content_id_equals_the_daemons() -> None:
    from iron_jarvis.mcpserver import apps

    daemon_id = getattr(apps, "bridge_content_id", None)
    if daemon_id is None:
        pytest.skip("mcpserver/apps.bridge_content_id is not in the tree yet (group 29, part A)")
    data = SHIM.read_bytes()
    content_id = _spec_content_id()
    assert content_id(data) == daemon_id(data)
    crlf = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    assert content_id(crlf) == daemon_id(crlf) == content_id(data)


def test_the_build_and_the_daemon_name_the_same_files() -> None:
    from iron_jarvis.mcpserver import apps, stdio_shim

    assert apps.BRIDGE_STEM == _literal("BRIDGE_NAME")
    assert apps.BRIDGE_ID_FILE == _literal("BRIDGE_ID_FILE")
    assert apps.BRIDGE_OK_LINE == stdio_shim.CHECK_LINE
    after = _read(AFTER_PACK)
    assert f'"{apps.BRIDGE_STEM}.exe" : "{apps.BRIDGE_STEM}"' in after
    assert f'"{apps.BRIDGE_ID_FILE}"' in after


def test_the_spec_writes_the_id_beside_the_bridge() -> None:
    text = _read(SPEC)
    block = text[text.index("BRIDGE_SCRIPT ="):]
    assert "os.path.join(DISTPATH, BRIDGE_DIR)" in block
    assert "BRIDGE_ID_FILE" in block and "_bridge_content_id(" in block


def test_the_build_script_requires_the_bridge() -> None:
    text = _read(BUILD_DAEMON)
    assert "bridge\\ironjarvis-bridge.exe" in text and "bridge\\bridge-id.txt" in text


# --------------------------------------------------------------------------
# The installer ships it; afterPack refuses a build without it
# --------------------------------------------------------------------------


def test_the_installer_ships_the_bridge_folder() -> None:
    extra = json.loads(_read(PACKAGE_JSON))["build"]["extraResources"]
    assert {"from": "../packaging/dist/bridge", "to": "bridge"} in extra


def test_afterpack_inventories_the_bridge() -> None:
    inventory = re.search(r"buildManifest\(resourcesDir, \[([^\]]*)\]", _read(AFTER_PACK))
    assert inventory and '"bridge"' in inventory.group(1)


def _run_after_pack(tmp_path: Path, platform: str) -> str:
    runner = tmp_path / "run.js"
    runner.write_text(
        "const ap = require(process.argv[2]);\n"
        "ap.default({appOutDir: process.argv[3], electronPlatformName: process.argv[4],\n"
        "  packager: {appInfo: {version: '0.0.0'}}})\n"
        "  .then(() => console.log('RESOLVED'), (e) => console.log('REJECTED ' + e.message));\n",
        encoding="utf-8",
    )
    out = subprocess.run(
        ["node", str(runner), str(AFTER_PACK), str(tmp_path / "app"), platform],
        capture_output=True, text=True, timeout=60,
    )
    # Only the outcome line: a warning on stderr naming the file is not a refusal.
    return next((ln for ln in out.stdout.splitlines() if ln.startswith(("REJECTED", "RESOLVED"))),
                out.stdout + out.stderr)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_afterpack_refuses_a_build_with_no_bridge(tmp_path: Path) -> None:
    (tmp_path / "app" / "resources").mkdir(parents=True)
    said = _run_after_pack(tmp_path, "win32")
    assert "REJECTED" in said and "ironjarvis-bridge.exe" in said, said


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_afterpack_refuses_a_bridge_without_its_id(tmp_path: Path) -> None:
    bridge = tmp_path / "app" / "resources" / "bridge"
    bridge.mkdir(parents=True)
    (bridge / "ironjarvis-bridge").write_bytes(b"x" * 16)
    said = _run_after_pack(tmp_path, "linux")
    assert "REJECTED" in said and "bridge-id.txt" in said, said
    # The Windows name is required on Windows, the bare name on Linux.
    said = _run_after_pack(tmp_path, "win32")
    assert "REJECTED" in said and "ironjarvis-bridge.exe" in said, said


# --------------------------------------------------------------------------
# release.yml checks it, frozen and signed
# --------------------------------------------------------------------------


def _release_steps() -> list[dict]:
    jobs = yaml.safe_load(_read(RELEASE))["jobs"]
    return [s for job in jobs.values() for s in (job.get("steps") or [])]


def test_the_freeze_step_runs_the_bridges_check() -> None:
    step = next(s for s in _release_steps() if str(s.get("name", "")).startswith("Freeze + smoke-test"))
    run = step["run"]
    assert "packaging/dist/bridge/ironjarvis-bridge.exe" in run
    assert "packaging/dist/bridge/bridge-id.txt" in run
    assert '"--check"' in run and "ironjarvis-bridge ok" in run
    from iron_jarvis.mcpserver.stdio_shim import CHECK_FLAG, CHECK_LINE

    assert f'"{CHECK_FLAG}"' in run and f'"{CHECK_LINE}"' in run


def test_the_signed_check_covers_the_bridge() -> None:
    step = next(s for s in _release_steps() if s.get("id") == "signature")
    run = step["run"]
    assert 'win-unpacked/resources/bridge/ironjarvis-bridge.exe' in run
    files_line = next(line for line in run.splitlines() if line.strip().startswith("$files = @("))
    assert "$bridge" in files_line, files_line
    after_files = run[run.index("$files = @("):]
    assert '"--check"' in after_files and "ironjarvis-bridge ok" in after_files


# --------------------------------------------------------------------------
# Backups leave the program copy out
# --------------------------------------------------------------------------


def test_backups_skip_the_bridge_folder(tmp_path: Path) -> None:
    from iron_jarvis.maintenance import create_backup

    home = tmp_path / ".ironjarvis"
    (home / "bridge").mkdir(parents=True)
    (home / "bridge" / "ironjarvis-bridge.exe").write_bytes(b"MZ" + b"\0" * 64)
    (home / "bridge" / "bridge-id.txt").write_text("0" * 64 + "\n", encoding="ascii")
    (home / "config.toml").write_text("# kept\n", encoding="utf-8")
    out, _n = create_backup(home, tmp_path / "out" / "b.tar.gz")
    with tarfile.open(out, "r:gz") as tar:
        names = [m.name.replace("\\", "/") for m in tar.getmembers()]
    assert ".ironjarvis/config.toml" in names  # anti-vacuity: the backup has files
    assert not [n for n in names if "/bridge/" in n], names


# --------------------------------------------------------------------------
# The daemon refreshes the copy at start: listed, off the loop, bounded
# --------------------------------------------------------------------------


def test_the_start_up_step_is_listed_and_off_the_loop() -> None:
    text = _read(APP)
    m = re.search(
        r"asyncio\.create_task\(\s*_rehydrate_step\(\s*\"refresh_bridge\",\s*([^,]+),\s*off_loop_s=([A-Z_]+)",
        text,
    )
    assert m, "refresh_bridge is not a background _rehydrate_step(..., off_loop_s=...)"
    bound = re.search(rf"^{m.group(2)} = ([0-9.]+)", text, flags=re.MULTILINE)
    assert bound and 0 < float(bound.group(1)) <= 120
    assert re.search(r"^\s{2}refresh_bridge: \{", _read(LOOP_WORDS), flags=re.MULTILINE)


def test_refresh_bridge_installs_then_moves_the_apps_over(monkeypatch, tmp_path: Path) -> None:
    from iron_jarvis.daemon import app as app_mod
    from iron_jarvis.mcpserver import apps

    calls: list[tuple[str, Path]] = []

    def ensure(home):
        calls.append(("ensure", Path(home)))
        return apps.BridgeState(True, "x", "a" * 64)

    monkeypatch.setattr(apps, "ensure_bridge", ensure)
    monkeypatch.setattr(apps, "migrate_entries", lambda home: calls.append(("migrate", Path(home))) or [])
    app_mod.refresh_bridge(tmp_path)
    assert calls == [("ensure", tmp_path), ("migrate", tmp_path)]


def test_refresh_bridge_fails_the_step_when_the_new_copy_fails_its_check(monkeypatch, tmp_path: Path) -> None:
    """Through the real ensure_bridge: a shipped program that cannot answer
    `--check` is not swapped in, and the start step FAILS with its words (the
    health line names it) instead of reading as done."""
    from iron_jarvis.daemon import app as app_mod
    from iron_jarvis.mcpserver import apps

    shipped = tmp_path / "resources" / "bridge"
    shipped.mkdir(parents=True)
    (shipped / apps.bridge_name()).write_bytes(b"not a program")
    (shipped / "bridge-id.txt").write_text("c" * 64 + "\n", encoding="ascii")
    monkeypatch.setenv("IRONJARVIS_BRIDGE_DIR", str(shipped))
    home = tmp_path / "home"
    home.mkdir()
    with pytest.raises(RuntimeError, match="did not answer its check"):
        app_mod.refresh_bridge(home)
    assert apps.installed_bridge(home) is None


def test_refresh_bridge_from_source_is_quiet(monkeypatch, tmp_path: Path) -> None:
    from iron_jarvis.daemon import app as app_mod

    monkeypatch.delenv("IRONJARVIS_BRIDGE_DIR", raising=False)
    app_mod.refresh_bridge(tmp_path)  # no shipped bridge: nothing to put in place, not a failure
    assert not (tmp_path / "bridge").exists()


# --------------------------------------------------------------------------
# The doctor row
# --------------------------------------------------------------------------

def _install(folder: Path, content_id: str) -> None:
    from iron_jarvis.mcpserver import apps

    folder.mkdir(parents=True, exist_ok=True)
    (folder / apps.bridge_name()).write_bytes(b"MZ" + b"\0" * 64)
    (folder / "bridge-id.txt").write_text(content_id + "\n", encoding="ascii")


def test_doctor_row_with_a_checked_copy(monkeypatch, tmp_path: Path) -> None:
    from iron_jarvis.onboarding.doctor import check_connector_bridge

    shipped = tmp_path / "resources" / "bridge"
    _install(shipped, "a" * 64)
    _install(tmp_path / "home" / "bridge", "a" * 64)
    monkeypatch.setenv("IRONJARVIS_BRIDGE_DIR", str(shipped))
    row = check_connector_bridge(tmp_path / "home")
    assert row["name"] == "connector_bridge" and row["ok"] is True, row
    assert str(tmp_path / "home" / "bridge") in row["detail"]


def test_doctor_row_with_no_checked_copy(monkeypatch, tmp_path: Path) -> None:
    from iron_jarvis.mcpserver import apps
    from iron_jarvis.onboarding.doctor import check_connector_bridge

    shipped = tmp_path / "resources" / "bridge"
    _install(shipped, "a" * 64)
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("IRONJARVIS_BRIDGE_DIR", str(shipped))
    row = check_connector_bridge(tmp_path / "home")
    assert row["ok"] is False and row["fix"], row
    # A copy with no id was never checked: the same answer.
    (tmp_path / "home" / "bridge").mkdir()
    (tmp_path / "home" / "bridge" / apps.bridge_name()).write_bytes(b"MZ")
    assert check_connector_bridge(tmp_path / "home")["ok"] is False


def test_doctor_row_with_an_older_copy(monkeypatch, tmp_path: Path) -> None:
    from iron_jarvis.onboarding.doctor import check_connector_bridge

    shipped = tmp_path / "resources" / "bridge"
    _install(shipped, "b" * 64)
    _install(tmp_path / "home" / "bridge", "a" * 64)
    monkeypatch.setenv("IRONJARVIS_BRIDGE_DIR", str(shipped))
    row = check_connector_bridge(tmp_path / "home")
    assert row["ok"] is False and "older" in row["detail"], row


def test_doctor_row_from_source(monkeypatch, tmp_path: Path) -> None:
    from iron_jarvis.onboarding.doctor import check_connector_bridge

    (tmp_path / "home").mkdir()
    monkeypatch.delenv("IRONJARVIS_BRIDGE_DIR", raising=False)
    row = check_connector_bridge(tmp_path / "home")
    assert row["ok"] is True and "source" in row["detail"], row


def test_the_doctor_runs_the_row() -> None:
    text = _read(ROOT / "src" / "iron_jarvis" / "onboarding" / "doctor.py")
    body = text[text.index("def _runtime_checks"):text.index("def check_model_targets")]
    assert "check_connector_bridge(platform.config.home)" in body
