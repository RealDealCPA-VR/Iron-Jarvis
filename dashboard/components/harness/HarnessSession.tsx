"use client";

/**
 * One IJ harness SESSION on Build (v1.369.0, plan Phase 6): its header, its
 * conversation, the question dock, its composer, and the panes beside it.
 *
 * THE CHAT PAGE'S OWN PIECES, never copies: the work line and its step rows
 * with their detail (WorkLine / LiveToolRows), the reply through the ONE
 * markdown renderer, what a reply changed (ReplyChanges), the receipt
 * (TurnReceipt), the dock (DockAsk — approvals, an app's questions, and now
 * the model's own `ask_user`), the permission chip, the model and reasoning
 * chips (components/chat/ModelMenu). The turn is the chat stream
 * (lib/useChatStream) pointed at `POST /build/sessions/{id}/turn`.
 *
 * THE DAEMON WRITES THE TRANSCRIPT. The screen never saves a session's
 * messages: it shows the question while the turn runs, re-reads the session
 * when the turn ends, and adopts the lines the daemon wrote. A turn already
 * running when the session opens is REJOINED (`GET /chat/turns/{id}/stream`).
 * Stop and steer reuse the chat routes; a steer note the turn never read goes
 * back in the composer, even when the turn ended while nobody watched it.
 */

import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  ArrowUp,
  CircleAlert,
  ListChecks,
  FolderOpen,
  FolderX,
  GitBranch,
  GitFork,
  Loader2,
  MoreHorizontal,
  Pencil,
  Square,
} from "lucide-react";
import { branchLine } from "@/lib/buildGit";
import { ApiError, get, post, put } from "@/lib/api";
// Harness gaps (fix 2, fix 3): the chat's failure line and receipt facts.
import { FailureDetails, HarnessFailureLine } from "@/components/harness/HarnessFailure";
import { retryItemFor, retryOffered } from "@/lib/buildRetry";
import { RESUMED_LINE, carriedOn, carriedOnTurns, resumedFrom } from "@/lib/buildResume";
import { receiptFacts } from "@/lib/receiptFacts";
import { useDaemon } from "@/lib/daemon";
import { useModels } from "@/lib/useModels";
import { useChatStream, useLiveText, StreamError, type StreamSeed } from "@/lib/useChatStream";
// v1.384.0: where the user was — the session, its running reply, the scroll.
import {
  atBottomOf,
  dropTurn,
  keepScroll,
  keepTurn,
  keepView,
  scrollFor,
  turnFor,
  viewFor,
} from "@/lib/turnCache";
import { useLiveThinking } from "@/lib/liveThinking";
import { mintTurnId } from "@/lib/turnRejoin";
import { turnErrorTechnical, turnErrorText } from "@/lib/failureLine";
import { answeredModelName } from "@/lib/answeredModel";
import { composerChipClass, fitIconChip, fitWords, iconOnlyTitle } from "@/lib/composerChips";
import { HARNESS_FIT_ORDER, fitStepOn, useToolbarFit, type FitStep } from "@/lib/toolbarFit";
import { asPermissionMode } from "@/lib/permissionLevels";
import { phaseLabel } from "@/lib/stepWords";
import { collectDockAsks, dockAskKeyHint } from "@/lib/dockAsk";
import { answerElicitation, decideSampling } from "@/lib/mcpInteract";
import { isProviderDefaultModel, machineName, modelGroupName, providerDisplay, providerHeading } from "@/lib/onboarding";
import { reasoningDefaultOf } from "@/lib/reasoningChip";
import type { ModelOption } from "@/lib/types";
import { Markdown, MemoMarkdown } from "@/components/Markdown";
import { WorkLine, LiveToolRows } from "@/components/chat/WorkLine";
import { ReplyChanges } from "@/components/chat/ReplyChanges";
import { TurnReceipt } from "@/components/chat/TurnReceipt";
import { DoorsStrip } from "@/components/chat/DoorsStrip";
import { DockAsk } from "@/components/chat/DockAsk";
import { PermissionChip } from "@/components/chat/PermissionChip";
import { ThinkingDisclosure } from "@/components/chat/ThinkingDisclosure";
import { TurnClock } from "@/components/chat/TurnClock";
import { ConfirmButton, ErrorNote, OfflineHint } from "@/components/ui";
import type { ChangeUndoState } from "@/components/chat/ChangedFiles";
import type { AskAnswer } from "@/components/chat/AskUserCard";
import {
  ModelMenu,
  ReasoningChip,
  modelTriggerOf,
  reasoningLevelsOf,
  reasoningRowFor,
  splitChoice,
} from "@/components/chat/ModelMenu";
import { joinUndoByPath, normalizeFsPath, type UndoRowLike } from "@/components/chat/ArtifactsRail";
import { modelText } from "@/components/ModelRowBits";
import { companyMenu } from "@/lib/harnessModelMenu";
import { paneTurnWindow, paneWorkInput } from "@/components/terminal/paneWork";
import { PANE_COMPOSER_CARD, paneSendClass } from "@/components/terminal/paneChatLook";
import { HarnessPanes, type SessionTerminalApi } from "@/components/harness/HarnessPanes";
import type { LiveTimelineHandle } from "@/components/harness/HarnessTimeline";
import { createLiveTimeline } from "@/lib/buildTimeline";
import { AccountChip, AccountLimitCard, AccountList } from "@/components/harness/HarnessAccounts";
import { RewindControl } from "@/components/harness/RewindControl";
// v1.373.0: the conversation's own reading look (a centred column, 15 px).
import {
  HARNESS_COLUMN,
  HARNESS_COMPOSER_BOX,
  HARNESS_REPLY_PROSE,
  HARNESS_TRANSCRIPT_ROWS,
  HARNESS_USER_BUBBLE,
} from "@/components/harness/harnessLook";
import { LibraryCards } from "@/components/harness/LibraryCards";
// v1.392.0 (long jobs, Phase 3): a reply that made no progress says so (Stop
// the step / Stop), a reply that summarized itself says so, and the
// session's optional cap (set in ⋯, met with Keep going).
import { CapForm, CapMetLine, StallLine, SummarizedLine } from "@/components/harness/HarnessLongJobs";
import {
  STALL_FRAME,
  STEP_STOPPED_NOTE,
  SUMMARY_FRAME,
  decodeCapMet,
  decodeSessionCap,
  decodeStall,
  decodeSummarized,
  stopStep,
  type SessionCap,
  type StallNotice,
  type Summarized,
} from "@/lib/buildLongJobs";
import { LiveHelperRows, SavedHelperRows } from "@/components/harness/HarnessHelpers";
// v1.397.0: try one task on several models — the attempts' rows, their card
// (Keep) and the box's "Try on…" control.
import { AttemptsCardView, LiveAttemptRows } from "@/components/harness/HarnessAttempts";
import { TryOnControl } from "@/components/harness/HarnessTryOn";
// v1.374.0 (part 2, Phase 3): the task list under the reply, how full the
// conversation is (and Compact), and "the user is looking at this session".
import { LivePlan, SettledPlan } from "@/components/harness/HarnessPlan";
import { ContextMeter } from "@/components/harness/ContextMeter";
import { CompactionCard } from "@/components/chat/CompactionCard";
import {
  compactSession,
  compactedNote,
  compactionCardInfo,
  decodeCompaction,
  latestContext,
  offersCompact,
  readingIsBeforeSummary,
  replyPlan,
} from "@/lib/buildContext";
import { LOOKING_BEAT_MS, noteLooking, stopLooking } from "@/lib/buildWatching";
import { applyHelperFrame, helperCallIds, type HelperView } from "@/lib/buildHelpers";
import { applyAttemptFrame, tryOnBody, type AttemptView, type TryOnPick } from "@/lib/buildAttempts";
import {
  AttachButton,
  AttachmentChips,
  SentAttachments,
  pastedFiles,
  useHarnessAttachments,
  type HarnessFile,
} from "@/components/harness/HarnessAttachments";
import { previewAddressFor } from "@/components/harness/BrowserPreview";
// v1.377.0 (part 2, Phase 6): Next step, Branch from a message, the keys,
// and what a rewind hands back to the box.
import {
  BRANCH_LABEL,
  BRANCH_TITLE_QUESTION,
  BRANCH_TITLE_REPLY,
  atFileFromPath,
  branchSession,
  branchedCodeWords,
  branchedFromWords,
  fetchNextSteps,
  fileFromPath,
  withNextStep,
  type BranchAt,
  type BranchDraft,
} from "@/lib/buildNext";
import { harnessKeysHint, useHarnessKeys } from "@/lib/harnessKeys";
import { isDesktopShell } from "@/lib/desktopShell";
import type { RewindExtras } from "@/lib/buildRewind";
import { finishedPageSteps } from "@/lib/agentBrowser";
import {
  AtMenu,
  PickedChips,
  RewindList,
  SentPicks,
  SlashInvoking,
  SlashMenu,
} from "@/components/harness/HarnessComposerMenus";
import { slashTokenAt, spliceToken, tokenAt } from "@/lib/slash";
import { atMenuOpen, slashMenuOpen } from "@/lib/composerMenus";
import { useProjectFilesState } from "@/lib/atMenuFiles";
import {
  atFileRows,
  decodeCommands,
  decodeSkills,
  INIT_PROMPT,
  leadingSlash,
  modelByName,
  pickedOf,
  queuedItemBody,
  queuedWords,
  READ_LIMIT_WORDS,
  READ_PER_MESSAGE,
  resolveTyped,
  sessionCommands,
  sessionUsageWords,
  skillFields,
  slashRows,
  withFilesRestored,
  withMessageRestored,
  type AtFile,
  type CommandId,
  type CustomCommand,
  type PickedSkill,
  type QueuedItem,
  type SkillOption,
  type SlashRow,
} from "@/lib/buildComposer";
import {
  accountMoveLine,
  armResumeWhenReady,
  cancelResumeWhenReady,
  chooseAccountAndContinue,
  decodeAccountLimit,
  decodeAccountMove,
  decodeAccountWait,
  fetchAccounts,
  limitCardWords,
  makeDefaultAccount,
  sessionAccount,
  setSessionAccount,
  vendorOf,
  type AccountLimit,
  type AccountsView,
  type BuildAccount,
} from "@/lib/buildAccounts";
import { useVisibleInterval } from "@/lib/useVisibleInterval";
import {
  answerQuestion,
  archiveSession,
  decodeAskResolved,
  decodeAskUser,
  decodeFinish,
  decodeSessionView,
  decodeStepLimit,
  deleteSession,
  KEEP_GOING_TEXT,
  stepLimitWords,
  getSession,
  isMissing,
  NO_FOLDER_NAME,
  noFolderEmptyWords,
  patchSession,
  planFromCards,
  shownMessages,
  readSentSteers,
  unreadSteersFor,
  withSteersRestored,
  writeSentSteers,
  type AskUserAsk,
  type BuildMessage,
  type BuildSessionView,
} from "@/lib/buildSessions";

/** How many times a finished turn's reply is looked for before giving up. */
const REREAD_TRIES = 4;
const REREAD_GAP_MS = 500;
/** v1.402.0: how long a finished plan turn's screen waits for the act turn
 *  the daemon starts by itself (the view's `planRun`) before it stops
 *  looking — a few seconds; the daemon starts it in well under one. */
export const PLAN_RUN_TRIES = 20;
export const PLAN_RUN_GAP_MS = 300;
/** The chat's own cap on waiting messages (routes/chat MAX_THREAD_QUEUE). */
const MAX_QUEUED = 3;
/** v1.377.0: how often a session read mid-Compact is read again. */
export const COMPACT_REREAD_MS = 2000;

function sleep(ms: number) {
  return new Promise((r) => setTimeout(r, ms));
}

/** What rides a NEW turn beside its words: the uploads (v1.371.0), the files
 *  picked with "@" and the skill picked with "/" (v1.373.0). Kept whole so a
 *  message that never started goes back in the box with all of it. */
interface TurnExtras {
  attachments: HarnessFile[];
  files: AtFile[];
  skill: PickedSkill | null;
  /** v1.379.0: one of Claude Code's own commands, typed "/name words" (the
   *  turn's `text` is then its words), and what was typed — what goes back
   *  in the box when the message never starts. */
  command?: string;
  typed?: string;
  /** v1.397.0: the models the "Try on…" control picked for this message. */
  tryOn?: TryOnPick[];
}

/** The live reply: subscribed HERE, so the session does not re-render per token. */
function LiveText({ stream }: { stream: ReturnType<typeof useChatStream> }) {
  const text = useLiveText(stream);
  const thinking = useLiveThinking(stream);
  return (
    <>
      {thinking ? <ThinkingDisclosure text={thinking} live /> : null}
      {text ? (
        <div data-testid="harness-live-text" className={HARNESS_REPLY_PROSE}>
          <Markdown content={text} />
        </div>
      ) : null}
      {!text && !stream.tools.length ? (
        <div className="flex items-center gap-2 text-[13px] text-zinc-500">
          <Loader2 size={12} className="animate-spin" />
          {stream.prepStep ? phaseLabel(stream.prepStep) : "Thinking…"}
          <TurnClock since={stream.startedAt ?? null} />
        </div>
      ) : null}
    </>
  );
}

/** v1.377.0 (Phase 6): "Branch from here" on a message — one press starts a
 *  NEW session (nothing of this one changes, so no confirm). Quiet until the
 *  message is hovered or the control has focus, like Rewind. */
function BranchButton({
  at,
  turnId,
  working,
  disabled,
  onBranch,
}: {
  at: BranchAt;
  turnId: string;
  working: boolean;
  disabled: boolean;
  onBranch: (turnId: string, at: BranchAt) => void;
}) {
  return (
    <button
      type="button"
      data-testid={`harness-branch-${at}`}
      data-turn={turnId}
      disabled={disabled || working}
      title={at === "reply" ? BRANCH_TITLE_REPLY : BRANCH_TITLE_QUESTION}
      onClick={() => onBranch(turnId, at)}
      className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[12px] text-zinc-500 opacity-0 transition-opacity hover:bg-white/[0.06] hover:text-zinc-200 focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 disabled:cursor-not-allowed group-hover/msg:opacity-100 [@media(hover:none)]:opacity-100"
    >
      {working ? <Loader2 size={12} className="animate-spin" aria-hidden /> : <GitFork size={12} aria-hidden />}
      {BRANCH_LABEL}
    </button>
  );
}

/** The confirm before an Undo: this session's own journal, its newest write. */
export function undoPrompt(kind: string | undefined, base: string): string {
  return kind === "file_delete" || kind === "files_delete"
    ? `Undo it? ${base} was created by this session and will be removed.`
    : `This restores ${base} to its content from before this session's newest write to it. Continue?`;
}

function baseName(p: string): string {
  const parts = p.split(/[\\/]/).filter(Boolean);
  return parts[parts.length - 1] ?? p;
}

/** Where a session runs when it has NO account (a local model or an API
 *  key — Phase 5 gives no place words): the model server's name for a
 *  local model (lib/onboarding machineName, else the group's name), the
 *  demo model by its name, the provider's group name for an API key. */
export function placeWords(provider: string, model: string, models: readonly ModelOption[]): string {
  if (!provider) return "";
  if (provider === "mock") return providerDisplay("mock");
  const row =
    models.find((m) => m.provider === provider && (!model || m.model === model)) ??
    models.find((m) => m.provider === provider);
  if (row && row.kind === "local" && row.on_this_network !== false) {
    return machineName(provider, { name: row.name, model, catalog: models }) || modelGroupName(row);
  }
  return row ? modelGroupName(row) : providerHeading(provider) || provider;
}

