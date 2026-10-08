"use client";

import { useEffect, useState } from "react";
import { currentScheme } from "@/lib/theme";

/** The Marks that are LIGHT themes (dark text on a bright canvas). Kept for
 *  callers that ask about a Mark id; the live answer is `currentScheme()`. */
export const LIGHT_MARKS: ReadonlySet<string> = new Set(["mark1", "mark8"]);

export type CanvasColorMode = "light" | "dark";

/** The canvas's colours, read live off the document. */
export interface CanvasColors {
  mode: CanvasColorMode;
  /** The theme's main colour as `#rrggbb` (wires, the trigger's minimap dot). */
  accent: string;
}

const FALLBACK: CanvasColors = { mode: "dark", accent: "#22d3ee" };

function tripletHex(raw: string): string | null {
  const m = /^\s*(\d{1,3})\s+(\d{1,3})\s+(\d{1,3})\s*$/.exec(raw);
  if (!m) return null;
  return `#${[m[1], m[2], m[3]].map((v) => Number(v).toString(16).padStart(2, "0")).join("")}`;
}

function read(): CanvasColors {
  const mode: CanvasColorMode = currentScheme();
  let accent: string | null = null;
  try {
    accent = tripletHex(getComputedStyle(document.documentElement).getPropertyValue("--accent-rgb"));
  } catch {
    /* no computed style (tests without a stylesheet) */
  }
  return { mode, accent: accent ?? (mode === "light" ? "#0e7490" : FALLBACK.accent) };
}

const OBSERVED = ["data-theme", "data-scheme", "style"];

/**
 * The React Flow colour mode AND wire colour that match the app's theme, LIVE.
 *
 * v1.313.0: the canvas was hard-wired to `colorMode="dark"` — a black slab in
 * the middle of a white page on Daylight. v1.317.0: the light/dark answer is
 * the document's scheme (so a light palette of the user's own counts), and
 * the wires take the theme's own main colour (they were always cyan).
 *
 * It starts dark on the server render and the first client render, so the
 * prerendered HTML and the first paint agree (no hydration mismatch), and
 * corrects itself in the effect.
 */
export function useCanvasColors(): CanvasColors {
  const [colors, setColors] = useState<CanvasColors>(FALLBACK);
  useEffect(() => {
    const sync = () =>
      setColors((prev) => {
        const next = read();
        return prev.mode === next.mode && prev.accent === next.accent ? prev : next;
      });
    sync();
    const obs = new MutationObserver(sync);
    obs.observe(document.documentElement, { attributes: true, attributeFilter: OBSERVED });
    return () => obs.disconnect();
  }, []);
  return colors;
}

export function useCanvasColorMode(): CanvasColorMode {
  return useCanvasColors().mode;
}
