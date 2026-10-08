"use client";

import { useEffect, useState } from "react";

/** The Marks that are LIGHT themes (dark text on a bright canvas). */
export const LIGHT_MARKS: ReadonlySet<string> = new Set(["mark1", "mark8"]);

export type CanvasColorMode = "light" | "dark";

function readMode(): CanvasColorMode {
  const theme = document.documentElement.dataset.theme ?? "";
  return LIGHT_MARKS.has(theme) ? "light" : "dark";
}

/**
 * The React Flow colour mode that matches the app's theme, LIVE (v1.313.0).
 *
 * The workflow canvas was hard-wired to `colorMode="dark"`, so on Daylight and
 * Liquid Glass it was a black slab in the middle of a white page. The theme
 * switcher flips `data-theme` on <html> without a reload, so reading it once
 * at render is not enough: a MutationObserver follows every switch, both ways.
 *
 * It starts as "dark" on the server render and the first client render, so
 * the HTML Next prerendered and the first paint agree (no hydration
 * mismatch), and corrects itself in the effect. It reads the attribute, never
 * `getComputedStyle().colorScheme`: the attribute is the switcher's own truth
 * and needs no stylesheet to answer.
 */
export function useCanvasColorMode(): CanvasColorMode {
  const [mode, setMode] = useState<CanvasColorMode>("dark");
  useEffect(() => {
    setMode(readMode());
    const obs = new MutationObserver(() => setMode(readMode()));
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => obs.disconnect();
  }, []);
  return mode;
}
