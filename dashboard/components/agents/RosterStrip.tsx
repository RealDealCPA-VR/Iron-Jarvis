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
//     (`lg:hidden` against the column's `hidden lg:block`), so exactly one of
//     the two is in the a11y tree at any viewport (display:none removes the
//     other) and a 380px-wide window gets a control that fits instead of a
//     15rem column eating the screen.
// The breakpoint is `lg`, not `md`, and app/agents/page.tsx's grid must agree:
// the round-table on that page splits at md with its OWN 16rem rail, so a
// 15rem rail at md left the transcript ~216px on a 768px window.
// The gear-with-a-face at the foot of the rail is ONE door to both setup
// surfaces (an agent of your own, or one on another computer).

import { type ReactElement, useState } from "react";
import { Briefcase, MessageCircle, WifiOff } from "lucide-react";
import { API_BASE, ijToken } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { timeAgo } from "@/lib/format";
import { Card } from "@/components/ui";
import { Reveal } from "@/components/motion";
import AgentFace from "@/components/agents/AgentFace";
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
}

/** <img> can't send the Authorization header — the token rides as ?token=,
 *  the same pattern every media surface uses (creative gallery, previews).
 *  `cacheKey` (the row's last_active — SetupCard's `rev` idea at low
 *  resolution) busts the browser cache after a portrait is replaced, so the
 *  roster never keeps rendering a stale image the daemon no longer serves. */
function avatarSrc(rel: string, cacheKey?: string | null): string {
  const token = ijToken();
  const v = encodeURIComponent(cacheKey || "0");
  return `${API_BASE}${rel}?v=${v}${token ? `&token=${encodeURIComponent(token)}` : ""}`;
}

const KIND_PILL: Record<AgentSource, string> = {
  builtin: "border-accent/30 bg-accent/[0.08] text-accent-soft",
  dynamic: "border-violet-500/25 bg-violet-500/10 text-violet-300",
  remote: "border-zinc-500/25 bg-zinc-500/10 text-zinc-400",
};

/** The shown name: the bare slug — the kind pill carries provenance, so the
 *  wire prefixes ("custom:", "remote:") stay off the screen. */
function bareName(name: string): string {
  if (name.startsWith("custom:")) return name.slice("custom:".length);
  if (name.startsWith("remote:")) return name.slice("remote:".length);
  return name;
}

/** The honest stats text. Prefer the daemon's own wording — the trailing
 *  parenthetical of its composed `line` ("87% over 23 runs", "no runs yet").
 *  "(offline)" is health, not stats — the row already shows an offline pill,
 *  so it falls through to the stats dict. Sample counts are ALWAYS visible;
 *  a percentage never renders bare. */
