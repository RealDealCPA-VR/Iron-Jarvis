"use client";

// v1.518.0 — "Other tools": the owner's chats in Claude Code and Codex, one
// quiet row at the foot of a list.
//
// CALM BY DESIGN. Closed, it is one line per tool: its mark, its name, and
// "3 new" when chats were written since the owner last looked. With nothing to
// show the part is not drawn at all. The list is read once, three seconds
// after the part appears (never part of a page's first paint).
//
// v1.528.0 — CLAUDE CHAT AND THE CONNECTOR. Claude desktop's Chat tab keeps
// its chats on claude.ai, so they reach Iron Jarvis only once the owner
// CONNECTS Claude desktop: one press adds Iron Jarvis to Claude's own tools,
// and Claude then records each chat. Those rows (and Claude's Cowork
// sessions) are carried on in Claude: a press shows what is known (Claude's
// own summary, a link back) instead of opening anything. Connect and
// Disconnect each say what they do first and need a second press; an app that
// is not on this PC is not offered. Nothing else changes another app.
//
// v1.530.0 — TWO PLACES, BY WHAT THE ROWS ARE (the owner: "the other tools are
// more applicable on Chat › Code … if the user is using the IJ harness while
// using other harnesses in the background, they can monitor it").
//
//  * mode "chat" (the chat list): Claude Chat only — conversations.
//  * mode "code" (Chat › Code's session list, beside the IJ harness): Claude
//    Code and Codex — coding sessions to WATCH. A session its tool wrote to a
//    moment ago is "working", with its newest step under its title; while one
//    is working the list is read again every 15 s, and nothing is read while
//    none is. Pressing a session opens its watch view (its newest steps, read
//    again every 4 s while it works). "Continue here" (a coding session on that
//    tool in Build › Code) is offered at once only for a session that has been
//    quiet for 10 minutes; for one written to since, the owner must say it is
//    closed in its own app first, because two programs writing one
//    conversation garble it (the daemon refuses it without that word too).
//
// The words follow lib/otherTools; theme tokens only, the sidebar's own ghost
// rows (SidebarChats' "Archived chats" is the pattern).

import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronDown, ChevronRight, ExternalLink, Plug, Terminal } from "lucide-react";

import { get, post } from "@/lib/api";
import { brandIcon } from "@/components/BrandGlyph";
import { failureOf } from "@/lib/loadFailed";
import { formatAge } from "@/lib/threadStatus";
import {
  APP_TOOL,
  CONNECT_WORDS,
  DISCONNECT_WORDS,
  FIRST_READ_MS,
  FOLDER_LIMIT,
  LIVE_ELSEWHERE_S,
  MODE_APPS,
  MODE_TOOLS,
  OPEN_SAVED_PATH,
  OTHER_TOOLS_PATH,
  STALE_MS,
  TOOL_LABEL,
  WATCH_REREAD_MS,
  WORKING_REREAD_MS,
  anyWorking,
  appConnectPath,
  appDisconnectPath,
  appForTool,
  decodeOtherApps,
  decodeOtherTools,
  decodeWatch,
  madeSessionId,
  newCount,
  newestOf,
  openSavedBody,
  quietWords,
  readFolded,
  readSeen,
  stepsPath,
  withFirstSight,
  withoutChat,
  writeFolded,
  writeSeen,
  type OtherApp,
  type OtherChat,
  type OtherTool,
  type OtherToolsMode,
  type Seen,
  type Watch,
} from "@/lib/otherTools";

/** Where a coding session on a real tool opens (lib/buildApp.buildCodeHref;
 *  spelled here so the sidebar does not load that module; a test ties them). */
export function codeSessionHref(id: string): string {
  return `/terminals?view=code&code=${encodeURIComponent(id)}`;
}

export const OPEN_FAILED = "That chat could not be opened.";
export const CHANGE_FAILED = "That did not work. Nothing was changed.";
export const WATCH_FAILED = "Its steps could not be read just now.";
export const WAITING_WORDS: Record<string, string> = {
  "claude-chat":
    "Connected. Restart Claude desktop if you have not. Each chat appears here when Claude records it.",
  codex: "Connected. New Codex chats add a summary here.",
  "claude-code": "Connected. New Claude Code sessions add a summary here.",
};

