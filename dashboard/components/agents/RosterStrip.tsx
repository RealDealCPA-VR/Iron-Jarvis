"use client";

// Roster (v1.139.0) — who can take delegated work. A read-only awareness
// strip over GET /agents/roster: every agent that chat escalation, workflows,
// and the supervisor can hand work to, with HONEST measured stats — a rate
// never renders without its sample count ("87% over 23 runs", never a bare
// "87%"). Older daemons don't serve the endpoint, so the whole section simply
// doesn't exist rather than erroring over a feature the daemon predates.
//
// v1.178.0 — THE RAIL. The page reads as a ROOM now: the faces stand in a
// persistent column down the LEFT of the Agents module and the user picks one
// to work with, instead of hunting a <select> above a form. Two things kept
// this honest rather than a rewrite:
//   * the column renders ONLY when the page hands over `onSelect`. A column of
//     faces nobody can select is decoration, and the older in-flow composition
//     (no selection state to drive) still has to work unchanged.
//   * the <select> did not go away — it IS the narrow-width form of the rail
//     (`md:hidden` since v1.180.0, against the column's `hidden md:block`), so one of
//     the two is in the a11y tree at any viewport (display:none removes the
//     other) and a 380px-wide window gets a control that fits instead of a
//     15rem column eating the screen.
// The breakpoint was `lg`, not `md`, and app/agents/page.tsx's grid had to
// agree: the round-table on that page splits at md with its OWN 16rem rail, so
// a 15rem rail BESIDE it at md left the transcript ~216px on a 768px window.
// (v1.180.0 stacks them instead, which is what let the rule move to `md` — see
// the note at the foot of this header.)
// The gear-with-a-face at the foot of the rail is ONE door to both setup
// surfaces (an agent of your own, or one on another computer).
//
// v1.179.0 — ONE RENDERING PER AGENT. Reported verbatim: "there seems to be a
// redundant agent on the left pane for vr-assistant". The roster was NOT
// serving a duplicate — the strip drew the rail AND then re-drew whichever
// agent was selected in a DETAIL BLOCK underneath it, face, name, kind pill and
// all. Two portraits of one agent, one above the other, is a duplicate as far
// as the person looking at it is concerned. So in rail mode the detail moved
// ONTO the selected row (a sub-line under that row's own button, outside it so
// the button's accessible name stays the agent's name) and the block below is
// gone. The standalone composition — no `onSelect`, no rail — keeps the block:
// there is no row for the detail to live on there.
// The same report also said the kind pill is worth keeping ("i do like that it
// has a little remote indicator on it so any remote agents should come with
// that"), so REMOTE rows carry it on every row, selected or not, alongside a
// worded offline pill. Built-in/Yours stay off the rows on purpose: at 15rem a
// pill on all five rows is noise, and their kind shows on the selected row's
// detail line and in every row's title.
//
// v1.180.0 — IT FOLDS. "The roster list should be collapsable for a cleaner
// look." The header becomes the disclosure (the page owns the state and
// persists it, the same way it owns the selection and the setup reveal), and
// folding HIDES rather than unmounts, so the narrow <select>'s value and the
// scroll position of a long rail survive a fold. Two things deliberately
// survive the fold on screen:
//   * the HEADER still says how many agents there are AND who is selected — a
//     collapsed control that says nothing about what it is hiding is a control
//     nobody reopens;
//   * THE GEAR. Agent configuration lives behind it and nowhere else since
//     v1.179.0, so folding the list must not make creating an agent
//     unreachable. It sits outside the folded region on purpose.
// The face column's breakpoint moved `lg` → `md` in the same release: the page
// stacks the conversation BELOW the roster now (v1.180.0) instead of beside it,
// so the two no longer compete for width and the reason the rule sat at `lg`
// is gone. It is still ONE rule in two halves — this file's `hidden md:block`
// column and `md:hidden` <select>, and the page's `md:w-[17rem]` cap — and all
// three must move together or a viewport band gets two pickers, or none.

// v1.193.0 — WHO IS BUSY, AND WHOSE HISTORY IS REAL. Two things the daemon
// only started knowing this release:
//   * LIVENESS. `RosterEntry.activity` ("busy" | "queued" | "idle" |
//     "unknown") reports which agents are TAKEN right now. It renders as a
//     dot-plus-word pill on EVERY rail row — form first, so a busy teammate
//     reads without being read — and NOTHING at all for idle/unknown, because
//     the daemon's signal cannot see delegate/spawn_agent children and absence
//     was never a claim that anyone is free (agents/roster.py states that
//     limit at length). The pill also had to be SPLIT OUT of the stats slot:
//     the daemon packs liveness and the track record into one parenthetical
//     ("busy, 87% over 23 runs"), which this file was rendering verbatim as
//     the stats line.
//   * REAL STATS FOR `custom:` AND `remote:` AGENTS. Outcomes are keyed by
//     roster name now, so a teammate the user created finally has a history.
//     Nothing here filtered by kind (checked — statsText never did), and the
//     dict fallback stays kind-blind on purpose so it cannot start to.
import { type ReactElement, useState } from "react";
import { Briefcase, ChevronDown, MessageCircle, WifiOff } from "lucide-react";
import { API_BASE, ijToken } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { timeAgo } from "@/lib/format";
import { Card } from "@/components/ui";
import { Reveal } from "@/components/motion";
import AgentFace, { type FaceOverride } from "@/components/agents/AgentFace";
import { SOURCE_LABEL, type AgentSource } from "@/components/agents/identity";

