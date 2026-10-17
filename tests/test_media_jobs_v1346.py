"""v1.346.0 lane W-jobs — background media jobs that come home to the chat.

Driven with the offline ``FakeProvider`` and an INJECTED sleep/clock (no
real wait, no network, no Pixio/OpenAI call). Pins:

* a video job runs in a tracked task, delivers (workspace file + gallery
  copy), appends EXACTLY ONE line with the markdown image syntax +
  ``documents`` + ``panelKind`` media, and publishes ``media.completed`` with
  the thread id and the path;
* a failed job appends the plain-words line and publishes ``media.failed``;
* a job with no thread id appends nothing and only publishes;
* ``media_job_max_s`` ends a job failed "took too long" and the poll backoff
  doubles 2 → 4 → 8 → 15 (capped);
* the boot reconcile re-polls a row left running (the fake answers "lost at
  restart"), never re-dispatches, and leaves finished rows alone;
* the routes answer (never a key); the table is a late model; the lifespan
  reconciles at boot and cancels at shutdown; a cancel leaves the row live.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect as sa_inspect

from iron_jarvis.core.db import _LATE_MODEL_MODULES, session_scope
from iron_jarvis.core.models import ChatThreadRecord
from iron_jarvis.daemon.app import create_app
from iron_jarvis.media.fake import FIXTURE_MP4, FakeProvider
from iron_jarvis.media.jobs import (
    BACKOFF_MAX_S,
    LOST_AT_RESTART,
    MEDIA_COMPLETED,
    MEDIA_FAILED,
    TOO_LONG,
    MediaJobs,
    MediaJobSpec,
    failed_line,
    media_markdown,
    ready_line,
)
from iron_jarvis.media.models import FAILED, RUNNING, SUCCEEDED, MediaJobRecord
from iron_jarvis.media.registry import MediaRegistry

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    """Source read tolerant of a CRLF checkout (the CI runner's autocrlf)."""
    return (ROOT / rel).read_text(encoding="utf-8").replace("\r\n", "\n")


# --------------------------------------------------------------------------- #
# harness
# --------------------------------------------------------------------------- #


class FakeTime:
    """The injected clock + sleep: a sleep ADVANCES the clock, nothing waits."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds
        await asyncio.sleep(0)


class SpyFake(FakeProvider):
    """The fake with its ``start`` door counted (the no-redispatch pin)."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.starts = 0

    async def start(self, model, params, kind, idem_key):
        self.starts += 1
        return await super().start(model, params, kind, idem_key)


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path))


def _harness(app, *, delay_s=3.0, fixture=None, append=None):
    platform = app.state.platform
    ft = FakeTime()
    provider = SpyFake(enabled=True, delay_s=delay_s, clock=ft.clock, fixture=fixture)
    platform.media = MediaRegistry([provider], config=platform.config)
    jobs = MediaJobs(platform, sleep=ft.sleep, clock=ft.clock, append=append)
    platform.media_jobs = jobs
    return platform, provider, jobs, ft


def _thread(engine, messages=None) -> str:
    rec = ChatThreadRecord(title="t", messages_json=json.dumps(messages or []))
    with session_scope(engine) as db:
        db.add(rec)
        db.commit()
        db.refresh(rec)
        return rec.id


def _messages(engine, thread_id):
    with session_scope(engine) as db:
        r = db.get(ChatThreadRecord, thread_id)
        assert r is not None
        return json.loads(r.messages_json or "[]")


def _row(engine, job_id) -> MediaJobRecord:
    with session_scope(engine) as db:
        r = db.get(MediaJobRecord, job_id)
        assert r is not None
        db.expunge(r)
        return r


def _events(platform, kind):
    return [e for e in list(platform.event_bus.history) if e.type == kind]


async def _started(provider, kind="video", prompt="a free cat"):
    handle = await provider.start(f"fake/{kind}", {"prompt": prompt}, kind, "ij-test")
    return handle


def _spec(provider, handle, kind="video", workspace=None, cost_note="free (Maker: 4 left today)"):
    return MediaJobSpec(
        provider=provider.id,
        kind=kind,
        model=f"fake/{kind}",
        prompt="a free cat",
        provider_job_id=handle.job_id,
        params={"prompt": "a free cat"},
        workspace=workspace,
        cost_note=cost_note,
        eta_s=handle.eta_s,
    )


