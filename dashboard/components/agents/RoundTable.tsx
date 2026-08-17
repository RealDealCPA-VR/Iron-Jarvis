"use client";

// The round-table: the open thread's conversation surface. Header: title +
// the panel (participant chips) + Edit panel. Messages: user turns
// right-aligned and accent-tinted; agent turns left-aligned with the agent's
// avatar, name (in its deterministic hue), role pill, and kind badge. An
// entry with an `error` is an honest per-agent failure rendered as a muted
// note ("hermes-mac-mini couldn't answer: …") — never hidden, never an empty
// bubble pretending to be an answer.
//
// LIVE ROUNDS: POST /say blocks for the whole round, but the daemon persists
// each speaker's entry as it lands and announces it as an
// `agent_thread.updated` event on the /events socket. Matching events refetch
// the open thread so replies appear one by one (the comm-thread live pattern
// from chat), and the blocking /say response is the final reconciliation.
// A mid-round reload simply shows the persisted-so-far entries — they're in
// the DB — and the next event or send catches up.
//
// DIRECTING: "@name" (or "@role") in the message makes only the mentioned
// participants speak; no mention → everyone. The composer offers an
// @-autocomplete popover over the thread's participants.
//
// MEMORY (v1.178.0): "Extract and add to memory" commits what the panel
// concluded to long-term memory — but only AFTER the user reads it. The first
// POST is a preview that writes nothing; the commit sends the previewed text
// back verbatim. See MemoryReviewCard for why both halves are load-bearing.
//
// ADD AN AGENT (v1.179.0): a thread is a ROOM, so bringing somebody else into
// it belongs IN the room — bottom right of the composer, next to "Let them
// continue" — not only behind the header's panel editor. The control reuses
// THE picker (PanelPicker) rather than growing a second one, seeded with
// everyone already seated, so the PUT it sends carries the FULL panel and
// adding is additive by construction: a body of just the new agent would
// REPLACE the panel, since PUT /agents/threads/{id}/participants sets the list
// (daemon `update_participants`). Nothing is shown as added until the daemon
// says so — the response (or a refetch) is what lands on screen, and a failed
// add leaves the thread exactly as it was, with the daemon's reason in the
// picker. The catalog is fetched on demand from the same roster the rail
// shows, with the /agents + /agents/remote fallback older daemons need.

import {
  createContext,
  isValidElement,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";
import {
  Brain,
  Check,
  Copy,
  LoaderCircle,
  MessagesSquare,
  Send,
  TriangleAlert,
  UserRoundPen,
  UserRoundPlus,
} from "lucide-react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { ApiError, get, post, put } from "@/lib/api";
import { useEvents } from "@/lib/useEvents";
import { timeAgo } from "@/lib/format";
import { Empty, ErrorNote, OfflineHint, SkeletonRows, SuccessNote } from "@/components/ui";
import AgentFace, { faceIdentity } from "./AgentFace";
import { PanelPicker, type PickerCatalog, type PickerOption } from "./PanelPicker";
import {
  RolePill,
  SOURCE_LABEL,
  SourceIcon,
  nameColor,
  type AgentSource,
  type Participant,
  type RemoteAgentInfo,
  type ThreadDetail,
  type ThreadEntry,
} from "./identity";

/* ------------------------------------------------------------- markdown --- */
/* Same pattern as the chat page's Markdown (kept local — pages don't import
 * across each other): GFM, styled blocks, copyable code fences. */

function nodeText(node: ReactNode): string {
  if (node === null || node === undefined || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number" || typeof node === "bigint")
    return String(node);
  if (Array.isArray(node)) return node.map(nodeText).join("");
  if (isValidElement(node))
    return nodeText((node as ReactElement<{ children?: ReactNode }>).props.children);
  return "";
}

function CopyIconButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const timerRef = useRef<number | null>(null);
  useEffect(
    () => () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    },
    [],
  );
  function copy() {
    navigator.clipboard
      .writeText(text)
      .then(() => {
        setCopied(true);
        if (timerRef.current !== null) window.clearTimeout(timerRef.current);
        timerRef.current = window.setTimeout(() => setCopied(false), 1500);
      })
      .catch(() => {
        /* clipboard unavailable */
      });
  }
  return (
    <button
      type="button"
      onClick={copy}
      title="Copy code"
      aria-label="Copy code"
      className="absolute right-2 top-2 z-10 grid h-6 w-6 place-items-center rounded-md border border-white/10 bg-white/[0.06] text-zinc-400 opacity-0 transition-opacity hover:text-zinc-100 focus-visible:opacity-100 group-hover/code:opacity-100"
    >
      {copied ? <Check size={12} className="text-emerald-400" /> : <Copy size={12} />}
    </button>
  );
}

const PreContext = createContext(false);

function MarkdownPre({ children }: { children?: ReactNode }) {
  const text = nodeText(children).replace(/\n$/, "");
  return (
    <div className="group/code relative my-2">
      <CopyIconButton text={text} />
      <PreContext.Provider value={true}>
        <pre className="overflow-x-auto rounded bg-black/40 p-3 font-mono text-xs leading-relaxed text-zinc-200">
          {children}
        </pre>
      </PreContext.Provider>
    </div>
  );
}

function MarkdownCode({ className, children }: { className?: string; children?: ReactNode }) {
  const inPre = useContext(PreContext);
  if (inPre) return <code className={className}>{children}</code>;
  return (
    <code className="rounded bg-white/[0.08] px-1.5 py-0.5 font-mono text-[0.85em] text-accent-soft">
      {children}
    </code>
  );
}

const MD_COMPONENTS: Components = {
  h1: ({ children }) => (
    <h1 className="mb-1.5 mt-3 text-base font-semibold text-zinc-100 first:mt-0">{children}</h1>
  ),
  h2: ({ children }) => (
    <h2 className="mb-1.5 mt-3 text-[15px] font-semibold text-zinc-100 first:mt-0">{children}</h2>
  ),
  h3: ({ children }) => (
    <h3 className="mb-1 mt-2.5 text-sm font-semibold text-zinc-100 first:mt-0">{children}</h3>
  ),
  p: ({ children }) => <p className="my-1.5 leading-relaxed first:mt-0 last:mb-0">{children}</p>,
  ul: ({ children }) => <ul className="my-1.5 list-disc space-y-1 pl-5">{children}</ul>,
  ol: ({ children }) => <ol className="my-1.5 list-decimal space-y-1 pl-5">{children}</ol>,
  li: ({ children }) => <li className="leading-relaxed [&>p]:my-0">{children}</li>,
  table: ({ children }) => (
    <div className="my-2 overflow-x-auto">
      <table className="w-full border-collapse text-[13px]">{children}</table>
    </div>
  ),
  th: ({ children }) => (
    <th className="border border-white/10 bg-white/[0.05] px-2.5 py-1.5 text-left font-medium text-zinc-100">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="border border-white/10 px-2.5 py-1.5 align-top text-zinc-300">{children}</td>
  ),
  a: ({ children, href }) => (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="text-accent-soft underline decoration-accent/40 underline-offset-2 transition-colors hover:decoration-accent"
    >
      {children}
    </a>
  ),
  blockquote: ({ children }) => (
    <blockquote className="my-2 border-l-2 border-accent/40 pl-3 text-zinc-400 [&>p]:my-0.5">
      {children}
    </blockquote>
  ),
  hr: () => <hr className="my-3 border-white/10" />,
  strong: ({ children }) => <strong className="font-semibold text-zinc-100">{children}</strong>,
  pre: MarkdownPre,
  code: MarkdownCode,
  img: ({ src, alt }) => (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={typeof src === "string" ? src : undefined}
      alt={alt || "image"}
      loading="lazy"
      className="my-2 max-h-96 w-auto max-w-full rounded-xl border border-white/10"
    />
  ),
};

