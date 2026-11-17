"use client";

// v1.518.0 — "Other tools": the owner's chats in Claude Code and Codex, one
// quiet row at the foot of the chat list.
//
// CALM BY DESIGN. Closed, it is one line: a tool's mark, its name, and "3 new"
// when chats were written since the owner last looked. With no such chats on
// this PC the row is not drawn at all. The list is read once, three seconds
// after the sidebar appears (never part of a page's first paint), and again
// only when a tool is opened and the list is a minute old. Nothing polls.
//
// OPEN, a tool lists its chats by folder, five per folder and then "Show
// more". Pressing a chat opens it as a coding session on that tool (the
// request Build › Code's own "saved by …" rows make), so the next message
// continues it there; the chat then belongs to the coding sessions' list and
// leaves this one. A press the daemon refuses says why under the row.
//
// v1.528.0 — CLAUDE CHAT AND THE CONNECTOR. Claude desktop's Chat tab keeps
// its chats on claude.ai, so they reach this list only once the owner
// CONNECTS Claude desktop: one press here adds Iron Jarvis to Claude's own
// connectors, and Claude then records each chat. Those rows (and Claude's
// Cowork sessions) are carried on in Claude: a press shows what is known
// (Claude's own summary, a link back) instead of opening anything. Codex can
// be connected the same way; its chats were already here, and a connected
// Codex adds a summary to each. Connect and Disconnect each say what they do
// first and need a second press; an app that is not on this PC is not
// offered. Nothing else changes another app.
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
  CONNECT_WORDS,
  DISCONNECT_WORDS,
  FIRST_READ_MS,
  FOLDER_LIMIT,
  OPEN_SAVED_PATH,
  OTHER_TOOLS_PATH,
  STALE_MS,
  TOOL_LABEL,
  APP_TOOL,
  appConnectPath,
  appDisconnectPath,
  appForTool,
  decodeOtherApps,
  decodeOtherTools,
  madeSessionId,
  newCount,
  newestOf,
  openSavedBody,
  readSeen,
  withFirstSight,
  withoutChat,
  writeSeen,
  type OtherApp,
  type OtherChat,
  type OtherTool,
  type Seen,
} from "@/lib/otherTools";

/** Where a coding session on a real tool opens (lib/buildApp.buildCodeHref;
 *  spelled here so the sidebar does not load that module; a test ties them). */
export function codeSessionHref(id: string): string {
  return `/terminals?view=code&code=${encodeURIComponent(id)}`;
}

export const OPEN_FAILED = "That chat could not be opened.";
export const CHANGE_FAILED = "That did not work. Nothing was changed.";
export const WAITING_WORDS: Record<string, string> = {
  "claude-chat":
    "Connected. Restart Claude desktop if you have not. Each chat appears here when Claude records it.",
  codex: "Connected. New Codex chats add a summary here.",
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
 *  row and is drawn in places that have none: a pop-out window). */
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
}: {
  onNavigate?: () => void;
  /** How to go to the opened session: the sidebar passes its router's push. */
  go?: (href: string) => void;
}) {
  const [tools, setTools] = useState<OtherTool[] | null>(null);
  const [apps, setApps] = useState<OtherApp[]>([]);
  const [seen, setSeen] = useState<Seen>({});
  const [openTool, setOpenTool] = useState<string | null>(null);
  const [more, setMore] = useState<Set<string>>(() => new Set());
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<{ id: string; words: string } | null>(null);
  const [shownChat, setShownChat] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<Confirm>(null);
  const [appNote, setAppNote] = useState<{ app: string; words: string } | null>(null);
  const readAt = useRef(0);
  const alive = useRef(true);

  const read = useCallback(async () => {
    try {
      const raw = await get<unknown>(OTHER_TOOLS_PATH);
      const got = decodeOtherTools(raw);
      if (!alive.current) return;
      readAt.current = Date.now();
      setTools(got);
      setApps(decodeOtherApps(raw));
      // The first sight of a tool marks everything in it as seen.
      const before = readSeen();
      const after = withFirstSight(got, before);
      if (after !== before) writeSeen(after);
      setSeen(after);
    } catch {
      // A list that could not be read draws nothing: the row is an extra,
      // and the chat list above it is never held up or replaced by it.
    }
  }, []);

  useEffect(() => {
    alive.current = true;
    const timer = setTimeout(() => void read(), FIRST_READ_MS);
    return () => {
      alive.current = false;
      clearTimeout(timer);
    };
  }, [read]);

  const toggle = useCallback(
    (tool: OtherTool) => {
      setNote(null);
      setShownChat(null);
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

  const open = useCallback(
    async (tool: string, folder: string, id: string) => {
      if (busy) return;
      setBusy(id);
      setNote(null);
      try {
        const made = madeSessionId(await post<unknown>(OPEN_SAVED_PATH, openSavedBody(tool, folder, id)));
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

  const chatRow = (t: OtherTool, c: OtherChat) => {
    if (t.openable) {
      return (
        <div key={c.id} className="min-w-0">
          <button
            type="button"
            data-testid={`other-chat-${c.id}`}
            disabled={busy !== null}
            title={`${c.title}${c.summary ? `\n${c.summary}` : ""}\nOpens as a coding session on ${t.label}.`}
            onClick={() => void open(t.tool, c.folder, c.id)}
            className={`${ROW} text-zinc-400 disabled:cursor-wait disabled:opacity-60`}
          >
            <span className="min-w-0 flex-1 truncate">{c.title}</span>
            <span className="shrink-0 text-[11px] text-zinc-600">
              {busy === c.id ? "Opening…" : formatAge(c.ended)}
            </span>
          </button>
          {note?.id === c.id ? (
            <p role="status" data-testid="other-chat-note" className="px-2.5 pb-1 text-[11px] leading-relaxed text-zinc-500">
              {note.words}
            </p>
          ) : null}
        </div>
      );
    }
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

  return (
    <div data-testid="other-tools" className="mt-1 min-w-0">
      <p className="px-2.5 pb-0.5 pt-1 text-[11px] text-zinc-600">Other tools</p>
      {lines.map((t) => {
        const fresh = newCount(t, seen);
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
              {fresh > 0 && !isOpen ? (
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
    </div>
  );
}
