"""A stand-in for Softeria's Microsoft 365 MCP server (v1.356.0, G60 pins).

No network, no Microsoft, no sign-in. It speaks line-delimited JSON-RPC 2.0 the
way ``iron_jarvis.mcp.client.StdioTransport`` expects and copies the REAL
server's tool surface where the card depends on it:

* ``TOOLS`` is what @softeria/ms-365-mcp-server 0.170.2 lists for the
  ``outlook,onedrive`` presets (119 tools, read off the real server's own
  ``tools/list`` on 2026-10-19, with each tool's ``readOnlyHint``). The six
  sign-in tools are always listed, as the real server lists them in stdio mode.
* Like the real server it narrows that list with the ``ENABLED_TOOLS``
  environment variable (a regex matched against each tool name,
  case-insensitively) — so the card's filter is tested against the real names.
* ``get-calendar-view`` answers one event tomorrow and says which Microsoft
  authority the server was started with (``MS365_MCP_TENANT_ID``; the real
  server's default is "common"), so a test can prove the account kind the user
  chose reached the pack's process.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import sys

#: (tool name, readOnlyHint) as the real server lists them.
TOOLS: tuple[tuple[str, bool], ...] = (
    ('accept-calendar-event', False),
    ('add-calendar-event-attachment', False),
    ('add-mail-attachment', False),
    ('cancel-calendar-event', False),
    ('copy-drive-item', False),
    ('copy-mail-message', False),
    ('create-calendar', False),
    ('create-calendar-event', False),
    ('create-calendar-event-attachment-upload-session', False),
    ('create-contact-child-folder', False),
    ('create-contact-folder', False),
    ('create-contact-in-folder', False),
    ('create-draft-email', False),
    ('create-drive-item-preview', False),
    ('create-drive-item-share-link', False),
    ('create-focused-inbox-override', False),
    ('create-forward-draft', False),
    ('create-mail-attachment-upload-session', False),
    ('create-mail-child-folder', False),
    ('create-mail-folder', False),
    ('create-mail-rule', False),
    ('create-my-calendar-permission', False),
    ('create-onedrive-folder', False),
    ('create-outlook-category', False),
    ('create-outlook-contact', False),
    ('create-reply-all-draft', False),
    ('create-reply-draft', False),
    ('create-specific-calendar-event', False),
    ('create-upload-session', False),
    ('decline-calendar-event', False),
    ('delete-calendar', False),
    ('delete-calendar-event', False),
    ('delete-contact-folder', False),
    ('delete-drive-item-permission', False),
    ('delete-focused-inbox-override', False),
    ('delete-mail-attachment', False),
    ('delete-mail-folder', False),
    ('delete-mail-message', False),
    ('delete-mail-rule', False),
    ('delete-my-calendar-permission', False),
    ('delete-onedrive-file', False),
    ('delete-outlook-contact', False),
    ('delete-specific-calendar-event', False),
    ('dismiss-calendar-event-reminder', False),
    ('download-bytes', True),
    ('download-bytes-to-file', True),
    ('forward-calendar-event', False),
    ('forward-mail-message', False),
    ('get-calendar-event', True),
    ('get-calendar-view', True),
    ('get-download-url', True),
    ('get-drive-delta', True),
    ('get-drive-item', True),
    ('get-drive-root-item', True),
    ('get-mail-attachment-content', True),
    ('get-mail-message', True),
    ('get-mail-message-mime', True),
    ('get-mail-tips', True),
    ('get-mailbox-settings', True),
    ('get-outlook-contact', True),
    ('get-specific-calendar-event', True),
    ('get-specific-calendar-view', True),
    ('get-upload-url', False),
    ('list-accounts', True),
    ('list-calendar-event-instances', True),
    ('list-calendar-events', True),
    ('list-calendar-events-delta', True),
    ('list-calendar-view-delta', True),
    ('list-calendars', True),
    ('list-contact-folder-child-folders', True),
    ('list-contact-folder-contacts', True),
    ('list-contact-folders', True),
    ('list-drive-item-permissions', True),
    ('list-drive-item-thumbnails', True),
    ('list-drive-item-versions', True),
    ('list-drives', True),
    ('list-focused-inbox-overrides', True),
    ('list-folder-files', True),
    ('list-mail-attachments', True),
    ('list-mail-child-folders', True),
    ('list-mail-folder-messages', True),
    ('list-mail-folder-messages-delta', True),
    ('list-mail-folders', True),
    ('list-mail-messages', True),
    ('list-mail-rules', True),
    ('list-my-calendar-permissions', True),
    ('list-outlook-categories', True),
    ('list-outlook-contacts', True),
    ('list-specific-calendar-events', True),
    ('list-supported-languages', True),
    ('list-supported-time-zones', True),
    ('login', False),
    ('logout', False),
    ('move-mail-message', False),
    ('move-rename-onedrive-item', False),
    ('read-mail-text', True),
    ('remove-account', False),
    ('reply-all-mail-message', False),
    ('reply-mail-message', False),
    ('search-onedrive-files', True),
    ('select-account', False),
    ('send-draft-message', False),
    ('send-mail', False),
    ('share-drive-item', False),
    ('snooze-calendar-event-reminder', False),
    ('tentatively-accept-calendar-event', False),
    ('update-calendar', False),
    ('update-calendar-event', False),
    ('update-contact-folder', False),
    ('update-focused-inbox-override', False),
    ('update-mail-folder', False),
    ('update-mail-message', False),
    ('update-mail-rule', False),
    ('update-mailbox-settings', False),
    ('update-my-calendar-permission', False),
    ('update-outlook-contact', False),
    ('update-specific-calendar-event', False),
    ('upload-file-content', False),
    ('verify-login', False),

)

#: Listed whatever the filter says (stdio mode registers them first).
AUTH_TOOLS = ("login", "logout", "verify-login", "list-accounts", "select-account", "remove-account")

#: What tomorrow's calendar holds.
EVENT = "Dentist appointment"

_out = sys.stdout.buffer


def _send(obj: dict) -> None:
    _out.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
    _out.flush()


def listed() -> list[dict]:
    """The tools this process lists, filtered the real server's way."""
    pattern = os.environ.get("ENABLED_TOOLS") or ""
    rx = re.compile(pattern, re.IGNORECASE) if pattern else None
    out = []
    for name, read_only in TOOLS:
        if name not in AUTH_TOOLS and rx is not None and not rx.search(name):
            continue
        tool = {"name": name, "description": f"{name} (Microsoft 365)",
                "inputSchema": {"type": "object", "properties": {}}}
        if read_only:
            tool["annotations"] = {"readOnlyHint": True}
        out.append(tool)
    return out


