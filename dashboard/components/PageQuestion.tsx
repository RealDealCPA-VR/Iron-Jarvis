"use client";

/**
 * v1.450.0 (Wave D, item 37): THE PAGE'S OWN QUESTION, where a native
 * `window.confirm` used to be.
 *
 * Four prompts were still the browser's dialog: the chat's Undo of a file
 * change, a Build terminal chat's Undo, saving a copy over a file of the same
 * name (DocPreview) and publishing a file to a public link (Creative). A native
 * dialog is a box the app does not draw: it carries no theme, blocks the whole
 * window, and on a phone is a system sheet in another voice. Every other
 * destructive press in the app is two presses (ConfirmButton) or an inline
 * question with two buttons (the coding session's Undo, v1.420.0). This is
 * that inline question, lifted so the four sites share it:
 *
 *   const q = usePageQuestion();
 *   if (!(await q.ask("Undo this write? …", { yes: "Undo it" }))) return;
 *   …
 *   <PageQuestion state={q.state} onAnswer={q.answer} testId="chat-undo-confirm" />
 *
 * `ask` resolves true on the yes press, false on no — and false when the
 * component goes away or a second question replaces the first, so no caller
 * hangs on a promise nobody can answer. `PageQuestion` draws the words and
 * the two buttons inline (the harness's `harness-undo-confirm` look, calm
 * variant); `PageQuestionDialog` draws the same question inside a Modal for a
 * surface with no composer line to sit above (the Creative page).
 *
 * dashboard/__tests__/settings-panel-words-v13540.test.tsx scans the whole
 * dashboard for a native confirm(): with these four gone the allow-list is
 * EMPTY, so a new native prompt anywhere fails that test.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Modal } from "@/components/Modal";
import { CALM_CONFIRM } from "@/components/ui";

export interface PageQuestionState {
  /** The question, in a full sentence saying what the yes press does. */
  words: string;
  /** The yes button's words (never a bare "OK": it names the action). */
  yes: string;
  /** The no button's words. */
  no: string;
}

export interface PageQuestionLabels {
  yes?: string;
  no?: string;
}

export const DEFAULT_YES = "Yes, do it";
export const DEFAULT_NO = "Keep";

export interface PageQuestionApi {
  state: PageQuestionState | null;
  ask: (words: string, labels?: PageQuestionLabels) => Promise<boolean>;
  answer: (yes: boolean) => void;
}

/** The question's state and its one resolver. A second `ask` answers the
 *  first "no" (one question at a time); unmounting answers "no" too. */
export function usePageQuestion(): PageQuestionApi {
  const [state, setState] = useState<PageQuestionState | null>(null);
  const resolveRef = useRef<((yes: boolean) => void) | null>(null);
  const answer = useCallback((yes: boolean) => {
    const resolve = resolveRef.current;
    resolveRef.current = null;
    setState(null);
    resolve?.(yes);
  }, []);
  useEffect(() => () => resolveRef.current?.(false), []);
  const ask = useCallback((words: string, labels?: PageQuestionLabels) => {
    resolveRef.current?.(false);
    return new Promise<boolean>((resolve) => {
      resolveRef.current = resolve;
      setState({ words, yes: labels?.yes ?? DEFAULT_YES, no: labels?.no ?? DEFAULT_NO });
    });
  }, []);
  return { state, ask, answer };
}

const NO_BUTTON =
  "inline-flex min-h-7 items-center rounded-lg px-2.5 py-1 text-xs text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-zinc-200 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50";

/** The inline question: words, the yes press (the calm confirm's armed look,
 *  so it reads as the second press it is) and the no press. Nothing when
 *  `state` is null. */
export function PageQuestion({
  state,
  onAnswer,
  testId = "page-question",
  className = "",
}: {
  state: PageQuestionState | null;
  onAnswer: (yes: boolean) => void;
  testId?: string;
  className?: string;
}) {
  const yesRef = useRef<HTMLButtonElement | null>(null);
  // AN review: where the focus was when the question appeared (the Undo
  // press, usually). Answered, the yes/no buttons are gone and the focus
  // would fall to the page — where a later Enter answers whatever card is
  // docked. It goes back where it came from, as the browser's confirm() did.
  const cameFromRef = useRef<HTMLElement | null>(null);
  const wasShownRef = useRef(false);
  // The yes press gets the focus when the question appears, so Enter (or
  // Tab + Enter) answers it from the keyboard; Escape is "no".
  useEffect(() => {
    if (state) {
      if (!wasShownRef.current) {
        const active = typeof document !== "undefined" ? document.activeElement : null;
        cameFromRef.current = active instanceof HTMLElement && active !== document.body ? active : null;
      }
      wasShownRef.current = true;
      yesRef.current?.focus({ preventScroll: true });
      return;
    }
    if (!wasShownRef.current) return;
    wasShownRef.current = false;
    const back = cameFromRef.current;
    cameFromRef.current = null;
    const active = document.activeElement;
    if (back && back.isConnected && (!active || active === document.body)) back.focus({ preventScroll: true });
  }, [state]);
  if (!state) return null;
  return (
    <div
      data-testid={testId}
      role="group"
      aria-label={state.words}
      onKeyDown={(e) => {
        // AN review: a HELD Enter (or Space) must never answer yes. The key
        // that pressed Undo moves the focus here; its auto-repeat would
        // otherwise click "Undo it" a moment later with no decision made.
        if ((e.key === "Enter" || e.key === " ") && e.repeat) {
          e.preventDefault();
          e.stopPropagation();
          return;
        }
        if (e.key === "Escape") {
          e.preventDefault();
          e.stopPropagation();
          onAnswer(false);
        }
      }}
      className={`flex min-w-0 flex-wrap items-center gap-2 px-2 text-[12px] text-zinc-300 ${className}`.trim()}
    >
      <span className="min-w-0 flex-1">{state.words}</span>
      <button
        ref={yesRef}
        type="button"
        data-testid={`${testId}-yes`}
        onClick={() => onAnswer(true)}
        className={`${CALM_CONFIRM.base} ${CALM_CONFIRM.armed}`}
      >
        {state.yes}
      </button>
      <button type="button" data-testid={`${testId}-no`} onClick={() => onAnswer(false)} className={NO_BUTTON}>
        {state.no}
      </button>
    </div>
  );
}

/** The same question in a dialog, for a surface with no line to sit on. */
export function PageQuestionDialog({
  state,
  onAnswer,
  testId = "page-question",
  label = "A question",
  z = 70,
}: {
  state: PageQuestionState | null;
  onAnswer: (yes: boolean) => void;
  testId?: string;
  label?: string;
  /** Above the dialog it was asked from (the Creative lightbox is 60). */
  z?: number;
}) {
  if (!state) return null;
  return (
    <Modal label={label} onClose={() => onAnswer(false)} className="w-full max-w-md" testId={`${testId}-dialog`} z={z}>
      <div className="p-4">
        <PageQuestion state={state} onAnswer={onAnswer} testId={testId} className="px-0" />
      </div>
    </Modal>
  );
}
