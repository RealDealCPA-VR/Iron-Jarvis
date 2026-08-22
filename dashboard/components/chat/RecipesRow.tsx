"use client";

// RecipesRow (v1.199.0) — the "whole job" tiles under the empty-state
// example chips on the chat page.
//
// SUGGEST-DON'T-ACT: clicking a recipe runs NOTHING. It only calls
// `onPick(recipe.prompt)`, which the chat page uses to PREFILL the composer —
// the user reads the prompt, edits it, and presses send themselves. A tile
// that fired a job on click would teach a brand-new user exactly the wrong
// lesson about this app.
//
// Static catalog, rendered unconditionally: no fetching, no state beyond
// CSS hover. The visual idiom follows the empty-state chips (chat/page.tsx)
// and the DOORS tiles (FirstRunWizard.tsx): zinc-on-dark cards that warm to
// the accent on hover.

import { RECIPES } from "@/lib/recipes";

export function RecipesRow({ onPick }: { onPick: (prompt: string) => void }) {
  return (
    <div className="w-full max-w-2xl">
      <p className="mb-2 text-center text-[10px] font-semibold uppercase tracking-[0.18em] text-zinc-600">
        or start a whole job
      </p>
      <div className="grid gap-1.5 sm:grid-cols-2">
        {RECIPES.map((r) => (
          <button
            key={r.key}
            type="button"
            // Prefill only — the user presses send (suggest-don't-act).
            onClick={() => onPick(r.prompt)}
            className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3.5 py-2.5 text-left transition-colors hover:border-accent/40 hover:bg-accent/[0.06]"
          >
            <span className="block text-xs font-semibold text-zinc-200">
              {r.title}
            </span>
            <span className="mt-0.5 block text-[11px] leading-snug text-zinc-500">
              {r.blurb}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}
