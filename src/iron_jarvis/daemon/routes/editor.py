"""Open a file in the user's code editor (v1.405.0, harness/code_editor).

* ``GET /editor/options`` — which editors this PC has (VS Code, Cursor), the
  one a press opens (the ``code_editor`` setting: auto = VS Code if installed,
  else Cursor), and a note when detection cannot tell (not Windows).
* ``POST /editor/open {path, line?, column?, editor?}`` — checks the path
  (harness/code_editor ``check_path``), builds the editor's URL ITSELF and
  hands it to the OS. The client never sends a URL.

Both routes are sync ``def`` on purpose: FastAPI runs them on its threadpool,
so the registry read, the disk checks and the launch are off the event loop
(the ``/documents/open`` rule). A refusal is one plain sentence.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from ...harness import code_editor


class EditorOpenBody(BaseModel):
    """``POST /editor/open``. Loose types on purpose: a wrong value is refused
    in one plain sentence by ``code_editor``, never a pydantic list."""

    path: Any = ""
    line: Any = None
    column: Any = None
    editor: Any = None


def _launch_url(url: str) -> None:
    """Hand an editor URL to the OS (its registered protocol handler opens the
    editor). Module-level so tests monkeypatch it instead of really launching
    an editor — the ``routes/documents._open_native`` pattern."""
    import os
    import subprocess
    import sys

    if sys.platform == "win32":
        os.startfile(url)  # noqa: S606 — explicit, user-initiated open
    elif sys.platform == "darwin":
        subprocess.Popen(["open", url])
    else:
        subprocess.Popen(["xdg-open", url])


def _setting(d: Any) -> str:
    cfg = getattr(getattr(d, "platform", None), "config", None)
    return code_editor.clean_setting(getattr(cfg, "code_editor", "auto"))


def register(app: FastAPI, d: Any) -> None:
    """Attach these routes to *app*; ``d`` is the create_app deps object."""

    @app.get("/editor/options")
    def editor_options() -> dict[str, Any]:
        """The editors this PC has and the one a press opens."""
        return code_editor.options(_setting(d))

    @app.post("/editor/open")
    def editor_open(body: EditorOpenBody) -> dict[str, Any]:
        """Open one file at a line in VS Code or Cursor. Sync: off the loop."""
        try:
            line = code_editor.check_position(body.line)
            column = code_editor.check_position(body.column, column=True)
            path = code_editor.check_path(body.path)
            editor = code_editor.choose(body.editor, _setting(d))
            url = code_editor.editor_url(editor, path, line, column)
        except code_editor.EditorRefusal as refusal:
            raise HTTPException(status_code=refusal.status, detail=refusal.sentence) from None
        try:
            _launch_url(url)
        except Exception:  # noqa: BLE001 — the OS refused; say so plainly
            label = code_editor.LABELS[editor]
            raise HTTPException(
                status_code=422,
                detail=f"Iron Jarvis could not open {label}. Open {label} once by itself, then try again.",
            ) from None
        return {"ok": True, "editor": editor, "label": code_editor.LABELS[editor], "line": line or 1}
