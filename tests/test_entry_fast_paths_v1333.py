"""The frozen entry serves ``repl-worker`` and ``mcp-stdio`` WITHOUT importing
the daemon (v1.333.0, D2).

Frozen, both children re-execute ``ironjarvis.exe`` itself. Through the Typer
app each one imported the whole daemon (platform, SQLAlchemy, FastAPI, numpy:
~820 MB committed and ~1.5 s per child) to run a module that needs ~10-20 MB.
These pins drive the entry file's own ``main()`` under the development
interpreter, the closest a test gets to the exe without a PyInstaller build,
and read ``sys.modules`` afterwards.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

from iron_jarvis.mcpserver import stdio_shim
from iron_jarvis.terminals import recipes

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "packaging" / "ironjarvis_entry.py"
HEAVY = ("iron_jarvis.daemon", "fastapi", "sqlalchemy", "numpy")


def _load_entry():
    spec = importlib.util.spec_from_file_location("ij_entry_v1333", ENTRY)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _run_entry(argv: list[str], report: Path, *, stdin: bytes, env: dict[str, str] | None = None):
    """Run the entry's main() with *argv*; the child writes its exit code and
    the heavy modules it loaded to *report* (stdout belongs to the wire)."""
    probe = (
        "import importlib.util, json, sys\n"
        f"spec = importlib.util.spec_from_file_location('e', {str(ENTRY)!r})\n"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        f"sys.argv = ['ironjarvis.exe', *{argv!r}]\n"
        "code = None\n"
        "try:\n"
        "    m.main()\n"
        "except SystemExit as exc:\n"
        "    code = exc.code\n"
        f"heavy = sorted(n for n in sys.modules if n.startswith({HEAVY!r}))\n"
        f"open({str(report)!r}, 'w', encoding='utf-8').write(json.dumps({{'code': code, 'heavy': heavy}}))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        input=stdin,
        capture_output=True,
        timeout=60,  # a guard against a hung child, not a performance assertion
        cwd=str(ROOT),
        env=env,
    )
    return proc, json.loads(report.read_text(encoding="utf-8"))


def test_the_entry_runs_the_repl_worker_without_importing_the_daemon(tmp_path):
    request = {"id": "r1", "code": "x = 6 * 7\nprint(x)"}
    proc, got = _run_entry(
        ["repl-worker"], tmp_path / "report.json", stdin=(json.dumps(request) + "\n").encode()
    )
    assert got == {"code": 0, "heavy": []}, proc.stderr.decode("utf-8", "replace")[-800:]
    replies = [json.loads(x) for x in proc.stdout.decode("utf-8").splitlines() if x.strip()]
    assert len(replies) == 1
    assert replies[0]["id"] == "r1"
    assert replies[0]["ok"] is True
    assert replies[0]["stdout"] == "42\n"


def test_the_entry_runs_the_mcp_stdio_bridge_without_importing_the_daemon(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != recipes.MCP_TOKEN_ENV}
    proc, got = _run_entry(
        ["mcp-stdio", "http://127.0.0.1:1/mcp"], tmp_path / "report.json", stdin=b"", env=env
    )
    # The shim's own exit code (2 = started with no token), relayed unchanged,
    # with the reason on stderr and never a byte on the wire.
    assert got == {"code": 2, "heavy": []}
    assert proc.stdout == b""
    assert stdio_shim.NO_TOKEN_MESSAGE in proc.stderr.decode("utf-8", "replace")


def test_the_fast_paths_claim_only_the_shapes_their_subcommands_accept():
    entry = _load_entry()
    assert entry.REPL_WORKER_SUBCOMMAND == "repl-worker"
    assert entry.MCP_STDIO_SUBCOMMAND == recipes.CodexRecipe.shim_command
    # An option or an extra argument is the Typer app's to answer (its own
    # usage message and exit code), so the fast path stands aside.
    for argv in (
        ["repl-worker", "extra"],
        ["repl-worker", "--help"],
        ["mcp-stdio", "--help"],
        ["mcp-stdio", "a", "b"],
        ["serve"],
        [],
    ):
        assert entry._fast_path(argv) is None, argv
