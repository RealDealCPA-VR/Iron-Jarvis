"""Audit routes (TX-01, §30): the canonical audit timeline + export.

Read-only projection over the event log + tool-invocation ledger into ONE
time-ordered ``AuditEntry`` stream (see :class:`eval.observability.AuditTimeline`).
Token-guarded by the app's auth middleware like every other route; all
filtering + keyset pagination happens IN SQL, never by materializing the
unbounded tables.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI


def register(app: FastAPI, d) -> None:
    """Attach these routes to *app*; ``d`` is the create_app deps object."""

    @app.get("/audit")
    def audit(
        session_id: str | None = None,
        type: str | None = None,
        tool: str | None = None,
        actor: str | None = None,
        kind: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 100,
        before: str | None = None,
    ) -> dict[str, Any]:
        """Canonical audit timeline, newest-first, keyset-paginated.

        Returns ``{entries: [AuditEntry], next_cursor, total?}`` — ``total`` only
        on the first page (``before`` unset). Pass ``before=<next_cursor>`` to
        walk older entries. ``limit`` defaults to 100, hard-capped at 500.
        """
        return d.platform.observability.timeline(
            session_id=session_id,
            type=type,
            tool=tool,
            actor=actor,
            kind=kind,
            since=since,
            until=until,
            limit=limit,
            before=before,
        )

    @app.get("/audit/export")
    def audit_export(
        session_id: str | None = None,
        type: str | None = None,
        tool: str | None = None,
        actor: str | None = None,
        kind: str | None = None,
        since: str | None = None,
        until: str | None = None,
        format: str = "md",
    ):
        """Export the (filtered) timeline as markdown or JSON.

        Bounded to the newest 500 matching entries — the same hard cap the query
        API enforces — so an export never scans the unbounded tables.
        """
        result = d.platform.observability.timeline(
            session_id=session_id,
            type=type,
            tool=tool,
            actor=actor,
            kind=kind,
            since=since,
            until=until,
            limit=500,
        )
        entries = result.get("entries", [])
        if format == "json":
            return {"entries": entries, "count": len(entries)}

        from fastapi.responses import PlainTextResponse

        lines = [
            "# Iron Jarvis audit timeline",
            "",
            f"- entries: {len(entries)}",
        ]
        for label, value in (
            ("session", session_id),
            ("kind", kind),
            ("tool", tool),
            ("actor", actor),
        ):
            if value:
                lines.append(f"- {label}: {value}")
        lines += [
            "",
            "| ts | kind | actor | tool | ok | summary |",
            "|---|---|---|---|---|---|",
        ]
        for e in entries:
            summary = str(e.get("summary") or "").replace("|", "\\|")
            lines.append(
                f"| {e.get('ts', '')} | {e.get('kind', '')} | {e.get('actor', '')} "
                f"| {e.get('tool', '') or ''} | {e.get('ok')} | {summary} |"
            )
        return PlainTextResponse("\n".join(lines), media_type="text/markdown")
