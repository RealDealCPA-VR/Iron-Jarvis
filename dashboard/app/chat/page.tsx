"use client";

// The friendly front door under "Work". Two modes, one thread:
//
// CHAT (default): a DIRECT completion via POST /chat — the full local bubble
// history is sent on every turn and the reply comes back in seconds. Personas
// and file attachments ride along (text is extracted server-side; images go to
// vision). No session machinery at all — multi-turn is just the local array.
// Chat-mode extras: a "+" menu arms up to 6 registry tools (sent as `tools`;
// the reply may report `tools_used`) and typing "/" picks a skill (sent as
// `skill`) — both persist across turns and clear on New chat / thread switch.
//
// AGENT: the original session-based flow, preserved verbatim. The message opens
// (or continues) a real Iron Jarvis session that can use tools. Sending is
// NON-BLOCKING: we POST with wait:false (the agent runs in the background) and
// then show a live "working" bubble that narrates the agent's steps from the
// /events stream. We finalize when the session's `agent.completed` event
// arrives (or, as a fallback when the socket is down, by polling the session
// until its status flips to completed/failed).
//
// PERSISTENCE: every completed turn autosaves the whole bubble array to
// PUT /chat/threads/{id} ("new" creates and returns the real id). A threads
// sidebar lists saved conversations; clicking one loads it back into chat mode.
// Saves are queued through a single promise chain so turns can never race two
// PUTs (the first turn's "new" must resolve to a real id before the second
// save starts, or we'd mint duplicate threads).
//
// Assistant bubbles render MARKDOWN (react-markdown + GFM) with styled code
// blocks (per-block copy button), tables, lists, and links; user bubbles stay
// plain pre-wrapped text.

import {
  createContext,
  isValidElement,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";
import {
  Bot,
  Check,
  Copy,
  History,
  Loader2,
  MessageSquare,
  Paperclip,
  Plus,
  RefreshCw,
  Search,
  Send,
  Sparkles,
  Square,
  Trash2,
  User,
  Wrench,
  X,
} from "lucide-react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { get, post, put, del, ApiError } from "@/lib/api";
import type { IJEvent, ModelOption, SessionView } from "@/lib/types";
import { timeAgo } from "@/lib/format";
import { useEvents } from "@/lib/useEvents";
import { Card, Empty, ErrorNote, LoaderInline, OfflineHint } from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";

type Mode = "chat" | "agent";

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  /** Display names of files attached to this (user) message — footer chips. */
  attachmentNames?: string[];
  /** Registry tools the reply actually ran (assistant messages) — footer line. */
  toolsUsed?: string[];
}

/** What POST /chat expects. */
interface ChatRequestMessage {
  role: "user" | "assistant";
  content: string;
}
interface ChatRequestBody {
  messages: ChatRequestMessage[];
  provider?: string;
  model?: string;
  persona?: string;
  attachments?: string[]; // uploaded document paths
  skill?: string; // playbook for the reply (omitted / "" = none)
  tools?: string[]; // armed registry tools (max 6) — the chat runs a tool loop
}
interface ChatResponse {
  reply: string;
  provider?: string;
  model?: string;
  images?: string[];
  skill?: string;
  tools_used?: string[];
}

interface PersonaOption {
  name: string;
  description: string;
}

/** One row from GET /skills. */
interface SkillOption {
  name: string;
  description: string;
  source?: string;
}

/** One row from GET /tools — the registry sends more fields; we need these two. */
interface ToolOption {
  name: string;
  description: string;
}

/** One row from GET /chat/threads (newest first). `messages` is a count, but
 * tolerate a daemon that inlines the array. */
interface ThreadSummary {
  id: string;
  title: string;
  persona?: string;
  messages: number | ChatMessage[];
  updated_at: string;
}

/** GET /chat/threads/{id}. */
interface ThreadDetail {
  id: string;
  title: string;
  persona?: string;
  messages: ChatMessage[];
}

/** PUT /chat/threads/{id} body + response. */
interface ThreadSaveBody {
  messages: ChatMessage[];
  title?: string;
  persona?: string;
}
interface ThreadSaveResult {
  id: string;
  title: string;
}

/** POST /documents/upload response (same contract NewSessionForm uses). */
interface UploadResult {
  path: string;
  name: string;
  bytes?: number;
}

/** One uploaded, ready-to-send attachment chip. */
interface UploadedFile {
  name: string;
  path: string;
  bytes: number;
}

// Attachment limits: keep uploads snappy and the /chat context sane.
const MAX_ATTACHMENTS = 4;
const MAX_FILE_BYTES = 20 * 1024 * 1024; // 20 MB

// Tool-loop limits: /chat accepts at most 6 armed tools; the registry is big,
// so the "+" menu renders at most this many rows (search narrows the rest).
const MAX_TOOLS = 6;
const TOOL_LIST_CAP = 100;

// Persona persistence (chat mode only).
const PERSONA_KEY = "ij_chat_persona";
const PERSONA_CUSTOM_KEY = "ij_chat_persona_custom";
const CUSTOM_PERSONA = "__custom__";

// Fallback until GET /chat/personas answers (or if it never does).
const DEFAULT_PERSONAS: PersonaOption[] = [
  { name: "assistant", description: "Helpful general-purpose assistant" },
];

// Prompts the user can click to prefill the composer on an empty chat.
const EXAMPLES = [
  "What can you do?",
  "Summarize the files in a folder",
  "Draft a follow-up email to a client",
];

// A few agent states worth naming; anything else falls back to "Working…".
const STATE_LABEL: Record<string, string> = {
  initializing: "Getting ready…",
  running: "Working…",
  waiting: "Waiting…",
  paused: "Paused…",
  delegating: "Bringing in a helper…",
  reviewing: "Reviewing the work…",
  completed: "Wrapping up…",
};

// Turn one raw session event into a short, human-friendly progress line (or null
// to skip events that don't read well as a step).
function stepLabel(e: IJEvent): string | null {
  const p = e.payload || {};
  switch (e.type) {
    case "agent.started":
      return "Thinking…";
    case "agent.state_changed": {
      // Backend payload is {from, to}; tolerate a `state` alias just in case.
      const to = (p.to ?? p.state) as string | undefined;
      if (!to) return "Working…";
      return STATE_LABEL[to.toLowerCase()] ?? "Working…";
    }
    case "tool.executed": {
      const tool = p.tool as string | undefined;
      return tool ? `Using ${tool}…` : "Using a tool…";
    }
    case "tool.denied": {
      const tool = p.tool as string | undefined;
      return tool ? `Skipped ${tool} (not permitted)` : "Skipped a tool";
    }
    case "provider.failed": {
      const provider = p.provider as string | undefined;
      return `Provider ${provider} failed — ${String(p.error || "").slice(0, 120)}`;
    }
    case "provider.downgraded":
      return "Model not connected — using offline mock (connect a model)";
    case "agent.completed":
      return "Finishing up…";
    default:
      return null;
  }
}

// The model <select> encodes the choice as `${provider}::${model}` (empty => let the
// server pick its default). Split it back out only when it carries both halves.
function splitChoice(choice: string): { provider?: string; model?: string } {
  const i = choice.indexOf("::");
  if (i === -1) return {};
  const provider = choice.slice(0, i);
  const model = choice.slice(i + 2);
  return provider && model ? { provider, model } : {};
}

/** Read a File as raw base64 (FileReader gives a data: URL — strip the prefix). */
function readAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("could not read file"));
    reader.onload = () => {
      const res = String(reader.result);
      const comma = res.indexOf(",");
      resolve(comma >= 0 ? res.slice(comma + 1) : res);
    };
    reader.readAsDataURL(file);
  });
}

function fmtSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${bytes} B`;
}

function capitalize(s: string): string {
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
}

/** Message count for a thread row (the list sends a number; tolerate an array). */
function msgCount(t: ThreadSummary): number {
  return typeof t.messages === "number" ? t.messages : t.messages.length;
}

// ------------------------------------------------------------------ markdown

/** Collect the plain text inside rendered markdown children (for copy buttons). */
function nodeText(node: ReactNode): string {
  if (node === null || node === undefined || typeof node === "boolean") return "";
  if (
    typeof node === "string" ||
    typeof node === "number" ||
    typeof node === "bigint"
  ) {
    return String(node);
  }
  if (Array.isArray(node)) return node.map(nodeText).join("");
  if (isValidElement(node)) {
    return nodeText((node as ReactElement<{ children?: ReactNode }>).props.children);
  }
  return "";
}

/** Small clipboard button: copies `text`, flashes a check for a moment. */
function CopyIconButton({
  text,
  title,
  className,
}: {
  text: string;
  title: string;
  className?: string;
}) {
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
        /* clipboard unavailable — nothing useful to surface */
      });
  }
  return (
    <button
      type="button"
      onClick={copy}
      title={title}
      aria-label={title}
      className={
        className ??
        "grid h-6 w-6 place-items-center rounded-md text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-200"
      }
    >
      {copied ? <Check size={12} className="text-emerald-400" /> : <Copy size={12} />}
    </button>
  );
}

// Lets the <code> override know it sits inside a <pre> block (block code keeps
// the pre's styling; standalone inline code gets the accent pill).
const PreContext = createContext(false);

/** Fenced code block: dark panel + hover copy button. */
function MarkdownPre({ children }: { children?: ReactNode }) {
  const text = nodeText(children).replace(/\n$/, "");
  return (
    <div className="group/code relative my-2">
      <CopyIconButton
        text={text}
        title="Copy code"
        className="absolute right-2 top-2 z-10 grid h-6 w-6 place-items-center rounded-md border border-white/10 bg-white/[0.06] text-zinc-400 opacity-0 transition-opacity hover:text-zinc-100 focus-visible:opacity-100 group-hover/code:opacity-100"
      />
      <PreContext.Provider value={true}>
        <pre className="overflow-x-auto rounded bg-black/40 p-3 font-mono text-xs leading-relaxed text-zinc-200">
          {children}
        </pre>
      </PreContext.Provider>
    </div>
  );
}

function MarkdownCode({
  className,
  children,
}: {
  className?: string;
  children?: ReactNode;
}) {
  const inPre = useContext(PreContext);
  if (inPre) return <code className={className}>{children}</code>;
  return (
    <code className="rounded bg-white/[0.08] px-1.5 py-0.5 font-mono text-[0.85em] text-accent-soft">
      {children}
    </code>
  );
}

// Explicit dark-theme element overrides (the app has no typography plugin, so
// this is our "prose-invert").
const MD_COMPONENTS: Components = {
  h1: ({ children }) => (
    <h1 className="mb-1.5 mt-3 text-base font-semibold text-zinc-100 first:mt-0">
      {children}
    </h1>
  ),
  h2: ({ children }) => (
    <h2 className="mb-1.5 mt-3 text-[15px] font-semibold text-zinc-100 first:mt-0">
      {children}
    </h2>
  ),
  h3: ({ children }) => (
    <h3 className="mb-1 mt-2.5 text-sm font-semibold text-zinc-100 first:mt-0">
      {children}
    </h3>
  ),
  p: ({ children }) => (
    <p className="my-1.5 leading-relaxed first:mt-0 last:mb-0">{children}</p>
  ),
  ul: ({ children }) => (
    <ul className="my-1.5 list-disc space-y-1 pl-5">{children}</ul>
  ),
  ol: ({ children }) => (
    <ol className="my-1.5 list-decimal space-y-1 pl-5">{children}</ol>
  ),
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
    <td className="border border-white/10 px-2.5 py-1.5 align-top text-zinc-300">
      {children}
    </td>
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
  strong: ({ children }) => (
    <strong className="font-semibold text-zinc-100">{children}</strong>
  ),
  pre: MarkdownPre,
  code: MarkdownCode,
};

const REMARK_PLUGINS = [remarkGfm];

function Markdown({ content }: { content: string }) {
  return (
    <ReactMarkdown remarkPlugins={REMARK_PLUGINS} components={MD_COMPONENTS}>
      {content}
    </ReactMarkdown>
  );
}

// ------------------------------------------------------------------- bubbles

function Bubble({ role, children }: { role: ChatMessage["role"]; children: ReactNode }) {
  const isUser = role === "user";
  return (
    <div className={`flex gap-3 ${isUser ? "flex-row-reverse" : ""}`}>
      <span
        className={`grid h-8 w-8 shrink-0 place-items-center rounded-xl border ${
          isUser
            ? "border-accent/30 bg-accent/10 text-accent-soft"
            : "border-white/[0.08] bg-white/[0.03] text-zinc-300"
        }`}
      >
        {isUser ? <User size={15} /> : <Bot size={15} />}
      </span>
      <div
        className={`min-w-0 max-w-[80%] rounded-2xl border px-4 py-2.5 text-sm leading-relaxed ${
          isUser
            ? "whitespace-pre-wrap border-accent/25 bg-accent/[0.1] text-zinc-100"
            : "border-white/[0.06] bg-white/[0.03] text-zinc-200"
        }`}
      >
        {children}
      </div>
    </div>
  );
}

/** The small "attached files" footer under a user bubble. */
function AttachmentFooter({ names }: { names: string[] }) {
  if (names.length === 0) return null;
  return (
    <div className="mt-1.5 space-y-0.5 border-t border-white/10 pt-1.5">
      {names.map((n, i) => (
        <div key={`${n}-${i}`} className="flex items-center gap-1.5 text-[11px] text-zinc-400">
          <Paperclip size={10} className="shrink-0 text-accent-soft/70" />
          {n}
        </div>
      ))}
    </div>
  );
}

export default function ChatPage() {
  const [mode, setMode] = useState<Mode>("chat");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  // AGENT MODE: the session id of the turn currently in flight (null when idle).
  // Drives the live "working" bubble, the completion watcher, and the polling
  // fallback.
  const [awaitingId, setAwaitingId] = useState<string | null>(null);
  // CHAT MODE: a direct /chat call is in flight (drives the shimmer bubble).
  const [chatBusy, setChatBusy] = useState(false);
  const [models, setModels] = useState<ModelOption[]>([]);
  const [choice, setChoice] = useState(""); // "" => server default model
  const [personas, setPersonas] = useState<PersonaOption[]>(DEFAULT_PERSONAS);
  const [persona, setPersona] = useState("assistant");
  const [customPersona, setCustomPersona] = useState("");
  const [attachments, setAttachments] = useState<UploadedFile[]>([]);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [input, setInput] = useState("");
  // "+" TOOLS MENU (chat mode): armed registry tool names — sent as `tools` on
  // every /chat turn and kept across turns until "New chat" / a thread switch.
  const [selectedTools, setSelectedTools] = useState<string[]>([]);
  const [toolsOpen, setToolsOpen] = useState(false);
  const [toolQuery, setToolQuery] = useState("");
  const [toolCatalog, setToolCatalog] = useState<ToolOption[] | null>(null);
  const [toolsError, setToolsError] = useState<string | null>(null);
  // "/" SKILL PICKER (chat mode): the chosen skill rides along as `skill` on
  // every turn until its chip is cleared. `slashDismissed` = Esc closed the
  // dropdown for the current "/…" text (any edit reopens it).
  const [skills, setSkills] = useState<SkillOption[] | null>(null);
  const [activeSkill, setActiveSkill] = useState("");
  const [skillIndex, setSkillIndex] = useState(0);
  const [slashDismissed, setSlashDismissed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [offline, setOffline] = useState(false);
  // Threads sidebar: the saved-conversation list + which one is loaded.
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false); // mobile-only toggle

  const { events } = useEvents(150);

  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  // "+" popover container — outside-click detection needs the DOM node.
  const toolsPopRef = useRef<HTMLDivElement>(null);
  // One-shot fetch guards for the /tools and /skills catalogs (cached in state;
  // reset on failure so reopening the affordance retries).
  const toolsFetchedRef = useRef(false);
  const skillsFetchedRef = useRef(false);
  // Latest events, readable synchronously inside send() without re-subscribing.
  const eventsRef = useRef<IJEvent[]>(events);
  eventsRef.current = events;
  // Latest attachments, readable from the window-level drop handler (which is
  // registered once and would otherwise close over a stale array).
  const attachmentsRef = useRef<UploadedFile[]>(attachments);
  attachmentsRef.current = attachments;
  // Latest messages, readable inside finalize()/stop() (both fire from timers
  // and event watchers, where `messages` from the closure could be stale).
  const messagesRef = useRef<ChatMessage[]>(messages);
  messagesRef.current = messages;
  // Event-id boundary captured at the start of each agent turn: we only treat
  // events NEWER than this as belonging to the current turn. This stops a stale
  // `agent.completed` from the previous turn (same session id, still in the
  // buffer) from instantly "completing" the next turn.
  const sinceRef = useRef<string | null>(null);
  // Guards against overlapping finalize attempts (events + polling can both fire).
  const finalizingRef = useRef(false);
  // Mirrors awaitingId so an in-flight finalize() can tell the turn was torn
  // down (Stop / New chat / thread switch) while its fetch was airborne.
  const awaitingIdRef = useRef<string | null>(null);
  // Bumped by "New chat" so an in-flight /chat reply from the OLD thread can't
  // land in the fresh one.
  const chatGenRef = useRef(0);
  // AUTOSAVE machinery. `saveChainRef` serializes every PUT: a turn's save only
  // starts after the previous one resolved, so the first save's "new" has
  // already been swapped for the real id before the second save reads it —
  // rapid turns can never mint two threads (and there is exactly ONE queueSave
  // call per completed turn, so no turn double-PUTs either). `saveTargetRef`
  // holds the id for the CURRENT conversation as a mutable box: saves queued
  // for an old conversation keep writing to the old box even if the user
  // switches threads before the chain drains.
  const saveChainRef = useRef<Promise<void>>(Promise.resolve());
  const saveTargetRef = useRef<{ id: string | null }>({ id: null });

  const awaiting = awaitingId !== null;
  const busy = awaiting || chatBusy;

  // Load the model catalog for the header picker (best-effort — stays on "default").
  useEffect(() => {
    let cancelled = false;
    get<{ models: ModelOption[] }>("/models")
      .then((d) => {
        // Only offer models the user can ACTUALLY run (provider connected);
        // tolerate older daemons that don't send the flag.
        if (!cancelled) setModels(d.models.filter((m) => m.available !== false));
      })
      .catch(() => {
        /* picker just stays on the server default */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Load the persona catalog (best-effort — falls back to "assistant" + Custom).
  useEffect(() => {
    let cancelled = false;
    get<{ personas: PersonaOption[] }>("/chat/personas")
      .then((d) => {
        if (!cancelled && d.personas?.length) setPersonas(d.personas);
      })
      .catch(() => {
        /* keep the fallback list */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Load the saved-thread list once (best-effort — the sidebar just stays empty).
  useEffect(() => {
    let cancelled = false;
    get<{ threads: ThreadSummary[] }>("/chat/threads")
      .then((d) => {
        if (!cancelled) setThreads(d.threads ?? []);
      })
      .catch(() => {
        /* sidebar stays empty */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Restore the saved persona choice + custom text (after mount, so SSR markup
  // matches the first client render).
  useEffect(() => {
    try {
      const saved = window.localStorage.getItem(PERSONA_KEY);
      if (saved) setPersona(saved);
      const savedCustom = window.localStorage.getItem(PERSONA_CUSTOM_KEY);
      if (savedCustom) setCustomPersona(savedCustom);
    } catch {
      /* ignore */
    }
  }, []);

  function choosePersona(value: string) {
    setPersona(value);
    try {
      window.localStorage.setItem(PERSONA_KEY, value);
    } catch {
      /* ignore */
    }
  }

  function editCustomPersona(value: string) {
    setCustomPersona(value);
    try {
      window.localStorage.setItem(PERSONA_CUSTOM_KEY, value);
    } catch {
      /* ignore */
    }
  }

  // ------------------------------------------------------------------ threads

  /** Silent sidebar refresh — autosaves and deletes call this; failures are moot. */
  async function refreshThreads() {
    try {
      const d = await get<{ threads: ThreadSummary[] }>("/chat/threads");
      setThreads(d.threads ?? []);
    } catch {
      /* quiet — the list just goes stale until the next refresh */
    }
  }

  /**
   * Queue ONE autosave for a completed turn. Called exactly once per turn
   * (chat success, regenerate success, agent finalize, Stop) with the full
   * bubble array — never from render — so a turn can never double-PUT.
   */
  function queueSave(msgs: ChatMessage[]) {
    if (msgs.length === 0) return;
    const target = saveTargetRef.current; // the conversation this save belongs to
    const personaValue = persona === CUSTOM_PERSONA ? customPersona.trim() : persona;
    saveChainRef.current = saveChainRef.current.then(async () => {
      try {
        const body: ThreadSaveBody = {
          messages: msgs,
          ...(personaValue ? { persona: personaValue } : {}),
        };
        const res = await put<ThreadSaveResult>(
          `/chat/threads/${target.id ?? "new"}`,
          body,
        );
        target.id = res.id; // "new" → real id; later saves in this convo reuse it
        if (saveTargetRef.current === target) setThreadId(res.id);
        await refreshThreads();
      } catch {
        /* autosave is best-effort — never disturb the conversation itself */
      }
    });
  }

  /** Load a saved thread into the pane (chat-mode concern; resets agent state). */
  async function openThread(id: string) {
    if (id === threadId) {
      setSidebarOpen(false);
      return;
    }
    // Orphan anything in flight from the previous conversation.
    chatGenRef.current += 1;
    awaitingIdRef.current = null;
    setAwaitingId(null);
    setChatBusy(false);
    setSessionId(null);
    setAttachments([]);
    setSelectedTools([]); // armed tools are per-conversation
    setToolsOpen(false);
    setToolQuery("");
    setActiveSkill(""); // so is the active skill
    setSlashDismissed(false);
    setError(null);
    setOffline(false);
    sinceRef.current = null;
    finalizingRef.current = false;
    try {
      const t = await get<ThreadDetail>(`/chat/threads/${id}`);
      setMessages(t.messages ?? []);
      setThreadId(t.id);
      saveTargetRef.current = { id: t.id };
      setMode("chat"); // saved threads continue as direct chat
      if (t.persona) {
        if (personas.some((p) => p.name === t.persona)) {
          choosePersona(t.persona);
        } else if (/\s/.test(t.persona) || t.persona.length > 40) {
          // Free-text instructions round-trip through the Custom slot.
          choosePersona(CUSTOM_PERSONA);
          editCustomPersona(t.persona);
        } else {
          choosePersona(t.persona); // unlisted name — the select tolerates it
        }
      }
      setSidebarOpen(false);
      inputRef.current?.focus();
    } catch (e) {
      if (e instanceof ApiError && e.status === 0) setOffline(true);
      else setError(e instanceof ApiError ? e.message : String(e));
    }
  }

  async function removeThread(id: string) {
    try {
      await del<void>(`/chat/threads/${id}`);
      setThreads((prev) => prev.filter((t) => t.id !== id));
      if (id === threadId) newChat(); // the open conversation is gone — clear the pane
      void refreshThreads();
    } catch (e) {
      if (e instanceof ApiError && e.status === 0) setOffline(true);
      else setError(e instanceof ApiError ? e.message : String(e));
    }
  }

  // ---------------------------------------------------------------- attachments

  async function addFiles(files: File[]) {
    setError(null);
    const room = MAX_ATTACHMENTS - attachmentsRef.current.length;
    if (room <= 0) {
      setError(`Up to ${MAX_ATTACHMENTS} files per message.`);
      return;
    }
    const accepted: File[] = [];
    for (const f of files) {
      if (f.size > MAX_FILE_BYTES) {
        setError(`${f.name} is too large (max 20 MB).`);
        continue;
      }
      if (accepted.length >= room) {
        setError(`Up to ${MAX_ATTACHMENTS} files per message.`);
        break;
      }
      accepted.push(f);
    }
    if (accepted.length === 0) return;
    setUploading(true);
    try {
      for (const f of accepted) {
        const content_b64 = await readAsBase64(f);
        const res = await post<UploadResult>("/documents/upload", {
          filename: f.name,
          content_b64,
        });
        setAttachments((prev) =>
          prev.length >= MAX_ATTACHMENTS
            ? prev
            : [...prev, { name: res.name, path: res.path, bytes: f.size }],
        );
      }
    } catch (e) {
      if (e instanceof ApiError && e.status === 0) setOffline(true);
      else setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setUploading(false);
    }
  }

  // Stable handle for the once-registered window drag listeners below.
  const addFilesRef = useRef(addFiles);
  addFilesRef.current = addFiles;

  function onPickFiles(e: React.ChangeEvent<HTMLInputElement>) {
    const files = e.target.files ? Array.from(e.target.files) : [];
    e.target.value = ""; // allow re-selecting the same file
    if (files.length) void addFiles(files);
  }

  function removeAttachment(index: number) {
    setAttachments((prev) => prev.filter((_, i) => i !== index));
  }

  // Full-page drag-and-drop: dragging files anywhere over the page lights up the
  // chat card with an accent ring; dropping uploads them. Registered on window so
  // the browser never navigates away to the dropped file.
  useEffect(() => {
    let depth = 0; // dragenter/dragleave fire per element — track nesting
    const hasFiles = (e: DragEvent) =>
      Array.from(e.dataTransfer?.types ?? []).includes("Files");
    const onDragEnter = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      depth += 1;
      setDragging(true);
    };
    const onDragOver = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
    };
    const onDragLeave = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      depth = Math.max(0, depth - 1);
      if (depth === 0) setDragging(false);
    };
    const onDrop = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      depth = 0;
      setDragging(false);
      const files = e.dataTransfer?.files;
      if (files && files.length) void addFilesRef.current(Array.from(files));
    };
    window.addEventListener("dragenter", onDragEnter);
    window.addEventListener("dragover", onDragOver);
    window.addEventListener("dragleave", onDragLeave);
    window.addEventListener("drop", onDrop);
    return () => {
      window.removeEventListener("dragenter", onDragEnter);
      window.removeEventListener("dragover", onDragOver);
      window.removeEventListener("dragleave", onDragLeave);
      window.removeEventListener("drop", onDrop);
    };
  }, []);

  // ------------------------------------------------- skills & tools (chat mode)

  // "/" skill dropdown state, derived from the composer text.
  const slashActive =
    mode === "chat" && !busy && input.startsWith("/") && !slashDismissed;
  const slashQuery = slashActive ? input.slice(1).trim().toLowerCase() : "";

  const skillMatches = useMemo(() => {
    if (!slashActive) return [] as SkillOption[];
    const list = skills ?? [];
    const filtered = slashQuery
      ? list.filter(
          (s) =>
            s.name.toLowerCase().includes(slashQuery) ||
            (s.description || "").toLowerCase().includes(slashQuery),
        )
      : list;
    // ALL matches — the dropdown scrolls. (An 8-row cap made the picker look
    // like it wasn't loading the whole skill library.)
    return filtered;
  }, [slashActive, slashQuery, skills]);

  const toolMatches = useMemo(() => {
    const list = toolCatalog ?? [];
    const q = toolQuery.trim().toLowerCase();
    if (!q) return list;
    return list.filter(
      (t) =>
        t.name.toLowerCase().includes(q) ||
        (t.description || "").toLowerCase().includes(q),
    );
  }, [toolCatalog, toolQuery]);

  // Lazily fetch + cache the skill catalog the first time "/" opens the picker.
  useEffect(() => {
    if (!slashActive || skillsFetchedRef.current) return;
    skillsFetchedRef.current = true;
    get<{ skills: SkillOption[] }>("/skills")
      .then((d) => setSkills(d.skills ?? []))
      .catch(() => {
        skillsFetchedRef.current = false; // a later "/" retries
        setSkills([]);
      });
  }, [slashActive]);

  // Lazily fetch + cache the tool registry the first time the "+" menu opens.
  useEffect(() => {
    if (!toolsOpen || toolsFetchedRef.current) return;
    toolsFetchedRef.current = true;
    setToolsError(null);
    get<{ tools: ToolOption[] }>("/tools")
      .then((d) => setToolCatalog(d.tools ?? []))
      .catch((e) => {
        toolsFetchedRef.current = false; // reopening retries
        setToolsError(e instanceof ApiError ? e.message : String(e));
      });
  }, [toolsOpen]);

  // Keep the highlighted skill row pinned to the top as the query changes.
  useEffect(() => {
    setSkillIndex(0);
  }, [slashQuery, slashActive]);

  // Close the "+" popover on any outside click.
  useEffect(() => {
    if (!toolsOpen) return;
    const onDown = (e: MouseEvent) => {
      if (!toolsPopRef.current?.contains(e.target as Node)) setToolsOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [toolsOpen]);

  // Agent mode hides both affordances — never leave the popover floating open.
  useEffect(() => {
    if (mode !== "chat") setToolsOpen(false);
  }, [mode]);

  function toggleTool(name: string) {
    setSelectedTools((prev) =>
      prev.includes(name)
        ? prev.filter((n) => n !== name)
        : prev.length >= MAX_TOOLS
          ? prev // at the cap — the row is disabled anyway
          : [...prev, name],
    );
  }

  function disarmTool(name: string) {
    setSelectedTools((prev) => prev.filter((n) => n !== name));
  }

  /** Select a skill from the "/" dropdown: chip on, "/query" text consumed. */
  function pickSkill(name: string) {
    setActiveSkill(name);
    setInput("");
    setSlashDismissed(false);
    inputRef.current?.focus();
  }

  // ---------------------------------------------------------- agent-mode machinery

  // Human-readable steps for the current agent turn, newest-first. Only events
  // after the turn boundary and tagged with this session's id count; consecutive
  // duplicates are collapsed so "Working…, Working…" reads as one line.
  const progress = useMemo(() => {
    if (!awaitingId) return [] as string[];
    const boundary = sinceRef.current;
    const out: string[] = [];
    for (const e of events) {
      if (e.id === boundary) break; // reached events from before this turn
      if (e.session_id !== awaitingId) continue;
      const label = stepLabel(e);
      if (!label) continue;
      if (out.length && out[out.length - 1] === label) continue;
      out.push(label);
    }
    return out;
  }, [events, awaitingId]);

  // Keep the newest message (or the live working bubble) in view.
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, awaitingId, chatBusy, progress.length]);

  // Fetch the finished session and turn it into the assistant's reply. Only acts
  // once the session has actually reached a terminal status (the `agent.completed`
  // event can land a beat before the session row flips), so a not-yet-done fetch
  // simply returns and lets the next event/poll retry.
  async function finalize(id: string) {
    if (finalizingRef.current) return;
    finalizingRef.current = true;
    try {
      // GET /sessions/{id} returns { session, transcript } — the session is
      // NESTED (unlike POST /sessions, which returns it flat). Read from the
      // wrapper, tolerating both shapes, so completion is actually detected
      // (reading a top-level `status` here always returned undefined => the
      // chat spun forever even though the session had finished).
      const res = await get<{ session?: SessionView } & Partial<SessionView>>(
        `/sessions/${id}`,
      );
      // The turn may have been torn down (Stop / New chat / thread switch)
      // while the fetch was airborne — never append into another conversation.
      if (awaitingIdRef.current !== id) return;
      const session = (res.session ?? (res as SessionView)) || ({} as SessionView);
      setOffline(false); // the daemon answered — clear any transient-blip banner
      const status = (session.status || "").toLowerCase();
      if (status !== "completed" && status !== "failed" && status !== "cancelled") {
        return; // still running — leave the working bubble up; retry later
      }
      const summary = (session.summary || "").trim();
      const content =
        status === "completed"
          ? summary || "(no response)"
          : summary ||
            `The agent stopped before finishing (${status}). Please try again.`;
      const full: ChatMessage[] = [
        ...messagesRef.current,
        { role: "assistant", content },
      ];
      setMessages(full);
      queueSave(full); // agent turns are conversations worth keeping too
      awaitingIdRef.current = null;
      setAwaitingId(null);
    } catch (e) {
      if (e instanceof ApiError && e.status === 0) {
        // Transient network blip — keep the turn alive and let the 1.5s poll
        // retry, so a reply that already completed server-side isn't dropped.
        setOffline(true);
        return;
      }
      // Hard failure: surface it and stop waiting so the turn doesn't hang forever.
      setError(e instanceof ApiError ? e.message : String(e));
      awaitingIdRef.current = null;
      setAwaitingId(null);
    } finally {
      finalizingRef.current = false;
    }
  }

  // PRIMARY completion signal: watch the live event stream for this session's
  // `agent.completed`. Scan only events newer than the turn boundary.
  useEffect(() => {
    if (!awaitingId) return;
    const boundary = sinceRef.current;
    for (const e of events) {
      if (e.id === boundary) break;
      if (e.session_id === awaitingId && e.type === "agent.completed") {
        void finalize(awaitingId);
        break;
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [events, awaitingId]);

  // FALLBACK: if the /events socket is down, poll the session until it finishes.
  // The interval is torn down whenever the turn ends or the component unmounts.
  useEffect(() => {
    if (!awaitingId) return;
    const timer = setInterval(() => void finalize(awaitingId), 1500);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [awaitingId]);

  // ------------------------------------------------------------------- sending

  /**
   * CHAT MODE core: one direct /chat completion over `history` (which must end
   * with a user message). Shared by sendChat and regenerate. On success the
   * reply is appended and the turn autosaved (the ONLY chat-mode save site).
   */
  async function completeChat(history: ChatMessage[], atts: UploadedFile[]) {
    const gen = chatGenRef.current;
    setMessages(history);
    setChatBusy(true);
    try {
      const { provider, model } = splitChoice(choice);
      const personaValue =
        persona === CUSTOM_PERSONA ? customPersona.trim() : persona;
      const body: ChatRequestBody = {
        // Full conversation every turn — the backend is stateless here.
        messages: history.map(({ role, content }) => ({ role, content })),
        ...(provider ? { provider } : {}),
        ...(model ? { model } : {}),
        ...(personaValue ? { persona: personaValue } : {}),
        ...(atts.length ? { attachments: atts.map((a) => a.path) } : {}),
        // The reply's playbook + armed tool loop (both sticky across turns).
        ...(activeSkill ? { skill: activeSkill } : {}),
        ...(selectedTools.length ? { tools: selectedTools.slice(0, MAX_TOOLS) } : {}),
      };
      const res = await post<ChatResponse>("/chat", body);
      if (chatGenRef.current !== gen) return; // "New chat" happened mid-flight
      const toolsUsed = (res.tools_used ?? []).filter((t) => Boolean(t));
      const full: ChatMessage[] = [
        ...history,
        {
          role: "assistant",
          content: (res.reply ?? "").trim() || "(no response)",
          ...(toolsUsed.length ? { toolsUsed } : {}),
        },
      ];
      setMessages(full);
      queueSave(full); // the turn is complete — persist it
    } catch (e) {
      if (chatGenRef.current !== gen) return;
      // Keep the typed thread intact — only surface the failure (a 502 carries
      // the provider's own message, e.g. a rate limit, in `detail`).
      if (e instanceof ApiError && e.status === 0) setOffline(true);
      else setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      if (chatGenRef.current === gen) setChatBusy(false);
    }
  }

  /** CHAT MODE: append the user's message and run one completion. */
  async function sendChat(message: string) {
    const atts = attachments;
    setAttachments([]); // chips are consumed by this message
    const userMsg: ChatMessage = {
      role: "user",
      content: message,
      ...(atts.length ? { attachmentNames: atts.map((a) => a.name) } : {}),
    };
    await completeChat([...messages, userMsg], atts);
  }

  /**
   * Drop the LAST assistant reply and re-run the completion over the history
   * ending at the preceding user message (chat mode only). The re-run saves
   * through the same single completeChat site, overwriting the thread with the
   * regenerated reply.
   */
  function regenerate() {
    if (busy || mode !== "chat") return;
    const msgs = messages;
    const last = msgs[msgs.length - 1];
    if (!last || last.role !== "assistant") return;
    const history = msgs.slice(0, -1);
    if (history.length === 0 || history[history.length - 1].role !== "user") return;
    setError(null);
    setOffline(false);
    void completeChat(history, []);
  }

  /** AGENT MODE: the original session flow (wait:false + live steps + finalize). */
  async function sendAgent(message: string) {
    const atts = attachments;
    setAttachments([]); // chips are consumed by this message
    // Match the kanban precedent: point the agent at the uploaded files in-text.
    const attachLines = atts.map((a) => `\n\nAttached file: ${a.path}`).join("");
    const task = message + attachLines;
    setMessages((prev) => [
      ...prev,
      {
        role: "user",
        content: message,
        ...(atts.length ? { attachmentNames: atts.map((a) => a.name) } : {}),
      },
    ]);
    // Mark where "this turn" begins in the event stream BEFORE kicking off work.
    sinceRef.current = eventsRef.current[0]?.id ?? null;
    try {
      let session: SessionView;
      if (sessionId) {
        // Continue the same chat — runs in the background (wait:false).
        session = await post<SessionView>(`/sessions/${sessionId}/continue`, {
          message: task,
          wait: false,
        });
      } else {
        // First message opens a session.
        const { provider, model } = splitChoice(choice);
        session = await post<SessionView>("/sessions", {
          task,
          agent_type: "builder",
          wait: false,
          ...(provider ? { provider } : {}),
          ...(model ? { model } : {}),
        });
      }
      // ALWAYS chain forward to the returned session id: `continue` spawns a NEW
      // session (recapping the old one), so the next turn must continue from it —
      // sticking with the first id would silently drop the intermediate turns.
      setSessionId(session.id);
      // Hand off to the event watcher + polling fallback to surface the reply.
      awaitingIdRef.current = session.id;
      setAwaitingId(session.id);
    } catch (e) {
      // Keep the typed thread intact — only surface the failure.
      if (e instanceof ApiError && e.status === 0) setOffline(true);
      else setError(e instanceof ApiError ? e.message : String(e));
    }
  }

  function send(text: string) {
    const message = text.trim();
    if (!message || busy) return;
    setError(null);
    setOffline(false);
    setInput("");
    if (mode === "chat") void sendChat(message);
    else void sendAgent(message);
  }

  // Ask the daemon to cancel the in-flight agent turn, then release the composer.
  // Cancel is best-effort — even if it fails server-side we stop waiting locally.
  function stop() {
    if (!awaitingId) return;
    post(`/sessions/${awaitingId}/cancel`).catch(() => {});
    const full: ChatMessage[] = [
      ...messagesRef.current,
      { role: "assistant", content: "Stopped." },
    ];
    setMessages(full);
    queueSave(full); // the (aborted) turn still completed a visible exchange
    awaitingIdRef.current = null;
    setAwaitingId(null); // also tears down the event watcher + polling interval
  }

  function newChat() {
    chatGenRef.current += 1; // orphan any in-flight /chat reply
    setMessages([]);
    setSessionId(null);
    awaitingIdRef.current = null;
    setAwaitingId(null); // also tears down any polling interval
    setChatBusy(false);
    setAttachments([]);
    setSelectedTools([]);
    setToolsOpen(false);
    setToolQuery("");
    setActiveSkill("");
    setSlashDismissed(false);
    setInput("");
    setError(null);
    setOffline(false);
    setThreadId(null);
    saveTargetRef.current = { id: null }; // next completed turn creates a fresh thread
    sinceRef.current = null;
    finalizingRef.current = false;
  }

  function prefill(text: string) {
    setInput(text);
    inputRef.current?.focus();
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    // While the "/" skill dropdown is open it owns the navigation keys.
    if (slashActive) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSkillIndex((i) => Math.min(i + 1, Math.max(skillMatches.length - 1, 0)));
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setSkillIndex((i) => Math.max(i - 1, 0));
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setSlashDismissed(true);
        return;
      }
      // Enter picks the highlighted skill; with no match it falls through and
      // sends the literal "/…" text like any other message.
      if (e.key === "Enter" && !e.shiftKey && skillMatches.length > 0) {
        e.preventDefault();
        pickSkill(skillMatches[Math.min(skillIndex, skillMatches.length - 1)].name);
        return;
      }
    }
    // Enter sends; Shift+Enter inserts a newline.
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send(input);
    }
  }

  const started = messages.length > 0 || sessionId !== null || threadId !== null;
  const personaNames = personas.map((p) => p.name);
  const selectedPersonaDesc =
    personas.find((p) => p.name === persona)?.description ?? "";

  return (
    <PageShell>
      <Reveal>
        <PageHeader
          title="Chat"
          subtitle="Talk to Iron Jarvis. Chat mode answers directly in seconds; Agent mode does real work with tools."
          actions={
            <div className="flex flex-wrap items-center gap-2">
              {/* Mode toggle: fast direct chat vs. the tool-using agent session. */}
              <div
                role="group"
                aria-label="Mode"
                className="flex items-center overflow-hidden rounded-xl border border-white/10 bg-white/[0.02]"
              >
                <button
                  type="button"
                  onClick={() => setMode("chat")}
                  aria-pressed={mode === "chat"}
                  title="Direct replies in seconds"
                  className={`inline-flex items-center gap-1.5 px-3 py-1.5 text-[13px] font-medium transition-colors ${
                    mode === "chat"
                      ? "bg-accent/15 text-accent-soft"
                      : "text-zinc-400 hover:text-zinc-200"
                  }`}
                >
                  <MessageSquare size={13} /> Chat
                </button>
                <button
                  type="button"
                  onClick={() => setMode("agent")}
                  aria-pressed={mode === "agent"}
                  title="Does real work with tools — files, web, terminals; slower"
                  className={`inline-flex items-center gap-1.5 px-3 py-1.5 text-[13px] font-medium transition-colors ${
                    mode === "agent"
                      ? "bg-accent/15 text-accent-soft"
                      : "text-zinc-400 hover:text-zinc-200"
                  }`}
                >
                  <Wrench size={13} /> Agent
                </button>
              </div>
              {mode === "chat" && (
                <select
                  aria-label="Persona"
                  value={persona}
                  onChange={(e) => choosePersona(e.target.value)}
                  disabled={busy}
                  title={
                    persona === CUSTOM_PERSONA
                      ? "Your own persona instructions"
                      : selectedPersonaDesc || "Persona for replies"
                  }
                  className="field w-auto py-1.5 text-[13px]"
                >
                  {/* Tolerate a saved persona the daemon no longer lists. */}
                  {!personaNames.includes(persona) && persona !== CUSTOM_PERSONA && (
                    <option value={persona}>{capitalize(persona)}</option>
                  )}
                  {personas.map((p) => (
                    <option key={p.name} value={p.name} title={p.description}>
                      {capitalize(p.name)}
                    </option>
                  ))}
                  <option value={CUSTOM_PERSONA}>Custom…</option>
                </select>
              )}
              <select
                aria-label="Model"
                value={choice}
                onChange={(e) => setChoice(e.target.value)}
                disabled={mode === "agent" ? awaiting || sessionId !== null : busy}
                title={
                  mode === "agent" && sessionId !== null
                    ? "Start a new chat to switch models"
                    : "Model for this chat"
                }
                className="field w-auto py-1.5 text-[13px]"
              >
                <option value="">default model</option>
                {models.map((m) => {
                  const v = `${m.provider}::${m.model}`;
                  return (
                    <option key={v} value={v}>
                      {m.provider} · {m.model}
                    </option>
                  );
                })}
              </select>
              <button
                onClick={newChat}
                disabled={
                  !started &&
                  attachments.length === 0 &&
                  selectedTools.length === 0 &&
                  activeSkill === ""
                }
                className="btn-ghost py-1.5 text-[13px]"
              >
                <Plus size={14} /> New chat
              </button>
            </div>
          }
        />
      </Reveal>

      <Reveal>
        <p className="flex items-center gap-2 text-xs text-zinc-500">
          <Sparkles size={13} className="shrink-0 text-accent-soft/70" />
          {mode === "chat"
            ? "Fast, direct answers — attach files or drop them anywhere on the page. Switch to Agent mode when you need real work done with tools."
            : "Replies come from a real agent that can read files, search, and use tools — you'll see its steps live as it works."}
        </p>
      </Reveal>

      {mode === "chat" && persona === CUSTOM_PERSONA && (
        <Reveal>
          <textarea
            value={customPersona}
            onChange={(e) => editCustomPersona(e.target.value)}
            rows={2}
            aria-label="Custom persona"
            placeholder="Describe the persona — e.g. “You are a sharp tax accountant. Be concise and cite the code section.”"
            className="field resize-y text-[13px]"
          />
        </Reveal>
      )}

      {offline && (
        <Reveal>
          <OfflineHint detail="Chat needs it running to reach your agent." />
        </Reveal>
      )}

      <Reveal>
        <div className="flex flex-col gap-4 md:flex-row md:items-start">
          {/* Mobile-only sidebar toggle (the sidebar is always visible on md+). */}
          <button
            type="button"
            onClick={() => setSidebarOpen((v) => !v)}
            aria-expanded={sidebarOpen}
            className="btn-ghost self-start py-1.5 text-[13px] md:hidden"
          >
            <History size={14} />{" "}
            {sidebarOpen
              ? "Hide chats"
              : `Chats${threads.length ? ` (${threads.length})` : ""}`}
          </button>

          {/* Threads sidebar */}
          <aside
            className={`${sidebarOpen ? "" : "hidden"} w-full shrink-0 md:block md:w-60`}
          >
            <Card pad={false} className="overflow-hidden">
              <div className="flex items-center justify-between border-b hairline px-3 py-2">
                <span className="text-[11px] font-medium uppercase tracking-wide text-zinc-500">
                  Threads
                </span>
                <button
                  type="button"
                  onClick={newChat}
                  className="btn-ghost px-2 py-1 text-[12px]"
                  title="Start a new conversation"
                >
                  <Plus size={13} /> New chat
                </button>
              </div>
              <div className="max-h-[70vh] overflow-y-auto p-1.5">
                {threads.length === 0 ? (
                  <p className="px-2.5 py-3 text-xs leading-relaxed text-zinc-500">
                    No saved chats yet — conversations appear here after the first
                    reply.
                  </p>
                ) : (
                  <div className="space-y-0.5">
                    {threads.map((t) => {
                      const active = t.id === threadId;
                      const count = msgCount(t);
                      return (
                        <div
                          key={t.id}
                          className={`group/thread relative rounded-lg border transition-colors ${
                            active
                              ? "border-accent/25 bg-accent/[0.08]"
                              : "border-transparent hover:bg-white/[0.04]"
                          }`}
                        >
                          <button
                            type="button"
                            onClick={() => void openThread(t.id)}
                            className="w-full px-2.5 py-2 pr-7 text-left"
                            title={t.title || "Untitled chat"}
                          >
                            <span
                              className={`block truncate text-[13px] ${
                                active ? "text-accent-soft" : "text-zinc-200"
                              }`}
                            >
                              {t.title || "Untitled chat"}
                            </span>
                            <span className="block text-[11px] text-zinc-500">
                              {timeAgo(t.updated_at)} · {count} msg
                              {count === 1 ? "" : "s"}
                            </span>
                          </button>
                          <button
                            type="button"
                            onClick={() => void removeThread(t.id)}
                            aria-label={`Delete ${t.title || "chat"}`}
                            title="Delete this chat"
                            className="absolute right-1.5 top-1/2 grid h-6 w-6 -translate-y-1/2 place-items-center rounded-md text-zinc-500 opacity-0 transition-opacity hover:bg-white/[0.06] hover:text-rose-300 focus-visible:opacity-100 group-hover/thread:opacity-100"
                          >
                            <Trash2 size={13} />
                          </button>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            </Card>
          </aside>

          {/* Conversation pane */}
          <div className="min-w-0 flex-1">
            <Card
              pad={false}
              className={`overflow-hidden transition-shadow ${
                dragging ? "ring-2 ring-accent/60" : ""
              }`}
            >
              {/* Message thread */}
              <div className="flex max-h-[60vh] min-h-[24rem] flex-col gap-4 overflow-y-auto p-4 sm:p-5">
                {messages.length === 0 && !busy ? (
                  <div className="flex flex-1 flex-col items-center justify-center gap-4">
                    <Empty icon={<MessageSquare size={28} />}>
                      {mode === "chat"
                        ? "Start a conversation. Ask a question, pick a persona, drop in a file — replies come back in seconds."
                        : "Start a conversation. Ask a question or describe what you need — your agent replies and reaches for tools on its own when they help."}
                    </Empty>
                    <div className="flex flex-wrap justify-center gap-2">
                      {EXAMPLES.map((ex) => (
                        <button
                          key={ex}
                          onClick={() => prefill(ex)}
                          className="rounded-full border border-white/[0.08] bg-white/[0.02] px-3 py-1.5 text-xs text-zinc-400 transition-colors hover:border-accent/40 hover:text-accent-soft"
                        >
                          {ex}
                        </button>
                      ))}
                    </div>
                  </div>
                ) : (
                  <>
                    {messages.map((m, i) => {
                      if (m.role === "user") {
                        return (
                          <Bubble key={i} role="user">
                            {m.content}
                            {m.attachmentNames && m.attachmentNames.length > 0 && (
                              <AttachmentFooter names={m.attachmentNames} />
                            )}
                          </Bubble>
                        );
                      }
                      // Assistant: markdown + hover actions (copy / regenerate).
                      const canRegen =
                        i === messages.length - 1 &&
                        i > 0 &&
                        messages[i - 1].role === "user" &&
                        mode === "chat" &&
                        !busy;
                      return (
                        <div key={i} className="group/msg">
                          <Bubble role="assistant">
                            <Markdown content={m.content} />
                          </Bubble>
                          {/* Tools the reply's tool loop actually ran */}
                          {m.toolsUsed && m.toolsUsed.length > 0 && (
                            <div className="ml-11 mt-1 flex min-w-0 items-center gap-1.5 text-[11px] text-zinc-500">
                              <Wrench size={10} className="shrink-0 text-accent-soft/70" />
                              <span className="truncate">
                                used: {m.toolsUsed.join(", ")}
                              </span>
                            </div>
                          )}
                          <div className="ml-11 mt-1 flex items-center gap-0.5 opacity-0 transition-opacity focus-within:opacity-100 group-hover/msg:opacity-100">
                            <CopyIconButton text={m.content} title="Copy message" />
                            {canRegen && (
                              <button
                                type="button"
                                onClick={regenerate}
                                title="Regenerate reply"
                                aria-label="Regenerate reply"
                                className="grid h-6 w-6 place-items-center rounded-md text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-200"
                              >
                                <RefreshCw size={12} />
                              </button>
                            )}
                          </div>
                        </div>
                      );
                    })}
                    {/* CHAT MODE: a subtle thinking shimmer — no step feed needed. */}
                    {chatBusy && (
                      <Bubble role="assistant">
                        <span className="inline-flex items-center gap-2 text-zinc-400">
                          <Loader2 size={14} className="animate-spin text-accent-soft" />
                          <span className="animate-pulse">Thinking…</span>
                        </span>
                      </Bubble>
                    )}
                    {/* AGENT MODE: the live working bubble narrating agent steps. */}
                    {awaiting && (
                      <Bubble role="assistant">
                        <div className="flex flex-col gap-1.5">
                          <span className="inline-flex items-center gap-2 text-zinc-300">
                            <Loader2 size={14} className="animate-spin text-accent-soft" />
                            {progress[0] ?? "Thinking…"}
                          </span>
                          {progress.length > 1 && (
                            <ul className="ml-[22px] space-y-0.5 text-xs text-zinc-500">
                              {progress.slice(1, 4).map((s, i) => (
                                <li key={i} className="flex items-center gap-1.5">
                                  <span className="h-1 w-1 shrink-0 rounded-full bg-zinc-600" />
                                  {s}
                                </li>
                              ))}
                            </ul>
                          )}
                        </div>
                      </Bubble>
                    )}
                  </>
                )}
                <div ref={bottomRef} />
              </div>

              {error && (
                <div className="border-t hairline p-3">
                  <ErrorNote>{error}</ErrorNote>
                </div>
              )}

              {/* Chips queued for the next message — active skill + armed tools
                  (chat mode) share the row with attachment chips. */}
              {(attachments.length > 0 ||
                (mode === "chat" &&
                  (activeSkill !== "" || selectedTools.length > 0))) && (
                <div className="flex flex-wrap items-center gap-2 border-t hairline px-3 py-2.5">
                  {mode === "chat" && activeSkill !== "" && (
                    <span className="inline-flex items-center gap-1.5 rounded-full border border-accent/25 bg-accent/[0.06] px-2.5 py-1 text-[11px] text-zinc-300">
                      <Sparkles size={11} className="shrink-0 text-accent-soft" />
                      <span className="max-w-[14rem] truncate font-mono">
                        {activeSkill}
                      </span>
                      <button
                        type="button"
                        onClick={() => setActiveSkill("")}
                        aria-label={`Clear skill ${activeSkill}`}
                        title="Clear skill"
                        className="text-zinc-500 transition-colors hover:text-rose-300"
                      >
                        <X size={11} />
                      </button>
                    </span>
                  )}
                  {mode === "chat" &&
                    selectedTools.map((name) => (
                      <span
                        key={name}
                        className="inline-flex items-center gap-1.5 rounded-full border border-accent/25 bg-accent/[0.06] px-2.5 py-1 text-[11px] text-zinc-300"
                      >
                        <Wrench size={11} className="shrink-0 text-accent-soft" />
                        <span className="max-w-[14rem] truncate font-mono">
                          {name}
                        </span>
                        <button
                          type="button"
                          onClick={() => disarmTool(name)}
                          aria-label={`Disarm ${name}`}
                          title="Disarm tool"
                          className="text-zinc-500 transition-colors hover:text-rose-300"
                        >
                          <X size={11} />
                        </button>
                      </span>
                    ))}
                  {attachments.map((a, i) => (
                    <span
                      key={`${a.path}-${i}`}
                      className="inline-flex items-center gap-1.5 rounded-full border border-accent/25 bg-accent/[0.06] px-2.5 py-1 text-[11px] text-zinc-300"
                    >
                      <Paperclip size={11} className="shrink-0 text-accent-soft" />
                      <span className="max-w-[14rem] truncate">{a.name}</span>
                      <span className="text-zinc-500">{fmtSize(a.bytes)}</span>
                      <button
                        type="button"
                        onClick={() => removeAttachment(i)}
                        aria-label={`Remove ${a.name}`}
                        className="text-zinc-500 transition-colors hover:text-rose-300"
                      >
                        <X size={11} />
                      </button>
                    </span>
                  ))}
                </div>
              )}

              {/* Composer */}
              <div className="relative flex items-end gap-2 border-t hairline p-3">
                {/* "/" skill picker — floats above the composer */}
                {slashActive && (
                  <div className="absolute bottom-full left-3 right-3 z-20 mb-2 overflow-hidden rounded-xl border border-white/10 bg-zinc-900 shadow-lg shadow-black/40">
                    {skills === null ? (
                      <p className="px-3 py-2.5 text-xs text-zinc-500">
                        Loading skills…
                      </p>
                    ) : skillMatches.length === 0 ? (
                      <p className="px-3 py-2.5 text-xs text-zinc-500">
                        no matching skill
                      </p>
                    ) : (
                      <div
                        role="listbox"
                        aria-label="Skills"
                        className="max-h-72 overflow-y-auto p-1"
                      >
                        <div className="px-2.5 pb-1 pt-1.5 text-[10px] font-semibold uppercase tracking-wide text-zinc-500">
                          {skillMatches.length} skill{skillMatches.length === 1 ? "" : "s"}
                          {slashQuery ? " matching" : ""} — ↑↓ + Enter, or keep typing
                        </div>
                        {skillMatches.map((s, i) => (
                          <button
                            key={s.name}
                            type="button"
                            role="option"
                            aria-selected={i === skillIndex}
                            ref={(el) => {
                              if (i === skillIndex)
                                el?.scrollIntoView({ block: "nearest" });
                            }}
                            onClick={() => pickSkill(s.name)}
                            onMouseEnter={() => setSkillIndex(i)}
                            title={s.description}
                            className={`flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left transition-colors ${
                              i === skillIndex
                                ? "bg-accent/[0.12] text-accent-soft"
                                : "text-zinc-300"
                            }`}
                          >
                            <Sparkles
                              size={12}
                              className="shrink-0 text-accent-soft/70"
                            />
                            <span className="shrink-0 font-mono text-[12px]">
                              {s.name}
                            </span>
                            <span className="truncate text-[11px] text-zinc-500">
                              {s.description}
                            </span>
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                )}
                <input
                  ref={fileRef}
                  type="file"
                  multiple
                  className="hidden"
                  onChange={onPickFiles}
                />
                <button
                  type="button"
                  onClick={() => fileRef.current?.click()}
                  disabled={uploading || attachments.length >= MAX_ATTACHMENTS}
                  aria-label="Attach files"
                  title={`Attach files (up to ${MAX_ATTACHMENTS}, 20 MB each) — or drop them anywhere`}
                  className="btn-ghost h-[2.75rem] px-3 py-0"
                >
                  {uploading ? <LoaderInline /> : <Paperclip size={15} />}
                </button>
                {/* "+" tools menu — arm registry tools for the /chat tool loop */}
                {mode === "chat" && (
                  <div ref={toolsPopRef} className="relative">
                    <button
                      type="button"
                      onClick={() => setToolsOpen((v) => !v)}
                      aria-expanded={toolsOpen}
                      aria-haspopup="true"
                      aria-label="Arm tools"
                      title={`Arm tools for this chat (up to ${MAX_TOOLS})`}
                      className={`btn-ghost h-[2.75rem] px-3 py-0 ${
                        toolsOpen || selectedTools.length > 0
                          ? "text-accent-soft"
                          : ""
                      }`}
                    >
                      <Plus size={16} />
                    </button>
                    {toolsOpen && (
                      <div className="absolute bottom-full left-0 z-20 mb-2 flex max-h-72 w-[min(22rem,calc(100vw-6rem))] flex-col overflow-hidden rounded-xl border border-white/10 bg-zinc-900 shadow-lg shadow-black/40">
                        <div className="shrink-0 border-b hairline p-2">
                          <div className="relative">
                            <Search
                              size={12}
                              className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-zinc-500"
                            />
                            <input
                              autoFocus
                              value={toolQuery}
                              onChange={(e) => setToolQuery(e.target.value)}
                              onKeyDown={(e) => {
                                if (e.key === "Escape") {
                                  e.preventDefault();
                                  setToolsOpen(false);
                                  inputRef.current?.focus();
                                }
                              }}
                              placeholder="Search tools…"
                              aria-label="Search tools"
                              className="field w-full py-1.5 pl-8 text-[12px]"
                            />
                          </div>
                          <p className="mt-1.5 px-0.5 text-[10px] text-zinc-500">
                            up to {MAX_TOOLS} tools · {selectedTools.length} armed
                          </p>
                        </div>
                        <div className="min-h-0 flex-1 overflow-y-auto p-1">
                          {toolsError ? (
                            <p className="px-2.5 py-2 text-[11px] text-rose-300">
                              {toolsError}
                            </p>
                          ) : toolCatalog === null ? (
                            <div className="px-2.5 py-2">
                              <LoaderInline />
                            </div>
                          ) : toolMatches.length === 0 ? (
                            <p className="px-2.5 py-2 text-[11px] text-zinc-500">
                              No tools match.
                            </p>
                          ) : (
                            <>
                              {toolMatches.slice(0, TOOL_LIST_CAP).map((t) => {
                                const checked = selectedTools.includes(t.name);
                                const atCap =
                                  !checked && selectedTools.length >= MAX_TOOLS;
                                return (
                                  <button
                                    key={t.name}
                                    type="button"
                                    role="checkbox"
                                    aria-checked={checked}
                                    disabled={atCap}
                                    onClick={() => toggleTool(t.name)}
                                    title={t.description}
                                    className={`flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left transition-colors ${
                                      atCap
                                        ? "opacity-40"
                                        : "hover:bg-white/[0.05]"
                                    }`}
                                  >
                                    <span
                                      className={`grid h-3.5 w-3.5 shrink-0 place-items-center rounded border ${
                                        checked
                                          ? "border-accent/60 bg-accent/20 text-accent-soft"
                                          : "border-white/20"
                                      }`}
                                    >
                                      {checked && <Check size={10} />}
                                    </span>
                                    <span
                                      className={`max-w-[45%] shrink-0 truncate font-mono text-[12px] ${
                                        checked
                                          ? "text-accent-soft"
                                          : "text-zinc-200"
                                      }`}
                                    >
                                      {t.name}
                                    </span>
                                    <span className="truncate text-[11px] text-zinc-500">
                                      {t.description}
                                    </span>
                                  </button>
                                );
                              })}
                              {toolMatches.length > TOOL_LIST_CAP && (
                                <p className="px-2.5 py-1.5 text-[10px] text-zinc-600">
                                  {toolMatches.length - TOOL_LIST_CAP} more —
                                  refine your search
                                </p>
                              )}
                            </>
                          )}
                        </div>
                      </div>
                    )}
                  </div>
                )}
                <textarea
                  ref={inputRef}
                  value={input}
                  onChange={(e) => {
                    setInput(e.target.value);
                    setSlashDismissed(false); // editing reopens the "/" dropdown
                  }}
                  onKeyDown={onKeyDown}
                  disabled={busy}
                  rows={1}
                  aria-label="Message"
                  placeholder="Message Iron Jarvis…  (Enter to send · Shift+Enter for a new line)"
                  className="field max-h-40 min-h-[2.75rem] flex-1 resize-none disabled:opacity-60"
                />
                {awaiting && (
                  <button
                    onClick={stop}
                    className="btn-ghost h-[2.75rem] px-3 py-0 text-[13px]"
                    title="Stop this turn"
                  >
                    <Square size={14} /> Stop
                  </button>
                )}
                <button
                  onClick={() => send(input)}
                  disabled={busy || !input.trim()}
                  className="btn-accent h-[2.75rem] px-4 py-0 text-[13px]"
                >
                  {busy ? (
                    <LoaderInline />
                  ) : (
                    <>
                      <Send size={16} /> Send
                    </>
                  )}
                </button>
              </div>
            </Card>
          </div>
        </div>
      </Reveal>
    </PageShell>
  );
}
