/**
 * The page grid that never widens a phone (v1.313.0).
 *
 * WHY THIS EXISTS: a page laid out as `grid gap-6 lg:grid-cols-3` has NO
 * column template below lg. Its one implicit track is sized to the widest
 * child's min-content, so a table or a `truncate` line inside a card stretches
 * the whole page past a 390px screen, and the `overflow-x-auto` box around the
 * table never gets the chance to scroll. /schedules, /sessions and /skills ran
 * off the right edge because of exactly that.
 *
 * The cure is two guards, in one place:
 *   - `grid-cols-[minmax(0,1fr)]`: the phone track may shrink to the screen.
 *   - `[&>*]:min-w-0`: each column may shrink too, so wide content scrolls or
 *     ellipsizes INSIDE its card instead of pushing the page sideways.
 * The desktop layout is unchanged: the same column count at lg, and children
 * keep their own `lg:col-span-*`.
 *
 * The lg column classes are written out in full on purpose: Tailwind only
 * generates classes it can read in the source, so a class name built from
 * the `cols` number at runtime would ship no CSS at all.
 */

import type { HTMLAttributes, ReactNode } from "react";

const COLS: Record<2 | 3, string> = {
  2: "lg:grid-cols-2",
  3: "lg:grid-cols-3",
};

export type PageGridProps = {
  /** How many columns at lg and wider. Below lg it is always one column. */
  cols: 2 | 3;
  className?: string;
  children?: ReactNode;
} & Omit<HTMLAttributes<HTMLDivElement>, "className" | "children">;

export function PageGrid({ cols, className, children, ...rest }: PageGridProps) {
  const cls = [
    "grid gap-6 grid-cols-[minmax(0,1fr)]",
    COLS[cols],
    "[&>*]:min-w-0",
    className,
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <div className={cls} {...rest}>
      {children}
    </div>
  );
}
