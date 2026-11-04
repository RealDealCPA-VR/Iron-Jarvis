"""The Microsoft 365 card, LOCKED DOWN (v1.428.0, the user's decision for a
firm that holds sensitive client information).

What a connect from the Directory card now always does (``env_for``):

* READ AND DRAFT ONLY. The server starts with :data:`SAFE_TOOLS`: mail, the
  calendar, contacts and OneDrive are READ; mail can be DRAFTED. No sending, no
  calendar changes. The server asks Microsoft for exactly Calendars.Read,
  Contacts.Read, Files.Read and Mail.ReadWrite (its ``--list-permissions`` with
  this filter; Mail.ReadWrite is the smallest permission that saves a draft).
* ONE ACCOUNT. ``MS365_MCP_EXPECTED_USERNAME`` = the email typed on the card:
  the server refuses any other account at sign-in and deletes its tokens
  (``rejectUnexpectedLoginAccount``, read in 0.170.2's dist/auth.js) — an
  admin account signed in by mistake is never used.
* THE FIRM'S OWN APP for a work account (required — the review: a shared
  public app consented once in a firm's directory is a door anyone with that
  app can walk through): ``MS365_MCP_CLIENT_ID`` names the app the firm's
  Microsoft 365 admin registered (single tenant), and the sign-in goes to the
  firm's own directory (``MS365_MCP_TENANT_ID`` = the email's domain —
  Microsoft accepts a verified domain in place of the tenant id). A personal
  account signs in with the package's own app, named explicitly
  (:data:`DEFAULT_APP`) so nothing else can choose it.
* ITS OWN SIGN-IN FOLDER: ``MS365_MCP_TOKEN_CACHE_PATH`` and
  ``MS365_MCP_SELECTED_ACCOUNT_PATH`` point into Iron Jarvis's own folder
  (``locked_pack.signin_dir``), never the package's shared one another copy of
  it would read. The cache file is encrypted by the package with a key it
  keeps in Windows Credential Manager (0.170.2's token-cache-storage.js).
* ``MS365_MCP_CLOUD_TYPE`` = "global", set rather than left to a ``.env`` file.
* LOCAL MODELS ONLY: the saved row says ``local_only`` (``service.pack_body``);
  ``mcp.tools`` refuses its tools in a turn that may reach a cloud model.

The program itself runs from Iron Jarvis's own lock
(``connectors.locked_pack.MS365``), never ``npx`` at its newest.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

#: The choice field's name and values (the server reads it as the authority).
TENANT_ENV = "MS365_MCP_TENANT_ID"
WORK = "common"
PERSONAL = "consumers"
EMAIL_ENV = "MS365_MCP_EXPECTED_USERNAME"
APP_ENV = "MS365_MCP_CLIENT_ID"
CLOUD_ENV = "MS365_MCP_CLOUD_TYPE"
TOOLS_ENV = "ENABLED_TOOLS"
CACHE_ENV = "MS365_MCP_TOKEN_CACHE_PATH"
ACCOUNT_ENV = "MS365_MCP_SELECTED_ACCOUNT_PATH"
#: The package's own public app (0.170.2), used only for a PERSONAL account.
DEFAULT_APP = "084a3e9f-a9f4-43f7-89f9-d229cf97853e"
#: The sign-in files' names inside the sign-in folder (the package's own).
CACHE_FILE = ".token-cache.json"
ACCOUNT_FILE = ".selected-account.json"

#: What the locked card lets Jarvis do: read, and draft mail. Never send,
#: never change the calendar. The server adds its own sign-in tools (login,
#: logout, verify-login, list-accounts, select-account, remove-account).
SAFE_TOOLS: tuple[str, ...] = (
    # Calendar (read)
    "get-calendar-view",
    "list-calendar-events",
    "get-calendar-event",
    # Outlook mail (read, and save a draft)
    "list-mail-messages",
    "get-mail-message",
    "read-mail-text",
    "list-mail-folders",
    "create-draft-email",
    # Contacts (read)
    "list-outlook-contacts",
    # OneDrive (read)
    "search-onedrive-files",
    "list-drives",
    "get-drive-root-item",
    "list-folder-files",
    "get-drive-item",
    "download-bytes",
)
SAFE_ENABLED_TOOLS = "^(" + "|".join(SAFE_TOOLS) + ")$"
#: What the server asks Microsoft for with :data:`SAFE_TOOLS` (measured).
PERMISSIONS: tuple[str, ...] = ("Calendars.Read", "Contacts.Read", "Files.Read", "Mail.ReadWrite")

_GUID_RX = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_EMAIL_RX = re.compile(r"^[^@\s\"'<>;|&^%!`$]+@([A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+)$")

BAD_EMAIL = "Type the email address of the Microsoft account Jarvis should use (for example you@yourfirm.com)."
BAD_APP_ID = (
    "The app ID should look like 12345678-abcd-1234-abcd-1234567890ab: copy the "
    "Application (client) ID from your app's Overview page in the Microsoft Entra admin center."
)
BAD_KIND = "Choose whether this is a work or school account or a personal one."
NEED_APP = (
    "A work or school account needs your firm's own app ID: ask your Microsoft 365 "
    "admin to register an app for Iron Jarvis (the Handbook has the steps), then "
    "paste its Application (client) ID here."
)


class Ms365Refused(ValueError):
    """A card answer that cannot be used; ``str()`` is one plain sentence."""


def email_domain(email: str) -> str:
    m = _EMAIL_RX.match(email.strip())
    return m.group(1).lower() if m else ""


def env_for(values: dict[str, Any], signin_dir: "str | Path | None" = None) -> dict[str, str]:
    """The settings a locked Microsoft 365 pack starts with, from the card's
    answers (and, when given, the folder its sign-in is kept in). Raises
    :class:`Ms365Refused` in plain words."""
    kind = str(values.get(TENANT_ENV, "") or "").strip()
    if kind not in (WORK, PERSONAL):
        raise Ms365Refused(BAD_KIND)
    email = str(values.get(EMAIL_ENV, "") or "").strip().lower()
    domain = email_domain(email)
    if not domain:
        raise Ms365Refused(BAD_EMAIL)
    app = str(values.get(APP_ENV, "") or "").strip().lower()
    if app and not _GUID_RX.match(app):
        raise Ms365Refused(BAD_APP_ID)
    if kind == WORK and (not app or app == DEFAULT_APP):
        raise Ms365Refused(NEED_APP)
    env = {
        TOOLS_ENV: SAFE_ENABLED_TOOLS,
        CLOUD_ENV: "global",
        EMAIL_ENV: email,
        APP_ENV: app or DEFAULT_APP,
        # The firm's own app signs in at the firm's own directory (a
        # single-tenant app refuses Microsoft's shared "common" one).
        TENANT_ENV: domain if kind == WORK else PERSONAL,
    }
    if signin_dir is not None:
        env[CACHE_ENV] = str(Path(signin_dir) / CACHE_FILE)
        env[ACCOUNT_ENV] = str(Path(signin_dir) / ACCOUNT_FILE)
    return env


def answers_from_env(env: dict[str, Any]) -> dict[str, str]:
    """The card's answers a saved row's settings came from (for checking that
    a saved row is still exactly what the card writes)."""
    tenant = str(env.get(TENANT_ENV, "") or "")
    kind = PERSONAL if tenant == PERSONAL else WORK
    out = {TENANT_ENV: kind, EMAIL_ENV: str(env.get(EMAIL_ENV, "") or "")}
    app = str(env.get(APP_ENV, "") or "")
    if app and app != DEFAULT_APP:
        out[APP_ENV] = app
    return out


def kind_of(env: dict[str, Any]) -> str:
    """``work`` or ``personal`` — which kind of account a saved row signs in."""
    return "personal" if str(env.get(TENANT_ENV, "")) == PERSONAL else "work"


def uses_firm_app(env: dict[str, Any]) -> bool:
    app = str(env.get(APP_ENV, "") or "")
    return bool(app) and app != DEFAULT_APP
