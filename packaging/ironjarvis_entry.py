"""Frozen entry point for the Iron Jarvis daemon.

PyInstaller targets THIS file. A frozen app has no console-script shim, so we
import the Typer ``app`` and invoke it directly. Every CLI subcommand
(``serve``, ``status``, ``demo``, ...) is therefore available from the exe, e.g.

    ironjarvis.exe serve --port 8799 --root C:\\path\\to\\state

``serve`` is the long-running daemon (FastAPI + uvicorn) the dashboard/Electron
shell spawns.
"""

from __future__ import annotations

import multiprocessing
import sys


#: v1.300.0: the Claude CLI spawns ``ironjarvis.exe claude-inert-mcp <manifest>``
#: as its tool-inventory server on EVERY model call, and waits for it before
#: it sends the request. Routing that through the Typer app imports
#: ``iron_jarvis.daemon.cli`` (FastAPI, SQLAlchemy, the platform — ~0.9 s warm
#: from source, more frozen) for a stdlib-only module that loads in ~15 ms.
#: The fast path below must stay behaviour-identical to the hidden subcommand
#: in ``daemon/cli.py`` (same module, same ``main([manifest])``).
INERT_MCP_SUBCOMMAND = "claude-inert-mcp"

#: v1.333.0: the two other self-re-exec children take the same door. Through
#: the Typer app each one imported the whole daemon (platform, orchestrator,
#: SQLAlchemy, FastAPI, numpy: ~820 MB committed, ~100 MB working set and
#: ~1.5 s per child) to run a module that needs ~10-20 MB on its own. A REPL
#: namespace is a child per agent session; a Codex Build pane starts one
#: ``mcp-stdio`` bridge. Each fast path below is behaviour-identical to its
#: hidden subcommand in ``daemon/cli.py``: same module, same arguments, same
#: exit code, and nothing printed on stdout (it carries the wire).
REPL_WORKER_SUBCOMMAND = "repl-worker"
MCP_STDIO_SUBCOMMAND = "mcp-stdio"


def _fast_path(argv: list[str]) -> int | None:
    """Exit code for a subcommand served WITHOUT the CLI import, else None.

    Anything the Typer subcommand would treat differently (an option such as
    ``--help``, an extra argument) is left to the Typer app, so its own
    message and exit code still apply."""
    if len(argv) == 2 and argv[0] == INERT_MCP_SUBCOMMAND:
        from iron_jarvis.providers.adapters.claude_native.inert_mcp import main as inert_main

        return inert_main([argv[1]])
    if argv == [REPL_WORKER_SUBCOMMAND]:
        from iron_jarvis.repl.worker import main as worker_main

        # The subcommand discards the worker's return value, so Typer exits 0.
        worker_main()
        return 0
    if argv and argv[0] == MCP_STDIO_SUBCOMMAND and len(argv) <= 2:
        url = argv[1] if len(argv) == 2 else ""
        if url.startswith("-"):
            return None  # an option, not the URL argument: Typer's to answer
        from iron_jarvis.mcpserver.stdio_shim import main as shim_main

        return shim_main([MCP_STDIO_SUBCOMMAND, url] if url else [MCP_STDIO_SUBCOMMAND])
    return None


def main() -> None:
    code = _fast_path(sys.argv[1:])
    if code is not None:
        raise SystemExit(code)
    # Defer the (heavy) package import until after freeze_support so the
    # multiprocessing bootstrap path stays cheap if it is ever taken.
    from iron_jarvis.daemon.cli import app

    app()


if __name__ == "__main__":
    # Harmless on a single-process daemon, but mandatory if any dependency ever
    # spawns a child process under a frozen Windows build.
    multiprocessing.freeze_support()
    main()
