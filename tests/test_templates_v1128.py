"""Templates become robust and self-explanatory (v1.128.0).

Three user-facing problems fixed:

1. Saving a template with a DYNAMIC agent type 400'd — ``TemplateStore.create``
   cast through the ``AgentType`` enum while the picker happily offered dynamic
   agent names. The column is a plain string now; legacy rows (which persisted
   the enum NAME, e.g. "BUILDER") normalize on read.
2. No edit: fixing a typo meant delete + retype. ``PATCH /templates/{id}``.
3. Nothing said what a template NEEDS to run. Requirement detection annotates
   every template and starter with unmet needs — a pinned model that is no
   longer connected, a Pixio key for media generation, an email plug-in, a
   Telegram/Slack connection — each with the dashboard page that sets it up.

Plus a browsable starter library (GET /templates/starters) the user can add
from any time, not just the empty-store first-run seed.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.templates import STARTER_CATALOG, analyze_requirements


@pytest.fixture
def client(tmp_path) -> TestClient:
    return TestClient(create_app(str(tmp_path)))


# --- agent_type is a plain string now ----------------------------------------


def test_dynamic_agent_type_saves(client):
    """The picker offers dynamic agents; saving one must not 400."""
    r = client.post(
        "/templates", json={"name": "T", "task": "do x", "agent_type": "my-custom-agent"}
    )
    assert r.status_code == 200
    assert r.json()["agent_type"] == "my-custom-agent"


def test_builtin_agent_types_still_work(client):
    r = client.post("/templates", json={"name": "T", "task": "x", "agent_type": "researcher"})
    assert r.status_code == 200
    assert r.json()["agent_type"] == "researcher"


def test_legacy_enum_name_rows_normalize_on_read(client):
    """Old rows persisted the enum NAME ("BUILDER"). They must read back as the
    canonical value so deep links / session starts keep working."""
    import sqlite3
    from pathlib import Path

    created = client.post("/templates", json={"name": "Old", "task": "x"}).json()
    root = client.app.state.platform.config.home  # type: ignore[attr-defined]
    db = Path(str(root)) / "ironjarvis.db"
    con = sqlite3.connect(db)
    con.execute(
        "UPDATE savedpromptrecord SET agent_type='BUILDER' WHERE id=?", (created["id"],)
    )
    con.commit()
    con.close()
    row = next(t for t in client.get("/templates").json()["templates"] if t["id"] == created["id"])
    assert row["agent_type"] == "builder"


# --- PATCH /templates/{id} ----------------------------------------------------


def test_edit_changes_only_what_was_sent(client):
    t = client.post(
        "/templates",
        json={"name": "A", "task": "orig task", "description": "orig", "agent_type": "builder"},
    ).json()
    r = client.patch(f"/templates/{t['id']}", json={"name": "B"})
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "B"
    assert body["task"] == "orig task"
    assert body["description"] == "orig"


def test_edit_can_unpin_a_model(client):
    t = client.post(
        "/templates",
        json={"name": "A", "task": "x", "provider": "anthropic", "model": "claude-x"},
    ).json()
    r = client.patch(f"/templates/{t['id']}", json={"clear_model": True})
    assert r.status_code == 200
    assert r.json()["provider"] is None and r.json()["model"] is None


def test_edit_unknown_template_is_404(client):
    assert client.patch("/templates/nope", json={"name": "X"}).status_code == 404


# --- requirement detection (API level: unmet needs carry setup links) ---------


def test_pinned_unavailable_model_is_flagged_with_setup_link(client):
    client.post(
        "/templates",
        json={"name": "P", "task": "hello", "provider": "ghost", "model": "gone-1"},
    )
    row = next(t for t in client.get("/templates").json()["templates"] if t["name"] == "P")
    assert row["ready"] is False
    req = next(r for r in row["requirements"] if r["key"] == "model")
    assert req["ok"] is False
    assert req["setup_path"] == "/connections"


def test_telegram_task_flags_the_channels_page(client):
    client.post("/templates", json={"name": "TG", "task": "Send me a Telegram summary"})
    row = next(t for t in client.get("/templates").json()["templates"] if t["name"] == "TG")
    req = next(r for r in row["requirements"] if r["key"] == "telegram")
    assert req["ok"] is False
    assert req["setup_path"] == "/channels"


def test_a_plain_template_is_ready(client):
    client.post("/templates", json={"name": "Plain", "task": "Summarize the file I mention."})
    row = next(t for t in client.get("/templates").json()["templates"] if t["name"] == "Plain")
    assert row["requirements"] == []
    assert row["ready"] is True


# --- requirement detection (pure function: both sides of every checker) -------

_CTX = dict(
    selectable_models=[{"provider": "anthropic", "model": "claude-x"}],
    live_tools=["web_search", "documents_read"],
    has_secret=lambda name: None,
    comm_config={},
    agent_names=["builder", "researcher"],
)


def _reqs(task="", provider=None, model=None, agent="builder", **over):
    ctx = {**_CTX, **over}
    return analyze_requirements(task, provider, model, agent, **ctx)


def test_media_needs_pixio_secret_and_clears_when_present():
    missing = _reqs("Generate an image of a lighthouse")
    assert [r["ok"] for r in missing if r["key"] == "media"] == [False]
    assert missing[0]["setup_path"] == "/secrets"
    present = _reqs("Generate an image of a lighthouse", has_secret=lambda n: "key" if n == "pixio" else None)
    assert [r["ok"] for r in present if r["key"] == "media"] == [True]


def test_drafting_an_email_needs_nothing():
    """'Draft a follow-up email' is text generation — flagging it would train
    users to ignore the warnings."""
    assert _reqs("Draft a polite follow-up email to a client.") == []


def test_reading_inbox_needs_an_email_plugin():
    flagged = _reqs("Check my unread emails and summarize them")
    email = next(r for r in flagged if r["key"] == "email")
    assert email["ok"] is False and email["setup_path"] == "/tools"
    ok = _reqs(
        "Check my unread emails and summarize them",
        live_tools=["mcp_gmail__list_messages"],
    )
    assert next(r for r in ok if r["key"] == "email")["ok"] is True


def test_pinned_model_ok_when_connected():
    reqs = _reqs("hello", provider="anthropic", model="claude-x")
    assert next(r for r in reqs if r["key"] == "model")["ok"] is True


def test_deleted_dynamic_agent_is_flagged():
    reqs = _reqs("hello", agent="ghost-agent")
    agent = next(r for r in reqs if r["key"] == "agent")
    assert agent["ok"] is False and agent["setup_path"] == "/agents"


def test_slack_task_checks_comm_config():
    flagged = _reqs("Post the summary to Slack")
    assert next(r for r in flagged if r["key"] == "slack")["ok"] is False
    ok = _reqs("Post the summary to Slack", comm_config={"slack": {"webhook_url": "x"}})
    assert next(r for r in ok if r["key"] == "slack")["ok"] is True


# --- starter library ----------------------------------------------------------


def test_starter_library_lists_the_catalog(client):
    starters = client.get("/templates/starters").json()["starters"]
    assert len(starters) == len(STARTER_CATALOG)
    for s in starters:
        assert {"id", "name", "task", "description", "requirements", "ready", "already_added"} <= set(s)
        assert "seed" not in s  # internal flag, not API surface


def test_adding_a_starter_marks_it_added(client):
    starters = client.get("/templates/starters").json()["starters"]
    target = starters[0]
    assert target["already_added"] is False
    client.post(
        "/templates",
        json={"name": target["name"], "task": target["task"], "description": target["description"]},
    )
    again = client.get("/templates/starters").json()["starters"]
    assert next(s for s in again if s["id"] == target["id"])["already_added"] is True


def test_connection_needing_starters_say_so(client):
    """The point of the feature: a starter that needs a connection SAYS so and
    links the setup page, instead of failing at run time."""
    starters = client.get("/templates/starters").json()["starters"]
    tg = next(s for s in starters if s["id"] == "telegram-status")
    assert tg["ready"] is False
    assert any(r["setup_path"] == "/channels" for r in tg["requirements"])
    img = next(s for s in starters if s["id"] == "generate-image")
    assert any(r["setup_path"] == "/secrets" for r in img["requirements"])


def test_first_run_seeds_only_connection_free_starters(tmp_path):
    """The empty-store seed must install templates that work on click one —
    nothing that immediately shows a warning chip."""
    from iron_jarvis.core.db import init_db, make_engine
    from iron_jarvis.templates import TemplateStore

    engine = make_engine(str(tmp_path / "t.db"))
    init_db(engine)
    store = TemplateStore(engine)
    seeded = store.seed_starters()
    assert seeded == sum(1 for e in STARTER_CATALOG if e.get("seed"))
    assert seeded >= 3
    assert store.seed_starters() == 0  # never re-seeds
