"use client";

// Memory housekeeping (v1.143.0) — the steward's review queue.
//
// The steward ADDS notes on its own: an append is additive and undoable, so a
// wrong note costs one click. Everything that would CHANGE a note you already
// have — a duplicate, a stale fact, a contradiction, three notes that want to
// be one — stops here and waits for you. That promise is the copy, not just
// the code: the card says "Nothing is changed until you approve" out loud, and
// each row is honest about whether that memory base can even be edited from
// here (a markdown base yes, Notion/plug-in bases no) and whether the change
// would be undoable.
//
// Mirrors SuggestedSkills.tsx: one overview fetch, per-row busy locks, honest
// errors surfaced verbatim, and a whole-section 404 -> the card is absent
// (an older daemon simply doesn't have these routes yet).

import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  Check,
  CalendarPlus,
  RotateCcw,
  Sparkles,
  Undo2,
  X,
} from "lucide-react";
import { ApiError, get, post } from "@/lib/api";
import { Card, ErrorNote, LoaderInline, SuccessNote } from "@/components/ui";

type ProposalKind = "duplicate" | "stale" | "contradiction" | "merge";

interface MemoryProposal {
  id: string;
  kind: ProposalKind;
  /** The memory base the affected notes live in. */
  base: string;
  refs: string[];
  rationale: string;
  suggested_action: string;
  status: "pending" | "approved" | "dismissed";
  /** Can this base be rewritten from here at all (markdown yes, cloud no)? */
  can_apply: boolean;
  /** Would approving be undoable (Time travel)? */
  undoable: boolean;
  /** The honest one-liner explaining can_apply/undoable. */
  base_note: string;
  /** Does approving replace a note's whole body? */
  rewrites: boolean;
  /** The note that survives and gets rewritten (when `rewrites`). */
  survivor_ref?: string;
  /** The notes approving would DELETE — the concrete effect, from the payload
   *  rather than from the model's prose. */
  remove_refs?: string[];
  removes: number;
  /** An earlier approve got part-way and then failed: some notes ALREADY
   *  changed while this is still pending. Saying nothing would read as
   *  "nothing has happened yet", which is the one thing it must not read as. */
  partial?: boolean;
  applied?: { changed?: string[]; undoable?: boolean; partial?: boolean };
}

interface ScheduleTemplate {
  name: string;
  label: string;
  cron: string;
  kind: string;
  task: string;
  description: string;
  installed: boolean;
}

interface ReviewOverview {
  proposals: MemoryProposal[];
  pending: number;
  stats: { pending: number; approved: number; dismissed: number };
  steward: { available: boolean; stats: Record<string, unknown>; runs: unknown[] };
  template: ScheduleTemplate;
}

// Plain-language badges — the wire's kind, said the way a person would.
const KIND_META: Record<ProposalKind, { label: string; cls: string }> = {
  duplicate: {
    label: "Duplicate",
    cls: "border-accent/30 bg-accent/10 text-accent-soft",
  },
  stale: {
    label: "Out of date",
    cls: "border-amber-500/30 bg-amber-500/10 text-amber-300",
  },
  contradiction: {
    label: "Contradiction",
    cls: "border-rose-500/30 bg-rose-500/10 text-rose-300",
  },
  merge: {
    label: "Merge",
    cls: "border-violet-500/30 bg-violet-500/10 text-violet-300",
  },
};

/** First present value among several possible keys — Pair M1 owns the steward's
 *  stat shape, so read it loosely rather than pinning one spelling. */
function pick(source: Record<string, unknown> | undefined, ...keys: string[]): string {
  for (const key of keys) {
    const value = source?.[key];
    if (value === null || value === undefined || value === "") continue;
    if (typeof value === "number") return String(value);
    if (typeof value === "string") return value;
  }
  return "";
}

