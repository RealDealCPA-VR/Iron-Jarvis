"use client";

/**
 * v1.447.0 (calm sweep): ONE small icon button for the few actions a
 * first-time reader knows by their picture alone (edit, delete, copy,
 * refresh, dismiss). Its accessible name is the words it replaced
 * (`label`, as `aria-label`), and a tip with those words shows after a short
 * pause on the pointer, or at once on keyboard focus.
 *
 * The tip is mounted ONLY while it shows, portaled to <body> and `fixed`, so
 * an icon button at rest adds nothing to the layout. (An always-mounted
 * absolute tip widens any scroll box it sits in: the v1.420.2 lesson.) It is
 * aria-hidden: the button already carries the same words as its name.
 *
 * Never use this for a primary action, a choice about privacy or money, an
 * approval, or anything whose picture is not universal. When in doubt, keep
 * the words.
 */

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type FocusEvent as ReactFocusEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
  type RefObject,
} from "react";
import { createPortal } from "react-dom";

/** How long the pointer rests before the tip shows (a passing pointer shows nothing). */
export const ICON_TIP_DELAY_MS = 400;

/** The tip's look: the v1.401.2 pane-tab tip, fixed instead of absolute. */
export const ICON_TIP_CLASS =
  "pointer-events-none fixed z-[90] max-w-[min(20rem,calc(100vw-1rem))] rounded-md border border-white/10 bg-ink-900 px-2 py-0.5 text-[12px] leading-5 text-zinc-200 shadow-lg shadow-black/30";

/** The button's look: a quiet 28 px square that fills on hover. */
export const ICON_BUTTON_CLASS =
  "inline-grid h-7 w-7 shrink-0 place-items-center rounded-lg text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-zinc-100 aria-pressed:bg-white/[0.08] aria-pressed:text-zinc-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 disabled:cursor-not-allowed disabled:opacity-50";

/** The warn look: for a dismiss on a warn-tone strip. */
export const ICON_BUTTON_WARN_CLASS =
  "inline-grid h-7 w-7 shrink-0 place-items-center rounded-lg text-tone-warn/80 transition-colors hover:bg-tone-warn/10 hover:text-tone-warn focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 disabled:cursor-not-allowed disabled:opacity-50";

/** The danger look (trash): neutral at rest, the danger tone on hover. */
export const ICON_BUTTON_DANGER_CLASS =
  "inline-grid h-7 w-7 shrink-0 place-items-center rounded-lg text-zinc-400 transition-colors hover:bg-tone-danger/10 hover:text-tone-danger focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 disabled:cursor-not-allowed disabled:opacity-50";

/** Whether the newest input was the keyboard (Tab, arrows), so a focus that
 *  follows it is a keyboard focus. The browser's own `:focus-visible` says the
 *  same; this keeps the answer where that selector is not known. */
let keyboardLast = false;
if (typeof window !== "undefined") {
  window.addEventListener(
    "keydown",
    (e) => {
      if (!e.metaKey && !e.ctrlKey && !e.altKey) keyboardLast = true;
    },
    true,
  );
  const pointer = () => {
    keyboardLast = false;
  };
  window.addEventListener("pointerdown", pointer, true);
  window.addEventListener("mousedown", pointer, true);
}

function focusVisible(el: Element): boolean {
  let native = false;
  try {
    native = el.matches(":focus-visible");
  } catch {
    native = false;
  }
  return native || keyboardLast;
}

/** The tip's state for one button: handlers to spread, and the tip to draw. */
export function useIconTip(text: string, testId?: string) {
  const ref = useRef<HTMLElement | null>(null);
  const [shown, setShown] = useState(false);
  const timer = useRef<number | null>(null);
  const clear = useCallback(() => {
    if (timer.current !== null) {
      window.clearTimeout(timer.current);
      timer.current = null;
    }
  }, []);
  const hide = useCallback(() => {
    clear();
    setShown(false);
  }, [clear]);
  useEffect(() => clear, [clear]);
  useEffect(() => {
    if (!shown) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") hide();
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("scroll", hide, true);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", hide, true);
    };
  }, [shown, hide]);

  const handlers = {
    onPointerEnter: (e: ReactPointerEvent<HTMLElement>) => {
      if (e.pointerType === "touch") return;
      clear();
      timer.current = window.setTimeout(() => setShown(true), ICON_TIP_DELAY_MS);
    },
    onPointerLeave: () => hide(),
    onPointerDown: () => hide(),
    onFocus: (e: ReactFocusEvent<HTMLElement>) => {
      if (focusVisible(e.currentTarget)) setShown(true);
    },
    onBlur: () => hide(),
  };

  const tip = shown ? <IconTip anchor={ref} text={text} testId={testId} /> : null;
  return { ref, handlers, tip, shown };
}

