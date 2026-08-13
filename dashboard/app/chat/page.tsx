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
  type CSSProperties,
  type ReactElement,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, motion } from "framer-motion";
import Link from "next/link";
import {
  AudioLines,
  BookmarkPlus,
  Bot,
  Brain,
  Check,
  ChevronDown,
  ChevronRight,
  Copy,
  Download,
  ExternalLink,
  FolderKanban,
  FolderOpen,
  FolderPen,
  GitBranch,
  Globe,
  History,
  Loader2,
  MessageSquare,
  Mic,
  MicOff,
  MoreHorizontal,
  PanelRight,
  PanelRightClose,
  PanelRightOpen,
  Paperclip,
  Pencil,
  Pin,
  PinOff,
  PlugZap,
  Plus,
  RefreshCw,
  RotateCcw,
  Save,
  Search,
  Send,
  Share2,
  Sparkles,
  Square,
  Store,
  Trash2,
  User,
  Volume2,
  VolumeX,
  Wrench,
  X,
  Zap,
} from "lucide-react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { get, post, put, del, ApiError, API_BASE, ijToken } from "@/lib/api";
import { CommThreadBanner } from "@/components/chat/CommThreadBanner";
import {
  CompactionCard,
  CompactionChip,
  type CompactionInfo,
} from "@/components/chat/CompactionCard";
import { WorkflowDraftCard } from "@/components/chat/WorkflowDraftCard";
import { RunResultCard, type RunResult } from "@/components/chat/RunResultCard";
import { DraftCard, draftFromFence } from "@/components/chat/DraftCard";
import { TurnReceipt, type TurnRoute } from "@/components/chat/TurnReceipt";
import {
  ArtifactsRail,
  confirmUndoPrompt,
  joinUndoByPath,
  normalizeFsPath,
  revertedActionIds,
  type UndoRowLike,
} from "@/components/chat/ArtifactsRail";
import { PreflightNote } from "@/components/chat/PreflightNote";
import { useProviderHealth } from "@/lib/useProviderHealth";
import type { WorkflowDraft } from "@/lib/types";
import type { IJEvent, ModelOption, SessionView } from "@/lib/types";
import { timeAgo } from "@/lib/format";
import { slashTokenAt, tokenAt, spliceToken } from "@/lib/slash";

/** An agent reachable with "@" from chat (GET /agents/mentionable). */
interface MentionableAgent {
  mention: string;
  name: string;
  kind: "builtin" | "dynamic" | "remote" | string;
  source: string;
  description: string;
  healthy: boolean;
  delegable: boolean;
}

/** One agent's turn in a panel round (POST /chat/panel). */
interface PanelEntry {
  who: string;
  role?: string;
  source?: string;
  content: string;
  error?: boolean;
  at?: string;
}
import { useEvents } from "@/lib/useEvents";
import { useDictation } from "@/lib/useDictation";
import { useTTS } from "@/lib/useTTS";
import {
  useChatStream,
  StreamError,
  type ToolCard,
  type ContextUsage,
} from "@/lib/useChatStream";
import { useRunStream } from "@/lib/useRunStream";
import { appendDictation } from "@/components/VoiceInput";
import { Card, Empty, ErrorNote, LoaderInline, OfflineHint } from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";
import { DocPreview } from "@/components/chat/DocPreview";
import { FilesPanel } from "@/components/terminal/FilesPanel";
import { DirectoryTree } from "@/components/terminal/DirectoryTree";
import { KnowledgePanel } from "@/components/project/KnowledgePanel";
import {
  ProjectSurface,
  type ProjectSurfaceView,
} from "@/components/project/ProjectSurfaces";
import { ShareChatDialog } from "@/components/chat/ShareChatDialog";
import {
  SourcesRow,
  WEB_TOOLS,
  extractWebSources,
  type ChatSource,
} from "@/components/chat/SourcesRow";

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  /** Set when a chat turn handed itself to the full agent (v1.108.0): the
   *  reason, shown in place of the reply while the agent works. There are no
   *  modes to pick, so the hand-off has to be visible or it reads as a stall. */
  escalated?: string;
  /** Who the turn handed itself to, as a human phrase ("the researcher",
   *  "your invoice-chaser agent") — only set when a NON-default roster target
   *  was actually chosen (v1.139.0), so the bubble names the specialist. */
  escalatedTo?: string;
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
  /** The provider that ACTUALLY answered when it differs from the one the
   *  user explicitly picked (capability reroute / failover) — an honesty chip
   *  so a local-model turn silently served by a CLI is never invisible.
   *  LEGACY (pre-v1.165.0): kept for messages persisted before `route`
   *  existed; when `route` is present the TurnReceipt supersedes this chip. */
  viaProvider?: string;
  /** SERVER-side route disclosure (v1.165.0): who was asked (""=default
   *  route), who actually answered, and why. The old viaProvider chip compared
   *  against the EXPLICIT pick only, so a downgraded default-route turn — the
   *  mock's "Done. Wrote RESULT.md" incident — surfaced nothing. */
  route?: TurnRoute;
  /** Armed tools the engine refused this turn (assistant messages) — a silent
   *  denial reads as the model ignoring the user, so the receipt shows it. */
  deniedTools?: string[];
  /** ABSOLUTE paths of files this turn created/edited — per-message so the
   *  receipt can say which TURN made which file (threadDocs is the rollup). */
  documents?: string[];
  /** This assistant reply was cut off mid-stream (Stop, or a committed failure)
   *  — shown with a subtle marker so a partial answer never looks complete. */
  interrupted?: boolean;
  /** The turn proposed a reusable workflow — rendered as a draft card
   *  (v1.120.0). Persists with the thread like every other field. */
  workflowDraft?: WorkflowDraft;
  /** The agent session that produced this reply — the "Keep this as a
   *  workflow?" chip's hook (v1.120.0). */
  fromSession?: string;
  /** What that session ACTUALLY did, from the tool ledger (v1.149.0) — files
   *  created/changed, tools run, errors, and what can still be reverted.
   *  Distinct from `content`, which is the model's own account of the work. */
  runResult?: RunResult;
  /** This reply came from an @-mentioned AGENT in a panel round (v1.150.0):
   *  the participant key ("builtin:builder", "remote:hermes"). Rendered with
   *  the agent's name so a three-way conversation is readable. */
  panelWho?: string;
  /** The agent thread the panel lives in — the "open in Agents" link. */
  panelThreadId?: string;
  /** That agent failed this round; its content is an honest error, not a reply. */
  panelError?: boolean;
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
  auto_tools?: boolean; // let the daemon arm safe tools from the request
  connectors?: string[]; // toggled-on connectors: MCP tool groups + memory
};
interface ChatResponse {
  reply: string;
  provider?: string;
  model?: string;
  /** Server-side route disclosure (v1.165.0) — see ChatMessage.route. */
  route?: TurnRoute;
  /** Armed tools the engine refused this turn. */
  denied_tools?: string[];
  images?: string[];
  skill?: string;
  tools_used?: string[];
  /** ABSOLUTE paths of documents this turn created/edited (preview panel). */
  documents?: string[];
  /** The turn asked to be re-run as a full agent session (v1.108.0). */
  escalate?: boolean;
  escalate_reason?: string;
  /** Validated roster name for the hand-off (v1.139.0): "researcher",
   *  "custom:<slug>", "remote:<name>" — null/absent keeps the builder default. */
  escalate_agent?: string | null;
  /** The turn proposed a reusable workflow instead of prose (v1.120.0). */
  workflow_draft?: WorkflowDraft | null;
  /** What the turn cost against the answering model's context window
   *  (v1.146.0) — drives the composer's headroom meter. */
  context?: ContextUsage | null;
}

/** A participant key ("builtin:builder", "remote:hermes-mac-mini") as the name
 *  the user typed after "@" — the source prefix is plumbing, not identity. */
function agentDisplayName(key: string): string {
  return key.includes(":") ? key.slice(key.indexOf(":") + 1) : key;
}

/** Human wording for a run phase (v1.149.0). An unknown phase falls through to
 *  its raw name rather than a generic label — a new phase the daemon adds
 *  should read oddly, not silently look like every other one. */
const PHASE_LABEL: Record<string, string> = {
  planning: "Planning the work…",
  running: "Working…",
  verifying: "Checking its work…",
  assembling: "Writing up the result…",
};

/**
 * Composer context gauge (v1.146.0).
 *
 * Shows nothing below HALF the window — a gauge that is always on is chrome,
 * and the number only becomes actionable as it approaches the edge. Turns amber
 * at 75% and rose once the daemon actually had to drop earlier turns, which is
 * the moment the user would otherwise conclude the assistant "forgot".
 */
function ContextMeter({ usage }: { usage: ContextUsage | null }) {
  if (!usage || !usage.window) return null;
  // v1.153.0: prefer the daemon's RAW fill (what the conversation would need
  // untrimmed) over `used`, which is <= the window by construction and so
  // always reads comfortable at exactly the moment it stops being comfortable.
  const raw = usage.percent ?? Math.round((usage.used / usage.window) * 100);
  const pct = Math.min(100, raw);
  const trimmed = usage.dropped > 0 || usage.clipped;
  if (pct < 50 && !trimmed && !usage.compacted) return null;
  const tone = trimmed
    ? "text-rose-400/90"
    : pct >= 75
      ? "text-amber-400/90"
      : "text-zinc-500";
  const k = (n: number) => (n >= 1000 ? `${Math.round(n / 1000)}k` : String(n));
  return (
    <span
      className={`hidden items-center gap-1 text-[11px] sm:inline-flex ${tone}`}
      title={
        trimmed
          ? `${usage.dropped} earlier message(s) were summarized to fit this model's ${k(
              usage.window,
            )}-token window. A larger-context model would keep them.`
          : `About ${k(usage.used)} of this model's ${k(usage.window)}-token context window is in use.`
      }
    >
      <span className="relative inline-block h-1 w-8 overflow-hidden rounded-full bg-white/10">
        <span
          className="absolute inset-y-0 left-0 rounded-full bg-current"
          style={{ width: `${pct}%` }}
        />
      </span>
      {pct}%
    </span>
  );
}

/**
 * The compaction offer (v1.153.0).
 *
 * Appears in the SUGGEST band — the daemon has noticed the window filling up
 * and has deliberately done nothing about it yet. The user gets first refusal;
 * only past the auto threshold does the daemon compact on its own, because by
 * then there is no headroom left in which to ask.
 *
 * Dismissal is per-band, not permanent: saying "not now" at 72% should not
 * silence the offer at 88%.
 */
function CompactionOffer({
  usage,
  busy,
  onCompact,
  onDismiss,
}: {
  usage: ContextUsage | null;
  busy: boolean;
  onCompact: () => void;
  onDismiss: () => void;
}) {
  // `disabled` means the user turned compaction off: the gauge still reports
  // the true fill level, but there is no offer to make.
  if (!usage || usage.level !== "suggest" || usage.disabled) return null;
  const pct = usage.percent ?? 0;
  const auto = usage.auto_at ?? 92;
  return (
    <div className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-amber-400/25 bg-amber-400/5 px-3 py-2 text-[12px] text-amber-200/90">
      <span>
        This conversation is using about <strong>{pct}%</strong> of this model&apos;s
        context window. Summarizing the earlier part keeps the thread going —
        the full transcript is kept either way.
      </span>
      <span className="ml-auto flex items-center gap-2">
        <button
          type="button"
          onClick={onCompact}
          disabled={busy}
          className="rounded-md border border-amber-400/40 px-2 py-1 font-medium text-amber-100 transition-colors hover:bg-amber-400/15 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy ? "Summarizing…" : "Compact now"}
        </button>
        <button
          type="button"
          onClick={onDismiss}
          disabled={busy}
          className="text-amber-200/60 transition-colors hover:text-amber-100"
          title={`If you do nothing, this happens automatically around ${auto}%.`}
        >
          Not now
        </button>
      </span>
    </div>
  );
}

/** Human phrase for a roster agent name (v1.139.0), used by the hand-off
 *  bubble: "researcher" → "the researcher"; "custom:invoice-chaser" → "your
 *  invoice-chaser agent"; "remote:hermes-mac-mini" → "the hermes-mac-mini
 *  remote agent". Plain and honest — no prefixes shown to the user. */
function agentPhrase(name: string): string {
  const custom = name.startsWith("custom:");
  const remote = name.startsWith("remote:");
  const bare = (custom || remote ? name.slice(name.indexOf(":") + 1) : name).trim();
  // A degenerate name (empty slug etc.) can only arrive on a broken wire —
  // degrade to a sane phrase, never "your  agent" / "the undefined".
  if (!bare) return "a specialist agent";
  if (custom) return `your ${bare} agent`;
  if (remote) return `the ${bare} remote agent`;
  return `the ${bare}`;
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
  /** "user" (default) or "daemon" — daemon-owned rows are MESSAGING threads
   *  the server writes; the page must never PUT their messages (409). */
  owner?: string;
  /** Messaging origin id (e.g. "telegram") when daemon-owned. */
  comm_channel?: string;
  /** Human sender label (e.g. "Val") when daemon-owned. */
  comm_display?: string;
}

/** Per-thread setup the daemon stores alongside the transcript: what was armed
 *  when the conversation last saved. Returned as `setup` by GET
 *  /chat/threads/{id}; accepted on PUT (all five keys sent, empties meaning
 *  deliberately cleared). */
interface ThreadSetup {
  tools?: string[];
  /** Connectors toggled ON for this conversation (MCP servers / memory). */
  connectors?: string[];
  /** Documents this conversation generated — the preview chips persist with
   *  the thread (and across restarts) until deliberately dismissed. */
  documents?: string[];
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
  /** Transcript-derived document paths for threads saved before v1.91.0
   *  recorded them — existence-checked server-side, so chips are real. */
  derived_documents?: string[];
  /** "user" (default) or "daemon" — daemon-owned = a MESSAGING thread: the
   *  server appends both sides; the page renders it live and replies through
   *  POST /comm/threads/{id}/send, never PUT. */
  owner?: string;
  /** Messaging origin id (e.g. "telegram") when daemon-owned. */
  comm_channel?: string;
  /** Human sender label (e.g. "Val") when daemon-owned. */
  comm_display?: string;
}