/** How often the account list is read again while the chip's list is open
 *  (the daemon answers from its cached snapshot). */
export const ACCOUNTS_POLL_MS = 5000;
/** v1.393.0: how often the account list is read again while a reply WORKS —
 *  the usage numbers the chip and the Timeline show move with every model
 *  call (the daemon answers from its cached snapshot: never a vendor call). */
export const USAGE_POLL_MS = 60_000;

export function HarnessSession({
  threadId,
  terminal,
  listRunning,
  onChanged,
  onGone,
  onBranched,
  initialDraft,
  onOpenSession,
  onNewSession,
  onFindSessions,
}: {
  threadId: string;
  terminal: SessionTerminalApi;
  /** The session list says a turn runs (a resumed wait, another window). */
  listRunning: boolean;
  /** Something the left pane shows changed (title, a new turn). */
  onChanged: () => void;
  /** The session was deleted or archived from here. */
  onGone: () => void;
  /** v1.377.0: "Branch from here" started a NEW session (open it; a question
   *  branch hands its words and files to the new session's box). */
  onBranched?: (id: string, draft: BranchDraft | null) => void;
  /** v1.377.0: what goes in this session's box when it opens (a session just
   *  branched from a question). */
  initialDraft?: BranchDraft | null;
  /** v1.377.0: open another session (the "Branched from" line). */
  onOpenSession?: (id: string) => void;
  /** v1.379.0: "/clear" — start a new session on `folder` ("" = no folder);
   *  resolves to the refusal in words, or null. */
  onNewSession?: (folder: string) => Promise<string | null>;
  /** v1.379.0: "/resume" — the caret in the session search. */
  onFindSessions?: () => void;
}) {
  const daemon = useDaemon();
  const catalog = useModels();
  // v1.384.0: WHERE THE USER WAS (lib/turnCache) — the session last read
  // and its running reply as this window last drew it (this component is
  // keyed by the session: these are read once, at mount). Drawn on the
  // FIRST render, then the reply carries on from its last frame.
  const [kept] = useState(() => turnFor(threadId));
  const stream = useChatStream({ textInState: false, seed: kept?.stream ?? null });
  // v1.390.0 (the Timeline): the running reply folded from its frames — a
  // store, never React state (the Timeline tab reads it once a second while
  // it is shown); carried on from where this window left it (lib/turnCache).
  const [timeline] = useState(() => createLiveTimeline(kept?.page.timeline));
  const [view, setView] = useState<BuildSessionView | null>(() => viewFor<BuildSessionView>(threadId));
  const viewRef = useRef<BuildSessionView | null>(null);
  viewRef.current = view;
  const [loadError, setLoadError] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Harness gaps (fix 2): the live failure's technical text (for its Details
  // fold) and the turn it belongs to — kept for THIS `error` only.
  const [errorTech, setErrorTech] = useState<{ text: string; technical: string; turn: string | null } | null>(null);
  // v1.384.0 review: the question the user SENT here rides the kept reply
  // (lib/turnCache) — the session view a return paints was read before the
  // send and nothing re-reads it mid-turn, so without this the question
  // popped in above the live reply when the open read landed (a jump).
  const [pending, setPending] = useState<BuildMessage | null>(
    () => (kept?.page.pending as BuildMessage | null | undefined) ?? null,
  );
  const pendingRef = useRef<BuildMessage | null>(pending);
  pendingRef.current = pending;
  const [questions, setQuestions] = useState<AskUserAsk[]>(() => (kept?.page.questions as AskUserAsk[] | undefined) ?? []);
  const [steerNotes, setSteerNotes] = useState<string[]>(() => kept?.page.steerNotes ?? []);
  // v1.372.0: the helper agents the running turn started, one row each
  // (its `helper` frames; lib/buildHelpers). The reply keeps them after.
  const [helpers, setHelpers] = useState<HelperView[]>(() => (kept?.page.helpers as HelperView[] | undefined) ?? []);
  // v1.397.0: the attempts the running turn started, one row each (its
  // `attempt` frames; lib/buildAttempts). The reply keeps them after, as a card.
  const [attempts, setAttempts] = useState<AttemptView[]>(() => (kept?.page.attempts as AttemptView[] | undefined) ?? []);
  // v1.384.0: the frames' folds of THIS session (helpers, questions) as the
  // running reply builds them — the truth the state above is drawn from, so a
  // replay folds silently and is drawn ONCE (at `caught_up`), and the kept
  // reply carries them. Every write goes through the setters below.
  const pageRef = useRef<{ helpers: HelperView[]; questions: AskUserAsk[]; attempts: AttemptView[] }>({
    helpers,
    questions,
    attempts,
  });
  const replayingRef = useRef(false);
  const steerRef = useRef<string[]>(steerNotes);
  const setPageHelpers = useCallback((next: HelperView[]) => {
    pageRef.current = { ...pageRef.current, helpers: next };
    setHelpers(next);
  }, []);
  const setPageAttempts = useCallback((next: AttemptView[]) => {
    pageRef.current = { ...pageRef.current, attempts: next };
    setAttempts(next);
  }, []);
  const setPageQuestions = useCallback((f: (prev: AskUserAsk[]) => AskUserAsk[]) => {
    const next = f(pageRef.current.questions);
    pageRef.current = { ...pageRef.current, questions: next };
    setQuestions(next);
  }, []);
  const setSteers = useCallback((next: string[]) => {
    steerRef.current = next;
    setSteerNotes(next);
  }, []);
  // v1.373.0: a waiting message keeps its own skill and files.
  const [queued, setQueued] = useState<QueuedItem[]>([]);
  const turnRef = useRef<string | null>(kept?.turnId ?? null);
  const [turnId, setTurnId] = useState<string | null>(kept?.turnId ?? null);
  // v1.384.0: the session is being LEFT (this component unmounts): its
  // running reply is not over, so it is kept, never settled or dropped.
  const leavingRef = useRef(false);
  // v1.384.0: a `stream.run` is in flight (the cleanup closes only that),
  // and which run is the newest.
  const inFlightRef = useRef(false);
  const runGenRef = useRef(0);
  const [turnsEnded, setTurnsEnded] = useState(0);
  // v1.370.0 (Phase 7): rewinds done here — the panes and the library read
  // again after one, like after a reply.
  const [rewinds, setRewinds] = useState(0);
  const boxRef = useRef<HTMLTextAreaElement | null>(null);
  // v1.373.0 (part 2, Phase 2): "/" and "@" in the box. The caret and the
  // two Escapes are what the chat's token menus read (lib/composerMenus);
  // a pick leaves a chip: a skill for the next message, files of the folder.
  const [caret, setCaret] = useState(0);
  const [slashDismissed, setSlashDismissed] = useState(false);
  const [atDismissed, setAtDismissed] = useState(false);
  const [menuActive, setMenuActive] = useState(0);
  // v1.373.0 review: the box names the open list and its highlighted row
  // (aria-controls / aria-activedescendant) — a screen reader heard nothing.
  const menuIdBase = useId();
  const [skill, setSkill] = useState<PickedSkill | null>(null);
  const skillRef = useRef<PickedSkill | null>(null);
  skillRef.current = skill;
  // v1.397.0: the models the "Try on…" control picked for the NEXT message.
  const [tryOn, setTryOn] = useState<TryOnPick[]>([]);
  const tryOnRef = useRef<TryOnPick[]>([]);
  tryOnRef.current = tryOn;
  const [atFiles, setAtFiles] = useState<AtFile[]>([]);
  const atFilesRef = useRef<AtFile[]>([]);
  atFilesRef.current = atFiles;
  // v1.371.0: pictures and files for the next message (HarnessAttachments).
  // v1.373.0 review: no more files in all than one turn reads — the uploads
  // AND the "@" picks (the box used to take 20 pictures; the turn read 4).
  const att = useHarnessAttachments({
    max: READ_PER_MESSAGE,
    others: () => atFilesRef.current.length,
    words: READ_LIMIT_WORDS,
  });
  const [yourSkills, setYourSkills] = useState<SkillOption[] | null>(null);
  const [folderSkills, setFolderSkills] = useState<SkillOption[] | null>(null);
  const [folderSkillNote, setFolderSkillNote] = useState("");
  // v1.379.0: Claude Code's own custom commands for this session (GET
  // /build/sessions/{id}/commands — an older Iron Jarvis has none: []).
  const [customCommands, setCustomCommands] = useState<CustomCommand[] | null>(null);
  const [commandsNote, setCommandsNote] = useState("");
  // v1.379.0: "/usage" — what the session used, said in the conversation.
  const [usageNote, setUsageNote] = useState<string | null>(null);
  // v1.379.0: the three lists "/" reads, read ONCE per session — and awaited
  // by a send that starts with "/name" (a waiting message sent after a reload
  // never opened the menu that reads them, and would go as plain text).
  const listsPromise = useRef<Promise<{
    yours: SkillOption[];
    folder: SkillOption[];
    custom: CustomCommand[];
  }> | null>(null);
  function ensureSlashLists() {
    if (!listsPromise.current) {
      const id = encodeURIComponent(threadId);
      const noteOf = (r: unknown) => {
        const note = r && typeof r === "object" ? (r as { note?: unknown }).note : "";
        return typeof note === "string" ? note : "";
      };
      listsPromise.current = Promise.all([
        get<unknown>("/skills")
          .then((r) => decodeSkills(r, "yours"))
          .catch(() => [] as SkillOption[])
          .then((s) => {
            setYourSkills(s);
            return s;
          }),
        get<unknown>(`/build/sessions/${id}/skills`)
          .then((r) => {
            setFolderSkillNote(noteOf(r));
            return decodeSkills(r, "folder");
          })
          .catch(() => [] as SkillOption[])
          .then((s) => {
            setFolderSkills(s);
            return s;
          }),
        get<unknown>(`/build/sessions/${id}/commands`)
          .then((r) => {
            setCommandsNote(noteOf(r));
            return decodeCommands(r);
          })
          .catch(() => [] as CustomCommand[])
          .then((c) => {
            setCustomCommands(c);
            return c;
          }),
      ]).then(([yours, folder, custom]) => ({ yours, folder, custom }));
    }
    return listsPromise.current;
  }
  const [rewindOpen, setRewindOpen] = useState(false);
  const transcriptRef = useRef<HTMLDivElement | null>(null);
  const [modelMenuOpen, setModelMenuOpen] = useState(false);
  // v1.400.0: the box's toolbar is ONE ROW whatever model is picked, as the
  // chat's is (lib/toolbarFit): it gives up space one step at a time
  // (HARNESS_FIT_ORDER: "Try on…", then the model's name truncates, then
  // "Plan first", the permission level, the reasoning words); only a box too
  // narrow even for that wraps as before.
  const [harnessFitRef, harnessFit] = useToolbarFit(HARNESS_FIT_ORDER);
  const harnessOn = (step: FitStep) => fitStepOn(HARNESS_FIT_ORDER, harnessFit, step);
  const [menuOpen, setMenuOpen] = useState(false);
  const [renaming, setRenaming] = useState<string | null>(null);
  // v1.392.0 (long jobs, Phase 3): the running reply's stall notice (the
  // daemon's `stall` frame; cleared by its `over` and by the turn's end), a
  // Stop the step under way and what it said, the summary the running reply
  // wrote of its own older rounds, and the ⋯ menu's cap form.
  const [stall, setStall] = useState<StallNotice | null>(null);
  const [stallStopping, setStallStopping] = useState(false);
  const [stallNote, setStallNote] = useState<string | null>(null);
  const [liveSummarized, setLiveSummarized] = useState<Summarized | null>(null);
  const [capOpen, setCapOpen] = useState(false);
  const [capBusy, setCapBusy] = useState(false);
  // Accounts (Phase 5): undefined = not read yet, null = this Iron Jarvis has
  // none (the chip is hidden).
  const [accounts, setAccounts] = useState<AccountsView | null | undefined>(undefined);
  const [accountsOpen, setAccountsOpen] = useState(false);
  // The turn running now reported an account limit: its error frame is the
  // same news in other words, so the card says it once.
  const limitSeenRef = useRef(false);
  const [accountNote, setAccountNote] = useState<string | null>(null);
  const [accountBusy, setAccountBusy] = useState(false);
  const [limit, setLimit] = useState<AccountLimit | null>(null);
  const [waiting, setWaiting] = useState(false);
  const [choosing, setChoosing] = useState(false);
  const [limitError, setLimitError] = useState<string | null>(null);
  // v1.374.0: Compact — a press under way, what it said, and the summary card.
  const [compacting, setCompacting] = useState(false);
  const [compactNote, setCompactNote] = useState<string | null>(null);
  const [summaryOpen, setSummaryOpen] = useState(false);
  const compactingRef = useRef(false);
  // v1.377.0 (Phase 6): a Compact the daemon is running for this session
  // that this screen did not start (pressed, then the user left and came
  // back): the view says so, and the box waits the same way.
  const elsewhereCompacting = !!view?.compacting && !compacting;
  // v1.377.0: the words of a rewind of committed work (the commits stayed).
  const [rewindNote, setRewindNote] = useState<string | null>(null);
  // v1.377.0: Next step — up to two suggested follow-ups after a reply (the
  // chat's, off unless switched on); a press fills the box, never sends.
  const [nextSteps, setNextSteps] = useState<string[]>([]);
  // v1.377.0: Branch from a message — a press under way, and its refusal.
  const [branching, setBranching] = useState<string | null>(null);
  const [branchError, setBranchError] = useState<string | null>(null);
  const nextGenRef = useRef(0);
  const busy = sending || stream.streaming;

  // v1.374.0: while this session is on screen in front and its window has
  // focus, the bell knows the user is LOOKING at it (lib/buildWatching): a
  // long turn that ends here says nothing — they watched it end.
  useEffect(() => {
    const look = () => {
      if (document.visibilityState === "visible" && document.hasFocus()) noteLooking(threadId);
      else stopLooking();
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
      stopLooking();
    };
  }, [threadId]);

  // ------------------------------------------------------------- reading
  /** Take a session view the daemon answered with: the view, and (from an
   *  Iron Jarvis that keeps them) its limit card and armed wait. */
  const adopt = useCallback((v: BuildSessionView) => {
    setView(v);
    setLoadError(null);
    if (v.knowsAccounts) {
      setLimit(decodeAccountLimit(v.accountLimit));
      setWaiting(!!decodeAccountWait(v.accountWait));
    }
  }, []);
  // v1.384.0: the view last drawn is what a return paints at once.
  useEffect(() => {
    if (view) keepView(threadId, view);
  }, [threadId, view]);

  const load = useCallback(async (): Promise<BuildSessionView | null> => {
    try {
      const v = await getSession(threadId);
      if (!v) throw new Error("The session could not be read.");
      adopt(v);
      return v;
    } catch (e) {
      if (isMissing(e)) {
        setLoadError("This session is gone.");
      } else {
        setLoadError(e instanceof Error ? e.message : String(e));
      }
      return null;
    }
  }, [threadId, adopt]);

  const refreshAccounts = useCallback(async () => {
    try {
      setAccounts(await fetchAccounts());
    } catch {
      setAccounts(null); // unreadable: no chip rather than a wrong one
    }
  }, []);

  // ---------------------------------------------------------- undo journal
  const [undoRows, setUndoRows] = useState<UndoRowLike[]>([]);
  const [undone, setUndone] = useState<Set<string>>(new Set());
  const refreshUndo = useCallback(async () => {
    try {
      const res = await get<{ actions: UndoRowLike[] }>(
        `/undo?session_id=${encodeURIComponent(view?.sessionId ?? `build:${threadId}`)}`,
      );
      setUndoRows(res.actions ?? []);
    } catch {
      /* no undo affordance, nothing broken */
    }
  }, [threadId, view?.sessionId]);
  const undoByPath = useMemo(() => joinUndoByPath(undoRows), [undoRows]);
  const undoFor = useCallback(
    (path: string): ChangeUndoState | null => {
      const row = undoByPath.get(normalizeFsPath(path));
      if (!row) return null;
      if (undone.has(row.action_id)) return { actionId: row.action_id, undoable: false, reason: "already undone" };
      return {
        actionId: row.action_id,
        undoable: row.undoable !== false,
        ...(row.undoable === false ? { reason: "this action has no safe inverse" } : {}),
      };
    },
    [undoByPath, undone],
  );
  const onUndo = useCallback(
    async (actionId: string, path: string) => {
      const row = undoByPath.get(normalizeFsPath(path));
      if (!window.confirm(undoPrompt(row?.kind, baseName(path)))) return;
      await post(`/undo/${encodeURIComponent(actionId)}`, {});
      setUndone((prev) => new Set(prev).add(actionId));
      void refreshUndo();
    },
    [undoByPath, refreshUndo],
  );
  useEffect(() => {
    if (view?.messages.some((m) => m.documents?.length)) void refreshUndo();
  }, [view?.messages, refreshUndo]);

  /** A rewind landed (v1.370.0): the DAEMON cut the transcript and put the
   *  files back, so read the session again; the message rewound to goes back
   *  in the box (unless the box already holds words), and the panes, the Undo
   *  rows and the library read again. v1.377.0 (Phase 6): its PICTURES and
   *  files go back with its words (the box says what did not fit), and a
   *  rewind of committed work says the commits stayed on the branch. */
  const onRewound = useCallback(
    async (question: string, extras?: RewindExtras) => {
      const v = await load();
      if (question) setInput((cur) => (cur.trim() ? cur : question));
      if (extras) {
        const folder = v?.folder ?? viewRef.current?.folder ?? "";
        const picks = extras.pickedFiles.map((p) => atFileFromPath(p, folder));
        if (picks.length) {
          const next = withFilesRestored(atFilesRef.current, picks);
          atFilesRef.current = next; // the uploads' room is counted against it at once
          setAtFiles(next);
        }
        if (extras.attachments.length) att.restore(extras.attachments.map(fileFromPath));
        // v1.377.0 review: its "/" skill too (one per message: a box that
        // already holds another keeps it and says which did not go back).
        if (extras.skill) {
          const back = extras.skill;
          const cur = skillRef.current;
          if (!cur) setSkill(back);
          else if (cur.name !== back.name || cur.source !== back.source) {
            const note = `The skill ${back.name} did not go back in the box: it already holds ${cur.name}.`;
            setError((e) => (e ? `${e} ${note}` : note));
          }
        }
        setRewindNote(extras.commitsNote || null);
      }
      setUndone(new Set());
      void refreshUndo();
      setRewinds((n) => n + 1);
      onChanged();
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [load, refreshUndo, onChanged],
  );

  // ----------------------------------------------------------- the turn
  /** v1.384.0: draw the session's own folds (pageRef) — at once while live,
   *  ONCE at `caught_up` while a replay is folded. */
  const drawPage = useCallback(() => {
    setHelpers(pageRef.current.helpers);
    setQuestions(pageRef.current.questions);
    setAttempts(pageRef.current.attempts);
  }, []);
  const onFrame = useCallback((event: string, data: Record<string, unknown>) => {
    if (event === "resume") {
      // A tagged follow (v1.384.0): what comes up to `caught_up` is folded,
      // not drawn; `resumed: false` is the whole turn — fold it from nothing.
      replayingRef.current = true;
      if (data.resumed !== true) {
        pageRef.current = { helpers: [], questions: [], attempts: [] };
        timeline.reset(turnRef.current ?? "");
      }
      return;
    }
    // v1.390.0: the Timeline folds what it reads (a no-op for the rest).
    timeline.fold(event, data);
    if (event === "caught_up") {
      replayingRef.current = false;
      drawPage();
      return;
    }
    const page = pageRef.current;
    if (event === "ask_user") {
      const ask = decodeAskUser(data);
      if (ask) pageRef.current = { ...page, questions: [...page.questions.filter((q) => q.id !== ask.id), ask] };
    } else if (event === "mcp_resolved") {
      const done = decodeAskResolved(data);
      if (done) {
        pageRef.current = {
          ...page,
          questions: page.questions.map((q) => (q.id === done.id ? { ...q, outcome: done.outcome } : q)),
        };
      }
    } else if (event === "account_limit") {
      const lim = decodeAccountLimit(data);
      if (lim) {
        limitSeenRef.current = true;
        setLimit(lim);
        setWaiting(false);
        setChoosing(false);
      }
      return;
    } else if (event === "helper") {
      pageRef.current = { ...page, helpers: applyHelperFrame(page.helpers, data) };
    } else if (event === "attempt") {
      // v1.397.0: an attempt's row (its state, steps, an approval card).
      pageRef.current = { ...page, attempts: applyAttemptFrame(page.attempts, data) };
    } else if (event === STALL_FRAME) {
      // v1.392.0: no progress for ten minutes (once per episode), or the
      // progress that ended it.
      const s = decodeStall(data);
      if (s === "over") setStall(null);
      else if (s) {
        setStall(s);
        setStallNote(null);
      }
      return;
    } else if (event === SUMMARY_FRAME) {
      // v1.392.0: the reply summarized its own older rounds to fit the model.
      const s = decodeSummarized(data);
      if (s) setLiveSummarized(s);
      return;
    } else {
      return;
    }
    if (!replayingRef.current) drawPage();
  }, [drawPage, timeline]);

  /** After a turn: adopt what the daemon wrote, put unread steer notes back
   *  in the box, and send what was waiting. */
  const settle = useCallback(
    async (finished: string, unread: readonly string[], clean: boolean) => {
      // THE SESSION VIEW IS THE TRUTH. The daemon writes the reply line —
      // and the limit card a turn ended on — BEFORE it passes the turn's
      // last frame on (v1.369.0 review, routes/build `recorded_turn`), so the
      // first read has both. It reads again only while the session still
      // says the turn runs and holds no reply for it (a turn stopped before
      // its first word writes none).
      let v: BuildSessionView | null = null;
      for (let i = 0; i < REREAD_TRIES; i += 1) {
        v = await load();
        if (!v || !v.running) break;
        if (v.messages.some((m) => m.role === "assistant" && m.turnId === finished)) break;
        if (i < REREAD_TRIES - 1) await sleep(REREAD_GAP_MS);
      }
      // A turn that ended on a limit changed what the account list says:
      // read it again, so the chip says so without opening the list.
      // v1.393.0: every turn — its model calls moved the account's usage,
      // which the chip and the Timeline show (the cached snapshot).
      void refreshAccounts();
      // The user may have sent the NEXT message while this read was out (the
      // composer is free the moment the turn ends): its question bubble,
      // notes and questions belong to that turn and stay (v1.369.0 review).
      if (turnRef.current === null) {
        setPending(null);
        setSteers([]);
        // The reply line now keeps the helpers (its `helpers`) — and the
        // attempts (its `attempts` card).
        setPageHelpers([]);
        setPageAttempts([]);
        setPageQuestions((prev) => prev.map((q) => (q.outcome ? q : { ...q, outcome: "stopped" })));
      }
      const sent = readSentSteers(threadId);
      const back = unreadSteersFor(sent, finished, v?.messages ?? [], unread);
      writeSentSteers(
        threadId,
        sent.filter((s) => s.turnId !== finished),
      );
      if (back.length) setInput((cur) => withSteersRestored(cur, back));
      // v1.392.0: the reply ended — its stall notice (and the live summary
      // line: the saved reply says it now) go with it.
      if (turnRef.current === null) {
        setStall(null);
        setStallNote(null);
        setLiveSummarized(null);
      }
      setTurnsEnded((n) => n + 1);
      onChanged();
      // v1.402.0: an approved plan's act turn the daemon starts by itself is
      // due (or already running): no Next step suggestions for a reply that
      // is about to be followed by the work itself.
      const planDue = !!v && (v.planRun || (v.running && !!v.turnId && v.turnId !== finished));
      // v1.377.0: Next step — asked once a reply ended cleanly and nothing
      // else started meanwhile (the daemon answers "off", making no model
      // call, unless the user switched suggestions on).
      const ask = nextGenRef.current;
      const replied = !!v?.messages.some((m) => m.role === "assistant" && m.turnId === finished && !m.error);
      if (clean && replied && turnRef.current === null && !planDue) {
        void fetchNextSteps(threadId).then((steps) => {
          if (nextGenRef.current === ask && turnRef.current === null) setNextSteps(steps);
        });
      }
      return v;
    },
    [load, onChanged, threadId, refreshAccounts, setSteers, setPageHelpers, setPageAttempts, setPageQuestions],
  );

  /** A message that never started goes back in the box: its words and what
   *  rode it — uploads, "@" files and the "/" skill. v1.373.0 review: BESIDE
   *  what the box holds now (a waiting message refused at send used to lose
   *  its words to the next message being typed, and hand that message its
   *  files). One skill per message: when the box already holds another, that
   *  one stays and the line says which did not go back. */
  const restoreUploads = att.restore;
  const restoreToBox = useCallback(
    (text: string, extras?: TurnExtras) => {
      // v1.379.0: a typed "/name words" goes back as it was typed.
      const back = extras?.typed ?? text;
      if (back) setInput((cur) => withMessageRestored(cur, back));
      if (!extras) return;
      if (extras.skill) {
        const back = extras.skill;
        const cur = skillRef.current;
        if (!cur) setSkill(back);
        else if (cur.name !== back.name || cur.source !== back.source) {
          const note = `The skill ${back.name} did not go back in the box: it already holds ${cur.name}.`;
          setError((e) => (e ? `${e} ${note}` : note));
        }
      }
      if (extras.files.length) {
        const back = extras.files;
        setAtFiles((cur) => withFilesRestored(cur, back));
      }
      if (extras.attachments.length) restoreUploads(extras.attachments);
      // v1.397.0: the "Try on…" picks go back on the chip.
      if (extras.tryOn?.length) setTryOn(extras.tryOn);
    },
    [restoreUploads],
  );

  /** v1.384.0: keep the running reply as it stands (lib/turnCache), so a
   *  return paints it at once and asks only for the frames after `pos`. */
  const keepRunning = useCallback(
    (id: string, pos: string) => {
      const seed = stream.seedOf?.();
      if (!seed || !pos) return;
      keepTurn({
        threadId,
        turnId: id,
        lastId: pos,
        stream: seed,
        page: {
          helpers: pageRef.current.helpers,
          attempts: pageRef.current.attempts,
          questions: pageRef.current.questions,
          steerNotes: steerRef.current,
          pending: pendingRef.current,
          // v1.390.0: the store itself (no copy — this runs per frame).
          timeline: timeline.state(),
        },
      });
    },
    [stream, threadId, timeline],
  );
  const posRef = useRef<string>(kept?.lastId ?? "");

  const runTurn = useCallback(
    async (
      text: string | null,
      rejoinId?: string,
      extras?: TurnExtras,
      resume?: { after: string; seed: StreamSeed },
    ) => {
      const id = rejoinId ?? mintTurnId();
      const gen = ++runGenRef.current;
      turnRef.current = id;
      setTurnId(id);
      setSending(true);
      setError(null);
      setCompactNote(null);
      // v1.377.0: a new turn retires the last reply's Next step buttons (and
      // a suggestion asked for the last one never lands under this one).
      nextGenRef.current += 1;
      setNextSteps([]);
      setRewindNote(null);
      // A new (or rejoined: its frames replay) turn draws its own helpers —
      // v1.384.0: one carried on from where the user was keeps its own.
      if (!resume) {
        setPageHelpers([]);
        setPageAttempts([]);
      }
      // v1.392.0: and its own stall notice and summary line.
      if (!resume) {
        setStall(null);
        setLiveSummarized(null);
      }
      setStallNote(null);
      // v1.390.0: and its own Timeline (a rejoin's replay folds it again).
      if (!resume) timeline.reset(id);
      replayingRef.current = false;
      posRef.current = resume?.after ?? "";
      limitSeenRef.current = false;
      let started = !!rejoinId;
      // A NEW turn retires the limit card at its first real frame (once —
      // the turn's own `account_limit` must stay up after it).
      let retired = !!rejoinId;
      let unread: string[] = [];
      let clean = false;
      const onPosition = (pos: string) => {
        posRef.current = pos;
        keepRunning(id, pos);
      };
      try {
        // v1.371.0: the message's files, and the page the Browser pane
        // shows (the agent may screenshot that dev server without asking).
        // v1.373.0: the files picked with "@" in the folder, and the skill
        // picked with "/" (`skill` for yours, `folder_skill` for the folder's).
        const preview = previewAddressFor(threadId);
        const ups = extras?.attachments ?? [];
        const picks = extras?.files ?? [];
        inFlightRef.current = true;
        const res = await stream.run(
          rejoinId
            ? undefined
            : {
                text,
                turn_id: id,
                ...(ups.length ? { attachments: ups.map((f) => f.path) } : {}),
                ...(picks.length ? { files: picks.map((f) => f.path) } : {}),
                ...skillFields(extras?.skill),
                // v1.379.0: one of Claude Code's own commands (the daemon
                // expands it; `text` is its words).
                ...(extras?.command ? { command: extras.command } : {}),
                // v1.397.0: the "Try on…" control's models (the daemon starts
                // one attempt per model; the words alone ask the same).
                ...(extras?.tryOn?.length ? { try_on: tryOnBody(extras.tryOn) } : {}),
                ...(preview ? { preview_url: preview } : {}),
                // v1.384.0: every frame tagged with its resume position.
                ids: true,
              },
          undefined,
          undefined,
          rejoinId
            ? {
                rejoin: rejoinId,
                // v1.384.0: tagged; with `resume`, only what came after.
                ids: true,
                ...(resume ? { resume } : {}),
                onPosition,
                onFrame: (e, d) => {
                  started = true;
                  onFrame(e, d);
                },
              }
            : {
                path: `/build/sessions/${encodeURIComponent(threadId)}/turn`,
                onPosition,
                onFrame: (e, d) => {
                  started = true;
                  if (!retired && e !== "phase" && e !== "resume" && e !== "caught_up") {
                    retired = true;
                    setLimit(null);
                  }
                  onFrame(e, d);
                },
              },
        );
        unread = res.unreadSteers ?? [];
        clean = true;
      } catch (e) {
        if (e instanceof StreamError && e.status === 409) {
          // Already answering (another window, a resumed wait): follow it.
          const v = await load();
          if (v?.turnId && v.turnId !== "running") {
            turnRef.current = null;
            setSending(false);
            if (text !== null) restoreToBox(text, extras);
            void runTurn(null, v.turnId);
            return;
          }
        }
        // An account limit is said by its card; the error frame repeats it.
        if (!limitSeenRef.current) {
          const line = turnErrorText(e);
          setError(line);
          // Harness gaps (fix 2): the server's own words, folded under
          // Details, and WHICH turn failed — a turn that started has its
          // failure on its reply line (the daemon writes it before the last
          // frame), so the note below steps aside once that line is read.
          setErrorTech({ text: line, technical: turnErrorTechnical(e), turn: started ? id : null });
        }
        if (!started && text !== null) {
          // Never started: the words and what rode them go back in the box,
          // nothing was lost (v1.373.0: a file-only message too).
          setPending(null);
          restoreToBox(text, extras);
        }
      } finally {
        // v1.384.0: only the NEWEST run owns these (a 409 above hands over to
        // a follow it started itself, which must keep them).
        const newest = runGenRef.current === gen;
        if (newest) inFlightRef.current = false;
        // v1.384.0: LEAVING the session (it unmounts) is not the reply's
        // end — it is kept (lib/turnCache) and carried on from on return;
        // nothing is settled here.
        if (!leavingRef.current && newest) {
          turnRef.current = null;
          setTurnId(null);
          setSending(false);
          dropTurn(threadId, id);
        }
      }
      if (!started || leavingRef.current) return;
      let v = await settle(id, unread, clean);
      // v1.402.0: AN APPROVED PLAN RUNS. When this turn approved a plan and
      // ended cleanly, the daemon starts the act turn BY ITSELF (the view
      // says `planRun` until it runs): read again (bounded) and FOLLOW it —
      // the same follow a turn the list saw start gets. Never over a turn
      // this window started meanwhile (the user's own message wins).
      for (
        let i = 0;
        i < PLAN_RUN_TRIES && clean && v?.planRun && !v.running && turnRef.current === null && !leavingRef.current;
        i += 1
      ) {
        await sleep(PLAN_RUN_GAP_MS);
        if (turnRef.current !== null || leavingRef.current) return;
        v = await load();
      }
      if (
        clean && turnRef.current === null && !leavingRef.current &&
        v?.running && v.turnId && v.turnId !== "running" && v.turnId !== id
      ) {
        void runTurn(null, v.turnId);
      }
    },
    [stream, threadId, onFrame, load, settle, restoreToBox, keepRunning, setPageHelpers, setPageAttempts, timeline],
  );

  // Open: read the session, its accounts, and follow a turn already running.
  // v1.384.0: the session and its running reply were DRAWN already, from
  // where this window last left them (lib/turnCache); the read below checks
  // the reply still runs and carries it on from its last frame (only the
  // frames after it are sent), or settles it when it ended meanwhile.
  useEffect(() => {
    let live = true;
    leavingRef.current = false;
    setInput("");
    setError(null);
    // v1.384.0 review: a kept reply's question stays drawn (it is the kept
    // turn's own; the read below confirms or drops it).
    if (!kept) setPending(null);
    setLimit(null);
    setNextSteps([]);
    setRewindNote(null);
    setBranchError(null);
    void refreshAccounts();
    void load().then(async (v) => {
      if (!live) return;
      if (!v) {
        // Unreadable (offline, gone): a kept reply is not left "working".
        if (kept) {
          dropTurn(threadId, kept.turnId);
          turnRef.current = null;
          setTurnId(null);
          stream.abort();
          setPending(null);
        }
        return;
      }
      // v1.377.0: a session just branched from a QUESTION opens with that
      // question's words and files in its box (nothing is sent).
      if (initialDraft) {
        if (initialDraft.text) setInput((cur) => (cur.trim() ? cur : initialDraft.text));
        const picks = initialDraft.pickedFiles.map((p) => atFileFromPath(p, v.folder));
        if (picks.length) {
          atFilesRef.current = picks;
          setAtFiles(picks);
        }
        if (initialDraft.attachments.length) att.restore(initialDraft.attachments.map(fileFromPath));
        // v1.377.0 review: the question's "/" skill goes in the box too.
        if (initialDraft.skill) setSkill(initialDraft.skill);
      }
      // Notes sent to a turn that ended while nobody watched: back in the box.
      const sent = readSentSteers(threadId);
      const ended = Array.from(new Set(sent.map((s) => s.turnId))).filter((t) => !(v.running && v.turnId === t));
      if (ended.length) {
        const back = ended.flatMap((t) => unreadSteersFor(sent, t, v.messages));
        writeSentSteers(
          threadId,
          sent.filter((s) => !ended.includes(s.turnId)),
        );
        if (back.length) setInput((cur) => withSteersRestored(cur, back));
      }
      if (v.queued.length) setQueued(v.queued.slice(0, MAX_QUEUED));
      const runningId = v.running && v.turnId && v.turnId !== "running" ? v.turnId : null;
      if (kept && runningId === kept.turnId && kept.lastId) {
        // Where the user was: carry on with only the frames after it.
        void runTurn(null, kept.turnId, undefined, { after: kept.lastId, seed: kept.stream });
      } else {
        if (kept) {
          // The kept reply ENDED while the user was away (or another one
          // runs now): the transcript has its line — the live state goes.
          dropTurn(threadId, kept.turnId);
          turnRef.current = null;
          setTurnId(null);
          stream.abort();
          setPageHelpers([]);
          setPageAttempts([]);
          setSteers([]);
          setPending(null);
        }
        if (runningId && turnRef.current !== runningId) void runTurn(null, runningId);
      }
    });
    return () => {
      live = false;
      // v1.384.0: the reply is not over because its session left the
      // screen — it is kept (lib/turnCache) and carried on from on return.
      // Only a stream in flight is closed: a kept state drawn at mount and
      // not yet carried on stays drawn (React's development double mount
      // would otherwise blank it for a frame).
      leavingRef.current = true;
      if (inFlightRef.current) stream.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [threadId]);

  // The list saw a turn start here (a resumed wait, another window).
  // v1.384.0: never a SECOND follow of the turn this screen already follows
  // (the open read and the list can both see it run — each follow used to
  // abort the other and replay the whole turn again).
  useEffect(() => {
    if (!listRunning || busy) return;
    void load().then((v) => {
      if (v?.running && v.turnId && v.turnId !== "running" && turnRef.current !== v.turnId) {
        void runTurn(null, v.turnId);
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [listRunning]);

  // Waiting messages go out after a clean finish, one at a time.
  const queueRef = useRef(queued);
  queueRef.current = queued;
  const persistQueue = useCallback(
    (items: QueuedItem[]) => {
      put(`/chat/threads/${encodeURIComponent(threadId)}/queue`, {
        items: items.map(queuedItemBody),
      }).catch(() => {
        /* the queue still lives in this window */
      });
    },
    [threadId],
  );
  useEffect(() => {
    if (busy || !view || queued.length === 0 || turnsEnded === 0) return;
    const [next, ...rest] = queued;
    setQueued(rest);
    persistQueue(rest);
    void send(next);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [turnsEnded]);

  /** Send the box (no argument) or a waiting message (its own words, skill
   *  and files — v1.373.0). */
  async function send(item?: QueuedItem) {
    const fromBox = item === undefined;
    if (!view) return;
    // v1.371.0: a send never goes without its files — one pressed while a
    // picture is still going up waits for it, then reads the box as it is
    // THEN (the chat page's v1.275.0 rule).
    if (fromBox && att.busyRef.current) {
      att.whenSettled(() => void send());
      return;
    }
    // v1.374.0: while Compact writes the summary nothing starts (the daemon
    // would refuse it): the box keeps its words, a waiting message waits on.
    // v1.377.0: a Compact found running when the session opened counts too.
    if (compactingRef.current || viewRef.current?.compacting) {
      if (item) {
        const back = [item, ...queueRef.current].slice(0, MAX_QUEUED);
        setQueued(back);
        persistQueue(back);
      }
      return;
    }
    const raw = (item ? item.text : (boxRef.current?.value ?? input)).trim();
    const files: HarnessFile[] = fromBox ? att.filesRef.current : (item.attachments ?? []);
    const picks: AtFile[] = fromBox ? atFilesRef.current : (item.files ?? []);
    const chip: PickedSkill | null = fromBox ? skillRef.current : (item.skill ?? null);
    // Harness gaps review: a message SENT once before (Retry) carries what it
    // ran — its words go as they are and its command by name; it is never
    // read for a "/" command again (lib/buildRetry).
    const resend = fromBox ? undefined : item.resend;
    // v1.379.0: a message that STARTS with "/name" runs that command or skill
    // (Claude Code's rule): the words after the name are its arguments. An
    // unknown "/word" is ordinary text (a path such as /etc/hosts too).
    const lists = !resend && leadingSlash(raw) ? await ensureSlashLists() : null;
    const typed = lists ? resolveTyped(raw, commands, lists.folder, lists.yours, lists.custom) : null;
    if (busy && typed) {
      // Claude Code queues a command sent while it answers; the session's own
      // quick ones (the model menu, the rewind list, …) just open.
      const needsTurn =
        typed.kind !== "command" || typed.command.id === "init" || (typed.command.id === "plan" && !!typed.args);
      if (needsTurn) {
        if (fromBox) queueMessage();
        return;
      }
    }
    if (typed?.kind === "command") {
      if (fromBox) {
        setInput("");
        setCaret(0);
      }
      await runCommand(typed.command.id, typed.args);
      return;
    }
    let text = raw;
    let picked: PickedSkill | null = chip;
    let command = resend?.command ?? "";
    // What the line shows and what goes back in the box if it never starts:
    // a command as it was typed ("/review app.py").
    let typedLine = command ? `/${command}${text ? ` ${text}` : ""}` : raw;
    if (typed?.kind === "skill") {
      const want = pickedOf(typed.skill);
      if (chip && (chip.name !== want.name || chip.source !== want.source)) {
        setError(`Send one skill per message: the box already holds ${chip.name}.`);
        return;
      }
      picked = want;
      text = typed.args;
    } else if (typed?.kind === "custom") {
      if (chip) {
        setError(`Send one command or one skill per message: the box holds the skill ${chip.name}.`);
        return;
      }
      command = typed.command.name;
      text = typed.args;
      typedLine = raw;
    }
    // v1.379.0: a skill or a command alone is a message too.
    // v1.397.0: the "Try on…" picks ride the next message from the box —
    // review: and a waiting message keeps its own.
    const trying: TryOnPick[] = fromBox ? tryOnRef.current : (item.tryOn ?? []);
    if (!text && files.length === 0 && picks.length === 0 && !picked && !command && !trying.length) return;
    if (busy) {
      // A note while it works is words only; the files and the skill wait
      // in the box.
      if (text) await steer(text);
      return;
    }
    if (view.archived) {
      setError("This session is archived. Bring it back before you continue it.");
      return;
    }
    const paths = files.map((f) => f.path);
    const filePaths = picks.map((f) => f.path);
    if (fromBox) {
      setInput("");
      setCaret(0);
      att.clear();
      setAtFiles([]);
      setSkill(null);
      setTryOn([]);
    }
    setUsageNote(null);
    // v1.384.0: a message the user sends brings them to the bottom.
    atBottomRef.current = true;
    setPending({
      role: "user",
      // A command shows as it was typed ("/review app.py"), like Claude Code.
      content: command ? typedLine : text,
      at: new Date().toISOString(),
      ...(paths.length ? { attachmentPaths: paths } : {}),
      ...(filePaths.length ? { filePaths } : {}),
      ...(picked ? { skill: picked.name } : {}),
      ...(command ? { command } : {}),
    });
    await runTurn(text, undefined, {
      attachments: files,
      files: picks,
      skill: picked,
      ...(command ? { command, typed: typedLine } : {}),
      ...(trying.length ? { tryOn: trying } : {}),
    });
  }

  /** v1.379.0: the session's own "/" commands — picked from the menu or typed
   *  ("/plan fix the login" plans first and sends the words). */
  async function runCommand(id: CommandId, args: string) {
    if (!view) return;
    setSlashDismissed(false);
    if (id === "plan") {
      if (args) {
        if (view.mode !== "plan") await patchSetup({ mode: "plan" });
        await send({ text: args });
      } else {
        void patchSetup({ mode: view.mode === "plan" ? "act" : "plan" });
      }
    } else if (id === "compact") {
      // v1.374.0: the meter's Compact. v1.379.0 review: Claude Code's
      // "/compact <words>" — the words say what the summary keeps in view
      // (they were dropped).
      void compact(args);
    } else if (id === "rewind") {
      setRewindOpen(true);
    } else if (id === "model") {
      // v1.379.0 review: "/model <name>" picks that model when exactly one
      // model in the menu is called so; anything else opens the menu.
      const named = args ? modelByName(catalog.models, args, provider) : null;
      if (named) void pickModel(named);
      else setModelMenuOpen(true);
    } else if (id === "clear") {
      // Claude Code's /clear starts fresh: a NEW session on the same folder
      // (the folder the user picked, for a session on its own branch), or
      // with no folder. This session stays in the list.
      const folder = view.noFolder ? "" : view.git?.checkout || view.folder;
      const problem = onNewSession ? await onNewSession(folder) : null;
      if (problem) setError(problem);
    } else if (id === "resume") {
      onFindSessions?.();
    } else if (id === "usage") {
      try {
        const u = await get<unknown>(`/chat/threads/${encodeURIComponent(threadId)}/usage`);
        setUsageNote(sessionUsageWords(u));
      } catch (e) {
        setUsageNote(
          isMissing(e) ? "This version of Iron Jarvis cannot say what a session used." : e instanceof Error ? e.message : String(e),
        );
      }
    } else if (id === "init") {
      await send({ text: INIT_PROMPT });
    }
  }

  async function steer(text: string) {
    const id = turnRef.current;
    if (!id) return;
    try {
      await post(`/chat/turns/${encodeURIComponent(id)}/steer`, { text });
      writeSentSteers(threadId, [...readSentSteers(threadId), { turnId: id, text }]);
      setSteers([...steerRef.current, text]);
      // v1.384.0: a return shows the note too.
      if (posRef.current) keepRunning(id, posRef.current);
      setInput("");
    } catch (e) {
      setError(
        e instanceof ApiError && e.status === 404
          ? "The reply already finished, so the note was not added. Your words are still in the box."
          : e instanceof Error
            ? e.message
            : String(e),
      );
    }
  }

  /** Ctrl+Enter while it works: the box waits to be sent after the reply —
   *  its words AND its own skill and files (v1.373.0), so the next message
   *  typed meanwhile starts empty. */
  function queueMessage() {
    const text = input.trim();
    const files = att.filesRef.current;
    const picks = atFilesRef.current;
    const trying = tryOnRef.current;
    if (!text && files.length === 0 && picks.length === 0 && trying.length === 0) return;
    if (att.busyRef.current) {
      setError("A file is still being attached. Queue the message when it is done.");
      return;
    }
    if (queued.length >= MAX_QUEUED) {
      setError(`Up to ${MAX_QUEUED} messages can wait. Send or remove one first.`);
      return;
    }
    const item: QueuedItem = {
      text,
      ...(skillRef.current ? { skill: skillRef.current } : {}),
      ...(picks.length ? { files: picks } : {}),
      ...(files.length ? { attachments: files } : {}),
      // Review (v1.397.0): the "Try on…" picks wait with THIS message.
      ...(trying.length ? { tryOn: trying } : {}),
    };
    const next = [...queued, item];
    setQueued(next);
    persistQueue(next);
    setInput("");
    setCaret(0);
    att.clear();
    setAtFiles([]);
    setSkill(null);
    setTryOn([]);
  }

  async function stop() {
    const id = turnRef.current;
    if (!id) {
      stream.abort();
      return;
    }
    try {
      await post(`/chat/turns/${encodeURIComponent(id)}/stop`, {});
    } catch {
      // Gone already (it finished): stop following it here.
      stream.abort();
    }
  }

  /** v1.392.0 (long jobs, Phase 3): the stall line's two presses. "Stop the
   *  step" cancels exactly the tool call the reply waits on (the reply
   *  carries on, told the user stopped it); "Stop" while a step hangs stops
   *  the reply AND that step, so the reply ends at once — with no step (the
   *  model is what it waits on) it is the ordinary Stop. */
  async function stopStalled(alsoReply: boolean) {
    const step = stall?.step;
    if (!step) {
      if (alsoReply) await stop();
      return;
    }
    setStallStopping(true);
    try {
      await stopStep(threadId, turnRef.current ?? "", step.id, alsoReply);
      if (!alsoReply) setStallNote(STEP_STOPPED_NOTE);
    } catch (e) {
      // The step finished meanwhile (404): there was nothing left to stop.
      setStallNote(
        e instanceof ApiError && e.status === 404
          ? "That step had already finished."
          : e instanceof Error
            ? e.message
            : String(e),
      );
      if (alsoReply) await stop();
    } finally {
      setStallStopping(false);
    }
  }

  /** v1.392.0 (decision 8): the session's optional cap on a reply — saved on
   *  the session (`setup.cap`); null removes it. Answers an error sentence,
   *  or null when it was saved. */
  async function saveCap(cap: SessionCap | null): Promise<string | null> {
    setCapBusy(true);
    try {
      const v = await patchSession(threadId, { cap });
      if (v) setView(v);
      onChanged();
      return null;
    } catch (e) {
      return e instanceof Error ? e.message : String(e);
    } finally {
      setCapBusy(false);
    }
  }

  /** v1.377.0: "Branch from here" — a NEW session with this conversation up
   *  to the message (the daemon copies it; nothing here changes), opened at
   *  once; a question's words and files go in ITS box. */
  async function branchFrom(turnId: string, at: BranchAt) {
    if (branching) return;
    setBranching(`${at}:${turnId}`);
    setBranchError(null);
    try {
      const made = await branchSession(threadId, turnId, at);
      onChanged();
      onBranched?.(made.id, made.draft);
    } catch (e) {
      setBranchError(
        isMissing(e) && !(e instanceof ApiError && /message/i.test(e.message))
          ? "This version of Iron Jarvis cannot branch a session yet."
          : e instanceof Error
            ? e.message
            : String(e),
      );
    } finally {
      setBranching(null);
    }
  }

  /** v1.377.0: a Next step press puts its words in the box — never sends. */
  function pickNextStep(step: string) {
    setInput((cur) => withNextStep(cur, step));
    setNextSteps([]);
    boxRef.current?.focus({ preventScroll: true });
  }

  async function answerAsk(id: string, answer: AskAnswer | null): Promise<string | null> {
    try {
      await answerQuestion(threadId, id, answer);
      return null;
    } catch (e) {
      return isMissing(e)
        ? "This question is no longer waiting."
        : e instanceof Error
          ? e.message
          : String(e);
    }
  }

  // ------------------------------------------------------------ the setup
  async function patchSetup(body: Record<string, unknown>) {
    try {
      const v = await patchSession(threadId, body);
      if (v) setView(v);
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  // ----------------------------------------------------------- accounts
  const defaultProvider = daemon.health?.default_provider ?? "";
  const defaultModel = daemon.health?.default_model ?? "";
  const provider = view?.setup.provider || defaultProvider;
  const model = view?.setup.provider ? view.setup.model : defaultModel;
  // The account decides the vendor (Phase 5): the session view says which.
  const vendor = vendorOf(view?.accountVendor);
  const currentAccount: BuildAccount | null = sessionAccount(view?.account, vendor, accounts ?? null);
  // The list is the daemon's cached snapshot: read it again while it is open
  // — the header chip's list, and (v1.376.0) the model menu, which lists the
  // same accounts with their states. An Iron Jarvis with no list (a 404:
  // `accounts` null) is not asked again.
  useVisibleInterval(() => void refreshAccounts(), ACCOUNTS_POLL_MS, accountsOpen || (modelMenuOpen && accounts !== null));
  // v1.393.0: while a reply works, its usage moves (the chip, the Timeline).
  useVisibleInterval(() => void refreshAccounts(), USAGE_POLL_MS, busy && !!accounts && !!currentAccount);
  useEffect(() => {
    if (modelMenuOpen && accounts !== null) void refreshAccounts();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [modelMenuOpen]);

  /** Read the session again after an account press and follow a turn the
   *  daemon is running for it (a wait that resumed at once). The press's own
   *  answer can be a moment behind: a Cancel or a plain pick that lands while
   *  the daemon re-checks the account stops the continuation it was about to
   *  start, and only a fresh read says which way that went (v1.368.0 review). */
  async function rereadAndFollow(): Promise<void> {
    const v = await load();
    if (v?.running && v.turnId && v.turnId !== "running" && !turnRef.current) void runTurn(null, v.turnId);
  }

  /** THE ONE WAY a session moves to another account — the header chip's
   *  list and the model menu (v1.376.0) both call it, so both show the same
   *  result: the daemon's answer is adopted (the chip and the model chip read
   *  the same view). With `model`, the account AND the model go in ONE
   *  request: the daemon sets both, or refuses the account and changes
   *  nothing. NEVER a continue — "carry on now" is offered only from the
   *  limit card (lib/buildAccounts chooseAccountAndContinue). Answers the
   *  daemon's own sentence when it refused, else null. */
  async function switchAccount(id: string, model?: string): Promise<string | null> {
    setAccountBusy(true);
    setAccountNote(null);
    try {
      const v = decodeSessionView(await setSessionAccount(threadId, id, model));
      if (v) adopt(v);
      await rereadAndFollow();
      onChanged();
      return null;
    } catch (e) {
      return e instanceof Error ? e.message : String(e);
    } finally {
      setAccountBusy(false);
    }
  }

  /** A pick in the chip's list: the session moves to that account from its
   *  next message. A refusal is said under the list. */
  async function pickAccount(a: BuildAccount) {
    const problem = await switchAccount(a.id);
    if (problem) setAccountNote(problem);
  }

  /** A pick in the model menu (v1.376.0). Under ANOTHER Iron-Proxy account:
   *  that account and that model together (`switchAccount`), its refusal
   *  said in the conversation with nothing changed. Anything else — the
   *  session's own account, a way with no accounts, an API key, a local
   *  model, the default — is the model alone, as before (the daemon keeps
   *  the account for the same vendor, and moves another vendor's pick to
   *  that vendor's Default account). */
  async function pickModel(value: string, accountId?: string) {
    const { provider: p, model: mo } = splitChoice(value);
    if (accountId && accountId !== currentAccount?.id) {
      setError(null);
      const problem = await switchAccount(accountId, mo ?? "");
      if (problem) setError(problem);
      return;
    }
    void patchSetup({ provider: p ?? "", model: mo ?? "" });
  }

  async function makeDefault(a: BuildAccount) {
    setAccountBusy(true);
    setAccountNote(null);
    try {
      const next = await makeDefaultAccount(a.id);
      if (next) setAccounts(next);
      else await refreshAccounts();
    } catch (e) {
      setAccountNote(e instanceof Error ? e.message : String(e));
    } finally {
      setAccountBusy(false);
    }
  }

  async function resumeWhenReady() {
    setLimitError(null);
    try {
      const v = decodeSessionView(await armResumeWhenReady(threadId));
      // The daemon's view says whether it waits (it may have found the
      // account ready and carried on at once); an answer that says nothing
      // about accounts keeps the press's meaning.
      if (v) adopt(v);
      if (!v?.knowsAccounts) setWaiting(true);
      await rereadAndFollow();
    } catch (e) {
      setLimitError(
        isMissing(e)
          ? "This version of Iron Jarvis cannot wait for a reset yet. Choose another account, or send again after the reset."
          : e instanceof Error
            ? e.message
            : String(e),
      );
    }
  }

  async function cancelWait() {
    setLimitError(null);
    try {
      const v = decodeSessionView(await cancelResumeWhenReady(threadId));
      if (v) adopt(v);
      if (!v?.knowsAccounts) setWaiting(false);
      await rereadAndFollow();
    } catch (e) {
      setLimitError(e instanceof Error ? e.message : String(e));
    }
  }

  async function chooseAndContinue(a: BuildAccount) {
    // "Carry on now" belongs to a session a limit cut off: with no limit on
    // screen this is a plain pick (the daemon would accept a continue anyway
    // and start a turn nobody asked for).
    if (!limit && !decodeAccountLimit(view?.accountLimit)) {
      setChoosing(false);
      await pickAccount(a);
      return;
    }
    setLimitError(null);
    setAccountBusy(true);
    try {
      const res = await chooseAccountAndContinue(threadId, a.id);
      const v = decodeSessionView(res.session);
      if (v) adopt(v);
      setLimit(null);
      setChoosing(false);
      onChanged();
      // The daemon carries on at once as a new turn on that account: follow it.
      if (res.turnId) void runTurn(null, res.turnId);
      else {
        const again = await load();
        if (again?.running && again.turnId && again.turnId !== "running") void runTurn(null, again.turnId);
      }
    } catch (e) {
      setLimitError(
        isMissing(e)
          ? "This version of Iron Jarvis cannot move a session to another account yet."
          : e instanceof Error
            ? e.message
            : String(e),
      );
      // A 409 here means a turn is already running for the session (the wait
      // resumed it, or another window): show it rather than a dead card.
      if (e instanceof ApiError && e.status === 409) await rereadAndFollow();
    } finally {
      setAccountBusy(false);
    }
  }

  // ------------------------------------------------- Compact (v1.374.0)
  /** Summarize the older turns (the meter's Compact, and "/compact"): the
   *  daemon writes it with the session's own model, checks it against the
   *  conversation and the session's ledger, and keeps it; later turns read it
   *  in their place. The conversation shown here is never cut. A refusal (not
   *  enough conversation yet, the demo model, nothing could be checked) is
   *  said in the daemon's words. */
  async function compact(focus = "") {
    if (compactingRef.current || !view) return;
    if (busy) {
      // Harness gaps (fix 4): the chat's word ("Summarize").
      setError("Wait for the reply to finish, then summarize the earlier turns.");
      // v1.379.0 review: a typed "/compact <words>" goes back in the box
      // (the box was emptied for it).
      if (focus) setInput((cur) => cur || `/compact ${focus}`);
      return;
    }
    compactingRef.current = true;
    setCompacting(true);
    setCompactNote(null);
    setError(null);
    try {
      const done = await compactSession(threadId, focus);
      await load();
      setCompactNote(done ? compactedNote(done.covers, done.stripped) : "Summarized the earlier turns.");
    } catch (e) {
      setError(e instanceof Error && e.message ? e.message : "The earlier turns could not be summarized.");
    } finally {
      compactingRef.current = false;
      setCompacting(false);
    }
  }

  // ---------------------------------------------------- header actions
  /** v1.379.0: a session with no folder — open its own folder. */
  async function openWorkFolder() {
    if (!view?.folder) return;
    try {
      await post("/documents/open", { path: view.folder });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function saveTitle() {
    const t = (renaming ?? "").trim();
    setRenaming(null);
    if (t && t !== view?.title) await patchSetup({ title: t });
  }

  async function archive(stopFirst = false) {
    setMenuOpen(false);
    try {
      await archiveSession(threadId, stopFirst);
      onGone();
    } catch (e) {
      setError(
        e instanceof ApiError && e.status === 409
          ? "This session is still running something. Stop it first, or archive it again to stop it."
          : e instanceof Error
            ? e.message
            : String(e),
      );
    }
  }

  async function remove() {
    setMenuOpen(false);
    try {
      await deleteSession(threadId);
      onGone();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  // ----------------------------------------- "/" and "@" in the box (v1.373.0)
  const offline = !daemon.checking && !daemon.online;
  const menuState = { text: input, caret, slashDismissed, atDismissed };
  // A running reply does NOT close them (the chat's composer does): a skill
  // or a file picked now rides the message queued with Ctrl+Enter.
  const slashOpen = !!view && !offline && slashMenuOpen(menuState, false);
  const atOpen = !!view && !offline && atMenuOpen(menuState, false);
  const slashToken = slashOpen ? slashTokenAt(input, caret) : null;
  const atToken = atOpen ? tokenAt(input, caret, "@") : null;
  // v1.379.0: a message that STARTS with "/name" (typed or pasted) needs the
  // lists too — Enter runs it by its name.
  const leading = view && !offline ? leadingSlash(input) : null;
  const wantsLists = slashOpen || !!leading;
  // The skill lists and Claude Code's own commands, asked the first time "/"
  // opens in this session (an older Iron Jarvis has no folder list and no
  // commands list: none, quietly).
  useEffect(() => {
    if (wantsLists) void ensureSlashLists();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wantsLists]);
  const commands = useMemo(
    () => sessionCommands(view?.mode ?? "act", { hasFolder: !view?.noFolder }),
    [view?.mode, view?.noFolder],
  );
  // Claude Code recognizes its custom commands only at the START of a
  // message, so they are offered only for a "/word" there.
  const atStart = !!slashToken && !input.slice(0, slashToken.start).trim();
  const slashList: SlashRow[] = slashToken
    ? slashRows(slashToken.query, commands, folderSkills, yourSkills, skill, atStart ? customCommands : null)
    : [];
  // After "/name " — what Enter will run, said above the box.
  const invoking =
    !slashToken && !atToken && leading?.hasSpace
      ? resolveTyped(input, commands, folderSkills, yourSkills, customCommands)
      : null;
  // The SESSION's folder only (GET /fs/files on it, the chat's own query).
  // v1.379.0: a session with no folder lists its own folder once made.
  const folderFiles = useProjectFilesState(atOpen, view?.folder || null);
  const atList = atToken
    ? atFileRows(folderFiles.listing, atToken.query, atFiles.map((f) => f.path))
    : { rows: [] as AtFile[], more: 0 };
  // The open list, its highlighted row, and their ids for the box's aria.
  const menuRowCount = slashToken ? slashList.length : atToken ? atList.rows.length : 0;
  const activeRow = Math.min(menuActive, Math.max(menuRowCount - 1, 0));
  const listId = slashToken ? `${menuIdBase}-slash` : atToken ? `${menuIdBase}-at` : undefined;
  const activeRowId = listId && menuRowCount > 0 ? `${listId}-row-${activeRow}` : undefined;
  // A list still being read (v1.373.0 review: Enter waits for it).
  const slashLoading = yourSkills === null || folderSkills === null || customCommands === null;
  // v1.379.0: a session with no folder has nothing to read before its first
  // message (no folder yet) — not "still loading" (Enter must not wait).
  const atLoading = !!view?.folder && folderFiles.listing === null && !folderFiles.failed;
  const menuLoading = slashToken ? slashLoading : atToken ? atLoading : false;
  // The highlighted row goes back to the top as the typed word changes.
  const menuWord = slashToken ? `/${slashToken.query}` : atToken ? `@${atToken.query}` : "";
  useEffect(() => {
    setMenuActive(0);
  }, [menuWord]);

  /** Put the caret at `pos` once React has written the new text. */
  function placeCaret(pos: number) {
    boxRef.current?.focus();
    requestAnimationFrame(() => {
      const el = boxRef.current;
      if (el) el.selectionStart = el.selectionEnd = pos;
    });
  }

  /** A pick takes ONLY the "/word" or "@word" out of the message (the chat's
   *  v1.105.0 rule: a prompt already written is never eaten). */
  function consumeToken(tok: { start: number; end: number; query: string }) {
    setInput(spliceToken(input, tok));
    setCaret(tok.start);
    placeCaret(tok.start);
  }

  /** A row picked: Enter, Tab or a click. v1.379.0: one of Claude Code's own
   *  custom commands RUNS on Enter (Claude Code's rule — the words after the
   *  "/word" ride as its arguments); Tab or a click puts "/name " in the box
   *  so its words can be typed first. */
  function pickSlash(row: SlashRow, how: "enter" | "tab" | "click" = "click") {
    const tok = slashTokenAt(input, caret);
    if (!tok || !view) return;
    setSlashDismissed(false);
    if (row.kind === "custom") {
      const rest = input.slice(tok.end).trim();
      if (how === "enter") {
        const typed = `/${row.command.name}${rest ? ` ${rest}` : ""}`;
        setInput(typed);
        setCaret(typed.length);
        if (boxRef.current) boxRef.current.value = typed;
        void send();
        return;
      }
      const next = `/${row.command.name} ${input.slice(tok.end).replace(/^\s+/, "")}`;
      setInput(next);
      const at = row.command.name.length + 2;
      setCaret(at);
      placeCaret(at);
      return;
    }
    consumeToken(tok);
    if (row.kind === "skill") {
      setSkill(pickedOf(row.skill));
      return;
    }
    // The session's commands call what the box already has.
    void runCommand(row.command.id, "");
  }

  function pickAt(f: AtFile) {
    const tok = tokenAt(input, caret, "@");
    if (!tok) return;
    setAtDismissed(false);
    consumeToken(tok);
    if (atFilesRef.current.some((x) => x.path === f.path)) return;
    // A file picked by name must be READ: one turn reads READ_PER_MESSAGE
    // files, uploads included, and the daemon refuses a message past it.
    if (att.held() + atFilesRef.current.length >= READ_PER_MESSAGE) {
      setError(READ_LIMIT_WORDS);
      return;
    }
    setAtFiles((cur) => [...cur, f]);
  }

  /** The menus' keys, asked before the box's own: ↑↓ move, Enter or Tab
   *  picks, Escape closes the menu (or the rewind list) WITHOUT sending and
   *  without touching the words. True when the key was handled. */
  function onMenuKey(e: React.KeyboardEvent<HTMLTextAreaElement>): boolean {
    if (slashToken || atToken) {
      if (e.key === "Escape") {
        e.preventDefault();
        if (slashToken) setSlashDismissed(true);
        else setAtDismissed(true);
        return true;
      }
      const n = slashToken ? slashList.length : atList.rows.length;
      if (n === 0) {
        // v1.373.0 review: a list still being read has nothing to pick YET —
        // Enter used to send the half-typed "@notes" as the message, with no
        // file. It waits instead; once the list is read, nothing to pick
        // means Enter sends the words (the chat's rule).
        const plainEnter = e.key === "Enter" && !e.shiftKey && !e.ctrlKey && !e.metaKey;
        if (menuLoading && (plainEnter || (e.key === "Tab" && !e.shiftKey))) {
          e.preventDefault();
          return true;
        }
        return false;
      }
      const cur = Math.min(menuActive, n - 1);
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        setMenuActive(e.key === "ArrowDown" ? Math.min(cur + 1, n - 1) : Math.max(cur - 1, 0));
        return true;
      }
      const enter = e.key === "Enter" && !e.shiftKey && !e.ctrlKey && !e.metaKey;
      if (enter || (e.key === "Tab" && !e.shiftKey)) {
        e.preventDefault();
        if (slashToken) pickSlash(slashList[cur], enter ? "enter" : "tab");
        else pickAt(atList.rows[cur]);
        return true;
      }
      return false;
    }
    if (rewindOpen && e.key === "Escape") {
      e.preventDefault();
      setRewindOpen(false);
      return true;
    }
    return false;
  }

  /** Every way the caret moves (the chat's four: onSelect alone never fires
   *  for a collapsed caret). */
  const trackCaret = (e: React.SyntheticEvent<HTMLTextAreaElement>) => setCaret(e.currentTarget.selectionStart ?? 0);

  // ------------------------------------------------------------- render
  // Keep the newest line in view — the TRANSCRIPT scrolls, never the page (a
  // scrollIntoView would drag the whole window down on a phone).
  // v1.384.0: only while the user is AT the bottom (a reader scrolled up to
  // an earlier reply is not dragged down by every new step), and the place is
  // kept per session (lib/turnCache): a return is at the bottom when the user
  // left it there, else exactly where they were — before the first paint.
  const atBottomRef = useRef(scrollFor(threadId)?.atBottom ?? true);
  const restoredRef = useRef(false);
  const onTranscriptScroll = useCallback(() => {
    const el = transcriptRef.current;
    if (!el) return;
    const at = atBottomOf(el);
    atBottomRef.current = at;
    keepScroll(threadId, { top: el.scrollTop, atBottom: at });
  }, [threadId]);
  useLayoutEffect(() => {
    const el = transcriptRef.current;
    if (!el || restoredRef.current) return;
    restoredRef.current = true;
    const saved = scrollFor(threadId);
    el.scrollTop = !saved || saved.atBottom ? el.scrollHeight : saved.top;
  });
  useEffect(() => {
    const el = transcriptRef.current;
    if (el && atBottomRef.current) el.scrollTop = el.scrollHeight;
  }, [view?.messages.length, pending, stream.tools.length, limit]);

  const asks = collectDockAsks({
    approval: stream.approval,
    mcpAsks: stream.mcpAsks,
    questions,
  });
  const asking = asks.length > 0;
  // v1.377.0 (Phase 6): the session's keys — Esc stops the reply, Ctrl+L the
  // box, Ctrl+K the model menu; inside the IJ harness only (lib/harnessKeys
  // decides where, and leaves Ctrl+K to the app's palette everywhere else).
  useHarnessKeys(
    {
      onStop: () => void stop(),
      onFocus: () => boxRef.current?.focus({ preventScroll: true }),
      onModel: () => {
        if (!asking) setModelMenuOpen(true);
      },
    },
    () => ({ busy, asking }),
  );
  // v1.377.0: a Compact found running when the session opened — read the
  // session again until it ends (then the meter shows the summary).
  useVisibleInterval(() => void load(), COMPACT_REREAD_MS, elsewhereCompacting);
  const shown: BuildMessage[] = useMemo(() => {
    // The daemon's own "carry on" questions are not shown (Phase 5).
    const base = shownMessages(view?.messages ?? []);
    if (!pending) return base;
    // The daemon wrote the question already (a re-read mid-turn): not twice.
    const dup = base.some((m) => m.role === "user" && m.content === pending.content && m.turnId && m.turnId === turnId);
    return dup ? base : [...base, pending];
  }, [view?.messages, pending, turnId]);
  const livePlan = planFromCards(stream.tools);
  // v1.390.0: what the Timeline tab reads — a STABLE handle (the tab is
  // memoized; a token never makes a new one), changed only when the reply
  // starts, ends or is rejoined.
  const timelineTurn = turnId ?? timeline.turn();
  const timelineLive: LiveTimelineHandle = useMemo(
    () => ({ store: timeline, running: busy, startedAt: stream.startedAt ?? null, turnId: timelineTurn || null }),
    [timeline, busy, stream.startedAt, timelineTurn],
  );
  // v1.374.0: how full the conversation is — the newest reply's reading, the
  // daemon's own numbers — and the summary standing in for the older turns.
  const reading = latestContext(view?.messages ?? []);
  const compaction = decodeCompaction(view?.compaction);
  const beforeSummary = readingIsBeforeSummary(reading?.at ?? null, compaction);
  const summarized = !!compaction && beforeSummary;
  const offerCompact = offersCompact(reading?.context ?? null, beforeSummary);
  const lastReply = [...shown].reverse().find((m) => m.role === "assistant");
  // Harness gaps (fix 2): the reply that ENDS the conversation (a question
  // after it — one stopped before a word — means it is no longer the one to
  // retry or to fix).
  const newestReply = lastReply && shown[shown.length - 1] === lastReply ? lastReply : undefined;
  // v1.391.0: the replies a restart cut off that the app carried on by
  // itself (a later line names them — lib/buildResume).
  const carried = carriedOnTurns(view?.messages ?? []);
  /** Retry: send the failed reply's question again, as a new turn (its
   *  words, uploads, "@" files and "/" skill — lib/buildRetry). The box keeps
   *  whatever the user has typed. */
  const retryReply = (index: number) => {
    if (busy || !view) return;
    const item = retryItemFor(shown, index, view.messages ?? [], view.folder);
    if (item) void send(item);
  };
  // v1.378.0: a message the daemon wrote can be rewound to (the refusal's
  // press opens the "/rewind" list only when there is one).
  const canRewindHere = !view?.archived && shown.some((m) => m.role === "user" && !m.steer && !!m.turnId);
  const choice = view?.setup.provider && view?.setup.model ? `${view.setup.provider}::${view.setup.model}` : "";
  // v1.376.0: the chip names the session's ACCOUNT after the model ("Opus 4.8
  // · Work"). A session on a provider with no model of its own (an account
  // chosen from another vendor leaves the model to the vendor) runs that
  // provider's own default — routes/build sends model "" — so the chip says
  // that ("Your Claude plan"), never the app's default model.
  const ownDefault = view?.setup.provider && !view.setup.model ? view.setup.provider : "";
  // Review: the MENU and the reasoning chip read the same pick the chip
  // names. A provider's own default is its plan row ("subscription": the
  // CLI's default, exactly what a turn with model "" runs); a provider with no
  // such row marks nothing — never "default model", the APP's default, which
  // this session does not run on.
  const ownDefaultRow = ownDefault
    ? catalog.models.find((m) => m.provider === ownDefault && isProviderDefaultModel(m.model))
    : undefined;
  const sessionChoice = ownDefault ? `${ownDefault}::${ownDefaultRow?.model ?? ""}` : choice;
  // v1.376.0: one group per company, and the Iron-Proxy accounts in it
  // (lib/harnessModelMenu); the chat's menu never gets this.
  const companies = useMemo(
    () => companyMenu(catalog.models, accounts, sessionAccount(view?.account, vendor, accounts ?? null), sessionChoice),
    [catalog.models, accounts, view?.account, vendor, sessionChoice],
  );
  // Review: the reasoning row is looked up in the FOLDED list, by the pick as
  // that list spells it. A keyless Anthropic's model picked under Claude Code
  // is stored as claude-cli::<id>, which GET /models never lists under
  // claude-cli: the chip used to vanish for it. The unfolded catalog answers
  // what the folded list no longer spells (the app's default on a keyless
  // Anthropic, say).
  const reasoningRow =
    reasoningRowFor(companies.choice, companies.models, defaultProvider, defaultModel) ??
    reasoningRowFor(sessionChoice, catalog.models, defaultProvider, defaultModel);
  const levels = reasoningLevelsOf(reasoningRow);
  const reasoningDef = reasoningDefaultOf(reasoningRow);
  const modelPart = ownDefault
    ? {
        // A provider no words table knows (a fleet node) by its group's name:
        // the label its catalog rows carry.
        text:
          modelText({ provider: ownDefault, model: "" }, catalog.models) ||
          modelGroupName(catalog.models.find((m) => m.provider === ownDefault) ?? { provider: ownDefault }),
        raw: ownDefault,
        isDefault: false,
      }
    : modelTriggerOf(choice, catalog.models, defaultProvider, defaultModel);
  const trigger = currentAccount ? { ...modelPart, account: currentAccount.name } : modelPart;
  const place = !vendor && !currentAccount ? placeWords(provider, model, catalog.models) : "";
  // v1.371.0: a picture alone is a message; one still going up is waited for.
  // v1.373.0: so is a file picked with "@".
  const sendReady =
    (!!input.trim() || att.files.length > 0 || att.uploading || atFiles.length > 0 || !!skill || tryOn.length > 0) &&
    !!view &&
    !offline &&
    !compacting &&
    !elsewhereCompacting;

  if (loadError && !view) {
    return (
      <div data-testid="harness-session" className="grid h-full place-items-center p-6">
        <div className="space-y-2 text-center">
          <ErrorNote>{loadError}</ErrorNote>
          <button type="button" onClick={() => void load()} className="btn-ghost text-xs">
            Try again
          </button>
        </div>
      </div>
    );
  }
  if (!view) {
    return (
      <div data-testid="harness-session" className="grid h-full place-items-center text-zinc-500">
        <Loader2 size={18} className="animate-spin" />
      </div>
    );
  }

  return (
    <div data-testid="harness-session" data-session={view.id} className="flex h-full min-h-0 flex-col gap-3 xl:flex-row">
      {/* ------------------------------------------------- the conversation */}
      {/* Below xl the conversation and the panes stack, each a screen-sized
          box of its own (the transcript scrolls inside); from xl they sit
          side by side at the view's height. NOT from lg: beside the app's
          own sidebar, Build's session list and the panes, a 1024 px window
          left the conversation ~90 px wide (v1.370.1, the user's report).
          IjHarnessView switches its row at the SAME breakpoint. The
          conversation box must NOT flex below xl: `flex-1` there made its
          height its content's, so a
          short session squeezed the transcript to a sliver over the panes
          and a long one grew the page instead of scrolling (v1.369.0
          review). Header, transcript and composer read as one unit, and
          the panes come after it, never inside it. */}
      <section
        data-testid="harness-conversation"
        className="flex h-[78vh] min-w-0 flex-none flex-col xl:h-auto xl:min-h-0 xl:flex-1"
      >
        <header className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 border-b border-white/[0.06] px-1 pb-2">
          <div className="min-w-0 flex-1">
            {renaming !== null ? (
              <input
                aria-label="Session title"
                data-testid="harness-title-input"
                value={renaming}
                autoFocus
                onChange={(e) => setRenaming(e.target.value)}
                onBlur={() => void saveTitle()}
                onKeyDown={(e) => {
                  if (e.key === "Enter") void saveTitle();
                  else if (e.key === "Escape") setRenaming(null);
                }}
                className="w-full rounded-md border hairline bg-transparent px-2 py-1 text-[14px] text-zinc-100 outline-none focus:border-accent/50"
              />
            ) : (
              <button
                type="button"
                data-testid="harness-title"
                onClick={() => setRenaming(view.title)}
                title="Rename this session"
                className="group flex max-w-full items-center gap-1.5 truncate text-left text-[14px] font-medium text-zinc-100"
              >
                <span className="truncate">{view.title}</span>
                <Pencil size={12} aria-hidden className="shrink-0 text-zinc-600 opacity-0 transition-opacity group-hover:opacity-100" />
              </button>
            )}
            {view.noFolder ? (
              // v1.379.0: a session with no folder says where its files are
              // (its own folder, made with its first message) and opens it.
              <p
                data-testid="harness-folder"
                data-no-folder="true"
                className="flex min-w-0 items-center gap-1 text-[12px] text-zinc-500"
                title={view.folder || view.workRoot}
              >
                <FolderX size={12} aria-hidden className="shrink-0" />
                <span className="shrink-0">{NO_FOLDER_NAME}</span>
                <span data-testid="harness-work-folder" className="min-w-0 truncate">
                  {view.folder
                    ? `· files in ${baseName(view.folder)}`
                    : "· its files get a folder of their own with your first message"}
                </span>
                {view.folder ? (
                  <button
                    type="button"
                    data-testid="harness-open-work-folder"
                    onClick={() => void openWorkFolder()}
                    title={`Open ${view.folder}`}
                    className="shrink-0 rounded px-1 text-zinc-400 hover:bg-white/[0.06] hover:text-zinc-100"
                  >
                    Open
                  </button>
                ) : null}
                {view.mode === "plan" ? (
                  <span data-testid="harness-mode-plan" className="ml-1 shrink-0 text-accent-soft">
                    · planning
                  </span>
                ) : null}
              </p>
            ) : (
              <p
                data-testid="harness-folder"
                className="flex min-w-0 items-center gap-1 truncate text-[12px] text-zinc-500"
                title={view.folder}
              >
                <FolderOpen size={12} aria-hidden className="shrink-0" />
                <span className="truncate">{view.folderName}</span>
                {view.mode === "plan" ? (
                  <span data-testid="harness-mode-plan" className="ml-1 shrink-0 text-accent-soft">
                    · planning
                  </span>
                ) : null}
              </p>
            )}
            {view.git ? (
              // v1.375.0 (part 2, Phase 4): the session's own branch and its
              // base; the worktree it works in is the title.
              <p
                data-testid="harness-branch"
                className="flex min-w-0 items-center gap-1 truncate text-[12px] text-zinc-500"
                title={`Works in ${view.git.worktree}; your checkout at ${view.git.checkout} is not touched.`}
              >
                <GitBranch size={12} aria-hidden className="shrink-0" />
                <span className="truncate">{branchLine(view.git)}</span>
              </p>
            ) : null}
            {view.branchedFrom ? (
              // v1.377.0: where this session was branched from (press: open it).
              <button
                type="button"
                data-testid="harness-branched-from"
                onClick={() => view.branchedFrom && onOpenSession?.(view.branchedFrom.threadId)}
                title="Open the session this one was branched from"
                className="flex min-w-0 max-w-full items-center gap-1 truncate text-left text-[12px] text-zinc-500 hover:text-zinc-200"
              >
                <GitFork size={12} aria-hidden className="shrink-0" />
                <span className="truncate">{branchedFromWords(view.branchedFrom)}</span>
              </button>
            ) : null}
          </div>
          {accounts && (vendor || currentAccount) ? (
            <AccountChip
              account={currentAccount}
              view={accounts}
              onPick={(a) => void pickAccount(a)}
              onMakeDefault={(a) => void makeDefault(a)}
              onOpenChange={(open) => {
                setAccountsOpen(open);
                if (open) void refreshAccounts();
              }}
              busy={accountBusy}
              note={accountNote}
            />
          ) : place ? (
            <span data-testid="harness-place" className="shrink-0 truncate px-2 text-[12px] text-zinc-500">
              {place}
            </span>
          ) : null}
          <div className="relative">
            <button
              type="button"
              aria-label="Session actions"
              aria-expanded={menuOpen}
              aria-haspopup="menu"
              onClick={() => setMenuOpen((v) => !v)}
              className="grid h-7 w-7 place-items-center rounded-md text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-200"
            >
              <MoreHorizontal size={15} />
            </button>
            {menuOpen ? (
              <div
                data-testid="harness-session-menu"
                className="absolute right-0 top-full z-30 mt-1 w-56 rounded-xl border border-white/10 bg-zinc-900 p-1 shadow-lg shadow-black/40"
              >
                <button
                  type="button"
                  onClick={() => {
                    setMenuOpen(false);
                    setRenaming(view.title);
                  }}
                  className="w-full rounded-lg px-2.5 py-1.5 text-left text-[13px] text-zinc-300 hover:bg-white/[0.06]"
                >
                  Rename
                </button>
                {compaction ? (
                  // v1.374.0: the summary standing in for the older turns.
                  <button
                    type="button"
                    data-testid="harness-read-summary"
                    onClick={() => {
                      setMenuOpen(false);
                      setSummaryOpen(true);
                    }}
                    className="w-full rounded-lg px-2.5 py-1.5 text-left text-[13px] text-zinc-300 hover:bg-white/[0.06]"
                  >
                    Read the summary of earlier turns
                  </button>
                ) : null}
                <button
                  type="button"
                  data-testid="harness-cap-open"
                  onClick={() => {
                    setMenuOpen(false);
                    setCapOpen(true);
                  }}
                  className="w-full rounded-lg px-2.5 py-1.5 text-left text-[13px] text-zinc-300 hover:bg-white/[0.06]"
                >
                  {decodeSessionCap(view.setup.cap) ? "Change the reply limit" : "Limit each reply"}
                </button>
                <button
                  type="button"
                  data-testid="harness-archive"
                  onClick={() => void archive(view.running)}
                  className="w-full rounded-lg px-2.5 py-1.5 text-left text-[13px] text-zinc-300 hover:bg-white/[0.06]"
                >
                  {view.running ? "Stop and archive" : "Archive"}
                </button>
                <div className="px-2.5 py-1.5">
                  <ConfirmButton
                    onConfirm={() => void remove()}
                    label="Delete"
                    confirmLabel="Delete for good"
                    title={
                      view.git && !view.git.removed
                        ? `Delete this session and its conversation. Its worktree goes to Iron Jarvis's trash; the branch ${view.git.branch} stays.`
                        : view.noFolder && view.folder
                          ? "Delete this session and its conversation. Its own folder goes only if it is empty; files in it stay."
                          : "Delete this session and its conversation"
                    }
                  />
                </div>
              </div>
            ) : null}
          </div>
          {view.branchedFrom?.code ? (
            // v1.377.0 review: where its CODE started (the committed work its
            // conversation describes), and what was not carried — a row of
            // its own across the header (the left column is narrow on a
            // phone, beside the account chip).
            <p data-testid="harness-branched-code" className="basis-full text-[12px] text-zinc-500">
              {branchedCodeWords(view.branchedFrom)}
            </p>
          ) : null}
        </header>
        {capOpen ? (
          // v1.392.0 (decision 8): the session's optional cap on a reply.
          <div className={`px-1 pt-2 ${HARNESS_COLUMN}`}>
            <CapForm
              current={decodeSessionCap(view.setup.cap)}
              busy={capBusy}
              onSave={saveCap}
              onClose={() => setCapOpen(false)}
            />
          </div>
        ) : null}

        <div
          ref={transcriptRef}
          data-testid="harness-transcript"
          className={`min-h-0 flex-1 space-y-6 overflow-y-auto px-1 py-3 ${HARNESS_TRANSCRIPT_ROWS}`}
          onScroll={onTranscriptScroll}
        >
          {offline ? <OfflineHint /> : null}
          {shown.length === 0 && !busy ? (
            <p data-testid="harness-empty" className="break-words pt-6 text-center text-[13px] text-zinc-500">
              {view.noFolder
                ? noFolderEmptyWords(view.workRoot)
                : `Ask for something to build or change in ${view.folderName}.`}
            </p>
          ) : null}
          {shown.map((m, i) =>
            m.role === "user" ? (
              m.steer ? (
                <div key={i} className="flex justify-end">
                  <p data-testid="harness-steer-line" className="max-w-[88%] text-right text-[12px] text-zinc-500">
                    Note while it worked: {m.content}
                  </p>
                </div>
              ) : (
                <div key={i} className="group group/msg flex flex-col items-end gap-1">
                  {m.content ? (
                    <div data-testid="harness-user-bubble" className={HARNESS_USER_BUBBLE}>
                      {m.content}
                    </div>
                  ) : null}
                  {/* v1.371.0: the pictures and files the message carried. */}
                  <SentAttachments paths={m.attachmentPaths ?? []} />
                  {/* v1.373.0: the folder's files picked with "@", and the
                      skill picked with "/". */}
                  <SentPicks files={m.filePaths} skill={m.skill} />
                  {/* v1.370.0: rewind to before this message (two presses;
                      a refusal is listed in words). Only a message the
                      daemon wrote has a turn to rewind to. */}
                  {m.turnId && m !== pending ? (
                    <RewindControl
                      threadId={threadId}
                      turnId={m.turnId}
                      disabled={busy || view.archived}
                      onRewound={(q, extras) => void onRewound(q, extras)}
                      onTouched={() => void onRewound("")}
                    />
                  ) : null}
                  {/* v1.377.0: a new session from before this message (it
                      goes in that session's box). */}
                  {m.turnId && m !== pending ? (
                    <BranchButton
                      at="question"
                      turnId={m.turnId}
                      working={branching === `question:${m.turnId}`}
                      disabled={!!branching}
                      onBranch={(t, at) => void branchFrom(t, at)}
                    />
                  ) : null}
                </div>
              )
            ) : (
              <div key={i} data-testid="harness-reply" className="group/msg min-w-0">
                {/* v1.391.0: this reply carried on one a restart cut off. */}
                {resumedFrom(m) ? (
                  <p data-testid="harness-resumed-line" className="mb-1 text-[12px] text-zinc-400">
                    {RESUMED_LINE}
                  </p>
                ) : null}
                {/* v1.393.0: this reply carried on with another account of
                    the same group ("<A> reached its limit; carried on with
                    <B> (same group)."). */}
                {(() => {
                  const move = decodeAccountMove(m.accountMove);
                  return move ? (
                    <p data-testid="harness-account-move" className="mb-1 text-[12px] text-zinc-400">
                      {accountMoveLine(move)}
                    </p>
                  ) : null;
                })()}
                <WorkLine
                  {...paneWorkInput(m)}
                  cutOff={m.truncated ? "truncated" : m.interrupted ? "interrupted" : null}
                  // Part 3, Phase 2: the reply's finish check, in one sentence.
                  finish={decodeFinish(m.finish)}
                />
                {/* v1.392.0: the reply summarized its own older rounds. */}
                <SummarizedLine summarized={decodeSummarized(m.summarized)} />
                {/* v1.372.0: the helper agents this reply's turn started. */}
                <SavedHelperRows raw={m.helpers} models={catalog.models} />
                {/* v1.397.0: the attempts on several models, side by side —
                    and Keep (only while nothing runs). */}
                {m.turnId && m.attempts ? (
                  <AttemptsCardView
                    raw={m.attempts}
                    threadId={threadId}
                    turnId={m.turnId}
                    canKeep={!busy && !view.archived}
                    onKept={() => {
                      void load();
                      onChanged();
                    }}
                  />
                ) : null}
                {m.content ? (
                  <div className={HARNESS_REPLY_PROSE}>
                    <MemoMarkdown content={m.content} />
                  </div>
                ) : null}
                {/* v1.374.0: the turn's task list, folded into its reply. */}
                <SettledPlan plan={replyPlan(m.plan, !!m.error)} />
                {/* v1.384.0: the reply met its STEP LIMIT — said plainly, and
                    the newest one offers ONE press to carry on (a new turn,
                    the same as typing "continue"; never automatic). */}
                {(() => {
                  const sl = decodeStepLimit(m.stepLimit);
                  if (!sl) return null;
                  const offer = m === lastReply && !busy && !view.archived;
                  return (
                    <div data-testid="harness-step-limit" className="mt-2 flex flex-wrap items-center gap-2">
                      <p className="flex items-center gap-1.5 text-[12px] text-zinc-400">
                        <CircleAlert size={12} aria-hidden className="shrink-0" />
                        {stepLimitWords(sl)}
                      </p>
                      {offer ? (
                        <button
                          type="button"
                          data-testid="harness-keep-going"
                          onClick={() => void send({ text: KEEP_GOING_TEXT })}
                          title={`Carry on from where it stopped (sends "${KEEP_GOING_TEXT}")`}
                          className="rounded-full bg-white/[0.06] px-3 py-1 text-[12px] text-zinc-200 transition-colors hover:bg-white/[0.1] hover:text-zinc-50 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50"
                        >
                          Keep going
                        </button>
                      ) : null}
                    </div>
                  );
                })()}
                {/* v1.392.0 (decision 8): the session's cap ended the reply —
                    the step-limit way: said plainly, and the newest offers
                    ONE press to carry on (a new reply, its own limit). */}
                <CapMetLine
                  cap={decodeCapMet(m.capMet)}
                  offer={m === lastReply && !busy && !view.archived}
                  keepGoingText={KEEP_GOING_TEXT}
                  onKeepGoing={() => void send({ text: KEEP_GOING_TEXT })}
                />
                {(() => {
                  // Phase 5: the limit card this reply ended on, as one quiet
                  // line in the transcript (the live card is below).
                  const lim = decodeAccountLimit(m.accountLimit);
                  return lim ? (
                    <p data-testid="harness-reply-limit" className="mt-1 flex items-start gap-1.5 text-[12px] text-tone-warn">
                      <CircleAlert size={12} className="mt-0.5 shrink-0" aria-hidden /> {lim.accountName}:{" "}
                      {limitCardWords(lim).title}
                    </p>
                  ) : null;
                })()}
                {/* Harness gaps (fix 2): the chat's failure line at a
                    session's scale (HarnessFailure) — the sentence (or its
                    headline beside a press), Sign in / the key link, Retry on
                    the NEWEST failed or cut-off reply, the server's own words
                    folded under Details. A safety refusal keeps its rewind
                    list (v1.378.0), never a Retry. A reply that ended on an
                    account's limit card says only that it is incomplete (the
                    card is its way out). */}
                <HarnessFailureLine
                  m={m.accountLimit ? { ...m, error: undefined } : m}
                  newest={m === newestReply}
                  busy={busy}
                  health={daemon.health}
                  turnProvider={m.route?.provider || provider}
                  retry={retryOffered(m, { newest: m === newestReply, busy, archived: !!view.archived })}
                  onRetry={() => retryReply(i)}
                  canRewind={canRewindHere}
                  onRewind={() => setRewindOpen(true)}
                  carriedOn={carriedOn(m, carried)}
                />
                <ReplyChanges
                  turn={(() => {
                    const w = paneTurnWindow(shown, i);
                    return w ? { ...w, sessionId: view.sessionId } : null;
                  })()}
                  undoFor={undoFor}
                  onUndo={onUndo}
                />
                <div className="mt-1 flex min-w-0 flex-wrap items-center empty:hidden">
                  <TurnReceipt
                    inline
                    modelName={answeredModelName(m.route, catalog.models)}
                    route={m.route}
                    toolsUsed={m.toolsUsed}
                    deniedTools={m.deniedTools}
                    trust={m.trust}
                    trustReason={m.trustReason}
                    trustNote={m.trustNote}
                    usage={m.usage}
                    steps={m.steps}
                    timing={m.timing}
                    documents={m.documents}
                    onOpenDocument={(path) => {
                      post("/documents/open", { path }).catch(() => {});
                    }}
                    undoFor={undoFor}
                    onUndo={onUndo}
                    // Harness gaps (fix 3): every receipt fact the chat page
                    // passes (app/chat/page.tsx), read off the daemon's reply
                    // line and decoded the chat's way — the approvals the
                    // user gave, the older messages left out to fit, the
                    // envelope's adaptation, a kept preference, the folder's
                    // rules and the earlier chats read.
                    {...receiptFacts(m)}
                  />
                  {/* v1.377.0: a new session with the conversation up to
                      and including this reply. */}
                  {m.turnId ? (
                    <BranchButton
                      at="reply"
                      turnId={m.turnId}
                      working={branching === `reply:${m.turnId}`}
                      disabled={!!branching}
                      onBranch={(t, at) => void branchFrom(t, at)}
                    />
                  ) : null}
                </div>
                <DoorsStrip doors={m.doors} />
              </div>
            ),
          )}
          {/* v1.377.0 (Phase 6): Next step — up to two follow-ups the session's
              model suggests (only when "Suggest follow-up questions" is on). A
              press puts the words in the box; nothing is sent. */}
          {!busy && nextSteps.length ? (
            <div data-testid="harness-next-steps" className="flex flex-wrap items-center gap-1.5">
              <span className="text-[12px] text-zinc-500">Next:</span>
              {nextSteps.map((s) => (
                <button
                  key={s}
                  type="button"
                  data-testid="harness-next-step"
                  onClick={() => pickNextStep(s)}
                  title="Put this in the message box (it is not sent)"
                  className="max-w-full truncate rounded-full bg-white/[0.06] px-3 py-1 text-left text-[12px] text-zinc-300 transition-colors hover:bg-white/[0.1] hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50"
                >
                  {s}
                </button>
              ))}
            </div>
          ) : null}
          {busy ? (
            <div data-testid="harness-live" className="min-w-0 space-y-2">
              {/* v1.372.0: each helper is ONE row (its own tool card is not
                  drawn a second time). */}
              <LiveHelperRows helpers={helpers} models={catalog.models} />
              {/* v1.397.0: each attempt is ONE row while it runs. */}
              <LiveAttemptRows attempts={attempts} />
              {(() => {
                const ids = helperCallIds(helpers);
                const cards = ids.size ? stream.tools.filter((t) => !ids.has(t.id)) : stream.tools;
                return cards.length ? <LiveToolRows cards={cards} since={stream.startedAt ?? null} /> : null;
              })()}
              <LiveText stream={stream} />
              {/* v1.392.0: the reply summarized its own older rounds. */}
              <SummarizedLine summarized={liveSummarized} />
              {/* v1.392.0: ten minutes with no progress — said once per
                  episode, with Stop the step (only that call) and Stop. */}
              {stall ? (
                <StallLine
                  notice={stall}
                  stopping={stallStopping}
                  note={stallNote}
                  onStopStep={() => void stopStalled(false)}
                  onStop={() => void stopStalled(true)}
                />
              ) : stallNote ? (
                <p data-testid="harness-stall-note" role="status" className="text-[12px] text-zinc-400">
                  {stallNote}
                </p>
              ) : null}
              {/* v1.374.0: the task list under the live reply, ticking off as
                  the turn's plan_update calls mark its steps. */}
              <LivePlan plan={livePlan} />
              {steerNotes.length ? (
                <ul data-testid="harness-steer-notes" className="space-y-0.5">
                  {steerNotes.map((n, k) => (
                    <li key={k} className="text-right text-[12px] text-zinc-500">
                      Note sent: {n}
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          ) : null}
          {limit ? (
            <AccountLimitCard
              limit={limit}
              waiting={waiting}
              wait={decodeAccountWait(view.accountWait)}
              busy={accountBusy}
              error={limitError}
              onResume={() => void resumeWhenReady()}
              onCancelWait={() => void cancelWait()}
              onChoose={() => {
                setChoosing((v) => !v);
                if (accounts === undefined) void refreshAccounts();
              }}
              chooser={
                choosing ? (
                  accounts ? (
                    <AccountList
                      view={accounts}
                      currentId={limit.account || null}
                      onPick={(a) => void chooseAndContinue(a)}
                      onMakeDefault={(a) => void makeDefault(a)}
                      busy={accountBusy}
                      title="Continue this conversation on:"
                    />
                  ) : accounts === null ? (
                    <p className="text-[12px] text-zinc-400">This version of Iron Jarvis has no account list.</p>
                  ) : (
                    <p className="flex items-center gap-1.5 text-[12px] text-zinc-500">
                      <Loader2 size={12} className="animate-spin" /> Reading the accounts…
                    </p>
                  )
                ) : null
              }
            />
          ) : null}
          {/* v1.370.0: the experience library's suggestions for this folder
              (Accept / Decline), never while a reply runs. */}
          {!busy ? <LibraryCards threadId={threadId} refreshKey={turnsEnded + rewinds} /> : null}
          {compactNote ? (
            <p data-testid="harness-compact-note" role="status" className="text-[12px] text-zinc-400">
              {compactNote}
            </p>
          ) : null}
          {usageNote ? (
            // v1.379.0: "/usage" (Claude Code's /cost) — what this session used.
            <p data-testid="harness-usage-note" role="status" className="text-[12px] text-zinc-400">
              {usageNote}
            </p>
          ) : null}
          {rewindNote ? (
            // v1.377.0: a rewind of COMMITTED work — the commits stayed.
            <p data-testid="harness-rewind-commits" role="status" className="text-[12px] text-tone-warn">
              {rewindNote}
            </p>
          ) : null}
          {branchError ? <ErrorNote>{branchError}</ErrorNote> : null}
          {/* Harness gaps (fix 2): a failed turn that STARTED has its failure
              on its reply line (with Retry and Details) once the session is
              read again; this note stays only for a failure with no line (a
              message that never started — its words are back in the box —
              or a read that failed), with the live failure's Details. */}
          {error &&
          !(
            errorTech?.text === error &&
            errorTech.turn &&
            newestReply?.turnId === errorTech.turn &&
            (newestReply.error || newestReply.accountLimit)
          ) ? (
            <>
              <ErrorNote>{error}</ErrorNote>
              {errorTech?.text === error ? (
                <div className="flex flex-wrap items-center">
                  <FailureDetails technical={errorTech.technical} testId="harness-error-details" />
                </div>
              ) : null}
            </>
          ) : null}
        </div>
        {summaryOpen && compaction ? (
          <CompactionCard info={compactionCardInfo(compaction)} onClose={() => setSummaryOpen(false)} />
        ) : null}

        {/* --------------------------------------------- the dock + composer */}
        <div className={`shrink-0 px-1 pb-1 ${HARNESS_COLUMN}`}>
          {asking ? (
            <DockAsk
              asks={asks}
              scope="session"
              // The plan door's plan is the reply above it: the live one while
              // the turn runs (read from the stream's store when the card
              // appears), else the last reply kept.
              planText={(busy ? stream.textStore?.get() || stream.text : "") || lastReply?.content}
              onAnswerElicitation={(id, action, content) => answerElicitation(id, action, content)}
              onDecideSampling={(id, decision) => decideSampling(id, decision)}
              onAnswerQuestion={answerAsk}
              onStop={() => void stop()}
              returnFocus={() => boxRef.current?.focus({ preventScroll: true })}
            />
          ) : null}
          {asking ? (
            <p className="px-2 pt-1 text-[11px] text-zinc-600">{dockAskKeyHint(asks[0].kind, asks[0])}</p>
          ) : null}
          {queued.length ? (
            <div data-testid="harness-queued" className="mb-1.5 flex flex-wrap gap-1.5 px-1">
              {queued.map((q, k) => (
                <span
                  key={k}
                  className="inline-flex max-w-full items-center gap-1 rounded-full bg-white/[0.06] px-2.5 py-1 text-[12px] text-zinc-400"
                >
                  <span className="truncate">Waiting to send: {queuedWords(q)}</span>
                  <button
                    type="button"
                    aria-label="Remove this waiting message"
                    onClick={() => {
                      const next = queued.filter((_, j) => j !== k);
                      setQueued(next);
                      persistQueue(next);
                    }}
                    className="shrink-0 text-zinc-500 hover:text-zinc-200"
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
          ) : null}
          <div
            data-testid="harness-composer"
            data-dock-composer=""
            inert={asking}
            aria-hidden={asking || undefined}
            className={`${asking ? "hidden" : "flex"} ${PANE_COMPOSER_CARD}`}
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
            {/* v1.373.0: "/" and "@" — above the box, never over it. */}
            {slashToken ? (
              <SlashMenu
                listId={listId ?? ""}
                rows={slashList}
                active={activeRow}
                loading={slashLoading}
                note={[folderSkillNote, atStart ? commandsNote : ""].filter(Boolean).join(" ")}
                onPick={(row) => pickSlash(row, "click")}
                onHover={setMenuActive}
              />
            ) : atToken ? (
              <AtMenu
                listId={listId ?? ""}
                rows={atList.rows}
                more={atList.more}
                active={activeRow}
                loading={atLoading}
                failed={folderFiles.failed}
                onPick={pickAt}
                onHover={setMenuActive}
                noFolderYet={view.noFolder && !view.folder}
              />
            ) : invoking && !rewindOpen ? (
              // v1.379.0: "/name " typed — what Enter runs, with its hint.
              <SlashInvoking target={invoking} />
            ) : rewindOpen ? (
              <RewindList
                threadId={threadId}
                disabled={busy || view.archived}
                onRewound={(q, extras) => void onRewound(q, extras)}
                onTouched={() => void onRewound("")}
                onClose={() => {
                  setRewindOpen(false);
                  boxRef.current?.focus({ preventScroll: true });
                }}
              />
            ) : null}
            <PickedChips
              skill={skill}
              files={atFiles}
              onRemoveSkill={() => setSkill(null)}
              onRemoveFile={(i) => setAtFiles((cur) => cur.filter((_, j) => j !== i))}
            />
            <AttachmentChips files={att.files} uploading={att.uploading} onRemove={att.remove} />
            {att.problem ? (
              <p data-testid="harness-attach-problem" role="alert" className="px-3 pt-1 text-[12px] text-tone-warn">
                {att.problem}
              </p>
            ) : null}
            <textarea
              ref={boxRef}
              aria-label="Message"
              aria-autocomplete="list"
              aria-controls={listId}
              aria-activedescendant={activeRowId}
              data-testid="harness-input"
              // v1.377.0 review: the keys, only those that work here.
              title={harnessKeysHint(isDesktopShell())}
              placeholder={
                busy
                  ? "Add a note while it works (Enter), or Ctrl+Enter to send after it"
                  : view.noFolder
                    ? "Ask for anything…  / for commands and skills, @ for files"
                    : `Build in ${view.folderName}…  / for commands and skills, @ for files`
              }
              value={input}
              rows={2}
              disabled={offline}
              onChange={(e) => {
                setInput(e.target.value);
                setCaret(e.target.selectionStart ?? e.target.value.length);
                // Typing again brings an Escaped menu back (the chat's rule).
                setSlashDismissed(false);
                setAtDismissed(false);
              }}
              onKeyUp={trackCaret}
              onClick={trackCaret}
              onFocus={trackCaret}
              onSelect={trackCaret}
              onPaste={(e) => {
                // v1.371.0: a pasted picture is attached; pasted text is typed.
                const pasted = pastedFiles(e.clipboardData);
                if (!pasted.length) return;
                e.preventDefault();
                void att.add(pasted, { pasted: true });
              }}
              onKeyDown={(e) => {
                if (e.nativeEvent.isComposing || e.keyCode === 229) return;
                // v1.373.0: an open "/" or "@" menu owns ↑↓, Enter, Tab, Escape.
                if (onMenuKey(e)) return;
                if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && busy) {
                  e.preventDefault();
                  queueMessage();
                  return;
                }
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  // v1.379.0: "/name words" runs by its name — while the
                  // lists are still being read, Enter waits (it would send
                  // the words as plain text).
                  if (leading && slashLoading) return;
                  void send();
                }
              }}
              className={HARNESS_COMPOSER_BOX}
            />
            {/* v1.400.0: one row whatever model is picked (lib/toolbarFit) —
                the chat's toolbar rules: `data-fit-row` keeps it one line (the
                right group gives way through the model chip's name), and one
                `data-fit-<step>` per step taken in HARNESS_FIT_ORDER, each
                read by its own chip; no `data-fit-row` (a phone, or a box too
                narrow even for that) wraps as before. */}
            <div
              ref={harnessFitRef}
              data-testid="harness-composer-toolbar"
              className="group/fit flex min-w-0 flex-wrap items-center gap-1 px-2 pb-2 data-[fit-row]:flex-nowrap"
            >
              <AttachButton onPick={(picked) => void att.add(picked)} disabled={offline} />
              <PermissionChip
                value={view.setup.approvalMode || "approve_for_me"}
                onChange={(mode) => void patchSetup({ approval_mode: asPermissionMode(mode) })}
                iconOnlyOnPhone
                iconOnly={harnessOn("ask")}
              />
              {(() => {
                const planning = view.mode === "plan";
                const words = planning ? "Planning" : "Plan first";
                const title =
                  view.mode === "plan"
                    ? "Plan mode is on: the session only reads and plans, then asks you to approve the plan. Press to let it work."
                    : "Plan first: the session only reads and writes a plan for you to approve before it changes anything.";
                return (
                  <button
                    type="button"
                    data-testid="harness-plan-mode"
                    aria-pressed={view.mode === "plan"}
                    // v1.400.0: its words were its name; they can go now.
                    aria-label={words}
                    onClick={() => void patchSetup({ mode: view.mode === "plan" ? "act" : "plan" })}
                    title={harnessOn("plan") && !planning ? iconOnlyTitle(words, title) : title}
                    // Review: only "Plan first" (off) gives up its words. On,
                    // "Planning" is the session's mode, and the list icon in
                    // the accent alone would tell it by hue.
                    className={`${composerChipClass(planning)}${planning ? "" : ` ${fitIconChip("plan")}`}`}
                  >
                    <ListChecks size={14} aria-hidden className="shrink-0" />
                    <span className={planning ? undefined : fitWords("plan")}>{words}</span>
                  </button>
                );
              })()}
              {/* v1.397.0: try the next message on up to three models, each on
                  its own branch (the words "try this with …" ask the same). */}
              <TryOnControl
                models={catalog.models}
                localOnly={view.setup.localOnly}
                picks={tryOn}
                onChange={setTryOn}
                disabled={view.archived || view.mode === "plan"}
                iconOnly={harnessOn("tryon")}
              />
              {/* Review (v1.374.0): this group may WRAP, right-aligned, and
                  never grows past the row. With the meter added, a model that
                  offers reasoning levels pushed Send out of the box at 390 px
                  ("Summarizing earlier turns…" / "Earlier turns summarized"
                  by ~60 px, the percent and Compact by ~4-15 px — measured in
                  a real browser); a row that fits is unchanged. */}
              <div
                data-testid="harness-box-actions"
                className="ml-auto flex min-w-0 max-w-full flex-wrap items-center justify-end gap-1 group-data-[fit-row]/fit:flex-nowrap group-data-[fit-row]/fit:justify-start group-data-[fit-row]/fit:pl-3"
              >
                {/* v1.374.0: how full the conversation is for this model
                    (the daemon's reading of the newest reply), and Compact
                    past the daemon's threshold. */}
                <ContextMeter
                  reading={reading?.context ?? null}
                  offer={offerCompact}
                  summarized={summarized}
                  busy={compacting || elsewhereCompacting}
                  onCompact={() => void compact()}
                  onViewSummary={() => setSummaryOpen(true)}
                />
                {levels.length > 0 ? (
                  <ReasoningChip
                    levels={levels}
                    value={view.setup.reasoning}
                    def={reasoningDef}
                    short={harnessOn("short")}
                    onChange={(level) => void patchSetup({ reasoning: level })}
                  />
                ) : null}
                <ModelMenu
                  open={modelMenuOpen}
                  onOpenChange={setModelMenuOpen}
                  choice={choice}
                  models={catalog.models}
                  trigger={trigger}
                  onPick={(v, account) => void pickModel(v, account)}
                  companies={companies}
                />
                {busy ? (
                  <button
                    type="button"
                    data-testid="harness-stop"
                    aria-label="Stop"
                    title="Stop this reply"
                    onClick={() => void stop()}
                    className="ml-1 grid h-[30px] w-[30px] shrink-0 place-items-center rounded-full bg-white/[0.08] text-zinc-200 transition-colors hover:bg-white/[0.14]"
                  >
                    <Square size={12} fill="currentColor" />
                  </button>
                ) : (
                  <button
                    type="button"
                    data-testid="harness-send"
                    aria-label="Send"
                    title="Send (Enter)"
                    disabled={!sendReady}
                    onClick={() => void send()}
                    className={paneSendClass(sendReady)}
                  >
                    <ArrowUp size={15} strokeWidth={2.4} />
                  </button>
                )}
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ------------------------------------------------------- the panes */}
      <aside className="flex h-[70vh] w-full shrink-0 flex-col xl:h-auto xl:min-h-0 xl:w-[320px] 2xl:w-[400px]">
        <HarnessPanes
          view={view}
          livePlan={livePlan}
          terminal={terminal}
          refreshKey={turnsEnded + rewinds}
          pageSteps={finishedPageSteps(stream.tools)}
          timeline={timelineLive}
          models={catalog.models}
          account={currentAccount}
          undoFor={undoFor}
          onUndo={onUndo}
          onSessionChanged={() => void load()}
          onSessionGone={onGone}
        />
      </aside>
    </div>
  );
}