function IconTip({
  anchor,
  text,
  testId,
}: {
  anchor: RefObject<HTMLElement | null>;
  text: string;
  testId?: string;
}) {
  const tipRef = useRef<HTMLSpanElement | null>(null);
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);
  useLayoutEffect(() => {
    const a = anchor.current;
    const t = tipRef.current;
    if (!a || !t) return;
    const r = a.getBoundingClientRect();
    const w = t.offsetWidth;
    const h = t.offsetHeight;
    const vw = window.innerWidth || document.documentElement.clientWidth || 0;
    const vh = window.innerHeight || document.documentElement.clientHeight || 0;
    const margin = 8;
    let left = r.left + r.width / 2 - w / 2;
    if (vw > 0) left = Math.max(margin, Math.min(left, vw - w - margin));
    // Under the button; above it when there is no room under.
    let top = r.bottom + 6;
    if (vh > 0 && top + h > vh - margin) top = Math.max(margin, r.top - h - 6);
    setPos({ left: Math.round(left), top: Math.round(top) });
  }, [anchor, text]);
  if (typeof document === "undefined") return null;
  return createPortal(
    <span
      ref={tipRef}
      aria-hidden="true"
      data-testid={testId ? `${testId}-tip` : "icon-tip"}
      data-icon-tip=""
      className={ICON_TIP_CLASS}
      style={pos ? { left: pos.left, top: pos.top } : { left: 0, top: 0, visibility: "hidden" }}
    >
      {text}
    </span>,
    document.body,
  );
}

/**
 * The icon button. `label` is its accessible name (the words it replaced);
 * `tip` the words shown on hover/focus (defaults to `label`). Every other
 * button prop passes through; `type` defaults to "button".
 */
export function IconButton({
  label,
  tip,
  icon,
  tone = "neutral",
  size = "md",
  testId,
  className = "",
  type = "button",
  onPointerEnter,
  onPointerLeave,
  onPointerDown,
  onFocus,
  onBlur,
  ...rest
}: {
  label: string;
  tip?: string;
  icon: ReactNode;
  tone?: "neutral" | "danger" | "warn";
  /** md = 28 px (rows, headers); sm = 24 px (a compact strip). */
  size?: "sm" | "md";
  testId?: string;
  className?: string;
} & Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children" | "aria-label" | "title">) {
  const t = useIconTip(tip ?? label, testId);
  const look = tone === "danger" ? ICON_BUTTON_DANGER_CLASS : tone === "warn" ? ICON_BUTTON_WARN_CLASS : ICON_BUTTON_CLASS;
  const sized = size === "sm" ? look.replace("h-7 w-7", "h-6 w-6 rounded-md").replace(" rounded-lg", "") : look;
  return (
    <>
      <button
        ref={(el) => {
          t.ref.current = el;
        }}
        type={type}
        aria-label={label}
        data-testid={testId}
        data-icon-button=""
        className={`${sized} ${className}`.trim()}
        onPointerEnter={(e) => {
          t.handlers.onPointerEnter(e);
          onPointerEnter?.(e);
        }}
        onPointerLeave={(e) => {
          t.handlers.onPointerLeave();
          onPointerLeave?.(e);
        }}
        onPointerDown={(e) => {
          t.handlers.onPointerDown();
          onPointerDown?.(e);
        }}
        onFocus={(e) => {
          t.handlers.onFocus(e);
          onFocus?.(e);
        }}
        onBlur={(e) => {
          t.handlers.onBlur();
          onBlur?.(e);
        }}
        {...rest}
      >
        {icon}
      </button>
      {t.tip}
    </>
  );
}
