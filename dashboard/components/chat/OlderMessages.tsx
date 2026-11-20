"use client";

/**
 * A long chat's earlier messages, above its live ones (48-a, idea 48
 * "Scroll back forever").
 *
 * At the top of a chat that has older messages sits one quiet line,
 * "Earlier messages". When the reader scrolls up to it (the page decides:
 * only once the reader has left the bottom), or presses it, the page reads
 * the 50 before and they appear here, oldest first, with a date line where
 * the day changes, until "Start of this chat". A read that fails says so in
 * words and offers Try again, never an empty top.
 *
 * The rows are drawn by the page's own row (`renderRow`, the chat's
 * `MessageRow` in its read-only form): they can be read, copied and quoted,
 * never edited or run again. They are NOT the page's `messages` (the save
 * sends those whole); `lib/olderMessages` holds them.
 *
 * Nothing is read until the reader reaches the top: the observer only tells
 * the page the line is in view.
 */

import { Fragment, memo, useEffect, useRef, type ReactNode, type RefObject } from "react";
import { ChevronUp, Loader2 } from "lucide-react";
import { chatDay } from "@/lib/atMenuRows";
import {
  OLDER_WORDS,
  atStartOfChat,
  dayKey,
  hasEarlier,
  type OlderState,
} from "@/lib/olderMessages";

/** How far above the scroller's top the line counts as reached, so the next
 *  page is on its way a moment before the reader gets there. */
const REACH_MARGIN = "400px 0px 0px 0px";

const COLUMN = "mx-auto w-full max-w-[760px]";

function DayLine({ at }: { at?: string }) {
  const label = chatDay(at);
  if (!label) return null;
  return (
    <div
      data-testid="older-day"
      role="separator"
      aria-label={label}
      className={`${COLUMN} flex items-center gap-3 text-[11px] text-zinc-500`}
    >
      <span aria-hidden className="h-px min-w-0 flex-1 bg-white/[0.06]" />
      <span className="shrink-0">{label}</span>
      <span aria-hidden className="h-px min-w-0 flex-1 bg-white/[0.06]" />
    </div>
  );
}

export interface OlderMessagesProps<M extends { at?: string }> {
  state: OlderState<M>;
  /** When the first live message was written: a date line where the older
   *  rows meet it, when the day changes there. */
  firstLiveAt?: string;
  /** The chat's scroller (what "in view" is measured against). */
  rootRef?: RefObject<HTMLElement | null>;
  /** The line came into view. */
  onReach: () => void;
  /** The line, or Try again, was pressed. */
  onLoad: () => void;
  /** One row, read-only (the page's own row component). */
  renderRow: (message: M, seq: number) => ReactNode;
}

function OlderMessagesImpl<M extends { at?: string }>({
  state,
  firstLiveAt,
  rootRef,
  onReach,
  onLoad,
  renderRow,
}: OlderMessagesProps<M>) {
  const topRef = useRef<HTMLDivElement>(null);
  const reachRef = useRef(onReach);
  reachRef.current = onReach;
  const more = hasEarlier(state);

  // A fresh observer each time the held rows grow upward: its first report
  // says whether the line is STILL in view (a short page), so the next page
  // follows without the reader having to scroll away and back.
  useEffect(() => {
    const el = topRef.current;
    if (!more || !el || typeof IntersectionObserver === "undefined") return;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) reachRef.current();
      },
      { root: rootRef?.current ?? null, rootMargin: REACH_MARGIN },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [more, state.loadedFrom, state.threadId, rootRef]);

  if (!state.threadId || state.total <= 0) return null;
  const rows = state.rows;
  const lastAt = rows.length ? rows[rows.length - 1].message.at : undefined;
  const joinDay = rows.length > 0 && dayKey(firstLiveAt) !== "" && dayKey(lastAt) !== dayKey(firstLiveAt);
  return (
    <div className="contents" data-testid="older-messages">
      {more ? (
        <div ref={topRef} data-testid="older-top" className={`${COLUMN} flex justify-center`}>
          {state.failed ? (
            <div
              data-testid="older-failed"
              role="status"
              className="flex flex-wrap items-center justify-center gap-x-2 text-center text-[12px] text-tone-warn"
            >
              <span>{OLDER_WORDS.failed}</span>
              <button
                type="button"
                onClick={onLoad}
                className="inline-flex h-7 items-center rounded-lg px-2 text-[12px] text-zinc-300 transition-colors hover:bg-white/[0.06] hover:text-zinc-100"
              >
                {OLDER_WORDS.retry}
              </button>
            </div>
          ) : (
            <button
              type="button"
              data-testid="older-more"
              onClick={onLoad}
              disabled={state.loading}
              aria-busy={state.loading}
              className="inline-flex h-7 items-center gap-1.5 rounded-lg px-2 text-[12px] text-zinc-500 transition-colors hover:bg-white/[0.04] hover:text-zinc-300 disabled:cursor-default disabled:hover:bg-transparent"
            >
              {state.loading ? (
                <Loader2 size={12} aria-hidden className="animate-spin" />
              ) : (
                <ChevronUp size={12} aria-hidden />
              )}
              {OLDER_WORDS.more}
            </button>
          )}
        </div>
      ) : atStartOfChat(state) ? (
        <div data-testid="older-start" className={`${COLUMN} text-center text-[12px] text-zinc-500`}>
          {OLDER_WORDS.start}
        </div>
      ) : null}
      {rows.map((r, i) => {
        const day = dayKey(r.message.at);
        const newDay = day !== "" && (i === 0 || day !== dayKey(rows[i - 1].message.at));
        return (
          <Fragment key={`older-${r.seq}`}>
            {newDay && <DayLine at={r.message.at} />}
            {/* A row that draws nothing (a hidden Continue turn) keeps no box,
                like the live rows. */}
            <div data-older-seq={r.seq} className={`${COLUMN} empty:hidden has-[[data-handoff-hidden]]:hidden`}>
              {renderRow(r.message, r.seq)}
            </div>
          </Fragment>
        );
      })}
      {joinDay && <DayLine at={firstLiveAt} />}
    </div>
  );
}

/** Memoized: a page render that changes nothing here (a daemon event, a
 *  streamed reply settling) does not walk every row held. The page hands it
 *  the same state object and stable callbacks until something changes. */
export const OlderMessages = memo(OlderMessagesImpl) as typeof OlderMessagesImpl;