const ROW =
  "flex h-7 w-full items-center gap-1.5 rounded-[10px] px-2.5 text-left text-[12px] text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-300 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60";
const SMALL_BUTTON =
  "rounded-md px-2 py-0.5 text-[11px] transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60 disabled:opacity-60";

/** The tool's own mark where the app ships one, else a terminal. */
function ToolMark({ tool }: { tool: string }) {
  const brand = tool === "claude-code" || tool === "claude-chat" ? brandIcon("claude-cli") : undefined;
  if (!brand) return <Terminal size={12} aria-hidden="true" className="shrink-0" />;
  return (
    <svg viewBox="0 0 24 24" width={12} height={12} aria-hidden="true" className="shrink-0 fill-current">
      <path d={brand.path} />
    </svg>
  );
}

/** Leave for another page without the app router (the chat page mounts this
 *  part and is drawn in places that have none: a pop-out window). */
function leaveFor(href: string): void {
  window.location.assign(href);
}

/** The lines to draw: every tool with chats, and a connected app's tool that
 *  has none yet (so the owner sees the connection took). */
function linesOf(tools: OtherTool[], apps: OtherApp[]): OtherTool[] {
  const out = [...tools];
  for (const a of apps) {
    const tool = APP_TOOL[a.app];
    if (!a.connected || !tool || out.some((t) => t.tool === tool)) continue;
    out.push({ tool, label: TOOL_LABEL[tool] ?? a.label, total: 0, openable: tool !== "claude-chat", folders: [] });
  }
  return out;
}

type Confirm = { app: string; connect: boolean } | null;

