"use client";

import { useEffect, useRef, useState } from "react";
import {
  FolderKanban,
  History,
  MessageSquare,
  Send,
  Sparkles,
  Loader2,
  Bot,
  User,
} from "lucide-react";
import { useApi } from "@/lib/useApi";
import { useDaemon } from "@/lib/daemon";
import { useEvents } from "@/lib/useEvents";
import { post, ApiError } from "@/lib/api";
import type { WorkflowRun } from "@/lib/types";
import { Card, Badge, Empty, SkeletonRows } from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";
import WorkflowCanvas from "@/components/workflow/WorkflowCanvas";
import { timeAgo } from "@/lib/format";

/** Context spine: which project workflow runs are stamped into right now. */
function ActiveProjectChip() {
  const { health } = useDaemon();
  const project = health?.active_project ?? null;
  if (!project) return null;
  return (
    <span
      title={`Working in ${project.name} — runs are tagged to this project. Switch it in the sidebar.`}
      className="inline-flex max-w-[12rem] items-center gap-1.5 rounded-xl border border-accent/25 bg-accent/[0.07] px-2.5 py-1.5 text-[12px] font-medium text-accent-soft"
    >
      <FolderKanban size={12} className="shrink-0" />
      <span className="truncate">{project.name}</span>
    </span>
  );
}

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
          actions={<ActiveProjectChip />}
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
  const threadRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    threadRef.current?.scrollTo({ top: threadRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, busy]);

  async function send(text: string) {
    const msg = text.trim();
    if (!msg || busy) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", content: msg }]);
    setBusy(true);
    try {
      const res = await post<{ name: string; description: string; steps: WfStep[]; reply: string }>(
        "/workflows/generate",
        { description: msg },
      );
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

/** Count the sessions a run spawned (`session_ids_json` is a JSON array string). */
function sessionCount(r: WorkflowRun): number {
  try {
    const arr = JSON.parse(String(r.session_ids_json ?? "[]"));
    return Array.isArray(arr) ? arr.length : 0;
  } catch {
    return 0;
  }
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
  const { data, error, loading, reload } = useApi<{ runs: WorkflowRun[] }>(
    "/workflows/runs",
  );

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
                return (
                  <tr
                    key={r.id ?? `${r.workflow_name}-${i}`}
                    className="border-b border-white/[0.04] last:border-0 hover:bg-white/[0.02]"
                  >
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
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
