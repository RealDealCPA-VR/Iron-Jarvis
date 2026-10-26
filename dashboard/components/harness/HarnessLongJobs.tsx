"use client";

/**
 * NEVER STALLS SILENTLY (v1.392.0, the long-jobs plan's Phase 3) — the IJ
 * harness's pieces for a coding reply that runs a long time:
 *
 * - StallLine: on the live reply, the daemon's own sentence once it made no
 *   progress for ten minutes ("No progress for 10 minutes — waiting on …"),
 *   with Stop the step (only that tool call; the reply carries on) and Stop.
 * - SummarizedLine: "Summarized N earlier steps to fit the model" — live and
 *   on the saved reply.
 * - CapMetLine: the session's cap ended the reply (the step-limit way), with
 *   Keep going on the newest reply.
 * - CapForm: the ⋯ menu's "Limit each reply…" — minutes and/or dollars; both
 *   empty removes the limit (none is the default).
 *
 * The words live in lib/buildLongJobs.
 */

import { CircleAlert, Loader2, Timer } from "lucide-react";
import { useState } from "react";
import {
  capFromForm,
  capMetWords,
  sessionCapWords,
  summarizedWords,
  type CapMet,
  type SessionCap,
  type StallNotice,
  type Summarized,
} from "@/lib/buildLongJobs";

const PILL =
  "rounded-full bg-white/[0.06] px-3 py-1 text-[12px] text-zinc-200 transition-colors hover:bg-white/[0.1] hover:text-zinc-50 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 disabled:opacity-50";

export function StallLine({
  notice,
  stopping,
  note,
  onStopStep,
  onStop,
}: {
  notice: StallNotice;
  stopping: boolean;
  note: string | null;
  onStopStep: () => void;
  onStop: () => void;
}) {
  return (
    <div data-testid="harness-stall" role="status" className="flex flex-wrap items-center gap-2">
      <p className="flex min-w-0 items-start gap-1.5 text-[12px] text-tone-warn">
        <CircleAlert size={12} aria-hidden className="mt-0.5 shrink-0" />
        <span data-testid="harness-stall-words" className="min-w-0 break-words">
          {notice.words}
        </span>
      </p>
      {notice.step ? (
        <button
          type="button"
          data-testid="harness-stop-step"
          disabled={stopping}
          onClick={onStopStep}
          title="Stop only this step. The reply carries on without it, and the model is told you stopped it."
          className={PILL}
        >
          Stop the step
        </button>
      ) : null}
      <button
        type="button"
        data-testid="harness-stall-stop"
        disabled={stopping}
        onClick={onStop}
        title="Stop the whole reply"
        className={PILL}
      >
        Stop
      </button>
      {stopping ? <Loader2 size={12} className="animate-spin text-zinc-500" aria-hidden /> : null}
      {note ? (
        <p data-testid="harness-stall-note" className="basis-full text-[12px] text-zinc-400">
          {note}
        </p>
      ) : null}
    </div>
  );
}

export function SummarizedLine({ summarized }: { summarized: Summarized | null }) {
  if (!summarized) return null;
  return (
    <p data-testid="harness-summarized" className="mt-1 text-[12px] text-zinc-500">
      {summarizedWords(summarized)}
    </p>
  );
}

export function CapMetLine({
  cap,
  offer,
  keepGoingText,
  onKeepGoing,
}: {
  cap: CapMet | null;
  offer: boolean;
  keepGoingText: string;
  onKeepGoing: () => void;
}) {
  if (!cap) return null;
  return (
    <div data-testid="harness-cap-met" className="mt-2 flex flex-wrap items-center gap-2">
      <p className="flex items-center gap-1.5 text-[12px] text-zinc-400">
        <Timer size={12} aria-hidden className="shrink-0" />
        {capMetWords(cap)}
      </p>
      {offer ? (
        <button
          type="button"
          data-testid="harness-cap-keep-going"
          onClick={onKeepGoing}
          title={`Carry on from where it stopped (sends "${keepGoingText}"; the next reply has its own limit)`}
          className={PILL}
        >
          Keep going
        </button>
      ) : null}
    </div>
  );
}

export function CapForm({
  current,
  busy,
  onSave,
  onClose,
}: {
  current: SessionCap | null;
  busy: boolean;
  onSave: (cap: SessionCap | null) => Promise<string | null>;
  onClose: () => void;
}) {
  const [minutes, setMinutes] = useState(current?.minutes !== undefined ? String(current.minutes) : "");
  const [usd, setUsd] = useState(current?.usd !== undefined ? String(current.usd) : "");
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const got = capFromForm(minutes, usd);
    if ("error" in got) {
      setError(got.error);
      return;
    }
    const failed = await onSave(got.cap);
    if (failed) setError(failed);
    else onClose();
  }

  return (
    <form
      data-testid="harness-cap-form"
      onSubmit={(e) => void submit(e)}
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.stopPropagation();
          onClose();
        }
      }}
      className="space-y-2 rounded-xl border border-white/10 bg-zinc-900 p-3 text-[12px] text-zinc-300"
    >
      <p className="font-medium text-zinc-200">Limit each reply</p>
      <p className="text-zinc-500">{sessionCapWords(current)}</p>
      <p className="text-zinc-500">
        At the limit the reply stops and says what it did; Keep going carries on. Leave both empty for no limit.
      </p>
      <label className="flex items-center gap-2">
        <span className="w-28 shrink-0">Minutes</span>
        <input
          data-testid="harness-cap-minutes"
          inputMode="numeric"
          value={minutes}
          onChange={(e) => setMinutes(e.target.value)}
          placeholder="No limit"
          className="w-28 rounded-md border border-white/10 bg-zinc-950 px-2 py-1 text-zinc-100"
        />
      </label>
      <label className="flex items-center gap-2">
        <span className="w-28 shrink-0">Dollars (list price)</span>
        <input
          data-testid="harness-cap-usd"
          inputMode="decimal"
          value={usd}
          onChange={(e) => setUsd(e.target.value)}
          placeholder="No limit"
          className="w-28 rounded-md border border-white/10 bg-zinc-950 px-2 py-1 text-zinc-100"
        />
      </label>
      {error ? (
        <p data-testid="harness-cap-error" role="alert" className="text-tone-danger">
          {error}
        </p>
      ) : null}
      <div className="flex justify-end gap-2">
        <button type="button" onClick={onClose} className={PILL}>
          Cancel
        </button>
        <button type="submit" data-testid="harness-cap-save" disabled={busy} className={PILL}>
          Save
        </button>
      </div>
    </form>
  );
}
