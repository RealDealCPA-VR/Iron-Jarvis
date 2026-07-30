"use client";

// The workflow draft card (v1.120.0) — the moment a conversation crystallizes
// into a process. Chat's workflow_draft exit tool (or a thread's "Turn into
// workflow") produces a draft; this card renders its steps in-thread with the
// three decisions that matter: Save it, Run it once right now, or Open it in
// the editor. Running streams the run LIVE through the engine's
// workflow.step_started/step_completed events — each step chip lights up as
// its agent works, with a 2s run-record poll as the fallback/authority so a
// dropped socket can never leave the card lying about a finished run.

import { useEffect, useRef, useState } from "react";
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
import type { IJEvent, WorkflowDraft, WorkflowRun } from "@/lib/types";
import { agentLabel, agentMeta, KIND_META, type StepKind } from "@/components/workflow/agents";

type StepState = "pending" | "running" | "completed" | "failed" | "skipped";

interface LiveStep {
  state: StepState;
  summary?: string;
}

const TERMINAL = new Set(["completed", "failed", "cancelled", "interrupted", "error"]);

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
  const [runStatus, setRunStatus] = useState<string | null>(null);
  // True once the run RECORD confirmed the terminal state — events alone may
  // have gaps (the WS has no replay), so the record stays the authority.
  const [reconciled, setReconciled] = useState(false);
  // The human gate (v1.121.0): a parked run's question + the reply being typed.
  const [waitingQ, setWaitingQ] = useState<string | null>(null);
  const [answerText, setAnswerText] = useState("");
  const [answering, setAnswering] = useState(false);
  // Question already answered — a stale poll/event replaying the SAME question
  // must not resurrect the banner (a later, different ask still shows).
  const answeredQRef = useRef<string | null>(null);
  const [steps, setSteps] = useState<Record<string, LiveStep>>({});
  const [error, setError] = useState<string | null>(null);
  // Events already applied to `steps` — useEvents re-delivers its rolling
  // window every render, so side-effectful consumption must dedupe by id.
  const seenRef = useRef<Set<string>>(new Set());

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

  // Poll fallback + authority: the run record's outputs settle the final truth
  // even if the socket dropped mid-run. Polls until the RECORD confirms the
  // terminal state — a workflow.completed learned via the event alone must not
  // stop reconciliation, or a lost step event leaves a chip spinning forever.
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
        if (rec.status && TERMINAL.has(rec.status)) {
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
      } catch {
        /* daemon briefly unreachable — the next tick retries */
      }
    };
    const iv = window.setInterval(() => void poll(), 2000);
    return () => {
      stop = true;
      window.clearInterval(iv);
    };
  }, [runId, reconciled]);

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
    // Clear the PREVIOUS run id FIRST: with the old id still set, run 1's
    // events sitting in the rolling window (plus the 2s poll) would instantly
    // repaint run 1's finished steps as run 2's state.
    setRunId(null);
    setSteps({});
    setRunStatus(null);
    setReconciled(false);
    setWaitingQ(null);
    setAnswerText("");
    answeredQRef.current = null;
    seenRef.current = new Set();
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
      setRunStatus(String(rec.status || "running"));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }

  async function submitAnswer() {
    const text = answerText.trim();
    if (!text || !runId || answering) return;
    setAnswering(true);
    setError(null);
    try {
      await post(`/workflows/runs/${runId}/answer`, { answer: text });
      answeredQRef.current = waitingQ;
      setWaitingQ(null);
      setAnswerText("");
      setRunStatus("running"); // the stream takes it from here
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setAnswering(false);
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

  const running = Boolean(runId) && (!runStatus || !TERMINAL.has(runStatus));

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
        {draft.steps.map((s, i) => {
          const live = steps[s.name];
          const meta = agentMeta(s.agent);
          return (
            <li key={`${s.name}-${i}`} className="flex items-start gap-2.5">
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
                      live?.state === "skipped" ? "text-zinc-600 line-through" : "text-zinc-500"
                    }`}
                  >
                    {i + 1}
                  </span>
                )}
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-baseline gap-x-2">
                  <span className="text-[12.5px] text-zinc-200">{s.name}</span>
                  {(s.kind ?? "agent") === "agent" ? (
                    <span
                      className={`rounded-full border px-1.5 py-px text-[10px] ${meta.chip}`}
                    >
                      {agentLabel(s.agent)}
                    </span>
                  ) : (
                    <span
                      className={`rounded-full border px-1.5 py-px text-[10px] ${
                        (KIND_META[(s.kind ?? "agent") as StepKind] ?? KIND_META.agent).chip
                      }`}
                    >
                      {(KIND_META[(s.kind ?? "agent") as StepKind] ?? KIND_META.agent).label}
                    </span>
                  )}
                </div>
                <p className="line-clamp-2 text-[11.5px] leading-snug text-zinc-500" title={s.task || s.message || ""}>
                  {s.task || s.message || (s.kind === "tool" ? s.tool ?? "" : "")}
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

      {runStatus === "waiting" && waitingQ && (
        <div className="mx-3.5 mb-2.5 rounded-lg border border-amber-500/25 bg-amber-500/[0.06] px-2.5 py-2">
          <p className="text-[12px] text-amber-200">{waitingQ}</p>
          <div className="mt-1.5 flex items-center gap-1.5">
            <input
              value={answerText}
              onChange={(e) => setAnswerText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void submitAnswer();
              }}
              placeholder="Type your answer — the run continues from here"
              aria-label="Answer the workflow"
              className="field flex-1 py-1.5 text-[12.5px]"
            />
            <button
              type="button"
              onClick={() => void submitAnswer()}
              disabled={answering || !answerText.trim()}
              className="btn-accent px-3 py-1.5 text-[12px] disabled:opacity-50"
            >
              {answering ? "Sending…" : "Answer"}
            </button>
          </div>
        </div>
      )}
      {runStatus && TERMINAL.has(runStatus) && (
        <div
          className={`mx-3.5 mb-2.5 rounded-lg border px-2.5 py-1.5 text-[12px] ${
            runStatus === "completed"
              ? "border-emerald-500/25 bg-emerald-500/[0.06] text-emerald-300"
              : "border-rose-500/25 bg-rose-500/[0.06] text-rose-300"
          }`}
        >
          {runStatus === "completed"
            ? "Run finished — every step completed."
            : `Run ${runStatus} — the step marked above is where it stopped.`}
        </div>
      )}
      {error && (
        <div className="mx-3.5 mb-2.5 rounded-lg border border-rose-500/25 bg-rose-500/[0.06] px-2.5 py-1.5 text-[12px] text-rose-300">
          {error}
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
