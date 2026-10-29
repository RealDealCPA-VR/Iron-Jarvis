"use client";

/**
 * THE NEXT-STEP BOX (v1.406.0 part 4) under the newest settled reply, in a
 * chat and in a coding session: one or two short sentences of "where we
 * are" (lib/nextStep, built from the reply's own record) and at most two
 * buttons for the obvious next move, the first one the recommendation.
 *
 * Calm: no box of its own — the quiet 12px line and the round pills the
 * reply's other lines (WorkLine, the step-limit line) already use. The
 * model-written follow-ups (the `chat_followups` setting) render INSIDE it,
 * below the recap (`children`): one place, not two. The recap and its
 * buttons step aside while the user types (`hidden`; the follow-ups stay, as
 * they always have — a press adds to the typed words) and the whole box
 * while a turn runs (the host draws nothing then); nothing here
 * is saved.
 */

import { Children, useSyncExternalStore, type ReactNode } from "react";
import { ArrowRight } from "lucide-react";
import type { ComposerStore } from "@/lib/composerStore";
import type { NextAction, NextStep } from "@/lib/nextStep";

const PILL =
  "inline-flex max-w-full items-center gap-1 truncate rounded-full px-3 py-1 text-[12px] transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 disabled:opacity-40";
const FIRST = "bg-white/[0.08] text-zinc-100 hover:bg-white/[0.12]";
const OTHER = "bg-white/[0.04] text-zinc-300 hover:bg-white/[0.08] hover:text-zinc-100";

export function NextStepBox({
  step,
  onAction,
  hidden = false,
  children,
  testid = "next-step-box",
  className = "",
}: {
  step: NextStep | null;
  onAction: (a: NextAction) => void;
  /** The user is typing: the recap and buttons step aside (the follow-ups,
   *  `children`, stay). */
  hidden?: boolean;
  /** The follow-up suggestions (and a press a host draws itself). */
  children?: ReactNode;
  testid?: string;
  /** The host's column (a chat holds it to the reading width). */
  className?: string;
}) {
  // (`Children.toArray` drops a null / false slot: two empty slots are none.)
  const hasChildren = Children.toArray(children).length > 0;
  const shown = hidden ? null : step;
  if (!shown && !hasChildren) return null;
  return (
    <div data-testid={testid} role="group" aria-label="Next step" className={`mt-2 min-w-0 space-y-1.5 ${className}`}>
      {shown?.recap ? (
        <p data-testid="next-step-recap" className="text-[13px] leading-relaxed text-zinc-400">
          {shown.recap}
        </p>
      ) : null}
      {shown?.actions.length ? (
        <div className="flex flex-wrap items-center gap-1.5">
          {shown.actions.map((a, i) => (
            <button
              key={a.kind}
              type="button"
              data-testid={a.testid ?? "next-step-action"}
              data-kind={a.kind}
              data-recommended={i === 0 ? "true" : undefined}
              title={a.title}
              onClick={() => onAction(a)}
              className={`${PILL} ${i === 0 ? FIRST : OTHER}`}
            >
              <span className="truncate">{a.label}</span>
              {i === 0 && a.how !== "fill" ? <ArrowRight size={12} aria-hidden className="shrink-0" /> : null}
            </button>
          ))}
        </div>
      ) : null}
      {hasChildren ? children : null}
    </div>
  );
}

/** Is the chat's message box empty? A small subscriber so the CHAT PAGE
 *  never subscribes to the composer (v1.250.0): only this re-renders, and
 *  only when "empty" flips. */
export function useComposerEmpty(store: ComposerStore): boolean {
  const read = () => store.get().text.trim() === "";
  return useSyncExternalStore(store.subscribe, read, read);
}

/** The chat's box: hidden while the message box holds words. */
export function ChatNextStepBox({
  store,
  ...props
}: Omit<Parameters<typeof NextStepBox>[0], "hidden"> & { store: ComposerStore; hidden?: boolean }) {
  const empty = useComposerEmpty(store);
  return <NextStepBox {...props} hidden={!!props.hidden || !empty} />;
}
