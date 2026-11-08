"""Readiness 0j.5 item 18 (b) — a phone run's result names its files.

A job started from the phone that wrote a report answered only with its
summary; the user could not tell from the phone that a file had been made (or,
for a job that failed, which files it left). ``InboundPoller._deliver_session``
now adds one line read off the run's own ledger card — "Files it wrote:
report.md." for a finished run, "The files it wrote are kept: …" for one that
did not finish — names only, never where on this PC they are.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from iron_jarvis.agents import outcome as O
from iron_jarvis.comm.base import InboundMessage
from iron_jarvis.comm.inbound import InboundPoller
from iron_jarvis.core.models import SessionStatus


class _Ch:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def send(self, body, chat_id=None):
        self.sent.append(body)
        return True


class _Orch:
    def __init__(self, status, summary) -> None:
        self.status, self.summary = status, summary

    async def run_session(self, sid):
        return SimpleNamespace(id=sid, status=self.status, summary=self.summary)


def _poller(orch) -> InboundPoller:
    platform = SimpleNamespace(engine=object(), providers=None)
    return InboundPoller(SimpleNamespace(), orch, platform.engine, platform=platform)


def _msg() -> InboundMessage:
    return InboundMessage(sender_id="42", text="write the report", reply_to="42")


@pytest.fixture
def card(monkeypatch):
    state: dict = {}

    def fake(engine, sid, **kw):
        return state["card"]

    monkeypatch.setattr(O, "session_result", fake)
    return state


async def test_a_finished_phone_run_names_the_files_it_wrote(card):
    card["card"] = {"files_created": ["out/report.md", "q3.xlsx"], "files_created_total": 2}
    ch = _Ch()
    poller = _poller(_Orch(SessionStatus.COMPLETED, "The report is ready."))
    await poller._deliver_session("tg", ch, _msg(), SimpleNamespace(id="s1", provider=""))
    assert ch.sent, "the phone got no reply"
    body = ch.sent[-1]
    assert body.endswith("Files it wrote: report.md, q3.xlsx.")
    assert "out/" not in body, "names only, never the folder"


async def test_a_run_that_did_not_finish_names_the_files_it_kept(card):
    card["card"] = {"files_created": ["draft.md"], "files_created_total": 1}
    ch = _Ch()
    poller = _poller(_Orch(SessionStatus.FAILED, "The run failed: the model went away."))
    await poller._deliver_session("tg", ch, _msg(), SimpleNamespace(id="s2", provider=""))
    assert ch.sent[-1].endswith("The file it wrote is kept: draft.md.")


async def test_a_run_that_wrote_nothing_adds_nothing(card):
    card["card"] = {"files_created": [], "files_changed": []}
    ch = _Ch()
    poller = _poller(_Orch(SessionStatus.COMPLETED, "Done."))
    await poller._deliver_session("tg", ch, _msg(), SimpleNamespace(id="s3", provider=""))
    assert ch.sent[-1].endswith("Done.")


async def test_a_summary_that_already_says_it_is_not_repeated(card):
    card["card"] = {"files_created": ["draft.md"], "files_created_total": 1}
    ch = _Ch()
    summary = "The run failed: x. The file it wrote is kept: draft.md."
    poller = _poller(_Orch(SessionStatus.FAILED, summary))
    await poller._deliver_session("tg", ch, _msg(), SimpleNamespace(id="s4", provider=""))
    assert ch.sent[-1].count("is kept") == 1