# --------------------------------------------------------------------------- #
# the happy path: a video comes home
# --------------------------------------------------------------------------- #


async def test_a_video_job_runs_in_the_background_delivers_and_comes_home(app, tmp_path):
    platform, provider, jobs, ft = _harness(app, delay_s=3.0)
    engine = platform.engine
    tid = _thread(engine, [{"role": "user", "content": "make me a video", "at": "x"}])
    handle = await _started(provider)
    # A SPACE in the workspace on purpose: the packaged home is
    # "%APPDATA%/Iron Jarvis/..." and the chat workfolder "Documents/Iron
    # Jarvis/<date> <title>" — a bare markdown destination with a space is
    # refused by CommonMark and the chat shows the literal text.
    workspace = tmp_path / "Iron Jarvis ws"

    job_id = jobs.start_background(
        _spec(provider, handle, workspace=workspace),
        thread_id=tid,
        session_id="chat",
        project_id="",
    )
    assert job_id.startswith("mj_")
    # The id is readable at once (the row lands one hop later).
    assert jobs.get_job(job_id)["status"] == "queued"
    await jobs.drain()

    row = _row(engine, job_id)
    assert row.status == SUCCEEDED
    assert row.provider_job_id == handle.job_id
    assert row.error == ""
    out = Path(row.output_path)
    assert out.is_absolute() and out.is_file()
    assert out.read_bytes() == FIXTURE_MP4.read_bytes()
    assert out.suffix == ".mp4"
    assert str(out).startswith(str((workspace / "media" / "fake").resolve()))
    # The gallery copy exists under the artifact the row names.
    assert row.artifact
    gallery = platform.artifacts.version_path(row.artifact)
    assert gallery is not None and gallery.is_file()
    assert row.cost_note == "fake"  # what the provider said it charged wins
    assert row.finished_at is not None

    # EXACTLY one line, the page's shape, through the daemon's writer.
    msgs = _messages(engine, tid)
    assert len(msgs) == 2
    line = msgs[-1]
    assert line["role"] == "assistant"
    posix = str(out).replace("\\", "/")
    assert " " in posix  # the control: this pin exercises the space case
    # Angle-bracketed destination — the only form CommonMark accepts with a
    # space; the dashboard's Markdown renders a <video> from it.
    assert line["content"] == f"Your video is ready.\n\n![video](<{posix}>)"
    assert media_markdown("image", "C:\\Iron Jarvis\\a b.png") == "![image](<C:/Iron Jarvis/a b.png>)"
    assert line["documents"] == [str(out)]
    assert line["panelKind"] == "media"
    assert line["at"]
    # The line this module builds carries the job id (the whitelist in
    # chat_append.LINE_KEYS is the integrator's to extend).
    assert ready_line("video", str(out), job_id)["mediaJob"] == job_id

    done = _events(platform, MEDIA_COMPLETED)
    assert len(done) == 1
    assert done[0].payload == {
        "job_id": job_id,
        "kind": "video",
        "provider": "fake",
        "thread_id": tid,
        "path": str(out),
        "cost_note": "fake",
    }
    assert _events(platform, MEDIA_FAILED) == []
    updated = [e for e in list(platform.event_bus.history) if e.type == "chat.thread_updated"]
    assert updated and updated[-1].payload["thread_id"] == tid and updated[-1].payload["who"] == "media"
    # The poll waited with the backoff, never a real clock: 3 s of fake delay
    # took two sleeps (2 then 4).
    assert ft.sleeps == [2.0, 4.0]
    assert jobs.live_ids() == []


async def test_a_job_the_process_cannot_poll_is_not_a_crash(app):
    """No tracked task ever raises out: an unknown provider id ends the job
    failed with words."""
    platform, provider, jobs, ft = _harness(app)
    tid = _thread(platform.engine)
    handle = await _started(provider)
    spec = _spec(provider, handle)
    spec.provider = "nope"
    job_id = jobs.start_background(spec, thread_id=tid)
    await jobs.drain()
    row = _row(platform.engine, job_id)
    assert row.status == FAILED
    assert "not connected" in row.error
    assert _events(platform, MEDIA_FAILED)[0].payload["job_id"] == job_id


# --------------------------------------------------------------------------- #
# failure words
# --------------------------------------------------------------------------- #


