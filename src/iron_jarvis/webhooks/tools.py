"""Agent-facing webhook tool (§19 tool interface).

``webhook_add`` lets an agent register its *own* inbound trigger or outbound
delivery through the tool loop. It is constructed with the assembled ``platform``
(like :class:`~iron_jarvis.agents.delegate_tool.DelegateTool`) and acts on
``platform.inbound_webhooks`` / ``platform.outbound_webhooks``, resolving any
secret via ``platform.secrets``. ``webhook_tools(platform)`` builds it for
registration.

For an inbound webhook the tool registers a default handler that simply
publishes a ``webhook.received`` event onto the platform event bus (so the rest
of the system — observability, outbound deliveries, notifier — can react).
"""

from __future__ import annotations

import ipaddress
import re
from typing import Any
from urllib.parse import urlsplit

from ..tools.base import Reversibility, SpendCheck, Tool, ToolContext, ToolResult

#: Fourth fix plan PG1 / C-PG-2: why an address card asks every time.
ADDRESS_REASON = "address"
_LOOPBACK_NAMES = frozenset({"localhost", "localhost.localdomain"})
_DRIVE = re.compile(r"^[A-Za-z]:(?:[\\/]|$)")


def _is_folder(value: str) -> bool:
    """A folder path (a drive, a share, a POSIX or home path), not an address."""
    v = value.strip()
    return bool(_DRIVE.match(v)) or v.startswith(("\\\\", "//", "/", "~"))


def _host(value: str) -> str:
    """The address's host, lower-case ("" when there is none). An address
    typed without a scheme ("gpu-box:8000") is read as one."""
    v = value.strip()
    if not v:
        return ""
    try:
        return str(ipaddress.ip_address(v.strip("[]")))  # a bare IP ("::1")
    except ValueError:
        pass
    try:
        return (urlsplit(v if "://" in v else "//" + v).hostname or "").lower()
    except ValueError:
        return ""


def _on_this_pc(host: str) -> bool:
    if host in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _folder_place(value: str) -> str:
    """A folder named by its drive or share and the folder under it
    (``D:\\Backups``, ``\\\\nas\\share``, ``/mnt``); "a folder in …" when the
    path goes deeper."""
    v = value.strip()
    parts = [p for p in re.split(r"[\\/]+", v) if p]
    if not parts:
        return "another folder"
    if v.startswith(("\\\\", "//")):
        keep, shown = 2, "\\\\" + "\\".join(parts[:2])
    elif _DRIVE.match(v):
        keep, shown = 2, parts[0] + "\\" + (parts[1] if len(parts) > 1 else "")
    else:
        keep = 2 if parts[0] == "~" else 1
        shown = ("" if parts[0] == "~" else "/") + "/".join(parts[:keep])
    return shown if len(parts) <= keep else f"a folder in {shown}"


def outbound_address_check(
    new_url: str, *, old_url: str | None = None, what: str = "Iron Jarvis's events", note: str = ""
) -> SpendCheck | None:
    """THE check for every tool that sets WHERE Iron Jarvis's data goes from
    now on (fourth fix plan PG1; ``tools/destinations.py`` lists them).

    v1.502.0 (north-star D6) built it for an outbound webhook; it is one helper
    now, so a webhook's new address, a model server's, the speech server's and
    the backup copy folder are asked about the same way, in every lane, through
    the seam they all read (the runtime pauses, the stream lane cards, the POST
    lane refuses, a workflow step refuses in words):

    * "Allow once" and Deny only, every time (``once_only``): no conversation
      grant, standing approval or "Run without asking" covers the next place;
    * "Don't ask" is the user's own posture and is kept (``dont_ask_skips``);
    * nobody there to answer (a schedule, a rule, the phone): refused in words
      (``attended_only``);
    * ``reason`` "address", so the card says why it asks every time;
    * the words name the HOST only (a webhook address can be its own secret),
      or a folder's drive or share.

    ``None`` (no opinion) for a new address on THIS PC (loopback: nothing
    leaves the PC). Never raises."""
    value = str(new_url or "").strip()
    folder = _is_folder(value)
    if folder:
        where = _folder_place(value)
        old_where = _folder_place(str(old_url)) if str(old_url or "").strip() else ""
    else:
        host = _host(value)
        if host and _on_this_pc(host):
            return None
        where = host or "another address"
        old_host = _host(str(old_url or ""))
        old_where = old_host
        if host and old_host == host and value != str(old_url or "").strip():
            # Two addresses on one host (two Slack webhooks): say so.
            where, old_where = f"a different address on {host}", ""
    # "…whole folder, database included, to D:\Backups": an aside in *what*
    # is closed before "to".
    words = f"{'Copy' if folder else 'Send'} {what}{',' if ',' in what else ''} to {where} from now on"
    if old_where and old_where != where:
        words += f", instead of {old_where}"
    if note:
        words += f" ({note})"
    return SpendCheck(
        ask=True,
        words=words,
        # Each one is its own question: "Allow once" and Deny, never a
        # standing grant that would cover the next address.
        once_only=True,
        reason=ADDRESS_REASON,
        dont_ask_skips=True,
        attended_only=True,
    )


