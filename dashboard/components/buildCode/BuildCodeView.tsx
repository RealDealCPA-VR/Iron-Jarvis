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
 * folder's files. Stop interrupts the tool. ⋯ → Open in terminal.
 *
 * v1.413.0 (the user: "I cant copy and past images into the build modules
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
  ShieldAlert,
  Square,
  UserRound,
} from "lucide-react";
import { ApiError, get, patch, post } from "@/lib/api";
import { streamSSE } from "@/lib/useChatStream";
import { mintTurnId } from "@/lib/turnRejoin";
import { useVisibleInterval } from "@/lib/useVisibleInterval";
import { matchProjectEntries, useProjectFilesState, type ProjectEntry } from "@/lib/atMenuFiles";
import { StartComposer } from "@/components/buildCode/StartComposer";
import { SessionEditorProvider } from "@/components/harness/OpenInEditor";
import { scrollToLatest } from "@/lib/transcriptRepin";
import { HARNESS_COLUMN, HARNESS_COMPOSER_BOX } from "@/components/harness/harnessLook";
import { CodeConversation } from "@/components/buildCode/CodeConversation";
import { CodeAskCard, type CodeAnswer } from "@/components/buildCode/CodeAskCard";
import {
  AttachButton,
  AttachmentChips,
  pastedFiles,
  useHarnessAttachments,
  type HarnessFile,
} from "@/components/harness/HarnessAttachments";
import {
  OPEN_IN_TERMINAL,
  KEEPS_LAST_TITLE,
  OWN_SIGN_IN_TITLE,
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
  reuseItems,
  decodeCodeEvent,
  decodeCodeSession,
  decodeCodeSessionView,
  decodeCodeSessions,
  effortsFor,
  decodeCodingTools,
  foldConversation,
  groupByFolder,
  keepsLastOf,
  modeIsDangerous,
  sessionFolderWords,
  sessionRowLine,
  signsInItself,
  sessionTitle,
  steerPlaceholder,
  STEER_RETURNED_WORDS,
  toolLabel,
  type CodeEvent,
  type CodeSession,
  type CodingTool,
  type ConvItem,
  type DiskSession,
  type OpenAsk,
  type ToolCommand,
} from "@/lib/buildApp";

/** The wire's own paths (the daemon's names; never shown). */
const API = "/build/app";
const SESSIONS = `${API}/sessions`;
const TOOLS = `${API}/harnesses`;

/** How often the list is read again while it is on screen. */
export const CODE_LIST_REREAD_MS = 15_000;
/** How often a session whose running turn is not named is read again. */
export const CODE_RUNNING_REREAD_MS = 2_000;
/** While Iron Jarvis is not answering mid-reply: how often to look again
 *  (the reply is followed again once it is back). */