async def test_a_failed_job_appends_the_plain_words_line_and_publishes_media_failed(app, tmp_path):
    platform, provider, jobs, ft = _harness(app, fixture=tmp_path / "missing.mp4")
    engine = platform.engine
    tid = _thread(engine)
    handle = await _started(provider)
    job_id = jobs.start_background(_spec(provider, handle), thread_id=tid)
    await jobs.drain()

    row = _row(engine, job_id)
    assert row.status == FAILED
    assert row.error == "the fake video fixture is missing"
    assert row.output_path == ""
    msgs = _messages(engine, tid)
    assert len(msgs) == 1
    assert msgs[0]["content"] == "Your video could not be made: the fake video fixture is missing"
    assert msgs[0]["panelKind"] == "media"
    assert failed_line("video", "x", job_id)["mediaJob"] == job_id
    failed = _events(platform, MEDIA_FAILED)
    assert len(failed) == 1
    assert failed[0].payload == {
        "job_id": job_id,
        "kind": "video",
        "provider": "fake",
        "thread_id": tid,
        "error": "the fake video fixture is missing",
    }
    assert _events(platform, MEDIA_COMPLETED) == []


async def test_an_unexpected_exception_is_masked_before_it_reaches_the_row_or_the_event(app):
    platform, provider, jobs, ft = _harness(app)
    tid = _thread(platform.engine)
    handle = await _started(provider)

    async def boom(job_id):  # noqa: ANN001
        raise RuntimeError("Authorization: Bearer sk-live-SECRET-1234567890 was refused")

    provider.poll = boom  # type: ignore[method-assign]
    job_id = jobs.start_background(_spec(provider, handle), thread_id=tid)
    await jobs.drain()
    row = _row(platform.engine, job_id)
    assert row.status == FAILED
    assert "SECRET-1234567890" not in row.error
    assert "RuntimeError" in row.error
    assert "SECRET-1234567890" not in _events(platform, MEDIA_FAILED)[0].payload["error"]
    assert "SECRET-1234567890" not in _messages(platform.engine, tid)[0]["content"]


# --------------------------------------------------------------------------- #
# no chat → nothing appended, the event still goes out
# --------------------------------------------------------------------------- #


async def test_a_job_with_no_thread_id_appends_nothing_and_only_publishes(app, tmp_path):
    appends: list = []

    def spy_append(engine, thread_id, lines, **kw):  # noqa: ANN001
        appends.append((thread_id, lines))
        return {"ok": True}

    platform, provider, jobs, ft = _harness(app, append=spy_append)
    handle = await _started(provider)
    job_id = jobs.start_background(
        _spec(provider, handle, workspace=tmp_path / "ws"), thread_id="", session_id="sess_1"
    )
    await jobs.drain()
    assert _row(platform.engine, job_id).status == SUCCEEDED
    assert appends == []
    done = _events(platform, MEDIA_COMPLETED)
    assert len(done) == 1 and done[0].payload["thread_id"] == ""

    # The same spy IS used when a thread is named — the control for the
    # assertion above.
    tid = _thread(platform.engine)
    handle2 = await _started(provider, prompt="another free cat")
    jobs.start_background(_spec(provider, handle2, workspace=tmp_path / "ws"), thread_id=tid)
    await jobs.drain()
    assert len(appends) == 1 and appends[0][0] == tid
    assert appends[0][1][0]["mediaJob"]


# --------------------------------------------------------------------------- #
# the cap and the backoff
# --------------------------------------------------------------------------- #


async def test_media_job_max_s_ends_a_job_failed_took_too_long_with_doubling_backoff(app):
    platform, provider, jobs, ft = _harness(app, delay_s=100_000.0)
    platform.config.media_job_max_s = 60
    tid = _thread(platform.engine)
    handle = await _started(provider)
    job_id = jobs.start_background(_spec(provider, handle), thread_id=tid)
    await jobs.drain()

    row = _row(platform.engine, job_id)
    assert row.status == FAILED
    assert row.error.startswith(TOO_LONG)
    assert "60" in row.error
    # 2, 4, 8, then the cap — and the loop ends once the fake clock passes 60 s.
    assert ft.sleeps == [2.0, 4.0, 8.0, BACKOFF_MAX_S, BACKOFF_MAX_S, BACKOFF_MAX_S, BACKOFF_MAX_S]
    assert ft.now >= 60
    assert _messages(platform.engine, tid)[0]["content"].startswith(
        f"Your video could not be made: {TOO_LONG}"
    )
    assert _events(platform, MEDIA_FAILED)[0].payload["error"].startswith(TOO_LONG)


