"use client";

import { Fragment, useEffect, useId, useRef, useState } from "react";
import { History, Send, Sparkles, Loader2, ChevronRight, ArrowUp } from "lucide-react";
import { useApi } from "@/lib/useApi";
import { useEvents } from "@/lib/useEvents";
import { useVisibleInterval } from "@/lib/useVisibleInterval";
import { post, ApiError } from "@/lib/api";
import type { WorkflowRun, WorkflowStep } from "@/lib/types";
import {
  STARTERS,
  isLiveRun,
  runBadgeTone,
  starterKindSummary,
  starterLoadDetail,
  stepKindHint,
  type StarterWorkflow,
} from "@/components/workflow/starters";
import { Badge, Empty, SkeletonRows } from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";
import WorkflowCanvas from "@/components/workflow/WorkflowCanvas";
import { SavedWorkflows } from "@/components/workflow/SavedWorkflows";
import {
  CalmSection,
  WF_CHIP,
  WF_GHOST,
  WF_GHOST_ON,
  WF_GHOST_ON_SM,
  WF_GHOST_SM,
} from "@/components/workflow/calm";
import { COMPOSER_CARD_EDGE } from "@/lib/composerChips";
import { timeAgo } from "@/lib/format";
import { plainText } from "@/components/Markdown";

export default function WorkflowsPage() {
  // Handoff from a terminal pane's "→ Workflow" button: it stashes the generated
  // workflow in sessionStorage and navigates here; load it into the canvas once
  // WorkflowCanvas has mounted its `ij:load-workflow` listener.
  useEffect(() => {
    let raw: string | null = null;
    try {
      raw = sessionStorage.getItem("ij_pending_workflow");
    } catch {
      return;
    }
    if (!raw) return;
    let def: unknown;
    try {
      def = JSON.parse(raw);
    } catch {
      // Malformed payload — clear it so it doesn't linger across visits.
      try {
        sessionStorage.removeItem("ij_pending_workflow");
      } catch {
        /* ignore */
      }
      return;
    }
    // Remove only when we actually dispatch: a StrictMode mount → cleanup →
    // remount cycle cancels this timeout, and the item must survive for the
    // second mount to consume.
    const t = setTimeout(() => {
      try {
        sessionStorage.removeItem("ij_pending_workflow");
      } catch {
        /* ignore */
      }
      window.dispatchEvent(new CustomEvent("ij:load-workflow", { detail: def }));
      window.scrollTo({ top: 0, behavior: "smooth" });
    }, 80);
    return () => clearTimeout(t);
  }, []);

  // v1.316.0 (UX wave 4): the builder's conversation lives HERE, so the quick
  // box above the canvas and the "Build with chat" card below are two doors
  // into ONE send (one POST /workflows/generate, one thread) — never a second
  // generator path.
  const builder = useWorkflowBuilder();

  return (
    <PageShell>
      <Reveal>
        <PageHeader
          title="Workflows"
          subtitle="Wire agents into a visual, multi-step workflow, then run it. Describe one below, or send a terminal session here with its → Workflow button."
        />
      </Reveal>
      {/* v1.316.0 (UX wave 4): the easy path before the power editor — with
          no saved workflows yet, describe one in words or pick a starter
          right here; the canvas below is unchanged. */}
      <QuickStart builder={builder} />
      <Reveal>
        <WorkflowCanvas />
      </Reveal>
      {/* v1.222.0: the saved workflows, visible — with Load and Delete on
          every row. Delete used to exist only as a hover icon inside the
          canvas's Load ▾ dropdown, which the user could not find. */}
      <Reveal>
        <SavedWorkflows />
      </Reveal>
      <Reveal>
        <StarterTemplates />
      </Reveal>
      <Reveal>
        <div id="build-with-chat" className="scroll-mt-4">
          <WorkflowBuilderChat builder={builder} />
        </div>
      </Reveal>
      <Reveal>
        <RunHistory />
      </Reveal>
    </PageShell>
  );
}

