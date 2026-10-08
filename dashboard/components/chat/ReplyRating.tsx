"use client";

/**
 * 👍 / 👎 under a chat reply (v1.320.0).
 *
 * The user's report: the setup checklist said "rate a finished session", the
 * rating lived only on an agent run's own page, and Chat — where the
 * checklist sent them — had nothing to press. Now every settled reply can be
 * rated where it was read:
 *
 *   - 👍 is recorded at once ("Thanks — noted").
 *   - 👎 asks one question, "What should be different next time?". The answer
 *     becomes a lesson every later prompt carries; Skip records the rating
 *     without one (a bare thumbs-down does not say what to change).
 *
 * The rating is stored by the daemon (POST /chat/feedback — it counts as
 * "Teach it your style") and on the message, so a reopened chat shows what
 * was said instead of asking again. The newest reply shows the buttons; an
 * older one shows them on hover, like its other actions.
 */

import { useState, type FormEvent } from "react";
import { ThumbsDown, ThumbsUp } from "lucide-react";
import { post } from "@/lib/api";

export interface ReplyRatingValue {
  value: "up" | "down";
  /** What the user said should be different (👎 only). */
  note?: string;
}

const NOTE_MAX = 500;

export function ReplyRating({
  rating,
  threadId,
  prominent,
  disabled,
  onRated,
}: {
  rating?: ReplyRatingValue;
  threadId: string | null;
  /** The newest reply: always visible. Older replies: on hover/focus. */
  prominent: boolean;
  /** A turn is running — answer when it is done. */
  disabled: boolean;
  onRated: (r: ReplyRatingValue) => void;
}) {
  const [asking, setAsking] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function send(r: ReplyRatingValue) {
    setBusy(true);
    setError("");
    try {
      await post("/chat/feedback", {
        rating: r.value,
        comment: r.note ?? "",
        thread_id: threadId ?? "",
      });
      setAsking(false);
      onRated(r);
    } catch {
      setError("Couldn't save that — try again.");
    } finally {
      setBusy(false);
    }
  }

  if (rating) {
    return (
      <p data-testid="reply-rated" className="ml-11 mt-1 text-[12px] text-zinc-500">
        {rating.value === "up" ? (
          <>
            <ThumbsUp size={11} className="mr-1 inline align-[-1px]" aria-hidden />
            Thanks — noted.
          </>
        ) : rating.note ? (
          <>
            <ThumbsDown size={11} className="mr-1 inline align-[-1px]" aria-hidden />
            Noted — Jarvis will remember: “{rating.note}”
          </>
        ) : (
          <>
            <ThumbsDown size={11} className="mr-1 inline align-[-1px]" aria-hidden />
            Noted.
          </>
        )}
      </p>
    );
  }

  if (asking) {
    const submit = (e: FormEvent) => {
      e.preventDefault();
      void send({ value: "down", note: note.trim() || undefined });
    };
    return (
      <form data-testid="reply-rating-ask" onSubmit={submit} className="ml-11 mt-1.5 max-w-xl space-y-1.5">
        <label htmlFor="reply-rating-note" className="block text-[12px] text-zinc-300">
          What should be different next time?
        </label>
        <div className="flex flex-wrap items-center gap-1.5">
          <input
            id="reply-rating-note"
            autoFocus
            value={note}
            maxLength={NOTE_MAX}
            onChange={(e) => setNote(e.target.value)}
            onKeyDown={(e) => {
              // Never bubbles to the composer (Enter submits THIS form).
              e.stopPropagation();
              if (e.key === "Escape") setAsking(false);
            }}
            placeholder="e.g. shorter, with bullet points"
            className="field min-w-0 flex-1 text-[13px]"
          />
          <button type="submit" disabled={busy} className="btn-soft px-2.5 py-1 text-[12px]">
            Save
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => void send({ value: "down" })}
            className="rounded-lg px-2 py-1 text-[12px] text-zinc-500 hover:text-zinc-300"
          >
            Skip
          </button>
        </div>
        <p className="text-[11px] text-zinc-500">Your answer is remembered and used in later replies.</p>
        {error && (
          <p role="alert" className="text-[12px] text-tone-danger">
            {error}
          </p>
        )}
      </form>
    );
  }

  return (
    <div
      data-testid="reply-rating"
      className={`ml-11 mt-1 flex items-center gap-1 text-[12px] text-zinc-500 ${
        prominent
          ? ""
          : "opacity-0 transition-opacity focus-within:opacity-100 group-hover/msg:opacity-100"
      }`}
    >
      {prominent && <span className="mr-0.5">Was this helpful?</span>}
      <button
        type="button"
        disabled={disabled || busy}
        onClick={() => void send({ value: "up" })}
        aria-label="Good reply"
        title="Good reply"
        className="grid h-6 w-6 place-items-center rounded-md transition-colors hover:bg-white/[0.06] hover:text-tone-success disabled:opacity-40"
      >
        <ThumbsUp size={13} />
      </button>
      <button
        type="button"
        disabled={disabled || busy}
        onClick={() => setAsking(true)}
        aria-label="Not quite right"
        title="Not quite right — say what to change"
        className="grid h-6 w-6 place-items-center rounded-md transition-colors hover:bg-white/[0.06] hover:text-tone-danger disabled:opacity-40"
      >
        <ThumbsDown size={13} />
      </button>
      {error && (
        <span role="alert" className="ml-1 text-tone-danger">
          {error}
        </span>
      )}
    </div>
  );
}