async def test_the_cap_is_read_live_from_the_config(app):
    platform, provider, jobs, ft = _harness(app, delay_s=100_000.0)
    platform.config.media_job_max_s = 5
    handle = await _started(provider)
    job_id = jobs.start_background(_spec(provider, handle))
    await jobs.drain()
    assert _row(platform.engine, job_id).status == FAILED
    assert ft.sleeps == [2.0, 4.0]


# --------------------------------------------------------------------------- #
# boot reconcile: re-poll, never re-dispatch
# --------------------------------------------------------------------------- #


def _plant(engine, *, status, provider_job_id="fake-gone-1", thread_id="", provider="fake"):
    row = MediaJobRecord(
        provider=provider,
        kind="video",
        model="fake/video",
        prompt="a cat",
        status=status,
        thread_id=thread_id,
        provider_job_id=provider_job_id,
    )
    with session_scope(engine) as db:
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id


async def test_boot_reconcile_repolls_a_running_row_and_never_redispatches(app):
    platform, provider, jobs, ft = _harness(app)
    engine = platform.engine
    tid = _thread(engine)
    running = _plant(engine, status=RUNNING, provider_job_id="fake-gone-1", thread_id=tid)
    queued = _plant(engine, status="queued", provider_job_id="fake-gone-2")
    done = _plant(engine, status=SUCCEEDED, provider_job_id="fake-old")
    failed = _plant(engine, status=FAILED, provider_job_id="fake-older")

    picked = await jobs.reconcile_at_boot()
    assert picked == 2
    await jobs.drain()

    # The fake does not know a job it never started: "lost at restart".
    assert _row(engine, running).status == FAILED
    assert _row(engine, running).error == LOST_AT_RESTART
    assert _row(engine, queued).status == FAILED
    assert _row(engine, queued).error == LOST_AT_RESTART
    # Finished rows untouched.
    assert _row(engine, done).status == SUCCEEDED
    assert _row(engine, failed).status == FAILED
    # NEVER a second generation.
    assert provider.starts == 0
    # The chat it was asked in hears about it; the one without a chat does not.
    msgs = _messages(engine, tid)
    assert len(msgs) == 1 and msgs[0]["content"] == f"Your video could not be made: {LOST_AT_RESTART}"
    failed_events = _events(platform, MEDIA_FAILED)
    assert {e.payload["job_id"] for e in failed_events} == {running, queued}
    # A second reconcile finds nothing live.
    assert await jobs.reconcile_at_boot() == 0


async def test_boot_reconcile_repolls_the_service_by_the_stored_job_id(app, tmp_path):
    """A service that still holds the generation (Pixio does) finishes it:
    the row is re-polled by provider_job_id and the file comes home."""
    platform, provider, jobs, ft = _harness(app, delay_s=0.0)
    engine = platform.engine
    tid = _thread(engine)
    handle = await _started(provider)  # the fake "remembers" it in-process
    row_id = _plant(engine, status=RUNNING, provider_job_id=handle.job_id, thread_id=tid)
    polled: list[str] = []
    real_poll = provider.poll

    async def spy_poll(job_id):  # noqa: ANN001
        polled.append(job_id)
        return await real_poll(job_id)

    provider.poll = spy_poll  # type: ignore[method-assign]
    assert await jobs.reconcile_at_boot() == 1
    await jobs.drain()
    assert polled == [handle.job_id]
    assert provider.starts == 1  # the one from the test's own start, none from the reconcile
    row = _row(engine, row_id)
    assert row.status == SUCCEEDED
    assert Path(row.output_path).is_file()
    assert _messages(engine, tid)[0]["content"].startswith("Your video is ready.")


async def test_a_job_this_process_drives_is_not_picked_up_again(app):
    platform, provider, jobs, ft = _harness(app)
    blocker = asyncio.Event()
    parked = asyncio.Event()

    async def hold(seconds):  # noqa: ANN001
        # The job reaching its first backoff IS the "running" signal: the
        # row was inserted and set RUNNING before the loop's first poll. No
        # clock — two thread-hop SQLite commits take what they take on a
        # loaded runner.
        parked.set()
        await blocker.wait()

    jobs._sleep = hold  # the job parks on its first backoff
    handle = await _started(provider)
    job_id = jobs.start_background(_spec(provider, handle))
    await asyncio.wait_for(parked.wait(), timeout=30)
    assert _row(platform.engine, job_id).status == RUNNING
    assert await jobs.reconcile_at_boot() == 0
    assert jobs.cancel_all() == 1
    await jobs.drain()
    # A cancel leaves the row LIVE for the next boot.
    assert _row(platform.engine, job_id).status == RUNNING
    assert _events(platform, MEDIA_FAILED) == []


