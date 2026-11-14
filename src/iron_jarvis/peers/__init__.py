"""Paired computers: another PC running Iron Jarvis, set up in one step (v1.497.0).

One computer shows a short code, the other types it, and each side then holds
its own token for the other and a remote agent named for it. See
``store.py`` (what a pairing is), ``codes.py`` (the code's rules) and
``link.py`` (the calls to the other computer); the routes are
``daemon/routes/peers.py``.
"""

from .codes import PairingCodes
from .store import PeerStore, store_for

__all__ = ["PairingCodes", "PeerStore", "store_for"]