/* -------------------------------------------------------------------------- */
/*  Quick start (v1.316.0): describe it, or pick a starter — above the editor  */
/* -------------------------------------------------------------------------- */

/** The easy path, shown ABOVE the canvas while the user has no saved
 *  workflows (UX wave 4: the viewport-tall editor used to hide both easy paths
 *  below the fold). Two doors, no new machinery:
 *   - "Describe a workflow" sends through the page's ONE builder (`builder.
 *     send` — the same POST, the same thread "Build with chat" shows);
 *   - a starter button fires the same `ij:load-workflow` event the Templates
 *     card fires, and saves nothing — the user presses Save.
 *  The describe door is NOT save-free: POST /workflows/generate saves the
 *  workflow it builds (daemon `_build_workflow` → `store.save`) before it
 *  replies, so this box never says "nothing is saved" about it — the
 *  daemon's own reply ("Built X … Loaded into the editor") is the line.
 *  Shown on the same rule the Templates card uses to expand itself: only once
 *  /workflows has ANSWERED with none (loading/offline = unknown = hidden). It
 *  reads the list once per visit, so it never vanishes mid-task when the
 *  first workflow is saved. */
function QuickStart({ builder }: { builder: WorkflowBuilder }) {
  const { data, error, loading } = useApi<{ workflows: unknown[] }>("/workflows");
  const saved = data?.workflows;
  const none = !loading && !error && Array.isArray(saved) && saved.length === 0;
  const [text, setText] = useState("");
  // Index in the builder thread where this box's last message landed, so the
  // reply shown here is the one to THIS box's request.
  const [sentAt, setSentAt] = useState<number | null>(null);
  const [loaded, setLoaded] = useState<string | null>(null);
  const inputId = useId();

  if (!none) return null;

  const answer =
    sentAt !== null && builder.messages[sentAt + 1]?.role === "assistant"
      ? builder.messages[sentAt + 1]
      : null;
  // The builder's reply is markdown ("Built **name** …"); this one line reads
  // it as plain words (the card below keeps the full reply).
  const reply = answer ? plainText(answer.content) : null;

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const msg = text.trim();
    if (!msg || builder.busy) return;
    setSentAt(builder.messages.length);
    setLoaded(null);
    setText("");
    void builder.send(msg);
  }

  function loadStarter(s: StarterWorkflow) {
    window.dispatchEvent(new CustomEvent("ij:load-workflow", { detail: starterLoadDetail(s) }));
    setLoaded(s.title);
    setSentAt(null);
  }

  function showChat() {
    document.getElementById("build-with-chat")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  const ready = !builder.busy && !!text.trim();

  return (
    <Reveal>
      {/* v1.329.0 (calm chat wave 9, K3): the page's ONE card, drawn like the
          chat composer: a borderless box inside a hairline-edged card, and
          Build it quiet until there is something to build. The label is tied
          to the box by useId (the v1.316.0 rule), in sentence case. */}
      <section data-testid="workflow-describe" className="space-y-2">
        <form
          onSubmit={submit}
          data-testid="workflow-describe-card"
          className={`rounded-[20px] bg-ink-800 ${COMPOSER_CARD_EDGE}`}
        >
          <label
            htmlFor={inputId}
            className="flex items-center gap-1.5 px-4 pt-3 text-[12px] font-medium text-zinc-400"
          >
            <Sparkles size={12} aria-hidden /> Describe a workflow
          </label>
          <div className="flex items-center gap-2 pb-2 pl-2 pr-2">
            <input
              id={inputId}
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="e.g. research a topic, draft a summary, then review it"
              className="min-w-0 flex-1 bg-transparent px-2 py-2 text-[14px] text-zinc-100 caret-accent outline-none placeholder:text-zinc-500 disabled:opacity-60"
              disabled={builder.busy}
            />
            <button
              type="submit"
              data-testid="workflow-describe-build"
              disabled={!ready}
              className={`inline-flex h-[34px] shrink-0 items-center gap-1.5 rounded-full px-3.5 text-[13px] ${
                ready ? "btn-accent" : "cursor-not-allowed bg-white/[0.06] text-zinc-500"
              }`}
            >
              {builder.busy ? <Loader2 size={14} className="animate-spin" /> : <ArrowUp size={15} />}
              Build it
            </button>
          </div>
        </form>
        {(builder.busy && sentAt !== null) || reply ? (
          <p className="px-2 text-[12px] text-zinc-400" aria-live="polite">
            {builder.busy && sentAt !== null ? (
              "Building the workflow…"
            ) : (
              <>
                {reply}{" "}
                <button
                  type="button"
                  onClick={showChat}
                  className="text-accent-soft underline-offset-2 hover:underline"
                >
                  Refine it in Build with chat
                </button>
              </>
            )}
          </p>
        ) : null}
        <div className="flex flex-wrap items-center gap-1 px-1">
          <span className="px-1 text-[12px] text-zinc-500">Or start from a template:</span>
          {STARTERS.map((s) => (
            <button
              key={s.name}
              type="button"
              onClick={() => loadStarter(s)}
              title={s.blurb}
              className={WF_CHIP}
            >
              {s.title}
            </button>
          ))}
        </div>
        {loaded && (
          <p className="px-2 text-[12px] text-zinc-500" aria-live="polite">
            Loaded “{loaded}” into the editor below. Press Save to keep it.
          </p>
        )}
      </section>
    </Reveal>
  );
}