export default function OtherToolChats({
  onNavigate,
  go = leaveFor,
  mode = "chat",
  className = "mt-1 min-w-0",
}: {
  onNavigate?: () => void;
  /** How to go to the opened session: the sidebar passes its router's push. */
  go?: (href: string) => void;
  /** v1.530.0: the chat list ("chat") or Chat › Code's session list ("code"). */
  mode?: OtherToolsMode;
  /** The part's frame (Chat › Code gives it a border of its own). */
  className?: string;
}) {
  const [tools, setTools] = useState<OtherTool[] | null>(null);
  const [apps, setApps] = useState<OtherApp[]>([]);
  const [seen, setSeen] = useState<Seen>({});
  const [openTool, setOpenTool] = useState<string | null>(null);
  const [more, setMore] = useState<Set<string>>(() => new Set());
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<{ id: string; words: string } | null>(null);
  const [shownChat, setShownChat] = useState<string | null>(null);
  const [watch, setWatch] = useState<{ id: string; got: Watch | null; failed: boolean } | null>(null);
  const [confirm, setConfirm] = useState<Confirm>(null);
  const [appNote, setAppNote] = useState<{ app: string; words: string } | null>(null);
  // v1.531.0: the whole part folds to its heading, remembered per place.
  const [folded, setFolded] = useState(false);
  const readAt = useRef(0);
  const alive = useRef(true);

  const read = useCallback(async () => {
    try {
      const raw = await get<unknown>(OTHER_TOOLS_PATH);
      const wanted = MODE_TOOLS[mode];
      const got = decodeOtherTools(raw).filter((t) => wanted.includes(t.tool));
      if (!alive.current) return;
      readAt.current = Date.now();
      setTools(got);
      setApps(decodeOtherApps(raw).filter((a) => MODE_APPS[mode].includes(a.app)));
      // The first sight of a tool marks everything in it as seen.
      const before = readSeen();
      const after = withFirstSight(got, before);
      if (after !== before) writeSeen(after);
      setSeen(after);
    } catch {
      // A list that could not be read draws nothing: the part is an extra,
      // and the list above it is never held up or replaced by it.
    }
  }, [mode]);

  useEffect(() => {
    setFolded(readFolded(mode));
  }, [mode]);

  useEffect(() => {
    alive.current = true;
    const timer = setTimeout(() => void read(), FIRST_READ_MS);
    return () => {
      alive.current = false;
      clearTimeout(timer);
    };
  }, [read]);

  // Chat › Code: while a session works, read the list again (its dot and
  // its newest step move); while none does, nothing is read.
  const working = mode === "code" && tools !== null && anyWorking(tools);
  useEffect(() => {
    if (!working) return;
    const timer = setTimeout(() => void read(), WORKING_REREAD_MS);
    return () => clearTimeout(timer);
  }, [working, tools, read]);

  const readSteps = useCallback(async (tool: string, id: string) => {
    try {
      const got = decodeWatch(await get<unknown>(stepsPath(tool, id)));
      if (!alive.current) return;
      setWatch((prev) => (prev && prev.id === id ? { id, got, failed: got === null } : prev));
    } catch {
      if (alive.current) setWatch((prev) => (prev && prev.id === id ? { ...prev, failed: true } : prev));
    }
  }, []);

  // The watch view reads its steps again while it is open and working.
  const watchedWorking = watch?.got?.working === true;
  useEffect(() => {
    if (!watch || !watchedWorking || !openTool) return;
    const id = watch.id;
    const timer = setTimeout(() => void readSteps(openTool, id), WATCH_REREAD_MS);
    return () => clearTimeout(timer);
  }, [watch, watchedWorking, openTool, readSteps]);

  const toggle = useCallback(
    (tool: OtherTool) => {
      setNote(null);
      setShownChat(null);
      setWatch(null);
      if (openTool === tool.tool) {
        setOpenTool(null);
        return;
      }
      setOpenTool(tool.tool);
      // Looked at: nothing in it is "new" any more.
      const newest = newestOf(tool);
      if (newest) {
        const next = { ...readSeen(), [tool.tool]: newest };
        writeSeen(next);
        setSeen(next);
      }
      if (Date.now() - readAt.current > STALE_MS) void read();
    },
    [openTool, read],
  );

  const toggleWatch = useCallback(
    (tool: string, id: string) => {
      setNote(null);
      if (watch?.id === id) {
        setWatch(null);
        return;
      }
      setWatch({ id, got: null, failed: false });
      void readSteps(tool, id);
    },
    [watch, readSteps],
  );

  const open = useCallback(
    async (tool: string, folder: string, id: string, closedThere = false) => {
      if (busy) return;
      setBusy(id);
      setNote(null);
      try {
        const made = madeSessionId(await post<unknown>(OPEN_SAVED_PATH, openSavedBody(tool, folder, id, closedThere)));
        if (!made) throw new Error(OPEN_FAILED);
        if (!alive.current) return;
        setTools((prev) => (prev ? withoutChat(prev, tool, id) : prev));
        onNavigate?.();
        go(codeSessionHref(made));
      } catch (e) {
        if (alive.current) setNote({ id, words: failureOf(e).message || OPEN_FAILED });
      } finally {
        if (alive.current) setBusy(null);
      }
    },
    [busy, go, onNavigate],
  );

  const change = useCallback(
    async (app: string, connect: boolean) => {
      if (busy) return;
      setBusy(`app:${app}`);
      setAppNote(null);
      try {
        const got = await post<unknown>(connect ? appConnectPath(app) : appDisconnectPath(app));
        const words = got && typeof got === "object" ? (got as { words?: unknown }).words : "";
        if (!alive.current) return;
        setAppNote({ app, words: typeof words === "string" && words ? words : CHANGE_FAILED });
        setConfirm(null);
        await read();
      } catch (e) {
        if (alive.current) setAppNote({ app, words: failureOf(e).message || CHANGE_FAILED });
      } finally {
        if (alive.current) setBusy(null);
      }
    },
    [busy, read],
  );

  if (tools === null) return null;
  const lines = linesOf(tools, apps);
  const offers = apps.filter((a) => a.installed && (!a.connected || a.entryMissing));
  if (lines.length === 0 && offers.length === 0 && !appNote) return null;
  const shown = lines.find((t) => t.tool === openTool) ?? null;

  const confirmBlock = (app: OtherApp, connect: boolean) => (
    <div data-testid={`other-app-confirm-${app.app}`} className="mx-2.5 mb-1.5 mt-0.5 rounded-lg bg-white/[0.04] p-2">
      <p className="text-[11px] leading-relaxed text-zinc-400">
        {connect ? CONNECT_WORDS[app.app] : `Disconnect ${app.label}? ${DISCONNECT_WORDS}`}
      </p>
      <div className="mt-1.5 flex gap-1.5">
        <button
          type="button"
          data-testid={`other-app-${connect ? "connect" : "disconnect"}-yes-${app.app}`}
          disabled={busy !== null}
          onClick={() => void change(app.app, connect)}
          className={`${SMALL_BUTTON} bg-accent/20 text-zinc-100 hover:bg-accent/30`}
        >
          {busy === `app:${app.app}` ? "Working…" : connect ? "Connect" : "Disconnect"}
        </button>
        <button
          type="button"
          data-testid={`other-app-cancel-${app.app}`}
          onClick={() => setConfirm(null)}
          className={`${SMALL_BUTTON} text-zinc-400 hover:bg-white/[0.06]`}
        >
          {connect ? "Not now" : "Keep"}
        </button>
      </div>
    </div>
  );

  const appNoteLine = (app: string) =>
    appNote?.app === app ? (
      <p role="status" data-testid={`other-app-note-${app}`} className="px-2.5 pb-1 text-[11px] leading-relaxed text-zinc-500">
        {appNote.words}
      </p>
    ) : null;

  const noteLine = (c: OtherChat) =>
    note?.id === c.id ? (
      <p role="status" data-testid="other-chat-note" className="px-2.5 pb-1 text-[11px] leading-relaxed text-zinc-500">
        {note.words}
      </p>
    ) : null;

  /** A coding session to watch (Chat › Code). */
  const codeRow = (t: OtherTool, c: OtherChat) => {
    const isShown = watch?.id === c.id;
    const live = watch?.id === c.id && watch.got ? watch.got : null;
    const quiet = live ? live.quietS : c.quietS;
    const stillInUse = quiet >= 0 && quiet < LIVE_ELSEWHERE_S;
    return (
      <div key={c.id} className="min-w-0">
        <button
          type="button"
          data-testid={`other-chat-${c.id}`}
          aria-expanded={isShown}
          title={`${c.title}${c.summary ? `\n${c.summary}` : ""}`}
          onClick={() => toggleWatch(t.tool, c.id)}
          className={`${ROW} h-auto min-h-7 py-1 text-zinc-400`}
        >
          {c.working ? (
            <span data-testid={`other-chat-working-${c.id}`} className="h-1.5 w-1.5 shrink-0 rounded-full bg-accent" aria-hidden="true" />
          ) : null}
          <span className="min-w-0 flex-1">
            <span className="block truncate">{c.title}</span>
            {c.working && c.doing ? (
              <span data-testid={`other-chat-doing-${c.id}`} className="block truncate text-[11px] text-zinc-600">
                {c.doing}
              </span>
            ) : null}
          </span>
          <span className={`shrink-0 text-[11px] ${c.working ? "text-accent-soft" : "text-zinc-600"}`}>
            {c.working ? "working" : formatAge(c.ended)}
          </span>
        </button>
        {isShown ? (
          <div data-testid={`other-chat-watch-${c.id}`} className="mx-2.5 mb-1.5 rounded-lg bg-white/[0.03] p-2 text-[11px] leading-relaxed">
            {watch?.failed ? (
              <p className="text-zinc-500">{WATCH_FAILED}</p>
            ) : !live ? (
              <p className="text-zinc-600">Reading its steps…</p>
            ) : live.steps.length === 0 ? (
              <p className="text-zinc-600">No steps to show yet.</p>
            ) : (
              <ol className="space-y-0.5">
                {live.steps.slice(-8).map((s, i) => (
                  <li key={`${s.at}-${i}`} className={`truncate ${s.kind === "said" ? "text-zinc-400" : "text-zinc-500"}`} title={s.words}>
                    {s.words}
                  </li>
                ))}
              </ol>
            )}
            {c.summary ? <p className="mt-1 whitespace-pre-line text-zinc-400">{c.summary}</p> : null}
            <div className="mt-1.5">
              {stillInUse ? (
                <>
                  <p data-testid={`other-chat-in-use-${c.id}`} className="text-zinc-500">
                    {t.label} wrote to it {quietWords(quiet)} ago, so it may still be open there. Continue it here only
                    once it is closed in {t.label}.
                  </p>
                  <button
                    type="button"
                    data-testid={`other-chat-closed-there-${c.id}`}
                    disabled={busy !== null}
                    onClick={() => void open(t.tool, c.folder, c.id, true)}
                    className={`${SMALL_BUTTON} mt-1 text-zinc-300 hover:bg-white/[0.06]`}
                  >
                    {busy === c.id ? "Opening…" : `It's closed in ${t.label}, continue here`}
                  </button>
                </>
              ) : (
                <button
                  type="button"
                  data-testid={`other-chat-continue-${c.id}`}
                  disabled={busy !== null}
                  onClick={() => void open(t.tool, c.folder, c.id)}
                  className={`${SMALL_BUTTON} text-zinc-300 hover:bg-white/[0.06]`}
                >
                  {busy === c.id ? "Opening…" : "Continue here"}
                </button>
              )}
            </div>
          </div>
        ) : null}
        {noteLine(c)}
      </div>
    );
  };

  /** A Claude Chat conversation (the chat list): read-only. */
  const chatRow = (t: OtherTool, c: OtherChat) => {
    if (t.openable) return codeRow(t, c);
    const isShown = shownChat === c.id;
    return (
      <div key={c.id} className="min-w-0">
        <button
          type="button"
          data-testid={`other-chat-${c.id}`}
          aria-expanded={isShown}
          title={c.title}
          onClick={() => setShownChat(isShown ? null : c.id)}
          className={`${ROW} text-zinc-400`}
        >
          <span className="min-w-0 flex-1 truncate">{c.title}</span>
          <span className="shrink-0 text-[11px] text-zinc-600">{formatAge(c.ended)}</span>
        </button>
        {isShown ? (
          <div data-testid={`other-chat-detail-${c.id}`} className="px-2.5 pb-1.5 text-[11px] leading-relaxed text-zinc-500">
            {c.summary ? (
              <p className="whitespace-pre-line text-zinc-400">{c.summary}</p>
            ) : (
              <p>{c.id.startsWith("c:") ? "A Cowork session in Claude desktop." : "Claude recorded no summary."}</p>
            )}
            <p className="mt-0.5">Carry it on in Claude desktop.</p>
            {c.link ? (
              <a
                href={c.link}
                target="_blank"
                rel="noopener noreferrer"
                data-testid={`other-chat-link-${c.id}`}
                className="mt-0.5 inline-flex items-center gap-1 text-accent-soft hover:underline"
              >
                <ExternalLink size={11} aria-hidden="true" />
                Open in Claude
              </a>
            ) : null}
          </div>
        ) : null}
      </div>
    );
  };

  const workingCount = (t: OtherTool) => t.folders.reduce((n, f) => n + f.chats.filter((c) => c.working).length, 0);

  const workingAll = mode === "code" ? lines.reduce((n, t) => n + workingCount(t), 0) : 0;
  const freshAll = lines.reduce((n, t) => n + newCount(t, seen), 0);
  const foldedNote = workingAll > 0 ? `${workingAll} working` : freshAll > 0 ? `${freshAll} new` : "";
  const fold = () => {
    const next = !folded;
    setFolded(next);
    writeFolded(mode, next);
  };

  return (
    <div data-testid={`other-tools${mode === "code" ? "-code" : ""}`} className={className}>
      <button
        type="button"
        data-testid="other-tools-fold"
        aria-expanded={!folded}
        title={folded ? "Show your other tools" : "Fold your other tools away"}
        onClick={fold}
        className="flex w-full min-w-0 items-center gap-1 rounded-md px-2.5 pb-0.5 pt-1 text-left text-[11px] text-zinc-600 hover:text-zinc-400"
      >
        <span className="min-w-0 flex-1 truncate">Other tools</span>
        {folded && foldedNote ? (
          <span data-testid="other-tools-folded-note" className="shrink-0 text-accent-soft/90">
            {foldedNote}
          </span>
        ) : null}
        {folded ? (
          <ChevronRight size={11} aria-hidden="true" className="shrink-0" />
        ) : (
          <ChevronDown size={11} aria-hidden="true" className="shrink-0" />
        )}
      </button>
      {folded ? null : (
      <>
      {lines.map((t) => {
        const fresh = newCount(t, seen);
        const busyNow = mode === "code" ? workingCount(t) : 0;
        const isOpen = shown?.tool === t.tool;
        const app = appForTool(apps, t.tool);
        return (
          <div key={t.tool} className="min-w-0">
            <button
              type="button"
              data-testid={`other-tool-${t.tool}`}
              aria-expanded={isOpen}
              onClick={() => toggle(t)}
              className={ROW}
            >
              <ToolMark tool={t.tool} />
              <span className="min-w-0 flex-1 truncate">{t.label}</span>
              {busyNow > 0 && !isOpen ? (
                <span data-testid={`other-tool-working-${t.tool}`} className="shrink-0 text-[11px] text-accent-soft">
                  {busyNow} working
                </span>
              ) : fresh > 0 && !isOpen ? (
                <span data-testid={`other-tool-new-${t.tool}`} className="shrink-0 text-[11px] text-accent-soft/90">
                  {fresh} new
                </span>
              ) : null}
              {isOpen ? (
                <ChevronDown size={12} aria-hidden="true" className="shrink-0" />
              ) : (
                <ChevronRight size={12} aria-hidden="true" className="shrink-0" />
              )}
            </button>
            {isOpen ? (
              <div data-testid={`other-tool-list-${t.tool}`} className="mb-1 min-w-0">
                {t.folders.length === 0 ? (
                  <p data-testid={`other-tool-waiting-${t.tool}`} className="px-2.5 py-1 text-[11px] leading-relaxed text-zinc-500">
                    {WAITING_WORDS[t.tool] ?? "Connected."}
                  </p>
                ) : null}
                {t.folders.map((f) => {
                  const key = `${t.tool}\n${f.folder}`;
                  const all = more.has(key);
                  const chats = all ? f.chats : f.chats.slice(0, FOLDER_LIMIT);
                  return (
                    <div key={f.folder || "none"} className="min-w-0">
                      <p title={f.folder} className="truncate px-2.5 pb-0.5 pt-1.5 text-[11px] text-zinc-600">
                        {f.name}
                      </p>
                      {chats.map((c) => chatRow(t, c))}
                      {!all && f.chats.length > FOLDER_LIMIT ? (
                        <button
                          type="button"
                          data-testid="other-folder-more"
                          onClick={() => setMore((prev) => new Set(prev).add(key))}
                          className={`${ROW} text-[11px]`}
                        >
                          Show {f.chats.length - FOLDER_LIMIT} more
                        </button>
                      ) : null}
                    </div>
                  );
                })}
                {app && app.connected ? (
                  <>
                    <div className="flex items-center gap-1.5 px-2.5 pt-1 text-[11px] text-zinc-600">
                      <span data-testid={`other-app-connected-${app.app}`} className="min-w-0 flex-1 truncate">
                        Connected{app.count ? ` · ${app.count} recorded` : ""}
                      </span>
                      <button
                        type="button"
                        data-testid={`other-app-disconnect-${app.app}`}
                        onClick={() => setConfirm({ app: app.app, connect: false })}
                        className={`${SMALL_BUTTON} text-zinc-500 hover:bg-white/[0.06] hover:text-zinc-300`}
                      >
                        Disconnect
                      </button>
                    </div>
                    {confirm?.app === app.app && !confirm.connect ? confirmBlock(app, false) : null}
                  </>
                ) : null}
                {app ? appNoteLine(app.app) : null}
              </div>
            ) : null}
          </div>
        );
      })}
      {offers.map((a) => (
        <div key={a.app} className="min-w-0">
          <button
            type="button"
            data-testid={`other-app-connect-${a.app}`}
            onClick={() => setConfirm(confirm?.app === a.app && confirm.connect ? null : { app: a.app, connect: true })}
            className={`${ROW} text-[11px]`}
          >
            <Plug size={12} aria-hidden="true" className="shrink-0" />
            <span className="min-w-0 flex-1 truncate">
              {a.entryMissing ? `Connect ${a.label} again` : `Connect ${a.label}`}
            </span>
          </button>
          {confirm?.app === a.app && confirm.connect ? confirmBlock(a, true) : null}
          {!lines.some((t) => t.tool === APP_TOOL[a.app] && t.tool === openTool) ? appNoteLine(a.app) : null}
        </div>
      ))}
      {appNote && !offers.some((a) => a.app === appNote.app) && !lines.some((t) => appForTool(apps, t.tool)?.app === appNote.app && t.tool === openTool)
        ? appNoteLine(appNote.app)
        : null}
      </>
      )}
    </div>
  );
}
