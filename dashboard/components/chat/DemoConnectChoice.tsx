"use client";

/**
 * THE CONNECT CHOICE AFTER THE FIRST MESSAGE (v1.443.0, ease-of-use review
 * item 2).
 *
 * Before: the chat's connect choices (ConnectDoors) showed only on a NEW chat
 * and went away with the first message, which most people send at once. The
 * demo reply then said "Choose a model above", and above was a menu whose
 * every real row was greyed. Now, under the message box, for as long as the
 * demo model is the default:
 *
 *  - a signed-in Claude Code, a key, a detected Ollama…: the one press
 *    "Use <it> for answers" (ModelSuggestChip, unchanged), and
 *  - always "Connect a model" (or "Other ways to connect" beside the press),
 *    which opens the same ConnectDoors right here.
 *
 * The demo reply's note (daemon/chat_turn.demo_note) points at this button.
 * It reads /health through useDaemon only — never the composer — so a
 * keystroke never re-renders it (the v1.250.0 rule).
 *
 * `HeroConnectAnchor` wraps the new chat's own doors so the strip's and the
 * model menu's "Connect a model" (lib/demoExit.openConnect) bring them into
 * view instead of leaving the page.
 */

import dynamic from "next/dynamic";
import { useRef, useState, type ReactNode } from "react";
import { Cable, X } from "lucide-react";
import { useDaemon } from "@/lib/daemon";
import { isDemoDefault } from "@/lib/onboarding";
import { CONNECT_A_MODEL, pressableCandidates, useConnectTarget } from "@/lib/demoExit";
import { ModelSuggestChip } from "@/components/chat/ModelSuggestChip";
import { chosenNote } from "@/components/onboarding/AnswerPress";

// Deferred like the chat page's own copy: once a model is chosen nothing here
// draws, so the route need not carry the doors.
const ConnectDoors = dynamic(
  () =>
    import("@/components/onboarding/ConnectDoors").then((m) => ({
      default: m.ConnectDoors,
    })),
  { ssr: false },
);

/** The press's words when a press is already offered beside it. */
export const OTHER_WAYS_TO_CONNECT = "Other ways to connect";

/** The heading above the doors this opens. */
export const CONNECT_PANEL_HEADING = "Connect a model for real answers";

/** Bring an element into view and put the keyboard on its first button. */
function bringIntoView(el: Element | null) {
  if (!el) return;
  try {
    (el as HTMLElement).scrollIntoView?.({ block: "nearest", behavior: "smooth" });
  } catch {
    /* jsdom / old engines */
  }
  const first = el.querySelector<HTMLElement>("button:not([disabled]), a[href]");
  first?.focus?.();
}

export function DemoConnectChoice({ onOther }: { onOther?: () => void }) {
  const { health } = useDaemon();
  const demo = isDemoDefault(health) && !!health;
  const hasPress = pressableCandidates(health).length > 0;
  const [open, setOpen] = useState(false);
  const [chosen, setChosen] = useState("");
  const rootRef = useRef<HTMLSpanElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  useConnectTarget(rootRef, demo, () => {
    setOpen(true);
    // The panel draws on the next frame; look for it then.
    window.setTimeout(() => bringIntoView(panelRef.current), 0);
  });

  return (
    <>
      {/* Unchanged: "Use <it> for answers · one tap" (or its "Done" line). */}
      <ModelSuggestChip onOther={onOther} />
      {demo && (
        <span ref={rootRef} data-testid="demo-connect" className="inline-flex items-center">
          <button
            type="button"
            data-testid="demo-connect-open"
            aria-expanded={open}
            aria-controls={open ? "demo-connect-panel" : undefined}
            onClick={() => setOpen((v) => !v)}
            className="inline-flex items-center gap-1 rounded-full border border-white/10 px-2.5 py-0.5 text-[12px] text-zinc-300 transition-colors hover:bg-white/[0.06] hover:text-zinc-100"
          >
            <Cable size={11} aria-hidden /> {hasPress ? OTHER_WAYS_TO_CONNECT : CONNECT_A_MODEL}
          </button>
        </span>
      )}
      {demo && open && (
        <div
          ref={panelRef}
          id="demo-connect-panel"
          data-testid="demo-connect-panel"
          className="relative mt-2 w-full max-w-2xl basis-full rounded-xl border border-white/[0.06] px-3 pb-3 pt-2 text-left"
        >
          <div className="mb-2 flex items-center gap-2">
            <p className="min-w-0 flex-1 text-[13px] text-zinc-400">{CONNECT_PANEL_HEADING}</p>
            <button
              type="button"
              aria-label="Close"
              onClick={() => setOpen(false)}
              className="grid h-6 w-6 shrink-0 place-items-center rounded-md text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-200"
            >
              <X size={13} />
            </button>
          </div>
          <ConnectDoors layout="row" onChosen={setChosen} />
        </div>
      )}
      {chosen && !demo && (
        <span role="status" data-testid="demo-connect-done" className="text-[12px] text-tone-success">
          {chosenNote(chosen)}
        </span>
      )}
    </>
  );
}

/** The new chat's doors, answering "Connect a model" from the strip and the
 *  model menu by coming into view (no second copy of the doors). */
export function HeroConnectAnchor({ children }: { children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  useConnectTarget(ref, true, () => bringIntoView(ref.current));
  return (
    <div ref={ref} data-testid="chat-connect-doors" className="w-full max-w-2xl">
      {children}
    </div>
  );
}