const REMARK_PLUGINS = [remarkGfm];

function Markdown({ content }: { content: string }) {
  return (
    <ReactMarkdown remarkPlugins={REMARK_PLUGINS} components={MD_COMPONENTS}>
      {content}
    </ReactMarkdown>
  );
}

/* ------------------------------------------------------------- mentions --- */

/** The daemon's mention grammar, ported from agents/threads.py _MENTION_RE:
 *  `@"quoted name"` (for names with spaces) or a bare token of
 *  letters/digits/`._-` starting with a letter/digit. The lookbehind rejects
 *  an `@` glued to the tail of a word — `planner@critic.io` is an address,
 *  never a mention, so an email in the message can't shrink the prediction. */
const MENTION_RE = /(?<![A-Za-z0-9._-])@(?:"([^"]+)"|([A-Za-z0-9][A-Za-z0-9._-]*))/g;

/** Mention tokens, normalized the way run_round normalizes them: bare tokens
 *  shed trailing `.`/`_`/`-` (sentence punctuation — "@builder." still
 *  targets builder), quoted tokens are verbatim; lowercased for the
 *  case-insensitive equality match. */
export function mentionTokens(message: string): string[] {
  const out: string[] = [];
  for (const m of message.matchAll(MENTION_RE)) {
    const t = (m[1] ?? (m[2] ?? "").replace(/[._-]+$/, "")).trim().toLowerCase();
    if (t) out.push(t);
  }
  return out;
}

/** Who the round will call on — the client-side PREDICTION of the daemon's
 *  rule (threads.py `_mentioned`): a token must EQUAL, case-insensitively, a
 *  participant's name, its role, or the name part of its key. Returns the
 *  matches in panel order; [] when no token matched anyone (→ everyone
 *  speaks). Cosmetic — the /say response is still the truth. */
export function predictSpeakers(
  message: string,
  participants: Participant[],
): Participant[] {
  const tokens = mentionTokens(message);
  if (tokens.length === 0) return [];
  return participants.filter((p) => {
    const colon = p.key.indexOf(":");
    const aliases = new Set(
      [p.name, p.role || "", colon >= 0 ? p.key.slice(colon + 1) : p.key]
        .map((a) => a.trim().toLowerCase())
        .filter(Boolean),
    );
    return tokens.some((t) => aliases.has(t));
  });
}

/** Write a mention the way the daemon will read it back as one: bare when
 *  the name survives the bare-token grammar (and won't lose a trailing
 *  `.`/`_`/`-` to punctuation-trimming), quoted otherwise — a name with a
 *  space is only addressable as `@"Full Name"`. */
export function mentionText(name: string): string {
  return /^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(name) && !/[._-]$/.test(name)
    ? `@${name}`
    : `@"${name}"`;
}

/* ------------------------------------------------------------- identity --- */

/** THE face seed for a participant key (v1.171.0): the BARE NAME.
 *
 *  A key is "<source>:<name>". TeamTree and the kanban seed faces by the bare
 *  agent_type ("builder"); seeding thread surfaces by the full key
 *  ("builtin:builder") hashed to a DIFFERENT shape/color, so the same agent
 *  wore a different face on session surfaces than on thread surfaces —
 *  defeating AgentFace's "same name → same face, everywhere" premise. Every
 *  key-only call site (transcript entries, round chips) seeds through this;
 *  sites that hold a Participant seed by `p.name`, which participantKey()
 *  guarantees is the same string. Stripping only the FIRST colon matches how
 *  the display name has always been derived from a key. */
// Canonical seed now lives with the face itself (coordinator relocation);
// re-exported so existing imports from this module keep working.
export { faceIdentity };

/* -------------------------------------------------------------- entries --- */

function UserBubble({ content, at }: { content: string; at?: string }) {
  return (
    <div className="flex justify-end">
      <div
        title={at ? timeAgo(at) : undefined}
        className="max-w-[80%] whitespace-pre-wrap rounded-2xl border border-accent/25 bg-accent/[0.1] px-4 py-2.5 text-sm leading-relaxed text-zinc-100"
      >
        {content}
      </div>
    </div>
  );
}

function KindBadge({ source }: { source?: string }) {
  const label = SOURCE_LABEL[(source ?? "") as AgentSource] ?? (source || "agent");
  return (
    <span className="inline-flex shrink-0 items-center gap-1 rounded-md border border-white/10 bg-white/[0.04] px-1.5 py-px text-[9px] font-medium uppercase tracking-wide text-zinc-400">
      <SourceIcon source={source} size={9} /> {label}
    </span>
  );
}

