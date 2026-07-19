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
// save starts, or we'd mint duplicate threads). Threads also carry a `setup`
// snapshot (armed tools/skill/workspace/model) that restores on open — sent
// only once restored or user-changed, so a plain reply never clobbers a stored
// setup with empties. FAILED turns save too (the typed message + any streamed
// partial, marked interrupted) so nothing is lost to navigation, and the
// message returns to the composer for an edit/resend.
//
// Assistant bubbles render MARKDOWN (react-markdown + GFM) with styled code
// blocks (per-block copy button), tables, lists, and links; user bubbles stay
// plain pre-wrapped text.

import {
  createContext,
  isValidElement,
  memo,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";
import Link from "next/link";
import {
  AudioLines,
  Bot,
  Check,
  ChevronDown,
  ChevronRight,
  Copy,
  ExternalLink,
  FolderKanban,
  FolderOpen,
  FolderPen,
  Globe,
  History,
  Loader2,
  MessageSquare,
  Mic,
  MicOff,
  PanelRight,
  PanelRightClose,
  PanelRightOpen,
  Paperclip,
  Pencil,
  Plus,
  RefreshCw,
  RotateCcw,
  Save,
  Search,
  Send,
  Share2,
  Sparkles,
  Square,
  Trash2,
  User,
  Volume2,
  VolumeX,
  Wrench,
  X,
} from "lucide-react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { get, post, put, del, ApiError, API_BASE, ijToken } from "@/lib/api";
import type { IJEvent, ModelOption, SessionView } from "@/lib/types";
import { timeAgo } from "@/lib/format";
import { useEvents } from "@/lib/useEvents";
import { useDictation } from "@/lib/useDictation";
import { useTTS } from "@/lib/useTTS";
import { useChatStream, StreamError, type ToolCard } from "@/lib/useChatStream";
import { useRunStream } from "@/lib/useRunStream";
import { appendDictation } from "@/components/VoiceInput";
import { Card, Empty, ErrorNote, LoaderInline, OfflineHint } from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";
import { FilesPanel } from "@/components/terminal/FilesPanel";
import { DirectoryTree } from "@/components/terminal/DirectoryTree";
import { ShareChatDialog } from "@/components/chat/ShareChatDialog";
import {
  SourcesRow,
  WEB_TOOLS,
  extractWebSources,
  type ChatSource,
} from "@/components/chat/SourcesRow";

type Mode = "chat" | "agent";

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  /** Display names of files attached to this (user) message — footer chips. */
  attachmentNames?: string[];
  /** Uploaded paths of those attachments, so a Regenerate can re-ground on them
   *  (the reply is otherwise silently ungrounded while the chip still shows). */
  attachmentPaths?: string[];
  /** Registry tools the reply actually ran (assistant messages) — footer line. */
  toolsUsed?: string[];
  /** Web sources the reply's web tool calls actually surfaced (assistant
   *  messages) — rendered as a compact domain-chip row under the bubble. */
  sources?: ChatSource[];
  /** This assistant reply was cut off mid-stream (Stop, or a committed failure)
   *  — shown with a subtle marker so a partial answer never looks complete. */
  interrupted?: boolean;
}

/** What POST /chat expects. */
interface ChatRequestMessage {
  role: "user" | "assistant";
  content: string;
}
// A type alias (not an interface) so it carries an implicit string index
// signature and stays assignable to the streaming hook's generic `run(body)`.
type ChatRequestBody = {
  messages: ChatRequestMessage[];
  provider?: string;
  model?: string;
  persona?: string;
  attachments?: string[]; // uploaded document paths
  skill?: string; // playbook for the reply (omitted / "" = none)
  tools?: string[]; // armed registry tools (max 6) — the chat runs a tool loop
  workspace_dir?: string; // absolute folder armed file tools operate in
  project_id?: string; // context spine: grounds the reply in the project
};
interface ChatResponse {
  reply: string;
  provider?: string;
  model?: string;
  images?: string[];
  skill?: string;
  tools_used?: string[];
}

interface PersonaOption {
  /** Slug id used as the `persona` value on the /chat POST. */
  name: string;
  /** Human label for the picker (falls back to a capitalized name). */
  title: string;
  description: string;
  /** The system prompt the server resolves for this persona. */
  prompt: string;
  builtin: boolean;
  /** A built-in with a saved user override applied on top. */
  overridden: boolean;
}