class WebhookAddTool(Tool):
    """Register an inbound webhook trigger or an outbound webhook delivery."""

    name = "webhook_add"
    description = (
        "Register a webhook. direction 'inbound' (default) wires a `slug` that, "
        "when POSTed to, publishes a `webhook.received` event carrying the body. "
        "direction 'outbound' POSTs matching events to `target_url` (required) "
        "for the given `event_types`. Pass `secret_name` to HMAC-sign/verify "
        "using a stored secret. Returns the slug and direction."
    )
    permission_key = "webhook_add"
    # Calm UI redesign (AUDIT §6.3): a webhook made from chat has an Undo (it
    # removes the webhook, or puts back the one a re-used slug replaced) —
    # settings.records holds the snapshot/restore.
    reversibility = Reversibility.REVERSIBLE

    def redact_args(self, args: dict[str, Any]) -> dict[str, Any]:
        """Fourth fix plan (D6, lead's review of PG1): the card, the ledger and
        the chat history carry the new address's HOST only, as
        ``webhook_update`` does — a webhook address can be its own secret."""
        from ..settings.records import _webhook_host_only

        out = dict(args or {})
        url = out.get("target_url")
        if isinstance(url, str) and url.strip():
            out["target_url"] = _webhook_host_only(url)
        return out

    input_schema = {
        "type": "object",
        "properties": {
            "slug": {"type": "string"},
            "direction": {"type": "string", "enum": ["inbound", "outbound"]},
            "target_url": {"type": "string"},
            "event_types": {"type": "array", "items": {"type": "string"}},
            "secret_name": {"type": "string"},
        },
        "required": ["slug"],
    }

    def __init__(self, platform) -> None:
        self.platform = platform

    async def spend_check(self, args: dict[str, Any], ctx: ToolContext) -> SpendCheck | None:
        """v1.502.0 (north-star D6): an OUTBOUND webhook sends Iron Jarvis's
        events to another address from then on, so it is asked about every
        time — in every lane, through the seam they all read (the runtime
        pauses, the stream lane cards, the POST lane refuses). The card names
        the address's HOST only: a webhook address can be its own secret.
        An unattended run is refused in words (``attended_only``); "Don't
        ask" is the user's own posture and is kept (``dont_ask_skips``), as a
        chat's ask is. An inbound webhook has no opinion (allow, as before)."""
        if args.get("direction", "inbound") != "outbound":
            return None
        # Fourth fix plan PG1: the one check every place-setting tool reads.
        return outbound_address_check(
            str(args.get("target_url") or ""), note="a new outbound webhook"
        )

    async def capture_undo(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any] | None:
        from ..settings.records import capture_create_undo

        return await capture_create_undo(self.platform, "webhook", str(args.get("slug") or ""))

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        slug = args.get("slug") or ""
        if not slug:
            return ToolResult(ok=False, error="slug is required")
        direction = args.get("direction", "inbound")

        secret_name = args.get("secret_name")
        secret = self.platform.secrets.get(secret_name) if secret_name else None

        if direction == "outbound":
            target_url = args.get("target_url")
            if not target_url:
                return ToolResult(
                    ok=False, error="target_url is required for an outbound webhook"
                )
            try:
                self.platform.outbound_webhooks.register(
                    slug,
                    target_url,
                    args.get("event_types") or [],
                    secret=secret,
                    secret_name=secret_name,  # persist the real vault key (survives restart)
                )
            except ValueError as exc:
                return ToolResult(ok=False, error=str(exc))
        else:
            async def handler(body, _slug=slug):
                await self.platform.event_bus.publish(
                    "webhook.received", {"slug": _slug, "body": body}
                )
                # v1.122.0: fire bound reflex rules immediately — the same
                # skip-until-restart bug the create route fixed lived here too
                # for agent-created webhooks.
                fired = []
                router = getattr(self.platform, "reflex_router", None)
                if router is not None:
                    try:
                        # Leap 05-a: through the start hub (on_webhook, as before).
                        from ..triggers.starts import hub_for as _hub_for

                        fired = await _hub_for(router).signal("webhook", fields={"slug": _slug, "body": body})
                    except Exception:  # noqa: BLE001 — never break the ack
                        pass
                from ..reflex.router import summarize_fires  # AE6: failed != fired

                return {"ok": True, **summarize_fires(fired)}

            self.platform.inbound_webhooks.register(
                slug, handler, secret=secret, secret_name=secret_name
            )

        data: dict[str, Any] = {"slug": slug, "direction": direction}
        card = _created_card(slug, direction)
        if card:
            data["record_change"] = card
        return ToolResult(
            ok=True,
            output=f"registered {direction} webhook '{slug}'",
            data=data,
        )


def _created_card(slug: str, direction: str) -> dict[str, Any] | None:
    """The reply's "Webhook created … [Undo]" card — only when this call
    captured an Undo (a direct ``execute`` keeps its old ``data``)."""
    try:
        from ..settings.records import create_card

        return create_card("webhook", slug, f"{direction} webhook")
    except Exception:  # noqa: BLE001 — a card never fails the tool
        return None


def webhook_tools(platform) -> list[Tool]:
    """Build the webhook tool bound to the assembled ``platform``."""
    return [WebhookAddTool(platform)]
