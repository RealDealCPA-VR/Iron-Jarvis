"""Integration tools (§19 tool interface).

Exposes the integration registry to agents:

* ``integration_list`` — list integrations with enabled/configured status
  (never secret values).
* ``integration_test`` — probe one integration's connectivity by id.
* ``rest_call`` (v1.357.0, G84) — ONE call to a path on one of the user's REST
  hookups, after an approval card (ASK tier).

All wrap an :class:`IntegrationRegistry` plus the injected ``secret_resolver``.
``integration_tools(registry, secret_resolver)`` builds them for registration
in the tool registry.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from ..tools.base import SpendCheck, Tool, ToolContext, ToolResult
from .base import SecretResolver
from .builtin import CALL_METHODS
from .hookups import find_hookup, hookup_names
from .registry import IntegrationRegistry


class IntegrationListTool(Tool):
    """List registered integrations and their status (§19)."""

    name = "integration_list"
    description = (
        "List registered external-service integrations with their enabled and "
        "configured status. Never returns secret values."
    )
    input_schema = {"type": "object", "properties": {}}

    def __init__(
        self, registry: IntegrationRegistry, secret_resolver: SecretResolver
    ) -> None:
        self._registry = registry
        self._resolver = secret_resolver

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        status = self._registry.list_status()
        lines = [
            f"{s['id']} ({s['kind']}) enabled={s['enabled']} configured={s['configured']}"
            for s in status
        ]
        return ToolResult(
            ok=True,
            output="\n".join(lines) if lines else "(no integrations registered)",
            data={"integrations": status},
        )


class IntegrationTestTool(Tool):
    """Test connectivity for one integration by id (§19)."""

    name = "integration_test"
    description = "Test connectivity for a configured integration by its id."
    input_schema = {
        "type": "object",
        "properties": {"id": {"type": "string"}},
        "required": ["id"],
    }

    def __init__(
        self, registry: IntegrationRegistry, secret_resolver: SecretResolver
    ) -> None:
        self._registry = registry
        self._resolver = secret_resolver

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        integration_id = str(args.get("id", "")).strip()
        if not integration_id:
            return ToolResult(ok=False, error="missing required arg 'id'")
        result = self._registry.test(integration_id, self._resolver)
        return ToolResult(
            ok=bool(result.get("ok")),
            output=str(result.get("detail", "")),
            data=result,
        )


# --------------------------------------------------------------------------- #
# v1.357.0 (G84): a REST hookup Jarvis can call.
# --------------------------------------------------------------------------- #

#: The most of an answer the model is shown (characters, after masking).
REST_RESULT_CHARS = 6_000
#: How much of the answer is masked before the cut (the cut is far shorter, so
#: a secret can never be left half-masked at the edge — the v1.349.0 rule).
_MASK_WINDOW = 16_000
#: A body shown on the approval card / kept in the ledger (characters).
_BODY_SHOWN = 2_000
#: Where hookups are added, for a refusal that has none to offer.
HOOKUPS_PLACE = "Settings › Connections (More ways to connect)"


def _method(args: dict[str, Any]) -> str:
    """The method a call will send: the model's, upper-cased; GET when absent."""
    raw = args.get("method") if isinstance(args, dict) else None
    return str(raw or "GET").strip().upper() or "GET"


def _has_body(value: Any) -> bool:
    return value not in (None, "", {}, [])


def _masked(text: str, cap: int) -> str:
    from ..detections.redact import mask

    return str(mask(str(text or "")[:_MASK_WINDOW]))[:cap]


def _answer_text(text: str, content_type: str) -> str:
    """A JSON answer as one compact JSON line (fewer characters for the same
    record); anything else as the service sent it."""
    raw = str(text or "")
    looks_json = "json" in str(content_type or "").lower() or raw.lstrip()[:1] in ("{", "[")
    if looks_json:
        try:
            return json.dumps(json.loads(raw), ensure_ascii=False)
        except (TypeError, ValueError):
            return raw
    return raw


