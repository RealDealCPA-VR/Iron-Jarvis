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
  CircleAlert,
  FileText,
  FolderKanban,
  Loader2,
  Paperclip,
  Play,
  Send,
  Undo2,
  Wrench,
  X,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { get, post, put, ApiError } from "@/lib/api";
import { useDaemon } from "@/lib/daemon";
import { useChatStream, StreamError, type ToolCard } from "@/lib/useChatStream";
import { ErrorNote, OfflineHint } from "@/components/ui";
import { ApprovalCard } from "@/components/chat/ApprovalCard";
import { TurnReceipt } from "@/components/chat/TurnReceipt";
import { DoorsStrip } from "@/components/chat/DoorsStrip";
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
}

/** GET /chat/threads/{id} — the slice this pane reads. */
interface PaneThreadDetail {
  id: string;
  title?: string;
  messages?: PaneMsg[];
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

/** One live tool row under the streaming bubble. */
function ToolRow({ card }: { card: ToolCard }) {
  return (
    <div
      data-testid="pane-tool-card"
      className="flex items-center gap-2 rounded-lg border border-white/[0.06] bg-white/[0.02] px-2.5 py-1.5 text-[11px] text-zinc-400"
    >
      {card.status === "running" ? (
        <Loader2 size={11} className="shrink-0 animate-spin text-accent-soft" />
      ) : (
        <Wrench
          size={11}
          className={`shrink-0 ${card.ok === false ? "text-rose-400" : "text-emerald-400/80"}`}
        />
      )}
      <span className="truncate font-mono">{card.name}</span>
      {card.status === "done" && card.ok === false ? (
        <span className="text-rose-300">failed</span>
      ) : null}
    </div>
  );
}

/** Assistant markdown, minimally styled for a narrow pane. */
function PaneMarkdown({ text }: { text: string }) {
  return (
    <div className="min-w-0 text-sm leading-relaxed text-zinc-200 [&_a]:text-accent-soft [&_a]:underline [&_code]:font-mono [&_code]:text-[12px] [&_h1]:mt-2 [&_h1]:text-base [&_h1]:font-semibold [&_h2]:mt-2 [&_h2]:text-sm [&_h2]:font-semibold [&_h3]:mt-2 [&_h3]:text-sm [&_h3]:font-semibold [&_li]:my-0.5 [&_ol]:list-decimal [&_ol]:pl-5 [&_p]:my-1.5 [&_pre]:my-2 [&_pre]:overflow-x-auto [&_pre]:rounded-lg [&_pre]:border [&_pre]:border-white/[0.06] [&_pre]:bg-black/40 [&_pre]:p-3 [&_table]:my-2 [&_table]:text-xs [&_td]:border [&_td]:border-white/10 [&_td]:px-2 [&_td]:py-1 [&_th]:border [&_th]:border-white/10 [&_th]:px-2 [&_th]:py-1 [&_ul]:list-disc [&_ul]:pl-5">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </div>
  );
}

export function PaneChat({ paneId, cwd, onRunCommand }: PaneChatProps) {
  const daemon = useDaemon();
  const stream = useChatStream();

  const [messages, setMessages] = useState<PaneMsg[]>([]);
  const messagesRef = useRef<PaneMsg[]>(messages);
  messagesRef.current = messages;
  const [loading, setLoading] = useState(true);
  // A stored thread that could not be LOADED (non-404): sending is blocked —
  // an autosave would PUT two bubbles over the stored transcript.
  const [loadError, setLoadError] = useState<string | null>(null);
  const [threadId, setThreadId] = useState<string | null>(null);
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

  // ------------------------------------------------------------- thread load
  useEffect(() => {
    let cancelled = false;
    // Fresh room per pane identity — the old pane's saves keep writing to the
    // old target box; this pane gets its own box and chain.
    setMessages([]);
    setThreadId(null);
    setLoadError(null);
    setError(null);
    setAttachments([]);
    setInput("");
    setProvider("");
    providerRef.current = "";
    modelRef.current = "";
    providerTouchedRef.current = false;
    paneGrantsRef.current = [];
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
    } catch (e) {
      // The guard's own words — a blocked undo must say why.
      setFileNote(norm, {
        ok: false,
        text: e instanceof Error ? e.message : String(e),
      });
    } finally {
      setUndoBusyPath(null);
    }
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
    (msgs: PaneMsg[]) => {
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
          const body: {
            messages: PaneMsg[];
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
          if (saveTargetRef.current === target) setThreadId(res.id);
        } catch {
          /* autosave is best-effort — never disturb the conversation */
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

  async function send() {
    const text = input.trim();
    if (!text || busy || !canCompose || uploading) return;
    const atts = attachmentsRef.current;
    setError(null);
    const userMsg: PaneMsg = {
      role: "user",
      content: text,
      ...(atts.length
        ? {
            attachmentNames: atts.map((a) => a.name),
            attachmentPaths: atts.map((a) => a.path),
          }
        : {}),
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
          approvalMode: baseSetupRef.current?.approval_mode,
        }),
      );
      const reply: PaneMsg = {
        role: "assistant",
        content: res.reply,
        ...(res.route ? { route: res.route } : {}),
        ...(res.adapted ? { adapted: res.adapted } : {}),
        ...(res.tools_used?.length ? { toolsUsed: res.tools_used } : {}),
        ...(res.deniedTools?.length ? { deniedTools: res.deniedTools } : {}),
        ...(res.documents?.length ? { documents: res.documents } : {}),
        ...(res.doors?.length ? { doors: res.doors } : {}),
      };
      const full = [...history, reply];
      setMessages(full);
      queueSave(full);
    } catch (e) {
      // HONEST failure: the error renders as an error; a streamed partial is
      // kept and marked interrupted (never presented as a complete answer);
      // the failed turn still saves so nothing is lost to a pane close.
      const partial = e instanceof StreamError ? e.partial : "";
      const full: PaneMsg[] = partial
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

  // Keep the newest bubble in view (guarded — jsdom has no scrollIntoView).
  useEffect(() => {
    endRef.current?.scrollIntoView?.({ behavior: "smooth", block: "end" });
  }, [messages, stream.text]);

  const engines = engineOptions(daemon.health?.providers);
  // A restored pick whose provider is currently offline still shows as ITSELF
  // (labelled), never silently swapped to Default — the no-auto-switch rule.
  const pickedUnavailable =
    provider !== "" && !engines.some((e) => e.id === provider);
  const folder = paneBasename(cwd) || cwd;
  const offline = !daemon.checking && !daemon.online;

  return (
    <div
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
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-3 py-3">
        {offline ? <OfflineHint /> : null}
        {loadError ? (
          <div className="space-y-2">
            <ErrorNote>{loadError}</ErrorNote>
            <button
              type="button"
              onClick={() => setLoadNonce((n) => n + 1)}
              className="rounded-lg border border-white/10 bg-white/[0.03] px-3 py-1.5 text-xs text-zinc-300 transition-colors hover:border-accent/40 hover:text-accent-soft"
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
              <div className="max-w-[92%] rounded-2xl rounded-br-md border border-accent/20 bg-accent/[0.08] px-3 py-2 text-sm whitespace-pre-wrap text-zinc-100">
                {m.content}
                {m.attachmentNames?.length ? (
                  <div className="mt-1.5 flex flex-wrap gap-1">
                    {m.attachmentNames.map((n) => (
                      <span
                        key={n}
                        className="inline-flex items-center gap-1 rounded-full border border-white/10 bg-white/[0.04] px-2 py-0.5 text-[10px] text-zinc-400"
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
              <PaneMarkdown text={m.content} />
              {m.interrupted ? (
                <div className="mt-1 flex items-center gap-1.5 text-[11px] text-amber-400/90">
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
                          className="flex items-center gap-2 rounded-lg border border-white/[0.06] bg-white/[0.02] px-2.5 py-1.5"
                        >
                          <code className="min-w-0 flex-1 truncate font-mono text-[11px] text-zinc-400">
                            {first}
                            {more ? " …" : ""}
                          </code>
                          <button
                            type="button"
                            onClick={() => runBlock(key, b.code)}
                            className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-accent/25 bg-accent/10 px-2 py-1 text-[11px] text-accent-soft transition-colors hover:bg-accent/20"
                          >
                            <Play size={10} /> Run in terminal
                          </button>
                        </div>
                        {runNotes[key] ? (
                          <div className="mt-1 flex items-center gap-1.5 text-[11px] text-amber-400/90">
                            <CircleAlert size={11} /> {runNotes[key]}
                          </div>
                        ) : null}
                      </div>
                    );
                  })
                : null}
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
                        className="rounded-lg border border-white/[0.06] bg-white/[0.02] px-2.5 py-1.5"
                      >
                        <div className="flex min-w-0 items-center gap-2">
                          <FileText
                            size={12}
                            className="shrink-0 text-accent-soft/80"
                          />
                          <span className="truncate text-xs text-zinc-200">
                            {paneBasename(p)}
                          </span>
                          <span
                            className="hidden truncate text-[10px] text-zinc-500 sm:inline"
                            title={p}
                          >
                            {parentDir(p)}
                          </span>
                          {outside ? (
                            // The receipt is truth — a path outside the
                            // pane's folder still renders, flagged.
                            <span className="shrink-0 rounded-full border border-amber-500/25 bg-amber-500/[0.06] px-1.5 py-px text-[10px] text-amber-300">
                              outside this folder
                            </span>
                          ) : null}
                          <div className="ml-auto flex shrink-0 items-center gap-1.5">
                            <button
                              type="button"
                              onClick={() => void openDoc(p)}
                              className="rounded-lg border border-white/10 bg-white/[0.03] px-2 py-1 text-[11px] text-zinc-300 transition-colors hover:border-accent/40 hover:text-accent-soft"
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
                                className="inline-flex items-center gap-1 rounded-lg border border-white/10 bg-white/[0.03] px-2 py-1 text-[11px] text-zinc-300 transition-colors hover:border-rose-400/40 hover:text-rose-300 disabled:opacity-40"
                              >
                                <Undo2 size={10} />
                                {undone ? "Undone" : "Undo newest write"}
                              </button>
                            ) : null}
                            {newerInThread ? (
                              <span className="shrink-0 text-[10px] text-amber-400/80">
                                (newer than this message)
                              </span>
                            ) : null}
                          </div>
                        </div>
                        {note ? (
                          <div
                            className={`mt-1 text-[11px] ${
                              note.ok ? "text-emerald-400/90" : "text-rose-300"
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
                documents={m.documents}
              />
              <DoorsStrip doors={m.doors} />
            </div>
          ),
        )}
        {stream.streaming ? (
          <div className="min-w-0" data-testid="pane-chat-live">
            {stream.tools.length > 0 ? (
              <div className="mb-2 space-y-1">
                {stream.tools.map((t) => (
                  <ToolRow key={t.id} card={t} />
                ))}
              </div>
            ) : null}
            {stream.text ? (
              <PaneMarkdown text={stream.text} />
            ) : (
              <div className="flex items-center gap-2 text-xs text-zinc-500">
                <Loader2 size={12} className="animate-spin" /> Thinking…
              </div>
            )}
          </div>
        ) : null}
        {/* MID-TURN APPROVAL (BC1 D1): the daemon paused this turn on an
            ask-tier tool — npm/git/docker asks are EXACTLY Build-pane
            language, and auto_tools arms them. Without this card the pause
            is invisible for up to 180s and the model answers around a
            refusal the user never saw. Same component, same POST, same
            grant store as the big page; the hook clears it on the
            approval_resolved frame (or when the stream ends). */}
        {stream.approval ? (
          <ApprovalCard
            approval={stream.approval}
            onConversation={armFromApproval}
          />
        ) : null}
        {error ? <ErrorNote>{error}</ErrorNote> : null}
        <div ref={endRef} />
      </div>

      {/* --------------------------------------------------------- composer */}
      <div className="border-t border-white/[0.06] px-3 pb-3 pt-2">
        {attachments.length > 0 ? (
          <div className="mb-2 flex flex-wrap gap-1.5">
            {attachments.map((a, i) => (
              <span
                key={a.path}
                className="inline-flex items-center gap-1.5 rounded-full border border-white/10 bg-white/[0.04] px-2.5 py-1 text-[11px] text-zinc-300"
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
          <div className="mb-1.5 flex items-center gap-1.5 text-[11px] text-zinc-500">
            <Loader2 size={10} className="animate-spin" /> Uploading…
          </div>
        ) : null}
        <div className="flex items-end gap-2">
          <textarea
            aria-label="Message"
            placeholder={`Build in ${folder}…`}
            value={input}
            disabled={!canCompose}
            rows={2}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void send();
              }
            }}
            className="min-h-[40px] flex-1 resize-none rounded-xl border border-white/10 bg-white/[0.03] px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-accent/40 focus:outline-none disabled:opacity-50"
          />
          <button
            type="button"
            aria-label="Send"
            disabled={!canCompose || busy || uploading || !input.trim()}
            onClick={() => void send()}
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-accent/30 bg-accent/15 text-accent-soft transition-colors hover:bg-accent/25 disabled:opacity-40"
          >
            {busy ? (
              <Loader2 size={15} className="animate-spin" />
            ) : (
              <Send size={15} />
            )}
          </button>
        </div>
        <div className="mt-1.5 flex min-w-0 items-center gap-2 text-[11px] text-zinc-500">
          <select
            aria-label="Engine"
            value={provider}
            onChange={(e) => pickEngine(e.target.value)}
            className="max-w-[180px] rounded-lg border border-white/10 bg-white/[0.03] px-1.5 py-1 text-[11px] text-zinc-300 focus:border-accent/40 focus:outline-none"
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
          {project ? (
            <span
              data-testid="pane-chat-project"
              className="inline-flex min-w-0 items-center gap-1 rounded-full border border-accent/20 bg-accent/[0.06] px-2 py-0.5 text-accent-soft"
              title={`Grounded in project ${project.name}`}
            >
              <FolderKanban size={10} className="shrink-0" />
              <span className="truncate">{project.name}</span>
            </span>
          ) : (
            <span className="truncate" title={cwd}>
              {folder}
            </span>
          )}
          {threadId ? (
            <span className="ml-auto shrink-0 text-zinc-600">saved</span>
          ) : null}
        </div>
      </div>
    </div>
  );
}
