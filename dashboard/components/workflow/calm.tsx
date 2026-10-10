"use client";

/**
 * The workflow editor's calm look (v1.329.0, calm chat wave 9, K3).
 *
 * The chat page is one card (the composer) on a quiet page; the workflow
 * editor sat beside it as a stack of boxed cards with bordered buttons. These
 * are the shared pieces that make /workflows read like the chat: a plain
 * section with a quiet sentence-case label over a hairline, ghost buttons
 * that are transparent at rest and fill on hover, and quiet form labels.
 *
 * Colours are theme tokens only (`zinc-*`, `accent`, `tone-*`, `white`
 * overlays), so the dark Marks and the light Daylight Mark both read right,
 * and text sizes are whole pixels (11 / 12 / 13 px).
 */

import type { ReactNode } from "react";

const FOCUS =
  "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 disabled:cursor-not-allowed disabled:opacity-40";

const GHOST_BASE = `inline-flex h-9 shrink-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-lg px-3 text-[13px] font-medium transition-colors ${FOCUS}`;

/** A toolbar button: no border, no fill at rest, a soft fill on hover. */
export const WF_GHOST = `${GHOST_BASE} text-zinc-300 hover:bg-white/[0.06] hover:text-zinc-100`;

/** The same ghost tinted with the accent: a send that is ready to go. Still
 *  no fill and no border, so it never competes with the page's one primary. */
export const WF_GHOST_ON = `${GHOST_BASE} text-accent-soft hover:bg-accent/10`;

/** The same, row-sized (a list row's Load, a template's Load into editor). */
export const WF_GHOST_SM = `inline-flex h-7 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-lg px-2 text-[12px] font-medium text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-zinc-100 ${FOCUS}`;

/** A row-sized ghost tinted with the accent (Resume, Answer): the next step
 *  on that row, still borderless. */
export const WF_GHOST_ON_SM = `inline-flex h-7 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-lg px-2 text-[12px] font-medium text-accent-soft transition-colors hover:bg-accent/10 ${FOCUS}`;

/** A row-sized ghost whose hover says it removes or stops something. */
export const WF_GHOST_DANGER_SM = `inline-flex h-7 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-lg px-2 text-[12px] font-medium text-zinc-400 transition-colors hover:bg-tone-danger/10 hover:text-tone-danger ${FOCUS}`;

/** The confirming press of a two-press delete: the danger tone, still borderless. */
export const WF_DANGER_SM = `inline-flex h-7 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-lg bg-tone-danger/10 px-2.5 text-[12px] font-medium text-tone-danger transition-colors hover:bg-tone-danger/20 ${FOCUS}`;

/** A suggestion chip (a starter, an example request): a round ghost. */
export const WF_CHIP = `inline-flex h-7 shrink-0 items-center whitespace-nowrap rounded-full px-2.5 text-[12px] text-zinc-300 transition-colors hover:bg-white/[0.06] hover:text-zinc-100 ${FOCUS}`;

/** A small icon-only ghost (close, refresh). */
export const WF_ICON_GHOST = `grid h-7 w-7 shrink-0 place-items-center rounded-lg text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-100 ${FOCUS}`;

/** A form label: sentence case, quiet, whole pixels. */
export const WF_LABEL = "mb-1.5 block text-[12px] font-medium text-zinc-400";

/** A floating panel over the canvas (the Load menu, the inspectors): opaque
 *  so the graph never shows through, one hairline, no glow. */
export const WF_POPOVER = "rounded-xl border hairline bg-ink-900 shadow-lg";

/** A plain section: a quiet label (and an optional control on its right)
 *  over one hairline. No card, no fill. The heading is a real <h2>, so the
 *  page still reads as sections to a screen reader and to the tests that
 *  look a section up by its heading. */
export function CalmSection({
  title,
  right,
  children,
  testId,
}: {
  title: ReactNode;
  right?: ReactNode;
  children: ReactNode;
  testId?: string;
}) {
  return (
    <section data-testid={testId}>
      <header className="flex min-h-[28px] flex-wrap items-center justify-between gap-x-3 gap-y-1 px-1 pb-1.5">
        <h2 className="text-[13px] font-medium text-zinc-400">{title}</h2>
        {right}
      </header>
      <div className="border-t hairline pt-3">{children}</div>
    </section>
  );
}
