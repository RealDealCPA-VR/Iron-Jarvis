"""A folder of documents becomes ONE summary sheet, with progress (v1.251.0, C-04).

``batch_documents`` has existed since v1.133.0 and almost nobody has ever run
it, for a reason the codebase states out loud: ``tools/autoselect`` keeps it OUT
of the auto-armable set on COST grounds — about one model call per document
(default 25, cap 100) — and says it "remains one click away in the '+' menu,
which is the interactive consent its cost profile deserves". So the tool was
never the missing piece. The missing pieces were:

* nobody was ever TOLD the folder they are pointed at could be summarised, or
  what it would cost, so the click never happened;
* the run is ONE tool call that can spend minutes and real money, and
  ``registry.invoke`` publishes ``tool.executed`` exactly ONCE — when it
  returns. A user watching a silent screen for four minutes cannot tell work
  from a hang, which is this app's most-reported complaint.

What this file pins:

1. THE PREVIEW AGREES WITH THE RUN. Both answers come from the same ``sweep`` +
   ``ocr_settings`` pair the pipeline itself uses, so the number on the card is
   the number the batch honours — never a second counter that drifts.
2. THE COUNT IS HONEST ABOUT WHAT IT LEAVES OUT. Subfolders, unsupported types,
   policy denials and over-the-cap files come back with ``sweep``'s own reason
   per entry; a count that silently omits files reads as complete.
3. PROGRESS IS REAL AND PER DOCUMENT: one ``batch.file_done`` per file, 1-based
   index, correct total, naming the file and its outcome.
4. PROGRESS CAN NEVER COST WORK. A callback that raises must not lose an
   extraction that has already been paid for — the ``RecentErrorsHandler``
   lesson, where the call meant to record a failure became the failure.
5. THE DEFAULT PATH IS UNCHANGED. ``on_file=None`` is byte-for-byte the
   pre-v1.251.0 loop, which is what every existing caller relies on.
6. THE SHEET IS REACHABLE: the run answers ABSOLUTE ``created_paths``, which is
   what puts the deliverable on chat's Files rail (the v1.153.2 say-WHERE rule).
7. THE ROUTE IS NOT A PERMISSION BYPASS. The run goes through
   ``registry.invoke`` with the real permission engine and passes NO
   ``session_allow``: ``batch_documents`` is allow-tier, so a route that
   self-granted would be widening consent rather than honouring it.

Offline throughout: the router is a scripted fake (the same ``ScriptedRouter``
shape ``tests/test_batch_documents_v1133.py`` uses), and every document is
invented.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core import fs_policy
from iron_jarvis.core.events import EventType
from iron_jarvis.daemon.app import create_app
from iron_jarvis.documents.batch import run_batch, sweep
from iron_jarvis.providers.adapters.base import LLMResponse
from iron_jarvis.providers.router import RouteResult


class ScriptedRouter:
    """Replays scripted completion texts in order. An ``Exception`` item raises
    (a real-provider failure). Same shape as the v1.133.0 suite's fake."""

    def __init__(self, replies, provider="anthropic"):
        self.replies = list(replies)
        self.provider = provider
        self.calls: list[tuple[str, str]] = []

    async def complete(self, *, system, messages, tools=None, task_class=None, **kw):
        self.calls.append((system, messages[0].content))
        assert self.replies, "router called more times than the script allows"
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return RouteResult(LLMResponse(text=item), self.provider, "test-model")


def _ext_reply(summary: str, facts=()) -> str:
    return json.dumps(
        {
            "summary": summary,
            "facts": list(facts),
            "entities": {"people": [], "orgs": [], "dates": [], "amounts": []},
            "figures": [],
        }
    )


SHEETS_REPLY = json.dumps(
    {"sheets": {"Overview": [["Document", "Summary"], ["alder.txt", "Alder note"]]}}
)