/* -------------------------------------------------------------------------- */
/*  Build-with-chat: describe a workflow, an agent builds it into the editor   */
/* -------------------------------------------------------------------------- */

type WfStep = { name: string; agent: string; task: string; tool: string | null };
type ChatMsg = { role: "user" | "assistant"; content: string };

const EXAMPLES = [
  "Research a topic, draft a summary, then review it",
  "Pull my open tasks, prioritize them, and write a plan for today",
  "Read a folder of docs, extract the key points, and save a brief",
];

/** v1.316.0: the builder chat's state, lifted to the page so the quick box
 *  above the canvas sends through the SAME `send` (and lands in the same
 *  thread) as the card's own box. Behaviour is the pre-v1.316.0 component's,
 *  moved verbatim. */
type WorkflowBuilder = {
  messages: ChatMsg[];
  busy: boolean;
  send: (text: string) => Promise<void>;
};

function useWorkflowBuilder(): WorkflowBuilder {
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [busy, setBusy] = useState(false);
  // The workflow currently loaded in the editor (name + steps). Sent back on a
  // follow-up so /workflows/generate REFINES it instead of minting a new one.
  const currentRef = useRef<{ name: string; steps: WfStep[] } | null>(null);
  // Track what the canvas has loaded (via Load, terminal handoff, or a prior
  // generate) so refinements carry the current workflow as context.
  useEffect(() => {
    const onChanged = (e: Event) => {
      const d = (e as CustomEvent).detail as
        | { name?: string; steps?: WfStep[] }
        | undefined;
      if (d?.name)
        currentRef.current = {
          name: d.name,
          steps: Array.isArray(d.steps) ? d.steps : [],
        };
    };
    window.addEventListener("ij:workflow-changed", onChanged);
    return () => window.removeEventListener("ij:workflow-changed", onChanged);
  }, []);

  async function send(text: string) {
    const msg = text.trim();
    if (!msg || busy) return;
    setMessages((m) => [...m, { role: "user", content: msg }]);
    setBusy(true);
    try {
      const cur = currentRef.current;
      const res = await post<{ name: string; description: string; steps: WfStep[]; reply: string }>(
        "/workflows/generate",
        // On a follow-up, hand the daemon the loaded workflow so it refines it.
        cur
          ? { description: msg, current: cur.steps, name: cur.name }
          : { description: msg },
      );
      currentRef.current = { name: res.name, steps: res.steps };
      // Load the generated steps into the editor above (WorkflowCanvas listens
      // for this event and rebuilds its graph — the same code path as "Load").
      window.dispatchEvent(
        new CustomEvent("ij:load-workflow", {
          detail: {
            name: res.name,
            description: res.description,
            steps_json: JSON.stringify(res.steps),
          },
        }),
      );
      setMessages((m) => [...m, { role: "assistant", content: res.reply }]);
    } catch (err) {
      let reply = "Something went wrong building that workflow.";
      if (err instanceof ApiError) {
        if (err.status === 422)
          reply = "I couldn't turn that into a workflow. Try describing the steps more concretely.";
        else if (err.status === 0) reply = "The daemon looks offline. Start it and try again.";
        else reply = err.message;
      }
      setMessages((m) => [...m, { role: "assistant", content: reply }]);
    } finally {
      setBusy(false);
    }
  }

  return { messages, busy, send };
}

