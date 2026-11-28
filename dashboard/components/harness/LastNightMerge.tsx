"use client";

/**
 * Last night › MERGE ALL THAT PASSED (25-a, R3.5; lib/nightLand and the
 * daemon's harness/landing.py).
 *
 * At the top of a night: "5 of 6 passed their tests." and ONE main button,
 * "Merge all 5". Last night reads the preview ONCE when it opens
 * (useMergeLook: again only when the night's numbers or its newest merge
 * change, or on a press; never on the summary's own beat), so the button's
 * count is the card's and a clashing row says so on the row before any press
 * (review 4 of 25-a). The first press reads the PREVIEW again and shows it
 * here, in place: each branch in the
 * order it was queued, its commits, its files with lines added and removed,
 * the branch it goes into ("main in D:\code\shop") and the tests run after
 * each; a branch that would clash says so ("Merging would clash in a.py")
 * and is left out, and so is every row that did not pass, with why. The
 * second press merges exactly that list (the preview's id); the merge's
 * record is read again only while it runs and the window is visible
 * (MERGE_POLL_MS). The result line says what happened, from the record, and
 * offers Undo the way its plan says (D-25.1), two presses, with each step and
 * its time folded under it. Nothing is pushed (D-25.3). At 390 px every line
 * wraps; nothing sits side by side.
 */

import { useCallback, useEffect, useState } from "react";
import { GitMerge, Loader2 } from "lucide-react";
import { Button, ConfirmButton } from "@/components/ui";
import { useVisibleInterval } from "@/lib/useVisibleInterval";
import {
  MERGE_POLL_MS,
  commitsWords,
  fileLines,
  landingFor,
  lookKey,
  mergeRunning,
  readMerge,
  readPreview,
  runningWords,
  secondsWords,
  startMerge,
  stepEndWords,
  undoMerge,
  type LandPreview,
  type Landing,
  type MergeRecord,
  type NightLand,
} from "@/lib/nightLand";

const QUIET_BUTTON =
  "inline-flex min-h-7 items-center gap-1 rounded-lg px-2 py-1 text-xs text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60 disabled:opacity-40";