class RestCallTool(Tool):
    """ONE call to one of the user's REST hookups (v1.357.0, G84).

    A hookup is a REST integration the user saved with its address and key
    (``POST /integrations``). Until this release it could only be TESTED; this
    tool calls it: a PATH under the hookup's own address — never an arbitrary
    web address (``builtin.build_call_url``) — with the key from the vault
    through the injected secret resolver, sent the way the Test button sends
    it. The request runs OFF the event loop and is bounded (``builtin.CALL_*``);
    the answer is cut to :data:`REST_RESULT_CHARS` and masked with
    ``detections.redact.mask``; the key's value is never in a log, an event, a
    result or an error (the call replaces it in every string it returns).

    ASK tier (``core/config.py``), kept away from a low-trust run
    (``core/trust.LOW_TRUST_DENY``), reaching outside this PC
    (``providers/egress.reaches_outside`` — a local-only chat cards every call),
    and ask-armed by the stream chat lane when a message names a saved hookup
    (``hookups.named_hookup_ask_tools``). A GET is an ordinary ask; any other
    method CHANGES something on the user's service, so it asks on EVERY call
    whatever the posture or an earlier "allow for this chat" — through the
    one existing "ask before every call" hook, :meth:`spend_check`, whose
    words put the method and path on the card. The answer is the service's
    text, so it is fenced and scanned like a web page
    (``returns_untrusted_content``)."""

    name = "rest_call"
    description = (
        "Call one of the user's own saved web services: a REST API they saved "
        "with its address and key. Give the service's name and a PATH on it (like "
        "/contacts/42 or /deals?stage=won), never a full web address: the "
        "service's own address and key are always used. method is GET to read; "
        "use POST, PUT, PATCH or DELETE (with a JSON body) only when the user "
        "asked to change something there. The user approves each call."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "service": {
                "type": "string",
                "description": "The saved web service's name (or id), as the user named it.",
            },
            "path": {
                "type": "string",
                "description": "A path under the service's address, e.g. /contacts/42 "
                "(a ?query is allowed). Not a full web address.",
            },
            "method": {
                "type": "string",
                "enum": list(CALL_METHODS),
                "description": "GET (the default) reads; the others change things.",
            },
            "body": {
                "type": "object",
                "description": "JSON body for POST, PUT or PATCH.",
            },
        },
        "required": ["service", "path"],
    }
    returns_untrusted_content = True

    def __init__(
        self, registry: IntegrationRegistry, secret_resolver: SecretResolver
    ) -> None:
        self._registry = registry
        self._resolver = secret_resolver

    def spec(self) -> dict[str, Any]:
        """The schema, with the user's hookups NAMED in the description (names
        only — an in-memory read of the registry's specs; never an address or
        a key), so the model can name the one the user meant."""
        out = super().spec()
        names = hookup_names(self._registry)
        if names:
            out["description"] = f"{self.description} The user's saved web services: {', '.join(names)}."
        return out

    def redact_args(self, args: dict[str, Any]) -> dict[str, Any]:
        """What the card shows and the ledger keeps: the service, the method
        THAT WILL BE SENT (GET when the model gave none — the card always says
        it), the path, and a masked, cut body. Nothing else the model sent is
        used, so nothing else is shown."""
        a = args if isinstance(args, dict) else {}
        out: dict[str, Any] = {
            "service": str(a.get("service") or ""),
            "method": _method(a),
            "path": str(a.get("path") or ""),
        }
        if _has_body(a.get("body")):
            try:
                text = json.dumps(a.get("body"), ensure_ascii=False, default=str)
            except Exception:  # noqa: BLE001
                text = str(a.get("body"))
            out["body"] = _masked(text, _BODY_SHOWN)
        return out

    async def spend_check(
        self, args: dict[str, Any], ctx: ToolContext
    ) -> "SpendCheck | None":
        """A call that CHANGES something (any method but GET) asks every time,
        with the method and path on the card; a GET has no opinion (the
        ordinary ask decides). An unknown method has no opinion either —
        :meth:`execute` refuses it in words."""
        a = args if isinstance(args, dict) else {}
        verb = _method(a)
        if verb == "GET" or verb not in CALL_METHODS:
            return None
        spec = find_hookup(self._registry, str(a.get("service") or ""))
        name = str(getattr(spec, "display_name", "") or a.get("service") or "the service")
        path = str(a.get("path") or "/")
        return SpendCheck(
            ask=True,
            words=f"{verb} {path} on {name}: this changes something on that service.",
            cost_note=f"{verb} on {name}",
        )

    def _call_blocking(self, iid: str, method: str, path: str, body: Any) -> dict[str, Any]:
        """The DB reads and the request — run OFF the loop. Never raises."""
        try:
            record = self._registry.get_record(iid)
            if record is None or not record.enabled:
                return {"error": "it is turned off"}
            integration = self._registry.instantiate(iid, self._resolver)
            call = getattr(integration, "call", None)
            if call is None:
                return {"error": "this kind of saved service cannot be called"}
            return dict(call(method, path, body))
        except Exception as exc:  # noqa: BLE001 — a call never crashes the turn
            return {"error": f"the call failed ({type(exc).__name__})"}

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        a = args if isinstance(args, dict) else {}
        verb = _method(a)
        if verb not in CALL_METHODS:
            return ToolResult(
                ok=False,
                error=f"{verb} is not a method a call can send (use {', '.join(CALL_METHODS)}).",
            )
        asked = str(a.get("service") or "").strip()
        spec = find_hookup(self._registry, asked)
        if spec is None:
            names = hookup_names(self._registry)
            have = (
                f" The saved ones are: {', '.join(names)}."
                if names
                else f" None is saved yet; one is added in {HOOKUPS_PLACE}."
            )
            return ToolResult(ok=False, error=f"No saved web service is called '{asked[:80]}'.{have}")
        body = a.get("body")
        if verb == "GET" and _has_body(body):
            return ToolResult(
                ok=False,
                error="A GET sends no body; use POST, PUT or PATCH to send one.",
            )
        iid = str(spec.id)
        name = str(getattr(spec, "display_name", "") or iid)
        path = str(a.get("path") or "")
        res = await asyncio.to_thread(
            self._call_blocking, iid, verb, path, body if _has_body(body) else None
        )
        label = f"{verb} {path or '/'} on {name}"
        data: dict[str, Any] = {
            "service": iid,
            "name": name,
            "method": verb,
            "path": path,
            "status": res.get("status"),
            "content_type": str(res.get("content_type") or ""),
            "truncated": bool(res.get("truncated")),
        }
        if res.get("error"):
            return ToolResult(
                ok=False, error=_masked(f"{label}: {res['error']}.", 600), data=data
            )
        text = _answer_text(str(res.get("text") or ""), data["content_type"])
        # Masked BEFORE the cut (a cut never leaves half a secret); "cut" is
        # judged on the masked text, whose length masking itself changes.
        masked = _masked(text, _MASK_WINDOW)
        shown = masked[:REST_RESULT_CHARS]
        cut = bool(res.get("truncated")) or len(text) > _MASK_WINDOW or len(masked) > len(shown)
        status = res.get("status")
        head = f"{label} answered {status} {str(res.get('reason') or '').strip()}".rstrip()
        output = head + "\n" + (shown or "(an empty answer)")
        if cut:
            output += "\n[the answer was longer; this is its start]"
            data["truncated"] = True
        if res.get("ok"):
            return ToolResult(ok=True, output=output, data=data)
        return ToolResult(
            ok=False,
            output=output,
            error=_masked(f"{head}: {shown[:500]}", 700),
            data=data,
        )


def integration_tools(
    registry: IntegrationRegistry, secret_resolver: SecretResolver
) -> list[Tool]:
    """Build the integration tools bound to ``registry`` + ``secret_resolver``."""
    return [
        IntegrationListTool(registry, secret_resolver),
        IntegrationTestTool(registry, secret_resolver),
        # v1.357.0 (G84): a REST hookup Jarvis can call.
        RestCallTool(registry, secret_resolver),
    ]
