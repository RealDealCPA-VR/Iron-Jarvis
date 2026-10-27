"use client";

/**
 * Coding sessions › LAST NIGHT (v1.399.0, night shift N2 — docs/NIGHT-SHIFT-PLAN.md,
 * the daemon's harness/night_morning.py and GET /build/night/summaries).
 *
 * ONE summary per night, built from the records — never a model's words.
 * Each task's row: its branch, how it ended, its tests, the files it changed,
 * its time, its usage per account and what needs the user; failed and
 * needs-you first (the daemon sorts them). A row whose main folder changed
 * says so un-folded. Iron Jarvis committed each task's work to its own branch
 * when it ended (never pushed); the buttons are the session's own git
 * machinery: Open, Changes (the Branch view), Push, Open a pull request, and
 * Throw away (two presses: the branch's folder goes to the trash, the branch
 * is KEPT). Older nights are listed below, newest first (the last 14).
 *
 * No new card styles: Tonight's row look, the calm Badge and ConfirmButton.
 */

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, ExternalLink, Loader2, Sunrise } from "lucide-react";
import { Badge, ConfirmButton } from "@/components/ui";
import { providerDisplay } from "@/lib/onboarding";
import { useVisibleInterval } from "@/lib/useVisibleInterval";
import {
  NIGHT_POLL_MS,
  countsWords,
  filesWords,
  listSummaries,
  nightHeading,
  nightStateWords,
  openNightPullRequest,
  pushNightBranch,
  ranWords,
  testsWords,
  throwAwayNightFolder,
  usageLine,
  type NightRow,
  type NightSummary,
} from "@/lib/nightShift";

const QUIET_BUTTON =
  "inline-flex min-h-7 items-center gap-1 rounded-lg px-2 py-1 text-xs text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60 disabled:opacity-40";

