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
  Check,
  Copy,
  LoaderCircle,
  MessagesSquare,
  Send,
  TriangleAlert,
  UserRoundPen,
} from "lucide-react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { ApiError, get, post } from "@/lib/api";
import { useEvents } from "@/lib/useEvents";
import { timeAgo } from "@/lib/format";
import { Empty, ErrorNote, OfflineHint, SkeletonRows } from "@/components/ui";
import {
  AgentAvatar,
  AvatarStack,
  RolePill,
  SOURCE_LABEL,
  SourceIcon,
  nameColor,
  type AgentSource,
  type Participant,
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
  const colon = entry.who.indexOf(":");
  const name = p?.name ?? (colon >= 0 ? entry.who.slice(colon + 1) : entry.who);
  const role = entry.role ?? p?.role;
  const source = entry.source ?? p?.source;
  const content = (entry.content ?? "").trim();
  return (
    <div className="flex gap-3">
      <AgentAvatar agentKey={entry.who} name={name} size="md" className="mt-0.5" />
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
          <button
            type="button"
            onClick={() => onEditPanel(detail)}
            title="Change who sits at this round-table and their roles"
            className="ml-auto inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1 text-xs font-medium text-zinc-400 transition-colors hover:border-accent/40 hover:text-accent-soft"
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
              <AgentAvatar agentKey={p.key} name={p.name} size="sm" />
              <span className="text-xs text-zinc-200">{p.name}</span>
              <RolePill role={p.role} />
              <SourceIcon source={p.source} size={11} />
            </span>
          ))}
        </div>
      </div>

      {/* Transcript */}
      <div className="max-h-[62vh] min-h-[40vh] space-y-4 overflow-y-auto p-4">
        {messages.length === 0 && !pendingUser && !speaking ? (
          <div className="flex min-h-[36vh] flex-col items-center justify-center gap-3 px-6 text-center">
            <AvatarStack participants={participants} size="lg" max={6} />
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
                const colon = key.indexOf(":");
                const name = p?.name ?? (colon >= 0 ? key.slice(colon + 1) : key);
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
                    <AgentAvatar agentKey={key} name={name} size="xs" />
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
                  <AgentAvatar agentKey={p.key} name={p.name} size="sm" />
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
        <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
          <p className="text-[11px] text-zinc-600">
            Each agent sees the replies before it — @name asks just them.
          </p>
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
  );
}