/** An ISO stamp as a short local date/time; anything else passes through. */
function when(value: string): string {
  if (!value) return "";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

/** A note ref (a full path on disk) shown as just its name. */
function noteLabel(ref: string): string {
  const tail = ref.split(/[\\/]/).pop() || ref;
  return tail.replace(/\.md$/i, "");
}

/** What approving would ACTUALLY do, read off the payload.
 *
 *  `suggested_action` is the model's own sentence about its suggestion; this is
 *  the effect the daemon will carry out. They are usually the same and must be
 *  shown separately anyway — "keep alpha, remove the copy" next to a payload
 *  that removes both is exactly the approval nobody should give blind. */
function effectOf(p: MemoryProposal): string {
  const removed = (p.remove_refs ?? []).map(noteLabel);
  const bits: string[] = [];
  if (p.rewrites) {
    bits.push(
      p.survivor_ref
        ? `replaces everything in “${noteLabel(p.survivor_ref)}”`
        : "replaces a note's contents",
    );
  }
  if (removed.length > 0) {
    bits.push(
      `deletes ${removed.length} note${removed.length === 1 ? "" : "s"}: ` +
        removed.map((n) => `“${n}”`).join(", "),
    );
  } else if (p.removes > 0) {
    bits.push(`deletes ${p.removes} note${p.removes === 1 ? "" : "s"}`);
  }
  if (bits.length === 0) return "";
  return `Approving ${bits.join(", and ")}.`;
}

export function MemoryReview() {
  const [overview, setOverview] = useState<ReviewOverview | null>(null);
  // null = still deciding; true = this daemon has no review routes -> render
  // nothing at all rather than an empty card that promises a missing feature.
  const [absent, setAbsent] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [scheduling, setScheduling] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [busy, setBusy] = useState<{ id: string; action: "approve" | "dismiss" } | null>(
    null,
  );
  const [rowErrors, setRowErrors] = useState<Record<string, string>>({});

  const refresh = useCallback(async () => {
    try {
      const body = await get<ReviewOverview>("/memory/review");
      setOverview(body);
      setAbsent(false);
    } catch (err) {
      if (err instanceof ApiError && (err.status === 404 || err.status === 405)) {
        setAbsent(true);
        return;
      }
      // A live daemon that errors is worth saying out loud; a dead one isn't
      // (status 0 = the app is simply not running, every page shows that once).
      if (err instanceof ApiError && err.status !== 0) {
        setError(err.message);
      }
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  /** POST /memory/review/run — a 400 (no model connected) shows its detail. */
  async function reviewNow() {
    if (running) return;
    setRunning(true);
    setError(null);
    setNote(null);
    try {
      // `started: false` is an honest answer, not a failure: there is nothing
      // new to review, and firing a session over an empty window is how memory
      // fills with invented facts.
      const res = await post<{ started?: boolean; note?: string }>("/memory/review/run");
      setNote(
        res?.started === false
          ? res.note || "Nothing new to review right now."
          : "Review started — it will save what's worth remembering and add any " +
              "cleanup suggestions here for you to approve.",
      );
      void refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setRunning(false);
    }
  }

  /** Install the weekly schedule — an ordinary POST /schedules, on a click. */
  async function scheduleWeekly(template: ScheduleTemplate) {
    if (scheduling) return;
    setScheduling(true);
    setError(null);
    setNote(null);
    try {
      await post("/schedules", {
        name: template.name,
        cron: template.cron,
        kind: template.kind,
        payload: { task: template.task },
      });
      setNote(`“${template.label}” added — you can change or remove it on Schedules.`);
      void refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setScheduling(false);
    }
  }

  /** Re-read history from the beginning on the next review — the escape hatch
   *  the review-point note itself tells the user about. */
  async function resetReviewPoint() {
    if (resetting) return;
    setResetting(true);
    setError(null);
    setNote(null);
    try {
      const res = await post<{ note?: string }>("/memory/review/reset");
      setNote(res?.note || "The next review starts from the beginning.");
      void refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setResetting(false);
    }
  }

  async function decide(p: MemoryProposal, action: "approve" | "dismiss") {
    if (busy) return;
    setBusy({ id: p.id, action });
    setNote(null);
    setRowErrors((e) => {
      const next = { ...e };
      delete next[p.id];
      return next;
    });
    try {
      const res = await post<{ applied?: { undoable?: boolean } }>(
        `/memory/review/${encodeURIComponent(p.id)}/${action}`,
      );
      setNote(
        action === "approve"
          ? // What the server ACTUALLY journalled, not what the base promised
            // before the click — the two can differ (a ledger write can fail
            // while the change stands), and a fake undo offer is worse than none.
            res?.applied?.undoable
            ? "Memory updated. You can undo it from Time travel."
            : "Memory updated."
          : "Suggestion dismissed — it won't be suggested again.",
      );
      void refresh();
    } catch (err) {
      setRowErrors((e) => ({
        ...e,
        [p.id]: err instanceof ApiError ? err.message : String(err),
      }));
      // A failed approve can still have changed part of what it named, and the
      // row carries that afterwards. Re-read so the card stops saying nothing
      // has happened yet.
      void refresh();
    } finally {
      setBusy(null);
    }
  }

  if (absent || !overview) return null;

  const pending = (overview.proposals ?? []).filter((p) => p.status === "pending");
  const stewardStats = overview.steward?.stats ?? {};
  const lastRun = when(pick(stewardStats, "last_run_at", "last_run", "ran_at", "at"));
  const notesAdded = pick(stewardStats, "notes_added", "added", "notes");
  const template = overview.template;
  // Non-empty exactly when a review point exists — i.e. exactly when the
  // limitation it describes can bite. Shown WITH its escape hatch, because a
  // caveat with no button is just an apology.
  const reviewPointNote = pick(stewardStats, "cursor_note");

  const statusBits: string[] = [];
  statusBits.push(lastRun ? `Last review ${lastRun}` : "No review has run yet");
  if (notesAdded) statusBits.push(`${notesAdded} notes added`);
  statusBits.push(
    pending.length === 1 ? "1 suggestion waiting" : `${pending.length} suggestions waiting`,
  );

  return (
    <Card
      title={
        pending.length > 0
          ? `Memory housekeeping · ${pending.length}`
          : "Memory housekeeping"
      }
      icon={<Sparkles size={15} />}
      right={
        <div className="flex flex-wrap items-center justify-end gap-2">
          {template && !template.installed && (
            <button
              type="button"
              onClick={() => void scheduleWeekly(template)}
              disabled={scheduling}
              title={template.description}
              className="btn-ghost py-1 text-[11px] disabled:opacity-50"
            >
              {scheduling ? (
                <LoaderInline label="Adding…" />
              ) : (
                <>
                  <CalendarPlus size={12} /> Review weekly
                </>
              )}
            </button>
          )}
          <button
            type="button"
            onClick={() => void reviewNow()}
            disabled={running}
            title="Read recent conversations now: save what's worth remembering, and suggest cleanups here"
            className="btn-ghost py-1 text-[11px] disabled:opacity-50"
          >
            {running ? (
              <LoaderInline label="Starting…" />
            ) : (
              <>
                <Sparkles size={12} /> Review now
              </>
            )}
          </button>
        </div>
      }
    >
      <p className="mb-3 text-[13px] leading-relaxed text-zinc-500">
        Iron Jarvis adds notes to your memory bases on its own — that&apos;s always
        undoable. Anything that would change or remove a note you already have waits
        here. <span className="text-zinc-300">Nothing is changed until you approve.</span>
      </p>

      <p className="mb-3 text-[11.5px] text-zinc-600">
        {statusBits.join(" · ")}
        {template?.installed ? " · Reviewing weekly" : ""}
      </p>

      {reviewPointNote && (
        <p className="mb-3 text-[11.5px] leading-relaxed text-zinc-600">
          {reviewPointNote}{" "}
          <button
            type="button"
            onClick={() => void resetReviewPoint()}
            disabled={resetting}
            title="The next review reads your whole history again. Nothing is deleted."
            className="inline-flex items-center gap-1 text-zinc-400 underline decoration-dotted underline-offset-2 hover:text-zinc-200 disabled:opacity-50"
          >
            <RotateCcw size={10} />
            {resetting ? "Resetting…" : "Reset the review point"}
          </button>
        </p>
      )}

      {note && (
        <div className="mb-3">
          <SuccessNote>{note}</SuccessNote>
        </div>
      )}
      {error && (
        <div className="mb-3">
          <ErrorNote>{error}</ErrorNote>
        </div>
      )}

      {pending.length === 0 ? (
        <p className="text-[13px] leading-relaxed text-zinc-600">
          Nothing to review. As Iron Jarvis reads your conversations it saves what
          matters and flags duplicates, out-of-date notes, and contradictions here.
        </p>
      ) : (
        <ul className="space-y-3">
          {pending.map((p) => {
            const meta = KIND_META[p.kind] ?? KIND_META.duplicate;
            const effect = effectOf(p);
            const rowBusy = busy?.id === p.id;
            const approving = rowBusy && busy?.action === "approve";
            const dismissing = rowBusy && busy?.action === "dismiss";
            return (
              <li
                key={p.id}
                className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-3.5"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span
                    className={`inline-flex shrink-0 items-center rounded-full border px-1.5 py-0.5 text-[10px] font-medium ${meta.cls}`}
                  >
                    {meta.label}
                  </span>
                  <span className="text-[11px] text-zinc-500">
                    memory base <span className="text-zinc-300">{p.base || "—"}</span>
                  </span>
                  {p.undoable && (
                    <span
                      className="inline-flex items-center gap-1 text-[11px] text-zinc-600"
                      title="Approving this lands on the Time travel list — one click puts the notes back."
                    >
                      <Undo2 size={11} /> undoable
                    </span>
                  )}
                </div>

                {p.suggested_action && (
                  <p className="mt-1.5 text-[13px] leading-relaxed text-zinc-200">
                    {p.suggested_action}
                  </p>
                )}
                {p.rationale && (
                  <p className="mt-1 text-xs leading-relaxed text-zinc-500">
                    {p.rationale}
                  </p>
                )}

                {p.refs?.length > 0 && (
                  <div className="mt-2 flex flex-wrap items-center gap-1.5">
                    <span className="text-[10px] uppercase tracking-[0.12em] text-zinc-600">
                      Notes
                    </span>
                    {p.refs.map((ref) => (
                      <span
                        key={ref}
                        title={ref}
                        className="rounded-full border border-white/[0.08] bg-white/[0.03] px-2 py-0.5 font-mono text-[11px] text-zinc-400"
                      >
                        {noteLabel(ref)}
                      </span>
                    ))}
                  </div>
                )}

                {effect && (
                  <p className="mt-2 text-[11.5px] leading-relaxed text-zinc-400">
                    {effect}
                  </p>
                )}

                {p.partial && (
                  <p className="mt-2 flex items-start gap-1.5 text-[11.5px] leading-relaxed text-amber-300/90">
                    <AlertTriangle size={12} className="mt-0.5 shrink-0" />
                    <span>
                      An earlier attempt got part-way before it failed
                      {p.applied?.changed?.length
                        ? `: ${p.applied.changed.join("; ")}`
                        : ""}
                      . Those changes are on the Time travel list if you want them back.
                    </span>
                  </p>
                )}

                {!p.can_apply && p.base_note && (
                  <p className="mt-2 text-[11.5px] leading-relaxed text-amber-300/80">
                    {p.base_note}
                  </p>
                )}

                <div className="mt-2.5 flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    onClick={() => void decide(p, "approve")}
                    disabled={rowBusy || !p.can_apply}
                    title={
                      p.can_apply
                        ? "Make this change now"
                        : "This memory base can't be changed from here"
                    }
                    className="btn-accent py-1.5 text-xs disabled:opacity-50"
                  >
                    {approving ? (
                      <LoaderInline label="Applying…" />
                    ) : (
                      <>
                        <Check size={13} /> Approve
                      </>
                    )}
                  </button>
                  <button
                    type="button"
                    onClick={() => void decide(p, "dismiss")}
                    disabled={rowBusy}
                    title="Never suggest this again"
                    className="btn-ghost py-1.5 text-xs disabled:opacity-50"
                  >
                    {dismissing ? (
                      <LoaderInline label="Dismissing…" />
                    ) : (
                      <>
                        <X size={13} /> Dismiss
                      </>
                    )}
                  </button>
                </div>

                {rowErrors[p.id] && (
                  <div className="mt-2">
                    <ErrorNote>{rowErrors[p.id]}</ErrorNote>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}
