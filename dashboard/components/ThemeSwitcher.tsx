"use client";

import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";

/**
 * Arc-reactor theme switcher. Four "Mark" reactors in the top bar re-skin the
 * whole app by flipping `data-theme` on <html> (the palette lives in CSS
 * variables — see globals.css + tailwind.config). Engaging one pops a brief
 * arc-reactor "suit up" HUD and morphs the colors. The choice persists to
 * localStorage; a tiny inline script in the layout applies it before paint.
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
    name: "Daylight",
    flavor: "Dark work on a bright canvas — full light mode.",
    accent: "#0891b2",
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

function hexA(hex: string, a: number): string {
  const h = hex.replace("#", "");
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  return `rgba(${r},${g},${b},${a})`;
}

/** A compact static arc reactor (switcher buttons), drawn in `currentColor`. */
function Reactor({ size = 18, glow = false }: { size?: number; glow?: boolean }) {
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

/** The big animated reactor for the "suit up" HUD — counter-rotating rings, a
 *  pulsing core and a blooming glow, all in the chosen Mark's color. */
function BigReactor({ color, size = 128 }: { color: string; size?: number }) {
  const layer = "absolute inset-0 h-full w-full";
  const spokes = Array.from({ length: 8 }).map((_, i) => {
    const a = (i * Math.PI) / 4;
    return (
      <line
        key={i}
        x1={50 + Math.cos(a) * 24}
        y1={50 + Math.sin(a) * 24}
        x2={50 + Math.cos(a) * 33}
        y2={50 + Math.sin(a) * 33}
        strokeWidth="1.3"
        strokeLinecap="round"
        opacity="0.7"
      />
    );
  });
  return (
    <div className="relative" style={{ width: size, height: size, color }}>
      {/* glow bloom */}
      <motion.div
        className={layer}
        style={{
          borderRadius: "50%",
          background: `radial-gradient(circle, ${hexA(color, 0.32)}, transparent 62%)`,
        }}
        animate={{ opacity: [0.55, 1, 0.55], scale: [0.9, 1.05, 0.9] }}
        transition={{ repeat: Infinity, duration: 2.8, ease: "easeInOut" }}
      />
      {/* outer dashed ring — slow spin */}
      <motion.svg
        className={layer}
        viewBox="0 0 100 100"
        fill="none"
        stroke="currentColor"
        animate={{ rotate: 360 }}
        transition={{ repeat: Infinity, ease: "linear", duration: 16 }}
      >
        <circle cx="50" cy="50" r="48" strokeWidth="0.8" strokeDasharray="2 5" opacity="0.45" />
      </motion.svg>
      {/* mid segmented ring — reverse spin */}
      <motion.svg
        className={layer}
        viewBox="0 0 100 100"
        fill="none"
        stroke="currentColor"
        animate={{ rotate: -360 }}
        transition={{ repeat: Infinity, ease: "linear", duration: 10 }}
      >
        <circle cx="50" cy="50" r="42" strokeWidth="1.6" strokeDasharray="18 10" opacity="0.8" />
        <circle cx="50" cy="50" r="37" strokeWidth="0.6" opacity="0.3" />
      </motion.svg>
      {/* static spokes + inner ring */}
      <svg className={layer} viewBox="0 0 100 100" fill="none" stroke="currentColor">
        {spokes}
        <circle cx="50" cy="50" r="23" strokeWidth="1.2" opacity="0.6" />
      </svg>
      {/* pulsing core */}
      <motion.svg
        className={layer}
        viewBox="0 0 100 100"
        animate={{ scale: [1, 1.08, 1], opacity: [0.85, 1, 0.85] }}
        transition={{ repeat: Infinity, duration: 1.7, ease: "easeInOut" }}
        style={{ color }}
      >
        <circle
          cx="50"
          cy="50"
          r="13"
          fill="currentColor"
          fillOpacity="0.18"
          stroke="currentColor"
          strokeWidth="1.4"
        />
        <circle cx="50" cy="50" r="5.5" fill="currentColor" />
      </motion.svg>
    </div>
  );
}

export function ThemeSwitcher() {
  const [active, setActive] = useState<string>(DEFAULT);
  const [reveal, setReveal] = useState<Mark | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const clsTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

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
    setReveal(m);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setReveal(null), 2100);
  }

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
            transition={{ duration: 0.22 }}
            onClick={() => setReveal(null)}
            role="dialog"
            aria-modal="true"
            aria-label={`Theme changed to ${reveal.mark}, ${reveal.name}`}
            className="fixed inset-0 z-[100] grid place-items-center bg-black/75 backdrop-blur-sm"
          >
            <motion.div
              initial={{ scale: 0.92, y: 10, opacity: 0 }}
              animate={{ scale: 1, y: 0, opacity: 1 }}
              exit={{ scale: 0.96, opacity: 0 }}
              transition={{ type: "spring", stiffness: 300, damping: 24 }}
              onClick={(e) => e.stopPropagation()}
              className="card-surface relative flex w-[min(88vw,320px)] flex-col items-center gap-3.5 px-10 py-9 text-center"
            >
              {/* HUD corner brackets */}
              {[
                "left-2 top-2 border-l-2 border-t-2",
                "right-2 top-2 border-r-2 border-t-2",
                "left-2 bottom-2 border-l-2 border-b-2",
                "right-2 bottom-2 border-r-2 border-b-2",
              ].map((c) => (
                <span
                  key={c}
                  aria-hidden="true"
                  className={`pointer-events-none absolute h-4 w-4 rounded-[3px] ${c}`}
                  style={{ borderColor: hexA(reveal.accent, 0.55) }}
                />
              ))}

              <BigReactor color={reveal.accent} />

              <div>
                <div className="font-mono text-[10px] font-medium uppercase tracking-[0.34em] text-zinc-500">
                  {reveal.mark}
                </div>
                <div className="mt-1 text-xl font-semibold tracking-tight text-zinc-50">
                  {reveal.name}
                </div>
              </div>

              <div
                className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.22em]"
                style={{ color: reveal.accent }}
              >
                <span
                  className="h-1.5 w-1.5 rounded-full"
                  style={{ background: reveal.accent, boxShadow: `0 0 8px ${reveal.accent}` }}
                />
                reactor online
              </div>

              <p className="max-w-[15rem] text-[13px] leading-relaxed text-zinc-400">
                {reveal.flavor}
              </p>

              <button
                type="button"
                autoFocus
                onClick={() => setReveal(null)}
                className="btn-accent mt-0.5 px-5 py-1.5 text-xs"
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
