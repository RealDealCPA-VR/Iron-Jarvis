"use client";

/**
 * "Rewind to here" on one of a Build session's messages (v1.370.0, IJ
 * harness plan Phase 7).
 *
 * TWO PRESSES. The first reads what the rewind would do (nothing is touched)
 * and shows it in words: which files go back, which are removed, the
 * commands that are NOT undone. The second rewinds. When a file cannot go
 * back (it changed after the session changed it, or its saved copy is gone)
 * the daemon refuses as a whole, and the refusal is listed here in its own
 * words — files and conversation stay exactly as they were.
 *
 * Never while a reply is running (the daemon refuses that too).
 */

import { useState } from "react";
import { History, Loader2 } from "lucide-react";
import {
  previewRewind,
  rewindFileWords,
  rewindProblemWords,
  rewindTo,
  type RewindExtras,
  type RewindPreview,
  type RewindProblem,
} from "@/lib/buildRewind";

type Phase =
  | { kind: "idle" }
  | { kind: "reading" }
  | { kind: "confirm"; preview: RewindPreview }
  | { kind: "working"; preview: RewindPreview }
  | { kind: "refused"; detail: string; problems: RewindProblem[]; restored: string[] };

/** The quiet words of the control (one place, pinned by the vocabulary scan). */
export const REWIND_LABEL = "Rewind to here";
export const REWIND_CONFIRM = "Rewind";
export const REWIND_BUSY_TITLE = "Stop the reply first, then rewind.";

