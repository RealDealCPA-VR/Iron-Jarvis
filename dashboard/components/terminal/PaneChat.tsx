"use client";

// PaneChat (v1.206.0) — the Build-chat room's ENGINE: one lean chat surface
// bound to a working directory, mounted inside a Build/terminals pane.
//
// This deliberately does NOT fork the 7k-line chat page. It reuses the same
// wire contracts (see paneChatCore.ts for the copied contracts) and the same
// exported honesty components (TurnReceipt, DoorsStrip), and keeps exactly one
// thread per pane:
//
//  - thread id persists in localStorage `ij.pane.thread.<paneId>`; on mount
//    the thread is loaded (GET /chat/threads/{id}) if it exists;
//  - every completed turn autosaves the full bubble array through ONE
//    serialized promise chain (the first save's "new" resolves to a real id
//    before the second save reads it — the chat page's saveChain contract);
//  - saves carry a `setup` snapshot {workspace_dir: cwd, provider, model} that
//    MERGES over the stored setup, never clobbers keys the pane doesn't own;
//  - a thread that FAILED to load blocks sending: a save would PUT this
//    pane's two bubbles over the stored transcript, and silently destroying a
//    conversation is worse than a disabled composer.
//
// HONESTY: stream errors render as errors (a streamed partial is kept, marked
// interrupted — never presented as a complete answer); the daemon-down state
// is the app's OfflineHint; the receipt and doors under each reply are the
// server's own disclosure, rendered by the shared components verbatim.

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ClipboardEvent as ReactClipboardEvent,
  type DragEvent as ReactDragEvent,
} from "react";
import {
  ArrowUp,
  ChevronDown,
  CircleAlert,
  FileText,
  FolderKanban,
  Loader2,
  Paperclip,
  Play,
  Undo2,
  X,
} from "lucide-react";
import { get, post, put, ApiError } from "@/lib/api";
import { useDaemon } from "@/lib/daemon";
import { useChatStream, StreamError } from "@/lib/useChatStream";
import { TurnClock } from "@/components/chat/TurnClock";
import { ErrorNote, OfflineHint } from "@/components/ui";
// v1.329.0: the ONE markdown renderer (CLAUDE.md, v1.230.0) — remote images
// wait for a press (RemoteMediaGate, v1.322.0), a ```chart fence draws a
// chart, a draft fence is the draft card. The pane used to run its own
// ReactMarkdown, which loaded a model-written image URL with no press.
import { Markdown, MemoMarkdown } from "@/components/Markdown";
import { composerChipClass } from "@/lib/composerChips";
import { PaneAsk, onScreen } from "@/components/terminal/PaneAsk";
import {
  PANE_COMPOSER_BOX,
  PANE_COMPOSER_CARD,
  PANE_COMPOSER_NARROW_PX,
  PANE_GHOST_BUTTON,
  PANE_PERMISSION_COMPACT,
  PANE_QUIET_ROW,
  PANE_REPLY_PROSE,
  PANE_USER_BUBBLE,
  paneSendClass,
} from "@/components/terminal/paneChatLook";
import { TurnReceipt } from "@/components/chat/TurnReceipt";
import { DoorsStrip } from "@/components/chat/DoorsStrip";
// v1.329.0 (calm chat wave 5): the chat page's own work line, changes line
// and permission chip, fed from the pane's messages by a small adapter
// (paneWork.ts), never forked.
import { LiveToolRows, WorkLine } from "@/components/chat/WorkLine";
import { ReplyChanges } from "@/components/chat/ReplyChanges";
import type { ChangeUndoState } from "@/components/chat/ChangedFiles";
import { PermissionChip } from "@/components/chat/PermissionChip";
import { asPermissionMode, type PermissionMode } from "@/lib/permissionLevels";
import {
  paneReplyWork,
  paneTurnWindow,
  paneWorkInput,
  type PaneMessage,
} from "@/components/terminal/paneWork";
import { usePaneNarrow } from "@/components/terminal/usePaneNarrow";
import {
  joinUndoByPath,
  normalizeFsPath,
  parentDir,
  type UndoRowLike,
} from "@/components/chat/ArtifactsRail";
import {
  buildTurnBody,
  engineLabel,
  engineOptions,
  mergeSetup,
  paneBasename,
  paneThreadKey,
  paneTitle,
  pathIsUnder,
  projectForCwd,
  readAsBase64,
  runnableBlocks,
  unionTools,
  PANE_MAX_ATTACHMENTS,
  PANE_MAX_FILE_BYTES,
  type PaneMsg,
  type PaneProjectOption,
  type PaneThreadSetup,
} from "@/components/terminal/paneChatCore";
import {
  CHAT_TAIL_CHARS,
  TAIL_REPORT_MS,
  textTail,
  type PaneChatStatus,
} from "@/components/terminal/paneStatusCore";

export interface PaneChatProps {
  /** Stable pane identity — keys the pane's thread in localStorage. */
  paneId: string;
  /** ABSOLUTE working directory this chat is bound to (workspace_dir). */
  cwd: string;
  /** Type `cmd` into this pane's REAL terminal (the page flips the pane to
   *  terminal view and writes cmd+Enter into the PTY). Returns whether the
   *  write landed — false renders an honest "terminal not connected" note.
   *  ABSENT = no terminal behind this chat: no Run buttons render at all. */
  onRunCommand?: (cmd: string) => boolean;
  /** v1.212.0: report this chat's live status whenever it CHANGES — the page
   *  keeps this component mounted-but-hidden behind the terminal view, so a
   *  streaming turn or (worse) an ApprovalCard the daemon holds for up to
   *  180s is invisible without it. The page paints toggle-button badges and
   *  (v1.213.0) the live peek strip from these reports. Transitions of
   *  streaming/approval/tool report immediately; the every-token textTail is
   *  paced to one report per TAIL_REPORT_MS. */
  onStatus?: (s: PaneChatStatus) => void;
}

/** GET /chat/threads/{id} — the slice this pane reads. */
interface PaneThreadDetail {
  id: string;
  title?: string;
  messages?: PaneMessage[];
  setup?: PaneThreadSetup | null;
}

/** One uploaded, ready-to-ride attachment chip. */
interface PaneAttachment {
  name: string;
  path: string;
}

/**
 * The undo confirm, in the pane's HONEST wording (BC2 reviewer, defect 3).
 * The join is newest-row-per-path over the shared session-"chat" journal —
 * the same journal the big chat page and every other pane write to — so the
 * write being reverted may be MORE RECENT than the message whose card was
 * clicked, and the since-changed hash guard cannot catch a same-content
 * cross-thread write. The confirm says so instead of implying "this
 * message's write". The file_delete case keeps the rail's created→removed
 * honesty: confirming a "restore" there would confirm the user into a
 * deletion.
 */
function paneUndoPrompt(kind: string | undefined, base: string): string {
  return kind === "file_delete" || kind === "files_delete"
    ? `Undo the newest write? ${base} was created by a chat write and will be ` +
        `removed — that write may be more recent than this message (panes and ` +
        `Chat share one journal).`
    : `This restores ${base} to its content from before the NEWEST write to ` +
        `it — which may be more recent than this message (panes and Chat ` +
        `share one journal). Continue?`;
}