interface RosterStats {
  sessions?: number | null;
  avg_score?: number | null;
  success_rate?: number | null;
  trend?: string | null;
}

/** One GET /agents/roster entry. Typed here (not lib/types.ts — that file is
 *  owned by the coordinating session this release). */
export interface RosterEntry {
  /** "builder" | "custom:<slug>" | "remote:<name>" — the delegation name. */
  name: string;
  kind: AgentSource;
  description: string;
  /** A session can actually be spawned on it (false → chat-only for now). */
  delegable: boolean;
  /** Remotes carry live status; builtin/dynamic are always true. */
  healthy: boolean;
  stats: RosterStats | null;
  /** The daemon's own composed one-liner, stats parenthetical included. */
  line?: string;
  /** v1.171.0 additive — absent on older daemons, so all optional. ISO time
   *  of this agent's newest round-table entry; null = no recorded activity. */
  last_active?: string | null;
  /** That newest entry's text, daemon-clipped to ≤140 plain chars. */
  last_message?: string | null;
  /** Serve path for a stored portrait — present ONLY when one exists. */
  avatar?: string | null;
  /** v1.180.0 additive: the CHOSEN face, or null/absent to derive it from the
   *  name. The daemon has served this on every roster row since v1.180.0
   *  (`_face_override(bare)`); it was never typed here because the rail read
   *  faces from the shared provider instead. The agents room (v1.214.0) draws
   *  from the roster directly, so the field is declared where it arrives. */
  face?: FaceOverride | null;
  /** v1.193.0 LIVENESS, additive and optional — "busy" | "queued" | "idle" |
   *  "unknown" (agents/roster.py::RosterEntry.activity). A daemon that predates
   *  it, or one whose /agents/roster serializer does not forward it yet, sends
   *  nothing and `livenessOf` falls back to the composed `line` (which carries
   *  the same word). "idle"/"unknown" render NOTHING on purpose: the daemon
   *  reports who is TAKEN and never asserts that anyone is free — it cannot see
   *  delegate/spawn_agent children at all. */
  activity?: string | null;
}

/** <img> can't send the Authorization header — the token rides as ?token=,
 *  the same pattern every media surface uses (creative gallery, previews).
 *  `cacheKey` (the row's last_active — SetupCard's `rev` idea at low
 *  resolution) busts the browser cache after a portrait is replaced, so the
 *  roster never keeps rendering a stale image the daemon no longer serves. */
export function rosterAvatarSrc(rel: string, cacheKey?: string | null): string {
  const token = ijToken();
  const v = encodeURIComponent(cacheKey || "0");
  return `${API_BASE}${rel}?v=${v}${token ? `&token=${encodeURIComponent(token)}` : ""}`;
}

export const KIND_PILL: Record<AgentSource, string> = {
  builtin: "border-accent/30 bg-accent/[0.08] text-accent-soft",
  dynamic: "border-violet-500/25 bg-violet-500/10 text-violet-300",
  remote: "border-zinc-500/25 bg-zinc-500/10 text-zinc-400",
};

/** The shown name: the bare slug — the kind pill carries provenance, so the
 *  wire prefixes ("custom:", "remote:") stay off the screen. */
export function bareName(name: string): string {
  if (name.startsWith("custom:")) return name.slice("custom:".length);
  if (name.startsWith("remote:")) return name.slice("remote:".length);
  return name;
}

export type Liveness = "busy" | "queued";
const LIVE_STATES = new Set<string>(["busy", "queued"]);

/** The trailing parenthetical of the daemon's composed `line`, with any
 *  LIVENESS prefix removed — `_suffix()` (agents/roster.py) puts liveness and
 *  the track record inside ONE pair of parens ("busy, 87% over 23 runs"), and
 *  the stats slot must not say "busy". Returns null when the line carries no
 *  usable stats parenthetical ("(offline)" is health, not stats). */
function statsParen(e: RosterEntry): string | null {
  const paren = /\(([^()]+)\)\s*$/.exec(e.line ?? "")?.[1]?.trim();
  if (!paren || paren.toLowerCase() === "offline") return null;
  const lead = /^(busy|queued),\s*/i.exec(paren);
  const rest = lead ? paren.slice(lead[0].length).trim() : paren;
  return rest || null;
}

/** Is this agent working right now? "busy" | "queued" | null (v1.193.0).
 *
 *  TWO SOURCES, ONE MEANING, both the daemon's own word — never inferred from
 *  anything else the UI happens to know:
 *    1. `activity`, the roster field itself;
 *    2. the liveness prefix the daemon already bakes into `line`'s suffix,
 *       which is what a daemon whose /agents/roster serializer does not forward
 *       `activity` still sends today (see the report for that gap).
 *  An OFFLINE remote reports nothing: the daemon's own `_suffix()` drops
 *  liveness for an unhealthy entry, the row already shows the more urgent
 *  offline pill, and "busy" about an unreachable box is noise.
 *  null is NOT "free" — it is "no claim" (idle, unknown, and every delegated
 *  child, which this signal structurally cannot see). Nothing renders for it. */
