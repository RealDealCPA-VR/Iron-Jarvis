"""``transcribe_recording`` (idea 33, phase 33-a): hand a chat a recording.

allow, REVERSIBLE (it only makes NEW files in the chat's folder: ``<name>
transcript.md`` and ``<name> notes.md``), and its result carries the speech
of the recording, so it is fenced as DATA (``returns_untrusted_content``).

It starts the job (``documents/recording_jobs``) and waits for it up to
``INLINE_WAIT_S``: a voice memo is done by then and its transcript and notes
are this reply's. A longer recording keeps working after the reply; the card
under it (``config_cards`` kind ``recording``) shows how far it is, and the
job writes ONE report into the chat when it ends. The same recording handed
again in the same folder is the job already made (no second pair of files).

Its Undo stops a job still running (the worker's tree is killed) and removes
the two files it wrote, each only while it still holds the bytes it was
written with, and drops the transcript's kept minutes. A file edited since
refuses the whole Undo, and that is checked BEFORE the job is touched: a
refused Undo leaves it working (its notes still come).

A call cancelled while it waits (or reads the job's end) leaves no job
unaccounted for (``recording_jobs.reply_stopped``): the reply's Stop, or the
call's own deadline, stops the job; anything else lets it carry on; either
way its end comes home as its one report. The call also WATCHES its turn's
Stop itself (:func:`_wait_or_stop`): the stream lane, the Chat page's own,
never cancels a plain tool that is running, so without it a Stop there did
nothing. The wait itself ends before the call's deadline
(:data:`DEADLINE_MARGIN_S`).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from ..core.fs_policy import fs_read_ok
from ..tools.base import Reversibility, RiskClass, Tool, ToolContext, ToolResult, safe_path
from . import recording as R
from . import recording_jobs as J
from .tools import _resolve_read_path

#: How much of a finished transcript rides in the result (the rest is in
#: the file: big results are referenced, not pasted).
TRANSCRIPT_IN_RESULT = 3000
#: How much of the notes rides in the result.
NOTES_IN_RESULT = 6000
#: On the PHONE the reply waits this long (no card reaches it), inside the
#: per-call deadline.
PHONE_WAIT_S = 480.0


def _turn_of(ctx: ToolContext) -> str:
    turn = getattr(ctx, "source_turn", None)
    return str(getattr(turn, "turn_id", "") or getattr(ctx, "turn_id", "") or "")


def _chat_model_of(ctx: ToolContext) -> "tuple[str, str] | None":
    """The chat's own model, as the chat lane resolved it for this turn
    (``ToolContext.chat_model``): the one a cloud notes offer names."""
    raw = getattr(ctx, "chat_model", None)
    if isinstance(raw, (list, tuple)) and len(raw) == 2 and str(raw[0] or "").strip():
        return str(raw[0]).strip(), str(raw[1] or "").strip()
    return None


def _answers_the_phone() -> bool:
    try:
        from ..providers.failure_words import answers_the_phone

        return answers_the_phone() is True
    except Exception:  # noqa: BLE001 — unsure: no card is promised
        return True


def _chat_key(thread_id: Any, turn_id: Any) -> str:
    """The chat a call belongs to: its saved chat, or (a new chat's first
    turn, not linked yet) its turn."""
    tid = str(thread_id or "")
    return f"chat:{tid}" if tid else f"turn:{turn_id or ''}"


async def pending_outlook(env: J.Env, state: "dict[str, Any]") -> "dict[str, Any]":
    """For the reply about a recording still being heard (on the phone and on
    this PC): who would write its notes as things stand
    (:func:`recording_jobs.notes_outlook`, the job's own reading), so the
    reply promises only what happens with no press. Unsure: ``{}`` (the reply
    then promises only the transcript)."""
    try:
        choice, yes, local_only = await J.notes_outlook(env, state)
        out: dict[str, Any] = {"choice": choice, "yes": yes}
        if choice is None:
            out["nobody"] = J.nobody_words(env.router(), J.chat_model_of(state), local_only=local_only,
                                           pending=True)
        return out
    except Exception:  # noqa: BLE001 — never a failed reply over the words
        return {}


def find_earlier(home: Path, sha256: str, workspace: Path) -> "dict[str, Any] | None":
    """A job already made for these bytes in this folder that is running or
    finished with its transcript still there (not one that failed or was
    undone). BLOCKING."""
    folder = J.jobs_dir(home)
    if not folder.is_dir():
        return None
    best: "dict[str, Any] | None" = None
    for i, path in enumerate(folder.glob("rec_*.json")):
        if i > 2000 or path.name.endswith((".lines.json", ".progress.json")):
            continue
        try:
            st = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(st, dict) or st.get("sha256") != sha256:
            continue
        if Path(str(st.get("workspace") or "")) != workspace:
            continue
        status = st.get("status")
        if status in (J.STATUS_FAILED, J.STATUS_UNDONE):
            continue
        if status not in J.RUNNING and not (st.get("transcript_written") and Path(str(st.get("transcript"))).is_file()):
            continue
        if best is None or str(st.get("created_at") or "") > str(best.get("created_at") or ""):
            best = st
    return best


def phone_notes_words(model_name: str, name: Any) -> str:
    """On the PHONE (no card there), why the notes wait, and what to do: the
    yes is asked on the recording's card, which only Iron Jarvis on this PC
    shows. The job keeps waiting for it, so handing the same recording to a
    chat there shows the card (in the same folder nothing is heard again)."""
    return (
        f"The notes are not written yet: no model on this PC or your network is set up, and writing them "
        f"sends the transcript to {model_name}, which needs the user's yes on the recording's card. This "
        f"conversation cannot show that card, and nothing was sent. Tell the user: to have the notes written, "
        f"hand {name} to a chat in Iron Jarvis on this PC and press Write the notes on its card. Do not write "
        "the notes yourself.")


def phone_pending_notes_words(outlook: "dict[str, Any]", name: Any) -> str:
    """On the PHONE, for a recording still being heard: what happens to its
    notes with no press, from :func:`recording_jobs.notes_outlook` as things
    stand. Only notes written with no press are promised: the yes for a
    cloud model is given on the recording's card, which only Iron Jarvis on
    this PC shows."""
    choice = outlook.get("choice") if isinstance(outlook.get("choice"), dict) else None
    if choice and outlook.get("yes"):
        if choice.get("local"):
            return (f"The notes will be written there too, by {choice.get('name')}: the transcript does not leave "
                    "this PC or your network.")
        return (f"The notes will be written there too, by {choice.get('name')}: the user already said yes to "
                "sending a recording's transcript there, in the chat it was handed to.")
    if choice:
        return (
            f"The notes will not be written by themselves: no model on this PC or your network is set up, and "
            f"writing them sends the transcript to {choice.get('name')}, which needs the user's yes on the "
            "recording's card. This conversation cannot show that card, and nothing is sent without it. Tell "
            f"the user: once the transcript is done, to have the notes written, hand {name} to a chat in Iron "
            "Jarvis on this PC and press Write the notes on its card.")
    if outlook.get("nobody"):
        return str(outlook["nobody"])
    # Not known: promise nothing about the notes.
    return ("The notes are written there too only by a model on this PC or your network; otherwise they wait "
            f"for the user's yes on the recording's card, which only Iron Jarvis on this PC shows (hand {name} "
            "to a chat there and press Write the notes).")


def pending_home_words(outlook: "dict[str, Any] | None", state: "dict[str, Any]") -> str:
    """On THIS PC, for a recording still being heard: what comes to the chat
    by itself, from :func:`recording_jobs.notes_outlook` as things stand. The
    notes are promised only when they are written with no press; notes that
    would go to a cloud model wait for the press on the card under the reply
    (the job ends in ``ask``)."""
    out = outlook or {}
    transcript, notes = state.get("transcript"), state.get("notes")
    choice = out.get("choice") if isinstance(out.get("choice"), dict) else None
    if choice and out.get("yes"):
        return ("the transcript and notes come to this chat by themselves when they are done, as new files in the "
                f"chat's folder ({transcript} and {notes}).")
    head = (f"the transcript comes to this chat by itself when it is done, as a new file in the chat's folder "
            f"({transcript}).")
    if choice:
        return (f"{head} The notes are not written by themselves: no model on this PC or your network is set up, "
                f"and writing them sends the transcript to {choice.get('name')}, so once the transcript is done "
                "the card under your reply asks the user, and only their press sends it.")
    if out.get("nobody"):
        return f"{head} {out['nobody']}"
    # Not known: promise nothing about the notes.
    return (f"{head} The notes ({notes}) come with it only when a model on this PC or your network writes them; "
            "otherwise the card under your reply asks the user first.")


def result_words(state: "dict[str, Any]", *, finished: bool, phone: bool,
                 outlook: "dict[str, Any] | None" = None, other_chat: bool = False) -> str:
    """What the model is told, from the job's own record. ``outlook``: who
    would write the notes of a recording still being heard
    (:func:`pending_outlook`). ``other_chat``: the job running is one another
    chat started (its report goes there)."""
    card = J.card_of(state)
    name = state.get("source_name")
    tr = state.get("transcriber") or {}
    where = "on this PC" if tr.get("kind") == R.THIS_PC else f"by {tr.get('who')}"
    lines: list[str] = []
    if tr.get("note"):
        lines.append(str(tr["note"]))
    if not finished:
        if phone:
            lines.append(
                f"{name} is still being turned into text {where}. It keeps going on this PC; the transcript will "
                f"be saved in the chat's folder ({Path(str(state.get('workspace')))}) when it is done. No message "
                "comes to this conversation when it is.")
            lines.append(phone_pending_notes_words(outlook or {}, name))
            lines.append("Tell the user that. Do not write a transcript or notes yourself.")
        elif other_chat:
            lines.append(
                f"{name} is still being turned into text {where} ({tr.get('who')}), for another chat that was "
                "handed it first. The card under your reply shows how far it is and, when it is done, the "
                "transcript and notes; the report of it goes to that other chat, not to this one. The files will "
                f"be new files in the chat's folder ({state.get('transcript')} and {state.get('notes')}). Nothing "
                "is written yet. Tell the user that; do not write a transcript or notes yourself.")
        else:
            lines.append(
                f"Started turning {name} into text {where} ({tr.get('who')}). It keeps working after your reply: "
                f"the card under your reply shows how far it is, and {pending_home_words(outlook, state)} Nothing "
                "is written yet. Tell the user that; do not write a transcript or notes yourself.")
        return "\n".join(lines)
    lines.append(f"Turned {name} ({card['length_words']}) into text {where}: {card['words']} words, saved as "
                 f"{card['transcript']}.")
    if card["cut_words"]:
        lines.append(card["cut_words"])
    status = state.get("status")
    if state.get("notes_written"):
        lines.append(
            f"The notes were written by {state.get('notes_by')} and each item was checked against the transcript "
            f"(its words were said near its time): {card['counts_words']}. "
            + (card["dropped_words"] + " " if card["dropped_words"] else "")
            + f"Saved as {card['notes']}.")
    elif status == J.STATUS_ASK and card["offer"] and phone:
        lines.append(phone_notes_words(card["offer"]["name"], name))
    elif status == J.STATUS_ASK and card["offer"]:
        lines.append(
            f"The notes are not written yet: no model on this PC or your network is set up, and writing them "
            f"sends the transcript to {card['offer']['name']}. The card under your reply asks the user; only "
            "their press sends it. Do not write the notes yourself.")
    elif card["notes_why"]:
        lines.append(card["notes_why"])
    try:
        if state.get("notes_written"):
            notes = Path(str(state.get("notes"))).read_text(encoding="utf-8")
            lines.append("\nThe notes:\n" + notes[:NOTES_IN_RESULT])
        transcript = Path(str(state.get("transcript"))).read_text(encoding="utf-8")
        if len(transcript) <= TRANSCRIPT_IN_RESULT:
            lines.append("\nThe transcript:\n" + transcript)
        else:
            lines.append(f"\nThe transcript is {len(transcript)} characters; read_document reads it from "
                         f"{state.get('transcript')} when you need a part of it.")
    except OSError:
        pass
    if not phone and state.get("notes_written"):
        lines.append("The card under your reply shows the notes with their times.")
    lines.append("Report only what the notes and the transcript say; add no decision or item of your own.")
    return "\n".join(lines)


def _check_unchanged(state: "dict[str, Any] | None", ws: Path) -> "list[tuple[Path, str]]":
    """The files the job has written in the chat's folder, as ``(path, the
    key of its hash)``; raises ``RevertConflict`` when one holds other bytes
    than it was written with (nothing is removed then). BLOCKING."""
    from ..tools.undo import RevertConflict

    if state is None:
        return []
    hashes = state.get("hashes") if isinstance(state.get("hashes"), dict) else {}
    targets: list[tuple[Path, str]] = []
    for key, flag in (("transcript", "transcript_written"), ("notes", "notes_written")):
        if not (state.get(flag) and state.get(key)):
            continue
        p = Path(str(state[key]))
        try:
            rel = p.resolve().relative_to(ws.resolve()).as_posix()
            targets.append((safe_path(ws, rel), str(p)))
        except Exception:  # noqa: BLE001 — outside the folder: never touched
            continue
    for target, key in targets:
        if target.exists() and J.file_sha256(target) != hashes.get(key):
            raise RevertConflict(f"{target.name} was changed after it was written; nothing was removed")
    return targets


#: The reply's wait for a recording ends this long before the call's own
#: deadline (``tool_deadline_left``), so the deadline never cuts a call that
#: waits on a recording: its words are read after the wait.
DEADLINE_MARGIN_S = 15.0


def _wait_s(phone: bool) -> float:
    want = PHONE_WAIT_S if phone else J.INLINE_WAIT_S
    try:
        from ..tools.registry import tool_deadline_left

        left = tool_deadline_left()
    except Exception:  # noqa: BLE001 — no deadline known: the wait stands
        left = None
    if left is not None:
        want = min(want, max(0.0, left - DEADLINE_MARGIN_S))
    return want


def _turn_handle(ctx: ToolContext) -> Any:
    """The running turn's handle (its Stop), or None (no named turn: the
    phone, an agent's run)."""
    try:
        from ..core.turns import TURNS

        return TURNS.handle(_turn_of(ctx))
    except Exception:  # noqa: BLE001 — no handle: the wait is the plain one
        return None