/** PUT/POST /chat/personas body + response. */
interface PersonaSaveBody {
  title: string;
  description?: string;
  prompt: string;
}
interface PersonaSaveResult {
  persona: PersonaOption;
}
interface PersonaDeleteResult {
  deleted: boolean;
  reverted_to_builtin: boolean;
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

/** One row from GET /projects — the fields the chat's project panel needs. */
interface ProjectOption {
  id: string;
  name: string;
  root?: string | null;
  /** False = the folder is confirmed missing (file tools stay off). */
  root_exists?: boolean;
  status?: string;
  default_provider?: string | null;
  default_model?: string | null;
}

/** One row from GET /chat/threads (newest first). `messages` is a count, but
 * tolerate a daemon that inlines the array. */
interface ThreadSummary {
  id: string;
  title: string;
  persona?: string;
  /** Context spine: the project this thread was tagged into (or null). */
  project_id?: string | null;
  messages: number | ChatMessage[];
  updated_at: string;
}

/** Per-thread setup the daemon stores alongside the transcript: what was armed
 *  when the conversation last saved. Returned as `setup` by GET
 *  /chat/threads/{id}; accepted on PUT (all five keys sent, empties meaning
 *  deliberately cleared). */
interface ThreadSetup {
  tools?: string[];
  skill?: string;
  workspace_dir?: string;
  provider?: string;
  model?: string;
}

/** GET /chat/threads/{id}. */
interface ThreadDetail {
  id: string;
  title: string;
  persona?: string;
  /** Context spine: the project this thread was tagged into (or null). */
  project_id?: string | null;
  messages: ChatMessage[];
  /** The armed tools/skill/workspace/model to restore (older daemons omit it). */
  setup?: ThreadSetup | null;
}

/** PUT /chat/threads/{id} body + response. */
interface ThreadSaveBody {
  messages: ChatMessage[];
  title?: string;
  persona?: string;
  setup?: ThreadSetup;
  /** The project tag (context spine). Explicit null deliberately clears it. */
  project_id?: string | null;
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

// Agent-mode handoff: escalating a chat conversation to a NEW agent session
// otherwise starts the agent blind (a fresh session carries no chat history),
// so we prepend a compact recap of the last few turns to the task.
const HANDOFF_TURNS = 6; // last N messages carried into the recap
const HANDOFF_CLIP = 600; // chars kept per message

// "+" tool menu grouping: bucket the flat registry into a few friendly
// categories by name/description. Heuristic — "other" catches the rest.
type ToolCategory = "integrations" | "files" | "web" | "media" | "documents" | "other";
const TOOL_CATEGORY_ORDER: ToolCategory[] = [
  "integrations",
  "files",
  "web",
  "media",
  "documents",
  "other",
];
const TOOL_CATEGORY_LABEL: Record<ToolCategory, string> = {
  integrations: "Integrations (MCP)",
  files: "Files",
  web: "Web",
  media: "Media",
  documents: "Documents",
  other: "Other",
};
// Checked in order — first match wins. Integrations (external MCP tools, named
// mcp__server__tool) come first so a connected Gmail/Drive tool never lands in
// a generic bucket. Media/documents precede the broad Files bucket so
// "read_pdf" / "image_convert" don't fall into it.
const TOOL_CATEGORY_RULES: { cat: ToolCategory; rx: RegExp }[] = [
  { cat: "integrations", rx: /^mcp__/ },
  {
    cat: "media",
    rx: /(image|video|audio|media|pixio|vision|song|music|photo|picture|render|\bsfx\b|\btts\b|\bvoice\b|speech)/,
  },
  {
    cat: "documents",
    rx: /(pdf|docx|xlsx|pptx|spreadsheet|\bdocument\b|\bdoc\b|slide|presentation|\bsheet\b)/,
  },
  { cat: "web", rx: /(\bweb\b|http|\burl\b|fetch|browse|scrape|crawl|\bsearch\b)/ },
  {
    cat: "files",
    rx: /(file|directory|folder|\bpath\b|glob|grep|\bread\b|\bwrite\b|\blist\b|\bfs\b)/,
  },
];

function categorizeTool(t: ToolOption): ToolCategory {
  const hay = `${t.name} ${t.description || ""}`.toLowerCase();
  for (const { cat, rx } of TOOL_CATEGORY_RULES) {
    if (rx.test(hay)) return cat;
  }
  return "other";
}

/** Compact "Conversation so far:" recap prepended to a new agent session. */
function conversationRecap(msgs: ChatMessage[]): string {
  if (msgs.length === 0) return "";
  const lines = msgs.slice(-HANDOFF_TURNS).map((m) => {
    const who = m.role === "user" ? "User" : "Assistant";
    const text = m.content.trim();
    const clipped =
      text.length > HANDOFF_CLIP ? `${text.slice(0, HANDOFF_CLIP)}…` : text;
    return `${who}: ${clipped}`;
  });
  return `Conversation so far:\n${lines.join("\n")}`;
}

// Persona persistence (chat mode only).
const PERSONA_KEY = "ij_chat_persona";
// Sentinel select value for the "+ New persona" entry (opens a blank editor).
const NEW_PERSONA = "__new__";

// Workspace panel persistence (chat mode). The chosen folder + expanded state.
const WORKSPACE_KEY = "ij_chat_workspace";
const WORKSPACE_OPEN_KEY = "ij_chat_workspace_open";
// The right-panel project selection persists across visits (like the folder).
const PROJECT_KEY = "ij_chat_project";
// Selecting a project with a live folder auto-arms the file essentials (find,
// extract, create) — 4 of the 6 tool slots, so the Web chip still fits beside
// them (the server truncates body.tools at six; nothing may be silently
// dropped off the end).
const PROJECT_FILE_TOOLS = [
  "file_search",
  "read_document",
  "write_document",
  "write_file",
];

// Fallback until GET /chat/personas answers (or if it never does).
const DEFAULT_PERSONAS: PersonaOption[] = [
  {
    name: "assistant",
    title: "Assistant",
    description: "Helpful general-purpose assistant",
    prompt: "",
    builtin: true,
    overridden: false,
  },
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
        <pre className="overflow-x-auto rounded border border-white/[0.06] bg-ink-900/80 p-3 font-mono text-xs leading-relaxed text-zinc-200">
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
  img: MarkdownMedia,
};

/** Media extensions the daemon's /creative/file-by-path endpoint will serve.
 *  Keep in sync with creative/service.py IMAGE/VIDEO/AUDIO_EXTS. */
const MEDIA_EXT_RX =
  /\.(png|jpe?g|webp|gif|bmp|svg|mp4|webm|mov|m4v|avi|mkv|mp3|wav|ogg|m4a|flac|aac|opus)$/i;
const VIDEO_EXT_RX = /\.(mp4|webm|mov|m4v|avi|mkv)$/i;
const AUDIO_EXT_RX = /\.(mp3|wav|ogg|m4a|flac|aac|opus)$/i;

/**
 * Inline media in replies — the "show me" half of the creative loop. The pixio
 * tools save generations to LOCAL paths and tell the model to embed them as
 * markdown images; a browser can't load `C:\…\pixio\out.png` directly, so
 * local absolute paths are rewritten through the daemon's guarded
 * /creative/file-by-path (media extensions only; ?token= because <img> can't
 * send an Authorization header). Video/audio extensions get real players.
 */
function MarkdownMedia({ src, alt }: { src?: string | Blob; alt?: string }) {
  const raw = typeof src === "string" ? src : "";
  if (!raw) return null;
  const isLocal = /^([A-Za-z]:[\\/]|\/(?!\/))/.test(raw) || raw.startsWith("file://");
  let resolved = raw;
  if (isLocal) {
    const path = raw.replace(/^file:\/\//, "");
    if (!MEDIA_EXT_RX.test(path)) {
      return <code className="text-[12px] text-zinc-400">{raw}</code>;
    }
    const token = ijToken();
    resolved = `${API_BASE}/creative/file-by-path?path=${encodeURIComponent(path)}${
      token ? `&token=${encodeURIComponent(token)}` : ""
    }`;
  }
  if (VIDEO_EXT_RX.test(raw)) {
    return (
      <video
        src={resolved}
        controls
        preload="metadata"
        className="my-2 max-h-96 w-full max-w-xl rounded-xl border border-white/10"
      />
    );
  }
  if (AUDIO_EXT_RX.test(raw)) {
    return <audio src={resolved} controls className="my-2 w-full max-w-xl" />;
  }
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={resolved}
      alt={alt || "generated media"}
      loading="lazy"
      className="my-2 max-h-96 w-auto max-w-full rounded-xl border border-white/10"
    />
  );
}

const REMARK_PLUGINS = [remarkGfm];

function Markdown({ content }: { content: string }) {
  return (
    <ReactMarkdown remarkPlugins={REMARK_PLUGINS} components={MD_COMPONENTS}>
      {content}
    </ReactMarkdown>
  );
}

/** Markdown for a SETTLED assistant message, memoized on its content. During a
 *  streaming turn the page re-renders on every token; without this, every prior
 *  assistant bubble would re-run the full remark/rehype parse each token (cost
 *  O(thread size) per token). A prior message's `content` string is referentially
 *  stable, so memo skips the re-parse and streaming stays smooth on long threads. */
const MemoMarkdown = memo(function MemoMarkdown({ content }: { content: string }) {
  return <Markdown content={content} />;
});

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
            ? "whitespace-pre-wrap break-words [overflow-wrap:anywhere] border-accent/25 bg-accent/[0.1] text-zinc-100"
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

// --------------------------------------------------------------- streaming UI

/** A compact list of live tool calls (the streaming hooks' `ToolCard`s, already
 *  redacted server-side): spinner while running, check/✗ when done, each with
 *  the tool name and a short output preview. */
function ToolCardList({ cards }: { cards: readonly ToolCard[] }) {
  if (!cards.length) return null;
  return (
    <div className="mt-1.5 flex flex-col gap-1">
      {cards.map((c) => {
        const running = c.status !== "done";
        const ok = c.ok !== false;
        return (
          <div
            key={c.id}
            className="flex items-start gap-2 rounded-lg border border-white/[0.06] bg-white/[0.02] px-2.5 py-1.5"
          >
            <span className="mt-0.5 shrink-0">
              {running ? (
                <Loader2 size={12} className="animate-spin text-accent-soft" />
              ) : ok ? (
                <Check size={12} className="text-emerald-400" />
              ) : (
                <X size={12} className="text-rose-400" />
              )}
            </span>
            <div className="min-w-0 flex-1">
              <span className="font-mono text-[12px] text-zinc-200">{c.name}</span>
              {c.output && (
                <div className="mt-0.5 line-clamp-2 whitespace-pre-wrap break-words text-[11px] text-zinc-500">
                  {c.output}
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/** Streamed assistant markdown with a blinking caret pinned after the last line
 *  (a `::after` on the final block, so it sits inline with the running text). */
function StreamingText({ content }: { content: string }) {
  return (
    <div className="[&>*:last-child]:after:ml-0.5 [&>*:last-child]:after:inline-block [&>*:last-child]:after:h-[0.95em] [&>*:last-child]:after:w-[2px] [&>*:last-child]:after:translate-y-[1px] [&>*:last-child]:after:animate-caret [&>*:last-child]:after:rounded-full [&>*:last-child]:after:bg-accent-soft [&>*:last-child]:after:align-baseline [&>*:last-child]:after:content-['']">
      <Markdown content={content} />
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
  // CHAT MODE: the last turn that FAILED (kept intact so Retry can re-send the
  // exact same history + attachments). Cleared the moment a turn succeeds.
  const [failedTurn, setFailedTurn] = useState<{
    history: ChatMessage[];
    atts: UploadedFile[];
  } | null>(null);
  const [models, setModels] = useState<ModelOption[]>([]);
  const [choice, setChoice] = useState(""); // "" => server default model
  const [personas, setPersonas] = useState<PersonaOption[]>(DEFAULT_PERSONAS);
  const [persona, setPersona] = useState("assistant");
  // PERSONA EDITOR: a collapsible panel that edits the SELECTED persona (or a
  // brand-new one). Every persona is now savable — built-in edits write an
  // override, custom personas POST. The draft rides along verbatim as free text
  // if the user sends before saving, so unsaved tweaks still apply that turn.
  const [personaEditorOpen, setPersonaEditorOpen] = useState(false);
  const [isNewPersona, setIsNewPersona] = useState(false);
  const [draftTitle, setDraftTitle] = useState("");
  const [draftDescription, setDraftDescription] = useState("");
  const [draftPrompt, setDraftPrompt] = useState("");
  const [personaSaving, setPersonaSaving] = useState(false);
  const [personaSaved, setPersonaSaved] = useState(false); // brief success flash
  const [personaError, setPersonaError] = useState<string | null>(null);
  // WORKSPACE PANEL: a Build-like folder + live Files panel on the right. When a
  // folder is chosen it rides along as `workspace_dir` so the chat's armed file
  // tools write there (and their output surfaces live in the panel).
  const [workspaceDir, setWorkspaceDir] = useState<string | null>(null);
  const [workspaceOpen, setWorkspaceOpen] = useState(false);
  const [pickingFolder, setPickingFolder] = useState(false); // "change folder"
  const [attachments, setAttachments] = useState<UploadedFile[]>([]);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [input, setInput] = useState("");
  // "+" TOOLS MENU (chat mode): armed registry tool names — sent as `tools` on
  // every /chat turn and kept across turns until "New chat" / a thread switch.
  const [selectedTools, setSelectedTools] = useState<string[]>([]);
  // Bumped on every USER edit to the thread setup (tools/skill/workspace/model)
  // — drives the persist-on-change effect below. Restores never bump it, so
  // merely opening a thread can't churn its updated_at with an echo save.
  const [setupVersion, setSetupVersion] = useState(0);
  const [toolsOpen, setToolsOpen] = useState(false);
  const [toolQuery, setToolQuery] = useState("");
  // "+" menu: category groups the user has collapsed (selection is unaffected).
  const [collapsedCats, setCollapsedCats] = useState<Set<string>>(new Set());
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
  // Share dialog for the OPEN thread (full transcript / compacted digest).
  const [shareOpen, setShareOpen] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false); // mobile-only toggle
  const [threadQuery, setThreadQuery] = useState(""); // sidebar title filter
  const [threadsLoading, setThreadsLoading] = useState(true); // first threads fetch
  // The reader scrolled up: show a "Jump to latest" pill and STOP auto-scrolling
  // so streamed tokens don't yank them back down while they re-read.
  const [showJump, setShowJump] = useState(false);
  // PROJECT PANEL: the chat's context spine. Selecting a project scopes the
  // thread list, tags every turn/save/session with project_id (the daemon
  // grounds replies in the project's instructions + knowledge), points the
  // workspace at the project folder, and arms the file essentials. Selection
  // is only ever explicit (picker, ?project= deep link, or the last-used
  // choice) — the daemon's "active project" never auto-applies here.
  const [projects, setProjects] = useState<ProjectOption[]>([]);
  const [projectId, setProjectId] = useState<string | null>(null);
  const [promoting, setPromoting] = useState(false); // folder → project POST in flight
  // Mirrors projectId for saves/sends that fire from timers + event watchers.
  const projectIdRef = useRef<string | null>(null);
  // True while the armed tools are exactly what THIS panel auto-armed —
  // deselecting the project then clears only that set, never a user's own.
  const autoArmedRef = useRef(false);

  const { events } = useEvents(150);
  // Threads are scoped to the selected project (the daemon filters by
  // project_id); with no project every saved conversation shows. The sidebar's
  // title filter narrows client-side on top.
  const visibleThreads = useMemo(() => {
    const q = threadQuery.trim().toLowerCase();
    if (!q) return threads;
    return threads.filter((t) => (t.title || "").toLowerCase().includes(q));
  }, [threads, threadQuery]);
  const activeProject = useMemo(
    () => (projectId ? (projects.find((p) => p.id === projectId) ?? null) : null),
    [projects, projectId],
  );

  // ---- Voice. ONE dictation engine for both the composer mic and hands-free
  // Voice Chat (two instances would fight over the mic / recognition service).
  // Replies are spoken through the shared TTS preference (same toggle as the
  // session page). Voice Chat = listen → auto-send on pause → speak the reply
  // (mic held while speaking, so it never hears itself) → listen again.
  const dictation = useDictation();
  const tts = useTTS();
  // Token streaming: `stream` drives the live CHAT bubble (deltas + tool cards);
  // `runStream` drives the AGENT working bubble. Both are additive — the
  // non-streaming /chat POST and the session finalize path remain the fallback.
  const stream = useChatStream();
  const runStream = useRunStream();
  // Whether the current streaming turn has fed TTS yet (drives the once-per-turn
  // resetStream in feedTTS).
  const ttsStreamStartedRef = useRef(false);
  const [voiceMode, setVoiceMode] = useState(false);
  // Chars of dictation.transcript already flushed into the composer.
  const dictEmittedRef = useRef(0);
  // Voice Chat only auto-sends text that CAME from dictation — typing while
  // voice chat is on must never fire a surprise send.
  const inputFromVoiceRef = useRef(false);

  const bottomRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null); // the message scroll container
  // True while the reader is pinned to (near) the bottom. Only then do streamed
  // tokens auto-scroll; scrolling up releases the pin until they return.
  const pinnedRef = useRef(true);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  // Synchronous send guard: `busy` is React state and lags a frame, so two
  // Enter keydowns in the same tick both saw busy===false and double-sent. This
  // ref flips instantly and is the real gate; cleared when the turn settles.
  const sendingRef = useRef(false);
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
  // Latest stream tool cards, readable after `stream.run` settles (the closure's
  // `stream.tools` is frozen at send time; this ref tracks re-renders) — source
  // extraction reads it once per turn.
  const streamToolsRef = useRef<readonly ToolCard[]>(stream.tools);
  streamToolsRef.current = stream.tools;
  // THREAD SETUP persistence guard: saves include a `setup` snapshot only once
  // it was restored from the open thread or the user actually armed/changed
  // something — a plain reply on a thread whose setup wasn't restored (older
  // daemon, or nothing armed) must never PUT empties over a stored setup.
  const sendSetupRef = useRef(false);
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
  // The persona selected before "+ New persona" — restored if the new-persona
  // editor is closed without saving.
  const prevPersonaRef = useRef("assistant");
  // Clears the "Saved" flash; held in a ref so it can be cancelled on unmount.
  const personaSavedTimerRef = useRef<number | null>(null);
  useEffect(
    () => () => {
      if (personaSavedTimerRef.current !== null)
        window.clearTimeout(personaSavedTimerRef.current);
    },
    [],
  );

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

  // Load the saved-thread list, re-scoped whenever the project selection
  // changes (best-effort — the sidebar just stays empty).
  useEffect(() => {
    let cancelled = false;
    const q = projectId ? `?project_id=${encodeURIComponent(projectId)}` : "";
    get<{ threads: ThreadSummary[] }>(`/chat/threads${q}`)
      .then((d) => {
        if (!cancelled) setThreads(d.threads ?? []);
      })
      .catch(() => {
        /* sidebar stays empty */
      })
      .finally(() => {
        if (!cancelled) setThreadsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  // Restore the saved persona choice + workspace (after mount, so SSR markup
  // matches the first client render).
  useEffect(() => {
    try {
      const saved = window.localStorage.getItem(PERSONA_KEY);
      if (saved) {
        setPersona(saved);
        prevPersonaRef.current = saved;
      }
      const wd = window.localStorage.getItem(WORKSPACE_KEY);
      if (wd) setWorkspaceDir(wd);
      const wo = window.localStorage.getItem(WORKSPACE_OPEN_KEY);
      if (wo === "1") setWorkspaceOpen(true);
    } catch {
      /* ignore */
    }
  }, []);

  // Load the project list, then restore the selection: a /chat?project= deep
  // link (the Projects hub links here) wins over the last-used choice. Read
  // via window.location — /chat is a static route, so no useSearchParams.
  useEffect(() => {
    let cancelled = false;
    get<{ projects: ProjectOption[] }>("/projects")
      .then((d) => {
        if (cancelled) return;
        const list = d.projects ?? [];
        setProjects(list);
        let wanted: string | null = null;
        try {
          wanted = new URLSearchParams(window.location.search).get("project");
          if (!wanted) wanted = window.localStorage.getItem(PROJECT_KEY);
        } catch {
          /* ignore */
        }
        const found = wanted ? list.find((p) => p.id === wanted) : undefined;
        if (found) applyProject(found, { armDefaults: true });
      })
      .catch(() => {
        /* the panel just shows "No project" */
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function choosePersona(value: string) {
    setPersona(value);
    prevPersonaRef.current = value;
    try {
      window.localStorage.setItem(PERSONA_KEY, value);
    } catch {
      /* ignore */
    }
  }

  /** Select a persona for THIS conversation without touching the stored
   *  default. Threads round-trip their persona verbatim — including free-text
   *  unsaved-draft prompts — so applying a loaded thread's persona through
   *  choosePersona would silently hijack the global default. Only an explicit
   *  pick or a saved persona goes through choosePersona. */
  function selectPersonaLocal(value: string) {
    setPersona(value);
    prevPersonaRef.current = value;
  }

  // ------------------------------------------------------------------ personas

  /** Refetch the persona catalog (after any save/revert/delete). */
  async function refetchPersonas(): Promise<PersonaOption[]> {
    try {
      const d = await get<{ personas: PersonaOption[] }>("/chat/personas");
      const list = d.personas?.length ? d.personas : DEFAULT_PERSONAS;
      setPersonas(list);
      return list;
    } catch {
      return personas;
    }
  }

  /** Human label for a persona name (title, else capitalized/clipped name). */
  function personaTitle(name: string): string {
    const p = personas.find((x) => x.name === name);
    if (p) return p.title || capitalize(p.name);
    // A free-text persona (round-tripped from a saved thread) — clip it.
    return name.length > 32 ? `${name.slice(0, 32)}…` : capitalize(name);
  }

  /**
   * The `persona` value to send this turn. Normally the selected NAME (the
   * server resolves its prompt). But if the editor has UNSAVED prompt edits,
   * send the live edited prompt as free text so the tweak still applies —
   * saving is still preferred.
   */
  function personaForSend(): string {
    if (personaEditorOpen) {
      const p = draftPrompt.trim();
      if (isNewPersona) {
        if (p) return p; // an unsaved new persona is pure free text
      } else {
        const saved = (personas.find((x) => x.name === persona)?.prompt ?? "").trim();
        if (p && p !== saved) return p; // unsaved edits to a known persona
      }
    }
    if (persona === NEW_PERSONA) return ""; // "+ New persona", nothing typed yet
    return persona;
  }

  /** Open the editor prefilled from the CURRENTLY selected persona. */
  function openPersonaEditor() {
    const p = personas.find((x) => x.name === persona);
    setIsNewPersona(false);
    setDraftTitle(p?.title ?? capitalize(persona));
    setDraftDescription(p?.description ?? "");
    setDraftPrompt(p?.prompt ?? "");
    setPersonaError(null);
    setPersonaSaved(false);
    setPersonaEditorOpen(true);
  }

  /** "+ New persona" — remember the current choice, open a blank editor. */
  function startNewPersona() {
    prevPersonaRef.current = persona === NEW_PERSONA ? prevPersonaRef.current : persona;
    setIsNewPersona(true);
    setDraftTitle("");
    setDraftDescription("");
    setDraftPrompt("");
    setPersonaError(null);
    setPersonaSaved(false);
    setPersonaEditorOpen(true);
    setPersona(NEW_PERSONA); // not persisted — becomes real only on save
  }

  /** Collapse the editor WITHOUT saving (reverting a throwaway new-persona pick). */
  function closePersonaEditor() {
    setPersonaEditorOpen(false);
    setPersonaError(null);
    setPersonaSaved(false);
    if (persona === NEW_PERSONA) {
      // Local restore only: the previous value may be a thread's free-text
      // persona, which must not be (re)written as the stored default.
      selectPersonaLocal(prevPersonaRef.current || personas[0]?.name || "assistant");
    }
    setIsNewPersona(false);
  }

  function flashSaved() {
    setPersonaSaved(true);
    if (personaSavedTimerRef.current !== null)
      window.clearTimeout(personaSavedTimerRef.current);
    personaSavedTimerRef.current = window.setTimeout(
      () => setPersonaSaved(false),
      2200,
    );
  }

  /** Save the draft: PUT an existing/built-in name (override), POST a new one. */
  async function savePersona() {
    const title = draftTitle.trim();
    const prompt = draftPrompt.trim();
    const description = draftDescription.trim();
    if (!prompt) {
      setPersonaError("A prompt is required.");
      return;
    }
    setPersonaSaving(true);
    setPersonaError(null);
    try {
      const body: PersonaSaveBody = { title, prompt, ...(description ? { description } : {}) };
      const res = isNewPersona
        ? await post<PersonaSaveResult>("/chat/personas", body)
        : await put<PersonaSaveResult>(
            `/chat/personas/${encodeURIComponent(persona)}`,
            body,
          );
      const savedName = res.persona?.name ?? persona;
      await refetchPersonas();
      choosePersona(savedName); // keep the saved persona selected
      setIsNewPersona(false); // it's a real persona now — later saves PUT
      flashSaved();
    } catch (e) {
      if (e instanceof ApiError && e.status === 0) setOffline(true);
      setPersonaError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setPersonaSaving(false);
    }
  }

  /** Revert a built-in override / delete a custom persona, then refetch. */
  async function deletePersona() {
    setPersonaSaving(true);
    setPersonaError(null);
    try {
      await del<PersonaDeleteResult>(`/chat/personas/${encodeURIComponent(persona)}`);
      const list = await refetchPersonas();
      // Built-in revert keeps the name (now the pristine default); a deleted
      // custom persona is gone — fall back to the first available persona.
      if (!list.some((p) => p.name === persona)) {
        choosePersona(list[0]?.name ?? "assistant");
      }
      setPersonaEditorOpen(false); // reopen with Modify to see the reverted default
      setIsNewPersona(false);
    } catch (e) {
      if (e instanceof ApiError && e.status === 0) setOffline(true);
      setPersonaError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setPersonaSaving(false);
    }
  }

  // ----------------------------------------------------------------- workspace

  /** Pick the workspace folder (from the tree) — persists + returns to Files. */
  function chooseWorkspace(path: string) {
    setWorkspaceDir(path);
    setPickingFolder(false);
    markSetupChanged();
    try {
      window.localStorage.setItem(WORKSPACE_KEY, path);
    } catch {
      /* ignore */
    }
  }

  function setWorkspaceOpenPersisted(open: boolean) {
    setWorkspaceOpen(open);
    try {
      window.localStorage.setItem(WORKSPACE_OPEN_KEY, open ? "1" : "0");
    } catch {
      /* ignore */
    }
  }

  // ------------------------------------------------------------------ project

  /** Keep ?project= in the URL in sync so the scoped view stays linkable
   *  (plain history API — /chat is a static route; no useSearchParams). */
  function syncProjectUrl(id: string | null) {
    try {
      const url = new URL(window.location.href);
      if (id) url.searchParams.set("project", id);
      else url.searchParams.delete("project");
      window.history.replaceState(null, "", url.toString());
    } catch {
      /* ignore */
    }
  }

  /** Point the chat at *p*: scope threads, tag turns, and (with `armDefaults`)
   *  aim the workspace at the project folder, arm the file essentials over an
   *  empty tool set, and adopt the project's default model when none is
   *  chosen. `armDefaults` stays off when a thread restore drives the switch —
   *  the thread's own saved setup wins. */
  function applyProject(p: ProjectOption, opts: { armDefaults: boolean }) {
    setProjectId(p.id);
    projectIdRef.current = p.id;
    try {
      window.localStorage.setItem(PROJECT_KEY, p.id);
    } catch {
      /* ignore */
    }
    syncProjectUrl(p.id);
    if (opts.armDefaults) {
      const folderLive = Boolean(p.root) && p.root_exists !== false;
      if (folderLive) setWorkspaceDir(p.root as string);
      if (folderLive && selectedTools.length === 0) {
        setSelectedTools(PROJECT_FILE_TOOLS);
        autoArmedRef.current = true;
      }
      if (choice === "" && p.default_provider && p.default_model) {
        setChoice(`${p.default_provider}::${p.default_model}`);
      }
    }
  }

  /** Back to plain chat: unscope the list, stop tagging, release anything the
   *  panel auto-armed, and return the workspace to the user's own default. */
  function clearProject() {
    setProjectId(null);
    projectIdRef.current = null;
    try {
      window.localStorage.removeItem(PROJECT_KEY);
    } catch {
      /* ignore */
    }
    syncProjectUrl(null);
    if (autoArmedRef.current) {
      autoArmedRef.current = false;
      setSelectedTools((prev) =>
        prev.every((t) => PROJECT_FILE_TOOLS.includes(t)) ? [] : prev,
      );
    }
    try {
      setWorkspaceDir(window.localStorage.getItem(WORKSPACE_KEY) || null);
    } catch {
      setWorkspaceDir(null);
    }
  }

  /** Promote the ad-hoc workspace folder into a real project (named after the
   *  folder) and select it — the one-click "this folder IS my project" path. */
  async function promoteFolderToProject() {
    const dir = workspaceDir;
    if (!dir || projectIdRef.current || promoting) return;
    setPromoting(true);
    try {
      const name = dir.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || dir;
      const p = await post<ProjectOption>("/projects", { name, root: dir });
      setProjects((prev) => [p, ...prev]);
      applyProject(p, { armDefaults: true });
      markSetupChanged();
    } catch (e) {
      if (e instanceof ApiError && e.status === 0) setOffline(true);
      else setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setPromoting(false);
    }
  }

  /** The rail select's onChange — an explicit pick also persists to the open
   *  thread's setup (arming/workspace changes count as setup edits). */
  function chooseProject(id: string) {
    if (id) {
      const p = projects.find((x) => x.id === id);
      if (!p) return;
      applyProject(p, { armDefaults: true });
    } else {
      clearProject();
    }
    markSetupChanged();
  }

  // ------------------------------------------------------------------ threads

  /** Silent sidebar refresh — autosaves and deletes call this; failures are
   *  moot. Scoped to the selected project (read via ref: refreshes fire from
   *  the autosave chain, where closures go stale). */
  async function refreshThreads() {
    try {
      const pid = projectIdRef.current;
      const q = pid ? `?project_id=${encodeURIComponent(pid)}` : "";
      const d = await get<{ threads: ThreadSummary[] }>(`/chat/threads${q}`);
      setThreads(d.threads ?? []);
    } catch {
      /* quiet — the list just goes stale until the next refresh */
    }
  }

  /** The thread-setup snapshot for saves: exactly what's armed right now. All
   *  five keys always ride along so a cleared skill/model reads as deliberately
   *  cleared, not merely omitted. */
  function currentSetup(): ThreadSetup {
    const { provider, model } = splitChoice(choice);
    return {
      tools: selectedTools.slice(0, MAX_TOOLS),
      skill: activeSkill,
      workspace_dir: workspaceDir ?? "",
      provider: provider ?? "",
      model: model ?? "",
    };
  }

  /** Mark the thread setup as USER-changed: saves start carrying it, and the
   *  effect below persists the change to an already-saved thread right away
   *  (arming a tool then navigating off must not lose it). */
  function markSetupChanged() {
    sendSetupRef.current = true;
    setSetupVersion((v) => v + 1);
  }

  // Persist USER setup edits to the open thread even without a new turn. Runs
  // only on real edits (setupVersion never bumps on a thread-open restore) and
  // only once the conversation is already saved — an unsaved chat's first turn
  // carries the setup itself.
  useEffect(() => {
    if (setupVersion === 0) return;
    if (!saveTargetRef.current.id || messagesRef.current.length === 0) return;
    queueSave(messagesRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [setupVersion]);

  /**
   * Queue ONE autosave for a completed turn. Called exactly once per turn
   * (chat success, regenerate success, agent finalize, Stop) with the full
   * bubble array — never from render — so a turn can never double-PUT.
   */
  function queueSave(msgs: ChatMessage[]) {
    if (msgs.length === 0) return;
    const target = saveTargetRef.current; // the conversation this save belongs to
    const personaValue = personaForSend();
    // Setup rides along only once it was restored from this thread or the user
    // actually changed something (see sendSetupRef) — never clobber a stored
    // setup with empties just because nothing was re-armed this visit.
    const setup = sendSetupRef.current ? currentSetup() : null;
    saveChainRef.current = saveChainRef.current.then(async () => {
      try {
        const body: ThreadSaveBody = {
          messages: msgs,
          // The project tag follows the CURRENT selection — the spine survives
          // thread switches, and an explicit null untags deliberately.
          project_id: projectIdRef.current,
          ...(personaValue ? { persona: personaValue } : {}),
          ...(setup ? { setup } : {}),
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
    stream.abort(); // tear down a live streaming turn (its throw won't fall back)
    tts.cancel(); // stop reading the previous thread's reply
    awaitingIdRef.current = null;
    setAwaitingId(null);
    setChatBusy(false);
    setFailedTurn(null);
    setSessionId(null);
    setAttachments([]);
    setSelectedTools([]); // armed tools are per-conversation
    sendSetupRef.current = false; // until this thread's setup (if any) restores
    setToolsOpen(false);
    setToolQuery("");
    setActiveSkill(""); // so is the active skill
    setSlashDismissed(false);
    setError(null);
    setOffline(false);
    sinceRef.current = null;
    finalizingRef.current = false;
    sendingRef.current = false;
    pinnedRef.current = true; // a loaded thread scrolls to its latest message
    setShowJump(false);
    try {
      const t = await get<ThreadDetail>(`/chat/threads/${id}`);
      setMessages(t.messages ?? []);
      setThreadId(t.id);
      saveTargetRef.current = { id: t.id };
      // Context follows the conversation: a project-tagged thread scopes the
      // chat to its project; an untagged one unscopes it. armDefaults stays
      // off — the thread's own saved setup (restored below) wins; without a
      // setup, the project folder still becomes the workspace.
      const tpid = t.project_id ?? null;
      if (tpid !== projectIdRef.current) {
        const proj = tpid ? projects.find((x) => x.id === tpid) : undefined;
        if (tpid && proj) {
          applyProject(proj, { armDefaults: false });
        } else if (tpid) {
          // Unknown project (list still loading / deleted) — keep the tag.
          setProjectId(tpid);
          projectIdRef.current = tpid;
          syncProjectUrl(tpid);
        } else {
          clearProject();
        }
        if (proj?.root && proj.root_exists !== false && !t.setup?.workspace_dir) {
          setWorkspaceDir(proj.root);
        }
      }
      setMode("chat"); // saved threads continue as direct chat
      setPersonaEditorOpen(false); // never carry a stale draft into another thread
      // A known name selects normally; an unlisted name / free-text instructions
      // are tolerated by the select (and sent verbatim, which the server treats
      // as free text). LOCAL selection only — a round-tripped persona (possibly
      // an unsaved free-text draft) must never overwrite the stored default.
      if (t.persona) selectPersonaLocal(t.persona);
      // Restore the thread's saved setup so reopening a conversation comes back
      // armed the way it was left. sendSetupRef stays false when the thread has
      // none — a plain reply then never PUTs empties over a stored setup.
      const setup = t.setup;
      if (setup && typeof setup === "object") {
        setSelectedTools(
          Array.isArray(setup.tools)
            ? setup.tools.filter((x) => typeof x === "string").slice(0, MAX_TOOLS)
            : [],
        );
        setActiveSkill(typeof setup.skill === "string" ? setup.skill : "");
        // Thread-local restore: the panel points at this conversation's folder
        // without touching the localStorage default (New chat returns to it).
        setWorkspaceDir(setup.workspace_dir ? setup.workspace_dir : null);
        setChoice(
          setup.provider && setup.model ? `${setup.provider}::${setup.model}` : "",
        );
        sendSetupRef.current = true;
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

  // The capped, visible matches bucketed into ordered categories for the "+"
  // menu's collapsible groups (empty categories are dropped).
  const toolGroups = useMemo(() => {
    const buckets = new Map<ToolCategory, ToolOption[]>();
    for (const t of toolMatches.slice(0, TOOL_LIST_CAP)) {
      const cat = categorizeTool(t);
      const arr = buckets.get(cat);
      if (arr) arr.push(t);
      else buckets.set(cat, [t]);
    }
    return TOOL_CATEGORY_ORDER.filter((c) => buckets.has(c)).map((c) => ({
      cat: c,
      tools: buckets.get(c)!,
    }));
  }, [toolMatches]);

  function toggleCat(cat: string) {
    setCollapsedCats((prev) => {
      const next = new Set(prev);
      if (next.has(cat)) next.delete(cat);
      else next.add(cat);
      return next;
    });
  }

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
    markSetupChanged();
  }

  function disarmTool(name: string) {
    setSelectedTools((prev) => prev.filter((n) => n !== name));
    markSetupChanged();
  }

  // WEB QUICK-TOGGLE: one click arms/disarms the web_search + web_fetch pair
  // (they ride the same `tools` mechanism as the "+" menu). Pressed only when
  // BOTH are armed; arming needs room for the pair inside the MAX_TOOLS cap.
  const webArmed = WEB_TOOLS.every((n) => selectedTools.includes(n));
  const webRoom =
    selectedTools.filter((n) => !WEB_TOOLS.includes(n)).length + WEB_TOOLS.length <=
    MAX_TOOLS;

  function toggleWeb() {
    setSelectedTools((prev) => {
      const others = prev.filter((n) => !WEB_TOOLS.includes(n));
      if (WEB_TOOLS.every((n) => prev.includes(n))) return others; // disarm both
      if (others.length + WEB_TOOLS.length > MAX_TOOLS) return prev; // no room
      return [...others, ...WEB_TOOLS];
    });
    markSetupChanged();
  }

  /** Select a skill from the "/" dropdown: chip on, "/query" text consumed. */
  function pickSkill(name: string) {
    setActiveSkill(name);
    setInput("");
    setSlashDismissed(false);
    markSetupChanged();
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

  // Keep the newest message (or the live working bubble) in view — but ONLY when
  // the reader is pinned near the bottom, so scrolling up to re-read isn't yanked
  // back down on the next token. During a live stream scroll INSTANTLY (a smooth
  // animation queued per token never settles and reads as jitter).
  useEffect(() => {
    if (!pinnedRef.current) return;
    bottomRef.current?.scrollIntoView({ behavior: busy ? "auto" : "smooth", block: "end" });
  }, [messages, awaitingId, chatBusy, progress.length, stream.text, runStream.text, busy]);

  // Track the reader's pin state; releasing the pin surfaces a "Jump to latest"
  // pill instead of fighting them for the scroll position.
  function onThreadScroll() {
    const el = scrollRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    pinnedRef.current = nearBottom;
    setShowJump((prev) => (prev === !nearBottom ? prev : !nearBottom));
  }

  function jumpToLatest() {
    pinnedRef.current = true;
    setShowJump(false);
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }

  // Auto-grow the composer to fit multi-line input (up to ~1/4 viewport), and
  // shrink back when it's cleared on send. Runs on every `input` change (incl.
  // the programmatic reset), so a Shift+Enter draft is never trapped in one row.
  useLayoutEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [input]);

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
      tts.speak(content); // no-op unless voice replies are on
      queueSave(full); // agent turns are conversations worth keeping too
      awaitingIdRef.current = null;
      setAwaitingId(null);
      inputRef.current?.focus(); // type-ready for the next turn
    } catch (e) {
      if (e instanceof ApiError && e.status === 0) {
        // Transient network blip — keep the turn alive and let the 1.5s poll
        // retry, so a reply that already completed server-side isn't dropped.
        setOffline(true);
        return;
      }
      // Hard failure: surface it and stop waiting so the turn doesn't hang forever.
      setError(e instanceof ApiError ? e.message : String(e));
      queueSave(messagesRef.current); // the typed message still survives navigation
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

  // AGENT MODE streaming: subscribe to this session's live run frames so the
  // working bubble narrates tokens + tool calls. Purely a live view — the reply
  // is still finalized from the session on `agent.completed` above.
  useEffect(() => {
    if (!awaitingId) return;
    runStream.start(awaitingId);
    return () => runStream.stop();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [awaitingId]);

  // ------------------------------------------------------------------- voice

  // Flush each newly-FINALIZED dictation chunk into the composer.
  useEffect(() => {
    if (dictation.transcript.length > dictEmittedRef.current) {
      const delta = dictation.transcript.slice(dictEmittedRef.current);
      dictEmittedRef.current = dictation.transcript.length;
      inputFromVoiceRef.current = true;
      setInput((p) => appendDictation(p, delta));
    }
  }, [dictation.transcript]);

  /** Composer mic: plain dictation into the input (works in any mode). */
  function micToggle() {
    if (!dictation.supported) return;
    if (dictation.listening) {
      dictation.stop();
      if (voiceMode) setVoiceMode(false); // the mic is the master off-switch
    } else {
      dictation.reset();
      dictEmittedRef.current = 0;
      dictation.start();
    }
  }

  /** Hands-free Voice Chat on/off. Entering turns spoken replies on (that's
   *  the point); leaving stops the mic but keeps the TTS preference. */
  function toggleVoiceMode() {
    if (voiceMode) {
      setVoiceMode(false);
      dictation.stop();
      return;
    }
    if (!dictation.supported) return;
    tts.enable();
    setVoiceMode(true); // the hold/resume effect below starts the mic
  }

  // Voice Chat mic scheduling: hold the mic while a reply is being generated
  // or spoken (so it never transcribes Iron Jarvis's own voice), listen
  // otherwise. Also (re)starts the mic on entering voice chat.
  useEffect(() => {
    if (!voiceMode) return;
    if (busy || tts.speaking) {
      dictation.stop();
    } else {
      dictation.reset();
      dictEmittedRef.current = 0;
      dictation.start();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [voiceMode, busy, tts.speaking]);

  // Voice Chat auto-send: once dictated text settles (no interim words, no
  // clip being transcribed), send it. Web Speech finalizes eagerly, so give
  // the speaker a moment to continue; the server engine already waited out
  // 1.4s of silence before finalizing, so send almost immediately.
  useEffect(() => {
    if (!voiceMode || busy || tts.speaking) return;
    if (!inputFromVoiceRef.current) return;
    const text = input.trim();
    if (!text) return;
    if (dictation.interim || dictation.processing || dictation.error) return;
    const delay = dictation.engine === "server" ? 350 : 1500;
    const timer = setTimeout(() => send(input), delay);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    voiceMode,
    input,
    busy,
    tts.speaking,
    dictation.interim,
    dictation.processing,
    dictation.error,
  ]);

  // ------------------------------------------------------------------- sending

  /** Build the /chat request body for `history` (shared by the streaming attempt
   *  and the non-streaming POST fallback so the two can never drift). */
  function buildChatBody(history: ChatMessage[], atts: UploadedFile[]): ChatRequestBody {
    const { provider, model } = splitChoice(choice);
    const personaValue = personaForSend();
    return {
      // Full conversation every turn — the backend is stateless here.
      messages: history.map(({ role, content }) => ({ role, content })),
      ...(provider ? { provider } : {}),
      ...(model ? { model } : {}),
      ...(personaValue ? { persona: personaValue } : {}),
      ...(atts.length ? { attachments: atts.map((a) => a.path) } : {}),
      // The reply's playbook + armed tool loop (both sticky across turns).
      ...(activeSkill ? { skill: activeSkill } : {}),
      ...(selectedTools.length ? { tools: selectedTools.slice(0, MAX_TOOLS) } : {}),
      // The workspace folder armed file tools operate in (when chosen).
      ...(workspaceDir ? { workspace_dir: workspaceDir } : {}),
      // Context spine: the daemon grounds the reply in this project's
      // instructions + brief + knowledge (ref — sends can fire from timers).
      ...(projectIdRef.current ? { project_id: projectIdRef.current } : {}),
    };
  }

  /** Feed streamed text into incremental TTS: reset the per-turn counter on the
   *  first call, then speak only the newly-complete sentences. No-op if muted. */
  function feedTTS(full: string, flush: boolean) {
    if (!tts.enabled) return;
    if (!ttsStreamStartedRef.current) {
      tts.resetStream();
      ttsStreamStartedRef.current = true;
    }
    tts.speakMore(full, flush);
  }

  /** Put a failed turn's typed message back in the composer — but only when
   *  it's empty (never clobber text typed while the turn was in flight). The
   *  restore is programmatic, so it must never count as voice input: Voice
   *  Chat's auto-send would otherwise re-fire the failed turn in a loop. */
  function restoreComposerDraft(history: ChatMessage[]) {
    const lastUser = [...history].reverse().find((m) => m.role === "user");
    if (!lastUser) return;
    inputFromVoiceRef.current = false;
    setInput((cur) => (cur.trim() ? cur : lastUser.content));
  }

  /**
   * CHAT MODE core: one /chat completion over `history` (which must end with a
   * user message). Shared by sendChat and regenerate. Tries token streaming
   * first (live bubble + incremental voice); on ANY streaming failure it falls
   * back to the direct /chat POST verbatim. On success the reply is appended and
   * the turn autosaved (the ONLY chat-mode save site).
   */
  async function completeChat(history: ChatMessage[], atts: UploadedFile[]) {
    const gen = chatGenRef.current;
    setMessages(history);
    pinnedRef.current = true; // a fresh turn always scrolls into view
    setShowJump(false);
    setFailedTurn(null); // a fresh attempt — retire any prior failure
    setChatBusy(true);
    ttsStreamStartedRef.current = false; // new turn — feedTTS will reset the counter
    const body = buildChatBody(history, atts);
    try {
      // --- Attempt token streaming (live deltas + tool cards + voice) ---
      try {
        const { reply, tools_used } = await stream.run(body, (_delta, full) =>
          feedTTS(full, false),
        );
        if (chatGenRef.current !== gen) return; // torn down mid-stream
        // One tick so the final tool_call frame's state flush lands before the
        // cards are read for source extraction (this resolve microtask can
        // outrun React's batched setTools render).
        await new Promise<void>((r) => window.setTimeout(r, 0));
        if (chatGenRef.current !== gen) return;
        feedTTS(reply, true); // flush any trailing fragment
        const toolsUsed = (tools_used ?? []).filter((t) => Boolean(t));
        // Sources the turn's web tools ACTUALLY returned (never prose links).
        const sources = extractWebSources(streamToolsRef.current);
        const finalReply = (reply ?? "").trim() || "(no response)";
        const full: ChatMessage[] = [
          ...history,
          {
            role: "assistant",
            content: finalReply,
            ...(toolsUsed.length ? { toolsUsed } : {}),
            ...(sources.length ? { sources } : {}),
          },
        ];
        setMessages(full);
        queueSave(full); // the turn is complete — persist it
        return; // streamed successfully
      } catch (e) {
        if (chatGenRef.current !== gen) return; // torn down — no fallback

        // A non-streaming re-POST re-runs the WHOLE turn from round 0. When the
        // stream already committed server-side work (streamed a token or ran a
        // tool), that would DOUBLE-execute the turn's tools (double credit spend,
        // duplicate writes/sends). So only fall back when the streaming endpoint
        // is genuinely ABSENT (an old daemon → 404/405) AND nothing was committed
        // — i.e. the server did zero work. Every other failure (committed work,
        // or an honest in-band provider error) is surfaced, never silently rerun;
        // the user-initiated Retry button remains the explicit way to re-send.
        const se = e instanceof StreamError ? e : null;
        const committed = se?.committed ?? false;
        const status = se?.status ?? (e instanceof ApiError ? e.status : 0);
        const endpointMissing = status === 404 || status === 405;
        if (committed || !endpointMissing) {
          // Preserve what the user already watched stream in — dropping it reads
          // like a crash. Keep it as an interrupted bubble (Retry re-runs from the
          // clean `history`, which doesn't include this partial).
          await new Promise<void>((r) => window.setTimeout(r, 0)); // tool flush
          if (chatGenRef.current !== gen) return;
          const partial = (se?.partial ?? "").trim();
          const sources = extractWebSources(streamToolsRef.current);
          const withPartial: ChatMessage[] = partial
            ? [
                ...history,
                {
                  role: "assistant",
                  content: partial,
                  interrupted: true,
                  ...(sources.length ? { sources } : {}),
                },
              ]
            : history;
          if (partial) setMessages(withPartial);
          if (se?.offline || (e instanceof ApiError && e.status === 0 && !se))
            setOffline(true);
          else setError(e instanceof ApiError ? e.message : String(e));
          setFailedTurn({ history, atts });
          // The failed turn must survive navigation: persist the typed message
          // (+ the interrupted partial) through the same autosave path a
          // completed turn uses, and put the message back in the composer.
          queueSave(withPartial);
          restoreComposerDraft(history);
          return;
        }
        // else: /chat/stream is absent on a reachable daemon → safe to fall back.
      }

      // --- Fallback: the direct /chat POST (only when /chat/stream is absent) ---
      const res = await post<ChatResponse>("/chat", body);
      if (chatGenRef.current !== gen) return; // "New chat" happened mid-flight
      const toolsUsed = (res.tools_used ?? []).filter((t) => Boolean(t));
      const reply = (res.reply ?? "").trim() || "(no response)";
      const full: ChatMessage[] = [
        ...history,
        {
          role: "assistant",
          content: reply,
          ...(toolsUsed.length ? { toolsUsed } : {}),
        },
      ];
      setMessages(full);
      // Nothing streamed on this path (endpoint absent), so this is the first and
      // only speak — no risk of re-voicing sentences speakMore already spoke.
      if (!ttsStreamStartedRef.current) tts.speak(reply);
      queueSave(full); // the turn is complete — persist it
    } catch (e) {
      if (chatGenRef.current !== gen) return;
      // Keep the typed thread intact — only surface the failure (a 502 carries
      // the provider's own message, e.g. a rate limit, in `detail`). Remember
      // the exact turn (history + attachments) so Retry can re-send it — the
      // attachments are NOT consumed on failure.
      if (e instanceof ApiError && e.status === 0) setOffline(true);
      else setError(e instanceof ApiError ? e.message : String(e));
      setFailedTurn({ history, atts });
      // Persist the typed message so navigating away doesn't lose it, and
      // restore it to the composer for an immediate edit/resend.
      queueSave(history);
      restoreComposerDraft(history);
    } finally {
      sendingRef.current = false;
      if (chatGenRef.current === gen) {
        setChatBusy(false);
        // Return focus so the next message is type-ready without a click.
        inputRef.current?.focus();
      }
    }
  }

  /** CHAT MODE: append the user's message and run one completion. */
  async function sendChat(message: string) {
    const atts = attachments;
    setAttachments([]); // chips are consumed by this message
    const userMsg: ChatMessage = {
      role: "user",
      content: message,
      ...(atts.length
        ? {
            attachmentNames: atts.map((a) => a.name),
            attachmentPaths: atts.map((a) => a.path),
          }
        : {}),
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
    const lastUser = history[history.length - 1];
    if (history.length === 0 || lastUser.role !== "user") return;
    setError(null);
    setOffline(false);
    // Re-ground on the SAME attachments the turn carried — otherwise the re-run
    // answers blind while the user bubble still shows the file chip.
    const atts: UploadedFile[] = (lastUser.attachmentPaths ?? []).map((path, i) => ({
      path,
      name: lastUser.attachmentNames?.[i] ?? path.split(/[\\/]/).pop() ?? path,
      bytes: 0,
    }));
    void completeChat(history, atts);
  }

  /** Re-send the last failed chat turn — same history + attachments, verbatim. */
  function retryTurn() {
    if (!failedTurn || busy) return;
    const { history, atts } = failedTurn;
    setError(null);
    setOffline(false);
    // Retire the auto-restored composer draft (it duplicates the message Retry
    // is about to re-send); anything the user typed themselves is kept.
    const lastUser = [...history].reverse().find((m) => m.role === "user");
    if (lastUser)
      setInput((cur) => (cur.trim() === lastUser.content.trim() ? "" : cur));
    void completeChat(history, atts);
  }

  /** AGENT MODE: the original session flow (wait:false + live steps + finalize). */
  async function sendAgent(message: string) {
    const atts = attachments;
    setAttachments([]); // chips are consumed by this message
    // A recap of the chat so far — prepended ONLY when opening a fresh session
    // below (switching to Agent mode drops all context otherwise). Captured
    // before the new user bubble is appended.
    const recap = conversationRecap(messages);
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
        // First message opens a session — carry the chat recap into the task so
        // the agent inherits the conversation instead of starting cold.
        const { provider, model } = splitChoice(choice);
        const openingTask = recap ? `${recap}\n\n---\n\n${task}` : task;
        session = await post<SessionView>("/sessions", {
          task: openingTask,
          agent_type: "builder",
          wait: false,
          ...(provider ? { provider } : {}),
          ...(model ? { model } : {}),
          // Context spine: the run lands in the selected project (continues
          // inherit it server-side, so only the opener needs the tag).
          ...(projectIdRef.current ? { project_id: projectIdRef.current } : {}),
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
      // Keep the typed thread intact and RESTORE the optimistically-cleared
      // attachments so a failed send never silently eats the user's files.
      if (atts.length) setAttachments(atts);
      if (e instanceof ApiError && e.status === 0) setOffline(true);
      else setError(e instanceof ApiError ? e.message : String(e));
      // Match the chat-mode failure path: the typed message survives navigation
      // (messagesRef already holds the appended user bubble by now) and returns
      // to the composer for an immediate edit/resend.
      queueSave(messagesRef.current);
      restoreComposerDraft(messagesRef.current);
    } finally {
      sendingRef.current = false;
    }
  }

  function send(text: string) {
    const message = text.trim();
    // `busy` is React state (lags a frame); `sendingRef` flips synchronously so
    // two Enter keydowns in the same tick can't both start a turn.
    if (!message || busy || sendingRef.current) return;
    sendingRef.current = true;
    // A new turn always follows: re-pin so the user's own message + the reply
    // scroll into view even if they'd scrolled up to re-read earlier context.
    pinnedRef.current = true;
    setShowJump(false);
    setError(null);
    setOffline(false);
    setInput("");
    if (mode === "chat") void sendChat(message);
    else void sendAgent(message);
  }

  // Stop the in-flight turn and keep whatever streamed so far as the answer.
  // Best-effort — even if the server-side cancel fails we stop waiting locally.
  function stop() {
    // CHAT: abort the stream. Bump the generation FIRST so the aborted
    // stream.run()'s throw lands in a torn-down completeChat (no POST fallback).
    if (chatBusy && stream.streaming) {
      chatGenRef.current += 1;
      stream.abort();
      tts.cancel(); // stop reading a reply the user just cut off
      const partial = stream.text.trim();
      const sources = extractWebSources(stream.tools);
      const full: ChatMessage[] = [
        ...messagesRef.current,
        {
          role: "assistant",
          content: partial || "Stopped.",
          ...(partial ? { interrupted: true } : {}),
          ...(partial && sources.length ? { sources } : {}),
        },
      ];
      setMessages(full);
      queueSave(full); // the (aborted) turn still completed a visible exchange
      setChatBusy(false);
      sendingRef.current = false;
      return;
    }
    // AGENT: ask the daemon to cancel the session.
    if (!awaitingId) return;
    tts.cancel();
    post(`/sessions/${awaitingId}/cancel`).catch(() => {});
    const partial = runStream.text.trim();
    const full: ChatMessage[] = [
      ...messagesRef.current,
      {
        role: "assistant",
        content: partial || "Stopped.",
        ...(partial ? { interrupted: true } : {}),
      },
    ];
    setMessages(full);
    queueSave(full); // the (aborted) turn still completed a visible exchange
    awaitingIdRef.current = null;
    setAwaitingId(null); // also tears down the event watcher + polling interval
  }

  function newChat() {
    chatGenRef.current += 1; // orphan any in-flight /chat reply
    stream.abort(); // tear down a live streaming turn (its throw won't fall back)
    tts.cancel(); // stop reading the old thread's reply
    setMessages([]);
    setSessionId(null);
    awaitingIdRef.current = null;
    setAwaitingId(null); // also tears down any polling interval
    setChatBusy(false);
    setFailedTurn(null);
    setAttachments([]);
    // A selected project keeps its file essentials armed on a fresh
    // conversation (its folder stays live); otherwise nothing is armed.
    const proj = projectIdRef.current
      ? projects.find((p) => p.id === projectIdRef.current)
      : undefined;
    const projFolderLive = Boolean(proj?.root) && proj?.root_exists !== false;
    if (projFolderLive) {
      setSelectedTools(PROJECT_FILE_TOOLS);
      autoArmedRef.current = true;
    } else {
      setSelectedTools([]);
    }
    sendSetupRef.current = false; // fresh conversation — nothing armed to persist
    setToolsOpen(false);
    setToolQuery("");
    setActiveSkill("");
    setSlashDismissed(false);
    setInput("");
    setError(null);
    setOffline(false);
    setThreadId(null);
    // Back to the defaults — the project folder while a project is selected,
    // else the user's own saved workspace/persona choices.
    try {
      setWorkspaceDir(
        projFolderLive
          ? (proj?.root as string)
          : window.localStorage.getItem(WORKSPACE_KEY) || null,
      );
      const savedPersona = window.localStorage.getItem(PERSONA_KEY);
      if (savedPersona) selectPersonaLocal(savedPersona);
    } catch {
      /* keep the current values */
    }
    saveTargetRef.current = { id: null }; // next completed turn creates a fresh thread
    sinceRef.current = null;
    finalizingRef.current = false;
    sendingRef.current = false;
    pinnedRef.current = true; // fresh pane follows new messages; retire any jump pill
    setShowJump(false);
    inputRef.current?.focus();
  }

  function prefill(text: string) {
    setInput(text);
    inputRef.current?.focus();
  }

  /** Switch Chat↔Agent. Clears the bound `sessionId` on a real change so a later
   *  Agent turn opens a FRESH session (recapping the visible thread) instead of
   *  silently continuing a stale one that never saw the intervening chat turns.
   *  An in-flight agent turn (tracked by awaitingIdRef) still finalizes. */
  function switchMode(next: Mode) {
    if (next === mode) return;
    setSessionId(null);
    setMode(next);
    inputRef.current?.focus();
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    // Ignore keystrokes mid-IME-composition (CJK / accented input): Enter is
    // confirming a candidate, not sending a half-finished message.
    if (e.nativeEvent.isComposing) return;
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
    // Escape cancels an in-flight turn (keyboard "Stop") without leaving the composer.
    if (e.key === "Escape" && busy) {
      e.preventDefault();
      stop();
      return;
    }
    // Enter sends; Shift+Enter inserts a newline.
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send(input);
    }
  }

  const started = messages.length > 0 || sessionId !== null || threadId !== null;
  const shareTitle =
    threads.find((t) => t.id === threadId)?.title?.trim() || "Chat";
  const personaNames = personas.map((p) => p.name);
  const curPersona = personas.find((p) => p.name === persona);
  const selectedPersonaDesc = curPersona?.description ?? "";
  // Show a Revert/Delete action for custom personas and overridden built-ins
  // (a pristine built-in has nothing to revert).
  const showRevertDelete =
    !isNewPersona &&
    !!curPersona &&
    (!curPersona.builtin || curPersona.overridden);

  return (
    <PageShell>
      <Reveal>
        <PageHeader
          title="Chat"
          subtitle="Talk to Iron Jarvis. Chat mode answers directly in seconds; Agent mode does real work with tools."
          actions={
            <div className="flex flex-wrap items-center gap-2">
              {/* Share the open thread — full transcript or compacted digest. */}
              <button
                type="button"
                onClick={() => setShareOpen(true)}
                disabled={!threadId}
                title={
                  threadId
                    ? "Share this chat — full transcript or a compacted digest"
                    : "A chat can be shared after its first reply (it saves automatically)"
                }
                className="btn-ghost px-2.5 py-1.5 text-[13px] disabled:cursor-not-allowed disabled:opacity-50"
              >
                <Share2 size={14} /> Share
              </button>
              {/* Voice: hands-free Voice Chat + spoken-replies toggle. */}
              <button
                type="button"
                onClick={toggleVoiceMode}
                disabled={!dictation.supported}
                aria-pressed={voiceMode}
                title={
                  voiceMode
                    ? "End voice chat"
                    : dictation.supported
                      ? "Voice chat — speak, hear replies, hands-free"
                      : dictation.reason || "Voice isn't available here yet"
                }
                className={`inline-flex items-center gap-1.5 rounded-xl border px-3 py-1.5 text-[13px] font-medium transition-all disabled:cursor-not-allowed disabled:opacity-50 ${
                  voiceMode
                    ? "border-rose-500/50 bg-rose-500/15 text-rose-300 shadow-[0_0_18px_-4px_rgba(244,63,94,0.7)]"
                    : "border-white/10 bg-white/[0.02] text-zinc-400 hover:border-accent/50 hover:text-accent-soft"
                }`}
              >
                <AudioLines size={14} /> {voiceMode ? "Voice on" : "Voice"}
              </button>
              {tts.supported && (
                <button
                  type="button"
                  onClick={tts.toggle}
                  aria-pressed={tts.enabled}
                  title={
                    tts.enabled
                      ? "Spoken replies on — click to mute"
                      : "Read replies aloud"
                  }
                  className={`btn-ghost px-2.5 py-1.5 text-[13px] ${
                    tts.enabled ? "text-accent-soft" : ""
                  }`}
                >
                  {tts.enabled ? <Volume2 size={14} /> : <VolumeX size={14} />}
                </button>
              )}
              {/* Mode toggle: fast direct chat vs. the tool-using agent session. */}
              <div
                role="group"
                aria-label="Mode"
                className="flex items-center overflow-hidden rounded-xl border border-white/10 bg-white/[0.02]"
              >
                <button
                  type="button"
                  onClick={() => switchMode("chat")}
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
                  onClick={() => switchMode("agent")}
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
                <div className="flex items-center gap-1">
                  <select
                    aria-label="Persona"
                    value={persona}
                    onChange={(e) => {
                      const v = e.target.value;
                      setPersonaEditorOpen(false);
                      if (v === NEW_PERSONA) startNewPersona();
                      else choosePersona(v);
                    }}
                    disabled={busy}
                    title={
                      persona === NEW_PERSONA
                        ? "Create a new persona"
                        : selectedPersonaDesc || "Persona for replies"
                    }
                    className="field w-auto py-1.5 text-[13px]"
                  >
                    {/* Tolerate a saved persona the daemon no longer lists. */}
                    {!personaNames.includes(persona) && persona !== NEW_PERSONA && (
                      <option value={persona}>{personaTitle(persona)}</option>
                    )}
                    {personas.map((p) => (
                      <option key={p.name} value={p.name} title={p.description}>
                        {p.title || capitalize(p.name)}
                        {p.overridden ? " ·" : ""}
                      </option>
                    ))}
                    <option value={NEW_PERSONA}>+ New persona…</option>
                  </select>
                  <button
                    type="button"
                    onClick={() =>
                      personaEditorOpen ? closePersonaEditor() : openPersonaEditor()
                    }
                    disabled={busy || persona === NEW_PERSONA}
                    aria-pressed={personaEditorOpen}
                    title="Modify this persona"
                    aria-label="Modify persona"
                    className={`btn-ghost px-2.5 py-1.5 text-[13px] ${
                      personaEditorOpen ? "text-accent-soft" : ""
                    }`}
                  >
                    <Pencil size={14} />
                  </button>
                </div>
              )}
              <select
                aria-label="Model"
                value={choice}
                onChange={(e) => {
                  setChoice(e.target.value);
                  markSetupChanged();
                }}
                disabled={mode === "agent" ? awaiting || sessionId !== null : busy}
                title={
                  mode === "agent" && sessionId !== null
                    ? "Start a new chat to switch models"
                    : "Model for this chat"
                }
                className="field w-auto py-1.5 text-[13px]"
              >
                <option value="">default model</option>
                {/* Tolerate a thread-restored model the catalog no longer lists. */}
                {choice !== "" &&
                  !models.some((m) => `${m.provider}::${m.model}` === choice) && (
                    <option value={choice}>{choice.replace("::", " · ")}</option>
                  )}
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
                type="button"
                onClick={() => setWorkspaceOpenPersisted(!workspaceOpen)}
                aria-pressed={workspaceOpen}
                title={
                  activeProject
                    ? `Project: ${activeProject.name} — replies ground in its knowledge; the panel holds its folder + files`
                    : workspaceOpen
                      ? "Hide the project panel"
                      : "Pick a project (or just a folder) — armed file tools run there"
                }
                className={`btn-ghost py-1.5 text-[13px] ${
                  workspaceOpen || workspaceDir || activeProject ? "text-accent-soft" : ""
                }`}
              >
                {activeProject ? <FolderKanban size={14} /> : <PanelRight size={14} />}{" "}
                <span className="max-w-[9rem] truncate">
                  {activeProject ? activeProject.name : "Project"}
                </span>
              </button>
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

      {mode === "chat" && personaEditorOpen && (
        <Reveal>
          <div className="rounded-2xl border border-accent/20 bg-accent/[0.03] p-4">
            <div className="mb-3 flex items-center justify-between gap-2">
              <div className="flex min-w-0 items-center gap-2 text-[13px] font-medium text-zinc-200">
                <Pencil size={13} className="shrink-0 text-accent-soft" />
                <span className="truncate">
                  {isNewPersona ? "New persona" : `Editing ${personaTitle(persona)}`}
                </span>
                {!isNewPersona && curPersona?.builtin && (
                  <span className="shrink-0 rounded-full border border-white/10 px-2 py-0.5 text-[10px] text-zinc-400">
                    {curPersona.overridden ? "customized built-in" : "built-in"}
                  </span>
                )}
              </div>
              <button
                type="button"
                onClick={closePersonaEditor}
                aria-label="Close persona editor"
                title="Close without saving"
                className="grid h-6 w-6 shrink-0 place-items-center rounded-md text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-200"
              >
                <X size={15} />
              </button>
            </div>
            <div className="grid gap-3">
              <div>
                <label className="mb-1 block text-[10px] uppercase tracking-[0.12em] text-zinc-400">
                  Title
                </label>
                <input
                  value={draftTitle}
                  onChange={(e) => {
                    setDraftTitle(e.target.value);
                    setPersonaSaved(false);
                  }}
                  placeholder="e.g. Tax Accountant"
                  aria-label="Persona title"
                  className="field w-full py-1.5 text-[13px]"
                />
              </div>
              <div>
                <label className="mb-1 block text-[10px] uppercase tracking-[0.12em] text-zinc-400">
                  Description <span className="text-zinc-600">(optional)</span>
                </label>
                <input
                  value={draftDescription}
                  onChange={(e) => {
                    setDraftDescription(e.target.value);
                    setPersonaSaved(false);
                  }}
                  placeholder="A short line shown in the picker tooltip"
                  aria-label="Persona description"
                  className="field w-full py-1.5 text-[13px]"
                />
              </div>
              <div>
                <label className="mb-1 block text-[10px] uppercase tracking-[0.12em] text-zinc-400">
                  Prompt
                </label>
                <textarea
                  value={draftPrompt}
                  onChange={(e) => {
                    setDraftPrompt(e.target.value);
                    setPersonaSaved(false);
                  }}
                  rows={5}
                  aria-label="Persona prompt"
                  placeholder="You are a sharp tax accountant. Be concise and cite the code section."
                  className="field w-full resize-y text-[13px]"
                />
              </div>
            </div>
            {personaError && (
              <div className="mt-3">
                <ErrorNote>{personaError}</ErrorNote>
              </div>
            )}
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={savePersona}
                disabled={personaSaving || !draftPrompt.trim()}
                className="btn-accent py-1.5 text-[13px]"
              >
                {personaSaving ? (
                  <LoaderInline />
                ) : (
                  <>
                    <Save size={14} /> Save
                  </>
                )}
              </button>
              {personaSaved && (
                <span className="inline-flex items-center gap-1 text-[12px] text-emerald-400">
                  <Check size={13} /> Saved
                </span>
              )}
              {showRevertDelete && (
                <button
                  type="button"
                  onClick={deletePersona}
                  disabled={personaSaving}
                  title={
                    curPersona?.builtin
                      ? "Discard your changes to this built-in persona"
                      : "Delete this custom persona"
                  }
                  className="btn-ghost ml-auto py-1.5 text-[13px] text-rose-300 hover:text-rose-200"
                >
                  {curPersona?.builtin ? (
                    <>
                      <RotateCcw size={14} /> Revert to default
                    </>
                  ) : (
                    <>
                      <Trash2 size={14} /> Delete
                    </>
                  )}
                </button>
              )}
            </div>
            <p className="mt-2 text-[11px] text-zinc-500">
              Unsaved prompt edits still apply to your next message — but Save to keep
              this persona for next time.
            </p>
          </div>
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
              <div className="border-b hairline px-3 py-2">
                <div className="flex items-center justify-between">
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
                {threads.length > 0 && (
                  <div className="relative mt-2">
                    <Search
                      size={12}
                      className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-zinc-500"
                    />
                    <input
                      value={threadQuery}
                      onChange={(e) => setThreadQuery(e.target.value)}
                      placeholder="Search chats…"
                      aria-label="Search chats"
                      className="field w-full py-1.5 pl-8 text-[12px]"
                    />
                  </div>
                )}
              </div>
              <div className="max-h-[70vh] overflow-y-auto p-1.5">
                {threadsLoading && threads.length === 0 ? (
                  <div className="space-y-1 p-1">
                    {[0, 1, 2, 3].map((i) => (
                      <div key={i} className="skeleton h-9 w-full" />
                    ))}
                  </div>
                ) : threads.length === 0 ? (
                  <p className="px-2.5 py-3 text-xs leading-relaxed text-zinc-500">
                    No saved chats yet — conversations appear here after the first
                    reply.
                  </p>
                ) : visibleThreads.length === 0 ? (
                  <p className="px-2.5 py-3 text-xs leading-relaxed text-zinc-500">
                    No chats match “{threadQuery.trim()}”.
                  </p>
                ) : (
                  <div className="space-y-0.5">
                    {visibleThreads.map((t) => {
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
                              className={`flex items-center gap-1.5 text-[13px] ${
                                active ? "text-accent-soft" : "text-zinc-200"
                              }`}
                            >
                              <span className="min-w-0 truncate">
                                {t.title || "Untitled chat"}
                              </span>
                              {/* In the unscoped view, project threads carry a
                                  small origin chip (scoped lists don't need it). */}
                              {!projectId && t.project_id && (
                                <span
                                  className="ml-auto max-w-[5.5rem] shrink-0 truncate rounded-full border border-white/10 bg-white/[0.04] px-1.5 text-[9px] uppercase tracking-wide text-zinc-500"
                                  title="Project thread"
                                >
                                  {projects.find((p) => p.id === t.project_id)?.name ??
                                    "project"}
                                </span>
                              )}
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
              <div
                ref={scrollRef}
                onScroll={onThreadScroll}
                className="flex max-h-[60vh] min-h-[24rem] flex-col gap-4 overflow-y-auto p-4 sm:p-5"
              >
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
                            <MemoMarkdown content={m.content} />
                          </Bubble>
                          {m.interrupted && (
                            <div className="ml-11 mt-1 text-[11px] italic text-amber-400/80">
                              interrupted — the reply was cut off
                            </div>
                          )}
                          {/* Tools the reply's tool loop actually ran */}
                          {m.toolsUsed && m.toolsUsed.length > 0 && (
                            <div className="ml-11 mt-1 flex min-w-0 items-center gap-1.5 text-[11px] text-zinc-500">
                              <Wrench size={10} className="shrink-0 text-accent-soft/70" />
                              <span className="truncate">
                                used: {m.toolsUsed.join(", ")}
                              </span>
                            </div>
                          )}
                          {/* URLs the turn's web tools actually returned */}
                          {m.sources && m.sources.length > 0 && (
                            <SourcesRow sources={m.sources} />
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
                    {/* CHAT MODE: the live streaming bubble. Streamed markdown +
                        a blinking caret once the first token lands (a Thinking
                        shimmer until then), with any live tool calls below. */}
                    {chatBusy && (
                      <div aria-live="polite" aria-busy="true">
                        <Bubble role="assistant">
                          {stream.text ? (
                            <StreamingText content={stream.text} />
                          ) : (
                            <span className="inline-flex items-center gap-2 text-zinc-400">
                              <Loader2
                                size={14}
                                className="animate-spin text-accent-soft"
                              />
                              <span className="animate-pulse">Thinking…</span>
                            </span>
                          )}
                          {stream.tools.length > 0 && (
                            <ToolCardList cards={stream.tools} />
                          )}
                        </Bubble>
                      </div>
                    )}
                    {/* AGENT MODE: the live working bubble. Narrates the current
                        step, streams the agent's tokens + tool calls as they
                        arrive, and keeps the step feed underneath. */}
                    {awaiting && (
                      <Bubble role="assistant">
                        <div className="flex flex-col gap-1.5" aria-live="polite" aria-busy="true">
                          <span className="inline-flex items-center gap-2 text-zinc-300">
                            <Loader2 size={14} className="animate-spin text-accent-soft" />
                            {progress[0] ?? "Thinking…"}
                          </span>
                          {runStream.text && <StreamingText content={runStream.text} />}
                          {runStream.tools.length > 0 && (
                            <ToolCardList cards={runStream.tools} />
                          )}
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
                {showJump && (
                  <button
                    type="button"
                    onClick={jumpToLatest}
                    className="sticky bottom-1 z-10 mx-auto flex items-center gap-1.5 rounded-full border border-accent/40 bg-ink-850/90 px-3 py-1 text-[12px] font-medium text-accent-soft shadow-glow-sm backdrop-blur transition-colors hover:bg-ink-800"
                    title="Scroll to the latest message"
                  >
                    <ChevronDown size={13} /> Jump to latest
                  </button>
                )}
                <div ref={bottomRef} />
              </div>

              {(error || (failedTurn && !busy)) && (
                <div className="flex flex-wrap items-center gap-2 border-t hairline p-3">
                  {error && (
                    <div className="min-w-0 flex-1">
                      <ErrorNote>{error}</ErrorNote>
                    </div>
                  )}
                  {failedTurn && !busy && (
                    <button
                      type="button"
                      onClick={retryTurn}
                      title="Re-send the last message"
                      className="btn-ghost shrink-0 py-1.5 text-[13px]"
                    >
                      <RefreshCw size={14} /> Retry
                    </button>
                  )}
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
                        onClick={() => {
                          setActiveSkill("");
                          markSetupChanged();
                        }}
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

              {/* Voice status strip — live mic/speech feedback for both the
                  composer mic and hands-free Voice Chat. */}
              {(voiceMode ||
                dictation.listening ||
                dictation.processing ||
                dictation.error) && (
                <div className="flex items-center gap-2 border-t hairline px-3 py-2 text-xs">
                  <span
                    className={`h-2 w-2 shrink-0 rounded-full ${
                      dictation.listening
                        ? "animate-pulse bg-rose-400 shadow-[0_0_8px_2px_rgba(244,63,94,0.5)]"
                        : "bg-zinc-600"
                    }`}
                  />
                  {dictation.error ? (
                    <span className="truncate text-rose-300">{dictation.error}</span>
                  ) : tts.speaking ? (
                    <span className="text-accent-soft/80">
                      speaking — mic resumes when done
                    </span>
                  ) : dictation.processing ? (
                    <span className="text-accent-soft/80">transcribing…</span>
                  ) : dictation.interim ? (
                    <span className="truncate italic text-zinc-400">
                      {dictation.interim}
                    </span>
                  ) : dictation.listening ? (
                    <span className="text-zinc-400">
                      listening…{voiceMode ? " pause to send" : ""}
                    </span>
                  ) : busy ? (
                    <span className="text-zinc-500">thinking…</span>
                  ) : (
                    <span className="text-zinc-500">voice chat on</span>
                  )}
                  {voiceMode && (
                    <button
                      type="button"
                      onClick={toggleVoiceMode}
                      className="ml-auto shrink-0 text-zinc-500 transition-colors hover:text-zinc-300"
                    >
                      end voice chat
                    </button>
                  )}
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
                          <p
                            className={`mt-1.5 px-0.5 text-[10px] ${
                              selectedTools.length >= MAX_TOOLS
                                ? "text-accent-soft"
                                : "text-zinc-500"
                            }`}
                          >
                            {selectedTools.length >= MAX_TOOLS
                              ? `Maximum ${MAX_TOOLS} tools armed — disarm one to add another`
                              : `Arm up to ${MAX_TOOLS} tools · ${selectedTools.length} armed`}
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
                              {toolGroups.map(({ cat, tools }) => {
                                const collapsed = collapsedCats.has(cat);
                                const armedInCat = tools.filter((t) =>
                                  selectedTools.includes(t.name),
                                ).length;
                                return (
                                  <div key={cat} className="mb-0.5">
                                    <button
                                      type="button"
                                      onClick={() => toggleCat(cat)}
                                      aria-expanded={!collapsed}
                                      className="flex w-full items-center gap-1.5 rounded-md px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-zinc-500 transition-colors hover:text-zinc-300"
                                    >
                                      <ChevronRight
                                        size={11}
                                        className={`shrink-0 transition-transform ${
                                          collapsed ? "" : "rotate-90"
                                        }`}
                                      />
                                      {TOOL_CATEGORY_LABEL[cat]}
                                      <span className="font-normal normal-case tracking-normal text-zinc-600">
                                        {tools.length}
                                        {armedInCat ? ` · ${armedInCat} armed` : ""}
                                      </span>
                                    </button>
                                    {!collapsed &&
                                      tools.map((t) => {
                                        const checked = selectedTools.includes(
                                          t.name,
                                        );
                                        const atCap =
                                          !checked &&
                                          selectedTools.length >= MAX_TOOLS;
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
                                  </div>
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
                {/* Web quick-toggle — one click arms web_search + web_fetch
                    (the same registry-tools mechanism as the "+" menu). */}
                {mode === "chat" && (
                  <button
                    type="button"
                    onClick={toggleWeb}
                    disabled={!webArmed && !webRoom}
                    aria-pressed={webArmed}
                    title={
                      webArmed
                        ? "Web research armed (web_search + web_fetch) — click to disarm"
                        : webRoom
                          ? "Arm web research — search the web and read pages in this chat"
                          : `All ${MAX_TOOLS} tool slots are armed — disarm one to add Web`
                    }
                    className={`btn-ghost h-[2.75rem] px-3 py-0 text-[13px] ${
                      webArmed ? "text-accent-soft" : ""
                    } disabled:cursor-not-allowed disabled:opacity-50`}
                  >
                    <Globe size={15} /> Web
                  </button>
                )}
                {/* Mic — dictate into the composer (daemon-transcribed in the
                    desktop app, Web Speech in a browser). */}
                <button
                  type="button"
                  onClick={micToggle}
                  disabled={!dictation.supported}
                  aria-pressed={dictation.listening}
                  aria-label={
                    dictation.listening ? "Stop dictation" : "Start dictation"
                  }
                  title={
                    dictation.supported
                      ? dictation.listening
                        ? "Stop dictation"
                        : "Dictate your message"
                      : dictation.reason || "Voice input isn't available here yet"
                  }
                  className={`relative h-[2.75rem] shrink-0 px-3 py-0 ${
                    dictation.listening ? "btn-ghost text-rose-300" : "btn-ghost"
                  } disabled:cursor-not-allowed disabled:opacity-50`}
                >
                  {dictation.listening && (
                    <span className="pointer-events-none absolute -right-0.5 -top-0.5 h-2 w-2 animate-pulse rounded-full bg-rose-400 shadow-[0_0_8px_2px_rgba(244,63,94,0.6)]" />
                  )}
                  {dictation.supported ? <Mic size={15} /> : <MicOff size={15} />}
                </button>
                <textarea
                  ref={inputRef}
                  value={input}
                  onChange={(e) => {
                    inputFromVoiceRef.current = false; // typed — never auto-send
                    setInput(e.target.value);
                    setSlashDismissed(false); // editing reopens the "/" dropdown
                  }}
                  onKeyDown={onKeyDown}
                  autoFocus
                  rows={1}
                  aria-label="Message"
                  placeholder="Message Iron Jarvis…  (Enter to send · Shift+Enter for a new line)"
                  className="field max-h-40 min-h-[2.75rem] flex-1 resize-none"
                />
                {(awaiting || (chatBusy && stream.streaming)) && (
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

          {/* Project panel (right): the context spine. Pick a project to scope
              threads, ground replies in its knowledge, and aim the workspace at
              its folder — or just browse to any folder for an ad-hoc workspace.
              The chosen folder rides along as workspace_dir so the chat's
              armed file tools write here and their output surfaces live below. */}
          {workspaceOpen ? (
            <aside className="w-full shrink-0 md:w-80">
              <div className="flex h-[26rem] flex-col gap-2 md:h-[60vh]">
                <div className="shrink-0 rounded-xl border border-white/[0.06] bg-ink-850/60 px-3 py-2">
                  <div className="flex items-center gap-2">
                    <FolderKanban size={13} className="shrink-0 text-accent-soft/80" />
                    <span className="text-[11px] font-medium uppercase tracking-wide text-zinc-500">
                      Project
                    </span>
                    {activeProject && (
                      <Link
                        href={`/projects/${encodeURIComponent(activeProject.id)}`}
                        title="Open the project hub — tasks, board, media, knowledge"
                        className="ml-auto inline-flex shrink-0 items-center gap-1 rounded-md px-1.5 py-1 text-[11px] text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-accent-soft"
                      >
                        Hub <ExternalLink size={11} />
                      </Link>
                    )}
                  </div>
                  <select
                    aria-label="Project"
                    value={projectId ?? ""}
                    onChange={(e) => chooseProject(e.target.value)}
                    className="field mt-2 w-full py-1.5 text-[12px]"
                  >
                    <option value="">No project — plain chat</option>
                    {/* Tolerate an open thread's project the list doesn't know. */}
                    {projectId && !projects.some((p) => p.id === projectId) && (
                      <option value={projectId}>(unknown project)</option>
                    )}
                    {projects.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                      </option>
                    ))}
                  </select>
                  {activeProject &&
                    (activeProject.root_exists === false ? (
                      <p className="mt-1.5 text-[10px] leading-relaxed text-amber-300/90">
                        The project folder is missing — file tools stay off until
                        it&apos;s back (fix it in the hub).
                      </p>
                    ) : (
                      <p className="mt-1.5 text-[10px] leading-relaxed text-zinc-600">
                        Replies ground in this project&apos;s instructions +
                        knowledge; chats and runs stay tagged to it.
                      </p>
                    ))}
                </div>
                <div className="min-h-0 flex-1">
                {workspaceDir && !pickingFolder ? (
                  <div className="flex h-full flex-col gap-2">
                    <div className="flex shrink-0 items-center gap-2 rounded-xl border border-white/[0.06] bg-ink-850/60 px-3 py-2">
                      <FolderOpen size={13} className="shrink-0 text-accent-soft/80" />
                      <span className="text-[11px] font-medium uppercase tracking-wide text-zinc-500">
                        Workspace
                      </span>
                      <div className="ml-auto flex shrink-0 items-center gap-1">
                        {!projectId && (
                          <button
                            type="button"
                            onClick={() => void promoteFolderToProject()}
                            disabled={promoting}
                            title="Turn this folder into a project — chats here get tagged, grounded, and gathered in one place"
                            aria-label="Make this folder a project"
                            className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-[11px] text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-accent-soft disabled:opacity-50"
                          >
                            {promoting ? (
                              <Loader2 size={13} className="animate-spin" />
                            ) : (
                              <FolderKanban size={13} />
                            )}{" "}
                            Make project
                          </button>
                        )}
                        <button
                          type="button"
                          onClick={() => setPickingFolder(true)}
                          title="Change folder"
                          aria-label="Change workspace folder"
                          className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-[11px] text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-accent-soft"
                        >
                          <FolderPen size={13} /> Change
                        </button>
                        <button
                          type="button"
                          onClick={() => setWorkspaceOpenPersisted(false)}
                          title="Collapse workspace"
                          aria-label="Collapse workspace"
                          className="grid h-6 w-6 place-items-center rounded-md text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-200"
                        >
                          <PanelRightClose size={14} />
                        </button>
                      </div>
                    </div>
                    <p className="shrink-0 px-1 text-[10px] text-zinc-600">
                      Files the chat&apos;s armed tools create land here.
                    </p>
                    <div className="min-h-0 flex-1">
                      <FilesPanel folder={workspaceDir} />
                    </div>
                  </div>
                ) : (
                  <DirectoryTree
                    selectedPath={workspaceDir}
                    onSelect={chooseWorkspace}
                    onOpenTerminal={() => {}}
                    onCollapse={() => {
                      // While changing an existing folder, the tree's collapse
                      // acts as "cancel → back to files"; otherwise it hides the
                      // whole project panel.
                      if (pickingFolder && workspaceDir) setPickingFolder(false);
                      else setWorkspaceOpenPersisted(false);
                    }}
                  />
                )}
                </div>
              </div>
            </aside>
          ) : (
            <button
              type="button"
              onClick={() => setWorkspaceOpenPersisted(true)}
              title={
                activeProject
                  ? `Show the project panel (${activeProject.name})`
                  : "Show the project panel"
              }
              aria-label="Show project panel"
              className="hidden shrink-0 self-stretch md:flex"
            >
              <span
                className={`flex h-full flex-col items-center gap-2 rounded-2xl border border-white/[0.06] bg-ink-850/60 px-2 py-3 transition-colors hover:text-accent-soft ${
                  activeProject ? "text-accent-soft/80" : "text-zinc-500"
                }`}
              >
                <PanelRightOpen size={16} />
                <span className="text-[10px] uppercase tracking-wide [writing-mode:vertical-rl]">
                  {activeProject ? activeProject.name.slice(0, 18) : "Project"}
                </span>
              </span>
            </button>
          )}
        </div>
      </Reveal>

      {shareOpen && threadId && (
        <ShareChatDialog
          threadId={threadId}
          title={shareTitle}
          onClose={() => setShareOpen(false)}
        />
      )}
    </PageShell>
  );
}