export function livenessOf(e: RosterEntry): Liveness | null {
  if (!e.healthy) return null;
  const direct = String(e.activity ?? "").trim().toLowerCase();
  if (LIVE_STATES.has(direct)) return direct as Liveness;
  const paren = /\(([^()]+)\)\s*$/.exec(e.line ?? "")?.[1] ?? "";
  const head = paren.split(",")[0]?.trim().toLowerCase() ?? "";
  // The comma is required: the daemon only ever prefixes liveness ONTO a stats
  // phrase, so a bare "(queued)" from anywhere else is not this signal.
  if (paren.includes(",") && LIVE_STATES.has(head)) return head as Liveness;
  return null;
}

/** The liveness marker: a DOT plus the word, so it reads at a glance without
 *  being parsed — and stays legible to a screen reader, which a dot alone
 *  would not be. Deliberately says nothing about agents with no marker. */
export function LivePill({
  state,
  bare,
  testId,
}: {
  state: Liveness;
  bare: string;
  testId: string;
}): ReactElement {
  const busy = state === "busy";
  return (
    <span
      data-testid={testId}
      data-activity={state}
      title={
        busy
          ? `${bare} is running a session right now`
          : `${bare} has a session waiting for a free slot`
      }
      className={`inline-flex shrink-0 items-center gap-1 rounded-md border px-1 py-px text-[9.5px] font-medium ${
        busy
          ? "border-amber-400/25 bg-amber-400/10 text-amber-200"
          : "border-sky-400/20 bg-sky-400/[0.07] text-sky-200/90"
      }`}
    >
      <span
        aria-hidden
        data-testid={`${testId}-dot`}
        className={`h-1.5 w-1.5 rounded-full ${
          busy ? "bg-amber-300 motion-safe:animate-pulse" : "bg-sky-300/80"
        }`}
      />
      {state}
    </span>
  );
}

/** The honest stats text. Prefer the daemon's own wording — the trailing
 *  parenthetical of its composed `line` ("87% over 23 runs", "no runs yet"),
 *  minus any liveness prefix (that renders as its own pill).
 *  "(offline)" is health, not stats — the row already shows an offline pill,
 *  so it falls through to the stats dict. Sample counts are ALWAYS visible;
 *  a percentage never renders bare. The dict fallback is KIND-BLIND on purpose:
 *  since v1.193.0 `custom:` and `remote:` agents finally accumulate a real
 *  track record, and a builtin-only shortcut here would keep reading
 *  "no runs yet" about exactly the agents that release exists for. */
export function statsText(e: RosterEntry): string {
  const paren = statsParen(e);
  if (paren) return paren;
  const s = e.stats;
  const runs = typeof s?.sessions === "number" ? s.sessions : 0;
  if (!s || runs <= 0) return "no runs yet";
  const runsTxt = `${runs} run${runs === 1 ? "" : "s"}`;
  const rate = s.success_rate;
  if (typeof rate !== "number") return `${runsTxt} so far`;
  // success_rate is a FRACTION by contract — improvement/engine.py composes
  // round(success_count / n, 4) and roster.py's own line() renders it * 100.
  // The scale is pinned, so no fraction-vs-percent guessing (1 means 100%,
  // never "1%"); the clamp only guards a corrupt wire value.
  const pct = Math.round(Math.min(1, Math.max(0, rate)) * 100);
  return `${pct}% over ${runsTxt}`;
}

/**
 * The gear WITH A FACE that opens agent configuration (v1.178.0) — the user
 * asked for this shape by name, and it earns the drawing: a plain cog reads as
 * "settings for this page", while a cog with eyes in it reads as "make one of
 * these", which is what the button actually does.
 *
 * Purely decorative: `aria-hidden` with NO role, because the button around it
 * carries the accessible name. Everything is `currentColor` so it inherits the
 * button's hover/focus colour instead of pinning a hue that only works on one
 * theme. Nothing animates — the rail's real faces carry the motion budget.
 */
export function GearFace({ size = 26 }: { size?: number }): ReactElement {
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      aria-hidden
      focusable="false"
      className="shrink-0"
      data-testid="gear-face"
    >
      {[0, 45, 90, 135, 180, 225, 270, 315].map((a) => (
        <rect
          key={a}
          x="10.7"
          y="0.9"
          width="2.6"
          height="4.2"
          rx="1"
          fill="currentColor"
          transform={`rotate(${a} 12 12)`}
        />
      ))}
      <circle cx="12" cy="12" r="8.4" fill="none" stroke="currentColor" strokeWidth="1.6" />
      <circle cx="9.3" cy="10.8" r="1.35" fill="currentColor" />
      <circle cx="14.7" cy="10.8" r="1.35" fill="currentColor" />
      <path
        d="M9.2 14.7 q2.8 2.1 5.6 0"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
    </svg>
  );
}

/**
 * The Roster section for the Agents page. Renders nothing while loading, on
 * ANY fetch error (a pre-roster daemon 404s here — hiding beats a scary
 * error), and on an empty roster.
 */
