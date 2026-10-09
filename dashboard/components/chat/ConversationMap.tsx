"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import { ListTree, Search, X } from "lucide-react";

/**
 * The CONVERSATION MAP (v1.325.0; the idea is assistant-ui's thread outline,
 * MIT — no code taken). A long chat is hard to scroll back through; this
 * panel lists each question the user asked — its first line — with a quiet
 * note of how long the answer was, and jumps to it.
 *
 * - Hidden `continuation` turns (the Continue button's "carry on") are not
 *   questions and never show.
 * - A `steer` note (typed while a reply was being written) is shown INDENTED
 *   under the question it steered, marked "Note" — it is the user's words
 *   and the model read it, so it belongs on the map, but it is not a turn.
 * - Keyboard: ↑/↓ (and Home/End) move, Enter jumps, Esc closes. The search
 *   box appears when there are more than 8 questions and filters on the
 *   whole text of each question.
 */

export interface MapMessage {
  role: "user" | "assistant";
  content: string;
  continuation?: boolean;
  steer?: boolean;
  at?: string;
}

/** More than this many questions and the map grows a search box. */
export const MAP_SEARCH_AFTER = 8;
/** A question's line on the map, in characters, before "…". */
export const MAP_LINE_CHARS = 80;

export interface MapRow {
  /** The message index to jump to. */
  index: number;
  kind: "question" | "note";
  /** First readable line, ≤ MAP_LINE_CHARS. */
  line: string;
  /** The whole text (what the search box reads). */
  text: string;
  /** Words in the reply this question got (questions only); null = no reply yet. */
  replyWords: number | null;
  at?: string;
}

function words(s: string): number {
  const t = s.trim();
  return t ? t.split(/\s+/).length : 0;
}

/** The first line worth reading: skips blank lines and quoted lines ("> …"
 *  — a quote pasted from a reply is not the question), collapses spaces,
 *  caps at MAP_LINE_CHARS. Falls back to the first line when every line is
 *  quoted. */
export function firstLine(content: string): string {
  const lines = (content ?? "").replace(/\r\n?/g, "\n").split("\n").map((l) => l.trim());
  const pick = lines.find((l) => l && !l.startsWith(">")) ?? lines.find((l) => l) ?? "";
  const flat = pick.replace(/^>+\s*/, "").replace(/\s+/g, " ").trim();
  if (!flat) return "";
  return flat.length > MAP_LINE_CHARS ? `${flat.slice(0, MAP_LINE_CHARS - 1).trimEnd()}…` : flat;
}

/** The map's rows, in conversation order. Pure — exported for tests. */
export function mapRows(messages: MapMessage[]): MapRow[] {
  const rows: MapRow[] = [];
  let current: MapRow | null = null;
  messages.forEach((m, index) => {
    if (!m) return;
    if (m.role === "user") {
      if (m.continuation) return;
      if (m.steer) {
        rows.push({ index, kind: "note", line: firstLine(m.content), text: m.content ?? "", replyWords: null, at: m.at });
        return;
      }
      current = { index, kind: "question", line: firstLine(m.content), text: m.content ?? "", replyWords: null, at: m.at };
      rows.push(current);
      return;
    }
    if (m.role === "assistant" && current) {
      current.replyWords = (current.replyWords ?? 0) + words(m.content ?? "");
    }
  });
  return rows;
}

function replyNote(n: number | null): string {
  if (n === null) return "no reply yet";
  if (n === 0) return "empty reply";
  if (n < 40) return "short reply";
  if (n < 400) return `reply · ${n} words`;
  return `long reply · ${n.toLocaleString()} words`;
}

