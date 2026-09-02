"use client";

// Agents — organized around the ROUND-TABLE: persistent threads where agents
// from different sources (built-in, yours, remote — including agents on other
// computers) sit on a panel, each with a role, and answer in turn — seeing
// each other's replies.
//
// v1.178.0 — A ROOM, NOT A FORM. The roster became a persistent left rail of
// faces and everything else — give work, setup, the round-table — the module
// beside it.
// v1.179.0 — THE THREAD IS THE PAGE. The give-work form and the setup card
// moved behind their own doors (a disclosure, and the rail's gear).
// v1.180.0 — ONE COLUMN, AND TALK *IS* WORK. The page became a stack, the
// roster folded, and dispatch moved into the thread composer — a job posted
// from a form and a thread started with an agent were two front doors to one
// intent.
// v1.184.0 — the roster moved INTO the conversation's grid cell so the two
// cards shared an edge.
//
// v1.214.0 — THE LEFT CARD IS THE THREADS, AND CONFIGURATION IS A DIALOG.
// Reported verbatim: the add-agent "+" popup "is bound by the size of the
// thread (chat window) and on a small card doesn't show everthing from this
// pop up"; the left pane should be "a new fixed full lenth and scrollable left
// card that is the height length of the app below the very top pane"; the
// roster and new-agent controls should collapse into "a small icon button on
// the bottom left" that opens "a modal pop up [where] the user [can] configure
// as desired"; "every agent should be customizable including the predefined
// agents"; and the pane should show "the image of the related agent or agents
// (layered as they are now)".
//
// FOUR CHANGES, and the first one is a real bug rather than a layout taste:
//
//   1. THE POPUP WAS NOT ACTUALLY FIXED TO THE VIEWPORT. `PanelPicker` is
//      `fixed inset-0`, but it is rendered from INSIDE `RoundTable`, whose
//      root is `<div class="card-surface … overflow-hidden">` — and
//      `.card-surface` carries `backdrop-filter: blur(18px)`. A non-`none`
//      backdrop-filter makes an element the CONTAINING BLOCK for fixed-position
//      descendants, so `inset-0` resolved to the thread card and
//      `overflow-hidden` clipped the rest. Every dialog in this module now goes
//      through `components/Modal.tsx`, which portals to `document.body`; the
//      full diagnosis lives in that file's header.
//
//   2. THE LEFT CARD IS THE THREAD LIST, full height, scrolling itself. The
//      roster used to be the rail and the threads a SECOND 16rem rail beside
//      the conversation. Two rails for one module, and the thing a user picks
//      dozens of times a day — a conversation — was in the narrower one.
//
//   3. THE ROSTER IS BEHIND THE ICON at the foot of that card, in
//      `AgentsModal`: who exists, what each one looks like, Talk / Give work,
//      and the create/connect surfaces `SetupCard` has always held. A dialog
//      is not bounded by the column it was revealed into, which is exactly
//      what went wrong with the old inline reveal.
//
//   4. EVERY AGENT IS CUSTOMIZABLE, built-ins included — portrait AND face.
//      The daemon never restricted portraits by kind (storage is
//      `avatars/<slug>.png` by name); one component's location did.
//
// THE OLDER-DAEMON PAGE IS UNTOUCHED. A daemon that serves no
// `/agents/roster`, or no thread routes at all, still gets the stacked
// composition with `RosterStrip`, `JobPostCard` and `SetupCard` in the flow —
// hiding them there would delete capabilities from the daemons least able to
// spare them.