/** PUT /chat/threads/{id} body + response. */
interface ThreadSaveBody {
  /** Omitted for title/project-only updates on daemon-owned (messaging)
   *  threads — writing `messages` there is a 409. */
  messages?: ChatMessage[];
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

/** One /connectors gallery entry, as the "+" Connectors flyout consumes it. */
interface ConnectorEntry {
  id: string;
  name: string;
  glyph?: string;
  connected?: boolean;
  /** "mcp" | "oauth" | "api_key" | "memory" — memory = an LTM source/brain. */
  connect_via?: string;
  tools_loaded?: number;
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
// Connector toggles: /chat accepts at most this many toggled-on connectors
// (an MCP connector arms its whole tool group server-side, additive to the
// 6-tool cap above; a memory connector grounds the turn with its top hits).
const MAX_CONNECTORS = 6;
// Document paths remembered per thread (the Artifacts rail: files this
// conversation made or was given) — newest survive the cap, matching the
// daemon's setup validation (_MAX_THREAD_DOCS, raised 8 → 30 in v1.166.0).
const MAX_THREAD_DOCS = 30;
// Resizable side rail (preview/workspace column): width bounds + persistence.
const RAIL_W_KEY = "ij_chat_rail_w";
const RAIL_MIN_W = 280;
const RAIL_DEFAULT_W = 320;

/** Clamp a rail width: never below the usable minimum, never past ~70% of the
 *  viewport (the conversation must stay readable beside it). */
function clampRailW(w: number): number {
  const max = Math.min(920, Math.round(window.innerWidth * 0.7));
  return Math.max(RAIL_MIN_W, Math.min(max, w));
}

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
  integrations: "Plug-ins (MCP)",
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
// Auto tools: "0" = the user turned the seamless arming off (default on).
const AUTO_TOOLS_KEY = "ij_chat_auto_tools";
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

/** Hover action "Add to project knowledge" (v1.168.0): promotes an assistant
 *  reply into the bound project's knowledge through the EXISTING knowledge
 *  path. Disabled with the honest reason when no project is bound (never a
 *  silent no-op); success flashes a quiet check like the copy button; a
 *  failure surfaces the server's error right next to the button. */
function PromoteKnowledgeButton({
  disabledReason,
  onPromote,
}: {
  disabledReason?: string | null;
  onPromote: () => Promise<void>;
}) {
  const [state, setState] = useState<"idle" | "busy" | "done">("idle");
  const [err, setErr] = useState<string | null>(null);
  const timerRef = useRef<number | null>(null);
  useEffect(
    () => () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    },
    [],
  );
  async function run() {
    if (state === "busy") return;
    setState("busy");
    setErr(null);
    try {
      await onPromote();
      setState("done");
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
      timerRef.current = window.setTimeout(() => setState("idle"), 1800);
    } catch (e) {
      setState("idle");
      setErr(e instanceof Error ? e.message : String(e));
    }
  }
  const label = disabledReason
    ? `Add to project knowledge — ${disabledReason}`
    : "Add to project knowledge";
  return (
    <span className="inline-flex min-w-0 items-center gap-1">
      <button
        type="button"
        disabled={!!disabledReason || state === "busy"}
        onClick={() => void run()}
        title={label}
        aria-label={label}
        className="grid h-6 w-6 place-items-center rounded-md text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-accent-soft disabled:cursor-not-allowed disabled:opacity-40"
      >
        {state === "busy" ? (
          <Loader2 size={12} className="animate-spin" />
        ) : state === "done" ? (
          <Check size={12} className="text-emerald-400" />
        ) : (
          <BookmarkPlus size={12} />
        )}
      </button>
      {err && (
        <span className="truncate text-[10.5px] text-rose-300/90">{err}</span>
      )}
    </span>
  );
}

// Lets the <code> override know it sits inside a <pre> block (block code keeps
// the pre's styling; standalone inline code gets the accent pill).
const PreContext = createContext(false);

/** Fenced code block: dark panel + hover copy button — unless the fence is a
 *  draft, which becomes a boxed, rich-copyable card instead. */
function MarkdownPre({ children }: { children?: ReactNode }) {
  const text = nodeText(children).replace(/\n$/, "");
  // The fence's CONTENT is markdown, not code: the model writes **bold** and
  // bullet lists in a draft, and a fence stops the outer pass from parsing
  // them. Parsing it here is what makes the copied HTML carry real formatting
  // instead of literal asterisks. All of the decision-making lives in
  // draftFromFence so this call site and the tests share one implementation.
  const draft = draftFromFence(children, text);
  if (draft) {
    return (
      <DraftCard subject={draft.subject} text={draft.text}>
        <Markdown content={draft.markdown} />
      </DraftCard>
    );
  }
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
  // Live per-provider availability (v1.165.0) — drives the preflight note
  // above the composer. 5s default keeps it in step with the topbar switcher.
  const health = useProviderHealth();
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
  // DOCUMENT PREVIEW (right rail): set when a turn creates/edits a document —
  // the chat column shifts over and the file renders beside the conversation.
  const [previewPath, setPreviewPath] = useState<string | null>(null);
  // The conversation's generated documents — persisted in the thread setup so
  // the preview chips survive leaving the page and restarts until dismissed.
  const [threadDocs, setThreadDocs] = useState<string[]>([]);
  // UNDO WHERE YOU LOOK (v1.168.0): chat's undo-journal rows (GET
  // /undo?session_id=chat — every chat tool call runs as session id "chat"),
  // joined to rail items / receipt file chips by ABSOLUTE path so "Undo this
  // write" lives next to the file it reverts. Refetched whenever the thread's
  // document list changes (every file-writing turn changes it) and after an
  // undo.
  const [undoRows, setUndoRows] = useState<UndoRowLike[]>([]);
  // Rows undone THIS visit: the refetched list drops them (the route only
  // lists live candidates), but the affordance must GREY to "already undone"
  // rather than vanish — vanishing reads as "this was never undoable". So the
  // undone row is stashed (undoneRows) and its id marked (undoneIds).
  const [undoneIds, setUndoneIds] = useState<Set<string>>(new Set());
  const [undoneRows, setUndoneRows] = useState<UndoRowLike[]>([]);
  // Bumped after an undo: it keys the DocPreview, so an open preview of the
  // reverted file remounts and refetches instead of showing stale content.
  const [previewNonce, setPreviewNonce] = useState(0);
  // Side-rail width (px, desktop only): draggable via the rail's left-edge
  // grip, clamped, persisted per device. Default keeps today's layout.
  const [railW, setRailW] = useState(RAIL_DEFAULT_W);
  useEffect(() => {
    try {
      const saved = parseInt(window.localStorage.getItem(RAIL_W_KEY) || "", 10);
      if (Number.isFinite(saved)) setRailW(clampRailW(saved));
    } catch {
      /* keep the default */
    }
  }, []);
  const [pickingFolder, setPickingFolder] = useState(false); // "change folder"
  const [attachments, setAttachments] = useState<UploadedFile[]>([]);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [input, setInput] = useState("");
  // "+" TOOLS MENU (chat mode): armed registry tool names — sent as `tools` on
  // every /chat turn and kept across turns until "New chat" / a thread switch.
  const [selectedTools, setSelectedTools] = useState<string[]>([]);
  // "+" CONNECTOR TOGGLES (chat mode): connector ids toggled ON — sent as
  // `connectors` on every /chat turn. An MCP connector arms its whole tool
  // group server-side; a memory connector grounds the turn with its store.
  const [selectedConnectors, setSelectedConnectors] = useState<string[]>([]);
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
  // "/" SKILL PICKER (both modes): the chosen skill rides along as `skill` on
  // every turn until its chip is cleared. `slashDismissed` = Esc closed the
  // dropdown for the current "/…" text (any edit reopens it).
  const [skills, setSkills] = useState<SkillOption[] | null>(null);
  const [activeSkill, setActiveSkill] = useState("");
  const [skillIndex, setSkillIndex] = useState(0);
  const [slashDismissed, setSlashDismissed] = useState(false);
  // "@" agent picker (v1.150.0): the catalog + whether Esc closed the dropdown.
  const [mentionable, setMentionable] = useState<MentionableAgent[] | null>(null);
  const [atDismissed, setAtDismissed] = useState(false);
  // Caret offset in the composer. The picker keys off the "/" token AT THE
  // CARET (v1.105.0), not the start of the message, so it needs to know where
  // the cursor is — mid-sentence "/" is the whole point of that change.
  const [caret, setCaret] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [offline, setOffline] = useState(false);
  // Threads sidebar: the saved-conversation list + which one is loaded.
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [threadId, setThreadId] = useState<string | null>(null);
  // Open MESSAGING thread (owner === "daemon", v1.136.0): the daemon writes it
  // (phone conversation mirrored here live); the composer replies through
  // POST /comm/threads/{id}/send and the page NEVER autosaves it.
  const [commMeta, setCommMeta] = useState<{
    channel: string;
    display: string;
  } | null>(null);
  // Mirror for send()/watchers that fire from keydown handlers and timers.
  const commMetaRef = useRef<{ channel: string; display: string } | null>(null);
  commMetaRef.current = commMeta;
  // Share dialog for the OPEN thread (full transcript / compacted digest).
  const [shareOpen, setShareOpen] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false); // mobile-only toggle
  const [threadQuery, setThreadQuery] = useState(""); // sidebar title filter
  // Pinned threads (per-device view preference) + inline rename state.
  const [pinnedIds, setPinnedIds] = useState<string[]>([]);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState("");
  // Commit-to-memory (per-thread): in-flight id + transient success id.
  const [rememberingId, setRememberingId] = useState<string | null>(null);
  const [rememberedId, setRememberedId] = useState<string | null>(null);
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
  // AUTO TOOLS (chat mode): the daemon reads each request and arms matching
  // safe tools (files/documents/web/vision) in the free "+" slots — explicit
  // picks always ride first, and replies still list exactly what RAN. Default
  // ON (the seamless path); the composer chip toggles it, persisted.
  const [autoTools, setAutoTools] = useState(true);
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
    const filtered = q
      ? threads.filter((t) => (t.title || "").toLowerCase().includes(q))
      : threads;
    // Pinned float to the top; the sort is stable so recency holds within
    // each group.
    return [...filtered].sort(
      (a, b) => Number(pinnedIds.includes(b.id)) - Number(pinnedIds.includes(a.id)),
    );
  }, [threads, threadQuery, pinnedIds]);

  // Hydrate pins once (per-device preference, like the workspace defaults).
  useEffect(() => {
    try {
      const raw = window.localStorage.getItem("ij_chat_pinned");
      const arr = raw ? (JSON.parse(raw) as unknown) : [];
      if (Array.isArray(arr)) setPinnedIds(arr.filter((x) => typeof x === "string"));
    } catch {
      /* no pins */
    }
  }, []);

  function togglePin(id: string) {
    setPinnedIds((prev) => {
      const next = prev.includes(id) ? prev.filter((x) => x !== id) : [id, ...prev];
      try {
        window.localStorage.setItem("ij_chat_pinned", JSON.stringify(next));
      } catch {
        /* pins just don't persist */
      }
      return next;
    });
  }

  /** Rename any listed thread: fetch its messages, PUT them back with the new
   *  title (the save route treats omitted fields as untouched). */
  // ⋯ THREAD MENU (v1.114.0). The four per-row hover icons (memory / pin /
  // rename / delete) compressed into one kebab whose popout also gained "Add
  // to project". Rendered through a PORTAL with position:fixed — the sidebar
  // Card is overflow-hidden with an inner scroll area, and under Mark 8 the
  // card surface carries backdrop-blur, which hijacks fixed positioning for
  // descendants — a portal to <body> escapes both.
  const [threadMenu, setThreadMenu] = useState<{
    id: string;
    x: number;
    y: number;
    up: boolean;
  } | null>(null);
  const [threadMenuProjects, setThreadMenuProjects] = useState(false);
  const [assigningThread, setAssigningThread] = useState(false);
  const threadMenuRef = useRef<HTMLDivElement | null>(null);

  function openThreadMenu(e: React.MouseEvent, id: string) {
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
    // Open upward when the row sits near the viewport bottom — a fixed menu
    // can't rely on a scroll container to make room for it.
    const up = window.innerHeight - r.bottom < 340;
    setThreadMenuProjects(false);
    setThreadMenu({ id, x: r.right, y: up ? r.top - 4 : r.bottom + 4, up });
  }

  useEffect(() => {
    if (!threadMenu) return;
    const close = () => setThreadMenu(null);
    const onDown = (e: MouseEvent) => {
      if (!threadMenuRef.current?.contains(e.target as Node)) close();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    // Any scroll OUTSIDE the menu strands a fixed popout at stale coordinates
    // — close instead of drifting. Scrolls INSIDE it (the project list) are
    // the menu working as intended.
    const onScroll = (e: Event) => {
      if (threadMenuRef.current?.contains(e.target as Node)) return;
      close();
    };
    document.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", close);
    return () => {
      document.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", close);
    };
  }, [threadMenu]);

  /** Tag a thread to a project (or null to untag) — the same read-then-PUT
   *  shape renameThread uses; the daemon treats an explicit project_id key as
   *  assign-or-clear and never infers one on update. */
  async function assignThreadProject(id: string, pid: string | null) {
    setAssigningThread(true);
    try {
      // Daemon-owned (messaging) threads reject `messages` writes with 409 —
      // tag them with a project_id-only body (the route's carve-out).
      if (threads.find((x) => x.id === id)?.owner === "daemon") {
        await put(`/chat/threads/${encodeURIComponent(id)}`, { project_id: pid });
      } else {
        const t = await get<ThreadDetail>(
          `/chat/threads/${encodeURIComponent(id)}`,
        );
        await put(`/chat/threads/${encodeURIComponent(id)}`, {
          messages: t.messages ?? [],
          project_id: pid,
        });
      }
      void refreshThreads();
      setThreadMenu(null);
    } catch (e) {
      if (e instanceof ApiError && e.status === 0) setOffline(true);
      else setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setAssigningThread(false);
    }
  }

  async function renameThread(id: string, title: string) {
    const clean = title.trim();
    setRenamingId(null);
    if (!clean) return;
    try {
      // Same 409 carve-out as assignThreadProject: rename a messaging thread
      // with a title-only body — its messages belong to the daemon.
      if (threads.find((x) => x.id === id)?.owner === "daemon") {
        await put(`/chat/threads/${encodeURIComponent(id)}`, { title: clean });
      } else {
        const t = await get<ThreadDetail>(
          `/chat/threads/${encodeURIComponent(id)}`,
        );
        await put(`/chat/threads/${encodeURIComponent(id)}`, {
          messages: t.messages ?? [],
          title: clean,
        });
      }
      void refreshThreads();
    } catch (e) {
      if (e instanceof ApiError && e.status === 0) setOffline(true);
      else setError(e instanceof ApiError ? e.message : String(e));
    }
  }
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
  // Composer project quick-toggle popover (the cowork switch).
  const projPopRef = useRef<HTMLDivElement>(null);
  const [projMenuOpen, setProjMenuOpen] = useState(false);
  // The rail IS the project workspace now (Projects left the nav): Files or
  // Knowledge inline; the wide surfaces (tasks/board/media) open from here.
  const [railTab, setRailTab] = useState<"files" | "knowledge">("files");
  // The conversation column can flip to a full project surface (tasks/board/
  // media) IN PLACE — the old project screen, inside the chat module.
  const [projectView, setProjectView] = useState<"chat" | ProjectSurfaceView>(
    "chat",
  );
  // Which "+" submenu flyout is open (skills / connectors / project).
  const [plusSub, setPlusSub] = useState<
    "skills" | "connectors" | "project" | null
  >(null);
  // The minimalist bottom-right model switcher: name + chevron, no box;
  // opens a provider list whose ▸ flyouts hold that provider's models.
  const modelPopRef = useRef<HTMLDivElement>(null);
  // Context-window accounting from the LAST turn (v1.146.0). Server-computed;
  // the composer only renders it, so the meter can never disagree with what the
  // planner actually budgeted. Cleared with the conversation.
  const [contextUsage, setContextUsage] = useState<ContextUsage | null>(null);
  // Compaction (v1.153.0). `compactDismissedAt` remembers the fill level the
  // user waved away, so "not now" at 72% stays quiet until the conversation
  // grows meaningfully — and does NOT silence the offer again at 88%.
  const [compactBusy, setCompactBusy] = useState(false);
  const [compactNote, setCompactNote] = useState<string | null>(null);
  const [compactDismissedAt, setCompactDismissedAt] = useState<number | null>(null);
  // Compaction inspect (v1.169.0): the summary STANDING over this thread —
  // SERVER truth (GET /chat/threads/{id}/compaction), fetched on thread load
  // and after a compact, never guessed from the gauge. The chip and card
  // render only while this is a found record.
  const [compaction, setCompaction] = useState<CompactionInfo | null>(null);
  const [compactionOpen, setCompactionOpen] = useState(false);
  // Monotonic fetch id so a slow response for the PREVIOUS thread can never
  // land on the one now open (same shape as chatGenRef for turns).
  const compactionGenRef = useRef(0);
  const [modelMenuOpen, setModelMenuOpen] = useState(false);
  const [modelSub, setModelSub] = useState<string | null>(null);
  /**
   * Providers for the composer's model menu, LOCAL FIRST (v1.148.0).
   *
   * The menu used to be whatever order the daemon happened to return, with no
   * indication of where anything ran — a 14B on your own box and a metered
   * frontier model read identically. Order is now: your own hardware, then
   * flat-rate subscription CLIs, then metered APIs, then anything currently
   * offline; within local, smallest model first, matching how the router now
   * escalates. `kind` comes from the daemon (one definition, shared with
   * /health), so the label can never disagree with the routing.
   */
  const modelProviders = useMemo(() => {
    const seen = new Map<
      string,
      { id: string; label: string; kind: string; available: boolean; size: number }
    >();
    for (const m of models) {
      const kind = m.kind ?? "api";
      const size = typeof m.size_b === "number" ? m.size_b : Number.POSITIVE_INFINITY;
      const prev = seen.get(m.provider);
      if (!prev) {
        seen.set(m.provider, {
          id: m.provider,
          label: m.name || m.provider,
          kind,
          available: m.available !== false,
          size,
        });
      } else {
        // A provider is "available" if ANY of its models is, and sorts by its
        // SMALLEST model — the rung the router would reach for first.
        prev.available = prev.available || m.available !== false;
        prev.size = Math.min(prev.size, size);
      }
    }
    const RANK: Record<string, number> = { local: 0, cli: 1, api: 2 };
    return [...seen.values()].sort((a, b) => {
      if (a.available !== b.available) return a.available ? -1 : 1; // offline last
      const ra = RANK[a.kind] ?? 3;
      const rb = RANK[b.kind] ?? 3;
      if (ra !== rb) return ra - rb;
      if (a.size !== b.size) return a.size - b.size; // smallest local rung first
      return a.label.localeCompare(b.label);
    });
  }, [models]);

  /** Badge for a provider row: where it runs, in one word. */
  const KIND_BADGE: Record<string, { text: string; cls: string }> = {
    local: { text: "local", cls: "text-emerald-400/80" },
    cli: { text: "included", cls: "text-sky-400/80" },
    api: { text: "metered", cls: "text-zinc-500" },
  };
  const modelLabel = useMemo(() => {
    if (!choice) return "default model";
    const { model } = splitChoice(choice);
    return model || choice.replace("::", " · ");
  }, [choice]);
  useEffect(() => {
    if (!modelMenuOpen) return;
    const onDown = (e: MouseEvent) => {
      if (!modelPopRef.current?.contains(e.target as Node)) {
        setModelMenuOpen(false);
        setModelSub(null);
      }
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [modelMenuOpen]);
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
  // Latest thread-doc list (v1.166.0). A turn's async closure spans awaits, so
  // by completion its captured `threadDocs` is stale — a second merge in the
  // same turn (attachments up front, made-docs at the end) would silently drop
  // the first. Every merge below reads AND writes this ref synchronously; the
  // render assignment keeps it in step with every other setThreadDocs site.
  const threadDocsRef = useRef<string[]>(threadDocs);
  threadDocsRef.current = threadDocs;
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
  // `daemon: true` marks the box as a MESSAGING thread: the server owns its
  // messages, so every queued save against that box is a deliberate no-op.
  const saveTargetRef = useRef<{ id: string | null; daemon?: boolean }>({
    id: null,
  });
  // The persona selected before "+ New persona" — restored if the new-persona
  // editor is closed without saving.
  const prevPersonaRef = useRef("assistant");
  // True once ANY persona selection happened (explicit pick, saved restore, or
  // an opened thread's own persona) — the async server-default seed below must
  // never clobber a choice that landed while its fetch was in flight.
  const personaTouchedRef = useRef(false);
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
        // v1.148.0: keep the ones that AREN'T connected too. They used to be
        // filtered out entirely, which answered "why isn't my model in the
        // list?" with silence; they now sort last, are badged "offline", and
        // are not selectable — labelled beats hidden, and a dead option the
        // user can't click is not the "silently fails" trap the filter existed
        // to prevent.
        if (!cancelled) setModels(d.models);
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
  // matches the first client render). When NO persona has ever been saved
  // here, seed the state from the server's default_persona setting instead of
  // the hardcoded "assistant" — the browser used to send that hardcoded value
  // with every turn (persona is sent whenever non-empty), which MASKED any
  // configured server default. Best-effort: on any failure "assistant" stands.
  useEffect(() => {
    let cancelled = false;
    let saved: string | null = null;
    try {
      saved = window.localStorage.getItem(PERSONA_KEY);
      if (saved) {
        setPersona(saved);
        prevPersonaRef.current = saved;
        personaTouchedRef.current = true;
      }
      const wd = window.localStorage.getItem(WORKSPACE_KEY);
      if (wd) setWorkspaceDir(wd);
      const wo = window.localStorage.getItem(WORKSPACE_OPEN_KEY);
      if (wo === "1") setWorkspaceOpen(true);
      if (window.localStorage.getItem(AUTO_TOOLS_KEY) === "0") setAutoTools(false);
    } catch {
      /* ignore */
    }
    if (!saved) {
      get<{ settings: { default_persona?: string } }>("/settings")
        .then((d) => {
          const dp = (d.settings?.default_persona || "").trim();
          // Seed only — never over an explicit pick / restored thread persona
          // that landed while this fetch was in flight, and never persisted to
          // localStorage (only the user's own choices are, via choosePersona).
          if (!cancelled && dp && !personaTouchedRef.current) {
            setPersona(dp);
            prevPersonaRef.current = dp;
          }
        })
        .catch(() => {
          /* keep "assistant" */
        });
    }
    return () => {
      cancelled = true;
    };
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

  // Global-search landing params (v1.111.0), all one-shot and all PREFILL/OPEN
  // only — never auto-send. The consent rule is that side effects wait for the
  // user's Enter, and a search box that fires agent work on its own breaks it.
  // Same window.location pattern as ?project= above (static route — no
  // useSearchParams).
  //   ?ask=<text>    the "Ask Iron Jarvis: …" fallback row — prefill the composer
  //   ?skill=<name>  a skill picked in search — arm it (chip shows; nothing runs)
  //   ?thread=<id>   a saved conversation picked in search — open it
  useEffect(() => {
    try {
      const params = new URLSearchParams(window.location.search);
      const ask = (params.get("ask") || "").trim();
      const skill = (params.get("skill") || "").trim();
      const thread = (params.get("thread") || "").trim();
      if (ask) {
        setInput(ask);
        setCaret(ask.length);
        inputRef.current?.focus();
      }
      if (skill) {
        // Arm, don't validate: an unknown name just yields a chip the user can
        // clear, which beats silently dropping their pick. markSetupChanged so
        // the armed skill persists with the thread like a "/"-picked one.
        setActiveSkill(skill);
        markSetupChanged();
        inputRef.current?.focus();
      }
      if (thread) void openThread(thread);
      if (ask || skill || thread) {
        // Strip the params so a refresh doesn't resurrect stale state over
        // whatever the user has done since.
        const url = new URL(window.location.href);
        url.searchParams.delete("ask");
        url.searchParams.delete("skill");
        url.searchParams.delete("thread");
        window.history.replaceState(null, "", url.toString());
      }
    } catch {
      /* a malformed URL must never break the page */
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function choosePersona(value: string) {
    setPersona(value);
    prevPersonaRef.current = value;
    personaTouchedRef.current = true;
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
    personaTouchedRef.current = true;
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
    setProjectView("chat"); // a plain chat has no project surfaces
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

  /** Re-pull the OPEN messaging thread after a daemon-side append
   *  (chat.thread_updated). Replace-only: the server owns comm threads, so its
   *  array is truth — never merged, never PUT back. Scroll behaves sanely for
   *  free: the messages effect follows only while the reader is pinned near
   *  the bottom, and an un-pinned reader keeps their place because content
   *  only grows below the fold. */
  async function refetchCommThread() {
    const id = saveTargetRef.current.id;
    if (!id) return;
    const gen = chatGenRef.current;
    try {
      const t = await get<ThreadDetail>(`/chat/threads/${encodeURIComponent(id)}`);
      // The user may have switched conversations while the fetch was airborne.
      if (chatGenRef.current !== gen || saveTargetRef.current.id !== id) return;
      setMessages(t.messages ?? []);
      if (t.owner === "daemon") {
        setCommMeta({
          channel: t.comm_channel ?? "",
          display: t.comm_display ?? "",
        });
        saveTargetRef.current.daemon = true;
      }
    } catch {
      /* quiet — the next thread_updated event retries */
    }
  }

  // LIVE COMM UPDATES (v1.136.0): the daemon appends to messaging threads
  // server-side (phone messages + its own replies) and announces every write
  // as a chat.thread_updated event on the /events socket. New frames refetch
  // the open thread when it's the one that changed, and opportunistically
  // refresh the sidebar list either way. The seen-boundary is an event id so
  // a re-render never re-processes old frames into refetch loops.
  const commEventSeenRef = useRef<string | null>(null);
  useEffect(() => {
    const newest = events[0];
    if (!newest) return;
    const boundary = commEventSeenRef.current;
    commEventSeenRef.current = newest.id;
    let listStale = false;
    let openStale = false;
    for (const e of events) {
      if (e.id === boundary) break; // frames already processed
      if (e.type !== "chat.thread_updated") continue;
      listStale = true;
      const tid = (e.payload as { thread_id?: unknown } | null)?.thread_id;
      if (typeof tid === "string" && tid === saveTargetRef.current.id)
        openStale = true;
    }
    if (listStale) void refreshThreads();
    if (openStale) void refetchCommThread();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [events]);

  /** The thread-setup snapshot for saves: exactly what's armed right now. All
   *  five keys always ride along so a cleared skill/model reads as deliberately
   *  cleared, not merely omitted. */
  function currentSetup(): ThreadSetup {
    const { provider, model } = splitChoice(choice);
    return {
      tools: selectedTools.slice(0, MAX_TOOLS),
      connectors: selectedConnectors.slice(0, MAX_CONNECTORS),
      // Via the ref, not the closure: queueSave runs at turn COMPLETION inside
      // the send's stale closure, and the docs merged during the turn
      // (attachments, made files) must ride that save (v1.166.0).
      documents: threadDocsRef.current.slice(-MAX_THREAD_DOCS),
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
    // Daemon-owned (messaging) threads are server-authoritative — a setup
    // tweak must never PUT their messages (the route 409s it anyway).
    if (saveTargetRef.current.daemon) return;
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
    // MESSAGING threads (owner === "daemon") are the server's to write: the
    // daemon has already persisted every message, and PUT would 409. The next
    // chat.thread_updated refetch reconciles the view instead.
    if (target.daemon) return;
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
    setSelectedConnectors([]); // so are connector toggles
    setPreviewPath(null); // the preview belongs to the previous conversation
    setThreadDocs([]); // until this thread's setup (if any) restores its own
    // Clear the chip AND orphan any in-flight compaction fetch: a bare state
    // clear leaves compactionGenRef untouched, so a GET started for the
    // PREVIOUS thread would still pass the gen guard when it resolves after
    // this reset and repaint the old thread's summary here. (The gen is
    // bumped again after the thread GET below — but that await can throw, and
    // this reset must stand on its own.)
    void refreshCompaction(null); // bumps the gen, then clears
    setCompactionOpen(false);
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
      // Does a compaction summary stand over this thread? Server-checked on
      // every open (v1.169.0) — the chip must reflect the store, not memory.
      void refreshCompaction(t.id);
      // MESSAGING thread? The server owns it: mark the save box daemon so
      // every queued autosave no-ops, and surface the origin banner.
      const isDaemon = t.owner === "daemon";
      setCommMeta(
        isDaemon
          ? { channel: t.comm_channel ?? "", display: t.comm_display ?? "" }
          : null,
      );
      saveTargetRef.current = { id: t.id, daemon: isDaemon };
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
        setSelectedConnectors(
          Array.isArray(setup.connectors)
            ? setup.connectors
                .filter((x) => typeof x === "string")
                .slice(0, MAX_CONNECTORS)
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
      // Document chips: recorded ones win; otherwise the server's transcript-
      // derived recovery fills in for threads saved before v1.91.0 recorded
      // them. No auto-open — the chips offer the preview until dismissed.
      const recorded =
        setup && typeof setup === "object" && Array.isArray(setup.documents)
          ? setup.documents.filter((x) => typeof x === "string")
          : [];
      const derived = Array.isArray(t.derived_documents)
        ? t.derived_documents.filter((x) => typeof x === "string")
        : [];
      setThreadDocs(
        (recorded.length ? recorded : derived).slice(-MAX_THREAD_DOCS),
      );
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

  /** Commit a thread to LONG-TERM MEMORY (the sidebar Brain action): the
   *  daemon distills it through a real model (or stores an honest verbatim
   *  excerpt offline) into the default brain. The button shows a transient
   *  check on success; the note lands on the Memory page. */
  // "Turn into workflow" (v1.120.0): the thread's work, generalized into a
  // reusable draft by the daemon, appended to the conversation as a card.
  const [crystallizingId, setCrystallizingId] = useState<string | null>(null);
  async function crystallizeThread(id: string) {
    if (crystallizingId) return;
    setCrystallizingId(id);
    try {
      if (threadId !== id) await openThread(id);
      // Flush pending autosaves FIRST: the daemon distills the STORED
      // transcript, and the chip can render before the final agent turn's PUT
      // has landed — crystallizing then would omit the newest work.
      await saveChainRef.current.catch(() => {});
      // The crystallize POST is a multi-second model call. If the user opens
      // another thread or starts a new chat meanwhile, appending via the refs
      // would write this draft into the WRONG thread — the same teardown class
      // completeChat guards with chatGenRef.
      const gen = chatGenRef.current;
      const draft = await post<WorkflowDraft>(
        `/chat/threads/${encodeURIComponent(id)}/crystallize`,
        {},
      );
      setThreadMenu(null);
      if (chatGenRef.current !== gen) return; // conversation moved on — drop
      const full: ChatMessage[] = [
        ...messagesRef.current,
        {
          role: "assistant",
          content: "Here's this conversation as a reusable workflow:",
          workflowDraft: draft,
        },
      ];
      setMessages(full);
      queueSave(full);
    } catch (e) {
      setThreadMenu(null);
      // The honest offline 400 ("connect a model…") lands here too.
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setCrystallizingId(null);
    }
  }

  async function rememberThread(id: string) {
    if (rememberingId) return; // one commit at a time
    setRememberingId(id);
    setRememberedId(null);
    try {
      await post<{ ok: boolean; ref: string; source: string; distilled: boolean }>(
        `/chat/threads/${id}/remember`,
        {},
      );
      setRememberedId(id);
      window.setTimeout(
        () => setRememberedId((cur) => (cur === id ? null : cur)),
        2500,
      );
    } catch (e) {
      if (e instanceof ApiError && e.status === 0) setOffline(true);
      else setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setRememberingId(null);
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

  // The "/" token the caret is sitting in, or null. Until v1.105.0 this was
  // `input.startsWith("/")`, so a skill could only be invoked when "/" was the
  // FIRST character of an empty composer — you could not write the prompt you
  // wanted and then reach for a skill part-way through it.
  //
  // The "/" must open a word: preceded by start-of-text or whitespace, and the
  // token itself carries no further "/". That is what keeps ordinary typing
  // from flickering a dropdown — `http://x`, `C:/Users`, `and/or` and `24/7`
  // are all rejected because their "/" follows a non-space character.
  //
  // Available in BOTH modes since v1.104.0 (it was gated to chat, so in Agent
  // mode "/" silently did nothing). The modes APPLY the skill differently, see
  // sendAgent: chat injects the playbook server-side, an agent has skill_load.
  const slashToken = useMemo(
    () => (busy || slashDismissed ? null : slashTokenAt(input, caret)),
    [input, caret, busy, slashDismissed],
  );

  const slashActive = slashToken !== null;
  const slashQuery = slashToken?.query ?? "";

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

  // "@" AGENT PICKER (v1.150.0) — the same token rule as "/", so a mention can
  // be reached for mid-sentence and an email address never opens a dropdown.
  const atToken = useMemo(
    () => (busy || atDismissed ? null : tokenAt(input, caret, "@")),
    [input, caret, busy, atDismissed],
  );
  const atActive = atToken !== null;
  const atQuery = atToken?.query ?? "";
  const agentMatches = useMemo(() => {
    if (!atActive) return [] as MentionableAgent[];
    const list = mentionable ?? [];
    if (!atQuery) return list;
    return list.filter(
      (a) =>
        a.mention.toLowerCase().includes(atQuery) ||
        (a.description || "").toLowerCase().includes(atQuery),
    );
  }, [atActive, atQuery, mentionable]);

  /** Mentions in the composer that resolve to a REAL agent — the send path
   *  routes to the panel only when at least one does, so "@ 9am" or an email
   *  address never diverts a normal message. */
  const liveMentions = useMemo(() => {
    const known = new Set((mentionable ?? []).map((a) => a.mention.toLowerCase()));
    const found = input.match(/(?<![A-Za-z0-9._-])@([A-Za-z0-9][A-Za-z0-9._-]*)/g) ?? [];
    return found
      .map((t) => t.slice(1).replace(/[._-]+$/, "").toLowerCase())
      .filter((t) => known.has(t));
  }, [input, mentionable]);

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

  // The mentionable-agent catalog. Fetched ONCE up front rather than lazily on
  // the first "@", because `liveMentions` needs it to decide whether a typed
  // mention is real — a message sent before the catalog arrived would silently
  // route to normal chat instead of the panel.
  useEffect(() => {
    let cancelled = false;
    get<{ agents: MentionableAgent[] }>("/agents/mentionable")
      .then((d) => {
        if (!cancelled) setMentionable(d.agents ?? []);
      })
      .catch(() => {
        if (!cancelled) setMentionable([]); // "@" just types a literal "@"
      });
    return () => {
      cancelled = true;
    };
  }, []);

  /** Lazily fetch the skill catalog (the "+" Skills flyout + the "/" picker). */
  function ensureSkills() {
    if (skillsFetchedRef.current) return;
    skillsFetchedRef.current = true;
    get<{ skills: SkillOption[] }>("/skills")
      .then((d) => setSkills(d.skills ?? []))
      .catch(() => {
        skillsFetchedRef.current = false; // a later open retries
        setSkills([]);
      });
  }

  // The "+" Connectors flyout: every ESTABLISHED connector (catalog + the
  // user's own MCP servers + memory sources/brains) gets an on/off toggle for
  // this conversation, plus a few not-yet-connected marketplace teasers so the
  // flyout always shows something connectable, never a dead end.
  const [connCatalog, setConnCatalog] = useState<ConnectorEntry[] | null>(null);
  const connCatalogFetchedRef = useRef(false);
  function ensureConnectorCatalog() {
    if (connCatalogFetchedRef.current) return;
    connCatalogFetchedRef.current = true;
    get<{
      connectors: {
        id: string;
        name: string;
        glyph?: string;
        connected?: boolean;
        status?: string;
        connect_via?: string;
        tools_loaded?: number;
      }[];
    }>("/connectors")
      .then((d) =>
        setConnCatalog(
          (d.connectors ?? []).map((c) => ({
            id: c.id,
            name: c.name,
            glyph: c.glyph,
            connected: Boolean(c.connected) || c.status === "connected",
            connect_via: c.connect_via,
            tools_loaded: c.tools_loaded,
          })),
        ),
      )
      .catch(() => {
        connCatalogFetchedRef.current = false; // a later open retries
        setConnCatalog([]);
      });
  }
  const connectedConnectors = useMemo(
    () => (connCatalog ?? []).filter((c) => c.connected),
    [connCatalog],
  );
  const marketplaceTeasers = useMemo(
    () => (connCatalog ?? []).filter((c) => !c.connected).slice(0, 3),
    [connCatalog],
  );

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

  // Close the composer project quick-toggle on any outside click.
  useEffect(() => {
    if (!projMenuOpen) return;
    const onDown = (e: MouseEvent) => {
      if (!projPopRef.current?.contains(e.target as Node)) setProjMenuOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [projMenuOpen]);

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

  /** Toggle a connector for this conversation (the "+" Connectors flyout).
   *  Counts as a thread-setup edit so the choice persists with the thread. */
  function toggleConnector(id: string) {
    setSelectedConnectors((prev) => {
      if (prev.includes(id)) return prev.filter((c) => c !== id);
      if (prev.length >= MAX_CONNECTORS) return prev; // at the cap
      return [...prev, id];
    });
    markSetupChanged();
  }

  /** AUTO TOOLS toggle — a persisted preference, not thread setup. */
  function toggleAutoTools() {
    setAutoTools((prev) => {
      const next = !prev;
      try {
        window.localStorage.setItem(AUTO_TOOLS_KEY, next ? "1" : "0");
      } catch {
        /* ignore */
      }
      return next;
    });
  }

  /** Select a skill from the "/" dropdown: chip on, "/query" text consumed. */
  function pickSkill(name: string) {
    setActiveSkill(name);
    // Splice out ONLY the "/token" being typed and keep the rest of the message
    // (v1.105.0). This was `setInput("")`, which was invisibly correct while
    // "/" could lead a message and nothing else — the token WAS the whole
    // message. The moment "/" is allowed mid-sentence, clearing would eat a
    // prompt the user had already written.
    const tok = slashToken;
    const next = spliceToken(input, tok);
    const pos = tok ? tok.start : 0;
    setInput(next);
    setCaret(pos); // keep the memo's view of the caret consistent THIS render
    setSlashDismissed(false);
    markSetupChanged();
    inputRef.current?.focus();
    // The DOM selection has to be fixed after React commits the new value,
    // otherwise the caret lands at the end of the spliced text and the next
    // thing typed goes to the wrong place.
    requestAnimationFrame(() => {
      const el = inputRef.current;
      if (el) el.selectionStart = el.selectionEnd = pos;
    });
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
      // WHAT ACTUALLY HAPPENED (v1.149.0). The message above is the model's own
      // account of the work; this is the LEDGER's. Fetched best-effort — a
      // result card is never worth losing the reply over — and attached to the
      // message so it survives a reload with the thread.
      let runResult: RunResult | undefined;
      try {
        const r = await get<RunResult>(`/sessions/${id}/result`);
        if (r?.found) runResult = r;
      } catch {
        /* no card; the reply still lands */
      }
      const full: ChatMessage[] = [
        ...messagesRef.current,
        { role: "assistant", content, fromSession: id, ...(runResult ? { runResult } : {}) },
      ];
      setMessages(full);
      // A file an AGENT made deserves the same right-rail preview a chat turn's
      // file gets (v1.155.0). It never appeared before: `documents` was only
      // ever collected in the chat lane, so an escalated run — which is how
      // redaction and most real file work actually happens — produced a file
      // the user was told about in prose and had nowhere to click. The preview
      // also WINS the rail over the project panel, which is the point: a file
      // just created should not be hidden behind whatever the project shows.
      if (runResult?.documents?.length) showDocPreview(runResult.documents);
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

  // ---------------------------------------------------------------- compaction

  /** Re-ask the server which summary stands over the saved thread (v1.169.0).
   *
   *  The chip must never render off the context gauge alone: `compacted` there
   *  is a per-turn report, and the summary's text + stripped claims live only
   *  server-side. `found: false` (or any failure) clears the chip — an
   *  inspect surface that guesses is worse than none.
   */
  async function refreshCompaction(id: string | null) {
    const gen = ++compactionGenRef.current;
    if (!id) {
      setCompaction(null);
      return;
    }
    try {
      const info = await get<CompactionInfo>(`/chat/threads/${id}/compaction`);
      if (compactionGenRef.current === gen) setCompaction(info.found ? info : null);
    } catch {
      if (compactionGenRef.current === gen) setCompaction(null);
    }
  }

  // A turn that arrived compacted (the auto lane past the ceiling) refreshes
  // the standing summary — by then the record exists server-side, keyed over a
  // prefix of what this thread already stored, so the fetch finds it even
  // before the autosave lands.
  useEffect(() => {
    if (contextUsage?.compacted && threadId) void refreshCompaction(threadId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [contextUsage?.compacted, contextUsage?.covers, threadId]);

  /** Compact this conversation because the USER chose to (the suggest band).
   *
   *  Nothing about the thread changes here: the daemon stores the verified
   *  summary against a hash of exactly the messages it covers, and the NEXT
   *  ordinary turn picks it up with no further model call. So there is nothing
   *  to merge into local state — only the gauge to refresh.
   */
  async function compactNow() {
    if (compactBusy) return;
    setCompactBusy(true);
    try {
      const res = await post<{
        covers: number;
        stripped: number;
        stripped_claims?: string[];
        summary?: string;
        provider?: string;
        model?: string;
        trigger?: string;
      }>("/chat/compact", {
        messages: messages.map(({ role, content }) => ({ role, content })),
        ...(splitChoice(choice).provider
          ? { provider: splitChoice(choice).provider }
          : {}),
        ...(splitChoice(choice).model ? { model: splitChoice(choice).model } : {}),
      });
      setContextUsage((u) =>
        u ? { ...u, level: "ok", compacted: true, covers: res.covers } : u,
      );
      // Report the STRIPPED count out loud when there is one. It is the honest
      // half of a model-written summary: those are things the model asserted
      // that the transcript and the execution ledger would not corroborate.
      setCompactNote(
        res.stripped > 0
          ? `Summarized ${res.covers} earlier messages — ${res.stripped} unverifiable claim${
              res.stripped === 1 ? "" : "s"
            } dropped.`
          : `Summarized ${res.covers} earlier messages.`,
      );
      // The inspect chip (v1.169.0): re-read the standing summary from the
      // saved thread; a conversation long enough to compact has autosaved by
      // now, but if this one somehow has no id yet, the POST response itself
      // is the same record — use it rather than showing nothing.
      if (threadId) {
        void refreshCompaction(threadId);
      } else {
        setCompaction({
          found: true,
          summary: res.summary,
          covers: res.covers,
          stripped: res.stripped,
          stripped_claims: res.stripped_claims ?? [],
          provider: res.provider,
          model: res.model,
          trigger: res.trigger,
        });
      }
    } catch (e) {
      setError(
        e instanceof ApiError && e.message
          ? e.message
          : "Could not summarize this conversation.",
      );
    } finally {
      setCompactBusy(false);
    }
  }

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
      // Connector toggles: MCP tool groups armed server-side + memory grounding.
      ...(selectedConnectors.length
        ? { connectors: selectedConnectors.slice(0, MAX_CONNECTORS) }
        : {}),
      // The workspace folder armed file tools operate in (when chosen).
      ...(workspaceDir ? { workspace_dir: workspaceDir } : {}),
      // Context spine: the daemon grounds the reply in this project's
      // instructions + brief + knowledge (ref — sends can fire from timers).
      ...(projectIdRef.current ? { project_id: projectIdRef.current } : {}),
      // Seamless arming: the daemon reads the request and fills the free tool
      // slots from its curated safe set (explicit picks above always first).
      ...(autoTools ? { auto_tools: true } : {}),
    };
  }

  /** The provider that ACTUALLY answered, when the user explicitly picked a
   *  DIFFERENT one (capability reroute / failover) — "" when there is nothing
   *  to disclose (default routing, same provider, or no info). Powers the
   *  honesty chip: a local-model turn silently served by a subscription CLI
   *  must never be invisible. */
  function servedByOther(served?: string): string {
    const requested = splitChoice(choice).provider ?? "";
    if (!served || !requested || served === requested) return "";
    return served;
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

  /** Persist the thread setup with an EXPLICIT documents list. State updates
   *  are async, so the doc-chip saves can't rely on currentSetup() seeing the
   *  new list — and the setupVersion effect skips a not-yet-saved thread,
   *  which would lose chips generated on a conversation's FIRST turn. Riding
   *  the serialized save chain means the turn's own save has already resolved
   *  the thread id (the chain mutates the shared target) by the time this
   *  PUT runs. */
  function queueSaveDocs(docs: string[]) {
    const target = saveTargetRef.current;
    if (target.daemon) return; // messaging threads: server-owned, never PUT
    const personaValue = personaForSend();
    const setup = { ...currentSetup(), documents: docs.slice(-MAX_THREAD_DOCS) };
    sendSetupRef.current = true; // future saves keep carrying the setup
    saveChainRef.current = saveChainRef.current.then(async () => {
      const msgs = messagesRef.current; // read INSIDE the chain — post-turn state
      if (msgs.length === 0) return;
      try {
        const body: ThreadSaveBody = {
          messages: msgs,
          project_id: projectIdRef.current,
          ...(personaValue ? { persona: personaValue } : {}),
          setup,
        };
        const res = await put<ThreadSaveResult>(
          `/chat/threads/${target.id ?? "new"}`,
          body,
        );
        target.id = res.id;
        if (saveTargetRef.current === target) setThreadId(res.id);
      } catch {
        /* autosave is best-effort — never disturb the conversation itself */
      }
    });
  }

  /** Merge files into the thread's remembered list WITHOUT opening a preview
   *  (v1.166.0). The rail lists what the conversation "made or was given" —
   *  an uploaded attachment is "given" and deserves a row the same as a
   *  generated file. Reads/writes threadDocsRef so two merges inside one
   *  turn's stale closure can never drop each other's paths. */
  function rememberThreadDocs(paths: string[]) {
    const docs = paths.filter(Boolean);
    if (docs.length === 0) return;
    const merged = [
      ...threadDocsRef.current.filter((p) => !docs.includes(p)),
      ...docs,
    ].slice(-MAX_THREAD_DOCS);
    threadDocsRef.current = merged;
    setThreadDocs(merged);
    queueSaveDocs(merged);
  }

  /** A turn created/edited documents: REMEMBER them on the thread (the rail
   *  persists and survives restarts until deliberately dismissed) and open the
   *  right rail. ONE file auto-opens its preview, as this panel always has;
   *  SEVERAL open the rail's file list instead (v1.166.0) — auto-opening the
   *  last write would bury the other N−1 behind it. "Don't auto-OPEN" must
   *  not become "tear DOWN": a preview the user already has on screen (e.g.
   *  the file they're watching gets edited alongside a log write) stays put. */
  function showDocPreview(paths?: string[]) {
    const docs = (paths ?? []).filter(Boolean);
    const last = docs.at(-1);
    if (!last) return;
    rememberThreadDocs(docs);
    setPreviewPath((cur) => (docs.length > 1 ? cur : last));
    setWorkspaceOpenPersisted(true);
  }

  /** Reopen a remembered document's preview (the chip's click). */
  function openDocPreview(path: string) {
    setPreviewPath(path);
    setWorkspaceOpenPersisted(true);
  }

  /** Drag the rail's left edge: wider preview when wanted, default when not.
   *  Pointer-captured so the drag survives leaving the grip; the chosen width
   *  persists per device on release. */
  function startRailDrag(e: React.PointerEvent<HTMLDivElement>) {
    e.preventDefault();
    const startX = e.clientX;
    const startW = railW;
    const el = e.currentTarget;
    el.setPointerCapture(e.pointerId);
    const move = (ev: PointerEvent) =>
      setRailW(clampRailW(startW + (startX - ev.clientX)));
    const up = () => {
      el.removeEventListener("pointermove", move);
      el.removeEventListener("pointerup", up);
      setRailW((w) => {
        try {
          window.localStorage.setItem(RAIL_W_KEY, String(w));
        } catch {
          /* private mode — the width still applies this session */
        }
        return w;
      });
    };
    el.addEventListener("pointermove", move);
    el.addEventListener("pointerup", up);
  }

  function resetRailW() {
    setRailW(RAIL_DEFAULT_W);
    try {
      window.localStorage.setItem(RAIL_W_KEY, String(RAIL_DEFAULT_W));
    } catch {
      /* ignore */
    }
  }

  /** Deliberately dismiss a remembered document (the chip's ×): forget it on
   *  the thread and close its panel if it is the one showing. */
  function dismissThreadDoc(path: string) {
    const next = threadDocsRef.current.filter((p) => p !== path);
    threadDocsRef.current = next;
    setThreadDocs(next);
    setPreviewPath((cur) => (cur === path ? null : cur));
    queueSaveDocs(next);
  }

  // ---- UNDO WHERE YOU LOOK (v1.168.0) --------------------------------------

  /** Refresh chat's live undo candidates. Failure is a quiet degrade — the
   *  rail simply offers no undo; chat itself must never block on this. */
  async function refreshUndoRows() {
    try {
      const res = await get<{ actions: UndoRowLike[] }>("/undo?session_id=chat");
      setUndoRows(res.actions ?? []);
    } catch {
      /* offline / older daemon — no undo affordances, nothing broken */
    }
  }

  // Fetch on mount and again whenever the thread's document list changes —
  // every file-writing turn merges into threadDocs, so this is exactly "a new
  // journal row may exist".
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await get<{ actions: UndoRowLike[] }>(
          "/undo?session_id=chat",
        );
        if (!cancelled) setUndoRows(res.actions ?? []);
      } catch {
        /* offline / older daemon — no undo affordances, nothing broken */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [threadDocs]);

  // UNDO PERFORMED ELSEWHERE (v1.168.0 fix): an undo run on another surface
  // (Timeline page, a second window) publishes action.reverted on /events —
  // without reacting, this page keeps offering a live "Undo this write" whose
  // POST can only 409. New frames grey the affected row immediately (the same
  // "already undone" state a local undo leaves — vanishing would read as
  // "never undoable") and refetch the candidate list, which also re-joins a
  // since-re-edited file to its NEWEST journal row. Seen-boundary is an event
  // id (the commEventSeenRef pattern) so re-renders never re-process frames.
  const undoEventSeenRef = useRef<string | null>(null);
  useEffect(() => {
    const newest = events[0];
    if (!newest) return;
    const boundary = undoEventSeenRef.current;
    undoEventSeenRef.current = newest.id;
    const ids = revertedActionIds(events, boundary);
    if (ids.length === 0) return;
    const hit = new Set(ids);
    setUndoneIds((prev) => {
      const next = new Set(prev);
      for (const id of ids) next.add(id);
      return next;
    });
    // Stash the greyed rows BEFORE the refetch drops them from the live list
    // (the route only lists not-yet-undone candidates).
    setUndoneRows((prev) => [
      ...prev,
      ...undoRows.filter(
        (r) =>
          hit.has(r.action_id) &&
          !prev.some((p) => p.action_id === r.action_id),
      ),
    ]);
    void refreshUndoRows();
    // On a rerun from the undoRows dep, boundary === newest.id, so the scan
    // stops immediately — no double processing.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [events, undoRows]);

  // Live rows first (newest write per path wins), stashed undone rows behind
  // them so an undone file keeps its greyed affordance until a NEWER write to
  // the same path takes the slot back.
  const undoByPath = useMemo(
    () => joinUndoByPath([...undoRows, ...undoneRows]),
    [undoRows, undoneRows],
  );

  /** Journal match for one absolute path — the rail's/receipt's `undoFor`.
   *  null = no row could be matched by path, so NO affordance is offered. */
  function undoForPath(path: string) {
    const row = undoByPath.get(normalizeFsPath(path));
    if (!row) return null;
    if (undoneIds.has(row.action_id))
      return {
        actionId: row.action_id,
        undoable: false,
        reason: "already undone",
        kind: row.kind,
      };
    return {
      actionId: row.action_id,
      undoable: row.undoable !== false,
      reason:
        row.undoable === false
          ? "this action has no safe inverse"
          : undefined,
      kind: row.kind,
    };
  }

  /** The one undo implementation both call sites share (rail row + receipt
   *  file chip): explicit confirm (window.confirm — the DocPreview
   *  convention), POST /undo/{id}, mark the row undone, refresh an open
   *  preview and the candidate list. THROWS on failure so each call site
   *  surfaces the server's error where the user clicked. */
  async function undoWrite(actionId: string, path: string) {
    const row = undoByPath.get(normalizeFsPath(path));
    const base = path.split(/[\\/]/).filter(Boolean).pop() ?? path;
    if (!window.confirm(confirmUndoPrompt(row?.kind, base))) return;
    await post(`/undo/${encodeURIComponent(actionId)}`, {});
    setUndoneIds((prev) => {
      const next = new Set(prev);
      next.add(actionId);
      return next;
    });
    if (row)
      setUndoneRows((prev) =>
        prev.some((r) => r.action_id === actionId) ? prev : [...prev, row],
      );
    // An open preview of the reverted file must show the reverted truth — the
    // nonce keys the DocPreview, so bumping it remounts + refetches. (A
    // preview of an unrelated file just refetches its own unchanged data.)
    setPreviewNonce((n) => n + 1);
    void refreshUndoRows();
  }

  // ---- PROMOTE TO KNOWLEDGE (v1.168.0) -------------------------------------

  /** Add an assistant reply to the bound project's knowledge as a note —
   *  the EXISTING knowledge path (the server names it from the first line).
   *  Throws when no project is bound / on server error; the button surfaces
   *  it. */
  async function promoteNoteToKnowledge(content: string) {
    const pid = projectIdRef.current;
    if (!pid) throw new Error("bind this chat to a project first");
    await post(`/projects/${encodeURIComponent(pid)}/knowledge`, {
      text: content,
    });
  }

  /** Add a produced FILE to the bound project's knowledge: fetch its bytes
   *  off the daemon (the same /documents/file the preview/download use) and
   *  post them through the existing knowledge upload path, which extracts the
   *  text server-side by filename. */
  async function promoteFileToKnowledge(path: string) {
    const pid = projectIdRef.current;
    if (!pid) throw new Error("bind this chat to a project first");
    const tok = ijToken();
    const url = `${API_BASE}/documents/file?path=${encodeURIComponent(path)}${
      tok ? `&token=${encodeURIComponent(tok)}` : ""
    }`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`could not read the file (HTTP ${res.status})`);
    const bytes = new Uint8Array(await res.arrayBuffer());
    let bin = "";
    const CHUNK = 0x8000; // spread in chunks — one call per byte is quadratic,
    for (let i = 0; i < bytes.length; i += CHUNK)
      bin += String.fromCharCode(...bytes.subarray(i, i + CHUNK));
    const base = path.split(/[\\/]/).filter(Boolean).pop() ?? path;
    await post(`/projects/${encodeURIComponent(pid)}/knowledge`, {
      content_b64: btoa(bin),
      filename: base,
      name: base,
    });
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
    // Files the user GAVE this turn join the rail up front (v1.166.0): "made
    // or was given" — an upload the user has to re-find on disk defeats the
    // rail, and the file is uploaded and in the committed bubble even if the
    // completion later fails. Persists via the same setup save made-docs use.
    if (atts.length) rememberThreadDocs(atts.map((a) => a.path));
    const body = buildChatBody(history, atts);
    try {
      // --- Attempt token streaming (live deltas + tool cards + voice) ---
      try {
        const {
          reply,
          tools_used,
          deniedTools,
          route,
          provider: servedBy,
          documents: madeDocs,
          escalate,
          escalateReason,
          escalateAgent,
          workflowDraft,
          context: ctxUsage,
        } = await stream.run(body, (_delta, full) => feedTTS(full, false));
        if (chatGenRef.current !== gen) return; // torn down mid-stream
        if (ctxUsage) setContextUsage(ctxUsage);
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
        const via = servedByOther(servedBy);
        // Accountability fields (v1.165.0): stored PER MESSAGE so the receipt
        // under each reply keeps telling the truth after restarts, not just on
        // the turn it streamed in.
        const receipt = {
          ...(route ? { route } : {}),
          ...(deniedTools?.length ? { deniedTools } : {}),
          ...(madeDocs?.length ? { documents: madeDocs } : {}),
        };
        const full: ChatMessage[] = [
          ...history,
          {
            role: "assistant",
            content: finalReply,
            ...(toolsUsed.length ? { toolsUsed } : {}),
            ...(sources.length ? { sources } : {}),
            ...(via ? { viaProvider: via } : {}),
            ...receipt,
          },
        ];
        // The turn crystallized into a workflow proposal (v1.120.0): commit
        // the card instead of prose. Checked before escalate — a validated
        // draft must not be discarded by a stray escalate in the same reply.
        if (workflowDraft) {
          const done: ChatMessage[] = [
            ...history,
            {
              role: "assistant",
              content:
                (reply ?? "").trim() || "Here's that as a reusable workflow:",
              workflowDraft,
              // Earlier rounds of THIS turn may have run tools — their
              // provenance must survive the draft exit like any other turn.
              ...(toolsUsed.length ? { toolsUsed } : {}),
              ...(sources.length ? { sources } : {}),
              ...(via ? { viaProvider: via } : {}),
              ...receipt,
            },
          ];
          setMessages(done);
          queueSave(done);
          showDocPreview(madeDocs); // docs written before the exit still count
          return;
        }
        // ONE SURFACE (v1.108.0): the turn decided it needs the full agent, so
        // re-run the SAME message as a session instead of handing the user a
        // reply that tells them to go flip a switch. The user's bubble and the
        // attachment chips are already committed, hence escalatedFrom.
        if (escalate) {
          const lastUser = [...history].reverse().find((m) => m.role === "user");
          setMessages(history);
          void sendAgent(lastUser?.content ?? "", {
            escalatedFrom: {
              atts,
              reason: escalateReason || "this one needs the full agent",
            },
            // The turn's own validated roster pick (v1.139.0); null/absent
            // keeps the builder default.
            ...(escalateAgent ? { agentType: escalateAgent } : {}),
          });
          return;
        }
        setMessages(full);
        queueSave(full); // the turn is complete — persist it
        showDocPreview(madeDocs); // a generated doc appears beside the chat
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
      if (res.context) setContextUsage(res.context);
      const toolsUsed = (res.tools_used ?? []).filter((t) => Boolean(t));
      const reply = (res.reply ?? "").trim() || "(no response)";
      const viaPost = servedByOther(res.provider);
      // Accountability fields (v1.165.0) — the POST lane's copy of the stream
      // lane's `receipt`. MIRROR NOTE: keep in step with the stream path above.
      const deniedPost = (res.denied_tools ?? []).filter(Boolean);
      const receiptPost = {
        ...(res.route ? { route: res.route } : {}),
        ...(deniedPost.length ? { deniedTools: deniedPost } : {}),
        ...(res.documents?.length ? { documents: res.documents } : {}),
      };
      const full: ChatMessage[] = [
        ...history,
        {
          role: "assistant",
          content: reply,
          ...(toolsUsed.length ? { toolsUsed } : {}),
          ...(viaPost ? { viaProvider: viaPost } : {}),
          ...receiptPost,
        },
      ];
      if (res.workflow_draft) {
        // NOT the pre-defaulted `reply` ("(no response)" is truthy) — the raw
        // wire value decides whether the caption fallback fires.
        const caption =
          (res.reply ?? "").trim() || "Here's that as a reusable workflow:";
        const done: ChatMessage[] = [
          ...history,
          {
            role: "assistant",
            content: caption,
            workflowDraft: res.workflow_draft,
            ...(toolsUsed.length ? { toolsUsed } : {}),
            ...(viaPost ? { viaProvider: viaPost } : {}),
          },
        ];
        setMessages(done);
        queueSave(done);
        if (!ttsStreamStartedRef.current) tts.speak(caption);
        showDocPreview(res.documents);
        return;
      }
      if (res.escalate) {
        const lastUser = [...history].reverse().find((m) => m.role === "user");
        setMessages(history);
        void sendAgent(lastUser?.content ?? "", {
          escalatedFrom: {
            atts,
            reason: res.escalate_reason || "this one needs the full agent",
          },
          // The turn's own validated roster pick (v1.139.0); null/absent
          // keeps the builder default.
          ...(res.escalate_agent ? { agentType: res.escalate_agent } : {}),
        });
        return;
      }
      setMessages(full);
      // Nothing streamed on this path (endpoint absent), so this is the first and
      // only speak — no risk of re-voicing sentences speakMore already spoke.
      if (!ttsStreamStartedRef.current) tts.speak(reply);
      queueSave(full); // the turn is complete — persist it
      showDocPreview(res.documents); // a generated doc appears beside the chat
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

  /** MESSAGING (daemon-owned) thread reply: the desktop composer posts through
   *  POST /comm/threads/{id}/send — the daemon persists BOTH sides itself and
   *  also delivers the reply out to the phone, so this path never queueSaves.
   *  The optimistic render is reconciled against the server's stored truth
   *  (an immediate refetch, plus every chat.thread_updated event). */
  async function sendComm(message: string) {
    const gen = chatGenRef.current;
    const id = saveTargetRef.current.id;
    if (!id) {
      sendingRef.current = false;
      return;
    }
    const before = messagesRef.current;
    const history: ChatMessage[] = [
      ...before,
      { role: "user", content: message },
    ];
    setMessages(history); // optimistic — the daemon persists it server-side
    pinnedRef.current = true;
    setShowJump(false);
    setFailedTurn(null);
    setChatBusy(true);
    try {
      const res = await post<ChatResponse & { sent?: boolean }>(
        `/comm/threads/${encodeURIComponent(id)}/send`,
        { text: message },
      );
      if (chatGenRef.current !== gen) return; // conversation moved on
      // The turn ran and persisted, but the outbound copy never reached the
      // phone — say so instead of letting the banner's promise quietly break.
      if (res.sent === false)
        setError(
          "Saved to the conversation, but delivery to your phone failed — check the destination on the Notifications page.",
        );
      const toolsUsed = (res.tools_used ?? []).filter((t) => Boolean(t));
      const reply = (res.reply ?? "").trim() || "(no response)";
      if (res.escalate) {
        // The daemon runs the escalated session SERVER-SIDE (it has to reply
        // to the phone too) — never spawn the browser's own agent path here.
        // The finished answer lands via chat.thread_updated; until then the
        // hand-off note keeps the wait honest.
        setMessages([
          ...history,
          {
            role: "assistant",
            content: "",
            escalated:
              res.escalate_reason ||
              "working on it — the full reply will land here and on your phone",
            // NO escalatedTo here: POST /comm/threads/{id}/send spawns its
            // long-standing supervisor default (routes/comm.py) and does NOT
            // read escalate_agent — naming the turn's pick would put a
            // specialist's name on a session the supervisor actually runs.
          },
        ]);
        return;
      }
      setMessages([
        ...history,
        {
          role: "assistant",
          content: reply,
          ...(toolsUsed.length ? { toolsUsed } : {}),
        },
      ]);
      tts.speak(reply); // no-op unless spoken replies are on
      // Reconcile with the daemon's stored truth right away (it has already
      // persisted both sides) — via GET, never a PUT.
      void refetchCommThread();
    } catch (e) {
      if (chatGenRef.current !== gen) return;
      // The send never reached the phone lane: roll the optimistic bubble back
      // (the daemon stored nothing) and hand the text back to the composer.
      setMessages(before);
      if (e instanceof ApiError && e.status === 0) setOffline(true);
      else setError(e instanceof ApiError ? e.message : String(e));
      inputFromVoiceRef.current = false; // programmatic restore, never voice
      setInput((cur) => (cur.trim() ? cur : message));
    } finally {
      sendingRef.current = false;
      if (chatGenRef.current === gen) {
        setChatBusy(false);
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
    if (busy) return;
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
  async function sendAgent(
    message: string,
    opts: {
      escalatedFrom?: { atts: UploadedFile[]; reason: string };
      /** Roster target the escalating turn chose (v1.139.0): a builtin name
       *  rides as the opening session's agent_type, "custom:<slug>" opens via
       *  POST /agents/{slug}/spawn, "remote:<name>" degrades to the default
       *  (no session-shaped run exists for remotes). Absent → "builder",
       *  exactly today's behavior. */
      agentType?: string;
    } = {},
  ) {
    // ESCALATION (v1.108.0): chat already appended the user's bubble and
    // already consumed the attachment chips, so re-doing either would show the
    // message twice and drop the files. The turn is being RE-RUN, not restarted.
    const esc = opts.escalatedFrom;
    const atts = esc ? esc.atts : attachments;
    if (!esc) setAttachments([]); // chips are consumed by this message
    // A recap of the chat so far — prepended ONLY when opening a fresh session
    // below (switching to Agent mode drops all context otherwise). Captured
    // before the new user bubble is appended.
    const recap = conversationRecap(messages);
    // Match the kanban precedent: point the agent at the uploaded files in-text.
    const attachLines = atts.map((a) => `\n\nAttached file: ${a.path}`).join("");
    // A "/"-picked skill (v1.104.0). Chat mode sends `skill` on the body and
    // the daemon injects the playbook; SessionCreate has no such field, so an
    // agent is NAMED the skill and loads it with the skill_load tool it
    // already carries — the split CLAUDE.md describes ("skills inject into
    // prompts, the agent-facing tools are just search/load"). Directing rather
    // than inlining also keeps the opening task short when the playbook is long.
    const skillLine = activeSkill
      ? `Use the "${activeSkill}" skill for this — load it with skill_load first.\n\n`
      : "";
    const task = skillLine + message + attachLines;
    // A NON-default roster pick only applies when this turn OPENS the session
    // — `continue` stays on the existing session's agent, so naming a
    // specialist there would be a lie the transcript can't cash. And the pick
    // must be one this page can actually honor: POST /sessions understands
    // builtin types ONLY (an unknown agent_type SILENTLY coerces to builder —
    // daemon/app.py _agent_type), a dynamic "custom:<slug>" runs through
    // POST /agents/{slug}/spawn (the same stored-definition + runtime path
    // the daemon itself uses for its own escalations), and a "remote:<name>"
    // has no session-shaped run at all — it stays on the unnamed builder
    // default, the same call the daemon's comm escalation makes for remotes.
    const picked = (!sessionId && opts.agentType) || "";
    // Slug stripped from the CANONICAL name (roster.py's NAME CONTRACT: the
    // remainder after ":" is the registry key, original casing preserved).
    const customSlug = picked.startsWith("custom:")
      ? picked.slice("custom:".length)
      : "";
    const builtinPick =
      picked && !picked.includes(":") && picked !== "builder" ? picked : "";
    // The hand-off bubble names ONLY what will truly run.
    const target = customSlug ? picked : builtinPick || null;
    setMessages((prev) =>
      esc
        ? // The bubble is already there — mark WHY the turn grew instead, so the
          // hand-off is visible rather than an unexplained pause. When a
          // specialist was chosen, name it (v1.139.0).
          [
            ...prev,
            {
              role: "assistant" as const,
              content: "",
              escalated: esc.reason,
              ...(target ? { escalatedTo: agentPhrase(target) } : {}),
            },
          ]
        : [
            ...prev,
            {
              role: "user" as const,
              content: message,
              ...(atts.length ? { attachmentNames: atts.map((a) => a.name) } : {}),
            },
          ],
    );
    // Files given to an AGENT turn land on the rail too (v1.166.0) — same
    // "made or was given" rule as chat mode. Escalations skip it: their
    // attachments were already merged by the chat lane that escalated.
    if (!esc && atts.length) rememberThreadDocs(atts.map((a) => a.path));
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
        session = customSlug
          ? // The escalating turn picked one of YOUR agents (v1.139.0): spawn
            // its stored definition — prompt, tool allowlist, and its OWN
            // pinned provider/model, which is why the model picker and the
            // project tag deliberately don't ride along here. Returns a flat
            // SessionView (wait:false parity with POST /sessions), so the
            // chaining below is identical.
            await post<SessionView>(
              `/agents/${encodeURIComponent(customSlug)}/spawn`,
              { task: openingTask, wait: false },
            )
          : await post<SessionView>("/sessions", {
              task: openingTask,
              // The escalating turn's builtin pick (already roster-validated
              // by the daemon); absent keeps the builder default (v1.139.0).
              agent_type: builtinPick || "builder",
              wait: false,
              ...(provider ? { provider } : {}),
              ...(model ? { model } : {}),
              // Context spine: the run lands in the selected project (continues
              // inherit it server-side, so only the opener needs the tag).
              ...(projectIdRef.current
                ? { project_id: projectIdRef.current }
                : {}),
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
    // MESSAGING threads take plain text only — refuse honestly instead of
    // silently dropping the files (the composer keeps both text and chips).
    if (commMetaRef.current && attachmentsRef.current.length > 0) {
      setError(
        "Attachments can't be sent to a messaging thread yet — remove them, or start a new chat.",
      );
      return;
    }
    sendingRef.current = true;
    // A new turn always follows: re-pin so the user's own message + the reply
    // scroll into view even if they'd scrolled up to re-read earlier context.
    pinnedRef.current = true;
    setShowJump(false);
    setError(null);
    setOffline(false);
    setInput("");
    // MESSAGING thread (owner === "daemon"): the reply goes out the comm lane
    // — the daemon runs the turn, stores it, and mirrors it to the phone.
    if (commMetaRef.current) {
      void sendComm(message);
      return;
    }
    // @MENTION (v1.150.0): naming agents routes the turn to THEM, not to Iron
    // Jarvis — "@builder @critic draft this" asks those two, in order, each
    // seeing the previous one's answer. Only fires when a mention resolves to a
    // real agent, so "@ 9am" or an email address is an ordinary message.
    if (liveMentions.length > 0) {
      void sendPanel(message);
      return;
    }
    // One entry point (v1.108.0). Every message starts as fast chat; the turn
    // escalates itself when it needs the full agent (see completeChat), so the
    // user never routes their own request.
    void sendChat(message);
  }

  /**
   * Send an @-mentioned message to the agent panel (v1.150.0).
   *
   * The panel is an ordinary agent thread bound to this chat thread, so the
   * inter-agent conversation shows up on the Agents page for free — and turn 3
   * can mention someone new who then sees what was already said.
   */
  async function sendPanel(message: string) {
    setChatBusy(true);
    const history: ChatMessage[] = [
      ...messagesRef.current,
      { role: "user", content: message },
    ];
    setMessages(history);
    try {
      const res = await post<{
        thread_id: string;
        entries: PanelEntry[];
        spoke: string[];
        skipped: string[];
        unknown_mentions: string[];
      }>("/chat/panel", {
        message,
        ...(threadId ? { chat_thread_id: threadId } : {}),
      });
      // The user's own turn is already in `history`; keep only the agents'.
      const replies = (res.entries ?? []).filter((e) => e.who && e.who !== "user");
      const full: ChatMessage[] = [
        ...history,
        ...replies.map((e) => ({
          role: "assistant" as const,
          content: e.content || "(no reply)",
          panelWho: e.who,
          panelThreadId: res.thread_id,
          ...(e.error ? { panelError: true } : {}),
        })),
      ];
      setMessages(full);
      queueSave(full);
      if (res.unknown_mentions?.length) {
        setError(
          `No agent matched ${res.unknown_mentions
            .map((u) => "@" + u)
            .join(", ")} — check the Agents page.`,
        );
      }
    } catch (e) {
      const err = e instanceof ApiError ? e : new ApiError(String(e), 0);
      setError(err.status === 0 ? "Daemon offline — the panel didn't run." : err.message);
      setInput(message); // never lose the typed message
      setMessages(messagesRef.current.slice(0, -1));
    } finally {
      setChatBusy(false);
      sendingRef.current = false;
    }
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
    setSelectedConnectors([]); // connector toggles are per-conversation
    setPreviewPath(null); // a fresh conversation starts without a preview
    setThreadDocs([]); // document chips belong to their conversation
    // The standing summary belongs to its thread — and clearing it must ALSO
    // invalidate any in-flight fetch (refreshCompaction bumps the gen before
    // clearing), or a GET racing this New Chat resolves late, passes the gen
    // guard, and pins the OLD thread's summary onto a fresh conversation.
    void refreshCompaction(null);
    setCompactionOpen(false);
    sendSetupRef.current = false; // fresh conversation — nothing armed to persist
    setToolsOpen(false);
    setToolQuery("");
    setActiveSkill("");
    setSlashDismissed(false);
    setInput("");
    setError(null);
    setOffline(false);
    setThreadId(null);
    setCommMeta(null); // a fresh conversation is browser-owned again
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
          subtitle="Talk to Iron Jarvis. Ask anything — quick answers come straight back, and work that needs files, tools or several steps just gets done."
          actions={
            <div className="flex flex-wrap items-center gap-2">
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
              {(
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
          Answers come back in seconds; when something needs real work — files,
          tools, several steps — it takes that on by itself. Attach files or
          drop them anywhere on the page.
        </p>
      </Reveal>

      {personaEditorOpen && (
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
                          {renamingId === t.id ? (
                            <input
                              autoFocus
                              value={renameDraft}
                              onChange={(e) => setRenameDraft(e.target.value)}
                              onKeyDown={(e) => {
                                if (e.key === "Enter") {
                                  e.preventDefault();
                                  void renameThread(t.id, renameDraft);
                                } else if (e.key === "Escape") {
                                  setRenamingId(null);
                                }
                              }}
                              onBlur={() => void renameThread(t.id, renameDraft)}
                              aria-label="Rename chat"
                              className="field mx-1.5 my-1.5 w-[calc(100%-0.75rem)] py-1 text-[13px]"
                            />
                          ) : (
                          <button
                            type="button"
                            onClick={() => void openThread(t.id)}
                            className="w-full px-2.5 py-2 pr-9 text-left"
                            title={t.title || "Untitled chat"}
                          >
                            <span
                              className={`flex items-center gap-1.5 text-[13px] ${
                                active ? "text-accent-soft" : "text-zinc-200"
                              }`}
                            >
                              {pinnedIds.includes(t.id) && (
                                <Pin size={11} className="shrink-0 text-accent-soft/80" />
                              )}
                              <span className="min-w-0 truncate">
                                {t.title || "Untitled chat"}
                              </span>
                              {/* Origin chip: a MESSAGING thread names where it
                                  comes from (the stronger signal, so it wins
                                  the slot); otherwise, in the unscoped view,
                                  project threads carry the project chip. */}
                              {t.owner === "daemon" ? (
                                <span
                                  className="ml-auto max-w-[5.5rem] shrink-0 truncate rounded-full border border-accent/25 bg-accent/[0.08] px-1.5 text-[9px] uppercase tracking-wide text-accent-soft"
                                  title="Messaging thread"
                                >
                                  {t.comm_channel || "linked"}
                                </span>
                              ) : !projectId && t.project_id ? (
                                <span
                                  className="ml-auto max-w-[5.5rem] shrink-0 truncate rounded-full border border-white/10 bg-white/[0.04] px-1.5 text-[9px] uppercase tracking-wide text-zinc-500"
                                  title="Project thread"
                                >
                                  {projects.find((p) => p.id === t.project_id)?.name ??
                                    "project"}
                                </span>
                              ) : null}
                            </span>
                            <span className="block text-[11px] text-zinc-500">
                              {timeAgo(t.updated_at)} · {count} msg
                              {count === 1 ? "" : "s"}
                            </span>
                          </button>
                          )}
                          {renamingId !== t.id && (
                            <span
                              className={`absolute right-1.5 top-1/2 -translate-y-1/2 transition-opacity focus-within:opacity-100 group-hover/thread:opacity-100 ${
                                threadMenu?.id === t.id ? "opacity-100" : "opacity-0"
                              }`}
                            >
                              <button
                                type="button"
                                onClick={(e) => openThreadMenu(e, t.id)}
                                aria-label={`Options for ${t.title || "chat"}`}
                                aria-haspopup="menu"
                                aria-expanded={threadMenu?.id === t.id}
                                title="Chat options"
                                className={`grid h-6 w-6 place-items-center rounded-md transition-colors hover:bg-white/[0.06] ${
                                  threadMenu?.id === t.id
                                    ? "bg-white/[0.06] text-zinc-200"
                                    : "text-zinc-500 hover:text-zinc-200"
                                }`}
                              >
                                <MoreHorizontal size={14} />
                              </button>
                            </span>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            </Card>
          </aside>

          {/* ⋯ thread menu popout (v1.114.0) — portaled to <body> so neither
              the Card's overflow-hidden nor a themed backdrop-filter can clip
              or reposition it. One menu at a time; every action either closes
              it or (memory) shows its progress inline. */}
          {typeof document !== "undefined" &&
            createPortal(
              <AnimatePresence>
                {threadMenu &&
                  (() => {
                    const mt = threads.find((x) => x.id === threadMenu.id);
                    if (!mt) return null;
                    const pinned = pinnedIds.includes(mt.id);
                    const item =
                      "flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-[12.5px] text-zinc-200 transition-colors hover:bg-white/[0.06]";
                    return (
                      <motion.div
                        ref={threadMenuRef}
                        role="menu"
                        aria-label={`Options for ${mt.title || "chat"}`}
                        initial={{ opacity: 0, scale: 0.96, y: threadMenu.up ? 4 : -4 }}
                        animate={{ opacity: 1, scale: 1, y: 0 }}
                        exit={{ opacity: 0, scale: 0.98 }}
                        transition={{ duration: 0.12, ease: "easeOut" }}
                        style={{
                          position: "fixed",
                          left: Math.max(8, threadMenu.x - 224),
                          ...(threadMenu.up
                            ? { bottom: window.innerHeight - threadMenu.y }
                            : { top: threadMenu.y }),
                          transformOrigin: threadMenu.up ? "bottom right" : "top right",
                        }}
                        className="z-50 w-56 rounded-xl border border-white/10 bg-zinc-900 p-1 shadow-lg shadow-black/40"
                      >
                        <button
                          type="button"
                          role="menuitem"
                          className={item}
                          onClick={() => {
                            setRenameDraft(mt.title || "");
                            setRenamingId(mt.id);
                            setThreadMenu(null);
                          }}
                        >
                          <Pencil size={14} className="shrink-0 text-zinc-400" />
                          Rename
                        </button>
                        <button
                          type="button"
                          role="menuitem"
                          className={item}
                          onClick={() => {
                            togglePin(mt.id);
                            setThreadMenu(null);
                          }}
                        >
                          {pinned ? (
                            <PinOff size={14} className="shrink-0 text-zinc-400" />
                          ) : (
                            <Pin size={14} className="shrink-0 text-zinc-400" />
                          )}
                          {pinned ? "Unpin" : "Pin to top"}
                        </button>
                        {/* Memory keeps the menu OPEN: the spinner→check that
                            used to live on the row icon now lives here, and
                            closing instantly would hide the only feedback. */}
                        <button
                          type="button"
                          role="menuitem"
                          className={item}
                          disabled={rememberingId !== null}
                          onClick={() => void rememberThread(mt.id)}
                        >
                          {rememberingId === mt.id ? (
                            <Loader2 size={14} className="shrink-0 animate-spin text-accent-soft" />
                          ) : rememberedId === mt.id ? (
                            <Check size={14} className="shrink-0 text-emerald-300" />
                          ) : (
                            <Brain size={14} className="shrink-0 text-zinc-400" />
                          )}
                          {rememberedId === mt.id ? "Saved to memory" : "Commit to memory"}
                        </button>
                        <button
                          type="button"
                          role="menuitem"
                          className={item}
                          disabled={crystallizingId !== null}
                          onClick={() => void crystallizeThread(mt.id)}
                        >
                          {crystallizingId === mt.id ? (
                            <Loader2 size={14} className="shrink-0 animate-spin text-accent-soft" />
                          ) : (
                            <GitBranch size={14} className="shrink-0 text-zinc-400" />
                          )}
                          Turn into workflow
                        </button>
                        <button
                          type="button"
                          role="menuitem"
                          aria-expanded={threadMenuProjects}
                          className={item}
                          onClick={() => setThreadMenuProjects((v) => !v)}
                        >
                          <FolderKanban size={14} className="shrink-0 text-zinc-400" />
                          Add to project
                          <ChevronRight
                            size={13}
                            className={`ml-auto shrink-0 text-zinc-500 transition-transform ${
                              threadMenuProjects ? "rotate-90" : ""
                            }`}
                          />
                        </button>
                        <AnimatePresence initial={false}>
                          {threadMenuProjects && (
                            <motion.div
                              initial={{ height: 0, opacity: 0 }}
                              animate={{ height: "auto", opacity: 1 }}
                              exit={{ height: 0, opacity: 0 }}
                              transition={{ duration: 0.14, ease: "easeOut" }}
                              className="overflow-hidden"
                            >
                              <div className="max-h-44 overflow-y-auto pl-4">
                                {projects.length === 0 ? (
                                  <p className="px-2.5 py-2 text-[11.5px] text-zinc-500">
                                    No projects yet — create one from the Project
                                    button above the chat.
                                  </p>
                                ) : (
                                  <>
                                    {projects.map((pr) => (
                                      <button
                                        key={pr.id}
                                        type="button"
                                        role="menuitem"
                                        className={item}
                                        disabled={assigningThread}
                                        onClick={() =>
                                          void assignThreadProject(mt.id, pr.id)
                                        }
                                      >
                                        <span className="min-w-0 flex-1 truncate">
                                          {pr.name}
                                        </span>
                                        {mt.project_id === pr.id && (
                                          <Check
                                            size={13}
                                            className="shrink-0 text-accent-soft"
                                          />
                                        )}
                                      </button>
                                    ))}
                                    {mt.project_id && (
                                      <button
                                        type="button"
                                        role="menuitem"
                                        className={`${item} text-zinc-400`}
                                        disabled={assigningThread}
                                        onClick={() =>
                                          void assignThreadProject(mt.id, null)
                                        }
                                      >
                                        <X size={13} className="shrink-0" />
                                        Remove from project
                                      </button>
                                    )}
                                  </>
                                )}
                              </div>
                            </motion.div>
                          )}
                        </AnimatePresence>
                        <div className="my-1 h-px bg-white/[0.06]" />
                        <button
                          type="button"
                          role="menuitem"
                          className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-[12.5px] text-rose-300 transition-colors hover:bg-rose-500/10"
                          onClick={() => {
                            void removeThread(mt.id);
                            setThreadMenu(null);
                          }}
                        >
                          <Trash2 size={14} className="shrink-0" />
                          Delete chat
                        </button>
                      </motion.div>
                    );
                  })()}
              </AnimatePresence>,
              document.body,
            )}

          {/* Conversation pane */}
          <div className="min-w-0 flex-1">
            {/* Project surface strip — the old project screen's tabs, inside
                the chat module. Chat stays mounted (hidden) so the thread and
                composer state survive a Tasks/Board detour untouched. */}
            {activeProject && (
              <div className="mb-3 flex flex-wrap items-center gap-1">
                {(["chat", "tasks", "board", "media"] as const).map((v) => (
                  <button
                    key={v}
                    type="button"
                    onClick={() => setProjectView(v)}
                    className={`rounded-lg border px-2.5 py-1 text-[12px] capitalize transition-colors ${
                      projectView === v
                        ? "border-accent/40 bg-accent/[0.1] text-accent-soft"
                        : "border-white/10 text-zinc-400 hover:text-zinc-200"
                    }`}
                  >
                    {v}
                  </button>
                ))}
              </div>
            )}
            {activeProject && projectView !== "chat" && (
              <ProjectSurface
                projectId={activeProject.id}
                hasRoot={Boolean(activeProject.root) && activeProject.root_exists !== false}
                view={projectView}
              />
            )}
            <Card
              pad={false}
              className={`relative overflow-hidden transition-shadow ${
                activeProject && projectView !== "chat" ? "hidden" : ""
              }`}
            >
              {/* Drop affordance (v1.104.0). A 2px accent ring on the card edge
                  was the whole signal before, which read as "this card is
                  focused" rather than "let go and I'll take that file". The
                  dashed inset border is the convention every file-drop surface
                  uses, and stating what happens on release removes the guess.
                  pointer-events-none is load-bearing: the drop itself is
                  handled by window listeners, so an overlay that swallowed
                  pointer events would break the very gesture it advertises. */}
              {dragging && (
                <div
                  aria-hidden
                  className="pointer-events-none absolute inset-0 z-30 rounded-[inherit] bg-zinc-950/90 p-2 backdrop-blur-[3px]"
                >
                  <div className="flex h-full w-full flex-col items-center justify-center gap-1.5 rounded-xl border-2 border-dashed border-accent/70 bg-accent/[0.06]">
                    <Paperclip size={20} className="text-accent-soft" />
                    <p className="text-sm font-medium text-accent-soft">
                      Drop to attach
                    </p>
                    <p className="text-xs text-zinc-400">
                      Files are uploaded and grounded into this chat
                    </p>
                  </div>
                </div>
              )}
              {/* MESSAGING thread banner: an open daemon-owned conversation
                  says where it also lives and what a reply here does. */}
              {commMeta && (
                <CommThreadBanner
                  channel={commMeta.channel}
                  display={commMeta.display}
                />
              )}
              {/* Compaction inspect (v1.169.0): a summary is standing in for
                  this thread's older messages — say so where the messages are,
                  and let the user read it (and what was stripped from it as
                  uncorroborated) instead of taking it on faith. Renders only
                  off the server's answer, never the gauge. */}
              <CompactionChip
                info={compaction}
                onView={() => setCompactionOpen(true)}
              />
              {compactionOpen && compaction?.found && (
                <CompactionCard
                  info={compaction}
                  onClose={() => setCompactionOpen(false)}
                />
              )}
              {/* Message thread */}
              <div
                ref={scrollRef}
                onScroll={onThreadScroll}
                className="flex max-h-[60vh] min-h-[24rem] flex-col gap-4 overflow-y-auto p-4 sm:p-5"
              >
                {messages.length === 0 && !busy ? (
                  <div className="flex flex-1 flex-col items-center justify-center gap-4">
                    <Empty icon={<MessageSquare size={28} />}>
                      Start a conversation. Ask a question or describe what you
                      need — quick answers come straight back, and real work
                      just gets done.
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
                      // No regenerate on MESSAGING threads: the daemon owns the
                      // transcript, so a browser-side re-run could never be
                      // saved (and would silently diverge from the phone).
                      const canRegen =
                        !commMeta &&
                        i === messages.length - 1 &&
                        i > 0 &&
                        messages[i - 1].role === "user" &&
                        !busy;
                      // The hand-off (v1.108.0). A turn that grew into a full
                      // agent run has no reply of its own — say WHY out loud,
                      // or the wait reads as the app having stalled.
                      if (m.workflowDraft)
                        return (
                          <div key={i} className="group/msg space-y-2">
                            {m.content && (
                              <Bubble role="assistant">
                                <MemoMarkdown content={m.content} />
                              </Bubble>
                            )}
                            <WorkflowDraftCard draft={m.workflowDraft} events={events} />
                          </div>
                        );
                      // v1.150.0: a panel reply is attributed. Without a name on
                      // it, a three-way conversation is an unreadable wall of
                      // anonymous assistant bubbles.
                      if (m.panelWho)
                        return (
                          <div key={i} className="group/msg space-y-1">
                            <div className="ml-11 flex items-center gap-2">
                              <span
                                className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-medium ${
                                  m.panelError
                                    ? "border-rose-500/30 bg-rose-500/[0.06] text-rose-300"
                                    : "border-accent/25 bg-accent/[0.06] text-accent-soft"
                                }`}
                              >
                                <Bot size={11} />
                                {agentDisplayName(m.panelWho)}
                              </span>
                              {m.panelError && (
                                <span className="text-[11px] text-rose-400/80">
                                  couldn&apos;t answer
                                </span>
                              )}
                              {m.panelThreadId && (
                                <Link
                                  href={`/agents?thread=${m.panelThreadId}`}
                                  className="ml-auto mr-1 text-[11px] text-zinc-500 transition-colors hover:text-accent-soft"
                                >
                                  open in Agents →
                                </Link>
                              )}
                            </div>
                            <Bubble role="assistant">
                              <MemoMarkdown content={m.content} />
                            </Bubble>
                          </div>
                        );
                      // v1.149.0: an agent turn shows the LEDGER's account under
                      // the model's own — files it really wrote, tools that
                      // really ran, errors, and what can still be reverted.
                      if (m.runResult)
                        return (
                          <div key={i} className="group/msg space-y-2">
                            {m.content && (
                              <Bubble role="assistant">
                                <MemoMarkdown content={m.content} />
                              </Bubble>
                            )}
                            <RunResultCard
                              result={m.runResult}
                              onRetry={() => {
                                const task = m.runResult?.task || "";
                                if (task) setInput(task);
                                inputRef.current?.focus();
                              }}
                            />
                          </div>
                        );
                      if (m.escalated)
                        return (
                          <div key={i} className="group/msg">
                            <div className="ml-11 flex items-start gap-2 rounded-xl border border-accent/20 bg-accent/[0.05] px-3 py-2 text-[12px] text-zinc-300">
                              <Zap
                                size={13}
                                className="mt-0.5 shrink-0 text-accent-soft"
                              />
                              <span>
                                {m.escalatedTo ? (
                                  <>
                                    Handing this to {m.escalatedTo} —{" "}
                                    {m.escalated}.
                                  </>
                                ) : (
                                  <>Taking this on properly — {m.escalated}.</>
                                )}
                              </span>
                            </div>
                          </div>
                        );
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
                          {/* Honesty chip: the reply came from a DIFFERENT
                              provider than the one the user picked (capability
                              reroute / failover) — never silent. */}
                          {/* TURN RECEIPT (v1.165.0): server-side accountability
                              — who answered and why, tools run/denied, files.
                              Supersedes the legacy viaProvider chip below
                              whenever the message carries a route. */}
                          {m.route && (
                            <div className="ml-11">
                              <TurnReceipt
                                route={m.route}
                                toolsUsed={m.toolsUsed}
                                deniedTools={m.deniedTools}
                                documents={m.documents}
                                onOpenDocument={openDocPreview}
                                undoFor={undoForPath}
                                onUndo={undoWrite}
                              />
                            </div>
                          )}
                          {!m.route && m.viaProvider && (
                            <div
                              className="ml-11 mt-1 inline-flex items-center gap-1.5 rounded-full border border-amber-400/25 bg-amber-400/[0.08] px-2 py-0.5 text-[11px] text-amber-200/90"
                              title={`Your selected model couldn't take this turn (it may not support tools, or it errored), so the router used ${m.viaProvider} instead. Verify the endpoint's tool support in Connections to keep turns local.`}
                            >
                              <Bot size={10} className="shrink-0" />
                              answered by {m.viaProvider}
                            </div>
                          )}
                          {/* Tools the reply's tool loop actually ran — LEGACY
                              line for pre-v1.165.0 messages; the TurnReceipt
                              carries the same fact (plus denials) when a route
                              is present, so showing both would say it twice. */}
                          {!m.route && m.toolsUsed && m.toolsUsed.length > 0 && (
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
                          {/* Crystallize nudge (v1.120.0): agent turns are by
                              definition multi-step — offer to keep the process. */}
                          {m.fromSession &&
                            i === messages.length - 1 &&
                            !busy &&
                            threadId && (
                              <button
                                type="button"
                                disabled={crystallizingId !== null}
                                onClick={() => void crystallizeThread(threadId)}
                                className="ml-11 mt-1.5 inline-flex items-center gap-1.5 rounded-full border border-accent/25 bg-accent/[0.06] px-2.5 py-1 text-[11.5px] text-accent-soft transition-colors hover:bg-accent/[0.12] disabled:opacity-50"
                              >
                                {crystallizingId ? (
                                  <Loader2 size={12} className="animate-spin" />
                                ) : (
                                  <GitBranch size={12} />
                                )}
                                Keep this as a workflow?
                              </button>
                            )}
                          <div className="ml-11 mt-1 flex items-center gap-0.5 opacity-0 transition-opacity focus-within:opacity-100 group-hover/msg:opacity-100">
                            <CopyIconButton text={m.content} title="Copy message" />
                            <PromoteKnowledgeButton
                              disabledReason={
                                projectId
                                  ? null
                                  : "bind this chat to a project first"
                              }
                              onPromote={() =>
                                promoteNoteToKnowledge(m.content)
                              }
                            />
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
                            {/* v1.149.0: the run's OWN phase, straight from the
                                daemon, in place of a generic "Thinking…". A run
                                that is planning now says so — it used to be
                                indistinguishable from one that was stuck. */}
                            {runStream.phase
                              ? PHASE_LABEL[runStream.phase.phase] ?? runStream.phase.phase
                              : (progress[0] ?? "Thinking…")}
                          </span>
                          {runStream.phase?.detail && (
                            <span className="ml-[22px] text-xs text-zinc-500">
                              {runStream.phase.detail}
                            </span>
                          )}
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
                  {compactNote && (
                    <div className="min-w-0 flex-1 text-[12px] text-zinc-400">
                      {compactNote}
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

              {/* PREFLIGHT (v1.165.0): the active model is known-unreachable
                  BEFORE the user types a paragraph into it. The app always had
                  this fact (/health) and used to reveal it only after the turn
                  failed. Watches the EXPLICIT pick when there is one, else the
                  DEFAULT provider — the default is exactly where the mock
                  incident happened. "auto" resolves per-turn, so it is never
                  warned about (absent from the map → undefined → silent). */}
              <PreflightNote
                provider={splitChoice(choice).provider || health.defaultProvider}
                available={
                  health.byProvider[
                    splitChoice(choice).provider || health.defaultProvider
                  ]
                }
                stale={health.stale}
              />

              {/* The compaction offer (v1.153.0). Sits directly above the
                  composer because it is about the message the user is about to
                  send. Suppressed once dismissed until the conversation grows
                  another ~8 points — the daemon keeps reporting `suggest`
                  every turn, and re-asking on each one would train the user to
                  ignore it well before the automatic threshold arrives. */}
              {(compactDismissedAt === null ||
                (contextUsage?.percent ?? 0) >= compactDismissedAt + 8) && (
                <CompactionOffer
                  usage={contextUsage}
                  busy={compactBusy}
                  onCompact={compactNow}
                  onDismiss={() => setCompactDismissedAt(contextUsage?.percent ?? 0)}
                />
              )}

              {/* Chips queued for the next message — active skill + armed tools
                  (chat mode) share the row with attachment chips. The skill
                  chip is NOT mode-gated (v1.104.0): Agent mode can pick a skill
                  now, and a picker whose selection leaves no trace on screen is
                  indistinguishable from one that failed. Armed tools/connectors
                  stay chat-only because those are chat-loop mechanics. */}
              {(attachments.length > 0 ||
                activeSkill !== "" ||
                selectedTools.length > 0 ||
                selectedConnectors.length > 0) && (
                <div className="flex flex-wrap items-center gap-2 border-t hairline px-3 py-2.5">
                  {activeSkill !== "" && (
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
                  {selectedTools.map((name) => (
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
                  {selectedConnectors.map((id) => (
                      <span
                        key={`conn-${id}`}
                        className="inline-flex items-center gap-1.5 rounded-full border border-accent/25 bg-accent/[0.06] px-2.5 py-1 text-[11px] text-zinc-300"
                      >
                        <PlugZap size={11} className="shrink-0 text-accent-soft" />
                        <span className="max-w-[14rem] truncate">{id}</span>
                        <button
                          type="button"
                          onClick={() => toggleConnector(id)}
                          aria-label={`Turn off connection ${id}`}
                          title="Turn off for this chat"
                          className="text-zinc-500 transition-colors hover:text-rose-300"
                        >
                          <X size={11} />
                        </button>
                      </span>
                    ))}
                  {/* Thread documents used to render duplicate chips here too
                      (v1.91.0) — gone in v1.166.0: the ArtifactsRail and each
                      turn's receipt are THE lists, and saying it twice made
                      the composer row crowd out the send box at the new
                      30-doc cap. */}
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
                {/* "@" AGENT PICKER (v1.150.0). Same shape as the "/" picker
                    below — one affordance grammar for both. */}
                {atActive && !slashActive && (
                  <div className="absolute bottom-full left-3 right-3 z-20 mb-2 overflow-hidden rounded-xl border border-white/10 bg-zinc-900 shadow-lg shadow-black/40">
                    {mentionable === null ? (
                      <p className="px-3 py-2.5 text-xs text-zinc-500">Loading agents…</p>
                    ) : agentMatches.length === 0 ? (
                      <p className="px-3 py-2.5 text-xs text-zinc-500">
                        no matching agent — add one on the Agents page
                      </p>
                    ) : (
                      <div
                        role="listbox"
                        aria-label="Agents"
                        className="max-h-72 overflow-y-auto p-1"
                      >
                        <div className="px-2.5 pb-1 pt-1.5 text-[10px] font-semibold uppercase tracking-wide text-zinc-500">
                          {agentMatches.length} agent{agentMatches.length === 1 ? "" : "s"}
                          {atQuery ? " matching" : ""} — they answer instead of Iron Jarvis
                        </div>
                        {agentMatches.map((a) => (
                          <button
                            key={a.name}
                            type="button"
                            role="option"
                            aria-selected={false}
                            onClick={() => {
                              setInput(
                                (prev) => spliceToken(prev, atToken) + `@${a.mention} `,
                              );
                              setAtDismissed(false);
                              inputRef.current?.focus();
                            }}
                            title={a.description}
                            className="flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-zinc-300 transition-colors hover:bg-accent/[0.12] hover:text-accent-soft"
                          >
                            <Bot size={12} className="shrink-0 text-accent-soft/70" />
                            <span className="shrink-0 font-mono text-[12px]">
                              {a.mention}
                            </span>
                            {/* Where it runs + whether it can actually take work.
                                An offline remote is LISTED, not hidden — "my
                                agent isn't in the list" is the worse failure. */}
                            <span className="shrink-0 text-[10px] text-zinc-600">
                              {a.kind === "remote" ? "remote" : a.kind === "dynamic" ? "custom" : "built-in"}
                            </span>
                            {!a.healthy && (
                              <span className="shrink-0 text-[10px] text-amber-400/80">
                                offline
                              </span>
                            )}
                            <span className="truncate text-[11px] text-zinc-500">
                              {a.description}
                            </span>
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                )}
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
                {/* The "+" menu — the composer stays minimal (+ · project ·
                    mic); attach, skills, connectors and the web/auto toggles
                    all live in here. */}
                <div ref={toolsPopRef} className="relative">
                  <button
                    type="button"
                    onClick={() => {
                      setToolsOpen((v) => !v);
                      setPlusSub(null);
                    }}
                    aria-expanded={toolsOpen}
                    aria-haspopup="true"
                    aria-label="Open the chat menu"
                    title="Attach · skills · connections · web & auto"
                    className={`btn-ghost h-[2.75rem] px-3 py-0 ${
                      toolsOpen ||
                      selectedTools.length > 0 ||
                      selectedConnectors.length > 0 ||
                      activeSkill
                        ? "text-accent-soft"
                        : ""
                    }`}
                  >
                    {uploading ? <LoaderInline /> : <Plus size={16} />}
                  </button>
                  {toolsOpen && (
                    <div className="absolute bottom-full left-0 z-20 mb-2 w-64 rounded-xl border border-white/10 bg-zinc-900 p-1 shadow-lg shadow-black/40">
                      <button
                        type="button"
                        onClick={() => {
                          setToolsOpen(false);
                          fileRef.current?.click();
                        }}
                        disabled={uploading || attachments.length >= MAX_ATTACHMENTS}
                        className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-[12.5px] text-zinc-200 transition-colors hover:bg-white/[0.06] disabled:opacity-40"
                      >
                        <Paperclip size={14} className="shrink-0 text-zinc-400" />
                        Attach files or photos
                      </button>
                      {/* Add this chat to a project — files the open thread
                          into the project (and the context follows), same
                          machinery as the composer toggle. */}
                      <div className="relative">
                        <button
                          type="button"
                          onClick={() =>
                            setPlusSub(plusSub === "project" ? null : "project")
                          }
                          aria-expanded={plusSub === "project"}
                          className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-[12.5px] text-zinc-200 transition-colors hover:bg-white/[0.06]"
                        >
                          <FolderKanban size={14} className="shrink-0 text-zinc-400" />
                          Add to project
                          {activeProject && (
                            <span className="max-w-[6rem] truncate rounded-full bg-accent/[0.12] px-1.5 text-[10px] text-accent-soft">
                              {activeProject.name}
                            </span>
                          )}
                          <ChevronRight size={13} className="ml-auto shrink-0 text-zinc-500" />
                        </button>
                        {/* bottom-0, NOT top-0 (v1.100.0). The thread +
                            composer live inside a Card with overflow-hidden, so
                            an absolute child that extends past it is CLIPPED
                            whatever its z-index. These flyouts hang off a menu
                            already anchored at the bottom of the chat, so
                            growing downward ran them straight off the bottom
                            edge and cut off the list. Growing upward keeps them
                            inside the Card — the same fix the model flyout got
                            in v1.87.0. */}
                        {plusSub === "project" && (
                          <div className="absolute bottom-0 left-full z-30 ml-1 max-h-64 w-60 overflow-y-auto rounded-xl border border-white/10 bg-zinc-900 p-1 shadow-lg shadow-black/40">
                            {projectId && (
                              <button
                                type="button"
                                onClick={() => {
                                  chooseProject("");
                                  setToolsOpen(false);
                                  setPlusSub(null);
                                }}
                                className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-[12px] text-rose-300/90 transition-colors hover:bg-white/[0.06]"
                              >
                                <X size={12} /> Remove from project
                              </button>
                            )}
                            {projects.length === 0 ? (
                              <p className="px-2.5 py-2 text-[11px] text-zinc-500">
                                No projects yet.
                              </p>
                            ) : (
                              projects.map((p) => (
                                <button
                                  key={p.id}
                                  type="button"
                                  onClick={() => {
                                    chooseProject(p.id);
                                    setToolsOpen(false);
                                    setPlusSub(null);
                                  }}
                                  className={`flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-[12.5px] transition-colors hover:bg-white/[0.06] ${
                                    projectId === p.id
                                      ? "text-accent-soft"
                                      : "text-zinc-200"
                                  }`}
                                >
                                  <FolderKanban size={13} className="shrink-0" />
                                  <span className="min-w-0 truncate">{p.name}</span>
                                  {projectId === p.id && (
                                    <Check size={12} className="ml-auto shrink-0" />
                                  )}
                                </button>
                              ))
                            )}
                          </div>
                        )}
                      </div>
                      {/* Skills sit OUTSIDE the chat-only group (v1.104.0):
                          Agent mode can invoke one now, so hiding the menu
                          route would leave "/" as the only way in — findable
                          only by someone who already knew. Armed tools and
                          connectors stay chat-only; an agent already holds the
                          whole registry, so arming a subset means nothing. */}
                      <div className="relative">
                        <button
                          type="button"
                          onClick={() => {
                            setPlusSub(plusSub === "skills" ? null : "skills");
                            ensureSkills();
                          }}
                          aria-expanded={plusSub === "skills"}
                          className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-[12.5px] text-zinc-200 transition-colors hover:bg-white/[0.06]"
                        >
                          <Sparkles size={14} className="shrink-0 text-zinc-400" />
                          Skills
                          {activeSkill && (
                            <span className="max-w-[6rem] truncate rounded-full bg-accent/[0.12] px-1.5 text-[10px] text-accent-soft">
                              {activeSkill}
                            </span>
                          )}
                          <ChevronRight size={13} className="ml-auto shrink-0 text-zinc-500" />
                        </button>
                        {plusSub === "skills" && (
                          <div className="absolute bottom-0 left-full z-30 ml-1 max-h-64 w-60 overflow-y-auto rounded-xl border border-white/10 bg-zinc-900 p-1 shadow-lg shadow-black/40">
                            {activeSkill && (
                              <button
                                type="button"
                                onClick={() => {
                                  setActiveSkill("");
                                  markSetupChanged();
                                }}
                                className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-[12px] text-rose-300/90 transition-colors hover:bg-white/[0.06]"
                              >
                                <X size={12} /> Clear “{activeSkill}”
                              </button>
                            )}
                            {skills === null ? (
                              <div className="px-2.5 py-2">
                                <LoaderInline />
                              </div>
                            ) : skills.length === 0 ? (
                              <p className="px-2.5 py-2 text-[11px] text-zinc-500">
                                No skills installed.
                              </p>
                            ) : (
                              skills.map((s) => (
                                <button
                                  key={s.name}
                                  type="button"
                                  onClick={() => {
                                    pickSkill(s.name);
                                    setToolsOpen(false);
                                    setPlusSub(null);
                                  }}
                                  title={s.description}
                                  className={`flex w-full flex-col rounded-lg px-2.5 py-1.5 text-left transition-colors hover:bg-white/[0.06] ${
                                    activeSkill === s.name ? "text-accent-soft" : "text-zinc-200"
                                  }`}
                                >
                                  <span className="truncate text-[12.5px]">{s.name}</span>
                                  <span className="truncate text-[10.5px] text-zinc-500">
                                    {s.description}
                                  </span>
                                </button>
                              ))
                            )}
                          </div>
                        )}
                      </div>
                      {(
                        <>
                          <div className="relative">
                            <button
                              type="button"
                              onClick={() => {
                                setPlusSub(
                                  plusSub === "connectors" ? null : "connectors",
                                );
                                ensureConnectorCatalog();
                              }}
                              aria-expanded={plusSub === "connectors"}
                              className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-[12.5px] text-zinc-200 transition-colors hover:bg-white/[0.06]"
                            >
                              <PlugZap size={14} className="shrink-0 text-zinc-400" />
                              Connections
                              <ChevronRight size={13} className="ml-auto shrink-0 text-zinc-500" />
                            </button>
                            {plusSub === "connectors" && (
                              <div className="absolute bottom-0 left-full z-30 ml-1 max-h-64 w-64 overflow-y-auto rounded-xl border border-white/10 bg-zinc-900 p-1 shadow-lg shadow-black/40">
                                {connCatalog === null ? (
                                  <div className="px-2.5 py-2">
                                    <LoaderInline />
                                  </div>
                                ) : connectedConnectors.length === 0 ? (
                                  <p className="px-2.5 py-2 text-[11px] leading-relaxed text-zinc-500">
                                    Nothing connected yet — pick one below.
                                  </p>
                                ) : (
                                  connectedConnectors.map((c) => {
                                    const on = selectedConnectors.includes(c.id);
                                    const atCap =
                                      !on &&
                                      selectedConnectors.length >= MAX_CONNECTORS;
                                    const isMemory = c.connect_via === "memory";
                                    return (
                                      <button
                                        key={c.id}
                                        type="button"
                                        role="switch"
                                        aria-checked={on}
                                        disabled={atCap}
                                        onClick={() => toggleConnector(c.id)}
                                        title={
                                          isMemory
                                            ? `${c.name} — grounds replies with this memory`
                                            : `${c.name} — arms its tools for this chat`
                                        }
                                        className={`flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left transition-colors ${
                                          atCap ? "opacity-40" : "hover:bg-white/[0.06]"
                                        }`}
                                      >
                                        <span className="w-4 shrink-0 text-center text-[13px]">
                                          {c.glyph || (isMemory ? "🧠" : "🔌")}
                                        </span>
                                        <span
                                          className={`min-w-0 truncate text-[12px] ${
                                            on ? "text-accent-soft" : "text-zinc-200"
                                          }`}
                                        >
                                          {c.name}
                                        </span>
                                        <span className="ml-auto shrink-0 text-[9.5px] uppercase tracking-wide text-zinc-600">
                                          {isMemory
                                            ? "memory"
                                            : `${c.tools_loaded ?? 0} tools`}
                                        </span>
                                        <span
                                          aria-hidden
                                          className={`flex h-3.5 w-6 shrink-0 items-center rounded-full border px-0.5 transition-colors ${
                                            on
                                              ? "justify-end border-accent/60 bg-accent/25"
                                              : "justify-start border-white/20 bg-white/[0.04]"
                                          }`}
                                        >
                                          <span
                                            className={`h-2 w-2 rounded-full ${
                                              on ? "bg-accent-soft" : "bg-zinc-500"
                                            }`}
                                          />
                                        </span>
                                      </button>
                                    );
                                  })
                                )}
                                {marketplaceTeasers.length > 0 && (
                                  <div className="mt-0.5 border-t hairline pt-1">
                                    <p className="px-2.5 pb-0.5 text-[9.5px] font-semibold uppercase tracking-wide text-zinc-600">
                                      From the marketplace
                                    </p>
                                    {marketplaceTeasers.map((c) => (
                                      <Link
                                        key={c.id}
                                        href="/marketplace"
                                        onClick={() => setToolsOpen(false)}
                                        title={`Connect ${c.name} in the Directory`}
                                        className="flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-[12px] text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-accent-soft"
                                      >
                                        <span className="w-4 shrink-0 text-center text-[13px]">
                                          {c.glyph || "🔌"}
                                        </span>
                                        <span className="min-w-0 truncate">{c.name}</span>
                                        <span className="ml-auto shrink-0 text-[10px] text-zinc-600">
                                          connect ↗
                                        </span>
                                      </Link>
                                    ))}
                                  </div>
                                )}
                                <Link
                                  href="/marketplace"
                                  onClick={() => setToolsOpen(false)}
                                  className="mt-0.5 flex w-full items-center gap-2 rounded-lg border-t hairline px-2.5 py-2 text-left text-[12px] text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-accent-soft"
                                >
                                  <Store size={13} className="shrink-0" />
                                  Marketplace ↗
                                </Link>
                              </div>
                            )}
                          </div>
                          <div className="my-1 border-t hairline" />
                          <button
                            type="button"
                            onClick={toggleWeb}
                            disabled={!webArmed && !webRoom}
                            role="switch"
                            aria-checked={webArmed}
                            title={
                              webArmed
                                ? "Web research armed — click to disarm"
                                : webRoom
                                  ? "Arm web research for this chat"
                                  : `All ${MAX_TOOLS} tool slots armed — disarm one first`
                            }
                            className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-[12.5px] text-zinc-200 transition-colors hover:bg-white/[0.06] disabled:opacity-40"
                          >
                            <Globe size={14} className="shrink-0 text-zinc-400" />
                            Web &amp; research
                            <span
                              className={`ml-auto flex h-4 w-7 items-center rounded-full border px-0.5 ${
                                webArmed
                                  ? "justify-end border-accent/40 bg-accent/20"
                                  : "justify-start border-white/10 bg-white/[0.03]"
                              }`}
                            >
                              <span
                                className={`h-2.5 w-2.5 rounded-full ${
                                  webArmed ? "bg-accent" : "bg-zinc-600"
                                }`}
                              />
                            </span>
                          </button>
                          <button
                            type="button"
                            onClick={toggleAutoTools}
                            role="switch"
                            aria-checked={autoTools}
                            title="Each request arms the safe tools it needs (files, documents, web, images)"
                            className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-[12.5px] text-zinc-200 transition-colors hover:bg-white/[0.06]"
                          >
                            <Sparkles size={14} className="shrink-0 text-zinc-400" />
                            Auto tools
                            <span
                              className={`ml-auto flex h-4 w-7 items-center rounded-full border px-0.5 ${
                                autoTools
                                  ? "justify-end border-accent/40 bg-accent/20"
                                  : "justify-start border-white/10 bg-white/[0.03]"
                              }`}
                            >
                              <span
                                className={`h-2.5 w-2.5 rounded-full ${
                                  autoTools ? "bg-accent" : "bg-zinc-600"
                                }`}
                              />
                            </span>
                          </button>
                        </>
                      )}
                    </div>
                  )}
                </div>
                {/* Project quick-toggle — flip between plain chat and a
                    project right from the composer (the cowork feel), without
                    opening the side panel. Selection logic is the panel's own
                    chooseProject/clearProject; this is just a nearer handle. */}
                <div ref={projPopRef} className="relative">
                  <button
                    type="button"
                    onClick={() => setProjMenuOpen((v) => !v)}
                    aria-expanded={projMenuOpen}
                    aria-haspopup="true"
                    aria-label="Switch project"
                    title={
                      activeProject
                        ? `Working in "${activeProject.name}" — click to switch projects or go plain chat`
                        : "Work inside a project — replies ground in its files + knowledge"
                    }
                    className={`btn-ghost h-[2.75rem] gap-1.5 px-3 py-0 ${
                      activeProject ? "text-accent-soft" : ""
                    }`}
                  >
                    <FolderKanban size={15} />
                    {activeProject && (
                      <span className="max-w-[7rem] truncate text-[12px]">
                        {activeProject.name}
                      </span>
                    )}
                  </button>
                  {projMenuOpen && (
                    <div className="absolute bottom-full left-0 z-20 mb-2 max-h-64 w-60 overflow-y-auto rounded-xl border border-white/10 bg-zinc-900 p-1 shadow-lg shadow-black/40">
                      <button
                        type="button"
                        onClick={() => {
                          chooseProject("");
                          setProjMenuOpen(false);
                        }}
                        className={`flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-[12.5px] transition-colors hover:bg-white/[0.06] ${
                          !projectId ? "text-accent-soft" : "text-zinc-300"
                        }`}
                      >
                        <MessageSquare size={13} className="shrink-0" />
                        Plain chat — no project
                      </button>
                      {projects.map((p) => (
                        <button
                          key={p.id}
                          type="button"
                          onClick={() => {
                            chooseProject(p.id);
                            setProjMenuOpen(false);
                          }}
                          className={`flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-[12.5px] transition-colors hover:bg-white/[0.06] ${
                            projectId === p.id ? "text-accent-soft" : "text-zinc-300"
                          }`}
                        >
                          <FolderKanban size={13} className="shrink-0" />
                          <span className="min-w-0 truncate">{p.name}</span>
                        </button>
                      ))}
                      <Link
                        href="/projects"
                        onClick={() => setProjMenuOpen(false)}
                        className="flex w-full items-center gap-2 rounded-lg border-t hairline px-2.5 py-2 text-left text-[12px] text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-accent-soft"
                      >
                        <Plus size={13} className="shrink-0" />
                        New project / manage all ↗
                      </Link>
                    </div>
                  )}
                </div>
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
                    setCaret(e.target.selectionStart ?? e.target.value.length);
                    setSlashDismissed(false); // editing reopens the "/" dropdown
                  }}
                  // Caret moves that onChange never sees: arrow keys, clicking
                  // into the middle of the text, Home/End, drag-select. All
                  // four are wired because React's onSelect ALONE does not fire
                  // for a collapsed caret — measured in a real browser, the DOM
                  // selectionStart went 21 -> 8 on ArrowLeft while the tracked
                  // value stayed at 21, so the picker refused to reopen when
                  // you moved back into an earlier "/word". keyup and click are
                  // the ones that actually fire for that; onSelect is kept for
                  // drag-selection and onFocus for tabbing back in.
                  onKeyUp={(e) => setCaret(e.currentTarget.selectionStart ?? 0)}
                  onClick={(e) => setCaret(e.currentTarget.selectionStart ?? 0)}
                  onFocus={(e) => setCaret(e.currentTarget.selectionStart ?? 0)}
                  onSelect={(e) => setCaret(e.currentTarget.selectionStart ?? 0)}
                  onKeyDown={onKeyDown}
                  autoFocus
                  rows={1}
                  aria-label="Message"
                  placeholder="Message Iron Jarvis…  (Enter to send · Shift+Enter new line · / for skills)"
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
                {/* The send ARROW: invisible until there's something to send
                    (text or an attachment) — then it materializes. */}
                {(input.trim() || attachments.length > 0 || busy) && (
                  <button
                    onClick={() => send(input)}
                    disabled={busy || !input.trim()}
                    aria-label="Send"
                    title="Send (Enter)"
                    className="btn-accent h-[2.75rem] w-[2.75rem] shrink-0 rounded-full p-0"
                  >
                    {busy ? <LoaderInline /> : <Send size={16} />}
                  </button>
                )}
              </div>
              {/* Composer footer: share on the left (under the project
                  control), the model switcher on the right. Both are the same
                  quiet weight — present when wanted, silent otherwise. */}
              <div className="flex items-center justify-between px-4 pb-2.5">
                <button
                  type="button"
                  onClick={() => setShareOpen(true)}
                  disabled={!threadId}
                  aria-label="Share this chat"
                  title={
                    threadId
                      ? "Share this chat — full transcript or a compacted digest"
                      : "A chat can be shared after its first reply (it saves automatically)"
                  }
                  className="inline-flex items-center gap-1 text-[11.5px] text-zinc-500 transition-colors hover:text-zinc-300 disabled:cursor-not-allowed disabled:opacity-30"
                >
                  <Share2 size={12} />
                </button>
                {/* Context headroom (v1.146.0). Deliberately quiet until it
                    matters: nobody needs a gauge at 12% of a 200k window, and
                    a permanent meter is the kind of chrome that gets ignored
                    exactly when it starts mattering. */}
                <ContextMeter usage={contextUsage} />
                <div ref={modelPopRef} className="relative">
                  <button
                    type="button"
                    onClick={() => {
                      setModelMenuOpen((v) => !v);
                      setModelSub(null);
                    }}
                    disabled={awaiting && sessionId !== null}
                    aria-expanded={modelMenuOpen}
                    aria-haspopup="true"
                    title={
                      awaiting && sessionId !== null
                        ? "Start a new chat to switch models"
                        : "Switch model"
                    }
                    className="inline-flex items-center gap-1 text-[11.5px] text-zinc-500 transition-colors hover:text-zinc-300 disabled:opacity-40"
                  >
                    <span className="max-w-[14rem] truncate font-mono">{modelLabel}</span>
                    <ChevronDown size={11} className="shrink-0" />
                  </button>
                  {modelMenuOpen && (
                    <div className="absolute bottom-full right-0 z-20 mb-1.5 w-52 rounded-xl border border-white/10 bg-zinc-900 p-1 shadow-lg shadow-black/40">
                      <button
                        type="button"
                        onClick={() => {
                          setChoice("");
                          markSetupChanged();
                          setModelMenuOpen(false);
                        }}
                        className={`flex w-full items-center rounded-lg px-2.5 py-1.5 text-left text-[12px] transition-colors hover:bg-white/[0.06] ${
                          !choice ? "text-accent-soft" : "text-zinc-300"
                        }`}
                      >
                        default model
                      </button>
                      {modelProviders.map((p) => (
                        <div key={p.id} className="relative">
                          <button
                            type="button"
                            onClick={() =>
                              setModelSub(modelSub === p.id ? null : p.id)
                            }
                            disabled={!p.available}
                            title={
                              p.available
                                ? undefined
                                : `${p.label} isn't connected — set it up on Connections`
                            }
                            aria-expanded={modelSub === p.id}
                            className={`flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-[12px] transition-colors hover:bg-white/[0.06] disabled:cursor-not-allowed disabled:opacity-45 disabled:hover:bg-transparent ${
                              splitChoice(choice).provider === p.id
                                ? "text-accent-soft"
                                : "text-zinc-300"
                            }`}
                          >
                            <span className="min-w-0 truncate">{p.label}</span>
                            {/* Where it runs — the whole point of the reorder:
                                a list you can act on without knowing which of
                                your providers costs money. */}
                            <span
                              className={`shrink-0 text-[10px] ${
                                (KIND_BADGE[p.kind] ?? KIND_BADGE.api).cls
                              }`}
                            >
                              {p.available
                                ? (KIND_BADGE[p.kind] ?? KIND_BADGE.api).text
                                : "offline"}
                            </span>
                            <ChevronRight
                              size={12}
                              className="ml-auto shrink-0 text-zinc-500"
                            />
                          </button>
                          {modelSub === p.id && (
                            /* Anchored to the BOTTOM so a long catalog
                               (OpenRouter) grows UPWARD over the chat area
                               instead of being clipped at the card edge. */
                            <div className="absolute bottom-0 right-full z-30 mr-1 max-h-[24rem] w-56 overflow-y-auto rounded-xl border border-white/10 bg-zinc-900 p-1 shadow-lg shadow-black/40">
                              {models
                                .filter((m) => m.provider === p.id)
                                .map((m) => {
                                  const v = `${m.provider}::${m.model}`;
                                  return (
                                    <button
                                      key={v}
                                      type="button"
                                      onClick={() => {
                                        setChoice(v);
                                        markSetupChanged();
                                        setModelMenuOpen(false);
                                        setModelSub(null);
                                      }}
                                      className={`flex w-full items-center rounded-lg px-2.5 py-1.5 text-left font-mono text-[11.5px] transition-colors hover:bg-white/[0.06] ${
                                        choice === v
                                          ? "text-accent-soft"
                                          : "text-zinc-300"
                                      }`}
                                    >
                                      <span className="min-w-0 truncate">{m.model}</span>
                                      {choice === v && (
                                        <Check size={11} className="ml-auto shrink-0" />
                                      )}
                                    </button>
                                  );
                                })}
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            </Card>
          </div>

          {/* Project panel (right): the context spine. Pick a project to scope
              threads, ground replies in its knowledge, and aim the workspace at
              its folder — or just browse to any folder for an ad-hoc workspace.
              The chosen folder rides along as workspace_dir so the chat's
              armed file tools write here and their output surfaces live below. */}
          {workspaceOpen ? (
            <aside
              className="relative w-full shrink-0 md:w-[var(--rail-w)]"
              style={{ "--rail-w": `${railW}px` } as CSSProperties}
            >
              {/* Drag grip (desktop): widen the preview/workspace column or
                  keep it as is — double-click resets to the default width. */}
              <div
                role="separator"
                aria-orientation="vertical"
                aria-label="Resize the side panel"
                title="Drag to resize — double-click to reset"
                onPointerDown={startRailDrag}
                onDoubleClick={resetRailW}
                className="group/resize absolute -left-2.5 top-0 z-10 hidden h-full w-3 cursor-col-resize touch-none items-center justify-center md:flex"
              >
                <span className="h-12 w-1 rounded-full bg-white/10 transition-colors group-hover/resize:bg-accent/60" />
              </div>
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
                  {/* The workspace strip: inline tabs + the wide surfaces.
                      Projects has no nav entry — this rail IS the module. */}
                  {activeProject && (
                    <div className="mt-2 flex flex-wrap items-center gap-1">
                      {(["files", "knowledge"] as const).map((t) => (
                        <button
                          key={t}
                          type="button"
                          onClick={() => setRailTab(t)}
                          className={`rounded-lg border px-2 py-1 text-[10.5px] capitalize transition-colors ${
                            railTab === t
                              ? "border-accent/40 bg-accent/[0.1] text-accent-soft"
                              : "border-white/10 text-zinc-400 hover:text-zinc-200"
                          }`}
                        >
                          {t}
                        </button>
                      ))}
                      {(["tasks", "board", "media"] as const).map((t) => (
                        <button
                          key={t}
                          type="button"
                          onClick={() => setProjectView(t)}
                          className="rounded-lg border border-white/10 px-2 py-1 text-[10.5px] capitalize text-zinc-400 transition-colors hover:border-accent/30 hover:text-accent-soft"
                          title={`Open ${t} in the chat column`}
                        >
                          {t}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
                {previewPath ? (
                  <div className="min-h-0 flex-1">
                    <DocPreview
                      // The nonce remounts the preview after an undo so it
                      // refetches and shows the reverted file (v1.168.0).
                      key={`${previewNonce}:${previewPath}`}
                      path={previewPath}
                      onClose={() => setPreviewPath(null)}
                    />
                  </div>
                ) : activeProject && railTab === "knowledge" ? (
                  <div className="min-h-0 flex-1 overflow-y-auto">
                    <KnowledgePanel projectId={activeProject.id} />
                  </div>
                ) : (
                <>
                {/* FILES IN THIS CHAT (v1.153.2 → ArtifactsRail v1.165.0).
                    Every file this conversation made or was given, reachable
                    without a project. Reported problem: a redacted document was
                    announced and the user had nowhere in the app to look. Now a
                    shared component with file-type icons, copy-path, download
                    AND per-item dismiss (the inline block had no dismiss here);
                    since v1.165.0 the backend also reports created_paths from
                    EVERY tool, so repl-made files land here too, not only the
                    document tools' output. */}
                {threadDocs.length > 0 && (
                  <div className="shrink-0">
                    <ArtifactsRail
                      items={threadDocs.map((p) => ({ path: p }))}
                      onPreview={openDocPreview}
                      onDismiss={dismissThreadDoc}
                      cap={MAX_THREAD_DOCS}
                      undoFor={undoForPath}
                      onUndo={undoWrite}
                      onPromote={promoteFileToKnowledge}
                      promoteDisabledReason={
                        projectId ? null : "bind this chat to a project first"
                      }
                      downloadHref={(p) => {
                        const tok = ijToken();
                        // &download=1 forces Content-Disposition: attachment
                        // (v1.166.0) — pdf/images serve INLINE by default now,
                        // and the anchor's own `download` attribute is ignored
                        // cross-origin (:8788 → :8787), so the server flag is
                        // the only thing that makes this a real download.
                        return `${API_BASE}/documents/file?path=${encodeURIComponent(
                          p,
                        )}${tok ? `&token=${encodeURIComponent(tok)}` : ""}&download=1`;
                      }}
                    />
                  </div>
                )}
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
                      <FilesPanel folder={workspaceDir} onPreview={openDocPreview} />
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
                </>
                )}
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
