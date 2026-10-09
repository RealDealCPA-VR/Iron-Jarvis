"use client";

// Calm chat W1-6 (v1.326.0): a question for you takes the composer's place.
//
// When a turn is paused on an approval, an app's question (MCP elicitation)
// or an app's request to use the turn's model (MCP sampling), the card is
// drawn HERE, in the dock, where the composer card sits — not in the
// transcript. The page keeps the composer mounted and hidden underneath, so
// the user's draft is exactly as they left it when the question is answered.
//
// KEYS: Enter presses the card's main answer (Allow once / Send) and Esc
// presses its "no" (Deny / Decline). Only while focus is inside this card,
// or nothing else on the page has focus. Enter never fires from a control
// that already does something with it (a button, a field, a <summary>), and
// Esc never fires from a field that takes typing. A key in the first moment
// after a question appears is ignored: an Enter typed for the composer must
// not allow a command the user has not seen yet. Keys press the card's OWN
// buttons, so a key runs exactly what a click runs (checks included).
//
// ORDER: several open questions show the first with "1 of N"; the one on
// screen stays first while it is still open (lib/dockAsk.withHeadFirst).

import { useCallback, useEffect, useRef, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { ApprovalCard } from "@/components/chat/ApprovalCard";
import { ElicitationCard } from "@/components/chat/ElicitationCard";
import { SamplingCard } from "@/components/chat/SamplingCard";
import { dockKeyAction, withHeadFirst, type DockAskItem } from "@/lib/dockAsk";
import type { AnswerResult } from "@/lib/mcpInteract";

/** How long after a question appears before Enter / Esc answer it. */
export const DOCK_KEY_ARM_MS = 400;

/** The card's edge: a hairline like the composer's, amber for an approval or
 *  a request to use the model, the accent for an app's question. Theme
 *  tokens only, so the light Marks and the user's own palettes follow. */
const EDGE: Record<DockAskItem["kind"], string> = {
  approval:
    "shadow-[0_0_0_0.5px_rgb(var(--tone-warn)/0.55),0_6px_24px_rgb(0_0_0/0.35)] [[data-scheme=light]_&]:shadow-[0_0_0_0.5px_rgb(var(--tone-warn)/0.6),0_4px_18px_rgb(var(--white)/0.08)]",
  sampling:
    "shadow-[0_0_0_0.5px_rgb(var(--tone-warn)/0.55),0_6px_24px_rgb(0_0_0/0.35)] [[data-scheme=light]_&]:shadow-[0_0_0_0.5px_rgb(var(--tone-warn)/0.6),0_4px_18px_rgb(var(--white)/0.08)]",
  elicitation:
    "shadow-[0_0_0_0.5px_rgb(var(--accent-rgb)/0.5),0_6px_24px_rgb(0_0_0/0.35)] [[data-scheme=light]_&]:shadow-[0_0_0_0.5px_rgb(var(--accent-rgb)/0.55),0_4px_18px_rgb(var(--white)/0.08)]",
};

function bodyHasFocus(): boolean {
  const ae = document.activeElement;
  return !ae || ae === document.body || ae === document.documentElement;
}

export function DockAsk({
  asks,
  onConversation,
  onAnswerElicitation,
  onDecideSampling,
  onStop,
  returnFocus,
  armDelayMs = DOCK_KEY_ARM_MS,
}: {
  /** Every open question, in answer order (lib/dockAsk.collectDockAsks). */
  asks: readonly DockAskItem[];
  /** "Allow for this conversation" on an approval: the page's one grant path. */
  onConversation?: (tool: string) => void;
  onAnswerElicitation: (
    id: string,
    action: "accept" | "decline" | "cancel",
    content?: Record<string, unknown>,
  ) => Promise<AnswerResult>;
  onDecideSampling: (id: string, decision: "approve" | "deny") => Promise<boolean>;
  /** Stop the running reply. The composer (and its Stop) is hidden while a
   *  question is up, so the dock offers it too. */
  onStop?: () => void;
  /** Called when the last question goes away while nothing has focus, so the
   *  composer gets the caret back. */
  returnFocus?: () => void;
  armDelayMs?: number;
}) {
  const headRef = useRef<string | null>(null);
  const shownAtRef = useRef(0);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const ordered = withHeadFirst(asks, headRef.current);
  const head = ordered[0] ?? null;
  const headId = head?.id ?? null;

  // A new question on screen: remember it (it stays first), start the key
  // guard, and take the focus when the composer had it (it is hidden now) or
  // nothing did. Focus in another field elsewhere on the page is left alone.
  useEffect(() => {
    if (headId === headRef.current) return;
    headRef.current = headId;
    if (!headId) return;
    shownAtRef.current = Date.now();
    const ae = document.activeElement;
    const composerHadIt = !!ae && !!ae.closest?.('[data-testid="chat-composer"]');
    if (bodyHasFocus() || composerHadIt) wrapRef.current?.focus({ preventScroll: true });
  }, [headId]);

  // The last question answered: the composer comes back; give it the caret
  // when the card took it with it (focus fell to the page).
  const hadAsk = useRef(false);
  const hasAsk = headId !== null;
  useEffect(() => {
    if (hadAsk.current && !hasAsk && bodyHasFocus()) returnFocus?.();
    hadAsk.current = hasAsk;
  }, [hasAsk, returnFocus]);

  /** Press the card's own button for this answer. False = nothing happened. */
  const press = useCallback(
    (which: "primary" | "decline"): boolean => {
      if (Date.now() - shownAtRef.current < armDelayMs) return false;
      const btn = wrapRef.current?.querySelector<HTMLButtonElement>(`[data-dock-${which}]`);
      if (!btn || btn.disabled) return false;
      btn.click();
      return true;
    },
    [armDelayMs],
  );

  // Nothing focused (the page itself): the keys still answer, unless a dialog
  // is open over the page.
  useEffect(() => {
    if (!hasAsk) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || !bodyHasFocus()) return;
      if (document.querySelector('[aria-modal="true"]')) return;
      const action = dockKeyAction(e, null, false);
      if (action && press(action)) e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [hasAsk, press]);

  // Inside the card: CAPTURE, because the app cards stop their own keys from
  // bubbling (so typing in their fields never reaches the page).
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

  if (!head) return null;
  const total = ordered.length;
  return (
    <div
      ref={wrapRef}
      data-testid="dock-ask"
      data-kind={head.kind}
      role="region"
      aria-label={total > 1 ? `A question for you, 1 of ${total}` : "A question for you"}
      tabIndex={-1}
      onKeyDownCapture={onKeyDownCapture}
      className={`relative z-[1] rounded-[20px] bg-ink-800 outline-none ${EDGE[head.kind]}`}
    >
      {(total > 1 || onStop) && (
        <div className="-mb-1 flex items-center gap-3 px-4 pt-2.5 text-[12px] text-zinc-500">
          {total > 1 && <span data-testid="dock-ask-count">1 of {total}</span>}
          {onStop && (
            <button
              type="button"
              data-testid="dock-ask-stop"
              onClick={onStop}
              title="Stop this reply"
              className="ml-auto rounded-md px-2 py-0.5 text-[12px] text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-200"
            >
              Stop
            </button>
          )}
        </div>
      )}
      {head.kind === "approval" ? (
        <ApprovalCard key={head.id} approval={head.approval} onConversation={onConversation} docked />
      ) : head.kind === "elicitation" ? (
        <ElicitationCard
          key={head.id}
          ask={head.ask}
          onAnswer={(act, content) => onAnswerElicitation(head.id, act, content)}
          docked
        />
      ) : (
        <SamplingCard
          key={head.id}
          ask={head.ask}
          onDecide={(dec) => onDecideSampling(head.id, dec)}
          docked
        />
      )}
    </div>
  );
}
