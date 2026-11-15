"""What a turn in a confirmed client's folders is told (v1.499.0, builder A).

The client block joins the STABLE head (``_Grounding`` slot ``client``,
``project + folder_rules + lessons + client + index``), BEFORE compaction and
the planner in both lanes, and in agent runs (after ``apply_to_prompt``) and
terminal help (after the lessons). Hop ``client`` is REQUIRED, bounded at
``CLIENT_HOP_MAX_S``.

STUB until builder A lands: "" for every folder, so the prompt is
byte-identical to a build without clients. The docstring is the rule.
"""

from __future__ import annotations

from typing import Any

#: The caps inside the block (its whole size is ``CLIENT_BLOCK_CHARS``).
MAX_PREFERENCE_LINES = 12
MAX_OTHER_FOLDERS = 4


def client_block(platform: Any, *, folder: str, local: bool | None = None) -> str:
    """The client block for a turn whose ``folder`` resolves to a CONFIRMED
    client (``store.load_map(...).client_of(folder)``); "" otherwise (no
    folder, no client, a waiting or declined name, a timeout, any error).
    BLOCKING; NEVER RAISES. Its only shape::

        # Client: Hartleys
        This chat's folder belongs to Hartleys, a client the owner confirmed.
        What the owner keeps for Hartleys (follow these for Hartleys' work unless the owner says otherwise in this chat; they hold for Hartleys only):
        - Use cash basis.
        Hartleys' other folders:
        - D:\\Scans\\Hartleys
        A preference the owner states for Hartleys is kept for Hartleys only, never with remember_preference.

    Caps: :data:`MAX_PREFERENCE_LINES`, :data:`MAX_OTHER_FOLDERS`,
    ``CLIENT_BLOCK_CHARS``. Every line through ``promptguard.scan_context
    (source='client preferences')``; a flagged line is dropped. A preference
    with ``local_only``, or whose source chat is NOW in
    ``egress.local_only_thread_ids`` (``None`` means withhold), is told only
    when ``egress.may_recall_local_only_chats()``. ``local``: None reads the
    turn's own scope; False is a cloud turn (the client page's "told"
    preview); True a turn that stays on this network. Other folders are
    listed in a cloud turn only when they hold work that is not Local only or
    kept home, or when the owner added them."""
    return ""


__all__ = ["MAX_OTHER_FOLDERS", "MAX_PREFERENCE_LINES", "client_block"]
