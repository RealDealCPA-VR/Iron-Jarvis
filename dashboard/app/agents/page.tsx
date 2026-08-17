"use client";

// Agents — organized around the ROUND-TABLE: persistent threads where agents
// from different sources (built-in, yours, remote — including agents on other
// computers) sit on a panel, each with a role, and answer in turn — seeing
// each other's replies. The management surfaces (create dynamic agents,
// connect remote ones) collapse into the "Set up agents" card; the
// round-table is the star.
//
// v1.178.0 — A ROOM, NOT A FORM. The roster stands as a PERSISTENT LEFT RAIL
// of faces (sticky from md up) and everything else — give work, setup, the
// round-table — is the module beside it. Clicking a face is the page's main
// gesture: it preselects that agent in the job-post card and, when a 1:1
// thread with exactly that agent already exists, opens it so the user carries
// on where they left off. It never CREATES a thread — that stays behind the
// explicit Talk button, because a POST is not what a click on a portrait
// promises. The rail column only appears when the roster does: an older
// daemon that doesn't serve /agents/roster gets the plain stacked page it has
// always had, with no empty column beside it.
//
// v1.179.0 — THE THREAD IS THE PAGE. Reported verbatim: "the give work part at
// the top shouldnt be there because it should simply open when a specific agent
// is selected and be treated more like a thread with that individual agent",
// and "the set up agents should all be contained in the new agent gear face on
// the left pane and not shown unless the user decided to configure an agent".
// So the module beside the rail opens on the CONVERSATION. Two surfaces that
// used to stand permanently above it moved behind their own doors:
//   * GIVE WORK is a collapsed disclosure UNDER the round-table. Posting a job
//     to the TEAM (a supervisor session that plans and delegates) is a real
//     capability with no other home, so it is one click away — from the
//     disclosure itself, or from the rail's Give-work button, which opens it
//     and preselects the agent exactly as before. The fold is a HIDE, not an
//     unmount: `hidden` takes the form out of the picture AND out of the a11y
//     tree, while a job half typed into it survives a stray collapse, and the
//     recent-jobs poll behaves exactly as it did when the card stood open.
//   * SETUP is not in the page at all until the gear-with-a-face is clicked,
//     and the gear collapses it again.
// Both doors need a rail to hang on, so on an older daemon with no roster the
// page keeps rendering them in the flow — the pre-rail page, unchanged.

