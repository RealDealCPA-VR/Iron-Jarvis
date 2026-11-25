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
written with (a file edited since refuses the whole Undo), and drops the
transcript's kept minutes.
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


def result_words(state: "dict[str, Any]", *, finished: bool, phone: bool) -> str:
    """What the model is told, from the job's own record."""
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
                f"{name} is still being turned into text {where}. It keeps going on this PC; the transcript and "
                f"notes will be saved in the chat's folder ({Path(str(state.get('workspace')))}) when they are "
                "done. Tell the user that. Do not write a transcript or notes yourself.")
        else:
            lines.append(
                f"Started turning {name} into text {where} ({tr.get('who')}). It keeps working after your reply: "
                "the card under your reply shows how far it is, and the transcript and notes come to this chat "
                "by themselves when they are done, as new files in the chat's folder "
                f"({state.get('transcript')} and {state.get('notes')}). Nothing is written yet. Tell the user "
                "that; do not write a transcript or notes yourself.")
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

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        desc = self._pending.pop(id(args), None)
        raw = str(args.get("path") or "").strip()
        if not raw:
            return ToolResult(ok=False, error="Say which recording to turn into text.")

        def _gate() -> "tuple[Path, tuple[bool, str], bool]":
            p = _resolve_read_path(raw, ctx)
            return p, fs_read_ok(p), p.is_file()

        path, (allowed, reason), exists = await asyncio.to_thread(_gate)
        if not allowed:
            return ToolResult(ok=False, error=reason)
        if not exists:
            return ToolResult(ok=False, error=f"There is no file at {path}.")
        if not R.is_recording(path):
            return ToolResult(ok=False, error=(
                f"{path.name} is not a recording. This turns audio (.m4a, .mp3, .wav, .ogg, ...) or the sound "
                "of a video (.mp4, .mov, ...) into text."))
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
                finished = await J.wait_inline(job, PHONE_WAIT_S if phone else J.INLINE_WAIT_S)
                earlier = job.state
            words = ("This recording was already handed to this chat's folder, so nothing new was made. "
                     + await asyncio.to_thread(lambda: result_words(earlier, finished=finished, phone=phone)))
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
        finished = await J.wait_inline(job, PHONE_WAIT_S if phone else J.INLINE_WAIT_S)
        st = job.state
        if finished and st.get("status") == J.STATUS_FAILED:
            return ToolResult(ok=False, error=str(st.get("error") or "The recording could not be turned into text."))
        made = [str(st[k]) for k, w in (("transcript", "transcript_written"), ("notes", "notes_written"))
                if finished and st.get(w)]
        # Off the loop: the words read the two files back.
        words = await asyncio.to_thread(lambda: result_words(st, finished=finished, phone=phone))
        return ToolResult(
            ok=True,
            output=words,
            data={"recording": J.card_of(st), "recording_id": st["id"]},
            created_paths=made or None,
        )

    async def revert(self, undo: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """Stop the job if it still runs, then remove the files it wrote, each
        only while it holds the bytes it was written with (one edited since
        refuses the whole Undo), and drop the transcript's kept minutes."""
        from ..tools.undo import RevertConflict, read_envelope

        meta = read_envelope(undo)
        rid = str(meta.get("recording_id") or "")
        home = Path(ctx.config.home)
        if not R.valid_recording_id(rid):
            return ToolResult(ok=True, output="This call made no file, so there is nothing to take back.")
        await J.stop(rid, "undo")
        state = await asyncio.to_thread(J.load_state, home, rid)
        live = J.live(rid)
        if live is not None:
            state = live.state

        def _undo() -> "tuple[int, int, list[str]]":
            ws = Path(ctx.workspace)
            hashes = (state or {}).get("hashes") if isinstance((state or {}).get("hashes"), dict) else {}
            written: list[Path] = []
            if state is not None:
                for key, flag in (("transcript", "transcript_written"), ("notes", "notes_written")):
                    if state.get(flag) and state.get(key):
                        written.append(Path(str(state[key])))
            targets: list[tuple[Path, str]] = []
            for p in written:
                try:
                    rel = p.resolve().relative_to(ws.resolve()).as_posix()
                    targets.append((safe_path(ws, rel), str(p)))
                except Exception:  # noqa: BLE001 — outside the folder: never touched
                    continue
            for target, key in targets:
                if target.exists() and J.file_sha256(target) != hashes.get(key):
                    raise RevertConflict(f"{target.name} was changed after it was written; nothing was removed")
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

        removed, gone, names = await asyncio.to_thread(_undo)
        if not names and not removed:
            return ToolResult(ok=True, output="Stopped: no transcript or notes had been written yet.")
        words = f"Removed {removed} file(s) made from the recording ({', '.join(names)})"
        if gone:
            words += f"; {gone} already gone"
        return ToolResult(ok=True, output=words + ".")


def recording_tools(router_resolver: "Any | None" = None) -> "list[Tool]":
    return [TranscribeRecordingTool(router_resolver)]


__all__ = ["PHONE_WAIT_S", "TranscribeRecordingTool", "find_earlier", "recording_tools", "result_words"]
