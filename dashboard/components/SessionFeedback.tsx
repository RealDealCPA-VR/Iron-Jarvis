"use client";

import { useState } from "react";
import { ThumbsUp, ThumbsDown, Heart, MessageSquare } from "lucide-react";
import { post, ApiError } from "@/lib/api";
import type { FeedbackResult } from "@/lib/types";
import { Card, ErrorNote, SuccessNote, LoaderInline } from "@/components/ui";
import { VoiceInput, appendDictation } from "@/components/VoiceInput";

type Rating = "up" | "down";

/**
 * A warm, compact "How did I do?" card. The user gives a 👍/👎 plus an optional
 * note ("what should I do differently?") which the daemon turns into a lesson the
 * agent carries forward. Deliberately conversational rather than form-like.
 */
export function SessionFeedback({ sessionId }: { sessionId: string }) {
  const [rating, setRating] = useState<Rating | null>(null);
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<Rating | null>(null);

  async function send() {
    if (!rating || busy) return;
    setBusy(true);
    setError(null);
    try {
      await post<FeedbackResult>(`/sessions/${sessionId}/feedback`, {
        rating,
        comment: comment.trim(),
      });
      setDone(rating);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  if (done) {
    return (
      <Card title="How did I do?" icon={<Heart size={15} />}>
        <div className="flex flex-col items-center gap-2 py-4 text-center">
          <span className="grid h-11 w-11 place-items-center rounded-2xl border border-accent/30 bg-accent/10 text-accent-soft">
            {done === "up" ? <ThumbsUp size={20} /> : <ThumbsDown size={20} />}
          </span>
          <SuccessNote>
            Thanks — I&apos;ll remember that for next time.
          </SuccessNote>
          <button
            onClick={() => {
              setDone(null);
              setRating(null);
              setComment("");
            }}
            className="text-xs text-zinc-500 underline-offset-2 transition-colors hover:text-accent-soft hover:underline"
          >
            Leave more feedback
          </button>
        </div>
      </Card>
    );
  }

  return (
    <Card title="How did I do?" icon={<Heart size={15} />}>
      <p className="mb-3.5 text-sm text-zinc-400">
        Your call helps me get better — was this run helpful?
      </p>

      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={() => setRating("up")}
          aria-pressed={rating === "up"}
          className={`flex flex-1 items-center justify-center gap-2 rounded-xl border px-4 py-2.5 text-sm font-medium transition-all ${
            rating === "up"
              ? "border-emerald-500/50 bg-emerald-500/15 text-emerald-200 shadow-[0_0_18px_-6px_rgba(52,211,153,0.7)]"
              : "border-white/[0.08] bg-white/[0.02] text-zinc-400 hover:border-emerald-500/40 hover:text-emerald-200"
          }`}
        >
          <ThumbsUp size={16} /> Nailed it
        </button>
        <button
          type="button"
          onClick={() => setRating("down")}
          aria-pressed={rating === "down"}
          className={`flex flex-1 items-center justify-center gap-2 rounded-xl border px-4 py-2.5 text-sm font-medium transition-all ${
            rating === "down"
              ? "border-rose-500/50 bg-rose-500/15 text-rose-200 shadow-[0_0_18px_-6px_rgba(244,63,94,0.7)]"
              : "border-white/[0.08] bg-white/[0.02] text-zinc-400 hover:border-rose-500/40 hover:text-rose-200"
          }`}
        >
          <ThumbsDown size={16} /> Not quite
        </button>
      </div>

      <div className="mt-3.5">
        <div className="mb-1.5 flex items-center justify-between">
          <label className="flex items-center gap-1.5 text-[11px] uppercase tracking-[0.1em] text-zinc-400">
            <MessageSquare size={12} /> What should I do differently?
          </label>
          <VoiceInput
            size="sm"
            onTranscript={(chunk) =>
              setComment((p) => appendDictation(p, chunk))
            }
          />
        </div>
        <input
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && rating) send();
          }}
          placeholder="e.g. keep summaries to 3 bullets — optional, but I learn fast from it"
          className="field"
        />
      </div>

      <button
        type="button"
        onClick={send}
        disabled={!rating || busy}
        className="btn-accent mt-3.5 w-full"
      >
        {busy ? <LoaderInline label="Sending…" /> : "Send it my way"}
      </button>

      {error && (
        <div className="mt-3">
          <ErrorNote>{error}</ErrorNote>
        </div>
      )}
    </Card>
  );
}
