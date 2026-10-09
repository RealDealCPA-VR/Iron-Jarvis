"use client";

import { useEffect, useRef, type RefObject } from "react";

/**
 * v1.326.0 (calm chat W1-2): the chat transcript's ONE bottom target.
 *
 * Every "go to the newest line" scroll on the chat page goes through
 * `scrollToLatest`: the scroll effect, the streamed reply's onGrow
 * (`scrollLiveIntoView`), "Jump to latest" and the growth re-pin below. They
 * all aim at the same element (the bottom sentinel) the same way, so no two of
 * them can disagree about where the bottom is.
 *
 * That disagreement was a real regression. The re-pin first set
 * `scrollTop = scrollHeight - clientHeight` (the true bottom) while every
 * other scroll used the sentinel's `scrollIntoView({block: "end"})`, which
 * stopped short by the scroller's 32px bottom padding. During a stream each
 * new line fired the observer (true bottom, before paint) and then onGrow
 * (32px short, after paint), so the reply bounced on every line. The sentinel
 * carries a `scroll-margin-bottom` equal to that padding, so the sentinel's
 * target IS the true bottom and the newest line sits clear of the fade.
 */
export function scrollToLatest(
  sentinel: Element | null | undefined,
  behavior: ScrollBehavior = "auto",
): void {
  sentinel?.scrollIntoView({ behavior, block: "end" });
}

/**
 * Keep a transcript that is FOLLOWING its newest line at the bottom when its
 * content grows on its own.
 *
 * Opening a saved chat scrolls to the bottom sentinel, and then a chart or a
 * table finishes laying out and the conversation grows by 40-120px. The
 * scroll had already been aimed at the old bottom, so the chat came to rest
 * short of its last line and the page's scroll handler read that as "the
 * reader scrolled up" and showed "Jump to latest" over a chat nobody touched
 * (measured on the scratch stack: 71px short on a desk, 150px on a phone).
 *
 * This watches the scroller's children and, when the content got TALLER while
 * `following()` says the reader is pinned to the bottom, scrolls to the
 * sentinel at once (instantly, through `scrollToLatest`, the same target as
 * every other bottom scroll). A ResizeObserver reports in the same frame as
 * the layout that grew, before that frame's scroll event is handled, so the
 * pin holds. It never moves a reader who scrolled up (`following()` is false
 * then), and content that SHRANK is left alone.
 *
 * Its own module (not the chat page) so it can be tested on its own, and
 * because tests that mock `@/lib/useChatStream` have fixed export lists.
 */
export function useRepinOnGrowth(
  scrollRef: RefObject<HTMLElement | null>,
  bottomRef: RefObject<HTMLElement | null>,
  following: () => boolean,
): void {
  const followingRef = useRef(following);
  followingRef.current = following;

  useEffect(() => {
    const el = scrollRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    let lastHeight = el.scrollHeight;
    const ro = new ResizeObserver(() => {
      const height = el.scrollHeight;
      const grew = height > lastHeight;
      lastHeight = height;
      if (grew && followingRef.current()) scrollToLatest(bottomRef.current, "auto");
    });
    // A `display: contents` wrapper has no box of its own (a ResizeObserver on
    // it never reports), so its children are the rows that grow: watch them,
    // and watch the wrapper for rows coming and going.
    const watched = new Set<Element>();
    const lists = new Set<Element>();
    const mo =
      typeof MutationObserver === "undefined"
        ? null
        : new MutationObserver((records) => {
            for (const r of records) {
              for (const node of Array.from(r.removedNodes)) {
                if (node instanceof Element && watched.delete(node)) ro.unobserve(node);
              }
            }
            watch(el);
          });
    const watch = (parent: Element) => {
      if (!lists.has(parent)) {
        lists.add(parent);
        mo?.observe(parent, { childList: true });
      }
      for (const child of Array.from(parent.children)) {
        if (getComputedStyle(child).display === "contents") {
          watch(child);
          continue;
        }
        if (watched.has(child)) continue;
        watched.add(child);
        ro.observe(child);
      }
    };
    watch(el);
    return () => {
      ro.disconnect();
      mo?.disconnect();
    };
  }, [scrollRef, bottomRef]);
}
