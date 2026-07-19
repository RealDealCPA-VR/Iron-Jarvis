"use client";

// Agents — organized around AGENT THREADS: persistent conversations where
// agents from different sources (built-in, yours, remote) sit on a panel,
// each with a role, and answer in turn — seeing each other's replies. The
// management surfaces (create dynamic agents, connect remote ones) collapse
// into the "Set up agents" card; the threads are the star.

import { useEffect, useState } from "react";
import { Check, MessagesSquare, Plus, Trash2 } from "lucide-react";
import { del, post, put, ApiError } from "@/lib/api";
import { useApi, usePolledApi } from "@/lib/useApi";
import type { AgentsResponse, ModelOption } from "@/lib/types";
import { Card, Empty, ErrorNote, OfflineHint, SkeletonRows } from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";
import { timeAgo } from "@/lib/format";
import {
  AvatarStack,
  type Participant,
  type RemoteAgentInfo,
  type ThreadDetail,
  type ThreadRow,
} from "@/components/agents/identity";
import { SetupCard, type DynamicAgentFull } from "@/components/agents/SetupCard";
import { PanelPicker } from "@/components/agents/PanelPicker";
import { ThreadView } from "@/components/agents/ThreadView";

type PickerState =
  | { mode: "create" }
  | { mode: "edit"; thread: ThreadDetail }
  | null;

