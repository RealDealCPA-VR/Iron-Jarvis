"""A paired computer: another PC running Iron Jarvis this one trusts (v1.497.0).

One row per pairing. The two tokens the pairing made live in the vault, never
here: ``in_secret`` names the token THIS computer made for the other one (the
other side shows it at this computer's task door and its unpair call; it is
checked here), ``out_secret`` names the token the OTHER computer made for this
one (shown at that computer's door). The remote agent the pairing made (so
"@<name>" in chat reaches the other computer) has the same name and uses
``out_secret`` as its credential.
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel

from ..core.ids import new_id, utcnow


class PeerRecord(SQLModel, table=True):
    """Another computer running Iron Jarvis, paired with this one."""

    __tablename__ = "peerrecord"

    id: str = Field(default_factory=lambda: new_id("peer"), primary_key=True)
    #: What THIS computer calls the other one. Also the name of the remote
    #: agent the pairing made, so it follows the remote-agent name rule.
    name: str = Field(index=True, unique=True)
    #: What the other computer calls itself.
    their_name: str = ""
    #: The other computer's address, scheme, host and port only
    #: (``http://192.168.1.21:8787``).
    address: str = ""
    #: Vault key of the token this computer made for the other one.
    in_secret: str = ""
    #: Vault key of the token the other computer made for this one.
    out_secret: str = ""
    paired_at: datetime = Field(default_factory=utcnow)
    #: The last time the other computer answered this one (a check, the pairing).
    last_ok_at: datetime | None = None
    #: The last time the other computer reached this one (a task, an answer).
    last_heard_at: datetime | None = None
    #: Why the last check did not get an answer, in plain words ("" = it did).
    last_error: str = ""
    last_error_at: datetime | None = None
    #: v1.509.0 (second review F6): the address the other computer last
    #: reached this one from, when it is NOT the one it was paired at ("" =
    #: the paired address). The pairing's address is never changed by it: a
    #: reply goes only where the two were paired. Additive columns
    #: (``core.db._reconcile_additive_columns`` adds them to an older table).
    heard_from: str | None = None
    heard_from_at: datetime | None = None
    #: Next leaps 41-a: what the OTHER computer may do on THIS one, one switch
    #: each (``peers/allows.SWITCHES``), every one OFF for a new pairing. Only
    #: the card's press turns one on. Additive: an older table gets them as
    #: empty columns, which ``PeerStore.settle_older`` fills (D-41.1: "Send me
    #: tasks" on for a pairing made before this version, the rest off).
    may_send_tasks: bool = False
    may_read_record: bool = False
    may_ask_back: bool = False
    may_hand_over: bool = False
    may_use_models: bool = False
    may_manage_models: bool = False
    may_code: bool = False
    #: The absolute folders "Look up my work record" and "Start coding work
    #: here" are limited to (JSON lists; none until the user adds some).
    record_folders_json: str = "[]"
    code_folders_json: str = "[]"
    #: What the OTHER computer last said it lets THIS one do (its probe's
    #: ``allows``, or its own push): a cache for the card, NEVER a permission
    #: here. ``{"allows": [...], "older": bool, "at": iso}``; "{}" = not yet.
    they_allow_json: str = "{}"
    #: A note the card shows once ("tasks": "Send me tasks" was kept on for a
    #: pairing from before this version); "" once it has been seen or a
    #: switch was changed.
    kept_note: str = ""
