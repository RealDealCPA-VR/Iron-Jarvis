"""North-star plan 4.8, the scan: every delete route journals, or says why not.

A delete made on a page must be recorded and, where the journal can do it,
undoable (``settings/page_records``). This reads every ``@app.delete`` route
and every ``POST``/``PUT`` route whose path ends in ``/delete`` or
``/remove`` under ``daemon/routes/``: its handler either calls
``journal_removal`` / ``journal_secret_change``, or it is on the allow list
below WITH its reason. A new delete route fails here until one is true.
The test is the rule.
"""

from __future__ import annotations

import re
from pathlib import Path

ROUTES = Path(__file__).resolve().parents[1] / "src" / "iron_jarvis" / "daemon" / "routes"

_DECORATOR = re.compile(
    r'@app\.(delete|post|put)\(\s*"([^"]+)"',
)
_JOURNALS = ("journal_removal(", "journal_secret_change(")

#: Not journaled yet, each with WHY. "Moves to the trash" and "soft" ones are
#: already recoverable another way; the rest are said plainly as permanent
#: until a record kind exists for them.
ALLOWED: dict[str, str] = {
    # -- not a stored record
    "DELETE /mcp": "closes a live connection from an outside tool; nothing is stored",
    "DELETE /terminals/{term_id}": "ends a running terminal; nothing is stored",
    # Third fix plan (TM19): "Close" on a terminal that could not be reopened
    # after a restart. Its kept output goes, as it does when any terminal is
    # closed; the user pressed Close on that one terminal.
    "DELETE /terminals/not-reopened/{term_id}": (
        "closes a terminal that could not be reopened; its kept output goes with it, as for any closed terminal"
    ),
    "DELETE /build/sessions/{thread_id}/resume-when-ready": "clears a wait flag; nothing is removed",
    # -- already recoverable another way
    "DELETE /sessions/{session_id}": "moves the run's files and record to the app's trash (v1.504.0, plan 4.2)",
    "POST /templates/skill-bundles/{bundle_id}/remove": "moves the bundle's skills to the app's trash",
    "DELETE /build/sessions/{thread_id}": "moves the coding copy and attempt folders to the trash, branch kept; the conversation itself is not kept (follow-up)",
    "DELETE /build/app/sessions/{sid}": "moves the session's events folder to the trash; its row is not kept (follow-up)",
    "DELETE /brain/pending/{pending_id}": "soft: marks the pending write discarded, the row is kept",
    "POST /team/notes/{note_id}/remove": "soft: stamps the note removed, the row is kept",
    "DELETE /backgrounds/{bg_id}": "a downloaded copy of a catalog file; downloaded again from the catalog",
    # -- in a file another change owns in v1.504.0 (chat lanes)
    "DELETE /chat/threads/{thread_id}": "daemon/routes/chat.py is edited by another change in v1.504.0; a saved chat has no record kind yet",
    # -- removes saved keys: a key's value is never written into the journal
    "DELETE /comm/channels/{name}": "removes the destination's saved keys; its Undo needs the key copy chat's channel_remove makes (follow-up)",
    "DELETE /mcp/servers/{name}": "removes the app's saved keys; its Undo needs the key copy chat's app_disconnect makes (follow-up)",
    "DELETE /connectors/{connector_id}": "removes an app or its saved keys; same as /mcp/servers (follow-up)",
    "DELETE /connections/{provider}": "removes a provider's saved keys; a key's value is never journaled",
    "DELETE /triggers/calendar/url": "removes the saved calendar link (a secret); chat's app_disconnect keeps an Undo copy (follow-up)",
    "DELETE /iron-proxy/accounts/{account_id}": "removes an account on the Iron-Proxy service itself; nothing local to restore",
    "DELETE /peers/{name}": "removes a paired computer and its tokens; a token is never journaled",
    "DELETE /agents/remote/{name}": "removes a remote agent and its saved token; a token is never journaled",
    # -- permanent until a record kind exists (follow-up)
    "DELETE /agents/{name}/avatar": "a picture file; no record kind yet",
    "DELETE /agents/{name}/face": "a face setting file; no record kind yet",
    "DELETE /brain/profiles/{profile_id}": "no record kind yet",
    "POST /build/app/sessions/{sid}/queue/{qid}/remove": "one queued message; no record kind yet",
    "DELETE /build/library/{item_id}": "no record kind yet",
    "POST /clients/{client_id}/folders/remove": "the 'client' record kind exists in chat; route it next",
    "DELETE /code-artifacts/{artifact_id}": "no record kind yet; its working folder is left alone",
    "DELETE /creative/items/{name:path}": "no record kind yet",
    "DELETE /documents/live/{doc_id}": "no record kind yet; the document file itself stays",
    "DELETE /fleet/nodes/{node_id}": "the 'model_server_removed' kind exists in chat; route it next",
    "DELETE /ltm/sources/{name}": "no record kind yet",
    "POST /memory/graph/node/delete": "no record kind yet",
    "DELETE /build/night/{task_id}": "no record kind yet for a removed night task",
    "DELETE /profile/samples/{sample_id}": "no record kind yet",
    "DELETE /projects/{project_id}": "no record kind brings a removed project back yet",
    "DELETE /projects/{project_id}/knowledge/{knowledge_id}": "no record kind yet",
    "DELETE /templates/{prompt_id}": "a saved prompt; no record kind yet",
}


