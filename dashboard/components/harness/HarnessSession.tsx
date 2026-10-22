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

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowUp,
  CircleAlert,
  ListChecks,
  FolderOpen,
  Loader2,
  MoreHorizontal,
  Pencil,
  Square,
} from "lucide-react";
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
import { modelGroupName, providerDisplay } from "@/lib/onboarding";
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
import {
  PANE_COMPOSER_BOX,
  PANE_COMPOSER_CARD,
  PANE_REPLY_PROSE,
  PANE_USER_BUBBLE,
  paneSendClass,
} from "@/components/terminal/paneChatLook";
import { HarnessPanes, type SessionTerminalApi } from "@/components/harness/HarnessPanes";
import { AccountChip, AccountLimitCard, AccountList } from "@/components/harness/HarnessAccounts";
import {
  accountFor,
  armResumeWhenReady,
  cancelResumeWhenReady,
  chooseAccountAndContinue,
  decodeAccountLimit,
  decodeResumeWait,
  fetchAccounts,
  makeDefaultAccount,
  setSessionAccount,
  vendorOfProvider,
  type AccountGroups,
  type AccountLimit,
  type BuildAccount,
} from "@/lib/buildAccounts";
import {
  answerQuestion,
  archiveSession,
  decodeAskResolved,
  decodeAskUser,
  deleteSession,
  getSession,
  isMissing,
  patchSession,
  planFromCards,
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

/** The live reply: subscribed HERE, so the session does not re-render per token. */
function LiveText({ stream }: { stream: ReturnType<typeof useChatStream> }) {
  const text = useLiveText(stream);
  const thinking = useLiveThinking(stream);
  return (
    <>
      {thinking ? <ThinkingDisclosure text={thinking} live /> : null}
      {text ? (
        <div data-testid="harness-live-text" className={PANE_REPLY_PROSE}>
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

/** Where a session runs when it has no account to pick: the model server's
 *  name for a local model, the demo model by its name, else "API key". */
export function placeWords(provider: string, models: readonly ModelOption[]): string {
  if (!provider) return "";
  if (provider === "mock") return providerDisplay("mock");
  const row = models.find((m) => m.provider === provider);
  if (row && row.kind === "local" && row.on_this_network !== false) return modelGroupName(row);
  return "API key";
}

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
  const [queued, setQueued] = useState<string[]>([]);
  const turnRef = useRef<string | null>(null);
  const [turnId, setTurnId] = useState<string | null>(null);
  const [turnsEnded, setTurnsEnded] = useState(0);
  const boxRef = useRef<HTMLTextAreaElement | null>(null);
  const transcriptRef = useRef<HTMLDivElement | null>(null);
  const [modelMenuOpen, setModelMenuOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [renaming, setRenaming] = useState<string | null>(null);
  // Accounts (Phase 5): undefined = not read yet, null = this Iron Jarvis has
  // none (the chip is hidden).
  const [accounts, setAccounts] = useState<AccountGroups | null | undefined>(undefined);
  const [accountNote, setAccountNote] = useState<string | null>(null);
  const [accountBusy, setAccountBusy] = useState(false);
  const [limit, setLimit] = useState<AccountLimit | null>(null);
  const [waiting, setWaiting] = useState(false);
  const [choosing, setChoosing] = useState(false);
  const [limitError, setLimitError] = useState<string | null>(null);
  const busy = sending || stream.streaming;

  // ------------------------------------------------------------- reading
  const load = useCallback(async (): Promise<BuildSessionView | null> => {
    try {
      const v = await getSession(threadId);
      if (!v) throw new Error("The session could not be read.");
      setView(v);
      setLoadError(null);
      const lim = decodeAccountLimit(v.accountLimit);
      if (lim) setLimit(lim);
      const wait = decodeResumeWait(v.resumeWhenReady);
      setWaiting(!!wait);
      return v;
    } catch (e) {
      if (isMissing(e)) {
        setLoadError("This session is gone.");
      } else {
        setLoadError(e instanceof Error ? e.message : String(e));
      }
      return null;
    }
  }, [threadId]);

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
        setLimit(lim);
        setWaiting(false);
        setChoosing(false);
      }
    }
  }, []);

  /** After a turn: adopt what the daemon wrote, put unread steer notes back
   *  in the box, and send what was waiting. */
  const settle = useCallback(
    async (finished: string, unread: readonly string[], clean: boolean) => {
      // The daemon writes the reply line as the turn ends, before it reports
      // the session as no longer running; look again only while it still
      // says the turn runs (a turn stopped before its first word writes none).
      let v: BuildSessionView | null = null;
      for (let i = 0; i < REREAD_TRIES; i += 1) {
        v = await load();
        if (!v || !v.running) break;
        if (v.messages.some((m) => m.role === "assistant" && m.turnId === finished)) break;
        if (i < REREAD_TRIES - 1) await sleep(REREAD_GAP_MS);
      }
      setPending(null);
      setSteerNotes([]);
      setQuestions((prev) => prev.map((q) => (q.outcome ? q : { ...q, outcome: "stopped" })));
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
    [load, onChanged, threadId],
  );

  const runTurn = useCallback(
    async (text: string | null, rejoinId?: string) => {
      const id = rejoinId ?? mintTurnId();
      turnRef.current = id;
      setTurnId(id);
      setSending(true);
      setError(null);
      let started = !!rejoinId;
      // A NEW turn retires the limit card at its first real frame (once —
      // the turn's own `account_limit` must stay up after it).
      let retired = !!rejoinId;
      let unread: string[] = [];
      let clean = false;
      try {
        const res = await stream.run(
          rejoinId ? undefined : { text, turn_id: id },
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
            if (text) setInput((cur) => (cur.trim() ? cur : text));
            void runTurn(null, v.turnId);
            return;
          }
        }
        setError(turnErrorText(e));
        if (!started && text) {
          // Never started: the words go back in the box, nothing was lost.
          setPending(null);
          setInput((cur) => (cur.trim() ? cur : text));
        }
      } finally {
        turnRef.current = null;
        setTurnId(null);
        setSending(false);
      }
      if (started) await settle(id, unread, clean);
    },
    [stream, threadId, onFrame, load, settle],
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
      if (v.queued.length) setQueued(v.queued.map((q) => q.text).slice(0, MAX_QUEUED));
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
    (items: string[]) => {
      put(`/chat/threads/${encodeURIComponent(threadId)}/queue`, {
        items: items.map((text) => ({ text })),
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

  async function send(textIn?: string) {
    const text = (textIn ?? input).trim();
    if (!text || !view) return;
    if (busy) {
      await steer(text);
      return;
    }
    if (view.archived) {
      setError("This session is archived. Bring it back before you continue it.");
      return;
    }
    if (!textIn) setInput("");
    setPending({ role: "user", content: text, at: new Date().toISOString() });
    await runTurn(text);
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

  function queueMessage() {
    const text = input.trim();
    if (!text) return;
    if (queued.length >= MAX_QUEUED) {
      setError(`Up to ${MAX_QUEUED} messages can wait. Send or remove one first.`);
      return;
    }
    const next = [...queued, text];
    setQueued(next);
    persistQueue(next);
    setInput("");
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
  const vendor = vendorOfProvider(provider);
  const groups = accounts ?? null;
  const currentAccount: BuildAccount | null = accountFor(groups, vendor, view?.setup.account ?? "");

  async function pickAccount(a: BuildAccount) {
    setAccountBusy(true);
    setAccountNote(null);
    try {
      await setSessionAccount(threadId, a.id);
      await load();
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
      await makeDefaultAccount(a.id);
      await refreshAccounts();
    } catch (e) {
      setAccountNote(e instanceof Error ? e.message : String(e));
    } finally {
      setAccountBusy(false);
    }
  }

  async function resumeWhenReady() {
    setLimitError(null);
    try {
      await armResumeWhenReady(threadId);
      setWaiting(true);
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
      await cancelResumeWhenReady(threadId);
      setWaiting(false);
    } catch (e) {
      setLimitError(e instanceof Error ? e.message : String(e));
    }
  }

  async function chooseAndContinue(a: BuildAccount) {
    setLimitError(null);
    setAccountBusy(true);
    try {
      await chooseAccountAndContinue(threadId, a.id);
      setLimit(null);
      setChoosing(false);
      const v = await load();
      if (v?.running && v.turnId && v.turnId !== "running") void runTurn(null, v.turnId);
    } catch (e) {
      setLimitError(
        isMissing(e)
          ? "This version of Iron Jarvis cannot move a session to another account yet."
          : e instanceof Error
            ? e.message
            : String(e),
      );
    } finally {
      setAccountBusy(false);
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
    const base = view?.messages ?? [];
    if (!pending) return base;
    // The daemon wrote the question already (a re-read mid-turn): not twice.
    const dup = base.some((m) => m.role === "user" && m.content === pending.content && m.turnId && m.turnId === turnId);
    return dup ? base : [...base, pending];
  }, [view?.messages, pending, turnId]);
  const livePlan = planFromCards(stream.tools);
  const lastReply = [...shown].reverse().find((m) => m.role === "assistant");
  const choice = view?.setup.provider && view?.setup.model ? `${view.setup.provider}::${view.setup.model}` : "";
  const levels = reasoningLevelsOf(reasoningRowFor(choice, catalog.models, defaultProvider, defaultModel));
  const reasoningDef = reasoningDefaultOf(reasoningRowFor(choice, catalog.models, defaultProvider, defaultModel));
  const trigger = modelTriggerOf(choice, catalog.models, defaultProvider, defaultModel);
  const offline = !daemon.checking && !daemon.online;
  const place = !vendor ? placeWords(provider, catalog.models) : "";
  const sendReady = !!input.trim() && !!view && !offline;

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
    <div data-testid="harness-session" data-session={view.id} className="flex h-full min-h-0 flex-col gap-3 lg:flex-row">
      {/* ------------------------------------------------- the conversation */}
      {/* Below lg the conversation and the panes stack, each a screen-sized
          box of its own (the transcript scrolls inside); from lg they sit
          side by side at the view's height. */}
      <section className="flex h-[78vh] min-w-0 flex-1 flex-col lg:h-auto lg:min-h-0">
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
          </div>
          {accounts && vendor ? (
            <AccountChip
              account={currentAccount}
              groups={accounts}
              onPick={(a) => void pickAccount(a)}
              onMakeDefault={(a) => void makeDefault(a)}
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
                    title="Delete this session and its conversation"
                  />
                </div>
              </div>
            ) : null}
          </div>
        </header>

        <div
          ref={transcriptRef}
          data-testid="harness-transcript"
          className="min-h-0 flex-1 space-y-4 overflow-y-auto px-1 py-3"
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
                <div key={i} className="flex justify-end">
                  <div data-testid="harness-user-bubble" className={PANE_USER_BUBBLE}>
                    {m.content}
                  </div>
                </div>
              )
            ) : (
              <div key={i} data-testid="harness-reply" className="min-w-0">
                <WorkLine
                  {...paneWorkInput(m)}
                  cutOff={m.truncated ? "truncated" : m.interrupted ? "interrupted" : null}
                />
                {m.content ? (
                  <div className={PANE_REPLY_PROSE}>
                    <MemoMarkdown content={m.content} />
                  </div>
                ) : null}
                {m.error ? (
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
              {stream.tools.length ? <LiveToolRows cards={stream.tools} since={stream.startedAt ?? null} /> : null}
              <LiveText stream={stream} />
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
              waitingResetsAt={decodeResumeWait(view.resumeWhenReady)?.resetsAt ?? null}
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
                      groups={accounts}
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
          {error ? <ErrorNote>{error}</ErrorNote> : null}
        </div>

        {/* --------------------------------------------- the dock + composer */}
        <div className="shrink-0 px-1 pb-1">
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
                  <span className="truncate">Waiting to send: {q}</span>
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
          >
            <textarea
              ref={boxRef}
              aria-label="Message"
              data-testid="harness-input"
              placeholder={
                busy ? "Add a note while it works (Enter), or Ctrl+Enter to send after it" : `Build in ${view.folderName}…`
              }
              value={input}
              rows={2}
              disabled={offline}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.nativeEvent.isComposing || e.keyCode === 229) return;
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
              className={PANE_COMPOSER_BOX}
            />
            <div className="flex min-w-0 flex-wrap items-center gap-1 px-2 pb-2">
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
              <div className="ml-auto flex shrink-0 items-center gap-1">
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
      <aside className="flex h-[70vh] w-full shrink-0 flex-col lg:h-auto lg:min-h-0 lg:w-[360px] xl:w-[420px]">
        <HarnessPanes
          view={view}
          livePlan={livePlan}
          terminal={terminal}
          refreshKey={turnsEnded}
          undoFor={undoFor}
          onUndo={onUndo}
        />
      </aside>
    </div>
  );
}
