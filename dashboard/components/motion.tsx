"use client";

import { motion, type Variants } from "framer-motion";
import type { ReactNode } from "react";

const EASE = [0.22, 1, 0.36, 1] as const;

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
    <motion.div
      initial="hidden"
      animate="show"
      variants={container}
      className={className}
    >
      {children}
    </motion.div>
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
    <motion.div variants={fadeUp} className={className}>
      {children}
    </motion.div>
  );
}
