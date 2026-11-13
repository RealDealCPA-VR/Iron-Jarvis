"use client";

/**
 * BUILD › CODE (v1.409.0): the REAL Claude Code or Codex, chosen by name and
 * driven from a GUI — Build's answer to the user's "an app like the chat's
 * Code view, but it runs the Claude or the Codex tool itself" (t3code was
 * the reference). The tool runs EVERYTHING itself (its tools, settings,
 * permission rules, its own saved session — `claude --resume` and
 * `codex resume` open the same conversation in a terminal); Iron Jarvis
 * starts it, draws what it does and answers its permission questions with
 * the user's own answer.
 *
 * LEFT: the sessions, grouped by folder; each row names its tool and the
 * account it runs on (a row the daemon found in the tool's own saved
 * sessions says so). v1.412.0: New session (and an empty stage) is the
 * composer at once — components/buildCode/StartComposer: type, and the chips
 * under it choose the folder (none by default; Windows' own picker), the tool,
 * the account (asked only when the tool has more than one login), the mode,
 * the model and the effort. Send makes the session and sends the words.
 *
 * CENTRE: the conversation (components/buildCode/CodeConversation), and the
 * composer — or, while the tool waits on the user, its question in the
 * composer's place (components/buildCode/CodeAskCard; the composer stays
 * mounted, hidden and inert, so a draft survives). Chips: tool · model,
 * effort, mode (Full access takes two presses and is sent with
 * `confirm: true`), account. `/` lists the tool's own commands, `@` the
 * folder's files. Stop interrupts the tool. ⋯ → Open in terminal, Rename,
 * Delete (two presses; v1.420.0, fix plan C4.10), and a search over the
 * list's titles and folders.
 *
 * v1.412.0 (the user: "I cant copy and past images into the build modules
 * </> code … the permission blocker does not allow change to auto-- allow
 * all, should be changeable mid session and the ability to steer work"):
 * PICTURES — paste, drop or the paperclip, through the chat's own upload
 * contract (components/harness/HarnessAttachments: POST /documents/upload,
 * pasted pictures renamed, thumbnails, a send waits for its uploads); a
 * picture alone is a message. THE MODE — the chip stays reachable while the
 * tool waits on a card (it sits beside the card), and the daemon starts every
 * Claude Code able to switch to Full access later. NOTES — Enter while it
 * works sends a NOTE (POST …/steer) through the tool's own way of taking one;
 * the note is drawn in order, and one the tool never read comes back to the
 * box with words.
 *
 * THE TURN IS IRON JARVIS'S (the durable-turn rule): closing the page never
 * stops it; opening a session whose turn still runs follows it again
 * (`GET …/turns/{turn_id}/stream`).
 *
 * Every read and write goes through lib/api's helpers; the streams through
 * lib/useChatStream's `streamSSE` (frames read by its `onFrame` tap).
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type MutableRefObject,
} from "react";
import {
  AlertTriangle,
  Archive,
  ArrowUp,
  ChevronDown,
  History,
  FolderOpen,
  Loader2,
  MoreHorizontal,
  Plus,
  Search,
  Server,
  ShieldAlert,
  Square,
  UserRound,
  X,
} from "lucide-react";
import { ApiError, del, get, patch, post } from "@/lib/api";
import { statusWords } from "@/lib/statusWords";
import { ConfirmButton } from "@/components/ui";
import { streamSSE } from "@/lib/useChatStream";
import { mintTurnId } from "@/lib/turnRejoin";
import { useVisibleInterval } from "@/lib/useVisibleInterval";
import { matchProjectEntries, useProjectFilesState, type ProjectEntry } from "@/lib/atMenuFiles";
import { StartComposer } from "@/components/buildCode/StartComposer";
import { SessionEditorProvider } from "@/components/harness/OpenInEditor";
import { scrollToLatest } from "@/lib/transcriptRepin";
import { HARNESS_COLUMN, HARNESS_COMPOSER_BOX } from "@/components/harness/harnessLook";
import { CodeConversation, type MessageActions } from "@/components/buildCode/CodeConversation";
import {
  BRANCH_ROW,
  BRANCH_ROW_TITLE,
  decodeBranch,
  decodeBranchAnswer,
  type BranchAnswer,
  type BranchFacts,
} from "@/lib/buildAppBranch";
import { CodePanes } from "@/components/buildCode/CodePanes";
import { WorkingSign } from "@/components/buildCode/WorkingSign";
import { ArchiveSummaryCard } from "@/components/buildCode/ArchiveSummary";
import {
  ARCHIVE_FAILED,
  ARCHIVE_ROW,
  ARCHIVE_ROW_TITLE,
  ARCHIVE_RUNNING_TITLE,
  archiveSummaryError,
  archiveSummaryFacts,
  type ArchiveSummaryFacts,
} from "@/lib/buildArchive";
import { CodeAskCard, type CodeAnswer } from "@/components/buildCode/CodeAskCard";
import { MENU_BUTTON_HOVER_FADE, useMenuClose } from "@/components/buildCode/useMenuClose";
import {
  AttachButton,
  AttachmentChips,
  pastedFiles,
  useHarnessAttachments,
  type HarnessFile,
} from "@/components/harness/HarnessAttachments";
import {
  OPEN_IN_TERMINAL,
  keepsLastTitle,
  ownSignInTitle,
  accountWords,
  appliedNextWords,
  defaultEffortWords,
  defaultModelWords,
  cutForRejoin,
  decodeDiskSessions,
  diskListPath,
  diskRowLine,
  firstMessageTitle,
  droppedWords,
  folderKey,
  isArchived,
  isFullAccess,
  modeLabel,
  reuseItems,
  decodeCodeEvent,
  decodeItemText,
  decodeCodeSession,
  decodeCodeSessionView,
  decodeCodeSessions,
  decodeTryChoices,
  effortsFor,
  decodeCodingTools,
  withNewerNotices,
  foldConversationFrom,
  type FoldMemo,
  groupByFolder,
  keepsLastOf,
  piFolderOf,
  piShellHint,
  PI_FOLDER_LOAD_LABEL,
  PI_FOLDER_SKIP_LABEL,
  type PiFolder,
  modeIsDangerous,
  sessionFolderWords,
  sessionRowLine,
  backgroundRereadMs,
  CODE_SEEN_BEAT_MS,
  COMMAND_SOURCE_WORDS,
  closeToolWords,
  decodeCommands,
  modelGroups,
  oneMillionLabel,
  slashMenuRows,
  signsInItself,
  sessionTitle,
  conversationMarkdown,
  EXPORT_NOTHING,
  EXPORT_NOT_READ,
  EXPORT_READING,
  exportGapWords,
  hasExportableItems,
  exportFileName,
  STEER_RETURNED_WORDS,
  toolChoiceWords,
  toolLabel,
  type CodeEvent,
  type CodeSession,
  type CodeSessionView,
  type CodeHelper,
  type CodingTool,
  type ConvItem,
  type DiskSession,
  type OpenAsk,
  type ToolCommand,
  type TryChoice,
} from "@/lib/buildApp";
import {
  LOAD_EARLIER,
  SLASH_ROWS,
  commandHints,
  collectAllEvents,
  decodeEventsPage,
  EXPORT_PAGE_LIMIT,
  decodeQueue,
  earlierStepsWords,
  effortWhileRunning,
  eventsPagePath,
  modelWhileRunning,
  nextMode,
  olderOf,
  pickCommand,
  prependEvents,
  queuedBackWords,
  queuedLine,
  slashAt,
  wordsBack,
  workingPlaceholder,
  type OlderInfo,
  type QueuedMessage,
} from "@/lib/buildAppHistory";
import { REWINDOW_AT, SHOW_EARLIER, hiddenMessages, hiddenWords, windowStart } from "@/lib/codeWindow";

/** The wire's own paths (the daemon's names; never shown). */
const API = "/build/app";
const SESSIONS = `${API}/sessions`;
const TOOLS = `${API}/harnesses`;
/** v1.429.0: how often (and how many times) the tool list is read again while
 *  a tool's model list is still being read (Pi answers in about a second). */
export const TOOLS_REREAD_MS = 2_000;
/** v1.475.0: opening the model picker reads the tools' lists again, at most
 *  this often (the page's first read counts). */
export const PICKER_REREAD_MS = 60_000;
export const TOOLS_REREAD_TRIES = 5;

/** How often the list is read again while it is on screen. */
export const CODE_LIST_REREAD_MS = 15_000;
/** How often a session whose running turn is not named is read again. */
export const CODE_RUNNING_REREAD_MS = 2_000;
/** While Iron Jarvis is not answering mid-reply: how often to look again
 *  (the reply is followed again once it is back). */
export const CODE_LOST_REREAD_MS = 3_000;
/** v1.422.0 review: while a limit card WAITS by itself (it carries the reply
 *  on at the reset, or on another account of the group, with nobody
 *  pressing anything), how often the open session is read again — so the
 *  carried-on reply shows here as it runs. */
export const CODE_WAIT_REREAD_MS = 15_000;
// v1.412.0: the mode chip lives in its own file (the start composer uses it
// too); its two-press timings are re-exported here, where tests read them.
export { FULL_ARM_GUARD_MS, FULL_DISARM_MS, ModeChip } from "@/components/buildCode/ModeChip";
import { ModelPicker } from "@/components/buildCode/ModelPicker";
import { ModeChip } from "@/components/buildCode/ModeChip";
import { decodeCarry, type CarryFacts } from "@/lib/buildCodeCarry";
import { LOOKING_BEAT_MS, noteLooking, stopLooking } from "@/lib/buildWatching";
import { JarvisToolsChip, ProfileShareOffer } from "@/components/buildCode/JarvisTools";
import { HelperLines } from "@/components/buildCode/HelperLines";
// v1.426.0 (Wave 6 lane B, E5): a folder's defaults for new sessions.
import { FolderDefaultsButton, FolderDefaultsPanel } from "@/components/folders/FolderDefaultsPanel";
import { toolDefaultsWords, type ToolDefaults } from "@/lib/folderDefaults";
/** "At the bottom" for the transcript, in pixels. */
const BOTTOM_SLACK_PX = 48;

/** One frame later (or as soon as possible where there are no frames). */
function nextFrame(fn: () => void): () => void {
  if (typeof window !== "undefined" && typeof window.requestAnimationFrame === "function") {
    const id = window.requestAnimationFrame(fn);
    return () => window.cancelAnimationFrame(id);
  }
  const id = setTimeout(fn, 16);
  return () => clearTimeout(id);
}

/** The bottom of a transcript, if the browser can scroll to it. */
function pinToBottom(sentinel: Element | null): void {
  if (sentinel && typeof (sentinel as HTMLElement).scrollIntoView === "function") scrollToLatest(sentinel);
}

/** Merge events that arrived into the list: the tool's echo of a question
 *  takes the place of the one drawn when it was sent; an ask settled twice
 *  (the stream's line and the page's own) is drawn once. */
export function applyEvents(prev: CodeEvent[], arrived: readonly CodeEvent[], optimistic: { current: CodeEvent | null }): CodeEvent[] {
  // v1.472.0: ONE copy of the list per frame (it was copied once per event
  // that arrived, so a burst of streamed words cost the list's length each).
  if (!arrived.length) return prev;
  const out = prev.slice();
  for (const ev of arrived) {
    if (ev.type === "resolved" && out.some((e) => e.type === "resolved" && e.requestId === ev.requestId)) continue;
    if (ev.type === "user" && optimistic.current) {
      const i = out.indexOf(optimistic.current);
      optimistic.current = null;
      if (i >= 0) {
        out[i] = ev;
        continue;
      }
    }
    out.push(ev);
  }
  return out;
}

const QUIET_BTN =
  "inline-flex h-7 items-center gap-1.5 rounded-lg px-2 text-[12px] text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60 disabled:cursor-not-allowed disabled:opacity-50";
const CHIP_SELECT =
  "h-7 max-w-[12rem] cursor-pointer truncate rounded-lg border-0 bg-transparent px-1.5 text-[12px] text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60 disabled:cursor-not-allowed disabled:opacity-50";

function errorWords(e: unknown, fallback: string): string {
  if (e instanceof ApiError && e.message) return e.message;
  if (e instanceof Error && e.message) return e.message;
  return fallback;
}

// ── one session ─────────────────────────────────────────────────────────────

type MenuRow = { kind: "command"; cmd: ToolCommand } | { kind: "file"; entry: ProjectEntry };

/** v1.439.0: the session row's menu item that saves the conversation. */
export const EXPORT_MARKDOWN = "Export as Markdown";

/** v1.439.0: the open session's events, for the export (a getter: always
 *  the newest the pane holds). */
export interface ExportSource {
  id: string;
  events: () => readonly CodeEvent[];
  /** v1.441.0 (review D1): what is older than what the pane holds (the
   *  export reads every page before it) and how many steps the record
   *  trimmed (gone; the file says so). */
  older: () => OlderInfo;
  dropped: () => number;
}

