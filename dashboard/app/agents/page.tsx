"use client";

// Agents — organized around the ROUND-TABLE: persistent threads where agents
// from different sources (built-in, yours, remote — including agents on other
// computers) sit on a panel, each with a role, and answer in turn — seeing
// each other's replies. The management surfaces (create dynamic agents,
// connect remote ones) collapse into the "Set up agents" card; the
// round-table is the star.

import { useEffect, useRef, useState } from "react";
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
  participantKey,
  type AgentSource,
  type Participant,
  type RemoteAgentInfo,
  type ThreadDetail,
  type ThreadRow,
} from "@/components/agents/identity";
import { SetupCard, type DynamicAgentFull } from "@/components/agents/SetupCard";
import { RosterStrip, type RosterEntry } from "@/components/agents/RosterStrip";
import { JobPostCard, type JobAssign } from "@/components/agents/JobPostCard";
import {
  PanelPicker,
  type PickerCatalog,
  type PickerOption,
} from "@/components/agents/PanelPicker";
import { RoundTable } from "@/components/agents/RoundTable";

type PickerState =
  | { mode: "create" }
  | { mode: "edit"; thread: ThreadDetail }
  | null;

/** "custom:slug" / "remote:name" → the bare registry name the thread routes
 *  accept (clean_participants stores source + bare name; the round engine
 *  looks the bare name up in the matching registry). Builtins pass through. */
function bareRosterName(name: string): string {
  if (name.startsWith("custom:")) return name.slice("custom:".length);
  if (name.startsWith("remote:")) return name.slice("remote:".length);
  return name;
}

