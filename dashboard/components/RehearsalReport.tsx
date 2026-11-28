"use client";

/**
 * A rehearsal's report (leap 06-a): what a rule or a schedule WOULD have
 * written, moved, sent or run, with nothing changed. One component for the
 * Rules page, the Schedules page and the chat card ("rehearse it"); leap
 * 06-b adds the Workflows page (a saved workflow) and the Autonomy page (one
 * try of a goal), and two honest limits: a workflow that would ask you stops
 * there and names what was not rehearsed, and a step that read after a held
 * change says the real run may differ.
 *
 * Everything it says comes from `GET /rehearsals/{id}`, which the daemon
 * builds from the ledger's rehearsed rows every time it is read: the
 * sentence, the counts and each item are counted by code, never taken from
 * the model's closing words. While the rehearsal runs, the card asks again
 * every few seconds, only while it is on screen and the window is visible
 * (it stops by itself when the rehearsal ends). Keep it is the one main
 * button; Stop, Close and the "show all" fold are quiet text buttons.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { get, post, ApiError } from "@/lib/api";
import { Button, ErrorNote, LoaderInline } from "@/components/ui";

export interface RehearsalItem {
  kind: string;
  words: string;
  target?: string;
  would: "run" | "ask" | "refuse" | string;
  why?: string;
}

export interface RehearsalView {
  id: string;
  subject: { kind: string; id: string; name: string };
  status: "running" | "ready" | "failed" | "stopped" | string;
  sentence: string;
  counts?: Record<string, number>;
  would: RehearsalItem[];
  would_total?: number;
  refused?: RehearsalItem[];
  reads?: number;
  guessed_after?: boolean;
  /** Leap 06-b: the line that says a later read was worked out without a
   *  held change ("" when none). */
  guessed_words?: string;
  /** Leap 06-b: a workflow's rehearsal that stopped at its question: what
   *  was not rehearsed, in one line ("" when it did not stop there). */
  stopped_words?: string;
  /** Leap 06-b review: one more line from the daemon (a paused goal). */
  note?: string;
  error?: string;
  /** Why no run started (a schedule with nothing to do), in the row's words. */
  detail?: string;
  sample?: string;
  kept_at?: string | null;
}

/** How often a running rehearsal is asked again, while on screen. */
export const REHEARSAL_POLL_MS = 3000;
/** How many items show before "Show all". */
export const REHEARSAL_SHOWN = 8;

const SUBJECT_WORD: Record<string, string> = {
  rule: "rule",
  schedule: "schedule",
  workflow: "workflow",
  goal: "goal",
};

/** Decode a report from the wire (anything malformed becomes nothing). */
export function decodeRehearsal(raw: unknown): RehearsalView | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  if (typeof r.id !== "string" || !r.id) return null;
  const s = (r.subject && typeof r.subject === "object" ? r.subject : {}) as Record<string, unknown>;
  const items = (v: unknown): RehearsalItem[] =>
    Array.isArray(v)
      ? v
          .filter((x): x is Record<string, unknown> => !!x && typeof x === "object")
          .map((x) => ({
            kind: String(x.kind ?? ""),
            words: String(x.words ?? ""),
            target: typeof x.target === "string" ? x.target : "",
            would: String(x.would ?? "run"),
            why: typeof x.why === "string" ? x.why : "",
          }))
          .filter((x) => x.words)
      : [];
  return {
    id: r.id,
    subject: { kind: String(s.kind ?? ""), id: String(s.id ?? ""), name: String(s.name ?? "") },
    status: String(r.status ?? ""),
    sentence: String(r.sentence ?? ""),
    counts: (r.counts && typeof r.counts === "object" ? r.counts : {}) as Record<string, number>,
    would: items(r.would),
    would_total: typeof r.would_total === "number" ? r.would_total : undefined,
    refused: items(r.refused),
    reads: typeof r.reads === "number" ? r.reads : 0,
    guessed_after: r.guessed_after === true,
    guessed_words: typeof r.guessed_words === "string" ? r.guessed_words : "",
    stopped_words: typeof r.stopped_words === "string" ? r.stopped_words : "",
    note: typeof r.note === "string" ? r.note : "",
    error: typeof r.error === "string" ? r.error : "",
    detail: typeof r.detail === "string" ? r.detail : "",
    sample: typeof r.sample === "string" ? r.sample : "",
    kept_at: typeof r.kept_at === "string" ? r.kept_at : null,
  };
}

function shortDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

export function RehearsalReport({
  id,
  initial,
  onClose,
  onKept,
  onChange,
  keepable = true,
}: {
  id: string;
  /** Next leaps 19-b: false for a schedule that is only offered (its
   *  Accept, not Keep it, makes it). */
  keepable?: boolean;
  initial?: RehearsalView | null;
  onClose?: () => void;
  /** Every new reading of the report (the page keeps its own copy). */
  onChange?: (view: RehearsalView) => void;
  /** Called after Keep it, so the page can show the row's new date. */
  onKept?: (view: RehearsalView) => void;
}) {
  const [view, setView] = useState<RehearsalView | null>(initial ?? null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"" | "keep" | "stop">("");
  const [showAll, setShowAll] = useState(false);
  const alive = useRef(true);
  // The page's callback is read through a ref, so a new one each render
  // never restarts the reading below.
  const changed = useRef(onChange);
  changed.current = onChange;
  // Read once on mount when the page had no report in hand.
  const hadInitial = useRef(!!initial);

  const load = useCallback(async () => {
    try {
      const next = decodeRehearsal(await get<unknown>(`/rehearsals/${encodeURIComponent(id)}`));
      if (alive.current && next) {
        setView(next);
        changed.current?.(next);
      }
    } catch (err) {
      if (alive.current) setError(err instanceof ApiError ? err.message : String(err));
    }
  }, [id]);

  useEffect(() => {
    alive.current = true;
    if (!hadInitial.current) void load();
    return () => {
      alive.current = false;
    };
  }, [load]);

  // Asked again only while running, on screen, and the window is visible.
  const running = view?.status === "running";
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => {
      if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
      void load();
    }, REHEARSAL_POLL_MS);
    return () => clearInterval(timer);
  }, [running, load]);

  async function keep() {
    setBusy("keep");
    setError(null);
    try {
      const next = decodeRehearsal(await post<unknown>(`/rehearsals/${encodeURIComponent(id)}/keep`));
      if (next) {
        setView(next);
        changed.current?.(next);
        onKept?.(next);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy("");
    }
  }

  async function stop() {
    setBusy("stop");
    setError(null);
    try {
      const next = decodeRehearsal(await post<unknown>(`/rehearsals/${encodeURIComponent(id)}/stop`));
      if (next) {
        setView(next);
        changed.current?.(next);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy("");
    }
  }

  const what = SUBJECT_WORD[view?.subject.kind ?? ""] ?? "";
  const name = view?.subject.name ?? "";
  const items = view?.would ?? [];
  const shown = showAll ? items : items.slice(0, REHEARSAL_SHOWN);
  const refused = view?.refused ?? [];

  return (
    <section
      data-testid={`rehearsal-report-${id}`}
      data-status={view?.status ?? "loading"}
      aria-label={name ? `Rehearsal of ${name}` : "Rehearsal"}
      className="min-w-0 space-y-2 rounded-xl border border-white/[0.08] bg-white/[0.02] p-3 text-sm"
    >
      <div className="text-[11px] uppercase tracking-[0.12em] text-zinc-500">
        Rehearsal{what && name ? ` of the ${what} “${name}”` : ""}
      </div>

      {!view && !error && <LoaderInline label="Loading the rehearsal…" />}

      {view?.status === "running" && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-zinc-300">
          <LoaderInline label="Rehearsing. Nothing is being changed." />
          <button
            type="button"
            onClick={stop}
            disabled={busy !== ""}
            data-testid={`rehearsal-stop-${id}`}
            className="text-xs text-zinc-400 underline-offset-2 hover:text-accent-soft hover:underline disabled:opacity-40"
          >
            Stop
          </button>
        </div>
      )}

      {view && view.status !== "running" && view.status !== "failed" && (
        <p data-testid={`rehearsal-sentence-${id}`} className="break-words text-zinc-100">
          {view.sentence}
        </p>
      )}
      {view?.status === "ready" && view.detail ? (
        <p data-testid={`rehearsal-detail-${id}`} className="break-words text-[13px] text-zinc-400">
          {view.detail}
        </p>
      ) : null}

      {view?.status === "failed" && (
        <ErrorNote>
          <span data-testid={`rehearsal-error-${id}`}>
            {view.error || "The rehearsal could not finish."} Nothing was changed.
          </span>
        </ErrorNote>
      )}

      {view && view.status !== "running" && items.length > 0 && (
        <ul data-testid={`rehearsal-items-${id}`} className="min-w-0 space-y-1 text-[13px] text-zinc-300">
          {shown.map((it, i) => (
            <li key={`${i}-${it.words}`} className="flex min-w-0 flex-wrap items-baseline gap-x-2">
              <span className="min-w-0 break-words">Would {it.words}</span>
              {it.would === "ask" && <span className="text-[11px] text-tone-warn">asks you first</span>}
            </li>
          ))}
        </ul>
      )}
      {view && items.length > REHEARSAL_SHOWN && (
        <button
          type="button"
          onClick={() => setShowAll((v) => !v)}
          className="text-xs text-zinc-400 underline-offset-2 hover:text-accent-soft hover:underline"
        >
          {showAll ? "Show fewer" : `Show all ${items.length}`}
        </button>
      )}

      {view && view.status !== "running" && refused.length > 0 && (
        <div data-testid={`rehearsal-refused-${id}`} className="space-y-1 text-[13px] text-zinc-400">
          <div>Would be refused:</div>
          <ul className="space-y-1">
            {refused.slice(0, REHEARSAL_SHOWN).map((it, i) => (
              <li key={`${i}-${it.words}`} className="break-words">
                {it.words}
                {it.why ? <span className="text-zinc-500"> ({it.why})</span> : null}
              </li>
            ))}
          </ul>
        </div>
      )}

      {view && view.status !== "running" && view.stopped_words ? (
        <p data-testid={`rehearsal-stopped-${id}`} className="break-words text-[13px] text-zinc-400">
          {view.stopped_words}
        </p>
      ) : null}
      {view && view.status !== "running" && view.note ? (
        <p data-testid={`rehearsal-note-${id}`} className="break-words text-[13px] text-zinc-400">
          {view.note}
        </p>
      ) : null}
      {view && view.status !== "running" && view.guessed_words ? (
        <p data-testid={`rehearsal-guessed-${id}`} className="break-words text-[13px] text-tone-warn">
          {view.guessed_words}
        </p>
      ) : null}

      {view && view.status === "ready" && (
        <p className="text-[12px] text-zinc-500">
          {view.reads ? `It read ${view.reads} thing${view.reads === 1 ? "" : "s"} to get there. ` : ""}
          {view.sample ? `It ran on ${view.sample}.` : ""}
        </p>
      )}

      {error && <ErrorNote>{error}</ErrorNote>}

      {view && view.status !== "running" && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 pt-1">
          {keepable && view.status === "ready" && !view.kept_at && (
            <Button
              variant="primary"
              size="sm"
              onClick={keep}
              disabled={busy !== ""}
              data-testid={`rehearsal-keep-${id}`}
            >
              Keep it
            </Button>
          )}
          {view.kept_at && (
            <span data-testid={`rehearsal-kept-${id}`} className="text-xs text-zinc-400">
              Kept{shortDate(view.kept_at) ? ` on ${shortDate(view.kept_at)}` : ""}.
            </span>
          )}
          {onClose && (
            <button
              type="button"
              onClick={onClose}
              className="text-xs text-zinc-400 underline-offset-2 hover:text-accent-soft hover:underline"
            >
              Close
            </button>
          )}
        </div>
      )}
    </section>
  );
}