export function RosterStrip({
  entries: supplied,
  onTalk,
  onAssign,
  onSelect,
  onConfigure,
  configureOpen = false,
  onToggleCollapse,
  collapsed = false,
  selected: picked = null,
}: {
  /** The page's OWN GET /agents/roster rows (v1.178.0). The page needs them
   *  anyway (job card, panel picker) and now also decides the page LAYOUT from
   *  them — a second independent fetch could disagree with that decision and
   *  leave a 15rem blank column beside the work. Passing them down makes the
   *  two agree by construction; omit the prop and the strip fetches for
   *  itself exactly as it always did. */
  entries?: RosterEntry[];
  /** The Talk button: open (or start) a 1:1 thread with this agent at the
   *  round-table. Only offered for delegable + healthy entries; omit the
   *  prop (older daemons without thread routes) and no button renders. */
  onTalk?: (kind: AgentSource, name: string) => void;
  /** The Give-work button (v1.166.0): aim the work at this agent. Same
   *  delegable + healthy gate as Talk — a non-delegable entry (supervisor)
   *  stays chat-only, and an offline remote can't take work. WHERE the work
   *  lands is the page's call, not this strip's: since v1.180.0 the agents
   *  page opens the 1:1 thread and arms its composer, while the pre-rail /
   *  no-thread-routes compositions still aim the standalone job card. So the
   *  button's wording here names the INTENT and never a destination. */
  onAssign?: (kind: AgentSource, name: string) => void;
  /** A face was clicked (v1.178.0): this agent is now the one being worked
   *  with. `canWork` is the SAME delegable + healthy gate the buttons use, so
   *  the page never preselects a job target that can't take the job. Supplying
   *  this prop is what turns the vertical face column on. */
  onSelect?: (kind: AgentSource, name: string, canWork: boolean) => void;
  /** Who the PAGE thinks is selected (kind + BARE name — the shape every
   *  handler on the page already speaks). The selection drives the job card
   *  and which thread is open, so the page has to own it: keeping it in here
   *  meant the highlight was lost on any page-level re-render that remounted
   *  the strip, and the rail would then be pointing at a different agent than
   *  the rest of the page was acting on. Omit it and the strip selects for
   *  itself exactly as before. */
  selected?: { kind: AgentSource; name: string } | null;
  /** The gear-with-a-face: open the create/connect surfaces. Omit it (a page
   *  with no setup surface to reveal) and no gear renders. */
  onConfigure?: () => void;
  /** Whether the surface behind the gear is currently showing (v1.179.0). The
   *  gear is a DISCLOSURE now — setup is not on the page until it is clicked —
   *  so the button has to announce the state it controls, not just change it. */
  configureOpen?: boolean;
  /** Fold the list away (v1.180.0). Supplying this turns the header into a
   *  disclosure button; omit it and the header is the plain caption it has
   *  always been. The STATE lives on the page — it is persisted across visits
   *  and the page is where the other two disclosure keys already live — so this
   *  component stays the renderer of a decision it does not own. */
  onToggleCollapse?: () => void;
  /** Whether the list is currently folded. Ignored without
   *  `onToggleCollapse`: a folded roster with no control to unfold it would be
   *  a section the user cannot get back. */
  collapsed?: boolean;
} = {}) {
  const [choice, setChoice] = useState("");
  // A supplied roster disables the fetch outright (path null) rather than
  // firing it and ignoring the answer.
  const { data, error } = useApi<{ roster?: RosterEntry[] }>(
    supplied && supplied.length > 0 ? null : "/agents/roster",
  );
  const entries = ((supplied?.length ? supplied : data?.roster) ?? []).filter(
    (e): e is RosterEntry => Boolean(e) && typeof e.name === "string",
  );
  if ((error && !supplied?.length) || entries.length === 0) return null;

  // The Reveal lives HERE (not at the call site) so a hidden roster leaves no
  // empty wrapper behind to double the page's space-y gap.
  //
  // v1.158.0: a PICKER, not a list. One row per agent meant the section grew
  // with the roster and pushed the actual work down the page. v1.178.0 keeps
  // the picker's shape — ONE selected agent carries the full detail — and
  // moves it sideways: down the left as a column of faces, where a long roster
  // costs the work no vertical room at all.
  //
  // The page's pick wins; `choice` is the local mirror that keeps the
  // standalone composition working. Both are written in the SAME call
  // (`choose`), so they cannot disagree — and a pick naming an agent this
  // roster no longer carries falls through to the first entry rather than
  // rendering nothing.
  const pickedEntry = picked
    ? entries.find(
        (e) => e.kind === picked.kind && bareName(e.name) === picked.name,
      )
    : undefined;
  const choiceEntry = entries.find((e) => e.name === choice);
  const selected = pickedEntry || choiceEntry || entries[0];
  // ADVERSARIAL REVIEW (v1.178.0): the entries[0] fallback is a DEFAULT
  // PREVIEW, not a selection, and the rail must not claim otherwise. On first
  // paint nobody has picked anyone — the page's `picked` is null, the job card
  // reads "Team", no thread was opened — yet marking `active` off `selected`
  // alone tinted the first row AND announced it `aria-current="true"`. The
  // roster's first entry is the supervisor, the one agent with
  // `delegable: false`: the rail said "you are working with supervisor" about
  // the one agent that can never take work, and then a typed job went to the
  // Team. So the highlight is gated on a REAL pick (resolved against these
  // very entries, so a stale pick naming an agent this roster no longer
  // carries doesn't light up entries[0] by accident). The detail block below
  // still previews entries[0] — it names the agent it is showing and claims
  // nothing about who the page is acting on.
  const isPick = Boolean(pickedEntry || choiceEntry);
  /** Talk / Give-work may only act on an agent that can take the work AND —
   *  in rail mode — one the user has actually picked (see the note by the
   *  buttons). */
  const actionable = (!onSelect || isPick) && selected.delegable && selected.healthy;
  /** Is there anything IN the action row at md and up? (Below md the <select>
   *  is always in it.) An empty padded strip between the rail and the gear is
   *  just dead space, so at md the row folds away until it has a button. */
  const showActions = Boolean((onTalk || onAssign) && actionable);
  const offline = selected.kind === "remote" && !selected.healthy;
  const selectedLive = livenessOf(selected);
  const shown = bareName(selected.name);
  const kindLabel = SOURCE_LABEL[selected.kind] ?? (selected.kind || "agent");
  const kindPill = KIND_PILL[selected.kind] ?? KIND_PILL.remote;

  /** ONE selection path for both forms of the picker (the face column and the
   *  narrow-width <select>), so they can never drift into meaning different
   *  things. `canWork` is computed here — the page must not have to re-derive
   *  the delegable + healthy rule and get it subtly wrong. */
  function choose(e: RosterEntry) {
    setChoice(e.name);
    onSelect?.(e.kind, bareName(e.name), e.delegable && e.healthy);
  }

  /** The fold (v1.180.0). `collapsed` alone never hides anything — without a
   *  toggle there is no way back, and a section the user cannot reopen is
   *  worse than a cluttered one. */
  const foldable = Boolean(onToggleCollapse);
  const folded = foldable && collapsed;
  /** What the folded header says it is hiding. The count is always true; the
   *  name is only shown for a REAL pick (the same distinction the aria-current
   *  gate draws — `selected` falls back to entries[0] as a preview). */
  const foldedCaption = isPick
    ? `${shown} selected · tap to show all`
    : "who can take delegated work";

  return (
    <Reveal>
      {/* The whole left pane, addressable as one thing (v1.179.0): the
          redundancy the user reported was rail + a second portrait of the same
          agent below it, and a test that only looks INSIDE the rail rows could
          never see that. */}
      <div data-testid="roster-pane">
        <Card pad={false} className="overflow-hidden">
          {/* THE HEADER IS THE FOLD (v1.180.0) when the page hands over a
              toggle. Its accessible name is the count plus the caption, so a
              screen-reader user hears WHAT is being hidden and — once someone
              is picked — who stays selected while it is hidden. Without a
              toggle it is the plain caption block it has always been. */}
          {foldable ? (
            <button
              type="button"
              onClick={onToggleCollapse}
              data-testid="roster-toggle"
              aria-expanded={!folded}
              aria-controls="roster-body"
              title={folded ? "Show the roster" : "Hide the roster"}
              className="flex w-full items-center gap-2 border-b hairline px-4 py-2.5 text-left transition-colors hover:bg-white/[0.03]"
            >
              <span className="min-w-0 flex-1">
                <span className="block text-[11px] font-medium uppercase tracking-wide text-zinc-500">
                  Roster · {entries.length}
                </span>
                <span className="block truncate text-[11px] text-zinc-600">
                  {folded ? foldedCaption : "who can take delegated work"}
                </span>
              </span>
              <ChevronDown
                size={14}
                aria-hidden
                className={`shrink-0 text-zinc-500 motion-safe:transition-transform ${
                  folded ? "-rotate-90" : ""
                }`}
              />
            </button>
          ) : (
            <div className="border-b hairline px-4 py-2.5">
              <div className="flex items-center justify-between gap-2">
                <span className="text-[11px] font-medium uppercase tracking-wide text-zinc-500">
                  Roster · {entries.length}
                </span>
                {!onSelect && (
                  <span className="text-[11px] text-zinc-600">
                    who can take delegated work
                  </span>
                )}
              </div>
              {onSelect && (
                <p className="mt-0.5 text-[11px] text-zinc-600">
                  who can take delegated work
                </p>
              )}
            </div>
          )}

          {/* EVERYTHING THE FOLD HIDES — and nothing else. `hidden` rather than
              an unmount: the narrow <select>'s value, the scroll position of a
              long rail and the local `choice` all survive a fold, and the
              attribute takes the controls out of the a11y tree so a folded list
              is not something a screen reader can still tab through. The gear
              below is OUTSIDE this wrapper on purpose — see the header note. */}
          <div id="roster-body" hidden={folded}>

          {/* THE RAIL (md and up). A column of faces you scan, not a list you
              read: name + face + health, and the detail for the selected one on
              the selected ROW — ONE rendering per agent, never a second portrait
              of it below. Capped height with its own scroll so a 30-agent roster
              can't push the gear off the bottom of the card. */}
          {onSelect && (
            <div
              data-testid="roster-rail"
              // FLOWS INTO COLUMNS AT WIDTH (v1.183.0). The card is now as
              // wide as the conversation above it, so a single 17rem-ish
              // stack of faces would leave most of the card empty and the
              // rows stretched to absurdity. A grid keeps each row the size
              // it wants to be and uses the space the alignment bought.
              // max-h still bounds it so a 30-agent roster cannot push the
              // gear off the bottom; with fewer rows per column now, the
              // scroll starts later than it used to.
              className="hidden max-h-[44vh] grid-cols-1 gap-x-2 gap-y-0.5 overflow-y-auto p-1.5 md:grid lg:grid-cols-2 xl:grid-cols-3"
            >
              {entries.map((e) => {
                const active = isPick && e.name === selected.name;
                const off = e.kind === "remote" && !e.healthy;
                const bare = bareName(e.name);
                // v1.193.0: liveness is on EVERY row, not just the selected
                // one — "who is busy" is a question you ask while scanning the
                // rail for someone to hand work to, and a marker you have to
                // click a face to see answers it too late.
                const live = livenessOf(e);
                return (
                  <div
                    key={e.name}
                    className={`rounded-xl border transition-colors ${
                      active
                        ? "border-accent/25 bg-accent/[0.08]"
                        : "border-transparent hover:bg-white/[0.04]"
                    }`}
                  >
                    <button
                      type="button"
                      // aria-current is how the selection is ANNOUNCED — the ring
                      // and the accent tint say it to the eye only, and this rail
                      // is the page's primary control now.
                      aria-current={active ? "true" : undefined}
                      onClick={() => choose(e)}
                      title={`${bare} — ${SOURCE_LABEL[e.kind] ?? e.kind}${
                        off ? " (offline)" : ""
                      }${!e.delegable ? " (chat-only)" : ""}`}
                      className="flex w-full items-center gap-1.5 rounded-xl px-2 py-1.5 text-left"
                    >
                      <AgentFace
                        name={bare}
                        mood="idle"
                        size={26}
                        // title="" = decorative: the visible name beside it is
                        // already the accessible name of this button.
                        title=""
                        avatarUrl={e.avatar ? rosterAvatarSrc(e.avatar, e.last_active) : undefined}
                        className={off ? "opacity-50" : ""}
                      />
                      <span
                        className={`min-w-0 flex-1 truncate text-[12.5px] ${
                          active ? "text-accent-soft" : "text-zinc-300"
                        }`}
                      >
                        {bare}
                      </span>
                      {/* WORKING RIGHT NOW (v1.193.0), before provenance and
                          after health: a taken teammate is the fact that
                          changes who you pick. Nothing renders when the daemon
                          makes no claim — see livenessOf. */}
                      {live && (
                        <LivePill
                          state={live}
                          bare={bare}
                          testId={`roster-activity-${bare}`}
                        />
                      )}
                      {/* Offline BEFORE provenance, and in words: an unreachable
                          agent is the more urgent fact, and a rose icon on its
                          own reaches nobody using a screen reader. */}
                      {off && (
                        <span className="inline-flex shrink-0 items-center gap-0.5 rounded-md border border-rose-500/25 bg-rose-500/10 px-1 py-px text-[9.5px] font-medium text-rose-300">
                          <WifiOff size={9} aria-hidden /> offline
                        </span>
                      )}
                      {/* THE REMOTE INDICATOR (v1.179.0) — on the ROW now, for
                          every remote whether it is selected or not. This is the
                          pill the detail block used to carry and the one thing
                          the user asked to keep: "any remote agents should come
                          with that". An agent running on ANOTHER COMPUTER is the
                          provenance that changes what a click means. */}
                      {e.kind === "remote" && (
                        <span
                          data-testid={`roster-kind-${bare}`}
                          className={`shrink-0 rounded-md border px-1 py-px text-[9.5px] font-medium ${KIND_PILL.remote}`}
                        >
                          {SOURCE_LABEL.remote}
                        </span>
                      )}
                    </button>

                    {/* THE DETAIL, ON THE ROW. Outside the button on purpose: a
                        last message and a stats line inside it would be read out
                        as part of the button's name. Renders for the selected row
                        only, so nothing here is ever said twice. */}
                    {active && (
                      <div className="px-2 pb-2 pt-0.5">
                        <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                          {/* Remote already showed its pill on the row above. */}
                          {e.kind !== "remote" && (
                            <span
                              data-testid={`roster-kind-${bare}`}
                              className={`shrink-0 rounded-md border px-1 py-px text-[9.5px] font-medium ${
                                KIND_PILL[e.kind] ?? KIND_PILL.remote
                              }`}
                            >
                              {SOURCE_LABEL[e.kind] ?? e.kind}
                            </span>
                          )}
                          {!e.delegable && (
                            <span className="shrink-0 text-[10px] text-zinc-600">
                              (chat-only for now)
                            </span>
                          )}
                          {/* Honest stats, unchanged rule: a rate never renders
                              without its sample count. */}
                          <span className="ml-auto shrink-0 text-[10.5px] tabular-nums text-zinc-500">
                            {statsText(e)}
                          </span>
                        </div>
                        {e.last_message ? (
                          <p
                            data-testid="roster-preview"
                            className="mt-1 flex items-baseline gap-1.5 text-[11px] leading-relaxed"
                          >
                            <span className="min-w-0 flex-1 truncate text-zinc-400">
                              {e.last_message}
                            </span>
                            {e.last_active && (
                              <span
                                data-testid="roster-when"
                                className="shrink-0 text-[10px] tabular-nums text-zinc-600"
                              >
                                {timeAgo(e.last_active)}
                              </span>
                            )}
                          </p>
                        ) : e.description ? (
                          <p className="mt-1 text-[11px] leading-relaxed text-zinc-500">
                            {e.description}
                          </p>
                        ) : null}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}

          {/* ADVERSARIAL REVIEW (v1.179.0): the bottom padding used to come
              from the detail block that sat under this row. With the block gone
              in rail mode the buttons landed flush against the gear's divider,
              so the row carries its own `pb-3` there — and folds at md when it
              has nothing to show, rather than leaving a padded blank strip. */}
          <div
            className={`flex flex-wrap items-center gap-2 px-4 pt-3 md:pt-2.5 ${
              !onSelect ? "" : showActions ? "pb-3" : "pb-3 md:hidden"
            }`}
          >
            {/* The narrow-width form of the rail above (and the whole picker on
                a page that supplies no onSelect). `md:hidden` keeps exactly one
                of the two in the a11y tree — and it must stay in lock-step with
                the column's `hidden md:block` above and with
                app/agents/page.tsx's `md:w-[17rem]` cap on the roster wrapper,
                or a viewport band gets either two pickers or none.
                REVIEW (v1.180.0): this note still said `lg` in all three places
                and cited a `lg:grid-cols-[15rem_...]` on the page — the grid the
                same release deleted. The rule really did move down to `md` (the
                page stacks the conversation BELOW the roster now, so the two no
                longer compete for width), and a comment naming the old
                breakpoint and a container that no longer exists is how the next
                editor moves one half of a two-half rule. */}
            <label className={`sr-only ${onSelect ? "md:hidden" : ""}`} htmlFor="roster-pick">
              Choose an agent
            </label>
            {/* ADVERSARIAL REVIEW (v1.179.0): in RAIL mode this select showed
                `entries[0]` before anyone had picked — and `actionable` now
                requires a REAL pick, so Talk/Give-work were hidden while the
                box read "builder — Built-in". Below lg the rail is
                display:none, so the select is the ONLY picker there, and
                re-choosing the option a select is already showing fires no
                `change` event: the roster's FIRST agent (builder on a real
                daemon — delegable and healthy) could never be given work at a
                narrow width at all. So in rail mode the unpicked select says
                what is true — nobody is chosen yet — which also makes picking
                entries[0] a genuine change. The standalone composition (no
                rail, the select IS the selection) keeps its old default
                exactly. */}
            <select
              id="roster-pick"
              value={onSelect && !isPick ? "" : selected.name}
              onChange={(ev) => {
                const next = entries.find((e) => e.name === ev.target.value);
                if (next) choose(next);
              }}
              className={`field min-w-0 flex-1 py-1.5 text-[12.5px] ${
                onSelect ? "md:hidden" : ""
              }`}
            >
              {onSelect && !isPick && (
                <option value="" disabled>
                  Choose an agent…
                </option>
              )}
              {entries.map((e) => (
                // Provenance and health ride IN the option text: a picker whose
                // closed state hides whether an agent is a remote — or offline —
                // is the wrong trade for a tidier page.
                <option key={e.name} value={e.name}>
                  {bareName(e.name)} — {SOURCE_LABEL[e.kind] ?? e.kind}
                  {e.kind === "remote" && !e.healthy ? " (offline)" : ""}
                  {!e.delegable ? " (chat-only)" : ""}
                </option>
              ))}
            </select>
            {/* THE ACTIONS FOR WHOEVER IS SELECTED. They sit here, below both
                forms of the picker, so the narrow layout (where the face column
                is display:none) can still reach them.
                `actionable` (v1.179.0): in RAIL mode a button may only act on a
                REAL pick. `selected` falls back to entries[0] as a preview, and
                a "Give work" that quietly meant the supervisor because nobody had
                clicked yet is the same lie the aria-current gate closed. The
                standalone composition keeps its old behaviour exactly (it has no
                rail, so its <select> IS the selection). */}
            {onTalk && actionable && (
              <button
                type="button"
                onClick={() => onTalk(selected.kind, shown)}
                title={`Talk with ${shown} at the round-table`}
                className="btn-ghost shrink-0 px-2.5 py-1.5 text-[11.5px]"
              >
                <MessageCircle size={12} /> Talk
              </button>
            )}
            {onAssign && actionable && (
              <button
                type="button"
                onClick={() => onAssign(selected.kind, shown)}
                // REVIEW (v1.180.0): this said "via the job-post card", and on
                // the page the user actually opens that card no longer exists —
                // the work is aimed at the thread composer now. A tooltip that
                // names a removed surface is a small lie about the only thing
                // the button does, and the strip cannot know which composition
                // it is in, so it names the intent instead.
                title={`Give ${shown} a job`}
                className="btn-ghost shrink-0 px-2.5 py-1.5 text-[11.5px]"
              >
                <Briefcase size={12} /> Give work
              </button>
            )}
          </div>

          {/* THE STANDALONE COMPOSITION'S ROW (v1.171.0), and ONLY that one.
              With a rail on screen this block was a SECOND portrait, name and
              kind pill for the agent already drawn a few rows above — the
              "redundant agent on the left pane" the user reported. In rail mode
              every one of these lines now lives on the selected ROW. Without a
              rail (no `onSelect`) there is no row to put them on, so the block
              stays exactly as it shipped. */}
          {!onSelect && (
          <div
            className={`flex items-start gap-2.5 px-4 pb-3.5 pt-2.5 ${
              offline ? "opacity-55" : ""
            }`}
          >
            {/* v1.171.0: the deterministic face (portrait wins when stored).
                Mood stays "idle". There IS a live busy signal since v1.193.0,
                but it rides in the pill beside the name, in words: a mood swap
                would encode it only in a drawing, and a drawing has no way to
                say "no claim" — which is what this signal reports for every
                idle agent AND for every delegated child it cannot see. */}
            <AgentFace
              name={shown}
              mood="idle"
              size={30}
              avatarUrl={
                selected.avatar
                  ? rosterAvatarSrc(selected.avatar, selected.last_active)
                  : undefined
              }
              className="mt-0.5"
            />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                <span
                  className="truncate text-[13px] font-medium text-zinc-100"
                  title={selected.name}
                >
                  {shown}
                </span>
                <span
                  className={`shrink-0 rounded-md border px-1.5 py-0.5 text-[10px] font-medium ${kindPill}`}
                >
                  {kindLabel}
                </span>
                {/* Same liveness marker as the rail rows (v1.193.0) — this
                    composition has no rail to carry it. */}
                {selectedLive && (
                  <LivePill
                    state={selectedLive}
                    bare={shown}
                    testId={`roster-activity-${shown}`}
                  />
                )}
                {offline && (
                  <span className="inline-flex shrink-0 items-center gap-1 rounded-md border border-rose-500/25 bg-rose-500/10 px-1.5 py-0.5 text-[10px] font-medium text-rose-300">
                    <WifiOff size={10} /> offline
                  </span>
                )}
                {!selected.delegable && (
                  <span className="shrink-0 text-[10px] text-zinc-600">
                    (chat-only for now)
                  </span>
                )}
                <span className="ml-auto shrink-0 text-[11px] tabular-nums text-zinc-500">
                  {statsText(selected)}
                </span>
              </div>
              {/* Messenger-style preview (v1.171.0): the agent's REAL last
                  round-table line + when, from the daemon's join — falls back
                  to the static description exactly as before when this agent
                  has no recorded activity. Never both: the preview IS the more
                  current answer to "what is this agent about right now". */}
              {selected.last_message ? (
                <p
                  data-testid="roster-preview"
                  className="mt-1 flex items-baseline gap-1.5 text-[11.5px] leading-relaxed"
                >
                  <span className="min-w-0 flex-1 truncate text-zinc-400">
                    {selected.last_message}
                  </span>
                  {selected.last_active && (
                    <span
                      data-testid="roster-when"
                      className="shrink-0 text-[10.5px] tabular-nums text-zinc-600"
                    >
                      {timeAgo(selected.last_active)}
                    </span>
                  )}
                </p>
              ) : selected.description ? (
                <p className="mt-1 text-[11.5px] leading-relaxed text-zinc-500">
                  {selected.description}
                </p>
              ) : null}
            </div>
          </div>
          )}
          </div>
          {/* ...end of the folded region. */}

          {/* ONE DOOR at the foot of the rail (v1.178.0). Local or remote is a
              question the setup surface itself asks — making the rail ask it
              first would mean two gears for one job. It lives outside the
              md-only column on purpose: the narrow layout needs it just as
              much.
              v1.179.0: it is the ONLY door — setup is not on the page until this
              is clicked ("the set up agents should all be contained in the new
              agent gear face ... and not shown unless the user decided to
              configure an agent") — so it announces what it controls. */}
          {onConfigure && (
            // REVIEW (v1.180.0): the divider is CONDITIONAL now. Folded, the
            // hidden body collapses to nothing and this `border-t` landed
            // directly under the header's `border-b` — two hairlines stacked
            // into one 2px rule, on the one composition whose whole point is
            // looking tidier.
            <div className={`p-1.5 ${folded ? "" : "border-t hairline"}`}>
              <button
                type="button"
                onClick={onConfigure}
                data-testid="roster-gear"
                aria-expanded={configureOpen}
                title="Configure a new agent — one of your own, or one running on another computer"
                className={`flex w-full items-center gap-2 rounded-xl px-2 py-1.5 text-left transition-colors hover:bg-white/[0.04] hover:text-accent-soft ${
                  configureOpen ? "text-accent-soft" : "text-zinc-500"
                }`}
              >
                <GearFace size={26} />
                <span className="min-w-0 flex-1 truncate text-[12.5px]">
                  New agent
                  {/* The button's job in full, for a screen reader that gets no
                      tooltip: the gear is aria-hidden and "New agent" alone
                      doesn't say that a REMOTE one lives behind the same door. */}
                  <span className="sr-only"> — configure a local or remote agent</span>
                </span>
              </button>
            </div>
          )}
        </Card>
      </div>
    </Reveal>
  );
}
