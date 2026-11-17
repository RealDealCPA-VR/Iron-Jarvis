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
// The words follow lib/otherTools; theme tokens only, the sidebar's own ghost
// rows (SidebarChats' "Archived chats" is the pattern).

import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronDown, ChevronRight, Terminal } from "lucide-react";

import { get, post } from "@/lib/api";
import { brandIcon } from "@/components/BrandGlyph";
import { failureOf } from "@/lib/loadFailed";
import { formatAge } from "@/lib/threadStatus";
import {
  FIRST_READ_MS,
  FOLDER_LIMIT,
  OPEN_SAVED_PATH,
  OTHER_TOOLS_PATH,
  STALE_MS,
  decodeOtherTools,
  madeSessionId,
  newCount,
  newestOf,
  openSavedBody,
  readSeen,
  withFirstSight,
  withoutChat,
  writeSeen,
  type OtherTool,
  type Seen,
} from "@/lib/otherTools";

/** Where a coding session on a real tool opens (lib/buildApp.buildCodeHref;
 *  spelled here so the sidebar does not load that module; a test ties them). */
export function codeSessionHref(id: string): string {
  return `/terminals?view=code&code=${encodeURIComponent(id)}`;
}

export const OPEN_FAILED = "That chat could not be opened.";

const ROW =
  "flex h-7 w-full items-center gap-1.5 rounded-[10px] px-2.5 text-left text-[12px] text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-300 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60";

/** The tool's own mark where the app ships one, else a terminal. */
function ToolMark({ tool }: { tool: string }) {
  const brand = tool === "claude-code" ? brandIcon("claude-cli") : undefined;
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

export default function OtherToolChats({
  onNavigate,
  go = leaveFor,
}: {
  onNavigate?: () => void;
  /** How to go to the opened session: the sidebar passes its router's push. */
  go?: (href: string) => void;
}) {
  const [tools, setTools] = useState<OtherTool[] | null>(null);
  const [seen, setSeen] = useState<Seen>({});
  const [openTool, setOpenTool] = useState<string | null>(null);
  const [more, setMore] = useState<Set<string>>(() => new Set());
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<{ id: string; words: string } | null>(null);
  const readAt = useRef(0);
  const alive = useRef(true);

  const read = useCallback(async () => {
    try {
      const got = decodeOtherTools(await get<unknown>(OTHER_TOOLS_PATH));
      if (!alive.current) return;
      readAt.current = Date.now();
      setTools(got);
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

  if (!tools || tools.length === 0) return null;
  const shown = tools.find((t) => t.tool === openTool) ?? null;
  return (
    <div data-testid="other-tools" className="mt-1 min-w-0">
      <p className="px-2.5 pb-0.5 pt-1 text-[11px] text-zinc-600">Other tools</p>
      {tools.map((t) => {
        const fresh = newCount(t, seen);
        const isOpen = shown?.tool === t.tool;
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
                {t.folders.map((f) => {
                  const key = `${t.tool}\n${f.folder}`;
                  const all = more.has(key);
                  const chats = all ? f.chats : f.chats.slice(0, FOLDER_LIMIT);
                  return (
                    <div key={f.folder} className="min-w-0">
                      <p title={f.folder} className="truncate px-2.5 pb-0.5 pt-1.5 text-[11px] text-zinc-600">
                        {f.name}
                      </p>
                      {chats.map((c) => (
                        <div key={c.id} className="min-w-0">
                          <button
                            type="button"
                            data-testid={`other-chat-${c.id}`}
                            disabled={busy !== null}
                            title={`${c.title}\nOpens as a coding session on ${t.label}.`}
                            onClick={() => void open(t.tool, f.folder, c.id)}
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
                      ))}
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
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