import { useEffect, useMemo, useRef, useState } from "react";
import { MessagesSquare } from "lucide-react";
import { del, post, put, ApiError } from "@/lib/api";
import { useApi, usePolledApi } from "@/lib/useApi";
import type { AgentsResponse, ModelOption } from "@/lib/types";
import { Card, Empty, ErrorNote, OfflineHint, SkeletonRows } from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";
import {
  participantKey,
  type AgentSource,
  type Participant,
  type RemoteAgentInfo,
  type ThreadDetail,
  type ThreadRow,
} from "@/components/agents/identity";
import { SetupCard, type DynamicAgentFull } from "@/components/agents/SetupCard";
import {
  RosterStrip,
  rosterAvatarSrc,
  type RosterEntry,
} from "@/components/agents/RosterStrip";
import { ThreadRail } from "@/components/agents/ThreadRail";
import { AgentsModal } from "@/components/agents/AgentsModal";
import {
  JobPostCard,
  TEAM_TARGET,
  type JobAssign,
} from "@/components/agents/JobPostCard";
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

/**
 * SetupCard's disclosure key — read and written HERE, and nowhere else.
 *
 * TWO STATES, ONE OWNER EACH (v1.185.0): `setupOpen` is "is the card on screen
 * this visit" (the gear's answer, deliberately not persisted) and
 * `setupExpanded` is "is its body disclosed" (the card's own chevron, the only
 * half worth remembering across visits).
 *
 * BOTH NOW BELONG TO THE OLDER-DAEMON PATH ONLY (v1.214.0). On a daemon that
 * serves the roster, configuration is a dialog and has no persisted open
 * state at all — a modal that reopened itself because it was open last week
 * would be a page that starts by interrupting you.
 */
const SETUP_OPEN_KEY = "ij_agents_setup_open";

/** "custom:slug" / "remote:name" → the bare registry name the thread routes
 *  accept (clean_participants stores source + bare name; the round engine
 *  looks the bare name up in the matching registry). Builtins pass through. */
function bareRosterName(name: string): string {
  if (name.startsWith("custom:")) return name.slice("custom:".length);
  if (name.startsWith("remote:")) return name.slice("remote:".length);
  return name;
}

/** What the module is, said once. The room shows it inside the thread rail
 *  (v1.214.3); the older-daemon page still shows it through `PageHeader`. Two
 *  copies of this sentence would be two chances for them to drift. */
