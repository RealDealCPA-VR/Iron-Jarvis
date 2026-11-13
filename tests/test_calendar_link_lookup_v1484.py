"""The feature lookup finds the settings that hold a private value (v1.484.0).

The calendar link and the speech-to-text key are settings whose value is
private: they are not in ``schema.SETTINGS`` (a value never rides a tool
argument in chat) but in ``schema.SECRETS``, and the lookup's index read only
the first. So "hook up my Google calendar so you can see my appointments"
could never reach the calendar link. Seen live, it became
``app_connect google_calendar``, which only ends in "There is no app called
'google_calendar'". Now:

* each SecretDef that stands alone (not a family like ``connection.{provider}``,
  whose own tools set it) is an index entry of kind ``secret``;
* its answer line says it is set with ``config_secret``'s secure card, and
  never claims ``config_list`` reads it;
* a lookup that finds it brings ``config_secret`` alone;
* ``app_connect`` asked for a calendar app says the way in is the calendar
  link; any other unknown app keeps its old words.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.capability_search import index as CI
from iron_jarvis.capability_search import join as CJ
from iron_jarvis.capability_search import tool as CT_TOOL
from iron_jarvis.daemon.app import create_app
from iron_jarvis.settings import records, schema

pytestmark = pytest.mark.usefixtures("no_network")


@pytest.fixture(scope="module")
def platform(tmp_path_factory):
    c = TestClient(create_app(str(tmp_path_factory.mktemp("home"))))
    yield c.app.state.platform
    try:
        c.app.state.platform.scheduler.shutdown()
    except Exception:  # noqa: BLE001
        pass


def test_each_standalone_secret_is_an_entry_and_no_family_is(platform):
    idx = CI.build(platform)
    secrets = {e.id: e for e in idx.entries if e.kind == "secret"}
    want = {s.name for s in schema.SECRETS if not s.pattern}
    assert set(secrets) == want and {"calendar_ics_url", "voice_transcribe_key"} <= want
    assert all(e.tool == "config_secret" and e.changes for e in secrets.values())
    assert "secret" in CI.KINDS
    # A family's name ("connection.{provider}") is never an entry of its own.
    assert not any("{" in e.id for e in idx.entries)


@pytest.mark.parametrize("query", [
    "connect my calendar on the internet / calendar link / calendar feed",
    "hook up google calendar so you can see my appointments",
])
def test_a_calendar_request_finds_the_calendar_link(platform, query):
    found = [e.id for _s, e in CI.index_for(platform).search(query, 5)]
    assert "calendar_ics_url" in found, found


def test_the_line_names_the_secure_card_and_never_config_list(platform):
    entry = next(e for e in CI.build(platform).entries if e.id == "calendar_ics_url")
    line, row = CT_TOOL.entry_line(platform, entry)
    assert "config_secret" in line and "secure card" in line and "Never ask" in line
    assert "config_list" not in line
    assert row["kind"] == "secret" and row["tool"] == "config_secret"


def test_a_secret_denied_by_the_user_says_so(platform, monkeypatch):
    entry = next(e for e in CI.build(platform).entries if e.id == "calendar_ics_url")
    monkeypatch.setattr(CT_TOOL, "_mode", lambda _p, _t: "deny")
    line, _row = CT_TOOL.entry_line(platform, entry)
    assert "Never run" in line and "secure card" not in line


def test_a_found_secret_brings_config_secret_alone():
    assert CJ._wanted([{"kind": "secret", "id": "calendar_ics_url", "tool": "config_secret"}]) == [
        "config_secret"]
    assert "config_secret" in CJ.joinable()


def _connect(platform, app):
    return asyncio.run(records.AppConnectTool(platform).execute({"app": app}, SimpleNamespace()))


@pytest.mark.parametrize("app", ["google_calendar", "iCal", "outlook_calendar"])
def test_a_calendar_app_points_at_the_calendar_link(platform, app):
    r = _connect(platform, app)
    assert r.ok is False and "calendar_ics_url" in r.error and "config_secret" in r.error
    assert f"There is no app called {app.lower()!r}" in r.error


def test_another_unknown_app_keeps_its_words(platform):
    r = _connect(platform, "trello")
    assert r.ok is False and r.error.startswith("There is no app called 'trello'. Known: ")
    assert "calendar" not in r.error.lower().split("known:")[0]
    assert "calendar_ics_url" not in r.error
