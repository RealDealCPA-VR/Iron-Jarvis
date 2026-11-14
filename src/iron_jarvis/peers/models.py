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
