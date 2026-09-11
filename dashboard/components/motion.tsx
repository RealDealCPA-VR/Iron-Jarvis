"use client";

import { m, type Variants } from "framer-motion";
import type { ReactNode } from "react";

const EASE = [0.22, 1, 0.36, 1] as const;

// v1.250.0 (S-08): `m` instead of `motion`. They render the same thing; the
// difference is that `motion.*` drags framer-motion's whole feature set into
// the bundle that imports it, while `m.*` carries none and takes its features
// from the LazyMotion provider in app/layout.tsx. Because the layout imports
// the Sidebar, the banner, the palette and the switcher — every one of them a
// motion user — that full bundle was sitting in the chunk shared by all 43
// routes. The animations, easing and durations below are untouched.
//
// Arrival motion, quieted (v1.99.0). This used to slide every section up 14px
// over 450ms, staggered 60ms apart — so a six-section page finished animating
// roughly 750ms after it was already usable, on every navigation, dozens of
// times a day. Motion should mean "this changed", not "this arrived": a short
// opacity fade still softens the entry without making the user wait for it.
// The slide and the stagger are gone; per-element motion that signals real
// state (streaming, live dots, drag) is untouched.
export const fadeUp: Variants = {
  hidden: { opacity: 0 },
  show: { opacity: 1, transition: { duration: 0.18, ease: EASE } },
};

const container: Variants = {
  hidden: {},
  show: { transition: { staggerChildren: 0, delayChildren: 0 } },
};

/** Page wrapper that staggers its <Reveal> children into view. */
export function PageShell({
  children,
  className = "space-y-6",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <m.div
      initial="hidden"
      animate="show"
      variants={container}
      className={className}
    >
      {children}
    </m.div>
  );
}

/** A single staggered item inside a PageShell (fades + slides up). */
export function Reveal({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <m.div variants={fadeUp} className={className}>
      {children}
    </m.div>
  );
}
