"""SEE THE HARNESS (v1.426.0, Wave 6, E1 of the 2026-11-01 audit).

A coding session's model is given Iron Jarvis's own instructions — the
COMPONENT TREE (``harness/components``: how it works, the guardrails, the
memory, the tools in its model family's words, the folder's skills, helper
briefs, scripts and rules, the lessons kept for the folder) — and nothing on
screen said what they were, or that a folder's own instruction files were
IGNORED (an invalid ``.ironjarvis/harness/`` layer is voided with one note,
which only the "/" menu ever showed).

``GET /build/sessions/{id}/harness`` answers the tree for the session's
folder and model, put together THE SAME WAY a turn does
(``daemon/chat_turn._build_harness``: the layer read and validated, the
folder's rule files, the accepted lessons, the model family's dialect, plan
mode, a team lead's tools, Local only, the approval posture) and rendered by
``harness/components.render``. READ-ONLY and NEVER STORED: computed on each
read, masked (``detections.redact.mask``) BEFORE it is cut to
:data:`SHOW_CAP` characters. What a turn adds per turn (today's date, the
project map, the skill listing, a picked skill, the team brief, the summary
standing in for older turns) is not part of the tree and is not here.

The answer: ``{family, dialect, dialect_label, layers, note, mode,
rule_files, text, chars, cut, fingerprint}`` — ``note`` is the folder layer's
own sentence when its files were not used ("" otherwise). A failure is an
``error`` sentence with no text, never a 500. Sync ``def``: the threadpool
runs it (stats, small reads, registry reads).
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI, HTTPException

from ...harness import sessions as S

log = logging.getLogger(__name__)

#: The most characters of the instructions the view shows (the tree's own
#: parts are capped at 64,000 of folder text plus the defaults).
SHOW_CAP = 80_000

#: Said when the instructions could not be put together.
SHOW_FAILED = "The instructions could not be put together just now. Try again in a moment."


def _setup(row: Any) -> dict[str, Any]:
    try:
        data = json.loads(getattr(row, "setup_json", "") or "{}")
    except (TypeError, ValueError):
        data = {}
    return data if isinstance(data, dict) else {}


def instructions_view(platform: Any, row: Any) -> dict[str, Any]:
    """The session's harness instructions (see the module docstring).
    BLOCKING; never raises."""
    try:
        from ...detections.redact import mask
        from ...harness import components as C
        from ...harness import mode as M
        from ...harness.dialects import DIALECTS
        from ...harness.library import library_lines
        from ...projects.folder_rules import chat_folder_rules_block, chat_folder_rules_enabled
        from ...projects.locate import project_for_path
        from ..chat_turn import _harness_model, normalize_approval_mode
        from .build import _turn_pair

        setup = _setup(row)
        folder = S.folder_of(row)
        home_folder = S.checkout_of(row) or folder
        plan_mode = S.mode_of(row) == S.MODE_PLAN
        engine = getattr(platform, "engine", None)
        project = project_for_path(engine, home_folder) if (home_folder and engine is not None) else None
        prov, mod = _turn_pair(
            platform,
            SimpleNamespace(provider=str(setup.get("provider") or ""), model=str(setup.get("model") or "")),
            project,
        )
        prov, mod = _harness_model(platform, prov, mod)
        layer = C.EMPTY_LAYER
        rule_files: list[str] = []
        library: list[str] = []
        if folder:
            try:
                known = set(platform.registry.names())
            except Exception:  # noqa: BLE001 — the validator then skips tool names
                known = None
            # The turn's own order (v1.375.0): the working folder's layer,
            # else — on a branch — the picked folder's (S.layer_folder_of).
            layer = C.read_project_layer(S.layer_folder_of(row), known_tools=known, event_bus=None)
            if chat_folder_rules_enabled(getattr(platform, "config", None)):
                kw = {"fallback_root": home_folder} if home_folder != folder else {}
                _section, rule_files = chat_folder_rules_block(folder, event_bus=None, **kw)
            library = library_lines(engine, home_folder, event_bus=None)
        lead = False
        if not plan_mode:
            from ...harness import team as T

            team = T.from_setup(setup)
            lead = team is not None and T.resolve(platform, team) is not None
        turn = M.build(
            registry=platform.registry,
            permissions=platform.permissions,
            requested="auto",
            provider=prov,
            model=mod,
            stream=True,
            layer=layer,
            rule_files=rule_files,
            local_only=setup.get("local_only") is True,
            approval_mode=normalize_approval_mode(str(setup.get("approval_mode") or "")),
            today="",
            plan_mode=plan_mode,
            ask_user=True,
            library=library,
            lead=lead,
        )
        info = turn.route_info()
        text = mask(str(turn.rendered or ""))
        cut = len(text) > SHOW_CAP
        if cut:
            text = text[:SHOW_CAP]
        dialect = str(info.get("dialect") or "")
        return {
            "family": str(info.get("family") or ""),
            "dialect": dialect,
            "dialect_label": DIALECTS[dialect].label if dialect in DIALECTS else "",
            "layers": list(info.get("layers") or []),
            "note": str(info.get("note") or ""),
            "mode": "plan" if plan_mode else "act",
            "rule_files": list(rule_files),
            "text": text,
            "chars": len(text),
            "cut": cut,
            "fingerprint": str(info.get("fingerprint") or ""),
        }
    except Exception:  # noqa: BLE001 — a view never breaks the session
        log.warning("the harness instructions could not be put together", exc_info=True)
        return {"error": SHOW_FAILED, "text": "", "chars": 0, "cut": False, "layers": [], "note": ""}


def register(app: FastAPI, d: Any) -> None:
    @app.get("/build/sessions/{thread_id}/harness")
    def build_session_harness(thread_id: str) -> dict[str, Any]:
        """v1.426.0 (Wave 6, E1): the instructions Iron Jarvis gives this
        session's model, read-only (:func:`instructions_view`). 404 for an
        unknown session. Sync: the threadpool runs it."""
        row = S.get_row(d.platform.engine, thread_id)
        if row is None:
            raise HTTPException(status_code=404, detail="no such coding session")
        return instructions_view(d.platform, row)
