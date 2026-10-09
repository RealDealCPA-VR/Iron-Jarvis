"use client";

// Calm chat wave 4 (v1.329.0): the Build pane's approval takes the composer's
// place, the way the chat page's DockAsk does (components/chat/DockAsk.tsx).
//
// Why a pane copy instead of DockAsk itself: DockAsk also answers Enter / Esc
// while NOTHING has focus (a window-level key listener). That is right on the
// chat page, where one chat is on screen. On the Build page a pane's chat is
// kept mounted but HIDDEN (visibility, never an unmount) behind its terminal,
// and several panes can each be waiting on a question at once. A window
// listener there would let one Enter allow a command in a pane the user cannot
// see. So this card answers keys ONLY while focus is inside it, and it takes
// the focus only when the pane is on screen and the user was not working
// somewhere else (a terminal, another field).
//
// Same look as the chat page's dock: one card, a hairline edge in the warning
// tone, theme tokens only (dark Marks, Daylight and the user's own palettes).
// The card itself is the shared ApprovalCard in its docked form, so the words,
// the buttons and the POST are exactly the chat page's.

import { useEffect, useRef, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { ApprovalCard } from "@/components/chat/ApprovalCard";
import { dockAskKeyHint, dockKeyAction } from "@/lib/dockAsk";
import type { PendingApproval } from "@/lib/useChatStream";

/** How long after a question appears before Enter / Esc answer it. The same
 *  wait as the chat page's dock (DOCK_KEY_ARM_MS, pinned equal by a test): an
 *  Enter typed for the composer must not allow a command not yet read. */
export const PANE_ASK_ARM_MS = 400;

/** The dock edge for an approval: the chat page's, in the warning tone. */
export const PANE_ASK_EDGE =
  "shadow-[0_0_0_0.5px_rgb(var(--tone-warn)/0.55),0_6px_24px_rgb(0_0_0/0.35)] [[data-scheme=light]_&]:shadow-[0_0_0_0.5px_rgb(var(--tone-warn)/0.6),0_4px_18px_rgb(var(--white)/0.08)]";

/** Is this element on screen? A pane hidden behind its terminal is
 *  visibility:hidden, which offsetParent does not see. No API (an odd
 *  runtime, jsdom) = treat it as on screen. */
export function onScreen(el: Element | null): boolean {
  if (!el) return false;
  const check = (el as Element & {
    checkVisibility?: (o?: { visibilityProperty?: boolean }) => boolean;
  }).checkVisibility;
  return typeof check === "function" ? check.call(el, { visibilityProperty: true }) : true;
}

function bodyHasFocus(): boolean {
  const ae = document.activeElement;
  return !ae || ae === document.body || ae === document.documentElement;
}

export function PaneAsk({
  approval,
  onConversation,
  paneRoot,
  armDelayMs = PANE_ASK_ARM_MS,
}: {
  approval: PendingApproval;
  /** "Allow for this conversation": the pane's one grant path. */
  onConversation?: (tool: string) => void;
  /** The pane chat's root: focus that was inside it may move to the card. */
  paneRoot?: { readonly current: Element | null };
  armDelayMs?: number;
}) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const shownAtRef = useRef(0);

  // A new question on screen: start the key guard, and take the focus when
  // the pane is visible and the focus was in this pane (its composer, now
  // hidden) or nowhere. A terminal or a field elsewhere keeps its focus.
  useEffect(() => {
    shownAtRef.current = Date.now();
    const el = wrapRef.current;
    if (!el || !onScreen(el)) return;
    const ae = document.activeElement;
    const root = paneRoot?.current ?? null;
    const inPane = !!ae && !!root && root.contains(ae);
    if (bodyHasFocus() || inPane) el.focus({ preventScroll: true });
    // Only when a NEW question arrives (its id), never on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [approval.id]);

  /** Press the card's own button for this answer: a key runs exactly what a
   *  click runs. False = nothing happened. */
  function press(which: "primary" | "decline"): boolean {
    if (Date.now() - shownAtRef.current < armDelayMs) return false;
    const btn = wrapRef.current?.querySelector<HTMLButtonElement>(`[data-dock-${which}]`);
    if (!btn || btn.disabled) return false;
    btn.click();
    return true;
  }

  function onKeyDownCapture(e: ReactKeyboardEvent<HTMLDivElement>) {
    if (e.nativeEvent.isComposing || e.keyCode === 229) return;
    const action = dockKeyAction(
      {
        key: e.key,
        shiftKey: e.shiftKey,
        ctrlKey: e.ctrlKey,
        altKey: e.altKey,
        metaKey: e.metaKey,
        repeat: e.repeat,
        isComposing: false,
      },
      e.target as Element,
      true,
    );
    if (action && press(action)) {
      e.preventDefault();
      e.stopPropagation();
    }
  }

  return (
    <div className="flex min-h-0 flex-col gap-1.5">
      <div
        ref={wrapRef}
        data-testid="pane-ask"
        role="region"
        aria-label="A question for you"
        tabIndex={-1}
        onKeyDownCapture={onKeyDownCapture}
        className={`min-h-0 overflow-y-auto rounded-[18px] bg-ink-800 outline-none ${PANE_ASK_EDGE}`}
      >
        <ApprovalCard key={approval.id} approval={approval} onConversation={onConversation} docked />
      </div>
      <p className="px-2 text-[11px] text-zinc-500">{dockAskKeyHint("approval")}</p>
    </div>
  );
}
