"""v1.460.0 — a USER BUG on the owner's office PC: Box connected ("2 in vault")
but its extension never started:

    ImportError: cannot import name 'box_claude_ai_agent_ask' from 'box_ai_agents_toolkit'

The Box card launched ``uvx mcp-server-box``. That PyPI package (0.1.2,
2025-06-05) is the last Box published, its GitHub repository is discontinued,
and it names its libraries with no upper bound, so uv now pulls a
``box-ai-agents-toolkit`` that dropped the name the server imports. Measured
on this PC (scratch uv cache and home, no Box credentials): the old line
fails with that exact error; ``connectors.catalog.BOX_ARGS`` answers
``tools/list`` with 30 tools.

1. The card launches BOX_ARGS (Directory card, Tools row, the add door).
2. A row saved with the old line is repaired at boot — only that exact row.
3. "The package's own code could not import" reads as a package broken
   upstream, never as "a missing key or setting is the usual cause".
4. A launch line's package is read past the launcher's option values
   (``uvx --python 3.13 …`` runs mcp-server-box, not "3.13").
5. Opt-in (IJ_LIVE_BOX=1): the line really starts and answers tools/list.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from iron_jarvis.connectors import catalog as cat
from iron_jarvis.connectors.catalog import BOX_ARGS, BOX_OLD_ARGS
from iron_jarvis.mcp.launch_args import package_arg
from iron_jarvis.mcp.package_fixes import REPLACED_LAUNCH_LINES, repair_known_package_names
from iron_jarvis.mcp.tools import classify_load_failure

#: The Tools row's text on the owner's office PC, as the app recorded it.
OWNER_ERROR = (
    "MCPServerStopped: extension 'box': the MCP server closed the connection (exit code 1); its last output: "
    'File "C:\\Users\\Valentino\\AppData\\Local\\uv\\cache\\archive-v0\\abc\\Scripts\\mcp-server-box.exe\\__main__.py", '
    "line 4, in <module> | from mcp_server_box import main | File "
    '"C:\\Users\\Valentino\\AppData\\Local\\uv\\cache\\archive-v0\\abc\\Lib\\site-packages\\mcp_server_box.py", '
    "line 14, in <module> | from box_ai_agents_toolkit import ( | ... | ) | ImportError: cannot import name "
    "'box_claude_ai_agent_ask' from 'box_ai_agents_toolkit' "
    "(C:\\Users\\Valentino\\AppData\\Local\\uv\\cache\\archive-v0\\abc\\Lib\\site-packages\\box_ai_agents_toolkit\\__init__.py)"
    "; it will be restarted on the next call. If it needs a key or setting, the user can fix it in Settings → Tools."
)
#: The same tail cut at 600 characters mid-path (the transport's cut).
CUT_ERROR = (
    "the MCP server closed the connection (exit code 1); its last output: ImportError: cannot import name "
    "'box_claude_ai_agent_ask' from 'box_ai_agents_toolkit' (C:\\Users\\Valentino\\AppData\\Local\\uv\\cache\\arch"
)


def _box_card():
    (card,) = [c for c in cat.CATALOG if c.id == "box"]
    return card


# --------------------------------------------------------------------------- #
# 1. the card
# --------------------------------------------------------------------------- #


def test_the_box_card_tools_row_and_add_body_launch_the_frozen_line():
    card = _box_card()
    assert card.command == "uvx" and tuple(card.args) == BOX_ARGS
    # Every piece the measurement needed is there.
    assert "mcp-server-box==0.1.2" in BOX_ARGS
    assert BOX_ARGS[BOX_ARGS.index("--exclude-newer") + 1] == "2025-06-06"
    assert BOX_ARGS[BOX_ARGS.index("--python") + 1] == "3.13"
    assert "boxsdk==3.14.0" in BOX_ARGS
    # The spec is LAST: uvx options must come before the command it runs.
    assert BOX_ARGS[-1] == "mcp-server-box==0.1.2"

    (row,) = [r for r in cat.mcp_catalog_rows() if r["id"] == "box"]
    assert row["command"] == "uvx" and tuple(row["args"]) == BOX_ARGS

    from iron_jarvis.connectors.service import pack_body

    body = pack_body(card, {"BOX_CLIENT_ID": "id", "BOX_CLIENT_SECRET": "secret"})
    assert body.command == "uvx" and tuple(body.args) == BOX_ARGS


# --------------------------------------------------------------------------- #
# 2. the boot repair
# --------------------------------------------------------------------------- #


class _Cfg:
    def __init__(self, home: Path, servers):
        self.home = home
        self.mcp_servers = servers


def test_the_exact_old_box_row_is_repaired_keeping_its_secrets_and_others_are_untouched(tmp_path, monkeypatch):
    written: list[dict] = []
    monkeypatch.setattr(
        "iron_jarvis.mcp.package_fixes.persist_config_values",
        lambda home, values: written.append(values),
    )
    owner = {
        "name": "box",
        "command": "uvx",
        "args": ["mcp-server-box"],
        "env": {},
        "env_secrets": {"BOX_CLIENT_ID": "mcp.box.BOX_CLIENT_ID", "BOX_CLIENT_SECRET": "mcp.box.BOX_CLIENT_SECRET"},
        "auto_approve": False,
    }
    cfg = _Cfg(
        tmp_path,
        [
            dict(owner),
            {"name": "box2", "command": "uvx", "args": ["mcp-server-box"], "transport": "stdio"},
            # A user's own lines: never touched.
            {"name": "latest", "command": "uvx", "args": ["mcp-server-box@latest"]},
            {"name": "pinned", "command": "uvx", "args": ["--with", "box-ai-agents-toolkit==0.0.42", "mcp-server-box"]},
            {"name": "uvtool", "command": "uv", "args": ["mcp-server-box"]},
            {"name": "fullpath", "command": "C:/x/uvx.exe", "args": ["mcp-server-box"]},
            {"name": "remote", "transport": "http", "command": "uvx", "args": ["mcp-server-box"], "url": "https://x"},
        ],
    )
    assert repair_known_package_names(config=cfg) == ["box", "box2"]
    fixed = cfg.mcp_servers[0]
    assert fixed["args"] == list(BOX_ARGS)
    assert {k: v for k, v in fixed.items() if k != "args"} == {k: v for k, v in owner.items() if k != "args"}
    assert cfg.mcp_servers[1]["args"] == list(BOX_ARGS)
    for row in cfg.mcp_servers[2:]:
        assert row["args"] != list(BOX_ARGS), row["name"]
    assert written and written[0]["mcp_servers"] == cfg.mcp_servers
    # Idempotent: the repaired line is not repaired again.
    assert repair_known_package_names(config=cfg) == []
    assert len(written) == 1
    assert REPLACED_LAUNCH_LINES[("uvx", BOX_OLD_ARGS)] == list(BOX_ARGS)


def test_the_daemon_boot_repairs_the_owners_box_row_in_config_toml(tmp_path):
    from iron_jarvis.core.config import persist_config_values
    from iron_jarvis.daemon.app import create_app

    app = create_app(str(tmp_path))
    home = Path(app.state.platform.config.home)
    persist_config_values(
        home,
        {"mcp_servers": [{"name": "box", "command": "uvx", "args": ["mcp-server-box"], "enabled": False}]},
    )
    again = create_app(str(tmp_path))
    (box,) = [s for s in again.state.platform.config.mcp_servers if s.get("name") == "box"]
    assert box["args"] == list(BOX_ARGS)
    assert "2025-06-06" in (home / "config.toml").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# 3. the words
# --------------------------------------------------------------------------- #


def test_the_owners_error_reads_as_a_package_broken_upstream_not_a_missing_key():
    out = classify_load_failure({"command": "uvx", "args": ["mcp-server-box"]}, OWNER_ERROR)
    assert "package is broken upstream" in out["reason"]
    assert "cannot import name 'box_claude_ai_agent_ask' from 'box_ai_agents_toolkit'" in out["reason"]
    assert "A missing key or setting does not cause this" in out["reason"]
    # This PC's path is left out of the sentence.
    assert "Valentino" not in out["reason"] and "AppData" not in out["reason"]
    # The transport's own words after the tail are not part of the message.
    assert "restarted" not in out["reason"] and "Settings" not in out["reason"]
    assert "usual cause" not in out["fix"] and "key" not in out["fix"]
    assert "newer version" in out["fix"]
    cut = classify_load_failure({"command": "uvx"}, CUT_ERROR)
    assert "from 'box_ai_agents_toolkit'), so its package is broken upstream" in cut["reason"]
    assert "Valentino" not in cut["reason"]


def test_other_import_failures_read_the_same_and_a_plain_crash_keeps_its_words():
    py = classify_load_failure(
        {"command": "python"},
        "closed the connection (exit code 1); its last output: ModuleNotFoundError: No module named 'boxsdk'",
    )
    assert "No module named 'boxsdk'" in py["reason"] and "broken" in py["reason"]
    assert "upstream" not in py["reason"]  # a local program: no registry to blame
    assert "install what it needs" in py["fix"]
    node = classify_load_failure(
        {"command": "npx"},
        "exit code 1; its last output: SyntaxError: The requested module 'zod' does not provide an export named 'z' | at x",
    )
    assert "does not provide an export named 'z'" in node["reason"] and "upstream" in node["reason"]
    npx_cmd = classify_load_failure(
        {"command": "C:\\Program Files\\nodejs\\npx.cmd"},
        "exit code 1; its last output: Error [ERR_MODULE_NOT_FOUND]: Cannot find package 'zod' imported from C:\\x\\index.js",
    )
    assert "Cannot find package 'zod' imported from" in npx_cmd["reason"] and "upstream" in npx_cmd["reason"]
    crash = classify_load_failure({"command": "uvx"}, "closed the connection (exit code 1); its last output: Error: BOX_CLIENT_ID missing")
    assert crash == {
        "reason": "the extension's server exited (exit code 1)",
        "fix": "read its last output below; a missing key or setting is the usual cause",
    }


def test_the_import_patterns_are_linear_on_a_huge_tail():
    """A new regex over a child's output gets a pathological-input pin in a
    child process with a deadline (the v1.421.0 rule)."""
    code = (
        "import time\n"
        "from iron_jarvis.mcp.tools import classify_load_failure as c\n"
        "t=time.perf_counter()\n"
        "n=128_000\n"
        "for raw in ('ImportError: ' * (n // 13), 'ImportError: ' + 'a' * n, "
        "\"The requested module '\" * (n // 22), \"Cannot find package '\" + 'x' * n, "
        "'ERR_PACKAGE_PATH_NOT_EXPORTED' * (n // 29), 'ImportError: x (C:\\\\' + 'a ' * n):\n"
        "    c({'command': 'uvx'}, 'closed the connection (exit code 1); its last output: ' + raw)\n"
        "print(time.perf_counter()-t)\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert float(out.stdout.strip()) < 10


# --------------------------------------------------------------------------- #
# 4. reading a launch line's package
# --------------------------------------------------------------------------- #


def test_a_launch_lines_package_is_read_past_option_values():
    assert package_arg(BOX_ARGS) == "mcp-server-box==0.1.2"
    assert package_arg(["mcp-server-box"]) == "mcp-server-box"
    assert package_arg(["-y", "@modelcontextprotocol/server-memory"]) == "@modelcontextprotocol/server-memory"
    assert package_arg(["--from", "pkg", "cmd"]) == "pkg"
    assert package_arg(["--from=pkg", "cmd"]) == "pkg"
    assert package_arg(["-p", "pkg", "cmd"]) == "pkg"  # npx's --package, read as before
    assert package_arg([]) == ""

    from iron_jarvis.daemon.routes.agents import _first_package
    from iron_jarvis.mcp.pack_door import _server_identity

    assert _first_package(list(BOX_ARGS)) == "mcp-server-box"
    assert _server_identity({"command": "uvx", "args": list(BOX_ARGS)}) == ("stdio", "", "uvx", "mcp-server-box==0.1.2")
    # A placeholder before the package still falls through to the package.
    assert _first_package(["<path>", "@scope/pkg"]) == "@scope/pkg"


# --------------------------------------------------------------------------- #
# 5. the real line (opt-in: it downloads from PyPI)
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(os.environ.get("IJ_LIVE_BOX") != "1", reason="set IJ_LIVE_BOX=1 to download and start Box's server")
@pytest.mark.timeout(600)
def test_the_frozen_line_really_starts_and_lists_its_tools(tmp_path):
    uvx = shutil.which("uvx")
    if not uvx:
        pytest.skip("uvx not installed")
    env = dict(os.environ)
    for k in list(env):
        if k.startswith("BOX_"):
            env.pop(k)
    env.update(UV_CACHE_DIR=str(tmp_path / "uvcache"), USERPROFILE=str(tmp_path / "home"), HOME=str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    proc = subprocess.Popen(
        [uvx, *BOX_ARGS], cwd=str(tmp_path), env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
    )
    lines: list[str] = []

    def _read():
        for line in proc.stdout:
            lines.append(line)

    threading.Thread(target=_read, daemon=True).start()
    try:
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}) + "\n")
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}) + "\n")
        proc.stdin.flush()
        deadline = time.monotonic() + 540
        while time.monotonic() < deadline and not any('"id":2' in x.replace(" ", "") for x in lines):
            time.sleep(0.2)
        (answer,) = [json.loads(x) for x in lines if '"id":2' in x.replace(" ", "")]
        assert len(answer["result"]["tools"]) >= 20
    finally:
        proc.kill()
        proc.wait()