def _call(name: str) -> dict:
    if name == "get-calendar-view":
        tomorrow = (_dt.date.today() + _dt.timedelta(days=1)).isoformat()
        body = {"value": [{"subject": EVENT, "start": {"dateTime": f"{tomorrow}T09:30:00"}}],
                "tenant": os.environ.get("MS365_MCP_TENANT_ID", "common")}
        return {"content": [{"type": "text", "text": json.dumps(body)}], "isError": False}
    return {"content": [{"type": "text", "text": f"{name}: ok"}], "isError": False}


def serve() -> None:
    for raw in sys.stdin.buffer:
        line = raw.decode("utf-8").strip()
        if not line:
            continue
        msg = json.loads(line)
        mid = msg.get("id")
        if mid is None:
            continue
        method = msg.get("method")
        if method == "initialize":
            _send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                "serverInfo": {"name": "Microsoft365MCP", "version": "0.170.2-fixture"}}})
        elif method == "tools/list":
            _send({"jsonrpc": "2.0", "id": mid, "result": {"tools": listed()}})
        elif method == "tools/call":
            params = msg.get("params") or {}
            _send({"jsonrpc": "2.0", "id": mid, "result": _call(str(params.get("name") or ""))})
        else:
            _send({"jsonrpc": "2.0", "id": mid, "result": {}})


if __name__ == "__main__":
    serve()
