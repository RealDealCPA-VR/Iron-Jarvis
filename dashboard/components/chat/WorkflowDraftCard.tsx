"use client";

// The workflow draft card (v1.120.0) — the moment a conversation crystallizes
// into a process. Chat's workflow_draft exit tool (or a thread's "Turn into
// workflow") produces a draft; this card renders its steps in-thread with the
// three decisions that matter: Save it, Run it once right now, or Open it in
// the editor. Running streams the run LIVE through the engine's
// workflow.step_started/step_completed events — each step chip lights up as
// its agent works, with a 2s run-record poll as the fallback/authority so a
// dropped socket can never leave the card lying about a finished run.
//
// v1.170.0 splits the RUN machinery (live narration + poll authority + the
// ask-gate answer box) into `useWorkflowRun`, shared with the exported
// `WorkflowRunChip` — the compact card chat renders when a reply payload
// carries `workflow_run` (the model ran a saved workflow via the tool) or the
// user starts one from the "+" menu. Two surfaces, ONE state machine: the
// chip must never disagree with the card about what "finished" means, so both
// read the shared WORKFLOW_RUN_TERMINAL set from lib/types.

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Check,
  CircleAlert,
  ExternalLink,
  GitBranch,
  Loader2,
  Play,
  Save,
} from "lucide-react";

import { ApiError, get, post } from "@/lib/api";
import { WORKFLOW_RUN_TERMINAL } from "@/lib/types";
import type {
  IJEvent,
  WorkflowDraft,
  WorkflowRun,
  WorkflowStep,
} from "@/lib/types";
import {
  agentLabel,
  agentMeta,
  KIND_META,
  type StepKind,
} from "@/components/workflow/agents";

type StepState = "pending" | "running" | "completed" | "failed" | "skipped";

export interface LiveStep {
  state: StepState;
  summary?: string;
}

/** The record-gone sentinel: the run RECORD no longer exists (pruned — runs
 *  keep only a bounded history since v1.170.0, or deleted). Not a member of
 *  WORKFLOW_RUN_TERMINAL because it is not a run outcome; it still SETTLES the
 *  chip — polling a deleted record forever is a spinner that never stops. */
export const RUN_RECORD_GONE = "record-gone";

/** A run is LIVE (keep polling, show the spinner) while its status is neither
 *  a terminal outcome nor the record-gone sentinel. */
export function runIsLive(status: string | null): boolean {
  return (
    !status ||
    (!WORKFLOW_RUN_TERMINAL.has(status) && status !== RUN_RECORD_GONE)
  );
}

/** Compact one-line preview of a tool step's arguments ("path=report.md ·
 *  q=Q3") so a deterministic step says what it will actually do. Values are
 *  stringified shallowly and the whole line clipped — this is a glance, not an
 *  inspector. Returns "" for absent/empty args. */