const AGENTS_HINT =
  "Assemble a round-table of agents — built-in, yours, and agents on other computers — give each a role, and talk it out together.";

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
  // --- catalog (also feeds the agents room + the panel picker) -------------
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
  // The roster (v1.139.0) feeds the agents room, the participant picker, and
  // the portraits the thread rail layers. Older daemons 404 here — the room is
  // not offered at all and the page falls back to its pre-rail composition.
  const { data: rosterData, reload: reloadRoster } =
    useApi<{ roster?: RosterEntry[] }>("/agents/roster");

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
  // WHO TAKES THE WORK — the THREAD COMPOSER's dispatch target since v1.180.0,
  // because a thread with an agent IS how a job starts now. `jobRef` is the
  // older-daemon path's scroll target and nothing else.
  const jobRef = useRef<HTMLDivElement>(null);
  const [assign, setAssign] = useState<JobAssign | null>(null);
  // THE AGENTS ROOM (v1.214.0). Not persisted, on purpose — see SETUP_OPEN_KEY.
  const [agentsOpen, setAgentsOpen] = useState(false);
  // The older-daemon setup card's two states (see SETUP_OPEN_KEY).
  const setupRef = useRef<HTMLDivElement>(null);
  const [setupOpen, setSetupOpen] = useState(false);
  const [setupExpanded, setSetupExpanded] = useState(true);
  useEffect(() => {
    try {
      setSetupExpanded(localStorage.getItem(SETUP_OPEN_KEY) !== "0");
    } catch {
      /* storage unavailable — the default stands */
    }
  }, []);
  // WHO THE USER IS WORKING WITH (v1.178.0). This lives on the page, not
  // inside the roster, because it drives the page: the composer's dispatch
  // target and which thread is open. Kept as kind + BARE name, the shape every
  // handler here already speaks (participantKey, JobAssign, talkWith).
  const [picked, setPicked] = useState<{ kind: AgentSource; name: string } | null>(
    null,
  );

  const polled = (threadsData?.threads ?? []).filter((t) => !hidden.has(t.id));
  const threads =
    justCreated && !polled.some((t) => t.id === justCreated.id)
      ? [justCreated, ...polled]
      : polled;
  const threadsReady = threadsData !== null || threadsError !== null;

  const rosterEntries = (rosterData?.roster ?? []).filter(
    (e): e is RosterEntry => Boolean(e) && typeof e.name === "string",
  );
  const hasRoster = rosterEntries.length > 0;
  /**
   * THE COMPOSITION THIS RELEASE INTRODUCES, and the one condition that
   * decides it. The full-height rail IS the thread list, so it needs the
   * thread routes to have anything in it; the agents room reads the roster, so
   * it needs that. Missing either one and the page renders exactly what the
   * daemon that lacks it has always been given.
   */
  const room = hasRoster && !threadsMissing;

  /**
   * participantKey → the portrait the roster serves for that agent.
   *
   * Built HERE from the page's own roster rows rather than fetched inside the
   * rail: the two would otherwise be independent answers to "what does this
   * agent look like", and the one in the narrower column would be the one that
   * went stale. `last_active` is the cache key the roster already uses, so a
   * replaced portrait stops being served from the browser cache.
   */
  const avatarByKey = useMemo(() => {
    const map = new Map<string, string | null>();
    for (const e of rosterEntries) {
      map.set(
        participantKey(e.kind, bareRosterName(e.name)),
        e.avatar ? rosterAvatarSrc(e.avatar, e.last_active) : null,
      );
    }
    return map;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rosterData]);

  /**
   * `/agents?thread=<id>` opens THAT round-table (v1.142.0).
   *
   * Read off window.location, not useSearchParams: /agents is a static route
   * and useSearchParams would force it behind a Suspense boundary. The ref is
   * a three-state handshake with the auto-select below, because both are mount
   * effects and "whichever setState lands second wins" is not a design:
   * `undefined` = the URL has not been read yet, a string = a deep link is
   * claiming the selection, `null` = nobody is.
   */
  const deepLinkRef = useRef<string | null | undefined>(undefined);
  // `/agents?talk=<builtin>&ask=<text>` (v1.224.0): open (or start) the 1:1
  // thread with a built-in agent and prefill the composer — the Help page's
  // "Ask the Guide" lands here with talk=guide. Read at mount; acted on once
  // the thread list has answered (Talk needs it to find an existing 1:1).
  // The params are stripped after use so a refresh does not re-open.
  const [pendingTalk, setPendingTalk] = useState<string | null>(null);
  const [pendingAsk, setPendingAsk] = useState<string>("");
  useEffect(() => {
    let wanted: string | null = null;
    try {
      const params = new URLSearchParams(window.location.search);
      wanted = params.get("thread");
      const talk = (params.get("talk") || "").trim();
      const ask = (params.get("ask") || "").trim();
      if (talk) setPendingTalk(talk);
      if (ask) setPendingAsk(ask);
      if (talk || ask) {
        const url = new URL(window.location.href);
        url.searchParams.delete("talk");
        url.searchParams.delete("ask");
        window.history.replaceState(null, "", url.toString());
      }
    } catch {
      /* a malformed query string is no deep link, not a broken page */
    }
    deepLinkRef.current = wanted;
    if (wanted) setSelectedId(wanted);
  }, []);
  // Act on ?talk= once the thread list has answered: Talk reuses an existing
  // 1:1 thread when there is one, which it can only know from the list.
  useEffect(() => {
    if (!pendingTalk || !threadsData) return;
    const name = pendingTalk;
    setPendingTalk(null);
    void talkWith("builtin", name);
    // talkWith is a stable function declaration in this component.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingTalk, threadsData]);

  // Auto-select the most recent thread so the star of the page is never blank.
  useEffect(() => {
    if (deepLinkRef.current === undefined) return; // the URL gets first refusal
    if (deepLinkRef.current) {
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

  /** The one 1:1 thread whose panel is EXACTLY this agent, if it exists.
   *  Extracted (v1.178.0) so "continue working with" and the Talk button ask
   *  the same question — two copies of this predicate would be two chances to
   *  open a thread with somebody else on the panel. */
  function soloThreadWith(kind: AgentSource, name: string) {
    const key = participantKey(kind, name);
    return threads.find(
      (t) => t.participants.length === 1 && t.participants[0]?.key === key,
    );
  }

  /** Talk: open the existing 1:1 thread with exactly this agent, or start one
   *  ("Talk with <name>"), then bring the round-table into view. */
  async function talkWith(kind: AgentSource, name: string) {
    if (talkBusyRef.current) return;
    const key = participantKey(kind, name);
    const existing = soloThreadWith(kind, name);
    setTableError(null);
    if (existing) {
      setSelectedId(existing.id);
      // The room is a dialog over the page: leaving it open on top of the
      // thread it just opened would hide the thing the click asked for.
      setAgentsOpen(false);
      tableRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
      return;
    }
    talkBusyRef.current = true;
    try {
      await createThread(`Talk with ${name}`, [{ key, source: kind, name, role: "" }]);
      setAgentsOpen(false);
      tableRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (e) {
      setTableError(e instanceof ApiError ? e.message : String(e));
    } finally {
      talkBusyRef.current = false;
    }
  }

  /**
   * Give work (v1.180.0): OPEN THE THREAD with this agent, with the work armed.
   *
   * It arms the composer's dispatch target (the nonce keeps a repeat click on
   * the same agent a distinct assign, so it still lands after a manual change)
   * and then does exactly what Talk does. Where there is no composer to arm —
   * an older daemon with no roster, or one without the thread routes —
   * JobPostCard is still standing in the flow, so that path keeps the v1.179.0
   * behaviour: preselect in the card and scroll to it. Both branches read the
   * SAME condition the card is rendered under, or Give-work would scroll to a
   * ref pointing at nothing.
   */
  function assignWork(kind: AgentSource, name: string) {
    setAssign({ kind, name, nonce: Date.now() });
    if (!room) {
      setAgentsOpen(false);
      jobRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
      return;
    }
    void talkWith(kind, name);
  }

  /**
   * An agent was picked: "this is who I'm working with".
   *
   * Two effects, both things the user can already do by hand — the click just
   * stops making them do it: the composer's target becomes this agent, and an
   * existing 1:1 thread with exactly this agent opens. Nothing is CREATED —
   * that stays behind Talk, because a POST is not what selecting a portrait
   * promises.
   *
   * ADVERSARIAL REVIEW (v1.178.0): the `canWork` guard alone left a STALE
   * target behind — picking an offline remote after the analyst moved the
   * highlight while the composer still aimed at the analyst. So an un-workable
   * pick doesn't merely skip the preselect, it RESETS the target to the Team,
   * which is also the honest answer: the supervisor is non-delegable and an
   * offline remote cannot take a session.
   */
  function selectAgent(kind: AgentSource, name: string, canWork: boolean) {
    setPicked({ kind, name });
    setAssign(
      canWork
        ? { kind, name, nonce: Date.now() }
        : { kind: "builtin", name: TEAM_TARGET, nonce: Date.now() },
    );
    const existing = soloThreadWith(kind, name);
    if (existing) {
      setTableError(null);
      setSelectedId(existing.id);
    }
  }

  /** The older-daemon gear: reveal the setup card, and fold it again. */
  function toggleSetup() {
    const next = !setupOpen;
    setSetupOpen(next);
    if (next) setSetupExpanded(true);
  }

  /** That card's own chevron — the half remembered across visits. */
  function setSetupDisclosure(open: boolean) {
    setSetupExpanded(open);
    try {
      localStorage.setItem(SETUP_OPEN_KEY, open ? "1" : "0");
    } catch {
      /* persistence is best-effort; the card on screen does not depend on it */
    }
  }

  // Focus and the viewport follow the older-daemon reveal — otherwise a
  // keyboard user activates the gear and their focus is still in the roster, a
  // section away from the form that just appeared. (The room needs none of
  // this: a dialog takes focus by being a dialog.)
  useEffect(() => {
    if (!setupOpen) return;
    const host = setupRef.current;
    host?.scrollIntoView({ behavior: "smooth", block: "start" });
    host?.querySelector<HTMLButtonElement>("button[aria-expanded]")?.focus();
  }, [setupOpen]);

  // --- the participant picker's catalog ------------------------------------
  // Roster-fed when available: descriptions + live health, offline remotes
  // shown but disabled. Roster names arrive as "builder" / "custom:<slug>" /
  // "remote:<name>"; the thread routes want source + BARE name.
  const rosterOptions = (kind: AgentSource): PickerOption[] =>
    rosterEntries
      .filter((e) => e.kind === kind)
      .map((e) => ({
        source: kind,
        name: bareRosterName(e.name),
        description: e.description || undefined,
        offline: kind === "remote" && !e.healthy,
        avatar: e.avatar ?? null,
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

  /** Portraits and faces are written by name, and BOTH lists carry them: the
   *  roster feeds the rail and the room, `/agents` feeds the dynamic rows. A
   *  write refreshes both, or the rail keeps drawing the picture the room just
   *  replaced. */
  function agentsChanged() {
    reloadAgents();
    reloadRoster();
  }

  /**
   * ONE DOOR TO A NEW THREAD (v1.214.1). Reported: "in the agents module there
   * are 2 areas to start a new thread and it should be one."
   *
   * There were three. The header carried a "New thread" button, the thread
   * rail's own header carries "+ New", and the empty conversation panel
   * carried a third. The rail is the one that keeps it: starting a thread is a
   * LIST operation, its control belongs on the list, and the rail is on screen
   * at every width and every state — including the empty one, which is why the
   * panel's button could go without recreating the dead end v1.180.0 closed.
   * The panel now POINTS at the rail instead of duplicating it.
   */
  const header = (
    <PageHeader title="Agents" subtitle={AGENTS_HINT} />
  );

  const modals = (
    <>
      {/* New-thread / edit-panel. Portalled by `Modal` inside PanelPicker, so
          it is no longer clipped by whichever card it was rendered from. */}
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
      {/* The agents room. */}
      {agentsOpen && room && (
        <AgentsModal
          roster={rosterEntries}
          dynamic={dynamic}
          remotes={remotes}
          models={models}
          selected={picked}
          onSelect={selectAgent}
          onTalk={talkWith}
          onAssign={assignWork}
          onAgentsChanged={agentsChanged}
          onRemotesChanged={reloadRemotes}
          onClose={() => setAgentsOpen(false)}
        />
      )}
    </>
  );

  /* ---------------------------------------------------- the room (v1.214.0) */
  if (room) {
    const conversation = !threadsReady ? (
      <Card>
        <SkeletonRows rows={4} />
      </Card>
    ) : threadsData === null ? (
      // Errored before any data — never fake an empty list. Offline shows the
      // hint above; other failures get an honest note.
      threadsError && threadsError.status !== 0 ? (
        <ErrorNote>{threadsError.message}</ErrorNote>
      ) : null
    ) : selectedId ? (
      <RoundTable
        threadId={selectedId}
        reloadNonce={detailNonce}
        onEditPanel={(detail) => setPicker({ mode: "edit", thread: detail })}
        onRoundDone={reloadThreads}
        // DISPATCH LIVES IN THE COMPOSER (v1.180.0). The page keeps owning WHO
        // the user is working with — the room sets it, the thread acts on it —
        // so the composer is handed the same `assign` the job card used to
        // read, and the page's own roster rows so it never has to fetch a
        // second opinion about who can take work.
        roster={rosterEntries}
        assign={assign}
        initialInput={pendingAsk}
      />
    ) : (
      <Card>
        <Empty icon={<MessagesSquare size={22} />}>
          {threads.length === 0
            ? "The round-table is empty. Press New in the rail to start a thread and pick who sits at the table — a planner, your own skeptic, and an agent on another computer can all talk it out."
            : "Pick a thread from the rail — or press New there to start one."}
        </Empty>
      </Card>
    );

    return (
      <PageShell className="space-y-0">
        {/* THE MODULE FILLS THE APP. `md:h-[calc(100vh-4.5rem)]` is the title
            bar (2.5rem) plus MainContent's own `py-4` (2rem), so the row ends
            exactly where the window does and nothing but the two panes
            scrolls. Below md it is a plain column: a 17rem rail beside a
            transcript on a phone is two unusable columns, so the rail becomes
            a normal card above the conversation (with its own capped height —
            see ThreadRail). */}
        <div
          data-testid="agents-room"
          className="flex flex-col gap-4 md:h-[calc(100vh-4.5rem)] md:min-h-[28rem] md:flex-row"
        >
          <div className="shrink-0 md:h-full md:w-[17rem]">
            <ThreadRail
              // THE MODULE'S NAME LIVES HERE NOW (v1.214.3), which is why the
              // conversation column below carries no PageHeader: one <h1> per
              // page, and it is this one.
              title="Agents"
              titleHint={AGENTS_HINT}
              threads={threads}
              selectedId={selectedId}
              onSelect={setSelectedId}
              onNew={() => setPicker({ mode: "create" })}
              pendingDelete={pendingDelete}
              onArmDelete={setPendingDelete}
              onConfirmDelete={(id) => void removeThread(id)}
              avatarByKey={avatarByKey}
              error={railError}
              // A TOGGLE, not a one-way reveal. The icon carries
              // `aria-expanded` for the dialog it controls, and a control that
              // announces a state it can only ever set in one direction is
              // lying about half of it. (In practice the backdrop is over the
              // icon while the room is open, so this is the keyboard path.)
              onOpenAgents={() => setAgentsOpen((v) => !v)}
              agentsOpen={agentsOpen}
              agentCount={rosterEntries.length}
              pickedName={picked?.name ?? null}
            />
          </div>

          {/* THE CONVERSATION STARTS AT THE TOP (v1.214.3). "the chat box
              pushed up so it looks more clean" — the page header used to stand
              in this column, so the transcript began a heading's height down
              while the rail beside it began at zero, and the two columns never
              lined up. The title moved into the rail; this column opens on the
              work. The notes below are transient by nature: when one is on
              screen it has earned the space it takes. */}
          <div
            data-testid="agents-conversation"
            className="flex min-w-0 flex-1 flex-col gap-4 md:min-h-0 md:overflow-y-auto"
          >
            {offline && (
              <Reveal>
                <OfflineHint />
              </Reveal>
            )}
            {tableError && (
              <Reveal>
                <ErrorNote>{tableError}</ErrorNote>
              </Reveal>
            )}
            <div ref={tableRef} className="min-h-0 flex-1">
              {conversation}
            </div>
          </div>
        </div>
        {modals}
      </PageShell>
    );
  }

  /* ------------------------------------------- the older-daemon page, as-is */
  return (
    <PageShell>
      <Reveal>{header}</Reveal>

      {offline && (
        <Reveal>
          <OfflineHint />
        </Reveal>
      )}

      <div data-testid="agents-stack" className="space-y-6">
        <div className="min-w-0 space-y-6">
          {/* Roster (v1.139.0) — who can take delegated work. Renders nothing
              on daemons that predate GET /agents/roster (it carries its own
              Reveal, so hiding leaves no empty gap). The Talk button needs the
              thread routes, so it's only offered when they exist. */}
          <RosterStrip
            entries={hasRoster ? rosterEntries : undefined}
            onTalk={threadsMissing ? undefined : talkWith}
            onAssign={assignWork}
            // Gated on `hasRoster` (v1.179.0): the strip falls back to fetching
            // for ITSELF when the page has no rows, so the page's fetch failing
            // while the strip's succeeds must not grow a gear that reveals
            // nothing — setup is already standing in the flow below. Tying
            // these to the same flag that decides the LAYOUT keeps the two
            // halves of the page telling one story.
            onSelect={hasRoster ? selectAgent : undefined}
            onConfigure={hasRoster ? toggleSetup : undefined}
            configureOpen={setupOpen}
            selected={picked}
          />

          {/* THE PRE-RAIL PAGE. No roster means no gear and no dialog to hold
              these, so they stay in the flow exactly as they shipped — hiding
              them here would delete two capabilities from the daemons least
              able to spare them.

              THE JOB CARD ALSO STANDS IN FOR A MISSING COMPOSER (v1.180.0):
              dispatch moved INTO the thread composer, so a daemon that serves
              the roster but not the thread routes would otherwise have a
              Give-work button and nowhere for the work to go. */}
          <Reveal>
            <div ref={jobRef}>
              <JobPostCard roster={rosterEntries} assign={assign} />
            </div>
          </Reveal>

          {(!hasRoster || setupOpen) && (
            <Reveal>
              <div ref={setupRef}>
                <SetupCard
                  builtin={builtin}
                  dynamic={dynamic}
                  remotes={remotes}
                  models={models}
                  onAgentsChanged={agentsChanged}
                  onRemotesChanged={reloadRemotes}
                  open={setupExpanded}
                  onOpenChange={setSetupDisclosure}
                />
              </div>
            </Reveal>
          )}

          {tableError && (
            <Reveal>
              <ErrorNote>{tableError}</ErrorNote>
            </Reveal>
          )}

          {/* The round-table (hidden entirely on daemons without the thread
              routes). Reached only when the roster is missing, since a daemon
              that has both renders the room above. */}
          {!threadsMissing && (
            <Reveal>
              <div ref={tableRef}>
                {!threadsReady ? (
                  <Card>
                    <SkeletonRows rows={4} />
                  </Card>
                ) : threadsData === null ? (
                  threadsError && threadsError.status !== 0 ? (
                    <ErrorNote>{threadsError.message}</ErrorNote>
                  ) : null
                ) : (
                  <div className="grid items-start gap-4 md:grid-cols-[16rem_minmax(0,1fr)]">
                    <ThreadRail
                      threads={threads}
                      selectedId={selectedId}
                      onSelect={setSelectedId}
                      onNew={() => setPicker({ mode: "create" })}
                      pendingDelete={pendingDelete}
                      onArmDelete={setPendingDelete}
                      onConfirmDelete={(id) => void removeThread(id)}
                      avatarByKey={avatarByKey}
                      error={railError}
                    />
                    <div className="min-w-0">
                      {selectedId ? (
                        <RoundTable
                          threadId={selectedId}
                          reloadNonce={detailNonce}
                          onEditPanel={(detail) =>
                            setPicker({ mode: "edit", thread: detail })
                          }
                          onRoundDone={reloadThreads}
                          roster={rosterEntries}
                          assign={assign}
                          initialInput={pendingAsk}
                        />
                      ) : (
                        <Card>
                          <Empty icon={<MessagesSquare size={22} />}>
                            Pick a thread from the rail — or press New there to
                            start one.
                          </Empty>
                        </Card>
                      )}
                    </div>
                  </div>
                )}
              </div>
            </Reveal>
          )}
        </div>
      </div>

      {modals}
    </PageShell>
  );
}
