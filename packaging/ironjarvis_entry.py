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


def _fast_path(argv: list[str]) -> int | None:
    """Exit code for a subcommand served WITHOUT the CLI import, else None."""
    if len(argv) == 2 and argv[0] == INERT_MCP_SUBCOMMAND:
        from iron_jarvis.providers.adapters.claude_native.inert_mcp import main as inert_main

        return inert_main([argv[1]])
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