function timeOf(at?: string): string {
  if (!at) return "";
  const d = new Date(at);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

export function ConversationMap({
  messages,
  activeIndex,
  onJump,
  onClose,
}: {
  messages: MapMessage[];
  /** The message index on screen now — its question is marked and starts selected. */
  activeIndex?: number;
  onJump: (index: number) => void;
  onClose: () => void;
}) {
  const all = useMemo(() => mapRows(messages), [messages]);
  const questions = all.filter((r) => r.kind === "question").length;
  const searchable = questions > MAP_SEARCH_AFTER;
  const [query, setQuery] = useState("");
  const q = searchable ? query.trim().toLowerCase() : "";
  const rows = useMemo(
    () => (q ? all.filter((r) => r.text.toLowerCase().includes(q)) : all),
    [all, q],
  );

  // The question the reader is at: the last question at or before activeIndex.
  const activeRow = useMemo(() => {
    if (activeIndex === undefined) return -1;
    let found = -1;
    all.forEach((r) => {
      if (r.kind === "question" && r.index <= activeIndex) found = r.index;
    });
    return found;
  }, [all, activeIndex]);

  const [cursor, setCursor] = useState(() => {
    const i = rows.findIndex((r) => r.index === activeRow);
    return i >= 0 ? i : 0;
  });
  // A filter change can leave the cursor past the end.
  useEffect(() => {
    setCursor((c) => (rows.length === 0 ? 0 : Math.min(c, rows.length - 1)));
  }, [rows.length]);

  const listRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const baseId = useId();
  const optionId = (i: number) => `${baseId}-row-${i}`;

  // Open with the keyboard ready: in the search box when there is one, else
  // on the list itself.
  useEffect(() => {
    if (searchable) searchRef.current?.focus();
    else listRef.current?.focus();
    // Only on open.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Keep the selected row in view.
  useEffect(() => {
    const el = document.getElementById(optionId(cursor));
    el?.scrollIntoView?.({ block: "nearest" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cursor]);

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      onClose();
      return;
    }
    if (rows.length === 0) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setCursor((c) => Math.min(c + 1, rows.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setCursor((c) => Math.max(c - 1, 0));
    } else if (e.key === "Home" && e.target === listRef.current) {
      e.preventDefault();
      setCursor(0);
    } else if (e.key === "End" && e.target === listRef.current) {
      e.preventDefault();
      setCursor(rows.length - 1);
    } else if (e.key === "Enter") {
      e.preventDefault();
      e.stopPropagation();
      const row = rows[Math.min(cursor, rows.length - 1)];
      if (row) onJump(row.index);
    }
  }

  return (
    <div
      data-testid="conversation-map"
      role="dialog"
      aria-label="Conversation map"
      onKeyDown={onKeyDown}
      className="flex max-h-[min(70vh,560px)] w-full min-w-0 max-w-md flex-col rounded-xl border border-white/[0.06] bg-ink-950/95 shadow-xl backdrop-blur"
    >
      <div className="flex shrink-0 items-center gap-1.5 border-b border-white/[0.05] px-2.5 py-2">
        <ListTree size={12} className="shrink-0 text-accent-soft/80" aria-hidden="true" />
        <span className="text-[11px] font-medium uppercase tracking-[0.1em] text-zinc-400">
          In this chat
        </span>
        <span className="text-[11px] text-zinc-600">
          {questions} question{questions === 1 ? "" : "s"}
        </span>
        <button
          type="button"
          aria-label="Close the map"
          title="Close (Esc)"
          onClick={onClose}
          className="ml-auto grid h-5 w-5 shrink-0 place-items-center rounded-md text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-200"
        >
          <X size={12} aria-hidden="true" />
        </button>
      </div>
      {searchable && (
        <div className="relative isolate shrink-0 border-b border-white/[0.05] px-2 py-1.5">
          <Search
            size={12}
            aria-hidden="true"
            className="pointer-events-none absolute left-4 top-1/2 z-[1] -translate-y-1/2 text-zinc-600"
          />
          <input
            ref={searchRef}
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Find a question"
            aria-label="Find a question"
            aria-controls={`${baseId}-list`}
            aria-activedescendant={rows.length ? optionId(cursor) : undefined}
            className="field w-full py-1 pl-7 text-[12px]"
          />
        </div>
      )}
      <div
        ref={listRef}
        id={`${baseId}-list`}
        role="listbox"
        tabIndex={0}
        aria-label="Questions in this chat"
        aria-activedescendant={rows.length ? optionId(cursor) : undefined}
        className="min-h-0 flex-1 overflow-y-auto p-1 outline-none"
      >
        {rows.length === 0 ? (
          <p className="px-2 py-3 text-[12px] text-zinc-500">
            {all.length === 0 ? "No questions yet." : "No questions match."}
          </p>
        ) : (
          rows.map((r, i) => {
            const selected = i === cursor;
            const active = r.index === activeRow;
            const time = timeOf(r.at);
            return (
              <div
                key={r.index}
                id={optionId(i)}
                role="option"
                aria-selected={selected}
                aria-current={active ? "true" : undefined}
                data-index={r.index}
                data-kind={r.kind}
                onMouseDown={(e) => e.preventDefault()}
                onMouseEnter={() => setCursor(i)}
                onClick={() => onJump(r.index)}
                className={`flex min-w-0 cursor-pointer items-baseline gap-2 rounded-lg px-2 py-1.5 transition-colors ${
                  r.kind === "note" ? "pl-6" : ""
                } ${selected ? "bg-white/[0.05]" : "hover:bg-white/[0.03]"}`}
              >
                <span className="min-w-0 flex-1">
                  <span
                    className={`block truncate ${
                      r.kind === "note" ? "text-[12px] text-zinc-500" : "text-[13px] text-zinc-300"
                    } ${active ? "text-accent-soft" : ""}`}
                  >
                    {r.kind === "note" && <span className="text-zinc-600">Note: </span>}
                    {r.line || <span className="italic text-zinc-600">(no text)</span>}
                  </span>
                  {r.kind === "question" && (
                    <span className="block truncate text-[11px] text-zinc-600">
                      {replyNote(r.replyWords)}
                      {time ? ` · ${time}` : ""}
                    </span>
                  )}
                </span>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
