"""Every tool and setting that sets WHERE Iron Jarvis's data goes (fourth fix
plan, PG1 and decision D3 option B).

"Ask every time when this changes where data goes" lives in each tool's own
check (``Tool.spend_check``), and a new tool or setting that changes a place
gets only the ordinary per-name level unless someone remembers to add one:
then a conversation grant or "Run without asking" covers every later address.
This table names each tool whose arguments can name a place, with what its
argument does and how each call is asked, so nothing is left to memory:

* ``tests/test_fourth_review_destinations_v1533.py`` scans the real registry:
  every tool with an address-shaped argument (:func:`address_shaped`) must be
  here, and each :data:`ASKS` row's own check must really ask.
* The chat lookup (``capability_search/tool.py``) and ``config_list`` read
  :func:`still_asks` to say a tool's one exception to "Run without asking"
  (C-PG-3).
* Later, "Sending outside this PC" in Settings (C-PG-1) reads it too.

The settings that set a place carry ``SettingDef.destination`` (the words for
what goes there); :func:`destination_settings` lists them. The check itself is
``webhooks.tools.outbound_address_check``. Pure data: imports nothing from the
tool layer at import time.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Its own check asks every time it sets a NEW place (an address off this
#: PC), "Allow once" only: no grant or level covers the next address.
ASKS = "asks"
#: The place works only after the user pastes its own token or password on
#: the secure card: that card is the per-place consent.
SECURE_CARD = "secure_card"
#: The user signs in in their own browser: that is the consent.
SIGN_IN = "sign_in"
#: Sends only to a place the user already set up, named by its name.
FIXED = "fixed"
#: One request now; nothing is set for later (the permission level, Local
#: only and low trust decide, as for any call).
ONE_OFF = "one_off"
#: Makes something available; sends nothing by itself (each later call is
#: gated on its own).
CONNECTS = "connects"
#: The argument names a file, a note, a queue slot or a label, not a place.
NOT_AN_ADDRESS = "not_address"

KINDS = (ASKS, SECURE_CARD, SIGN_IN, FIXED, ONE_OFF, CONNECTS, NOT_AN_ADDRESS)

#: C-PG-3: what a tool set to "Run without asking" still asks about.
STILL_ASKS_LINE = "Still asks every time it would send to a new address."

#: Argument names that can name a place outside this PC (the scan's list).
DESTINATION_ARGS = frozenset({
    "target_url", "url", "address", "base_url", "endpoint", "webhook_url", "to", "to_addr",
    "recipient", "recipients", "phone", "chat_id", "channel", "host", "imap_host", "server", "uri",
})

#: Settings that name a FOLDER rather than an address (the card names the
#: folder's drive or share; a folder on this PC may still be synced away).
FOLDER_SETTINGS = frozenset({"backup_mirror_dir"})


def address_shaped(arg: str) -> bool:
    """Can an argument called *arg* name a place outside this PC?"""
    a = str(arg or "").strip().lower()
    return a in DESTINATION_ARGS or a.endswith(("_url", "_host", "_address"))


@dataclass(frozen=True)
class Destination:
    tool: str
    kind: str
    #: What its place-naming argument does, and how each call is asked.
    why: str


def _rows(*rows: tuple[str, str, str]) -> dict[str, Destination]:
    return {tool: Destination(tool, kind, why) for tool, kind, why in rows}


DESTINATION_TOOLS: dict[str, Destination] = _rows(
    # ---- its own check asks every time it sets a new place
    ("webhook_add", ASKS, "An outbound webhook sends later events to target_url; asks every time (Allow once)."),
    ("webhook_update", ASKS, "Re-points an outbound webhook's target_url; asks every time (Allow once)."),
    ("config_change", ASKS, "Sets an address setting (a model server, the speech server, the backup copy "
                            "folder); asks every time for a new place off this PC (Allow once)."),
    ("model_server_add", ASKS, "Adds a server that answers chats; its own check asks every time."),
    ("model_server_default", ASKS, "Makes an added server's model the default; its own check asks every time."),
    ("web_service_add", ASKS, "Saves a web service's address; its own check asks every time."),
    ("mcp_server_add", ASKS, "Adds a tool server, possibly remote; asks every time (Allow once)."),
    ("memory_base_add", ASKS, "Adds a memory base (a folder, or Notion in the cloud); its own check asks every time."),
    ("app_import", ASKS, "Brings over another app's servers; asks every time (Allow once)."),
    # ---- consent given another way
    ("channel_connect", SECURE_CARD, "A new destination (chat id, channel, mail address) works only after the "
                                     "user pastes its own token or password on the secure card."),
    ("account_add", SIGN_IN, "The user signs in in their own browser."),
    # ---- a place the user already set up
    ("rest_call", FIXED, "A path under a web service the user saved; anything but GET asks every time."),
    ("notify", FIXED, "A destination the user set up, by name."),
    ("delegate_remote", FIXED, "A remote agent registered on its own page, by name."),
    ("mcp_read_resource", FIXED, "An app or server already connected."),
    ("mcp_list_resources", FIXED, "An app or server already connected."),
    ("model_server_remove", FIXED, "Removes a server already added; sends nothing."),
    # ---- one request now, nothing set for later
    ("web_fetch", ONE_OFF, "Reads one page now."),
    ("browse", ONE_OFF, "Opens one page now."),
    ("browser_navigate", ONE_OFF, "Opens one page now in the user's browser."),
    ("browser_create_tab", ONE_OFF, "Opens one tab now in the user's browser."),
    ("page_open", ONE_OFF, "Opens one page now in a coding session's browser."),
    ("page_screenshot", ONE_OFF, "Takes one picture of a page now."),
    ("pixio_upload", ONE_OFF, "Uploads one file now to the media service the user connected."),
    # ---- connects; sends nothing by itself
    ("app_connect", CONNECTS, "Makes an app's tools available; each of its calls is gated on its own."),
    # ---- not a place
    ("download_manage", NOT_AN_ADDRESS, "`to` is a folder on this PC for a download."),
    ("brain_change", NOT_AN_ADDRESS, "`to` is the new text of a note."),
    ("night_task_move", NOT_AN_ADDRESS, "`to` is a slot in the night queue."),
    ("chat_record", NOT_AN_ADDRESS, "`chat_id` and `link` label another app's chat in the work record."),
    ("secret_set", NOT_AN_ADDRESS, "Stores nothing from chat; the user types the value on the page."),
)


def still_asks(tool: str) -> bool:
    """Does *tool* still ask every time it would send to a new address, even
    when set to "Run without asking"? (C-PG-3.)"""
    row = DESTINATION_TOOLS.get(str(tool or ""))
    return row is not None and row.kind == ASKS


def destination_settings() -> dict[str, str]:
    """``{setting key: what goes there}`` for every setting that sets a place
    (``SettingDef.destination``). Never raises."""
    try:
        from ..settings import schema

        return {d.key: d.destination for d in schema.SETTINGS if getattr(d, "destination", "")}
    except Exception:  # noqa: BLE001 — a table we cannot read is empty, never a guess
        return {}
