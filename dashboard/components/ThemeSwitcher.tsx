"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, m } from "framer-motion"; // v1.250.0 (S-08)
import { THEMES, DEFAULT_THEME as DEFAULT, LIGHT_MARKS, applyTheme, type Mark } from "@/lib/theme";

/**
 * Arc-reactor theme switcher. Four "Mark" reactors in the top bar re-skin the
 * whole app by flipping `data-theme` on <html> (the palette lives in CSS
 * variables — see globals.css + tailwind.config). Engaging one pops a brief
 * arc-reactor "suit up" HUD and morphs the colors. The choice persists to
 * localStorage; a tiny inline script in the layout applies it before paint.
 *
 * v1.313.0 — legible and reachable everywhere:
 *  - On the LIGHT Marks (Daylight, Liquid Glass) each reactor is drawn in a
 *    deeper `onLight` shade of its colour at full opacity. The pale accents
 *    (Arc Cyan, Gold, Silver at 55% opacity) were smudges on a white bar —
 *    Silver measured 1.23:1. Every dot now clears 3:1 on both light bars.
 *  - Every instance watches `data-theme` on <html>, so the bar's row and the
 *    nav drawer's row (the phone's way in — the bar hides its row below sm)
 *    never disagree about which Mark is on.
 *  - The suit-up reveal is portalled to <body>: inside the drawer it would
 *    otherwise be trapped in the drawer's transformed box.
 */

// v1.314.0: the Mark list, the storage key and the setter moved to
// lib/theme.ts — the Settings Appearance row and the palette's "Theme:"
// commands drive the SAME store, so the list exists once.

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
      <m.div
        className={layer}
        style={{
          borderRadius: "50%",
          background: `radial-gradient(circle, ${hexA(color, 0.32)}, transparent 62%)`,
        }}
        animate={{ opacity: [0.55, 1, 0.55], scale: [0.9, 1.05, 0.9] }}
        transition={{ repeat: Infinity, duration: 2.8, ease: "easeInOut" }}
      />
      {/* outer dashed ring — slow spin */}
      <m.svg
        className={layer}
        viewBox="0 0 100 100"
        fill="none"
        stroke="currentColor"
        animate={{ rotate: 360 }}
        transition={{ repeat: Infinity, ease: "linear", duration: 16 }}
      >
        <circle cx="50" cy="50" r="48" strokeWidth="0.8" strokeDasharray="2 5" opacity="0.45" />
      </m.svg>
      {/* mid segmented ring — reverse spin */}
      <m.svg
        className={layer}
        viewBox="0 0 100 100"
        fill="none"
        stroke="currentColor"
        animate={{ rotate: -360 }}
        transition={{ repeat: Infinity, ease: "linear", duration: 10 }}
      >
        <circle cx="50" cy="50" r="42" strokeWidth="1.6" strokeDasharray="18 10" opacity="0.8" />
        <circle cx="50" cy="50" r="37" strokeWidth="0.6" opacity="0.3" />
      </m.svg>
      {/* static spokes + inner ring */}
      <svg className={layer} viewBox="0 0 100 100" fill="none" stroke="currentColor">
        {spokes}
        <circle cx="50" cy="50" r="23" strokeWidth="1.2" opacity="0.6" />
      </svg>
      {/* pulsing core */}
      <m.svg
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
      </m.svg>
    </div>
  );
}

/**
 * `variant="drawer"` (v1.313.0) is the same row inside the nav drawer, with
 * a visible "Theme · <name>" line: on a phone the drawer is the only place
 * the row is reachable, and there is no hover there to read a tooltip.
 */
export function ThemeSwitcher({ variant = "bar" }: { variant?: "bar" | "drawer" } = {}) {
  const [active, setActive] = useState<string>(DEFAULT);
  const [reveal, setReveal] = useState<Mark | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // The reveal is portalled to <body>; render the portal only after mount so
  // the server render and the first client render agree.
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  // Follow <html data-theme> — whoever changed it (this row, the drawer's
  // row, the pre-paint restore script). A second switcher that only read the
  // attribute on mount kept highlighting the old Mark after the first was
  // pressed; observing the attribute keeps every instance in step.
  useEffect(() => {
    const html = document.documentElement;
    const sync = () => setActive(html.dataset.theme || DEFAULT);
    sync();
    const mo = new MutationObserver(sync);
    mo.observe(html, { attributes: true, attributeFilter: ["data-theme"] });
    return () => mo.disconnect();
  }, []);

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  function apply(m: Mark) {
    applyTheme(m.id); // the one setter (lib/theme.ts) — every row follows
    setActive(m.id);
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

  const light = LIGHT_MARKS.has(active);
  const activeMark = THEMES.find((t) => t.id === active);
  const drawer = variant === "drawer";

  const row = (
    <div
      className={`flex items-center gap-0.5 rounded-lg border border-white/10 bg-white/[0.03] ${
        drawer ? "justify-between p-1" : "h-8 px-0.5"
      }`}
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
            title={`${m.mark} — ${m.name}: ${m.flavor}`}
            aria-label={`${m.mark} — ${m.name}`}
            aria-pressed={on}
            style={{ color: light ? m.onLight : m.accent }}
            // On a light Mark nothing is dimmed: a 55% dot on white is a
            // smudge. The pressed one still stands out by its fill and ring.
            className={`grid place-items-center rounded-md transition-all ${
              drawer ? "h-9 w-9" : "h-7 w-7"
            } ${
              on
                ? "bg-white/[0.08] ring-1 ring-white/15"
                : light
                  ? "hover:bg-white/[0.05]"
                  : "opacity-55 hover:opacity-100 hover:bg-white/[0.05]"
            }`}
          >
            <Reactor size={drawer ? 20 : 18} glow={on} />
          </button>
        );
      })}
    </div>
  );

  return (
    <>
      {drawer ? (
        <div className="space-y-1.5">
          <div className="flex items-baseline justify-between px-1 text-[11px]">
            <span className="font-medium uppercase tracking-wider text-zinc-500">Theme</span>
            {activeMark && <span className="text-zinc-400">{activeMark.name}</span>}
          </div>
          {row}
        </div>
      ) : (
        row
      )}

      {mounted &&
        createPortal(
          <AnimatePresence>
            {reveal && (
              <m.div
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
                <m.div
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
                </m.div>
              </m.div>
            )}
          </AnimatePresence>,
          document.body,
        )}
    </>
  );
}
