"use client";

// The "which kind?" chooser (v1.118.0) — the tile pattern the memory-base
// picker proved in v1.110.0, extracted for its third caller. A bare <select>
// presents every option as equally hard and hides what each will demand until
// you're already in the form; a tile answers the three questions people
// actually have — what is this, how long will it take, and what will it ask
// me for — BEFORE they commit. Easiest-first ordering is the caller's job.

import type { ReactNode } from "react";

import { EFFORT_TONE, type BaseOption } from "@/components/memory/baseCatalog";

export interface ChooserOption {
  key: string;
  /** What the user HAS, not how we talk to it. */
  label: string;
  /** One line: what picking this gets them. */
  blurb: string;
  /** Exactly what the form will ask for — no surprises mid-setup. */
  needs: string;
  effort: BaseOption["effort"];
  icon?: ReactNode;
  /** Renders the tile as already-done (e.g. a built-in that needs nothing). */
  connected?: boolean;
}

export function ChooserTiles({
  options,
  value,
  onChange,
  ariaLabel,
}: {
  options: ChooserOption[];
  value: string;
  onChange: (key: string) => void;
  ariaLabel: string;
}) {
  return (
    <div role="radiogroup" aria-label={ariaLabel} className="grid gap-1.5">
      {options.map((o) => {
        const on = value === o.key;
        return (
          <button
            key={o.key}
            type="button"
            role="radio"
            aria-checked={on}
            disabled={o.connected}
            onClick={() => onChange(o.key)}
            className={`flex items-start gap-2.5 rounded-xl border p-2.5 text-left transition-colors ${
              o.connected
                ? "cursor-default border-emerald-500/25 bg-emerald-500/[0.05]"
                : on
                  ? "border-accent/40 bg-accent/[0.07]"
                  : "border-white/[0.06] hover:bg-white/[0.03]"
            }`}
          >
            {o.icon && (
              <span
                className={`mt-0.5 shrink-0 ${
                  o.connected
                    ? "text-emerald-300"
                    : on
                      ? "text-accent-soft"
                      : "text-zinc-500"
                }`}
              >
                {o.icon}
              </span>
            )}
            <span className="min-w-0 flex-1">
              <span className="flex flex-wrap items-baseline gap-x-2">
                <span className="text-[13px] text-zinc-200">{o.label}</span>
                {o.connected ? (
                  <span className="text-[10px] text-emerald-300/90">connected</span>
                ) : (
                  <span className={`text-[10px] ${EFFORT_TONE[o.effort]}`}>
                    {o.effort}
                  </span>
                )}
              </span>
              <span className="mt-0.5 block text-[11px] leading-snug text-zinc-500">
                {o.blurb}
              </span>
              {on && !o.connected && (
                <span className="mt-1 block text-[11px] text-zinc-400">
                  You&apos;ll need: {o.needs}
                </span>
              )}
            </span>
          </button>
        );
      })}
    </div>
  );
}
