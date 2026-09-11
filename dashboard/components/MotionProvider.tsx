"use client";

import { LazyMotion, domAnimation } from "framer-motion";
import type { ReactNode } from "react";

/**
 * Features for every `m.*` component in the app (v1.250.0, S-08).
 *
 * `m` ships no animation features of its own — it takes them from here — so
 * this provider is what makes the swap from `motion.*` to `m.*` invisible.
 * `domAnimation` covers what this app actually uses: enter/exit animations
 * (AnimatePresence), transforms, opacity, variants and gestures via hover/tap
 * props. It does NOT include layout animation or drag, which framer bundles
 * separately as `domMax`; nothing here animates `layout` or uses framer's own
 * `drag` (the Overview grid and the kanban board drag through dnd-kit), so
 * loading domMax would buy back the weight this change exists to remove.
 *
 * A `layout` prop or framer `drag` added later needs `domMax` here, or the
 * animation silently does nothing — which is exactly the failure this comment
 * is here to shorten.
 *
 * It is a plain (non-lazy) features import on purpose: the features are small,
 * and `strict` is off, so a stray `motion.*` left anywhere keeps working
 * instead of throwing in the user's face.
 */
export function MotionProvider({ children }: { children: ReactNode }) {
  return <LazyMotion features={domAnimation}>{children}</LazyMotion>;
}