# --------------------------------------------------------------------------- #
# the reads: same-tick order and a dict popped mid-listing
# --------------------------------------------------------------------------- #


def _plant_at(engine, created_at, *, status=SUCCEEDED, prompt="x"):
    """A row with a FIXED created_at — two of these share one tick, which is
    what Windows 3.12's 15.6 ms utcnow() makes routine."""
    row = MediaJobRecord(
        provider="fake",
        kind="image",
        model="fake/image",
        prompt=prompt,
        status=status,
        provider_job_id=f"pj-{prompt}",
        created_at=created_at,
    )
    with session_scope(engine) as db:
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id


def test_two_jobs_in_one_tick_list_newest_inserted_first(app):
    """The reviewer's item 1: ``created_at`` alone leaves a same-tick pair's
    order to the engine (the v1.286.0 / v1.292.0 / v1.303.3 gate reds). The
    tie-break is the SQLite rowid — insertion order. Measured: without the
    clause this SQLite's sorter happens to hand DESC ties back reverse-
    inserted (this pin stays green) while the ASC read in `_live_rows`
    comes back out of order (its pin goes red) — so the DESC half is also
    pinned on the clause itself, below."""
    platform = app.state.platform
    engine = platform.engine
    jobs = MediaJobs(platform)
    tick = datetime(2026, 10, 17, 12, 0, 0)
    first = _plant_at(engine, tick, prompt="first")
    second = _plant_at(engine, tick, prompt="second")
    third = _plant_at(engine, tick, prompt="third")
    # The control: the three really share one created_at.
    assert {_row(engine, i).created_at for i in (first, second, third)} == {tick}

    listed = [r["id"] for r in jobs.list_jobs()]
    assert listed == [third, second, first]
    # Scoped to a thread, same rule (the empty thread id is the filter here).
    assert [r["id"] for r in jobs.list_jobs(limit=2)] == [third, second]


def test_the_reconcile_reads_same_tick_rows_in_insertion_order(app):
    platform = app.state.platform
    engine = platform.engine
    jobs = MediaJobs(platform)
    tick = datetime(2026, 10, 17, 12, 0, 0)
    a = _plant_at(engine, tick, status=RUNNING, prompt="a")
    b = _plant_at(engine, tick, status=RUNNING, prompt="b")
    c = _plant_at(engine, tick, status="queued", prompt="c")
    assert [r.id for r in jobs._live_rows()] == [a, b, c]


def test_both_order_clauses_tie_break_on_the_rowid():
    """The SQL pin: a `created_at`-only ORDER BY happens to answer insertion
    order for ties on an idle SQLite today, which is why the behavioural pin
    above is paired with the clause itself (the repo rule names rowid)."""
    src = _read("src/iron_jarvis/media/jobs.py")
    head, _, rest = src.partition("def list_jobs(")
    list_body, _, _ = rest.partition("def get_job(")
    assert "MediaJobRecord.created_at.desc()" in list_body
    assert 'text("rowid DESC")' in list_body
    _, _, live = src.partition("def _live_rows(")
    live_body, _, _ = live.partition("def _insert(")
    assert "MediaJobRecord.created_at.asc()" in live_body
    assert 'text("rowid")' in live_body
    assert "from sqlalchemy import text" in head


