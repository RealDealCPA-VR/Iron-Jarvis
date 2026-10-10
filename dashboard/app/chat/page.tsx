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
  memo,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ComponentProps,
  type CSSProperties,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, m } from "framer-motion"; // v1.250.0 (S-08)
import Link from "next/link";
import {
  Archive,
  ArchiveRestore,
  ArrowLeft,
  ArrowUp,
  AudioLines,
  BookmarkPlus,
  Bot,
  Brain,
  Check,
  ChevronDown,
  ChevronRight,
  Blocks,
  Download,
  FileText,
  ExternalLink,
  Folder,
  FolderKanban,
  FolderOpen,
  FolderPen,
  GitBranch,
  Globe,
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
  Share2,
  ListTree,
  Sparkles,
  Square,
  SquareTerminal,
  Store,
  Trash2,
  Volume2,
  VolumeX,
  Wrench,
  X,
  Zap,
} from "lucide-react";
import { get, post, put, del, ApiError, API_BASE, ijToken } from "@/lib/api";
import {
  ARCHIVED_THREADS_PATH,
  archiveChat,
  stillRunningNote,
  unarchiveChat,
} from "@/lib/archiveChat";
import { cacheDrop, cachedGet, cacheSet } from "@/lib/apiCache";
import { CommThreadBanner } from "@/components/chat/CommThreadBanner";
import {
  CompactionCard,
  CompactionChip,
  type CompactionInfo,
} from "@/components/chat/CompactionCard";
import {
  WorkflowDraftCard,
  WorkflowRunChip,
} from "@/components/chat/WorkflowDraftCard";
import type { RunResult } from "@/components/chat/RunResultCard";
import { CopyIconButton, Markdown, MemoMarkdown } from "@/components/Markdown";
import {
  TurnReceipt,
  receiptWantsAttention,
  type TurnAdapted,
  type TurnRoute,
} from "@/components/chat/TurnReceipt";
import { ThinkingDisclosure } from "@/components/chat/ThinkingDisclosure";
import { LiveToolRows, WorkLine, WorkRow } from "@/components/chat/WorkLine";
import { ReplyChanges } from "@/components/chat/ReplyChanges";
import { turnWindow } from "@/lib/turnChanges";
import { FollowupChips } from "@/components/chat/FollowupChips";
import { DockAsk } from "@/components/chat/DockAsk";
import { collectDockAsks, dockAskKeyHint } from "@/lib/dockAsk";
import { PackPromptForm } from "@/components/chat/PackPromptForm";
import {
  answerElicitation,
  decideSampling,
  decodeResourceReceipts,
  fetchPackPrompts,
  fetchPackResources,
  outcomeWords,
  type PackPrompt,
  type PackResource,
  type ResourceReceipt,
} from "@/lib/mcpInteract";
import { decodeFolderRules, decodeThreadRefs, type ThreadRefReceipt } from "@/lib/turnReads";
import {
  CHAT_REFS_FULL_NOTE,
  CHAT_REFS_MAX,
  addChatRef,
  chatRefIds,
  useChatRefSearch,
  visibleChatRefRows,
  type ChatRefPick,
} from "@/lib/chatRefs";
import {
  AT_FILES_ROWS_MAX,
  entryWhere,
  matchProjectEntries,
  sameFolderPath,
  useProjectFiles,
  type ProjectEntry,
} from "@/lib/atMenuFiles";
import { atAgentName, chatRefSecondary } from "@/lib/atMenuRows";
import { fetchFollowups } from "@/lib/followups";
import { useLiveThinking } from "@/lib/liveThinking";
import { DoorsStrip, type Door } from "@/components/chat/DoorsStrip";
import { PreferenceSuggestion } from "@/components/chat/PreferenceSuggestion";
import { ReplyRating, type ReplyRatingValue } from "@/components/chat/ReplyRating";
import { ConfigCards } from "@/components/chat/ConfigCards";
import { SecretPasteNotice } from "@/components/chat/SecretPasteNotice";
import {
  applySettledCards,
  cardId,
  decodeConfigCards,
  looksLikeSecret,
  type ConfigCard,
} from "@/lib/configCards";
import { getDeviceId } from "@/lib/device";
import { NEW_CHAT_EVENT, closeChatSlot, useChatSlot } from "@/lib/sidebarSlot";
import {
  applySettled,
  decodeSuggestion,
  type ChatSuggestion,
} from "@/lib/preferences";
import { NewChatSuggestions, NEW_CHAT_SUGGESTIONS } from "@/components/chat/NewChatSuggestions";
import {
  ArtifactsRail,
  confirmUndoPrompt,
  joinUndoByPath,
  normalizeFsPath,
  revertedActionIds,
  type UndoRowLike,
} from "@/components/chat/ArtifactsRail";
import { PreflightNote } from "@/components/chat/PreflightNote";
import { HomeLine } from "@/components/chat/HomeLine";
import { ChatMoreMenu } from "@/components/chat/ChatMoreMenu";
import { ProjectDrawer } from "@/components/chat/ProjectDrawer";
import {
  PANEL_GHOST,
  PANEL_SELECT,
  PanelSection,
  PanelTabs,
  useOpenTerminal,
} from "@/components/chat/ProjectPanelParts";
import { ModelSuggestChip } from "@/components/chat/ModelSuggestChip";
import { PermissionChip } from "@/components/chat/PermissionChip";
import { asPermissionMode, type PermissionMode } from "@/lib/permissionLevels";
import {
  COMPOSER_CARD_EDGE,
  COMPOSER_ICON_BUTTON,
  composerChipClass,
  composerKeyHint,
  toolsChipWords,
} from "@/lib/composerChips";
import { scrollToLatest, useRepinOnGrowth } from "@/lib/transcriptRepin";
import type { BatchPreview } from "@/components/chat/BatchSuggestCard";
import { CHAT_EXAMPLES, pickExamples } from "@/components/chat/examples";
import { stepLabel } from "@/components/chat/stepLabel";
import { useProviderHealth } from "@/lib/useProviderHealth";
import { useDaemon } from "@/lib/daemon";
import { needsConnect, providerDisplay } from "@/lib/onboarding";
import type { WorkflowDraft, WorkflowRun } from "@/lib/types";
import type { IJEvent, ModelOption, SessionView, TurnUsage } from "@/lib/types";
import { turnUsageFrom } from "@/lib/types";
import { slashTokenAt, tokenAt, spliceToken } from "@/lib/slash";
import { anyComposerMenuOpen, atMenuOpen, slashMenuOpen } from "@/lib/composerMenus";
import { JumpToLatest } from "@/components/chat/JumpToLatest";

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
/** What POST /chat/panel answers (v1.284.0): a ROUND (`mode: "panel"`), or a
 *  HAND-OFF (`mode: "session"`) when one local agent was asked for work a
 *  panel seat cannot do — the page then opens the same tooled session a chat
 *  escalation opens, naming `target`. An older daemon answers with no `mode`,
 *  which reads as a round. */
interface PanelResponse {
  mode?: "panel" | "session";
  thread_id?: string;
  entries?: PanelEntry[];
  spoke?: string[];
  skipped?: string[];
  unknown_mentions?: string[];
  /** Session mode: the roster target ("builder", "custom:<slug>") and the
   *  participant key the reply bubble is attributed to. */
  target?: string;
  who?: string;
  reason?: string;
  tools?: string[];
  /** Panel mode: how much of the chat the speakers were shown (honesty). */
  context?: { chat_messages?: number; chat_dropped?: number };
}
import { useEvents } from "@/lib/useEvents";
import { CHAT_EVENT_TYPES } from "@/components/chat/chatEventTypes";
import { useDictation } from "@/lib/useDictation";
import { useTTS } from "@/lib/useTTS";
import {
  useChatStream,
  StreamError,
  type ToolCard,
  type ContextUsage,
  useLiveText,
  type UseChatStream,
  type PrepStep,
} from "@/lib/useChatStream";
import { settledSplit } from "@/lib/streamSplit";
import { useModels } from "@/lib/useModels";
import {
  createComposerStore,
  useComposer,
  type ComposerStore,
} from "@/lib/composerStore";
import { clearDraft, readDraft, writeDraft } from "@/lib/chatDrafts";
import { CONTINUE_PROMPT, mergeContinuation } from "@/lib/continueReply";
import { canRetryWithDefault, providerTrouble } from "@/lib/providerFallback";
import { RetryTurnButton } from "@/components/chat/RetryTurnButton";
import { matchModels, readRecentModels, rememberRecentModel } from "@/lib/recentModels";
import { ModelRowChips, modelText } from "@/components/ModelRowBits";
import { friendlyModelName } from "@/lib/friendlyModelName";
import { answeredModelName as answeredModelNameFor } from "@/lib/answeredModel";
import { QuietNote, TurnClock } from "@/components/chat/TurnClock";
import { branchInfo, forkTail, switchBranch, type BranchSet } from "@/lib/branches";
import { BranchPicker } from "@/components/chat/BranchPicker";
import { REPLY_ACTION_BTN, REPLY_ACTION_SQUARE } from "@/components/chat/replyActions";
import { ConversationMap } from "@/components/chat/ConversationMap";
import { QuoteSelection } from "@/components/chat/QuoteSelection";
import { RegenerateMenu } from "@/components/chat/RegenerateMenu";
import { insertQuote, quoteBlock } from "@/lib/quote";
import { takePageContext, type PageContext } from "@/lib/pageContext";
import { useRunStream, type UseRunStream } from "@/lib/useRunStream";
import dynamic from "next/dynamic";
import { useVisibleInterval } from "@/lib/useVisibleInterval";
import ThreadGroups, { GROUP_LIMIT } from "@/components/chat/ThreadGroups";
import { formatAge, markViewed, readLastViewed, threadStatuses } from "@/lib/threadStatus";
import { AT_MENU_ROOMY_PX, squeezedLeadRows, useAtMenuFit } from "@/lib/chatRefsMenuFit";
import { useThreadListPoll } from "@/lib/threadListPoll";
import { appendDictation } from "@/components/VoiceInput";
import { ErrorNote, LoaderInline, OfflineHint } from "@/components/ui";
import { ModuleTitle } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";

/** What the module is, said once (v1.215.0). Shown behind the title in the
 *  thread rail — see `ModuleTitle`. It absorbs the standing blurb that used to
 *  sit under the page header ("Answers come back in seconds… Attach files or
 *  drop them anywhere on the page"): that sentence earned its line on a user's
 *  first visit and cost one on every visit after, which is the whole reason
 *  this module's chrome is being taken down. The drop affordance it named is
 *  not lost — the card draws a dashed drop target the moment a file is over
 *  it, which says the same thing at the moment it matters. */
const CHAT_HINT =
  "Talk to Iron Jarvis. Ask anything — quick answers come straight back, and " +
  "work that needs files, tools or several steps just gets done. Attach files, " +
  "or drop them anywhere on the page.";
import type { ProjectSurfaceView } from "@/components/project/ProjectSurfaces";
import {
  SourcesRow,
  WEB_TOOLS,
  extractWebSources,
  type ChatSource,
} from "@/components/chat/SourcesRow";

/* v1.258.0 (S-03): six panels the chat route used to download before first
 * paint. Every one renders only behind a state gate that is false on open —
 * a project board, a folder-batch card, the knowledge rail, the share dialog,
 * a run-result card and the goal offer — so deferring them changes nothing a
 * user sees. ProjectSurfaces is the big one: it statically imports KanbanBoard,
 * the only path pulling dnd-kit (43.6 KiB) onto this route.
 *
 * NOT deferred, deliberately: CompactionCard and WorkflowDraftCard. Each shares
 * a module with a chip the page renders anyway (CompactionChip, WorkflowRunChip),
 * and bundling is per-module — the code would ship regardless and the change
 * would look like progress while moving nothing.
 *
 * `{ ssr: false }` with no `loading`, matching components/Overlays.tsx: there is
 * nothing to show until the gate opens.
 */
const KnowledgePanel = dynamic(
  () =>
    import("@/components/project/KnowledgePanel").then((m) => ({
      default: m.KnowledgePanel,
    })),
  { ssr: false },
);
const ShareChatDialog = dynamic(
  () =>
    import("@/components/chat/ShareChatDialog").then((m) => ({
      default: m.ShareChatDialog,
    })),
  { ssr: false },
);
// v1.328.0 (calm chat W3-3): shown only when an archive finds the chat still
// working, so its code arrives with it.
const ArchiveChatDialog = dynamic(
  () =>
    import("@/components/chat/ArchiveChatDialog").then((m) => ({
      default: m.ArchiveChatDialog,
    })),
  { ssr: false },
);
// The heaviest single win on this route: ProjectSurfaces statically imports
// KanbanBoard, which is the only thing pulling dnd-kit (chunk 6284, 43.6 KiB
// measured) onto /chat. Nothing else on the page reaches either.
const ProjectSurface = dynamic(
  () =>
    import("@/components/project/ProjectSurfaces").then((m) => ({
      default: m.ProjectSurface,
    })),
  { ssr: false },
);
const BatchSuggestCard = dynamic(
  () =>
    import("@/components/chat/BatchSuggestCard").then((m) => ({
      default: m.BatchSuggestCard,
    })),
  { ssr: false },
);
const RunResultCard = dynamic(
  () =>
    import("@/components/chat/RunResultCard").then((m) => ({
      default: m.RunResultCard,
    })),
  { ssr: false },
);
const GoalBirth = dynamic(
  () =>
    import("@/components/chat/GoalContractCard").then((m) => ({
      default: m.GoalBirth,
    })),
  { ssr: false },
);
/* v1.311.0: three more panels that render only after a click — the file
 * preview (a file chip or a generated document), the workspace's files list,
 * and the folder tree that picks it (the workspace panel is closed by
 * default). With the e-mail composer (deferred inside DraftCard) the finding
 * measured ~62 kB of raw JS every chat visit parsed for nothing. Checked
 * against the v1.258.0 rule first: the page imports ONLY the component from
 * each module, and no other module on this route value-imports any of the
 * three (DocPreview's helpers have no importers outside the file), so the
 * modules really leave the route. The fourth, the e-mail composer, is
 * deferred inside DraftCard. */
const DocPreview = dynamic(
  () =>
    import("@/components/chat/DocPreview").then((m) => ({
      default: m.DocPreview,
    })),
  { ssr: false },
);
const FilesPanel = dynamic(
  () =>
    import("@/components/terminal/FilesPanel").then((m) => ({
      default: m.FilesPanel,
    })),
  { ssr: false },
);
const DirectoryTree = dynamic(
  () =>
    import("@/components/terminal/DirectoryTree").then((m) => ({
      default: m.DirectoryTree,
    })),
  { ssr: false },
);
/* v1.310.0 (wave 2, demo-mode-chat-nonsense): the shared connect doors, shown
 * in the EMPTY state only while no real model answers (or the default is still
 * the offline demo). Deferred like the panels above: once a model is chosen the
 * gate is false on every open, so the route need not carry the doors. */
const ConnectDoors = dynamic(
  () =>
    import("@/components/onboarding/ConnectDoors").then((m) => ({
      default: m.ConnectDoors,
    })),
  { ssr: false },
);

/** One pending ask of an escalated run (v1.227.0) — folded from
 *  approval.requested/approval.resolved events, one ApprovalCard each. */
interface SessionAsk {
  id: string;
  tool: string;
  args?: Record<string, unknown>;
  /** v1.247.0: a batched ask (count > 1) and the daemon's wait (0 = until
   *  answered) — read off the approval.requested payload or the listing. */
  count?: number;
  examples?: Record<string, unknown>[];
  timeoutS?: number;
}

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  /** v1.324.0: what the user attached from their apps ("@"), and — on the
   *  reply — whether each one could be read. */
  appResources?: ResourceReceipt[];
  /** v1.278.0: a note the user sent MID-TURN that the turn read at a round
   *  boundary — kept as a user message because the model saw it as one. */
  steer?: boolean;
  /** v1.282.0: the preference sentences this turn kept (the receipt says them). */
  remembered?: string[];
  /** v1.320.0: the user's 👍 / 👎 on this reply (and what to change), so a
   *  reopened chat shows the answer instead of asking again. */
  rating?: ReplyRatingValue;
  /** v1.305.0: a repeated correction the daemon proposes keeping — the quiet
   *  line under the receipt. `state` records the user's answer, so a reload
   *  renders "Remembered: …" (or "won't suggest again"), never the ask. */
  suggestion?: ChatSuggestion;
  /** Calm UI redesign S3/S4: "Setting changed … [Undo]" and secure credential
   *  cards from this turn. A card's state (undone, saved) is stored here, so a
   *  reopened chat shows what happened, never the ask again. */
  configCards?: ConfigCard[];
  /** v1.298.0: the turn's trust posture ("low" when it ran under low trust),
   *  the daemon's reason and its note — the receipt's quiet line. Absent on
   *  full-trust turns and on messages from before the field existed. */
  trust?: string;
  trustReason?: string;
  trustNote?: string;
  /** v1.300.0: the turn's token accounting (cache reads, list-price cost) —
   *  the receipt's quiet "cached N% · ~$x list" line. Absent → nothing. */
  usage?: TurnUsage;
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
  /** The agent session this (last) bubble is still waiting on (v1.226.0).
   *  Set by sendAgent once POST /sessions answers and stripped by finalize/
   *  Stop with the reply — so a thread reopened mid-run resumes the wait
   *  instead of losing the answer. Lives on the MESSAGE, not `setup`: the
   *  daemon whitelists setup keys (routes/chat.py _clean_setup) but stores
   *  bubbles verbatim. */
  awaitingSession?: string;
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
  /** The capability envelope bent this turn to fit a measured-weak model
   *  (v1.202.0) — the receipt's quiet "adapted to <model>: …" line. Persists
   *  with the thread like route; absent on unbent turns (the daemon sends
   *  null there, which never lands on the message). */
  adapted?: TurnAdapted;
  /** Armed tools the engine refused this turn (assistant messages) — a silent
   *  denial reads as the model ignoring the user, so the receipt shows it. */
  deniedTools?: string[];
  /** ABSOLUTE paths of files this turn created/edited — per-message so the
   *  receipt can say which TURN made which file (threadDocs is the rollup). */
  documents?: string[];
  /** DOORS into the surfaces this turn actually touched (v1.199.0) —
   *  SERVER-derived from tools that executed ok (deduped, capped at 4; files
   *  excluded — the ArtifactsRail owns files). Persists with the thread like
   *  every other field; pre-v1.199.0 messages simply carry none. */
  doors?: Door[];
  /** This assistant reply was cut off mid-stream (Stop, or a committed failure)
   *  — shown with a subtle marker so a partial answer never looks complete. */
  interrupted?: boolean;
  /** The turn proposed a reusable workflow — rendered as a draft card
   *  (v1.120.0). Persists with the thread like every other field. */
  workflowDraft?: WorkflowDraft;
  /** The turn RAN a saved workflow via the workflow_run tool (v1.170.0,
   *  contract 2) — or the user started one from the "+" menu. Rendered as a
   *  live WorkflowRunChip under the message; persists with the thread, and on
   *  reload the chip's run-record poll settles the final truth. */
  workflowRun?: { runId: string; name: string };
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
  /** The agent room the panel round ran in — sent back as `panel_thread_id`
   *  so the next round stays in the same room (v1.284.0). Not a link: the
   *  conversation lives in chat (v1.309.0). */
  panelThreadId?: string;
  /** That agent failed this round; its content is an honest error, not a reply. */
  panelError?: boolean;
  /** v1.284.0: what the speakers were NOT shown — "3 earlier messages did not
   *  fit the agent's context window" — on the round's first reply. Never
   *  silent: a forgotten turn the user can see is a limit, an invisible one
   *  reads as a broken feature. */
  panelNote?: string;
  /** v1.285.0: a REMOTE agent's line that came through its inbound door —
   *  `progress` renders as a quiet line, `question`/`done` as a reply;
   *  `pending` = the remote took the task and will report back. */
  panelKind?: string;
  /** v1.285.0: the room entry's timestamp the line was mirrored from — the
   *  dedupe key, so a live event and a reopen never show one line twice. */
  panelAt?: string;
  /** v1.323.0: when this message was sent (user) or settled (assistant), ISO.
   *  Shown quietly on hover; older messages simply carry none. */
  at?: string;
  /** v1.323.0: the model's own reasoning, shown folded above the reply.
   *  Display-only — `toRequestMessages` never sends it back to a model. */
  thinking?: string;
  /** v1.323.0: seconds the turn thought before its first word (the
   *  disclosure's "Thought for N s"). */
  thinkingSeconds?: number;
  /** v1.323.0: the answer stopped because the model ran out of output room —
   *  the reply offers Continue. */
  truncated?: boolean;
  /** v1.323.0: the turn's tool steps with how long each took (receipt).
   *  v1.329.0: each may carry a short safe `target` (lib/workTarget), last. */
  steps?: { name: string; ok: boolean | null; ms: number | null; target?: string }[];
  /** v1.323.0: when the turn started, said its first word, and finished (ms
   *  epoch) — the receipt's speed line. */
  timing?: { startedAt: number; firstTokenAt: number | null; endedAt: number };
  /** v1.323.0: the hidden "please continue" turn of a Continue press — never
   *  rendered; replaced by the merged reply once the continuation lands. */
  continuation?: boolean;
  /** v1.325.0: other versions of the conversation from this message on — an
   *  edit (on the user message) or a Try again (on the reply). Display-only:
   *  `toRequestMessages` sends role + content, never this. */
  branch?: BranchSet<ChatMessage>;
  /** v1.325.0: the dashboard page this question was asked about ("Ask Jarvis
   *  about this page") — sent with this turn, and again on a Try again. */
  pageContext?: PageContext;
  /** v1.328.0 (calm chat W3-1): the saved chats this user message pointed at
   *  with "@" ({id, title}) — sent as `thread_refs` with this turn, and again
   *  on a Try again. */
  chatRefs?: ChatRefPick[];
  /** v1.327.0 (calm chat W2-3): the project folder's instruction files this
   *  turn followed ("AGENTS.md", "CLAUDE.md") — the receipt says them. Absent
   *  when none were read and on older messages. */
  folderRules?: string[];
  /** v1.327.0: the saved chats this message pointed to with "@", read or
   *  left out (`{id, title, chars, ok, note}`) — the receipt says them. */
  threadRefs?: ThreadRefReceipt[];
}

/** v1.325.0: a message written while a reply was running — sent after it. */
interface QueuedMessage {
  id: string;
  text: string;
  files: UploadedFile[];
  appRes: PackResource[];
  page: PageContext | null;
  /** v1.328.0: the saved chats picked with "@" when it was queued. */
  refs: ChatRefPick[];
}
/** v1.325.0: at most this many messages wait for the running reply. */
const MAX_QUEUED = 3;

/** v1.323.0: how much of a reply's reasoning is kept on the message (the
 *  daemon caps the POST lane's at the same size). */
const THINKING_CAP = 20_000;

/** v1.323.0: a message's time in the user's own words — "10:42", or with the
 *  day when it is not today. Empty for a missing or unreadable stamp. */
function messageTime(at: string | undefined): { short: string; full: string } {
  if (!at) return { short: "", full: "" };
  const d = new Date(at);
  if (Number.isNaN(d.getTime())) return { short: "", full: "" };
  const time = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  const today = new Date().toDateString() === d.toDateString();
  return {
    short: today ? time : `${d.toLocaleDateString([], { month: "short", day: "numeric" })}, ${time}`,
    full: d.toLocaleString(),
  };
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
  turn_id?: string; // v1.278.0: names the turn so /chat/turns/{id}/steer can reach it
  thread_id?: string; // v1.327.0 (W2-1): the open SAVED chat, so the list can light it
  // as running / waiting; a new unsaved chat sends none
  device_id?: string; // redesign S3: a per-device setting changed in chat lands on THIS device
  granted_tools?: string[]; // v1.312.0 (W4-2): "Allow for this conversation" grants
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
  resources?: { pack: string; uri: string; name?: string }[]; // v1.324.0: "@" → from your apps
  mcp_cards?: boolean; // v1.324.0: this page draws the apps' question/model-request cards
  page_context?: { title: string; path: string; text: string }; // v1.325.0: "Ask Jarvis about this page"
  thread_refs?: string[]; // v1.328.0: "@" → saved chats read as reference material (≤ 3 ids)
};
interface ChatResponse {
  reply: string;
  provider?: string;
  model?: string;
  /** Server-side route disclosure (v1.165.0) — see ChatMessage.route. */
  route?: TurnRoute;
  /** Armed tools the engine refused this turn. */
  denied_tools?: string[];
  /** v1.282.0: the preference sentences this turn kept. */
  remembered?: string[];
  /** v1.305.0: null or {id, text, count, quotes, since} — decoded through
   *  decodeSuggestion (the stream lane's whitelist), never trusted raw. */
  suggestion?: unknown;
  /** Redesign S3/S4: settings cards — decoded through decodeConfigCards. */
  config_cards?: unknown;
  /** v1.298.0: trust posture of the turn (optional on the wire). */
  trust?: string;
  trust_reason?: string;
  trust_note?: string;
  /** v1.300.0: token accounting — decoded through turnUsageFrom (numbers only). */
  usage?: unknown;
  images?: string[];
  skill?: string;
  tools_used?: string[];
  /** ABSOLUTE paths of documents this turn created/edited (preview panel). */
  documents?: string[];
  /** Doors into the surfaces this turn touched (v1.199.0) — see
   *  ChatMessage.doors. Server-derived; the client never invents these. */
  doors?: Door[];
  /** The envelope's adaptation disclosure (v1.202.0) — null on every unbent
   *  turn (always present on the wire; see ChatMessage.adapted). */
  adapted?: TurnAdapted | null;
  /** The turn asked to be re-run as a full agent session (v1.108.0). */
  escalate?: boolean;
  escalate_reason?: string;
  /** Validated roster name for the hand-off (v1.139.0): "researcher",
   *  "custom:<slug>", "remote:<name>" — null/absent keeps the builder default. */
  escalate_agent?: string | null;
  /** The turn proposed a reusable workflow instead of prose (v1.120.0). */
  workflow_draft?: WorkflowDraft | null;
  /** The turn's tool loop executed workflow_run successfully (v1.170.0,
   *  contract 2): {run_id, name}. Absent otherwise (failed tool → absent). */
  workflow_run?: { run_id?: string; name?: string } | null;
  /** What the turn cost against the answering model's context window
   *  (v1.146.0) — drives the composer's headroom meter. */
  context?: ContextUsage | null;
}

/** A participant key ("builtin:builder", "remote:hermes-mac-mini") as the name
 *  the user typed after "@" — the source prefix is plumbing, not identity. */
function agentDisplayName(key: string): string {
  return key.includes(":") ? key.slice(key.indexOf(":") + 1) : key;
}

/** Who a saved conversation is TALKING TO (v1.284.0): the agents of the
 *  trailing panel round (a round may hold several), else nobody — Iron
 *  Jarvis. Trailing user messages (a follow-up not yet answered, a failed
 *  send) keep the addressee: the follow-up is theirs. Read on thread open and
 *  after every round, so a reload keeps the conversation where it was. */
function addresseeOf(msgs: ChatMessage[]): string[] {
  let i = msgs.length - 1;
  while (i >= 0 && msgs[i].role === "user") i--;
  const keys: string[] = [];
  for (; i >= 0 && msgs[i].role === "assistant"; i--) {
    const who = msgs[i].panelWho;
    if (!who) break;
    if (!keys.includes(who)) keys.unshift(who);
  }
  return keys;
}

/** A participant key → the target `sendAgent` can open a session on
 *  ("builtin:builder" → "builder", "dynamic:remy" → "custom:remy"). A remote
 *  has no session-shaped run on this machine → null (the panel keeps it). */
function sessionTargetOf(key: string | undefined): string | null {
  if (!key) return null;
  const at = key.indexOf(":");
  const source = at >= 0 ? key.slice(0, at) : "builtin";
  const name = (at >= 0 ? key.slice(at + 1) : key).trim();
  if (!name) return null;
  if (source === "builtin") return name;
  if (source === "dynamic") return `custom:${name}`;
  return null;
}

/** The message without its addresses — the task a hand-off session gets.
 *  Same token rule as the daemon's `_MENTION_RE`; the user's bubble keeps the
 *  original words. Falls back to the original when nothing else is left. */
function stripMentions(text: string): string {
  const out = text
    .replace(/(?<![A-Za-z0-9._-])@(?:"[^"]+"|[A-Za-z0-9][A-Za-z0-9._-]*)\s*/g, "")
    .replace(/^[\s,:;\-—]+/, "")
    .trim();
  return out || text.trim();
}

/** The chat so far as the panel route reads it (v1.284.0): who said each
 *  line — the user, Iron Jarvis, or a panel agent by its key — and the words.
 *  Empty bubbles (hand-off markers, a stopped turn) carry nothing and are
 *  skipped; the daemon budgets the rest to the speaker's model. */
function panelHistoryOf(msgs: ChatMessage[]): { who: string; content: string }[] {
  const out: { who: string; content: string }[] = [];
  for (const m of msgs) {
    const content = (m.content || "").trim();
    if (!content) continue;
    out.push({ who: m.role === "user" ? "user" : m.panelWho || "jarvis", content });
  }
  return out;
}

/** The room this conversation's @-rounds live in — the last panel reply's
 *  room id — or "" (v1.285.0). */
function roomOf(msgs: ChatMessage[]): string {
  for (let i = msgs.length - 1; i >= 0; i--) {
    const r = msgs[i].panelThreadId;
    if (r) return r;
  }
  return "";
}

/** One entry of GET /agents/threads/{id} as this page reads it (v1.285.0). */
interface RoomEntry {
  who?: string;
  content?: string;
  at?: string;
  inbound?: boolean;
  kind?: string;
  documents?: string[];
}

/** The conversation as the Iron Jarvis lanes send it (v1.284.0). A panel
 *  agent's reply is LABELLED, so Jarvis reads it as what builder said — not as
 *  its own earlier words. Before this, a plain message after an @-round made
 *  Jarvis answer as if it had made the agent's claims. Both Jarvis-lane
 *  request builders (the turn and compaction) go through here. */
function toRequestMessages(msgs: ChatMessage[]): ChatRequestMessage[] {
  return msgs.map((m) => ({
    role: m.role,
    content:
      m.role === "assistant" && m.panelWho
        ? `[Reply from the agent ${agentDisplayName(m.panelWho)}, on the agent panel — not Iron Jarvis]\n${m.content}`
        : m.content,
  }));
}

/** Validate a wire `workflow_run` payload (v1.170.0, contract 2) into the
 *  message field, or null. A payload without a run id renders NOTHING — a chip
 *  that can never reconcile against a run record would spin forever, which is
 *  worse than no chip. Shared by BOTH chat lanes so they cannot drift. */
function workflowRunFrom(raw: unknown): { runId: string; name: string } | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as { run_id?: unknown; name?: unknown };
  const runId = typeof r.run_id === "string" ? r.run_id.trim() : "";
  if (!runId) return null;
  const name =
    typeof r.name === "string" && r.name.trim() ? r.name.trim() : "workflow";
  return { runId, name };
}

/** Decode the daemon's `doors` array (v1.199.0) — server truth carried onto
 *  the message verbatim. Entries are shape-checked only (the payload crosses
 *  a JSON boundary), never re-derived, sliced, or reordered: which doors a
 *  turn earned is the DAEMON's call (executed-ok tools, deduped, capped). */
function doorsFrom(raw: unknown): Door[] | null {
  if (!Array.isArray(raw)) return null;
  const out: Door[] = [];
  for (const item of raw) {
    if (!item || typeof item !== "object") continue;
    const d = item as { href?: unknown; label?: unknown };
    if (typeof d.href !== "string" || !d.href.trim()) continue;
    out.push({
      href: d.href,
      label: typeof d.label === "string" && d.label.trim() ? d.label : d.href,
    });
  }
  return out.length > 0 ? out : null;
}

/** Decode the daemon's `adapted` object (v1.202.0) — the envelope's own
 *  disclosure that this turn was bent to fit a measured-weak model, carried
 *  onto the message verbatim (shape-checked only; the daemon sends null on
 *  every unbent turn, and null decodes to null so nothing lands on the
 *  message). Shared by BOTH chat lanes so they cannot drift. */
function adaptedFrom(raw: unknown): TurnAdapted | null {
  if (!raw || typeof raw !== "object") return null;
  const a = raw as { model?: unknown; changes?: unknown };
  if (!Array.isArray(a.changes)) return null;
  const changes = a.changes.filter(
    (c): c is string => typeof c === "string" && c.trim().length > 0,
  );
  if (changes.length === 0) return null;
  return {
    ...(typeof a.model === "string" && a.model.trim()
      ? { model: a.model }
      : {}),
    changes,
  };
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
  // v1.329.0: the tone tokens, which every theme (Daylight included) re-inks.
  const tone = trimmed
    ? "text-tone-danger"
    : pct >= 75
      ? "text-tone-warn"
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
    // v1.329.0 (calm chat wave 5, G4): a calm notice in the warning TONE (a
    // hairline edge, a faint tint, ghost buttons), so Daylight re-inks it.
    <div
      data-testid="compaction-offer"
      className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-tone-warn/25 bg-tone-warn/[0.05] px-3 py-2 text-[12px] text-tone-warn"
    >
      <span>
        This conversation is using about <strong>{pct}%</strong> of this model&apos;s
        context window. Summarizing the earlier part keeps the thread going, and
        the full transcript is kept either way.
      </span>
      <span className="ml-auto flex items-center gap-1">
        <button
          type="button"
          onClick={onCompact}
          disabled={busy}
          className="rounded-lg px-2 py-1 font-medium text-tone-warn transition-colors hover:bg-tone-warn/10 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy ? "Summarizing…" : "Compact now"}
        </button>
        <button
          type="button"
          onClick={onDismiss}
          disabled={busy}
          className="rounded-lg px-2 py-1 text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-200 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50"
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
  /** v1.327.0 (calm chat W2-1): a turn is in flight for this chat right now
   *  (the daemon's live registry; an older daemon sends neither flag). */
  running?: boolean;
  /** v1.327.0: that turn is parked on the user (a card or an app's question). */
  waiting?: boolean;
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
  /** v1.263.0: the reasoning level ("low" | "medium" | "high"); "" or absent
   *  = the model's own default. */
  reasoning?: string;
  /** Permission posture for the mid-turn ask (v1.188.0). Absent = the
   *  default ("approve_for_me") — the daemon stores nothing for the default
   *  so a stray string never reloads as a posture nobody picked. */
  approval_mode?: string;
  /** v1.312.0 (W4-2): tools the user said "Allow for this conversation" to.
   *  A GRANT, not an arming: sent as `granted_tools` on every turn, and the
   *  daemon honours a name only when that turn armed it by some other path.
   *  Uncapped — it holds no arming slot. */
  granted_tools?: string[];
}

/** The three approval postures (v1.188.0). Since v1.327.0 the words and the
 *  values live in ONE place, `lib/permissionLevels.ts` (the composer's
 *  permission chip reads the same table): the VALUE strings are the wire
 *  vocabulary the daemon validates, and an unknown value falls back to the
 *  default ("approve_for_me"), never to the no-ask level. */
type ApprovalMode = PermissionMode;

const APPROVAL_MODE_KEY = "ij_chat_approval_mode";

const asApprovalMode: (raw: unknown) => ApprovalMode = asPermissionMode;

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
  /** Version stamp (CL3, v1.232.0): handed back as `if_updated_at` so a save
   *  from a stale window is refused (409) instead of clobbering. */
  updated_at?: string | null;
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
  /** The `updated_at` this window loaded/last saved (CL3): the daemon answers
   *  409 when the row is newer, and the save is rebased onto the server copy. */
  if_updated_at?: string;
}
interface ThreadSaveResult {
  id: string;
  title: string;
  updated_at?: string | null;
}

/** The mutable SAVE BOX for one conversation (see `saveTargetRef`). */
interface SaveTarget {
  id: string | null;
  /** A MESSAGING thread: the server owns its messages, saves no-op. */
  daemon?: boolean;
  /** The row's `updated_at` as this window last saw it (CL3). */
  updatedAt?: string;
  /** Length of the local array when it last matched the server — the
   *  bubbles after it are THIS window's own, appended on a 409 rebase. */
  syncedLen?: number;
  /** A 409 rebase: local messages `local` (by reference) stand for the
   *  server's `server` array in every later save from the same turn. */
  rebase?: { local: ChatMessage[]; server: ChatMessage[] };
  /** Sequence of the newest save queued for this box (CL2): the chip only
   *  retires when the LATEST save landed, not an older one. */
  seq?: number;
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

/** POST /documents/workfolder response (v1.244.0): the conversation's own
 *  folder, and where each attached upload was copied inside it. */
interface WorkfolderResult {
  path: string;
  created: boolean;
  files: { name: string; path: string; bytes: number; source: string }[];
  skipped?: { source: string; reason: string }[];
  note?: string;
}

// Attachment limits: keep uploads snappy and the /chat context sane.
/** v1.275.0: how many attachment uploads run at once (order is kept). */
const UPLOAD_CONCURRENCY = 3;
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
/** How many of the conversation's EARLIER files ride each turn (v1.251.0,
 *  C-01). The thread may hold 30; naming all of them in every prompt would
 *  spend the turn's budget on file paths, and the point is only that "it" and
 *  "that return" resolve — so the NEWEST few, which is what a follow-up
 *  almost always means. The daemon bounds this again on its side. */
const MAX_CARRIED_FILES = 8;
/** How many documents a folder needs before the app OFFERS to summarise the
 *  whole thing (v1.251.0, C-04). One or two files are quicker to attach and
 *  ask about — the batch pipeline earns its cost (about a model call per
 *  document) only once reading them by hand is the slow part. */
const BATCH_SUGGEST_MIN = 6;
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

/** Below Tailwind's `md` (768px): where the project drawer covers the whole
 *  chat. False when the browser cannot say (no matchMedia). */
function isPhoneWidth(): boolean {
  try {
    return typeof window !== "undefined" && !!window.matchMedia?.("(max-width: 767.98px)").matches;
  } catch {
    return false;
  }
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
  integrations: "Extensions (MCP)",
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

// Workspace panel persistence (chat mode): the chosen folder. Since v1.326.0
// the panel is a drawer opened on demand, so its open state is not restored
// (the old "ij_chat_workspace_open" key is no longer read or written).
const WORKSPACE_KEY = "ij_chat_workspace";
// The right-panel project selection persists across visits (like the folder).
const PROJECT_KEY = "ij_chat_project";
// v1.311.0: the conversation THIS WINDOW had open, so Chat → Overview → Chat
// reopens it instead of a blank new chat. sessionStorage on purpose: per
// window (two windows each keep their own place) and gone with the window, so
// a fresh launch still opens on a new conversation. Value: JSON
// {id, project} — the project lets a restore stay inside the scope the page
// is opening on (a ?project= link to another project never pulls a foreign
// conversation in).
const OPEN_THREAD_KEY = "ij_chat_open_thread";
// v1.311.0: the event filter, built ONCE so useEvents sees the same list on
// every render (see components/chat/chatEventTypes.ts).
const CHAT_EVENTS_OPTS = { types: CHAT_EVENT_TYPES };

/** The thread-list path for a scope — also the payload-cache key, so each
 *  project's list is remembered separately and one scope's list can never be
 *  painted for another. */
function threadsPath(scope: string | null): string {
  return scope ? `/chat/threads?project_id=${encodeURIComponent(scope)}` : "/chat/threads";
}

/** The project this visit opens on: a ?project= deep link, else the last-used
 *  choice. Read after mount only (never in a state initializer — the server
 *  render has no window). */
function wantedProjectId(): string | null {
  try {
    return (
      new URLSearchParams(window.location.search).get("project") ||
      window.localStorage.getItem(PROJECT_KEY) ||
      null
    );
  } catch {
    return null;
  }
}

function readOpenThread(): { id: string; project: string | null } | null {
  try {
    const raw = window.sessionStorage.getItem(OPEN_THREAD_KEY);
    if (!raw) return null;
    const v = JSON.parse(raw) as { id?: unknown; project?: unknown };
    if (typeof v?.id !== "string" || !v.id) return null;
    return { id: v.id, project: typeof v.project === "string" && v.project ? v.project : null };
  } catch {
    return null;
  }
}

function rememberOpenThread(id: string, project: string | null): void {
  try {
    window.sessionStorage.setItem(OPEN_THREAD_KEY, JSON.stringify({ id, project }));
  } catch {
    /* a blocked or full storage only costs the restore */
  }
}

function forgetOpenThread(): void {
  try {
    window.sessionStorage.removeItem(OPEN_THREAD_KEY);
  } catch {
    /* ignore */
  }
}
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

// stepLabel (the event -> progress-line renderer) lives in
// components/chat/stepLabel.ts since v1.202.0: an App Router page may not
// carry extra named exports (the .next/types check rejects them), and the
// function needed unit tests for the plan.* / envelope.adapted narration.

// The model <select> encodes the choice as `${provider}::${model}` (empty => let the
// server pick its default). Split it back out only when it carries both halves.
/** v1.263.0: the levels the composer may offer — the daemon's vocabulary. */
const REASONING_LEVELS = ["low", "medium", "high"];

/** v1.277.0: how many rows a typed model filter lists. */
const MODEL_FILTER_MAX = 12;

/** v1.278.0: a name for the turn about to run, so it can be steered (or stopped)
 *  by name. Chosen here — the daemon mints none (v1.241.0). */
function mintTurnId(): string {
  const rnd =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : Math.random().toString(36).slice(2);
  return `chat-${Date.now().toString(36)}-${rnd}`;
}

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

// ------------------------------------------------------------------ markdown
// The renderer itself lives in components/Markdown.tsx (v1.230.0, U2) so the
// session summary and project rows render the same way chat does.

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
        className={`${REPLY_ACTION_SQUARE} hover:text-accent-soft`}
      >
        {state === "busy" ? (
          <Loader2 size={12} className="animate-spin" />
        ) : state === "done" ? (
          <Check size={12} className="text-tone-success" />
        ) : (
          <BookmarkPlus size={12} />
        )}
      </button>
      {err && (
        <span className="truncate text-[12px] text-tone-danger">{err}</span>
      )}
    </span>
  );
}


// ------------------------------------------------------------------- bubbles

/** Calm chat W1-4: a reply is PROSE on the page. Tables inside it (markdown
 *  tables, a chart's "Show as table") read as part of the text: hairline rules
 *  under each row, no grid, no filled header. Scoped here so the same
 *  markdown keeps its grid everywhere else in the app. */
const REPLY_PROSE =
  "min-w-0 text-sm leading-relaxed text-zinc-200 " +
  "[&_table]:text-[13px] " +
  "[&_th]:border-x-0 [&_th]:border-t-0 [&_th]:border-white/[0.16] [&_th]:bg-transparent [&_th]:py-1.5 [&_th]:pl-0 [&_th]:pr-3 [&_th]:font-medium [&_th]:text-zinc-400 " +
  "[&_td]:border-x-0 [&_td]:border-t-0 [&_td]:border-white/[0.08] [&_td]:py-1.5 [&_td]:pl-0 [&_td]:pr-3";

function Bubble({ role, children }: { role: ChatMessage["role"]; children: ReactNode }) {
  // Calm chat W1-4 (v1.326.0): no avatars and no box around a reply. Your own
  // message is a soft tinted bubble on the right; the reply is prose in the
  // same centred column. Side and fill already say who is speaking.
  if (role === "user")
    return (
      <div className="flex justify-end">
        <div
          data-testid="user-bubble"
          className="min-w-0 max-w-[85%] whitespace-pre-wrap break-words rounded-[20px] bg-accent/[0.1] px-4 py-2.5 text-sm leading-relaxed text-zinc-100 [overflow-wrap:anywhere] sm:max-w-[72%]"
        >
          {children}
        </div>
      </div>
    );
  return (
    <div data-testid="reply-prose" className={REPLY_PROSE}>
      {children}
    </div>
  );
}

/** v1.315.0 (project-tabs-shift-and-not-tabs): the project's views as a REAL
 *  tablist: role=tab, aria-selected, aria-controls, roving tabindex. It used
 *  to be a row of chip buttons ABOVE the chat card that pushed the card ~52px
 *  down whenever the open chat belonged to a project. Since v1.326.0 it is
 *  plain text tabs in the chat TOP BAR, which every view shares, so there is
 *  one tablist on screen whatever is showing and nothing shifts. Arrow keys move focus only (wrapping); a click, Enter or Space selects —
 *  manual activation, because selecting mounts a whole surface. */
/** The project drawer's own tabs (v1.329.0): only what the top bar does NOT
 *  hold. Tasks / Board / Media live in ProjectViewTabs below. */
const PROJECT_PANEL_TABS = [
  { value: "files", label: "Files" },
  { value: "knowledge", label: "Knowledge" },
] as const;
const PROJECT_VIEWS = ["chat", "tasks", "board", "media"] as const;
const PROJECT_VIEW_CHAT_ID = "project-view-chat";
const PROJECT_VIEW_SURFACE_ID = "project-view-surface";
function ProjectViewTabs({
  view,
  onSelect,
}: {
  view: "chat" | ProjectSurfaceView;
  onSelect: (v: "chat" | ProjectSurfaceView) => void;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  function onKeyDown(e: React.KeyboardEvent<HTMLButtonElement>, i: number) {
    let next = -1;
    if (e.key === "ArrowRight") next = (i + 1) % PROJECT_VIEWS.length;
    else if (e.key === "ArrowLeft") next = (i - 1 + PROJECT_VIEWS.length) % PROJECT_VIEWS.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = PROJECT_VIEWS.length - 1;
    if (next < 0) return;
    e.preventDefault();
    refs.current[next]?.focus();
  }
  return (
    <div role="tablist" aria-label="Project views" className="flex items-center gap-2">
      {PROJECT_VIEWS.map((v, i) => {
        const selected = view === v;
        return (
          <button
            key={v}
            ref={(el) => {
              refs.current[i] = el;
            }}
            type="button"
            role="tab"
            aria-selected={selected}
            aria-controls={v === "chat" ? PROJECT_VIEW_CHAT_ID : PROJECT_VIEW_SURFACE_ID}
            tabIndex={selected ? 0 : -1}
            onClick={() => onSelect(v)}
            onKeyDown={(e) => onKeyDown(e, i)}
            // v1.326.0 (calm chat): plain text tabs in the top bar, the open
            // one in ink and the rest muted; no underline, no chip.
            className={`rounded-md px-1.5 py-1 text-[13px] capitalize transition-colors ${
              selected
                ? "font-medium text-zinc-100"
                : "text-zinc-500 hover:text-zinc-200"
            }`}
          >
            {v}
          </button>
        );
      })}
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

// Calm chat W1-5 (v1.326.0): the live tool calls are grey one-line rows now
// (LiveToolRows in components/chat/WorkLine.tsx, memoized there for the same
// per-frame reason this list was: v1.257.0 S-02), never a stack of boxes.

/** Drop the agent-lane wait mark (v1.226.0) — every path that ENDS a turn
 *  (finalize, Stop, a hard finalize failure) saves through this, so the mark
 *  never outlives the wait it describes. */
function stripAwaiting(msgs: ChatMessage[]): ChatMessage[] {
  if (!msgs.some((m) => m.awaitingSession)) return msgs;
  return msgs.map((m) => {
    if (!m.awaitingSession) return m;
    const { awaitingSession: _drop, ...rest } = m;
    return rest;
  });
}

/** The session a stored thread was still waiting on when it was last saved
 *  (v1.226.0) — only the LAST bubble counts: a reply after it means the wait
 *  already ended. */
function pendingSessionOf(msgs: ChatMessage[]): string | null {
  const last = msgs[msgs.length - 1];
  return last && typeof last.awaitingSession === "string" && last.awaitingSession
    ? last.awaitingSession
    : null;
}

/** The agent run's live text, subscribed HERE instead of on the page.
 *
 *  v1.257.0 (S-02): the agent lane's counterpart to <LiveReply>. Every token
 *  used to re-render this 8,177-line page and fire its scroll effect, because
 *  `runStream.text` was page state and a dependency of that effect. The text
 *  now lives in the run's store, this component is its only subscriber, and the
 *  growth reports itself for the scroll exactly as the chat lane does.
 *
 *  Renders precisely what the inline expression rendered before: StreamingText
 *  when there is text, nothing when there is not.
 */
function AgentLiveText({ stream, onGrow }: { stream: UseRunStream; onGrow: () => void }) {
  const text = useLiveText(stream);
  useEffect(() => {
    if (text) onGrow();
  }, [text, onGrow]);
  return text ? <StreamingText content={text} /> : null;
}

/** Streamed assistant markdown with a blinking caret pinned after the last line
 *  (a `::after` on the final block, so it sits inline with the running text). */
function StreamingText({ content }: { content: string }) {
  // v1.250.0 (S-03): re-parse only what is still GROWING. Everything before
  // the last blank line that sits outside a code fence is finished markdown —
  // it goes through MemoMarkdown, so a frame's flush re-parses the tail alone
  // instead of the whole reply (cost was O(reply) per update).
  //
  // The split is skipped unless the tail is non-empty: the caret is an
  // `::after` on the LAST child of this wrapper, so an empty tail would hang
  // it on a blank line. Blocks that continue across the boundary (a list, a
  // table) keep their own paragraph, because the cut is always a blank line.
  const cut = settledSplit(content);
  return (
    <div className="[&>*:last-child]:after:ml-0.5 [&>*:last-child]:after:inline-block [&>*:last-child]:after:h-[0.95em] [&>*:last-child]:after:w-[2px] [&>*:last-child]:after:translate-y-[1px] [&>*:last-child]:after:animate-caret [&>*:last-child]:after:rounded-full [&>*:last-child]:after:bg-accent-soft [&>*:last-child]:after:align-baseline [&>*:last-child]:after:content-['']">
      {cut > 0 && <MemoMarkdown content={content.slice(0, cut)} />}
      <Markdown content={cut > 0 ? content.slice(cut) : content} />
    </div>
  );
}

/* ===================================================================== *
 * THE COMPOSER (v1.250.0, S-05)
 *
 * Five values — text, caret, the two dismissals, the highlighted skill row —
 * live in a store instead of the page (lib/composerStore.ts), and the four
 * subtrees that actually read them subscribe here. The page keeps the chrome
 * it always had: the "+" menu, the project control, the mic. Nothing about
 * the markup changes; what changes is who re-renders when a key goes down.
 * ===================================================================== */

/**
 * The TEXT half of picking a skill, in ONE place (v1.250.0, S-05).
 *
 * Both the "/" dropdown's click and the textarea's Enter land here, because
 * they must do the same thing: splice out ONLY the "/token" being typed and
 * keep the rest of the message (v1.105.0 — clearing would eat a prompt the
 * user had already written), then put the caret where the token was. The
 * page's half (arm the chip, persist it with the thread) is its own callback.
 */
function applySkillPick(
  store: ComposerStore,
  tok: ReturnType<typeof slashTokenAt>,
  inputRef: React.RefObject<HTMLTextAreaElement | null>,
): void {
  const next = spliceToken(store.get().text, tok);
  const pos = tok ? tok.start : 0;
  store.setText(next, pos);
  store.setSlashDismissed(false);
  inputRef.current?.focus();
  // The DOM selection has to be fixed after React commits the new value, or
  // the caret lands at the end of the spliced text and the next thing typed
  // goes to the wrong place.
  requestAnimationFrame(() => {
    const el = inputRef.current;
    if (el) el.selectionStart = el.selectionEnd = pos;
  });
}

/** The textarea. The four caret handlers are the ones v1.105.0 measured as
 *  necessary (onSelect alone never fires for a collapsed caret), and the
 *  auto-grow lives here too — it keys off the text, so it belongs with it. */
/** v1.315.0 (phone-composer-cramped): true at Tailwind's `sm` (640px) and up.
 *  Read INSIDE the memoized composer, never on the page (v1.250.0: the page
 *  must not re-render for the composer). It starts true — the server render
 *  and a browser without matchMedia (jsdom) keep the full keyboard hints — and
 *  follows the media query after mount. */
const WIDE_QUERY = "(min-width: 640px)";
function useWideScreen(): boolean {
  const [wide, setWide] = useState(true);
  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia(WIDE_QUERY);
    const sync = () => setWide(mq.matches);
    sync();
    if (typeof mq.addEventListener === "function") {
      mq.addEventListener("change", sync);
      return () => mq.removeEventListener("change", sync);
    }
    mq.addListener?.(sync);
    return () => mq.removeListener?.(sync);
  }, []);
  return wide;
}

const ComposerInput = memo(function ComposerInput({
  store,
  inputRef,
  busy,
  skills,
  onSend,
  onStop,
  onSteer,
  onQueue,
  onOpened,
  onPickSkill,
  onTyped,
  onPasteFiles,
  talkingTo = "",
  atKeys,
}: {
  store: ComposerStore;
  inputRef: React.RefObject<HTMLTextAreaElement | null>;
  busy: boolean;
  skills: SkillOption[] | null;
  /** v1.328.0: while the "@" menu is open it owns ↑↓, Enter and Esc. The
   *  picker puts its handler here (null while closed); true = handled. */
  atKeys?: React.RefObject<AtKeyHandler | null>;
  onSend: (text: string) => void;
  onStop: () => void;
  /** v1.284.0: the agent(s) the conversation is with — the placeholder says
   *  so, because a box that still reads "Message Iron Jarvis" while the reply
   *  will come from builder is the confusion the strip above it exists to end. */
  talkingTo?: string;
  /** v1.278.0: Enter while a turn runs sends the box as a STEER note — the
   *  turn reads it at its next step. Absent, Enter mid-turn does nothing. */
  onSteer?: (text: string) => void;
  /** v1.325.0: Ctrl+Enter while a turn runs holds the box (words and files)
   *  and sends it after the reply finishes. */
  onQueue?: (text: string) => void;
  /** Called when a "/" token opens — the page fetches the skill catalog once
   *  (v1.250.0, S-05: it used to be an effect on a page-level `slashActive`,
   *  and the page no longer watches the text). Idempotent on its own side. */
  onOpened: () => void;
  /** The page's half of picking a skill: arm the chip, persist it with the
   *  thread. The TEXT half (splice the "/token", place the caret) is the
   *  store's and stays here. */
  onPickSkill: (name: string) => void;
  /** A real keystroke — the page uses it to retire the voice auto-send, which
   *  must never fire for something the user typed by hand. */
  onTyped: () => void;
  /** v1.275.0: files on the clipboard (a screenshot, a copied file) become
   *  attachments exactly as a drop does; text pastes fall through untouched. */
  onPasteFiles: (files: File[]) => void;
}) {
  const composerState = useComposer(store);
  const { text, caret } = composerState;
  // v1.315.0: a touch screen has no Enter/Shift/Esc keys to hint at, and on a
  // 390px phone the hints wrapped the placeholder onto three lines.
  const wide = useWideScreen();

  // Auto-grow to fit multi-line text (up to ~1/4 viewport) and shrink back
  // when it is cleared on send, so a Shift+Enter draft is never trapped in
  // one row. Runs on every text change, including the programmatic reset.
  useLayoutEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [text, inputRef]);

  // The "/" menu's one open rule (lib/composerMenus), as the picker reads it.
  const slashToken = slashMenuOpen(composerState, busy) ? slashTokenAt(text, caret) : null;
  const slashActive = slashToken !== null;
  const slashQuery = slashToken?.query ?? "";
  // Ask the page for the catalog the first time a "/" token opens.
  const openedRef = useRef(onOpened);
  openedRef.current = onOpened;
  useEffect(() => {
    if (slashActive) openedRef.current();
  }, [slashActive]);
  const skillMatches = useMemo(() => {
    if (!slashActive) return [] as SkillOption[];
    const list = skills ?? [];
    return slashQuery
      ? list.filter(
          (s) =>
            s.name.toLowerCase().includes(slashQuery) ||
            (s.description || "").toLowerCase().includes(slashQuery),
        )
      : list;
  }, [slashActive, slashQuery, skills]);

  /** Select a skill: chip on, the "/token" being typed consumed — ONLY that
   *  token, so a prompt already written is never eaten (v1.105.0). */
  function pickSkill(name: string) {
    const tok = slashToken;
    const next = spliceToken(text, tok);
    const pos = tok ? tok.start : 0;
    store.setText(next, pos);
    store.setSlashDismissed(false);
    onPickSkill(name);
    inputRef.current?.focus();
    // The DOM selection has to be fixed after React commits the new value, or
    // the caret lands at the end and the next thing typed goes elsewhere.
    requestAnimationFrame(() => {
      const el = inputRef.current;
      if (el) el.selectionStart = el.selectionEnd = pos;
    });
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    // Ignore keystrokes mid-IME-composition (CJK / accented input): Enter is
    // confirming a candidate, not sending a half-finished message.
    // v1.322.0: Safari reports an IME commit as keyCode 229 with
    // isComposing already false (borrowed from assistant-ui's isCompositionKey).
    if (e.nativeEvent.isComposing || e.keyCode === 229) return;
    // While the "/" skill dropdown is open it owns the navigation keys.
    if (slashActive) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        store.setSkillIndex((i) => Math.min(i + 1, Math.max(skillMatches.length - 1, 0)));
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        store.setSkillIndex((i) => Math.max(i - 1, 0));
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        store.setSlashDismissed(true);
        return;
      }
      // Enter picks the highlighted skill; with no match it falls through and
      // sends the literal "/…" text like any other message.
      if (e.key === "Enter" && !e.shiftKey && skillMatches.length > 0) {
        e.preventDefault();
        const idx = Math.min(store.get().skillIndex, skillMatches.length - 1);
        pickSkill(skillMatches[idx].name);
        return;
      }
    }
    // v1.328.0: the "@" menu (agents, chats, app files) — it yields to the
    // "/" menu, so it is only asked when that one is closed.
    if (!slashActive && atKeys?.current?.(e)) return;
    // Escape cancels an in-flight turn (keyboard "Stop") without leaving the composer.
    if (e.key === "Escape" && busy) {
      e.preventDefault();
      onStop();
      return;
    }
    // Enter sends; Shift+Enter inserts a newline. v1.278.0: while a turn is
    // running, Enter STEERS it instead — the note reaches the turn at its
    // next step — and the send path is not entered (it refuses mid-turn).
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (busy && onQueue && (e.ctrlKey || e.metaKey)) {
        onQueue(store.get().text);
        return;
      }
      if (busy && onSteer) {
        onSteer(store.get().text);
        return;
      }
      onSend(store.get().text);
    }
  }

  return (
    <textarea
      ref={inputRef}
      value={text}
      onChange={(e) => {
        onTyped(); // typed — never auto-send
        store.type(e.target.value, e.target.selectionStart ?? e.target.value.length);
      }}
      // Caret moves that onChange never sees: arrow keys, clicking into the
      // middle of the text, Home/End, drag-select. All four are wired because
      // React's onSelect ALONE does not fire for a collapsed caret — measured
      // in a real browser, the DOM selectionStart went 21 -> 8 on ArrowLeft
      // while the tracked value stayed at 21, so the picker refused to reopen
      // when you moved back into an earlier "/word". keyup and click are the
      // ones that actually fire for that; onSelect is kept for drag-selection
      // and onFocus for tabbing back in.
      onKeyUp={(e) => store.setCaret(e.currentTarget.selectionStart ?? 0)}
      onClick={(e) => store.setCaret(e.currentTarget.selectionStart ?? 0)}
      onFocus={(e) => store.setCaret(e.currentTarget.selectionStart ?? 0)}
      onSelect={(e) => store.setCaret(e.currentTarget.selectionStart ?? 0)}
      onKeyDown={onKeyDown}
      onPaste={(e) => {
        // v1.275.0: Ctrl+V of a screenshot or a copied file. There was no
        // paste handler at all — the most-used attach path in every other
        // chat app meant "save to disk, then + → Attach" here.
        const files = Array.from(e.clipboardData?.files ?? []);
        if (files.length === 0) return; // plain text: the browser's own paste
        e.preventDefault();
        onPasteFiles(files);
      }}
      autoFocus
      rows={1}
      aria-label="Message"
      // v1.326.0 (calm chat): the keys moved to the quiet line under the
      // card (from sm up), so the box only says who it talks to.
      placeholder={
        busy && onSteer
          ? "Steer Jarvis mid-turn…"
          : talkingTo
            ? wide
              ? `Message ${talkingTo}…  (@ to bring in someone else · Back to Jarvis above)`
              : `Message ${talkingTo}…`
            : "Message Iron Jarvis…"
      }
      // v1.326.0 (calm chat W1-2): the box is the top of the composer card,
      // not a field of its own: no border, no fill, no focus ring (the caret
      // is the cue; the card's edge does not change on focus). It takes the
      // whole width above the toolbar at every size.
      className="block max-h-40 min-h-[2.75rem] w-full resize-none bg-transparent px-[18px] pb-1 pt-3 text-sm leading-6 text-zinc-100 caret-accent outline-none placeholder:text-zinc-500"
    />
  );
});

/** The "/" skill picker — floats above the composer. Renders from the same
 *  token rule as the textarea's own; both read the store, so they cannot
 *  disagree about which "/word" the caret is in. */
const SlashPicker = memo(function SlashPicker({
  store,
  busy,
  skills,
  inputRef,
  onOpened,
  onPick,
  prompts,
  onPickPrompt,
}: {
  store: ComposerStore;
  busy: boolean;
  skills: SkillOption[] | null;
  inputRef: React.RefObject<HTMLTextAreaElement | null>;
  /** Same one-shot catalog load the textarea asks for — whichever notices the
   *  open token first wins, and the page's ref makes the other a no-op. */
  onOpened: () => void;
  onPick: (name: string) => void;
  /** v1.324.0: the prompts the user's apps offer (null = not loaded yet). */
  prompts: PackPrompt[] | null;
  /** Picking one consumes the "/token" and opens its little form. */
  onPickPrompt: (p: PackPrompt) => void;
}) {
  const composerState = useComposer(store);
  const { text, caret, skillIndex } = composerState;
  // One open rule (lib/composerMenus) shared with the "@" menu and the Jump
  // to latest pill, so a change to it cannot leave the pill out of step.
  const slashToken = slashMenuOpen(composerState, busy) ? slashTokenAt(text, caret) : null;
  const slashQuery = slashToken?.query ?? "";
  const openedRef = useRef(onOpened);
  openedRef.current = onOpened;
  useEffect(() => {
    if (slashToken !== null) openedRef.current();
  }, [slashToken !== null]); // eslint-disable-line react-hooks/exhaustive-deps
  // Keep the highlighted row pinned to the top as the query changes — moved
  // here with the query it watches (v1.250.0, S-05).
  useEffect(() => {
    store.setSkillIndex(0);
  }, [slashQuery, slashToken !== null, store]); // eslint-disable-line react-hooks/exhaustive-deps
  if (slashToken === null) return null;
  const list = skills ?? [];
  // ALL matches — the dropdown scrolls. (An 8-row cap made the picker look
  // like it wasn't loading the whole skill library.)
  const skillMatches = slashQuery
    ? list.filter(
        (s) =>
          s.name.toLowerCase().includes(slashQuery) ||
          (s.description || "").toLowerCase().includes(slashQuery),
      )
    : list;
  const promptMatches = (prompts ?? []).filter(
    (p) =>
      !slashQuery ||
      p.name.toLowerCase().includes(slashQuery) ||
      (p.title || "").toLowerCase().includes(slashQuery) ||
      (p.description || "").toLowerCase().includes(slashQuery),
  );
  function pickPrompt(p: PackPrompt) {
    const cur = store.get();
    store.setText(spliceToken(cur.text, slashToken), slashToken ? slashToken.start : 0);
    store.setSlashDismissed(false);
    onPickPrompt(p);
  }

  return (
    <div className="absolute bottom-full left-3 right-3 z-20 mb-2 overflow-hidden rounded-xl border border-white/10 bg-zinc-900 shadow-lg shadow-black/40">
      {promptMatches.length > 0 && (
        <div role="listbox" aria-label="Prompts from your apps" className="max-h-40 overflow-y-auto border-b border-white/[0.06] p-1">
          <div className="px-2.5 pb-1 pt-1.5 text-[10px] font-semibold uppercase tracking-wide text-zinc-500">
            From your apps
          </div>
          {promptMatches.map((p) => (
            <button
              key={`${p.pack}/${p.name}`}
              type="button"
              role="option"
              aria-selected={false}
              data-testid="pack-prompt-option"
              onClick={() => pickPrompt(p)}
              title={p.description}
              className="flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-zinc-300 transition-colors hover:bg-accent/[0.12] hover:text-accent-soft"
            >
              <Blocks size={12} className="shrink-0 text-accent-soft/70" />
              <span className="shrink-0 text-[12px]">{p.title || p.name}</span>
              <span className="shrink-0 text-[10px] text-zinc-600">{p.pack}</span>
              <span className="truncate text-[11px] text-zinc-500">{p.description}</span>
            </button>
          ))}
        </div>
      )}
      {skills === null ? (
        <p className="px-3 py-2.5 text-xs text-zinc-500">Loading skills…</p>
      ) : skillMatches.length === 0 ? (
        <p className="px-3 py-2.5 text-xs text-zinc-500">no matching skill</p>
      ) : (
        <div role="listbox" aria-label="Skills" className="max-h-72 overflow-y-auto p-1">
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
                if (i === skillIndex) el?.scrollIntoView({ block: "nearest" });
              }}
              onClick={() => onPick(s.name)}
              onMouseEnter={() => store.setSkillIndex(i)}
              title={s.description}
              className={`flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left transition-colors ${
                i === skillIndex ? "bg-accent/[0.12] text-accent-soft" : "text-zinc-300"
              }`}
            >
              <Sparkles size={12} className="shrink-0 text-accent-soft/70" />
              <span className="shrink-0 font-mono text-[12px]">{s.name}</span>
              <span className="truncate text-[11px] text-zinc-500">{s.description}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
});

/** v1.329.0: one heading for every "@" menu section, calm and in sentence
 *  case ("Agents", "Files", "Chats"), with a quiet plain hint beside it. */
function AtSectionHead({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="flex items-baseline gap-2 px-2.5 pb-1 pt-1.5">
      <span className="shrink-0 text-[11px] font-medium text-zinc-400">{title}</span>
      {hint && <span className="min-w-0 truncate text-[11px] text-zinc-600">{hint}</span>}
    </div>
  );
}

/** v1.328.0: the "@" menu's key handler. The picker owns it (it knows the
 *  rows); the textarea's keydown asks it first while the menu is open.
 *  Returns true when the key was handled. */
type AtKeyHandler = (e: React.KeyboardEvent<HTMLTextAreaElement>) => boolean;

/** The "@" picker: agents (v1.150.0), app files (v1.324.0) and saved chats
 *  (v1.328.0). Same shape as the "/" picker — one affordance grammar for
 *  both — and it yields to it when both could open. ↑↓ move across every
 *  section in the order drawn, Enter picks, Esc closes. */
const AtPicker = memo(function AtPicker({
  store,
  busy,
  mentionable,
  resources,
  onOpened,
  onPickResource,
  inputRef,
  openChatId,
  chatRefs,
  onPickChat,
  keysRef,
  filesRoot,
  attachedPaths,
  onPickEntry,
  chatProjectName,
}: {
  store: ComposerStore;
  busy: boolean;
  mentionable: MentionableAgent[] | null;
  /** v1.324.0: files and records the user's apps offer (null = not loaded). */
  resources: PackResource[] | null;
  /** The "@" token opened — the page loads the apps' list (cached). */
  onOpened: () => void;
  /** Picking one consumes the "@token" and attaches it to the next message. */
  onPickResource: (r: PackResource) => void;
  inputRef: React.RefObject<HTMLTextAreaElement | null>;
  /** v1.328.0: the saved chat this page has open — never offered. */
  openChatId: string | null;
  /** v1.328.0: the chats already picked (not offered again). */
  chatRefs: ChatRefPick[];
  /** v1.328.0: picking a chat adds a chip; the message text is untouched
   *  apart from the "@token" being consumed. */
  onPickChat: (c: ChatRefPick) => void;
  /** v1.328.0: where this picker puts its key handler for the textarea. */
  keysRef: React.RefObject<AtKeyHandler | null>;
  /** v1.329.0: the active project's folder (null = no project, or its folder
   *  is missing): its files and folders are offered under "Files". */
  filesRoot: string | null;
  /** v1.329.0: paths already attached to the next message (not offered). */
  attachedPaths: readonly string[];
  /** v1.329.0: a file is attached like "+ Attach"; a folder becomes the
   *  working folder like "+ Choose a working folder". */
  onPickEntry: (e: ProjectEntry) => void;
  /** v1.329.0: the project a saved chat belongs to, by name, when known. */
  chatProjectName: (c: ChatRefPick) => string | null;
}) {
  const composerState = useComposer(store);
  const { text, caret, atDismissed } = composerState;
  const atToken = busy || atDismissed ? null : tokenAt(text, caret, "@");
  // v1.329.0: one definition with the "Jump to latest" pill, which hides
  // while this menu (or the "/" one) is open (lib/composerMenus).
  const slashOpen = slashMenuOpen(composerState, busy);
  const open = atMenuOpen(composerState, busy);
  const openedRef = useRef(onOpened);
  openedRef.current = onOpened;
  useEffect(() => {
    if (open) openedRef.current();
  }, [open]);
  const typed = open ? (atToken?.query ?? "") : "";
  // Asked at once each time the menu opens, then again as the query changes.
  const chatRows = useChatRefSearch(open, typed, openChatId);
  // v1.329.0: the project folder's files, listed once each time the menu
  // opens (typing narrows that list).
  const projectListing = useProjectFiles(open, filesRoot);
  // The highlighted row, across every section; back to the top as the query
  // changes or the menu reopens.
  const [active, setActive] = useState(0);
  const activeRef = useRef(0);
  activeRef.current = active;
  useEffect(() => {
    setActive(0);
  }, [typed, open]);
  // v1.328.0 fix round: the menu opens where it has room. On a NEW chat the
  // card is centred and the section that clips the menu starts just above it,
  // so a tall menu opening upward lost its first rows off the top. The fit
  // measures the card against that edge (before paint) and picks the side and
  // a height that keep every row reachable by scrolling inside the menu.
  const menuRef = useRef<HTMLDivElement | null>(null);
  const fit = useAtMenuFit(menuRef, open, `${chatRefs.length}|${text.length}`);
  if (atToken === null || slashOpen) {
    keysRef.current = null;
    return null;
  }
  const atQuery = atToken.query ?? "";
  const list = mentionable ?? [];
  const agentMatches = atQuery
    ? list.filter(
        (a) =>
          a.mention.toLowerCase().includes(atQuery) ||
          (a.description || "").toLowerCase().includes(atQuery),
      )
    : list;
  const resourceMatches = (resources ?? [])
    .filter(
      (r) =>
        !atQuery ||
        (r.name || "").toLowerCase().includes(atQuery) ||
        (r.title || "").toLowerCase().includes(atQuery) ||
        r.uri.toLowerCase().includes(atQuery),
    )
    .slice(0, 50);
  const chatMatches = visibleChatRefRows(chatRows, chatRefs, openChatId, atQuery);
  // v1.329.0: the project's files and folders matching what is typed.
  const fileMatches = filesRoot
    ? matchProjectEntries(projectListing, atQuery, attachedPaths, Infinity)
    : [];
  // With full room each section keeps its own short scroll, so agents and
  // chats are both in view at once. When the fit squeezed the menu, those
  // inner scrolls would nest inside a small one: the sections drop their caps
  // and the menu scrolls as one list.
  const roomy = !fit || fit.maxHeight >= AT_MENU_ROOMY_PX;
  // ... and then the agents alone would fill it (a new chat, where pointing at
  // an earlier chat is most likely), with the Chats section below the fold.
  // So while a section further down matches (Files, Chats), the sections
  // above it keep only their first rows and say how many more there are;
  // typing narrows them as before.
  const squeezed = !roomy && fit ? squeezedLeadRows(fit.maxHeight) : Infinity;
  const lead = chatMatches.length > 0 || fileMatches.length > 0 ? squeezed : Infinity;
  const fileLead = Math.min(chatMatches.length > 0 ? squeezed : Infinity, AT_FILES_ROWS_MAX);
  const resourceShown = resourceMatches.slice(0, lead);
  const agentShown = mentionable === null ? [] : agentMatches.slice(0, lead);
  const fileShown = fileMatches.slice(0, fileLead);
  const resourceMore = resourceMatches.length - resourceShown.length;
  const agentMore = mentionable === null ? 0 : agentMatches.length - agentShown.length;
  const fileMore = fileMatches.length - fileShown.length;
  const tok = atToken;

  function pickResource(r: PackResource) {
    const cur = store.get();
    store.setText(spliceToken(cur.text, tok), tok.start);
    store.setAtDismissed(false);
    onPickResource(r);
  }
  function pickAgent(a: MentionableAgent) {
    const cur = store.get();
    store.setText(spliceToken(cur.text, tok) + `@${a.mention} `);
    store.setAtDismissed(false);
  }
  /** A chat becomes a chip, never words in the message: only the "@token"
   *  is consumed, the caret goes back where it was, the box keeps focus. */
  function pickChat(c: ChatRefPick) {
    const cur = store.get();
    const pos = tok.start;
    store.setText(spliceToken(cur.text, tok), pos);
    store.setAtDismissed(false);
    onPickChat(c);
    inputRef.current?.focus();
    requestAnimationFrame(() => {
      const el = inputRef.current;
      if (el) el.selectionStart = el.selectionEnd = pos;
    });
  }
  /** v1.329.0: a file or folder, like a chat, is never words in the message:
   *  only the "@token" is consumed and the caret goes back where it was. */
  function pickEntry(e: ProjectEntry) {
    const cur = store.get();
    const pos = tok.start;
    store.setText(spliceToken(cur.text, tok), pos);
    store.setAtDismissed(false);
    onPickEntry(e);
    inputRef.current?.focus();
    requestAnimationFrame(() => {
      const el = inputRef.current;
      if (el) el.selectionStart = el.selectionEnd = pos;
    });
  }

  // Every row in the order it is drawn — the keys walk this one list.
  const rows: { key: string; pick: () => void }[] = [
    ...resourceShown.map((r) => ({ key: `res:${r.pack}/${r.uri}`, pick: () => pickResource(r) })),
    ...agentShown.map((a) => ({
      key: `agent:${a.name}`,
      pick: () => pickAgent(a),
    })),
    ...fileShown.map((f) => ({ key: `${f.kind}:${f.path}`, pick: () => pickEntry(f) })),
    ...chatMatches.map((c) => ({ key: `chat:${c.id}`, pick: () => pickChat(c) })),
  ];
  const activeIdx = rows.length ? Math.min(active, rows.length - 1) : -1;
  const rowIndex = new Map(rows.map((r, i) => [r.key, i]));
  const rowProps = (key: string) => {
    const i = rowIndex.get(key) ?? -1;
    const on = i === activeIdx;
    return {
      "aria-selected": on,
      "data-active": on ? "true" : undefined,
      ref: (el: HTMLButtonElement | null) => {
        if (on) el?.scrollIntoView({ block: "nearest" });
      },
      onMouseEnter: () => setActive(i),
      className: `flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left transition-colors hover:bg-accent/[0.12] hover:text-accent-soft ${
        on ? "bg-accent/[0.12] text-accent-soft" : "text-zinc-300"
      }`,
    };
  };
  keysRef.current = (e) => {
    const n = rows.length;
    if (e.key === "Escape") {
      e.preventDefault();
      store.setAtDismissed(true);
      return true;
    }
    if (n === 0) return false; // nothing to move over or pick: Enter sends
    const cur = Math.min(activeRef.current, n - 1);
    if (e.key === "ArrowDown") {
      e.preventDefault();
      const next = Math.min(cur + 1, n - 1);
      activeRef.current = next;
      setActive(next);
      return true;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      const next = Math.max(cur - 1, 0);
      activeRef.current = next;
      setActive(next);
      return true;
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      rows[cur].pick();
      return true;
    }
    return false;
  };
  const chatsFull = chatRefs.length >= CHAT_REFS_MAX;
  const below = fit?.side === "below";
  const sectionCap = (cls: string) => (roomy ? `${cls} overflow-y-auto` : "");
  const ageNow = Date.now();
  // v1.329.0: each chat row's quiet second part (its project, else the day).
  const chatSecondary = chatRefSecondary(chatMatches, chatProjectName, ageNow);

  return (
    <div
      ref={menuRef}
      data-testid="at-menu"
      data-side={below ? "below" : "above"}
      style={fit ? { maxHeight: fit.maxHeight } : undefined}
      className={`absolute left-3 right-3 z-20 ${
        below ? "top-full mt-2" : "bottom-full mb-2"
      } max-h-[min(28rem,60vh)] overflow-y-auto rounded-xl border border-white/10 bg-zinc-900 shadow-lg shadow-black/40`}
    >
      {resourceMatches.length > 0 && (
        <div role="listbox" aria-label="From your apps" className={`${sectionCap("max-h-40")} border-b border-white/[0.06] p-1`}>
          <AtSectionHead title="From your apps" hint="Attach to your next message" />
          {resourceShown.map((r) => (
            <button
              key={`${r.pack}/${r.uri}`}
              type="button"
              role="option"
              data-testid="pack-resource-option"
              {...rowProps(`res:${r.pack}/${r.uri}`)}
              onClick={() => pickResource(r)}
              title={r.description || r.uri}
            >
              <FileText size={12} className="shrink-0 text-accent-soft/70" />
              <span className="shrink-0 text-[12px]">{r.title || r.name || r.uri}</span>
              <span className="shrink-0 text-[10px] text-zinc-600">{r.pack}</span>
              <span className="truncate text-[11px] text-zinc-500">{r.description}</span>
            </button>
          ))}
          {resourceMore > 0 && (
            <p data-testid="at-menu-more-resources" className="px-2.5 py-1 text-[11px] text-zinc-500">
              {resourceMore} more from your apps, type to narrow
            </p>
          )}
        </div>
      )}
      {mentionable === null ? (
        <p className="px-3 py-2.5 text-xs text-zinc-500">Loading agents…</p>
      ) : agentMatches.length === 0 ? (
        // v1.328.0: when chats (v1.329.0: or files) match, the empty agents
        // line is noise.
        chatMatches.length === 0 &&
        fileMatches.length === 0 && (
          <p className="px-3 py-2.5 text-xs text-zinc-500">
            No agent by that name. Add one on the Agents page.
          </p>
        )
      ) : (
        <div
          role="listbox"
          aria-label="Agents"
          className={`${sectionCap(chatMatches.length > 0 || fileMatches.length > 0 ? "max-h-40" : "max-h-72")} p-1`}
        >
          {/* v1.329.0: a calm heading in sentence case, like Chats. */}
          <AtSectionHead title="Agents" hint="Answer in place of Iron Jarvis" />
          {agentShown.map((a) => (
            <button
              key={a.name}
              type="button"
              role="option"
              {...rowProps(`agent:${a.name}`)}
              onClick={() => pickAgent(a)}
              title={a.description}
            >
              <Bot size={12} className="shrink-0 text-accent-soft/70" />
              {/* v1.329.0: the agent's name in words, in the normal font;
                  the "@name" you type stays as a muted hint. */}
              <span data-testid="at-agent-name" className="max-w-[12rem] shrink-0 truncate text-[12px]">
                {atAgentName(a)}
              </span>
              <span data-testid="at-agent-mention" className="shrink-0 text-[11px] text-zinc-500">
                @{a.mention}
              </span>
              {/* Where it runs + whether it can actually take work. An offline
                  remote is LISTED, not hidden — "my agent isn't in the list"
                  is the worse failure. */}
              <span className="hidden shrink-0 text-[10px] text-zinc-600 sm:inline">
                {a.kind === "remote" ? "remote" : a.kind === "dynamic" ? "custom" : "built-in"}
              </span>
              {!a.healthy && (
                <span className="shrink-0 text-[10px] text-tone-warn">offline</span>
              )}
              <span className="truncate text-[11px] text-zinc-500">{a.description}</span>
            </button>
          ))}
          {agentMore > 0 && (
            <p data-testid="at-menu-more-agents" className="px-2.5 py-1 text-[11px] text-zinc-500">
              {agentMore} more agent{agentMore === 1 ? "" : "s"}, type to narrow
            </p>
          )}
        </div>
      )}
      {/* v1.329.0: FILES in the active project's folder (GET /fs/files, the
          Files tab's own query). A file is attached like "+ Attach"; a folder
          becomes the working folder like "+ Choose a working folder". No
          project, or nothing matching, draws nothing. */}
      {fileMatches.length > 0 && (
        <div
          role="listbox"
          aria-label="Files"
          data-testid="at-menu-files"
          className={`${sectionCap("max-h-48")} border-t border-white/[0.06] p-1`}
        >
          <AtSectionHead title="Files" hint="A file is attached, a folder becomes the working folder" />
          {fileShown.map((f) => {
            const where = entryWhere(f);
            return (
              <button
                key={`${f.kind}:${f.path}`}
                type="button"
                role="option"
                data-testid="at-file-option"
                data-kind={f.kind}
                {...rowProps(`${f.kind}:${f.path}`)}
                onClick={() => pickEntry(f)}
                title={f.kind === "folder" ? `Work in ${f.path}` : `Attach ${f.path}`}
              >
                {f.kind === "folder" ? (
                  <Folder size={12} className="shrink-0 text-accent-soft/70" />
                ) : (
                  <FileText size={12} className="shrink-0 text-accent-soft/70" />
                )}
                <span data-testid="at-file-name" className="min-w-0 truncate text-[12px]">
                  {f.name}
                </span>
                {where && (
                  <span className="ml-auto min-w-0 shrink truncate pl-2 text-[11px] text-zinc-500">
                    {where}
                  </span>
                )}
              </button>
            );
          })}
          {fileMore > 0 && (
            <p data-testid="at-menu-more-files" className="px-2.5 py-1 text-[11px] text-zinc-500">
              {fileMore} more in this project, type to narrow
            </p>
          )}
        </div>
      )}
      {/* v1.328.0: SAVED CHATS (GET /chat/threads/search-refs). A pick is a
          chip in the composer card, read with the next message. Nothing is
          drawn while the first answer is on its way or when none match. */}
      {chatMatches.length > 0 && (
        <div
          role="listbox"
          aria-label="Chats"
          data-testid="at-menu-chats"
          className={`${sectionCap("max-h-48")} border-t border-white/[0.06] p-1`}
        >
          <AtSectionHead
            title="Chats"
            hint={
              chatsFull
                ? `${CHAT_REFS_FULL_NOTE}. Remove one to add another.`
                : "Read with your next message"
            }
          />
          {chatMatches.map((c) => (
            <button
              key={c.id}
              type="button"
              role="option"
              data-testid="chat-ref-option"
              {...rowProps(`chat:${c.id}`)}
              onClick={() => pickChat(c)}
              title={chatSecondary.get(c.id) ? `${c.title} (${chatSecondary.get(c.id)})` : c.title}
            >
              <MessageSquare size={12} className="shrink-0 text-accent-soft/70" />
              <span data-testid="chat-ref-title" className="min-w-0 truncate text-[12px]">{c.title}</span>
              {/* v1.329.0: the project it belongs to (or the day), so chats
                  that share a title read apart. */}
              {chatSecondary.get(c.id) && (
                <span
                  data-testid="chat-ref-where"
                  className="min-w-0 shrink truncate text-[11px] text-zinc-500"
                >
                  {chatSecondary.get(c.id)}
                </span>
              )}
              {/* When it last changed, so chats that share a title can be
                  told apart (the chat list's own "8m / 2h" format). */}
              {formatAge(c.updatedAt, ageNow) && (
                <span data-testid="chat-ref-age" className="ml-auto shrink-0 pl-2 text-[11px] text-zinc-500">
                  {formatAge(c.updatedAt, ageNow)}
                </span>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  );
});

/** The send ARROW: a round button at the right end of the composer's toolbar.
 *  v1.326.0 (calm chat): always in its place, quiet and disabled until there
 *  is something to send (text or an attachment), then the accent. Subscribed
 *  because its look is a function of the text. */
const SendArrow = memo(function SendArrow({
  store,
  busy,
  hasAttachments,
  onSend,
}: {
  store: ComposerStore;
  busy: boolean;
  hasAttachments: boolean;
  onSend: (text: string) => void;
}) {
  const { text } = useComposer(store);
  // v1.275.0: a file alone is a message.
  const ready = !busy && Boolean(text.trim() || hasAttachments);
  return (
    <button
      type="button"
      onClick={() => onSend(store.get().text)}
      disabled={!ready}
      aria-label="Send"
      title="Send (Enter)"
      // ml-auto pins Send to the right end of the toolbar whatever wraps.
      // Only a message ready to go wears the accent (one primary per view).
      className={`ml-auto grid h-[34px] w-[34px] shrink-0 place-items-center rounded-full p-0 ${
        ready ? "btn-accent" : "cursor-not-allowed bg-white/[0.06] text-zinc-500"
      }`}
    >
      {busy ? <LoaderInline /> : <ArrowUp size={16} strokeWidth={2.4} />}
    </button>
  );
});

/** Voice Chat auto-send (headless). Once dictated text settles — no interim
 *  words, nothing being transcribed — send it. Web Speech finalizes eagerly,
 *  so give the speaker a moment; the server engine already waited out 1.4s of
 *  silence before finalizing, so send almost immediately.
 *
 *  It lives in its own component purely so that watching the TEXT does not
 *  re-render the page: the effect needs every keystroke, the page does not. */
const VoiceAutoSend = memo(function VoiceAutoSend({
  store,
  armed,
  delayMs,
  onSend,
}: {
  store: ComposerStore;
  /** Every condition the page owns, already ANDed: voice mode on, not busy,
   *  not speaking, nothing interim/processing/errored — and the last text
   *  came from the microphone rather than the keyboard. */
  armed: boolean;
  /** The dictation engine's settle delay, in ms — 350 for the server engine
   *  (it already waited out 1.4s of silence), 1500 for Web Speech. */
  delayMs: number;
  onSend: (text: string) => void;
}) {
  const { text } = useComposer(store);
  const onSendRef = useRef(onSend);
  onSendRef.current = onSend;
  useEffect(() => {
    if (!armed) return;
    const t = text.trim();
    if (!t) return;
    const timer = setTimeout(() => onSendRef.current(store.get().text), delayMs);
    return () => clearTimeout(timer);
  }, [armed, text, delayMs, store]);
  return null;
});

/** What a message row may ask the page to do. ONE object, built once with
 *  stable callbacks, so a row's props only change when its own message does
 *  (v1.250.0, S-05). */
export interface RowHandlers {
  retryTask: (task: string) => void;
  /** v1.325.0: `choice` = "Try again with…" another model, for this turn. */
  regenerate: (choice?: string) => void;
  /** v1.325.0: show version `to` of the conversation from message `index`. */
  switchVersion: (index: number, to: number) => void;
  /** v1.323.0: carry on the newest reply where it was cut off. */
  continueReply: () => void;
  /** v1.323.0: read reply `index` aloud (a second press stops it). */
  readAloud: (index: number) => void;
  /** v1.278.0: put a sent message back in the box (with its files) and drop
   *  everything after it — the resend is a fresh turn over what preceded it. */
  editMessage: (index: number) => void;
  /** v1.284.0: "Have builder do this" — the panel seat proposed; a REAL
   *  session of that agent carries it out, with the conversation as recap. */
  handOff: (index: number) => void;
  /** v1.305.0: the user answered the suggestion under reply `index` — store
   *  the answer on that message and save it. */
  settleSuggestion: (index: number, next: ChatSuggestion) => void;
  /** Redesign S3/S4: a settings card under reply `index` changed (Undo
   *  pressed, credential saved) — store it on the message and save. */
  settleConfigCard: (index: number, cardIndex: number, next: ConfigCard) => void;
  /** v1.320.0: the user rated reply `index` — store it on the message, save. */
  rateReply: (index: number, rating: ReplyRatingValue) => void;
  crystallize: (threadId: string) => void;
  promote: (content: string) => Promise<void>;
  /** The receipt's own prop types, not a second description of them: these
   *  three are handed straight to TurnReceipt, and a hand-written shape here
   *  would be one more thing that can drift from the component it feeds. */
  openDocument: NonNullable<ComponentProps<typeof TurnReceipt>["onOpenDocument"]>;
  undoFor: NonNullable<ComponentProps<typeof TurnReceipt>["undoFor"]>;
  undoWrite: NonNullable<ComponentProps<typeof TurnReceipt>["onUndo"]>;
}

/**
 * ONE message in the conversation (v1.250.0, S-05).
 *
 * Memoized on purpose: every keystroke in the composer, and every daemon
 * event, used to re-render all 50 bubbles of a long thread (measured: 105
 * bubble renders per keystroke, 173 per streamed token). The branches, the
 * markup and the order are exactly what the inline map rendered; only the
 * closures became props — and the live workflow cards subscribe to events
 * themselves, so an event no longer reaches this row at all.
 */
const MessageRow = memo(function MessageRow({
  m,
  i,
  isLast,
  prevUser,
  prevUserAt,
  canRegen,
  busy,
  threadId,
  projectId,
  crystallizingId,
  reading = false,
  regen,
  answeredName,
  h,
}: {
  m: ChatMessage;
  i: number;
  isLast: boolean;
  prevUser: string;
  /** v1.328.0 (W3-2): when the question before this reply was sent (ISO) —
   *  the start of the turn's window when the reply carries no `timing`. */
  prevUserAt?: string;
  canRegen: boolean;
  busy: boolean;
  threadId: string | null;
  /** Nullable, exactly as the page holds it: GoalBirth takes it as-is and the
   *  promote button turns a missing project into its own disabled reason. */
  projectId: string | null;
  crystallizingId: string | null;
  /** v1.323.0: this reply is being read aloud right now. */
  reading?: boolean;
  /** v1.325.0: what "Try again with…" offers — given to the newest reply only. */
  regen?: { models: ModelOption[]; recent: string[]; current: string };
  /** v1.326.0: the name of the model that answered (the catalog's label, else
   *  its id) for the receipt's "answered by …" words. A string, so the memo
   *  holds while the catalog is unchanged. */
  answeredName?: string;
  h: RowHandlers;
}) {
  // v1.323.0: the hidden "please continue" turn of a Continue press.
  if (m.continuation) return null;
  const when = messageTime(m.at);
  const versions = branchInfo(m);
  if (m.role === "user") {
    return (
      <div className="group/msg">
        <Bubble role="user">
          {/* v1.278.0: a mid-turn note is a user message the model read; the
              label says why it sits between a question and its answer. */}
          {m.steer && (
            <span
              data-testid="steer-label"
              className="mr-1.5 inline-block rounded-full border border-white/15 px-1.5 py-px align-middle text-[10px] uppercase tracking-wide text-zinc-400"
            >
              steer
            </span>
          )}
          {m.content}
          {m.attachmentNames && m.attachmentNames.length > 0 && (
            <AttachmentFooter names={m.attachmentNames} />
          )}
          {m.appResources && m.appResources.length > 0 && (
            <AttachmentFooter
              names={m.appResources.map((r) => r.note || r.uri.split(/[\\/]/).pop() || r.uri)}
            />
          )}
          {m.pageContext && (
            <AttachmentFooter names={[`About: ${m.pageContext.title || m.pageContext.path}`]} />
          )}
          {m.chatRefs && m.chatRefs.length > 0 && (
            <AttachmentFooter names={m.chatRefs.map((c) => `Chat: ${c.title}`)} />
          )}
        </Bubble>
        {/* v1.325.0: the versions an edit kept — always on screen. */}
        {versions && (
          <div className="mt-0.5 flex justify-end">
            <BranchPicker
              pos={versions.pos}
              count={versions.count}
              disabled={busy}
              onSwitch={(to) => h.switchVersion(i, to)}
            />
          </div>
        )}
        {/* v1.278.0: EDIT AND RESEND — never mid-turn, never on a steer note
            (it was read inside a turn; there is no "after it" to cut). */}
        {((!busy && !m.steer) || when.short) && (
          <div className="mt-0.5 flex items-center justify-end gap-1 opacity-0 transition-opacity focus-within:opacity-100 group-hover/msg:opacity-100 [@media(hover:none)]:opacity-100">
            {when.short && (
              <time dateTime={m.at} title={when.full} data-testid="message-time" className="px-1 text-[12px] text-zinc-500">
                {when.short}
              </time>
            )}
            {!busy && !m.steer && (
              <button
                type="button"
                onClick={() => h.editMessage(i)}
                title="Edit and resend — what follows is kept as an earlier version"
                aria-label="Edit and resend"
                className={REPLY_ACTION_BTN}
              >
                <Pencil size={12} />
              </button>
            )}
          </div>
        )}
      </div>
    );
  }
  // The hand-off (v1.108.0). A turn that grew into a full agent run has no
  // reply of its own — say WHY out loud, or the wait reads as a stall.
  if (m.workflowDraft)
    return (
      <div className="group/msg space-y-2">
        {m.content && (
          <Bubble role="assistant">
            <MemoMarkdown content={m.content} />
          </Bubble>
        )}
        <WorkflowDraftCard draft={m.workflowDraft} />
        {m.workflowRun && (
          <WorkflowRunChip runId={m.workflowRun.runId} name={m.workflowRun.name} />
        )}
      </div>
    );
  // v1.285.0: a remote's PROGRESS line is a quiet one-liner, not a reply —
  // unless it delivered files, which deserve the full bubble and the rail.
  if (m.panelWho && m.panelKind === "progress" && !m.documents?.length)
    return (
      <div
        data-testid="panel-progress"
        className="flex min-w-0 items-center gap-2 text-[12px] text-zinc-500"
      >
        <span className="shrink-0 font-medium text-zinc-400">{agentDisplayName(m.panelWho)}</span>
        <span className="truncate">{m.content}</span>
      </div>
    );
  // v1.150.0: a panel reply is attributed. Without a name on it, a three-way
  // conversation is an unreadable wall of anonymous assistant bubbles.
  // Calm chat W1-4: the name is a small muted line above the prose, no pill
  // and no icon; the reply itself has no box, like Jarvis's own.
  if (m.panelWho)
    return (
      <div className="group/msg space-y-1">
        <div className="flex items-center gap-2 text-[12px]">
          <span
            data-testid="panel-who"
            className={`font-medium ${m.panelError ? "text-tone-danger" : "text-zinc-400"}`}
          >
            {agentDisplayName(m.panelWho)}
          </span>
          {m.panelError && (
            <span className="text-tone-danger">couldn&apos;t answer</span>
          )}
          {/* v1.285.0: what KIND of line a remote sent back, when it is not
              a plain reply — a question, a "done", or "still working". */}
          {m.panelKind && m.panelKind !== "message" && m.panelKind !== "progress" && (
            <span data-testid="panel-kind" className="text-zinc-500">
              {m.panelKind === "pending" ? "working — will report back" : m.panelKind}
            </span>
          )}
          {/* v1.309.0: the "open in Agents →" link is GONE. It sent a live
              conversation to `/agents?thread=`, which since v1.308.0 is a
              READ-ONLY transcript of "the old round table" — the user left
              the chat they were in for a page they could not reply on. The
              conversation is already here; keep it here. (No prefilled
              "give this to the team" door exists to offer instead.) */}
        </div>
        {(m.content || !m.runResult) && (
          <Bubble role="assistant">
            <MemoMarkdown content={m.content} />
          </Bubble>
        )}
        {/* A hand-off's reply is the AGENT's AND a run (v1.284.0): the ledger
            card — files, tools, revert — renders here exactly as it does for
            an unnamed escalation. This branch returns before the `runResult`
            one below, so without this line the files would vanish from the
            very replies that make them. */}
        {m.runResult && (
          <RunResultCard
            result={m.runResult}
            onRetry={() => h.retryTask(m.runResult?.task || "")}
          />
        )}
        {/* What the speakers were NOT shown (v1.284.0) — said, never silent. */}
        {m.panelNote && (
          <div data-testid="panel-note" className="text-[12px] text-zinc-500">
            {m.panelNote}
          </div>
        )}
        {/* HANDS (v1.284.0): a panel seat only advises. When the newest reply
            is from a LOCAL agent, one press has that agent DO it — a real
            session with tools, the conversation as its recap, and the files
            it makes back here in chat. A remote agent has no local session
            shape, so it gets no chip rather than a chip that lies; a reply
            that already IS a run gets none either. */}
        {isLast && !busy && !m.panelError && !m.fromSession && sessionTargetOf(m.panelWho) && (
          <button
            type="button"
            data-testid="panel-hand-off"
            onClick={() => h.handOff(i)}
            className="-ml-2 inline-flex h-7 items-center gap-1.5 rounded-lg px-2 text-[12px] text-accent-soft transition-colors hover:bg-accent/[0.08]"
          >
            <Wrench size={12} />
            Have {agentDisplayName(m.panelWho)} do this
          </button>
        )}
      </div>
    );
  // v1.149.0: an agent turn shows the LEDGER's account under the model's own —
  // files it really wrote, tools that really ran, errors, what can be reverted.
  if (m.runResult)
    return (
      <div className="group/msg space-y-2">
        {m.content && (
          <Bubble role="assistant">
            <MemoMarkdown content={m.content} />
          </Bubble>
        )}
        <RunResultCard
          result={m.runResult}
          onRetry={() => h.retryTask(m.runResult?.task || "")}
        />
      </div>
    );
  if (m.escalated)
    return (
      <div className="group/msg">
        <div className="flex items-start gap-2 text-[13px] text-zinc-400">
          <Zap size={13} className="mt-[3px] shrink-0 text-accent-soft" />
          <span>
            {m.escalatedTo ? (
              <>
                Handing this to {m.escalatedTo} — {m.escalated}.
              </>
            ) : (
              <>Taking this on properly — {m.escalated}.</>
            )}
          </span>
        </div>
      </div>
    );
  const suggestion = decodeSuggestion(m.suggestion);
  const configCards = decodeConfigCards(m.configCards);
  // Calm chat W1-5 (v1.326.0): the receipt lives in the action row below, and
  // that row shows on hover only for an older reply. A warning (mock answer,
  // failover, blocked tools, low trust, "Remembered: …") must never wait for
  // a hover, so a reply whose receipt carries one keeps its row on screen.
  const receiptLoud =
    !!m.route &&
    receiptWantsAttention({
      route: m.route,
      adapted: m.adapted,
      deniedTools: m.deniedTools,
      remembered: m.remembered,
      trust: m.trust,
      threadRefs: m.threadRefs,
    });
  return (
    <div className="group/msg" data-testid="reply">
      {/* Calm chat W1-5 (v1.326.0): the work behind the answer — tools, steps,
          the model's reasoning — folded into ONE quiet line above it ("Worked
          for 3.2 s · read 2 files"). Nothing at all for a reply with none. */}
      <WorkLine
        steps={m.steps}
        toolsUsed={m.toolsUsed}
        timing={m.timing}
        thinking={m.thinking}
        thinkingSeconds={m.thinkingSeconds}
      />
      <Bubble role="assistant">
        {/* v1.325.0: a selection in here offers "Quote". */}
        <div data-quote-source>
          <MemoMarkdown content={m.content} />
        </div>
      </Bubble>
      {/* v1.324.0: an app resource the user attached that could not be read
          says so here — the answer above was written without it. */}
      {(m.appResources ?? []).some((r) => !r.ok) && (
        <div data-testid="app-resource-failed" className="mt-1 text-[12px] text-tone-warn">
          {(m.appResources ?? [])
            .filter((r) => !r.ok)
            .map((r) => `Couldn't read ${r.uri} from ${r.pack}${r.note ? `: ${r.note}` : ""}`)
            .join(" · ")}
        </div>
      )}
      {(m.interrupted || m.truncated) && (
        <div className="mt-1 flex flex-wrap items-center gap-2 text-[12px] italic text-tone-warn">
          <span data-testid="reply-cut-note">
            {m.truncated
              ? "The reply ran out of room and stopped."
              : "The reply was cut off."}
          </span>
          {/* v1.323.0: CONTINUE — only the newest plain chat reply, never mid-turn. */}
          {isLast && !busy && !m.panelWho && !m.fromSession && !m.runResult && m.content.trim() && (
            <button
              type="button"
              data-testid="continue-reply"
              onClick={h.continueReply}
              className="not-italic inline-flex h-7 items-center rounded-lg px-2 text-[12px] text-zinc-300 transition-colors hover:bg-white/[0.06] hover:text-zinc-100"
            >
              Continue
            </button>
          )}
        </div>
      )}
      {/* v1.170.0: the turn RAN a workflow (the model via the workflow_run
          tool, or the user from the "+" menu) — the live chip renders where
          the user is standing, INSIDE the generic branch so the reply keeps
          every standard affordance: hover actions, sources, receipt,
          regenerate. A forked branch here once silently dropped all of them. */}
      {m.workflowRun && (
        <div className="mt-2">
          <WorkflowRunChip runId={m.workflowRun.runId} name={m.workflowRun.name} />
        </div>
      )}
      {/* Calm chat W3-2 (v1.328.0): WHAT THE REPLY CHANGED — "2 files changed
          +14 −3", each file opening its diff, Undo through the receipt's own
          handler. Only a reply whose turn reported files has a window; the
          daemon is asked once, when the reply is on screen, by the turn's
          stored start and end (so a reopened chat shows it too). */}
      <ReplyChanges turn={turnWindow(m, prevUserAt)} undoFor={h.undoFor} onUndo={h.undoWrite} />
      {/* Calm chat W1-4 (v1.326.0): ONE quiet row of 28px ghost buttons right
          under the reply: Copy, Try again, 👍 / 👎, read aloud, save to the
          project, the versions a Try again kept ("‹ 1 / 2 ›") and the time.
          The newest reply keeps it on screen; an older one shows it on hover
          or focus, and always on a touch screen (no hover there). A 👎 that
          is asking "what should be different?" keeps the row open.
          W1-5: the receipt ends the row ("answered by …"); an open receipt
          or menu keeps the row open, and a receipt with a warning keeps it on
          screen without a hover (receiptLoud above). */}
      <div
        data-testid="reply-actions"
        className={`-ml-1.5 mt-1 flex flex-wrap items-center text-zinc-500 ${
          isLast || receiptLoud
            ? ""
            : "opacity-0 transition-opacity focus-within:opacity-100 has-[form]:opacity-100 has-[[aria-expanded=true]]:opacity-100 group-hover/msg:opacity-100 [@media(hover:none)]:opacity-100"
        }`}
      >
        <CopyIconButton text={m.content} title="Copy message" className={REPLY_ACTION_BTN} />
        {canRegen && regen && (
          // v1.325.0: Try again — the same model, or "with…" another one for
          // this turn only (the conversation's own pick is untouched).
          <RegenerateMenu
            models={regen.models}
            recent={regen.recent}
            current={regen.current}
            answeredBy={m.route?.provider ? { provider: m.route.provider } : undefined}
            onRegenerate={(c) => h.regenerate(c)}
            disabled={busy}
            quickLabel="Regenerate reply"
          />
        )}
        {canRegen && !regen && (
          <button
            type="button"
            onClick={() => h.regenerate()}
            title="Regenerate reply"
            aria-label="Regenerate reply"
            className={REPLY_ACTION_BTN}
          >
            <RefreshCw size={12} />
          </button>
        )}
        {/* v1.320.0: 👍 / 👎 — a reply can be rated where it was read. Not on
            a reply still being worked on by an agent (nothing to judge yet). */}
        {m.content.trim() && !m.awaitingSession && (
          <ReplyRating
            rating={m.rating}
            threadId={threadId}
            prominent={isLast}
            disabled={busy}
            onRated={(r) => h.rateReply(i, r)}
          />
        )}
        {/* v1.323.0: READ ALOUD — this one reply, on a press, whether or
            not spoken replies are on; a second press stops it. */}
        {m.content.trim() && (
          <button
            type="button"
            data-testid="read-aloud"
            onClick={() => h.readAloud(i)}
            aria-pressed={reading}
            title={reading ? "Stop reading" : "Read aloud"}
            aria-label={reading ? "Stop reading" : "Read aloud"}
            className={`${REPLY_ACTION_BTN} ${reading ? "!text-accent-soft" : ""}`}
          >
            {reading ? <VolumeX size={12} /> : <Volume2 size={12} />}
          </button>
        )}
        <PromoteKnowledgeButton
          disabledReason={projectId ? null : "bind this chat to a project first"}
          onPromote={() => h.promote(m.content)}
        />
        {/* v1.325.0: the answers a Try again kept, "‹ 1 / 2 ›". */}
        {versions && (
          <BranchPicker
            pos={versions.pos}
            count={versions.count}
            disabled={busy}
            onSwitch={(to) => h.switchVersion(i, to)}
          />
        )}
        {when.short && (
          <time dateTime={m.at} title={when.full} data-testid="message-time" className="px-1.5 text-[12px] text-zinc-500">
            {when.short}
          </time>
        )}
        {/* TURN RECEIPT (v1.165.0): server-side accountability — who answered
            and why, tools run/denied, files. Supersedes the legacy viaProvider
            chip below whenever the message carries a route. Calm chat W1-5
            (v1.326.0): it is the END of this row, "answered by <model> ·
            2 files", and opens onto the full receipt on its own line; every
            warning keeps its amber (or accent) tone on the row. */}
        {m.route && (
          <TurnReceipt
            inline
            modelName={answeredName}
            route={m.route}
            adapted={m.adapted}
            toolsUsed={m.toolsUsed}
            deniedTools={m.deniedTools}
            remembered={m.remembered}
            trust={m.trust}
            trustReason={m.trustReason}
            trustNote={m.trustNote}
            usage={m.usage}
            steps={m.steps}
            timing={m.timing}
            documents={m.documents}
            onOpenDocument={h.openDocument}
            undoFor={h.undoFor}
            onUndo={h.undoWrite}
            folderRules={m.folderRules}
            threadRefs={m.threadRefs}
          />
        )}
      </div>
      {/* PREFERENCE SUGGESTION (v1.305.0): a repeated correction, offered as
          a standing preference in the receipt's own quiet voice — directly
          under it, with Keep · Edit · Not this. Decoded again here because a
          reopened thread hands back whatever the disk held. */}
      {suggestion && suggestion.state !== "gone" && (
        <PreferenceSuggestion
          suggestion={suggestion}
          onSettle={(next) => h.settleSuggestion(i, next)}
        />
      )}
      {/* SETTINGS CARDS (redesign S3/S4): a change made in chat, with its
          Undo, and the secure card a credential is pasted into. Decoded again
          here because a reopened thread hands back whatever the disk held. */}
      <ConfigCards cards={configCards} onSettle={(ci, next) => h.settleConfigCard(i, ci, next)} />
      {/* DOORS (v1.199.0): links into the surfaces this turn actually touched —
          SERVER-derived from the tools that executed ok (files excluded; the
          ArtifactsRail owns files). Rides the message, so live and persisted
          turns render alike; a pre-v1.199.0 message has none. */}
      <DoorsStrip doors={m.doors} />
      {!m.route && m.viaProvider && (
        <div
          className="mt-1 text-[12px] text-tone-warn"
          title={`Your selected model couldn't take this turn (it may not support tools, or it errored), so the router used ${m.viaProvider} instead. Verify the endpoint's tool support in Connections to keep turns local.`}
        >
          answered by {m.viaProvider}
        </div>
      )}
      {/* Tools the reply's tool loop actually ran — LEGACY line for
          pre-v1.165.0 messages; the TurnReceipt carries the same fact (plus
          denials) when a route is present, so both would say it twice. */}
      {!m.route && m.toolsUsed && m.toolsUsed.length > 0 && (
        <div className="mt-1 flex min-w-0 items-center gap-1.5 text-[12px] text-zinc-500">
          <Wrench size={11} className="shrink-0" />
          <span className="truncate">used: {m.toolsUsed.join(", ")}</span>
        </div>
      )}
      {/* URLs the turn's web tools actually returned */}
      {m.sources && m.sources.length > 0 && <SourcesRow sources={m.sources} />}
      {/* Crystallize nudge (v1.120.0): agent turns are by definition
          multi-step — offer to keep the process. */}
      {m.fromSession && isLast && !busy && threadId && (
        <button
          type="button"
          disabled={crystallizingId !== null}
          onClick={() => h.crystallize(threadId)}
          className="-ml-2 mt-1 inline-flex h-7 items-center gap-1.5 rounded-lg px-2 text-[12px] text-accent-soft transition-colors hover:bg-accent/[0.08] disabled:opacity-50"
        >
          {crystallizingId ? (
            <Loader2 size={12} className="animate-spin" />
          ) : (
            <GitBranch size={12} />
          )}
          Keep this as a workflow?
        </button>
      )}
      {/* Goal birth (v1.208.0): only on the newest settled reply; GoalBirth
          applies the deliberately-high bar and renders nothing otherwise,
          because a false chip trains the user to ignore every chip. */}
      {isLast && !busy && (
        <GoalBirth userText={prevUser} toolsUsed={m.toolsUsed} projectId={projectId} />
      )}
    </div>
  );
});

/** What the bubble says while the daemon prepares a turn (v1.312.0, W4-3) —
 *  one per stage of the daemon's `phase` frame, in the user's terms. */
const PREP_WORDS: Record<PrepStep, string> = {
  recalling: "Recalling what’s relevant…",
  reading_files: "Reading your files…",
  summarizing: "Summarizing earlier conversation…",
  choosing_tools: "Choosing tools…",
};

/**
 * The live reply bubble (v1.250.0, S-03).
 *
 * THE ONLY component that watches the streamed text: it subscribes to the
 * stream's text store, so a token re-renders THIS and nothing else. The chat
 * page used to hold that text in state, which meant every word redrew the
 * whole conversation — 173 bubble renders per token on a 50-message thread.
 *
 * Everything it renders is what the bubble always rendered, in the same order:
 * the streamed markdown (or the waiting row with its clock), the live tool
 * cards and the quiet note. Calm chat W1-6 (v1.326.0): a question for the
 * user (the mid-turn approval, an app's question) is no longer here. It
 * takes the composer's place in the dock (<DockAsk>); only the record of
 * an app's question that ENDED stays, as one quiet line.
 */
function LiveReply({
  stream,
  onGrow,
}: {
  stream: UseChatStream;
  onGrow: () => void;
}) {
  const text = useLiveText(stream);
  // v1.323.0: the model's reasoning, folded — read HERE, never on the page.
  const thinking = useLiveThinking(stream);
  // Keep the newest line in view as the reply grows — the page's scroll effect
  // can no longer see this text, so the growth reports itself.
  useEffect(() => {
    if (text) onGrow();
  }, [text, onGrow]);
  return (
    <Bubble role="assistant">
      {/* Calm chat W1-5 (v1.326.0): the work in progress is grey one-line
          rows ABOVE the answer: the model's thinking, each tool call, and the
          step the turn is on (spinning, softly pulsing; still under reduced
          motion). When the answer lands they fold into the reply's "Worked
          for …" line. Empty (no thinking, no tools, words flowing) = hidden. */}
      <div data-testid="live-work" className="mb-2 grid gap-1 empty:hidden">
        <ThinkingDisclosure text={thinking} live={!text} />
        <LiveToolRows cards={stream.tools} />
        {!text && (
          <WorkRow
            icon={Loader2}
            running
            // v1.246.0: WHAT it is waiting on, and for how long — a working
            // turn and a stuck one used to show the same pulsing word.
            // v1.312.0 (W4-3): a current daemon NAMES each preparation stage
            // (a `phase` frame) — that wins. An older one sends nothing until
            // it has prepared, so the v1.246.0 inference stays for it.
            title={
              stream.prepStep
                ? PREP_WORDS[stream.prepStep]
                : stream.phase === "preparing" && stream.withFiles
                  ? "Reading your files…"
                  : "Thinking…"
            }
            meta={<TurnClock since={stream.startedAt ?? null} />}
          />
        )}
      </div>
      {text && <StreamingText content={text} />}
      {text && <QuietNote since={stream.lastEventAt ?? null} />}
      {/* v1.324.0: an app's question (or its request to use the model) is
          answered in the dock while its tool waits (W1-6). Once it ENDS, how
          it ended stays here as one quiet grey line, so the reply's record
          says what happened to it. */}
      {(stream.mcpAsks ?? [])
        .filter((a) => a.outcome)
        .map((a) => (
          <p key={a.id} data-testid="mcp-ask-settled" className="mt-1 text-[12px] text-zinc-500">
            {outcomeWords(a.outcome!, a.pack)}
          </p>
        ))}
    </Bubble>
  );
}


export default function ChatPage() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  // WHO that session runs on (v1.284.0): the roster target it was OPENED with
  // ("builder", "reviewer", "custom:<slug>"). A named hand-off to a different
  // agent opens a new session instead of continuing this one under a false
  // name. "" = no session / the builder default.
  const sessionTargetRef = useRef<string>("");
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
  // AUTOSAVE FAILED (v1.226.0): the chip by the composer. `retry` re-queues
  // the exact save that failed; cleared by a later successful save, Dismiss,
  // or leaving the conversation (the retry targets THIS conversation's box).
  const [saveFailure, setSaveFailure] = useState<{
    detail: string;
    retry: () => void;
    /** Retry was clicked; the chip stays until that save lands (CL2). */
    retrying?: boolean;
  } | null>(null);
  const [models, setModels] = useState<ModelOption[]>([]);
  /** v1.263.0: the reasoning levels the PICKED model offers, from the daemon's
   *  catalog row — [] for a model with no knob and for an older daemon that
   *  sends none. v1.329.0: with no pick ("") it reads the DEFAULT model's row
   *  (/health names it), so the chip shows there too; the daemon still applies
   *  a level only where the serving model offers it. "auto" and an unknown
   *  default match no row and draw nothing. */
  function reasoningLevelsFor(c: string): string[] {
    const { provider, model } = c
      ? splitChoice(c)
      : { provider: defaultProviderId, model: defaultModelName };
    if (!provider || !model) return [];
    const row = models.find((m) => m.provider === provider && m.model === model);
    return (row?.reasoning ?? []).filter((l) => REASONING_LEVELS.includes(l));
  }
  const [choice, setChoice] = useState(""); // "" => server default model
  // v1.263.0: the reasoning level for this conversation — "" = the model's own
  // default. Sent on every turn; the daemon applies it only where the serving
  // model offers one, and the receipt says what was applied. Persists with the
  // thread setup like the model pick.
  const [reasoning, setReasoning] = useState("");
  // Live per-provider availability (v1.165.0) — drives the preflight note
  // above the composer. 5s default keeps it in step with the topbar switcher.
  const health = useProviderHealth();
  // v1.312.0: what the failed-turn row needs to know about the provider the
  // turn ran on (the pick, else the default) — known down, or cooling down.
  // Facts only; which model to use instead is the user's call.
  const trouble = providerTrouble(choice, health);
  // The app's shared /health poll names the DEFAULT model (v1.232.0, audit
  // U8): the footer used to say "default model" while the title bar said
  // "brain (RTX)" — two words for one thing. Read, never polled here.
  const defaultModelName = useDaemon().health?.default_model ?? "";
  // v1.314.0: which provider the default runs on, so the footer can name the
  // default by its catalog label ("Claude Opus 4.8"), the same as a pick.
  const defaultProviderId = useDaemon().health?.default_provider ?? "";
  // v1.310.0: the empty state leads with the connect doors while no real
  // model answers (or the default is still the offline demo) — read off the
  // same /health, never a second poll.
  const showConnectDoors = needsConnect(useDaemon().health);
  // Redesign S9: "Make this my default" re-reads /health so every reader follows.
  const daemonRefresh = useDaemon().refresh;
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
  // v1.326.0: whether the open drawer was opened by a PRESS (it takes focus
  // and gives it back) or by the app (a reply made a file: no focus steal).
  const [drawerTakeFocus, setDrawerTakeFocus] = useState(false);
  // v1.326.0: where focus goes when the drawer closes and its opener is gone
  // (the "⋯" row unmounts in the press that opens it): the "⋯" button when
  // the menu opened it, else the top bar's Project button.
  const drawerReturnRef = useRef<HTMLElement | null>(null);
  const projectButtonRef = useRef<HTMLButtonElement>(null);
  // v1.326.0: the title of the thread this window opened, for the top bar's
  // breadcrumb when the thread is not in the loaded list (an older one).
  const [openedTitle, setOpenedTitle] = useState<{ id: string; title: string } | null>(null);
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
  // v1.329.0 (calm chat W4 F6): the drawer's Terminal button. A new Build
  // terminal in the folder, then Build focused on it.
  const folderTerminal = useOpenTerminal(workspaceDir);
  const [attachments, setAttachments] = useState<UploadedFile[]>([]);
  // v1.278.0: the running turn's name (so a steer can reach it) and the notes
  // sent to it so far — shown under the live reply until the turn ends.
  const turnIdRef = useRef("");
  const [pendingSteers, setPendingSteers] = useState<string[]>([]);
  // v1.287.0: set when the turn finished before reading a steer note — the
  // note is back in the box and this line says why. Cleared by the next turn.
  const [steerBack, setSteerBack] = useState(false);
  // v1.325.0: "Try again with…" — the model ONE turn runs on ("provider::model");
  // null = the conversation's own choice. Set by completeChat, cleared after.
  const turnChoiceRef = useRef<string | null>(null);
  // v1.325.0: messages written while a reply runs, sent one by one after it
  // finishes cleanly (Ctrl+Enter). They carry the files that were in the box.
  const [queued, setQueued] = useState<QueuedMessage[]>([]);
  const queuedRef = useRef<QueuedMessage[]>([]);
  queuedRef.current = queued;
  // v1.325.0: a Try again in flight — the reply it replaces, filed as another
  // version once the new reply lands. `conv` is the conversation it belongs to
  // (convGenRef: Stop does not move it, leaving the conversation does).
  const pendingForkRef = useRef<{ conv: number; index: number; oldTail: ChatMessage[] } | null>(null);
  // v1.325.0: the user pressed Stop on the running turn — its queued messages
  // wait for a press, and a Try again stopped before its first word puts the
  // earlier reply back. Reset when the next turn starts.
  const stoppedRef = useRef(false);
  // v1.325.0: the conversation map (a list of the questions to jump between).
  const [mapOpen, setMapOpen] = useState(false);
  // The folder THIS conversation was given for its files (v1.244.0 — see
  // placeInWorkfolder): shown as a chip, and later attachments join it. The
  // ref is what the next attach reads; the state is what the chip renders.
  const convFolderRef = useRef<string | null>(null);
  const [workfolder, setWorkfolder] = useState<string | null>(null);
  // Why the chat's previous folder was not used, when it was replaced.
  const [workfolderNote, setWorkfolderNote] = useState("");
  // A FOLDER OF DOCUMENTS COULD BE ONE SUMMARY SHEET (v1.251.0, C-04).
  // `batch_documents` has existed since v1.133.0 and is deliberately NOT
  // auto-armed (cost: about one model call per document), so it has always sat
  // behind a "+" menu click nobody knew to make. When this chat is pointed at a
  // folder, ask the daemon what a batch would REALLY process and offer it with
  // that number and the spend attached. Dismissable, per folder.
  const [batchPreview, setBatchPreview] = useState<BatchPreview | null>(null);
  const [batchDismissed, setBatchDismissed] = useState("");
  useEffect(() => {
    const folder = (workspaceDir || "").trim();
    setBatchPreview(null); // the offer belongs to the folder that earned it
    if (!folder) return;
    let live = true;
    void (async () => {
      try {
        const pv = await post<BatchPreview>("/documents/batch/preview", { folder });
        if (live && pv && pv.count >= BATCH_SUGGEST_MIN) setBatchPreview(pv);
      } catch {
        /* an unreadable or missing folder simply gets no offer */
      }
    })();
    return () => {
      live = false;
    };
  }, [workspaceDir]);
  const [uploading, setUploading] = useState(false);
  // v1.275.0: `uploading` is React state (lags a frame); the ref is the truth a
  // send checks synchronously. A send pressed while files are still uploading
  // used to go WITHOUT the files and nothing said so; now it waits for them.
  const uploadingRef = useRef(false);
  const queuedSendRef = useRef(false);
  // v1.322.0 (borrowed from assistant-ui's thread-switch fixes): which
  // CONVERSATION this window is on. It moves only when a chat is opened or
  // started — never on Stop — so an upload that finishes after the user left
  // never lands in the conversation they moved to.
  const convGenRef = useRef(0);
  // v1.323.0: FOLLOW-UP QUESTIONS (setting chat_followups, off by default) —
  // fetched after a finished reply, shown only while that reply is the newest.
  const followupsOnRef = useRef(false);
  const [followups, setFollowups] = useState<{ forLen: number; items: string[] } | null>(null);
  const [dragging, setDragging] = useState(false);
  // v1.250.0 (S-05): the composer's text/caret/dismissals/highlight live in a
  // store, not in this component — a keystroke re-renders the textarea, the
  // pickers and the send arrow, and nothing else. `composer.get().text` is the
  // synchronous read every send path uses; `setText` is the ref API that
  // prefill, dictation, retry and ?ask= write through.
  const composerRef = useRef<ComposerStore | null>(null);
  if (composerRef.current === null) composerRef.current = createComposerStore();
  const composer = composerRef.current;
  // Empty-state example chips (v1.198.0). The initializer must be
  // DETERMINISTIC: this page is prerendered, so a random initial render would
  // bake one trio into the build's HTML and hydration-mismatch nearly every
  // live visit. We render the stable first three (anchor-led, same first chip
  // as any pick), then rotate in an effect AFTER hydration. Calm chat W1-3:
  // three quiet pills under the card, not four.
  const [examples, setExamples] = useState<string[]>(() =>
    CHAT_EXAMPLES.slice(0, NEW_CHAT_SUGGESTIONS),
  );
  useEffect(() => {
    // Rotate only after hydration (the roster-fold idiom, like approvalMode
    // below): server HTML and first client render must agree, and both lead
    // with the CHAT_EXAMPLES[0] anchor so no visible first-chip swap.
    setExamples(pickExamples(NEW_CHAT_SUGGESTIONS));
  }, []);
  // "+" TOOLS MENU (chat mode): armed registry tool names — sent as `tools` on
  // every /chat turn and kept across turns until "New chat" / a thread switch.
  const [selectedTools, setSelectedTools] = useState<string[]>([]);
  // "+" CONNECTOR TOGGLES (chat mode): connector ids toggled ON — sent as
  // `connectors` on every /chat turn. An MCP connector arms its whole tool
  // group server-side; a memory connector grounds the turn with its store.
  const [selectedConnectors, setSelectedConnectors] = useState<string[]>([]);
  // APPROVAL POSTURE (v1.188.0): how the mid-turn ask behaves. Hydrated from
  // the localStorage DEFAULT after mount (SSR parity — the roster-fold idiom);
  // a thread's saved setup overrides it on open; New chat returns to the
  // default. Changing it in an open thread persists with that thread.
  const [approvalMode, setApprovalMode] = useState<ApprovalMode>("approve_for_me");
  useEffect(() => {
    try {
      setApprovalMode(asApprovalMode(localStorage.getItem(APPROVAL_MODE_KEY)));
    } catch {
      /* storage unavailable — the default stands */
    }
  }, []);
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
  // skillIndex / slashDismissed / atDismissed / caret: see `composer` above
  // (v1.250.0, S-05) — they are the composer's, and the composer re-renders
  // alone when they change.
  // "@" agent picker (v1.150.0): the catalog + whether Esc closed the dropdown.
  const [mentionable, setMentionable] = useState<MentionableAgent[] | null>(null);
  // v1.324.0: what the user's apps offer under "/" (prompts) and "@"
  // (resources), the resources picked for the NEXT message, and the prompt
  // whose little form is open.
  const [packPrompts, setPackPrompts] = useState<PackPrompt[] | null>(null);
  const [packResources, setPackResources] = useState<PackResource[] | null>(null);
  const [appResources, setAppResources] = useState<PackResource[]>([]);
  // v1.325.0: "Ask Jarvis about this page" — the page the next message asks
  // about (a chip above the box; consumed by the send).
  const [pageCtx, setPageCtx] = useState<PageContext | null>(null);
  // v1.328.0 (calm chat W3-1): saved chats picked with "@" for the NEXT
  // message (chips in the card; consumed by the send like attachments). The
  // ref is what a send reads (sends fire from stale closures); the note is
  // the quiet "Up to 3 chats" a pick past the cap leaves beside the chips.
  const [chatRefs, setChatRefsState] = useState<ChatRefPick[]>([]);
  const chatRefsRef = useRef<ChatRefPick[]>([]);
  const setChatRefs = useCallback((next: ChatRefPick[]) => {
    chatRefsRef.current = next;
    setChatRefsState(next);
  }, []);
  const [chatRefNote, setChatRefNote] = useState("");
  const atKeysRef = useRef<AtKeyHandler | null>(null);
  const [promptForm, setPromptForm] = useState<PackPrompt | null>(null);
  const packListsAtRef = useRef<{ prompts: number; resources: number }>({ prompts: 0, resources: 0 });
  // TALKING TO AN AGENT (v1.284.0): after "@builder …" the follow-ups keep
  // going to builder — participant keys ("builtin:builder"); [] = Iron Jarvis.
  // Set after every round, restored from the saved conversation on open,
  // cleared by "Back to Jarvis", a new chat, or a different @ in the text.
  const [addressee, setAddressee] = useState<string[]>([]);
  const addresseeRef = useRef<string[]>([]);
  addresseeRef.current = addressee;
  // The agent a running hand-off session belongs to (v1.284.0), so its reply
  // bubble is attributed to it when the run lands; null = a plain escalation.
  const awaitingWhoRef = useRef<string | null>(null);
  // Caret offset in the composer. The picker keys off the "/" token AT THE
  // CARET (v1.105.0), not the start of the message, so it needs to know where
  // the cursor is — mid-sentence "/" is the whole point of that change.
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
  // v1.322.0: what an "Edit and resend" cut away, until the next send, so a
  // stray press can be undone (borrowed idea: assistant-ui's edit composer,
  // which changes nothing until Send — here the cut stays immediate, as
  // v1.278.0 defined it, and gains an Undo).
  const [editUndo, setEditUndo] = useState<{ before: ChatMessage[]; text: string; files: UploadedFile[] } | null>(null);
  // v1.322.0: the chat whose delete item was pressed once (armed).
  const [deleteArmedId, setDeleteArmedId] = useState<string | null>(null);
  // v1.328.0 (calm chat W3-3): ARCHIVE. A chat still working is not archived
  // until the user says to stop it (`archiveAsk` = the dialog, with the
  // daemon's own words for what is running). `archivedThreads` is GET
  // /chat/threads?archived=only (every project); `archivedView` swaps the
  // rail's list for it. `archiveNote` is the one sentence said when work
  // kept going after an archive (it could not be stopped from here).
  const [archiveAsk, setArchiveAsk] = useState<{
    id: string;
    title: string;
    running: string[];
  } | null>(null);
  const [archiveBusy, setArchiveBusy] = useState(false);
  const [archiveError, setArchiveError] = useState<string | null>(null);
  const [archiveNote, setArchiveNote] = useState<string | null>(null);
  const [archivedThreads, setArchivedThreads] = useState<ThreadSummary[]>([]);
  const [archivedView, setArchivedView] = useState(false);
  // Calm UI redesign S7 (AUDIT Q5): on a wide screen the app sidebar holds
  // the conversation list — this page's own thread rail, portaled in (layout
  // only: same state, same rename / pin / move / delete). No slot (a phone, a
  // test, a pop-out) keeps the rail where it always was.
  const chatSlot = useChatSlot();
  const [threadQuery, setThreadQuery] = useState(""); // sidebar title filter
  // Pinned threads (per-device view preference) + inline rename state.
  const [pinnedIds, setPinnedIds] = useState<string[]>([]);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState("");
  // Commit-to-memory (per-thread): in-flight id + transient success id.
  const [rememberingId, setRememberingId] = useState<string | null>(null);
  const [rememberedId, setRememberedId] = useState<string | null>(null);
  const [threadsLoading, setThreadsLoading] = useState(true); // first threads fetch
  // v1.311.0: the scope the sidebar's list was last ASKED for (undefined =
  // never). A list answer applies only while its scope is still this one, so a
  // slow answer for an old scope can never repaint the rail; and a request for
  // the scope already asked for is not sent again.
  const listScopeRef = useRef<string | null | undefined>(undefined);
  // v1.315.0 (thread-rail-scope-silent): the rail's scope switch changes the
  // RAIL only. The open chat, its project, the composer's project and every
  // save stay exactly as they are (the verifier caught that wiring it to
  // chooseProject("") would re-save the open chat as "no project").
  // v1.329.0 (calm chat W5 G3): inside a project chat the rail is the SAME
  // grouped list as everywhere else: that project's group first and open,
  // every other group folded to a heading that still shows its dot. The
  // project's own chats still come from the scoped read (complete; the
  // unscoped list is the newest 100), the rest from one unscoped read
  // (`otherThreads`). The switch is now a FILTER: `railOnly` shows just this
  // project's chats as one plain list ("Only <project>"), nothing re-read.
  const [railOnly, setRailOnly] = useState(false);
  // The project the rail is showing (the render's copy of listScopeRef).
  const [railScope, setRailScope] = useState<string | null>(null);
  const [otherThreads, setOtherThreads] = useState<ThreadSummary[]>([]);
  // True once the page KNOWS its project: /projects answered (or failed), or
  // the user picked/cleared one. Before that a null projectId is "not known
  // yet", not "no project".
  const projectsSettledRef = useRef(false);
  const mountedRef = useRef(false);
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

  // v1.311.0: only the event types this page reads reach it — a browser tab
  // switch or the router's provider.routed no longer re-runs the whole page
  // (see chatEventTypes.ts). Module-level options: one stable list.
  const { events } = useEvents(150, CHAT_EVENTS_OPTS);
  // Threads are scoped to the selected project (the daemon filters by
  // project_id); with no project every saved conversation shows. The sidebar's
  // title filter narrows client-side on top. v1.327.0: pinned chats are drawn
  // first by ThreadGroups (its "Pinned" group), so no sort here.
  const visibleThreads = useMemo(() => {
    const q = threadQuery.trim().toLowerCase();
    return q ? threads.filter((t) => (t.title || "").toLowerCase().includes(q)) : threads;
  }, [threads, threadQuery]);
  // v1.329.0 (G3): the chats OUTSIDE the rail's project (other projects and
  // No project), from the unscoped read. A row of the rail's own project is
  // the scoped list's to show (it may be newer), so it is left out here.
  const railOthers = useMemo(() => {
    if (!railScope) return [] as ThreadSummary[];
    const own = new Set(threads.map((t) => t.id));
    return otherThreads.filter((t) => t.project_id !== railScope && !own.has(t.id));
  }, [otherThreads, threads, railScope]);
  // Every row the rail can show, for the row ⋯ menu and the actions behind it.
  const listedThreads = useMemo(
    () => (railOthers.length ? [...threads, ...railOthers] : threads),
    [threads, railOthers],
  );
  const visibleOthers = useMemo(() => {
    const q = threadQuery.trim().toLowerCase();
    return q ? railOthers.filter((t) => (t.title || "").toLowerCase().includes(q)) : railOthers;
  }, [railOthers, threadQuery]);
  // v1.328.0: the same title filter over the archived list.
  const visibleArchived = useMemo(() => {
    const q = threadQuery.trim().toLowerCase();
    return q
      ? archivedThreads.filter((t) => (t.title || "").toLowerCase().includes(q))
      : archivedThreads;
  }, [archivedThreads, threadQuery]);

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
  // v1.329.0 (calm chat W5 G1): the top bar ⋯'s own "Add to project" list,
  // folded each time that menu opens.
  const [moreProjectsOpen, setMoreProjectsOpen] = useState(false);
  const [assigningThread, setAssigningThread] = useState(false);
  const threadMenuRef = useRef<HTMLDivElement | null>(null);

  function openThreadMenu(e: React.MouseEvent, id: string) {
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
    // Open upward when the row sits near the viewport bottom — a fixed menu
    // can't rely on a scroll container to make room for it.
    const up = window.innerHeight - r.bottom < 340;
    setThreadMenuProjects(false);
    setDeleteArmedId(null); // a fresh menu never opens already armed
    setThreadMenu({ id, x: r.right, y: up ? r.top - 4 : r.bottom + 4, up });
  }

  useEffect(() => {
    if (!threadMenu) return;
    const close = () => setThreadMenu(null);
    const onDown = (e: MouseEvent) => {
      if (!threadMenuRef.current?.contains(e.target as Node)) close();
    };
    // v1.329.0: caught first (capture) and kept there, so Escape closes the
    // menu ONLY. On a phone the list sits in the nav drawer, which closes on
    // its own Escape listener.
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.stopPropagation();
      close();
    };
    // Any scroll OUTSIDE the menu strands a fixed popout at stale coordinates
    // — close instead of drifting. Scrolls INSIDE it (the project list) are
    // the menu working as intended.
    const onScroll = (e: Event) => {
      if (threadMenuRef.current?.contains(e.target as Node)) return;
      close();
    };
    document.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey, true);
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", close);
    return () => {
      document.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey, true);
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", close);
    };
  }, [threadMenu]);

  /** Tag a thread to a project (or null to untag) — the same read-then-PUT
   *  shape renameThread uses; the daemon treats an explicit project_id key as
   *  assign-or-clear and never infers one on update. */
  async function assignThreadProject(id: string, pid: string | null): Promise<boolean> {
    setAssigningThread(true);
    try {
      // Daemon-owned (messaging) threads reject `messages` writes with 409 —
      // tag them with a project_id-only body (the route's carve-out).
      if (listedThreads.find((x) => x.id === id)?.owner === "daemon") {
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
      // v1.329.0 (calm chat W5 G1): moving the OPEN chat (its top-bar ⋯, or
      // its own row) moves the page with it. Every autosave sends the page's
      // project (`projectIdRef`), so without this the next save put the chat
      // straight back where it was. The thread's own setup still owns the
      // folder, as when it is opened: keepFolder stops "Remove from project"
      // from resetting it to the user's default, which the next save would
      // then write over the chat's saved folder.
      if (saveTargetRef.current.id === id) {
        followThreadProject({ project_id: pid } as ThreadDetail, false, { keepFolder: true });
      }
      void refreshThreads();
      setThreadMenu(null);
      return true;
    } catch (e) {
      if (e instanceof ApiError && e.status === 0) setOffline(true);
      else setError(e instanceof ApiError ? e.message : String(e));
      return false;
    } finally {
      setAssigningThread(false);
    }
  }

  /** Resolves true when the new title was saved (v1.329.0: the top bar's
   *  rename reads it to undo its at-once title on a failure). */
  async function renameThread(id: string, title: string): Promise<boolean> {
    const clean = title.trim();
    setRenamingId(null);
    if (!clean) return false;
    try {
      // Same 409 carve-out as assignThreadProject: rename a messaging thread
      // with a title-only body — its messages belong to the daemon.
      if (listedThreads.find((x) => x.id === id)?.owner === "daemon") {
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
      return true;
    } catch (e) {
      if (e instanceof ApiError && e.status === 0) setOffline(true);
      else setError(e instanceof ApiError ? e.message : String(e));
      return false;
    }
  }

  // v1.329.0 (calm chat W4 F8): RENAME THE OPEN CHAT from the top bar's ⋯.
  // The title in the top bar turns into a box in place (the list row's
  // rename does the same in its row). It is its OWN state, not renamingId:
  // with one shared id the list row of the same chat would open a second
  // box, and two boxes would each save on blur. `id` is the chat being
  // renamed, captured at the press, so a save always lands on that chat.
  const [titleRename, setTitleRename] = useState<{ id: string; draft: string } | null>(null);
  // One save per rename: Enter, Escape and the blur that follows the box
  // leaving the page all come through here, and only the first counts.
  const titleRenameOpenRef = useRef(false);
  // Where focus goes back after Enter or Escape: the ⋯ button.
  const titleRenameReturnRef = useRef<HTMLElement | null>(null);

  function startTitleRename(id: string, current: string, returnTo: HTMLElement | null) {
    titleRenameOpenRef.current = true;
    titleRenameReturnRef.current = returnTo;
    setTitleRename({ id, draft: current });
  }

  /** `save` false = Escape (nothing changes). `refocus` = a key ended it, so
   *  focus goes back to the ⋯ button; a blur leaves focus where the user put it. */
  async function endTitleRename(save: boolean, refocus: boolean) {
    if (!titleRenameOpenRef.current) return;
    titleRenameOpenRef.current = false;
    const r = titleRename;
    setTitleRename(null);
    if (refocus) titleRenameReturnRef.current?.focus();
    if (!save || !r) return;
    const clean = r.draft.trim();
    const before = threads.find((t) => t.id === r.id)?.title ?? "";
    if (!clean || clean === before.trim()) return;
    // Shown at once, so the bar never flashes the old title between the
    // box closing and the list coming back. A failed save puts it back.
    setThreads((prev) => prev.map((t) => (t.id === r.id ? { ...t, title: clean } : t)));
    setOpenedTitle((o) => (o && o.id === r.id ? { ...o, title: clean } : o));
    const saved = await renameThread(r.id, clean);
    if (!saved && mountedRef.current) {
      setThreads((prev) => prev.map((t) => (t.id === r.id ? { ...t, title: before } : t)));
      void refreshThreads();
    }
  }

  // A rename box belongs to the chat it was opened on: opening another chat
  // (or a new one) closes it without saving.
  useEffect(() => {
    if (titleRename && titleRename.id !== threadId) {
      titleRenameOpenRef.current = false;
      setTitleRename(null);
    }
  }, [threadId, titleRename]);

  const activeProject = useMemo(
    () => (projectId ? (projects.find((p) => p.id === projectId) ?? null) : null),
    [projects, projectId],
  );
  // v1.315.0: the rail is showing ONLY one project's chats, as one plain list.
  // v1.329.0 (G3): that is now the "Only <project>" filter; a project chat
  // opens on the grouped list with the project's group first.
  const railScoped = Boolean(projectId) && railOnly;
  // The project whose group leads the grouped rail (none on the plain list).
  const railLead = useMemo(() => {
    if (!railScope || railScoped) return null;
    const name =
      projects.find((p) => p.id === railScope)?.name?.trim() || "This project";
    return { projectId: railScope, name };
  }, [railScope, railScoped, projects]);

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
  // v1.250.0 (S-03): the live reply is rendered by <LiveReply> below, which
  // subscribes to the stream's text store — so a streamed token no longer
  // re-renders this 7,750-line page.
  const stream = useChatStream({ textInState: false });
  // v1.257.0 (S-02): same treatment the chat lane got in v1.250.0 — the agent
  // text is rendered by <AgentLiveText>, which subscribes to the store, so a
  // token no longer re-renders this page.
  const runStream = useRunStream({ textInState: false });
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
  // v1.326.0 (calm chat W1-2): true until the transcript is first filled after
  // the page opens or a conversation is left (leaveConversation). That first
  // fill lands at the bottom AT ONCE: a smooth glide from the top of a long
  // chat fired scroll events far from the bottom (each one releasing the pin)
  // while it was still aimed at a bottom a late chart had already moved.
  const openingRef = useRef(true);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  // Synchronous send guard: `busy` is React state and lags a frame, so two
  // Enter keydowns in the same tick both saw busy===false and double-sent. This
  // ref flips instantly and is the real gate; cleared when the turn settles.
  const sendingRef = useRef(false);
  // "+" popover container — outside-click detection needs the DOM node.
  const toolsPopRef = useRef<HTMLDivElement>(null);
  // The "+" button itself: where focus goes back when the project drawer it
  // opened ("Choose a working folder") closes (v1.326.0).
  const plusBtnRef = useRef<HTMLButtonElement>(null);
  // Composer project quick-toggle popover (the cowork switch).
  const projPopRef = useRef<HTMLDivElement>(null);
  const [projMenuOpen, setProjMenuOpen] = useState(false);
  // v1.326.0 (calm chat W1-2): the composer's tools chip and its menu (Auto
  // tools, web, the tools armed by hand, connections).
  const toolMenuRef = useRef<HTMLDivElement>(null);
  const [toolMenuOpen, setToolMenuOpen] = useState(false);
  // v1.329.0 (calm chat W5 G1): the permission chip's menu is open (the chip
  // owns it; the "Jump to latest" pill steps aside while it is).
  const [permissionMenuOpen, setPermissionMenuOpen] = useState(false);
  // The rail IS the project workspace now (Projects left the nav): Files or
  // Knowledge inline; the wide surfaces (tasks/board/media) open from here.
  const [railTab, setRailTab] = useState<"files" | "knowledge">("files");
  // The conversation column can flip to a full project surface (tasks/board/
  // media) IN PLACE — the old project screen, inside the chat module.
  const [projectView, setProjectView] = useState<"chat" | ProjectSurfaceView>(
    "chat",
  );
  // Which "+" submenu flyout is open (skills / connectors / project /
  // workflows).
  const [plusSub, setPlusSub] = useState<
    "skills" | "connectors" | "project" | "workflows" | null
  >(null);
  // Saved workflow defs for the "+" menu's "Run a workflow…" flyout
  // (v1.170.0). null = not fetched yet (the flyout shows a loader);
  // "error" = the daemon didn't answer — a DISTINCT state, because rendering
  // the genuine-empty copy on a failed fetch would claim "you have none"
  // when the truth is "I couldn't ask".
  const [savedWorkflows, setSavedWorkflows] = useState<
    { name: string; description?: string }[] | "error" | null
  >(null);
  const workflowsFetchedRef = useRef(false);
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
  // v1.277.0: FIND A MODEL BY TYPING, AND THE LAST PICKS FIRST. The tree below
  // is 12 providers deep on the daily driver; a typed word lists every model
  // whose id, provider or name contains it (Enter picks the first), and an
  // empty box opens on the last three picks. Both are read when the menu
  // opens, never per keystroke of the composer (S-05).
  const [modelFilter, setModelFilter] = useState("");
  const [recentModels, setRecentModels] = useState<string[]>([]);
  useEffect(() => {
    if (!modelMenuOpen) return;
    setModelFilter("");
    setRecentModels(readRecentModels());
  }, [modelMenuOpen]);
  const modelMatches = useMemo(
    () => matchModels(models, modelFilter, MODEL_FILTER_MAX),
    [models, modelFilter],
  );
  // Calm UI redesign S9 (AUDIT Q3): the composer's menu IS the model
  // selector — the title-bar chip is retired, so the app-wide door
  // ("Switch model" in Ctrl K, "Choose a model" on the demo strip) opens it.
  useEffect(() => {
    const onOpen = () => {
      setModelMenuOpen(true);
      setModelSub(null);
    };
    window.addEventListener("ij:open-switcher", onOpen);
    return () => window.removeEventListener("ij:open-switcher", onOpen);
  }, []);
  // "Make this my default": the conversation's pick becomes the saved default
  // through the one settings writer (ledger + Undo, like any setting).
  const [defaultNote, setDefaultNote] = useState("");
  async function makeDefault() {
    const { provider, model } = splitChoice(choice);
    if (!provider) return;
    try {
      await put("/settings", { values: { default_provider: provider, default_model: model || "" } });
      setDefaultNote("Saved as your default.");
      try {
        daemonRefresh();
      } catch {
        /* the next poll catches up */
      }
    } catch (err) {
      setDefaultNote((err as { message?: string })?.message || "Couldn't save the default.");
    }
  }
  /** The ONE way a menu row picks: the choice, the thread setup, the menu, the memory. */
  function pickModel(v: string) {
    setDefaultNote("");
    setChoice(v);
    markSetupChanged();
    setModelMenuOpen(false);
    setModelSub(null);
    if (v) setRecentModels(rememberRecentModel(v));
  }
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
      // v1.230.0 (U5): a keyless API provider served through the logged-in
      // CLI (`inherited_from`) is flat-rate — it ranks and labels as a CLI
      // ("included"), never "metered". The daemon names the CLI; /health and
      // /connections carry the same answer.
      const kind = m.inherited_from ? "cli" : (m.kind ?? "api");
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
    local: { text: "local", cls: "text-tone-success" },
    cli: { text: "included", cls: "text-tone-info" },
    api: { text: "metered", cls: "text-zinc-500" },
  };
  const modelLabel = useMemo(() => {
    if (!choice) {
      return defaultModelName ? `default · ${defaultModelName}` : "default model";
    }
    const { model } = splitChoice(choice);
    return model || choice.replace("::", " · ");
  }, [choice, defaultModelName]);
  // v1.300.0: the trigger says the picker's label ("Opus 5.5") for a pick
  // whose catalog row carries one; every other case is modelLabel verbatim.
  // v1.314.0 (composer-footer-jargon): the DEFAULT gets the same lookup, read
  // as "Default: Claude Opus 4.8" — the raw id only when the catalog has no
  // label for it (nothing invented). A default on the scripted demo model is
  // named as that, never as the model id it pretends to be. The raw id the
  // turn will run on stays in modelTriggerRaw (the trigger's inner title).
  // v1.329.0 (calm chat F7): the chip SHOWS only the name ("Opus 4.8"), as
  // the approved mockup draws it; "Default:" stays in the words a screen
  // reader hears (an sr-only span, modelTriggerDefault) and in the title.
  // Showing it took the room the reasoning chip needs, so the toolbar
  // broke onto two lines in a project chat.
  const modelTriggerDefault = !choice && !!defaultModelName;
  const modelTriggerText = useMemo(() => {
    if (!choice) {
      if (!defaultModelName) return modelLabel;
      if (defaultProviderId === "mock") return providerDisplay("mock");
      const row = models.find(
        (m) => m.provider === defaultProviderId && m.model === defaultModelName,
      );
      return row?.label ? modelText(row) : friendlyModelName(defaultModelName);
    }
    const { provider, model } = splitChoice(choice);
    const row = models.find((m) => m.provider === provider && m.model === model);
    // v1.329.0: no label → the name people say ("Opus 4.8"), derived only
    // from what the id spells; an id it cannot read stays as it is.
    return row?.label ? modelText(row) : friendlyModelName(modelLabel);
  }, [choice, models, modelLabel, defaultModelName, defaultProviderId]);
  /** The record behind the trigger's words: provider · model id. */
  const modelTriggerRaw = useMemo(() => {
    if (!choice) {
      return defaultModelName
        ? [defaultProviderId, defaultModelName].filter(Boolean).join(" · ")
        : "";
    }
    return choice.replace("::", " · ");
  }, [choice, defaultModelName, defaultProviderId]);
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
  // v1.305.0: every suggestion answered in this window, by id. queueSave runs
  // each save through it, so a turn that began BEFORE the press (its history
  // still holds the open ask) can never put the question back on disk.
  const settledSuggestionsRef = useRef<Map<string, ChatSuggestion>>(new Map());
  // Redesign S3/S4: settings cards settled in this window (same reason).
  const settledCardsRef = useRef<Map<string, ConfigCard>>(new Map());
  // Redesign S4 (AUDIT Q9): a message held because it looks like a key or
  // token; `secretSendOkRef` lets "Send anyway" through the guard once.
  const [heldSecret, setHeldSecret] = useState<string | null>(null);
  const secretSendOkRef = useRef(false);
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
  // Latest ARMED TOOL SET. A mid-turn approval card's "Allow for this
  // conversation" arms a tool DURING a turn, while the escalation body and the
  // turn's own queueSave both run inside the send's pre-turn closure — reading
  // the `selectedTools` binding there silently drops the grant the user just
  // made (the threadDocsRef lesson, v1.166.0). Anything that reads the armed
  // set AFTER a turn has begun reads it here; `armFromApproval` also writes it
  // synchronously so a grant is visible before React re-renders.
  const selectedToolsRef = useRef<string[]>(selectedTools);
  selectedToolsRef.current = selectedTools;
  // v1.312.0 (W4-2): the conversation's GRANTS ("Allow for this
  // conversation"), kept apart from the armed set. Arming alone could not
  // hold one at the 6-tool cap, so the card's "stops asking here" silently
  // failed on a busy thread. A ref, not state: nothing renders the list, and
  // a grant made mid-turn must ride that turn's own save (the
  // selectedToolsRef lesson). Lives exactly as long as the armed set does.
  const grantedToolsRef = useRef<string[]>([]);
  // The plain-words note when a grant could not ALSO be armed (the cap):
  // the tool name, or null. Cleared by the next send, a dismiss, or leaving
  // the conversation.
  const [grantCapNote, setGrantCapNote] = useState<string | null>(null);
  // THREAD SETUP persistence guard: saves include a `setup` snapshot only once
  // it was restored from the open thread or the user actually armed/changed
  // something — a plain reply on a thread whose setup wasn't restored (older
  // daemon, or nothing armed) must never PUT empties over a stored setup.
  const sendSetupRef = useRef(false);
  // v1.311.0: how many USER setup edits this page has seen (markSetupChanged).
  // openThread snapshots it at a cached paint: an edit made before the
  // thread's GET answers is the user acting on the painted conversation, and
  // that GET must then not lay the stored setup back over it.
  const setupEditsRef = useRef(0);
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
  // The session a thread-open RESUMED waiting on (v1.226.0) — finalize treats
  // "no such session" for it as a pruned record (strip the mark quietly),
  // not as an error the user caused by opening a conversation. Compared to
  // the finalize id, so a stale value can never misfile a live turn.
  const resumedSessionRef = useRef<string | null>(null);
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
  const saveTargetRef = useRef<SaveTarget>({ id: null });
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

  // v1.327.0 (calm chat W2-1): the chat list's status dots. Running and
  // waiting are the daemon's (each GET /chat/threads row); unread is this
  // browser's last-viewed stamps (lib/threadStatus), so a stamp written by
  // noteViewed bumps `viewedTick` to re-read them. The open chat is never
  // unread, and while THIS page is answering in it, it reads running at once
  // (the list it holds was fetched before the turn reached the daemon).
  const [viewedTick, setViewedTick] = useState(0);
  const threadStatusMap = useMemo(() => {
    const map = threadStatuses(listedThreads, readLastViewed(), threadId);
    if (busy && threadId && map[threadId] !== "waiting") map[threadId] = "running";
    return map;
    // eslint-disable-next-line react-hooks/exhaustive-deps -- viewedTick re-reads the stamps
  }, [listedThreads, threadId, busy, viewedTick]);
  /** Stamp a chat as seen now (opened, or left). Never throws. */
  function noteViewed(id: string | null | undefined, updatedAt?: string | null) {
    if (!id) return;
    markViewed(id, updatedAt);
    setViewedTick((n) => n + 1);
  }
  // While a row is running or waiting, re-read the list every few seconds:
  // those flags are live in the daemon and nothing pushes their change. Stops
  // when no row is live, while the window is hidden, and on unmount.
  // v1.329.0 (G3): the other groups' rows count too while they are shown
  // (their folded headings carry the dot); the filtered rail polls its own.
  useThreadListPoll(railOnly ? threads : listedThreads, () => void refreshThreads());
  // v1.328.0: how many chats are archived, for the rail's quiet
  // "Archived (N)" link. Once on mount; archive/unarchive re-read it.
  useEffect(() => {
    void refreshArchived();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once, on mount
  }, []);

  // The model catalog for the header picker. v1.250.0 (S-02): through the
  // SHARED hook, so this page renders the catalog the title bar's switcher has
  // usually already loaded instead of re-fetching it on every mount (a raw
  // `get` also bypassed the payload cache entirely, so it could never be
  // seeded). Best-effort as before: no catalog, and the picker stays on
  // "default".
  //
  // v1.148.0 still holds: the ones that AREN'T connected are KEPT. They used to
  // be filtered out entirely, which answered "why isn't my model in the list?"
  // with silence; they now sort last, are badged "offline", and are not
  // selectable — labelled beats hidden, and a dead option the user can't click
  // is not the "silently fails" trap the filter existed to prevent.
  const catalog = useModels();
  useEffect(() => {
    if (catalog.models.length > 0) setModels(catalog.models);
  }, [catalog.models]);

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
  //
  // v1.311.0 (chat-entry-waterfall): opening Chat used to fetch the list
  // TWICE — unscoped on mount (projectId starts null), then again scoped once
  // /projects confirmed the remembered project — and could paint the unscoped
  // list in between. Until /projects settles, a null projectId now means "the
  // project this visit is opening on" (wantedProjectId), so the FIRST request
  // is already scoped and the confirmation finds that scope asked for and
  // fetches nothing. A layout effect so a cached list (showThreadsFor seeds
  // from lib/apiCache) is in the first frame, not a skeleton frame later.
  useLayoutEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);
  useLayoutEffect(() => {
    const scope =
      projectId === null && !projectsSettledRef.current ? wantedProjectId() : projectId;
    // v1.315.0: picking (or leaving) a project shows the rail as it opens on
    // a project again: the filter was a look around, not a new selection.
    setRailOnly(false);
    void showThreadsFor(scope);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  /** v1.311.0: remember the open conversation for THIS window, so leaving
   *  Chat and coming back reopens it (the restore lives with the landing
   *  params below). Called where a thread BECOMES the open one — opened,
   *  deep-linked, minted by its first save — and where its project changes;
   *  New chat forgets it. Imperative on purpose: an effect keyed on threadId
   *  ran AFTER a New chat pressed in the same tick and wrote the old id back. */
  function noteOpenThread() {
    const id = saveTargetRef.current.id;
    if (id) rememberOpenThread(id, projectIdRef.current);
  }

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
      if (window.localStorage.getItem(AUTO_TOOLS_KEY) === "0") setAutoTools(false);
    } catch {
      /* ignore */
    }
    get<{ settings: { default_persona?: string; chat_followups?: boolean } }>("/settings")
      .then((d) => {
        // v1.323.0: follow-up questions only when the user switched them on.
        followupsOnRef.current = d.settings?.chat_followups === true;
        if (saved) return;
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
        /* keep "assistant"; no follow-ups */
      });
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
        projectsSettledRef.current = true;
        if (found) {
          // v1.311.0: a conversation restored before /projects answered may
          // already have scoped the page to this project — confirm it without
          // arming the project's defaults over that thread's own setup.
          applyProject(found, { armDefaults: projectIdRef.current !== found.id });
        } else if (projectIdRef.current === null) {
          // The remembered project is gone (or none was wanted): the rail was
          // possibly asked for its list — fall back to every conversation.
          void showThreadsFor(null);
        }
      })
      .catch(() => {
        /* the panel just shows "No project" */
        if (cancelled) return;
        projectsSettledRef.current = true;
        // Unconfirmed, the page is NOT scoped: the rail must agree with it.
        if (projectIdRef.current === null) void showThreadsFor(null);
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
  //   ?persona=<n>   (v1.223.0) select a persona for THIS conversation — the
  //                  Help page's "Ask the Guide" lands here with persona=guide.
  //                  Local to the conversation on purpose: it must not become
  //                  the saved default just because someone asked the Guide.
  useEffect(() => {
    try {
      const params = new URLSearchParams(window.location.search);
      const ask = (params.get("ask") || "").trim();
      const skill = (params.get("skill") || "").trim();
      const thread = (params.get("thread") || "").trim();
      const wantPersona = (params.get("persona") || "").trim();
      // Redesign S7: the sidebar's "New chat" from another page lands here —
      // a fresh conversation, so the last one is not reopened.
      const fresh = params.get("new") === "1";
      // Redesign S9: "Switch model" from another page lands here, menu open.
      const openModels = params.get("model") === "1";
      // v1.325.0: "Ask Jarvis about this page" — the palette stashed the page
      // it was opened on; it becomes a chip on the next message (one-shot).
      const about = params.get("about") === "page";
      if (about) {
        const ctx = takePageContext();
        if (ctx) setPageCtx(ctx);
        inputRef.current?.focus();
      }
      if (openModels) setModelMenuOpen(true);
      if (wantPersona) selectPersonaLocal(wantPersona);
      if (ask) {
        composer.setText(ask);
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
      else if (!ask && !skill && !wantPersona && !fresh && !about) {
        // v1.311.0: no landing params — reopen the conversation this window
        // had open (a ?thread= link wins and becomes the remembered one; an
        // ask/skill/persona landing is the start of something new). Only
        // inside the scope this visit opens on.
        const open = readOpenThread();
        if (open && open.project === wantedProjectId()) void openThread(open.id, { restore: true });
      }
      if (ask || skill || thread || wantPersona || fresh || openModels || about) {
        // Strip the params so a refresh doesn't resurrect stale state over
        // whatever the user has done since.
        const url = new URL(window.location.href);
        url.searchParams.delete("ask");
        url.searchParams.delete("skill");
        url.searchParams.delete("thread");
        url.searchParams.delete("persona");
        url.searchParams.delete("new");
        url.searchParams.delete("model");
        url.searchParams.delete("about");
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

  /** v1.326.0: the project panel is a DRAWER opened on demand. A press
   *  ("user") opens it with focus, and the drawer gives focus back on close.
   *  The app opening it ("app": a reply or a batch made a file, so its
   *  preview shows) never takes the caret from the composer, and does
   *  nothing on a phone, where the drawer would cover the reply it is
   *  about: the file's chip in that reply is one tap away. `returnTo` is
   *  where focus goes back on close if the opener is gone by then. */
  function showProjectPanel(by: "user" | "app", returnTo: HTMLElement | null = null) {
    if (workspaceOpen) return;
    if (by === "app" && isPhoneWidth()) return;
    drawerReturnRef.current = returnTo;
    setDrawerTakeFocus(by === "user");
    setWorkspaceOpen(true);
  }

  /** The drawer's return target, read when it closes. */
  function drawerReturnTarget(): HTMLElement | null {
    const named = drawerReturnRef.current;
    if (named && named.isConnected) return named;
    return projectButtonRef.current;
  }

  function hideProjectPanel() {
    setWorkspaceOpen(false);
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
    projectsSettledRef.current = true;
    setProjectId(p.id);
    projectIdRef.current = p.id;
    noteOpenThread(); // the remembered conversation's scope follows
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
   *  panel auto-armed, and return the workspace to the user's own default.
   *  `keepFolder` leaves the working folder alone: the open chat was taken
   *  out of its project, and its own saved folder must ride the next save. */
  function clearProject({ keepFolder = false }: { keepFolder?: boolean } = {}) {
    setProjectView("chat"); // a plain chat has no project surfaces
    projectsSettledRef.current = true;
    setProjectId(null);
    projectIdRef.current = null;
    noteOpenThread(); // the remembered conversation's scope follows
    // Already null (cleared before /projects confirmed a remembered project):
    // no state change re-runs the list effect, so unscope the rail here.
    // showThreadsFor skips the request when the rail is already unscoped.
    void showThreadsFor(null);
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
    if (keepFolder) return;
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
    // The scope the rail is SHOWING (v1.311.0) — before /projects confirms a
    // remembered project that is the remembered one, not the still-null
    // projectId, so a refresh never swaps in another scope's list.
    // v1.329.0: a project's rail re-reads its other groups as well.
    const scope =
      listScopeRef.current === undefined ? projectIdRef.current : listScopeRef.current;
    await showThreadsFor(scope, true);
  }

  /** Point the sidebar at `scope`'s conversations (v1.311.0). A scope already
   *  asked for is not asked again unless `force` (a refresh); a NEW scope
   *  paints its cached list at once (the last answer this window saw for that
   *  exact path) and revalidates behind it. Quiet on failure, like before —
   *  the list just goes stale until the next refresh. */
  async function showThreadsFor(scope: string | null, force = false): Promise<void> {
    if (!force && listScopeRef.current === scope) return;
    const path = threadsPath(scope);
    if (listScopeRef.current !== scope) {
      const cached = cachedGet<{ threads?: ThreadSummary[] }>(path);
      if (cached) {
        setThreads(cached.threads ?? []);
        setThreadsLoading(false);
      }
    }
    listScopeRef.current = scope;
    setRailScope(scope);
    // The rail's OWN list is asked for first; a project's rail then reads the
    // other chats (its folded groups), never painted as this project's list.
    const own = get<{ threads: ThreadSummary[] }>(path);
    if (scope) void showOtherThreads(scope, force);
    else setOtherThreads([]);
    try {
      const d = await own;
      cacheSet(path, d);
      if (mountedRef.current && listScopeRef.current === scope) setThreads(d.threads ?? []);
    } catch {
      /* quiet — the sidebar keeps what it shows */
    } finally {
      if (mountedRef.current && listScopeRef.current === scope) setThreadsLoading(false);
    }
  }

  /** v1.329.0 (calm chat W5 G3): the chats outside the rail's project, for
   *  its other (folded) groups and their dots. The unscoped list, painted from
   *  the cache when the rail moves to a project, and dropped if the rail has
   *  left that project before the answer lands. Quiet on failure. */
  async function showOtherThreads(scope: string, force: boolean): Promise<void> {
    const path = threadsPath(null);
    if (!force) {
      const cached = cachedGet<{ threads?: ThreadSummary[] }>(path);
      if (cached) setOtherThreads(cached.threads ?? []);
    }
    try {
      const d = await get<{ threads: ThreadSummary[] }>(path);
      cacheSet(path, d);
      if (mountedRef.current && listScopeRef.current === scope) setOtherThreads(d.threads ?? []);
    } catch {
      /* quiet — the folded groups keep what they show */
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
      // The server's array is the truth for this thread; a Retry offered
      // against an older copy of it (CL5) is retired along with that copy.
      setFailedTurn(null);
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

  // A REMOTE AGENT MESSAGED BACK (v1.285.0). Its line lands in the ROOM this
  // conversation's @-rounds live in (the daemon owns the room; the browser
  // owns this thread), and the room announces it as agent_thread.updated
  // with who = "remote:<name>". When that room is ours, pull the room and
  // mirror every inbound entry we have not shown — then the thread's own
  // save keeps it. Same seen-boundary idiom as the comm effect above.
  const roomEventSeenRef = useRef<string | null>(null);
  const mirrorInFlightRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    const newest = events[0];
    if (!newest) return;
    const boundary = roomEventSeenRef.current;
    roomEventSeenRef.current = newest.id;
    const room = roomOf(messagesRef.current);
    if (!room) return;
    let stale = false;
    for (const e of events) {
      if (e.id === boundary) break;
      if (e.type !== "agent_thread.updated" && e.type !== "remote.message") continue;
      const p = e.payload as { thread_id?: unknown; who?: unknown; agent?: unknown } | null;
      if (p?.thread_id !== room) continue;
      if (e.type === "remote.message" || String(p?.who ?? "").startsWith("remote:")) stale = true;
    }
    if (stale) void mirrorRoomInbound(room);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [events]);

  /** Pull the room and append every inbound remote line not shown yet
   *  (v1.285.0). `base` is the message list to build on when the caller
   *  holds a fresher one than the ref (a thread just opened). Best-effort:
   *  a room the daemon cannot show leaves the conversation as it is. */
  async function mirrorRoomInbound(room: string, base?: ChatMessage[]) {
    // ONE mirror per room at a time, and only into the conversation it was
    // started for: two in-flight fetches would both append against the same
    // pre-append list, and a thread switch during the fetch would land the
    // line — and SAVE it — into whatever the user opened next.
    if (mirrorInFlightRef.current.has(room)) return;
    mirrorInFlightRef.current.add(room);
    const gen = chatGenRef.current;
    let detail: { messages?: RoomEntry[] } | null = null;
    try {
      detail = await get<{ messages?: RoomEntry[] }>(`/agents/threads/${encodeURIComponent(room)}`);
    } catch {
      return;
    } finally {
      mirrorInFlightRef.current.delete(room);
    }
    if (chatGenRef.current !== gen) return; // the conversation changed under us
    const current = base ?? messagesRef.current;
    if (roomOf(current) !== room) return; // not this conversation's room any more
    const seen = new Set(current.map((m) => m.panelAt).filter(Boolean));
    const fresh: ChatMessage[] = [];
    for (const e of detail?.messages ?? []) {
      if (!e || !e.inbound || !e.who?.startsWith("remote:")) continue;
      if (!e.at || seen.has(e.at)) continue;
      const content = (e.content || "").trim();
      if (!content) continue;
      fresh.push({
        role: "assistant",
        content,
        panelWho: e.who,
        panelThreadId: room,
        panelKind: e.kind || "message",
        panelAt: e.at,
        ...(e.documents?.length ? { documents: e.documents } : {}),
      });
    }
    if (fresh.length === 0) return;
    if (base && messagesRef.current !== base && messagesRef.current.length > base.length) return; // the user moved on
    const full = [...current, ...fresh];
    setMessages(full);
    queueSave(full);
    const docs = fresh.flatMap((m) => m.documents ?? []);
    if (docs.length) showDocPreview(docs);
  }

  /** The thread-setup snapshot for saves: exactly what's armed right now. All
   *  five keys always ride along so a cleared skill/model reads as deliberately
   *  cleared, not merely omitted. */
  function currentSetup(): ThreadSetup {
    const { provider, model } = splitChoice(choice);
    return {
      // Via the ref for the same reason the documents are (below): a tool
      // granted mid-turn on an approval card must ride the very save that
      // turn queues, or the grant lives only in live state and is gone when
      // the thread is reopened.
      tools: selectedToolsRef.current.slice(0, MAX_TOOLS),
      // v1.312.0 (W4-2): via the ref for the same reason — a grant made on a
      // card mid-turn must ride the save that turn queues. Uncapped.
      granted_tools: [...grantedToolsRef.current],
      connectors: selectedConnectors.slice(0, MAX_CONNECTORS),
      // Via the ref, not the closure: queueSave runs at turn COMPLETION inside
      // the send's stale closure, and the docs merged during the turn
      // (attachments, made files) must ride that save (v1.166.0).
      documents: threadDocsRef.current.slice(-MAX_THREAD_DOCS),
      skill: activeSkill,
      workspace_dir: workspaceDir ?? "",
      provider: provider ?? "",
      model: model ?? "",
      approval_mode: approvalMode,
      reasoning,
    };
  }

  /** Mark the thread setup as USER-changed: saves start carrying it, and the
   *  effect below persists the change to an already-saved thread right away
   *  (arming a tool then navigating off must not lose it). */
  function markSetupChanged() {
    sendSetupRef.current = true;
    setupEditsRef.current += 1;
    setSetupVersion((v) => v + 1);
  }

  /** "Allow for this conversation" on an approval card (chat's mid-turn ask and
   *  an escalated run's mid-run ask both land here): arm the tool for the rest
   *  of this conversation, exactly as the "+"-menu does.
   *
   *  Two things beyond `setSelectedTools`, both load-bearing:
   *  (1) the REF is written synchronously, because the state update is visible
   *      only to LATER renders while the same turn may escalate before then —
   *      the grant would then be missing from the escalation's `allow_tools`
   *      and the run would re-ask for what the user just approved;
   *  (2) `markSetupChanged`, because a card grant is user consent like any
   *      other arming path — without it a never-otherwise-marked thread saves
   *      `setup: null` forever and the grant is gone on reopen, breaking the
   *      card's own "stops asking here" promise. */
  function armFromApproval(tool: string) {
    // v1.312.0 (W4-2): the GRANT is recorded first and always — uncapped,
    // sent on every turn, persisted with the setup. It never arms anything
    // by itself (the daemon honours it only for a tool armed some other way),
    // so arming below is still what keeps the tool AVAILABLE next turn.
    const granted = !grantedToolsRef.current.includes(tool);
    if (granted) grantedToolsRef.current = [...grantedToolsRef.current, tool];
    const prev = selectedToolsRef.current;
    if (prev.includes(tool) || prev.length >= MAX_TOOLS) {
      // Already armed, or the cap: the grant stands either way. At the cap
      // the tool cannot also be armed — that used to be a silent no-op, so
      // the next turn asked again right after the user said yes. Say what
      // happened and what to do instead.
      if (!prev.includes(tool)) setGrantCapNote(tool);
      if (granted) markSetupChanged();
      return;
    }
    selectedToolsRef.current = [...prev, tool];
    setSelectedTools(selectedToolsRef.current);
    markSetupChanged();
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

  /** Autosave failure handling (v1.226.0). A 404 means the thread is gone
   *  (deleted from another surface): reset the box so the NEXT save re-creates
   *  it via /chat/threads/new, and drop the stale id from the page when it is
   *  the open conversation — before this, every later PUT 404'd silently
   *  forever. Status 0 stays silent (the offline banner covers it); anything
   *  else raises the chip by the composer with a Retry, but only for the
   *  conversation on screen (a retry must never write into another box). */
  function noteSaveFailure(
    e: unknown,
    target: { id: string | null },
    retry: () => void,
  ) {
    const status = e instanceof ApiError ? e.status : -1;
    if (status === 404) {
      const hadId = target.id !== null;
      target.id = null;
      if (saveTargetRef.current === target) setThreadId(null);
      void refreshThreads(); // the sidebar drops the thread that is gone
      // Re-queue the SAME save once into the reset box: /chat/threads/new
      // cannot 404 (hadId guards the impossible repeat, so no loop), and the
      // turn whose save just failed is on disk NOW, not at the next turn.
      if (hadId) retry();
      return;
    }
    if (status === 0 || saveTargetRef.current !== target) return;
    setSaveFailure({
      detail: e instanceof Error ? e.message : String(e),
      retry,
    });
  }

  /**
   * Queue ONE autosave of the full bubble array — never from render. Called
   * at turn START (the typed message is durable before the model is asked,
   * v1.226.0) and once at turn END (chat success, regenerate success, agent
   * finalize, Stop); the chain serializes, so the end save reuses the id the
   * start save minted and a turn can never mint two threads.
   */
  function queueSave(
    msgs: ChatMessage[],
    // The conversation this save belongs to — passed explicitly when the
    // caller's conversation may no longer be the open one (a 404 re-queue,
    // sendAgent's hand-off mark after a torn-down POST; v1.226.0).
    target: SaveTarget = saveTargetRef.current,
  ) {
    if (msgs.length === 0) return;
    msgs = applySettled(msgs, settledSuggestionsRef.current);
    msgs = applySettledCards(msgs, settledCardsRef.current);
    // MESSAGING threads (owner === "daemon") are the server's to write: the
    // daemon has already persisted every message, and PUT would 409. The next
    // chat.thread_updated refetch reconciles the view instead.
    if (target.daemon) return;
    const personaValue = personaForSend();
    // Setup rides along only once it was restored from this thread or the user
    // actually changed something (see sendSetupRef) — never clobber a stored
    // setup with empties just because nothing was re-armed this visit.
    const setup = sendSetupRef.current ? currentSetup() : null;
    const seq = (target.seq = (target.seq ?? 0) + 1);
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
        const res = await putThread(target, body, msgs);
        if (saveTargetRef.current === target) {
          setThreadId(res.id);
          noteOpenThread(); // a new conversation's first save names it
          // On disk again — but only the LATEST queued save may retire the
          // chip (CL2): an older save landing while a newer one is still in
          // the chain says nothing about what is on screen now.
          if (target.seq === seq) setSaveFailure(null);
        }
        await refreshThreads();
      } catch (e) {
        // Best-effort for the CONVERSATION (the bubbles stay), never silent
        // (v1.226.0): a swallowed failure was data loss the user never saw.
        // Retry sends the TRUTH (CL2): the array on screen now, not the one
        // captured when this save failed — that snapshot could predate a
        // reply that a later save already put on disk, and re-sending it
        // rolled the thread back silently. A box that is no longer the open
        // conversation keeps its own array (messagesRef is another thread's).
        noteSaveFailure(e, target, () =>
          queueSave(
            saveTargetRef.current === target ? messagesRef.current : msgs,
            target,
          ),
        );
      }
    });
  }

  /** Rebase a save onto a 409 merge from earlier in the same turn: the
   *  closures of an in-flight turn keep building on the array they captured
   *  (by reference), so a later save whose head IS that captured base is
   *  rewritten onto the server's array the merge established. */
  function rebased(target: SaveTarget, msgs: ChatMessage[]): ChatMessage[] {
    const rb = target.rebase;
    if (!rb) return msgs;
    const k = rb.local.length;
    if (msgs.length < k) return msgs;
    for (let i = 0; i < k; i++) if (msgs[i] !== rb.local[i]) return msgs;
    return [...rb.server, ...msgs.slice(k)];
  }

  /** The ONE thread PUT (CL3, v1.232.0). Carries the version stamp this
   *  window holds; on 409 (the row is newer — another window saved) it
   *  refetches, appends THIS window's new bubbles (everything after
   *  `syncedLen`) onto the server's array, saves that, and shows it — the
   *  other window's turns survive instead of being clobbered. Records the
   *  stamp and sync point on success so the next save is conditional too. */
  async function putThread(
    target: SaveTarget,
    body: ThreadSaveBody,
    msgs: ChatMessage[],
  ): Promise<ThreadSaveResult> {
    let sent = rebased(target, msgs);
    const conditional = (m: ChatMessage[]): ThreadSaveBody => ({
      ...body,
      messages: m,
      ...(target.updatedAt ? { if_updated_at: target.updatedAt } : {}),
    });
    let res: ThreadSaveResult;
    try {
      res = await put<ThreadSaveResult>(
        `/chat/threads/${target.id ?? "new"}`,
        conditional(sent),
      );
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 409 && target.id)) throw e;
      const fresh = await get<ThreadDetail>(`/chat/threads/${target.id}`);
      const server = Array.isArray(fresh.messages) ? fresh.messages : [];
      const k = Math.min(target.syncedLen ?? 0, sent.length);
      target.rebase = { local: sent.slice(0, k), server };
      target.updatedAt = fresh.updated_at ?? undefined;
      sent = [...server, ...sent.slice(k)];
      res = await put<ThreadSaveResult>(
        `/chat/threads/${target.id}`,
        conditional(sent),
      );
    }
    target.id = res.id; // "new" → real id; later saves in this convo reuse it
    if (res.updated_at) target.updatedAt = res.updated_at;
    target.syncedLen = sent.length;
    // v1.311.0: keep this window's copy of the thread as current as the disk,
    // so reopening it (or coming back to Chat) paints what was last saved, not
    // what it held when it was first opened. The setup and persona this save
    // carried ride the copy too (the daemon stores them wholesale), so a paint
    // never restores a setup older than the one on disk.
    const cachePath = `/chat/threads/${res.id}`;
    const had = cachedGet<ThreadDetail>(cachePath);
    cacheSet(cachePath, {
      ...(had ?? { title: "" }),
      id: res.id,
      messages: sent,
      updated_at: target.updatedAt ?? null,
      project_id: body.project_id ?? null,
      ...(body.setup ? { setup: body.setup } : {}),
      ...(body.persona ? { persona: body.persona } : {}),
    } satisfies ThreadDetail);
    if (sent !== msgs && saveTargetRef.current === target) {
      // The merged array is what is on disk: show it (the other window's
      // bubbles included) instead of the stale copy this window built.
      messagesRef.current = sent;
      setMessages(sent);
    }
    return res;
  }

  /** Point the chat's project scope at an opened thread's tag (the
   *  context-follows-the-conversation step of openThread). `folder` lets the
   *  project folder become the workspace when the thread saved none; it is
   *  off while a turn sent on a cached paint is running (v1.311.0), whose
   *  folder was already chosen when it was sent. `keepFolder` (a chat moved
   *  while open) leaves the working folder as it is when the chat leaves its
   *  project: nothing re-applies the thread's setup after a move, so a reset
   *  here would be saved over the chat's own folder. */
  function followThreadProject(
    t: ThreadDetail,
    folder: boolean,
    { keepFolder = false }: { keepFolder?: boolean } = {},
  ) {
    const tpid = t.project_id ?? null;
    if (tpid === projectIdRef.current) return;
    const proj = tpid ? projects.find((x) => x.id === tpid) : undefined;
    if (tpid && proj) {
      applyProject(proj, { armDefaults: false });
    } else if (tpid) {
      // Unknown project (list still loading / deleted) — keep the tag.
      setProjectId(tpid);
      projectIdRef.current = tpid;
      syncProjectUrl(tpid);
    } else {
      clearProject({ keepFolder });
    }
    if (folder && proj?.root && proj.root_exists !== false && !t.setup?.workspace_dir) {
      setWorkspaceDir(proj.root);
    }
  }

  /** The per-conversation setup a fresh open starts from: nothing armed, the
   *  user's DEFAULT posture, no document chips, and nothing to persist until
   *  a thread's own setup restores (or the user changes something). */
  function resetThreadSetup() {
    selectedToolsRef.current = [];
    setSelectedTools([]); // armed tools are per-conversation
    grantedToolsRef.current = []; // so are "this conversation" grants (v1.312.0)
    setGrantCapNote(null);
    setSelectedConnectors([]); // so are connector toggles
    setActiveSkill("");
    // New chat returns to the user's DEFAULT posture (the localStorage one),
    // not the previous thread's — a YOLO grant is per-conversation consent
    // and must never leak into a conversation that never made it.
    try {
      setApprovalMode(asApprovalMode(localStorage.getItem(APPROVAL_MODE_KEY)));
    } catch {
      setApprovalMode("approve_for_me");
    }
    threadDocsRef.current = [];
    setThreadDocs([]);
    sendSetupRef.current = false;
  }

  /** Restore an opened thread's persona, saved setup and document chips.
   *  v1.311.0: lifted out of openThread's success path so the CACHED paint
   *  applies it too. The paint moves the save box onto the real thread, and
   *  the daemon replaces a stored setup wholesale on PUT — so a setup edit (or
   *  a send) made on a paint still sitting on the reset defaults PUT empties
   *  over the conversation's tools, folder, documents, model and posture.
   *  The refs are written now, not at the next render, because a save queued
   *  before React re-renders reads them. `hadSetup`: a setup applied earlier
   *  (the paint's) that this copy no longer carries goes back to the reset. */
  function applyThreadSetup(t: ThreadDetail, hadSetup = false) {
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
      const tools = Array.isArray(setup.tools)
        ? setup.tools.filter((x) => typeof x === "string").slice(0, MAX_TOOLS)
        : [];
      selectedToolsRef.current = tools;
      setSelectedTools(tools);
      // v1.312.0 (W4-2): the conversation's grants come back with it — a
      // reopened thread must not re-ask what the user allowed here.
      grantedToolsRef.current = Array.isArray(setup.granted_tools)
        ? [...new Set(setup.granted_tools.filter((x) => typeof x === "string" && x))]
        : [];
      setSelectedConnectors(
        Array.isArray(setup.connectors)
          ? setup.connectors
              .filter((x) => typeof x === "string")
              .slice(0, MAX_CONNECTORS)
          : [],
      );
      setActiveSkill(typeof setup.skill === "string" ? setup.skill : "");
      // Thread-local posture restore (v1.188.0): absent = the stored
      // default IS "approve_for_me" (the daemon never stores the default),
      // and a reopened thread must come back with the posture it was left
      // on — a YOLO thread reopening as ask-everything would re-card work
      // the user already waved through.
      setApprovalMode(asApprovalMode(setup.approval_mode));
      // Thread-local restore: the panel points at this conversation's folder
      // without touching the localStorage default (New chat returns to it).
      setWorkspaceDir(setup.workspace_dir ? setup.workspace_dir : null);
      setChoice(
        setup.provider && setup.model ? `${setup.provider}::${setup.model}` : "",
      );
      setReasoning(REASONING_LEVELS.includes(setup.reasoning ?? "") ? (setup.reasoning as string) : "");
      sendSetupRef.current = true;
    } else if (hadSetup) {
      resetThreadSetup();
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
    const docs = (recorded.length ? recorded : derived).slice(-MAX_THREAD_DOCS);
    threadDocsRef.current = docs;
    setThreadDocs(docs);
  }

  /** Load a saved thread into the pane (chat-mode concern; resets agent state).
   *  `restore` (v1.311.0): this is the window reopening the conversation it
   *  had open — a thread that has since gone is forgotten quietly, because a
   *  vanished thread is not an error the user made. */
  async function openThread(id: string, opts: { restore?: boolean } = {}) {
    if (id === threadId) {
      closeChatSlot(); // the phone drawer, when it holds the list
      return;
    }
    leaveConversation();
    // v1.327.0 (W2-1): opening a chat is looking at it — its unread dot goes.
    noteViewed(id, listedThreads.find((t) => t.id === id)?.updated_at);
    // Orphan anything in flight from the previous conversation.
    chatGenRef.current += 1;
    const openGen = chatGenRef.current;
    stream.abort(); // tear down a live streaming turn (its throw won't fall back)
    tts.cancel(); // stop reading the previous thread's reply
    awaitingIdRef.current = null;
    awaitingWhoRef.current = null;
    setAwaitingId(null);
    setChatBusy(false);
    setFailedTurn(null);
    setSaveFailure(null); // its Retry belonged to the conversation being left
    setSessionId(null);
    sessionTargetRef.current = "";
    setAttachments([]);
    // The conversation folder chip belongs to the conversation that made it;
    // a reopened thread still works in its saved folder (setup.workspace_dir).
    convFolderRef.current = null;
    setWorkfolder(null);
    setWorkfolderNote("");
    resetThreadSetup(); // until this thread's setup (if any) restores its own
    setPreviewPath(null); // the preview belongs to the previous conversation
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
    composer.reset(); // and so is anything half-typed for the old thread
    // v1.323.0: ...and what was half-typed for THIS one comes back.
    const draft = readDraft(id);
    if (draft) {
      inputFromVoiceRef.current = false; // a restore is never voice input
      composer.setText(draft);
    }
    setSteerBack(false); // a returned steer note went with the box
    setError(null);
    setOffline(false);
    sinceRef.current = null;
    finalizingRef.current = false;
    sendingRef.current = false;
    pinnedRef.current = true; // a loaded thread scrolls to its latest message
    setShowJump(false);
    // v1.311.0: paint the copy this window last saw at once and revalidate
    // behind it — reopening a conversation (or coming back to Chat) no longer
    // waits on the GET to show anything. The SAVE BOX moves with the paint:
    // a turn sent before the GET answers saves into THIS thread under the
    // cached version stamp, and the CL3 409 merge re-bases it onto whatever
    // is newer on disk, so a stale copy can never clobber the thread.
    const path = `/chat/threads/${id}`;
    const cached = cachedGet<ThreadDetail>(path);
    const seeded = Boolean(cached && Array.isArray(cached.messages));
    let seedTarget: SaveTarget | null = null;
    const seedSetupEdits = setupEditsRef.current;
    if (cached && seeded) {
      const daemon = cached.owner === "daemon";
      setMessages(cached.messages);
      // The ref now, not at the next render: "has the user acted since the
      // paint?" below compares against exactly this array, and a GET that
      // answers before React re-renders must not read the LEFT thread's.
      messagesRef.current = cached.messages;
      setCommMeta(
        daemon ? { channel: cached.comm_channel ?? "", display: cached.comm_display ?? "" } : null,
      );
      seedTarget = {
        id: cached.id,
        daemon,
        updatedAt: cached.updated_at ?? undefined,
        syncedLen: cached.messages.length,
      };
      saveTargetRef.current = seedTarget;
      // The setup is painted WITH the messages: the save box above already
      // points at the real thread, so an edit or a send on the paint must sit
      // on this conversation's setup, never on the reset defaults (a PUT
      // replaces the stored setup wholesale).
      applyThreadSetup(cached);
      // ...and its PROJECT (v1.311.0 review): the save and the send read
      // `projectIdRef`, and a paint that left it null sent `project_id: null`
      // — which the daemon reads as "untag" — and an ungrounded turn, while
      // /projects or the thread GET were still on their way.
      followThreadProject(cached, false);
    }
    // Has the user acted on the paint since it was drawn? (See the guard after
    // the GET.) A send leaves `sendingRef` up while it runs and a new array in
    // `messagesRef` once it lands; a setup edit bumps `setupEditsRef`.
    const actedOnPaint = () =>
      seedTarget !== null &&
      cached !== undefined &&
      (sendingRef.current ||
        messagesRef.current !== cached.messages ||
        setupEditsRef.current !== seedSetupEdits);
    try {
      const t = await get<ThreadDetail>(path);
      // Only over the copy this open started from: a save that landed while
      // this GET was out cached a NEWER copy (its own messages, setup and
      // stamp), and the answer to a GET sent before that save is older.
      if (cachedGet<ThreadDetail>(path) === cached) cacheSet(path, t);
      // v1.311.0: New chat or another open while this GET was out owns the
      // pane now. With the cached paint a user can act before the answer
      // lands, so a late answer must not pull the conversation they left
      // back onto the screen (and back into this window's memory).
      if (chatGenRef.current !== openGen) return;
      const msgs = t.messages ?? [];
      // v1.311.0: the cached paint looks loaded, so the user can send (or
      // arm a tool) before this GET answers, and a send does not bump
      // chatGenRef, so the guard above passes. Laying the server copy over
      // that wiped the just-sent bubble mid-stream, swapped the save box out
      // from under the in-flight queueSave (its `=== target` checks then
      // skipped setThreadId / noteOpenThread), re-applied the stored setup
      // over the live turn and, when the durable-at-send PUT landed first,
      // offered Retry on a turn still streaming. So once the user has acted,
      // the screen, the turn state and the save box stay theirs; only the
      // conversation's identity (id, origin banner, compaction chip, project)
      // is taken from the answer.
      if (seedTarget && cached && actedOnPaint()) {
        const isDaemon = t.owner === "daemon";
        seedTarget.daemon = isDaemon; // a messaging thread's saves must no-op
        // Fold the server's stamp in ONLY while it still describes what this
        // window holds: no save has landed since the paint (that recorded
        // its own, newer stamp) and the disk has exactly the painted bubbles.
        // A newer disk copy keeps the paint's stamp, so the next save 409s
        // and the CL3 merge re-bases this window's bubbles onto it; folding
        // its stamp here would let that save clobber the other window's turns.
        if (
          seedTarget.updatedAt === (cached.updated_at ?? undefined) &&
          msgs.length === cached.messages.length
        ) {
          seedTarget.updatedAt = t.updated_at ?? undefined;
        }
        setThreadId(t.id);
        setOpenedTitle({ id: t.id, title: t.title ?? "" });
        setCommMeta(
          isDaemon ? { channel: t.comm_channel ?? "", display: t.comm_display ?? "" } : null,
        );
        void refreshCompaction(t.id);
        followThreadProject(t, false);
        noteOpenThread();
        closeChatSlot(); // the phone drawer, when it holds the list
        return;
      }
      setMessages(msgs);
      setThreadId(t.id);
      setOpenedTitle({ id: t.id, title: t.title ?? "" });
      setAddressee(addresseeOf(msgs)); // still talking to whoever answered last
      // A remote may have messaged back while this was closed (v1.285.0).
      const room = roomOf(msgs);
      if (room) void mirrorRoomInbound(room, msgs);
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
      saveTargetRef.current = {
        id: t.id,
        daemon: isDaemon,
        // The version this window holds (CL3): every save hands it back, so
        // a copy another window has since outrun is refused, not written.
        updatedAt: t.updated_at ?? undefined,
        syncedLen: msgs.length,
      };
      // A TURN LEFT IN FLIGHT (v1.226.0): the last bubble names the session
      // the agent lane was waiting on when the page went away (see sendAgent).
      // Resume exactly the wait the page would have kept: the 1.5s poll +
      // event watcher below terminate because the daemon reconciles
      // interrupted sessions at boot, and a session that already finished
      // appends its reply right now, through the same finalize.
      const pending = pendingSessionOf(msgs);
      if (pending) {
        messagesRef.current = msgs; // finalize may read the ref before React re-renders
        resumedSessionRef.current = pending;
        awaitingIdRef.current = pending;
        setAwaitingId(pending);
        void finalize(pending);
      } else {
        // UNANSWERED (CL5, v1.232.0): the question was saved before the
        // model was asked (v1.226.0) and the daemon restarted mid-turn, so
        // the thread ends on a user bubble with no reply and no wait to
        // resume. That is a failed turn in every way but the React state
        // the failure path sets — set it, so the same Retry the live
        // failure offers is here too instead of a silent dead end.
        // NOT on a MESSAGING thread: the daemon appends the phone's message
        // BEFORE it asks the model (comm/inbound), so a comm thread ends on
        // a user bubble for as long as the daemon is composing — and the
        // chat-lane Retry could not help there anyway (the save box is
        // daemon-owned and no-ops, so its reply would reach neither disk
        // nor the phone). Those threads reconcile via chat.thread_updated.
        const last = msgs[msgs.length - 1];
        if (!isDaemon && last && last.role === "user")
          setFailedTurn({ history: msgs, atts: attachmentsOf(last) });
      }
      // Context follows the conversation: a project-tagged thread scopes the
      // chat to its project; an untagged one unscopes it. armDefaults stays
      // off — the thread's own saved setup (restored below) wins; without a
      // setup, the project folder still becomes the workspace.
      followThreadProject(t, true);
      noteOpenThread(); // after the project followed the conversation
      // The paint already applied the cached copy's setup; the answer is the
      // truth, so it is applied again (a copy that had a setup the disk no
      // longer has goes back to the defaults the open started from).
      applyThreadSetup(t, seeded && Boolean(cached?.setup));
      closeChatSlot(); // the phone drawer, when it holds the list
      inputRef.current?.focus();
    } catch (e) {
      const offlineNow = e instanceof ApiError && e.status === 0;
      if (e instanceof ApiError && e.status === 404) cacheDrop(path);
      if (chatGenRef.current !== openGen) return; // the pane moved on
      // v1.311.0: a turn the user already sent on the paint keeps its screen
      // and its save box — the same rule as the success path. A thread that
      // is gone is re-created by that turn's own save (the 404 path in
      // noteSaveFailure), so wiping the pane here would only lose the turn.
      if (actedOnPaint()) {
        if (offlineNow) setOffline(true);
        return;
      }
      // The cached paint was a promise the GET could not keep (the thread is
      // gone or unreadable): take it back to a fresh conversation rather than
      // leave a dead thread on screen with the save box aimed at it. Offline,
      // the copy stays — it is still the last thing this window saw.
      if (seeded && !offlineNow) {
        setMessages([]);
        setCommMeta(null);
        setThreadId(null);
        saveTargetRef.current = { id: null };
        resetThreadSetup(); // the gone thread's painted setup must not arm the fresh one
        forgetOpenThread();
      }
      if (opts.restore && !offlineNow) {
        forgetOpenThread();
        return;
      }
      if (offlineNow) setOffline(true);
      else setError(e instanceof ApiError ? e.message : String(e));
    }
  }

  /** v1.322.0: the thread menu's delete — the first press arms, the second deletes. */
  function pressDelete(id: string) {
    if (deleteArmedId !== id) {
      setDeleteArmedId(id);
      return;
    }
    setDeleteArmedId(null);
    void removeThread(id);
    setThreadMenu(null);
  }

  async function removeThread(id: string) {
    try {
      await del<void>(`/chat/threads/${id}`);
      setThreads((prev) => prev.filter((t) => t.id !== id));
      // v1.328.0: a chat deleted from the Archived view leaves that list too.
      setArchivedThreads((prev) => prev.filter((t) => t.id !== id));
      if (id === threadId) newChat(); // the open conversation is gone — clear the pane
      void refreshThreads();
    } catch (e) {
      if (e instanceof ApiError && e.status === 0) setOffline(true);
      else setError(e instanceof ApiError ? e.message : String(e));
    }
  }

  /** v1.328.0: re-read the archived chats (every project). Quiet on failure,
   *  like the chat list: the count just stays what it was. */
  async function refreshArchived() {
    try {
      const d = await get<{ threads?: ThreadSummary[] }>(ARCHIVED_THREADS_PATH);
      if (mountedRef.current) {
        setArchivedThreads(Array.isArray(d?.threads) ? d.threads : []);
      }
    } catch {
      /* quiet */
    }
  }

  /** v1.328.0 (calm chat W3-3): the ⋯ menu's Archive, and the dialog's
   *  confirm (`stop` = "Stop and archive"). Nothing running → archived at
   *  once (it is undone from the Archived view). Still working → the daemon
   *  says 409 with what is running and nothing changes until the user
   *  confirms in ArchiveChatDialog. Archiving the OPEN chat leaves it for a
   *  new chat, as Delete does. */
  async function archiveThread(id: string, stop = false) {
    const title =
      listedThreads.find((x) => x.id === id)?.title ??
      (archiveAsk?.id === id ? archiveAsk.title : "");
    setThreadMenu(null);
    setDeleteArmedId(null);
    setArchiveNote(null);
    if (archiveAsk) {
      setArchiveBusy(true);
      setArchiveError(null);
    }
    try {
      const out = await archiveChat(id, stop);
      if (out.kind === "busy") {
        setArchiveAsk({ id, title, running: out.running });
        return;
      }
      setArchiveAsk(null);
      setArchiveNote(stillRunningNote(out.stillRunning, out.note));
      setThreads((prev) => prev.filter((t) => t.id !== id));
      if (id === threadId) newChat(); // archived chats leave the pane, like a delete
      void refreshThreads();
      void refreshArchived();
    } catch (e) {
      const offlineNow = e instanceof ApiError && e.status === 0;
      if (archiveAsk) {
        // The dialog is open: say it there, where the user is looking.
        setArchiveError(
          offlineNow
            ? "Could not reach Iron Jarvis. Try again."
            : `Could not archive this chat. ${e instanceof Error ? e.message : String(e)}`,
        );
      } else if (offlineNow) setOffline(true);
      else setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setArchiveBusy(false);
    }
  }

  /** v1.328.0: bring an archived chat back into the chat list; `open` also
   *  opens it (pressing an archived row = bring it back and read it). */
  async function unarchiveThread(id: string, open = false) {
    setThreadMenu(null);
    setDeleteArmedId(null);
    try {
      await unarchiveChat(id);
      setArchivedThreads((prev) => prev.filter((t) => t.id !== id));
      void refreshThreads();
      void refreshArchived();
      if (open) {
        setArchivedView(false);
        await openThread(id);
      }
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
    const convGen = convGenRef.current;
    setUploading(true);
    uploadingRef.current = true;
    try {
      // v1.275.0: uploads run a few at a time, results kept in the user's
      // order. Four files used to be five sequential round trips (each with
      // a main-thread base64 read) before the first chip appeared.
      const uploaded: (UploadedFile | undefined)[] = new Array(accepted.length);
      // v1.322.0: one file that fails never costs the others — each upload
      // settles on its own and the ones that made it are attached.
      const failed: { name: string; why: string }[] = [];
      let wentOffline = false;
      let next = 0;
      const worker = async () => {
        while (next < accepted.length) {
          const i = next++;
          const f = accepted[i];
          try {
            const content_b64 = await readAsBase64(f);
            const res = await post<UploadResult>("/documents/upload", {
              filename: f.name,
              content_b64,
            });
            uploaded[i] = { name: res.name, path: res.path, bytes: f.size };
          } catch (e) {
            if (e instanceof ApiError && e.status === 0) wentOffline = true;
            failed.push({ name: f.name, why: e instanceof ApiError ? e.message : String(e) });
          }
        }
      };
      await Promise.all(
        Array.from({ length: Math.min(UPLOAD_CONCURRENCY, accepted.length) }, worker),
      );
      // The user left this conversation while the files went up: they are not
      // this new conversation's files (and a send queued behind them is gone).
      if (convGen !== convGenRef.current) return;
      const ok = uploaded.filter((u): u is UploadedFile => !!u);
      if (ok.length) {
        const placed = await placeInWorkfolder(ok);
        if (convGen !== convGenRef.current) return;
        setAttachments((prev) => [...prev, ...placed].slice(0, MAX_ATTACHMENTS));
      }
      if (wentOffline) setOffline(true);
      if (failed.length) {
        setError(
          failed.length === 1
            ? `Couldn't attach ${failed[0].name}: ${failed[0].why}`
            : `Couldn't attach ${failed.length} files (${failed.map((f) => f.name).join(", ")}) — the others are attached.`,
        );
        // A send queued behind the uploads waits for the user to look.
        if (!ok.length) queuedSendRef.current = false;
      }
    } catch (e) {
      if (e instanceof ApiError && e.status === 0) setOffline(true);
      else setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      uploadingRef.current = false;
      setUploading(false);
      // A send the user pressed while this ran fires from the effect below,
      // AFTER the render that commits the new attachments — firing here would
      // build the body from `attachmentsRef` before it mirrored them.
    }
  }

  // v1.275.0: the queued send goes the moment the uploads settle, with whatever
  // the box holds at that moment (anything typed since counts).
  useEffect(() => {
    if (uploading || !queuedSendRef.current) return;
    queuedSendRef.current = false;
    send(composer.get().text);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `send` is the component's own hoisted function
  }, [uploading]);

  /**
   * THE CONVERSATION GETS A FOLDER (v1.244.0).
   *
   * Reported: "attach a document and ask for work — a lagging delay, a request
   * for information, then a completed screen with absolutely no output; it
   * works inside a project." Replayed on the live model: with no project the
   * chat had no folder and no file tools, so it could not make the workbook it
   * was asked for, handed the job to an agent in a hidden AppData scratch
   * folder, and that agent built the file, stopped twice for approval, ran out
   * of steps and said "Task failed" beside a file nobody could find.
   *
   * Selecting a project avoids all of it by binding a REAL folder and arming
   * the file essentials, so this does the same for a plain chat the moment it
   * is handed a file: the daemon makes a dated folder under Documents\Iron
   * Jarvis (or keeps the folder the chat already points at, when the app can
   * work in it), copies the uploads in, and the chat binds it and arms
   * PROJECT_FILE_TOOLS over an empty set. Everything the project path already
   * does — the folder grounding, the tools, a hand-off carrying the folder —
   * then applies unchanged. The folder is PER CONVERSATION: it rides the
   * thread's setup and is never written to the sticky WORKSPACE_KEY default.
   *
   * Never loses the file: if the folder cannot be made, the upload attaches
   * exactly as it always did and the user is told why.
   */
  async function placeInWorkfolder(files: UploadedFile[]): Promise<UploadedFile[]> {
    if (projectIdRef.current || files.length === 0) return files;
    const own = convFolderRef.current;
    let res: WorkfolderResult;
    try {
      res = await post<WorkfolderResult>("/documents/workfolder", {
        files: files.map((f) => f.path),
        ...(own ? { into: own } : { title: files[0].name, prefer: workspaceDir ?? "" }),
      });
    } catch (e) {
      if (e instanceof ApiError && e.status === 0) throw e; // offline — the caller says so
      setError(
        `Attached, but this chat has no folder to save its work in: ${
          e instanceof ApiError ? e.message : String(e)
        }`,
      );
      return files;
    }
    if (res.created) {
      convFolderRef.current = res.path;
      setWorkfolder(res.path);
    }
    setWorkfolderNote(res.note ?? "");
    if (res.path && res.path !== workspaceDir) setWorkspaceDir(res.path);
    if (selectedToolsRef.current.length === 0) {
      setSelectedTools(PROJECT_FILE_TOOLS);
      autoArmedRef.current = true;
    }
    markSetupChanged(); // the folder + the tools ride this conversation's setup
    const copies = new Map(res.files.map((c) => [c.source, c]));
    return files.map((f) => {
      const c = copies.get(f.path);
      return c ? { name: c.name, path: c.path, bytes: f.bytes } : f;
    });
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
  // The "/" and "@" token rules, the skill/agent match lists and the caret
  // they key off all moved into the composer components (v1.250.0, S-05):
  // ComposerInput, SlashPicker and AtPicker each derive them from the store, so
  // deriving them HERE would re-render the whole page on every keystroke for
  // values only those three subtrees ever read. The rule itself is unchanged —
  // "/" must open a word, which is what keeps `http://x`, `C:/Users`, `and/or`
  // and `24/7` from flickering a dropdown (v1.105.0).

  /** Mentions in the composer that resolve to a REAL agent — the send path
   *  routes to the panel only when at least one does, so "@ 9am" or an email
   *  address never diverts a normal message. Called with the text being sent
   *  (a function, not a memo: watching the text is what we just stopped). */
  const liveMentionsIn = useCallback(
    (text: string) => {
      const known = new Set((mentionable ?? []).map((a) => a.mention.toLowerCase()));
      const found = text.match(/(?<![A-Za-z0-9._-])@([A-Za-z0-9][A-Za-z0-9._-]*)/g) ?? [];
      return found
        .map((t) => t.slice(1).replace(/[._-]+$/, "").toLowerCase())
        .filter((t) => known.has(t));
    },
    [mentionable],
  );

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

  /** Lazily fetch the saved workflow defs (the "+" Run-a-workflow flyout,
   *  v1.170.0). Fetch once, retry on the next open. A FAILED fetch sets the
   *  "error" sentinel, never [] — the empty state is a positive factual claim
   *  ("you have none") the daemon never made. */
  function ensureWorkflows() {
    if (workflowsFetchedRef.current) return;
    workflowsFetchedRef.current = true;
    get<{ workflows: { name: string; description?: string }[] }>("/workflows")
      .then((d) => setSavedWorkflows(d.workflows ?? []))
      .catch(() => {
        workflowsFetchedRef.current = false; // a later open retries
        setSavedWorkflows("error");
      });
  }

  /** Start a SAVED workflow from the "+" menu (v1.170.0). NAME-ONLY body
   *  (contract 1): the daemon resolves the stored steps AND the project pin
   *  server-side — sending steps here would silently strip the pin. The run
   *  lands in the thread as a local card row carrying the live chip, and on a
   *  USER-owned thread it persists like any other message so the chip survives
   *  navigation (its run-record poll settles the final truth on reload). On a
   *  daemon-owned MESSAGING thread it could NOT persist (queueSave no-ops and
   *  refetchCommThread is replace-only), so the entry point is disabled there
   *  and this refuses defensively rather than launch a run nothing watches. */
  async function runSavedWorkflow(name: string) {
    setToolsOpen(false);
    setPlusSub(null);
    setError(null);
    if (commMetaRef.current) {
      setError(
        "This messaging thread is server-owned, so the run card can't persist here — run it from the Workflows page instead.",
      );
      return;
    }
    try {
      const rec = await post<WorkflowRun>("/workflows/run", { name });
      const runId = String(rec.id ?? "");
      if (!runId) throw new Error("the run record came back without an id");
      const next: ChatMessage[] = [
        ...messagesRef.current,
        {
          role: "assistant",
          content: `Started the “${name}” workflow — it runs here.`,
          workflowRun: { runId, name },
        },
      ];
      pinnedRef.current = true; // the new row scrolls into view
      setMessages(next);
      queueSave(next);
    } catch (e) {
      if (e instanceof ApiError && e.status === 0) setOffline(true);
      else setError(e instanceof ApiError ? e.message : String(e));
    }
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

  /** Lazily fetch + cache the skill catalog the first time "/" opens the
   *  picker. v1.250.0 (S-05): a CALLBACK rather than an effect on
   *  `slashActive`, because the page no longer watches the composer's text —
   *  the textarea and the picker both call it when the token opens, and the
   *  ref makes the second call a no-op. */
  /** v1.324.0: the apps' prompts / resources, re-read at most once a minute
   *  (the daemon caches each app's list for as long). A failure lists none. */
  const loadPackPrompts = useCallback(() => {
    const now = Date.now();
    if (now - packListsAtRef.current.prompts < 60_000) return;
    packListsAtRef.current.prompts = now;
    fetchPackPrompts()
      .then((d) => setPackPrompts(d.prompts))
      .catch(() => setPackPrompts([]));
  }, []);
  const loadPackResources = useCallback(() => {
    const now = Date.now();
    if (now - packListsAtRef.current.resources < 60_000) return;
    packListsAtRef.current.resources = now;
    fetchPackResources()
      .then((d) => setPackResources(d.resources))
      .catch(() => setPackResources([]));
  }, []);
  const onSlashOpened = useCallback(() => {
    loadSkillsOnce();
    loadPackPrompts();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadPackPrompts]);
  const pickAppResource = useCallback((r: PackResource) => {
    setAppResources((cur) =>
      cur.some((x) => x.pack === r.pack && x.uri === r.uri) || cur.length >= 8
        ? cur
        : [...cur, r],
    );
  }, []);
  // v1.328.0: "@" → a saved chat. At most CHAT_REFS_MAX; a pick past that is
  // refused with the quiet note, the chips unchanged.
  const pickChatRef = useCallback(
    (c: ChatRefPick) => {
      const { next, full } = addChatRef(chatRefsRef.current, c);
      if (full) {
        setChatRefNote(CHAT_REFS_FULL_NOTE);
        return;
      }
      setChatRefNote("");
      if (next !== chatRefsRef.current) setChatRefs(next);
    },
    [setChatRefs],
  );
  // v1.329.0: "@" → a file or folder in the project. A FILE joins the
  // attachments exactly as "+ Attach" leaves an upload (same chip, same
  // `attachments` request field; the daemon reads the absolute path through
  // its file policy). A FOLDER becomes the working folder exactly as
  // "+ Choose a working folder" does (`workspace_dir`).
  const pickFolderRef = useRef<(path: string) => void>(() => {});
  const pickProjectEntry = useCallback((e: ProjectEntry) => {
    if (e.kind === "folder") {
      pickFolderRef.current(e.path);
      return;
    }
    const cur = attachmentsRef.current;
    if (cur.some((a) => a.path === e.path)) return;
    if (cur.length >= MAX_ATTACHMENTS) {
      setError(`Up to ${MAX_ATTACHMENTS} files per message.`);
      return;
    }
    setError(null);
    setAttachments((prev) =>
      prev.some((a) => a.path === e.path) || prev.length >= MAX_ATTACHMENTS
        ? prev
        : [...prev, { name: e.name, path: e.path, bytes: e.size ?? 0 }],
    );
  }, []);
  pickFolderRef.current = chooseWorkspace;
  // v1.329.0: the "@" menu's Files section searches the ACTIVE project's
  // folder; no project (or its folder missing) = no section.
  const atFilesRoot =
    activeProject && activeProject.root && activeProject.root_exists !== false
      ? activeProject.root
      : null;
  const attachedPaths = useMemo(() => attachments.map((a) => a.path), [attachments]);
  // v1.329.0: the working folder is a folder INSIDE the project (not its own
  // folder): the composer card says so (`working-folder-chip`).
  const projectSubfolder =
    atFilesRoot && workspaceDir && workfolder === null && !sameFolderPath(workspaceDir, atFilesRoot)
      ? workspaceDir
      : null;
  // v1.329.0: which project a saved chat belongs to, for the "@" row's quiet
  // second part: what the daemon said on the row, else what the chat list
  // here knows; the name from the project list. Unknown = null (the row then
  // shows the day instead).
  const chatProjectName = useCallback(
    (c: ChatRefPick): string | null => {
      let pid: string | null | undefined = c.projectId;
      if (pid === undefined) {
        const t = threads.find((x) => x.id === c.id) ?? archivedThreads.find((x) => x.id === c.id);
        pid = t ? (t.project_id ?? null) : undefined;
      }
      if (!pid) return null;
      return projects.find((p) => p.id === pid)?.name ?? null;
    },
    [threads, archivedThreads, projects],
  );

  const loadSkillsOnce = useCallback(() => {
    if (skillsFetchedRef.current) return;
    skillsFetchedRef.current = true;
    get<{ skills: SkillOption[] }>("/skills")
      .then((d) => setSkills(d.skills ?? []))
      .catch(() => {
        skillsFetchedRef.current = false; // a later "/" retries
        setSkills([]);
      });
  }, []);

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

  // Keeping the highlighted skill row pinned to the top as the query changes
  // moved into SlashPicker (v1.250.0, S-05) — it is the only thing that reads
  // the query, and watching it here re-rendered the page on every keystroke.

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

  // Close the composer's tools menu on any outside click (v1.326.0).
  useEffect(() => {
    if (!toolMenuOpen) return;
    const onDown = (e: MouseEvent) => {
      if (!toolMenuRef.current?.contains(e.target as Node)) setToolMenuOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [toolMenuOpen]);

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
  // v1.326.0: the tools armed by hand, web research apart (it has its own
  // chip), and the composer's tools chip words.
  const armedTools = selectedTools.filter((n) => !WEB_TOOLS.includes(n));
  const toolsChip = toolsChipWords(autoTools, armedTools.length);

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

  /** The page's half of picking a skill: arm the chip and persist it with the
   *  thread. The TEXT half — splice out ONLY the "/token" being typed, keep the
   *  rest of the message, place the caret — is `applySkillPick`, which both
   *  the dropdown's click and the textarea's Enter call (v1.250.0, S-05). */
  function pickSkill(name: string) {
    setActiveSkill(name);
    markSetupChanged();
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
    // v1.315.0 (jump-pill-on-empty-state): the empty state has no latest
    // message. It opens at its TOP — the lead line and the "Connect a model"
    // card first — instead of being scrolled to its bottom, and the reader is
    // re-pinned so the first message of the conversation still follows.
    if (messages.length === 0 && !busy) {
      pinnedRef.current = true;
      setShowJump(false);
      const el = scrollRef.current;
      if (el) el.scrollTop = 0;
      return;
    }
    if (!pinnedRef.current) return;
    const opening = openingRef.current;
    openingRef.current = false;
    scrollToLatest(bottomRef.current, busy || opening ? "auto" : "smooth");
    // v1.257.0 (S-02): `runStream.text` is deliberately NOT a dependency any
    // more. It used to be, and because scrollIntoView walks every scrollable
    // ancestor, each agent token cost a whole page render plus a synchronous
    // layout. <AgentLiveText> reports its growth through scrollLiveIntoView
    // instead; listing the text here again would restore both costs.
  }, [messages, awaitingId, chatBusy, progress.length, busy]);

  // v1.250.0 (S-03): the streamed reply no longer re-renders this page, so its
  // growth cannot be an effect dependency — <LiveReply> calls this after each
  // frame's flush instead, which is the same "scroll once the text grew" rule
  // in the same pinned-only, instant-while-streaming shape.
  const busyRef = useRef(busy);
  busyRef.current = busy;
  const scrollLiveIntoView = useCallback(() => {
    if (!pinnedRef.current) return;
    scrollToLatest(bottomRef.current, busyRef.current ? "auto" : "smooth");
  }, []);

  // Track the reader's pin state; releasing the pin surfaces a "Jump to latest"
  // pill instead of fighting them for the scroll position.
  function onThreadScroll() {
    const el = scrollRef.current;
    if (!el) return;
    // v1.315.0: scrolling the empty state is reading it, not leaving a
    // conversation behind — there is nothing below to jump to.
    if (messages.length === 0 && !busy) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    pinnedRef.current = nearBottom;
    setShowJump((prev) => (prev === !nearBottom ? prev : !nearBottom));
  }

  // A chart or table that finishes laying out after the scroll grows the
  // transcript under a reader who is following it: keep them at the bottom
  // (never one who scrolled up, and never over the empty state). It scrolls to
  // the same sentinel as every other bottom scroll here (`scrollToLatest`):
  // two different bottoms made a streaming reply bounce on every line.
  useRepinOnGrowth(
    scrollRef,
    bottomRef,
    () => pinnedRef.current && !(messagesRef.current.length === 0 && !busyRef.current),
  );

  function jumpToLatest() {
    pinnedRef.current = true;
    setShowJump(false);
    scrollToLatest(bottomRef.current, "smooth");
  }

  // The composer's auto-grow moved into ComposerInput with the text it keys
  // off (v1.250.0, S-05).

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
      // A hand-off's reply is the AGENT's, and says so (v1.284.0) — the same
      // chip a panel reply wears, so "who said this" reads the same either way.
      const who = awaitingWhoRef.current;
      awaitingWhoRef.current = null;
      const full: ChatMessage[] = [
        ...stripAwaiting(messagesRef.current), // the wait is over (v1.226.0)
        {
          role: "assistant",
          content,
          fromSession: id,
          ...(runResult ? { runResult } : {}),
          ...(who ? { panelWho: who } : {}),
        },
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
      if (
        e instanceof ApiError &&
        e.status === 404 &&
        resumedSessionRef.current === id
      ) {
        // A RESUMED wait whose session record is gone (pruned since the
        // thread was saved; v1.226.0): merely opening the thread must not
        // shout "no such session". Strip the mark quietly and save it so the
        // next open doesn't ask again.
        const kept = stripAwaiting(messagesRef.current);
        setMessages(kept);
        queueSave(kept);
        awaitingIdRef.current = null;
        setAwaitingId(null);
        return;
      }
      // Hard failure: surface it and stop waiting so the turn doesn't hang forever.
      setError(e instanceof ApiError ? e.message : String(e));
      awaitingWhoRef.current = null;
      // The typed message still survives navigation — minus the wait mark
      // (v1.226.0), or a reopen would resume a wait that already failed.
      const kept = stripAwaiting(messagesRef.current);
      setMessages(kept);
      queueSave(kept);
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

  // MID-RUN APPROVALS (v1.189.0): a PAUSED escalated run's ask renders HERE —
  // under the turn the user is actually watching — via approval.requested/
  // approval.resolved events tagged with this session's id. The measured
  // failure: the run's shell asks died on the headless resolver and the only
  // visible artifact was a capability proposal on the Tools page. Same card,
  // same POST /chat/approvals/{id} route as chat's own mid-turn ask — the
  // registry is shared platform-side, so one answer path serves both pauses.
  //
  // ONE CARD PER PENDING ASK (v1.227.0, A1). The runtime asks in PARALLEL
  // batches — the measured job published 4-5 `approval.requested` in the same
  // microsecond — and the old single-slot state kept only the newest: the
  // user answered one card, the rest stayed pending invisibly, and 300 s
  // later each expired as "denied by the clock". The pending set is FOLDED
  // from the events (newest-first, so a resolve is always seen before the
  // request it closes): every request for this session whose id has no
  // later resolve is a card, oldest first, and answering one leaves the
  // others exactly where they were.
  const askFold = useMemo(() => {
    const resolved = new Set<string>();
    const asks = new Map<string, SessionAsk>();
    if (!awaitingId) return { asks: [] as SessionAsk[], resolved };
    const boundary = sinceRef.current;
    for (const e of events) {
      if (e.id === boundary) break;
      if (e.session_id !== awaitingId) continue;
      if (e.type === "approval.resolved") {
        resolved.add(String(e.payload?.approval_id ?? ""));
        continue;
      }
      if (e.type === "approval.requested") {
        const id = String(e.payload?.approval_id ?? "");
        if (!id || resolved.has(id) || asks.has(id)) continue;
        const p = (e.payload ?? {}) as Record<string, unknown>;
        asks.set(id, {
          id,
          tool: String(e.payload?.tool ?? ""),
          args: (e.payload?.args ?? undefined) as
            | Record<string, unknown>
            | undefined,
          ...(typeof p.count === "number" && p.count > 1 ? { count: p.count } : {}),
          ...(Array.isArray(p.examples)
            ? { examples: p.examples as Record<string, unknown>[] }
            : {}),
          ...(typeof p.timeout_s === "number" ? { timeoutS: p.timeout_s } : {}),
        });
      }
    }
    return { asks: Array.from(asks.values()).reverse(), resolved }; // oldest first
  }, [events, awaitingId]);

  // WITHOUT A LIVE EVENT (A10, v1.232.0): a reloaded page resumes the wait
  // but has no event replay, so a run already paused on an ask showed no
  // card until the bell caught it (<=15 s). The finalize poll below also
  // reads /chat/approvals/pending for the awaited session; that route lists
  // the tool only (never args — its posture), so the card shows the tool.
  // An ask the events already resolved is never resurrected from the poll.
  const [polledAsks, setPolledAsks] = useState<SessionAsk[]>([]);
  async function pollPendingAsks(id: string) {
    try {
      const r = await get<{
        approvals?: {
          id?: unknown;
          tool?: unknown;
          session_id?: unknown;
          count?: unknown;
          timeout_s?: unknown;
        }[];
      }>("/chat/approvals/pending");
      if (awaitingIdRef.current !== id) return;
      const mine = (r.approvals ?? [])
        .filter((a) => a.session_id === id && typeof a.id === "string")
        .map((a) => ({
          id: String(a.id),
          tool: String(a.tool ?? ""),
          ...(typeof a.count === "number" && a.count > 1 ? { count: a.count } : {}),
          ...(typeof a.timeout_s === "number" ? { timeoutS: a.timeout_s } : {}),
        }));
      setPolledAsks(mine);
    } catch {
      /* best-effort — the event fold and the bell still cover it */
    }
  }
  const sessionAsks = useMemo(() => {
    const seen = new Set(askFold.asks.map((a) => a.id));
    const extra = polledAsks.filter(
      (a) => !seen.has(a.id) && !askFold.resolved.has(a.id),
    );
    return extra.length ? [...askFold.asks, ...extra] : askFold.asks;
  }, [askFold, polledAsks]);

  // CALM CHAT W1-6 (v1.326.0): every question waiting on the user, in one
  // list, drawn in the composer's place (<DockAsk>): this chat turn's
  // approval and its apps' open questions, then an escalated run's asks.
  // Gated exactly as the bubbles that used to carry them were (chatBusy /
  // awaiting), so nothing shows that the transcript would not have shown.
  const dockAsks = useMemo(
    () =>
      collectDockAsks({
        approval: chatBusy ? stream.approval : null,
        mcpAsks: chatBusy ? stream.mcpAsks : null,
        sessionApprovals: awaiting
          ? sessionAsks.map((ask) => ({
              id: ask.id,
              callId: "",
              tool: ask.tool,
              args: ask.args,
              count: ask.count,
              examples: ask.examples,
              timeoutS: ask.timeoutS,
            }))
          : null,
      }),
    [chatBusy, stream.approval, stream.mcpAsks, awaiting, sessionAsks],
  );
  const askInDock = dockAsks.length > 0;
  // The question answered and the card gone: the caret goes back to the box.
  const focusComposerAfterAsk = useCallback(() => inputRef.current?.focus(), []);

  // FALLBACK: if the /events socket is down, poll the session until it finishes.
  // The interval is torn down whenever the turn ends or the component unmounts.
  useEffect(() => {
    if (!awaitingId) return;
    void pollPendingAsks(awaitingId); // a reload's first look, at once (A10)
    return () => {
      setPolledAsks([]);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [awaitingId]);
  // v1.257.0 (S-02): the 1.5 s REPEAT runs through useVisibleInterval. Two
  // fetches every 1.5 s kept firing while the window was minimised — this page
  // was the last polling surface still on a bare setInterval after v1.250.0
  // (S-09) moved nine others. Same cadence, torn down while hidden, and one
  // catch-up run on the hidden -> visible edge. The callback reads the REF, not
  // the closed-over id, so a recreated closure never restarts the timer.
  useVisibleInterval(
    () => {
      const id = awaitingIdRef.current;
      if (!id) return;
      void finalize(id);
      void pollPendingAsks(id);
    },
    1500,
    !!awaitingId,
  );

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
      composer.setText(appendDictation(composer.get().text, delta));
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

  // Voice Chat auto-send moved into <VoiceAutoSend> (v1.250.0, S-05): the
  // effect needs every dictated character, the page does not. Everything the
  // page owns is ANDed into `armed` below, where it re-renders nothing.

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
        messages: toRequestMessages(messages),
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
    // v1.325.0: "Try again with…" names a model for ONE turn; the
    // conversation's own choice is untouched.
    const turnChoice = turnChoiceRef.current ?? choice;
    const { provider, model } = splitChoice(turnChoice);
    const personaValue = personaForSend();
    return {
      // Full conversation every turn — the backend is stateless here.
      messages: toRequestMessages(history),
      // v1.327.0 (calm chat W2-1): which saved chat this turn belongs to, so
      // the daemon can show it as running / waiting in the chat list. Read
      // from the save box (sends fire from stale closures); a new chat has
      // no id until its first save lands, and sends none.
      ...(saveTargetRef.current.id ? { thread_id: saveTargetRef.current.id } : {}),
      // Redesign S3: which device asked, so a per-device setting changed in
      // chat (the theme) lands here and not on every screen.
      device_id: getDeviceId(),
      ...(provider ? { provider } : {}),
      ...(model ? { model } : {}),
      ...(personaValue ? { persona: personaValue } : {}),
      ...(atts.length ? { attachments: atts.map((a) => a.path) } : {}),
      // THE CONVERSATION'S FILES (v1.251.0, C-01). History goes over as
      // {role, content} text, so a follow-up ("now turn that into a memo")
      // used to carry no file at all and the chat asked for it again. This is
      // the SAME list the Files rail shows — via the ref, like `documents` in
      // the setup save, because a send can fire from a stale closure — minus
      // the files this message is already attaching, and capped so a long
      // conversation cannot crowd out the turn's own prompt.
      ...(() => {
        const here = new Set(atts.map((a) => a.path));
        const carried = threadDocsRef.current
          .filter((p) => !here.has(p))
          .slice(-MAX_CARRIED_FILES);
        return carried.length ? { thread_files: carried } : {};
      })(),
      // The reply's playbook + armed tool loop (both sticky across turns).
      ...(activeSkill ? { skill: activeSkill } : {}),
      ...(selectedTools.length ? { tools: selectedTools.slice(0, MAX_TOOLS) } : {}),
      // v1.312.0 (W4-2): this conversation's grants, uncapped, every turn.
      // Via the ref (sends fire from stale closures). A grant only lifts the
      // ask for a tool this turn arms some other way — it never arms one.
      ...(grantedToolsRef.current.length
        ? { granted_tools: [...grantedToolsRef.current] }
        : {}),
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
      // The approval posture (v1.188.0) — only the non-default rides, so a
      // pre-v1.188.0 daemon sees a body it already understands.
      ...(approvalMode !== "approve_for_me"
        ? { approval_mode: approvalMode }
        : {}),
      // v1.263.0: the reasoning level — only when the picked model offers it,
      // so a level chosen for one model never rides a request to another and
      // a pre-v1.263.0 daemon sees a body it already understands.
      ...(reasoning && reasoningLevelsFor(turnChoice).includes(reasoning) ? { reasoning } : {}),
    };
  }

  /** The provider that ACTUALLY answered, when the user explicitly picked a
   *  DIFFERENT one (capability reroute / failover) — "" when there is nothing
   *  to disclose (default routing, same provider, or no info). Powers the
   *  honesty chip: a local-model turn silently served by a subscription CLI
   *  must never be invisible. */
  function servedByOther(served?: string): string {
    const requested = splitChoice(turnChoiceRef.current ?? choice).provider ?? "";
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

  /** v1.311.0 (W3-1): the stream sent `reset` and a rewrite follows. The
   *  consumed counter still points into the discarded text, so the rewrite
   *  would be voiced from that offset (or not at all until it outgrew it).
   *  An explicit signal from the stream, not an inference from a shorter
   *  `full`: a rewrite whose first token is longer than the discarded text
   *  never looks shorter. Before the first token there is nothing to undo. */
  function resetTTSFeed() {
    if (!tts.enabled || !ttsStreamStartedRef.current) return;
    tts.resetStream();
  }

  /** Persist the thread setup with an EXPLICIT documents list. State updates
   *  are async, so the doc-chip saves can't rely on currentSetup() seeing the
   *  new list — and the setupVersion effect skips a not-yet-saved thread,
   *  which would lose chips generated on a conversation's FIRST turn. Riding
   *  the serialized save chain means the turn's own save has already resolved
   *  the thread id (the chain mutates the shared target) by the time this
   *  PUT runs. */
  function queueSaveDocs(
    docs: string[],
    target: SaveTarget = saveTargetRef.current, // see queueSave
  ) {
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
        const res = await putThread(target, body, msgs);
        if (saveTargetRef.current === target) {
          setThreadId(res.id);
          noteOpenThread(); // a new conversation's first save names it
          setSaveFailure(null);
        }
      } catch (e) {
        // Same contract as queueSave (v1.226.0): 404 resets, 0 is silent,
        // the rest raise the chip — Retry re-queues THIS doc save.
        noteSaveFailure(e, target, () => queueSaveDocs(docs, target));
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
    showProjectPanel("app");
  }

  /** Reopen a remembered document's preview (the chip's click). */
  function openDocPreview(path: string) {
    setPreviewPath(path);
    showProjectPanel("user");
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

  /** v1.323.0: ask the model that wrote the reply for up to three follow-up
   *  questions — only when the user switched them on. Shown while that reply
   *  is still the newest; a newer turn, Stop or a switch drops them. */
  function askFollowups(list: ChatMessage[], route?: TurnRoute | null) {
    if (!followupsOnRef.current) return;
    const gen = chatGenRef.current;
    const len = list.length;
    void fetchFollowups(
      toRequestMessages(list.filter((x) => !x.continuation)),
      route?.provider || undefined,
      route?.model || undefined,
    ).then((items) => {
      if (chatGenRef.current !== gen || !items.length) return;
      setFollowups({ forLen: len, items });
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
    if (!composer.get().text.trim()) composer.setText(lastUser.content);
  }

  /**
   * CHAT MODE core: one /chat completion over `history` (which must end with a
   * user message). Shared by sendChat and regenerate. Tries token streaming
   * first (live bubble + incremental voice); on ANY streaming failure it falls
   * back to the direct /chat POST verbatim. On success the reply is appended and
   * the turn autosaved (the ONLY chat-mode save site).
   */
  async function completeChat(
    history: ChatMessage[],
    atts: UploadedFile[],
    opts: { choice?: string } = {},
  ) {
    const gen = chatGenRef.current;
    // v1.325.0: "Try again with…" — this turn only; read by buildChatBody and
    // servedByOther, cleared in the finally below.
    turnChoiceRef.current = opts.choice ?? null;
    stoppedRef.current = false;
    setFollowups(null); // they answered the reply this turn replaces or follows
    setMessages(history);
    // DURABLE AT SEND (v1.226.0): the typed message is on disk BEFORE the
    // model is asked — a reload or route change mid-stream aborted the fetch
    // and lost the question with the partial. The end-of-turn save below
    // still runs; the chain serializes, so it reuses the id this one mints.
    // v1.325.0: a Try again keeps the reply it replaces on disk until the
    // new one lands (the question is already saved).
    const tryFork = pendingForkRef.current;
    queueSave(tryFork && tryFork.index === history.length ? [...history, ...tryFork.oldTail] : history);
    pinnedRef.current = true; // a fresh turn always scrolls into view
    setShowJump(false);
    setFailedTurn(null); // a fresh attempt — retire any prior failure
    setGrantCapNote(null); // read by now; this turn carries the grant regardless
    setChatBusy(true);
    ttsStreamStartedRef.current = false; // new turn — feedTTS will reset the counter
    // Files the user GAVE this turn join the rail up front (v1.166.0): "made
    // or was given" — an upload the user has to re-find on disk defeats the
    // rail, and the file is uploaded and in the committed bubble even if the
    // completion later fails. Persists via the same setup save made-docs use.
    if (atts.length) rememberThreadDocs(atts.map((a) => a.path));
    const body = buildChatBody(history, atts);
    // v1.324.0: this page draws the apps' cards, and carries what the user
    // attached from their apps to the message being answered — read from that
    // message, so a Regenerate reads them again (as attachments re-ground).
    body.mcp_cards = true;
    const asked = history[history.length - 1];
    const turnRes = asked?.role === "user" ? (asked.appResources ?? []) : [];
    if (turnRes.length) {
      body.resources = turnRes.map((r) => ({
        pack: r.pack,
        uri: r.uri,
        ...(r.note ? { name: r.note } : {}),
      }));
    }
    // v1.325.0: the page this question was asked about rides the same way —
    // read from the message, so a Try again sends it again.
    const turnPage = asked?.role === "user" ? asked.pageContext : undefined;
    if (turnPage?.text.trim()) body.page_context = turnPage;
    // v1.328.0: the saved chats this question pointed at with "@" — read
    // from the message too, and the same body goes to both lanes.
    const turnRefs = chatRefIds(asked?.role === "user" ? asked.chatRefs : undefined);
    if (turnRefs.length) body.thread_refs = turnRefs;
    // v1.278.0: the turn is NAMED so a steer note can reach it while it runs.
    const turnId = mintTurnId();
    body.turn_id = turnId;
    turnIdRef.current = turnId;
    setPendingSteers([]);
    setSteerBack(false);
    try {
      // --- Attempt token streaming (live deltas + tool cards + voice) ---
      try {
        const streamRes = await stream.run(
          body,
          (_delta, full) => feedTTS(full, false),
          resetTTSFeed,
        );
        turnIdRef.current = "";
        // The notes the turn READ join the conversation as the user's own
        // messages, in order, before the reply — the model saw them so, and
        // the next turn resends this history.
        const steerBubbles: ChatMessage[] = (streamRes.steered ?? []).map((t) => ({
          role: "user",
          content: t,
          steer: true,
        }));
        const {
          reply,
          tools_used,
          deniedTools,
          remembered,
          suggestion,
          configCards,
          trust,
          trustReason,
          trustNote,
          usage: turnUsage,
          route,
          provider: servedBy,
          documents: madeDocs,
          escalate,
          escalateReason,
          escalateAgent,
          workflowDraft,
          context: ctxUsage,
        } = streamRes;
        if (chatGenRef.current !== gen) return; // torn down mid-stream
        if (ctxUsage) setContextUsage(ctxUsage);
        // v1.287.0: notes the turn ACCEPTED but finished before reading go
        // back in the box — ahead of anything typed since — so Enter sends
        // them. "Steer sent" was a promise; this keeps it or says it didn't.
        const unread = streamRes.unreadSteers ?? [];
        if (unread.length) {
          const typed = composer.get().text.trim();
          composer.setText([...unread, ...(typed ? [typed] : [])].join("\n"));
          setSteerBack(true);
        }
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
        // Contract 2 (v1.170.0): the turn's tool loop ran workflow_run — the
        // chip under this reply lets the user watch the run where they stand.
        // useChatStream now decodes the done frame's `workflow_run` into the
        // typed `workflowRun` result field (coordinator integration); the raw
        // fallback stays for one release of belt-and-braces.
        const wfRun = workflowRunFrom(
          streamRes.workflowRun ??
            (streamRes as unknown as { workflow_run?: unknown }).workflow_run,
        );
        // Doors (v1.199.0): read off the stream result defensively — the
        // done-frame decode in useChatStream whitelists fields, so this cast
        // starts carrying data the moment the hook copies `doors` through.
        const doors = doorsFrom(
          (streamRes as unknown as { doors?: unknown }).doors,
        );
        // Adapted (v1.202.0) — the envelope's disclosure rides the message
        // like route/doors. MIRROR NOTE: keep in step with the POST lane's
        // receiptPost below — this exact merge line is where done-frame
        // fields have died silently before (denied_tools, doors — measured).
        const adapted = adaptedFrom(streamRes.adapted);
        // v1.323.0: the reply's own details — guarded, because a test double
        // (or an older hook) may resolve with the reply alone.
        const thinkingText = (streamRes.thinking ?? "").slice(0, THINKING_CAP);
        const turnSteps = streamRes.steps ?? [];
        // v1.327.0: what the turn read besides the conversation (folder
        // rules, @-referenced chats) — decoded again because a test double
        // or an older hook may hand back anything. MIRROR NOTE: the POST
        // lane's receiptPost below carries the same two fields, last.
        const folderRules = decodeFolderRules(streamRes.folderRules);
        const threadRefs = decodeThreadRefs(streamRes.threadRefs);
        const receipt = {
          at: new Date().toISOString(), // v1.323.0 — the settle time, both lanes
          ...(route ? { route } : {}),
          ...(adapted ? { adapted } : {}),
          ...(deniedTools?.length ? { deniedTools } : {}),
          ...(remembered?.length ? { remembered } : {}),
          // Suggestion (v1.305.0): already whitelisted by the done-frame decode.
          ...(suggestion ? { suggestion } : {}),
          // Settings cards (redesign S3/S4): whitelisted by the same decode.
          ...(configCards?.length ? { configCards } : {}),
          // Trust (v1.298.0): only a LOW posture lands on the message — a
          // "full" would be noise on every reply, and absent is today's look.
          ...(trust === "low"
            ? {
                trust,
                ...(trustReason ? { trustReason } : {}),
                ...(trustNote ? { trustNote } : {}),
              }
            : {}),
          // Usage (v1.300.0): already whitelisted by the done-frame decode.
          ...(turnUsage ? { usage: turnUsage } : {}),
          ...(madeDocs?.length ? { documents: madeDocs } : {}),
          ...(wfRun ? { workflowRun: wfRun } : {}),
          ...(doors ? { doors } : {}),
          // v1.323.0: the reply's own details (kept last: the trust pin reads
          // the head of this object).
          ...(thinkingText ? { thinking: thinkingText } : {}),
          ...(thinkingText && streamRes.thinkingMs
            ? { thinkingSeconds: Math.max(1, Math.round(streamRes.thinkingMs / 1000)) }
            : {}),
          ...(streamRes.truncated ? { truncated: true } : {}),
          ...(turnSteps.length ? { steps: turnSteps } : {}),
          ...(streamRes.timing ? { timing: streamRes.timing } : {}),
          ...(streamRes.resources?.length ? { appResources: streamRes.resources } : {}),
          // v1.327.0 (kept last, the receipt rule): folder rules + chats read.
          ...(folderRules.length ? { folderRules } : {}),
          ...(threadRefs.length ? { threadRefs } : {}),
        };
        const full: ChatMessage[] = [
          ...history,
          ...steerBubbles,
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
            ...steerBubbles,
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
        const settled = mergeContinuation(full);
        setMessages(settled);
        queueSave(settled); // the turn is complete — persist it
        askFollowups(settled, route);
        showDocPreview(madeDocs); // a generated doc appears beside the chat
        return; // streamed successfully
      } catch (e) {
        turnIdRef.current = ""; // v1.278.0: nothing to steer any more
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
      // Contract 2 (v1.170.0) — the POST lane's copy of the stream lane's
      // workflow_run handling. MIRROR NOTE: keep in step with the stream path.
      const wfRunPost = workflowRunFrom(res.workflow_run);
      // Doors (v1.199.0) — the POST lane's copy of the stream lane's doors
      // handling. MIRROR NOTE: keep in step with the stream path above.
      const doorsPost = doorsFrom(res.doors);
      // Adapted (v1.202.0) — the POST lane's copy of the stream lane's
      // adapted handling. MIRROR NOTE: keep in step with the stream path.
      const adaptedPost = adaptedFrom(res.adapted);
      // Usage (v1.300.0) — the POST lane's copy of the stream lane's decode.
      const usagePost = turnUsageFrom(res.usage);
      // Suggestion (v1.305.0) — the POST lane's copy of the stream lane's
      // whitelist decode. MIRROR NOTE: keep in step with the stream path.
      const suggestionPost = decodeSuggestion(res.suggestion);
      // Settings cards (redesign S3/S4) — MIRROR NOTE: keep in step.
      const configCardsPost = decodeConfigCards(res.config_cards);
      // v1.323.0: the POST lane's copy of the stream lane's reply details.
      const thinkingPost =
        typeof (res as { thinking?: unknown }).thinking === "string"
          ? ((res as { thinking?: string }).thinking ?? "").slice(0, THINKING_CAP)
          : "";
      // v1.327.0: the POST lane's copy of the stream lane's folder rules and
      // chats read. MIRROR NOTE: keep in step with the stream path above.
      const folderRulesPost = decodeFolderRules((res as { folder_rules?: unknown }).folder_rules);
      const threadRefsPost = decodeThreadRefs((res as { thread_refs?: unknown }).thread_refs);
      const receiptPost = {
        at: new Date().toISOString(), // v1.323.0 — the settle time, both lanes
        ...(thinkingPost ? { thinking: thinkingPost } : {}),
        ...((res as { truncated?: unknown }).truncated === true ? { truncated: true } : {}),
        ...(res.route ? { route: res.route } : {}),
        ...(adaptedPost ? { adapted: adaptedPost } : {}),
        ...(deniedPost.length ? { deniedTools: deniedPost } : {}),
        ...(res.remembered?.length ? { remembered: res.remembered } : {}),
        ...(suggestionPost ? { suggestion: suggestionPost } : {}),
        ...(configCardsPost.length ? { configCards: configCardsPost } : {}),
        // Trust (v1.298.0) — the POST lane's copy of the stream lane's rule.
        ...(res.trust === "low"
            ? {
                trust: res.trust,
                ...(typeof res.trust_reason === "string" && res.trust_reason
                  ? { trustReason: res.trust_reason }
                  : {}),
                ...(typeof res.trust_note === "string" && res.trust_note
                  ? { trustNote: res.trust_note }
                  : {}),
              }
            : {}),
        ...(usagePost ? { usage: usagePost } : {}),
        ...(res.documents?.length ? { documents: res.documents } : {}),
        ...(wfRunPost ? { workflowRun: wfRunPost } : {}),
        ...(doorsPost ? { doors: doorsPost } : {}),
        // v1.324.0: the apps' resources this turn read (whitelisted).
        ...(decodeResourceReceipts((res as { resources?: unknown }).resources).length
          ? { appResources: decodeResourceReceipts((res as { resources?: unknown }).resources) }
          : {}),
        // v1.327.0 — the POST lane's copy of the stream lane's folder rules
        // and chats read (whitelisted, kept last). MIRROR NOTE: keep in step.
        ...(folderRulesPost.length ? { folderRules: folderRulesPost } : {}),
        ...(threadRefsPost.length ? { threadRefs: threadRefsPost } : {}),
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
            // Earlier rounds of THIS turn may have run tools — their
            // provenance must survive the draft exit like any other turn.
            // MIRROR NOTE: the stream lane's draft exit spreads its full
            // `...receipt`; this spread must stay in step, or a turn that
            // earned a door/route/document in round 0 and crystallized a
            // draft in round 1 silently loses them on the fallback lane
            // (v1.199.0 — this exact drop shipped for route/documents/doors).
            // receiptPost already carries workflowRun, so no separate spread.
            ...(toolsUsed.length ? { toolsUsed } : {}),
            ...(viaPost ? { viaProvider: viaPost } : {}),
            ...receiptPost,
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
      setMessages(mergeContinuation(full));
      // Nothing streamed on this path (endpoint absent), so this is the first and
      // only speak — no risk of re-voicing sentences speakMore already spoke.
      if (!ttsStreamStartedRef.current) tts.speak(reply);
      queueSave(mergeContinuation(full)); // the turn is complete — persist it
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
      turnChoiceRef.current = null;
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
      if (!composer.get().text.trim()) composer.setText(message);
    } finally {
      sendingRef.current = false;
      if (chatGenRef.current === gen) {
        setChatBusy(false);
        inputRef.current?.focus();
      }
    }
  }

  /** CHAT MODE: append the user's message and run one completion. */
  async function sendChat(
    message: string,
    opts: { queued?: QueuedMessage; editBefore?: ChatMessage[] | null } = {},
  ) {
    // v1.323.0: the draft's words are now a message — never restore them.
    if (!opts.queued && saveTargetRef.current.id) clearDraft(saveTargetRef.current.id);
    // v1.325.0: a QUEUED message carries what was in the box when it was
    // queued; the box now holds whatever the user has typed since.
    const q = opts.queued;
    const atts = q ? q.files : attachments;
    if (!q) setAttachments([]); // chips are consumed by this message
    // v1.324.0: so are the apps' resources — read by the daemon for THIS turn.
    const appRes = q ? q.appRes : appResources;
    if (!q) setAppResources([]);
    const page = q ? q.page : pageCtx;
    if (!q) setPageCtx(null);
    // v1.328.0: and so are the chats picked with "@" (a queued message keeps
    // its own). They ride the user message so a Try again sends them again.
    const refs = q ? q.refs : chatRefsRef.current;
    if (!q) {
      setChatRefs([]);
      setChatRefNote("");
    }
    const userMsg: ChatMessage = {
      role: "user",
      content: message,
      at: new Date().toISOString(),
      ...(page ? { pageContext: page } : {}),
      // Saved as id + title only: the age shown in the menu and on the chip is
      // for telling chats apart at pick time, not part of the conversation.
      ...(refs.length ? { chatRefs: refs.map((r) => ({ id: r.id, title: r.title })) } : {}),
      ...(appRes.length
        ? { appResources: appRes.map((r) => ({ pack: r.pack, uri: r.uri, ok: true, note: r.title || r.name || "" })) }
        : {}),
      ...(atts.length
        ? {
            attachmentNames: atts.map((a) => a.name),
            attachmentPaths: atts.map((a) => a.path),
          }
        : {}),
    };
    // v1.325.0: an EDITED message keeps what it replaced as another version
    // (‹ 1/2 ›) — only when the thread is still exactly the cut the edit made.
    const before = opts.editBefore;
    const history =
      before && before.length > messages.length && messages.every((m, k) => m === before[k])
        ? forkTail(before, messages.length, [userMsg]).messages
        : [...messages, userMsg];
    await completeChat(history, atts);
  }

  // v1.325.0: FILE A TRY AGAIN once its turn is over. The new reply (or the
  // partial a Stop kept) becomes the live version and the one it replaced is
  // kept beside it; a try that failed with nothing to show puts the old reply
  // back. A turn that handed itself to an agent keeps today's behaviour.
  useEffect(() => {
    const f = pendingForkRef.current;
    if (!f || chatBusy) return;
    pendingForkRef.current = null;
    if (convGenRef.current !== f.conv) return;
    const cur = messages;
    const tail = cur.slice(f.index);
    let next: ChatMessage[] | null = null;
    const lastOfTail = tail[tail.length - 1];
    if (tail.length > 0 && lastOfTail.role === "assistant" && stoppedRef.current && !lastOfTail.interrupted) {
      // Stopped before the first word ("Stopped."): nothing new to keep.
      next = [...cur.slice(0, f.index), ...f.oldTail];
    } else if (tail.length > 0 && lastOfTail.role === "assistant") {
      next = forkTail([...cur.slice(0, f.index), ...f.oldTail], f.index, tail).messages;
    } else if (tail.length === 0 && failedTurn) {
      next = [...cur, ...f.oldTail];
      setFailedTurn(null); // the earlier reply is back; Try again is the way to retry
    }
    if (!next) return;
    messagesRef.current = next;
    setMessages(next);
    queueSave(next);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chatBusy, messages, failedTurn]);

  // v1.325.0: SEND THE NEXT QUEUED MESSAGE when a reply finishes cleanly — not
  // after a Stop (an interrupted reply), a failure or an offline daemon: then
  // the queue waits for a press ("Send now").
  const wasBusyRef = useRef(false);
  useEffect(() => {
    const was = wasBusyRef.current;
    wasBusyRef.current = busy;
    if (!was || busy) return;
    const next = queuedRef.current[0];
    if (!next) return;
    const last = messages[messages.length - 1];
    if (stoppedRef.current || failedTurn || error || offline || !last || last.role !== "assistant" || last.interrupted)
      return;
    const rest = queuedRef.current.slice(1);
    queuedRef.current = rest;
    setQueued(rest);
    sendQueued(next);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [busy]);

  /**
   * Drop the LAST assistant reply and re-run the completion over the history
   * ending at the preceding user message (chat mode only). The re-run saves
   * through the same single completeChat site, overwriting the thread with the
   * regenerated reply.
   */
  function regenerate(choice?: string) {
    if (busy) return;
    const msgs = messages;
    const last = msgs[msgs.length - 1];
    if (!last || last.role !== "assistant") return;
    const history = msgs.slice(0, -1);
    const lastUser = history[history.length - 1];
    if (history.length === 0 || lastUser.role !== "user") return;
    setError(null);
    setOffline(false);
    // v1.325.0: the reply being replaced is KEPT as another version — the
    // effect below files it once the new one lands (or puts it back when the
    // new one fails with nothing to show).
    pendingForkRef.current = { conv: convGenRef.current, index: history.length, oldTail: [last] };
    // Re-ground on the SAME attachments the turn carried — otherwise the re-run
    // answers blind while the user bubble still shows the file chip.
    // v1.325.0: `choice` = "Try again with…" another model, for this turn only.
    void completeChat(history, attachmentsOf(lastUser), choice ? { choice } : {});
  }

  /** v1.325.0: a selection quoted from a reply joins the box (never sent). */
  function quoteIntoBox(text: string) {
    composer.setText(insertQuote(composer.get().text, quoteBlock(text)));
    inputRef.current?.focus();
  }

  /** v1.325.0: the map's jump — bring message `index` into view. */
  function jumpToMessage(index: number) {
    setMapOpen(false);
    pinnedRef.current = false; // reading back — don't snap to the bottom
    const anchor = document.querySelector(`[data-msg-index="${index}"]`);
    const el = anchor?.firstElementChild ?? null;
    el?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  /** v1.325.0: switch message `index` to another version of the conversation. */
  function switchVersion(index: number, to: number) {
    if (busy) return;
    const next = switchBranch(messagesRef.current, index, to);
    if (next === messagesRef.current) return;
    setFollowups(null); // they answered the version being put away
    messagesRef.current = next;
    setMessages(next);
    queueSave(next);
  }

  /** v1.325.0: Ctrl+Enter mid-turn — hold the box (words, files, the app and
   *  page chips) and send it after this reply finishes cleanly. */
  function queueFollowup(text: string) {
    const words = text.trim();
    if (!words && attachmentsRef.current.length === 0) return;
    if (uploadingRef.current) {
      setError("Wait for the files to finish uploading, then queue the message.");
      return;
    }
    if (queuedRef.current.length >= MAX_QUEUED) {
      setError(`Up to ${MAX_QUEUED} messages can wait for this reply — send or remove one first.`);
      return;
    }
    const item: QueuedMessage = {
      id: mintTurnId(),
      text: words,
      files: attachmentsRef.current,
      appRes: appResources,
      page: pageCtx,
      refs: chatRefsRef.current,
    };
    const next = [...queuedRef.current, item];
    queuedRef.current = next;
    setQueued(next);
    setAttachments([]);
    setAppResources([]);
    setPageCtx(null);
    setChatRefs([]);
    setChatRefNote("");
    composer.reset();
  }

  /** v1.325.0: take a queued message out — back into the box ("Edit") or gone. */
  function unqueue(id: string, toBox: boolean) {
    const item = queuedRef.current.find((x) => x.id === id);
    if (!item) return;
    const next = queuedRef.current.filter((x) => x.id !== id);
    queuedRef.current = next;
    setQueued(next);
    if (toBox) putBackInBox([item]);
  }

  /** Queued messages that cannot go out on their own return to the box, ahead
   *  of anything typed since — nothing the user wrote is dropped silently. */
  function putBackInBox(items: QueuedMessage[]) {
    if (!items.length) return;
    const cur = composer.get().text.trim();
    const words = items.map((x) => x.text).filter(Boolean);
    composer.setText([...words, ...(cur ? [cur] : [])].join("\n\n"));
    const files = items.flatMap((x) => x.files);
    if (files.length) setAttachments((prev) => [...files, ...prev]);
    const res = items.flatMap((x) => x.appRes);
    if (res.length) setAppResources((prev) => [...res, ...prev]);
    const page = items.find((x) => x.page)?.page;
    if (page) setPageCtx(page);
    // v1.328.0: their chats come back as chips too, ahead of any picked
    // since (no duplicates, at most CHAT_REFS_MAX).
    const back = items.flatMap((x) => x.refs ?? []);
    if (back.length) {
      let refs: ChatRefPick[] = [];
      for (const r of [...back, ...chatRefsRef.current]) refs = addChatRef(refs, r).next;
      setChatRefs(refs);
    }
  }

  /** v1.325.0: send one queued message — the chat lane only. A messaging
   *  thread, an agent conversation, an @-mention or a pasted key go back to
   *  the box instead: those doors read the box, and the key guard asks first. */
  function sendQueued(item: QueuedMessage) {
    const text = item.text;
    if (
      commMetaRef.current ||
      addresseeRef.current.length > 0 ||
      liveMentionsIn(text).length > 0 ||
      (text && looksLikeSecret(text)) ||
      (!text && item.files.length === 0)
    ) {
      putBackInBox([item]);
      inputRef.current?.focus();
      return;
    }
    if (busy || sendingRef.current) {
      const next = [item, ...queuedRef.current];
      queuedRef.current = next;
      setQueued(next);
      return;
    }
    sendingRef.current = true;
    pinnedRef.current = true;
    setShowJump(false);
    setError(null);
    setOffline(false);
    void sendChat(text, { queued: item });
  }

  /** v1.323.0: CONTINUE the newest reply, cut off by the model's output
   *  limit or by Stop — a hidden "carry on" turn whose answer is merged into
   *  it (lib/continueReply). Plain chat replies only. */
  function continueReply() {
    if (busy) return;
    const msgs = messages;
    const last = msgs[msgs.length - 1];
    if (!last || last.role !== "assistant" || !(last.truncated || last.interrupted)) return;
    setError(null);
    setOffline(false);
    const ask: ChatMessage = {
      role: "user",
      content: CONTINUE_PROMPT,
      continuation: true,
      at: new Date().toISOString(),
    };
    void completeChat([...msgs, ask], []);
  }

  /** The attachments a saved user bubble carried, as a re-run needs them. */
  function attachmentsOf(m: ChatMessage): UploadedFile[] {
    return (m.attachmentPaths ?? []).map((path, i) => ({
      path,
      name: m.attachmentNames?.[i] ?? path.split(/[\\/]/).pop() ?? path,
      bytes: 0,
    }));
  }

  /** v1.278.0: STEER the running turn — a note it reads at its next step.
   *  Posted to the turn BY NAME (the same registry Stop uses), so the daemon
   *  answers 404 when nothing is running to read it, and the words stay in
   *  the box for a plain send. */
  async function steerTurn(text: string): Promise<void> {
    const note = text.trim();
    if (!note) return;
    const id = turnIdRef.current;
    if (!id) {
      setError("Nothing is running to steer — send it as a message.");
      return;
    }
    try {
      await post(`/chat/turns/${encodeURIComponent(id)}/steer`, { text: note });
    } catch (e) {
      const status = e instanceof ApiError ? e.status : 0;
      setError(
        status === 404
          ? "That turn has already finished — send it as a new message."
          : `Couldn't send the steer note: ${e instanceof Error ? e.message : String(e)}`,
      );
      return;
    }
    composer.reset();
    setPendingSteers((prev) => [...prev, note]);
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
      if (composer.get().text.trim() === lastUser.content.trim()) composer.reset();
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
    // A NAMED hand-off to a DIFFERENT agent than this conversation's session
    // runs on OPENS A NEW SESSION (v1.284.0): continuing builder's session
    // while the bubble and the "Talking to" strip say taxpro would be three
    // lies in one turn. The same agent (or no name at all) continues as
    // before, keeping its workspace and the files it already made.
    const wanted = opts.agentType || "";
    const wouldRun = wanted.startsWith("custom:")
      ? wanted
      : wanted && !wanted.includes(":")
        ? wanted
        : "builder";
    const reopen =
      Boolean(sessionId) && Boolean(wanted) && wouldRun !== (sessionTargetRef.current || "builder");
    const opensSession = !sessionId || reopen;
    const picked = (opensSession && opts.agentType) || "";
    // Slug stripped from the CANONICAL name (roster.py's NAME CONTRACT: the
    // remainder after ":" is the registry key, original casing preserved).
    const customSlug = picked.startsWith("custom:")
      ? picked.slice("custom:".length)
      : "";
    const builtinPick =
      picked && !picked.includes(":") && picked !== "builder" ? picked : "";
    // The hand-off bubble names ONLY what will truly run.
    const target = customSlug ? picked : builtinPick || null;
    const withBubble: ChatMessage[] = [
      ...messagesRef.current,
      esc
        ? // The bubble is already there — mark WHY the turn grew instead, so the
          // hand-off is visible rather than an unexplained pause. When a
          // specialist was chosen, name it (v1.139.0).
          {
            role: "assistant" as const,
            content: "",
            escalated: esc.reason,
            ...(target ? { escalatedTo: agentPhrase(target) } : {}),
          }
        : {
            role: "user" as const,
            content: message,
            ...(atts.length ? { attachmentNames: atts.map((a) => a.name) } : {}),
          },
    ];
    setMessages(withBubble);
    // The ref is written NOW (v1.226.0): the saves below and a finalize that
    // beats React's re-render must both see the bubble.
    messagesRef.current = withBubble;
    // DURABLE BEFORE THE DAEMON IS ASKED (v1.226.0): an agent turn runs for
    // minutes and the page used to save nothing until finalize — leave for
    // the Sessions page and the question was gone with the answer. The gen
    // and the box are captured HERE: everything after the POST's await must
    // prove it still belongs to this conversation (New chat / a thread switch
    // is clickable while the POST is airborne — chatBusy is already false).
    const gen = chatGenRef.current;
    const box = saveTargetRef.current;
    queueSave(withBubble, box);
    // The POST goes out only once that PUT has settled (a local round trip;
    // the chain never rejects) — "on disk before the daemon is asked" is then
    // a fact, not a microtask race the session could win.
    await saveChainRef.current;
    // Files given to an AGENT turn land on the rail too (v1.166.0) — same
    // "made or was given" rule as chat mode. Escalations skip it: their
    // attachments were already merged by the chat lane that escalated.
    if (!esc && atts.length) rememberThreadDocs(atts.map((a) => a.path));
    // Mark where "this turn" begins in the event stream BEFORE kicking off work.
    sinceRef.current = eventsRef.current[0]?.id ?? null;
    try {
      let session: SessionView;
      // The armed set as of NOW, via the ref: this function belongs to the
      // render where the turn STARTED, so a tool granted mid-turn on the
      // approval card (setSelectedTools → a later render) is invisible to
      // the `selectedTools` binding here — the escalation would then have to
      // re-ask for the grant the user made seconds earlier. Read ABOVE the
      // branch (v1.232.0, A6): the continue sends it too.
      const armedNow = selectedToolsRef.current;
      // v1.312.0 (W4-2): "Allow for this conversation" grants made at the
      // arming cap live only in grantedToolsRef — they ride the escalation
      // too, or the run re-asks for what the user already allowed. Same
      // consent, same effect: a session grant only lifts an ask, it never
      // gives the run a tool its own tool set lacks.
      const extraGrants = grantedToolsRef.current.filter((t) => !armedNow.includes(t));
      // THE POSTURE RIDES THE ESCALATION (v1.232.0, A7) — the same idiom as
      // the chat body: only the non-default is sent. The daemon never
      // inherits "yolo" (it lands as approve-for-me), so a yolo chat's
      // escalated run still asks once per ask-tier tool.
      const posture =
        approvalMode !== "approve_for_me" ? { approval_mode: approvalMode } : {};
      if (sessionId && !reopen) {
        // Continue the same chat — runs in the background (wait:false).
        session = await post<SessionView>(`/sessions/${sessionId}/continue`, {
          message: task,
          wait: false,
          // GRANTS RIDE THE CONTINUE (v1.232.0, A6): a tool granted on a card
          // AFTER the opener reached no later turn — the daemon unions this
          // with the session's stored grant, so a continue can widen but
          // never narrow what the earlier run was allowed.
          ...(armedNow.length || extraGrants.length
            ? { allow_tools: armedNow.slice(0, MAX_TOOLS).concat(extraGrants) }
            : {}),
          ...posture,
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
              {
                task: openingTask,
                wait: false,
                // GRANTS RIDE THE ESCALATION (v1.187.0) — see the POST
                // /sessions branch below for why this is the same consent.
                ...(armedNow.length || extraGrants.length
                  ? { allow_tools: armedNow.slice(0, MAX_TOOLS).concat(extraGrants) }
                  : {}),
                ...posture,
                // THE FOLDER RIDES TOO (v1.189.0) — see below.
                ...(workspaceDir ? { workspace_root: workspaceDir } : {}),
                // Presence asserted — see the POST /sessions branch below.
                origin: "chat",
              },
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
              // GRANTS RIDE THE ESCALATION (v1.187.0). The composer's armed
              // set is explicit user consent — the "+"-menu, or an approval
              // card's "Allow for this conversation" — and the chat lane
              // already honours it as an allow override. An escalated run is
              // the SAME conversation continuing in the agent lane; without
              // this, granting shell in chat and then asking for anything
              // multi-step got it silently re-denied by the headless
              // resolver, and the user had no idea their grant had lapsed.
              // A base `deny` still holds — session grants never lift it.
              // Read from the ref (`armedNow`) so a grant made on THIS turn's
              // approval card rides too, not just ones armed before it began.
              ...(armedNow.length || extraGrants.length
                ? { allow_tools: armedNow.slice(0, MAX_TOOLS).concat(extraGrants) }
                : {}),
              ...posture,
              // THE FOLDER RIDES THE ESCALATION (v1.189.0). Chat's own tools
              // operate in this folder; the session the turn escalates into
              // used to lose it and work in a scratch dir instead — measured:
              // rename_file refusing all 27 tax documents as outside a
              // workspace the user never chose, and the agent filing a
              // capability request for a tool it already had.
              ...(workspaceDir ? { workspace_root: workspaceDir } : {}),
              // PRESENCE IS ASSERTED, never assumed (v1.189.0): "chat" is what
              // lets the run PAUSE on an ask-tier tool and render its card
              // here — an unattributed session keeps the instant denial,
              // because "somebody is watching" is a fact only the watching
              // surface can state.
              origin: "chat",
            });
      }
      // ALWAYS chain forward to the returned session id: `continue` spawns a NEW
      // session (recapping the old one), so the next turn must continue from it —
      // sticking with the first id would silently drop the intermediate turns.
      // THE HAND-OFF IS ON DISK (v1.226.0): the last bubble names the session
      // this turn now waits on, so openThread can resume the wait after a
      // reload or route change (see ChatMessage.awaitingSession for why it is
      // a message field, not `setup`). finalize/Stop strip it with the reply.
      // Built from withBubble, never from messagesRef — the ref is whatever
      // conversation is open NOW.
      const marked: ChatMessage[] = [
        ...withBubble.slice(0, -1),
        { ...withBubble[withBubble.length - 1], awaitingSession: session.id },
      ];
      if (chatGenRef.current !== gen) {
        // TORN DOWN while the POST was airborne (New chat / thread switch):
        // nothing here may touch the open conversation. The session still
        // runs, so the mark goes into the ORIGINAL box — reopening that
        // thread resumes the wait and lands the reply where it belongs.
        queueSave(marked, box);
        return;
      }
      setSessionId(session.id);
      // Remember who it runs on ONLY when this turn opened it — a continue
      // carries no name and must not relabel the running session.
      if (opensSession) sessionTargetRef.current = customSlug ? picked : builtinPick || "builder";
      setMessages(marked);
      messagesRef.current = marked;
      queueSave(marked, box);
      // Hand off to the event watcher + polling fallback to surface the reply.
      awaitingIdRef.current = session.id;
      setAwaitingId(session.id);
    } catch (e) {
      // Torn down mid-POST (v1.226.0): the bubble was saved into its own box
      // before the POST; the open conversation is somebody else's now.
      if (chatGenRef.current !== gen) return;
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
    // v1.275.0: a message can be a file alone ("here" + a PDF), and a send
    // pressed mid-upload is QUEUED — it fires with the box's text the moment
    // the uploads settle — instead of going out without the files.
    const hasFiles = attachmentsRef.current.length > 0;
    if (uploadingRef.current) {
      queuedSendRef.current = true;
      return;
    }
    // `busy` is React state (lags a frame); `sendingRef` flips synchronously so
    // two Enter keydowns in the same tick can't both start a turn.
    if ((!message && !hasFiles) || busy || sendingRef.current) return;
    // A KEY PASTED INTO THE BOX IS HELD (redesign S4, AUDIT Q9): sent, it
    // would sit in the transcript and every later turn's context. The notice
    // offers the vault instead; "Send anyway" passes this guard once.
    if (message && looksLikeSecret(message) && !secretSendOkRef.current) {
      setHeldSecret(message);
      return;
    }
    secretSendOkRef.current = false;
    setHeldSecret(null);
    // v1.325.0: an edit being sent keeps what it replaced as another version.
    const editBefore = editUndo?.before ?? null;
    setEditUndo(null);
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
    composer.reset();
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
    const live = liveMentionsIn(message);
    if (live.length > 0) {
      void sendPanel(message, live);
      return;
    }
    // STILL TALKING TO AN AGENT (v1.284.0): no "@" in the text, but the
    // conversation is with builder — the follow-up goes to builder, the words
    // verbatim and the addressees on the side. "Back to Jarvis" (the strip
    // above the composer) is the way out; another @ in the text switches.
    if (addresseeRef.current.length > 0) {
      // A file alone is a message (v1.275.0) — here it is work for the agent,
      // and the route wants words: name the files.
      const text =
        message ||
        `Attached: ${attachmentsRef.current.map((a) => a.name).join(", ")}`;
      void sendPanel(text, addresseeRef.current.map(agentDisplayName));
      return;
    }
    // One entry point (v1.108.0). Every message starts as fast chat; the turn
    // escalates itself when it needs the full agent (see completeChat), so the
    // user never routes their own request.
    void sendChat(message, { editBefore });
  }

  /**
   * Send an @-mentioned message to the agent panel (v1.150.0).
   *
   * The panel is an ordinary agent thread bound to this chat thread, so the
   * inter-agent conversation shows up on the Agents page for free — and turn 3
   * can mention someone new who then sees what was already said.
   *
   * v1.284.0 — THE AGENT REMEMBERS, STAYS ADDRESSED, AND CAN ACT. The body
   * carries the chat so far (`history`, the speaker's transcript — budgeted by
   * the daemon), the room an earlier round answered with (`panel_thread_id`,
   * adopted when this chat has no thread id yet — a new chat's first two
   * rounds used to land in two rooms), and the addressees (`mentions`, so a
   * follow-up without "@" keeps talking to the same agent with its words
   * verbatim). A `mode: "session"` verdict means the ask is WORK a panel seat
   * cannot do: it opens the same tooled session a chat escalation opens, and
   * the reply comes back with the files. Attachments make it work by
   * definition (`hands: true`) — a panel seat cannot read a file.
   */
  async function sendPanel(message: string, mentions: string[]) {
    setChatBusy(true);
    const before = messagesRef.current;
    const atts = attachmentsRef.current;
    const history: ChatMessage[] = [
      ...before,
      {
        role: "user",
        content: message,
        ...(atts.length ? { attachmentNames: atts.map((a) => a.name) } : {}),
      },
    ];
    setMessages(history);
    const priorRoom = [...before].reverse().find((m) => m.panelThreadId)?.panelThreadId;
    // The files ride the wire by PATH (the kanban/agent precedent), so a round
    // that cannot open them at least knows they exist; a session gets them
    // through sendAgent's own attach lines instead (never both).
    const wire = atts.length
      ? message + atts.map((a) => `\n\nAttached file: ${a.path}`).join("")
      : message;
    try {
      const res = await post<PanelResponse>("/chat/panel", {
        message: wire,
        mentions,
        ...(threadId ? { chat_thread_id: threadId } : {}),
        ...(priorRoom ? { panel_thread_id: priorRoom } : {}),
        history: panelHistoryOf(before),
        ...(atts.length ? { hands: true } : {}),
      });
      if (atts.length) setAttachments([]); // consumed — the daemon has them now
      if (res.mode === "session" && res.target) {
        // WORK, NOT A QUESTION: the same lane a chat escalation takes. The
        // user's bubble is already on screen (hence escalatedFrom), the reply
        // is attributed to the agent when it lands (awaitingWhoRef), and the
        // conversation stays with that agent.
        const who = res.who || `builtin:${res.target}`;
        setAddressee([who]);
        awaitingWhoRef.current = who;
        setChatBusy(false);
        await sendAgent(stripMentions(message), {
          escalatedFrom: {
            atts,
            reason:
              res.reason ||
              `${agentDisplayName(who)} is doing this in a real session, with tools`,
          },
          agentType: res.target,
        });
        return;
      }
      // The user's own turn is already in `history`; keep only the agents'.
      const replies = (res.entries ?? []).filter((e) => e.who && e.who !== "user");
      const dropped = res.context?.chat_dropped ?? 0;
      const note =
        dropped > 0
          ? `${dropped} earlier message${dropped === 1 ? "" : "s"} did not fit the agent's context window`
          : "";
      const full: ChatMessage[] = [
        ...history,
        ...replies.map((e, idx) => ({
          role: "assistant" as const,
          content: e.content || "(no reply)",
          panelWho: e.who,
          panelThreadId: res.thread_id,
          ...(e.error ? { panelError: true } : {}),
          ...(idx === 0 && note ? { panelNote: note } : {}),
        })),
      ];
      setMessages(full);
      // The conversation is WITH these agents now — the next plain message
      // goes to them too. The daemon's `spoke` is the truth of who answered.
      const spoke = (res.spoke ?? []).filter(Boolean);
      setAddressee(spoke.length ? spoke : addresseeOf(full));
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
      composer.setText(message); // never lose the typed message
      if (atts.length) setAttachments(atts); // ...nor the files (never cleared before the POST landed)
      setMessages(before);
    } finally {
      setChatBusy(false);
      sendingRef.current = false;
    }
  }

  /**
   * "Have builder do this" (v1.284.0). The panel seat answered in words; a
   * REAL session of that agent now carries it out — the ask it answered as
   * the task, the conversation (its own proposal included) as the recap
   * `sendAgent` prepends, the files back in chat through the run result.
   * Explicitly the user's press, so the daemon is not asked to judge it.
   */
  async function handOffPanelReply(index: number) {
    if (busy || sendingRef.current) return;
    const reply = messagesRef.current[index];
    const who = reply?.panelWho;
    const target = sessionTargetOf(who);
    if (!who || !target) return;
    let ask = "";
    for (let j = index - 1; j >= 0; j--) {
      const m = messagesRef.current[j];
      if (m.role === "user" && !m.steer) {
        ask = m.content;
        break;
      }
    }
    const task =
      `${stripMentions(ask) || "Carry out what you proposed."}\n\n` +
      "You already answered this on the agent panel (see the conversation " +
      "above) — now DO it with your tools, and report the files you made.";
    sendingRef.current = true;
    setAddressee([who]);
    awaitingWhoRef.current = who;
    await sendAgent(task, {
      escalatedFrom: { atts: [], reason: `you asked ${agentDisplayName(who)} to do it` },
      agentType: target,
    });
  }

  // Stop the in-flight turn and keep whatever streamed so far as the answer.
  // Best-effort — even if the server-side cancel fails we stop waiting locally.
  function stop() {
    stoppedRef.current = true; // v1.325.0: see the queue and Try again effects
    // CHAT: abort the stream. Bump the generation FIRST so the aborted
    // stream.run()'s throw lands in a torn-down completeChat (no POST fallback).
    if (chatBusy && stream.streaming) {
      chatGenRef.current += 1;
      // v1.312.0 (W4-3): tell the daemon too, by the turn's name. The daemon
      // now prepares a turn INSIDE the open stream (recall, reading files, a
      // summary) and checks Stop between steps — dropping the fetch alone
      // left that work running for nobody. Best-effort and silent: a 404 (the
      // turn already ended) or a dead daemon changes nothing the user sees,
      // because the local stop below has already happened either way.
      const stopId = turnIdRef.current;
      if (stopId)
        post(`/chat/turns/${encodeURIComponent(stopId)}/stop`).catch(() => {});
      stream.abort();
      tts.cancel(); // stop reading a reply the user just cut off
      // v1.250.0 (S-03): the live text lives in the stream's store now, and
      // the store is always current — including between frames, which is
      // exactly when Stop lands.
      const partial = (stream.textStore?.get() ?? stream.text).trim();
      // v1.322.0 (borrowed idea: assistant-ui's cancel-restores-the-draft):
      // stopped before the first word — usually "oops, wrong question" — so
      // the question goes back into the box, ready to fix (only into an
      // empty box; the "Stopped." line stays as the record).
      if (!partial) restoreComposerDraft(messagesRef.current);
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
    // The STORE is the truth. With `textInState: false` the `text` field stays
    // empty, so reading it here would save "Stopped." over whatever the agent
    // had already written — losing the user's partial answer to a speed change.
    const partial = (runStream.textStore?.get() ?? runStream.text).trim();
    const full: ChatMessage[] = [
      ...stripAwaiting(messagesRef.current), // the wait is over (v1.226.0)
      {
        role: "assistant",
        content: partial || "Stopped.",
        ...(partial ? { interrupted: true } : {}),
      },
    ];
    setMessages(full);
    queueSave(full); // the (aborted) turn still completed a visible exchange
    awaitingIdRef.current = null;
    awaitingWhoRef.current = null; // a stopped hand-off names nobody later
    setAwaitingId(null); // also tears down the event watcher + polling interval
  }

  /**
   * v1.322.0: leaving this conversation (New chat, or opening another one).
   * An upload still running, a send queued behind it, the dictation mic and
   * an Edit's Undo all belong to the conversation being left, not the next.
   * Both doors call this one function so neither can forget a step.
   */
  function leaveConversation() {
    // v1.327.0 (W2-1): the chat being left was seen up to now — its own
    // messages moved its `updated_at` while it was open, and that must not
    // read as "new since you last looked" once it is back in the list.
    noteViewed(saveTargetRef.current.id, saveTargetRef.current.updatedAt);
    // v1.325.0: messages waiting for a reply belong to THIS conversation —
    // their words join the box first, so the draft below keeps them.
    if (queuedRef.current.length) {
      const words = queuedRef.current.map((x) => x.text).filter(Boolean);
      const cur = composer.get().text.trim();
      if (words.length) composer.setText([...words, ...(cur ? [cur] : [])].join("\n\n"));
      queuedRef.current = [];
      setQueued([]);
    }
    pendingForkRef.current = null;
    setPageCtx(null);
    // v1.323.0: what is half-typed for a SAVED conversation is kept for it
    // (an empty box removes the old draft); an unsaved chat has nowhere to
    // come back to, so New chat still starts empty.
    saveDraftNow();
    setFollowups(null);
    convGenRef.current += 1;
    // The next conversation opens at its bottom at once (see openingRef).
    openingRef.current = true;
    queuedSendRef.current = false;
    if (dictation.listening) dictation.stop();
    setEditUndo(null);
    // v1.324.0: an app resource picked for one conversation is not sent in
    // another.
    setAppResources([]);
    // v1.328.0: nor a chat picked with "@" (it may be the one opened next).
    setChatRefs([]);
    setChatRefNote("");
    setPromptForm(null);
  }

  /** v1.323.0: keep the box's text as the open saved conversation's draft. */
  function saveDraftNow() {
    const id = saveTargetRef.current.id;
    if (id) writeDraft(id, composer.get().text);
  }

  // Leaving the chat page (another module, a reload) keeps the draft too —
  // and (v1.327.0) stamps the open chat as seen, like leaving it in place.
  useEffect(() => {
    const seen = () => markViewed(saveTargetRef.current.id ?? "", saveTargetRef.current.updatedAt);
    const onHide = () => {
      saveDraftNow();
      seen();
    };
    window.addEventListener("pagehide", onHide);
    return () => {
      window.removeEventListener("pagehide", onHide);
      saveDraftNow();
      seen();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reads refs only
  }, []);

  // Redesign S7: the sidebar's "New chat" while this page is open.
  const newChatRef = useRef<() => void>(() => {});
  useEffect(() => {
    const onNew = () => newChatRef.current();
    window.addEventListener(NEW_CHAT_EVENT, onNew);
    return () => window.removeEventListener(NEW_CHAT_EVENT, onNew);
  }, []);

  function newChat() {
    leaveConversation();
    forgetOpenThread(); // v1.311.0: the next visit starts fresh too
    chatGenRef.current += 1; // orphan any in-flight /chat reply
    stream.abort(); // tear down a live streaming turn (its throw won't fall back)
    tts.cancel(); // stop reading the old thread's reply
    setMessages([]);
    setSessionId(null);
    sessionTargetRef.current = "";
    awaitingIdRef.current = null;
    setAwaitingId(null); // also tears down any polling interval
    setChatBusy(false);
    setFailedTurn(null);
    setSaveFailure(null); // its Retry belonged to the conversation being left
    setAttachments([]);
    // A fresh conversation has no folder of its own until it is handed a file.
    convFolderRef.current = null;
    setWorkfolder(null);
    setWorkfolderNote("");
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
    grantedToolsRef.current = []; // grants are per-conversation (v1.312.0)
    setGrantCapNote(null);
    setSelectedConnectors([]); // connector toggles are per-conversation
    // New chat returns to the user's DEFAULT posture (the localStorage one),
    // not the previous thread's — a YOLO grant is per-conversation consent and
    // must never leak into a conversation that never made it. The thread-open
    // reset does exactly this; without the same line here, opening a saved
    // YOLO thread and then clicking New chat left the fresh pane silently
    // auto-approving the whole ask tier (and a restored always_ask left it
    // over-carding). Deleting the open thread routes through here too.
    try {
      setApprovalMode(asApprovalMode(localStorage.getItem(APPROVAL_MODE_KEY)));
    } catch {
      setApprovalMode("approve_for_me");
    }
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
    composer.reset();
    setError(null);
    setOffline(false);
    setThreadId(null);
    setAddressee([]); // a fresh conversation is with Iron Jarvis
    awaitingWhoRef.current = null;
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
    composer.setText(text);
    inputRef.current?.focus();
  }

  // onKeyDown moved into ComposerInput (v1.250.0, S-05) — it reads the "/"
  // match list and the highlighted row, which now live with the text.

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

  /** v1.326.0 (calm chat): the breadcrumb's last part is the SAVED thread's
   *  title. An empty new chat shows no crumb, and neither does a first turn
   *  before its save lands (a moment): echoing the question there would put
   *  the same words on screen twice, right above the bubble that holds them. */
  const crumbTitle =
    threads.find((t) => t.id === threadId)?.title?.trim() ||
    (threadId && openedTitle?.id === threadId ? openedTitle.title.trim() : "");

  /**
   * THE CHAT TOP BAR (v1.326.0, calm chat W1-1). It replaces the chat card's
   * header row of buttons (v1.215.0: Voice, read aloud, Persona, edit,
   * Project) and the vertical project strip on the right.
   *
   * LEFT: where you are. "<project> / <thread title>", just the title with
   * no project, nothing on an empty new chat; then, in a project, its
   * Chat / Tasks / Board / Media views as plain text tabs (the same
   * ProjectViewTabs, so the tab roles and ids are unchanged).
   * RIGHT: a quiet Project button (opens the project panel drawer), Share
   * (moved here from under the composer) and "⋯", which holds hands-free
   * Voice chat, Read replies aloud, the Persona choice + its editor, and the
   * project panel toggle. Every control the old header had is one press (or
   * one press and the menu) away.
   *
   * It sits OUTSIDE the chat card, above both the conversation and a
   * project's Tasks/Board/Media surface, so one bar (and one tablist) serves
   * every view and nothing moves when the view changes. `shrink-0` in a flex
   * column whose only scrolling child is the transcript: never scrolled away,
   * and not `sticky` (v1.215.0's reason still holds). No border: the calm
   * look is hairlines at most, and the bar needs none.
   */
  const menuRow =
    "flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-[13px] text-zinc-200 transition-colors hover:bg-white/[0.06] disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-transparent";
  const chatTopBar = (
    <div
      data-testid="chat-topbar"
      className="flex min-h-12 shrink-0 flex-wrap items-center gap-x-3 gap-y-1 py-1 sm:h-12 sm:flex-nowrap sm:py-0"
    >
      {/* v1.329.0 (calm chat W4 F2): on a phone the chat list lives in the
          nav drawer (the same list the sidebar holds on a wide screen), and
          this quiet ghost opens it. Its name says which chats it opens on
          (the v1.315.0 rule). Not drawn while a sidebar holds the list. */}
      {!chatSlot && (
        <button
          type="button"
          data-testid="chat-open-chats"
          onClick={() => window.dispatchEvent(new CustomEvent("ij:toggle-nav"))}
          aria-label={`Chats${activeProject ? ` in ${activeProject.name}` : ""}${
            threads.length ? ` (${threads.length})` : ""
          }`}
          title="Your chats"
          className="-ml-1.5 inline-flex h-7 shrink-0 items-center gap-1.5 rounded-lg px-1.5 text-[13px] text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-zinc-100 md:hidden"
        >
          <MessageSquare size={14} aria-hidden />
          Chats
        </button>
      )}
      {titleRename && titleRename.id === threadId ? (
        // v1.329.0 (calm chat W4 F8): the ⋯ menu's Rename. The title turns
        // into a box right where it is read; Enter saves, Escape leaves it as
        // it was, and clicking away saves (the list row's rename does too).
        <div
          data-testid="chat-title-rename"
          className="flex min-w-0 flex-1 items-center gap-1.5 text-[13px] text-zinc-500 sm:max-w-[28rem]"
        >
          {activeProject && (
            // On a phone the box needs the room more than the project name
            // does (the Project button still names it).
            <span className="hidden min-w-0 items-center gap-1.5 sm:flex">
              <span className="min-w-0 max-w-[10rem] shrink truncate" title={activeProject.name}>
                {activeProject.name}
              </span>
              <span aria-hidden className="shrink-0 text-zinc-600">
                /
              </span>
            </span>
          )}
          <input
            autoFocus
            value={titleRename.draft}
            onChange={(e) => {
              const v = e.target.value;
              setTitleRename((r) => (r ? { ...r, draft: v } : r));
            }}
            onFocus={(e) => e.currentTarget.select()}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                void endTitleRename(true, true);
              } else if (e.key === "Escape") {
                // Only the rename: never the composer's Stop or a drawer.
                e.preventDefault();
                e.stopPropagation();
                void endTitleRename(false, true);
              }
            }}
            onBlur={() => void endTitleRename(true, false)}
            aria-label="Rename this chat"
            placeholder="Chat name"
            className="field h-8 min-w-0 flex-1 py-1 text-[13px] text-zinc-100"
          />
        </div>
      ) : crumbTitle ? (
        <nav
          aria-label="Breadcrumb"
          data-testid="chat-breadcrumb"
          className="flex min-w-0 flex-1 items-center gap-1.5 text-[13px] text-zinc-500 sm:flex-initial"
        >
          {activeProject && (
            <>
              <span className="min-w-0 max-w-[14rem] shrink truncate" title={activeProject.name}>
                {activeProject.name}
              </span>
              <span aria-hidden className="shrink-0 text-zinc-600">
                /
              </span>
            </>
          )}
          <span
            aria-current="page"
            className="min-w-0 truncate font-medium text-zinc-100"
            title={crumbTitle}
          >
            {crumbTitle}
          </span>
        </nav>
      ) : (
        // Keeps the right-hand controls at the right on an empty new chat.
        <span aria-hidden className="flex-1 sm:hidden" />
      )}
      {activeProject && (
        // On a phone the tabs take their own line under the crumb; from sm
        // they sit beside it.
        <div className="order-last w-full sm:order-none sm:w-auto">
          <ProjectViewTabs view={projectView} onSelect={setProjectView} />
        </div>
      )}
      <div className="ml-auto flex shrink-0 items-center gap-1">
        {voiceMode && (
          // Hands-free voice is ON: say so where the user is looking and keep
          // its off switch one press away (the old header's red button).
          <button
            type="button"
            onClick={toggleVoiceMode}
            aria-label="End voice chat"
            title="End voice chat"
            className="inline-flex h-7 items-center gap-1.5 rounded-lg px-2 text-[12px] font-medium text-tone-danger transition-colors hover:bg-white/[0.06]"
          >
            <AudioLines size={14} />
            <span className="hidden sm:inline">Voice on</span>
          </button>
        )}
        {/* v1.315.0's rule kept: icon-only below sm and NAMED with the
            project, so on a phone the button still says which one. */}
        <button
          ref={projectButtonRef}
          type="button"
          data-testid="chat-project-button"
          onClick={() => (workspaceOpen ? hideProjectPanel() : showProjectPanel("user"))}
          aria-expanded={workspaceOpen}
          aria-haspopup="dialog"
          aria-label={activeProject ? `Project: ${activeProject.name}` : "Project"}
          title={
            activeProject
              ? `Project: ${activeProject.name}. Its folder, files and knowledge`
              : "Pick a project (or just a folder). Armed file tools run there"
          }
          className={`inline-flex h-7 items-center gap-1.5 rounded-lg px-2 text-[13px] transition-colors hover:bg-white/[0.06] hover:text-zinc-100 ${
            workspaceOpen ? "bg-white/[0.06] text-zinc-100" : "text-zinc-400"
          }`}
        >
          {activeProject ? <FolderKanban size={14} /> : <PanelRight size={14} />}
          <span className="hidden sm:inline">Project</span>
        </button>
        <button
          type="button"
          onClick={() => setShareOpen(true)}
          disabled={!threadId}
          aria-label="Share this chat"
          title={
            threadId
              ? "Share this chat: the full transcript or a compacted digest"
              : "A chat can be shared after its first reply (it saves automatically)"
          }
          className="inline-flex h-7 items-center gap-1.5 rounded-lg px-2 text-[13px] text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-zinc-100 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent"
        >
          <Share2 size={14} />
          <span className="hidden sm:inline">Share</span>
        </button>
        <ChatMoreMenu
          // A fresh visit never opens with Delete already half-pressed, and
          // leaving the menu disarms it (the list row's menu does the same).
          onOpenChange={() => {
            setDeleteArmedId(null);
            // v1.329.0 (W5 G1): every visit opens with the project list folded.
            setMoreProjectsOpen(false);
          }}
        >
          {(closeMenu, menuTrigger) => (
            <>
              {/* v1.329.0 (calm chat W4 F8): THE OPEN CHAT'S OWN ACTIONS.
                  Only for a saved chat (a new chat has nothing to rename,
                  pin, archive or delete yet), and the very handlers the
                  chat list's row menu calls: renameThread, togglePin,
                  archiveThread (409 → ArchiveChatDialog) and the two-press
                  pressDelete. Each one that closes the menu hands focus to
                  the ⋯ button first, so a dialog that opens next (Archive's)
                  gives it back there, never to the page body. */}
              {threadId && (
                <>
                  <button
                    type="button"
                    data-testid="chat-more-rename"
                    onClick={() => {
                      const trigger = menuTrigger();
                      closeMenu();
                      startTitleRename(threadId, crumbTitle, trigger);
                    }}
                    className={menuRow}
                  >
                    <Pencil size={15} className="shrink-0 text-zinc-400" />
                    <span className="min-w-0 flex-1">Rename</span>
                  </button>
                  <button
                    type="button"
                    data-testid="chat-more-pin"
                    aria-pressed={pinnedIds.includes(threadId)}
                    onClick={() => {
                      menuTrigger()?.focus();
                      closeMenu();
                      togglePin(threadId);
                    }}
                    className={menuRow}
                  >
                    {pinnedIds.includes(threadId) ? (
                      <PinOff size={15} className="shrink-0 text-zinc-400" />
                    ) : (
                      <Pin size={15} className="shrink-0 text-zinc-400" />
                    )}
                    <span className="min-w-0 flex-1">
                      {pinnedIds.includes(threadId) ? "Unpin" : "Pin to top"}
                    </span>
                  </button>
                  {/* v1.329.0 (calm chat W5 G1): the rest of the row ⋯'s
                      actions, the same handlers: rememberThread,
                      crystallizeThread and assignThreadProject. A busy row
                      says so with aria-disabled rather than `disabled`, so
                      focus stays on it while it works (a disabled button
                      drops focus to the page). Memory keeps the menu open
                      (its spinner and check are the only feedback, as on the
                      row); the workflow card and a project move close it. */}
                  <button
                    type="button"
                    data-testid="chat-more-remember"
                    aria-disabled={rememberingId !== null || undefined}
                    onClick={() => void rememberThread(threadId)}
                    className={`${menuRow} aria-disabled:cursor-default aria-disabled:opacity-60`}
                  >
                    {rememberingId === threadId ? (
                      <Loader2 size={15} className="shrink-0 animate-spin text-accent-soft" />
                    ) : rememberedId === threadId ? (
                      <Check size={15} className="shrink-0 text-tone-success" />
                    ) : (
                      <Brain size={15} className="shrink-0 text-zinc-400" />
                    )}
                    <span className="min-w-0 flex-1">
                      {rememberedId === threadId ? "Saved to memory" : "Commit to memory"}
                    </span>
                  </button>
                  <button
                    type="button"
                    data-testid="chat-more-workflow"
                    aria-disabled={crystallizingId !== null || undefined}
                    onClick={async () => {
                      if (crystallizingId) return;
                      const trigger = menuTrigger();
                      await crystallizeThread(threadId);
                      // Focus goes back to ⋯ only if it is still in this menu
                      // (the call takes seconds; the user may have moved on).
                      if (trigger?.parentElement?.contains(document.activeElement)) {
                        trigger.focus();
                      }
                      closeMenu();
                    }}
                    className={`${menuRow} aria-disabled:cursor-default aria-disabled:opacity-60`}
                  >
                    {crystallizingId === threadId ? (
                      <Loader2 size={15} className="shrink-0 animate-spin text-accent-soft" />
                    ) : (
                      <GitBranch size={15} className="shrink-0 text-zinc-400" />
                    )}
                    <span className="min-w-0 flex-1">Turn into workflow</span>
                  </button>
                  <button
                    type="button"
                    data-testid="chat-more-project"
                    aria-expanded={moreProjectsOpen}
                    onClick={() => setMoreProjectsOpen((v) => !v)}
                    className={menuRow}
                  >
                    <FolderKanban size={15} className="shrink-0 text-zinc-400" />
                    <span className="min-w-0 flex-1">Add to project</span>
                    <ChevronRight
                      size={13}
                      className={`shrink-0 text-zinc-500 transition-transform ${
                        moreProjectsOpen ? "rotate-90" : ""
                      }`}
                    />
                  </button>
                  {moreProjectsOpen && (
                    <div
                      data-testid="chat-more-project-list"
                      role="group"
                      aria-label="Add to project"
                      className="max-h-44 overflow-y-auto pl-4"
                    >
                      {projects.length === 0 ? (
                        <p className="px-2.5 py-2 text-[12px] text-zinc-500">
                          No projects yet. Make one with the Project button
                          above the chat.
                        </p>
                      ) : (
                        <>
                          {projects.map((pr) => (
                            <button
                              key={pr.id}
                              type="button"
                              aria-pressed={projectId === pr.id}
                              aria-disabled={assigningThread || undefined}
                              onClick={async () => {
                                if (assigningThread) return;
                                const trigger = menuTrigger();
                                if (await assignThreadProject(threadId, pr.id)) {
                                  trigger?.focus();
                                  closeMenu();
                                }
                              }}
                              className={`${menuRow} aria-disabled:cursor-default aria-disabled:opacity-60`}
                            >
                              <span className="min-w-0 flex-1 truncate">{pr.name}</span>
                              {projectId === pr.id && (
                                <Check size={13} className="shrink-0 text-accent-soft" />
                              )}
                            </button>
                          ))}
                          {projectId && (
                            <button
                              type="button"
                              data-testid="chat-more-project-remove"
                              aria-disabled={assigningThread || undefined}
                              onClick={async () => {
                                if (assigningThread) return;
                                const trigger = menuTrigger();
                                if (await assignThreadProject(threadId, null)) {
                                  trigger?.focus();
                                  closeMenu();
                                }
                              }}
                              className={`${menuRow} text-zinc-400 aria-disabled:cursor-default aria-disabled:opacity-60`}
                            >
                              <X size={13} className="shrink-0" />
                              <span className="min-w-0 flex-1">Remove from project</span>
                            </button>
                          )}
                        </>
                      )}
                    </div>
                  )}
                  <button
                    type="button"
                    data-testid="chat-more-archive"
                    onClick={() => {
                      menuTrigger()?.focus();
                      closeMenu();
                      void archiveThread(threadId);
                    }}
                    title="Hide it from the chat list. It is kept, and comes back from Archived"
                    className={menuRow}
                  >
                    <Archive size={15} className="shrink-0 text-zinc-400" />
                    <span className="min-w-0 flex-1">Archive</span>
                  </button>
                  <div className="my-1 h-px bg-white/[0.06]" />
                </>
              )}
              <button
                type="button"
                onClick={() => {
                  closeMenu();
                  toggleVoiceMode();
                }}
                disabled={!dictation.supported}
                aria-pressed={voiceMode}
                // The visible words are the name, so the accessible name holds
                // what a sighted user reads.
                aria-label={voiceMode ? "Voice on" : "Voice chat"}
                title={
                  voiceMode
                    ? "End voice chat"
                    : dictation.supported
                      ? "Speak, hear the replies, hands-free"
                      : dictation.reason || "Voice isn't available here yet"
                }
                className={menuRow}
              >
                <AudioLines
                  size={15}
                  className={`shrink-0 ${voiceMode ? "text-tone-danger" : "text-zinc-400"}`}
                />
                <span className="min-w-0 flex-1">{voiceMode ? "Voice on" : "Voice chat"}</span>
                {voiceMode && <span className="text-[12px] text-tone-danger">On</span>}
              </button>
              {tts.supported && (
                <button
                  type="button"
                  onClick={tts.toggle}
                  aria-pressed={tts.enabled}
                  title={tts.enabled ? "Spoken replies are on. Press to mute" : "Read replies aloud"}
                  className={menuRow}
                >
                  {tts.enabled ? (
                    <Volume2 size={15} className="shrink-0 text-accent-soft" />
                  ) : (
                    <VolumeX size={15} className="shrink-0 text-zinc-400" />
                  )}
                  <span className="min-w-0 flex-1">Read replies aloud</span>
                  <span className="text-[12px] text-zinc-500">{tts.enabled ? "On" : "Off"}</span>
                </button>
              )}
              <div className="my-1 h-px bg-white/[0.06]" />
              <div className="px-2.5 pb-1 pt-1.5">
                <label
                  htmlFor="chat-persona-select"
                  className="mb-1 block text-[12px] text-zinc-500"
                >
                  Persona
                </label>
                <div className="flex items-center gap-1.5">
                  <select
                    id="chat-persona-select"
                    aria-label="Persona"
                    value={persona}
                    onChange={(e) => {
                      const v = e.target.value;
                      setPersonaEditorOpen(false);
                      if (v === NEW_PERSONA) {
                        // The editor opens below the bar; get out of its way.
                        closeMenu();
                        startNewPersona();
                      } else choosePersona(v);
                    }}
                    disabled={busy}
                    title={
                      persona === NEW_PERSONA
                        ? "Create a new persona"
                        : selectedPersonaDesc || "Persona for replies"
                    }
                    className="field min-w-0 flex-1 py-1.5 text-[13px]"
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
                    onClick={() => {
                      closeMenu();
                      if (personaEditorOpen) closePersonaEditor();
                      else openPersonaEditor();
                    }}
                    disabled={busy || persona === NEW_PERSONA}
                    aria-pressed={personaEditorOpen}
                    title="Modify this persona"
                    aria-label="Modify persona"
                    className={`inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg transition-colors hover:bg-white/[0.06] disabled:cursor-not-allowed disabled:opacity-50 ${
                      personaEditorOpen ? "text-accent-soft" : "text-zinc-400"
                    }`}
                  >
                    <Pencil size={14} />
                  </button>
                </div>
              </div>
              <div className="my-1 h-px bg-white/[0.06]" />
              <button
                type="button"
                onClick={() => {
                  closeMenu();
                  if (workspaceOpen) hideProjectPanel();
                  // This row unmounts with the menu, so name the "⋯"
                  // button as where focus comes back on close.
                  else showProjectPanel("user", menuTrigger());
                }}
                aria-pressed={workspaceOpen}
                className={menuRow}
              >
                {workspaceOpen ? (
                  <PanelRightClose size={15} className="shrink-0 text-zinc-400" />
                ) : (
                  <PanelRightOpen size={15} className="shrink-0 text-zinc-400" />
                )}
                <span className="min-w-0 flex-1">
                  {workspaceOpen ? "Hide project panel" : "Show project panel"}
                </span>
              </button>
              {threadId && (
                <>
                  <div className="my-1 h-px bg-white/[0.06]" />
                  {/* Deleting is permanent: the first press arms it and says
                      so (the menu stays open), the second deletes. The same
                      pressDelete as the list row, so the open chat is left
                      for a new one, as there. */}
                  <button
                    type="button"
                    data-testid="chat-more-delete"
                    data-armed={deleteArmedId === threadId ? "true" : undefined}
                    onClick={() => {
                      if (deleteArmedId === threadId) {
                        menuTrigger()?.focus();
                        closeMenu();
                      }
                      pressDelete(threadId);
                    }}
                    className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-[13px] text-tone-danger transition-colors hover:bg-tone-danger/10"
                  >
                    <Trash2 size={15} className="shrink-0" />
                    <span className="min-w-0 flex-1">
                      {deleteArmedId === threadId ? "Delete for good? Press again" : "Delete chat"}
                    </span>
                  </button>
                </>
              )}
            </>
          )}
        </ChatMoreMenu>
        {/* NO "New chat" HERE (v1.215.0). The thread rail's header already
            carries one; two buttons doing one thing is the duplication the
            user called out in the Agents module. */}
      </div>
    </div>
  );

  // v1.250.0 (S-05): ONE handlers object for every MessageRow, identity-stable
  // for the life of the page. Each method calls through a ref refreshed on
  // every render, so a memoized row's props change only when its own message
  // does — a keystroke or a streamed token no longer invalidates 50 bubbles.
  // The implementations below are the SAME functions the inline map called;
  // all of them are hoisted `function` declarations, so reading them here is
  // safe wherever this sits in the body.
  const rowImpl: RowHandlers = {
    retryTask: (task) => {
      if (task) composer.setText(task);
      inputRef.current?.focus();
    },
    regenerate: (choice) => regenerate(choice),
    switchVersion: (index, to) => switchVersion(index, to),
    continueReply: () => continueReply(),
    readAloud: (index) => {
      const msg = messagesRef.current[index];
      if (msg?.content) tts.readAloud(msg.content, `reply-${index}`);
    },
    editMessage: (index) => {
      // v1.278.0: the message goes back into the box with its files and the
      // conversation is cut BEFORE it. The cut is saved at once when anything
      // precedes it; an emptied thread is re-saved by the resend itself.
      if (busy) return;
      const target = messagesRef.current[index];
      if (!target || target.role !== "user") return;
      const kept = messagesRef.current.slice(0, index);
      setEditUndo({ before: messagesRef.current, text: composer.get().text, files: attachmentsRef.current });
      setMessages(kept);
      if (kept.length) queueSave(kept);
      composer.setText(target.content);
      setAttachments(attachmentsOf(target));
      // v1.328.0: the chats it pointed at come back as chips with it.
      setChatRefs(target.chatRefs ?? []);
      setChatRefNote("");
      inputRef.current?.focus();
    },
    crystallize: (id) => void crystallizeThread(id),
    handOff: (index) => void handOffPanelReply(index),
    rateReply: (index, rating) => {
      const cur = messagesRef.current;
      if (!cur[index] || cur[index].role !== "assistant") return;
      const updated = cur.map((m, j) => (j === index ? { ...m, rating } : m));
      messagesRef.current = updated;
      setMessages(updated);
      // The buttons are disabled mid-turn, so this never races a turn's save.
      queueSave(updated);
    },
    settleSuggestion: (index, next) => {
      // v1.305.0: the answer is remembered for every LATER save too — a turn
      // that started before the press ends with a history still holding the
      // open ask, and queueSave runs every save through this map.
      settledSuggestionsRef.current.set(next.id, next);
      const cur = messagesRef.current;
      const target = cur[index];
      if (!target || target.suggestion?.id !== next.id) return;
      const updated = cur.map((m, j) => (j === index ? { ...m, suggestion: next } : m));
      messagesRef.current = updated;
      setMessages(updated);
      // Mid-turn the turn's own end save carries it (through the map); a
      // save now would race that turn's start save for the same thread.
      if (!busy) queueSave(updated);
    },
    settleConfigCard: (index, cardIndex, next) => {
      // Like settleSuggestion: remembered for every later save, so a turn
      // that began before the press cannot write the unsettled card back.
      settledCardsRef.current.set(cardId(next), next);
      const cur = messagesRef.current;
      const target = cur[index];
      if (!target?.configCards?.[cardIndex]) return;
      const cards = target.configCards.map((c, k) => (k === cardIndex ? next : c));
      const updated = cur.map((m, j) => (j === index ? { ...m, configCards: cards } : m));
      messagesRef.current = updated;
      setMessages(updated);
      if (!busy) queueSave(updated);
    },
    promote: (content) => promoteNoteToKnowledge(content),
    openDocument: (path) => openDocPreview(path),
    undoFor: (path) => undoForPath(path),
    undoWrite: (actionId, path) => undoWrite(actionId, path),
  };
  // v1.325.0: what the newest reply's "Try again with…" offers. Recent picks
  // are read when the catalog or the pick changes, never per keystroke.
  const regenOptions = useMemo(
    () => ({ models, recent: readRecentModels(), current: choice }),
    [models, choice],
  );
  // Calm chat W1-5 (v1.326.0): what the receipt's "answered by …" calls the
  // model. v1.329.0 (G2): the SAME name the composer's model chip uses (the
  // catalog's label, else friendlyModelName: "Opus 4.8", never
  // "claude-opus-4-8"); lib/answeredModel holds the rule. No model on the
  // route: undefined, and the receipt names the provider instead. Returns a
  // STRING so each memoized row re-renders only when its own name changes.
  const answeredModelName = useCallback(
    (route: TurnRoute | undefined): string | undefined => answeredModelNameFor(route, models),
    [models],
  );
  const rowImplRef = useRef(rowImpl);
  rowImplRef.current = rowImpl;
  const rowHandlers = useMemo<RowHandlers>(
    () => ({
      retryTask: (task) => rowImplRef.current.retryTask(task),
      regenerate: (choice) => rowImplRef.current.regenerate(choice),
      switchVersion: (index, to) => rowImplRef.current.switchVersion(index, to),
      continueReply: () => rowImplRef.current.continueReply(),
      readAloud: (index) => rowImplRef.current.readAloud(index),
      editMessage: (index) => rowImplRef.current.editMessage(index),
      crystallize: (id) => rowImplRef.current.crystallize(id),
      handOff: (index) => rowImplRef.current.handOff(index),
      settleSuggestion: (index, next) =>
        rowImplRef.current.settleSuggestion(index, next),
      rateReply: (index, rating) => rowImplRef.current.rateReply(index, rating),
      settleConfigCard: (index, cardIndex, next) =>
        rowImplRef.current.settleConfigCard(index, cardIndex, next),
      promote: (content) => rowImplRef.current.promote(content),
      openDocument: (path) => rowImplRef.current.openDocument(path),
      undoFor: (path) => rowImplRef.current.undoFor(path),
      undoWrite: (actionId, path) => rowImplRef.current.undoWrite(actionId, path),
    }),
    [],
  );

  // The ⋯ beside every row, in the chat list AND (v1.328.0) the Archived
  // view; its menu knows which list it was opened from.
  const threadRowAction = (t: ThreadSummary) => (
    /* v1.315.0: visible by default — a touch screen has no
       hover, so the old opacity-0 left an invisible target.
       Only a hover-capable pointer hides it until the row is
       hovered or focused (where it takes the age's place). */
    <span
      className={`absolute right-1 top-1/2 -translate-y-1/2 transition-opacity focus-within:opacity-100 [@media(hover:hover)]:group-hover/thread:opacity-100 ${
        threadMenu?.id === t.id
          ? "opacity-100"
          : "[@media(hover:hover)]:opacity-0"
      }`}
    >
      <button
        type="button"
        onClick={(e) => openThreadMenu(e, t.id)}
        aria-label={`Options for ${t.title || "chat"}`}
        aria-haspopup="menu"
        aria-expanded={threadMenu?.id === t.id}
        title="Chat options"
        className={`grid h-7 w-7 place-items-center rounded-md transition-colors hover:bg-white/[0.06] md:h-6 md:w-6 ${
          threadMenu?.id === t.id
            ? "bg-white/[0.06] text-zinc-200"
            : "text-zinc-500 hover:text-zinc-200"
        }`}
      >
        <MoreHorizontal size={14} />
      </button>
    </span>
  );

  // The sidebar's New chat always reaches this render's newChat.
  newChatRef.current = newChat;
  // Redesign S7: the thread rail, lifted into a value so it can render in the
  // app sidebar (portal) or in place — one element, one behaviour.
  // v1.329.0 (calm chat W5 G3): a slot that scrolls as ONE column (the phone
  // nav drawer, `data-flow="column"`) takes the rail at its full height, so
  // the list is never a short scroll box inside the drawer's own scroll.
  const slotFlows = chatSlot?.getAttribute("data-flow") === "column";
  const threadRail = (
            <section
              data-testid="chat-thread-rail"
              data-in-sidebar={chatSlot ? "true" : undefined}
              className={
                // v1.329.0: no card around it beside the chat either (a
                // pop-out, a collapsed sidebar): one hairline divides it.
                slotFlows
                  ? "flex flex-col"
                  : chatSlot
                    ? "flex min-h-0 flex-1 flex-col"
                    : "flex h-full min-h-0 flex-col overflow-hidden border-r hairline pr-2"
              }
            >
              {/* THE MODULE'S NAME, TOP LEFT, INSIDE THIS CARD (v1.215.0) —
                  "We can also put the chat title in the card on the left just
                  like in agents". `ModuleTitle` is the SAME component the other
                  pages use, at a smaller size: the hover/focus/tap popover and
                  its a11y wiring have one implementation, not two. */}
              <div className={chatSlot ? "hidden" : "shrink-0 px-3 pt-2.5"}>
                <ModuleTitle
                  title="Chat"
                  hint={CHAT_HINT}
                  className="text-[15px] font-semibold tracking-tight text-zinc-50"
                  iconSize={11}
                />
              </div>
              <div className="shrink-0 px-3 pb-2 pt-1.5">
                <div className="flex items-center justify-between gap-2">
                  {/* v1.315.0 (thread-rail-scope-silent): a project-scoped
                      rail SAYS so — the other chats are not gone, they are
                      one press away ("All chats", which widens this list
                      only). */}
                  {railScoped && activeProject ? (
                    <span
                      // Two lines before it clips: the project's name is the
                      // point of this label, and the rail is only 15rem wide.
                      className="line-clamp-2 min-w-0 flex-1 break-words text-[11px] font-medium leading-snug text-zinc-400"
                      title={`Showing the chats in ${activeProject.name}`}
                    >
                      Threads in {activeProject.name}
                    </span>
                  ) : activeProject ? (
                    <span className="text-[11px] font-medium uppercase tracking-wide text-zinc-500">
                      All chats
                    </span>
                  ) : chatSlot ? (
                    // In the app sidebar the "Chats" heading already names it.
                    <span />
                  ) : (
                    <span className="text-[11px] font-medium uppercase tracking-wide text-zinc-500">
                      Threads
                    </span>
                  )}
                  <button
                    type="button"
                    onClick={newChat}
                    className={`inline-flex h-7 shrink-0 items-center gap-1 whitespace-nowrap rounded-[10px] px-2 text-[12px] text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60 ${chatSlot ? "hidden" : ""}`}
                    title="Start a new conversation"
                  >
                    <Plus size={13} /> New chat
                  </button>
                </div>
                {activeProject && (
                  <button
                    type="button"
                    data-testid="thread-rail-filter"
                    aria-pressed={railScoped}
                    onClick={() => setRailOnly(!railScoped)}
                    className="mt-0.5 max-w-full truncate text-left text-[11px] text-zinc-500 underline-offset-2 transition-colors hover:text-accent-soft hover:underline"
                    title={
                      railScoped
                        ? "Show every saved chat here. The open chat and its project stay as they are."
                        : `Show only the chats in ${activeProject.name}`
                    }
                  >
                    {railScoped ? "All chats" : `Only ${activeProject.name}`}
                  </button>
                )}
                {listedThreads.length > 0 && (
                  // `isolate` + `z-[1]` (the v1.313.0 rule): the icon must
                  // paint OVER the field, not under its fill.
                  <div className="relative isolate mt-2">
                    <Search
                      size={12}
                      className="pointer-events-none absolute left-2.5 top-1/2 z-[1] -translate-y-1/2 text-zinc-500"
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
              {/* THE ONLY SCROLLING PART of the rail. `min-h-0` is load-bearing:
                  a flex child's default `min-height:auto` refuses to shrink
                  below its content, so without it the list grows the card past
                  the bottom of the window. (v1.329.0: no `max-h` floor any
                  more; below md the rail is only ever in the phone drawer.)
                  v1.329.0 (G3): in a one-column slot (the phone drawer) it does
                  not scroll at all; the drawer scrolls, once. */}
              <div className={slotFlows ? "p-1.5" : "min-h-0 flex-1 overflow-y-auto p-1.5"}>
                {archivedView ? (
                  /* v1.328.0 (calm chat W3-3): the ARCHIVED view, in the
                     list's place. Every project's archived chats, one plain
                     list; a row's ⋯ offers Unarchive and Delete, and pressing
                     a row brings the chat back and opens it. */
                  <div data-testid="thread-archived-view" className="min-w-0">
                    <div className="flex items-center gap-2 px-1 pb-1">
                      <button
                        type="button"
                        onClick={() => setArchivedView(false)}
                        data-testid="thread-archived-back"
                        className="inline-flex h-7 items-center gap-1 rounded-[10px] px-1.5 text-[12px] text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-200 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60"
                      >
                        <ArrowLeft size={13} aria-hidden="true" />
                        Chats
                      </button>
                      <h3 className="ml-auto pr-1.5 text-[11px] font-medium uppercase tracking-[0.06em] text-zinc-500">
                        Archived
                      </h3>
                    </div>
                    {archivedThreads.length === 0 ? (
                      <p className="px-2.5 py-3 text-xs leading-relaxed text-zinc-500">
                        No archived chats.
                      </p>
                    ) : visibleArchived.length === 0 ? (
                      <p className="px-2.5 py-3 text-xs leading-relaxed text-zinc-500">
                        No archived chats match “{threadQuery.trim()}”.
                      </p>
                    ) : (
                      <>
                        <p className="px-2.5 pb-1.5 text-[12px] leading-relaxed text-zinc-500">
                          Opening one brings it back to your chats.
                        </p>
                        <ThreadGroups
                          threads={visibleArchived}
                          projects={projects}
                          activeId={null}
                          onOpen={(id) => void unarchiveThread(id, true)}
                          headings={false}
                          limit={Infinity}
                          rowAction={threadRowAction}
                        />
                      </>
                    )}
                  </div>
                ) : threadsLoading && threads.length === 0 ? (
                  <div className="space-y-1 p-1">
                    {[0, 1, 2, 3].map((i) => (
                      <div key={i} className="skeleton h-9 w-full" />
                    ))}
                  </div>
                ) : (railLead ? listedThreads.length : threads.length) === 0 ? (
                  <p className="px-2.5 py-3 text-xs leading-relaxed text-zinc-500">
                    {(railScoped && activeProject) || railLead
                      ? "No chats in this project yet."
                      : "No saved chats yet — conversations appear here after the first reply."}
                  </p>
                ) : (railLead ? visibleThreads.length + visibleOthers.length : visibleThreads.length) === 0 ? (
                  <p className="px-2.5 py-3 text-xs leading-relaxed text-zinc-500">
                    No chats match “{threadQuery.trim()}”.
                  </p>
                ) : (
                  /* v1.327.0 (calm chat W2-1): grouped under projects, each
                     row a status dot (running / waiting / unread), the title
                     and a short age. The "Only <project>" filter makes it one
                     plain list (its header already names the project) and a
                     search shows every match; otherwise five per group, then
                     "Show more". Rename, pin and the ⋯ menu ride the rows'
                     slots exactly as before.
                     v1.329.0 (G3): in a project chat the same grouped list,
                     that project's group first and open (`lead`), the other
                     groups folded to a heading with its dot (`foldOthers`); a
                     search opens them all. */
                  <>
                  {railLead && threads.length === 0 && !threadQuery.trim() ? (
                    <p
                      data-testid="thread-rail-project-empty"
                      className="px-2.5 pb-1 pt-2 text-[12px] leading-relaxed text-zinc-500"
                    >
                      No chats in this project yet.
                    </p>
                  ) : null}
                  <ThreadGroups
                    threads={railLead ? [...visibleThreads, ...visibleOthers] : visibleThreads}
                    projects={projects}
                    activeId={threadId}
                    onOpen={(id) => void openThread(id)}
                    statuses={threadStatusMap}
                    pinnedIds={pinnedIds}
                    lead={railLead}
                    foldOthers={Boolean(railLead)}
                    forceOpen={Boolean(threadQuery.trim())}
                    headings={!(railScoped && activeProject)}
                    limit={(railScoped && activeProject) || threadQuery.trim() ? Infinity : GROUP_LIMIT}
                    rowEditor={(t) =>
                      renamingId === t.id ? (
                        <input
                          autoFocus
                          value={renameDraft}
                          onChange={(e) => setRenameDraft(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") {
                              e.preventDefault();
                              void renameThread(t.id, renameDraft);
                            } else if (e.key === "Escape") {
                              // Only the rename: never the phone drawer the
                              // list may be sitting in (it closes on Escape).
                              e.stopPropagation();
                              setRenamingId(null);
                            }
                          }}
                          onBlur={() => void renameThread(t.id, renameDraft)}
                          aria-label="Rename chat"
                          className="field my-0.5 w-full py-1 text-[13px]"
                        />
                      ) : null
                    }
                    rowBadge={(t) =>
                      // A MESSAGING thread names where it comes from (the
                      // v1.315.0 cue, now beside the title).
                      t.owner === "daemon" ? (
                        <span
                          className="shrink-0 text-[11px] text-accent-soft/80"
                          title="Messaging thread"
                        >
                          {capitalize(t.comm_channel || "linked")}
                        </span>
                      ) : null
                    }
                    rowAction={threadRowAction}
                  />
                  </>
                )}
                {/* v1.328.0: work an archive could not stop from here is
                    named, never called stopped. */}
                {archiveNote ? (
                  <p
                    role="status"
                    data-testid="thread-archive-note"
                    className="mx-1 mt-2 flex items-start gap-2 rounded-[10px] border hairline px-2.5 py-2 text-[12px] leading-relaxed text-zinc-400"
                  >
                    <span className="min-w-0 flex-1">{archiveNote}</span>
                    <button
                      type="button"
                      onClick={() => setArchiveNote(null)}
                      aria-label="Dismiss"
                      className="shrink-0 text-zinc-500 transition-colors hover:text-zinc-200"
                    >
                      <X size={12} />
                    </button>
                  </p>
                ) : null}
                {/* v1.328.0: the way into the Archived view, quiet at the
                    foot of the list, only while something is archived. */}
                {!archivedView && archivedThreads.length > 0 ? (
                  <button
                    type="button"
                    onClick={() => {
                      setArchivedView(true);
                      void refreshArchived();
                    }}
                    data-testid="thread-archived-link"
                    className="mt-2 flex h-7 w-full items-center gap-1.5 rounded-[10px] pl-[22px] pr-2.5 text-left text-[12px] text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-300 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60"
                  >
                    <Archive size={12} aria-hidden="true" className="shrink-0" />
                    Archived ({archivedThreads.length})
                  </button>
                ) : null}
              </div>
            </section>
  );

  // Calm chat W1-3 (v1.326.0): a NEW chat (nothing said, nothing running) puts
  // the composer in the middle of the screen. The SAME composer element moves
  // to the bottom dock after the first message: only its containers change
  // layout, so the typed text and the focus survive the move.
  const emptyHero = messages.length === 0 && !busy;

  // The project chip and its menu. ONE definition, drawn in one of two places:
  // in the card's toolbar once the conversation has a message, and just above
  // the card on a new chat (where it names the project, so a phone shows the
  // name too, and the menu opens downward because the top of the screen is
  // the greeting). Same label, same title, same menu either way.
  const projectSwitch = (where: "toolbar" | "above") => {
    const above = where === "above";
    return (
      <div ref={projPopRef} className={above ? "relative" : "sm:relative"}>
        <button
          type="button"
          onClick={() => {
            setProjMenuOpen((v) => !v);
            setToolMenuOpen(false);
          }}
          aria-expanded={projMenuOpen}
          aria-haspopup="true"
          aria-label="Switch project"
          title={
            activeProject
              ? `Working in "${activeProject.name}" — click to switch projects or go plain chat`
              : "Work inside a project — replies ground in its files + knowledge"
          }
          className={
            above
              ? `inline-flex h-8 max-w-full items-center gap-1.5 rounded-full px-3 text-[13px] transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 hover:bg-white/[0.06] ${
                  activeProject ? "text-accent-soft" : "text-zinc-500 hover:text-zinc-200"
                }`
              : composerChipClass(Boolean(activeProject))
          }
        >
          <FolderKanban size={14} className="shrink-0" />
          {above ? (
            <>
              <span className="min-w-0 truncate">
                {activeProject ? activeProject.name : "No project"}
              </span>
              <ChevronDown size={13} className="shrink-0 opacity-70" aria-hidden />
            </>
          ) : (
            /* v1.315.0: icon-only on a phone (the title above and the top
               bar's breadcrumb still name the project). */
            activeProject && (
              <span className="hidden max-w-[9rem] truncate sm:inline">
                {activeProject.name}
              </span>
            )
          )}
        </button>
        {projMenuOpen && (
          <div
            className={`absolute left-0 z-20 max-h-64 w-60 overflow-y-auto rounded-xl border border-white/10 bg-zinc-900 p-1 shadow-lg shadow-black/40 ${
              above ? "top-full mt-2" : "bottom-full mb-2"
            }`}
          >
            <button
              type="button"
              onClick={() => {
                chooseProject("");
                setProjMenuOpen(false);
              }}
              className={`flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-[13px] transition-colors hover:bg-white/[0.06] ${
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
                className={`flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-[13px] transition-colors hover:bg-white/[0.06] ${
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
    );
  };

  // The PreflightNote, built once: on a new chat it sits quietly UNDER the
  // card (W1-3), in a conversation it stays in the notices tray above it.
  const preflightNote = (
    <PreflightNote
      provider={splitChoice(choice).provider || health.defaultProvider}
      available={
        health.byProvider[
          splitChoice(choice).provider || health.defaultProvider
        ]
      }
      stale={health.stale}
      cooldownS={
        // Optional-chained on purpose (v1.232.0): this map is newer
        // than the hook's other fields, and a caller holding an
        // older shape must not take the whole composer down.
        health.cooldownByProvider?.[
          splitChoice(choice).provider || health.defaultProvider
        ]
      }
      signedOut={
        // v1.234.0: same optional-chain rule as cooldownS.
        health.signedOutByProvider?.[
          splitChoice(choice).provider || health.defaultProvider
        ]
      }
    />
  );

  return (
    <PageShell className="space-y-0">
      {/* NOTHING STANDS ABOVE THE WORK (v1.215.0). The page header and the
          standing blurb that followed it are both gone: the title moved into
          the thread rail, its explanation moved behind that title, and the
          controls moved into the chat card's own header. What is left starts
          at the top of the module. */}
      <Reveal>
        {/* THE MODULE FILLS THE APP (v1.215.0), the same frame the Agents room
            uses: `md:h-[calc(100vh-4.5rem)]` is the title bar (2.5rem) plus
            MainContent's own `py-4` (2rem) — minus the demo strip's height
            (`--ij-strip-h`, published by SimulatedBanner, v1.314.0), so the row ends exactly where the
            window does and only the panes inside it scroll. `items-stretch`
            (was `items-start`) is what lets all three columns take that
            height. Below md it is a plain stack — three columns on a phone is
            three unusable columns — and each pane carries its own capped
            height there instead. */}
        <div
          data-testid="chat-room"
          className="flex flex-col gap-4 md:h-[calc(100vh-4.5rem-var(--ij-strip-h,0px))] md:min-h-[28rem] md:flex-row md:items-stretch"
        >
          {/* Threads: in the app sidebar on a wide screen (redesign S7) and
              in the phone nav drawer while it is open (v1.329.0, calm chat W4
              F2), portaled. With no slot (a pop-out, a collapsed sidebar) the
              rail sits beside the chat from md up; on a phone it never sits
              in the page: the top bar's Chats opens the drawer, which holds
              this same list (one list, not two). */}
          {chatSlot ? (
            createPortal(threadRail, chatSlot)
          ) : (
          <aside
            data-testid="chat-rail-aside"
            className="hidden shrink-0 md:block md:h-full md:w-60"
          >
            {threadRail}
          </aside>
          )}

          {/* ⋯ thread menu popout (v1.114.0) — portaled to <body> so neither
              the Card's overflow-hidden nor a themed backdrop-filter can clip
              or reposition it. One menu at a time; every action either closes
              it or (memory) shows its progress inline. */}
          {typeof document !== "undefined" &&
            createPortal(
              <AnimatePresence>
                {threadMenu &&
                  (() => {
                    // v1.328.0: a menu opened in the Archived view is about
                    // an archived chat: Unarchive and Delete only.
                    const mt = (archivedView ? archivedThreads : listedThreads).find(
                      (x) => x.id === threadMenu.id,
                    );
                    if (!mt) return null;
                    const pinned = pinnedIds.includes(mt.id);
                    const item =
                      "flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-[13px] text-zinc-200 transition-colors hover:bg-white/[0.06]";
                    return (
                      <m.div
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
                        {archivedView ? (
                          <button
                            type="button"
                            role="menuitem"
                            className={item}
                            data-testid="thread-menu-unarchive"
                            onClick={() => void unarchiveThread(mt.id)}
                          >
                            <ArchiveRestore size={14} className="shrink-0 text-zinc-400" />
                            Unarchive
                          </button>
                        ) : (
                          <>
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
                              <Check size={14} className="shrink-0 text-tone-success" />
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
                              <m.div
                                initial={{ height: 0, opacity: 0 }}
                                animate={{ height: "auto", opacity: 1 }}
                                exit={{ height: 0, opacity: 0 }}
                                transition={{ duration: 0.14, ease: "easeOut" }}
                                className="overflow-hidden"
                              >
                                <div className="max-h-44 overflow-y-auto pl-4">
                                  {projects.length === 0 ? (
                                    <p className="px-2.5 py-2 text-[12px] text-zinc-500">
                                      No projects yet. Make one with the Project
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
                              </m.div>
                            )}
                          </AnimatePresence>
                          <button
                            type="button"
                            role="menuitem"
                            className={item}
                            data-testid="thread-menu-archive"
                            onClick={() => void archiveThread(mt.id)}
                          >
                            <Archive size={14} className="shrink-0 text-zinc-400" />
                            Archive
                          </button>
                          </>
                        )}
                        <div className="my-1 h-px bg-white/[0.06]" />
                        {/* v1.322.0 (borrowed idea: assistant-ui's separate
                            archive/delete): deleting is permanent, so the
                            first press arms it and says so; the second
                            deletes. A client conversation is never one
                            misclick away from gone. */}
                        <button
                          type="button"
                          role="menuitem"
                          className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-[13px] text-tone-danger transition-colors hover:bg-tone-danger/10"
                          data-armed={deleteArmedId === mt.id ? "true" : undefined}
                          onClick={() => pressDelete(mt.id)}
                        >
                          <Trash2 size={14} className="shrink-0" />
                          {deleteArmedId === mt.id ? "Delete for good? Press again" : "Delete chat"}
                        </button>
                      </m.div>
                    );
                  })()}
              </AnimatePresence>,
              document.body,
            )}

          {/* Conversation pane. A flex COLUMN that fills the row's height, so
              the card inside it can put a fixed header above a scrolling
              transcript above a fixed composer — "the cards can be pushed up
              making the chat seem more minimalistic". */}
          <div
            className={`flex min-w-0 flex-1 flex-col gap-3 md:min-h-0 ${
              // v1.326.0: on a wide screen an open project drawer takes its
              // own room instead of covering the end of the conversation.
              workspaceOpen ? "xl:pr-[var(--rail-w)]" : ""
            }`}
            style={{ "--rail-w": `${railW}px` } as CSSProperties}
          >
            {chatTopBar}
            {/* Both of these are CONDITIONAL and both moved in here
                (v1.215.0). Left in the page flow above the row, either one
                appearing would push a `100vh`-tall layout off the bottom of
                the window. Inside the column they simply take room from the
                card, which is what a flex column is for. */}
            {offline && (
              <OfflineHint detail="Chat needs it running to reach your agent." />
            )}
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
                      <span className="inline-flex items-center gap-1 text-[12px] text-tone-success">
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
                        className="btn-ghost ml-auto py-1.5 text-[13px] text-tone-danger hover:bg-tone-danger/10"
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
            {/* Project surfaces — the old project screen's views, inside the
                chat module. Chat stays mounted (hidden) so the thread and
                composer state survive a Tasks/Board detour untouched.
                v1.326.0: the one tablist lives in the top bar above, which
                every view shares, so coming back from Tasks needs no second
                copy here. */}
            {activeProject && (
              <div
                id={PROJECT_VIEW_SURFACE_ID}
                role="tabpanel"
                aria-label={`Project ${projectView}`}
                hidden={projectView === "chat"}
              >
                {projectView !== "chat" && (
                  <ProjectSurface
                    projectId={activeProject.id}
                    hasRoot={Boolean(activeProject.root) && activeProject.root_exists !== false}
                    view={projectView}
                  />
                )}
              </div>
            )}
            {/* A plain section, not <Card> (v1.215.0). Card wraps its
                children in an unstyled `<div>` (`ui.tsx`: `{pad ? "p-4" : ""}`),
                so a flex column declared on the Card had exactly ONE flex child
                — that wrapper — which sized to its content and left the rest of
                the column empty. Measured: the card was 828px tall and its
                content stopped at 740, with 144px of dead space under the
                composer. The transcript and the composer have to be DIRECT
                children of the flex column for `flex-1` to divide the height
                between them.
                v1.326.0 (calm chat): NO CARD. No border, fill or shadow
                (`card-surface` is gone): the conversation sits on the page
                background and the composer is the one card on the screen.
                The testid keeps its old name; tests and tools find it by it. */}
            <section
              data-testid="chat-card"
              id={PROJECT_VIEW_CHAT_ID}
              role={activeProject ? "tabpanel" : undefined}
              aria-label={activeProject ? "Project chat" : undefined}
              // Calm chat W1-3: a new chat may be taller than a short window
              // (the connect doors, the ideas), so it scrolls as a whole; a
              // conversation scrolls only its transcript, as before.
              // v1.328.0: from md only. Below md the column has no set height,
              // so the section is as tall as its content and never scrolled:
              // the class only CLIPPED, and it held the composer's "@" menu to
              // the strip under the card while the rest of the phone screen
              // sat empty. The page itself scrolls there.
              className={`relative flex h-full min-h-0 flex-col ${
                emptyHero ? "md:overflow-y-auto" : "overflow-hidden"
              } ${activeProject && projectView !== "chat" ? "hidden" : ""}`}
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
              {/* Calm chat W1-3: the room ABOVE the new-chat group. With the
                  matching spacer under the dock (grown a little more, so the
                  card sits slightly above true centre) it centres greeting,
                  project chip, card and suggestions in the screen. In a
                  conversation both spacers shrink to nothing; flex-grow
                  eases, so the card glides to the bottom (not under
                  prefers-reduced-motion). Always mounted: the dock must keep
                  its place among its siblings so the composer never remounts. */}
              <div
                aria-hidden
                data-testid="chat-hero-spacer-top"
                className="min-h-0 shrink-0 basis-0 transition-[flex-grow] duration-300 ease-out motion-reduce:transition-none"
                style={{ flexGrow: emptyHero ? 1 : 0 }}
              />
              {/* Message thread — THE ONLY SCROLLING PART of the card.
                  `min-h-0 flex-1` replaces `max-h-[60vh] min-h-[24rem]`: the
                  transcript now takes exactly the room the header and composer
                  leave it, instead of a guessed fraction of the viewport that
                  left dead space under short conversations and a second
                  scrollbar under long ones. The `max-h` is the narrow-width
                  floor, where the column has no height to divide up. */}
              <div
                ref={scrollRef}
                onScroll={onThreadScroll}
                // Redesign S11 (AUDIT §8): the conversation reads in a 760 px
                // column, centred — each row is held to it, no new wrapper.
                // v1.326.0 (calm chat W1-2): no divider above the composer;
                // the last 28px of the transcript FADE into it (a mask, so it
                // fades into whatever the theme's page is), and the bottom
                // padding keeps the newest line clear of the fade.
                // Calm chat W1-3: on a new chat it holds only the greeting and
                // takes its own height (`flex-none`), so the group can centre;
                // there is nothing to fade there, so no mask and no fade room
                // (the greeting would be faded, and sit far above the card).
                className={`flex flex-col gap-4 overflow-y-auto [&>*]:mx-auto [&>*]:w-full [&>*]:max-w-[760px] ${
                  emptyHero
                    ? "flex-none px-4 pb-4 pt-4 sm:px-5"
                    : "max-h-[60vh] min-h-0 flex-1 p-4 pb-8 sm:p-5 sm:pb-8 md:max-h-none [-webkit-mask-image:linear-gradient(to_bottom,black_calc(100%-28px),transparent)] [mask-image:linear-gradient(to_bottom,black_calc(100%-28px),transparent)]"
                }`}
              >
                {emptyHero ? (
                  // Redesign S11 (AUDIT §8, wireframe home.md): a calm greeting
                  // in the display step. Calm chat W1-3: one line; the
                  // project chip, the card, the doors and the suggestions sit
                  // in the dock below, so the composer is the same element
                  // before and after the first message.
                  <div data-testid="chat-greeting" className="flex flex-col items-center pt-2 text-center">
                    <p className="text-display font-medium tracking-tight text-zinc-100">
                      What can I help with?
                    </p>
                  </div>
                ) : (
                  <>
                    {/* v1.250.0 (S-05): one memoized row per message. The
                        branches moved into MessageRow unchanged; what the page
                        keeps is the per-row FACTS (is it last, what came
                        before it, may it be regenerated) and one stable
                        handlers object, so typing or streaming no longer
                        re-renders every bubble in the thread. */}
                    <QuoteSelection onQuote={quoteIntoBox} className="contents">
                    {messages.map((m, i) => {
                      // No regenerate on MESSAGING threads: the daemon owns the
                      // transcript, so a browser-side re-run could never be
                      // saved (and would silently diverge from the phone).
                      const canRegen =
                        !commMeta &&
                        i === messages.length - 1 &&
                        i > 0 &&
                        messages[i - 1].role === "user" &&
                        !busy;
                      return (
                        // Calm chat W1-4: each message holds itself to the
                        // reading column. The scroller's `[&>*]` rule cannot
                        // reach it — it sits inside QuoteSelection, which has
                        // no box (`contents`) — so without this the replies
                        // and your bubble spread across the whole window. A
                        // hidden Continue turn renders nothing, so it keeps
                        // no box (an empty one would add a gap).
                        <div
                          key={i}
                          data-msg-index={i}
                          className={m.continuation ? "contents" : "mx-auto w-full max-w-[760px]"}
                        >
                        <MessageRow
                          m={m}
                          i={i}
                          isLast={i === messages.length - 1}
                          prevUser={
                            i > 0 && messages[i - 1].role === "user"
                              ? messages[i - 1].content
                              : ""
                          }
                          prevUserAt={
                            i > 0 && messages[i - 1].role === "user"
                              ? messages[i - 1].at
                              : undefined
                          }
                          canRegen={canRegen}
                          busy={busy}
                          threadId={threadId}
                          projectId={projectId}
                          crystallizingId={crystallizingId}
                          reading={tts.readingKey === `reply-${i}`}
                          regen={canRegen ? regenOptions : undefined}
                          answeredName={answeredModelName(m.route)}
                          h={rowHandlers}
                        />
                        </div>
                      );
                    })}
                    </QuoteSelection>
                    {/* v1.323.0: FOLLOW-UP QUESTIONS under the newest reply —
                        a press puts the question in the box, never sends it. */}
                    {!busy && followups && followups.forLen === messages.length && (
                      <FollowupChips
                        suggestions={followups.items}
                        onPick={(q) => {
                          inputFromVoiceRef.current = false;
                          composer.setText(q);
                          setFollowups(null);
                          inputRef.current?.focus();
                        }}
                      />
                    )}
                    {/* CHAT MODE: the live streaming bubble. Streamed markdown +
                        a blinking caret once the first token lands (a Thinking
                        shimmer until then), with any live tool calls below. */}
                    {chatBusy && (
                      <div aria-live="polite" aria-busy="true">
                        {/* v1.250.0 (S-03): the live bubble is its own
                            component so a streamed token re-renders IT, not
                            this page. Same bubble, same order, same clock. */}
                        <LiveReply stream={stream} onGrow={scrollLiveIntoView} />
                        {/* v1.278.0: the steer notes sent to this turn. Honest
                            wording — a note lands at the next step, never
                            inside a sentence already being written. */}
                        {pendingSteers.length > 0 && (
                          <div data-testid="steer-notes" className="mt-1 space-y-0.5">
                            {pendingSteers.map((note, k) => (
                              <p key={k} className="text-[12px] text-zinc-500">
                                <span className="text-accent-soft">Steer sent:</span> {note}
                                <span className="text-zinc-600">
                                  {" "}
                                  — Jarvis reads it at its next step.
                                </span>
                              </p>
                            ))}
                          </div>
                        )}
                      </div>
                    )}
                    {/* AGENT MODE: the live working bubble. Narrates the current
                        step, streams the agent's tokens + tool calls as they
                        arrive, and keeps the step feed underneath. */}
                    {awaiting && (
                      <Bubble role="assistant">
                        <div className="flex flex-col gap-1.5" aria-live="polite" aria-busy="true">
                          {/* Calm chat W1-5 (v1.326.0): the run's work as grey
                              one-line rows above its words, oldest first: the
                              last few steps it finished, its tool calls, then
                              the step it is on now (spinning, softly pulsing). */}
                          <div data-testid="live-work" className="grid gap-1">
                            {progress.slice(1, 4).reverse().map((s, i) => (
                              <WorkRow key={`${i}-${s}`} icon={Check} title={s.replace(/…$/, "")} />
                            ))}
                            <LiveToolRows cards={runStream.tools} />
                            {/* v1.149.0: the run's OWN phase, straight from the
                                daemon, in place of a generic "Thinking…". A run
                                that is planning now says so — it used to be
                                indistinguishable from one that was stuck. */}
                            <WorkRow
                              icon={Loader2}
                              running
                              title={
                                runStream.phase
                                  ? PHASE_LABEL[runStream.phase.phase] ?? runStream.phase.phase
                                  : (progress[0] ?? "Thinking…")
                              }
                              detail={runStream.phase?.detail || null}
                            />
                          </div>
                          <AgentLiveText stream={runStream} onGrow={scrollLiveIntoView} />
                          {/* MID-RUN APPROVAL (v1.189.0): the run's asks are
                              answered in the dock, in the composer's place
                              (calm chat W1-6, `dockAsks` below). */}
                        </div>
                      </Bubble>
                    )}
                  </>
                )}
                {/* The "Jump to latest" pill is NOT here: it lives in the dock
                    below, out of this scroller's flow (see there).
                    The sentinel's scroll margin EQUALS the scroller's bottom
                    padding (pb-8), so scrolling it into view lands at the true
                    bottom: the newest line stays clear of the fade, and every
                    bottom scroll (scrollToLatest) agrees on one target. */}
                <div ref={bottomRef} data-testid="chat-bottom" className="scroll-mb-8" />
              </div>

              {/* v1.326.0 (calm chat W1-2): THE DOCK. Everything under the transcript
                  lives here: the notices tray, the ONE composer card (every control
                  inside it) and a quiet line of text under it. The column is the
                  transcript's reading column plus 16px a side, so the text typed in the
                  card lines up with the replies above it. The transcript fades into
                  this area (a mask on the scroller) instead of a divider line. */}
              <div data-testid="chat-dock" className="relative shrink-0 px-3 pb-2 pt-1 sm:px-5 sm:pb-3">
                {/* "Jump to latest" floats just above the dock, over the
                    transcript's faded foot. It must stay OUT of the scroller's
                    flow: in there its own height (plus the gap) was added to
                    the very distance onThreadScroll compares with 80, so once a
                    scroll during a chat's opening showed it, it kept itself on
                    for good over a chat resting at its bottom; and the
                    scroller's `[&>*]:w-full` stretched it to the column.
                    v1.315.0: never over the empty state (the same condition as
                    that branch) — a pill pointing at messages that do not exist
                    covered the "I have an API key" door on a phone. A first
                    reply still streaming is not the empty state.
                    v1.329.0 (calm chat W5 G1): it also steps aside while any
                    composer menu is open, which opens upward into this same
                    space (on a phone it covered the "@" menu's Chats rows).
                    The typed "@" and "/" menus are read from the composer
                    store inside JumpToLatest, never here. */}
                <JumpToLatest
                  store={composer}
                  busy={busy}
                  show={showJump && !(messages.length === 0 && !busy)}
                  menuOpen={anyComposerMenuOpen({
                    plus: toolsOpen,
                    tools: toolMenuOpen,
                    model: modelMenuOpen,
                    project: projMenuOpen,
                    permission: permissionMenuOpen,
                    promptForm: promptForm !== null,
                  })}
                  onJump={jumpToLatest}
                />
                <div className="mx-auto w-full max-w-[792px]">
                  {/* Calm chat W1-3: on a new chat the project chip sits just above
                      the card (in a conversation it is in the card's toolbar). */}
                  {emptyHero && (
                    <div data-testid="chat-hero-project" className="mb-1.5 flex min-w-0 px-2">
                      {projectSwitch("above")}
                    </div>
                  )}
                  {/* THE NOTICES TRAY: what is about the next message (a failed save,
                      an error and its Retry, the preflight warning, the compaction
                      offer, queued messages, the edit Undo, a held key, who the chat
                      is talking to) sits ABOVE the card and attached to it: narrower,
                      tucked under its top edge. Empty, it takes no room at all. */}
                  <div
                    data-testid="composer-notices"
                    className="relative mx-4 -mb-4 flex flex-col rounded-t-2xl bg-ink-875 px-1 pb-4 pt-0.5 shadow-[0_0_0_0.5px_rgb(var(--white)/0.08)] empty:hidden sm:mx-7 [&>*+*]:border-t [&>*+*]:border-white/[0.06]"
                  >
                    {/* AUTOSAVE FAILED (v1.226.0): persistent + dismissible. What
                        is on screen is NOT on disk until Retry succeeds; a silent
                        catch was the bug. Offline (status 0) never lands here —
                        the OfflineHint above already says it. */}
                    {saveFailure && (
                      <div
                        role="status"
                        className="flex flex-wrap items-center gap-2 px-3 py-2 text-[12px]"
                      >
                        <span className="min-w-0 flex-1 text-tone-warn">
                          Couldn&apos;t save this conversation: {saveFailure.detail}
                        </span>
                        <button
                          type="button"
                          disabled={saveFailure.retrying}
                          onClick={() => {
                            // The chip stays until the retry LANDS (CL2): clearing
                            // it here claimed a save that had not happened yet.
                            setSaveFailure({ ...saveFailure, retrying: true });
                            saveFailure.retry();
                          }}
                          title="Save this conversation again"
                          className="btn-ghost shrink-0 py-1 text-[12px]"
                        >
                          <RefreshCw size={12} />{" "}
                          {saveFailure.retrying ? "Retrying…" : "Retry"}
                        </button>
                        <button
                          type="button"
                          aria-label="Dismiss save warning"
                          onClick={() => setSaveFailure(null)}
                          className="btn-ghost shrink-0 py-1 text-[12px]"
                        >
                          <X size={12} />
                        </button>
                      </div>
                    )}
                    {/* v1.312.0 (W4-2): "Allow for this conversation" at the
                        6-tool cap. The grant is kept and sent every turn, but the
                        tool could not also be armed — say so, and say how to keep
                        it on hand, instead of the old silent no-op. At the cap the
                        + picker's rows are disabled, so the way forward names the
                        real path (free a slot first), and "won't ask again" is
                        scoped to when the tool is in use: a grant never arms. */}
                    {grantCapNote && (
                      <div
                        data-testid="grant-cap-note"
                        className="flex items-center gap-2 px-3 py-2 text-[12px] text-zinc-400"
                      >
                        <span className="min-w-0 flex-1">
                          Allowed for this conversation — {grantCapNote} won&apos;t ask
                          again here when it&apos;s in use. All {MAX_TOOLS} tool slots
                          are in use: remove one, then add it from + to keep it
                          available.
                        </span>
                        <button
                          type="button"
                          aria-label="Dismiss"
                          onClick={() => setGrantCapNote(null)}
                          className="btn-ghost shrink-0 py-1 text-[12px]"
                        >
                          <X size={12} />
                        </button>
                      </div>
                    )}
                    {(error || steerBack || (failedTurn && !busy)) && (
                      <div className="flex flex-wrap items-center gap-2 px-3 py-2">
                        {steerBack && (
                          <div
                            data-testid="steer-unread"
                            className="min-w-0 flex-1 text-[12px] text-zinc-400"
                          >
                            Jarvis finished before reading this — press Enter to send it.
                          </div>
                        )}
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
                        {/* A reopened thread ending on a question (CL5): no error
                            text belongs to it, so say what happened. */}
                        {!error && failedTurn && !busy && (
                          <div className="min-w-0 flex-1 text-[12px] text-tone-warn">
                            This didn&apos;t get a reply.
                          </div>
                        )}
                        {/* v1.312.0: while the provider this turn ran on is cooling
                            down, a press could only be refused with the same words —
                            so Retry says when it will work and counts down. When that
                            provider (the pick, else the default) is known down or
                            cooling, the button also offers "Choose another model…",
                            which opens the model menu and nothing else: the page
                            names no model and switches nothing (v1.162.0). */}
                        {failedTurn && !busy && (
                          <RetryTurnButton
                            cooldownS={trouble.cooldownS}
                            onRetry={retryTurn}
                            provider={trouble.provider}
                            down={trouble.down}
                            onChooseModel={() => {
                              setModelSub(null);
                              setModelMenuOpen(true);
                            }}
                          />
                        )}
                        {/* v1.275.0: the page already knows the explicit pick's
                            provider is down and the default is up — one press,
                            instead of the model menu after a typed request. Only
                            that case: a different provider is the user's choice,
                            never a fallback the page makes (the v1.162.0 rule). */}
                        {failedTurn && !busy && canRetryWithDefault(choice, health) && (
                          <button
                            type="button"
                            onClick={() => {
                              setChoice("");
                              retryTurn();
                            }}
                            title={`${splitChoice(choice).provider} is not reachable; ${health.defaultProvider} is. Re-send with the default model.`}
                            className="btn-ghost shrink-0 py-1.5 text-[13px]"
                          >
                            Retry with the default model
                          </button>
                        )}
                      </div>
                    )}
                    {/* A FOLDER BECOMES ONE SUMMARY SHEET (v1.251.0, C-04). Sits with
                        the other pre-send notes above the composer, because it is
                        about the folder this conversation is already pointed at. The
                        card states the document count and the spend BEFORE the click:
                        one press runs the batch, so the number has to be honest. */}
                    {batchPreview && batchDismissed !== batchPreview.folder && (
                      <BatchSuggestCard
                        preview={batchPreview}
                        events={events}
                        // Whatever the user has typed is what the sheet should cover
                        // — read AT THE PRESS, not at render (v1.255.0). This arrived
                        // from a branch cut before v1.250.0 (S-05) moved the composer
                        // into its own store, and `input` no longer exists here: the
                        // page deliberately does not re-render per keystroke, which is
                        // exactly why a render-time read would hand the card stale
                        // text. `get()` is the store's own non-reactive read — the
                        // same one this page already uses on send.
                        getInstructions={() => composer.get().text}
                        // The sheet lands in this conversation's own folder when it
                        // has one (v1.244.0), else beside the documents themselves.
                        workspaceDir={workfolder ?? workspaceDir}
                        onDone={(made) => {
                          // The Files rail is how a made file is ever found again.
                          if (made.length) {
                            rememberThreadDocs(made);
                            showProjectPanel("app");
                          }
                        }}
                        onDismiss={() => setBatchDismissed(batchPreview.folder)}
                      />
                    )}
                    {/* THE ONE CONDITIONAL LINE (redesign S8, AUDIT R2): restart-cut
                        jobs, failing background work, or running / waiting work —
                        the most urgent only, Open → Everything › Status. */}
                    <HomeLine />
                    {/* PREFLIGHT (v1.165.0): the active model is known-unreachable
                        BEFORE the user types a paragraph into it. The app always had
                        this fact (/health) and used to reveal it only after the turn
                        failed. Watches the EXPLICIT pick when there is one, else the
                        DEFAULT provider — the default is exactly where the mock
                        incident happened. "auto" resolves per-turn, so it is never
                        warned about (absent from the map → undefined → silent). */}
                    {/* Calm chat W1-3: on a new chat the note sits under the card
                        instead (the same element, `preflightNote`). */}
                    {!emptyHero && preflightNote}
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
                    {/* v1.325.0: messages waiting for the running reply (Ctrl+Enter).
                        Sent one by one when a reply finishes cleanly; after a Stop
                        or a failure they wait for "Send now". */}
                    {queued.length > 0 && (
                      <div data-testid="queued-messages" className="space-y-1 px-3 py-1.5">
                        {queued.map((q, k) => (
                          <div
                            key={q.id}
                            data-testid="queued-message"
                            className="flex items-center gap-2 text-[12px] text-zinc-400"
                          >
                            <span className="shrink-0 text-zinc-500">
                              {busy ? (k === 0 ? "Sends after this reply:" : "Then:") : "Waiting to send:"}
                            </span>
                            <span className="min-w-0 flex-1 truncate text-zinc-300" title={q.text}>
                              {q.text || q.files.map((f) => f.name).join(", ")}
                              {q.text && q.files.length > 0 ? ` (+${q.files.length} file${q.files.length === 1 ? "" : "s"})` : ""}
                            </span>
                            {!busy && k === 0 && (
                              <button
                                type="button"
                                onClick={() => {
                                  unqueue(q.id, false);
                                  sendQueued(q);
                                }}
                                className="rounded-md px-2 py-0.5 text-zinc-200 hover:bg-white/[0.06]"
                              >
                                Send now
                              </button>
                            )}
                            <button
                              type="button"
                              onClick={() => unqueue(q.id, true)}
                              className="rounded-md px-2 py-0.5 text-zinc-300 hover:bg-white/[0.06]"
                            >
                              Edit
                            </button>
                            <button
                              type="button"
                              onClick={() => unqueue(q.id, false)}
                              aria-label="Don't send this message"
                              title="Don't send this message"
                              className="grid h-6 w-6 place-items-center rounded-md text-zinc-500 hover:bg-white/[0.06] hover:text-zinc-200"
                            >
                              <X size={12} />
                            </button>
                          </div>
                        ))}
                      </div>
                    )}
                    {editUndo && (
                      <div
                        data-testid="edit-undo"
                        role="status"
                        className="flex items-center gap-2 px-3 py-1.5 text-[12px] text-zinc-400"
                      >
                        <span className="min-w-0 flex-1">
                          Editing a sent message — the {editUndo.before.length - messages.length === 1 ? "message" : "messages"} after it{" "}
                          {editUndo.before.length - messages.length === 1 ? "was" : "were"} removed. Send keeps the earlier
                          version one click away (‹ ›).
                        </span>
                        <button
                          type="button"
                          onClick={() => {
                            const back = editUndo;
                            setEditUndo(null);
                            messagesRef.current = back.before;
                            setMessages(back.before);
                            queueSave(back.before);
                            composer.setText(back.text);
                            setAttachments(back.files);
                          }}
                          className="rounded-md px-2 py-0.5 text-zinc-200 hover:bg-white/[0.06]"
                        >
                          Undo
                        </button>
                      </div>
                    )}
                    {heldSecret !== null && (
                      <SecretPasteNotice
                        message={heldSecret}
                        onSaved={(rest) => {
                          setHeldSecret(null);
                          composer.setText(rest);
                          inputRef.current?.focus();
                        }}
                        onSendAnyway={() => {
                          const text = heldSecret;
                          secretSendOkRef.current = true;
                          setHeldSecret(null);
                          send(text);
                        }}
                        onCancel={() => setHeldSecret(null)}
                      />
                    )}
                    {/* TALKING TO AN AGENT (v1.284.0). After "@builder …" the
                        conversation stays with builder: plain follow-ups go to the
                        panel, and this strip says so — with the way back. */}
                    {addressee.length > 0 && !commMeta && (
                      <div
                        data-testid="addressee-strip"
                        className="flex items-center gap-2 px-3 py-1.5 text-[12px]"
                      >
                        <Bot size={12} className="shrink-0 text-accent-soft" />
                        <span className="min-w-0 truncate text-zinc-300">
                          Talking to{" "}
                          <span className="font-medium text-accent-soft">
                            {addressee.map(agentDisplayName).join(", ")}
                          </span>
                          <span className="text-zinc-500">
                            {" "}
                            — replies come from {addressee.length > 1 ? "them" : "it"}, not Iron
                            Jarvis. Use @ to bring in someone else.
                          </span>
                        </span>
                        <button
                          type="button"
                          onClick={() => setAddressee([])}
                          className="ml-auto shrink-0 rounded-md px-2 py-0.5 text-[12px] text-zinc-300 transition-colors hover:bg-white/[0.06] hover:text-zinc-100"
                        >
                          Back to Jarvis
                        </button>
                      </div>
                    )}
                  </div>
                  {/* THE COMPOSER: the one card on the chat screen. The box on top,
                      then ONE toolbar row. Left: "+", the project, approvals, tools and
                      web. Right: reasoning, the model, the mic and Send (Stop while a
                      turn runs). Chips are ghosts that fill only on hover; the card has
                      a hairline edge and no focus ring (the caret is the cue). */}
                  {/* CALM CHAT W1-6 (v1.326.0): A QUESTION FOR YOU TAKES THE
                      COMPOSER'S PLACE. While a turn waits on the user (an
                      approval, an app's question, an app asking to use the
                      model), that card is drawn here instead of in the
                      transcript, and the composer below is hidden but stays
                      mounted, so the draft, attachments and pickers are all
                      exactly as they were when the question is answered.
                      "Allow for this conversation" routes to the page's ONE
                      grant handler for both lanes (chat-consent-v1192). */}
                  <DockAsk
                    asks={dockAsks}
                    onConversation={armFromApproval}
                    onAnswerElicitation={answerElicitation}
                    onDecideSampling={decideSampling}
                    onStop={stop}
                    returnFocus={focusComposerAfterAsk}
                  />
                  <div
                    data-testid="chat-composer"
                    inert={askInDock}
                    aria-hidden={askInDock || undefined}
                    className={`relative z-[1] ${askInDock ? "hidden" : "flex"} flex-col rounded-[24px] bg-ink-800 ${COMPOSER_CARD_EDGE}`}
                  >
                    {/* "/" skill picker — floats above the composer */}
                    {/* "@" AGENT PICKER (v1.150.0). Same shape as the "/" picker
                        below — one affordance grammar for both. */}
                    <AtPicker
                      store={composer}
                      busy={busy}
                      mentionable={mentionable}
                      resources={packResources}
                      onOpened={loadPackResources}
                      onPickResource={pickAppResource}
                      inputRef={inputRef}
                      openChatId={threadId}
                      chatRefs={chatRefs}
                      onPickChat={pickChatRef}
                      keysRef={atKeysRef}
                      filesRoot={atFilesRoot}
                      attachedPaths={attachedPaths}
                      onPickEntry={pickProjectEntry}
                      chatProjectName={chatProjectName}
                    />
                    <SlashPicker
                      store={composer}
                      busy={busy}
                      skills={skills}
                      inputRef={inputRef}
                      onOpened={onSlashOpened}
                      onPick={pickSkill}
                      prompts={packPrompts}
                      onPickPrompt={setPromptForm}
                    />
                    {/* v1.324.0: an app's prompt — fill its blanks, and its text
                        lands in the box (never sent by itself). */}
                    {promptForm && (
                      <div className="absolute bottom-full left-3 right-3 z-30 mb-2 rounded-xl border border-white/10 bg-zinc-900 p-3 shadow-lg shadow-black/40">
                        <div className="mb-2 flex items-center gap-2">
                          <span className="text-[12px] text-zinc-300">
                            {promptForm.title || promptForm.name}
                          </span>
                          <span className="text-[11px] text-zinc-500">from {promptForm.pack}</span>
                          <button
                            type="button"
                            onClick={() => setPromptForm(null)}
                            aria-label="Close"
                            className="ml-auto text-zinc-500 transition-colors hover:text-zinc-300"
                          >
                            <X size={12} />
                          </button>
                        </div>
                        <PackPromptForm
                          prompt={promptForm}
                          onInsert={(t) => {
                            const cur = composer.get().text.trimEnd();
                            composer.setText(cur ? `${cur}\n\n${t}` : t);
                            setPromptForm(null);
                            inputRef.current?.focus();
                          }}
                        />
                      </div>
                    )}
                    {/* What goes with the next message, INSIDE the card above the
                        box (v1.326.0): this chat's folder, the skill, connections,
                        the page and app files it asks about, and attachments. The
                        skill chip is NOT mode-gated (v1.104.0): a picker whose
                        selection leaves no trace on screen is indistinguishable
                        from one that failed. Tools armed by hand are counted on the
                        toolbar's tools chip and listed (with Disarm) in its menu. */}
                    {(attachments.length > 0 ||
                      appResources.length > 0 ||
                      chatRefs.length > 0 ||
                      chatRefNote !== "" ||
                      pageCtx !== null ||
                      workfolder !== null ||
                      projectSubfolder !== null ||
                      activeSkill !== "" ||
                      selectedConnectors.length > 0) && (
                      <div className="flex flex-wrap items-center gap-1.5 px-3 pt-3">
                        {/* v1.329.0: the working folder is a folder INSIDE the
                            project (picked with "@" or "+ Choose a working
                            folder"). Said on the card, with a way back to the
                            project's own folder, because a choice that leaves no
                            trace reads as one that failed. */}
                        {projectSubfolder !== null && atFilesRoot && (
                          <span
                            data-testid="working-folder-chip"
                            title={`Files this chat's tools make land in ${projectSubfolder}`}
                            className="max-w-full inline-flex items-center gap-1.5 rounded-lg bg-white/[0.05] px-2 py-1 text-[12px] text-zinc-300"
                          >
                            <FolderOpen size={11} className="shrink-0 text-accent-soft" />
                            <span className="max-w-[14rem] truncate">
                              Working in {projectSubfolder.split(/[\\/]/).filter(Boolean).pop() ?? projectSubfolder}
                            </span>
                            <button
                              type="button"
                              onClick={() => chooseWorkspace(atFilesRoot)}
                              aria-label="Work in the project folder again"
                              title="Work in the project folder again"
                              className="text-zinc-500 transition-colors hover:text-tone-danger"
                            >
                              <X size={11} />
                            </button>
                          </span>
                        )}
                        {/* THIS CONVERSATION'S FOLDER (v1.244.0, placeInWorkfolder) —
                            where the files it was handed were copied and where what
                            it makes is saved. On screen because an output nobody can
                            find is exactly the defect this exists to fix. */}
                        {workfolder !== null && (
                          <span
                            data-testid="workfolder-chip"
                            title={`This chat saves its work in ${workfolder}`}
                            className="max-w-full inline-flex items-center gap-1.5 rounded-lg bg-white/[0.05] px-2 py-1 text-[12px] text-zinc-300"
                          >
                            <FolderOpen size={11} className="shrink-0 text-accent-soft" />
                            <span className="max-w-[18rem] truncate">
                              {workfolder.split(/[\\/]/).filter(Boolean).pop() ?? workfolder}
                            </span>
                            <button
                              type="button"
                              onClick={() => {
                                void post("/documents/open", { path: workfolder }).catch((e) =>
                                  setError(e instanceof ApiError ? e.message : String(e)),
                                );
                              }}
                              aria-label="Open this chat's folder"
                              title="Open this folder"
                              className="text-accent-soft/80 transition-colors hover:text-accent"
                            >
                              Open
                            </button>
                          </span>
                        )}
                        {workfolderNote && (
                          <span
                            data-testid="workfolder-note"
                            className="max-w-full truncate text-[11px] text-tone-warn"
                            title={workfolderNote}
                          >
                            {workfolderNote}
                          </span>
                        )}
                        {activeSkill !== "" && (
                          <span className="inline-flex items-center gap-1.5 rounded-lg bg-white/[0.05] px-2 py-1 text-[12px] text-zinc-300">
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
                              className="text-zinc-500 transition-colors hover:text-tone-danger"
                            >
                              <X size={11} />
                            </button>
                          </span>
                        )}
                        {selectedConnectors.map((id) => (
                            <span
                              key={`conn-${id}`}
                              className="inline-flex items-center gap-1.5 rounded-lg bg-white/[0.05] px-2 py-1 text-[12px] text-zinc-300"
                            >
                              <PlugZap size={11} className="shrink-0 text-accent-soft" />
                              <span className="max-w-[14rem] truncate">{id}</span>
                              <button
                                type="button"
                                onClick={() => toggleConnector(id)}
                                aria-label={`Turn off connection ${id}`}
                                title="Turn off for this chat"
                                className="text-zinc-500 transition-colors hover:text-tone-danger"
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
                        {/* v1.325.0: the page this message asks about. */}
                        {pageCtx && (
                          <span
                            data-testid="page-context-chip"
                            title={`${pageCtx.path} — what that page showed goes with your next message`}
                            className="inline-flex items-center gap-1.5 rounded-lg bg-white/[0.05] px-2 py-1 text-[12px] text-zinc-300"
                          >
                            <FileText size={11} className="shrink-0 text-accent-soft" />
                            <span className="max-w-[16rem] truncate">About: {pageCtx.title || pageCtx.path}</span>
                            <button
                              type="button"
                              onClick={() => setPageCtx(null)}
                              aria-label="Don't send the page"
                              title="Don't send the page"
                              className="text-zinc-500 transition-colors hover:text-tone-danger"
                            >
                              <X size={11} />
                            </button>
                          </span>
                        )}
                        {appResources.map((r) => (
                          <span
                            key={`${r.pack}/${r.uri}`}
                            data-testid="app-resource-chip"
                            className="inline-flex items-center gap-1.5 rounded-lg bg-white/[0.05] px-2 py-1 text-[12px] text-zinc-300"
                          >
                            <FileText size={11} className="shrink-0 text-accent-soft" />
                            <span className="max-w-[14rem] truncate">{r.title || r.name || r.uri}</span>
                            <span className="text-zinc-500">{r.pack}</span>
                            <button
                              type="button"
                              onClick={() =>
                                setAppResources((cur) =>
                                  cur.filter((x) => !(x.pack === r.pack && x.uri === r.uri)),
                                )
                              }
                              aria-label={`Remove ${r.title || r.name || r.uri}`}
                              className="text-zinc-500 transition-colors hover:text-tone-danger"
                            >
                              <X size={11} />
                            </button>
                          </span>
                        ))}
                        {/* v1.328.0: saved chats picked with "@" — read with the
                            next message as reference material (≤ 3). */}
                        {chatRefs.map((c) => (
                          <span
                            key={`chat-ref-${c.id}`}
                            data-testid="chat-ref-chip"
                            title={`${c.title}: this chat is read with your next message`}
                            className="inline-flex max-w-full items-center gap-1.5 rounded-lg bg-white/[0.05] px-2 py-1 text-[12px] text-zinc-300"
                          >
                            <MessageSquare size={11} className="shrink-0 text-accent-soft" />
                            <span data-testid="chat-ref-chip-title" className="max-w-[14rem] truncate">{c.title}</span>
                            {formatAge(c.updatedAt) && (
                              <span data-testid="chat-ref-chip-age" className="shrink-0 text-[11px] text-zinc-500">
                                {formatAge(c.updatedAt)}
                              </span>
                            )}
                            <button
                              type="button"
                              onClick={() => {
                                setChatRefs(chatRefsRef.current.filter((x) => x.id !== c.id));
                                setChatRefNote("");
                              }}
                              aria-label={`Remove chat ${c.title}`}
                              title="Don't send this chat"
                              className="text-zinc-500 transition-colors hover:text-tone-danger"
                            >
                              <X size={11} />
                            </button>
                          </span>
                        ))}
                        {chatRefNote && (
                          <span
                            data-testid="chat-ref-limit"
                            role="status"
                            className="text-[11px] text-zinc-500"
                          >
                            {chatRefNote}
                          </span>
                        )}
                        {attachments.map((a, i) => (
                          <span
                            key={`${a.path}-${i}`}
                            className="inline-flex items-center gap-1.5 rounded-lg bg-white/[0.05] px-2 py-1 text-[12px] text-zinc-300"
                          >
                            <Paperclip size={11} className="shrink-0 text-accent-soft" />
                            <span className="max-w-[14rem] truncate">{a.name}</span>
                            <span className="text-zinc-500">{fmtSize(a.bytes)}</span>
                            <button
                              type="button"
                              onClick={() => removeAttachment(i)}
                              aria-label={`Remove ${a.name}`}
                              className="text-zinc-500 transition-colors hover:text-tone-danger"
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
                      <div className="flex items-center gap-2 px-4 pt-2.5 text-xs">
                        <span
                          className={`h-2 w-2 shrink-0 rounded-full ${
                            dictation.listening
                              ? "animate-pulse bg-tone-danger"
                              : "bg-zinc-600"
                          }`}
                        />
                        {dictation.error ? (
                          <span className="truncate text-tone-danger">{dictation.error}</span>
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
                    <input
                      ref={fileRef}
                      type="file"
                      multiple
                      className="hidden"
                      onChange={onPickFiles}
                    />
                    <ComposerInput
                      store={composer}
                      inputRef={inputRef}
                      busy={busy}
                      skills={skills}
                      onSend={send}
                      onStop={stop}
                      onSteer={(text) => void steerTurn(text)}
                      onQueue={queueFollowup}
                      onOpened={onSlashOpened}
                      onPickSkill={pickSkill}
                      onTyped={() => {
                        inputFromVoiceRef.current = false; // typed — never auto-send
                      }}
                      onPasteFiles={(files) => void addFilesRef.current(files)}
                      talkingTo={addressee.map(agentDisplayName).join(", ")}
                      atKeys={atKeysRef}
                    />
                    <div
                      data-testid="composer-toolbar"
                      className="flex flex-wrap items-center gap-x-1 gap-y-1 px-2 pb-2 pt-1"
                    >
                      <div className="flex min-w-0 flex-wrap items-center gap-1">
                        {/* The "+" menu (v1.326.0): what you ADD to a message. Attach, a
                            working folder for the files the chat makes, a skill (the same list
                            "/" opens) and a saved workflow. Persona lives in the top bar's "⋯";
                            tools, web and connections in the tools chip. Its lists open in
                            place (no side flyouts) so a phone never loses them off the edge.
                            `sm:relative`: on a phone the menu hangs off the card, not the chip. */}
                        <div ref={toolsPopRef} className="sm:relative">
                          <button
                            ref={plusBtnRef}
                            type="button"
                            onClick={() => {
                              setToolsOpen((v) => !v);
                              setPlusSub(null);
                              setToolMenuOpen(false);
                            }}
                            aria-expanded={toolsOpen}
                            aria-haspopup="true"
                            aria-label="Open the chat menu"
                            title="Attach files, a working folder, skills and workflows"
                            className={`grid h-[30px] w-[30px] shrink-0 place-items-center rounded-full bg-white/[0.07] transition-colors hover:bg-white/[0.12] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 ${
                              toolsOpen || activeSkill ? "text-accent-soft" : "text-zinc-400 hover:text-zinc-200"
                            }`}
                          >
                            {uploading ? <LoaderInline /> : <Plus size={16} />}
                          </button>
                          {toolsOpen && (
                            <div className="absolute bottom-full left-0 z-20 mb-2 w-64 max-w-[calc(100vw-2rem)] rounded-xl border border-white/10 bg-zinc-900 p-1 shadow-lg shadow-black/40">
                              <button
                                type="button"
                                onClick={() => {
                                  setToolsOpen(false);
                                  fileRef.current?.click();
                                }}
                                disabled={uploading || attachments.length >= MAX_ATTACHMENTS}
                                className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-[13px] text-zinc-200 transition-colors hover:bg-white/[0.06] disabled:opacity-40"
                              >
                                <Paperclip size={14} className="shrink-0 text-zinc-400" />
                                Attach files or photos
                              </button>
                              <button
                                type="button"
                                onClick={() => {
                                  setToolsOpen(false);
                                  setPreviewPath(null);
                                  setRailTab("files");
                                  setPickingFolder(Boolean(workspaceDir));
                                  showProjectPanel("user", plusBtnRef.current);
                                }}
                                title={
                                  workspaceDir
                                    ? `Files this chat's tools make land in ${workspaceDir}`
                                    : "Pick a folder for the files this chat's tools make"
                                }
                                className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-[13px] text-zinc-200 transition-colors hover:bg-white/[0.06]"
                              >
                                <FolderOpen size={14} className="shrink-0 text-zinc-400" />
                                {workspaceDir ? "Change the working folder" : "Choose a working folder"}
                              </button>
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
                                  className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-[13px] text-zinc-200 transition-colors hover:bg-white/[0.06]"
                                >
                                  <Sparkles size={14} className="shrink-0 text-zinc-400" />
                                  Skills
                                  {activeSkill && (
                                    <span className="max-w-[6rem] truncate rounded-full bg-accent/[0.12] px-1.5 text-[10px] text-accent-soft">
                                      {activeSkill}
                                    </span>
                                  )}
                                  <ChevronDown size={13} className="ml-auto shrink-0 text-zinc-500" />
                                </button>
                                {plusSub === "skills" && (
                                  <div className="mb-1 ml-4 max-h-56 overflow-y-auto border-l hairline pl-1">
                                    {activeSkill && (
                                      <button
                                        type="button"
                                        onClick={() => {
                                          setActiveSkill("");
                                          markSetupChanged();
                                        }}
                                        className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-[12px] text-tone-danger/90 transition-colors hover:bg-white/[0.06]"
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
                                          <span className="truncate text-[13px]">{s.name}</span>
                                          <span className="truncate text-[11px] text-zinc-500">
                                            {s.description}
                                          </span>
                                        </button>
                                      ))
                                    )}
                                  </div>
                                )}
                              </div>
                              {/* Run a SAVED workflow from where the user is standing
                                  (v1.170.0): one click starts it name-only (contract 1
                                  — the daemon resolves stored steps + project pin) and
                                  the live chip lands in the thread as a card row.
                                  DISABLED on MESSAGING threads: the daemon owns those
                                  transcripts (queueSave no-ops and every
                                  chat.thread_updated refetch is replace-only), so the
                                  card row would be silently deleted mid-run — same
                                  guard regenerate uses. */}
                              <div className="relative">
                                <button
                                  type="button"
                                  onClick={() => {
                                    setPlusSub(
                                      plusSub === "workflows" ? null : "workflows",
                                    );
                                    ensureWorkflows();
                                  }}
                                  aria-expanded={plusSub === "workflows"}
                                  disabled={Boolean(commMeta)}
                                  title={
                                    commMeta
                                      ? "This messaging thread is server-owned, so the run card can't persist here — run it from the Workflows page instead."
                                      : undefined
                                  }
                                  className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-[13px] text-zinc-200 transition-colors hover:bg-white/[0.06] disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent"
                                >
                                  <GitBranch size={14} className="shrink-0 text-zinc-400" />
                                  Run a workflow…
                                  <ChevronDown size={13} className="ml-auto shrink-0 text-zinc-500" />
                                </button>
                                {plusSub === "workflows" && (
                                  <div className="mb-1 ml-4 max-h-56 overflow-y-auto border-l hairline pl-1">
                                    {savedWorkflows === null ? (
                                      <div className="px-2.5 py-2">
                                        <LoaderInline />
                                      </div>
                                    ) : savedWorkflows === "error" ? (
                                      <p className="px-2.5 py-2 text-[11px] leading-relaxed text-tone-warn/90">
                                        Couldn&apos;t load workflows. Reopen to retry.
                                      </p>
                                    ) : savedWorkflows.length === 0 ? (
                                      <p className="px-2.5 py-2 text-[11px] leading-relaxed text-zinc-500">
                                        No saved workflows yet — draft one by asking,
                                        or open the editor.
                                      </p>
                                    ) : (
                                      savedWorkflows.map((w) => (
                                        <button
                                          key={w.name}
                                          type="button"
                                          onClick={() => void runSavedWorkflow(w.name)}
                                          title={w.description || `Run “${w.name}” now`}
                                          className="flex w-full flex-col rounded-lg px-2.5 py-1.5 text-left text-zinc-200 transition-colors hover:bg-white/[0.06]"
                                        >
                                          <span className="truncate text-[13px]">
                                            {w.name}
                                          </span>
                                          {w.description && (
                                            <span className="truncate text-[11px] text-zinc-500">
                                              {w.description}
                                            </span>
                                          )}
                                        </button>
                                      ))
                                    )}
                                    <Link
                                      href="/workflows"
                                      onClick={() => setToolsOpen(false)}
                                      className="mt-0.5 flex w-full items-center gap-2 rounded-lg border-t hairline px-2.5 py-2 text-left text-[12px] text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-accent-soft"
                                    >
                                      <ExternalLink size={13} className="shrink-0" />
                                      Open the editor ↗
                                    </Link>
                                  </div>
                                )}
                              </div>
                            </div>
                          )}
                        </div>
                        {/* The project chip. On a NEW chat it sits just above the card
                            (calm chat W1-3, `projectSwitch("above")`); once the conversation
                            has a message it lives here, in the toolbar. One chip at a time. */}
                        {!emptyHero && projectSwitch("toolbar")}
                        {/* APPROVAL POSTURE (v1.188.0): how the mid-turn ask behaves for this
                            conversation. v1.327.0: ONE permission chip (a shield, the level's
                            plain name, a chevron) whose small menu lists the levels with a line
                            each; it replaced the native select and keeps its id. The values and
                            words live in lib/permissionLevels.ts; the no-ask level reads amber,
                            so a conversation running without asks looks like one. On a phone
                            only the shield shows. */}
                        <PermissionChip
                          id="chat-approval-mode"
                          iconOnlyOnPhone
                          value={approvalMode}
                          onChange={(picked) => {
                            const mode = asApprovalMode(picked);
                            setApprovalMode(mode);
                            // The pick is BOTH this conversation's posture (persists
                            // with the thread via the setup snapshot) and the user's
                            // new default for future chats: one dial, not two.
                            try {
                              localStorage.setItem(APPROVAL_MODE_KEY, mode);
                            } catch {
                              /* best-effort */
                            }
                            markSetupChanged();
                          }}
                          onOpenChange={setPermissionMenuOpen}
                        />
                        {/* THE TOOLS CHIP (v1.326.0): Auto tools, and the tools armed by hand
                            for this chat, counted so a pick leaves a trace. Its menu holds the
                            web and Auto tools switches with their plain lines, the armed tools
                            (each with Disarm) and the chat's connections. */}
                        <div ref={toolMenuRef} className="sm:relative">
                          <button
                            type="button"
                            data-testid="composer-tools"
                            onClick={() => {
                              setToolMenuOpen((v) => !v);
                              setPlusSub(null);
                              setToolsOpen(false);
                              setProjMenuOpen(false);
                            }}
                            aria-expanded={toolMenuOpen}
                            aria-haspopup="true"
                            aria-label={`Tools: ${toolsChip.text}`}
                            title={toolsChip.title}
                            className={composerChipClass(toolsChip.on)}
                          >
                            <Wrench size={14} className="shrink-0" />
                            <span className="hidden sm:inline">{toolsChip.text}</span>
                            {toolsChip.armed > 0 && <span className="sm:hidden">{toolsChip.armed}</span>}
                          </button>
                          {toolMenuOpen && (
                            <div
                              data-testid="composer-tools-menu"
                              className="absolute bottom-full left-0 z-20 mb-2 w-72 max-w-[calc(100vw-2rem)] rounded-xl border border-white/10 bg-zinc-900 p-1 shadow-lg shadow-black/40"
                            >
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
                                className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-[13px] text-zinc-200 transition-colors hover:bg-white/[0.06] disabled:opacity-40"
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
                              {/* v1.232.0 (audit U8): one plain line under each
                                  switch — the title attribute only shows on hover,
                                  and a switch named "Auto tools" says nothing about
                                  what it does until then. */}
                              <p className="-mt-1 px-2.5 pb-1.5 text-[11px] leading-snug text-zinc-500">
                                Lets this chat search the web and read pages.
                              </p>
                              <button
                                type="button"
                                onClick={toggleAutoTools}
                                role="switch"
                                aria-checked={autoTools}
                                title="Each request arms the safe tools it needs (files, documents, web, images)"
                                className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-[13px] text-zinc-200 transition-colors hover:bg-white/[0.06]"
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
                              <p className="-mt-1 px-2.5 pb-1.5 text-[11px] leading-snug text-zinc-500">
                                Each request picks the safe tools it needs (files, documents, web, images).
                              </p>
                              {armedTools.length > 0 && (
                                <div className="mt-1 border-t hairline pt-1">
                                  <p className="px-2.5 pb-0.5 pt-1 text-[10px] uppercase tracking-wide text-zinc-500">
                                    Turned on for this chat
                                  </p>
                                  {armedTools.map((name) => (
                                    <div
                                      key={name}
                                      className="flex items-center gap-2 rounded-lg px-2.5 py-1 text-[12px] text-zinc-300"
                                    >
                                      <Wrench size={12} className="shrink-0 text-accent-soft" />
                                      <span className="min-w-0 flex-1 truncate font-mono">{name}</span>
                                      <button
                                        type="button"
                                        onClick={() => disarmTool(name)}
                                        aria-label={`Disarm ${name}`}
                                        title="Disarm tool"
                                        className="grid h-6 w-6 shrink-0 place-items-center rounded-md text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-tone-danger"
                                      >
                                        <X size={12} />
                                      </button>
                                    </div>
                                  ))}
                                </div>
                              )}
                              <div className="my-1 border-t hairline" />
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
                                  className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-[13px] text-zinc-200 transition-colors hover:bg-white/[0.06]"
                                >
                                  <PlugZap size={14} className="shrink-0 text-zinc-400" />
                                  Connections
                                  <ChevronDown size={13} className="ml-auto shrink-0 text-zinc-500" />
                                </button>
                                {plusSub === "connectors" && (
                                  <div className="mb-1 ml-4 max-h-56 overflow-y-auto border-l hairline pl-1">
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
                                            <span className="ml-auto shrink-0 text-[10px] uppercase tracking-wide text-zinc-600">
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
                                        <p className="px-2.5 pb-0.5 text-[10px] font-semibold uppercase tracking-wide text-zinc-600">
                                          From the Directory
                                        </p>
                                        {marketplaceTeasers.map((c) => (
                                          <Link
                                            key={c.id}
                                            href="/marketplace"
                                            onClick={() => setToolMenuOpen(false)}
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
                                      onClick={() => setToolMenuOpen(false)}
                                      className="mt-0.5 flex w-full items-center gap-2 rounded-lg border-t hairline px-2.5 py-2 text-left text-[12px] text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-accent-soft"
                                    >
                                      <Store size={13} className="shrink-0" />
                                      Directory ↗
                                    </Link>
                                  </div>
                                )}
                              </div>
                            </div>
                          )}
                        </div>
                        {/* Web research, one press (v1.326.0). The same switch, with its
                            plain line, is in the tools menu. */}
                        <button
                          type="button"
                          data-testid="composer-web"
                          onClick={toggleWeb}
                          disabled={!webArmed && !webRoom}
                          role="switch"
                          aria-checked={webArmed}
                          aria-label="Web research"
                          title={
                            webArmed
                              ? "Web research is on for this chat. Press to turn it off."
                              : webRoom
                                ? "Let this chat search the web and read pages"
                                : `All ${MAX_TOOLS} tool slots are in use. Turn one off first.`
                          }
                          className={composerChipClass(webArmed)}
                        >
                          <Globe size={14} className="shrink-0" />
                          <span className="hidden sm:inline">Web</span>
                        </button>
                      </div>
                      <div className="ml-auto flex shrink-0 items-center gap-1">
                        {/* v1.263.0: the reasoning level, ONLY for a model that offers
                            one (the daemon's catalog says which). A control that does
                            nothing for the picked model is not drawn at all. */}
                        {reasoningLevelsFor(choice).length > 0 && (
                          <span className="relative inline-flex shrink-0 items-center">
                            <select
                              aria-label="Reasoning level"
                              data-testid="reasoning-level"
                              value={reasoningLevelsFor(choice).includes(reasoning) ? reasoning : ""}
                              onChange={(e) => {
                                setReasoning(e.target.value);
                                markSetupChanged();
                              }}
                              disabled={awaiting && sessionId !== null}
                              title="How hard the model thinks before answering. Higher is slower and costs more. Reasoning means the model's own default level."
                              className="h-[30px] max-w-[10rem] cursor-pointer appearance-none rounded-lg border-0 bg-transparent pl-2 pr-6 text-[13px] text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-zinc-200 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 disabled:opacity-40"
                            >
                              {/* v1.326.0: short words for a chip beside the
                                  model ("High"); the select's name says what
                                  they measure. */}
                              <option value="" className="bg-ink-900 text-zinc-200">
                                Reasoning
                              </option>
                              {reasoningLevelsFor(choice).map((lvl) => (
                                <option key={lvl} value={lvl} className="bg-ink-900 text-zinc-200">
                                  {lvl.charAt(0).toUpperCase() + lvl.slice(1)}
                                </option>
                              ))}
                            </select>
                            <ChevronDown
                              size={12}
                              aria-hidden
                              className="pointer-events-none absolute right-2 text-zinc-500"
                            />
                          </span>
                        )}
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
                            className={`${composerChipClass(false)} max-w-[11rem] sm:max-w-[16rem]`}
                          >
                            <span
                              data-testid="model-chip-words"
                              className="min-w-0 truncate"
                              title={
                                modelTriggerRaw
                                  ? modelTriggerDefault
                                    ? `The default model: ${modelTriggerRaw}`
                                    : modelTriggerRaw
                                  : undefined
                              }
                            >
                              {modelTriggerDefault && <span className="sr-only">Default: </span>}
                              {modelTriggerText}
                            </span>
                            <ChevronDown size={12} className="shrink-0 opacity-70" />
                          </button>
                          {modelMenuOpen && (
                            <div
                              data-testid="model-menu"
                              className="absolute bottom-full right-0 z-20 mb-1.5 w-60 rounded-xl border border-white/10 bg-zinc-900 p-1 shadow-lg shadow-black/40"
                            >
                              {/* v1.277.0: type to find a model across every provider. */}
                              <input
                                data-testid="model-filter"
                                value={modelFilter}
                                onChange={(e) => setModelFilter(e.target.value)}
                                onKeyDown={(e) => {
                                  if (e.key === "Enter" && modelMatches[0]) {
                                    e.preventDefault();
                                    pickModel(`${modelMatches[0].provider}::${modelMatches[0].model}`);
                                  } else if (e.key === "Escape") {
                                    setModelMenuOpen(false);
                                    setModelSub(null);
                                  }
                                }}
                                placeholder="Type to find a model…"
                                aria-label="Find a model"
                                autoFocus
                                className="mb-1 w-full rounded-lg border border-white/10 bg-black/20 px-2 py-1 text-[12px] text-zinc-200 outline-none placeholder:text-zinc-600 focus:border-accent/40"
                              />
                              {modelFilter.trim() ? (
                                modelMatches.length === 0 ? (
                                  <p className="px-2.5 py-1.5 text-[12px] text-zinc-500">No model matches.</p>
                                ) : (
                                  modelMatches.map((m) => {
                                    const v = `${m.provider}::${m.model}`;
                                    return (
                                      <button
                                        key={`match-${v}`}
                                        type="button"
                                        data-testid="model-match"
                                        onClick={() => pickModel(v)}
                                        disabled={m.available === false}
                                        title={m.available === false ? `${m.name || m.provider} isn't connected` : undefined}
                                        className={`flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left font-mono text-[12px] transition-colors hover:bg-white/[0.06] disabled:cursor-not-allowed disabled:opacity-45 ${
                                          choice === v ? "text-accent-soft" : "text-zinc-300"
                                        }`}
                                      >
                                        <span className="min-w-0 truncate" title={m.label ? m.model : undefined}>
                                          {modelText(m)}
                                        </span>
                                        <ModelRowChips m={m} />
                                        <span className="ml-auto shrink-0 font-sans text-[10px] text-zinc-500">
                                          {m.name || m.provider}
                                        </span>
                                      </button>
                                    );
                                  })
                                )
                              ) : (
                                <>
                              {recentModels.length > 0 && (
                                <div data-testid="model-recent" className="mb-1 border-b border-white/[0.06] pb-1">
                                  <p className="px-2.5 pb-0.5 text-[10px] uppercase tracking-wide text-zinc-600">Recent</p>
                                  {recentModels.map((v) => {
                                    const { provider, model } = splitChoice(v);
                                    const row = models.find((m) => m.provider === provider && m.model === model);
                                    if (!row) return null;
                                    return (
                                      <button
                                        key={`recent-${v}`}
                                        type="button"
                                        onClick={() => pickModel(v)}
                                        disabled={row.available === false}
                                        className={`flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left font-mono text-[12px] transition-colors hover:bg-white/[0.06] disabled:cursor-not-allowed disabled:opacity-45 ${
                                          choice === v ? "text-accent-soft" : "text-zinc-300"
                                        }`}
                                      >
                                        <span className="min-w-0 truncate" title={row.label ? model : undefined}>
                                          {modelText(row)}
                                        </span>
                                        <ModelRowChips m={row} />
                                        <span className="ml-auto shrink-0 font-sans text-[10px] text-zinc-500">
                                          {row.name || row.provider}
                                        </span>
                                      </button>
                                    );
                                  })}
                                </div>
                              )}
                              <button
                                type="button"
                                onClick={() => pickModel("")}
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
                                              onClick={() => pickModel(v)}
                                              className={`flex w-full items-center rounded-lg px-2.5 py-1.5 text-left font-mono text-[12px] transition-colors hover:bg-white/[0.06] ${
                                                choice === v
                                                  ? "text-accent-soft"
                                                  : "text-zinc-300"
                                              }`}
                                            >
                                              <span
                                                className="min-w-0 truncate"
                                                title={m.label ? m.model : undefined}
                                              >
                                                {modelText(m)}
                                              </span>
                                              <span className="ml-1.5 inline-flex shrink-0 items-center gap-1">
                                                <ModelRowChips m={m} />
                                              </span>
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
                                </>
                              )}
                              {splitChoice(choice).provider && (
                                <div className="mt-1 border-t border-white/[0.06] pt-1">
                                  <button
                                    type="button"
                                    data-testid="model-make-default"
                                    onClick={() => void makeDefault()}
                                    className="w-full rounded-lg px-2.5 py-1.5 text-left text-[12px] text-zinc-300 hover:bg-white/[0.06]"
                                  >
                                    Make this my default
                                  </button>
                                  {defaultNote && (
                                    <p role="status" className="px-2.5 pb-1 text-[11px] text-zinc-500">
                                      {defaultNote}
                                    </p>
                                  )}
                                </div>
                              )}
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
                          className={`${COMPOSER_ICON_BUTTON} ${
                            dictation.listening
                              ? "text-tone-danger hover:bg-white/[0.06]"
                              : "text-zinc-400 hover:bg-white/[0.06] hover:text-zinc-200"
                          }`}
                        >
                          {dictation.listening && (
                            <span className="pointer-events-none absolute right-1 top-1 h-2 w-2 animate-pulse rounded-full bg-tone-danger" />
                          )}
                          {dictation.supported ? <Mic size={16} /> : <MicOff size={16} />}
                        </button>
                        {/* Send, round and in the accent; while a reply streams the same
                            spot is Stop (v1.326.0). SendArrow subscribes to the box's text
                            (v1.250.0, S-05) so the page never re-renders per keystroke. */}
                        {awaiting || (chatBusy && stream.streaming) ? (
                          <button
                            type="button"
                            onClick={stop}
                            aria-label="Stop"
                            title="Stop this turn"
                            className="ml-auto grid h-[34px] w-[34px] shrink-0 place-items-center rounded-full bg-accent text-ink-950 transition-colors hover:bg-accent-soft focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
                          >
                            <Square size={12} fill="currentColor" aria-hidden />
                          </button>
                        ) : (
                          <SendArrow
                            store={composer}
                            busy={busy}
                            hasAttachments={attachments.length > 0}
                            onSend={send}
                          />
                        )}
                      </div>
                    </div>
                    {/* v1.250.0 (S-05): headless — Voice Chat's auto-send watches
                        the dictated text without the page watching it. */}
                    <VoiceAutoSend
                      store={composer}
                      armed={
                        voiceMode &&
                        !busy &&
                        !tts.speaking &&
                        inputFromVoiceRef.current &&
                        !dictation.interim &&
                        !dictation.processing &&
                        !dictation.error
                      }
                      delayMs={dictation.engine === "server" ? 350 : 1500}
                      onSend={send}
                    />
                  </div>
                  {/* Under the card, small and muted (v1.326.0): the conversation
                      map, the context gauge, a detected-model offer and the keys. The
                      old footer row (Share, Map, Approvals, Default model) is gone:
                      Share is in the top bar, the rest is in the card. */}
                  <div
                    data-testid="composer-meta"
                    className="flex min-h-7 flex-wrap items-center justify-center gap-x-4 gap-y-1 px-3 pt-1.5 text-[12px] text-zinc-500"
                  >
                    {/* v1.325.0: the conversation map — every question, one press away.
                        Calm chat W1-3: not on a new chat, where it could only be
                        a disabled button pointing at questions nobody asked. */}
                    {!emptyHero && (
                    <button
                      type="button"
                      data-testid="open-conversation-map"
                      onClick={() => setMapOpen((v) => !v)}
                      disabled={messages.filter((x) => x.role === "user" && !x.continuation).length < 2}
                      aria-expanded={mapOpen}
                      aria-label="Conversation map"
                      title="Jump to any question in this conversation"
                      className="inline-flex h-6 items-center gap-1.5 rounded-md px-1.5 text-[12px] text-zinc-500 transition-colors hover:bg-white/[0.04] hover:text-zinc-300 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent"
                    >
                      <ListTree size={13} />
                      <span>Map</span>
                    </button>
                    )}
                    {mapOpen && (
                      <div className="fixed bottom-24 right-4 z-40 w-[min(26rem,calc(100vw-2rem))]">
                        <ConversationMap
                          messages={messages}
                          onJump={jumpToMessage}
                          onClose={() => setMapOpen(false)}
                        />
                      </div>
                    )}
                    {/* Context headroom (v1.146.0). Deliberately quiet until it
                        matters: nobody needs a gauge at 12% of a 200k window, and
                        a permanent meter is the kind of chrome that gets ignored
                        exactly when it starts mattering. */}
                    <ContextMeter usage={contextUsage} />
                    {/* Redesign S9 (Q7): a detected model, one tap — never picked
                        silently. Silent once a real model answers, and absent
                        while the empty state's connect doors make the same offer
                        (one offer on screen at a time). */}
                    {messages.length > 0 && (
                      <ModelSuggestChip
                        onOther={() => {
                          setModelMenuOpen(true);
                          setModelSub(null);
                        }}
                      />
                    )}
                    <span className="hidden sm:inline">
                      {askInDock
                        ? dockAskKeyHint(dockAsks[0].kind)
                        : composerKeyHint(busy, !commMeta, atFilesRoot !== null)}
                    </span>
                  </div>
                  {/* Calm chat W1-3: under the card on a NEW chat, quietly. The
                      preflight warning, then (v1.310.0: the way forward comes
                      BEFORE the demo prompts, because every chip the offline demo
                      answers gets the same scripted sentence) the connect doors
                      while replies are a demo, then three suggestions and the
                      folded whole-job ideas. Each press only fills the box. */}
                  {emptyHero && (
                    <div data-testid="chat-hero-foot" className="flex flex-col items-center gap-4 px-1 sm:mt-3">
                      {preflightNote}
                      {showConnectDoors && (
                        // v1.314.0: the doors side by side from sm. No box of its
                        // own any more: the composer is the one card on screen.
                        <div className="w-full max-w-2xl">
                          <p className="mb-2 text-center text-[13px] text-zinc-400">
                            Connect a model for real answers
                          </p>
                          <ConnectDoors layout="row" />
                        </div>
                      )}
                      <NewChatSuggestions examples={examples} onPick={prefill} />
                    </div>
                  )}
                </div>
              </div>
              {/* Calm chat W1-3: the room UNDER the new-chat group (see the top
                  spacer). A little more than above, so the card sits slightly
                  above true centre. */}
              <div
                aria-hidden
                data-testid="chat-hero-spacer-bottom"
                className="min-h-0 shrink-0 basis-0 transition-[flex-grow] duration-300 ease-out motion-reduce:transition-none"
                style={{ flexGrow: emptyHero ? 1.4 : 0 }}
              />
            </section>
          </div>

          {/* Project panel (right): the context spine. Pick a project to scope
              threads, ground replies in its knowledge, and aim the workspace at
              its folder — or just browse to any folder for an ad-hoc workspace.
              The chosen folder rides along as workspace_dir so the chat's
              armed file tools write here and their output surfaces live below. */}
          {/* v1.326.0 (calm chat): a DRAWER opened from the top bar's
              Project button or its "⋯" menu, no longer a permanent column.
              Its contents are unchanged; ProjectDrawer owns the shell (the
              portal, Escape, focus in and back, the phone scrim, the resize
              grip). */}
          {workspaceOpen && (
            <ProjectDrawer
              width={railW}
              takeFocus={drawerTakeFocus}
              returnFocusTo={drawerReturnTarget}
              onClose={hideProjectPanel}
              onResizeStart={startRailDrag}
              onResizeReset={resetRailW}
            >
              {/* v1.329.0 (calm chat W4 F6): plain sections, not stacked
                  cards. A quiet label and a hairline per section, ghost
                  controls, Files / Knowledge as plain text tabs. Tasks /
                  Board / Media are not repeated here: the top bar holds them
                  (ProjectViewTabs). Parts in components/chat/ProjectPanelParts. */}
              <div data-testid="project-panel-body" className="flex min-h-0 flex-1 flex-col">
                <PanelSection
                  label="Project"
                  testId="project-panel-project"
                  right={
                    activeProject ? (
                      <Link
                        href={`/projects/${encodeURIComponent(activeProject.id)}`}
                        title="Open this project's own page: tasks, board, media and knowledge"
                        className={PANEL_GHOST}
                      >
                        Project page <ExternalLink size={12} />
                      </Link>
                    ) : null
                  }
                >
                  <select
                    aria-label="Project"
                    value={projectId ?? ""}
                    onChange={(e) => chooseProject(e.target.value)}
                    className={PANEL_SELECT}
                  >
                    <option value="">No project (plain chat)</option>
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
                      <p className="mt-1 text-[12px] leading-relaxed text-tone-warn">
                        The project folder is missing, so file tools stay off
                        until it is back. Fix it on the project page.
                      </p>
                    ) : (
                      <p className="mt-1 text-[12px] leading-relaxed text-zinc-500">
                        Replies use this project&apos;s instructions and
                        knowledge. Its chats and runs stay tagged to it.
                      </p>
                    ))}
                </PanelSection>
                {activeProject && (
                  <PanelTabs
                    label="Project panel views"
                    idPrefix="project-panel-tab"
                    panelId="project-panel-tabpanel"
                    tabs={PROJECT_PANEL_TABS}
                    value={railTab}
                    onSelect={setRailTab}
                  />
                )}
                <div
                  id="project-panel-tabpanel"
                  role={activeProject ? "tabpanel" : undefined}
                  aria-labelledby={activeProject ? `project-panel-tab-${railTab}` : undefined}
                  className="flex min-h-0 flex-1 flex-col pt-3"
                >
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
                    <KnowledgePanel projectId={activeProject.id} bare />
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
                  <div className="mb-3 shrink-0 border-b hairline pb-3">
                    <ArtifactsRail
                      bare
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
                <div className="flex min-h-0 flex-1 flex-col">
                {workspaceDir && !pickingFolder ? (
                  <>
                    {/* The folder, as a plain section. Its actions are ghost
                        buttons; the drawer's own Close replaces the old
                        "Collapse workspace" button. */}
                    <PanelSection
                      label="Folder"
                      testId="project-panel-folder"
                      className="border-b-0 pt-0"
                      right={
                        <>
                          {!projectId && (
                            <button
                              type="button"
                              onClick={() => void promoteFolderToProject()}
                              disabled={promoting}
                              title="Turn this folder into a project: chats here get tagged, grounded and gathered in one place"
                              aria-label="Make this folder a project"
                              className={PANEL_GHOST}
                            >
                              {promoting ? (
                                <Loader2 size={13} className="animate-spin" />
                              ) : (
                                <FolderKanban size={13} />
                              )}
                              Make project
                            </button>
                          )}
                          <button
                            type="button"
                            onClick={() => setPickingFolder(true)}
                            title="Change folder"
                            aria-label="Change workspace folder"
                            className={PANEL_GHOST}
                          >
                            <FolderPen size={13} /> Change
                          </button>
                          <button
                            type="button"
                            data-testid="project-panel-terminal"
                            onClick={() => void folderTerminal.open()}
                            disabled={folderTerminal.busy}
                            title="Open a new Build terminal in this folder"
                            aria-label="Open a terminal in this folder"
                            className={PANEL_GHOST}
                          >
                            {folderTerminal.busy ? (
                              <Loader2 size={13} className="animate-spin" />
                            ) : (
                              <SquareTerminal size={13} />
                            )}
                            Terminal
                          </button>
                        </>
                      }
                    >
                      <p className="text-[12px] text-zinc-500">
                        Files the chat&apos;s tools create land here.
                      </p>
                      {folderTerminal.error && (
                        <p role="alert" className="mt-1 text-[12px] text-tone-danger">
                          No terminal opened. {folderTerminal.error}
                        </p>
                      )}
                    </PanelSection>
                    {/* v1.329.0 (calm chat wave 5, G4): the CALM file list,
                        no card, file names in the normal font. Build keeps
                        its own card look. */}
                    <div className="min-h-0 flex-1">
                      <FilesPanel
                        variant="calm"
                        folder={workspaceDir}
                        onPreview={openDocPreview}
                      />
                    </div>
                  </>
                ) : (
                  // The folder picker is shared with Build (which keeps its
                  // card and its "Open terminal here"); here it is the CALM
                  // picker (v1.329.0: no card, no accent header, quiet
                  // sentence-case labels, hairline inputs, a ghost Go) and
                  // without that action, which never did anything from chat.
                  // Once a folder is picked the Folder section above offers
                  // the working Terminal button. With no folder yet, the
                  // drawer's own Close is the way out; while CHANGING a
                  // folder, "Back to files" cancels.
                  <div className="min-h-0 flex-1">
                    <DirectoryTree
                      variant="calm"
                      selectedPath={workspaceDir}
                      onSelect={chooseWorkspace}
                      hideAction
                      collapseLabel="Back to files"
                      onCollapse={
                        pickingFolder && workspaceDir ? () => setPickingFolder(false) : undefined
                      }
                    />
                  </div>
                )}
                </div>
                </>
                )}
                </div>
              </div>
            </ProjectDrawer>
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
      {/* v1.328.0 (calm chat W3-3): the chat is still working. The dialog
          lists what would stop; confirming re-sends the archive with
          {stop: true}, the same stop the Stop button sends. */}
      {archiveAsk && (
        <ArchiveChatDialog
          title={archiveAsk.title}
          running={archiveAsk.running}
          busy={archiveBusy}
          error={archiveError}
          onConfirm={(stop) => void archiveThread(archiveAsk.id, stop)}
          onClose={() => {
            setArchiveAsk(null);
            setArchiveError(null);
          }}
        />
      )}
    </PageShell>
  );
}