async def _wait_or_stop(job: J.Job, timeout: float, handle: Any) -> "bool | None":
    """:func:`recording_jobs.wait_inline` RACED against the reply's Stop (the
    turn's ``wait_stopped``), so a Stop ends the wait at once in BOTH chat
    lanes: the stream lane (the Chat page's) never cancels a plain tool that
    is running (its stop route says so), the POST lane does. True / False:
    the job ended / the wait ran out (it is DETACHED); None: the Stop came
    first (the job is untouched here; the caller settles it)."""
    if handle is None:
        return await J.wait_inline(job, timeout)
    waiting = asyncio.ensure_future(J.wait_inline(job, timeout))
    stop = asyncio.ensure_future(handle.wait_stopped())
    try:
        await asyncio.wait({waiting, stop}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for fut in (stop, waiting):
            if not fut.done():
                fut.cancel()
    if waiting.done() and not waiting.cancelled():
        return waiting.result()
    return None


def stopped_words(state: "dict[str, Any]", *, finished: bool) -> str:
    """What the call says when its reply was stopped while it waited for the
    recording (or read its end): the ledger's words; no reply carries them."""
    name = state.get("source_name")
    if finished:
        return (f"The reply was stopped just as {name} was done. What was saved stays in the chat's folder, and "
                "its end comes to the chat as one message.")
    if state.get("transcript_written"):
        return (f"The reply was stopped, so writing the notes of {name} was stopped too. The transcript already "
                "saved stays, and the chat is told so in one message.")
    return f"The reply was stopped, so turning {name} into text was stopped too. Nothing more is written."


def _stop_pressed(ctx: ToolContext) -> bool:
    """Is the cancel now unwinding this call a STOP: the reply's Stop (its
    turn is marked stopped) or the call's own deadline (whose record says the
    call was stopped)? Anything else (the page went away, Iron Jarvis
    closing) is not: the recording then carries on and comes home."""
    try:
        from ..tools.registry import tool_deadline_expired

        if tool_deadline_expired():
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        from ..core.turns import TURNS

        handle = TURNS.handle(_turn_of(ctx))
        return bool(handle is not None and handle.stopped)
    except Exception:  # noqa: BLE001 — unsure: it carries on and comes home
        return False


class TranscribeRecordingTool(Tool):
    name = "transcribe_recording"
    reversibility = Reversibility.REVERSIBLE  # NEW files only; one Undo removes them
    risk_class = RiskClass.PAGE_ACTION
    returns_untrusted_content = True  # the words of the recording are data
    description = (
        "Turn ONE recording (a meeting, a call or a voice memo; audio, or the sound of a video) into a "
        "transcript with the time each line was said, and notes: the decisions, the things the user owes "
        "and the things others owe, each with its time and checked against the transcript. The speech is "
        "turned into text on this PC by the offline speech model (or by the speech service the user chose "
        "for recordings in Settings). Both are saved as NEW files in the chat's folder. A long recording "
        "keeps working after your reply and comes back to this chat by itself. Use it when the user hands "
        "you a recording or asks what was said or decided in one."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "The recording (absolute, or in the chat's folder)."},
        },
        "required": ["path"],
    }

    def __init__(self, router_resolver: "Any | None" = None) -> None:
        self._router_resolver = router_resolver
        # capture_undo → execute hand-off (the registry calls both with the
        # SAME args object): execute names the files and the job in the
        # envelope the registry journals after it.
        self._pending: dict[int, dict[str, Any]] = {}

    def _env(self, ctx: ToolContext) -> J.Env:
        return J.Env(engine=ctx.engine, event_bus=ctx.event_bus, config=ctx.config,
                     router_resolver=self._router_resolver)

    async def capture_undo(self, args: dict[str, Any], ctx: ToolContext) -> "dict[str, Any] | None":
        desc = {
            "kind": "files_delete",
            "reversible": True,
            "pre_ref": None,
            "pre_inline": json.dumps({"paths": [], "mode": "raw", "sha256": {}, "recording_id": ""}),
            "pre_sha256": None,
            "post_sha256": None,
        }
        self._pending[id(args)] = desc
        return desc

    @staticmethod
    def _gate(raw: str, ctx: ToolContext) -> "tuple[Path | None, str]":
        """``(the recording, "")`` or ``(None, why not)`` in words. BLOCKING."""
        if not raw:
            return None, "Say which recording to turn into text."
        p = _resolve_read_path(raw, ctx)
        allowed, reason = fs_read_ok(p)
        if not allowed:
            return None, reason
        if not p.is_file():
            return None, f"There is no file at {p}."
        if not R.is_recording(p):
            return None, (
                f"{p.name} is not a recording. This turns audio (.m4a, .mp3, .wav, .ogg, ...) or the sound "
                "of a video (.mp4, .mov, ...) into text.")
        return p, ""

    def rehearsal_preflight(self, args: dict[str, Any], ctx: ToolContext) -> str:
        """Leap 06-b review: ``execute``'s own checks of the recording."""
        return self._gate(str(args.get("path") or "").strip() if isinstance(args, dict) else "", ctx)[1]

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        desc = self._pending.pop(id(args), None)
        raw = str(args.get("path") or "").strip()
        found, problem = await asyncio.to_thread(self._gate, raw, ctx)
        if found is None:
            return ToolResult(ok=False, error=problem)
        path = found
        from ..providers.egress import current_scope

        env = self._env(ctx)
        local_only = current_scope().on
        phone = _answers_the_phone()
        workspace = Path(ctx.workspace).resolve()
        sha = await asyncio.to_thread(J.file_sha256, path)
        earlier = await asyncio.to_thread(find_earlier, env.home, sha, workspace)
        if earlier is not None:
            job = J.live(str(earlier.get("id")))
            finished = earlier.get("status") not in J.RUNNING
            if job is not None and not finished:
                # Another call's job: a cancel here leaves it to that call.
                finished = await J.wait_inline(job, _wait_s(phone))
                earlier = job.state
            other = _chat_key(earlier.get("thread_id"), earlier.get("turn_id")) != _chat_key(
                getattr(ctx, "thread_id", ""), _turn_of(ctx))
            outlook = await pending_outlook(env, earlier) if not finished and (phone or not other) else None
            words = ("This recording was already handed to this chat's folder, so nothing new was made. "
                     + await asyncio.to_thread(lambda: result_words(earlier, finished=finished, phone=phone,
                                                                    outlook=outlook, other_chat=other)))
            return ToolResult(ok=True, output=words,
                              data={"recording": J.card_of(earlier), "recording_id": earlier["id"]})
        try:
            tr = await J.choose_now(env, local_only=local_only)
        except R.RecordingRefused as exc:
            return ToolResult(ok=False, error=str(exc))
        try:
            transcript, notes = await asyncio.to_thread(R.target_paths, workspace, path.name)
        except OSError as exc:
            return ToolResult(ok=False, error=f"No new file can be made in {workspace}: {exc}")
        state = J.new_state(source=path.resolve(), sha256=sha, workspace=workspace,
                            thread_id=getattr(ctx, "thread_id", "") or "", turn_id=_turn_of(ctx),
                            local_only=local_only, transcriber=tr, transcript=transcript, notes=notes,
                            chat_model=_chat_model_of(ctx))
        await asyncio.to_thread(J.save_state, env.home, state)
        if desc is not None:
            desc["pre_inline"] = json.dumps({
                "paths": [transcript.relative_to(workspace).as_posix(), notes.relative_to(workspace).as_posix()],
                "mode": "raw", "sha256": {}, "recording_id": state["id"],
            })
        job = J.start(env, state, tr)
        handle = _turn_handle(ctx)
        try:
            finished = await _wait_or_stop(job, _wait_s(phone), handle)
            st = job.state
            # A job that ended FAILED (also in the moment its reply was
            # stopped) saved nothing: the failure is the call's answer, and
            # nothing is reported.
            if (job.done.is_set() if finished is None else finished) and st.get("status") == J.STATUS_FAILED:
                return ToolResult(ok=False,
                                  error=str(st.get("error") or "The recording could not be turned into text."))
            if finished is not None:
                outlook = await pending_outlook(env, st) if not finished else None
                # Off the loop: the words read the two files back.
                words = await asyncio.to_thread(
                    lambda: result_words(st, finished=finished, phone=phone, outlook=outlook))
        except asyncio.CancelledError:
            # No reply carries it now: a Stop stops it, anything else lets it
            # carry on, and either way its end comes home (``reply_stopped``).
            J.reply_stopped(job, pressed=_stop_pressed(ctx))
            raise
        made = [str(st[k]) for k, w in (("transcript", "transcript_written"), ("notes", "notes_written"))
                if st.get(w)]
        if finished is None or (handle is not None and handle.stopped):
            # The reply's Stop came while the call waited or read the job's
            # end, and nothing cancelled the call (the stream lane): the call
            # settles it itself, with no await. A job still working is
            # stopped; one that ended in that moment comes home as its ONE
            # report (the stopped reply draws no card, so none rides here).
            # ok, naming what was made: the call keeps its Undo.
            ended = job.done.is_set()
            J.reply_stopped(job, pressed=True)
            return ToolResult(ok=True, output=stopped_words(st, finished=ended),
                              data={"recording_id": st["id"]}, created_paths=made or None)
        if not finished:
            made = []
        return ToolResult(
            ok=True,
            output=words,
            data={"recording": J.card_of(st), "recording_id": st["id"]},
            created_paths=made or None,
        )

    async def revert(self, undo: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """Check the files written so far FIRST (one edited since refuses the
        whole Undo and leaves the job as it was: still working, its notes
        still to come), then stop the job if it still runs, check again, and
        remove the files it wrote, and drop the transcript's kept minutes."""
        from ..tools.undo import RevertConflict, read_envelope

        meta = read_envelope(undo)
        rid = str(meta.get("recording_id") or "")
        home = Path(ctx.config.home)
        if not R.valid_recording_id(rid):
            return ToolResult(ok=True, output="This call made no file, so there is nothing to take back.")
        ws = Path(ctx.workspace)

        async def _state_now() -> "dict[str, Any] | None":
            state = await asyncio.to_thread(J.load_state, home, rid)
            live = J.live(rid)
            return live.state if live is not None else state

        # BEFORE the stop: a refused Undo never stops the notes, and nothing
        # says the files were taken back while they are still there.
        await asyncio.to_thread(_check_unchanged, await _state_now(), ws)
        await J.stop(rid, "undo")
        state = await _state_now()

        def _undo() -> "tuple[int, int, list[str]]":
            # Again, now that nothing more is written: the notes may have been
            # written, or a file edited, while the job was being stopped.
            targets = _check_unchanged(state, ws)
            removed = gone = 0
            for target, _key in targets:
                if target.exists():
                    target.unlink()
                    removed += 1
                else:
                    gone += 1
            try:
                from .source_store import drop_kept_paths

                drop_kept_paths(ctx.engine, [str(state.get("transcript"))] if state else [])
            except Exception:  # noqa: BLE001
                pass
            if state is not None:
                state["status"] = J.STATUS_UNDONE
                state["transcript_written"] = False
                state["notes_written"] = False
                J.save_state(home, state)
            return removed, gone, [t.name for t, _ in targets]

        try:
            removed, gone, names = await asyncio.to_thread(_undo)
        except RevertConflict:
            # Edited in the moment the job was being stopped: it was stopped
            # and nothing was removed; its record says so (never "undone").
            await asyncio.to_thread(J.undo_refused, home, state)
            raise
        if not names and not removed:
            return ToolResult(ok=True, output="Stopped: no transcript or notes had been written yet.")
        words = f"Removed {removed} file(s) made from the recording ({', '.join(names)})"
        if gone:
            words += f"; {gone} already gone"
        return ToolResult(ok=True, output=words + ".")


def recording_tools(router_resolver: "Any | None" = None) -> "list[Tool]":
    return [TranscribeRecordingTool(router_resolver)]


__all__ = ["DEADLINE_MARGIN_S", "PHONE_WAIT_S", "TranscribeRecordingTool", "find_earlier", "recording_tools",
           "result_words"]