function errorWords(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export type LookState = "" | "reading" | "read" | "failed";

export interface MergeLook {
  look: LandPreview | null;
  state: LookState;
  /** A press read the preview: the head line and the rows follow it. */
  took: (p: LandPreview) => void;
}

/** The night's preview, read ONCE when the night is on screen with something
 *  that passed, and again only when `lookKey` changes (the night's numbers or
 *  its newest merge), never on the summary's own beat (G7). */
export function useMergeLook(night: string, land: NightLand | null, active: boolean): MergeLook {
  const [look, setLook] = useState<LandPreview | null>(null);
  const [state, setState] = useState<LookState>("");
  const wanted = active && !!land && land.green > 0;
  const key = lookKey(land);
  useEffect(() => {
    if (!wanted) return;
    let gone = false;
    setState((s) => (s === "read" ? s : "reading"));
    readPreview(night).then(
      (p) => {
        if (gone) return;
        setLook(p);
        setState("read");
      },
      () => {
        if (!gone) setState((s) => (s === "read" ? s : "failed"));
      },
    );
    return () => {
      gone = true;
    };
  }, [night, wanted, key]);
  const took = useCallback((p: LandPreview) => {
    setLook(p);
    setState("read");
  }, []);
  return { look, state, took };
}

function PreviewCard({
  landing,
  busy,
  onMerge,
}: {
  landing: Landing;
  busy: boolean;
  onMerge: (l: Landing) => void;
}) {
  return (
    <div data-testid={`merge-preview-${landing.folderName || landing.folder}`} className="space-y-2 rounded-[10px] bg-white/[0.03] px-3 py-2">
      <p className="break-words text-[12px] text-zinc-300">
        Into <span className="font-medium text-zinc-100">{landing.into || landing.folder}</span>
      </p>
      {landing.commandWords ? (
        <p data-testid="merge-command" className="break-words text-[12px] text-zinc-400">
          {landing.commandWords}
        </p>
      ) : null}
      {landing.depsWords ? <p className="break-words text-[12px] text-zinc-500">{landing.depsWords}</p> : null}
      {landing.steps.length ? (
        <ol data-testid="merge-steps" className="space-y-2">
          {landing.steps.map((s, i) => (
            <li key={s.taskId} data-testid={`merge-step-${s.taskId}`} data-ready={s.ready ? "true" : "false"} className="space-y-0.5">
              <p className="break-words text-[13px] text-zinc-200">
                {i + 1}. {s.title}
              </p>
              <p className="break-words text-[12px] text-zinc-500">
                {s.branch} · {commitsWords(s.commitCount || s.commits.length)} · +{s.added} −{s.removed}
              </p>
              {s.clashWords ? (
                <p data-testid={`merge-clash-${s.taskId}`} className="break-words text-[12px] font-medium text-tone-warn">
                  {s.clashWords}
                </p>
              ) : null}
              {s.commits.length ? (
                <ul className="space-y-0.5 pl-3 text-[12px] text-zinc-400">
                  {s.commits.map((c) => (
                    <li key={c.sha} className="break-words">
                      {c.sha} {c.subject}
                    </li>
                  ))}
                </ul>
              ) : null}
              {s.files.length ? (
                <ul data-testid={`merge-files-${s.taskId}`} className="space-y-0.5 pl-3 text-[12px] text-zinc-400">
                  {s.files.map((f) => (
                    <li key={f.path} className="break-all">
                      {f.path} <span className="text-zinc-500">{fileLines(f)}</span>
                    </li>
                  ))}
                  {s.fileCount > s.files.length ? (
                    <li className="text-zinc-500">…and {s.fileCount - s.files.length} more</li>
                  ) : null}
                </ul>
              ) : null}
            </li>
          ))}
        </ol>
      ) : null}
      {landing.left.length ? (
        <div data-testid="merge-left" className="space-y-0.5">
          <p className="text-[12px] font-medium text-zinc-400">Left out</p>
          <ul className="space-y-0.5 pl-3 text-[12px] text-zinc-400">
            {landing.left.map((x) => (
              <li key={x.taskId} className="break-words">
                {x.title}: {x.why}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {landing.blocked ? (
        <p role="alert" data-testid="merge-blocked" className="break-words text-[12px] text-tone-danger">
          {landing.blocked}
        </p>
      ) : null}
      {landing.id && landing.button ? (
        <Button
          variant="primary"
          size="sm"
          data-testid="merge-confirm"
          disabled={busy}
          onClick={() => onMerge(landing)}
          title="Merges these branches one by one in a copy of the folder, runs the tests after each, and moves your branch once. Nothing is pushed."
        >
          {busy ? <Loader2 size={12} className="mr-1 inline animate-spin" aria-hidden /> : null}
          {landing.button}
        </Button>
      ) : null}
    </div>
  );
}

function ResultLine({ merge, onChanged }: { merge: MergeRecord; onChanged: () => void }) {
  const [plan, setPlan] = useState<MergeRecord | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const read = useCallback(async () => {
    try {
      setPlan(await readMerge(merge.id));
    } catch {
      setPlan(null);
    }
  }, [merge.id]);
  useEffect(() => {
    if (merge.state === "merged" && !merge.undone) void read();
  }, [merge.state, merge.undone, read]);
  const undo = plan?.undo;
  const shown = plan ?? merge;
  return (
    <div data-testid="merge-result" data-state={shown.state} className="space-y-1">
      <p role="status" className="break-words text-[12px] text-zinc-300">
        {shown.words}
      </p>
      {shown.steps.length ? (
        // Each step and its time (the plan's Risk: six branches times a slow suite).
        <details data-testid="merge-step-times" className="text-[12px] text-zinc-400">
          <summary className="cursor-pointer select-none text-zinc-500 hover:text-zinc-300">
            Each step ({shown.steps.length})
          </summary>
          <ol className="mt-1 space-y-0.5 pl-3">
            {shown.steps.map((s, i) => (
              <li key={`${s.taskId}-${i}`} className="break-words">
                {i + 1}. {s.title}: {stepEndWords(s.state)}
                {s.seconds !== null ? ` · ${secondsWords(s.seconds)}` : ""}
              </li>
            ))}
          </ol>
        </details>
      ) : null}
      {shown.undone ? (
        <p data-testid="merge-undone" className="break-words text-[12px] text-zinc-400">
          {shown.undoWords}
        </p>
      ) : undo && undo.how ? (
        <div className="flex flex-wrap items-center gap-1">
          {undo.how === "refused" ? (
            <p data-testid="merge-undo-refused" className="break-words text-[12px] text-zinc-500">
              {undo.words}
            </p>
          ) : (
            <>
              <span data-testid="merge-undo">
                <ConfirmButton
                  variant="calm"
                  label={undo.button || "Undo"}
                  confirmLabel="Undo this merge?"
                  title={undo.words}
                  onConfirm={async () => {
                    setProblem(null);
                    try {
                      const done = await undoMerge(merge.id, undo.how);
                      if (done) setPlan(done);
                    } catch (e) {
                      setProblem(errorWords(e));
                      await read();
                    }
                    onChanged();
                  }}
                />
              </span>
              <span className="break-words text-[12px] text-zinc-500">{undo.words}</span>
            </>
          )}
        </div>
      ) : null}
      {problem ? (
        <p role="alert" className="break-words text-[12px] text-tone-danger">
          {problem}
        </p>
      ) : null}
    </div>
  );
}

export function LastNightMerge({
  night,
  land,
  look,
  onChanged,
}: {
  night: string;
  land: NightLand | null;
  /** The night's preview as Last night read it (useMergeLook). */
  look: MergeLook;
  onChanged: () => void;
}) {
  const [preview, setPreview] = useState<LandPreview | null>(null);
  const [opening, setOpening] = useState(false);
  const [merging, setMerging] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [running, setRunning] = useState<MergeRecord | null>(null);
  const latest = running ?? land?.merges[0] ?? null;
  const live = mergeRunning(latest);

  // A merge already running when the view opened (or started elsewhere).
  useEffect(() => {
    const m = land?.merges[0];
    if (m && mergeRunning(m) && !running) setRunning(m);
  }, [land, running]);

  const poll = useCallback(async () => {
    if (!running) return;
    try {
      const m = await readMerge(running.id);
      if (!m) return;
      setRunning(m);
      if (!mergeRunning(m)) onChanged();
    } catch {
      // read again on the next beat
    }
  }, [running, onChanged]);
  useVisibleInterval(() => void poll(), MERGE_POLL_MS, live);

  async function open() {
    if (opening) return;
    setOpening(true);
    setProblem(null);
    try {
      const p = await readPreview(night);
      setPreview(p);
      look.took(p);
    } catch (e) {
      setProblem(errorWords(e));
    }
    setOpening(false);
  }

  async function merge(l: Landing) {
    if (merging) return;
    setMerging(true);
    setProblem(null);
    try {
      const m = await startMerge(night, l.id);
      setPreview(null);
      if (m) setRunning(m);
    } catch (e) {
      setProblem(errorWords(e));
      // The list changed since it was read: read it again, so the card shows what is true now.
      try {
        const p = await readPreview(night);
        setPreview(p);
        look.took(p);
      } catch {
        // the problem line says enough
      }
    }
    setMerging(false);
    onChanged();
  }

  const heads = (land?.folders ?? []).filter((f) => f.green > 0 && f.button);
  if (!heads.length && !latest && !preview) return null;

  return (
    <section data-testid={`merge-all-${night}`} aria-label="Merge all that passed" className="space-y-2 px-2 py-2">
      {!preview && !live
        ? heads.map((f) => {
            // Once the preview is read, the head line and its button are the
            // card's own (its count leaves out a clash); until then the
            // summary's numbers and the button's plain name; if it could not
            // be read, the summary's count (the card reads it again anyway).
            const l = look.state === "read" ? landingFor(look.look, f) : null;
            const label = l ? l.button : look.state === "failed" ? f.button : "Merge all that passed";
            return (
              <div key={f.folder} className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
                <GitMerge size={13} aria-hidden className="shrink-0 text-zinc-400" />
                <span data-testid="merge-head" className="min-w-0 flex-1 break-words text-[13px] text-zinc-200">
                  {heads.length > 1 ? `${f.folderName || f.folder}: ` : ""}
                  {l?.words || f.words}
                </span>
                {label ? (
                  <Button
                    variant="primary"
                    size="sm"
                    data-testid="merge-open"
                    disabled={opening}
                    onClick={() => void open()}
                    title="Shows every branch it would merge, and what each adds, before anything is merged."
                  >
                    {opening ? <Loader2 size={12} className="mr-1 inline animate-spin" aria-hidden /> : null}
                    {label}
                  </Button>
                ) : (
                  // Nothing can be merged now (each clash is on its row; what
                  // blocks it is on the card): the press only shows why.
                  <button
                    type="button"
                    data-testid="merge-open"
                    className={QUIET_BUTTON}
                    disabled={opening}
                    onClick={() => void open()}
                  >
                    {opening ? <Loader2 size={12} className="animate-spin" aria-hidden /> : null}
                    Show why
                  </button>
                )}
              </div>
            );
          })
        : null}
      {preview ? (
        <div data-testid="merge-preview" className="space-y-2">
          {preview.emergencyStop ? (
            <p role="alert" className="break-words text-[12px] text-tone-danger">
              Emergency stop is on, so nothing can be merged until it is off.
            </p>
          ) : null}
          {preview.landings.map((l) => (
            <PreviewCard key={l.folder} landing={l} busy={merging || preview.emergencyStop} onMerge={(x) => void merge(x)} />
          ))}
          {!preview.landings.length ? (
            <p className="text-[12px] text-zinc-500">Nothing from this night is ready to merge.</p>
          ) : null}
          <button type="button" data-testid="merge-not-now" className={QUIET_BUTTON} onClick={() => setPreview(null)}>
            Not now
          </button>
        </div>
      ) : null}
      {live && latest ? (
        <p role="status" data-testid="merge-running" className="flex items-start gap-1.5 break-words text-[12px] text-zinc-300">
          <Loader2 size={12} className="mt-[2px] shrink-0 animate-spin" aria-hidden />
          <span className="min-w-0">{runningWords(latest)}</span>
        </p>
      ) : null}
      {!live && latest && !preview ? <ResultLine key={latest.id} merge={latest} onChanged={onChanged} /> : null}
      {problem ? (
        <p role="alert" data-testid="merge-problem" className="break-words text-[12px] text-tone-danger">
          {problem}
        </p>
      ) : null}
    </section>
  );
}
