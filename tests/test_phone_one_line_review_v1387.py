"""Readiness 12B review (0e.4): one line on the phone — but never NO line.

The lane made a parked workflow tell the phone once: the engine's park line
and the schedule's "needs you" line leave out every destination the
ANSWERABLE question reaches. It decided "reaches" from the CONFIGURATION (a
two-way destination with an established conversation at its own address),
not from what was SENT — so whenever the answerable copy did not go out, the
phone got nothing at all where it used to get two lines:

* registering the prompt failed (``PendingPromptStore.register`` → None —
  a locked database, a disk error);
* the send of the question failed (the channel answered an error once);
* nothing registered the question at all — a workflow run in a process
  without the daemon's ``workflow.waiting`` handler (``ironjarvis
  workflow``): the phone then heard neither the question nor the park.

Now a destination is skipped only when the question for THAT run was really
delivered there (``comm.prompts.note_question_sent``, written by the handler
after a send that succeeded); otherwise the old lines go, and the park line
says how to answer ("answer it in chat or on the Workflows page").

Same harness as the lane's own pins (the real app factory, the real Telegram
channel with a recording transport, the real engine and scheduler fire).
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.comm import prompts as prompts_mod
from iron_jarvis.daemon.app import create_app
from tests.test_phone_one_line_v1387 import (
    PARK_LINE,
    QUESTION_LINE,
    SCHEDULE_HEAD,
    SENDER,
    FakeTelegram,
    _gated_workflow,
    _phone,
    _wait,
)

pytestmark = pytest.mark.usefixtures("no_network")


class FlakyTelegram(FakeTelegram):
    """Telegram answering an error to the FIRST message only."""

    def __init__(self) -> None:
        super().__init__()
        self.failed: list[str] = []

    def post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.failed:
            self.failed.append(str(payload.get("text")))
            return {"status_code": 500}
        return super().post(url, payload)


def _fire(client: TestClient) -> None:
    _gated_workflow(client)
    r = client.post(
        "/schedules",
        json={"name": "fire-gated", "cron": "0 8 * * *", "kind": "workflow", "payload": {"workflow": "gated"}},
    )
    assert r.status_code == 200, r.text
    assert client.post("/schedules/fire-gated/run").json()["last_status"] == "needs_you"


@pytest.mark.timeout(120)
def test_a_prompt_that_could_not_be_registered_leaves_the_phone_its_lines(tmp_path, monkeypatch):
    monkeypatch.setattr(prompts_mod.PendingPromptStore, "register", lambda self, *a, **k: None)
    with TestClient(create_app(str(tmp_path / "home"))) as client:
        fake = FakeTelegram()
        _phone(client, fake)
        _fire(client)
        _wait(lambda: PARK_LINE in fake.texts(), what="the park line on the phone")
        texts = fake.texts()
        assert QUESTION_LINE not in texts, texts  # nothing answerable went out
        # The line the user can act on — how to answer — and the schedule's.
        assert any(t.startswith(SCHEDULE_HEAD) for t in texts), texts


@pytest.mark.timeout(120)
def test_a_question_whose_send_failed_leaves_the_phone_its_lines(tmp_path):
    with TestClient(create_app(str(tmp_path / "home"))) as client:
        fake = FlakyTelegram()
        _phone(client, fake)
        _fire(client)
        assert fake.failed and fake.failed[0].startswith("Iron Jarvis: Workflow"), fake.failed
        _wait(lambda: PARK_LINE in fake.texts(), what="the park line on the phone")
        assert any(t.startswith(SCHEDULE_HEAD) for t in fake.texts()), fake.texts()


@pytest.mark.timeout(120)
def test_a_run_nobody_registered_the_question_for_still_tells_the_phone(tmp_path):
    """A workflow parked where no ``workflow.waiting`` handler runs (the CLI's
    own process): the phone gets the park line, as before the lane."""
    with TestClient(create_app(str(tmp_path / "home"))) as client:
        bus = client.app.state.platform.event_bus
        bus._handlers[:] = [h for h in bus._handlers if getattr(h, "__name__", "") != "_on_workflow_waiting"]
        fake = FakeTelegram()
        _phone(client, fake)
        _gated_workflow(client)
        assert client.post("/workflows/run", json={"name": "gated"}).status_code < 300
        _wait(lambda: PARK_LINE in fake.texts(), what="the park line on the phone")
        assert QUESTION_LINE not in fake.texts()


@pytest.mark.timeout(120)
def test_the_answerable_copy_still_replaces_both_lines_when_it_went_out(tmp_path):
    """The lane's outcome holds once the question really went out (control)."""
    with TestClient(create_app(str(tmp_path / "home"))) as client:
        fake = FakeTelegram()
        _phone(client, fake)
        _fire(client)
        _wait(lambda: QUESTION_LINE in fake.texts(), what="the answerable question")
        time.sleep(0.3)
        assert fake.texts() == [QUESTION_LINE], fake.texts()


def test_what_was_sent_is_kept_per_run_and_bounded():
    prompts_mod.note_question_sent("run-a", "tg", SENDER)
    prompts_mod.note_question_sent("run-a", "mail", "Boss@Acme.com")
    assert prompts_mod.sent_question_addresses("run-a") == {"tg": {str(SENDER)}, "mail": {"boss@acme.com"}}
    assert prompts_mod.sent_question_addresses("run-b") == {}
    for i in range(prompts_mod._SENT_MAX + 5):
        prompts_mod.note_question_sent(f"flood-{i}", "tg", SENDER)
    assert prompts_mod.sent_question_addresses("run-a") == {}
    assert len(prompts_mod._SENT) == prompts_mod._SENT_MAX
