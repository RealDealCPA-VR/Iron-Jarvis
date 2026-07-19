"""Remote agents — agents the user runs ELSEWHERE (§11/§12 extension).

A *remote agent* is not run by this daemon at all: it lives on another machine
the user controls — their own Hermes on a second PC, an OpenClaw instance, or
any OpenAI-compatible chat endpoint — and this platform simply hands it a task
over HTTP and relays the reply back. The user explicitly registers the endpoint,
so a LAN/localhost target is a FEATURE, not an SSRF risk: remote-agent calls
therefore do NOT pass through :func:`assert_safe_webhook_url` (which rejects
private addresses by default).

Three shapes are supported:

* ``openai-chat`` — POST ``{base_url}/chat/completions`` (or ``base_url`` as-is
  if it already ends in ``completions``) with an OpenAI ``chat/completions``
  body, ``Authorization: Bearer <secret>``, and parse
  ``choices[0].message.content``.
* ``openai-responses`` — POST ``{base_url}/responses`` with an OpenAI
  **Responses API** body (``input``, not ``messages``) and parse
  ``output_text`` / ``output[].content[].text``. A growing number of agent
  endpoints speak only this dialect; sending them a chat body earns a
  ``400 Missing 'input' field``, which is the live symptom that added it here.
* ``http-task`` — POST ``base_url`` with ``{"task": ...}`` (+ optional bearer)
  and accept a ``{"result": ...}`` or ``{"output": ...}`` reply.

A 4xx whose body complains about the *other* dialect's field is answered with
the fix ("this endpoint expects the Responses API — set kind to
openai-responses") rather than a bare status code: the shape mismatch is
recognisable, so the user should not have to guess it.

The credential is stored ONLY in the encrypted secrets vault (referenced by
``secret_name``); it is resolved at call time and NEVER logged.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlmodel import Field, SQLModel, select

from ..core.db import session_scope
from ..core.ids import new_id, utcnow
from ..tools.base import Tool, ToolContext, ToolResult

if TYPE_CHECKING:  # avoid importing the heavy SQLAlchemy symbol at runtime
    from sqlalchemy import Engine

#: ``secret_resolver(name) -> str | None`` — resolves a vault secret name to its
#: plaintext value (wire ``platform.secrets.get``). Kept injectable for tests.
SecretResolver = Callable[[str], "str | None"]

#: The remote-agent transports.
KINDS = ("http-task", "openai-chat", "openai-responses")


def _responses_text(data: Any) -> str:
    """Text out of an OpenAI **Responses API** reply.

    Servers vary: some return the convenience field ``output_text``, others only
    the structured ``output[]`` items. Both are read, and unknown item types are
    skipped rather than guessed at.
    """
    if not isinstance(data, dict):
        return ""
    direct = data.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct
    if isinstance(direct, list):  # some servers send a list of strings
        joined = "".join(p for p in direct if isinstance(p, str))
        if joined.strip():
            return joined
    parts: list[str] = []
    for item in data.get("output") or []:
        if not isinstance(item, dict) or item.get("type") not in (None, "message"):
            continue
        for part in item.get("content") or []:
            if isinstance(part, dict) and part.get("type") in ("output_text", "text"):
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
    return "".join(parts)


def _dialect_hint(kind: str, body: str) -> str:
    """Turn a shape-mismatch 4xx into the setting that fixes it.

    ``Missing 'input' field`` from a chat-shaped POST means the endpoint speaks
    the Responses API, and vice versa. The user should not have to know that —
    the live report was exactly this error with correct credentials.
    """
    low = (body or "").lower()
    if kind == "openai-chat" and "input" in low and "missing" in low:
        return (
            "this endpoint expects the OpenAI Responses API — change this agent's "
            "kind to 'openai-responses' (it sends `input` instead of `messages`)"
        )
    if kind == "openai-responses" and "messages" in low and "missing" in low:
        return (
            "this endpoint expects OpenAI chat/completions — change this agent's "
            "kind to 'openai-chat' (it sends `messages` instead of `input`)"
        )
    if kind == "http-task" and ("input" in low or "messages" in low) and "missing" in low:
        return (
            "this looks like an OpenAI-compatible endpoint, not a plain task "
            "webhook — set kind to 'openai-responses' or 'openai-chat'"
        )
    return ""


class RemoteAgentRecord(SQLModel, table=True):
    """A remote agent endpoint the user registered. The secret lives in the
    vault (``secret_name``), never here."""

    id: str = Field(default_factory=lambda: new_id("rem"), primary_key=True)
    name: str = Field(index=True, unique=True)
    base_url: str = ""
    kind: str = "http-task"  # one of KINDS
    secret_name: str | None = None  # vault key for the bearer token (nullable)
    model: str | None = None  # model id for openai-chat endpoints (nullable)
    enabled: bool = True
    timeout_s: int = 120
    created_at: datetime = Field(default_factory=utcnow)


class RemoteAgentRegistry:
    """CRUD + invocation for user-registered remote agents.

    Constructed cheaply from the shared engine; ensures its own table exists so
    it works even before ``init_db`` has seen this module.
    """

    def __init__(self, engine: "Engine") -> None:
        self.engine = engine
        # Self-heal: create the table if init_db hasn't (checkfirst = idempotent).
        try:
            RemoteAgentRecord.__table__.create(engine, checkfirst=True)
        except Exception:  # noqa: BLE001 — already exists / created concurrently
            pass

    # --- CRUD -------------------------------------------------------------

    def list(self) -> list[RemoteAgentRecord]:
        with session_scope(self.engine) as db:
            rows = list(db.exec(select(RemoteAgentRecord)))
            for r in rows:
                db.expunge(r)
        return sorted(rows, key=lambda r: r.name)

    def get(self, name: str) -> RemoteAgentRecord | None:
        with session_scope(self.engine) as db:
            row = db.exec(
                select(RemoteAgentRecord).where(RemoteAgentRecord.name == name)
            ).first()
            if row is not None:
                db.expunge(row)
            return row

    def upsert(
        self,
        name: str,
        base_url: str,
        kind: str,
        *,
        secret_name: str | None = None,
        model: str | None = None,
        enabled: bool = True,
        timeout_s: int = 120,
    ) -> RemoteAgentRecord:
        """Create or update a remote agent (upsert by unique ``name``)."""
        with session_scope(self.engine) as db:
            row = db.exec(
                select(RemoteAgentRecord).where(RemoteAgentRecord.name == name)
            ).first()
            if row is None:
                row = RemoteAgentRecord(name=name)
            row.base_url = base_url
            row.kind = kind
            row.secret_name = secret_name
            row.model = model
            row.enabled = enabled
            row.timeout_s = timeout_s
            db.add(row)
            db.commit()
            db.refresh(row)
            db.expunge(row)
            return row

    def remove(self, name: str) -> bool:
        """Delete a remote agent by name; True if a row was removed."""
        with session_scope(self.engine) as db:
            row = db.exec(
                select(RemoteAgentRecord).where(RemoteAgentRecord.name == name)
            ).first()
            if row is None:
                return False
            db.delete(row)
            db.commit()
            return True

    def set_enabled(self, name: str, enabled: bool) -> RemoteAgentRecord | None:
        with session_scope(self.engine) as db:
            row = db.exec(
                select(RemoteAgentRecord).where(RemoteAgentRecord.name == name)
            ).first()
            if row is None:
                return None
            row.enabled = enabled
            db.add(row)
            db.commit()
            db.refresh(row)
            db.expunge(row)
            return row

    # --- invocation -------------------------------------------------------

    async def run(
        self,
        record: RemoteAgentRecord,
        task: str,
        secret_resolver: SecretResolver,
        *,
        timeout_s: int | None = None,
    ) -> dict[str, Any]:
        """Hand ``task`` to a remote agent and relay its reply.

        Returns ``{ok, result, detail}`` — fail-closed with an honest ``detail``
        on timeout, a non-2xx status, or a reply that doesn't match the shape.
        The secret is resolved here and NEVER logged.
        """
        import httpx

        timeout = timeout_s or record.timeout_s or 120
        token = ""
        if record.secret_name:
            try:
                token = (secret_resolver(record.secret_name) or "") if secret_resolver else ""
            except Exception:  # noqa: BLE001 — a vault miss just means no auth header
                token = ""
        headers: dict[str, str] = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        if record.kind == "openai-chat":
            base = (record.base_url or "").rstrip("/")
            url = base if base.endswith("completions") else base + "/chat/completions"
            payload: dict[str, Any] = {
                "model": record.model or "",
                "messages": [{"role": "user", "content": task}],
            }
        elif record.kind == "openai-responses":
            base = (record.base_url or "").rstrip("/")
            url = base if base.endswith("responses") else base + "/responses"
            # The Responses API takes `input`, not `messages`. The plain-string
            # form is accepted by every implementation of it and avoids the
            # richer content-part shape, which servers disagree about.
            payload = {"model": record.model or "", "input": task}
        else:  # http-task (default / unknown kind falls here)
            url = record.base_url or ""
            payload = {"task": task}

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, json=payload, headers=headers)
        except Exception as exc:  # noqa: BLE001 — timeout / connection / DNS
            return {"ok": False, "result": "", "detail": f"request failed: {exc}"}

        status = getattr(resp, "status_code", 0)
        if status // 100 != 2:
            snippet = ""
            try:
                snippet = (resp.text or "")[:200]
            except Exception:  # noqa: BLE001
                snippet = ""
            detail = f"remote returned HTTP {status}"
            if snippet:
                detail += f": {snippet}"
            hint = _dialect_hint(record.kind, snippet)
            if hint:
                detail += f" — {hint}"
            return {"ok": False, "result": "", "detail": detail}

        try:
            data = resp.json()
        except Exception:  # noqa: BLE001 — not JSON
            return {"ok": False, "result": "", "detail": "remote returned a non-JSON body"}

        if record.kind == "openai-chat":
            try:
                content = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError):
                content = None
            if not isinstance(content, str) or not content.strip():
                return {
                    "ok": False,
                    "result": "",
                    "detail": "remote reply had no choices[0].message.content",
                }
            return {"ok": True, "result": content, "detail": "ok"}

        if record.kind == "openai-responses":
            content = _responses_text(data)
            if not content.strip():
                return {
                    "ok": False,
                    "result": "",
                    "detail": "remote reply had no output_text / output[].content[].text",
                }
            return {"ok": True, "result": content, "detail": "ok"}

        # http-task: accept {result} or {output}
        result = None
        if isinstance(data, dict):
            result = data.get("result")
            if result is None:
                result = data.get("output")
        if not isinstance(result, str):
            return {
                "ok": False,
                "result": "",
                "detail": "remote reply had no 'result' or 'output' string field",
            }
        return {"ok": True, "result": result, "detail": "ok"}

    async def test(
        self, record: RemoteAgentRecord, secret_resolver: SecretResolver
    ) -> dict[str, Any]:
        """Cheap reachability probe: ask the remote to reply 'ok', short timeout."""
        probe = await self.run(
            record,
            "reply with the single word: ok",
            secret_resolver,
            timeout_s=min(record.timeout_s or 120, 15),
        )
        if probe.get("ok"):
            return {"ok": True, "detail": f"{record.name} replied"}
        return {"ok": False, "detail": probe.get("detail") or "probe failed"}


class DelegateRemoteTool(Tool):
    """Delegate a task to a registered remote agent running elsewhere."""

    name = "delegate_remote"
    description = (
        "Hand a task to a REMOTE agent the user registered on another machine "
        "(their own Hermes/OpenClaw or any OpenAI-compatible endpoint) and "
        "return its reply. Args: agent (the registered remote agent's name) and "
        "task (the self-contained instruction)."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "agent": {"type": "string"},
            "task": {"type": "string"},
        },
        "required": ["agent", "task"],
    }
    permission_key = "delegate_remote"
    #: The remote's reply is externally-sourced — fence it as untrusted content.
    returns_untrusted_content = True

    def __init__(self, platform) -> None:
        self.platform = platform

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        agent = (args.get("agent") or "").strip()
        task = args.get("task") or ""
        if not agent:
            return ToolResult(ok=False, error="`agent` is required")
        registry = RemoteAgentRegistry(self.platform.engine)
        record = registry.get(agent)
        if record is None:
            return ToolResult(ok=False, error=f"unknown remote agent '{agent}'")
        if not record.enabled:
            return ToolResult(ok=False, error=f"remote agent '{agent}' is disabled")
        res = await registry.run(record, task, self.platform.secrets.get)
        if res.get("ok"):
            return ToolResult(
                ok=True,
                output=res.get("result") or "",
                data={"agent": agent, "kind": record.kind},
            )
        return ToolResult(
            ok=False,
            error=res.get("detail") or "remote agent call failed",
            data={"agent": agent, "kind": record.kind},
        )


def register_remote_agent_tool(platform) -> None:
    """Register the ``delegate_remote`` tool on the platform's tool registry.

    Coordinator wiring (platform.py, one line near the DelegateTool register):
        ``from .agents.remote import register_remote_agent_tool``
        ``register_remote_agent_tool(platform)``
    """
    platform.registry.register(DelegateRemoteTool(platform))