export const CODE_LOST_REREAD_MS = 3_000;
// v1.412.0: the mode chip lives in its own file (the start composer uses it
// too); its two-press timings are re-exported here, where tests read them.
export { FULL_ARM_GUARD_MS, FULL_DISARM_MS, ModeChip } from "@/components/buildCode/ModeChip";
import { ModeChip } from "@/components/buildCode/ModeChip";
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
function applyEvents(prev: CodeEvent[], arrived: readonly CodeEvent[], optimistic: { current: CodeEvent | null }): CodeEvent[] {
  let out = prev;
  for (const ev of arrived) {
    if (ev.type === "resolved" && out.some((e) => e.type === "resolved" && e.requestId === ev.requestId)) continue;
    if (ev.type === "user" && optimistic.current) {
      const i = out.indexOf(optimistic.current);
      optimistic.current = null;
      if (i >= 0) {
        out = [...out.slice(0, i), ev, ...out.slice(i + 1)];
        continue;
      }
    }
    out = [...out, ev];
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

function SessionPane({
  sessionId,
  tools,
  onChanged,
  onOpenedTerminal,
  onArchived,
  firstMessage = null,
  firstFiles = null,
  onFirstSent,
}: {
  sessionId: string;
  tools: readonly CodingTool[];
  onChanged: (s: CodeSession) => void;
  onOpenedTerminal: (terminalId: string) => void;
  onArchived: (id: string) => void;
  /** v1.412.0: the start composer's words — sent as this session's first
   *  message as soon as it is read (one press: make + send). */
  firstMessage?: string | null;
  /** v1.413.0: the start composer's files (pictures already uploaded). */
  firstFiles?: string[] | null;
  onFirstSent?: () => void;
}) {
  const [session, setSession] = useState<CodeSession | null>(null);
  const [events, setEvents] = useState<CodeEvent[]>([]);
  const [extra, setExtra] = useState<CodeEvent[]>([]);
  const [running, setRunning] = useState(false);
  const [following, setFollowing] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [askError, setAskError] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [files, setFiles] = useState<string[]>([]);
  const [menuOpen, setMenuOpen] = useState(false);
  const [menuIndex, setMenuIndex] = useState(0);
  const [moreOpen, setMoreOpen] = useState(false);
  const [fetchedCommands, setFetchedCommands] = useState<ToolCommand[] | null>(null);
  const [viewCommands, setViewCommands] = useState<ToolCommand[] | null>(null);
  /** v1.411.0: the conversation keeps its last model and effort (Pi). */
  const [keepsLast, setKeepsLast] = useState(false);
  const [dropped, setDropped] = useState(0);
  /** A quiet word that is not an error ("applies from your next message"). */
  const [info, setInfo] = useState<string | null>(null);
  const [continuing, setContinuing] = useState(false);
  /** Iron Jarvis stopped answering while a reply ran: looked for again. */
  const [lost, setLost] = useState(false);
  /** The reader scrolled up while something new arrived. */
  const [jump, setJump] = useState(false);
  const atBottomRef = useRef(true);
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  /** Events that arrived and are not drawn yet (flushed once a frame). */
  const pendingRef = useRef<CodeEvent[]>([]);
  const cancelFrameRef = useRef<(() => void) | null>(null);
  /** Bumped by each send: a read started before it never undoes it. */
  const sendSeqRef = useRef(0);
  const prevItemsRef = useRef<ConvItem[] | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const genRef = useRef(0);
  const optimisticRef = useRef<CodeEvent | null>(null);
  const answeredRef = useRef<Set<string>>(new Set());
  const boxRef = useRef<HTMLTextAreaElement | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  /** v1.413.0: pictures and files pasted, dropped or picked (uploaded). */
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
  const conv = useMemo(() => {
    const c = foldConversation(events, extra);
    const items = reuseItems(prevItemsRef.current, c.items);
    prevItemsRef.current = items;
    return { ...c, items };
  }, [events, extra]);
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

  /** v1.413.0: a note this window sent that the tool never read: its words
   *  and files go back in the box (beside anything typed since), said once. */
  const giveNoteBack = useCallback((id: string) => {
    const note = notesMineRef.current.get(id);
    if (!note || notesBackRef.current.has(id)) return;
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
      try {
        const raw = await get<unknown>(`${SESSIONS}/${encodeURIComponent(sessionId)}`);
        if (gen !== genRef.current) return;
        // A message sent while this read was out: the read is older than
        // what is on screen, so it decides nothing about the reply.
        if (seq !== sendSeqRef.current) return;
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
        if (view.commands) setViewCommands(view.commands);
        setKeepsLast(view.keepsLast);
        if (view.running && view.runningTurn && opts.follow) {
          const { base, carry } = cutForRejoin(view.events, view.runningTurn);
          setEvents(base);
          setRunning(true);
          void follow(gen, `${SESSIONS}/${encodeURIComponent(sessionId)}/turns/${encodeURIComponent(view.runningTurn)}/stream`, undefined, carry);
        } else if (opts.follow) {
          setEvents(view.events);
          setRunning(view.running);
        } else {
          // After a turn ends: the saved record replaces what was drawn —
          // unless it holds less (not written yet), which would blank the
          // reply the user just watched.
          setEvents((prev) => (view.events.length >= prev.length ? view.events : prev));
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

  // The newest stays in view only for a reader who is AT the bottom (the
  // chat's rule); one who scrolled up is left where they are, with "Jump to
  // latest".
  const onScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const at = el.scrollHeight - el.scrollTop - el.clientHeight <= BOTTOM_SLACK_PX;
    atBottomRef.current = at;
    if (at) setJump(false);
  }, []);
  useEffect(() => {
    if (atBottomRef.current) pinToBottom(sentinelRef.current);
    else setJump(true);
  }, [conv.items]);
  const jumpToLatest = useCallback(() => {
    atBottomRef.current = true;
    setJump(false);
    pinToBottom(sentinelRef.current);
  }, []);

  /** v1.413.0: a note for the reply that is running (Enter while it works):
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
        if (res?.state === "returned") giveNoteBack(id);
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

  const send = useCallback(async (handed?: string, handedFiles?: string[]) => {
    if (!session) return;
    // v1.413.0: a send waits for the pictures still going up (never goes
    // without them — the v1.275.0 rule); it reads the box when it runs.
    if (handed === undefined && att.busyRef.current) {
      att.whenSettled(() => void sendRef.current?.());
      return;
    }
    if (handed === undefined && runningRef.current) {
      void steer();
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
  }, [session, follow, onChanged, att, steer]);
  const sendRef = useRef<typeof send | null>(null);
  sendRef.current = send;

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

  const change = useCallback(
    async (patchIn: Record<string, unknown>) => {
      if (!session) return;
      setNote(null);
      setInfo(null);
      try {
        const raw = await patch<unknown>(`${SESSIONS}/${encodeURIComponent(session.id)}`, patchIn);
        const later = appliedNextWords(raw);
        if (later) setInfo(later);
        const keeps = keepsLastOf(raw);
        if (keeps !== null) setKeepsLast(keeps);
        const next = decodeCodeSession(raw);
        if (next) {
          setSession(next);
          onChanged(next);
        } else {
          void load({ follow: false });
        }
      } catch (e) {
        setNote(errorWords(e, "That change did not save."));
      }
    },
    [session, onChanged, load],
  );

  /** The account hit its limit: carry on with the same conversation on the
   *  next account of the same tool (Iron-Proxy picks it). A refusal (no
   *  other account) is said in its words. */
  const continueOn = useCallback(async () => {
    if (!session || continuing) return;
    setContinuing(true);
    setNote(null);
    setInfo(null);
    try {
      const raw = await post<unknown>(`${SESSIONS}/${encodeURIComponent(session.id)}/continue-on`, {});
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
    } finally {
      setContinuing(false);
    }
  }, [session, continuing, onChanged, tools]);

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
      await post(`${SESSIONS}/${encodeURIComponent(session.id)}/archive`);
      onArchived(session.id);
    } catch (e) {
      setNote(errorWords(e, "The session was not archived."));
    }
  }, [session, onArchived]);

  // ── the `/` and `@` lists ──
  const slash = /^\/(\S*)$/.exec(text);
  const at = /(^|\s)@(\S*)$/.exec(text);
  const filesState = useProjectFilesState(menuOpen && !!at, session?.folder ?? null);
  const commands = useMemo(() => {
    if (viewCommands && viewCommands.length) return viewCommands;
    if (tool && tool.commands.length) return tool.commands;
    return fetchedCommands ?? [];
  }, [viewCommands, tool, fetchedCommands]);
  // Keyed on the TEXT a regex found (a new match object each render would
  // re-run these on every key press).
  const slashKey = slash ? slash[0] : null;
  const atKey = at ? at[0] : null;
  useEffect(() => {
    if (slashKey === null || !session || (viewCommands && viewCommands.length) || (tool && tool.commands.length) || fetchedCommands !== null)
      return;
    get<unknown>(`${SESSIONS}/${encodeURIComponent(session.id)}/commands`)
      .then((raw) => {
        const list = Array.isArray(raw) ? raw : (raw as { commands?: unknown })?.commands;
        const out: ToolCommand[] = [];
        for (const c of Array.isArray(list) ? list : []) {
          const o = c as { name?: unknown; description?: unknown };
          if (typeof o?.name === "string" && o.name.trim())
            out.push({ name: o.name.trim().replace(/^\//, ""), description: typeof o.description === "string" ? o.description : "" });
        }
        setFetchedCommands(out);
      })
      .catch(() => setFetchedCommands([]));
  }, [slashKey, session, viewCommands, tool, fetchedCommands]);
  const menuRows: MenuRow[] = useMemo(() => {
    if (slash) {
      const q = slash[1].toLowerCase();
      return commands
        .filter((c) => c.name.toLowerCase().includes(q))
        .slice(0, 8)
        .map((cmd) => ({ kind: "command", cmd }));
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
    if (row.kind === "command") {
      setText(`/${row.cmd.name} `);
    } else if (at) {
      setText(text.slice(0, text.length - at[2].length) + `${row.entry.rel} `);
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
  return (
    <SessionEditorProvider folder={session.folder}>
      <div data-testid="code-session" data-session={session.id} className="flex h-full min-h-0 flex-col">
        <div className="flex shrink-0 items-center gap-2 border-b border-white/[0.06] px-4 py-2">
          <div className="min-w-0 flex-1">
            <p className="truncate text-[14px] text-zinc-100" data-testid="code-session-title">
              {sessionTitle(session, tools)}
            </p>
            <p className="truncate text-[12px] text-zinc-500" title={session.folder}>
              {sessionRowLine(session, tools)} · {sessionFolderWords(session)}
            </p>
          </div>
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
                  disabled={running}
                  onClick={() => void archive()}
                  className="flex w-full items-center rounded-lg px-2.5 py-1.5 text-left text-[13px] text-zinc-300 hover:bg-white/[0.06] disabled:opacity-50"
                >
                  Archive
                </button>
              </div>
            ) : null}
          </div>
        </div>

        <div
          ref={scrollRef}
          onScroll={onScroll}
          data-testid="code-transcript"
          className="min-h-0 flex-1 overflow-y-auto px-4 py-4"
        >
          <div className={HARNESS_COLUMN}>
            {dropped > 0 ? (
              <p data-testid="code-dropped" className="mb-3 text-center text-[11px] text-zinc-600">
                {droppedWords(dropped)}
              </p>
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
                folder={session.folder}
                tool={session.tool}
                onContinueOn={continueOn}
                continuing={continuing}
              />
            )}
            {running && !asking ? (
              <p data-testid="code-working" className="mt-3 flex items-center gap-2 text-[12px] text-zinc-500">
                <Loader2 size={13} className="animate-spin motion-reduce:animate-none" aria-hidden="true" />
                {toolName} is working…
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
          {asking ? (
            <>
              <CodeAskCard
                asks={conv.asks}
                toolName={toolName}
                dangerousMode={dangerous}
                onAnswer={answer}
                onStop={running ? stop : undefined}
                error={askError}
              />
              {/* v1.413.0: the mode can change while a card waits (the
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
                          <span className="shrink-0 font-mono">/{row.cmd.name}</span>
                          <span className="min-w-0 truncate text-[12px] text-zinc-500">{row.cmd.description}</span>
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
              placeholder={running ? steerPlaceholder(toolName) : `Message ${toolName} — / for commands, @ for files`}
              rows={2}
              value={text}
              onChange={(e) => setText(e.target.value)}
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
              <select
                aria-label={`${toolName} model`}
                data-testid="code-model-chip"
                value={session.model}
                disabled={running}
                title={keepsLast ? KEEPS_LAST_TITLE : undefined}
                onChange={(e) => void change({ model: e.target.value })}
                className={CHIP_SELECT}
              >
                {/* A conversation that keeps its last model offers no way
                    back to the default (Pi would restore the last one). */}
                {!keepsLast || !session.model ? (
                  <option value="">{defaultModelWords(toolName, keepsLast)}</option>
                ) : null}
                {(tool?.models ?? []).map((m) => (
                  <option key={m.id} value={m.id}>
                    {toolName} · {m.label}
                  </option>
                ))}
                {session.model && !(tool?.models ?? []).some((m) => m.id === session.model) ? (
                  <option value={session.model}>
                    {toolName} · {session.model}
                  </option>
                ) : null}
              </select>
              {tool && (effortsFor(tool, session.model).length || session.effort) ? (
                <select
                  aria-label="Effort"
                  data-testid="code-effort-chip"
                  value={session.effort}
                  disabled={running}
                  title={keepsLast ? KEEPS_LAST_TITLE : undefined}
                  onChange={(e) => void change({ effort: e.target.value })}
                  className={CHIP_SELECT}
                >
                  {!keepsLast || !session.effort ? (
                    <option value="">{defaultEffortWords(keepsLast)}</option>
                  ) : null}
                  {/* v1.412.0 review: the levels THIS model takes (the
                      daemon's per-model list, else the tool's); a kept
                      effort the model no longer lists stays visible. */}
                  {[...effortsFor(tool, session.model), ...(session.effort && !effortsFor(tool, session.model).includes(session.effort) ? [session.effort] : [])].map((x) => (
                    <option key={x} value={x}>
                      {x.charAt(0).toUpperCase() + x.slice(1)} effort
                    </option>
                  ))}
                </select>
              ) : null}
              <ModeChip
                tool={tool}
                value={session.mode}
                onChange={(mode, confirm) => void change(confirm ? { mode, confirm: true } : { mode })}
              />
              <span
                data-testid="code-account-chip"
                title={
                  signsInItself(session.tool, tools)
                    ? OWN_SIGN_IN_TITLE
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
                  onClick={() => void send()}
                  aria-label={`Send a note to ${toolName}`}
                  title={`Send a note to ${toolName} while it works (Enter)`}
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
        </div>
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
  const [rowNote, setRowNote] = useState<string | null>(null);
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
  useEffect(() => {
    readList();
    get<unknown>(TOOLS)
      .then((raw) => setTools(decodeCodingTools(raw)))
      .catch((e) => setToolsError(errorWords(e, "The coding tools could not be looked for.")));
  }, [readList]);
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
    (id: string) => {
      setSessions((prev) => (prev ? prev.filter((s) => s.id !== id) : prev));
      if (openId === id) open(null);
      if (showArchived) readArchived();
    },
    [openId, open, showArchived, readArchived],
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

  const groups = useMemo(() => groupByFolder(sessions ?? []), [sessions]);
  const archivedGroups = useMemo(() => groupByFolder(archivedRows ?? [], { archived: true }), [archivedRows]);

  const row = (s: CodeSession, archivedRow: boolean) => (
    <li key={s.id} className="group relative">
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
            <Loader2 size={11} className="shrink-0 animate-spin text-zinc-400 motion-reduce:animate-none" aria-label="Working" />
          ) : null}
          {s.waiting ? <ShieldAlert size={11} className="shrink-0 text-tone-warn" aria-label="Waiting for your OK" /> : null}
          {s.lastError ? <AlertTriangle size={11} className="shrink-0 text-tone-warn" aria-label="Last reply failed" /> : null}
          <span className="truncate">{sessionTitle(s, tools)}</span>
        </span>
        <span data-testid={`code-row-line-${s.id}`} className="truncate text-[11px] text-zinc-500">
          {sessionRowLine(s, tools)}
          {s.source === "disk" ? ` · saved by ${toolLabel(s.tool, tools)}` : ""}
        </span>
      </button>
      <button
        type="button"
        aria-label={`More for ${sessionTitle(s, tools)}`}
        aria-haspopup="menu"
        aria-expanded={rowMenu === s.id}
        data-testid={`code-row-more-${s.id}`}
        onClick={() => setRowMenu((cur) => (cur === s.id ? null : s.id))}
        className="absolute right-1 top-1.5 grid h-6 w-6 place-items-center rounded-md text-zinc-500 hover:bg-white/[0.06] hover:text-zinc-200 [@media(hover:hover)]:opacity-0 [@media(hover:hover)]:group-hover:opacity-100 focus-visible:opacity-100"
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
        </div>
      ) : null}
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
        <div className="min-h-0 flex-1 overflow-y-auto px-1.5 pb-2">
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
              No sessions yet. Type a task to start Claude Code, Codex or Pi.
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
                </h3>
                <ul className="mt-0.5">{g.sessions.map((s) => row(s, false))}</ul>
                {g.noFolder ? null : savedFor(g.folder)}
              </section>
            ))
          )}
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
            onChanged={noteOpen}
            onOpenedTerminal={onOpenedTerminal}
            onArchived={archived}
            firstMessage={firstFor && firstFor.id === openId ? firstFor.text : null}
            firstFiles={firstFor && firstFor.id === openId ? firstFor.files : null}
            onFirstSent={clearFirst}
          />
        )}
      </section>
    </div>
  );
}

export default BuildCodeView;