export function toolArgsPreview(args?: Record<string, unknown> | null): string {
  if (!args || typeof args !== "object") return "";
  try {
    const parts = Object.entries(args).map(
      ([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`,
    );
    if (parts.length === 0) return "";
    return parts.join(" · ").slice(0, 160);
  } catch {
    return "";
  }
}

/**
 * The RUN state machine (v1.170.0), extracted from the draft card so the run
 * chip shares it verbatim:
 *
 *  - LIVE NARRATION: workflow.step_started / step_completed / waiting /
 *    completed events for THIS run id fold into per-step state. useEvents
 *    re-delivers its rolling window every render, so consumption dedupes by
 *    event id; other runs' events are filtered by run_id.
 *  - POLL AUTHORITY: a 2s GET of the run record (plus one immediate read, so
 *    a chip mounted on a reloaded thread doesn't spin for 2s on a run that
 *    finished yesterday). Polls until the RECORD confirms a terminal state —
 *    a workflow.completed learned via the event alone must not stop
 *    reconciliation, or a lost step event leaves a chip spinning forever.
 *    `waiting` and `resuming` are NOT terminal (WORKFLOW_RUN_TERMINAL) — the
 *    poll continues through both.
 *  - ASK GATE: a parked run's question + the reply being typed. A question
 *    already answered must not be resurrected by a stale poll/event replaying
 *    the SAME text (a later, different ask still shows).
 */
export function useWorkflowRun(runId: string | null, events: IJEvent[]) {
  const [steps, setSteps] = useState<Record<string, LiveStep>>({});
  const [runStatus, setRunStatus] = useState<string | null>(null);
  // True once the run RECORD confirmed the terminal state — events alone may
  // have gaps (the WS has no replay), so the record stays the authority.
  const [reconciled, setReconciled] = useState(false);
  const [waitingQ, setWaitingQ] = useState<string | null>(null);
  const [answerText, setAnswerText] = useState("");
  const [answering, setAnswering] = useState(false);
  const [answerError, setAnswerError] = useState<string | null>(null);
  const answeredQRef = useRef<string | null>(null);
  // Events already applied to `steps` — dedupe by id (see doc above).
  const seenRef = useRef<Set<string>>(new Set());

  /** Clear every trace of the previous run BEFORE starting a new one: with
   *  the old id still live, run 1's events sitting in the rolling window
   *  (plus the 2s poll) would instantly repaint run 1's finished steps as
   *  run 2's state. */
  const reset = useCallback(() => {
    setSteps({});
    setRunStatus(null);
    setReconciled(false);
    setWaitingQ(null);
    setAnswerText("");
    setAnswering(false);
    setAnswerError(null);
    answeredQRef.current = null;
    seenRef.current = new Set();
  }, []);

  // Live narration: fold this run's step events into per-step state.
  useEffect(() => {
    if (!runId) return;
    for (const ev of [...events].reverse()) {
      if (!ev.id || seenRef.current.has(ev.id)) continue;
      const p = ev.payload as Record<string, unknown>;
      if (p?.run_id !== runId) continue;
      if (ev.type === "workflow.step_started") {
        seenRef.current.add(ev.id);
        setSteps((s) => ({ ...s, [String(p.step)]: { state: "running" } }));
      } else if (ev.type === "workflow.step_completed") {
        seenRef.current.add(ev.id);
        const ok = p.status === "completed";
        setSteps((s) => ({
          ...s,
          [String(p.step)]: {
            state: ok ? "completed" : "failed",
            summary: String(p.summary ?? ""),
          },
        }));
      } else if (ev.type === "workflow.waiting") {
        seenRef.current.add(ev.id);
        const q = String(p.question ?? "This run needs your answer.");
        if (q !== answeredQRef.current) {
          setRunStatus("waiting");
          setWaitingQ(q);
        }
      } else if (ev.type === "workflow.completed") {
        seenRef.current.add(ev.id);
        setRunStatus(String(p.status ?? "completed"));
      }
    }
  }, [events, runId]);

  // Poll fallback + authority (see the hook doc above).
  useEffect(() => {
    if (!runId || reconciled) return;
    let stop = false;
    const poll = async () => {
      try {
        const rec = await get<WorkflowRun>(`/workflows/runs/${runId}`);
        if (stop) return;
        if (rec.status === "waiting") {
          try {
            const w = JSON.parse(String(rec.waiting_json || "{}")) as {
              question?: string;
            };
            const q = w.question || "This run needs your answer.";
            if (q !== answeredQRef.current) {
              setRunStatus("waiting");
              setWaitingQ(q);
            }
          } catch {
            /* record readable next tick */
          }
        }
        if (rec.status && WORKFLOW_RUN_TERMINAL.has(rec.status)) {
          setRunStatus(rec.status);
          setReconciled(true);
          try {
            const outs = JSON.parse(String(rec.outputs_json ?? "{}")) as Record<
              string,
              { status?: string; summary?: string }
            >;
            setSteps((s) => {
              const next = { ...s };
              for (const [name, o] of Object.entries(outs)) {
                const st = o?.status;
                next[name] = {
                  state:
                    st === "completed"
                      ? "completed"
                      : st === "skipped"
                        ? "skipped"
                        : "failed",
                  summary: o?.summary ?? next[name]?.summary,
                };
              }
              return next;
            });
          } catch {
            /* outputs unreadable — the status banner still tells the truth */
          }
        }
      } catch (e) {
        // A 404 is not "try again": the record is GONE (pruned/deleted), and
        // nothing a later tick learns can change that. Settle honestly rather
        // than spin forever — but never overwrite a terminal outcome already
        // learned from an event.
        if (e instanceof ApiError && e.status === 404 && !stop) {
          setReconciled(true);
          setRunStatus((s) =>
            s && WORKFLOW_RUN_TERMINAL.has(s) ? s : RUN_RECORD_GONE,
          );
          return;
        }
        /* daemon briefly unreachable — the next tick retries */
      }
    };
    void poll(); // immediate read — a reloaded thread's chip settles now, not in 2s
    const iv = window.setInterval(() => void poll(), 2000);
    return () => {
      stop = true;
      window.clearInterval(iv);
    };
  }, [runId, reconciled]);

  const submitAnswer = useCallback(async () => {
    const text = answerText.trim();
    if (!text || !runId || answering) return;
    setAnswering(true);
    setAnswerError(null);
    try {
      await post(`/workflows/runs/${runId}/answer`, { answer: text });
      answeredQRef.current = waitingQ;
      setWaitingQ(null);
      setAnswerText("");
      setRunStatus("running"); // the stream takes it from here
    } catch (e) {
      setAnswerError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setAnswering(false);
    }
  }, [answerText, runId, answering, waitingQ]);

  return {
    steps,
    runStatus,
    setRunStatus,
    reconciled,
    waitingQ,
    answerText,
    setAnswerText,
    answering,
    answerError,
    submitAnswer,
    reset,
  };
}

type RunState = ReturnType<typeof useWorkflowRun>;

/** The ask-gate box: a parked run's question + the reply being typed. */
function WaitingBox({ run }: { run: RunState }) {
  if (run.runStatus !== "waiting" || !run.waitingQ) return null;
  return (
    <div className="mx-3.5 mb-2.5 rounded-lg border border-amber-500/25 bg-amber-500/[0.06] px-2.5 py-2">
      <p className="text-[12px] text-amber-200">{run.waitingQ}</p>
      <div className="mt-1.5 flex items-center gap-1.5">
        <input
          value={run.answerText}
          onChange={(e) => run.setAnswerText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void run.submitAnswer();
          }}
          placeholder="Type your answer — the run continues from here"
          aria-label="Answer the workflow"
          className="field flex-1 py-1.5 text-[12.5px]"
        />
        <button
          type="button"
          onClick={() => void run.submitAnswer()}
          disabled={run.answering || !run.answerText.trim()}
          className="btn-accent px-3 py-1.5 text-[12px] disabled:opacity-50"
        >
          {run.answering ? "Sending…" : "Answer"}
        </button>
      </div>
    </div>
  );
}

/** The terminal banner — one honest sentence about how the run ended (or the
 *  muted admission that the record no longer exists to say). */
function OutcomeBanner({ status }: { status: string | null }) {
  if (!status || runIsLive(status)) return null;
  if (status === RUN_RECORD_GONE)
    return (
      <div className="mx-3.5 mb-2.5 rounded-lg border border-white/[0.08] bg-white/[0.03] px-2.5 py-1.5 text-[12px] text-zinc-500">
        This run&apos;s record is no longer on the daemon (old runs are pruned)
        — no outcome to show.
      </div>
    );
  return (
    <div
      className={`mx-3.5 mb-2.5 rounded-lg border px-2.5 py-1.5 text-[12px] ${
        status === "completed"
          ? "border-emerald-500/25 bg-emerald-500/[0.06] text-emerald-300"
          : "border-rose-500/25 bg-rose-500/[0.06] text-rose-300"
      }`}
    >
      {status === "completed"
        ? "Run finished — every step completed."
        : `Run ${status} — the step marked above is where it stopped.`}
    </div>
  );
}

/** Per-step status glyph shared by the card's plan rows and the chip's
 *  discovered rows. `fallback` renders when the step has no live state yet. */
function StepGlyph({ live, index }: { live?: LiveStep; index: number }) {
  return (
    <span className="mt-0.5 w-5 shrink-0 text-center">
      {live?.state === "running" ? (
        <Loader2 size={13} className="inline animate-spin text-accent-soft" />
      ) : live?.state === "completed" ? (
        <Check size={13} className="inline text-emerald-300" />
      ) : live?.state === "failed" ? (
        <CircleAlert size={13} className="inline text-rose-300" />
      ) : (
        <span
          className={`font-mono text-[11px] ${
            live?.state === "skipped"
              ? "text-zinc-600 line-through"
              : "text-zinc-500"
          }`}
        >
          {index + 1}
        </span>
      )}
    </span>
  );
}

/**
 * A LIVE workflow run, compact (v1.170.0) — rendered in-thread when a chat
 * reply's payload carries `workflow_run` (the model ran a saved workflow via
 * the tool) or when the user starts one from the "+" menu. Unlike the draft
 * card it does not know the plan up front: steps appear as their events (or
 * the run record's outputs) name them. Same machinery, same authority rules.
 */
export function WorkflowRunChip({
  runId,
  name,
  events,
}: {
  runId: string;
  name: string;
  events: IJEvent[];
}) {
  const run = useWorkflowRun(runId, events);
  const status = run.runStatus;
  const terminal = Boolean(status && WORKFLOW_RUN_TERMINAL.has(status));
  const live = runIsLive(status);
  const entries = Object.entries(run.steps);
  return (
    <div
      data-testid="workflow-run-chip"
      className="ml-11 max-w-[640px] rounded-xl border border-accent/20 bg-accent/[0.04]"
    >
      <div className="flex items-center gap-2.5 border-b border-white/[0.05] px-3.5 py-2.5">
        <GitBranch size={15} className="shrink-0 text-accent-soft" />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline gap-x-2">
            <span className="font-mono text-[13px] text-zinc-100">{name}</span>
            <span className="text-[10px] uppercase tracking-[0.1em] text-zinc-500">
              workflow run
            </span>
          </div>
        </div>
        {terminal ? (
          status === "completed" ? (
            <Check size={14} className="shrink-0 text-emerald-300" />
          ) : (
            <CircleAlert size={14} className="shrink-0 text-rose-300" />
          )
        ) : status === "waiting" ? (
          <span className="shrink-0 rounded-full border border-amber-500/25 bg-amber-500/[0.08] px-2 py-0.5 text-[10.5px] text-amber-300">
            waiting on you
          </span>
        ) : live ? (
          <Loader2 size={14} className="shrink-0 animate-spin text-accent-soft" />
        ) : null}
      </div>
      {entries.length > 0 ? (
        <ol className="space-y-1 px-3.5 py-2.5">
          {entries.map(([stepName, live], i) => (
            <li key={stepName} className="flex items-start gap-2.5">
              <StepGlyph live={live} index={i} />
              <div className="min-w-0 flex-1">
                <span className="text-[12.5px] text-zinc-200">{stepName}</span>
                {live.summary && live.state !== "running" && (
                  <p
                    className="mt-0.5 line-clamp-2 text-[11.5px] leading-snug text-zinc-400"
                    title={live.summary}
                  >
                    {live.summary}
                  </p>
                )}
              </div>
            </li>
          ))}
        </ol>
      ) : (
        live && (
          <p className="px-3.5 py-2.5 text-[11.5px] text-zinc-500">
            Run started — steps light up here as they happen.
          </p>
        )
      )}
      <WaitingBox run={run} />
      <OutcomeBanner status={status} />
      {run.answerError && (
        <div className="mx-3.5 mb-2.5 rounded-lg border border-rose-500/25 bg-rose-500/[0.06] px-2.5 py-1.5 text-[12px] text-rose-300">
          {run.answerError}
        </div>
      )}
    </div>
  );
}

export function WorkflowDraftCard({
  draft,
  events,
}: {
  draft: WorkflowDraft;
  events: IJEvent[];
}) {
  const router = useRouter();
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  // The name actually saved under — suffixed when the model-chosen name would
  // silently OVERWRITE an existing saved workflow (upsert-by-name semantics).
  const [savedAs, setSavedAs] = useState<string | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const run = useWorkflowRun(runId, events);
  // The ONE step shape (v1.170.0): drafts widened to full step kinds (tool
  // args, on_failure, group) — view them through the shared type so the card
  // can say what a non-agent step really does.
  const draftSteps: WorkflowStep[] = draft.steps;

  async function save() {
    setSaving(true);
    setError(null);
    try {
      // POST /workflows upserts by name — and this name is MODEL-CHOSEN, so a
      // collision would silently clobber a workflow the user hand-tuned.
      // Mirror the generate path's never-clobber rule: suffix to a free name.
      const existing = new Set(
        (await get<{ workflows: { name: string }[] }>("/workflows")).workflows.map(
          (w) => w.name,
        ),
      );
      let name = draft.name;
      for (let i = 2; existing.has(name); i += 1) name = `${draft.name}-${i}`;
      await post("/workflows", {
        name,
        steps: draft.steps,
        description: draft.description || "saved from chat",
        ...(draft.project_id ? { project_id: draft.project_id } : {}),
      });
      setSaved(true);
      setSavedAs(name);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  async function runOnce() {
    setError(null);
    // Clear the PREVIOUS run FIRST (see useWorkflowRun.reset): with the old id
    // still set, run 1's events sitting in the rolling window (plus the 2s
    // poll) would instantly repaint run 1's finished steps as run 2's state.
    setRunId(null);
    run.reset();
    try {
      const rec = await post<WorkflowRun>("/workflows/run", {
        name: draft.name,
        steps: draft.steps,
        // Explicit pin: "" = force-unpinned. Without this the route would
        // inherit the pin of any SAVED workflow sharing this (model-chosen)
        // name — grounding the run in a different project's folder.
        project_id: draft.project_id ?? "",
      });
      setRunId(String(rec.id ?? "") || null);
      run.setRunStatus(String(rec.status || "running"));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }

  function openInEditor() {
    try {
      sessionStorage.setItem(
        "ij_pending_workflow",
        JSON.stringify({
          name: draft.name,
          description: draft.description,
          steps: draft.steps,
        }),
      );
    } catch {
      /* private mode — the editor just won't auto-load */
    }
    router.push("/workflows");
  }

  const running = Boolean(runId) && runIsLive(run.runStatus);

  return (
    <div className="ml-11 max-w-[640px] rounded-xl border border-accent/20 bg-accent/[0.04]">
      <div className="flex items-start gap-2.5 border-b border-white/[0.05] px-3.5 py-2.5">
        <GitBranch size={15} className="mt-0.5 shrink-0 text-accent-soft" />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline gap-x-2">
            <span className="font-mono text-[13px] text-zinc-100">{draft.name}</span>
            <span className="text-[10px] uppercase tracking-[0.1em] text-zinc-500">
              workflow draft
            </span>
          </div>
          {draft.description && (
            <p className="mt-0.5 text-[12px] leading-snug text-zinc-400">
              {draft.description}
            </p>
          )}
        </div>
      </div>

      <ol className="space-y-1 px-3.5 py-2.5">
        {draftSteps.map((s, i) => {
          const live = run.steps[s.name];
          const kind = (s.kind ?? "agent") as string;
          const kindMeta = KIND_META[kind as StepKind] ?? KIND_META.agent;
          const meta = agentMeta(s.agent ?? "");
          // Say what each kind actually DOES (v1.170.0): ask/notify lead with
          // their message (the question / the notification), a tool step names
          // the tool and previews its args — a draft the user can't read is a
          // draft they can't trust before running it.
          const detail =
            kind === "ask" || kind === "notify"
              ? s.message || s.task || ""
              : s.task ||
                s.message ||
                (kind === "tool" ? toolArgsPreview(s.args) : "");
          return (
            <li key={`${s.name}-${i}`} className="flex items-start gap-2.5">
              <StepGlyph live={live} index={i} />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-baseline gap-x-2">
                  <span className="text-[12.5px] text-zinc-200">{s.name}</span>
                  {kind === "agent" ? (
                    <span
                      className={`rounded-full border px-1.5 py-px text-[10px] ${meta.chip}`}
                    >
                      {agentLabel(s.agent ?? "") || "Agent"}
                    </span>
                  ) : (
                    <>
                      <span
                        className={`rounded-full border px-1.5 py-px text-[10px] ${kindMeta.chip}`}
                      >
                        {kindMeta.label}
                      </span>
                      {kind === "tool" && s.tool && (
                        <span className="font-mono text-[10.5px] text-sky-300/80">
                          {s.tool}
                        </span>
                      )}
                    </>
                  )}
                </div>
                <p
                  className="line-clamp-2 text-[11.5px] leading-snug text-zinc-500"
                  title={detail}
                >
                  {detail}
                </p>
                {live?.summary && live.state !== "running" && (
                  <p
                    className="mt-0.5 line-clamp-2 text-[11.5px] leading-snug text-zinc-400"
                    title={live.summary}
                  >
                    {live.summary}
                  </p>
                )}
              </div>
            </li>
          );
        })}
      </ol>

      <WaitingBox run={run} />
      <OutcomeBanner status={run.runStatus} />
      {(error || run.answerError) && (
        <div className="mx-3.5 mb-2.5 rounded-lg border border-rose-500/25 bg-rose-500/[0.06] px-2.5 py-1.5 text-[12px] text-rose-300">
          {error || run.answerError}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-1.5 border-t border-white/[0.05] px-3.5 py-2">
        <button
          type="button"
          onClick={() => void save()}
          disabled={saving || saved}
          className="btn-ghost px-2.5 py-1.5 text-[12px] disabled:opacity-60"
        >
          {saved ? (
            <>
              <Check size={13} className="text-emerald-300" /> Saved
            </>
          ) : (
            <>
              <Save size={13} /> {saving ? "Saving…" : "Save workflow"}
            </>
          )}
        </button>
        <button
          type="button"
          onClick={() => void runOnce()}
          disabled={running}
          className="btn-ghost px-2.5 py-1.5 text-[12px] disabled:opacity-60"
        >
          {running ? (
            <>
              <Loader2 size={13} className="animate-spin" /> Running…
            </>
          ) : (
            <>
              <Play size={13} /> Run once
            </>
          )}
        </button>
        <button
          type="button"
          onClick={openInEditor}
          className="btn-ghost px-2.5 py-1.5 text-[12px]"
        >
          <ExternalLink size={13} /> Open in editor
        </button>
        {saved && (
          <span className="ml-auto text-[11px] text-zinc-500">
            {savedAs && savedAs !== draft.name
              ? `saved as “${savedAs}” (the original name was taken)`
              : "find it on the Workflows page — or schedule it"}
          </span>
        )}
      </div>
    </div>
  );
}
