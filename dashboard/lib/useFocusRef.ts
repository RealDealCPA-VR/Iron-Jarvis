"use client";

import { useEffect, useRef, type RefObject } from "react";

/* -------------------------------------------------------------------------- */
/*  Deep-link focus: land on the CARD, not just the page                       */
/* -------------------------------------------------------------------------- */

/*
 * Global search (and any other "take me to X" affordance) is only useful if it
 * lands on the exact control the user asked for. Sending someone to /documents
 * when they searched "redact" leaves them scanning a long page for a card they
 * have never seen — which is how a feature that exists still reads as missing
 * ("i dont see the ability to rename" was exactly this failure mode).
 *
 * The convention: `/<route>?focus=<key>` scrolls the matching card into view
 * and flashes it once. A card opts in with one line:
 *
 *     const ref = useFocusRef<HTMLDivElement>("redact");
 *     ...
 *     <div ref={ref}><Card title="Redact PII"> … </Card></div>
 *
 * The wrapper div exists because the shared `Card` primitive does not forward
 * refs; wrapping is strictly cheaper (and safer) than changing a component the
 * whole app renders.
 */

/**
 * Tailwind classes for the one-shot highlight. Written as literals so the JIT
 * scanner picks them up from this file (tailwind.config content includes
 * ./lib/**), and toggled through `classList` rather than React state so the
 * host component never re-renders for a purely decorative flash.
 */
const HIGHLIGHT_CLASSES = [
  "ring-2",
  "ring-accent/70",
  // Without an explicit radius the ring draws a hard rectangle around cards
  // that are themselves rounded (`.card-surface` is `rounded-2xl`), which reads
  // as a rendering glitch rather than a highlight. 2xl matches the card scale
  // set in tailwind.config.ts.
  "rounded-2xl",
  "transition-shadow",
];

/** How long the flash lingers. Long enough to notice, short enough to forget. */
const HIGHLIGHT_MS = 2500;

/** True when the OS asks for reduced motion (also the safe answer if unknown). */
function prefersReducedMotion(): boolean {
  try {
    return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
  } catch {
    return false;
  }
}

/**
 * Scroll the ref'd element into view and flash it once when the current URL
 * carries `?focus=<key>`.
 *
 * CRITICAL — this reads `window.location.search` directly instead of
 * next/navigation's `useSearchParams`. Nearly every dashboard route is
 * statically generated, and `useSearchParams` opts a static route into
 * client-side bailout: Next then demands a `<Suspense>` boundary around every
 * consumer or the production build fails outright. The same constraint is
 * documented at the /chat project deep-link (dashboard/app/chat/page.tsx,
 * ~line 1285). A query param read once on mount does not need the router.
 *
 * Everything is guarded: no window, no element, no `focus` param, or a key
 * mismatch all no-op. Passing an empty key disables the hook, which lets a
 * component that renders many instances (one per provider, say) opt only the
 * relevant one in without breaking the rules of hooks.
 */
export function useFocusRef<T extends HTMLElement>(key: string): RefObject<T | null> {
  const ref = useRef<T | null>(null);

  useEffect(() => {
    if (!key || typeof window === "undefined") return;

    let wanted: string | null = null;
    try {
      wanted = new URLSearchParams(window.location.search).get("focus");
    } catch {
      // Malformed query strings should never take a page down.
      return;
    }
    if (wanted !== key) return;

    const el = ref.current;
    if (!el) return; // conditionally-rendered target: nothing to focus, no crash

    const smooth = !prefersReducedMotion();

    // Only ever REMOVE what we actually added. Some highlight classes (notably
    // `rounded-2xl`) legitimately appear in a target's own className; blindly
    // stripping them at the end of the flash would delete styling React put
    // there and never restores, since className never re-renders.
    let added: string[] = [];
    let timer = 0;

    // Defer one frame: on first paint the page is still settling (Reveal's
    // fade-up, data-driven cards mounting), so a scroll issued synchronously
    // can aim at a position that no longer exists a frame later.
    const raf = requestAnimationFrame(() => {
      try {
        el.scrollIntoView({ block: "center", behavior: smooth ? "smooth" : "auto" });
      } catch {
        // Older engines reject the options object — fall back to the boolean form.
        try {
          el.scrollIntoView();
        } catch {
          /* ignore */
        }
      }
      added = HIGHLIGHT_CLASSES.filter((c) => !el.classList.contains(c));
      el.classList.add(...added);
      // The timer starts HERE, not alongside the rAF: a background tab freezes
      // rAF but not setTimeout, so a sibling timer would expire before the ring
      // was ever drawn and the highlight would then stick forever.
      timer = window.setTimeout(() => {
        el.classList.remove(...added);
        added = [];
      }, HIGHLIGHT_MS);
    });

    // Unmounting mid-flash must not leave a permanent ring behind on a reused
    // DOM node, and must not fire a scroll into a page that has moved on.
    return () => {
      cancelAnimationFrame(raf);
      if (timer) window.clearTimeout(timer);
      el.classList.remove(...added);
      added = [];
    };
  }, [key]);

  return ref;
}
