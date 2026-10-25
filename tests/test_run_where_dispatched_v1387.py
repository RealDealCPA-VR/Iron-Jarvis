"""Readiness 12A (0f.3) — the run page and the receipt agree on where a run
went when a cloud call went out and then failed before any step.

THE LEFTOVER (docs/PRODUCT-READINESS-RESULTS.md 0f.3, "Run page and receipt
can differ"): ``GET /sessions/{id}/result`` judges ``on_this_network`` from
``agents/outcome.reached_a_model``, which counts the router's dispatch events
(``provider.routed`` is published right before an adapter is called). The run
page decided from its TRANSCRIPT alone (a step, tokens or a tool call), so a
cloud call that left the network and then failed read "cloud" on the receipt
and nothing on the run page.

NOW the receipt carries the daemon's own verdict, ``reached_a_model``
(additive; an older daemon sends none), and the page trusts an explicit
``true`` beside its transcript. A run the router refused before sending
anything still says nothing on both.

The contract fixture ``dashboard/__tests__/fixtures/run-where-dispatched-v1387.json``
is the REAL daemon's two reads for both shapes (``IJ_REGEN_RUN_WHERE_DISPATCHED=1``);
``dashboard/__tests__/run-failure-words-v1387.test.tsx`` renders the real run
page from it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi.testclient import TestClient

from iron_jarvis.core.events import EventType
from iron_jarvis.daemon.app import create_app
from tests.test_run_where_reached_v13610 import _seed

_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = _ROOT / "dashboard" / "__tests__" / "fixtures" / "run-where-dispatched-v1387.json"


def _reads(client, sid: str) -> dict:
    detail = client.get(f"/sessions/{sid}").json()
    result = client.get(f"/sessions/{sid}/result").json()
    return {"detail": detail, "result": result}


def test_a_dispatched_then_failed_run_says_reached_and_a_refused_one_does_not(tmp_path):
    client = TestClient(create_app(str(tmp_path / "home")))
    engine = client.app.state.platform.engine
    _seed(engine, "sess_dispatched", event=EventType.PROVIDER_ROUTED)
    _seed(engine, "sess_refused", event=EventType.PROVIDER_DOWNGRADED)
    sent = _reads(client, "sess_dispatched")
    refused = _reads(client, "sess_refused")
    # The transcript shows nothing for either (the page's own evidence) …
    for got in (sent, refused):
        runs = got["detail"]["transcript"]["runs"]
        assert runs and all(r["steps"] == 0 and r["input_tokens"] == 0 for r in runs)
        assert got["detail"]["transcript"]["tools"] == []
    # … so only the daemon's own verdict can tell them apart (RED on the old
    # code: no such key).
    assert sent["result"]["reached_a_model"] is True
    assert sent["result"]["on_this_network"] is False
    assert refused["result"]["reached_a_model"] is False
    assert refused["result"]["on_this_network"] is None


def test_the_contract_fixture_is_the_real_daemons_answer(tmp_path):
    client = TestClient(create_app(str(tmp_path / "home")))
    engine = client.app.state.platform.engine
    _seed(engine, "sess_dispatched", event=EventType.PROVIDER_ROUTED)
    _seed(engine, "sess_refused", event=EventType.PROVIDER_DOWNGRADED)
    data = {
        "about": (
            "Readiness 12A (0f.3): the REAL daemon's GET /sessions/{id} and GET /sessions/{id}/result "
            "for a cloud run that was dispatched and failed before any step, and one the router refused "
            "before sending anything. Written by tests/test_run_where_dispatched_v1387.py "
            "(IJ_REGEN_RUN_WHERE_DISPATCHED=1); read by dashboard/__tests__/run-failure-words-v1387.test.tsx."
        ),
        "dispatched": _reads(client, "sess_dispatched"),
        "refused": _reads(client, "sess_refused"),
    }
    if os.environ.get("IJ_REGEN_RUN_WHERE_DISPATCHED") == "1":
        FIXTURE.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    assert json.loads(FIXTURE.read_text(encoding="utf-8")) == json.loads(json.dumps(data)), (
        "regenerate with IJ_REGEN_RUN_WHERE_DISPATCHED=1 after an intended change"
    )