function statsText(e: RosterEntry): string {
  const paren = /\(([^()]+)\)\s*$/.exec(e.line ?? "")?.[1];
  if (paren && paren.trim().toLowerCase() !== "offline") return paren.trim();
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
function GearFace({ size = 26 }: { size?: number }): ReactElement {
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
  /** The Give-work button (v1.166.0): preselect this agent in the job-post
   *  card. Same delegable + healthy gate as Talk — a non-delegable entry
   *  (supervisor) stays chat-only, and an offline remote can't take work. */
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
  const offline = selected.kind === "remote" && !selected.healthy;
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

  return (
    <Reveal>
      <Card pad={false} className="overflow-hidden">
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

        {/* THE RAIL (lg and up). A column of faces you scan, not a list you
            read: name + face + health, and the detail for whichever one is
            selected lives once, below. Capped height with its own scroll so a
            30-agent roster can't push the gear off the bottom of the card. */}
        {onSelect && (
          <div
            data-testid="roster-rail"
            className="hidden max-h-[44vh] space-y-0.5 overflow-y-auto p-1.5 lg:block"
          >
            {entries.map((e) => {
              const active = isPick && e.name === selected.name;
              const off = e.kind === "remote" && !e.healthy;
              const bare = bareName(e.name);
              return (
                <button
                  key={e.name}
                  type="button"
                  // aria-current is how the selection is ANNOUNCED — the ring
                  // and the accent tint say it to the eye only, and this rail
                  // is the page's primary control now.
                  aria-current={active ? "true" : undefined}
                  onClick={() => choose(e)}
                  title={`${bare} — ${SOURCE_LABEL[e.kind] ?? e.kind}${
                    off ? " (offline)" : ""
                  }${!e.delegable ? " (chat-only)" : ""}`}
                  className={`flex w-full items-center gap-2 rounded-xl border px-2 py-1.5 text-left transition-colors ${
                    active
                      ? "border-accent/25 bg-accent/[0.08]"
                      : "border-transparent hover:bg-white/[0.04]"
                  }`}
                >
                  <AgentFace
                    name={bare}
                    mood="idle"
                    size={26}
                    // title="" = decorative: the visible name beside it is
                    // already the accessible name of this button.
                    title=""
                    avatarUrl={e.avatar ? avatarSrc(e.avatar, e.last_active) : undefined}
                    className={off ? "opacity-50" : ""}
                  />
                  <span
                    className={`min-w-0 flex-1 truncate text-[12.5px] ${
                      active ? "text-accent-soft" : "text-zinc-300"
                    }`}
                  >
                    {bare}
                  </span>
                  {off && (
                    <>
                      <WifiOff size={11} className="shrink-0 text-rose-300/80" aria-hidden />
                      {/* The icon is colour+shape only; the word has to reach
                          a screen reader too. */}
                      <span className="sr-only">offline</span>
                    </>
                  )}
                </button>
              );
            })}
          </div>
        )}

        <div className="flex flex-wrap items-center gap-2 px-4 pt-3 lg:pt-2.5">
          {/* The narrow-width form of the rail above (and the whole picker on
              a page that supplies no onSelect). `lg:hidden` keeps exactly one
              of the two in the a11y tree — and it must stay in lock-step with
              the column's `hidden lg:block` and with app/agents/page.tsx's
              `lg:grid-cols-[15rem_...]`, or a viewport band gets either two
              pickers or none. (Was `md` — see the page's comment: at 768px the
              rail and the round-table's own 16rem rail engaged together and
              left the transcript ~216px.) */}
          <label className={`sr-only ${onSelect ? "lg:hidden" : ""}`} htmlFor="roster-pick">
            Choose an agent
          </label>
          <select
            id="roster-pick"
            value={selected.name}
            onChange={(ev) => {
              const next = entries.find((e) => e.name === ev.target.value);
              if (next) choose(next);
            }}
            className={`field min-w-0 flex-1 py-1.5 text-[12.5px] ${
              onSelect ? "lg:hidden" : ""
            }`}
          >
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
          {onTalk && selected.delegable && selected.healthy && (
            <button
              type="button"
              onClick={() => onTalk(selected.kind, shown)}
              title={`Talk with ${shown} at the round-table`}
              className="btn-ghost shrink-0 px-2.5 py-1.5 text-[11.5px]"
            >
              <MessageCircle size={12} /> Talk
            </button>
          )}
          {onAssign && selected.delegable && selected.healthy && (
            <button
              type="button"
              onClick={() => onAssign(selected.kind, shown)}
              title={`Give ${shown} a job via the job-post card`}
              className="btn-ghost shrink-0 px-2.5 py-1.5 text-[11.5px]"
            >
              <Briefcase size={12} /> Give work
            </button>
          )}
        </div>

        <div
          className={`flex items-start gap-2.5 px-4 pb-3.5 pt-2.5 ${
            offline ? "opacity-55" : ""
          }`}
        >
          {/* v1.171.0: the deterministic face (portrait wins when stored).
              Mood stays "idle" — the roster carries no live busy signal, and
              an invented "work" scan would be the dishonest kind of warmth. */}
          <AgentFace
            name={shown}
            mood="idle"
            size={30}
            avatarUrl={
              selected.avatar
                ? avatarSrc(selected.avatar, selected.last_active)
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

        {/* ONE DOOR at the foot of the rail (v1.178.0). Local or remote is a
            question the setup surface itself asks — making the rail ask it
            first would mean two gears for one job. It lives outside the
            md-only column on purpose: the narrow layout needs it just as
            much. */}
        {onConfigure && (
          <div className="border-t hairline p-1.5">
            <button
              type="button"
              onClick={onConfigure}
              data-testid="roster-gear"
              title="Configure a new agent — one of your own, or one running on another computer"
              className="flex w-full items-center gap-2 rounded-xl px-2 py-1.5 text-left text-zinc-500 transition-colors hover:bg-white/[0.04] hover:text-accent-soft"
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
    </Reveal>
  );
}