/** A sensible thread title when none is typed — named after the panel. */
function defaultTitle(participants: Participant[]): string {
  const names = participants.map((p) => p.name);
  if (names.length === 0) return "";
  if (names.length === 1) return `Talk with ${names[0]}`;
  if (names.length === 2) return `${names[0]} & ${names[1]}`;
  if (names.length === 3) return `${names[0]}, ${names[1]} & ${names[2]}`;
  return `${names[0]}, ${names[1]} & ${names.length - 2} more`;
}

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
  // The roster (v1.139.0) also feeds the participant picker: descriptions +
  // live remote health. Older daemons 404 here — the picker falls back to the
  // raw /agents + /agents/remote lists below.
  const { data: rosterData } = useApi<{ roster?: RosterEntry[] }>("/agents/roster");

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
  // An older daemon without the thread routes: the round-table section simply
  // doesn't exist rather than erroring over a feature the daemon predates.
  const threadsMissing =
    threadsError?.status === 404 || threadsError?.status === 405;

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detailNonce, setDetailNonce] = useState(0);
  const [picker, setPicker] = useState<PickerState>(null);
  const [railError, setRailError] = useState<string | null>(null);
  // Talk-button failures surface here — the rail may not exist yet to show them.
  const [tableError, setTableError] = useState<string | null>(null);
  // Locally-deleted ids, hidden until the poll catches up (ids never reuse).
  const [hidden, setHidden] = useState<Set<string>>(() => new Set());
  // A just-created thread, shown in the rail before the poll includes it.
  const [justCreated, setJustCreated] = useState<ThreadRow | null>(null);
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);
  const tableRef = useRef<HTMLDivElement>(null);
  const talkBusyRef = useRef(false);
  // The job-post card (v1.166.0): the roster's "Give work" preselects a
  // target there and scrolls the card into view.
  const jobRef = useRef<HTMLDivElement>(null);
  const [assign, setAssign] = useState<JobAssign | null>(null);

  const polled = (threadsData?.threads ?? []).filter((t) => !hidden.has(t.id));
  const threads =
    justCreated && !polled.some((t) => t.id === justCreated.id)
      ? [justCreated, ...polled]
      : polled;
  const threadsReady = threadsData !== null || threadsError !== null;

  /**
   * `/agents?thread=<id>` opens THAT round-table (v1.142.0).
   *
   * The palette's "In your conversations" lane sends round-table hits here.
   * Without this the page just auto-selected the newest thread, so clicking a
   * search result for one conversation silently opened a DIFFERENT one — the
   * worst kind of wrong, because nothing on screen says so.
   *
   * Read off window.location, not useSearchParams: /agents is a static route
   * and useSearchParams would force it behind a Suspense boundary (the same
   * reason app/reflex, app/schedules and app/terminals read params this way).
   * An id that no longer exists is not special-cased: RoundTable already
   * renders a missing thread honestly, which is the whole point — showing a
   * DIFFERENT thread is the failure, showing "gone" is an answer.
   *
   * The ref is a three-state handshake with the auto-select below, because
   * both are mount effects and "whichever setState lands second wins" is not
   * a design: `undefined` = the URL has not been read yet, a string = a deep
   * link is claiming the selection, `null` = nobody is, carry on as before.
   */
  const deepLinkRef = useRef<string | null | undefined>(undefined);
  useEffect(() => {
    let wanted: string | null = null;
    try {
      wanted = new URLSearchParams(window.location.search).get("thread");
    } catch {
      /* a malformed query string is no deep link, not a broken page */
    }
    deepLinkRef.current = wanted;
    if (wanted) setSelectedId(wanted);
  }, []);

  // Auto-select the most recent thread so the star of the page is never blank.
  useEffect(() => {
    if (deepLinkRef.current === undefined) return; // the URL gets first refusal
    if (deepLinkRef.current) {
      // A deep link owns THIS pass — without the skip, an already-loaded rail
      // lets both effects fire in the same commit and the newest thread wins.
      // Consumed once, so deleting the deep-linked thread later hands the
      // choice straight back to the auto-select.
      deepLinkRef.current = null;
      return;
    }
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
      title: title.trim() || defaultTitle(participants),
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
    return res;
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

  /** The roster's Talk button: open the existing 1:1 thread with exactly this
   *  agent, or start one ("Talk with <name>"), then jump to the round-table. */
  async function talkWith(kind: AgentSource, name: string) {
    if (talkBusyRef.current) return;
    const key = participantKey(kind, name);
    const existing = threads.find(
      (t) => t.participants.length === 1 && t.participants[0]?.key === key,
    );
    setTableError(null);
    if (existing) {
      setSelectedId(existing.id);
      tableRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
      return;
    }
    talkBusyRef.current = true;
    try {
      await createThread(`Talk with ${name}`, [{ key, source: kind, name, role: "" }]);
      tableRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (e) {
      setTableError(e instanceof ApiError ? e.message : String(e));
    } finally {
      talkBusyRef.current = false;
    }
  }

  /** The roster's Give-work button: preselect this agent in the job-post card
   *  and bring the card into view. The nonce keeps a repeat click on the same
   *  agent a distinct assign, so it still re-selects after a manual change. */
  function assignWork(kind: AgentSource, name: string) {
    setAssign({ kind, name, nonce: Date.now() });
    jobRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // --- the participant picker's catalog ------------------------------------
  // Roster-fed when available: descriptions + live health, offline remotes
  // shown but disabled. Roster names arrive as "builder" / "custom:<slug>" /
  // "remote:<name>"; the thread routes want source + BARE name (verified
  // against agents/threads.py clean_participants + participant_key).
  const rosterEntries = (rosterData?.roster ?? []).filter(
    (e): e is RosterEntry => Boolean(e) && typeof e.name === "string",
  );
  const rosterOptions = (kind: AgentSource): PickerOption[] =>
    rosterEntries
      .filter((e) => e.kind === kind)
      .map((e) => ({
        source: kind,
        name: bareRosterName(e.name),
        description: e.description || undefined,
        offline: kind === "remote" && !e.healthy,
      }));
  const catalog: PickerCatalog =
    rosterEntries.length > 0
      ? {
          builtin: rosterOptions("builtin"),
          dynamic: rosterOptions("dynamic"),
          remotes: rosterOptions("remote"),
        }
      : {
          builtin: builtin.map((name) => ({ source: "builtin" as const, name })),
          dynamic: dynamic.map((a) => ({
            source: "dynamic" as const,
            name: a.name,
            description: a.description || undefined,
          })),
          remotes: remotes.map((r) => ({
            source: "remote" as const,
            name: r.name,
            description: r.kind || undefined,
            offline: r.enabled === false,
          })),
        };

  return (
    <PageShell>
      <Reveal>
        <PageHeader
          title="Agents"
          subtitle="Assemble a round-table of agents — built-in, yours, and agents on other computers — give each a role, and talk it out together."
          actions={
            !threadsMissing ? (
              <button
                type="button"
                onClick={() => setPicker({ mode: "create" })}
                className="btn-accent"
              >
                <Plus size={14} /> New thread
              </button>
            ) : undefined
          }
        />
      </Reveal>

      {offline && (
        <Reveal>
          <OfflineHint />
        </Reveal>
      )}

      {/* Give work (v1.166.0) — post a job to the Team (a supervisor session
          that plans & delegates) or straight to one delegable roster agent.
          Dispatched sessions carry origin "job:agents" and list in the card. */}
      <Reveal>
        <div ref={jobRef}>
          <JobPostCard roster={rosterEntries} assign={assign} />
        </div>
      </Reveal>

      {/* Setup — collapsed by default; the round-table below is the star. */}
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

      {/* Roster (v1.139.0) — who can take delegated work. Renders nothing on
          daemons that predate GET /agents/roster (it carries its own Reveal,
          so hiding leaves no empty gap). The Talk button needs the thread
          routes, so it's only offered when they exist. */}
      <RosterStrip
        onTalk={threadsMissing ? undefined : talkWith}
        onAssign={assignWork}
      />

      {tableError && (
        <Reveal>
          <ErrorNote>{tableError}</ErrorNote>
        </Reveal>
      )}

      {/* The round-table (hidden entirely on daemons without the thread routes) */}
      {!threadsMissing && (
        <Reveal>
          <div ref={tableRef}>
            {!threadsReady ? (
              <Card>
                <SkeletonRows rows={4} />
              </Card>
            ) : threadsData === null ? (
              // Errored before any data — never fake an empty list. Offline
              // shows the hint at the top; other failures get an honest note.
              threadsError && threadsError.status !== 0 ? (
                <ErrorNote>{threadsError.message}</ErrorNote>
              ) : null
            ) : threads.length === 0 ? (
              <Card>
                <Empty icon={<MessagesSquare size={26} />}>
                  <span className="mb-1 block text-sm font-medium text-zinc-300">
                    The round-table is empty
                  </span>
                  Start a thread and pick which agents sit at the table — a
                  planner, your own skeptic, and an agent on another computer
                  can all talk it out.
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
                      Round-table · {threads.length}
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
                    <RoundTable
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
          </div>
        </Reveal>
      )}

      {/* New-thread / edit-panel modal */}
      {picker && (
        <PanelPicker
          mode={picker.mode}
          catalog={catalog}
          initialParticipants={picker.mode === "edit" ? picker.thread.participants : []}
          onClose={() => setPicker(null)}
          onSubmit={
            picker.mode === "create"
              ? async (title, participants) => {
                  await createThread(title, participants);
                }
              : (_title, participants) => savePanel(picker.thread.id, participants)
          }
        />
      )}
    </PageShell>
  );
}
