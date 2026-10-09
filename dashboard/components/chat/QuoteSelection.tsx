"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Quote } from "lucide-react";

/**
 * QUOTE A SELECTION (v1.325.0; the idea is assistant-ui's, MIT — no code
 * taken). Select words inside a reply and a small "Quote" button floats
 * above them; pressing it hands the text to `onQuote` (the page puts it in
 * the box with lib/quote) and clears the selection.
 *
 * Only text inside ONE element marked `data-quote-source` (the coordinator
 * marks assistant reply bodies) inside THIS wrapper counts. The button hides
 * when the selection is empty or collapsed, starts or ends outside that one
 * source, or the page scrolls (its position would be stale).
 *
 * The button is portaled to <body> with fixed coordinates: the chat's
 * animated rows can carry a transform, and a transformed ancestor would turn
 * `position: fixed` into "fixed to that row".
 */

const BUTTON_W = 76;
const BUTTON_H = 28;
const GAP = 6;
const EDGE = 8;

function elementOf(node: Node | null): Element | null {
  if (!node) return null;
  return node.nodeType === 1 ? (node as Element) : node.parentElement;
}

function sourceOf(node: Node | null): Element | null {
  return elementOf(node)?.closest("[data-quote-source]") ?? null;
}

interface Offer {
  text: string;
  top: number;
  left: number;
}

export function QuoteSelection({
  children,
  onQuote,
  className,
}: {
  children: ReactNode;
  onQuote: (text: string) => void;
  className?: string;
}) {
  const rootRef = useRef<HTMLDivElement>(null);
  const [offer, setOffer] = useState<Offer | null>(null);

  const read = useCallback(() => {
    const root = rootRef.current;
    const sel = typeof window !== "undefined" ? window.getSelection() : null;
    if (!root || !sel || sel.rangeCount === 0 || sel.isCollapsed) {
      setOffer(null);
      return;
    }
    const text = sel.toString().trim();
    if (!text) {
      setOffer(null);
      return;
    }
    const range = sel.getRangeAt(0);
    const from = sourceOf(range.startContainer);
    const to = sourceOf(range.endContainer);
    if (!from || from !== to || !root.contains(from)) {
      setOffer(null);
      return;
    }
    // Range.getBoundingClientRect is missing in some environments (jsdom);
    // the source's own box is an honest fallback.
    let rect: DOMRect | null = null;
    if (typeof range.getBoundingClientRect === "function") rect = range.getBoundingClientRect();
    if (!rect || (rect.width === 0 && rect.height === 0)) rect = from.getBoundingClientRect();
    const vw = window.innerWidth || 1024;
    let top = rect.top - BUTTON_H - GAP;
    if (top < EDGE) top = rect.bottom + GAP;
    const left = Math.min(
      Math.max(EDGE, rect.left + rect.width / 2 - BUTTON_W / 2),
      Math.max(EDGE, vw - BUTTON_W - EDGE),
    );
    setOffer({ text, top, left });
  }, []);

  useEffect(() => {
    const onSel = () => read();
    const hide = () => setOffer(null);
    document.addEventListener("selectionchange", onSel);
    // Capture: a scroll inside ANY container (the chat list scrolls itself).
    window.addEventListener("scroll", hide, true);
    window.addEventListener("resize", hide);
    return () => {
      document.removeEventListener("selectionchange", onSel);
      window.removeEventListener("scroll", hide, true);
      window.removeEventListener("resize", hide);
    };
  }, [read]);

  function press() {
    if (!offer) return;
    const text = offer.text;
    setOffer(null);
    try {
      window.getSelection()?.removeAllRanges();
    } catch {
      /* nothing to clear */
    }
    onQuote(text);
  }

  return (
    <div
      ref={rootRef}
      className={className}
      // selectionchange is the main signal; these catch environments that
      // do not fire it on every change.
      onMouseUp={read}
      onKeyUp={read}
    >
      {children}
      {offer &&
        typeof document !== "undefined" &&
        createPortal(
          <button
            type="button"
            data-testid="quote-selection"
            aria-label="Quote this in your message"
            title="Quote this in your message"
            // Keep the selection alive through the press.
            onMouseDown={(e) => e.preventDefault()}
            onClick={press}
            style={{ position: "fixed", top: offer.top, left: offer.left, zIndex: 70 }}
            className="inline-flex h-7 items-center gap-1 rounded-full border border-white/[0.1] bg-ink-900/95 px-2.5 text-xs text-zinc-300 shadow-lg backdrop-blur transition-colors hover:border-white/[0.18] hover:text-zinc-100"
          >
            <Quote size={11} aria-hidden="true" className="shrink-0 text-accent-soft/80" />
            Quote
          </button>,
          document.body,
        )}
    </div>
  );
}