import { useEffect, useRef, useState } from "react";
import {
  Briefcase,
  Check,
  ChevronDown,
  MessagesSquare,
  Plus,
  Trash2,
} from "lucide-react";
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
 * SetupCard's own disclosure key, written here BEFORE the card mounts.
 *
 * The card owns an internal open state hydrated from localStorage; v1.178.0
 * drove it by clicking its toggle through the DOM, deliberately avoiding this
 * duplication. That stopped working when the page took over VISIBILITY
 * (v1.179.0): the card is not rendered until the gear is clicked, so the click
 * would land on a freshly-mounted card whose hydration `setOpen(true)` was
 * still queued — the DOM would read `aria-expanded="false"`, the click would
 * queue `setOpen(false)` after it, and a user who had opened setup before would
 * get a collapsed card from a gear that says it opened. Writing the key first
 * has no such race: whichever way the card hydrates, it hydrates OPEN.
 * If SetupCard ever renames the key the failure is soft — the gear reveals a
 * collapsed card the user can open with one more click, never an error.
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
  // target there and scrolls the card into view. v1.179.0 — it lives behind a
  // disclosure now: `jobOpen` is whether it SHOWS (the card itself stays
  // mounted and hidden, see the header note), and `jobFocus` counts DELIBERATE
  // opens so the page only scrolls for those — clicking a face also sets
  // `assign`, and yanking the page under someone reading a transcript is the
  // opposite of what a rail is for.
  const jobRef = useRef<HTMLDivElement>(null);
  const [assign, setAssign] = useState<JobAssign | null>(null);
  const [jobOpen, setJobOpen] = useState(false);
  const [jobFocus, setJobFocus] = useState(0);
  // The rail's gear reveals the setup surfaces through this wrapper.
  const setupRef = useRef<HTMLDivElement>(null);
  const [setupOpen, setSetupOpen] = useState(false);
  // WHO THE USER IS WORKING WITH (v1.178.0). This lives on the page, not
  // inside the rail, because it drives the page: the job card's target and
  // which thread is open. Kept as kind + BARE name, the shape every handler
  // here already speaks (participantKey, JobAssign, talkWith).
  const [picked, setPicked] = useState<{ kind: AgentSource; name: string } | null>(
    null,
  );

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

  /** The one 1:1 thread whose panel is EXACTLY this agent, if it exists.
   *  Extracted (v1.178.0) so the rail's "continue working with" and the Talk
   *  button ask the same question — two copies of this predicate would be two
   *  chances to open a thread with somebody else on the panel. */
  function soloThreadWith(kind: AgentSource, name: string) {
    const key = participantKey(kind, name);
    return threads.find(
      (t) => t.participants.length === 1 && t.participants[0]?.key === key,
    );
  }

  /** The roster's Talk button: open the existing 1:1 thread with exactly this
   *  agent, or start one ("Talk with <name>"), then jump to the round-table. */
  async function talkWith(kind: AgentSource, name: string) {
    if (talkBusyRef.current) return;
    const key = participantKey(kind, name);
    const existing = soloThreadWith(kind, name);
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

  /** The roster's Give-work button: OPEN the job-post disclosure, preselect
   *  this agent in it, and bring it into view. The nonce keeps a repeat click
   *  on the same agent a distinct assign, so it still re-selects after a manual
   *  change. The scroll waits for the effect below — on the first open the card
   *  does not exist yet, so scrolling here would scroll nothing. */
  function assignWork(kind: AgentSource, name: string) {
    setAssign({ kind, name, nonce: Date.now() });
    openJob();
  }

  /** Show the job-post card and scroll to it. */
  function openJob() {
    setJobOpen(true);
    setJobFocus((n) => n + 1);
  }

  /** The disclosure's own header: reveal, or fold away again. */
  function toggleJob() {
    if (jobOpen) {
      setJobOpen(false);
      return;
    }
    openJob();
  }

  // Scroll only for a deliberate open — and only once the panel is in the DOM.
  useEffect(() => {
    if (jobFocus === 0) return;
    jobRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [jobFocus]);

  /**
   * A face in the rail was clicked (v1.178.0): "this is who I'm working with".
   *
   * Two effects, both of them things the user can already do by hand — the
   * click just stops making them do it:
   *   - the job-post card preselects this agent, so typing a job and pressing
   *     Post sends it to the face that is highlighted. Only when it can
   *     actually TAKE work: `canWork` is the roster's delegable + healthy
   *     gate, and preselecting an offline remote would put a target in the box
   *     that the card then silently falls back to Team for.
   *   - "continue working with": an existing 1:1 thread with exactly this
   *     agent opens. Nothing is created — that is Talk's job, and a POST is
   *     not what clicking a portrait promises.
   * It deliberately does NOT scroll the job card into view the way Give-work
   * does: selecting is a light gesture, and yanking the page under someone who
   * was reading a transcript is the opposite of what a rail is for.
   */
  function selectAgent(kind: AgentSource, name: string, canWork: boolean) {
    setPicked({ kind, name });
    // ADVERSARIAL REVIEW (v1.178.0): the `if (canWork)` guard alone left a
    // STALE target behind. Click the analyst (job card → "custom:analyst"),
    // then click the offline down-box: the rail moved its ring and its
    // aria-current onto down-box while the card still said analyst, and Post
    // would have sent the job to the analyst. That is the page disagreeing
    // with itself, the same class of bug that forced the selection onto the
    // page in the first place — just reached from the other side.
    //
    // So an un-workable pick doesn't merely skip the preselect, it RESETS the
    // target to the Team. `wireTarget` passes builtin names through unchanged
    // (its own contract: "builtins are bare on the wire already"), so a
    // builtin-kinded assign carrying JobPostCard's own TEAM_TARGET sentinel
    // lands as exactly that sentinel and the card visibly reads
    // "Team — supervisor plans & delegates". Team is also the honest answer:
    // the supervisor is non-delegable and an offline remote cannot take a
    // session, so the Team is where that job was always going to end up.
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

  /**
   * The gear-with-a-face: the ONE door to agent configuration (v1.179.0).
   *
   * Setup is not in the page until this runs — "not shown unless the user
   * decided to configure an agent" — and clicking again folds it away. The
   * localStorage write is what makes the card mount OPEN rather than mounted
   * and still collapsed; see SETUP_OPEN_KEY for why it is a write and not a
   * click on the card's own toggle any more.
   */
  function toggleSetup() {
    const next = !setupOpen;
    try {
      localStorage.setItem(SETUP_OPEN_KEY, next ? "1" : "0");
    } catch {
      /* persistence is best-effort; the reveal below does not depend on it */
    }
    setSetupOpen(next);
  }

  // Focus and the viewport follow the reveal — otherwise a keyboard user
  // activates the gear and their focus is still down in the rail, a section
  // away from the form that just appeared.
  useEffect(() => {
    if (!setupOpen) return;
    const host = setupRef.current;
    host?.scrollIntoView({ behavior: "smooth", block: "start" });
    host?.querySelector<HTMLButtonElement>("button[aria-expanded]")?.focus();
  }, [setupOpen]);

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

  // The rail (v1.178.0). `hasRoster` decides the page LAYOUT, so the rail is
  // handed the page's OWN rows rather than fetching a second opinion — a rail
  // that disagreed with the grid would leave a 15rem column of nothing beside
  // the work. When the page has no rows (older daemon, or its fetch failed
  // while the strip's succeeded) the component falls back to fetching for
  // itself and simply renders in the stacked flow, exactly as before.
  const hasRoster = rosterEntries.length > 0;
  const rail = (
    <RosterStrip
      entries={hasRoster ? rosterEntries : undefined}
      onTalk={threadsMissing ? undefined : talkWith}
      onAssign={assignWork}
      // ADVERSARIAL REVIEW (v1.179.0): both of these are gated on `hasRoster`
      // now. The strip falls back to fetching /agents/roster for ITSELF when
      // the page has no rows, so the page's fetch failing while the strip's
      // succeeds used to render a rail and a gear inside the STACKED (pre-rail)
      // layout — and the gear was then a lie: `hasRoster && setupOpen` gates the
      // reveal, so clicking it flipped aria-expanded to "true" and revealed
      // nothing, while setup was already standing in the flow below. Tying both
      // props to the same flag that decides the LAYOUT keeps the two halves of
      // the page telling one story; in the normal case (`hasRoster`) nothing
      // changes at all.
      onSelect={hasRoster ? selectAgent : undefined}
      onConfigure={hasRoster ? toggleSetup : undefined}
      configureOpen={setupOpen}
      selected={picked}
    />
  );

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

      {/* THE ROOM: faces down the left, the module beside them. With no
          roster the wrapper is a plain block and the column below keeps the
          page's own space-y-6 — byte-for-byte the layout an older daemon has
          always rendered. */}
      <div
        className={
          hasRoster
            ? "grid items-start gap-4 lg:grid-cols-[15rem_minmax(0,1fr)]"
            : ""
        }
      >
        {hasRoster && (
          // Sticky so the faces stay reachable while a long transcript
          // scrolls — "persistent" is the whole point of a rail. Only from lg
          // up: below that the grid is one column and a sticky block would
          // pin the roster over the content it sits above.
          //
          // ADVERSARIAL REVIEW (v1.178.0): this breakpoint was `md` and that
          // wrecked exactly the widths the brief said not to wreck. The page
          // has no persistent sidebar (MainContent is `w-full ... px-3`), so a
          // 768px window is ~744px of content; the ROUND-TABLE below already
          // splits at `md:grid-cols-[16rem_minmax(0,1fr)]`, so both engaged at
          // the same width and the transcript column got
          // 744 − 240 − 16 − 256 − 16 ≈ 216px, minus card padding. At `lg`
          // (1024px) it gets 1000 − 240 − 16 − 256 − 16 ≈ 472px — the same
          // width it had at md BEFORE the rail existed — and the 768–1023px
          // band falls back to the stacked single column, which is the layout
          // that band has always had. Keep this breakpoint in lock-step with
          // RosterStrip's `hidden lg:block` column / `lg:hidden` <select>:
          // they are two halves of one responsive rule.
          <div className="lg:sticky lg:top-4">{rail}</div>
        )}

        <div className="min-w-0 space-y-6">
          {/* Roster (v1.139.0) — who can take delegated work. Renders nothing
              on daemons that predate GET /agents/roster (it carries its own
              Reveal, so hiding leaves no empty gap). The Talk button needs the
              thread routes, so it's only offered when they exist. */}
          {!hasRoster && rail}

          {/* THE PRE-RAIL PAGE (older daemon). No roster means no rail, which
              means no gear and no disclosure to hold these — so they stay in
              the flow exactly as they shipped. Hiding them here would delete
              two capabilities from the daemons least able to spare them. */}
          {!hasRoster && (
            <>
              <Reveal>
                <div ref={jobRef}>
                  <JobPostCard roster={rosterEntries} assign={assign} />
                </div>
              </Reveal>
              <Reveal>
                <div ref={setupRef}>
                  <SetupCard
                    builtin={builtin}
                    dynamic={dynamic}
                    remotes={remotes}
                    models={models}
                    onAgentsChanged={reloadAgents}
                    onRemotesChanged={reloadRemotes}
                  />
                </div>
              </Reveal>
            </>
          )}

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

          {/* GIVE WORK — BELOW the conversation, folded away (v1.179.0).
              "The give work part at the top shouldnt be there." It is not
              deleted: posting a job to the TEAM — a supervisor session that
              plans and delegates across the roster — has no other home on this
              page, and neither does the recent-jobs list. It is one click from
              here, and the rail's Give-work button opens it with the agent
              already selected. Dispatched sessions still carry origin
              "job:agents". */}
          {hasRoster && (
            <Reveal>
              <div>
                {/* The closed state IS a card — one bar in the page's rhythm,
                    not a form. Opening puts the real card below it rather than
                    inside it: JobPostCard is a Card, and a card-surface nested
                    in a card-surface draws two borders around one thing. */}
                <button
                  type="button"
                  onClick={toggleJob}
                  aria-expanded={jobOpen}
                  aria-controls="job-post-panel"
                  className="card-surface flex w-full items-center gap-3 px-5 py-3.5 text-left"
                >
                  <Briefcase size={15} className="shrink-0 text-accent-soft/80" />
                  <span className="min-w-0 flex-1">
                    {/* Deliberately NOT the words "Give work": that is the
                        rail button's name and the card's own title, and three
                        controls answering to one phrase is how a click lands on
                        the wrong one — for a test query and for a screen-reader
                        user picking from a list of buttons alike. */}
                    <span className="block text-[13px] font-semibold tracking-wide text-zinc-200">
                      Post a job
                    </span>
                    <span className="block text-[11.5px] text-zinc-500">
                      The team plans and delegates, or one agent takes it
                    </span>
                  </span>
                  <ChevronDown
                    size={15}
                    aria-hidden
                    className={`shrink-0 text-zinc-500 motion-safe:transition-transform ${
                      jobOpen ? "rotate-180" : ""
                    }`}
                  />
                </button>
                {/* A HIDE, not an unmount. `hidden` removes the panel from the
                    picture and from the a11y tree — a folded disclosure is not
                    a set of controls a screen reader can still tab into — while
                    a job half typed into it survives a stray collapse, the
                    preselect from a rail click is already applied when it
                    opens, and the recent-jobs poll behaves exactly as it did
                    when this card stood open at the top of the page. */}
                <div
                  id="job-post-panel"
                  ref={jobRef}
                  hidden={!jobOpen}
                  className="mt-3"
                >
                  <JobPostCard roster={rosterEntries} assign={assign} />
                </div>
              </div>
            </Reveal>
          )}

          {/* SETUP — only once the gear says so. Not rendered at all otherwise:
              "not shown unless the user decided to configure an agent". */}
          {hasRoster && setupOpen && (
            <Reveal>
              <div ref={setupRef}>
                <SetupCard
                  builtin={builtin}
                  dynamic={dynamic}
                  remotes={remotes}
                  models={models}
                  onAgentsChanged={reloadAgents}
                  onRemotesChanged={reloadRemotes}
                />
              </div>
            </Reveal>
          )}
        </div>
      </div>

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
