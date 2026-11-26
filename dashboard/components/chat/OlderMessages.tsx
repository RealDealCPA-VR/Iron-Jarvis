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
 * The top says "Start of this chat" only when the daemon says the first
 * kept message is the chat's very first (`start_kept`); a chat that passed
 * 200 messages before v1.499.4 lost its first ones then, and its top says
 * "Earlier messages were not kept before this version".
 *
 * Opening a chat reads nothing older, and no earlier page is read until
 * the reader reaches the top: the observer only tells the page whether the
 * line is in view. (The page does read, at once, the few rows that leave
 * its live list when it takes a newer copy whose count grew, so what the
 * reader was looking at stays on screen.)
 */

import { Fragment, memo, useEffect, useRef, type ReactNode, type RefObject } from "react";
import { ChevronUp, Loader2 } from "lucide-react";
import { chatDay } from "@/lib/atMenuRows";
import {
  OLDER_WORDS,
  atStartOfChat,
  dayKey,
  gapToFill,
  hasEarlier,
  startWords,
  type OlderState,
} from "@/lib/olderMessages";

/** How far above the scroller's top the line counts as reached, so the next
 *  page is on its way a moment before the reader gets there. */
const REACH_MARGIN = "400px 0px 0px 0px";

const COLUMN = "mx-auto w-full max-w-[760px]";

function DayLine({ at, column = COLUMN }: { at?: string; column?: string }) {
  const label = chatDay(at);
  if (!label) return null;
  return (
    <div
      data-testid="older-day"
      role="separator"
      aria-label={label}
      className={`${column} flex items-center gap-3 text-[11px] text-zinc-500`}
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
  /** Whether the line is in view (within the reach margin), on every
   *  report: the page loads when a reader who was following the newest
   *  message scrolls up while it already is (no report comes then). */
  onInView?: (inView: boolean) => void;
  /** The line, or Try again, was pressed. */
  onLoad: () => void;
  /** Try again on the line between the held rows and the live ones (the
   *  rows in between could not be read). */
  onRetryGap?: () => void;
  /** One row, read-only (the page's own row component). */
  renderRow: (message: M, seq: number) => ReactNode;
  /** 48-b: what the top says once every earlier message is held and the
   *  first is the very first (a chat's "Start of this chat"; a coding
   *  session and an old round-table name themselves). */
  startText?: string;
  /** 48-b: the column each line sits in (a chat's reading column; a page
   *  whose rows fill their card passes its own). */
  column?: string;
}

function OlderMessagesImpl<M extends { at?: string }>({
  state,
  firstLiveAt,
  rootRef,
  onReach,
  onInView,
  onLoad,
  onRetryGap,
  renderRow,
  startText = OLDER_WORDS.start,
  column = COLUMN,
}: OlderMessagesProps<M>) {
  const topRef = useRef<HTMLDivElement>(null);
  const reachRef = useRef(onReach);
  reachRef.current = onReach;
  const inViewRef = useRef(onInView);
  inViewRef.current = onInView;
  const more = hasEarlier(state);

  // A fresh observer each time the held rows grow upward: its first report
  // says whether the line is STILL in view (a short page), so the next page
  // follows without the reader having to scroll away and back.
  useEffect(() => {
    const el = topRef.current;
    if (!more || !el || typeof IntersectionObserver === "undefined") return;
    const io = new IntersectionObserver(
      (entries) => {
        const hit = entries.some((e) => e.isIntersecting);
        inViewRef.current?.(hit);
        if (hit) reachRef.current();
      },
      { root: rootRef?.current ?? null, rootMargin: REACH_MARGIN },
    );
    io.observe(el);
    return () => {
      io.disconnect();
      inViewRef.current?.(false); // until the next observer says otherwise
    };
  }, [more, state.loadedFrom, state.threadId, rootRef]);

  if (!state.threadId || state.total <= 0) return null;
  const rows = state.rows;
  const lastAt = rows.length ? rows[rows.length - 1].message.at : undefined;
  const joinDay = rows.length > 0 && dayKey(firstLiveAt) !== "" && dayKey(lastAt) !== dayKey(firstLiveAt);
  // The rows between the held ones and the live ones could not be read: a
  // line where they go says so, and Try again reads them.
  const gapFailed = state.gapFailed && rows.length > 0 && gapToFill(state) !== null;
  return (
    <div className="contents" data-testid="older-messages">
      {more ? (
        <div ref={topRef} data-testid="older-top" className={`${column} flex justify-center`}>
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
        <div data-testid="older-start" className={`${column} text-center text-[12px] text-zinc-500`}>
          {startWords(state, startText)}
        </div>
      ) : null}
      {rows.map((r, i) => {
        const day = dayKey(r.message.at);
        const newDay = day !== "" && (i === 0 || day !== dayKey(rows[i - 1].message.at));
        return (
          <Fragment key={`older-${r.seq}`}>
            {newDay && <DayLine at={r.message.at} column={column} />}
            {/* A row that draws nothing (a hidden Continue turn) keeps no box,
                like the live rows. */}
            <div data-older-seq={r.seq} className={`${column} empty:hidden has-[[data-handoff-hidden]]:hidden`}>
              {renderRow(r.message, r.seq)}
            </div>
          </Fragment>
        );
      })}
      {gapFailed ? (
        <div
          data-testid="older-gap-failed"
          role="status"
          className={`${column} flex flex-wrap items-center justify-center gap-x-2 text-center text-[12px] text-tone-warn`}
        >
          <span>{OLDER_WORDS.failed}</span>
          <button
            type="button"
            onClick={onRetryGap}
            className="inline-flex h-7 items-center rounded-lg px-2 text-[12px] text-zinc-300 transition-colors hover:bg-white/[0.06] hover:text-zinc-100"
          >
            {OLDER_WORDS.retry}
          </button>
        </div>
      ) : (
        joinDay && <DayLine at={firstLiveAt} column={column} />
      )}
    </div>
  );
}

/** Memoized: a page render that changes nothing here (a daemon event, a
 *  streamed reply settling) does not walk every row held. The page hands it
 *  the same state object and stable callbacks until something changes. */
export const OlderMessages = memo(OlderMessagesImpl) as typeof OlderMessagesImpl;