/**
 * The detectable HALF of "is the newest journal row newer than this
 * message?": a LATER assistant message in THIS thread listing the same path
 * proves it (that later turn wrote the file after this one). The
 * CROSS-SURFACE half is NOT detectable with the current row shape — rows
 * carry created_at but no thread/message attribution, and wire messages
 * carry no timestamps to compare against — which is exactly why the button
 * says "Undo newest write" rather than pretending to know.
 */
function docReappearsLater(
  messages: readonly PaneMsg[],
  index: number,
  normPath: string,
): boolean {
  for (let j = index + 1; j < messages.length; j += 1) {
    const m = messages[j];
    if (m.role !== "assistant") continue;
    if (m.documents?.some((d) => normalizeFsPath(d) === normPath)) return true;
  }
  return false;
}

function storedThreadId(paneId: string): string | null {
  try {
    return window.localStorage.getItem(paneThreadKey(paneId));
  } catch {
    return null;
  }
}

/** A reply: prose with no box, through the app's ONE markdown renderer (the
 *  chat page's). A settled reply is memoized on its text; the live one is
 *  re-parsed as it grows. Tables read as part of the text (PANE_REPLY_PROSE). */
function PaneMarkdown({ text, live = false }: { text: string; live?: boolean }) {
  return (
    <div data-testid="pane-reply" className={PANE_REPLY_PROSE}>
      {live ? <Markdown content={text} /> : <MemoMarkdown content={text} />}
    </div>
  );
}

