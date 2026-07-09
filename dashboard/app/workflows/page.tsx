"use client";

import { Fragment, useEffect, useRef, useState } from "react";
import {
  History,
  MessageSquare,
  Send,
  Sparkles,
  Loader2,
  Bot,
  User,
  ChevronRight,
} from "lucide-react";
import { useApi } from "@/lib/useApi";
import { useEvents } from "@/lib/useEvents";
import { post, ApiError } from "@/lib/api";
import type { WorkflowRun } from "@/lib/types";
import { Card, Badge, Empty, SkeletonRows } from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";
import WorkflowCanvas from "@/components/workflow/WorkflowCanvas";
import { timeAgo } from "@/lib/format";

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

  return (
    <PageShell>
      <Reveal>
        <PageHeader
          title="Workflows"
          subtitle="Wire agents into a visual, multi-step workflow, then run it — describe one below, or send a terminal session here with its → Workflow button."
        />
      </Reveal>
      <Reveal>
        <WorkflowCanvas />
      </Reveal>
      <Reveal>
        <WorkflowBuilderChat />
      </Reveal>
      <Reveal>
        <RunHistory />
      </Reveal>
    </PageShell>
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

function WorkflowBuilderChat() {
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  // The workflow currently loaded in the editor (name + steps). Sent back on a
  // follow-up so /workflows/generate REFINES it instead of minting a new one.
  const currentRef = useRef<{ name: string; steps: WfStep[] } | null>(null);
  const threadRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    threadRef.current?.scrollTo({ top: threadRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, busy]);

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
    setInput("");
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
          reply = "I couldn't turn that into a workflow — try describing the steps more concretely.";
        else if (err.status === 0) reply = "The daemon looks offline — start it and try again.";
        else reply = err.message;
      }
      setMessages((m) => [...m, { role: "assistant", content: reply }]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Build with chat" icon={<Sparkles size={15} />}>
      <p className="mb-3 text-xs text-zinc-500">
        Describe a process and the agent builds the steps into the editor above — e.g.{" "}
        <span className="text-zinc-400">“research a topic, draft a summary, then review it.”</span>
      </p>

      <div
        ref={threadRef}
        className="mb-3 max-h-72 space-y-3 overflow-y-auto rounded-xl border border-white/[0.05] bg-ink-950/40 p-3"
      >
        {messages.length === 0 && !busy ? (
          <div className="space-y-2 py-2">
            <div className="flex items-center gap-2 text-xs text-zinc-500">
              <MessageSquare size={14} /> Try one of these:
            </div>
            <div className="flex flex-wrap gap-2">
              {EXAMPLES.map((ex) => (
                <button
                  key={ex}
                  onClick={() => send(ex)}
                  className="rounded-full border border-white/10 bg-white/[0.03] px-3 py-1.5 text-[11px] text-zinc-300 transition-colors hover:border-accent/40 hover:text-accent-soft"
                >
                  {ex}
                </button>
              ))}
            </div>
          </div>
        ) : (
          messages.map((m, i) => (
            <div
              key={i}
              className={`flex gap-2.5 ${m.role === "user" ? "flex-row-reverse" : ""}`}
            >
              <span
                className={`mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-lg ${
                  m.role === "user"
                    ? "bg-accent/15 text-accent-soft"
                    : "border border-white/10 bg-white/[0.03] text-zinc-400"
                }`}
              >
                {m.role === "user" ? <User size={13} /> : <Bot size={13} />}
              </span>
              <div
                className={`max-w-[80%] whitespace-pre-wrap rounded-xl px-3 py-2 text-[13px] leading-relaxed ${
                  m.role === "user"
                    ? "bg-accent/[0.1] text-zinc-100"
                    : "bg-white/[0.03] text-zinc-300"
                }`}
              >
                {m.content}
              </div>
            </div>
          ))
        )}
        {busy && (
          <div className="flex items-center gap-2 text-xs text-zinc-500">
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
        <button type="submit" disabled={busy || !input.trim()} className="btn-accent">
          {busy ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
          Build
        </button>
      </form>
    </Card>
  );
}

type StepDef = { name?: string; agent?: string };
type StepOut = {
  session_id?: string | null;
  status?: string;
  summary?: string;
  tool?: string | null;
};

/** The ordered step definitions the run was created with. */
function parseStepDefs(r: WorkflowRun): StepDef[] {
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

  // Refetch the moment a workflow finishes (the engine emits workflow.completed).
  const { events } = useEvents(50);
  const lastCompleted = events.find((e) => e.type === "workflow.completed");
  useEffect(() => {
    if (lastCompleted) reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lastCompleted?.id]);

  const offline = error && error.status === 0;
  const runs = data?.runs ?? [];
  // Newest first (records carry a started_at timestamp).
  const ordered = [...runs].sort((a, b) => {
    const ta = new Date(runTimestamp(a) ?? 0).getTime();
    const tb = new Date(runTimestamp(b) ?? 0).getTime();
    return tb - ta;
  });

  return (
    <Card
      title={`Run history${runs.length ? ` · ${runs.length}` : ""}`}
      icon={<History size={15} />}
    >
      {loading && !data ? (
        <SkeletonRows rows={4} />
      ) : offline ? (
        <Empty icon={<History size={22} />}>
          Daemon offline — run history is unavailable.
        </Empty>
      ) : ordered.length === 0 ? (
        <Empty icon={<History size={22} />}>
          No workflow runs yet. Run a workflow above to see it here.
        </Empty>
      ) : (
        <div className="-mx-1 overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b hairline text-[11px] uppercase tracking-[0.1em] text-zinc-400">
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
                      className={`border-b border-white/[0.04] last:border-0 hover:bg-white/[0.02] ${
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
                        <Badge value={r.status || "unknown"} />
                      </td>
                      <td className="px-2 py-2.5 text-zinc-400">
                        {n} session{n === 1 ? "" : "s"}
                      </td>
                      <td className="px-2 py-2.5 text-zinc-500">
                        {ts ? timeAgo(ts) : "—"}
                      </td>
                    </tr>
                    {isOpen && (
                      <tr className="border-b border-white/[0.04] bg-ink-950/30">
                        <td colSpan={5} className="px-3 py-2.5">
                          {r.status === "interrupted" && (
                            <div className="mb-2 text-[12px] text-amber-300/80">
                              This run was interrupted (the daemon restarted
                              mid-run) — steps below reflect how far it got.
                            </div>
                          )}
                          <ol className="space-y-1.5">
                            {defs.map((d, di) => {
                              const nm = d.name?.trim() || `step-${di + 1}`;
                              const o = outs[nm];
                              const st = o?.status ?? "pending";
                              const failed = st === "failed";
                              return (
                                <li
                                  key={`${nm}-${di}`}
                                  className="rounded-lg border border-white/[0.05] bg-white/[0.02] px-2.5 py-2"
                                >
                                  <div className="flex flex-wrap items-center gap-2">
                                    <span className="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-white/[0.05] text-[10px] font-semibold text-zinc-400">
                                      {di + 1}
                                    </span>
                                    <span className="text-[13px] font-medium text-zinc-100">
                                      {nm}
                                    </span>
                                    {d.agent && (
                                      <span className="text-[11px] text-zinc-500">
                                        · {d.agent}
                                      </span>
                                    )}
                                    <Badge value={st} />
                                  </div>
                                  {o?.summary && (
                                    <p
                                      className={`mt-1.5 whitespace-pre-wrap text-[12px] leading-relaxed ${
                                        failed ? "text-rose-200/90" : "text-zinc-400"
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
    </Card>
  );
}
