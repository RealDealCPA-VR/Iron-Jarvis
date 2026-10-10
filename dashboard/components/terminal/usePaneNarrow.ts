"use client";

/**
 * Is this element narrower than `px`? (calm chat wave 5, v1.329.0)
 *
 * A Build pane is as wide as the user drags it (a canvas pane can be 280px on
 * a 1440px screen), so the viewport's `sm:` breakpoint says nothing about the
 * room inside a pane's composer. This watches the element itself with a
 * ResizeObserver. Without one (jsdom, an odd runtime) it answers false: the
 * wide layout, as before.
 */

import { useEffect, useState, type RefObject } from "react";

export function usePaneNarrow(ref: RefObject<HTMLElement | null>, px: number): boolean {
  const [narrow, setNarrow] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const check = (w: number) => setNarrow(w > 0 && w < px);
    check(el.getBoundingClientRect().width);
    const ro = new ResizeObserver((entries) => {
      const box = entries[entries.length - 1];
      if (box) check(box.contentRect.width);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref, px]);
  return narrow;
}
