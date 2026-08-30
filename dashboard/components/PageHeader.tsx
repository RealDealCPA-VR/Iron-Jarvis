"use client";

/**
 * The module title: the NAME, and the explanation on demand (v1.214.1).
 *
 * Reported: "in all the modules there is a title like Overview (with a
 * subtitle that tells you about) … lets make it so the only thing present is
 * the title of the module for each module and if the user hovers over the
 * title it will give them the same details of the subtitle as a popup/modal
 * but not visible otherwise. This will provide a cleaner surface area as the
 * user is engaged with any specific module."
 *
 * The subtitle earns its place exactly once — the first time you open a
 * module — and then costs a line of prose above the work on every visit after
 * that, on 36 of the 38 pages. So it moves behind the title, and the title
 * becomes the thing that offers it.
 *
 * WHAT THIS IS NOT: `title=""`. The native tooltip cannot be styled, waits
 * about a second, is invisible to touch, and does not exist for a keyboard
 * user at all. This is a real popover, and it opens three ways because there
 * are three kinds of user:
 *
 *   HOVER   the mouse case the report describes.
 *   FOCUS   the keyboard case — the trigger is focusable and reachable by Tab,
 *           so the description is not mouse-only.
 *   CLICK   the touch case, where hover does not exist at all. Click toggles,
 *           so a tap opens it and a second tap (or Escape, or moving away)
 *           closes it.
 *
 * AND IT IS ALWAYS THERE FOR A SCREEN READER. The popover is rendered on every
 * page load and merely made invisible, never unmounted — `aria-describedby`
 * cannot resolve to an element that is not in the document, and a description
 * that appears only on hover would be a description that assistive technology
 * can never reach. So the a11y behaviour is unchanged from when the subtitle
 * was printed in full: the trigger is described by it, always. Only the
 * PIXELS are conditional.
 *
 * v1.214.3 — `ModuleTitle` is EXTRACTED from `PageHeader`, because the Agents
 * module now carries its title inside the thread rail rather than above the
 * page ("the title Agents should be on the top left inside the card of the
 * threads"). Two copies of this would be two copies of the a11y wiring, and
 * the half that is easy to get wrong is the half nobody looks at. One
 * component, two sizes.
 */

import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { motion } from "framer-motion";
import { Info } from "lucide-react";

export function ModuleTitle({
  title,
  hint,
  className = "text-2xl font-semibold tracking-tight text-zinc-50",
  iconSize = 13,
}: {
  title: string;
  /** The description, shown on demand. Absent = a plain heading, no trigger. */
  hint?: string;
  /** Styling for the <h1> itself — a rail wants it far smaller than a page. */
  className?: string;
  iconSize?: number;
}) {
  const tipId = useId();
  const [open, setOpen] = useState(false);
  const hostRef = useRef<HTMLDivElement>(null);

  // Escape closes, and so does a click anywhere else — the popover is opened
  // by a click on touch, and a thing you opened by tapping has to be closable
  // by tapping past it. Bound only while open: 38 pages must not each carry a
  // pair of idle document listeners.
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    function onDown(e: MouseEvent) {
      if (!hostRef.current?.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onDown);
    };
  }, [open]);

  return (
    <div ref={hostRef} className="relative">
      <h1 className={className}>
        {hint ? (
          <span
            // Focusable so Tab reaches it, `role="button"` because clicking
            // it does something (it toggles the popover) — an interactive
            // control that claimed no role would be a control a screen
            // reader user is never told they can operate.
            role="button"
            tabIndex={0}
            aria-describedby={tipId}
            aria-expanded={open}
            data-testid="page-title"
            onMouseEnter={() => setOpen(true)}
            onMouseLeave={() => setOpen(false)}
            onFocus={() => setOpen(true)}
            onBlur={() => setOpen(false)}
            onClick={() => setOpen((v) => !v)}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                setOpen((v) => !v);
              }
            }}
            className="group inline-flex cursor-help items-center gap-1.5 rounded-md outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
          >
            {title}
            {/* THE AFFORDANCE. Without it the popover is a secret: nothing
                about a bare heading says "there is more here". Quiet by
                default, accent on hover/focus, and `aria-hidden` because the
                description it hints at is already wired to the trigger. */}
            <Info
              size={iconSize}
              aria-hidden
              className={`shrink-0 transition-colors ${
                open ? "text-accent-soft" : "text-zinc-600 group-hover:text-zinc-400"
              }`}
            />
          </span>
        ) : (
          title
        )}
      </h1>
      {hint && (
        <p
          id={tipId}
          role="tooltip"
          data-testid="page-subtitle"
          data-open={open ? "true" : "false"}
          // RENDERED ALWAYS, SHOWN ON DEMAND — see the header note. It is
          // `absolute`, so an invisible popover costs the page no layout,
          // which is the whole point of the change; and `opacity` rather
          // than `hidden`, so it stays resolvable by `aria-describedby`.
          //
          // OPAQUE, and that is a correction rather than a preference. The
          // first cut used the app's dialog surface — `bg-ink-850/95` over
          // `backdrop-blur-xl` — which is right for a modal, because a modal
          // sits on a dimming backdrop. This popover sits on nothing, and
          // driven in a real browser the card behind it read THROUGH the
          // words. `z-40` for the same reason: it has to clear what it
          // overlaps, and inside a 17rem rail it overlaps the conversation.
          className={`absolute left-0 top-full z-40 mt-2 w-max max-w-md rounded-xl border border-white/10 bg-ink-850 px-3 py-2 text-sm leading-relaxed text-zinc-200 shadow-card-hover transition-opacity duration-150 ${
            open ? "opacity-100" : "pointer-events-none opacity-0"
          }`}
        >
          {hint}
        </p>
      )}
    </div>
  );
}

export function PageHeader({
  title,
  subtitle,
  actions,
}: {
  title: string;
  /** Kept as a prop on all 38 callers — it is not gone, it is behind the
   *  title. Absent and the title is simply a title. */
  subtitle?: string;
  actions?: ReactNode;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: -8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, ease: [0.22, 1, 0.36, 1] }}
      className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between"
    >
      <ModuleTitle title={title} hint={subtitle} />
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </motion.div>
  );
}