def _docs(folder: Path, **bodies: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name, body in bodies.items():
        (folder / name.replace("_", ".")).write_text(body, encoding="utf-8")


@pytest.fixture
def client(tmp_path) -> TestClient:
    return TestClient(create_app(str(tmp_path / "home")))


# ------------------------------------------------- 1-2. the preview is honest --


def test_the_preview_counts_what_the_batch_would_really_process(client, tmp_path):
    """The number on the card comes from the pipeline's own sweep."""
    src = tmp_path / "client docs"
    _docs(src, alder_txt="Alder Trust note", brightwater_txt="Brightwater LLC note")
    (src / "logo.zip").write_bytes(b"\x00\x01")
    (src / "nested").mkdir()

    res = client.post("/documents/batch/preview", json={"folder": str(src)})
    assert res.status_code == 200, res.text
    body = res.json()

    assert body["count"] == 2
    assert {f["name"] for f in body["files"]} == {"alder.txt", "brightwater.txt"}
    # ...and it is the SAME answer the pipeline's own sweep gives.
    files, _skipped = sweep(src, 25)
    assert [f["path"] for f in body["files"]] == [str(p) for p in files]
    # One call per document plus the synthesis pass that writes the sheet.
    assert body["estimate_calls"] == 3
    assert body["truncated"] is False


def test_the_preview_names_what_it_leaves_out(client, tmp_path):
    """A count that silently omits files reads as complete — the truncation lie
    this repo bans. Every exclusion carries sweep's own reason."""
    src = tmp_path / "docs"
    _docs(src, keep_txt="kept")
    (src / "blob.zip").write_bytes(b"\x00\x01")
    (src / "nested").mkdir()

    body = client.post("/documents/batch/preview", json={"folder": str(src)}).json()
    reasons = {Path(s["file"]).name: s["reason"] for s in body["skipped"]}
    assert "unsupported" in reasons["blob.zip"]
    assert "subfolder" in reasons["nested"]


def test_the_preview_says_when_the_cap_would_bite(client, tmp_path):
    """A folder bigger than the cap must SAY so before the user spends: the
    card states the bound, and the files beyond it are named as skipped."""
    src = tmp_path / "docs"
    _docs(src, **{f"doc{i}_txt": f"body {i}" for i in range(5)})

    body = client.post(
        "/documents/batch/preview", json={"folder": str(src), "max_files": 3}
    ).json()
    assert (body["count"], body["cap"], body["truncated"]) == (3, 3, True)
    over = [s for s in body["skipped"] if "max_files" in s["reason"]]
    assert {Path(s["file"]).name for s in over} == {"doc3.txt", "doc4.txt"}


def test_the_preview_refuses_what_it_cannot_read(client, tmp_path):
    """Same fs policy as every other read door, and an honest status per case."""
    missing = client.post(
        "/documents/batch/preview", json={"folder": str(tmp_path / "nope")}
    )
    assert missing.status_code == 404

    assert client.post("/documents/batch/preview", json={"folder": ""}).status_code == 400
    assert (
        client.post("/documents/batch/preview", json={"folder": "docs"}).status_code
        == 400
    )

    src = tmp_path / "secret"
    _docs(src, a_txt="x")
    fs_policy.register_protected_root(src)
    try:
        denied = client.post("/documents/batch/preview", json={"folder": str(src)})
    finally:
        fs_policy._PROTECTED_ROOTS.discard(fs_policy._canonical(src))
    assert denied.status_code == 403


# --------------------------------------------------- 3-5. per-file progress ----


async def test_one_progress_report_per_document(tmp_path):
    """The whole point: something to watch while the money is spent."""
    src = tmp_path / "docs"
    _docs(src, alder_txt="Alder body", brightwater_txt="Brightwater body")
    seen: list[tuple] = []

    router = ScriptedRouter([_ext_reply("Alder"), _ext_reply("Brightwater"), SHEETS_REPLY])
    res = await run_batch(
        src, tmp_path / "out", router, output="xlsx",
        on_file=lambda i, total, name, status: seen.append((i, total, name, status)),
    )

    assert res["processed"] == 2
    assert seen == [
        (1, 2, "alder.txt", "extracted"),
        (2, 2, "brightwater.txt", "extracted"),
    ]


async def test_a_cached_rerun_still_reports_each_file(tmp_path):
    """Re-running a folder RESUMES (v1.133.0) — and a resumed file must still
    tick, or a re-run looks frozen precisely because it is fast."""
    src = tmp_path / "docs"
    _docs(src, alder_txt="Alder body")
    out = tmp_path / "out"

    await run_batch(src, out, ScriptedRouter([_ext_reply("Alder"), SHEETS_REPLY]),
                    output="xlsx")
    seen: list[tuple] = []
    res = await run_batch(
        src, out, ScriptedRouter([SHEETS_REPLY]), output="xlsx",
        on_file=lambda i, t, n, s: seen.append((i, t, n, s)),
    )

    assert (res["processed"], res["cached"]) == (0, 1)
    assert seen == [(1, 1, "alder.txt", "cached")]


async def test_a_failed_document_reports_as_failed_and_the_batch_goes_on(tmp_path):
    """Per-document failures were always collected, never fatal. The report
    must say which file failed while the rest still run.

    ONE provider failure, not two: ``extract_one``'s repair round fires only on
    a VALIDATION error (a malformed reply). A provider failure raises straight
    to the caller, so scripting two of them here would fail two DOCUMENTS — the
    first cut of this test did exactly that and measured the wrong thing."""
    src = tmp_path / "docs"
    _docs(src, alder_txt="Alder body", brightwater_txt="Brightwater body")
    seen: list[tuple] = []

    router = ScriptedRouter(
        [RuntimeError("provider hiccup"), _ext_reply("Brightwater"), SHEETS_REPLY]
    )
    res = await run_batch(
        src, tmp_path / "out", router, output="xlsx",
        on_file=lambda i, t, n, s: seen.append((i, t, n, s)),
    )

    assert len(res["failed"]) == 1 and res["processed"] == 1
    assert ("alder.txt", "failed") in [(n, s) for _i, _t, n, s in seen]
    assert ("brightwater.txt", "extracted") in [(n, s) for _i, _t, n, s in seen]


async def test_a_reporting_failure_never_costs_paid_work(tmp_path):
    """THE GUARD, mutation-proven: a callback that raises must not lose an
    extraction the user has already been billed for. Reporting is the least
    important thing in this function."""
    src = tmp_path / "docs"
    _docs(src, alder_txt="Alder body", brightwater_txt="Brightwater body")

    def _boom(*_a):
        raise RuntimeError("the progress bar exploded")

    router = ScriptedRouter([_ext_reply("Alder"), _ext_reply("Brightwater"), SHEETS_REPLY])
    res = await run_batch(
        src, tmp_path / "out", router, output="xlsx", on_file=_boom
    )

    assert (res["processed"], res["failed"]) == (2, [])
    assert res["deliverables"], "a broken callback cost the deliverable"


async def test_the_default_path_is_the_old_loop(tmp_path):
    """``on_file=None`` — every existing caller — must behave exactly as before."""
    src = tmp_path / "docs"
    _docs(src, alder_txt="Alder body")
    res = await run_batch(
        src, tmp_path / "out", ScriptedRouter([_ext_reply("Alder"), SHEETS_REPLY]),
        output="xlsx",
    )
    assert (res["processed"], res["cached"], res["failed"]) == (1, 0, [])


# ------------------------------------------- 6-7. the run route, end to end ----


def test_the_run_route_publishes_progress_and_hands_back_the_sheet(client, tmp_path):
    """The click, driven through the REAL endpoint: the batch runs under the
    real permission engine, each document ticks on the event bus, and the
    deliverable comes back as an ABSOLUTE path — which is what puts it on the
    Files rail."""
    platform = client.app.state.platform
    src = tmp_path / "client docs"
    _docs(src, alder_txt="Alder Trust body", brightwater_txt="Brightwater body")

    platform.router.complete = ScriptedRouter(
        [_ext_reply("Alder"), _ext_reply("Brightwater"), SHEETS_REPLY]
    ).complete

    seen: list[dict] = []
    original = platform.event_bus.publish

    async def _spy(type, payload=None, session_id=None):
        if type == EventType.BATCH_FILE_DONE:
            seen.append({**(payload or {}), "session_id": session_id})
        return await original(type, payload, session_id)

    platform.event_bus.publish = _spy

    res = client.post(
        "/documents/batch",
        json={
            "folder": str(src),
            "instructions": "one row per client",
            "output": "xlsx",
            "workspace_dir": str(tmp_path / "chatfolder"),
            "session_id": "chat",
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()

    assert body["report"]["processed"] == 2
    made = body["created_paths"]
    assert made and all(Path(p).is_absolute() for p in made), made
    assert all(Path(p).exists() for p in made), "the sheet was announced but not written"

    # One tick per document, in order, naming each file — and tagged with the
    # conversation so the page can tell this batch from any other.
    assert [(e["index"], e["total"], e["name"]) for e in seen] == [
        (1, 2, "alder.txt"),
        (2, 2, "brightwater.txt"),
    ]
    assert {e["status"] for e in seen} == {"extracted"}
    assert {e["session_id"] for e in seen} == {"chat"}
    assert {e["folder"] for e in seen} == {str(src)}


def test_the_run_route_does_not_self_grant(client, tmp_path):
    """ARMING IS GRANTING, so a route that passed ``session_allow`` would widen
    consent rather than honour it. ``batch_documents`` is allow-tier and needs
    no grant; this pins that the route passes none."""
    src = Path(__file__).resolve().parents[1] / "src" / "iron_jarvis" / "daemon"
    text = (src / "routes" / "documents.py").read_text(encoding="utf-8").replace(
        "\r\n", "\n"
    )
    run_route = text.split('@app.post("/documents/batch")', 1)[1].split("@app.", 1)[0]
    assert "registry.invoke" in run_route, "the route stopped going through the registry"
    # THE CALL, not the prose around it. The first cut grepped the whole handler
    # and failed on the COMMENT explaining that no grant is passed — a pin that
    # breaks when the code is well documented is asserting the wrong thing.
    call = run_route.split("registry.invoke(", 1)[1].split("\n        )", 1)[0]
    assert "session_allow" not in call, (
        "the batch route self-granted a tool the cost gate deliberately leaves "
        f"behind an interactive click: {call!r}"
    )
    # ...and the permission engine really is in the call.
    assert "permissions" in call, call


def test_the_run_route_refuses_an_unreadable_folder(client, tmp_path):
    """Nothing may run before the folder passes the same read gate."""
    assert (
        client.post("/documents/batch", json={"folder": str(tmp_path / "nope")}).status_code
        == 404
    )
    assert client.post("/documents/batch", json={"folder": ""}).status_code == 400


def test_both_endpoints_are_reachable_on_a_real_app(client):
    """The v1.238.0 lesson: a route no install could reach, with a green suite.
    Asserted against the REAL app's route table."""
    paths = {getattr(r, "path", "") for r in client.app.routes}
    assert "/documents/batch/preview" in paths
    assert "/documents/batch" in paths