def _routes() -> dict[str, tuple[str, str]]:
    """``"METHOD /path"`` -> (file name, handler source up to the next route)."""
    out: dict[str, tuple[str, str]] = {}
    for f in sorted(ROUTES.glob("*.py")):
        src = f.read_text(encoding="utf-8").replace("\r\n", "\n")
        marks = list(_DECORATOR.finditer(src))
        all_routes = [m.start() for m in re.finditer(r"@app\.\w+\(", src)]
        for m in marks:
            method, path = m.group(1).upper(), m.group(2)
            if method != "DELETE" and not re.search(r"/(delete|remove)$", path):
                continue
            nxt = [s for s in all_routes if s > m.start()]
            body = src[m.start(): nxt[0] if nxt else len(src)]
            out[f"{method} {path}"] = (f.name, body)
    return out


def test_every_delete_route_journals_or_says_why():
    found = _routes()
    assert len(found) >= 40, "the scan found too few routes; the pattern is broken"
    missing = [
        f"{key} ({where})"
        for key, (where, body) in found.items()
        if not any(j in body for j in _JOURNALS) and not ALLOWED.get(key, "").strip()
    ]
    assert missing == [], (
        "a delete route neither journals (settings/page_records) nor is on the allow list "
        f"with its reason: {missing}"
    )


def test_the_allow_list_has_no_stale_or_doubled_entries():
    found = _routes()
    stale = [k for k in ALLOWED if k not in found]
    assert stale == [], f"allowed routes that no longer exist: {stale}"
    doubled = [k for k in ALLOWED if any(j in found[k][1] for j in _JOURNALS)]
    assert doubled == [], f"these journal now; take them off the allow list: {doubled}"


def test_the_routes_this_change_routed_journal():
    found = _routes()
    for key in (
        "DELETE /schedules/{name}",
        "DELETE /workflows/{name}",
        "DELETE /agents/{name}",
        "DELETE /tools/custom/{name}",
        "DELETE /webhooks/{slug}",
        "DELETE /sentinels/{name}",
        "DELETE /goals/{goal_id}",
        "DELETE /reflex/rules/{rule_id}",
        "DELETE /lessons/{lesson_id}",
        "DELETE /memory/preferences/{pref_id}",
        "DELETE /secrets/{name}",
        # Next leaps 15-a: a client's preference forgotten on a page (or from
        # the chat's "From memory" box) is recorded with its Undo.
        "DELETE /clients/{client_id}/preferences/{pref_id}",
    ):
        assert any(j in found[key][1] for j in _JOURNALS), key
