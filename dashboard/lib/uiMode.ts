"use client";

/**
 * SIMPLE / ADVANCED — one switch, read everywhere (v1.318.0).
 *
 * The switch has lived in the side menu since the Simple mode shipped
 * (`ij_nav_advanced`, "1" = Advanced, anything else = Simple — the DEFAULT).
 * Each reader kept its own copy that read storage once on mount, so turning
 * Advanced on in the drawer left the Overview showing the other mode until a
 * reload. Now the menu, the Overview and the page headers all read it here,
 * and a change is announced to every reader in this window at once
 * (`UI_MODE_EVENT`) and in other windows through `storage`.
 *
 * Seeded Simple for the server render and the first client render (stable
 * hydration), then corrected from storage in an effect — as before.
 */

import { useCallback, useEffect, useState } from "react";

export const ADVANCED_KEY = "ij_nav_advanced";
export const UI_MODE_EVENT = "ij-ui-mode-change";

export function readAdvanced(): boolean {
  try {
    return localStorage.getItem(ADVANCED_KEY) === "1";
  } catch {
    return false; // storage unavailable — Simple
  }
}

export function writeAdvanced(on: boolean): void {
  try {
    localStorage.setItem(ADVANCED_KEY, on ? "1" : "0");
  } catch {
    /* the switch still applies for this window */
  }
  if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent(UI_MODE_EVENT, { detail: on }));
}

/** `[advanced, setAdvanced]` — live across every reader. */
export function useAdvancedMode(): [boolean, (on: boolean) => void] {
  const [advanced, setLocal] = useState(false);
  useEffect(() => {
    const sync = () => setLocal(readAdvanced());
    sync();
    const onEvent = (e: Event) => {
      const d = (e as CustomEvent<boolean>).detail;
      setLocal(typeof d === "boolean" ? d : readAdvanced());
    };
    const onStorage = (e: StorageEvent) => {
      if (e.key === null || e.key === ADVANCED_KEY) sync();
    };
    window.addEventListener(UI_MODE_EVENT, onEvent);
    window.addEventListener("storage", onStorage);
    return () => {
      window.removeEventListener(UI_MODE_EVENT, onEvent);
      window.removeEventListener("storage", onStorage);
    };
  }, []);
  const set = useCallback((on: boolean) => {
    setLocal(on);
    writeAdvanced(on);
  }, []);
  return [advanced, set];
}
