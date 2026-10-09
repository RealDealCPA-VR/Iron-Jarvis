"use client";

// NewChatSuggestions (calm chat W1-3, v1.326.0): what sits under the composer
// card on a NEW chat. Three quiet suggestion pills (hairline edge, muted text,
// a fill only on hover) and, folded behind "More ideas", the whole-job recipes
// that used to sit open under them. The recipes stay one press away; they are
// no longer a second wall of cards around the one card on the screen.
//
// SUGGEST-DON'T-ACT, as before: a press only calls `onPick(text)`, which the
// chat page uses to put the words in the box. Nothing is sent.

import { useState } from "react";
import { ChevronDown } from "lucide-react";
import { RecipesRow } from "@/components/chat/RecipesRow";

/** How many suggestion pills a new chat shows. */
export const NEW_CHAT_SUGGESTIONS = 3;

/** One quiet pill: transparent at rest, a hairline edge, filled on hover. */
export const SUGGESTION_PILL =
  "rounded-full border border-white/[0.08] px-3 py-1 text-[13px] text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-zinc-200 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50";

export function NewChatSuggestions({
  examples,
  onPick,
}: {
  examples: string[];
  onPick: (text: string) => void;
}) {
  const [moreOpen, setMoreOpen] = useState(false);
  return (
    <div className="flex w-full flex-col items-center gap-3">
      <div data-testid="chat-suggestions" className="flex flex-wrap justify-center gap-2">
        {examples.slice(0, NEW_CHAT_SUGGESTIONS).map((ex) => (
          <button key={ex} type="button" onClick={() => onPick(ex)} className={SUGGESTION_PILL}>
            {ex}
          </button>
        ))}
      </div>
      <button
        type="button"
        data-testid="chat-more-ideas"
        aria-expanded={moreOpen}
        onClick={() => setMoreOpen((v) => !v)}
        className="inline-flex h-6 items-center gap-1 rounded-md px-1.5 text-[12px] text-zinc-500 transition-colors hover:bg-white/[0.04] hover:text-zinc-300 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50"
      >
        {moreOpen ? "Fewer ideas" : "More ideas"}
        <ChevronDown
          size={13}
          aria-hidden
          className={`transition-transform motion-reduce:transition-none ${moreOpen ? "rotate-180" : ""}`}
        />
      </button>
      {moreOpen && <RecipesRow onPick={onPick} />}
    </div>
  );
}