/** v1.439.0: hand the browser a text file to save. */
function downloadText(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/markdown;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/** v1.441.0: the same branch facts keep their object (the user's messages do
 *  not draw again on every re-read). */
function keepSameBranch(prev: BranchFacts | null, next: BranchFacts | null): BranchFacts | null {
  if (!prev || !next) return next;
  if (prev.fork !== next.fork || prev.forkAt !== next.forkAt || prev.rewind !== next.rewind) return next;
  if (prev.note !== next.note || prev.at.size !== next.at.size) return next;
  for (const a of next.at) if (!prev.at.has(a)) return next;
  return prev;
}

function SessionPane({
  sessionId,
  tools,
  onChanged,
  onOpenedTerminal,
  onArchived,
  onUnarchive,
  firstMessage = null,
  firstFiles = null,
  onFirstSent,
  exportSource,
  onBranched,
  initialDraft = null,
  onOpenSession,
  onPickerOpen,
}: {
  sessionId: string;
  tools: readonly CodingTool[];
  onChanged: (s: CodeSession) => void;
  onOpenedTerminal: (terminalId: string) => void;
  /** v1.453.0: with the daemon's words when the summary was not written. */
  onArchived: (id: string, note?: string) => void;
  /** v1.453.0: "Bring back" on an archived session's summary card. */
  onUnarchive?: (id: string) => Promise<void> | void;
  /** v1.412.0: the start composer's words — sent as this session's first
   *  message as soon as it is read (one press: make + send). */
  firstMessage?: string | null;
  /** v1.412.0: the start composer's files (pictures already uploaded). */
  firstFiles?: string[] | null;
  onFirstSent?: () => void;
  /** v1.439.0: where this pane leaves the events it holds, so the session
   *  row's "Export as Markdown" builds the file from them (no new read). */
  exportSource?: MutableRefObject<ExportSource | null>;
  /** v1.441.0: a branch was made from this session (the page opens it). */
  onBranched?: (a: BranchAnswer) => void;
  /** v1.441.0: a branch just made from another session — its message's words
   *  go in this box and the daemon's line says what was done. */
  initialDraft?: { text: string; note: string } | null;
  /** v1.474.0: open another Code session (a helper, or the one that started
   *  this helper). */
  onOpenSession?: (id: string) => void;
  /** v1.475.0: the model picker opened (the page may read the lists again). */
  onPickerOpen?: () => void;
}) {
  const [session, setSession] = useState<CodeSession | null>(null);
  /** v1.474.0 (Hand off): this session's helpers, and the session a helper
   *  belongs to. */
  const [helpers, setHelpers] = useState<CodeHelper[]>([]);
  const [handedFrom, setHandedFrom] = useState<CodeSessionView["handedFrom"]>(null);
  const [events, setEvents] = useState<CodeEvent[]>([]);
  const [extra, setExtra] = useState<CodeEvent[]>([]);
  const [running, setRunning] = useState(false);
  const [following, setFollowing] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [askError, setAskError] = useState<string | null>(null);
  const [text, setText] = useState("");
  /** v1.418.0 (C4.8): where the caret is in the box (the `/` menu reads it). */
  const [caret, setCaret] = useState(0);
  const [files, setFiles] = useState<string[]>([]);
  const [menuOpen, setMenuOpen] = useState(false);
  const [menuIndex, setMenuIndex] = useState(0);
  const [moreOpen, setMoreOpen] = useState(false);
  const [fetchedCommands, setFetchedCommands] = useState<ToolCommand[] | null>(null);
  const [viewCommands, setViewCommands] = useState<ToolCommand[] | null>(null);
  /** v1.411.0: the conversation keeps its last model and effort (Pi). */
  const [keepsLast, setKeepsLast] = useState(false);
  /** v1.431.0: a Pi folder's own extensions Pi skips here (one press loads
   *  them for this session), or the session already loads them. */
  const [piFolder, setPiFolder] = useState<PiFolder | null>(null);
  const [dropped, setDropped] = useState(0);
  /** v1.453.0: the summary kept from an archive (where it is), or null. */
  const [summaryFacts, setSummaryFacts] = useState<ArchiveSummaryFacts | null>(null);
  /** v1.418.0 (C4.6): steps loaded with "Load earlier" (oldest first), the
   *  cursor of the page before them, and how many are older still. */
  const [earlierEvents, setEarlierEvents] = useState<CodeEvent[]>([]);
  const [older, setOlder] = useState<OlderInfo>({ cursor: null, earlier: 0 });
  const [loadingEarlier, setLoadingEarlier] = useState(false);
  /** v1.418.0 (C4.8): messages waiting behind the running reply. */
  const [queued, setQueued] = useState<QueuedMessage[]>([]);
  const takingBackRef = useRef(false);
  const takeBackRef = useRef<(() => Promise<void>) | null>(null);
  const earlierCountRef = useRef(0);
  /** v1.418.0 (C4.8): the turns this window followed (a waiting message the
   *  daemon sent is a NEW turn: followed once, when the reply before it ends). */
  const followedTurnsRef = useRef<Set<string>>(new Set());
  earlierCountRef.current = earlierEvents.length;
  /** v1.418.0 (C4.8): each `/` command's argument hint. */
  const [hints, setHints] = useState<Record<string, string>>({});
  /** A quiet word that is not an error ("applies from your next message"). */
  const [info, setInfo] = useState<string | null>(null);
  const [continuing, setContinuing] = useState(false);
  const [tryChoices, setTryChoices] = useState<TryChoice[]>([]);
  /** v1.422.0 (B3, B5): a reply a restart cut off (Continue) and a limit's
   *  wait that carries on by itself (until when; Cancel). */
  const [carryFacts, setCarryFacts] = useState<CarryFacts>({ cut: null, wait: null, limitNote: "" });
  const [continuingCut, setContinuingCut] = useState(false);
  /** Iron Jarvis stopped answering while a reply ran: looked for again. */
  const [lost, setLost] = useState(false);
  /** v1.441.0: what this tool can branch or rewind from (null = an older
   *  Iron Jarvis: nothing offered). */
  const [branch, setBranch] = useState<BranchFacts | null>(null);
  const [branching, setBranching] = useState(false);
  /** The reader scrolled up while something new arrived. */
  const [jump, setJump] = useState(false);
  /** The reader is away from the end of the conversation (the "working"
   *  sign floats over the text then, and gets a pill behind it). */
  const [away, setAway] = useState(false);
  const atBottomRef = useRef(true);
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  /** Events that arrived and are not drawn yet (flushed once a frame). */
  const pendingRef = useRef<CodeEvent[]>([]);
  const cancelFrameRef = useRef<(() => void) | null>(null);
  /** Bumped by each send: a read started before it never undoes it. */
  const sendSeqRef = useRef(0);
  /** v1.417.0 review: bumped by each answer to a card. A read that was out
   *  when the user answered still holds the card as open (a helper asks while
   *  the view reads every few seconds): it never puts it back — the session
   *  is read again instead. */
  const answerSeqRef = useRef(0);
  const prevItemsRef = useRef<ConvItem[] | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const genRef = useRef(0);
  const optimisticRef = useRef<CodeEvent | null>(null);
  const answeredRef = useRef<Set<string>>(new Set());
  const boxRef = useRef<HTMLTextAreaElement | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  /** v1.412.0: pictures and files pasted, dropped or picked (uploaded). */
  const att = useHarnessAttachments();
  /** The box and its "@" picks, read when a send runs (a send held for an
   *  upload runs later, from an older render). */
  const textRef = useRef(text);
  textRef.current = text;
  const picksRef = useRef(files);
  picksRef.current = files;
  const runningRef = useRef(running);
  runningRef.current = running;
  /** The notes THIS window sent (only those come back to its box), and the
   *  ones already put back (never twice). */
  const notesMineRef = useRef<Map<string, { text: string; files: string[]; uploads: HarnessFile[] }>>(new Map());
  const notesBackRef = useRef<Set<string>>(new Set());
  /** v1.412.0 review: notes the stream said were RETURNED before this
   *  window had the note's own answer (the reply ended right after it went
   *  out) — put back as soon as the answer names them. */
  const returnedEarlyRef = useRef<Set<string>>(new Set());
  const [steering, setSteering] = useState(false);
  const attRestoreRef = useRef(att.restore);
  attRestoreRef.current = att.restore;
  const sessionToolRef = useRef("");
  const toolsRef = useRef(tools);
  toolsRef.current = tools;

  const tool = useMemo(() => tools.find((t) => t.id === session?.tool) ?? null, [tools, session?.tool]);
  sessionToolRef.current = session?.tool ?? "";
  const toolName = toolLabel(session?.tool ?? "", tools);
  // Rows whose item did not change keep their object (they do not draw
  // again; the v1.250.0 rule).
  // v1.418.0 (C4.6): the steps loaded earlier come first.
  const allEvents = useMemo(() => (earlierEvents.length ? [...earlierEvents, ...events] : events), [earlierEvents, events]);
  const allEventsRef = useRef(allEvents);
  allEventsRef.current = allEvents;
  const olderRef = useRef(older);
  olderRef.current = older;
  const droppedRef = useRef(dropped);
  droppedRef.current = dropped;
  // v1.441.0 (review D2): offered only once THIS session was read (before the
  // first read, or after a failed one, the pane holds nothing to export).
  const exportReady = session?.id === sessionId;
  useEffect(() => {
    if (!exportSource || !exportReady) return;
    const src: ExportSource = {
      id: sessionId,
      events: () => allEventsRef.current,
      older: () => olderRef.current,
      dropped: () => droppedRef.current,
    };
    exportSource.current = src;
    return () => {
      if (exportSource.current === src) exportSource.current = null;
    };
  }, [exportSource, sessionId, exportReady]);
  // v1.472.0: only the events that arrived since the last frame are folded
  // (lib/buildApp foldConversationFrom); a list that is not the old one plus
  // more folds whole.
  const foldMemoRef = useRef<FoldMemo | null>(null);
  const conv = useMemo(() => {
    const folded = foldConversationFrom(foldMemoRef.current, allEvents, extra);
    foldMemoRef.current = folded.memo;
    const c = folded.conversation;
    const items = reuseItems(prevItemsRef.current, c.items);
    prevItemsRef.current = items;
    return { ...c, items };
  }, [allEvents, extra]);
  // Full access by the session's own mode id, whatever the tool list says.
  const dangerous = isFullAccess(session?.mode ?? "", tool);

  /** Draw what arrived, now (a stream's end, before reading the record). */
  const flushNow = useCallback(() => {
    cancelFrameRef.current?.();
    cancelFrameRef.current = null;
    const arrived = pendingRef.current;
    if (!arrived.length) return;
    pendingRef.current = [];
    setEvents((prev) => applyEvents(prev, arrived, optimisticRef));
  }, []);

  /** v1.412.0: a note this window sent that the tool never read: its words
   *  and files go back in the box (beside anything typed since), said once. */
  const giveNoteBack = useCallback((id: string) => {
    const note = notesMineRef.current.get(id);
    if (!note) {
      returnedEarlyRef.current.add(id);
      return;
    }
    if (notesBackRef.current.has(id)) return;
    notesBackRef.current.add(id);
    setText((cur) => (cur.trim() ? `${cur.replace(/\s+$/, "")}\n${note.text}` : note.text));
    if (note.files.length) setFiles((cur) => [...cur, ...note.files.filter((f) => !cur.includes(f))]);
    if (note.uploads.length) attRestoreRef.current(note.uploads);
    setInfo(STEER_RETURNED_WORDS(toolLabel(sessionToolRef.current, toolsRef.current)));
  }, []);

  /** An event arrived: drawn with the others of this frame (at most one
   *  redraw a frame, however fast the words stream). */
  const append = useCallback(
    (gen: number, ev: CodeEvent) => {
      if (gen !== genRef.current) return;
      if (ev.type === "resolved" && answeredRef.current.has(ev.requestId)) return;
      if (ev.type === "steer" && ev.state === "returned") giveNoteBack(ev.id);
      pendingRef.current.push(ev);
      if (!cancelFrameRef.current) {
        cancelFrameRef.current = nextFrame(() => {
          cancelFrameRef.current = null;
          flushNow();
        });
      }
    },
    [flushNow],
  );

  /** Read the session from the daemon; follow its running turn. */
  const load = useCallback(
    async (opts: { follow: boolean }) => {
      const gen = genRef.current;
      const seq = sendSeqRef.current;
      const aseq = answerSeqRef.current;
      try {
        const raw = await get<unknown>(`${SESSIONS}/${encodeURIComponent(sessionId)}`);
        if (gen !== genRef.current) return;
        // A message sent while this read was out: the read is older than
        // what is on screen, so it decides nothing about the reply.
        if (seq !== sendSeqRef.current) return;
        // A card answered while it was out: read again (this one would put
        // the answered card back).
        if (aseq !== answerSeqRef.current) {
          void load(opts);
          return;
        }
        setLost(false);
        const view = decodeCodeSessionView(raw);
        if (!view) {
          setLoadError("This session could not be read.");
          return;
        }
        setLoadError(null);
        setSession(view.session);
        onChanged(view.session);
        setExtra(view.pending);
        setDropped(view.dropped);
        setSummaryFacts(archiveSummaryFacts(raw));
        if (view.commands) setViewCommands(view.commands);
        setKeepsLast(view.keepsLast);
        setPiFolder(view.piFolder);
        setHelpers(view.helpers);
        setHandedFrom(view.handedFrom);
        setBranch((prev) => keepSameBranch(prev, decodeBranch(raw)));
        // v1.422.0 (B3, B5): what carries on by itself, and what is offered.
        setCarryFacts(decodeCarry(raw));
        // v1.418.0 (C4.8): argument hints, and the messages waiting behind
        // the reply (held ones go back to the box).
        setHints(commandHints((raw as { commands?: unknown } | null)?.commands));
        const q = decodeQueue(raw);
        if (q) {
          setQueued(q.queued);
          if (q.held && q.queued.length) void takeBackRef.current?.();
        }
        // v1.418.0 (C4.6): what is older than this read ("Load earlier").
        if (opts.follow) {
          setEarlierEvents([]);
          setOlder(olderOf(raw, view.events.length));
        } else if (!earlierCountRef.current) {
          setOlder(olderOf(raw, view.events.length));
        }
        // v1.418.0 (C4.8): a reply that ended and a WAITING message the
        // daemon sent as the next one: that one is followed too.
        // Only a turn this window has not followed yet (a record that still
        // names the turn that just ended is left to the re-read below —
        // never a follow loop).
        const nextQueued =
          !opts.follow &&
          !abortRef.current &&
          view.running &&
          !!view.runningTurn &&
          !followedTurnsRef.current.has(view.runningTurn);
        if (view.running && view.runningTurn && (opts.follow || nextQueued)) {
          followedTurnsRef.current.add(view.runningTurn);
          const { base, carry } = cutForRejoin(view.events, view.runningTurn);
          setEvents((prev) => (nextQueued && prev.length > base.length ? prev : base));
          setRunning(true);
          void follow(gen, `${SESSIONS}/${encodeURIComponent(sessionId)}/turns/${encodeURIComponent(view.runningTurn)}/stream`, undefined, carry);
        } else if (opts.follow) {
          setEvents(view.events);
          setRunning(view.running);
        } else {
          // After a turn ends: the saved record replaces what was drawn —
          // unless it holds less (not written yet), which would blank the
          // reply the user just watched.
          // v1.481.1: what is kept still gets a notice newer than anything
          // drawn (a model change's line, read again after the change: the
          // drawn reply's many small pieces outnumber the saved record).
          setEvents((prev) => (view.events.length >= prev.length ? view.events : withNewerNotices(prev, view.events)));
          setRunning(view.running);
        }
      } catch (e) {
        if (gen !== genRef.current) return;
        // Still not answering: keep looking (the lost-reply re-read).
        if (lost) return;
        setLoadError(errorWords(e, "This session could not be read."));
      }
    },
    // `follow` is stable (declared below with useCallback and no deps).
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [sessionId, onChanged, lost],
  );

  /** Follow one turn's events (a new turn: `body`; a rejoin: none).
   *  `onRefused`: a new turn the daemon refused before any frame (409, any
   *  status of 400 or more) — the caller puts the message back. */
  const follow = useCallback(
    async (
      gen: number,
      path: string,
      body: unknown,
      carry: CodeEvent | null = null,
      onRefused?: (words: string) => void,
    ) => {
      abortRef.current?.abort();
      const ctl = new AbortController();
      abortRef.current = ctl;
      setFollowing(true);
      let first = true;
      let refused = false;
      // A frame named "error" is also yielded by the generator (decoded for
      // the chat); only an error with no frame before it is the TRANSPORT's.
      let tappedError = false;
      try {
        for await (const ev of streamSSE(path, body, ctl.signal, {
          onFrame: (name, data) => {
            tappedError = name === "error";
            const decoded = decodeCodeEvent(data, name);
            if (!decoded) return;
            if (first && carry) {
              if (decoded.type !== "user") append(gen, carry);
            }
            first = false;
            append(gen, decoded);
          },
        })) {
          if (ev.type !== "error") continue;
          if (tappedError) {
            tappedError = false;
            continue;
          }
          if (gen !== genRef.current) continue;
          const status = typeof ev.status === "number" ? ev.status : 0;
          if (first && status >= 400 && onRefused) {
            onRefused(ev.title || ev.detail || "That message was not sent.");
            refused = true;
            continue;
          }
          if (ev.offline) {
            // Iron Jarvis went away mid-reply: the reply goes on there; it is
            // followed again once Iron Jarvis answers.
            setLost(true);
            continue;
          }
          {
            append(gen, {
              type: "error",
              at: "",
              title: ev.title || ev.detail || "The reply could not be followed.",
              fix: ev.fix ?? "",
              technical: ev.technical ?? "",
              fixKind: ev.fix_kind ?? "",
              account: "",
            });
          }
        }
      } finally {
        if (abortRef.current === ctl) abortRef.current = null;
        if (gen === genRef.current) {
          flushNow();
          setFollowing(false);
          setRunning(false);
          // The saved record is the truth (titles, trimmed frames): read it
          // again once the turn is over, unless the user left this session
          // or the message never went.
          if (!ctl.signal.aborted && !refused) void load({ follow: false });
        }
      }
    },
    [append, load, flushNow],
  );

  // A new session id: start over.
  useEffect(() => {
    genRef.current += 1;
    abortRef.current?.abort();
    optimisticRef.current = null;
    answeredRef.current = new Set();
    setSession(null);
    setEvents([]);
    setExtra([]);
    setRunning(false);
    setText("");
    setFiles([]);
    att.clear();
    setNote(null);
    setAskError(null);
    setFetchedCommands(null);
    setViewCommands(null);
    setKeepsLast(false);
    setDropped(0);
    setInfo(null);
    setEarlierEvents([]);
    setOlder({ cursor: null, earlier: 0 });
    setQueued([]);
    setHints({});
    setCarryFacts({ cut: null, wait: null, limitNote: "" });
    followedTurnsRef.current = new Set();
    void load({ follow: true });
    return () => {
      genRef.current += 1;
      abortRef.current?.abort();
      cancelFrameRef.current?.();
      cancelFrameRef.current = null;
      pendingRef.current = [];
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  // A turn that runs but was not named (an older daemon): read again.
  useVisibleInterval(() => void load({ follow: true }), CODE_RUNNING_REREAD_MS, running && !following && !lost);
  // Iron Jarvis stopped answering mid-reply: look again until it is back,
  // then follow the reply again (the read rejoins a running turn).
  useVisibleInterval(() => void load({ follow: true }), CODE_LOST_REREAD_MS, lost && !following);
  // v1.422.0 review: a limit card that waits by itself carries the reply on
  // with nobody pressing anything — read the session again while it waits
  // (one read at a time), so the carried-on reply is followed here.
  const waitReadOutRef = useRef(false);
  useVisibleInterval(
    () => {
      if (waitReadOutRef.current) return;
      waitReadOutRef.current = true;
      void load({ follow: true }).finally(() => {
        waitReadOutRef.current = false;
      });
    },
    CODE_WAIT_REREAD_MS,
    carryFacts.wait !== null && !running && !following && !lost,
  );
  // v1.417.0 (a USER BUG: "it seems to be getting stuck"): the reply ended
  // while helpers work on in the background; when they finish, the tool
  // writes a report of its own that no message owns. Read the session again
  // meanwhile (only while the window is seen) so that report appears as it
  // is written — and stop once nothing is happening.
  const bgRereadMs = backgroundRereadMs(session, running);
  // v1.417.0 review: one such read at a time — a slow answer is never
  // stacked under the next tick (nor overtaken by it, which would draw an
  // older record over a newer one).
  const bgReadOutRef = useRef(false);
  useVisibleInterval(
    () => {
      if (bgReadOutRef.current) return;
      bgReadOutRef.current = true;
      void load({ follow: true }).finally(() => {
        bgReadOutRef.current = false;
      });
    },
    bgRereadMs,
    bgRereadMs > 0 && !lost,
  );
  // The read that said "nothing is happening" any more: one more read, so
  // the end of the report is on screen (never on a send, a switch or a lost
  // daemon — those read for themselves).
  const bgWatchRef = useRef<{ sid: string; on: boolean }>({ sid: "", on: false });
  const bgOn = bgRereadMs > 0;
  useEffect(() => {
    const prev = bgWatchRef.current;
    bgWatchRef.current = { sid: sessionId, on: bgOn };
    if (prev.on && !bgOn && prev.sid === sessionId && session && !runningRef.current && !lost) {
      void load({ follow: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bgOn, sessionId]);

  // v1.422.0 (C4.7): while this session is on screen (the window seen), say
  // so to Iron Jarvis once a minute: the session in front is closed after 60
  // minutes with nothing to do instead of 15. Never on mount (the first read
  // already says it); a hidden window stops beating, and within minutes the
  // session is back on the 15-minute rule. Errors change nothing.
  useVisibleInterval(() => {
    post(`${SESSIONS}/${encodeURIComponent(sessionId)}/seen`).catch(() => undefined);
  }, CODE_SEEN_BEAT_MS);

  // The newest stays in view only for a reader who is AT the bottom (the
  // chat's rule); one who scrolled up is left where they are, with "Jump to
  // latest".
  const onScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const at = el.scrollHeight - el.scrollTop - el.clientHeight <= BOTTOM_SLACK_PX;
    atBottomRef.current = at;
    setAway(!at);
    if (at) setJump(false);
  }, []);
  useEffect(() => {
    if (atBottomRef.current) pinToBottom(sentinelRef.current);
    else setJump(true);
  }, [conv.items]);
  const jumpToLatest = useCallback(() => {
    atBottomRef.current = true;
    setAway(false);
    setJump(false);
    pinToBottom(sentinelRef.current);
  }, []);

  // v1.473.0: only the newest rows are drawn (lib/codeWindow); a long
  // session's earlier rows wait behind "Show earlier". The first row drawn is
  // decided once a session's rows are there, kept while the reader reads
  // (rows that stream in are added below it), and cut back to one window when
  // the drawn part has grown long and the reader is at the bottom.
  const [drawn, setDrawn] = useState<{ session: string; from: number } | null>(null);
  const convItemsRef = useRef(conv.items);
  convItemsRef.current = conv.items;
  const drawFrom = drawn && drawn.session === sessionId
    ? Math.min(drawn.from, conv.items.length)
    : windowStart(conv.items);
  const drawnRef = useRef(drawn);
  drawnRef.current = drawn;
  const rowsSeenRef = useRef(0);
  useEffect(() => {
    if (!conv.items.length) return;
    if (!drawn || drawn.session !== sessionId) setDrawn({ session: sessionId, from: windowStart(conv.items) });
  }, [conv.items, sessionId, drawn]);
  useEffect(() => {
    // Only rows that ARRIVED cut the drawn part back (never a "Show
    // earlier" press), and only for a reader at the bottom.
    const grew = conv.items.length > rowsSeenRef.current;
    rowsSeenRef.current = conv.items.length;
    const d = drawnRef.current;
    if (!grew || !d || d.session !== sessionId || !atBottomRef.current) return;
    if (conv.items.length - d.from > REWINDOW_AT) {
      setDrawn({ session: sessionId, from: windowStart(conv.items) });
      nextFrame(() => pinToBottom(sentinelRef.current));
    }
  }, [conv.items, sessionId]);
  const foldedMessages = useMemo(() => hiddenMessages(conv.items, drawFrom), [conv.items, drawFrom]);
  /** Draw the window before the first row drawn; the reader stays where
   *  they were (the rows grow above them). */
  const showEarlier = useCallback(() => {
    const el = scrollRef.current;
    const fromBottom = el ? el.scrollHeight - el.scrollTop : 0;
    setDrawn((d) => ({
      session: sessionId,
      from: windowStart(convItemsRef.current, d && d.session === sessionId ? d.from : 0),
    }));
    nextFrame(() => {
      const box = scrollRef.current;
      if (box) box.scrollTop = Math.max(0, box.scrollHeight - fromBottom);
    });
  }, [sessionId]);

  /** v1.412.0: a note for the reply that is running (Enter while it works):
   *  through the tool's own way of taking one. No reply running after all
   *  (409 `not_working`) → it goes as a message. */
  const steer = useCallback(async () => {
    if (!session || steering) return;
    const words = textRef.current.trim();
    const uploads: HarnessFile[] = att.filesRef.current.slice();
    const picks = picksRef.current.slice();
    const all = [...uploads.map((f) => f.path), ...picks];
    if (!words && !all.length) return;
    setSteering(true);
    setNote(null);
    setText("");
    setFiles([]);
    att.clear();
    try {
      const res = await post<{ id?: string; state?: string }>(`${SESSIONS}/${encodeURIComponent(session.id)}/steer`, {
        text: words,
        ...(all.length ? { files: all } : {}),
      });
      const id = typeof res?.id === "string" ? res.id : "";
      if (id) {
        notesMineRef.current.set(id, { text: words, files: picks, uploads });
        // It ended while the note went out: nothing reached the tool.
        if (res?.state === "returned" || returnedEarlyRef.current.has(id)) giveNoteBack(id);
      }
    } catch (e) {
      const body = e instanceof ApiError ? (e as ApiError & { body?: unknown }).body : null;
      const code = body && typeof body === "object" ? (body as { code?: unknown }).code : undefined;
      if (code === "not_working") {
        // The reply had ended: the words (and files) go as a message now —
        // a refused message puts them back in the box itself.
        setRunning(false);
        runningRef.current = false;
        void sendRef.current?.(words, all);
        return;
      }
      setText((cur) => (cur.trim() ? cur : words));
      setFiles((cur) => (cur.length ? cur : picks));
      att.restore(uploads);
      setNote(errorWords(e, "Your note did not reach the coding tool."));
    } finally {
      setSteering(false);
    }
  }, [session, steering, att, giveNoteBack]);

  /** v1.418.0 (C4.8): the waiting messages taken back into the box (the
   *  reply ended with Stop, a failure or an account's limit card — nothing
   *  is sent behind the user's back). Once at a time. */
  const takeBack = useCallback(async () => {
    if (takingBackRef.current) return;
    takingBackRef.current = true;
    try {
      const raw = await post<{ taken?: unknown }>(`${SESSIONS}/${encodeURIComponent(sessionId)}/queue/take`);
      const items = decodeQueue({ queued: raw?.taken })?.queued ?? [];
      setQueued([]);
      if (!items.length) return;
      setText((cur) => wordsBack(cur, items));
      const back = items.flatMap((i) => i.files);
      if (back.length) setFiles((cur) => [...cur, ...back.filter((f) => !cur.includes(f))]);
      setInfo(queuedBackWords(toolLabel(sessionToolRef.current, toolsRef.current), items.length));
    } catch (e) {
      setNote(errorWords(e, "Your waiting messages could not be read back."));
    } finally {
      takingBackRef.current = false;
    }
  }, [sessionId]);
  takeBackRef.current = takeBack;
  const steerRef = useRef(steer);
  steerRef.current = steer;
  /** A note for the running reply (Shift+Enter, or its arrow) — after the
   *  pictures still going up (a note never goes without them). */
  const sendNote = useCallback(() => {
    if (att.busyRef.current) att.whenSettled(() => void steerRef.current());
    else void steerRef.current();
  }, [att]);

  /** v1.418.0 (C4.8): Enter while it works QUEUES the message: it goes as
   *  the next message when this reply ends cleanly. No reply running after
   *  all (409 `not_working`) → it goes as a message now. */
  const queueIt = useCallback(async () => {
    if (!session) return;
    const words = textRef.current.trim();
    const uploads: HarnessFile[] = att.filesRef.current.slice();
    const picks = picksRef.current.slice();
    const all = [...uploads.map((f) => f.path), ...picks];
    if (!words && !all.length) return;
    setNote(null);
    setText("");
    setFiles([]);
    att.clear();
    try {
      const raw = await post<unknown>(`${SESSIONS}/${encodeURIComponent(session.id)}/queue`, {
        text: words,
        ...(all.length ? { files: all } : {}),
      });
      const q = decodeQueue(raw);
      if (q) {
        setQueued(q.queued);
        if (q.held && q.queued.length) void takeBackRef.current?.();
      }
    } catch (e) {
      const body = e instanceof ApiError ? (e as ApiError & { body?: unknown }).body : null;
      const code = body && typeof body === "object" ? (body as { code?: unknown }).code : undefined;
      if (code === "not_working") {
        setRunning(false);
        runningRef.current = false;
        void sendRef.current?.(words, all);
        return;
      }
      setText((cur) => (cur.trim() ? cur : words));
      setFiles((cur) => (cur.length ? cur : picks));
      att.restore(uploads);
      setNote(errorWords(e, "Your message could not wait for the reply — send it again once it ends."));
    }
  }, [session, att]);

  /** v1.418.0 (C4.8): a waiting message taken off the queue (its ×). */
  const removeQueued = useCallback(
    async (id: string) => {
      if (!session) return;
      setQueued((cur) => cur.filter((q) => q.id !== id));
      try {
        const raw = await post<unknown>(`${SESSIONS}/${encodeURIComponent(session.id)}/queue/${encodeURIComponent(id)}/remove`);
        const q = decodeQueue(raw);
        if (q) setQueued(q.queued);
      } catch (e) {
        if (!(e instanceof ApiError && e.status === 404)) setNote(errorWords(e, "That waiting message was not removed."));
      }
    },
    [session],
  );

  /** v1.418.0 (C4.4, wired here): the whole text the record keeps for one
   *  step (`GET …/events/{item}`); null when it cannot be read. */
  const expandStep = useCallback(
    async (item: string) => {
      try {
        return decodeItemText(await get<unknown>(`${SESSIONS}/${encodeURIComponent(sessionId)}/events/${encodeURIComponent(item)}`));
      } catch {
        return null;
      }
    },
    [sessionId],
  );

  /** v1.418.0 (C4.6): the page of steps before the oldest one shown. The
   *  reader stays where they were (the new rows grow above them). */
  const loadEarlier = useCallback(async () => {
    if (!session || !older.cursor || loadingEarlier) return;
    setLoadingEarlier(true);
    const gen = genRef.current;
    const el = scrollRef.current;
    const fromBottom = el ? el.scrollHeight - el.scrollTop : 0;
    try {
      const raw = await get<unknown>(eventsPagePath(SESSIONS, session.id, older.cursor));
      if (gen !== genRef.current) return;
      const page = decodeEventsPage(raw);
      setEarlierEvents((prev) => prependEvents(page.events, prev));
      setOlder({ cursor: page.older, earlier: page.earlier });
      nextFrame(() => {
        const box = scrollRef.current;
        if (box) box.scrollTop = Math.max(0, box.scrollHeight - fromBottom);
      });
    } catch (e) {
      if (gen === genRef.current) setNote(errorWords(e, "The earlier steps could not be loaded."));
    } finally {
      if (gen === genRef.current) setLoadingEarlier(false);
    }
  }, [session, older.cursor, loadingEarlier]);

  const send = useCallback(async (handed?: string, handedFiles?: string[]) => {
    if (!session) return;
    // v1.412.0: a send waits for the pictures still going up (never goes
    // without them — the v1.275.0 rule); it reads the box when it runs.
    if (handed === undefined && att.busyRef.current) {
      att.whenSettled(() => void sendRef.current?.());
      return;
    }
    if (handed === undefined && runningRef.current) {
      // v1.418.0 (C4.8): it WAITS for the reply (a note is Shift+Enter).
      void queueIt();
      return;
    }
    const typedNow = handed ?? textRef.current;
    const words = typedNow.trim();
    const uploads: HarnessFile[] = handed === undefined ? att.filesRef.current.slice() : [];
    const picks = handed === undefined ? picksRef.current.slice() : [];
    const sent = handed === undefined ? [...uploads.map((f) => f.path), ...picks] : (handedFiles ?? []);
    if ((!words && !sent.length) || runningRef.current) return;
    const gen = genRef.current;
    const turnId = mintTurnId();
    followedTurnsRef.current.add(turnId); // v1.418.0 (C4.8)
    sendSeqRef.current += 1;
    const mine: CodeEvent = { type: "user", at: new Date().toISOString(), text: words, files: sent };
    optimisticRef.current = mine;
    setEvents((prev) => [...prev, mine]);
    const typed = typedNow;
    // The daemon titles an untitled session by its first message (the
    // chat's rule); the list says so at once, before its next read.
    if (!session.title.trim()) {
      const echo = firstMessageTitle(words);
      if (echo) onChanged({ ...session, title: echo });
    }
    if (handed === undefined) {
      setText("");
      setFiles([]);
      att.clear();
    }
    setNote(null);
    setRunning(true);
    runningRef.current = true;
    // v1.412.0: a session with no folder gets its own folder with this first
    // message — its header names it as soon as the daemon has made it.
    if (session.noFolder && !session.folder) {
      const sid = session.id;
      setTimeout(() => {
        get<unknown>(`${SESSIONS}/${encodeURIComponent(sid)}?limit=1`)
          .then((raw) => {
            const v = decodeCodeSessionView(raw);
            if (v && v.session.folder) {
              setSession((cur) => (cur && cur.id === sid ? { ...cur, folder: v.session.folder } : cur));
            }
          })
          .catch(() => undefined);
      }, 800);
    }
    // Sending shows the newest again.
    atBottomRef.current = true;
    setJump(false);
    await follow(
      gen,
      `${SESSIONS}/${encodeURIComponent(session.id)}/turn`,
      { text: words, turn_id: turnId, ...(sent.length ? { files: sent } : {}) },
      null,
      (words2) => {
        // Refused before anything ran: no bubble for a message that never
        // went; the words and files go back in the box, with the reason.
        optimisticRef.current = null;
        setEvents((prev) => prev.filter((e) => e !== mine));
        setText((cur) => (cur.trim() ? cur : typed));
        // A handed message's files come back by path (they went up already).
        setFiles((cur) => (cur.length ? cur : handed === undefined ? picks : (handedFiles ?? [])));
        att.restore(uploads);
        setNote(words2);
      },
    );
  }, [session, follow, onChanged, att, queueIt]);
  const sendRef = useRef<typeof send | null>(null);
  sendRef.current = send;
  // v1.472.0: the panes read only the newest message with files from the
  // conversation, so they are handed that one event (the same list while it
  // does not change) and a send that never changes: a streamed word no
  // longer draws the panes again.
  const sendFromPanes = useCallback((t: string) => void sendRef.current?.(t), []);
  const paneEventsRef = useRef<readonly CodeEvent[]>([]);
  const paneEvents = useMemo(() => {
    let found: CodeEvent | null = null;
    for (let i = allEvents.length - 1; i >= 0; i -= 1) {
      const e = allEvents[i];
      if ((e.type === "user" || (e.type === "steer" && e.state === "sent")) && e.files.length) {
        found = e;
        break;
      }
    }
    const prev = paneEventsRef.current;
    const next: readonly CodeEvent[] = found ? (prev[0] === found ? prev : [found]) : prev.length ? [] : prev;
    paneEventsRef.current = next;
    return next;
  }, [allEvents]);

  // v1.412.0: the start composer's words go as the first message, once.
  const firstRef = useRef<string | null>(firstMessage);
  const firstFilesRef = useRef<string[]>(firstFiles ?? []);
  useEffect(() => {
    const words = firstRef.current;
    const withFiles = firstFilesRef.current;
    if ((words === null || (!words && !withFiles.length)) || !session || running) return;
    firstRef.current = null;
    firstFilesRef.current = [];
    onFirstSent?.();
    void send(words, withFiles);
  }, [session, running, send, onFirstSent]);

  const stop = useCallback(() => {
    if (!session) return;
    post(`${SESSIONS}/${encodeURIComponent(session.id)}/interrupt`).catch((e) =>
      setNote(errorWords(e, "Stop did not reach the coding tool.")),
    );
  }, [session]);

  // v1.422.0 (B4): the user's own "Close it now" for a session kept open
  // because its tool runs a program (two presses: the program stops too).
  const closeTool = useCallback(async () => {
    if (!session) return;
    try {
      await post(`${SESSIONS}/${encodeURIComponent(session.id)}/close`);
      setNote(null);
      void load({ follow: false });
    } catch (e) {
      setNote(errorWords(e, `${toolName} could not be closed.`));
    }
  }, [session, toolName, load]);

  const answer = useCallback(
    async (ask: OpenAsk, a: CodeAnswer) => {
      if (!session) return;
      setAskError(null);
      try {
        await post(`${SESSIONS}/${encodeURIComponent(session.id)}/answer`, {
          request_id: ask.requestId,
          decision: a.decision,
          ...(a.message ? { message: a.message } : {}),
          ...(a.answers ? { answers: a.answers } : {}),
        });
        answeredRef.current.add(ask.requestId);
        answerSeqRef.current += 1;
        // The stream's own line may have landed first: one line, never two.
        setEvents((prev) =>
          prev.some((e) => e.type === "resolved" && e.requestId === ask.requestId)
            ? prev
            : [
                ...prev,
                {
                  type: "resolved",
                  at: new Date().toISOString(),
                  requestId: ask.requestId,
                  decision: ask.type === "question" ? (a.decision === "deny" ? "deny" : "answered") : a.decision,
                },
              ],
        );
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) {
          // Answered elsewhere, or the tool stopped asking.
          answeredRef.current.add(ask.requestId);
          answerSeqRef.current += 1;
          setEvents((prev) =>
            prev.some((e) => e.type === "resolved" && e.requestId === ask.requestId)
              ? prev
              : [...prev, { type: "resolved", at: "", requestId: ask.requestId, decision: "cancelled" }],
          );
          return;
        }
        setAskError(errorWords(e, "The answer did not reach the coding tool. Try again."));
      }
    },
    [session],
  );

  /** v1.414.0: a session the daemon answered with (the Iron Jarvis tools
   *  chip's change). */
  const adoptSession = useCallback(
    (next: CodeSession) => {
      setSession(next);
      onChanged(next);
    },
    [onChanged],
  );

  const change = useCallback(
    async (patchIn: Record<string, unknown>) => {
      if (!session) return;
      setNote(null);
      setInfo(null);
      try {
        const raw = await patch<unknown>(`${SESSIONS}/${encodeURIComponent(session.id)}`, patchIn);
        // v1.475.0: a model or effort change in a conversation is said by the
        // daemon's own line in it (read again below), so no second note here.
        // v1.481.1: while a reply runs the line comes with the reply's own
        // stream — said once then too.
        const onlyModel = Object.keys(patchIn).every((k) => k === "model" || k === "effort");
        const saidInConversation = onlyModel && (!!session.toolSessionId || running);
        const later = appliedNextWords(raw);
        if (later && !saidInConversation) setInfo(later);
        const keeps = keepsLastOf(raw);
        if (keeps !== null) setKeepsLast(keeps);
        const folder = piFolderOf(raw);
        if (folder !== undefined) setPiFolder(folder);
        const next = decodeCodeSession(raw);
        if (next) {
          setSession(next);
          onChanged(next);
          // v1.475.0: a model or effort change in a conversation leaves a
          // line in it (the daemon writes it): read the conversation again to
          // show it. While a reply runs the line comes with its stream.
          if (("model" in patchIn || "effort" in patchIn) && next.toolSessionId && !running) {
            void load({ follow: false });
          }
        } else {
          void load({ follow: false });
        }
      } catch (e) {
        setNote(errorWords(e, "That change did not save."));
      }
    },
    [session, onChanged, load, running],
  );

  /** The account hit its limit: carry on with the same conversation on the
   *  next account of the same tool (Iron-Proxy picks it). A refusal (no
   *  other account) is said in its words. */
  /** v1.417.0 (C4.9): Retry on the newest failed reply — the same message
   *  again, its files by path (they went up already).
   *  v1.422.0 review: a carry-on Iron Jarvis sent (its `carry`) is run again
   *  by the daemon, hidden (POST …/continue) — its words are Iron Jarvis's to
   *  the tool and must never be sent as the user's own message. */
  const continueCutRef = useRef<(() => Promise<void>) | null>(null);
  // v1.441.0: a branch just made lands with its message in the box.
  const draftTakenRef = useRef(false);
  useEffect(() => {
    if (!initialDraft || draftTakenRef.current) return;
    draftTakenRef.current = true;
    if (initialDraft.text) setText((cur) => (cur.trim() ? cur : initialDraft.text));
    if (initialDraft.note) setInfo(initialDraft.note);
  }, [initialDraft]);

  /** v1.441.0: Branch from here — a NEW session (the page opens it). */
  const sessionIdNow = session?.id ?? "";
  const sessionSource = session?.source ?? "";
  const branchFrom = useCallback(
    async (at: string) => {
      if (!sessionIdNow || branching) return;
      setBranching(true);
      setNote(null);
      try {
        const raw = await post<unknown>(`${SESSIONS}/${encodeURIComponent(sessionIdNow)}/fork`, { at });
        const a = decodeBranchAnswer(raw);
        if (!a) throw new Error("The branch could not be made.");
        onBranched?.(a);
      } catch (e) {
        setNote(errorWords(e, "The branch could not be made."));
      } finally {
        setBranching(false);
      }
    },
    [sessionIdNow, branching, onBranched],
  );

  /** v1.441.0: Rewind to here — the conversation drops that message and
   *  everything after it; its words come back to the box. */
  const rewindTo = useCallback(
    async (at: string) => {
      if (!sessionIdNow || branching) return;
      setBranching(true);
      setNote(null);
      setInfo(null);
      try {
        const raw = await post<unknown>(`${SESSIONS}/${encodeURIComponent(sessionIdNow)}/rewind`, { to: at });
        const a = decodeBranchAnswer(raw);
        if (!a) throw new Error("The conversation could not be rewound.");
        setText((cur) => (cur.trim() ? cur : a.text));
        setInfo(a.note);
        await load({ follow: true });
      } catch (e) {
        setNote(errorWords(e, "The conversation could not be rewound."));
      } finally {
        setBranching(false);
      }
    },
    [sessionIdNow, branching, load],
  );

  const messageActions = useMemo<MessageActions | null>(
    () =>
      branch && sessionIdNow && sessionSource !== "disk"
        ? { facts: branch, busy: running || branching, onBranch: (at) => void branchFrom(at), onRewind: rewindTo }
        : null,
    [branch, sessionIdNow, sessionSource, running, branching, branchFrom, rewindTo],
  );

  const retryFailed = useCallback(() => {
    if (runningRef.current) return;
    for (let i = conv.items.length - 1; i >= 0; i--) {
      const it = conv.items[i];
      if (it.kind === "user") {
        if (it.carry) void continueCutRef.current?.();
        else void sendRef.current?.(it.text, it.files);
        return;
      }
    }
  }, [conv.items]);

  /** v1.427.3: `account` + `force` = the user's "Try X anyway" (an account
   *  Iron-Proxy has parked for a limit); none = the daemon picks. */
  const moveAccount = useCallback(async (account = "", force = false) => {
    if (!session || continuing) return;
    setContinuing(true);
    setNote(null);
    setInfo(null);
    try {
      const raw = await post<unknown>(
        `${SESSIONS}/${encodeURIComponent(session.id)}/continue-on`,
        account ? { account, force } : {},
      );
      setTryChoices([]);
      const next = decodeCodeSession(raw);
      if (next) {
        setSession(next);
        onChanged(next);
      }
      const o = (raw && typeof raw === "object" ? raw : {}) as { account_name?: unknown; note?: unknown };
      const name = typeof o.account_name === "string" && o.account_name ? o.account_name : next ? accountWords(next, tools) : "";
      const extraWords = typeof o.note === "string" && o.note.trim() ? ` ${o.note.trim()}` : "";
      setInfo(`${name ? `Now on ${name}.` : "Moved to another account."}${extraWords} Send your message again to carry on.`);
    } catch (e) {
      setNote(errorWords(e, "The session could not move to another account."));
      // Kept when a "Try X anyway" itself was refused: the others stay offered.
      if (!account) setTryChoices(decodeTryChoices(e instanceof ApiError ? e.body : null));
    } finally {
      setContinuing(false);
    }
  }, [session, continuing, onChanged, tools]);
  const continueOn = useCallback(() => moveAccount(), [moveAccount]);
  const tryAccount = useCallback((id: string) => moveAccount(id, true), [moveAccount]);

  /** v1.422.0 (B3): Continue on a reply a restart cut off — the same hidden
   *  carry-on the app sends by itself, started by this press; the running
   *  turn is then followed as on any read. */
  const continueCut = useCallback(async () => {
    if (!session || continuingCut) return;
    setContinuingCut(true);
    setNote(null);
    setInfo(null);
    try {
      await post<unknown>(`${SESSIONS}/${encodeURIComponent(session.id)}/continue`, {});
      setCarryFacts((c) => ({ ...c, cut: null }));
      await load({ follow: true });
    } catch (e) {
      setNote(errorWords(e, "The reply could not be carried on."));
    } finally {
      setContinuingCut(false);
    }
  }, [session, continuingCut, load]);
  continueCutRef.current = continueCut;

  /** v1.422.0 (B5): Cancel on a limit card that waits by itself — nothing
   *  carries on by itself after it; the card and its press stay. */
  const cancelWait = useCallback(async () => {
    if (!session) return;
    setNote(null);
    try {
      await post<unknown>(`${SESSIONS}/${encodeURIComponent(session.id)}/wait/cancel`, {});
      setCarryFacts((c) => ({ ...c, wait: null }));
    } catch (e) {
      setNote(errorWords(e, "The wait could not be cancelled."));
      void load({ follow: false });
    }
  }, [session, load]);

  const openInTerminal = useCallback(async () => {
    if (!session) return;
    setMoreOpen(false);
    setNote(null);
    try {
      const res = await post<{ terminal_id?: string }>(`${SESSIONS}/${encodeURIComponent(session.id)}/open-terminal`);
      const id = typeof res?.terminal_id === "string" ? res.terminal_id : "";
      if (id) onOpenedTerminal(id);
      else setNote("The terminal did not open.");
    } catch (e) {
      setNote(errorWords(e, "The terminal did not open."));
    }
  }, [session, onOpenedTerminal]);

  const archive = useCallback(async () => {
    if (!session) return;
    setMoreOpen(false);
    try {
      const raw = await post<unknown>(`${SESSIONS}/${encodeURIComponent(session.id)}/archive`);
      onArchived(session.id, archiveSummaryError(raw));
    } catch (e) {
      setNote(errorWords(e, ARCHIVE_FAILED));
    }
  }, [session, onArchived]);

  // ── the `/` and `@` lists ──
  // v1.418.0 (C4.8): `/` at the start of ANY line (where the caret is).
  const slash = slashAt(text, caret);
  const at = /(^|\s)@(\S*)$/.exec(text);
  const filesState = useProjectFilesState(menuOpen && !!at, session?.folder ?? null);
  const commands = useMemo(() => {
    if (viewCommands && viewCommands.length) return viewCommands;
    if (tool && tool.commands.length) return tool.commands;
    return fetchedCommands ?? [];
  }, [viewCommands, tool, fetchedCommands]);
  // Keyed on the TEXT a regex found (a new match object each render would
  // re-run these on every key press).
  const slashKey = slash ? `${slash.start}:${slash.query}` : null;
  const atKey = at ? at[0] : null;
  useEffect(() => {
    if (slashKey === null || !session || (viewCommands && viewCommands.length) || (tool && tool.commands.length) || fetchedCommands !== null)
      return;
    get<unknown>(`${SESSIONS}/${encodeURIComponent(session.id)}/commands`)
      .then((raw) => {
        // v1.426.0 (E4): a Codex skill is typed `$<name>`; v1.429.0: each
        // row keeps its kind and (Pi) why it stays in Pi's own terminal.
        setFetchedCommands(decodeCommands(raw) ?? []);
        setHints((cur) => ({ ...commandHints(raw), ...cur }));
      })
      .catch(() => setFetchedCommands([]));
  }, [slashKey, session, viewCommands, tool, fetchedCommands]);
  const menuRows: MenuRow[] = useMemo(() => {
    if (slash) {
      return slashMenuRows(commands, slash.query, SLASH_ROWS).map((cmd) => ({ kind: "command", cmd }));
    }
    if (at) {
      return matchProjectEntries(filesState.listing, at[2], files, 6, { fuzzy: true }).map((entry) => ({ kind: "file", entry }));
    }
    return [];
  }, [slash, at, commands, filesState.listing, files]);
  useEffect(() => {
    setMenuOpen(slashKey !== null || atKey !== null);
    setMenuIndex(0);
  }, [text, slashKey, atKey]);

  const pick = (row: MenuRow) => {
    if (row.kind === "command" && row.cmd.note) {
      // v1.429.0: one of Pi's commands that only its own terminal has — the
      // one line says why, and nothing is typed.
      setNote(`/${row.cmd.name}: ${row.cmd.note}`);
      setMenuOpen(false);
      boxRef.current?.focus();
      return;
    }
    if (row.kind === "command") {
      if (slash) {
        // v1.418.0 (C4.8): only the `/word` is replaced (a line of its own).
        const done = pickCommand(text, slash, row.cmd.name, row.cmd.prefix ?? "/");
        setText(done.text);
        setCaret(done.caret);
        nextFrame(() => boxRef.current?.setSelectionRange?.(done.caret, done.caret));
      } else {
        setText(`${row.cmd.prefix ?? "/"}${row.cmd.name} `);
      }
    } else if (at) {
      const next = text.slice(0, text.length - at[2].length) + `${row.entry.rel} `;
      setText(next);
      setCaret(next.length);
      if (row.entry.kind === "file") setFiles((prev) => (prev.includes(row.entry.path) ? prev : [...prev, row.entry.path]));
    }
    setMenuOpen(false);
    boxRef.current?.focus();
  };

  const onBoxKey = (e: ReactKeyboardEvent<HTMLTextAreaElement>) => {
    if (e.nativeEvent.isComposing || e.keyCode === 229) return;
    const showing = menuOpen && menuRows.length > 0;
    if (showing && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
      e.preventDefault();
      setMenuIndex((i) => (i + (e.key === "ArrowDown" ? 1 : -1) + menuRows.length) % menuRows.length);
      return;
    }
    if (showing && (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey))) {
      e.preventDefault();
      pick(menuRows[Math.min(menuIndex, menuRows.length - 1)]);
      return;
    }
    if (e.key === "Escape" && menuOpen) {
      e.preventDefault();
      setMenuOpen(false);
      return;
    }
    // v1.418.0 (C4.8): Esc stops the reply when no menu (and no card — the
    // box is hidden under one) is open: the same press as Stop.
    if (e.key === "Escape" && runningRef.current) {
      e.preventDefault();
      stop();
      return;
    }
    // Shift+Tab steps the mode (never into Full access: two presses there).
    if (e.key === "Tab" && e.shiftKey && session) {
      const m = nextMode(tool, session.mode);
      if (m) {
        e.preventDefault();
        void change({ mode: m });
      }
      return;
    }
    // While it works: Enter queues the message, Shift+Enter sends a note now.
    if (e.key === "Enter" && e.shiftKey && runningRef.current) {
      e.preventDefault();
      sendNote();
      return;
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void send();
    }
  };

  // The last question answered: the caret goes back to the message box (the
  // card held it, and it is gone).
  const askingNow = conv.asks.length > 0;
  const wasAsking = useRef(false);
  useEffect(() => {
    if (wasAsking.current && !askingNow) {
      const ae = document.activeElement;
      if (!ae || ae === document.body || ae === document.documentElement) boxRef.current?.focus();
    }
    wasAsking.current = askingNow;
  }, [askingNow]);

  // v1.414.0: while this session is on screen and its window has focus, the
  // bell knows the user is LOOKING at it (lib/buildWatching — the same note
  // Chat › Code writes): a long reply that ends here says nothing, they
  // watched it end. The pane is mounted only while Build shows Code.
  useEffect(() => {
    const look = () => {
      if (document.visibilityState === "visible" && document.hasFocus()) noteLooking(sessionId);
      else stopLooking(sessionId);
    };
    look();
    const beat = window.setInterval(look, LOOKING_BEAT_MS);
    window.addEventListener("focus", look);
    window.addEventListener("blur", look);
    document.addEventListener("visibilitychange", look);
    return () => {
      window.clearInterval(beat);
      window.removeEventListener("focus", look);
      window.removeEventListener("blur", look);
      document.removeEventListener("visibilitychange", look);
      stopLooking(sessionId);
    };
  }, [sessionId]);

  if (loadError && !session) {
    return (
      <div className="grid h-full place-items-center px-4">
        <p role="alert" data-testid="code-load-error" className="text-[13px] text-tone-danger">
          {loadError}
        </p>
      </div>
    );
  }
  if (!session) {
    return (
      <div className="grid h-full place-items-center text-zinc-600">
        <Loader2 size={18} className="animate-spin" aria-label="Opening the session" />
      </div>
    );
  }

  const asking = conv.asks.length > 0;
  const accountName = accountWords(session, tools);
  // v1.418.0 (C4.8): which chip changes wait for the reply to end.
  const modelLive = modelWhileRunning(session.tool, toolName, running);
  // v1.475.0 review: while the tools' list is not read (still loading, could
  // not be read, or the tool is no longer installed) the chip still names
  // the session's tool and model, from the session itself.
  // (A plain value: this is past the pane's early return, so no hook here.)
  const pickerTools = tool
    ? [tool]
    : decodeCodingTools({ harnesses: [{ id: session.tool, label: toolName, installed: true, signed_in: true, models: [] }] });
  const effortLive = effortWhileRunning(session.tool, toolName, running, tool?.effortLive === true);
  return (
    <SessionEditorProvider folder={session.folder}>
      {/* v1.414.0: the conversation and, to its right, the panes (Changes,
          Usage, Files, Session) — side by side from xl, the panes BELOW it
          under xl (the row scrolls; the conversation keeps the stage's whole
          height, so its box works at 390 px). Chat › Code's own rule. */}
      <div
        data-testid="code-session-row"
        className="flex h-full min-h-0 flex-col gap-3 overflow-y-auto xl:flex-row xl:gap-0 xl:overflow-hidden"
      >
      <div
        data-testid="code-session"
        data-session={session.id}
        className="flex h-full min-h-0 flex-none flex-col xl:min-w-0 xl:flex-1"
      >
        <div className="flex shrink-0 items-center gap-2 border-b border-white/[0.06] px-4 py-2">
          <div className="min-w-0 flex-1">
            <p className="truncate text-[14px] text-zinc-100" data-testid="code-session-title">
              {sessionTitle(session, tools)}
            </p>
            <p className="truncate text-[12px] text-zinc-500" title={session.folder}>
              {sessionRowLine(session, tools)} · {sessionFolderWords(session)}
            </p>
          </div>
          {/* v1.414.0: Iron Jarvis's own tools for this session (all off
              until ticked) — the same two boxes a Build terminal has. */}
          {/* v1.441.0 (review A5): never for a tool that takes none. */}
          {session.jarvisToolsTaken === false ? null : (
            <JarvisToolsChip
              session={session}
              toolName={toolName}
              onChanged={adoptSession}
              onInfo={setInfo}
            />
          )}
          <div className="relative">
            <button
              type="button"
              aria-label="More for this session"
              aria-haspopup="menu"
              aria-expanded={moreOpen}
              data-testid="code-session-more"
              onClick={() => setMoreOpen((v) => !v)}
              className="grid h-7 w-7 place-items-center rounded-md text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-200"
            >
              <MoreHorizontal size={15} />
            </button>
            {moreOpen ? (
              <div role="menu" className="absolute right-0 top-full z-20 mt-1 w-52 rounded-xl bg-ink-800 p-1 shadow-card">
                <button
                  type="button"
                  role="menuitem"
                  data-testid="code-open-terminal"
                  disabled={running}
                  title={running ? "Wait for the reply to finish, or press Stop first." : undefined}
                  onClick={() => void openInTerminal()}
                  className="flex w-full items-center rounded-lg px-2.5 py-1.5 text-left text-[13px] text-zinc-300 hover:bg-white/[0.06] disabled:opacity-50"
                >
                  {OPEN_IN_TERMINAL}
                </button>
                <button
                  type="button"
                  role="menuitem"
                  data-testid="code-archive"
                  disabled={running || isArchived(session)}
                  title={running ? ARCHIVE_RUNNING_TITLE : ARCHIVE_ROW_TITLE}
                  onClick={() => void archive()}
                  className="flex w-full items-center rounded-lg px-2.5 py-1.5 text-left text-[13px] text-zinc-300 hover:bg-white/[0.06] disabled:opacity-50"
                >
                  {ARCHIVE_ROW}
                </button>
              </div>
            ) : null}
          </div>
        </div>

        <div
          ref={scrollRef}
          onScroll={onScroll}
          data-testid="code-transcript"
          // v1.420.2: `relative` — the transcript positions (and so clips)
          // what it holds; see HarnessSession's transcript, the same bug.
          className="relative min-h-0 flex-1 overflow-y-auto px-4 py-4"
        >
          <div className={HARNESS_COLUMN}>
            {/* v1.453.0: the summary written when it was archived (open from
                "Archived sessions"); folded once it is brought back. */}
            <ArchiveSummaryCard
              sessionId={session.id}
              facts={summaryFacts}
              archived={isArchived(session)}
              onBringBack={
                onUnarchive
                  ? () => {
                      void (async () => {
                        await onUnarchive(session.id);
                        void load({ follow: false });
                      })();
                    }
                  : undefined
              }
            />
            {/* v1.418.0 (C4.6): what is older than this read, said — and one
                press loads the page before it. */}
            {drawFrom > 0 ? (
              <div data-testid="code-folded" className="mb-3 flex items-center justify-center gap-2 text-[12px] text-zinc-500">
                <span data-testid="code-folded-count">{hiddenWords(foldedMessages)}</span>
                <button
                  type="button"
                  data-testid="code-show-earlier"
                  onClick={showEarlier}
                  className="rounded-lg px-2 py-0.5 text-zinc-300 transition-colors hover:bg-white/[0.06] hover:text-zinc-100"
                >
                  {SHOW_EARLIER}
                </button>
              </div>
            ) : older.earlier > 0 ? (
              <div data-testid="code-earlier" className="mb-3 flex items-center justify-center gap-2 text-[12px] text-zinc-500">
                <span data-testid="code-earlier-count">{earlierStepsWords(older.earlier)}</span>
                {older.cursor ? (
                  <button
                    type="button"
                    data-testid="code-load-earlier"
                    disabled={loadingEarlier}
                    onClick={() => void loadEarlier()}
                    className="rounded-lg px-2 py-0.5 text-zinc-300 transition-colors hover:bg-white/[0.06] hover:text-zinc-100 disabled:opacity-50"
                  >
                    {loadingEarlier ? "Loading…" : LOAD_EARLIER}
                  </button>
                ) : null}
              </div>
            ) : null}
            {dropped > 0 ? (
              <p data-testid="code-dropped" className="mb-3 text-center text-[11px] text-zinc-600">
                {droppedWords(dropped)}
              </p>
            ) : null}
            {conv.items.length === 0 && session.source !== "disk" ? (
              <ProfileShareOffer tool={session.tool} toolName={toolName} />
            ) : null}
            {conv.items.length === 0 ? (
              <p data-testid="code-empty" className="py-10 text-center text-[13px] text-zinc-500">
                {session.source === "disk"
                  ? `This conversation was saved by ${toolName}. Send a message to carry it on here.`
                  : session.noFolder && !session.folder
                    ? `Ask ${toolName} to do something. Its files go in a folder of its own, made with your first message.`
                    : `Ask ${toolName} to do something in ${session.folder || "this folder"}.`}
              </p>
            ) : (
              <CodeConversation
                items={conv.items}
                from={drawFrom}
                folder={session.folder}
                tool={session.tool}
                onContinueOn={continueOn}
                continuing={continuing}
                tryChoices={tryChoices}
                onTryAccount={tryAccount}
                onRetry={running ? undefined : retryFailed}
                cut={running ? null : carryFacts.cut}
                onContinueCut={continueCut}
                continuingCut={continuingCut}
                wait={running ? null : carryFacts.wait}
                onCancelWait={cancelWait}
                limitNote={carryFacts.limitNote}
                model={session.model}
                account={session.account ? accountName : ""}
                onExpand={expandStep}
                messageActions={messageActions}
              />
            )}
            {/* The "working" sign: a reply being written, helpers still at
                work after it (v1.417.0), or the tool's own reply. Its slot is
                always here, so nothing moves when the sign comes or goes; it
                stays in view (sticky) for a reader who scrolled up. */}
            <WorkingSign
              session={session}
              toolName={toolName}
              running={running}
              asking={asking}
              floating={away}
              compact={jump}
            />
            {/* v1.422.0 (B4): kept open past its idle stop because the tool
                runs a program it started that serves (a dev server) — said,
                with the way to close it now; and what the last stop ENDED. */}
            {!running && session.keepNote ? (
              <div data-testid="code-keeping" className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1">
                <p role="status" className="min-w-0 text-[12px] text-zinc-500">
                  {session.keepNote}
                </p>
                <span data-testid="code-close-tool" className="shrink-0">
                  <ConfirmButton
                    label={closeToolWords(toolName).idle}
                    confirmLabel={closeToolWords(toolName).armed}
                    title={closeToolWords(toolName).title}
                    onConfirm={closeTool}
                  />
                </span>
              </div>
            ) : null}
            {!running && !session.keepNote && session.stoppedNote ? (
              <p role="status" data-testid="code-stopped-programs" className="mt-3 whitespace-pre-line text-[12px] text-zinc-500">
                {session.stoppedNote}
              </p>
            ) : null}
            {lost ? (
              <p role="status" data-testid="code-lost" className="mt-3 text-[12px] text-zinc-500">
                Iron Jarvis isn&apos;t answering right now. The reply keeps going there and shows here again once
                it&apos;s back.
              </p>
            ) : null}
            {/* The bottom: every "show the newest" scroll aims here. */}
            <div ref={sentinelRef} aria-hidden="true" className="h-px scroll-mb-4" />
          </div>
        </div>

        <div className={`relative shrink-0 px-4 pb-4 ${HARNESS_COLUMN}`}>
          {jump ? (
            <button
              type="button"
              data-testid="code-jump-to-latest"
              onClick={jumpToLatest}
              title="Scroll to the latest step"
              className="absolute bottom-full left-1/2 z-10 mb-2 flex -translate-x-1/2 items-center gap-1.5 whitespace-nowrap rounded-full border border-white/[0.1] bg-ink-850/95 px-3 py-1 text-[12px] text-zinc-300 backdrop-blur transition-colors hover:border-white/[0.16] hover:bg-ink-800 hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60"
            >
              <ChevronDown size={13} aria-hidden="true" /> Jump to latest
            </button>
          ) : null}
          {note ? (
            <p role="alert" data-testid="code-note" className="mb-2 text-[12px] text-tone-danger">
              {note}
            </p>
          ) : null}
          {info ? (
            <p role="status" data-testid="code-info" className="mb-2 text-[12px] text-zinc-500">
              {info}
            </p>
          ) : null}
          {/* v1.474.0 (Hand off): the helpers this session's tool started,
              or, in a helper, the session that started it. */}
          <HelperLines helpers={helpers} handedFrom={handedFrom} onOpen={onOpenSession} />
          {/* v1.431.0: the folder's own Pi extensions, skills or settings —
              Pi skips them here (it asks only in its terminal); nothing
              loads without this press, and it is for this session only. */}
          {piFolder && (piFolder.offer || piFolder.loaded) ? (
            <div data-testid="code-pi-folder" className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1 px-1">
              <p role="status" className="min-w-0 text-[12px] text-zinc-500" title={piFolder.title || undefined}>
                {piFolder.line}
              </p>
              <button
                type="button"
                data-testid={piFolder.offer ? "code-pi-folder-load" : "code-pi-folder-skip"}
                title={piFolder.title || undefined}
                onClick={() => void change({ load_folder_resources: piFolder.offer })}
                className="shrink-0 rounded-full px-2.5 py-0.5 text-[12px] text-zinc-300 shadow-[0_0_0_0.5px_rgb(var(--white)/0.16)] transition-colors hover:bg-white/[0.06] hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60"
              >
                {piFolder.offer ? PI_FOLDER_LOAD_LABEL : PI_FOLDER_SKIP_LABEL}
              </button>
            </div>
          ) : null}
          {/* v1.441.0 review: Pi runs `!words` as a shell command (its own
              terminal colours the box; here one quiet line says it). */}
          {piShellHint(session.tool, text) ? (
            <p role="status" data-testid="code-pi-shell" className="mb-2 px-1 text-[12px] text-zinc-500">
              {piShellHint(session.tool, text)}
            </p>
          ) : null}
          {asking ? (
            <>
              <CodeAskCard
                asks={conv.asks}
                toolName={toolName}
                dangerousMode={dangerous}
                onAnswer={answer}
                onStop={running ? stop : undefined}
                error={askError}
                takesWords={session.tool === "claude-code"}
              />
              {/* v1.412.0: the mode can change while a card waits (the
                  composer, and its chip, are hidden under the card). */}
              <div data-testid="code-ask-mode-row" className="mt-1 flex items-center gap-1 px-1">
                <ModeChip
                  tool={tool}
                  value={session.mode}
                  testId="code-ask-mode"
                  onChange={(mode, confirm) => void change(confirm ? { mode, confirm: true } : { mode })}
                />
              </div>
            </>
          ) : null}
          <div
            data-testid="code-composer"
            data-code-composer=""
            hidden={asking}
            inert={asking}
            className="relative rounded-[20px] bg-ink-800 shadow-[0_0_0_0.5px_rgb(var(--white)/0.12)]"
            onDragOver={(e) => {
              if (e.dataTransfer?.types?.includes("Files")) e.preventDefault();
            }}
            onDrop={(e) => {
              const dropped = Array.from(e.dataTransfer?.files ?? []);
              if (!dropped.length) return;
              e.preventDefault();
              void att.add(dropped);
            }}
          >
            {menuOpen && menuRows.length > 0 ? (
              <ul
                role="listbox"
                aria-label={slash ? `${toolName}'s commands` : "Files in this folder"}
                data-testid={slash ? "code-slash-menu" : "code-at-menu"}
                className="absolute bottom-full left-0 z-20 mb-2 max-h-64 w-full overflow-auto rounded-xl bg-ink-800 p-1 shadow-card"
              >
                {menuRows.map((row, i) => (
                  <li key={row.kind === "command" ? row.cmd.name : row.entry.path}>
                    <button
                      type="button"
                      role="option"
                      aria-selected={i === menuIndex}
                      data-testid={row.kind === "command" ? `code-command-${row.cmd.name}` : "code-at-file"}
                      onMouseDown={(e) => e.preventDefault()}
                      onClick={() => pick(row)}
                      className={`flex w-full items-baseline gap-2 rounded-lg px-2.5 py-1.5 text-left text-[13px] ${
                        i === menuIndex ? "bg-white/[0.06] text-zinc-100" : "text-zinc-300"
                      }`}
                    >
                      {row.kind === "command" ? (
                        <>
                          <span className="shrink-0 font-mono">
                            {row.cmd.prefix ?? "/"}
                            {row.cmd.name}
                          </span>
                          {hints[row.cmd.name] ? (
                            <span data-testid="code-command-hint" className="shrink-0 font-mono text-[12px] text-zinc-500">
                              {hints[row.cmd.name]}
                            </span>
                          ) : null}
                          <span className="min-w-0 truncate text-[12px] text-zinc-500">
                            {row.cmd.note ? `${row.cmd.description}: ${row.cmd.note}` : row.cmd.description}
                          </span>
                          {row.cmd.source ? (
                            <span data-testid="code-command-source" className="ml-auto shrink-0 text-[11px] text-zinc-500">
                              {COMMAND_SOURCE_WORDS[row.cmd.source]}
                            </span>
                          ) : null}
                        </>
                      ) : (
                        <span className="min-w-0 truncate">{row.entry.rel}</span>
                      )}
                    </button>
                  </li>
                ))}
              </ul>
            ) : menuOpen && at && !filesState.listing ? (
              <p className="absolute bottom-full left-0 mb-2 rounded-xl bg-ink-800 px-3 py-2 text-[12px] text-zinc-500 shadow-card">
                {filesState.failed ? "This folder's files could not be read." : "Reading this folder's files…"}
              </p>
            ) : null}
            <AttachmentChips files={att.files} uploading={att.uploading} onRemove={att.remove} />
            {att.problem ? (
              <p data-testid="code-attach-problem" role="alert" className="px-3 pt-1 text-[12px] text-tone-warn">
                {att.problem}
              </p>
            ) : null}
            <textarea
              ref={boxRef}
              data-testid="code-box"
              aria-label={`Message ${toolName}`}
              placeholder={running ? workingPlaceholder(toolName) : `Message ${toolName} — / for commands, @ for files`}
              rows={2}
              value={text}
              onChange={(e) => {
                setText(e.target.value);
                setCaret(e.target.selectionStart ?? e.target.value.length);
              }}
              onSelect={(e) => setCaret(e.currentTarget.selectionStart ?? e.currentTarget.value.length)}
              onKeyDown={onBoxKey}
              onPaste={(e) => {
                // A pasted picture is attached; pasted text is typed.
                const pasted = pastedFiles(e.clipboardData);
                if (!pasted.length) return;
                e.preventDefault();
                void att.add(pasted, { pasted: true });
              }}
              className={HARNESS_COMPOSER_BOX}
            />
            <div className="flex flex-wrap items-center gap-1 px-2 pb-2">
              <AttachButton onPick={(picked) => void att.add(picked)} />
              <span className="sr-only">Coding tool: {toolName}</span>
              {/* v1.475.0: one chip for the model and its effort; the tool is
                  fixed once a session exists. v1.418.0 (C4.8) still holds: the
                  model stays open while a reply runs; only going back to the
                  default (a new start) waits. A conversation that keeps its last
                  model offers no way back to the default. */}
              <ModelPicker
                tools={pickerTools}
                value={{ tool: session.tool, model: session.model, effort: session.effort }}
                onChange={(field, next) => {
                  if (field === "model") void change({ model: next });
                  else if (field === "effort") void change({ effort: next });
                }}
                lockTool
                testId="code-pick"
                chipTestId="code-model-chip"
                defaultModelRow={!keepsLast || !session.model}
                defaultModelLabel={(t) => defaultModelWords(t, keepsLast)}
                defaultModelHeld={modelLive.defaultHeld ? modelLive.title || "Not while a reply runs" : undefined}
                keptModelLabel={oneMillionLabel(session.model, tool?.models ?? []) || undefined}
                defaultEffortRow={!keepsLast || !session.effort}
                defaultEffortLabel={defaultEffortWords(keepsLast)}
                defaultEffortHeld={effortLive.defaultHeld ? effortLive.title || "Not while a reply runs" : undefined}
                effortHeld={effortLive.held ? effortLive.title || "Not while a reply runs" : undefined}
                title={keepsLast ? keepsLastTitle(session.tool) : modelLive.title || undefined}
                onOpen={onPickerOpen}
                shortcut
              />
              <ModeChip
                tool={tool}
                value={session.mode}
                onChange={(mode, confirm) => void change(confirm ? { mode, confirm: true } : { mode })}
              />
              <span
                data-testid="code-account-chip"
                title={
                  signsInItself(session.tool, tools)
                    ? ownSignInTitle(session.tool, tools)
                    : "The account this session runs on (chosen when it was made)"
                }
                className="inline-flex h-7 max-w-[12rem] items-center gap-1.5 truncate px-2 text-[12px] text-zinc-500"
              >
                <UserRound size={13} aria-hidden="true" className="shrink-0" />
                <span className="truncate">{accountName}</span>
              </span>
              <span className="ml-auto" />
              {running && (text.trim() || att.files.length || files.length) ? (
                <button
                  type="button"
                  data-testid="code-steer-send"
                  disabled={steering}
                  onClick={sendNote}
                  aria-label={`Send a note to ${toolName}`}
                  title={`Send a note to ${toolName} while it works (Shift+Enter) — Enter queues it for when it finishes`}
                  className="grid h-8 w-8 place-items-center rounded-full bg-accent text-ink-950 transition-opacity disabled:opacity-40"
                >
                  <ArrowUp size={15} aria-hidden="true" />
                </button>
              ) : null}
              {running ? (
                <button
                  type="button"
                  data-testid="code-stop"
                  onClick={stop}
                  aria-label={`Stop ${toolName}`}
                  className="grid h-8 w-8 place-items-center rounded-full bg-white/[0.08] text-zinc-100 transition-colors hover:bg-white/[0.14]"
                >
                  <Square size={13} aria-hidden="true" />
                </button>
              ) : (
                <button
                  type="button"
                  data-testid="code-send"
                  disabled={!text.trim() && !att.files.length && !files.length}
                  onClick={() => void send()}
                  aria-label="Send"
                  className="grid h-8 w-8 place-items-center rounded-full bg-accent text-ink-950 transition-opacity disabled:opacity-40"
                >
                  <ArrowUp size={15} aria-hidden="true" />
                </button>
              )}
            </div>
          </div>
          {files.length ? (
            <p data-testid="code-files" className="mt-1 truncate px-2 text-[11px] text-zinc-500">
              With: {files.map((f) => f.split(/[\\/]/).pop()).join(", ")}
            </p>
          ) : null}
          {/* v1.418.0 (C4.8): messages waiting for the reply to end (sent
              one by one when it ends cleanly; back in the box otherwise). */}
          {queued.length ? (
            <ul data-testid="code-queued" aria-label={`Waiting for ${toolName} to finish`} className="mt-2 space-y-1 px-1">
              {queued.map((q) => (
                <li
                  key={q.id}
                  data-testid="code-queued-item"
                  className="flex items-center gap-2 rounded-lg bg-white/[0.04] px-2.5 py-1 text-[12px] text-zinc-400"
                >
                  <span className="shrink-0 text-zinc-500">Waiting</span>
                  <span className="min-w-0 flex-1 truncate" title={q.text}>
                    {queuedLine(q)}
                  </span>
                  <button
                    type="button"
                    data-testid={`code-queued-remove-${q.id}`}
                    aria-label={`Remove the waiting message: ${queuedLine(q)}`}
                    title="Remove this waiting message"
                    onClick={() => void removeQueued(q.id)}
                    className="grid h-5 w-5 shrink-0 place-items-center rounded text-zinc-500 hover:bg-white/[0.08] hover:text-zinc-200"
                  >
                    <X size={12} aria-hidden="true" />
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      </div>
      <aside
        aria-label="Changes, usage, files and session"
        data-testid="code-panes-aside"
        className="flex h-[70vh] w-full shrink-0 flex-col border-t border-white/[0.06] p-2 xl:h-auto xl:min-h-0 xl:w-[320px] xl:border-l xl:border-t-0 2xl:w-[400px]"
      >
        <CodePanes
          session={session}
          tools={tools}
          events={paneEvents}
          running={running}
          onSend={sendFromPanes}
        />
      </aside>
      </div>
    </SessionEditorProvider>
  );
}

// ── the view ────────────────────────────────────────────────────────────────

/** What one folder's saved conversations are, while they are read. */
type DiskState = { state: "loading" } | { state: "error"; words: string } | { state: "ok"; rows: DiskSession[] };

export function BuildCodeView({
  initialSession,
  linkSeq = 0,
  startFolder = null,
  onSessionChange,
  onOpenedTerminal,
}: {
  /** The session the address (or a link) names. */
  initialSession: string | null;
  /** Bumped when a link names a session again (the page stays mounted). */
  linkSeq?: number;
  /** Where New session's folder tree starts (Build's last folder). */
  startFolder?: string | null;
  /** The open session changed (the page puts it in the address). */
  onSessionChange?: (id: string | null) => void;
  /** Open in terminal landed: the page shows that terminal. */
  onOpenedTerminal: (terminalId: string) => void;
}) {
  const [sessions, setSessions] = useState<CodeSession[] | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [tools, setTools] = useState<CodingTool[] | null>(null);
  const [toolsError, setToolsError] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(initialSession);
  const [creating, setCreating] = useState(false);
  const [rowMenu, setRowMenu] = useState<string | null>(null);
  // v1.441.0 (integration I6): the open row's ⋯ (its button and its list)
  // closes on Escape and on a press outside, like every other small menu.
  const rowMenuBox = useRef<HTMLDivElement | null>(null);
  const rowMenuButton = useRef<HTMLButtonElement | null>(null);
  const closeRowMenu = useCallback(() => setRowMenu(null), []);
  useMenuClose(rowMenu !== null, rowMenuBox, closeRowMenu, rowMenuButton);
  /** v1.439.0: the open session's loaded events (its pane fills it). */
  const exportRef = useRef<ExportSource | null>(null);
  // v1.441.0 review: the export reads up to 200 pages one after another, so
  // it SAYS it is reading, takes one press at a time, and stops (no more
  // pages, no file) when the view goes away.
  const exportingRef = useRef(false);
  const [exporting, setExporting] = useState(false);
  const viewAliveRef = useRef(true);
  useEffect(() => {
    viewAliveRef.current = true;
    return () => {
      viewAliveRef.current = false;
    };
  }, []);
  const exportMarkdown = async (s: CodeSession) => {
    setRowMenu(null);
    if (exportingRef.current) return;
    const src = exportRef.current;
    if (!src || src.id !== s.id) {
      setRowNote(EXPORT_NOT_READ);
      return;
    }
    exportingRef.current = true;
    setExporting(true);
    setRowNote(null);
    try {
      // v1.441.0 (review D1): the WHOLE conversation — every page before what
      // the pane holds, read through the same route "Load earlier" reads.
      const got = await collectAllEvents(src.events(), src.older().cursor, async (cursor) => {
        if (!viewAliveRef.current) throw new Error("left");
        return decodeEventsPage(await get<unknown>(eventsPagePath(SESSIONS, s.id, cursor, EXPORT_PAGE_LIMIT)));
      });
      if (!viewAliveRef.current) return;
      if (!hasExportableItems(got.events)) {
        setRowNote(EXPORT_NOTHING);
        return;
      }
      const title = sessionTitle(s, tools);
      downloadText(
        exportFileName(title),
        conversationMarkdown(title, got.events, toolLabel(s.tool, tools), exportGapWords(src.dropped(), got.unread)),
      );
    } finally {
      exportingRef.current = false;
      if (viewAliveRef.current) setExporting(false);
    }
  };
  // v1.426.0 (E5): the folder whose defaults panel is open under its heading.
  const [defaultsFor, setDefaultsFor] = useState<string | null>(null);
  const [rowNote, setRowNote] = useState<string | null>(null);
  // v1.420.0 (fix plan C4.10): rename a session in its row, and search the
  // list by title and folder (the list the view holds; no new read).
  const [renaming, setRenaming] = useState<{ id: string; text: string } | null>(null);
  // The row being renamed, read synchronously: Enter saves and the input's
  // blur (its removal) must not save a second time.
  const renameOpenRef = useRef<string | null>(null);
  const [query, setQuery] = useState("");
  // The archived list (read only while it is shown).
  const [showArchived, setShowArchived] = useState(false);
  const [archivedRows, setArchivedRows] = useState<CodeSession[] | null>(null);
  // The tool's own saved conversations, per folder: read ONLY for a folder
  // the user opens ("Saved by the tool") or the folder in front — never on
  // a timer.
  const [disk, setDisk] = useState<Record<string, DiskState>>({});
  const [diskOpen, setDiskOpen] = useState<Set<string>>(() => new Set());
  const diskAsked = useRef<Set<string>>(new Set());
  const [openSessionFolder, setOpenSessionFolder] = useState<string | null>(null);
  /** v1.412.0: the folder a start is on — a folder heading's "+"; New session
   *  starts with NO folder (the user's choice, "Choose…" on the composer). */
  const [newOn, setNewOn] = useState<{ folder: string | null; n: number }>({ folder: null, n: 0 });
  const startNew = useCallback((folder: string | null) => {
    setNewOn((cur) => ({ folder, n: cur.n + 1 }));
    setCreating(true);
    setRowMenu(null);
  }, []);
  /** v1.412.0: the words a just-made session sends as its first message. */
  const [firstFor, setFirstFor] = useState<{ id: string; text: string; files: string[] } | null>(null);
  const clearFirst = useCallback(() => setFirstFor(null), []);
  // v1.412.0 review: words parked for a just-made session are for THAT open
  // only — the user opening a different session drops them (they used to be
  // sent later, when the user came back to the made session).
  useEffect(() => {
    setFirstFor((cur) => (cur && cur.id !== openId ? null : cur));
  }, [openId]);
  /** Where a session with no folder keeps its files (the list's answer). */
  const [workRoot, setWorkRoot] = useState("");

  useEffect(() => {
    if (initialSession) {
      setOpenId(initialSession);
      setCreating(false);
    }
  }, [initialSession, linkSeq]);

  const readList = useCallback(() => {
    get<unknown>(SESSIONS)
      .then((raw) => {
        setSessions(decodeCodeSessions(raw));
        const root = (raw as { work_root?: unknown } | null)?.work_root;
        if (typeof root === "string") setWorkRoot(root);
        setListError(null);
      })
      .catch((e) => setListError(errorWords(e, "The sessions could not be read.")));
  }, []);
  // v1.429.0: a tool whose model list is still being read (Pi, the first
  // time) is looked at again a few times, a moment apart — never polled.
  // v1.450.0 (Wave D, item 40): when those re-reads run out and a list is
  // STILL being read, the start box says it couldn't read it, with Try again
  // (`rereadModels`), which arms the same few re-reads once more.
  const [modelsGaveUp, setModelsGaveUp] = useState(false);
  const toolsReadRef = useRef<{ tries: number; timer: ReturnType<typeof setTimeout> | null; gone: boolean }>({
    tries: 0,
    timer: null,
    gone: false,
  });
  const readTools = useCallback(() => {
    const st = toolsReadRef.current;
    get<unknown>(TOOLS)
      .then((raw) => {
        if (st.gone) return;
        const got = decodeCodingTools(raw);
        setTools(got);
        const reading = got.some((t) => t.modelsReading);
        if (reading && st.tries < TOOLS_REREAD_TRIES) {
          st.tries += 1;
          st.timer = setTimeout(readTools, TOOLS_REREAD_MS);
        } else {
          setModelsGaveUp(reading);
        }
      })
      .catch((e) => {
        if (!st.gone) setToolsError(errorWords(e, "The coding tools could not be looked for."));
      });
  }, []);
  const pickerReadAt = useRef(Date.now());
  const refreshTools = useCallback(() => {
    if (Date.now() - pickerReadAt.current < PICKER_REREAD_MS) return;
    pickerReadAt.current = Date.now();
    readTools();
  }, [readTools]);
  const rereadModels = useCallback(() => {
    const st = toolsReadRef.current;
    if (st.timer) clearTimeout(st.timer);
    st.tries = 0;
    setModelsGaveUp(false);
    readTools();
  }, [readTools]);
  useEffect(() => {
    readList();
    const st = toolsReadRef.current;
    st.gone = false;
    st.tries = 0;
    readTools();
    return () => {
      st.gone = true;
      if (st.timer) clearTimeout(st.timer);
    };
  }, [readList, readTools]);
  useVisibleInterval(readList, CODE_LIST_REREAD_MS);

  const readArchived = useCallback(() => {
    get<unknown>(`${SESSIONS}?archived=1`)
      .then((raw) => setArchivedRows(decodeCodeSessions(raw).filter((s) => isArchived(s))))
      .catch((e) => {
        setArchivedRows([]);
        setRowNote(errorWords(e, "The archived sessions could not be read."));
      });
  }, []);
  useEffect(() => {
    if (showArchived) readArchived();
  }, [showArchived, readArchived]);

  /** Read one folder's saved conversations (once, unless `again`). */
  const readDisk = useCallback((folder: string, again = false) => {
    const k = folderKey(folder);
    if (!folder || (!again && diskAsked.current.has(k))) return;
    diskAsked.current.add(k);
    setDisk((prev) => ({ ...prev, [k]: { state: "loading" } }));
    get<unknown>(diskListPath(folder))
      .then((raw) => setDisk((prev) => ({ ...prev, [k]: { state: "ok", rows: decodeDiskSessions(raw) } })))
      .catch((e) =>
        setDisk((prev) => ({ ...prev, [k]: { state: "error", words: errorWords(e, "The saved conversations could not be read.") } })),
      );
  }, []);
  // The folder in front: its saved conversations are listed without a press.
  useEffect(() => {
    if (openSessionFolder) readDisk(openSessionFolder);
  }, [openSessionFolder, readDisk]);

  const open = useCallback(
    (id: string | null) => {
      setOpenId(id);
      setCreating(false);
      setRowMenu(null);
      onSessionChange?.(id);
    },
    [onSessionChange],
  );

  const noteChanged = useCallback((s: CodeSession) => {
    setSessions((prev) => {
      if (!prev) return [s];
      const i = prev.findIndex((x) => x.id === s.id);
      if (i < 0) return [s, ...prev];
      const next = [...prev];
      next[i] = { ...prev[i], ...s };
      return next;
    });
  }, []);
  /** The open session read itself: also the folder in front. */
  const noteOpen = useCallback(
    (s: CodeSession) => {
      noteChanged(s);
      setOpenSessionFolder((cur) => (cur !== null && folderKey(cur) === folderKey(s.folder) ? cur : s.folder));
    },
    [noteChanged],
  );

  const archived = useCallback(
    (id: string, note?: string) => {
      setSessions((prev) => (prev ? prev.filter((s) => s.id !== id) : prev));
      if (openId === id) open(null);
      if (showArchived) readArchived();
      // v1.453.0: archived, but its summary could not be written: said.
      if (note) setRowNote(note);
    },
    [openId, open, showArchived, readArchived],
  );

  /** v1.453.0: Archive from the row — the daemon writes the session's
   *  summary and refuses (in words, said here) while a reply or background
   *  work runs; nothing leaves the list then. */
  const rowArchive = useCallback(
    async (id: string) => {
      setRowMenu(null);
      setRowNote(null);
      try {
        const raw = await post<unknown>(`${SESSIONS}/${encodeURIComponent(id)}/archive`);
        archived(id, archiveSummaryError(raw));
      } catch (e) {
        setRowNote(errorWords(e, ARCHIVE_FAILED));
      }
    },
    [archived],
  );

  const rowOpenTerminal = useCallback(
    async (id: string) => {
      setRowMenu(null);
      setRowNote(null);
      try {
        const res = await post<{ terminal_id?: string }>(`${SESSIONS}/${encodeURIComponent(id)}/open-terminal`);
        const tid = typeof res?.terminal_id === "string" ? res.terminal_id : "";
        if (tid) onOpenedTerminal(tid);
        else setRowNote("The terminal did not open.");
      } catch (e) {
        setRowNote(errorWords(e, "The terminal did not open."));
      }
    },
    [onOpenedTerminal],
  );

  const unarchive = useCallback(
    async (id: string) => {
      setRowMenu(null);
      setRowNote(null);
      try {
        const raw = await post<unknown>(`${SESSIONS}/${encodeURIComponent(id)}/unarchive`);
        setArchivedRows((prev) => (prev ? prev.filter((s) => s.id !== id) : prev));
        const back = decodeCodeSession(raw);
        if (back) noteChanged({ ...back, archivedAt: "" });
        readList();
      } catch (e) {
        setRowNote(errorWords(e, "The session was not brought back."));
      }
    },
    [noteChanged, readList],
  );

  /** Open a conversation the tool saved by itself: a new session on it. */
  const openSaved = useCallback(
    async (d: DiskSession) => {
      setRowNote(null);
      try {
        const raw = await post<unknown>(SESSIONS, {
          harness: d.tool,
          folder: d.folder,
          harness_session_id: d.toolSessionId,
        });
        const made = decodeCodeSession(raw);
        if (!made) throw new Error("The saved conversation could not be opened.");
        const k = folderKey(d.folder);
        setDisk((prev) => {
          const cur = prev[k];
          if (!cur || cur.state !== "ok") return prev;
          return { ...prev, [k]: { state: "ok", rows: cur.rows.filter((x) => x.toolSessionId !== d.toolSessionId) } };
        });
        noteChanged(made);
        open(made.id);
      } catch (e) {
        setRowNote(errorWords(e, "The saved conversation could not be opened."));
      }
    },
    [noteChanged, open],
  );

  /** v1.420.0 (C4.10): Rename — PATCH the title; the daemon's answer is the
   *  row. An empty or unchanged name saves nothing. */
  const saveRename = useCallback(
    async (id: string, text: string, was: string) => {
      if (renameOpenRef.current !== id) return;
      renameOpenRef.current = null;
      setRenaming(null);
      const title = text.trim();
      if (!title || title === was.trim()) return;
      setRowNote(null);
      try {
        const raw = await patch<unknown>(`${SESSIONS}/${encodeURIComponent(id)}`, { title });
        const next = decodeCodeSession(raw);
        if (next) {
          noteChanged(next);
          setArchivedRows((prev) => (prev ? prev.map((x) => (x.id === id ? { ...x, ...next } : x)) : prev));
        } else readList();
      } catch (e) {
        setRowNote(errorWords(e, "The new name did not save."));
      }
    },
    [noteChanged, readList],
  );

  /** v1.420.0 (C4.10): Delete — two presses (ConfirmButton), the existing
   *  DELETE route. A session whose reply is running is refused by the daemon
   *  (409) and its words are said here; nothing leaves the list then. */
  const deleteSession = useCallback(
    async (id: string) => {
      setRowNote(null);
      try {
        await del<unknown>(`${SESSIONS}/${encodeURIComponent(id)}`);
        setRowMenu(null);
        setSessions((prev) => (prev ? prev.filter((x) => x.id !== id) : prev));
        setArchivedRows((prev) => (prev ? prev.filter((x) => x.id !== id) : prev));
        if (openId === id) open(null);
      } catch (e) {
        setRowMenu(null);
        setRowNote(errorWords(e, "The session was not deleted."));
      }
    },
    [openId, open],
  );

  /** v1.441.0: a branch was made (from a message, or the row's "Branch"):
   *  the list reads it and the page opens it, its message in the box. */
  const [draftFor, setDraftFor] = useState<{ id: string; text: string; note: string } | null>(null);
  useEffect(() => {
    // Words parked for a branch are for opening THAT branch only.
    setDraftFor((cur) => (cur && cur.id !== openId ? null : cur));
  }, [openId]);
  const branched = useCallback(
    (a: BranchAnswer) => {
      setDraftFor({ id: a.sessionId, text: a.text, note: a.note });
      readList();
      open(a.sessionId);
    },
    [readList, open],
  );
  const rowBranch = useCallback(
    async (id: string) => {
      setRowMenu(null);
      setRowNote(null);
      try {
        const a = decodeBranchAnswer(await post<unknown>(`${SESSIONS}/${encodeURIComponent(id)}/fork`, {}));
        if (!a) throw new Error("The branch could not be made.");
        branched(a);
      } catch (e) {
        setRowNote(errorWords(e, "The branch could not be made."));
      }
    },
    [branched],
  );

  /** v1.420.0 (C4.10): the search — a title or a folder (its path or its
   *  last part) holding every typed word, case ignored. Blank: everything. */
  const matches = useCallback(
    (s: CodeSession) => {
      const words = query.toLowerCase().split(/\s+/).filter(Boolean);
      if (!words.length) return true;
      const hay = `${sessionTitle(s, tools)} ${s.folder}`.toLowerCase();
      return words.every((w) => hay.includes(w));
    },
    [query, tools],
  );
  const groups = useMemo(() => groupByFolder((sessions ?? []).filter(matches)), [sessions, matches]);
  // v1.426.0 (E5): the settings of the session open in front, when it is one
  // of `folder`'s — what "Use for new sessions in this folder" saves (Full
  // access never: a folder does not keep it).
  const frontDefaults = (folder: string): ToolDefaults | null => {
    const s = sessions?.find((x) => x.id === openId) ?? null;
    if (!s || !s.tool || folderKey(s.folder) !== folderKey(folder)) return null;
    const t = tools?.find((x) => x.id === s.tool) ?? null;
    const dangerous = !!t?.modes.some((m) => m.id === s.mode && m.dangerous);
    return {
      harness: s.tool,
      ...(s.model ? { model: s.model } : {}),
      ...(s.effort ? { effort: s.effort } : {}),
      ...(s.mode && !dangerous ? { mode: s.mode } : {}),
    };
  };
  const archivedGroups = useMemo(
    () => groupByFolder((archivedRows ?? []).filter(matches), { archived: true }),
    [archivedRows, matches],
  );

  const row = (s: CodeSession, archivedRow: boolean) => (
    <li key={s.id} className="group relative">
      {renaming?.id === s.id ? (
        <input
          autoFocus
          aria-label="Session name"
          data-testid={`code-row-rename-input-${s.id}`}
          value={renaming.text}
          maxLength={120}
          onChange={(e) => setRenaming({ id: s.id, text: e.target.value })}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              void saveRename(s.id, renaming.text, s.title);
            } else if (e.key === "Escape") {
              e.preventDefault();
              e.stopPropagation();
              renameOpenRef.current = null;
              setRenaming(null);
            }
          }}
          onBlur={() => void saveRename(s.id, renaming.text, s.title)}
          className="field w-full py-1 text-[13px]"
        />
      ) : (
      <button
        type="button"
        data-testid={`code-row-${s.id}`}
        data-source={s.source || undefined}
        data-archived={archivedRow ? "true" : undefined}
        aria-current={openId === s.id ? "true" : undefined}
        onClick={() => open(s.id)}
        className={`flex w-full flex-col rounded-lg py-1.5 pl-2 pr-8 text-left transition-colors ${
          openId === s.id ? "bg-white/[0.07]" : "hover:bg-white/[0.04]"
        }`}
      >
        <span className="flex min-w-0 items-center gap-1.5 text-[13px] text-zinc-200">
          {s.running ? (
            <Loader2 size={11} className="shrink-0 animate-spin text-zinc-400 motion-reduce:animate-none" aria-label={statusWords("working").word} />
          ) : null}
          {s.waiting ? <ShieldAlert size={11} className="shrink-0 text-tone-warn" aria-label={statusWords("waiting").word} /> : null}
          {s.lastError ? <AlertTriangle size={11} className="shrink-0 text-tone-warn" aria-label="Last reply failed" /> : null}
          {/* v1.422.0 (B4): kept open because its tool runs a program it
              started (a dev server); the session's own line says how to stop it. */}
          {(s.programs?.length ?? 0) > 0 ? (
            <span data-testid={`code-row-keeping-${s.id}`} title={s.keepNote || undefined} className="inline-flex shrink-0">
              <Server size={11} className="text-zinc-400" aria-label="Kept open: it runs a program" />
            </span>
          ) : null}
          <span className="truncate">{sessionTitle(s, tools)}</span>
        </span>
        <span data-testid={`code-row-line-${s.id}`} className="truncate text-[11px] text-zinc-500">
          {sessionRowLine(s, tools)}
          {s.source === "disk" ? ` · saved by ${toolLabel(s.tool, tools)}` : ""}
        </span>
      </button>
      )}
      <div ref={rowMenu === s.id ? rowMenuBox : undefined} className="contents">
      <button
        ref={rowMenu === s.id ? rowMenuButton : undefined}
        type="button"
        aria-label={`More for ${sessionTitle(s, tools)}`}
        aria-haspopup="menu"
        aria-expanded={rowMenu === s.id}
        data-testid={`code-row-more-${s.id}`}
        onClick={() => setRowMenu((cur) => (cur === s.id ? null : s.id))}
        className={`absolute right-1 top-1.5 grid h-6 w-6 place-items-center rounded-md text-zinc-500 hover:bg-white/[0.06] hover:text-zinc-200 ${
          rowMenu === s.id ? "" : `${MENU_BUTTON_HOVER_FADE} [@media(hover:hover)]:group-hover:opacity-100`
        } focus-visible:opacity-100`}
      >
        <MoreHorizontal size={13} />
      </button>
      {rowMenu === s.id ? (
        <div role="menu" className="absolute right-1 top-full z-20 w-48 rounded-xl bg-ink-800 p-1 shadow-card">
          {archivedRow ? (
            <button
              type="button"
              role="menuitem"
              data-testid={`code-row-unarchive-${s.id}`}
              onClick={() => void unarchive(s.id)}
              className="flex w-full rounded-lg px-2.5 py-1.5 text-left text-[13px] text-zinc-300 hover:bg-white/[0.06]"
            >
              Bring back
            </button>
          ) : null}
          <button
            type="button"
            role="menuitem"
            data-testid={`code-row-open-terminal-${s.id}`}
            onClick={() => void rowOpenTerminal(s.id)}
            className="flex w-full rounded-lg px-2.5 py-1.5 text-left text-[13px] text-zinc-300 hover:bg-white/[0.06]"
          >
            {OPEN_IN_TERMINAL}
          </button>
          {/* v1.441.0: a new session with this whole conversation (only where
              the tool can branch one; absent from an older Iron Jarvis). */}
          {s.canBranch && !archivedRow ? (
            <button
              type="button"
              role="menuitem"
              data-testid={`code-row-branch-${s.id}`}
              title={BRANCH_ROW_TITLE}
              onClick={() => void rowBranch(s.id)}
              className="flex w-full rounded-lg px-2.5 py-1.5 text-left text-[13px] text-zinc-300 hover:bg-white/[0.06]"
            >
              {BRANCH_ROW}
            </button>
          ) : null}
          {/* v1.439.0: the session open in front (its events are loaded) can be
              saved as Markdown: messages, replies and step titles. */}
          {exportRef.current?.id === s.id ? (
            <button
              type="button"
              role="menuitem"
              data-testid={`code-row-export-${s.id}`}
              onClick={() => void exportMarkdown(s)}
              className="flex w-full rounded-lg px-2.5 py-1.5 text-left text-[13px] text-zinc-300 hover:bg-white/[0.06]"
            >
              {EXPORT_MARKDOWN}
            </button>
          ) : null}
          {/* v1.453.0: Archive writes the session's summary and keeps it. */}
          {!archivedRow && s.source !== "disk" ? (
            <button
              type="button"
              role="menuitem"
              data-testid={`code-row-archive-${s.id}`}
              disabled={s.running}
              title={s.running ? ARCHIVE_RUNNING_TITLE : ARCHIVE_ROW_TITLE}
              onClick={() => void rowArchive(s.id)}
              className="flex w-full rounded-lg px-2.5 py-1.5 text-left text-[13px] text-zinc-300 hover:bg-white/[0.06] disabled:opacity-50"
            >
              {ARCHIVE_ROW}
            </button>
          ) : null}
          {/* v1.420.0 (fix plan C4.10): Rename in the row; Delete takes two
              presses. */}
          <button
            type="button"
            role="menuitem"
            data-testid={`code-row-rename-${s.id}`}
            onClick={() => {
              setRowMenu(null);
              renameOpenRef.current = s.id;
              setRenaming({ id: s.id, text: s.title });
            }}
            className="flex w-full rounded-lg px-2.5 py-1.5 text-left text-[13px] text-zinc-300 hover:bg-white/[0.06]"
          >
            Rename
          </button>
          <div role="menuitem" data-testid={`code-row-delete-${s.id}`}>
            <ConfirmButton
              label="Delete"
              confirmLabel="Delete for good?"
              title={
                s.running
                  ? "A reply is running: stop it first (Iron Jarvis refuses to delete a working session)"
                  : "Delete this session from the list (press twice). The tool's own saved conversation stays."
              }
              className="w-full justify-start"
              onConfirm={() => deleteSession(s.id)}
            />
          </div>
        </div>
      ) : null}
      </div>
    </li>
  );

  /** One folder's saved conversations, under its sessions. */
  const savedFor = (folder: string) => {
    const k = folderKey(folder);
    const shown = diskOpen.has(k) || (openSessionFolder !== null && folderKey(openSessionFolder) === k);
    const st = disk[k];
    return (
      <div data-testid="code-saved" className="mt-0.5">
        <button
          type="button"
          aria-expanded={shown}
          data-testid={`code-saved-toggle-${k}`}
          onClick={() => {
            setDiskOpen((prev) => {
              const next = new Set(prev);
              if (next.has(k)) next.delete(k);
              else next.add(k);
              return next;
            });
            if (!diskOpen.has(k)) readDisk(folder);
          }}
          className="flex w-full items-center gap-1 rounded-md px-2 py-0.5 text-left text-[11px] text-zinc-600 hover:text-zinc-300"
        >
          <History size={11} aria-hidden="true" />
          {shown ? "Saved by the tool" : "Show the tool's saved conversations"}
        </button>
        {shown && st ? (
          st.state === "loading" ? (
            <p className="px-2 text-[11px] text-zinc-600">Reading…</p>
          ) : st.state === "error" ? (
            <p role="alert" className="px-2 text-[11px] text-tone-danger">
              {st.words}
            </p>
          ) : st.rows.length === 0 ? (
            <p data-testid="code-saved-none" className="px-2 text-[11px] text-zinc-600">
              No other saved conversations here.
            </p>
          ) : (
            <ul>
              {st.rows.map((d) => (
                <li key={`${d.tool}:${d.toolSessionId}`}>
                  <button
                    type="button"
                    data-testid={`code-disk-${d.toolSessionId}`}
                    onClick={() => void openSaved(d)}
                    title={`Open this conversation here (${toolLabel(d.tool, tools)} keeps it in its own saved sessions)`}
                    className="flex w-full flex-col rounded-lg py-1 pl-2 pr-2 text-left hover:bg-white/[0.04]"
                  >
                    <span className="truncate text-[12px] text-zinc-300">{d.title || "Untitled conversation"}</span>
                    <span data-testid={`code-disk-line-${d.toolSessionId}`} className="truncate text-[11px] text-zinc-600">
                      {diskRowLine(d, tools)}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )
        ) : null}
      </div>
    );
  };

  return (
    <div data-testid="build-code-view" className="flex flex-col gap-4 lg:flex-row">
      <nav
        aria-label="Sessions"
        data-testid="code-session-list"
        className="flex w-full max-h-64 shrink-0 flex-col rounded-2xl bg-ink-900/40 lg:w-64 lg:max-h-none lg:h-[calc(100vh-12rem-var(--ij-strip-h,0px))] lg:min-h-[480px]"
      >
        <div className="flex shrink-0 items-center gap-2 px-3 pb-1 pt-3">
          <span className="flex-1 text-[13px] text-zinc-400">{showArchived ? "Archived" : "Sessions"}</span>
          {!showArchived ? (
            <button
              type="button"
              data-testid="code-new"
              onClick={() => startNew(null)}
              className={QUIET_BTN}
            >
              <Plus size={13} aria-hidden="true" />
              New session
            </button>
          ) : null}
        </div>
        {(sessions?.length ?? 0) > 0 || (archivedRows?.length ?? 0) > 0 || query ? (
          // v1.420.0 (fix plan C4.10): search by title or folder. `isolate`
          // + `z-[1]` (the v1.313.0 rule): the icon paints over the field.
          <div className="relative isolate shrink-0 px-3 pb-1">
            <Search
              size={12}
              aria-hidden="true"
              className="pointer-events-none absolute left-5 top-1/2 z-[1] -translate-y-1/2 text-zinc-500"
            />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Escape" && query) {
                  e.preventDefault();
                  e.stopPropagation();
                  setQuery("");
                }
              }}
              placeholder="Search sessions…"
              aria-label="Search sessions"
              data-testid="code-session-search"
              className="field w-full py-1.5 pl-7 text-[12px]"
            />
          </div>
        ) : null}
        <div className="min-h-0 flex-1 overflow-y-auto px-1.5 pb-2">
          {query.trim() && !(showArchived ? archivedGroups : groups).length && (showArchived ? archivedRows : sessions)?.length ? (
            <p data-testid="code-search-none" className="px-2 py-2 text-[12px] text-zinc-500">
              No sessions match “{query.trim()}”.
            </p>
          ) : null}
          {showArchived ? (
            !archivedRows ? (
              <p className="px-2 py-2 text-[12px] text-zinc-500">Reading the archived sessions…</p>
            ) : archivedGroups.length === 0 ? (
              <p data-testid="code-archived-empty" className="px-2 py-2 text-[12px] text-zinc-500">
                Nothing is archived.
              </p>
            ) : (
              archivedGroups.map((g) => (
                <section key={g.folder} data-testid="code-folder-group" className="mt-2">
                  <h3 className="flex items-center gap-1.5 px-2 text-[12px] text-zinc-500" title={g.folder}>
                    <FolderOpen size={12} aria-hidden="true" />
                    <span className="truncate">{g.name}</span>
                  </h3>
                  <ul className="mt-0.5">{g.sessions.map((s) => row(s, true))}</ul>
                </section>
              ))
            )
          ) : listError && !sessions ? (
            <p role="alert" className="px-2 py-2 text-[12px] text-tone-danger">
              {listError}
            </p>
          ) : !sessions ? (
            <p className="px-2 py-2 text-[12px] text-zinc-500">Reading your sessions…</p>
          ) : groups.length === 0 ? (
            <p data-testid="code-list-empty" className="px-2 py-2 text-[12px] text-zinc-500">
              No sessions yet. Type a task to start {toolChoiceWords(tools)}.
            </p>
          ) : (
            groups.map((g) => (
              <section key={g.folder} data-testid="code-folder-group" className="mt-2">
                <h3 className="flex items-center gap-1.5 px-2 text-[12px] text-zinc-500" title={g.folder}>
                  <FolderOpen size={12} aria-hidden="true" />
                  <span className="min-w-0 flex-1 truncate">{g.name}</span>
                  {/* New session on THIS folder (the v1.378.0 rule); the
                      "No folder" group's starts with none (v1.412.0). */}
                  <button
                    type="button"
                    aria-label={g.noFolder ? "New session with no folder" : `New session in ${g.name}`}
                    title={g.noFolder ? "New session with no folder" : `New session in ${g.folder}`}
                    data-testid={g.noFolder ? "code-new-in-no-folder" : `code-new-in-${folderKey(g.folder)}`}
                    onClick={() => startNew(g.noFolder ? null : g.folder)}
                    className="grid h-5 w-5 shrink-0 place-items-center rounded-md text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-200 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60"
                  >
                    <Plus size={12} aria-hidden="true" />
                  </button>
                  {g.noFolder ? null : (
                    <FolderDefaultsButton
                      name={g.name}
                      open={defaultsFor === g.folder}
                      onToggle={() => setDefaultsFor((cur) => (cur === g.folder ? null : g.folder))}
                      testId={`code-folder-defaults-${folderKey(g.folder)}`}
                    />
                  )}
                </h3>
                {!g.noFolder && defaultsFor === g.folder ? (
                  <FolderDefaultsPanel
                    folder={g.folder}
                    view="tool"
                    describe={(d: ToolDefaults) =>
                      toolDefaultsWords(d, {
                        tool: (id) => tools?.find((t) => t.id === id)?.label ?? id,
                        model: (tid, id) => tools?.find((t) => t.id === tid)?.models.find((m) => m.id === id)?.label ?? id,
                        mode: (tid, id) => modeLabel(id, tools?.find((t) => t.id === tid) ?? null),
                      })
                    }
                    current={frontDefaults(g.folder)}
                    testId="code-folder-defaults-panel"
                  />
                ) : null}
                <ul className="mt-0.5">{g.sessions.map((s) => row(s, false))}</ul>
                {g.noFolder ? null : savedFor(g.folder)}
              </section>
            ))
          )}
          {exporting ? (
            <p role="status" data-testid="code-export-status" className="px-2 pt-2 text-[12px] text-zinc-500">
              {EXPORT_READING}
            </p>
          ) : null}
          {rowNote ? (
            <p role="alert" data-testid="code-row-note" className="px-2 pt-2 text-[12px] text-tone-danger">
              {rowNote}
            </p>
          ) : null}
        </div>
        <div className="shrink-0 px-3 pb-2">
          <button
            type="button"
            data-testid="code-archived-toggle"
            aria-pressed={showArchived}
            onClick={() => {
              setShowArchived((v) => !v);
              setRowMenu(null);
            }}
            className="flex items-center gap-1 text-[11px] text-zinc-600 hover:text-zinc-300"
          >
            <Archive size={11} aria-hidden="true" />
            {showArchived ? "Back to your sessions" : "Archived sessions"}
          </button>
        </div>
      </nav>

      <section
        aria-label="Session"
        data-testid="code-stage"
        data-code-fit=""
        className="min-w-0 flex-1 overflow-hidden rounded-2xl border border-white/[0.06] bg-ink-900/40"
        style={{ height: "calc(100vh - 12rem - var(--ij-strip-h, 0px))", minHeight: 480 }}
      >
        {creating || !openId ? (
          // v1.412.0: the composer at once — no form first. A folder
          // heading's "+" starts on that folder; New session on none.
          <StartComposer
            key={creating ? `new-${newOn.n}` : "empty"}
            tools={tools}
            toolsError={toolsError}
            modelsGaveUp={modelsGaveUp}
            onRereadModels={rereadModels}
            onPickerOpen={refreshTools}
            startFolder={creating ? newOn.folder : null}
            workRoot={workRoot}
            startAt={openSessionFolder ?? startFolder}
            onStarted={(s, words, files) => {
              noteChanged(s);
              setFirstFor({ id: s.id, text: words, files: files ?? [] });
              open(s.id);
            }}
          />
        ) : (
          <SessionPane
            key={openId}
            sessionId={openId}
            tools={tools ?? []}
            onPickerOpen={refreshTools}
            onChanged={noteOpen}
            onOpenedTerminal={onOpenedTerminal}
            onArchived={archived}
            onUnarchive={unarchive}
            firstMessage={firstFor && firstFor.id === openId ? firstFor.text : null}
            firstFiles={firstFor && firstFor.id === openId ? firstFor.files : null}
            onFirstSent={clearFirst}
            exportSource={exportRef}
            onBranched={branched}
            onOpenSession={open}
            initialDraft={draftFor && draftFor.id === openId ? draftFor : null}
          />
        )}
      </section>
    </div>
  );
}

export default BuildCodeView;
