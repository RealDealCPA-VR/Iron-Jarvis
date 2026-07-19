"use client";

// The conversation pane of one agent thread. Header: title + the panel
// (participant chips) + Edit panel. Messages: user turns right-aligned and
// accent-tinted; agent turns left-aligned with the agent's avatar, name (in
// its deterministic hue), role pill, and markdown content. An entry with an
// `error` is an honest per-agent failure rendered as an amber note — never
// hidden, never an empty bubble pretending to be an answer.

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
import { timeAgo } from "@/lib/format";
import { Empty, ErrorNote, OfflineHint, SkeletonRows } from "@/components/ui";
import {
  AgentAvatar,
  AvatarStack,
  RolePill,
  SourceIcon,
  nameColor,
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
          <SourceIcon source={source} size={11} />
          <span className="text-[10px] text-zinc-600">{timeAgo(entry.at)}</span>
        </div>
        {entry.error ? (
          // An honest per-agent failure — surfaced, never hidden.
          <div className="flex items-start gap-2 rounded-xl border border-amber-500/25 bg-amber-500/[0.07] px-3 py-2 text-xs leading-relaxed text-amber-200">
            <TriangleAlert size={13} className="mt-0.5 shrink-0" aria-hidden="true" />
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

export function ThreadView({
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
  const [speaking, setSpeaking] = useState(false);
  const [pendingUser, setPendingUser] = useState<string | null>(null);
  const [sayError, setSayError] = useState<string | null>(null);

  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  // Bumped on thread switch so a slow round from the OLD thread can't land here.
  const genRef = useRef(0);

  useEffect(() => {
    genRef.current += 1;
    let cancelled = false;
    setDetail(null);
    setLoadError(null);
    setSayError(null);
    setPendingUser(null);
    setSpeaking(false);
    setInput("");
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

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.length, speaking, pendingUser]);

  /** One speaking round. Empty message = "let them continue" (the agents take
   *  another round among themselves). Can take a while — each agent is a turn. */
  async function say(raw: string) {
    if (speaking || !detail) return;
    const message = raw.trim();
    const gen = genRef.current;
    setSpeaking(true);
    setSayError(null);
    if (message) {
      setPendingUser(message);
      setInput("");
    }
    try {
      const res = await post<{ entries: ThreadEntry[] }>(
        `/agents/threads/${encodeURIComponent(threadId)}/say`,
        { message },
      );
      if (genRef.current !== gen) return; // switched threads mid-round
      const entries = res.entries ?? [];
      setDetail((d) => (d ? { ...d, messages: [...d.messages, ...entries] } : d));
      onRoundDone();
    } catch (e) {
      if (genRef.current !== gen) return;
      setSayError(
        e instanceof ApiError
          ? e.status === 0
            ? "Daemon offline — the panel couldn't speak."
            : e.message
          : String(e),
      );
      if (message) setInput(message); // hand the text back — nothing lost
    } finally {
      if (genRef.current === gen) {
        setPendingUser(null);
        setSpeaking(false);
        inputRef.current?.focus();
      }
    }
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void say(input);
    }
  }

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
            title="Change who sits on this panel and their roles"
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
              respond to each other.
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
        {pendingUser && <UserBubble content={pendingUser} />}
        {speaking && (
          <div className="flex items-center gap-2.5 text-xs text-zinc-400">
            <AvatarStack participants={participants} size="xs" />
            <LoaderCircle size={13} className="animate-spin-slow text-accent-soft" />
            panel is speaking… ({participants.length} agent
            {participants.length === 1 ? "" : "s"})
          </div>
        )}
        {sayError && <ErrorNote>{sayError}</ErrorNote>}
        <div ref={bottomRef} />
      </div>

      {/* Composer */}
      <div className="border-t hairline p-3">
        <div className="flex items-end gap-2">
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
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
            title="Send — every agent answers in turn"
          >
            <Send size={14} /> Ask the panel
          </button>
        </div>
        <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
          <p className="text-[11px] text-zinc-600">
            Each agent sees the replies before it — later seats respond to
            earlier ones.
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