function WorkflowBuilderChat({ builder }: { builder: WorkflowBuilder }) {
  const { messages, busy } = builder;
  const [input, setInput] = useState("");
  const threadRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    threadRef.current?.scrollTo({ top: threadRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, busy]);

  function send(text: string) {
    if (!text.trim() || busy) return;
    setInput("");
    void builder.send(text);
  }

  const ready = !busy && !!input.trim();

  // v1.329.0 (calm chat wave 9, K3): a plain section like the rest of the
  // page. The thread reads like the chat: replies are bare prose, your own
  // message is the tinted bubble, and the examples are quiet chips.
  return (
    <CalmSection title="Build with chat">
      <p className="mb-3 px-1 text-[12px] text-zinc-500">
        Describe a process and the agent builds the steps into the editor above. For example,{" "}
        <span className="text-zinc-400">“research a topic, draft a summary, then review it.”</span>
      </p>

      <div ref={threadRef} className="mb-3 max-h-72 space-y-3 overflow-y-auto px-1">
        {messages.length === 0 && !busy ? (
          <div className="space-y-1.5">
            <div className="text-[12px] text-zinc-500">Try one of these:</div>
            <div className="-ml-1 flex flex-wrap gap-1">
              {EXAMPLES.map((ex) => (
                <button key={ex} type="button" onClick={() => send(ex)} className={WF_CHIP}>
                  {ex}
                </button>
              ))}
            </div>
          </div>
        ) : (
          messages.map((m, i) => (
            <div key={i} className={`flex ${m.role === "user" ? "justify-end" : ""}`}>
              <div
                className={`whitespace-pre-wrap text-[13px] leading-relaxed ${
                  m.role === "user"
                    ? "max-w-[80%] rounded-2xl bg-accent/[0.1] px-3 py-2 text-zinc-100"
                    : "max-w-full text-zinc-300"
                }`}
              >
                {m.content}
              </div>
            </div>
          ))
        )}
        {busy && (
          <div className="flex items-center gap-2 text-[12px] text-zinc-500">
            <Loader2 size={13} className="animate-spin" /> Building the workflow…
          </div>
        )}
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          send(input);
        }}
        className="flex items-end gap-2"
      >
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send(input);
            }
          }}
          rows={2}
          placeholder="Describe the workflow you want… (Enter to send, Shift+Enter for a new line)"
          className="field flex-1 resize-y text-[13px]"
          disabled={busy}
        />
        <button
          type="submit"
          disabled={!ready}
          className={ready ? WF_GHOST_ON : WF_GHOST}
        >
          {busy ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
          Build
        </button>
      </form>
    </CalmSection>
  );
}

/* -------------------------------------------------------------------------- */
/*  Starter templates: five real workflows, one click to load onto the canvas  */
/* -------------------------------------------------------------------------- */