export default function AgentsPage() {
  // --- catalog (also feeds the setup card + the panel picker) --------------
  const {
    data: agentsData,
    error: agentsError,
    reload: reloadAgents,
  } = useApi<AgentsResponse>("/agents");
  const { data: remoteData, reload: reloadRemotes } = useApi<{
    agents?: RemoteAgentInfo[];
    remotes?: RemoteAgentInfo[];
  }>("/agents/remote");
  const { data: modelsData } = useApi<{ models: ModelOption[] }>("/models");

  const builtin = agentsData?.builtin ?? [];
  const dynamic = (agentsData?.dynamic ?? []) as DynamicAgentFull[];
  const remotes = remoteData?.agents ?? remoteData?.remotes ?? [];
  const models = modelsData?.models ?? [];

  // --- threads (polled; `data` persists between ticks so nothing strobes) --
  const {
    data: threadsData,
    error: threadsError,
    reload: reloadThreads,
  } = usePolledApi<{ threads: ThreadRow[] }>("/agents/threads", 8000);

  const offline =
    threadsError?.status === 0 || agentsError?.status === 0 || false;

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detailNonce, setDetailNonce] = useState(0);
  const [picker, setPicker] = useState<PickerState>(null);
  const [railError, setRailError] = useState<string | null>(null);
  // Locally-deleted ids, hidden until the poll catches up (ids never reuse).
  const [hidden, setHidden] = useState<Set<string>>(() => new Set());
  // A just-created thread, shown in the rail before the poll includes it.
  const [justCreated, setJustCreated] = useState<ThreadRow | null>(null);
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);

  const polled = (threadsData?.threads ?? []).filter((t) => !hidden.has(t.id));
  const threads =
    justCreated && !polled.some((t) => t.id === justCreated.id)
      ? [justCreated, ...polled]
      : polled;
  const threadsReady = threadsData !== null || threadsError !== null;

  // Auto-select the most recent thread so the star of the page is never blank.
  useEffect(() => {
    if (selectedId === null && threads.length > 0) setSelectedId(threads[0].id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [threads.length, selectedId]);

  // Auto-disarm a pending rail delete after a moment.
  useEffect(() => {
    if (!pendingDelete) return;
    const t = setTimeout(() => setPendingDelete(null), 3000);
    return () => clearTimeout(t);
  }, [pendingDelete]);

  async function createThread(title: string, participants: Participant[]) {
    // Throws on failure — the picker shows the error inline.
    const res = await post<ThreadDetail>("/agents/threads", {
      title,
      participants: participants.map(({ source, name, role }) => ({ source, name, role })),
    });
    setJustCreated({
      id: res.id,
      title: res.title,
      participants: res.participants,
      message_count: res.message_count ?? 0,
      updated_at: res.updated_at,
    });
    setSelectedId(res.id);
    setPicker(null);
    reloadThreads();
  }

  async function savePanel(threadId: string, participants: Participant[]) {
    await put(`/agents/threads/${encodeURIComponent(threadId)}/participants`, {
      participants: participants.map(({ source, name, role }) => ({ source, name, role })),
    });
    setPicker(null);
    setDetailNonce((n) => n + 1); // refetch the open transcript with the new panel
    reloadThreads();
  }

  async function removeThread(id: string) {
    setPendingDelete(null);
    try {
      await del(`/agents/threads/${encodeURIComponent(id)}`);
      setHidden((prev) => new Set(prev).add(id));
      if (justCreated?.id === id) setJustCreated(null);
      if (selectedId === id) setSelectedId(null); // auto-select picks the next one
      setRailError(null);
      reloadThreads();
    } catch (e) {
      setRailError(e instanceof ApiError ? e.message : String(e));
    }
  }

  const catalog = { builtin, dynamic, remotes };

  return (
    <PageShell>
      <Reveal>
        <PageHeader
          title="Agents"
          subtitle="Assemble a panel of agents — built-in, yours, and remote — give each a role, and let them talk it out in persistent threads."
          actions={
            <button
              type="button"
              onClick={() => setPicker({ mode: "create" })}
              className="btn-accent"
            >
              <Plus size={14} /> New thread
            </button>
          }
        />
      </Reveal>

      {offline && (
        <Reveal>
          <OfflineHint />
        </Reveal>
      )}

      {/* Setup — collapsed by default; the threads below are the star. */}
      <Reveal>
        <SetupCard
          builtin={builtin}
          dynamic={dynamic}
          remotes={remotes}
          models={models}
          onAgentsChanged={reloadAgents}
          onRemotesChanged={reloadRemotes}
        />
      </Reveal>

      {/* The threads area */}
      <Reveal>
        {!threadsReady ? (
          <Card>
            <SkeletonRows rows={4} />
          </Card>
        ) : threadsData === null ? (
          // Errored before any data — never fake an empty list. Offline shows
          // the hint at the top; other failures get an honest error note.
          threadsError && threadsError.status !== 0 ? (
            <ErrorNote>{threadsError.message}</ErrorNote>
          ) : null
        ) : threads.length === 0 ? (
          <Card>
            <Empty icon={<MessagesSquare size={26} />}>
              <span className="mb-1 block text-sm font-medium text-zinc-300">
                No agent threads yet
              </span>
              Create a thread and pick which agents talk in it — a planner, your
              own skeptic, and a remote agent can all sit on one panel.
            </Empty>
            <div className="flex justify-center pb-2">
              <button
                type="button"
                onClick={() => setPicker({ mode: "create" })}
                className="btn-accent"
              >
                <Plus size={14} /> New thread
              </button>
            </div>
          </Card>
        ) : (
          <div className="grid items-start gap-4 md:grid-cols-[16rem_minmax(0,1fr)]">
            {/* Thread rail */}
            <Card pad={false} className="overflow-hidden">
              <div className="flex items-center justify-between border-b hairline px-3 py-2">
                <span className="text-[11px] font-medium uppercase tracking-wide text-zinc-500">
                  Threads · {threads.length}
                </span>
                <button
                  type="button"
                  onClick={() => setPicker({ mode: "create" })}
                  className="btn-ghost px-2 py-1 text-[12px]"
                  title="Start a new agent thread"
                >
                  <Plus size={13} /> New
                </button>
              </div>
              <div className="max-h-[70vh] space-y-0.5 overflow-y-auto p-1.5">
                {railError && <ErrorNote>{railError}</ErrorNote>}
                {threads.map((t) => {
                  const active = t.id === selectedId;
                  return (
                    <div
                      key={t.id}
                      className={`group/thread relative rounded-xl border transition-colors ${
                        active
                          ? "border-accent/25 bg-accent/[0.08]"
                          : "border-transparent hover:bg-white/[0.04]"
                      }`}
                    >
                      <button
                        type="button"
                        onClick={() => setSelectedId(t.id)}
                        className="w-full px-2.5 py-2 pr-8 text-left"
                        title={t.title || "Agent thread"}
                      >
                        <span
                          className={`block truncate text-[13px] ${
                            active ? "text-accent-soft" : "text-zinc-200"
                          }`}
                        >
                          {t.title || "Agent thread"}
                        </span>
                        <span className="mt-1.5 flex items-center gap-2">
                          <AvatarStack participants={t.participants} size="sm" />
                          <span className="text-[11px] text-zinc-500">
                            {t.message_count} msg{t.message_count === 1 ? "" : "s"} ·{" "}
                            {timeAgo(t.updated_at)}
                          </span>
                        </span>
                      </button>
                      {pendingDelete === t.id ? (
                        <button
                          type="button"
                          onClick={() => void removeThread(t.id)}
                          aria-label="Confirm delete"
                          title="Click again to delete"
                          className="absolute right-1.5 top-1/2 grid h-6 w-6 -translate-y-1/2 place-items-center rounded-md bg-rose-500/15 text-rose-300"
                        >
                          <Check size={13} />
                        </button>
                      ) : (
                        <button
                          type="button"
                          onClick={() => setPendingDelete(t.id)}
                          aria-label={`Delete ${t.title || "thread"}`}
                          title="Delete this thread"
                          className="absolute right-1.5 top-1/2 grid h-6 w-6 -translate-y-1/2 place-items-center rounded-md text-zinc-500 opacity-0 transition-opacity hover:bg-white/[0.06] hover:text-rose-300 focus-visible:opacity-100 group-hover/thread:opacity-100"
                        >
                          <Trash2 size={13} />
                        </button>
                      )}
                    </div>
                  );
                })}
              </div>
            </Card>

            {/* Conversation */}
            <div className="min-w-0">
              {selectedId ? (
                <ThreadView
                  threadId={selectedId}
                  reloadNonce={detailNonce}
                  onEditPanel={(detail) => setPicker({ mode: "edit", thread: detail })}
                  onRoundDone={reloadThreads}
                />
              ) : (
                <Card>
                  <Empty icon={<MessagesSquare size={22} />}>
                    Pick a thread from the rail — or start a new one.
                  </Empty>
                </Card>
              )}
            </div>
          </div>
        )}
      </Reveal>

      {/* New-thread / edit-panel modal */}
      {picker && (
        <PanelPicker
          mode={picker.mode}
          catalog={catalog}
          initialParticipants={picker.mode === "edit" ? picker.thread.participants : []}
          onClose={() => setPicker(null)}
          onSubmit={
            picker.mode === "create"
              ? createThread
              : (_title, participants) => savePanel(picker.thread.id, participants)
          }
        />
      )}
    </PageShell>
  );
}