def test_list_jobs_survives_a_job_finishing_mid_listing(app):
    """The reviewer's item 2: ``list_jobs`` runs on a route threadpool thread
    while ``_task_done`` (the loop's done-callback) pops ``_live``. Iterating
    the live dict raises RuntimeError and the route 500s; a snapshot does
    not. The pop is staged from inside the loop body (the first view's
    ``get``), which is exactly where a finishing job lands."""
    platform = app.state.platform
    jobs = MediaJobs(platform)

    class PopsAnother(dict):
        def get(self, key, default=None):  # noqa: ANN001
            # A job finishing while this one is being read.
            jobs._live.pop("mj_finishing", None)
            return super().get(key, default)

    jobs._live["mj_first"] = PopsAnother(
        {"id": "mj_first", "thread_id": "t1", "status": "running"}
    )
    jobs._live["mj_finishing"] = {"id": "mj_finishing", "thread_id": "t1", "status": "running"}
    jobs._live["mj_third"] = {"id": "mj_third", "thread_id": "t1", "status": "queued"}

    rows = jobs.list_jobs(thread_id="t1")
    ids = [r["id"] for r in rows]
    assert "mj_first" in ids and "mj_third" in ids
    # The finishing job was popped from the live dict mid-listing: the
    # snapshot still lists it (its row lands one hop later), the dict is
    # smaller now, and nothing raised.
    assert "mj_finishing" not in jobs._live
    assert set(jobs._live) == {"mj_first", "mj_third"}

    # The route itself answers 200 under the same race.
    jobs._live["mj_finishing"] = {"id": "mj_finishing", "thread_id": "t1", "status": "running"}
    platform.media_jobs = jobs
    r = TestClient(app).get("/media/jobs", params={"thread_id": "t1"})
    assert r.status_code == 200
    assert {j["id"] for j in r.json()["jobs"]} >= {"mj_first", "mj_third"}


# --------------------------------------------------------------------------- #
# the routes
# --------------------------------------------------------------------------- #


async def test_the_routes_answer_the_jobs_and_never_a_key(app, tmp_path):
    platform, provider, jobs, ft = _harness(app)
    tid = _thread(platform.engine)
    other = _thread(platform.engine)
    handle = await _started(provider)
    job_id = jobs.start_background(_spec(provider, handle, workspace=tmp_path / "ws"), thread_id=tid)
    await jobs.drain()
    handle2 = await _started(provider, prompt="free dog")
    other_id = jobs.start_background(_spec(provider, handle2, workspace=tmp_path / "ws"), thread_id=other)
    await jobs.drain()

    client = TestClient(app)
    r = client.get("/media/jobs", params={"thread_id": tid})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1 and body["jobs"][0]["id"] == job_id
    assert body["jobs"][0]["status"] == "succeeded"
    assert body["jobs"][0]["output_path"].endswith(".mp4")
    r_all = client.get("/media/jobs")
    assert {j["id"] for j in r_all.json()["jobs"]} == {job_id, other_id}
    assert r_all.json()["jobs"][0]["id"] == other_id  # newest first

    one = client.get(f"/media/jobs/{job_id}")
    assert one.status_code == 200
    view = one.json()
    assert set(view) == {
        "id", "provider", "kind", "model", "prompt", "params", "status", "thread_id",
        # v1.347.0: the turn that asked + when the line reached the chat.
        "turn_id", "homed_at",
        "session_id", "project_id", "provider_job_id", "output_path", "artifact", "error",
        "cost_note", "created_at", "finished_at",
    }
    assert view["params"] == {"prompt": "a free cat"}
    lowered = json.dumps(view).lower()
    assert "authorization" not in lowered and "bearer" not in lowered and "api_key" not in lowered
    assert client.get("/media/jobs/mj_nope").status_code == 404


# --------------------------------------------------------------------------- #
# the table, the lifespan
# --------------------------------------------------------------------------- #


def test_the_media_job_table_is_a_late_model_and_exists_on_a_fresh_db(app):
    assert "..media.models" in _LATE_MODEL_MODULES
    names = sa_inspect(app.state.platform.engine).get_table_names()
    assert "mediajob" in names


def test_build_platform_attaches_media_jobs(app):
    assert isinstance(app.state.platform.media_jobs, MediaJobs)


def test_the_lifespan_reconciles_at_boot_and_cancels_at_shutdown(app):
    calls: list[str] = []

    class Spy:
        async def reconcile_at_boot(self):
            calls.append("reconcile")
            return 0

        def cancel_all(self):
            calls.append("cancel")
            return 0

        async def drain(self):
            calls.append("drain")

    app.state.platform.media_jobs = Spy()
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert calls == ["reconcile"]
    assert calls == ["reconcile", "cancel", "drain"]


def test_the_lifespan_wiring_is_in_the_source():
    src = _read("src/iron_jarvis/daemon/app.py")
    assert "await _media_jobs.reconcile_at_boot()" in src
    assert "_media_jobs_svc.cancel_all()" in src
    # Beside the other reconcile steps, before the loops are armed.
    assert src.index("reconcile_media_jobs") < src.index('bg_tasks: dict[str, asyncio.Task] = {}')