function StarterTemplates() {
  // Whether the user has ANY saved workflows decides prominence: none → the
  // catalog is the empty state (expanded cards); some → a collapsed Templates
  // section they can open. Loading or offline means saved defs are UNKNOWN —
  // auto-expanding there would assert "you have nothing yet" on a guess, so
  // unknown stays collapsed (the toggle still works).
  const { data, error, loading } = useApi<{ workflows: unknown[] }>("/workflows");
  const saved = data?.workflows;
  const hasSaved = Array.isArray(saved) && saved.length > 0;
  const [userToggle, setUserToggle] = useState<boolean | null>(null);
  const expanded = userToggle ?? (!loading && !error && !hasSaved);
  // Which starter was last loaded — feedback that the click did something,
  // and where it went (the canvas above, NOT the saved list).
  const [loadedName, setLoadedName] = useState<string | null>(null);

  function loadStarter(s: StarterWorkflow) {
    // Same event (and detail shape) as the terminal "→ Workflow" handoff: the
    // canvas rebuilds its graph from `steps`. Deliberately NOT auto-saved —
    // the user reviews/edits and presses Save themselves (suggest-don't-act).
    window.dispatchEvent(
      new CustomEvent("ij:load-workflow", { detail: starterLoadDetail(s) }),
    );
    setLoadedName(s.name);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  // v1.329.0 (calm chat wave 9, K3): a plain section; each template is a row
  // over a hairline (title, what it does, a quiet Load), not a boxed tile.
  return (
    <CalmSection
      title="Templates"
      right={
        <button type="button" onClick={() => setUserToggle(!expanded)} className={WF_GHOST_SM}>
          {expanded ? "Hide" : `Show ${STARTERS.length}`}
        </button>
      }
    >
      {!expanded ? (
        <p className="px-1 text-[12px] text-zinc-500">
          {STARTERS.length} starter workflows: client intake, month-end close,
          weekly digest and more. Load one onto the canvas and make it yours.
        </p>
      ) : (
        <>
          {!hasSaved && !loading && !error && (
            <p className="mb-2 px-1 text-[12px] text-zinc-500">
              No saved workflows yet. Start from one of these. Loading a
              template only fills the editor above, and nothing is saved until
              you press Save.
            </p>
          )}
          <ul data-testid="workflow-templates" className="divide-y divide-white/[0.06]">
            {STARTERS.map((s) => (
              <li
                key={s.name}
                className="flex flex-col gap-2 px-1 py-2.5 sm:flex-row sm:items-start sm:gap-4"
              >
                <div className="min-w-0 flex-1">
                  <div className="text-[13px] font-medium text-zinc-100">{s.title}</div>
                  <div className="mt-0.5 text-[11px] text-zinc-500">{starterKindSummary(s)}</div>
                  <p className="mt-1 text-[12px] leading-relaxed text-zinc-400">{s.blurb}</p>
                </div>
                <div className="flex shrink-0 items-center gap-2 sm:flex-col sm:items-end">
                  <button
                    type="button"
                    onClick={() => loadStarter(s)}
                    className={`${WF_GHOST_SM} -ml-2 sm:ml-0`}
                  >
                    Load into editor
                  </button>
                  {loadedName === s.name && (
                    <span className="text-[11px] text-zinc-500">
                      Loaded above. Press Save to keep it.
                    </span>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </>
      )}
    </CalmSection>
  );
}

/* -------------------------------------------------------------------------- */
/*  Run history                                                                */
/* -------------------------------------------------------------------------- */

type StepOut = {
  session_id?: string | null;
  status?: string;
  summary?: string;
  tool?: string | null;
};

/** The ordered step definitions the run was created with — the ONE shared
 *  step shape (v1.170.0); this file used to keep its own two-field copy.
 *  isLiveRun / runBadgeTone / stepKindHint live in workflow/starters.ts (a
 *  page file may export nothing beyond its default). */
function parseStepDefs(r: WorkflowRun): Partial<WorkflowStep>[] {
  try {
    const p = JSON.parse(String((r as { steps_json?: string }).steps_json ?? "[]"));
    return Array.isArray(p) ? p : [];
  } catch {
    return [];
  }
}

/** The per-step outputs the engine wrote as it ran (stepName → result). */
function parseStepOuts(r: WorkflowRun): Record<string, StepOut> {
  try {
    const p = JSON.parse(String((r as { outputs_json?: string }).outputs_json ?? "{}"));
    return p && typeof p === "object" && !Array.isArray(p) ? p : {};
  } catch {
    return {};
  }
}

/** A waiting run's parked question (v1.121.0). */
function parseWaiting(r: WorkflowRun): { question?: string } | null {
  try {
    const w = JSON.parse(String((r as { waiting_json?: string }).waiting_json || ""));
    return w && typeof w === "object" ? (w as { question?: string }) : null;
  } catch {
    return null;
  }
}

/** Run-level honesty notes (v1.225.0, `notes_json`): what the engine decided
 *  silently before — chiefly "the pinned project's folder is missing, steps
 *  ran in a scratch workspace". Absent on an older daemon → []. */
function parseNotes(r: WorkflowRun): string[] {
  try {
    const n = JSON.parse(String((r as { notes_json?: string }).notes_json ?? "[]"));
    return Array.isArray(n) ? n.map(String).filter(Boolean) : [];
  } catch {
    return [];
  }
}

/** Count the sessions a run spawned — from `session_ids_json`, falling back to
 *  the per-step outputs (each completed step carries its session id). */
function sessionCount(r: WorkflowRun): number {
  try {
    const arr = JSON.parse(String(r.session_ids_json ?? "[]"));
    if (Array.isArray(arr) && arr.length) return arr.length;
  } catch {
    /* fall through */
  }
  return Object.values(parseStepOuts(r)).filter((o) => o?.session_id).length;
}

/** Best-available timestamp (the daemon record uses `started_at`). */
function runTimestamp(r: WorkflowRun): string | null {
  const raw = (r.started_at ?? r.finished_at ?? r.created_at) as
    | string
    | null
    | undefined;
  return raw ?? null;
}

function RunHistory() {
  // Newest-first, capped server-side.
  const { data, error, loading, reload } = useApi<{ runs: WorkflowRun[] }>(
    "/workflows/runs?limit=50",
  );
  const [expanded, setExpanded] = useState<string | null>(null);
  // The human gate (v1.121.0): per-run answer drafts for parked (waiting) runs.
  const [answerDraft, setAnswerDraft] = useState<Record<string, string>>({});
  const [answeringId, setAnsweringId] = useState<string | null>(null);
  const [answerErr, setAnswerErr] = useState<string | null>(null);
  // Resume-from-interrupted (v1.170.0, contract 4).
  const [resumingRunId, setResumingRunId] = useState<string | null>(null);
  const [resumeErr, setResumeErr] = useState<{ id: string; message: string } | null>(
    null,
  );

  async function resumeRun(runId: string) {
    if (resumingRunId) return;
    setResumingRunId(runId);
    setResumeErr(null);
    try {
      await post(`/workflows/runs/${encodeURIComponent(runId)}/resume`, {});
      reload();
    } catch (e) {
      // 409 = the run is no longer interrupted (finished, or already resumed
      // elsewhere) — the server's message says which; show it, don't guess.
      setResumeErr({
        id: runId,
        message: e instanceof ApiError ? e.message : String(e),
      });
      // The 409 is proof our local record is STALE ("interrupted" is terminal,
      // so no poll would ever correct it) — refetch so the row agrees with the
      // error message beside it instead of keeping a clickable Resume button.
      reload();
    } finally {
      setResumingRunId(null);
    }
  }

  async function submitAnswer(runId: string) {
    const text = (answerDraft[runId] ?? "").trim();
    if (!text || answeringId) return;
    setAnsweringId(runId);
    setAnswerErr(null);
    try {
      await post(`/workflows/runs/${encodeURIComponent(runId)}/answer`, {
        answer: text,
      });
      setAnswerDraft((d) => ({ ...d, [runId]: "" }));
      reload();
    } catch (e) {
      setAnswerErr(e instanceof ApiError ? e.message : String(e));
    } finally {
      setAnsweringId(null);
    }
  }

  // Refetch the moment a workflow finishes (the engine emits workflow.completed).
  const { events } = useEvents(50);
  const lastCompleted = events.find(
    (e) => e.type === "workflow.completed" || e.type === "workflow.waiting",
  );
  useEffect(() => {
    if (lastCompleted) reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lastCompleted?.id]);

  const offline = error && error.status === 0;
  const runs = data?.runs ?? [];

  // While any run is live (running / waiting / resuming — the shared
  // WORKFLOW_RUN_TERMINAL set decides), keep the table fresh: a resumed run's
  // progress emits no workflow.completed until the END, so events alone leave
  // the history stale for the whole run.
  const hasLive = runs.some(isLiveRun);
  // v1.250.0 (S-09): only while the window is visible — a minimised dashboard
  // refetched the run table every 5 s for as long as anything was live.
  useVisibleInterval(reload, 5000, hasLive);

  // Newest first (records carry a started_at timestamp).
  const ordered = [...runs].sort((a, b) => {
    const ta = new Date(runTimestamp(a) ?? 0).getTime();
    const tb = new Date(runTimestamp(b) ?? 0).getTime();
    return tb - ta;
  });

  // v1.329.0 (calm chat wave 9, K3): a plain section with sentence-case
  // column names; the notes and the waiting ask are tone lines, not boxes.
  return (
    <CalmSection title={`Run history${runs.length ? ` · ${runs.length}` : ""}`}>
      {loading && !data ? (
        <SkeletonRows rows={4} />
      ) : offline ? (
        <Empty icon={<History size={22} />}>
          Daemon offline. Run history is unavailable.
        </Empty>
      ) : ordered.length === 0 ? (
        <Empty icon={<History size={22} />}>
          No workflow runs yet. Run a workflow above to see it here.
        </Empty>
      ) : (
        <div className="-mx-1 overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b hairline text-[12px] text-zinc-500">
                <th className="px-2 py-2.5 font-medium" />
                <th className="px-2 py-2.5 font-medium">Workflow</th>
                <th className="px-2 py-2.5 font-medium">Status</th>
                <th className="px-2 py-2.5 font-medium">Sessions</th>
                <th className="px-2 py-2.5 font-medium">When</th>
              </tr>
            </thead>
            <tbody>
              {ordered.map((r, i) => {
                const ts = runTimestamp(r);
                const n = sessionCount(r);
                const key = String(r.id ?? `${r.workflow_name}-${i}`);
                const defs = parseStepDefs(r);
                const outs = parseStepOuts(r);
                const isOpen = expanded === key;
                const canExpand = defs.length > 0;
                return (
                  <Fragment key={key}>
                    <tr
                      onClick={() =>
                        canExpand && setExpanded(isOpen ? null : key)
                      }
                      className={`border-b border-white/[0.06] last:border-0 hover:bg-white/[0.03] ${
                        canExpand ? "cursor-pointer" : ""
                      }`}
                    >
                      <td className="px-2 py-2.5 text-zinc-500">
                        {canExpand && (
                          <ChevronRight
                            size={14}
                            className={`transition-transform ${isOpen ? "rotate-90" : ""}`}
                          />
                        )}
                      </td>
                      <td className="px-2 py-2.5 text-zinc-100">
                        {r.workflow_name || "—"}
                      </td>
                      <td className="px-2 py-2.5">
                        <div className="flex flex-wrap items-center gap-2">
                          <Badge
                            value={r.status || "unknown"}
                            tone={runBadgeTone(r.status)}
                          />
                          {r.status === "interrupted" && r.id != null && (
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation();
                                void resumeRun(String(r.id));
                              }}
                              disabled={resumingRunId != null}
                              className={WF_GHOST_ON_SM}
                            >
                              {resumingRunId === String(r.id)
                                ? "Resuming…"
                                : "Resume"}
                            </button>
                          )}
                        </div>
                        {resumeErr && resumeErr.id === String(r.id) && (
                          <p className="mt-1 text-[12px] text-tone-danger">
                            {resumeErr.message}
                          </p>
                        )}
                      </td>
                      <td className="px-2 py-2.5 text-zinc-400">
                        {n} session{n === 1 ? "" : "s"}
                      </td>
                      <td className="px-2 py-2.5 text-zinc-500">
                        {ts ? timeAgo(ts) : "—"}
                      </td>
                    </tr>
                    {isOpen && (
                      <tr className="border-b border-white/[0.06]">
                        <td colSpan={5} className="px-3 pb-3 pt-1">
                          {parseNotes(r).map((note) => (
                            <p
                              key={note}
                              data-testid="run-note"
                              className="mb-2 text-[13px] leading-relaxed text-tone-warn"
                            >
                              {note}
                            </p>
                          ))}
                          {r.status === "interrupted" && (
                            <div className="mb-2 text-[12px] text-tone-warn">
                              This run was interrupted (the daemon restarted
                              mid-run). The steps below show how far it got.
                              Resume continues from the first unfinished step.
                            </div>
                          )}
                          {r.status === "waiting" && (
                            <div className="mb-3">
                              <p className="text-[13px] text-tone-warn">
                                {parseWaiting(r)?.question ??
                                  "This run is waiting for your answer."}
                              </p>
                              <div className="mt-1.5 flex items-center gap-1.5">
                                <input
                                  value={answerDraft[String(r.id)] ?? ""}
                                  onChange={(e) =>
                                    setAnswerDraft((d) => ({
                                      ...d,
                                      [String(r.id)]: e.target.value,
                                    }))
                                  }
                                  onKeyDown={(e) => {
                                    if (e.key === "Enter")
                                      void submitAnswer(String(r.id));
                                  }}
                                  onClick={(e) => e.stopPropagation()}
                                  placeholder="Type your answer. The run continues from here."
                                  aria-label="Answer the workflow"
                                  className="field flex-1 py-1.5 text-[13px]"
                                />
                                <button
                                  type="button"
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    void submitAnswer(String(r.id));
                                  }}
                                  disabled={
                                    answeringId === String(r.id) ||
                                    !(answerDraft[String(r.id)] ?? "").trim()
                                  }
                                  className={WF_GHOST_ON_SM}
                                >
                                  {answeringId === String(r.id)
                                    ? "Sending…"
                                    : "Answer"}
                                </button>
                              </div>
                              {answerErr && (
                                <p className="mt-1 text-[12px] text-tone-danger">
                                  {answerErr}
                                </p>
                              )}
                            </div>
                          )}
                          <ol className="divide-y divide-white/[0.06]">
                            {defs.map((d, di) => {
                              const nm = d.name?.trim() || `step-${di + 1}`;
                              const o = outs[nm];
                              const st = o?.status ?? "pending";
                              const failed = st === "failed";
                              return (
                                <li
                                  key={`${nm}-${di}`}
                                  className="py-2"
                                >
                                  <div className="flex flex-wrap items-center gap-2">
                                    <span className="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-white/[0.05] text-[11px] font-medium text-zinc-400">
                                      {di + 1}
                                    </span>
                                    <span className="text-[13px] font-medium text-zinc-100">
                                      {nm}
                                    </span>
                                    {(d.kind ?? "agent") === "agent" &&
                                      d.agent && (
                                        <span className="text-[11px] text-zinc-500">
                                          · {d.agent}
                                        </span>
                                      )}
                                    {stepKindHint(d) && (
                                      <span className="text-[11px] text-zinc-500">
                                        · {stepKindHint(d)}
                                      </span>
                                    )}
                                    <Badge value={st} />
                                  </div>
                                  {o?.summary && (
                                    <p
                                      className={`mt-1.5 whitespace-pre-wrap text-[12px] leading-relaxed ${
                                        failed ? "text-tone-danger" : "text-zinc-400"
                                      }`}
                                    >
                                      {o.summary}
                                    </p>
                                  )}
                                </li>
                              );
                            })}
                          </ol>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </CalmSection>
  );
}
