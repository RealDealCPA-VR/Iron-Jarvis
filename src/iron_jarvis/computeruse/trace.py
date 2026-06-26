"""Trace recorder — the durable audit trail of a Computer-Use run.

Every action, result, screenshot reference, error, and artifact is appended as a
JSON-able event with a monotonic sequence number and timestamp. The harness
serialises ``events()`` into ``ComputerUseRun.trace_json`` (JSON-on-the-run; no
extra table needed).

Screenshots are saved to the platform :class:`ArtifactStore` when one is
provided, and the trace records only the **path reference** (never inline bytes).
"""

from __future__ import annotations

import json
from typing import Any

from ..core.ids import utcnow
from .base import Action, ActionResult


class TraceRecorder:
    """Append-only, JSON-serialisable trace for one run."""

    def __init__(self, artifacts: Any | None = None) -> None:
        #: optional ArtifactStore (duck-typed: ``.save(name, content, kind, filename)``).
        self.artifacts = artifacts
        self.run_id: str | None = None
        self._events: list[dict[str, Any]] = []
        self._seq = 0

    # -- lifecycle ----------------------------------------------------------
    def start(self, run_id: str) -> None:
        """Bind the recorder to a run and reset its event log."""
        self.run_id = run_id
        self._events = []
        self._seq = 0

    # -- low-level ----------------------------------------------------------
    def _append(self, kind: str, **data: Any) -> dict[str, Any]:
        self._seq += 1
        entry = {
            "seq": self._seq,
            "ts": utcnow().isoformat(),
            "kind": kind,
            **data,
        }
        self._events.append(entry)
        return entry

    # -- recording API ------------------------------------------------------
    def record_action(self, action: Action, *, checkpoint: str | None = None) -> None:
        self._append("action", checkpoint=checkpoint, action=action.to_dict())

    def record_result(self, result: ActionResult) -> None:
        self._append("result", **result.to_dict())

    def record_screenshot(self, data: bytes, *, label: str = "screenshot") -> str:
        """Persist a screenshot as an artifact and record its path ref.

        Returns the path reference recorded (``"<in-memory>"`` if no store).
        """
        ref = "<in-memory>"
        size = len(data or b"")
        if self.artifacts is not None:
            name = f"computeruse/{self.run_id or 'run'}/{label}-{self._seq + 1}"
            artifact = self.artifacts.save(
                name, data, kind="screenshot", filename=f"{label}.png"
            )
            ref = str(getattr(artifact, "path", name))
        self._append("screenshot", label=label, path=ref, bytes=size, method="screenshot")
        return ref

    def record_error(self, message: str, *, where: str = "") -> None:
        self._append("error", message=message, where=where)

    def record_artifact(self, name: str, path: str, *, kind: str = "file") -> None:
        self._append("artifact", name=name, path=path, artifact_kind=kind)

    def record_note(self, message: str, **data: Any) -> None:
        self._append("note", message=message, **data)

    def record_approval(self, request_id: str, status: str, reason: str) -> None:
        self._append("approval", request_id=request_id, status=status, reason=reason)

    # -- access -------------------------------------------------------------
    def events(self) -> list[dict[str, Any]]:
        return list(self._events)

    def to_json(self) -> str:
        return json.dumps(self._events, default=str)