export function RewindControl({
  threadId,
  turnId,
  disabled,
  onRewound,
  onTouched,
  shown = false,
}: {
  threadId: string;
  turnId: string;
  /** A reply is running: the control waits. */
  disabled?: boolean;
  /** v1.373.0: always visible (the "/rewind" list), not only on hover. */
  shown?: boolean;
  /** Rewound: the question of the message rewound to (it goes back in the
   *  box) — v1.377.0: with its pictures and files, and the committed-work
   *  sentence when the rewound work was committed. */
  onRewound: (question: string, extras: RewindExtras) => void;
  /** A rewind was refused AFTER it was sent (the disk moved under it): read
   *  the session again — the daemon's sentence says what already went back. */
  onTouched?: () => void;
}) {
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const [error, setError] = useState<string | null>(null);

  async function first() {
    setError(null);
    setPhase({ kind: "reading" });
    try {
      const preview = await previewRewind(threadId, turnId);
      if (!preview) {
        setPhase({ kind: "idle" });
        setError("This version of Iron Jarvis cannot rewind a session.");
        return;
      }
      if (!preview.canRewind) {
        setPhase({ kind: "refused", detail: preview.detail, problems: preview.problems, restored: [] });
        return;
      }
      setPhase({ kind: "confirm", preview });
    } catch (e) {
      setPhase({ kind: "idle" });
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function second(preview: RewindPreview) {
    setError(null);
    setPhase({ kind: "working", preview });
    try {
      const out = await rewindTo(threadId, turnId);
      if (out.ok) {
        setPhase({ kind: "idle" });
        onRewound(out.question || preview.question, {
          attachments: out.attachments.length ? out.attachments : preview.attachments,
          pickedFiles: out.pickedFiles.length ? out.pickedFiles : preview.pickedFiles,
          commitsNote: out.commitsNote || preview.commitsNote,
        });
        return;
      }
      setPhase({ kind: "refused", detail: out.detail, problems: out.problems, restored: out.restored });
      onTouched?.();
    } catch (e) {
      setPhase({ kind: "confirm", preview });
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  const open = phase.kind !== "idle";
  return (
    <div data-testid="rewind-control" className="flex flex-col items-end gap-1">
      {!open ? (
        <button
          type="button"
          data-testid="rewind-start"
          disabled={disabled}
          title={disabled ? REWIND_BUSY_TITLE : "Put the files back as they were before this message, and remove the conversation from here on"}
          onClick={() => void first()}
          className={
            shown
              ? "inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[12px] text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-zinc-200 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 disabled:cursor-not-allowed disabled:opacity-40"
              : "inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[12px] text-zinc-500 opacity-0 transition-opacity hover:bg-white/[0.06] hover:text-zinc-200 focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 disabled:cursor-not-allowed disabled:opacity-0 group-hover:opacity-100 [@media(hover:none)]:opacity-100"
          }
        >
          <History size={12} aria-hidden />
          {REWIND_LABEL}
        </button>
      ) : null}
      {phase.kind === "reading" ? (
        <p className="flex items-center gap-1.5 text-[12px] text-zinc-500">
          <Loader2 size={12} className="animate-spin" aria-hidden /> Checking what would go back…
        </p>
      ) : null}
      {phase.kind === "confirm" || phase.kind === "working" ? (
        <div
          data-testid="rewind-confirm"
          role="group"
          aria-label="Rewind to before this message"
          className="w-full max-w-[88%] space-y-2 rounded-2xl bg-white/[0.04] px-4 py-3 text-left"
        >
          <p className="text-[13px] font-medium text-zinc-100">Rewind to before this message?</p>
          {phase.preview.files.length ? (
            <ul data-testid="rewind-files" className="space-y-0.5 text-[12px] text-zinc-300">
              {phase.preview.files.map((f) => (
                <li key={f.path || f.rel}>{rewindFileWords(f)}</li>
              ))}
            </ul>
          ) : (
            <p className="text-[12px] text-zinc-400">No file changed from here on; only the conversation goes back.</p>
          )}
          {phase.preview.note ? (
            <p data-testid="rewind-commands" className="text-[12px] text-zinc-400">
              {phase.preview.note}
            </p>
          ) : null}
          {phase.preview.commitsNote ? (
            // v1.377.0: work from here on was committed on the session's
            // branch: the files go back, the commits stay.
            <p data-testid="rewind-commits" className="text-[12px] text-tone-warn">
              {phase.preview.commitsNote}
            </p>
          ) : null}
          <p className="text-[12px] text-zinc-400">
            The conversation from this message on is removed, and the message goes back in your box.
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              data-testid="rewind-confirm-button"
              disabled={phase.kind === "working"}
              onClick={() => void second(phase.preview)}
              className="btn-accent text-xs"
            >
              {phase.kind === "working" ? "Rewinding…" : REWIND_CONFIRM}
            </button>
            <button
              type="button"
              data-testid="rewind-cancel"
              disabled={phase.kind === "working"}
              onClick={() => setPhase({ kind: "idle" })}
              className="rounded-md px-2 py-1 text-[12px] text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-zinc-100 disabled:opacity-40"
            >
              Cancel
            </button>
          </div>
        </div>
      ) : null}
      {phase.kind === "refused" ? (
        <div
          data-testid="rewind-refused"
          role="alert"
          className="w-full max-w-[88%] space-y-1.5 rounded-2xl bg-tone-warn/[0.06] px-4 py-3 text-left shadow-[0_0_0_0.5px_rgb(var(--tone-warn)/0.45)]"
        >
          <p className="text-[13px] text-zinc-100">{phase.detail || "This rewind was refused."}</p>
          {phase.problems.length ? (
            <ul data-testid="rewind-problems" className="space-y-0.5 text-[12px] text-zinc-300">
              {phase.problems.map((p, k) => (
                <li key={k}>{rewindProblemWords(p)}</li>
              ))}
            </ul>
          ) : null}
          <button
            type="button"
            data-testid="rewind-close"
            onClick={() => setPhase({ kind: "idle" })}
            className="rounded-md px-2 py-1 text-[12px] text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-zinc-100"
          >
            Close
          </button>
        </div>
      ) : null}
      {error ? (
        <p role="alert" className="text-[12px] text-tone-danger">
          {error}
        </p>
      ) : null}
    </div>
  );
}