function AgentTurn({ entry, byKey }: { entry: ThreadEntry; byKey: Map<string, Participant> }) {
  const p = byKey.get(entry.who);
  // "<source>:<name>" → the name; a key without a colon renders as-is.
  const name = p?.name ?? faceIdentity(entry.who);
  const role = entry.role ?? p?.role;
  const source = entry.source ?? p?.source;
  const content = (entry.content ?? "").trim();
  return (
    <div className="flex gap-3">
      {/* The speaker's face (v1.171.0) — seeded by the BARE name
          (faceIdentity) so this is the SAME face the agent wears on TeamTree
          and the kanban. An entry that carries an error shows the honest X-X
          eyes; a landed reply just sits idle — no mood is ever invented.
          Decorative (title="" + aria-hidden): the speaker's name is the
          visible label right beside it, and a duplicate SVG <title> text node
          would double every get-by-text AND read the name twice to a screen
          reader. */}
      <span aria-hidden="true" className="contents">
        <AgentFace
          name={faceIdentity(entry.who)}
          title=""
          mood={entry.error ? "error" : "idle"}
          size={26}
          className="mt-0.5"
        />
      </span>
      <div className="min-w-0 max-w-[85%]">
        <div className="mb-1 flex flex-wrap items-center gap-1.5">
          <span className="text-xs font-semibold" style={{ color: nameColor(entry.who) }}>
            {name}
          </span>
          <RolePill role={role} />
          <KindBadge source={source} />
          <span className="text-[10px] text-zinc-600">{timeAgo(entry.at)}</span>
        </div>
        {entry.error ? (
          // An honest per-agent failure — a muted note, never hidden and never
          // a fabricated answer.
          <div className="flex items-start gap-2 rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2 text-xs italic leading-relaxed text-zinc-400">
            <TriangleAlert size={13} className="mt-0.5 shrink-0 text-amber-300/70" aria-hidden="true" />
            <span>{entry.error}</span>
          </div>
        ) : content ? (
          <div className="rounded-2xl border border-white/[0.06] bg-white/[0.03] px-4 py-2.5 text-sm leading-relaxed text-zinc-200">
            <Markdown content={content} />
          </div>
        ) : (
          <p className="text-xs italic text-zinc-600">(empty reply)</p>
        )}
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- memory --- */

/** What `POST /agents/threads/{id}/remember` answers — the preview AND the
 *  commit (same shape; the commit carries `preview:false` and a filled `ref`).
 *
 *  EVERY FIELD IS OPTIONAL ON PURPOSE. A daemon older than v1.178.0 has no such
 *  route at all (the call 404s and the message is shown), and this UI must also
 *  survive a response that simply omits something: `items` missing is not "no
 *  claims", `distilled` missing is not "not distilled". Each read below states
 *  its own degrade. */
interface RememberResult {
  /** true = nothing was written. The daemon defaults to true; we still send it
   *  explicitly, so a future default flip cannot turn a look into a write. */
  preview?: boolean;
  /** Where the note landed (a commit only). */
  ref?: string;
  /** The memory base the note goes to ("" = the default brain). */
  source?: string;
  mode?: string;
  /** FALSE = a real model never ran; the body is a verbatim excerpt. */
  distilled?: boolean;
  /** The daemon's own words about a degrade. Always shown when present. */
  note?: string;
  title?: string;
  messages?: number;
  participants?: string[];
  /** The extracted claims, flat — what the user actually reads. May carry a
   *  truncation marker as its LAST entry; that marker is an item like any
   *  other and is rendered, never filtered. */
  items?: string[];
  /** The exact text that would land. The commit sends this back verbatim. */
  content?: string;
  provider?: string;
}

/** An honest message for a failed call: status 0 is the daemon being gone
 *  (lib/api maps a dead fetch to 0), anything else is the daemon's own words —
 *  relayed, never replaced by a friendlier sentence that hides the reason. */
function failureText(e: unknown, offline: string): string {
  if (e instanceof ApiError) return e.status === 0 ? offline : e.message;
  return String(e);
}

/** The review step: what WOULD be committed, before anything is.
 *
 *  TWO THINGS HERE ARE THE FEATURE, not decoration:
 *
 *  1. THE COMMIT SENDS `content` BACK. A bare `preview:false` re-runs the whole
 *     distillation server-side, and a model asked twice does not answer twice
 *     the same — so the text the user approved would not be the text stored,
 *     and every later turn would quote back something nobody read. If the
 *     preview carried no `content` (an unexpected/older shape) we REFUSE to
 *     commit rather than fire the blind call: silently storing unreviewed
 *     agent-written text is the exact failure this whole screen exists to
 *     prevent.
 *  2. `distilled === false` IS SAID OUT LOUD. With no real model connected the
 *     daemon degrades to a verbatim excerpt. Rendering that identically to a
 *     real distillation would let the user believe a summary was written when
 *     it was not, so the note rides in amber, above the fold, before the
 *     button — not in a receipt afterwards. */
function MemoryReviewCard({
  result,
  committing,
  error,
  onCommit,
  onDiscard,
}: {
  result: RememberResult;
  committing: boolean;
  error: string | null;
  onCommit: () => void;
  onDiscard: () => void;
}) {
  const items = result.items ?? [];
  const content = (result.content ?? "").trim();
  const approvable = content !== "";
  // Explicit false only — an ABSENT flag is unknown, not a denial, and
  // accusing a real distillation of being an excerpt is its own lie.
  const degraded = result.distilled === false;
  return (
    <section
      aria-label="Review what will be added to memory"
      className="border-b hairline bg-white/[0.02] px-4 py-3"
    >
      <div className="flex flex-wrap items-center gap-2">
        <Brain size={14} className="shrink-0 text-accent-soft" aria-hidden="true" />
        <h3 className="text-xs font-semibold uppercase tracking-[0.12em] text-zinc-300">
          Add to memory — read it first
        </h3>
        <span className="text-[11px] text-zinc-500">
          {result.title ? `“${result.title}”` : "this panel"}
          {result.source ? ` → ${result.source}` : ""}
          {typeof result.messages === "number" ? ` · ${result.messages} messages` : ""}
        </span>
      </div>
      <p className="mt-1 text-[11px] text-zinc-500">
        Nothing has been written yet — this is what would be saved.
      </p>

      {degraded && (
        // The honesty signal. `note` is the daemon's own sentence and is
        // preferred verbatim; the fallback covers a response that set the flag
        // without one, because the FLAG is the claim that must not go unsaid.
        <p className="mt-2 flex items-start gap-2 rounded-lg border border-amber-400/25 bg-amber-400/[0.06] px-3 py-2 text-[11px] leading-relaxed text-amber-200/90">
          <TriangleAlert size={13} className="mt-px shrink-0" aria-hidden="true" />
          <span>
            {result.note ||
              "No real model was connected — this is a verbatim excerpt, not a distillation."}
          </span>
        </p>
      )}
      {!degraded && result.note && (
        // A note WITHOUT the degrade flag still says something true about how
        // this text was produced (a failed distillation, an approved commit) —
        // never swallowed just because the amber case didn't fire.
        <p className="mt-2 text-[11px] leading-relaxed text-zinc-400">{result.note}</p>
      )}

      {items.length > 0 ? (
        <ul className="mt-2 max-h-52 space-y-1 overflow-y-auto pr-1 text-xs leading-relaxed text-zinc-300">
          {items.map((it, i) => (
            <li key={i} className="flex gap-2">
              <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-accent/60" aria-hidden="true" />
              {/* A truncation marker is an ITEM: the daemon caps the list and
                  says so in the last entry. Hiding it would show the user less
                  than what lands — the one thing a preview may never do. */}
              <span className={it.startsWith("[") ? "italic text-zinc-500" : ""}>{it}</span>
            </li>
          ))}
        </ul>
      ) : approvable ? (
        <p className="mt-2 text-xs italic text-zinc-500">
          This daemon listed no separate items — read the full text below before
          saving.
        </p>
      ) : (
        // …and when there is no text below either, saying "read the full text
        // below" points at nothing: the <details> only renders when there IS
        // something to approve. A review screen that describes content it is
        // not showing is the same lie as one that shows less than what lands,
        // just quieter (reviewer finding).
        <p className="mt-2 text-xs italic text-zinc-500">
          This preview carried nothing to read — no items and no text.
        </p>
      )}

      {approvable && (
        <details className="mt-2">
          <summary className="cursor-pointer text-[11px] text-zinc-500 hover:text-zinc-300">
            Show the exact text that will be saved
          </summary>
          <pre className="mt-1.5 max-h-56 overflow-auto whitespace-pre-wrap rounded-lg bg-black/40 p-3 font-mono text-[11px] leading-relaxed text-zinc-300">
            {content}
          </pre>
        </details>
      )}

      {error && <div className="mt-2"><ErrorNote>{error}</ErrorNote></div>}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={onCommit}
          disabled={committing || !approvable}
          className="btn-accent py-1 text-xs"
          title={
            approvable
              ? "Save exactly the text above to long-term memory"
              : "The preview carried no text — nothing can be approved"
          }
        >
          {committing ? (
            <LoaderCircle size={13} className="animate-spin-slow" aria-hidden="true" />
          ) : (
            <Brain size={13} aria-hidden="true" />
          )}
          Save to memory
        </button>
        <button type="button" onClick={onDiscard} disabled={committing} className="btn-ghost py-1 text-xs">
          Discard
        </button>
        {!approvable && (
          <span className="text-[11px] text-amber-200/80">
            The preview returned no text to approve, so saving is blocked — a
            blind save would store something you never read.
          </span>
        )}
      </div>
    </section>
  );
}

/* -------------------------------------------------------- add an agent --- */

/** The roster rows this surface reads (GET /agents/roster). Deliberately a
 *  LOCAL minimal shape rather than the rail's full RosterEntry: everything
 *  here is optional-with-a-degrade, so a daemon that omits a field renders
 *  less, never a lie — an absent `healthy` means "not reported", which must
 *  not paint an agent offline. */
interface RosterRow {
  /** "builder" | "custom:<slug>" | "remote:<name>" — the delegation name. */
  name: string;
  kind: AgentSource;
  description?: string;
  healthy?: boolean;
  avatar?: string | null;
}

/** "custom:slug" / "remote:name" → the bare registry name the thread routes
 *  accept (`clean_participants` stores source + bare name, and the round
 *  engine looks the bare name up in the matching registry). Builtins have no
 *  prefix and pass through. Same transform the page applies for its own
 *  picker — it lives there as a module-private function, so this is a second
 *  copy on purpose rather than an import that would reach into a page. */
function bareRosterName(name: string): string {
  if (name.startsWith("custom:")) return name.slice("custom:".length);
  if (name.startsWith("remote:")) return name.slice("remote:".length);
  return name;
}

/** The full agent catalog for the add-picker: all three sources the app
 *  supports, exactly as the rail sees them.
 *
 *  ROSTER FIRST (descriptions + live remote health + stored portraits), then
 *  the raw lists a pre-roster daemon still serves. The fallback is not
 *  belt-and-braces: on a daemon without /agents/roster an empty catalog would
 *  tell the user they have no agents at all, while `/agents` answers happily.
 *  A dead daemon (status 0) is re-thrown instead of quietly falling through —
 *  the caller says "offline" rather than showing three empty groups. */
async function loadCatalog(): Promise<PickerCatalog> {
  try {
    const res = await get<{ roster?: RosterRow[] }>("/agents/roster");
    const rows = (res?.roster ?? []).filter(
      (e): e is RosterRow => Boolean(e) && typeof e?.name === "string",
    );
    if (rows.length > 0) {
      const options = (kind: AgentSource): PickerOption[] =>
        rows
          .filter((e) => e.kind === kind)
          .map((e) => ({
            source: kind,
            name: bareRosterName(e.name),
            description: e.description || undefined,
            // Absent health is UNKNOWN, not offline (older daemons don't
            // send it) — only an explicit false locks a remote out.
            offline: kind === "remote" && e.healthy === false,
            avatar: e.avatar ?? null,
          }));
      return {
        builtin: options("builtin"),
        dynamic: options("dynamic"),
        remotes: options("remote"),
      };
    }
  } catch (e) {
    if (e instanceof ApiError && e.status === 0) throw e;
    /* 404/405 = a daemon older than the roster — fall through to the lists */
  }
  const agents = await get<{
    builtin?: string[];
    dynamic?: { name: string; description?: string }[];
  }>("/agents");
  // A daemon without the remote registry still has built-ins and dynamics;
  // losing them over a missing route would be the worse failure.
  const remote = await get<{ agents?: RemoteAgentInfo[]; remotes?: RemoteAgentInfo[] }>(
    "/agents/remote",
  ).catch(() => ({}) as { agents?: RemoteAgentInfo[]; remotes?: RemoteAgentInfo[] });
  const remotes = remote?.agents ?? remote?.remotes ?? [];
  return {
    builtin: (agents?.builtin ?? []).map((name) => ({ source: "builtin" as const, name })),
    dynamic: (agents?.dynamic ?? []).map((a) => ({
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
}

/** Who is on the picker's list but was not on the thread before — used only
 *  for the receipt ("Added X"), never to decide what is SENT. */
function addedNames(before: Participant[], after: Participant[]): string[] {
  const seated = new Set(before.map((p) => p.key));
  return after.filter((p) => !seated.has(p.key)).map((p) => p.name);
}

/* ----------------------------------------------------------------- view --- */

/** One round in flight: the message count when it started (everything after
 *  that index landed THIS round) and who we expect to speak. */
interface RoundState {
  base: number;
  expected: string[];
}

interface SayResponse {
  entries: ThreadEntry[];
  /** New-additive on the /say response: who actually spoke / was skipped. */
  spoke?: string[];
  skipped?: string[];
}

export function RoundTable({
  threadId,
  reloadNonce,
  onEditPanel,
  onRoundDone,
}: {
  threadId: string;
  /** Bump to refetch the transcript (e.g. after the panel was edited). */
  reloadNonce: number;
  onEditPanel: (detail: ThreadDetail) => void;
  /** A speaking round finished — refresh the thread rail counts. */
  onRoundDone: () => void;
}) {
  const [detail, setDetail] = useState<ThreadDetail | null>(null);
  const [loadError, setLoadError] = useState<ApiError | null>(null);
  const [input, setInput] = useState("");
  const [round, setRound] = useState<RoundState | null>(null);
  const [pendingUser, setPendingUser] = useState<string | null>(null);
  const [sayError, setSayError] = useState<string | null>(null);
  // MEMORY (v1.178.0). Four separate pieces on purpose: `memPreview` is the
  // text the user is reading and NOTHING is written while it is set;
  // `memSaved` is the commit receipt; `memBusy` names which call is in flight
  // (a preview and a commit must not read as the same wait); `memError` is
  // kept OUTSIDE the preview so a failed commit leaves the preview standing
  // exactly as it was read — nothing about the thread changes on failure.
  const [memPreview, setMemPreview] = useState<RememberResult | null>(null);
  const [memSaved, setMemSaved] = useState<RememberResult | null>(null);
  const [memBusy, setMemBusy] = useState<"preview" | "commit" | null>(null);
  const [memError, setMemError] = useState<string | null>(null);
  // ADD AN AGENT (v1.179.0). `addCatalog !== null` is what opens the picker —
  // one state instead of an open flag plus a catalog, so the dialog can never
  // stand there claiming "no built-in agents available" while its fetch is
  // still in flight. `addError` is the catalog fetch's failure (a failed PUT
  // is shown inside the picker, where the user is standing); `addNote` is the
  // receipt for an add that actually landed.
  const [addCatalog, setAddCatalog] = useState<PickerCatalog | null>(null);
  const [addLoading, setAddLoading] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);
  const [addNote, setAddNote] = useState<string | null>(null);
  // The @-autocomplete popover: the partial after "@" and where it starts.
  const [mention, setMention] = useState<{ query: string; start: number } | null>(null);
  const [mentionIdx, setMentionIdx] = useState(0);

  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  // Bumped on thread switch so a slow round from the OLD thread can't land here.
  const genRef = useRef(0);
  // Mirrors `round !== null` for async closures (refetchLive's shrink guard
  // must read the CURRENT round state, not the one it closed over).
  const speakingRef = useRef(false);
  // The latest committed detail — async code (reconciliation) reads lengths
  // from here instead of a stale closure.
  const detailRef = useRef<ThreadDetail | null>(null);

  const speaking = round !== null;

  useEffect(() => {
    genRef.current += 1;
    let cancelled = false;
    setDetail(null);
    setLoadError(null);
    setSayError(null);
    setPendingUser(null);
    setRound(null);
    speakingRef.current = false;
    setInput("");
    setMention(null);
    // A preview belongs to the thread it was extracted from. Carrying one
    // across a thread switch would offer a Save that writes ANOTHER panel's
    // text under this thread's id.
    setMemPreview(null);
    setMemSaved(null);
    setMemBusy(null);
    setMemError(null);
    // Same reason the preview is dropped: an open add-picker belongs to the
    // thread it was opened on, and its Save PUTs to whatever thread is open —
    // which would rewrite ANOTHER panel with this one's seating.
    setAddCatalog(null);
    setAddLoading(false);
    setAddError(null);
    setAddNote(null);
    get<ThreadDetail>(`/agents/threads/${encodeURIComponent(threadId)}`)
      .then((d) => {
        if (!cancelled) setDetail(d);
      })
      .catch((e: unknown) => {
        if (!cancelled)
          setLoadError(e instanceof ApiError ? e : new ApiError(String(e), 500));
      });
    return () => {
      cancelled = true;
    };
  }, [threadId, reloadNonce]);

  const messages = detail?.messages ?? [];
  const participants = detail?.participants ?? [];
  const byKey = new Map(participants.map((p) => [p.key, p]));
  detailRef.current = detail;

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.length, speaking, pendingUser]);

  /** Re-pull the open thread after the daemon persisted a speaker's entry
   *  mid-round (agent_thread.updated). Replace-only — the server's array is
   *  truth. MID-ROUND it's never applied when it would SHRINK the transcript
   *  (a stale in-flight response from earlier in the round must not roll
   *  replies back); once the round is over the server wins UNCONDITIONALLY —
   *  that's the escape hatch that heals a transcript inflated by a concurrent
   *  round in another tab or shifted by the message-cap trim. */
  async function refetchLive() {
    const gen = genRef.current;
    try {
      const t = await get<ThreadDetail>(`/agents/threads/${encodeURIComponent(threadId)}`);
      if (genRef.current !== gen) return; // switched threads mid-fetch
      setDetail((d) => {
        if (!d) return t;
        if (speakingRef.current && (t.messages?.length ?? 0) < d.messages.length) return d;
        return t;
      });
    } catch {
      /* quiet — the next event or the blocking /say response catches up */
    }
  }

  // LIVE ROUND UPDATES: the daemon announces every persisted speaker entry as
  // an agent_thread.updated event; new frames for THIS thread refetch it so
  // replies render progressively. The seen-boundary is an event id so a
  // re-render never re-processes old frames into refetch loops (the same
  // pattern chat uses for live comm threads).
  const { events } = useEvents(120);
  const eventSeenRef = useRef<string | null>(null);
  useEffect(() => {
    const newest = events[0];
    if (!newest) return;
    const boundary = eventSeenRef.current;
    eventSeenRef.current = newest.id;
    let stale = false;
    for (const e of events) {
      if (e.id === boundary) break; // frames already processed
      if (e.type !== "agent_thread.updated") continue;
      const tid = (e.payload as { thread_id?: unknown } | null)?.thread_id;
      if (typeof tid === "string" && tid === threadId) stale = true;
    }
    if (stale) void refetchLive();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [events]);

  /** One speaking round. Empty message = "let them continue" (the agents take
   *  another round among themselves). "@name" directs the round to just the
   *  mentioned participants. Blocks until the round ends; entries stream in
   *  live via agent_thread.updated, and the response is the reconciliation. */
  async function say(raw: string) {
    if (speaking || !detail) return;
    const message = raw.trim();
    const gen = genRef.current;
    const base = detail.messages.length;
    // The last pre-round message's identity — proof at reconciliation time
    // that `base` still indexes the same boundary (the message cap trims
    // oldest entries and shifts indexes underneath a long round).
    const baseMark = base > 0 ? detail.messages[base - 1] : null;
    // Predict the speakers so the indicator is honest for directed rounds:
    // only @-mentioned participants speak; no mention → everyone. Same rule
    // as the daemon's (see predictSpeakers) — the response is still truth.
    const mentioned = message ? predictSpeakers(message, participants).map((p) => p.key) : [];
    const expected = mentioned.length > 0 ? mentioned : participants.map((p) => p.key);
    setRound({ base, expected });
    speakingRef.current = true;
    setSayError(null);
    setMention(null);
    if (message) {
      setPendingUser(message);
      setInput("");
    }
    try {
      const res = await post<SayResponse>(
        `/agents/threads/${encodeURIComponent(threadId)}/say`,
        { message },
      );
      if (genRef.current !== gen) return; // switched threads mid-round
      const entries = res.entries ?? [];
      // FINAL RECONCILIATION — THE RULE: `base + entries` is the truth for
      // THIS round, but it's only applied when it cannot LOSE anything the
      // screen already shows: (a) it must not SHRINK the shown array — a
      // CONCURRENT round (second tab, phone trigger) may have interleaved
      // its own entries after `base`, and slicing them away here would drop
      // them with no event left to bring them back; (b) `base` must still be
      // a valid boundary (baseMark) — the message cap trims oldest entries
      // and shifts indexes, and a blind splice would duplicate the round.
      // When either check fails: keep what's shown and pull the server's
      // MERGED truth instead (post-round, refetchLive applies the server
      // unconditionally, so the pull can never wedge).
      const shown = detailRef.current?.messages ?? [];
      const boundaryOk =
        base === 0 ||
        (shown.length >= base &&
          shown[base - 1]?.at === baseMark?.at &&
          shown[base - 1]?.who === baseMark?.who);
      if (boundaryOk && base + entries.length >= shown.length) {
        setDetail((d) => {
          if (!d) return d;
          const next = [...d.messages.slice(0, base), ...entries];
          // Re-guarded at apply time — a live refetch may land in between.
          return next.length >= d.messages.length ? { ...d, messages: next } : d;
        });
      } else {
        void refetchLive();
      }
      onRoundDone();
    } catch (e) {
      if (genRef.current !== gen) return;
      setSayError(
        e instanceof ApiError
          ? e.status === 0
            ? "Daemon offline — the round-table couldn't speak."
            : e.message
          : String(e),
      );
      if (message) setInput(message); // hand the text back — nothing lost
    } finally {
      if (genRef.current === gen) {
        setPendingUser(null);
        setRound(null);
        speakingRef.current = false;
        inputRef.current?.focus();
      }
    }
  }

  /* --------------------------------------------------------- memory ------- */

  /** STEP 1 — LOOK. `preview: true` writes nothing and answers with the items
   *  plus the exact text that would land. The daemon already defaults to a
   *  preview; sending the flag anyway means a future change of that default
   *  cannot turn this button into a write. */
  async function previewMemory() {
    if (memBusy !== null || !detail) return;
    const gen = genRef.current;
    setMemBusy("preview");
    setMemError(null);
    setMemSaved(null);
    try {
      const res = await post<RememberResult>(
        `/agents/threads/${encodeURIComponent(threadId)}/remember`,
        { preview: true },
      );
      if (genRef.current !== gen) return; // switched threads mid-fetch
      setMemPreview(res ?? {});
    } catch (e) {
      if (genRef.current !== gen) return;
      setMemError(
        failureText(e, "Daemon offline — couldn't read this thread for memory."),
      );
    } finally {
      if (genRef.current === gen) setMemBusy(null);
    }
  }

  /** STEP 2 — COMMIT. `preview:false` PLUS the previewed text, verbatim.
   *
   *  The content is not optional politeness: without it the daemon re-runs the
   *  whole ladder, including a second distillation, so what lands is not what
   *  the user read and approved. Untrimmed and unedited — any reshaping here
   *  reintroduces the same mismatch on a smaller scale. */
  async function commitMemory() {
    const approved = memPreview?.content ?? "";
    if (memBusy !== null || approved.trim() === "") return;
    const gen = genRef.current;
    setMemBusy("commit");
    setMemError(null);
    try {
      const res = await post<RememberResult>(
        `/agents/threads/${encodeURIComponent(threadId)}/remember`,
        { preview: false, content: approved },
      );
      if (genRef.current !== gen) return;
      setMemSaved(res ?? {});
      setMemPreview(null);
    } catch (e) {
      if (genRef.current !== gen) return;
      // The preview stays on screen: the write did not happen, so the review
      // the user was in the middle of must not vanish (and must not read as
      // done). Retrying sends the SAME approved text.
      setMemError(failureText(e, "Daemon offline — nothing was saved to memory."));
    } finally {
      if (genRef.current === gen) setMemBusy(null);
    }
  }

  /* ------------------------------------------------- add an agent -------- */

  /** Open the picker — but only once there is something real to show. The
   *  catalog is fetched per open so an agent created (or a remote registered)
   *  since this thread opened is on the list; a failure names itself here
   *  instead of opening three empty groups that read as "you have no agents". */
  async function openAdd() {
    if (addLoading || addCatalog) return;
    const gen = genRef.current;
    setAddLoading(true);
    setAddError(null);
    setAddNote(null);
    try {
      const catalog = await loadCatalog();
      if (genRef.current !== gen) return; // switched threads mid-fetch
      // THE EMPTY-PICKER GUARD, for EVERY path (reviewer finding). The roster
      // branch above refuses an empty `roster` array, but the older-daemon
      // fallback has no such gate and a daemon answering `/agents` with `{}`
      // (or a roster whose rows all carry an unknown kind) resolves to three
      // empty groups — the picker then opens and states "No built-in agents
      // available", "No agents of your own yet", "No remote agents connected",
      // which reads as "you have no agents" when the truth is "this daemon
      // told us nothing". A named failure beats three confident denials.
      if (catalog.builtin.length + catalog.dynamic.length + catalog.remotes.length === 0) {
        setAddError("This daemon listed no agents at all — there is nobody to add yet.");
        return;
      }
      setAddCatalog(catalog);
    } catch (e) {
      if (genRef.current !== gen) return;
      setAddError(failureText(e, "Daemon offline — couldn't load the agent list."));
    } finally {
      if (genRef.current === gen) setAddLoading(false);
    }
  }

  /** Seat the picker's panel on this thread.
   *
   *  THE LIST IS SENT WHOLE. `PUT .../participants` SETS the panel, so a body
   *  carrying only the newly-picked agent would silently evict everyone else.
   *  The picker opens with the current panel preselected, so what comes back
   *  here is "everyone seated, plus whoever was added" — additive because the
   *  round-trip never drops what it started with, and a deliberate removal in
   *  the picker is still honoured (the affordance is right there; ignoring it
   *  would be its own lie).
   *
   *  NOTHING IS SHOWN AS ADDED UNTIL THE DAEMON AGREES: the PUT's own thread
   *  view is applied when it carries one, otherwise the thread is refetched.
   *  On failure this THROWS — PanelPicker renders the reason and stays open,
   *  and the thread on screen is untouched. */
  async function addToPanel(_title: string, next: Participant[]) {
    const gen = genRef.current;
    const before = detailRef.current?.participants ?? [];
    // Defensive dedupe: the daemon rejects a repeated key outright ("X is
    // already in this thread"), and no click in the picker can produce one —
    // but a body it builds is not worth trusting blindly.
    const seen = new Set<string>();
    const body = next
      .filter((p) => {
        const key = `${p.source}:${p.name}`;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      })
      .map(({ source, name, role }) => ({ source, name, role }));
    const res = await put<ThreadDetail>(
      `/agents/threads/${encodeURIComponent(threadId)}/participants`,
      { participants: body },
    );
    if (genRef.current !== gen) return; // switched threads mid-save
    // WHO JOINED IS READ OFF THE DAEMON'S ANSWER, never off the picker
    // (reviewer finding). The picker holds what was ASKED for; naming somebody
    // in the receipt on that basis is the same lie as an optimistic chip, only
    // later and in words — and it survives even when the response seats
    // somebody else, because nothing downstream re-checks it. When the response
    // carries no panel we name NOBODY: the refetch below is what will say who
    // is actually seated, and it has not answered yet.
    const confirmed =
      res && Array.isArray(res.participants) && Array.isArray(res.messages)
        ? res.participants
        : null;
    if (confirmed) setDetail(res);
    else void refetchLive(); // older/odd shape — ask the server rather than guess
    const joined = confirmed ? addedNames(before, confirmed) : [];
    setAddCatalog(null);
    setAddError(null);
    setAddNote(
      joined.length > 0
        ? `${joined.join(", ")} joined this thread — ${
            joined.length === 1 ? "it answers" : "they answer"
          } from the next message on.`
        : confirmed
          ? "Panel saved — nobody new was added."
          : "Panel saved — reloading the thread to show who is seated.",
    );
    onRoundDone(); // the rail's panel avatars are now stale
  }

  /* -------------------------------------------------- @-autocomplete ------ */

  function updateMention(el: HTMLTextAreaElement) {
    const caret = el.selectionStart ?? el.value.length;
    const before = el.value.slice(0, caret);
    const m = /(^|\s)@([\w-]*)$/.exec(before);
    if (m) {
      const query = m[2];
      setMention((prev) => {
        if (!prev || prev.query !== query) setMentionIdx(0);
        return { query, start: caret - query.length - 1 };
      });
    } else {
      setMention(null);
    }
  }

  const mentionMatches = mention
    ? participants.filter((p) => {
        const q = mention.query.toLowerCase();
        return (
          p.name.toLowerCase().startsWith(q) ||
          (p.role || "").toLowerCase().startsWith(q)
        );
      })
    : [];

  function insertMention(p: Participant) {
    if (!mention) return;
    const before = input.slice(0, mention.start);
    const after = input.slice(mention.start + 1 + mention.query.length);
    // Quoted when the bare form wouldn't survive the daemon's grammar —
    // "@Full Name" reads back as "@Full", which directs nobody.
    const text = mentionText(p.name);
    const next = `${before}${text} ${after}`;
    setInput(next);
    setMention(null);
    const pos = before.length + text.length + 1;
    requestAnimationFrame(() => {
      inputRef.current?.setSelectionRange(pos, pos);
      inputRef.current?.focus();
    });
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (mention && mentionMatches.length > 0) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setMentionIdx((i) => (i + 1) % mentionMatches.length);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setMentionIdx((i) => (i - 1 + mentionMatches.length) % mentionMatches.length);
        return;
      }
      if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault();
        insertMention(mentionMatches[mentionIdx] ?? mentionMatches[0]);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setMention(null);
        return;
      }
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void say(input);
    }
  }

  /* ----------------------------------------------------------- render ----- */

  if (loadError) {
    if (loadError.status === 0) return <OfflineHint />;
    if (loadError.status === 404)
      return (
        <div className="card-surface">
          <Empty icon={<MessagesSquare size={22} />}>
            This thread no longer exists — pick another from the rail or start a
            new one.
          </Empty>
        </div>
      );
    return <ErrorNote>{loadError.message}</ErrorNote>;
  }

  if (!detail)
    return (
      <div className="card-surface p-5">
        <SkeletonRows rows={4} />
      </div>
    );

  // Who already answered THIS round (entries landed after the round's base).
  const landedKeys = round
    ? messages.slice(round.base).filter((m) => m.who !== "user").map((m) => m.who)
    : [];
  const nowSpeaking = round
    ? round.expected.find((k) => !landedKeys.includes(k))
    : undefined;

  return (
    <div className="card-surface flex min-w-0 flex-col overflow-hidden">
      {/* Header: title + the panel */}
      <div className="border-b hairline px-4 py-3">
        <div className="flex items-center gap-2">
          <h2 className="min-w-0 truncate text-sm font-semibold tracking-wide text-zinc-100">
            {detail.title || "Agent thread"}
          </h2>
          {/* Extract to memory. Disabled with an empty transcript (the daemon
              400s: there is nothing to remember) and DURING a round — a
              preview taken while replies are still landing would ask the user
              to approve a snapshot of a moving transcript. */}
          <button
            type="button"
            onClick={() => void previewMemory()}
            disabled={memBusy !== null || speaking || messages.length === 0}
            title={
              messages.length === 0
                ? "Nothing has been said yet"
                : "Read what this panel concluded, then choose to save it to long-term memory"
            }
            className="ml-auto inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1 text-xs font-medium text-zinc-400 transition-colors hover:border-accent/40 hover:text-accent-soft disabled:opacity-40 disabled:hover:border-white/10 disabled:hover:text-zinc-400"
          >
            {memBusy === "preview" ? (
              <LoaderCircle size={13} className="animate-spin-slow" aria-hidden="true" />
            ) : (
              <Brain size={13} aria-hidden="true" />
            )}
            Extract and add to memory
          </button>
          <button
            type="button"
            onClick={() => onEditPanel(detail)}
            title="Change who sits at this round-table and their roles"
            className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1 text-xs font-medium text-zinc-400 transition-colors hover:border-accent/40 hover:text-accent-soft"
          >
            <UserRoundPen size={13} /> Edit panel
          </button>
        </div>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {participants.map((p) => (
            <span
              key={p.key}
              className="inline-flex items-center gap-1.5 rounded-full border border-white/[0.08] bg-white/[0.03] py-0.5 pl-1 pr-2"
              title={`${p.name} — ${p.role} (${p.source})`}
            >
              {/* Mood is REAL round state only: the panel tracks who is
                  speaking mid-round (nowSpeaking); everyone else sits idle.
                  Seeded by the bare name (= p.name) so the chip matches the
                  face on every other surface; decorative because the name is
                  the visible text right beside it. */}
              <span aria-hidden="true" className="contents">
                <AgentFace
                  name={p.name}
                  title=""
                  mood={round && p.key === nowSpeaking ? "work" : "idle"}
                  size={18}
                />
              </span>
              <span className="text-xs text-zinc-200">{p.name}</span>
              <RolePill role={p.role} />
              <SourceIcon source={p.source} size={11} />
            </span>
          ))}
        </div>
      </div>

      {/* The review step — present only while there is something to approve.
          Nothing has been written while this is on screen. */}
      {memPreview && (
        <MemoryReviewCard
          result={memPreview}
          committing={memBusy === "commit"}
          error={memError}
          onCommit={() => void commitMemory()}
          onDiscard={() => {
            setMemPreview(null);
            setMemError(null);
          }}
        />
      )}
      {/* A preview that never arrived: the reason is shown where the button
          is, and no review card is faked around it. */}
      {!memPreview && !memSaved && memError && (
        <div className="border-b hairline px-4 py-3">
          <ErrorNote>{memError}</ErrorNote>
        </div>
      )}
      {memSaved && (
        <div className="space-y-1.5 border-b hairline px-4 py-3">
          <SuccessNote>
            Saved to memory
            {memSaved.source ? ` — ${memSaved.source}` : ""}
            {memSaved.ref ? ` · ${memSaved.ref}` : ""}
          </SuccessNote>
          {/* The receipt relays the daemon's note VERBATIM and applies none of
              the review card's degrade wording: a commit of approved text
              answers distilled:false because it did not re-distill, which is
              not the same claim as "no model was connected". Reusing that
              copy here would invent a degrade that did not happen. */}
          {memSaved.note && <p className="text-[11px] text-zinc-500">{memSaved.note}</p>}
          <button
            type="button"
            onClick={() => setMemSaved(null)}
            className="btn-ghost py-1 text-xs"
          >
            Dismiss
          </button>
        </div>
      )}

      {/* Transcript */}
      <div className="max-h-[62vh] min-h-[40vh] space-y-4 overflow-y-auto p-4">
        {messages.length === 0 && !pendingUser && !speaking ? (
          <div className="flex min-h-[36vh] flex-col items-center justify-center gap-3 px-6 text-center">
            {/* The panel's faces, idle — waiting for the first question.
                These KEEP their title/label: no visible name sits beside them,
                so the face is the only identity carrier here. */}
            <div className="flex items-center gap-1.5">
              {participants.slice(0, 6).map((p) => (
                <AgentFace key={p.key} name={p.name} title={p.name} size={34} />
              ))}
              {participants.length > 6 && (
                <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full border border-white/10 bg-ink-800 text-[12px] text-zinc-400">
                  +{participants.length - 6}
                </span>
              )}
            </div>
            <p className="max-w-sm text-sm leading-relaxed text-zinc-400">
              Ask the panel anything — every agent answers in turn, and they can
              respond to each other. Mention one with @name to ask just them.
            </p>
          </div>
        ) : (
          messages.map((m, i) =>
            m.who === "user" ? (
              <UserBubble key={i} content={m.content} at={m.at} />
            ) : (
              <AgentTurn key={i} entry={m} byKey={byKey} />
            ),
          )
        )}
        {/* The optimistic user bubble only until the daemon's persisted copy
            lands (the first live refetch of the round replaces it). */}
        {pendingUser && round && messages.length <= round.base && (
          <UserBubble content={pendingUser} />
        )}
        {round && (
          <div className="space-y-1.5">
            <div className="flex flex-wrap items-center gap-1.5">
              {round.expected.map((key) => {
                const p = byKey.get(key);
                const name = p?.name ?? faceIdentity(key);
                const done = landedKeys.includes(key);
                const current = !done && key === nowSpeaking;
                return (
                  <span
                    key={key}
                    className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] ${
                      done
                        ? "border-emerald-500/25 bg-emerald-500/[0.06] text-zinc-300"
                        : current
                          ? "border-accent/30 bg-accent/[0.06] text-zinc-200"
                          : "border-white/[0.06] text-zinc-500"
                    }`}
                  >
                    {/* Every mood here is tracked round state: landed → done,
                        the current speaker → work, the rest wait idle. Bare-
                        name seed + decorative — the name renders beside it. */}
                    <span aria-hidden="true" className="contents">
                      <AgentFace
                        name={faceIdentity(key)}
                        title=""
                        mood={done ? "done" : current ? "work" : "idle"}
                        size={14}
                      />
                    </span>
                    {name}
                    {done ? (
                      <Check size={11} className="text-emerald-400" />
                    ) : current ? (
                      <span className="inline-flex items-center gap-1 text-accent-soft">
                        <LoaderCircle size={11} className="animate-spin-slow" />
                        speaking…
                      </span>
                    ) : (
                      <span className="text-zinc-600">waiting</span>
                    )}
                  </span>
                );
              })}
            </div>
            <p className="text-[11px] text-zinc-500">
              {landedKeys.length} of {round.expected.length} answered — replies
              land as each agent finishes.
            </p>
          </div>
        )}
        {sayError && <ErrorNote>{sayError}</ErrorNote>}
        <div ref={bottomRef} />
      </div>

      {/* Composer */}
      <div className="border-t hairline p-3">
        <div className="relative flex items-end gap-2">
          {mention && mentionMatches.length > 0 && !speaking && (
            <div
              role="listbox"
              aria-label="Mention a participant"
              className="absolute bottom-full left-0 z-20 mb-1.5 max-h-56 w-64 overflow-y-auto rounded-xl border border-white/10 bg-ink-850/95 shadow-card-hover backdrop-blur-xl"
            >
              <p className="border-b hairline px-3 py-1.5 text-[10px] font-medium uppercase tracking-[0.12em] text-zinc-500">
                Direct the round
              </p>
              {mentionMatches.map((p, i) => (
                <button
                  key={p.key}
                  type="button"
                  role="option"
                  aria-selected={i === mentionIdx}
                  // mousedown (not click) so the textarea never loses focus.
                  onMouseDown={(e) => {
                    e.preventDefault();
                    insertMention(p);
                  }}
                  onMouseEnter={() => setMentionIdx(i)}
                  className={`flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs ${
                    i === mentionIdx
                      ? "bg-accent/[0.1] text-accent-soft"
                      : "text-zinc-300"
                  }`}
                >
                  {/* Bare-name seed + decorative: the option's visible text
                      IS the name — see faceIdentity. */}
                  <span aria-hidden="true" className="contents">
                    <AgentFace name={p.name} title="" size={18} />
                  </span>
                  <span className="min-w-0 truncate">{p.name}</span>
                  <RolePill role={p.role} />
                </button>
              ))}
            </div>
          )}
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => {
              setInput(e.target.value);
              updateMention(e.currentTarget);
            }}
            onKeyDown={onKeyDown}
            onKeyUp={(e) => updateMention(e.currentTarget)}
            onClick={(e) => updateMention(e.currentTarget)}
            rows={2}
            disabled={speaking}
            placeholder="Ask the panel…"
            aria-label="Message the panel"
            className="field min-w-0 flex-1 resize-none text-sm disabled:opacity-60"
          />
          <button
            type="button"
            onClick={() => void say(input)}
            disabled={speaking || !input.trim()}
            className="btn-accent shrink-0"
            title="Send — everyone answers unless you @-mention someone"
          >
            <Send size={14} /> Ask the panel
          </button>
        </div>
        {/* The add's own outcomes live down here, beside the control that
            caused them: a catalog that never arrived (no picker is faked
            around it) and the receipt for an add that landed. A failed PUT is
            NOT here — it belongs inside the picker the user is still standing
            in, which stays open with the panel unchanged. */}
        {addError && (
          <div className="mt-2">
            <ErrorNote>{addError}</ErrorNote>
          </div>
        )}
        {addNote && (
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <SuccessNote>{addNote}</SuccessNote>
            <button
              type="button"
              onClick={() => setAddNote(null)}
              className="btn-ghost py-1 text-xs"
            >
              Dismiss
            </button>
          </div>
        )}
        <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
          <p className="text-[11px] text-zinc-600">
            Each agent sees the replies before it — @name asks just them.
          </p>
          {/* BOTTOM RIGHT OF THE ROOM (v1.179.0): bring somebody else in
              without leaving the conversation. Disabled mid-round — the
              daemon is already speaking to the panel it read when the round
              started, so reseating it now would change who answers halfway
              through and the screen would disagree with the transcript. */}
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => void openAdd()}
              disabled={addLoading || speaking}
              title={
                speaking
                  ? "Wait for this round to finish — the panel can't change mid-round"
                  : "Add another agent to this thread — everyone already here stays"
              }
              className="btn-ghost py-1 text-xs"
            >
              {addLoading ? (
                <LoaderCircle size={13} className="animate-spin-slow" aria-hidden="true" />
              ) : (
                <UserRoundPlus size={13} aria-hidden="true" />
              )}
              Add an agent
            </button>
            <button
              type="button"
              onClick={() => void say("")}
              disabled={speaking}
              title="Send no message — the agents take another round among themselves"
              className="btn-ghost py-1 text-xs"
            >
              <MessagesSquare size={13} /> Let them continue
            </button>
          </div>
        </div>
      </div>

      {/* THE picker — the same component the page uses for a new thread and
          for editing a panel, seeded with everyone already seated so Save
          sends the WHOLE panel (see addToPanel). Rendered only once its
          catalog is in hand, so it never shows empty groups while loading. */}
      {addCatalog && (
        <PanelPicker
          mode="edit"
          catalog={addCatalog}
          initialParticipants={participants}
          onClose={() => setAddCatalog(null)}
          onSubmit={addToPanel}
        />
      )}
    </div>
  );
}
