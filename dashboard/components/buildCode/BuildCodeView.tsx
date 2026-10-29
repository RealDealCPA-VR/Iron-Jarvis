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
 * sessions says so). New session: the tool by name (greyed with the reason
 * when it is not installed or signed out), the folder (the existing folder
 * tree), the account, the mode.
 *
 * CENTRE: the conversation (components/buildCode/CodeConversation), and the
 * composer — or, while the tool waits on the user, its question in the
 * composer's place (components/buildCode/CodeAskCard; the composer stays
 * mounted, hidden and inert, so a draft survives). Chips: tool · model,
 * effort, mode (Full access takes two presses and is sent with
 * `confirm: true`), account. `/` lists the tool's own commands, `@` the
 * folder's files. Stop interrupts the tool. ⋯ → Open in terminal.
 *
 * THE TURN IS IRON JARVIS'S (the durable-turn rule): closing the page never
 * stops it; opening a session whose turn still runs follows it again
 * (`GET …/turns/{turn_id}/stream`).
 *
 * Every read and write goes through lib/api's helpers; the streams through
 * lib/useChatStream's `streamSSE` (frames read by its `onFrame` tap).
 */

import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import {
  AlertTriangle,
  ArrowUp,
  Code2,
  FolderOpen,
  Loader2,
  MoreHorizontal,
  Plus,
  Shield,
  ShieldAlert,
  Square,
  UserRound,
  X,
} from "lucide-react";
import { ApiError, get, patch, post } from "@/lib/api";
import { streamSSE } from "@/lib/useChatStream";
import { mintTurnId } from "@/lib/turnRejoin";
import { useVisibleInterval } from "@/lib/useVisibleInterval";
import { matchProjectEntries, useProjectFilesState, type ProjectEntry } from "@/lib/atMenuFiles";
import { DirectoryTree } from "@/components/terminal/DirectoryTree";
import { SessionEditorProvider } from "@/components/harness/OpenInEditor";
import { HARNESS_COLUMN, HARNESS_COMPOSER_BOX } from "@/components/harness/harnessLook";
import { CodeConversation } from "@/components/buildCode/CodeConversation";
import { CodeAskCard, type CodeAnswer } from "@/components/buildCode/CodeAskCard";
import {
  OPEN_IN_TERMINAL,
  accountWords,
  cutForRejoin,
  decodeCodeEvent,
  decodeCodeSession,
  decodeCodeSessionView,
  decodeCodeSessions,
  decodeCodingTools,
  foldConversation,
  groupByFolder,
  modeIsDangerous,
  modeLabel,
  sessionRowLine,
  sessionTitle,
  toolLabel,
  toolUnavailable,
  type CodeEvent,
  type CodeSession,
  type CodingTool,
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

const QUIET_BTN =
  "inline-flex h-7 items-center gap-1.5 rounded-lg px-2 text-[12px] text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60 disabled:cursor-not-allowed disabled:opacity-50";
const CHIP_SELECT =
  "h-7 max-w-[12rem] cursor-pointer truncate rounded-lg border-0 bg-transparent px-1.5 text-[12px] text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60 disabled:cursor-not-allowed disabled:opacity-50";

function errorWords(e: unknown, fallback: string): string {
  if (e instanceof ApiError && e.message) return e.message;
  if (e instanceof Error && e.message) return e.message;
  return fallback;
}

// ── the mode chip: Full access takes two presses ────────────────────────────

/** The mode menu. Choosing an ordinary mode is one press; a DANGEROUS one
 *  (Full access) arms first ("Press again…") and only the second press
 *  chooses it, reported with `confirm: true`. */
export function ModeChip({
  tool,
  value,
  onChange,
  disabled,
  testId = "code-mode-chip",
}: {
  tool: CodingTool | null;
  value: string;
  onChange: (mode: string, confirm: boolean) => void;
  disabled?: boolean;
  testId?: string;
}) {
  const [open, setOpen] = useState(false);
  const [armed, setArmed] = useState<string | null>(null);
  const modes = tool?.modes ?? [];
  const dangerous = modeIsDangerous(value, tool);
  useEffect(() => {
    if (!open) setArmed(null);
  }, [open]);
  return (
    <div className="relative" data-testid={testId}>
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={disabled || modes.length === 0}
        data-testid={`${testId}-button`}
        onClick={() => setOpen((v) => !v)}
        className={`${QUIET_BTN} ${dangerous ? "text-tone-warn" : ""}`}
        title="What the tool may do without asking you"
      >
        {dangerous ? <ShieldAlert size={13} aria-hidden="true" /> : <Shield size={13} aria-hidden="true" />}
        {modeLabel(value, tool)}
      </button>
      {open ? (
        <div
          role="menu"
          data-testid={`${testId}-menu`}
          className="absolute bottom-full left-0 z-20 mb-1 w-64 max-w-[calc(100vw-2rem)] rounded-xl bg-ink-800 p-1 shadow-card"
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              e.preventDefault();
              setOpen(false);
            }
          }}
        >
          {modes.map((m) => {
            const isArmed = armed === m.id;
            return (
              <button
                key={m.id}
                type="button"
                role="menuitemradio"
                aria-checked={m.id === value}
                data-testid={`${testId}-option-${m.id}`}
                data-armed={isArmed ? "true" : undefined}
                onClick={() => {
                  if (m.dangerous && !isArmed) {
                    setArmed(m.id);
                    return;
                  }
                  setOpen(false);
                  onChange(m.id, m.dangerous);
                }}
                className={`flex w-full flex-col items-start rounded-lg px-2.5 py-1.5 text-left text-[13px] transition-colors hover:bg-white/[0.06] ${
                  m.dangerous ? "text-tone-warn" : m.id === value ? "text-zinc-100" : "text-zinc-300"
                }`}
              >
                <span>{m.label}</span>
                {isArmed ? (
                  <span className="text-[12px] text-tone-warn">
                    Press again to let it do anything without asking.
                  </span>
                ) : m.dangerous ? (
                  <span className="text-[12px] text-zinc-500">Runs everything without asking. Two presses.</span>
                ) : null}
              </button>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}

// ── one session ─────────────────────────────────────────────────────────────

type MenuRow = { kind: "command"; cmd: ToolCommand } | { kind: "file"; entry: ProjectEntry };

function SessionPane({
  sessionId,
  tools,
  onChanged,
  onOpenedTerminal,
  onArchived,
}: {
  sessionId: string;
  tools: readonly CodingTool[];
  onChanged: (s: CodeSession) => void;
  onOpenedTerminal: (terminalId: string) => void;
  onArchived: (id: string) => void;
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
  const abortRef = useRef<AbortController | null>(null);
  const genRef = useRef(0);
  const optimisticRef = useRef<CodeEvent | null>(null);
  const answeredRef = useRef<Set<string>>(new Set());
  const boxRef = useRef<HTMLTextAreaElement | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  const tool = useMemo(() => tools.find((t) => t.id === session?.tool) ?? null, [tools, session?.tool]);
  const toolName = toolLabel(session?.tool ?? "", tools);
  const conv = useMemo(() => foldConversation(events, extra), [events, extra]);
  const dangerous = modeIsDangerous(session?.mode ?? "", tool);

  const append = useCallback((gen: number, ev: CodeEvent) => {
    if (gen !== genRef.current) return;
    if (ev.type === "resolved" && answeredRef.current.has(ev.requestId)) return;
    setEvents((prev) => {
      if (ev.type === "user" && optimisticRef.current) {
        const i = prev.indexOf(optimisticRef.current);
        optimisticRef.current = null;
        if (i >= 0) return [...prev.slice(0, i), ev, ...prev.slice(i + 1)];
      }
      return [...prev, ev];
    });
  }, []);

  /** Read the session from the daemon; follow its running turn. */
  const load = useCallback(
    async (opts: { follow: boolean }) => {
      const gen = genRef.current;
      try {
        const raw = await get<unknown>(`${SESSIONS}/${encodeURIComponent(sessionId)}`);
        if (gen !== genRef.current) return;
        const view = decodeCodeSessionView(raw);
        if (!view) {
          setLoadError("This session could not be read.");
          return;
        }
        setLoadError(null);
        setSession(view.session);
        onChanged(view.session);
        setExtra(view.pending);
        if (view.running && view.runningTurn && opts.follow) {
          const { base, carry } = cutForRejoin(view.events);
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
        setLoadError(errorWords(e, "This session could not be read."));
      }
    },
    // `follow` is stable (declared below with useCallback and no deps).
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [sessionId, onChanged],
  );

  /** Follow one turn's events (a new turn: `body`; a rejoin: none). */
  const follow = useCallback(
    async (gen: number, path: string, body: unknown, carry: CodeEvent | null = null) => {
      abortRef.current?.abort();
      const ctl = new AbortController();
      abortRef.current = ctl;
      setFollowing(true);
      let first = true;
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
          if (gen === genRef.current) {
            append(gen, {
              type: "error",
              at: "",
              title: ev.title || ev.detail || "The reply could not be followed.",
              fix: ev.fix ?? "",
              technical: ev.technical ?? "",
              fixKind: ev.fix_kind ?? "",
            });
          }
        }
      } finally {
        if (abortRef.current === ctl) abortRef.current = null;
        if (gen === genRef.current) {
          setFollowing(false);
          setRunning(false);
          // The saved record is the truth (titles, trimmed frames): read it
          // again once the turn is over, unless the user left this session.
          if (!ctl.signal.aborted) void load({ follow: false });
        }
      }
    },
    [append, load],
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
    setNote(null);
    setAskError(null);
    setFetchedCommands(null);
    void load({ follow: true });
    return () => {
      genRef.current += 1;
      abortRef.current?.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  // A turn that runs but was not named (an older daemon): read again.
  useVisibleInterval(() => void load({ follow: true }), CODE_RUNNING_REREAD_MS, running && !following);

  // Keep the newest at the bottom.
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [conv.items.length, events.length]);

  const send = useCallback(async () => {
    const words = text.trim();
    if (!words || running || !session) return;
    const gen = genRef.current;
    const turnId = mintTurnId();
    const mine: CodeEvent = { type: "user", at: new Date().toISOString(), text: words, files };
    optimisticRef.current = mine;
    setEvents((prev) => [...prev, mine]);
    setText("");
    const sent = files;
    setFiles([]);
    setNote(null);
    setRunning(true);
    await follow(gen, `${SESSIONS}/${encodeURIComponent(session.id)}/turn`, {
      text: words,
      turn_id: turnId,
      ...(sent.length ? { files: sent } : {}),
    });
  }, [text, files, running, session, follow]);

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
        setEvents((prev) => [
          ...prev,
          {
            type: "resolved",
            at: new Date().toISOString(),
            requestId: ask.requestId,
            decision: ask.type === "question" ? (a.decision === "deny" ? "deny" : "answered") : a.decision,
          },
        ]);
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) {
          // Answered elsewhere, or the tool stopped asking.
          answeredRef.current.add(ask.requestId);
          setEvents((prev) => [...prev, { type: "resolved", at: "", requestId: ask.requestId, decision: "cancelled" }]);
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
      try {
        const raw = await patch<unknown>(`${SESSIONS}/${encodeURIComponent(session.id)}`, patchIn);
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
    if (tool && tool.commands.length) return tool.commands;
    return fetchedCommands ?? [];
  }, [tool, fetchedCommands]);
  useEffect(() => {
    if (!slash || !session || (tool && tool.commands.length) || fetchedCommands !== null) return;
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
  }, [slash, session, tool, fetchedCommands]);
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
    setMenuOpen(!!(slash || at));
    setMenuIndex(0);
  }, [text, slash, at]);

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
              {sessionRowLine(session, tools)} · {session.folder}
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

        <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
          <div className={HARNESS_COLUMN}>
            {conv.items.length === 0 ? (
              <p data-testid="code-empty" className="py-10 text-center text-[13px] text-zinc-500">
                {session.source === "disk"
                  ? `This conversation was saved by ${toolName}. Send a message to carry it on here.`
                  : `Ask ${toolName} to do something in ${session.folder || "this folder"}.`}
              </p>
            ) : (
              <CodeConversation items={conv.items} folder={session.folder} />
            )}
            {running && !asking ? (
              <p data-testid="code-working" className="mt-3 flex items-center gap-2 text-[12px] text-zinc-500">
                <Loader2 size={13} className="animate-spin motion-reduce:animate-none" aria-hidden="true" />
                {toolName} is working…
              </p>
            ) : null}
          </div>
        </div>

        <div className={`shrink-0 px-4 pb-4 ${HARNESS_COLUMN}`}>
          {note ? (
            <p role="alert" data-testid="code-note" className="mb-2 text-[12px] text-tone-danger">
              {note}
            </p>
          ) : null}
          {asking ? (
            <CodeAskCard
              asks={conv.asks}
              toolName={toolName}
              dangerousMode={dangerous}
              onAnswer={answer}
              onStop={running ? stop : undefined}
              error={askError}
            />
          ) : null}
          <div
            data-testid="code-composer"
            data-code-composer=""
            hidden={asking}
            inert={asking}
            className="relative rounded-[20px] bg-ink-800 shadow-[0_0_0_0.5px_rgb(var(--white)/0.12)]"
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
            <textarea
              ref={boxRef}
              data-testid="code-box"
              aria-label={`Message ${toolName}`}
              placeholder={running ? `${toolName} is working. Press Stop to interrupt it.` : `Message ${toolName} — / for commands, @ for files`}
              rows={2}
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={onBoxKey}
              className={HARNESS_COMPOSER_BOX}
            />
            <div className="flex flex-wrap items-center gap-1 px-2 pb-2">
              <span className="sr-only">Coding tool: {toolName}</span>
              <select
                aria-label={`${toolName} model`}
                data-testid="code-model-chip"
                value={session.model}
                disabled={running}
                onChange={(e) => void change({ model: e.target.value })}
                className={CHIP_SELECT}
              >
                <option value="">{toolName} · default model</option>
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
              {tool && tool.efforts.length ? (
                <select
                  aria-label="Effort"
                  data-testid="code-effort-chip"
                  value={session.effort}
                  disabled={running}
                  onChange={(e) => void change({ effort: e.target.value })}
                  className={CHIP_SELECT}
                >
                  <option value="">Default effort</option>
                  {tool.efforts.map((x) => (
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
                title="The account this session runs on (chosen when it was made)"
                className="inline-flex h-7 max-w-[12rem] items-center gap-1.5 truncate px-2 text-[12px] text-zinc-500"
              >
                <UserRound size={13} aria-hidden="true" className="shrink-0" />
                <span className="truncate">{accountName}</span>
              </span>
              <span className="ml-auto" />
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
                  disabled={!text.trim()}
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

// ── New session ─────────────────────────────────────────────────────────────

function NewSession({
  tools,
  toolsError,
  startFolder,
  onCreated,
  onCancel,
}: {
  tools: readonly CodingTool[] | null;
  toolsError: string | null;
  startFolder: string | null;
  onCreated: (s: CodeSession) => void;
  onCancel: () => void;
}) {
  const firstOk = tools?.find((t) => !toolUnavailable(t))?.id ?? "";
  const [toolId, setToolId] = useState(firstOk);
  const [folder, setFolder] = useState<string | null>(startFolder);
  const [account, setAccount] = useState("");
  const [mode, setMode] = useState<{ id: string; confirm: boolean }>({ id: "", confirm: false });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!toolId && firstOk) setToolId(firstOk);
  }, [toolId, firstOk]);
  const tool = tools?.find((t) => t.id === toolId) ?? null;
  useEffect(() => {
    setAccount("");
    setMode({ id: tool?.modes[0]?.id ?? "", confirm: false });
  }, [tool]);

  const start = async () => {
    if (!tool || !folder || toolUnavailable(tool)) return;
    setBusy(true);
    setError(null);
    try {
      const raw = await post<unknown>(SESSIONS, {
        harness: tool.id,
        folder,
        ...(account ? { account } : {}),
        ...(mode.id ? { mode: mode.id } : {}),
        ...(mode.confirm ? { confirm: true } : {}),
      });
      const s = decodeCodeSession(raw);
      if (!s) throw new Error("The session could not be made.");
      onCreated(s);
    } catch (e) {
      setError(errorWords(e, "The session could not be made."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div data-testid="code-new-session" className="h-full overflow-y-auto px-4 py-4">
      <div className={`${HARNESS_COLUMN} space-y-5`}>
        <div className="flex items-center gap-2">
          <h2 className="flex-1 text-[15px] text-zinc-100">New session</h2>
          <button type="button" onClick={onCancel} aria-label="Close" className="rounded-md p-1 text-zinc-500 hover:bg-white/[0.06] hover:text-zinc-200">
            <X size={15} />
          </button>
        </div>

        <fieldset className="space-y-1.5">
          <legend className="mb-1 text-[13px] text-zinc-400">Coding tool</legend>
          {toolsError ? (
            <p role="alert" className="text-[13px] text-tone-danger">
              {toolsError}
            </p>
          ) : !tools ? (
            <p className="text-[13px] text-zinc-500">Looking for Claude Code and Codex on this PC…</p>
          ) : (
            tools.map((t) => {
              const why = toolUnavailable(t);
              return (
                <label
                  key={t.id}
                  data-testid={`code-new-tool-${t.id}`}
                  data-available={why ? "false" : "true"}
                  className={`flex items-start gap-2 rounded-lg px-2 py-1.5 text-[13px] ${why ? "opacity-50" : "cursor-pointer hover:bg-white/[0.04]"}`}
                >
                  <input
                    type="radio"
                    name="code-tool"
                    value={t.id}
                    checked={toolId === t.id}
                    disabled={!!why}
                    onChange={() => setToolId(t.id)}
                    className="mt-0.5"
                  />
                  <span className="min-w-0">
                    <span className="block text-zinc-200">
                      {t.label}
                      {t.version ? <span className="ml-1.5 text-[12px] text-zinc-500">{t.version}</span> : null}
                    </span>
                    {why ? (
                      <span data-testid={`code-new-tool-why-${t.id}`} className="block text-[12px] text-zinc-500">
                        {why}
                      </span>
                    ) : null}
                  </span>
                </label>
              );
            })
          )}
        </fieldset>

        <div className="space-y-1.5">
          <p className="text-[13px] text-zinc-400">
            Folder:{" "}
            <span data-testid="code-new-folder" className="text-zinc-200">
              {folder ?? "pick one below"}
            </span>
          </p>
          <div className="max-h-72 overflow-y-auto rounded-xl bg-ink-900/40 p-1">
            <DirectoryTree variant="calm" hideAction showProjects selectedPath={folder} onSelect={setFolder} />
          </div>
        </div>

        {tool ? (
          <div className="flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-1.5 text-[13px] text-zinc-400">
              Account
              <select
                data-testid="code-new-account"
                value={account}
                onChange={(e) => setAccount(e.target.value)}
                className={CHIP_SELECT}
              >
                <option value="">This PC&apos;s sign-in</option>
                {tool.accounts.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                    {a.status && a.status !== "ready" ? ` (${a.status})` : ""}
                  </option>
                ))}
              </select>
            </label>
            <span className="flex items-center gap-1.5 text-[13px] text-zinc-400">
              Mode
              <ModeChip
                tool={tool}
                value={mode.id}
                onChange={(id, confirm) => setMode({ id, confirm })}
                testId="code-new-mode"
              />
            </span>
          </div>
        ) : null}

        {error ? (
          <p role="alert" data-testid="code-new-error" className="text-[13px] text-tone-danger">
            {error}
          </p>
        ) : null}
        <button
          type="button"
          data-testid="code-new-start"
          disabled={busy || !tool || !folder || !!(tool && toolUnavailable(tool))}
          onClick={() => void start()}
          className="inline-flex h-9 items-center gap-2 rounded-lg bg-accent px-4 text-[13px] font-medium text-ink-950 disabled:opacity-40"
        >
          {busy ? <Loader2 size={14} className="animate-spin" /> : <Code2 size={14} aria-hidden="true" />}
          Start session
        </button>
      </div>
    </div>
  );
}

// ── the view ────────────────────────────────────────────────────────────────

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
        setListError(null);
      })
      .catch((e) => setListError(errorWords(e, "The sessions could not be read.")));
  }, []);
  useEffect(() => {
    readList();
    get<unknown>(TOOLS)
      .then((raw) => setTools(decodeCodingTools(raw)))
      .catch((e) => setToolsError(errorWords(e, "Claude Code and Codex could not be looked for.")));
  }, [readList]);
  useVisibleInterval(readList, CODE_LIST_REREAD_MS);

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

  const archived = useCallback(
    (id: string) => {
      setSessions((prev) => (prev ? prev.filter((s) => s.id !== id) : prev));
      if (openId === id) open(null);
    },
    [openId, open],
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

  const groups = useMemo(() => groupByFolder(sessions ?? []), [sessions]);

  return (
    <div
      data-testid="build-code-view"
      className="flex flex-col gap-4 lg:flex-row"
    >
      <nav
        aria-label="Sessions"
        data-testid="code-session-list"
        className="flex w-full max-h-64 shrink-0 flex-col rounded-2xl bg-ink-900/40 lg:w-64 lg:max-h-none lg:h-[calc(100vh-12rem-var(--ij-strip-h,0px))] lg:min-h-[480px]"
      >
        <div className="flex shrink-0 items-center gap-2 px-3 pb-1 pt-3">
          <span className="flex-1 text-[13px] text-zinc-400">Sessions</span>
          <button
            type="button"
            data-testid="code-new"
            onClick={() => {
              setCreating(true);
              setRowMenu(null);
            }}
            className={QUIET_BTN}
          >
            <Plus size={13} aria-hidden="true" />
            New session
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-1.5 pb-2">
          {listError && !sessions ? (
            <p role="alert" className="px-2 py-2 text-[12px] text-tone-danger">
              {listError}
            </p>
          ) : !sessions ? (
            <p className="px-2 py-2 text-[12px] text-zinc-500">Reading your sessions…</p>
          ) : groups.length === 0 ? (
            <p data-testid="code-list-empty" className="px-2 py-2 text-[12px] text-zinc-500">
              No sessions yet. Press New session to start Claude Code or Codex in a folder.
            </p>
          ) : (
            groups.map((g) => (
              <section key={g.folder} data-testid="code-folder-group" className="mt-2">
                <h3 className="flex items-center gap-1.5 px-2 text-[12px] text-zinc-500" title={g.folder}>
                  <FolderOpen size={12} aria-hidden="true" />
                  <span className="truncate">{g.name}</span>
                </h3>
                <ul className="mt-0.5">
                  {g.sessions.map((s) => (
                    <li key={s.id} className="group relative">
                      <button
                        type="button"
                        data-testid={`code-row-${s.id}`}
                        data-source={s.source || undefined}
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
                  ))}
                </ul>
              </section>
            ))
          )}
          {rowNote ? (
            <p role="alert" data-testid="code-row-note" className="px-2 pt-2 text-[12px] text-tone-danger">
              {rowNote}
            </p>
          ) : null}
        </div>
      </nav>

      <section
        aria-label="Session"
        data-testid="code-stage"
        className="min-w-0 flex-1 overflow-hidden rounded-2xl border border-white/[0.06] bg-ink-900/40"
        style={{ height: "calc(100vh - 12rem - var(--ij-strip-h, 0px))", minHeight: 480 }}
      >
        {creating ? (
          <NewSession
            tools={tools}
            toolsError={toolsError}
            startFolder={startFolder}
            onCancel={() => setCreating(false)}
            onCreated={(s) => {
              noteChanged(s);
              open(s.id);
            }}
          />
        ) : openId ? (
          <SessionPane
            key={openId}
            sessionId={openId}
            tools={tools ?? []}
            onChanged={noteChanged}
            onOpenedTerminal={onOpenedTerminal}
            onArchived={archived}
          />
        ) : (
          <div className="grid h-full place-items-center px-6 text-center">
            <div className="max-w-sm space-y-3">
              <Code2 size={24} className="mx-auto text-zinc-600" aria-hidden="true" />
              <p className="text-[14px] text-zinc-200">Claude Code or Codex, in a window</p>
              <p className="text-[13px] text-zinc-500">
                The real coding tool runs here with its own settings and its own saved conversations. Iron Jarvis
                shows what it does and asks you whenever it asks.
              </p>
              <button type="button" data-testid="code-new-empty" onClick={() => setCreating(true)} className={QUIET_BTN}>
                <Plus size={13} aria-hidden="true" />
                New session
              </button>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}

export default BuildCodeView;
