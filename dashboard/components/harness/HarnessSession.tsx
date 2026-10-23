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

import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import {
  ArrowUp,
  CircleAlert,
  ListChecks,
  FolderOpen,
  GitBranch,
  Loader2,
  MoreHorizontal,
  Pencil,
  Square,
} from "lucide-react";
import { branchLine } from "@/lib/buildGit";
import { ApiError, get, post, put } from "@/lib/api";
import { useDaemon } from "@/lib/daemon";
import { useModels } from "@/lib/useModels";
import { useChatStream, useLiveText, StreamError } from "@/lib/useChatStream";
import { useLiveThinking } from "@/lib/liveThinking";
import { mintTurnId } from "@/lib/turnRejoin";
import { turnErrorText } from "@/lib/failureLine";
import { answeredModelName } from "@/lib/answeredModel";
import { composerChipClass } from "@/lib/composerChips";
import { asPermissionMode } from "@/lib/permissionLevels";
import { phaseLabel } from "@/lib/stepWords";
import { collectDockAsks, dockAskKeyHint } from "@/lib/dockAsk";
import { answerElicitation, decideSampling } from "@/lib/mcpInteract";
import { machineName, modelGroupName, providerDisplay, providerHeading } from "@/lib/onboarding";
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
import { paneTurnWindow, paneWorkInput } from "@/components/terminal/paneWork";
import { PANE_COMPOSER_CARD, paneSendClass } from "@/components/terminal/paneChatLook";
import { HarnessPanes, type SessionTerminalApi } from "@/components/harness/HarnessPanes";
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
import { LiveHelperRows, SavedHelperRows } from "@/components/harness/HarnessHelpers";
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
import {
  AttachButton,
  AttachmentChips,
  SentAttachments,
  pastedFiles,
  useHarnessAttachments,
  type HarnessFile,
} from "@/components/harness/HarnessAttachments";
import { previewAddressFor } from "@/components/harness/BrowserPreview";
import { finishedPageSteps } from "@/lib/agentBrowser";
import { AtMenu, PickedChips, RewindList, SentPicks, SlashMenu } from "@/components/harness/HarnessComposerMenus";
import { slashTokenAt, spliceToken, tokenAt } from "@/lib/slash";
import { atMenuOpen, slashMenuOpen } from "@/lib/composerMenus";
import { useProjectFilesState } from "@/lib/atMenuFiles";
import {
  atFileRows,
  decodeSkills,
  queuedItemBody,
  queuedWords,
  READ_LIMIT_WORDS,
  READ_PER_MESSAGE,
  sessionCommands,
  skillFields,
  slashRows,
  withFilesRestored,
  withMessageRestored,
  type AtFile,
  type PickedSkill,
  type QueuedItem,
  type SkillOption,
  type SlashRow,
} from "@/lib/buildComposer";
import {
  armResumeWhenReady,
  cancelResumeWhenReady,
  chooseAccountAndContinue,
  decodeAccountLimit,
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
  decodeSessionView,
  deleteSession,
  getSession,
  isMissing,
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
/** The chat's own cap on waiting messages (routes/chat MAX_THREAD_QUEUE). */
const MAX_QUEUED = 3;

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

export function HarnessSession({
  threadId,
  terminal,
  listRunning,
  onChanged,
  onGone,
}: {
  threadId: string;
  terminal: SessionTerminalApi;
  /** The session list says a turn runs (a resumed wait, another window). */
  listRunning: boolean;
  /** Something the left pane shows changed (title, a new turn). */
  onChanged: () => void;
  /** The session was deleted or archived from here. */
  onGone: () => void;
}) {
  const daemon = useDaemon();
  const catalog = useModels();
  const stream = useChatStream({ textInState: false });
  const [view, setView] = useState<BuildSessionView | null>(null);
  const viewRef = useRef<BuildSessionView | null>(null);
  viewRef.current = view;
  const [loadError, setLoadError] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<BuildMessage | null>(null);
  const [questions, setQuestions] = useState<AskUserAsk[]>([]);
  const [steerNotes, setSteerNotes] = useState<string[]>([]);
  // v1.372.0: the helper agents the running turn started, one row each
  // (its `helper` frames; lib/buildHelpers). The reply keeps them after.
  const [helpers, setHelpers] = useState<HelperView[]>([]);
  // v1.373.0: a waiting message keeps its own skill and files.
  const [queued, setQueued] = useState<QueuedItem[]>([]);
  const turnRef = useRef<string | null>(null);
  const [turnId, setTurnId] = useState<string | null>(null);
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
  const [rewindOpen, setRewindOpen] = useState(false);
  const transcriptRef = useRef<HTMLDivElement | null>(null);
  const [modelMenuOpen, setModelMenuOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [renaming, setRenaming] = useState<string | null>(null);
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
   *  rows and the library read again. */
  const onRewound = useCallback(
    async (question: string) => {
      await load();
      if (question) setInput((cur) => (cur.trim() ? cur : question));
      setUndone(new Set());
      void refreshUndo();
      setRewinds((n) => n + 1);
      onChanged();
    },
    [load, refreshUndo, onChanged],
  );

  // ----------------------------------------------------------- the turn
  const onFrame = useCallback((event: string, data: Record<string, unknown>) => {
    if (event === "ask_user") {
      const ask = decodeAskUser(data);
      if (ask) setQuestions((prev) => [...prev.filter((q) => q.id !== ask.id), ask]);
    } else if (event === "mcp_resolved") {
      const done = decodeAskResolved(data);
      if (done) setQuestions((prev) => prev.map((q) => (q.id === done.id ? { ...q, outcome: done.outcome } : q)));
    } else if (event === "account_limit") {
      const lim = decodeAccountLimit(data);
      if (lim) {
        limitSeenRef.current = true;
        setLimit(lim);
        setWaiting(false);
        setChoosing(false);
      }
    } else if (event === "helper") {
      setHelpers((prev) => applyHelperFrame(prev, data));
    }
  }, []);

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
      if (limitSeenRef.current) void refreshAccounts();
      // The user may have sent the NEXT message while this read was out (the
      // composer is free the moment the turn ends): its question bubble,
      // notes and questions belong to that turn and stay (v1.369.0 review).
      if (turnRef.current === null) {
        setPending(null);
        setSteerNotes([]);
        // The reply line now keeps the helpers (its `helpers`).
        setHelpers([]);
        setQuestions((prev) => prev.map((q) => (q.outcome ? q : { ...q, outcome: "stopped" })));
      }
      const sent = readSentSteers(threadId);
      const back = unreadSteersFor(sent, finished, v?.messages ?? [], unread);
      writeSentSteers(
        threadId,
        sent.filter((s) => s.turnId !== finished),
      );
      if (back.length) setInput((cur) => withSteersRestored(cur, back));
      setTurnsEnded((n) => n + 1);
      onChanged();
      return clean;
    },
    [load, onChanged, threadId, refreshAccounts],
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
      if (text) setInput((cur) => withMessageRestored(cur, text));
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
    },
    [restoreUploads],
  );

  const runTurn = useCallback(
    async (text: string | null, rejoinId?: string, extras?: TurnExtras) => {
      const id = rejoinId ?? mintTurnId();
      turnRef.current = id;
      setTurnId(id);
      setSending(true);
      setError(null);
      setCompactNote(null);
      // A new (or rejoined: its frames replay) turn draws its own helpers.
      setHelpers([]);
      limitSeenRef.current = false;
      let started = !!rejoinId;
      // A NEW turn retires the limit card at its first real frame (once —
      // the turn's own `account_limit` must stay up after it).
      let retired = !!rejoinId;
      let unread: string[] = [];
      let clean = false;
      try {
        // v1.371.0: the message's files, and the page the Browser pane
        // shows (the agent may screenshot that dev server without asking).
        // v1.373.0: the files picked with "@" in the folder, and the skill
        // picked with "/" (`skill` for yours, `folder_skill` for the folder's).
        const preview = previewAddressFor(threadId);
        const ups = extras?.attachments ?? [];
        const picks = extras?.files ?? [];
        const res = await stream.run(
          rejoinId
            ? undefined
            : {
                text,
                turn_id: id,
                ...(ups.length ? { attachments: ups.map((f) => f.path) } : {}),
                ...(picks.length ? { files: picks.map((f) => f.path) } : {}),
                ...skillFields(extras?.skill),
                ...(preview ? { preview_url: preview } : {}),
              },
          undefined,
          undefined,
          rejoinId
            ? {
                rejoin: rejoinId,
                onFrame: (e, d) => {
                  started = true;
                  onFrame(e, d);
                },
              }
            : {
                path: `/build/sessions/${encodeURIComponent(threadId)}/turn`,
                onFrame: (e, d) => {
                  started = true;
                  if (!retired && e !== "phase") {
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
        if (!limitSeenRef.current) setError(turnErrorText(e));
        if (!started && text !== null) {
          // Never started: the words and what rode them go back in the box,
          // nothing was lost (v1.373.0: a file-only message too).
          setPending(null);
          restoreToBox(text, extras);
        }
      } finally {
        turnRef.current = null;
        setTurnId(null);
        setSending(false);
      }
      if (started) await settle(id, unread, clean);
    },
    [stream, threadId, onFrame, load, settle, restoreToBox],
  );

  // Open: read the session, its accounts, and follow a turn already running.
  useEffect(() => {
    let live = true;
    setView(null);
    setInput("");
    setError(null);
    setPending(null);
    setQuestions([]);
    setLimit(null);
    void refreshAccounts();
    void load().then(async (v) => {
      if (!live || !v) return;
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
      if (v.running && v.turnId && v.turnId !== "running") void runTurn(null, v.turnId);
    });
    return () => {
      live = false;
      stream.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [threadId]);

  // The list saw a turn start here (a resumed wait, another window).
  useEffect(() => {
    if (!listRunning || busy) return;
    void load().then((v) => {
      if (v?.running && v.turnId && v.turnId !== "running") void runTurn(null, v.turnId);
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
    if (compactingRef.current) {
      if (item) {
        const back = [item, ...queueRef.current].slice(0, MAX_QUEUED);
        setQueued(back);
        persistQueue(back);
      }
      return;
    }
    const text = (item ? item.text : (boxRef.current?.value ?? input)).trim();
    const files: HarnessFile[] = fromBox ? att.filesRef.current : (item.attachments ?? []);
    const picks: AtFile[] = fromBox ? atFilesRef.current : (item.files ?? []);
    const picked: PickedSkill | null = fromBox ? skillRef.current : (item.skill ?? null);
    if (!text && files.length === 0 && picks.length === 0) return;
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
    }
    setPending({
      role: "user",
      content: text,
      at: new Date().toISOString(),
      ...(paths.length ? { attachmentPaths: paths } : {}),
      ...(filePaths.length ? { filePaths } : {}),
      ...(picked ? { skill: picked.name } : {}),
    });
    await runTurn(text, undefined, { attachments: files, files: picks, skill: picked });
  }

  async function steer(text: string) {
    const id = turnRef.current;
    if (!id) return;
    try {
      await post(`/chat/turns/${encodeURIComponent(id)}/steer`, { text });
      writeSentSteers(threadId, [...readSentSteers(threadId), { turnId: id, text }]);
      setSteerNotes((prev) => [...prev, text]);
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
    if (!text && files.length === 0 && picks.length === 0) return;
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
    };
    const next = [...queued, item];
    setQueued(next);
    persistQueue(next);
    setInput("");
    setCaret(0);
    att.clear();
    setAtFiles([]);
    setSkill(null);
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
  // The list is the daemon's cached snapshot: read it again while it is open.
  useVisibleInterval(() => void refreshAccounts(), ACCOUNTS_POLL_MS, accountsOpen);

  /** Read the session again after an account press and follow a turn the
   *  daemon is running for it (a wait that resumed at once). The press's own
   *  answer can be a moment behind: a Cancel or a plain pick that lands while
   *  the daemon re-checks the account stops the continuation it was about to
   *  start, and only a fresh read says which way that went (v1.368.0 review). */
  async function rereadAndFollow(): Promise<void> {
    const v = await load();
    if (v?.running && v.turnId && v.turnId !== "running" && !turnRef.current) void runTurn(null, v.turnId);
  }

  /** A pick in the chip's list: the session moves to that account from its
   *  next message. NEVER a continue — "carry on now" is offered only from the
   *  limit card (lib/buildAccounts chooseAccountAndContinue). */
  async function pickAccount(a: BuildAccount) {
    setAccountBusy(true);
    setAccountNote(null);
    try {
      const v = decodeSessionView(await setSessionAccount(threadId, a.id));
      if (v) adopt(v);
      await rereadAndFollow();
      onChanged();
    } catch (e) {
      setAccountNote(e instanceof Error ? e.message : String(e));
    } finally {
      setAccountBusy(false);
    }
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
  async function compact() {
    if (compactingRef.current || !view) return;
    if (busy) {
      setError("Wait for the reply to finish, then compact.");
      return;
    }
    compactingRef.current = true;
    setCompacting(true);
    setCompactNote(null);
    setError(null);
    try {
      const done = await compactSession(threadId);
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
  // The two skill lists, asked the first time "/" opens in this session (an
  // older Iron Jarvis has no folder list: none, quietly).
  useEffect(() => {
    if (!slashOpen) return;
    if (yourSkills === null) {
      get<unknown>("/skills")
        .then((r) => setYourSkills(decodeSkills(r, "yours")))
        .catch(() => setYourSkills([]));
    }
    if (folderSkills === null) {
      get<unknown>(`/build/sessions/${encodeURIComponent(threadId)}/skills`)
        .then((r) => {
          setFolderSkills(decodeSkills(r, "folder"));
          const note = r && typeof r === "object" ? (r as { note?: unknown }).note : "";
          setFolderSkillNote(typeof note === "string" ? note : "");
        })
        .catch(() => setFolderSkills([]));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slashOpen]);
  const commands = useMemo(() => sessionCommands(view?.mode ?? "act"), [view?.mode]);
  const slashList: SlashRow[] = slashToken
    ? slashRows(slashToken.query, commands, folderSkills, yourSkills, skill)
    : [];
  // The SESSION's folder only (GET /fs/files on it, the chat's own query).
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
  const slashLoading = yourSkills === null || folderSkills === null;
  const atLoading = folderFiles.listing === null && !folderFiles.failed;
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

  function pickSlash(row: SlashRow) {
    const tok = slashTokenAt(input, caret);
    if (!tok || !view) return;
    setSlashDismissed(false);
    consumeToken(tok);
    if (row.kind === "skill") {
      setSkill({ name: row.skill.name, source: row.skill.source });
      return;
    }
    // The session's commands call what the box already has.
    if (row.command.id === "plan") void patchSetup({ mode: view.mode === "plan" ? "act" : "plan" });
    else if (row.command.id === "compact") void compact(); // v1.374.0: the meter's Compact
    else if (row.command.id === "rewind") setRewindOpen(true);
    else setModelMenuOpen(true);
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
        if (slashToken) pickSlash(slashList[cur]);
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
  useEffect(() => {
    const el = transcriptRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [view?.messages.length, pending, stream.tools.length, limit]);

  const asks = collectDockAsks({
    approval: stream.approval,
    mcpAsks: stream.mcpAsks,
    questions,
  });
  const asking = asks.length > 0;
  const shown: BuildMessage[] = useMemo(() => {
    // The daemon's own "carry on" questions are not shown (Phase 5).
    const base = shownMessages(view?.messages ?? []);
    if (!pending) return base;
    // The daemon wrote the question already (a re-read mid-turn): not twice.
    const dup = base.some((m) => m.role === "user" && m.content === pending.content && m.turnId && m.turnId === turnId);
    return dup ? base : [...base, pending];
  }, [view?.messages, pending, turnId]);
  const livePlan = planFromCards(stream.tools);
  // v1.374.0: how full the conversation is — the newest reply's reading, the
  // daemon's own numbers — and the summary standing in for the older turns.
  const reading = latestContext(view?.messages ?? []);
  const compaction = decodeCompaction(view?.compaction);
  const beforeSummary = readingIsBeforeSummary(reading?.at ?? null, compaction);
  const summarized = !!compaction && beforeSummary;
  const offerCompact = offersCompact(reading?.context ?? null, beforeSummary);
  const lastReply = [...shown].reverse().find((m) => m.role === "assistant");
  const choice = view?.setup.provider && view?.setup.model ? `${view.setup.provider}::${view.setup.model}` : "";
  const levels = reasoningLevelsOf(reasoningRowFor(choice, catalog.models, defaultProvider, defaultModel));
  const reasoningDef = reasoningDefaultOf(reasoningRowFor(choice, catalog.models, defaultProvider, defaultModel));
  const trigger = modelTriggerOf(choice, catalog.models, defaultProvider, defaultModel);
  const place = !vendor && !currentAccount ? placeWords(provider, model, catalog.models) : "";
  // v1.371.0: a picture alone is a message; one still going up is waited for.
  // v1.373.0: so is a file picked with "@".
  const sendReady =
    (!!input.trim() || att.files.length > 0 || att.uploading || atFiles.length > 0) &&
    !!view &&
    !offline &&
    !compacting;

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
                        : "Delete this session and its conversation"
                    }
                  />
                </div>
              </div>
            ) : null}
          </div>
        </header>

        <div
          ref={transcriptRef}
          data-testid="harness-transcript"
          className={`min-h-0 flex-1 space-y-6 overflow-y-auto px-1 py-3 ${HARNESS_TRANSCRIPT_ROWS}`}
        >
          {offline ? <OfflineHint /> : null}
          {shown.length === 0 && !busy ? (
            <p data-testid="harness-empty" className="pt-6 text-center text-[13px] text-zinc-500">
              Ask for something to build or change in {view.folderName}.
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
                <div key={i} className="group flex flex-col items-end gap-1">
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
                      onRewound={(q) => void onRewound(q)}
                      onTouched={() => void onRewound("")}
                    />
                  ) : null}
                </div>
              )
            ) : (
              <div key={i} data-testid="harness-reply" className="min-w-0">
                <WorkLine
                  {...paneWorkInput(m)}
                  cutOff={m.truncated ? "truncated" : m.interrupted ? "interrupted" : null}
                />
                {/* v1.372.0: the helper agents this reply's turn started. */}
                <SavedHelperRows raw={m.helpers} />
                {m.content ? (
                  <div className={HARNESS_REPLY_PROSE}>
                    <MemoMarkdown content={m.content} />
                  </div>
                ) : null}
                {/* v1.374.0: the turn's task list, folded into its reply. */}
                <SettledPlan plan={replyPlan(m.plan, !!m.error)} />
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
                {m.error && !m.accountLimit ? (
                  <p data-testid="harness-reply-error" className="mt-1 flex items-start gap-1.5 text-[12px] text-tone-danger">
                    <CircleAlert size={12} className="mt-0.5 shrink-0" aria-hidden /> {m.error}
                  </p>
                ) : m.interrupted ? (
                  <p className="mt-1 flex items-center gap-1.5 text-[12px] text-tone-warn">
                    <CircleAlert size={12} aria-hidden /> This answer is incomplete.
                  </p>
                ) : null}
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
                    folderRules={m.folderRules}
                    onOpenDocument={(path) => {
                      post("/documents/open", { path }).catch(() => {});
                    }}
                    undoFor={undoFor}
                    onUndo={onUndo}
                  />
                </div>
                <DoorsStrip doors={m.doors} />
              </div>
            ),
          )}
          {busy ? (
            <div data-testid="harness-live" className="min-w-0 space-y-2">
              {/* v1.372.0: each helper is ONE row (its own tool card is not
                  drawn a second time). */}
              <LiveHelperRows helpers={helpers} />
              {(() => {
                const ids = helperCallIds(helpers);
                const cards = ids.size ? stream.tools.filter((t) => !ids.has(t.id)) : stream.tools;
                return cards.length ? <LiveToolRows cards={cards} since={stream.startedAt ?? null} /> : null;
              })()}
              <LiveText stream={stream} />
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
              waitingResetsAt={decodeAccountWait(view.accountWait)?.resetsAt ?? null}
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
          {error ? <ErrorNote>{error}</ErrorNote> : null}
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
            <p className="px-2 pt-1 text-[11px] text-zinc-600">{dockAskKeyHint(asks[0].kind)}</p>
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
                note={folderSkillNote}
                onPick={pickSlash}
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
              />
            ) : rewindOpen ? (
              <RewindList
                threadId={threadId}
                disabled={busy || view.archived}
                onRewound={(q) => void onRewound(q)}
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
              placeholder={
                busy
                  ? "Add a note while it works (Enter), or Ctrl+Enter to send after it"
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
                  void send();
                }
              }}
              className={HARNESS_COMPOSER_BOX}
            />
            <div className="flex min-w-0 flex-wrap items-center gap-1 px-2 pb-2">
              <AttachButton onPick={(picked) => void att.add(picked)} disabled={offline} />
              <PermissionChip
                value={view.setup.approvalMode || "approve_for_me"}
                onChange={(mode) => void patchSetup({ approval_mode: asPermissionMode(mode) })}
                iconOnlyOnPhone
              />
              <button
                type="button"
                data-testid="harness-plan-mode"
                aria-pressed={view.mode === "plan"}
                onClick={() => void patchSetup({ mode: view.mode === "plan" ? "act" : "plan" })}
                title={
                  view.mode === "plan"
                    ? "Plan mode is on: the session only reads and plans, then asks you to approve the plan. Press to let it work."
                    : "Plan first: the session only reads and writes a plan for you to approve before it changes anything."
                }
                className={composerChipClass(view.mode === "plan")}
              >
                <ListChecks size={14} aria-hidden className="shrink-0" />
                <span>{view.mode === "plan" ? "Planning" : "Plan first"}</span>
              </button>
              {/* Review (v1.374.0): this group may WRAP, right-aligned, and
                  never grows past the row. With the meter added, a model that
                  offers reasoning levels pushed Send out of the box at 390 px
                  ("Summarizing earlier turns…" / "Earlier turns summarized"
                  by ~60 px, the percent and Compact by ~4-15 px — measured in
                  a real browser); a row that fits is unchanged. */}
              <div
                data-testid="harness-box-actions"
                className="ml-auto flex min-w-0 max-w-full flex-wrap items-center justify-end gap-1"
              >
                {/* v1.374.0: how full the conversation is for this model
                    (the daemon's reading of the newest reply), and Compact
                    past the daemon's threshold. */}
                <ContextMeter
                  reading={reading?.context ?? null}
                  offer={offerCompact}
                  summarized={summarized}
                  busy={compacting}
                  onCompact={() => void compact()}
                  onViewSummary={() => setSummaryOpen(true)}
                />
                {levels.length > 0 ? (
                  <ReasoningChip
                    levels={levels}
                    value={view.setup.reasoning}
                    def={reasoningDef}
                    onChange={(level) => void patchSetup({ reasoning: level })}
                  />
                ) : null}
                <ModelMenu
                  open={modelMenuOpen}
                  onOpenChange={setModelMenuOpen}
                  choice={choice}
                  models={catalog.models}
                  trigger={trigger}
                  onPick={(v) => {
                    const { provider: p, model: mo } = splitChoice(v);
                    void patchSetup({ provider: p ?? "", model: mo ?? "" });
                  }}
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
          undoFor={undoFor}
          onUndo={onUndo}
          onSessionChanged={() => void load()}
          onSessionGone={onGone}
        />
      </aside>
    </div>
  );
}
