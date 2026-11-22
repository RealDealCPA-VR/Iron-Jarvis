"use client";

/**
 * The slim header of Quick Ask's answer window (next leaps 36-a, R2.2). The
 * chat page drawn compact (lib/quickAnswer) has no chat list, no Chat | Code
 * switch and no top bar; this one quiet line names the window and offers its
 * one way onward: "Continue in the main window" opens the same saved chat
 * there. It waits until there is a saved chat to continue (the first message
 * names it, at send).
 */

import { AppWindow } from "lucide-react";

export const QUICK_ASK_NAME = "Quick Ask";
export const CONTINUE_IN_MAIN_WORDS = "Continue in the main window";

export function QuickAskHeader({ canContinue, onContinue }: { canContinue: boolean; onContinue: () => void }) {
  return (
    <div
      data-testid="quick-ask-header"
      className="flex h-9 min-w-0 shrink-0 items-center justify-between gap-2 px-1"
    >
      <span className="min-w-0 truncate text-[13px] font-medium text-zinc-300">{QUICK_ASK_NAME}</span>
      <button
        type="button"
        data-testid="quick-ask-continue"
        disabled={!canContinue}
        onClick={() => {
          if (canContinue) onContinue();
        }}
        title={canContinue ? "Open this chat in the main window" : "Ask something first"}
        className="inline-flex h-7 min-w-0 shrink items-center gap-1.5 rounded-lg px-2 text-[12px] text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent disabled:hover:text-zinc-400"
      >
        <AppWindow size={13} className="shrink-0" aria-hidden />
        <span className="min-w-0 truncate">{CONTINUE_IN_MAIN_WORDS}</span>
      </button>
    </div>
  );
}
