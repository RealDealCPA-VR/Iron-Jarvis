"""The v1.356.0 Microsoft 365 stand-in, with the real server's sign-in check
(v1.358.0, lane 8D pins).

Everything is ``ms365_mcp_server_v13560`` (the real server's tool names, its
``ENABLED_TOOLS`` filter, its calendar answer) except ``verify-login``, which
answers the way @softeria/ms-365-mcp-server 0.170.2 does
(``dist/auth-tools.js`` + ``dist/auth.js`` ``testLogin``, read on this PC):
one text block holding ``{"success": false, "message": "Login failed: No
valid token found"}`` before a sign-in, and ``{"success": true, "message":
"Login successful", "userData": {"displayName", "userPrincipalName"}}`` after.

Whether "the user has signed in" is a FILE the test writes (its path in
``MS365_FIXTURE_SIGNIN``): absent or empty = signed out; one line holding a
principal name = signed in as that account — the way the real server reads
its token cache from disk before every use, so a sign-in finished elsewhere
is seen by the running pack without a restart. No network, no Microsoft.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ms365_mcp_server_v13560 as base  # noqa: E402


def _signed_in_as() -> str:
    path = os.environ.get("MS365_FIXTURE_SIGNIN") or ""
    try:
        return Path(path).read_text(encoding="utf-8").strip() if path else ""
    except OSError:
        return ""


def _verify() -> dict:
    who = _signed_in_as()
    if not who:
        body = {"success": False, "message": "Login failed: No valid token found"}
    else:
        body = {
            "success": True,
            "message": "Login successful",
            "userData": {"displayName": "Dana Example", "userPrincipalName": who},
        }
    return {"content": [{"type": "text", "text": json.dumps(body)}]}


_real_call = base._call


def _call(name: str) -> dict:
    if name == "verify-login":
        return _verify()
    return _real_call(name)


base._call = _call

if __name__ == "__main__":
    base.serve()
