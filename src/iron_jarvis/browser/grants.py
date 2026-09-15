"""Per-tab approval grants: one Allow per tab, for as long as that tab is open (v1.266.0).

THE REPORT: "I need to keep on providing approvals over and over. It should just
take one approval per tab. The approval in that tab lasts as long as the tab is
open."

What was true. Every page action in the sidebar is ask-armed (visible, never
granted — v1.262.0), so the chat lane raised a card for each one; "Allow for this
task" widened the grant for the REST OF THAT TURN, and every Send is a fresh turn
with a fresh grant set. Ten messages into one job, that is ten cards for the
same tab.

What this module holds. A set of tab ids the user has said "allow" for, kept
by :class:`~iron_jarvis.browser.service.BrowserRuntime` and consulted by the
chat lane's card predicate: an acting browser call whose EFFECTIVE tab (the
``tab_id`` argument, else the tab the user is looking at) is granted gets no
card. A grant is made by the decision word ``tab`` on an approval — the
sidebar's "Allow for this tab", or the chat page's — and ends in exactly three
ways, each of them something that happened rather than something timed:

* **the tab closes** — the add-on reports ``tab_removed`` (``chrome.tabs.onRemoved``)
  and the grant for that id is dropped;
* **the browser restarts** — tab ids are per browser session and a new session
  reuses small integers, so a grant made in the last session could otherwise
  cover an unrelated tab. The add-on mints a ``browser_session`` id once per
  browser session (``chrome.storage.session``, which lives exactly that long and
  survives service-worker restarts) and sends it on ``browser.hello``;
  :meth:`TabGrants.note_session` clears everything when it changes;
* **Forget** — the pairing ends, and every consent that rode on it ends too.

A service-worker restart (the add-on reconnecting a few seconds later, which
happens after ~30 s idle) is NOT one of them: the tabs did not close. Clearing
on reconnect would bring the cards straight back, which is the report.

WHAT A TAB GRANT DOES NOT COVER, on purpose. The risk gate inside every acting
tool (``risk.browser_risk_decision`` → ``_ActingTool._require_approval``) never
reads this module: a payment or password field, the destructive vocabulary, a
page that tripped the injection detector, a target the daemon could not read —
those still stop at their own card. "Jarvis still stops to ask when a decision
is yours" is the Handbook's sentence, and a grant that covered a whole tab
would otherwise cover "Delete account" on it. The card predicate this feeds is
the ORDINARY ask — the one that used to fire on every scroll and click.

Every method is a lock and a set operation: no database, no hash, so it is safe
to call on the event loop (unlike the pairing store).
"""

from __future__ import annotations

import threading
from typing import Any

from .risk import BASE_RISK
from ..tools.base import RiskClass


def is_acting_browser_tool(name: str) -> bool:
    """Whether ``name`` is a browser tool that CHANGES something — the only kind a tab grant covers.

    Read off the risk table rather than a list typed here, so a tool added to
    ``BASE_RISK`` as an action is covered without a second edit, and a read tool
    (which never carded anyway) never is.
    """
    cls = BASE_RISK.get(str(name or ""))
    return cls is not None and cls is not RiskClass.READ


def tab_key(tab_id: Any) -> str:
    """The one spelling of a tab id: ``7``, ``"7"`` and ``7.0`` are the same tab."""
    if tab_id is None or tab_id == "":
        return ""
    try:
        return str(int(tab_id))
    except (TypeError, ValueError):
        return str(tab_id).strip()


class TabGrants:
    """The set of granted tabs, and the browser session they belong to."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tabs: set[str] = set()
        #: The ``browser_session`` the grants were made under, or ``""`` before any
        #: hello has said. A grant made before the first hello is kept by the first
        #: hello (nothing to compare it with) and cleared by a DIFFERENT one later.
        self._session: str = ""

    # --- reads ---------------------------------------------------------------

    def covers(self, tab_id: Any) -> bool:
        key = tab_key(tab_id)
        if not key:
            return False
        with self._lock:
            return key in self._tabs

    def tab_ids(self) -> list[str]:
        with self._lock:
            return sorted(self._tabs)

    @property
    def session(self) -> str:
        return self._session

    # --- writes --------------------------------------------------------------

    def grant(self, tab_id: Any) -> str:
        """Grant ``tab_id``; returns the key recorded, or ``""`` when there was no tab to grant."""
        key = tab_key(tab_id)
        if not key:
            return ""
        with self._lock:
            self._tabs.add(key)
        return key

    def revoke(self, tab_id: Any) -> bool:
        """Drop one tab's grant (the tab closed, or the user ended it). Whether one was held."""
        key = tab_key(tab_id)
        if not key:
            return False
        with self._lock:
            had = key in self._tabs
            self._tabs.discard(key)
        return had

    def clear(self) -> int:
        """Drop every grant (Forget, or a new browser session). How many died."""
        with self._lock:
            n = len(self._tabs)
            self._tabs.clear()
        return n

    def note_session(self, session_id: Any) -> int:
        """Record the browser session a hello announced; a CHANGE clears every grant.

        Returns how many grants were cleared. An empty announcement (an older
        add-on that sends no session) changes nothing and clears nothing: the
        conservative reading of "I don't know" is not "everything is stale" —
        that would put the cards back for every user on an older add-on — and the
        tab-closed event still ends each grant on its own.
        """
        sid = str(session_id or "").strip()[:64]
        if not sid:
            return 0
        with self._lock:
            if self._session and self._session != sid:
                n = len(self._tabs)
                self._tabs.clear()
            else:
                n = 0
            self._session = sid
        return n


__all__ = ["TabGrants", "is_acting_browser_tool", "tab_key"]