export function PaneChat({ paneId, cwd, onRunCommand, onStatus }: PaneChatProps) {
  const daemon = useDaemon();
  const stream = useChatStream();

  const [messages, setMessages] = useState<PaneMessage[]>([]);
  const messagesRef = useRef<PaneMessage[]>(messages);
  messagesRef.current = messages;
  const [loading, setLoading] = useState(true);
  // A stored thread that could not be LOADED (non-404): sending is blocked —
  // an autosave would PUT two bubbles over the stored transcript.
  const [loadError, setLoadError] = useState<string | null>(null);
  const [threadId, setThreadId] = useState<string | null>(null);
  // AUTOSAVE FAILED (v1.226.0): the chip by the composer; `msgs` is the exact
  // array whose save failed, so Retry re-queues it through the CURRENT
  // queueSave (cwd/project-fresh). Cleared by a later successful save,
  // Dismiss, or a pane identity change.
  const [saveFailure, setSaveFailure] = useState<{
    detail: string;
    msgs: PaneMessage[];
  } | null>(null);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [attachments, setAttachments] = useState<PaneAttachment[]>([]);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  // "" = Default (omit provider — the daemon routes as configured).
  const [provider, setProvider] = useState("");
  const providerRef = useRef(provider);
  providerRef.current = provider;
  // The thread's pinned MODEL (BC1 D5): restored from setup, rides every turn
  // beside the provider, cleared when the user switches provider. No picker —
  // the pane manages providers only; the pin comes from /chat.
  const modelRef = useRef("");
  // Did THIS pane's user touch the engine picker? While untouched, the
  // save-time setup refresh (BC1 D3) may adopt a provider/model changed from
  // /chat instead of resurrecting the mount-time value over it.
  const providerTouchedRef = useRef(false);
  // Tools granted via THIS pane's approval card ("Allow for this
  // conversation", BC1 D1). Only the pane's own grants — never the mount-time
  // stored set — so a save can union them into a FRESH base without
  // resurrecting a tool the user disarmed in /chat meanwhile.
  const paneGrantsRef = useRef<string[]>([]);
  // v1.329.0: the conversation's permission level (the chat page's three
  // postures, lib/permissionLevels). It starts as the thread's stored
  // posture and rides every turn's body (the stream lane gates on it for a
  // pane turn exactly as for a /chat turn). A pick HERE persists into the
  // thread's setup; while untouched, the save-time refresh adopts a change
  // made from /chat (the engine picker's rule, BC1 D3).
  const [approvalMode, setApprovalMode] = useState<PermissionMode>("approve_for_me");
  const approvalRef = useRef<PermissionMode>("approve_for_me");
  const approvalTouchedRef = useRef(false);
  // Bumped by the Retry affordance on a failed thread load.
  const [loadNonce, setLoadNonce] = useState(0);
  const [project, setProject] = useState<PaneProjectOption | null>(null);

  // ---- changed-file cards + undo-where-you-look (BC2) --------------------
  // Live undo candidates from GET /undo?session_id=chat — pane turns run
  // through /chat/stream, whose file writes all land as session id "chat",
  // so the pane reads the SAME journal lane the big chat page does. Failure
  // is a quiet degrade: no undo affordance, nothing broken.
  const [undoRows, setUndoRows] = useState<UndoRowLike[]>([]);
  // Actions undone FROM THIS PANE — keeps the button disabled after success
  // even after the refetch drops the row from the live candidate list.
  const [undoneActions, setUndoneActions] = useState<Set<string>>(new Set());
  // One undo in flight at a time (keyed by normalized path).
  const [undoBusyPath, setUndoBusyPath] = useState<string | null>(null);
  // Per-file result notes (open failure, undo success, the guard's refusal),
  // keyed by normalized path — a blocked undo must say why, where clicked.
  const [fileNotes, setFileNotes] = useState<
    Record<string, { ok: boolean; text: string }>
  >({});
  // Per-run-button notes ("terminal not connected"), keyed "<msg>:<block>".
  const [runNotes, setRunNotes] = useState<Record<string, string>>({});

  // The setup snapshot the open thread STORED — carried forward on every save
  // so the pane never clobbers keys it does not manage (see mergeSetup).
  const baseSetupRef = useRef<PaneThreadSetup | null>(null);
  // AUTOSAVE machinery (the chat page's contract): one serialized PUT chain;
  // the target box holds the id so "new" → real-id happens exactly once.
  const saveChainRef = useRef<Promise<void>>(Promise.resolve());
  const saveTargetRef = useRef<{ id: string | null }>({ id: null });
  const endRef = useRef<HTMLDivElement | null>(null);
  // The pane chat's root and its box: an approval takes the composer's place
  // and gives the caret back when it is answered (PaneAsk).
  const rootRef = useRef<HTMLDivElement | null>(null);
  const boxRef = useRef<HTMLTextAreaElement | null>(null);
  // The composer card: in a narrow pane the permission chip shows only its
  // shield (the pane's width, not the window's, decides; v1.329.0).
  const composerRef = useRef<HTMLDivElement | null>(null);
  const composerNarrow = usePaneNarrow(composerRef, PANE_COMPOSER_NARROW_PX);

  // ------------------------------------------------------------- thread load
  useEffect(() => {
    let cancelled = false;
    // Fresh room per pane identity — the old pane's saves keep writing to the
    // old target box; this pane gets its own box and chain.
    setMessages([]);
    setThreadId(null);
    setLoadError(null);
    setSaveFailure(null);
    setError(null);
    setAttachments([]);
    setInput("");
    setProvider("");
    providerRef.current = "";
    modelRef.current = "";
    providerTouchedRef.current = false;
    paneGrantsRef.current = [];
    setApprovalMode("approve_for_me");
    approvalRef.current = "approve_for_me";
    approvalTouchedRef.current = false;
    baseSetupRef.current = null;
    saveChainRef.current = Promise.resolve();
    setUndoRows([]);
    setUndoneActions(new Set());
    setUndoBusyPath(null);
    setFileNotes({});
    setRunNotes({});
    const stored = storedThreadId(paneId);
    saveTargetRef.current = { id: stored };
    if (!stored) {
      setLoading(false);
      return;
    }
    setLoading(true);
    get<PaneThreadDetail>(`/chat/threads/${encodeURIComponent(stored)}`)
      .then((t) => {
        if (cancelled) return;
        setMessages(Array.isArray(t.messages) ? t.messages : []);
        setThreadId(t.id);
        baseSetupRef.current = t.setup ?? null;
        const p = t.setup?.provider;
        if (typeof p === "string" && p) {
          setProvider(p);
          providerRef.current = p;
        }
        // The pinned model rides with its provider (BC1 D5).
        const mo = t.setup?.model;
        if (typeof mo === "string") modelRef.current = mo;
        // The conversation's permission level (v1.329.0).
        const level = asPermissionMode(t.setup?.approval_mode);
        approvalRef.current = level;
        setApprovalMode(level);
      })
      .catch((e) => {
        if (cancelled) return;
        if (e instanceof ApiError && e.status === 404) {
          // The thread is gone (deleted from the chat page) — start fresh.
          try {
            window.localStorage.removeItem(paneThreadKey(paneId));
          } catch {
            /* ignore */
          }
          saveTargetRef.current = { id: null };
        } else {
          setLoadError(
            e instanceof ApiError && e.status === 0
              ? "Daemon offline — this pane's conversation could not be loaded."
              : `Couldn't load this pane's conversation: ${
                  e instanceof Error ? e.message : String(e)
                }`,
          );
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [paneId, loadNonce]);

  // ------------------------------------------------------ project grounding
  useEffect(() => {
    let cancelled = false;
    get<{ projects: PaneProjectOption[] }>("/projects")
      .then((d) => {
        if (!cancelled) setProject(projectForCwd(cwd, d.projects ?? []));
      })
      .catch(() => {
        // No list, no chip — folder grounding via workspace_dir still stands.
        if (!cancelled) setProject(null);
      });
    return () => {
      cancelled = true;
    };
  }, [cwd]);

  // v1.281.0: MAKE THIS FOLDER A PROJECT, from the pane. The chip above says
  // which project grounds this pane; when none does, the folder is just a
  // folder — the daemon's assist, this chat and every agent handed work here
  // know nothing about it. One press creates a project rooted at the pane's
  // folder (the same POST the chat page's "Make this folder a project" makes)
  // and the chip appears; the next turn carries `project_id`, and the assist
  // bar finds the project by path on its own.
  const [makingProject, setMakingProject] = useState(false);
  async function makeProject(): Promise<void> {
    if (!cwd || project || makingProject) return;
    setMakingProject(true);
    setError(null);
    try {
      const made = await post<PaneProjectOption>("/projects", { name: folder, root: cwd });
      if (made && typeof made.id === "string") {
        setProject({ id: made.id, name: made.name || folder, root: made.root ?? cwd });
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setMakingProject(false);
    }
  }

  // ------------------------------------------------ file cards: open + undo
  const refreshUndoRows = useCallback(async () => {
    try {
      const res = await get<{ actions: UndoRowLike[] }>(
        "/undo?session_id=chat",
      );
      setUndoRows(res.actions ?? []);
    } catch {
      /* offline / older daemon — no undo affordances, nothing broken */
    }
  }, []);

  // Fetch when any message carries documents (thread load or a file-writing
  // turn completing) — exactly "a new journal row may exist".
  useEffect(() => {
    if (messages.some((m) => m.documents?.length)) void refreshUndoRows();
  }, [messages, refreshUndoRows]);

  // Newest journal row per absolute path (GET /undo is newest-first and
  // joinUndoByPath keeps the first) — the shared join, not a reimplementation.
  const undoByPath = useMemo(() => joinUndoByPath(undoRows), [undoRows]);

  function setFileNote(normPath: string, note: { ok: boolean; text: string } | null) {
    setFileNotes((prev) => {
      const next = { ...prev };
      if (note) next[normPath] = note;
      else delete next[normPath];
      return next;
    });
  }

  /** "Open": POST /documents/open — the OS-associated app, the ArtifactsRail's
   *  own Open mechanism. Chosen over DocPreview deliberately: the preview is
   *  a rail-sized surface (diff machinery, width management) and this pane is
   *  a narrow column inside a terminal pane; launching the real app is the
   *  lightest honest open. A failure lands on the card, verbatim. */
  async function openDoc(path: string) {
    const norm = normalizeFsPath(path);
    setFileNote(norm, null);
    try {
      await post<{ ok: boolean; app?: string }>("/documents/open", { path });
    } catch (e) {
      setFileNote(norm, {
        ok: false,
        text: e instanceof Error ? e.message : String(e),
      });
    }
  }

  /** "Undo newest write": explicit confirm (the app's window.confirm
   *  convention, in the pane's honest wording — see paneUndoPrompt), POST
   *  /undo/{action_id}. The daemon's since-changed hash guard is the safety —
   *  its refusal (409 detail) renders VERBATIM on the card; success disables
   *  the button and says so. */
  async function undoWrite(actionId: string, path: string) {
    const norm = normalizeFsPath(path);
    try {
      await undoNewestWrite(actionId, path);
    } catch (e) {
      // The guard's own words — a blocked undo must say why.
      setFileNote(norm, {
        ok: false,
        text: e instanceof Error ? e.message : String(e),
      });
    }
  }

  /** The ONE undo both places use (the file card above, and the "files
   *  changed" list, v1.329.0): the pane's honest confirm, POST /undo/{id},
   *  the card's note, the refetch. THROWS on a refusal, so each caller shows
   *  the guard's words where the user pressed. A declined confirm (or an undo
   *  already running) does nothing. */
  async function undoNewestWrite(actionId: string, path: string) {
    const norm = normalizeFsPath(path);
    if (undoBusyPath) return;
    const row = undoByPath.get(norm);
    if (!window.confirm(paneUndoPrompt(row?.kind, paneBasename(path)))) return;
    setUndoBusyPath(norm);
    setFileNote(norm, null);
    try {
      await post(`/undo/${encodeURIComponent(actionId)}`, {});
      setUndoneActions((prev) => new Set(prev).add(actionId));
      setFileNote(norm, {
        ok: true,
        text: "undone — restored to before the newest write",
      });
      void refreshUndoRows();
    } finally {
      setUndoBusyPath(null);
    }
  }

  /** The changes list's journal match for a file: the same newest-row join
   *  the file card reads, so the two never disagree about what Undo does. */
  function undoStateFor(path: string): ChangeUndoState | null {
    const row = undoByPath.get(normalizeFsPath(path));
    if (!row) return null;
    if (undoneActions.has(row.action_id))
      return { actionId: row.action_id, undoable: false, reason: "already undone" };
    return {
      actionId: row.action_id,
      undoable: row.undoable !== false,
      ...(row.undoable === false ? { reason: "this action has no safe inverse" } : {}),
    };
  }

  /** "Run in terminal" (BC2): hand the fence's code VERBATIM to the page's
   *  PTY writer. A false return is the page saying the write did not land —
   *  render the honest note instead of pretending the command ran. */
  function runBlock(key: string, code: string) {
    if (!onRunCommand) return;
    const landed = onRunCommand(code);
    setRunNotes((prev) => {
      const next = { ...prev };
      if (landed) delete next[key];
      else next[key] = "terminal not connected";
      return next;
    });
  }

  // ------------------------------------------------------------------ saving
  /** Queue ONE autosave of the full bubble array (turn completion, failed
   *  turn, engine change). Serialized; the id is read INSIDE the chain step so
   *  the first save's "new"→real-id lands before the second save runs. */
  const queueSave = useCallback(
    (msgs: PaneMessage[]) => {
      if (msgs.length === 0) return;
      const target = saveTargetRef.current;
      saveChainRef.current = saveChainRef.current.then(async () => {
        try {
          const creating = target.id === null;
          // BC1 D3 — refresh the base BEFORE merging: the mount-time setup
          // goes stale the moment the user touches this thread in /chat (a
          // grant, a skill, a posture change), and merging onto the stale
          // copy would resurrect the old values over the foreign change.
          // Cost: one extra GET per save — a save is a completed turn or an
          // engine pick, both human-paced, and silently clobbering consent
          // state is the expensive alternative. On a failed refresh the last
          // known base stands (best-effort autosave, same as the PUT).
          if (!creating && target.id) {
            try {
              const t = await get<PaneThreadDetail>(
                `/chat/threads/${encodeURIComponent(target.id)}`,
              );
              baseSetupRef.current = t.setup ?? null;
              if (!providerTouchedRef.current) {
                // Nobody picked an engine HERE — adopt the thread's current
                // pick rather than resurrecting the mount-time one.
                providerRef.current = t.setup?.provider ?? "";
                modelRef.current = t.setup?.model ?? "";
                setProvider(providerRef.current);
              }
              if (!approvalTouchedRef.current) {
                // Same rule for the permission level (v1.329.0).
                approvalRef.current = asPermissionMode(t.setup?.approval_mode);
                setApprovalMode(approvalRef.current);
              }
            } catch {
              /* refresh is best-effort — the save still runs */
            }
          }
          const setup = mergeSetup(
            baseSetupRef.current,
            cwd,
            providerRef.current,
          );
          // This pane's own approval-card grants join the armed set — the
          // persistence half of "Allow for this conversation".
          if (paneGrantsRef.current.length) {
            setup.tools = unionTools(setup.tools, paneGrantsRef.current);
          }
          // A level picked HERE is this conversation's posture from now on.
          if (approvalTouchedRef.current) setup.approval_mode = approvalRef.current;
          const body: {
            messages: PaneMessage[];
            setup: PaneThreadSetup;
            title?: string;
            project_id?: string;
          } = {
            messages: msgs, // verbatim — the wire stores bubbles as-is
            setup,
            // Title only on CREATE: a later user rename must survive saves.
            ...(creating ? { title: paneTitle(cwd) } : {}),
            // Tag into the project when detected; OMITTED otherwise (an
            // explicit null would deliberately untag — not this pane's call).
            ...(project ? { project_id: project.id } : {}),
          };
          const res = await put<{ id: string }>(
            `/chat/threads/${target.id ?? "new"}`,
            body,
          );
          target.id = res.id;
          try {
            window.localStorage.setItem(paneThreadKey(paneId), res.id);
          } catch {
            /* ignore */
          }
          if (saveTargetRef.current === target) {
            setThreadId(res.id);
            setSaveFailure(null); // on disk again — retire the chip
          }
        } catch (e) {
          // Best-effort for the CONVERSATION (the bubbles stay), never silent
          // (v1.226.0): a swallowed failure was data loss nobody saw.
          if (e instanceof ApiError && e.status === 404) {
            // The thread is gone (deleted from the chat page) — the same
            // reset the load path makes: the next save re-creates it.
            target.id = null;
            try {
              window.localStorage.removeItem(paneThreadKey(paneId));
            } catch {
              /* ignore */
            }
            if (saveTargetRef.current === target) setThreadId(null);
          } else if (
            !(e instanceof ApiError && e.status === 0) && // offline: the hint covers it
            saveTargetRef.current === target // never a Retry into another box
          ) {
            setSaveFailure({
              detail: e instanceof Error ? e.message : String(e),
              msgs,
            });
          }
        }
      });
    },
    [cwd, paneId, project],
  );

  // ------------------------------------------------------------- attachments
  // Latest attachments, readable from send()/drop handlers whose closures may
  // be stale (the chat page's attachmentsRef pattern).
  const attachmentsRef = useRef<PaneAttachment[]>(attachments);
  attachmentsRef.current = attachments;

  const addFiles = useCallback(async (files: File[]) => {
    setError(null);
    const room = PANE_MAX_ATTACHMENTS - attachmentsRef.current.length;
    if (room <= 0) {
      setError(`Up to ${PANE_MAX_ATTACHMENTS} files per message.`);
      return;
    }
    const accepted: File[] = [];
    for (const f of files) {
      if (f.size > PANE_MAX_FILE_BYTES) {
        setError(`${f.name} is too large (max 20 MB).`);
        continue;
      }
      if (accepted.length >= room) {
        setError(`Up to ${PANE_MAX_ATTACHMENTS} files per message.`);
        break;
      }
      accepted.push(f);
    }
    if (accepted.length === 0) return;
    setUploading(true);
    try {
      for (const f of accepted) {
        const content_b64 = await readAsBase64(f);
        const res = await post<{ path: string; name: string }>(
          "/documents/upload",
          { filename: f.name, content_b64 },
        );
        setAttachments((prev) =>
          prev.length >= PANE_MAX_ATTACHMENTS
            ? prev
            : [...prev, { name: res.name, path: res.path }],
        );
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setUploading(false);
    }
  }, []);

  function onDrop(e: ReactDragEvent<HTMLDivElement>) {
    if (!Array.from(e.dataTransfer?.types ?? []).includes("Files")) return;
    e.preventDefault();
    setDragging(false);
    const files = e.dataTransfer?.files;
    if (files && files.length) void addFiles(Array.from(files));
  }

  function onPaste(e: ReactClipboardEvent<HTMLDivElement>) {
    const files = Array.from(e.clipboardData?.files ?? []);
    const text = e.clipboardData?.getData("text/plain") ?? "";
    // TEXT WINS (the Excel rule, v1.194.0): claim the paste only when the
    // clipboard carries files and NO text flavour — a spreadsheet copy
    // exposes a bitmap alongside its text and must stay a text paste.
    if (files.length === 0 || text) return;
    e.preventDefault();
    void addFiles(files);
  }

  // ---------------------------------------------------------------- sending
  const canCompose =
    !loading && !loadError && (daemon.online || daemon.checking);
  const busy = sending || stream.streaming;

  // ---- status reporting (v1.212.0, peek fields v1.213.0) ------------------
  // Streaming is reported as `busy` (sending || stream.streaming) — the same
  // derivation the composer's spinner uses — so the pre-stream POST window
  // counts as working and the badge never flickers off between the POST
  // landing and the first streamed token. Read `onStatus` through a ref so
  // the page may hand a fresh closure each render without re-firing the
  // effect.
  //
  // The v1.213.0 peek fields are SERVER truth only: `tool` is the last
  // still-RUNNING card of the live stream, `textTail` the tail of the text
  // that actually streamed — both "" while idle, so a finished turn's stale
  // card list can never read as current activity. textTail changes EVERY
  // TOKEN, and one page re-render per token across the whole Build canvas is
  // a render storm — so reports are split in two: transitions (busy /
  // approval / tool) fire immediately, while tail-only changes are paced
  // through a ref-timestamped scheduler to one report per TAIL_REPORT_MS
  // (every report carries the freshest tail; a scheduled trailing report
  // flushes the last tokens of a burst).
  const onStatusRef = useRef(onStatus);
  onStatusRef.current = onStatus;
  const approvalPending = !!stream.approval;
  const runningTool = busy
    ? ([...stream.tools].reverse().find((t) => t.status === "running")?.name ?? "")
    : "";
  const tail = busy ? textTail(stream.text, CHAT_TAIL_CHARS) : "";
  const latestStatusRef = useRef<PaneChatStatus>({
    streaming: busy,
    approval: approvalPending,
    tool: runningTool,
    textTail: tail,
  });
  latestStatusRef.current = {
    streaming: busy,
    approval: approvalPending,
    tool: runningTool,
    textTail: tail,
  };
  const lastTailReportRef = useRef(0);
  const tailTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reportStatusNow = useCallback(() => {
    if (tailTimerRef.current !== null) {
      clearTimeout(tailTimerRef.current);
      tailTimerRef.current = null;
    }
    // Every report counts as a tail report too — it carries the freshest
    // tail, so the pacer's clock restarts here as well.
    lastTailReportRef.current = Date.now();
    onStatusRef.current?.(latestStatusRef.current);
  }, []);
  // Transitions report IMMEDIATELY — an approval pause or a tool starting
  // must never wait out the tail pacer.
  useEffect(() => {
    reportStatusNow();
  }, [busy, approvalPending, runningTool, reportStatusNow]);
  // The tail pacer: report now when the window already elapsed, else keep
  // exactly ONE trailing report scheduled (it reads latestStatusRef at fire
  // time, so the burst's final tokens always land).
  useEffect(() => {
    if (!busy) return; // idle tail is "" and rode the transition report
    const wait = TAIL_REPORT_MS - (Date.now() - lastTailReportRef.current);
    if (wait <= 0) {
      reportStatusNow();
      return;
    }
    if (tailTimerRef.current !== null) return;
    tailTimerRef.current = setTimeout(() => {
      tailTimerRef.current = null;
      reportStatusNow();
    }, wait);
  }, [tail, busy, reportStatusNow]);
  // Hygiene: an unmounted chat is not working on anything — never leave a
  // stale "working"/"approval" badge (or peek line) behind. (The page never
  // unmounts an opened PaneChat (v1.206.0); this covers pane close and
  // future callers.) The pending trailing report dies with the component.
  useEffect(
    () => () => {
      if (tailTimerRef.current !== null) clearTimeout(tailTimerRef.current);
      onStatusRef.current?.({
        streaming: false,
        approval: false,
        tool: "",
        textTail: "",
      });
    },
    [],
  );

  async function send() {
    const text = input.trim();
    if (!text || busy || !canCompose || uploading) return;
    const atts = attachmentsRef.current;
    setError(null);
    const userMsg: PaneMessage = {
      role: "user",
      content: text,
      ...(atts.length
        ? {
            attachmentNames: atts.map((a) => a.name),
            attachmentPaths: atts.map((a) => a.path),
          }
        : {}),
      // v1.329.0: when it was sent (the changes line's window falls back to
      // it for a reply with no timing).
      at: new Date().toISOString(),
    };
    const history = [...messagesRef.current, userMsg];
    setMessages(history);
    setInput("");
    setAttachments([]);
    setSending(true);
    try {
      const res = await stream.run(
        buildTurnBody({
          history,
          cwd,
          // WHICH pane asked (v1.236.0) — the daemon's ChatBody.pane_id. Without
          // this one line the field exists on both sides and nothing ever sends
          // it, which is the "green suite over an unreachable feature" shape.
          paneId,
          provider: providerRef.current,
          // The thread's pinned model rides with the provider (BC1 D5).
          model: modelRef.current,
          attachments: atts.map((a) => a.path),
          // Stored armed set + this pane's card grants — a granted tool must
          // actually ride later turns or the grant was a lie (BC1 D1).
          tools: unionTools(baseSetupRef.current?.tools, paneGrantsRef.current),
          projectId: project?.id ?? null,
          // The thread's consent posture — an always_ask thread must not run
          // pane turns at the default, nor a yolo one re-ask (BC1 D4).
          // v1.329.0: the permission chip's level (the stored posture until
          // the user picks one here).
          approvalMode: approvalRef.current,
        }),
      );
      const reply: PaneMessage = {
        role: "assistant",
        content: res.reply,
        ...(res.route ? { route: res.route } : {}),
        ...(res.adapted ? { adapted: res.adapted } : {}),
        ...(res.tools_used?.length ? { toolsUsed: res.tools_used } : {}),
        ...(res.deniedTools?.length ? { deniedTools: res.deniedTools } : {}),
        // Trust (v1.298.0): the chat page's rule — only a LOW posture lands.
        ...(res.trust === "low"
          ? {
              trust: res.trust,
              ...(res.trustReason ? { trustReason: res.trustReason } : {}),
              ...(res.trustNote ? { trustNote: res.trustNote } : {}),
            }
          : {}),
        // Usage (v1.300.0): whitelisted by the done-frame decode.
        ...(res.usage ? { usage: res.usage } : {}),
        ...(res.documents?.length ? { documents: res.documents } : {}),
        ...(res.doors?.length ? { doors: res.doors } : {}),
        // v1.329.0 (kept LAST, the receipt rule): the settle time and the
        // turn's steps, timing and thinking, for the work line and the
        // changes line.
        ...paneReplyWork(res, new Date().toISOString()),
      };
      const full = [...history, reply];
      setMessages(full);
      queueSave(full);
    } catch (e) {
      // HONEST failure: the error renders as an error; a streamed partial is
      // kept and marked interrupted (never presented as a complete answer);
      // the failed turn still saves so nothing is lost to a pane close.
      const partial = e instanceof StreamError ? e.partial : "";
      const full: PaneMessage[] = partial
        ? [...history, { role: "assistant", content: partial, interrupted: true }]
        : history;
      setMessages(full);
      queueSave(full);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSending(false);
    }
  }

  /** Engine pick: rides the next turn's body AND persists to the thread setup
   *  right away when a conversation already exists (arming then closing the
   *  pane must not lose the pick). A fresh pane's pick rides the first save.
   *  The stored MODEL pin survives only while the provider stays the same —
   *  a stale model id against a new provider is a routing error waiting to
   *  happen (mirrors mergeSetup's rule exactly). */
  function pickEngine(value: string) {
    providerTouchedRef.current = true;
    setProvider(value);
    providerRef.current = value;
    modelRef.current =
      value === (baseSetupRef.current?.provider ?? "")
        ? (baseSetupRef.current?.model ?? "")
        : "";
    if (messagesRef.current.length > 0 && !loadError) {
      queueSave(messagesRef.current);
    }
  }

  /** "Allow for this conversation" on the mid-turn approval card: remember
   *  the grant so LATER turns arm the tool (body.tools) and the next save
   *  persists it into setup.tools — the card's "stops asking here" promise.
   *  (The daemon already grants the REST OF THIS TURN server-side.) */
  function armFromApproval(tool: string) {
    if (!paneGrantsRef.current.includes(tool)) {
      paneGrantsRef.current = [...paneGrantsRef.current, tool];
    }
    // Persist right away on an existing conversation — granting then closing
    // the pane must not lose the grant.
    if (messagesRef.current.length > 0 && !loadError) {
      queueSave(messagesRef.current);
    }
  }

  /** The permission chip (v1.329.0): rides the next turn's body AND persists
   *  to the thread's setup right away when a conversation exists (picking a
   *  level then closing the pane must not lose it); a fresh pane's pick rides
   *  the first save. This pane's conversation only: the chat page's default
   *  for new chats is left as it is. */
  function pickPermission(mode: PermissionMode) {
    approvalTouchedRef.current = true;
    approvalRef.current = mode;
    setApprovalMode(mode);
    if (messagesRef.current.length > 0 && !loadError) {
      queueSave(messagesRef.current);
    }
  }

  // Keep the newest bubble in view (guarded — jsdom has no scrollIntoView).
  useEffect(() => {
    endRef.current?.scrollIntoView?.({ behavior: "smooth", block: "end" });
  }, [messages, stream.text]);

  // v1.329.0: the approval sits in the composer's place. When it is
  // answered, the composer comes back; give it the caret when the card took
  // the focus with it (focus fell to the page) and this pane is on screen.
  // A pane hidden behind its terminal never takes the focus.
  const hadAskRef = useRef(false);
  useEffect(() => {
    const had = hadAskRef.current;
    hadAskRef.current = approvalPending;
    if (!had || approvalPending) return;
    const ae = document.activeElement;
    const pageHasIt = !ae || ae === document.body || ae === document.documentElement;
    if (pageHasIt && onScreen(rootRef.current)) boxRef.current?.focus({ preventScroll: true });
  }, [approvalPending]);

  const engines = engineOptions(daemon.health?.providers);
  // A restored pick whose provider is currently offline still shows as ITSELF
  // (labelled), never silently swapped to Default — the no-auto-switch rule.
  const pickedUnavailable =
    provider !== "" && !engines.some((e) => e.id === provider);
  const folder = paneBasename(cwd) || cwd;
  const offline = !daemon.checking && !daemon.online;
  const sendReady = canCompose && !busy && !uploading && !!input.trim();
  const engineName =
    provider === ""
      ? "Default"
      : (engines.find((e) => e.id === provider)?.label ??
        `${engineLabel(provider)} (offline)`);

  return (
    <div
      ref={rootRef}
      data-testid="pane-chat"
      className={`relative flex h-full min-h-0 flex-col ${
        dragging ? "ring-1 ring-accent/50" : ""
      }`}
      onDragEnter={(e) => {
        if (Array.from(e.dataTransfer?.types ?? []).includes("Files")) {
          e.preventDefault();
          setDragging(true);
        }
      }}
      onDragOver={(e) => {
        if (Array.from(e.dataTransfer?.types ?? []).includes("Files"))
          e.preventDefault();
      }}
      onDragLeave={(e) => {
        const to = e.relatedTarget as Node | null;
        if (!to || !e.currentTarget.contains(to)) setDragging(false);
      }}
      onDrop={onDrop}
      onPaste={onPaste}
    >
      {/* ------------------------------------------------------- transcript */}
      <div
        data-testid="pane-chat-transcript"
        className="min-h-0 flex-1 space-y-4 overflow-y-auto px-3 py-3"
      >
        {offline ? <OfflineHint /> : null}
        {loadError ? (
          <div className="space-y-2">
            <ErrorNote>{loadError}</ErrorNote>
            <button
              type="button"
              onClick={() => setLoadNonce((n) => n + 1)}
              className={PANE_GHOST_BUTTON}
            >
              Retry loading
            </button>
          </div>
        ) : null}
        {loading ? (
          <div className="flex items-center gap-2 text-xs text-zinc-500">
            <Loader2 size={12} className="animate-spin" /> Loading conversation…
          </div>
        ) : null}
        {messages.map((m, i) =>
          m.role === "user" ? (
            <div key={i} className="flex justify-end">
              {/* Calm chat (v1.329.0): your message is a tinted bubble with
                  no border, the chat page's look at pane scale. */}
              <div data-testid="pane-user-bubble" className={PANE_USER_BUBBLE}>
                {m.content}
                {m.attachmentNames?.length ? (
                  <div className="mt-1.5 flex flex-wrap gap-1">
                    {m.attachmentNames.map((n) => (
                      <span
                        key={n}
                        className="inline-flex items-center gap-1 rounded-full bg-white/[0.06] px-2 py-0.5 text-[11px] text-zinc-400"
                      >
                        <Paperclip size={9} /> {n}
                      </span>
                    ))}
                  </div>
                ) : null}
              </div>
            </div>
          ) : (
            <div key={i} className="min-w-0">
              {/* v1.329.0: the work behind the answer, folded into the chat
                  page's ONE quiet line ("Worked for 3.2 s · read 2 files");
                  nothing for a reply with none. */}
              <WorkLine {...paneWorkInput(m)} />
              <PaneMarkdown text={m.content} />
              {m.interrupted ? (
                <div className="mt-1 flex items-center gap-1.5 text-[11px] text-tone-warn">
                  <CircleAlert size={11} /> interrupted — this answer is
                  incomplete
                </div>
              ) : null}
              {/* RUN-IN-TERMINAL (BC2): one action row per language-tagged
                  shell fence, rendered UNDER the markdown rather than inside
                  it — one renderer for the block text (the mocked-markdown
                  test idiom, and no fragile children-extraction from
                  react-markdown's tree). No onRunCommand prop = no terminal
                  behind this chat = no buttons at all. */}
              {onRunCommand
                ? runnableBlocks(m.content).map((b, bi) => {
                    const key = `${i}:${bi}`;
                    const first = b.code.split("\n")[0];
                    const more = b.code.includes("\n");
                    return (
                      <div key={key} className="mt-1.5">
                        <div
                          data-testid="pane-run-block"
                          className={`flex items-center gap-2 ${PANE_QUIET_ROW}`}
                        >
                          <code className="min-w-0 flex-1 truncate font-mono text-[11px] text-zinc-400">
                            {first}
                            {more ? " …" : ""}
                          </code>
                          <button
                            type="button"
                            onClick={() => runBlock(key, b.code)}
                            className={`${PANE_GHOST_BUTTON} text-accent-soft hover:bg-accent/10 hover:text-accent-soft`}
                          >
                            <Play size={10} /> Run in terminal
                          </button>
                        </div>
                        {runNotes[key] ? (
                          <div className="mt-1 flex items-center gap-1.5 text-[11px] text-tone-warn">
                            <CircleAlert size={11} /> {runNotes[key]}
                          </div>
                        ) : null}
                      </div>
                    );
                  })
                : null}
              {/* v1.329.0: WHAT THE REPLY CHANGED, the chat page's line
                  ("2 files changed +14 −3", each file opening its diff).
                  Only a reply whose documents list paths has a window; it is
                  asked once, when the reply is on screen. Undo is the pane's
                  own (the same confirm and journal row as the card below). */}
              <ReplyChanges
                turn={paneTurnWindow(messages, i)}
                undoFor={undoStateFor}
                onUndo={undoNewestWrite}
              />
              {/* CHANGED-FILE CARDS (BC2): the receipt's created/changed
                  paths as actionable rows — Open (OS app) + Undo (the real
                  journal). The receipt below stays the accountability record;
                  these are the actions where the user is looking. */}
              {m.documents?.length ? (
                <div className="mt-1.5 space-y-1">
                  {Array.from(new Set(m.documents)).map((p) => {
                    const norm = normalizeFsPath(p);
                    const row = undoByPath.get(norm);
                    const undone = row
                      ? undoneActions.has(row.action_id)
                      : false;
                    const outside = !pathIsUnder(p, cwd);
                    const note = fileNotes[norm];
                    // The detectable half of "newest row is newer than this
                    // message" — a later turn in THIS thread wrote the file
                    // again (cross-surface writes stay undetectable, see
                    // docReappearsLater).
                    const newerInThread =
                      !!row && docReappearsLater(messages, i, norm);
                    return (
                      <div
                        key={p}
                        data-testid="pane-file-card"
                        className={PANE_QUIET_ROW}
                      >
                        <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
                          <FileText
                            size={12}
                            className="shrink-0 text-accent-soft/80"
                          />
                          <span className="min-w-0 truncate text-xs text-zinc-200">
                            {paneBasename(p)}
                          </span>
                          <span
                            className="hidden min-w-0 truncate text-[11px] text-zinc-500 sm:inline"
                            title={p}
                          >
                            {parentDir(p)}
                          </span>
                          {outside ? (
                            // The receipt is truth — a path outside the
                            // pane's folder still renders, flagged.
                            <span className="shrink-0 rounded-full bg-tone-warn/[0.08] px-1.5 py-px text-[11px] text-tone-warn">
                              outside this folder
                            </span>
                          ) : null}
                          <div className="ml-auto flex shrink-0 items-center gap-1">
                            <button
                              type="button"
                              onClick={() => void openDoc(p)}
                              className={PANE_GHOST_BUTTON}
                            >
                              Open
                            </button>
                            {row ? (
                              <button
                                type="button"
                                disabled={
                                  undone ||
                                  undoBusyPath === norm ||
                                  row.undoable === false
                                }
                                title={
                                  row.undoable === false
                                    ? "this action has no safe inverse"
                                    : undefined
                                }
                                onClick={() =>
                                  void undoWrite(row.action_id, p)
                                }
                                className={`${PANE_GHOST_BUTTON} hover:bg-tone-danger/10 hover:text-tone-danger`}
                              >
                                <Undo2 size={10} />
                                {undone ? "Undone" : "Undo newest write"}
                              </button>
                            ) : null}
                            {newerInThread ? (
                              <span className="shrink-0 text-[11px] text-tone-warn">
                                (newer than this message)
                              </span>
                            ) : null}
                          </div>
                        </div>
                        {note ? (
                          <div
                            className={`mt-1 text-[11px] ${
                              note.ok ? "text-tone-success" : "text-tone-danger"
                            }`}
                          >
                            {note.text}
                          </div>
                        ) : null}
                      </div>
                    );
                  })}
                </div>
              ) : null}
              {/* Server-truth receipt + doors — the shared components, verbatim. */}
              <TurnReceipt
                route={m.route}
                adapted={m.adapted}
                toolsUsed={m.toolsUsed}
                deniedTools={m.deniedTools}
                trust={m.trust}
                trustReason={m.trustReason}
                trustNote={m.trustNote}
                usage={m.usage}
                documents={m.documents}
              />
              <DoorsStrip doors={m.doors} />
            </div>
          ),
        )}
        {stream.streaming ? (
          <div className="min-w-0" data-testid="pane-chat-live">
            {/* v1.329.0: the chat page's live rows ("Reading harbor.xlsx",
                "Running excel_query"), one grey line each. */}
            {stream.tools.length > 0 ? (
              <div className="mb-2">
                <LiveToolRows cards={stream.tools} />
              </div>
            ) : null}
            {stream.text ? (
              <PaneMarkdown text={stream.text} live />
            ) : (
              <div className="flex items-center gap-2 text-xs text-zinc-500">
                <Loader2 size={12} className="animate-spin" /> Thinking…
                {/* v1.246.0: how long — same clock as the Chat page. */}
                <TurnClock since={stream.startedAt ?? null} />
              </div>
            )}
          </div>
        ) : null}
        {error ? <ErrorNote>{error}</ErrorNote> : null}
        <div ref={endRef} />
      </div>

      {/* --------------------------------------------------------- composer
          v1.329.0 (calm chat): ONE card holds the box, the Engine chip, the
          folder / project chip and a round send button. No border line above
          it. A mid-turn approval takes the card's place (below). */}
      <div className="flex max-h-[75%] min-h-0 shrink-0 flex-col px-3 pb-3 pt-1">
        {/* AUTOSAVE FAILED (v1.226.0): persistent + dismissible — what is on
            screen is NOT on disk until Retry succeeds. */}
        {saveFailure ? (
          <div
            role="status"
            className="mb-1.5 flex items-center gap-2 px-1 text-[11px] text-tone-warn"
          >
            <span className="min-w-0 flex-1">
              Couldn&apos;t save this conversation: {saveFailure.detail}
            </span>
            <button
              type="button"
              onClick={() => {
                const { msgs } = saveFailure;
                setSaveFailure(null);
                queueSave(msgs);
              }}
              className={PANE_GHOST_BUTTON}
            >
              Retry
            </button>
            <button
              type="button"
              aria-label="Dismiss save warning"
              onClick={() => setSaveFailure(null)}
              className="shrink-0 text-zinc-500 hover:text-zinc-200"
            >
              <X size={10} />
            </button>
          </div>
        ) : null}
        {/* MID-TURN APPROVAL (BC1 D1): the daemon paused this turn on an
            ask-tier tool — npm/git/docker asks are EXACTLY Build-pane
            language, and auto_tools arms them. Without this card the pause
            is invisible for up to 180s and the model answers around a
            refusal the user never saw. Same component, same POST, same
            grant store as the big page; the hook clears it on the
            approval_resolved frame (or when the stream ends).
            v1.329.0: drawn in the composer's place, like the chat page's
            dock. The composer stays mounted (hidden + inert), so the draft
            is exactly as it was when the question is answered. */}
        {stream.approval ? (
          <PaneAsk
            approval={stream.approval}
            onConversation={armFromApproval}
            paneRoot={rootRef}
          />
        ) : null}
        <div
          ref={composerRef}
          data-testid="pane-chat-composer"
          inert={approvalPending}
          aria-hidden={approvalPending || undefined}
          className={`${approvalPending ? "hidden" : "flex"} ${PANE_COMPOSER_CARD}`}
        >
          {attachments.length > 0 ? (
            <div className="flex flex-wrap gap-1.5 px-3 pt-2.5">
              {attachments.map((a, i) => (
                <span
                  key={a.path}
                  className="inline-flex items-center gap-1.5 rounded-full bg-white/[0.06] px-2.5 py-1 text-[11px] text-zinc-300"
                >
                  <Paperclip size={10} className="text-accent-soft" />
                  <span className="max-w-[160px] truncate">{a.name}</span>
                  <button
                    type="button"
                    aria-label={`Remove ${a.name}`}
                    className="text-zinc-500 hover:text-zinc-200"
                    onClick={() =>
                      setAttachments((prev) => prev.filter((_, j) => j !== i))
                    }
                  >
                    <X size={10} />
                  </button>
                </span>
              ))}
            </div>
          ) : null}
          {uploading ? (
            <div className="flex items-center gap-1.5 px-3.5 pt-2 text-[11px] text-zinc-500">
              <Loader2 size={10} className="animate-spin" /> Uploading…
            </div>
          ) : null}
          <textarea
            ref={boxRef}
            aria-label="Message"
            placeholder={`Build in ${folder}…`}
            value={input}
            disabled={!canCompose}
            rows={2}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.nativeEvent.isComposing || e.keyCode === 229) return;
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void send();
              }
            }}
            className={PANE_COMPOSER_BOX}
          />
          <div className="flex min-w-0 items-center gap-1 px-2 pb-2">
            {/* The Engine choice: a quiet chip, transparent at rest, that
                hugs its words. The native select stays (keyboard, screen
                reader, the same value the tests and the save path read),
                laid invisibly over the chip so a press opens it. */}
            <span
              data-testid="pane-chat-engine"
              className={`${composerChipClass(provider !== "")} relative min-w-0 max-w-[45%] pr-1.5 text-[12px] focus-within:ring-1 focus-within:ring-accent/50`}
            >
              <span className="min-w-0 truncate">{engineName}</span>
              <ChevronDown size={12} aria-hidden className="shrink-0 text-zinc-500" />
              <select
                aria-label="Engine"
                value={provider}
                onChange={(e) => pickEngine(e.target.value)}
                title={`Which model answers here: ${engineName}`}
                className="absolute inset-0 h-full w-full cursor-pointer appearance-none opacity-0"
              >
                <option value="">Default</option>
                {engines.map((o) => (
                  <option key={o.id} value={o.id}>
                    {o.label}
                  </option>
                ))}
                {pickedUnavailable ? (
                  <option value={provider}>{engineLabel(provider)} (offline)</option>
                ) : null}
              </select>
            </span>
            {/* v1.329.0: the chat page's ONE permission chip, for this pane's
                conversation. The level rides every pane turn (the stream lane
                gates a pane turn on it exactly as a /chat turn) and is saved
                with the thread. A narrow pane shows only the shield. */}
            <span
              data-testid="pane-chat-permission"
              data-compact={composerNarrow ? "true" : undefined}
              className={`inline-flex shrink-0 ${composerNarrow ? PANE_PERMISSION_COMPACT : ""}`}
            >
              <PermissionChip
                value={approvalMode}
                onChange={pickPermission}
                disabled={!canCompose}
                iconOnlyOnPhone
              />
            </span>
            <div className="flex min-w-0 flex-1 items-center gap-0.5 overflow-hidden">
              {project ? (
                <span
                  data-testid="pane-chat-project"
                  className="inline-flex h-[30px] min-w-0 items-center gap-1 rounded-lg px-2 text-[12px] text-accent-soft"
                  title={`Grounded in project ${project.name}`}
                >
                  <FolderKanban size={12} className="shrink-0" />
                  <span className="truncate">{project.name}</span>
                </span>
              ) : (
                <>
                  <span
                    className="min-w-0 shrink-[4] truncate px-1 text-[12px] text-zinc-500"
                    title={cwd}
                  >
                    {folder}
                  </span>
                  {cwd ? (
                    // v1.329.0: a narrow pane shows only the icon, so the
                    // folder name keeps its room beside the permission
                    // shield (it read "H…" at 390px). The name is still
                    // said to a screen reader and on hover.
                    <button
                      type="button"
                      data-testid="pane-chat-make-project"
                      data-compact={composerNarrow ? "true" : undefined}
                      aria-label={composerNarrow ? "Make this a project" : undefined}
                      onClick={() => void makeProject()}
                      disabled={makingProject}
                      title="Creates a project rooted in this folder, so this chat, the assist bar and any agent handed work here are grounded in it"
                      className={`inline-flex h-[30px] min-w-[30px] items-center gap-1.5 overflow-hidden rounded-lg px-2 text-[12px] text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-zinc-200 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 disabled:cursor-not-allowed disabled:opacity-40 ${composerNarrow ? "shrink-0 justify-center" : "shrink"}`}
                    >
                      <FolderKanban size={12} className="shrink-0" />
                      {composerNarrow ? null : (
                        <span data-testid="pane-chat-make-project-words" className="min-w-0 truncate">
                          {makingProject ? "Making…" : "Make this a project"}
                        </span>
                      )}
                    </button>
                  ) : null}
                </>
              )}
              {threadId ? (
                <span className="shrink-0 px-1 text-[11px] text-zinc-600">saved</span>
              ) : null}
            </div>
            <button
              type="button"
              aria-label="Send"
              title="Send (Enter)"
              disabled={!canCompose || busy || uploading || !input.trim()}
              onClick={() => void send()}
              className={paneSendClass(sendReady)}
            >
              {busy ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <ArrowUp size={15} strokeWidth={2.4} />
              )}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
