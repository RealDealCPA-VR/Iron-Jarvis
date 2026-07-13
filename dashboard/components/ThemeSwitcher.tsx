"use client";

import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";

/**
 * Arc-reactor theme switcher. Four "Mark" reactors in the top bar re-skin the
 * whole app by flipping `data-theme` on <html> (the palette lives in CSS
 * variables — see globals.css + tailwind.config). Engaging one pops a brief
 * "suit up" modal and morphs the colors. The choice persists to localStorage;
 * a tiny inline script in the layout applies it before paint (no flash).
 */

interface Mark {
  id: string; // data-theme value
  mark: string; // "Mark 1"
  name: string;
  flavor: string;
  accent: string; // preview color (each reactor shows ITS theme's color)
}

const THEMES: Mark[] = [
  {
    id: "mark1",
    mark: "Mark 1",
    name: "Bright Graphite",
    flavor: "Raw steel — a brighter, cleaner finish.",
    accent: "#7dd3fc",
  },
  {
    id: "mark2",
    mark: "Mark 2",
    name: "Arc Cyan",
    flavor: "The signature reactor glow. Balanced and cool.",
    accent: "#22d3ee",
  },
  {
    id: "mark23",
    mark: "Mark 23",
    name: "Gold & Red",
    flavor: "The classic hero colors — powered up.",
    accent: "#f5b731",
  },
  {
    id: "mark29",
    mark: "Mark 29",
    name: "Silver & Red",
    flavor: "Sleek chrome with a red-line edge.",
    accent: "#bfc8d6",
  },
];

const STORAGE_KEY = "ij_theme";
const DEFAULT = "mark2";

/** A stylized arc reactor drawn in `currentColor`. */
function Reactor({ size = 22, glow = false }: { size?: number; glow?: boolean }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      aria-hidden="true"
      className={glow ? "drop-shadow-[0_0_12px_currentColor]" : undefined}
    >
      <circle cx="12" cy="12" r="9.4" strokeWidth="1.1" opacity="0.32" />
      <circle cx="12" cy="12" r="6.3" strokeWidth="1" opacity="0.5" />
      {Array.from({ length: 8 }).map((_, i) => {
        const a = (i * Math.PI) / 4;
        return (
          <line
            key={i}
            x1={12 + Math.cos(a) * 4.1}
            y1={12 + Math.sin(a) * 4.1}
            x2={12 + Math.cos(a) * 6.1}
            y2={12 + Math.sin(a) * 6.1}
            strokeWidth="1"
            strokeLinecap="round"
            opacity="0.7"
          />
        );
      })}
      <circle cx="12" cy="12" r="3.1" fill="currentColor" fillOpacity="0.22" strokeWidth="1.1" />
      <circle cx="12" cy="12" r="1.3" fill="currentColor" stroke="none" />
    </svg>
  );
}

export function ThemeSwitcher() {
  const [active, setActive] = useState<string>(DEFAULT);
  const [reveal, setReveal] = useState<Mark | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const clsTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Reflect whatever the no-flash script already applied.
  useEffect(() => {
    setActive(document.documentElement.dataset.theme || DEFAULT);
  }, []);

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
      if (clsTimer.current) clearTimeout(clsTimer.current);
    },
    [],
  );

  function apply(m: Mark) {
    const html = document.documentElement;
    html.classList.add("theme-transition"); // smooth color morph, briefly
    html.dataset.theme = m.id;
    try {
      localStorage.setItem(STORAGE_KEY, m.id);
    } catch {
      /* private mode — the theme still applies for this session */
    }
    setActive(m.id);
    if (clsTimer.current) clearTimeout(clsTimer.current);
    clsTimer.current = setTimeout(() => html.classList.remove("theme-transition"), 520);
    // "Suit up" reveal, auto-dismissed.
    setReveal(m);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setReveal(null), 1900);
  }

  // Esc closes the reveal.
  useEffect(() => {
    if (!reveal) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setReveal(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [reveal]);

  return (
    <>
      <div
        className="flex items-center gap-0.5 rounded-lg border border-white/10 bg-white/[0.03] px-1 py-1"
        role="group"
        aria-label="App theme (arc reactor)"
      >
        {THEMES.map((m) => {
          const on = active === m.id;
          return (
            <button
              key={m.id}
              type="button"
              onClick={() => apply(m)}
              title={`${m.mark} — ${m.name}`}
              aria-label={`${m.mark} — ${m.name}`}
              aria-pressed={on}
              style={{ color: m.accent }}
              className={`grid h-7 w-7 place-items-center rounded-md transition-all ${
                on
                  ? "bg-white/[0.08] ring-1 ring-white/15"
                  : "opacity-55 hover:opacity-100 hover:bg-white/[0.05]"
              }`}
            >
              <Reactor size={18} glow={on} />
            </button>
          );
        })}
      </div>

      <AnimatePresence>
        {reveal && (
          <motion.div
            key="theme-reveal"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            onClick={() => setReveal(null)}
            role="dialog"
            aria-modal="true"
            aria-label={`Theme changed to ${reveal.mark}, ${reveal.name}`}
            className="fixed inset-0 z-[100] grid place-items-center bg-black/70 backdrop-blur-sm"
          >
            <motion.div
              initial={{ scale: 0.9, y: 8, opacity: 0 }}
              animate={{ scale: 1, y: 0, opacity: 1 }}
              exit={{ scale: 0.95, opacity: 0 }}
              transition={{ type: "spring", stiffness: 320, damping: 26 }}
              onClick={(e) => e.stopPropagation()}
              className="card-surface flex w-[min(88vw,340px)] flex-col items-center gap-3 px-8 py-8 text-center"
            >
              <motion.div
                style={{ color: reveal.accent }}
                initial={{ rotate: -90, scale: 0.6 }}
                animate={{ rotate: 0, scale: 1 }}
                transition={{ type: "spring", stiffness: 200, damping: 18 }}
              >
                <Reactor size={72} glow />
              </motion.div>
              <div className="text-[11px] font-semibold uppercase tracking-[0.28em] text-zinc-400">
                {reveal.mark}
              </div>
              <div className="text-xl font-semibold tracking-tight text-zinc-50">
                {reveal.name}
              </div>
              <p className="max-w-[15rem] text-[13px] leading-relaxed text-zinc-400">
                {reveal.flavor}
              </p>
              <button
                type="button"
                autoFocus
                onClick={() => setReveal(null)}
                className="btn-accent mt-1 px-4 py-1.5 text-xs"
              >
                Suit up
              </button>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}
