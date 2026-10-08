"""UX wave 2, track T3 (v1.314.0) — the Templates page's requirement words.

What the user saw (fresh__templates.png, verified): the Inbox triage starter
said "needs an email plug-in (e.g. Gmail or Outlook via MCP). Connect one under
Tools -> Plug-ins." with a link "Tools → Plug-ins". VOCABULARY.md retired
"plug-in" for "extension" in v1.216.0 (the Tools page section is "Extensions"),
the copy mixed an ASCII "->" with "→", and the web requirement named an
internal tool id ("The web_search tool isn't loaded on this install.").

The requirement is rendered verbatim by dashboard/app/templates/page.tsx, so the
words are pinned HERE, at their source.

What must NOT change (anti-vacuity): the requirement keys, `ok` verdicts and
`setup_path` values (test_templates_v1128 pins those) — only the words a user
reads move.
"""

from __future__ import annotations

import re

from iron_jarvis.templates import analyze_requirements

_CTX = dict(
    selectable_models=[{"provider": "anthropic", "model": "claude-x"}],
    live_tools=["documents_read"],
    has_secret=lambda name: None,
    comm_config={},
    agent_names=["builder", "researcher"],
)


def _reqs(task: str, **over):
    ctx = {**_CTX, **over}
    return analyze_requirements(task, None, None, "builder", **ctx)


def _one(reqs, key):
    return next(r for r in reqs if r["key"] == key)


_PLUGIN = re.compile(r"plug-?ins?", re.IGNORECASE)


def test_missing_email_speaks_extension_not_plugin():
    email = _one(_reqs("Check my unread emails and summarize them"), "email")
    assert email["ok"] is False
    assert not _PLUGIN.search(email["detail"]), email["detail"]
    assert not _PLUGIN.search(email["setup_label"]), email["setup_label"]
    assert "extension" in email["detail"].lower()
    assert email["setup_label"] == "Tools → Extensions"
    # The wire is untouched: the link still opens the Tools page.
    assert email["setup_path"] == "/tools"


def test_connected_email_speaks_extension_not_plugin():
    email = _one(
        _reqs("Check my unread emails and summarize them", live_tools=["mcp_gmail__list_messages"]),
        "email",
    )
    assert email["ok"] is True
    assert not _PLUGIN.search(email["detail"]), email["detail"]


def test_missing_web_search_never_names_the_tool_id():
    web = _one(_reqs("Research the latest news on tariffs"), "web")
    assert web["ok"] is False
    assert "web_search" not in web["detail"], web["detail"]
    assert web["detail"].strip()  # still says something
    assert web["setup_path"] == "/tools"


def test_no_requirement_mixes_an_ascii_arrow():
    """Every detail/label a user reads uses one arrow, '→', never '->'."""
    tasks = [
        "Check my unread emails and summarize them",
        "Research the latest news on tariffs",
        "Post the summary to Slack",
        "Send the digest on Telegram",
        "Generate an image of a lighthouse",
    ]
    for task in tasks:
        for r in _reqs(task):
            assert "->" not in r["detail"], (task, r["detail"])
            assert "->" not in r["setup_label"], (task, r["setup_label"])


def test_slack_requirement_keeps_its_page_and_words():
    """Control: the Slack/Telegram rows already say Notifications — keep them."""
    slack = _one(_reqs("Post the summary to Slack"), "slack")
    assert slack["setup_label"] == "Notifications"
    assert slack["setup_path"] == "/channels"
