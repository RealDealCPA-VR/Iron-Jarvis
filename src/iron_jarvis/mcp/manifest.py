"""The persisted record of what a pack's tools LOOKED LIKE last time (v1.299.0).

THE GAP THIS CLOSES. A pack is trusted as a whole: ``auto_approve`` on a server
(or the global ``mcp_auto_approve``) answers yes for EVERY ``mcp_call``. The user
reviewed the pack's tools when they connected it — and never again. The tool
list is re-read from the server at every boot, add and reload, and nothing kept
the previous list, so a pack update that grows ``delete_repository`` next to
``list_issues`` handed an autonomous agent a destructive tool nobody had seen.
This file is the memory that makes "new since you looked" a fact we can state.

ONE JSON FILE PER SERVER at ``<home>/mcp/manifests/<server>.json``::

    {"server": "github", "at": "...", "tools": [
        {"name": "list_issues", "write_like": false, "risk": "read",
         "schema_sha": "…", "trusted": true},
        {"name": "delete_repo", "write_like": true, "risk": "external_commit",
         "schema_sha": "…", "trusted": false}]}

THE RULES, in the order they were decided:

* The FIRST load of a pack (no manifest yet) trusts everything. The user
  installed that pack knowingly, with that list in front of them; quarantining
  the whole pack on day one would make every connect a second approval round.
* A tool that APPEARS LATER, or CHANGES SHAPE (its ``inputSchema`` hash or its
  write-likeness flips), is NOT trusted until the user says so — ``changed``
  counts because a tool whose arguments grew ``force: true`` is not the tool
  that was reviewed.
* A READ-ONLY tool (``readOnlyHint: true``) is never quarantined, new or not:
  it cannot act, and its output is already fenced as untrusted content by
  :class:`~iron_jarvis.mcp.tools.MCPRemoteTool`. Quarantine exists to stop a
  WRITE nobody approved, not to make reading ask.
* A tool that is GONE is dropped from the manifest. If it comes back it is new
  again — "I saw it once, months ago" is not review.

Only a load that HANDS TOOLS TO THE REGISTRY records (the same rule as the load
record): the read-only ``/test`` probe passes ``record=False`` and never touches
this file, so testing a pack cannot silently pre-trust its new tools.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

__all__ = ["PackManifest", "schema_sha", "tool_entry"]


def schema_sha(schema: Any) -> str:
    """A stable short digest of a tool's ``inputSchema`` (key order ignored)."""
    try:
        blob = json.dumps(schema or {}, sort_keys=True, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        blob = repr(schema)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def tool_entry(spec: dict[str, Any]) -> dict[str, Any]:
    """Normalise one tool spec into the manifest's comparable shape.

    Accepts either a raw MCP ``tools/list`` entry (``inputSchema`` +
    ``annotations``) or an already-classified dict carrying ``write_like`` /
    ``risk``. ``write_like`` defaults to True — an unannotated tool is treated
    as able to act, the same fail-safe as ``RiskClass``'s default.
    """
    from .tools import is_write_like, risk_for  # local: tools imports this module

    name = str(spec.get("name") or "")
    if "write_like" in spec:
        write_like = bool(spec["write_like"])
    else:
        write_like = is_write_like(spec)
    risk = spec.get("risk")
    if not isinstance(risk, str) or not risk:
        risk = risk_for(spec)[0].value
    schema = spec.get("inputSchema")
    if schema is None:
        schema = spec.get("input_schema")
    sha = spec.get("schema_sha") if isinstance(spec.get("schema_sha"), str) else None
    return {
        "name": name,
        "write_like": write_like,
        "risk": risk,
        "schema_sha": sha or schema_sha(schema),
    }


_SAFE = re.compile(r"[^A-Za-z0-9_.-]")


class PackManifest:
    """Read/write the per-server tool manifests under ``<home>/mcp/manifests``."""

    def __init__(self, home: str | Path) -> None:
        self.root = Path(home) / "mcp" / "manifests"

    # ------------------------------------------------------------------ #
    # Storage.
    # ------------------------------------------------------------------ #
    def path(self, server: str) -> Path:
        # The route validates server names; this is the belt to that brace, so
        # a name that somehow carries a separator cannot pick the file.
        safe = _SAFE.sub("_", str(server or "")) or "_"
        return self.root / f"{safe}.json"

    def load(self, server: str) -> dict[str, Any] | None:
        """The saved manifest, or ``None`` when this pack was never recorded."""
        p = self.path(server)
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            # A corrupt file reads as "never recorded" — which trusts the NEXT
            # load wholesale, so say so in the record rather than hide it.
            return None
        if not isinstance(raw, dict) or not isinstance(raw.get("tools"), list):
            return None
        return raw

    def _write(self, server: str, doc: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        p = self.path(server)
        tmp = p.with_name(f".{p.name}.tmp-{os.getpid()}")
        tmp.write_text(json.dumps(doc, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, p)

    # ------------------------------------------------------------------ #
    # Comparison.
    # ------------------------------------------------------------------ #
    def diff(self, server: str, specs: list[dict[str, Any]]) -> dict[str, list[str]]:
        """``{new, gone, changed}`` of ``specs`` against the saved manifest.

        ``changed`` = the tool is in both and its ``write_like`` flipped or its
        ``inputSchema`` digest differs. With no manifest EVERYTHING is ``new``
        — the caller (``record``) is what decides a first load trusts them.
        """
        current = {e["name"]: e for e in (tool_entry(s) for s in specs) if e["name"]}
        saved = self.load(server)
        prior = {
            str(t.get("name")): t
            for t in (saved or {}).get("tools", [])
            if isinstance(t, dict) and t.get("name")
        }
        new = sorted(n for n in current if n not in prior)
        gone = sorted(n for n in prior if n not in current)
        changed = sorted(
            n
            for n, e in current.items()
            if n in prior
            and (
                bool(prior[n].get("write_like", True)) != e["write_like"]
                or str(prior[n].get("schema_sha") or "") != e["schema_sha"]
            )
        )
        return {"new": new, "gone": gone, "changed": changed}

    # ------------------------------------------------------------------ #
    # Recording.
    # ------------------------------------------------------------------ #
    def record(
        self,
        server: str,
        specs: list[dict[str, Any]],
        trusted_names: "set[str] | list[str] | None" = None,
    ) -> dict[str, Any]:
        """Write the manifest for ``server`` from ``specs`` and return it.

        ``trusted_names=None`` applies THE RULE: first load → every tool
        trusted; later loads → a tool keeps the trust it had unless it is new
        or changed; a read-only tool is always trusted (it cannot act). An
        explicit set overrides the rule for the write-like tools only — a
        read-only tool is trusted whatever the caller says, because
        ``quarantined()`` must never name one.
        """
        current = [e for e in (tool_entry(s) for s in specs) if e["name"]]
        saved = self.load(server)
        if trusted_names is None:
            if saved is None:
                trusted = {e["name"] for e in current}
            else:
                d = self.diff(server, specs)
                not_trusted_now = set(d["new"]) | set(d["changed"])
                prior_trusted = {
                    str(t.get("name"))
                    for t in saved.get("tools", [])
                    if isinstance(t, dict) and t.get("trusted") is True
                }
                trusted = {
                    e["name"]
                    for e in current
                    if e["name"] in prior_trusted and e["name"] not in not_trusted_now
                }
        else:
            trusted = set(trusted_names)
        tools = [
            {**e, "trusted": (not e["write_like"]) or e["name"] in trusted}
            for e in current
        ]
        doc = {
            "server": str(server),
            "at": datetime.now(timezone.utc).isoformat(),
            "tools": tools,
        }
        self._write(server, doc)
        return doc

    def trust(self, server: str, tool: str) -> bool:
        """Mark ``tool`` trusted in the saved manifest. False when the pack or
        the tool is not in the manifest (nothing is written then)."""
        saved = self.load(server)
        if saved is None:
            return False
        hit = False
        for t in saved.get("tools", []):
            if isinstance(t, dict) and str(t.get("name")) == str(tool):
                t["trusted"] = True
                hit = True
        if not hit:
            return False
        saved["at"] = datetime.now(timezone.utc).isoformat()
        self._write(server, saved)
        return True

    def quarantined(self, server: str) -> set[str]:
        """Write-like tools in the saved manifest that are NOT trusted. A
        read-only tool is never here, by construction of ``record``."""
        saved = self.load(server)
        if saved is None:
            return set()
        return {
            str(t.get("name"))
            for t in saved.get("tools", [])
            if isinstance(t, dict)
            and t.get("name")
            and bool(t.get("write_like", True))
            and t.get("trusted") is not True
        }