function errorWords(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

function NightTaskRow({
  row,
  onChanged,
  onOpenSession,
}: {
  row: NightRow;
  onChanged: () => void;
  onOpenSession: (threadId: string, opts?: { changes?: boolean }) => void;
}) {
  const [busy, setBusy] = useState<"" | "push" | "pr">("");
  const [problem, setProblem] = useState<string | null>(null);
  const [said, setSaid] = useState<string | null>(null);
  const words = nightStateWords(row.state);
  const s = row.session;
  const live = s.exists && s.hasBranch && !s.removed;

  async function act(kind: "push" | "pr", fn: () => Promise<string | null>) {
    if (busy) return;
    setBusy(kind);
    setProblem(null);
    setSaid(null);
    try {
      const note = await fn();
      if (note) setSaid(note);
    } catch (e) {
      setProblem(errorWords(e));
    }
    setBusy("");
    onChanged();
  }

  return (
    <li data-testid={`last-night-task-${row.id}`} data-state={row.state} className="space-y-1 rounded-[10px] px-2 py-2">
      <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
        <Badge value={words.word} tone={words.tone} keepCase data-testid={`last-night-state-${row.id}`} data-status={row.state} />
        <span className="min-w-0 flex-1 break-words text-[13px] text-zinc-200">{row.title}</span>
      </div>
      <p className="break-words text-[12px] text-zinc-500">
        {row.folderName || row.folder}
        {row.branch ? ` · ${row.branch}` : ""} · {ranWords(row.seconds)}
      </p>
      {row.mainChanged ? (
        // Never folded, never by hue alone (Tonight's rule): its commands are
        // not confined to its branch, and the user's main folder changed.
        <p
          data-testid={`last-night-main-changed-${row.id}`}
          className="flex items-start gap-1.5 break-words text-[12px] font-medium text-tone-warn"
        >
          <AlertTriangle size={13} aria-hidden="true" className="mt-[2px] shrink-0" />
          <span className="min-w-0">{row.mainChangedWords || "Your main folder changed while this task ran."}</span>
        </p>
      ) : null}
      {row.failure ? (
        <p data-testid={`last-night-failure-${row.id}`} className="break-words text-[12px] text-zinc-300">
          {row.failure}
        </p>
      ) : null}
      {row.needs.length ? (
        <ul data-testid={`last-night-needs-${row.id}`} className="space-y-0.5 text-[12px] text-zinc-300">
          {row.needs.map((n, i) => (
            <li key={`need-${i}`} className="break-words">
              {n}
            </li>
          ))}
        </ul>
      ) : null}
      <p data-testid={`last-night-facts-${row.id}`} className="break-words text-[12px] text-zinc-400">
        {testsWords(row)} · {filesWords(row)}
        {row.commit ? ` · Committed ${row.commit.sha.slice(0, 7)}` : ""}
      </p>
      {row.committedWords || row.commitNote ? (
        <p data-testid={`last-night-commit-${row.id}`} className="break-words text-[12px] text-zinc-500">
          {row.committedWords || row.commitNote}
        </p>
      ) : null}
      {row.usage.length ? (
        <ul data-testid={`last-night-usage-${row.id}`} className="space-y-0.5 text-[12px] text-zinc-500">
          {row.usage.map((u, i) => (
            <li key={`${u.provider}-${u.model}-${i}`} className="break-words" title={u.model ? `${u.provider} · ${u.model}` : u.provider}>
              {usageLine(u, providerDisplay)}
            </li>
          ))}
        </ul>
      ) : null}
      {row.notes.length ? (
        <details className="text-[12px] text-zinc-400" data-testid={`last-night-notes-${row.id}`}>
          <summary className="cursor-pointer select-none text-zinc-500 hover:text-zinc-300">
            What happened ({row.notes.length})
          </summary>
          <ul className="mt-1 space-y-0.5 pl-3">
            {row.notes.map((n, i) => (
              <li key={`${n.kind}-${i}`} className="break-words">
                {n.text}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
      <div className="flex flex-wrap items-center gap-1">
        {s.exists && row.threadId ? (
          <button
            type="button"
            data-testid={`last-night-open-${row.id}`}
            onClick={() => onOpenSession(row.threadId)}
            className={QUIET_BUTTON}
          >
            Open
          </button>
        ) : null}
        {live ? (
          <button
            type="button"
            data-testid={`last-night-changes-${row.id}`}
            onClick={() => onOpenSession(row.threadId, { changes: true })}
            className={QUIET_BUTTON}
          >
            Changes
          </button>
        ) : null}
        {live && row.commit && !s.pushed && !s.prUrl ? (
          <button
            type="button"
            data-testid={`last-night-push-${row.id}`}
            disabled={!!busy}
            onClick={() =>
              void act("push", async () => {
                await pushNightBranch(row.threadId);
                return `Pushed ${row.branch}.`;
              })
            }
            className={QUIET_BUTTON}
          >
            {busy === "push" ? <Loader2 size={12} className="animate-spin" aria-hidden /> : null}
            Push
          </button>
        ) : null}
        {live && row.commit && !s.prUrl ? (
          <button
            type="button"
            data-testid={`last-night-pr-${row.id}`}
            disabled={!!busy}
            onClick={() =>
              void act("pr", async () => {
                const url = await openNightPullRequest(row.threadId);
                return url ? `Opened a pull request: ${url}` : "Opened a pull request.";
              })
            }
            className={QUIET_BUTTON}
          >
            {busy === "pr" ? <Loader2 size={12} className="animate-spin" aria-hidden /> : null}
            Open a pull request
          </button>
        ) : null}
        {s.prUrl ? (
          <a
            href={s.prUrl}
            target="_blank"
            rel="noreferrer"
            data-testid={`last-night-pr-link-${row.id}`}
            className={QUIET_BUTTON}
          >
            The pull request <ExternalLink size={11} aria-hidden />
          </a>
        ) : null}
        {live && row.state !== "running" ? (
          <span data-testid={`last-night-throw-${row.id}`}>
            <ConfirmButton
              variant="calm"
              label="Throw away"
              confirmLabel="Move its folder to the trash?"
              title="Its branch folder goes to Iron Jarvis's trash; the branch itself is kept."
              onConfirm={async () => {
                setProblem(null);
                setSaid(null);
                try {
                  const note = await throwAwayNightFolder(row.threadId);
                  setSaid(note || `The folder is in Iron Jarvis's trash; the branch ${row.branch} is kept.`);
                } catch (e) {
                  setProblem(errorWords(e));
                }
                onChanged();
              }}
            />
          </span>
        ) : null}
        {s.removed ? (
          <span data-testid={`last-night-removed-${row.id}`} className="px-2 text-[12px] text-zinc-500">
            Folder in the trash; the branch is kept.
          </span>
        ) : null}
      </div>
      {said ? (
        <p role="status" data-testid={`last-night-said-${row.id}`} className="break-words text-[12px] text-zinc-400">
          {said}
        </p>
      ) : null}
      {problem ? (
        <p role="alert" data-testid={`last-night-problem-${row.id}`} className="break-words text-[12px] text-tone-danger">
          {problem}
        </p>
      ) : null}
    </li>
  );
}

function NightBlock({
  summary,
  newest,
  onChanged,
  onOpenSession,
}: {
  summary: NightSummary;
  newest: boolean;
  onChanged: () => void;
  onOpenSession: (threadId: string, opts?: { changes?: boolean }) => void;
}) {
  return (
    <ol data-testid={`last-night-rows-${summary.night}`} className="divide-y divide-white/[0.05]">
      {summary.rows.map((r) => (
        <NightTaskRow key={r.id} row={r} onChanged={onChanged} onOpenSession={onOpenSession} />
      ))}
      {!summary.rows.length ? (
        <li className="px-2 py-2 text-[12px] text-zinc-500">{newest ? "No task ran that night." : "No tasks."}</li>
      ) : null}
    </ol>
  );
}

export function LastNightList({
  onOpenSession,
  focusNight = null,
}: {
  onOpenSession: (threadId: string, opts?: { changes?: boolean }) => void;
  /** A night a link named (`/terminals?night=<date>`): shown open. */
  focusNight?: string | null;
}) {
  const [nights, setNights] = useState<NightSummary[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setNights(await listSummaries());
      setLoadError(null);
    } catch (e) {
      setLoadError(
        e && typeof e === "object" && "status" in e && (e as { status?: number }).status === 404
          ? "This version of Iron Jarvis has no Last night summary yet."
          : errorWords(e),
      );
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);
  useVisibleInterval(() => void refresh(), NIGHT_POLL_MS);

  const newest = nights?.[0] ?? null;
  const older = nights?.slice(1) ?? [];

  return (
    <section data-testid="last-night" aria-labelledby="last-night-title" className="mx-auto w-full max-w-[768px] space-y-3 px-1">
      <header className="space-y-1">
        <h2 id="last-night-title" className="flex items-center gap-2 text-[15px] font-medium text-zinc-100">
          <Sunrise size={14} aria-hidden /> {newest ? nightHeading(newest, true) : "Last night"}
        </h2>
        {newest ? (
          <p data-testid="last-night-counts" className="text-[13px] text-zinc-300">
            {newest.rows.length} task{newest.rows.length === 1 ? "" : "s"}: {countsWords(newest.counts)}.
          </p>
        ) : null}
        <p data-testid="last-night-rule" className="text-[12px] text-zinc-500">
          When a night task ends, Iron Jarvis commits its work to the task&apos;s own branch. It never pushes or
          merges: that is yours to do here.
        </p>
      </header>

      {loadError ? (
        <p role="alert" className="px-2 text-[12px] text-tone-danger">
          {loadError}
        </p>
      ) : nights === null ? (
        <p className="flex items-center gap-1.5 px-2 text-[12px] text-zinc-500">
          <Loader2 size={12} className="animate-spin" /> Reading last night…
        </p>
      ) : !newest ? (
        <p data-testid="last-night-empty" className="px-2 text-[13px] text-zinc-500">
          No night has ended yet. Queue tasks in Tonight; their summary is here in the morning.
        </p>
      ) : (
        <>
          <NightBlock summary={newest} newest onChanged={() => void refresh()} onOpenSession={onOpenSession} />
          {older.length ? (
            <div data-testid="last-night-older" className="space-y-1 pt-2">
              <h3 className="px-2 text-[12px] font-medium text-zinc-400">Earlier nights</h3>
              {older.map((n) => (
                <details
                  key={n.night}
                  data-testid={`last-night-older-${n.night}`}
                  open={focusNight === n.night}
                  className="rounded-[10px] px-2 py-1"
                >
                  <summary className="cursor-pointer select-none text-[13px] text-zinc-300 hover:text-zinc-100">
                    {nightHeading(n, false)} · {n.rows.length} task{n.rows.length === 1 ? "" : "s"}: {countsWords(n.counts)}
                  </summary>
                  <NightBlock summary={n} newest={false} onChanged={() => void refresh()} onOpenSession={onOpenSession} />
                </details>
              ))}
            </div>
          ) : null}
        </>
      )}
    </section>
  );
}
